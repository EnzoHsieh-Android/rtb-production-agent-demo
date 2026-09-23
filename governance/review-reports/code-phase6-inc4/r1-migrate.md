severity: clean

未在「開庫補欄位、索引與舊資料」這個鏡頭下找到 major/minor 發現。以下是實測方法與結果。

## 實測方法

在臨時目錄用真實舊版程式碼(不是手刻假 schema)建出舊資料庫,再用現在 HEAD(8de1054,已包含這份 r1-snapshot.patch)的程式碼開啟,檢查欄位、索引、交易次數與多工作者同時開庫。

- 用 `git worktree` 把 `/Users/enzo/rtb-3b` 複本(`rtb-3b-copy`,唯讀 repo 的複本,不影響原 repo)簽出兩個舊版本各自建庫:
  - `e33b3c0`(Phase 6 之前,代表 Phase 3/4/5 時代):`attempts` 有 `written_version`/`capability_expires_at`,但沒有 `tenant`/`reserved_amount`;沒有 `write_stops` 表(該功能還沒出現)。
  - `ba87e6a`(增量 1 收尾):`attempts` 已有 `tenant`/`reserved_amount`,`write_stops` 表已存在但沒有任何索引,也沒有 `attempts_first_rows_by_tenant`。
- 用現在 HEAD 的 `InboxStore` 開這兩顆舊庫,以及一顆全新庫。

## 結果

1. **舊資料庫開啟不失敗**:Phase 3/4/5 時代庫與增量 1 時代庫都能正常開啟,`_migrate_columns` 依序補上缺的欄位(`tenant`、`reserved_amount`)、`attempt_store.TENANT_INDEX`(`attempts_first_rows_by_tenant`)與 `write_stops` 的三個新索引(`write_stops_by_tenant`、`write_stops_by_campaign`、`write_stops_by_time`),`PRAGMA table_info` 與 `sqlite_master` 核對過,兩顆庫收斂到同一份 schema。全新庫直接建表時就帶齊三個 `write_stops` 索引(在 SCHEMA 常數裡),`tenant`/`reserved_amount`/`TENANT_INDEX` 因為不在 SCHEMA 裡(注解說明是刻意的,參照後補欄位)、仍走一次 `_migrate_columns`,結果也補齊。

2. **索引都補上,且補齊條件正確涵蓋「欄位已齊只缺索引」的舊庫**:`_migrate_columns` 的守門條件 `not self._missing_columns() and not self._proposals_outdated() and not attempt_store.tenant_index_missing(self._conn)` 三個子句都查了才放行略過,增量 1 那種「欄位已經齊全、只缺這個索引」的舊庫能被第三個子句單獨攔下來補索引,不會因為前兩個子句為 False 就漏補。

3. **每次開庫不會被不必要地拉進補欄位交易**:對已經補齊(全新庫、Phase 3/4/5 庫、增量 1 庫,三種各自補完一次之後)的資料庫,監測 `immediate_transaction` 呼叫次數,重新 `InboxStore(db)` 再 `close()`,三種情況都是 0 次進入交易(見下方指令與輸出)。

4. **多工作者同時開庫的競態**:鎖外的判斷式(`_missing_columns()` / `_proposals_outdated()` / `tenant_index_missing()`)只決定要不要進交易,拿到鎖之後 `for table, ddl in self._missing_columns()`(重新查)、`self._conn.execute(attempt_store.TENANT_INDEX)`(`CREATE INDEX IF NOT EXISTS`,天生冪等)、`if self._proposals_outdated():`(重新查)三處都在鎖內重新判斷,符合 docstring 講的「拿到鎖後再查一次」。用同一顆舊庫跑了：
   - 8 個 Python 執行緒同時 `InboxStore(db)`:全部成功,無 `DatabaseBusy`、無重複欄位錯誤。
   - **10 個獨立行程**(`multiprocessing.Process`,spawn,各自開新連線,busy_timeout 10 秒)各跑 3 輪,對 Phase 3/4/5 庫與增量 1 庫都測過:30 個行程全部回報 `ok`,schema 最終收斂正確、沒有 `duplicate column name` 或其他錯誤。這排除了「鎖外判斷通過的多個工作者,拿到鎖後沒再查就直接 ALTER」會造成的重複欄位錯誤。

## 對既有測試改動的確認

`tests/executor/test_aggregate_limit.py::test_old_attempts_count_against_every_tenant_until_they_leave_the_window` 模擬 Phase 6 之前資料庫時,新增了先 `DROP INDEX IF EXISTS attempts_first_rows_by_tenant` 再 `ALTER TABLE attempts DROP COLUMN tenant`——這是必要的,SQLite 不准先砍掉還被索引參照的欄位;引句:「Phase 6 之前的資料庫沒有按租戶的索引(增量 4 加的),先拿掉才拿得掉它參照的租戶欄」，跟實際行為一致。

執行 `tests/executor/test_aggregate_limit.py`、`test_queue.py`、`test_observability.py`、`test_attempt_store.py`、`test_multi_worker.py` 共 157 個測試全部通過。

## 附:關鍵指令與輸出

```
# 舊庫 -> 現在 HEAD 開啟後的 schema(Phase 3/4/5 時代與增量 1 時代都補齊三個 write_stops 索引 + attempts_first_rows_by_tenant)
sqlite3 phase345.db "SELECT name FROM sqlite_master WHERE type IN ('table','index') ORDER BY name;"
-> attempts, attempts_first_rows, attempts_first_rows_by_tenant, attempts_one_terminal_per_key,
   attempts_terminal_rows, attempts_verified_by_time, inbox_events, proposals, ...,
   write_stops, write_stops_by_campaign, write_stops_by_tenant, write_stops_by_time

# 二次開庫不進交易(monkeypatch 計數 immediate_transaction 呼叫次數)
brandnew second open, migrate transaction entered: 0 times
phase345 second open, migrate transaction entered: 0 times
inc1 second open, migrate transaction entered: 0 times

# 10 個獨立行程同時對同一顆未補齊的舊庫開庫,各跑 3 輪
=== multiprocess race iter 1 ===  ['ok']*10
=== multiprocess race iter 2 ===  ['ok']*10
=== multiprocess race iter 3 ===  ['ok']*10
(phase345 與 inc1 兩種舊庫各測過)
```

## 沒有查到脈絡衝突

沒有動用到「筆記說一套、程式碼是另一套」的情況;這次結論全部由實測(舊版程式碼建庫 + 現版程式碼開庫)與現有測試得出,不依賴知識圖譜的 `RULE:`/`FACT:` 內容。派工單裡「效能上『還沒結案那段用不上索引』是增量 1 既有成本,這次不動,不要報」——這次審查範圍內沒有新增或改到那段查詢,沒有東西要報。
