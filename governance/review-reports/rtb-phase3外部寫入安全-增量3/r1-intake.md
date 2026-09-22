preflight-4: ran

# 增量 3 設計審第 1 輪前掃紀錄(2026-09-22)

便宜代理只掃「增量 3 設計」一節,固定清單四項:① 未定義的詞:無。② 壞引用:無。③ 範圍自相矛盾:無。
④ 機械宣稱驗語意:同鍵重送回目前狀態(src/rtb/executor/inbox_store.py 的接收邏輯)成立;DSP 回應對照表每一列與 src/rtb/dsp/server.py 的錯誤對照表一致;增量 1 一般轉換表允許表中每個轉換(src/rtb/domain/attempt.py)。代理標了一條「收件表新狀態不成立」:那是這份設計要新增的狀態,不是對既有程式的宣稱,判為誤報,不修真檔。沒有動到核心裁定。
# 增量 3 設計審第 1 輪收貨與重現紀錄(2026-09-22)

機械檢查:七份報告已正規化、quote-check 全數錨定。流程席用真的 DSP 實測了寫入與讀取的 404 差異。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| s1-1 | 佇列卡在第一份卡住的提案 | 讀快照第 1 步只挑最舊一份、第 4 步不成立就結束這一輪;src/rtb/executor/attempt_store.py 的 begin 對同廣告未結案丟 CampaignLocked | HIT |
| s1-2 | 讀取 404 沒和暫時失敗分開、寫入 404 打不到 | 席位以真的 DSP 實測:寫入不存在的廣告回 403 範圍不符、讀取回 404;src/rtb/analyzer/dsp_client.py 既有讀取把非 200 併成同一例外 | HIT |
| s1-3 | 其他 4xx 一律算失敗 | 讀 src/rtb/httpkit.py 與 src/rtb/dsp/server.py 的協定層 4xx 清單;這些是本地組壞請求 | HIT |
| s1-4 | 已交給執行後失敗無回饋路徑 | 讀快照不做的事只提擋下 | HIT |
| s2-1 | 同 s1-1 | 同上 | HIT |
| s2-2 | 鍵已存在沒有收尾 | 讀 src/rtb/executor/attempt_store.py 的 begin:鍵已存在回傳既有列 created=False | HIT |
| s2-3 | 擋下沒有競態合約 | 讀快照合約清單,S45 只涵蓋取件 | HIT |
| s2-4 | 第二個執行迴圈會吞掉寫入結果 | 讀 src/rtb/executor/attempt_store.py 的 recover_in_flight 不分來源、_current 序號不符回 None | HIT |
| s3-1 | 同 s2-2 | 同上 | HIT |
| s3-2 | 重建表沒有鎖內再查 | 讀 src/rtb/dsp/store.py 既有遷移鎖內再查;src/rtb/executor/inbox_server.py 每個請求新開連線 | HIT |
| s3-3 | 擋下代碼列了用不到的提案過期 | 讀快照同段下一句 | HIT |
| s4-1 | 同 s2-2 | 同上 | HIT |
| s4-2 | 同 s1-2 | 同上 | HIT |
| s4-3 | 同 s3-3 | 同上 | HIT |
| s4-4 | 部分回應正常流程造不出 | 讀 src/rtb/dsp/store.py 指紋比對:同一份提案算出的鍵與內容一致,冪等衝突造不出 | HIT |
| s4-5 | 啟動程式沒有就緒訊號、兩情境併一條 | 讀 src/rtb/dsp/server.py 與 src/rtb/executor/inbox_server.py 印 PORT= 的做法 | HIT |
| s4-6 | 暫停的驗證沒要求窮舉 | 讀快照 S49 沒要求從動作清單列舉 | HIT |
| s5-1 | 憑證過期重送逾時沒有去處 | 讀快照「不寫新列」;重送逾時停在嘗試中、提案已交給執行不會再被挑到 | HIT |
| s5-2 | 寫入後版本沒地方存 | 讀 src/rtb/executor/attempt_store.py 的嘗試表結構沒有版本欄位 | HIT |
| s5-3 | 拒絕簽發併了設定壞掉與超過上限 | 讀 src/rtb/executor/capability_signer.py 的拒簽原因 | HIT |
| s5-4 | F1 預告合約可能被提前轉正 | 讀 docs/rtb-production-agent-demo-knowledge/Verification 的 F1 預告合約要求對帳與最終一致 | HIT |
| s5-5 | 不在投放難觸發 | 讀 src/rtb/dsp/store.py 暫停會推進版本,版本檢查先攔 | HIT |
| s5-6 | 護欄其餘子項沒點名 | 讀交接文件第 8 節第 2 項 | HIT |
| sarch-1 | 沒交代時間單一來源與協定注入 | 讀 src/rtb/analyzer/flow.py 的協定與 advance;圖譜分析行程流程的時間單一來源規則 | HIT |
| sarch-2 | 重建表是第二種做法 | 讀 src/rtb/analyzer/task_store.py 與 src/rtb/dsp/store.py 只補欄位 | HIT |
| sarch-3 | DSP 讀寫用戶端沒交代位置 | 讀 src/rtb/analyzer/dsp_client.py 的職責說明 | HIT |
| x1-1 | 同 s1-1 | 同上 | HIT |
| x1-2 | 同 s5-1(重送不計次) | 同上 | HIT |

所有發現都折入第 2 版;收件表的實作方式從重建表改成加處置欄位,使用者選的做法一(收件表自己記已擋下與原因代碼)不變。
