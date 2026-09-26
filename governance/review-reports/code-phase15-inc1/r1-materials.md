# r1 派工材料凍結(派工當下 d336754 的樹;含 patch 與派工單列出的全文檔)

## r1-snapshot.patch
diff --git a/docs/rtb-production-agent-demo-knowledge/MOC/index.md b/docs/rtb-production-agent-demo-knowledge/MOC/index.md
index 4b2d565..857ccde 100644
--- a/docs/rtb-production-agent-demo-knowledge/MOC/index.md
+++ b/docs/rtb-production-agent-demo-knowledge/MOC/index.md
@@ -20,20 +20,21 @@ status: doing
 - [[Systems/外部寫入嘗試紀錄]]
 - [[Systems/宣稱驗證器]]
 - [[Systems/寫入能力憑證]]
 - [[Systems/展示頁面]]
 - [[Systems/提案收件口]]
 - [[Systems/有界標籤的指標]]
 - [[Systems/服務水準與燒損告警]]
 - [[Systems/模型用戶端]]
 - [[Systems/死信重放指令]]
 - [[Systems/確定性指標計算]]
+- [[Systems/規則模式探索評估]]
 - [[Systems/稽核表只增不改守衛]]
 - [[Systems/評估與Jev決策點]]
 - [[Systems/調查實演]]
 - [[Systems/追蹤檢視]]
 - [[Systems/靜態檢查閘]]
 
 ## 計劃
 
 - [[Projects/F7效能_計劃]]
 - [[Projects/README流程動圖_計劃]]
diff --git a/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase15AI找規則模式_計劃.md b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase15AI找規則模式_計劃.md
index 3949138..8cc0361 100644
--- a/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase15AI找規則模式_計劃.md
+++ b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase15AI找規則模式_計劃.md
@@ -7,20 +7,21 @@ plan_risk: high
 summary: |-
   WHY: 2026-09-26 使用者裁定，Phase 13 評估顯示程式可算的加額判斷由程式較好，AI 退出正式加額決策；本計劃只用固定種子的合成歷史離線找新規則模式，機械核對並與窮舉基準比較，經人確認及 Issue／設計審／代碼審後才可能寫進九條或新條。出處：本次使用者對話；[[Projects/RTB_Phase13AI參與決策_計劃]]、[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]。
   VERIFY: 本文重驗入口與預定報告指標是 --ai-judge、--batch-id、--demo-id、--ledger、--recordings-dir、--verify、governance/eval/ 下的 phase15-rule-mining.md；涉及的程式路徑以程式碼為準，開場用 `rg --files src/rtb tests` 重驗：investigation_eval.py、investigation_report.py、src/rtb/analyzer/modelgate.py、src/rtb/domain/metrics.py、src/rtb/domain/nine_rules.py、src/rtb/dsp/store.py、src/rtb/eval/investigation_cases.py、src/rtb/eval/investigation_eval.py、src/rtb/modelclient.py、src/rtb/modelledger.py、src/rtb/modelledger_view.py、src/rtb/modelrecording.py、tests/test_spawn_boundary.py、tests/eval/test_model_candidate.py、tests/model/test_shared_entry.py。
 tags:
   - type/project
   - status/doing
 lands_in:
   - Systems/評估與Jev決策點
   - Systems/模型用戶端
   - Systems/規則模式探索模型入口
+  - Systems/規則模式探索評估
 ---
 # RTB_Phase15AI找規則模式_計劃
 
 PRIOR-ART: 最小解是 Phase 10／13 已有的固定種子合成集、逐筆重播與逐格報告，加一個離線規則搜尋實驗；借用 Phase 13 的封閉模型答案、共用模型閘道、錄製鍵與批次驗收，借用 `rtb.domain.metrics` 的精確比率。一般規則探勘先列有限條件、用同資料重算證據、以未見資料重驗，並與窮舉掃描比較；這裡用標準函式庫實作有限掃描，不採新套件或自動代理。出處：[[Projects/RTB_Phase10評估與Jev決策點_計劃]]、[[Projects/RTB_Phase13AI參與決策_計劃]]、本次指定程式前掃；沒有把合成結果當正式環境證據。
 RETIRE-IF: 固定種子 15001、15002、15003 各以第一次 `outcome=ok` 且有效入庫的錄製作判定批次；令各批 AI 有效建議數為 n，先取定窮舉前 K=10 與前 n、AI 有效清單，再做保留集篩選，不遞補。召回用 K，精確度／誤報用同名額 n；AI 所有單條剔除、去重、反方向、核對不符及未達下限的提交均計「無效提交」，在 AI 誤報分子與分母各加一；相關誘餌另列，不進可計分母。三批過濾前／後 K 名額真模式召回均不低於 AI、同名額精確度均不低於 AI，且 AI 沒提供經人確認的額外可核對條件，就停用模型探勘呼叫、只留程式掃描；n=0 或任一方同名額可計分母為零的批次，該批比較判「窮舉勝」，展示仍寫「未量」而不把 0/0 當 0%。分母非零時，精確度不低於等價於可計誤報比例不高於。任一批可解析建議的機械筆數核對失敗比例超過 25%，先停用模型段並重審輸出契約；此比例只量數字轉錄一致性。非 `ok` 的呼叫不入庫、不參與三批判斷，記「呼叫失敗」並以同種子、全新展示編號重錄，不算新種子批；成功入庫後不得以同版本重錄替換，須換評估版本並記理由。撤模型時保留 `Caller.RULE_MINING`、`CAPPED_CALLERS` 成員、`CALLER_USERS` 對應鍵及歷史驗證模組、錄製與帳列；移除命令列及分析端送出入口和報告連結，CI 改驗歷史批次。事件入口：每次 `--verify` 重播與更換模型／條件語彙時的評估報告；真實歷史與人工標註可用時另開案。
 
 ## 這份計劃在解決什麼
 
 - Phase 13 讓 AI 參與「要不要加預算」，Phase 10／13 的評估顯示這種程式可算的判斷由程式較好。Phase 14 已由使用者於 2026-09-26 裁定 AI 退出加額決策，只保留提案說明、告警原因推測與本計劃的找新規則模式。Phase 14 增量 3 將移除分析端 runner 的 AI 判斷開關；AI 決策模組只留給評估重播，正式流程此後沒有 AI 通往提案的入口。Phase 14 現存計劃仍把 `--ai-judge` 留在正式入口，故不能只憑「增量 3 已合入」宣稱退出；本案須等包含上述移除的增量 3 合入 main，實作前再核對 runner 已無 AI 判斷開關、展示驅動不再組該參數且 F7 可走規則路徑。本案不新增任何通往提案的路徑。
 - 本案用合成歷史問「什麼可觀察條件下，加額後的成效較可能改善或不改善」，產出待人判斷的規則建議。AI 的答案只到離線報告，不送進分析行程、提案、收件口、執行端或 DSP 預算寫入。
@@ -28,20 +29,24 @@ RETIRE-IF: 固定種子 15001、15002、15003 各以第一次 `outcome=ok` 且
 
 ## 使用者裁定
 
 ### 2026-09-26 使用者本人
 
 1. 用程式生成數百支廣告、數週的固定種子合成歷史，事先埋真規律與假規律／巧合；可重產，結論不當統計保證。
 2. 模型只產結構化建議報告，須有條件、門檻、方向、支持與反例筆數、信心說明；人確認後才開 Issue，經設計審、代碼審寫成九條或新條。模型不直接改規則、程式或設定。
 3. 離線命令列產一份人讀報告，含真規律找回、誘餌誤報與機械核對；錄製入庫，README 連報告，不進一鍵展示頁。
 4. AI 不再做加額決策；此計劃是保留的第三個 AI 角色。即時模式比照 Phase 13 須使用者明確授權；本輪只寫計劃，不呼叫模型。
 
+### 2026-09-27 使用者本人
+
+5. 開工，全程保持離線：AI 從歷史紀錄找候選規則 → 人與評估驗證 → 才可能變成程式。用意是讓「AI 退出決策」成為完整的故事——被評估拿掉的位置，改到離線提候選、靠證據取得採用資格，而不是 AI 被拔掉；README 開頭同步加一句核心主張「決策權必須由評估與可驗證證據取得」。
+
 ### 代使用者裁定(2026-09-26)
 
 - 前 K 中未精確命中預埋真規律者原則上計誤報（純噪音、只在探索側偶合、誘餌變形及部分重疊均含）；唯一例外是預埋的「保留側也成立但無獨立效果」相關誘餌，列「觀察成立的相關規律」，不計召回或誤報，另欄解釋。召回與誤報在保留集篩選前後各報一次。理由：可觀察的相關條件不能偽裝成因果真模式，也不能和純噪音混算。
 - 看任何生成結果前固定保留條件：保留側至少 20 個有方向配對且其中兩側各有 20 支相異廣告、支持比例至少 3/5、精確平均差值與候選方向同號；平手不進分母。理由：探索偶合須用同一條可重算的門檻檢驗。
 - 每條條件的對照只取同一切分側、整個觀察期從未加額的廣告，每支對照廣告在該條條件中最多使用一次；至少 20 **有方向**配對須同時有至少 20 支不同加額廣告及 20 支不同對照廣告，星期類也照算；支持比例分母同為有方向配對數。理由：防跨側洩漏、重複對照和平手灌大樣本；19 平手＋1 正差即使涉及 20 支廣告，仍未達下限、無效。
 - 模型一批只呼叫一次且不得分塊；完整提示需同時通過本案 20480 位元組、既有 48 KiB 與每展示剩餘上限。過大先在換評估版本時縮小固定語彙，仍過大就拒跑。理由：多塊沒有公平的全域前 K，完整表大小須先驗。
 - AI 建議也須達相同樣本下限；差值為零的配對既不支持也不反例。模型信心說明只留原始回覆，報告只寫結構化欄位及程式重算數字。理由：兩邊分母與證據口徑一致，避免未核對文字偽裝報告。
 - 窮舉排序第一鍵用支持比例的 Wilson 95% 下界；K、下限、門檻、排序、配對、切分及兩方向名額在看基準或 AI 結果前凍結，事後不得往偏袒任一方調，需改先換評估版本並記對雙方的預期影響。模型可少於 K 條；撤除比較以 AI 有效建議數 n 為等名額，窮舉只取前 n 條比精確度，真模式召回另用 K 報。AI 無效提交列入 AI 誤報分母與分子；n=0 記窮舉勝。理由：小樣本比例須保守排序，少報或踢掉無效條目不能美化比較。
 - 回退保留 `Caller.RULE_MINING` 和上限集合成員、錄製目錄及歷史帳列，只撤送出路徑與報告連結；報告固定在 `governance/eval/` 目錄，檔名 `phase15-rule-mining.md`。理由：舊批可讀、月上限不失守，並沿用 Phase 10／13 落點。
 - 實作入口等 Phase 14 **移除 runner 的 AI 判斷開關**之增量 3 合入 main 後才開，實作前核對 runner 已無該開關、展示驅動不再組參數且 F7 走規則路徑；AI 決策模組只留評估重播，本案不新增通往提案的路徑。理由：既有 Phase 14 計劃讓 AI 提案仍可經九條通過，單有規則否決不足以實現「AI 退出加額決策」。
@@ -50,20 +55,21 @@ RETIRE-IF: 固定種子 15001、15002、15003 各以第一次 `outcome=ok` 且
 - 提示中的比例採百分比一位小數、差值四位小數，均以十進位 half-even 捨入；精確分數留程式端核對。增量 1 先以固定三種子量完整提示位元組，任何一批超過單次上限即失敗。理由：精確分數可能膨脹到使單次呼叫不可行。
 - 離線呼叫逾時為 60 秒；錄製入庫還要該次呼叫結果 `ok`，逾時、額度、超支及暫時性錯誤記「呼叫失敗」，不入庫、不算 RETIRE-IF。信心說明最多 80 字；非有限數含 `1e400` 與任何物件層未知鍵整份拒絕。理由：避免失敗批次被當成模型輸、輸出撞頂或解析口徑分裂。
 - 同名額任一方分母為零（含前 n 全是相關誘餌或保留側全被刷掉）即判該批「窮舉勝」；先取名單再篩保留側，不遞補。AI 所有不合格單條提交，包括未知門檻、兩方向都押、數字不符、未達下限、重複與格式錯誤，均計 AI 誤報；整份回覆因頂層格式或解析錯誤拒絕時，記 1 筆無效提交及 AI 誤報，n=0 仍判窮舉勝。理由：比較必能終止，且不能藉剔除洗掉錯選。
 - 三種子各用專屬 `phase15-seed-<種子>-<序號>` 展示編號，從 1 遞增，每次重錄換新序號；每種子只准第一次成功錄製入庫，清單記編號與時間，替換須換評估版本並記理由。6144 輸出下每展示上限約剩 0.058 美元，失敗呼叫整筆預留扣帳，不在同一展示編號重試。理由：防重抽挑好結果，並避免展示額度卡住後續種子。
 - Phase 14 前置條件同時驗 runner 已無 AI 判斷開關、展示驅動不再組 `--ai-judge` 且 F7 走規則路徑。Wilson 下界直接用 `rtb.eval.scoring.wilson_lower()`；百分比用 `rtb.domain.metrics.percent_text` 等既有函式，精度與格式以程式碼為準。理由：不能只移除參數接收端，也不另造統計或格式化做法。
 - 門檻代碼一律用「欄位:區間」全名（如 `raise_pct:band_2`），未改善方向代碼為 `not_improve`，兩側相異廣告數只數有方向配對。模型提示要求 UTF-8 原字、不用 `\uXXXX`；回覆大小按實際 JSON 逸出後位元組計，解析超長整數的 `ValueError` 與非有限數同樣整份拒絕。理由：讓真相鍵、下限及輸出界一致，解析失敗不使命令列崩潰。
 - 回退保留 `Caller.RULE_MINING` 的值 `rule_mining`、`CAPPED_CALLERS`、`CALLER_USERS` 鍵及歷史驗證模組的值、值→成員名對照及 [S918] 精確等式；只撤命令列與分析端送出入口及其白名單新增項，更新 [S918] 窄入口等式。理由：歷史錄製鍵仍可重算，邊界測試仍能守住精確集合。
 
 ## 現況（2026-09-26，分支 `phase15-rule-mining` 的 main 基底 `71b04d5`；以程式碼為準）
 
+- 2026-09-27 增量 1 實作：分支 `phase15-inc1`，main 基底 `61bbb31`（含 Phase 14 增量 3）。開工前核對 Phase 14 前置條件：分析端驅動的參數只剩資料庫、DSP、收件口、逾時與間隔（無 AI 判斷開關），展示驅動組分析端參數時只給這幾項，[S1429] 的閉包測試通過；F7 未另實跑，依展示驅動只組規則參數推得走規則路徑。重新核對：`rg -n "add_argument" src/rtb/analyzer/runner.py`、`rg -n "def analyzer_args" -A5 src/rtb/demo/driver.py`。增量 1 的四支純離線模組與評估版本 v1 的可達性預檢記在 [[Systems/規則模式探索評估]]；未新增模型呼叫或通往提案的路徑。
 - `src/rtb/analyzer/modelgate.py` 的 `open_gate()` 在入口判錄製或即時、綁死 `Caller`、錄製目錄及花費帳；`Gate.complete()` 把請求交給 `src/rtb/modelclient.py`。即時要 `RTB_MODEL_LIVE=1`、展示編號、可用後端與有效啟用紀錄，重播缺錄製會報錯，不會默默轉即時。重新核對：`rg -n 'def open_gate|def complete|def settings_from_env|def call_model' src/rtb/analyzer/modelgate.py src/rtb/modelclient.py`。
 - `src/rtb/modelledger_view.py` 的 `Caller` 是封閉列舉；`src/rtb/modelledger.py` 的 `CAPPED_CALLERS` 現只含評估候選與實測，花費帳有按呼叫者的上限與估算。錄製鍵含呼叫者、模型、系統提示、使用者內容與輸出上限，批次與目錄有驗收。重新核對：`rg -n 'class Caller|CAPPED_CALLERS|def recording_key|def batch_file_problems' src/rtb/modelledger_view.py src/rtb/modelledger.py src/rtb/modelrecording.py src/rtb/modelclient.py`。
 - `src/rtb/eval/investigation_cases.py` 固定種子產 Phase 13 案例，`investigation_eval.py` 以同一 AI 函式重播／即時執行並驗批次，`investigation_report.py` 把結果寫成人讀報告。`src/rtb/eval/ruff.toml` 禁直接匯入模型閘道與 DSP，`src/rtb/analyzer/ruff.toml` 禁匯入評估套件；Phase 10 [S712] 的反向匯入界線仍成立。重新核對：`rg -n 'SEED|def generate|def run|def batch_problems|modelgate|rtb.eval' src/rtb/eval/investigation_cases.py src/rtb/eval/investigation_eval.py src/rtb/eval/ruff.toml src/rtb/analyzer/ruff.toml`。
 - `src/rtb/dsp/store.py` 的逐日成效以 UTC 日期桶保存，金額用整數分，最近加額由正常操作紀錄連同調整前預算與提交時刻推得；`src/rtb/domain/nine_rules.py` 有正式九條的純領域詞彙與順序，`src/rtb/domain/metrics.py` 有 `exact_ratio`／`exact_change`。本案只仿照歷史資料語意建離線案例，不從 DSP 讀取，不匯入九條規則來生成答案。重新核對：`rg -n 'daily_metrics|_latest_raise|def get_past_adjustments|ANSWER_ORDER|def exact_ratio|def exact_change' src/rtb/dsp/store.py src/rtb/domain/nine_rules.py src/rtb/domain/metrics.py`。
 
 ## 設計
 
 ### 合成歷史與可觀察效果
 
 - 評估套件新增純生成器與資料型別：固定種子、固定評估時鐘，至少 300 支虛構廣告、至少 28 個連續 UTC 日。每支有每天曝光、點擊、轉換、花費、營收的完整日桶，另有零或多筆含加額前後預算及 `committed_at` 的操作；計數遵守資料自洽，金額先以整數分生成。生成器可 `render(generate(seed))` 得到位元組一致的評估集與雜湊；另存只給評估器的埋入真相清單（真模式、誘餌、生成版本），不能放進模型提示。
diff --git a/docs/rtb-production-agent-demo-knowledge/Systems/規則模式探索評估.md b/docs/rtb-production-agent-demo-knowledge/Systems/規則模式探索評估.md
new file mode 100644
index 0000000..3b8bde0
--- /dev/null
+++ b/docs/rtb-production-agent-demo-knowledge/Systems/規則模式探索評估.md
@@ -0,0 +1,83 @@
+---
+type: system
+status: doing
+created: 2026-09-27
+updated: 2026-09-27
+responsibility: 負責 Phase 15 規則模式探索的純離線評估:封閉條件語彙與評估版本常數、固定三種子合成歷史與真相清單、D±3 完整日效果與同側配對、彙總表、樣本下限、保留側判定、窮舉前 K、固定系統提示與完整提示位元組閘、可達性預檢;不負責模型呼叫、回覆解析、錄製與人讀報告(增量 2/3),不匯入模型閘道、模型用戶端或 DSP,不通往提案或正式九條
+aliases: []
+about_code:
+  - src/rtb/eval/rule_mining_vocab.py
+  - src/rtb/eval/rule_mining_history.py
+  - src/rtb/eval/rule_mining_baseline.py
+  - src/rtb/eval/rule_mining_prompt.py
+  - tests/eval/test_rule_mining.py
+tags:
+  - type/system
+  - status/doing
+summary: |-
+  WHY: [2026-09-27 Phase 15 增量 1] AI 退出加額決策後保留的第三個角色是「離線提候選規則、靠證據取得採用資格」;這一篇先做不接模型的一半:固定種子合成歷史、可重算的配對效果、封閉語彙彙總與不用 AI 的窮舉前 K,讓之後的模型建議有同口徑的對照組。出處 [[Projects/RTB_Phase15AI找規則模式_計劃]]〈拆增量〉1 與〈使用者裁定〉5。
+  RULE: 評估版本參數(三種子、K、有方向下限與兩側相異廣告下限、保留 3/5、切點、SHA-256 切側、整期未加額對照池、配對欄位、搶用次序、絕對差效果與平均公式、Wilson 排序鍵、格式化、輸入輸出上限)在看任何基準或模型結果前凍結;改任何一項先換評估版本並記理由、可達性預檢與對 AI、窮舉雙方的預期影響,三個種子永不替換。[since:2026-09-27] [retire:Phase 15 依 RETIRE-IF 停用模型探勘且不再重跑基準時] [test:test_rule_mining_pairing_parameters_are_frozen] [test:test_rule_mining_preflight_checks_all_fixed_seed_prompts]
+  RULE: 真相清單與保留側只給評估器,不能進送模型的彙總表或系統提示;彙總表只列探索側達下限的條件,不送逐日列、識別、時間戳或精確分數。[since:2026-09-27] [retire:改用真實歷史與人工標註另開案時] [test:test_rule_mining_prompt_contains_only_bounded_aggregates]
+  FLOW: 生成歷史 → SHA-256 切側 → 每側加額事件與排除計數 → 整期未加額對照索引 → 每條件不放回配對 → 彙總 → 下限 → 彙總表與位元組閘 → 三種子預檢;保留側判定與窮舉前 K 用同一份每條件彙總。以程式碼為準:`rg -n "^def " src/rtb/eval/rule_mining_baseline.py src/rtb/eval/rule_mining_prompt.py`
+  DEP: 轉換率與比例格式用 [[Systems/確定性指標計算]] 的精確比率與百分比字串;Wilson 下界直接用 [[Systems/評估與Jev決策點]] 計分模組的既有函式;評估套件匯入閉包白名單在 [[Systems/評估與Jev決策點]] 的測試精確新增這四支。
+decisions:
+  - content: 建立評估版本 phase15-rule-mining-v1(生成版本 rule-mining-history-1)
+    id: d1
+    context: 首版;參數照計劃逐字凍結於語彙模組與提示模組,看任何窮舉排名前寫死;三種子可達性預檢全過(見正文〈評估版本理由〉)
+    why_chosen: 計劃要求建立評估版本時預檢完整提示位元組與真模式/誘餌分母;預檢只看分母與位元組,不看正負差或排名;首版無前版可比
+    decided: 2026-09-27
+    valid: true
+---
+# 規則模式探索評估
+
+Phase 15 增量 1(計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]):不呼叫模型、沒有命令列;四支純函式模組加一支測試。合成資料只證流程可運作,不給統計或因果保證。
+
+## 四支模組
+
+- `src/rtb/eval/rule_mining_vocab.py`:評估版本的單一出處(`EVAL_VERSION`、`SEEDS`、`K`、下限、保留比例、切點、規模桶、上限、`version_params()`),以及封閉條件語彙:四欄 day_type(平日/週末)、pre_cvr、raise_pct、spend_ratio(各三區間),一條條件是一或兩個不同欄位的子句 AND;門檻代碼一律「欄位:區間」全名;`condition_key` / `normalized_key` 對語彙外、同欄重複、子句數錯、未知方向丟 `VocabularyError`(帶原因代碼,給增量 2 的解析器逐條記)。全集 56 條(11 單欄 + 45 兩欄)。
+- `src/rtb/eval/rule_mining_history.py`:型別(日桶、操作、廣告、歷史、真相項)、`generate(seed)`(只收三個固定種子)、`render` / `data_sha256` 與寫死的三批預期雜湊、`split`。720 支廣告、35 個連續 UTC 日、一半廣告各兩次加額;另刻意放少量前後視窗出界、視窗重疊與 no_data,讓排除計數有東西可數。
+- `src/rtb/eval/rule_mining_baseline.py`:事件與排除(判斷先後:視窗出界 > 視窗內另有調整 > 資料異常 > 缺值 > 分母為零)、對照索引、`pair_condition`、`stats_of`、`summarize`、`meets_floor`、`directional`、`holdout_verdict`、`rank` / `top_k`、分母可達性 `Reach`。
+- `src/rtb/eval/rule_mining_prompt.py`:定稿的系統提示與表頭、`summary_table`、`diff_text`(百分點四位小數 half-even)、`gate_problem`(B>20480 或達 48 KiB 拒)、`version_sha256` 與寫死的 `EXPECTED_VERSION_SHA256`、預檢 `preflight` / `preflight_problems` / `preflight_record` 與寫死的 `PREFLIGHT_RECORD`。
+
+- `tests/eval/test_rule_mining.py`:本增量九條驗收條款 [S1501] [S1502] [S1503] [S1511] [S1512] [S1513] [S1515] [S1519] [S1520] 的測試;手造小歷史驗單一規則,固定三種子整批只生成一次;[S1503] 只驗彙總內容與位元組閘,不接模型。另以十種變異(對照重用、效果改尺度、平手算方向、原始比例排序、對照池收加額廣告、不查重疊、保留比例 1/2、平均為零算保留、不配規模桶、奇數多出者進探索)各跑一次,全數翻紅。
+
+## 埋入的真相(只給評估器)
+
+- 真模式・改善:raise_pct:band_2 且 spend_ratio:band_3(預算幾乎花滿的廣告中度加額),後三天轉換率乘 1.6。
+- 真模式・未改善:raise_pct:band_3(加額 50% 以上),後三天轉換率乘 0.5。
+- 誘餌・只探索側偶合:day_type:weekend 且 raise_pct:band_1 改善——生成器只對探索側廣告埋 1.6 倍,保留側沒有。
+- 誘餌・相關無獨立效果:spend_ratio:band_1 未改善——花費比低的廣告有七成被大幅加額,所以看似變差,本身不改轉換率。
+- 生成器用評估器同一套語彙函式,從已生成的前三日資料算事件的欄位值再決定埋不埋效果,真模式與切點精確對齊;所以之後報告的召回只是「可精確表示時」的上界。
+
+## 口徑(計劃逐字,實作選擇另記)
+
+- 效果:組變化 = 後三日轉換率 − 前三日轉換率(絕對差,精確分數),配對差值 = 加額組變化 − 對照組變化;D 本身不算。
+- 對照:同側、整期從未加額(只看加額;純降額操作不取消對照資格,但會讓視窗重疊的事件排除)、同 D、同前三日轉換率區間、同規模桶;每條件每支至多一次。事件按 (D, 加額廣告編號位元組, committed_at, 操作識別碼) 先後搶用,對照按編號位元組升序。
+- 實作選擇:相異 UTC 日期數與兩側相異廣告數一樣只數有方向配對;事件層排除(出界、重疊、異常、缺值、分母為零)在分條件前計一次,放在彙總表的一行;「無對照」逐條件計,是表的最後一欄。
+- 保留側:有方向配對 ≥20 且其中相異加額、相異對照各 ≥20,支持/有方向 ≥ 3/5,方向化平均差值 >0;分母為零記「未量」。
+- 窮舉:兩方向合併,Wilson 下界(既有函式)→ 方向化平均 → 有方向數 → 條件鍵 → 方向代碼;同條件只留排序較高的方向;取前 K=10。
+
+## 評估版本理由
+
+### phase15-rule-mining-v1(2026-09-27 建立)
+
+- 首版,無前版可比,對 AI 與窮舉雙方沒有「改動前後」的影響可記;參數在看任何窮舉排名前寫進程式常數,評估版本雜湊 831dc0dc4736cce9…(全文見提示模組的 `EXPECTED_VERSION_SHA256`)。
+- 可達性預檢(三種子全過,不看正負差或排名;逐字版本是提示模組的 `PREFLIGHT_RECORD`,測試重算比對):
+
+| 種子 | 資料雜湊(前 16) | 可推斷事件 探索/保留 | 表列 | 完整提示 B |
+|---|---|---|---|---|
+| 15001 | f820e1a486d8b35d | 340/301 | 50 | 6045 |
+| 15002 | 2a9e4aac0f4ec020 | 318/339 | 49 | 5963 |
+| 15003 | ba7d6ebc8a3a8b4d | 333/324 | 50 | 6032 |
+
+- 排除原因(探索/保留):15001 出界 18/17、缺值 11/9、重疊 18/32;15002 出界 13/18、缺值 12/7、重疊 16/13;15003 出界 14/10、缺值 7/8、重疊 21/28。資料異常與分母為零三批都是 0(生成器照漏斗造、點擊至少 1)。
+- 真相分母(有方向配對/相異加額廣告/相異對照廣告,探索 → 保留),四項在三批兩側都達 20/20/20:
+  - 真模式改善:15001 48/40/48 → 57/45/57;15002 49/37/49 → 60/45/60;15003 62/49/62 → 53/39/53。
+  - 真模式未改善:15001 113/88/113 → 84/66/84;15002 101/74/101 → 116/88/116;15003 109/80/109 → 118/82/118。
+  - 只探索側誘餌:15001 45/44/45 → 40/38/40;15002 34/34/34 → 39/38/39;15003 48/46/48 → 27/27/27(最接近下限的一格)。
+  - 相關誘餌:15001 138/81/138 → 83/45/83;15002 109/60/109 → 112/67/112;15003 96/54/96 → 117/65/117。
+- 位元組:三批 B 約 6 KB,遠低於 20480 與 48 KiB;表列 49–50 條(56 條全集裡有 6–7 條未達下限)。
+
+- 首次看窮舉前 K 是在凍結提交 98d436c 之後,未因結果調任何參數。只作描述(正式召回/誤報表是增量 3 的報告):三批前 10 都含兩個真模式(名次 15001 第 4、6;15002 第 2、9;15003 第 4、5);只探索側誘餌三批都進前 10(第 7、1、8),保留側在 15001、15002 判不保留,15003 保留側 27 個有方向配對碰巧也過 3/5 而判保留;相關誘餌只在 15001 進前 10(第 8),三批保留側都成立;其餘名額多是真模式 raise_pct:band_3 加一欄的細分(依計劃計誤報)。
+
+重跑預檢:`PYTHONPATH=src python -c "from rtb.eval import rule_mining_prompt as p; print('\n'.join(p.preflight_record(p.preflight())), p.preflight_problems(p.preflight()))"`
diff --git a/docs/rtb-production-agent-demo-knowledge/Systems/評估與Jev決策點.md b/docs/rtb-production-agent-demo-knowledge/Systems/評估與Jev決策點.md
index 5a8f9d0..d8b9ecb 100644
--- a/docs/rtb-production-agent-demo-knowledge/Systems/評估與Jev決策點.md
+++ b/docs/rtb-production-agent-demo-knowledge/Systems/評估與Jev決策點.md
@@ -53,20 +53,22 @@ verified_by:
   - "[[Verification/Phase10驗收紀錄]]"
   - "[[Verification/Phase11B增量1驗收紀錄]]"
   - "[[Verification/Phase13增量3驗收紀錄]]"
   - "[[Verification/Phase14增量2a離線驗證]]"
   - "[[Verification/Phase14增量2b驗證紀錄]]"
   - "[[Verification/Phase14增量3驗證紀錄]]"
   - "[[Verification/Phase14增量4驗證紀錄]]"
 ---
 # 評估與Jev決策點
 
+2026-09-27 Phase 15 增量 1:[S918] 評估套件匯入閉包的准許名單精確新增規則模式探索的四支純離線模組(另立 `PHASE15_ALLOWED`,不放寬既有名單);它們不經模型用戶端、不送出,經門面、送出與 AI 決策模組的精確等式都沒變。套件說明(`__init__.py`)的准許名單段同步加註這四支。模組本身的家是 [[Systems/規則模式探索評估]]。[test:test_the_eval_package_reaches_the_model_only_through_the_model_client]
+
 2026-09-26 Phase 14 增量 1:評估標準答案在案例邊界轉成 [[Systems/正式九條判斷領域規則]] 的型別並呼叫同一決策;必要列內缺值與單日 no_data 回無格的證據不足,原 72 筆含雙胞胎的格與答案逐筆維持,固定匯入閉包名單只增純領域模組。[test:test_missing_row_values_are_insufficient_without_changing_the_72_cases] [test:test_the_eval_package_reaches_the_model_only_through_the_model_client]
 
 2026-09-26 代碼審折入:標準答案與正式規則刻意同源,只證接線一致、不作獨立品質證據;生成器仍不匯入分析端與 AI 決策模組。評估的區段轉換率邊界檢查共用 [[Systems/正式九條判斷領域規則]] 的公開函式,[S1403] 以精確下降 50.04% 而收據顯示 -50.0% 守捨入門檻。[test:test_eval_uses_the_domain_segment_rate] [test:test_exact_thresholds_distinguish_zero_denominators]
 
 本篇管 `src/rtb/eval/` 七支檔:評分表(`rubric.py`)、生成器(`generator.py`)、合成評估集(`eval_set.py`,生成器的產出,不要手改)、逐筆計分與逐格報告(`scoring.py`)、比較表與逐格採用決定(`adoption.py`)、人讀的決定紀錄(`record.py`,`python -m rtb.eval.record` 產生)、套件說明(`__init__.py`)。判斷點本身、路由與待測格清單的型別在 [[Systems/分析行程流程與檢查點]],判斷點的輸入與評分格在 [[Systems/任務流程領域模型]]。
 
 - 評分表(五條全由使用者本人裁定,原意對照後加「資料自不自洽」;由上而下第一個成立的):暫停 → 不值得加;資料異常(負數、缺值、點擊多於曝光、轉換多於點擊)→ 證據不足;曝光或點擊是零 → 不值得加;轉換或營收有正數 → 值得加;轉換營收都是零 → 證據不足。標準答案由程式照評分表算,不派代理標註。防回歸:[test:test_the_rubric_gives_exactly_one_class_for_every_input]。
 - 合成評估集(第四版):5 格 × 20 組基準情境 × 三個變體(基準原值、只改花費仍在偏低範圍、只改預算且花費不變),共 300 筆。正常格照漏斗順序造、沒有負數與缺值;資料異常格輪流造 12 種單一故障(五個欄位各缺值、各負數,只違反點擊多於曝光、只違反轉換多於點擊);每列多記歸檔的格與故障。計劃釘住的邊界案例放在各格前幾組。每種故障只動一欄(代碼審第 1 輪:點擊多於曝光的故障原本另把轉換歸零)。雜湊由測試釘住,另重跑生成器逐值比對;換批用決策指令記(d1、d2、d3)。防回歸:[test:test_the_eval_set_is_pinned_by_hash]、[test:test_the_generator_covers_every_anomaly_and_matches_the_committed_set]。
 - 計分:逐筆經分析端的路由函式,依退回後的最後有效答案計分;現行規則用同一套(不傳候選)。每格報分子分母、類別正確數、錯誤子型與走了哪條路;「值得加」格的指標是召回率,其他三格是誤提案率,另報類別正確率;無關欄位擾動後答案改變的組數另報。防回歸:[test:test_the_eval_report_is_per_slice_and_marks_thin_slices]、[test:test_irrelevant_fields_do_not_change_the_answer]、[test:test_the_code_rule_is_scored_per_slice_like_a_candidate]。
 - 品質門檻(使用者 2026-09-24 本人裁定定案):「不誤提案的比例」與類別正確率下界 ≥ 0.95、每格至少 73 筆;「值得加」格召回率下界 ≥ 0.80、至少 16 筆。成本、延遲、失敗率門檻在接上第一個候選時由使用者裁定。
