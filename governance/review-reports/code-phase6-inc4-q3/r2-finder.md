severity: major

## F1 廣告篩選會把別份內容的舊嘗試算成核可放行
severity: major
blocking: 是 — 查詢未比對內容雜湊或操作鍵，會多算錯誤廣告的放行數，違反 [S362] 的篩選語意。

引句:「EXISTS (SELECT 1 FROM attempts f WHERE f.seq = 1 AND f.task_id = u.task_id」

子查詢只用任務、修訂與廣告連結嘗試，完全沒有使用 `u.content_hash`。然而嘗試紀錄只增不改，收件表的已結案任務則會被清除；因此任務編號與修訂重新使用後，舊內容的第一列嘗試仍會存在。新內容的一筆核可使用紀錄會同時符合新、舊兩個廣告。

file: `src/rtb/executor/inbox_store.py:1045`  
file: `src/rtb/executor/inbox_store.py:524`  
file: `src/rtb/executor/attempt_store.py:3`  
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:348`

重現命令：

```sh
PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c "import sqlite3; from rtb.executor import attempt_store, inbox_store; c=sqlite3.connect(':memory:'); c.executescript(attempt_store.SCHEMA); c.executescript(inbox_store.SCHEMA); q=\"INSERT INTO attempts (key,seq,campaign_id,state,send_count,verification_timeouts,written_at,task_id,revision,action,expected_version,proposal_json) VALUES (?,1,?,'in_flight',1,0,'2026-09-23T00:00:00Z','task-1',1,'update_budget',1,'{}')\"; c.execute(q,('old-key','old-campaign')); c.execute(q,('new-key','new-campaign')); c.execute(\"INSERT INTO approval_uses (approval_id,task_id,revision,content_hash,key,tenant,stage,amount,capped,at) VALUES ('approval-new','task-1',1,'new-hash','new-key','tenant-a','aggregate_limit_reached',10,0,'2026-09-23T00:00:00Z')\"); print('old-campaign:',c.execute(*inbox_store.approval_use_count_query(campaign_id='old-campaign')).fetchone()[0]); print('new-campaign:',c.execute(*inbox_store.approval_use_count_query(campaign_id='new-campaign')).fetchone()[0])"
```

輸出：

```text
old-campaign: 1
new-campaign: 1
```

實際只有 `new-key` 使用核可，舊廣告應為 0。現有測試只覆蓋「同任務、不同修訂」，沒有覆蓋同任務同修訂但內容不同的歷史第一列。

## F2 廣告索引測試假綠，外層仍掃完整核可使用表
severity: major
blocking: 是 — [S369] 明定只依廣告查詢不可全掃；新增測試只尋找任一索引名稱，沒有檢查外層 `approval_uses` 的完整掃描。

引句:「assert re.search(rf"\b{index}\b", _plan(store, sql, params)), _plan(store, sql, params)」

只依廣告篩選時，`attempts_first_rows` 確實出現在相關子查詢，所以斷言會通過；但外層沒有任何 `approval_uses` 條件，SQLite 仍掃過整份核可使用表。新增的兩個索引都沒有被這條查詢使用。

file: `tests/executor/test_observability.py:299`  
file: `tests/executor/test_observability.py:306`  
file: `src/rtb/executor/inbox_store.py:1052`  
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:415`

在上述記憶體資料後執行：

```sh
PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c "import sqlite3; from rtb.executor import attempt_store,inbox_store; c=sqlite3.connect(':memory:'); c.executescript(attempt_store.SCHEMA); c.executescript(inbox_store.SCHEMA); sql,params=inbox_store.approval_use_count_query(campaign_id='c1'); print(*[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,params)],sep='\n')"
```

輸出：

```text
SCAN u USING COVERING INDEX sqlite_autoindex_approval_uses_1
SEARCH f EXISTS USING INDEX attempts_first_rows (campaign_id=?)
```

測試應同時拒絕 `SCAN u`，否則資料量增加後，這支握著全域寫入鎖的可觀測查詢會線性掃描全部核可使用紀錄。
