severity: major

# 第 2 輪 correct 席報告(code-phase6-inc4)

範圍:r2-delta.patch 對收件口模組 `stop_count`/`stops`、嘗試紀錄模組
`counted_first_rows_started`/`counted_first_rows`、稽核明細「通過的」/「目前佔額度的」欄位語意、
holding 加總與已用額度的一致性、空集合與大量鍵的 IN 子句處理。已在 `/tmp/rtb3b_copy`(複製自
`/Users/enzo/rtb-3b`,唯讀規則不動來源)實跑驗證,repo 本身未修改。

## F1 停下次數查詢丟了「時間必須帶時區」的守門,naive datetime 會靜默算錯而不是丟例外

severity: major
blocking: 是 — 會做出錯的行為:呼叫端傳沒有時區的時間,原本應該立刻丟 `ValueError`,現在會被
`datetime.astimezone(UTC)` 悄悄當成本地時間算,查出來的停下次數會是錯的且不會有任何錯誤訊號。

引句:「("at >= ?", None if since is None else _iso(since)),」

第 1 輪之前,`observability.py` 自己的 `stop_count_query`/`_count` 是透過
`attempt_store.iso`(即 `_iso`)組時間字串,而 `attempt_store._iso` 一定先呼叫
`_require_aware`,沒有時區就丟 `ValueError("時間必須帶時區")`。第 1 輪把 `stop_count_query`
搬進 `inbox_store.py`(架構席的折衷,SQL 只能由擁有那張表的模組寫),但改用了
`inbox_store.py` 自己的 `_iso`——那支 `_iso` 只有 `moment.astimezone(UTC).strftime(...)`,
沒有 `_require_aware` 檢查(見 `src/rtb/executor/inbox_store.py:285`)。結果是
`observability.aggregate_stop_count`、`observability.table_full_deferral_count`,以及
`aggregate_audit` 裡算 `stopped` 那段(`store.stops(...)`),現在收到沒有時區的 `since`/
`until` 都不會再拒絕,而是把它當成本地時間去比對 `write_stops.at`(UTC 字串)。

`aggregate_audit` 因為後面還會呼叫 `attempt_store.counted_first_rows_started`(走
`attempt_store._iso`,還是有 `_require_aware`)才整體丟出例外,所以最終還是失敗——但
`aggregate_stop_count`/`table_full_deferral_count`(合約 [S360]、[S361])完全沒有其他守門,
naive datetime 傳進去會直接回一個看起來正常、實際算錯的整數,不會有任何錯誤或警告。

重現:
```
$ /Users/enzo/rtb-production-agent-demo/.venv/bin/python /tmp/naive_test.py
aggregate_stop_count with naive datetimes did NOT raise, result: 0
```
(`/tmp/naive_test.py` 直接呼叫 `observability.aggregate_stop_count(store, tx, tenant=TENANT,
since=naive_since, until=naive_until)`,`naive_since`/`naive_until` 是沒帶 `tzinfo` 的
`datetime`;原本預期會丟 `ValueError("時間必須帶時區")`,實際印出的是一個整數結果。)

全專案搜尋 `時間必須帶時區` 只出現在 `attempt_store.py` 一處定義,`inbox_store.py`/
`observability.py` 沒有任何測試覆蓋「naive 時間該丟例外」這件事(`grep -rn
"時間必須帶時區" tests/` 沒有命中),所以這個洞不會被既有測試殺出來。

修法建議:`inbox_store.py` 的 `_iso`(`stop_count`/`stops`/`stop_count_query` 用到的那支)
補上跟 `attempt_store._require_aware` 等價的檢查,或者這幾支查詢一律呼叫
`attempt_store` 匯出的、已經有守門的版本,不要各模組各自維護一份時間格式化邏輯。

---

## 其他檢查項,沒發現問題

