---
type: system
status: done
created: 2026-09-24
updated: 2026-09-24
responsibility: 重畫 README 的 GIF 與 SVG 流程圖，不參與正式分析或執行流程。
aliases: []
about_code:
  - docs/assets/make_agent_flow_gif.py
tags:
  - type/system
  - status/done
summary: |-
  WHY: [2026-09-24] 使用者裁定 README 產圖腳本只作文件產出物，移到 docs/assets，不增加正式或開發依賴。出處：README 代碼審第 1 輪後的使用者裁定。
  TEST: python3 docs/assets/make_agent_flow_gif.py；ruff check .；mypy；pytest -q tests/tools tests/test_static_checks.py；GIF 抽格及 SVG XML 解析。
verified_by:
  - "[[Verification/README流程動圖驗證]]"
---
# README流程動圖產生器

WHY: [2026-09-24] 使用者裁定產圖腳本移到 `docs/assets/make_agent_flow_gif.py`，作為文件產出物工具，不列入 `tools/`、mypy 掃描與正式工具規範；用系統 Python 和 Pillow 12 重畫，不增加專案開發依賴。出處：README 代碼審第 1 輪後的使用者裁定。

圖中的流程節點鍵對照 [[Systems/一鍵展示]] 所管的 main 流程定義；`a_propose` 到 `a_submit` 的「直接送出」是主線，`a_narrate` 灰階標示為 11B 增量 2 規劃中、未接入。F6 人工逐筆重放與 F7 超額核可分別依 [[Systems/提案收件口]]、[[Systems/執行迴圈]] 的合約。

驗證入口：`python3 docs/assets/make_agent_flow_gif.py` 重畫 GIF 與 SVG；用 `--font` 或 `RTB_FLOW_FONT` 指定字型，未指定時才用 macOS Hiragino Sans GB，找不到字型會報錯退出。執行 Ruff、mypy、`tests/tools`、`tests/test_static_checks.py`，前者抽格目視，後者用 XML 解析。
