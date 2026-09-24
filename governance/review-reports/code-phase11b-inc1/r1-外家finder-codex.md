severity: major

月界線會用不同月份判上限與記帳，可能突破每月 20 美元硬上限
severity: major
blocking: 是
引句:「for_demo, in_month = _used(conn, request.demo_id, _utc_now().strftime("%Y-%m"))」
判斷月份取一次時鐘，但 `_insert_reservation` 又取一次；若剛好跨 UTC 月界，會用上個月餘額判斷，卻把獲准預留寫入新月份。見 `src/rtb/modelclient.py:938`、`src/rtb/modelclient.py:883`，違反 S902/S903：`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md:226`。
重現: 在複本用假後端，把月上限縮成一筆預留額，先於 9 月記滿，再讓下一筆檢查時間為 8 月、寫入時間為 9 月；後端共被呼叫 2 次，輸出 `month_cap: 19694400`、`september_used: 39388800`、`cap_exceeded: True`，新月份已用成為上限兩倍。
建議: 在交易開始時只取一次 `now` 與 `month`，將同一值傳給 `_used`、`_insert_reservation` 及拒絕列；補一個跨 UTC 月界的時鐘序列測試。

並行錄製的先查後寫不是原子操作，會重複付費且把已送出的呼叫誤報成設定錯誤
severity: major
blocking: 是
引句:「existing = _load_recording(path)」
錄製檔檢查位於送出前，但真正建立檔案在模型呼叫及結算後；兩個行程可同時看到不存在、各自預留並送出，第二個直到寫檔才得到 `RecordingConflict`。見 `src/rtb/modelclient.py:1123`、`src/rtb/modelclient.py:1143`，破壞 S931：`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md:255`。
重現: 在複本以 barrier 讓兩個執行緒同批、同鍵並行，假後端收到 2 次呼叫、花費帳有 2 筆，只有 1 個錄製檔；結果為一個 `ModelResult` 和一個 `RecordingConflict`。後者發生在已送出及結算之後，卻沒有實際結算資訊。
建議: 送出前以 SQLite claim 表或原子鎖檔取得 `(key, batch)` 所有權；輸家等待持有者完成後重播。不同批次也必須在同一原子 claim 中拒絕，不能等模型已回來才報衝突。

未開錄製時沒有同批去重，相同七欄輸入仍會重複呼叫與計費
severity: major
blocking: 是
引句:「result = mc.call_model(request, self._settings, recordings_dir=self._recordings,」
`ModelCandidate` 每個情境都直接呼叫；唯一的共用機制藏在 `settings.record` 分支，因此一般即時模式不遵守 S933。見 `src/rtb/eval/model_candidate.py:128`、`src/rtb/modelclient.py:1123`；合約要求與錄製開關無關：`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md:264`。
重現: 在複本用假後端跑完整固定子集、關閉錄製；210 個情境只有 209 個不同提示，但 `backend_calls` 是 210，花費帳也逐次記帳。現有 S933 測試只覆蓋 `record_too=True`，所以對應官方測試仍通過。
建議: 在評估批次層以錄製鍵維護結果／失敗快取，無論是否錄製都只送出一次；後續情境產生 `shared=True` 的旁路列。並行安全應與上一條共用原子 claim。

登入檢查與正式呼叫各拿完整時限，單次請求會超過呼叫者總期限
severity: major
blocking: 是
引句:「self.check_login()」
首次呼叫先給登入檢查固定 10 秒，接著又把完整的 `call.timeout_seconds` 給模型子行程，沒有扣除已耗時間。見 `src/rtb/modelclient.py:765`、`src/rtb/modelclient.py:775`；設計明定登入檢查要算進入口總期限：`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md:96`。
重現: 假 `claude` 的登入與正式回應各睡 0.15 秒，請求期限設 0.2 秒；呼叫仍成功回傳，實測總耗時約 0.59 秒，接近宣告期限三倍。
建議: 入口建立單一 monotonic deadline；登入檢查使用 `min(10, remaining)`，正式呼叫只取得剩餘時間，剩餘為零立即結束且清理行程群組。

永久帳檔錯誤沒有旁路紀錄，評估退回規則後反而因空清單崩潰
severity: major
blocking: 是
引句:「attempt = candidate.attempts[-1]」
候選只替 `ModelCallFailed` 追加 attempt；SQLite 永久錯誤等其他例外會被既有路由吞掉並退回規則，但 `run_subset` 無條件讀取最後一筆 attempt，造成 `IndexError`。見 `src/rtb/eval/model_candidate.py:133`、`src/rtb/eval/model_candidate.py:263`，違反 S908：`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md:232`。
重現: 在複本把帳檔路徑指向既有目錄，SQLite 產生永久性開檔錯誤；假後端未被呼叫、`attempts` 為 0，最終對外拋出 `IndexError: list index out of range`，沒有正常報告或保守停止。
建議: 讓模型邊界把所有預期的帳檔／錄製 I/O 故障轉成明確結果類別並先追加 attempt；`run_subset` 也應驗證本輪確實新增一筆，缺失時產生「無法可靠分類」並停止，而不是索引舊資料或空清單。

5 條,blocking 5。