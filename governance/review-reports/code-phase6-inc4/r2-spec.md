severity: clean

# 規格符合度審查(第 2 輪,code-phase6-inc4)

範圍:S360、S361、S363–S369、S380;計劃「增量 4 設計:可觀測」節的最小設計;稽核明細四樣輸出(stopped、passed、holding、utilization)欄位;三篇筆記(`Systems/可觀測查詢.md`、`Systems/提案收件口.md`、`Systems/外部寫入嘗試紀錄.md`)是否跟程式一致。材料為 `governance/review-reports/code-phase6-inc4/r2-snapshot.patch`(凍結)與 `r2-delta.patch`(本輪修正),對照 `/Users/enzo/rtb-3b` 當前工作樹(等於 r1+r2 已套用的狀態)。

## 第 1 輪修正逐條核對

r1-intake.md 列的五項修正(finder-F1/veto-F1、arch-F1、security-F1、tests-F1、tests-F2、spec-F1)在 r2-delta 與現狀都已落地:

- `Holding`→本輪的「通過的」「目前佔額度的」改成 `attempt_store.CountedFirstRow`(帶 `key, task_id, revision, campaign_id, started_at, amount, legacy`),`observability.Entry` 據此補上身分欄位(`src/rtb/executor/observability.py:50-62`)。`test_the_aggregate_audit_lists_what_passed_and_what_was_stopped` 與 `test_the_audit_reconciles_with_used_across_every_kind_of_row` 都斷言了 `task_id`/`revision`/`legacy`,不再只驗金額。
- `observability.py` 不再直接對 `write_stops`/`attempts` 下 SQL:停下紀錄改呼叫 `InboxStore.stop_count`/`stops`(`inbox_store.py:715-737`,內部先 `self._own(tx)` 核對交易是這個收件表開的),嘗試紀錄改呼叫 `attempt_store.counted_first_rows_started`/`counted_first_rows`(`attempt_store.py:438-457`)。`attempt_store.connection()`/`iso()` 兩個對外函式已刪除,全庫(含測試)沒有殘留呼叫。新增 `test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox` 驗證別的收件表開的交易讀不到,拿掉 `_own` 檢查會紅。
- 稽核明細範圍留白文件已補(security-F1 的處置):`observability.py:9-10` 與 `Systems/可觀測查詢.md` 都寫明「現在不設上限,Phase 9 一併決定」。
- `test_utilization_matches_the_reservation_ledger` 已拿掉「`used` 跟 `aggregate_used` 同源互比」那行斷言,只留對照具體數字的斷言(`tests/executor/test_observability.py`,對照 delta 中刪除的 `assert got.used == read(store, attempt_store.aggregate_used, TENANT, NOW)`)。
- spec-F1(筆記依賴方向寫錯)已修正:`Systems/可觀測查詢.md` 現在寫「這裡 → 收件口模組、嘗試紀錄模組」,跟程式的 import(`from rtb.executor.inbox_store import InboxStore, StopKind`)一致。

## 合約逐條核對(S360、S361、S363–S369、S380)

跑了現有測試全綠:

```
$ .venv/bin/python -m pytest -p no:cacheprovider tests/executor/test_observability.py tests/executor/test_aggregate_limit.py -q
50 passed in 7.49s
$ .venv/bin/python -m pytest -p no:cacheprovider tests/executor -q
388 passed in 43.81s
```

- S360/S361:`aggregate_stop_count`/`table_full_deferral_count` 透過 `InboxStore.stop_count` 依租戶/廣告/時間篩,語意不變(`observability.py:73-88`)。
- S363:`utilization` 仍呼叫 `attempt_store.aggregate_used`,門檻 0 回 `increases_allowed=False`(`_utilization`,`observability.py:95-96`)。
- S364:`test_observability_queries_write_nothing` 未動,查詢路徑全改唯讀方法,沒有新增寫入。
- S365:F7 端到端測試已改呼叫新簽名(`observability.aggregate_stop_count(h.store, tx, ...)`、`observability.aggregate_audit(h.store, tx, ...)`),斷言不變且綠燈。
- S366/S380:三個 `write_stops` 索引仍在 `SCHEMA`(建表語句內,`inbox_store.py:159-161`);`attempts_first_rows_by_tenant` 仍在補欄位流程裡「索引不存在才建」(`inbox_store.py:348-356`,條件含 `attempt_store.tenant_index_missing`)。
- S367/S368:「通過的」用 `counted_first_rows_started`(不看 24 小時窗口)、「目前佔額度的」用 `counted_first_rows(tx, tenant, {aggregate_holdings 的鍵})`——兩者共用 `_counted`,金額必然與 `aggregate_holdings`/`aggregate_used` 一致(兩邊查的是同一批第一列欄位:`tenant, reserved_amount, action, proposal_json`)。舊列標記、逐列計入規則、目前狀態(`attempt_store.latest`)都對應到 `Entry` 的欄位。
- S369:索引查詢計畫測試改呼叫 `inbox_store.stop_count_query`/`attempt_store.first_rows_started_query`(SQL 生成邏輯原封搬移,只是搬了家),斷言的索引名不變。

## 稽核明細四樣輸出欄位比對設計

設計要求(計劃「查詢五」節)與 `AggregateAudit` 的欄位逐一核對一致:

- 被停下的:冪等鍵、任務、修訂、內容雜湊、廣告、金額、當時已用、當時門檻、時間 → `Stopped` 九個欄位、順序對應 `InboxStore.stops` 的 SELECT 順序(`key, task_id, revision, content_hash, campaign_id, amount, used, cap, at`)。
- 通過的/目前佔額度的:冪等鍵、任務、修訂、廣告、計入金額、開始時間、目前狀態、目前是否計入、是不是舊列 → `Entry` 九個欄位齊全,`counted_now`/`legacy` 都有填值。
- 剩餘額度:`Utilization`(`used, limit, remaining, increases_allowed`)沿用查詢四的計算式。

## 筆記與程式比對

三篇筆記(`Systems/可觀測查詢.md`、`Systems/提案收件口.md`、`Systems/外部寫入嘗試紀錄.md`)的本輪新增/改動內容(函式名 `stop_count`/`stops`/`counted_first_rows_started`/`counted_first_rows`、依賴方向、不交出連線、索引清單、補欄位條件)逐一對照程式碼行,沒有找到跟程式對不上的描述。`ruff check` 對三支改動的原始碼檔案(`observability.py`、`attempt_store.py`、`inbox_store.py`)全過。

沒有發現需要回報的問題。
