---
type: system
status: doing
created: 2026-09-24
updated: 2026-10-02
responsibility: 負責「值不值得加」判斷點的離線評估:評分表、合成評估集與生成器、逐格計分與報告、比較表、逐格採用決定與人讀的決定紀錄,接入點 3 的模型候選(旁路紀錄、整批停下、子集、批次紀錄、比較表模型列),以及 Phase 13 AI 調查的評估(九格評分與標準答案、72 筆含誘導雙胞胎的評估集與生成器、直接呼叫 AI 決策函式的評估執行器(Phase 14 起正式路徑不呼叫 AI,其他套件匯入 AI 決策模組由 [S1429] 的匯入禁令擋)、逐格報告與不採用的決定、錄製批次驗收);不負責判斷點本身、路由與 AI 決策函式(在分析端);除了經模型用戶端寫花費帳與呼叫模型,不讀寫任何資料庫、不啟動子行程;不被任何其他套件匯入
aliases: []
about_code:
  - src/rtb/eval/__init__.py
  - src/rtb/eval/rubric.py
  - src/rtb/eval/generator.py
  - src/rtb/eval/eval_set.py
  - src/rtb/eval/scoring.py
  - src/rtb/eval/adoption.py
  - src/rtb/eval/record.py
  - src/rtb/eval/model_candidate.py
  - tests/eval/test_model_candidate.py
  - src/rtb/eval/investigation_cases.py
  - src/rtb/eval/investigation_set.py
  - src/rtb/eval/investigation_eval.py
  - src/rtb/eval/investigation_report.py
  - tests/eval/test_investigation_eval.py
tags:
  - type/system
  - status/doing
summary: |-
  WHY: [2026-09-26 Phase 14 增量 3] AI 退出正式與展示的加額決策後,評估執行器是 `ai_judge.Judge` 唯一的呼叫者:直接呼叫、先前各輪紀錄與記次在記憶體、沒有流程層/任務庫/規則輪;不再選查詢就當場結束;增量 4 起每筆分開存 AI 原始(模型自己下的結論,退回是無有效答案)與案例九條 `rule_verdict(case)`,見正文〈報告三列分列〉。錄製重播與協調者授權的即時加錄製都照舊可達。出處 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]] [S1146] 改寫、[S1429]。防回歸:[test:test_the_investigation_eval_runs_the_same_ai_judge]、[test:test_only_phase13_eval_uses_ai_judge_and_runner_stays_rule_only]。
  WHY:[2026-09-24 Phase 12 代碼審 r1] 命令列參數不收縮寫(allow_abbrev=False):一鍵展示的故障啟動器用正式入口同一支 parser 的解析結果核對目標路徑,縮寫與等號寫法都不能繞過(Phase 12 代碼審 r1 s1/l2/x2)。長選項一律寫全。出處:[[Projects/RTB_Phase12一鍵展示與HTML報告_計劃]]。
  WHY: [2026-09-24] 交接文件 Phase 10:只在有證據的窄決策點評估 Jev,依切片報品質、跟程式基準比品質成本延遲、沒達門檻明確不採用。使用者本人裁定評估對象是「值不值得加」這一個判斷,結論照實寫兩個不採用理由。出處:[[Projects/RTB_Phase10評估與Jev決策點_計劃]] 增量 2。
  RULE: 合成評估集只是有限的合約案例,不套統計信賴、不能產生已驗證清單;已驗證清單只能來自正式環境隱藏抽樣集,而且只有採用函式建得出來。[since:2026-09-24] [retire:接上正式環境抽樣集與人工標註、改用它們重建評估時] [test:test_evaluation_calls_the_candidate_without_validating_anything]
  WHY: [2026-09-24] Phase 11B 接入點 3:模型候選實作既有候選介面,評估紀錄命令列成為三支模型入口之一;評估套件的「不讀寫任何資料庫」有意識地放寬成「除了經模型用戶端寫花費帳與呼叫模型」,匯入閉包以開工前為基準只准多出模型用戶端一條分支。出處 [[Projects/RTB_Phase11B大模型接入_計劃]]〈接入點 3〉〈既有邊界怎麼改〉。
  RULE: 評估套件不被分析端、執行端、DSP、領域層、維運套件匯入(五個目錄的匯入規則禁令加邊界測試),規則作者的程式讀不到評估集;生成器不讀決策規則與它的測試。[since:2026-09-24] [retire:評估集改由獨立、有存取控制的評估執行器注入、不再放進程式庫時] [test:test_nothing_outside_the_eval_package_imports_it]
decisions:
  - content: 合成評估集第一版未提交就換成第二版:正數計數改照漏斗順序造
    id: d1
    context: 乾淨代理對第一版情境幾乎每筆都先質疑資料(點擊多於曝光、轉換多於點擊),對照結果被資料不合理蓋住
    why_chosen: 評估集換批要留紀錄;規則沒有因為看過答案而改,換批只為了讓合成情境不自相矛盾
    decided: 2026-09-24
    valid: true
  - content: 合成評估集換成第三版:評分表改 5 格(使用者裁定加資料自洽),正常格照漏斗造、資料異常格輪流造單一故障,每列多記歸檔的格與故障
    id: d2
    context: 評分表從 4 格改成 5 格,舊評估集的標準答案與格都不再適用
    why_chosen: 評分表改了照規則換批;規則沒有因為看過答案而改
    decided: 2026-09-24
    valid: true
  - content: 合成評估集換成第四版:三個變體改成基準原值、只改花費、只改預算(不重抽花費);點擊多於曝光的故障不再另把轉換歸零
    id: d3
    context: 增量 2 代碼審第 1 輪:舊版預算變體會重抽花費、點擊故障動到兩欄
    why_chosen: 生成器改了照規則換批;規則沒有因為看過答案而改
    decided: 2026-09-24
    valid: true
