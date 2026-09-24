severity: major
Now composing the final report.

severity: major

### F1 候選硬逾時包裝沒有既有的「同步呼叫加逾時」機制可比照,設計也沒指定機制

severity: major
blocking: 是
引句:「另有一層包裝負責硬逾時:候選超過逾時沒回、丟例外、回傳不是那個列舉」

專案裡目前所有「逾時」都是網路層的合作式 socket 逾時(`urlopen(..., timeout=...)`、`http.client` 的 connection timeout、SQLite 的 `busy_timeout`),機制成立的前提是被呼叫端(作業系統的 socket)本身支援逾時中斷。候選是「型別協定定義」的任意同步 Python 呼叫(不保證是網路呼叫,之後接的可能是 Jev/模型 API,也可能是別的同步規則),要對它做「硬逾時」——候選本身不配合、甚至掛住不回——就不能靠 `timeout_seconds` 參數這種合作式做法,必須換一種完全不同的機制(執行緒 + `join(timeout)`,或 `signal.alarm`,或子行程),而這三種在 `src/rtb/` 生產程式碼裡都沒有先例(`threading` 只在 `httpkit.py` 用來跑伺服器的 accept 迴圈,不是拿來對呼叫方逾時)。更麻煩的是:執行緒式的「硬逾時」其實不硬——`join(timeout)` 逾時只是呼叫端放棄等待,候選那個執行緒仍在背景跑,不會被殺掉,這對「純判斷」的領域層/評估套件而言是一個新的資源存活期問題,設計完全沒提。設計又沒寫死是哪種機制,等於把「引入第二種逾時做法」的決定丟給實作階段,審查階段完全看不出這條路徑守不守得住。

file: `src/rtb/httpclient.py:52` (現有逾時都是傳給 `urlopen` 的合作式 socket timeout)
file: `src/rtb/httpkit.py:15` (production 唯一的 `threading` 用途是伺服器 accept 迴圈,不是呼叫方逾時包裝)
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase10評估與Jev決策點_計劃.md:53`

建議:實作前先在設計裡把機制釘死是哪一種(執行緒/子行程/其他),並老實承認執行緒版「逾時」不等於「終止候選」,把這個殘留風險寫進〈實務隱患〉;或者限定候選介面本身必須是可信任會逾時的 I/O 呼叫(重用 `httpclient.py` 現成的 `timeout_seconds` 合作式模式),不要無條件承諾「硬」逾時。

### F2 「隔離模組執行舊原始碼」等於引入 exec/動態執行,跟專案既有的行為比對做法(靜態掃描)不同一種,也跟專案自己對 exec/importlib 的敵意矛盾

severity: major
blocking: 是
引句:「在隔離的模組裡執行舊原始碼,對那批輸入的結果等於存下的結果」

專案現有「新舊程式行為/寫入意圖比對」一律走靜態語法樹分析,不動態執行任何字串化的原始碼:`tests/executor/write_scan.py` 用 `ast.parse` 拼字串常數、遞移追函式呼叫判定「會不會寫資料庫」;`tests/executor/audit_guard.py` 用同一套 AST 結果做稽核表守衛。全專案搜不到任何「把原始碼存成字串常數、在測試裡動態組出模組執行」的先例(`exec`/`compile`/`module_from_spec`/`SourceFileLoader` 一次都沒出現在 `tests/` 或 `src/`)。更關鍵的是,這不只是「沒有先例」,是跟專案立場正面衝突:領域層的匯入禁令明講「領域層不得動態匯入,會繞過這裡的檢查」,維運套件的邊界測試把 `eval`、`exec`、`compile`、`importlib`、`__import__` 整組列進 `DYNAMIC_LOOKUPS`/`BUILTIN_RUNNERS`,連經 `builtins` 別名繞過都要抓——這一整組機械防線存在的理由就是把「動態執行字串化的程式碼」當成攻擊面看待。這次要動態執行的原始碼雖然是規則作者自己存的固定資料而不是外部輸入,但引入的执行機制(exec/動態組模組)本身是專案從沒用過、且被其他層明文當作危險模式禁止的第二種「驗證行為不變」做法。

file: `tests/executor/write_scan.py:17` (既有「比對新舊程式行為」一律靜態解析 AST,不執行)
file: `tests/executor/audit_guard.py:9` (稽核表守衛同樣是靜態掃描,不執行)
file: `src/rtb/domain/ruff.toml:27` (領域層明文禁止動態匯入)
file: `tests/ops/test_ops_boundaries.py:96` (`DYNAMIC_LOOKUPS` 把 eval/exec/compile/importlib 整組當危險模式掃)

建議:改用專案既有的「樣本模組」模式(比照 `tests/domain/proposal_samples.py`)——把改動前的 `decide()` 直接存成 `tests/analyzer/` 底下一支普通的、正常 `import` 的凍結 `.py` 檔(例如 `frozen_policy_v1.py`),測試裡正常 `from tests.analyzer.frozen_policy_v1 import decide as old_decide` 呼叫,雜湊照樣釘住那支檔的內容;這樣完全不需要 `exec`/動態組模組,行為比對(呼叫舊函式拿結果比對)一樣做得到,又不引入專案沒有、且被自己其他層明文禁止的機制。

共 2 條,blocking 2 條。
