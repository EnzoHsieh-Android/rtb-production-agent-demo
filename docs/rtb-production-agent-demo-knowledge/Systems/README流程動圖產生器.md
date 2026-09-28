---
type: system
status: done
created: 2026-09-24
updated: 2026-09-27
responsibility: 重畫 README 的 GIF 與 SVG 流程圖，不參與正式分析或執行流程。
self_audit: gpt-5.6-sol/2026-09-25
aliases: []
about_code:
  - docs/assets/make_agent_flow_gif.py
  - tests/test_readme_flow_diagram.py
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

（2026-09-25 當時的寫法，已過時：展示流程定義那時把「AI 說明」列在提案與送件之間；2026-09-28 起流程定義與本圖都改成送出後的旁支，見文末同日一段。）實際展示驅動在情境結束後才讀已送出的提案、產生給人看的說明，告警假說也在那時獨立處理；兩段文字都不進收件或執行決策。

WHY: [2026-09-25] F6/F7 回頭線用人工色虛線，AI 查詢回頭線用 AI 色虛線，與程式主線分辨；GIF 和 SVG 共用節點與路徑定義。出處：README 代碼審第 2 輪與本次 Phase 13 README 更新。

WHY: [2026-09-26] 使用者指出舊圖的「蒐集資料」沒有連到廣告平台，讀者會以為分析端只看系統內部資料；實際上分析端的 DSP 用戶端在蒐集時讀廣告現況與成效指標，AI 追加查詢也回到蒐集再讀一次(見 [[Systems/Mock-DSP]])。平台列在蒐集下方另畫一格「Mock DSP 讀現況／指標」，用平台色虛線標「只讀」，跟執行端的實線寫入分開，保住「寫入只在執行端」這個重點；不拉長線接到右邊那一格，免得橫越整張圖。展示流程定義是狀態流程、不畫呼叫關係，這條線不在裡面，是這張 README 圖自己補的。

WHY: [2026-09-27 Phase 14] 使用者要 README 圖跟正式決策路徑一致：分析端第一步 A 讀現況與 1h 成效，用 A 的資料初篩；配速偏低才續讀 B（歷史、過去調整）、C（逐日、1d／7d、重讀），由九條程式規則定案，值得加才算金額與送件。AI 決策與選查詢已退出正式及展示分析，圖只保留送件後給確認者看的提案說明，以及告警時的原因假說；兩支均不進加額決策。F6 人工重放及 F7 核可仍回佇列重驗。出處：[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈使用者裁定〉1–8、實際展示驅動與本次 README 工作；以 `python3 docs/assets/make_agent_flow_gif.py` 重畫並核對 [[Verification/README流程動圖驗證]]。

WHY: [2026-09-28] [[Issues/流程圖把AI說明畫在送出建議之前]]：舊圖把「AI 寫提案說明」放在「送出提案」正下方、夾在送出與「收件檢查」之間，主線還得繞過它，讀的人會以為提案要先經過 AI 才進收件。改成送出提案往下直接一條實線接收件檢查；AI 說明畫在待處理佇列正上方，由佇列往上一條 AI 色虛線接過去——說明命令列只讀收件收下、交給執行的提案，被拒收或過時的不會有說明（代碼審 r1 正確性 F1：初版從送出提案岔出，跟「收件收下後」的說法矛盾，且送出那一格旁支就先亮了）。AI 色虛線的旁支跟主線一樣，走到它的起點之前畫成暗色。舊註記「送件後供確認者參考，不進決策」不符事實（人工核可表單看不到說明，見 [[Systems/展示頁面]] 同日一節），格子標題、註記與 SVG 描述統一用「收件收下後」起頭：標題「收件收下後，AI 另外寫給人看的說明；執行端不讀、不等」，註記「收件收下後另寫，只給人看，執行端不讀」（註記挪到說明格上方，免得壓到往上的虛線）。出處：協調者交辦、代碼審 r1 與本次重畫，驗證見 [[Verification/README流程動圖驗證]] 同日一節。

WHY: [2026-09-28 代碼審 r1 正確性 F3] 這張圖原本沒有任何測試守著，說明被畫回主線不會翻紅。`tests/test_readme_flow_diagram.py` 用語法樹讀產生器模組層的 `EDGES`、`NOTES`、`CAPTIONS` 字面值（不匯入產生器：Pillow 不是專案依賴，匯入就得在 CI 跳過），斷言送出提案 → 收件檢查是主線、說明沒有出去的邊、進說明的只有佇列來的一條 AI 虛線，以及標題與註記都用「收件收下後」、不寫「確認者」。四種變異（畫回送出 → 說明 → 收件、旁支改回從送出岔出、註記改回舊說法、旁支改成實線）各自讓它翻紅。代價：`EDGES` 等要維持字面值寫法，改成用函式算出來時測試會先紅、要跟著改。防回歸：[test:test_the_readme_diagram_keeps_the_narrative_off_the_main_line]、[test:test_the_readme_diagram_says_the_narrative_comes_after_intake_the_same_way]。

本圖由同一份 `NODES`、`EDGES`、`CAPTIONS` 資料產 GIF 與 SVG，同一環境連續重畫兩次 GIF 與 SVG 的雜湊都相同。上方 2026-09-24 至 26 日的 Phase 13 畫法及抽格紀錄是歷史脈絡，不能當成現行圖；兩條 AI 旁支的時點依展示驅動（說明在送出之後、告警假說另外處理），流程定義 2026-09-28 起也把說明畫成送出後的旁支。

驗證入口：`python3 docs/assets/make_agent_flow_gif.py` 重畫 GIF 與 SVG；用 `--font` 或 `RTB_FLOW_FONT` 指定字型，未指定時才用 macOS Hiragino Sans GB，找不到字型會報錯退出。產出後抽格目視、解析 SVG XML，並跑 `ruff check docs/assets/make_agent_flow_gif.py`。

PITFALL: GIF 各格共用一張調色盤；舊圖曾只從圖例格取色，把當時的「目前步驟」青色併成程式藍，人工格看起來像程式處理(README 代碼審第 3 輪)。現行圖仍把全部影格拼成一張再取色；目前高亮色依展示 CSS 為黃色。重驗：抽人工重放與核可的最後兩格，亮黃色像素都應大於 0；可用 Pillow 逐格讀取 RGB 數值。


## 2026-09-28 圖上文字改白話

- WHY: 使用者反映動圖裡的敘述文字太像內部術語(A／B／C 步驟代號、1h/1d/7d、配速、收件口、執行端、憑證、Mock DSP、假說),讀的人看不懂。節點名、旁註、每格標題與 SVG 描述都改成白話;加長的字另外縮短到放得進原本的格子寬度,位置不動。說明那一格的標題與旁註一起改成「建議被收下後」開頭,守衛測試跟著改,守的仍是「兩處同一個說法、不寫確認者」。出處:本次對話使用者回饋。
