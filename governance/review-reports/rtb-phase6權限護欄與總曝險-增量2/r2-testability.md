severity: minor

審查範圍:凍結快照「## 增量 2 設計:護欄表格」一節(含末尾實務隱患、落點、使用者裁定),鏡頭是可測性,重點驗改寫後的 S402–S408。已核對 `src/rtb/executor/execution.py`(`precheck`、`RESPONSE_TABLE`)、`src/rtb/executor/dsp_client.py`(`_positive_int`、`read_campaign`)、`src/rtb/executor/inbox_store.py`(`BlockCode`、`_proposals_outdated`/`_rebuild_proposals`)、`tests/executor/fakes.py`(`Harness`、`FakeDsp`、`write_config`)、`tests/executor/test_execution.py` 的 `BLOCK_TRIGGERS`、`tests/executor/test_queue.py` 的 `PHASE3_SCHEMA` 舊庫重建測試手法、`tests/analyzer/test_boundaries.py` 的既有 AST/子行程掃描測試、`src/rtb/analyzer/dsp_client.py`/`inbox_client.py`、`src/rtb/httpclient.py`(`request_json`、`ClientHeader`)。

## F1 S402 的「比例上限的判斷函式」沒有指到具體函式名

severity: minor
blocking: 否 — 措辭精度問題,不是行為錯誤;上下文幾乎只指向一種讀法,不會做出錯的測試

引句:「比例上限的判斷函式直接呼叫,不經 DSP 用戶端」

這節在點名既有函式時一律用反引號(例:現況段「執行前檢查(執行迴圈模組的 `precheck`」;簽發器段「簽發器(能力憑證簽發模組的 `sign`」),但 [S402] 新增的這句「比例上限的判斷函式」既沒有反引號、也沒有指到 `precheck`,而「最小設計」全段(見 file: `governance/review-reports/rtb-phase6權限護欄與總曝險-增量2/r2-snapshot.md:112-122`)只說新規則「排在執行前檢查最後」,從沒說過要另外切一支只算比例的函式——`src/rtb/executor/execution.py:280-288` 目前的 `precheck(proposal, view)` 已經是純函式,是本節唯一在算現況相關判斷的候選。照上下文推,「比例上限的判斷函式」最可能就是指增量後的 `precheck` 本身(拿一個現況 `budget=0`、`status="active"`、版本相符的手造 `CampaignView` 直接餵給它,略過真的 DSP 用戶端),但這一支拿掉「有沒有講清楚這支函式存在、在哪、叫法」這個問法時,字面上確實沒有把它釘死——沒有反引號、沒有模組路徑、也沒有排除「另開一支只算比例的輔助函式」這個讀法。若照字面切出一支獨立的比例判斷函式,S400/S401 的「執行前檢查應擋下」與 S402「判斷函式直接呼叫」就會變成兩套不同的呼叫入口,S402 有沒有真的守住 `precheck` 整體行為(而不只是被抽出來的子函式)會失焦。建議把這句改成明寫 `precheck`(比照現況段的反引號慣例),或者如果真的要切一支子函式,把它的名字與所在模組也一併寫進「最小設計」段。

## 觀察(非發現)

S400/S401 的護欄核心行為可以直接用 `Harness`/`FakeDsp` 造:`write_config` 的 `max_budget` 參數與直接改寫 `harness.dsp.campaigns["c1"]` 都能控制現況預算與租戶上限,拿掉比例檢查後 S400 的期望(擋下、不寫嘗試、不呼叫 DSP 寫入)會變成 `Result.EXECUTED` 且 DSP 收到寫入,翻紅是真的;S401 反向驗證減預算/暫停不受影響同樣有殺傷力。

S402「現況預算 0 走不到這一項」的新增文字逐句核對屬實:`src/rtb/executor/dsp_client.py:30`、`:51-55` 顯示 `_positive_int` 把 `budget=0` 過濾成 `None`,導致 `read_campaign` 在 `status != 200 or budget is None` 那條分支丟 `DspUnavailable`(模組說明「其他失敗一律 DspUnavailable」),跟逾時同一條路,`precheck` 根本收不到 `budget=0` 的 `CampaignView`——這句話不是自我宣稱,是可查證的既有行為。不過要注意:`tests/executor/fakes.py` 的 `FakeDsp.read_campaign` 沒有複刻這道過濾(它是直接回傳 `self.campaigns.get(...)`,不管 `budget` 是不是正整數),所以「走真的處理一筆路徑的表格測試,現況預算一律 ≥ 1」這條界線目前只靠測試作者自律、不是 `FakeDsp` 機械擋下;如果表格測試不小心塞進 `budget=0` 的替身廣告,`h.process()` 會走出一條真 DSP 不會產生的路徑(precheck 收到 budget=0 的 view),但這只會讓表格測試的結果跟公式定義一致(0→1 過、2 擋),不會讓任何條款假綠,只是提醒實作測試時這條線要人工守住。

S403/S404 的表格驅動測試設計可以直接對上既有 `BLOCK_TRIGGERS`(`tests/executor/test_execution.py:152-179`)的位置與寫法擴充;完整性測試改用「單筆規則代碼／非單筆代碼」兩份明列清單,能正確涵蓋增量 1 會新增的總曝險擋下原因,不會像現行 `set(BLOCK_TRIGGERS) == set(BlockCode)` 那樣把整個列舉當全集,對症現況段自己點名的假綠缺口。

S406 需要「用增量 1 版的擋下原因列舉(含總曝險代碼、不含比例代碼)建舊資料庫」,這在這個專案裡已有現成、驗證過的手法可以照抄:`tests/executor/test_queue.py:75-100`(`PHASE3_SCHEMA`)就是用一段手寫的 `CREATE TABLE ... CHECK (block_code IN (...))` SQL 字面值,直接用原生 `sqlite3` 連線建出「舊版」資料庫,再開 `InboxStore` 觸發遷移、驗證新代碼寫得進去。S406 只要比照這個既有模式手寫一段固定的舊版 CHECK 子句(增量 1 的列舉快照),不需要新的測試基礎設施,也不用真的切到增量 1 的某個 git 版本。

S408 的掃描範圍擴大(「載入分析行程時帶進來的每一個本專案模組」)可以直接沿用 `tests/analyzer/test_boundaries.py:153-171` 既有的子行程載入手法——該測試已經用 `importlib.import_module` 載入分析行程模組、再讀 `sys.modules` 找出間接載入的套件,S408 只需把 `banned` 前綴比對換成收集 `rtb.` 開頭的每個模組再逐一 AST 掃描,不必新起一種手法。「認呼叫的方式不靠函式名稱比對」要求的「賦值給別名、當參數傳出去、被偏函式包住都要紅」比既有掃描(只比對 import 節點)複雜一階,需要在 `ast.walk` 時額外追蹤父節點才能分辨一個 `Name(id="request_json")` 是被直接呼叫還是被賦值/傳遞,但這用標準 `ast` 模組可以做到(遍歷時自建父節點對照表),不是缺工具;`src/rtb/analyzer/inbox_client.py:18,61`、`src/rtb/analyzer/dsp_client.py:31,118,193` 核對過,目前確實只有這兩支檔案以「從 `rtb.httpclient` 匯入 `request_json`」的方式取得它,呼叫處的 method 都是字面字串、都沒有傳 `headers` 參數,跟設計描述的現況一致,S408 的期望值不會無中生有。

沒有發現「條款寫了行為卻沒有對應測試守」或「測試會假綠」的情形;唯一的落差(F1)是措辭精度,不影響已核對過的六條合約(S403–S408)本身的可測性與翻紅能力。
