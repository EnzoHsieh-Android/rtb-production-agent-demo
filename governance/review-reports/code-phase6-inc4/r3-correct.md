severity: clean

第 2 輪修正(r3-delta.patch,對應 commit b0154be)在正確性與回歸這個鏡頭下沒有發現會擋的問題,已在臨時副本(`/tmp/rtb3b-copy`,複製自 `/Users/enzo/rtb-3b`)實跑驗證。

## 檢查逐項

- **`aggregate_holdings` 改一次查詢帶出身分後,已用額度的結果是否還對**:`aggregate_used` 現在是
  `sum(holding.amount for holding in aggregate_holdings(tx, tenant, now))`(`src/rtb/executor/attempt_store.py:371-374`),`aggregate_holdings` 內部仍是同一條 `_counted` 逐列計入規則(`_counted_rows` → `_counted(rest, tenant)`),欄位順序 `_FIRST_ROW_FIELDS = (key, task_id, revision, campaign_id, written_at, tenant, reserved_amount, action, proposal_json)` 與 `_counted_rows` 解包 `key, task_id, revision, campaign_id, started_at, *rest` 對得上(`rest` = tenant, reserved_amount, action, proposal_json,跟 `_counted` 期待的 `owner, amount, action, snapshot` 一致)。值本身沒變,只是少了一次回查。

- **開始一筆的額度判斷**:`begin()`(`attempt_store.py:344`)與 `execution.py:288-291,448` 都還是呼叫 `attempt_store.aggregate_used`,簽名、語意未變;既有並行競態測試 `test_aggregate_limit.py::_race_two_workers`(monkeypatch `attempt_store.aggregate_used`)與效能測試(`monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", ...)` 那支、量測 `agg_time <= 2*pick_time+0.005` 那支)都还是直接打補丁在 `aggregate_used` 這個入口,不受這次重構影響。

- **既有攔截測試是否照舊**:`test_other_tenants_rows_are_filtered_in_the_database` monkeypatch `attempt_store._counted(row, tenant)`,呼叫方式(位置參數)沒變,租戶過濾仍在 SQL(`WHERE ... AND (f.tenant = ? OR f.tenant IS NULL)`)完成,`seen == []` 的斷言不受影響。全域搜尋確認沒有測試或程式碼還引用已刪除的 `Holding` dataclass 或 `counted_first_rows(tx, tenant, keys)`。

- **兩段查詢(已驗證窗口、未結案)同一把鍵會不會出現兩次**:`VERIFIED` 在 `TERMINAL_STATES = {VERIFIED, FAILED}`(`src/rtb/domain/attempt.py:70`),且 `VERIFIED` 的允許轉移是空集合(`attempt.py:59`),所以一把鍵一旦出現在「已驗證窗口」查詢(`v.state = VERIFIED`),必定已經寫入一列終點列,會被「未結案」查詢的 `NOT EXISTS (... t.state IN (TERMINAL_LIST))` 排除,兩段結果不會對同一把鍵重複計入。這段邏輯這一輪沒有改動,只是欄位從 4 欄擴成 9 欄,結構仍相同。

- **時區檢查是否在所有停下紀錄查詢路徑生效**:`inbox_store.stop_count`/`stops`、模組級 `stop_count_query` 全部改用 `attempt_store.iso`(帶 `_require_aware` 檢查),`aggregate_stop_count`/`table_full_deferral_count`/`aggregate_audit` 都經由這幾個入口讀停下紀錄,新增測試 `test_stop_queries_reject_times_without_a_timezone` 同時涵蓋 `aggregate_stop_count` 與 `aggregate_audit` 兩個對外入口,都能捕到沒帶時區丟 `ValueError`。`inbox_store.py` 內既有的私有 `_iso`(寫入路徑用,如 `record_stop`)本身不驗時區,但這次的鏡頭是「停下紀錄查詢路徑」,寫入路徑不在範圍內,而且沒有查詢路徑繞過 `attempt_store.iso`——確認過 `stops()` 的 `since`/`until` 是必填參數,無法跳過檢查。

## 實跑結果

在 `/tmp/rtb3b-copy`(唯讀複製,未動原 repo)用 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider`:

- `tests/executor/test_observability.py tests/executor/test_aggregate_limit.py`:52 passed
- `tests/executor` 全部:390 passed

沒有發現需要開 `## F` 的問題。
