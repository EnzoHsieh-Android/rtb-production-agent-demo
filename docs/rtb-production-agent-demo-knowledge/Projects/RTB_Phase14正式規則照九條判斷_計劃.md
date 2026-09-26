---
type: project
status: done
created: 2026-09-26
updated: 2026-09-27
plan_risk: high
summary: |-
  WHY: 2026-09-26 使用者裁定把 Phase 13 評估的九條標準答案搬進正式規則；Phase 13 名稱正常案例現行規則僅 12/36，沿用只看曝光點擊會誤提案。出處：本次使用者裁定、[[Issues/現行規則只看有沒有投放就提案]]。
  WHY: 2026-09-26 第 1 輪設計審後，AI 提案要經四查詢與九條否決；列內缺值判證據不足；分步證據按輪次隔離；政策升版擋舊提案；報告分列 AI 原始、AI+規則否決與程式規則。出處：本計劃〈使用者裁定〉5–6、〈審計修正紀錄〉r1。
  WHY: 2026-09-26 第 2 輪設計審與使用者裁定後，第 5 條七日任一天 no_data 即證據不足；DSP 預算調整改單源與 UTC 日期，A/B/C 進度由已提交列重算，AI 複查只定案一次，--hold-submit 攔全部提案出口。出處：本計劃〈使用者裁定〉7、〈審計修正紀錄〉r2。
  WHY: 2026-09-26 第 3 輪末輪把加額三天切點移到決策時鐘，金額改用整數分精確核對，模擬 DSP 跨日生成新桶，缺狀態沿用缺現況原因；折入後由代碼審把關。出處：本計劃〈審計修正紀錄〉r3。
  WHY: 2026-09-26 增量 3 範圍變更審後，使用者裁定 AI 退出正式與展示的加額決策，保留說明／告警假說；`--ai-judge` 撤除，AI 決策模組與舊調查錄製只供 Phase 13 評估重播及授權即時錄製，F5 考題與 hold-submit 撤除。出處：本計劃〈使用者裁定〉8、〈拆增量〉3、〈審計修正紀錄〉增量 3 範圍變更審 r1。
  DEP: 本案程式與測試引用索引（以程式碼為準；重查：rg --files src tests）：src/rtb/analyzer/policy.py、src/rtb/analyzer/dsp_client.py、src/rtb/analyzer/instrumented.py、src/rtb/analyzer/investigation.py、src/rtb/analyzer/runner.py、src/rtb/domain/proposal.py、src/rtb/dsp/store.py、src/rtb/eval/investigation_cases.py、src/rtb/eval/rubric.py、src/rtb/eval/scoring.py、investigation_cases.py、scoring.py、investigation_eval.py、generator.py、frozen_policy_e8b26f6.py、policy_before_samples.py、tests/analyzer/test_ai_judge.py、tests/analyzer/test_f4_end_to_end.py、tests/analyzer/test_policy.py、tests/analyzer/test_worth_check.py、tests/eval/test_evaluation.py、tests/eval/test_investigation_eval.py、src/rtb/eval/investigation_set.py、investigation_set.py、src/rtb/dsp/seed.py、store.py、tests/analyzer/test_dsp_client.py。
  DEP: 增量 3 逐項去留索引（以程式碼為準；重查：rg --files src tests）：ai_judge.py、basis.py、demo/flow.py、demo/recordings.py、driver.py、flow.py、flow_svg.py、observe.py、page.py、runner.py、test_ai_demo.py、test_model_entry_contracts.py、tests/demo/fake_recordings.py、tests/demo/test_ai_launcher.py、tests/demo/test_record_demo_batch.py、tests/model/test_recording_integrity.py、tests/test_spawn_boundary.py。
  DEP: 增量 3 範圍變更審 r2 補列依賴者（以程式碼為準；重查：rg -n 'ai_decide|NEVER_LIVE|AI_JUDGE_FLAG|ai_stop_grace_seconds|rule_round_opened_by_ai' src tests）：analyzer/flow.py、launcher/__init__.py、server.py、stepbudget.py、test_ai_judge.py、test_investigation_e2e.py、test_investigation_review.py、test_investigation_review_r2.py、test_observe.py、tests/analyzer/test_investigation_flow.py。
tags:
  - type/project
  - status/done
lands_in:
  - Systems/模型用戶端
  - Systems/分析行程流程與檢查點
  - Systems/任務流程領域模型
  - Systems/評估與Jev決策點
  - Systems/一鍵展示
  - Systems/展示頁面
  - Systems/Mock-DSP
  - Systems/追蹤檢視
  - Systems/正式九條判斷領域規則
  - Systems/確定性指標計算
---
# RTB_Phase14正式規則照九條判斷_計劃

PRIOR-ART: 最小解在既有 `rtb.domain` 的判斷點與精確指標、Phase 13 的四種唯讀查詢及收據；借用九條標準答案的順序、`WorthInput`、`exact_ratio`／`exact_change`、已驗證的 DSP 原始回應與現有任務租約，不引入新依賴或第二套評分表。外部一般做法是先收齊可核對的證據再依優先順序套確定性規則；此處可直接沿用專案已實作的評估規則，無須另建規則引擎。來源：[[Projects/RTB_Phase13AI參與決策_計劃]]、[[Projects/RTB_Phase10評估與Jev決策點_計劃]]，以及本次程式前掃。
RETIRE-IF: 正式環境抽樣、人工標註的隱藏集證明這九條在任一危險格反覆誤提案，應撤回或重訂相應條件；若決策點改由人工核可，撤掉這套自動判斷及追加查詢；若外部資料契約不再供應四種查詢，重新設計證據門檻，不能暗中退回舊規則。事件入口：每次 [[Systems/評估與Jev決策點]] 的新隱藏集驗證、DSP 端點契約變更或決策路由改版。

## 這份計劃在解決什麼

- 正式路徑的 `policy.code_rule` 只看 1 小時曝光與點擊，有就判值得加。Phase 13 名稱正常的 36 筆案例只對 12 筆；問題與使用者裁定見 [[Issues/現行規則只看有沒有投放就提案]]。此計劃修正式規則、蒐證與相關展示和報告，不在本次改程式。
- 要防的是資料不齊或剛調完預算時，舊規則仍產生加預算提案，經人工確認後才被寫入模擬 DSP。九條規則的「證據不足」必須在正式路徑可達，而不能只存在於評估標準答案。

## 使用者裁定

### 2026-09-26 使用者本人

1. 把 Phase 13 標準答案的九條順序與判法搬到正式規則，門檻沿用 Phase 13 裁定 12 的數字：最近 3 天、轉換率低於一半、最近一次加預算後 3 天轉換不多於前 3 天。不得改數字。
2. 規則模式也要取得 `check_longer_window`、`check_change_history`、`check_daily_trend`、`check_past_adjustments` 四種追加資料。
3. DSP 查詢逾時、404、欄位不合格、跨窗不一致等被讀取層記成「沒有結果」時，結論是證據不足、不提案。資料完整但分母為零等令某條比率算不出時，僅該條不適用、續判下一條，沿用標準答案函式語意。
4. F4、F6 的另一方剛改預算後接續任務，應依第 3 條落到證據不足、不提案；展示斷言及端到端測試同步調整，[S1168] 放寬為程式規則判的證據不足也算。
5. **失效（2026-09-26 代使用者裁定）**：原「開 AI 模式、AI 可參與提案但須經九條否決」僅描述已完成的增量 2b 與 Phase 13 歷史評估，不再是正式或展示執行路徑。理由：裁定 8 讓 AI 退出加額決策；舊錄製的 AI 原始與 AI+規則否決仍在評估報告分列，不拿來核准送件。例：模型舊錄製答 `propose`、案例顯示昨天調過預算 → 評估仍記 AI 原始 `propose`、規則第 3 條證據不足；正式分析不呼叫模型且不提案。
6. 查詢有回來但列內用到的欄位缺值（如某日 `conversions=null`、`no_data=false`，或最近一次加預算的 `before_conversions=null`）時，用到該值的第 4／5／6 條判「證據不足」，不可當作不可算而跳過；只有所需資料齊全、分母為零使精確比率不可算才跳過該條。標準答案函式同步改為此語意；現有 72 筆無缺值，逐筆的格與答案應不變，須用缺值翻紅案例與 72 筆回歸測試釘住。`SYSTEM_PROMPT` 位元組不得改，因它是錄製鍵；缺值分流由讀取後的程式規則執行，不添進提示。例：最近三天其中一天 `conversions=null`、其餘數字會使第 8 條值得加 → 第 5 條證據不足、無提案；三天點擊全為 0 但轉換欄齊 → 第 5 條不可算、續判。
7. 逐日資料裡第 5 條需要的七個完整日（最近 3 天與前 4 天）只要有一天 `no_data=true`，第 5 條就判證據不足，不排除那天、不用剩下六天加總後續判。已核對評估集 72 筆中 `no_data` 日為 0/72，標準答案逐筆不變；須以單日 `no_data=true`、其他六天齊全且第 8 條本可提案的翻紅例，以及 72 筆回歸測試釘住。

8. AI 退出「要不要加預算」的決定,只留三個角色:提案說明、告警原因推測、找新的規則模式(後者另開 [[Projects/RTB_Phase15AI找規則模式_計劃]])。理由(使用者與協調者討論後):九條是程式能精確算出的商業規則,模型在這一段最好也只是跟規則一樣、還會出錯,沒有非它不可的位置;AI 放在規則寫不出來的判斷。這觸發 [[Projects/RTB_Phase13AI參與決策_計劃]] RETIRE-IF 第一條(使用者決定展示不再讓 AI 影響要不要提案,退回純程式規則,只留說明與假說)。

### 2026-09-26 協調者技術取捨（代使用者裁定）

- 九條的順序、判定與可展示的規則文字以 `rtb.domain` 新模組為單一來源。它收正式形狀的 `WorthInput`、四種已驗證原始查詢與同一個 `now`，不收評估 `Case` 字典；評估標準答案與正式規則由它產生，提示只核對九條順序與三類結論，缺值／`no_data` 豁免見 [S1408]。分析端不匯入評估套件。
- 第 4、5 條以精確分數比較；來源選同一輪蒐證中經 DSP 白名單驗證且與收據一起存下的原始回應。捨入收據只供模型與人閱讀，不用來判門檻。下文將 [S1130] 列為要改寫的合約。
- 「最近 3 天」以做出最終決策的 `now` 為準，與新鮮度檢查用同一時間。暫停與資料異常先用 1 小時資料判，其餘才要求四查詢。
- 現況狀態缺值或不屬 active／paused 時，不建立 `WorthInput`，正式路徑以 `MISSING_STATE_OR_METRICS` 不提案；理由是現有 `policy._payload()`／`steps()` 已把缺可信現況導向此原因，毋須把無格輸入硬塞進九格的證據不足。基本讀取層須將 200 回應中不合格的狀態轉為缺現況診斷並提交可結案結果，不能拋錯後無限重試；5xx／連線失敗仍照純讀取重試，絕不退回「曝光點擊正數就提案」。
- **改寫（2026-09-26 代使用者裁定）**：正式及展示分析一律進九條規則輪，不存在 AI 退回或 AI 期查詢；暫停與資料異常仍先用基本資料結案，不能被配速門檻掩蓋。Phase 13 評估僅保留 Judge 解析、舊錄製重播與案例九條比較；原先「退回與否決分支留評估」的判斷已由本次增量 3 範圍變更審 r2 撤除，見〈AI 決策退出正式路徑與展示〉。
- 領域模組自己定義四查詢的純資料型別及判定結果型別；分析端把白名單驗證後的 `QueryRead`／原始 dict 轉成領域型別，`rtb.domain` 不匯入 `rtb.analyzer.dsp_client`。新領域檔的家預定為 [[Systems/正式九條判斷領域規則]]，增量 1 建檔時同時建立該 Systems 篇。
- 原本列在 Phase 10／13 的舊合約與測試，不應靠改標準答案維持綠燈；落地時逐條重寫或撤除並留下翻案指向。

## 現況（2026-09-26 前掃；以程式碼為準）

- `src/rtb/eval/investigation_cases.py` 的 `answer()` 依序判九格；`src/rtb/eval/rubric.py` 只認 Phase 10 自己的 5 格答案,`investigation_cases.py` 的 `_PHASE10`／`VERDICT` 再把它們映到九格裡的第 1、2、7、8、9 格(不連續),其餘四格(裁定 8、裁定 12 三條)的答案直接寫在 `VERDICT`。第 4 條使用最近一筆加預算的調整前後 3 天轉換變化，第 5 條使用逐日資料分段轉換率的變化；比率不可算時跳過該條。重新核對：`rg -n 'def answer|def raise_change|def trend_rate_change|RUBRIC' src/rtb/eval`。
- `src/rtb/analyzer/policy.py` 的 `code_rule()` 只看曝光、點擊正數；`_judge()` 在 `WorthInputInvalid` 時也用同一舊判法；`steps()` 先做新鮮度與配速再判是否值得加。`build_proposal()` 只用傳入的證據列出參照，現行正式呼叫傳基本三筆。重新核對：`rg -n 'def code_rule|def _judge|def steps|def build_proposal' src/rtb/analyzer/policy.py`。
- `src/rtb/analyzer/dsp_client.py` 已把四查詢的逾時、404、欄位不合格及 1d／7d 跨窗不一致分成 `QueryRead` 的沒有結果；5xx 與連線失敗會丟例外，仍沿用純讀取步驟下次重試。`src/rtb/analyzer/instrumented.py` 目前只在 AI 調查路徑重讀模型選過的查詢；`src/rtb/analyzer/investigation.py` 的 `CODE_RULE_KINDS` 會排除追加收據。重新核對：`rg -n 'class QueryRead|def make_query_reader|def investigation_source|CODE_RULE_KINDS' src/rtb/analyzer`。
- `src/rtb/analyzer/runner.py` 非 AI 模式以「2 次 × 逾時 × 2 < 60 秒」守租約；AI 模式蒐證守衛計入取租約等鎖 5 秒、每次 DSP 逾時與呼叫紀錄等鎖 5 秒、提交等鎖 5 秒。四種查詢共 5 次 HTTP，加上基本現況與 1 小時指標共 7 次；預設 DSP 逾時 3 秒時單步上界 `5 + 7 × (3 + 5) + 5 = 66 秒`，超過租約 60 秒。重新核對：`rg -n 'def _unsafe|collect_step_worst_seconds|MAX_COLLECT_READS|READS_PER_OPTION' src/rtb/analyzer`。
- 展示種子 1 小時有 1 筆轉換、逐日平穩、沒有既往調整；除 F4／F6 接續任務，九條下原展示結局預期不變。F7 300 件、8 個工作者、情境時限 300 秒。重新核對：`rg -n 'seed_platform|DEMO_PROFILE|F7_CAMPAIGNS|F7_WORKERS|timeout_seconds' src/rtb/demo src/rtb/dsp`。

## 設計

### 九條判斷與單一來源

| 順序 | 條件（先命中先回） | 結論 |
|---|---|---|
| 1 | 狀態 paused | 不值得加 |
| 2 | 1 小時曝光、點擊、轉換、花費、營收缺值或負數，或點擊多於曝光、轉換多於點擊 | 證據不足 |
| 3 | 最近 3 天內有預算調整 | 證據不足 |
| 4 | 最近一次加預算後 3 天轉換不多於前 3 天，且所需欄位齊全、精確變化算得出 | 不值得加 |
| 5 | 最近 3 天轉換率低於前 4 天的一半，且所需逐日欄位齊全、精確變化算得出 | 證據不足 |
| 6 | 1 小時有點擊而轉換、營收均零，1d 或 7d 有轉換 | 值得加 |
| 7 | 1 小時沒有曝光或點擊 | 不值得加 |
| 8 | 1 小時有曝光與點擊，轉換或營收為正 | 值得加 |
| 9 | 其餘有曝光與點擊、沒有價值的情形 | 證據不足 |

