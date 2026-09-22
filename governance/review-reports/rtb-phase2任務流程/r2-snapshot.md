---
type: project
status: doing
created: 2026-09-22
updated: 2026-09-22
tags:
  - type/project
  - status/doing
lands_in:
  - Systems/任務流程領域模型
  - Systems/共用行程基礎
  - Systems/提案收件口
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

- 合法與非法的狀態轉換都有測試,包含終點狀態沒有出路。
- 新鮮度:過期、版本已變、剛好在邊界、時鐘倒退、沒有時區的時間,各有測試。
- 提案解析:合法、缺欄位、多餘欄位、型別錯、過大、過期時間早於建立時間,各有測試。
- 提案與副作用明確分離:提案物件沒有任何執行能力,只是資料。
- 使用者能用自己的話說明狀態與檢查點的差別:已在 2026-09-22 對話完成。
- 增量 4 才會有「能由任務走到驗證完成」與「一條 trace 連結證據、決策、政策、工具呼叫與驗證」,這兩條在那之前不宣稱完成。

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

## 增量 3 設計:分析行程的流程與檢查點(2026-09-22)

PRIOR-ART: 最小解是「一支函式從頭跑到尾」,但那測不出「跑到一半當機、重啟後接著做」的安全語意,本專案要證明的正是這個。採用「一次只做一步、每步落地才推進狀態」的驅動函式(同一種做法已經在 DSP 與收件口用過:單一交易內先查後寫);不引入工作流程引擎。
RETIRE-IF: 若 Phase 3、4 做完後,這裡的檢查點機制從沒真的擋到一次「重做已完成的外部副作用」,換成更簡單的整支函式重跑也不會有事,就該檢討是不是過度設計。

- 事故:分析行程在流程跑到一半時當機重啟,不確定卡在哪一步、外部呼叫(蒐證、送出提案)有沒有真的發生,於是把已完成的事重做一遍(重複送出、重複決策),或把已經作廢的判斷當成現在的事實繼續走。
- 範圍:只做流程與檢查點的機制;不做真的網路呼叫(蒐證與送出提案都是可替換介面,增量 4 才接真的 DSP 與收件口);不做真正的決策規則(介面先定,規則留給後面階段);不做多任務排程(一次推進一個任務,呼叫端決定何時對哪個任務呼叫)。
- 核心概念已在 2026-09-22 對話裡使用者答對並收到回饋的部分(見上方「核心概念」節),這裡不重複。

### 儲存:只增不改

- `tasks` 歷史表:每次狀態推進都是新增一列,不更新既有列;主鍵是(任務編號, 序號),一個任務的「目前狀態」永遠是序號最大的那一列。這樣「這一步的輸出已安全存好」跟「這一列真的寫進資料庫」是同一件事,不需要另外的「已完成」旗標。
- `evidence` 表存增量 1 的 `Evidence`,同樣只增不改,歸屬某個任務的某次蒐證。
- 兩者的寫入(還有狀態推進)在同一個 `BEGIN IMMEDIATE` 交易內一起提交,做法沿用 [[Systems/共用行程基礎]] 的 `immediate_transaction`。

### 驅動函式:一次只做一步

- `advance(task_id)`:讀這個任務目前(序號最大)的狀態列,依狀態呼叫對應的可替換介面,把結果連同新狀態列一起提交,回傳新狀態。呼叫端(增量 4)負責重複呼叫它,直到狀態不再變動或變成終點。
- 狀態變化一律透過增量 1 `task_state.transition()` 計算,不手刻新的轉換邏輯;非法轉換會讓 `advance()` 直接丟出 `IllegalTransition`,不會靜默寫出壞資料。
- 三個可替換介面(增量 3 用測試假物件,增量 4 接真的實作):
  - `EvidenceSource`:`(task) -> tuple[Evidence, ...]`,讀現況,不改任何狀態。
  - `Decide`:`(task, evidence) -> NoAction | ProposalDecision | NeedsFreshEvidence`,三選一的決策結果;`NeedsFreshEvidence` 是決策層自己判斷證據不夠新、要求重新蒐證的訊號,不是例外。
  - `Submit`:`(proposal) -> Accepted | Stale | Busy`,對應收件口的三類結果:`Accepted` 含「是否為重送」;`Stale` 表示這份提案已經過期或被取代(收件口的 409/422);`Busy` 表示暫時性、可重試,不代表提案本身有問題。

### 每個狀態怎麼推進

