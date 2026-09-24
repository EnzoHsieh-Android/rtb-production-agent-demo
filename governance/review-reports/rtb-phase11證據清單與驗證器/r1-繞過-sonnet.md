severity: blocker

### F1 未列入 harness 的 conftest.py 可讓失敗測試在 JUnit 顯示為通過

severity: blocker
blocking: 是(直接打穿第 6 步「真的跑」的信任基礎,而且不會被第 3 步雜湊檢查抓到)
引句:「另帶 harness:證據依賴的測試基礎設施檔(模擬 DSP、假物件、注入樣本、conftest)清單,每一項也帶 sha256」

- file: `/tmp/p11exp/conftest.py` 我寫的實驗:一個 `pytest_runtest_makereport` hookwrapper,把 `rep.outcome` 在 `when == "call"` 時強制改成 `"passed"`。
- file: `/tmp/p11exp/junit4.xml` 用它跑 `test_cheat.py::test_should_fail`(斷言 `1 == 2`,原本必失敗),結果 exit code 0、JUnit 裡是純通過的 `<testcase>`,沒有任何 `<failure>` 節點。
- file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:53` 第 3 步只重算「清單裡列出的」scope 與 harness 檔案雜湊;驗證器對「pytest 實際會載入、但沒被作者列進 harness 的 conftest.py」沒有任何存在性或完整性稽核,pytest 仍會照常載入並套用它。
- 只要一份宣稱的 harness 清單漏列(或作者事後在同一個測試目錄新增)一支 conftest.py,前面四步全部通過的情況下,第 6 步「真的跑」就可能被這支未受管的 conftest 竄改結果,而清單本身完全不會出現雜湊落差、審查員也看不到任何差異提示。這比「未排除」清單裡討論的「雜湊重貼」更嚴重:雜湊重貼至少會在提交裡留下清單差異,這個路徑連清單差異都沒有。
- 觸發門檻:寫這樣的 hook 需要刻意;但「忘記把某支 conftest 列進 harness」是純粹粗心就會發生,而粗心的後果(該檔案的任何改動完全逃出雜湊稽核)跟刻意攻擊一樣重。

### F2 增量 1 只建兩份清單,卻要跟第 1 步「缺五條必要宣稱就擋」同時上線

severity: blocker
blocking: 是(要嘛增量 1 的 CI 自己把自己擋死,要嘛「五條必要」這條規則被延後、之後容易被忘記補上)
引句:「先做冪等與總曝險兩條清單;CI 平行工作」

- file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:51` 「沒有任何清單、或少了五條必要宣稱之一也擋」寫在第 1 步(格式),而拆增量寫明第 1 步在增量 1 就要做出來。
- file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:79` 增量 1 只有冪等與總曝險兩份清單,權限護欄、並行、提示注入三份要到增量 2 才有。
- 兩段字面上矛盾:若第 1 步從增量 1 就檢查「五條都要有」,增量 1 的 CI 平行工作會在自己的第一次執行就被自己的規則擋下;若實作上讓這條檢查延後到增量 2 才啟用,spec 完全沒寫這個分期開關長什麼樣、誰負責在增量 2 打開它——這正是「寫一句已完成、其實忘了補」最容易發生的地方,而且沒有任何機制逼人回來補。

### F3 白名單鍵檢查沒寫明是否遞迴到巢狀物件,結果類欄位可能藏在裡層

severity: major
blocking: 是(能讓「結果」欄位繞過白名單,而白名單擋結果自報正是這一步存在的理由)
引句:「欄位白名單,多一個不認得的鍵就擋(白名單以外的任何鍵都擋」

- file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:40` 這句只講「清單」層級多一個鍵就擋,scope/harness/evidence 底下每一項(例如 `{"node": "...", "covers": [...], "kind": "..."}`)算不算「清單」的一部分、要不要遞迴檢查,全篇沒有第二句話講清楚。
- 若實作(合理地)只逐層檢查最外層已知的固定 schema,而每個 evidence 項目用 `dict.keys()` 減掉已知欄位才判斷「多餘鍵」時很容易漏掉遞迴進 `scope[i]`、`evidence[i]` 這些子物件——一旦漏掉,作者可以在某個 evidence 項目裡塞 `"result": "pass"` 之類欄位,不影響任何機械檢查,但破壞了「清單不准有結果欄」這條使用者裁定的字面保證。
- 這是實作時沒把 schema 檢查寫成真遞迴就會發生的疏忽,不需要刻意繞。

### F4 用參數化的單一個案節點編號取代函式層編號,可以只驗證部分參數

severity: major
blocking: 是(削弱第 4 步列舉覆蓋與第 6 步「全部參數全部要過」的保證範圍)
引句:「參數化的函式層編號展開成全部參數,全部要過」

