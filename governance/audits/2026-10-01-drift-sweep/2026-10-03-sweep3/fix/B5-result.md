# B5 修正結果(2026-10-03)

工作目錄 /Users/enzo/rtb-lumos-update。沒有執行任何寫入 git 的指令,沒動 src/tests/tools/claims/scripts。

## 逐列處置

筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據
---|---|---|---|---
Systems/評估與Jev決策點:117 | X9 | 已修 | 把「`test_one_exact_ratio_function_feeds_the_answer_key`(這支測試從沒以正式測試提交過)」改成 [test:test_one_exact_ratio_function_feeds_receipts_and_the_answer_key],註明它在 tests/domain/test_metrics.py、末尾呼叫 `answer_key_goes_through_exact_ratio` 驗標準答案那半,首版名字少了 receipts | tests/domain/test_metrics.py:325 定義、:357 無條件呼叫 answer_key_goes_through_exact_ratio;tests/eval/test_investigation_eval.py:312-342 用 spy 斷言 ic.gold 經 m.exact_ratio;git log -S 舊名只出現在 f4831bd
Projects/RTB_Phase2任務流程_計劃:189 | X9 | 已修 | 直接改正 10-02 那個寫錯的更正括號:接續鏈 3 代(MAX_GENERATION)只管交給執行後因版本已變、政策已變、決策已過時被擋下的重新規劃;同一任務內退回 COLLECTING_EVIDENCE(SubmitStale、證據不新鮮、規則輪證據過舊或不屬於目前這一輪)不開接續任務、沒有次數上限,只有規則輪「基本資料變動」重來最多 2 次(MAX_CHANGE_RESTARTS);修訂序號寫死 1,收件口 50 次修訂上限碰不到;風險仍歸同篇 2026-10-20 清理那條回頭條件 | flow.py:354-355(NeedsFreshEvidence→COLLECTING)、:377-378(NeedsFreshEvidence/RuleContinue→COLLECTING)、:405-406(SubmitStale→COLLECTING)、:427-432 _REPLAN_ON_BLOCK 只在擋下時;rule_round.py:46、:306-309(變動上限 2)、:279/:300/:319/:338/:347 RESTART_STALE/NO_ROUND 無上限;task_store.py:144 MAX_GENERATION=3;policy.py:345 revision=1;executor/inbox_store.py:65 MAX_REVISIONS_PER_TASK=50。補充:稽核員沒提規則輪變動重來有 2 次上限,已一併寫準
Projects/RTB_Phase2任務流程_計劃:126 | X9 | 已修 | 更正括號補「409 too_many_revisions 對 SubmitRejectedPermanently」,並註明第四種是增量 4 補上的,引用改成 [S45]–[S47] 與增量 4〈收件口拒絕代碼的分類〉 | src/rtb/analyzer/inbox_client.py:20-25(_PERMANENT_CODES 409 too_many_revisions)
Projects/RTB_Phase2任務流程_計劃:123 | D1 | 已修 | 「Phase 3 已做完仍沒裁定」改成「2026-10-03 使用者裁定先不做,改綁出現第二個送件來源或收件口對外開放,以提案收件口同名回頭條件為準」 | Systems/提案收件口.md:64、:66 的 REVISIT 已寫 10-03 裁定
Projects/RTB_Phase12一鍵展示與HTML報告_計劃:99 | H2 | 已修 | 「是否補做待使用者決定」改成「2026-10-03 使用者裁定不補做,展示狀態的費用欄位與頁面費用字樣都已拿掉,見〈頁面分區〉頂端摘要那條」 | src/rtb/demo/state.py:279-299 DemoState 沒有費用欄位;page.py grep「費用/cost」0 筆;tests/demo/test_page.py:1583 test_no_page_shows_an_ai_cost_line
Projects/RTB_Phase12一鍵展示與HTML報告_計劃:45、199 | H1 | 已修 | 兩處「補做或撤掉待使用者裁定」改成「2026-10-03 撤除不做:使用者裁定 AI 說明不進核可表單,[S1034] 標作廢,見該 Issue(已結案)」。同篇其他講收據未實作的更正括號(〈頁面與進度讀取〉例外那條、報告附收據那條、增量 2b 範圍、已排除的不可逆、人的判斷那條風險)一併補「2026-10-03 收據撤除不做」 | Issues/確認頁顯示AI說明時的收據還沒做 status resolved、結案框寫 10-03;同篇 [S1034] 已 [status:superseded];Phase 11B 計劃決策紀錄第 17 行
Projects/RTB_Phase12一鍵展示與HTML報告_計劃:5 | F1 | 已修 | lumos set updated 2026-10-03 | 本次與 aef0f5d 都有 10-03 改動
Systems/展示頁面:72 | H1/T1 | 已修 | 那半句後補「(2026-10-03 費用欄位已拿掉,防回歸裡的 test_the_cost_line_says_no_ai_was_called_instead_of_no_run 一併刪除,見文末〈拿掉 AI 費用欄位〉)」 | grep src tests「沒有費用 / test_the_cost_line」0 筆;git log -S 該測試名 → aef0f5d
Systems/展示頁面:66 | H1/M2 | 已修 | 那半句標「(已由 2026-09-25 第二輪裁定取代:頁面不顯示模型模式,page.py 不讀 model_mode;test_plain_wording_for_normal_situations 現在反過來斷言沒有『AI 說明方式』與『錄製』)」 | grep model_mode/NOT_CALLED/沒有呼叫 AI src/rtb/demo/page.py、flow_svg.py 0 筆;tests/demo/test_page.py:1406
Verification/Phase14增量2a離線驗證:49、62、71 | D1 | 已修 | 三處句後加 2026-10-03 更正括號:71 直接寫 17:40 時展示伺服器已關、那筆是 84 筆一批的評估錄製重播經模型閘道退路寫入;62 寫 r2 全套落在 r2 收斂(17:09)與 r3 收斂(17:36)之間,同時段那筆是 17:14 評估重播;49 寫當天除 12:31 那批對得上展示頁外都是評估重播批次,r1 修前那次落在 16:00 前後、照時段對得上評估重播 | Issues/錄製模式的原因假說寫進真帳本:28-29、:34;docs/.governance-log.jsonl 6399/6401/6405(code-phase14-inc2a 三輪收斂 16:56、17:09、17:36)。注意:49、62 兩處的時段是由治理帳收斂時間與 Issue KEY(16:00)推的,已在句中寫「照時段」
Issues/執行端寫入前再確認沒記讀到的平台版本:5 | F1 | 已修 | updated 改 2026-10-03;另把 10-02 更正括號裡的「要另訂觸發時點,待使用者裁定」補「2026-10-03 使用者已裁定不做、結案,見〈結案(2026-10-03)〉」 | 同篇〈結案(2026-10-03)〉一節
Verification/事故F1_結果不明只用原鍵對帳:5 | F1 | 已修 | updated 改 2026-10-03 | 同篇第 32 行 10-03 改 superseded
Systems/評估與Jev決策點:77 | S1 | 已修 | 改成「在 Phase 11B 接上模型候選時由使用者裁定(2026-09-24):每次 0.002 美元、p95 3 秒、失敗率 1%(延遲中位數協調者補成同 p95),常數 MODEL_LIMITS;AI 調查另用不設成本門檻的 INVESTIGATION_LIMITS」 | src/rtb/eval/model_candidate.py:69-72;src/rtb/eval/investigation_report.py:54-56

