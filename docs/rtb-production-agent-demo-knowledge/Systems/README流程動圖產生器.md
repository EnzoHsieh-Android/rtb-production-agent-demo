---
type: system
status: done
created: 2026-09-24
updated: 2026-09-25
responsibility: 重畫 README 的 GIF 與 SVG 流程圖，不參與正式分析或執行流程。
self_audit: gpt-5.6-sol/2026-09-25
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

WHY: [2026-09-25 Phase 13 主線] 先前灰色「AI 說明規劃中」旁支已誤導讀者：開啟 AI 判斷的展示流程會在程式初篩後讓 AI 選唯讀查詢或結論，查詢回蒐證、失敗改由程式規則決定；此路徑未通過正式採用門檻。給人看的說明與告警假說已接入展示，不影響決策。重畫時把這三種去向及 F6/F7 回佇列重查畫出，節點與分支以 [[Systems/一鍵展示]] 所管的展示流程定義為準，深色配色以 [[Systems/展示頁面]] 的 CSS 為準。出處：Phase 13 已合主線的程式與 [[Projects/RTB_Phase13AI參與決策_計劃]]，可重跑 `python3 docs/assets/make_agent_flow_gif.py` 並對照圖及流程定義。

展示流程定義把「AI 說明」列在提案與送件之間，作為提案旁的可選節點；實際展示驅動在情境結束後才讀已送出的提案、產生給人看的說明，告警假說也在那時獨立處理。本圖送件後的虛線旁支表現展示時點，不表示那段文字會跟提案一起進入收件或執行決策。

WHY: [2026-09-25] F6/F7 回頭線用人工色虛線，AI 查詢回頭線用 AI 色虛線，與程式主線分辨；GIF 和 SVG 共用節點與路徑定義。出處：README 代碼審第 2 輪與本次 Phase 13 README 更新。

驗證入口：`python3 docs/assets/make_agent_flow_gif.py` 重畫 GIF 與 SVG；用 `--font` 或 `RTB_FLOW_FONT` 指定字型，未指定時才用 macOS Hiragino Sans GB，找不到字型會報錯退出。產出後抽格目視、解析 SVG XML，並跑 `ruff check docs/assets/make_agent_flow_gif.py`。

PITFALL: GIF 各格共用一張調色盤；舊圖曾只從圖例格取色，把當時的「目前步驟」青色併成程式藍，人工格看起來像程式處理(README 代碼審第 3 輪)。現行圖仍把全部影格拼成一張再取色；目前高亮色依展示 CSS 為黃色。重驗：抽人工重放與核可的最後兩格，亮黃色像素都應大於 0；可用 Pillow 逐格讀取 RGB 數值。
