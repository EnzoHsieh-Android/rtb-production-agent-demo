---
type: issue
status: resolved
created: 2026-09-27
updated: 2026-09-27
aliases: []
about_code:
  - src/rtb/demo/flow.py
  - src/rtb/analyzer/narrate.py
tags:
  - type/issue
  - status/resolved
summary: |-
  FLAG: 展示流程圖「選擇判法」仍有 Phase 10 候選判斷分支(請 AI 提供參考判斷 → 候選判斷),雖從未走到、收在「這次沒走的分支」,但與「AI 不參與決定」矛盾;提案說明(AI)只拿到基本三筆證據,看不到規則輪讀的四種查詢,說明文字會寫「歷史異動、過去調整、較長時間窗、每日趨勢內容為空」,誤導讀者。
  DECISION: 部分處理(2026-09-27,協調者交辦的最小修法,代使用者裁定;依據 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈使用者裁定〉8):流程圖撤掉 Phase 10 候選判斷分支(直接移除,不改名留著);提案說明只列、只描述實際拿到的證據,四種查詢收據不送也不列,提示明講沒列出的證據不要提。沒選「把四種收據加進說明輸入」:那要動證據白名單與既有的提示注入安全測試,本次不做。回頭的事件入口:協調者用真 claude 重錄入庫展示批次 phase14-demo(指令見下方〈等重錄〉),重播測試轉綠、README 截圖重拍後才標 resolved。
  KEY: 已解決(2026-09-27):協調者用真 claude 重錄 phase14-demo 批次(phase14-demo-20260927,兩份說明錄製),入庫前檢查 F1–F6 通過、重播測試轉綠;README F1–F7 截圖以新錄製重播重拍,目視確認流程圖無候選分支、說明卡不再寫其他證據為空;README 已知問題那句拿掉。
  KEY: 2026-09-27 重拍 docs/assets/phase14/full-F*.jpg 時發現(截圖代理回報與協調者目視 F4)。
---
# 展示還留著AI參考判斷分支與說明看不到四查詢


## 要做的
- 流程圖拿掉 Phase 10 候選判斷分支(或改名標明是評估用、正式與展示都不走),對應測試與頁面說明同步。
- 提案說明的輸入補上規則輪實際讀到的四種查詢收據(或在提示中明講說明只依基本證據、不評論其他證據),重錄說明錄製。

## 處理紀錄(2026-09-27,部分處理)

### 已做
- 流程圖:撤掉「由誰判斷值不值得加預算?」分流與「請模型候選判斷」兩個節點、五條邊,換成「偏慢 → 用程式規則判斷」一條;對應清單拿掉只剩評估在用的分析端路由(十八 → 十七個);頁面短標籤、F5「轉向」、沒開 AI 時的邊界說明、分析端補點與範例資料同步。細節與防回歸見 [[Systems/一鍵展示]]、[[Systems/展示頁面]] 的〈撤 Phase 10 候選判斷分支〉。
- 提案說明:根因是說明把欄位白名單外的四種查詢收據一律標成「不可信文字,內容在下面的資料區」,資料區卻只有廣告名稱,模型就寫「歷史異動、過去調整、較長時間窗、每日趨勢內容為空」。現在只列說明實際拿到的證據,提示加一句沒列出的證據不要提、不要評論。細節見 [[Systems/分析行程流程與檢查點]]〈說明只列實際拿到的證據〉。

改前 → 改後(說明送出內容的證據段,F5 端到端的正常名稱提案):

```
改前                                                    改後
- 證據甲 campaign_state:budget=100、…                  - 證據甲 campaign_state:budget=100、…
- 證據乙 metrics:window="1h"、impressions=500、…       - 證據乙 metrics:window="1h"、impressions=500、…
- 證據丙 campaign_text:不可信文字,內容在下面的資料區   - 證據丙 campaign_text:不可信文字,內容在下面的資料區
- 證據丁 change_history:不可信文字,內容在下面的資料區
- 證據戊 past_adjustments:不可信文字,內容在下面的資料區
- 證據己 longer_window:不可信文字,內容在下面的資料區
- 證據庚 daily_trend:不可信文字,內容在下面的資料區
```

### 等重錄(沒做完,不能標 resolved)
- 說明的系統提示與送出內容都改了,錄製鍵全換:入庫展示批次 `recordings/model/phase14-demo/`(批號 phase14-demo-20260927,兩份說明錄製)全部對不上,`test_committed_demo_recordings_replay_f1_to_f6_with_narratives` 照設計是紅的,等協調者重錄。舊的 phase13-demo 是唯讀歷史、不重錄;phase13-investigation-eval 是評估調查錄製、提示沒動,不受影響。
- 重錄指令(協調者本人,用真 claude;目錄要全新、不在入庫目錄底下;錄完自動跑入庫前檢查):
  1. `PYTHONPATH=src .venv/bin/python -m rtb.demo.recordings --record --dir /tmp/rtb-rec/phase14-demo --batch-id phase14-demo-YYYYMMDD --work-dir /tmp/rtb-rec/work`
  2. 通過後刪掉 `recordings/model/phase14-demo/` 裡舊的兩份,把新目錄的錄製檔整批搬進去(同一個目錄只准一個批次)。
  3. 入庫前再檢查一次:`PYTHONPATH=src .venv/bin/python -m rtb.demo.recordings --dir recordings/model/phase14-demo --batch-id phase14-demo-YYYYMMDD --work-dir /tmp/rtb-rec/check`,再跑 `tests/demo/test_ai_demo.py`。
- 重錄後:README 的 F1–F7 截圖(`docs/assets/phase14/full-F*.jpg`)要照 [[Verification/README流程動圖驗證]] 的做法重拍,README 那一行「截圖裡說其他證據為空是已知問題」再拿掉;之後才把這篇標 resolved。


## 結案

2026-09-27 重錄與重拍完成(見摘要 KEY 已解決那行);另外代碼審 r1 正確性席指出「拿不定」原本誤對到「不調整」,已改對到「用程式規則判斷」(路由收到就退回九條再判)。
