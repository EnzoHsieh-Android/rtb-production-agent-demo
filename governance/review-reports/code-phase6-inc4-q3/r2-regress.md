severity: clean

## 逐項驗證

**1. 已核可放行數依廣告篩改看「提案第一列嘗試的廣告」:第一列是否一定存在**

`src/rtb/executor/execution.py:696` 的 `record_approval_use` 只在 `_audit`(`execution.py:667-669`)被呼叫,而 `_audit` 只在 `begun.created` 為真時才呼叫,`begun` 來自同一個交易裡剛執行的 `attempt_store.begin(tx, ...)`(`execution.py:649`)。`begin()`(`src/rtb/executor/attempt_store.py:329-368`)在 `created=True` 的唯一路徑就是「這把鍵目前沒有任何列」,並在回傳前先 `INSERT INTO attempts (... seq ...) VALUES (?, 1, ...)`(`attempt_store.py:357-366`)寫下 seq=1 那列。也就是說,`record_approval_use` 一定在「這把鍵剛剛才被同一個交易插入 seq=1」之後才會被呼叫,兩個寫入在同一個 `BEGIN IMMEDIATE` 交易(`execution.py:628` 的 `with self.store.transaction() as tx:`)裡,要嘛一起提交、要嘛一起回滾。正式流程沒有「核可使用紀錄存在但第一列缺席」的視窗。

`record_approval_use` 全庫只有這一個呼叫點(`grep record_approval_use` 只命中 `execution.py:696` 與定義處),所以不存在繞過這條路徑直接寫核可使用紀錄的管道。

「舊資料、補欄位」的疑慮:`approval_uses` 表是 Phase 6 增量 3 才新開的表(`inbox_store.py:183-190` 的 `CREATE TABLE IF NOT EXISTS approval_uses`),在這次改動之前完全沒有資料,不存在「舊核可使用紀錄對不上新版第一列格式」的相容問題;`attempts.task_id`/`revision` 雖然在 schema 裡沒標 `NOT NULL`(`attempt_store.py:50`),但 `begin()` 寫入時一定帶入 `proposal.task_id`/`proposal.revision` 兩個非空值(`attempt_store.py:363-364`),不會是空值。

結論:這條在目前的寫入路徑下成立,沒發現反例。

**2. 同一份提案在嘗試紀錄裡有沒有可能有多把鍵的第一列(重複計數或錯廣告)**

`approval_use_count_query` 的廣告篩是相關子查詢 `EXISTS (SELECT 1 FROM attempts f WHERE f.seq = 1 AND f.task_id = u.task_id AND f.revision = u.revision AND f.campaign_id = ?)`(`inbox_store.py:238-241`),用 `EXISTS` 而非 `JOIN`,即使某個 `(task_id, revision)` 理論上對到多列,也只會判斷「存在/不存在」,不會讓外層 `count(*)` 重複計數。

而 `(task_id, revision)` 對到多把鍵(多個不同 `operation_key`)在現有寫入路徑下也不會發生:`operation_key`(`src/rtb/domain/attempt.py:20-34`)明白排除 `revision` 不參與雜湊,但收件口 `_accept_in_transaction` → `_check_revision_and_times`(`inbox_store.py:329-336`)要求 `proposal.revision == highest + 1`(逐一遞增),同一個 `task_id` 的同一個 `revision` 全庫只會被 accept 一次、對應唯一一份提案內容,因此 `(task_id, revision)` 在 `attempts` 表裡最多對到一把 `seq=1` 的鍵。用實驗驗證過此不變量成立(見下方第 5 節的變異測試,反向改動後這條路徑立刻翻紅)。

結論:設計上不會重複計數或算錯廣告。

**3. 兩個新索引:對既有資料庫是否也會補上;查詢計畫是否真的用上**

`approval_uses_by_tenant`、`approval_uses_by_time` 兩個索引寫在 `SCHEMA` 字串裡(`inbox_store.py:145-146`),而 `SCHEMA`(含 `attempt_store.SCHEMA`)是 `InboxStore.__init__` 每次開連線都會透過 `sqlitekit.connect()` 執行的 `executescript`(`inbox_store.py:369`、`src/rtb/sqlitekit.py:23-38`),兩條都是 `CREATE INDEX IF NOT EXISTS`,所以不論資料庫是不是舊的(有 `approval_uses` 表但沒這兩個索引),重新開啟 `InboxStore` 時一定會補上,不需要額外的補欄位/遷移判斷(這點跟 `attempts_first_rows_by_tenant` 需要靠 `_migrate_columns()` 額外判斷不同,因為後者依賴後補的 `tenant` 欄位,前兩者所在的 `approval_uses` 表本身沒有後補欄位的問題)。用 `tests/executor/test_observability.py` 裡 `test_an_old_database_gains_the_observability_indexes`、`test_an_old_attempts_table_gains_the_tenant_index_after_the_column` 兩個測試(先 `DROP INDEX` 再重開)驗證過,行為與程式碼一致。

