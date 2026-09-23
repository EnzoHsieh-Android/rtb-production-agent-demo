severity: clean

這批修正在寫法上跟專案既有的一致,沒有引入第二種做法,也沒有跨層直呼。

逐項對照:

- **module-level query 函式 + instance method 轉呼叫**:新增的 `approval_use_count_query()`(`src/rtb/executor/inbox_store.py:1027`)回傳 `(sql, params)` tuple,`approval_use_count` 方法只是呼叫它再執行,這跟既有的 `stop_count_query()` / `stop_count()` 那一組(`inbox_store.py:1017` 起)完全同一種組法——module 級函式給測試看查詢計畫,instance method 負責用自己開的連線執行。改動前 `approval_use_count` 是唯一一支把 SQL 直接寫在方法體內、沒有拆出 query 函式的方法,這次改法反而是把它拉齊到跟 `stop_count_query` 一樣的既有模式,不是新樣式。

- **`_AWAITING_P` 別名常數**:`awaiting_count`(`inbox_store.py:929` 起)本來用 `AWAITING.replace(' AND ', ' AND p.')` 這種字串代換臨時拼出帶別名版本,這次抽成模組常數 `_AWAITING_P`(`inbox_store.py:151`),跟 `PENDING`/`IN_PROGRESS`/`AWAITING`/`OPEN` 那組常數放在同一段、同樣「一句話定義、到處引用」的寫法,沒有引入新機制;`AWAITING` 本體與既有呼叫處(`inbox_store.py:765`、`829`)都沒動。

- **跨模組讀 `attempts` 表**:`approval_use_count_query` 用 `EXISTS (SELECT 1 FROM attempts f WHERE f.seq = 1 AND f.task_id = u.task_id AND f.revision = u.revision AND f.campaign_id = ?)`(`inbox_store.py:1045`)直接在 `inbox_store.py` 裡碰 `attempts` 表(該表由 `attempt_store.py` 建表與擁有,`attempt_store.py:46`)。這不是新先例——同一支檔的 `awaiting()` 方法(`inbox_store.py:734`)本來就有 `OR EXISTS (SELECT 1 FROM attempts f WHERE f.task_id = p.task_id AND f.seq = 1))`,一樣是收件口模組直接下 SQL 查 `attempts` 表、不透過 `attempt_store` 的函式。兩處用的都是 `attempts` 的 `seq = 1`(第一列)這個既有慣用法,且 `approval_uses` 表本身沒有 `campaign_id` 欄(它的欄位是 tenant/task_id/revision/content_hash/stage,見 `inbox_store.py:183`),要依廣告篩就一定得靠這個既有的跨表查法,不是可用 join 自身欄位解決卻繞道跨層。

- **索引宣告位置**:新增的 `approval_uses_by_tenant`、`approval_uses_by_time`(`inbox_store.py:189-190`)直接寫在 `SCHEMA` 字串裡、緊接在 `approval_uses`建表語句之後,跟 `write_stops` 的三個索引(`inbox_store.py:174-176`,建表語句後直接列索引)同一種組法,不是另外開 migration 或在別處動態補建。

- **`observability.py` 回傳型別**:`ApprovalCounts` 從 `NamedTuple` 改成 `@dataclass(frozen=True)`(`observability.py` 對應 hunk),這反而是跟同檔案裡 `Utilization`、`Stopped`、`Entry`、`AggregateAudit` 這幾個既有回傳型別(全部是 `@dataclass(frozen=True)`)對齊,修正掉原本 `ApprovalCounts` 是全檔唯一一個 `NamedTuple` 的不一致,不是引入新型別系統。`observability.py` 本身仍然沒有寫任何 SQL,`approval_counts()` 只呼叫 `store.awaiting_count` / `store.approval_use_count`(既有依賴方向「observability → inbox_store/attempt_store」沒變)。

- **收件口模組讀 `attempts` 的先例**在筆記 `docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md` 裡沒有另外點名記過(该篇只記了 `awaiting_count`、`approval_use_count` 這兩支方法本身),但程式碼裡的 `awaiting()` 先例是查得到、可重跑的事實依據,不靠筆記背書。

機械反查列出的 `tests/executor/test_aggregate_limit.py`(純新增 hunk)與 `test_an_approval_that_expires_mid_flight_lets_nothing_through`(`tests/executor/test_approval.py:341`)兩個命中經確認都不呼叫這次改到的 `approval_use_count`、`awaiting_count`、`ApprovalCounts`、`approval_use_count_query`,也不依賴 `AWAITING`(只用到未改動的 `AWAITING` 本體,行為不變),跟這批修正無關。
