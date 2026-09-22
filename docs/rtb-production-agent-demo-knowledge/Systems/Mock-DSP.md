---
type: system
status: doing
created: 2026-09-21
updated: 2026-09-21
responsibility: 負責 Mock DSP 的廣告狀態、版本、操作歷史與冪等紀錄的儲存與原子提交、型別化錯誤,以及獨立行程的 HTTP 介面與故障注入;不負責指標計算與 agent 端的任何邏輯。
aliases: []
about_code:
  - src/rtb/dsp/store.py
  - src/rtb/dsp/errors.py
  - src/rtb/dsp/server.py
tags:
  - type/system
  - status/doing
summary: |-
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
  WHY: 每一種寫入都必須帶預期版本、版本不符就拒收,這是「結果不明期間同範圍寫入」與「DSP 版本已被別人推進 → 舊提案過期、重新分析、不強制覆寫」這兩條對帳規則在 DSP 端的最後防線;少了它,過期的提案或 lease 過期後才醒來的工作者會覆蓋別人的新結果。出處:[[Projects/RTB_Agent_Phase0架構]] 的外部寫入失敗語意與對帳判定表。
  KEY:★INVARIANT★ 同一把冪等鍵最多只套用一次:同鍵同內容重送回原結果、不再改狀態;同鍵不同內容一律拒收且不改任何東西;並行搶同一把鍵也只有一個真的套用。 [test:test_same_key_same_payload_applies_once_and_returns_original_result,test_same_key_different_payload_is_rejected_and_changes_nothing,test_concurrent_requests_with_the_same_key_apply_exactly_once,test_concurrent_same_key_requests_over_http_apply_exactly_once] [audit:sonnet/2026-09-22] [kill:recipes]
  KEY:★INVARIANT★ 一次寫入操作(改狀態、升版本、記歷史、記冪等紀錄)要嘛全部生效、要嘛全部沒發生;中途失敗或行程猝死都不留下半途狀態。 [test:test_failure_between_state_change_and_idempotency_record_rolls_everything_back,test_process_death_between_state_change_and_idempotency_record_leaves_no_half_state] [audit:sonnet/2026-09-22] [kill:recipes]
  WHY: DSP 端這幾條合約(F1 提交前後逾時、版本不符一律拒收)描述的是「真實 DSP 必須具備、執行行程對帳要依賴的行為」;Mock DSP 是外部 DSP 的替身。目前沒有正式程式碼呼叫 DSP 寫入,依賴方是 Phase 3 的執行行程對帳,已登記成有最遲日期的預告合約 [[Verification/2026-09-22_事故-F1-的執行面-執行行程遇到外部結果不明-逾時-連線中斷-時標記為結果不明]]。出處:2026-09-22 合約獨立審計判「誰依賴它」不穩定後,使用者裁定「認定有依賴方」。
  KEY:★INVARIANT★ 事故 F1 的 DSP 側:提交前逾時的請求事後絕不偷偷提交;提交後才逾時的請求已生效且只生效一次,呼叫端用同一把冪等鍵重試拿到原結果、不會再套用一次。 [test:test_timeout_before_commit_client_sees_timeout_and_dsp_never_commits_later,test_timeout_after_commit_client_sees_timeout_but_dsp_applied_exactly_once,test_retry_with_same_key_after_commit_timeout_replays_and_does_not_apply_twice] [audit:sonnet/2026-09-22] [kill:recipes]
  KEY:★INVARIANT★ 每一種寫入動作,預期版本跟現況不符(不論比現況舊或比現況新)一律拒收,不得覆寫較新的狀態,版本號與歷史都不動。 [test:test_stale_expected_version_is_rejected_without_writing,test_stale_version_gets_409_and_does_not_overwrite_newer_state,test_a_future_expected_version_is_rejected_not_only_a_stale_one,test_pause_with_a_stale_expected_version_is_rejected_without_writing,test_every_write_action_on_the_http_routes_has_a_version_check_example,test_every_write_action_rejects_a_stale_expected_version,test_only_the_store_module_writes_to_the_dsp_database] [audit:人裁/2026-09-22] [kill:recipes]
