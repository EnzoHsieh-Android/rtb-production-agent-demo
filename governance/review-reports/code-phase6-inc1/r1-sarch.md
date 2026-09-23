severity: major

### 1. 舊格式嘗試列快照毀損時,總曝險查詢丟出的 CorruptedAttemptRow 沒有比照既有慣例轉成 ExecutorHalted,會讓整條執行迴圈未受控當機

severity: major
blocking: 是 直接違反本專案既有的「例外與停機分類」慣例(讀不回來的歷史列一律轉成 ExecutorHalted、乾淨停機讓人看),實際會做出錯的行為——未受控的當機取代設計要求的「出事就停,讓人注意到」;而且一旦踩到,同一租戶之後每一筆新提案都會在同一處重複踩雷,等於讓那個租戶的執行迴圈進入當機迴圈,而不是一次乾淨停機等人處理壞列。
引句:「raise CorruptedAttemptRow("舊嘗試的提案快照讀不出新預算,無法保守計入額度") from exc」

說明:`_counted()`(`attempt_store.py` 新增)在讀到 Phase 6 之前的舊格式嘗試列(`tenant`/`reserved_amount` 都是空值)、而且該列 `action == "update_budget"` 時,會嘗試從 `proposal_json` 解出 `requested_change.new_budget`;解不出來就丟 `CorruptedAttemptRow`。這是這次改動第一次讓「開始一筆」的路徑去讀「別的鍵」的歷史快照——Phase 6 之前 `begin()` 從不做這件事。

這個例外從 `aggregate_used()` 經 `attempt_store.begin()`、`Executor._take()`、`Executor._process()`,一路到 `Executor.process_one()`。但 `process_one()` 只接 `LeaseLost`:
file:`src/rtb/executor/execution.py:364-367`
啟動程式主迴圈 `_loop()` 也只接 `InboxBusy` 與 `ExecutorHalted`:
file:`src/rtb/executor/runner.py:65-77`
整條路徑沒有任何一層把 `CorruptedAttemptRow` 轉成 `ExecutorHalted`,於是它會直接從 `_loop()` 往外炸,行程整個當掉——不會印「停機:...」訊息、不會用非零代碼(`EXIT_HALTED`)乾淨結束。

對照既有慣例:同一種例外在對帳路徑 `_reconcile()` 是明確接住並轉停機的:
file:`src/rtb/executor/execution.py:639-640`(`except attempt_store.CorruptedAttemptRow as exc: raise ExecutorHalted("unreadable_attempt") from exc`)
Phase 6 新增的「開始一筆時算總曝險」這條路徑沒有比照辦理,是這次改動新引入、之前不存在的一個當機面。

已實測驗證(複製整個 src/tests 到 `/private/tmp` 下跑,沒有動 repo):先在收件庫直接塞一列 Phase 6 之前格式、`proposal_json` 是壞掉 JSON 的 `update_budget` 未結案列,再用 `Harness`/`Executor` 對同一租戶的另一個廣告送一筆全新、格式正確的提案並呼叫 `process_one()`——結果 `CorruptedAttemptRow` 原樣從 `process_one()` 逃出,不是 `ExecutorHalted`(訊息:「舊嘗試的提案快照讀不出新預算,無法保守計入額度」)。

現有測試沒有蓋到這個組合:`tests/executor/test_attempt_store.py` 裡兩支 `CorruptedAttemptRow` 測試都只測 `attempt_store.snapshot()`(讀一把鍵自己的快照),沒有一支測「算總曝險時,同租戶另一把舊鍵的快照壞了」這條新路徑,也沒有一支測 `Executor.process_one()` 或啟動程式 `_loop()` 在這種情況下該有的收尾行為(印訊息、以 `EXIT_HALTED` 結束),屬於代碼審該補的假綠缺口。

---

另外驗證過、確認沒有問題、不列為發現的重點(供對照):並行合約 [S336] 那支測試雖然作者說做不出對應變異,但我把「在寫入鎖外讀額度」的真實壞版本(用一條不搶寫入鎖的唯讀連線先讀額度,再另開交易寫入)接到測試上跑了 6 次,全部穩定翻紅(`assert kinds == sorted([...])` 抓到兩筆都 `executed`),證明這支測試確實守得住設計最擔心的那個漏洞,不是假綠。