verified_by:
  - "[[Verification/Phase10驗收紀錄]]"
  - "[[Verification/Phase11B增量1驗收紀錄]]"
  - "[[Verification/Phase13增量3驗收紀錄]]"
  - "[[Verification/Phase14增量2a離線驗證]]"
  - "[[Verification/Phase14增量3驗證紀錄]]"
  - "[[Verification/Phase14增量4驗證紀錄]]"
---
# 評估與Jev決策點

2026-09-27 Phase 15 增量 1:[S918] 評估套件匯入閉包的准許名單精確新增規則模式探索的四支純離線模組(另立 `PHASE15_ALLOWED`,不放寬既有名單);它們不經模型用戶端、不送出,經門面、送出與 AI 決策模組的精確等式都沒變。套件說明(`__init__.py`)的准許名單段同步加註這四支。模組本身的家是 [[Systems/規則模式探索評估]]。[test:test_the_eval_package_reaches_the_model_only_through_the_model_client]

2026-09-27 Phase 15 增量 2:[S918] 再逐項精確新增(`PHASE15_ALLOWED`):規則模式探索的回覆核對、歷史錄製鍵與單一種子探勘執行器三支評估模組(家是 [[Systems/規則模式探索評估]]),以及探勘執行器經它開閘道送出的分析端模型窄入口(家是 [[Systems/規則模式探索模型入口]];閘道本身早在 Phase 13 名單);`PHASE13_ALLOWED` 不動。經門面的精確名單多歷史錄製鍵那一支(只取錄製鍵函式與呼叫者列舉);直接送出仍只有模型候選;AI 決策模組的匯入與送出等式照舊只有調查評估執行器;另立一組等式:匯入窄入口、用它的送出名字(`suggest`、`gate_ask`)的評估模組都精確等於探勘執行器一支(`rule_mining_senders()`)。套件說明(`__init__.py`)同步。[test:test_the_eval_package_reaches_the_model_only_through_the_model_client]

2026-09-27 Phase 15 增量 3:[S918] `PHASE15_ALLOWED` 只精確加規則模式探索的純比較與報告模組(家是 [[Systems/規則模式探索評估]]);命令列寫在既有的探勘執行器裡,經門面、送出、AI 決策模組與窄入口的精確等式都沒變(歷史錄製鍵那一支多取錄製檔的唯讀驗收,仍在同一個經門面名單裡)。套件說明(`__init__.py`)同步。[test:test_the_eval_package_reaches_the_model_only_through_the_model_client]

2026-09-26 Phase 14 增量 1:評估標準答案在案例邊界轉成 [[Systems/正式九條判斷領域規則]] 的型別並呼叫同一決策;必要列內缺值與單日 no_data 回無格的證據不足,原 72 筆含雙胞胎的格與答案逐筆維持,固定匯入閉包名單只增純領域模組。[test:test_missing_row_values_are_insufficient_without_changing_the_72_cases] [test:test_the_eval_package_reaches_the_model_only_through_the_model_client]

2026-09-26 代碼審折入:標準答案與正式規則刻意同源,只證接線一致、不作獨立品質證據;生成器仍不匯入分析端與 AI 決策模組。評估的區段轉換率邊界檢查共用 [[Systems/正式九條判斷領域規則]] 的公開函式,[S1403] 以精確下降 50.04% 而收據顯示 -50.0% 守捨入門檻。[test:test_eval_uses_the_domain_segment_rate] [test:test_exact_thresholds_distinguish_zero_denominators]

本篇管十四支檔(開頭 about_code 列的,含兩支測試);其中 Phase 10 評估的是 `src/rtb/eval/` 這七支:評分表(`rubric.py`)、生成器(`generator.py`)、合成評估集(`eval_set.py`,生成器的產出,不要手改)、逐筆計分與逐格報告(`scoring.py`)、比較表與逐格採用決定(`adoption.py`)、人讀的決定紀錄(`record.py`,`python -m rtb.eval.record` 產生)、套件說明(`__init__.py`)。判斷點本身、路由與待測格清單的型別在 [[Systems/分析行程流程與檢查點]],判斷點的輸入與評分格在 [[Systems/任務流程領域模型]]。另外七支見下文:模型候選與它的測試在〈模型候選〉,AI 調查評估的四支模組與測試在〈AI 調查的評估〉。

