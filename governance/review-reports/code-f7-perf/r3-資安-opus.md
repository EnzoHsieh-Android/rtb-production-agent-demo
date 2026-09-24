severity: major

## 第 2 輪驗收

| 第 2 輪條目 | 這輪改了什麼 | 驗收結果 |
|---|---|---|
| 1. 卡住的舊列只要比最新 64 筆舊列還舊,退回路徑的工作量就是原算法的兩倍 | 便宜偵測改成兩步。第一步 `legacy_recent_query` 看最新 8 筆。第二步 `legacy_open_query` 從最舊的往新找第一把沒有終點的舊鍵,不設筆數上限 | **已修好**。第 2 輪的重現改用 2 萬筆窗內列、1 把 500 天前卡住的舊鍵,前面再放 0、64、500 把比它新的已結案舊鍵。三種情形都判得出來,步數也都跟原算法差不多:<br>`0` 時 `11000/11000`<br>`64` 時 `11018/11016`<br>`500` 時 `11121/11116`<br>回傳值都一樣。不過這個修法帶出新的退步,見發現 1。|
| 2. 租約時間讀不懂時,讀租約的函式丟 ValueError 或 TypeError,讓執行迴圈直接崩潰 | 新增 `_lease_time`:不是帶時區的 ISO 時間就丟 `CorruptedInboxRow`。`process_one` 收到後改丟 `ExecutorHalted("unreadable_message")`。對帳那條路由 `reconcile_all` 先記下,處理完其他鍵後停機 | **已修好**。`test_an_unreadable_lease_time_halts_instead_of_crashing` 用 `'garbage'` 和沒帶時區的時間兩種值測,都會乾淨地停機,而且一次都沒退避。整數或二進位的值會先丟 TypeError,這裡也一併攔下。|

## 發現 1:所有舊鍵都已結案時,每次查已用額度都要把全部舊鍵讀一遍;快路徑反而比原算法慢 2 到 7 倍,比第 2 輪版慢 5 到 10 倍
severity: major
blocking: 是

引句:「"ORDER BY f.written_at LIMIT 1", ())」
引句:「最壞情形是卡住的舊鍵夾在大量已結案舊鍵中間」
引句:「把卡住的舊鍵人工結案就解除」
file: `src/rtb/executor/attempt_store.py:662`、`src/rtb/executor/attempt_store.py:677`、`docs/rtb-production-agent-demo-knowledge/Verification/F7效能驗收紀錄.md:64`、`docs/rtb-production-agent-demo-knowledge/Verification/F7效能驗收紀錄.md:66`

**問題在哪**
- 第二步 `legacy_open_query` 只有碰到沒有終點的舊鍵才會停。一把都沒有時,它會把每一把沒記租戶的第一列都讀過,每一列還要查一次終點索引。
- 升級之後最常見的狀態正是「舊鍵很多、全都結案了」。這時每次查已用額度都要全讀一遍,而且是在全域寫入鎖裡讀。
- 程式說明和驗收紀錄把最壞情形寫成「卡住的舊鍵夾在中間」。實際上,沒有卡住的舊鍵才最壞,而且每次呼叫都會碰到。
- 驗收紀錄的事件處理寫「把卡住的舊鍵人工結案就解除」。這是反的:結案之後,第二步就沒有地方可以提早停,變成每次都全讀。
- 測試 `test_many_closed_legacy_rows_still_take_the_fast_path` 只斷言偵測步數不超過 960 把舊鍵的量級,沒有拿去跟原算法比。所以沒抓到這個退步。

**具體例子**
- 輸入:比照 [S341] 的 30 萬列歷史,但沒有卡住的舊鍵。窗內有 3000 或 2 萬筆 t-a 已驗證列,其餘都是窗外已結案、沒記租戶的舊鍵。
- 預期:乾淨資料走快路徑,握鎖時間不超過原算法,至少不比第 2 輪版差。
- 實際:在 `/tmp/資安-opus-f7cr3` 複本寫了一支一次性測試,每種情形取 5 次裡最快的一次(已刪除)。

