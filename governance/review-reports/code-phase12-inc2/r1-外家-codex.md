severity: major

新一輪「全部跑一次」會顯示上次重跑的情境結果  
severity: major  
blocking: 是  
引句:「swaps = dict(self.reruns)」  
查證: `src/rtb/demo/server.py:141`、`src/rtb/demo/server.py:158`、`src/rtb/demo/server.py:161`  
重現: 唯讀追查 `state()`：先完成一次單一情境重跑，再啟動「全部跑一次」。新展示仍在跑時，舊的 `reruns` 會覆蓋新展示的同名情境；直到整輪結束，`_finished()` 才清空它。頁面因此可能把尚未跑到的情境顯示為上次的「照預期跑完」。  
建議: 啟動完整展示時停止套用舊的重跑結果，並以同一把鎖取得頁面所需的展示編號與重跑對照。

新一輪完整展示尚未查核，就顯示舊的查核通過  
severity: major  
blocking: 是  
引句:「verifier = reader.latest_verifier_run()」  
查證: `src/rtb/demo/present.py:204`、`src/rtb/demo/state_store.py:344`、`src/rtb/demo/page.py:1348`  
重現: 唯讀追查 `build_demo_state()`：新一輪完整展示開始後、自己的驗證器尚未執行前，讀取器仍取資料庫中上一輪的查核結果；頁面會顯示綠色的「自動查核通過」。雖有標出舊展示編號，這仍不是目前完整展示的查核結果。  
建議: 完整展示進行中只顯示該展示編號的查核結果；只有單一情境重跑才沿用上次完整展示的結果。

確認逾時清除後，已開始處理的 POST 仍可能簽發核可  
severity: major  
blocking: 是  
引句:「pending = reader.confirmation(run.demo_id)」  
查證: `src/rtb/demo/server.py:190`、`src/rtb/demo/server.py:202`、`src/rtb/demo/server.py:255`、`src/rtb/demo/driver.py:964`、`src/rtb/demo/driver.py:970`  
重現: 唯讀對照兩條執行緒：POST 先讀到待確認資料；F7 等待到時後清除該資料；POST 隨後繼續簽發。簽前只核對建議的決策到期時間，沒有再核對展示的確認期限或待確認資料是否仍存在。確認期限刻意早於決策到期，因此這段時間窗可以成立。  
建議: 將「仍可確認」的核對與簽章寫入放進同一個受保護的操作，並核對展示確認期限。

本席只做唯讀程式追查，未跑測試、未啟動行程。`ls ~/.rtb` 前後均為空目錄，`~/.rtb/demo-reports` 前後均不存在。前後執行 `ps` 檢查均回覆 `operation not permitted`；本席沒有啟動需清理的行程。

3 條,blocking 3。