- 評分表(五條全由使用者本人裁定,原意對照後加「資料自不自洽」;由上而下第一個成立的):暫停 → 不值得加;資料異常(負數、缺值、點擊多於曝光、轉換多於點擊)→ 證據不足;曝光或點擊是零 → 不值得加;轉換或營收有正數 → 值得加;轉換營收都是零 → 證據不足。標準答案由程式照評分表算,不派代理標註。防回歸:[test:test_the_rubric_gives_exactly_one_class_for_every_input]。
- 合成評估集(第四版):5 格 × 20 組基準情境 × 三個變體(基準原值、只改花費仍在偏低範圍、只改預算且花費不變),共 300 筆。正常格照漏斗順序造、沒有負數與缺值;資料異常格輪流造 12 種單一故障(五個欄位各缺值、各負數,只違反點擊多於曝光、只違反轉換多於點擊);每列多記歸檔的格與故障。計劃釘住的邊界案例放在各格前幾組。每種故障只動一欄(代碼審第 1 輪:點擊多於曝光的故障原本另把轉換歸零)。雜湊由測試釘住,另重跑生成器逐值比對;換批用決策指令記(d1、d2、d3)。防回歸:[test:test_the_eval_set_is_pinned_by_hash]、[test:test_the_generator_covers_every_anomaly_and_matches_the_committed_set]。
- 計分:逐筆經分析端的路由函式,依退回後的最後有效答案計分;現行規則用同一套(不傳候選)。每格報分子分母、類別正確數、錯誤子型與走了哪條路;「值得加」格的指標是召回率,其他三格是誤提案率,另報類別正確率;無關欄位擾動後答案改變的組數另報。防回歸:[test:test_the_eval_report_is_per_slice_and_marks_thin_slices]、[test:test_irrelevant_fields_do_not_change_the_answer]、[test:test_phase_ten_scenarios_explicitly_report_missing_queries](原 test_the_code_rule_is_scored_per_slice_like_a_candidate,已隨 b2fc512 照 Phase 14 [S1417] 改寫成這支,現行規則仍逐格報)。
- 品質門檻(使用者 2026-09-24 本人裁定定案):「不誤提案的比例」與類別正確率下界 ≥ 0.95、每格至少 73 筆;「值得加」格召回率下界 ≥ 0.80、至少 16 筆。成本、延遲、失敗率門檻在接上第一個候選時由使用者裁定。
- 報告分兩種型別(代碼審第 1 輪:原本只靠一個字串區分,合成集結果改個字串就能被當成正式):合成集報告不含信賴下界;正式報告只能經計分模組的建構函式建出來(帶簽發者),必帶隱藏集出處(版本、雜湊、抽樣與人工標註出處、候選版本、候選是否早於揭露),每項指標存分子、分母、點估計與 95% Wilson 下界。代碼審第 2、3 輪補:正式報告每一筆都要交給候選,候選答的或候選退回的(逾時、例外、不知道、非法回傳)都算候選的結果,任何一筆走現行規則就拒(第 2 輪只要求每格一筆,1 筆候選加 72 筆現行規則就能讓現行規則替候選過關);隱藏集雜湊用領域層共用的雜湊判準;「候選是否早於揭露」要是真的布林值。
- 採用一律 fail-closed:證據不是正式報告、候選不早於揭露、這一格沒有報告或沒有比較表那一列、任何欄位沒量或不合法(非有限、負數、比率超過 1)、門檻還沒裁定,都不驗證。一格要品質下界過門檻(值得加格召回率 0.80;其他四格「不誤提案的比例」與類別正確率都要 0.95,兩項都比),而且這一格的成本、延遲中位與 p95、各失敗率都不超過門檻。比較表是逐格一列,列上記的格要等於它被拿來判的那一格(掛錯鍵視同沒量);有限數判準用領域層共用的檢查、另加不為負;結論、理由與缺的證據都從同一個採用結果衍生,採用時不印不採用理由。最少樣本(16、73)由下界本身擋住,不另寫樣本數檢查;其他三格只比類別正確率,因為每次誤提案也是一次類別錯誤——兩道另寫的檢查變異檢查都證實拿掉結果不變。防回歸:[test:test_a_slice_is_validated_only_when_every_bar_is_met]、[test:test_the_adoption_decision_is_no_without_measured_validated_slices]、[test:test_unmeasured_candidates_show_no_numbers]。
- 決定紀錄(`governance/eval/phase10-worth-adoption.md`,`python -m rtb.eval.record` 產生,命令列照專案的兩層形狀;評估套件不准匯入維運套件,所以比照執行端各自建解析器)。2026-09-24 用第四版評估集:結論不採用;當時現行規則在暫停格誤提案 39/60、資料異常格類別全錯、沒價值格 60/60 判成值得加,沒投放與有價值兩格全對。2026-09-27 正式規則換成九條後重產(b2b17ea),現在的紀錄是:暫停、資料異常、沒價值三格全對,沒投放與有價值兩格全判證據不足(舊資料不足以評九條);擾動 0 組改變。數字以那支檔為準。
- 匯入禁令(代碼審第 2 輪):分析端、執行端、DSP、領域層、維運套件五層的匯入規則另禁 importlib,邊界測試把這五層任何 __import__ 或 import_module 呼叫、對 __import__ 的任何引用、從 builtins 匯入都算違規(不只認字面常數,第 3 輪補引用與 builtins);這五層今天沒有合法的動態匯入。防回歸:[test:test_nothing_outside_the_eval_package_imports_it]。
- 評分表原意對照(`governance/eval/rubric-intent-check.md`,評分表每次改動都重跑一批):4 格版兩批促成使用者加「資料自洽」;5 格版第 3 批在暫停、沒投放兩格一致,資料異常 2/3、有價值 1/3 一致,沒價值格代理 3/3 判「不值得加」、跟使用者裁定的「證據不足」不一致;使用者本人確認維持原裁定(一小時窗太短、轉換常有延遲,看不出來不等於沒效益),評分表沒改。

