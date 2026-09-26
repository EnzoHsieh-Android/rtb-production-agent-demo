severity: clean

# 對答案審查:RTB_Phase14 增量 2b(正式規則與分步蒐證)

比對範圍:spec `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase14正式規則照九條判斷_計劃.md` 的 [S1401][S1402][S1403](正式路徑那半)[S1404][S1405][S1406][S1413][S1416][S1417][S1419][S1424]、[S1107] 證據參照改寫、使用者裁定 1–7、〈要改寫的既有合約〉表中 [S700][S49][S701][S702][S704][S705][S714][S1104]/[S1107] 那半、[S1113]/[S1115]/[S1138]、[S1130]、[S1136]、[S1001]。diff:`governance/review-reports/code-phase14-inc2b/r1-snapshot.patch`(7130 行,涵蓋 src/rtb/analyzer、src/rtb/domain、src/rtb/eval、src/rtb/demo、tests、docs 知識圖譜)。

逐條核對結論:上述全部條款在 diff 中都找得到對應實作與測試,沒有發現縮水或未實作的項目,也沒有找到 spec 無對應的行為多做。以下逐條列佐證。

## 驗收條款(2b 範圍)

- **[S1401]**(暫停/異常只用基本資料結案、不讀四查詢):`src/rtb/analyzer/rule_round.py` 的 `_base_outcome` 在步驟 A 用 `policy.steps(evidence, now, queries=None)` 判斷,`needs_queries` 為假時直接結案。
  引句:「1. 步驟 A 讀現況與 1 小時指標(2 讀);只用基本資料判得出的...在 A 就結案,追加讀取 0 次」
  file: `src/rtb/analyzer/rule_round.py`(diff 2495-2498)
  測試 `test_paused_and_anomalous_campaigns_finish_from_base_evidence` 斷言 `calls==2` 且不含 `QUERY_ENDPOINTS`。file: `tests/analyzer/test_rule_round.py:5683-5694`

- **[S1402]**(任一查詢沒有結果→證據不足):`nine_rules.decide` 對四查詢逐一檢查 `value is None` 回 `QUERY_NO_RESULT`;`test_missing_query_results_prevent_a_proposal` 用 monkeypatch 造 404/timeout/invalid 四種情形驗證 `judged_insufficient` 且事件記 `query_no_result:<option>`。
  引句:「for query, value in ((QueryKind.LONGER, evidence.longer),...): if value is None: return _insufficient(RuleReason.QUERY_NO_RESULT, query)」
  file: `src/rtb/domain/nine_rules.py`(diff 3969-3975)、`tests/analyzer/test_rule_round.py:5697-5721`

- **[S1403]正式路徑那半**(分母為零只跳過該條,精確分數比較):`nine_rules._final_decision`/`_daily_decision`/`_past_decision` 沿用 `metrics.exact_change`/`exact_ratio` 的 `NO_DENOMINATOR` 語意(增量 1 既有),2b 起 `policy.code_rule`/`rule_round.decide` 直接呼叫同一支 `rules.decide`,首次讓正式路徑走得到這條分流。file: `src/rtb/analyzer/policy.py:2241-2243`、`src/rtb/domain/nine_rules.py:301-330`

- **[S1404]**(現況缺值/非法→缺現況結案,不進九格,不沿用 A 的舊現況):`dsp_client.make_client.fetch` 對 `has_state=False` 時不產生 `CAMPAIGN_STATE` 證據且廣告文字不帶版本;`policy.steps`/`explain` 以 `_has_state` 過濾、回 `MISSING_STATE_OR_METRICS`;`rule_round._decide` 在 C 缺可信現況時直接回 `NoAction`、不拿 A 的。
  引句:「C 缺可信現況就以缺現況結案,不沿用 A 的([S1404])」
  file: `src/rtb/analyzer/rule_round.py`(diff 2777-2780)、`src/rtb/analyzer/dsp_client.py`(diff 1592-1666)
  測試:`test_missing_dsp_state_finishes_without_proposal`(4 格)、`test_missing_state_at_step_c_does_not_reuse_step_a`、`test_a_dsp_server_error_on_the_state_is_still_retried`(5xx 仍重試)。file: `tests/analyzer/test_dsp_client.py:4857-4905`

