severity: clean

站在攻擊者角度檢查了四個洞:(1) 用無效/用不到/被取代/剛過期的核可讓過時決策寫進 DSP,(2) 重放補寫信封灌爆信封表,(3) 重放讓收件表總列數無上限成長,(4) SQL 拼接與握寫入鎖期間的查詢成本。四個方向都沒找到可被刻意繞過的新洞;以下逐項說明,最後附兩點觀察(非阻擋)。

## 過時決策 + 核可的組合有沒有新繞法

`_stale_without_approval`(`src/rtb/executor/execution.py:451-464`)只檢查傳入的 `approvals` 集合裡「有沒有任何一張現在還沒過期」,不重驗 scope/tenant/amount/是否仍是該關最新——但這些校驗在核可被放進 `held`/`signed.approvals` 之前已經做過(`_approvals`、`_resign` 都先呼叫 `_read_approval`→`approval.holds`)。真正要擔心的是「查好之後被取代」的核可,會不會在過時檢查放行之後,繞過取代檢查直接寫進 DSP。追了兩條路徑,答案都是不會:

引句:「if any(self._superseded(tx, picked.proposal, stage, found)
                   for stage, found in held.items()):  # 查好之後又有人簽了更新的核可:下一輪重判
                self.store.release(tx, receipt, now, None)
                return Processed(Result.DEFERRED)」

這段(`_take`,`execution.py:664-667`)排在 `_too_late`(過時檢查)之後、`attempt_store.begin`(真正落地那一列)之前,同一個交易裡執行;被取代就直接 `DEFERRED`,不會走到 `begin`,也就不會有後續的 `dsp.write`。`_in_flight_again` 的 `still_latest` 守衛(`execution.py:626-630`)也是同一個順序:先丟 `_DecisionStale` 再丟 `_ApprovalSuperseded`,兩種例外都在 `attempt_store.transition` 之前擲出,交易回滾。往上追一層,`_after_expiry`(`execution.py:849-858`)證實兩個例外都在 `dsp.write` 呼叫之前被接住:

引句:「except _ApprovalSuperseded:
            return self._not_resent(proposal, row, receipt, None)
        except _DecisionStale:
            return self._not_resent(proposal, row, receipt, BlockCode.DECISION_STALE)」

所以「過時決策 + 剛好還沒過期但已被取代的核可」這個組合,最壞情況是多繞一輪(下一輪 `_approvals`/`latest_approval` 重查會拿到新核可、判定不算數),不會讓一筆真正被取代或用不到的核可把過時決策寫進 DSP。

至於「有效核可放行過時決策」本身(S512 的字面行為):只要那張核可是「這一次真的需要」的關卡(`_gate` 只留下比例超標、或預判會超總曝險的那幾張,見 `_approvals` 的 `execution.py:499-516`),而且沒過期,設計就是讓它蓋過新鮮度——這是使用者裁定要的行為,不是繞過;而且 `_aggregate_full`(`execution.py:699-709`)完全不看核可,只要決策過時就直接擋,不會被「手上剛好有一張其他關卡的舊核可」矇混過去。

## 重放補寫信封會不會被灌爆

`_backfill_envelope`(`inbox_store.py:713-723`)只在 `max(id) FROM dead_letters` 查不到(從沒寫過信封)且該列此刻仍是 `state='pending' AND disposition='dead_letter'` 時才插入一列;插入之後同一份提案/修訂就有信封了,下一次 `replay()` 的 `max(id)` 查詢會命中既有列,不會再補。`replay()` 全程在 `immediate_transaction`(寫入鎖)裡跑,兩個操作人同時打同一把鍵只有一個能進去,不會出現重複補寫的競態(對應測試 `test_two_simultaneous_replays_requeue_once` 與新增的 `test_a_dead_letter_from_before_the_upgrade_gets_its_envelope_on_replay` 也驗到只補一列)。要讓信封表持續變大,得先讓同一份提案真的再被投遞次數用完、再進一次死信——這是 `_record_dead_letter` 的路,不是重放路徑,重放本身不會無限灌信封表。

## 只看待處理名額之後,重放能不能讓收件表無上限成長

