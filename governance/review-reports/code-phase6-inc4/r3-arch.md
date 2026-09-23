severity: clean

本輪(r3)只查兩點:收件口模組改用嘗試紀錄模組公開的 `iso` 轉時間(對照收件口模組自己的 `_iso`)、`aggregate_holdings` 回傳型別改變。逐項核對如下,沒有發現引入第二種做法或跨層讀寫。

**收件口模組改用 `attempt_store.iso`**——`inbox_store.py` 的 `stops`(`inbox_store.py:737`)與模組層 `stop_count_query`(`inbox_store.py:778-779`)把原本的 `_iso(since)/_iso(until)` 換成 `attempt_store.iso(since)/attempt_store.iso(until)`。`inbox_store` 早在既有程式碼就已 `from rtb.executor import attempt_store`(型別 `ExecutorTransaction` 就是它的),依賴方向本來就是「inbox_store → attempt_store」;這次只是多呼叫一個既有依賴模組新公開出來的函式,不是新開一條跨層路徑,也符合圖譜寫的「兩張表的比較規則一致」的理由。

引句:「收件口模組的停下紀錄讀取方法也用它(代碼審第 2 輪:搬過去時一度漏掉這個檢查,沒帶時區的時間會被當成本機時間)。」(`docs/rtb-production-agent-demo-knowledge/Systems/外部寫入嘗試紀錄.md`)

值得記一筆但不構成 major 的觀察:`inbox_store.py` 自己的 `_iso`(`inbox_store.py:285`)沒有 `_require_aware` 檢查,而 `attempt_store.iso`/`_iso`(`attempt_store.py:194-201`)有;這次只把「停下紀錄」讀取範圍的兩處(`stops`、`stop_count_query`)換成嚴格版,`record_stop` 寫入同一張 `write_stops` 表的 `at` 欄位(`inbox_store.py:641`)、以及模組內其他所有欄位(`received_at`、`lease_until` 等)仍然用寬鬆的本地 `_iso`。這是同一份筆記明講的範圍(只提「讀取方法」),不是修正沒做到或漏改;兩支函式格式化輸出本身一致(都轉 UTC、同款字串樣式),差別只在對「沒帶時區」的輸入要不要拒絕,不影響已通過驗證的呼叫路徑,只是同一個模組內對同一欄位的寫入與讀取用不同嚴格度,供主線判斷要不要順手把 `record_stop` 也收緊。

**`aggregate_holdings` 回傳型別**——舊版回傳專用的 `Holding(key, amount, legacy)`;這次改回傳 `tuple[CountedFirstRow, ...]`(`attempt_store.py:377-381`),`Holding` dataclass 已整支刪除,全 repo(`src/`、`tests/`)grep 不到殘留引用。`CountedFirstRow` 原本就是「按逐列計入規則算進租戶的一把鍵的第一列」這個概念(`attempt_store.py:410-417`),`aggregate_holdings` 語意上本來就是同一種資料(目前計入的第一列),兩個查詢也都改用同一支 `_first_row_columns(alias)` 組欄位(`attempt_store.py:427-428`)取代各自手刻的欄位列表。這是把兩個本來重複的資料形狀合併成一份,不是疊加出第二種做法——`aggregate_used` 只是把 `.amount` 加總(`attempt_store.py:373-376`),呼叫端不需要知道型別變了。

`observability.aggregate_audit`(`src/rtb/executor/observability.py:270-278`)呼叫端相應把 `holdings = attempt_store.aggregate_holdings(...)` 直接當 `CountedFirstRow` 序列用,拿掉了「先取 `holding.key` 集合、再用 `counted_first_rows(tx, tenant, counted)` 依鍵回查」這一步(該函式與 `Holding` 一起被刪除)。`_entries` 的簽名沒變(`tx, rows: tuple[CountedFirstRow, ...], counted`),`observability.py` 匯入的仍是 `from rtb.executor.attempt_store import CountedFirstRow, ExecutorTransaction`(`observability.py:21`),沒有另開型別或繞過模組邊界直接碰 `attempts` 表。依賴方向仍是「observability → attempt_store / inbox_store」,跟第 1、2 輪確認過的分工一致。
