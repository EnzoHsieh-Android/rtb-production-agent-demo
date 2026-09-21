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
verified_by:
  - "[[Verification/Phase1驗收紀錄]]"
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
- 測試的白箱部分(對 `_record_idempotency`、`_conn` 打補丁)綁定私有成員,重構時會跟著紅,屬刻意。

型別檢查:2026-09-22 起 `store.py` 與 `server.py` 通過 mypy 嚴格模式。`Operation.params` 與 `expected_version` 都標成未驗證(`dict[str, object]` 與 `object`),`_next_state` 讀預算時用 `_is_plain_int` 收窄,型別檢查因此守得住這條不可信資料的路徑。順手修了兩個真的隱患:`cursor.lastrowid` 可能是 None(現在明確報錯並回滾),以及 `Operation.params` 與 `expected_version` 來自不可信請求,型別改標為未驗證的 `object`,由 `_validate` 用 `TypeGuard` 確認後才使用。伺服器的等待逾時改成直接設定連線的逾時,不再設定基底類別的類別變數。

2026-09-22:伺服器共通行為(Host 檢查、請求本文上限、逾時、JSON 錯誤與 500 後備)與 SQLite 連線及寫入交易已抽到 [[Systems/共用行程基礎]],DSP 只保留路由、錯誤對照表與故障注入;上面這些規則的實作與防回歸現在在那一篇。
連線建立階段(切換 WAL、建表)撞上鎖競爭,現在也回 503 store_busy(可重試),不再落到不可重試的 500;每個請求開的儲存連線在成功、領域例外與非預期例外三條路徑都會關閉,有測試守著(見共用行程基礎的測試)。
X-Fault 標頭的讀取與「沒開旗標就拒絕」也改由共用基礎提供(提案收件口共用同一套),DSP 只保留自己的故障模式清單。
