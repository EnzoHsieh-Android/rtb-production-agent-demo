severity: minor

# r1 sarch 架構對齊席報告

結論:沒有引入第二種做法或跨層直呼,沒有 major。下列 5 條都是 minor。

## 對齊面逐項結果(無 finding 的部分)

- TypeGuard 兩份(領域層 `_checks.py`、DSP `store.py`):符合。`任務流程領域模型.md:25` 已有 RULE 記錄「DSP 刻意不依賴領域層,兩邊不同步是有意的」,含 since 與 retire。`store.py` 的 `_is_plain_int` 沒有 import 領域層。已讀,無 finding。
- `Operation.expected_version: object`:與領域層不構成第二種慣例。DSP 原本就用「例外加 `_validate`」,領域層用「結果型別」,metrics.py 的模組說明已寫明兩層為何不同。`object` 加 TypeGuard 收窄是兩層共用的做法。
- noqa 三處(ARG002 四個、S603 一個、B015 舊有):都有理由、範圍只在該行。ARG002 因為是覆寫基底類別簽章、參數名不能改成底線,所以是最小做法。已讀,無 finding。
- mypy 設定:`strict = true`、`files = ["src", "tools"]`、`mypy_path = "src"` 一致,測試不檢查已在已知缺口如實寫出。已讀,無 finding。
- 圖譜筆記的測試名稱:`靜態檢查閘.md` 引用的三個測試名稱在 patch 中都存在(mypy 壞掉大聲失敗、CI 執行每個分析器、CI 每次 push 跑並跑測試)。REVISIT 獨立成行,日期 2026-09-25(距今三天)合理。
- `tools/` 放置與命名:`tools/mypy_sarif.py` 有 Systems 節點當家(about_code 列了它),測試放 `tests/tools/`,命名與 `src/rtb` 的 snake_case 一致。已讀,無 finding。

## Findings

### 1. tests 的 per-file-ignores 有兩條沒有任何違規在用,ARG 整族放行過寬
severity: minor
blocking: 否 依 minor 判準:只是設定範圍寬鬆,沒有引入第二種做法。
- 位置:pyproject.toml 的 `[tool.ruff.lint.per-file-ignores]`。
- 引句:「"tests/**" = ["S101", "S603", "S310", "S311", "S110", "ARG", "SIM117"]」
- 問題:我在 /private/tmp/sarch/r 的複本上逐條拿掉再跑 `ruff check tests`。S110 與 SIM117 拿掉後都是零違規(patch 本身已把 try/except/pass 全改成 `contextlib.suppress`),等於預先放行了未來的吞例外與可合併的巢狀 with。ARG 整族放行,實際只有 4 個 ARG002 在用。其餘各條對應的違規數:S101 270 個、S603 5 個、S310 2 個、S311 1 個。
- 佐證:重現指令為複本上逐條移除後 `ruff check tests --statistics`,S110、SIM117 輸出為空。
- 建議:移除 S110、SIM117;ARG 改成只放行實際用到的 `ARG002`;S110 的放行會抵消 CLAUDE.md 鐵則「承認限制要有回頭條件」的精神。

### 2. lumos 推送前閘的 ruff 範圍不含 tools,與 CI 的 `ruff check .` 範圍不一致
severity: minor
blocking: 否 依 minor 判準:範圍縫隙,CI 仍會補到。
- 位置:`.lumos/lint.json`。
- 引句:「.venv/bin/ruff check --output-format sarif --output-file {LINT_SARIF_OUT} src tests」
- 問題:mypy 的 `files` 與 CI 的 ruff 都涵蓋 `tools`,lumos 的 ruff 只掃 `src tests`。新增的 `tools/mypy_sarif.py` 本身就有一個 noqa S603,但 lumos 的新增告警閘看不到 tools 裡的新 ruff 告警,只有 CI 才抓得到。`靜態檢查閘.md` 的「三層守衛」說法因此不完全成立,已知缺口也沒列。
- 佐證:`tests/test_static_wiring.py` 的接線測試只檢查「工具名出現在 lint 宣告」,不檢查掃描範圍,所以不會翻紅。
- 建議:lint.json 的 ruff 加上 `tools`,或在已知缺口補一行並附 REVISIT。

