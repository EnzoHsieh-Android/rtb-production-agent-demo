---
type: issue
status: open
created: 2026-09-27
updated: 2026-09-27
aliases: []
about_code:
  - src/rtb/demo/flow.py
  - src/rtb/analyzer/narrate.py
tags:
  - type/issue
  - status/open
summary: |-
  FLAG: 展示流程圖「選擇判法」仍有 Phase 10 候選判斷分支(請 AI 提供參考判斷 → 候選判斷),雖從未走到、收在「這次沒走的分支」,但與「AI 不參與決定」矛盾;提案說明(AI)只拿到基本三筆證據,看不到規則輪讀的四種查詢,說明文字會寫「歷史異動、過去調整、較長時間窗、每日趨勢內容為空」,誤導讀者。
  DECISION: 尚未處理;Phase 14 README 與截圖更新時發現,另開一件做。
  KEY: 2026-09-27 重拍 docs/assets/phase14/full-F*.jpg 時發現(截圖代理回報與協調者目視 F4)。
---
# 展示還留著AI參考判斷分支與說明看不到四查詢


## 要做的
- 流程圖拿掉 Phase 10 候選判斷分支(或改名標明是評估用、正式與展示都不走),對應測試與頁面說明同步。
- 提案說明的輸入補上規則輪實際讀到的四種查詢收據(或在提示中明講說明只依基本證據、不評論其他證據),重錄說明錄製。
