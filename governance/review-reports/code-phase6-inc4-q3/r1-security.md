severity: major

# 資安審查(席名:資安-sonnet)

## F1 `approval_use_count` 在最主要的呼叫形狀下對 `approval_uses`(無上限成長表)做全表掃描,同時握著執行行程唯一的寫入鎖

severity: major
blocking: 是 — 常見呼叫形狀(只給租戶、或只給時間範圍)會在握著整個執行行程唯一寫入鎖(`BEGIN IMMEDIATE`)期間對一張沒有保留期/沒有列數上限的表做全表掃描,阻塞所有租戶的寫入;而且這條路徑完全沒被本增量原本用來釘查詢計畫的機制([S369]/`test_observability_queries_use_their_indexes`)覆蓋到。

前提:`approval_uses` 是核可使用的耐久紀錄表,只增不刪(`record_approval_use` 只有 `INSERT OR IGNORE`,收件口模組沒有任何清除/保留期邏輯管它,對照 `proposals` 有 `MAX_ROWS = 5000` 與 `RETENTION` 兩層上限——`src/rtb/executor/inbox_store.py:46-47`)。這張表只有一個索引,是 `(task_id, revision, content_hash, stage)` 的隱含唯一索引(建表語句本身的欄位組合限制),沒有任何以 `tenant` 或 `at` 開頭的索引。

動作:`InboxStore.approval_use_count`(新增,見引句)用 `tenant`、`since`、`until` 過濾時,SQL 是:

引句:「JOIN write_stops w ON w.task_id = u.task_id AND w.revision = u.revision」

——但這段 JOIN 只在 `campaign_id is not None` 時才接上(`join = ("" if campaign_id is None else ...)`),也就是只給 `tenant`、或只給 `since`/`until`(不給 `campaign_id`)時,整條查詢就是單純 `SELECT count(*) FROM approval_uses u WHERE u.tenant = ? ...`,完全沒有可用索引。用實際的 SQLite 連線(同表結構、同索引)跑 `EXPLAIN QUERY PLAN` 重現:

```
$ /Users/enzo/rtb-production-agent-demo/.venv/bin/python - <<'EOF'
sql = "SELECT count(*) FROM approval_uses u WHERE u.tenant = ? AND u.at >= ? AND u.at < ?"
# EXPLAIN QUERY PLAN 結果:
(3, 0, 216, 'SCAN u')
EOF
```
只給 `tenant`、只給時間範圍、或兩者都給但不給 `campaign_id`,計畫都是 `SCAN u`(全表掃描),跟 `campaign_id` 有給時會走 `write_stops_by_campaign` 索引再回頭用主鍵索引查 `approval_uses` 的計畫(`SEARCH u USING COVERING INDEX`)形成明顯落差。而 `approval_counts`(`src/rtb/executor/observability.py:101-107`)呼叫 `approval_use_count` 時預設就是不帶 `campaign_id`(依租戶篩是設計文件明講的主要篩選維度之一:「依租戶、廣告篩」),所以「只依租戶查」「只依時間範圍查」這兩種最常見的呼叫形狀,天天都會走這條全表掃描路徑。

可觀察效果:`InboxStore.transaction()` 進入時是 `BEGIN IMMEDIATE`(`src/rtb/sqlitekit.py:43-56`),SQLite 的保留鎖在提交前排他阻擋所有其他寫入者(收件、取件、記嘗試紀錄……全部要等)。`approval_use_count` 在這個交易裡執行,掃描成本正比於 `approval_uses` 的總列數——這張表沒有 `proposals` 那種 `MAX_ROWS`/`RETENTION` 上限,會隨系統存活時間單調成長(每一次核可放行都留一列,永久保存,用於稽核)。專案裡對照的先例是嘗試紀錄表的 `aggregate_used`「還沒結案」段全表掃描(100 萬列約 0.9 秒、握寫入鎖),那條路徑有專門的 RULE/PITFALL/REVISIT(`2027-01-31`)與明確數字記在 [[Systems/可觀測查詢]];`approval_use_count` 的這條全表掃描路徑目前沒有任何對應的風險紀錄、沒有索引、也不在 [S369] 釘住的查詢計畫測試清單裡(`tests/executor/test_observability.py:277-296` 的 `test_observability_queries_use_their_indexes` 只覆蓋 `stop_count_query` 與 `first_rows_started_query`,不含 `approval_use_count`/`awaiting_count`)。