- **[S1405]**(A/B/C 分步讀、逐步租約守衛):`rule_round.py` 的 `Step`/`STEP_QUERIES`/`declared_reads`,`stepbudget.RULE_STEP_READS={"A":2,"B":2,"C":5}`,`runner._unsafe_rule_steps` 逐步檢查 `< lease`,不符時說明是哪一步。
  引句:「for step, worst in rule_step_worst_seconds(timeout).items(): if not worst < lease: return (f"拒絕啟動:規則輪步驟 {step} 最壞...")」
  file: `src/rtb/analyzer/runner.py`(diff 2909-2917)、`src/rtb/stepbudget.py`(diff 4516-4521)
  測試 `test_rule_collection_steps_fit_the_lease` 精確驗證 26/26/50 秒與 5 秒逾時時 C=60 秒拒絕。file: `tests/analyzer/test_rule_round.py:5725-5742`,並在 `test_investigation_e2e.py` 驗 4 秒放行、5 秒拒絕(diff 5274-5278)。

- **[S1406]**(同 now 驗新鮮度、C 逐日 UTC 日界不同即證據不足):`nine_rules.DailyTrend.read_at` 與 `_daily_decision` 的 `DAY_BOUNDARY` 分支;`rule_round._decide` 用 `_all_young` 重驗三步證據年齡。
  引句:「if daily.read_at is not None and (daily.read_at.astimezone(UTC).date() != now.astimezone(UTC).date()): return _insufficient(RuleReason.DAY_BOUNDARY, QueryKind.DAILY)」
  file: `src/rtb/domain/nine_rules.py`(diff 3951-3956)
  測試:`test_decisions_recheck_freshness_and_utc_day_boundaries`(`tests/analyzer/test_rule_round.py`)、`test_daily_read_on_another_utc_day_is_insufficient`(`tests/domain/test_nine_rules.py:6699-6708`)。

- **[S1413]**(進度由已提交列重算、過期/變動/409 開新輪、連續變動兩次上限):`rule_round.progress()` 從 `rule_steps`/`rule_events` 重算,`collect_plan` 對過期步驟開新輪;`_decide` 對 `_changed` 命中且 `state.changes >= MAX_CHANGE_RESTARTS` 回 `CHANGED_LIMIT` 結案。
  引句:「MAX_CHANGE_RESTARTS = 2  # 版本/基本資料變動連續重來的上限」
  file: `src/rtb/analyzer/rule_round.py`(diff 2534、2593-2631、2781-2786)
  測試:`test_rule_round_checkpoints_exclude_stale_evidence`、`test_changes_between_a_and_c_restart_twice_then_stop`(`tests/analyzer/test_rule_round.py:5777-5832`)。

- **[S1416]**(政策升版,`nine-rules-v1`,舊版擋件):`src/rtb/domain/proposal.py` 的 `POLICY_VERSION="nine-rules-v1"`、`KNOWN_POLICY_VERSIONS` 追加保留舊值。
  引句:「POLICY_VERSION = "nine-rules-v1"」
  file: `src/rtb/domain/proposal.py`(diff 3996-4005)
  測試:`test_old_policy_proposals_are_blocked_after_the_rule_change`(`tests/executor/test_stale_decision.py:5106-5119`)。

- **[S1417]**(Phase 10 情境明傳缺四查詢,報告標「舊資料不足」):`policy.route`/`code_rule` 簽章改必填 `queries`/`now`;`scoring.score` 傳 `MISSING_FOUR_QUERIES` 與固定 `RULE_NOW`;`synthetic_report` 帶 `MISSING_QUERIES_NOTE`。
  引句:「RULE_NOW = datetime(2026, 9, 25, tzinfo=UTC)」
  file: `src/rtb/eval/scoring.py`(diff 4380-4491)
  測試:`test_phase_ten_scenarios_explicitly_report_missing_queries`(`tests/eval/test_evaluation.py:6790`)。

- **[S1419]**(規則模式停止寬限至少 50 秒,取三步最壞):`stepbudget.rule_stop_grace_seconds`、`launcher.stop_grace_seconds` 併入 `rule_stop_grace_seconds(timeout)`。
  引句:「grace = max(STOP_SECONDS, CALLS_PER_STEP * timeout + 1, rule_stop_grace_seconds(timeout))」
  file: `src/rtb/demo/launcher/__init__.py`(diff 3565-3580)
  測試:`test_stop_grace_covers_the_longest_rule_step`(`tests/demo/test_launcher.py:6588`)。

