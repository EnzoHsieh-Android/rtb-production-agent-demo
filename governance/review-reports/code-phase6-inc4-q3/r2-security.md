severity: major

## F1 只依廣告篩「已核可放行數」時,新索引救不到——仍會在握寫入鎖期間全表掃描 approval_uses
severity: major
blocking: 是 — 破壞合約:[S369] 明講「依租戶、只依時間、只依廣告數已核可放行,查詢計畫應不含全表掃描」,實測只依廣告時查詢計畫對 `approval_uses` 是全表等級的 SCAN,而且這支查詢是在 `store.transaction()`(`BEGIN IMMEDIATE`)開的交易裡跑,握的正是寫入鎖。

引句:「EXISTS (SELECT 1 FROM attempts f WHERE f.seq = 1 AND f.task_id = u.task_id 」

這批修正把「已核可放行數」的廣告篩選從原本 JOIN `write_stops`(有索引)改成 `EXISTS (SELECT 1 FROM attempts f ...)` 子查詢(`src/rtb/executor/inbox_store.py:1034-1053`)。子查詢本身確實吃得到 `attempts_first_rows` 這個部分索引(`f.campaign_id = ?` 走 `SEARCH`),但外層 `FROM approval_uses u` 在只給 `campaign_id` 時,WHERE 子句只剩 `1 = 1 AND EXISTS(...)`——沒有任何條件直接落在 `approval_uses` 自己的欄位上,新加的 `approval_uses_by_tenant`、`approval_uses_by_time`(`src/rtb/executor/inbox_store.py:189-190`)兩個索引完全用不到。

實測(複製 schema 到 `/tmp`,不動 repo):20 萬列 `approval_uses`、20 萬列 `attempts`,只帶 `campaign_id` 呼叫 `approval_use_count_query`:

```
campaign-only filter: (1000,) time: 58.404
plan: [(7, 0, 213, 'SCAN u USING COVERING INDEX sqlite_autoindex_approval_uses_1'),
       (9, 0, 61, 'SEARCH f EXISTS USING INDEX attempts_first_rows (campaign_id=?)')]
```
對照:只帶 `tenant` 或只帶時間範圍都是即時(`SEARCH ... USING COVERING INDEX approval_uses_by_tenant/by_time`,0.000–0.003 秒)。可重現指令見文末。「SCAN u」雖然用的是唯一限制的 covering index(不是真的堆表掃描),但成本一樣是 O(全表列數)——跟停下紀錄表 `PITFALL` 筆記描述的「唯一限制以種類開頭,計畫顯示用了索引、實際同種類全掃」是同一種假象,只是這次發生在 `approval_uses` 的外層查詢,不是停下紀錄表。

更值得注意的是:補的防回歸測試踩了同一個坑而沒被攔下。

引句:「({"campaign_id": "c1"}, "attempts_first_rows"),」

`tests/executor/test_observability.py` 新增的 `test_applied_count_uses_its_index` 對 `campaign_id` 這組參數,只斷言查詢計畫字串裡出現 `attempts_first_rows`(子查詢用得到的索引),沒有斷言外層 `approval_uses` 沒有被全表等級掃描。子查詢用到索引這件事本來就成立,所以測試綠燈,但沒有驗到 S369 真正要保證的東西——查詢計畫「應不含全表掃描」。這正是同一份筆記已經寫過的坑(`docs/rtb-production-agent-demo-knowledge/Systems/可觀測查詢.md` 的 `PITFALL: [2026-09-23] 查詢計畫測試只看「有沒有用索引」會假綠`),但這次新增的測試沒有套用那條教訓,是假綠。

目前程式庫裡還沒有任何呼叫端會用「只給 campaign_id」呼叫 `approval_use_count`/`approval_counts`(僅測試在用,`observability.py` 檔頭註解也寫「現在只有人工與測試會呼叫」),所以現況還不到能被外部觸發的地步;但 S369 把這個篩選組合列為承諾要有索引效力的合約項目,一旦 Phase 9 的告警/儀表板或任何管理工具照著合約單獨用廣告篩這支查詢,或資料量隨時間變大,呼叫端會在寫入鎖裡卡住(20 萬列約 1 分鐘,線性隨列數增加,執行行程期間所有 `accept`/`receive`/確認等寫入都會被這個交易卡住),形同用一支「合約保證的」唯讀查詢就能讓收件口整個停擺——而且沒有任何 REVISIT 或已知限制記在筆記裡提醒未來要收斂(不像 `aggregate_audit` 的無上限範圍那樣有註記與 REVISIT 日期)。

修法建議(不改程式,留給作者):要嘛比照 `awaiting_count` 的做法改成 `LEFT/INNER JOIN write_stops`(有索引)取代 `EXISTS` 子查詢在無其他篩選條件時退化成全表掃描的路徑;要嘛把「只依廣告」這個組合從 S369 的合約承諾裡拿掉,或替 `approval_uses` 加一個以 `campaign` 相關欄位起頭的索引(目前 `approval_uses` 表本身沒有 campaign_id 欄位,只能靠 attempts 反查,所以前一個做法更直接)。

可重現指令(在 `/tmp`,不動 repo):
```
cd /tmp && python3 - <<'EOF'
import sqlite3, time
conn = sqlite3.connect(":memory:", isolation_level=None)
conn.executescript("""
CREATE TABLE approval_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL, task_id TEXT NOT NULL,
    revision INTEGER NOT NULL, content_hash TEXT NOT NULL, key TEXT NOT NULL, tenant TEXT NOT NULL,
    stage TEXT NOT NULL, amount INTEGER NOT NULL, used INTEGER, cap INTEGER,
    capped INTEGER NOT NULL, at TEXT NOT NULL,
    UNIQUE (task_id, revision, content_hash, stage));
CREATE INDEX approval_uses_by_tenant ON approval_uses (tenant, at);
CREATE INDEX approval_uses_by_time ON approval_uses (at);
CREATE TABLE attempts (key TEXT, seq INTEGER, campaign_id TEXT, task_id TEXT, revision INTEGER);
CREATE INDEX attempts_first_rows ON attempts (campaign_id) WHERE seq = 1;
""")
N = 200000
conn.execute("BEGIN")
conn.executemany("INSERT INTO approval_uses (approval_id, task_id, revision, content_hash, key, "
    "tenant, stage, amount, used, cap, capped, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
    ((f"ap{i}", f"t{i}", 1, f"h{i}", f"k{i}", f"tenant{i%50}", "aggregate_limit_reached", 10,
      None, None, 0, f"2026-01-01T00:00:{i%60:02d}.000000Z") for i in range(N)))
conn.executemany("INSERT INTO attempts VALUES (?,?,?,?,?)",
    ((f"k{i}", 1, f"campaign{i%200}", f"t{i}", 1) for i in range(N)))
conn.execute("COMMIT")
q = ("SELECT count(*) FROM approval_uses u WHERE 1 = 1 AND EXISTS (SELECT 1 FROM attempts f "
     "WHERE f.seq = 1 AND f.task_id = u.task_id AND f.revision = u.revision AND f.campaign_id = ?)")
t0 = time.time(); print(conn.execute(q, ("campaign5",)).fetchone(), time.time() - t0)
print(conn.execute("EXPLAIN QUERY PLAN " + q, ("campaign5",)).fetchall())
EOF
```

---

## 其他檢查過、確認沒問題的地方

**SQL 拼接與參數化**:`inbox_store.py` 這批改動新開的兩支查詢組字函式——`stop_count_query`(未改,對照組)與新的 `approval_use_count_query`——都只拼接固定字面(欄位名、比較運算子、`1 = 1`、EXISTS 子查詢的固定骨架),所有篩選值(`tenant`、`campaign_id`、`since`、`until`)一律經 `params` 走 `?` 綁定,逐一核對過 `src/rtb/executor/inbox_store.py:1034-1053` 沒有任何 f-string 把外部值直接嵌進 SQL 文字。`awaiting_count`(`inbox_store.py:929-950`)的重構(把 `AWAITING.replace(' AND ', ' AND p.')` 換成常數 `_AWAITING_P`)也核對過兩者是同一個字面字串,不影響查詢語意或參數化方式。都帶 `# noqa: S608` 且註記「只拼接固定條件」,跟實際程式碼相符。

**握寫入鎖的查詢成本(除 F1 外)**:「只依租戶」與「只依時間」兩種篩選組合,新索引 `approval_uses_by_tenant`(`tenant, at`)、`approval_uses_by_time`(`at`)確實吃得到(見上方 `EXPLAIN QUERY PLAN` 實測,`SEARCH ... USING COVERING INDEX`,20 萬列都是毫秒等級),沒有全表掃描,S369 對這兩個組合的承諾站得住。

**索引寫進建表語句,對舊庫開庫的風險**:`approval_uses_by_tenant`、`approval_uses_by_time` 兩個 `CREATE INDEX IF NOT EXISTS` 直接寫進 `SCHEMA` 常數(`inbox_store.py:189-190`),跟着 `sqlitekit.connect()` 在每次開連線時透過 `executescript` 執行(`sqlitekit.py:23-40`)。因為是 `IF NOT EXISTS`,對已經有 `approval_uses` 表但缺這兩個索引的舊庫重開時不會拋錯、也不需要走 `_migrate_columns` 那條要重建表的路(那條只處理欄位與 CHECK 限制,不處理單純加索引);跟現有 `write_stops_by_tenant` 等索引是同一種既有模式,沒有引入新的失敗路徑。唯一要注意的是建索引本身要掃一次現有列,舊庫資料量大時第一次開庫會變慢(而且跟 `_migrate_columns` 的 `ALTER TABLE`/補索引一樣,若撞上別的連線持有鎖會走 `DatabaseBusy → InboxBusy`,不是靜默失敗、也不是資料損壞),這點屬於維運層面的既有取捨,不是這批修正新增的資安缺口。

**核可使用表反查嘗試紀錄的正確性影響面**:改成看「這份提案第一列嘗試的廣告」、不再接停下紀錄(`inbox_store.py` 的 `approval_use_count_query` docstring 與 `Systems/提案收件口.md` 的改動)是本輪代碼審已經裁定同意的正確性修正,不在資安鏡頭範圍內,但附帶確認它沒有引入新的資料外洩或跨租戶讀取面——`u.tenant` 仍是核可使用表自己記的租戶,`EXISTS` 子查詢只用來確認「這份提案第一筆嘗試對應到指定的廣告」,不會把別租戶、別提案的資料摻進計數。

看過的檔:src/rtb/executor/inbox_store.py、src/rtb/executor/observability.py、tests/executor/test_observability.py、docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md、docs/rtb-production-agent-demo-knowledge/Systems/可觀測查詢.md、docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md
