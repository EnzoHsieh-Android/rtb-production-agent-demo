severity: major

材料:src/rtb/executor/inbox_store.py 死信部分與重放方法、src/rtb/executor/replay.py、src/rtb/executor/execution.py
(決策新鮮度 [S511][S512])、tests/executor/test_dead_letter.py、tests/executor/test_stale_decision.py。
逐項照派工的五點驗。開場先說明一個流程偏差:第一輪重現實驗誤在 `/Users/enzo/rtb-3b`(唯讀材料)
直接寫入並跑了臨時測試檔,發現後已刪除且 `git status` 確認沒有殘留改動;之後把重現腳本移到
`/Users/enzo/rtb-3b` 的副本(先前已複製到本次臨時目錄 `scratchpad/work/rtb-3b`)重新做一次,結果一致。

## F1 [S512] 新鮮度豁免checks「任一關有核可」,不是「它真正卡住的那一關」

severity: major
blocking: 是 — 違反 [S512] 的合約字面義,會讓過時決策在沒有對應核可的情況下繞過即時擋下、
改停在待核可,交給不知情的人核可放行

引句:「決策過時、又沒有任一關的有效核可。有效核可照增量 3 的查法判(每一關最新那張、此刻
算數),不另加「核可放回」旗標」

file: `src/rtb/executor/execution.py:465`(`_stale_in` 的說明,實作在 469 行)

```
return not any(
    self._read_approval(self.store.latest_approval(tx, proposal, stage), proposal, stage,
                        tenant, amount, now) is not None
    for stage in APPROVABLE)
```

[S512] 的文字是「當提案有**它停下那一關**的有效核可…應不判新鮮度」,強調的是這份提案*實際會
卡住的那一關*,不是「兩個可核可關卡中隨便哪一關」。但 `_stale_in`(`_run` 用它判斷是否要在簽發
之後、判比例之前擋成決策已過時)與 `_stale_on_rerun`(重跑路徑用)都用 `any(... for stage in
APPROVABLE)`:只要 AGGREGATE 或 RATIO 任一關有一張此刻算數的核可,就整條豁免新鮮度——不論
這份提案這一次真正會卡在哪一關。

`APPROVABLE` 的兩關(總曝險已滿、比例過大)彼此獨立觸發:一份提案可能先因為比例過大停下、拿到
一張只綁 RATIO 的核可(`approval.holds` 有 `approval.stage is stage` 檢查,核可本身確實綁死
在核准當時那一關,`latest_approval` 查詢也用 `stage = ?` 篩選),之後在放回等候重新處理時,
比例已經不超(或仍超,不影響),但**同一租戶的總曝險額度被別的提案吃掉**、改成卡在 AGGREGATE
——這一關從沒被核可過。此時 `_stale_in` 仍會因為「RATIO 那張核可還算數」判定「有有效核可」,
豁免新鮮度,讓一個已經過時 15 分鐘以上的決策繼續往下跑,最後停在待核可、等人核可 AGGREGATE
那一關。核可 AGGREGATE 的人完全看不出這份決策已經過時(擋下原因只會顯示
`aggregate_limit_reached`,不會顯示或提示新鮮度已違反),核可放行後這份過時的決策就會被送出。

這正是計劃筆記自己點名的風險型態(「過時又超過比例的仍直接擋成決策已過時」),但那句話只保證了
「新鮮度先判、比例核可用不上」這一個方向;沒有處理「豁免用的核可屬於另一關」這個方向,
[S512] 的文字「兩個方向都釘」在這裡沒有真正釘住第二個方向。

重現(在唯讀材料的副本上跑,不影響原始材料):

```
$ cd <scratch>/rtb-3b && .venv .../python -m pytest -p no:cacheprovider \
    tests/executor/test_zzz_repro_cross_gate.py -q -s
```
腳本(見本報告附錄,執行後已刪除):
1. `limit(h, 60)` 固定總曝險門檻(全程不再改,租戶範圍指紋不變,排除「範圍指紋改變讓核可失效」
   這個已知、且被正確擋下的路徑干擾)。
2. `waiting(h, RATIO)`:t1/c1 加預算超過五成,停在 `budget_increase_too_large`。
3. `approve_it(h, prop, RATIO, expires_in=1499)`:只簽 RATIO 這一關,效期夠久(不會在後面
   20 分鐘內過期)。
