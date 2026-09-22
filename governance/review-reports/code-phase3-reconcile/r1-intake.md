# 對帳代碼審第 1 輪收貨與重現紀錄(2026-09-23)

機械檢查:七份報告已正規化、quote-check 全數錨定。判定表、合約與測試強度兩席 clean;作廢並行席與邊界席各自用真的 DSP 子行程與多執行緒/多行程實驗驗過作廢的線性化。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| x1-1 | 重送簽憑證用重算的鍵、送出用存下的鍵 | 讀 src/rtb/executor/execution.py 的 _sign 用 operation_key(proposal);新測試把算鍵函式換掉模擬算法升版,變異還原後兩支測試紅 | HIT |
| sarch-1 | 作廢端點沒走共用的故障注入外殼 | 讀 src/rtb/dsp/server.py:作廢直接呼叫 store.void;改成共用 _commit 後,新的作廢逾時測試變異可翻紅 | HIT |
| sarch-2 | 兩支查詢方法各自解析一份 | 讀 src/rtb/executor/dsp_client.py;改成舊方法轉呼叫新方法 | HIT |
| sarch-3 | 兩張對照表各抄一份狀態碼比對函式 | 讀 src/rtb/executor/execution.py 的 _status 與 _void_status 逐字相同;合併成一支 | HIT |
| sec-1 | 作廢的例外處理沒有測試守 | 席位拿掉 try/except 後 70 支測試全綠;測試骨架把作廢排除在故障注入外 | HIT |
| sec-2 | 作廢表只增不減沒有上限 | 讀 src/rtb/dsp/store.py 的 voided_keys 沒有清理路徑 | HIT |
| s2-1 | 作廢查詢的註解容易被讀成「在交易外」 | 讀 src/rtb/dsp/store.py 的 _execute_in_transaction 註解 | HIT |
| s3-1 | 預期版本沒有整數上界 | 席位用真 DSP 實測 2**63 的作廢回 200;加上界後新測試變異翻紅 | HIT |

## 判讀

- 8 條全部成立、全部折入(含 3 條 minor:輪內有 major,依代碼審規則不留放行);refuted 為 none。
- sec-1 與 sarch-1 是同一件事的兩面:作廢端點沒走故障注入外殼,所以測試造不出作廢逾時。折法是讓作廢走同一層外殼、拿掉測試骨架對作廢的排除,再補一支真的逾時測試。
- x1-1 的第一版測試抓不到(新舊算法算出同一把鍵),改成在測試裡把算鍵函式換掉模擬升版才真的守得住;過期重簽那條路另補一支。
- sec-2 折成圖譜規則加 REVISIT(2026-11-30 盤點筆數),程式行為不變。
