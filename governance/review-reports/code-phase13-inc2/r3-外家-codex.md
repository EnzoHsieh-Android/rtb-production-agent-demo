severity: major

x1 已驗:理由會先檢查能否編成 UTF-8，孤立代理字元會被判為選項外答案。  
x2 已驗:不可列印的非 BMP 字元會寫成兩段 UTF-16 代理對跳脫，JSON 讀回可還原原字元。

## 發現 1:記錄呼叫次數時收到停止訊號，仍會呼叫模型
severity: major
blocking: 是

引句:「+        if inputs.context.begin_call(inv.MAX_ROUNDS) > inv.MAX_ROUNDS:」

最後一次停止旗標檢查發生在 `begin_call` **之前**。記次要另開寫入交易，可能等資料庫鎖；若這段期間收到停止訊號，記次回來後便直接呼叫模型，沒有再查旗標。這違反「呼叫模型前已收到停止就不呼叫、不寫入」的 [S1161]。file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:730`、`src/rtb/analyzer/ai_judge.py:109`、`src/rtb/analyzer/task_store.py:901`

例子：組完提示時停止旗標為假 → `begin_call` 等鎖期間旗標變真 → 預期丟 `RenewalSkipped`，模型呼叫數為 0，這一步不寫入；實際記下呼叫次數後仍執行 `complete`，並可能提交結論。

重現方式：用假 `AiContext.begin_call` 在回傳 `1` 前設停止旗標，假 `complete` 只累計呼叫次數；給 `Judge` 合格的基本證據與 `stop_requested`。檢查 `complete` 仍被呼叫。此重現依唯讀程式碼推理，未在沙盒執行。

1 條,blocking 1。