# B2 修正結果(2026-10-03,工作目錄 /Users/enzo/rtb-lumos-update)

行號是稽核時(HEAD 29f1c04)的行號。

## 逐列處置

Projects/RTB_Phase4佇列與重新投遞_計劃:505–506 | X9 | 已修(本篇部分) | 「另掛日期保底:」改成「另掛了一條日期保底的回頭條件。」,後面加 2026-10-03 更正括號:事件 2026-09-24 已發生(新增 src/rtb/analyzer/runner.py)、那次計劃沒補合約、它只推進已建立的任務沒有訊息入口、日期保底的回頭條件 10-03 已撤(用內容指稱「本節『2026-10-03 撤除一條回頭條件』那則註記」),「這不是還在等的事件入口」。執行迴圈:22 與 Phase4驗收紀錄:40 不在我的範圍,列進轉給別篇 | git log --diff-filter=A runner.py → 8ff8c95 2026-09-24;runner.py 只有 argparse 的 --db/--dsp-url/--inbox-url 等,沒有訊息入口;git show aef0f5d 確認刪了 REVISIT:[when-file:src/rtb/analyzer/runner.py][by:2026-12-31]
Projects/RTB_Phase4佇列與重新投遞_計劃:491 | X9 | 已修 | 10-02 括號後加 2026-10-03 更正:F1–F3 驗證檔 10-03 改成已取代,三條連結也從執行迴圈拿掉,現在執行迴圈與提案收件口都沒有;F1–F3 合約行仍在執行迴圈 | grep -c "Verification/事故F" 執行迴圈、提案收件口 都是 0;事故F1/F2/F3 三篇 status: superseded;執行迴圈 摘要有 F1、F2、F3 的 ★INVARIANT★ 行;git show aef0f5d 刪了五條 verified_by
Projects/RTB_Phase4佇列與重新投遞_計劃:5 | F1 | 已修 | lumos set updated 2026-10-03 | set 成功
Issues/存量筆記漂移等工具修復:160 | X9 | 已修 | 直接改正 10-03 那個寫錯的括號:裁定是「費用欄位拿掉、註解與兩支測試名改掉」;aef0f5d 拿掉費用欄位、改了程式註解與佇列那支測試名;HTTP 用戶端那支被工具誤擋暫緩(指到〈落實 15 項裁定時撞到的工具誤擋〉);flow.py 模組說明另一處「三個介面用 typing.Protocol」那次漏改、10-03 稍後的清理補改 | tests/httpclient/test_httpclient.py:143 舊名還在;tests/executor/test_queue.py:106 已是 test_an_old_inbox_database_migrates_to_the_current_dispositions_without_losing_data;HEAD 的 flow.py:30 仍是「三個介面」,工作區(協調者未提交)已改成「這些介面」。**注意:括號寫「10-03 稍後的清理補改」是照協調者工作區的改動寫的,若 flow.py 這處最後沒提交,這句要改回**
Issues/存量筆記漂移等工具修復:166–167 | W1 | 已修 | 166 改成「當時這條配方證明不了什麼」;REVISIT 2026-10-15 拿掉,換成 2026-10-03 已處理說明:aef0f5d 改綁 test_an_approval_does_not_hold_for_a_proposal_from_another_policy_version(tests/executor/test_stale_decision.py)、配方見執行迴圈、已提交版本實跑 killed;另記我在暫存副本自己重跑的結果 | 執行迴圈 kill_recipes 有 test=…another_policy_version、old=`and proposal.policy_version == POLICY_VERSION`;我在 scratchpad/b2mut(git archive HEAD)跑:原樣 1 passed,照配方改壞後 1 failed
Issues/存量筆記漂移等工具修復:20–21 | H1 | 已修 | 〈現況〉改成兩條:「2026-09-28 開這篇時」(原句保留)+「2026-10-03 回頭看」:點名的四處都已處理、剩工具抓不到的形狀靠清理循環,指到具名小節,不用「下面各節」 | 一鍵展示.md:41 模型變數帶 [count:…];分析行程流程與檢查點.md:165 增量 2b 句已改寫並帶 [count:…Cell=9];展示頁面.md:88 Phase 13 增量 4 節有 [retired:2026-09-27 b2fc512];事故F1–F3 status superseded
Projects/RTB_Phase15AI找規則模式_計劃:188 | X9 | 已修 | 「見 [[Systems/規則模式探索評估]]」後加 2026-10-03 更正:用詞在 governance/eval/phase15-rule-mining.md 與 src/rtb/eval/rule_mining_report.py(那支檔的家才是該 Systems 篇),附重查指令;updated 設 2026-10-03 | grep 只命中 phase15-rule-mining.md:206、rule_mining_report.py:609;Systems/規則模式探索評估 about_code 含 rule_mining_report.py
Verification/Phase7驗收紀錄:30 | S3 | 已修 | 句後加 2026-10-03 更正:三種是 09-23 當時;2026-09-27 提交 b2fc512(Phase 14 正式規則改照九條)起綁定測試斷言 DSP 五種讀取加送件共六種,附重查指令 | git log -L EXPECTED_ENDPOINTS:b43e252(09-23)三個 → b2fc512(09-27)六個;test_f5_end_to_end.py:37–38、:154
Verification/Phase11B增量1驗收紀錄:23 | X9 | 已修 | 直接改正 10-02 括號:寫明是「開頭重驗事件原本寫的『11B 增量 2 完成時改寫成整份 11B 驗收紀錄』」,補一句 10-03 已從重驗事件拿掉、同件事改記在效期欄「2026-10-03 補」那條(用內容指稱,不寫第幾項) | git log -p:aef0f5d 刪了 revalidate_when 那句,valid_under 加了「2026-10-03 補:11B 增量 2 併進 Phase 13 增量 1…」
Verification/F7效能驗收紀錄:56 | P2 | 已修 | REVISIT 2026-10-31 拿掉,換成 10-03 回頭看:「推上 CI 記時間」09-25 已做(〈CI〉節);「CI 比 23 秒基準慢兩成就拆」比法不成立(本機對 CI,CI 慢 1.5–2 倍);代碼審第 2 輪後本機慢兩秒多的原因沒拆,往後由〈CI〉節那條 2026-10-31、整行超過 110 秒的回頭條件盯 | 同篇〈CI〉節:五次估計約 30/34/33/35/46 秒,「CI 慢約 1.5 到 2 倍」;〈CI〉節 REVISIT:2026-10-31 仍在
Verification/事故F6_死信重放必須重新驗證:5 | F1 | 已修 | lumos set updated 2026-10-03 | git log -1 aef0f5d 2026-10-03
Verification/事故F4_舊版本寫入被拒不覆蓋:5 | F1 | 已修 | lumos set updated 2026-10-03 | git log -1 aef0f5d 2026-10-03