- file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:56` 這句只規定「函式層編號」要展開成全部參數;沒有規定 evidence 項目「不准」直接寫成 `test_x.py::test_x[case_ok]` 這種已經指到單一參數的節點編號。
- 我的實驗(`/tmp/p11exp/test_demo.py::test_param[2]`)證實 pytest 接受單一參數節點編號、正常收集執行,驗證器沒有機制强迫它一定得是函式層寫法。作者只要在 evidence 裡引用會過的那個參數(例如只引用 `[update_budget]`,不引用同一支測試裡涵蓋作廢或版本衝突的其他參數),就能讓第 6 步跑得動、也讓第 4 步的 covers 標記照樣通過,而實際上多個參數化案例裡失敗或未涵蓋的那些完全不會被跑到。
- 這是撰寫 evidence 時偷懶（少打幾個字、直接複製 pytest 報告裡看到的具體節點編號）就會發生的路徑,不需要刻意造假的意圖。

### F5 symbols 檢查只認「同名定義存在」,不檢查型別或是否真的被證據測試用到

severity: minor
blocking: 否(spec 沒有把 symbols 檢查的失敗與最終通過/擋下的判準直接掛鉤到語意保證,只影響「存在性」這一步本身的嚴謹度)
引句:「symbols 的名稱用語法樹在該檔找得到定義」

- file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:52` 只講「找得到定義」,沒有限定必須是函式/類別定義,也沒有要求該符號被列出的證據測試實際呼叫。
- 若用語法樹掃描時把 `ast.Assign`(例如模組層 `verified_claims = None`)也算作「定義」,作者可以用一個同名的空殼變數取代真正想引用的函式,檢查照樣過關,但這條宣稱實際依賴的行為完全沒被鎖定。⚠ 是否真的接受 Assign 屬於實作細節,目前 spec 沒排除,標記待實作時確認。

### F6 驗證器與產品互不匯入(S808)用語法樹查 import 陳述,可能漏掉動態匯入

severity: minor
blocking: 否(只有刻意寫 `importlib.import_module` 才會觸發,不是粗心路徑)
引句:「驗證器不匯入 rtb 的任何模組(讀程式一律用語法樹與文字、跑測試用子行程)」

- file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:60` 只講「不匯入」,沒有講清楚檢查手法是否涵蓋 `importlib.import_module("rtb.xxx")` 或 `__import__("rtb.xxx")` 這類字串型動態匯入——這兩種在語法樹上不是 `Import`/`ImportFrom` 節點,樸素的 AST 掃描會漏掉。
- 天花板判準:寫這種動態匯入需要刻意規避,不會因為粗心發生,只需要在驗證器實作與其測試裡記一句「不涵蓋動態匯入」即可,不必因此判整份設計失敗。

## 逐節審查

- 背景 / 這份計劃在解決什麼:已讀,無 finding。
- 使用者裁定:已讀,無 finding(四條裁定與後面設計一致,「證據不採信外部結果」「驗證器與 lumos 互不依賴」在設計章節都對應到位)。
- 現況:已讀,無 finding;`CAMPAIGN_WRITE_ACTIONS` 在 `src/rtb/dsp/server.py:110` 只有 `("update_budget", "pause_campaign")`,`void_operation` 路由在 `server.py:107`、其處理常式 `_void_operation`(`server.py:256` 附近)只驗鍵與範圍、不改廣告狀態,跟 spec 第 70 行的窄化說法一致,不重複列為 finding(依審查要求已排除)。
- 宣稱清單(除 F1、F3 已列):其餘欄位(manifest_version、claim_id、policy、enumerations)已讀,無 finding。
- 驗證器六步(除 F1、F2、F4、F5、F6 已列):第 5 步(故障注入)已讀,無新增 finding——「只證明有注入、有斷言,不證明注入有意義」spec 自己已在「未排除」承認,不重複列。
- 五條宣稱:已讀,無 finding。
- CI 與本機:已讀,無 finding;`.github/workflows/ci.yml` 目前只有一個 `checks` job,平行新增一個 job 的做法可行,不影響 60 秒上限那支測試所在的既有 job。
- 拆增量(除 F2 已列):其餘部分已讀,無 finding。
- 合約候選 S800–S809:S800(對應 F3 的邊界情形)、S804(對應 F4 的邊界情形)已在對應 finding 說明;其餘 S801、S802、S803、S805、S806、S807、S809 條文本身與設計敘述一致,已讀,無新增 finding。
- 實務隱患:已排除三項的理由成立,無 finding;「未排除」三項與 F1 的差異已在 F1 內說明(F1 是清單完全看不到差異的更深一層漏洞,不是同一件事,不算重複)。
- 回退:已讀,無 finding,兩個增量的回退步驟與新增內容一一對應。

## 總結

最嚴重等級:blocker;blocking 條數:4(F1、F2、F3、F4)。