- **收件口模組 `stop_count`/`stops` 的篩選與排序**:`stop_count_query` 動態組 `WHERE`(只在
  篩選值非 `None` 時加子句)跟移動前邏輯逐字相同,只是時間字串改用 `inbox_store._iso`(見
  F1)。`stops` 固定 `tenant` 必填、`ORDER BY at, id`,跟 `Stopped` dataclass 的欄位順序
  (`key, task_id, revision, content_hash, campaign_id, amount, used, limit, at`)對得上
  SELECT 的欄位順序(`key, task_id, revision, content_hash, campaign_id, amount, used,
  cap, at`)。兩支都先呼叫 `self._own(tx)`,`_own` 檢查 `tx.conn is not self._conn`,
  用另一個 `InboxStore` 開的交易呼叫會丟 `NotInTransaction`——已用新增的
  `test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox` 驗過,本地
  `pytest tests/executor/test_observability.py tests/executor/test_attempt_store.py`
  80 個測試全過。

- **`counted_first_rows_started`/`counted_first_rows` 的 SQL 與逐列計入**:兩支都收斂到
  `_counted_rows`,同一條 `_counted` 規則(跟 `aggregate_holdings`/`aggregate_used` 共用),
  沒有另外寫一份計入邏輯。`counted_first_rows` 用 `key IN (...)` 重查第一列,原始資料
  (`tenant`/`reserved_amount`/`action`/`proposal_json`)只寫一次不會變,所以跟
  `aggregate_holdings` 算出來的金額必然一致,已用大量鍵實跑驗證(見下)。

- **「通過的」(`passed`)與「目前佔額度的」(`holding`)欄位語意**:兩邊现在都回
  `Entry`(帶 `key/task_id/revision/campaign_id/amount/started_at/state/counted_now/
  legacy`),欄位定義一致;`holding` 就是 `counted`(`aggregate_holdings` 算出的鍵集合)
  重查回來的 `Entry`,所以每一筆 `counted_now` 必為真,`passed` 的 `counted_now` 才有分岔
  意義,語意沒有互相打架。

- **holding 加總是否仍等於已用額度**:`aggregate_audit` 內 `aggregate_holdings` 與
  `counted_first_rows` 都在同一個已經拿到寫入鎖的交易裡執行,中間沒有可能被其他連線插入
  寫入(執行行程資料庫交易入口開的是握寫入鎖的交易),兩段查詢對到的是同一個快照。實跑
  2000 把鍵(全部 `verified` 且在 24 小時窗內)驗證:
  ```
  holdings count: 2000
  used: 20000
  audit holding entries: 2000
  audit.utilization.used: 20000
  OK - matches
  ```
  `sum(holding.amount) == aggregate_used(...) == audit.utilization.used` 三邊一致。

- **空集合**:`counted_first_rows(tx, tenant, ())` 在 `wanted` 為空時直接回 `()`,不會組出
  `key IN ()` 的壞語法;`aggregate_audit` 在沒有任何 holding 時整段跑完回
  `passed=() holding=() used=0`,不丟例外(已用 `/tmp/empty_holdings_test.py` 驗過)。

- **大量鍵的 IN 子句參數數量上限**:`counted_first_rows` 用一個 `key IN (?, ?, …)`,參數個
  數等於 `counted` 的鍵數(理論上不受 `MAX_UNRESOLVED=20` 限制,因為已驗證且在 24 小時窗
  內的鍵沒有上限)。本機 venv 的 `sqlite3.sqlite_version` 是 3.53.3,實測 2000、32766、
  40000 個佔位符都不會撞 `SQLITE_MAX_VARIABLE_NUMBER`(該限制自 SQLite 3.32.0 起預設是
  32766,舊版本或某些編譯選項可能仍是 999)。目前沒有任何機制限制單一租戶 24 小時內能有
  多少筆「已驗證」寫入撐到這個量級,理論上在用較舊 SQLite 編譯選項的環境還是有機會撞到
  `sqlite3.OperationalError: too many SQL variables`。這個風險跟筆記裡已經寫的「稽核明細
  時間範圍不設上限、Phase 9 才決定上限」是同一類尚未收斂的問題,不算這次修正引入的新洞,
  現有環境也沒有重現到,所以不升到獨立 `## F` 項;留意未來排查「查一個长期活躍租戶的稽核明細
  突然噴 SQL 錯誤」時,先看這裡。
