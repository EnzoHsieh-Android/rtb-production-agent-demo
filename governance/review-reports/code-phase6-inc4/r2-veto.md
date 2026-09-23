severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 目前佔額度筆數超過 SQLite 參數上限時稽核查詢會崩潰
severity: major
blocking: 是 — 合法的正式資料量會令總曝險稽核完全無法產出，破壞 S367、S368。

引句:「marks = ", ".join("?" for _ in wanted)」

`aggregate_audit` 把 `aggregate_holdings` 的全部鍵一次交給 `counted_first_rows`；後者為每把鍵產生一個 SQL 綁定參數，沒有分批或直接 JOIN。未結案雖最多 20 把，但 24 小時內已驗證的紀錄沒有筆數上限。

具體輸入：同一租戶在目前 24 小時窗口內有 250,001 筆已驗證、預留金額大於零的嘗試。預期回傳 250,001 筆 `holding`，且其金額加總成 `utilization.used`；實際在讀取明細時拋出 `sqlite3.OperationalError`。

file: `src/rtb/executor/observability.py:111`  
file: `src/rtb/executor/attempt_store.py:450`

重現命令：

```sh
PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'import sqlite3; from rtb.executor import attempt_store; c=sqlite3.connect(":memory:"); c.execute("CREATE TABLE attempts (key TEXT, seq INTEGER, task_id TEXT, revision INTEGER, campaign_id TEXT, written_at TEXT, tenant TEXT, reserved_amount INTEGER, action TEXT, proposal_json TEXT)"); c.execute("BEGIN IMMEDIATE"); tx=attempt_store.ExecutorTransaction(c, attempt_store._EXECUTOR_TRANSACTION_ISSUER); print("limit", [x[0] for x in c.execute("pragma compile_options") if "MAX_VARIABLE_NUMBER" in x[0]][0]);
try: attempt_store.counted_first_rows(tx, "acct", {str(i) for i in range(250001)})
except Exception as e: print(type(e).__name__ + ":", e)'
```

輸出：

```text
limit MAX_VARIABLE_NUMBER=250000
OperationalError: too many SQL variables
```

應改成不受綁定參數數量限制的資料庫內 JOIN，或將鍵分批查詢後維持既定排序與加總語意。

第 1 輪要求的交易歸屬核對、移除新模組內的 SQL，以及 `holding` 補齊任務、修訂、廣告、狀態與舊列標記，均已在凍結材料中看到對應修正，未重報。
