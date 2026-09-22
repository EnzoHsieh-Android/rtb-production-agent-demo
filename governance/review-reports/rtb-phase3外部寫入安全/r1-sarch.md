severity: major

### 1. 嘗試紀錄表要不要開第二個 SQLite 模組,沒有對齊 Phase0 已定的「同一段」決定

severity: major
blocking: 是 這決定書寫進去的資料庫檔案結構會直接決定增量 1 的 schema 與之後呼叫端怎麼連線,一旦定案錯了,增量 2、3 的呼叫路徑要跟著重寫。

引句:「兩張表,放在執行行程自己的資料庫檔」

說明:快照把嘗試表、轉換紀錄表放進一個新的、獨立的模組(「回退就是刪掉新增的模組」暗示這是全新檔案/模組,不是擴充既有的 `inbox_store.py`),但沒有交代這個「自己的資料庫檔」是不是跟 `InboxStore` 開的同一個 db 檔、同一個 schema。專案現有三個行程(DSP、執行行程、分析行程)各自只有**一個** SQLite 模組管一個資料庫檔、把所有相關表放進同一份 `SCHEMA` 字串、用同一個 `connect()`(`src/rtb/executor/inbox_store.py` 的 `proposals`+`inbox_events`、`src/rtb/analyzer/task_store.py` 的 `tasks`+`evidence`+`tool_calls`、`src/rtb/dsp/store.py` 的 `campaigns`+`operations`+`idempotency_keys`+`metrics`);`InboxServer`/`DSPServer` 啟動也都只吃一個 `--db` 參數。目前沒有任何一個行程開過第二個 SQLite 模組連自己的第二份資料。更關鍵的是 Phase0 架構已經明講「嘗試中」紀錄跟提案佇列同屬執行行程這一段:「在執行行程,這把 lease 同時管提案佇列的訊息與「嘗試中」紀錄,同一段內不存在兩把」「Queue:有兩段,各在自己行程的資料庫裡」。快照完全沒有引用或討論這條既定架構,就把嘗試紀錄表當成全新獨立的東西設計,若最後真的開出第二個模組/第二個檔案,就是引入專案原本沒有的「一行程多 SQLite 模組」做法,且跟 Phase0 的既定決定不一致。

file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md:180`
file: `src/rtb/executor/inbox_store.py:31`
file: `src/rtb/executor/inbox_server.py:117`

### 2.「現況列+只增不改事件表」的說法名不符實,實際上是拼出第三種狀態儲存做法

severity: major
blocking: 是 這決定表結構(要不要維護兩張表互相同步)與轉換寫入路徑,影響增量 1 的合約 S4~S9 怎麼實作,錯了要重寫核心資料層。

引句:「比照分析行程 commit_step 的序號核對」

說明:快照聲稱嘗試紀錄表「沿用[[Systems/提案收件口]]『現況列加只增不改事件表』的做法」,但收件口的 `inbox_events` 其實是**捷選、有上限、去重**的拒收事件記錄(只記固定幾種拒收代碼、每種代碼最多留 `MAX_EVENTS_PER_CODE=200` 筆、同一事件連續重複只留一筆),不是每一次合法狀態轉換都留痕——收件口自己的節點也寫明「事件表是盡力而為的紀錄,不是稽核依據」。而快照的合約 S9 要求「每一次狀態轉換應在同一個交易內留下一筆只增不改的轉換紀錄」,且未見任何上限或清理規則,這其實正是 `src/rtb/analyzer/task_store.py` 的 `commit_step` 已經免費提供的效果:用「序號比對現在最新一列」做樂觀鎖、新增一列即完成狀態推進,不需要另外一張現況表加一張事件表。快照第 72 行也自承轉換的寫入邏輯是「比照分析行程 commit_step 的序號核對」——換句話說,快照要的行為(全量、永久、按交易留痕的轉換史)在精神上更接近分析行程的整張只增不改歷史表,卻選擇借用收件口的「現況列+捷選事件表」的**外殼**,拼出一個兩邊都不是、專案原本沒有的第三種狀態儲存做法(現況列可覆寫 + 無上限全量事件表)。快照沒有討論過為什麼不直接照 `task_store.commit_step` 的純附加版本化列做,這個選擇的理由不成立。

file: `src/rtb/analyzer/task_store.py:202`
file: `src/rtb/executor/inbox_store.py:26`