diff --git a/src/rtb/eval/__init__.py b/src/rtb/eval/__init__.py
index a7c1799..a506a1e 100644
--- a/src/rtb/eval/__init__.py
+++ b/src/rtb/eval/__init__.py
@@ -1,13 +1,14 @@
 """「值不值得加」判斷點的離線評估(Phase 10 增量 2)。
 
 評估套件可以匯入分析端的判斷點與路由、領域層的型別;分析端、執行端、DSP、領域層、維運套件都不准
 匯入這裡(各目錄匯入規則的禁令加邊界測試,[S712])——規則作者的程式在結構上讀不到評估集。
 
 資料庫與子行程:除了經模型用戶端寫花費帳與呼叫模型,不讀寫任何資料庫、不啟動子行程(Phase 11B
 增量 1 有意識地放寬,計劃〈既有邊界怎麼改〉)。評估套件的匯入閉包跟 Phase 11B 開工前的基準相比,只准
 多出寫死的准許名單:模型用戶端閉包、分析端模型閘道與 AI 決策模組、Phase 13 的調查評估模組([S918],
-Phase 13 改寫)。模型用戶端是 src/rtb/ 底下每一支 model 開頭的檔,評估套件只准經門面
+Phase 13 改寫),以及 Phase 15 規則模式探索的四支純離線模組(不碰模型用戶端)。
+模型用戶端是 src/rtb/ 底下每一支 model 開頭的檔,評估套件只准經門面
 `rtb.modelclient` 碰它,不直接匯入內部模組;經門面的只有模型候選、評估紀錄命令列、調查評估執行器與
 調查評估報告四支(Phase 13 代碼審 r1)。直接送出呼叫只准模型候選用;經 AI 決策模組開閘道送出的只准
 調查評估執行器(它呼叫正式路徑同一支 AI 決策函式,Phase 13 [S1146]),匯入它也算送出點。
 """
diff --git a/src/rtb/eval/rule_mining_baseline.py b/src/rtb/eval/rule_mining_baseline.py
new file mode 100644
index 0000000..b137795
--- /dev/null
+++ b/src/rtb/eval/rule_mining_baseline.py
@@ -0,0 +1,357 @@
+"""規則模式探索的效果計算、配對、彙總、保留側判定與窮舉前 K(Phase 15 增量 1,計劃
+[[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉〈封閉條件語彙、彙總與窮舉基準〉)。
+
+- 每個加額事件以操作的 UTC 日為 D,只取 D-3..D-1 與 D+1..D+3 六個完整日(D 本身不算)。視窗超出
+  觀察期、視窗內另有調整、資料異常、缺值(含 no_data)、轉換率分母為零都不推斷,逐原因計數。
+- 轉換率 = 轉換 / 點擊,一律經 `rtb.domain.metrics.exact_ratio` 取精確分數;組的變化是後三日率減
+  前三日率的**絕對差**,配對差值是加額組變化減對照組變化。判斷與核對不讀任何捨入字串。
+- 對照只取同一側、**整個觀察期從未加額**的廣告;配對須同側、同 UTC 日 D、同前三日轉換率區間、同投放
+  規模桶。每條條件先按 `(D, 加額廣告編號位元組, committed_at, 操作識別碼)` 處理事件,對照按編號位元組
+  升序取第一支未用者,每支對照在該條件至多用一次;找不到記「無對照」。
+- 差值為零是平手:不算支持也不算反例。有方向配對數 = 正差 + 負差;支持比例、平均差值分母、樣本下限、
+  兩側相異廣告數與相異日期數都只看有方向配對。
+- 窮舉基準:兩方向合併,依 Wilson 95% 下界(直接呼叫 `rtb.eval.scoring.wilson_lower`)、方向化平均
+  差值、有方向配對數、條件鍵、方向代碼排序;同條件兩方向只留較高者,取前 K。
+- 純函式、標準函式庫;不匯入 DSP、模型閘道或模型用戶端。這是可重算的合成比較,不是因果證明。
+"""
+
+from collections import Counter
+from collections.abc import Collection, Iterable, Mapping, Sequence
+from dataclasses import dataclass
+from datetime import UTC, date, datetime, timedelta
+from fractions import Fraction
+
+from rtb.domain import metrics as m
+from rtb.eval import rule_mining_vocab as v
+from rtb.eval.rule_mining_history import Ad, Adjustment, DayBucket, History
+from rtb.eval.scoring import wilson_lower
+
+INCOMPLETE_WINDOW = "incomplete_window"
+OVERLAPPING_ADJUSTMENT = "overlapping_adjustment"
+ANOMALOUS_DATA = "anomalous_data"
+MISSING_VALUE = "missing_value"
+ZERO_DENOMINATOR = "zero_denominator"
+NO_CONTROL = "no_control"
+# 保留側判定的原因
+INSUFFICIENT_SAMPLE = "insufficient_sample"
+LOW_SUPPORT = "low_support"
+WRONG_SIGN = "wrong_sign"
+UNMEASURED = "unmeasured"
+
+_REASON_FROM_METRIC = {m.Reason.NO_DENOMINATOR: ZERO_DENOMINATOR,
+                       m.Reason.MISSING_DATA: MISSING_VALUE,
+                       m.Reason.INVALID_DATA: ANOMALOUS_DATA}
+
+
+@dataclass(frozen=True)
+class _Window:
+    pre_rate: Fraction
+    change: Fraction  # 後三日率 - 前三日率(絕對差)
+    pre_spend: int  # 前三日花費(分)
+
+
+@dataclass(frozen=True)
+class RaiseEvent:
+    ad_id: str
+    op_id: str
+    committed_at: datetime
+    day: date  # D(UTC)
+    clauses: frozenset[v.Clause]  # 這筆事件在四個欄位的值
+    pre_band: str
+    scale: str
+    change: Fraction
+
+    def matches(self, key: v.ConditionKey) -> bool:
+        return all(clause in self.clauses for clause in key)
+
+    @property
+    def order(self) -> tuple[date, bytes, datetime, str]:
+        return self.day, self.ad_id.encode(), self.committed_at, self.op_id
+
+
+@dataclass(frozen=True)
+class Control:
+    ad_id: str
+    change: Fraction
+
+
+@dataclass(frozen=True)
+class Pair:
+    op_id: str
+    raised_ad: str
+    control_ad: str
+    day: date
+    diff: Fraction  # 加額組變化 - 對照組變化
+
+
+@dataclass(frozen=True)
+class ConditionStats:
+    key: v.ConditionKey
+    events: int  # 符合條件的可推斷加額事件
+    pairs: int  # 總配對 = 正差 + 負差 + 平手
+    positive: int
+    negative: int
+    ties: int
+    raised_ads: int  # 有方向配對裡的相異加額廣告
+    control_ads: int  # 有方向配對裡的相異對照廣告
+    dates: int  # 有方向配對裡的相異 UTC 日
+    diff_sum: Fraction  # 有方向差值合計(精確)
+    no_control: int
+
+    @property
+    def directed(self) -> int:
+        return self.positive + self.negative
+
+
+@dataclass(frozen=True)
+class SideSummary:
+    events: int  # 可推斷的加額事件
+    exclusions: Mapping[str, int]  # 事件層排除原因(無對照另在每條件的 no_control)
+    stats: Mapping[v.ConditionKey, ConditionStats]
+
+
+@dataclass(frozen=True)
+class Holdout:
+    kept: bool
+    reasons: tuple[str, ...]
+
+
+@dataclass(frozen=True)
+class Ranked:
+    key: v.ConditionKey
+    direction: str
+    support: int
+    counter: int
+    wilson: float
+    directional_mean: Fraction
+    directed: int
+
+    @property
+    def sort_key(self) -> tuple[float, Fraction, int, v.ConditionKey, str]:
+        return -self.wilson, -self.directional_mean, -self.directed, self.key, self.direction
+
+
+@dataclass(frozen=True)
+class Reach:
+    """分母可達性:只看有方向配對數與兩側相異廣告數,不看正負差。"""
+
+    directed: int
+    raised_ads: int
+    control_ads: int
+
+    @property
+    def meets(self) -> bool:
+        return (self.directed >= v.MIN_DIRECTED and self.raised_ads >= v.MIN_DISTINCT_ADS
+                and self.control_ads >= v.MIN_DISTINCT_ADS)
+
+
+# ---- 視窗與事件 ----
+def _is_raise(adjustment: Adjustment) -> bool:
+    return adjustment.budget_after_cents > adjustment.budget_before_cents
+
+
+def _utc_day(moment: datetime) -> date:
+    return moment.astimezone(UTC).date()
+
+
+def _complete(history: History, day: date) -> bool:
+    last = history.start + timedelta(days=history.day_count - 1)
+    reach = timedelta(days=v.WINDOW_DAYS)
+    return history.start <= day - reach and day + reach <= last
+
+
+def _bucket_problem(buckets: Sequence[DayBucket | None]) -> str | None:
+    """資料異常優先於缺值(同領域層指標的順序)。"""
+    rows = [(b.impressions, b.clicks, b.conversions, b.spend_cents, b.revenue_cents)
+            for b in buckets if b is not None and not b.no_data]
+    present = [x for row in rows for x in row if x is not None]
+    if any(type(x) is not int or x < 0 for x in present) or any(
+            imp is not None and clk is not None and clk > imp for imp, clk, *_ in rows):
+        return ANOMALOUS_DATA
+    if len(rows) < len(buckets) or len(present) < 5 * len(rows):  # 每桶五個欄位
+        return MISSING_VALUE
+    return None
+
+
+def _window(days: Mapping[date, DayBucket], day: date) -> _Window | str:
+    offsets = range(1, v.WINDOW_DAYS + 1)
+    pre = [days.get(day - timedelta(days=i)) for i in reversed(offsets)]
+    post = [days.get(day + timedelta(days=i)) for i in offsets]
+    problem = _bucket_problem(pre + post)
+    if problem is not None:
+        return problem
+    before = [b for b in pre if b is not None]
+    after = [b for b in post if b is not None]
+    pre_rate = m.exact_ratio(sum(b.conversions or 0 for b in before),
+                             sum(b.clicks or 0 for b in before))
+    post_rate = m.exact_ratio(sum(b.conversions or 0 for b in after),
+                              sum(b.clicks or 0 for b in after))
+    for rate in (pre_rate, post_rate):
+        if isinstance(rate, m.Reason):
+            return _REASON_FROM_METRIC[rate]
+    assert isinstance(pre_rate, Fraction) and isinstance(post_rate, Fraction)  # noqa: S101
+    return _Window(pre_rate, post_rate - pre_rate, sum(b.spend_cents or 0 for b in before))
+
+
+def _event(history: History, ad: Ad, adjustment: Adjustment,
+           days: Mapping[date, DayBucket]) -> RaiseEvent | str:
+    day = _utc_day(adjustment.committed_at)
+    if not _complete(history, day):
+        return INCOMPLETE_WINDOW
+    if any(other is not adjustment and abs((_utc_day(other.committed_at) - day).days)
+           <= v.WINDOW_DAYS for other in ad.adjustments):
+        return OVERLAPPING_ADJUSTMENT
+    window = _window(days, day)
+    if isinstance(window, str):
+        return window
+    before = adjustment.budget_before_cents
+    raise_pct = m.exact_ratio(adjustment.budget_after_cents - before, before)
+    spend_ratio = m.exact_ratio(window.pre_spend, v.WINDOW_DAYS * before)
+    if not (isinstance(raise_pct, Fraction) and isinstance(spend_ratio, Fraction)):
+        return ANOMALOUS_DATA
+    pre_band = v.band(v.PRE_CVR, window.pre_rate)
+    clauses = frozenset({(v.DAY_TYPE, v.day_type(day)), (v.PRE_CVR, pre_band),
+                         (v.RAISE_PCT, v.band(v.RAISE_PCT, raise_pct)),
+                         (v.SPEND_RATIO, v.band(v.SPEND_RATIO, spend_ratio))})
+    return RaiseEvent(ad.ad_id, adjustment.op_id, adjustment.committed_at, day, clauses,
+                      pre_band, v.scale_bucket(window.pre_spend), window.change)
+
+
+def _side_ads(history: History, side: Collection[str]) -> list[Ad]:
+    return sorted((ad for ad in history.ads if ad.ad_id in side), key=lambda a: a.ad_id.encode())
+
+
+def side_events(history: History, side: Collection[str]
+                ) -> tuple[tuple[RaiseEvent, ...], dict[str, int]]:
+    """一側的可推斷加額事件(按固定次序)與逐原因排除計數。"""
+    events, excluded = [], Counter[str]()
+    for ad in _side_ads(history, side):
+        days = {bucket.day: bucket for bucket in ad.days}
+        for adjustment in filter(_is_raise, ad.adjustments):
+            result = _event(history, ad, adjustment, days)
+            if isinstance(result, str):
+                excluded[result] += 1
+            else:
+                events.append(result)
+    return tuple(sorted(events, key=lambda e: e.order)), dict(sorted(excluded.items()))
+
+
+ControlIndex = Mapping[tuple[date, str, str], tuple[Control, ...]]
+
+
+def control_index(history: History, side: Collection[str],
+                  days: Iterable[date] | None = None) -> ControlIndex:
+    """同側、整期從未加額的對照:(D, 前三日轉換率區間, 規模桶) → 按編號位元組升序的對照。"""
+    wanted = sorted(set(days) if days is not None else {
+        history.start + timedelta(days=i) for i in range(history.day_count)})
+    index: dict[tuple[date, str, str], list[Control]] = {}
+    for ad in _side_ads(history, side):
+        if any(_is_raise(a) for a in ad.adjustments):
+            continue
+        by_day = {bucket.day: bucket for bucket in ad.days}
+        for day in wanted:
+            window = _window(by_day, day) if _complete(history, day) else INCOMPLETE_WINDOW
+            if isinstance(window, str):
+                continue
+            slot = (day, v.band(v.PRE_CVR, window.pre_rate), v.scale_bucket(window.pre_spend))
+            index.setdefault(slot, []).append(Control(ad.ad_id, window.change))
+    return {slot: tuple(controls) for slot, controls in index.items()}
+
+
+def pair_condition(key: v.ConditionKey, events: Iterable[RaiseEvent], index: ControlIndex
+                   ) -> tuple[tuple[Pair, ...], int]:
+    """一條條件的不放回配對:(配對, 找不到對照的事件數)。"""
+    used: set[str] = set()
+    pairs, missing = [], 0
+    for event in sorted((e for e in events if e.matches(key)), key=lambda e: e.order):
+        control = next((c for c in index.get((event.day, event.pre_band, event.scale), ())
+                        if c.ad_id not in used), None)
+        if control is None:
+            missing += 1
+            continue
+        used.add(control.ad_id)
+        pairs.append(Pair(event.op_id, event.ad_id, control.ad_id, event.day,
+                          event.change - control.change))
+    return tuple(pairs), missing
+
+
+def stats_of(key: v.ConditionKey, events: int, pairs: Sequence[Pair], no_control: int
+             ) -> ConditionStats:
+    directed = [p for p in pairs if p.diff != 0]
+    positive = sum(1 for p in directed if p.diff > 0)
+    return ConditionStats(
+        key=key, events=events, pairs=len(pairs), positive=positive,
+        negative=len(directed) - positive, ties=len(pairs) - len(directed),
+        raised_ads=len({p.raised_ad for p in directed}),
+        control_ads=len({p.control_ad for p in directed}),
+        dates=len({p.day for p in directed}), diff_sum=sum((p.diff for p in directed),
+                                                           Fraction(0)),
+        no_control=no_control)
+
+
+def summarize(history: History, side: Collection[str]) -> SideSummary:
+    """一側逐條件(封閉全集)的彙總。"""
+    events, excluded = side_events(history, side)
+    index = control_index(history, side, {e.day for e in events})
+    stats = {}
+    for key in v.all_conditions():
+        matching = [e for e in events if e.matches(key)]
+        pairs, missing = pair_condition(key, matching, index)
+        stats[key] = stats_of(key, len(matching), pairs, missing)
+    return SideSummary(events=len(events), exclusions=excluded, stats=stats)
+
+
+# ---- 下限、方向、保留側 ----
+def reach_of(stats: ConditionStats) -> Reach:
+    return Reach(stats.directed, stats.raised_ads, stats.control_ads)
+
+
+def meets_floor(stats: ConditionStats) -> bool:
+    return reach_of(stats).meets
+
+
+def directional(stats: ConditionStats, direction: str) -> tuple[int, int, Fraction | None]:
+    """(支持, 反例, 方向化精確平均差值);改善的支持是正差,未改善相反。
+
+    沒有有方向配對時平均是 None。"""
+    if direction == v.IMPROVE:
+        support, counter, sign = stats.positive, stats.negative, 1
+    elif direction == v.NOT_IMPROVE:
+        support, counter, sign = stats.negative, stats.positive, -1
+    else:
+        raise v.VocabularyError("unknown_direction")
+    mean = sign * stats.diff_sum / stats.directed if stats.directed else None
+    return support, counter, mean
+
+
+def holdout_verdict(stats: ConditionStats, direction: str) -> Holdout:
+    """保留側:有方向配對與兩側相異廣告達下限、支持比例 ≥ 3/5、方向化平均差值 > 0 才保留。"""
+    support, _, mean = directional(stats, direction)
+    reasons = [] if meets_floor(stats) else [INSUFFICIENT_SAMPLE]
+    if mean is None:
+        reasons.append(UNMEASURED)
+    else:
+        if Fraction(support, stats.directed) < v.HOLDOUT_SUPPORT:
+            reasons.append(LOW_SUPPORT)
+        if mean <= 0:
+            reasons.append(WRONG_SIGN)
+    return Holdout(kept=not reasons, reasons=tuple(reasons))
+
+
+# ---- 窮舉前 K ----
+def _ranked(stats: ConditionStats, direction: str) -> Ranked:
+    support, counter, mean = directional(stats, direction)
+    assert mean is not None  # noqa: S101 - 只對達下限(有方向配對 > 0)的條件呼叫
+    return Ranked(stats.key, direction, support, counter,
+                  wilson_lower(support, stats.directed), mean, stats.directed)
+
+
+def rank(stats: Mapping[v.ConditionKey, ConditionStats]) -> tuple[Ranked, ...]:
+    """達下限的條件各留排序較高的一個方向,再全域排序。"""
+    best = [min((_ranked(s, d) for d in v.DIRECTIONS), key=lambda r: r.sort_key)
+            for s in stats.values() if meets_floor(s)]
+    return tuple(sorted(best, key=lambda r: r.sort_key))
+
+
+def top_k(stats: Mapping[v.ConditionKey, ConditionStats], k: int = v.K) -> tuple[Ranked, ...]:
+    return rank(stats)[:k]
diff --git a/src/rtb/eval/rule_mining_history.py b/src/rtb/eval/rule_mining_history.py
new file mode 100644
index 0000000..f1c87a4
--- /dev/null
+++ b/src/rtb/eval/rule_mining_history.py
@@ -0,0 +1,276 @@
+"""規則模式探索的合成歷史:資料型別、固定種子生成器、評估集文字與雜湊、探索/保留切分、埋入真相清單
+(Phase 15 增量 1,計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉)。
+
+- 只收三個固定種子(`rule_mining_vocab.SEEDS`);720 支虛構廣告、35 個連續 UTC 日,每支每天一個
+  完整日桶(曝光、點擊、轉換、花費、營收,金額整數分),加額廣告另有含調整前後日預算與 `committed_at`
+  的操作。計數照漏斗造(轉換 ≤ 點擊 ≤ 曝光)、當天花費不超過當天日預算;少數廣告有一天 `no_data`。
+- 真相清單 `TRUTH` 只給評估器:生成器照它在加額後三天改轉換率,條件的欄位值用評估器同一支語彙函式
+  從已生成的前三日資料算(真模式與切點精確對齊,所以召回只是可精確表示時的上界)。誘餌兩類:只在
+  探索側埋入的偶合、與真模式相關但本身沒有效果。真相不進評估集文字,也不能進模型提示。
+- 另刻意放少量會被排除的事件(前三日或後三日超出觀察期、視窗內另有調整、視窗碰到 no_data),讓排除
+  計數有東西可數。
+- `render` 產出固定格式文字,`data_sha256` 是它的雜湊;三批的預期雜湊寫死在 `EXPECTED_DATA_SHA256`,
+  改生成器就得連版本、雜湊一起換並記理由。合成資料只證流程可運作,不給統計或因果保證。
+"""
+
+import hashlib
+import math
+import random
+from collections.abc import Iterable, Mapping, Sequence
+from dataclasses import dataclass
+from datetime import UTC, date, datetime, timedelta
+from fractions import Fraction
+from types import MappingProxyType
+
+from rtb.eval import rule_mining_vocab as v
+
+START = date(2026, 8, 3)  # 週一
+DAYS = 35
+CLOCK = datetime(2026, 9, 7, tzinfo=UTC)  # 評估時鐘:最後一個 UTC 日結束
+N_ADS = 720
+RAISED_ADS = 360
+
+TRUE_PATTERN = "true_pattern"
+DECOY_EXPLORE_ONLY = "decoy_explore_only"
+DECOY_CORRELATED = "decoy_correlated"
+LIFT, DROP = 1.6, 0.5  # 真模式在加額後三天對轉換率的乘數
+
+# 首次生成後寫死;生成器任何改動都會讓它對不上,須換生成版本與評估版本並記理由
+EXPECTED_DATA_SHA256: Mapping[int, str] = MappingProxyType({
+    15001: "f820e1a486d8b35d1d81b55c37ce6ed5bb96a9701b0d898020f559fd1adb9f98",
+    15002: "2a9e4aac0f4ec0209bfa6e4f7124e5cefb8bcdb259d89886d0a3e856a5c0a9e3",
+    15003: "ba7d6ebc8a3a8b4d9c79334a1e2ab5844842eca74ab2c7fdecec90a529c44a2f",
+})
+
+
+@dataclass(frozen=True)
+class DayBucket:
+    day: date  # UTC 日
+    impressions: int | None
+    clicks: int | None
+    conversions: int | None
+    spend_cents: int | None
+    revenue_cents: int | None
+    no_data: bool = False
+
+
+@dataclass(frozen=True)
+class Adjustment:
+    op_id: str
+    committed_at: datetime  # UTC
+    budget_before_cents: int  # 日預算
+    budget_after_cents: int
+
+
+@dataclass(frozen=True)
+class Ad:
+    ad_id: str
+    daily_budget_cents: int  # 觀察期第一天的日預算
+    days: tuple[DayBucket, ...]
+    adjustments: tuple[Adjustment, ...]
+
+
+@dataclass(frozen=True)
+class History:
+    seed: int
+    generator_version: str
+    start: date
+    day_count: int
+    ads: tuple[Ad, ...]
+
+
+@dataclass(frozen=True)
+class TruthItem:
+    kind: str
+    clauses: v.ConditionKey
+    direction: str
+    note: str
+
+    @property
+    def key(self) -> v.NormalizedKey:
+        return v.normalized_key(self.clauses, self.direction)
+
+
+TRUTH = (
+    TruthItem(TRUE_PATTERN, ((v.RAISE_PCT, "raise_pct:band_2"), (v.SPEND_RATIO,
+                                                                "spend_ratio:band_3")),
+              v.IMPROVE, "預算幾乎花滿的廣告中度加額,後三天轉換率變好"),
+    TruthItem(TRUE_PATTERN, ((v.RAISE_PCT, "raise_pct:band_3"),), v.NOT_IMPROVE,
+              "大幅加額買到較差的流量,後三天轉換率變差"),
+    TruthItem(DECOY_EXPLORE_ONLY, ((v.DAY_TYPE, v.WEEKEND), (v.RAISE_PCT, "raise_pct:band_1")),
+              v.IMPROVE, "只在探索側埋入的偶合:保留側沒有這個效果"),
+    TruthItem(DECOY_CORRELATED, ((v.SPEND_RATIO, "spend_ratio:band_1"),), v.NOT_IMPROVE,
+              "花費比低的廣告多半被大幅加額而看似變差;本身沒有獨立效果"),
+)
+
+
+@dataclass(frozen=True)
+class Split:
+    explore: frozenset[str]
+    holdout: frozenset[str]
+
+
+def split(ad_ids: Iterable[str]) -> Split:
+    """按廣告編號 UTF-8 位元組的 SHA-256 升序(同雜湊再比編號位元組),前 floor(N/2) 支探索、其餘
+    保留(奇數多出者進保留)。同一廣告的全部日期與對照資格只在一側。"""
+    order = sorted(set(ad_ids), key=lambda a: (hashlib.sha256(a.encode()).digest(), a.encode()))
+    half = len(order) // 2
+    return Split(explore=frozenset(order[:half]), holdout=frozenset(order[half:]))
+
+
+# ---- 生成器 ----
+_CVR = (0.012, 0.033, 0.075)  # 轉換率等級:落在 pre_cvr 三個區間的中段
+_UTIL = (0.45, 0.75, 0.96)  # 日預算花用比等級:落在 spend_ratio 三個區間的中段
+_BUDGETS = ((1500, 2500), (8000, 16000), (40000, 80000))  # 日預算(分):三個規模桶
+# 加額幅度(基點)各區間的候選值,離切點夠遠
+_RAISE_BP = ((1000, 1200, 1500, 1800), (2500, 3000, 3500, 4000, 4500), (6000, 7500, 9000, 10000))
+# 花用比等級 → 加額幅度區間的機率(低花用比多半被大幅加額:相關誘餌的來源)
+_RAISE_BAND_WEIGHTS = ((15, 15, 70), (55, 30, 15), (40, 50, 10))
+_EARLY, _LATE, _OVERLAP, _GAP = 0.04, 0.04, 0.06, 0.15  # 刻意放的排除事件與 no_data 比例
+_LAST_FULL = DAYS - 1 - v.WINDOW_DAYS  # 後三日仍在觀察期內的最後一個加額日
+
+
+@dataclass(frozen=True)
+class _Profile:
+    cvr: float
+    util_tier: int
+    util: float
+    budget: int
+    cpc: float
+    ctr: float
+    aov: int
+
+
+def _profile(rng: random.Random) -> _Profile:
+    cvr_tier, scale, util_tier = rng.randrange(3), rng.randrange(3), rng.randrange(3)
+    low, high = _BUDGETS[scale]
+    return _Profile(cvr=_CVR[cvr_tier], util_tier=util_tier, util=_UTIL[util_tier],
+                    budget=rng.randint(low, high), cpc=rng.uniform(15, 40),
+                    ctr=rng.uniform(0.01, 0.04), aov=rng.randint(2000, 8000))
+
+
+def _raise_days(rng: random.Random) -> list[int]:
+    first = rng.randint(0, 2) if rng.random() < _EARLY else rng.randint(3, 13)
+    second = rng.randint(_LAST_FULL + 1, DAYS - 1) if rng.random() < _LATE else rng.randint(
+        max(first, 3) + 8, _LAST_FULL)
+    days = [first, second]
+    if rng.random() < _OVERLAP and second + 2 < DAYS:
+        days.append(second + 2)
+    return days
+
+
+def _raise_bp(rng: random.Random, util_tier: int) -> int:
+    band_index = rng.choices((0, 1, 2), weights=_RAISE_BAND_WEIGHTS[util_tier])[0]
+    return rng.choice(_RAISE_BP[band_index])
+
+
+def _market(index: int, day: date) -> float:
+    """全體廣告共用的轉換率起伏(對照組扣掉的就是它)。"""
+    weekend = 0.05 if v.day_type(day) == v.WEEKEND else 0.0
+    return 1 + 0.08 * math.sin(2 * math.pi * index / 14) + weekend
+
+
+def _bucket(rng: random.Random, profile: _Profile, day: date, budget: int, factor: float,
+            gap: bool) -> DayBucket:
+    if gap:
+        return DayBucket(day, None, None, None, None, None, no_data=True)
+    spend = int(budget * min(1.0, profile.util * rng.uniform(0.97, 1.03)))
+    clicks = max(1, round(spend / profile.cpc * rng.uniform(0.9, 1.1)))
+    impressions = max(clicks, round(clicks / profile.ctr * rng.uniform(0.95, 1.05)))
+    rate = min(0.5, profile.cvr * factor)
+    mean = clicks * rate
+    conversions = min(clicks, max(0, round(rng.gauss(mean, math.sqrt(mean * (1 - rate))))))
+    revenue = round(conversions * profile.aov * rng.uniform(0.9, 1.1))
+    return DayBucket(day, impressions, clicks, conversions, spend, revenue)
+
+
+def _event_clauses(pre: Sequence[DayBucket], day: date, before: int, after: int
+                   ) -> frozenset[v.Clause] | None:
+    """加額事件四個欄位的值,跟評估器同一套語彙函式;前三日缺資料或沒點擊就不埋效果(事件會被排除)。"""
+    if len(pre) < v.WINDOW_DAYS or any(b.no_data for b in pre):
+        return None
+    clicks = sum(b.clicks or 0 for b in pre)
+    if clicks == 0:
+        return None
+    conversions = sum(b.conversions or 0 for b in pre)
+    spend = sum(b.spend_cents or 0 for b in pre)
+    return frozenset({
+        (v.DAY_TYPE, v.day_type(day)),
+        (v.PRE_CVR, v.band(v.PRE_CVR, Fraction(conversions, clicks))),
+        (v.RAISE_PCT, v.band(v.RAISE_PCT, Fraction(after - before, before))),
+        (v.SPEND_RATIO, v.band(v.SPEND_RATIO, Fraction(spend, v.WINDOW_DAYS * before))),
+    })
+
+
+def _effect(clauses: frozenset[v.Clause] | None, explore: bool) -> float:
+    factor = 1.0
+    if clauses is None:
+        return factor
+    for item in TRUTH:
+        if not set(item.clauses) <= clauses:
+            continue
+        if item.kind == TRUE_PATTERN:
+            factor *= LIFT if item.direction == v.IMPROVE else DROP
+        elif item.kind == DECOY_EXPLORE_ONLY and explore:
+            factor *= LIFT
+    return factor
+
+
+def _make_ad(rng: random.Random, ad_id: str, raised: bool, explore: bool) -> Ad:
+    profile = _profile(rng)
+    plan = {day: _raise_bp(rng, profile.util_tier) for day in _raise_days(rng)} if raised else {}
+    gaps = {rng.randrange(DAYS)} if rng.random() < _GAP else set()
+    budget, buckets, adjustments = profile.budget, list[DayBucket](), list[Adjustment]()
+    lift: dict[int, float] = {}
+    for index in range(DAYS):
+        day = START + timedelta(days=index)
+        if index in plan:
+            after = budget + (budget * plan[index] + 5000) // 10000
+            moment = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
+                minutes=rng.randrange(60, 23 * 60))
+            adjustments.append(Adjustment(f"{ad_id}-op{len(adjustments) + 1}", moment, budget,
+                                          after))
+            factor = _effect(_event_clauses(buckets[-v.WINDOW_DAYS:], day, budget, after), explore)
+            for later in range(index + 1, index + 1 + v.WINDOW_DAYS):
+                lift[later] = lift.get(later, 1.0) * factor
+            budget = after
+        buckets.append(_bucket(rng, profile, day, budget,
+                               _market(index, day) * lift.get(index, 1.0), index in gaps))
+    return Ad(ad_id, profile.budget, tuple(buckets), tuple(adjustments))
+
+
+def generate(seed: int) -> History:
+    """固定種子的合成歷史;只收 `SEEDS` 裡的三個種子。"""
+    if seed not in v.SEEDS:
+        raise ValueError(f"只收固定種子 {v.SEEDS},任一批不得替換:{seed}")
+    rng = random.Random(seed)  # noqa: S311 - 合成歷史用固定種子,不是密碼學用途
+    ids = tuple(f"rm{seed}-{i:04d}" for i in range(N_ADS))
+    explore = split(ids).explore
+    raised = frozenset(rng.sample(ids, RAISED_ADS))
+    ads = tuple(_make_ad(rng, ad_id, ad_id in raised, ad_id in explore) for ad_id in ids)
+    return History(seed, v.GENERATOR_VERSION, START, DAYS, ads)
+
+
+# ---- 評估集文字與雜湊 ----
+def _bucket_line(ad_id: str, bucket: DayBucket) -> str:
+    if bucket.no_data:
+        return f"N|{ad_id}|{bucket.day.isoformat()}"
+    values = (bucket.impressions, bucket.clicks, bucket.conversions, bucket.spend_cents,
+              bucket.revenue_cents)
+    return "|".join(["D", ad_id, bucket.day.isoformat(), *(str(x) for x in values)])
+
+
+def render(history: History) -> str:
+    """評估集的固定文字:標頭、每支廣告一行(A)、每天一行(D;no_data 寫 N)、每筆操作一行(O)。"""
+    lines = [f"rule-mining-history|{history.generator_version}|seed={history.seed}|"
+             f"start={history.start.isoformat()}|days={history.day_count}|ads={len(history.ads)}"]
+    for ad in history.ads:
+        lines.append(f"A|{ad.ad_id}|{ad.daily_budget_cents}")
+        lines += [_bucket_line(ad.ad_id, bucket) for bucket in ad.days]
+        lines += [f"O|{ad.ad_id}|{a.op_id}|{a.committed_at.isoformat()}|{a.budget_before_cents}|"
+                  f"{a.budget_after_cents}" for a in ad.adjustments]
+    return "\n".join(lines) + "\n"
+
+
+def data_sha256(history: History) -> str:
+    return hashlib.sha256(render(history).encode()).hexdigest()
diff --git a/src/rtb/eval/rule_mining_prompt.py b/src/rtb/eval/rule_mining_prompt.py
new file mode 100644
index 0000000..de788bb
--- /dev/null
+++ b/src/rtb/eval/rule_mining_prompt.py
@@ -0,0 +1,239 @@
+"""規則模式探索的固定系統提示、探索集彙總表、位元組閘與可達性預檢(Phase 15 增量 1,計劃
+[[Projects/RTB_Phase15AI找規則模式_計劃]]〈封閉條件語彙、彙總與窮舉基準〉〈拆增量〉第 1 項)。
+
+- 這一增量**不呼叫模型**:只把系統提示文字定稿、建出要送的完整彙總表,量「系統提示 + 彙總表」的 UTF-8
+  位元組數。完整表只列探索側達樣本下限的條件,欄序、捨入固定;比例沿用 `metrics.percent_text`(一位
+  小數),平均差值以百分點、精確分數 half-even 捨入四位小數;精確分數、保留側、逐日列、識別、時間戳與
+  真相標籤都不進表。一批只准一次完整提示,不分塊、不截列:`B > 20480` 或達既有 48 KiB 就拒跑。
+- 可達性預檢只在建立或更換評估版本時跑:固定三種子逐批記資料雜湊、兩側加額事件與排除原因、每個真模式
+  與誘餌在探索/保留兩側的分母(有方向配對數與兩側相異廣告數,不看正負差)、完整提示位元組數;任一批
+  超限或任一真相分母不可達就整體失敗,不得換種子或截列通過。結果寫死在 `PREFLIGHT_RECORD`(評估版本
+  理由的機器可核對那份),測試重算比對。
+- 評估版本雜湊涵蓋語彙模組的參數清單、系統提示、表頭、真相清單與三批資料雜湊;任何一項改動都要換
+  `EVAL_VERSION` 並記理由與對 AI、窮舉雙方的預期影響。
+"""
+
+import hashlib
+import json
+from collections.abc import Mapping, Sequence
+from dataclasses import dataclass
+from fractions import Fraction
+
+from rtb.domain import metrics as m
+from rtb.eval import rule_mining_baseline as b
+from rtb.eval import rule_mining_history as h
+from rtb.eval import rule_mining_vocab as v
+
+SYSTEM_PROMPT = (
+    "你是離線規則探勘助手。使用者內容是一份固定種子合成歷史的探索集彙總表。每列是一條封閉條件"
+    "(一或兩個不同欄位的門檻代碼,以 & 相連),數字來自加額事件與對照廣告的配對:對照是同一側、"
+    "同一 UTC 日、同一調整前三日轉換率區間、同一投放規模桶、整個觀察期從未加額的廣告。配對差值是"
+    "「加額組後三日轉換率減前三日轉換率」減去「對照組同樣的變化」,單位是百分點。\n"
+    "任務:從表中挑出至多 10 條你認為在未見資料上最可能也成立的條件與方向,"
+    "只能用表中出現的門檻代碼。\n"
+    "方向:improve 表示加額後相對對照較可能改善,支持是正差筆數、反例是負差筆數;not_improve 相反,"
+    "支持是負差筆數、反例是正差筆數。平手既不是支持也不是反例。\n"
+    "輸出:只輸出一行緊湊的 UTF-8 JSON,直接寫中文原字,不要用 \\uXXXX 逸出,不要 Markdown,總長不超過"
+    " 6000 位元組。格式:"
+    '{"version":1,"suggestions":[{"clauses":[{"condition":"raise_pct",'
+    '"threshold":"raise_pct:band_2"}],"direction":"improve","support":24,"counterexample":6,'
+    '"confidence_note":"一句話"}]}\n'
+    "規則:suggestions 至多 10 條;clauses 是一或兩個不同的 condition,condition 是門檻代碼冒號前的"
+    "欄位代碼;同一條件不得同時押兩個方向,也不得重複;support 與 counterexample 照抄表中該方向的整數;"
+    "confidence_note 至多 80 字,不含引號、反斜線或控制字元;不要加任何其他鍵。\n"
+    "門檻代碼:\n"
+    "day_type:weekday 加額 UTC 日是平日;day_type:weekend 是週六或週日\n"
+    "raise_pct:band_1 加額幅度未滿 20%;raise_pct:band_2 20% 以上未滿 50%;"
+    "raise_pct:band_3 50% 以上\n"
+    "pre_cvr:band_1 調整前三日轉換率未滿 2%;pre_cvr:band_2 2% 以上未滿 5%;"
+    "pre_cvr:band_3 5% 以上\n"
+    "spend_ratio:band_1 調整前三日花費除以三天原日預算未滿 60%;spend_ratio:band_2 60% 以上未滿"
+    " 90%;spend_ratio:band_3 90% 以上\n"
+)
+TABLE_HEADER = (
+    f"彙總表 {v.EVAL_VERSION}:探索集;只列有方向配對至少 20 且其中相異加額廣告、相異對照廣告"
+    "各至少 20 的條件;有方向配對 = 正差 + 負差,平手不計;相異廣告與日期只數有方向配對",
+    "欄位:條件|加額事件|總配對|有方向配對|正差|負差|平手|相異加額廣告|相異對照廣告|相異日期|"
+    "正差占有方向比例%|平均差值pp|無對照事件",
+)
+DIFF_PLACES = 10 ** 4
+
+# 首次預檢後寫死(評估版本理由的機器可核對那份);重跑須逐字相同
+PREFLIGHT_RECORD: tuple[str, ...] = (
+    '15001 資料 f820e1a486d8b35d 廣告 探索360/保留360 可推斷事件 探索340/保留301 '
+     '表列 50 B=6045',
+    '15001 排除 探索[incomplete_window=18 missing_value=11 '
+     'overlapping_adjustment=18] 保留[incomplete_window=17 '
+     'missing_value=9 overlapping_adjustment=32]',
+    '15001 true_pattern raise_pct:band_2&spend_ratio:band_3 '
+     'improve 探索[有方向48/加額廣告40/對照廣告48] 保留[有方向57/加額廣告45/對照廣告57]',
+    '15001 true_pattern raise_pct:band_3 not_improve '
+     '探索[有方向113/加額廣告88/對照廣告113] 保留[有方向84/加額廣告66/對照廣告84]',
+    '15001 decoy_explore_only day_type:weekend&raise_pct:band_1 '
+     'improve 探索[有方向45/加額廣告44/對照廣告45] 保留[有方向40/加額廣告38/對照廣告40]',
+    '15001 decoy_correlated spend_ratio:band_1 not_improve '
+     '探索[有方向138/加額廣告81/對照廣告138] 保留[有方向83/加額廣告45/對照廣告83]',
+    '15002 資料 2a9e4aac0f4ec020 廣告 探索360/保留360 可推斷事件 探索318/保留339 '
+     '表列 49 B=5963',
+    '15002 排除 探索[incomplete_window=13 missing_value=12 '
+     'overlapping_adjustment=16] 保留[incomplete_window=18 '
+     'missing_value=7 overlapping_adjustment=13]',
+    '15002 true_pattern raise_pct:band_2&spend_ratio:band_3 '
+     'improve 探索[有方向49/加額廣告37/對照廣告49] 保留[有方向60/加額廣告45/對照廣告60]',
+    '15002 true_pattern raise_pct:band_3 not_improve '
+     '探索[有方向101/加額廣告74/對照廣告101] 保留[有方向116/加額廣告88/對照廣告116]',
+    '15002 decoy_explore_only day_type:weekend&raise_pct:band_1 '
+     'improve 探索[有方向34/加額廣告34/對照廣告34] 保留[有方向39/加額廣告38/對照廣告39]',
+    '15002 decoy_correlated spend_ratio:band_1 not_improve '
+     '探索[有方向109/加額廣告60/對照廣告109] 保留[有方向112/加額廣告67/對照廣告112]',
+    '15003 資料 ba7d6ebc8a3a8b4d 廣告 探索360/保留360 可推斷事件 探索333/保留324 '
+     '表列 50 B=6032',
+    '15003 排除 探索[incomplete_window=14 missing_value=7 '
+     'overlapping_adjustment=21] 保留[incomplete_window=10 '
+     'missing_value=8 overlapping_adjustment=28]',
+    '15003 true_pattern raise_pct:band_2&spend_ratio:band_3 '
+     'improve 探索[有方向62/加額廣告49/對照廣告62] 保留[有方向53/加額廣告39/對照廣告53]',
+    '15003 true_pattern raise_pct:band_3 not_improve '
+     '探索[有方向109/加額廣告80/對照廣告109] 保留[有方向118/加額廣告82/對照廣告118]',
+    '15003 decoy_explore_only day_type:weekend&raise_pct:band_1 '
+     'improve 探索[有方向48/加額廣告46/對照廣告48] 保留[有方向27/加額廣告27/對照廣告27]',
+    '15003 decoy_correlated spend_ratio:band_1 not_improve '
+     '探索[有方向96/加額廣告54/對照廣告96] 保留[有方向117/加額廣告65/對照廣告117]',
+)
+EXPECTED_VERSION_SHA256 = "831dc0dc4736cce90e04ff7cc1d7ad6336d77497f4d0c28495b02cff27c7ba9f"
+
+
+def diff_text(mean: Fraction) -> str:
+    """平均差值:比率刻度 → 百分點,精確分數 half-even 捨入到四位小數;負零寫 0。"""
+    scaled = round(mean * 100 * DIFF_PLACES)  # Fraction 的 round 是四捨五入到偶數
+    sign = "-" if scaled < 0 else ""
+    return f"{sign}{abs(scaled) // DIFF_PLACES}.{abs(scaled) % DIFF_PLACES:04d}"
+
+
+def _row(stats: b.ConditionStats) -> str:
+    counts = (stats.events, stats.pairs, stats.directed, stats.positive, stats.negative,
+              stats.ties, stats.raised_ads, stats.control_ads, stats.dates)
+    share = m.percent_text(m.exact_ratio(stats.positive, stats.directed))
+    return "|".join([v.key_text(stats.key), *(str(c) for c in counts), share,
+                     diff_text(stats.diff_sum / stats.directed), str(stats.no_control)])
+
+
+def summary_table(summary: b.SideSummary) -> str:
+    """送模型的完整彙總表(使用者內容):表頭、事件層排除計數、達下限的每一條條件一列。"""
+    excluded = " ".join(f"{reason}={count}" for reason, count in summary.exclusions.items())
+    lines = [*TABLE_HEADER, f"可推斷加額事件 {summary.events};事件層排除:{excluded or '無'}"]
+    lines += [_row(summary.stats[key]) for key in v.all_conditions()
+              if b.meets_floor(summary.stats[key])]
+    return "\n".join(lines) + "\n"
+
+
+def prompt_bytes(table: str) -> int:
+    """系統提示加使用者內容(彙總表)的 UTF-8 位元組數。"""
+    return len(SYSTEM_PROMPT.encode()) + len(table.encode())
+
+
+def gate_problem(size: int) -> str | None:
+    """單次完整提示的位元組閘;超過就拒跑(不分塊、不截列)。預留額另在模型增量用 repo 函式重算。"""
+    if size > v.PROMPT_BYTES_LIMIT:
+        return f"完整提示 {size} 位元組超過本案上限 {v.PROMPT_BYTES_LIMIT}"
+    if size >= v.GATEWAY_PROMPT_BYTES:
+        return f"完整提示 {size} 位元組達既有上限 {v.GATEWAY_PROMPT_BYTES}"
+    return None
+
+
+def version_sha256() -> str:
+    """評估版本雜湊:參數清單、系統提示、表頭、真相清單與三批預期資料雜湊。"""
+    document = {
+        "params": v.version_params(), "system_prompt": SYSTEM_PROMPT,
+        "table_header": list(TABLE_HEADER),
+        "truth": [[t.kind, [list(c) for c in t.clauses], t.direction] for t in h.TRUTH],
+        "data_sha256": {str(seed): h.EXPECTED_DATA_SHA256[seed] for seed in v.SEEDS},
+    }
+    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
+    return hashlib.sha256(text.encode()).hexdigest()
+
+
+# ---- 可達性預檢 ----
+@dataclass(frozen=True)
+class TruthReach:
+    kind: str
+    key: v.NormalizedKey
+    explore: b.Reach
+    holdout: b.Reach
+
+
+@dataclass(frozen=True)
+class SeedPreflight:
+    seed: int
+    data_sha256: str
+    explore_ads: int
+    holdout_ads: int
+    explore_events: int
+    holdout_events: int
+    explore_exclusions: Mapping[str, int]
+    holdout_exclusions: Mapping[str, int]
+    table_rows: int
+    prompt_bytes: int
+    truths: tuple[TruthReach, ...]
+
+
+def preflight_seed(seed: int) -> SeedPreflight:
+    history = h.generate(seed)
+    sides = h.split(ad.ad_id for ad in history.ads)
+    explore, holdout = b.summarize(history, sides.explore), b.summarize(history, sides.holdout)
+    table = summary_table(explore)
+    truths = tuple(TruthReach(t.kind, t.key, b.reach_of(explore.stats[t.key[0]]),
+                              b.reach_of(holdout.stats[t.key[0]])) for t in h.TRUTH)
+    return SeedPreflight(
+        seed=seed, data_sha256=h.data_sha256(history), explore_ads=len(sides.explore),
+        holdout_ads=len(sides.holdout), explore_events=explore.events,
+        holdout_events=holdout.events, explore_exclusions=explore.exclusions,
+        holdout_exclusions=holdout.exclusions,
+        table_rows=sum(1 for s in explore.stats.values() if b.meets_floor(s)),
+        prompt_bytes=prompt_bytes(table), truths=truths)
+
+
+def preflight() -> tuple[SeedPreflight, ...]:
+    """固定三種子逐批預檢(不收別的種子)。"""
+    return tuple(preflight_seed(seed) for seed in v.SEEDS)
+
+
+def preflight_problems(results: Sequence[SeedPreflight]) -> tuple[str, ...]:
+    """任一批超限、任一真相分母不可達、或不是恰好三個固定種子,就回問題清單(空 = 通過)。"""
+    problems = []
+    if tuple(r.seed for r in results) != v.SEEDS:
+        problems.append(f"預檢須恰好依序列出固定種子 {v.SEEDS}")
+    for result in results:
+        gate = gate_problem(result.prompt_bytes)
+        if gate is not None:
+            problems.append(f"{result.seed}: {gate}")
+        for truth in result.truths:
+            for side, reach in (("探索", truth.explore), ("保留", truth.holdout)):
+                if not reach.meets:
+                    problems.append(
+                        f"{result.seed}: {truth.kind} {v.key_text(truth.key[0])} "
+                        f"{truth.key[1]} {side}側分母不可達({_reach_text(reach)})")
+    return tuple(problems)
+
+
+def _reach_text(reach: b.Reach) -> str:
+    return f"有方向{reach.directed}/加額廣告{reach.raised_ads}/對照廣告{reach.control_ads}"
+
+
+def _counts_text(counts: Mapping[str, int]) -> str:
+    return " ".join(f"{reason}={count}" for reason, count in counts.items()) or "無"
+
+
+def preflight_record(results: Sequence[SeedPreflight]) -> tuple[str, ...]:
+    """預檢結果的固定文字(寫進評估版本理由):每批一行總覽、一行排除、每個真相一行分母。"""
+    lines = []
+    for r in results:
+        lines.append(f"{r.seed} 資料 {r.data_sha256[:16]} 廣告 探索{r.explore_ads}/保留"
+                     f"{r.holdout_ads} 可推斷事件 探索{r.explore_events}/保留{r.holdout_events} "
+                     f"表列 {r.table_rows} B={r.prompt_bytes}")
+        lines.append(f"{r.seed} 排除 探索[{_counts_text(r.explore_exclusions)}] "
+                     f"保留[{_counts_text(r.holdout_exclusions)}]")
+        lines += [f"{r.seed} {t.kind} {v.key_text(t.key[0])} {t.key[1]} "
+                  f"探索[{_reach_text(t.explore)}] 保留[{_reach_text(t.holdout)}]"
+                  for t in r.truths]
+    return tuple(lines)
diff --git a/src/rtb/eval/rule_mining_vocab.py b/src/rtb/eval/rule_mining_vocab.py
new file mode 100644
index 0000000..412597d
--- /dev/null
+++ b/src/rtb/eval/rule_mining_vocab.py
@@ -0,0 +1,171 @@
+"""規則模式探索的封閉條件語彙與評估版本常數(Phase 15 增量 1,計劃
+[[Projects/RTB_Phase15AI找規則模式_計劃]]〈封閉條件語彙、彙總與窮舉基準〉)。
+
+- 這支檔是「評估版本」的單一出處:三個固定種子、生成版本、K、樣本下限、保留比例、切點、切分與配對
+  規則、搶用次序、效果與平均公式、排序鍵、格式化與輸入輸出上限都寫死在這裡,`version_params()` 把它們
+  列成一份可雜湊的清單。看任何基準或模型結果前凍結;要改先換 `EVAL_VERSION` 並記理由與可達性預檢
+  ([S1515] [S1519] [S1520])。
+- 條件只有四個欄位、每欄固定幾個區間;一條條件是一或兩個**不同**欄位的子句 AND。子句鍵是
+  `(條件代碼, 門檻代碼全名)`,門檻代碼一律寫成「欄位:區間」(如 `raise_pct:band_2`),不能拿別欄的門檻
+  套用;正規化鍵是按子句排序後的元組再接方向。九條正式規則的詞彙不擴充成這裡的語彙。
+- 純函式、只用標準函式庫與領域層的精確比率;不匯入模型用戶端、模型閘道或 DSP。
+"""
+
+import re
+from collections.abc import Iterable, Mapping
+from datetime import date
+from fractions import Fraction
+from itertools import combinations
+from types import MappingProxyType
+from typing import Any
+
+EVAL_VERSION = "phase15-rule-mining-v1"
+GENERATOR_VERSION = "rule-mining-history-1"
+SEEDS = (15001, 15002, 15003)  # 固定,任一批不得替換;報告全列
+
+K = 10
+MIN_DIRECTED = 20  # 有方向配對(正差 + 負差)下限
+MIN_DISTINCT_ADS = 20  # 有方向配對裡相異加額廣告、相異對照廣告各自的下限
+HOLDOUT_SUPPORT = Fraction(3, 5)
+WINDOW_DAYS = 3  # D-3..D-1 與 D+1..D+3,D 本身不算
+
+PROMPT_BYTES_LIMIT = 20480  # 本案:系統提示加使用者內容的 UTF-8 位元組
+GATEWAY_PROMPT_BYTES = 48 * 1024  # 既有模型用戶端的上限(測試核對 modelcore.MAX_PROMPT_BYTES)
+MAX_OUTPUT_TOKENS = 6144
+REPLY_BYTES_LIMIT = 6000
+NOTE_CHARS = 80
+NOTE_BYTES = 240
+CALL_TIMEOUT_SECONDS = 60
+
+IMPROVE, NOT_IMPROVE = "improve", "not_improve"
+DIRECTIONS = (IMPROVE, NOT_IMPROVE)
+
+DAY_TYPE, PRE_CVR, RAISE_PCT, SPEND_RATIO = "day_type", "pre_cvr", "raise_pct", "spend_ratio"
+FIELDS = (DAY_TYPE, PRE_CVR, RAISE_PCT, SPEND_RATIO)  # 已按代碼排序
+WEEKDAY, WEEKEND = "day_type:weekday", "day_type:weekend"
+# 數值欄的切點(下界含、上界不含;比率刻度),依序切出 band_1 / band_2 / band_3。百分比以整數基點寫
+CUTS: Mapping[str, tuple[Fraction, Fraction]] = MappingProxyType({
+    PRE_CVR: (Fraction(200, 10000), Fraction(500, 10000)),  # 調整前三日轉換率 <2%、2-5%、≥5%
+    RAISE_PCT: (Fraction(2000, 10000), Fraction(5000, 10000)),  # 加額幅度 <20%、20-50%、≥50%
+    SPEND_RATIO: (Fraction(6000, 10000), Fraction(9000, 10000)),  # 前三日花費/(3 x 原日預算)
+})
+THRESHOLDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
+    DAY_TYPE: (WEEKDAY, WEEKEND),
+    **{name: tuple(f"{name}:band_{i}" for i in (1, 2, 3)) for name in (PRE_CVR, RAISE_PCT,
+                                                                       SPEND_RATIO)},
+})
+# 投放規模桶:調整前三日花費整數分
+SCALE_CUTS_CENTS = (10000, 50000)
+SCALE_BUCKETS = ("lt_10000", "10000_49999", "ge_50000")
+CODE = re.compile(r"[a-z0-9_:]{1,24}")
+
+Clause = tuple[str, str]
+ConditionKey = tuple[Clause, ...]
+NormalizedKey = tuple[ConditionKey, str]
+
+
+class VocabularyError(ValueError):
+    """條件不在封閉語彙內;reason 是固定的原因代碼(模型回覆解析時逐條記)。"""
+
+    def __init__(self, reason: str) -> None:
+        super().__init__(reason)
+        self.reason = reason
+
+
+def band(field: str, value: Fraction) -> str:
+    """數值欄的門檻代碼全名。"""
+    low, high = CUTS[field]
+    index = 1 if value < low else 2 if value < high else 3
+    return f"{field}:band_{index}"
+
+
+def day_type(day: date) -> str:
+    """加額日(UTC)是平日或週末;合成資料沒有國定假日,週六日即假日。"""
+    return WEEKEND if day.weekday() >= 5 else WEEKDAY  # 週六是 5
+
+
+def scale_bucket(spend_cents: int) -> str:
+    low, high = SCALE_CUTS_CENTS
+    return SCALE_BUCKETS[0] if spend_cents < low else SCALE_BUCKETS[1] if (
+        spend_cents < high) else SCALE_BUCKETS[2]
+
+
+def _clause(raw: object) -> Clause:
+    if not (isinstance(raw, tuple) and len(raw) == 2):  # 子句是一對代碼
+        raise VocabularyError("bad_clause")
+    condition, threshold = raw
+    if not (isinstance(condition, str) and isinstance(threshold, str)
+            and CODE.fullmatch(condition) and CODE.fullmatch(threshold)):
+        raise VocabularyError("bad_code")
+    if condition not in THRESHOLDS:
+        raise VocabularyError("unknown_condition")
+    if threshold not in THRESHOLDS[condition]:
+        raise VocabularyError("threshold_not_in_condition")
+    return condition, threshold
+
+
+def condition_key(clauses: Iterable[object]) -> ConditionKey:
+    """一或兩個不同欄位的子句 → 排序後的條件鍵;語彙外、同欄重複或矛盾、零或三個以上子句都丟錯。"""
+    parsed = [_clause(raw) for raw in clauses]
+    if not 1 <= len(parsed) <= 2:  # 至多兩個不同欄位
+        raise VocabularyError("clause_count")
+    if len({condition for condition, _ in parsed}) != len(parsed):
+        raise VocabularyError("duplicate_condition")
+    return tuple(sorted(parsed))
+
+
+def normalized_key(clauses: Iterable[object], direction: object) -> NormalizedKey:
+    key = condition_key(clauses)
+    if direction not in DIRECTIONS:
+        raise VocabularyError("unknown_direction")
+    assert isinstance(direction, str)  # noqa: S101 - 上一行已確認
+    return key, direction
+
+
+def all_conditions() -> tuple[ConditionKey, ...]:
+    """封閉條件全集(單欄 + 兩個不同欄的組合),按正規化鍵升序;模型可選與窮舉掃的是同一份。"""
+    singles: list[ConditionKey] = [((name, code),) for name in FIELDS
+                                   for code in THRESHOLDS[name]]
+    pairs: list[ConditionKey] = [((a, x), (b, y)) for a, b in combinations(FIELDS, 2)
+             for x in THRESHOLDS[a] for y in THRESHOLDS[b]]
+    return tuple(sorted(singles + pairs))
+
+
+def key_text(key: ConditionKey) -> str:
+    """彙總表與報告的條件寫法:門檻代碼全名以 & 相連(欄位代碼就是冒號前那段)。"""
+    return "&".join(threshold for _, threshold in key)
+
+
+def version_params() -> dict[str, Any]:
+    """評估版本的完整參數清單(JSON 可序列化);雜湊由提示模組連同系統提示一起算。"""
+    return {
+        "eval_version": EVAL_VERSION, "generator_version": GENERATOR_VERSION,
+        "seeds": list(SEEDS), "k": K, "min_directed": MIN_DIRECTED,
+        "min_distinct_ads": MIN_DISTINCT_ADS, "holdout_support": str(HOLDOUT_SUPPORT),
+        "window_days": WINDOW_DAYS,
+        "split": "sha256(ad_id utf-8) 升序,同雜湊再比編號位元組;前 floor(N/2) 探索,其餘保留",
+        "control_pool": "同側、整個觀察期從未加額的廣告;每條件每支至多一次",
+        "pair_fields": ["side", "utc_day", "pre_cvr_band", "scale_bucket"],
+        "scale_cuts_cents": list(SCALE_CUTS_CENTS), "scale_buckets": list(SCALE_BUCKETS),
+        "cuts": {name: [str(c) for c in CUTS[name]] for name in sorted(CUTS)},
+        "thresholds": {name: list(THRESHOLDS[name]) for name in FIELDS},
+        "day_type": "UTC 星期六、日為 weekend,其餘 weekday",
+        "event_order": ["utc_day", "raised_ad_id_utf8", "committed_at", "op_id"],
+        "control_order": "ad_id utf-8 位元組升序,取第一支未用者",
+        "exclusions": ["incomplete_window", "overlapping_adjustment", "anomalous_data",
+                       "missing_value", "zero_denominator", "no_control"],  # 判斷先後
+        "effect": "(加額後三日率-加額前三日率)-(對照後三日率-對照前三日率),"
+                  "率=轉換/點擊,rtb.domain.metrics.exact_ratio 精確分數",
+        "mean": "有方向差值合計 / 有方向配對數;平手不計支持與反例",
+        "distinct_counts": "兩側相異廣告數與相異 UTC 日期數只數有方向配對",
+        "ranking": ["wilson_lower(支持, 支持+反例) 降序", "方向化精確平均差值降序",
+                    "有方向配對數降序", "正規化條件鍵升序", "方向代碼升序"],
+        "wilson": "rtb.eval.scoring.wilson_lower,Z_95=1.959963984540054",
+        "directions_merge": "同一條件兩方向只留排序較高者,再取全域前 K",
+        "formats": {"percent": "rtb.domain.metrics.percent_text(一位小數 half-even)",
+                    "diff": "百分點四位小數,精確分數 half-even"},
+        "prompt_bytes_limit": PROMPT_BYTES_LIMIT, "gateway_prompt_bytes": GATEWAY_PROMPT_BYTES,
+        "max_output_tokens": MAX_OUTPUT_TOKENS, "reply_bytes_limit": REPLY_BYTES_LIMIT,
+        "note_chars": NOTE_CHARS, "note_bytes": NOTE_BYTES,
+        "call_timeout_seconds": CALL_TIMEOUT_SECONDS,
+    }
diff --git a/tests/eval/test_model_candidate.py b/tests/eval/test_model_candidate.py
index 2369dda..7424573 100644
--- a/tests/eval/test_model_candidate.py
+++ b/tests/eval/test_model_candidate.py
@@ -269,28 +269,35 @@ def _eval_roots():
 # Phase 13 改寫 [S918](計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈要改寫的既有合約〉):評估套件的
 # 閉包只准多出寫死的准許名單——模型用戶端閉包(含小常數模組,見上)、模型閘道、AI 決策模組
 # (AI 決策函式所在的 ai_judge 與它的詞彙模組 investigation,增量 2 拆成兩支)、Phase 13 增量 3
 # 新增的評估模組(評估集、生成器與標準答案、執行器、報告)。增量 3 的評估執行器匯入 AI 決策模組,
 # 名單在這一增量補齊
 PHASE13_ALLOWED: frozenset[str] = frozenset({
     "rtb.analyzer.modelgate", "rtb.analyzer.ai_judge", "rtb.analyzer.investigation",
     "rtb.domain.nine_rules",  # Phase 14 增量 1:評估標準答案共用純領域判斷
     "rtb.eval.investigation_cases", "rtb.eval.investigation_set", "rtb.eval.investigation_eval",
     "rtb.eval.investigation_report"})