- [S20] 當 `create_task` 被呼叫、這個任務編號已經有歷史列,驅動函式應當作空操作,不新增任何列。[test:test_creating_the_same_task_id_twice_is_a_no_op]
- [S21] 當 `create_task` 被呼叫、帶的廣告編號與既有歷史不同,驅動函式應丟出例外,不得靜默接受。[test:test_creating_a_task_id_again_with_a_different_campaign_id_is_an_error]
- [S22] 當 `advance()` 呼叫的任務編號完全沒有歷史列,`advance()` 應清楚地失敗,不得憑空生出 RECEIVED 狀態。[test:test_advancing_a_task_that_was_never_created_fails_loudly]
- [S23] 當任務狀態是 RECEIVED,`advance()` 應只新增一列 COLLECTING_EVIDENCE,不呼叫任何介面。[test:test_leaving_received_writes_a_checkpoint_before_any_collaborator_is_called]
- [S24] 當任務狀態是 COLLECTING_EVIDENCE、`EvidenceSource` 呼叫成功,`advance()` 應在同一個交易內把證據列與一列 ANALYZING 一起提交。[test:test_a_successful_evidence_fetch_commits_evidence_and_the_next_state_together]
- [S25] 當任務狀態是 COLLECTING_EVIDENCE、`EvidenceSource` 呼叫失敗,`advance()` 應不寫入任何東西,讓狀態留在 COLLECTING_EVIDENCE。[test:test_a_failing_evidence_fetch_leaves_no_trace_and_the_task_stays_ready_to_retry]
- [S26] 當任務狀態是 ANALYZING、`Decide` 回傳 NoAction,`advance()` 應新增一列 NO_ACTION。[test:test_analyzing_with_no_action_ends_the_task]
- [S27] 當任務狀態是 ANALYZING、`Decide` 回傳 ProposalDecision,`advance()` 應在同一個交易內把提案快照與一列 PROPOSED 一起提交。[test:test_analyzing_with_a_proposal_decision_stores_the_snapshot_and_moves_to_proposed]
- [S28] 當任務狀態是 ANALYZING、`Decide` 回傳 NeedsFreshEvidence,`advance()` 應新增一列 COLLECTING_EVIDENCE。[test:test_analyzing_that_needs_fresh_evidence_goes_back_to_collecting_evidence]
- [S29] 當任務狀態是 PROPOSED,`advance()` 應重送歷史列裡存的提案快照給 `Submit`,不得另外組一份新的。[test:test_proposed_always_resubmits_the_stored_snapshot_never_a_freshly_built_one]
- [S30] 當 `Submit` 回傳 Accepted(不論是否為重送),`advance()` 應新增一列 HANDED_OFF。[test:test_accepted_or_replayed_both_hand_off]
- [S31] 當 `Submit` 回傳 Stale,`advance()` 應新增一列 COLLECTING_EVIDENCE。[test:test_stale_goes_back_to_collecting_evidence]
- [S32] 當 `Submit` 回傳 Busy,`advance()` 應不寫入任何東西,讓狀態留在 PROPOSED。[test:test_busy_leaves_the_task_untouched_for_a_later_retry]
- [S33] 當任務狀態是終點狀態或 HANDED_OFF,`advance()` 應是空操作,不呼叫任何介面。[test:test_advancing_a_terminal_or_handed_off_task_calls_no_collaborator]
- [S34] 當任一步驟在提交前中斷後重新呼叫 `advance()`,結果應與沒有中斷時一致。[test:test_resuming_after_a_crash_before_commit_at_every_step_converges_to_the_uninterrupted_outcome]
- [S35] 當任一步驟在提交後中斷後重新呼叫 `advance()`,`advance()` 應不重複呼叫已經成功的那一步、也不送出跟已存快照不同的內容。[test:test_resuming_after_a_crash_after_commit_never_repeats_or_diverges_from_the_committed_step]
- [S36] 歷史表應只增不改,不得出現 UPDATE 或 DELETE 敘述。[test:test_the_history_table_has_no_update_or_delete_statements]

### 不做的事(範圍)

- 不做真的 HTTP 呼叫:`EvidenceSource`、`Submit` 在這個增量都是測試用的假物件;增量 4 接上真的 DSP 用戶端與收件口用戶端。
- 不做決策規則:`Decide` 的真正邏輯(什麼情況該改預算、改多少)留給後面階段;這個增量只定義三選一的介面形狀。
- 不做多任務排程:`advance()` 一次只推進一個任務;要不要輪詢多個待處理任務、多久輪詢一次,是增量 4 呼叫端的事。
- 分析行程套件(`src/rtb/analyzer/`)不得匯入 `rtb.dsp` 或 `rtb.executor`,比照現有兩個行程互不匯入的規則,加對應的 ruff 設定。

## 回退

若收件口設計在審查或實作中被證明不成立,可整個移除收件口與收件表而不影響增量 1 的領域模型與 Phase 1 的 DSP,因為這個增量只新增檔案、沒有修改既有介面;回退就是刪掉新增的模組與測試,計劃改回「增量 2 未做」。

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

## 審計修正紀錄

- r1(2026-09-22,6 席:正確性、併發與資源、安全、合約一致、可測性、架構對齊;沒派外家席):34 條/blocking 22(major 22、minor 12)/全部折入,第 2 版設計重寫了收件口:嚴格連號、單一交易的判斷順序、重送不受上限影響、取代先釋放名額、事件表有界、Origin 與 Content-Type 防護、共用基礎抽出、故障注入點。指標:governance/review-reports/rtb-phase2任務流程/。
- 使用者裁定的部分沒動:相同內容當作重送、不同內容拒收(2026-09-22)。其餘全部是我依審查結果做的設計選擇,例如預設全域上限 8、事件上限 1000、把「新提案必須連號」定為規則;這幾個數字沒有實測依據,是暫用值,實作時可調。
