severity: clean

第 2 輪修正(`aggregate_holdings` 改成一次查詢帶出身分、`iso` 時區檢查)逐行核對過,沒有找到可被利用的跨租戶資料外洩、注入或交易歸屬繞過。

## 核對過的路徑

- `aggregate_holdings`(`src/rtb/executor/attempt_store.py:379`)兩支 SQL 都在 `WHERE` 子句用參數化的 `(f.tenant = ? OR f.tenant IS NULL)` 過濾,新加的 `_first_row_columns(alias)` 只拼接模組內寫死的欄位名稱(`_FIRST_ROW_FIELDS`),`alias` 兩處呼叫都是常數字串 `'f'`,不是外部輸入,不構成注入面。
- 拿掉的 `counted_first_rows`(依鍵 `IN (...)` 回查)原本就沒有在 SQL 加租戶過濾,是靠呼叫端傳入的鍵集合本來就已經是同租戶查詢篩出來的,拿掉這支之後改成同一次查詢直接帶欄位,過濾邏輯沒有變弱,反而少了「兩次查詢之間資料變動」的視窗(round 2 收貨紀錄已載明)。
- `_counted(row, tenant)`(`attempt_store.py:461`)的 `owner == tenant` 判斷在新流程下確實是死碼(SQL 已經先過濾過,`owner` 只會是 `tenant` 或 `None`),但死碼不放大攻擊面,不算開洞。
- `CountedFirstRow` 新帶出的 `task_id`/`revision`/`campaign_id`/`started_at` 這幾個身分欄位,在 round 1 就已經透過 `counted_first_rows` 用同樣的形狀回傳給 `aggregate_audit`,round 2 只是把「兩段查詢」合併成「一段查詢」,曝露的欄位集合沒有變大,不是新開的資料面。
- 舊列(`tenant IS NULL`)"算進每一個租戶"是既有設計決策(`docs/.../外部寫入嘗試紀錄.md` 的「總曝險預留」一節與 Phase 6 設計文件),不是 round 2 這次修正引入的行為。
- `iso`(`attempt_store.py:198`)只是把既有 `_require_aware` + `_iso` 包一層公開出去,`inbox_store.py` 的 `stops`(`inbox_store.py:735`)與 `stop_count_query`(`inbox_store.py:779`)改叫它之後,沒帶時區一樣會 `raise ValueError`,行為跟嘗試紀錄模組一致,沒有放寬檢查,也沒有新的字串拼接進 SQL(仍是綁定參數)。
- `InboxStore.stops`/`stop_count`(`inbox_store.py:715`、`726`)呼叫前都先 `self._own(tx)`,確認交易是這個收件口自己開的連線;`attempt_store` 各函式也都先 `_conn(tx)` 檢查交易型別與開啟狀態。round 2 這次改動沒有動到這兩道檢查,交易歸屬沒有被繞過。
- 新增測試 `test_the_audit_handles_more_holdings_than_sqlite_bind_parameters` 把連線的 SQLite 參數上限調到 100、灌 110 把鍵,實際重跑確認轉綠(`.venv/bin/python -m pytest tests/executor/test_observability.py -q` → 22 passed),證實 round 2 抓到的「逐鍵當查詢參數」DoS/崩潰面確實補上,沒有殘留。

引句:「鍵的數量沒有上限,逐一當查詢參數會超過 SQLite 的參數上限(舊版 999;代碼審第 2 輪兩席 Codex)」

沒有可被利用的洞。
