severity: major

### 1. 判斷點驗證與 DSP 白名單不一致，合法輸入會跳過候選
severity: major
blocking: 是

引句:「return is_plain_number(value) and abs(value) <= MAX_INT」

`WorthInput` 對六個數值欄位套用同一條規則，但 DSP 白名單是逐欄不同：曝光、點擊、轉換只接受整數；花費、營收接受任意有限數，沒有 `MAX_INT` 上限；預算則只接受 0 到 `MAX_INT` 的整數。見 `src/rtb/analyzer/dsp_client.py:50`、`src/rtb/analyzer/dsp_client.py:54`、`src/rtb/analyzer/dsp_client.py:58`、`src/rtb/analyzer/dsp_client.py:67`。

觸發情境：DSP 合法回傳 `status="active", budget=100, spend=1.0, impressions=500, clicks=12, conversions=1, revenue=1e20`，該格已驗證，候選回 `NOT_WORTH`。DSP 白名單接受 `revenue=1e20`，但 `WorthInput` 因其絕對值大於 `MAX_INT` 拒絕；`_judge()` 隨即把它當成「歸不了格」走舊規則，候選完全不會被呼叫，最後反而產生加預算提案。這破壞「DSP 白名單與判斷點同一套判準」及 [S701]。反方向也有問題：`impressions=1.5` 會被 `WorthInput` 接受，卻不可能通過 DSP 白名單，離線評估可能驗證不存在於正式資料路徑的輸入。

625 筆固定資料沒有覆蓋這個差異：計數沒有合法範圍內的小數，花費及營收也沒有大於 `MAX_INT`、但仍是有限浮點數的值；目前 [S715] 測試只驗雙方都拒絕的案例，沒有驗收完整判準相等。見 `tests/analyzer/policy_before_samples.py:31`、`tests/analyzer/policy_before_samples.py:32`、`tests/analyzer/test_worth_check.py:174`。

建議把各欄驗證抽成與 DSP 用戶端共用的欄位級判斷：預算用非負整數上限、三個計數用整數或缺值、花費與營收用有限數或缺值；再以同一組邊界值逐欄比對 DSP 白名單與 `WorthInput` 的接受結果，並加入上述合法大額營收會實際呼叫候選的回歸測試。

### 2. 「已驗證清單」可由任何呼叫者自行偽造
severity: major
blocking: 是

引句:「已驗證、允許呼叫候選的評分格。只有採用函式產生得出來(Phase 10 增量 2);正式路徑是空的。」

`ValidatedCells` 是公開且沒有任何來源驗證的 dataclass；任何呼叫者都能直接執行 `ValidatedCells(frozenset(WorthCell))`，再交給 `route()` 或 `explain()`。測試本身也正是直接建構全部格與暫停格，而不是透過採用決定取得。見 `src/rtb/analyzer/policy.py:72`、`src/rtb/analyzer/policy.py:139`、`tests/analyzer/test_worth_check.py:44`、`tests/analyzer/test_worth_check.py:67`。

觸發情境：沒有正式環境抽樣、人工標註或候選實測的呼叫者自行建構包含任一格的 `ValidatedCells`，傳入任意候選；路由只檢查集合成員，便會呼叫尚未驗證的候選。候選若回合法但錯誤的 `WORTH`，結果不會退回現行規則。這直接破壞「只有採用函式產生得出已驗證清單」以及只有已驗證格才能交給候選的安全合約。

建議將已驗證清單做成不可由一般呼叫端直接建構的能力物件，由採用函式持有私有建構權；評估用格另用獨立型別與入口。增量 1 尚未有採用函式時，正式路徑只能取得模組提供的空清單，測試則應透過私有測試工廠或之後的採用函式建立非空清單。