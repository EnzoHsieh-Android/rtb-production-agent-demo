severity: clean
# 資安審查報告 — code-phase9-inc2 增量 2(r1-snapshot-src.patch)

severity: clean

以攻擊者視角逐一檢查四個面向,材料範圍內(`src/rtb/ops/metrics.py` 新指標模組、`src/rtb/analyzer/task_store.py`/`instrumented.py`/`dsp_client.py` 的 `ToolEndpoint` 封閉列舉、`src/rtb/executor/attempt_store.py`/`inbox_store.py` 的窗口讀取函式)沒有找到可被不可信輸入利用的洞:

- **有界標籤**:所有會進標籤的欄位都經過封閉處理——租戶用設定檔白名單(`_Resolver.tenant`,不在名單歸「未知」)、`BLOCK_REASON`/`DSP_CALL_KIND`/`DSP_CALL_RESULT`/`ANALYZER_ENDPOINT` 都經 `_closed()` 對照固定列舉、`TERMINAL_KIND` 只在 `event.kind in TERMINAL_KINDS`(4 個固定字面值)通過後才輸出、`PROGRAM_VERSION` 有「窗內最多留 20 種、其餘併成 other」的機制且來源是硬編碼常數 `rtb.PROGRAM_VERSION = "0.9.1"`(非外部輸入)。`ToolEndpoint` 更在寫入時(`task_store.record_tool_call`)用 `type(endpoint) is not ToolEndpoint` 嚴格擋掉任意字串、且拒收 `OTHER`,舊列讀出時用 `_endpoint()` 把列舉外的值收斂成 `OTHER`——這正是把先前「端點是任意字串」的潛在無界標籤面關掉。輸出裡唯一帶「自由文字」的是 `exemplars`(task_id),而 task_id 在提案解析與任務建立時都受 `ID_PATTERN`/`is_id`(`[A-Za-z0-9._:-]{1,128}`)約束,且每個樣本上限 `MAX_EXEMPLARS = 3` 並去重,不會被灌爆。
- **查詢參數**:`tool_calls_between_query`/`dsp_calls_between_query`/`lifecycle_events_between_query`/`terminal_rows_between_query` 一律用 `?` 綁參數傳 `since`/`until`,f-string 只拼接程式內固定的欄位名或固定列舉值清單(`DSP_CALL_FIELDS`、`_LIFECYCLE_FIELDS`、`_TERMINAL_LIST`),沒有任何外部字串進入 SQL 文字,不存在注入面。24 小時窗上限由 `_check_window()` 在 `collect_window()` 函式本體第一行强制(不只是 CLI 層),繞不過;四張表都各自補齊了對應的時間或部分索引(`tool_calls_by_time`、`attempts_terminal_by_time` 為本增量新增,`dsp_calls_by_time`、`lifecycle_events_by_time` 既有),查詢成本隨窗內筆數而非全表筆數增長。本增量沒有新增「租戶篩選」的 SQL 參數面(CLI 只有 `--executor-db`/`--analyzer-db`/`--tenants-config`/`--since`/`--until`/`--now`)。
- **終端控制字元**:輸出經 `json.dumps(..., ensure_ascii=False)`,但落入輸出的自由文字只有已受 `ID_PATTERN` 限制的 task_id(exemplars),其餘欄位(labels、status、note)全是程式內固定字串或封閉列舉值,沒有機密欄位(如 `error_detail`)被帶出這個模組。
- **唯讀保證**:`ops/metrics.py` 只透過 `ReadOnlyInbox`/`analyzer.task_store.TaskReader` 存取,兩者底層都是 `sqlitekit.connect_read_only()`(`mode=ro` URI,SQLite 驅動層級擋寫),且新增的讀取方法(`pending_snapshot`、`lifecycle_events_between`、`dsp_calls_between`、`terminal_rows_between`)都透過 `_own_read`/`_read_conn` 驗證 `tx.conn` 確實綁定同一個唯讀連線,型別檢查用 `type(tx) not in (...)`,無法用寫入交易或偽造物件繞過。

檔案位置(供交叉核對,未在材料 patch 內、屬查證用):
- `src/rtb/domain/_checks.py:14` `ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")`
- `src/rtb/domain/proposal.py:167` task_id 於提案解析時以 `ID_PATTERN` 驗證
- `src/rtb/sqlitekit.py:75-83` `connect_read_only()` 用 `mode=ro` URI
- `src/rtb/executor/inbox_store.py:547-550` `_own_read()` 驗證 `tx.conn is self._conn`
- `src/rtb/__init__.py:8` `PROGRAM_VERSION = "0.9.1"`(硬編碼常數,非外部輸入)

以上四個攻擊面在本增量範圍內都有對應且正確生效的邊界控制,沒有可被利用的洞。
