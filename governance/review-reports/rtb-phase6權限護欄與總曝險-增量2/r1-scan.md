severity: major

## F1 呼叫識別只認得「直接呼叫字面名稱」,別名、屬性存取、functools.partial 都會讓呼叫整個消失在掃描之外

severity: major
blocking: 是 — 照設計字面實作(逐個 Call 節點比對函式名稱、檢查參數是否字面常數)極可能漏掉一整類開發者會自然寫出的呼叫寫法,讓帶著未授權方法/網址的呼叫完全不出現在掃描結果裡,測試維持綠燈。

引句:「解析分析行程全部原始碼,找出每一個對共用 HTTP 用戶端的呼叫:方法必須是字面常數;GET 可以;POST 只准一處」

設計只講「方法必須是字面常數」「POST 只准一處」,沒講怎麼認出「這是一次對共用用戶端的呼叫」。現有 `test_boundaries.py` 裡既有的兩個掃描(禁止匯入網路模組、禁止匯入簽發器)之所以能不管別名也抓得到,是因為它們比對的是 `ast.Import`/`ast.ImportFrom` 的**模組名稱**(`file: tests/analyzer/test_boundaries.py:104-140`),`alias.asname` 不影響 `alias.name`,別名對這種「整支模組禁用」的掃描沒有意義。但 S408 要查的是**呼叫**,不是「有沒有匯入」,而且是對一支本來就允許匯入的模組(`rtb.httpclient`)。這種檢查如果照最直覺的寫法去比對 `ast.Call` 且 `node.func` 是 `ast.Name(id="request_json")`,以下幾種開發者會自然寫出的變體都不會被辨識成「對共用用戶端的呼叫」,因而完全不受檢查:

- 匯入時取別名:`from rtb.httpclient import request_json as rj`,之後呼叫 `rj(url, "POST", body, t)`——目前 `src/rtb/analyzer/dsp_client.py:31` 與 `src/rtb/analyzer/inbox_client.py:18` 都是 `from rtb.httpclient import request_json`(無別名),但設計沒有規定不能取別名,換一個貪快的開發者很自然會縮寫。
- 用模組限定存取:`from rtb import httpclient` 後寫 `httpclient.request_json(url, "POST", body, t)`——這時 `node.func` 是 `ast.Attribute`,不是 `ast.Name`,同一套「比對名稱字串」的邏輯要多寫一層才抓得到,設計沒提。
- `functools.partial`:`_post = functools.partial(request_json, method="POST")`,之後呼叫 `_post(url, body, t)`。這裡「呼叫 `request_json`」這件事在語法樹上被拆成兩段——`functools.partial(request_json, method="POST")` 這一行的 `func` 是 `functools.partial`,`request_json` 只是被當成參數傳進去的一個名字,不是被呼叫;真正發生呼叫的那一行 `func` 是 `_post`。任何只找「呼叫 request_json」這個 Call 節點的掃描,兩行都會跳過,`method="POST"` 這個字面常數也就永遠不會被檢查到。這是很平常的「幫兩三個地方共用同一個 GET/POST 呼叫方式」的寫法,不是刻意繞過。

反過來,如果掃描只認「呼叫時的字面引數」而不特別處理間接呼叫,大部分把 method 抽成變數往下傳的寫法(例如寫一支 `_call(url, method, body)` 再轉呼叫 `request_json(url, method, body, t)`)會讓 `method` 在真正呼叫 `request_json` 的那一行變成一個 `ast.Name`,依設計本身「方法不是字面常數就要紅」的規則,這類間接呼叫其實會被正確攔下——這點設計是抓對的。真正漏的是上面三種「呼叫節點本身就對不上『request_json』這個名字」的情況,跟字面常數檢查無關,是呼叫識別本身的問題。

建議:比照既有兩支掃描的做法,不要靠比對 Call 節點的名稱字串,改成先解析檢查對象檔案裡 `rtb.httpclient` 這個模組(以及它匯出的 `request_json`、`ClientHeader`)在本地被綁定到哪些名字(含 `import ... as`、`from ... import ... as`、以及賦值成新變數名如 `_post = request_json`),再用這組名字集合去比對呼叫節點;另外把「把 `request_json`(或其別名)當成非呼叫的值傳給任何其他呼叫(例如 `functools.partial`、賦值給另一個名字後再呼叫)」本身視為一種違規模式,直接讓測試紅,不去嘗試追蹤間接呼叫的實際引數——這樣即使有開發者寫出間接呼叫,測試也會因「偵測到間接引用」而攔下,不需要真的解析出最終的字面常數。

