結論:不同意

## 逐子句判斷

**「提案形成之後若另一方先更新了廣告」「帶著舊預期版本的提案不會寫進 DSP」——執行前檢查擋法**
守護測試:`test_f4_a_stale_proposal_is_replanned_from_the_current_state[precheck]`、`test_each_failed_precheck_blocks_the_proposal_without_a_write[version_changed]`(`src/rtb/executor/execution.py` 第 362 行 `signed = precheck(proposal, view) or self._sign(proposal)` 是關鍵分流:precheck 若回傳 BlockCode,根本不會呼叫 `self._sign`/開嘗試/呼叫 DSP)。
判斷:真守。我自己做的改壞實驗(見下)證實會翻紅。

**「檢查通過後 DSP 以版本衝突拒收」**
守護測試:`test_f4_a_stale_proposal_is_replanned_from_the_current_state[dsp_write]`(端到端,真柵欄搶跑)、`test_two_concurrent_writers_reproduce_a_version_conflict`(兩套真執行系統、真 DSP server 並行寫,線程級競態)、`test_a_dsp_version_conflict_is_acknowledged_as_version_changed`(單元測試,三處分流:終點確認、開始一筆撞既有失敗鍵、取件時撞既有失敗鍵)。
判斷:真守,而且是三種不同高度(端到端/真並行/單元)交叉驗證,分量足夠。

**「收件口回報版本已變」**
同上 `test_a_dsp_version_conflict_is_acknowledged_as_version_changed` 加 `test_f4` 端到端斷言 `error_detail == "blocked=version_changed;..."`。
判斷:真守。

**「分析行程另開接續任務重讀現況再規劃」**
守護測試:`test_a_version_conflict_hands_the_task_over_to_a_follow_up`(單元,確認建出接續任務、`follow_up_of` 指回)、`test_f4` 端到端(接續任務的提案 `campaign_version_observed == 2`、`new_budget == FRESH_BUDGET`,是真的重讀現況算出來的新提案,不是照抄舊值)。
判斷:真守。

**「(有給操作查詢時)」這個限定詞**
語意在 `src/rtb/analyzer/flow.py` 第 179 行:`if row.state is TaskState.HANDED_OFF and operation_lookup is None: return row.state`(沒給操作查詢,已交給執行的任務原地不動,不呼叫任何協作者)。
判斷:**沒人守**。這 7 支綁定測試沒有一支用 `operation_lookup=None` 呼叫過 `flow.advance`(`test_replan.py` 的 `step()` 預設把 `None` 轉成 `Counting()`,`test_f4_end_to_end.py` 也一路都給查詢函式)。真正守著這行的是 `tests/analyzer/test_flow.py::test_advancing_a_handed_off_task_calls_no_collaborator`(S316,程式裡的註解也承認「已由 test_flow 既有那支守」)——但這支測試**沒有綁進**這條 KEY 合約的 `[test:]` 清單。我在臨時副本把這行守衛拿掉,7 支綁定測試全數維持綠燈,證實了這個缺口。

**「整條接續鏈最多 3 代」**
守護測試:`test_the_follow_up_chain_stops_at_the_generation_limit`,對照 `src/rtb/analyzer/task_store.py` 的 `MAX_GENERATION = 3` 與 `if generation > MAX_GENERATION:`。斷言 `len(chain) == 3`(不是 `<=`),邊界卡得很緊。
判斷:真守。

**「不會覆蓋較新的值」**
主要靠 `test_f4_a_stale_proposal_is_replanned_from_the_current_state` 的最終斷言:`STALE_BUDGET not in [...寫入清單...]`、DSP 最終 `(budget, version) == (FRESH_BUDGET, 3)`。
判斷:真守,而且是最直接、最強的一支。

**旁註**:`test_after_the_retention_window_a_missing_write_is_replanned` 也綁在這條 KEY 上,但它觸發的是 `ReplanReason.AFTER_RETENTION`(收件表已清掉、DSP 用存下的鍵查不到寫入),不是「另一方先改」的版本衝突(`ReplanReason.VERSION_CHANGED`)。它驗的是「接續任務建立/代數計算」這段共用機制,不是 F4 描述的那個觸發情境本身;算是可以接受的旁證,不算誤導,但不是這句話字面在講的東西。

## 改壞實驗

在 `/tmp/rtb-audit-copy`(rsync 排除 `.git`/`.venv`,PYTHONPATH=src,用 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest`)先跑基準:`tests/executor/test_execution.py`、`tests/executor/test_version_conflict.py`、`tests/analyzer/test_replan.py`、`tests/analyzer/test_f4_end_to_end.py` 共 121 支全綠。

1. **改壞 A(作者沒想到)**:`src/rtb/executor/execution.py` 第 362 行,把
   `signed = precheck(proposal, view) or self._sign(proposal)`
   改成
   `signed = self._sign(proposal) or precheck(proposal, view)`
   (讓簽發先跑,簽發成功時 precheck 完全不會被呼叫,等於繞過「執行前檢查」這一層)。
   跑同 4 個檔:**4 支翻紅** —— `test_each_failed_precheck_blocks_the_proposal_without_a_write[campaign_not_found/campaign_not_active/version_changed]`、`test_f4_a_stale_proposal_is_replanned_from_the_current_state[precheck]`。證實兩層擋法裡「執行前檢查」這一層真的被測試守著;順帶也對照出即使這層被繞過,最終寫入內容仍由 DSP 版本檢查兜底,符合 PITFALL 段落自己講的「兩層防線」設計。

2. **改壞 B(作者沒想到,對應上面找到的缺口)**:`src/rtb/analyzer/flow.py` 第 179 行,把
   `if row.state is TaskState.HANDED_OFF and operation_lookup is None: return row.state`
   改成
   `if False: return row.state`
   (拿掉「沒給操作查詢就原地不動」的守衛)。
   跑同 4 個檔:**121 支全綠,沒有一支翻紅**。另外跑 `tests/analyzer/test_flow.py`(不在綁定清單裡):**2 支翻紅**(`test_advancing_a_handed_off_task_calls_no_collaborator`、`test_resuming_after_a_crash_after_commit_never_repeats_or_diverges_from_the_committed_step`)。證實這個行為確實被測試守著,但守護測試沒有綁進這條 KEY 合約。

兩次實驗後都刪掉了 `/tmp/rtb-audit-copy`,專案原始目錄未被觸碰。

## 不同意要補什麼

把 S316 的守護測試(`tests/analyzer/test_flow.py::test_advancing_a_handed_off_task_calls_no_collaborator`,或另寫一支 F4 情境專用、明確斷言「`operation_lookup=None` 時已交給執行的任務不重新規劃」的測試)加進這條 KEY 行的 `[test:]` 清單。這樣句子裡「(有給操作查詢時)」這個限定詞才有一支綁定測試,在它被改壞時會直接跟著這條合約一起翻紅,而不必仰賴清單外、沒人特別去看的另一支測試。這是可以照現有前例(F1 合約把獨立守護的兩支對帳單元測試加進清單)直接複製做法的小修補,補上就同意。