verified_by:
  - "[[Verification/Phase1驗收紀錄]]"
kill_recipes: |-
  [{"invariant": "同一把冪等鍵最多只套用一次", "test": "test_same_key_same_payload_applies_once_and_returns_original_result", "file": "src/rtb/dsp/store.py", "old": "        if existing is not None:\n            return existing", "new": "        if False:\n            return existing", "note": "重送不再回原結果,同一把鍵被套用第二次"}, {"invariant": "要嘛全部生效", "test": "test_failure_between_state_change_and_idempotency_record_rolls_everything_back", "file": "src/rtb/dsp/store.py", "old": "                self._conn.execute(\"ROLLBACK\")", "new": "                self._conn.execute(\"COMMIT\")", "note": "中途失敗時把半途狀態提交而不是回滾"}, {"invariant": "事故 F1 的 DSP 側", "test": "test_timeout_before_commit_client_sees_timeout_and_dsp_never_commits_later", "file": "src/rtb/dsp/server.py", "old": "            time.sleep(self.server.hang_seconds)  # 不論客戶端是否還在,都不提交\n            raise NoResponse", "new": "            time.sleep(self.server.hang_seconds)", "note": "提交前逾時睡醒後繼續往下提交"}, {"invariant": "預期版本跟現況不符", "test": "test_a_future_expected_version_is_rejected_not_only_a_stale_one", "file": "src/rtb/dsp/store.py", "old": "        if current.version != op.expected_version:", "new": "        if current.version > op.expected_version:", "note": "只擋舊版本,未來版本照樣放行"}]
---
# Mock-DSP

已有儲存層與獨立行程的 HTTP 介面(查廣告、改預算、暫停、用冪等鍵查操作、查歷史、查指標)加故障注入(需啟動旗標,旗標關閉時收到故障標頭回 400 且不改狀態)。`getMetrics` 只回原始事實(曝光、點擊、轉換、花費、營收;時間窗限 1h、1d、7d),沒有的欄位維持 null,不會被補成 0;比率由 [[確定性指標計算]] 自己算。測試用的 `seed_metrics` 只收存進去不會失真的值:計數欄位只收整數(拒絕 1.5、字串、bytes、布林);金額欄位是 REAL,整數讀回會變浮點(12 變 12.0),所以整數只收到 2**53 以內;NaN、無限大、超出範圍的值、不存在的廣告與不合法的時間窗都被拒絕,沒給的欄位維持 null。負數照存,讓測試能造出不合理資料去驗領域層。標準函式庫會把請求路徑開頭的多個斜線收合,所以 `//網域/...` 不會被當成網域,有測試鎖住。

各檔分工:`src/rtb/dsp/store.py` 是儲存層,負責廣告狀態、版本、操作歷史、冪等紀錄與原子提交;`src/rtb/dsp/errors.py` 是型別化錯誤,讓呼叫端靠型別分辨可重試、永久拒絕、版本已變;`src/rtb/dsp/server.py` 是獨立行程的 HTTP 介面與故障注入,只綁定本機回送位址。

冪等鍵的並行語意是「有上限地等,再回原結果」:後到的請求在寫入鎖上排隊,等第一個提交後看見冪等紀錄,直接回同樣的結果;等待上限由連線的 busy_timeout 決定,逾時回可重試的暫時性錯誤。

## 已知缺口(如實標明,尚未做或刻意不做)