4. 另提一份 t2/c2、加預算 40(比例內、免核可)並讓它整筆執行完成,把總曝險吃掉 40。
5. 把時鐘推到決策建立後 20 分鐘(超過 15 分鐘新鮮度門檻)。
6. 模擬「核可放回待處理」(把 t1 那列的 `disposition`/`block_code`/`deliveries` 清回可取件
   的狀態,核可表本身沒有改動,RATIO 那張核可還在、還沒過期、範圍指紋沒變)。
7. `h.process()`。

結果:`Processed(kind=AWAITING_APPROVAL, block_code=AGGREGATE_LIMIT_REACHED)`,不是預期的
`Processed(kind=BLOCKED, block_code=DECISION_STALE)`。也就是說,一張只核准過 RATIO 關卡的
核可,讓一份現在卡在 AGGREGATE 關卡(從未被核可)、已經過時的決策繞過 [S512] 該擋下的新鮮度
檢查,改進入待核可佇列。

`_stale_on_rerun`(execution.py:474-481)同樣用 `not self.store.used_approvals(tx, proposal)`
——`used_approvals` 撈的是這份提案「用過的所有核可」(見 inbox_store.py 的 docstring:
「這份提案用過的核可(關卡、整張核可)」),一樣不分關卡,同一個邏輯缺口存在於兩條重跑路徑。

修法方向(不是本次要做的事,寫給接手的人):`_stale_in`/`_stale_on_rerun` 需要先知道這份提案
「這一次」實際會卡在哪一關(比例超過用 `guardrails.increase_too_large`,總曝險用當下已用額度
比對門檻),只用那一關對應的核可去判斷是否豁免;沒卡在任何可核可關卡的(比例內、額度也夠)才
用「兩關都沒有核可」的寫法退回照樣擋。

## 逐項覆核(未列 major 的部分)

1. **進死信與寫信封、稽核是否同一交易**:`receive()`(inbox_store.py:617-658)裡,`_lease()`
   剛在同一個交易寫入 `disposition = in_progress`;緊接著 `_finish(...)` 用同一把收據做條件
   UPDATE 轉成 `dead_letter`,成功才呼叫 `_record_dead_letter`(寫信封)進而 `_audit_dead_letter`
   (寫稽核)。三個寫入都在 `transaction()` 開的同一個 `immediate_transaction` 裡,任何一步例外
   都整個回滾(`test_a_dead_letter_writes_its_envelope_in_the_same_transaction` 驗證:讓
   `_record_dead_letter` 中途丟例外,處置沒變、信封與稽核都沒寫)。因為 `_lease` 與 `_finish`
   在同一個交易、同一條連線裡緊接執行,中間沒有別的寫入者能插進來改變這把鍵的租約,所以
   `_finish` 的條件寫入在正常路徑下必然成立(收據剛核對過);沒有看到「信封寫了、處置沒寫」或
   反過來的路徑。這一步沒發現問題。

2. **重放條件與寫回是否同一交易、並行只放回一次、放回後欄位**:`_replay`(inbox_store.py:
   726-744)把「查條件」(`_replay_refusal`:還在、是死信、沒過期、沒有更新修訂、名額)與
   「寫回」(UPDATE `disposition = NULL, dead_letter_reason = NULL, deliveries = 0,
   lease_until = NULL, lease_owner = NULL`)放在同一個 `immediate_transaction` 裡;寫回的
   WHERE 子句額外帶 `AND state = 'pending' AND disposition = ?`(死信),所以兩個交易序列化
   執行時,先完成的那個會把 disposition 改掉,第二個重新查 `_replay_refusal` 會因為
   disposition 不再是死信而判定 `NOT_DEAD_LETTER`——`test_two_simultaneous_replays_requeue_once`
   已經驗證兩個 thread 各開自己的連線同時重放,結果永遠是一個 `requeued`、一個
   `not_dead_letter`。放回後欄位裡,`disposition`、`dead_letter_reason`、`deliveries`、
   `lease_until`、`lease_owner` 都照預期清空/歸零;`last_failure` 沒有被清掉(維持進死信前
   最後一次卡住的原因)。這跟既有的 `settle_awaiting` 的 `RELEASED` 分支是同一個做法
   (那裡也不清 `last_failure`),不是這次新引入的不一致,而且 `last_failure` 只是給人看的
   說明欄,不參與任何後續判斷(`receive()` 的 `reclaimed` 只看 `disposition`/`lease_owner`),
   不會讓下一次取件或保留期清理算錯。保留期清理只看 `OPEN`(pending/in_progress/
   awaiting_approval),死信本來就不算 open,所以「收件表清掉之後信封還在」(S500)跟
   「重放時如果已經被清掉」(NOT_IN_INBOX,信封仍可查到、稽核仍能記)兩條路徑都對得上,
   測試 `test_a_dead_letter_leaves_a_durable_envelope` 已覆蓋。這一步只有前述 last_failure
   的觀察,不算問題。

