severity: major

### 1. `tool_calls_between` 對「無時區」時間吃不出錯,窗界會被本機時區悄悄位移,與同批的 `dsp_calls_between`/`lifecycle_events_between` 行為不一致
severity: major
blocking: 是
引句:「def tool_calls_between_query(since: datetime, until: datetime) -> tuple[str, tuple[str, ...]]:」

`task_store.py` 的 `tool_calls_between_query`(`src/rtb/analyzer/task_store.py:140`)把 `since`/`until` 直接丟給模組內的 `_iso()`(`src/rtb/analyzer/task_store.py:217`)。這支 `_iso` 只有 `moment.astimezone(UTC).strftime(...)`,**沒有**像 `attempt_store.py` 的 `_iso`(`src/rtb/executor/attempt_store.py:383`)那樣先呼叫 `_require_aware()`(`src/rtb/executor/attempt_store.py:378`)拒收無時區(naive)的 `datetime`。同一個增量新增的另外三支窗口讀取函式——`attempt_store.dsp_calls_between_query`(`src/rtb/executor/attempt_store.py:877`,用會擋 naive 的 `_iso`)與 `inbox_store.lifecycle_events_between_query`(用 `attempt_store.iso()`,同樣會擋)——都會擋,唯獨分析端這支不擋,四支「窗口讀取函式」的時間輸入合約不一致。

觸發情境:呼叫端(`rtb.ops.metrics` 的 `--since`/`--until` 用 `datetime.fromisoformat` 解析 CLI 參數,操作者少打一個時區尾碼,例如打 `2026-09-24T01:00:00` 而不是 `...Z`)傳入的是 naive `datetime`。經實測驗證(在 `/private/tmp/.../scratchpad/p9i2review` 建的暫存 `analyzer.db`,系統時區為 UTC+8):
- 用 UTC 01:00–03:00(帶時區)查一筆真實時間為 UTC 02:00 的呼叫紀錄,`reader.tool_calls_between(...)` 正確回傳 1 筆。
- 用「看起來一樣」但沒帶時區的 01:00–03:00 呼叫同一支函式,`_iso()` 把它當本機時間換算成 UTC 17:00(前一天)–19:00,**不丟任何例外**,回傳空 tuple——同一筆紀錄悄悄被漏掉,沒有任何錯誤訊息。
- 對照組:把同樣 naive 的 `since`/`until` 丟進 `attempt_store.dsp_calls_between_query`,立刻丟 `ValueError: 時間必須帶時區`。

在目前唯一接線的呼叫路徑 `collect_window()` 裡,因為 `_read_executor()`(呼叫 `attempt_store.dsp_calls_between`)排在 `_read_analyzer()`(呼叫 `tool_calls_between`)之前,同一組 naive `since`/`until` 會先在執行端那支炸出 `ValueError`——但 `run()`(`src/rtb/ops/metrics.py:818`)的 `except` 只接 `WindowTooLong`/`SigningRefused`/`FileNotFoundError`/`DatabaseNotUpgraded`(`src/rtb/ops/metrics.py:830-839`),沒有接這個 `ValueError`,結果是整支 CLI 用未捕捉例外的原始 traceback 崩潰,而不是規格說的「窗超過 24 小時、資料庫還沒升級、讀取不穩定各有固定結束代碼與訊息」那種乾淨錯誤。更嚴重的是:`tool_calls_between`/`tool_calls_between_query` 本身的弱防護是獨立缺陷——只要日後直接呼叫這支函式(不透過 `collect_window` 那個「先炸執行端」的巧合順序),例如未來的工具、測試或別的維運腳本,就會拿到「悄悄漏資料」而非「拋錯」的結果,這正是這次審查要看的「ISO 字串時區與格式是否一致」的核心風險。

建議修法:讓 `task_store.py` 的 `_iso`(或至少 `tool_calls_between_query`)比照 `attempt_store._require_aware` 拒收 naive `datetime`,同時讓 `rtb.ops.metrics.run()` 補一個對這類輸入驗證錯誤的 `except`,回乾淨的 `EXIT_BAD_CONFIG` 訊息而不是讓 traceback 外洩。

### 2. 新增的時間索引只在寫入開法第一次開啟時建立;唯讀連線開舊資料庫不會失敗,但也不會建索引,期間指標查詢會退化成全表掃描
severity: minor
blocking: 否
引句:「CREATE INDEX IF NOT EXISTS tool_calls_by_time ON tool_calls (at);」

`connect_read_only()`(`src/rtb/sqlitekit.py:75`,文件字串「不建新檔。不建表、不切日誌模式。」在 `src/rtb/sqlitekit.py:78`)明講唯讀開法不會執行 SCHEMA,`missing_schema()`(`src/rtb/sqlitekit.py:107`)也只檢查表與欄位,不檢查索引是否存在。所以新加的 `tool_calls_by_time`(`src/rtb/analyzer/task_store.py:70`)、`attempts_terminal_by_time`(`src/rtb/executor/attempt_store.py:70`)這兩個索引,只有在對應的寫入開法(`TaskStore`/`InboxStore`)第一次開啟該資料庫檔時,經由 `connect(..., schema=SCHEMA)` 的 `executescript` 才會被建出來;純粹只被 `TaskReader`/`ReadOnlyInbox` 唯讀開過的舊資料庫,不會失敗(不會丟 `DatabaseNotUpgraded`,因為表跟欄位都在),但在對應的執行迴圈/分析行程第一次以寫入模式重開之前,`rtb.ops.metrics` 對它跑窗口查詢時,`tool_calls_between`/`terminal_rows_between` 會退化成全表掃描而不是走時間索引——這正好違反規格「只限窗長、沒有時間索引,SQLite 仍會掃全表,成本隨歷史線性長」要防的那個情況,只是要等到部署後第一次啟動寫入行程才會自癒。屬於部署順序造成的暫時性效能問題,不是資料錯誤,測試(`tests/ops/test_window_readers.py`)因為固定先用 `InboxStore`/`TaskStore` 建表才驗證查詢計畫,沒有覆蓋「只被唯讀開過」這個路徑,所以沒有暴露出來。

建議修法(非必要,視上線流程風險決定):部署腳本或維運文件明講「升級後要先讓執行迴圈與分析行程各啟動一次(寫入開法)才能讓指標查詢吃到新索引」,或讓 `missing_schema`/唯讀開法額外檢查這幾支新索引存在與否,缺了就跟缺表一樣丟 `DatabaseNotUpgraded`。
