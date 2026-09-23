severity: major

## F1 重放條件漏了「同任務已有更新的修訂」這一格,跟既有待核可路徑的防護不對稱

severity: major
blocking: 是 — 照 spec 字面實作,重放指令會把一份已被同任務更新修訂邏輯上取代的死信提案原樣放回佇列重跑,而三項重放條件都攔不下它,兩項新執行前檢查(政策版本、決策新鮮度)也未必攔得下,在特定時序下會把已被取代的舊決策寫進 DSP,正是這個計劃要防的 F6。

引句:「條件:收件表裡那一列還在、處置是死信、提案還沒過期。任一不成立就拒絕重放,寫一列稽核說明原因」

引句:「當重放的提案還在收件表、處置是死信、而且還沒過期,重放指令應把它放回待處理並寫一列稽核;任一條件不成立應拒絕並寫一列說明原因的稽核。」

重放條件只有三項(還在收件表、處置死信、未過期),完全沒有「同任務有沒有更新的修訂」這一項。對照現有 Phase 6 增量 3 的待核可路徑,`_settle_awaiting`(file: `src/rtb/executor/execution.py:576`)在放行前明確先查 `has_newer_revision`(把撞到更新修訂的提案標成 `SUPERSEDED`,不重跑,見 `AwaitingOutcome.SUPERSEDED` 分支,file: `src/rtb/executor/execution.py:588-591`),再查同一把鍵是否已被另一份修訂開過嘗試(file: `src/rtb/executor/execution.py:592-595`)。`has_newer_revision` 這支函式本身就在 `inbox_store.py`(file: `src/rtb/executor/inbox_store.py:739-743`),是現成、已經在用的檢查,設計卻沒有把它接進重放條件。

具體例子(輸入 → 預期落差):
- 任務 T1 修訂 0 的提案 P0:campaign_id=C1、UPDATE_BUDGET、new_budget=200、campaign_version_observed=5、decision_created_at=t0。因投遞次數用完(例如連續 `DspUnavailable`,`_release` 不必等租約到期即可再次取件,幾秒內就能跑滿 5 次)在 t0+幾分鐘進死信,此時死信信封記下冪等鍵 K0(五欄含 new_budget=200 算出)。
- 分析端在死信發生後、決策仍新鮮(< 15 分鐘)時,針對同一任務送出修訂 1:new_budget=250(其餘欄位相同)。這份提案走一般流程被處理,假設因某個業務原因被擋下結案(未寫入 DSP,DSP 版本仍是 5)。修訂 1 的冪等鍵 K1(new_budget=250)與 K0 不同。
- 操作人幾分鐘後對 T1/修訂 0 下重放:三項條件(還在收件表、處置死信、未過期)全部成立 → 通過,放回待處理。
- 執行迴圈 `receive()` 取件:`attempt_store.latest(tx, operation_key(proposal))` 查的是 K0,K1 的存在完全查不到、不影響 K0(file: `src/rtb/executor/inbox_store.py` 的 `receive`,約行 592 起的 `existing = attempt_store.latest(...)`)。precheck 的 `VERSION_CHANGED` 檢查用的是 DSP 現況版本(仍是 5)對比 `campaign_version_observed`(5)→ 相符,不擋。政策版本沒變、決策新鮮度仍在 15 分鐘內 → 兩項新檢查也不擋。
- 結果:修訂 0 的舊決策(new_budget=200)被判定「一關不略」全部通過,寫進 DSP——即使分析端已經用修訂 1 重新算過這個任務、判定不該用 200 這個值。三項重放條件、兩項新執行前檢查,沒有一項能反映「這個任務已經有更新的修訂在管」這件事,而現成的 `has_newer_revision` 檢查明明已經存在、也已經被拿來處理幾乎一模一樣的情境(待核可提案撞到更新修訂)。

這不是無法實作的東西——現成函式就在同一支模組裡,只是重放條件的清單(S502 字面)沒有把它列進去。建議重放條件比照 `_settle_awaiting` 加一項:若 `has_newer_revision(tx, task_id, revision)` 為真,重放應拒絕(或直接確認成 `SUPERSEDED`/`BLOCKED`,不放回待處理),並寫一列稽核說明原因。

## 逐項核對:第 13 節七項重放時應重新驗證的項目

- 廣告現況與版本:沿用既有 `precheck()` 的 `CAMPAIGN_NOT_FOUND`/`CAMPAIGN_NOT_ACTIVE`/`VERSION_CHANGED`(file: `src/rtb/executor/execution.py:283-293`),重放的提案跟一般提案走同一段程式碼,沒有捷徑,已覆蓋。
- 決策到期:`_process` 進入時判一次、讀完 DSP 後再判一次、`_take` 開始一筆的交易裡又判一次(file: `src/rtb/executor/execution.py:395,401,662-664`),三處都覆蓋,重放沒有特殊待遇。
- 證據新鮮度:執行端本來就看不到證據,設計用「決策新鮮度 15 分鐘」當代理指標,並在文中自己承認這個替代關係(引句:「執行端看不到證據本身,用決策建立時間代替」)。核對分析端常數:`MAX_EVIDENCE_AGE = timedelta(minutes=15)`、`DECISION_LIFETIME = timedelta(minutes=30)`(file: `src/rtb/analyzer/policy.py:30-31`),跟設計文中引的數字一致,這個折衷有講清楚代價與理由,不算遺漏。
- 政策版本:新執行前檢查比對 `rtb.domain.proposal.POLICY_VERSION`,對應 [S504],`Proposal` 本身已有 `policy_version` 欄位(file: `src/rtb/domain/proposal.py:58`),資料齊全,可以實作。
- 權限與核可:重放開的是全新嘗試(冪等鍵之前沒有嘗試紀錄),會走 `_run`→`_gate`→`_approvals` 一般核可查核路徑(file: `src/rtb/executor/execution.py:413-447`),等同處理一筆全新提案,沒有繞過核可判斷。
- 總曝險額度:走 `_take` 裡 `attempt_store.begin()` 的預留與門檻判斷(file: `src/rtb/executor/execution.py:660-694`),同樣是一般路徑,重放沒有特殊入口。
- 先前操作的結果:靠 `receive()` 裡「這把鍵已有嘗試紀錄就確認、不重跑」的既有機制(`existing = attempt_store.latest(tx, operation_key(proposal))`)涵蓋,重放沒有繞過這一步——但正如 F1 所述,這只保護「同一把鍵」被重複處理,保護不到「同一任務、不同鍵的更新修訂」這個情境,兩者是不同的安全屬性。

