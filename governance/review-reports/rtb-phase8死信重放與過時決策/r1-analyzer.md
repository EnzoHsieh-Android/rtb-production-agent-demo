severity: major

分析鏡頭:src/rtb/analyzer/flow.py(核心)、task_store.py、instrumented.py;對照 executor/inbox_store.py、executor/approval.py 查證伺服端行為;Phase 5 計劃 [S304][S317] 原文;Phase 8 計劃(=審查快照,兩者 diff 為空,逐字相同)第 5 節與 [S507]。

## F1 兩個新擋下原因(policy_version_changed / decision_stale)不會被分析端目前的白名單接住,既有「接續任務」路徑實際上不通
severity: major

引句:「兩個新原因都不是權限類,回給分析行程照原樣。」

引句:「應建接續任務重新規劃」

現況程式碼(`src/rtb/analyzer/flow.py`):
```
294  _BLOCK_CODES = frozenset({_VERSION_CHANGED, "not_permitted", "campaign_not_found",
295                            "campaign_not_active", "operation_previously_failed"})
```
`_belongs_to()`(320-326 行)用這份白名單判斷收件口回應是否「認得」:
```
323      consistent = answer.state in _KNOWN_STATES and (
324          answer.block_code in _BLOCK_CODES if answer.state == "blocked"
325          else answer.block_code is None)
```
`policy_version_changed`、`decision_stale` 目前都不在 `_BLOCK_CODES` 裡。若 Phase 8 只在執行端(`rtb.domain.proposal`/執行迴圈)新增這兩個擋下原因,而不同時把它們加進 `flow.py` 的 `_BLOCK_CODES`,`_belongs_to()` 會判定 `consistent=False`,`_from_handed_off()`(313-317 行)就會走到:
```
315      if not _belongs_to(answer, row.proposal):
316          return None  # 別的提案的回應、或處置與原因對不上:不拿來結案
```
效果是任務卡在 `HANDED_OFF`,永遠不會建接續任務,也不會結案——跟「回應讀不懂」的靜默吞掉是同一條路徑,不是計劃講的「建接續任務重新規劃」。除了 `_BLOCK_CODES`,`_from_inbox_answer()`(329-340 行)目前也只認得 `_VERSION_CHANGED` 一種 blocked 原因會轉重新規劃(336 行),其餘 blocked 一律落進預設的 `_closed()`(340 行)——所以即使先解決了白名單問題,若不额外加兩個 `if` 分支呼叫 `_replan(ReplanReason.POLICY_CHANGED, ...)` / `_replan(ReplanReason.DECISION_STALE, ...)`,這兩個新原因一樣會被當成「擋下、不重新規劃」處理,跟 [S507] 要求的「建接續任務重新規劃」不符。

好消息:`ReplanReason`(`task_store.py` 94-99 行)與它背後的 `follow_ups.reason` 欄位(51-53 行:`reason TEXT NOT NULL`,沒有 CHECK/IN 限制)沒有 DB 層允許值限制,加兩個新成員是純 Python 端的事,不需要遷移收件表以外的表。真正要動的是 `flow.py` 這兩處(`_BLOCK_CODES` 白名單 + `_from_inbox_answer` 的分支),這是計劃裡沒有明說、但「新原因走接續任務既有路徑」要真的通所必須做的事。

blocking: 是 — 照 spec 字面(只改「Phase 5 [S304] 的死信那一半」,沒提兩個新 blocked 原因要同步加進分析端白名單)實作,會讓 F6 情境裡「政策已變/決策已過時」兩種永遠卡住不重新規劃,直接違反 [S507] 與計劃開頭的完成條件「過時決策被擋下或重新規劃」。

## F2 死信等待分支要判「決策是否過期」,但收件口不會把 dead_letter 的列自動轉成 expired——這個判斷只能在分析端自己比對時鐘,現有函式簽章沒有這個資訊通道
severity: major

引句:「改成:收件口回死信而決策還沒過期 → 當成還在處理、等待;決策已過期 → 結案、不重新規劃(死信本身是暫時失敗用完,不自動重做)。」

