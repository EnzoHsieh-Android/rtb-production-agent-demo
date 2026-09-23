severity: clean

# 對答案審查報告:RTB Phase5 執行端(S300 / S310 / S309 / S315 執行側)

白話總結:這批 diff 就像「一份提案被擋下時,把擋下的真正原因(版本被別人改過,還是單純先前判過失敗)老實寫清楚,並且讓重送的人查得到這個原因」。逐條對照收斂 spec 之後,涵蓋範圍內的每一條(S300、S310、S309、S315 執行側、三處版本衝突回報、收件口回報擋下原因收件口那半、衝突可查執行端查詢、被取代既有合約第一條、block_code 介面約定)都對得上,沒有縮水,也沒有超出範圍亂加東西。

## 已實作

- **〈執行端:DSP 端的版本衝突也回報成「版本已變」〉三處共用函式**:裁定 已實作。共用函式 `block_code_for_failure` 定義在 `src/rtb/executor/inbox_store.py`(patch 行 167-172),命中版本衝突回 `VERSION_CHANGED`、其他回 `OPERATION_PREVIOUSLY_FAILED`,三處呼叫點都改用它:確認終點(`src/rtb/executor/execution.py` patch 行 91 `done = self.store.ack_blocked(tx, receipt, now, block_code_for_failure(row.code))`)、開始一筆撞到既有失敗鍵(同檔 patch 行 75 `code = block_code_for_failure(begun.row.code)`)、收件口取件撞到既有失敗鍵(`src/rtb/executor/inbox_store.py` patch 行 271、278,把 `existing.code` 一併傳進 `_settle_existing`)。
  引句:「三處共用一個小函式「失敗嘗試對應的擋下原因」,不各寫一份。」

- **[S310]**:裁定 已實作。新測試 `test_a_dsp_version_conflict_is_acknowledged_as_version_changed`(`tests/executor/test_version_conflict.py` patch 行 402-420)逐一覆蓋三處(終點確認、開始撞既有失敗鍵、取件撞既有失敗鍵),另有 `test_other_failures_are_still_acknowledged_as_previously_failed`(patch 行 423-435)驗證非版本衝突的失敗仍寫 `operation_previously_failed`。

- **被取代的既有合約第一條**:裁定 已實作。既有斷言 `test_a_failed_attempt_blocks_the_new_delivery`(`tests/executor/test_queue.py` patch 行 330-337)由 `operation_previously_failed` 改驗 `version_changed`,並附註記 S310。逐支核對其餘既有斷言「operation_previously_failed」的測試(不在這份 diff、但在目前 repo 中查證):`tests/executor/test_crash_recovery.py:296`(觸發原因是作廢後的 `not_happened`,非版本衝突)、`tests/executor/test_reconcile.py:286`(同為 `not_happened`)、`tests/executor/test_queue.py:513`(人工手動 resolve,非 DSP 版本衝突)、`tests/executor/test_execution.py:513`(觸發原因是 403 `capability_scope_mismatch`,非版本衝突)——這四支都不是由版本衝突觸發,維持原斷言,符合「由版本衝突觸發的改驗新代碼,其他失敗維持」。
  引句:「由版本衝突觸發的改驗新代碼,其他失敗維持。」

- **〈收件口回報擋下原因〉(收件口那半)**:裁定 已實作。`Accepted` 資料類別新增 `block_code: str | None = None`(`src/rtb/executor/inbox_store.py` patch 行 196);`_accept_in_transaction` 對已存在提案的查詢改成只在 `disposition = 'blocked'` 時才帶出 `block_code`(patch 行 218-230),其餘處置為 None;`inbox_server.py` 的成功回應本文加一鍵 `"block_code": result.block_code`,其餘既有鍵不動(patch 行 111-118)。
  引句:「收件口對重送的回應本文多帶「擋下原因」(只有處置是已擋下時才有值)。」

- **[S300]**:裁定 已實作。新測試 `test_a_resend_reports_why_the_proposal_was_blocked`(`tests/executor/test_version_conflict.py` patch 行 445-467)驗證:首次收下 `block_code` 為 None、擋下後重送回 `state == "blocked"` 且 `block_code == "version_changed"`,並用 `assert set(answer) == {..., "block_code"}` 鎖住既有鍵不動。

- **主線約定:`block_code` 介面**:裁定 已實作。鍵一律存在(`_accepted_body` 直接寫入 `result.block_code`,不做條件式省略),只有處置為已擋下時有值,其他一律 `null`;其餘既有鍵(status/task_id/revision/state/content_hash/replayed)原樣保留,見上引 `inbox_server.py` patch 行 118 與測試 patch 行 452、464-467。

- **[S309]**:裁定 已實作。新測試 `test_two_concurrent_writers_reproduce_a_version_conflict`(`tests/executor/test_version_conflict.py` patch 行 519-544)起真的 DSP,兩個寫入者用 barrier 會合後同時寫,驗證只有一方 `verified`、另一方 `("failed", "version_conflict")` 且收件口擋下原因為 `("blocked", "version_changed")`,DSP 最終只留一筆 `update_budget`、版本前進到 2。
  引句:「另一個的嘗試記版本衝突、收件口擋下原因記版本已變。」

- **〈衝突可查〉(只看執行端查詢)**:裁定 已實作。`attempt_store.version_conflict_count`(`src/rtb/executor/attempt_store.py` patch 行 16-24)從嘗試紀錄依 `code = VERSION_CONFLICT` 計數、可選依 `campaign_id` 篩。

- **[S315]執行側**:裁定 已實作。新測試 `test_conflict_counts_are_queryable`(`tests/executor/test_version_conflict.py` patch 行 548-562)驗證全域計數、依廣告篩、以及「別種失敗不算」。
  引句:「執行端的查詢應回報正確的 DSP 版本衝突次數,兩者都可依廣告篩。」

## 縮水

(無)

## 多做

(無)

## 未實作

(無)

縮水+未實作共 0 條

⚠ 交編排者:「被取代的既有合約第一條」裡「逐支核對其餘既有斷言」這件事,diff 本身只改了一處既有測試,其餘四處未受影響的斷言不在 diff 範圍內,我是另外對目前 repo 現況做 grep 查證(非這份 diff 的一部分)才確認它們的失敗原因不是版本衝突;若 repo 現況與這份 diff 基準點不同步,這個交叉核對可能失真,建議再核一次 diff 的 base commit 是否等於目前 repo HEAD。
