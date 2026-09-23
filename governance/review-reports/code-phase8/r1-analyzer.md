severity: clean

鏡頭:`src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`(Phase 8 死信等待與新擋下原因)。逐項核對如下,均未發現問題。

## 1. 死信等待的邊界與 `_belongs_to` 一致性核對

`flow.py` 新增:

引句:「if answer.state == "dead_letter" and now < proposal.decision_expires_at:」

邊界是 `now < decision_expires_at` 才等,`now == expires_at` 就不等、落到 `_closed`。這跟收件口自己判過期的邊界一致:`inbox_store.py` 的 `_check_revision_and_times`(`proposal.decision_expires_at <= now` 即判過期)、`_replay_refusal`(`row[2] <= _iso(now)` 即 EXPIRED)、`awaiting()` 的 sweep(`p.expires_at <= ?`)三處全部用 `<=` 當「到期那一刻起算過期」,分析端用嚴格 `<` 當「還沒過期才等」正是同一個邊界的另一面寫法。新測試 `tests/analyzer/test_dead_letter_wait.py` 明確釘了兩側(`EXPIRES - 1s` 等待、`EXPIRES` 結案),斷言註解也寫明「到期那一刻起算已過期(跟收件口判過期同一個邊界)」。

需要注意但不是問題的一點:收件口從不會把 `dead_letter` 這個 disposition 自動轉成 `state='expired'`(只有 `PENDING`=`disposition IS NULL` 的列會被 `_accept_in_transaction` 裡的 sweep 轉;死信列 disposition 一直是 `dead_letter` 直到重放)。所以「過期」完全是分析端自己拿快照裡的 `decision_expires_at` 跟自己的 `now` 比對出來的,這正是設計註解「過期沒由分析端用自己的時鐘比對快照的到期時間(收件口不會把死信轉成已過期)」要的行為,不是缺陷。

`_belongs_to` 的一致性核對:

