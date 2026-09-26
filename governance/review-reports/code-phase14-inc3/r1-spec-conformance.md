severity: minor

# spec-conformance 審查報告(r1)

範圍:〈使用者裁定〉8、〈拆增量〉3 逐項清單(刪/留/改)、[S1409][S1411][S1427][S1428][S1429]、
Phase 13 翻案索引(42 條抽樣覆蓋約 15 條含全部關鍵條)。[S1410][S1418] 屬增量 4,不判。

## 總體裁定

逐條對照下來,〈使用者裁定〉8、〈拆增量〉3 的 9 個大項(刪 runner/流程層、刪考題、改展示開關、改展示證據與圖、
改 F5/F7、留評估、改匯入邊界與測試、改錄製與批次驗收、推送順序)全部**已實作**,且與 diff、repo 現狀逐一核對
一致;[S1409][S1411][S1427][S1428][S1429] 五條均**已實作**,綁定的測試存在且內容確實驗證對應行為;
抽樣的 Phase 13 翻案索引條款(S1100/S1104/S1107/S1113/S1116/S1125/S1128/S1138/S1143/S1144/S1145/
S1146/S1156/S1161/S1166/S1167)均已依右欄處置(撤除的完全查無殘留、改綁的新條款/測試確實存在)。
只有一條文件準確性層級的偏差,列在下面,不影響行為。

## 逐項裁定

### 使用者裁定 8:AI 退出加額決定,只留三角色

裁定:已實作。`ai_judge.py` 文件明寫「這支檔只給評估執行器直接呼叫:分析端驅動、流程層與一鍵展示都不匯入它」;
`tests/test_spawn_boundary.py::test_only_phase13_eval_uses_ai_judge_and_runner_stays_rule_only` 靜態掃描
`rtb.analyzer.runner`、`rtb.demo.driver`、`rtb.demo.server` 三個匯入閉包確認不含 `rtb.analyzer.ai_judge`、
`rtb.analyzer.modelgate`、`rtb.modelclient`,並掃全原始碼確認只有 `rtb.eval.investigation_eval` 匯入
`ai_judge`。三角色(提案說明 narrate、告警假說 hypothesis、找規則模式)中前兩者維持既有入口,第三者正確地
另開 [[Projects/RTB_Phase15AI找規則模式_計劃]] 不在本次範圍。
file: `src/rtb/analyzer/ai_judge.py:1-4`、`tests/test_spawn_boundary.py:613-648`

### 拆增量 3 逐項(刪/留/改)

1. **刪(runner/流程層)**:已實作。`runner.py` 全文檔搜尋 `--ai-judge`、`--hold-submit`、`_unsafe_ai`、
   `_unsafe_hold`、`_judge_for`、`_open_ai_gate`、`MODEL_LINE`、`GateOpener`、`class _Ai` 均為 0 命中;
   `_advance_one` 固定呼叫 `instrumented.rule_source` 與 `rule_round.decide`。`flow.advance` 簽章確認無
   `ai_decide`、`clock` 參數,只有 `rule_decide`。`NoAction`/`ProposalDecision`/`RuleContinue` 留在
   `flow.py`,`AiContext`/`AiOutcome`/`QueryMore` 搬到 `investigation.py`,與清單描述完全一致。
   `rule_round_opened_by_ai`、`held_rule`、`AI_PROPOSE_VETOED`、`ai_propose_vetoed:` 全庫(src+tests)
   搜尋只剩 `ai_judge.py` 文件裡一句「撤除」的歷史說明。
   file: `src/rtb/analyzer/runner.py:10,120,135`、`src/rtb/analyzer/flow.py:229-242`
2. **刪(考題)**:已實作。`EXAM_HOLD`、`a_exam_hold`、`EXAM_PASSED`、`EXAM_FAILED`、`EXAM_UNCOMPARABLE` 在
   `src/` 全庫搜尋 0 命中;`policy.NoActionReason` 已無 `EXAM_HOLD` 成員,原位置改成純註解說明舊資料庫
   字串唯讀留存。
   file: `src/rtb/analyzer/policy.py:138-148`
3. **改(展示開關)**:已實作。`AiSetup` docstring 明寫「分析端 AI 判斷那一半撤除,分析端永遠不帶模型參數」;
   `NEVER_LIVE`、`F7_RECORDED_ONLY`、`AI_JUDGE_FLAG` 全庫 0 命中;`stepbudget.py` 只剩
   `collect_step_worst_seconds`、`rule_step_worst_seconds`、`rule_stop_grace_seconds` 三支通用函式,無
   AI 步專屬函式。
   file: `src/rtb/demo/driver.py:151-159`、`src/rtb/stepbudget.py:9-12,41-58`
