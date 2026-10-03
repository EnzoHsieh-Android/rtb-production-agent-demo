# B8 修正結果(2026-10-03,工作目錄 /Users/enzo/rtb-lumos-update)

## 逐列處置

筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據
---|---|---|---|---
Projects/RTB_Phase11B大模型接入_計劃:95 | X9 | 已修 | 2026-10-02 那個寫錯的更正括號直接改正:錄製重播記帳時後端種類記 `recording`,列舉有 `claude_code` 與 `recording` 兩個成員 | src/rtb/modelledger_view.py:73-77 `Backend` 兩成員;modelledger.py:190 錄製記 `Backend.RECORDING`;modelclaude.py:611 記 `CLAUDE_CODE`
Projects/RTB_Phase11B大模型接入_計劃:198、204 | H2 | 已修 | 兩處 10-02 更正後各補「(已被取代:使用者 2026-10-03 改裁成說明不進核可表單,顯示收據不做,見本篇決策紀錄)」;198 那處另連 [[Issues/確認頁顯示AI說明時的收據還沒做]] | 同篇 decisions d1(decided 2026-10-03);該 Issue status resolved(7732b7e);src/rtb/demo/page.py:1090-1093 表單寫「這張表單不帶 AI 說明」
Projects/RTB_Phase11B大模型接入_計劃:363 | P1 | 已修 | REVISIT 行改成「(2026-10-03 撤除一條回頭條件:…事情已處理…)」的說明句,保留「已確認(2026-09-25…)」子行當歷史 | src/rtb/modelcore.py:67 `OUTPUT_RECOVERY_ATTEMPTS = 3`、:318-319 預留照 1+續寫次數算;同篇「已確認」子行記使用者 09-25 裁定
Projects/RTB_Phase11B大模型接入_計劃:5 | F1 | 已修 | `lumos set … updated 2026-10-03` | 本次有改動;d1 decided 2026-10-03
Systems/分析行程流程與檢查點:180 | D1 | 已修 | 系統筆記直接改句:「請模型寫一段只給人看的說明(放在報告與展示頁的情境明細,不進人工核可表單,見 Phase 11B 計劃決策紀錄)」 | src/rtb/analyzer/narrate.py 檔頭「只給人看…人工核可表單也不帶說明」;page.py:1090-1093
Systems/分析行程流程與檢查點:158 | S1 | 已修 | 改成「當初為追蹤檢視開的(出處 Phase 9 增量 1);現在的使用者以程式碼為準:`rg -n "TaskReader\(" src/rtb`(2026-10-03 查,扣掉類別定義有六處:追蹤檢視、指標、服務水準、一鍵展示的驅動程式、展示觀察器、展示錄製入庫前檢查)」 | `grep -rn "TaskReader(" src/rtb` 7 行(含 task_store.py:1009 類別定義):ops/trace.py:272、ops/metrics.py:440、ops/sli.py:209、demo/driver.py:463、demo/observe.py:321、demo/recordings.py:124
Systems/分析行程流程與檢查點:134 | H2 | 已修 | 改成「『不允許』是權限類擋下原因合併後的泛稱:Phase 5 起合併超過預算上限與廣告不屬於租戶,Phase 6 再併進單次加額過大與總曝險已滿,現況共四種(以程式碼為準:[[Systems/提案收件口]] 那支伺服器的 `_PERMISSION_BLOCKS`)」 | src/rtb/executor/inbox_server.py:135-139 四成員,註解標 Phase 6;inbox_server.py 的家是 Systems/提案收件口
Systems/分析行程流程與檢查點:66 | S3 | 已修(範圍比原判窄) | 改成「狀態機的去處(狀態共十個[count:…TaskState=10];這支測試直接驗到哪些狀態以程式碼為準:`grep -o "TaskState\.[A-Z_]*" tests/analyzer/test_flow.py \| sort -u`,2026-10-03 查時完成、擋下、被取代三個終點不在這支裡)」。沒照原建議只把 9 改 10:原句「每一種去處」也不成立,test_flow.py 裡 COMPLETED/BLOCKED/SUPERSEDED 出現 0 次 | src/rtb/domain/task_state.py:11-21 十個成員;`grep -c "TaskState.COMPLETED\|BLOCKED\|SUPERSEDED" tests/analyzer/test_flow.py` 都是 0,大小寫不分也 0
Systems/分析行程流程與檢查點:6 | X9 | 已修(措辭比原建議改正) | responsibility 補「另有模型說明的領取表與結果表」「提案的模型說明命令列(narrate.py,說明只給人看、不進人工核可表單)」「調查詞彙與純計算(investigation.py:四種唯讀查詢選項與收據算法,規則輪、評估與展示都讀它,這支不碰模型)」。原建議寫「只供評估與展示讀」不成立:正式路徑的 rule_round.py、instrumented.py 都匯入它 | rule_round.py:31、:59-71(STEP_QUERIES 用 inv.QueryOption);instrumented.py:16、:125-133(receipt_evidence);investigation.py:41-47 QueryOption 四成員;task_store.py:92、:95 narrative_claims / narrative_results
Systems/分析行程流程與檢查點:5 | F1 | 已修 | updated 2026-10-03 | 本次有改動
Verification/Phase4驗收紀錄:40 | X9 | 已修 | 同句那個 10-02 寫錯的更正括號直接改正:「Phase 4 計劃替這件事掛的日期保底回頭條件已於 2026-10-03 撤除、目前沒有排程,見 Phase 4 計劃〈使用者覆核(2026-09-23,第 1 輪設計審帶出)〉那節的撤除註記」 | Phase 4 計劃第 506 行撤除註記;該篇現存 REVISIT 只有 129、327、330、515 行,都跟訊息入口無關(原判寫〈審計修正〉末段,實際小節名是〈使用者覆核…〉)
Verification/Phase4驗收紀錄:5-6 | V1 | 沒修:要人裁 | 協調者交代:「分析行程加啟動程式時補合約」那一項算不算已發生,使用者還沒裁定,開頭欄位 valid_under / revalidate_when 都沒動 | runner.py 8ff8c95(2026-09-24)起存在、只推進已建立任務、沒有訊息入口(事實已驗,等裁定再寫)
Verification/Phase12增量1驗收紀錄:46、48 | P2 | 已修 | 第 46 行後加「(2026-10-03 更正:已修並上 main——936d5b0 把縮小版 F7 情境總時限 3 秒放寬到 120 秒,等人確認不算時限改用 1.5 秒短時限測試驗;節點筆數 11 對 12 是被時限切斷、不是等待條件漏等,不用改程式,見 Systems/一鍵展示 記 CI run 36013610073 的 PITFALL)」;REVISIT 行改成撤除說明句(已結案驗收紀錄、事情已處理) | `git show 936d5b0 -- tests/demo/test_driver.py`(3 → SMALL_F7_LIMIT_SECONDS=120、新增 1.5 秒測試);`git branch -r --contains 936d5b0` → origin/main;Systems/一鍵展示 第 71 行 PITFALL「不是等待條件漏了」
Verification/Phase12增量1驗收紀錄:25 | S3 | 已修 | 歷史句不改寫,句後加「(2026-10-03 更正:是小於 0.1 秒才拒絕,剛好 0.1 秒放行;推送當時 eade8e7 也是這樣…)」 | src/rtb/analyzer/runner.py:40、:87 `t >= MIN_TIMEOUT_SECONDS`;`git show eade8e7:src/rtb/analyzer/runner.py` 第 77 行同式
MOC/index:4 | F1 | 已修 | updated 2026-10-03 | `git log -- MOC/index.md` → 7732b7e、aef0f5d 都是 2026-10-03
Systems/可觀測查詢:6 | F1 | 已修 | responsibility 改成含「人工核可計數(查詢三:待核可數、接不到停下紀錄的待核可數、已核可放行數,增量 3 合進後補上)」,「不負責」改成「人工核可的簽發與放行(增量 3 的核可模組)」;updated 2026-10-03 | src/rtb/executor/observability.py:1-2、:100-117(ApprovalCounts 三欄 awaiting / awaiting_unknown_tenant / applied)
Systems/服務水準與燒損告警:6 | F1 | 已修 | responsibility 改成前段幾支「只讀」,另管異常原因假說命令列(告警響時才經模型用戶端呼叫模型、只給建議;業務資料庫一律只讀,經模型用戶端記花費帳);updated 2026-10-03 | src/rtb/ops/hypothesis.py 檔頭([S910] 沒響不呼叫、[S912] 唯一寫入是花費帳)、:281 call_model、:284-286 ledger
Issues/F7端到端在CI上偶爾超過60秒:77 | P1 | 已修 | 已結案 Issue 上的 REVISIT 改成撤除說明句:上限已放寬到 120 秒,「60 秒會紅」前提不在;往後 CI 耗時由 [[Verification/F7效能驗收紀錄]]〈CI〉節的回頭條件盯(整行超過 110 秒拆開量);updated 2026-10-03 | tests/executor/test_f7_end_to_end.py:97 `< 120`;F7效能驗收紀錄第 70 行 REVISIT:2026-10-31 110 秒門檻
Issues/執行迴圈收到SIGTERM等於硬殺:21 | P2 | 已修(要不要提早修記成要人裁) | REVISIT 只留還沒發生的「執行迴圈改走正式啟動器」觸發(日期 2026-12-31 不變);下面加改寫說明:「呼叫紀錄用在斷言或指標」寫下時就已成立(追蹤檢視 98f247b 03:22、指標 c5a34da 05:19 都早於 0b2499a 18:43;展示觀察器 4cab35e 21:09 也讀),附查詢指令;寫明缺口仍在、SIGTERM 停下會讓指標/追蹤/觀察器少算,要不要因此提早修要人裁;updated 2026-10-03 | `git log -S"dsp_calls_for" -- src/rtb/ops/trace.py`、`-S"dsp_calls" -- src/rtb/ops/metrics.py`、`-S"dsp_calls_for" -- src/rtb/demo/observe.py`;executor/runner.py:97-109 finally 呼叫 flush_calls 寫 dsp_calls 表(attempt_store.py:77、:1093、:1211 讀同一張表);`grep -rn "signal\.signal\|add_signal_handler" src/rtb/executor` 0 筆;launcher/__init__.py:199-204 stop() 先送 SIGTERM

