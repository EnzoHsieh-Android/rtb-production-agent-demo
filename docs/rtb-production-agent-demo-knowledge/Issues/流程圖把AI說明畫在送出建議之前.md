---
type: issue
status: open
created: 2026-09-27
updated: 2026-09-27
aliases: []
about_code:
  - src/rtb/demo/flow.py
tags:
  - type/issue
  - status/open
summary: |-
  FLAG:TECHNICAL
  DECISION: 尚未處理;2026-09-27 README 重排的事實核對發現,README 文字已先照程式改成旁支,展示流程圖與 README 動圖另案修。
  KEY: 來源=governance/review-reports/code-readme-restructure/r1-事實核對.md 第 1 條
---
# 流程圖把AI說明畫在送出建議之前


## 現象
展示流程圖與 README 動圖把「AI 寫說明」畫在「提出建議」與「送進收件口」之間;程式實際上是建議送進收件口之後,由說明命令列另外讀已送出的提案寫說明,執行端不讀、不等它(見 [[Systems/分析行程流程與檢查點]] 的說明段與一鍵展示在情境跑完後才產說明)。讀圖的人會以為 AI 在寫入的關鍵路徑上。

## 要做的
- 展示流程圖把說明節點改成送出後的旁支,對應測試與頁面說明同步。
- README 動圖產生器同步改畫,重產 GIF/SVG。
