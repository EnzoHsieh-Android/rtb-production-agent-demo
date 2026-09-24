severity: major

### 1. 端到端收斂後會與第一輪的其他指標拼成矛盾快照
severity: major
blocking: 是
引句:「return Report(_compute_window(since, until, part, analyzer, tenants, current), True,」
file: `src/rtb/ops/metrics.py:797`
file: `src/rtb/ops/metrics.py:801`
file: `src/rtb/ops/metrics.py:807`

觸發情境：第一輪完整執行端快照讀完後，新增一筆窗內終點事件；第二、三輪端到端重讀都看見這筆事件，因而連續兩輪得到相同端到端樣本。

會出什麼錯：程式以第二、三輪相等判定 `stable=True`，但 `_compute_window` 仍使用第一輪的 `part` 與 `analyzer`，只把較新快照算出的 `current` 端到端樣本塞進去。以三輪 stub 實際重現後，報告同時得到 `terminal_event_rate=no_samples`、`execution_seconds=no_samples`，卻有一筆 `end_to_end_seconds=600`，而且標成穩定。也就是同一份報告宣稱沒有任何終點事件，卻已有以該終點算出的端到端延遲，破壞指標快照的自洽性。

建議修法：不可在端到端追上較新執行端快照後，仍沿用第一輪 `part` 計算其他指標。最小修法是固定第一輪執行端輸入，後續重讀只用來確認端到端結果仍與第一輪一致；一旦端到端改變就回報不穩定，不得在後兩輪彼此相等時改採較新結果。另一方案是端到端收斂後，以同一個被接受的執行端快照重算全部執行端指標。應補競態測試：第一輪完整讀取後插入窗內終點，第二、三輪保持不變，斷言不得產生上述 `stable=True` 的矛盾報告。

其餘核對未找到可證實的新錯誤：多次重放、待核可與租約接手的分段起點符合事件順序；等待區間經分段裁切後沒有重複扣除；結束代碼 7 在 repo 內沒有既有呼叫者仍依賴 argparse 的代碼 2；正常由目前寫入開法建立的資料庫具備全部指定索引，不會被缺索引檢查誤擋。指定 pytest 已嘗試執行，但唯讀沙盒沒有可寫的暫存目錄，pytest 在收集測試前即因 `FileNotFoundError: No usable temporary directory` 結束。