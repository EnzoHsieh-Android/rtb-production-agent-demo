---
type: verification
status: pass
date: 2026-09-25
valid_under: "docs-readme 工作樹；系統 Python 3.14.6 與 Pillow 12.2.0；現有流程節點對照 main 的 src/rtb/demo/flow.py；灰色 AI 旁支依 Phase 11B 增量 2 計劃，展示頁深色 CSS 尚未上主線。"
revalidate_when: "修改 main 的流程定義、展示頁處理者深色配色、F6/F7 合約、README 流程描述、docs/assets/make_agent_flow_gif.py，或換字型與 Pillow 版本時重跑；Phase 11B 增量 2 接入分析端時、Phase 12 展示頁上主線時重驗圖與 README。"
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/README流程動圖_計劃]]"
---
# README流程動圖驗證

2026-09-24 在 docs-readme 工作樹用系統 Python 3.14.6 與 Pillow 12.2.0 重畫 GIF 與 SVG。產圖腳本移至 `docs/assets/make_agent_flow_gif.py`；它是文件產出物工具，不在 pyproject 的 mypy 掃描路徑 `src`、`tools`，也沒有行內 `# mypy:` 例外。以不存在的 `--font` 路徑執行，明確報錯並以代碼 2 退出。

2026-09-25 依第 2 輪審查重畫後，以 Pillow 抽出 `/tmp/rtb-readme-flow-review-r2/` 的第 1、7、15–20 格逐張目視：送出建議後的灰色 AI 說明是旁支，註明送件後供確認者參考；平台回覆有獨立的向下寫入、向上核對箭頭；F7 回線由佇列下方進入，沒有貼著收件檢查；五條人工路徑的箭頭尖端露在節點與徽章之外，人工目前步驟是青色亮框，與黃色虛線不同。SVG 的對應座標與箭頭由同一支腳本產生，另以標準庫 XML 解析確認 5 條人工路徑帶箭頭、AI 說明註記存在，並核對箭頭終點在節點框外。`/Users/enzo/rtb-production-agent-demo/.venv/bin/python` 執行 `ruff check .` 通過、`mypy` 回報 73 支原始檔無錯、`pytest -q tests/tools tests/test_static_checks.py` 為 305 passed。SVG 未取得瀏覽器截圖；Phase 12 展示頁上主線時照本篇 revalidate_when 在頁面中目視重驗。

2026-09-25 第 3 輪審查後修正並重驗(編排者):先前那句「人工目前步驟是青色亮框」對當時的產物不成立——調色盤只取自圖例格,青色被併成程式藍。改成全部影格共用一張從所有影格取出的調色盤後重畫,逐格數青色像素:第 0–18 格都有(第 15、16 格各約 2000 點,人工核可與回頭線為青色),第 19 格圖例格為 0,符合預期;目視第 15 格確認人工核可為青框。F7 回頭線改從佇列右下方進入、佇列往多次未啟動改為直下,兩條不再疊在一起;AI 旁支註記移到 AI 節點下方、用詞統一為「核可者」。