4. **改(展示證據與圖)**:已實作。`flow.AI_NODES` 現值 `frozenset({"a_candidate", "a_narrate"})`,`a_ai`
   節點與 `a_ai_query` 回頭均已移除(`test_observe.py:482` 斷言 `a_ai_query` 不在 `BACK_TRANSITIONS`);
   `ai_fallback_problems` 只剩 `recordings.py` 裡一句「隨增量 3 撤除」的說明。F4/F6 用
   `RULE_THREE_LABEL = "剛被調過預算,先不動"`,`test_f4_and_f6_follow_ups_show_rule_insufficiency_without_writes`
   驗證摘要文字、無提案、無 `x_`/`i_` 節點、`narrative_json is None`。
   file: `src/rtb/demo/flow.py:52-53`、`src/rtb/demo/driver.py:148`、
   `tests/demo/test_ai_demo.py:297-320`
5. **改(F5/F7)**:已實作。`F5_TWIN`、字串 `"c3"` 在 driver.py 與 F5 端到端測試裡 0 命中,
   `test_f5_runs_without_a_twin_or_an_exam` 直接斷言 `not hasattr(driver_module, "F5_TWIN")` 且
   `reader.history("t3") == ()`;`c2.budget == 300` 確認旁證廣告未被寫入。[S1427] 端到端測試見下。
   F7:`F7_CAMPAIGNS, F7_LIMIT, F7_WORKERS = 300, 1234, 8`,`scenario.time_limit_seconds == 300` 斷言原
   時限未放寬;`test_f7_shares_one_recording_key_with_f1` 全庫 0 命中(已刪,[S1420] 併入 [S1411])。
   file: `tests/demo/test_ai_demo.py:324-341`、`src/rtb/demo/driver.py:920`、
   `tests/demo/test_driver.py:1461-1508`
6. **留評估(AI 決策模組)**:已實作。`ai_judge.py` 公開介面僅 `open_investigation_gate`、`gate_complete`、
   `Judge`、`_Inputs`、`_rule`、`_outcome_code`、`_fallback`、`_campaign_name`;`raw_replay`、
   `preflight_ok`、`stop_requested`、`.hold` 等屬性 0 命中(只剩文件裡一句歷史提及)。`rule_verdict(case)`
   確實存在於 `rtb/eval/investigation_report.py:58`,供評估直接產生九條結果。
   file: `src/rtb/analyzer/ai_judge.py:47-172`
7. **改(匯入邊界與測試)**:已實作。`GATE_USERS = {"rtb.analyzer.narrate", "rtb.analyzer.ai_judge"}`,
   `CALL_MODEL_USERS`/`SENDING_ENTRIES` 含 `rtb.eval.investigation_eval`、不含 `rtb.analyzer.runner`;
   `CALLER_USERS["INVESTIGATION"] = frozenset({"rtb.analyzer.ai_judge"})`。`test_ai_judge.py` 現存函式清單
   已無 `held_rule`/`ai_propose_vetoed` 相關測試名;`fake_recordings.py` docstring 明寫「假錄製只造提案
   說明(NARRATIVE);需要時另造假說」;`test_ai_launcher.py::test_only_model_entries_get_model_env_and_only_when_live`
   驗證 `Role.ANALYZER` 不拿模型環境變數。
   file: `tests/test_spawn_boundary.py:41-42,441-459`、`tests/demo/fake_recordings.py:1-5`
8. **改(錄製與批次驗收)**:已實作。`recordings.py`:`BATCH_PATTERN = re.compile(r"phase14-demo-\d{8}")`,
   `DEMO_RECORDINGS = .../recordings/model/phase14-demo`;
   `test_the_phase13_demo_batch_stays_as_read_only_history` 逐檔雜湊釘住舊 `phase13-demo` 六份、批號
   `phase13-demo-20260925` 且 `driver.DEMO_RECORDINGS != PHASE13_DEMO`。
   `test_proposed_demo_tasks_require_only_narrative_recordings` 驗證 F4/F6 無帳本也算過、有提案情境要有
   說明錄製。
   file: `src/rtb/demo/recordings.py:5-6,46-47`、`tests/model/test_recording_integrity.py:211-224`、
   `tests/demo/test_ai_demo.py:344-379`
9. **推送順序**:屬流程規範,非程式行為,diff 無法驗證,不適用「已實作/縮水」判準,故不列入計數。

### [S1409] F4/F6 接續任務規則第 3 條、無提案、無寫入

裁定:已實作。`tests/demo/test_ai_demo.py:297-320`
引句:「規則第 3 條{driver_module.RULE_THREE_LABEL}」in verdict.summary
file: `tests/demo/test_ai_demo.py:303`

### [S1411] F7 全規模純規則實測(併原 S1420)

裁定:已實作。`tests/demo/test_driver.py:1462-1508` 用真實情境規模(300 件、8 工作者)、原 300 秒時限跑一次,
量測 DSP 讀取數(`expected_reads = F7_CAMPAIGNS * 9`)、規則步數(`steps >= 3 * F7_CAMPAIGNS`),不放寬時限。
file: `tests/demo/test_driver.py:1472-1508`

### [S1427] F5 名稱不影響九條、逸出顯示

