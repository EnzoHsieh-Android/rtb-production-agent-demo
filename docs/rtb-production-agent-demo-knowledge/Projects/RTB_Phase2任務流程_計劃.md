---
type: project
status: done
created: 2026-09-22
updated: 2026-09-22
tags:
  - type/project
  - status/done
lands_in:
  - Systems/任務流程領域模型
  - Systems/共用行程基礎
  - Systems/提案收件口
  - Systems/分析行程流程與檢查點
---
# RTB_Phase2任務流程_計劃

PRIOR-ART: 任務狀態機、證據與提案都可以用標準函式庫的 dataclass 與列舉自己寫,不需要工作流程引擎(Temporal、Airflow 等);本專案要證明的是「當機重啟後的安全語意」,引擎會把這些語意藏起來。採用:借用列舉與 dataclass,自建狀態轉換表。
RETIRE-IF: 若 Phase 3 到 4 做完後,任務狀態機的轉換表仍沒有抓到任何一次「非法轉換」或「過期證據被沿用」的錯誤,而換成現成的狀態機函式庫才抓得到,就該重審自建的部分。

## 這份計劃在解決什麼

- 事故:agent 做到一半當機重啟,不知道自己做到哪,於是把做過的事重做一遍(浪費錢,甚至重複寫入),或把過時的判斷當成現在的事實繼續執行。
- 範圍:交接文件 Phase 2 的「單一廣告的完整流程」,不加佇列。依 [[Projects/RTB_Agent_Phase0架構]] 的決策,從這一階段起分析與執行是兩個行程,中間以有大小上限的提案通道交接。
- 一次只做一個增量,每個增量先寫事故測試。

## 四個增量

- 增量 1:純程式的領域模型(任務狀態機、證據與新鮮度檢查、提案的嚴格解析)。放在領域層,不碰資料庫與網路。
- 增量 2:執行行程的提案收件口與提案佇列(有大小上限的 HTTP)。這是高風險的跨行程交接,動手前要先過設計審。
- 增量 3:分析行程的流程與檢查點,存進自己的資料庫。
- 增量 4:事件與 trace,把整條正常流程串起來,並補驗 agent 端連不到故障注入、不匯入 DSP 內部模組這兩條(Phase 1 留下的)。

## 核心概念(使用者已在 2026-09-22 對話中答對並收到回饋)

- 狀態(State)是系統已知的事實;檢查點(Checkpoint)只是「哪一步的輸出已安全存好、重跑不出事」的進度標記,不保證證據現在還是真的。
- 證據是「事實在某個時間點的快照」,要記下讀取時間與讀到的廣告版本。
- 新鮮度由純程式判斷:先看年齡,再看版本;過期或版本已變就重讀。重讀是安全的,重做副作用才危險。
- 就算證據沿用,執行的那一刻執行行程仍要重讀 DSP 現況、比對版本、重跑整套檢查。新鮮度是第一道,執行前重新授權是最後一道。

## 任務狀態機(分析行程這一側)

- 狀態:收到、蒐集證據、分析、已提案、已交接(執行行程的收件表已接受)。終點:完成、失敗、被擋、不需動作、被取代。
- 合法轉換:收到 → 蒐集證據;蒐集證據 → 分析;分析 → 已提案 或 不需動作;已提案 → 已交接;已交接 → 完成、被擋或被取代。任何非終點狀態都可以 → 失敗。
- 重新規劃的路:分析 或 已提案(證據過期、提案過期)→ 蒐集證據。
- 終點狀態沒有任何出路。狀態機是一張表,不散落在條件判斷裡。
- 執行行程那一側的操作狀態(接受、驗證、執行中、驗證結果、成功、失敗、結果不明)留到增量 2 與 Phase 3 定義。

## 證據與提案

- 證據欄位:證據編號、任務編號、種類(廣告狀態或指標)、來源、讀取時間、讀到的廣告版本、內容雜湊、信任標記(可信或不可信文字)。
- 提案欄位照交接文件第 3 節:任務編號、廣告編號、動作類型、要求的變更、原因代碼、證據參照、觀察到的廣告版本、決策建立時間、決策到期時間、政策版本、風險摘要;另加版本序號。
- 提案是不可信輸入:解析採嚴格白名單,不認得的欄位一律拒收(防止夾帶工具名稱、網址或憑證),型別、大小與長度都有上限,解析結果是「成功或錯誤清單」,不丟例外也不放行。

## 驗收(對照交接文件 Phase 2 的完成條件)

- 獨立的驗收紀錄在 [[Verification/Phase2驗收紀錄]](2026-09-23 補寫,跟 Phase 1、3 對齊);下面是當初寫在計劃裡的原文。
- 合法與非法的狀態轉換都有測試,包含終點狀態沒有出路。
- 新鮮度:過期、版本已變、剛好在邊界、時鐘倒退、沒有時區的時間,各有測試。
- 提案解析:合法、缺欄位、多餘欄位、型別錯、過大、過期時間早於建立時間,各有測試。
- 提案與副作用明確分離:提案物件沒有任何執行能力,只是資料。
- 使用者能用自己的話說明狀態與檢查點的差別:已在 2026-09-22 對話完成。
- 增量 4 已交付:任務能真的從建立走到分析行程這一側的終點(HANDED_OFF 或 NO_ACTION),`trace_for` 把證據、決策(提案快照裡的 `policy_version`)、工具呼叫串成一條可查的紀錄,`test_the_agent_cannot_reach_fault_injection_on_production_style_servers` 用真的 DSP 與收件口跑過這整條路徑。
- 措辭校正(2026-09-22 收尾核對時發現):交接文件原文 Phase 2 寫的是「一條 trace 可連結 Evidence、decision、policy、tool attempt 與 **verification**」(含執行後的驗證結果),但 [[Projects/RTB_Agent_Phase0架構]] 的決策已把分析與執行拆成兩個獨立行程,「verification」(執行是否真的成功)屬於執行行程(這份計劃稱為 Phase 3)的職責,不在分析行程能交付的範圍——這是決策的必然結果,不是漏做;trace 目前只到 HANDED_OFF 為止,交接之後的驗證結果要接上執行行程才看得到,屬 Phase 3 的範圍。

## 增量 2 設計:提案收件口(2026-09-22,第 2 版:已折入第 1 輪設計審)

PRIOR-ART: 最小解是「函式呼叫」,但那測不出跨行程的失敗語意;世界上這件事用訊息佇列加冪等鍵解(至少一次投遞加去重)。採用「去重鍵」的想法,自建最小的本機 HTTP 收件口,但**共用**專案已有的伺服器與 SQLite 做法(見下「共用基礎」),不引入佇列軟體。
RETIRE-IF: 若 Phase 4 做完後,收件口的衝突拒收、跳號拒收與滿載拒收從沒被任何測試或事故抓到,就該檢討這條通道是否過度設計。