查證(`src/rtb/executor/inbox_store.py`):
```
478  def _accept_in_transaction(self, proposal: Proposal, now: datetime) -> Accepted:
479      digest = content_hash(proposal)
480      self._conn.execute(
481          f"UPDATE proposals SET state = 'expired' WHERE {PENDING} AND expires_at <= ?",
482          (_iso(now),),
483      )
```
這條把「過期」寫回 `state='expired'` 的 SQL 只作用在 `PENDING`(`state = 'pending' AND disposition IS NULL`)的列。死信列的 `disposition` 已經是 `dead_letter`,不符合 `PENDING`,所以這條 SQL 永遠不會碰它——死信列的 `state`/`disposition` 不會因為決策過期而自動變成 `expired`。而 `_accept_in_transaction` 485-494 行回應既有列時是 `coalesce(disposition, state)`,disposition 優先,所以只要列還在,收件口對同一個任務/修訂的重複查詢會一直回 `dead_letter`,不會自己變成 `expired`。

對照:待核可(`awaiting_approval`)有 `awaiting()`(inbox_store.py 711-737 行)這條主動撈「已到期」候選給執行迴圈處理、進而由執行迴圈把它結案成 `blocked`(`_finish(..., 'state = \'expired\', disposition = NULL', ())`,859 行是同一支 `_finish` 助手,用在核可到期路徑)的機制;死信沒有對應機制。也就是說「待核可可否比照」——不行照抄同一招:`awaiting_approval` 是靠執行端自己有一條到期後會把處置改掉的路,分析端只要繼續等、看下一次收件口回的是不是 `blocked` 就好(`_OPEN_STATES` 裡本來就有 `awaiting_approval`,分析端完全不用自己算過期);死信沒有這條路,收件口對死信永遠回同一個 `dead_letter`,決策是否過期只能靠分析端自己拿 `row.proposal.decision_expires_at` 跟 `advance()` 收到的 `now` 比對,不能靠「再問一次收件口,等它變成別的狀態」。

現有程式碼的訊號路徑目前傳不到判斷點:
```
298  def _from_handed_off(
299      store: TaskStore, row: TaskRow, c: _Collaborators, _now: datetime
300  ) -> _StepOutcome:
...
317      return _from_inbox_answer(answer)
329  def _from_inbox_answer(answer: Accepted) -> _StepOutcome:
```
`_now` 目前用底線前綴標記「不用」,`_from_inbox_answer(answer)` 的簽章也只有 `answer`,沒有 `now`、沒有 `proposal`。要做出「死信+未過期→等待,死信+已過期→結案」這個分支,必須把 `now`(要用 `advance()` 傳進來的那個時間,不能另外讀系統時鐘——這正是本檔案開頭 66-77 行 `EvidenceSource`/`Decide` docstring 反覆強調的「不自己讀系統時鐘」設計不變量,同一條規則理當也適用在這裡)和 `row.proposal.decision_expires_at` 一起傳進判斷點。這是實作 [S507] 必經的簽章改動,計劃裡沒有點出這個管線缺口,但技術上可行(資訊都在,只是沒接線)。

引句:「執行端看不到證據本身,用決策建立時間代替」

這句講的是執行端側的政策/新鮮度檢查(對照 [S505]),跟分析端這裡要判斷的「決策是否過期」是同一個 `decision_expires_at` 欄位但用途不同層級,不要混為一談;分析端這裡判的是「提案本身的到期時間」,不是新的 15 分鐘新鮮度規則。

blocking: 是 — 這是 [S507] 能否落地的必要管線;沒有這條資訊通道,「等待/結案」兩分支無法真的分辨,只能誤把死信一律當等待(永遠不結案)或一律當結案(退回 Phase 5 舊行為),兩者都不符合 [S507]。

### 死信等待「會不會永遠等」、輪詢頻率、跟 [S317] 會不會打架——查證結果,沒發現矛盾

分析端本身沒有排程迴圈。`docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md` 第 6 行明寫:「不負責多任務排程」;`advance()`(flow.py 156-191 行)是純被動函式,一次只做狀態機的下一步,呼叫的頻率完全由外部呼叫端決定,程式碼裡查不到輪詢間隔或逾時參數(這件事程式碼答不出來,不是本次改動的範圍)。

