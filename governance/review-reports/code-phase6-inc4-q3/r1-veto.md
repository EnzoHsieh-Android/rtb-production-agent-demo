severity: major

## F1 廣告篩選會跨租戶接上舊停下紀錄
severity: major
blocking: 是 — 會把另一租戶留下的停下紀錄接到核可使用列，正式查詢因此多算

引句:「JOIN write_stops w ON w.task_id = u.task_id AND w.revision = u.revision」

`approval_use_count` 的 join 比對任務、修訂、內容雜湊與關卡，卻漏了主線要求的租戶欄：`src/rtb/executor/inbox_store.py:964`。這在租戶設定變更時是可達狀態：處理待核可會重新按目前設定取得租戶，核可真正生效時也把目前租戶寫進使用紀錄，見 `src/rtb/executor/execution.py:531`、`src/rtb/executor/execution.py:689`；但原停下紀錄受 `(kind, task_id, revision, content_hash)` 唯一限制保護且只增不改，仍保留舊租戶，見 `src/rtb/executor/inbox_store.py:167`、`src/rtb/executor/inbox_store.py:692`。

具體輸入：提案 `t1/1/h` 在 `old-tenant` 的比例關卡停下，停下列的廣告是 `c1`；等待期間 `c1` 改歸 `new-tenant`，重新核可並放行後，核可使用列記成 `new-tenant`。查詢 `tenant="new-tenant", campaign_id="c1"` 時，現行 SQL 跨租戶接上舊停下列並回 `applied=1`；依四欄對照要求，租戶不符時不得用該停下列證明廣告歸屬，預期是 `0`。

重現命令：

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'import sqlite3; c=sqlite3.connect(":memory:"); c.executescript("CREATE TABLE approval_uses(task_id TEXT,revision INTEGER,content_hash TEXT,tenant TEXT,stage TEXT,at TEXT); CREATE TABLE write_stops(id INTEGER,task_id TEXT,revision INTEGER,content_hash TEXT,tenant TEXT,campaign_id TEXT,kind TEXT); INSERT INTO write_stops VALUES(1,\"t1\",1,\"h\",\"old-tenant\",\"c1\",\"budget_increase_too_large\"); INSERT INTO approval_uses VALUES(\"t1\",1,\"h\",\"new-tenant\",\"budget_increase_too_large\",\"2026-09-23T00:00:00Z\");"); sql="SELECT count(*) FROM approval_uses u JOIN write_stops w ON w.task_id=u.task_id AND w.revision=u.revision AND w.content_hash=u.content_hash AND w.kind=u.stage WHERE u.tenant=? AND w.campaign_id=?"; print("actual",c.execute(sql,("new-tenant","c1")).fetchone()[0]); print("expected_with_four_field_match",c.execute(sql.replace(" AND w.kind=u.stage", " AND w.kind=u.stage AND w.tenant=u.tenant"),("new-tenant","c1")).fetchone()[0])'
```

輸出：

```text
actual 1
expected_with_four_field_match 0
```