- 事故:分析行程當機重啟後重送提案;或被誘導而對同一個任務送出內容不同的第二份、或用極大的修訂序號搶位、或用大量任務編號填滿收件口。使用者已在 2026-09-22 選對:相同內容當作重送,不同內容拒收。
- 位置:執行行程(唯一有權寫 DSP 的一側)提供收件口,寫進執行行程自己的 SQLite 收件表;分析行程只是呼叫者,不開這個檔。
- **收件鍵**(任務編號, 修訂序號):與 Phase 0 架構的收件表唯一約束一致。它跟 DSP 的操作冪等鍵是兩個不同的東西,只是都在做去重,文件中不混用名稱。
- **內容雜湊**:提案經增量 1 的解析後,轉成基本型別(`to_primitives`),時間一律換成 UTC,以固定鍵排序、無多餘空白、不允許 NaN 的 JSON 序列化,取 SHA-256。串列的順序有意義(順序不同就是不同提案)。這個雜湊函式增量 2 新增,放在領域層,不碰資料庫。
- **收件表狀態**:待處理(pending,即「在途」)、已被取代(superseded)、已過期(expired)。之後的階段會再加交接後的狀態;這一版只定義收件口自己需要的三個。
- **在途**的定義:狀態為待處理的提案。同一個任務最多一份待處理。全域上限是啟動時給的常數(預設 8;Phase 0 只要求單一在途任務,所以這是收件口能容納的上限,不是並行執行數)。
- **修訂序號嚴格連號**:任務的第一份提案必須是修訂 1,之後每份必須是「已收最高修訂加 1」。這擋掉用巨大序號搶位;分析端重新分析要送 N+1,不能跳號。
- **判斷順序**(每個請求照這個順序,先中先回):
  1. 請求前置檢查:Host 標頭、有沒有 Origin 標頭、Content-Type、大小、JSON 語法、逾時。
  2. 嚴格解析(增量 1 的 `parse_proposal`)。
  3. 進入單一 `BEGIN IMMEDIATE` 交易,以下全部在同一個交易內:先把到期的待處理標為已過期;查收件鍵,存在則比雜湊(相同→重送結果、不同→衝突);不存在則檢查修訂連號、決策到期時間、全域上限;最後寫入並提交。
  4. 提交成功之後才回應。
- **重送結果**:200,內容為 {"status":"accepted","task_id","revision","state":<目前狀態>,"content_hash","replayed":true};首次接受是 201,同樣的欄位、replayed 為 false。所以重送永遠回「目前」的狀態(可能已被取代或已過期),而且重送不受全域上限影響(它不新增任何東西)。
- **取代**:接受修訂 N+1 時,同一任務的待處理修訂 N 在同一個交易內標為已被取代;因為取代先釋放名額,全域已滿時「取代自己任務的舊提案」仍可接受,不會被上限擋住。
- **決策到期**:收件時決策到期時間已過,回 422 `expired_proposal`、不寫入提案(沒有東西可交接),只記事件。待處理的提案之後到期,由下一次任何請求的交易順手標為已過期,不需要背景執行緒。
- **事件紀錄**:單一表,每筆只有固定欄位(時間、任務編號如果符合編號格式否則空、修訂、事件代碼、內容雜湊或空),絕不存請求原文;事件代碼是封閉列舉。表有筆數上限(1000),寫入時同一交易內刪除最舊的;事件寫入失敗不影響對呼叫者的回應。
- **HTTP 層防護**:只接受 Content-Type 為 application/json 的 POST(否則 415);請求帶有 Origin 標頭一律 403(瀏覽器跨站請求會帶,程式呼叫者不帶);Host 必須是回送位址;啟動時綁定位址不是回送位址就拒絕啟動。
- **失敗語意**:交易提交前的任何失敗(當機、磁碟滿、鎖逾時)都是「沒收到」;提交後回應前當機是「收到了但呼叫者不知道」,呼叫者重送就得到重送結果。忙碌逾時回 503 `busy`,與全域已滿的 503 `inbox_full` 用不同的錯誤代碼。
- **分析端的恢復路徑**:遇到 409(同鍵不同內容)或修訂被拒,分析端的正確做法是以「已收最高修訂加 1」送出新提案;這一步的呼叫端行為在增量 3 實作,這裡只保證收件口的回應足以讓它判斷(錯誤代碼固定、含目前已收最高修訂)。

### 共用基礎(避免長出第二套)

- 把 DSP 伺服器裡與業務無關的部分抽成共用模組(回送位址與 Host 檢查、有上限的請求本文讀取、逾時、JSON 錯誤回應、未預期例外的 500 後備、故障注入標頭的開關),DSP 與收件口都用它;抽出時 DSP 既有的測試必須全部維持通過。
- SQLite 連線建立(WAL、忙碌逾時、每執行緒一條連線、`BEGIN IMMEDIATE`)同樣抽成共用小模組。
- 故障注入沿用 DSP 的做法(啟動時帶旗標才接受 X-Fault 標頭),收件口新增兩種:提交前中斷、提交後回應前中斷,讓「提交前」在測試中可以被製造。
- 圖譜:新增兩篇 Systems 節點,一篇管共用基礎模組、一篇管收件口(收件表、事件表、收件口伺服器);領域層的雜湊函式寫進既有的「任務流程領域模型」。

### 收件口的合約

