# 增量 3 設計審第 2 輪收貨與重現紀錄(2026-09-22)

機械檢查:七份報告已正規化、quote-check 全數錨定。第 2 輪不是首輪,不跑前掃。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| sarch-1 | 鎖檔是專案裡第二種並行做法 | grep src/rtb 無 flock/fcntl;src/rtb/sqlitekit.py 的 begin_immediate 是既有唯一互斥做法 | HIT |
| s1-1 | 鍵已存在不分終點與未結案,吞掉人工判失敗 | 讀 src/rtb/executor/attempt_store.py 的 begin:鍵已存在不論狀態都回既有列;MANUAL_FAILURE 在 src/rtb/domain/attempt.py 是失敗類 | HIT |
| s1-2 | 憑證過期鏈沒處理送出次數已達上限 | 讀 src/rtb/executor/attempt_store.py transition:sends >= MAX_SENDS 丟 SendLimitReached | HIT |
| s1-3 | 過期後重跑檢查把設定壞掉混成業務拒簽 | 讀 src/rtb/executor/capability_signer.py:設定類與業務類都是 SigningRefused,只靠 reason 分辨 | HIT |
| s2-1 | 鎖檔路徑別名與刪檔重建 | 讀快照只寫「資料庫旁的鎖檔」一句,沒有正規化與重建對策 | HIT |
| s2-2 | 一個交易帶第 4 步序號跟過期多筆鏈衝突 | 讀 src/rtb/executor/attempt_store.py 的 _current:序號不是最新列就回 None 不寫 | HIT |
| s3-1 | 收件到期標記不看處置 | 讀 src/rtb/executor/inbox_store.py 的 _accept_in_transaction:UPDATE 只濾 state = 'pending';席位以 SQLite 實驗重現 | HIT |
| s4-1 | 處理一筆與回應對照函式沒有接線保證 | 讀快照 S47 只驗對照函式本身;src/rtb/dsp/server.py 的 FAULT_MODES 造不出冪等衝突與範圍不符 | HIT |
| s4-2 | 全表未結案已滿沒有合約 | 讀快照合約清單,S39 到 S61 沒有這條;attempt_store 的 begin 丟 TooManyUnresolved | HIT |
| s4-3 | S55 沒驗鎖在恢復之前 | 讀快照 S55 測試名只含恢復在就緒之前 | HIT |
| s4-4 | 寫入後版本離開待驗證之後沒驗 | 讀快照 S51 只涵蓋留在已提交待驗證 | HIT |
| s5-1 | 過期記成結果不明後當機會丟掉 DSP 明確沒寫 | 讀 src/rtb/executor/attempt_store.py 的 recover_in_flight 只轉 in_flight;結果不明列沒有任何標記 | HIT |
| s5-2 | 增量 4 查到後轉已提交待驗證沒交代寫入後版本 | 讀 src/rtb/dsp/store.py 的 OperationResult.version_after 有值可用;快照只定義步驟 6 那條路徑 | HIT |
| s5-3 | 本地請求錯誤只鎖一個廣告,跟設定壞掉停機矛盾 | 讀快照兩段理由句式一致、處置不同;MAX_UNRESOLVED = 20 | HIT |
| s5-4 | 沒有送出時間,增量 4 等待窗口沒有起算點 | 讀 src/rtb/executor/attempt_store.py:只有 written_at,寫在送出之前 | HIT |
| x1-1 | 共用用戶端標頭列舉送不出憑證 | 讀 src/rtb/httpclient.py 的 ClientHeader 只有 IDEMPOTENCY_KEY,非成員丟 TypeError | HIT |
| x1-2 | 整輪固定時間讓重簽簽出過期憑證 | 讀 src/rtb/executor/capability_signer.py 的 sign:iat = now、exp = now + 120,直接用傳入值 | HIT |
| x1-3 | 對照表漏 3xx | 讀 src/rtb/httpclient.py:_NoRedirect 不跟轉址,HTTPError 分支回傳 error.code | HIT |
| x1-4 | 鎖檔沒綁資料庫實體身分 | 同 s2-1 | HIT |

## 判讀

- 19 條全部成立、全部折入第 3 版。同一件事的幾條(sarch-1、s2-1、x1-4 都是鎖;s5-1、s5-4 都靠「嘗試中記下憑證到期時間」解)合併成一個改法。
- 觀察與判準分開驗:
  - sarch-1 建議直接鎖執行行程資料庫。這樣做會讓收件口收不了提案,所以改成鎖一個專門的小 SQLite 檔,檔名綁資料庫的裝置與 inode 編號。這樣同時解掉 s2-1 與 x1-4 的路徑別名問題。
  - s5-1 建議把「DSP 明確沒寫」記在結果不明那一列。這裡改成嘗試中那一列記下憑證到期時間,當機後由增量 4 以「查不到且憑證已過期」證明沒發生。
  - s4-1 建議用靜態檢查接線。這裡改成讓處理一筆接假的 DSP 寫入用戶端逐列驗證,直接驗到行為。
- 沒有機械重現不到的條目,refuted 為 none。
