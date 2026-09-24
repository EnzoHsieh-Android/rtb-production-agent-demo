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
---
# 評估與Jev決策點

本篇管 `src/rtb/eval/` 七支檔:評分表(`rubric.py`)、生成器(`generator.py`)、合成評估集(`eval_set.py`,生成器的產出,不要手改)、逐筆計分與逐格報告(`scoring.py`)、比較表與逐格採用決定(`adoption.py`)、人讀的決定紀錄(`record.py`,`python -m rtb.eval.record` 產生)、套件說明(`__init__.py`)。判斷點本身、路由與待測格清單的型別在 [[Systems/分析行程流程與檢查點]],判斷點的輸入與評分格在 [[Systems/任務流程領域模型]]。

- 評分表:暫停 → 不值得加;啟用但曝光點擊不全是正數 → 不值得加;都是正數且轉換或營收有正數 → 值得加;否則 → 證據不足。「證據不足」那格與三類答案是使用者本人裁定,其餘是暫用值。標準答案由程式照評分表算,不派代理標註。防回歸:[test:test_the_rubric_gives_exactly_one_class_for_every_input]。
- 合成評估集:每格 20 組基準情境,每組三個變體(基準、只改花費仍在偏低範圍、只改預算),共 240 筆;正數計數照漏斗順序造(點擊不多於曝光、轉換不多於點擊)。雜湊由測試釘住,換批要用決策指令記一筆。防回歸:[test:test_the_eval_set_is_pinned_by_hash]。
- 計分:逐筆經分析端的路由函式,依退回後的最後有效答案計分;現行規則用同一套(不傳候選)。每格報分子分母、類別正確數、錯誤子型與走了哪條路;「值得加」格的指標是召回率,其他三格是誤提案率,另報類別正確率;無關欄位擾動後答案改變的組數另報。防回歸:[test:test_the_eval_report_is_per_slice_and_marks_thin_slices]、[test:test_irrelevant_fields_do_not_change_the_answer]、[test:test_the_code_rule_is_scored_per_slice_like_a_candidate]。
- 採用:一格要在正式環境抽樣集上、品質的 95% Wilson 下界過門檻(值得加格召回率 0.80,其他三格類別正確率 0.95),而且成本、延遲、各失敗率都量過且不超過門檻(門檻由使用者在接上第一個候選時裁定);比較表有沒量的欄位就擋。最少樣本(16、73)由下界本身擋住,不另寫樣本數檢查;其他三格只比類別正確率,因為每次誤提案也是一次類別錯誤——兩道另寫的檢查變異檢查都證實拿掉結果不變。防回歸:[test:test_a_slice_is_validated_only_when_every_bar_is_met]、[test:test_the_adoption_decision_is_no_without_measured_validated_slices]、[test:test_unmeasured_candidates_show_no_numbers]。
- 決定紀錄(`governance/eval/phase10-worth-adoption.md`,2026-09-24 產生):結論不採用;現行規則在暫停格誤提案 33/60、證據不足格 60/60 全判成值得加,其他兩格全對;擾動 0 組改變;經路由的延遲本機單次量測中位約 0.8 微秒。
- 評分表原意對照(`governance/eval/rubric-intent-check.md`):乾淨代理在「曝光點擊不全是正數」「有價值」兩格 6/6 答「證據不足」,跟評分表不一致,已列給使用者覆核,評分表沒改。
