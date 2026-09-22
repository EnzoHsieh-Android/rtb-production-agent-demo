# 增量 2 設計審第 2 輪收貨與重現紀錄(2026-09-22)

機械檢查:七份報告 report-normalize 皆已正規化;quote-check 有三句錨不到——三塊分工席第 4 條把快照的「轉人工」抄成「轉人類」、外家席第 2、3 條的標點與快照全形半形不同。依規定錨不到的不直接丟,改由編排者對照快照與程式重現,三條內容都成立,列入下表。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| s1-1 | 重簽用現況版本會把合法重放變冪等衝突 | 讀 src/rtb/dsp/store.py 的 fingerprint 包含預期版本、_existing_operation 指紋不等即 IdempotencyConflict | HIT |
| s2-1 | 驗簽比對對象沒寫成不變量 | 讀快照格式機制段,確實只寫拆解不寫比對對象 | HIT |
| s2-2 | 請求本文沒有閉集合 | 讀 src/rtb/dsp/server.py 的 _update_budget、_pause_campaign:多出的本文欄位被忽略 | HIT |
| s2-3 | 廣告不存在可能落到既有 404 | 讀 src/rtb/dsp/store.py 的 get_campaign 丟 CampaignNotFound | HIT |
| s2-4 | 設定檔先查路徑再開檔有競態 | 讀快照設定檔權限句,沒寫檢查與讀取綁同一個檔案 | HIT |
| s2-5 | 憑證不代表業務核准的邊界沒寫 | 讀快照,確實沒有這句 | HIT |
| s3-1 | 兩邊金鑰來源沒有共同約定 | 讀快照 DSP 與執行行程兩段各自寫從環境變數讀,沒有共同名稱 | HIT |
| s3-2 | 分析行程匯入禁令可被 noqa 跳過 | 讀 tests/analyzer/test_boundaries.py 的 test_the_analyzer_package_never_imports_dsp_internals:只驗 ruff 規則存在,不掃原始碼 | HIT |
| s3-3 | 時間欄位格式沒定 | 讀快照聲明段,沒有時間格式;json.dumps 遇 datetime 會 TypeError | HIT |
| s3-4 | 新增結果代碼會改增量 1 已凍結的領域層檔 | 讀 src/rtb/domain/attempt.py 的 OutcomeCode 封閉列舉;引句錨不到(轉人類),內容成立 | HIT |
| s4-1 | 同行程直接建構伺服器的測試沒納入遷移 | 讀 tests/kit/test_shared_base.py 的 invalid_json 斷言,照快照驗證順序會先被憑證擋 | HIT |
| s4-2 | 執行行程拒絕啟動沒有程式可掛 | 讀 src/rtb/executor/inbox_server.py 檔頭:不呼叫 DSP | HIT |
| s5-1 | 對帳重送時不簽沒有去處 | 讀快照「不簽發生在開始一筆之前」只涵蓋第一次 | HIT |
| s5-2 | 過期一律轉人工會鎖死 | 讀快照增量 3 收到拒收段:一律轉人工;src/rtb/domain/attempt.py 轉人工沒有一般出路 | HIT |
| x1-1 | 同 s1-1 | 同上 | HIT |
| x1-2 | 只看檔案模式擋不住替換整個檔案 | 讀快照設定檔權限句,只看檔案本身;引句標點不同,內容成立 | HIT |
| x1-3 | 同 s5-2 | 同上;引句標點不同,內容成立 | HIT |

架構對齊席 clean。
