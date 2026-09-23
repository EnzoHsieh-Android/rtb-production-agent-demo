severity: clean

第 2 輪三處第 1 輪修正在 `tests/executor/test_observability.py` 裡都補了對應斷言,實際在臨時副本(`/tmp/rtb3b-mut`,非唯讀 repo 本體)把實作改壞後跑 `pytest tests/executor/test_observability.py -p no:cacheprovider -q`,三處都真的翻紅,沒有假綠;也沒發現新出現的同源互比。

## 已驗證的殺傷力(非發現,列供對照)

- 交易歸屬測試 `test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox`:拿掉 `src/rtb/executor/inbox_store.py` 的 `InboxStore.stop_count`/`stops` 裡的 `self._own(tx)` 呼叫,`test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox` 從 `DID NOT RAISE NotInTransaction` 翻紅,其餘 19 條不受影響——隔離度好。
- holding 身分與舊列標記斷言:把 `observability.aggregate_audit` 的 `holding` 改回只回 `(key, amount, legacy)` 三元組(拿掉身分),`test_the_aggregate_audit_lists_what_passed_and_what_was_stopped`、`test_the_audit_reconciles_with_used_across_every_kind_of_row`、`test_f7_is_explainable_with_the_observability_queries` 三支都以 `AttributeError` 翻紅。另外只把 `_entries` 裡傳入的 `row.legacy` 改成寫死 `False`(身分維持正確),`test_the_audit_reconciles_with_used_across_every_kind_of_row` 的 `{e.task_id: e.legacy for e in audit.holding} == {...}` 單獨接住這個問題、其餘測試仍綠——確認這行斷言是專門測 legacy 標記,不是靠別的斷言順帶擋住。
- 改走擁有模組後的查詢計畫測試 `test_observability_queries_use_their_indexes`:
  - 把 `attempt_store.first_rows_started_query` 改回單一條 `tenant = ? OR tenant IS NULL` 寫法(不再拆兩段 UNION),三個參數化案例全部因 `plan.count("attempts_first_rows_by_tenant") == 2` 翻紅(`0 == 2`)。
  - 把 `InboxStore._migrate_columns` 拿掉建 `TENANT_INDEX` 那行,三個索引測試 (`test_observability_queries_use_their_indexes` 三案例、兩個 `test_an_old_attempts_table_gains_the_tenant_index_after_the_column` 案例、`test_an_old_database_gains_the_observability_indexes`) 一共 6 條翻紅,`_indexes(...) >= OBSERVABILITY_INDEXES` 準確抓到缺的索引。

## 其他觀察

- `read()` 測試輔助函式按函式身分分派到 `observability.aggregate_stop_count`/`table_full_deferral_count`/`aggregate_audit`(帶 `store`)或直接呼叫(不帶 `store`),對照第 1 輪 `r1-intake.md` 的 arch-F1 折案與 r2-delta.patch,呼叫端簽章與正式程式碼一致,不是測試端自造捷徑。
- `test_utilization_matches_the_reservation_ledger` 已依 `r1-intake.md` 的 tests-F1 拿掉「`got.used == read(store, attempt_store.aggregate_used, TENANT, NOW)`」那行同源互比,現在只跟字面數字比,沒有殘留同源互比。
- `test_f7_is_explainable_with_the_observability_queries` 裡 `started` 集合是另外一條原生 SQL(`SELECT key FROM attempts WHERE seq = 1`)查出來的,跟 `audit.passed`/`observability.aggregate_stop_count` 不同源,可以互相校驗,沒發現新的同源比對。
- `_legacy_database` 造舊庫的手法(先拿掉索引再拿掉欄位)符合程式裡 `_migrate_columns` 的補欄位順序假設(先補欄位、再建參照它的索引),沒有測試造出程式不可能出現的中間狀態。