死信等待分支本身有時間上限,不是無界等待:只要 `advance()` 持續被呼叫(不論頻率),`row.proposal.decision_expires_at` 最遲在建立後 1 小時到期(`rtb.domain.proposal.MAX_DECISION_LIFETIME`;實務上 `policy.py` 的 `DECISION_LIFETIME` 只給 30 分鐘),過了這個時間點分析端的本地過期檢查(F2 要補的那段)就會把任務結案,不再回傳「等待」。所以「沒人重放時會不會永遠等」的答案是:不會在邏輯上永遠等——會在決策過期後的下一次 `advance()` 呼叫轉為結案;但如果外部呼叫端完全停止呼叫 `advance()`,那是任何非終點狀態共通的既有限制(HANDED_OFF 的正常等待、COLLECTING_EVIDENCE 的重試等都一樣依賴外部反覆呼叫),不是死信這條路徑獨有的新問題。

跟 [S317](收件表清掉、DSP 查不到、走 `_from_dsp` → `_replan(AFTER_RETENTION, ...)`)會不會撞:不會,因為 `RETENTION = timedelta(hours=2)`(`inbox_store.py` 47 行,註解「必須比提案能活的時間長」)大於 `MAX_DECISION_LIFETIME`(1 小時)。也就是說死信列一定會先因為「決策過期」被分析端在本地判定結案(進 `TaskState.BLOCKED`,依 `src/rtb/domain/task_state.py` 37-44 行是終點狀態),再過至少 1 小時收件口才會把這筆已無待處理/處理中的任務整批清掉(`_purge_finished_tasks`,inbox_store.py 521-533 行)。分析端一旦到達終點狀態,`advance()` 一開頭就直接回傳(flow.py 177-178 行:`if row.state in TERMINAL_STATES: return row.state`),不會再呼叫 `submit()`,自然不會再走到 `_from_dsp`/[S317] 那條路,兩者時間序上是先後關係、不是同時可能觸發的分支,不會重複建接續任務。唯一會讓「決策未過期」分支被清掉表格繞過的情況,是保留期被改得比決策存活期短——目前常數關係不會發生,但這是隱含前提,沒有寫成任何測試或斷言釘住;若日後有人各自改動 `RETENTION`/`MAX_DECISION_LIFETIME` 兩個常數(分屬 executor 與 domain 兩個模組),可能悄悄破壞這個順序,建議留一條測試或斷言釘住 `RETENTION > MAX_DECISION_LIFETIME`。

### 待核可(增量 3)在分析端現在怎麼處理

`_OPEN_STATES = frozenset({"pending", "in_progress", "awaiting_approval"})`(flow.py 287 行),`_from_inbox_answer()` 332-333 行:`answer.state` 屬於 `_OPEN_STATES` 就回 `None`(不改任務、純等待),註解(286 行)也寫明:「待核可(Phase 6 增量 3)跟處理中一樣是等待:人核可後執行端接續處理,到期才確認成已擋下」。分析端這裡完全不用自己判斷核可是否過期——過期後的轉換是執行端主動做的(`inbox_store.py` `awaiting()` 711-737 行撈出已到期或有更新修訂的候選給執行迴圈,執行迴圈再把它結案成 `blocked`),分析端只是被動地等下一次收件口回應變成別的狀態。

死信不能照抄這個「純被動等待」模式(即單純把 `dead_letter` 加進 `_OPEN_STATES`):待核可有執行端主動的到期轉換機制頂著,分析端不用自己算時間;死信沒有對應機制(F2 已查證:收件口對死信列不會自動把 `state` 轉成 `expired`),如果只是把 `dead_letter` 平移進 `_OPEN_STATES`,會變成「死信+任何情況都等待」,決策過期後也不會結案,違反 [S507] 的「決策已過期應結案、不建接續任務」那一半,也違反計劃「回退」節提到的「死信本身是暫時失敗用完,不自動重做」的取捨。死信這條必須是分析端自己算「決策是否過期」的條件分支(見 F2),不是單純比照待核可的無條件等待。

### 結論

程式碼與筆記沒有互相矛盾的地方(Phase 5 [S304]/[S317] 原文與現在程式碼行為一致,Phase 8 計劃對現況的描述也跟程式碼核對得上,包括對「回退」節「死信且未過期就等待」回退前要確認沒有待重放死信的提醒)。兩個 F 都是「照 spec 字面實作會漏掉合約」等級:F1 是新原因根本進不了 `_BLOCK_CODES`/`_from_inbox_answer` 的分支表,既有「接續任務」路徑實際上不通;F2 是「決策是否過期」這個判斷所需的時間與門面資訊,現有函式簽章沒有接線,必須改 `_from_handed_off`/`_from_inbox_answer` 的簽章才能做。
