severity: clean

材料:`/Users/enzo/rtb-3b` HEAD(8ed6710),對照 `governance/review-reports/code-phase8/r2-intake.md`
的重現表(regress-F1、finder-F1、veto-F1、finder-F2)逐條複驗,並補跑對稱情境。實驗一律在臨時
clone `/private/tmp/claude-501/.../scratchpad/rtb-3b-copy` 做(`git -C` 或 cd 進臨時目錄),沒有
動過 `/Users/enzo/rtb-3b`。

## 逐條核對第 2 輪重現表

**regress-F1 / finder-F1(總曝險需不需要核可,以開始一筆交易裡握鎖重算的已用額度決定)**

`_take` 現在把「開始一筆前查好的 held」與「決策過時要不要放行」拆開:到期 → 被取代
(`_superseded`,`src/rtb/executor/execution.py:665`)→ 比例核可過期(`_stop_before_begin` 裡
`RATIO in held and RATIO not in live`)→ 過時又需不需要核可(`_approved_while_stale`,
`execution.py:726-744`)→ 開始一筆(`attempt_store.begin`)。`_approved_while_stale` 用
`_aggregate_used(tx, tenant.name, now)`(`execution.py:736`)算「這一次真的需不需要」,這支函式
底層呼叫 `attempt_store.aggregate_used(tx, ...)`(`attempt_store.py:380`),`attempt_store.begin`
內部(`attempt_store.py:351`)也是呼叫同一支函式、同一個 `tx`——`self.store.transaction()` 在
`_take` 進場就是 `BEGIN IMMEDIATE`(`sqlitekit.py:46`),整段期間握著寫入鎖,兩次呼叫之間沒有
別的寫入者能插進來,兩處讀到的是同一個數字。跑既有測試
`test_an_aggregate_approval_needed_only_inside_the_write_still_counts`(預判用不到、寫入時額度
滿了、有核可)結果 `EXECUTED`、`approval_uses` 記到 `aggregate_limit_reached`;
`test_an_aggregate_approval_freed_up_before_the_write_does_not_exempt_a_stale_decision`(預判要用、
寫入時額度已釋放)結果 `BLOCKED/decision_stale`、`dsp.writes == []`。兩條都綠,方向對稱,r2 的
F1(finder/veto/regress 三席同一個洞)修到。

**veto-F1(比例核可查好後過期、決策又過時,要先放掉重來、再判過時)**

`_superseded` 迴圈(`execution.py:665`)在 `_stop_before_begin` 之前、且不看 `stale`,任何一關
被取代都先 `release`+`DEFERRED`。我在臨時 clone 補了一個直接測這個順序的案例:比例核可先查好,
決策已過時,查好之後、開始一筆前又有人簽了一張新的(用 `h.clock.advance(1)` 讓兩張核可內容真的
不同,不是巧合湊出同一個 id)——結果 `DEFERRED`、`dsp.writes == []`,不是被擋成
`decision_stale`。第一次寫這條測試時沒推進時鐘,兩張核可的簽發時間相同,`approval.issue` 產生
的 token 逐位元組一樣,`approval_id` 因而相同,`_superseded` 判不出差異、誤判「沒被取代」——這是
我測試設計的失誤,不是程式的問題,加上 `advance(1)` 改對之後才看出真正的行為,順帶留意:
`_superseded`/`approval_id` 是「內容雜湊」而非序號,同一秒內容全同的兩張核可會被當成同一張,這
點如果之後要測「同一秒內兩次核可」要記得帶點不同的欄位或時間,不算本輪的洞。

**finder-F2(讀不回提案的死信重放要拒絕,不能照樣放回、稽核記成功)**

`inbox_store.py` 新增 `ReplayOutcome.UNREADABLE`(`inbox_store.py:362`),`_replay_refusal`
(`inbox_store.py:773-795`)在查到「pending + dead_letter」之後、過期判斷之前,先用
`_parse_payload(row[3]) is None` 擋下讀不回的那一列,回 `UNREADABLE`,不會走到後面的
`UPDATE ... SET disposition = NULL`(即不會被放回待處理)。既有測試
`test_a_dead_letter_whose_proposal_cannot_be_read_back_is_not_replayed`
(`tests/executor/test_dead_letter.py:406-417`)直接驗證:`replay()` 回 `UNREADABLE`,
`disposition` 仍是 `("pending", "dead_letter")`,稽核兩列是
`("replay_requested", OPERATOR, None)`、`("replay_refused", OPERATOR, "unreadable")`——沒有補
信封、沒有第二次機會、稽核沒記成功。r2 finder-F2 折到。

## 補測開始一筆判斷順序的對稱組合(新鮮/過時 × 有核可/沒核可 × 預判對/不對)

r2-intake 表跟既有測試涵蓋的是「過時」那一側;我在臨時 clone 額外加了三條「新鮮」側與「被取代」
側的鏡像測試,確認同一段程式碼在另一半組合下也照設計走(不是只有過時分支被修好、新鮮分支意外
壞掉):

