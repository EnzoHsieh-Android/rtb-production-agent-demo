severity: clean

本輪只鎖三個第 2 輪之後改過的點,逐一對照程式碼與第 2 版之前的做法,沒有發現引入第二種做法或跨層依賴。

- **嘗試紀錄的部分索引改放進補欄位流程裡建。** 第 2 版寫「嘗試紀錄的部分索引參照的租戶欄是後補的欄位……所以放在補欄位流程裡、補完欄位之後才建」。核對 `src/rtb/executor/inbox_store.py:338`(`_migrate_columns`)與 `attempt_store.py:64`(`ADDED_COLUMNS`):現有補欄位機制已經是「先 `ALTER TABLE ... ADD COLUMN`,同一個 `immediate_transaction` 內完成」的單一入口,`tenant`、`reserved_amount` 兩欄(增量 1)已經是照這條路徑補上的既有成員。設計把新的租戶部分索引也放進同一個 `_migrate_columns` 流程、接在補欄位之後,是在既有機制上延伸一步,不是另開一條補索引的路。跟既有「第一列按廣告」的部分索引(`attempts_first_rows`,`attempt_store.py:53`)寫法同一種技術(`WHERE seq = 1`),差別只在建立時機(靜態 SCHEMA vs 補欄位流程之後),因為它依賴的欄位是後補的,原因寫得清楚,不是憑空多一種做法。停下紀錄表(`write_stops`)的三個索引因為那張表本身是全新建的,直接寫進 `CREATE TABLE` 語句,跟 `attempts_first_rows`、`task_store.py:55` 的 `follow_ups_by_campaign` 同一種既有寫法(新表索引寫進建表語句)。兩種時機分流(新表直接寫進 SCHEMA、舊表後補欄位的索引放進補欄位流程)照的是既有規則本身的界線,不是新增第三種。

- **查詢五收傳入門檻。** 核對 `execution.py:409` 到 `433`:既有簽發路徑(`_sign`/`_take`)已經是「簽發時在交易外用 `capability_signer.load_tenants` 讀門檻,包進 `Grant`/`Reservation` 傳進交易」,`attempt_store.aggregate_used(tx, tenant, now)` 本身完全不讀設定檔、門檻由呼叫端傳入比較。查詢四、查詢五都照這個既有語意——由呼叫端用 `load_tenants` 取得門檻後傳入,查詢函式本身不讀設定檔——跟既有寫入路徑同一種介面形狀,沒有另開一條「查詢自己讀設定檔」的路。查詢五放在收件口模組、依賴嘗試紀錄模組,方向與現有的「收件口依賴嘗試紀錄,反過來沒有」(`inbox_store.py:1` 模組說明、`from rtb.executor import attempt_store`)一致,沒有新增反向依賴。

- **`aggregate_used` 內部拆清單。** 核對 `attempt_store.py:355`(`aggregate_used`):目前實作本來就是「兩段 SQL 撈 `rows` → `sum(_counted(row, tenant) for row in rows)`」的兩步結構,拆成「列出清單」與「加總」是把既有的中間結果(`rows`)升格成一個對外函式,加總的一步改呼叫它,不是重寫第二套算法或另開一條資料來源。查詢五「目前佔額度的」清單與查詢四的已用共用同一份底層查詢與同一支 `_counted` 判斷,設計裡也明講「跟 `aggregate_used` 共用同一支判斷(`_counted`)」,是同一顆函式的兩個呼叫端,不是分岔成兩種計入規則。

沒有發現第二種做法或跨層問題,判 clean。
