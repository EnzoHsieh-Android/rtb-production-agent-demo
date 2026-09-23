severity: clean

鏡頭:增量 4 對既有 `aggregate_used`/`_counted`/`test_aggregate_limit.py` 的相容性影響(材料:`governance/review-reports/code-phase6-inc4/r1-snapshot.patch`,對照 8616c96..8de1054)。

## 結論
沒發現會做出錯的行為、破壞合約、資料損壞或測試假綠的問題,全部觀察都確認沒事。`tests/executor/test_aggregate_limit.py` 30 條全過(`/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider tests/executor/test_aggregate_limit.py -q` → `30 passed`)。

## 逐項確認

**開始一筆的額度判斷行為**:`begin()` 仍是呼叫模組層級名字 `aggregate_used(tx, reservation.tenant, now)`(`src/rtb/executor/attempt_store.py:414`),`aggregate_used` 本身現在只是 `sum(holding.amount for holding in aggregate_holdings(tx, tenant, now))`。兩段 SQL(已驗證窗口內 + 未結案)欄位只多選了 `f.key`,篩選條件、參數、順序都沒變,回傳仍是 `int`,呼叫端(`execution.py:291`、`observability.py:104`)都當 `int` 用,沒人假設別的型別。判斷結果(超不超過門檻)不變。

**握鎖時間**:兩段查詢本身沒變,只是多包一層 `Holding` dataclass 與一次 generator→tuple 具現化。既有效能測試(`tests/executor/test_aggregate_limit.py:330-357`,300000 列、斷言 `agg_time <= 2 * pick_time + 0.005`)包含在通過的 30 條裡,沒有變慢到觸發門檻。

**壞舊快照丟的例外**:`aggregate_holdings` 裡 `Holding(key, _counted(rest, tenant), rest[0] is None) for key, *rest in rows` 這段 generator 是在 `tuple(...)` 具現化時才逐一求值,`_counted` 拋 `CorruptedAttemptRow` 的時機沒變(仍在建出 `Holding` 之前就丟),不會被後面 `if holding.amount > 0` 的過濾吞掉。`test_an_unreadable_old_snapshot_halts_when_the_table_is_full_too` 通過,確認例外照樣一路傳到 `ExecutorHalted`。

**新過濾 `if holding.amount > 0` 會不會偷改總額**:`_counted`/`counted_amount` 的回傳只有 0 或正整數——`owner is not None` 分支要嘛回 `int(amount)` 要嘛回 0;舊列分支的 `new_budget` 在 `domain/proposal.py:145` 已經用 `_positive_int` 檢查過是正整數。全程式沒有負的 `reserved_amount` 或 `new_budget` 路徑,所以過濾掉 `amount == 0` 的列不改變 `sum()`,`aggregate_used` 的結果跟拆分前一致。

**既有測試攔截點**:`test_aggregate_limit.py` 兩處 `monkeypatch.setattr(attempt_store, "aggregate_used"/"_counted", ...)`(第 193-216 行的並行測試、第 492-519 行的「別租戶列在資料庫裡就濾掉」測試)都還攔得到——`begin()` 呼叫的是裸名 `aggregate_used`,`aggregate_holdings` 內部呼叫的是裸名 `_counted`,都是模組全域查找,monkeypatch 換掉模組屬性後,不論是直接呼叫還是透過 `aggregate_holdings` 間接呼叫都會命中補丁版本。兩條測試都在通過的 30 條裡。

**`test_aggregate_limit.py` 模擬舊資料庫多拿掉的索引**:新增的 `conn.execute("DROP INDEX IF EXISTS attempts_first_rows_by_tenant")` 只是配合 SQLite「欄位被索引參照時不能直接 `ALTER TABLE ... DROP COLUMN`」的限制,拿掉索引後才拿掉 `tenant`/`reserved_amount` 欄位,拿掉的動作跟索引本身的查詢邏輯無關,沒有削弱這支測試原本要驗的「Phase 6 之前的舊資料庫也要能推出已用額度」的意圖。

**新公開函式(`iso`、`connection`、`counted_amount`)會不會讓別的模組繞過交易核對**:
- `connection(tx)` 就是 `_conn(tx)`,仍會檢查 `type(tx) is ExecutorTransaction and tx.is_open`,拿到的是同一條已在交易裡、已握寫入鎖的連線,不會繞過核對本身;只是把「能對這條連線下什麼 SQL」的限制從「只能呼叫 attempt_store 預先寫好的函式」放寬成「`observability.py` 可以自己拼 SQL 查」。`observability.py` 目前只用它做 `SELECT`(唯讀查詢),符合模組文件自陳的威脅模型「防忘記、不防刻意繞過」(`attempt_store.py` 開頭模組說明本就這樣定調,`i4c-common.txt` 第 7 點也把這個列為既定範圍,不算新問題)。
- `iso(moment)` 是純格式化(`_iso` 的公開別名),不碰交易,無風險。
- `counted_amount(row, tenant)` 是純函式(`_counted` 的公開別名),不碰交易、不碰資料庫,無風險。

## 沒對上的地方
沒有。程式行為、既有測試、設計偏離(查詢一二五不放收件口模組)都跟 `i4c-common.txt` 描述的一致,沒有需要另外立 Issue 或標「以程式碼為準」的落差。