- [S1] 當收件口收到「任務編號與修訂序號都相同、內容雜湊也相同」的請求,收件口應回 200 與該提案目前的狀態且不新增任何一筆,即使收件口已滿或該提案已被取代或已過期。[test:test_a_resent_identical_proposal_returns_the_current_state_and_adds_nothing_even_when_full_or_superseded]
- [S2] 當收件口收到「任務編號與修訂序號相同、內容雜湊不同」的請求,收件口應以 409 拒收、不改動已收的那一份,並記一筆衝突事件。[test:test_a_different_proposal_for_the_same_key_is_rejected_and_the_original_is_untouched]
- [S3] 當收件口收到一份新的收件鍵,而它的修訂序號不是「已收最高修訂加 1」(任務第一份必須是 1),收件口應以 409 拒收並記事件,錯誤內容含目前已收最高修訂。[test:test_a_revision_that_is_not_exactly_the_next_one_is_rejected_with_the_current_highest]
- [S4] 當同一任務收到修訂 N+1 並接受,收件口應在同一個交易內把待處理的修訂 N 標為已被取代,且即使全域已滿也照樣接受。[test:test_accepting_the_next_revision_supersedes_the_previous_one_even_when_the_inbox_is_full]
- [S5] 當請求內容超過大小上限、不是合法 JSON、含不認得的欄位或解析失敗,收件口應回 4xx、不寫入收件表,也不因此結束行程。[test:test_malformed_or_oversized_bodies_are_rejected_without_writing_or_crashing]
- [S6] 若全域待處理數已達上限而請求會新增一個待處理提案,收件口應回 503 `inbox_full` 且不寫入。[test:test_a_full_inbox_answers_503_inbox_full_and_writes_nothing]
- [S7] 若資料庫忙碌到逾時,收件口應回 503 `busy`(與 `inbox_full` 不同)且不留下半筆寫入。[test:test_a_busy_database_answers_503_busy_and_leaves_nothing_behind]
- [S8] 收件口應只在寫入交易提交之後才回 2xx;在提交前中斷時呼叫者重送會被當作第一次接受,在提交後回應前中斷時重送會得到重送結果。[test:test_a_crash_before_commit_loses_nothing_and_a_crash_after_commit_is_answered_by_the_replay]
- [S9] 當兩個以上的請求同時送同一個收件鍵,收件口應恰好接受一個;內容相同的其餘請求得到重送結果,內容不同的得到 409。[test:test_concurrent_requests_for_the_same_key_accept_exactly_one]
- [S10] 當收件口收到的提案決策到期時間已過,收件口應回 422 `expired_proposal`、不寫入提案,只記事件。[test:test_an_already_expired_proposal_is_rejected_with_422_and_not_stored]
- [S11] 當待處理的提案之後到期,收件口應在下一個請求的交易內把它標為已過期,並釋放它佔的名額。[test:test_a_pending_proposal_that_expires_is_marked_expired_and_frees_its_slot]
- [S12] 收件口應拒絕 Host 標頭不是回送位址的請求、任何帶 Origin 標頭的請求(403)、Content-Type 不是 application/json 的請求(415),並在啟動時拒絕綁定非回送位址。[test:test_the_inbox_rejects_foreign_hosts_origins_content_types_and_non_loopback_binding]
- [S13] 收件口對呼叫者的錯誤回應應只含固定的錯誤代碼與必要的數字(例如目前最高修訂),不得回顯請求內容。[test:test_error_responses_never_echo_request_content]
- [S14] 事件紀錄應只存固定欄位、事件代碼是封閉列舉、筆數不超過上限,且事件寫入失敗時對呼叫者的回應不變。[test:test_the_event_log_is_bounded_fixed_shape_and_never_affects_the_response]
- [S15] 收件表與其中的提案應在收件行程重啟之後仍然存在,重啟後重送同一份提案得到重送結果。[test:test_proposals_survive_a_restart_and_a_resend_is_a_replay]
- [S16] 收件口收到請求時應使用增量 1 的 `parse_proposal` 作為唯一的欄位驗證:收件口模組不得自帶欄位驗證,且增量 1 解析器拒絕的每一份樣本(測試共用同一批樣本)收件口都得回 4xx。[test:test_every_sample_the_domain_parser_rejects_is_also_rejected_by_the_inbox_and_the_inbox_defines_no_validators]
- [S17] 內容雜湊應與時區表示法無關(同一時刻的不同時區寫法得到同一個雜湊),並且欄位順序、空白不同的等價 JSON 得到同一個雜湊。[test:test_the_content_hash_ignores_timezone_notation_and_json_formatting]
- [S18] 慢速連線與只送一半請求的連線應在逾時後被放棄,不佔住執行緒;逾時值沿用共用基礎的設定。[test:test_a_stalled_connection_is_dropped_after_the_socket_timeout]
- [S19] 抽出共用基礎之後,DSP 既有的伺服器測試應全部維持通過,行為不變。[test:test_the_dsp_server_behaviour_is_unchanged_after_extracting_the_shared_base]

### 不做的事(範圍)與已知限制

- 不執行提案、不呼叫 DSP、不做政策檢查與租戶相符檢查(租戶只是索引,相符與否是 Phase 3 執行前授權的事)。收件口只負責「安全地收、去重、記帳」。
- 不做身分認證:呼叫者是否可信是 Phase 3 的能力憑證要處理的事;收件口能防的只有格式、大小、重複、搶位、填滿與跨站請求。
- 不做全域請求速率限制:用不同任務編號輪流送新提案,仍能在「待處理上限加上到期時間」的範圍內持續佔滿名額(到期後會釋放)。已承認的風險。REVISIT:2026-10-20 Phase 3 開始前,決定要不要在收件口加請求速率限制,或把這件事交給 Phase 3 的政策層。
- 不做收件表的清理與保留期限:已被取代與已過期的提案會累積;每個任務的提案數受修訂連號與到期時間節制,但總量沒有上限。REVISIT:2026-10-20 決定保留期限與清理方式(可與 Phase 8 的死信處理一起做)。
- 不做交接之後的狀態(已交接、完成等),那是 Phase 3 執行行程定義的。
- 增量 1 的狀態機只描述分析行程這一側;收件表的「待處理、已被取代、已過期」是執行行程這一側的另一張小表,兩邊對應關係要在增量 3 定義,現在不宣稱一致。

## 增量 3 設計:分析行程的流程與檢查點(2026-09-22,第 2 版:已折入第 1 輪設計審)

PRIOR-ART: 最小解是「一支函式從頭跑到尾」,但那測不出「跑到一半當機、重啟後接著做」的安全語意,本專案要證明的正是這個。採用「一次只做一步、每步落地才推進狀態」的驅動函式,序號與並行檢查都在寫入交易內決定(不是收件口那種「查跟寫都不碰外部呼叫」的單一交易,詳見下方「並行與序號」);不引入工作流程引擎。
RETIRE-IF: 若 Phase 3、4 做完後,這裡的檢查點機制從沒真的擋到一次「重做已完成的外部副作用」,換成更簡單的整支函式重跑也不會有事,就該檢討是不是過度設計。

- 事故:分析行程在流程跑到一半時當機重啟,不確定卡在哪一步、外部呼叫(蒐證、送出提案)有沒有真的發生,於是把已完成的事重做一遍(重複送出、重複決策),或把已經作廢的判斷當成現在的事實繼續走。
- 範圍:只做流程與檢查點的機制;不做真的網路呼叫(蒐證與送出提案都是可替換介面,增量 4 才接真的 DSP 與收件口);不做真正的決策規則(介面先定,規則留給後面階段);不做多任務排程(一次推進一個任務,呼叫端決定何時對哪個任務呼叫)。
- 核心概念已在 2026-09-22 對話裡使用者答對並收到回饋的部分(見上方「核心概念」節),這裡不重複。

