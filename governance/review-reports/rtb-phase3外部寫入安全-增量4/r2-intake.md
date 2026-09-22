# 增量 4 設計審第 2 輪收貨與重現紀錄(2026-09-23)

機械檢查:五份報告已正規化、quote-check 全數錨定。並行席在暫存目錄照設計補最小作廢實作跑了四組實驗,作廢的核心論證(同一把寫入鎖下只有兩種結果)站得住。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| sarch-1 | 作廢怎麼套既有範圍檢查沒寫 | 讀 src/rtb/dsp/capability.py 的 check_scope 六項比對含預期版本 | HIT |
| x1-1 | 沒有真正重疊的作廢與舊寫入合約 | 讀 src/rtb/dsp/server.py 延遲睡眠在 store.execute 之前;快照 S78 只有先後兩例 | HIT |
| x1-2 | 廣告不存在作廢會被範圍檢查擋 | 讀 check_scope 對 tenant_of 為空判不符 | HIT |
| x1-3 | 作廢端點 4xx 沒有去處 | 讀快照第 3 步只列逾時斷線 5xx 讀不懂 | HIT |
| x1-4 | 操作已作廢被泛用 409 規則吃掉 | 讀 src/rtb/executor/execution.py 的 version_conflict 規則不看錯誤代碼、react 取第一個 | HIT |
| x1-5 | 回退會刪掉墓碑 | 讀快照回退段 | HIT |
| s1-1 | 作廢後同一把鍵的新修訂永遠擋死 | 讀 src/rtb/domain/attempt.py 的 operation_key 不含修訂與到期;增量 3 鍵已存在失敗就擋下 | HIT |
| s1-2 | 簽作廢設定檔壞掉沒有去處 | 讀快照第 3 步 | HIT |
| s2-1 | DSP 操作表沒存預期版本 | 讀 src/rtb/dsp/store.py 的 operations 表與 _apply | HIT |
| s2-2 | 同 x1-2 | 同上 | HIT |
| s3-1 | 一般寫入憑證能拿去作廢 | 讀快照範圍只列三項沒列動作 | HIT |
| s3-2 | 同 x1-4 | 同上 | HIT |
| s3-3 | 新路由撞既有兩支窮舉測試 | 讀 tests/dsp/test_capability.py 與 tests/dsp/test_store.py 的路由窮舉斷言 | HIT |
| s3-4 | S50 變嚴格但替身跟不上 | 讀 tests/executor/fakes.py 的 operations 只存整數 | HIT |
| s3-5 | 延遲提交測試骨架延遲寫死 0 | 讀 tests/executor/test_execution_e2e.py 的 PlannedDsp | HIT |
| s3-6 | 同 s1-2 | 同上 | HIT |

## 判讀

- 16 條全部成立、全部折入第 3 版;refuted 為 none。
- x1-2 與 s2-2 的判準是「讓不存在的廣告也能作廢」;這裡改成廣告不存在時不作廢、直接判失敗,理由是模擬 DSP 沒有建立或刪除廣告的介面,舊請求在範圍檢查就不可能過。這條前提寫進計劃,之後若加了建立或刪除廣告的介面要回頭改。
- s1-1 不改冪等鍵算法(增量 1 已凍結),寫成刻意並補合約與 REVISIT。
- 這一輪的折入沒有再派第 3 輪設計審(使用者授權自主推進);由代碼審把關。