- 第 1、2 條只需基本資料；狀態缺值或非法時先由基本讀取層記缺現況，`policy.steps()`／`explain()` 以 `MISSING_STATE_OR_METRICS` 不提案、不進九格；C 若才讀到缺狀態也須作廢同輪 A 的舊現況，不可從 `_payload()` 拿舊 state 繼續定案。其他已驗證基本欄位能建 `WorthInput` 才進第 1／2 條。第 3–9 條要求四個查詢都有**有效原始結果**；任一沒有結果，即使現有值似乎足夠推到較後規則，也回證據不足。第 1、2 條既已先決定，不因後續查詢缺失而重判。最近 3 天的歷史切點沿用原函式的嚴格 `committed_at > now - 3 days`，剛好滿 3 天不算近期。歷史／過去調整成功回空列代表沒有相應事件，第 3／4 條不命中；逐日該有的天數或用到的列內欄位缺值，以及 1d／7d 所需轉換欄皆無法確認時，對應第 4／5／6 條回證據不足。即使後面第 8 條可能提案也不得跳過。只有資料齊全而精確比率分母為零，才跳過第 4／5 條。例：最近一次加預算列 `before_conversions=null` → 第 4 條證據不足；`before_conversions=0` 且其餘欄齊 → 第 4 條不可算、續判。
- 領域函式回傳 `RuleDecision(verdict, cell?, reason)`：正常九條帶 `Cell`；四查詢沒有結果、跨查詢矛盾、列內缺值等九格之外的證據不足帶 `cell=None` 與穩定診斷原因，不能假裝命中第 9 格。`reason`、退回原因與無結果原因一律為封閉 `StrEnum`，查詢代碼／條號用獨立欄位或有限枚舉值表達，不拼自由文字。正式診斷仍落 `JUDGED_INSUFFICIENT` 並保存細因；Phase 13 評估正常 72 筆映回既有九格，若出現無格結局應明報「非九格／缺證據」，不得偷映到第 9 格。Phase 10 舊案例則由明確的缺四查詢轉接回證據不足並標舊資料不足。九條描述、選項代碼、`RECENT_DAYS=3`、門檻與收據的 `budget_changes_last_3d`／逐日分段共用領域常數或同一分段函式；跨模組測試核對同值。`rtb.domain` 自定義資料型別，分析端轉換後傳入，保持 `rtb.eval` 可依賴 `rtb.domain`、`rtb.analyzer` 不依賴 `rtb.eval`，領域層也不依賴 `dsp_client`。第 4／5 條經 `metrics.exact_change`／`exact_ratio` 的分數結果判 `<= 0` 與 `< -1/2`；第 5 條只用最近七個完整日，任一日 `no_data=true` 或必要欄位缺值先判證據不足，七日齊全才各自加總轉換與點擊計算前四日、後三日轉換率。
- 保留新鮮度、配速、提案金額與權限守衛的原來職責。基本資料先驗狀態與異常，再判新鮮度與配速；配速算不出或不偏低在步驟 A 結案、不讀四查詢、不提案。為避免異常被配速提前遮蔽，診斷原因優先呈現第 1／2 條；同一筆需要查詢的資料在查齊、通過新鮮度與配速後才可提案。例：active、資料齊、配速為正常 → A 結案且追加讀取 0 次。

### 正式蒐證、時間與租約

- 規則查詢使用既有 `COLLECTING_EVIDENCE → ANALYZING → COLLECTING_EVIDENCE` 合法轉換，不新增平行任務狀態，也不持久化 `next_rule_step`。原始查詢列與收據同交易提交 `rule_round_id`、步驟 A/B/C、任務序號、讀取時刻、版本；重來事件列記輪次與連續變動次數。每次分析都仿 `investigation.progress()` 從已提交列重算進度：取最新未作廢輪次，按同輪已提交的完整步驟集合算出「尚無 A→A、只有 A→B、有 A/B→C、A/B/C 齊全→DECIDE」；缺步或重複只取同輪最新有效序號，不靠待寫的下一步欄位。正常 A→B→C 要有專屬「規則查詢續步」原因，不畫成資料過期重蒐證。步驟 A 讀現況與 1 小時指標（2 次）；200 回應的狀態缺值／非法由 `dsp_client` 記缺現況（不造可信 state 證據），`flow` 提交診斷而非留在純讀取重試，分析步以 `MISSING_STATE_OR_METRICS` 結案；其他白名單不合格、5xx／連線故障仍照各自既有重試語意。其餘依序判 paused／資料異常、新鮮度、配速；可續判才排 B/C。B 讀操作歷史與過去調整（2 次）；C 讀逐日、1d、7d，並重讀現況與 1 小時指標（5 次）。`flow` 最終分析與 `basis.analysis()` 按同一輪的 A/B/C 序號取證，只准一輪各查詢最新且已驗證的結果，禁止只用最後一列或混入 AI 期／別輪資料。例：B 已存於序號 4、C 在序號 6 → 決策讀同輪序號 4 和 6；409 退回後新輪序號 9 不得拿序號 4 的歷史。
- 最終判斷以決策 `now` 重驗所有證據 15 分鐘新鮮度。當機續跑時先從已提交列重算進度：同輪證據仍新鮮才從算出的下一步續跑；任何過期、提案送件 409 退回蒐證、C 發現版本或基本資料變動，都開新 `rule_round_id` 並**整組從 A 重讀**，舊輪只留審計、不參與決策。A 與 C 比廣告編號、狀態、版本及 1 小時五項原始指標；同一件工作因版本／基本資料變動連續重來最多 **2 次**，第 2 次重來後再變動就以 `JUDGED_INSUFFICIENT` 結案，避免即時指標連續更新造成無限迴圈。選 2 次可容納一次競爭後的穩定重讀，又把最壞重讀額外限制為 2 組；實作測試以可控時鐘與變動序列驗證；展示 F1–F7 照現有行程使用真實時鐘，跨日穩定性由 DSP 日期桶滾動與跨午夜測試保障。例：A=版本 1、C=版本 2 → 新輪 A；新輪仍兩度變動 → 證據不足，沒有第 4 輪。
- C 逐日查詢的讀取時刻與最終決策 `now` 比 **UTC 日期**；不同日就判證據不足，不用前一天的桶提案。模擬 DSP 逐日資料改用 UTC 日期作鍵，C 讀最近七個完整日；正式 DSP 接入前也要定義桶時區與快照版本。逐日對 1d／7d 的一致性與既有長窗跨窗核對都放在 `dsp_client` 讀取層同一處：C 讀完逐日、1d、7d 後核對最近一天與 1d、七天逐日合計與 7d 的曝光、點擊、轉換、花費、營收；計數仍用整數直接比。模擬 DSP 對花費／營收以整數最小貨幣單位（分）存入 SQLite，逐日與 1d／7d 窗均由分數相加；HTTP 回應將分轉成固定兩位小數字串（如 `"0.10"`、`"0.20"`、`"0.30"`），讀取白名單、領域轉接與評估轉接都先將該字串轉 `Decimal`／`Fraction`，在同一精確表示下核對，不在寫窗或讀窗時做浮點 `sum`。舊評估數字以 `Decimal(repr(value))` 正規化到分後走同一核對；收據維持既有顯示格式與錄製鍵，另測投影雜湊。例：兩日 `0.10+0.20=0.30`，7d `0.30` 有效，不能把同源資料判 `invalid`。`no_data` 日只在核對 7d 窗時按 DSP 窗口契約視為零貢獻；第 5 條仍依裁定 7 判證據不足，不排除那天後續判。任一必要欄缺失或不等，讀取層將查詢記 `invalid` 並附封閉原因，領域與評估使用同一已驗證結果；評估的案例轉接也要經同一純核對函式，不可繞過。若真實 DSP 提供同一快照／版本，也須逐欄測約束。例：逐日七天點擊合計 700、7d 回 710 → 讀取層回沒有結果、無提案。
- 每步租約上界沿用 AI 蒐證的保守算式：`5 + N × (DSP 逾時 + 5) + 5 < 60`。預設逾時 3 秒時 A=`5+2×8+5=26`、B=`5+2×8+5=26`、C=`5+5×8+5=50` 秒，皆小於 60 秒；runner 啟動時對 A／B／C 分別檢查，不把九次放在單步，也不靠續租掩蓋超時。若使用者把逾時設高，守衛須拒啟動並說明哪一步不符。啟動器收到停止訊號後的規則模式寬限改取 `max(舊兩讀寬限, max(A,B,C) 的最壞秒數)`，預設至少 **50 秒**；AI 模式再與 [S1136] 的續租等鎖＋AI 步上界取最大值（現值 60 秒）。三者共用 `stepbudget` 常數，改啟動器 [n3] 與測試，確保 C 中途不被硬殺。
- 查詢過程任何「沒有結果」保留查詢代碼與原因的收據，最後落 `JUDGED_INSUFFICIENT`、不送提案；其他外呼故障沿用純讀取重試，不誤寫成「無結果」。決策、展示重算與診斷原因都使用同一輪已存原始查詢，不能各自重打 DSP 得到不同答案。
- 提案的 `evidence_refs` 選擇列入四種成功查詢的收據，以及基本三筆；理由是正式規則的結論可能由歷史、趨勢或長窗支持，提案應可追溯。沒有結果的查詢不得產生提案。調整 [S1107] 的「逐欄相同」比較範圍：AI 與規則對同一批完整證據仍須一致，既有基本三筆的舊快照需改；不能放入原始資料庫列的內部鍵或不可信名稱內容。
- 模擬 DSP 在正常 `update_budget` 更新廣告的**同一交易**寫唯一調整紀錄，至少存廣告、調整前預算、調整後預算、`committed_at` 帶時區時間戳，以及可供歷史端點投影的操作 ID／版本／冪等鍵；正常寫入用操作 ID 關聯既有審計列。種子也只寫這張調整表，按時間先後套用預算並生成上述歷史欄位，不再另寫 `past_adjustments` 種子表與一份種子操作紀錄，故無合併去重。正常操作仍保留既有 `operations` 審計列，但預算事件在 `check_change_history` 從唯一調整表投影，其餘暫停等事件才從 `operations` 讀，避免同一加額出現兩筆；種子預算調整同樣可被歷史端點看到。舊資料遷移先從舊種子與舊操作可核對的列補入，無法證明調整前預算的舊列不得猜測或靜默當「零筆」，須標為缺證據並測遷移。`check_past_adjustments` 依調整前後預算在**全量**紀錄找最近一次加額，不讓後來的減額擠掉；端點不按 DSP 查詢時刻篩三天，只回最近一筆加額（仍低於五列上限），含帶時區 `committed_at`、調整前後預算與 D 前後各三個完整 UTC 日的成效；後窗未滿三個完整日時 `after_conversions=null`。讀取層 `ADJUSTMENT_ROW_FIELDS` 移除 `MIN_ADJUSTMENT_AGE_DAYS` 的拒收條件，保留 `days_ago` 作收據顯示，時間界線只由領域以最終決策 `now` 判：未滿三天先按第 3 條歷史語意證據不足（以調整紀錄補足截斷歷史的預算事件）；剛滿三天但後窗未完整，按裁定 6 在第 4 條判證據不足。端點序列化、讀取白名單、領域轉接與原合約同改。
- 模擬 DSP 的逐日表改用 UTC `date` 為鍵，保留最近 **30 個完整日**。展示種子除七日初值外，另存每廣告可重播的完整日樣板與最後已生成 UTC 日期；每次跨 UTC 日界，剛結束的每一天由樣板推出完整日桶（沒有樣板的廣告就是沒資料，不照抄前一天），1d／7d 窗由同一批整數分桶加總，跨多日也逐日補齊；讀取端只在記憶體以「已存日桶＋樣板＋一次時鐘讀數」推算、不寫資料庫，持久化與保留由寫入端（種子、改預算）在同一寫入交易做，寫下的值與讀取推算相同（2a 代碼審 r1 協調者裁定：讀取不得拿寫入鎖）。當日未完成桶不進最近七個完整日；每個讀取端點只讀一次時鐘，整個回應用同一日期快照，不能只滾動保留卻不產生新日。展示仍用真實時鐘；在 UTC 午夜（台北 08:00）前後各啟動一次完整 F1–F7 蒐證，預期都有七個完整日且不因缺新日判證據不足；若單次 C 恰跨日，仍依 [S1406] 重讀／拒用舊日證據。另釘住「該廣告最近一次加額」所需的六個日桶直到被更新的加額取代；30 日覆蓋既有評估最舊 15 天前調整及前後各 3 天（最早需 18 天前），釘住六桶避免長跑後第 4 條因保留期永久缺前窗。調整日 UTC 日期記 D；D 當天混有調整前後流量，**兩段都不算**；前段 D−3、D−2、D−1，後段 D+1、D+2、D+3，各三個完整日。端點由日期表算 `before_conversions`／`after_conversions` 等前後五欄；後窗未完、任一必要日不存在、`no_data` 或欄位缺值時，仍回該筆且對應欄為 null，第 4 條依裁定 6 判證據不足，不能回 `rows=[]`。若加額恰滿 3×24 小時但 D+3 尚未完，也維持 null。例：D 為 9 月 20 日，前段 17–19 日、後段 21–23 日；9 月 24 日 00:00 UTC 以後三日完整且轉換 10→10 → 第 4 條不值得加；若 23 日桶缺值 → 第 4 條證據不足。
- `check_change_history` 查最近 **7 天**、最多 **50 筆**，只將 50 筆放進既有 `history` 清單以守住 64 KiB 回應；超過上限時端點另帶由完整七天集合算出的 `recent_budget_change`、預算及暫停計數與 `truncated=true`，讀取層白名單核對並讓第 3 條用完整集合的近期旗標，不能從截斷清單推「沒有近期調整」。未截斷時維持既有 `{"history": […]}` 形狀與收據字串；截斷時收據依摘要計數且標截斷，避免誤導 AI。F4／F6 另一方剛寫的操作必在最近 7 天與第 3 條旗標內，因此是第 3 條證據不足，不是過去調整端點無結果。`check_past_adjustments` 新時間戳不加進既有 `adj*_...` AI 收據欄；既有錄製使用凍結提示與舊案例的收據投影；`adj*_...` 收據字串不含 `committed_at`，由 `days_ago` 推出的時間戳不改該字串，錄製鍵與錄製內容照舊可重播。以 F1／F7 錄製鍵雜湊、Phase 13 全錄製重播及新端點白名單／摘要測試釘住；正式新回應形狀與重生評估集都帶 `committed_at`，收據投影與凍結 `SYSTEM_PROMPT` 不變。
- 九條改版時同步提高 `POLICY_VERSION` 並把舊值留在 `KNOWN_POLICY_VERSIONS`，分析端新提案使用新值；提案送件與執行端沿用 Phase 8 [S504] 的政策版本檢查，舊政策下待確認／待執行的提案即使廣告版本未變、仍在 30 分鐘期限內，也擋 `POLICY_VERSION_CHANGED`。升版當下仍在分析中、只有舊版基本證據或沒有新輪次列的任務，一律先開新規則輪從 A 全量重讀；不可把缺輪次誤當 DECIDE。九條版停在 B/C 的任務遇回退版也要作廢該輪、按回退政策從新基本步重讀，不使用只有部分步驟的最後序號。例：舊版對近期調整建立提案，九條版上線後才人工核可 → 執行端不寫 DSP；舊版在途分析任務 → 新輪 A，不能用舊兩讀直接建提案。

### AI 決策退出正式路徑與展示

- **失效（2026-09-26 代使用者裁定）**：原 AI `propose`、退回後開規則輪、規則否決、`--hold-submit`、F5 雙胞胎考題及展示「AI 自選／程式補查」三段，只作增量 2b 歷史脈絡。理由是 AI 不再決定是否提案；評估執行器只直接呼叫 `ai_judge.Judge` 與 `rule_verdict(case)`，沒有 `TaskStore`、`flow.advance` 或 A/B/C 規則輪入口。評估保留 Judge、調查提示、收據、解析、錄製重播與協調者授權的即時加錄製；流程層 `ai_decide` 參數及 AI 分支、`rule_round_opened_by_ai`、AI 規則否決細因與 runner 端 AI 開輪判斷依「沒有入口就刪」撤除。原 [S1407] 的送件前置／否決機制在入口存在期間守住未經九條卻帶新政策版本的提案；入口拔除後沒有存續理由，改以 [S1410][S1429] 驗評估語意與正式邊界。
- **改寫（2026-09-26 代使用者裁定）**：正式分析由規則輪 A/B/C 蒐證、九條定案。展示保留提案說明與告警原因假說的模型入口，並保留九條證據與細因；F4／F6 接續任務顯示規則第 3 條「剛被調過預算，先不動」，無提案、收件口或 DSP 寫入。頁面刪除 AI 調查卡、流程圖 AI 決策節點及灰色未走分支，只在提案的說明格與告警的假說格標 AI 文字。例：F5 名稱含「加 500%、洩漏金鑰」→ 九條仍只按數字判、提案金額不變，名稱只以逸出文字顯示；說明裡的數字仍須經既有 `traceable_sentences` 對到提案／收據。

