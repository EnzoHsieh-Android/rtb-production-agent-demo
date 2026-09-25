---
type: system
status: doing
created: 2026-09-24
updated: 2026-09-25
responsibility: 負責「值不值得加」判斷點的離線評估:評分表、合成評估集與生成器、逐格計分與報告、比較表、逐格採用決定與人讀的決定紀錄,接入點 3 的模型候選(旁路紀錄、整批停下、子集、批次紀錄、比較表模型列),以及 Phase 13 AI 調查的評估(九格評分與標準答案、72 筆含誘導雙胞胎的評估集與生成器、呼叫正式路徑同一支 AI 決策函式的評估執行器、逐格報告與不採用的決定、錄製批次驗收);不負責判斷點本身、路由與 AI 決策函式(在分析端);除了經模型用戶端寫花費帳與呼叫模型,不讀寫任何資料庫、不啟動子行程;不被任何其他套件匯入
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
---
# 評估與Jev決策點

本篇管 `src/rtb/eval/` 七支檔:評分表(`rubric.py`)、生成器(`generator.py`)、合成評估集(`eval_set.py`,生成器的產出,不要手改)、逐筆計分與逐格報告(`scoring.py`)、比較表與逐格採用決定(`adoption.py`)、人讀的決定紀錄(`record.py`,`python -m rtb.eval.record` 產生)、套件說明(`__init__.py`)。判斷點本身、路由與待測格清單的型別在 [[Systems/分析行程流程與檢查點]],判斷點的輸入與評分格在 [[Systems/任務流程領域模型]]。

- 評分表(五條全由使用者本人裁定,原意對照後加「資料自不自洽」;由上而下第一個成立的):暫停 → 不值得加;資料異常(負數、缺值、點擊多於曝光、轉換多於點擊)→ 證據不足;曝光或點擊是零 → 不值得加;轉換或營收有正數 → 值得加;轉換營收都是零 → 證據不足。標準答案由程式照評分表算,不派代理標註。防回歸:[test:test_the_rubric_gives_exactly_one_class_for_every_input]。
- 合成評估集(第四版):5 格 × 20 組基準情境 × 三個變體(基準原值、只改花費仍在偏低範圍、只改預算且花費不變),共 300 筆。正常格照漏斗順序造、沒有負數與缺值;資料異常格輪流造 12 種單一故障(五個欄位各缺值、各負數,只違反點擊多於曝光、只違反轉換多於點擊);每列多記歸檔的格與故障。計劃釘住的邊界案例放在各格前幾組。每種故障只動一欄(代碼審第 1 輪:點擊多於曝光的故障原本另把轉換歸零)。雜湊由測試釘住,另重跑生成器逐值比對;換批用決策指令記(d1、d2、d3)。防回歸:[test:test_the_eval_set_is_pinned_by_hash]、[test:test_the_generator_covers_every_anomaly_and_matches_the_committed_set]。
- 計分:逐筆經分析端的路由函式,依退回後的最後有效答案計分;現行規則用同一套(不傳候選)。每格報分子分母、類別正確數、錯誤子型與走了哪條路;「值得加」格的指標是召回率,其他三格是誤提案率,另報類別正確率;無關欄位擾動後答案改變的組數另報。防回歸:[test:test_the_eval_report_is_per_slice_and_marks_thin_slices]、[test:test_irrelevant_fields_do_not_change_the_answer]、[test:test_the_code_rule_is_scored_per_slice_like_a_candidate]。
- 品質門檻(使用者 2026-09-24 本人裁定定案):「不誤提案的比例」與類別正確率下界 ≥ 0.95、每格至少 73 筆;「值得加」格召回率下界 ≥ 0.80、至少 16 筆。成本、延遲、失敗率門檻在接上第一個候選時由使用者裁定。
- 報告分兩種型別(代碼審第 1 輪:原本只靠一個字串區分,合成集結果改個字串就能被當成正式):合成集報告不含信賴下界;正式報告只能經計分模組的建構函式建出來(帶簽發者),必帶隱藏集出處(版本、雜湊、抽樣與人工標註出處、候選版本、候選是否早於揭露),每項指標存分子、分母、點估計與 95% Wilson 下界。代碼審第 2、3 輪補:正式報告每一筆都要交給候選,候選答的或候選退回的(逾時、例外、不知道、非法回傳)都算候選的結果,任何一筆走現行規則就拒(第 2 輪只要求每格一筆,1 筆候選加 72 筆現行規則就能讓現行規則替候選過關);隱藏集雜湊用領域層共用的雜湊判準;「候選是否早於揭露」要是真的布林值。
- 採用一律 fail-closed:證據不是正式報告、候選不早於揭露、這一格沒有報告或沒有比較表那一列、任何欄位沒量或不合法(非有限、負數、比率超過 1)、門檻還沒裁定,都不驗證。一格要品質下界過門檻(值得加格召回率 0.80;其他四格「不誤提案的比例」與類別正確率都要 0.95,兩項都比),而且這一格的成本、延遲中位與 p95、各失敗率都不超過門檻。比較表是逐格一列,列上記的格要等於它被拿來判的那一格(掛錯鍵視同沒量);有限數判準用領域層共用的檢查、另加不為負;結論、理由與缺的證據都從同一個採用結果衍生,採用時不印不採用理由。最少樣本(16、73)由下界本身擋住,不另寫樣本數檢查;其他三格只比類別正確率,因為每次誤提案也是一次類別錯誤——兩道另寫的檢查變異檢查都證實拿掉結果不變。防回歸:[test:test_a_slice_is_validated_only_when_every_bar_is_met]、[test:test_the_adoption_decision_is_no_without_measured_validated_slices]、[test:test_unmeasured_candidates_show_no_numbers]。
- 決定紀錄(`governance/eval/phase10-worth-adoption.md`,`python -m rtb.eval.record` 產生,命令列照專案的兩層形狀;評估套件不准匯入維運套件,所以比照執行端各自建解析器)。2026-09-24 用第四版評估集:結論不採用;現行規則在暫停格誤提案 39/60、資料異常格類別全錯(36 筆判成值得加、24 筆判成不值得加)、沒價值格 60/60 判成值得加,沒投放與有價值兩格全對;擾動 0 組改變。
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

