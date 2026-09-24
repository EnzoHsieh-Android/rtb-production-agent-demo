severity: major
我已完成這批改動(c5a34da..HEAD)的架構對齊審查，比對了 metrics.py/trace.py(增量 1、2 先例)、dsp/server.py、dsp/store.py、attempt_store.py、guardrails.py 與各層 ruff.toml，並實跑了 ruff 與測試(875 個全過、ruff 全過，含 banned-api 匯入邊界檢查)。以下是完整報告全文：

---

severity: major

### 1. side_effects.py 的副作用核對用「單輪順序讀取」取代整個維運套件既有的「多輪重開快照直到一致」讀法
severity: major
blocking: 是

引句:「讀取順序:先讀 DSP 的窗,再開執行端的唯讀快照」
引句:「writes = read_dsp_window(dsp_url, since, until, timeout)  # 先讀 DSP」
引句:「跨兩個資料庫:每輪重開快照,兩輪相同才回;三輪都不同回最後一輪」

觸發情境:`sli.unauthorized_side_effects` / `sli.harmful_duplicates`(經 `slo.py` 的燃燒率告警或週期統計呼叫)需要合併 DSP(經 HTTP)與執行端(經 `ReadOnlyInbox`)兩個資料源。`src/rtb/ops/side_effects.py` 的 `unauthorized()`(第 247 行起)與 `duplicates()`(第 270 行起)只做一次「先讀 DSP 窗、再開執行端快照」，沒有重試迴圈，也不在回傳的 `Tally` 上標示這次讀取是否可能被讀取期間的並行寫入影響。

會出什麼錯的行為:這與同一個維運套件裡跨資料源讀取的既有慣例不同——`metrics.py:collect_window`(`src/rtb/ops/metrics.py:735`)、`trace.py:build_trace`(`src/rtb/ops/trace.py:427`)、乃至**同一批修改自己新寫的** `sli.py:end_to_end_handoff`(引句所在處，`src/rtb/ops/sli.py:229`)都是「每輪重開兩邊快照整份重算,兩輪結果相同才回傳,最多三輪,仍不同就回最後一輪並在結果裡標明不穩定」。`side_effects.py` 放棄了這套機制,改成只用一次性的因果順序假設(執行端一定在呼叫 DSP 之前就提交第一列)來保證正確性。這個假設本身在文件裡有推理支持,但它是一套跟既有做法不同、且沒有「不穩定」旗標的讀取一致性策略:下游(`slo.py` 的燒損率、錯誤預算、違規判定)會把這兩條指標的數字當成跟其他四條指標一樣「已重試到穩定」的確定值來用,但實際上它們是單次讀取、遇到讀取當下的並行寫入時無法感知也無法標記,而其他四條與同一批的 `end_to_end_handoff` 都有這層保護。維運人員或告警系統看不出這兩條指標跟另外四條在一致性保證上是不同等級的。

建議修法:讓 `unauthorized()` / `duplicates()` 比照 `end_to_end_handoff` 的既有做法,包成「重開兩邊快照、兩輪結果相同才回、最多三輪」的迴圈,並把是否穩定的旗標一併放進 `Tally`(或另開一個欄位),讓 `slo.py` 的評估器能跟其餘四條指標一樣識別並回報「讀取期間有新提交」的狀況;如果作者認為單向因果順序保證確實足夠、有意不採用重試機制，也應該把這個決定寫成跟其他四條一致的「有 stable 旗標、但恆為 True 並附理由」，而不是悄悄少一層既有的一致性保護。

---

## 其他檢查結果(非另立發現)

- **設定載入**:`slo.py`/`sli.py` 一律用 argparse 顯式旗標(`--executor-db`、`--dsp-url`…),跟 `trace.py`、`metrics.py` 既有 CLI 寫法一致,沒有另一套環境變數或設定檔載入方式。
- **時間窗切法**:`slo.py:period_windows` 把 30 天週期切成不超過 24 小時的子窗再逐段相加,沿用的正是 `metrics.py` 既有「單次查詢窗不得超過 24 小時」的上限(`MAX_WINDOW`),是延伸既有紀律而非另立一套;沒有發現違反該上限的呼叫路徑。
- **分數/比例運算**:`guardrails.increase_allowance` 是把既有的比例上限公式抽成函式重用(`increase_too_large` 與 `execution.py` 記錄核對材料都呼叫它),沒有另立算法;`side_effects.py` 的核對函式只比對「開始一筆當下記下的既有數值」,不重算比例,符合它自己文件寫的「只用寫入當時的資料,不用查詢當下設定」的原則。`slo.py` 的燒損率/錯誤預算用 `Fraction` 是新指標(先前不存在錯誤預算概念),不是既有比率演算法的第二套寫法。
- **HTTP 路由/游標寫法**:`dsp/server.py` 新增的兩條路由沿用既有 `(METHOD, regex, handler_name)` 清單格式與「路徑裡恰一個參數」的慣例;游標分頁(`operation_cursor` + `operations_after`)是專案裡第一個分頁機制,查無既有第二套游標寫法可比對衝突。
- **唯讀交易取得方式(開法本身)**:新函式(`first_rows_for`、`first_rows_for_proposal`、`unknown_rows_between`、`approval_uses_for`)全部走既有的 `Readable`/`_read_conn(tx)` 與 `ReadOnlyInbox.read_transaction()`,`tests/ops/test_ops_boundaries.py` 的白名單也已同步更新,機械化邊界測試通過。
- **跨層直呼**:`ops/` 沒有出現 `import rtb.dsp` 或直接開 DSP 的 SQLite 檔;DSP 一律經 `rtb.httpclient.request_json` 唯讀端點讀,`ruff check`(含各層 `ruff.toml` 的 banned-api 規則)全過,沒有違反「維運套件只經唯讀開法讀執行端/分析端、DSP 只經 HTTP」的架構規則。
- **sli.py 與增量 2 metrics.py 的重疊**:兩者都讀 `lifecycle_events_between` 等既有讀取函式,但語義不同——`metrics.py` 是不帶「好/壞」判斷的可觀測性計數與延遲分布,`sli.py` 是新增「好事件/有效事件」的業務判準(如 `safe_completion` 的 `BUSINESS_BLOCKS`、`queue_wait` 的期限判定),兩者判準集合不互相牴觸(`BUSINESS_BLOCKS` 與 `metrics.py` 的 `STALE_REASONS` 是不同用途的分類,沒有互相覆蓋或衝突定義)。沒有找到「同一件事被兩套演算法各自算一次、結果可能對不上」的情形;`queue_wait` 一類指標確實重新刻了一遍「同修訂配對第一次投遞」的比對邏輯(而非重用 metrics.py 私有的 `_Window` 內部函式),屬於程式碼重複而非兩套互相衝突的架構,未達 major 門檻。