```
685fb5d(這輪)
s341-like window=3000  legacy_closed=297000: picking=0.1585s fast=0.1768s ref=0.0263s detect=0.1588s
s341-like window=20000 legacy_closed=280000: picking=0.1575s fast=0.1885s ref=0.0913s detect=0.1484s
steady closed_legacy=100000: detect_steps=15004 fast_steps=20093 ref_steps=5078 fast=0.0569s ref=0.0045s equal=True
be19aad(第 2 輪,只換 attempt_store.py)
s341-like window=3000  legacy_closed=297000: fast=0.0175s ref=0.0267s detect=0.0001s
s341-like window=20000 legacy_closed=280000: fast=0.0364s ref=0.0902s detect=0.0001s
steady closed_legacy=100000: detect_steps=26 fast_steps=5115 ref_steps=5078 fast=0.0039s ref=0.0043s
```

- 回傳值跟原算法相同(`equal=True`),不會多放行,也不會多擋。
- [S341] 的預算是挑選時間的兩倍加 5 毫秒,約 0.32 秒。這輪的 0.18 秒還在預算內。
- 問題是握鎖時間:這個狀態下,快路徑比它要取代的原算法慢 2 到 6.7 倍,比第 2 輪版慢 5 到 10 倍。F7 這項工作本來就是為了縮短握鎖時間。
- 攻擊者造不出新的沒記租戶的列,所以這不能被外部放大。成本只取決於升級前留下的舊鍵數量。

**建議**
- 舊鍵不會再增加,結案了也不會變回未結案。所以「沒有未結案的舊鍵」這個結果一旦成立,就永遠成立。
- 第二步查到「沒有」之後,就在這條連線或這個行程裡記住結果,之後不再查。這樣就不會漏判,穩定狀態的成本也會回到第 2 輪版的水準。
- 要把驗收紀錄裡的最壞情形和事件處理改正確。測試也要補一條:所有舊鍵都已結案時,快路徑步數不超過原算法。

## 發現 2:is_lock_contention 公開後,拿到不帶錯誤碼的資料庫錯誤會丟 AttributeError,蓋掉原本的錯誤
severity: minor
blocking: 否

引句:「primary_code = exc.sqlite_errorcode & 0xFF」
引句:「其他模組要分也用這支」
file: `src/rtb/sqlitekit.py:27`、`src/rtb/executor/inbox_store.py:1318`

**問題在哪**
- `sqlite3` 模組自己產生的 OperationalError 沒有 `sqlite_errorcode`(例如建函式失敗的 `Error creating function`),程式碼自己丟的也沒有。
- 函式說明要其他模組也用這支判斷。但拿到這種錯誤時,這支函式會自己丟 AttributeError,原本的錯誤只剩在例外鏈裡。
- 連這輪新加的測試也要手動補 `contention.sqlite_errorcode = sqlite3.SQLITE_BUSY` 才能用。

**具體例子**
- 輸入:`is_lock_contention(sqlite3.OperationalError("database is locked"))`
- 預期:回 False,或回 True。
- 實際:`AttributeError: 'OperationalError' object has no attribute 'sqlite_errorcode'`(Python 3.14.6,SQLite 3.53.3 實測)。

**影響範圍**
- 讀租約那一句是真正的 SELECT,產生的錯誤一定帶錯誤碼,所以現在的用法碰不到。
- 就算碰到,也是往外丟、讓行程停下,屬於保守失敗。
- 建議改用 `getattr(exc, "sqlite_errorcode", None)`,拿不到就回 False。

## 重點題目的結論

