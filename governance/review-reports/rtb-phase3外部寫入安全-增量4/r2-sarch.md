severity: minor

### 1. 作廢端點的範圍驗證沒交代怎麼套進既有的 `check_scope`,字面實作有滑向第二套範圍檢查的風險

severity: minor
blocking: 否(未確定會走向第二種做法,只是設計沒交代清楚,屬於文件缺口而非已引入的偏離)
引句:「範圍比照寫入(廣告、冪等鍵、租戶都要等於聲明)」

說明:增量 2 建立的 `check_scope`(`src/rtb/dsp/capability.py:122-135`)是本專案對「請求範圍」唯一的比對函式,寫死比對六項:廣告、動作、冪等鍵、租戶、新預算、預期版本,其中 `is_plain_int(request.expected_version) and claims.expected_version == request.expected_version` 這一項對所有動作一視同仁,沒有依動作分流。增量 4 對作廢的授權只說「範圍比照寫入(廣告、冪等鍵、租戶都要等於聲明)」,合約 [S88] 則寫「DSP 對作廢應驗證憑證的廣告、冪等鍵、租戶範圍」——只點名三項,沒提新預算與預期版本這兩項既有函式一定會查的欄位,也沒說作廢請求的本文要不要帶 `expected_version`。

照字面實作,若直接沿用既有 `check_scope`(六項都比對)但作廢請求本文沒有 `expected_version` 欄位,`is_plain_int(request.expected_version)` 會恆為 False,`check_scope` 會把每一次合法的作廢都判成 `capability_scope_mismatch`,對帳「作廢再判失敗」整條路徑打不通(例:對帳查不到某把鍵、執行前檢查判定業務上不過,呼叫簽發器簽出作廢憑證後送出作廢請求 → 預期回「已作廢」,但因 `check_scope` 缺 `expected_version` 而回 403,對帳沒有出路)。要避開這個死路,只剩兩條:(a) 幫作廢請求的本文也塞一個「總會等於聲明」的 `expected_version`,讓那一項比對形同虛設、不是真檢查;(b) 另外寫一個只比三項的範圍檢查函式給作廢端點用。(b) 正是本檔一路強調要避免的「另開一份」(參照本節「儲存層只多一個…判斷,不碰憑證、不碰時間」與增量 3「回應對照只有一份」的既有做法),但設計目前沒有明講該走哪一條,讓實作者容易在不自覺間走上第二套範圍驗證邏輯。

file: `src/rtb/dsp/capability.py:122`