查詢計畫確有用上:`test_applied_count_uses_its_index`(`tests/executor/test_observability.py:359-368`)對租戶篩、租戶+時間篩斷言 `approval_uses_by_tenant`,純時間篩斷言 `approval_uses_by_time`,廣告篩斷言用到 `attempts_first_rows`(`attempt_store.py:52` 的既有部分索引 `ON attempts (campaign_id) WHERE seq = 1`)。實跑 `EXPLAIN QUERY PLAN` 通過(見下方測試結果)。

結論:既有資料庫會自動補上兩個索引,查詢計畫確實用得上。

**4. 待核可條件改寫後跟原本常數語意是否一致**

舊寫法在兩處各自用 `f"p.{AWAITING.replace(' AND ', ' AND p.')}"`(`AWAITING = "state = 'pending' AND disposition = 'awaiting_approval'"`)把常數字串硬套別名前綴;新寫法把結果直接寫成常數 `_AWAITING_P = "p.state = 'pending' AND p.disposition = 'awaiting_approval'"`(`inbox_store.py:123`)。逐字比對兩種寫法在 `AWAITING` 現有內容下的展開結果完全相同,`awaiting_count` 兩處引用(`inbox_store.py:169`、`180`)都同步換成 `_AWAITING_P`,語意不變。

唯一要留意的是:`_AWAITING_P` 現在是獨立字面值,不再用字串代換從 `AWAITING` 推導,如果之後有人改了 `AWAITING` 的內容(例如加新條件)、忘記同步改 `_AWAITING_P`,兩處會悄悄不一致。這是維護面的風險而非目前這批 diff 造成的錯誤行為,程式裡已有註解提醒「同一句,帶別名」,且 `AWAITING`/`PENDING`/`IN_PROGRESS` 這組常數本來就不常變。不到 blocking 的程度,列為觀察而非 `## F` 發現。

**5. 新測試是否對症狀翻紅(變異實驗)**

在 `/tmp/rtb3b-mut`(複本,全程用 `git -C /tmp/rtb3b-mut`)把 `src/rtb/executor/inbox_store.py`、`src/rtb/executor/observability.py` 還原到修正前一個提交(`0c7834b`,只留這批修正要修的兩支程式檔,測試維持這批修正後的版本),重跑 `tests/executor/test_observability.py`:

```
8 failed, 24 passed
FAILED test_applied_count_uses_its_index[filters0-approval_uses_by_tenant]
FAILED test_applied_count_uses_its_index[filters1-approval_uses_by_tenant]
FAILED test_applied_count_uses_its_index[filters2-approval_uses_by_time]
FAILED test_applied_count_uses_its_index[filters3-attempts_first_rows]
FAILED test_an_old_attempts_table_gains_the_tenant_index_after_the_column[no_tenant_column]
FAILED test_an_old_attempts_table_gains_the_tenant_index_after_the_column[column_but_no_index]
FAILED test_an_old_database_gains_the_observability_indexes
FAILED test_approval_counts_cover_waiting_and_applied
```

`test_approval_counts_cover_waiting_and_applied` 的失敗訊息是 `applied: 1 != 2`(campaign_id="c5" 篩選那筆),對應的正是這批修正要解的原始症狀(比例那一關核可先簽好、沒有停下紀錄時舊寫法漏算);索引相關 4 支測試因為 `approval_use_count_query` 這個函式在舊版根本不存在而失敗,連帶把「兩個新索引」與「補欄位補索引」的迴歸也一起蓋住。確認新測試組確實會對症狀翻紅,不是裝飾性斷言。

另外兩個新測試 `test_applied_count_follows_the_tenant_recorded_when_the_approval_was_used`、`test_settled_awaiting_proposals_stop_counting` 在還原前一版程式碼時仍然通過(不翻紅)。前者是因為舊版 `approval_use_count` 本來就是直接篩 `u.tenant`(核可使用紀錄自己記的租戶),不是取自停下紀錄的租戶,這支測試驗的是「不要退回去用停下紀錄的租戶」這個被否決方案的迴歸,跟這批修正本身要修的 bug 不是同一個路徑,所以對這次修正無翻紅義務,是面向未來設計決策的迴歸測試;後者驗的是 `awaiting_count` 在提案結案後不再計入待核可,是既有行為(增量 3 就有)的補充覆蓋,跟這批 diff 無關,不影響本次修正的翻紅判斷。

在 HEAD(修正後)完整跑 `tests/executor/` 全部 503 個測試,全部通過(52.8 秒)。

## 未對上的地方

沒有發現。圖譜(`docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`、`.../可觀測查詢.md`、計劃節點增量 4)裡對這批修正的描述(第一列嘗試的廣告、不接停下紀錄、兩個索引在建表語句裡建)跟程式碼實際行為一致,沒有互相矛盾之處。
