severity: major
已完成材料閱讀與程式碼比對(patch 全文、`_checks.py`、`evidence.py`、`proposal.py`、`dsp_client.py`、`flow.py`、`httpclient.py`、既有樣本模組、Systems 筆記)。以下為完整報告全文。

---

severity: major

### 1. `worth.py` 的建構驗證另立一套數字判準,沒有沿用領域層既有的 `math.isfinite` 做法
severity: major
blocking: 是
引句:「這一道比較就把它們擋掉(不另寫有限數檢查:變異檢查證實那道拿掉結果不變)。」
file: `src/rtb/domain/evidence.py:95`
file: `src/rtb/domain/evidence.py:96`
file: `src/rtb/domain/evidence.py:127`
file: `src/rtb/domain/proposal.py:194`
file: `src/rtb/analyzer/dsp_client.py:58`

觸發情境:任何呼叫端用一個極大浮點數(例如超出 float 表示範圍、需要 `math.isfinite` 內部做 float 轉換才會觸發 `OverflowError` 的值)去建構 `WorthInput`。

會出什麼錯的行為:`worth.py` 的 `_is_amount` 只用 `is_plain_number(value) and abs(value) <= MAX_INT` 擋 NaN/Infinity,刻意不呼叫 `math.isfinite()`,理由是註解裡自陳的「變異檢查證實拿掉結果不變」。但這條判準是這個增量自己另外導出的一套數字驗證邏輯,跟專案領域層既有、且是**代碼審查追認過**的做法不同:`evidence.py` 的 `_is_payload_value`(:95)、`_is_positive_finite`(:127-131)與 `proposal.py` 的 `_leaf_bytes`(:194)全部用 `math.isfinite()` 顯式判斷,`evidence.py:96` 的註解更明講「2026-09-22 代碼審發現這裡漏掉,跟數值驗證的既有慣例不一致」——也就是說,「只用比較擋 NaN/Inf、不呼叫 `math.isfinite`」正是這個專案已經在代碼審查裡認定過是錯誤慣例的寫法,`_is_amount` 又把它重新引入了一次。DSP 用戶端白名單(`dsp_client.py:58` `_is_finite_or_none`)同樣是 `is_plain_number(value) and math.isfinite(value)`,增量 1 的計劃筆記自稱「跟分析端 DSP 用戶端白名單同一套判準」,但實作沒有真的沿用同一支函式或同一種寫法,是領域層裡第二套獨立維護的數字判準,以後兩邊各自改容易再次失準。

建議修法:`_is_amount` 改成呼叫 `math.isfinite(value)`(並比照 `evidence.py`/`proposal.py` 用 `try/except OverflowError` 包住,或直接抽出共用檢查放進 `_checks.py` 給 `evidence.py`、`dsp_client.py`、`worth.py` 三處共用),不要靠「跟上限比較、NaN/Inf 比較恆假」這個副作用來擋值。

### 2. `explain()` 給 `timeout_seconds` 設了預設值,牴觸共用 HTTP 用戶端「逾時沒有預設值」的既有規則
severity: major
blocking: 是
引句:「timeout_seconds: float = 0.0,」
file: `src/rtb/httpclient.py:4`
file: `src/rtb/analyzer/dsp_client.py:131`
file: `src/rtb/analyzer/inbox_client.py:57`

觸發情境:Phase 10 增量 2(或任何後續評估入口)呼叫 `policy.explain(task, evidence, now, candidate=真的候選)`,但忘了明確帶 `timeout_seconds`。

會出什麼錯的行為:`explain()` 簽名把 `timeout_seconds: float = 0.0` 設成有預設值(`src/rtb/analyzer/policy.py` 新增的 `explain` 函式),一旦候選有給、又忘了帶逾時,`route()` 會用 `timeout_seconds=0.0` 直接呼叫候選,候選內部經共用 HTTP 用戶端 `request_json` 送出時等於「不等」,幾乎每次都會立刻逾時失敗(或視底層實作直接拋非預期例外),而不是明確地在呼叫這一刻就報錯攔下來。這正是 `src/rtb/httpclient.py:4` 明講的既有規則要防的事:「逾時沒有預設值——呼叫端一定要自己決定要等多久,不會有人忘記設定而讓請求永遠卡住」;專案裡所有帶 `timeout_seconds` 的既有函式(`dsp_client.py:131` 的 `fetch()`、`inbox_client.py:57` 的 `make_client()`、`executor/dsp_client.py:82` 等)全部沒有預設值,呼叫端一定要自己填。`explain()` 是這個專案裡第一個給逾時參數塞預設值的地方,是第二種做法;而且新增的測試(`tests/analyzer/test_worth_check.py`)裡每一筆真的帶候選的呼叫都手動填了 `timeout_seconds=1.0`,完全沒有測過這個預設值路徑,問題不會被現有測試攔下。

建議修法:`explain()` 的 `timeout_seconds` 拿掉預設值,改成必填參數(只有正式路徑 `decide()` 內部用「沒有候選」呼叫時才不需要它,可以讓 `decide()` 呼叫 `explain()` 時明確傳一個不會被用到的值,或把「有候選才需要逾時」這件事在型別上表達出來,而不是給一個看起來合法、實際會讓每次候選呼叫都失敗的 0.0)。

---

其餘檢查項(候選協定比照 `flow.py` 既有 `Protocol` 寫法、領域層是否依賴分析端、Systems 筆記的家)都跟既有做法一致,沒有發現偏離:`WorthCandidate` 沿用 `flow.py` 的 `Protocol` + `__call__` 寫法;`src/rtb/domain/worth.py` 只匯入 `rtb.domain._checks`、`rtb.domain.proposal`,沒有依賴分析端或模型用戶端;`worth.py` 併入既有的「Systems/任務流程領域模型」跟 `task_state.py`、`evidence.py`、`proposal.py`、`attempt.py` 同一個「純判斷型別」分組一致。凍結舊規則檔(`tests/analyzer/frozen_policy_e8b26f6.py`)與固定資料模組(`tests/analyzer/policy_before_samples.py`)是這個專案第一次做「凍結舊規則雜湊比對」這種回歸手法,沒有既有的「凍結+雜湊」先例可比對是否一致,但檔名沿用了既有 `_samples.py` 的命名慣例,計劃筆記裡也明講是比照既有樣本模組,設計已經過裁定並留了 `PRIOR-ART`/`RETIRE-IF`/`REVISIT`,未列為架構對齊問題。
