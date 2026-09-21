severity: minor

# r2 架構對齊席(narch)報告

對照基準:`src/rtb/dsp/` 既有做法、Phase0架構「程式結構原則」。只判差異,風格偏好不列。沒有引入第二種做法或跨層直呼,無 major。以下五條都是記錄與一致性的小缺口。

## 逐項結論

- server 路徑解析:`_route` 與 `_get_metrics` 各自呼叫 `urlsplit(self.path)`,全檔沒有別的手切 `split("?")`(已 grep)。同一種做法,已一致。
- `.lumos/lint.json` 跑 `ruff check src tests`:由 ruff 逐目錄找最近的設定檔,`src/rtb/domain/ruff.toml` 會生效(已跑 `ruff check src tests` 通過)。

## Findings

### F1 兩種錯誤慣例只寫在程式碼 docstring,沒進圖譜與計劃

severity: minor
blocking: 否 慣例本身合理(結果型別 vs 型別化例外),缺的是記錄,不是做法衝突。

- 位置:`src/rtb/domain/metrics.py` 模組 docstring;對照 `src/rtb/dsp/errors.py:1` 與 Mock-DSP.md、確定性指標計算.md
- 問題:引句:「這一層用「結果型別」而不是例外:指標的空值是預期中的資料狀態」只出現在程式 docstring。Mock-DSP.md、確定性指標計算.md 的正文與摘要、Phase0架構「程式結構原則」都沒有記這條分層原則(領域層用結果型別、外部邊界用型別化例外),`errors.py` 也沒有反向指出。之後新增 agent 或 client 層的人只能靠讀 metrics.py 才知道要選哪一種。
- 佐證:`grep -rn "結果型別" docs` 只落在指標筆記描述 MetricResult 的句子,沒有「為什麼與 DSP 例外不同」的 WHY 行。
- 建議:在 確定性指標計算.md 加一行 WHY(出處:本次代碼審),Phase0架構「程式結構原則」加一條,或 errors.py docstring 補一句。

### F2 domain/ruff.toml 的分層邊界可運作,但擴充到第二層要複製整份禁用清單,且根設定留有已失效的說明

severity: minor
blocking: 否 現在只有一層,不會出錯;是擴充性與說明準確度問題。

- 位置:`pyproject.toml:9` 與 `src/rtb/domain/ruff.toml`
- 問題一:根 `select` 的註解仍寫「禁用匯入」且選了 `TID`,但根設定已沒有 banned-api,對根來說是空轉;真正的清單只在 domain/ruff.toml。domain 再 `extend-select = ["TID251"]` 是重複選取(TID 已含 TID251)。
- 問題二:實測在 `src/rtb/agent/x.py` 放 `import sqlite3`,只被「未使用匯入」抓到、沒有 TID251(符合預期,不受影響)。但若之後 agent 層也要禁止例如 `sqlite3`,只能再放一份 `ruff.toml` 並複製整張清單(banned-api 在子設定裡是覆寫,不是與別處合併),而且 `extend = "../../../pyproject.toml"` 的相對深度要每層自己數。目前沒有共用清單的機制。
- 建議:根註解改成「禁用匯入的清單在各層自己的 ruff.toml」;第二層出現時再決定是複製還是抽共用。可在確定性指標計算.md 的 RULE 旁加一行 REVISIT,綁「新增第二個受限層時」。
- 補充:Phase0架構第 233 行仍寫成「領域層的禁用匯入清單」,沒提「每個目錄自己一份 ruff.toml」的機制;可補一句。

### F3 store.py 新增項目與既有風格有三處小不一致

severity: minor
blocking: 否 沒有重複造輪:重用了 `_is_plain_int` 與 `SQLITE_INTEGER_MAX`,並沒有另起驗證框架。

- 位置:`src/rtb/dsp/store.py` 的 `COUNT_FIELDS`、`_is_storable_number`、`seed_metrics`
- 問題一:引句:「COUNT_FIELDS = ("impressions", "clicks", "conversions")」放在函式之間,而同類常數 `METRIC_FIELDS`、`METRIC_WINDOWS`、`SQLITE_INTEGER_MAX` 集中在檔案上方(`store.py:43-45`)。而且它是 `METRIC_FIELDS` 的子集,新增欄位時要同步兩處,沒有機制守。
- 問題二:`_is_storable_number(value, allow_float)` 用布林旗標參數,Phase0架構「命名說明意圖」條寫的是不叫 flag;同檔既有做法是每種規則一個具名檢查。
- 問題三:窗口檢查引句:「window 必須是 1h、1d 或 7d 其中之一」在 `seed_metrics` 與 `get_metrics` 各寫一次(同字串同條件),沒抽成共用檢查。
- 建議:常數移到上方並註明與 `METRIC_FIELDS` 的關係;窗口檢查抽成一個小函式。

### F4 metrics.py 對同一個原因有兩種寫法

severity: minor
blocking: 否 是相容別名,語意相同,但形成兩種引用方式。

- 位置:`src/rtb/domain/metrics.py`
- 問題:引句:「NO_DENOMINATOR = Reason.NO_DENOMINATOR」保留模組層別名,測試同時用 `INVALID_DATA` 與 `Reason.NO_DENOMINATOR` 兩種寫法(`tests/domain/test_metrics.py`)。專案還沒有外部呼叫者,別名沒有相容性理由。確定性指標計算.md 也沒提 `Reason` 型別。
- 建議:擇一(建議只留 `Reason.X`),並在筆記提到 `Reason`。

### F5 圖譜筆記有兩處與程式現況不完全對應

severity: minor
blocking: 否 都是敘述位置或缺漏,不影響行為的真實性。

- 位置:Mock-DSP.md、確定性指標計算.md
- 問題一:引句:「測試用的 `seed_metrics` 現在會拒絕字串、bytes、布林、NaN、無限大、超出 SQLite 範圍的值」放在「已知缺口」清單,但這是已完成的行為而不是缺口,且漏了「計數欄位拒絕小數(1.5)」這條(`_checked_metric` 對 `COUNT_FIELDS` 不收 float,測試有覆蓋)。
- 問題二:確定性指標計算.md 寫「四個指標函式加 `MetricResult` 結果型別」為唯一內容,但 `Reason` 列舉是新增的公開型別;`src/rtb/domain/ruff.toml` 也不在任何筆記 `about_code`(是設定檔,非程式檔,不違反「每支程式檔有家」,僅提示)。
- 其他已讀:確定性指標計算.md 的 RULE(ruff.toml 只在該目錄生效)、已知缺口(`__import__`、`noqa`)、PITFALL 各條與程式一致,無 finding。

## 已讀,無 finding

- `src/rtb/dsp/server.py`(路徑解析)
- `tests/dsp/test_store.py`、`tests/domain/test_metrics.py`(架構面)

總結:最嚴重等級為 minor,blocking 條數為 0。
