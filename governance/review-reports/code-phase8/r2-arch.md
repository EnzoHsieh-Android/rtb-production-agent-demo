severity: clean

確認沒問題的觀察:

`_write` 的 `guard` 參數把型別從 `Callable[[ExecutorTransaction], bool]` 改成
`Callable[[ExecutorTransaction], None]`,呼叫端不再靠回傳值判斷、一律靠例外中止交易。這不是新引入的第二種寫法:修正前的 `still_latest`(`_in_flight_again` 內)本來就已經對決策已過時那一支直接 `raise _DecisionStale(row.key)`,只有核可被取代那一支是回傳 `bool` 再讓 `_write` 轉譯成 `raise _ApprovalSuperseded`。修正後的寫法只是把僅存的那一支也改成同樣「guard 內自己丟例外」,跟同一支函式裡另一半的寫法統一,也跟整個檔案一路用 `LeaseLost` / `_ApprovalSuperseded` / `_DecisionStale` 作交易中止訊號的既有慣例(`execution.py:145,365,369` 及一路到 `934` 的多處 raise/except)一致。`_write` 只有這一個呼叫端傳 `guard`(`execution.py:632`),沒有分裂出平行機制。

新的 `_aggregate_full`、`_too_late` 兩個小函式是把既有邏輯原地抽出,沒有換一套做法:
- `_aggregate_full` 裡「決策已過時就直接 `ack_blocked` 擋下、不然才 `_await_in`」的順序,跟同檔 `_gate` 裡 RATIO 那一關「`guardrails.decision_stale` 為真就先擋、否則才 `_await`」的既有寫法(`execution.py` 第 468 行前後)是同一個模式;因為 `_aggregate_full` 已經在 `_begin` 開的交易裡執行,不能像 `_settle` 那樣另開交易,所以直接呼叫 `self.store.ack_blocked(tx, ...)`,這跟 `_too_late` 本來就有的「同一交易內直接 `ack_blocked`」寫法(該函式修正前後都是如此)相同,不是跨層直呼。
- `_too_late` 的參數從 `(tenant, amount)` 改成 `held: dict[BlockCode, Approval]`,是因為呼叫者 `_begin` 本來就已經拿到 `held`(`_gate` 回傳的核可字典),改成直接傳這個既有物件,跟 `_stale_without_approval` 另一個呼叫點(`still_latest` 傳 `signed.approvals`)一樣都是傳 `Iterable[Approval]`,兩處型別介面對齊,沒有兩套判核可存活的邏輯。

收件口重放方法 `replay(self, task_id, revision, operator, clock: Callable[[], datetime])` 改收時鐘函式、在拿到 `immediate_transaction` 寫入鎖之後才呼叫 `clock()`,跟同檔 `accept()` 的寫法逐字對應:`accept()` 本來就是 `clock: Callable[[], datetime]` 參數、且 docstring 明寫「clock 在拿到寫入鎖之後才讀」(`inbox_store.py:512-527`);呼叫端 `inbox_server.py:94` 傳的是 `self.server.clock` 這個函式本身而不是呼叫結果,`replay.py` 這次的修正把 `store.replay(..., clock())` 改成 `store.replay(..., clock)` 正是套用同一個呼叫慣例,不是另創一套。

補寫信封的 `_backfill_envelope` 抽出 `_insert_envelope` 供 `_record_dead_letter` 與它共用,是把既有一條 INSERT 邏輯做成共用私有方法,沒有引入第二種寫信封的路徑;`_audit_dead_letter` 的呼叫方式(帶 `envelope` id、`operator`、`reason`)前後一致。

只看待處理名額的查詢(`_replay_refusal` 內,取代原本的 `try: self._check_capacity(supersedes=False) except InboxFull:`)改成直接查 `SELECT COUNT(*) FROM proposals WHERE {PENDING}` 再跟 `self._max_pending` 比較、回傳 `ReplayOutcome.INBOX_FULL`。跟 `_check_capacity`(`inbox_store.py:575`)比,少了「總列數 `MAX_ROWS`」與「`supersedes` 讓一個名額」兩項,但這是因為重放不新增列,總列數上限本來就是收新提案用的,注釋也點明了這個差異、不是漏改。就函式本身的一致性看,`_replay_refusal` 其餘每一項判斷(收件表那一列還在、狀態、到期、修訂被超過)本來就都是「直接 `if` 判斷條件、回傳對應的 `ReplayOutcome`」,原本用 `try/except InboxFull` 包 `_check_capacity` 反而是這支函式裡唯一用例外做流程控制的一段;改成內嵌 `if pending >= self._max_pending: return ReplayOutcome.INBOX_FULL` 反而跟同一支函式裡其他四項判斷的寫法更一致。跟 `_check_capacity` 之間確實有一段查詢邏輯重複(同一句 SQL 字面重出現),但這是子集查詢、語意不同(只看待處理、不看總表),沒有另立一套「判斷收件口容量」的抽象或跨層直呼,屬於可接受的既有模式延伸,不算 major。

小結:這批修正都是把既有的例外中止交易慣例、既有的 clock 注入慣例、既有的私有小函式抽取慣例套到新的分支上,沒有看到第二種平行做法或跨層直接呼叫。
