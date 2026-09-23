severity: major

材料:`/Users/enzo/rtb-3b` HEAD(e66d01a),對照第 1 輪收貨紀錄
`governance/review-reports/code-phase8/r1-intake.md` 的重現表逐條複驗。實驗在臨時 clone
`/private/tmp/claude-501/.../scratchpad/rtb-3b-copy` 做,一律 `git -C` 或直接 cd 進臨時目錄,
沒有動過 `/Users/enzo/rtb-3b`。

## 逐條核對第 1 輪重現表

exec-F1、veto-F1(掛一張用不到的核可、過時仍照常寫入/停待核可):`_gate` 在
`held.pop(RATIO, None)`(比例沒觸發時彈掉)與只在 `RATIO not in held` 分支才判 `decision_stale`
兩處都已改好,`test_an_unneeded_approval_does_not_exempt_a_stale_decision`、
`test_an_approval_for_the_other_gate_does_not_exempt_a_stale_decision` 綠燈,重現原問題不再出現。

deadletter-F1(比例核可放回後另一份先用掉額度,開始一筆撞到總曝險):`_take` 的
`AggregateLimitReached` 例外交給新方法 `_aggregate_full`(`src/rtb/executor/execution.py:699`),
過時直接擋、不停待核可,對應測試 `test_a_stale_decision_approved_for_one_gate_is_blocked_at_the_next`
綠燈。**但這個修法本身留了一個新洞,見下面 F1**。

finder-F4、finder-F1、finder-F2/veto-F2、finder-F3、arch-F2、tests-F1、spec-F1、security-F1、
arch-F1:讀碼與對應測試(`test_a_resend_whose_approval_expires_before_the_transition_is_not_sent`、
`test_a_replay_reads_the_clock_after_taking_the_write_lock`、
`test_a_replay_is_not_refused_just_because_the_table_is_long`、
`test_a_dead_letter_from_before_the_upgrade_gets_its_envelope_on_replay`、`_write` 的 guard 改成
`Callable[[tx], None]` 一律丟例外)都對得上第 1 輪的折法,沒有再出現。

## F1 總曝險已滿又過時:預判漏看的有效核可被直接吃掉,不是「沒有有效核可」

severity: major
blocking: 是 — 違反 [S512](有效核可只免新鮮度一項,「用得到的核可」不分預判方向都該被看到)這個
使用者裁定,會讓一份實際上核可過、額度也真的用得到那張核可的決策被誤判成沒有核可、直接擋成
`decision_stale`,分析端因而重新規劃、核可人核准的這張核可完全沒用上——這正是計劃裡「用得到的
核可」條款要防的事,只是方向反過來。

引句:「總曝險已滿又沒有有效核可:停在待核可;決策已過時就直接擋下,不等一張新核可(Phase 8)。」
(`src/rtb/executor/execution.py:704`,`_aggregate_full` 的 docstring)

`_aggregate_full`(`src/rtb/executor/execution.py:699-709`)只憑 `guardrails.decision_stale`
判斷要不要擋下,完全不看有沒有一張此刻算數的總曝險核可:

```
def _aggregate_full(self, tx, receipt, proposal, reservation, full, now):
    if guardrails.decision_stale(proposal, now):
        self.store.ack_blocked(tx, receipt, now, BlockCode.DECISION_STALE)
        return Processed(Result.BLOCKED, block_code=BlockCode.DECISION_STALE)
    return self._await_in(tx, receipt, proposal, AGGREGATE, self._stop(...), now)
```

它會被呼叫,前提是 `attempt_store.begin` 丟出 `AggregateLimitReached`,而這只在
`reservation.approved`(即 `AGGREGATE in live`)是 False 時才會發生
(`src/rtb/executor/attempt_store.py:353-354`)。`live` 又是從 `held` 篩出來的,`held` 來自
`_gate` 呼叫的 `_approvals()`(`src/rtb/executor/execution.py:499-516`)——這支方法在「開始一筆」
之前另開一個交易先「預判」要不要查總曝險核可:

引句:「這裡算的已用額度只是預判,開始一筆的交易裡照舊重算;預判沒超過、實際超過時照樣進待核可,
下一輪再用上這張。」(`src/rtb/executor/execution.py:504-506`)

