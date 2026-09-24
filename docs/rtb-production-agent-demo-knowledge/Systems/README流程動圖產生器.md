---
type: system
status: done
created: 2026-09-24
updated: 2026-09-24
responsibility: 重畫 README 的 GIF 與 SVG 流程圖，不參與正式分析或執行流程。
aliases: []
about_code:
  - tools/make_agent_flow_gif.py
tags:
  - type/system
  - status/done
summary: |-
  WHY: [2026-09-24] 使用者要求 README 動圖沿用展示頁處理者視覺語言，並可重畫兩種資產；產圖工具放 tools，避免接入正式服務。出處：本次 README 流程動圖需求。
  TEST: python3 tools/make_agent_flow_gif.py；ruff check tools/make_agent_flow_gif.py；抽查 GIF 關鍵格及 SVG XML。
verified_by:
  - "[[Verification/README流程動圖驗證]]"
---
# README流程動圖產生器

WHY: [2026-09-24] 使用者指定 README 動圖須延用展示頁的程式、AI、人工與外部平台配色與線型，且可用系統 Python 和 Pillow 重畫；產圖工具因此放在 `tools/make_agent_flow_gif.py`，不接正式分析或執行流程。出處：本次 README 流程動圖需求。

圖中的 AI 寫說明以 Phase 12 展示頁的可選流程為準，F6 人工逐筆重放與 F7 超額核可分別依 [[Systems/提案收件口]]、[[Systems/執行迴圈]] 的合約。AI 步驟不等於此分支正式分析流程已接入模型說明；README 會直接告知讀者這個版本差異。

驗證入口：`python3 tools/make_agent_flow_gif.py` 重畫兩個資產，`ruff check tools/make_agent_flow_gif.py` 檢查腳本。產物是 docs/assets/agent-flow.gif 與 docs/assets/agent-flow.svg，以程式碼為準；前者抽格目視，後者用 XML 解析確認有效。