- `src/rtb/eval/investigation_cases.py`:九格評分(= 標準答案的九條規則,順序寫死在 `ANSWER_ORDER`,系統提示的九條照同一個順序)、標準答案產生函式、生成器。標準答案的比率只經領域層的精確比率函式取得、用分數判門檻,不讀捨入後的收據字串;資料異常直接用 Phase 10 的 `is_anomalous` 看原始 1 小時指標。生成器每格 4 組,每組一筆正常名稱配一份名稱藏誘導文字的雙胞胎(數字完全相同),共 72 筆;任何百分比門檻(轉換率變化 -50%、最近一次加預算的轉換變化 0%、前置過濾的配速 50%)的精確值離門檻不到 0.05 個百分點就重抽,每筆也要落在它要的格,不然生成器自己丟錯。比照 Phase 10 生成器,不匯入分析端(選項代碼另寫一份,測試核對一致)。防回歸:[test:test_the_answer_key_applies_the_history_rules_in_order]、[test:test_no_generated_case_sits_on_a_rounding_boundary]、[test:test_one_exact_ratio_function_feeds_the_answer_key]。
- `src/rtb/eval/investigation_set.py`:生成器的產出(不要手改);改題目、名稱或查詢結果會讓錄製鍵對不上,要重錄一批。
- `src/rtb/eval/investigation_eval.py`:評估執行器與命令列 `python -m rtb.eval.investigation_eval`。逐筆呼叫正式路徑同一支 AI 決策函式(見 [[Systems/分析行程流程與檢查點]]),只把查詢來源換成案例存的原始結果(收據用正式路徑同一支收據函式算)、續租回呼永遠成功、先前各輪紀錄放在記憶體;沒有自己的驗證或輪數邏輯。預設重播入庫目錄 recordings/model/phase13-investigation-eval/;即時加錄製要帶 `phase13-eval-YYYYMMDD` 批次,開閘道時先跑模型用戶端的開錄前目錄檢查(見 [[Systems/模型用戶端]]);`--verify` 只准重播,照〈錄製批次與入庫〉的驗過條件核(失敗類錄製 0 份、同一批、沒有佔位、重播找不到錄製 0 筆),驗不過以 1 結束。評估批次不另存批次紀錄檔:模型那一列從重播帶回的錄製當時延遲與原價算,錄製日期與批次讀錄製檔。防回歸:[test:test_the_investigation_eval_runs_the_same_ai_judge]、[test:test_the_batch_check_goes_red_on_missing_failed_or_mixed_recordings]、[test:test_the_eval_runner_refuses_to_record_into_a_mixed_directory]。
- `src/rtb/eval/investigation_report.py`:逐格報告只算名稱正常的 4 筆(誤提案、類別正確、值得加格召回、平均與最多輪數、每個決策的原價、退回原因分布);對抗切片另列誘導雙胞胎結論翻轉的筆數與差異;比較表是現行程式規則(實測)對模型(歷史觀測、錄製日期);採用一律不採用、不建任何已驗證清單。模型那一列用本計劃自己的門檻常數 `INVESTIGATION_LIMITS`:成本不設門檻(`cost_exempt`),延遲 p95 3 秒、失敗率 1%。防回歸:[test:test_the_investigation_report_is_per_slice_and_never_adopts_synthetic]、[test:test_the_report_counts_decisions_flipped_by_injected_names]、[test:test_the_investigation_evaluation_never_validates_a_slice]。
- `src/rtb/eval/adoption.py` 的門檻型別加 `cost_exempt`(預設假;為真時成本門檻必須是 None);採用判定抽成 `operational_problems`,Phase 10 的逐格採用與調查評估的模型那一列共用,`cost_exempt` 為真就不比成本、延遲與失敗率照查。`src/rtb/eval/model_candidate.py` 的逐欄判定在 `cost_exempt` 為真時成本那一欄寫「不設門檻(假設正式環境用自研模型、成本另計)」;Phase 11B 模型候選那組門檻常數不動。防回歸:[test:test_an_explicit_no_cost_gate_skips_only_the_cost_check]、[test:test_the_report_writes_no_cost_gate_for_an_exempt_limit]。
- 匯入邊界([S918] 照 Phase 13 改寫):評估套件閉包的准許名單加模型閘道、AI 決策模組(ai_judge 與它的詞彙模組)與這四支;匯入 AI 決策模組、經它開閘道送出的,評估套件裡只准評估執行器。
- 決定紀錄 `governance/eval/phase13-investigation-adoption.md`(命令列產生)。2026-09-25 入庫時還沒有評估錄製:72 筆全部「找不到錄製」、退回現行規則,模型那一列沒量,結論不採用。現行規則實測:暫停、資料異常、裁定 12 三格與沒價值格全錯(有投放就提案),較長窗有轉換、沒投放、有價值三格全對。
- `tests/eval/test_investigation_eval.py`:上面各條的合約測試([S1117]–[S1119]、[S1133]、[S1140]、[S1141]、[S1146]、[S1155]、[S1159]、[S1163]、[S1165]),以及 [S1162] 標準答案那半的檢查函式(由 tests/domain/test_metrics.py 綁 [S1162] 的那支呼叫)。全部用假的模型呼叫或假 claude,不碰真模型。
- 代碼審 r1 補強(2026-09-25):比較表某格只要有一筆正常案例沒真的呼叫到模型就寫「沒量(錄製不全)」,任一筆缺錄時模型那一列整列沒量,品質與退回率的分母是筆數;成本豁免時成本欄可以沒量;評估套件只經模型用戶端門面(匯入檢查涵蓋全部 model 開頭的檔);即時加錄製一定要給入庫目錄以外的新目錄;評估執行器匯入就算送出點。防回歸:[test:test_a_partly_recorded_batch_is_not_reported_as_measured]、[test:test_the_fallback_rate_counts_cases_not_calls]、[test:test_the_answer_key_boundaries_match_the_receipts]、[test:test_exempt_limits_still_check_failure_rates_and_the_batch_check_counts_ledger_busy]、[test:test_cost_exempt_ignores_an_unmeasured_cost]、[test:test_live_recording_must_go_to_a_fresh_directory]、[test:test_importing_the_eval_runner_counts_as_a_send_point]。
