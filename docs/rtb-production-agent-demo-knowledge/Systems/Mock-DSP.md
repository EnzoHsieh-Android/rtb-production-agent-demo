---
type: system
status: doing
created: 2026-09-21
updated: 2026-10-03
responsibility: 負責 Mock DSP 的廣告狀態、版本、操作歷史與冪等紀錄的儲存與原子提交、型別化錯誤、逐日成效與過去調整(含補值表)的存取與唯讀端點、展示種子、要稽核金鑰的列操作唯讀端點,以及獨立行程的 HTTP 介面與故障注入;不負責比率等指標計算與 agent 端的任何邏輯。
aliases: []
about_code:
  - src/rtb/dsp/store.py
  - src/rtb/dsp/errors.py
  - src/rtb/dsp/server.py
  - src/rtb/dsp/capability.py
  - tests/executor/fakes.py
  - src/rtb/dsp/seed.py
  - tests/dsp/test_investigation_data.py
  - tests/dsp/test_store.py
  - tests/dsp/test_server.py
tags:
  - type/system
  - status/doing
summary: |-
  WHY:[2026-09-24 Phase 12 代碼審 r1 a3、s1] 參數表抽成 `build_parser()`、兩把金鑰的讀法抽成 `read_keys()`(稽核金鑰超過上限照舊以設定錯誤結束):展示的故障 DSP 用同一份,不再另寫一份少兩個參數的子集。命令列參數不收縮寫(allow_abbrev=False):一鍵展示的故障啟動器用正式入口同一支 parser 的解析結果核對目標路徑,縮寫與等號寫法都不能繞過(Phase 12 代碼審 r1 s1/l2/x2)。長選項一律寫全。出處:[[Projects/RTB_Phase12一鍵展示與HTML報告_計劃]]。
  RULE: 所有寫入先驗寫入能力憑證,順序固定:金鑰已設定 → 標頭存在 → 格式與簽章 → 聲明欄位與型別 → 時間窗 → 讀本文(白名單外的欄位拒收)→ 驗操作內容(不合法的值照舊 422)→ 範圍(廣告、動作、冪等鍵、租戶、確切新預算、預期版本都要等於聲明,廣告不存在也算範圍不符)。每一步失敗都在任何寫入之前,三張表都不動;拒收代碼沿用錯誤對照表,都不可重試,只回固定代碼不回顯聲明。見 [[Systems/寫入能力憑證]]。[since:2026-09-22] [retire:換成真實 DSP 時撤除]
  PITFALL: 代碼審第 1 輪指出:驗證函式若把「讀憑證標頭」當參數傳入,Python 會在函式內檢查金鑰之前就先讀標頭,沒金鑰又重複帶標頭時回的是「標頭重複」而不是「沒有可用金鑰」;現在標頭改成金鑰檢查之後才讀。政策版本原本驗完就丟,「只記不驗」沒有落實,現在寫進操作紀錄(舊操作留空值)。第 2 輪再補:時鐘也改成金鑰檢查之後才讀(時鐘出錯也蓋不掉沒有金鑰);政策版本 DSP 自己再擋一次格式(1 到 64 個英數與 . _ : -)。同一把鍵重放時操作紀錄只留第一次套用時的政策版本,這是刻意的:紀錄的是「這筆變更實際在哪個政策版本下套用」,重放沒有再套用一次。防回歸:[test:test_without_a_key_duplicate_capability_headers_still_answer_not_configured]、[test:test_the_policy_version_of_an_applied_write_is_recorded]、[test:test_the_key_is_checked_before_the_clock_is_read]、[test:test_every_earlier_check_answers_before_the_clock_is_read](有金鑰時標頭、格式、聲明的問題都在讀時鐘前回報)、[test:test_a_policy_version_outside_the_dsp_format_is_invalid]。
  RULE: 廣告的租戶只在建檔時設定,沒有任何寫入端點能改(有測試從路由表列舉寫入端點);舊資料庫沿用補欄位做法,舊廣告屬於預設租戶,舊操作的政策版本留空值。只有啟動程式讀金鑰環境變數,伺服器物件收參數。[since:2026-09-22] [retire:需要支援改租戶時,改租戶必須推進廣告版本並重審]
  WHY:[2026-09-23] 事故 F5(不可信文字不能擴權)要有真的攻擊面可測:交接文件第 12 節點名廣告名稱與素材文字,使用者裁定只加廣告名稱(素材文字走同一條路,落地頁網址會引出「會不會去抓網址」的新問題)。出處:[[Projects/RTB_Phase7提示注入與信任邊界_計劃]] 增量 2。
  RULE: 廣告多一個名稱欄位(不可信文字),只在建檔時設定,沒有任何寫入端點能改(比照租戶;有測試列舉路由表的寫入端點,並掃儲存層原始碼確認改廣告的敘述不碰名稱——這才是防護,算下一個狀態時照抄名稱只是因為型別必填);查廣告回傳名稱;改預算、暫停只改預算、狀態、版本。建檔名稱只收字串、不過濾內容,上限 4096 字元,超過、不是字串、或含孤立代理字元(SQLite 只收合法 UTF-8)一律丟 `ValidationRejected`;最壞情況(每字都要代理對、回應用 ASCII 逃脫每字 12 位元組)4096 字的查詢回應約 48 KB,低於分析端 64 KB 上限。舊資料庫照補欄位做法補名稱,舊廣告為空字串。防回歸:[test:test_an_old_dsp_database_gains_the_campaign_name_column]、[test:test_the_dsp_caps_campaign_names_below_the_response_limit]、[test:test_writes_keep_the_campaign_name_and_no_route_changes_it]。[since:2026-09-23] [retire:需要支援改名稱時,改名稱必須推進廣告版本並重審;換真的 DSP 時核對它的名稱上限]
  WHY: DSP 是獨立的外部事實來源,儲存與 agent 完全分開,agent 只能走它的公開介面;這樣「DSP 已提交但 agent 不知道」才測得出來。出處:[[RTB_Agent_Phase0架構]] 決策 d2。
  WHY: 一次操作的狀態變更、操作歷史、冪等紀錄放在同一個交易裡一起提交或回滾,避免「狀態已改但冪等鍵沒記」讓重送雙寫。出處:[[RTB_Agent_Phase0架構]] 的外部寫入失敗語意一節。
  PITFALL: 寫入交易若用普通 BEGIN(延遲交易),並行同鍵請求在 WAL 下會直接回 database is locked,busy_timeout 不會等;必須 BEGIN IMMEDIATE。防回歸:[test:test_concurrent_requests_with_the_same_key_apply_exactly_once],改成 BEGIN 會紅(2026-09-21 變異檢查實測)。
  PITFALL: 故障注入的「提交前逾時」若處理程式睡醒後繼續往下走,會在客戶端離開之後才提交,變成提交後逾時;必須睡醒後明確不提交。防回歸:[test:test_timeout_before_commit_client_sees_timeout_and_dsp_never_commits_later],測試必須等過處理程式睡醒的時間才檢查狀態(2026-09-21 變異檢查實測:太早檢查會讓這個錯誤存活)。
  PITFALL: 驗證「慢請求進行中查詢仍即時回應」時,必須確保慢請求真的已進入 DSP,否則查詢可能搶先被處理而讓單執行緒伺服器也通過。防回歸:[test:test_query_is_answered_immediately_while_a_slow_request_is_in_flight](2026-09-21 變異檢查實測)。
  PITFALL: 沒預期到的例外若讓連線被無聲切斷,呼叫端分不出「永久錯誤」與「提交前逾時」,會拿同一把鍵無限重送;所有沒預期的例外都必須回 500 JSON 並記到標準錯誤。防回歸:[test:test_unexpected_internal_failure_returns_a_500_json_instead_of_dropping_the_connection](2026-09-21 代碼審四席獨立指出,超大預算、壞的 Content-Length、超長數字都會觸發)。
  PITFALL: expected_version 若不驗型別,布林 true 會被 Python 當成版本 1 而繞過樂觀鎖。防回歸:[test:test_expected_version_must_be_a_positive_plain_integer](2026-09-21 代碼審實測)。
  PITFALL: 冪等指紋含 expected_version,所以重送必須沿用原提案的 expected_version;換了版本重送會得到 422 而不是重放。防回歸:[test:test_same_key_different_payload_is_rejected_and_changes_nothing]。
  PITFALL: 冪等鍵正則若用 match 加 $,結尾換行會通過而形成另一把鍵;必須用 fullmatch。防回歸:[test:test_idempotency_key_must_be_short_plain_ascii](2026-09-21 第二輪代碼審實測)。
  PITFALL: SQLite 的 sqlite_errorcode 在新版回傳擴充碼(例如 BUSY_SNAPSHOT 為 517),直接比對主碼會漏判;要取低 8 位。防回歸:[test:test_extended_busy_error_codes_are_still_recognised_as_retryable_store_busy]。
  RULE: Host 標頭比對不分大小寫;缺 Host 只有 HTTP/1.0 請求放行(舊式探針),因為瀏覽器一定會送 Host,DNS rebinding 的攻擊從瀏覽器來。[since:2026-09-21] [retire:DSP 不再只綁本機回送位址時重審]
  RULE: 對外的錯誤回應一律是型別化 JSON(含 retryable),包含基底類別產生的 404、501 與畸形請求;不准有無聲斷線。[since:2026-09-21] [retire:DSP 改由框架提供統一錯誤處理時撤除]
  TEST: tests/dsp/test_store.py 與 tests/dsp/test_server.py 涵蓋同鍵只套用一次(含 20 個並行、同鍵不同內容並行、以及故意沒有保護的對照實作會雙寫)、過期版本被拒、重開後冪等紀錄仍在、行程猝死後無半途狀態、交易中途失敗整體回滾、等待鎖逾時回 503、畸形輸入回型別化錯誤、Host 標頭與重複標頭檢查、執行期只用標準函式庫。
  WHY: 每一種會改廣告狀態的寫入(改預算、暫停;不含作廢)都必須帶預期版本、版本不符就拒收,這是「結果不明期間同範圍寫入」與「DSP 版本已被別人推進 → 舊提案過期、重新分析、不強制覆寫」這兩條對帳規則在 DSP 端的最後防線;少了它,過期的提案或 lease 過期後才醒來的工作者會覆蓋別人的新結果。出處:[[Projects/RTB_Agent_Phase0架構]] 的外部寫入失敗語意與對帳判定表。
  KEY:★INVARIANT★ 同一把冪等鍵最多只套用一次:同鍵同內容重送回原結果、不再改狀態;同鍵不同內容一律拒收且不改任何東西;並行搶同一把鍵也只有一個真的套用。 [test:test_same_key_same_payload_applies_once_and_returns_original_result,test_every_write_action_with_the_same_key_applies_once_and_replays,test_same_key_different_payload_is_rejected_and_changes_nothing,test_concurrent_requests_with_the_same_key_apply_exactly_once,test_concurrent_same_key_requests_over_http_apply_exactly_once] [audit:sonnet/2026-09-22] [kill:recipes]
  RULE: 並行搶同一把鍵只套用一次的測試(store 與 HTTP 兩支)只用改預算;暫停的同鍵只有循序重送的測試(Phase 11 補的那支)。儲存層的同鍵判斷不分動作,所以目前判斷暫停也成立,但沒有測試直接證明,宣稱驗證器的冪等清單也沒把並行同鍵寫成「每一種」。 [since:2026-09-24] [retire:補上暫停的並行同鍵測試,或儲存層改成依動作分流同鍵判斷時] [confirmed:2026-09-24]
  KEY:★INVARIANT★ 一次寫入操作(改狀態、升版本、記歷史、記冪等紀錄)要嘛全部生效、要嘛全部沒發生;中途失敗或行程猝死都不留下半途狀態。 [test:test_failure_between_state_change_and_idempotency_record_rolls_everything_back,test_process_death_between_state_change_and_idempotency_record_leaves_no_half_state] [audit:sonnet/2026-09-22] [kill:recipes]
  WHY: DSP 端這幾條合約(F1 提交前後逾時、版本不符一律拒收)描述的是「真實 DSP 必須具備、執行行程對帳要依賴的行為」;Mock DSP 是外部 DSP 的替身。寫下時(2026-09-22)還沒有正式程式碼呼叫 DSP 寫入;Phase 3 起執行行程的 DSP 用戶端會發寫入與作廢(以程式碼為準,查法在那支檔的家 [[Systems/執行迴圈]]),依賴方就是執行行程對帳,當時先登記成有最遲日期的預告合約 [[Verification/事故F1_結果不明只用原鍵對帳]]。出處:2026-09-22 合約獨立審計判「誰依賴它」不穩定後,使用者裁定「認定有依賴方」。
  KEY:★INVARIANT★ 事故 F1 的 DSP 側:提交前逾時的請求事後絕不偷偷提交;提交後才逾時的請求已生效且只生效一次,呼叫端用同一把冪等鍵重試拿到原結果、不會再套用一次。 [test:test_timeout_before_commit_client_sees_timeout_and_dsp_never_commits_later,test_timeout_after_commit_client_sees_timeout_but_dsp_applied_exactly_once,test_retry_with_same_key_after_commit_timeout_replays_and_does_not_apply_twice] [audit:sonnet/2026-09-22] [kill:recipes]
  KEY:★INVARIANT★ 每一種會改廣告狀態的寫入動作(改預算、暫停;不含作廢),預期版本跟現況不符(不論比現況舊或比現況新)一律拒收,不得覆寫較新的狀態,版本號與歷史都不動。 [test:test_stale_expected_version_is_rejected_without_writing,test_stale_version_gets_409_and_does_not_overwrite_newer_state,test_a_future_expected_version_is_rejected_not_only_a_stale_one,test_pause_with_a_stale_expected_version_is_rejected_without_writing,test_every_write_action_on_the_http_routes_has_a_version_check_example,test_every_write_action_rejects_a_stale_expected_version,test_only_the_store_module_writes_to_the_dsp_database] [audit:人裁/2026-09-22] [kill:recipes]
  WHY: [2026-09-24] 版本不符那條原本寫「每一種寫入動作」,但路由另有作廢(void_operation):作廢只看冪等鍵、不改廣告狀態,程式裡預期版本只驗格式不比對,綁的測試也只參數化改預算與暫停兩種。這是合約措辭過大,不是程式漏洞,所以把措辭改窄成跟程式一致,不改程式。改窄後不隨 Phase 11 回退改回。出處:[[Projects/RTB_Phase11證據清單與驗證器_計劃]]〈五條宣稱〉;宣稱驗證器的冪等清單用 CAMPAIGN_WRITE_ACTIONS 列舉,見 [[Systems/宣稱驗證器]]。