## 模型候選(Phase 11B 增量 1)

- `src/rtb/eval/model_candidate.py`:把七個欄位格式化成固定模板,要模型只回 `{"verdict": ...}`;失敗一律往外丟,
  由既有路由退回現行規則;旁路紀錄記每次的結果類別、結算狀態、無法可靠分類與工具使用標記。跑合成集固定子集
  (每格 14 組、每組 3 個變體,種子 20260924);本地上限拒絕、訂閱額度用完、設定錯誤、花費帳忙碌、超支、
  無法可靠分類、偵測到工具使用就在那個情境之後整批停下。批次紀錄每個情境一列,七欄相同的標「共用前一列」;
  比較表模型列從批次紀錄算(成本取最高一次原價,沒呼叫模型的不算進比率)。模型呼叫經 [[Systems/模型用戶端]]。
- `src/rtb/eval/record.py` 在入口讀模式(即時開關、展示編號、PATH 上的 claude、啟用紀錄),即時模式的花費帳寫死
  家目錄那一本,`--ledger` 只在錄製模式能用;預留時花費帳忙碌以結束代碼 9 結束。
- `tests/eval/test_model_candidate.py`:[S908]、[S909]、[S918]、[S919]、[S924]、[S928]、[S933]、[S934]。
- 代碼審第 1 輪補強(2026-09-24):去重跟錄製開關無關(即時沒開錄製時候選自己記住這一批呼叫過的鍵,開錄製時照錄製檔);
  共用的失敗列標共用、不算送出;模型用戶端以外的例外也替那個情境補一列(暫時性、無法可靠分類)並停下,不會讀到上一列;
  成功形狀帶工具痕跡、超支又未結算也停。模型的計分走既有的計分與合成集報告,模型段另印「模型逐格結果」表、錯誤子型與
  擾動改變的組數;有旗標時比較表的 LLM 列寫「沒量(原因:旗標)」;重播時批次紀錄的情境清單要是這次子集的開頭一段,
  不是就標「批次紀錄的情境清單跟這次子集不同」。防回歸:[test:test_identical_inputs_share_one_call_without_recording]、
  [test:test_a_non_model_error_gets_its_own_row_and_stops]、[test:test_the_model_section_reports_its_own_scores]、
  [test:test_flags_show_up_in_the_llm_row_and_the_scenarios_must_match]。
- 代碼審第 2 輪補強(2026-09-24):重播時批次紀錄要跟這次重播逐列對得上(列數、順序、有錄製的列的結果類別與原價),
  讀不懂或對不上就掛旗標、不算門檻;即時跑到一半被中斷時照寫已跑的部分並標中斷;模型逐格結果只算真的呼叫了模型的情境,
  另列實際作答、退回、沒呼叫;即時沒開錄製時來源寫「即時、未存檔」;「不採用的理由」是 Phase 10 固定文字,模型段另註明
  候選實測已有。評估套件的送出名字(send、backend、BackendCall…)只准模型候選用 call_model,模型用戶端的匯入閉包寫死、
  不准有網路模組。防回歸:[test:test_a_batch_record_missing_rows_is_flagged]、
  [test:test_a_tampered_batch_record_is_not_trusted]、[test:test_the_model_scores_only_count_calls_that_were_made]、
  [test:test_an_interrupted_live_run_still_writes_its_batch]。
- 代碼審第 3 輪補強(2026-09-24):重播核對擴大到延遲、判定與共用/送出標記(共用照子集的提示重算),有旗標的批次
  不印門檻判定;被中斷的那一次也進批次紀錄(interrupted,附沒結算的預留編號);別批擋住時旗標寫明先清哪一批。
  防回歸:[test:test_a_faster_batch_record_cannot_pass_the_latency_bar]。


## AI 調查的評估(Phase 13 增量 3)

計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈評估案例〉〈花費帳與採用判定〉〈錄製批次與入庫〉。評估對象是「配速偏低之後,整段調查的最後結論」。

