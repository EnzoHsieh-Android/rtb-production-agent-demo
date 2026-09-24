preflight-4: ran
r3 為末輪 delta 審,前掃沿用 r1。

## 收貨機械重現(編排者 Claude 查證的部分;協調者核對的另補)

| id | 指令或讀檔 | 輸出摘要 | 判定 |
|---|---|---|---|
| m1 | sed -n 62,68p src/rtb/capabilitykit.py | read_key 取環境變數字串再 raw.encode("utf-8");環境變數只裝字串,位元組金鑰傳不進去,伺服器用原始位元組簽、子行程用文字轉回的位元組驗,兩邊不同 | HIT |
| m3 | grep -n "MAX_SKEW_SECONDS" src/rtb/dsp/capability.py | MAX_SKEW_SECONDS = 30(:37),:92 拒收簽發時間超過現在 30 秒以上的寫入許可;只撥執行迴圈時鐘 5 分鐘、DSP 不撥就會被拒 | HIT |
| m5 | grep -n "def reply\|application/json" src/rtb/httpkit.py | reply(:235)只收字典、寫死 Content-Type application/json(:239),沒有 Location、text/css 的出口 | HIT |
| m8 | grep -rn "Sec-Fetch" src | 目前沒有任何地方看 Sec-Fetch 標頭;GET 沒有表單隨機值可驗 | HIT |
| z1 | grep -n "def issue" -A8 src/rtb/executor/approval.py | issue 只檢查關卡、核可人格式與到期,不看那筆提案目前是不是還在等人確認 | HIT |
| p1 | grep -n "Barrier" tests/analyzer/test_task_lease.py | 既有租約測試用 threading.Barrier(2)(:96)逼兩邊同時出發,F3 並行分析斷言照這個手法 | HIT |