verified_by:
  - "[[Verification/Phase1驗收紀錄]]"
  - "[[Verification/Phase3驗收紀錄]]"
  - "[[Verification/Phase7驗收紀錄]]"
  - "[[Verification/Phase9驗收紀錄]]"
  - "[[Verification/Phase11驗收紀錄]]"
  - "[[Verification/Phase14增量2a離線驗證]]"
kill_recipes: |-
  [{"invariant": "同一把冪等鍵最多只套用一次", "test": "test_same_key_same_payload_applies_once_and_returns_original_result", "file": "src/rtb/dsp/store.py", "old": "        if existing is not None:\n            return existing", "new": "        if False:\n            return existing", "note": "重送不再回原結果,同一把鍵被套用第二次"}, {"invariant": "事故 F1 的 DSP 側", "test": "test_timeout_before_commit_client_sees_timeout_and_dsp_never_commits_later", "file": "src/rtb/dsp/server.py", "old": "            time.sleep(self.server.hang_seconds)  # 不論客戶端是否還在,都不提交\n            raise NoResponse", "new": "            time.sleep(self.server.hang_seconds)", "note": "提交前逾時睡醒後繼續往下提交"}, {"invariant": "預期版本跟現況不符", "test": "test_a_future_expected_version_is_rejected_not_only_a_stale_one", "file": "src/rtb/dsp/store.py", "old": "        if current.version != op.expected_version:", "new": "        if current.version > op.expected_version:", "note": "只擋舊版本,未來版本照樣放行"}, {"invariant": "要嘛全部生效", "test": "test_failure_between_state_change_and_idempotency_record_rolls_everything_back", "file": "src/rtb/dsp/store.py", "old": "            if self._conn.in_transaction:  # SQLite 有時已自行回滾;再回滾會蓋掉真正的原因\n                self._conn.execute(\"ROLLBACK\")", "new": "            if self._conn.in_transaction:  # SQLite 有時已自行回滾;再回滾會蓋掉真正的原因\n                self._conn.execute(\"COMMIT\")", "note": "中途失敗時把半途狀態提交而不是回滾"}]
