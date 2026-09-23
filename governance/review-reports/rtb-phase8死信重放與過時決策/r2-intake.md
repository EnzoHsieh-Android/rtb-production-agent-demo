preflight-4: ran

# 第 2 輪收貨(2026-09-24)

三席:前輪修正驗收與鏡像(sonnet)、外家 Codex finder、架構對齊(sonnet)。收齊後才動計劃。三席引句全數錨定;記帳載體用 Codex 席。架構對齊 clean。

| id | 重現方式 | 結果 | 處置 |
|---|---|---|---|
| verify-F1 | 讀快照:實務隱患節「裁定前不開始實作新鮮度那一項」與待裁節「裁定前 [S505] 的測試照…寫」 | HIT | 折:實作與測試都等裁定,寫明 [S505] 邊界在哪些選項下不變 |
| verify-F2 | 讀快照:[S511] 排在 [S510] 前面 | HIT | 折:移到後面 |
| finder-F1 | 讀 `src/rtb/executor/inbox_store.py:498`:新修訂只取代待處理的舊列;讀 `src/rtb/analyzer/policy.py:83`:分析端每個任務只送修訂 1 | 現象 HIT;在本專案的送件方下不可達 | 放行:一般流程取件後本來就有同一個窗口,分析端不送新修訂(重新規劃開接續任務) |
| finder-F2 | 讀 `src/rtb/executor/execution.py:633`:開始一筆的交易會重判決策到期,不重判其他硬規則 | HIT | 折:新鮮度在開始一筆與重跑轉回送出的交易裡重判,[S505] 補 |
| finder-F3 | 讀 `src/rtb/executor/execution.py:404`、`src/rtb/executor/inbox_store.py:521`:執行前檢查擋下沒有嘗試紀錄,收件列會被清 | HIT | 折:改寫結果的耐久來源,執行前檢查擋下的從分析端任務歷史查 |
| finder-F4 | 讀 `src/rtb/executor/approve.py:67`、`src/rtb/executor/approval.py:83`:核可工具不看決策建立多久 | HIT | 折:改寫選項 c 的後果,補選項 e(核可入口機械拒絕) |