為什麼要緊:這次增量原本已經為同一個查詢三的姊妹查詢(`stop_count`/`write_stops`)特地加了三支索引並用 [S369] 釘住查詢計畫,標準明確存在;`approval_use_count` 是同一個增量、同一輪(r1–r3)之後補進來的查詢,卻沒有經過同樣的索引/鎖審查(計劃筆記自己寫「查詢三與 [S362] 等增量 3 合進 main 後補」,繞過了 r1–r3 三輪設計審)。一旦 Phase 9 接上定時輪詢或告警(這正是計劃筆記寫的回頭條件觸發點),或是人工儀表板常態地「查某租戶這個月已核可放行數」,`approval_uses` 表隨時間長大後,這支查詢會讓整個執行行程的寫入路徑(收件、取件、記錄嘗試)在鎖等待中停頓,時間隨表列數線性成長、沒有上限——這是文件裡「防忘記」這一類風險,現在就是被忘記的那一個。

補充驗證(用同一份 patch 凍結的程式碼、同樣的建表 DDL,在 `/tmp/qplan_test/repo` 這個唯讀複本上跑,沒有修改 `/Users/enzo/rtb-3b` 任何檔案):`awaiting_count` 的兩段查詢裡,「未知租戶」那一段(`w.id IS NULL`,永遠不帶篩選條件)雖然也固定是 `SCAN p`,但 `proposals` 表受 `MAX_ROWS = 5000` 上限保護(`src/rtb/executor/inbox_store.py:46`),掃描成本有界,不構成同等風險。

## 檢查過、沒發現洞的部分

- **注入**:兩支新方法(`awaiting_count`、`approval_use_count`)裡所有 SQL 字串內插的都是模組常數(`AWAITING`)或寫死的子句字面值(如 `"w.tenant = ?"`),值一律經 `?` 佔位符與 `params` 綁定傳入,沒有任何呼叫端字串被直接接進 SQL 文字。`join` 變數只依 `campaign_id is None` 這個布林值二選一(兩個都是寫死字串),不是依 `campaign_id` 的值組字串。沒有注入面。
- **交易歸屬核對能不能繞過**:兩支新方法都在最前面呼叫 `self._own(tx)`,行為與既有的 `stop_count`/`stops` 一致;新增的測試 `test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox` 也明確驗過這兩支方法在別的收件表開的交易上會丟 `NotInTransaction`(引句:「store.awaiting_count(tx)」出現在 `pytest.raises(attempt_store.NotInTransaction)` 區塊裡),沒有繞過路徑。
- **跨租戶待核可/核可資料外洩**:`awaiting_count` 回傳的「未知租戶」份數(`unknown`)確實不受呼叫端傳入的 `tenant`/`campaign_id` 篩選(SQL 沒有套用篩選條件),乍看像是把別租戶的資料混進篩選後的結果——但這是 [S362] 合約明文要求的行為(「接不到停下紀錄的應列進未知租戶份數」),且 `test_approval_counts_cover_waiting_and_applied` 有逐案驗證(例如 `tenant="nobody"` 仍回 `unknown=1`),回傳的只是一個聚合計數、不帶任何可識別其他租戶身分的欄位,屬於設計已知且已審過的取捨,不算實作疏漏,不列為發現。
- 其餘篩選路徑(`awaiting_count` 給 `tenant`/`campaign_id` 時)實測走 `write_stops_by_tenant`/`write_stops_by_campaign` 索引再以 `proposals` 主鍵查回,計畫正常,沒有全表掃描問題。
