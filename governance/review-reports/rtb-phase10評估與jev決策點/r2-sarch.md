severity: major
Confirmed no `[build-system]` section at all in pyproject.toml — this project has no packaging config; `src` is added to `pythonpath` only for tests. Now composing the final report.

severity: major

### F1 評估套件反向匯入禁令只承諾白箱測試,漏了專案既有雙層防線的另一半(ruff TID251)
severity: major
blocking: 是
引句:「分析端、執行端、DSP、領域層、維運套件都不准匯入評估套件——規則作者的程式在結構上讀不到評估集」

專案現有的每一道套件邊界(領域層、analyzer/executor/dsp 互不匯入、ops 唯讀套件)都是雙層防線:①在會匯入該套件的**每一個**目錄自己的 `ruff.toml` 裡用 `flake8-tidy-imports.banned-api` 宣告 TID251 禁令(lint 期立即擋、IDE 也看得到),②再用一支直接解析原始碼的 AST 白箱測試把關(不看 noqa,防禁令被跳過)。ops 套件是最新的先例,`src/rtb/ops/ruff.toml` 檔頭本身就寫明「反過來分析端、執行端、DSP 都不准匯入這裡(禁令寫在各自目錄的 ruff.toml)」,而 `src/rtb/analyzer/ruff.toml`、`src/rtb/executor/ruff.toml`、`src/rtb/dsp/ruff.toml`、`src/rtb/domain/ruff.toml` 四份設定裡確實各自都有一條 `"rtb.ops"` 的 banned-api;`tests/ops/test_ops_boundaries.py` 的 `test_the_ops_package_is_read_only_and_imported_by_nobody` 也先用 `subprocess` 呼叫 `ruff check` 驗證這三個目錄的 TID251 真的擋得住,才接著跑 AST 白箱掃描。

第 3 版對 `rtb.eval` 的反向匯入只交代了 [S712] 這一支 `test_nothing_outside_the_eval_package_imports_it`(白箱測試那一層),完全沒提要在 `analyzer`、`executor`、`dsp`、`domain`、`ops` 五個目錄的 `ruff.toml` 裡各加一條 `"rtb.eval"` 的 banned-api——也就是既有雙層防線的第一層(lint 期防線)整個缺席。這不是風格偏好,是引入了跟專案既有邊界機制不一致的「單層」做法:少了 lint 期立即標紅,寫程式的人在編輯器或 CI 的 ruff 步驟看不到警訊,只有整套 pytest 跑到那支測試才會發現匯錯了。round 1 的 sarch-F1 已經指出「新開 eval 套件並禁止反向匯入」,折法只寫了 [S712],並沒有把 ruff.toml 那一半補上,折法沒補全。

file: `src/rtb/ops/ruff.toml:1-3`
file: `src/rtb/analyzer/ruff.toml:1-5`(`"rtb.ops"` 禁令)
file: `src/rtb/domain/ruff.toml:1-27`(`"rtb.ops"` 禁令,無對應 `"rtb.eval"`)
file: `tests/ops/test_ops_boundaries.py:141-149`(先驗 ruff TID251、再驗 AST 白箱)

建議改法:在 `src/rtb/analyzer/ruff.toml`、`src/rtb/executor/ruff.toml`、`src/rtb/dsp/ruff.toml`、`src/rtb/domain/ruff.toml`、`src/rtb/ops/ruff.toml` 各補一條 `"rtb.eval"` 的 banned-api,[S712] 的白箱測試裡仿照 `test_ops_boundaries.py` 先驗這五份設定檔的 TID251 真的擋得住,再做 AST 掃描補漏。

### F2 評估集當成 src 套件目錄裡的資料檔,專案沒有這個先例,設計沒交代打包與路徑讀取怎麼做
severity: major
blocking: 是
引句:「評估集是這個套件目錄裡入版本控制的資料檔(不寫進任何資料庫,不碰分析端與執行端的資料庫隔離)」

`pyproject.toml` 完全沒有 `[build-system]` 或任何 packaging 設定,只有 `[tool.pytest.ini_options]` 的 `pythonpath = ["src"]` 讓測試找得到套件——這個專案目前根本不是一個會被建置成 wheel/sdist 的可安裝套件,`src/rtb/` 底下除了每層一份的 `ruff.toml` 之外沒有任何非 `.py` 檔案(用 `find src -type f ! -name "*.py"` 核對過,只有各層 `ruff.toml` 和 `__pycache__`)。專案既有的「固定測試資料」一律是 tests/ 底下的 Python 模組——`tests/domain/proposal_samples.py`、`tests/adversarial_samples.py`、`tests/capability_samples.py`——用程式碼產生樣本,不是資料檔;連 `[S706]` 自己要釘住的「評估集」在同一份設計裡也還是靠雜湊測試釘住的抽象概念,沒說清楚具體是 JSON/CSV 這類檔案還是 Python 模組。