`replay()` 對 `proposals` 表只有一句 UPDATE(把已存在那一列的 `disposition` 從 `dead_letter` 改回 `NULL`),沒有任何 `INSERT`:

引句:「UPDATE proposals SET disposition = NULL, dead_letter_reason = NULL, "
                "deliveries = 0, lease_until = NULL, lease_owner = NULL "
                "WHERE task_id = ? AND revision = ? AND state = 'pending' AND disposition = ?"」

原本 `_check_capacity` 的兩個門檻(`pending >= max_pending` 與 `total >= MAX_ROWS`)是給「新提案要 INSERT 一列」的路用的(`inbox_store.py:558-581`);重放不新增列,`total` 不會因為重放變大,拿掉 `MAX_ROWS` 檢查、只留 `pending >= max_pending` 是對的,沒有讓總列數失控的新路——真正會讓 `proposals` 表變大的仍是 `submit()`,那條路的兩個檢查一個都沒動。新增的測試 `test_a_replay_is_not_refused_just_because_the_table_is_long` 也直接驗證了「表很長但只算待處理名額」這個意圖。

## SQL 拼接與握鎖期間的查詢成本

`inbox_store.py:788` 的 `f"SELECT COUNT(*) FROM proposals WHERE {PENDING}"` 裡 `{PENDING}` 是模組層固定字串常數(`PENDING = "state = 'pending' AND disposition IS NULL"`,`inbox_store.py:150`),不是任何呼叫參數拼進去的,`task_id`/`revision`/`operator` 等外部輸入全部走 `?` 參數化,沒有注入面。握鎖期間的查詢成本方面:`replay()` 本來就在 `immediate_transaction` 裡跑,`_check_capacity` 舊版在鎖內做兩次 COUNT(pending 一次、total 一次),新版只做一次 COUNT(pending),成本是降低不是提高;`clock()` 改成拿到鎖之後才呼叫(`replay(self, task_id, revision, operator, clock)` 內部先進 `immediate_transaction` 再呼叫 `clock()`),避免等鎖排隊期間用舊時間判斷,對安全屬性是加強(對應新測試 `test_a_replay_reads_the_clock_after_taking_the_write_lock`),沒有引入額外的鎖內重查。

## 觀察(非阻擋)

- `decision_created_at`、核可查得到的欄位等都是提案內容的一部分,而提案內容按威脅模型「分析行程被劫持後可被攻擊者操控」——換句話說,若分析端真的被劫持,攻擊者理論上可以在提案裡填一個很新的 `decision_created_at` 讓 `guardrails.decision_stale` 永遠判「不過時」。這不是這輪修正引入的,是 Phase 8 設計本身用「決策建立時間」當新鮮度依據(而非某個執行端獨立蓋章的時間)的既有性質,r1/r2 都沒有改變這一點,僅記錄供之後設計覆核參考,不算這批修正的新洞。
- `_replay()` 對不存在的 `task_id`/`revision`(從沒進過收件表、也從沒死信過)仍會先寫一列 `replay_requested` 稽核(`envelope=NULL`)才做後續判斷回 `NOT_IN_INBOX`——這個「即使無效請求也先寫稽核」的行為在 r1 就存在,r2 沒有改變它,重放管理指令本身的授權只驗操作人格式(`is_id`),不驗身份權限;若要濫用灌爆 `dead_letter_ops`,走的是這條既有的稽核先寫路徑,而不是這輪新加的信封補寫或名額檢查,不算這批修正新增的攻擊面。

看過的檔:
`/Users/enzo/rtb-3b/src/rtb/executor/execution.py`
`/Users/enzo/rtb-3b/src/rtb/executor/inbox_store.py`
`/Users/enzo/rtb-3b/src/rtb/executor/replay.py`
`/Users/enzo/rtb-3b/tests/executor/test_dead_letter.py`
`/Users/enzo/rtb-3b/tests/executor/test_stale_decision.py`
`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase8死信重放與過時決策_計劃.md`
`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md`
`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`
`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/死信重放指令.md`
（另讀取以確認假設，未改動：`src/rtb/executor/guardrails.py`、`src/rtb/executor/approval.py`、`src/rtb/executor/attempt_store.py`）