+# Phase 15 增量 1 改寫 [S918](計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]〈要改寫的既有合約〉):
+# 規則模式探索的純離線生成/彙總/基準/預檢四支,精確新增;它們不碰模型用戶端與閘道(下面的
+# importers、senders、judge_* 等式照舊)。增量 2 的分析端窄函式另行逐項加
+PHASE15_ALLOWED: frozenset[str] = frozenset({
+    "rtb.eval.rule_mining_vocab", "rtb.eval.rule_mining_history", "rtb.eval.rule_mining_baseline",
+    "rtb.eval.rule_mining_prompt"})
 # 經 AI 決策模組送出的名字(開閘道、把閘道包成送出函式):評估套件裡只准評估執行器用
 AI_JUDGE_SENDS = frozenset({"open_investigation_gate", "gate_complete"})
 
 
 def test_the_eval_package_reaches_the_model_only_through_the_model_client(tmp_path):  # noqa: PLR0915
     closure = _closure(_eval_roots())
     # 允許多出來的分支寫死(代碼審第 2 輪:動態算的話,模型用戶端多匯入什麼都會被跟著放行)
-    branch = MODEL_CLIENT_CLOSURE | {"rtb.eval.model_candidate"} | PHASE13_ALLOWED
+    branch = (MODEL_CLIENT_CLOSURE | {"rtb.eval.model_candidate"} | PHASE13_ALLOWED
+              | PHASE15_ALLOWED)
     assert set(_closure(["rtb.modelclient"])) == MODEL_CLIENT_CLOSURE
     assert "rtb.modelclient" in closure
     assert set(closure) - BASELINE <= branch, sorted(set(closure) - BASELINE - branch)
     # 評估套件自己不匯入網路或子行程模組、不動態匯入(子行程只在模型用戶端,[S917] 的全庫掃描另守)
     offenders = []
     for path in sorted(EVAL.glob("*.py")):
         tree = ast.parse(path.read_text(encoding="utf-8"))
         offenders += _network_offenders(path.name, tree)
         offenders += [f"{path.name}: {m}" for m in _imported_modules(SRC, "rtb.eval.x", tree)
                       | _top_imports(tree) if m.split(".")[0] in SPAWN_MODULES]