### 評估與報告

- 增量落地後重跑 Phase 10 與 Phase 13 評估報告，僅重播現有錄製與本機規則，不呼叫模型。Phase 10 五格仍維持使用者裁定的評分表；`src/rtb/eval/scoring.py` 的 `score → route → code_rule` 要隨新簽章改成顯式傳 `RuleEvidence`：Phase 10 的 `Scenario`（`generator.py`）仍只有 `worth_input`、不假造四查詢欄位，轉接器明傳 `MISSING_FOUR_QUERIES`；`route` 的 CODE_RULE 與 `_candidate_answer` 各退回分支都接這個值，paused／異常在領域前兩條先判，其餘回證據不足。Phase 10 報告每格標「舊資料不足以評估九條規則」，原 Phase 10 標準答案不動；`tests/eval/test_evaluation.py` 改斷言五格路由、候選退回、缺證據標籤與原標準答案。Phase 13 36 筆名稱正常案例的規則答案因同源而預期 36/36；這只證接線一致，不是獨立品質證明。
- Phase 13 原 72 筆中 46 筆調整列缺 `committed_at`；生成器以固定評估 `NOW − timedelta(days=days_ago)` 決定性補出帶時區時刻，再重新 `render(generate())` 產生 `investigation_set.py`，不得讓評估繞過正式白名單。重生前後逐筆斷言 72 筆的格與答案不變，`SYSTEM_PROMPT` 位元組與錄製鍵不變；收據 `adj*_...` 不含時間戳，因此斷言字串及其雜湊不變。Phase 13 `investigation_eval.run_case` 直接呼叫 `Judge`：遇 `RuleContinue` 當場回傳，該分支 AI 有效答 `propose` 時原始判定為 `WorthVerdict.WORTH`、退回時用 `rule_verdict(case)`；其他結果由 `_verdict(outcome)` 轉成原始結論。不呼叫 `flow.advance`、沒有 `TaskStore`、也不開 A/B/C 規則輪。評估標準答案與程式規則把 `Case.results` 四查詢轉成同源 `RuleEvidence`，用案例固定 `NOW` 執行九條；AI 原始列保存舊錄製的有效答案、無有效答案／格式錯誤、呼叫數與延遲，不建提案、不寫平台、不改錄製鍵。`CaseRun`／報告分開持有 `ai_raw` 與 `code_rule`；若保留第三列 `ai_with_rule_veto`，明定它只是**報告層**把 AI 原始 `propose` 與案例九條結果相交的派生比較，否決原因取九條細因，不產生 `ai_propose_vetoed:` 事件、規則輪或送件語意；無有效原始答案不得填成 AI 回答。`_verdict` 遇到正式前置過濾要記來源與理由，不丟 `ValueError`。例：異常案例舊錄製 AI 答 `propose`、九條判證據不足 → AI 原始算 1 筆誤提案，派生比較列為無提案，程式規則列證據不足。
- 兩份報告逐格保留分子、分母、錯誤子型；Phase 13 醒目分列「AI 原始」「AI+規則否決」「程式規則」，逐列寫**誤提案筆數／應不提案筆數**與各自分母；無有效 AI 原始答案的筆數另列，不能算成 AI 答對。原始錄製的數字一律以重播結果為準，不把舊報告含退回規則的數字當作 AI 原始。`AI+規則否決` 在這份評估集與標準答案共用同一規則，誤提案數依構造恆為 0；該列及規則 36/36 都醒目註明「同源，不作品質證據」，對抗雙胞胎切片同樣分列三種來源、以 AI 原始列觀察模型品質。延遲欄名與量法都標單位：判斷點本機計算用微秒、模型呼叫錄製觀測用毫秒、整段正式蒐證另報毫秒，不混成一個「延遲」。保留原錄製批次、雜湊、原始品質及既有「不採用」結論；同源 36/36 不能改 AI 採用判定。

## 拆增量

### 實作解讀（2026-09-26，增量 1）

增量 1 已建立四查詢純領域型別、九格判斷及無格封閉原因,評估標準答案改從該模組取得,收據三天切點共用領域常數。72 筆含雙胞胎逐筆格與答案維持,列內缺值、單日 no_data、分母零與精確門檻已有翻紅後轉綠的離線測試;正式 `policy.code_rule` 與送件流程留到增量 2。驗證:全套 3249 筆在 2026-09-26 通過(代碼審前),代碼審兩輪 10 條:9 條修入、1 條放行。

增量 2 必做(代碼審 r2 放行的條件):領域 `Window`／`DailyRow` 的字串金額分支目前寬於 `WorthInput` 與 DSP 白名單(`Fraction("5/2")` 也能過),增量 2 照 [S1414] 收緊成只收固定兩位小數字串並補測試。另(2a 代碼審 r1 架構對齊第 3 條、r2 追記):過去調整的 `committed_at` 已進 DSP 回應與讀取白名單,但 2a 還沒有讀取端消化它;增量 2b 的領域第 3/4 條要消化 `committed_at`(以決策 now 判近期與 D+3 是否完整),不再只靠 `days_ago`。

### 實作解讀（2026-09-26，增量 2 進行中）

以下 2b 條目記錄當時已做的實作與代碼審，不是增量 3 之後的正式／展示入口要求；凡提到 AI 決策、`--hold-submit`、展示補查或雙胞胎者，均由〈拆增量〉3 與 [S1407][S1409][S1411][S1420][S1421] 的 2026-09-26 代使用者裁定取代。評估重播仍保留舊模組與錄製。

先以 `test_fixed_two_decimal_amount_strings` 翻紅確認領域字串金額會誤收分數寫法，再收緊 `Window`／`DailyRow` 為固定兩位小數字串。領域子集 337 筆、Ruff 與該領域檔 mypy 通過。正式規則接線、分步蒐證、DSP 單源與日期桶、評估轉接及原合約改寫尚未完成；未啟用新政策，不能宣稱增量 2 已驗收。

增量 2a（2026-09-26）：已把 DSP 預算事件統一到操作紀錄，逐日改 UTC 日期桶、整數分金額、30 完整日與最近加額 D±3 保留；讀取時推算新完成日桶與 1d/7d（代碼審 r1 後讀取不寫資料庫，見下方修正紀錄），同一步已有長窗時由讀取層以 Fraction 核對跨窗。歷史回 50 列上限與完整七日近期預算摘要；過去調整回最新加額、帶時區提交時刻與 D 前後三完整日，未滿三日後段留空。評估 72 筆已重產並驗收據與 SYSTEM_PROMPT 不變。相關 [S1414][S1415][S1422][S1423][S1425][S1426] 測試先紅後綠；Phase 13 相關既有合約與各程式家已改寫。正式九條決策、分步蒐證、政策版本、展示完整重播屬後續子增量，仍未啟用。離線紅綠、72 筆入庫錄製重播與需綁埠測試的界線見 [[Verification/Phase14增量2a離線驗證]]。

2a 代碼審 r1 修正（2026-09-26，九席發現全數處理；協調者定修法方向）：
- 讀取不寫資料庫：逐日、1d/7d、過去調整、歷史四種讀取只在讀取快照裡讀、在記憶體推算剛完成的日桶與視窗，別的連線握著寫入鎖時照樣可讀；持久化與 30 日保留改由寫入端（種子、改預算／暫停）在同一寫入交易做，最新日期在拿到寫入鎖後才讀。每個讀取端點只讀一次時鐘。
- 新完成的日子只由展示樣板推出；沒有樣板就是沒資料，不再把最新一天（含沒資料那天）照抄下去。展示種子帶樣板，[S1426] 跨午夜仍有七個完整日。七天全沒資料時沒有 7d 窗（404，同舊種子）。
- 金額單一來源：分析端固定兩位小數金額的判準只在 `rtb.domain._checks`（ASCII 數字、整數部分最多 13 位、可帶負號），讀取白名單、指標收據、九條共用；讀取層跨窗比較回到領域 `exact_value` 三態語意，負數不參與比較（恢復原行為，收據那欄 na）。DSP 是外部系統模擬器，依既有邊界自留一份同值上限（整數分 ≤ 10^15−1），展示種子改用 DSP 自己的分值函式。13 位而非 15 位的理由：13 位整數加兩位小數是 15 位有效數字，1 小時基本讀取轉浮點再以 `Decimal(repr(x))` 寫收據必為原值，收據不會差一分；七日加總也遠低於 SQLite 整數上限，讀取時以 Python 整數加總、不寫回，不會丟溢位例外；超出讀取白名單上限者由讀取層記 invalid。
- 極端值在邊界拒收：1e300 這類金額種子時就 ValidationRejected；原防回歸改以能存的最大金額驗「任務不卡在蒐證」。舊庫 REAL 金額存不下者升級時記缺值，DSP 照常啟動。
- 舊庫補不回調整前預算的加額，端點照回該筆、`budget_before=null`（讀取層收、領域判證據不足），不再 404；操作紀錄是只增不改稽核表，補值另存補值表、不 UPDATE。
- 歷史：摘要與列在同一讀取快照；未截斷（≤50 筆）維持 `{"history": […]}` 既有形狀與收據；截斷才帶 `summary` 與 `truncated=true`，收據另標 `history_truncated`。讀取層核對摘要自身大小關係及與回傳列的下界（暫停＋加額 ≤ 總數、列中加額／暫停不多於摘要、總數多於 50）。摘要的「最近 3 天／7 天」以 DSP 讀取時刻切，只作截斷時的保守旗標；正式規則仍以決策 now 判第 3 條。
- 逐日與 1d/7d 分屬不同 HTTP 讀取，恰跨 UTC 午夜時兩邊日期快照不同 → 逐日記 invalid、該步證據不足（保守、不錯提案）。接受此取捨：消除它需另打 DSP 重讀，違反 [S1113] 讀取上限；正式規則 C 步另由 [S1406] 比對讀取與決策 UTC 日期。
- 評估：歷史列與過去調整列改為同一時刻（NOW − days_ago），重產 72 筆；格、答案、收據雜湊、SYSTEM_PROMPT 與錄製重播不變。
- 本段對應測試見 [[Verification/Phase14增量2a離線驗證]]〈代碼審 r1 修正〉。

2a 代碼審 r2 修正（2026-09-26，協調者接受 r1 兩處偏離：跨午夜判證據不足、DSP 自留金額規則與 13 位）：
- 日桶與樣板每天上限改為整數分上限與計數上限的七分之一，保證 1d/7d、加額前後三天合計都在讀取白名單上限內（鏡頭B 1）。
- 截斷歷史：預算調整＋暫停必須等於總筆數；另以這一步讀取時刻核對摘要近期計數不少於回傳列中的近期加額，不合整份 invalid（外家 finder 1、鏡頭B 2）。
- 測試時鐘守衛：DSP 測試檔對未注入時鐘的儲存層給「固定 NOW 後 400 天」，真時鐘配固定日期當場翻紅（鏡頭A 1）。
- 舊庫第一筆補不回前值、之後只有減額的廣告會一直證據不足，接受並記在 [[Systems/Mock-DSP]]（鏡頭A 2）。

2a 代碼審 r3 修正（2026-09-26，末輪，只有 minor）：
- 截斷歷史近期核對：r2 說明把時鐘先後寫反，更正為「DSP 讀取時刻正常就晚於這一步 now，界線附近會誤判」；並留與證據新鮮度相同的 15 分鐘容忍，正常讀取延遲不再判 invalid（代價：界線 15 分鐘內的少報不擋）。補一支只有「最近 7 天」核對擋得下的測試。
- 每天上限對舊資料一致套用：遷移的相對日數列與前一版已存的日期鍵日桶、樣板，讀取推算時同一支處理（超限欄記缺值；五欄全缺即沒資料）。
- `DAILY_MAX_COUNT` 改用既有 `SQLITE_INTEGER_MAX`。

2a 租約讀取次數修正（2026-09-26）：AI 單查逐日只讀 1 次；同一步已有長窗 1d/7d 才在 dsp_client 共用讀取層做精確核對，規則 C 後續也應走此入口；全選項組合的實際讀數對齊 READS_PER_OPTION，MAX_COLLECT_READS=6 時預設逾時單步最壞 58 秒，小於 60 秒租約，維持 [S1113] 與收據、SYSTEM_PROMPT、錄製鍵不變。[test:test_every_query_combination_uses_its_declared_dsp_reads_within_the_lease]

增量 2b（2026-09-26，正式規則與分步蒐證）：正式路徑改判九條並分 A/B/C 三步讀四查詢；新程式在 `rule_round`（[[Systems/分析行程流程與檢查點]]）。要點與測試對應：
- 正式規則：`policy.explain/steps/decide` 改用 `rtb.domain.nine_rules`；順序是新鮮度 → 可信現況要有合法狀態 → 第 1/2 條（只用基本資料）→ 配速 → 其餘七條（要四查詢）。四查詢一律明傳：只有基本資料時明傳 `MISSING_FOUR_QUERIES`（配速偏低其餘情形 = 證據不足）；舊「曝光點擊正數就加」與判斷點建不起來時的舊判式整段撤掉。[S1401] [test:test_paused_and_anomalous_campaigns_finish_from_base_evidence]、[S1402] [test:test_missing_query_results_prevent_a_proposal]。
- 規則輪：A=現況+1h（2 讀）、B=歷史+過去調整（2 讀）、C=逐日+1d+7d+重讀現況與 1h（5 讀）；宣告讀數由查詢選項算、測試核對等於實際讀數。進度只由已提交的 `rule_steps`／`rule_events` 重算；最新一輪有非續步事件或不是本政策版本就作廢，新輪從 A；蒐證時先前步驟過期（當機續跑逾 15 分鐘）也開新輪；定案以決策 now 重驗三步證據年齡、A/C 比編號狀態版本與 1h 五項原始指標，變動連續重來兩次後再變動以證據不足結案；409 退回後因定案事件已寫、新輪從 A 全量重讀。[S1405] [test:test_rule_collection_steps_fit_the_lease]、[S1413] [test:test_rule_round_checkpoints_exclude_stale_evidence]、[test:test_changes_between_a_and_c_restart_twice_then_stop]、[S1424] [test:test_policy_switch_restarts_inflight_analysis]。
- 時間：逐日帶 C 的讀取時刻，跟決策 now 不同 UTC 日即證據不足（領域 `DailyTrend.read_at`、原因 `day_boundary`）；第 3 條另看過去調整的 `committed_at` 與截斷歷史的完整集合近期旗標，第 4 條以決策 now 判 D+3 是否完整（now 早於 D+4 日 00:00 UTC 就證據不足，DSP 回了數字也不採信）；缺提交時刻的過去調整列判證據不足。72 筆逐筆格與答案不變。[S1406] [test:test_decisions_recheck_freshness_and_utc_day_boundaries]、領域 [test:test_past_adjustment_commit_time_is_judged_on_the_decision_clock]、[test:test_truncated_history_recent_flag_counts_as_a_recent_change]、[test:test_daily_read_on_another_utc_day_is_insufficient]。
- 缺狀態：現況 200 回應只有狀態缺值／非法時讀取層不丟例外、不造 state 證據（廣告文字也不帶版本），分析以 `MISSING_STATE_OR_METRICS` 結案；C 才缺狀態也不沿用 A 的；5xx／連線照舊重試。[S1404] [test:test_missing_dsp_state_finishes_without_proposal]、[test:test_a_dsp_server_error_on_the_state_is_still_retried]、[test:test_missing_state_at_step_c_does_not_reuse_step_a]。
- 證據參照：規則輪提案列 C 的基本三筆加四種成功查詢收據（[S1107] 改寫）。（原寫「AI 直接提案仍只列基本三筆」已失效：代碼審 r1 起 AI 答 propose 也開規則輪、由規則輪建提案，見下方 r1 修正）[test:test_underpacing_campaign_reads_three_steps_and_proposes_with_query_receipts]、[test:test_a_model_chosen_proposal_equals_the_formula_proposal]。
- 政策版本：`POLICY_VERSION` 改 `nine-rules-v1`，`KNOWN_POLICY_VERSIONS` 保留 `demo-pacing-v1`；舊版提案由 [S504] 擋。[S1416] [test:test_old_policy_proposals_are_blocked_after_the_rule_change]。
- 租約與寬限：runner 對 A/B/C 各查 `5 + N ×（逾時 + 5）+ 5 < 60`，兩種模式都查（沒開 AI 的 5 秒逾時因 C=60 改拒絕）；啟動器規則模式寬限取 max(舊兩讀寬限, A/B/C 最壞)=50 秒，AI 模式再與 60 秒取大；三者共用 `stepbudget.RULE_STEP_READS`。[S1419] [test:test_stop_grace_covers_the_longest_rule_step]。
- Phase 10：`route` 的四查詢與決策時間改必填，`score`／模型候選／延遲量測明傳 `MISSING_FOUR_QUERIES` 與固定 `RULE_NOW`；報告每格標「舊資料不足以評估九條規則」。[S1417] [test:test_phase_ten_scenarios_explicitly_report_missing_queries]。Phase 13 報告的「程式規則」列改用九條加案例的四查詢（同源、36/36 只證接線；報告文字與三列分列是增量 4）。
- AI 退回（與增量 3 的分界）：原本 AI 退回走 `_rule`（舊「有投放就加」），九條需要四查詢，所以 2b 照計劃〈AI 退回與展示〉先做**退回那一半**：退回時基本三筆判得出的（暫停、異常、前置過濾那幾道）當場結案，其餘開一次新規則輪 A/B/C 全量重讀（退回紀錄結果代碼 `rule_round`），規則輪那幾步由流程層直接問規則輪、不經 AI；AI 用過之後的蒐證改讀規則輪（撤除 [S1138]「只讀基本兩樣」）；開 AI 時規則輪定案也照 --hold-submit 攔（[S1156] 的這個出口）。評估執行器遇到退回開規則輪，改由案例的四查詢跑同一支正式規則。**留給增量 3**(代碼審 r1 後否決與前置過濾已提前到 2b,見下方修正)：[S1421] 全部出口集中攔、展示頁與流程圖分開畫程式補查、F4/F6 展示斷言與 [S1409]、F7 負載驗收 [S1411]/[S1420]。
- 展示最小相容：觀察器把規則輪續步的中間列（續步那一列蒐集、B/C 的分析中、AI 退回開規則輪那一列的重新蒐集與規則輪 A 的分析中）併進既有節點，不另畫；F5 名稱核對改成「讀到的名稱都是原文」（A、C 各讀一次現況）。
- 既有測試期望改變（逐條理由見 [[Verification/Phase14增量2b驗證紀錄]]）：F4 接續任務改為第 3 條證據不足、無提案、收件口與 DSP 只有原先那些寫入；[S700] 改列九條與舊結果的差異；[S705] 豁免只剩考題結束；[S714] 改名改寫；S49、[S1106]、[S1115]、[S1138]、[S1156] 等的退回期望改成規則輪；runner/F5/F6/端到端夾具補種四查詢歷史、步數與端點清單照三步讀調整。
- 未解決（停在這裡）：政策版本升版後，入庫展示錄製（phase13-demo-20260925）裡模型說明（提案說明命令列）的提示含政策版本，重播 F1–F6 有 8 筆說明呼叫找不到錄製，`test_committed_demo_recordings_have_no_ai_fallback_in_f1_to_f6` 紅；代理不准錄，需協調者用真 claude 重錄展示批次（或裁定其他處置）。AI 調查的提示與錄製鍵不含政策版本，Phase 13 評估 72 筆重播不受影響。