### 儲存:只增不改,而且刻意跟 DSP、收件口的做法不同

- `tasks` 歷史表:每次狀態推進都是新增一列,不更新既有列;主鍵是(任務編號, 序號),一個任務的「目前狀態」永遠是序號最大的那一列。欄位:任務編號、序號、狀態、廣告編號、提案快照(JSON,只有狀態為 PROPOSED 或之後的列才非空;沿用同一份直到 HANDED_OFF)、錯誤細節(只有狀態為 FAILED 的列才非空)、寫入時間。
- `evidence` 表存增量 1 的 `Evidence`,同樣只增不改,歸屬某個任務的某次蒐證。
- 兩者的寫入(還有狀態推進)在同一個 `BEGIN IMMEDIATE` 交易內一起提交,做法沿用 [[Systems/共用行程基礎]] 的 `immediate_transaction`。
- **為什麼不跟 DSP(現況表+歷史表分兩張)或收件口(現況列原地改狀態+另一張事件表)一樣**:這個增量存在的目的就是要讓「這一步的輸出已安全存好」變成可以憑一列資料直接回答的問題。原地改寫現況列做不到這件事——一旦允許 UPDATE,就一定要另外回答「這次 UPDATE 到底有沒有真的提交」,那正是這個增量想避免重新發明的問題。全部只增不改,讓「目前狀態」的定義只有一種讀法(序號最大的那一列),沒有「表面上的目前列」跟「還沒提交完的目前列」兩種可能。代價:長時間運作歷史列會累積,見下方「已知限制」。

### 驅動函式:一次只做一步

- `advance(task_id)`:讀這個任務目前(序號最大)的狀態列,依狀態呼叫對應的可替換介面,把結果連同新狀態列一起提交,回傳新狀態。呼叫端(增量 4)負責重複呼叫它,直到狀態不再變動或變成終點。
- 狀態變化一律透過增量 1 `task_state.transition()` 計算,不手刻新的轉換邏輯;非法轉換會讓 `advance()` 直接丟出 `IllegalTransition`,不會靜默寫出壞資料。
- 三個可替換介面(增量 3 用測試假物件,增量 4 接真的實作),比照收件口既有的「成功回傳值、預期內的失敗用型別化例外」風格(`src/rtb/executor/inbox_store.py` 的 `Accepted` 與 `InboxRejected` 子類別),不是自己另發明一套:
  - `EvidenceSource`:`(task) -> tuple[Evidence, ...]`,讀現況,不改任何狀態。這是純讀取,重試永遠安全:任何例外都當成暫時性的,不觸發 FAILED。
  - `Decide`:`(task, evidence) -> NoAction | ProposalDecision | NeedsFreshEvidence`,三選一的決策結果;`NeedsFreshEvidence` 是決策層自己判斷證據不夠新、要求重新蒐證的訊號,不是例外。這是對已經拿到手的證據做純計算,沒有外部狀態——如果它自己丟出例外(不是回傳上面三選一),代表輸入或決策邏輯本身有問題,用同一份證據重跑只會得到同樣的例外,所以不當成暫時性失敗,直接轉 FAILED。
  - `Submit`:`(proposal) -> Accepted`,對應收件口的成功結果(含是否為重送);`SubmitStale`(對應收件口的 409/422,提案已過期或被取代)與 `SubmitBusy`(對應收件口的 503,暫時性)是兩個型別化例外,成功以外的預期結果一律用例外表達,不夾在回傳值裡。**前提**(必須明講,不能只靠讀者從收件口的合約反推):`Submit` 的實作保證對同一份提案(同一個修訂序號、同一份內容)重複呼叫是安全的——這正是增量 2 收件口的核心合約(重送回目前狀態,不新增、不改動),所以 `Submit` 丟出「三個已定義結果以外」的例外(例如逾時、連線失敗)一樣視為暫時性、可以放心重試,不觸發 FAILED。

### 並行與序號

- 同一個 `task_id` 原則上一次只有一個呼叫端在呼叫 `advance()`(增量 4 的排程負責保證);但驅動函式自己也要防禦:寫入交易內重新核對「準備要接的那一列(序號 N)」是不是真的還是目前最新的一列,不是就中止、不寫入、回傳「這次沒有進展,稍後再試」,不會把兩個互相矛盾的分析結果都當成合法轉換寫進去。
- 新列的序號一律在寫入交易內用 `MAX(seq)+1` 決定,不在讀取當下先算好——序號的值本身就是「這次寫入有沒有搶到」的判斷依據。

### 每個狀態怎麼推進