**1. 讀租約錯誤的新分類:沒找到可以利用的洞。**
- 會回空值、讓程式不再重試的,只有 SQLITE_BUSY 和 SQLITE_LOCKED,包含它們的擴充碼。其他 OperationalError 和 DatabaseError(例如資料庫檔損壞)都原樣往外丟,不會被當成忙碌吞掉。
- 能不能讓執行端停機?
  - WAL 模式下,讀不會被別人的寫入擋住。攻擊者從收件口灌請求造寫入鎖,讀租約那一句不會因此收到忙碌。
  - 就算收到忙碌,結果也只是不再試、休息一輪,連續幾輪才因忙碌停機。這跟原本收件口忙碌的處理一樣。
  - 租約時間讀不懂才會停機。但只有執行端自己會寫這一欄,外部改不到。
- 丟出的 `ExecutorHalted` 發生在 DSP 寫入之後、紀錄寫進去之前。這把鍵會停在嘗試中,重啟後由恢復程序和對帳收,跟其他停機情形一樣。
- 對帳那條路讀租約時丟的 `CorruptedInboxRow`,會被 `reconcile_all` 記下,處理完其他鍵再停機,不會被默默跳過。

**2. 兩步舊鍵偵測:騙不成少算;可以拖長握鎖時間,但不是攻擊者能控制的。**
- 兩步偵測都只會讓程式改走原算法。偵測漏判時,加總那一趟的精確偵測還是會接著判,而且兩段候選都包含沒記租戶的列。所以毀損資料騙不成少算。
- 毀損資料能不能讓第一步漏看?
  - 假設 8 筆沒記租戶的列,時間欄被改成二進位值。倒序排列時,二進位排在文字前面,這 8 筆會佔滿第一步。
  - 結果只是窗內剛驗證完的舊鍵要等加總那一趟才被判出來,工作量回到第 1 版的水準,算出來的數字不變。
- 握鎖時間拉長的問題見發現 1。攻擊者造不出新的沒記租戶的列,沒辦法放大。
- `unresolved_count` 移到偵測之後、try 之前:各種情形下呼叫它的次數跟原算法一樣,判斷條件也一樣。

**3. is_lock_contention 公開:只看到發現 2 這個小問題。**
- 目前只有 `sqlitekit` 自己和讀租約兩個地方用它。沒有地方把「交易開起來之後」的忙碌也當成沒開起來。
- `InboxBusyNotStarted` 仍然只在 `began` 為 False 時丟出。收件口在 `REJECTION_STATUS` 補登記了它,對外照樣回 503,不會露出新的錯誤類別。

實驗都在 `/tmp/資安-opus-f7cr3` 和 `/tmp/資安-opus-f7cr3-r2` 兩份複本裡做,都已刪除,沒有留下行程。被審的工作樹沒改。那裡原本就有的 `docs/.governance-log.jsonl` 修改和未追蹤的 `governance/review-reports/code-f7-perf/`,都不是這一席動的。

## 看過的檔(r3-snapshot.patch 全份,含 r3-delta.patch)
- `/Users/enzo/rtb-f7-rev/governance/review-reports/code-f7-perf/r3-snapshot.patch`
- `/Users/enzo/rtb-f7-rev/governance/review-reports/code-f7-perf/r3-delta.patch`
- `/Users/enzo/rtb-f7-rev/governance/review-reports/code-f7-perf/r2-資安-opus.md`
- `/Users/enzo/rtb-f7-rev/claims/aggregate-blast-radius.json`
- `/Users/enzo/rtb-f7-rev/claims/concurrency.json`
- `/Users/enzo/rtb-f7-rev/claims/idempotency-unknown-outcome.json`
- `/Users/enzo/rtb-f7-rev/claims/permission-guardrail.json`
- `/Users/enzo/rtb-f7-rev/claims/prompt-injection.json`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Issues/F7端到端在CI上偶爾超過60秒.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Projects/F7效能_計劃.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/外部寫入嘗試紀錄.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/寫入能力憑證.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Verification/F7效能驗收紀錄.md`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/attempt_store.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/capability_signer.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/execution.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/inbox_server.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/inbox_store.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/runner.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/sqlitekit.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_aggregate_fast_path.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_approval.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_attempt_store.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_config_cache.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_inbox_server.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_read_only.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_result_write_retry.py`

2 條,blocking 1。