七項裡,前六項各自都有明確攔阻它的關卡且經程式追蹤確認;第七項的「同鍵」半邊有機制、「同任務跨修訂」半邊沒有——這正是 F1 指出的洞。

## 「放回待處理」之後的取件與處理路徑

追過 `process_one`(file: `src/rtb/executor/execution.py:360`)到 `_process`/`_run`/`_take`/`_record` 全程:取件 `receive()` → 過期判斷(兩次)→ 讀 DSP → `precheck`(含未來要加的兩項)→ 簽發 → `_gate`(核可查核)→ `_take`(續租、過期再判、核可是否被替換、總曝險預留 `attempt_store.begin`)→ 寫入 DSP。整段沒有任何一處讀取或判斷是被「這是重放來的」這個事實跳過的——因為執行迴圈壓根不知道、也不需要知道一筆提案是不是重放來的:重放只是把 `disposition` 清空、`deliveries` 歸零、租約清空,交出去的提案跟全新提案在資料結構上沒有差異。設計原文「重放沒有任何捷徑或旗標讓哪一關略過」在程式碼層級成立,沒有找到反例。另外兩個對帳重送入口 `_after_expiry`(file: `src/rtb/executor/execution.py:790`)與 `_reconcile_not_found`(file: `src/rtb/executor/execution.py:956`)也都呼叫同一支 `precheck()`,所以「兩項新檢查對所有提案生效,不只對重放」這句話在對帳重送路徑上也站得住。

## 重放的並行

兩個人同時重放同一列:`InboxStore` 的既有慣例是「查後寫在同一個 `BEGIN IMMEDIATE` 交易內完成」(見模組頂端說明,file: `src/rtb/executor/inbox_store.py` 開頭 docstring),`accept`/`receive`/`_held`/`_finish`/`settle_awaiting` 全部照這個模式;只要重放指令的「查條件」與「寫回待處理」比照這個慣例包在同一個交易裡(設計文字「同一個交易裡把那一列放回待處理」暗示如此,但沒有像 S502 那樣把「查是同一交易」明講成字面要求),兩個並行呼叫會被 SQLite 的寫入鎖序列化:先到的一個成功把處置改掉,後到的一個查條件時「處置是死信」已經不成立,自然落入「任一不成立就拒絕」那支路徑,不會產生雙重效果。這點屬於慣例延伸出來的推論,不是設計文字明講的合約,值得在合約裡把「查與寫同一交易」講白,但目前程式慣例夠一致,先不升成單獨一條 finding。

重放同時保留期清理:`_purge_finished_tasks`(file: `src/rtb/executor/inbox_store.py:521-534`)只在 `accept()`(收新提案時)呼叫,不是背景排程,而且清除條件是「整個任務 `SUM(OPEN) = 0`」,`OPEN` 包含 `PENDING`(file: `src/rtb/executor/inbox_store.py:148-152`)。兩種時序都推演過:重放交易先於清理——那一列的處置已經是 `NULL`(待處理),`SUM(OPEN)` 不為 0,清理的 SQL 條件不成立,不會被清掉;清理先於重放——那一列已被整個任務一起刪除,重放的「還在收件表」條件檢查不到那一列,依設計文字直接落入「已過保留期被清掉」的拒絕分支(這句話設計原文已經寫明)。兩種順序都不會产生半殘資料,沒有找到問題。

## 待處理上限

設計文字已經明講:「通過:同一個交易裡把那一列放回待處理...待處理上限滿了照收件口既有規則拒絕,也寫稽核」,對應到現有 `_check_capacity`(file: `src/rtb/executor/inbox_store.py:518-520`)的既有邏輯,只是套用在重放這個新入口上,沒有遺漏。

## 總結

七項重新驗證裡六項半有著落,唯一的洞是「同任務已有更新的修訂」沒有被列進重放條件,而且現成的 `has_newer_revision` 函式已經在另一條路徑上做過幾乎一樣的事,設計沒有把它接進來(F1,major,blocking)。放回待處理之後的執行路徑本身沒有找到捷徑或漏判關卡。並行(雙重重放、重放對保留期清理)在既有交易慣例下是安全的,但「查條件與寫回待處理要在同一個交易裡」這件事設計文字只是暗示,沒有像其他合約句一樣明講,值得在下一版寫清楚。