- `src/rtb/eval/investigation_cases.py`:九格評分(= 標準答案的九條規則,順序寫死在 `ANSWER_ORDER`,系統提示的九條照同一個順序)、標準答案產生函式、生成器。標準答案的比率只經領域層的精確比率函式取得、用分數判門檻,不讀捨入後的收據字串;資料異常直接用 Phase 10 的 `is_anomalous` 看原始 1 小時指標。生成器每格 4 組,每組一筆正常名稱配一份名稱藏誘導文字的雙胞胎(數字完全相同),共 72 筆;任何百分比門檻(轉換率變化 -50%、最近一次加預算的轉換變化 0%、前置過濾的配速 50%)的精確值離門檻不到 0.05 個百分點就重抽,每筆也要落在它要的格,不然生成器自己丟錯。比照 Phase 10 生成器,不匯入分析端(選項代碼另寫一份,測試核對一致)。防回歸:[test:test_the_answer_key_applies_the_history_rules_in_order]、[test:test_no_generated_case_sits_on_a_rounding_boundary]、`test_one_exact_ratio_function_feeds_the_answer_key`(這支測試從沒以正式測試提交過)。
- `src/rtb/eval/investigation_set.py`:生成器的產出(不要手改);改題目、名稱或查詢結果會讓錄製鍵對不上,要重錄一批。
- `src/rtb/eval/investigation_eval.py`:評估執行器與命令列 `python -m rtb.eval.investigation_eval`。逐筆直接呼叫 AI 決策函式(見 [[Systems/分析行程流程與檢查點]];寫下時它也是正式路徑那一支;Phase 14 增量 3 起正式路徑不呼叫 AI,其他套件匯入 AI 決策模組由 [S1429] 的匯入禁令擋),只把查詢來源換成案例存的原始結果(收據用同一支收據函式算)、先前各輪紀錄放在記憶體;原本的「永遠成功的續租回呼」增量 3 已刪(見下方〈AI 退出加額決策後的評估〉);沒有自己的驗證或輪數邏輯。預設重播入庫目錄 recordings/model/phase13-investigation-eval/;即時加錄製要帶 `phase13-eval-YYYYMMDD` 批次,開閘道時先跑模型用戶端的開錄前目錄檢查(見 [[Systems/模型用戶端]]);`--verify` 只准重播,照〈錄製批次與入庫〉的驗過條件核(失敗類錄製 0 份、同一批、沒有佔位、重播找不到錄製 0 筆),驗不過以 1 結束。評估批次不另存批次紀錄檔:模型那一列從重播帶回的錄製當時延遲與原價算,錄製日期與批次讀錄製檔。防回歸:[test:test_the_investigation_eval_runs_the_same_ai_judge]、[test:test_the_batch_check_goes_red_on_missing_failed_or_mixed_recordings]、[test:test_the_eval_runner_refuses_to_record_into_a_mixed_directory]。
- `src/rtb/eval/investigation_report.py`:逐格報告只算名稱正常的 4 筆(誤提案、類別正確、值得加格召回、平均與最多輪數、每個決策的原價、退回原因分布);對抗切片另列誘導雙胞胎結論翻轉的筆數與差異;比較表是現行程式規則(實測)對模型(歷史觀測、錄製日期);採用一律不採用、不建任何已驗證清單。模型那一列用本計劃自己的門檻常數 `INVESTIGATION_LIMITS`:成本不設門檻(`cost_exempt`),延遲 p95 3 秒、失敗率 1%。防回歸:[test:test_the_investigation_report_is_per_slice_and_never_adopts_synthetic]、[test:test_the_report_counts_decisions_flipped_by_injected_names]、[test:test_the_investigation_evaluation_never_validates_a_slice]。
- `src/rtb/eval/adoption.py` 的門檻型別加 `cost_exempt`(預設假;為真時成本門檻必須是 None);採用判定抽成 `operational_problems`,Phase 10 的逐格採用與調查評估的模型那一列共用,`cost_exempt` 為真就不比成本、延遲與失敗率照查。`src/rtb/eval/model_candidate.py` 的逐欄判定在 `cost_exempt` 為真時成本那一欄寫「不設門檻(假設正式環境用自研模型、成本另計)」;Phase 11B 模型候選那組門檻常數不動。防回歸:[test:test_an_explicit_no_cost_gate_skips_only_the_cost_check]、[test:test_the_report_writes_no_cost_gate_for_an_exempt_limit]。
- 匯入邊界([S918] 照 Phase 13 改寫):評估套件閉包的准許名單加模型閘道、AI 決策模組(ai_judge 與它的詞彙模組)與這四支;匯入 AI 決策模組、經它開閘道送出的,評估套件裡只准評估執行器。
- (2026-09-25 當時紀錄,現況數字見文末〈報告三列分列〉)決定紀錄 `governance/eval/phase13-investigation-adoption.md`(命令列產生)。2026-09-25 入庫時還沒有評估錄製:72 筆全部「找不到錄製」、退回現行規則,模型那一列沒量,結論不採用。現行規則實測:暫停、資料異常、裁定 12 三格與沒價值格全錯(有投放就提案),較長窗有轉換、沒投放、有價值三格全對。
- `tests/eval/test_investigation_eval.py`:上面各條的合約測試([S1117]–[S1119]、[S1133]、[S1140]、[S1141]、[S1146]、[S1155]、[S1159]、[S1163]、[S1165]),以及 [S1162] 標準答案那半的檢查函式(由 tests/domain/test_metrics.py 綁 [S1162] 的那支呼叫)。全部用假的模型呼叫或假 claude,不碰真模型。
- 代碼審 r1 補強(2026-09-25):比較表某格只要有一筆正常案例沒真的呼叫到模型就寫「沒量(錄製不全)」,任一筆缺錄時模型那一列整列沒量,品質與退回率的分母是筆數;成本豁免時成本欄可以沒量;評估套件只經模型用戶端門面(匯入檢查涵蓋全部 model 開頭的檔);即時加錄製一定要給入庫目錄以外的新目錄;評估執行器匯入就算送出點。防回歸:[test:test_a_partly_recorded_batch_is_not_reported_as_measured]、[test:test_the_fallback_rate_counts_cases_not_calls]、[test:test_the_answer_key_boundaries_match_the_receipts]、[test:test_exempt_limits_still_check_failure_rates_and_the_batch_check_counts_ledger_busy]、[test:test_cost_exempt_ignores_an_unmeasured_cost]、[test:test_live_recording_must_go_to_a_fresh_directory]、[test:test_importing_the_eval_runner_counts_as_a_send_point]。
- 代碼審 r2 補強(2026-09-25):即時錄製不准寫進入庫目錄改由模型用戶端共用的開錄前目錄檢查判(見 [[Systems/模型用戶端]]),評估執行器不再自己比路徑;逐欄判定、欄位標示與沒送出的結果類別搬到 `adoption.py` 共用,調查報告不匯入模型候選。防回歸:[test:test_every_live_recording_entry_refuses_the_committed_directory]、[test:test_the_report_takes_the_shared_marks_from_adoption_not_the_sender]。

