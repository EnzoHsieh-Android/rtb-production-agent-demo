severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 稽核明細在大量有效預留時超過 SQLite 參數上限

severity: major

blocking: 是 — 違反 S367；有效預留數量沒有上限，但查詢五會在達到 SQLite 參數上限時直接失敗，無法產出總曝險稽核明細。

引句:「marks = ", ".join("?" for _ in wanted)」

`aggregate_audit` 先收集全部目前佔額度的鍵，再由 `counted_first_rows` 把每把鍵展開成單一 `IN (?, ?, …)` 查詢。最近 24 小時內的已驗證預留沒有筆數上限，因此鍵數可能超過 SQLite 的變數上限。

file: `src/rtb/executor/observability.py:108`

file: `src/rtb/executor/observability.py:111`

file: `src/rtb/executor/attempt_store.py:450`

file: `src/rtb/executor/attempt_store.py:453`

重現命令：

```sh
PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'import sqlite3; from rtb.executor import attempt_store as a; c=sqlite3.connect(":memory:"); c.executescript(a.SCHEMA); c.execute("ALTER TABLE attempts ADD COLUMN tenant TEXT"); c.execute("ALTER TABLE attempts ADD COLUMN reserved_amount INTEGER"); c.execute("BEGIN IMMEDIATE"); tx=a.ExecutorTransaction(c,a._EXECUTOR_TRANSACTION_ISSUER); print("limit",c.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER));
try: a.counted_first_rows(tx,"t",[str(i) for i in range(c.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)+1)])
except Exception as e: print(type(e).__name__,str(e))'
```

輸出：

```text
limit 250000
OperationalError too many SQL variables
```

應避免按全部鍵展開佔位符，例如直接讓額度明細查詢帶回稽核所需身分，或把鍵分成不超過連線變數上限的批次查詢。

上一輪要求的交易歸屬核對、不交出資料庫連線，以及「目前佔額度」逐筆帶任務、修訂、廣告與舊列標記，都已在凍結版本落實，未重報。