連帶修(照規則 3 搜到、在我名單內的篇):
- Issues/錄製模式的原因假說寫進真帳本:28「寫入者沒查出來」句後加「2026-10-03 註:同日稍後唯讀帳本中繼欄位查明,17:40 那筆是評估錄製重播」;updated 2026-10-03。摘要 KEY「16:00…同時一鍵展示伺服器開著」本身屬實(伺服器確實開著),而摘要是多行開頭欄位、lumos set 不適合改,沒動。
- Issues/流程圖把AI說明畫在送出建議之前:34「讓表單拿到說明是另一件事,這次不做」後補 10-03 裁定(AI 說明不進核可表單);updated 2026-10-03。
- Systems/展示頁面:147「讓表單拿到說明是另一件事」後補同一裁定。
- Verification/Phase11驗收紀錄、Issues/展示還留著AI參考判斷分支與說明看不到四查詢、Systems/README流程動圖產生器:搜「費用/收據/核可表單/待裁定/速率限制」沒有跟 10-03 裁定衝突的句子,沒改。

統計:13 列全部已修;沒修 0 列。

## 轉給別篇

1. Issues/確認頁顯示AI說明時的收據還沒做(不在我名單):
   - 開頭 updated 是 2026-10-02,但頂端結案框寫 2026-10-03 結案 → 改 2026-10-03。
   - 摘要 `DECISION: 尚未裁定要補做還是撤掉這條` 與 10-03 結案矛盾 → 改成「2026-10-03 使用者裁定撤除不做(AI 說明不進核可表單,Phase 11B 計劃決策紀錄),[S1034] 標作廢」。
   - 正文〈發生什麼事〉10-02 更正括號末尾「補做或撤掉仍待使用者裁定,撤掉的理由可直接用這一條」→ 補「2026-10-03 已裁定撤除」。

## 程式要改

無。

## 各篇 lint

全部 `lumos lint` 0 問題:Systems/評估與Jev決策點、Projects/RTB_Phase2任務流程_計劃、Projects/RTB_Phase12一鍵展示與HTML報告_計劃、Systems/展示頁面、Verification/Phase14增量2a離線驗證、Issues/錄製模式的原因假說寫進真帳本、Issues/執行端寫入前再確認沒記讀到的平台版本、Verification/事故F1_結果不明只用原鍵對帳、Issues/流程圖把AI說明畫在送出建議之前。