把資料檔放進 `src/rtb/eval/` 這個會被 import 的套件目錄,踩兩個這個專案從沒踩過的坑:①用什麼機制讀(相對路徑吃 cwd、`importlib.resources` 吃是否真的打包成 package data)專案完全沒有先例可循;②`src/rtb/` 目前的角色是「純程式碼樹」,混進資料檔等於在既有「一層一個純 Python 套件」的骨架裡開了第一個例外,而設計沒有解釋為什麼不放在 `tests/` 底下比照現有 `*_samples.py` 的模式,或另開一個 `src/` 外的 `data/` 目錄。round 1 的 sarch-F3 只確認了「是資料檔、不進資料庫」這個資料庫隔離角度,沒有回答packaging與路徑讀取這個角度,折法沒有覆蓋到這裡。

file: `pyproject.toml:1-45`(無 `[build-system]`,只有 `pythonpath = ["src"]`)
file: `tests/domain/proposal_samples.py`、`tests/adversarial_samples.py`、`tests/capability_samples.py`(既有固定資料的既有做法:Python 模組,非資料檔)

建議改法:比照既有 `*_samples.py` 的模式,把評估集寫成 `tests/eval/` 或 `src/rtb/eval/` 下的一個 Python 模組(常數/tuple 語法產生情境),沿用專案已經在用、已經有先例的做法;如果堅持要用資料檔,設計裡要交代讀取路徑怎麼算(不依賴 cwd)、要不要進 packaging 設定,並在 `lands_in` 的 Systems 家裡寫清楚。

### F3 判斷點輸入型別與四值列舉放在 domain 還是 analyzer,設計全文沒有交代
severity: major
blocking: 是
引句:「判斷點的輸入是一個專用的不可變資料型別,只有決策當下的可信數字欄位」

專案既有的型別放置有清楚的分野:純分類、無副作用的領域概念一律放在 `src/rtb/domain/`——`EvidenceKind`、`TrustClass`(`src/rtb/domain/evidence.py:28,34`)、`ActionType`(`src/rtb/domain/proposal.py:43`)都是這樣;只有跟流程控制、持久化緊綁的型別才放 `analyzer`——`TaskRow` 是資料列(`src/rtb/analyzer/task_store.py:202`),`Decision`/`NoAction`/`ProposalDecision`/`NeedsFreshEvidence` 是 `advance()` 流程自己的控制結果(`src/rtb/analyzer/flow.py:46-63`)。這次新加的「判斷點輸入型別」與「值得加/不值得加/證據不足/不知道」四值列舉,性質是對既有可信證據欄位的一次分類判斷,跟 `EvidenceKind`/`TrustClass` 同類,不是流程控制結果,照既有慣例應該放 `domain`。

但整份設計只講了介面要「比照決策函式的介面用型別協定定義」,完全沒說這個輸入型別與列舉本身要定義在哪一層。這不是小事:`domain/ruff.toml` 明確禁止匯入模型客戶端(`"anthropic"`、`"openai"`)與網路,是專案唯一對「不得呼叫模型」有機械禁令的一層;如果實作時把這個型別隨手放進 `analyzer`(因為判斷點函式本身在 analyzer),就少了 domain 這一層對「純資料型別不該沾到模型呼叫」的額外把關,而且會跟既有「分類型別歸 domain」的慣例不一致。設計交出去實作時,型別放哪完全看實作者當下的判斷,沒有規範可循。

file: `src/rtb/domain/evidence.py:28,34`(EvidenceKind、TrustClass)
file: `src/rtb/domain/proposal.py:43`(ActionType)
file: `src/rtb/analyzer/task_store.py:202`(TaskRow,持久化列放 analyzer)
file: `src/rtb/analyzer/flow.py:46-63`(Decision 三態,流程控制結果放 analyzer)
file: `src/rtb/domain/ruff.toml:1-27`(唯一禁止匯入模型客戶端的層)

建議改法:設計裡明講判斷點輸入型別與四值列舉定義在 `src/rtb/domain/`(比照 `EvidenceKind`/`TrustClass`),`analyzer` 只匯入它們;若刻意要放 `analyzer`,要寫清楚理由與 domain 那道「不得呼叫模型」保護少一層的取捨。

共 3 條,blocking 3 條。