decision_refs_ai:
  - "Projects/RTB_Agent_Phase0架構.md#d11"
---
# Mock-DSP

已有儲存層與獨立行程的 HTTP 介面加故障注入。端點全集以 `src/rtb/dsp/server.py` 的 `ROUTES` 為準(`grep -n -A14 "^ROUTES" src/rtb/dsp/server.py`):查廣告、查歷史、查指標、逐日成效、過去調整、用冪等鍵查操作、依時間找游標、依游標列操作(後兩支要稽核金鑰,見 Phase 9 增量 3 那段)、改預算、暫停、作廢。故障注入(需啟動旗標,旗標關閉時收到故障標頭回 400 且不改狀態)。查指標(`get_metrics`,路由 `GET /campaigns/<id>/metrics`)只回原始事實(曝光、點擊、轉換、花費、營收;時間窗限 1h、1d、7d),沒有的欄位維持 null,不會被補成 0;比率由 [[確定性指標計算]] 自己算。測試用的 `seed_metrics` 只收存進去不會失真的值:計數欄位只收整數(拒絕 1.5、字串、bytes、布林);Phase 14 增量 2a 起金額轉成整數分存放,只收不超過兩位小數、整數部分最多 13 位的值,讀回固定兩位小數字串;NaN、無限大、超出範圍的值、不存在的廣告與不合法的時間窗都被拒絕,沒給的欄位維持 null。負數照存,讓測試能造出不合理資料去驗領域層。標準函式庫會把請求路徑開頭的多個斜線收合,所以 `//網域/...` 不會被當成網域,有測試鎖住。

