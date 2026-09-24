severity: major

唯讀驗收顯示，x1 的空用量會使實測失敗，x2 原本的缺列批次會被標記；指定的 b1、b2、t1、a1、s1 修正也已對照程式碼與測試。**未能實跑原重現**：執行 `cp -R /Users/enzo/rtb-11b-rev /tmp/codex-p11b-r3-external` 得到 `Operation not permitted`，因此以下兩條依程式路徑推論。實驗前後的 `ls -la ~/.rtb` 均顯示 `total 0`、只有 `.` 與 `..`；未呼叫 Claude 模型。

x2 的批次核對仍漏掉延遲，可把 p95 錯標為通過  
severity: major  
blocking: 是  
引句:「and (kept.outcome, kept.list_nanousd) != (replayed.outcome, replayed.list_nanousd)」  
file: `src/rtb/eval/record.py:194`  
file: `src/rtb/eval/model_candidate.py:386`  
file: `src/rtb/eval/model_candidate.py:400`  
重現：原報告的缺列及竄改成本案例，依新比較式會掛旗標。但若保留完整列數、結果與原價，只把有效批次檔的 `latency_ms` 從 4000 改為 100，核對不會發現差異；門檻卻從批次檔計算，p95 會由「沒過」變成「過」。沙盒禁止建立複本，未實跑此變體。  
建議：逐列核對批次與錄製回應的延遲，以及所有參與門檻計算的欄位；不一致時停止使用該批次判門檻。

s1 的 SIGHUP 清理在背景執行緒仍會留下 Claude 子行程  
severity: major  
blocking: 是  
引句:「if threading.current_thread() is not threading.main_thread():」  
file: `src/rtb/modelclaude.py:286`  
file: `src/rtb/modelclaude.py:313`  
file: `src/rtb/modelclaude.py:334`  
重現：靜態追查背景執行緒呼叫 `run_claude` 的路徑：訊號處理器在該執行緒不會安裝；主執行緒收到 SIGHUP 時仍按預設方式結束，而已啟動的 Claude 位於新工作階段，背景執行緒無法執行 `_abandon`。第二輪在主執行緒使用假 Claude 的 SIGHUP 重現路徑已獲修正；此背景執行緒變體因複本建立遭拒，未實跑。  
建議：在主執行緒建立涵蓋呼叫期間的訊號清理機制，或明確拒絕背景執行緒啟動此後端，並加入背景執行緒收到 SIGHUP 的假子行程測試。

2 條,blocking 2。