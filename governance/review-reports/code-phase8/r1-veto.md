severity: major

## F1 [S512] 任一核可都能放行過時決策

severity: major

blocking: 是 — 過時提案只要掛有同提案任一關的有效核可，即使從未停在該關，也能略過新鮮度並寫入 DSP，破壞 [S512] 與 F6。

引句:「有它停下那一關的有效核可的提案不判新鮮度,其他檢查照跑」

`_stale_in()` 遍歷整個 `APPROVABLE`，找到任何有效核可就回傳「不過時」，完全沒查 `write_stops` 是否存在該關的停下紀錄：`src/rtb/executor/execution.py:461`。因此，同一提案若誤簽一張 aggregate 核可，但它其實既未超比例、總曝險也未滿，20 分鐘後仍會通過新鮮度；後續 `_gate()` 又會丟掉用不到的 aggregate 核可，最後用普通憑證寫入。這正是威脅模型中的「忘記／簽錯關卡」，不需要刻意繞過。

現有測試只覆蓋真正停在比例關的核可，以及「別的提案」的核可；沒有覆蓋「同一提案、錯誤關卡或從未停下」：`tests/executor/test_stale_decision.py:137`、`tests/executor/test_stale_decision.py:170`。

重現命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c '...建立比例與總曝險皆未觸發的提案，替同提案簽 aggregate 核可，再呼叫 Executor._stale_in...'
```

輸出：

```text
ratio_too_large=False, aggregate_full=False
decision_stale= True
executor_blocks_as_stale= False
```

應把豁免條件綁到該提案實際的停下紀錄與其關卡，而不是只看核可表中是否存在任一有效 token；並補同提案錯關卡、從未停下兩個反例。

## F2 收件表總列數滿時，重放被錯誤拒絕

severity: major

blocking: 是 — 重放不新增提案列，卻套用了新提案的總列數上限；即使待處理名額完全空閒，合法死信也無法恢復。

引句:「條件:收件表那一列還在、處置是死信、提案還沒過期、同任務沒有更新的修訂、待處理還有名額」

`_replay_refusal()` 呼叫 `_check_capacity(supersedes=False)`：`src/rtb/executor/inbox_store.py:746`。該函式除了檢查待處理名額，也在 `total >= MAX_ROWS` 時拋出 `InboxFull`：`src/rtb/executor/inbox_store.py:575`。但重放只更新既有列的 disposition，並不新增 proposals 列；所以資料庫有 5000 筆保留中的歷史列、待處理數為 0 時，設計列出的所有重放條件都成立，實作仍回 `inbox_full`。

重現命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c '...令 pending=0、total=MAX_ROWS，再呼叫 InboxStore._check_capacity(False)...'
```

輸出：

```text
replay_refused: pending=0 total=5000
```

新增測試 `test_a_replay_is_refused_when_the_inbox_is_full` 只製造待處理名額已滿，沒有覆蓋總列數滿但待處理有空間的情境：`tests/executor/test_dead_letter.py:156`。重放應只套用待處理名額限制；總列數限制應保留給會插入新 proposal 的收件路徑。

另核對 S511 的兩條重跑路徑仍沿用原冪等鍵；結果不明路徑查不到寫入時會先作廢原鍵，再落成未發生，未見破壞 F1、F2、F3、F4 或 F7 的另一個擋合併問題。