### 3. 圖譜筆記已知缺口不完整,且部分缺口沒有回頭條件
severity: minor
blocking: 否 依 minor 判準:筆記完整度問題,不影響程式行為。
- 位置:`docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md`「已知缺口」。
- 引句:「mypy 只嚴格檢查 `src` 與 `tools`,測試程式沒有檢查型別。」
- 問題:CLAUDE.md 鐵則 4 要求承認限制附回頭條件。四條缺口只有第一條(CI 未跑過)有 REVISIT,「CI 沒有跑 lumos doctor」「測試不檢查型別」「槽位屬 lumos」三條沒有日期或事件入口。此外有遺漏的限制:
  1. 上一條 finding 的 lumos ruff 不含 tools。
  2. mypy 與 ruff 在同一次推送被跑三遍(pytest 內的 `test_static_checks.py`、CI 的獨立步驟、lumos 的 lint 宣告),沒有說明是否有意。
  3. CI 同時開 `push:` 與 `pull_request:`,同一分支有 PR 時會雙跑。
  4. `tests/test_static_checks.py` 的模組說明已過時,見 finding 4。
- 佐證:`任務流程領域模型.md` 新增段落寫「343 條測試仍全過」,現在實際收集到 358 條(`.venv/bin/python -m pytest -q --collect-only`),數字沒有標日期,會隨測試增加變成謊。
- 建議:各缺口補 `REVISIT:` 獨立行;新增上述遺漏項;測試數字去掉或標明當時的數量。

### 4. tests/test_static_checks.py 的說明與現況矛盾
severity: minor
blocking: 否 依 minor 判準:過時註解。
- 位置:`tests/test_static_checks.py` 模組說明。
- 引句:「這兩個檢查沒有接進 lumos 的 linter 宣告(mypy 沒有現成的 SARIF 轉換器),所以用測試來守:」
- 問題:同一個 patch 已把 mypy 用 `tools/mypy_sarif.py` 接進 `.lumos/lint.json`,ruff 也早就在裡面。這句話對「這兩個」都不成立,會讓下一個人誤判為什麼有兩套守衛。同時 `靜態檢查閘.md` 的 TEST 行描述它是「讓 mypy 嚴格模式與 ruff 成為測試的一部分」,與程式內註解說法不同。
- 佐證:`.lumos/lint.json:4` 已有 `tools/mypy_sarif.py`。
- 建議:改寫說明,講清楚「為什麼在 lumos 與 CI 之外還要一份測試(pytest 一跑就檢查、拿不到 lumos 的環境時也守得住)」。

### 5. Operation 內的「未驗證」標註只用在 expected_version,params 用 Any 反而放行了未驗證值
severity: minor
blocking: 否 依 minor 判準:標註不一致,行為經 `_validate` 守住。
- 位置:`src/rtb/dsp/store.py` 的 `Operation`。
- 引句:「params: dict[str, Any]  # 來自不可信的請求:_validate 會檢查內容」
- 問題:同一個 dataclass 內,`expected_version` 標 `object`(mypy 會強迫先收窄),`params` 卻標 `dict[str, Any]`(mypy 完全不查)。`_next_state` 直接把 `op.params["new_budget"]` 當 int 放進 `Campaign`,型別檢查對這條不可信資料是失明的。註解說「未驗證」,型別卻是最寬鬆的 `Any`。領域層 `proposal.py` 同樣用 `dict[str, Any]`,所以是專案共通的取捨,不是第二種慣例,但 `Any` 與 `object` 混用讓「標成未驗證」的意圖不一致。
- 佐證:`src/rtb/dsp/store.py` 的 `_next_state` 讀 `op.params["new_budget"]`,mypy strict 通過只因為 `Any`。
- 建議:或把 params 也標成 `dict[str, object]` 並在 `_next_state` 收窄,或在筆記已知缺口註明「params 內容是 Any,由 `_validate` 用執行期檢查守,型別檢查守不到」並附回頭條件。

## 另記(不列 finding)

- `tests/tools/test_mypy_sarif.py` 用 `importlib.util.spec_from_file_location` 載入 `tools/mypy_sarif.py`,與其他測試靠 `pythonpath = ["src"]` 匯入不同;因為 `tools/` 不是套件也不在 pythonpath,這是合理的最小做法,不算第二種慣例。
- `server.py` 的 `log_message` 行出現兩段重複註解「# 不印每個請求  # 不把每個請求印到終端」,屬風格瑕疵,不列。

總結:最嚴重等級為 minor,blocking 條數為 0。
