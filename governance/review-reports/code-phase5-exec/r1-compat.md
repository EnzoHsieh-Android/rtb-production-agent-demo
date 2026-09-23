severity: clean

## 已看:①〈被取代的既有合約〉——既有斷言 operation_previously_failed / OPERATION_PREVIOUSLY_FAILED 的測試逐支核對

`grep -rn "operation_previously_failed\|OPERATION_PREVIOUSLY_FAILED" tests/ src/` 找到以下斷言點,逐支核對觸發它們失敗的 OutcomeCode 是不是版本衝突:

- `tests/executor/test_queue.py:432`(`test_a_failed_attempt_blocks_the_new_delivery`):由 `WriteAnswer(409, "version_conflict")` 觸發 → 版本衝突,diff 已改成斷言 `version_changed`。**已改**,正確。
- `tests/executor/test_crash_recovery.py:296`(`test_crash_before_the_dsp_call_then_a_failed_recheck_voids_and_blocks`):失敗路徑走 `_void_then_fail`,寫入的 code 來自 `react_void` 的 `VoidReaction`(`NOT_HAPPENED` / `CAPABILITY_REJECTED` / `LOCAL_REQUEST_ERROR`),VOID_TABLE 裡沒有任何一條會產生 `VERSION_CONFLICT`。**維持不變是對的**。
- `tests/executor/test_reconcile.py:286`(`test_a_voided_operation_blocks_the_same_key_from_a_new_revision`):失敗前置是 `states(h, prop)[-1] == ("failed", "not_happened")`,即 `NOT_HAPPENED`。**維持不變是對的**。
- `tests/executor/test_execution.py:158/165/172/513`:全部經 `_previously_failed` 輔助函式(`tests/executor/test_execution.py:138-149`),用 `attempt_store.resolve(...)` 人工判失敗,寫入 `OutcomeCode.MANUAL_FAILURE`(見 `src/rtb/executor/attempt_store.py:431`),不是版本衝突。**維持不變是對的**。
- `tests/executor/test_queue.py:513`(`test_a_manual_resolution_is_acknowledged_by_the_next_reconciliation`):同樣經 `resolve` 產生 `MANUAL_FAILURE`。**維持不變是對的**。
- `tests/executor/test_inbox_disposition.py:224`(`test_block_reasons_are_a_closed_list`):只是列舉 `BlockCode` 全部成員做封閉集合檢查,不是任何一次失敗觸發的斷言,與此次改動無關。**不需要改**。

另外查了所有 `WriteAnswer(409, "version_conflict")` 的呼叫點(`test_execution.py:253`、`test_version_conflict.py`、`test_queue.py:423,745`、`test_execution_e2e.py:126`):`test_execution.py:253` 是既有的 code↔reaction 參數化表,本來就斷言 `C.VERSION_CONFLICT`(嘗試紀錄層級,不是 block_code),不受影響;`test_queue.py:745` 與 `test_execution_e2e.py:126` 都只斷言嘗試狀態/代碼,沒有斷言收件表 block_code,不在〈被取代的既有合約〉要求改的範圍內,也沒有漏改的必要。

結論:三處都改了、其他失敗都維持,沒有找到漏改或錯改的既有測試。

## 已看:② test_inbox_server.py 允許清單加 OutcomeCode 是否削弱「收件口不自己做欄位驗證」

`tests/executor/test_inbox_server.py:365-373` 的機制是:對 `inbox_server`、`inbox_store` 兩個模組做 AST 掃描,收集所有 `from rtb.domain.* import ...` 的名字,斷言這個集合是 `allowed` 的子集(封閉清單),用來擋「收件口自己重寫欄位驗證邏輯,不透過 `parse_proposal` 一份解析器」。

`OutcomeCode` 加進 `allowed` 只被 `inbox_store.py` 的 `block_code_for_failure()` 用來判斷「這筆嘗試的結果代碼是不是版本衝突」,決定擋下原因該寫 `version_changed` 還是 `operation_previously_failed`——這是讀已存在的嘗試紀錄的結果碼做路由,不是對外部送進來的提案欄位做結構/型別驗證(欄位驗證仍然全部經過 `parse_proposal`,`test_every_sample_the_domain_parser_rejects_is_also_rejected_by_the_inbox...` 這支測試前半段對 `REJECTED_OVERRIDES` 逐一送出、斷言全部經過同一個 `parse_proposal`,這段完全沒動)。清單仍是封閉的(還是要顯式列出成員才能通過),`inbox_server.py` 本身也沒有新增 `rtb.domain` import(diff 裡它只加了回應本文的 `block_code` 鍵,沒有新 import)。