各檔分工:`src/rtb/dsp/store.py` 是儲存層,負責廣告狀態、版本、操作歷史、冪等紀錄與原子提交;`src/rtb/dsp/errors.py` 是型別化錯誤,讓呼叫端靠型別分辨可重試、永久拒絕、版本已變;`src/rtb/dsp/server.py` 是獨立行程的 HTTP 介面與故障注入,只綁定本機回送位址。

冪等鍵的並行語意是「有上限地等,再回原結果」:後到的請求在寫入鎖上排隊,等第一個提交後看見冪等紀錄,直接回同樣的結果;等待上限由連線的 busy_timeout 決定,逾時回可重試的暫時性錯誤。

## 已知缺口(如實標明,尚未做或刻意不做)

- 計劃驗收要求「同一種故障連續 50 次結果 100% 一致」。目前是手動用迴圈重跑相關測試(2026-09-21:第一批 50 次、修復後 50 次、第二輪代碼審修復後 30 次,各 0 次失敗),沒有自動化進 CI。指令:對 `-k "concurrent or naive or process_death or timeout or in_flight or restart or lock_contention or non_loopback"` 重複執行。
- 計劃列為以標頭注入的三種故障(版本衝突、同鍵同內容重送、同鍵不同內容)沒有做成標頭注入,而是用真實邏輯產生並測試。這是有意的偏離:真實邏輯比注入更能證明行為。
- 動作驗證與狀態轉換目前是 if 分支,不是計劃「狀態機是資料」所說的動作表;只有預算與暫停兩種動作,可接受。
REVISIT:2026-11-21 動作增加到第三種時,把驗證與狀態轉換抽成動作表。
- 「只綁本機」的測試在沒有非回送網卡的機器上會略過,略過時沒有訊號進治理帳。
- 仍沒有測試守護的小防護(第二輪代碼審的存活變異):對客戶端中途離開靜音的 `handle_error`、回應是否帶 Content-Length。兩者行為現況正確,缺的是回歸網。原本同列的「每個請求結束時關閉資料庫連線」已有測試守著(見下文 2026-09-22 抽共用基礎那段與 [[Systems/共用行程基礎]])。
- 部分「提交前逾時」類測試仍用固定睡眠等待(等過處理程式睡醒),慢機器上只會讓測試少等而偏向漏抓,不會誤紅;提交後逾時與慢請求進行中已改用輪詢與同步點。
- 歷史查詢沒有分頁;每個請求新建資料庫連線;這兩項是效能檢核題的「張力」表態,Mock 規模可接受。
- 2026-09-22 合約獨立審計記下的兩個薄弱處(合約本身沒被違反):①「同一把冪等鍵只套用一次」的並行測試全部是同一個行程裡的多執行緒;把資料庫層互斥換成行程內的鎖,這幾支測試照樣綠,真正跨兩個行程時 SQLite 仍擋住雙寫,但輸家拿到未分類的 500 而不是可重試的 503。②「一次寫入全有全無」的防護,有一大半靠行程猝死測試裡那一行精確的快照字串;那行若被簡化,這條的防護力會明顯下降。
  跨兩個行程搶同一把鍵的測試原本綁「Phase 4 開工或完成時補」;Phase 4 2026-09-23 已完成,這支測試仍沒補(2026-10-03 查:`tests/dsp/` 的並行同鍵測試都是同一個行程裡的多執行緒,HTTP 那支是一個 DSP 行程配多執行緒客戶端;`grep -rn "兩個獨立行程\|cross_process" tests/` 無)。沒有找到會自然逼出這支測試的後續事件,改綁日期。
