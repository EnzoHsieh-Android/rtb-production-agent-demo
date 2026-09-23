severity: clean

沒有 major/minor 發現。這批修正(`_stop_before_begin`、`_approved_while_stale`、`_block_stale`、總曝險在開始一筆交易裡重算、重放讀不回提案拒絕)寫法都對齊同檔既有做法,沒有引入第二種做法或跨層直呼。

- `_stop_before_begin` 接手 `receipt`/`tx`/`now`,依開始一筆既有 `_await_in`、`_superseded` 的方式接收 `tx` 當參數、不自己開交易,跟 `_take` 裡其他私有方法(`_audit`、`_existing_key`)同一種寫法;拆出來的 `_block_stale` 也照同樣模式(`tx` 已在手上、不另開交易),跟 `_await_in`(同樣是拿現成 `tx` 寫、不開新交易)是同一路;跟會自己開交易的 `_settle`/`_release`/`_await` 是不同情境(那三個是在交易外被呼叫),不是同一層兩種寫法混用。
  引句:「def _stop_before_begin(  # noqa: PLR0913 - 開始一筆的交易裡重判要用的每一樣」

- `_approved_while_stale` 算總曝險已用額度呼叫的是既有的模組級 `_aggregate_used(tx, tenant.name, now)`,跟 `_approvals`(既有的核可預查方法)算預判額度用的是同一個包裝函式,不是另開一條讀法;讀核可也是 `self.store.latest_approval(tx, proposal, AGGREGATE)` 接 `self._read_approval(...)`,跟 `_approvals`、`_superseded` 用的是同一組。
  引句:「found = self._read_approval(self.store.latest_approval(tx, proposal, AGGREGATE),」

- 在同一個交易裡「先查一次額度判要不要核可、真正 `attempt_store.begin()` 內部又算一次」不是新模式:改動前 `_aggregate_full` 走的就是「`begin()` 內先靠丟例外算一次,外面 `_aggregate_full` 再呼叫 `_aggregate_used` 重算一次給停下紀錄用快照」,本來就是同一個交易裡算兩次已用額度;這次只是把「要不要核可」提前到 `begin()` 之前再算一次,沿用同一支 `aggregate_used`/`_aggregate_used`,沒有另立算法。
  引句:「needed = amount > 0 and (」

- 重放讀不回提案(`UNREADABLE`)接在既有 `_replay_refusal` 的一串「查一項、不行就回對應 `ReplayOutcome`」出口鏈路裡,用的還是全模組共用的 `_parse_payload`(跟第 635、720、910、1051、1075 行同一支),回傳值判 `None` 的寫法也跟既有其他呼叫點一致;只是在既有出口鏈裡插入一個新出口,沒有另開一條判斷路徑。
  引句:「if _parse_payload(row[3]) is None:  # 代碼審第 2 輪外家席:原本照樣放回、稽核記成功」

- `noqa: PLR0913`/`PLR0911` 帶一句理由的寫法,在 `execution.py`(`_take`、`_write`、`_after_expiry`、`_reconcile_not_found`)與 `inbox_store.py`(`_record_dead_letter`、`_insert_envelope`、`_audit_dead_letter`)本來就有多筆前例,`_stop_before_begin`、`_replay_refusal` 的加法照抄同一種格式,不算新寫法。

圖譜三格皆空(受影響測試/共改夥伴/呼叫者都 0 筆),沒有可對照的既有相依可查;上述比對全部從 `src/rtb/executor/execution.py`、`src/rtb/executor/inbox_store.py` 本檔既有寫法直接核對得出。
