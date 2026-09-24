severity: minor

## Q1:分層與依賴方向

現況:專案目前有**兩種**既有機制在擋跨層匯入——(a) 各層目錄自己的 `ruff.toml` 用 `[lint.flake8-tidy-imports.banned-api]` 宣告簡單的「整個模組不准匯入」(`src/rtb/analyzer/ruff.toml:5-9`、`src/rtb/domain/ruff.toml:6-29`、`src/rtb/dsp/ruff.toml:4-7`、`src/rtb/executor/ruff.toml:4-7`、`src/rtb/eval/ruff.toml:8-12`、`src/rtb/ops/ruff.toml:8-13`,同一條 `rtb.eval`/`rtb.ops`/`importlib` 禁令在五份檔案裡各自重複一次);(b) 白名單較細、需要區分讀寫的情況才用自寫的語法樹掃描測試(`tests/ops/test_ops_boundaries.py:1-6` 的說明:「匯入禁令只能整個模組一起禁,讀寫函式又在同一個模組,所以另外掃…語法樹」)。「rtb 不准匯入 tools」屬於單純的整模組禁令,跟 (a) 是同一類問題,但計劃選的是 (b):`[S808]` 只掛一支自寫測試 `test_the_verifier_and_the_product_do_not_import_each_other`,沒有在任一層 `ruff.toml`(或新開一份 `tools/ruff.toml`)補 `banned-api`。專案裡目前沒有 `tools/ruff.toml`,`tools/` 也不在任何 `[tool.mypy].files`/`ruff` 的分層禁令範圍內,所以這條路要嘛新開 `tools/ruff.toml`(banned-api `rtb`)並在六份既有 `src/rtb/*/ruff.toml` 各補一行 `tools`(跟現有重複五次的作法一致),要嘛就是計劃選的自寫測試——兩條路都存在於專案裡,計劃沒交代為什麼跳過既有慣例更常用的 (a),判不準,標 ⚠。

另外,`tools/verify_claims.py`(`/tmp/rtb-phase11-r1.md:48`)本身不透過 `ruff.toml` 的 banned-api 擋自己匯入 rtb(因為 tools/ 目前沒有 ruff.toml),只靠同一支 `[S808]` 測試,單點失守就兩個方向都沒人守——這點計劃本身有意識到「機械驗證的天花板」但講的是雜湊重貼,沒提到這裡。

## Q2:命名與錯誤處理

結束代碼 0/1/2 的形狀(`/tmp/rtb-phase11-r1.md:50`)與 `tools/mypy_sarif.py` 的 0/(1 不用)/2(`tools/mypy_sarif.py:192-198`)同一個量級,語言也一致(全中文訊息、以「擋下」「無法決定」措辭,對照 `tools/mypy_sarif.py:176,180,193`)。差異:`tools/mypy_sarif.py` 从没有单独定义「參數錯」的結束代碼,`argparse` 走預設(結束碼 2),剛好和它自己「工具本身失敗」的 2 撞在一起(算是既有的、被接受的不精確);計劃裡的清單也只定義了 0/1/2 三種語意,同樣沒提參數錯誤(例如 `claims/` 路徑不存在)要走哪個代碼——如果比照 `tools/mypy_sarif.py` 用 argparse 預設,一樣會撞上「2 是清單讀不懂」。這跟既有工具的既有瑕疵一致,不是新引入的問題,列為 minor:命名與錯誤處理形狀吻合,但「參數錯」這個分支沒交代,可能複製既有的撞碼問題。`src/rtb/ops/cli.py:15`(`EXIT_BAD_ARGUMENTS = 7`)是 `rtb.ops` 套件自己的慣例,不屬於 `tools/` 層,計劃不採用它並不算不一致。

## Q3:第二種做法

