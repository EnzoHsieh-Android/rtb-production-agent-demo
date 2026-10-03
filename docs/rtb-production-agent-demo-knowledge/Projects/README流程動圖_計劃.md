---
type: project
status: done
created: 2026-09-24
updated: 2026-10-02
tags:
  - type/project
  - status/done
lands_in:
  - Systems/README流程動圖產生器
---
# README流程動圖_計劃

PRIOR-ART: 最小解是借用 main 的 `src/rtb/demo/flow.py` 節點與展示頁深色主題的處理者配色，以系統 Python 和 Pillow 12 離線產出 GIF 與 SVG；Pillow 不列入專案開發依賴，產圖腳本不算正式工具。（2026-10-02 更正：產生器 2026-09-25 晚間改寫後用自己的 `NODES`，不匯入也不沿用 flow.py 的節點鍵，見 [[Systems/README流程動圖產生器]]）
RETIRE-IF: README 不再展示逐步流程，或展示頁處理者語言改版且此圖無法同步時，撤掉此產圖腳本與產物，改引用新的單一圖源。

這次只改說明文件與 `docs/assets/` 的離線產圖工具，不更動 RTB 執行行為；屬低風險文件改動，跳過設計審迴圈。圖上的現有節點鍵盡量沿用 main 的流程定義，主線走 `a_propose` 到 `a_submit` 的「直接送出」邊。`a_narrate` 灰階標為 Phase 11B 增量 2 規劃中，依 11B 計劃放在送件後的參考旁支。（2026-10-02 更正：現況 AI 說明已接上，圖上畫成收件收下後由待處理佇列岔出的 AI 色旁支，不再灰階標規劃中；節點鍵也改成產生器自己的，見 [[Systems/README流程動圖產生器]]）F6 的人工逐筆重放與 F7 的人工核可依 [[Systems/提案收件口]]、[[Systems/執行迴圈]] 合約。

WHY: [2026-09-25] 產出物工具不寫自動測試：它只在文件改版時手動重畫，輸出價值是人能否辨清箭頭、徽章與文字；對同一套座標再寫像素斷言只會複製實作，無法證明圖意正確。（已被取代：2026-09-28 代碼審 r1 正確性 F3 起加了 `tests/test_readme_flow_diagram.py`，用語法樹讀產生器的 EDGES、NOTES、CAPTIONS 守「AI 說明不准畫回主線」，見 [[Issues/流程圖把AI說明畫在送出建議之前]]；仍不做像素斷言）這輪以指定 GIF 影格目視、SVG XML 與箭頭終點座標核對，加上 Ruff、mypy、現有工具測試驗證；SVG 的頁面目視留到 Phase 12 展示頁上主線時。（2026-10-02：條件已成立——Phase 12 已做完；但 [[Verification/README流程動圖驗證]] 裡 SVG 仍只有 XML 解析，沒有頁面目視的紀錄）Phase 11B 增量 2 接入分析端或 Phase 12 頁面上主線時由 [[Verification/README流程動圖驗證]] 的 revalidate_when 觸發重驗。出處：README 代碼審第 2 輪使用者裁定。

驗證入口：`python3 docs/assets/make_agent_flow_gif.py` 重畫；再執行 Ruff、mypy、`tests/tools` 與 `tests/test_static_checks.py`（2026-10-02 更正：另加 `tests/test_readme_flow_diagram.py`），以 Pillow 抽看 GIF 起點、主線、F6、F7 與末格，並用 XML 解析 SVG。實際結果記在 [[Verification/README流程動圖驗證]]。
