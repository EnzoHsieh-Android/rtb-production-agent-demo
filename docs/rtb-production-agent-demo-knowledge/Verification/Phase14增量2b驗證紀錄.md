---
type: verification
status: superseded
date: 2026-09-26
valid_under: Phase 14 增量 2b,已與增量 3 一起提交為 b2fc5122f100、合併差異過代碼審(卷證 governance/review-reports/code-phase14-inc2b);全套測試、宣稱驗證器、ruff、mypy 與 Phase 13 評估 72 筆錄製離線重播;入庫展示錄製因政策升版有 8 筆模型說明找不到錄製(增量 3 重錄展示批次後解掉,見增量 3 驗證紀錄)
revalidate_when:
  - 協調者重錄展示批次之後
  - 增量 3 接 hold-submit 全部出口、展示分開畫程式複查與 F7 負載之後
  - 規則輪讀取次數、租約常數或政策版本再變動時
tags:
  - type/verification
  - status/superseded
plan_refs:
  - "[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]"
---
# Phase14增量2b驗證紀錄

範圍:[[Projects/RTB_Phase14正式規則照九條判斷_計劃]] 增量 2b(正式規則照九條、規則輪 A/B/C 分步蒐證、缺狀態診斷、證據參照、政策升版、租約守衛與停止寬限、Phase 10 缺四查詢轉接、AI 退回開規則輪)。狀態記 pending,因為入庫展示錄製有一支測試紅(見〈未解決〉),其餘全綠。(2026-10-02 更正:開頭現為 superseded(見文末),由 [[Verification/Phase14增量3驗證紀錄]] 取代;〈未解決〉那支紅的測試已在 b2fc512 刪除,展示重播改由讀 phase14-demo 的 test_committed_demo_recordings_replay_f1_to_f6_with_narratives 守,已綠。)

## 紅綠(先紅:把新測試放在 HEAD 的原始碼上跑)

先紅是在暫存目錄放 `git archive HEAD src` 加現在的 tests 跑;後綠是本工作樹。
- [S1401] test_paused_and_anomalous_campaigns_finish_from_base_evidence:HEAD 上規則輪模組不存在(收集錯誤)→ 綠。
- [S1402] test_missing_query_results_prevent_a_proposal:同上 → 綠。
- [S1404] test_missing_dsp_state_finishes_without_proposal(4 格):HEAD 上 4 紅(狀態缺值丟例外、卡在蒐證)→ 綠;test_missing_state_at_step_c_does_not_reuse_step_a 綠;5xx 重試 test_a_dsp_server_error_on_the_state_is_still_retried 兩邊都綠(守既有行為)。
- [S1405] test_rule_collection_steps_fit_the_lease、[S1406] test_decisions_recheck_freshness_and_utc_day_boundaries、[S1413] test_rule_round_checkpoints_exclude_stale_evidence 與 test_changes_between_a_and_c_restart_twice_then_stop、[S1424] test_policy_switch_restarts_inflight_analysis:HEAD 上收集錯誤 → 綠。讀取次數宣告等於實際讀數:test_underpacing_campaign_reads_three_steps_and_proposes_with_query_receipts(每步 2/2/5)。
- [S1416] test_old_policy_proposals_are_blocked_after_the_rule_change:紅 → 綠。
- [S1417] test_phase_ten_scenarios_explicitly_report_missing_queries:紅 → 綠。
- [S1419] test_stop_grace_covers_the_longest_rule_step:紅 → 綠。
- 領域第 3/4 條消化 committed_at:test_past_adjustment_commit_time_is_judged_on_the_decision_clock、test_past_adjustment_without_or_after_the_decision_time_is_insufficient、test_truncated_history_recent_flag_counts_as_a_recent_change、test_daily_read_on_another_utc_day_is_insufficient:4 紅 → 綠;72 筆格與答案不變(既有 72 筆回歸測試綠)。
- [S1107] 改寫 test_a_model_chosen_proposal_equals_the_formula_proposal、[S700] 改寫 test_the_frozen_old_rule_stays_as_history_and_nine_rule_differences_are_listed、[S49] 補 test_underpacing_with_delivery_but_only_base_evidence_is_insufficient:HEAD 上紅 → 綠。