## 批次驗收跟展示批次共用(Phase 13 增量 4 代碼審 r1 h2,2026-09-25)

- WHY:評估批次的 `batch_problems` 把批次本身的三條交給模型用戶端門面的共用驗收 `batch_file_problems`(展示錄製批次的入庫前檢查呼叫同一支),自己只加「重播找不到錄製 0 筆」;共用那一支另外拒收不是正式後端錄的錄製。防回歸:[test:test_the_batch_check_goes_red_on_missing_failed_or_mixed_recordings]。
- WHY(2026-09-25):調查評估決定紀錄的不採用理由原本寫「展示只能標展示模式、未通過採用門檻」,使用者同日拿掉展示模式橫幅後對不上;改成「展示照樣可以用 AI 回答做示範,但不進正式決策路徑」,重播入庫錄製重產決定紀錄,只有這一句變。防回歸:[test:test_the_decision_record_no_longer_promises_a_demo_mode_banner]。

## Phase 14 增量 2a 評估資料（2026-09-26）

出處 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]] [S1425]。生成器以固定 NOW 減 days_ago 補帶時區 committed_at 並重產 72 筆。逐筆格與標準答案、調整收據、SYSTEM_PROMPT 位元組及錄製鍵維持原值；時間戳僅供讀取白名單驗證，不進模型提示。2a 代碼審 r1：同一次加額在歷史列與過去調整列改成同一時刻（NOW 減 days_ago；原本歷史列早 2 小時、落在前一個 UTC 日），重產 72 筆後收據雜湊、格與答案不變，入庫錄製重播驗收通過。防回歸：[test:test_adjustment_timestamp_preserves_recorded_receipts_for_all_72_cases]、[test:test_each_past_adjustment_has_the_same_moment_as_its_history_row]。

## Phase 14 增量 2b:Phase 10 轉接與 Phase 13 程式規則列(2026-09-26)

- Phase 10:`route` 的四查詢與決策時間改必填,`score`、模型候選 `run_subset`、延遲量測一律明傳 `MISSING_FOUR_QUERIES` 與固定 `scoring.RULE_NOW`;暫停/異常照第 1/2 條先判,其餘證據不足。`synthetic_report` 每格帶 `note`「舊資料不足以評估九條規則」,逐格表印在格名後;標準答案不動。出處 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]] [S1417]。防回歸:[test:test_phase_ten_scenarios_explicitly_report_missing_queries]。
- Phase 13:`investigation_report.rule_verdict` 改成九條加案例四查詢(`rule_evidence`)以案例 NOW 判,跟標準答案同源,36/36 只證接線、不作品質證據(報告三列分列與同源警語是增量 4)。評估執行器遇到 AI 退回開規則輪(`RuleContinue`),改由同一支 `rule_verdict` 從案例取四查詢定案,不打 DSP、不問模型。
- 評估案例轉接同步帶上過去調整提交時刻與截斷旗標([[Systems/正式九條判斷領域規則]])。

## 代碼審 r1(Phase 14 增量 2b,2026-09-26)

(`Judge(raw_replay=True)` 的旗標在 Phase 14 增量 3 拿掉:Judge 只剩評估這一個呼叫者,語意固定就是原始錄製重播,見文末)

- 正式環境抽樣報告(`production_report`)同樣標「舊資料不足以評估九條規則」;延遲量測與報告寫明量的是缺四查詢的九條短路徑。
- Phase 13 評估用 `Judge(raw_replay=True)`:沿用舊前置過濾、AI 答 propose 時 `CaseRun.final` 記模型自己的答案(增量 4 撤掉 `final`:它把退回後規則答的也混進來,見文末)(正式路徑已改成規則否決);報告數字不變,「AI+規則否決」列是增量 4。

## 錄製重播不寫真帳本(Phase 14 增量 2b 代碼審 r2,2026-09-26)