增量 2b 代碼審 r1 修正(2026-09-26,八席:外家 finder 3、外家否決 1、資安 2、架構對齊 1、鏡頭 1–4 共 9 條 minor;spec-conformance clean;協調者定修法):
- 外家 finder-1(blocker):長窗任一窗負數、點擊多於曝光、轉換多於點擊,或 1 天窗任一欄大於 7 天窗,九條一律證據不足;並把四查詢所有「不合格」檢查(長窗、晚於決策 now 的歷史／過去調整時刻、負數過去調整、不合格逐日列)移到第 3 條之前,任何一條本會命中都擋在前面。讀取層照 2a 裁定保留「負數不參與跨窗比較、收據寫 na」(給 AI 看),決策以領域為準,兩邊語意一致(都不採用那個值)。[test:test_negative_or_inconsistent_longer_windows_are_insufficient]、[test:test_every_invalid_query_result_blocks_a_proposal_whatever_rule_would_hit]。評估測試原有一組「1 天 2、7 天 0」自相矛盾的長窗改成合理數字,另斷言矛盾那組是證據不足。
- 外家否決-1(blocker)+資安-1:把原屬增量 3 的兩件提前做。AI 前置過濾納入第 1/2 條(暫停、異常、判斷點輸入建不成)直接由規則結案、不呼叫模型;AI 答 `propose` 不再直接建提案,記下 AI 原始結論後開一次新規則輪 A/B/C,九條也判值得加才由規則輪建提案,否則否決(定案事件細因帶 `ai_propose_vetoed:`,[S1407] 否決那半)。所以不會再有未經九條卻帶 `nine-rules-v1` 的提案。評估的原始錄製重播(`Judge(raw_replay=True)`)沿用 Phase 13 舊前置過濾、`CaseRun.final` 記模型自己的答案,Phase 13 報告數字不變;「AI+規則否決」列仍是增量 4。[test:test_paused_and_anomalous_campaigns_never_reach_the_model]、[test:test_an_ai_propose_opens_a_rule_round_instead_of_a_proposal]、[test:test_an_ai_propose_is_vetoed_when_the_rules_disagree]、[S1107] [test:test_a_model_chosen_proposal_equals_the_formula_proposal](改成端到端:規則輪建提案、模型只呼叫一次)。
- 外家 finder-2:C 步改成先讀逐日與長窗、最後才重讀現況與 1 小時(仍 5 讀,租約算式不變),查詢期間另一方改預算會被 A/C 比對抓到而重來。[test:test_step_c_rereads_the_state_after_its_queries]。
- 外家 finder-3:連續變動重來次數只數目前政策版本的輪,升版後歸零([S1424])。[test:test_change_restarts_do_not_carry_over_a_policy_switch]。
- 架構對齊-1:`rule_round.progress`、`collect_plan` 改純函式,吃呼叫端先讀出的記錄(同 `investigation.progress` 慣例);`decide` 只讀一次規則輪記錄。[test:test_progress_is_recomputed_from_plain_records]。
- 鏡頭3-1:AI 退回測試的預期值寫死(不拿正式程式的 `needs_queries` 當預言機),並驗證把判準改壞時 [S1105][S1131] 兩支 20 筆翻紅。
- 鏡頭3-2:F6「決策變舊」的接續任務走完規則輪、提案、執行、完成,補回接續任務正路。[test:test_f6_a_stale_decision_follow_up_is_replanned_and_executed]。
- 鏡頭2:規則步驟表毀損時,開不開 AI、分析或蒐證那一步都轉 FAILED(原本 AI 模式卡在分析中、蒐證無限重試)。[test:test_a_corrupted_rule_step_row_fails_the_task_instead_of_looping]、[test:test_a_corrupted_rule_step_row_fails_the_collect_step]。調查原始資料表的「沒有決策路徑讀」說法改成「只有規則輪定案讀」,守衛測試改成白名單掃描整個分析端目錄(只准 `rule_round`)。「一步最多兩次讀」四處說法補上規則輪 C 最多 5 讀。
- 鏡頭4:正式環境抽樣報告同樣標「舊資料不足以評估九條規則」;延遲量測寫明是缺四查詢的九條短路徑。
- 資安-2:DSP 操作歷史回應頂層帶廣告編號,讀取層核對是這件工作的廣告,核過拿掉,存下的原始回應與收據維持既有形狀。[test:test_the_change_history_must_belong_to_the_task_campaign]。
- 鏡頭1/2 檔頭:`rule_round`、`ai_judge` 檔頭改成實際接線。
- 與增量 3 的新分界:否決與前置過濾已在 2b;留給增量 3 的是 [S1421] 全部出口集中攔(純規則路徑的 hold-submit)、展示頁與流程圖分開畫「程式複查／補查」、F4/F6 展示標註與 [S1409]、F7 負載 [S1411]/[S1420]。

增量 2b 代碼審 r2 修正(2026-09-26,五席;資安 clean;協調者定修法):
- 架構對齊-1:`dsp_client.check_history` 改成跟 `check_daily`/`check_adjustments` 同款——廣告編號必填、頂層形狀連編號一起驗、核過的編號留在回傳值(存下的原始回應多一個編號欄;收據只讀列與摘要,字串與錄製鍵不變)。[test:test_the_change_history_must_belong_to_the_task_campaign]。
- 鏡頭1-1/鏡頭2-2:`ai_propose_vetoed:` 只標在 AI 答 propose 之後的**第一次**規則輪定案(之後還沒有任何 `decided` 事件);規則已同意、送件後收件口 409 退回的新輪判不提案,照一般規則細因記,不算否決。觀察器「接在 AI 那一步之後、省略新鮮度與配速」也只套第一輪。[test:test_only_the_first_decision_after_an_ai_propose_is_a_veto]。
- 鏡頭2-1:規則事件表讀不回來也包成 `CorruptedHistoryRow`,蒐證那一步轉 FAILED。[test:test_a_corrupted_rule_event_row_fails_the_collect_step]。
- 外家 finder-1:逐日讀取跨 UTC 日時,就算另有壞列,細因也記 `day_boundary`(跨日比壞列根本,整批日桶不用)。[test:test_a_cross_day_daily_read_reports_the_day_boundary_even_with_a_bad_row]。
- 鏡頭1-2:補上 [S1407] 綁定的 [test:test_ai_proposals_and_fallbacks_use_a_fresh_rule_round](propose 與退回各跑一次:只開一輪 A/B/C、DECIDE 一次、模型只呼叫一次、沒有第二列退回)。這支在 r1 的程式上就綠(否決那半在 r1 已做),是補綁定,不是先紅後綠。
- 說明一致:計劃 2b 證據參照一條、〈分析行程流程與檢查點〉2b 段、`ai_judge._fallback`、`policy.build_proposal` 的舊說法(AI 直接提案、否決屬增量 3)都標成已失效或改寫;驗證紀錄的 revalidate_when 改成增量 3 剩下的範圍。
- 錄製模式不退回真帳本(協調者新增,原屬增量 3):模型閘道在錄製模式沒拿到帳本就 GateRefused;命令列入口(評估重播、Phase 10 評估、原因假說、說明、分析端驅動)錄製模式沒帶帳本時改用這次執行專屬的暫存帳本(r3 收成 `mc.recorded_ledger`,路徑一律印在標準錯誤);展示驅動把情境自己的帳本給原因假說與說明(r3 更正:沒開 AI 的情境不跑這兩支入口)。[test:test_recorded_entries_never_touch_the_account_ledger]、[test:test_recorded_hypotheses_without_a_ledger_use_a_scratch_ledger]、[test:test_model_entries_only_run_with_ai_and_never_without_a_ledger]、[test:test_the_gate_decides_mode_ledger_and_recordings_once]。事故見 [[Issues/錄製模式的原因假說寫進真帳本]](已結)。

增量 2b 代碼審 r3 修正(2026-09-26,末輪四席;資安 clean):
- 架構對齊-1:「錄製模式沒帶帳本就用暫存帳本並通知」收成模型用戶端一支 `recorded_ledger(mode, given, notify)`;說明、分析端驅動、評估重播經模型閘道的 `notify` 參數呼叫它,Phase 10 評估與原因假說直接呼叫它;五個入口都印路徑到標準錯誤(record 原本漏印)。測試改成每個入口各自的輸出緩衝區。
- 鏡頭A-3/外家 finder-1:判出模式之後才決定;即時模式不建暫存目錄、不印那句;暫存目錄登記 atexit、行程結束清掉。[test:test_the_shared_resolver_decides_after_the_mode]、[test:test_the_gate_uses_the_shared_resolver_only_in_recorded_mode]。
- 鏡頭A-2:[test:test_narrate_and_the_runner_book_recorded_replays_into_a_scratch_ledger] 驗說明與分析端驅動實際用的帳本不在帳號家目錄;把閘道改回「沒帶帳本用真帳本」時它與 gate 那支翻紅(變異驗證)。
- 鏡頭A-1/A-4:觀察器與否決標記改用同一支 `investigation.rule_round_opened_by_ai`,補 [test:test_only_the_first_rule_round_after_the_ai_skips_the_early_checks]。
- 外家 finder-2:Issue 摘要改成已修,舊段標成當時紀錄。
- 外家 finder-3:沒開 AI 的情境根本不跑模型入口,`_entry_args` 撤回 r2 加的那條走不到的分支;改測實際入口:[test:test_model_entries_only_run_with_ai_and_never_without_a_ledger](沒開 AI 不叫任何入口),開 AI 的真入口由既有 test_recorded_demos_book_into_a_temporary_ledger 驗。

### 實作解讀(2026-09-26~27,增量 3)

照〈拆增量〉3 逐項清單做完程式、測試與錄製驗收工具;**展示批次待協調者錄製**,錄完才全套綠(等錄製的紅只有一支,見 [[Verification/Phase14增量3驗證紀錄]])。逐項:

