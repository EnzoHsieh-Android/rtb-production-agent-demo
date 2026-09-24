---
type: system
status: doing
created: 2026-09-24
updated: 2026-09-24
responsibility: 負責「值不值得加」判斷點的離線評估:評分表、合成評估集與生成器、逐格計分與報告、比較表、逐格採用決定與人讀的決定紀錄;不負責判斷點本身與路由(在分析端的決策規則),不讀也不寫任何資料庫,不被任何其他套件匯入
aliases: []
about_code:
  - src/rtb/eval/__init__.py
  - src/rtb/eval/rubric.py
  - src/rtb/eval/generator.py
  - src/rtb/eval/eval_set.py
  - src/rtb/eval/scoring.py
  - src/rtb/eval/adoption.py
  - src/rtb/eval/record.py
tags:
  - type/system
  - status/doing
summary: |-
  WHY: [2026-09-24] 交接文件 Phase 10:只在有證據的窄決策點評估 Jev,依切片報品質、跟程式基準比品質成本延遲、沒達門檻明確不採用。使用者本人裁定評估對象是「值不值得加」這一個判斷,結論照實寫兩個不採用理由。出處:[[Projects/RTB_Phase10評估與Jev決策點_計劃]] 增量 2。
  RULE: 合成評估集只是有限的合約案例,不套統計信賴、不能產生已驗證清單;已驗證清單只能來自正式環境隱藏抽樣集,而且只有採用函式建得出來。[since:2026-09-24] [retire:接上正式環境抽樣集與人工標註、改用它們重建評估時] [test:test_evaluation_calls_the_candidate_without_validating_anything]
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