REVISIT:2026-11-30 補一支兩個獨立行程搶同一把鍵的測試,並斷言輸家拿到可重試的錯誤;決定不補就寫明理由,把這個薄弱處改記成刻意不做。
- 「提交前逾時絕不偷偷提交」的測試只能在有限的觀察時間內確認;刻意另開脫離的行程、等觀察時間過了才提交的寫法(2026-09-22 審計員實測構造出來)擋不住。這屬於刻意規避,不在「防忘記、不防繞過」的威脅模型內;任何有限時間的測試都無法證明「永遠不會晚點提交」。
- 測試的白箱部分(對 `_record_idempotency`、`_conn` 打補丁)綁定私有成員,重構時會跟著紅,屬刻意。

型別檢查:2026-09-22 起 `store.py` 與 `server.py` 通過 mypy 嚴格模式。`Operation.params` 與 `expected_version` 都標成未驗證(`dict[str, object]` 與 `object`),`_next_state` 讀預算時用 `is_plain_int` 收窄,型別檢查因此守得住這條不可信資料的路徑。順手修了兩個真的隱患:`cursor.lastrowid` 可能是 None(現在明確報錯並回滾),以及 `Operation.params` 與 `expected_version` 來自不可信請求,型別改標為未驗證的 `object`,由 `_validate` 用 `TypeGuard` 確認後才使用。伺服器的等待逾時改成直接設定連線的逾時,不再設定基底類別的類別變數。

2026-09-22:伺服器共通行為(Host 檢查、請求本文上限、逾時、JSON 錯誤與 500 後備)與 SQLite 連線及寫入交易已抽到 [[Systems/共用行程基礎]],DSP 只保留路由、錯誤對照表與故障注入;上面這些規則的實作與防回歸現在在那一篇。
連線建立階段(切換 WAL、建表)撞上鎖競爭,現在也回 503 store_busy(可重試),不再落到不可重試的 500;每個請求開的儲存連線在成功、領域例外與非預期例外三條路徑都會關閉,有測試守著(見共用行程基礎的測試)。
X-Fault 標頭的讀取與「沒開旗標就拒絕」也改由共用基礎提供(提案收件口共用同一套),DSP 只保留自己的故障模式清單。

憑證驗證在 `src/rtb/dsp/capability.py`:DSP 自己的聲明欄位、範圍欄位與本文白名單(刻意不依賴領域層,動作名稱用路由表既有的)。