- 刪(runner/流程層):`runner.py` 只剩規則參數,`--ai-judge`、`--hold-submit`、模型參數、`_unsafe_ai`/`_unsafe_hold`、`_Ai`、`_judge_for`、`_open_ai_gate`、`MODEL_LINE`/模式行、`GateOpener` 全撤,每一步固定 `rule_source` + `rule_round.decide`。`flow.advance` 撤 `ai_decide`/`clock`、`AiDecide`、`_from_ai`、續租與記次回呼;`AiContext`(拿掉續租回呼)、`AiOutcome`、`QueryMore` 搬到 `investigation`,`NoAction`/`ProposalDecision`/`RuleContinue` 規則輪也用、留在流程層。`rule_round_opened_by_ai`、`opens_rule_round`、`held_rule`、`AI_PROPOSE_VETOED` 撤除;`rule_round` 本身沒有 AI 開輪判斷,只改說明。[S1429] [test:test_only_phase13_eval_uses_ai_judge_and_runner_stays_rule_only]。
- 刪(考題):`NoActionReason.EXAM_HOLD`、`a_exam_hold`、`driver._exam`/`EXAM_*`、`Outcome.EXAM_HOLD`、F5 雙胞胎全撤;舊資料庫的 `exam_hold` 字串唯讀留著。
- 改(展示開關):`World.ai`/`AiSetup` 只剩說明與假說入口的設定;分析端參數永遠不帶模型參數,啟動器永遠不給分析端模型變數;模型模式由說明/假說入口的輸出回報(`driver.entry_mode`)。`ai_tasks`、`allow()`、`NEVER_LIVE`/`F7_RECORDED_ONLY`、launcher 的 `AI_JUDGE_FLAG` 與 AI 寬限、stepbudget 的 AI 步三支函式撤除;`server --live` 只拒不認得的情境。
- 改(展示證據與圖):`observe`、`basis`、`present`、`page`、`flow_svg` 撤 AI 調查格、AI 判斷事件、退回/否決/考題映射與 `after_fallback`;`demo/flow` 撤 `a_ai`(含 `a_pacing→a_ai` 等六條邊)、`a_ai_query` 回頭、`a_exam_hold`、退回原因 12 筆映射(對應清單回到十八個);`flow.AI_NODES` 只留模型候選與說明。展示狀態的 `decided_by`/`exam`/`answered_rounds` 撤除,舊鍵唯讀略過。F4/F6 接續任務照第 3 條「剛被調過預算,先不動」,驅動核三處寫入都 0。[S1409] [test:test_f4_and_f6_follow_ups_show_rule_insufficiency_without_writes]。
- 改(F5/F7):F5 留受攻擊的 c1 與旁邊不調整的 c2(清單的「只保留受攻擊廣告 c1」解讀為撤掉雙胞胎 c3,c2 是 Phase 12 起就有的對照,不屬 AI 考題)。[S1427] 在分析端端到端量:規則細因、9 次讀取、提案金額與同數字正常名稱相同,說明的程式數字段逐字相同。F7 用正式規模與原 300 秒實測:2026-09-27 本機 16.1 秒走完,DSP 讀取 2700 次(每件 9 讀、沒有重來)、規則步 900 步,不需要時限變更。[S1411] [test:test_f7_finishes_with_rule_reads_under_the_actual_allowance]。
- 留評估:`ai_judge.Judge` 只剩評估實際走到的一段(Phase 13 舊前置過濾、輪數上限、提示、解析、退回),`raw_replay`/`preflight_ok`/`stop_requested`/`hold` 撤除;評估執行器直接呼叫,`RuleContinue` 當場回傳。錄製重播與即時加錄製由 [S1429] 那支用假後端各跑一次。
- 改(錄製與批次驗收):新入庫目錄 `recordings/model/phase14-demo/`、批號 `phase14-demo-YYYYMMDD`;有提案的情境要有說明呼叫、不提案的可無帳本、批次與帳不准有分析端調查(代碼審 r1 起「有提案」改從情境分析端資料庫數真正送出的提案,見下方 r1 修正);撤 `ai_fallback_problems` 與覆蓋舊批指示。舊 `phase13-demo` 六份以逐檔雜湊釘住([test:test_the_phase13_demo_batch_stays_as_read_only_history])。[S1428] [test:test_proposed_demo_tasks_require_only_narrative_recordings]。本批無 HYPOTHESIS:F1–F6 用假錄製實跑,六個情境的假說命令列都回「沒有告警」(F6 的執行端連線故障沒達服務水準告警門檻),照清單明記,等專用告警情境補錄後才聲稱假說已驗。
- 宣稱清單:五份重算雜湊;`prompt-injection` 的 policy 改成「要不要加預算只由九條規則看數字決定…名稱只在說明的資料區逸出引述」,證據把評估 Judge 那支換成 [S1427] 的端到端,scope 拿掉 `ai_judge.py`、補 `narrate.py`,harness 拿掉只為 AI 路徑而在的四支。
- 清單與程式對不上的地方(初版回報、代碼審 r1 後裁定):① `instrumented.investigation_source` 沒有正式入口;② 任務模組的 `renew_lease`(初版寫「只剩測試用」不準:它的測試隨 AI 那一步一起刪了,當時已沒有任何呼叫者,spec-conformance 指出)、`record_model_call`、`commit_step(investigation=…)` 與啟動器 `Process.next_line` 只剩測試用 → 協調者裁定照「沒有入口就刪」全刪;③ 說明的數字核對只比數值,「加 500%」擋不下 → r1 改成百分比對百分比(當時只認 % 與 %),代碼審 r2 三席指出換寫法可繞過,r2 起百分比的寫法先 NFKC 正規化再認:%(含全形與小型百分號)、阿拉伯百分號、percent/per cent/pct、(個)百分點、百分之N(視同 N%);千分號、萬分號整句拿掉。沒列到的寫法仍只比數值,不含數字的句子不做語意過濾。
- 代碼審 r1 修正(2026-09-27,協調者裁定:F5 留 c2 接受、沒有入口就刪、本批無假說接受):
  - F3 只付一次費:賽跑改用規則輪證據來源,贏家讀的就是規則輪 A、分析端認得不重讀;驅動核一輪規則輪、無重來、平台 9 讀,撤掉 F3 容許的重讀節點。[test:test_f3_races_only_the_evidence_step_and_the_driver_never_loads_the_model]
  - 錄製驗收的「有提案」改從情境分析端資料庫數真正送出的提案;F4/F6 追蹤那件沒提案時,驅動改說明情境最後一份送出的提案(t1),[S1428] 定義同步統一。已入庫的 phase14-demo-20260927 批次不需補錄(t1 的說明提示與 F1 逐字相同,重播命中)。
  - 舊版 AI 決策記錄不再顯示成「程式規則(九條)」:展示狀態留 `decided_by`(新記錄一律 `nine_rules`),舊值 `ai`/`ai_fallback`/`rule` 照實標成已撤除的舊流程或早於九條。[test:test_old_records_keep_who_decided]
  - F7 等待改用 300 秒(`F7_TIME_LIMIT_SECONDS`,跟情境時限同一個常數)。[test:test_f7_waits_for_every_task_as_long_as_its_time_limit]
  - 說明核對百分比對百分比:「加 500%」整句拿掉、「加 10%」留著(代碼審 r2 補齊寫法,見下一條)。[test:test_a_percentage_must_trace_back_to_a_percentage] [test:test_f5_adversarial_name_preserves_rule_and_traceable_narrative]
  - 說明命令列沒跑完時,展示頁說「說明命令列沒有跑完」而不是「沒有呼叫 AI」。[test:test_a_failed_narration_is_not_reported_as_no_ai_call]
  - 刪無入口程式:`instrumented.investigation_source`、`TaskStore.renew_lease`/`record_model_call`/`investigation_call_count`/`investigation_reason_code`、`commit_step(investigation=…)`、啟動器 `Process.next_line`;任務模組四處過時說明改寫。
  - 匯入邊界:`investigation` 從 `flow` 單向拿 `Decision`/`RuleContinue`,寫進模組說明並加測試。[test:test_the_investigation_vocabulary_depends_on_flow_one_way_only]
  - 其他:分析端 ruff 禁匯入 `ai_judge`、各 ruff.toml 訊息改寫、舊 test_runner 准用清單撤 ai_judge/modelgate、`recorded_ledger` 說明改四項、`recordings._modes` 改 walrus、宣稱 prompt-injection 明寫「只過濾數字,不含數字的句子靠標示與人工確認」。
- 代碼審 r2 修正(2026-09-27):
  - 百分比核對補齊寫法(鏡頭A、外家finder、資安三席一致):r2 起百分比的寫法先 NFKC 正規化再認:%(含全形與小型百分號)、阿拉伯百分號、percent/per cent/pct、(個)百分點、百分之N(視同 N%);千分號、萬分號整句拿掉。沒列到的寫法仍只比數值,不含數字的句子不做語意過濾。擋得住的範圍就是這張列舉,宣稱清單與 [[Systems/模型用戶端]] 同樣照實寫。[test:test_every_way_of_writing_a_percentage_must_trace_back_to_a_percentage] [test:test_a_traceable_percentage_survives_any_spelling]
  - `instrumented.dsp_evidence_source` 與 `InstrumentedEvidenceSource`(後者協調者追加裁定)沒有正式呼叫端,照「沒有入口就刪」刪除;Phase 2 [S50] 的三支證據來源測試保留原名、改走 `rule_source` 的 A 步(成功各端點記一筆、連不上記失敗且例外照丟、拿掉紀錄表時證據照讀),逐端點記錄的兩支測試改走 `rule_source` 的 A 步,升版測試的「舊版兩讀」直接用 `dsp_client.make_client`。
  - [[Systems/展示頁面]] 摘要「這次誰決定固定寫九條」改成照 `decided_by` 顯示。
- 代碼審 r3 末輪修正(2026-09-27,無 blocking,3 條 minor 折入不開新輪):
  - 說明核對所有比對之前先拿掉看不見與空白樣的字元(Unicode 類別 Mn、Me、Cf,韓文填充字、點字空白),數字與百分號之間插這類字元不再能繞過;只改核對一端、不改頁面逸出,理由見 [[Systems/模型用戶端]]。
  - 百分比寫法接受連字號與複數(500-percent、500 per-cent、500pcts)。以上兩條的測試先在 r2 程式上翻紅 14 支再修。[test:test_every_way_of_writing_a_percentage_must_trace_back_to_a_percentage]
  - `test_instrumented.py` 起模擬 DSP 改用同目錄慣用的 pytest fixture。
- 翻案回指:Phase 13 計劃 26 條原綁測試已刪的條款改綁新測試或 `[manual:]`(spec-trace 0 懸空);Phase 12 [S1028] 的分析端模式行測試改綁入口回報模式那一支。本計劃 [S1410] [S1418] 仍懸空,屬增量 4(報告)。(2026-09-27 增量 4 已綁,見下一節)

### 實作解讀(2026-09-27,增量 4)

重跑兩份評估報告,只重播入庫錄製與本機規則、不呼叫模型,每次都帶暫存 `--ledger`。驗證見 [[Verification/Phase14增量4驗證紀錄]]。

- 報告程式([[Systems/評估與Jev決策點]]):評估執行器每筆分開存「AI 原始」(模型自己下的結論)與案例九條結果,撤掉舊的混合「最後答案」——舊報告的「模型」列其實含退回後由規則答的筆數,品質被高估(舊 0.4722,AI 原始只有 12/36=0.3333)。退回、選項外、呼叫失敗、輪數用完一律記「無有效答案」另列,不算誤提案也不拿規則答案頂替。「AI+規則否決」只在報告層由兩者派生,原因取九條細因。採用理由加一條 AI 原始品質;規則同源全對不改判。延遲分三種單位與範圍:模型呼叫毫秒(錄製觀測)、九條本機計算微秒(這次行程內量)、整段正式蒐證不在評估裡量(沒量,指向 F7 實測)。[S1410] [test:test_reports_keep_raw_ai_errors_separate_from_rule_vetoes]、[S1418] [test:test_shared_rule_accuracy_never_changes_the_ai_adoption_decision],兩支都先在舊程式上翻紅(`CaseRun` 沒有 `ai_raw`;採用理由沒有 AI 原始品質)再轉綠。
- Phase 13 重播數字(批次 phase13-eval-20260925,名稱正常 36 筆):AI 原始有效答案 23 筆、答對 12 筆,應不提案的有效答案 19 筆裡誤提案 11 筆(異常 1/1、近期調整 3/3、加額沒效 3/3、轉換率掉 3/3、沒價值 1/2),無有效答案 13 筆(全是選項外;較長窗格 4 筆全部);派生否決列誤提案 0/19;程式規則 36/36、誤提案 0/28(兩者同源構造)。雙胞胎切片 AI 原始 13 組不同(舊報告 5 組,是把退回規則的答案當成模型答案才少算)。模型呼叫延遲中位 4236、p95 7244 毫秒(門檻各 3000);九條本機計算中位約 87 微秒。結論照舊不採用。
- Phase 10 報告:照 [S1417] 已接的程式重產,五格每格標「舊資料不足以評估九條規則」,標準答案不變;暫停與異常 60/60,沒投放格 0/60 類別正確(全判證據不足)、有價值格召回 0/60,五格誤提案皆 0。LLM 列照實寫「沒量(沒有批次紀錄)」(Phase 11B 從未入庫批次紀錄;舊版報告產生時還沒有模型段)。
- 與計劃的差異:原「比較表(現行規則 對 模型)」併進三列分列表,不另列;`CaseRun.final` 刪除(會把退回混進 AI 原始,留著就是暗門)。
- 代碼審 r1 修正(2026-09-27,三席 9 條,協調者裁定全修,逐條見 [[Systems/評估與Jev決策點]]〈代碼審 r1 修正〉):缺錄製另列並讓報告標不可採信;派生細因走 `rule_decision`;數字格共用 `adoption.format_value`、單位只寫在欄名;Phase 10 沒呼叫模型時寫沒有跑、比較表規則列帶舊資料不足;入庫報告與重播逐字比對(排除九條延遲那一行)並補派生列殺傷力;揭露無有效答案的本意(本意誤提案 2、本意答對 4、本意選查詢 7;照本意算誤提案 13/25,非正式口徑);雙胞胎拆成兩邊都答而不同 3 組、一邊格式失敗 10 組;說明評估集雜湊跟錄製時不同的原因;無有效答案原因改扁平代碼。
- 代碼審 r2 修正(2026-09-27,兩席 6 條 minor,全修,逐條見 [[Systems/評估與Jev決策點]]〈代碼審 r2 修正〉):雙胞胎任一側缺錄製另列並讓報告標不可採信;評估集雜湊說明改實際比對登記的錄製時雜湊;Phase 10 表下註更正(暫停、異常品質 1 有效);模型延遲整份只用毫秒;補照本意算分母、本意取最後一輪、缺錄製涵蓋所有沒送出類別的反例;缺錄製欄位照單複數慣例命名。對外數字不變。

