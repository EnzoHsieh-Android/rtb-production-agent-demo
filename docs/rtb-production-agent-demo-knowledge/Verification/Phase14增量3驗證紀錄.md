---
type: verification
status: pending
date: 2026-09-27
valid_under: 僅 Phase 14 增量 2b＋增量 3 本工作樹(未提交、合併差異未過代碼審);全套測試、ruff、mypy、宣稱驗證器;展示批次尚未錄製
revalidate_when:
  - 協調者錄完 phase14-demo 批次、搬進入庫目錄之後
  - 2b＋增量 3 合併差異的代碼審改動程式之後
  - 分析端驅動、展示驅動或說明入口的參數再變動時
tags:
  - type/verification
  - status/pending
plan_refs:
  - "[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]"
---
# Phase14增量3驗證紀錄

範圍:[[Projects/RTB_Phase14正式規則照九條判斷_計劃]] 增量 3(〈使用者裁定〉8、〈拆增量〉3 逐項去留清單):AI 退出正式與展示的加額決策,只留提案說明與告警原因假說;評估保留 Judge、錄製重播與授權即時加錄製。狀態 pending:展示錄製批次要協調者用真 claude 錄,錄好之前「入庫展示批次重播」那一支測試照設計是紅的。

## 紅綠(新測試放在增量 3 之前的原始碼上跑)

先紅:暫存目錄放增量 3 之前(增量 2b 工作樹)的 src 加現在的 tests,只跑新增的測試;後綠:本工作樹。
2026-09-27 實跑 21 支新增測試(另 3 支的展示頁面測試所在檔在舊原始碼上收集就失敗):
- 紅 17 支:Judge 評估語意 3 支(舊 Judge 要續租回呼與 flow 的 AiContext)、展示 9 支(舊驅動仍帶 --ai-judge、F4/F6 接續任務照 AI 判、F5 有雙胞胎、伺服器拒 F7、流程圖有 AI 節點、批次檢查要分析端調查錄製)、驅動接續任務結案 1 支、觀察器回頭節點 1 支、舊批次唯讀雜湊 1 支(舊預設目錄就是 phase13-demo)、匯入邊界 [S1429] 1 支、入庫展示批次重播 1 支(等錄製,錄前錄後都紅)。
- 收集錯誤 2 檔:tests/demo/test_ai_page.py、tests/demo/test_present.py(新測試引用的 `RULE_DECIDES`、`RULE_THREE_LABEL` 在舊原始碼不存在)→ 本工作樹綠。
- 舊原始碼上就綠的 3 支(釘住既有行為、不是先紅):[S1427] test_f5_adversarial_name_preserves_rule_and_traceable_narrative(沒開 AI 的正式路徑本來就不受名稱影響)、test_only_model_entries_get_model_env_and_only_when_live(沒帶 --ai-judge 時分析端本來就沒有模型變數)、[S1411] test_f7_finishes_with_rule_reads_under_the_actual_allowance(量測型)。

## 逐項清單的處理

照 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈實作解讀〉增量 3 那一段;這裡只記驗證怎麼看。

- 刪(runner/流程層)、刪(考題)、改(展示開關)、改(展示證據與圖)、改(F5/F7)、留評估、改(匯入邊界與測試)、改(錄製與批次驗收):逐項都有對應的新測試或改綁測試(見下一節),全套綠。
- 清單與程式對不上的幾支無入口程式(`instrumented.investigation_source`、任務模組 `renew_lease`/`record_model_call`/`commit_step(investigation=…)`、啟動器 `Process.next_line`):協調者在代碼審 r1 後裁定照「沒有入口就刪」,已刪,見〈代碼審 r1 修正〉。F5 的 c2(旁邊不調整的廣告)照舊保留:清單「只保留受攻擊廣告 c1」解讀為撤雙胞胎 c3。

## 新增、刪除、改綁的測試