也就是說:如果預判時「還沒超過限額」,`_approvals()` 會把 `tokens[AGGREGATE]` 設成 `None`,
連讀都不讀,這張核可從頭到尾不會進 `held`/`live`。等到 `_take()` 真正開始一筆時,如果額度已經
被別的工作者用掉(這正是文件自己承認的「已知窄窗」,只是原本承認的是反方向:「預判要用總曝險
核可、開始一筆時額度剛好被別的工作者釋放」——這裡是它的鏡像:預判不用、開始一筆時額度被別人
用掉,變成真的要用),`begin()` 就會丟 `AggregateLimitReached`,进 `_aggregate_full`。

在 Phase 8 之前,這裡只會 `_await_in`(停待核可),下一輪重新取件時 `_approvals()` 會重新預判
(這次額度確實不夠,不會再把 token 設成 None),核可才會被正確讀到、正確使用——所以這是文件裡
「已知窄窗」承認的「多一趟往返」但最終正確。**Phase 8 加了 `_aggregate_full` 這個新分支之後**,
只要決策同時已過 15 分鐘,就不會再走「停待核可、下一輪重新預判」這條安全路徑,而是直接判
`decision_stale`——即使核可表裡確實有一張此刻算數、對這一關有效的核可,也完全沒被查過。

### 重現(已在臨時 clone 用測試證實,不靠理論)

在 `tests/executor/test_stale_decision.py` 加的測試(檔案已改在臨時目錄,沒有動
`/Users/enzo/rtb-3b`):

1. 總曝險上限 60;t1 加 40(比例內,不用比例核可),先簽好一張 t1 的 `AGGREGATE_LIMIT_REACHED`
   核可(600 秒內到期,還沒過期)。
2. `_gate` 跑完之後(即 `_approvals()` 已經預判「t1 不需要總曝險核可,因為 0+40<=60」,`held`
   裡沒有 AGGREGATE)、`_take()` 開始一筆之前,插入 t2 先跑完一輪(t2 加 50,比例內,把總曝險用掉
   0+50=50)。
3. 時鐘撥到決策建立後 20 分鐘(過時)。
4. 處理 t1:此時開始一筆會發現 50+40=90 > 60,`begin()` 丟 `AggregateLimitReached`,進
   `_aggregate_full`;t1 確實有一張此刻算數(600 秒到期)的 AGGREGATE 核可。

實測結果:`Processed(kind=BLOCKED, block_code=DECISION_STALE)`,`h.dsp.writes` 沒有這一筆——
被直接擋成過時,那張核可完全沒被檢查過。對照組(把 t2 的動作搬到 t1 送出之前,讓 `_approvals()`
預判時就查得到)則正常 `EXECUTED`,證明差別只在於「這張核可有沒有被預判排除」,不是核可本身
失效。

跑法(在臨時 clone 裡):
```
cd <臨時clone>
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider -q -s \
  tests/executor/test_stale_decision.py \
  -k "aggregate_approval_that_the_predictor_missed or control_when_the_predictor_gets_it_right"
```
兩個測試都跑了、印出 `RESULT`/`CONTROL RESULT`:漏判那條印
`Processed(kind=BLOCKED, block_code=DECISION_STALE)`,對照組印
`Processed(kind=EXECUTED, ...)`,其他既有測試(`test_stale_decision.py`、`test_dead_letter.py`、
`test_execution.py`、`test_approval.py`,共 175 個)全綠,不是改壞了什麼既有行為,是這個分支
本來就沒補上核可重查。

### 跟已知窄窗的差異,為什麼算是新問題

計劃裡「不改」的「已知窄窗」段落只承認了預判方向錯誤中「憑證被核可到期壓短、核可卻沒用上」這一種
後果有界的情況(多一趟往返,不影響正確性)。Phase 8 的 `_aggregate_full` 是新加的分支,它把
「預判漏看、實際用得到」這個對稱的窄窗從「多一趟往返」變成「決策被誤判成過時、核可白費、
分析端多花一次重新規劃」——這正是 [S512] 要保的東西被繞過,而且是本來就有文件承認的既有窄窗
被新程式碼放大成有實際後果的誤判,實作狀態節與 WHY 筆記都沒有提到這個新的組合後果,是漏記,
不是使用者已經接受的取捨。

### 建議修法方向(不代替設計審,只是複驗附帶觀察)

`_aggregate_full` 在判斷過時之前,應該像 `_too_late`/`_stale_without_approval` 一樣,用「這一關
此刻是否真的有一張算數的核可」來判斷,而不是只問「決策是否過時」——也就是在丟 `decision_stale`
之前,補一次對 AGGREGATE 這一關的核可查詢(用 `self.store.latest_approval` + `_read_approval`,
跟 `_approvals()` 用的是同一支查法),而不是假設「走到這裡就一定沒有有效核可」。

