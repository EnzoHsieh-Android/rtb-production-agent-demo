severity: clean

## 範圍
鏡頭:查詢三(`InboxStore.awaiting_count`、`InboxStore.approval_use_count`、`observability.approval_counts`)跟增量 3 人工核可正式流程(`execution.py` 的 `_gate`/`_await`/`_await_in`/`process_awaiting`/`_settle_awaiting`/`_take`/`_audit`,以及 `inbox_store.py` 的 `await_approval`/`record_stop`/`settle_awaiting`/`record_approval_use`/`add_approval`)寫出來的資料,是否會讓新查詢數錯。逐一對過派工單列的四個情境。

## 情境一:一份提案先比例過大待核可、核可放行後又總曝險已滿待核可
`execution.py:642-661` 顯示兩關是循序觸發的:先進 `_await_in`(比例過大)寫一列 `write_stops(kind=budget_increase_too_large)`,`process_awaiting` 放行後 `disposition/block_code` 清空(`inbox_store.py:757-758`),下一輪若總曝險已滿又觸發 `except attempt_store.AggregateLimitReached` 再進一次 `_await_in`(`execution.py:659-661`),寫第二列 `write_stops(kind=aggregate_limit_reached)`。`awaiting_count` 的 join 條件是
引句:「AND w.content_hash = p.content_hash AND w.kind = p.block_code」
只接「目前這一關」那一列,不會因為兩列都在而重複數,跟 `tests/executor/test_observability.py` 裡 `w1` 的斷言(先進 AGG 關又補一列 RATIO 停下紀錄,仍只算一份)一致。

## 情境二:待核可到期轉擋下
`settle_awaiting` 的 `EXPIRED` 分支只改 `disposition = ?`(值是 `blocked`),不動 `block_code`(`inbox_store.py:753-756`)。`awaiting_count` 篩的是 `AWAITING = "state = 'pending' AND disposition = 'awaiting_approval'"`(`inbox_store.py:150`),到期轉擋下之後 `disposition` 不再是 `awaiting_approval`,自然從待核可數退出,不會殘留。

## 情境三:被新修訂取代
`SUPERSEDED` 分支把 `state` 改成 `superseded`(`inbox_store.py:755-756`),`AWAITING` 的 `state = 'pending'` 條件不成立,同樣正確退出待核可數,而且原本那一列的 `write_stops`/`approval_uses`(若有)留著不影響——這兩張表本來就「只增不改」,不記錄「現在還是不是待核可」。

## 情境四:核可使用表有列但停下紀錄沒有對應種類
`approval_use_count` 在有 `campaign_id` 篩選時才 JOIN `write_stops`(因為 `approval_uses` 表沒有 `campaign_id` 欄):
引句:「JOIN write_stops w ON w.task_id = u.task_id AND w.revision = u.revision」
這條 JOIN 若真的接不到,會把那一列從篩選後的計數裡悄悄丟掉(不像 `awaiting_count` 有另外回報接不到的份數)。追查這在正式流程裡能不能發生:
- `record_approval_use` 只有一個呼叫點 `execution.py:696`(`_audit`),而 `_audit` 只在 `begun.created`(第一次開始這把鍵)時呼叫,且只在 `RATIO in live` 或 `AGGREGATE in live and begun.over_limit is not None` 時才寫那一關的列(`execution.py:687-696`)。
- 能進入 `live`(此刻算數的核可)前提是 `approvals` 表裡有這份提案這一關的核可(`_approvals`/`latest_approval`,`execution.py:483-490`)。
- 核可表唯一的寫入路徑是 `add_approval`(`inbox_store.py:836-852`),唯一呼叫端是 `approve.py` 的 `_sign`,而它在簽之前先 `find_proposal` 拿「現在待核可的那一關」,並且
引句:「if waiting.stage is not stage: # 只能簽它現在停的那一關」
拒簽不符的關卡(`approve.py:69-71`)。也就是說,一張核可要存在,前提是這份提案「當時」正處在那一關的待核可,而待核可只能經 `_await_in` 進入,`_await_in` 一定在同一個交易裡先 `await_approval` 後 `record_stop`(`execution.py:509-511`),兩者不會分開發生。
- 因此,凡是能走到 `record_approval_use` 的那一關,對應 `write_stops` 那一列一定已經先寫過(而且這張表只增不刪,不會事後消失),`approval_use_count` 的 JOIN 理論上不會漏接正式流程產生的列。`tests/executor/test_observability.py` 裡用 `_approval_use` 直接灌一筆沒有對應 `write_stops` 的核可使用列(`a2`)是繞過 `add_approval` 的守門直接呼叫 store 方法,屬於防禦性測試,不是正式流程能產生的狀態;程式對這種狀態的處理(篩選時悄悄丟掉、不篩選時照樣算入)跟收件口筆記
引句:「已核可放行數只讀核可使用表的租戶、任務、修訂、內容雜湊與關卡,不讀核可表」
的既定設計一致,不算數錯。

## 其他檢查
- `awaiting_count`/`approval_use_count` 都以 `self._own(tx)` 開頭核對交易來源,新增測試 `test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox` 涵蓋兩支新方法,跟停下紀錄表既有兩支方法同規格。
- `approval_uses` 表 `UNIQUE (task_id, revision, content_hash, stage)`、`write_stops` 表 `UNIQUE (kind, task_id, revision, content_hash)` 都是「一份提案一關至多一列」,兩邊 JOIN 鍵完全對齊(`task_id`/`revision`/`content_hash` + `kind = stage`),不會因為多列而重複計數。
- `BlockCode.AGGREGATE_LIMIT_REACHED`/`BUDGET_INCREASE_TOO_LARGE` 與對應的 `StopKind` 成員字串值完全相同(都是 `aggregate_limit_reached`/`budget_increase_too_large`),`w.kind = p.block_code`、`w.kind = u.stage` 這兩處跨型別字串比對不會因為列舉值不同而失配。
- `tests/executor/test_observability.py test_approval_counts_cover_waiting_and_applied` 與既有測試套件(`test_observability.py`、`test_execution.py`)實測全部通過(`.venv/bin/python -m pytest -p no:cacheprovider tests/executor/test_observability.py tests/executor/test_execution.py -q` → 80 passed)。
