severity: clean

# 第 3 輪架構對齊審查——src/rtb/analyzer/flow.py 租約擁有者修正

## ① 上輪 F2 是否修到位

### 驗收:租約擁有者改回「啟動程式組好身分、注入業務層」的分工

上輪 major 指出 `advance()` 內部自己讀 `os.getpid()`、`threading.get_ident()`、用模組層
`itertools.count()` 產生擁有者字串,跟執行側「啟動程式(`src/rtb/executor/runner.py:159`
`owner = owner or f"{os.getpid()}-{int(time.time())}"`)組好身分、業務層(`src/rtb/executor/execution.py:316`
`owner: str = "executor"`)只收注入值」的分工不同。

本輪修正:

- `src/rtb/analyzer/flow.py` 移除 `import itertools`、`import os`、`import threading` 與
  `_new_owner()`/`_OWNER_CALLS`,`advance()` 新增 `owner: str = "analyzer"` 形參,直接
  `store.acquire_lease(task_id, owner, now)`(對照 `execution.py:316` 的 `owner: str = "executor"`
  預設值寫法,同一種「有預設值、等真正的啟動程式接上再覆寫」的過渡態)。
- 目前 repo 內還沒有 analyzer 的啟動程式(`grep -rn "advance(" src` 只找到文件字串與 docstring
  提及,沒有生產呼叫端),所以現在無人傳入 `owner`,吃的是預設值 `"analyzer"`——這跟
  `execution.py` 當初也是先有預設值、後來才由 `runner.py` 接上注入是同一個階段,不算新的
  不一致,是同一種漸進式接線。
- 圍籬邏輯查證:`src/rtb/analyzer/task_store.py` 主鍵是 `(task_id, lease_seq)`(42–43 行),
  `release_lease` 的圍籬判斷在 341 行比對 `(lease_seq, lease.owner)` 是否等於目前最新列,
  真正擋舊持有者的是遞增的 `lease_seq`,`owner` 只是隨行標籤——跟新增測試
  `test_callers_sharing_one_owner_are_still_fenced_by_the_lease_sequence`
  (`tests/analyzer/test_task_lease.py`)驗的結論一致,不是自我宣稱沒有查證。

判定:**F2 修到位**,沒有修出新的不一致。

## ② 修正本身有沒有引入新的第二種做法或跨層

沒有。`advance()` 從「內部自產身分」改成「形參收注入值、帶預設值」,是把 analyzer 這條路
拉齊到跟 `execution.py:316` 的 `Executor.owner` 同一種寫法(呼叫端傳入、業務層不碰
`os`/`threading`),沒有另立第三種格式,也沒有新增跨層直呼(`os`/`threading`/`itertools`
三個匯入已全部移除,`flow.py` 現在不再碰行程/執行緒層級的東西)。

新增/改寫的三個測試(`test_the_owner_is_what_the_caller_passes_in_and_only_a_label`、
`test_callers_sharing_one_owner_are_still_fenced_by_the_lease_sequence`、
`test_a_programming_error_while_releasing_is_not_swallowed`)測的是行為本身(注入值原樣落地、
序號圍籬、例外不吞),不是新架構模式,跟既有測試風格(直接查 SQLite 表、用
`monkeypatch.setattr` 注入故障)一致。

不對齊共 0 條,其中 major 0 條。

⚠ 交編排者:analyzer 目前沒有自己的啟動程式(`runner.py` 只服務 executor),`owner` 預設值
`"analyzer"` 目前無人覆寫,是否要在本輪就補一個 analyzer 啟動程式來注入真實身分,還是留到
後續增量——這是排期問題,不屬於「跟既有做法不一致」的範疇,本審查不裁定,留給編排者決定。