## 針對題目 1(新鮮度判斷點有沒有漏路徑)——其餘路徑確認沒問題

三個判斷點(`_gate` 判比例前、`_aggregate_full`/`_too_late` 在開始一筆的交易、`_in_flight_again`
在兩條重跑路徑轉回嘗試中的交易)覆蓋了取件、重跑、待核可放回後再取件三種進場方式;「取件時鍵已
存在的分流」(`_existing_key`)不寫新決策,只是確認既有嘗試的終點狀態,不是新鮮度要防的寫入口,
沒有漏。「處理待核可那一步」(`process_awaiting`/`_settle_awaiting`)本身不判新鮮度,但它只是把
提案放回待處理,重新取件時一定會再走一次「開始一筆的交易」那個判斷點(`_too_late`),已用讀碼
與 `test_an_approved_proposal_skips_only_the_freshness_check` 確認正確。「對帳查到已寫入的路徑」
(`_found`)不判新鮮度是對的:那個寫入在過時之前已經真的發生過,不該回頭否認。核可過的提案在
`_gate`、`_too_late`、`_in_flight_again` 三處都有照 [S512] 放行,除了 F1 那個因為預判排除、
根本沒被查到的例外。

## 針對題目 3(轉回嘗試中改成一律丟例外,既有例外順序有沒有變)——確認沒有變

讀 `src/rtb/executor/execution.py:794-818`(`_write`)與 round-1 修正前後的差異:
`guard` 從「回 False 就外層再丟 `_ApprovalSuperseded`」改成「guard 自己丟例外」,但呼叫位置完全
沒動——仍是「`extend`(收據失效丟 `LeaseLost`)→ `guard(tx)`(決策過時/核可被取代)→
`attempt_store.transition(...)`(序號不合丟 `_no_progress`;送出次數到頂丟 `SendLimitReached`)」
這個順序,round-1 前後(比對 r2-snapshot.patch 裡 `_write` 那段的 diff)都是一樣的位置,只是
把「回布林值再外層判斷」換成「guard 內部直接丟」,兩種寫法在這個呼叫序列裡是等價的——沒有調換
跟 `SendLimitReached`、`LeaseLost` 的相對順序。`attempt_store.transition` 本身也是先重讀序號
(對不上回 None、外層丟 `_no_progress`/`LeaseLost`)再檢查送出次數,這兩者的檢查順序也沒被這次
改動觸碰。沒有發現順序性的回歸。

## 針對題目 4(重放補寫信封:讀不回提案、並行重放會不會補兩列)——確認沒問題

`_replay` 整段(含 `_backfill_envelope`)都跑在 `InboxStore.replay()` 開的
`immediate_transaction`(`src/rtb/sqlitekit.py` 的 `BEGIN IMMEDIATE` + `BUSY_TIMEOUT_SECONDS`
重試)裡,兩個工作者同時重放同一份升級前(沒有信封)的死信會被序列化:第一個補寫信封並提交後,
第二個重新執行「`SELECT max(id) FROM dead_letters ...`」會看到第一個補寫的那一列,不會再補一次。

實測(臨時 clone,`tests/executor/test_dead_letter.py` 加的
`test_two_simultaneous_replays_of_a_pre_upgrade_dead_letter_backfill_once`):先製造一個沒有
信封的死信(刪掉 `dead_letters`/`dead_letter_ops`),兩個執行緒同時重放,結果
`sorted(outcomes) == ["not_dead_letter", "requeued"]`,信封表最後只有一列。跑法:
```
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider -q -s \
  tests/executor/test_dead_letter.py -k backfill_once
```
「讀不回提案」的分支(`_backfill_envelope` 的 SELECT 找不到列或 payload 解不開)回 `None`,
之後 `_replay_refusal` 會照現有規則回 `NOT_IN_INBOX`/`NOT_DEAD_LETTER`,不會有第二次補寫的機會
(envelope 維持 None,不是多寫一列,是完全沒寫)。沒有發現雙寫。

## 小結

第 1 輪收貨表裡列出的每一條都在 HEAD 上驗證修好,唯一新發現的是 F1:`_aggregate_full`
在「總曝險已滿又過時」這個分支直接假設沒有有效核可,而不像其他新鮮度判斷點一樣先查一次
「這一關此刻是否真的有算數的核可」,導致「預判排除、實際用得到」方向的窄窗被 Phase 8 從
「多一趟往返」放大成「核可白費、決策誤判過時」,違反 [S512]。其餘三個題目(判斷點路徑、
例外順序、補信封並行安全)複驗後都沒有發現問題。
