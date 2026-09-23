severity: clean

第 1 輪架構席的折法(observability.py 不寫 SQL、停下紀錄走收件口模組的方法、嘗試紀錄走嘗試紀錄模組的函式)在第 2 輪修正裡確實落地,沒有引入第二種做法或跨層讀寫。逐項核對:

**收件口模組的 `stop_count`/`stops`**——兩支都放在 `InboxStore` 類別內、緊接在 `take_over` 之後、`record_event` 正上方(`src/rtb/executor/inbox_store.py:715-736`),與主線要求的位置一致。都在方法一開頭呼叫 `self._own(tx)`(`inbox_store.py:721`、`inbox_store.py:732`),跟既有的 `extend`、`release`、`ack_handed_off` 等「執行迴圈用」方法同一種交易歸屬核對寫法,不是另開一套檢查。查詢字串组裝抽成模組層函式 `stop_count_query`(`inbox_store.py:770-782`),回傳 `(sql, params)` 給測試看查詢計畫,`stops` 本身直接內嵌 SQL(因為固定回傳欄位、不像 `stop_count` 有多種篩選組合需要測查詢計畫)——這跟同檔案內既有的一支方法配一份查詢字串的寫法相符,不算另一種模式。

引句:「停下紀錄計數的查詢語句(測試用它看查詢計畫);篩選值一律走參數,只拼接固定條件。」(`inbox_store.py:774`)

**模組層 `stop_count_query` 的放法**——放在 `InboxStore` 類別定義之後、模組尾端(`inbox_store.py:770`),對照 `attempt_store.py` 的 `unresolved_count_query`(`attempt_store.py:260`)與 `first_rows_started_query`(`attempt_store.py:426`)都是「類別外的模組函式、被類別內或另一支模組函式呼叫」的同一種佈局,只是 `attempt_store` 把這類函式放在類別定義之前、`inbox_store` 放在之後——這是既有兩支模組本來就不一致的檔案內順序(不是這次修正新引入的),不影響呼叫關係或分層,不算新做法。

**嘗試紀錄模組新函式**——`counted_first_rows_started`/`counted_first_rows`(`attempt_store.py:438-457`)延續 `unresolved_count`/`unresolved_count_query`、`version_conflict_count` 已經建立的「純函式接 `tx`、模組層組 SQL、回傳唯讀資料」慣例,沒有另開連線或另收憑證。`aggregate_used` 改成呼叫新拆出的 `aggregate_holdings` 做加總(`attempt_store.py:374-377`),呼叫關係是模組內部重構,不影響對外介面或分層。

`TENANT_INDEX`、`tenant_index_missing` 定義在 `attempt_store.py`(`attempt_store.py:73-74`、`359-362`),由 `inbox_store._migrate_columns` 呼叫來建索引(`inbox_store.py:350`、`356`)。這是既有分工的延伸,不是新模式:`attempts` 表的欄位定義(`ADDED_COLUMNS`)本來就放在 `attempt_store`、由 `inbox_store` 這個「唯一開連線開交易的地方」實際執行 `ALTER TABLE`(`inbox_store.py:176`、`_ADDED_COLUMNS = {"attempts": list(attempt_store.ADDED_COLUMNS)}`);這次新增的索引沿用同一條分工線(表結構定義歸屬資料的擁有模組、遷移動作歸屬連線的擁有模組),沒有另開第二種遷移路徑。

**新模組只組合兩邊公開介面**——`observability.py` 已確認不再內嵌 SQL,四支查詢都只呼叫 `InboxStore` 的方法(`store.stop_count`、`store.stops`)與 `attempt_store` 的模組函式(`aggregate_used`、`aggregate_holdings`、`counted_first_rows_started`、`counted_first_rows`、`latest`),沒有繞過兩個模組直接碰 `attempts`/`write_stops` 表。反向確認:`grep` `inbox_store.py`、`attempt_store.py` 全檔沒有出現 `observability`,依賴方向只有「observability → inbox_store、attempt_store」,跟筆記寫的一致。

沒有發現架構層面的問題。