引句:「consistent = answer.state in _KNOWN_STATES and (」

對 `dead_letter` 這個狀態,`_accept_in_transaction` 裡回 `block_code` 的 CASE 只在 `disposition = BLOCKED` 時才給值(見 `inbox_store.py:548-550`),死信 disposition 永遠讓 `block_code` 是 `None`,剛好符合 `_belongs_to` 對非 `blocked` 狀態要求 `block_code is None` 的核對,兩邊型別/取值來源對得上,不會誤判「別的提案的回應」。

## 2. 兩個新擋下原因

`_REPLAN_ON_BLOCK` 把 `policy_version_changed`→`ReplanReason.POLICY_VERSION_CHANGED`、`decision_stale`→`ReplanReason.DECISION_STALE` 都接進 `_BLOCK_CODES`(供 `_belongs_to` 認得)與重新規劃分派(`_from_inbox_answer` 裡 `if answer.state == "blocked" and answer.block_code in _REPLAN_ON_BLOCK: return _replan(_REPLAN_ON_BLOCK[answer.block_code], ...)`)。字串值跟執行端 `src/rtb/executor/inbox_store.py:100-101`(`BlockCode.POLICY_VERSION_CHANGED = "policy_version_changed"`、`BlockCode.DECISION_STALE = "decision_stale"`)完全對得上,`src/rtb/executor/execution.py:324,725-726,861,1030` 送出的正是這兩個值。

接續關係表記的原因:`_write_follow_up` 寫 `head = f"replan={reason.value}"`,跟 `_closed`/`_replan` 傳入的 `detail`(`f"blocked={answer.block_code}"`)串接成 `error_detail`,格式跟舊的 `version_changed`/`expired` 路徑一致,沒有另外分岔。新測試 `test_a_new_block_reason_hands_the_task_over_to_a_follow_up` 驗了 `f"blocked={code}" in detail and f"replan={reason.value}" in detail`,且直接讀 `follow_ups` 表核對 `reason` 欄寫的是列舉值本身,通過。

接續鏈上限(`MAX_GENERATION = 3`,`task_store.py:67`)是跨原因共用的通用機制,新原因沒有另開分支繞過,適用同一套代數計算與 `LIMIT_REACHED` 結果。

舊資料庫的接續關係表原因欄有沒有允許值限制:

引句:「original_task_id TEXT PRIMARY KEY, follow_up_task_id TEXT UNIQUE,」

`follow_ups` 表的 `reason TEXT NOT NULL` 沒有 `CHECK (reason IN (...))` 這種允許值限制(跟 `inbox_store.py` 裡 `block_code`/`dead_letter_reason` 那種用 `_in_list` 產生的 CHECK 不同),所以舊資料庫開啟後直接寫入 `policy_version_changed`/`decision_stale` 不會被 CHECK 擋掉,不需要遷移動作。這點筆記(`Systems/分析行程流程與檢查點.md`、`Systems/執行迴圈.md` 等)裡若有描述 follow_ups 的欄位限制,應以程式碼這份 CREATE TABLE 為準——目前沒看到筆記跟這裡矛盾。

## 3. Phase 5 [S304] 死信改動後跟 [S317]、接續任務、F4/F5 的互動

`_from_handed_off` 對 `submit()` 丟出 `SubmitStale` 的判斷順序沒變:先判 `str(stale) in _PURGED_CODES`(`{"expired_proposal", "revision_out_of_order"}`)才走 `_from_dsp`([S317] 清表路徑),否則才是一般的 `_closed`。只有沒被清掉、收件口確實還回 `dead_letter` 狀態的 `Accepted` 才會進到新的等待/結案分支;一旦收件表因保留期被清掉,永遠是先進 `_from_dsp` 重新規劃(`ReplanReason.AFTER_RETENTION`),跟死信是否過期無關。這正好對上設計計劃 r3 折入的裁定(「死信過期就結案」限定在收件口還回得出死信時,分析端停機跨過保留期的過期死信改走 [S317] 重新規劃)。

驗證:收件口的 `_purge_finished_tasks` 只清 `SUM(OPEN)=0`(`OPEN` 不含 `dead_letter` disposition,即死信本身就算「非 open」)且 `MAX(received_at) < now - RETENTION` 的任務;而 `RETENTION(2h) > MAX_DECISION_LIFETIME` 是模組載入時的斷言(`inbox_store.py:413-414`),所以死信列被清掉之前,決策必然早已過期——不會出現「決策還沒過期但已經被清表」這種讓兩條路徑打架的情形。

實測:在 repo 內(唯讀,未改動任何檔案)用 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider tests/analyzer/test_f4_end_to_end.py tests/analyzer/test_f5_end_to_end.py tests/analyzer/test_dead_letter_wait.py tests/analyzer/test_replan.py tests/analyzer/test_f6_end_to_end.py tests/executor/test_dead_letter.py -q` → `108 passed`;`tests/analyzer` + `tests/executor` 全跑 → `835 passed`。F4 端到端、F5 事故合約兩份既有測試(這次 diff 沒有改到它們)都還是綠的,沒有被死信等待邏輯或新擋下原因波及。

## 4. `_from_inbox_answer` 多收參數後的呼叫端與測試

簽名從 `_from_inbox_answer(answer)` 改成 `_from_inbox_answer(answer, proposal, now)`。

引句:「return _from_inbox_answer(answer, row.proposal, now)」

用 `grep -rn "_from_inbox_answer" src/rtb/` 核對,整個 `src/` 只有這一處呼叫端(`_from_handed_off` 內),沒有其他生產程式碼直呼這個私有函式。測試側 `tests/analyzer/test_replan.py` 也同步改了唯一一處直呼:舊 `assert _from_inbox_answer(answer) is None` 改成 `assert _from_inbox_answer(answer, prop, h.clock()) is None`,已跟上新簽名,沒有殘留舊呼叫。

## 筆記對照

筆記與程式碼在本次覆核範圍內沒有發現互相矛盾之處;`Systems/分析行程流程與檢查點.md` 對死信等待的描述跟 flow.py 實作、以及計劃筆記 [S507] 的合約句一致,不需要另立 Issue。
