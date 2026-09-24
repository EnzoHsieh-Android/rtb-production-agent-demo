severity: major

F1 展示情境沒有自己的結果斷言，錯誤執行仍會顯示「照預期跑完」
severity: major
blocking: 是
引句:「F1–F7 的「確定性通過或失敗斷言」不由驅動程式判」
驗證器只執行清單指定的既有測試節點，沒有讀取這次展示產生的狀態、資料庫或故障注入紀錄：`/Users/enzo/rtb-mainwt/tools/verify_claims.py:1260`、`/Users/enzo/rtb-mainwt/tools/verify_claims.py:1264`。
具體例：F1 展示啟動時漏載故障排程，請求直接順利提交 → 驅動程式沒有情境斷言，只因流程結束便標成「照預期跑完」，而驗證器另外跑測試夾具仍可通過 → 應把這次展示是否真的出現結果不明、對帳與同鍵重送作為 F1 自己的完成條件。
建議：每個展示情境建立針對該次暫存資料庫與操作歷史的結果 oracle；只有 oracle 通過才能標 DONE，否則標「展示結果不符預期」。Phase 11 驗證器應是另一個證據徽章，不能代替本次展示斷言。

F2 「同時只准一次」沒有原子取得機制，兩個同步 POST 可以一起啟動
severity: major
blocking: 是
引句:「同時只准一次展示在跑:在跑時任何觸發都回「已有展示在跑」」
共用伺服器是逐請求開執行緒的 `ThreadingHTTPServer`：`src/rtb/httpkit.py:17`、`src/rtb/httpkit.py:41`。S1006 只測「已經在跑」後的第二次觸發，沒有覆蓋兩個請求同時觀察到閒置的競態。
具體例：兩個 `/run` 請求在柵欄後同時進入 → 兩條處理緒都在 running 被設為真以前讀到假，各自啟動一套驅動程式 → 應只有一個請求原子取得展示席位，另一個立即回現有進度。
建議：用鎖保護「檢查閒置→建立 demo_id→設成 STARTING」的整段原子轉換，啟動失敗與結束都在 finally 釋放；新增以 Barrier 同時送出多個 POST、斷言驅動程式恰好啟動一次的測試。

F3 核可只綁提案雜湊，沒有綁展示編號與使用者實際確認的數字快照
severity: major
blocking: 是
引句:「送出時伺服器重新核對每個確認框都勾了、而且提案雜湊對得上這次展示待核可的那一筆」
既有核可憑證綁提案、關卡、範圍指紋與 max_increase，但沒有 demo_id、顯示收據或畫面數字快照：`src/rtb/executor/approval.py:85`。有效性檢查同樣不核對核可者看過的數字：`src/rtb/executor/approval.py:121`。表單欄位值固定為 1，因此 POST 本身也沒有攜帶被確認的數值。
具體例：頁面顯示「剩餘額度 100、此次增加 90」，之後其他提案改變額度或設定，而這份提案雜湊不變 → 舊頁面的勾選仍通過，伺服器簽出基於另一份現況的核可 → 應拒絕已過時的核可畫面，要求重新顯示並確認新數字。
建議：產生一次性 approval-view nonce，伺服器端綁定 demo_id、提案雜湊、關卡、程式數字的正規化雜湊、模型文字雜湊與顯示時間；POST 原子核對並消耗 nonce，重新計算目前數字，不一致就拒絕。收據識別碼也應進入核可稽核鏈。

F4 流程圖的窮舉測試漏掉真正控制對帳與轉人工的結果型別
severity: major
blocking: 是
引句:「系統的結果列舉每一個成員,在流程圖裡都要對得到一條邊或一個節點」
S1017 列出的型別沒有包含 `AttemptState`、`OutcomeCode`、`VoidOutcome` 與執行迴圈 `Result`。這些型別實際控制結果不明、驗證失敗、轉人工與能力憑證拒絕：`src/rtb/domain/attempt.py:44`、`src/rtb/domain/attempt.py:76`、`src/rtb/executor/execution.py:264`、`src/rtb/executor/execution.py:310`。
具體例：DSP 回能力憑證拒絕，執行端走 `VoidOutcome.ESCALATED` 與 `OutcomeCode.CAPABILITY_REJECTED` → 目前列出的完整性測試即使流程圖完全沒有「轉人工／憑證拒絕」邊仍可通過 → 應由測試要求該結果有明確節點、邊與白話說明。
建議：先盤點所有會改變控制流或終態的封閉列舉及反應表，讓完整性測試直接從這些型別取成員；至少補入上述四組，以及分析端 `RoutePath`、`NoActionReason`。不要用人工維護的部分型別清單宣稱全流程完整。