- [S20] 當 `create_task` 被呼叫、這個任務編號完全沒有歷史列,`create_task` 應新增一列 RECEIVED,欄位含任務編號與帶入的廣告編號。[test:test_creating_a_new_task_id_writes_a_received_row]
- [S21] 當 `create_task` 被呼叫、這個任務編號已經有歷史列且廣告編號相同,`create_task` 應當作空操作,不新增任何列。[test:test_creating_the_same_task_id_twice_with_the_same_campaign_is_a_no_op]
- [S22] 當 `create_task` 被呼叫、這個任務編號已經有歷史列但帶的廣告編號不同,`create_task` 應丟出例外,不得靜默接受。[test:test_creating_a_task_id_again_with_a_different_campaign_id_is_an_error]
- [S23] 當 `advance()` 呼叫的任務編號完全沒有歷史列,`advance()` 應清楚地失敗,不得憑空生出 RECEIVED 狀態。[test:test_advancing_a_task_that_was_never_created_fails_loudly]
- [S24] 當任務狀態是 RECEIVED,`advance()` 應只新增一列 COLLECTING_EVIDENCE,不呼叫任何介面。[test:test_leaving_received_writes_a_checkpoint_before_any_collaborator_is_called]
- [S25] 當任務狀態是 COLLECTING_EVIDENCE、`EvidenceSource` 呼叫成功,`advance()` 應在同一個交易內把證據列與一列 ANALYZING 一起提交。[test:test_a_successful_evidence_fetch_commits_evidence_and_the_next_state_together]
- [S26] 當任務狀態是 COLLECTING_EVIDENCE、`EvidenceSource` 呼叫丟出任何例外,`advance()` 應不寫入任何東西,讓狀態留在 COLLECTING_EVIDENCE 以便之後重試。[test:test_a_failing_evidence_fetch_leaves_no_trace_and_the_task_stays_ready_to_retry]
- [S27] 當任務狀態是 ANALYZING、`Decide` 回傳 NoAction,`advance()` 應新增一列 NO_ACTION。[test:test_analyzing_with_no_action_ends_the_task]
- [S28] 當任務狀態是 ANALYZING、`Decide` 回傳 ProposalDecision,`advance()` 應在同一個交易內把提案快照與一列 PROPOSED 一起提交。[test:test_analyzing_with_a_proposal_decision_stores_the_snapshot_and_moves_to_proposed]
- [S29] 當任務狀態是 ANALYZING、`Decide` 回傳 NeedsFreshEvidence,`advance()` 應新增一列 COLLECTING_EVIDENCE。[test:test_analyzing_that_needs_fresh_evidence_goes_back_to_collecting_evidence]
- [S30] 當任務狀態是 ANALYZING、`Decide` 呼叫丟出任何例外,`advance()` 應新增一列 FAILED 並記下錯誤細節,不得重試同一份證據。[test:test_a_decide_exception_ends_the_task_as_failed_instead_of_retrying_forever]
- [S31] 當任務狀態是 PROPOSED,`advance()` 應把歷史列裡存的提案快照原封不動送給 `Submit`,不得另外組一份新的。[test:test_proposed_always_resubmits_the_stored_snapshot_never_a_freshly_built_one]
- [S32] 當 `Submit` 回傳 Accepted(不論是否為重送),`advance()` 應新增一列 HANDED_OFF。[test:test_accepted_or_replayed_both_hand_off]
- [S33] 當 `Submit` 丟出 `SubmitStale`,`advance()` 應新增一列 COLLECTING_EVIDENCE。[test:test_stale_goes_back_to_collecting_evidence]
- [S34] 當 `Submit` 丟出 `SubmitBusy`,`advance()` 應不寫入任何東西,讓狀態留在 PROPOSED。[test:test_busy_leaves_the_task_untouched_for_a_later_retry]
- [S35] 當 `Submit` 丟出前兩者以外的例外,`advance()` 應不寫入任何東西、視為可重試,讓狀態留在 PROPOSED(重送同一份提案永遠安全,是 `Submit` 介面的前提)。[test:test_an_unrecognised_submit_failure_is_treated_as_retryable_not_fatal]
- [S36] 當任務狀態是終點狀態或 HANDED_OFF,`advance()` 應是空操作,不呼叫任何介面。[test:test_advancing_a_terminal_or_handed_off_task_calls_no_collaborator]
- [S37] 當寫入交易內發現「準備要接的那一列」已經不是目前最新的一列,`advance()` 應中止、不寫入任何東西,回傳沒有進展。[test:test_two_concurrent_advance_calls_on_the_same_task_never_both_commit_conflicting_outcomes]
- [S38] 新列的序號應在寫入交易內用目前最大序號加一決定,不得在讀取當下先行決定。[test:test_the_next_sequence_number_is_decided_inside_the_write_transaction]
- [S39] 當任一步驟在提交前中斷後重新呼叫 `advance()`(交易的中斷鉤子語意與收件口的 `before_commit` 相同),結果應與沒有中斷時一致。[test:test_a_crash_before_commit_while_leaving_received_loses_nothing] [test:test_a_crash_before_commit_while_collecting_evidence_loses_nothing] [test:test_a_crash_before_commit_while_analyzing_loses_nothing] [test:test_a_crash_before_commit_while_proposed_loses_nothing](實作時拆成四個測試、每個狀態各一個,原本單一測試名沒有真的涵蓋每一步,見 2026-09-22 代碼審 s1f5/x1f5)
- [S40] 當任一步驟在提交後中斷後重新呼叫 `advance()`,`advance()` 應不重複呼叫已經成功的那一步、也不送出跟已存快照不同的內容。[test:test_resuming_after_a_crash_after_commit_never_repeats_or_diverges_from_the_committed_step]
- [S41] 歷史表應只增不改,不得出現 UPDATE 或 DELETE 敘述。[test:test_the_history_table_has_no_update_or_delete_statements]

### 不做的事(範圍)與已知限制

- 不做真的 HTTP 呼叫:`EvidenceSource`、`Submit` 在這個增量都是測試用的假物件;增量 4 接上真的 DSP 用戶端與收件口用戶端。
- 不做決策規則:`Decide` 的真正邏輯(什麼情況該改預算、改多少)留給後面階段;這個增量只定義三選一的介面形狀。
- 不做多任務排程:`advance()` 一次只推進一個任務;要不要輪詢多個待處理任務、多久輪詢一次,是增量 4 呼叫端的事;同一任務不能被並行呼叫也是排程端的責任,S37 只是最後一道防線,不是主要防護。
- 分析行程套件(`src/rtb/analyzer/`)不得匯入 `rtb.dsp` 或 `rtb.executor`,比照現有兩個行程互不匯入的規則,加對應的 ruff 設定。
- `tasks` 與 `evidence` 兩張表只增不改,長時間運作會無界成長(尤其 COLLECTING_EVIDENCE 到 ANALYZING 或 PROPOSED 之間反覆重新規劃的迴圈沒有次數上限);這跟 [[Systems/提案收件口]] 已經承認過的同類風險是同一件事,解法(清理、保留期限)留到那邊一起決定。
REVISIT:2026-10-20 與提案收件口的保留期限一起決定分析行程歷史表的清理方式。
- COMPLETED、BLOCKED、SUPERSEDED 三個終點不在這個增量寫入,留到 Phase 3 執行行程那一側定義;增量 3 的「不做的事」也沒有跟它們牴觸。

## 增量 4 設計:真的網路呼叫、最小決策規則與 trace(2026-09-22,第 2 版:已折入第 1 輪設計審)

PRIOR-ART: 最小解是把三個可替換介面直接接 urllib.request,不需要 HTTP client 框架;世界上這件事通常用重試庫加分散式追蹤系統,但本專案是單機 Demo,自建「只增不改的呼叫紀錄表」就夠當 trace 用,不需要 OpenTelemetry 這類工具。採用:標準函式庫 urllib.request,共用的「逾時、JSON 編解碼」邏輯抽成一個小模組(比照 httpkit.py 是共用的伺服器基礎,這是共用的用戶端基礎),trace 是既有 SQLite 表格模式的延伸,不引入新依賴。
RETIRE-IF: 若這個增量做完後,trace 表從沒被用來追查過一次真實的問題,而且維護它的成本大於好處,就該檢討是不是過度設計。

- 事故:增量 1~3 都是用測試假物件證明流程與檢查點正確;真的接上網路之後,新的風險是「以為蒐證蒐了一半也算數」「收件口的錯誤代碼被接錯」「不小心讓分析端連得到故障注入」「對方掛了永遠卡住」。這個增量把假物件換成真的用戶端,重跑增量 3 的合約(S20~S41 不變),並補上這幾類新風險的合約。
- 範圍:真的 DSP 用戶端(讀現況與指標)、真的收件口用戶端(送提案)、一個示範用的最小決策規則、把呼叫記錄成只增不改的 trace、補驗 Phase 1 留下的兩條邊界測試。
- 核心判斷已在 2026-09-22 對話裡使用者答對:兩種證據要嘛都讀到、要嘛整輪都不算數,不存半套。

