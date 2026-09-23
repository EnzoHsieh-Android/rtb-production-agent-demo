severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 大量正式紀錄會讓唯讀稽核阻塞所有寫入並超過忙碌逾時
severity: major
blocking: 是 — 30 萬筆近期已驗證紀錄會產生 60 萬次逐鍵查詢，在寫入鎖內耗時超過資料庫的 5 秒等待上限，並行執行工作可能取得 `InboxBusy` 而失敗或延後。

引句:「latest = attempt_store.latest(tx, row.key)  # 目前狀態要另查最新一列」

具體輸入：同一租戶最近一小時有 300,000 把已驗證鍵，稽核範圍涵蓋該小時。這些鍵同時出現在 `passed` 和 `holding`，而 `_entries` 對兩組各呼叫一次，因此執行 600,000 次 `latest()`，且整段位於 `BEGIN IMMEDIATE` 交易內。file: `src/rtb/executor/observability.py:113`、file: `src/rtb/executor/observability.py:122`、file: `src/rtb/executor/inbox_store.py:386`

記憶體 SQLite 重現輸出：

```text
300000 6.600s 300000 300000
```

資料庫寫入者的等待上限只有 5 秒。file: `src/rtb/sqlitekit.py:11`

預期：上述資料量仍應以集合式查詢一次取得最新狀態，且同一把鍵不能因同時屬於 `passed`、`holding` 而重查；可觀測查詢不應讓正常執行交易超時。第 2 輪的 SQLite 參數上限問題雖已消除，但 110 筆測試沒有覆蓋這個新熱路徑。