- RULE: 作廢紀錄永久保留、沒有筆數或保留期上限:本地已憑它把嘗試判成失敗,刪掉舊請求就可能又能提交。Demo 規模下只增不減可接受。[since:2026-09-23] [retire:真實部署或資料量成長到需要清理時,改成有保留期的設計並先解決「過期清理後舊請求又能提交」]
REVISIT:2026-11-30 盤點作廢表的筆數成長,決定要不要設保留期與清理方式。
- RULE: 會改狀態的端點(寫入與作廢)一律經同一層故障注入外殼,故障行為只有一套。防回歸:[test:test_a_void_call_that_times_out_comes_back_as_no_answer]。[since:2026-09-23] [retire:故障注入整個撤掉時]
- RULE: 冪等鍵與預期版本的格式檢查(含預期版本的整數上界)由寫入與作廢共用同一支函式。防回歸:[test:test_an_out_of_range_expected_version_is_rejected_before_anything_is_voided]。[since:2026-09-23] [retire:兩個端點的驗證需求分岔時]
- RULE: 儲存層建構子補欄位失敗時要關掉剛開的連線,等鎖逾時轉成自己模組的「忙碌」例外,跟收件表、分析行程的任務表同一寫法。防回歸:[test:test_a_failed_column_migration_closes_the_new_connection]。[since:2026-09-23] [retire:三支資料庫模組改用共用的開庫函式時撤除]

## 作廢冪等鍵(Phase 3 增量 4 對帳)

- WHY: 對帳要判「沒發生」之前,必須證明舊請求永遠不會晚到提交。「憑證到期時間加寬限」證明不了(DSP 驗完時間之後、提交之前可能被暫停),所以改由 DSP 提供作廢:作廢與寫入在同一把寫入鎖下排隊,結果只有「寫入先提交(作廢回已提交)」或「作廢先成功(之後同鍵寫入一律 409 operation_voided)」兩種。出處:[[Projects/RTB_Phase3外部寫入安全_計劃]] 增量 4 設計審第 1、2 輪。
- RULE: 寫入要在拿到寫入鎖之後、冪等重放判斷之後才查作廢;放在鎖外,舊請求可能先查到「沒作廢」、等作廢完成後才提交。作廢只看鍵,不碰憑證與時間、不進操作指紋。[since:2026-09-23] [retire:DSP 改用別的 fencing 機制(例如佇列消費者的 lease)時撤除] 防回歸:[test:test_voiding_and_a_late_write_are_linearized_by_the_write_lock]
- RULE: 作廢紀錄永久保留:回退時可以拿掉作廢端點,不能拿掉作廢表與寫入時的作廢檢查——執行行程已憑它把嘗試判成失敗。[since:2026-09-23] [retire:所有憑作廢判失敗的嘗試都已人工核對、且舊請求確定已排空時] 防回歸:[test:test_the_dsp_voids_a_key_or_reports_the_commit_that_won]
- RULE: 模擬 DSP 沒有建立或刪除廣告的介面(只有測試的種子資料):廣告現在不存在就代表從來不存在,執行行程對帳時因此可以不作廢、直接判失敗(廣告不存在)。[since:2026-09-23] [retire:模擬 DSP 加了建立或刪除廣告的介面時,要回頭改對帳那一格] 防回歸:[test:test_reconcile_fails_a_missing_campaign_without_voiding]
- 作廢端點:路徑掛在廣告底下、帶冪等鍵標頭與能力憑證,本文帶預期版本;驗證順序與範圍檢查照寫入端點(同一個函式),端點把自己的動作常數「void_operation」交給範圍檢查,所以一般寫入憑證拿來作廢會因動作不符被拒。防回歸:[test:test_voiding_needs_a_void_capability_scoped_to_the_key]
- 操作紀錄補存預期版本(照補欄位做法;舊列留空值),用鍵查詢的端點多回參數、預期版本與冪等鍵。防回歸:[test:test_the_operation_lookup_returns_params_and_expected_version]、[test:test_an_old_dsp_database_gains_the_expected_version_column]
- 寫入路由分兩類:改廣告的(改預算、暫停,`CAMPAIGN_WRITE_ACTIONS`)與作廢;兩支既有窮舉測試改成分兩類明列。

## 執行行程測試用的假 DSP(2026-09-23)

- RULE: tests/executor/fakes.py 的假 DSP 的冪等語意要跟真 DSP 對齊:同一把鍵再寫一次,內容相同回第一次的結果、內容不同回冪等衝突,已作廢的鍵回操作已作廢。改真 DSP 的冪等判斷時回頭改假 DSP。[since:2026-09-23] [retire:執行行程測試改用真 DSP 時]
- WHY: Phase 4 增量 3a 設計審第 2 輪指出假 DSP 同鍵第二次寫入會再套用一次:失去租約的工作者醒來補送的舊請求本來就會晚到,不去重就變成假的「套用兩次」。攔截點(on_write)在狀態鎖外面先跑:等在攔截點的工作者若握著鎖,接手的另一個永遠寫不進來(第 3 輪外家席)。出處:[[Projects/RTB_Phase4佇列與重新投遞_計劃]] 增量 3a。