1. 領域層建自己的四查詢資料型別、九條判斷、格、封閉 `StrEnum` 診斷原因與共用切點；評估 `answer()` 改呼叫它。固定 Phase 13 72 筆（含名稱雙胞胎）在改前改後的格與標準答案一致，新增列內缺值、單日 `no_data=true` 與資料齊但分母零的對照案例；此增量只搬單一來源，不改正式行為。新領域檔同步開 [[Systems/正式九條判斷領域規則]]。
2. 正式規則與分步蒐證：先讓近期調整、缺值、查詢沒有結果、跨窗矛盾、paused、異常、政策版本等測試翻紅，再接 `policy`、`flow`、`runner`、`instrumented`、`TaskStore`、DSP 唯一調整紀錄、精確分儲存／回應、有日期逐日端點的跨日物化、基本缺狀態診斷及證據參照。明訂輪次由已提交列重算、歷史七日上限與重進入，修改租約守衛、啟動器停止寬限、原始資料讀取合約與診斷原因；假的 DSP 故障驗證不提案或重試。同步接好 `scoring.py → route` 的 Phase 10 缺四查詢轉接，避免改簽章後既有評估報告斷線。這個增量行為改變最大，單獨驗收。
3. **AI 決策退出正式／展示分析（2026-09-26 代使用者裁定）**：理由是九條已能精確決定加額，AI 決策只增加錯誤與展示耦合；AI 說明／告警假說仍有獨立用途。逐項去留如下；「留評估」均指 `rtb.eval.investigation_eval` 的錄製重播與協調者授權的即時加錄製，不是留在正式提案路徑。
   - **刪（runner／流程層）**：`runner.py` 的 `--ai-judge`、`--hold-submit` 及「只在 --ai-judge 時可用」防呆、`_unsafe_ai`／`_unsafe_hold`、`_Ai`、`_judge_for`、`_open_ai_gate`、`MODEL_LINE`／登入預檢模式行、`GateOpener`、`ai_judge`／`modelgate` 匯入與模型參數；`_advance_one` 固定用 `instrumented.rule_source`、`rule_round.decide`。`analyzer/flow.py` 的 `advance(ai_decide=...)` 參數、`AiDecide` 轉手與 `_from_ai` 等 AI 分支、續租／記次 AI 回呼一併刪；目前由評估與 Judge 匯入的 `AiContext`、`NoAction`、`ProposalDecision`、`QueryMore`、`RuleContinue` 則移到調查語意模組並改匯入，不可連型別一起刪。`investigation.rule_round_opened_by_ai`、`rule_round` 的 AI 開輪判斷、`ai_judge.held_rule`／`AI_PROPOSE_VETOED` 與 `ai_propose_vetoed:` 細因刪除，Judge 只留評估直接呼叫到的提示、收據、解析與開閘道。保留規則步驟租約守衛、停止旗標與九條 A/B/C。理由：評估執行器不呼叫 flow 或規則輪，沒有入口就刪。
   - **刪（考題）**：`--hold-submit` 的解析與 `ai_judge.held_rule` 包裝、`NoActionReason.EXAM_HOLD`、`a_exam_hold`、`driver._exam`／`EXAM_PASSED`／`EXAM_FAILED`／`EXAM_UNCOMPARABLE`、`Outcome.EXAM_HOLD`、F5 雙胞胎種子／任務／只判不送清單及其測試。理由：它們只考 AI 會否被名稱帶偏；不改成純規則的 hold 功能。[S1421] 撤除。`investigation` 其他選項與收據型別留評估，歷史資料列唯讀保留。
   - **改（展示開關）**：`driver.World.ai`／`AiSetup` 拆成「分析端 AI 判斷」（刪）與「說明／假說模型入口的錄製或即時設定」（留）；`Driver.ai_setup`、`_entry_args`、`run_model_entries`、`_narrate`、`_hypothesize` 由後者控制，提案照常跑說明、告警照常跑假說。分析端組參數永不加 `--ai-judge`、`--hold-submit`、模型花費帳與模式行等待；模型模式／原因改由說明或假說入口自己的結果回報，不從 runner 的 `MODEL_LINE` 推測。即時清單只控制這兩個入口的環境變數與新錄製目錄；F7 無提案時不跑說明，若有提案則仍受普通錄製／即時政策。`driver.py` 的 `ai_tasks`、`allow()` AI 輪數放寬、`NEVER_LIVE`／`F7_RECORDED_ONLY` 拿掉，`server.py` 同步移除 `NEVER_LIVE` 匯入與即時清單的 F7 拒收，仍拒未知情境。`launcher/__init__.py` 刪 `AI_JUDGE_FLAG`、分析端模型環境分支與停止寬限的 AI 步取大分支；`stepbudget.py` 刪 AI 步最壞耗時／停止寬限常數與函式，保留規則步共用常數。純規則等待先以原 300 秒實測，再決定是否另提時限變更。
   - **改（展示證據與圖）**：`observe.py`、`basis.py`、`page.py` 移除 AI 調查格、AI 選項／退回／否決、`EXAM_HOLD` 的映射及 `after_fallback()` 展示分支，保留規則 A/B/C 收據、九條細因、說明與假說。`demo/flow.py` 的 `NODES`／`EDGES` 移除 `a_ai` 及其出入邊（含 `a_pacing→a_ai`），保留 `a_narrate`；`a_ai_query` 實在 `BACK_TRANSITIONS`，連同該回頭列移除。`flow.AI_NODES` 是節點歸屬，只移除 `a_ai`、保留 `a_candidate` 與 `a_narrate` 的 AI 歸屬；退回原因到 `a_ai→a_rule` 的 12 筆映射及出口檢查表 `a_ai` 列同步刪除。`driver.AI_NODES` 是路徑容忍清單，與 `with_ai_nodes` 一起撤掉；`page.py` 沒有 `AI_NODES`，只清其 AI 顯示與路徑判斷。`flow_svg.py` 刪 AI 決策與考題標籤但留 `ai_hypothesis`；SVG 即使畫未走分支也不得出現「AI 選下一步」。F4／F6 接續任務只標規則第 3 條，三處寫入皆 0。
   - **改（F5／F7）**：F5 只保留受攻擊廣告 `c1`，名稱作不可信資料逸出顯示；九條輸入型別不含名稱，以翻紅測試核規則格／細因、讀取次數及提案金額與同數字正常名稱一致，平台只允許受攻擊廣告一筆寫入。`narrate` 是剩下唯一處理對抗名稱的模型入口；沿用 `modelgate.traceable_sentences`：說明中每個數字都須對到提案／收據，不能把「加 500%／洩漏金鑰」當成動作或未核對的主張，名稱只作逸出引述。F7 保留 300 件與 F1 同數字的種子以核對規則結論，改掉「共用 AI 錄製鍵」docstring 與 `test_f7_shares_one_recording_key_with_f1`；撤除雙胞胎錄製鍵斷言。保留 [S1411] 與 `test_f7_finishes_with_rule_reads_under_the_actual_allowance`，併入 [S1420] 的純規則時限內容、撤除 [S1420] 定義與原測試名；量單分析行程逐件耗時、2700 次預期 DSP 讀取、SQLite 等待、分步提交與總耗時，300 秒超時即停 F7 展示入口，量測後再提時限變更，不預先放寬。
   - **留評估（AI 決策模組）**：`ai_judge.py` 只保留 `investigation_eval.run_case` 實際用到的 `Judge`、調查提示／收據、解析與評估開閘道；`rule_verdict(case)` 直接產生案例九條結果。評估目前從 `flow` 匯入 `AiContext`、`NoAction`、`ProposalDecision`、`QueryMore`、`RuleContinue`，Judge 也使用這些結果型別；刪 flow 的 AI 分支時將它們移至調查語意模組，讓評估與 Judge 繼續共用，不因此保留 `flow.advance(ai_decide=...)`。2b 的 AI 提案規則否決、AI 退回開規則輪及細因均不留。`Judge(raw_replay=True)` 的舊錄製重播與協調者授權即時錄製均須可達；報告的 AI+規則否決只是報告層派生比較，見〈評估與報告〉。`modelgate` 仍供 `narrate` 與評估開閘道，告警假說沿既有直接模型用戶端入口運作。
   - **改（匯入邊界與測試）**：`tests/test_spawn_boundary.py` 的 `GATE_USERS`、`CALL_MODEL_USERS`、`SENDING_ENTRIES` 都撤掉 `rtb.analyzer.runner`，保留 `rtb.analyzer.ai_judge`、`rtb.eval.investigation_eval` 與說明／假說的既有准用；`CALLER_USERS["INVESTIGATION"]` 仍僅准 `ai_judge`，`NARRATIVE`／`HYPOTHESIS` 保留。新增靜態與執行邊界測試：runner 及展示匯入閉包不含 `ai_judge`，`flow.advance` 不再接受 `ai_decide`，只有 `rtb.eval` 會使用 `Judge`，其即時錄製與重播均可達。`tests/analyzer/test_investigation_flow.py`、`test_investigation_review.py`、`test_investigation_review_r2.py`、`test_investigation_e2e.py` 的流程 AI 分支測試刪除或搬到評估語意；`test_ai_judge.py` 刪 `held_rule`／`ai_propose_vetoed:` 測試，保留 Judge 解析與評估呼叫測試；`test_observe.py` 刪 `rule_round_opened_by_ai`／`a_ai_query` 斷言。`tests/demo/` 的 AI 決策、退回、雙胞胎、考題與模式行斷言撤除或改綁規則結果；`tests/demo/fake_recordings.py` 只造說明／必要的假說，`tests/demo/test_ai_launcher.py` 驗入口環境而非分析端環境。
   - **改（錄製與批次驗收）**：`recordings/model/phase13-demo/` 的舊六份與 `phase13-demo-20260925` 留作唯讀歷史證據、不覆寫、不刪，也不再由展示重播讀取；新展示入庫目錄為 `recordings/model/` 下的 `phase14-demo/`，新批號格式固定 `phase14-demo-YYYYMMDD`，只含 `NARRATIVE` 與有告警才有的 `HYPOTHESIS`，不含 `INVESTIGATION`。`demo/recordings.py` 的 `BATCH_PATTERN`／`BATCH_SHAPE` 改驗新格式，`driver.DEMO_RECORDINGS` 與錄批工具的重播、入庫指示改指新目錄；舊批歷史完整性由 `tests/model/test_recording_integrity.py` 專測舊目錄六份、舊批號與檔案雜湊（若現有測試未覆蓋，先補這支再切換預設目錄）。再改 `replay_problems`／`check_demo_batch`／`record_demo_batch`：每個有提案的情境都要有對應說明錄製，F4／F6 接續任務不提案可沒有帳本，不能因零模型呼叫判失敗；撤除 `ai_fallback_problems` 空轉守衛及「錄完覆蓋舊批」指示。現有 F1–F6 不一定觸發告警假說，本批以 F6 執行端連線故障達告警門檻時驗一筆 `HYPOTHESIS`；若實測未達，明記本批無假說及觸發條件，在專用告警情境補錄後才聲稱假說已驗。`tests/demo/test_record_demo_batch.py`、`test_ai_demo.py`、`test_model_entry_contracts.py`、`tests/model/test_recording_integrity.py` 的新展示硬編批號與目錄同步改；錄批工具、假錄製測試先綠，再由協調者依授權作即時錄製；本輪不呼叫模型。
   - **推送順序**：增量 3 程式 → 錄製驗收工具 → 協調者錄製 → 全套綠 → 對 2b＋增量 3 合併差異跑代碼審 → 推送。2b 在 `32447ea` 的代碼審留痕不涵蓋增量 3；一同推送時回退亦按合併版本處理，不把「關 AI 入口」當回退動作。

   Phase 13 逐條翻案索引（2026-09-26 代使用者裁定；下列 42 條在舊條款旁逐條回指；舊 `[test:]` 為歷史綁定，落地時按右欄刪除、拆分或改綁。判準：`rg -n -- '--ai-judge|a_ai_query|a_ai' src tests` 與 Phase 13 條款／測試名對照；理由是正式／展示不再由 AI 決定加額，評估只走 Judge 與案例九條）：

   | Phase 13 條款 | 去留與原測試處置 |
   |---|---|
   | `[S1100]` | 改寫匯入白名單；runner 移出、`ai_judge` 只留評估；邊界測試改綁。 |
   | `[S1102]` | 刪 runner 帶 `--ai-judge` 錄製帳分支；說明／假說帳測試保留。 |
   | `[S1104]` | runner AI 前置過濾撤除；正式早停改綁 [S1401]，舊 AI 原始答案改綁 [S1410]。 |
   | `[S1105]` | Judge 格式／選項解析留評估；runner 退回測試刪除，評估測試改綁。 |
   | `[S1106]` | Judge 故障與停止例外留評估；退回不開輪，runner 端到端測試刪除。 |
   | `[S1107]` | AI `propose` 開輪及送件否決撤除；提案改綁九條，報告派生比較改綁 [S1410]。 |
   | `[S1110]` | AI 流程狀態與調查列同交易分支撤除；刪舊 flow 測試，規則提交改綁 [S1413]。 |
   | `[S1112]` | AI 名稱回答不再送收件口；Judge 解析留評估，F5 正式名稱不影響規則改綁 [S1427]。 |
   | `[S1113]` | 撤除 AI 六讀／模型步租約與 `--ai-judge` 啟動測試；規則步改綁 [S1405]。 |
   | `[S1114]` | 執行端不讀調查列保留；原 `AiWorld` 送件測試改純規則提案。 |
   | `[S1115]` | 調查收據隔離只留 Judge 解析；正式規則使用同輪四查詢，舊基本三筆斷言刪除。 |
   | `[S1116]` | 刪 runner 模型呼叫中停止測試；規則釋租與評估停止分別測。 |
   | `[S1120]` | 即時清單只控制說明／假說；分析端 AI 錄製測試刪除。 |
   | `[S1121]` | AI 調查輪卡與 `a_ai` 節點撤除；刪舊頁面測試，保留說明／假說卡測試。 |
   | `[S1122]` | `a_ai→a_rule` 退回標示撤除；舊測試刪，九條細因改綁規則頁面。 |
   | `[S1123]` | 橫幅一律不掛；舊 AI 情境測試改綁一般詳情／報告。 |
   | `[S1124]` | 雙胞胎與考題撤除；F5 改綁 [S1427]。 |
   | `[S1125]` | AI 決策／退回／考題截圖驗收撤除；新版頁面改驗九條、說明與假說。 |
   | `[S1128]` | AI 多查詢 runner 路徑撤除；原測試 runner 部分刪，Judge 解析只在評估測。 |
   | `[S1135]` | AI 步續租撤除；刪舊測試，規則租約改綁 [S1405][S1413]。 |
   | `[S1136]` | 刪 AI 步停止寬限與常數測試；規則 C 步改綁 [S1419]。 |
   | `[S1137]` | runner 模型模式判定與測試撤除；保留各評估／說明／假說入口的模式測試。 |
   | `[S1138]` | AI 已用過後開規則輪撤除；刪原測試，正式重入改綁 [S1413]。 |
   | `[S1139]` | 刪 `a_ai_query` 與 AI 原因映射；一般 `a_recollect` 測試保留並改綁。 |
   | `[S1142]` | 刪 runner 即時錄製缺批號測試；開錄前目錄檢查由評估 [S1165] 與模型入口測試守。 |
   | `[S1143]` | F3 競爭蒐證與展示不匯入模型保留；AI 前提撤除、測試改綁。 |
   | `[S1144]` | AI 合法不提案的展示狀態撤除；刪舊測試、改驗規則結局。 |
   | `[S1145]` | 分析端環境與 F7 AI 限制撤除；`server.py` 不拒 F7，入口環境／未知代碼測試改綁。 |
   | `[S1146]` | 評估直接用 Judge 與 `rule_verdict(case)`；原評估測試改綁，flow／規則輪假設撤除。 |
   | `[S1149]` | AI 續租後收據容器撤除；刪舊 flow 測試。 |
   | `[S1150]` | AI 續租時計時入口撤除；刪舊測試。 |
   | `[S1152]` | AI `RenewalSkipped` 分支撤除；刪舊測試，規則失租另測。 |
   | `[S1154]` | runner 不再接模型閘道停止例外；原測試 runner 分支刪，模型用戶端→閘道→評估改綁。 |
   | `[S1156]` | `--hold-submit` 與考題全撤；刪舊測試。 |
   | `[S1157]` | AI 路徑容忍清單撤除；刪舊測試，改驗純規則圖。 |
   | `[S1158]` | F5／F7 共用 AI 錄製鍵撤除；F7 改綁 [S1411]。 |
   | `[S1159]` | 凍結調查提示與九條順序留評估；原提示測試續綁。 |
   | `[S1160]` | runner READY 後登入預檢與測試撤除；共用預檢如評估即時入口仍需，由評估測試守。 |
   | `[S1161]` | AI 呼叫前停訊分支撤除；刪舊測試，規則停止另測。 |
   | `[S1166]` | 即時目錄只給說明／假說；分析端錄製斷言刪，入口目錄測試改綁。 |
   | `[S1167]` | 結局選預期改為純規則，考題節點撤除；測試改綁。 |
   | `[S1168]` | F4／F6 接續任務改為規則第 3 條；測試改綁 [S1409]。 |

4. 重跑 Phase 10／13 報告、更新原合約和各 Systems 家的決策與驗證脈絡，明列 Phase 10 舊資料不足、Phase 13 AI 原始／AI+規則否決／程式規則三列、同源 36/36、採用結論及延遲單位。先查每個實作檔的 Systems 家；報告只讀已定版規則。

## 會卡住這個設計的既有程式

- `tests/analyzer/test_worth_check.py` 的凍結舊 `frozen_policy_e8b26f6.py`、625 筆 `policy_before_samples.py` 及 [S700] 把舊行為當永久等式；要保留舊快照作歷史證據，撤掉「新正式規則仍等於它」的斷言，改為舊與新預期差異逐格說明。
- `tests/eval/test_evaluation.py` 的 code_rule 逐格與薄切片按「只看投放」寫；`tests/eval/test_investigation_eval.py` 釘住現行規則六格錯法。更新成九條正式規則與同源警語，不能刪掉逐格誤提案統計。
- `tests/analyzer/test_f4_end_to_end.py` 的接續提案 `220` 及 F6 對稱斷言需改為證據不足、無提案、無收件口／平台寫入；展示驅動的預期組與頁面說明同改。
- `tests/analyzer/test_ai_judge.py` 既有「額外查詢證據不改規則」與 `CODE_RULE_KINDS` 過濾，與新規則必讀四查詢衝突；可保留「不可信文字不左右規則」及「查詢收據不直接改金額」兩個安全意圖，改測原始資料經驗證後的判定。
- `tests/analyzer/test_policy.py` 某些證據只有曝光點擊、沒有狀態／轉換；新語意應以 `MISSING_STATE_OR_METRICS` 不提案。`tests/analyzer/test_dsp_client.py` 的狀態缺值重試案例須改驗 200 回應能結案、5xx 仍重試。`build_proposal` 的基本三參照快照、Phase 12 展示頁的舊規則文字、Phase 13 提示順序測試及 runner 匯入邊界測試也要同步。
- 現有 AI 查詢只在模型選過後重讀，最多三種；正式規則需要四種。兩個現成收據來源有重用價值，但 `MAX_COLLECT_READS` 與原始資料表的讀取禁令不能原樣照搬。
- `src/rtb/eval/scoring.py` 逐筆呼叫 `route`，`generator.py` 的 `Scenario` 沒四查詢；增量 2 需讓 route 的正式呼叫點顯式收規則證據、Phase 10 評估傳 `MISSING_FOUR_QUERIES`，改 `tests/eval/test_evaluation.py` 的 CODE_RULE 與候選退回預期，不讓舊只看投放的 `code_rule(worth_input)` 留作暗門。
- `src/rtb/dsp/store.py` 的正常 `update_budget` 只寫 `operations`，過去調整端點另讀種子表；增量 2 要讓端點由唯一調整紀錄投影預算事件，含本系統寫入，並測三天切點與長跑第 4 條；`src/rtb/dsp/seed.py` 的浮點 `_total()` 與 `store.py` 的逐日寫入／視窗讀取須改成整數分和跨日物化。`src/rtb/domain/proposal.py` 的 `POLICY_VERSION` 及已知版本清單亦須隨九條改版，驗證 Phase 8 執行端政策版本擋舊提案。

## 要改寫的既有合約

