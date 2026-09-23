severity: major

## F1 flow.py 直接匯入 sqlite3 與 DatabaseBusy,跨層認得資料庫例外
severity: major
blocking: 是 — 驅動函式層(對照 execution.py)原本不認得任何資料庫層例外,DB 例外一律由 store 方法內部吞掉或轉成領域例外(LeaseLost),flow.py 這次自己 import 並 catch 打破了這個分層
引句:「from rtb.sqlitekit import DatabaseBusy」

## F2 uuid.uuid4().hex 當租約擁有者,是專案裡沒出現過的識別產生方式
severity: major
blocking: 是 — 全 repo(含執行側 owner = f"{os.getpid()}-{int(time.time())}")沒有任何地方用 uuid 或任何隨機字串產生器,這支是唯一一處,而且擁有者語意也從「穩定代表某個行程/工作者」變成「每次呼叫都换一個匿名亂數」
引句:「store.acquire_lease(task_id, uuid.uuid4().hex, now)」

## F3 acquire_lease/release_lease 命名跟執行側租約動詞不同
severity: minor
blocking: 否 — 結構(取得回收據、放掉核對收據)跟執行側一致,只是動詞挑了 acquire/release 而不是執行側慣用的 _lease/_held/extend/release/take_over,純命名差異,不影響行為或分層
引句:「def acquire_lease(self, task_id: str, owner: str, now: datetime) -> LeaseReceipt | None:」

---

### 問題一:分層與依賴方向

新碼分兩層,跟鄰居一樣:`task_store.py` 加 `task_leases` 表與 `acquire_lease/release_lease/_current_lease/_holds/_lease_allows/_append_release`,都留在 store 層,只被 `flow.py` 呼叫,方向沒變(`flow.py` 呼叫 `TaskStore`,不是反過來)。`_lease_allows` 在 `commit_step` 交易內做核對,跟既有 `expected_seq` 核對擺在同一段,寫法也是先核對再 `SELECT … ORDER BY seq DESC LIMIT 1`(`task_store.py:263-268`),跟 `latest()`(`task_store.py:204-210`)、`create_task()`(`task_store.py:190-193`)既有的「只取最新一列」寫法一致——**作者表態的 py-memory existing 查證屬實**:改動前的 `task_store.py`(`c2aa8dc`)裡 `latest()`/`create_task()`/`commit_step()` 本來就是 `ORDER BY seq DESC LIMIT 1`,這裡沒有另立寫法。

問題出在 `flow.py`:`import sqlite3` 與 `from rtb.sqlitekit import DatabaseBusy`(`flow.py` diff 第 16、34 行)讓驅動函式層直接認得資料庫例外類型,用在 `_release_keeping_the_original_error` 裡 `except (sqlite3.Error, DatabaseBusy): return`。這算跨層——對照同一角色的 `execution.py`(執行迴圈的驅動函式,協定注入、不開資料庫交易、寫法比照分析行程流程,見其檔頭說明),它完全不 import `sqlite3` 或 `DatabaseBusy`,遇到租約/收據對不上是讓 `InboxStore` 自己吞 DB 例外或丟出領域例外 `LeaseLost`(`execution.py:121,325,400,405,422,437`)再由 `execution.py` catch 領域例外,從不摸資料庫例外類型。`task_store.py` 自己的 `record_tool_call` 也是把 DB 例外的吞法留在 store 內部(`task_store.py:341-357`,吞 `(sqlite3.Error, DatabaseBusy)`)。這次 `flow.py` 反而自己 import 這兩個型別來 catch,等於把「哪些例外算資料庫層失敗」的知識從 store 搬到驅動層,跟兩個鄰居(`execution.py` 的分層方式、`task_store.py` 自己 `record_tool_call` 的封裝方式)都不一樣。更一致的做法應該是 store 提供一個「盡力放掉、失敗就算了」的方法把 `sqlite3.Error`/`DatabaseBusy` 吞在 store 內部,flow.py 不必認得這兩個型別。**這條判為跨層,不是純風格。**

### 問題二:命名與錯誤處理

`LeaseReceipt`(`task_store.py:96-101`,欄位 `task_id, lease_seq, owner`)對照執行側 `Receipt`(`inbox_store.py:198-205`,欄位 `task_id, revision, content_hash, owner, lease_seq`)——命名模式(`XxxReceipt`、`owner`+`lease_seq` 欄位)一致,只是因為分析側租約掛在任務本身、不是掛在某個修訂上,少了 `revision`/`content_hash` 是領域差異,不是命名不一致。