## F2 掃描範圍若只走「分析行程套件本身」,漏了分析行程會載入但實體檔案不在分析行程套件裡的共用模組

severity: major
blocking: 是 — 與 F1 同一個「每一個」完整性主張(S408 條款字面寫「每一個對共用 HTTP 用戶端的呼叫」),掃描範圍若照既有掃描的慣例只走 `src/rtb/analyzer/*.py`,會漏掉分析行程實際會執行到、但檔案位置在套件外的共用程式碼。

引句:「解析分析行程全部原始碼,找出每一個對共用 HTTP 用戶端的呼叫」

分析行程實際載入、執行到的程式碼不只 `src/rtb/analyzer/` 底下六支檔案——`dsp_client.py`、`inbox_client.py`、`flow.py`、`policy.py`、`task_store.py`、`instrumented.py` 逐一匯入了 `rtb.httpclient`(共用 HTTP 用戶端本身)、`rtb.sqlitekit`、`rtb.domain._checks`、`rtb.domain.evidence`、`rtb.domain.metrics`、`rtb.domain.proposal`、`rtb.domain.task_state`、`rtb.domain.attempt`,這些檔案物理上都不在 `src/rtb/analyzer/` 底下(用 `grep -rhn "^from rtb\|^import rtb" src/rtb/analyzer/*.py` 核對過,見上)。既有 `test_the_analyzer_reaches_the_network_only_through_the_shared_client`(`file: tests/analyzer/test_boundaries.py:96-115`)與 `test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer`(`file: tests/analyzer/test_boundaries.py:119-140`)都只走 `analyzer.rglob("*.py")`,也就是只掃 `src/rtb/analyzer/` 這一個目錄樹;S408 的設計敘述沿用同一句「解析分析行程全部原始碼」,如果實作時照抄既有掃描的目錄範圍(這是最自然、最省事的做法,前例就是這樣做的),就只會掃 `src/rtb/analyzer/*.py`,不會掃 `rtb.httpclient`、`rtb.domain.*`、`rtb.sqlitekit` 這些被匯入、實際會被分析行程執行的模組本身。

目前這幾支被匯入的模組裡沒有寫任何 HTTP 呼叫(`rtb.httpclient` 本身是共用用戶端的定義處,不是呼叫處;`rtb.domain.*`、`rtb.sqlitekit` 都是純邏輯/資料庫層,沒有網路呼叫),所以現況下不構成可利用的漏洞。但這正是「防忘記」的情境該防的方向:將來如果有人在 `rtb.domain.*`(分析與執行兩側共用的領域層)裡新增一支被分析端呼叫到的輔助函式,而那支函式裡意外帶了一次共用用戶端呼叫(例如替某個共用檢查加一個「順便回報」的呼叫),只掃 `src/rtb/analyzer/` 的實作完全看不到,測試仍然全綠。

建議:設計裡明講掃描範圍是「`src/rtb/analyzer/` 底下的原始碼,加上它遞移匯入、且不在 `rtb.executor`/`rtb.dsp` 之下的每一個模組」,或者比照既有 `test_importing_the_analyzer_does_not_load_the_capability_module`(`file: tests/analyzer/test_boundaries.py:153-171`)的做法,額外用子行程真的把分析行程整個載入,對 `sys.modules` 裡「屬於這次載入、且不是標準庫」的每一支模組都跑一次同樣的呼叫掃描,不要只信任靜態列出的檔案清單。

---

以下是查證後確認沒有問題的觀察,不算發現:

引句:「POST 只准一處,而且網址以收件口的提案路徑結尾」