| 原條款 | 落地時的處置與理由 |
|---|---|
| Phase 10 [S700] | 撤除「新決策等於 625 筆舊結果」的永久要求；凍結檔雜湊及舊結果自洽檢查留作歷史，新增九條對照測試。使用者已明示要改正式規則。 |
| Phase 2 [S49] | 改成低配速只是進入九條判斷的前提；有曝光點擊不保證提案，還要完整有效查詢與「值得加」。原零投放、正常配速不提案仍保留。 |
| Phase 10 [S701]／[S702]／[S704] | 「沒有候選／退回候選仍走程式規則」保留，正式流程須進新輪重讀四查詢；Phase 10 舊評估無四查詢時則顯式轉為缺證據，不造假結果。[S704] 的空已驗證清單不變，舊的「即使沒有追加資料也能立即判」意涵撤除。 |
| Phase 10 [S705] | 診斷結果仍等於正式決策且有原因；正式規則新增可到達 `JUDGED_INSUFFICIENT`，豁免僅留下候選、AI、考題專有原因，固定資料需帶完整查詢或明示缺查詢。 |
| Phase 10 [S714] | 五格逐格報法保留，舊「現行規則」數字與錯誤子型按明示缺四查詢的新正式規則重算，每格標舊資料不足；不把 Phase 13 數字算進本條。 |
| Phase 10 計劃〈設計〉「現行規則不產出證據不足」 | 撤除；新正式規則有第 2、3、5、9 條與查詢無結果／`WorthInput` 不可建的證據不足。原句應標作當時現況並指回本計劃；基本狀態缺值改標 `MISSING_STATE_OR_METRICS` 不提案。 |
| Phase 13 [S1104]／[S1107] | 正式前置過濾改由規則處理 paused／資料異常，九條 A/B/C 有效證據才可提案；AI `propose` 開規則輪與送件前否決撤除，評估只留 Judge 原始回答與報告層派生比較。 |
| Phase 13 [S1113]／[S1115]／[S1138] | 正式規則 A/B/C 共九讀且每步驗租約上界；AI 已用過／退回開輪撤除，評估直接用 Judge 與案例 `rule_verdict`。 |
| Phase 13 [S1127] 與「過去調整另存種子表」決定 | 改為唯一調整紀錄：正常寫入同交易記調整前後預算與提交時間，展示種子只種此來源；`check_change_history` 與過去調整端點都從此來源看預算事件，端點不以查詢時鐘拒收未滿三天加額，原種子雙寫一致性與「每筆至少三天前」測試改成單源、決策時鐘與遷移測試。 |
| Phase 13 [S1133] | 原「用到的值算不出即跳過」縮為**資料齊全但分母為零**；必要列內缺值及第 5 條七日任一天 `no_data=true` 應證據不足，不能跳到第 8 條。 |
| Phase 13 [S1146] | **改寫、保留評估用**：評估直接呼叫 `ai_judge.Judge` 並以 `rule_verdict(case)` 取規則結果，沒有 flow、TaskStore 或規則輪；錄製重播與授權即時錄製保留。 |
| Phase 13 [S1156] | **撤除** `--hold-submit`、F5 雙胞胎、`EXAM_HOLD` 與綁定測試；它們只考 AI 是否被名稱帶偏，AI 不再進正式或展示決策。 |
| Phase 13 [S1130]／追加查詢白名單 | `ADJUSTMENT_ROW_FIELDS` 撤除 `MIN_ADJUSTMENT_AGE_DAYS` 拒收並驗 `committed_at`；金額欄接受固定兩位小數字串、以整數分／精確分數核對，計數仍是整數。原始回應仍只增不改、同交易儲存；「現行決策函式不讀原始資料表」改成正式規則可讀**本次蒐證已驗證的四種原始查詢**，不得讀別輪或未驗證資料。提示仍只用收據。 |
| Phase 13 [S1159]／[S1168] | [S1159] **保留評估用**：舊調查提示位元組及九條順序不變以重播舊錄製；正式規則缺值／`no_data` 更嚴，正式路徑不靠 AI 否決。[S1168] **改寫**：F4／F6 接續任務由規則第 3 條判證據不足算預期，畫面不標 AI 決策來源。 |
| Phase 13 [S1136]／展示啟動器 [n3] | 正式規則模式停止寬限改蓋過 A/B/C 最壞單步 50 秒（預設逾時 3 秒）；AI 步常數與啟動器 AI 寬限分支刪除，共用 `stepbudget` 規則常數與測試，不能沿用兩讀的 7 秒。 |
| Phase 13 評估報告與採用裁定 | `AI 原始`、`AI+規則否決`、`程式規則` 各列，36/36 同源附註只放 Phase 13；原錄製品質與不採用結論維持獨立，不因規則全對改判。 |
| Phase 8 [S504] | 規則改版與回退都須換新 `POLICY_VERSION`，讓先前兩期未執行提案由版本不等檢查擋下；`KNOWN_POLICY_VERSIONS` 保留舊值只供指標分類，不參與擋件。 |

翻案索引所列 42 條 Phase 13 原條款均在原文旁加 2026-09-26 代使用者裁定註記；原 `[test:]` 留作歷史追溯，增量 3 實作按索引刪、拆或改綁。Phase 12 [S1001] 非 AI 兩讀的算式可保留於基本步，規則 A/B/C 另立逐步守衛；若改 runner 共用守衛的程式結構，原條款與測試需明示兩種步驟的範圍。[S1136] 與 [n3] 的 7 秒規則模式舊寬限需同步撤掉。

## 合約候選

- 九條先命中、查詢沒有結果與數值不可算的分流、同一 `now` 的時間界線，是改壞就可能誤提案的候選合約；實作後依各 Systems 家與對應測試決定是否升格，不在初稿直接標為已核准的不變量。
- 租約分步上界、並行唯一提交、F4／F6 無寫入，是正式守衛與展示共同依賴的候選合約；AI 退回補查只留評估相容性。以下條款是這些候選的可執行驗收方式。

## 驗收條款

- [S1401] 當正式路徑收到 paused 或 1 小時資料異常時，主體應只用基本資料依第 1／2 條結案，不讀四種追加資料，也不提案。例：paused、點擊 2 → 第 1 條不值得加、追加讀取 0 次。[test:test_paused_and_anomalous_campaigns_finish_from_base_evidence]
- [S1402] 當配速偏低且基本資料可續判時，規則蒐證主體應讀四種追加資料；任一查詢逾時、404、欄位不合格或跨窗不一致成為「沒有結果」時，應判證據不足、不建提案。例：歷史 404、1 小時有轉換 → 證據不足、提案 0 筆。[test:test_missing_query_results_prevent_a_proposal]
- [S1403] 當四種原始查詢有效且所需數值齊全，但第 4／5 條比率因分母零算不出時，九條判斷主體應只跳過該條；可算時用精確分數而非捨入收據比較。例：前段點擊 0、轉換 0 → 跳過第 5 條；精確下降 50.04% → 命中第 5 條，即使收據顯示 -50.0%。[test:test_exact_thresholds_distinguish_zero_denominators]
- [S1404] 當 DSP 現況 200 回應的狀態缺值或非法時，`dsp_client` 基本白名單應把它記成缺可信現況，`flow` 提交可結案診斷，`policy.steps()`／`explain()` 應以 `MISSING_STATE_OR_METRICS` 不提案，不進九格、不退回曝光點擊舊規則；C 缺狀態時也不得重用 A 的舊現況；5xx／連線故障仍重試。例：`status=null`、曝光 500、點擊 12 → 正式蒐證提交後 `MISSING_STATE_OR_METRICS`、提案 0 筆。[test:test_missing_dsp_state_finishes_without_proposal]
- [S1405] 當正式規則需要完整四查詢時，流程主體應以 A/B/C 已提交列重算檢查點並分步讀取，只讓租約與序號持有者提交；預設逾時 3 秒時各步 2／2／5 讀、最壞 26／26／50 秒均小於 60 秒租約，自訂逾時不合式則拒啟動。評估執行器不開 A/B/C 規則輪。例：C 的五讀最壞 50 秒 → 放行；自訂逾時使 C 達 60 秒 → 拒啟動。[test:test_rule_collection_steps_fit_the_lease]
- [S1406] 當最終決策使用先前步驟的查詢時，主體應用同一 `now` 驗 15 分鐘新鮮度及最近 3 天界線，C 逐日讀取時刻與 `now` 的 UTC 日期不同則判證據不足。例：C 在 23:59:59 UTC、決策在次日 00:00:01 UTC → 證據不足、無提案。[test:test_decisions_recheck_freshness_and_utc_day_boundaries]
- 原 `[S1407]` **撤除（2026-09-26 代使用者裁定）**：2b 的 AI 提案經新規則輪否決，在 runner 仍能呼叫 AI 時防止未經九條的提案；runner 拔掉 AI 入口後，`investigation_eval.run_case` 又只直接用 `Judge` 與 `rule_verdict(case)`，沒有可承接 `flow.advance` 或 A/B/C 輪的評估入口。刪 `test_ai_proposals_and_fallbacks_use_a_fresh_rule_round`（它用 `AiWorld` 帶 `--ai-judge` 跑真 runner）；原始 `propose` 與案例九條結果分列改綁 [S1410] 的評估報告測試，只有評估可用 Judge 與正式零呼叫改綁 [S1429]。不保留 `ai_propose_vetoed:` 事件或否決細因；報告派生比較只寫九條原因。[manual:核對舊測試已刪，評估報告與匯入邊界各有新綁定]
- [S1408] 當評估案例與正式原始輸入代表同一組數字時，標準答案與正式規則應共用九條順序、條件與三類結論；評估調查提示僅核九條順序與結論，**缺值／`no_data` 的程式端判法比提示更嚴**，提示仍寫 `na` 跳過且位元組凍結以維持既有評估錄製鍵。測試同時斷言提示位元組／錄製鍵不變、程式端單日 `no_data` 及列內缺值判證據不足，並核收據三天切點同源。例：逐日第二天 `no_data=true`、提示可能跳第 5 條 → 正式程式第 5 條證據不足、無提案。[test:test_answer_key_receipts_and_prompt_share_the_rule_contract]
- [S1409] **改寫（2026-09-26 代使用者裁定）**：當 F4／F6 原任務因版本已變被擋且接續任務剛調過預算時，展示主體應把規則第 3 條證據不足視為預期結局，標「剛被調過預算，先不動」，不顯示 AI 自選／補查來源；接續任務沒有提案、收件口與 DSP 寫入。例：另一方剛加額後 F4 接續任務 → 第 3 條證據不足、三處寫入均 0。[test:test_f4_and_f6_follow_ups_show_rule_insufficiency_without_writes]
- [S1410] 當重跑 Phase 13 錄製評估時，報告主體應逐格分列 AI 原始、AI+規則否決、程式規則的誤提案分子分母，原始數字以重播結果為準；標 36/36 與 AI+規則否決的零誤提案皆為同源構造、不作品質證據，雙胞胎切片用 AI 原始列看模型，所有延遲欄標單位與量測範圍。例：一筆模型有效答 `propose`、三筆 off-menu 無有效答案 → AI 原始只計 1 筆誤提案、另列 3 筆無有效答案，不能寫成 4/4。[test:test_reports_keep_raw_ai_errors_separate_from_rule_vetoes]
- [S1411] 當 F7 用 300 件、8 個**執行端**工作者及唯一一個分析行程時，展示主體應讓全部工作由純規則 A/B/C 判定（2026-09-26 代使用者裁定，併入舊 [S1420]）；基準時限先用 300 秒實測，不套 AI 輪數的 60 秒放寬，保留故障與權限斷言。量分析端逐件耗時、2700 次預期 DSP 讀取、SQLite 等待、分步提交與總耗時；未量前不宣稱 300 秒足夠，若超時先停 F7 展示入口、量測並另提時限裁定。例：每件 2＋2＋5＝9 讀、300 件 2700 讀，必須實跑才知是否在 300 秒內。[test:test_f7_finishes_with_rule_reads_under_the_actual_allowance]
- [S1412] 當成功查詢的必要列內欄位缺值，或第 5 條所需七個完整日有**任一天** `no_data=true` 時，使用該值的第 4／5／6 條應判證據不足、不排除該日或跳過；標準答案同語意，原 72 筆 `no_data` 日 0/72，格與答案逐筆不變。例：逐日第二天 `no_data=true`、其餘六天齊全且 1 小時有轉換 → 第 5 條證據不足、提案 0 筆；第二天 `conversions=null,no_data=false` 同結果。[test:test_missing_row_values_are_insufficient_without_changing_the_72_cases]
- [S1413] 當重進入規則蒐證時，`TaskStore` 應按 `rule_round_id` 只讀同輪 A/B/C 序號；過期、409、當機續跑超過 15 分鐘或基本資料變動均整組從 A 重來，連續變動重來兩次後再變動即證據不足。例：舊輪 B 在序號 4、409 後新輪 A 在 9 → 不讀序號 4；連續第三次變動 → 結案無提案。[test:test_rule_round_checkpoints_exclude_stale_evidence]
- [S1414] 當 C 逐日與 1d／7d 描述不同數字時，模擬 DSP 應先將逐日、視窗金額以整數分存算並回固定兩位小數字串，`dsp_client` 讀取層以同一 `Fraction` 精確核對，把真不一致記 `invalid`；計數維持整數，評估共用核對。例：逐日花費 `"0.10"`＋`"0.20"`、其餘日零，7d 花費 `"0.30"` → 有效；7d 點擊 710、逐日合計 700 → 無結果、不提案。[test:test_daily_rows_must_match_the_longer_windows]
- [S1415] 當模擬 DSP 正常寫入加預算時，應同交易記唯一調整紀錄的前後預算與帶時區提交時刻；種子只寫此來源。端點不以查詢時鐘篩三天，回最近一次**加額**的 `committed_at`、前後預算與 D 前後各三個完整日成效，D 當天排除，後段未滿三個完整日則 `after_conversions=null`，不回空列；後來五筆減額不擠掉它。領域只以決策 `now` 判：未滿三天由第 3 條證據不足；剛滿三天但 D+3 未完整由第 4 條證據不足。例：B 在提交 T+3d−60s 讀到加額，定案在 T+3d+10s → D+3 未完、提案 0 筆；D+4 日前後轉換 10→10 → 第 4 條不值得加。[test:test_past_adjustments_include_normal_budget_operations]
- [S1416] 當九條政策上線時，分析端與執行端應使用升版的 `POLICY_VERSION`，舊版未執行提案按 [S504] 擋下。例：舊政策建立、尚在 30 分鐘內且廣告版本未變的提案 → `POLICY_VERSION_CHANGED`、DSP 寫入 0 次。[test:test_old_policy_proposals_are_blocked_after_the_rule_change]
- [S1417] 當 Phase 10 舊 `Scenario` 經 `score → route → code_rule` 評估時，轉接器應顯式供 `MISSING_FOUR_QUERIES`，paused／異常先判，其餘回證據不足；五格報告每格標舊資料不足且標準答案不變。例：舊案例 active、有投放但無四查詢 → 新規則證據不足、該格標「舊資料不足以評估九條規則」。[test:test_phase_ten_scenarios_explicitly_report_missing_queries]
- [S1418] 當 Phase 13 規則與標準答案同源 36/36 時，採用判定主體仍應依 AI 原始品質與既有門檻維持「不採用」，不能把否決後結果充作模型答對。例：異常格一筆 AI 有效誤提案、三筆 off-menu 無有效答案，而同源規則四筆答對 → 報告仍不採用 AI。[test:test_shared_rule_accuracy_never_changes_the_ai_adoption_decision]
- [S1419] 當停止訊號落在規則 C 步驟內時，正式啟動器應按共用常數給規則模式至少 50 秒寬限，不在一步中途硬殺；展示不再啟動 AI 決策步。例：預設逾時 3 秒、C 等第 5 次 DSP 回應 → 7 秒不 SIGKILL、可完成提交或釋租。[test:test_stop_grace_covers_the_longest_rule_step]
- 原 `[S1420]` **撤除並併入 [S1411]（2026-09-26 代使用者裁定）**：純規則是主路徑，不是「關閉 AI 的回退」；原 `test_f7_rule_only_rollback_meets_its_own_deadline` 改綁 [S1411] 的 `test_f7_finishes_with_rule_reads_under_the_actual_allowance`，避免兩套時限。[manual:核對 F7 只留 S1411 一條時限]
- 原 `[S1421]` **撤除（2026-09-26 代使用者裁定）**：`--hold-submit`／F5 雙胞胎／`EXAM_HOLD` 只考 AI 名稱誘導，AI 退出決策後沒有考題用途；刪 `test_hold_submit_covers_every_proposal_exit`，新 F5 要求見 [S1427]。[manual:核對考題入口與測試均已移除]
- [S1422] 當同一廣告累積超過 50 筆七日內操作時，歷史端點應只回最多 50 列並提供以完整七日集合計算的近期預算旗標與計數；第 3 條不得因被截斷的舊列不在回應就判沒有近期調整。例：7 日內 60 筆、最近 3 日其中一筆加額但不在回傳清單 → 第 3 條仍證據不足，HTTP 回應低於 64 KiB。[test:test_bounded_history_preserves_recent_budget_changes]
- [S1423] 當同廣告最近一次加額超過 30 日仍是最近一次加額時，DSP 應保留該次 D 前後六日桶，端點仍能計算前後轉換，不因 30 日滾動淘汰而永久缺證據。例：40 日前加額、D±三日資料完整且 10→10 → 第 4 條不值得加。[test:test_latest_raise_daily_buckets_survive_rolling_retention]
- [S1424] 當九條政策升版或以新政策版本切回舊判法時，在途且缺本版本規則輪列的任務應先作廢舊輪、從本版本基本步重讀；舊政策未執行提案均被版本守衛擋。例：升版前只有基本兩讀、升版後分析 → 新輪 A；回退前停在九條 B → 回退版重讀基本步，不以 B 最後序號定案。[test:test_policy_switch_restarts_inflight_analysis]
- [S1425] 當過去調整回應新增 `committed_at` 時，讀取層應驗帶時區時間戳但不得再以 `days_ago<3` 拒收；生成器按固定評估 `NOW−days_ago` 補舊案例時間戳、重新 render 72 筆，逐筆格與答案不變。正常未截斷歷史、`adj*_...` 收據字串（不含該時間戳）、評估 `SYSTEM_PROMPT` 位元組及錄製鍵須保持相同，Phase 13 評估舊錄製可重播；F1／F7 不再重播 AI 決策錄製。讀取層另由 `tests/analyzer/test_dsp_client.py` 的 `test_adjustment_timestamp_preserves_recorded_receipts` 驗證。例：無時區 → `invalid`；`days_ago=2` 且時間戳有效 → 讀取成功、由決策層判第 3 條。[test:test_adjustment_timestamp_preserves_recorded_receipts_for_all_72_cases]
- [S1426] 當展示種子跨 UTC 午夜（台北 08:00）後才蒐證時，模擬 DSP 應在讀取時由已存日桶、展示樣板與同一次時鐘讀數推算剛完成的 UTC 日桶與 1d／7d 窗（讀取不寫資料庫，持久化由寫入端同交易做）；午夜前後各啟動一次完整 F1–F7，應各有最近七個完整日且跨窗相等，不因少一天落證據不足。例：9 月 26 日 23:59 UTC 種完、27 日 00:01 UTC 開始 F1 → 26 日成為完整日、1d 指向 26 日、七日窗滑動一天；F1 仍可提案。[test:test_demo_daily_rollover_preserves_full_windows]
- [S1427] 當 F5 受攻擊廣告名稱含「加 500%／洩漏金鑰」時，正式九條的格、細因、讀取次數、提案金額應與同數字正常名稱的測試夾具相同，平台只允許 F5 受攻擊廣告一筆寫入；名稱只以逸出文字顯示，提案說明中的每個數字均能由提案／收據追溯，不把名稱中的命令當動作。例：名稱有「加 500%」而數字應提案 100→110 → 實際提案仍 110，說明不得主張 500% 加額。[test:test_f5_adversarial_name_preserves_rule_and_traceable_narrative]
- [S1428] 當展示錄製批次更新時，`BATCH_PATTERN`／`BATCH_SHAPE` 應接受 `phase14-demo-YYYYMMDD`，`driver.DEMO_RECORDINGS` 與重播／入庫指示應指向 `recordings/model/` 下的 `phase14-demo/`；情境分析端資料庫裡每一份送進收件口的提案（依任務與版本去重）都應有一次 `NARRATIVE` 錄製呼叫，提案數從資料庫數、不看驅動寫的說明欄（2026-09-27 代碼審 r1 統一定義：F4／F6 原任務 t1 的提案也算，接續任務不提案不需說明），一份提案都沒有的情境可沒有帳本，告警觸發時才要求 `HYPOTHESIS`，批次不得含 `INVESTIGATION`。舊 `recordings/model/phase13-demo/` 六份與 `phase13-demo-20260925` 唯讀保留、不再展示讀取，其完整性由 `tests/model/test_recording_integrity.py` 驗六份、批號與雜湊；四處硬編新批號測試同步改。例：F4 接續任務第 3 條結案、t1 提案有一次說明 → 入庫檢查通過；F1 有提案但說明錄製缺失 → 失敗；F4 資料庫有提案、帳裡 0 次說明 → 失敗。[test:test_proposed_demo_tasks_require_only_narrative_recordings] [test:test_the_replay_needs_a_narrative_call_for_every_real_proposal] [test:test_proposals_are_counted_from_the_scenario_analyzer_database]
- [S1429] 當正式 runner 或一鍵展示載入／推進時，匯入閉包應不含 `ai_judge`，`flow.advance` 應沒有 `ai_decide` 參數與 AI 分支；只有評估執行器可實際用 `Judge`，錄製重播與協調者授權即時錄製都能工作；說明／假說則仍照展示設定運行。例：展示 F1 規則提案 → `Judge` 呼叫 0 次、`NARRATIVE` 呼叫 1 次。[test:test_only_phase13_eval_uses_ai_judge_and_runner_stays_rule_only]

