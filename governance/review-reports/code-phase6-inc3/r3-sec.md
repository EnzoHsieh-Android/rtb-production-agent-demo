severity: major

### 1. 待核可提案不計入收件口的待處理上限,可用來灌爆佇列做阻斷式服務
severity: major
blocking: 是 這條繞過了收件口原本用來擋佇列爆量的節流閥(`_max_pending`,預設只有 8),讓待核可提案最多能囤到 `MAX_ROWS`(5000)筆,期間還會逐輪拖慢執行迴圈,屬於「讓待核可卡死或系統停擺」的可利用漏洞。
引句:「AWAITING = "state = 'pending' AND disposition = 'awaiting_approval'"」

說明(攻擊者角度):
- 收件口原本的佇列節流是 `_check_capacity()` 用 `PENDING`(`state = 'pending' AND disposition IS NULL`)去數「還沒處置的待處理提案」,超過 `_max_pending`(預設 `DEFAULT_MAX_PENDING = 8`)就丟 `InboxFull` 拒收。這是刻意設得很小的背壓閥,用來擋佇列爆量。
- 這次新增的「待核可」處置(`Disposition.AWAITING_APPROVAL`)把提案寫成 `state='pending', disposition='awaiting_approval'`,`disposition` 不是 `NULL`,所以永遠不計進 `PENDING`、不計進 `_check_capacity()` 的節流閥,只受得到 `total &gt;= MAX_ROWS`(5000)這道全庫總列數上限的攔阻——比原本的節流閥寬鬆超過 600 倍。
- `OPEN`(保留期清除、判斷任務是否結束用)這條巨集有跟著把 `AWAITING` 算進「還沒結束」,但 `_check_capacity()` 用的 `PENDING` 巨集完全沒被這份 patch 碰到,兩處語意就此脫節——這正是作者沒注意到的地方。
- 已在暫存副本用 `InboxStore(max_pending=2)` 實測驗證(未改動 repo 任何檔案,程式見 `/private/tmp/.../scratchpad/p6i3sec/exp1.py`):連續建立 10 筆分屬不同 `task_id`、處置為 `awaiting_approval` 的提案,收件口完全不擋;之後正常 `pending` 提案仍準確在第 3 筆(超過 `max_pending=2`)被 `InboxFull` 擋下——證實待核可提案是「額外」量體,不佔用原本的節流名額,兩者互不干擾地各自累加。
- 後果:任何能讓提案觸發「總曝險已滿」或「比例過大」而進入待核可的來源(例如同一多租戶環境下某個惡意/失控租戶自己名下大量廣告持續送出超比例或超總曝險的加預算提案),可以不斷用不同 `task_id` 把待核可佇列灌到接近 `MAX_ROWS`。一來會讓收件口對所有租戶的新提案(含正常提案)都回 `InboxFull`,造成全系統阻斷;二來 `runner.py` 的 `_loop()` 每一輪都先呼叫 `executor.process_awaiting()`,而它固定用 `self.store.awaiting(tx)` 一次讀出**全部**待核可列逐筆處理再輪到 `process_one()`,待核可堆積越大,每輪掃描與逐筆交易的開銷就越大,新提案的處理速度會被拖垮,即「系統停擺」。
- 由於待核可提案的生命週期上限是 `decision_expires_at`(最長 1 小時)而非租約(`VISIBILITY_TIMEOUT`,短很多),攻擊者只要維持送件速率高於到期釋放速率,就能把這個堆積長期撐住,不需要一次性瞬間灌爆。

file: `src/rtb/executor/inbox_store.py:148`(`PENDING` 定義,`_check_capacity` 判斷用這條,未把 `awaiting_approval` 算進去)
file: `src/rtb/executor/inbox_store.py:507`(`_check_capacity`,只查 `PENDING` 與 `total &gt;= MAX_ROWS`,這份 patch 未觸碰這個函式)
file: `src/rtb/executor/inbox_store.py:44`(`DEFAULT_MAX_PENDING = 8`,原本的節流閥)
file: `src/rtb/executor/inbox_store.py:46`(`MAX_ROWS = 5000`,唯一還擋得住待核可提案的上限)
file: `src/rtb/executor/runner.py`(`_loop()` 每輪先 `executor.process_awaiting()` 再 `executor.process_one()`,待核可堆積會拖慢每輪處理)
file: `src/rtb/executor/execution.py`(`process_awaiting()` 用 `self.store.awaiting(tx)` 無上限讀出全部待核可列)