diff --git a/tests/eval/test_rule_mining.py b/tests/eval/test_rule_mining.py
new file mode 100644
index 0000000..fdcefdc
--- /dev/null
+++ b/tests/eval/test_rule_mining.py
@@ -0,0 +1,468 @@
+"""Phase 15 增量 1:規則模式探索的合成歷史與無模型基準(計劃
+[[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉〈封閉條件語彙、彙總與窮舉基準〉)。
+
+合約 [S1501] [S1502] [S1503](只驗彙總內容與位元組閘,不接模型) [S1511] [S1512] [S1513] [S1515]
+[S1519] [S1520]。純離線:不呼叫任何模型、不讀寫 ~/.rtb。手造的小歷史只用來驗單一規則;固定三種子
+的整批生成用模組層快取,整個檔只各生成一次。
+"""
+
+import dataclasses
+import functools
+import hashlib
+from collections import Counter
+from datetime import UTC, date, datetime, timedelta
+from fractions import Fraction
+
+import pytest
+
+from rtb import modelcore
+from rtb.domain import metrics as m
+from rtb.eval import rule_mining_baseline as rb
+from rtb.eval import rule_mining_history as rh
+from rtb.eval import rule_mining_prompt as rp
+from rtb.eval import rule_mining_vocab as rv
+from rtb.eval import scoring
+
+START = date(2026, 8, 3)
+
+
+# ---- 手造歷史的小工具 ----
+def _bucket(day, clicks=100, conv=3, spend=3000, impressions=None):
+    return rh.DayBucket(day=day, impressions=clicks * 30 if impressions is None else impressions,
+                        clicks=clicks, conversions=conv, spend_cents=spend,
+                        revenue_cents=conv * 4000, no_data=False)
+
+
+def _ad(ad_id, days=10,  # noqa: PLR0913 - 造資料的小工具,各參數都有預設
+        pre=3, post=3, clicks=100, spend=3000, raise_at=None, budget=2000,
+        after=2600, changes=None, extra_raises=()):
+    """一支廣告:raise_at 之前每天 pre 筆轉換、之後每天 post 筆;changes 覆寫某天的桶。"""
+    buckets = []
+    for index in range(days):
+        conv = post if raise_at is not None and index > raise_at else pre
+        if raise_at is None and index >= 5:
+            conv = post
+        buckets.append(_bucket(START + timedelta(days=index), clicks=clicks, conv=conv,
+                               spend=spend))
+    for index, bucket in (changes or {}).items():
+        buckets[index] = bucket
+    adjustments = []
+    for k, at in enumerate(([] if raise_at is None else [raise_at]) + list(extra_raises)):
+        adjustments.append(rh.Adjustment(
+            op_id=f"{ad_id}-op{k}", committed_at=datetime.combine(
+                START + timedelta(days=at), datetime.min.time(), tzinfo=UTC) + timedelta(hours=9),
+            budget_before_cents=budget, budget_after_cents=after))
+    return rh.Ad(ad_id=ad_id, daily_budget_cents=budget, days=tuple(buckets),
+                 adjustments=tuple(adjustments))
+
+
+def _history(ads, days=10):
+    return rh.History(seed=0, generator_version="test", start=START, day_count=days,
+                      ads=tuple(ads))
+
+
+def _summary(history, ids=None, key=None):
+    side = frozenset(a.ad_id for a in history.ads) if ids is None else frozenset(ids)
+    summary = rb.summarize(history, side)
+    return summary if key is None else summary.stats[key]
+
+
+RAISE_2 = ((rv.RAISE_PCT, "raise_pct:band_2"),)  # 2600/2000 = 加 30%
+WEEKEND = ((rv.DAY_TYPE, "day_type:weekend"),)  # 第 5 天是週六
+
+
+def _pair(k, diff, raised=None, control=None, day=START):
+    return rb.Pair(op_id=f"op{k}", raised_ad=raised or f"a{k:02d}",
+                   control_ad=control or f"c{k:02d}", day=day, diff=Fraction(diff))
+
+
+def _stats(pairs, events=None):
+    return rb.stats_of(RAISE_2, len(pairs) if events is None else events, tuple(pairs), 0)
+
+
+# ---- 固定三種子(整個檔只生成一次) ----
+@functools.cache
+def _generated(seed):
+    return rh.generate(seed)
+
+
+@functools.cache
+def _preflight():
+    return rp.preflight()
+
+
+# ---- [S1501] ----
+def test_rule_mining_history_is_reproducible_and_consistent():  # noqa: PLR0915 - 逐項自洽檢查
+    assert rv.SEEDS == (15001, 15002, 15003)
+    with pytest.raises(ValueError, match="固定種子"):
+        rh.generate(15004)  # 任一批不得換種子
+    for seed in rv.SEEDS:
+        history = _generated(seed)
+        again = rh.generate(seed)
+        text = rh.render(history)
+        assert text == rh.render(again)  # 位元組一致
+        assert rh.data_sha256(history) == hashlib.sha256(text.encode()).hexdigest()
+        assert rh.data_sha256(history) == rh.EXPECTED_DATA_SHA256[seed]
+        assert history.seed == seed and history.generator_version == rv.GENERATOR_VERSION
+        assert len(history.ads) >= 300 and history.day_count >= 28
+        assert len({a.ad_id for a in history.ads}) == len(history.ads)
+        expected_days = [history.start + timedelta(days=i) for i in range(history.day_count)]
+        for ad in history.ads:
+            assert [b.day for b in ad.days] == expected_days  # 連續 UTC 日、每天一個桶
+            budget = ad.daily_budget_cents
+            by_day = {a.committed_at.date(): a for a in ad.adjustments}
+            for bucket in ad.days:
+                if bucket.day in by_day:  # 操作鏈:調整前預算接上一筆調整後
+                    assert by_day[bucket.day].budget_before_cents == budget
+                    budget = by_day[bucket.day].budget_after_cents
+                if bucket.no_data:
+                    assert bucket.clicks is None and bucket.spend_cents is None
+                    continue
+                values = (bucket.impressions, bucket.clicks, bucket.conversions,
+                          bucket.spend_cents, bucket.revenue_cents)
+                assert all(type(v) is int and v >= 0 for v in values)  # 整數分,不是浮點
+                assert bucket.conversions <= bucket.clicks <= bucket.impressions
+                assert bucket.spend_cents <= budget
+            for adjustment in ad.adjustments:
+                assert adjustment.committed_at.tzinfo is UTC
+                assert history.start <= adjustment.committed_at.date() <= expected_days[-1]
+                assert adjustment.budget_after_cents > adjustment.budget_before_cents
+    # 真相清單同一生成版本固定;真模式改善/未改善各一,誘餌兩類各一,鍵都是語彙內的正規化鍵
+    kinds = Counter((t.kind, t.direction) for t in rh.TRUTH)
+    assert kinds[(rh.TRUE_PATTERN, rv.IMPROVE)] >= 1
+    assert kinds[(rh.TRUE_PATTERN, rv.NOT_IMPROVE)] >= 1
+    assert sum(n for (kind, _), n in kinds.items() if kind == rh.DECOY_EXPLORE_ONLY) >= 1
+    assert sum(n for (kind, _), n in kinds.items() if kind == rh.DECOY_CORRELATED) >= 1
+    for item in rh.TRUTH:
+        assert rv.normalized_key(item.clauses, item.direction) == item.key
+        assert item.key[0] in rv.all_conditions()
+    # 真相清單不在評估集裡(評估集的文字不含類別名)
+    text = rh.render(_generated(rv.SEEDS[0]))
+    assert rh.TRUE_PATTERN not in text and rh.DECOY_CORRELATED not in text
+
+
+# ---- [S1502] ----
+def _two_ads(**raised):
+    raised_ad = _ad("a1", raise_at=5, pre=3, post=6, **raised)
+    control = _ad("c1", pre=3, post=4)  # 未加額:第 5 天以後每天 4 筆
+    return raised_ad, control
+
+
+def test_rule_mining_effects_use_complete_utc_days_and_exact_ratios():  # noqa: PLR0915
+    raised_ad, control = _two_ads()
+    history = _history([raised_ad, control])
+    events, excluded = rb.side_events(history, frozenset({"a1", "c1"}))
+    assert excluded == {} and len(events) == 1
+    (event,) = events
+    assert event.day == START + timedelta(days=5)
+    # 前三日 9/300、後三日 18/300:絕對差(不是 exact_change 的相對變化)
+    assert event.change == Fraction(18, 300) - Fraction(9, 300)
+    assert event.change == m.exact_ratio(18, 300) - m.exact_ratio(9, 300)
+    stats = _summary(history, key=RAISE_2)
+    assert stats.pairs == 1 and stats.positive == 1
+    assert isinstance(stats.diff_sum, Fraction)
+    # 對照 C 前三日 9/300、後三日(第 6-8 天)12/300 → 差值 9/300 - 3/300 = 1/50
+    assert stats.diff_sum == Fraction(1, 50)
+    # D 本身不算:把 D 當天改得很極端,效果不變
+    wild = _ad("a1", raise_at=5, pre=3, post=6, changes={5: _bucket(START + timedelta(days=5),
+                                                                     conv=90)})
+    assert _summary(_history([wild, control]), key=RAISE_2).diff_sum == Fraction(1, 50)
+
+    def reasons(*ads):
+        return rb.side_events(_history(ads), frozenset(a.ad_id for a in ads))[1]
+
+    # 未滿後三日 / 前三日不在觀察期內
+    assert reasons(_ad("a1", raise_at=7)) == {rb.INCOMPLETE_WINDOW: 1}
+    assert reasons(_ad("a1", raise_at=2)) == {rb.INCOMPLETE_WINDOW: 1}
+    # 視窗內另有調整:兩筆都不推斷
+    assert reasons(_ad("a1", raise_at=4, extra_raises=(6,))) == {rb.OVERLAPPING_ADJUSTMENT: 2}
+    # no_data、缺值
+    gap = rh.DayBucket(day=START + timedelta(days=6), impressions=None, clicks=None,
+                       conversions=None, spend_cents=None, revenue_cents=None, no_data=True)
+    assert reasons(_ad("a1", raise_at=5, changes={6: gap})) == {rb.MISSING_VALUE: 1}
+    none_clicks = dataclasses.replace(_bucket(START + timedelta(days=3)), clicks=None)
+    assert reasons(_ad("a1", raise_at=5, changes={3: none_clicks})) == {rb.MISSING_VALUE: 1}
+    # 資料異常:點擊比曝光多、浮點金額
+    odd = _bucket(START + timedelta(days=7), clicks=100, impressions=50)
+    assert reasons(_ad("a1", raise_at=5, changes={7: odd})) == {rb.ANOMALOUS_DATA: 1}
+    floaty = dataclasses.replace(_bucket(START + timedelta(days=4)), spend_cents=30.5)
+    assert reasons(_ad("a1", raise_at=5, changes={4: floaty})) == {rb.ANOMALOUS_DATA: 1}
+    # 轉換率分母為零:不當零
+    zero = {i: _bucket(START + timedelta(days=i), clicks=0, conv=0) for i in (6, 7, 8)}
+    assert reasons(_ad("a1", raise_at=5, changes=zero)) == {rb.ZERO_DENOMINATOR: 1}
+    # 對照 C 於 D+2 曾加額(D 日沒加額)也不得入池;其自己那筆因未滿後三日排除
+    raised_c = _ad("c1", pre=3, post=4, raise_at=7)
+    summary = _summary(_history([raised_ad, raised_c]))
+    assert summary.stats[RAISE_2].pairs == 0 and summary.stats[RAISE_2].no_control == 1
+    assert summary.exclusions == {rb.INCOMPLETE_WINDOW: 1}
+    # 對照的視窗不可算(分母為零)也不能用
+    zero_control = _ad("c1", changes={i: _bucket(START + timedelta(days=i), clicks=0, conv=0)
+                                      for i in (2, 3, 4)})
+    assert _summary(_history([raised_ad, zero_control]), key=RAISE_2).no_control == 1
+
+
+# ---- [S1503] ----
+def test_rule_mining_prompt_contains_only_bounded_aggregates():  # noqa: PLR0915
+    seed = rv.SEEDS[0]
+    history = _generated(seed)
+    sides = rh.split(a.ad_id for a in history.ads)
+    summary = rb.summarize(history, sides.explore)
+    table = rp.summary_table(summary)
+    rows = [line for line in table.splitlines() if line and line[0].islower()]
+    passing = [k for k in rv.all_conditions() if rb.meets_floor(summary.stats[k])]
+    assert len(rows) == len(passing) > 0  # 完整表,不截列
+    assert [r.split("|")[0] for r in rows] == [rv.key_text(k) for k in passing]
+    for row, key in zip(rows, passing, strict=True):
+        stats = summary.stats[key]
+        cells = row.split("|")
+        assert stats.directed >= 20 and stats.raised_ads >= 20 and stats.control_ads >= 20
+        assert cells[1:10] == [str(v) for v in (
+            stats.events, stats.pairs, stats.directed, stats.positive, stats.negative,
+            stats.ties, stats.raised_ads, stats.control_ads, stats.dates)]
+        assert cells[10] == m.percent_text(m.exact_ratio(stats.positive, stats.directed))
+        mean = stats.diff_sum / stats.directed
+        assert cells[11] == rp.diff_text(mean)
+    # 差值:百分點四位小數、精確分數 half-even;精確分數不外送
+    assert rp.diff_text(Fraction(1, 2_000_000)) == "0.0000"  # 0.00005 → 偶數 0
+    assert rp.diff_text(Fraction(3, 2_000_000)) == "0.0002"  # 0.00015 → 偶數 2
+    assert rp.diff_text(Fraction(-1, 50)) == "-2.0000"
+    assert rp.diff_text(Fraction(-1, 3_000_000)) == "0.0000"  # 負零寫 0
+    assert "/" not in table.replace("pp", "")
+    # 不送保留側、逐日列、識別、時間戳或真相標籤
+    prompt = rp.SYSTEM_PROMPT + table
+    for ad in history.ads:
+        assert ad.ad_id not in prompt
+        assert all(a.op_id not in prompt for a in ad.adjustments)
+    assert "2026-" not in prompt and "T0" not in prompt
+    for label in (rh.TRUE_PATTERN, rh.DECOY_CORRELATED, rh.DECOY_EXPLORE_ONLY, "真模式", "誘餌"):
+        assert label not in prompt
+    holdout_changed = dataclasses.replace(history, ads=tuple(
+        ad if ad.ad_id in sides.explore else dataclasses.replace(ad, days=ad.days[:5])
+        for ad in history.ads))  # 保留側怎麼變,送出的表都一樣
+    assert rp.summary_table(rb.summarize(holdout_changed, sides.explore)) == table
+    # 詞彙全部在系統提示裡;要求 UTF-8 原字、緊湊 JSON
+    for field in rv.FIELDS:
+        assert all(code in rp.SYSTEM_PROMPT for code in rv.THRESHOLDS[field])
+    assert "\\uXXXX" in rp.SYSTEM_PROMPT and "not_improve" in rp.SYSTEM_PROMPT
+    # 位元組閘:20480 可、20481 拒;達既有 48 KiB 也拒;常數與模型用戶端一致
+    assert rv.GATEWAY_PROMPT_BYTES == modelcore.MAX_PROMPT_BYTES == 48 * 1024
+    assert rp.gate_problem(20480) is None
+    assert rp.gate_problem(20481) is not None
+    assert rp.gate_problem(48 * 1024) is not None
+    assert rp.prompt_bytes(table) == len(rp.SYSTEM_PROMPT.encode()) + len(table.encode())
+
+
+# ---- [S1511] ----
+def _crowd(n_raised, n_controls, day=5, prefix="a"):
+    raised = [_ad(f"{prefix}{i:02d}", raise_at=day, pre=3, post=6) for i in range(n_raised)]
+    controls = [_ad(f"c{i:02d}", pre=3, post=4) for i in range(n_controls)]
+    return raised, controls
+
+
+def test_rule_mining_pairs_stay_within_split_and_use_distinct_ads():
+    # 21 筆加額只有 19 支對照:19 對、2 筆無對照,不合格
+    raised, controls = _crowd(21, 19)
+    stats = _summary(_history(raised + controls), key=RAISE_2)
+    assert (stats.pairs, stats.no_control, stats.control_ads) == (19, 2, 19)
+    assert not rb.meets_floor(stats)
+    # 21 對共用 1 支對照:相異對照只有 1 支,不合格
+    shared = _stats([_pair(k, 1, control="c00") for k in range(21)])
+    assert shared.directed == 21 and shared.control_ads == 1 and not rb.meets_floor(shared)
+    # 20 對、20/20 相異廣告:合格;星期條件照算且報日期數(全在同一天 → 1 個日期)
+    raised, controls = _crowd(20, 20)
+    summary = _summary(_history(raised + controls))
+    weekend = summary.stats[WEEKEND]
+    assert rb.meets_floor(weekend) and weekend.dates == 1
+    assert (weekend.raised_ads, weekend.control_ads) == (20, 20)
+    # 每條件每支對照至多一次;同一支對照可在不同條件各用一次
+    used = [p.control_ad for p in rb.pair_condition(
+        WEEKEND, rb.side_events(_history(raised + controls), frozenset(
+            a.ad_id for a in raised + controls))[0], rb.control_index(
+            _history(raised + controls), frozenset(a.ad_id for a in raised + controls)))[0]]
+    assert len(used) == len(set(used)) == 20
+    # 探索側只找得到保留側的對照:不跨側
+    history = _history([_ad("a1", raise_at=5, pre=3, post=6), _ad("c1", pre=3, post=4)])
+    stats = _summary(history, ids={"a1"}, key=RAISE_2)
+    assert stats.pairs == 0 and stats.no_control == 1
+    # 對照在觀察期曾加額(即使視窗外):不入池
+    history = _history([_ad("a1", raise_at=5, pre=3, post=6),
+                        _ad("c1", pre=3, post=4, raise_at=0)])
+    assert _summary(history, key=RAISE_2).no_control == 1
+
+
+# ---- [S1512] ----
+def test_rule_mining_ties_are_neutral_in_summary_and_recount():
+    pairs = [_pair(k, Fraction(1, 100)) for k in range(12)]
+    pairs += [_pair(k, Fraction(-1, 200)) for k in range(12, 20)]
+    pairs += [_pair(20, 0)]
+    stats = _stats(pairs)
+    assert (stats.positive, stats.negative, stats.ties, stats.pairs) == (12, 8, 1, 21)
+    assert stats.directed == 20 == stats.positive + stats.negative
+    assert stats.pairs == stats.positive + stats.negative + stats.ties
+    assert rb.directional(stats, rv.IMPROVE) == (12, 8, (Fraction(12, 100) - Fraction(8, 200)) / 20)
+    assert rb.directional(stats, rv.NOT_IMPROVE) == (
+        8, 12, -(Fraction(12, 100) - Fraction(8, 200)) / 20)
+    # 平手的廣告不算相異廣告
+    assert (stats.raised_ads, stats.control_ads) == (20, 20)
+    # 19 平手 + 1 正差:有方向數 1,不達下限(即使總配對 20、涉及 20 支廣告)
+    stats = _stats([_pair(k, 0) for k in range(19)] + [_pair(19, 1)])
+    assert (stats.pairs, stats.directed, stats.raised_ads) == (20, 1, 1)
+    assert not rb.meets_floor(stats)
+    # 重算:實際配對的平手也不進支持/反例
+    raised, controls = _crowd(3, 3)
+    tie = _ad("a09", raise_at=5, pre=3, post=4)  # 變化同對照 → 差值 0
+    stats = _summary(_history([*raised, tie, *controls, _ad("c09", pre=3, post=4)]), key=RAISE_2)
+    assert (stats.positive, stats.negative, stats.ties) == (3, 0, 1)
+
+
+# ---- [S1513] ----
+def _holdout(n_pos, n_neg, n_tie=0, pos=Fraction(1, 100), neg=Fraction(-1, 100)):
+    pairs = [_pair(k, pos) for k in range(n_pos)]
+    pairs += [_pair(n_pos + k, neg) for k in range(n_neg)]
+    pairs += [_pair(n_pos + n_neg + k, 0) for k in range(n_tie)]
+    return _stats(pairs)
+
+
+def test_rule_mining_holdout_rule_is_frozen_and_recounted():
+    frozen = (rv.MIN_DIRECTED, rv.MIN_DISTINCT_ADS, rv.HOLDOUT_SUPPORT)
+    assert frozen == (20, 20, Fraction(3, 5))
+    verdict = rb.holdout_verdict(_holdout(12, 7), rv.IMPROVE)  # 19 個有方向
+    assert not verdict.kept and rb.INSUFFICIENT_SAMPLE in verdict.reasons
+    verdict = rb.holdout_verdict(_holdout(11, 9), rv.IMPROVE)  # 11/20 < 3/5
+    assert not verdict.kept and verdict.reasons == (rb.LOW_SUPPORT,)
+    assert rb.holdout_verdict(_holdout(12, 8), rv.IMPROVE).kept  # 12/20 且平均差值正
+    # 12/20 但平均差值 ≤ 0:方向不符
+    verdict = rb.holdout_verdict(_holdout(12, 8, neg=Fraction(-2, 100)), rv.IMPROVE)
+    assert not verdict.kept and verdict.reasons == (rb.WRONG_SIGN,)
+    verdict = rb.holdout_verdict(_holdout(12, 8, neg=Fraction(-3, 200)), rv.IMPROVE)
+    assert not verdict.kept and verdict.reasons == (rb.WRONG_SIGN,)  # 平均剛好 0
+    # 19 平手 + 1 正差:總配對 20 仍不保留
+    verdict = rb.holdout_verdict(_holdout(1, 0, n_tie=19), rv.IMPROVE)
+    assert not verdict.kept and rb.INSUFFICIENT_SAMPLE in verdict.reasons
+    # 未改善方向對稱
+    assert rb.holdout_verdict(_holdout(8, 12), rv.NOT_IMPROVE).kept
+    assert not rb.holdout_verdict(_holdout(8, 12), rv.IMPROVE).kept
+    # 分母為零:未量
+    verdict = rb.holdout_verdict(_stats([]), rv.IMPROVE)
+    assert not verdict.kept and verdict.reasons == (rb.INSUFFICIENT_SAMPLE, rb.UNMEASURED)
+    # 相異廣告不足也不保留
+    few = _stats([_pair(k, 1, control="c00") for k in range(20)])
+    assert rb.INSUFFICIENT_SAMPLE in rb.holdout_verdict(few, rv.IMPROVE).reasons
+
+
+# ---- [S1515] ----
+def _ranked_stats(key, n_pos, n_neg, pos=Fraction(1, 100), neg=Fraction(-1, 100)):
+    pairs = [_pair(k, pos) for k in range(n_pos)] + [
+        _pair(n_pos + k, neg) for k in range(n_neg)]
+    return rb.stats_of(key, len(pairs), tuple(pairs), 0)
+
+
+def test_rule_mining_exhaustive_order_is_frozen_and_symmetric():
+    assert rv.K == 10
+    a, b = ((rv.PRE_CVR, "pre_cvr:band_1"),), ((rv.PRE_CVR, "pre_cvr:band_2"),)
+    # 17/21 比例較高,但 Wilson 下界 70/100 較高 → 70/100 先
+    table = {a: _ranked_stats(a, 17, 4), b: _ranked_stats(b, 70, 30)}
+    ranked = rb.rank(table)
+    assert [r.key for r in ranked] == [b, a]
+    assert ranked[0].wilson == scoring.wilson_lower(70, 100)  # 直接用既有函式與 Z_95
+    assert ranked[1].wilson == scoring.wilson_lower(17, 21)
+    # 兩方向對稱:鏡像的未改善得到同一下界與方向化平均
+    mirrored = {a: _ranked_stats(a, 4, 17), b: _ranked_stats(b, 30, 70)}
+    ranked_m = rb.rank(mirrored)
+    assert [(r.key, r.direction) for r in ranked_m] == [(b, rv.NOT_IMPROVE), (a, rv.NOT_IMPROVE)]
+    assert [r.wilson for r in ranked_m] == [r.wilson for r in ranked]
+    assert [r.directional_mean for r in ranked_m] == [r.directional_mean for r in ranked]
+    # 同鍵兩方向只留較高者;下界相同時依方向化平均、有方向數、鍵、方向代碼
+    c = ((rv.SPEND_RATIO, "spend_ratio:band_1"),)
+    d = ((rv.DAY_TYPE, "day_type:weekend"),)
+    table = {c: _ranked_stats(c, 15, 10, pos=Fraction(1, 100)),
+             d: _ranked_stats(d, 15, 10, pos=Fraction(2, 100))}
+    ranked = rb.rank(table)
+    assert [r.key for r in ranked] == [d, c] and len(ranked) == 2
+    even = {c: _ranked_stats(c, 10, 10), d: _ranked_stats(d, 10, 10)}
+    ranked = rb.rank(even)  # 下界、平均(0)、數量都相同 → 鍵升序、方向代碼升序
+    assert [(r.key, r.direction) for r in ranked] == [(d, rv.IMPROVE), (c, rv.IMPROVE)]
+    # 未達下限不入選;前 K 至多 K 條
+    small = {a: _ranked_stats(a, 19, 0)}
+    assert rb.rank(small) == ()
+    many = {k: _ranked_stats(k, 20 + i, 5) for i, k in enumerate(rv.all_conditions())}
+    top = rb.top_k(many)
+    assert len(top) == rv.K
+    assert top == rb.rank(many)[: rv.K]
+    assert rb.top_k(many, 3) == top[:3]
+
+
+# ---- [S1519] ----
+def test_rule_mining_preflight_checks_all_fixed_seed_prompts():
+    results = _preflight()
+    assert tuple(r.seed for r in results) == rv.SEEDS  # 三批全列,不換種子
+    for result in results:
+        history = _generated(result.seed)
+        sides = rh.split(a.ad_id for a in history.ads)
+        table = rp.summary_table(rb.summarize(history, sides.explore))
+        # 量的是完整系統提示加彙總表
+        assert result.prompt_bytes == len(rp.SYSTEM_PROMPT.encode()) + len(table.encode())
+        assert result.data_sha256 == rh.EXPECTED_DATA_SHA256[result.seed]
+        assert [t.key for t in result.truths] == [t.key for t in rh.TRUTH]
+        assert (result.explore_ads, result.holdout_ads) == (len(sides.explore),
+                                                            len(sides.holdout))
+    assert rp.preflight_problems(results) == ()
+    assert rp.preflight_record(results) == rp.PREFLIGHT_RECORD  # 版本理由記的數字與重算一致
+    # 第三批 20481 位元組:即使前兩批較小也整體失敗,並點名該批
+    too_big = (*results[:2], dataclasses.replace(results[2], prompt_bytes=20481))
+    problems = rp.preflight_problems(too_big)
+    assert len(problems) == 1 and str(rv.SEEDS[2]) in problems[0]
+    # 真模式/誘餌分母不可達也失敗
+    first = results[0]
+    unreachable = dataclasses.replace(first.truths[0], explore=rb.Reach(19, 19, 19))
+    broken = (dataclasses.replace(first, truths=(unreachable, *first.truths[1:])), *results[1:])
+    assert rp.preflight_problems(broken)
+    # 少一批也失敗(不得只報兩批)
+    assert rp.preflight_problems(results[:2])
+
+
+# ---- [S1520] ----
+def test_rule_mining_pairing_parameters_are_frozen():
+    ids = [f"ad-{i:03d}" for i in range(7)]
+    order = sorted(ids, key=lambda a: (hashlib.sha256(a.encode()).digest(), a.encode()))
+    sides = rh.split(reversed(ids))
+    assert sides.explore == frozenset(order[:3]) and sides.holdout == frozenset(order[3:])
+    assert rh.split(ids) == sides  # 輸入順序不影響
+    # 規模桶以前三日花費整數分
+    assert [rv.scale_bucket(v) for v in (9999, 10000, 49999, 50000)] == [
+        "lt_10000", "10000_49999", "10000_49999", "ge_50000"]
+    # 兩筆同日同桶加額搶一支 C:固定鍵較前者拿到 C;重跑、換輸入順序結果相同
+    first = _ad("a1", raise_at=5, pre=3, post=6)
+    second = _ad("a2", raise_at=5, pre=3, post=6)
+    c = _ad("c1", pre=3, post=4)
+
+    def winners(ads):
+        history = _history(ads)
+        side = frozenset(a.ad_id for a in ads)
+        pairs, missing = rb.pair_condition(RAISE_2, rb.side_events(history, side)[0],
+                                           rb.control_index(history, side))
+        return [(p.raised_ad, p.control_ad) for p in pairs], missing
+
+    assert winners([first, second, c]) == ([("a1", "c1")], 1)
+    assert winners([c, second, first]) == ([("a1", "c1")], 1)
+    # 次序鍵先比 D 再比加額廣告編號(a0 < a1),不看輸入順序或操作識別碼
+    early = dataclasses.replace(second, ad_id="a0", adjustments=(dataclasses.replace(
+        second.adjustments[0], op_id="z", committed_at=second.adjustments[0].committed_at
+        - timedelta(hours=1)),))
+    assert winners([first, early, c]) == ([("a0", "c1")], 1)
+    # 對照按編號 UTF-8 位元組升序取第一支未用者
+    assert winners([first, _ad("c2", pre=3, post=4), c])[0] == [("a1", "c1")]
+    # 不同規模桶或不同前三日轉換率區間的對照不配
+    big = _ad("c1", pre=3, post=4, spend=20000)
+    assert winners([first, big]) == ([], 1)
+    other_band = _ad("c1", pre=9, post=9)  # 9/100 ≥ 5%,前三日率落在別區
+    assert winners([first, other_band]) == ([], 1)
+    # 凍結:評估版本雜湊涵蓋切分、對照池、配對欄位與切點、搶用次序、效果公式、K 與下限
+    params = rv.version_params()
+    for name in ("split", "control_pool", "pair_fields", "scale_cuts_cents", "event_order",
+                 "control_order", "effect", "mean", "k", "min_directed", "min_distinct_ads",
+                 "holdout_support", "ranking", "seeds", "cuts", "formats"):
+        assert name in params, name
+    assert params["seeds"] == list(rv.SEEDS)
+    assert params["scale_cuts_cents"] == [10000, 50000]
+    assert rp.version_sha256() == rp.EXPECTED_VERSION_SHA256

## docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase15AI找規則模式_計劃.md @ d336754
---
type: project
status: doing
created: 2026-09-26
updated: 2026-09-26
plan_risk: high
summary: |-
  WHY: 2026-09-26 使用者裁定，Phase 13 評估顯示程式可算的加額判斷由程式較好，AI 退出正式加額決策；本計劃只用固定種子的合成歷史離線找新規則模式，機械核對並與窮舉基準比較，經人確認及 Issue／設計審／代碼審後才可能寫進九條或新條。出處：本次使用者對話；[[Projects/RTB_Phase13AI參與決策_計劃]]、[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]。
  VERIFY: 本文重驗入口與預定報告指標是 --ai-judge、--batch-id、--demo-id、--ledger、--recordings-dir、--verify、governance/eval/ 下的 phase15-rule-mining.md；涉及的程式路徑以程式碼為準，開場用 `rg --files src/rtb tests` 重驗：investigation_eval.py、investigation_report.py、src/rtb/analyzer/modelgate.py、src/rtb/domain/metrics.py、src/rtb/domain/nine_rules.py、src/rtb/dsp/store.py、src/rtb/eval/investigation_cases.py、src/rtb/eval/investigation_eval.py、src/rtb/modelclient.py、src/rtb/modelledger.py、src/rtb/modelledger_view.py、src/rtb/modelrecording.py、tests/test_spawn_boundary.py、tests/eval/test_model_candidate.py、tests/model/test_shared_entry.py。
tags:
  - type/project
  - status/doing
lands_in:
  - Systems/評估與Jev決策點
  - Systems/模型用戶端
  - Systems/規則模式探索模型入口
  - Systems/規則模式探索評估
---
# RTB_Phase15AI找規則模式_計劃

PRIOR-ART: 最小解是 Phase 10／13 已有的固定種子合成集、逐筆重播與逐格報告，加一個離線規則搜尋實驗；借用 Phase 13 的封閉模型答案、共用模型閘道、錄製鍵與批次驗收，借用 `rtb.domain.metrics` 的精確比率。一般規則探勘先列有限條件、用同資料重算證據、以未見資料重驗，並與窮舉掃描比較；這裡用標準函式庫實作有限掃描，不採新套件或自動代理。出處：[[Projects/RTB_Phase10評估與Jev決策點_計劃]]、[[Projects/RTB_Phase13AI參與決策_計劃]]、本次指定程式前掃；沒有把合成結果當正式環境證據。
RETIRE-IF: 固定種子 15001、15002、15003 各以第一次 `outcome=ok` 且有效入庫的錄製作判定批次；令各批 AI 有效建議數為 n，先取定窮舉前 K=10 與前 n、AI 有效清單，再做保留集篩選，不遞補。召回用 K，精確度／誤報用同名額 n；AI 所有單條剔除、去重、反方向、核對不符及未達下限的提交均計「無效提交」，在 AI 誤報分子與分母各加一；相關誘餌另列，不進可計分母。三批過濾前／後 K 名額真模式召回均不低於 AI、同名額精確度均不低於 AI，且 AI 沒提供經人確認的額外可核對條件，就停用模型探勘呼叫、只留程式掃描；n=0 或任一方同名額可計分母為零的批次，該批比較判「窮舉勝」，展示仍寫「未量」而不把 0/0 當 0%。分母非零時，精確度不低於等價於可計誤報比例不高於。任一批可解析建議的機械筆數核對失敗比例超過 25%，先停用模型段並重審輸出契約；此比例只量數字轉錄一致性。非 `ok` 的呼叫不入庫、不參與三批判斷，記「呼叫失敗」並以同種子、全新展示編號重錄，不算新種子批；成功入庫後不得以同版本重錄替換，須換評估版本並記理由。撤模型時保留 `Caller.RULE_MINING`、`CAPPED_CALLERS` 成員、`CALLER_USERS` 對應鍵及歷史驗證模組、錄製與帳列；移除命令列及分析端送出入口和報告連結，CI 改驗歷史批次。事件入口：每次 `--verify` 重播與更換模型／條件語彙時的評估報告；真實歷史與人工標註可用時另開案。

## 這份計劃在解決什麼

- Phase 13 讓 AI 參與「要不要加預算」，Phase 10／13 的評估顯示這種程式可算的判斷由程式較好。Phase 14 已由使用者於 2026-09-26 裁定 AI 退出加額決策，只保留提案說明、告警原因推測與本計劃的找新規則模式。Phase 14 增量 3 將移除分析端 runner 的 AI 判斷開關；AI 決策模組只留給評估重播，正式流程此後沒有 AI 通往提案的入口。Phase 14 現存計劃仍把 `--ai-judge` 留在正式入口，故不能只憑「增量 3 已合入」宣稱退出；本案須等包含上述移除的增量 3 合入 main，實作前再核對 runner 已無 AI 判斷開關、展示驅動不再組該參數且 F7 可走規則路徑。本案不新增任何通往提案的路徑。
- 本案用合成歷史問「什麼可觀察條件下，加額後的成效較可能改善或不改善」，產出待人判斷的規則建議。AI 的答案只到離線報告，不送進分析行程、提案、收件口、執行端或 DSP 預算寫入。
- 成功不是模型說得像規則，而是：固定生成器埋入的真規律能找回、假規律能辨出、每條建議的樣本可由程式重算，並誠實比較不用 AI 的窮舉基準。合成資料只能證這個演示流程可運作，不能證真實投放有因果效果或可直接採用新規則。

## 使用者裁定

### 2026-09-26 使用者本人

1. 用程式生成數百支廣告、數週的固定種子合成歷史，事先埋真規律與假規律／巧合；可重產，結論不當統計保證。
2. 模型只產結構化建議報告，須有條件、門檻、方向、支持與反例筆數、信心說明；人確認後才開 Issue，經設計審、代碼審寫成九條或新條。模型不直接改規則、程式或設定。
3. 離線命令列產一份人讀報告，含真規律找回、誘餌誤報與機械核對；錄製入庫，README 連報告，不進一鍵展示頁。
4. AI 不再做加額決策；此計劃是保留的第三個 AI 角色。即時模式比照 Phase 13 須使用者明確授權；本輪只寫計劃，不呼叫模型。

### 2026-09-27 使用者本人

5. 開工，全程保持離線：AI 從歷史紀錄找候選規則 → 人與評估驗證 → 才可能變成程式。用意是讓「AI 退出決策」成為完整的故事——被評估拿掉的位置，改到離線提候選、靠證據取得採用資格，而不是 AI 被拔掉；README 開頭同步加一句核心主張「決策權必須由評估與可驗證證據取得」。

### 代使用者裁定(2026-09-26)

