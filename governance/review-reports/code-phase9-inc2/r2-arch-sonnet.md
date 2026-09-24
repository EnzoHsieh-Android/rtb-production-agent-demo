severity: major
以下是完整報告全文(已依格式規則撰寫,可直接存檔)。

---

severity: major

### 1. 時區必帶檢查在這批修正裡另外寫了兩份,沒有共用 attempt_store 既有的 `_require_aware`

severity: major
blocking: 是

引句:「沒帶時區就拒絕(比照嘗試紀錄的同名函式)」

引句:「def _require_aware(*moments: datetime) -> None:」

file: `src/rtb/executor/attempt_store.py:378`(既有先例:`def _require_aware(now: datetime) -> None:` / `if not is_aware(now): raise ValueError(...)`)
file: `src/rtb/executor/attempt_store.py:383`(既有先例:`_iso` 呼叫共用的 `_require_aware`,不是各自重寫判斷式)

觸發情境:`rtb.ops.metrics` 的 `_check_window`/`collect_snapshot` 收到未帶時區的 `datetime`,以及 `rtb.analyzer.task_store._iso` 在視窗查詢路徑收到未帶時區的 `datetime`。

會出什麼錯的行為:這批修正沒有把 `attempt_store._require_aware` 這個既有先例抽成共用函式讓三處匯入,而是各自重寫了三份「沒帶時區就 raise」的邏輯——`attempt_store._require_aware(now)`(單一位置參數)、`task_store._iso` 內直接 inline 一段 `if not is_aware(moment): raise ValueError("時間必須帶時區")`(連函式都沒抽,docstring 自己承認是「比照」而非「呼叫」同名函式)、`metrics._require_aware(*moments)`(改成可變參數、錯誤訊息也換了一版,附上範例字串)。三份邏輯目前語意接近,但已經是三份獨立維護的原始碼而非一份共用定義,正好違反 `rtb/domain/_checks.py` 檔頭自己寫的規矩:「同一個規則只有一份定義,免得各模組改了一處漏另一處」。之後只要有人改動其中一份的判準或錯誤訊息(例如把 metrics 那份「有帶沒帶混用」的邊界情況修正,或把 attempt_store 那份訊息換成雙語),其餘兩份不會跟著變,同一條「時間必須帶時區」的系統级不變量會在不同模組跑出不同行為/錯誤訊息。

建議修法:把 `attempt_store._require_aware` 提升到 `rtb.domain._checks`(或其他所有相關模組都能匯入的共用位置),讓 `task_store._iso`、`metrics._require_aware`、`metrics._aware_time` 都改成呼叫同一份,不要各自重寫判斷式與例外訊息;metrics 需要一次驗多個時間的能力,可以讓共用函式本身支援可變參數,而不是另開一個同名但簽名不同的私有函式。

### 2. metrics.py 新增結束代碼 7 與自訂 argparse 錯誤處理,但沒有同步套用到既有先例 trace.py,兩支 ops CLI 的結束代碼表變成兩套不一致

severity: major
blocking: 是

引句:「EXIT_BAD_ARGUMENTS = 7  # 參數錯(缺參數、時間沒帶時區);argparse 預設的 2 跟資料庫檔不存在撞號」

引句:「class _Parser(argparse.ArgumentParser):」

file: `src/rtb/ops/trace.py:43-45`(既有結束代碼表只有 `EXIT_OK=0`、`EXIT_NO_DATABASE=2`、`EXIT_NOT_UPGRADED=3`,沒有「參數錯」專屬代碼)
file: `src/rtb/ops/trace.py:467`(既有先例:`_parse` 直接用 `argparse.ArgumentParser`,沒有覆寫 `error()`)

觸發情境:操作人下 `python -m rtb.ops.trace` 漏打必填參數(例如漏 `--task-id`),或下 `python -m rtb.ops.metrics` 漏打必填參數(例如漏 `--executor-db`,或 `--since`/`--until` 少帶時區)。

會出什麼錯的行為:trace.py 沒有被這批修正碰,仍是預設 `argparse.ArgumentParser`——漏參數時走 argparse 內建的 `error()`,結束代碼固定是 `2`,跟 trace.py 自己定義的 `EXIT_NO_DATABASE = 2`(資料庫檔不存在)撞號;呼叫端(監控腳本、runbook)看到 `2` 分不清是「忘記傳參數」還是「資料庫檔不存在」。這批修正在 metrics.py 新增 `_Parser` 子類與 `EXIT_BAD_ARGUMENTS = 7`,正是為了解決這個撞號(patch 註解自己寫明「argparse 預設的 2 跟資料庫檔不存在撞號」),卻只套用在 metrics.py,沒有回頭修 trace.py 這個被架構對齊材料點名要對照的先例。結果同一個 `rtb/ops/` 目錄下兩支結構幾乎一樣(唯讀、argparse、`_parse`/`run`/`main`、自訂 `EXIT_*` 常數表)的 CLI 工具,一支「參數錯」與「資料庫不存在」仍共用結束代碼 2(未修的舊行為),另一支已經拆成 2 與 7 兩個代碼——結束代碼表變成兩套不一致的合約,同一批依賴結束代碼做分流的自動化(監控、runbook)沒辦法用同一套邏輯判讀這兩支工具。

建議修法:把「參數錯與資料庫不存在分開」這個做法(`_Parser`/`EXIT_BAD_ARGUMENTS`)同步套用到 `trace.py`,或者把這段 argparse 錯誤代碼處理抽成 `rtb/ops/` 底下的共用小工具讓兩支 CLI 一起用,避免兩套結束代碼表各自漂移;若這次只打算先動 metrics.py,至少要在兩份程式的結束代碼表旁互相加註「trace.py 尚未套用此修正」,提醒下一個維護者兩表目前語意不同。