新增(21 支):
- [S1429] test_only_phase13_eval_uses_ai_judge_and_runner_stays_rule_only(tests/test_spawn_boundary.py)
- [S1427] test_f5_adversarial_name_preserves_rule_and_traceable_narrative(tests/analyzer/test_f5_end_to_end.py)
- [S1411] test_f7_finishes_with_rule_reads_under_the_actual_allowance(tests/demo/test_driver.py)
- [S1409] test_f4_and_f6_follow_ups_show_rule_insufficiency_without_writes、test_f4_follow_up_shows_rule_three_without_ai
- [S1428] test_proposed_demo_tasks_require_only_narrative_recordings、test_the_replay_needs_a_narrative_call_for_every_real_proposal、test_proposals_are_counted_from_the_scenario_analyzer_database、test_committed_demo_recordings_replay_f1_to_f6_with_narratives(等錄製)、test_the_phase13_demo_batch_stays_as_read_only_history
- 其餘:test_pages_have_no_ai_decision_cards_nodes_or_exam、test_decision_hero_says_the_rules_decide、test_the_pure_rule_path_allows_no_ai_nodes、test_a_return_to_collecting_always_maps_to_recollect、test_only_listed_scenarios_get_live_model_env_for_their_entries、test_only_model_entries_get_model_env_and_only_when_live、test_the_server_refuses_only_unknown_codes_in_the_live_list、test_f5_runs_without_a_twin_or_an_exam、test_the_new_task_counts_as_settled_only_once_it_has_an_outcome、test_several_queries_chosen_in_one_round_are_one_query_step、test_a_case_that_already_used_the_ai_falls_back_without_calling_the_model、test_an_ai_propose_is_kept_as_the_raw_answer_without_a_proposal

刪除(70 支,整支檔 tests/analyzer/test_runner_model_line.py 在內):流程層 AI 那一步(續租、收據容器、RenewalSkipped、記次、停止、提交忙碌重付)、分析端驅動帶 --ai-judge 的端到端(守衛、停止、模式判一次、即時錄製、登入預檢、hold-submit、AI 提案否決與開輪)、展示 AI 輪卡與考題、故障沒走到、AI 輪數放寬、模式行、F7 共用錄製鍵、雙胞胎、`ai_fallback_problems` 守衛。每一支的原綁定條款都在原出處改綁或改 `[manual:]`(Phase 13 計劃 26 條、Phase 12 [S1028]),`lumos spec-trace` 對 Phase 12/13 計劃 0 條新增懸空。

改綁(改測新語意、測試名保留):test_code_prefilters_run_before_any_model_call 等 Judge 解析與收據測試改用評估的呼叫方式;test_the_executor_never_sees_model_rounds 改用純規則提案;test_the_investigation_eval_runs_the_same_ai_judge 拿掉續租;test_narrate_and_the_runner_book_recorded_replays_into_a_scratch_ledger 只留說明那半;test_stop_grace_covers_the_longest_rule_step 改驗 AI 寬限已撤;test_the_mapped_enums_are_the_eighteen_in_the_plan 回到十八個;test_formal_flow_map_and_every_sample_route_match 48 節點/72 邊。

## F7 全規模實測([S1411])

2026-09-27 本機:300 件、8 個執行端工作者、1 個分析行程、時限 300 秒 → 16.1 秒走完(含自動核可);DSP 讀取 2700 次(= 300 × 9,沒有重來)、規則步 900 步、DSP 讀取本身合計 3.8 秒;逐件從建立到提案 p50 5.58 秒、最長 7.72 秒(單一分析行程輪流推進)。SQLite 等待與分步提交沒有另外量,以「分析跨度 7.7 秒 − DSP 讀取 3.8 秒」當上界。不需要時限變更。

## 其他檢查

- ruff check src tests tools:通過;mypy:通過(95 支)。
- 宣稱驗證器:5 條宣稱、78 支證據測試通過(五份清單雜湊重算;prompt-injection 的 policy、證據、scope/harness 照上文改)。
- lumos spec-trace:Phase 12 懸空只剩舊有的 S1034,Phase 13 0 懸空,本計劃只剩增量 4 的 S1410/S1418。lint 每篇 0;doctor 1 個 issue 是既有的(連到還沒建的 Phase15 計劃),增量 3 之前就有。