`acquire_lease`/`release_lease` 這兩個名字本身(`task_store.py:294,308`)跟執行側 `_lease`/`_held`/`extend`/`release`/`take_over`(`inbox_store.py:494-548,618`)這組動詞不同——執行側沒有 `acquire` 這個字。這是命名差異但結構相同(取得回收據、核對收據才放掉、對不上就回 False/None),判為 F3、minor。

錯誤處理範圍:`_release_keeping_the_original_error` 只吞 `(sqlite3.Error, DatabaseBusy)`(`flow.py` diff 第 119 行),跟 `record_tool_call` 的既有寫法「只吞資料庫層錯誤,程式錯誤不吞」(`task_store.py:341-357`)吞的型別集合完全一樣,連注解都直接寫「吞的範圍比照 `TaskStore.record_tool_call`」——**這一段錯誤處理範圍跟鄰居一致**,問題只在型別知識該放在哪一層(見問題一的 F1)。

`noqa: PLR0913` 的寫法(`_advance_holding` 沒有加、`advance` 保留既有那行)——`_advance_holding` 剛好 6 個參數,對照 `pyproject.toml` 的 `max-args = 6`(`pyproject.toml:33`),不用加 noqa;`advance` 本身的 noqa 註解是既有程式碼(diff 裡是 context 行,不是新增),沒有變動。跟其他既有 `# noqa: PLR0913 - 理由` 的寫法(`attempt_store.py:308,364`、`runner.py:123`、`dsp/server.py:246`)風格一致,沒有另立格式。

### 問題三:有沒有引入第二種做法

**uuid 當擁有者**:`flow.py` 用 `uuid.uuid4().hex` 產生每次呼叫都不同的租約擁有者(`flow.py` diff 第 70 行)。全 repo 搜尋 `uuid`,只有這一處;執行側的 owner 是啟動程式傳進來、代表某個工作者身分的穩定字串 `f"{os.getpid()}-{int(time.time())}"`(`runner.py:159`,經 `execution.py:316` 的 `owner: str = "executor"` 參數一路傳下去)。這不只是換了一個產生器,擁有者的語意也變了:執行側一個工作者的所有租約用同一個 owner,分析側現在每次呼叫 `advance()` 都换一個匿名亂數,將來想從 owner 反查「是哪個工作者卡住」做不到。這是專案裡沒出現過的識別產生方式,判為 F2、major(第二種做法)。

**只增不改租約表 vs 執行側改欄位**:`task_leases` 表沒有 `UPDATE`/`DELETE`,取得與放掉各新增一列(`task_store.py` diff 第 41-43、298-349 行,並有測試 `test_opening_an_old_task_database_adds_the_lease_table_without_touching_history` 直接斷言原始碼裡沒有 `UPDATE `/`DELETE `);執行側的租約是在同一列上 `UPDATE proposals SET lease_seq = lease_seq + 1, lease_owner = ?, lease_until = ?`(`inbox_store.py:494-498,618-629`)。這兩種做法表面上不同,但**不是這次 diff 才引入的分歧**:`task_store.py` 的檔頭本來就寫明「沒有 UPDATE、沒有 DELETE」(改動前 `c2aa8dc` 版本第 2-6 行就有這段),`inbox_store.py` 的 `proposals` 表本來就有 `UPDATE`(標記 `expired`/`superseded` 等)。也就是說分析側資料庫一直是只增不改、執行側資料庫一直允許 UPDATE,這是兩個子系統各自從增量 1 就有的既有慣例,租約表延續各自母表原本的寫法,不是這次新開一種第三種做法,也不是在同一個模組裡臨時混用兩種寫法。判定:**不算引入第二種做法,是照著各自 store 既有的家規走**,予以放行、不列為 finding。

**`_advance_holding` 拆函式**:把「核對序號 → 呼叫外部介面 → 提交 → 放租約」拆成 `advance`(取得租約、包 try/except)+`_advance_holding`(真正做事)+`_release_keeping_the_original_error`(放租約的例外安全網),跟原本 `advance` 是單一函式的寫法比,多了兩層。但這個拆法沒有引入新語言機制或新控制流模式,純粹是把既有邏輯搬進獨立函式方便讀,跟專案裡其他驅動函式(`execution.py` 的 `_process`/`_settle`/`_release`/`_take`/`_record` 這種一路拆小函式的寫法)是同一種風格,不判為第二種做法。

不對齊共 3 條,其中 major 2 條。
