---
type: project
status: done
created: 2026-09-24
updated: 2026-09-24
tags:
  - type/project
  - status/done
lands_in:
  - "[[Systems/README流程動圖產生器]]"
---
# README流程動圖_計劃

PRIOR-ART: 最小解是借用 Phase 12 展示頁的流程節點與 CSS 處理者配色，使用 Pillow 輸出 GitHub 可播的 GIF，同源資料另輸出 SVG；不新增正式服務或依賴。
RETIRE-IF: README 不再展示逐步流程，或展示頁處理者語言改版且此圖無法同步時，撤掉此產圖腳本與產物，改引用新的單一圖源。

這次只改說明文件與離線產圖工具，不更動 RTB 執行行為；屬低風險文件改動，跳過設計審迴圈。圖上的主線依 Phase 12 展示頁流程節點；F6 的人工逐筆重放與 F7 的人工核可依 [[Systems/提案收件口]]、[[Systems/執行迴圈]] 合約。README 明寫 AI 說明是展示頁的可選步驟，此分支正式分析流程尚未接入。

驗證：`python3 tools/make_agent_flow_gif.py`、`ruff check tools/make_agent_flow_gif.py`；以 Pillow 確認 22 格、22 秒與檔案大小，抽看首格、中段、末格及 F6／F7 分支。SVG 以 XML 解析確認檔案有效。