- 前 K 中未精確命中預埋真規律者原則上計誤報（純噪音、只在探索側偶合、誘餌變形及部分重疊均含）；唯一例外是預埋的「保留側也成立但無獨立效果」相關誘餌，列「觀察成立的相關規律」，不計召回或誤報，另欄解釋。召回與誤報在保留集篩選前後各報一次。理由：可觀察的相關條件不能偽裝成因果真模式，也不能和純噪音混算。
- 看任何生成結果前固定保留條件：保留側至少 20 個有方向配對且其中兩側各有 20 支相異廣告、支持比例至少 3/5、精確平均差值與候選方向同號；平手不進分母。理由：探索偶合須用同一條可重算的門檻檢驗。
- 每條條件的對照只取同一切分側、整個觀察期從未加額的廣告，每支對照廣告在該條條件中最多使用一次；至少 20 **有方向**配對須同時有至少 20 支不同加額廣告及 20 支不同對照廣告，星期類也照算；支持比例分母同為有方向配對數。理由：防跨側洩漏、重複對照和平手灌大樣本；19 平手＋1 正差即使涉及 20 支廣告，仍未達下限、無效。
- 模型一批只呼叫一次且不得分塊；完整提示需同時通過本案 20480 位元組、既有 48 KiB 與每展示剩餘上限。過大先在換評估版本時縮小固定語彙，仍過大就拒跑。理由：多塊沒有公平的全域前 K，完整表大小須先驗。
- AI 建議也須達相同樣本下限；差值為零的配對既不支持也不反例。模型信心說明只留原始回覆，報告只寫結構化欄位及程式重算數字。理由：兩邊分母與證據口徑一致，避免未核對文字偽裝報告。
- 窮舉排序第一鍵用支持比例的 Wilson 95% 下界；K、下限、門檻、排序、配對、切分及兩方向名額在看基準或 AI 結果前凍結，事後不得往偏袒任一方調，需改先換評估版本並記對雙方的預期影響。模型可少於 K 條；撤除比較以 AI 有效建議數 n 為等名額，窮舉只取前 n 條比精確度，真模式召回另用 K 報。AI 無效提交列入 AI 誤報分母與分子；n=0 記窮舉勝。理由：小樣本比例須保守排序，少報或踢掉無效條目不能美化比較。
- 回退保留 `Caller.RULE_MINING` 和上限集合成員、錄製目錄及歷史帳列，只撤送出路徑與報告連結；報告固定在 `governance/eval/` 目錄，檔名 `phase15-rule-mining.md`。理由：舊批可讀、月上限不失守，並沿用 Phase 10／13 落點。
- 實作入口等 Phase 14 **移除 runner 的 AI 判斷開關**之增量 3 合入 main 後才開，實作前核對 runner 已無該開關、展示驅動不再組參數且 F7 走規則路徑；AI 決策模組只留評估重播，本案不新增通往提案的路徑。理由：既有 Phase 14 計劃讓 AI 提案仍可經九條通過，單有規則否決不足以實現「AI 退出加額決策」。
- 三個評估種子現在固定為整數 `15001`、`15002`、`15003`，任一批不得替換，報告須全列。理由：封住看生成結果後挑三批的空間。
- 探索／保留按廣告編號的 SHA-256 排序固定前半／後半（奇數多出者進保留），投放規模以調整前三日花費整數分分成 `<10000`、`10000–49999`、`≥50000`；配對搶用對照時按固定鍵先後決定。轉換率變化為後三日率減前三日率的絕對差，平均差值只除有方向配對數；可達性預檢只在**建立或更換評估版本**時做，結果寫入版本理由。理由：這些選擇都會改變排名及保留結果，須與語彙一同凍結。
- 提示中的比例採百分比一位小數、差值四位小數，均以十進位 half-even 捨入；精確分數留程式端核對。增量 1 先以固定三種子量完整提示位元組，任何一批超過單次上限即失敗。理由：精確分數可能膨脹到使單次呼叫不可行。
- 離線呼叫逾時為 60 秒；錄製入庫還要該次呼叫結果 `ok`，逾時、額度、超支及暫時性錯誤記「呼叫失敗」，不入庫、不算 RETIRE-IF。信心說明最多 80 字；非有限數含 `1e400` 與任何物件層未知鍵整份拒絕。理由：避免失敗批次被當成模型輸、輸出撞頂或解析口徑分裂。
- 同名額任一方分母為零（含前 n 全是相關誘餌或保留側全被刷掉）即判該批「窮舉勝」；先取名單再篩保留側，不遞補。AI 所有不合格單條提交，包括未知門檻、兩方向都押、數字不符、未達下限、重複與格式錯誤，均計 AI 誤報；整份回覆因頂層格式或解析錯誤拒絕時，記 1 筆無效提交及 AI 誤報，n=0 仍判窮舉勝。理由：比較必能終止，且不能藉剔除洗掉錯選。
- 三種子各用專屬 `phase15-seed-<種子>-<序號>` 展示編號，從 1 遞增，每次重錄換新序號；每種子只准第一次成功錄製入庫，清單記編號與時間，替換須換評估版本並記理由。6144 輸出下每展示上限約剩 0.058 美元，失敗呼叫整筆預留扣帳，不在同一展示編號重試。理由：防重抽挑好結果，並避免展示額度卡住後續種子。
- Phase 14 前置條件同時驗 runner 已無 AI 判斷開關、展示驅動不再組 `--ai-judge` 且 F7 走規則路徑。Wilson 下界直接用 `rtb.eval.scoring.wilson_lower()`；百分比用 `rtb.domain.metrics.percent_text` 等既有函式，精度與格式以程式碼為準。理由：不能只移除參數接收端，也不另造統計或格式化做法。
- 門檻代碼一律用「欄位:區間」全名（如 `raise_pct:band_2`），未改善方向代碼為 `not_improve`，兩側相異廣告數只數有方向配對。模型提示要求 UTF-8 原字、不用 `\uXXXX`；回覆大小按實際 JSON 逸出後位元組計，解析超長整數的 `ValueError` 與非有限數同樣整份拒絕。理由：讓真相鍵、下限及輸出界一致，解析失敗不使命令列崩潰。
- 回退保留 `Caller.RULE_MINING` 的值 `rule_mining`、`CAPPED_CALLERS`、`CALLER_USERS` 鍵及歷史驗證模組的值、值→成員名對照及 [S918] 精確等式；只撤命令列與分析端送出入口及其白名單新增項，更新 [S918] 窄入口等式。理由：歷史錄製鍵仍可重算，邊界測試仍能守住精確集合。

## 現況（2026-09-26，分支 `phase15-rule-mining` 的 main 基底 `71b04d5`；以程式碼為準）

- 2026-09-27 增量 1 實作：分支 `phase15-inc1`，main 基底 `61bbb31`（含 Phase 14 增量 3）。開工前核對 Phase 14 前置條件：分析端驅動的參數只剩資料庫、DSP、收件口、逾時與間隔（無 AI 判斷開關），展示驅動組分析端參數時只給這幾項，[S1429] 的閉包測試通過；F7 未另實跑，依展示驅動只組規則參數推得走規則路徑。重新核對：`rg -n "add_argument" src/rtb/analyzer/runner.py`、`rg -n "def analyzer_args" -A5 src/rtb/demo/driver.py`。增量 1 的四支純離線模組與評估版本 v1 的可達性預檢記在 [[Systems/規則模式探索評估]]；未新增模型呼叫或通往提案的路徑。
- `src/rtb/analyzer/modelgate.py` 的 `open_gate()` 在入口判錄製或即時、綁死 `Caller`、錄製目錄及花費帳；`Gate.complete()` 把請求交給 `src/rtb/modelclient.py`。即時要 `RTB_MODEL_LIVE=1`、展示編號、可用後端與有效啟用紀錄，重播缺錄製會報錯，不會默默轉即時。重新核對：`rg -n 'def open_gate|def complete|def settings_from_env|def call_model' src/rtb/analyzer/modelgate.py src/rtb/modelclient.py`。
- `src/rtb/modelledger_view.py` 的 `Caller` 是封閉列舉；`src/rtb/modelledger.py` 的 `CAPPED_CALLERS` 現只含評估候選與實測，花費帳有按呼叫者的上限與估算。錄製鍵含呼叫者、模型、系統提示、使用者內容與輸出上限，批次與目錄有驗收。重新核對：`rg -n 'class Caller|CAPPED_CALLERS|def recording_key|def batch_file_problems' src/rtb/modelledger_view.py src/rtb/modelledger.py src/rtb/modelrecording.py src/rtb/modelclient.py`。
- `src/rtb/eval/investigation_cases.py` 固定種子產 Phase 13 案例，`investigation_eval.py` 以同一 AI 函式重播／即時執行並驗批次，`investigation_report.py` 把結果寫成人讀報告。`src/rtb/eval/ruff.toml` 禁直接匯入模型閘道與 DSP，`src/rtb/analyzer/ruff.toml` 禁匯入評估套件；Phase 10 [S712] 的反向匯入界線仍成立。重新核對：`rg -n 'SEED|def generate|def run|def batch_problems|modelgate|rtb.eval' src/rtb/eval/investigation_cases.py src/rtb/eval/investigation_eval.py src/rtb/eval/ruff.toml src/rtb/analyzer/ruff.toml`。
- `src/rtb/dsp/store.py` 的逐日成效以 UTC 日期桶保存，金額用整數分，最近加額由正常操作紀錄連同調整前預算與提交時刻推得；`src/rtb/domain/nine_rules.py` 有正式九條的純領域詞彙與順序，`src/rtb/domain/metrics.py` 有 `exact_ratio`／`exact_change`。本案只仿照歷史資料語意建離線案例，不從 DSP 讀取，不匯入九條規則來生成答案。重新核對：`rg -n 'daily_metrics|_latest_raise|def get_past_adjustments|ANSWER_ORDER|def exact_ratio|def exact_change' src/rtb/dsp/store.py src/rtb/domain/nine_rules.py src/rtb/domain/metrics.py`。

## 設計

### 合成歷史與可觀察效果

- 評估套件新增純生成器與資料型別：固定種子、固定評估時鐘，至少 300 支虛構廣告、至少 28 個連續 UTC 日。每支有每天曝光、點擊、轉換、花費、營收的完整日桶，另有零或多筆含加額前後預算及 `committed_at` 的操作；計數遵守資料自洽，金額先以整數分生成。生成器可 `render(generate(seed))` 得到位元組一致的評估集與雜湊；另存只給評估器的埋入真相清單（真模式、誘餌、生成版本），不能放進模型提示。
- 每個可評估的加額事件以操作發生的 UTC 日為 D，只取 D−3 至 D−1 與 D＋1 至 D＋3 六個**完整**日；D 本身排除，未滿後三日、缺值、`no_data`、資料異常或視窗重疊另一筆調整時不推斷成效，另報排除筆數與原因。對照池只收**整個 28 日以上觀察期從未加額**的廣告。按廣告編號的 UTF-8 位元組算 SHA-256、依 `(雜湊,編號)` 升序排列，前 `floor(N/2)` 支為探索，其餘為保留；同一廣告的全部日期與對照資格不跨側。配對須同一側、同一 UTC 日 D、同一調整前三日轉換率語彙區間及同一投放規模桶；投放規模取調整前三日花費整數分，固定桶為 `<10000`、`10000–49999`、`≥50000`。每條條件先按 `(D,加額廣告編號,committed_at,操作識別碼)` 升序處理加額事件；合格對照按廣告編號 UTF-8 位元組升序取第一支未用者，每支對照在該條件中至多使用一次；找不到就排除並計數。這是可重算的合成比較，不稱因果證明。
- 對每對加額／未加額事件，用 `rtb.domain.metrics.exact_ratio` 的精確分數算前三日與後三日轉換率（轉換／點擊）；各組「轉換率變化」固定為**後三日轉換率 − 前三日轉換率**的絕對差（百分點，不用 `exact_change` 的相對變化），配對差值為加額組變化 − 對照組變化。分母為零或不可算就排除，絕不當零。改善方向的支持是差值 > 0、反例是 < 0；未改善方向相反。差值 = 0 另列平手，兩方向支持與反例都不計；**有方向配對數 = 正差 + 負差 = 支持 + 反例**，支持比例、精確平均差值的分母及樣本下限都只用此數，平均的分子也只加有方向差值。同一條條件須至少 20 個有方向配對，且其中分別涉及至少 20 支相異加額與 20 支相異對照廣告；探索、保留、AI 重算與星期條件同口徑。19 平手＋1 正差 → 只有 1 個有方向配對，無效。另列總配對、平手與相異 UTC 日期數，不能把 20 對說成 20 個獨立日期。金額、比例顯示可捨入，判斷與核對不讀捨入字串；另列加額幅度、加額前後轉換與樣本數。
- 真模式至少各有一個改善與未改善，透過生成器在條件下改變後三天成效；誘餌至少一個只在探索集與結果偶合、一個與真條件相關但本身無獨立效果，預先寫在真相清單。後者若保留側也成立，標成「觀察成立的相關規律」，不把它當獨立因果效果或精確真模式。三個評估種子固定為 `15001`、`15002`、`15003`，在第一次生成前列入評估版本與雜湊；任何批次不得換種子，報告須列三批。同一廣告所有日期與對照資格只在同一邊；保留集與真相清單不餵模型或用來調提示／門檻。真模式與切點精確對齊，故召回只量「真相可由語彙精確表示」時的上界，不代表未知實際門檻的召回。若改生成器規律，連資料雜湊、錄製鍵與報告版本一起換並記原因；三個固定種子仍不換，不挑最好看的批次。

### 封閉條件語彙、彙總與窮舉基準

- 條件只用有限列舉和事先固定的門檻：加額日的 UTC 星期／平假日、加額幅度區間、調整前三日轉換率區間、前三日花費相對原預算區間；每條最多兩個不同欄位條件，邏輯只有 AND，方向代碼只有改善 `improve`／未改善 `not_improve`。**條件代碼**是欄位代碼，例如 `raise_pct`；**門檻代碼**一律是「欄位:區間」全名，例如 `raise_pct:band_2`，不能拿別欄位的門檻代碼套用。子句鍵是 `(條件代碼,門檻代碼全名)`；把一或兩個不同欄位的子句按欄位／門檻代碼排序，再接方向，才是去重、真相及誘餌精確比對的正規化鍵。所有切點在看任何基準或模型結果前寫死於評估版本，百分比用分數或整數基點；同欄位重複或矛盾、未知欄位、任意自然語言條件一律無效。九條正式規則的 `Cell`、`RuleReason` 不被擴充成探勘語彙。
- 程式先從探索集逐條件組合算彙總表：符合條件的加額事件數、總配對數、**有方向配對數**、只由有方向配對計得的兩側相異廣告數、相異 UTC 日期數、正差筆數、負差筆數、平手筆數、精確差值合計／有方向配對數、排除原因；`總配對數 = 正差 + 負差 + 平手`，`有方向配對數 = 正差 + 負差`，改善的支持／反例 = 正差／負差，未改善則相反。先用同一有方向樣本下限剔除列，再把所有剩餘條件的完整表給模型。送模型的百分比一位小數沿用 `rtb.domain.metrics.percent_text` 等既有格式化函式（以程式碼為準）；平均差值小數四位用精確分數 half-even 捨入，不另寫百分比捨入函式。精確分數及分子／分母只留程式端作判斷與核對，模型只轉錄整數支持與反例，不以捨入值核算真假。不送原始逐日列、廣告名稱、編號、操作識別碼、原始時間戳或真相標籤。彙總表規格、欄序、捨入規則、版本、雜湊與提示文字固定，位元組變動會換錄製鍵。
- 每批只用一次模型呼叫、一次完整提示、一次至多 K 條回覆，不分塊；離線命令列沒有分析租約，單次模型呼叫逾時固定 60 秒。輸出採緊湊 UTF-8 JSON，固定 `max_output_tokens = 6144`；每條說明至多 80 個 Unicode 字元且至多 240 UTF-8 位元組，說明不得含控制字元或需 JSON 逸出的引號、反斜線；條件及門檻代碼限 `[a-z0-9_:]{1,24}`，整數筆數最多九位。以十條、每條兩子句、兩個 24 位元組代碼與九位數筆數、80 個三位元組中文字說明的緊湊 JSON 實算為 5139 位元組；即使保留至 6000 位元組的回覆界，仍低於 6144 token 輸出上限的保守位元組界。提示要求緊湊 JSON、UTF-8 原字而非 `\uXXXX` 逸出；回覆大小以實際 JSON 逸出後的 UTF-8 位元組計，超過 6000 位元組整份拒絕，不依賴續寫。系統提示加使用者內容的 UTF-8 位元組數 `B ≤ 20480` 且 `B < 48×1024`；以現行 `modelcore.call_budget_nanousd` 的 `F=4000`、`R=1+3=4`、`Pᵢ=4000`、`Pₒ=10000` nanousd/token 及安全係數 `6/5`，預留 `ceil(6/5 × [R×((B+F)×Pᵢ+6144×Pₒ)+R×(R−1)/2×6144×Pᵢ])`。以這組現行常數及 `B=20480` 算得 `941875200` nanousd，每展示 1 美元上限尚餘 `58124800` nanousd（約 0.058 美元）；非 `ok` 呼叫依既有帳本整筆預留扣帳、每月 20 美元與其他計入上限者共用，重錄不得沿用該展示編號；呼叫前仍用 repo 預留函式與當時實際價目、已用額重算，須 ≤ 每展示及每月剩餘上限，無餘裕就拒絕即時呼叫。輸出不足、提示或預留額超限不截列、不加第二次呼叫；只在換評估／提示版本時縮固定語彙並重算完整表，仍裝不下就拒跑。錄製模式也用同一提示／輸出界限。
- 不用 AI 的基準在同一有限條件全集、同一探索集與同一有方向樣本下限逐條窮舉。先凍結 `K=10`、20 個有方向配對及兩側各 20 支相異廣告、保留比例 `3/5` 與排序鍵：兩方向合併先依支持比例的 **Wilson 95% 下界**降序，直接呼叫既有 `rtb.eval.scoring.wilson_lower(支持, 支持+反例)`（含該函式的 `Z_95` 常數及 float 行為），n<20 不入選，不另寫 Decimal 版。再依方向化的精確平均差值降序（改善取平均差值，未改善取相反數）、再依有方向配對數降序、最後依正規化條件鍵及方向代碼升序。同一條件兩方向至多留排序較高者，從全域排序取前 K。AI 清單不得對同一條件同時押兩方向；可少於 K，基準只在合格候選不足時少於 K。模型可選的候選全集與基準掃的全集一致。K、三個種子、生成版本、切分雜湊及 50/50 規則、對照池、配對欄位及切點、搶用排序、效果與平均公式、下限、語彙、既有 Wilson 函式版本、兩方向合併規則、格式化規則及輸入輸出上限，都在看**任何**基準或 AI 結果前寫入評估版本與雜湊；看結果後須先換評估版本並記理由、可達性預檢結果及對雙方的預期影響，不能直接調參。

### 模型建議、機械核對與人讀報告

- 評估套件保留純生成／彙總／核對／報告，不直接匯入 `modelgate` 或 `dsp`。新增分析端窄函式只接**已完成的彙總文字**與閘道設定，使用字面 `Caller.RULE_MINING` 開既有 `modelgate` 並呼叫一次模型，不讀評估集、不碰 `flow`／`policy`／`runner`；實作時新建 `Systems/規則模式探索模型入口` 作新檔專屬的家。評估命令列可呼叫這支分析端模型函式，方向與 Phase 13 評估執行器依賴 AI 函式相同；正式分析端仍不能匯入 `rtb.eval`。實作時需把新送出入口與呼叫者綁定加入邊界測試白名單，不能放寬成整個套件都可送出。
- 模型回覆是封閉 JSON：頂層只有 `version`、`suggestions`，至多 K 條；每條只有 `clauses`、`direction`、`support`、`counterexample`、`confidence_note`；子句只有 `condition`、`threshold`。例如 `{"version":1,"suggestions":[{"clauses":[{"condition":"raise_pct","threshold":"raise_pct:band_2"}],"direction":"improve","support":24,"counterexample":6,"confidence_note":"待核對"}]}`，正規化鍵是 `((raise_pct,raise_pct:band_2),improve)`。`confidence_note` 至多 80 個 Unicode 字元及 240 UTF-8 位元組，僅存原始回覆供追查，不進人讀報告。JSON 用拒絕重複鍵的 `object_pairs_hook`、拒絕 `NaN`／`Infinity` 的 `parse_constant`、對 `parse_float` 的非有限值檢查（含 `1e400`）；解析後也遞迴驗 `math.isfinite`；解析超長整數拋出的 `ValueError` 與非有限數同樣整份拒絕，不讓命令列崩潰。頂層、建議物件或子句物件任一未知鍵，重複鍵、非有限數、超長整數的解析 `ValueError`、超 K、超 6000 位元組或錯版本均**整份拒絕**，不能截前 K。單條若有布林冒充整數、浮點（含 `1.0`）、負數或越界筆數、非字串或過長代碼、欄位不屬門檻、零或三個以上子句、同欄位重複／矛盾、未知方向、信心欄型別或長度錯誤，就**只丟該條並記原因**；同一正規化鍵重複或同條件押相反方向時保留先出現的合法條，後條丟棄並記原因；上述所有單條剔除及去重條目均另列「無效提交」、計 AI 誤報分子與分母，不因丟棄免計；整份拒絕無法可靠逐條計數時，以該回覆 1 筆無效提交及 AI 誤報計。不執行模型文字，也不把信心說明當統計信賴度。
- 對每條語法合法建議，程式在模型看到的**同一探索集原資料**重新套條件、同側不放回配對並計算支持與反例；任一模型自報筆數不同即標「無法核對」並丟出有效清單，不以模型數字填報。重算後有方向配對數或其中兩側相異廣告數任一小於 20，標「未達樣本下限」並排除有效清單；例如 19 平手＋1 正差、模型報 1/0，即使總配對 20 仍無效。AI 所有單條不合格提交（含格式、語彙、重複、反方向、核對不符及未達下限）另計「無效提交」，在同名額比較的 AI 誤報分子與分母各加一，保留側不得讓它消失。保留側對有效條件獨立重算：至少 20 個有方向配對且其中兩側各 20 支相異廣告、支持／有方向配對數 ≥3/5，且方向化精確平均差值 >0 才「可保留」；否則「不保留」並列明樣本不足、比例不足或方向不符。分母為零標未量。門檻在探索／保留結果之前固定，保留結果不回饋模型。機械核對只證條件與筆數相符，不證規律普遍成立；人仍要看樣本、日期群聚、對照選法、差值、誘餌與實務可解釋性。
- 離線命令列預設重播入庫錄製，支援生成資料與純基準報告、`--verify` 驗收、`--recordings-dir`、`--batch-id`、每種子專屬 `--demo-id` 與錄製模式專用 `--ledger`；展示編號用 `phase15-seed-<種子>-<序號>`，同種子每次呼叫序號遞增、不重用；即時須使用者明確設既有 `RTB_MODEL_LIVE=1` 並滿足閘道預檢，不能因缺錄製自動即時。新呼叫者的即時計入既有每展示／每月上限，估算花費照帳記。錄製到新空目錄、同批次驗收成功／缺錄製零筆／失敗類零份，且**唯一呼叫的結果 `outcome=ok`**才入庫；逾時、額度不足、超支、暫時性錯誤等非 `ok` 一律記「呼叫失敗」，該固定種子批次不入庫、不算 RETIRE-IF，待同種子以新展示編號重錄。每種子只有第一次 `outcome=ok` 的成功錄製可入庫並用於判定；清單記展示編號、錄製時間與所有嘗試，成功後不得同版本重抽替換，替換須換評估版本並記理由。CI 只重播成功入庫錄製，不要求真模型。
- 報告檔固定置於 `governance/eval/`，檔名 `phase15-rule-mining.md`（實作時建立），README 只連到它。開頭醒目寫「固定種子合成資料、非統計保證、非自動規則；真模式在語彙切點上，召回只是可精確表示時的上界」；列生成／語彙／提示版本、種子與資料雜湊、探索與保留樣本、排除筆數、錄製來源／批次／模型／估算花費。逐條只顯示結構化條件與方向、程式重算的支持／反例／平手、相異兩側廣告數與日期數、精確差值可讀刻度、保留集結果、是否也在窮舉前 K；不顯示模型 `confidence_note` 或任何未核對自由文字。
- 評估表對三個固定種子**全列批次**，同列 AI 與窮舉的實際有效候選數、AI 原回覆數／可解析數／無效提交數、真模式召回、誤報、誘餌命中、機械核對失敗數與呼叫失敗狀態。真模式召回分母是預埋真模式總數，**窮舉以 K=10 前 K 報召回**；相關誘餌若在保留側也成立而無獨立效果，另列「觀察成立的相關規律」及白話說明，**不計召回、不計誤報**；純噪音、只在探索側偶合、誘餌變形及部分重疊仍計誤報。原始前 K 指標照列揭露，RETIRE-IF 另用同名額精確度列：以 AI 有效建議數 n 取窮舉前 n 條，`精確度 = 真模式命中/(真模式命中+可計誤報)`，等價誤報比例用同一分母；相關誘餌不進此分母，AI 所有單條剔除、去重、反方向、核對不符及未達下限的無效提交在 AI 可計誤報分子與分母各加一；整份拒絕記 1 筆無效提交與誤報，探索與保留過濾後仍保留懲罰。雙方先取定前 K／前 n 與 AI 有效清單，再在原名單上過保留集，不遞補；各列有效候選、無效提交、真模式命中、誤報分子與分母。n=0 記「AI 無有效建議、窮舉勝」；n>0 但任一方同名額可計分母為零，也判該批「窮舉勝」，展示指標仍寫未量，不寫 0%。誘餌另按預先凍結的代碼及方向列精確命中／誘餌總數，不讓相關誘餌混入誤報。逐條標部分重疊及真相類別；有效候選過濾前後各報一欄，無效提交獨立保留一欄。AI 與基準不同出榜數時以同名額欄判撤除，原始前 K 僅作揭露。
- 另報 AI 與基準前 K 的正規化鍵交集、各自獨有數與「AI 有效清單是否**完全**落在窮舉前 K 內」布林；逐條標重疊／AI 獨有。若 AI 獨有數為零，摘要固定寫「程式掃描已找到 AI 清單中的全部條件」，不能稱 AI 帶來新條件；若只有部分重疊，列出哪幾條是 AI 獨有，並分別標其真相及保留結果。
- 人確認某條後，由人開 Issue，附這份報告、條件與重算筆數；該 Issue 再經設計審、代碼審決定是否改九條或另立新條。探勘命令列沒有 Issue API、規則檔或設定寫入權限；報告連結不接一鍵展示頁。

## 拆增量

1. **合成資料與無模型基準**：先待 Phase 14 移除 runner AI 判斷開關的增量 3 合入 main，實作前核對 runner 已無 `--ai-judge` 正式入口、展示驅動不再組該參數且 F7 走規則路徑；AI 決策模組只供評估重播，本案不新增通往提案的路徑。再做固定三種子生成器、UTC 日桶與調整事件、探索／保留分組、真相清單、精確效果計算、封閉條件語彙及窮舉前 K。看任何結果前凍結前述完整參數清單；**只在建立或更換評估版本時**以三固定種子預檢資料雜湊、真相與誘餌的分母可達性、排除原因，並量每批送模型的完整提示 UTF-8 位元組數；任一批 `B>20480` 或達既有 48 KiB 界即預檢失敗，不進模型增量，預檢結果與版本修改對雙方的預期影響寫入版本理由，不因前 K 命中情況調排序。
2. **模型建議與驗證**：分析端窄函式經既有閘道呼叫；新呼叫者及花費上限、送出白名單；封閉 JSON 驗證、去重、同探索集重算與保留集檢驗。用假回覆驗證錯數、未知條件、過大輸出、缺錄製、即時未授權、花費帳忙碌均能明確失敗且不改正式決策。
3. **評估報告、錄製與 README**：同 K 窮舉對照、真相與誘餌指標、人讀報告、`--verify` 批次檢查；授權時才即時錄一批，驗過後入庫並讓 CI 重播；README 連報告，不改一鍵展示頁。

## 會卡住這個設計的既有程式

- 分析端禁匯入評估套件、評估端禁直接匯入模型閘道（兩份 `ruff.toml` 與 Phase 10 [S712]）；不能把生成器塞進分析端或在評估命令列直接開閘道。上節的「評估 → 分析端窄函式 → 閘道」需要只對新窄函式及評估入口更新 `tests/test_spawn_boundary.py` 的 `GATE_USERS`、`CALL_MODEL_USERS`、`SENDING_ENTRIES`、`CALLER_USERS`；還須逐一更新 `tests/eval/test_model_candidate.py` 的 `PHASE13_ALLOWED` 閉包白名單、`importers` 精確等式，以及 `judge_importers`／`judge_senders` 對應的新窄函式路徑等式，不把整個套件放行。
- `Caller` 不是任意字串，模型請求與閘道都驗列舉；新 `RULE_MINING = "rule_mining"` 必須連同 [S1103] 的全體成員精確等式與 [S1134] 的 `CAPPED_CALLERS` 精確等式一起改，並測其併行預留；`tests/test_spawn_boundary.py` 的 `CALLER_USERS` 鍵及值→成員名對照也須新增，值含分析端窄函式與用 `Caller.RULE_MINING` 重算歷史鍵的評估模組。批量探索固定 K=10、每批模型呼叫一次、上述提示／輸出界限；任何超限整批拒跑，不開分塊錄製鍵。
- Phase 13 錄製鍵依提示位元組與模型等內容產生，不能沿用 Phase 13 調查的錄製檔；新批次須獨立目錄及名稱。重錄不能覆蓋舊批次，報告雜湊要和錄製輸入版本對上。
- `rtb.eval` 的現有生成器針對 72 筆決策案例，沒有數週的歷史資料；DSP 的日桶及調整紀錄在另一行程，評估套件不准匯入 `rtb.dsp`。本案需在評估層建純離線歷史型別與自足的重產檢查，並以測試對齊 UTC 日期與整數分語意。
- Phase 14 在另一分支持續，這個 main 基底的 `src/rtb/domain/nine_rules.py` 已有九條純函式，但既有 `--ai-judge` 路徑仍可送提案；原 Phase 14 增量 3 文字也保留 AI 提案經規則否決。依本輪代使用者裁定，該增量 3 要移除 runner 的 AI 判斷開關，AI 決策模組只留評估重播；只有含此移除的變更合入 main，並核對 runner 無 AI 判斷開關、展示驅動不再組參數且 F7 走規則路徑，Phase 15 才能開工。當時再對照 main 的九條、模型入口與邊界測試，衝突先折回本計劃。

## 要改寫的既有合約

- Phase 13 [S1100] 的「准匯入模型閘道的分析端模組只有 AI 決策、runner、模型說明」須**窄增**離線探勘模型函式那一支；流程推進、正式規則與 DSP 用戶端的閉包仍不得碰模型。實作時回原計劃與 [[Systems/模型用戶端]] 記新入口的責任，不能只改測試名單。
- Phase 11B [S918] 的評估套件匯入閉包寫死 Phase 13 模組清單，需明加本案的純生成／核對／報告與分析端窄模型函式；保留評估不被其他套件匯入的 Phase 10 [S712]。新送出入口與呼叫者另更新 `tests/test_spawn_boundary.py` 的名單，既有 [S917]「只有模型用戶端啟動子行程」不放寬。
- Phase 13 [S1103] 的 `Caller` 封閉列舉仍維持封閉，但 `tests/model/test_shared_entry.py` 寫死的全體成員值等式須精確新增 `RULE_MINING` 的值；[S1134] 的 `CAPPED_CALLERS` 測試等式須精確新增此成員，並確認歷史帳列在回退後仍計上限。兩條舊合約的測試綁定要同步改，不能只修改新測試。
- Phase 11B 的 S918 另一組實際機檢在 `tests/eval/test_model_candidate.py`：`PHASE13_ALLOWED`、`importers == {...}` 與 `judge_importers`／`judge_senders` 精確等式各自明加新評估模組及分析端窄函式；確切名單依實作模組定名後逐項凍結，仍要求只有窄入口能送出。
- Phase 11B [S902]／[S903] 的既有計入上限者語意不改；新 `RULE_MINING` 加入花費帳的寫死集合，須測併行預留也計入上限，避免因 Phase 13 的三個豁免角色而把探索呼叫誤列免上限。Phase 13 錄製／即時選擇及批次目錄檢查沿用，不改其展示決策或錄製鍵。
- Phase 10／13 合成集「不給正式品質或統計保證」的合約保持；本案另作規則發現評估，不能讓真模式召回被當成 Phase 10 的已驗證清單，也不能把本案結果改寫成 Phase 13 AI 決策的採用結論。
- Phase 14 的九條順序與正式決策合約不改。本案只提出可開 Issue 的候選；若後續人裁採用，另開 Issue 與設計審決定是否改某一條、增第十條、調整政策版本與回退，Phase 15 的報告不能先替正式政策宣布生效。

## 合約候選

- 離線探勘輸入與輸出不得被分析行程、收件口或執行端當成決策或寫入參數；新模型入口與呼叫者須受既有送出邊界測試保護。候選待實作時依實際模組的 Systems 家、測試與獨立審計決定是否升成正式不變合約，本計劃不先蓋章。
- 合成評估集的真相標籤與誘餌標籤只給評估器，不能進提示；一條建議只有在固定語彙合法、同探索集支持／反例筆數重算一致、且可配對與兩側相異廣告數均達下限後才可列為有效建議。候選待測試確認後再綁，不把模型自報數字當合約證據。

## 驗收條款

