# 執行一筆代碼審第 2 輪收貨與重現紀錄(2026-09-23)

機械檢查:四份報告已正規化、quote-check 全數錨定。

流程偏離(照實記):外家席與架構對齊、資安席先回來後,編排者在「修正本身與回歸」席還沒回報前就動手改工作目錄。該席報告自己也注意到 repo 正被改動;它的變異實驗都在獨立複本上做,結論不受影響,但它讀到的不再是凍結當下的碼。下次照規則等所有席收齊再動。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| x1-1 | 拿不到鎖前已用硬連結檔名以 SQLite 開庫 | 讀 src/rtb/executor/runner.py 的 run:鎖前先 InboxStore(args.db);SQLite 官方 howtocorrupt 列多重連結;新測試以替身記錄 InboxStore 建構,變異還原舊寫法後紅 | HIT |
| x1-2 | 簽發期間過期仍送出 | 新測試讓簽發器簽完推進時鐘;修正前寫入照送,修正後在取件交易內判到期;變異拿掉後紅 | HIT |
| s1-1 | 同 x1-2 | 同上 | HIT |
| s1-2 | 啟動程式檔頭仍說硬連結都算同一把 | 讀 src/rtb/executor/runner.py 檔頭 | HIT |
| sarch-1 | 任務表補欄位忙碌丟裸 DatabaseBusy | 讀 src/rtb/analyzer/task_store.py 建構子;席位實驗重現;三支建構子改同一形狀並補參數化測試,兩支變異皆紅 | HIT |
| sarch-2 | 新測試檔放 tests 根目錄 | 讀 tests 根目錄只有靜態檢查閘兩支;已搬到 tests/kit | HIT |
| sec-1 | 新測試說明把 DSP 儲存層寫成長活物件 | 讀 src/rtb/dsp/server.py 每請求新開 CampaignStore | HIT |

## 判讀

- 7 條全部成立、全部折入(含 3 條 minor 一併修掉);refuted 為 none。
- 到期判斷最後落在「開始一筆」的同一個交易裡,這是寫 DSP 前最後一個本地步驟;讀 DSP 之前那一次保留(過期就不必讀 DSP)。取件交易之後到 DSP 收到請求之間仍有極短窗口,由憑證到期時間(120 秒)兜底。