裁定:已實作。`test_f5_adversarial_name_preserves_rule_and_traceable_narrative` 比對受攻擊名稱與同數字
正常名稱的 `state`/`decided`/`reads`/`endpoints`/`writes`/`proposal` 全相同、`reads == 9`,並驗證
`trusted` 程式數字段逐字相同、`ADVERSARIAL_NAME not in trusted`。已知的「500% 剛好對上曝光 500」限制有依
規範寫入 Systems 筆記並帶 `REVISIT:2026-12-31`。
file: `tests/analyzer/test_f5_end_to_end.py:191-213`、
`docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:371`

### [S1428] 展示批次只留說明/假說錄製、F4/F6 可無帳本

裁定:已實作。`test_committed_demo_recordings_replay_f1_to_f6_with_narratives` 為協調者錄製前的**唯一預期
紅測試**,與計劃「等錄製的紅只有一支」的自陳一致;`test_proposed_demo_tasks_require_only_narrative_recordings`
以假錄製驗證 F4/F6 無帳本也判過。
file: `tests/demo/test_ai_demo.py:344-378`

### [S1429] 匯入閉包不含 AI 決策模組、`flow.advance` 無 `ai_decide`

裁定:已實作,見上方「使用者裁定 8」與「拆增量 3 之 1」的引用,測試同一支
`test_only_phase13_eval_uses_ai_judge_and_runner_stays_rule_only` 綁定。
file: `tests/test_spawn_boundary.py:614-648`

### Phase 13 翻案索引(抽樣覆蓋)

`[S1100][S1104][S1107][S1113][S1116][S1125][S1128][S1138][S1143][S1144][S1145][S1146][S1156][S1161]
[S1166][S1167]` 抽樣核對:應完全撤除者([S1125] AI 決策/退回/考題截圖、[S1161] AI 呼叫前停訊分支)在
src、tests 全庫搜尋均 0 命中;應改綁者(如 [S1107] 改寫證據參照見 `policy.py:307-331` 與
`tests/analyzer/test_rule_round.py:111`、[S1138] 改綁 `instrumented.py:138,152` 與
`tests/analyzer/test_ai_judge.py:364`、[S1145] 改綁 `server.py:549`、`launcher/__init__.py:101`、
`tests/demo/test_ai_launcher.py:14`)均可在對應檔案找到明確回指與新測試,未見「刪了但沒改綁」或「改綁到不存在
的條款」的孤兒情形。
file: `src/rtb/analyzer/policy.py:307-331`、`src/rtb/analyzer/instrumented.py:138,152`

`claims/prompt-injection.json` 的 `policy` 文字已改為「要不要加預算只由程式照九條規則看數字決定…」,`scope`
移除 `src/rtb/analyzer/ai_judge.py`、新增 `src/rtb/analyzer/narrate.py`,`evidence` 把原
`test_ai_judge.py::test_an_injected_name_can_only_flip_propose_or_not` 換成
`test_f5_end_to_end.py::test_f5_adversarial_name_preserves_rule_and_traceable_narrative`(kind 改
`end_to_end`),`harness` 移除 `tests/analyzer/test_ai_judge.py`、`tests/analyzer/test_investigation_e2e.py`、
`tests/model/__init__.py`、`tests/model/fakes.py` 剛好四支,與計劃文字「scope 拿掉 ai_judge.py、補
narrate.py,harness 拿掉只為 AI 路徑而在的四支」逐字對上。
file: `claims/prompt-injection.json`(diff 內對應區塊)

## 發現 1:「清單與程式對不上」註記②對 `renew_lease` 的描述與 diff 實際結果不符

severity: minor
blocking: 否

spec 原文:計劃〈拆增量〉3 增量 3 段落寫「任務模組的 `renew_lease`、`record_model_call`、
`commit_step(investigation=…)` 與啟動器 `Process.next_line` 同理只剩測試用」。

實查:`record_model_call`、`commit_step(investigation=...)`、`Process.next_line` 確實仍各自被至少一支
測試呼叫(`tests/analyzer/test_investigation_review_r2.py:245,247`、
`tests/demo/test_observe.py:491`、`tests/demo/test_ai_launcher.py:39-40`),但 `renew_lease` 在這次
diff 裡呼叫它的測試(`test_investigation_review.py`、`test_investigation_review_r2.py` 內多處)整批被刪除
(全是 `-` 行、無對應 `+` 行復原呼叫),目前 `src/`、`tests/` 全庫搜尋 `renew_lease` 只剩定義本身與一句
docstring 提及,沒有任何呼叫端——不是「只剩測試用」,而是已無任何呼叫者(比清單描述更空)。這不影響執行行為
(反正沒有正式入口),但計劃文字對現狀的描述不準確。

引句:「renewed = store.renew_lease(lease.current, clock)」
file: `governance/review-reports/code-phase14-inc3/r1-snapshot.patch:1131`(此行為 `-` 刪除行,對應呼叫
已消失;現狀見 `src/rtb/analyzer/task_store.py:961` 定義處已無呼叫者)

## 縮水+未實作共 0 條