- **[S1424]**(政策升版/回退時在途任務作廢重讀):`rule_round.progress()` 的 `open_` 判斷含 `all(record.policy_version == POLICY_VERSION ...)`,不符即視為已作廢、下一輪從 A。
  引句:「and all(record.policy_version == POLICY_VERSION for _seq, record in mine)」
  file: `src/rtb/analyzer/rule_round.py`(diff 2607-2611)
  測試:`test_policy_switch_restarts_inflight_analysis`(`tests/analyzer/test_rule_round.py:5833`)。

- **[S1107] 證據參照改寫**(規則輪提案列 C 的基本三筆+四種成功查詢收據;AI 直接提案仍只列基本三筆,兩者除參照外逐欄相同):`policy.build_proposal` 文件與行為不變(仍照傳入的 evidence 列參照);`rule_round._decide` 組出的 evidence 含 `_receipts(middle)`+`_receipts(last)`。
  引句:「規則輪傳基本三筆加四種成功查詢的收據;AI 直接提案(增量 3 前)只列基本三筆」
  file: `src/rtb/analyzer/policy.py`(diff 2452-2457)
  測試:`test_underpacing_campaign_reads_three_steps_and_proposes_with_query_receipts` 驗證 `evidence_refs` 恰為 C 基本三筆+B 的歷史/過去調整+C 的長窗/逐日(`tests/analyzer/test_rule_round.py:5653-5674`);`test_a_model_chosen_proposal_equals_the_formula_proposal` 改寫驗證兩路徑除參照外逐欄相同(`tests/analyzer/test_ai_judge.py:4665-4694`)。

## 使用者裁定 1–7

1. 九條順序/門檻數字不變:沿用增量 1 的 `rtb.domain.nine_rules`(本次未改動門檻常數 `RECENT_DAYS`、`DROP_THRESHOLD`、`GAIN_THRESHOLD`),2b 只是把正式路徑接上同一支。
2. 規則模式取得四種追加查詢:`rule_round` 的 B/C 步驟涵蓋 `CHECK_CHANGE_HISTORY`/`CHECK_PAST_ADJUSTMENTS`/`CHECK_LONGER_WINDOW`/`CHECK_DAILY_TREND`。file: `src/rtb/analyzer/rule_round.py:2547-2551`
3. 逾時/404/欄位不合格/跨窗不一致記沒有結果→證據不足;分母零則該條不適用續判:`nine_rules.decide` 的四查詢 `None` 檢查與 `exact_ratio`/`exact_change` 的 `NO_DENOMINATOR` 分流(增量 1 已立,2b 重用)。
4. F4/F6 接續任務依第 3 條落證據不足:`tests/analyzer/test_f4_end_to_end.py` 全面改寫,斷言 `child_row.state is NO_ACTION`、`child_reason=="judged_insufficient"`、`child_rule_events[-1]==("decided","recent_budget_change")`、`inbox_tasks==["t1"]`,DSP 只有另一方那一筆寫入。
  引句:「assert facts["child_reason"] == "judged_insufficient"」
  file: `tests/analyzer/test_f4_end_to_end.py`(diff 5089-5104)
5. AI 的 propose 不是送件許可,規則否決有可區分原因,AI 原始另記——**此裁定的「否決」半段屬增量 3(spec 本文〈AI 退回與展示〉明寫 2b 只做「退回」那半,詳見下方備註),裁定中「退回」半段(AI 已用過/失敗/答非選項時開一次新規則輪全量重讀)已在 2b 完整實作**:`ai_judge._fallback` 對需要四查詢的情形回 `RuleContinue`,`held_rule` 讓開 AI 時的規則輪定案也照 `--hold-submit`。file: `src/rtb/analyzer/ai_judge.py`(diff 1511-1543)
6. 列內缺值(如 `conversions=null`)判證據不足,不可當跳過;committed_at 缺值同樣判證據不足:`nine_rules._past_decision`/`_daily_decision` 對缺值回 `MISSING_ROW_VALUE`;2b 新增 `AdjustmentRow.committed_at`、`_invalid_adjustment_row` 檢查 `is_aware(committed_at)`。
7. 單日 `no_data=true` 使第 5 條整體證據不足,不排除該天:`_daily_decision` 的 `any(row.no_data for row in rows)` 檢查(增量 1 既有,2b 未變動、測試持續釘住)。