### 核心概念延伸(使用者已在 2026-09-22 對話答對並收到回饋)

- `EvidenceSource` 的合約本來就是「回傳這一輪要用的全部證據,或整個失敗」(增量 3 的 S25/S26);多證據來源不是新規則,是同一個合約套用在兩次 HTTP 呼叫上——兩個端點都成功才回傳,任一個失敗就讓整個函式丟例外,由 `advance()` 既有的「純讀取,重試永遠安全」處理,不需要新的部分完成狀態。

### 共用的用戶端基礎(第 1 輪發現:兩個用戶端各自造輪子,而且都沒設逾時)

- 新增 `src/rtb/httpclient.py`(跟 `httpkit.py`、`sqlitekit.py` 同一層,是第三個共用的行程基礎模組):提供 `request_json(url, method, body, timeout_seconds, headers=None) -> tuple[int, dict]`,固定用 `urllib.request`、固定帶 `Content-Type: application/json`、固定 `timeout=timeout_seconds`(呼叫端必填,不給預設值——沒有「忘記設逾時」這個選項,因為函式簽章逼你填)。連線不重用(`urllib` 本來就是每次呼叫開一條,跟收件口用戶端這種低頻呼叫的場景相稱;不做連線池,避免過度設計)。
- `headers` 只接受一個**封閉的列舉**(不是任意字典):`class ClientHeader(StrEnum): IDEMPOTENCY_KEY = "Idempotency-Key"`(只給 DSP 用戶端用;之後如果真的需要更多,列舉再加,不開放任意標頭字串)。`request_json` 對傳入的 `headers` 一律用 `isinstance(name, ClientHeader)` 核對,不是 `ClientHeader` 成員就拒收——**這才是真正的保證**:不管呼叫端怎麼組出 `"X-Fault"` 這個字串(直接寫死、拼接、f-string),只要它不是這個封閉列舉的成員,`request_json` 根本不會把它放進請求裡。(第 2 輪發現:原本設計只講「原始碼掃描」,那只是子字串比對,能被拼接繞過,不是真正的防線;原始碼掃描改列為**輔助**訊號——留著是為了在程式審查時第一眼就看到有沒有人想加 `X-Fault`,不是安全機制本身。)
- 逾時值:`DSP_TIMEOUT_SECONDS = 5.0`、`INBOX_TIMEOUT_SECONDS = 5.0`(暫用值,比照 DSP 伺服器自己的 `SOCKET_TIMEOUT_SECONDS = 10.0` 抓一半)。

### 三個新模組,以及它們放在哪裡

- `src/rtb/analyzer/dsp_client.py`:真的 `EvidenceSource`。依序呼叫 `request_json` 打 `GET /campaigns/{id}`、`GET /campaigns/{id}/metrics?window=1h`;兩個都成功才把回應轉成 `Evidence`(CAMPAIGN_STATE、METRICS 各一筆)回傳;任一個失敗(逾時、連線失敗、4xx/5xx)整個函式往外丟例外,不吞、不回傳半套。這支檔只做「打 HTTP、轉成 Evidence」,不碰 `TaskStore`。
  - **content_hash 演算法**(第 2 輪發現:第 1 版折入時文字沒有真的寫進來):沿用增量 2 收件口內容雜湊已經定案的做法(`Proposal.to_primitives` 那一套「鍵排序、無多餘空白、不允許 NaN」的 JSON 正規化),對 DSP 回應本身直接套用同一種正規化再取 SHA-256——不是重新發明一套,是把既有規則套用到新的資料形狀上;現況(campaign state)雜湊涵蓋 `id`/`budget`/`status`/`version`,指標雜湊涵蓋查到的那個時間窗與其中的欄位。
- `src/rtb/analyzer/inbox_client.py`:真的 `Submit`。POST 到收件口的 `/proposals`;成功、`SubmitStale`、`SubmitBusy`、新增的 `SubmitRejectedPermanently`(見下)四種結果的對照見合約 S46a~S46c。同樣不碰 `TaskStore`。
- `src/rtb/analyzer/policy.py`:示範用的最小 `Decide`,規則見下方「決策規則」。
- `src/rtb/analyzer/instrumented.py`:**新增**(第 1 輪發現:tool_calls 誰寫、寫在哪沒交代)。提供 `InstrumentedEvidenceSource`、`InstrumentedSubmit` 兩個包裝類別,建構時吃一個原始的 `dsp_client`/`inbox_client` 函式與一個 `TaskStore`;每次呼叫內層函式,不論成功或丟例外都呼叫 `store.record_tool_call(...)`(S50),記完才把結果或例外原樣往外傳。`flow.advance()` 拿到的 `EvidenceSource`/`Submit` 一律是包裝過的版本,`flow.py` 本身不知道、也不需要知道 tool_calls 這件事——保持增量 3 的驅動函式不變。
- 圖譜落點:五支新檔案(dsp_client.py、inbox_client.py、policy.py、instrumented.py、httpclient.py)分兩篇:`src/rtb/httpclient.py` 併入既有的 [[Systems/共用行程基礎]](跟 httpkit.py、sqlitekit.py 同一篇管);其餘四支併入既有的 [[Systems/分析行程流程與檢查點]](這篇的 about_code 本次增列)。不新開節點。

### 決策規則(policy.py)

- 用增量 1 `src/rtb/domain/metrics.py` 既有的 `MetricResult`/`Reason` 設計判斷「有沒有值」,不自己重寫一套缺值判斷:配速 = 指標證據的花費 ÷ (現況證據的預算 ÷ 24);預算為 0、花費缺值、或曝光/點擊缺值,`below()` 一律回 `None`(不知道),對應到 `NoAction`,不丟例外。
- 配速 `below(0.5)` 為 `True`(明顯偏低,暫用門檻)、且曝光與點擊都大於 0(真的有在投放)才提案調高預算(固定漲一成,暫用值);其餘(含配速正常、配速偏低但沒有投放、任何一項不知道)一律 `NoAction`。

### 事件與 trace

