---
type: issue
status: open
created: 2026-09-24
updated: 2026-09-24
aliases: []
about_code:
  - src/rtb/executor/runner.py
tags:
  - type/issue
  - status/open
summary: |-
  FLAG: 執行迴圈沒有裝 SIGTERM 處理:一鍵展示的啟動器 stop() 先送 SIGTERM,執行迴圈直接結束,`_serve` 的 finally(結束前補寫欠著的 DSP 呼叫紀錄、照實印出少記幾列)不會跑。以前的測試都用 SIGKILL 停,這個缺口沒露出來(Phase 12 代碼審 r1 a1,架構對齊席)。
  DECISION: 協調者 2026-09-24 同意先記下、另案處理,不在 Phase 12 增量 1 改執行迴圈:改動落在正式執行端,要走執行迴圈自己的代碼審;展示的資料每次重建,少記的呼叫紀錄不影響情境斷言(斷言看平台真實狀態與嘗試紀錄,不看呼叫紀錄)。
  KEY: 修法方向:比照分析端驅動命令列,訊號處理器只設普通旗標、主迴圈每輪與每次休息之後檢查,讓 finally 照常跑。防回歸要一支子行程送 SIGTERM、斷言補寫有跑的測試。
---
# 執行迴圈收到SIGTERM等於硬殺

見摘要。相關:[[Systems/執行迴圈]]、[[Systems/一鍵展示]]、[[Projects/RTB_Phase12一鍵展示與HTML報告_計劃]]。

REVISIT:2026-12-31 執行迴圈改走正式啟動器(不只展示)、或呼叫紀錄開始用在任何斷言或指標時,照 KEY 的方向修掉並補測試。
