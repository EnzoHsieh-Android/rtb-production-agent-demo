# 第 2 輪收貨與重現紀錄(2026-09-23)

機械檢查:七份報告已正規化、quote-check 全數錨定。

## 重現與判讀

- 22 條全部成立、全部折入第 3 版;refuted 為 none。
- 三條 blocker 有兩條是同一件事:保留期清除只看「是不是待處理」,處理中的提案不算待處理,收件超過兩小時就會被不相干的收件整列刪掉。並行席用現有程式實驗重現(以既有 block 模擬非空處置,時鐘撥到兩小時五分後呼叫不相干任務的 accept,該列消失)。編排者核對 src/rtb/executor/inbox_store.py 的 _purge_finished_tasks 條件是 SUM(PENDING)=0 且 MAX(received_at) 超過保留期 → 成立。
- 第三條 blocker(三段式席):對帳挑鍵的入口 awaiting_reconciliation 只選結果不明與已提交待驗證,轉人工不在其中,設計說要續租卻沒有路 → 編排者讀 src/rtb/executor/attempt_store.py 確認成立。可測性席獨立抓到同一件事。
- 外家席兩條都引用 Phase 0 架構筆記已定的 lease 規則(序號加擁有者、嘗試寫入與 ACK 都用條件更新核對)。編排者讀 Projects/RTB_Agent_Phase0架構.md 的 Queue 與 owner、lease、fencing 兩段確認:第 2 版把這些簡化掉了,是設計偏離既有裁定,不是新主張。第 3 版照裁定補回。
- 判準與觀察分開驗:外家席建議把租約欄位複製到嘗試紀錄。這裡改成在同一個交易裡先核對收件表那一列的收據、再寫嘗試紀錄——兩張表在同一個資料庫檔,不必複製欄位,也不會兩份不一致。
- 銜接席提的「重啟恢復在多工作者下會誤殺別人的嘗試」判為增量 3 的待辦,寫進計劃「留給增量 2、3」,它自己也標 blocking 否。
