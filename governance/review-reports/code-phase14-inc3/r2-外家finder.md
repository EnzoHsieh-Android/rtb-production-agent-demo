severity: major

## 第 1 輪修正驗收

增量 diff 已納入 F3 規則輪蒐證、F4/F6 原任務提案說明、按分析資料庫提案數驗錄製、舊版 AI 決定標示、F7 的 300 秒等待、共用暫存帳本，以及移除無入口的 AI 流程程式。靜態追查顯示正式 runner 固定使用規則輪，`Judge` 的呼叫留在評估執行器；未找到模型輸出通往正式提案決定的入口。

## 發現 1: 新增的百分比核對可被相容百分號繞過
severity: major
blocking: 是

引句:「_PERCENT_SIGN = re.compile(r"\s*[%\uff05]")」

F5 的曝光數恰好是 500。這次修正會刪掉模型說的「加 500%」，但外觀同為百分號的 `﹪`（U+FE6A）不在核對範圍內；「請把預算加 500﹪」於是借曝光數 500 通過，仍可在給核可人看的說明中主張與 10% 提案矛盾的加額。程式提案金額不受影響，但 [S1427] 要求說明不得主張 500% 加額。file: `src/rtb/modelclient.py:223`；file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase14正式規則照九條判斷_計劃.md:357`

最小重現，僅呼叫純文字核對函式，沒有呼叫模型或寫帳本：

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -c 'from rtb.modelclient import traceable_sentences; e="impressions=500; budget +10%"; print(traceable_sentences("請把預算加 500﹪。", e)); print(traceable_sentences("請把預算加 500%。", e))'
('請把預算加 500﹪。', 0)
('', 1)
```

請在判斷百分比前處理相容百分號，並以 F5 的曝光數 500 驗證這種寫法也會整句移除。

## 圖譜固定席逐項判讀

- **模型用戶端、分析行程流程與檢查點**：正式提案仍由規則決定，F5 的未授權工具呼叫合約未見被破壞；發現 1 影響模型說明的數字核對。模型用戶端筆記已記半形、全形百分號的修正，尚未涵蓋這個輸入。file: `docs/rtb-production-agent-demo-knowledge/Systems/模型用戶端.md:273`
- **正式九條判斷領域規則、任務流程領域模型、確定性指標計算、評估與Jev決策點**：本輪修正未把模型結果接回規則輪；證據新鮮度、狀態轉換與評估直接呼叫 `Judge` 的路徑，靜態檢查未見相反接線。
- **Mock-DSP、共用行程基礎、執行迴圈、提案收件口**：本輪增量未改其寫入、冪等鍵、租約或核可實作；未見這些合約因增量修正而改變。
- **一鍵展示、展示頁面、追蹤檢視、服務水準與燒損告警**：舊展示紀錄的決策者已按儲存值呈現；說明與假說仍是展示模型入口。發現 1 會影響展示的提案說明，其餘固定席宣稱未見被本輪修正破壞。

本次唯讀沙箱沒有可用暫存目錄，pytest 在啟動擷取輸出時即失敗，故未宣稱測試通過；上述發現已用不寫檔的最小指令重現。