severity: clean

查了什麼:對照 `r1-sec.md`、`r2-sec.md` 兩份前兩輪資安席報告,逐項核對防線是否還在最後一版(`r3-snapshot.patch`,已對照 repo 現況 `src/rtb/executor/attempt_store.py`、`execution.py`、`inbox_store.py`、`capability_signer.py`、`inbox_server.py` 確認 patch 與現況一致):

1. **r1 發現「總曝險擋下的停下紀錄可被同一交易回滾吞掉」**:確認這條在 r1 收貨紀錄(`r1-intake.md` 的 sec-1 列)被裁定為「觀察 HIT、判準另想」——收據失效代表提案已被接手,回滾不重複記才是正確行為,不是漏洞;作者補了 `test_a_lost_receipt_during_an_aggregate_block_writes_no_stop` 釘住「丟 `LeaseLost`、不留紀錄」這個行為,變異(改成照記)修後翻紅。這支測試在 r3 快照裡原樣還在,`execution.py:532-538` 的 `if not self.store.ack_blocked(...): raise LeaseLost(...) from full` 寫法也沒有再變,實測 `PYTHONPATH=src ./.venv/bin/python -m pytest tests/executor/test_aggregate_limit.py -q` 30 個測試全綠,防線仍在。

2. **r2 發現「表滿路徑在排他寫入鎖裡對全系統做無租戶過濾的掃描」**:`r3-delta.patch` 把 `aggregate_used()` 的兩段 SQL 都加上 `AND (f.tenant = ? OR f.tenant IS NULL)`,在資料庫層就把「這個租戶的列」與「沒有租戶的舊列」留下、把別的租戶濾掉,不再把全系統的列撈進 Python。逐條核對兩段查詢(已驗證窗口查詢、沒有終點查詢)的參數順序與佔位符對得上、`AND`/`OR` 用括號明確分組,沒有漏掉任一分支(`src/rtb/executor/attempt_store.py:355-376`)。用既有測試組直接驗證「不漏算」:
   - `test_a_budget_increase_reserves_its_amount_with_the_first_attempt`、`test_an_unresolved_reservation_counts_no_matter_how_old`、`test_a_verified_reservation_leaves_the_window_24_hours_after_verification` 等確認**這個租戶自己的列**(已驗證窗口內、沒有終點)都還算得到。
   - `test_old_attempts_count_against_every_tenant_until_they_leave_the_window`(用真的拿掉 `tenant`/`reserved_amount` 欄位再補回的舊格式資料庫模擬)確認**沒有租戶的舊列**對任意租戶("a"、"b")都仍然全額算入,不會因為加了 SQL 過濾而漏算。
   - `test_another_tenants_reservation_is_not_counted`、`test_other_tenants_rows_are_filtered_in_the_database`(1000 列別的租戶、其中一半沒有終點)確認別的租戶的列被過濾掉,而且靠斷言 `_counted` 從未被呼叫(`seen == []`)證明過濾發生在 SQL 層而不是事後在 Python 篩。
   - `test_the_aggregate_query_is_no_slower_than_picking_and_never_overflows`(30 萬列規模)同時驗證正確性(`total == (3000 + 20) * 7`)與 r2 指出的鎖內耗時問題已解除(`agg_time <= 2 * pick_time + 0.005`)。
   全部實測執行皆綠(`30 passed in 6.82s`)。

   另外確認 `tenant` 字串的來源:`CapabilitySigner._tenant_of()` 只從管理員設定檔用 `campaign_id` 查表得到,`is_id()` 正則驗證過,不是攻擊者可控輸入;`Reservation.tenant`(簽發時取得)與 `aggregate_used()` 查詢用的 `tenant` 是同一次呼叫鏈裡的同一個字串,沒有大小寫或重新讀設定檔造成不一致的空間;每把鍵的 `tenant` 只在 `begin()` 的 INSERT 寫一次、後續 `_append`/`transition` 不會碰,不會中途變成別的租戶。

沒有找到會漏算導致超額,或讓前兩輪防線失效的洞。