- 讀取設定:清單是 JSON、不是 TOML,計劃沒有引入另一種 TOML 讀法;跟既有 `tomllib` 讀 `pyproject.toml` 的手法(`tools/mypy_sarif.py:86`)不衝突,claim 檔本來就不該是設定檔。
- 跑子行程:計劃寫「子行程、sys.executable -m pytest…」(`/tmp/rtb-phase11-r1.md:56`),跟 `tools/mypy_sarif.py:155-160`(`subprocess.run([sys.executable, "-m", …], cwd=ROOT, timeout=…)`)與 `tests/test_static_checks.py:16-19` 的 `run_tool` 是同一種手法,沒有引入第二套執行子行程的方式。
- AST 工具:計劃多處要「用語法樹讀出字面常數」「用語法樹確認…有引用」(`/tmp/rtb-phase11-r1.md:43-44,55`)。專案已有用 `ast` 模組做原始碼掃描的先例(`tests/ops/test_ops_boundaries.py:9,33-40`、`tests/executor/test_executor_boundaries.py`、`tests/analyzer/test_boundaries.py`),所以這不是新引入的技術路線,是延用既有手法。沒有發現「專案已有同功能掃描、卻自創一套」的情形——列舉用的登錄表 `CAMPAIGN_WRITE_ACTIONS` 經查證確實是 `src/rtb/dsp/server.py:110` 的字面 tuple,不是動態組出來的,計劃描述屬實。

本問沒有 major 發現。

## Q4:落點

`tools/mypy_sarif.py` 現在的家是 `docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md`(`about_code: tools/mypy_sarif.py`),責任描述明寫「不負責 ruff 本身、CI 設定內容,也不決定哪些工具該接」——範圍刻意收窄到「mypy → SARIF 轉換」這一件事。`tools/verify_claims.py` 是完全不同的職責(五條宣稱的機械驗證、JUnit 解析、語法樹核對列舉/故障注入、CI 平行工作),不屬於「靜態檢查閘」現有責任範圍內的任何一句,比照專案「一支工具一篇家、責任互斥」的既有切法(每份 Systems 筆記只收自己 `about_code` 列的檔),`lands_in: Systems/宣稱驗證器` 開新篇是對齊既有慣例的做法,不需要併入 `靜態檢查閘.md`。

## 不對齊清單

### rtb 不准匯入 tools 未指定用既有的 ruff banned-api 機制

severity: minor
blocking: 否(不影響能不能實作,但若不補一句「為什麼跳過既有的逐層 banned-api、改用自寫測試」,下一個實作的人會兩種都試,或漏掉 tools 那一側的守衛)
引句:「rtb 的任何模組也不准匯入 tools」
file: `/tmp/rtb-phase11-r1.md:60`
對照:`src/rtb/analyzer/ruff.toml:5`、`src/rtb/eval/ruff.toml:8`(既有的整模組匯入禁令一律走 `banned-api`,六份檔案重複宣告同一條規則)

### 新的 CI 平行工作沒有比照既有的「防止閘變不擋」合約

severity: minor
blocking: 否(功能面不受影響,但 CI 步驟可能被 `continue-on-error`、`|| true`、`if:` 等悄悄關掉而沒有測試守著,跟既有動機衝突)
引句:「CI 另開一個跟 checks 平行的工作:checkout、裝開發期工具、跑同一條指令」
file: `/tmp/rtb-phase11-r1.md:74`
對照:`tests/test_static_wiring.py:76-99`(`ci_problems` 專門抓「看起來有跑、其實不擋」的寫法)與 `docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md` 裡「接線檢查若只看 CI 檔案裡有沒有工具名稱的字…」那條 PITFALL——計劃的合約候選 S800–S809 沒有一條比照這個既有 PITFALL 去鎖新 CI 工作的精確指令字串。

### 參數錯誤的結束代碼未定義,可能複製 mypy_sarif.py 既有的代碼撞號

severity: minor
blocking: 否(不是本設計新引入的缺陷,mypy_sarif.py 本身就有同樣的既有問題;只是計劃沒有藉這次機會避開)
引句:「結束代碼 0 是全通過、1 是有擋下、2 是清單讀不懂」
file: `/tmp/rtb-phase11-r1.md:50`
對照:`tools/mypy_sarif.py:192-198`(工具失敗與 argparse 預設參數錯誤同樣共用結束碼 2)

不對齊共 3 條,其中 major 0 條。