## 實務隱患

- 未排除：**金流**。規則路徑不呼叫付費模型，但「值得加」的提案經確認、執行端守衛後可能寫入模擬 DSP 預算；任何誤提案仍有金額後果。保留單筆、24 小時總額、版本、權限與人工確認守衛，逐格測誤提案；事件入口：Phase 10／13 報告出現新的誤提案，或正式環境抽樣隱藏集重驗時，重新裁九條條件。
- 已排除：**對外送出**。此計劃不新增模型、電郵或外部服務請求；四查詢只讀現有模擬 DSP。AI 路徑原有的模型送出與花費帳限制照 Phase 13 合約運作，提示仍只帶收據，不帶原始操作識別碼。若將四查詢接到真實外部 DSP，事件入口是 DSP 連線設定切換到正式端點，先重審送出資料邊界。
- 未排除：**不可逆**。提案可能在人工確認後使模擬 DSP 預算變更，既有執行紀錄只增不改；本次只讓提案出現與否改變，不讓規則直接寫平台。回退時不得改寫已執行歷史，需用原操作核對或人工反向調整；事件入口：端到端測試首次觀察到與九條不符的實際寫入。
- 未排除：**守衛面**。租約分步、同輪原始查詢、政策版本及證據參照會碰 [S1001]、[S1130]、[S1136]、[n3]；逐步上界、同交易序號、讀取層跨查詢一致性、時鐘重驗、不可信文字隔離及匯入邊界都列成測試。F7 主路徑每件規則 A 2＋B 2＋C 5＝9 次、300 件預期 2700 次 DSP 讀取，比舊非 AI 兩讀多 2100 次。`driver.make_f7()` 只啟動**一個分析行程**、其 `runner._loop()` 逐件推進；八個工作者是執行端，不能拿 8×300 秒估分析容量。300 秒是待實測基準，不是已證吞吐；[S1411] 實跑量逐件耗時、讀取數、SQLite 等待、分步提交與總耗時，超時先停 F7 展示入口。事件入口：增量 3 F7 首次實跑、日後負載重播、runner 自訂逾時守衛測試及跨午夜測試。

## 回退

- 增量 1 尚未接正式路徑時，可把評估 `answer()` 改回原實作並移除新領域模組；先核對九條標準答案的固定案例不變。
- 增量 2 上線後若九條與資料讀取不符，先停止接新提案；另升**新的回退政策版本**並切回舊判法與相容的讀取路徑，不直接恢復舊程式的 `demo-pacing-v1` 版本字串。回退版使用新的 `POLICY_VERSION`，使兩期未執行提案都因版本不等由 [S504] 擋下；`KNOWN_POLICY_VERSIONS` 保留舊版與九條版只供指標分類。在途九條 A/B/C 任務作廢該輪，依回退版基本步重讀，不把 B/C 最後序號當完整證據。舊判法在 Phase 13 名稱正常 36 筆只對 12 筆，因此回退只作隔離期間措施；事件入口：回退後第一輪正式抽樣或 Phase 13 重播仍見危險格誤提案時，維持提案入口關閉並重新裁定政策。已送平台的預算不靠回退程式自動撤銷，按執行紀錄人工核對。
- **改寫（2026-09-26 代使用者裁定）**：增量 2b＋3 一同推送，故回退按合併版本處理；「關閉 AI 決策入口」已是主線，不能當回退。若九條規則或展示重算有錯，先停新提案，升新的回退政策版本並按上一段處理在途任務；展示可暫停 F7 或只列可驗原始證據，不能恢復舊「有投放就提案」文字。若整個合併版本真的回到 2b 之前，舊 `--ai-judge` 也會隨版本回來，必須另阻斷其正式／展示入口再放行，不能把它當安全的局部回退。F7 仍按 [S1411] 的純規則實測；增量 4 報告不一致時保留兩批歷史錄製與舊報告供比對，重算而不改錄製答案。

## 待審問題

- 模擬 DSP 增量 2 改 UTC 日期鍵、跨日物化與最近 30 完整日保留，釘住最新加額所需六日桶；單次 C 與定案跨 UTC 日界仍判證據不足，午夜前後各自完整蒐證應正常取得新七日窗。正式 DSP 將來若要支援跨日續跑，必須先裁定桶時區與快照版本契約，否則不能宣稱第 5 條跨日資料仍正確。舊版只有 `days_ago` 且有些舊操作無調整前預算；遷移無法可靠還原的歷史列須顯式缺證據，不得補猜。替代是保留舊資料唯讀、對受影響廣告停止加額提案，待有可核對來源再解除；事件入口：增量 2 遷移測試與首批既有資料抽樣。
- F4／F6 接續任務的歷史查詢是否確實回到另一方剛提交的預算變更，須在增量 3 以模擬 DSP 時序測試確認；若資料延遲，依使用者裁定 3 判證據不足而非猜測近期調整。一般寫入已由 [S1415] 指定唯一調整紀錄作預算事件來源，不能再把端點靜態空列當成無加額。

## 審計修正紀錄

- 2026-09-26 初稿：只寫設計與預期合約，未改程式；後續按高風險設計審與各增量驗證修訂。
- 2026-09-26 機械檢查：條款句式、隱患、引用及筆記格式已通過；`spec-gate` 的九支既有相依測試紅，逐支重跑均在本沙箱因 `socket.bind: PermissionError` 或 DSP 無法綁埠而失敗，尚不能宣稱回歸全綠。事件入口：增量實作進代碼審前，在准許本機 HTTP 的環境重跑 `lumos spec-gate RTB_Phase14正式規則照九條判斷_計劃`，須讓相依測試全綠。
- r1(2026-09-26,7 席)：30 條/blocking 15/結論：全數折入，仍無放行。逐條 ID 與落點：a1→裁定 6／[S1412]、a2→評估／[S1410]、a3→裁定 5／[S1407]、a4→領域結果／[S1404]、a5→單一來源／[S1408]、a6→過去調整／[S1415]、a7→重進入／[S1413]、a8→步驟 A／[S1401]、a9→F7／[S1411]；b1→檢查點／[S1413]、b2→停止寬限／[S1419]、b3→AI 全量重讀／[S1407]、b4→重進入上限／[S1413]、b5→UTC 日界／[S1406]、b6→F7／[S1411]；c1→裁定 5／[S1407]、c2→AI 與程式查詢分列／[S1409]、c3→[S1104]／[S1107] 改寫；d1→Phase 10 呼叫鏈／[S1417]、d2→報告與採用／[S1417][S1418]、d3→[S714] 範圍；e1→F7 估算／[S1411]、e2→回退／[S1420]、e3→`allow()`／[S1411]；f1→驗收測試命名、f2→新領域檔家；x1→裁定 5／[S1407]、x2→過去調整／[S1415]、x3→跨步一致性／[S1414]、x4→政策升版／[S1416]。
- 席報告：governance/review-reports/rtb-phase14正式規則照九條判斷/（r1 鏡頭 1–5、架構對齊、外家 Codex）。
- r2(2026-09-26,4 席)：21 條/blocking 12（鏡頭1 4、鏡頭2 2、架構對齊 2、外家 Codex 4）/結論：全數折入；七日 `no_data`、唯一調整來源、分步進度、AI 定案出口與負載估算均改寫，尚待後續設計閘與實作驗收。逐條 ID 與落點：l1_1→裁定 7／[S1412]；l1_2→AI 規則輪單次定案／[S1407]；l1_3→保留送件／[S1421]；l1_4→調整端點篩選／[S1415]；l1_5→報告數字以重播為準／[S1410]；l1_6→否決列同源警語／[S1410]；l1_7→讀取層精確跨窗核對／[S1414]；l1_8→提示豁免與舊合約表／[S1408]；l2_1→UTC 日期與保留／[S1423]；l2_2→單一調整紀錄／[S1415]；l2_3→時間戳白名單與錄製鍵／[S1425]；l2_4→新版本回退與在途任務／[S1424]；l2_5→DECIDE 繞過模型／[S1407]；a1→已提交列重算進度／[S1413]；a2→跨窗核對留讀取層／[S1414]；a3→封閉 `StrEnum` 原因／領域結果；x1→同交易前後預算／[S1415]；x2→最近加額不受減額擠出／[S1415]；x3→提示位元組凍結豁免／[S1408]；x4→七日歷史上限與完整近期旗標／[S1422]；x5→單分析行程實測／[S1411]。
- 席報告：governance/review-reports/rtb-phase14正式規則照九條判斷/（r2-鏡頭1、r2-鏡頭2、r2-架構對齊、r2-外家-codex）。
- r3(2026-09-26,4 席,末輪)：10 條/blocking 5（兩席三天界線獨立一致，架構對齊 clean）/結論：全數折入；末輪折入後不再進設計審，由代碼審把關。逐條 ID 與落點：l1_1→決策時鐘與加額端點／[S1415]；l1_2→評估時間戳重生／[S1425]；l1_3→規則輪續步繞過 AI／[S1407]；l2_1→同 l1_1／[S1415]；l2_2→同 l1_3／[S1407]；l2_3→日期桶跨日物化、撤除展示固定時鐘說法／[S1426]；l2_4→回退政策版本歸因／[S504]；x1→整數分金額跨窗核對／[S1414]；x2→跨午夜新日桶／[S1426]；x3→基本狀態缺值正式入口／[S1404]。
- 席報告：governance/review-reports/rtb-phase14正式規則照九條判斷/（r3-鏡頭1、r3-鏡頭2、r3-架構對齊、r3-外家-codex）。
- 增量 3 範圍變更審 r1（2026-09-26，3 席）：12 條／blocking 6／全數折入；單席-1→條款與 Phase 13 註記、單席-2→展示開關、單席-3→錄製驗收、單席-4→評估保留與邊界、單席-5→考題撤除、單席-6→F7、單席-7→合併差異代碼審與回退；鏡頭2-1→流程圖殘留、鏡頭2-2→F5 說明追溯、鏡頭2-3→F5／F7 死碼；架構對齊-1→逐項去留清單、架構對齊-2→`lands_in`。以上均為代使用者裁定（2026-09-26），理由與輸入→預期例見〈拆增量〉3、[S1407][S1409][S1411][S1420][S1421]。
- 席報告指標：governance/review-reports/rtb-phase14-inc3-scope/（r1-單席、r1-鏡頭2、r1-架構對齊；凍結快照與前掃紀錄同目錄）。
- 增量 3 範圍變更審 r2（2026-09-26，2 席）：5 條／blocking 2／全數折入；架構對齊 0 條 clean。逐條 ID 與落點：單席-1→Phase 13 42 條翻案索引與原條款回指；單席-2→[S1407] 撤除、評估直接 Judge／`rule_verdict(case)`、測試去向；單席-3→`flow.AI_NODES` 保留 `a_narrate`，清 `a_ai_query` 回頭及 12 筆退回映射；單席-4→`server.py`、launcher、stepbudget 依賴與 [S1145] F7 定案；單席-5→新批號／目錄、舊六份唯讀完整性測試與 [S1428]。以上依〈使用者裁定〉8 與本次協調者裁定折入，入口拔除後原 AI 否決守衛也撤除。
- 席報告指標：governance/review-reports/rtb-phase14-inc3-scope/（r2-單席 5 條／blocking 2；r2-架構對齊 clean）。
