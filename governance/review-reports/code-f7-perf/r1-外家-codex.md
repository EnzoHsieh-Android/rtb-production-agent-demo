severity: major

第一列時間型別異常時，快路徑放過原算法會拒絕的資料
severity: major
blocking: 是
引句:「# 候選裡屬於這個租戶、金額型別不是整數也不是空值的列數。偵測只看型別與租戶欄,不把候選列取回程式」
審材外查證：`src/rtb/executor/attempt_store.py:58` 的 `written_at` 欄位不是嚴格型別；原算法在 `src/rtb/executor/attempt_store.py:763` 取回第一列，並於 `src/rtb/executor/attempt_store.py:770` 依時間排序。快路徑只檢查金額型別，見 `src/rtb/executor/attempt_store.py:634`。
重現：唯讀比對上述路徑後，構造兩筆同租戶、正整數金額、窗內已驗證的資料：一筆第一列的 `written_at` 存 BLOB，另一筆存正常文字；兩筆驗證列的時間都正常。原算法排序時會因 `bytes` 與 `str` 無法比較而丟 `TypeError`，快路徑則會回傳加總，可能據此放行。嘗試以 `cp -R` 建立 `/tmp` 複本時收到 `Operation not permitted`，故未執行測試。
建議：快路徑須偵測候選第一列中原算法無法處理的排序欄位型別並退回原算法；加入上述資料的等價測試，核對兩邊都丟相同例外。

S19 核對：目前只匯入純判斷函式，沒有發現這次收窄測試讓嘗試紀錄模組自行開連線。

1 條,blocking 1。