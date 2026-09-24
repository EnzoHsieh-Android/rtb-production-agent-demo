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

PRIOR-ART: 最小解是借用 main 的 `src/rtb/demo/flow.py` 節點與展示頁深色主題的處理者配色，以系統 Python 和 Pillow 12 離線產出 GIF 與 SVG；Pillow 不列入專案開發依賴，產圖腳本不算正式工具。
RETIRE-IF: README 不再展示逐步流程，或展示頁處理者語言改版且此圖無法同步時，撤掉此產圖腳本與產物，改引用新的單一圖源。

這次只改說明文件與 `docs/assets/` 的離線產圖工具，不更動 RTB 執行行為；屬低風險文件改動，跳過設計審迴圈。圖上的節點鍵盡量沿用 main 的流程定義，主線走 `a_propose` 到 `a_submit` 的「直接送出」邊。`a_narrate` 灰階標為 Phase 11B 增量 2 規劃中、未接入。F6 的人工逐筆重放與 F7 的人工核可依 [[Systems/提案收件口]]、[[Systems/執行迴圈]] 合約。

驗證入口：`python3 docs/assets/make_agent_flow_gif.py` 重畫；再執行 Ruff、mypy、`tests/tools` 與 `tests/test_static_checks.py`，以 Pillow 抽看 GIF 起點、主線、F6、F7 與末格，並用 XML 解析 SVG。實際結果記在 [[Verification/README流程動圖驗證]]。
