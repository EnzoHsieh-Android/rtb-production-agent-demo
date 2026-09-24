preflight-4: ran

# r1 收件與前掃(f7效能-重試,第 2 部分設計)

## 前掃(編排者自己開碼核對機械宣稱的語意)

| 宣稱 | 查證 | 結果 |
|---|---|---|
| 開寫入交易等鎖逾時丟忙碌,這時什麼都沒寫 | `src/rtb/sqlitekit.py:64-71` begin_immediate 只執行 BEGIN IMMEDIATE,鎖競爭轉 DatabaseBusy;`immediate_transaction` 在 begin 失敗時根本沒進 try | HIT,成立 |
| 收件表的交易入口把忙碌轉成收件口忙碌 | `src/rtb/executor/inbox_store.py:894-907` transaction() 以 except DatabaseBusy 包住整個 with,**交易本體內**的 DatabaseBusy 也會被轉成 InboxBusy | HIT;所以設計要求「旗標分辨本體開始了沒」是必要的,不是多餘的 |

無修改真檔;無動到核心裁定。