- 只增不改的 `tool_calls` 表(`TaskStore` 新增 `record_tool_call(task_id, task_seq, endpoint, outcome, latency_ms, now)`),每次對外呼叫一筆:呼叫哪個端點、結果(狀態碼或例外類型名稱,不存回應內容或例外訊息全文——避免意外存進敏感資訊,這是第 1 輪資安鏡頭的提醒)、耗時、時間戳。這筆寫入不跟狀態推進同一個交易,而且**自己絕不讓例外往外傳**(比照收件口事件表「寫入失敗不影響回應」的既有做法):寫失敗就放棄這筆記錄,不能因為記錄失敗而讓整個檢查點卡住。
  - **task_seq 語意**(第 2 輪發現:原本沒定義):跟 `evidence` 表的 `task_seq` 是同一件事——`instrumented.py` 的包裝層呼叫內層函式之前,先從傳入的 `TaskRow`(`EvidenceSource`/`Decide`/`Submit` 都收得到目前這一列)讀出 `row.seq`,原封不動當成 `task_seq` 寫入;也就是「呼叫發生在哪一列的檢查點期間」,不是呼叫完成後才推算的新序號。這樣 `trace_for` 把 `tool_calls` 跟 `evidence` 用同一個 `task_seq` 對起來,兩張表的關聯方式完全一致,不是各自定義一套。
- `trace_for(store, task_id) -> TraceRecord`(**新增具體介面**,第 1 輪發現 S51 原本沒有介面設計):`analyzer/task_store.py` 新增這支函式,回傳一個 dataclass,欄位是這個任務的完整歷史列(`tasks`)、每一步的證據(`evidence`)、每一次呼叫記錄(`tool_calls`),三者都用任務編號查、依序號或時間排序。政策版本從歷史列裡的提案快照(`proposal.policy_version`)取得,不用另外存。

### 收件口拒絕代碼的分類(第 1 輪發現:too_many_revisions 被誤吸進 SubmitStale,會卡進無限重試)

- `revision_out_of_order`、`content_conflict`、`expired_proposal`、`expiry_too_far`、`created_in_future`:轉 `SubmitStale`(退回蒐證、送下一個修訂能解決,重試有意義)。
- `too_many_revisions`:轉**新的例外型別 `SubmitRejectedPermanently`**(不是 `SubmitStale`)。這個任務已經碰到修訂數上限,退回蒐證再送下一個修訂只會再次碰到同一個上限,重試不會解決——`flow.py` 的 `_from_proposed` 對這個新例外的處理是新增一列 FAILED(比照 `_from_analyzing` 對 `Decide` 自己丟例外的既有做法,同一種「重試不會變好,直接停」的邏輯延伸到 `Submit` 這邊)。這是這個增量唯一需要修改增量 3 既有程式碼(`flow.py`)的地方,原因寫清楚,不是悄悄擴權。
  - **定義在哪一層**(第 2 輪發現:原文兩處交代不一致):`SubmitRejectedPermanently` 跟 `SubmitStale`、`SubmitBusy` 一樣,定義在 `flow.py`(它們都是 `Submit` 這個介面自己的例外詞彙表,屬於介面定義的一部分);`inbox_client.py` 只是匯入並在對照到 `too_many_revisions` 時丟出它,不會反過來讓 `flow.py` 依賴 `inbox_client.py` 的實作。
- `inbox_full`、`busy`:轉 `SubmitBusy`(暫時性)。
- 其他狀態碼或連線失敗:原樣往外丟,由既有的「未定義例外視為可重試」接住。

### 故障注入的對稱保護(第 1 輪發現:S44/S52 只替 DSP 寫了,收件口沒有)

- 收件口(增量 2)一樣支援 `X-Fault`(`crash_before_commit`/`crash_after_commit`),分析端的 `inbox_client.py` 同樣不得有任何送出這個標頭的路徑,適用跟 dsp_client 一樣的機械掃描與「沒有公開介面」設計(見上面「共用的用戶端基礎」)。

### 合約

- [S42] 當 DSP 的現況與指標兩個請求都成功,`dsp_client` 應回傳兩筆證據。[test:test_both_endpoints_succeeding_returns_both_pieces_of_evidence]
- [S43] 當 DSP 的現況或指標任一個請求失敗,`dsp_client` 應整個丟出例外,不回傳只含一部分的結果。[test:test_a_nonexistent_campaign_makes_the_whole_call_raise] [test:test_the_dsp_being_unreachable_makes_the_whole_call_raise] [test:test_campaign_state_succeeding_but_metrics_failing_raises_and_returns_nothing_partial](第 1 輪代碼審指出,原本沒有測試覆蓋「現況成功、指標失敗」這個排列組合,已補上第三個測試)
- [S44] `dsp_client.py`、`inbox_client.py`、`httpclient.py` 三支檔的原始碼都不應該出現 `X-Fault` 字樣,`request_json` 的簽章也不應該有能傳入任意標頭名稱的參數。[test:test_the_dsp_client_module_never_mentions_the_fault_header] [test:test_the_inbox_client_module_never_mentions_the_fault_header] [test:test_the_client_module_source_never_mentions_the_fault_header] [test:test_a_header_key_that_is_not_a_clientheader_member_is_rejected]
- [S45] 當收件口回應 201 或 200,`inbox_client` 應回傳 `Accepted`,`replayed` 對應狀態碼是否為 200。[test:test_first_acceptance_maps_to_accepted_not_replayed] [test:test_a_resend_maps_to_accepted_replayed]
- [S46] 當收件口回應 409(`revision_out_of_order`、`content_conflict`)或 422(`expired_proposal`、`expiry_too_far`、`created_in_future`),`inbox_client` 應丟出 `SubmitStale`。[test:test_a_content_conflict_maps_to_submit_stale] [test:test_an_expired_proposal_maps_to_submit_stale] [test:test_an_expiry_too_far_in_the_future_maps_to_submit_stale] [test:test_a_decision_created_too_far_in_the_future_maps_to_submit_stale]
- [S46a] 當收件口回應 409 且錯誤代碼是 `too_many_revisions`,`inbox_client` 應丟出 `SubmitRejectedPermanently`,不是 `SubmitStale`。[test:test_too_many_revisions_maps_to_a_permanent_rejection_not_stale]
- [S46b] 當 `_from_proposed` 收到 `SubmitRejectedPermanently`,`advance()` 應新增一列 FAILED,不得退回 COLLECTING_EVIDENCE。[test:test_a_permanent_submit_rejection_fails_the_task_instead_of_looping_forever]
- [S47] 當收件口回應 503,`inbox_client` 應丟出 `SubmitBusy`。[test:test_a_full_inbox_maps_to_submit_busy]
- [S48] 當收件口回應其他狀態碼或連線失敗,`inbox_client` 應讓例外原樣往外傳,不得自行吞掉或改分類。[test:test_an_unreachable_inbox_propagates_unmapped]
- [S49] 當配速已知且明顯偏低、曝光與點擊都大於零,`policy.decide` 應回傳調高預算的 `ProposalDecision`;當配速正常、沒有投放、或任一數值不知道,應回傳 `NoAction`,不得丟出例外。[test:test_underpacing_with_real_delivery_proposes_a_budget_increase] [test:test_normal_pacing_is_no_action] [test:test_underpacing_with_zero_delivery_is_no_action_not_a_proposal] [test:test_zero_budget_never_raises_and_is_no_action] [test:test_missing_metrics_never_raises_and_is_no_action]
- [S50] 每次對外呼叫(不論成功或失敗)都應該在 `tool_calls` 留下一筆紀錄;這筆寫入本身失敗不應該影響呼叫端拿到的結果。[test:test_a_successful_call_is_recorded] [test:test_a_failing_call_is_recorded_and_the_exception_still_propagates] [test:test_a_failing_tool_call_write_does_not_affect_the_wrapped_calls_own_result] [test:test_submit_calls_are_recorded_too] [test:test_a_failing_submit_call_is_recorded_and_the_exception_still_propagates]
- [S51] `trace_for` 應該回傳一個包含這個任務的狀態史、每一步證據與每一次呼叫記錄的結構化紀錄。[test:test_trace_for_returns_the_full_history_evidence_and_calls_for_a_task]
- [S52] 當 DSP 與收件口都沒有帶 `--fault-injection` 啟動,分析行程用真的用戶端跑完整套正常流程應該全部成功,且過程中沒有任何一次請求帶 `X-Fault`。[test:test_the_agent_cannot_reach_fault_injection_on_production_style_servers]
- [S53] 分析行程套件不應該匯入 `rtb.dsp` 的任何內部模組(機械掃描,只能透過 HTTP 溝通)。[test:test_the_analyzer_package_never_imports_dsp_internals]
- [S54] 當 `request_json` 被呼叫,呼叫端應明確提供逾時秒數(沒有預設值),請求應在逾時後真的放棄,不會無限期卡住。[test:test_request_json_has_no_default_timeout] [test:test_a_request_that_never_responds_gives_up_after_the_timeout]

