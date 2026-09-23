severity: major

## F1 字面實作會讓舊資料庫永遠補不到嘗試紀錄的租戶索引(補欄位流程的「缺才進交易」門檻沒把「缺索引」算進去)

severity: major
blocking: 是 — 照設計字面的位置指示實作,現有(已跑過增量 1)的每一個生產資料庫都會永久拿不到 S369/S370 要保證的索引,不是邊角案例

引句:「嘗試紀錄的部分索引參照的租戶欄是後補的欄位,既有開庫順序是先跑整份建表與建索引、後補欄位,放進建表語句會讓舊資料庫一開就失敗,所以放在補欄位流程裡、補完欄位之後才建」

現況:`_migrate_columns` 的結構是「先在交易外快速判斷有沒有東西要補,沒有就直接返回;有才進 `BEGIN IMMEDIATE` 交易,拿到鎖後重查一次再動手」:

```
def _migrate_columns(self) -> None:
    if not self._missing_columns() and not self._proposals_outdated():
        return
    with immediate_transaction(self._conn):
        for table, ddl in self._missing_columns():
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        if self._proposals_outdated():
            self._rebuild_proposals()
```
file: `src/rtb/executor/inbox_store.py:340-354`

設計只交代「新索引要放在補完欄位之後」,沒交代要同時修改最外層那個「缺才進交易」的判斷條件(`_missing_columns()` / `_proposals_outdated()`)。照字面把索引建立語句直接接在補欄位迴圈後面(還在同一個由 `_missing_columns()`/`_proposals_outdated()` 把關的區塊裡),對「本來就已經補齊 `tenant`、`reserved_amount` 等欄位、只是還沒有這支索引」的資料庫——也就是任何已經跑過增量 1 的既有資料庫——`_missing_columns()` 永遠回空、`_proposals_outdated()` 永遠是 False,最外層的判斷會直接 `return`,連交易都不會開,索引建立那行程式碼永遠不會被執行到。這不是一次性的問題:`_migrate_columns` 每次開庫都會重新跑一次同樣的判斷,所以這個資料庫會**永久**卡在沒有這支索引的狀態,除非把檔案刪掉重建。

已用臨時目錄實測驗證(不是用讀到的程式碼推論,是真的跑):
1. 用現有(增量 4 之前)的 `InboxStore` 在空檔案上開庫一次再關閉——這步驟本身就會把 `tenant`、`reserved_amount` 等欄位透過既有補欄位流程補齊,代表「已經跑過增量 1」的真實資料庫狀態。開完之後 `attempts` 上只有既有四個索引,沒有任何按租戶的索引(印證「現況」段所說的「沒有任何索引」對 `write_stops` 成立、對 `attempts` 也只有既有兩個部分索引)。
2. 照設計字面,把新索引的 `CREATE INDEX IF NOT EXISTS ... ON attempts (tenant, written_at) WHERE seq = 1` 接在補欄位迴圈之後、仍在同一個由既有 `_missing_columns()`/`_proposals_outdated()` 把關的區塊裡,重新開這顆已經補齊欄位的資料庫——索引沒有出現(`tenant index present: False`)。
3. 對照組:用一顆連 `tenant` 欄位都還沒有的真正舊資料庫(未跑過增量 1)跑同一段邏輯,索引正常出現(`tenant index present: True`)——因為這種情況下 `_missing_columns()` 非空,會正常進交易。

換句話說,字面實作只對「完全沒跑過任何 Phase 6 遷移」的資料庫有效,對「已經跑過增量 1、只差這支索引」的資料庫——也就是這個專案裡實際會遇到的升級路徑——完全無效。