3. **信封流水編號、稽核信封欄指向;兩張新表允許值清單、索引;舊庫開啟補表與新擋下原因重建**:
   信封表 `dead_letters` 用 `AUTOINCREMENT` 流水號,同一份提案重放後再死信會拿到新的 `id`
   (`test_a_replayed_proposal_that_dead_letters_again_gets_a_second_envelope` 驗證
   envelope id 依序變成 1、2);`_audit_dead_letter` 的 `envelope` 參數在 `_replay` 裡固定用
   `SELECT max(id) FROM dead_letters WHERE task_id=? AND revision=?` 取「這份提案最新那一列
   信封」,對從沒進過死信的提案回 `NULL`(`test_a_replay_is_refused_when_the_inbox_no_longer_
   has_the_proposal` 驗證稽核的 envelope 欄是 `None`),指向邏輯與文件說明一致。允許值清單:
   `FailureClass`、`DeadLetterReason`、`DeadLetterAction` 三個列舉都在 SCHEMA 字串定義完之後、
   類別定義完之後才用 `_in_list` 替換進 CHECK 約束(模組載入順序上沒有漏換,實測
   `SCHEMA` 裡沒有殘留的 `XXX_LIST` 佔位字串)。索引 `dead_letters_by_proposal`、
   `dead_letter_ops_by_envelope` 都有對應的 `EXPLAIN QUERY PLAN` 測試
   (`test_the_dead_letter_tables_have_their_lookup_indexes`)驗證真的被用上。舊庫開啟補表與
   `BlockCode` 新成員(`policy_version_changed`、`decision_stale`)造成的 `proposals` 表
   CHECK 約束重建,`test_an_old_inbox_gains_the_dead_letter_tables` 用手動退回舊表結構、拿掉
   兩個新值再重跑 `InboxStore(db)` 驗證:兩張新表補上、舊資料原樣保留、新收件能用新的擋下原因
   確認成已擋下。這一步沒發現問題。

4. **過期判斷的時間字串比較格式是否一致**:收件口內所有「跟現在時間比較 `expires_at`」的地方
   (`_accept_in_transaction`、`awaiting()`、`_replay_refusal`)一律用同一支
   `_iso(moment) = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")` 把
   datetime 轉成字串再比較,跟寫入 `expires_at` 時用的是同一支函式,格式自洽。
   `attempt_store.py` 另外有自己的 `_iso`/`iso`,格式字串完全相同(同樣的
   `strftime("%Y-%m-%dT%H:%M:%S.%fZ")`),`stops()` 方法混用 `attempt_store.iso(...)` 跟
   收件表自己的 `_iso` 產生的字串可以互相比較,沒有格式落差。決策新鮮度([S505][S511][S512])
   用的是 `guardrails.decision_stale`,比較的是真正的 `datetime` 物件(`now -
   proposal.decision_created_at > DECISION_FRESHNESS`),不是字串比較,跟過期判斷是兩套不同
   機制但各自一致,沒有格式不一致的問題。這一步沒發現問題。

5. **重放工具的出口代碼、錯誤輸出、資料庫忙碌處理是否跟核可工具一致**:`replay.py` 的
   `EXIT_REFUSED = 4`、`EXIT_BUSY = 6` 跟 `approve.py` 的同名常數數值相同,註解也互相點名
   「跟核可工具同一個代碼」;兩支工具都是「開資料庫失敗抓 InboxBusy 印同一句『資料庫忙碌,
   稍後再試』回 EXIT_BUSY」「主要動作(重放/簽核可)也另包一層 InboxBusy」「都用 finally
   關資料庫連線」;操作人/核可人格式不合都在管理工具這層擋、不寫稽核(`is_id` 檢查失敗直接
   return,不呼叫 store)。這一步沒發現問題。

## 小結

除 F1 外,死信信封/稽核的交易原子性、重放的並行安全與欄位正確性、兩張新表的遷移與允許值清單、
時間字串格式一致性、重放工具跟核可工�的錯誤處理慣例,逐項檢查與既有測試(40 個死信/新鮮度相關
測試全綠)都對得上,沒有發現額外的資料損壞或交易不一致問題。F1 是本輪唯一的 blocking 發現:
[S512]「有它停下那一關的有效核可才免新鮮度」在程式裡退化成「兩個可核可關卡任一關有核可就免」,
在提案於重試/放回之間換了卡住的關卡時會放行本該擋下的過時決策。
