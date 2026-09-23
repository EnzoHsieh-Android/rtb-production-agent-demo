preflight-4: ran

# code-phase8-f6tz 第 1 輪收貨(2026-09-24)

範圍:Phase 8 代碼審通過之後的改動(事故 F6 轉正、補測試、收件口寫入時間統一、Phase 8 驗收紀錄),565 行,分級 standard。兩席:審查席(sonnet)、架構對齊席(sonnet)。收齊後才動工作目錄;收件前核對 reflog,沒有不是編排者做的提交。記帳載體用審查席(全數錨定)。

| id | 重現方式 | 結果 | 處置 |
|---|---|---|---|
| reviewer-F1 | 新測試:把 SQL 用 + 拆開、相鄰字串、f-string 寫法,結構檢查要拼得回來;配方改用拆開的字串 | HIT(原檢查只比對單一字面,配方跟檢查同一種寫法所以循環) | 折:結構檢查先把拆開的字串拼回來再查;循環的配方改成拆開寫法、再加一條放掉租約時用拆開字串改回死信的配方;26 條全紅;刻意用變數組表名仍繞得過,照實寫進合約說明與驗收紀錄(防忘記不防刻意繞過) |
| arch-F1 | 讀既有測試:`tests/executor/test_attempt_store.py:689` 與 `tests/executor/test_capability_signer.py:164` 全庫 rglob 讀原始碼找字串;`tests/domain/test_metrics.py:226` 全目錄 ast 走訪 | 「引入第二種做法」MISS | 駁:全庫掃原始碼找字串、用 ast 走訪整個目錄都是專案既有做法,正規表示式比對只是找字串的嚴格版 |