- Phase 9 增量 3:提交時間取「時鐘讀數」與「上一筆提交時間」較大的那個(在寫入交易裡讀上一筆),依操作編號翻頁才等於依時間翻頁;時鐘倒退時提交時間會被墊高。防回歸:[test:test_dsp_commit_times_never_go_backwards]。另加提交時間索引與兩支唯讀端點(路徑恰好一個參數,照既有路由形狀):依時間找起點回「提交時間大於等於它的第一筆」之前的最後一個操作編號;依游標列之後的操作(連同廣告建檔時的租戶),一頁最多 50 筆並回下一頁游標。一頁 50 筆而不是設計寫的 5000 筆:讀它的是 [[Systems/服務水準與燒損告警]],經共用 HTTP 用戶端,回應上限 64 KB。
- Phase 9 增量 3 代碼審第 1 輪(2026-09-24):
  - 列操作兩支端點回全租戶明細,要帶唯讀稽核金鑰(代使用者裁定):放在專用標頭、寫法是金鑰位元組的 base64url,解回位元組後固定時間比對,解不開當帶錯(代碼審第 2 輪:第 1 輪借用能力憑證標頭、放原文,非 Latin-1 金鑰送不出);稽核金鑰超過 1024 位元組啟動就報設定錯誤、不啟動(代碼審第 3 輪,防回歸:[test:test_the_dsp_refuses_to_start_with_an_overlong_audit_key]);沒帶 401、帶錯 403、啟動時沒設這把金鑰一律 503(比照沒設簽發金鑰拒收寫入)。不沿用寫入憑證:那套聲明綁單一廣告、冪等鍵與寫入動作,又是簽發金鑰簽的。金鑰由啟動程式讀環境變數傳進伺服器物件。既有依鍵、依廣告的唯讀端點不變。防回歸:[test:test_the_operation_list_endpoints_require_the_audit_key]。
  - 游標只收十進位數字(上標數字判數字會過、轉整數會失敗),轉整數後不得超過 SQLite 整數上限,都回 400 invalid_cursor。防回歸:[test:test_a_cursor_must_be_a_plain_decimal_within_the_integer_range]。
  - 提交時間寫入前統一成固定 UTC 寫法(換算成 +00:00 的 isoformat),依時間找起點的查詢用同一支函式:時鐘注入非 UTC 偏移時,原樣存下會讓字串順序跟時間順序對不上、游標漏筆。舊資料不搬(理由:預設時鐘本來就是這種寫法、補欄位做法從不改寫舊列、操作紀錄只增不改;詳見 [[Projects/RTB_Phase9可觀測與SLO_計劃]] 增量 3 的實作解讀)。防回歸:[test:test_dsp_commit_times_are_stored_in_one_utc_form]、[test:test_a_page_boundary_between_equal_commit_times_reads_each_operation_once]。

## 逐日成效與過去調整（Phase 14 增量 2a，2026-09-26）

出處 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]] [S1414]、[S1415]、[S1422]、[S1423]、[S1426]；改寫 [[Projects/RTB_Phase13AI參與決策_計劃]] [S1126]、[S1127]、[S1130]。