### 不做的事(範圍)

- 不做真正的業務決策規則:`policy.py` 是示範用的最小規則,交接文件後面階段的真正邏輯不在這裡做。
- 不做多任務併發呼叫排程:一次處理一個任務,跟增量 3 一樣。
- 不做連線池或連線重用:每次呼叫各自開關,量體小,不值得為此增加複雜度。
- 不做 Phase 3 執行面的任何東西:HANDED_OFF 之後的狀態不歸這個增量管。
- 不重寫增量 3 的合約(S20~S41):三個可替換介面的形狀不變,只新增 `SubmitRejectedPermanently` 這一個例外型別與 `_from_proposed` 對它的一個新分支,原因見上方「收件口拒絕代碼的分類」。

## 回退


若收件口設計在審查或實作中被證明不成立,可整個移除收件口與收件表而不影響增量 1 的領域模型與 Phase 1 的 DSP,因為這個增量只新增檔案、沒有修改既有介面;回退就是刪掉新增的模組與測試,計劃改回「增量 2 未做」。

若增量 3 的檢查點設計在審查或實作中被證明不成立,同樣只新增了 `src/rtb/analyzer/` 這個全新套件,沒有修改增量 1、2 的任何既有介面;回退就是刪掉這個套件與測試,計劃改回「增量 3 未做」。

## 設計審的安排

- 增量 1(純程式、沒有副作用、風險低)先做,不先過設計審;狀態機與證據格式會在設計審前先定型,審查員若要求改,代價由我承擔。
- 增量 2 動手前,把「提案通道」的設計補進這份計劃並過設計審。REVISIT:2026-10-05 若增量 2 尚未開始,回頭確認這個安排是否還適用。

## 實務隱患

- 已排除:金流:整個專案只有本機的 Mock DSP,預算數字都是模擬值。
- 已排除:對外送出:Phase 2 的四個增量都不呼叫模型 API,也不連外部網路;分析行程接模型是後面階段的事,屆時另開設計審。
- 已排除:不可逆:這一階段沒有資料搬移或刪除;任務狀態只增不改的資料表在增量 3 才出現,且沒有外部副作用。
- 未排除:守衛面:增量 2 的提案通道與提案的嚴格解析,是「不可信內容進入有寫入能力的行程」的入口,屬守衛面。這個增量動手前必須把通道設計補進本計劃並過設計審;增量 1 的解析只是純函式,不構成授權決定。
- 殘留風險:狀態機與證據格式在設計審前先定型,審查員可能要求修改,已在「設計審的安排」承認。

## 落點

- lands_in: 增量 1 新開一篇 Systems 節點「任務流程領域模型」,管任務狀態、證據、提案三個模組。
- lands_in: 增量 3 新開一篇 Systems 節點「分析行程流程與檢查點」,管 `src/rtb/analyzer/` 這個套件(tasks 歷史表、evidence 表、`advance()` 驅動函式、三個可替換介面)。

## 審計修正紀錄

- r1(2026-09-22,6 席:正確性、併發與資源、安全、合約一致、可測性、架構對齊;沒派外家席):34 條/blocking 22(major 22、minor 12)/全部折入,第 2 版設計重寫了收件口:嚴格連號、單一交易的判斷順序、重送不受上限影響、取代先釋放名額、事件表有界、Origin 與 Content-Type 防護、共用基礎抽出、故障注入點。指標:governance/review-reports/rtb-phase2任務流程/。
- 使用者裁定的部分沒動:相同內容當作重送、不同內容拒收(2026-09-22)。其餘全部是我依審查結果做的設計選擇,例如預設全域上限 8、事件上限 1000、把「新提案必須連號」定為規則;這幾個數字沒有實測依據,是暫用值,實作時可調。
- r2(2026-09-22,3 席:正確性與可測性、併發與崩潰恢復、架構對齊;沒派外家席):8 條/blocking 4(major 4、minor 4)/全部折入,第 2 版設計補上:FAILED 狀態的觸發條件(Decide 丟例外)、並行呼叫的防禦(交易內重核對最新列、序號在交易內決定)、Submit 改成比照收件口的「成功回傳值、預期失敗用型別化例外」風格並明講重送安全是它的前提、tasks 表欄位、create_task 主線的條款、故障注入鉤子語意、無界成長的 REVISIT、lands_in。架構對齊席指出 tasks 表只增不改跟 DSP、收件口的做法不同,已在設計裡補上明確理由(不是照建議改成一致,是解釋為什麼刻意不同),屬合法的張力,不是壓掉。指標:governance/review-reports/rtb-phase2任務流程/。
- 使用者這輪沒有新的裁定,全部是我依審查結果做的設計選擇。