對照 [S370] 的斷言:「當嘗試紀錄是 Phase 6 之前建的(沒有租戶欄),開啟後應補上租戶欄與按租戶和開始時間的部分索引,不丟例外、舊列不變。」——引句:「當嘗試紀錄是 Phase 6 之前建的(沒有租戶欄)」這個前提條件明確把測試場景釘在「連欄位都沒有」的情況,跟上面驗證會通過的對照組一致,不會碰到真正壞掉的「欄位已補、索引未補」狀態,所以 [S370] 驗不到這個坑。

[S366] 的斷言:「當停下紀錄表與嘗試紀錄是加索引之前建的,開啟時應補上本節的四個索引,舊列不變。」措辭比較籠統,「加索引之前建的」理論上可以涵蓋「已跑過增量 1」這種資料庫,但也可以照字面沿用跟 [S370] 一樣的「沒有租戶欄」固定件(兩條合約在「審計修正紀錄」段落是同一輪一起加的,`test_an_old_attempts_table_gains_the_tenant_index_after_the_column` 這個測試名稱本身就是照 [S370] 的敘述「後補欄位之後」命名,暗示撰寫者心裡的固定件就是「先缺欄位」那一種)。設計沒有明講 [S366] 的固定件要涵蓋「欄位已補、索引未補」這個狀態,所以能不能驗到完全取決於實作者自己有沒有想到——這正是本節開頭「現況」段自己都沒發現、要靠實測才浮現的坑,不能指望這條斷言字面上一定擋得住。

修法方向(供參考,不是本輪要做的事):最外層那個「缺才進交易」的判斷要多一個「索引缺」的檢查項(跟 `_proposals_outdated()` 現在的接法一樣,同時掛在外層判斷與交易內重查),不能只靠「補完欄位之後」這句話決定索引程式碼放哪一行。

## 其他核對過、沒有發現問題的地方

`write_stops` 的三個新索引:設計說「這張表的三個索引參照的欄位建表時就有,放進建表語句即可」——核對 `write_stops` 的 `CREATE TABLE` 語句(`src/rtb/executor/inbox_store.py:155-160`),`kind`、`tenant`、`campaign_id`、`at` 都是建表當時就有的欄位,不是後補欄位,增量 4 也沒有要幫 `write_stops` 加欄位。把三個索引直接寫進 `SCHEMA` 字串是安全的:`InboxStore.__init__` 每次開庫都會呼叫 `connect(path, ..., SCHEMA + attempt_store.SCHEMA)`(`src/rtb/executor/inbox_store.py:308`),而 `connect()` 每次都無條件執行 `conn.executescript(schema)`(`src/rtb/sqlitekit.py:29`),不像補欄位流程有「缺才進交易」的門檻,所以不管資料庫是全新的還是已經開過很多次的舊庫,`CREATE INDEX IF NOT EXISTS` 每次開庫都會嘗試執行一次,舊庫一樣補得到。這部分字面實作是可行的,跟 F1 描述的 `attempts` 租戶索引那種「被門檻擋住」的狀況不一樣。

多個工作者同時開庫的競態:三個 `write_stops` 索引與既有四個 `attempts` 索引一樣是透過 `executescript` 在連線建立時無條件跑 `CREATE INDEX IF NOT EXISTS`,不是新引入的並行模式——SQLite 對並行 DDL 本來就靠檔案鎖擋,兩個行程同時開空庫時,其中一個會拿到寫入鎖先建完,另一個撞鎖等到逾時內完成或收到 `DatabaseBusy`(`connect()` 已經有 catch `_is_lock_contention` 轉成 `DatabaseBusy`,呼叫端本來就會重試),沒有新增的競態風險。真正在交易內、需要「拿到鎖後重查一次」的是 `attempts` 的租戶部分索引(因為它依賴後補欄位),而這條路徑的問題不是競態,是 F1 講的「根本進不了交易」。若照 F1 的修法補上索引缺失檢查、並沿用既有「重查一次」模式(跟補欄位、`_proposals_outdated()` 一樣的寫法),兩個工作者同時撞上「缺索引」也會照既有模式正常收斂,不會有新的並行問題。