- DSP 預算寫入與展示種子只寫操作紀錄一個來源。預算事件在同一交易記錄前後預算與帶時區提交時間；過去調整端點從操作紀錄取最近一次加額，後續減額不會擠掉它。三日窗以加額的 UTC 日期 D 前後各三個完整日桶計算；D+1 至 D+3 未全完成，後段轉換回 null。舊資料庫遷移補不回調整前預算的那筆（例如遷移前 3 天內的第一筆真實加額）照樣回、`budget_before` 為 null，由領域判證據不足，不回 404 讓它消失；操作紀錄是只增不改稽核表，補得回的前值另記在補值表 `operation_budget_backfill`，不回頭改操作紀錄。防回歸：[test:test_past_adjustments_include_normal_budget_operations]、[test:test_old_adjustment_seed_tables_migrate_to_the_single_operation_source]、[test:test_a_legacy_raise_without_a_provable_budget_before_is_reported_as_missing_evidence]、[test:test_audit_tables_are_only_ever_inserted_into]。
- 已知邊界（2a 代碼審 r2 鏡頭A 發現 2，接受）：舊資料庫某廣告的第一筆改預算補不回前值、之後只有減額時，這筆一直是「最近一次加額」候選（分不出是不是加額），端點一直回它、`budget_before` null，領域第 4 條一直證據不足，直到這個廣告出現下一筆加額為止。接受的理由：只影響 2a 之前建的舊資料庫（新寫入一律同交易記前值；展示每次重種平台）；方向保守（不提案，不會錯提案）；設「多舊不算數」等於猜那筆不是加額，違反「補不回不猜」。正式九條第 4 條在增量 2b 接線時，若要處理這種廣告，改成由人工或新加額解開，不在 DSP 猜。
REVISIT:2026-12-31 若正式規則上線後仍有來自 2a 之前舊庫的廣告卡在這個邊界，再評估讓人工標記那筆的前值。
- 逐日資料以 UTC 日期作鍵，保留最近 30 個完整日與最近加額 D±3 日桶。**讀取端點不寫資料庫**（2a 代碼審 r1 鏡頭3 改正：原本讀前物化要搶寫入鎖，別人握鎖時讀取會 503）：逐日、1d/7d、過去調整、歷史都在讀取快照裡讀，剛完成的日桶與 1d/7d 由「已存日桶＋展示樣板＋一次時鐘讀數」在記憶體推算（`derive_days`、`window_figures`）；每個讀取端點只讀一次時鐘，整個回應同一日期快照（外家 finder 1）。持久化與 30 日保留只在寫入端（種子、每筆執行寫入）同一寫入交易做，寫下的值跟讀取推算的相同，最新日期在拿到寫入鎖後才讀（外家 finder 2）。有日桶的廣告 1d/7d 一律推算，不准另用 `seed_metrics` 種這兩窗；七天全沒資料時沒有 7d 窗（404）。防回歸：[test:test_demo_daily_rollover_preserves_full_windows]、[test:test_reads_never_write_and_still_work_while_the_write_lock_is_held]、[test:test_one_read_uses_one_clock_reading_across_midnight]、[test:test_writes_persist_days_after_reading_the_newest_day_under_the_write_lock]、[test:test_a_seven_day_window_without_any_data_is_not_found]。
- 儲存層沒注入時鐘時，時鐘在呼叫時才查模組的 `_utc_now`：`tests/dsp/test_investigation_data.py` 用自動夾具把它換成固定 NOW 之後 400 天，忘了注入時鐘又用固定日期種資料的測試當場翻紅，不等真實日期走到某天（2a 代碼審 r2 鏡頭A：r1 新加的溢位測試重犯真時鐘配固定日期，10/01 起會紅）。子行程起的 DSP 不受夾具管，用它的測試必須跟日期無關。
- 新完成的日子只由展示樣板（`daily_templates`，種子給）推出；沒有樣板的廣告，新完成的日子就是沒資料，不把最新一天（含沒資料那天）照抄下去（2a 代碼審 r1 鏡頭1）。防回歸：[test:test_a_completed_day_without_a_template_has_no_data_instead_of_a_copy]。
- 花費與營收以整數分存算，對外回固定兩位小數字串；DSP 收金額只經 `cents_of`（只收 ASCII 數字、最多兩位小數、整數部分最多 13 位，其餘 ValidationRejected），格式化只經 `money_text`，展示種子的核對也用這兩支。13 位跟分析端讀取白名單同一個數（DSP 依既有邊界不依賴領域層，自留一份）：15 位有效數字轉浮點不差一分，七天加總遠低於 SQLite 上限；加總在讀取時以 Python 整數算、不寫回，不會丟溢位例外。日桶與樣板每天另有上限：金額是整數分上限的七分之一（`DAILY_MAX_CENTS`）、計數是 `SQLITE_INTEGER_MAX` 的七分之一（`DAILY_MAX_COUNT`），超過整份種子拒收；舊相對日數資料遷移、以及前一版已存的日期鍵日桶與樣板（開庫不走遷移）在讀取推算時同一支 `_bounded_bucket` 處理：超限的欄記缺值，五欄都缺值的那天就是沒資料（no_data 為真，不回「五欄全空卻說有資料」讓讀取層整週拒收；2a 代碼審 r3 鏡頭A 3、外家 finder 1/2）。所以 1d/7d 與加額前後三天的合計一定在分析端讀取白名單上限內，不會「每天存得進、讀長窗卻整份 invalid」（2a 代碼審 r2 鏡頭B 發現 1）；1 小時窗是單一值，照原上限。舊庫 REAL 金額存不下者升級時記缺值，DSP 照常啟動。防回歸：[test:test_amounts_are_stored_as_integer_cents_and_returned_as_fixed_decimal_strings]、[test:test_seven_day_totals_never_overflow_into_a_server_error]、[test:test_a_legacy_day_whose_every_value_is_over_the_daily_limit_becomes_no_data]、[test:test_stored_date_keyed_buckets_over_the_daily_limit_read_as_missing]、[test:test_legacy_amounts_that_no_longer_fit_do_not_stop_the_dsp_from_starting]。
- 歷史端點最多回 50 列；沒超過 50 筆時維持既有 `{"history": […]}` 形狀，超過才另帶由完整集合算的近期預算旗標與計數 `summary` 及 `truncated=true`，讓讀取者不因截斷漏看加額。摘要與列在同一讀取快照裡讀（外家否決 2）。摘要的「最近 3 天」以 DSP 讀取時刻切，只是截斷時的保守旗標，正式規則以決策 now 判。防回歸：[test:test_bounded_history_preserves_recent_budget_changes]、[test:test_an_untruncated_history_keeps_the_existing_shape]、[test:test_history_summary_and_rows_come_from_one_snapshot]。
- 逐日與過去調整兩支端點只准 GET、不寫資料庫；舊的獨立過去調整表與種子標記不再是正式資料來源。測試在 tests/dsp/test_investigation_data.py、tests/dsp/test_store.py、tests/dsp/test_server.py；HTTP 測試需能綁本機網路埠的環境。

## 操作歷史回應帶廣告編號(Phase 14 增量 2b 代碼審 r1 資安-2,2026-09-26)

`/campaigns/<id>/history` 回應頂層另帶 `campaign_id`,分析端讀取層核對是這件工作的廣告(歷史進了正式規則第 3 條);列的形狀、截斷摘要照舊,分析端核過就拿掉,存下的原始回應與 AI 收據維持既有形狀。出處 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]。[test:test_the_change_history_must_belong_to_the_task_campaign]