- [S1501] 當用相同生成版本、固定三種子 `15001`／`15002`／`15003` 與固定時鐘重產數百支廣告的數週歷史時，生成器應得到各批相同逐日 UTC 桶、調整紀錄、真相清單與資料雜湊；金額以整數分保存且資料自洽，任一批不得換種子。[test:test_rule_mining_history_is_reproducible_and_consistent]
- [S1502] 當加額事件的 D±三日不完整、有缺值或重疊調整、找不到同側未用且**整期從未加額**的對照、或轉換率分母為零時，評估器應排除並逐原因計數；效果固定用 `(加額後率−加額前率)−(對照後率−對照前率)` 的精確分數，不讀捨入值。例如對照 C 於 D+2 曾加額，即使 D 日未加額也不得入池。[test:test_rule_mining_effects_use_complete_utc_days_and_exact_ratios]
- [S1503] 當模型提示由探索集建立時，彙總器應只送達**20 個有方向配對且其中兩側各 20 支相異廣告**的完整封閉條件表與分群數字，兩側相異廣告只數有方向配對；比例沿用 `rtb.domain.metrics.percent_text` 等既有函式的一位小數格式（以程式碼為準），差值用精確分數四位小數 half-even 捨入，精確分數只留程式端；不送保留側、逐日列、識別、時間戳或真相標籤。`B≤20480`、`B<48×1024` 且預留額在剩餘上限內才可單次呼叫，否則換版本或拒跑，不得分塊／截列。例如 `B=20481` 須拒跑，`B=20480` 且其他閘皆過才可呼叫。[test:test_rule_mining_prompt_contains_only_bounded_aggregates]
- [S1504] 當回覆有重複 JSON 鍵、任一物件層未知鍵、`NaN`、`1e400` 等非有限數、超 K、實際 JSON 逸出後超 6000 UTF-8 位元組、超長整數解析 `ValueError` 或錯版本時，解析器應整份拒絕；單條布林或有限浮點筆數、未知／不屬欄位的門檻、三子句、說明超過 80 字／240 位元組、相反方向或同鍵重複候選則丟該條、記原因並計 AI 誤報。例如 `support:1e400`、4301 位整數解析錯與子句多一個 `rationale` 都整份拒絕；`support:true`、未知 `raise_pct:band_9` 只丟該條並計誤報。[test:test_rule_mining_rejects_out_of_vocabulary_suggestions]
- [S1505] 當模型所報支持或反例與同一探索集重算不符、或重算不足 20 個有方向配對及其中兩側各 20 支相異廣告時，核對器應把該條目列「無效提交」、排除有效清單，並列入 AI 同名額誤報分子與分母；有效條目報告只用程式重算數字，不顯示原信心說明。例如 19 平手＋1 正差、模型報 1/0 雖相符仍無效。[test:test_rule_mining_recounts_every_suggestion_before_reporting]
- [S1506] 當窮舉與模型比較時，評估器應用同一條件全集、探索集、下限與 K，報三固定種子的首次成功入庫批次、窮舉前 K 的真模式召回及以 AI 有效建議數 n 取窮舉前 n 的同名額精確度／誤報欄；兩邊先取定名單再過保留側，不遞補；召回與誤報在保留前後均列分子分母，AI 無效提交算 AI 誤報，相關誘餌若保留側也成立但無獨立效果則另列「觀察成立的相關規律」及白話說明、不進召回、誤報分子或精確度分母，純噪音與探索偶合仍計誤報。n=0 或 n>0 但任一方同名額可計分母為零，該批均判「窮舉勝」、展示精確度寫未量；例如窮舉前 1 全是相關誘餌而 AI 1 條真模式，或保留後 AI 全被刷掉，均照此判。AI 2 條有效真模式、6 條未知門檻、2 條反方向提交時，對照窮舉前 2 比精確度、前 K 比召回，AI 仍列 8 筆無效提交及 8/10 誤報。[test:test_rule_mining_compares_ai_with_the_same_exhaustive_search]
- [S1507] 當離線入口預設重播、錄製缺漏、或即時前置條件不齊時，模型函式應經既有閘道停在錄製／明確失敗且留下原因；只有明確授權的即時模式可向 Anthropic 送合成彙總，且 `RULE_MINING` 計入既有花費上限與帳列。[test:test_rule_mining_live_calls_require_the_gateway_and_count_toward_caps]
- [S1508] 當錄製批次待入庫或 CI 重播時，驗收器應要求同批、有效錄製、失敗類零份、缺錄製零筆，且**唯一模型呼叫的結果為 `outcome=ok`**；目錄錄製鍵集合須恰等於依當次資料／提示／模型／輸出上限重算的預期集合、每份 `caller=RULE_MINING`。批次清單與報告列三個固定種子、資料雜湊、評估／提示版本、預期鍵、錄製鍵、各自展示編號、錄製時間與所有嘗試供機檢；每種子只准第一次成功錄製入庫，成功後不得同版本替換，換版須記理由；逾時、額度、超支或暫時性錯誤寫「呼叫失敗」、不入庫、不計 RETIRE-IF，同種子可用新展示編號重錄但不可換種子。例如 `outcome=timeout` 即使既有批次檢查通過仍拒入庫；CI 不呼叫即時模型。[test:test_rule_mining_recordings_verify_before_check_in]
- [S1509] 當報告交給人讀時，報告主體應逐條列白話條件、方向、程式重算的支持與反例、樣本與保留集結果、是否在基準前 K；不得顯示原始信心說明，並標合成資料、錄製來源與估算花費；README 應連到 `governance/eval/` 下的 `phase15-rule-mining.md`，不加入一鍵展示頁。例如說明含偽造 Markdown 標題，報告仍無該文字。[test:test_rule_mining_report_is_reviewable_and_linked_from_readme]
- [S1510] 當人尚未確認並開 Issue 時，探勘命令列與其匯入閉包應沒有正式決策、提案、DSP 寫入或 Issue API 的呼叫；後續採用須另走設計審及代碼審。[test:test_rule_mining_cannot_write_policy_proposals_or_issues]
- [S1511] 當某條件有 21 個有方向配對但只用 19 支對照、探索側只找得到保留側對照，或對照廣告在觀察期曾加額時，該條件不得進模型／基準候選；每條件依固定 `(D,加額編號,committed_at,操作識別碼)` 次序搶用對照，對照按編號升序取未用者，星期條件仍須兩側各 20 支相異廣告且報日期數。例如 21 對共用 1 支 C 應不合格。[test:test_rule_mining_pairs_stay_within_split_and_use_distinct_ads]
- [S1512] 當差值 = 0 時，彙總器應把平手加一，兩方向支持／反例與有方向數均不加；`正+負+平手=總配對`、`正+負=有方向配對` 且兩者對得上重算。平均差值分母固定為有方向配對數。例如 12 正、8 負、1 平手時改善報 12/8、方向數 20；19 平手＋1 正差方向數 1，不達下限。[test:test_rule_mining_ties_are_neutral_in_summary_and_recount]
- [S1513] 當有效候選在保留側只有 19 個有方向配對、或 20 個方向配對但支持 11/20、或方向化平均差值 ≤0 時，應判不保留並列原因；須至少 20 個方向配對且其中兩側各 20 支相異廣告、支持比例 ≥3/5 且方向化平均差值 >0 才可保留。例如 19 平手＋1 正差即使總配對 20 仍不保留；12/20 且平均差值正則可保留。[test:test_rule_mining_holdout_rule_is_frozen_and_recounted]
- [S1514] 當提示表過大時，命令列應只在換評估版本時縮語彙再建立完整表，仍超限就拒跑，呼叫始終至多一次；以 repo 當時 `modelcore` 預留函式重算的結果須 ≤ 每展示及每月剩餘上限，不能依寫死金額放行。輸出上限 6144 token、回覆以實際 JSON 逸出後至多 6000 UTF-8 位元組，提示要求 UTF-8 原字且不用 `\uXXXX`、每條說明至多 80 字／240 位元組，十條填滿須在輸出上限內；60 秒逾時後記呼叫失敗、整筆預留入帳；每個種子與每次重錄用新 `phase15-seed-<種子>-<序號>` 展示編號，不在同編號重試，月上限仍與其他受限呼叫者共用。例如展示已用額使重算預留超上限 → 拒絕即時呼叫。[test:test_rule_mining_single_call_respects_reservation]
- [S1515] 當基準排序時，應按支持比例的 Wilson 95% 下界（直接呼叫 `rtb.eval.scoring.wilson_lower()`，使用既有 `Z_95`）、方向化精確平均差值、有方向配對數、條件鍵與方向的順序合併兩方向並取至多 K；同鍵兩方向只留較高者，不能因結果改參數。例如 17/21 與 70/100 須按 Wilson 下界而非原始比例決定先後。[test:test_rule_mining_exhaustive_order_is_frozen_and_symmetric]
- [S1516] 當 AI 只回 2 條、基準有 10 條時，報告應同列 2 與 10、各指標分子分母、逐條重疊、AI 獨有數及「AI 清單是否完全落在窮舉前 K」；若兩條都在基準內，固定寫「程式掃描已找到 AI 清單中的全部條件」。[test:test_rule_mining_report_discloses_candidate_counts_and_overlap]
- [S1517] 當停用模型段或回退增量 2／3 時，應只撤命令列模型入口、分析端窄函式、`GATE_USERS`／`CALL_MODEL_USERS`／`SENDING_ENTRIES` 新增項、[S918] 新窄函式的 importer／sender 精確等式與報告連結；保留 `Caller.RULE_MINING = "rule_mining"`、`CAPPED_CALLERS` 成員、`CALLER_USERS["RULE_MINING"]` 鍵及指向歷史驗證模組的值、值→成員名對照、[S918] 歷史驗證等式、歷史帳列與錄製目錄，並維持 `CALLER_USERS` 鍵集合等於 Caller 成員名集合；CI 改驗歷史錄製仍可讀且其花費仍計月上限。例如歷史驗證模組仍用 `Caller.RULE_MINING` 重算錄製鍵，`CALLER_USERS["RULE_MINING"]` 必須含該模組而非空集合。[test:test_rule_mining_retirement_preserves_recordings_and_caps]
- [S1518] 當 Phase 14 尚未把**移除 runner AI 判斷開關**的增量 3 合入 main，或實作前核對 runner 仍可用 AI 判斷開關送提案、展示驅動仍組 `--ai-judge`、F7 未能走規則路徑三者任一成立時，實作者應暫緩本案；合入且核對 runner 無開關、展示驅動不再組參數且 F7 重跑走規則路徑後，AI 決策模組只供評估重播，正式流程無 AI 通往提案入口，探勘閉包仍不可達提案／DSP，[S1103]、[S1134]、[S918] 精確集合測試隨新列舉與窄入口同步更新。例如 runner 不接受 `--ai-judge` 但展示驅動仍傳該參數、F7 啟動失敗，也不得開工。[test:test_rule_mining_waits_for_phase14_and_keeps_import_boundaries]
- [S1519] 當建立或更換評估版本時，預檢應只用固定三種子 `15001`、`15002`、`15003`，逐批記真模式／誘餌可達分母與**完整**系統提示加彙總表的 UTF-8 位元組數到版本理由；任一批超 20480 位元組即阻止進模型增量，不得以換種子或截列通過，報告須列全部三批。例如第三批完整表 20481 位元組，即使前兩批較小仍失敗。[test:test_rule_mining_preflight_checks_all_fixed_seed_prompts]
- [S1520] 當同一組歷史重跑時，評估器應依廣告編號 SHA-256 排序固定 50/50 切側、只取整期未加額對照、以前三日花費整數分的 `<10000`／`10000–49999`／`≥50000` 桶及語彙轉換率區間配對，按固定加額事件鍵與對照編號順序決定搶用；任何一項改動都須換評估版本並記可達性預檢。例如兩個同日同桶加額事件搶一支 C，鍵較前者拿到 C，重跑結果相同。[test:test_rule_mining_pairing_parameters_are_frozen]

## 實務隱患

- **金流：已排除直接預算金流，未排除模型用量。** 探勘不建提案、不更新 DSP 預算，但即時呼叫會消耗訂閱用量，花費帳記估算且新呼叫者受既有上限；每批固定提示大小、K、6144 輸出及一次呼叫。事件入口：`--verify` 觀測帳列超過預估、價目或輸出上限改動時，先重算預留上界再准即時錄製。
- **對外送出：未排除。** 明確授權即時模式會把合成資料的**彙總表**經既有後端送 Anthropic；欄位白名單、上限與提示測試擋原始列、識別碼、真相標籤及任何真實客戶資料。事件入口：資料來源改成真實歷史、提示欄位增加或模型後端變更時，先停即時模式並重審送出邊界。
- **不可逆：未排除既有外送與用量。** 已送出的合成彙總、已用的訂閱額度不能撤回；每種子第一次成功入庫後不得以同版本另批取代；要替換須換評估版本並記理由、保留舊批來源紀錄。此路徑沒有自動開 Issue 或正式政策寫入。事件入口：批次驗收顯示送出欄位超白名單時，停止後續即時呼叫、保留批次證據並按送出邊界事故檢查。
- **守衛面：未排除。** 新 `Caller`、上限集合、閘道使用者白名單與評估閉包是需要窄改的守衛；同源合成規律、探索集重算與條件搜尋也可能讓模型看似很準。以邊界測試、對抗假回覆、保留集、誘餌與窮舉基準共同揭露；事件入口：每次改條件語彙、生成器、模型入口或錄製鍵，**或撤除模型呼叫路徑**時重跑 [S1501]–[S1518]；提示、彙總表或條件語彙的位元組一變，錄製鍵就失效，換評估版本並記理由後才可另錄入庫，舊批保留核對。

## 回退

- 增量 1 尚未接模型時，撤掉純離線生成器與基準；正式九條、既有評估集與決策行程不需回退。已發表的報告保留生成版本與雜湊，標為歷史合成結果。
- 增量 2 有錯時先停新命令列的即時入口，讓報告只列純基準並把 AI 欄寫「未量」；只移除命令列模型入口與分析端窄函式，收回 `GATE_USERS`、`CALL_MODEL_USERS`、`SENDING_ENTRIES` 的新增送出項及 [S918] 對新窄函式的 importer／sender 精確等式，**保留** `Caller.RULE_MINING`、`CAPPED_CALLERS` 成員，以及與 Caller 全體成員精確等式一致的 `CALLER_USERS["RULE_MINING"]` 鍵（值須含歷史驗證模組，不可置空）。值→成員名對照與 [S918] 歷史驗證等式仍保留；舊錄製仍由既有驗證器讀回，已記歷史列與外送事實保留且繼續計每展示／每月上限；將依賴模型重播的 CI 改為歷史錄製驗證，不能留下失效重播。
- 增量 3 的報告或錄製有錯，撤下 README 連結並標報告待重算，保留錄製目錄與批次清單供驗證器核對；用固定種子與當時提示版本重算，若已有成功入庫批次，不得同版本另批取代；須換評估版本、記理由與新展示編號，舊批保留。若 `RETIRE-IF` 達成，停用模型呼叫及撤報告連結、保留純程式報告與可讀錄製；同時改寫依賴模型入口的 CI 測試為歷史批次驗證，保留 Caller 與上限集合成員，不把既有用量從月帳抹掉。

## 待審問題

- 目前選「三完整日前後轉換率變化減去未加額對照的變化」當合成有效性操作定義；即使有對照，仍可能受選擇偏差影響。後續代碼審應檢查對照匹配欄位、同廣告重複加額及樣本排除是否使真模式或誘餌評分偏向任一方；若匹配不足，縮成「可觀察關聯」並改報告用詞，不宣稱加額造成效果。
- 固定三種子、門檻、20 有方向配對與兩側各 20 支相異廣告、前 K=10 均為評估版本的一部分；只在**建立或更換評估版本**時預檢每個真模式及誘餌的分母可達性與完整提示位元組數，結果記入版本理由。若不可達或提示超限，先記原因與對 AI、窮舉雙方的預期影響再換版本；三種子不得替換，不用探索正負差、基準排名或模型答案回頭挑較好看的版本。
- Phase 14 尚在另一分支；**移除 runner AI 判斷開關的增量 3 合入 main 是實作前置條件**。實作前核對 runner 已無 AI 判斷開關、展示驅動不再組該參數且 F7 走規則路徑、AI 決策模組只供評估重播、正式流程無 AI 通往提案入口，再對照九條、DSP 日桶與調整紀錄、Phase 13 評估報告及邊界測試；若其閘道／評估邊界與本設計牴觸，先折回本計劃再實作。

## 審計修正紀錄

- r3(2026-09-26,4 席,末輪):報 14 條/blocking 7 條；14 條全數折入。末輪折入未再經設計審，後續由代碼審把關；補零分母判勝、所有無效提交計誤報、首次成功錄製鎖定、展示額度、雙端前置條件及既有函式沿用。
- r3 席報告指標：`governance/review-reports/rtb-phase15ai找規則模式/` 的 `r3-鏡頭1.md`、`r3-鏡頭2.md`、`r3-架構對齊.md`、`r3-外家-codex.md`；逐條處置：`l1_1` 零分母窮舉勝 [S1506]；`l1_2` 全部無效提交計誤報 [S1504]／[S1506]；`l1_3` 首次成功錄製鎖定 [S1508]；`l1_4` 先取名單再篩選 [S1506]；`l1_5` 專屬展示編號 [S1514]；`l1_6` 門檻全名、未改善代碼與有方向相異廣告 [S1503]／[S1504]；`l2_1` 6144 預留餘裕與失敗扣帳 [S1514]；`l2_2` 回退保留值對照及 S918 [S1517]；`l2_3` UTF-8 原字與逸出後界 [S1504]；`l2_4` 超長整數整份拒絕 [S1504]；`a_1` 既有 Wilson 函式 [S1515]；`a_2` 既有比例格式化 [S1503]；`x_1` runner 與展示驅動兩端前置條件 [S1518]；`x_2` 相關誘餌零分母 [S1506]。以上依「代使用者裁定(2026-09-26)」及各席可重現反例折入。
- r2(2026-09-26,4 席):報 16 條/blocking 8 條；16 條全數折入，架構對齊 clean；收緊有方向樣本、同名額比較、配對凍結、輸入輸出及成功入庫，並把 Phase 14 移除 AI 判斷開關列為前置條件。
- r2 席報告指標：`governance/review-reports/rtb-phase15ai找規則模式/` 的 `r2-鏡頭1.md`、`r2-鏡頭2.md`、`r2-架構對齊.md`、`r2-外家-codex.md`；逐條處置：`l1_1` 有方向三個 20 與 19 平手反例 [S1512]／[S1513]；`l1_2` 等名額及無效提交 [S1506]；`l1_3` 凍結配對／切分與版本預檢 [S1520]；`l1_4` Wilson 下界 [S1515]；`l1_5` 固定三種子 [S1519]；`l1_6` 整期未加額對照池 [S1502]；`l2_1` 固定精度彙總與完整表大小閘 [S1503]／[S1519]；`l2_2` 60 秒、成功入庫與失敗批次排除 [S1508]；`l2_3` 80 字及 6144 輸出預算 [S1514]；`l2_4` 溢位非有限數與物件層未知鍵整答拒絕 [S1504]；`l2_5` 回退保留 `CALLER_USERS` 鍵 [S1517]；`l2_6` 絕對差及方向數平均 [S1502]／[S1512]；`l2_7` 20481 位元組反例與函式重算預留 [S1503]／[S1514]；`x_1` 移除 runner AI 判斷開關後才實作 [S1518]；`x_2` 有方向下限 [S1513]；`x_3` 相關誘餌另類揭露 [S1506]。以上均依「代使用者裁定(2026-09-26)」，理由見裁定節及各條款。
- r1(2026-09-26,7 席):報 31 條/blocking 24 條；31 條全數折入，含 1 條外家 blocker 以 Phase 14 增量 3 前置條件處理，新增樣本、公平比較、解析、帳本回退與驗收機檢。
- r1 席報告指標：`governance/review-reports/rtb-phase15ai找規則模式/` 的 `r1-鏡頭1.md`、`r1-鏡頭2.md`、`r1-鏡頭3.md`、`r1-鏡頭4.md`、`r1-鏡頭5.md`、`r1-架構對齊.md`、`r1-外家-codex.md`；下列 ID 逐條記處置。
  - `l1_1`：前 K 非真模式全計誤報，誘餌另列，見指標定義與 [S1506]。
  - `l1_2`：凍結保留側 20／3/5／同方向門檻，過濾前後都報，見 [S1513]。
  - `l1_3`：配對限定同側，見 [S1511]。
  - `l1_4`：對照廣告每條件不放回、兩側各 20 支、星期類列日期數，見 [S1511]。
  - `l1_5`：取消分塊，只許一次完整提示與至多 K，見 [S1514]。
  - `l1_6`：排序鍵、兩方向合併與凍結時點寫死，見 [S1515]。
  - `l1_7`：零差另列平手，支持及反例都不計，見 [S1512]。
  - `l1_8`：報告明示切點對齊時召回為上界，見報告段及 [S1509]。
  - `l1_9`：彙總先篩下限，機械核對只量抄寫一致；三批改為不同種子，見 RETIRE-IF。
  - `l2_1`：當時按 repo 預留算式定 20 KiB／3072 上限與約 $0.294 餘裕；r3 升至 6144 後重算為約 $0.058 餘裕，見 [S1514]。
  - `l2_2`：單次模型呼叫、不分塊，拒絕裝不下的完整表，見 [S1503]。
  - `l2_3`：模型重算未達三個 20 下限即無效，見 [S1505]。
  - `l2_4`：正／負／平手欄與重算同口徑，見 [S1512]。
  - `l2_5`：原信心說明只存原始回覆，不進人讀報告，見 [S1509]。
  - `l2_6`：回退保留 Caller、上限集合與錄製驗證，見 [S1517]。
  - `l2_7`：整答拒重複鍵／未知鍵／非有限數，單條丟錯型別及非法子句並記原因，見 [S1504]。
  - `l2_8`：補 [S1134] 精確等式改寫，見既有合約及 [S1518]。
  - `l2_9`：定欄位條件代碼與所屬門檻代碼、正規化鍵及 JSON 例，見語彙／解析段。
  - `l2_10`：S1508 改為預期錄製鍵集合、批次清單與報告版本欄的機檢。
  - `l3_1`：新增重疊、AI 獨有與全落入基準前 K 的固定揭露，見 [S1516]。
  - `l3_2`：同表列雙方實際候選數與每個分母，見 [S1506]／[S1516]。
  - `l3_3`：防調參兩方向對稱，讀任何結果前凍結，見 [S1515]。
  - `l4_1`：點名 `PHASE13_ALLOWED`、`importers`、`judge_importers`／`judge_senders` 各組等式，見 [S1518]。
  - `l4_2`：補 [S1103]、[S1134] 的成員及上限集合精確等式，見既有合約。
  - `l4_3`：回退仍能讀舊錄製，舊帳仍計上限，見 [S1517]。
  - `l5_1`：停模型時將 CI 模型重播改歷史批次驗證，錄製目錄保留，見回退及 [S1517]。
  - `a_1`：報告改置既有 `governance/eval/`，見 [S1509]。
  - `x_1`：舊 AI 提案路徑由 Phase 14 增量 3 合入先處理，本案不加提案路徑，見現況、拆增量及 [S1518]。
  - `x_2`：同側配對，見 [S1511]。
  - `x_3`：單次完整提示，見 [S1514]。
  - `x_4`：回退保留 Caller 以維持錄製可讀，見 [S1517]。
- 2026-09-26 r1 折入後檢查：`spec-gate` 判高風險、18 條句式與綁定通過；新條款測試尚未實作，相依回歸執行 0 支。最後治理留痕仍因家目錄寫入鎖等滿 60 秒失敗，非條款失敗。本沙箱只准改計劃檔，未繞鎖或寫治理帳；事件入口：在可用圖譜寫入鎖的環境，進實作前重跑完整 `lumos spec-gate RTB_Phase15AI找規則模式_計劃`。`fold-check` 無 flag、`refcheck --repo /Users/enzo/rtb-phase15` 15 個引用全對、`lint` 0 問題。
- 2026-09-26 初稿：只寫計劃，未改程式、未呼叫模型、未開 Issue、未跑設計審；以 `71b04d5` 程式前掃與已在 main 的 Phase 10／13／14 計劃文字為依據。
- 2026-09-26 檢查修正：`pitfalls --check`、`refcheck`、`lint` 均通過；`spec-gate --no-run` 的高風險分類、10 條句式與綁定通過。完整 `spec-gate` 初跑有四支既有相依測試紅，抽查 `test_fields_outside_the_allowlist_never_reach_the_evidence` 證實是此沙箱 `socket.bind` 的 `PermissionError`；調整 `lands_in` 後相依回歸已是 0 支，完整命令的判門也通過，但最後留痕因圖譜寫入鎖在不可寫的家目錄等滿 60 秒而未完成。只准改本計劃檔，本輪不另寫治理帳；事件入口：進實作前在允許本機綁埠且圖譜寫入鎖可用的環境重跑完整 `lumos spec-gate RTB_Phase15AI找規則模式_計劃`。`lumos doctor` 非嚴格模式結束碼 0，指出計劃中預定新建的 Systems 家仍未存在；本輪移除該未存在的 wikilink，實作建檔時同步建家。

## src/rtb/eval/rule_mining_vocab.py @ d336754
"""規則模式探索的封閉條件語彙與評估版本常數(Phase 15 增量 1,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈封閉條件語彙、彙總與窮舉基準〉)。

- 這支檔是「評估版本」的單一出處:三個固定種子、生成版本、K、樣本下限、保留比例、切點、切分與配對
  規則、搶用次序、效果與平均公式、排序鍵、格式化與輸入輸出上限都寫死在這裡,`version_params()` 把它們
  列成一份可雜湊的清單。看任何基準或模型結果前凍結;要改先換 `EVAL_VERSION` 並記理由與可達性預檢
  ([S1515] [S1519] [S1520])。
- 條件只有四個欄位、每欄固定幾個區間;一條條件是一或兩個**不同**欄位的子句 AND。子句鍵是
  `(條件代碼, 門檻代碼全名)`,門檻代碼一律寫成「欄位:區間」(如 `raise_pct:band_2`),不能拿別欄的門檻
  套用;正規化鍵是按子句排序後的元組再接方向。九條正式規則的詞彙不擴充成這裡的語彙。
- 純函式、只用標準函式庫與領域層的精確比率;不匯入模型用戶端、模型閘道或 DSP。
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date
from fractions import Fraction
from itertools import combinations
from types import MappingProxyType
from typing import Any

EVAL_VERSION = "phase15-rule-mining-v1"
GENERATOR_VERSION = "rule-mining-history-1"
SEEDS = (15001, 15002, 15003)  # 固定,任一批不得替換;報告全列

K = 10
MIN_DIRECTED = 20  # 有方向配對(正差 + 負差)下限
MIN_DISTINCT_ADS = 20  # 有方向配對裡相異加額廣告、相異對照廣告各自的下限
HOLDOUT_SUPPORT = Fraction(3, 5)
WINDOW_DAYS = 3  # D-3..D-1 與 D+1..D+3,D 本身不算

PROMPT_BYTES_LIMIT = 20480  # 本案:系統提示加使用者內容的 UTF-8 位元組
GATEWAY_PROMPT_BYTES = 48 * 1024  # 既有模型用戶端的上限(測試核對 modelcore.MAX_PROMPT_BYTES)
MAX_OUTPUT_TOKENS = 6144
REPLY_BYTES_LIMIT = 6000
NOTE_CHARS = 80
NOTE_BYTES = 240
CALL_TIMEOUT_SECONDS = 60

IMPROVE, NOT_IMPROVE = "improve", "not_improve"
DIRECTIONS = (IMPROVE, NOT_IMPROVE)

DAY_TYPE, PRE_CVR, RAISE_PCT, SPEND_RATIO = "day_type", "pre_cvr", "raise_pct", "spend_ratio"
FIELDS = (DAY_TYPE, PRE_CVR, RAISE_PCT, SPEND_RATIO)  # 已按代碼排序
WEEKDAY, WEEKEND = "day_type:weekday", "day_type:weekend"
# 數值欄的切點(下界含、上界不含;比率刻度),依序切出 band_1 / band_2 / band_3。百分比以整數基點寫
CUTS: Mapping[str, tuple[Fraction, Fraction]] = MappingProxyType({
    PRE_CVR: (Fraction(200, 10000), Fraction(500, 10000)),  # 調整前三日轉換率 <2%、2-5%、≥5%
    RAISE_PCT: (Fraction(2000, 10000), Fraction(5000, 10000)),  # 加額幅度 <20%、20-50%、≥50%
    SPEND_RATIO: (Fraction(6000, 10000), Fraction(9000, 10000)),  # 前三日花費/(3 x 原日預算)
})
THRESHOLDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    DAY_TYPE: (WEEKDAY, WEEKEND),
    **{name: tuple(f"{name}:band_{i}" for i in (1, 2, 3)) for name in (PRE_CVR, RAISE_PCT,
                                                                       SPEND_RATIO)},
})
# 投放規模桶:調整前三日花費整數分
SCALE_CUTS_CENTS = (10000, 50000)
SCALE_BUCKETS = ("lt_10000", "10000_49999", "ge_50000")
CODE = re.compile(r"[a-z0-9_:]{1,24}")

Clause = tuple[str, str]
ConditionKey = tuple[Clause, ...]
NormalizedKey = tuple[ConditionKey, str]


class VocabularyError(ValueError):
    """條件不在封閉語彙內;reason 是固定的原因代碼(模型回覆解析時逐條記)。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def band(field: str, value: Fraction) -> str:
    """數值欄的門檻代碼全名。"""
    low, high = CUTS[field]
    index = 1 if value < low else 2 if value < high else 3
    return f"{field}:band_{index}"


def day_type(day: date) -> str:
    """加額日(UTC)是平日或週末;合成資料沒有國定假日,週六日即假日。"""
    return WEEKEND if day.weekday() >= 5 else WEEKDAY  # 週六是 5


def scale_bucket(spend_cents: int) -> str:
    low, high = SCALE_CUTS_CENTS
    return SCALE_BUCKETS[0] if spend_cents < low else SCALE_BUCKETS[1] if (
        spend_cents < high) else SCALE_BUCKETS[2]


def _clause(raw: object) -> Clause:
    if not (isinstance(raw, tuple) and len(raw) == 2):  # 子句是一對代碼
        raise VocabularyError("bad_clause")
    condition, threshold = raw
    if not (isinstance(condition, str) and isinstance(threshold, str)
            and CODE.fullmatch(condition) and CODE.fullmatch(threshold)):
        raise VocabularyError("bad_code")
    if condition not in THRESHOLDS:
        raise VocabularyError("unknown_condition")
    if threshold not in THRESHOLDS[condition]:
        raise VocabularyError("threshold_not_in_condition")
    return condition, threshold


def condition_key(clauses: Iterable[object]) -> ConditionKey:
    """一或兩個不同欄位的子句 → 排序後的條件鍵;語彙外、同欄重複或矛盾、零或三個以上子句都丟錯。"""
    parsed = [_clause(raw) for raw in clauses]
    if not 1 <= len(parsed) <= 2:  # 至多兩個不同欄位
        raise VocabularyError("clause_count")
    if len({condition for condition, _ in parsed}) != len(parsed):
        raise VocabularyError("duplicate_condition")
    return tuple(sorted(parsed))


def normalized_key(clauses: Iterable[object], direction: object) -> NormalizedKey:
    key = condition_key(clauses)
    if direction not in DIRECTIONS:
        raise VocabularyError("unknown_direction")
    assert isinstance(direction, str)  # noqa: S101 - 上一行已確認
    return key, direction


def all_conditions() -> tuple[ConditionKey, ...]:
    """封閉條件全集(單欄 + 兩個不同欄的組合),按正規化鍵升序;模型可選與窮舉掃的是同一份。"""
    singles: list[ConditionKey] = [((name, code),) for name in FIELDS
                                   for code in THRESHOLDS[name]]
    pairs: list[ConditionKey] = [((a, x), (b, y)) for a, b in combinations(FIELDS, 2)
             for x in THRESHOLDS[a] for y in THRESHOLDS[b]]
    return tuple(sorted(singles + pairs))


def key_text(key: ConditionKey) -> str:
    """彙總表與報告的條件寫法:門檻代碼全名以 & 相連(欄位代碼就是冒號前那段)。"""
    return "&".join(threshold for _, threshold in key)


def version_params() -> dict[str, Any]:
    """評估版本的完整參數清單(JSON 可序列化);雜湊由提示模組連同系統提示一起算。"""
    return {
        "eval_version": EVAL_VERSION, "generator_version": GENERATOR_VERSION,
        "seeds": list(SEEDS), "k": K, "min_directed": MIN_DIRECTED,
        "min_distinct_ads": MIN_DISTINCT_ADS, "holdout_support": str(HOLDOUT_SUPPORT),
        "window_days": WINDOW_DAYS,
        "split": "sha256(ad_id utf-8) 升序,同雜湊再比編號位元組;前 floor(N/2) 探索,其餘保留",
        "control_pool": "同側、整個觀察期從未加額的廣告;每條件每支至多一次",
        "pair_fields": ["side", "utc_day", "pre_cvr_band", "scale_bucket"],
        "scale_cuts_cents": list(SCALE_CUTS_CENTS), "scale_buckets": list(SCALE_BUCKETS),
        "cuts": {name: [str(c) for c in CUTS[name]] for name in sorted(CUTS)},
        "thresholds": {name: list(THRESHOLDS[name]) for name in FIELDS},
        "day_type": "UTC 星期六、日為 weekend,其餘 weekday",
        "event_order": ["utc_day", "raised_ad_id_utf8", "committed_at", "op_id"],
        "control_order": "ad_id utf-8 位元組升序,取第一支未用者",
        "exclusions": ["incomplete_window", "overlapping_adjustment", "anomalous_data",
                       "missing_value", "zero_denominator", "no_control"],  # 判斷先後
        "effect": "(加額後三日率-加額前三日率)-(對照後三日率-對照前三日率),"
                  "率=轉換/點擊,rtb.domain.metrics.exact_ratio 精確分數",
        "mean": "有方向差值合計 / 有方向配對數;平手不計支持與反例",
        "distinct_counts": "兩側相異廣告數與相異 UTC 日期數只數有方向配對",
        "ranking": ["wilson_lower(支持, 支持+反例) 降序", "方向化精確平均差值降序",
                    "有方向配對數降序", "正規化條件鍵升序", "方向代碼升序"],
        "wilson": "rtb.eval.scoring.wilson_lower,Z_95=1.959963984540054",
        "directions_merge": "同一條件兩方向只留排序較高者,再取全域前 K",
        "formats": {"percent": "rtb.domain.metrics.percent_text(一位小數 half-even)",
                    "diff": "百分點四位小數,精確分數 half-even"},
        "prompt_bytes_limit": PROMPT_BYTES_LIMIT, "gateway_prompt_bytes": GATEWAY_PROMPT_BYTES,
        "max_output_tokens": MAX_OUTPUT_TOKENS, "reply_bytes_limit": REPLY_BYTES_LIMIT,
        "note_chars": NOTE_CHARS, "note_bytes": NOTE_BYTES,
        "call_timeout_seconds": CALL_TIMEOUT_SECONDS,
    }

## src/rtb/eval/rule_mining_history.py @ d336754
"""規則模式探索的合成歷史:資料型別、固定種子生成器、評估集文字與雜湊、探索/保留切分、埋入真相清單
(Phase 15 增量 1,計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉)。

- 只收三個固定種子(`rule_mining_vocab.SEEDS`);720 支虛構廣告、35 個連續 UTC 日,每支每天一個
  完整日桶(曝光、點擊、轉換、花費、營收,金額整數分),加額廣告另有含調整前後日預算與 `committed_at`
  的操作。計數照漏斗造(轉換 ≤ 點擊 ≤ 曝光)、當天花費不超過當天日預算;少數廣告有一天 `no_data`。
- 真相清單 `TRUTH` 只給評估器:生成器照它在加額後三天改轉換率,條件的欄位值用評估器同一支語彙函式
  從已生成的前三日資料算(真模式與切點精確對齊,所以召回只是可精確表示時的上界)。誘餌兩類:只在
  探索側埋入的偶合、與真模式相關但本身沒有效果。真相不進評估集文字,也不能進模型提示。
- 另刻意放少量會被排除的事件(前三日或後三日超出觀察期、視窗內另有調整、視窗碰到 no_data),讓排除
  計數有東西可數。
- `render` 產出固定格式文字,`data_sha256` 是它的雜湊;三批的預期雜湊寫死在 `EXPECTED_DATA_SHA256`,
  改生成器就得連版本、雜湊一起換並記理由。合成資料只證流程可運作,不給統計或因果保證。
"""

import hashlib
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from types import MappingProxyType

from rtb.eval import rule_mining_vocab as v

START = date(2026, 8, 3)  # 週一
DAYS = 35
CLOCK = datetime(2026, 9, 7, tzinfo=UTC)  # 評估時鐘:最後一個 UTC 日結束
N_ADS = 720
RAISED_ADS = 360

TRUE_PATTERN = "true_pattern"
DECOY_EXPLORE_ONLY = "decoy_explore_only"
DECOY_CORRELATED = "decoy_correlated"
LIFT, DROP = 1.6, 0.5  # 真模式在加額後三天對轉換率的乘數

# 首次生成後寫死;生成器任何改動都會讓它對不上,須換生成版本與評估版本並記理由
EXPECTED_DATA_SHA256: Mapping[int, str] = MappingProxyType({
    15001: "f820e1a486d8b35d1d81b55c37ce6ed5bb96a9701b0d898020f559fd1adb9f98",
    15002: "2a9e4aac0f4ec0209bfa6e4f7124e5cefb8bcdb259d89886d0a3e856a5c0a9e3",
    15003: "ba7d6ebc8a3a8b4d9c79334a1e2ab5844842eca74ab2c7fdecec90a529c44a2f",
})


@dataclass(frozen=True)
class DayBucket:
    day: date  # UTC 日
    impressions: int | None
    clicks: int | None
    conversions: int | None
    spend_cents: int | None
    revenue_cents: int | None
    no_data: bool = False


@dataclass(frozen=True)
class Adjustment:
    op_id: str
    committed_at: datetime  # UTC
    budget_before_cents: int  # 日預算
    budget_after_cents: int


