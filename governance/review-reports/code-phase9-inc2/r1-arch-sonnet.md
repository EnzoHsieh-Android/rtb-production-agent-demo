severity: major
# 審查報告

severity: major

### 1. version_conflict_rate 繞過既有 version_conflict_count,另開一條算法讀 dsp_calls

severity: major
blocking: 是

引句:「if call.error == DspErrorCode.VERSION_CONFLICT:」

`src/rtb/ops/metrics.py` 的 `_dsp()` 算 `version_conflict_rate` 時,直接掃 `attempt_store.dsp_calls_between()` 撈回來的 `dsp_calls` 表列,靠 `call.error == DspErrorCode.VERSION_CONFLICT` 分子分母。但 `attempt_store.py` 已經有 `version_conflict_count(tx, campaign_id=None)`(file: `src/rtb/executor/attempt_store.py:493`),讀的是 `attempts` 表的 `code` 欄(`OutcomeCode.VERSION_CONFLICT`),而且該函式的文件字面上就寫著「執行前檢查擋下的『版本已變』只記在收件表、會被清,長期計數等 Phase 9」(file: `src/rtb/executor/attempt_store.py:494-495`)——這句話等的正是這次(Phase 9 增量 2)的指標功能,結果沒有接上它,而是另開一條路直接讀 `dsp_calls` 表重算。

觸發情境:一次 DSP 寫入呼叫回版本衝突。
會出什麼錯:`dsp_calls` 表記的是「呼叫本身回了什麼錯」,`attempts.code` 記的是「這次嘗試最終定案的結果碼」,兩者在交易邊界、重試語意上不保證一一對應;`metrics.py` 算出來的 `version_conflict_rate` 和既有 `version_conflict_count()` 對同一段時間可能給出不同數字,以後任何人要查「版本衝突到底幾次」要先搞清楚該信哪一張表算出來的哪個數字。
建議修法:`version_conflict_rate` 改呼叫既有的 `attempt_store.version_conflict_count()`(必要時擴充它加時間窗參數),不要另開一條直接讀 `dsp_calls` 的路;真的有理由不能用它,要在 `metrics.py` 的文件裡寫明原因並讓兩邊數字對得上。

### 2. blocked/awaiting_approval/approval_released 直接算 lifecycle_events,跟 observability.py 既有的停下紀錄查詢是兩套算法

severity: major
blocking: 是

引句:「"blocked": _REASONED, "awaiting_approval": _REASONED, "approval_released": _REASONED,」

`src/rtb/ops/metrics.py` 的 `_event_counts()` 直接讀 `inbox.lifecycle_events_between()` 回來的事件,自己用 `_labels`/`_counts` 分組計數出 `blocked`、`awaiting_approval`、`approval_released`、`stale_rejections` 這幾個樣本。但 `src/rtb/executor/observability.py` 已經是這個領域(「總曝險停下次數、表滿延後份數、人工核可…」,file: `src/rtb/executor/observability.py:1-2`)的既有唯讀查詢先例,提供 `aggregate_stop_count`(file: `src/rtb/executor/observability.py:81-87`)、`table_full_deferral_count`(`:90-96`)、`approval_counts`(`:109-116`)三支查詢,都已支援 `since`/`until` 窗口,讀的是 `write_stops`/核可使用這幾張專用表。

以 `_await_in()`(file: `src/rtb/executor/execution.py:609-617`)為例,同一次「總曝險已滿進待核可」會在同一個交易裡同時寫 `await_approval`(進 `lifecycle_events`)跟 `record_stop`(進 `write_stops`,且 `record_stop` 的文件寫明「同一份提案同一種類只記一列,重投時再記一次就略過」,即有去重、`INSERT OR IGNORE`);`lifecycle_events` 那一側沒有這層去重。`metrics.py` 沒有呼叫 `observability.aggregate_stop_count`/`table_full_deferral_count`/`approval_counts`(它只用到 `observability.utilization`,見 `collect_snapshot`),而是另外對 `lifecycle_events` 重寫一套計數邏輯。

觸發情境:同一段時間窗內,有提案因總曝險已滿進待核可、之後被核可放行。
會出什麼錯:`metrics.py` 的 `awaiting_approval`/`approval_released` 樣本跟既有 `observability.aggregate_stop_count`/`approval_counts` 對同一個窗口算出來的數字,因為兩套算法的去重規則不同,不保證一致——維運看板跟稽核指令(`aggregate_audit`)可能報出兩個不同的「待核可次數」,沒有人知道該信哪一個。
建議修法:這幾個樣本改呼叫既有的 `aggregate_stop_count`/`table_full_deferral_count`/`approval_counts`,或在 `metrics.py` 文件裡明講為什麼刻意繞過既有的去重語意、改用 `lifecycle_events` 當唯一真相來源,並讓兩邊不要同時存在、各自宣稱正確。

### 3. ToolEndpoint 型別檢查丟 TypeError,既有封閉列舉一律丟 ValueError

severity: minor
blocking: 否

引句:「raise TypeError("端點必須是 ToolEndpoint 的成員")」

`src/rtb/analyzer/task_store.py` 新增的 `record_tool_call` 對「端點必須是封閉列舉成員」的型別檢查丟 `TypeError`。但同一層(執行端)既有的同類檢查——`InboxStore.ack_blocked` 對 `BlockCode`(file: `src/rtb/executor/inbox_store.py:1299-1300`,丟 `ValueError`)、`_log_held` 對 `LifecycleKind`/`Actor`(`:961-966`,丟 `ValueError`)、`release` 對 `LastFailure`(`:1269-1270`,丟 `ValueError`)——全部是同一個寫法:`type(x) is not 列舉類別` 就丟 `ValueError`。

觸發情境:呼叫端傳一個長得像端點值的一般字串(而非 `ToolEndpoint` 成員)給 `record_tool_call`。
會出什麼錯:沿用既有慣例、只 `except ValueError` 來擋「傳錯型別」的呼叫端會漏接這裡丟出的 `TypeError`(測試 `test_analyzer_tool_call_endpoints_are_a_closed_list` 已經把這個行為釘死成 `pytest.raises(TypeError)`,是刻意選的,不是失手,但跟既有慣例不一致,只是措辭/例外型別層級的落差,不影響資料正確性)。
建議修法:改成跟既有慣例一致丟 `ValueError`;若堅持用 `TypeError` 區分「型別錯」與「值錯」,在 `task_store.py` 註明這是刻意跟執行端既有慣例不同的選擇。

---

材料以外查證用到的檔案:`src/rtb/executor/attempt_store.py:493-501`、`src/rtb/executor/observability.py:81-125`、`src/rtb/executor/execution.py:609-617,765-771`、`src/rtb/executor/inbox_store.py:120-131,961-966,1261-1281,1294-1306`、`src/rtb/analyzer/task_store.py:120-138,580-586`、`src/rtb/ops/trace.py`、`src/rtb/ops/ruff.toml`、`src/rtb/executor/ruff.toml`、`src/rtb/analyzer/ruff.toml`、`src/rtb/dsp/ruff.toml`、`src/rtb/domain/ruff.toml`。
