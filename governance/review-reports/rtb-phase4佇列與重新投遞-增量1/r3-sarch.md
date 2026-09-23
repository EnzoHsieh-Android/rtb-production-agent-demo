severity: major

### 1. 取件裡「查未結案廣告」與「查既有嘗試紀錄」兩步,沒有指定要經 attempt_store 的既有函式,照字面可讀成收件表直接對 attempts 表下 SQL,等於開第二條查詢路徑

severity: major
blocking: 是 這兩步是取件邏輯的核心分支(排哪些廣告、要不要交出去),照字面實作若真的在 `inbox_store.py` 裡新寫 SQL 直接查 `attempts` 表,就會跟全專案「attempts 表內容只透過 attempt_store 模組函式讀寫」這條至今零例外的既有邊界打架,而這個邊界正是增量 2(重新投遞恢復要依賴 attempt_store 的未結案計數、終點列判斷一致)與增量 3(圍籬要靠 attempt_store 對 `attempts` 表唯一索引與交易語意的既有保證)後續要繼續靠的地基;一旦增量 1 先開了第二條查詢路徑,增量 2、3 沿用哪一條就成了懸案。
引句:「用嘗試紀錄查,嘗試表跟收件表在同一個檔」

說明:這句話出現在取件第 1 步——「在同一個交易裡算出『已有未結案嘗試的廣告』(用嘗試紀錄查,嘗試表跟收件表在同一個檔),排除它們」,第 3 步「先看這把鍵有沒有嘗試紀錄(用快照重算冪等鍵)」也是同一件事(查 `attempts` 表內容)。這兩步現在要挪進 `receive()`(取件,設計明講「比照既有的收件」,即比照 `accept()` 那種由 `InboxStore` 自己開交易、自己讀寫的寫法),但設計全文沒有一處寫「呼叫 `attempt_store.unresolved_count()`」或「呼叫 `attempt_store.latest()`」——而這兩個函式正好就是現有程式裡做這兩件事的唯一入口(`unresolved_count` 給 `execution.py` 的 `_pick()` 用來算未結案廣告、`latest` 給 `begin()`/`_reconcile()` 用來查最新嘗試列)。

我核對過全專案:對 `attempts` 表內容的每一筆 `SELECT`/`INSERT` 都只出現在 `attempt_store.py` 一支檔裡(`latest`、`history`、`snapshot`、`unresolved_count_query`、`awaiting_reconciliation`、`recover_in_flight`),`inbox_store.py` 對 `attempts` 表向來零直接 SQL,只在 `_ADDED_COLUMNS`(第 82 行)引用 `attempt_store.ADDED_COLUMNS` 做欄位遷移、以及建立 `ExecutorTransaction` 物件——這跟兩支模組檔頭各自寫的分工("外部寫入嘗試的表結構在 attempt_store,但由這裡建立"、"這支模組不開連線、不開交易:每個函式只接受…交易入口發出的交易物件")完全對得上,是全專案迄今唯一、沒有例外的做法。

「嘗試表跟收件表在同一個檔」這句原意是拿來解釋「為什麼能放進同一個交易」(物理上同一條連線),但字面上也同樣可以拿來當「所以收件表可以直接查 attempts 表」的理由——這正是這句話危險的地方:它沒有把「經 attempt_store 函式」釘成唯一讀法,留下了另一種一樣站得住的讀法。

輸入 → 預期:實作者照字面把取件第 1 步寫成 `inbox_store.py` 內一段新 SQL(例如 `SELECT campaign_id FROM attempts WHERE seq=1 ...` 之類自組的未結案判斷),跟 `attempt_store.unresolved_count_query()`(第 231–239 行)各自維護一份幾乎相同但不保證同步的邏輯 → 兩處對「未結案」的定義後續改一邊忘改另一邊時不會有任何測試或型別檢查抓到,而且 `inbox_store.py` 從此多了一條沒有經過 attempt_store 把關的 attempts 表存取路徑,增量 3 若之後在 attempt_store 收斂圍籬邏輯(例如把「未結案」定義加一個新排除條件),`inbox_store.py` 裡這條獨立 SQL 不會跟著變。

file: `src/rtb/executor/attempt_store.py:198`
file: `src/rtb/executor/attempt_store.py:242`
file: `src/rtb/executor/inbox_store.py:25`
file: `src/rtb/executor/inbox_store.py:82`
file: `src/rtb/executor/execution.py:326`

---

以下三項在這個「只判第二種做法或跨層直呼」的鏡頭下,沒有發現 major:

- 兩個新封閉列舉(死信原因、最後一次失敗)已明寫「都定義在收件表模組裡,不借執行迴圈的結果列舉」,直接解掉第 2 輪架構對齊席抓到的循環匯入風險(當時的引句「執行迴圈的結果列舉」對應 `execution.py:237` 的 `Result`,現已不再借用),寫法跟既有 `Disposition`/`BlockCode` 的 `StrEnum` + `_in_list()` CHECK 約束一致,沒有第二種列舉做法。
- 對帳「原子接手租約」的寫法(條件是處置為處理中、且租約到期或擁有者是自己,才改序號/擁有者/到期時間)跟既有 `_dispose()` 的條件式 `UPDATE`(WHERE 帶條件、看 rowcount)是同一套模式,沒看到 execution.py 或對帳邏輯繞過 `InboxStore` 直接下 SQL 的字面。
- 租約擁有者「行程編號加啟動時間」是全新概念,全專案沒有既有的 process-identity 機制可能被它重複或牴觸(`grep getpid/uuid` 無命中),跟 Phase 0 架構筆記的 owner 定義(「紀錄帶 owner 與 lease」)不衝突,只是尚未講清楚這個值怎麼從 `runner.py` 傳進 `Executor`/`InboxStore`,但這是實作細節缺口,不是第二種做法或跨層直呼。