結論:沒有削弱——欄位驗證仍然只經 `parse_proposal` 這一條路,`OutcomeCode` 是既有失敗結果的路由邏輯,不是驗證邏輯,且清單仍是封閉、顯式列舉。

## 已看:③ tests/executor/test_version_conflict.py 逐支拿掉對應機制實跑翻紅

在 `mktemp -d` 臨時目錄複製 `src/`、`tests/`(等同 HEAD,即 diff 已套用後的狀態),用 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider` 跑。基準:5 支全過。逐一改回舊代碼後重跑,全部翻紅:

1. `src/rtb/executor/execution.py:434`(`_ack_terminal` 終點確認)改回 `BlockCode.OPERATION_PREVIOUSLY_FAILED` → `test_a_dsp_version_conflict_is_acknowledged_as_version_changed`、`test_two_concurrent_writers_reproduce_a_version_conflict` 兩支翻紅(`'operation_previously_failed' != 'version_changed'`)。
2. `src/rtb/executor/execution.py:419`(開始一筆撞到既有失敗鍵)改回 `BlockCode.OPERATION_PREVIOUSLY_FAILED` → `test_a_dsp_version_conflict_is_acknowledged_as_version_changed` 翻紅(第二段斷言,`result.block_code` 不符)。
3. `src/rtb/executor/inbox_store.py:500`(`_settle_existing` 取件撞到既有失敗鍵)改回 `BlockCode.OPERATION_PREVIOUSLY_FAILED` → `test_a_dsp_version_conflict_is_acknowledged_as_version_changed` 及既有的 `tests/executor/test_queue.py::test_a_failed_attempt_blocks_the_new_delivery` 都翻紅。
4. `src/rtb/executor/inbox_server.py:136` 回應本文拿掉 `"block_code": result.block_code` → `test_a_resend_reports_why_the_proposal_was_blocked` 翻紅(`KeyError: 'block_code'`)。
5. `src/rtb/executor/attempt_store.py` 的 `version_conflict_count` 拿掉 `campaign_id` 篩選條件(改成永遠不帶 `AND campaign_id = ?`)→ `test_conflict_counts_are_queryable` 翻紅(`version_conflict_count(tx, "c1")` 回 3 而非預期 2)。

每次改完都用 `git checkout`/還原備份把臨時目錄復原到基準狀態再測下一個,不影響下一輪。五處機制拿掉都能讓對應測試從綠翻紅,新測試有殺傷力。

## 已看:④ 收件口回應多一個鍵,既有逐字比對回應本文的測試有沒有漏改

`grep -rn '"status": "accepted"' tests/` 只在 `tests/executor/test_inbox_server.py:32` 出現,且是 `data["status"] == "accepted" and data["state"] == "pending"` 這種單鍵比對,不是整包 dict/鍵集合比對。另外搜了 `== {`、`set(data)`、`set(answer)`、`set(body)` 等整包比對寫法(`test_inbox_server.py`、`test_queue.py`、`test_inbox_disposition.py`、`test_execution_e2e.py`、`test_crash_recovery.py`),命中的都是別的集合(拒收錯誤碼集合、行為計數、任務編號集合),不是接受回應本文。也查了 `inbox_store.Accepted(...)` 的建構呼叫,tests/ 裡沒有任何地方直接用位置參數建構它(只有 `src/rtb/executor/inbox_store.py` 內部兩處,diff 已同步補上第 6 個參數)。

結論:專案裡原本就沒有對「已收下」回應本文做逐字全 dict/鍵集合比對的既有測試,所以沒有漏改的既有測試;新加的 `set(answer) == {...}` 全鍵比對只存在於新測試 `test_a_resend_reports_why_the_proposal_was_blocked` 裡,本身就已經涵蓋新鍵。

⚠ 交編排者:test_execution_e2e.py:126(`test_f4_a_stale_proposal_never_overwrites_a_newer_value`)裡确实有一段用 `WriteAnswer` 觸發版本衝突並只斷言嘗試代碼 `("failed", "version_conflict")`,沒有斷言收件表 block_code——這不是漏改(該測試本來就不管收件表這一層),只是提醒編排者:如果之後想加「端到端也驗證擋下原因」的覆蓋率,這支測試是候選,但不屬於這次〈被取代的既有合約〉要求範圍,沒有把它算進 finding。

最高不落地判定:clean,blocking 0 條。