## 其他檢查

- ruff check src tests tools:通過;mypy src:通過。
- 宣稱驗證器:5 條宣稱、78 支證據測試通過(清單雜湊與依賴閉包 scope/harness 已重算;policy/evidence 語意未變)。
- Phase 13 評估 72 筆入庫錄製離線重播(空 HOME、只重播):找不到錄製 0 筆、批次驗收通過。
- 全套測試結果見〈全套〉。

## 改了期望的既有測試(理由)

- F4 端到端(Phase 5 S308):接續任務由第 3 條判證據不足、無提案,收件口只有原任務、DSP 只有另一方那一筆(使用者裁定 4)。
- [S700]/[S701]/[S702]/[S704]/[S705]/[S714]、test_policy 各支、test_trust_boundary、test_guardrails 的提案樣本:只看投放的舊規則撤掉,要提案的測試帶齊四查詢;沒有四查詢一律證據不足。
- AI 退回相關([S1105]/[S1106]/[S1115]/[S1131]/[S1138]、代碼審 r1/r2 兩支):退回結果改成開規則輪(RuleContinue、紀錄結果代碼 rule_round);[S1138] 撤「只讀基本兩樣」。
- runner 與 F5/F6/端到端夾具:補種四查詢歷史;步數(提案 7 步、交出 8 步)與端點清單照三步讀調整;沒開 AI 的 5 秒逾時改為拒啟動(C=60 秒)。
- 展示:啟動器規則模式寬限 7 秒改 50 秒;test_basis 帶四查詢、暫停改由第 1 條先判(test_campaign_status_decides_first_and_the_standard_says_so 取代舊「狀態不影響」那支)。

## 未解決

- 政策版本升版後,入庫展示錄製 phase13-demo-20260925 的模型說明提示含政策版本,重播 F1–F6 有 8 筆說明找不到錄製(每個情境 AI 調查呼叫都找得到),test_committed_demo_recordings_have_no_ai_fallback_in_f1_to_f6 紅。代理不准呼叫模型,需協調者重錄展示批次或另裁處置。

## 全套

2026-09-26 本工作樹全套:3338 過、1 略過、1 紅(只有 test_committed_demo_recordings_have_no_ai_fallback_in_f1_to_f6,原因見〈未解決〉),約 15.6 分鐘。展示子集 600 過、同一支紅。

## 代碼審 r1 修正的紅綠(2026-09-26)

- 外家 finder-1:test_negative_or_inconsistent_longer_windows_are_insufficient(6 格)、test_every_invalid_query_result_blocks_a_proposal_whatever_rule_would_hit:先紅 → 綠。
- 外家否決-1/資安-1:test_paused_and_anomalous_campaigns_never_reach_the_model(2 格)、test_an_ai_propose_opens_a_rule_round_instead_of_a_proposal:先紅 → 綠;test_an_ai_propose_is_vetoed_when_the_rules_disagree:先紅(細因沒有否決標記)→ 綠。
- 外家 finder-2:test_step_c_rereads_the_state_after_its_queries:先紅(現況先讀)→ 綠。
- 外家 finder-3、架構對齊-1:test_change_restarts_do_not_carry_over_a_policy_switch、test_progress_is_recomputed_from_plain_records:先紅 → 綠。
- 鏡頭2 毀損:test_a_corrupted_rule_step_row_fails_the_task_instead_of_looping[開 AI]、test_a_corrupted_rule_step_row_fails_the_collect_step:先紅 → 綠。
- 鏡頭3-1:把 `needs_queries` 改成恆假的變異版本上,[S1105][S1131] 兩支共 20 筆翻紅(原本全綠)。
- 資安-2:test_the_change_history_must_belong_to_the_task_campaign:先紅 → 綠。
- 鏡頭4-1:production_report 標註斷言先紅 → 綠。
- 72 筆錄製離線重播(空 HOME):找不到錄製 0、批次驗收通過;宣稱驗證器 5 條、78 支證據測試通過;ruff、mypy 通過。
- 代碼審 r1 修正後全套:3356 過、1 略過、2 紅。紅的一支是展示錄製(協調者稍後重錄);另一支 test_the_suite_never_touches_the_real_ledger,原因是全套執行期間家目錄的真花費帳被外部行程改了(修改時間落在這次全套執行中),單獨重跑 11 支全綠。