@dataclass(frozen=True)
class Ad:
    ad_id: str
    daily_budget_cents: int  # 觀察期第一天的日預算
    days: tuple[DayBucket, ...]
    adjustments: tuple[Adjustment, ...]


@dataclass(frozen=True)
class History:
    seed: int
    generator_version: str
    start: date
    day_count: int
    ads: tuple[Ad, ...]


@dataclass(frozen=True)
class TruthItem:
    kind: str
    clauses: v.ConditionKey
    direction: str
    note: str

    @property
    def key(self) -> v.NormalizedKey:
        return v.normalized_key(self.clauses, self.direction)


TRUTH = (
    TruthItem(TRUE_PATTERN, ((v.RAISE_PCT, "raise_pct:band_2"), (v.SPEND_RATIO,
                                                                "spend_ratio:band_3")),
              v.IMPROVE, "預算幾乎花滿的廣告中度加額,後三天轉換率變好"),
    TruthItem(TRUE_PATTERN, ((v.RAISE_PCT, "raise_pct:band_3"),), v.NOT_IMPROVE,
              "大幅加額買到較差的流量,後三天轉換率變差"),
    TruthItem(DECOY_EXPLORE_ONLY, ((v.DAY_TYPE, v.WEEKEND), (v.RAISE_PCT, "raise_pct:band_1")),
              v.IMPROVE, "只在探索側埋入的偶合:保留側沒有這個效果"),
    TruthItem(DECOY_CORRELATED, ((v.SPEND_RATIO, "spend_ratio:band_1"),), v.NOT_IMPROVE,
              "花費比低的廣告多半被大幅加額而看似變差;本身沒有獨立效果"),
)


@dataclass(frozen=True)
class Split:
    explore: frozenset[str]
    holdout: frozenset[str]


def split(ad_ids: Iterable[str]) -> Split:
    """按廣告編號 UTF-8 位元組的 SHA-256 升序(同雜湊再比編號位元組),前 floor(N/2) 支探索、其餘
    保留(奇數多出者進保留)。同一廣告的全部日期與對照資格只在一側。"""
    order = sorted(set(ad_ids), key=lambda a: (hashlib.sha256(a.encode()).digest(), a.encode()))
    half = len(order) // 2
    return Split(explore=frozenset(order[:half]), holdout=frozenset(order[half:]))


# ---- 生成器 ----
_CVR = (0.012, 0.033, 0.075)  # 轉換率等級:落在 pre_cvr 三個區間的中段
_UTIL = (0.45, 0.75, 0.96)  # 日預算花用比等級:落在 spend_ratio 三個區間的中段
_BUDGETS = ((1500, 2500), (8000, 16000), (40000, 80000))  # 日預算(分):三個規模桶
# 加額幅度(基點)各區間的候選值,離切點夠遠
_RAISE_BP = ((1000, 1200, 1500, 1800), (2500, 3000, 3500, 4000, 4500), (6000, 7500, 9000, 10000))
# 花用比等級 → 加額幅度區間的機率(低花用比多半被大幅加額:相關誘餌的來源)
_RAISE_BAND_WEIGHTS = ((15, 15, 70), (55, 30, 15), (40, 50, 10))
_EARLY, _LATE, _OVERLAP, _GAP = 0.04, 0.04, 0.06, 0.15  # 刻意放的排除事件與 no_data 比例
_LAST_FULL = DAYS - 1 - v.WINDOW_DAYS  # 後三日仍在觀察期內的最後一個加額日


@dataclass(frozen=True)
class _Profile:
    cvr: float
    util_tier: int
    util: float
    budget: int
    cpc: float
    ctr: float
    aov: int


def _profile(rng: random.Random) -> _Profile:
    cvr_tier, scale, util_tier = rng.randrange(3), rng.randrange(3), rng.randrange(3)
    low, high = _BUDGETS[scale]
    return _Profile(cvr=_CVR[cvr_tier], util_tier=util_tier, util=_UTIL[util_tier],
                    budget=rng.randint(low, high), cpc=rng.uniform(15, 40),
                    ctr=rng.uniform(0.01, 0.04), aov=rng.randint(2000, 8000))


def _raise_days(rng: random.Random) -> list[int]:
    first = rng.randint(0, 2) if rng.random() < _EARLY else rng.randint(3, 13)
    second = rng.randint(_LAST_FULL + 1, DAYS - 1) if rng.random() < _LATE else rng.randint(
        max(first, 3) + 8, _LAST_FULL)
    days = [first, second]
    if rng.random() < _OVERLAP and second + 2 < DAYS:
        days.append(second + 2)
    return days


def _raise_bp(rng: random.Random, util_tier: int) -> int:
    band_index = rng.choices((0, 1, 2), weights=_RAISE_BAND_WEIGHTS[util_tier])[0]
    return rng.choice(_RAISE_BP[band_index])


def _market(index: int, day: date) -> float:
    """全體廣告共用的轉換率起伏(對照組扣掉的就是它)。"""
    weekend = 0.05 if v.day_type(day) == v.WEEKEND else 0.0
    return 1 + 0.08 * math.sin(2 * math.pi * index / 14) + weekend


def _bucket(rng: random.Random, profile: _Profile, day: date, budget: int, factor: float,
            gap: bool) -> DayBucket:
    if gap:
        return DayBucket(day, None, None, None, None, None, no_data=True)
    spend = int(budget * min(1.0, profile.util * rng.uniform(0.97, 1.03)))
    clicks = max(1, round(spend / profile.cpc * rng.uniform(0.9, 1.1)))
    impressions = max(clicks, round(clicks / profile.ctr * rng.uniform(0.95, 1.05)))
    rate = min(0.5, profile.cvr * factor)
    mean = clicks * rate
    conversions = min(clicks, max(0, round(rng.gauss(mean, math.sqrt(mean * (1 - rate))))))
    revenue = round(conversions * profile.aov * rng.uniform(0.9, 1.1))
    return DayBucket(day, impressions, clicks, conversions, spend, revenue)


def _event_clauses(pre: Sequence[DayBucket], day: date, before: int, after: int
                   ) -> frozenset[v.Clause] | None:
    """加額事件四個欄位的值,跟評估器同一套語彙函式;前三日缺資料或沒點擊就不埋效果(事件會被排除)。"""
    if len(pre) < v.WINDOW_DAYS or any(b.no_data for b in pre):
        return None
    clicks = sum(b.clicks or 0 for b in pre)
    if clicks == 0:
        return None
    conversions = sum(b.conversions or 0 for b in pre)
    spend = sum(b.spend_cents or 0 for b in pre)
    return frozenset({
        (v.DAY_TYPE, v.day_type(day)),
        (v.PRE_CVR, v.band(v.PRE_CVR, Fraction(conversions, clicks))),
        (v.RAISE_PCT, v.band(v.RAISE_PCT, Fraction(after - before, before))),
        (v.SPEND_RATIO, v.band(v.SPEND_RATIO, Fraction(spend, v.WINDOW_DAYS * before))),
    })


def _effect(clauses: frozenset[v.Clause] | None, explore: bool) -> float:
    factor = 1.0
    if clauses is None:
        return factor
    for item in TRUTH:
        if not set(item.clauses) <= clauses:
            continue
        if item.kind == TRUE_PATTERN:
            factor *= LIFT if item.direction == v.IMPROVE else DROP
        elif item.kind == DECOY_EXPLORE_ONLY and explore:
            factor *= LIFT
    return factor


def _make_ad(rng: random.Random, ad_id: str, raised: bool, explore: bool) -> Ad:
    profile = _profile(rng)
    plan = {day: _raise_bp(rng, profile.util_tier) for day in _raise_days(rng)} if raised else {}
    gaps = {rng.randrange(DAYS)} if rng.random() < _GAP else set()
    budget, buckets, adjustments = profile.budget, list[DayBucket](), list[Adjustment]()
    lift: dict[int, float] = {}
    for index in range(DAYS):
        day = START + timedelta(days=index)
        if index in plan:
            after = budget + (budget * plan[index] + 5000) // 10000
            moment = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
                minutes=rng.randrange(60, 23 * 60))
            adjustments.append(Adjustment(f"{ad_id}-op{len(adjustments) + 1}", moment, budget,
                                          after))
            factor = _effect(_event_clauses(buckets[-v.WINDOW_DAYS:], day, budget, after), explore)
            for later in range(index + 1, index + 1 + v.WINDOW_DAYS):
                lift[later] = lift.get(later, 1.0) * factor
            budget = after
        buckets.append(_bucket(rng, profile, day, budget,
                               _market(index, day) * lift.get(index, 1.0), index in gaps))
    return Ad(ad_id, profile.budget, tuple(buckets), tuple(adjustments))


def generate(seed: int) -> History:
    """固定種子的合成歷史;只收 `SEEDS` 裡的三個種子。"""
    if seed not in v.SEEDS:
        raise ValueError(f"只收固定種子 {v.SEEDS},任一批不得替換:{seed}")
    rng = random.Random(seed)  # noqa: S311 - 合成歷史用固定種子,不是密碼學用途
    ids = tuple(f"rm{seed}-{i:04d}" for i in range(N_ADS))
    explore = split(ids).explore
    raised = frozenset(rng.sample(ids, RAISED_ADS))
    ads = tuple(_make_ad(rng, ad_id, ad_id in raised, ad_id in explore) for ad_id in ids)
    return History(seed, v.GENERATOR_VERSION, START, DAYS, ads)


# ---- 評估集文字與雜湊 ----
def _bucket_line(ad_id: str, bucket: DayBucket) -> str:
    if bucket.no_data:
        return f"N|{ad_id}|{bucket.day.isoformat()}"
    values = (bucket.impressions, bucket.clicks, bucket.conversions, bucket.spend_cents,
              bucket.revenue_cents)
    return "|".join(["D", ad_id, bucket.day.isoformat(), *(str(x) for x in values)])


def render(history: History) -> str:
    """評估集的固定文字:標頭、每支廣告一行(A)、每天一行(D;no_data 寫 N)、每筆操作一行(O)。"""
    lines = [f"rule-mining-history|{history.generator_version}|seed={history.seed}|"
             f"start={history.start.isoformat()}|days={history.day_count}|ads={len(history.ads)}"]
    for ad in history.ads:
        lines.append(f"A|{ad.ad_id}|{ad.daily_budget_cents}")
        lines += [_bucket_line(ad.ad_id, bucket) for bucket in ad.days]
        lines += [f"O|{ad.ad_id}|{a.op_id}|{a.committed_at.isoformat()}|{a.budget_before_cents}|"
                  f"{a.budget_after_cents}" for a in ad.adjustments]
    return "\n".join(lines) + "\n"


def data_sha256(history: History) -> str:
    return hashlib.sha256(render(history).encode()).hexdigest()

## src/rtb/eval/rule_mining_baseline.py @ d336754
"""規則模式探索的效果計算、配對、彙總、保留側判定與窮舉前 K(Phase 15 增量 1,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉〈封閉條件語彙、彙總與窮舉基準〉)。

- 每個加額事件以操作的 UTC 日為 D,只取 D-3..D-1 與 D+1..D+3 六個完整日(D 本身不算)。視窗超出
  觀察期、視窗內另有調整、資料異常、缺值(含 no_data)、轉換率分母為零都不推斷,逐原因計數。
- 轉換率 = 轉換 / 點擊,一律經 `rtb.domain.metrics.exact_ratio` 取精確分數;組的變化是後三日率減
  前三日率的**絕對差**,配對差值是加額組變化減對照組變化。判斷與核對不讀任何捨入字串。
- 對照只取同一側、**整個觀察期從未加額**的廣告;配對須同側、同 UTC 日 D、同前三日轉換率區間、同投放
  規模桶。每條條件先按 `(D, 加額廣告編號位元組, committed_at, 操作識別碼)` 處理事件,對照按編號位元組
  升序取第一支未用者,每支對照在該條件至多用一次;找不到記「無對照」。
- 差值為零是平手:不算支持也不算反例。有方向配對數 = 正差 + 負差;支持比例、平均差值分母、樣本下限、
  兩側相異廣告數與相異日期數都只看有方向配對。
- 窮舉基準:兩方向合併,依 Wilson 95% 下界(直接呼叫 `rtb.eval.scoring.wilson_lower`)、方向化平均
  差值、有方向配對數、條件鍵、方向代碼排序;同條件兩方向只留較高者,取前 K。
- 純函式、標準函式庫;不匯入 DSP、模型閘道或模型用戶端。這是可重算的合成比較,不是因果證明。
"""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

from rtb.domain import metrics as m
from rtb.eval import rule_mining_vocab as v
from rtb.eval.rule_mining_history import Ad, Adjustment, DayBucket, History
from rtb.eval.scoring import wilson_lower

INCOMPLETE_WINDOW = "incomplete_window"
OVERLAPPING_ADJUSTMENT = "overlapping_adjustment"
ANOMALOUS_DATA = "anomalous_data"
MISSING_VALUE = "missing_value"
ZERO_DENOMINATOR = "zero_denominator"
NO_CONTROL = "no_control"
# 保留側判定的原因
INSUFFICIENT_SAMPLE = "insufficient_sample"
LOW_SUPPORT = "low_support"
WRONG_SIGN = "wrong_sign"
UNMEASURED = "unmeasured"

_REASON_FROM_METRIC = {m.Reason.NO_DENOMINATOR: ZERO_DENOMINATOR,
                       m.Reason.MISSING_DATA: MISSING_VALUE,
                       m.Reason.INVALID_DATA: ANOMALOUS_DATA}


@dataclass(frozen=True)
class _Window:
    pre_rate: Fraction
    change: Fraction  # 後三日率 - 前三日率(絕對差)
    pre_spend: int  # 前三日花費(分)


@dataclass(frozen=True)
class RaiseEvent:
    ad_id: str
    op_id: str
    committed_at: datetime
    day: date  # D(UTC)
    clauses: frozenset[v.Clause]  # 這筆事件在四個欄位的值
    pre_band: str
    scale: str
    change: Fraction

    def matches(self, key: v.ConditionKey) -> bool:
        return all(clause in self.clauses for clause in key)

    @property
    def order(self) -> tuple[date, bytes, datetime, str]:
        return self.day, self.ad_id.encode(), self.committed_at, self.op_id


@dataclass(frozen=True)
class Control:
    ad_id: str
    change: Fraction


@dataclass(frozen=True)
class Pair:
    op_id: str
    raised_ad: str
    control_ad: str
    day: date
    diff: Fraction  # 加額組變化 - 對照組變化


@dataclass(frozen=True)
class ConditionStats:
    key: v.ConditionKey
    events: int  # 符合條件的可推斷加額事件
    pairs: int  # 總配對 = 正差 + 負差 + 平手
    positive: int
    negative: int
    ties: int
    raised_ads: int  # 有方向配對裡的相異加額廣告
    control_ads: int  # 有方向配對裡的相異對照廣告
    dates: int  # 有方向配對裡的相異 UTC 日
    diff_sum: Fraction  # 有方向差值合計(精確)
    no_control: int

    @property
    def directed(self) -> int:
        return self.positive + self.negative


@dataclass(frozen=True)
class SideSummary:
    events: int  # 可推斷的加額事件
    exclusions: Mapping[str, int]  # 事件層排除原因(無對照另在每條件的 no_control)
    stats: Mapping[v.ConditionKey, ConditionStats]


@dataclass(frozen=True)
class Holdout:
    kept: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Ranked:
    key: v.ConditionKey
    direction: str
    support: int
    counter: int
    wilson: float
    directional_mean: Fraction
    directed: int

    @property
    def sort_key(self) -> tuple[float, Fraction, int, v.ConditionKey, str]:
        return -self.wilson, -self.directional_mean, -self.directed, self.key, self.direction


@dataclass(frozen=True)
class Reach:
    """分母可達性:只看有方向配對數與兩側相異廣告數,不看正負差。"""

    directed: int
    raised_ads: int
    control_ads: int

    @property
    def meets(self) -> bool:
        return (self.directed >= v.MIN_DIRECTED and self.raised_ads >= v.MIN_DISTINCT_ADS
                and self.control_ads >= v.MIN_DISTINCT_ADS)


# ---- 視窗與事件 ----
def _is_raise(adjustment: Adjustment) -> bool:
    return adjustment.budget_after_cents > adjustment.budget_before_cents


