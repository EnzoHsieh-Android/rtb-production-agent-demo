severity: major

### 1. 「操作查詢」可選參數沒有接到既有的推進機制上:HANDED_OFF 的短路寫在取得租約之前、也不在 `_STEPS` 分派表裡

severity: major
blocking: 是 字面實作若只在 `advance()` 簽章加一個具名參數,不動這段既有的短路與分派結構,S301/S310/S311/S313/S317 等所有「有給操作查詢」的新行為永遠到不了,連租約都拿不到
引句:「沒有給 → 已交給執行的任務照 Phase 4 的行為直接返回,不呼叫任何協作者;有給 → 走下面那一步」

計劃只說「有給 → 走下面那一步」,但沒有交代這一步怎麼接進 `advance()` 現有的結構。現在的程式碼裡,HANDED_OFF 跟終點狀態被同一行擋在租約之前直接返回,而且 HANDED_OFF 從來不是 `_STEPS` 分派表的一員(表裡只有 RECEIVED/COLLECTING_EVIDENCE/ANALYZING/PROPOSED 四個狀態,每個都由 `_advance_holding` 統一取租約、統一呼叫、統一 `commit_step`)。計劃要求「有給操作查詢」時 HANDED_OFF 要「照 Phase 4 增量 3b 的任務租約走(先取得租約才呼叫外部)」,這代表現有的「先短路、後取租約」順序必須拆開重寫,而且新的一步要嘛擴充 `_Collaborators`/`_STEPS`(讓其餘四步也多帶一個永遠用不到的可選欄位)、要嘛在 `_advance_holding` 外面另開一條不經過統一分派與統一放租約邏輯的路。計劃完全沒提這兩條路怎麼選,對「架構怎麼接」這件事是空白的。
file: `src/rtb/analyzer/flow.py:143`
file: `src/rtb/analyzer/flow.py:146`
file: `src/rtb/analyzer/flow.py:247-252`

### 2. 「一般建任務入口拒絕 fu- 開頭編號」沒有指明只能管 task_id,而現有的識別碼格式檢查是全專案共用一份

severity: major
blocking: 否 只要實作時把新檢查限定在 `create_task` 的 `task_id` 參數本身、不去動 `is_id()`,就不會出事;但計劃沒寫這條限制,而現有唯一的格式檢查函式正好被 `create_task` 同一行拿來同時檢查 `task_id` 與 `campaign_id`,是很容易被字面接錯的位置
引句:「只有一個建任務入口,照字面加檢查會連接續任務自己都擋掉」

`domain/_checks.py` 的 `is_id()` 是全專案共用的識別碼格式檢查(依筆記「領域層的共用小檢查…只有一份定義」的既有做法),不只用在任務編號:`task_store.create_task` 同一行用它同時檢查 `task_id` 和 `campaign_id`;`domain/evidence.py` 用它檢查 `evidence_id`/`task_id`,還用它檢查可信證據 payload 的每個鍵與字串值(短代號規則);`executor/capability_signer.py` 用它檢查租戶設定裡的廣告編號清單。計劃講「一般的建任務入口拒絕『fu-』開頭的編號」時,只提到「入口」,沒有講清楚這個新規則的作用範圍是「這個入口收到的 task_id 這一個值」,還是順手掛在 `is_id()` 本身。若掛在後者(對只有一個共用函式、字面上最省事的接法),會連廣告編號、證據編號、證據內容裡任何以 fu- 開頭的短代號值都一起被拒收,這些跟接續任務的保留命名空間毫無關係。
file: `src/rtb/domain/_checks.py:25-26`
file: `src/rtb/analyzer/task_store.py:187`
file: `src/rtb/domain/evidence.py:66-67`
file: `src/rtb/domain/evidence.py:120`
file: `src/rtb/executor/capability_signer.py:89-90`

### 3. 分析端新的「可依廣告篩」指標查詢是任務表第一次要用 campaign_id 過濾,但計劃沒提索引,跟執行端同類查詢的既有做法不一致

severity: minor
blocking: 否 只影響查詢效能,不影響結果對不對,也不影響 F4 或既有合約
引句:「兩支唯讀查詢,各自讀自己那一側不會被清的資料」

執行端的嘗試紀錄表(`attempt_store.py`)對「依廣告篩」這類查詢,已經明確建了 `attempts_first_rows`、`attempts_terminal_rows` 這類針對 `campaign_id` 的局部索引;但分析端的 `tasks` 表(`task_store.py` 的 `SCHEMA`)自始至終只有 `PRIMARY KEY (task_id, seq)`,沒有任何以 `campaign_id` 為條件的索引,因為既有查詢一律用 `task_id` 找。計劃要求的「分析端:重新規劃次數、重新規劃用完次數…可依廣告篩」是這張表第一次要支援依廣告過濾的統計查詢,卻沒有像執行端那樣提到要補對應索引,兩側做法不一致。`tasks` 表本身又是已知會無界成長的只增表,少了索引的影響會隨時間放大。
file: `src/rtb/executor/attempt_store.py:53-56`
file: `src/rtb/analyzer/task_store.py:27-44`
