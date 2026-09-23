severity: major

### 1. 新增的部分索引參照 Phase 6 才補上的欄位,字面實作會讓舊資料庫在開啟時直接撞成 SQLite 錯誤
severity: major
blocking: 是 屬於審查鏡頭第一項(預留欄位的補欄做法),照字面實作會在啟動階段直接卡死(SQLite OperationalError),而且會讓合約 [S343] 的舊資料遷移測試連資料庫都開不起來,有具體、可重現的證據
引句:「給第一列建一個部分索引(租戶、預留時間,只收預留金額大於 0 的第一列)」

說明:設計要求新增的部分索引直接參照兩個 Phase 6 才會補上的新欄位(租戶、預留金額)。但本專案既有的「補欄位」機制,執行順序是先連線執行整份 `SCHEMA`(含所有 `CREATE INDEX`),之後才在 `_migrate_columns()` 裡對缺欄位的舊表做 `ALTER TABLE ADD COLUMN`——`InboxStore.__init__` 先呼叫 `connect(path, busy_timeout_seconds, SCHEMA + attempt_store.SCHEMA)`(把整個 schema 字串一次 `executescript()`),只有成功之後才呼叫 `self._migrate_columns()` 去補欄位。對一個 Phase 6 之前建立的執行行程資料庫(`attempts` 表還沒有 `tenant`/`reserved_amount` 兩欄),`CREATE TABLE IF NOT EXISTS attempts (...)` 因為表已存在會是 no-op,不會補上新欄位,但緊接著同一個腳本裡若照現有慣例把這個新的部分索引一起放進 `attempt_store.SCHEMA`(既有三個索引 `attempts_first_rows`、`attempts_terminal_rows`、`attempts_one_terminal_per_key` 全都放在同一段 SCHEMA 字串裡,設計文字也沒有另外指出這個新索引要放在別處或延後建立),`CREATE INDEX ... WHERE seq = 1 AND reserved_amount > 0` 會直接參照一個舊表上還不存在的欄位。

具體例子(輸入 → 預期):輸入一個 Phase 6 之前建立、`attempts` 表沒有 `tenant`/`reserved_amount` 欄位的執行行程資料庫檔,啟動執行行程開啟它 → 字面實作下 `connect()` 內的 `conn.executescript(schema)` 會丟出 `sqlite3.OperationalError: no such column: tenant`(已在暫存目錄用 sqlite3 實際重現,見下方指令),`connect()` 的例外處理只把非鎖競爭的 `OperationalError` 原樣往外丟,`InboxStore.__init__` 直接失敗,`_migrate_columns()` 根本沒有機會執行補欄位——執行行程完全無法啟動。這正是 [S343]「當執行行程資料庫是 Phase 6 之前建的,開啟時應替嘗試紀錄補上租戶與預留金額兩欄」要測的情境,字面實作下這條測試會在「資料庫開不起來」這一步就先炸掉,而不是驗到欄位補齊與舊曝險保守計入的邏輯。既有的 ADDED_COLUMNS 機制(`written_version`、`capability_expires_at`,Phase 3 增量 3)從未有索引參照過這兩個補上的欄位,所以這是本專案第一次讓「先建索引、後補欄位」的既有次序反過來變成陷阱,設計文字完全沒有處理這個順序問題(沒有說要把這個索引的建立挪到 `_migrate_columns()` 補完欄位之後,或另外用類似 `_rebuild_proposals()` 的延後建立手法)。

file: `src/rtb/executor/inbox_store.py:270`
file: `src/rtb/executor/inbox_store.py:274`
file: `src/rtb/sqlitekit.py:29-31`
file: `src/rtb/executor/attempt_store.py:45-58`
file: `src/rtb/executor/attempt_store.py:61-65`
file: 已用 `python3` 對記憶體內 SQLite 實際重現:`CREATE TABLE IF NOT EXISTS t (a INTEGER PRIMARY KEY)` 後執行 `conn.executescript("CREATE TABLE IF NOT EXISTS t2 (a INTEGER PRIMARY KEY); CREATE INDEX IF NOT EXISTS idx ON t2 (tenant) WHERE a > 0;")` 丟出 `sqlite3.OperationalError: no such column: tenant`
