severity: minor

第 2 輪修正(拿掉 `connection()`、停下紀錄走收件口模組的 `stop_count`/`stops`、嘗試紀錄走 `counted_first_rows_started`/`counted_first_rows`、稽核明細「目前佔額度的」帶身分與舊列標記)都已到位,且沒有破壞既有測試攔截點,細節見下方確認段落。只有一條新增的簽名不一致,列為 F1。

## F1 `observability.py` 四支查詢的收件表參數不一致
severity: minor
blocking: 否 — 目前唯一呼叫端(`tests/executor/test_observability.py` 的 `read()`)已經特判過,production 端還沒有呼叫者(筆記寫明 Phase 9 才接輪詢),不影響現有行為或合約,是簽名風格問題不是錯的行為。

引句:「def utilization(tx: ExecutorTransaction, tenant: str, limit: int, now: datetime) -> Utilization:」

`aggregate_stop_count`、`table_full_deferral_count`、`aggregate_audit` 都是 `(store: InboxStore, tx: ExecutorTransaction, ...)` 開頭(收件表在前、交易在後),唯獨 `utilization` 沒有 `store` 參數、直接從 `tx` 開始(因為它只查嘗試紀錄模組,用不到收件表)。這在函式內部是對的(用不到就不要收),但對外是模組四支查詢裡唯一一支參數形狀不同的:`tests/executor/test_observability.py` 的 `read()` helper 因此要用 `if query in (observability.aggregate_stop_count, observability.table_full_deferral_count, observability.aggregate_audit): return query(store, tx, *args, **kwargs)` / `else: return query(tx, *args, **kwargs)` 這種特判(見該檔 57–63 行)才能統一呼叫四支查詢。以後 Phase 9 若要寫一支通用的輪詢/告警呼叫端,對這四支查詢一視同仁地傳 `(store, tx, ...)`,會在 `utilization` 上得到 `TypeError`(多傳了一個位置參數);目前沒有這種呼叫端,所以不擋,只留意將來要嘛在 `utilization` 也收一個沒用到的 `store` 佔位、要嘛在呼叫端保留特判。`docs/rtb-production-agent-demo-knowledge/Systems/可觀測查詢.md` 目前沒有記這條不一致。

## 其他確認過沒問題的地方

- `attempt_store.py` 的 `ExecutorTransaction` 已經拿掉「交出連線」的 `connection()`(全檔搜尋 `def connection` 與 `.connection(` 都是 0 筆),`_conn(tx)` 仍是模組內部私有函式,唯讀查詢（`aggregate_holdings`、`counted_first_rows_started`、`counted_first_rows`）都收 `tx: ExecutorTransaction` 走 `_conn(tx)`,沒有另開連線或另開交易的路徑。
- `aggregate_used(tx, tenant, now)` 現在是薄封裝,內部呼叫 `aggregate_holdings(tx, tenant, now)` 再加總(`attempt_store.py:374-377`);`begin()` 裡呼叫的也是模組全域名稱 `aggregate_used(...)`(377 行呼叫點在同一模組內)。`tests/executor/test_aggregate_limit.py` 的 `monkeypatch.setattr(attempt_store, "aggregate_used", slow_used)`(215 行)與 `monkeypatch.setattr(attempt_store, "_counted", ...)`(502 行)攔的都是模組全域名稱,`begin()`/`aggregate_holdings()` 內部呼叫時都是執行期查模組全域,不是綁死的函式參照,所以兩處攔截仍然生效——實測 `pytest tests/executor/test_aggregate_limit.py tests/executor/test_observability.py` 50 個測試全綠(含 `test_other_tenants_rows_are_filtered_in_the_database`、`test_the_aggregate_sum_stays_linear_in_the_window`〔`agg_time`/`pick_time` 那支〕)。
- `InboxStore.stop_count(self, tx, kind, *, tenant=None, campaign_id=None, since=None, until=None)` 與 `InboxStore.stops(self, tx, kind, tenant, since, until)` 兩支方法的參數順序本身(收件表方法的 `tx` 都放第一個、`kind` 第二個)彼此一致;`tenant` 在 `stop_count` 是選填 keyword-only、在 `stops` 是必填位置參數,是刻意的(前者是可選篩選的計數,後者是規格要求「某租戶」的明細列表,見 `stops` docstring),呼叫端(`observability.py` 的 `aggregate_stop_count`/`table_full_deferral_count`/`aggregate_audit`)分別對應正確,沒有誤用。
- `stops()`/`stop_count()` 都先 `self._own(tx)` 核對交易是同一個 `InboxStore` 開的,`tests/executor/test_observability.py::test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox` 驗到位(對應 r1 折入的 arch-F1)。
- `write_stops` 的三個索引(`write_stops_by_tenant`、`write_stops_by_campaign`、`write_stops_by_time`)寫在 `inbox_store.SCHEMA` 裡,靠 `sqlitekit.connect()` 每次開庫都跑 `executescript(schema)`(`CREATE INDEX IF NOT EXISTS`),不靠 `_migrate_columns()` 判斷,舊庫拿掉索引後重開一樣補得回來(`test_an_old_database_gains_the_observability_indexes` 驗證);只有 `attempts_first_rows_by_tenant`(參照後補的 `tenant` 欄)才需要 `_migrate_columns()` 裡的 `tenant_index_missing()` 特判,補完欄位後才建,順序正確。
- `Holding`/`CountedFirstRow` 的欄位與底層 SQL 的 `_FIRST_ROW_COLUMNS`/查詢欄位順序逐一核對過,位置對得上(`_counted(rest, tenant)` 傳入的 4 元組跟 `_counted(owner, amount, action, snapshot)` 的拆法一致);`observability.Stopped` 的 9 個欄位跟 `InboxStore.stops()` 的 `SELECT key, task_id, revision, content_hash, campaign_id, amount, used, cap, at` 順序一致。
