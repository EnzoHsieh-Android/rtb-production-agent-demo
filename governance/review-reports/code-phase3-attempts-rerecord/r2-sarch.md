severity: minor

### 1. 拒收無時區時間的檢查在儲存層又重寫一份，沒有沿用領域層既有的共用檢查
severity: minor
blocking: 否 純粹是程式碼重複、邏輯與既有實作等價，不影響行為或跨模組相容性
引句:「if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None」

`src/rtb/executor/attempt_store.py` 新增的 `_check_now` 是這個專案第三份「拒收沒有時區的時間」判斷式：
- `src/rtb/domain/evidence.py:45-47` 已有 `_is_aware(value)`（`isinstance(value, datetime) and value.utcoffset() is not None`）
- `src/rtb/domain/proposal.py:114`（`_parse_time`）已有等價的 `parsed.utcoffset() is None` 檢查
- 這次凍結 patch 又在 `attempt_store.py` 私下寫了第三份等價邏輯，且放在儲存層而非領域層

對照本次修正另一處「序號檢查」的做法——同一支檔案同一批修改改用 `from rtb.domain._checks import is_plain_int`（`r2-snapshot.patch` 第 60 行）正確重用了 `src/rtb/domain/_checks.py` 的共用判斷——`_checks.py` 檔頭就明講「只放…到處要用的判斷,免得各模組改了一處漏另一處」，這正是為了避免現在這種情況。時區檢查沒有走同一條路，等於在同一份 patch 裡一半遵守、一半沒遵守專案自己定的「共用判斷只放一份」慣例。不是新的第二種架構（沒有行為衝突），但值得在下一輪把 `_check_now` 的邏輯收斂進 `_checks.py`（或直接 import `evidence._is_aware`），避免以後三份定義其中一份漏改。

---

其餘對照點都查過，沒有發現 major 等級的架構不一致：

- **ExecutorTransaction 交易物件**：對照 `src/rtb/sqlitekit.py` 的 `immediate_transaction`/`begin_immediate` 與 `src/rtb/analyzer/task_store.py` 的用法——底層開/關交易仍然只有 `sqlitekit.immediate_transaction` 一條路，`inbox_store.py` 內部仍是 `with immediate_transaction(self._conn):` 沒有改變。`ExecutorTransaction` 只是把「已經在交易中的連線」包成一個有型別的權杖，跨模組傳遞交易本身（`inbox_store.transaction()` 把連線遞給 `attempt_store`）是這次修正之前就有的既有做法（凍結 patch 只是把裸 `sqlite3.Connection` 換成 dataclass,並加測試擋非法建構），不是新引入的第二種交易管理機制。
- **`IllegalAttemptTransition` 搬到 `src/rtb/domain/attempt.py`**：與 `src/rtb/domain/task_state.py` 的 `IllegalTransition` 做法一致（都是領域層定義的 `ValueError` 子類、儲存層直接 raise）。搜過全專案沒有任何地方用 `except AttemptRejected` 廣義接住它，所以把它移出 `AttemptRejected` 繼承鏈不會漏接既有的錯誤處理。
- **部分索引與 `unresolved_count_query`**：專案裡沒有既有的部分索引或「回傳 SQL 字串供 EXPLAIN QUERY PLAN 檢驗」的用法可供比對，這是全新技巧而非取代既有做法，且沒有繞過 `sqlitekit` 或跨層直呼（沒有 import `connect`/`begin_immediate`/`immediate_transaction`，既有的 AST 檢查測試也還在）。