1. `test_a_fresh_decision_whose_ratio_approval_lapses_before_the_write_awaits_a_new_one`:新鮮的
   決策,查好的比例核可在開始一筆前過期、沒有新的一張 → `AWAITING_APPROVAL`(回待核可),不是
   `BLOCKED`。跟過時側的鏡像測試(`test_a_stale_decision_whose_ratio_approval_lapses_before_the_write_is_blocked`)
   對照,差別只在新鮮/過時,行為對稱。
2. `test_fresh_decision_aggregate_needed_only_inside_write_awaits_not_blocked`:新鮮的決策,預判
   用不到總曝險核可、開始一筆時額度滿了、也沒有核可 → `AWAITING_APPROVAL`(回待核可等新核可),
   不是執行、也不是擋下——這是 Phase 6 就有的既有窄窗(多一趟往返),Phase 8 沒有動到它。寫這條
   時第一次假設「預判會呼叫一次 `aggregate_used`、開始一筆再呼叫一次」而用兩段式假資料
   (`_usage` 寫法)硬套,結果錯判成 `EXECUTED`——原因是 `_approvals()` 在完全沒有核可 token
   時直接跳過已用額度查詢(`execution.py:511` 的 `tokens[AGGREGATE] is not None and (...)`
   短路),沒有核可的提案預判階段根本不會呼叫 `aggregate_used`,少了一次呼叫、我的假資料序列
   跟著錯位。這是我測試工具用錯,不是程式的洞;改成單純把 `aggregate_used` 固定回填一個值後
   測試才對得上真實呼叫次數。
3. `test_a_stale_decision_whose_approval_is_superseded_defers_instead_of_blocking`:見上面
   veto-F1 段落,確認「先放掉重來、再判過時」在過時側也生效。

三條加上既有 26 條全綠(`tests/executor/test_stale_decision.py` 共 27 條)。

## 握鎖重算的已用額度跟開始一筆自己算的是否同一個數字

已在 regress-F1 段落確認:兩處都是 `attempt_store.aggregate_used(tx, tenant, now)`,同一個
`tx`(從 `_take` 開頭 `BEGIN IMMEDIATE` 到結束都是同一個),中間沒有別的交易能插入寫入,是同一個
數字、不是兩次獨立預判。

## 開始一筆記核可使用是否記到實際用上的那一張

`_audit`(`execution.py:754-770`)寫 `ApprovalUse` 時取的是 `live[AGGREGATE].approval_id`;
`live[AGGREGATE]` 在 `_approved_while_stale` 裡是被 `self.store.latest_approval` +
`_read_approval` 這次現查現讀出來的 `found`(`execution.py:739-743`),不是預判階段
(`_approvals()`/`held`)那張可能已經不算數或根本沒查過的舊快照;而且只在
`begun.over_limit is not None`(即這一筆真的超過門檻)才記,沒用上不記。跟
`test_an_aggregate_approval_needed_only_inside_the_write_still_counts` 裡
`assert h.query("SELECT stage FROM approval_uses") == [("aggregate_limit_reached",)]`
的斷言一致。

## 回歸

`/Users/enzo/rtb-3b`(真實 HEAD)全套測試:1542 passed(78.8s),ruff `All checks passed!`。
臨時 clone 加了 4 條新測試後 `tests/executor/`:570 passed。臨時 clone 跑全套 `pytest` 時
`test_static_checks.py::test_ruff_reports_no_rule_violations_anywhere_in_the_project` 落紅,錯誤
指向 `tests/domain/test_metrics.py:212` 的 `# noqa` 格式;diff 這個檔案跟真實 HEAD 完全一致
(`diff` 無輸出),只在真實 repo(`/Users/enzo/rtb-3b`)跑 `ruff check .` 是
`All checks passed!`——是臨時 clone 的環境問題(路徑/設定差異),不是這次改動的回歸,不算發現。

## 小結

r2-intake.md 表裡四條(regress-F1、finder-F1、veto-F1、finder-F2)在 HEAD 上都驗證修到,且用
既有測試與臨時 clone 裡的鏡像測試確認了對稱方向(新鮮側、被取代側)沒有被順手弄壞。開始一筆的
判斷順序(到期 → 被取代 → 比例核可過期 → 過時與核可 → 開始一筆)在新鮮/過時、有核可/沒核可、
總曝險預判對/不對的各種組合下都照裁定走:核可過的提案不被新鮮度擋(`_approved_while_stale`
永遠用鎖內現查現讀的核可)、過時的決策不靠用不到的核可寫進 DSP(不需要時直接看 RATIO,完全
忽略 AGGREGATE 的 held/live)、過時又缺核可不停進待核可等新核可(`_block_stale` 直接擋,不
`_await_in`)。握鎖重算的已用額度與開始一筆自己算的是同一個數字(同一個 `tx`、同一支函式)。
核可使用紀錄記的是實際用上、現查現讀的那一張,不是預判階段的舊快照。沒有發現會擋合併的新問題。