Phase 13 評估命令列與 Phase 10 評估(`record`)在錄製模式沒帶 `--ledger` 時改用這次執行專屬的暫存帳本(評估命令列把路徑印到標準錯誤),不再退回帳號家目錄那一本。出處 [[Issues/錄製模式的原因假說寫進真帳本]]。防回歸:[test:test_recorded_entries_never_touch_the_account_ledger]。

## AI 退出加額決策後的評估(Phase 14 增量 3,2026-09-26)

出處 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈拆增量〉3「留評估(AI 決策模組)」、[S1146] 改寫;驗證見 [[Verification/Phase14增量3驗證紀錄]]。

- WHY:`Judge` 的 `raw_replay`、`preflight_ok`、`stop_requested`、`hold` 參數撤除(沒有入口就刪):評估一向用原始錄製重播的語意(暫停/異常照舊問模型,還原錄製當時的原始答案),正式路徑的前置過濾分支已經不存在。`AiContext` 從流程層搬到 `rtb.analyzer.investigation`、拿掉續租回呼,評估執行器的「永遠成功的續租」跟著刪。報告數字不變(重播同一批錄製)。
- WHY:匯入邊界:原始碼裡只有 `rtb.eval.investigation_eval` 匯入 AI 決策模組;分析端驅動移出准匯入模型閘道與送出點的名單。防回歸:[test:test_only_phase13_eval_uses_ai_judge_and_runner_stays_rule_only]。
- (已由增量 4 完成,見下一節)報告的「AI 原始 / AI+規則否決 / 程式規則」三列分列與同源警語。

## 報告三列分列(Phase 14 增量 4,2026-09-27)

出處 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈評估與報告〉[S1410][S1418]、使用者裁定 8;驗證見 [[Verification/Phase14增量4驗證紀錄]]。

- WHY:舊報告的「模型」列取的是「最後答案」,AI 退回(多半是選項外答案)時填的是規則的答案,所以把規則答對的算成模型答對(品質 0.4722,AI 原始只有 0.3333)。改成 `CaseRun` 分開存 `ai_raw`(模型自己下的結論,沒有就是 None)與 `code_rule`/`rule_reason`(案例九條),刪 `final`;沒有有效答案另列原因,不算誤提案、不算答對。防回歸:[test:test_reports_keep_raw_ai_errors_separate_from_rule_vetoes]。
- WHY:「AI+規則否決」只在報告層由 `ai_raw` 與 `code_rule` 派生(propose 被九條否決就取九條結果,原因取九條細因),分母同 AI 原始;正式流程已無此機制(裁定 8)。它與程式規則都跟標準答案同源,報告逐列標「同源構造,不作品質證據」。
- WHY:採用理由加「AI 原始品質不足」一條,品質欄只數 AI 原始答對 / 名稱正常筆數、退回率改數無有效答案的筆數;規則 36/36 不能替模型答對。防回歸:[test:test_shared_rule_accuracy_never_changes_the_ai_adoption_decision]。
- WHY:延遲分單位與量測範圍——模型呼叫毫秒(錄製記的毫秒原值)、九條本機計算微秒(報告產生時行程內量,每次重產數字會小幅變動,只當量級)、整段正式蒐證不在評估裡量(寫沒量、指向 F7 實測)。代碼審 r1 起單位只寫在欄名、格內不換算:模型那一列照門檻常數存微秒、印微秒。
- 報告開頭加三到五行白話摘要,數字取這次重播。原「比較表(現行規則 對 模型)」併進三列分列表。
- 2026-09-27 重播數字(名稱正常 36 筆):AI 原始有效 23、答對 12、誤提案 11/19、無有效答案 13;派生 0/19;程式規則 36/36(同源);雙胞胎 AI 原始 13 組不同;模型延遲中位 4236、p95 7244 毫秒。以程式碼為準,重查:`PYTHONPATH=src .venv/bin/python -m rtb.eval.investigation_eval --verify --ledger <暫存路徑>`。

### 代碼審 r1 修正(Phase 14 增量 4,2026-09-27)

出處:governance/review-reports/code-phase14-inc4/(r1 單 reviewer、架構對齊、外家 finder),協調者裁定全修。