## 全套

2026-09-27 本工作樹:3314 passed、1 skipped、1 failed(902 秒)。唯一的紅是等錄製的 test_committed_demo_recordings_replay_f1_to_f6_with_narratives。

## 代碼審 r1 修正(2026-09-27)

協調者已錄 recordings/model/phase14-demo(批號 phase14-demo-20260927)並過入庫檢查。r1 修正逐條落點見 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈實作解讀〉增量 3「代碼審 r1 修正」。每條行為改變都先寫測試、在修正前的原始碼上翻紅再修。

- 已入庫的批次在 r1 修正後照樣重播通過(test_committed_demo_recordings_replay_f1_to_f6_with_narratives 綠),不需要補錄:F4/F6 改說明 t1,t1 的提示跟 F1 逐字相同。
- 兩支舊斷言改寫。一支原本斷言 F4/F6 帳裡沒有說明呼叫,現在改驗恰好一次。另一支「舊記錄不顯示九條」原本用子字串「九條)」判,會誤中「早於九條)」,改比對完整的「程式規則(九條)」。
- 宣稱:五份清單雜湊重算,驗證器 5 條宣稱、78 支證據測試通過。ruff 通過,mypy 通過(95 支)。spec-trace 懸空只剩本計劃增量 4 的 S1410/S1418 與 Phase 12 舊有的 S1034。每篇 lint 0,doctor 0 issues。
- 全套:3320 passed、1 skipped。那次跑有 2 支失敗,就是上一條那兩支舊斷言,改完後兩支所在檔 40 passed。其餘沒有等錄製的紅。

## 代碼審 r2 修正(2026-09-27)

- 百分比寫法補齊:對抗樣本 16 種寫法先在 r1 的程式上跑,15 種翻紅(全形數字那種 r1 已擋);修後全綠,對得回的寫法 4 種照留。F5 端到端證據測試另核 8 種寫法。
- 刪 `instrumented.dsp_evidence_source` 與 `InstrumentedEvidenceSource`,[S50] 三支測試保留原名改走 `rule_source`;[[Systems/展示頁面]] 摘要措辭改正。
- 全套 3342 passed、1 skipped(0 失敗,含已入庫錄製的重播;追加刪 InstrumentedEvidenceSource 後重跑 3341 passed、1 skipped,少的一支是併進 [S50] 的重複測試);ruff、mypy(95 支)通過;宣稱 5 條、78 支證據測試通過;doctor 0 issues。

## 代碼審 r3 末輪修正(2026-09-27)

- 測試補 17 支,在 r2 程式上 14 支翻紅:10 支是數字與百分號之間插看不見或空白樣的字元,4 支是連字號與複數寫法;另外 3 支修前就綠(500 percents 本來就擋,10-percent 與夾不可見字元的 10% 兩支照留)。修後全綠;F5 端到端證據測試另核 4 種。
- test_instrumented.py 起模擬 DSP 改用 pytest fixture。
- 全套 3358 passed、1 skipped、0 失敗;ruff、mypy(95 支)通過;宣稱 5 條、78 支證據測試通過;doctor 0 issues。

## 等錄製與未完成

- (已解)等錄製:協調者已錄 phase14-demo-20260927,test_committed_demo_recordings_replay_f1_to_f6_with_narratives 已綠。
- 本批沒有 HYPOTHESIS:假錄製實跑 F1–F6,六個情境的假說命令列都回「沒有告警」(F6 執行端連不上平台沒達服務水準告警門檻)。要聲稱假說已驗,需要專用的告警情境補錄。
- (代碼審 r1 已修)說明的數字核對原本只比數值,「加 500%」借曝光數 500 過關;百分比現在要對回證據裡的百分比,見〈代碼審 r1 修正〉。
- 本計劃 [S1410] [S1418] 仍懸空,屬增量 4。
