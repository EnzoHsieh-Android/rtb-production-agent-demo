severity: major

### 1. 死信「最後一次處理的結果」要引用執行迴圈層(execution.py)的列舉,讓收件口反過來依賴它上面那一層

severity: major
blocking: 是 照字面做,inbox_store.py 要嘛出現循環匯入而無法啟動,要嘛被迫放棄既有「列舉靠資料庫 CHECK 約束把關」的寫法,兩者都要在增量 1 定案,拖到增量 2、3 才發現會回頭改收件表結構。
引句:「死信另存**最後一次處理的結果**(執行迴圈的結果列舉,可空)當失敗脈絡,讓人看得出它是卡在哪一步」

說明:「執行迴圈的結果列舉」在這個專案裡只對得上一個東西——`src/rtb/executor/execution.py` 第 237 行的 `class Result(StrEnum)`(IDLE / DEFERRED / EXPIRED / BLOCKED / HANDED_OFF_TO_EXISTING / EXECUTED),它是「處理一筆」`process_one()` 回傳的結果,定義在執行迴圈那一層。但依賴方向現在是單向的:`execution.py` 第 30 行 `from rtb.executor.inbox_store import BlockCode, InboxStore, PendingProposal` 匯入收件口,收件口自己完全不匯入 `execution.py`(收件口的職責宣告也講明「這支模組也是整個執行行程資料庫唯一開連線、開交易的地方」,是底層)。

收件表對「封閉列舉」欄位一律靠 `_in_list(EnumClass)` 現場從活的列舉類別組出資料庫 CHECK 約束(`inbox_store.py` 第 54–60 行的 `_DISPOSITION_COLUMN`、`_BLOCK_CODE_COLUMN` 都這樣做,連 `block()` 方法都在第 379–380 行擋下非 `BlockCode` 型別的值)。如果「最後一次處理的結果」這欄也要照這個既有寫法上 CHECK,inbox_store.py 就得 `from rtb.executor.execution import Result`——但 execution.py 已經反向匯入 inbox_store,這會是循環匯入,匯入時直接炸掉整個執行行程。

輸入 → 預期:一份提案第 5 次取件仍未確認,依 [S102] 要寫死信並附「最後一次處理的結果」;若照設計把這欄存成 `Result.DEFERRED`(或任一 `Result` 成員)並比照既有寫法補 CHECK 約束 → 匯入 execution.py 造成循環匯入,啟動即失敗。若為了避開循環匯入,改成這一欄不設 CHECK、直接存自由字串 → 同一張表裡 disposition、block_code、死信原因都有資料庫層兜底,唯獨這一欄沒有,是同一張表裡兩種不同的「存列舉值」做法並存(第二種做法),而且繞過模組直接寫也擋不住亂值,跟 `inbox_store.py` 檔頭「資料庫自己也只收列舉值,繞過模組直接寫也寫不進去」這個既有承諾矛盾。

file: `src/rtb/executor/execution.py:30`
file: `src/rtb/executor/execution.py:237`
file: `src/rtb/executor/inbox_store.py:54`
file: `src/rtb/executor/inbox_store.py:59`
file: `src/rtb/executor/inbox_store.py:60`
file: `src/rtb/executor/inbox_store.py:10`

### 2. 處置欄位 CHECK 約束要靠「重建表」遷移,本專案沒有先例、sqlitekit 沒有對應工具

severity: minor
blocking: 否 這是 SQLite 本身不能用 `ALTER TABLE` 改既有 CHECK 約束逼出來的做法,不是在跟既有「補欄位」寫法搶同一個問題的第二解,所以不擋增量 1 定案;但值得記下來,免得增量 2、3 或之後別的表也要改約束時,各自重新摸索一次重建表的細節(索引、WAL、鎖序)。
引句:「補欄位遷移要照 SQLite 官方的重建表步驟(建新表、抄資料、換名),整段在同一個立即取得寫入鎖的交易裡做,拿到鎖後再查一次」

說明:專案裡三支資料庫模組(`inbox_store.py`、`dsp/store.py`、`analyzer/task_store.py`)現有的遷移全部是「補欄位」——`PRAGMA table_info` 查缺欄位、缺就在鎖到的交易裡 `ALTER TABLE ... ADD COLUMN`,三支各自寫一份但手法一致(`inbox_store.py` 第 213–220 行、`dsp/store.py` 第 217–245 行、`analyzer/task_store.py` 第 148–157 行都是同一個形狀)。`sqlitekit.py` 全檔(見 1–64 行)只提供 `connect`/`begin_immediate`/`immediate_transaction` 這三個連線與交易原語,沒有任何「重建表」或「改約束」的共用函式,專案裡也搜不到既有的 `RENAME TO` / 建新表複製資料的前例。

這份設計要求的「改 CHECK 約束、多收兩個處置成員」是 `ALTER TABLE` 做不到的(SQLite 只能靠它加欄位、改名,不能改既有欄位的 CHECK),所以重建表本身沒有可替代的「第一種做法」好比較,不算違反「不引入第二種做法」;但它是全專案第一次出現這麼重的 DDL 操作,而且完全沒有共用工具兜底,實作時有沒有處理好索引重建、交易期間別的連線讀到半成品表、以及三份既有補欄位程式碼會不會被要求比照這個新手法改寫,設計文字都沒交代,值得在增量 1 定案前補一句「這段留在 inbox_store.py 自己刻,不進 sqlitekit」或反過來講清楚。

file: `src/rtb/executor/inbox_store.py:213`
file: `src/rtb/dsp/store.py:217`
file: `src/rtb/analyzer/task_store.py:148`
file: `src/rtb/sqlitekit.py:1`