其餘分到的筆記(Projects/RTB_Phase8死信重放與過時決策_計劃、Verification/Phase13增量4驗收紀錄、Systems/規則模式探索模型入口、Issues/竄改錄製檔外殼深巢狀讓錄製讀取當掉)B2 沒有發現列,沒改。

## 轉給別篇

1. Systems/執行迴圈:22(事故 F3 的 WHY):「這條合約仍沒補(回頭條件在 [[Projects/RTB_Phase4佇列與重新投遞_計劃]] 增量 3b 的 REVISIT 2026-12-31)」→ 改成「這條合約仍沒補;原本掛在 Phase4 計劃增量 3b 的回頭條件 2026-10-03 使用者裁定撤掉,目前沒有排程」。理由:aef0f5d 已刪那條 REVISIT,現在指向不存在的回頭條件,而 Phase4 計劃的撤除註記又指回這裡。
2. Verification/Phase4驗收紀錄:40:括號裡「回頭條件改掛在 [[Projects/RTB_Phase4佇列與重新投遞_計劃]] 的 REVISIT」→ 再加更正或改括號:「那條回頭條件 2026-10-03 已撤、目前沒有排程」。同上理由。
3. Verification/Phase4驗收紀錄:6(revalidate_when 最後那件「分析行程加啟動程式時,補『同一則訊息穩定對應同一個任務編號』的合約」):事件 2026-09-24(runner.py,8ff8c95)已發生、合約沒補、10-03 裁定不排程。照規則 6 處理:拿掉或改綁「分析端有訊息入口時」這類還沒發生的事件,並在 valid_under 記一句現況。
4. Systems/執行迴圈:184〈F7 政策版本那條殺傷力配方改綁〉的 PITFALL 根因,和同篇 kill_recipes 那條 another_policy_version 配方的 note(「端到端路上執行前檢查會先擋」):**根因寫錯**。端到端測試 test_an_approval_is_void_across_policy_or_tenant_changes 只 monkeypatch `approval.POLICY_VERSION`,execution.py:363 用的是自己匯入的 POLICY_VERSION,沒被改,所以執行前檢查在這支測試裡不會擋;擋住的是 approval.py:62 範圍指紋裡的 "policy_version": POLICY_VERSION。證據(scratchpad/b2mut,git archive HEAD):只改壞 approval.py:125 → 這支測試 1 passed(殺不掉);再拿掉 :62 的指紋欄位 → 1 failed。Issues/存量筆記漂移等工具修復:166 的 10-01 說法「核可另有範圍指紋也含政策版本,兩道各自擋得住」才對。建議把 PITFALL 根因改成「核可的範圍指紋也含政策版本,端到端測試換版時兩道一起變,只拆比對那道照樣擋」,配方 note 同步改(配方 note 改了要重跑 guard kill 嗎?note 不影響 old/new,應不用)。

## 程式要改

1. src/rtb/analyzer/flow.py:229 `def advance(  # noqa: PLR0913 - 三個可替換介面加時間、中斷鉤子、租約擁有者與操作查詢`:「三個可替換介面」跟模組說明第 7 行「起初三個,後來加了操作查詢、不提案原因、規則輪」不一致,這是第三處。建議看 advance() 實際參數改成「可替換介面(蒐證、決策、送出…)加時間…」或去掉數字。
2. src/rtb/analyzer/flow.py:30:協調者工作區已改成「這些介面」(未提交);Issues/存量筆記漂移等工具修復:160 的括號依此寫「10-03 稍後的清理補改」,要跟著提交。
3. tests/httpclient/test_httpclient.py:143 test_client_headers_are_exactly_idempotency_key_and_capability:仍待工具誤擋修好後改名(已有 REVISIT 2026-10-15 在 Issues/存量筆記漂移等工具修復),不是這輪新增。

## lint

- Projects/RTB_Phase4佇列與重新投遞_計劃:0 問題
- Issues/存量筆記漂移等工具修復:0 問題
- Projects/RTB_Phase15AI找規則模式_計劃:0 問題
- Verification/Phase7驗收紀錄:0 問題
- Verification/Phase11B增量1驗收紀錄:0 問題
- Verification/F7效能驗收紀錄:0 問題
- Verification/事故F6_死信重放必須重新驗證:0 問題
- Verification/事故F4_舊版本寫入被拒不覆蓋:0 問題
- 沒改的四篇(Phase8 計劃、Phase13增量4驗收紀錄、規則模式探索模型入口、竄改錄製檔問題單):0 問題
