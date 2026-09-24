severity: major

本席範圍:五條宣稱(claims/*.json 未來要寫的那五份)跟 /Users/enzo/rtb-3b 現存測試的對應關係;scope/harness 該含卻沒提到的檔;端到端測試是否真的走到宣稱那條路;Mock-DSP 版本檢查排除 void_operation 的取捨是否成立;作廢冪等性的證據現況。

## 逐條宣稱核對

### claims/idempotency-unknown-outcome.json — 已讀,有 finding(見 F1)

- F1 三支測試存在且名稱吻合:`tests/executor/test_crash_recovery.py::test_f1_timeout_before_commit_is_reconciled_by_a_same_key_resend`、`test_f1_timeout_after_commit_is_reconciled_from_the_operation_record`、`test_f1_a_delayed_commit_and_a_resend_apply_once`。
- F2 對應 `tests/executor/test_crash_recovery.py` 裡的當機恢復測試群(`test_crash_after_the_dsp_commit_is_recovered_from_the_operation_record` 等),存在。
- DSP 儲存層同鍵套用一次由 `tests/dsp/test_store.py`、`tests/dsp/test_void.py` 的既有 ★INVARIANT★ 測試涵蓋(`test_same_key_same_payload_applies_once_and_returns_original_result` 等)。
- F6 的問題見下方 F1 finding。

### claims/permission-guardrail.json — 已讀,無 finding

計劃承認「沒有既有 ★INVARIANT★,證據節點在實作增量時從前掃列的測試檔逐支選定」,查證現存測試確實逐項都有候選:比例上限 `tests/executor/test_guardrails.py::test_a_budget_increase_is_bounded_by_the_ratio_cap`、決策過時 `tests/executor/test_stale_decision.py::test_a_stale_decision_is_blocked`、版本已變 `tests/executor/test_version_conflict.py::test_a_dsp_version_conflict_is_acknowledged_as_version_changed`、人工核可只對簽的那份有效 `tests/executor/test_approval.py::test_an_approval_covers_only_its_own_stage` 與 `test_an_approval_is_void_when_scope_expiry_hash_or_stage_changes`、故障注入旗標關閉即拒 `tests/dsp/test_server.py:106`(`fault_injection_disabled`)、分析端送不出故障標頭由 `tests/analyzer/test_dsp_client.py::test_the_dsp_client_module_never_mentions_the_fault_header` 直接證明。延後選定合理,不是漏洞。

### claims/concurrency.json — 已讀,無 finding

F3/F4 測試名稱與內容吻合:`tests/analyzer/test_task_lease.py::test_two_real_threads_advancing_the_same_task_pay_for_the_analysis_once`、`tests/executor/test_multi_worker.py::test_two_live_workers_process_a_message_once`、`tests/analyzer/test_f4_end_to_end.py::test_f4_a_stale_proposal_is_replanned_from_the_current_state`、`tests/executor/test_version_conflict.py::test_two_concurrent_writers_reproduce_a_version_conflict`。端到端測試確實跑真的模擬 DSP、真的分析/執行行程(讀過 `test_f4_end_to_end.py` 開頭說明),不是只測 mock 呼叫。

### claims/prompt-injection.json — 已讀,無 finding

`tests/analyzer/test_f5_end_to_end.py` 開頭明寫「用真的模擬 DSP(行程內執行緒)、真的分析行程、真的收件口、真的執行迴圈跑一次」,確實端到端。信任邊界差異測試 `tests/analyzer/test_trust_boundary.py`、提案白名單 `src/rtb/domain/proposal.py:241`(`unknown_field` 檢查)與 `tests/analyzer/test_dsp_client.py::test_fields_outside_the_allowlist_never_reach_the_evidence` 都存在且對得上宣稱子句。

### claims/aggregate-blast-radius.json — 已讀,無 finding

`tests/executor/test_f7_end_to_end.py::test_f7_many_small_increases_stop_at_the_aggregate_limit`、`tests/executor/test_aggregate_limit.py::test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it`、`test_two_workers_cannot_race_past_the_aggregate_limit`、核可測試 `tests/executor/test_approval.py` 全部存在,子句逐一對得上。

## Mock-DSP 措辭改窄的取捨:成立

讀 `src/rtb/dsp/store.py:377-393` 的 `void()`:只查 `voided_keys`/既有操作紀錄,從未呼叫 `_next_state`,不碰 `campaigns` 表版本欄;`_execute_in_transaction`(`store.py:405`)裡版本檢查只出現在一般寫入路徑,void 走的是完全不同的方法。`tests/dsp/test_store.py:83-90` 的 `test_every_write_action_on_the_http_routes_has_a_version_check_example` 已經把「作廢路由不改廣告、不經寫入入口,另有測試(tests/dsp/test_void.py)」寫進註解,`WRITE_ACTION_PARAMS`/`CAMPAIGN_WRITE_ACTIONS` 兩支既有窮舉測試都只涵蓋 `update_budget`、`pause_campaign`。這代表程式行為與現存測試設計本來就已經把 void 排除在「版本不符拒收」之外——計劃只是把圖譜 `docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md:41` 那條措辭過寬的 ★INVARIANT★(「每一種寫入動作」)改成跟程式碼與既有測試一致,不是新開洞。

## 作廢的冪等性:有證據,但只涵蓋循序與「作廢對寫入」的競態,沒有「作廢對作廢」的並行測試

`tests/dsp/test_void.py::test_the_dsp_voids_a_key_or_reports_the_commit_that_won` 循序呼叫兩次 `void("k1")` 都回 `voided`(冪等);`test_voiding_needs_a_void_capability_scoped_to_the_key` 用 HTTP 也重複呼叫驗證同樣行為;`test_voiding_and_a_late_write_are_linearized_by_the_write_lock` 額外驗證重開資料庫後再作廢一次仍是 `voided`。但全檔沒有一支測試讓兩個執行緒同時對同一把鍵呼叫 `void()`(只測 void-vs-write 的競態,`race()` 輔助函式只接受 `winner` 是 `"void"` 或 `"write"` 兩種角色)。結構上 `_begin_write_transaction`(`store.py:371`)用同一把 SQLite 寫入鎖序列化,`INSERT OR IGNORE`(`store.py:385-387`)理論上能安全處理,但這是我讀程式碼推出來的,不是測試證明的;若五條宣稱裡任何一條之後把「void 本身的並行冪等」也算進涵蓋範圍,目前拿不出對應測試節點。

## Finding

### F1 claims/idempotency-unknown-outcome.json 把 F6 端到端列為證據,但 F6 測的是死信重放新鮮度、不是冪等對帳

severity: major
blocking: 是(語意上宣稱範圍大於證據,即使機械檢查全過,也代表這條清單一旦寫死就是假陽性;判準:validator 的第 6 步只查測試存在且通過,查不出 covers 語意錯配,必須靠這次獨立審查在寫清單前擋下)
引句:「證據:F1 三支、F2、F6 端到端與 DSP 儲存層、收件口的單元與並行測試。」
file: `/Users/enzo/rtb-3b/tests/analyzer/test_f6_end_to_end.py:1-8` — 檔案開頭自述「事故 F6 端到端…舊工作進了死信,之後廣告、政策或決策變了;重放時必須重新驗證並拒絕舊決策」,整份檔案(用 `grep -n "idempotency" tests/analyzer/test_f6_end_to_end.py` 只命中一行 `capability_key=TEST_KEY`,不是冪等鍵)完全沒有測「同一把冪等鍵套用一次」或「用同一把鍵對帳、不換新鍵重送」。
file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md:15` — 圖譜自己把 F6 的 ★INVARIANT★ 定義成死信重放要「照常跑每一關」,跟冪等對帳是不同主題。
現況段落(`/tmp/rtb-phase11-r1.md:31`)把 F6 跟「DSP 同鍵只套用一次」並列,是同一份文件內部就已經把兩個不同事故的證據混在一起;寫 claims/idempotency-unknown-outcome.json 時若照抄這句話選 F6 的節點編號,會通過機械驗證(測試存在、會過),但語意上沒有涵蓋到宣稱裡「同一把鍵對帳、不換新鍵重送」這句話。

### F2 scope 的定義沒有要求納入被多支測試共用依賴的正式程式檔(sqlitekit.py / capabilitykit.py / httpkit.py),雜湊守不到它們的改動

severity: major
blocking: 是(這幾支檔的行為是至少三條宣稱的機制基礎,漏列會讓「新舊」步驟形同虛設;判準:若改壞這些檔案不會讓任何一條清單的雜湊比對失敗,就代表 scope 定義有結構性缺口,不是單一清單填錯)
引句:「scope:這條宣稱涵蓋的正式程式檔清單,每一項帶 sha256」
file: `/Users/enzo/rtb-3b/src/rtb/dsp/store.py:32` — `from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy, begin_immediate, connect`,同鍵冪等與並行安全的鎖語意實際由 `sqlitekit.py` 的 `begin_immediate` 提供。
file: `/Users/enzo/rtb-3b/src/rtb/dsp/server.py:22,62,69` — `DspServer` 同時匯入 `rtb.capabilitykit`(能力憑證驗證,對應 permission-guardrail 宣稱)與 `rtb.httpkit`(故障注入外殼與 Host 檢查,對應 idempotency 與 permission 兩條宣稱)。
計劃全文舉的 scope/harness 範例只提到「模擬 DSP、假物件、注入樣本、conftest」這類測試基礎設施要放進 harness,完全沒提醒作者「跨多條宣稱共用的正式依賴模組」也要放進 scope;若增量 1、2 寫清單時只列 `dsp/store.py`、`dsp/server.py`、`executor/execution.py` 這類「看得到宣稱關鍵字」的檔案,`sqlitekit.py`/`capabilitykit.py`/`httpkit.py` 被改到(例如鎖逾時邏輯、能力驗簽邏輯)時,清單雜湊不會變,驗證器第 3 步「新舊」查不出來,等於這幾支檔案的回歸不受這支驗證器保護。

## 總結

最嚴重等級:major;blocking 條數:2(F1、F2)。五條宣稱本身在現存測試庫裡,除了 F1 指出的 F6 誤植之外,每一句政策白話都能對應到具名、確實跑到端到端路徑的測試;Mock-DSP 措辭改窄的取捨經程式碼與既有測試設計交叉確認成立,不是漏洞掩護;作廢的冪等性有循序與 void-vs-write 競態的證據,但缺 void-vs-write 以外的 void-vs-void 並行測試,目前沒有任何宣稱把這點算進涵蓋範圍,故只記錄不升級為 finding。