- 計劃驗收要求「同一種故障連續 50 次結果 100% 一致」。目前是手動用迴圈重跑相關測試(2026-09-21:第一批 50 次、修復後 50 次、第二輪代碼審修復後 30 次,各 0 次失敗),沒有自動化進 CI。指令:對 `-k "concurrent or naive or process_death or timeout or in_flight or restart or lock_contention or non_loopback"` 重複執行。
- 計劃列為以標頭注入的三種故障(版本衝突、同鍵同內容重送、同鍵不同內容)沒有做成標頭注入,而是用真實邏輯產生並測試。這是有意的偏離:真實邏輯比注入更能證明行為。
- 動作驗證與狀態轉換目前是 if 分支,不是計劃「狀態機是資料」所說的動作表;只有預算與暫停兩種動作,可接受。
REVISIT:2026-11-21 動作增加到第三種時,把驗證與狀態轉換抽成動作表。
- 「只綁本機」的測試在沒有非回送網卡的機器上會略過,略過時沒有訊號進治理帳。
- 仍沒有測試守護的小防護(第二輪代碼審的存活變異):伺服器端每個請求結束時關閉資料庫連線(拿掉只會等垃圾回收)、對客戶端中途離開靜音的 `handle_error`、回應是否帶 Content-Length。三者行為現況正確,缺的是回歸網。
- 部分「提交前逾時」類測試仍用固定睡眠等待(等過處理程式睡醒),慢機器上只會讓測試少等而偏向漏抓,不會誤紅;提交後逾時與慢請求進行中已改用輪詢與同步點。
- 歷史查詢沒有分頁;每個請求新建資料庫連線;這兩項是效能檢核題的「張力」表態,Mock 規模可接受。
- 2026-09-22 合約獨立審計記下的兩個薄弱處(合約本身沒被違反):①「同一把冪等鍵只套用一次」的並行測試全部是同一個行程裡的多執行緒;把資料庫層互斥換成行程內的鎖,這幾支測試照樣綠,真正跨兩個行程時 SQLite 仍擋住雙寫,但輸家拿到未分類的 500 而不是可重試的 503。②「一次寫入全有全無」的防護,有一大半靠行程猝死測試裡那一行精確的快照字串;那行若被簡化,這條的防護力會明顯下降。
REVISIT:2026-11-30 Phase 4 開始有多個工作者行程時,補一支兩個獨立行程搶同一把鍵的測試,並斷言輸家拿到可重試的錯誤。
- 「提交前逾時絕不偷偷提交」的測試只能在有限的觀察時間內確認;刻意另開脫離的行程、等觀察時間過了才提交的寫法(2026-09-22 審計員實測構造出來)擋不住。這屬於刻意規避,不在「防忘記、不防繞過」的威脅模型內;任何有限時間的測試都無法證明「永遠不會晚點提交」。
- 測試的白箱部分(對 `_record_idempotency`、`_conn` 打補丁)綁定私有成員,重構時會跟著紅,屬刻意。

型別檢查:2026-09-22 起 `store.py` 與 `server.py` 通過 mypy 嚴格模式。`Operation.params` 與 `expected_version` 都標成未驗證(`dict[str, object]` 與 `object`),`_next_state` 讀預算時用 `_is_plain_int` 收窄,型別檢查因此守得住這條不可信資料的路徑。順手修了兩個真的隱患:`cursor.lastrowid` 可能是 None(現在明確報錯並回滾),以及 `Operation.params` 與 `expected_version` 來自不可信請求,型別改標為未驗證的 `object`,由 `_validate` 用 `TypeGuard` 確認後才使用。伺服器的等待逾時改成直接設定連線的逾時,不再設定基底類別的類別變數。

2026-09-22:伺服器共通行為(Host 檢查、請求本文上限、逾時、JSON 錯誤與 500 後備)與 SQLite 連線及寫入交易已抽到 [[Systems/共用行程基礎]],DSP 只保留路由、錯誤對照表與故障注入;上面這些規則的實作與防回歸現在在那一篇。
連線建立階段(切換 WAL、建表)撞上鎖競爭,現在也回 503 store_busy(可重試),不再落到不可重試的 500;每個請求開的儲存連線在成功、領域例外與非預期例外三條路徑都會關閉,有測試守著(見共用行程基礎的測試)。
X-Fault 標頭的讀取與「沒開旗標就拒絕」也改由共用基礎提供(提案收件口共用同一套),DSP 只保留自己的故障模式清單。
