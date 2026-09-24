# code-phase9-inc2 第 2 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 5 席收齊才判讀:regress、tests、arch、資安(sonnet),finder(Codex,沒撞到限額;它的唯讀沙盒跑不了 pytest,只讀碼與 stub 重現)。受審 40b2a01..fc2c3d6 全量(資安)與 c5a34da..fc2c3d6 修正段(其他席)。
- 前輪 14 條:驗收席逐條判已修;測試席對 14 條逐一變異,全紅。
- 格式處理:regress 席把整份報告包在一組 ``` 圍欄裡,機械收貨讀不到 severity。編排者只刪掉包住全文的第一行與最後一行圍欄(內文實驗輸出的圍欄保留),內容一字未改,再跑 report-normalize。
- quote-check:資安席(clean)5 句有 1 句錨不到(「if not is_aware(moment): raise ValueError("時間必須帶時區")」):原文在 src/rtb/analyzer/task_store.py:220-221 跨兩行,機械重現 HIT;該席無發現,不影響處置。其餘四席全數錨定。
- 共 5 條發現,合併成 4 件事。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| regress-1 | 讀 src/rtb/ops/metrics.py:787-810:其他指標固定用第一輪 part,端到端取後幾輪較新快照;席位 /tmp 實跑同一份報告端到端 3 筆、終點事件率 1 筆 | HIT,折入 |
| x1-1 | 同 regress-1,另指後兩輪相等時還標穩定 | HIT,同一件,折入(固定第一輪輸入,重讀只確認端到端與第一輪一致,變了就標不穩定) |
| arch-1 | 讀 src/rtb/executor/attempt_store.py:378、src/rtb/analyzer/task_store.py:220、src/rtb/ops/metrics.py:88 三份時區檢查;src/rtb/domain/_checks.py 檔頭規定同一規則一份定義 | HIT,折入(共用一份放進 domain/_checks) |
| arch-2 | 讀 src/rtb/ops/trace.py:43-45、:467 缺參數仍回 2,與資料庫檔不存在撞號;metrics 已改 7 | HIT,折入(兩支共用同一套參數錯處理) |
| tests-1 | 缺必要旗標與語意組合檢查沒有測試斷言結束代碼 7 | HIT,折入(minor,本輪有 major 不放行) |

## 處置
- 全部折入,放行 0、駁回 0。修法交回增量 2 修正實作員。第 3 輪是代碼審上限。
