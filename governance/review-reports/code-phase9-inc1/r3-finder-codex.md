severity: major

### 1. 提交後清單刪除前被中斷，收尾補寫會把同一批呼叫紀錄重寫
severity: major
blocking: 是

引句:「清單拿掉,中間被打斷只可能是行程當機。」

file: `src/rtb/executor/execution.py:468`  
file: `src/rtb/executor/execution.py:474`  
file: `src/rtb/executor/runner.py:97`  
file: `src/rtb/executor/runner.py:100`  
file: `src/rtb/executor/attempt_store.py:68`

觸發情境：`flush_calls()` 的 SQLite 交易已成功提交，但程序在執行 `del self._pending[:len(waiting)]` 前收到 `SIGINT`，Python 因而拋出 `KeyboardInterrupt`。這不是只會讓行程立即消失的當機；例外會展開堆疊並執行 `_serve()` 的 `finally`。

會出什麼錯的行為：已提交的項目仍留在 `_pending`，收尾再次呼叫 `flush_calls()`，把同一批紀錄重新 `INSERT`。`dsp_calls` 只有自增主鍵，沒有穩定的呼叫識別碼或唯一限制可以去重，因此追蹤與後續 DSP 呼叫次數、錯誤率、延遲統計都會重算。第 2 輪已指出的「提交後刪清單前被打斷會重寫」仍未修乾淨。

建議修法：每次 DSP 呼叫在加入待寫清單時產生穩定且唯一的 `call_id`，資料表為它建立唯一限制，補寫使用冪等插入；如此不論正常補寫、例外展開或收尾重試，都只會留下同一列。另補一支故障注入測試，在交易提交後、刪除 `_pending` 前拋出 `KeyboardInterrupt`，再執行收尾補寫並斷言沒有重複列。