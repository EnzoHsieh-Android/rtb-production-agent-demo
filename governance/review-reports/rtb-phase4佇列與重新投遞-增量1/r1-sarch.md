severity: clean

查了什麼:對照 `增量 1 設計:佇列語意與確認規則` 節(及其前面「三個增量」的範圍段)與下列既有邊界,逐條核對有沒有引入第二種做法或跨層直呼——

1. **佇列語意長在收件表上、不是新開一張表**:設計明寫「沿用既有補欄位做法(每次連線檢查、缺才在交易內補、拿到鎖後再查一次),加兩欄:租約到期時間與投遞次數」,跟 `src/rtb/executor/inbox_store.py` 的 `_ADDED_COLUMNS` / `_missing_columns()` / `_migrate_columns()`(第 80–83、206–220 行)這套既有補欄位機制字面一致,沒有另開表、另存訊息格式。

2. **租約用資料庫欄位、不是另一種鎖**:設計把租約講成「租約到期時間」欄位,取件/確認/延長租約都描述成落在既有的 `store.transaction()`(`ExecutorTransaction`)裡的 UPDATE,對照 `inbox_store.py` 的 `_dispose()`/`transaction()`(第 225–239、357–367 行)與 `src/rtb/sqlitekit.py` 的 `immediate_transaction()`(BEGIN IMMEDIATE)是同一套交易機制;沒有出現 `threading.Lock`、外部鎖檔或其他第二種鎖。`runner.py` 的單一執行者鎖檔(另一種 SQLite 鎖,第 44–72 行)屬於增量 3 的範圍,增量 1 文字沒有去動它,也沒有把這兩種鎖混用。

3. **死信沿用既有處置與原因列舉**:設計把死信講成「處置列舉從兩個成員變三個」「原因代碼沿用既有的擋下原因列舉、新增一個成員」,對照 `inbox_store.py` 的 `Disposition`/`BlockCode` 都是封閉的 `StrEnum` 並靠 `_in_list()` 轉成資料庫 `CHECK` 約束(第 36–60 行);設計沒有另開一個獨立的「死信原因」列舉或用自由字串,是在既有兩個封閉列舉上各加一個成員,做法一致。

4. **確認時機改變仍留在既有呼叫邊界內**:確認規則把「寫處置」的時機從取件(`_take()`)延後到嘗試到終點,但呼叫路徑仍是「execution.py 判斷 → 呼叫 `store.hand_off`/`store.block` 這類既有方法」,沒有出現 execution.py 或 runner.py 繞過 `InboxStore`/`ExecutorTransaction` 直接下 SQL 的跡象;「終點」「未結案」的分類也直接借用 `src/rtb/domain/attempt.py` 既有的 `TERMINAL_STATES`/`UNRESOLVED_STATES`(第 70–71 行),不是自造一套新的狀態分類。

在這個「只判第二種做法或跨層直呼」的鏡頭下,沒有找到 major 等級的架構偏離。