## 〈要改寫的既有合約〉表(2b 應改寫項目)

以下條款逐一確認已回到原出處(各自的 Phase 計劃筆記)改寫,並非只在 Phase14 計劃裡片面宣稱:

- **[S700]**:`RTB_Phase10評估與Jev決策點_計劃.md` 撤除「新決策等於 625 筆舊結果」要求,改列九條與舊結果的四類差異(43/4/33/1);測試 `test_the_frozen_old_rule_stays_as_history_and_nine_rule_differences_are_listed` 逐一比對 `NINE_RULE_DIFFERENCES`。file: `tests/analyzer/test_worth_check.py`(diff 6156-6194)
- **[S49]**:`RTB_Phase2任務流程_計劃.md` 追加改寫說明與新測試 `test_underpacing_with_delivery_but_only_base_evidence_is_insufficient`。
- **[S701][S702][S704][S705][S714]**:`RTB_Phase10評估與Jev決策點_計劃.md` 逐條加註九條/缺四查詢語意;`[S714]` 改綁 `test_phase_ten_scenarios_explicitly_report_missing_queries`。
- **[S1104]/[S1107] 那半**:僅 `[S1107]` 半段依 2b 範圍改寫(`RTB_Phase13AI參與決策_計劃.md`);`[S1104]` 前置過濾納入暫停/異常留待增量 3,本輪未動——與 spec 分界一致,非缺陷。
- **[S1113]/[S1115]/[S1138]**:三條均改寫,並各自更新綁定測試(`test_the_runner_refuses_a_model_timeout_that_outlives_the_lease`、`test_extra_query_evidence_never_changes_the_code_rule`、`test_a_task_that_already_used_the_ai_goes_straight_to_the_rule`)。
- **[S1130]**:`ADJUSTMENT_ROW_FIELDS` 相關文字改寫(增量 2a 已動,2b 再次確認消化 `committed_at`)。
- **[S1136]**:改寫為「沒帶 --ai-judge 的舊 7 秒撤掉,改取 max(舊兩讀寬限, 規則輪 A/B/C 最壞)」,對應 `stepbudget.rule_stop_grace_seconds`。
- **[S1001]**:`RTB_Phase12一鍵展示與HTML報告_計劃.md` 補範圍說明(該算式限基本兩讀,規則輪另立 [S1405])。

## 增量 3 範圍條款(僅供參考,未計入未實作)

以下依任務指示不判「未實作」;diff 顯示 2b 已提前做了部分工作,列出供留痕:

- **[S1407] 否決那半**:未實作(AI 答 `propose` 時 2b 仍直接依原公式建提案,`src/rtb/analyzer/ai_judge.py` 的 propose 分支未改動);「退回那半」已完整實作(見使用者裁定 5)。
- **[S1409]**(F4/F6 展示斷言):`tests/analyzer/test_f4_end_to_end.py` 已完整改寫並斷言規則證據不足、無寫入,等同提前完成 F4 的驗收;F6(`test_f6_end_to_end.py`)僅補種四查詢與改用 `rule_source`,未新增規則否決相關斷言(該檔案本就不涉及規則否決,無缺口)。
- **[S1421]**(`--hold-submit` 全部出口集中攔):目前只在開 AI 模式的規則輪 DECIDE 出口以 `ai_judge.held_rule` 攔一次;純規則路徑(未開 AI)的 `rule_round.decide` 本身未套 `--hold-submit` 包裝(`runner.py` 對 `ai is None` 分支直接傳 `rule_round.decide`,未經 `held_rule`)。這是 spec 明文列給增量 3 的「集中在建立/送出 ProposalDecision 前」全出口整合,2b 現狀只覆蓋其中一個出口,其餘出口(純規則路徑)尚未套用——按指示不計入未實作,僅記錄現狀。
- **[S1408][S1410][S1418][S1411][S1420]**:diff 未見改動(`SYSTEM_PROMPT`、報告三列分列、F7 負載驗收均未涉及),與 spec 增量歸屬一致,不計入未實作。

## 判準不準的項目

無。所有條款均能在 diff 或既有(未改動)程式碼中找到明確依據,未發現需要標 ⚠ 的情形。

縮水+未實作共 0 條
