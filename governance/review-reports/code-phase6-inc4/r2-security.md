severity: clean

站在攻擊者的角度看了第 1 輪修正後的三個重點:`counted_first_rows` 的 `IN` 子句拼佔位符、`stop_count_query` 拼條件、交易歸屬核對能不能繞過、稽核明細會不會洩漏別的租戶資料。沒找到可利用的洞。

## 逐項確認

### 1. `counted_first_rows` 的 `IN` 子句 — 沒有注入面
```python
marks = ", ".join("?" for _ in wanted)
rows = _conn(tx).execute(
    f"SELECT {_FIRST_ROW_COLUMNS} FROM attempts WHERE seq = 1 AND key IN ({marks})",  # noqa: S608 - 只拼接佔位符
    wanted)
```
引句:「只拼接佔位符」(file: `src/rtb/executor/attempt_store.py:454`)

`marks` 只由 `wanted`(鍵清單)的**個數**決定字串長度(每個都是固定的 `"?"`),鍵本身的內容從沒有進到 SQL 文字裡,是透過 `execute(sql, wanted)` 當參數傳的。就算鍵字串裡塞 `' OR 1=1--` 之類的內容,也只會被當成一個字面值去比對,不會被解析成 SQL。沒有注入面。

補一層檢查:這支函式的 `keys` 目前只有一個呼叫端——`aggregate_audit` 內部算出來的 `counted = {holding.key for holding in holdings}`,而 `holdings` 本身已經是 `aggregate_holdings(tx, tenant, now)` 用 `WHERE f.tenant = ? OR f.tenant IS NULL` 篩過那個租戶的結果(`src/rtb/executor/attempt_store.py:398-402`)。所以就算之後有人想把 `keys` 對應到「攻擊者可控輸入」,現在的呼叫路徑上也沒有這個口子。

### 2. `stop_count_query` 拼條件 — 沒有注入面
```python
where, params = ["kind = ?"], [kind.value]
for clause, value in (("tenant = ?", tenant), ("campaign_id = ?", campaign_id),
                      ("at >= ?", None if since is None else _iso(since)),
                      ("at < ?", None if until is None else _iso(until))):
    if value is not None:
        where.append(clause)
        params.append(value)
return f"SELECT count(*) FROM write_stops WHERE {' AND '.join(where)}", tuple(params)  # noqa: S608 - 只拼接固定條件
```
引句:「只拼接固定條件」(file: `src/rtb/executor/inbox_store.py:781`)

`where` 清單裡加進去的字串(`"tenant = ?"`、`"campaign_id = ?"` 等)全部是寫死在原始碼裡的常數,呼叫端傳進來的 `tenant`、`campaign_id`、`since`、`until` 只出現在 `params`,一律走參數化。`kind.value` 來自封閉列舉 `StopKind`,不是任意字串。沒有注入面。

### 3. 交易歸屬核對(`_own`)— 目前這輪沒看到能繞過的路
```python
def _own(self, tx: attempt_store.ExecutorTransaction) -> None:
    if type(tx) is not attempt_store.ExecutorTransaction or not tx.is_open or (
            tx.conn is not self._conn):
        raise attempt_store.NotInTransaction("只能在這個收件口自己開的交易裡處置提案")
```
引句:「只能在這個收件口自己開的交易裡處置提案」(file: `src/rtb/executor/inbox_store.py:496`)

`stop_count`、`stops` 兩支新讀取方法(`src/rtb/executor/inbox_store.py:228-249`)開頭都先呼叫 `self._own(tx)`。核對用的是 `type(tx) is not ...`(嚴格型別比對,不是 `isinstance`,擋掉子類繞過)加上 `tx.conn is not self._conn`(物件身分比對,不是用資料庫路徑或名稱字串比對,擋掉「開一個指到同一個檔案但不同連線物件」的混淆嘗試)。第 1 輪修正新增的測試 `test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox` 直接對著這兩支方法、拿別的 `InboxStore` 開的交易去戳,驗證會拋 `NotInTransaction`——實際跑過:

```
$ /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider \
    /Users/enzo/rtb-3b/tests/executor/test_observability.py \
    -k "test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox" -q
1 passed
```
(用 rtb-3b 的檔案跑,只跑測試,沒有動任何檔案。)

嘗試紀錄那邊(`_conn`,`src/rtb/executor/attempt_store.py:207-210`)用同一種寫法,也是型別加 `is_open` 核對,沒有另開後門。威脅模型本來就寫明「防忘記,不防刻意繞過」(`src/rtb/executor/attempt_store.py:11`),站在真正的攻擊者角度看,這一版擋掉的是「拿錯交易物件」這種意外接線,不是要擋一個能執行任意 Python 的內部人——這點跟本來的威脅模型一致,不算沒做到位。

### 4. 稽核明細會不會洩漏別的租戶資料 — 目前這輪沒發現
- `stops()` 的 `tenant` 是必填位置參數(不像 `stop_count` 那樣可省略),SQL 裡 `AND tenant = ?` 是固定條件,直接鎖住那個租戶。
- `passed`(`counted_first_rows_started`)用 `first_rows_started_query(tenant, ...)`,SQL 用 `UNION ALL` 拼「這個租戶」與「租戶是空值的舊列」兩段,沒有第三段撈別的租戶。
- `holding`(`counted_first_rows`)雖然 SQL 本身只用 `key IN (...)` 沒帶租戶條件,但如第 1 點所述,傳進去的 `keys` 已經是 `aggregate_holdings` 用那個租戶篩過的鍵;而且每一列都還要再過 `_counted_rows` 裡的 `_counted(rest, tenant)`——`owner != tenant` 的列會被算成 0、直接濾掉(`src/rtb/executor/attempt_store.py:459-461`)。所以就算未來有人放寬 `keys` 的來源,`_counted` 這一層還是會擋住別的租戶的列被算進金額;但要注意這只擋金額計入,不擋「列出來」本身——目前因為 `keys` 只會是已篩過的鍵,這條路徑實際上不會把別租戶的列塞進回傳的 tuple,所以現在不成立為漏洞,只是提醒這層防禦不是靠 SQL 本身的租戶條件,單靠 `_counted` 這層防禦線;如果之後有人改成把「全部鍵」丟給 `counted_first_rows`,`_counted` 仍會把別租戶的金額算成 0、但那一列的 `key/task_id/campaign_id/started_at` 等身分欄位還是會被列進回傳結果(因為 `_counted_rows` 是先算 `amount`,`amount > 0` 才收,別租戶金額算 0 就不會被收——所以其實這條路也擋住了,不是只擋金額)。整體上目前程式碼與呼叫路徑都沒有真的洩漏別租戶列的口子。
- `stopped` 走 `stop_count`/`stops` 都是「歸屬那個收件表」+ 租戶固定條件雙重限制,無跨租戶洩漏。

第 1 輪 `security-F1`(`aggregate_audit` 不擋極寬時間範圍,目前沒有外部呼叫端)已經處置為「文件記載代價,留到 Phase 9 決定上限」,這輪程式碼與文件都照這個處置做了(`docs/rtb-production-agent-demo-knowledge/Systems/可觀測查詢.md` 新增了那一行,`src/rtb/executor/observability.py` 模組說明也補了對應段落),沒有再檢查到新的洞,這裡不重報。

## 結論
這一輪要看的三個點(IN 子句拼裝、stop_count_query 拼條件、交易歸屬核對能不能繞過、稽核明細跨租戶洩漏)都沒有找到可利用的漏洞,判定 `severity: clean`。
