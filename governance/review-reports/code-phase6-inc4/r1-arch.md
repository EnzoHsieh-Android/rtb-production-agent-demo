severity: major

## F1 observability.py 直接對收件口模組的 write_stops 表下 SQL,繞過既有的「表由擁有模組的方法讀」慣例

severity: major
blocking: 是 — 破壞合約(跨模組直接碰別人擁有的表,不是走擁有模組的公開方法),屬於架構層而非風格偏好

`write_stops` 表是收件口模組(`inbox_store.py`)建的,在這次改動之前,唯一讀它的地方是 `InboxStore` 自己的方法 `stop_amount`——用 `self._conn`,而且方法一開頭呼叫 `self._own(tx)` 核對「這筆交易是這個收件口自己開的連線」(`tx.conn is not self._conn` 就拒絕)。這是既有慣例:嘗試紀錄表由 `attempt_store` 自己的函式讀寫(`version_conflict_count`、`aggregate_used` 等一律用模組內的 `_conn(tx)`),收件口自己的表由 `InboxStore` 自己的方法讀寫,沒有第三個模組直接對別人的表下 SQL 的先例。

新模組 `observability.py` 打破了這個慣例。它不 import `InboxStore`,只 import 了 `StopKind` 這個列舉:

引句:「from rtb.executor.inbox_store import StopKind」

但它接著自己組 SQL 對 `write_stops` 下查詢,例如計數查詢:

引句:「SELECT count(*) FROM write_stops WHERE {' AND '.join(where)}」

以及稽核明細查詢:

引句:「SELECT {_STOP_COLUMNS} FROM write_stops WHERE kind = ? AND tenant = ? 」

這兩處都是拿 `attempt_store.connection(tx)` 回傳的原始連線直接執行,而不是呼叫 `InboxStore` 既有或新增的方法。連線本身也是這次新開的第二條取得路徑:

引句:「同一個執行行程資料庫交易的連線,先核對交易仍開著(可觀測查詢讀停下紀錄表用)。」

`attempt_store.connection()` 做的核對只到「型別對、交易還開著」(沿用既有 `_conn(tx)` 的檢查),沒有 `InboxStore._own()` 那層「這筆交易是不是這個收件口實例自己開的連線」的核對。目前系統裡一個執行行程只會開一個 `InboxStore`,所以實際不會撞到不同連線,但這是把「表的存取權限檢查」繞過了一層,不是單純風格問題:換成任何別的模組,只要能拿到 `ExecutorTransaction`,現在都能不經過 `InboxStore` 直接讀寫 `write_stops`(目前只有讀,但沒有機制擋寫)。

比對设计筆記自己宣稱的邊界也對不上:

引句:「也讓依賴方向只有「新模組 → 嘗試紀錄模組」」

這句話只講 Python 模組匯入層面成立(`observability.py` 沒 import `inbox_store` 的 `InboxStore` 類別),但資料表存取層面其實是「新模組 → 收件口模組擁有的表」,依賴方向並不像敘述的那樣單純只到嘗試紀錄模組。如果要維持「表由擁有模組的方法讀」這個既有分層,`aggregate_stop_count`、`table_full_deferral_count`、`aggregate_audit` 裡讀 `write_stops` 的部分應該透過 `InboxStore` 新增的公開方法(仿照 `stop_amount` 的寫法),而不是繞過去對表名直接下 SQL。

---

以下是確認過、跟既有做法一致、不算發現的部分:

- 查詢收的交易種類跟既有做法一致。`observability.py` 的函式全部收 `ExecutorTransaction`(收件口交易入口開出的寫入交易),文件自己也點出這跟 Phase 5 `version_conflict_count` 同一種做法(握寫入鎖、只讀不寫),這跟 `attempt_store.py` 既有的唯讀查詢(`version_conflict_count`、`unresolved_count`)用同一種交易取用方式,沒有另開一種唯讀交易或延遲交易。

- 索引補建放進補欄位流程的位置,跟既有的「先建表建索引、後補欄位」開庫順序沒有衝突,是同一條規則的延伸而非新規則:Phase 6 增量 1 原本就記著「新部分索引只能用既有欄位,不能參照新補的欄位,否則舊資料庫一開就失敗」,增量 4 的 `attempts_first_rows_by_tenant` 第一次需要參照後補的租戶欄,所以順理成章地挪到 `_migrate_columns` 補完欄位之後才建(`tenant_index_missing` 判斷 + `self._conn.execute(attempt_store.TENANT_INDEX)`)。`inbox_store.py` 本來就已經用 `attempt_store.ADDED_COLUMNS` 幫 `attempt_store` 擁有的 `attempts` 表補欄位(`_ADDED_COLUMNS = {"attempts": list(attempt_store.ADDED_COLUMNS)}`),這次只是同一個既有分工(收件口模組管兩張表的開庫與遷移、attempt_store 提供欄位/索引定義常數)多做一件事,不是開了新的做法。`write_stops` 自己新增的三個索引則直接放進 `CREATE TABLE` 語句所在的 `SCHEMA`,因為參照的是建表時就有的欄位,跟既有「建表順序建索引」的路徑一致,沒有走補欄位流程,這點也符合既有邏輯(新索引該不該進補欄位流程,取決於它參照的欄位是不是後補的,不是看它是哪個增量加的)。

- `test_aggregate_limit.py` 模擬 Phase 6 之前資料庫時,先 `DROP INDEX attempts_first_rows_by_tenant` 再 `ALTER TABLE ... DROP COLUMN tenant`,順序跟「索引參照這個欄位,拿掉欄位前要先拿掉索引」的 SQLite 限制一致,不是額外開的新做法。