統計:已修 18 列(含兩列措辭比稽核員的建議更窄或改正過);沒修 1 列(要人裁:Phase4 驗收紀錄開頭欄位)。另有一列(SIGTERM)已修,但其中「要不要提早修」記成要人裁。

原判部分有誤(已照驗證結果修,沒照原建議抄):
- 分析行程:66 原建議只把 9 改 10;原句「每一種去處」也不對,test_flow.py 沒涵蓋完成、擋下、被取代三個狀態。
- 分析行程:6 原建議寫 investigation.py「只供評估與展示讀」;正式路徑的規則輪(rule_round.py)與 instrumented.py 都匯入它。
- Phase4 驗收紀錄:40 原建議指〈審計修正〉末段;撤除註記實際在 Phase 4 計劃〈使用者覆核(2026-09-23,第 1 輪設計審帶出)〉那節。

## 轉給別篇

1. Systems/執行迴圈 第 22 行(事故 F3 的 WHY):「回頭條件在 [[Projects/RTB_Phase4佇列與重新投遞_計劃]] 增量 3b 的 REVISIT 2026-12-31」指向的 REVISIT 已在 2026-10-03 撤除(Phase 4 計劃第 506 行)。系統筆記直接改句成「那條日期保底回頭條件 2026-10-03 已撤除、目前沒有排程,見 Phase 4 計劃〈使用者覆核(2026-09-23,第 1 輪設計審帶出)〉」。另外「算不算已發生」等使用者裁定(跟 Phase4 驗收紀錄開頭欄位同一題)。
2. Projects/RTB_Phase12一鍵展示與HTML報告_計劃 第 45 行:10-02 更正寫「補做或撤掉待使用者裁定」,已過時;補「(已被取代:使用者 2026-10-03 改裁 AI 說明不進核可表單,收據不做,見 Phase 11B 計劃決策紀錄)」。第 342 行的更正括號同樣要補一句收據不做。第 98、199、339 行是原計劃講收據例外與收據檔的句子,也沒有「2026-10-03 撤除」標記(S1018、S1034 合約行已有),建議同樣補。
3. Projects/RTB_Phase12一鍵展示與HTML報告_計劃 第 40 行:「接入點 2 的說明給人工核可的人看」只有 10-02 的「確認頁一律不帶模型說明」更正,沒標 2026-10-03 決策 d1 取代;補「(已被取代:使用者 2026-10-03 決策,說明只放報告,見 Phase 11B 計劃決策紀錄)」。
4. Systems/一鍵展示 第 51 行 PITFALL 寫「展示的情境斷言不看呼叫紀錄,不受影響」:展示觀察器(src/rtb/demo/observe.py:489)讀 `dsp_calls_for` 組判斷紀錄。我沒查情境斷言本身是否用到它,只確認觀察器讀它;管那篇的人要查「斷言」與「頁面顯示的判斷紀錄」各自用不用,措辭照實縮小。
5. (本篇內、但不在我的列裡,沒動,交協調者決定)Systems/分析行程流程與檢查點 第 110 行 `REVISIT:[when-file:src/rtb/analyzer/runner.py][by:2026-12-31]`:when-file 條件 2026-09-24 就成立了(句尾自己也寫「條件到了,量測還沒做」),照規則 6 應改寫現況並改綁未發生的事件或日期。

## 程式要改

無(narrate.py 的 `SYSTEM_PROMPT` 仍寫給核可的人看,已登記在 [[Issues/說明提示仍寫給核可的人看]],改了會讓入庫錄製失效,不屬本次)。

## 各篇 lint 結果

- Projects/RTB_Phase11B大模型接入_計劃:0 問題
- Systems/分析行程流程與檢查點:0 問題
- Verification/Phase4驗收紀錄:0 問題
- Verification/Phase12增量1驗收紀錄:0 問題
- MOC/index:0 問題
- Systems/可觀測查詢:0 問題
- Systems/服務水準與燒損告警:0 問題
- Issues/F7端到端在CI上偶爾超過60秒:0 問題
- Issues/執行迴圈收到SIGTERM等於硬殺:0 問題
- 沒有列、沒動的允許篇:Projects/RTB_Phase5樂觀鎖與重新規劃_計劃、Verification/Phase6驗收紀錄、Verification/Phase13增量2驗收紀錄(稽核員讀完沒報發現)。