F5 設計指定的資料來源沒有保存分析判斷原因，頁面無法如實還原「為什麼」
severity: major
blocking: 是
引句:「每個情境的判斷紀錄從追蹤檢視、生命週期事件與稽核組出來」
分析歷史表只保存狀態、提案與錯誤，沒有判斷原因欄：`src/rtb/analyzer/task_store.py:48`。程式雖能算出多種 `NoActionReason`，正式 `decide` 只回傳 decision、丟掉 reason：`src/rtb/analyzer/policy.py:203`、`src/rtb/analyzer/policy.py:252`。
具體例：缺少狀態證據與配速不低都會落成同一個 NO_ACTION 狀態 → 展示端只讀追蹤與生命週期，無法知道當時走的是哪個判斷分支，只能猜測或用新版程式重算 → 應顯示當時決策點實際留下的原因。
建議：在做出判斷時原子寫入封閉列舉的 decision point、outcome、taken edge、reason code 與必要輸入摘要；展示只翻譯這筆持久事實，不事後從終態推測。缺證據時應明示「無法還原」，不可生成看似確定的理由。

F6 展示故障工具放進正式套件，現有單向匯入禁令阻止不了直接使用
severity: major
blocking: 是
引句:「這些手段照 F1–F7 端到端測試的組法,只存在展示套件裡;正式程式與其他套件都不准匯入展示套件」
計劃把猝死點、時鐘偏移與開啟故障旗標的啟動器放在 `src/rtb/demo/launch.py`；S1002 只禁止其他套件匯入它，沒有禁止人或其他程式直接匯入／執行展示套件，也沒有要求它只能操作本次新建的暫存根目錄。正式 HTTP 基礎本身含可造成不回應的故障原語：`src/rtb/httpkit.py:37`；DSP 與收件口也公開 `--fault-injection`：`src/rtb/dsp/server.py:334`、`src/rtb/executor/inbox_server.py:181`。
具體例：另一段正式側程式或操作人直接呼叫 `rtb.demo.launch`，把既有資料庫路徑交給它並開猝死／時鐘偏移 → S1002 仍通過，因為沒有任何正式模組匯入 demo → 應從結構上保證故障工具無法指向展示驅動程式剛建立範圍以外的資源。
建議：把故障 harness 放到不隨正式套件提供的 tools/tests 邊界；若必須留在 `src`，啟動器不得接受任意資料庫或服務位址，必須持有 driver 建立的一次性能力物件並驗證所有路徑都在該次安全建立的暫存根目錄內。另加直接執行、動態匯入與非展示路徑的拒絕測試，不能只掃反向 import。

F7 單一情境顯示舊驗證器結果，但資料介面沒有舊結果的時間或來源展示編號
severity: major
blocking: 是
引句:「單一情境重跑不跑驗證器,頁面照舊顯示上一次的驗證器結果並標時間」
凍結介面的 `VerifierResult` 只有 passed、lines、reasons，沒有 verified_at 或來源 demo_id；`DemoState.started_at` 則是這一次展示的時間。照字面無法標出「上一次」的時間與身份。
具體例：上午完整展示驗證通過，下午修改工作樹後只重跑 F3 → 頁面顯示上午的通過結果，卻只能同時呈現下午這次展示的 started_at → 使用者可能把舊綠燈誤認為目前版本／目前展示已驗證；應明確標成哪一次、哪個提交、何時產生的舊證據。
建議：為驗證結果加入 `verified_at`、`source_demo_id`、`commit`、驗證器雜湊與清單雜湊，並測試單一重跑絕不把當前展示時間貼到舊結果上；若無法完整保留來源，就不要在單一重跑頁顯示舊綠燈。

7 條,blocking 7。