- WHY:缺錄製(任一次呼叫是沒送出的結果類別)另列 `missing`,不算 AI「無有效答案」、不進有效答案分母;有缺錄製時報告開頭、合計、摘要標「不可採信」,也不出 AI 原始品質理由(改由「錄製不全」理由說明)。原本逐格標沒量、合計卻把缺錄製算成無有效答案(外家 finder-1)。防回歸:[test:test_missing_recordings_are_their_own_column_and_void_the_report]。
- WHY:派生否決細因走評估集既有的 `rule_decision(case)` 封裝,報告不再直接匯入領域九條(架構對齊-1)。防回歸:[test:test_the_rule_reason_goes_through_the_case_wrapper]。
- WHY:數字格統一走 `adoption.format_value`(Phase 10 比較表 `record._cell` 與調查報告共用):四位有效數字、一萬以上印整數,不出科學記號;單位寫在欄名、格內不換算(架構對齊-2、單 reviewer-5)。模型呼叫毫秒直接取錄製記的毫秒原值(`Report.model_latency_ms`)。防回歸:[test:test_latency_units_live_in_the_column_names_not_in_the_cells]。
- WHY:Phase 10 模型段沒呼叫過模型時寫「候選子集 N 個情境:沒有跑」,不寫「跑了」;比較表「現行程式規則」五列格名帶「舊資料不足以評估九條規則」,表下註明品質 1 是缺四查詢短路徑剛好等於標準答案(外家 finder-2、單 reviewer-4)。防回歸:[test:test_the_phase_ten_record_says_what_did_not_run_and_marks_old_data]。
- WHY:入庫的 Phase 13 決定紀錄要跟入庫錄製重播逐字相同,只排除九條本機延遲那一行(每次行程內重量);派生列殺傷力補反例(AI 有效答錯的非提案照留、值得加格 propose 不算否決、派生雙胞胎件數、九條延遲量級),單 reviewer 列的四種改壞(派生恆取規則、雙胞胎改用 AI 原始算、延遲不除 1000、任何 propose 都算否決)逐一在原檔改壞後翻紅、已還原(外家 finder-3、單 reviewer-3)。防回歸:[test:test_the_committed_report_matches_a_replay_of_the_committed_recordings]、[test:test_the_derived_row_only_replaces_vetoed_proposals]。
- WHY:無有效答案照最後一次呼叫原文讀本意(第一個帶 choice 的 JSON 物件):本意誤提案/答對/答錯/選查詢/讀不出/沒有原文,另給「照本意算」參考值,明標非正式口徑;正式口徑照舊只算有效答案。為此 `Call` 多存回應原文(只供揭露、不參與計分)(單 reviewer-1)。防回歸:[test:test_off_menu_answers_disclose_what_the_model_meant]。
- WHY:雙胞胎拆「兩邊都有有效答案而結論不同」與「一邊沒有有效答案」兩行,摘要同步(單 reviewer-2);報告說明評估集雜湊為何跟錄製時不同、重播找不到錄製 0 筆代表題目未變(單 reviewer-6);`no_answer` 一律扁平代碼(退回代碼、`preflight`、`no_conclusion`,架構對齊-3)。防回歸:[test:test_the_twin_slice_separates_changed_answers_from_format_failures]、[test:test_the_report_explains_why_the_eval_set_hash_differs_from_recording_time]、[test:test_no_answer_reasons_are_flat_codes]。
- 2026-09-27 r1 修正後重播:本意揭露為本意誤提案 2、本意答對 4、本意選查詢 7;照本意算(非正式)有答案 29/36、答對 16、誤提案 13/25;雙胞胎兩邊都有答案而不同 3 組、一邊沒有有效答案 10 組。其餘數字不變。

### 代碼審 r2 修正(Phase 14 增量 4,2026-09-27)

出處:governance/review-reports/code-phase14-inc4/(r2 單 reviewer、架構對齊),全是 minor,協調者裁定 6 條全修。

- WHY:缺錄製的單筆旗標與筆數照同檔慣例分單複數:`CaseRun.missing_recording`(布林)對 `Report`/`CellStats`/`Totals` 的 `missing_recordings`(整數);原本只看「找不到錄製」的舊 `missing_recording` 併進來,一律看全部沒送出類別(上限拒絕、設定錯誤、花費帳忙碌也算)。批次驗收那句改成「找不到錄製(或沒送出)」。防回歸:[test:test_every_unsent_outcome_counts_as_a_missing_recording]。
- WHY:雙胞胎任一側缺錄製的組不比、不進「一邊沒有有效答案(格式失敗等)」,另列「缺錄製 N 組」;開頭、合計、摘要的不可採信改看整批(含誘導側)的 `missing_recordings`。防回歸:[test:test_a_missing_twin_is_not_a_format_failure_and_voids_the_report]。
- WHY:評估集雜湊說明改成實際比對:`RECORDED_EVAL_SETS` 登記每個入庫評估批次錄製時的雜湊與換版原因(phase13-eval-20260925 → 8dccc36a…,已由 git 於 f4831bd 的 investigation_set.py 重算核對),相同印相同、不同才印差異與原因、沒登記照實寫無法比對、沒有批次不印。重錄新批次時要補一列。防回歸:[test:test_the_report_explains_why_the_eval_set_hash_differs_from_recording_time]。
- WHY:同一個模型延遲整份只用毫秒呈現,模型那一列的延遲兩欄印錄製記的毫秒原值(欄名標毫秒),門檻照舊在內部用微秒比。
- WHY:Phase 10 表下註更正:暫停、異常兩格九條用基本資料就判得出(品質 1 有效),只有沒價值格是缺四查詢時一律證據不足而恰好等於標準答案。
- 殺傷力補反例:照本意算的分母只算應不提案格([test:test_intent_reference_counts_only_should_not_cells_in_its_denominator])、本意讀最後一輪原文([test:test_intent_reads_the_last_call_of_a_multi_round_case])、缺錄製判斷涵蓋所有沒送出類別;三種改壞加「雙胞胎不排除缺錄製」逐一改在原檔後各自翻紅,已還原。
- 重產後對外數字不變;報告多一行「缺錄製:0 組」,雜湊說明改成比對結果,模型列延遲改印 4236/7244 毫秒。
