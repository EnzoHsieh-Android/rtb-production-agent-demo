---
type: system
status: done
created: 2026-09-24
updated: 2026-09-25
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

圖中的現有流程節點鍵對照 [[Systems/一鍵展示]] 所管的 main 流程定義；`a_propose` 到 `a_submit` 的「直接送出」是主線，`a_narrate` 灰階標示為送件後供核可者參考的 11B 增量 2 規劃旁支，位置依 11B 計劃，不照展示流程節點表的先後。F6 人工逐筆重放與 F7 超額核可分別依 [[Systems/提案收件口]]、[[Systems/執行迴圈]] 的合約。

WHY: [2026-09-25] F6/F7 回頭線刻意用人工描邊色，以便跟藍色主線分辨；線型仍用 6 4 虛線，規劃 AI 節點用 6 2 虛線，GIF 和 SVG 共用常數。出處：README 代碼審第 2 輪 9 條 minor 的修正。

驗證入口：`python3 docs/assets/make_agent_flow_gif.py` 重畫 GIF 與 SVG；用 `--font` 或 `RTB_FLOW_FONT` 指定字型，未指定時才用 macOS Hiragino Sans GB，找不到字型會報錯退出。執行 Ruff、mypy、`tests/tools`、`tests/test_static_checks.py`，前者抽格目視，後者用 XML 解析。

PITFALL: GIF 各格共用一張調色盤;只從圖例那一格取色會把「目前步驟」的青色併成程式藍,人工那幾格看起來像程式處理(README 代碼審第 3 輪)。調色盤改由全部影格拼成一張再取。重驗:抽第 15–18 格數青色像素,應大於 0。
