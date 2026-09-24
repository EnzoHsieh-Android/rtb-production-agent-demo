severity: major

## 發現 1:租約時間讀得出來卻解析失敗時，忙碌重試會讓執行行程異常退出
severity: major
blocking: 是

引句:「return datetime.fromisoformat(row[0])」

`lease_until` 只把 SQLite 查詢錯誤轉成 `None`，沒有處理時間欄位的解析錯誤，見 `src/rtb/executor/inbox_store.py:1306`、`src/rtb/executor/inbox_store.py:1316`。DSP 已回覆、結果交易開不起來時，這個錯誤會從重試判斷冒出；啟動迴圈只接 `InboxBusy` 與 `ExecutorHalted`，見 `src/rtb/executor/runner.py:76`、`src/rtb/executor/runner.py:83`。

具體例子：收據欄位全都相符，但 `lease_until` 是無效時間字串；DSP 已寫入，結果交易遇忙碌 → 預期讀不到可用期限便停止重試，照舊拋出忙碌，留待對帳 → 實際拋出未處理的 `ValueError`，執行行程異常退出。應把無效或不帶時區的租約時間視為不可重試。

記憶體 SQLite 重現，沒有修改專案或建立臨時檔：插入相符收據與 `lease_until='invalid-lease'` 後呼叫 `lease_until(receipt)`，輸出為 `receipt_matches= 1`，接著在 `datetime.fromisoformat` 拋出 `ValueError: Invalid isoformat string: 'invalid-lease'`。

### 第 1 輪驗收

| 上輪問題 | 驗收結果 |
|---|---|
| 第一列 `written_at` 型別異常時，快路徑放過原算法會拒絕的資料 | 已修。候選列的 `written_at` 與 `key` 非文字時會退回原算法，見 `src/rtb/executor/attempt_store.py:641`；二進位與整數時間的對照案例見 `tests/executor/test_aggregate_fast_path.py:146`。 |

1 條,blocking 1。