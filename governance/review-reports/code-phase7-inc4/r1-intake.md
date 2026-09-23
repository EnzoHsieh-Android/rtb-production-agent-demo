# code-phase7-inc4 收貨紀錄(2026-09-23)

light 分級(改動只有測試與文件)。編制只要求架構對齊一席;因為這批測試之後要綁進事故 F5 的正式合約,編排者自願加派一席通才專看「測試夠不夠格」。兩席全到才讀、才動。通才席 clean:每一層都是真的正式程式、S213 斷言逐條對回計劃、對照組確實改預算、S214/S215 不會把卡在蒐證誤判成通過,兩種變異在臨時目錄都翻紅。

| id | 重現命令 | 輸出摘錄 | 結論 | 處置 |
|---|---|---|---|---|
| arch-F2 | 對照 tests/analyzer/test_boundaries.py 的伺服器啟停(啟動執行緒在 try 內) | 本測試啟動執行緒在 try 外 | HIT | 折:啟動執行緒移進各自的 try/finally |
| arch-F1 | `grep -n '^from rtb.executor' tests/analyzer/test_boundaries.py` | 既有分析側端到端測試在檔頭直接匯入 rtb.executor.inbox_server | HIT(判準不成立) | 放行:既有同層端到端測試就是檔頭直接匯入執行側模組,沒有「區域匯入標記」的慣例 |

refuted:none。
