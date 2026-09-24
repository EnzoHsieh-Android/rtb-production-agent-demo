severity: major

### 1. 任一重讀吻合第一輪仍可能把變動中的資料標成穩定
severity: major
blocking: 是
引句:「有一輪一致就標穩定;都不一致就標不穩定,照樣回」

file: `src/rtb/ops/metrics.py:797`  
file: `src/rtb/ops/metrics.py:666`  
file: `src/rtb/ops/metrics.py:918`

觸發情境：三輪端到端摘要依序為 A→B→A。這在只增資料仍可能發生：分析端新增 follow-up 後，原鏈尾暫時不再計入而得到 B；執行端稍後補上新鏈尾終點，又恢復與 A 相同的 count、p50、p95、max 與前三個 exemplar。因端到端比較的是有損摘要，不是完整輸入，新舊任務不同也可能得到完全相同的摘要。

錯誤行為：第三輪等於第一輪時，程式回傳 `stable=True`；命令列因此回 0，而不是不穩定的結束代碼 5，儘管第二輪已證明讀取期間資料曾影響端到端結果。

建議修法：兩次重讀都必須與第一輪相同才標穩定，也就是固定完成三輪後要求 `second == first and third == first`；補 A→A→B 與 A→B→A 兩個測試，兩者都應回 `stable=False`、結束代碼 5。