檢查了「inbox 的 POST 路徑被改成打到 DSP 位址」這個角度:S408 的掃描是純原始碼層級的字面比對(呼叫時的 URL 字串字面常數結尾是不是收件口的提案路徑),並不驗證 `base_url` 這個執行期參數實際指向誰,所以理論上就算有人把接線接錯(`inbox_client.make_client` 被餵進 DSP 的位址),原始碼裡那個 `f"{base_url}/proposals"` 的字面結尾依然是 `/proposals`,靜態掃描本身看不出接線接錯。但這條路徑實際上打不穿:DSP 的路由表(`file: src/rtb/dsp/server.py:80-88`)完全沒有 `/proposals` 這個端點,它的寫入端點是 `/campaigns/{id}/budget`、`/campaigns/{id}/pause`、`/campaigns/{id}/void`,三者都要求先驗過 `X-Capability` 標頭(`file: src/rtb/dsp/server.py:165-184`);分析行程沒有能力產生合法的能力憑證(既有 `test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer` 與 `test_importing_the_analyzer_does_not_load_the_capability_module` 已經守住這一點),就算接線接錯把 `/proposals` 打到 DSP 位址,也只會收到 DSP 的 404,不會造成寫入。這個角度不成立成一個獨立的洞,是「配置接線錯誤」而不是「S408 這道掃描自己的邏輯漏洞」,而且有 DSP 端憑證檢查兜底,跟設計自己承認的「掃描只防忘記,兜底是 DSP 端沒有憑證一律拒收」是同一道防線,沒有另外留縫。

另外核對了「共用用戶端之外的其他共用模組」這個角度:`src/rtb/` 底下除了 `httpclient.py` 之外沒有第二支可以發出 HTTP 請求的共用函式——`httpkit.py` 是伺服器端(給 `DspServer`/`InboxServer` 用的 handler 基礎設施,不是用戶端)、`sqlitekit.py` 是資料庫連線輔助、`capabilitykit.py` 只管簽章與讀金鑰,三者都沒有對外發請求的能力。所以「開發者不小心繞道另一支共用發請求函式」這個角度目前在這個程式庫裡不成立,值得記錄的反而是 F1/F2 講的「同一支 `request_json` 但呼叫識別/掃描範圍不夠」。

「分析端 HTTP 用戶端標頭」這一段(`headers` 引數)的防線其實有兩層,不是只靠 S408 這道原始碼掃描:`rtb.httpclient.request_json` 本身在執行期就會核對每個標頭鍵是不是 `ClientHeader` 列舉成員,不是就丟 `TypeError`(`file: src/rtb/httpclient.py:56-61`)。S408 的「不帶自訂標頭」斷言是這道執行期檢查之外多一層原始碼層級的預先攔截,兩者不衝突,但 S408 這層一樣受 F1 講的呼叫識別問題影響(如果呼叫節點本身沒被認出是對 `request_json` 的呼叫,連帶標頭檢查也一起被跳過)。

## 跟既有 test_boundaries.py 掃描的重疊判斷

不重疊,是補一塊真空:既有四支測試(`test_the_analyzer_package_never_imports_dsp_internals`、`test_the_agent_cannot_reach_fault_injection_on_production_style_servers`、`test_fault_headers_sent_to_production_style_servers_are_refused`、`test_the_analyzer_reaches_the_network_only_through_the_shared_client`、`test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer`、`test_importing_the_analyzer_does_not_load_the_capability_module`,共六支,`file: tests/analyzer/test_boundaries.py`)守的是「分析行程完全不能碰網路原始模組」與「分析行程完全不能碰到簽發能力的鑰匙或模組」,都是「整支模組/整個名稱空間禁用」的粗粒度掃描,對別名天然免疫(比對的是模組名稱,不是本地綁定名)。S408 要守的是完全不同層次的東西:分析行程被**允許**用共用用戶端讀資料、送提案,S408 要確保它「只用來讀」加「只送去收件口那一個地方」——這是對允許呼叫的**引數**做檢查,前面六支測試沒有一支碰這件事,設計自己的「現況」小節也點名這是缺口(「缺的是:沒有任何測試守住『分析行程的程式裡沒有打 DSP 寫入端點的呼叫』」)。這個判斷跟設計原文一致,沒有矛盾;真正的問題只在於新加的這道檢查本身(F1、F2)夠不夠緊。
