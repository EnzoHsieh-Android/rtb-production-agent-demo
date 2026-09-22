preflight-4: ran

# 增量 4 設計審第 1 輪前掃紀錄(2026-09-23)

便宜代理只掃「增量 4 設計:對帳」一節,固定清單四項。① 未定義的詞:「延遲提交」沒對齊程式裡的「延遲回應」故障。② 壞引用:無。③ 範圍自相矛盾:無。④ 機械宣稱驗語意:4 條語意類命中,已修真檔,沒有動到核心裁定(情境題代答、提交當下再驗憑證時間、判定表的分支與去向都沒改):

| 項 | 修改前 | 修改後 |
|---|---|---|
| ①/④ 延遲提交 | 「延遲提交」故障是驗完才睡、睡完才提交 | 用既有「延遲回應」故障重現,延遲秒數設得比執行行程的 DSP 逾時長(src/rtb/dsp/server.py 的 FAULT_MODES 只有 delayed_response) |
| ④ 操作紀錄欄位 | 操作紀錄只回廣告、動作、寫入後版本 | 另有操作編號、提交時間、是否重放;執行行程用戶端查操作紀錄現在只取寫入後版本,要擴充解析廣告與動作(src/rtb/executor/dsp_client.py) |
| ④ 查證逾時上限 | 記一次查證逾時(增量 1;達上限自動轉人工) | 增量 1 的記錄函式達上限丟例外,由對帳接住再轉人工(src/rtb/executor/attempt_store.py 的 record_verification_timeout) |
| ④ 執行後驗證 | 走增量 3 的執行後驗證(同一個函式) | 那是執行者物件的方法,對帳掛在同一個執行者物件上呼叫(src/rtb/executor/execution.py) |

# 增量 4 設計審第 1 輪收貨與重現紀錄(2026-09-23)

機械檢查:七份報告已正規化、quote-check 全數錨定。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| sarch-1 | 提交當下驗憑證時間會讓儲存層懂憑證 | 讀 src/rtb/dsp/store.py 的 _execute_in_transaction:重放判斷在前、_apply 在後,只能插在儲存層 | HIT |
| s1-1 | 對帳檢查不過的代碼跟增量 2 那句矛盾 | 讀快照增量 2「執行行程怎麼簽」對帳重送那句與增量 4 第 3 步 | HIT |
| s1-2 | 重送後只點名回應對照函式 | 讀 src/rtb/executor/execution.py:react 只查表,_record 才補寫入後版本與過期接續 | HIT |
| s2-1 | 寬限值來源違反執行迴圈邊界 | 讀 src/rtb/dsp/capability.py 的 MAX_SKEW_SECONDS 與 tests/executor/test_executor_boundaries.py;席位以掃描器實測 | HIT |
| s2-2 | 一輪對帳沒有時間上限或退避 | 讀 src/rtb/executor/runner.py 的 _loop 只看新提案結果決定休息 | HIT |
| s2-3 | 到期時間若併進指紋會打壞同鍵重送 | 讀 src/rtb/dsp/store.py 的 fingerprint;席位實驗 | HIT |
| s3-1 | DSP 兩支時鐘不相通 | 讀 src/rtb/dsp/server.py 建 CampaignStore 沒傳時鐘;席位實驗 str 與 int 比較丟 TypeError | HIT |
| s3-2 | 沒交代不帶到期時間的既有寫入路徑 | 讀 tests/dsp/test_store.py 直接建 Operation 呼叫 execute | HIT |
| s3-3 | 到期檢查與版本檢查先後沒寫 | 讀快照 | HIT |
| s4-1 | 提交當下檢查寫不出先紅的測試 | 讀 tests/executor/test_execution_e2e.py 的 PlannedHandler 只在請求開頭定時間 | HIT |
| s4-2 | 擴充查寫入後版本會動到增量 3 介面 | 讀 src/rtb/executor/execution.py 的 _check_applied 用 == 比整數 | HIT |
| s4-3 | 送出上限與設定壞掉沒有合約 | 讀快照 S70 到 S84 | HIT |
| s4-4 | 查詢失敗與讀取失敗綁同一支測試 | 讀快照 S76 | HIT |
| s4-5 | 對帳順序沒有合約 | 讀快照 S82 | HIT |
| s4-6 | 無法證明未發生會變死碼 | 讀快照判定表沒有任何一格用它 | HIT |
| s5-1 | 同 s4-2 | 同上 | HIT |
| x1-1 | 提交當下檢查不是提交時間的硬上限 | 讀 src/rtb/dsp/store.py 的 execute:檢查與 COMMIT 之間可被暫停 | HIT |
| x1-2 | 查鍵與讀版本之間的競態 | 讀快照第 1、2 步是兩次獨立讀取 | HIT |
| x1-3 | 只核對廣告與動作不夠 | 讀 src/rtb/dsp/store.py 的 OperationResult 沒有參數與預期版本;鍵由執行端算 | HIT |

## 判讀

- 19 條全部成立、全部折入第 2 版。x1-1、x1-2、s3-1、s3-2、s3-3、s4-1、s2-1、s2-3、sarch-1 九條都指向「用時間證明舊請求不會晚到」這個核心機制,折法是整個換成 DSP 端的作廢(墓碑);這是代使用者裁定,在計劃裡標明待覆核。
- 觀察與判準分開驗:x1-3 建議「DSP 提供鍵與內容綁定證據」;這裡改成查詢端點多回參數與預期版本、執行端逐項核對,不另造證據格式。
- 鏡像核對:編排者自己全文找「寬限」「提交當下」,增量 3 的憑證到期時間段補一行後記,增量 2 對帳重送那句加註改判法。沒有另派代理。
- refuted 為 none。