## 代碼審 r2 修正的紅綠(2026-09-26)

在暫存目錄放 HEAD(r1 修正版)的原始碼加現在的測試跑,以下每支先紅;本工作樹都綠:
- test_the_change_history_must_belong_to_the_task_campaign(架構對齊-1)
- test_only_the_first_decision_after_an_ai_propose_is_a_veto(鏡頭1-1/鏡頭2-2)
- test_a_corrupted_rule_event_row_fails_the_collect_step(鏡頭2-1)
- test_a_cross_day_daily_read_reports_the_day_boundary_even_with_a_bad_row(外家 finder-1)
- test_recorded_entries_never_touch_the_account_ledger、test_recorded_hypotheses_without_a_ledger_use_a_scratch_ledger、test_model_entries_only_run_with_ai_and_never_without_a_ledger、test_the_gate_decides_mode_ledger_and_recordings_once(錄製模式不退回真帳本)
- test_ai_proposals_and_fallbacks_use_a_fresh_rule_round([S1407] 補綁定):在 r1 程式上就綠,屬補綁定,不是紅綠。
- 72 筆錄製離線重播改帶 `--ledger <暫存路徑>`:找不到錄製 0、批次驗收通過;ruff、mypy、宣稱驗證器(5 條、78 支)通過;`lumos spec-trace` 的 [S1407] 已綁,懸空的 6 條是增量 3/4 的測試。
- 代碼審 r2 修正後全套:3364 過、1 略過、1 紅(只剩入庫展示錄製那支,等協調者重錄);真花費帳隔離那支這次綠。

## 代碼審 r3 修正的紅綠(2026-09-26)

在暫存目錄放 HEAD(r2 修正版)的原始碼加現在的測試跑:
- 先紅 → 綠:test_the_shared_resolver_decides_after_the_mode、test_recorded_entries_never_touch_the_account_ledger(改成每個入口自己的緩衝區後,Phase 10 評估漏印那半翻紅)、test_the_gate_uses_the_shared_resolver_only_in_recorded_mode、test_only_the_first_rule_round_after_the_ai_skips_the_early_checks、test_recorded_hypotheses_without_a_ledger_use_a_scratch_ledger(改驗標準錯誤)。
- 守退化、不是紅綠:test_narrate_and_the_runner_book_recorded_replays_into_a_scratch_ledger 在 r2 程式上綠;把模型閘道改回「沒帶帳本用真帳本」的變異版本上它與 gate 那支翻紅。test_model_entries_only_run_with_ai_and_never_without_a_ledger 驗的是既有可達性(沒開 AI 不跑模型入口),在 r2 程式上也綠。
- ruff、mypy、宣稱驗證器(5 條、78 支)通過;72 筆錄製重播帶 `--ledger <暫存路徑>`:找不到錄製 0、批次驗收通過。
- 代碼審 r3 修正後全套:3368 過、1 略過、1 紅(只剩入庫展示錄製那支,等協調者重錄)。

2026-09-30 狀態改為 superseded(存量漂移 c3),參考 [[Verification/Phase14增量3驗證紀錄]]
