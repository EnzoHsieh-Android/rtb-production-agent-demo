---
type: verification
status: pass
date: 2026-09-25
valid_under: readme-phase13 工作樹；Phase 13 四個增量已上主線；展示與評估錄製批次已入庫；圖對照展示流程定義、實際展示驅動與 F6/F7 回佇列路徑，配色對照深色展示頁。
revalidate_when: 展示流程定義或實際展示驅動的 AI 調查、說明與假說時點改變，F6/F7 合約、展示頁深色配色、README 流程描述、docs/assets/make_agent_flow_gif.py、字型或 Pillow 版本改變時，重跑產圖、抽格及 SVG 解析。
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/README流程動圖_計劃]]"
---
# README流程動圖驗證

## 2026-09-26 補分析端只讀平台（現行圖）

「蒐集資料」往下一條平台色虛線、標「只讀」，接到平台列新加的「Mock DSP 讀現況／指標」；執行端寫入平台的實線不變。用系統 Python 3.14.6、Pillow 12.2.0、Hiragino Sans GB 重畫：GIF 1800×760、21 格（多一格只讀說明），抽「蒐集資料」高亮那一格（第 2 格，只讀線保持平台色）與「只讀平台格」高亮那一格（第 15 格）目視（格數從 1 起算，跟下一節同一套數法）；最後兩格（人工重放、核可）黃色高亮像素各 5113 點，不為 0；SVG 用標準庫 XML 解析通過；Ruff 檢查產圖腳本通過。下一節是前一版的紀錄，GIF 格數等數字以本節為準。

## 2026-09-25 Phase 13 重畫（前一版）

現行 GIF/SVG 已對照 Phase 13 四個增量上主線後的展示流程定義、實際展示驅動與深色展示配色重畫。分析泳道標明程式初篩、AI 選查詢回蒐證或下結論、失敗改由程式規則；展示驅動在情境結束後為已送出的提案產生給人看的說明，告警假說另行處理；F6/F7 回佇列重查。展示流程定義中的可選「AI 說明」節點位於提案與送件之間，圖的虛線旁支表示實際展示時點，文字均不進決策。下方「灰色 AI 旁支」「展示頁尚未上主線」與其驗證結果是歷史記錄，不能當現況；現行有效前提以開頭欄位與下文「環境基準」段為準,重驗入口以開頭欄位為準。

本次用系統 Python 執行產圖腳本成功；GIF 為 1800×760、20 格，抽格目視 AI 查詢回線、規則退回線、F6/F7 回線及人工高亮；SVG 可由標準庫 XML 解析，且兩種圖共用節點與路徑資料。README 的 21 個相對連結逐一檢查皆存在；Ruff 檢查產圖腳本、`pytest -q tests/test_static_wiring.py`（27 個）、兩篇圖譜 lint 與 `lumos doctor` 均通過。回頭條件見開頭的 `revalidate_when`。

環境基準（補開頭 valid_under 缺的；lumos 沒有改 valid_under 的指令，環境與版本以本段為準）：系統 Python 3.14.6、Pillow 12.2.0、字型 /System/Library/Fonts/Hiragino Sans GB.ttc；同一環境重畫出的 GIF 與已提交的逐位元組相同。開頭寫的「readme-phase13 工作樹」指的是這次合入主線的 README 重寫提交，合併後以主線提交為準。

2026-09-24 在 docs-readme 工作樹用系統 Python 3.14.6 與 Pillow 12.2.0 重畫 GIF 與 SVG。產圖腳本移至 `docs/assets/make_agent_flow_gif.py`；它是文件產出物工具，不在 pyproject 的 mypy 掃描路徑 `src`、`tools`，也沒有行內 `# mypy:` 例外。以不存在的 `--font` 路徑執行，明確報錯並以代碼 2 退出。

2026-09-25 依第 2 輪審查重畫後，以 Pillow 抽出 `/tmp/rtb-readme-flow-review-r2/` 的第 1、7、15–20 格逐張目視：送出建議後的灰色 AI 說明是旁支，註明送件後供確認者參考；平台回覆有獨立的向下寫入、向上核對箭頭；F7 回線由佇列下方進入，沒有貼著收件檢查；五條人工路徑的箭頭尖端露在節點與徽章之外，人工目前步驟是青色亮框，與黃色虛線不同。SVG 的對應座標與箭頭由同一支腳本產生，另以標準庫 XML 解析確認 5 條人工路徑帶箭頭、AI 說明註記存在，並核對箭頭終點在節點框外。`/Users/enzo/rtb-production-agent-demo/.venv/bin/python` 執行 `ruff check .` 通過、`mypy` 回報 73 支原始檔無錯、`pytest -q tests/tools tests/test_static_checks.py` 為 305 passed。SVG 未取得瀏覽器截圖；Phase 12 展示頁上主線時照本篇 revalidate_when 在頁面中目視重驗。

2026-09-25 第 3 輪審查後修正並重驗(編排者):先前那句「人工目前步驟是青色亮框」對當時的產物不成立——調色盤只取自圖例格,青色被併成程式藍。改成全部影格共用一張從所有影格取出的調色盤後重畫,逐格數青色像素:第 0–18 格都有(第 15、16 格各約 2000 點,人工核可與回頭線為青色),第 19 格圖例格為 0,符合預期;目視第 15 格確認人工核可為青框。F7 回頭線改從佇列右下方進入、佇列往多次未啟動改為直下,兩條不再疊在一起;AI 旁支註記移到 AI 節點下方、用詞統一為「核可者」。