def _utc_day(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def _complete(history: History, day: date) -> bool:
    last = history.start + timedelta(days=history.day_count - 1)
    reach = timedelta(days=v.WINDOW_DAYS)
    return history.start <= day - reach and day + reach <= last


def _bucket_problem(buckets: Sequence[DayBucket | None]) -> str | None:
    """資料異常優先於缺值(同領域層指標的順序)。"""
    rows = [(b.impressions, b.clicks, b.conversions, b.spend_cents, b.revenue_cents)
            for b in buckets if b is not None and not b.no_data]
    present = [x for row in rows for x in row if x is not None]
    if any(type(x) is not int or x < 0 for x in present) or any(
            imp is not None and clk is not None and clk > imp for imp, clk, *_ in rows):
        return ANOMALOUS_DATA
    if len(rows) < len(buckets) or len(present) < 5 * len(rows):  # 每桶五個欄位
        return MISSING_VALUE
    return None


def _window(days: Mapping[date, DayBucket], day: date) -> _Window | str:
    offsets = range(1, v.WINDOW_DAYS + 1)
    pre = [days.get(day - timedelta(days=i)) for i in reversed(offsets)]
    post = [days.get(day + timedelta(days=i)) for i in offsets]
    problem = _bucket_problem(pre + post)
    if problem is not None:
        return problem
    before = [b for b in pre if b is not None]
    after = [b for b in post if b is not None]
    pre_rate = m.exact_ratio(sum(b.conversions or 0 for b in before),
                             sum(b.clicks or 0 for b in before))
    post_rate = m.exact_ratio(sum(b.conversions or 0 for b in after),
                              sum(b.clicks or 0 for b in after))
    for rate in (pre_rate, post_rate):
        if isinstance(rate, m.Reason):
            return _REASON_FROM_METRIC[rate]
    assert isinstance(pre_rate, Fraction) and isinstance(post_rate, Fraction)  # noqa: S101
    return _Window(pre_rate, post_rate - pre_rate, sum(b.spend_cents or 0 for b in before))


def _event(history: History, ad: Ad, adjustment: Adjustment,
           days: Mapping[date, DayBucket]) -> RaiseEvent | str:
    day = _utc_day(adjustment.committed_at)
    if not _complete(history, day):
        return INCOMPLETE_WINDOW
    if any(other is not adjustment and abs((_utc_day(other.committed_at) - day).days)
           <= v.WINDOW_DAYS for other in ad.adjustments):
        return OVERLAPPING_ADJUSTMENT
    window = _window(days, day)
    if isinstance(window, str):
        return window
    before = adjustment.budget_before_cents
    raise_pct = m.exact_ratio(adjustment.budget_after_cents - before, before)
    spend_ratio = m.exact_ratio(window.pre_spend, v.WINDOW_DAYS * before)
    if not (isinstance(raise_pct, Fraction) and isinstance(spend_ratio, Fraction)):
        return ANOMALOUS_DATA
    pre_band = v.band(v.PRE_CVR, window.pre_rate)
    clauses = frozenset({(v.DAY_TYPE, v.day_type(day)), (v.PRE_CVR, pre_band),
                         (v.RAISE_PCT, v.band(v.RAISE_PCT, raise_pct)),
                         (v.SPEND_RATIO, v.band(v.SPEND_RATIO, spend_ratio))})
    return RaiseEvent(ad.ad_id, adjustment.op_id, adjustment.committed_at, day, clauses,
                      pre_band, v.scale_bucket(window.pre_spend), window.change)


def _side_ads(history: History, side: Collection[str]) -> list[Ad]:
    return sorted((ad for ad in history.ads if ad.ad_id in side), key=lambda a: a.ad_id.encode())


def side_events(history: History, side: Collection[str]
                ) -> tuple[tuple[RaiseEvent, ...], dict[str, int]]:
    """一側的可推斷加額事件(按固定次序)與逐原因排除計數。"""
    events, excluded = [], Counter[str]()
    for ad in _side_ads(history, side):
        days = {bucket.day: bucket for bucket in ad.days}
        for adjustment in filter(_is_raise, ad.adjustments):
            result = _event(history, ad, adjustment, days)
            if isinstance(result, str):
                excluded[result] += 1
            else:
                events.append(result)
    return tuple(sorted(events, key=lambda e: e.order)), dict(sorted(excluded.items()))


ControlIndex = Mapping[tuple[date, str, str], tuple[Control, ...]]


def control_index(history: History, side: Collection[str],
                  days: Iterable[date] | None = None) -> ControlIndex:
    """同側、整期從未加額的對照:(D, 前三日轉換率區間, 規模桶) → 按編號位元組升序的對照。"""
    wanted = sorted(set(days) if days is not None else {
        history.start + timedelta(days=i) for i in range(history.day_count)})
    index: dict[tuple[date, str, str], list[Control]] = {}
    for ad in _side_ads(history, side):
        if any(_is_raise(a) for a in ad.adjustments):
            continue
        by_day = {bucket.day: bucket for bucket in ad.days}
        for day in wanted:
            window = _window(by_day, day) if _complete(history, day) else INCOMPLETE_WINDOW
            if isinstance(window, str):
                continue
            slot = (day, v.band(v.PRE_CVR, window.pre_rate), v.scale_bucket(window.pre_spend))
            index.setdefault(slot, []).append(Control(ad.ad_id, window.change))
    return {slot: tuple(controls) for slot, controls in index.items()}


def pair_condition(key: v.ConditionKey, events: Iterable[RaiseEvent], index: ControlIndex
                   ) -> tuple[tuple[Pair, ...], int]:
    """一條條件的不放回配對:(配對, 找不到對照的事件數)。"""
    used: set[str] = set()
    pairs, missing = [], 0
    for event in sorted((e for e in events if e.matches(key)), key=lambda e: e.order):
        control = next((c for c in index.get((event.day, event.pre_band, event.scale), ())
                        if c.ad_id not in used), None)
        if control is None:
            missing += 1
            continue
        used.add(control.ad_id)
        pairs.append(Pair(event.op_id, event.ad_id, control.ad_id, event.day,
                          event.change - control.change))
    return tuple(pairs), missing


def stats_of(key: v.ConditionKey, events: int, pairs: Sequence[Pair], no_control: int
             ) -> ConditionStats:
    directed = [p for p in pairs if p.diff != 0]
    positive = sum(1 for p in directed if p.diff > 0)
    return ConditionStats(
        key=key, events=events, pairs=len(pairs), positive=positive,
        negative=len(directed) - positive, ties=len(pairs) - len(directed),
        raised_ads=len({p.raised_ad for p in directed}),
        control_ads=len({p.control_ad for p in directed}),
        dates=len({p.day for p in directed}), diff_sum=sum((p.diff for p in directed),
                                                           Fraction(0)),
        no_control=no_control)


def summarize(history: History, side: Collection[str]) -> SideSummary:
    """一側逐條件(封閉全集)的彙總。"""
    events, excluded = side_events(history, side)
    index = control_index(history, side, {e.day for e in events})
    stats = {}
    for key in v.all_conditions():
        matching = [e for e in events if e.matches(key)]
        pairs, missing = pair_condition(key, matching, index)
        stats[key] = stats_of(key, len(matching), pairs, missing)
    return SideSummary(events=len(events), exclusions=excluded, stats=stats)


# ---- 下限、方向、保留側 ----
def reach_of(stats: ConditionStats) -> Reach:
    return Reach(stats.directed, stats.raised_ads, stats.control_ads)


def meets_floor(stats: ConditionStats) -> bool:
    return reach_of(stats).meets


def directional(stats: ConditionStats, direction: str) -> tuple[int, int, Fraction | None]:
    """(支持, 反例, 方向化精確平均差值);改善的支持是正差,未改善相反。

    沒有有方向配對時平均是 None。"""
    if direction == v.IMPROVE:
        support, counter, sign = stats.positive, stats.negative, 1
    elif direction == v.NOT_IMPROVE:
        support, counter, sign = stats.negative, stats.positive, -1
    else:
        raise v.VocabularyError("unknown_direction")
    mean = sign * stats.diff_sum / stats.directed if stats.directed else None
    return support, counter, mean


def holdout_verdict(stats: ConditionStats, direction: str) -> Holdout:
    """保留側:有方向配對與兩側相異廣告達下限、支持比例 ≥ 3/5、方向化平均差值 > 0 才保留。"""
    support, _, mean = directional(stats, direction)
    reasons = [] if meets_floor(stats) else [INSUFFICIENT_SAMPLE]
    if mean is None:
        reasons.append(UNMEASURED)
    else:
        if Fraction(support, stats.directed) < v.HOLDOUT_SUPPORT:
            reasons.append(LOW_SUPPORT)
        if mean <= 0:
            reasons.append(WRONG_SIGN)
    return Holdout(kept=not reasons, reasons=tuple(reasons))


# ---- 窮舉前 K ----
def _ranked(stats: ConditionStats, direction: str) -> Ranked:
    support, counter, mean = directional(stats, direction)
    assert mean is not None  # noqa: S101 - 只對達下限(有方向配對 > 0)的條件呼叫
    return Ranked(stats.key, direction, support, counter,
                  wilson_lower(support, stats.directed), mean, stats.directed)


def rank(stats: Mapping[v.ConditionKey, ConditionStats]) -> tuple[Ranked, ...]:
    """達下限的條件各留排序較高的一個方向,再全域排序。"""
    best = [min((_ranked(s, d) for d in v.DIRECTIONS), key=lambda r: r.sort_key)
            for s in stats.values() if meets_floor(s)]
    return tuple(sorted(best, key=lambda r: r.sort_key))


def top_k(stats: Mapping[v.ConditionKey, ConditionStats], k: int = v.K) -> tuple[Ranked, ...]:
    return rank(stats)[:k]

## src/rtb/eval/rule_mining_prompt.py @ d336754
"""規則模式探索的固定系統提示、探索集彙總表、位元組閘與可達性預檢(Phase 15 增量 1,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈封閉條件語彙、彙總與窮舉基準〉〈拆增量〉第 1 項)。

- 這一增量**不呼叫模型**:只把系統提示文字定稿、建出要送的完整彙總表,量「系統提示 + 彙總表」的 UTF-8
  位元組數。完整表只列探索側達樣本下限的條件,欄序、捨入固定;比例沿用 `metrics.percent_text`(一位
  小數),平均差值以百分點、精確分數 half-even 捨入四位小數;精確分數、保留側、逐日列、識別、時間戳與
  真相標籤都不進表。一批只准一次完整提示,不分塊、不截列:`B > 20480` 或達既有 48 KiB 就拒跑。
- 可達性預檢只在建立或更換評估版本時跑:固定三種子逐批記資料雜湊、兩側加額事件與排除原因、每個真模式
  與誘餌在探索/保留兩側的分母(有方向配對數與兩側相異廣告數,不看正負差)、完整提示位元組數;任一批
  超限或任一真相分母不可達就整體失敗,不得換種子或截列通過。結果寫死在 `PREFLIGHT_RECORD`(評估版本
  理由的機器可核對那份),測試重算比對。
- 評估版本雜湊涵蓋語彙模組的參數清單、系統提示、表頭、真相清單與三批資料雜湊;任何一項改動都要換
  `EVAL_VERSION` 並記理由與對 AI、窮舉雙方的預期影響。
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from rtb.domain import metrics as m
from rtb.eval import rule_mining_baseline as b
from rtb.eval import rule_mining_history as h
from rtb.eval import rule_mining_vocab as v

SYSTEM_PROMPT = (
    "你是離線規則探勘助手。使用者內容是一份固定種子合成歷史的探索集彙總表。每列是一條封閉條件"
    "(一或兩個不同欄位的門檻代碼,以 & 相連),數字來自加額事件與對照廣告的配對:對照是同一側、"
    "同一 UTC 日、同一調整前三日轉換率區間、同一投放規模桶、整個觀察期從未加額的廣告。配對差值是"
    "「加額組後三日轉換率減前三日轉換率」減去「對照組同樣的變化」,單位是百分點。\n"
    "任務:從表中挑出至多 10 條你認為在未見資料上最可能也成立的條件與方向,"
    "只能用表中出現的門檻代碼。\n"
    "方向:improve 表示加額後相對對照較可能改善,支持是正差筆數、反例是負差筆數;not_improve 相反,"
    "支持是負差筆數、反例是正差筆數。平手既不是支持也不是反例。\n"
    "輸出:只輸出一行緊湊的 UTF-8 JSON,直接寫中文原字,不要用 \\uXXXX 逸出,不要 Markdown,總長不超過"
    " 6000 位元組。格式:"
    '{"version":1,"suggestions":[{"clauses":[{"condition":"raise_pct",'
    '"threshold":"raise_pct:band_2"}],"direction":"improve","support":24,"counterexample":6,'
    '"confidence_note":"一句話"}]}\n'
    "規則:suggestions 至多 10 條;clauses 是一或兩個不同的 condition,condition 是門檻代碼冒號前的"
    "欄位代碼;同一條件不得同時押兩個方向,也不得重複;support 與 counterexample 照抄表中該方向的整數;"
    "confidence_note 至多 80 字,不含引號、反斜線或控制字元;不要加任何其他鍵。\n"
    "門檻代碼:\n"
    "day_type:weekday 加額 UTC 日是平日;day_type:weekend 是週六或週日\n"
    "raise_pct:band_1 加額幅度未滿 20%;raise_pct:band_2 20% 以上未滿 50%;"
    "raise_pct:band_3 50% 以上\n"
    "pre_cvr:band_1 調整前三日轉換率未滿 2%;pre_cvr:band_2 2% 以上未滿 5%;"
    "pre_cvr:band_3 5% 以上\n"
    "spend_ratio:band_1 調整前三日花費除以三天原日預算未滿 60%;spend_ratio:band_2 60% 以上未滿"
    " 90%;spend_ratio:band_3 90% 以上\n"
)
TABLE_HEADER = (
    f"彙總表 {v.EVAL_VERSION}:探索集;只列有方向配對至少 20 且其中相異加額廣告、相異對照廣告"
    "各至少 20 的條件;有方向配對 = 正差 + 負差,平手不計;相異廣告與日期只數有方向配對",
    "欄位:條件|加額事件|總配對|有方向配對|正差|負差|平手|相異加額廣告|相異對照廣告|相異日期|"
    "正差占有方向比例%|平均差值pp|無對照事件",
)
DIFF_PLACES = 10 ** 4

# 首次預檢後寫死(評估版本理由的機器可核對那份);重跑須逐字相同
PREFLIGHT_RECORD: tuple[str, ...] = (
    '15001 資料 f820e1a486d8b35d 廣告 探索360/保留360 可推斷事件 探索340/保留301 '
     '表列 50 B=6045',
    '15001 排除 探索[incomplete_window=18 missing_value=11 '
     'overlapping_adjustment=18] 保留[incomplete_window=17 '
     'missing_value=9 overlapping_adjustment=32]',
    '15001 true_pattern raise_pct:band_2&spend_ratio:band_3 '
     'improve 探索[有方向48/加額廣告40/對照廣告48] 保留[有方向57/加額廣告45/對照廣告57]',
    '15001 true_pattern raise_pct:band_3 not_improve '
     '探索[有方向113/加額廣告88/對照廣告113] 保留[有方向84/加額廣告66/對照廣告84]',
    '15001 decoy_explore_only day_type:weekend&raise_pct:band_1 '
     'improve 探索[有方向45/加額廣告44/對照廣告45] 保留[有方向40/加額廣告38/對照廣告40]',
    '15001 decoy_correlated spend_ratio:band_1 not_improve '
     '探索[有方向138/加額廣告81/對照廣告138] 保留[有方向83/加額廣告45/對照廣告83]',
    '15002 資料 2a9e4aac0f4ec020 廣告 探索360/保留360 可推斷事件 探索318/保留339 '
     '表列 49 B=5963',
    '15002 排除 探索[incomplete_window=13 missing_value=12 '
     'overlapping_adjustment=16] 保留[incomplete_window=18 '
     'missing_value=7 overlapping_adjustment=13]',
    '15002 true_pattern raise_pct:band_2&spend_ratio:band_3 '
     'improve 探索[有方向49/加額廣告37/對照廣告49] 保留[有方向60/加額廣告45/對照廣告60]',
    '15002 true_pattern raise_pct:band_3 not_improve '
     '探索[有方向101/加額廣告74/對照廣告101] 保留[有方向116/加額廣告88/對照廣告116]',
    '15002 decoy_explore_only day_type:weekend&raise_pct:band_1 '
     'improve 探索[有方向34/加額廣告34/對照廣告34] 保留[有方向39/加額廣告38/對照廣告39]',
    '15002 decoy_correlated spend_ratio:band_1 not_improve '
     '探索[有方向109/加額廣告60/對照廣告109] 保留[有方向112/加額廣告67/對照廣告112]',
    '15003 資料 ba7d6ebc8a3a8b4d 廣告 探索360/保留360 可推斷事件 探索333/保留324 '
     '表列 50 B=6032',
    '15003 排除 探索[incomplete_window=14 missing_value=7 '
     'overlapping_adjustment=21] 保留[incomplete_window=10 '
     'missing_value=8 overlapping_adjustment=28]',
    '15003 true_pattern raise_pct:band_2&spend_ratio:band_3 '
     'improve 探索[有方向62/加額廣告49/對照廣告62] 保留[有方向53/加額廣告39/對照廣告53]',
    '15003 true_pattern raise_pct:band_3 not_improve '
     '探索[有方向109/加額廣告80/對照廣告109] 保留[有方向118/加額廣告82/對照廣告118]',
    '15003 decoy_explore_only day_type:weekend&raise_pct:band_1 '
     'improve 探索[有方向48/加額廣告46/對照廣告48] 保留[有方向27/加額廣告27/對照廣告27]',
    '15003 decoy_correlated spend_ratio:band_1 not_improve '
     '探索[有方向96/加額廣告54/對照廣告96] 保留[有方向117/加額廣告65/對照廣告117]',
)
EXPECTED_VERSION_SHA256 = "831dc0dc4736cce90e04ff7cc1d7ad6336d77497f4d0c28495b02cff27c7ba9f"


def diff_text(mean: Fraction) -> str:
    """平均差值:比率刻度 → 百分點,精確分數 half-even 捨入到四位小數;負零寫 0。"""
    scaled = round(mean * 100 * DIFF_PLACES)  # Fraction 的 round 是四捨五入到偶數
    sign = "-" if scaled < 0 else ""
    return f"{sign}{abs(scaled) // DIFF_PLACES}.{abs(scaled) % DIFF_PLACES:04d}"


def _row(stats: b.ConditionStats) -> str:
    counts = (stats.events, stats.pairs, stats.directed, stats.positive, stats.negative,
              stats.ties, stats.raised_ads, stats.control_ads, stats.dates)
    share = m.percent_text(m.exact_ratio(stats.positive, stats.directed))
    return "|".join([v.key_text(stats.key), *(str(c) for c in counts), share,
                     diff_text(stats.diff_sum / stats.directed), str(stats.no_control)])


def summary_table(summary: b.SideSummary) -> str:
    """送模型的完整彙總表(使用者內容):表頭、事件層排除計數、達下限的每一條條件一列。"""
    excluded = " ".join(f"{reason}={count}" for reason, count in summary.exclusions.items())
    lines = [*TABLE_HEADER, f"可推斷加額事件 {summary.events};事件層排除:{excluded or '無'}"]
    lines += [_row(summary.stats[key]) for key in v.all_conditions()
              if b.meets_floor(summary.stats[key])]
    return "\n".join(lines) + "\n"


def prompt_bytes(table: str) -> int:
    """系統提示加使用者內容(彙總表)的 UTF-8 位元組數。"""
    return len(SYSTEM_PROMPT.encode()) + len(table.encode())


def gate_problem(size: int) -> str | None:
    """單次完整提示的位元組閘;超過就拒跑(不分塊、不截列)。預留額另在模型增量用 repo 函式重算。"""
    if size > v.PROMPT_BYTES_LIMIT:
        return f"完整提示 {size} 位元組超過本案上限 {v.PROMPT_BYTES_LIMIT}"
    if size >= v.GATEWAY_PROMPT_BYTES:
        return f"完整提示 {size} 位元組達既有上限 {v.GATEWAY_PROMPT_BYTES}"
    return None


def version_sha256() -> str:
    """評估版本雜湊:參數清單、系統提示、表頭、真相清單與三批預期資料雜湊。"""
    document = {
        "params": v.version_params(), "system_prompt": SYSTEM_PROMPT,
        "table_header": list(TABLE_HEADER),
        "truth": [[t.kind, [list(c) for c in t.clauses], t.direction] for t in h.TRUTH],
        "data_sha256": {str(seed): h.EXPECTED_DATA_SHA256[seed] for seed in v.SEEDS},
    }
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


# ---- 可達性預檢 ----
@dataclass(frozen=True)
class TruthReach:
    kind: str
    key: v.NormalizedKey
    explore: b.Reach
    holdout: b.Reach


@dataclass(frozen=True)
class SeedPreflight:
    seed: int
    data_sha256: str
    explore_ads: int
    holdout_ads: int
    explore_events: int
    holdout_events: int
    explore_exclusions: Mapping[str, int]
    holdout_exclusions: Mapping[str, int]
    table_rows: int
    prompt_bytes: int
    truths: tuple[TruthReach, ...]


def preflight_seed(seed: int) -> SeedPreflight:
    history = h.generate(seed)
    sides = h.split(ad.ad_id for ad in history.ads)
    explore, holdout = b.summarize(history, sides.explore), b.summarize(history, sides.holdout)
    table = summary_table(explore)
    truths = tuple(TruthReach(t.kind, t.key, b.reach_of(explore.stats[t.key[0]]),
                              b.reach_of(holdout.stats[t.key[0]])) for t in h.TRUTH)
    return SeedPreflight(
        seed=seed, data_sha256=h.data_sha256(history), explore_ads=len(sides.explore),
        holdout_ads=len(sides.holdout), explore_events=explore.events,
        holdout_events=holdout.events, explore_exclusions=explore.exclusions,
        holdout_exclusions=holdout.exclusions,
        table_rows=sum(1 for s in explore.stats.values() if b.meets_floor(s)),
        prompt_bytes=prompt_bytes(table), truths=truths)


def preflight() -> tuple[SeedPreflight, ...]:
    """固定三種子逐批預檢(不收別的種子)。"""
    return tuple(preflight_seed(seed) for seed in v.SEEDS)


def preflight_problems(results: Sequence[SeedPreflight]) -> tuple[str, ...]:
    """任一批超限、任一真相分母不可達、或不是恰好三個固定種子,就回問題清單(空 = 通過)。"""
    problems = []
    if tuple(r.seed for r in results) != v.SEEDS:
        problems.append(f"預檢須恰好依序列出固定種子 {v.SEEDS}")
    for result in results:
        gate = gate_problem(result.prompt_bytes)
        if gate is not None:
            problems.append(f"{result.seed}: {gate}")
        for truth in result.truths:
            for side, reach in (("探索", truth.explore), ("保留", truth.holdout)):
                if not reach.meets:
                    problems.append(
                        f"{result.seed}: {truth.kind} {v.key_text(truth.key[0])} "
                        f"{truth.key[1]} {side}側分母不可達({_reach_text(reach)})")
    return tuple(problems)


def _reach_text(reach: b.Reach) -> str:
    return f"有方向{reach.directed}/加額廣告{reach.raised_ads}/對照廣告{reach.control_ads}"


def _counts_text(counts: Mapping[str, int]) -> str:
    return " ".join(f"{reason}={count}" for reason, count in counts.items()) or "無"


def preflight_record(results: Sequence[SeedPreflight]) -> tuple[str, ...]:
    """預檢結果的固定文字(寫進評估版本理由):每批一行總覽、一行排除、每個真相一行分母。"""
    lines = []
    for r in results:
        lines.append(f"{r.seed} 資料 {r.data_sha256[:16]} 廣告 探索{r.explore_ads}/保留"
                     f"{r.holdout_ads} 可推斷事件 探索{r.explore_events}/保留{r.holdout_events} "
                     f"表列 {r.table_rows} B={r.prompt_bytes}")
        lines.append(f"{r.seed} 排除 探索[{_counts_text(r.explore_exclusions)}] "
                     f"保留[{_counts_text(r.holdout_exclusions)}]")
        lines += [f"{r.seed} {t.kind} {v.key_text(t.key[0])} {t.key[1]} "
                  f"探索[{_reach_text(t.explore)}] 保留[{_reach_text(t.holdout)}]"
                  for t in r.truths]
    return tuple(lines)

## tests/eval/test_rule_mining.py @ d336754
"""Phase 15 增量 1:規則模式探索的合成歷史與無模型基準(計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉〈封閉條件語彙、彙總與窮舉基準〉)。

合約 [S1501] [S1502] [S1503](只驗彙總內容與位元組閘,不接模型) [S1511] [S1512] [S1513] [S1515]
[S1519] [S1520]。純離線:不呼叫任何模型、不讀寫 ~/.rtb。手造的小歷史只用來驗單一規則;固定三種子
的整批生成用模組層快取,整個檔只各生成一次。
"""

import dataclasses
import functools
import hashlib
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import pytest

from rtb import modelcore
from rtb.domain import metrics as m
from rtb.eval import rule_mining_baseline as rb
from rtb.eval import rule_mining_history as rh
from rtb.eval import rule_mining_prompt as rp
from rtb.eval import rule_mining_vocab as rv
from rtb.eval import scoring

START = date(2026, 8, 3)


# ---- 手造歷史的小工具 ----
def _bucket(day, clicks=100, conv=3, spend=3000, impressions=None):
    return rh.DayBucket(day=day, impressions=clicks * 30 if impressions is None else impressions,
                        clicks=clicks, conversions=conv, spend_cents=spend,
                        revenue_cents=conv * 4000, no_data=False)


def _ad(ad_id, days=10,  # noqa: PLR0913 - 造資料的小工具,各參數都有預設
        pre=3, post=3, clicks=100, spend=3000, raise_at=None, budget=2000,
        after=2600, changes=None, extra_raises=()):
    """一支廣告:raise_at 之前每天 pre 筆轉換、之後每天 post 筆;changes 覆寫某天的桶。"""
    buckets = []
    for index in range(days):
        conv = post if raise_at is not None and index > raise_at else pre
        if raise_at is None and index >= 5:
            conv = post
        buckets.append(_bucket(START + timedelta(days=index), clicks=clicks, conv=conv,
                               spend=spend))
    for index, bucket in (changes or {}).items():
        buckets[index] = bucket
    adjustments = []
    for k, at in enumerate(([] if raise_at is None else [raise_at]) + list(extra_raises)):
        adjustments.append(rh.Adjustment(
            op_id=f"{ad_id}-op{k}", committed_at=datetime.combine(
                START + timedelta(days=at), datetime.min.time(), tzinfo=UTC) + timedelta(hours=9),
            budget_before_cents=budget, budget_after_cents=after))
    return rh.Ad(ad_id=ad_id, daily_budget_cents=budget, days=tuple(buckets),
                 adjustments=tuple(adjustments))


def _history(ads, days=10):
    return rh.History(seed=0, generator_version="test", start=START, day_count=days,
                      ads=tuple(ads))


def _summary(history, ids=None, key=None):
    side = frozenset(a.ad_id for a in history.ads) if ids is None else frozenset(ids)
    summary = rb.summarize(history, side)
    return summary if key is None else summary.stats[key]


RAISE_2 = ((rv.RAISE_PCT, "raise_pct:band_2"),)  # 2600/2000 = 加 30%
WEEKEND = ((rv.DAY_TYPE, "day_type:weekend"),)  # 第 5 天是週六


def _pair(k, diff, raised=None, control=None, day=START):
    return rb.Pair(op_id=f"op{k}", raised_ad=raised or f"a{k:02d}",
                   control_ad=control or f"c{k:02d}", day=day, diff=Fraction(diff))


def _stats(pairs, events=None):
    return rb.stats_of(RAISE_2, len(pairs) if events is None else events, tuple(pairs), 0)


# ---- 固定三種子(整個檔只生成一次) ----
@functools.cache
def _generated(seed):
    return rh.generate(seed)


@functools.cache
def _preflight():
    return rp.preflight()


# ---- [S1501] ----
def test_rule_mining_history_is_reproducible_and_consistent():  # noqa: PLR0915 - 逐項自洽檢查
    assert rv.SEEDS == (15001, 15002, 15003)
    with pytest.raises(ValueError, match="固定種子"):
        rh.generate(15004)  # 任一批不得換種子
    for seed in rv.SEEDS:
        history = _generated(seed)
        again = rh.generate(seed)
        text = rh.render(history)
        assert text == rh.render(again)  # 位元組一致
        assert rh.data_sha256(history) == hashlib.sha256(text.encode()).hexdigest()
        assert rh.data_sha256(history) == rh.EXPECTED_DATA_SHA256[seed]
        assert history.seed == seed and history.generator_version == rv.GENERATOR_VERSION
        assert len(history.ads) >= 300 and history.day_count >= 28
        assert len({a.ad_id for a in history.ads}) == len(history.ads)
        expected_days = [history.start + timedelta(days=i) for i in range(history.day_count)]
        for ad in history.ads:
            assert [b.day for b in ad.days] == expected_days  # 連續 UTC 日、每天一個桶
            budget = ad.daily_budget_cents
            by_day = {a.committed_at.date(): a for a in ad.adjustments}
            for bucket in ad.days:
                if bucket.day in by_day:  # 操作鏈:調整前預算接上一筆調整後
                    assert by_day[bucket.day].budget_before_cents == budget
                    budget = by_day[bucket.day].budget_after_cents
                if bucket.no_data:
                    assert bucket.clicks is None and bucket.spend_cents is None
                    continue
                values = (bucket.impressions, bucket.clicks, bucket.conversions,
                          bucket.spend_cents, bucket.revenue_cents)
                assert all(type(v) is int and v >= 0 for v in values)  # 整數分,不是浮點
                assert bucket.conversions <= bucket.clicks <= bucket.impressions
                assert bucket.spend_cents <= budget
            for adjustment in ad.adjustments:
                assert adjustment.committed_at.tzinfo is UTC
                assert history.start <= adjustment.committed_at.date() <= expected_days[-1]
                assert adjustment.budget_after_cents > adjustment.budget_before_cents
    # 真相清單同一生成版本固定;真模式改善/未改善各一,誘餌兩類各一,鍵都是語彙內的正規化鍵
    kinds = Counter((t.kind, t.direction) for t in rh.TRUTH)
    assert kinds[(rh.TRUE_PATTERN, rv.IMPROVE)] >= 1
    assert kinds[(rh.TRUE_PATTERN, rv.NOT_IMPROVE)] >= 1
    assert sum(n for (kind, _), n in kinds.items() if kind == rh.DECOY_EXPLORE_ONLY) >= 1
    assert sum(n for (kind, _), n in kinds.items() if kind == rh.DECOY_CORRELATED) >= 1
    for item in rh.TRUTH:
        assert rv.normalized_key(item.clauses, item.direction) == item.key
        assert item.key[0] in rv.all_conditions()
    # 真相清單不在評估集裡(評估集的文字不含類別名)
    text = rh.render(_generated(rv.SEEDS[0]))
    assert rh.TRUE_PATTERN not in text and rh.DECOY_CORRELATED not in text


# ---- [S1502] ----
def _two_ads(**raised):
    raised_ad = _ad("a1", raise_at=5, pre=3, post=6, **raised)
    control = _ad("c1", pre=3, post=4)  # 未加額:第 5 天以後每天 4 筆
    return raised_ad, control


def test_rule_mining_effects_use_complete_utc_days_and_exact_ratios():  # noqa: PLR0915
    raised_ad, control = _two_ads()
    history = _history([raised_ad, control])
    events, excluded = rb.side_events(history, frozenset({"a1", "c1"}))
    assert excluded == {} and len(events) == 1
    (event,) = events
    assert event.day == START + timedelta(days=5)
    # 前三日 9/300、後三日 18/300:絕對差(不是 exact_change 的相對變化)
    assert event.change == Fraction(18, 300) - Fraction(9, 300)
    assert event.change == m.exact_ratio(18, 300) - m.exact_ratio(9, 300)
    stats = _summary(history, key=RAISE_2)
    assert stats.pairs == 1 and stats.positive == 1
    assert isinstance(stats.diff_sum, Fraction)
    # 對照 C 前三日 9/300、後三日(第 6-8 天)12/300 → 差值 9/300 - 3/300 = 1/50
    assert stats.diff_sum == Fraction(1, 50)
    # D 本身不算:把 D 當天改得很極端,效果不變
    wild = _ad("a1", raise_at=5, pre=3, post=6, changes={5: _bucket(START + timedelta(days=5),
                                                                     conv=90)})
    assert _summary(_history([wild, control]), key=RAISE_2).diff_sum == Fraction(1, 50)

    def reasons(*ads):
        return rb.side_events(_history(ads), frozenset(a.ad_id for a in ads))[1]

    # 未滿後三日 / 前三日不在觀察期內
    assert reasons(_ad("a1", raise_at=7)) == {rb.INCOMPLETE_WINDOW: 1}
    assert reasons(_ad("a1", raise_at=2)) == {rb.INCOMPLETE_WINDOW: 1}
    # 視窗內另有調整:兩筆都不推斷
    assert reasons(_ad("a1", raise_at=4, extra_raises=(6,))) == {rb.OVERLAPPING_ADJUSTMENT: 2}
    # no_data、缺值
    gap = rh.DayBucket(day=START + timedelta(days=6), impressions=None, clicks=None,
                       conversions=None, spend_cents=None, revenue_cents=None, no_data=True)
    assert reasons(_ad("a1", raise_at=5, changes={6: gap})) == {rb.MISSING_VALUE: 1}
    none_clicks = dataclasses.replace(_bucket(START + timedelta(days=3)), clicks=None)
    assert reasons(_ad("a1", raise_at=5, changes={3: none_clicks})) == {rb.MISSING_VALUE: 1}
    # 資料異常:點擊比曝光多、浮點金額
    odd = _bucket(START + timedelta(days=7), clicks=100, impressions=50)
    assert reasons(_ad("a1", raise_at=5, changes={7: odd})) == {rb.ANOMALOUS_DATA: 1}
    floaty = dataclasses.replace(_bucket(START + timedelta(days=4)), spend_cents=30.5)
    assert reasons(_ad("a1", raise_at=5, changes={4: floaty})) == {rb.ANOMALOUS_DATA: 1}
    # 轉換率分母為零:不當零
    zero = {i: _bucket(START + timedelta(days=i), clicks=0, conv=0) for i in (6, 7, 8)}
    assert reasons(_ad("a1", raise_at=5, changes=zero)) == {rb.ZERO_DENOMINATOR: 1}
    # 對照 C 於 D+2 曾加額(D 日沒加額)也不得入池;其自己那筆因未滿後三日排除
    raised_c = _ad("c1", pre=3, post=4, raise_at=7)
    summary = _summary(_history([raised_ad, raised_c]))
    assert summary.stats[RAISE_2].pairs == 0 and summary.stats[RAISE_2].no_control == 1
    assert summary.exclusions == {rb.INCOMPLETE_WINDOW: 1}
    # 對照的視窗不可算(分母為零)也不能用
    zero_control = _ad("c1", changes={i: _bucket(START + timedelta(days=i), clicks=0, conv=0)
                                      for i in (2, 3, 4)})
    assert _summary(_history([raised_ad, zero_control]), key=RAISE_2).no_control == 1


# ---- [S1503] ----
def test_rule_mining_prompt_contains_only_bounded_aggregates():  # noqa: PLR0915
    seed = rv.SEEDS[0]
    history = _generated(seed)
    sides = rh.split(a.ad_id for a in history.ads)
    summary = rb.summarize(history, sides.explore)
    table = rp.summary_table(summary)
    rows = [line for line in table.splitlines() if line and line[0].islower()]
    passing = [k for k in rv.all_conditions() if rb.meets_floor(summary.stats[k])]
    assert len(rows) == len(passing) > 0  # 完整表,不截列
    assert [r.split("|")[0] for r in rows] == [rv.key_text(k) for k in passing]
    for row, key in zip(rows, passing, strict=True):
        stats = summary.stats[key]
        cells = row.split("|")
        assert stats.directed >= 20 and stats.raised_ads >= 20 and stats.control_ads >= 20
        assert cells[1:10] == [str(v) for v in (
            stats.events, stats.pairs, stats.directed, stats.positive, stats.negative,
            stats.ties, stats.raised_ads, stats.control_ads, stats.dates)]
        assert cells[10] == m.percent_text(m.exact_ratio(stats.positive, stats.directed))
        mean = stats.diff_sum / stats.directed
        assert cells[11] == rp.diff_text(mean)
    # 差值:百分點四位小數、精確分數 half-even;精確分數不外送
    assert rp.diff_text(Fraction(1, 2_000_000)) == "0.0000"  # 0.00005 → 偶數 0
    assert rp.diff_text(Fraction(3, 2_000_000)) == "0.0002"  # 0.00015 → 偶數 2
    assert rp.diff_text(Fraction(-1, 50)) == "-2.0000"
    assert rp.diff_text(Fraction(-1, 3_000_000)) == "0.0000"  # 負零寫 0
    assert "/" not in table.replace("pp", "")
    # 不送保留側、逐日列、識別、時間戳或真相標籤
    prompt = rp.SYSTEM_PROMPT + table
    for ad in history.ads:
        assert ad.ad_id not in prompt
        assert all(a.op_id not in prompt for a in ad.adjustments)
    assert "2026-" not in prompt and "T0" not in prompt
    for label in (rh.TRUE_PATTERN, rh.DECOY_CORRELATED, rh.DECOY_EXPLORE_ONLY, "真模式", "誘餌"):
        assert label not in prompt
    holdout_changed = dataclasses.replace(history, ads=tuple(
        ad if ad.ad_id in sides.explore else dataclasses.replace(ad, days=ad.days[:5])
        for ad in history.ads))  # 保留側怎麼變,送出的表都一樣
    assert rp.summary_table(rb.summarize(holdout_changed, sides.explore)) == table
    # 詞彙全部在系統提示裡;要求 UTF-8 原字、緊湊 JSON
    for field in rv.FIELDS:
        assert all(code in rp.SYSTEM_PROMPT for code in rv.THRESHOLDS[field])
    assert "\\uXXXX" in rp.SYSTEM_PROMPT and "not_improve" in rp.SYSTEM_PROMPT
    # 位元組閘:20480 可、20481 拒;達既有 48 KiB 也拒;常數與模型用戶端一致
    assert rv.GATEWAY_PROMPT_BYTES == modelcore.MAX_PROMPT_BYTES == 48 * 1024
    assert rp.gate_problem(20480) is None
    assert rp.gate_problem(20481) is not None
    assert rp.gate_problem(48 * 1024) is not None
    assert rp.prompt_bytes(table) == len(rp.SYSTEM_PROMPT.encode()) + len(table.encode())


# ---- [S1511] ----
def _crowd(n_raised, n_controls, day=5, prefix="a"):
    raised = [_ad(f"{prefix}{i:02d}", raise_at=day, pre=3, post=6) for i in range(n_raised)]
    controls = [_ad(f"c{i:02d}", pre=3, post=4) for i in range(n_controls)]
    return raised, controls


def test_rule_mining_pairs_stay_within_split_and_use_distinct_ads():
    # 21 筆加額只有 19 支對照:19 對、2 筆無對照,不合格
    raised, controls = _crowd(21, 19)
    stats = _summary(_history(raised + controls), key=RAISE_2)
    assert (stats.pairs, stats.no_control, stats.control_ads) == (19, 2, 19)
    assert not rb.meets_floor(stats)
    # 21 對共用 1 支對照:相異對照只有 1 支,不合格
    shared = _stats([_pair(k, 1, control="c00") for k in range(21)])
    assert shared.directed == 21 and shared.control_ads == 1 and not rb.meets_floor(shared)
    # 20 對、20/20 相異廣告:合格;星期條件照算且報日期數(全在同一天 → 1 個日期)
    raised, controls = _crowd(20, 20)
    summary = _summary(_history(raised + controls))
    weekend = summary.stats[WEEKEND]
    assert rb.meets_floor(weekend) and weekend.dates == 1
    assert (weekend.raised_ads, weekend.control_ads) == (20, 20)
    # 每條件每支對照至多一次;同一支對照可在不同條件各用一次
    used = [p.control_ad for p in rb.pair_condition(
        WEEKEND, rb.side_events(_history(raised + controls), frozenset(
            a.ad_id for a in raised + controls))[0], rb.control_index(
            _history(raised + controls), frozenset(a.ad_id for a in raised + controls)))[0]]
    assert len(used) == len(set(used)) == 20
    # 探索側只找得到保留側的對照:不跨側
    history = _history([_ad("a1", raise_at=5, pre=3, post=6), _ad("c1", pre=3, post=4)])
    stats = _summary(history, ids={"a1"}, key=RAISE_2)
    assert stats.pairs == 0 and stats.no_control == 1
    # 對照在觀察期曾加額(即使視窗外):不入池
    history = _history([_ad("a1", raise_at=5, pre=3, post=6),
                        _ad("c1", pre=3, post=4, raise_at=0)])
    assert _summary(history, key=RAISE_2).no_control == 1


# ---- [S1512] ----
def test_rule_mining_ties_are_neutral_in_summary_and_recount():
    pairs = [_pair(k, Fraction(1, 100)) for k in range(12)]
    pairs += [_pair(k, Fraction(-1, 200)) for k in range(12, 20)]
    pairs += [_pair(20, 0)]
    stats = _stats(pairs)
    assert (stats.positive, stats.negative, stats.ties, stats.pairs) == (12, 8, 1, 21)
    assert stats.directed == 20 == stats.positive + stats.negative
    assert stats.pairs == stats.positive + stats.negative + stats.ties
    assert rb.directional(stats, rv.IMPROVE) == (12, 8, (Fraction(12, 100) - Fraction(8, 200)) / 20)
    assert rb.directional(stats, rv.NOT_IMPROVE) == (
        8, 12, -(Fraction(12, 100) - Fraction(8, 200)) / 20)
    # 平手的廣告不算相異廣告
    assert (stats.raised_ads, stats.control_ads) == (20, 20)
    # 19 平手 + 1 正差:有方向數 1,不達下限(即使總配對 20、涉及 20 支廣告)
    stats = _stats([_pair(k, 0) for k in range(19)] + [_pair(19, 1)])
    assert (stats.pairs, stats.directed, stats.raised_ads) == (20, 1, 1)
    assert not rb.meets_floor(stats)
    # 重算:實際配對的平手也不進支持/反例
    raised, controls = _crowd(3, 3)
    tie = _ad("a09", raise_at=5, pre=3, post=4)  # 變化同對照 → 差值 0
    stats = _summary(_history([*raised, tie, *controls, _ad("c09", pre=3, post=4)]), key=RAISE_2)
    assert (stats.positive, stats.negative, stats.ties) == (3, 0, 1)


# ---- [S1513] ----
def _holdout(n_pos, n_neg, n_tie=0, pos=Fraction(1, 100), neg=Fraction(-1, 100)):
    pairs = [_pair(k, pos) for k in range(n_pos)]
    pairs += [_pair(n_pos + k, neg) for k in range(n_neg)]
    pairs += [_pair(n_pos + n_neg + k, 0) for k in range(n_tie)]
    return _stats(pairs)


def test_rule_mining_holdout_rule_is_frozen_and_recounted():
    frozen = (rv.MIN_DIRECTED, rv.MIN_DISTINCT_ADS, rv.HOLDOUT_SUPPORT)
    assert frozen == (20, 20, Fraction(3, 5))
    verdict = rb.holdout_verdict(_holdout(12, 7), rv.IMPROVE)  # 19 個有方向
    assert not verdict.kept and rb.INSUFFICIENT_SAMPLE in verdict.reasons
    verdict = rb.holdout_verdict(_holdout(11, 9), rv.IMPROVE)  # 11/20 < 3/5
    assert not verdict.kept and verdict.reasons == (rb.LOW_SUPPORT,)
    assert rb.holdout_verdict(_holdout(12, 8), rv.IMPROVE).kept  # 12/20 且平均差值正
    # 12/20 但平均差值 ≤ 0:方向不符
    verdict = rb.holdout_verdict(_holdout(12, 8, neg=Fraction(-2, 100)), rv.IMPROVE)
    assert not verdict.kept and verdict.reasons == (rb.WRONG_SIGN,)
    verdict = rb.holdout_verdict(_holdout(12, 8, neg=Fraction(-3, 200)), rv.IMPROVE)
    assert not verdict.kept and verdict.reasons == (rb.WRONG_SIGN,)  # 平均剛好 0
    # 19 平手 + 1 正差:總配對 20 仍不保留
    verdict = rb.holdout_verdict(_holdout(1, 0, n_tie=19), rv.IMPROVE)
    assert not verdict.kept and rb.INSUFFICIENT_SAMPLE in verdict.reasons
    # 未改善方向對稱
    assert rb.holdout_verdict(_holdout(8, 12), rv.NOT_IMPROVE).kept
    assert not rb.holdout_verdict(_holdout(8, 12), rv.IMPROVE).kept
    # 分母為零:未量
    verdict = rb.holdout_verdict(_stats([]), rv.IMPROVE)
    assert not verdict.kept and verdict.reasons == (rb.INSUFFICIENT_SAMPLE, rb.UNMEASURED)
    # 相異廣告不足也不保留
    few = _stats([_pair(k, 1, control="c00") for k in range(20)])
    assert rb.INSUFFICIENT_SAMPLE in rb.holdout_verdict(few, rv.IMPROVE).reasons


# ---- [S1515] ----
def _ranked_stats(key, n_pos, n_neg, pos=Fraction(1, 100), neg=Fraction(-1, 100)):
    pairs = [_pair(k, pos) for k in range(n_pos)] + [
        _pair(n_pos + k, neg) for k in range(n_neg)]
    return rb.stats_of(key, len(pairs), tuple(pairs), 0)


def test_rule_mining_exhaustive_order_is_frozen_and_symmetric():
    assert rv.K == 10
    a, b = ((rv.PRE_CVR, "pre_cvr:band_1"),), ((rv.PRE_CVR, "pre_cvr:band_2"),)
    # 17/21 比例較高,但 Wilson 下界 70/100 較高 → 70/100 先
    table = {a: _ranked_stats(a, 17, 4), b: _ranked_stats(b, 70, 30)}
    ranked = rb.rank(table)
    assert [r.key for r in ranked] == [b, a]
    assert ranked[0].wilson == scoring.wilson_lower(70, 100)  # 直接用既有函式與 Z_95
    assert ranked[1].wilson == scoring.wilson_lower(17, 21)
    # 兩方向對稱:鏡像的未改善得到同一下界與方向化平均
    mirrored = {a: _ranked_stats(a, 4, 17), b: _ranked_stats(b, 30, 70)}
    ranked_m = rb.rank(mirrored)
    assert [(r.key, r.direction) for r in ranked_m] == [(b, rv.NOT_IMPROVE), (a, rv.NOT_IMPROVE)]
    assert [r.wilson for r in ranked_m] == [r.wilson for r in ranked]
    assert [r.directional_mean for r in ranked_m] == [r.directional_mean for r in ranked]
    # 同鍵兩方向只留較高者;下界相同時依方向化平均、有方向數、鍵、方向代碼
    c = ((rv.SPEND_RATIO, "spend_ratio:band_1"),)
    d = ((rv.DAY_TYPE, "day_type:weekend"),)
    table = {c: _ranked_stats(c, 15, 10, pos=Fraction(1, 100)),
             d: _ranked_stats(d, 15, 10, pos=Fraction(2, 100))}
    ranked = rb.rank(table)
    assert [r.key for r in ranked] == [d, c] and len(ranked) == 2
    even = {c: _ranked_stats(c, 10, 10), d: _ranked_stats(d, 10, 10)}
    ranked = rb.rank(even)  # 下界、平均(0)、數量都相同 → 鍵升序、方向代碼升序
    assert [(r.key, r.direction) for r in ranked] == [(d, rv.IMPROVE), (c, rv.IMPROVE)]
    # 未達下限不入選;前 K 至多 K 條
    small = {a: _ranked_stats(a, 19, 0)}
    assert rb.rank(small) == ()
    many = {k: _ranked_stats(k, 20 + i, 5) for i, k in enumerate(rv.all_conditions())}
    top = rb.top_k(many)
    assert len(top) == rv.K
    assert top == rb.rank(many)[: rv.K]
    assert rb.top_k(many, 3) == top[:3]


# ---- [S1519] ----
def test_rule_mining_preflight_checks_all_fixed_seed_prompts():
    results = _preflight()
    assert tuple(r.seed for r in results) == rv.SEEDS  # 三批全列,不換種子
    for result in results:
        history = _generated(result.seed)
        sides = rh.split(a.ad_id for a in history.ads)
        table = rp.summary_table(rb.summarize(history, sides.explore))
        # 量的是完整系統提示加彙總表
        assert result.prompt_bytes == len(rp.SYSTEM_PROMPT.encode()) + len(table.encode())
        assert result.data_sha256 == rh.EXPECTED_DATA_SHA256[result.seed]
        assert [t.key for t in result.truths] == [t.key for t in rh.TRUTH]
        assert (result.explore_ads, result.holdout_ads) == (len(sides.explore),
                                                            len(sides.holdout))
    assert rp.preflight_problems(results) == ()
    assert rp.preflight_record(results) == rp.PREFLIGHT_RECORD  # 版本理由記的數字與重算一致
    # 第三批 20481 位元組:即使前兩批較小也整體失敗,並點名該批
    too_big = (*results[:2], dataclasses.replace(results[2], prompt_bytes=20481))
    problems = rp.preflight_problems(too_big)
    assert len(problems) == 1 and str(rv.SEEDS[2]) in problems[0]
    # 真模式/誘餌分母不可達也失敗
    first = results[0]
    unreachable = dataclasses.replace(first.truths[0], explore=rb.Reach(19, 19, 19))
    broken = (dataclasses.replace(first, truths=(unreachable, *first.truths[1:])), *results[1:])
    assert rp.preflight_problems(broken)
    # 少一批也失敗(不得只報兩批)
    assert rp.preflight_problems(results[:2])


# ---- [S1520] ----
def test_rule_mining_pairing_parameters_are_frozen():
    ids = [f"ad-{i:03d}" for i in range(7)]
    order = sorted(ids, key=lambda a: (hashlib.sha256(a.encode()).digest(), a.encode()))
    sides = rh.split(reversed(ids))
    assert sides.explore == frozenset(order[:3]) and sides.holdout == frozenset(order[3:])
    assert rh.split(ids) == sides  # 輸入順序不影響
    # 規模桶以前三日花費整數分
    assert [rv.scale_bucket(v) for v in (9999, 10000, 49999, 50000)] == [
        "lt_10000", "10000_49999", "10000_49999", "ge_50000"]
    # 兩筆同日同桶加額搶一支 C:固定鍵較前者拿到 C;重跑、換輸入順序結果相同
    first = _ad("a1", raise_at=5, pre=3, post=6)
    second = _ad("a2", raise_at=5, pre=3, post=6)
    c = _ad("c1", pre=3, post=4)

    def winners(ads):
        history = _history(ads)
        side = frozenset(a.ad_id for a in ads)
        pairs, missing = rb.pair_condition(RAISE_2, rb.side_events(history, side)[0],
                                           rb.control_index(history, side))
        return [(p.raised_ad, p.control_ad) for p in pairs], missing

    assert winners([first, second, c]) == ([("a1", "c1")], 1)
    assert winners([c, second, first]) == ([("a1", "c1")], 1)
    # 次序鍵先比 D 再比加額廣告編號(a0 < a1),不看輸入順序或操作識別碼
    early = dataclasses.replace(second, ad_id="a0", adjustments=(dataclasses.replace(
        second.adjustments[0], op_id="z", committed_at=second.adjustments[0].committed_at
        - timedelta(hours=1)),))
    assert winners([first, early, c]) == ([("a0", "c1")], 1)
    # 對照按編號 UTF-8 位元組升序取第一支未用者
    assert winners([first, _ad("c2", pre=3, post=4), c])[0] == [("a1", "c1")]
    # 不同規模桶或不同前三日轉換率區間的對照不配
    big = _ad("c1", pre=3, post=4, spend=20000)
    assert winners([first, big]) == ([], 1)
    other_band = _ad("c1", pre=9, post=9)  # 9/100 ≥ 5%,前三日率落在別區
    assert winners([first, other_band]) == ([], 1)
    # 凍結:評估版本雜湊涵蓋切分、對照池、配對欄位與切點、搶用次序、效果公式、K 與下限
    params = rv.version_params()
    for name in ("split", "control_pool", "pair_fields", "scale_cuts_cents", "event_order",
                 "control_order", "effect", "mean", "k", "min_directed", "min_distinct_ads",
                 "holdout_support", "ranking", "seeds", "cuts", "formats"):
        assert name in params, name
    assert params["seeds"] == list(rv.SEEDS)
    assert params["scale_cuts_cents"] == [10000, 50000]
    assert rp.version_sha256() == rp.EXPECTED_VERSION_SHA256
