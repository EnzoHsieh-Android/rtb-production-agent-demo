severity: minor

範圍:對照 `/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase8死信重放與過時決策_計劃.md` 的最小設計 1–6 節、[S500]–[S512]、兩則使用者裁定、〈不做〉節,逐句核對 `r1-snapshot-src.patch`(analyzer/flow.py、task_store.py、executor/execution.py、guardrails.py、inbox_store.py、replay.py 新檔)與三篇既有 Systems 筆記、新開的 `Systems/死信重放指令.md`。另在 `/Users/enzo/rtb-3b` 實跑 `tests/executor/` `tests/analyzer/` 全套,835 passed(含 `test_dead_letter.py` 等列在 [S500]–[S512] 的所有具名測試)。

## 逐句核對結果(合約 [S500]–[S512])

- [S500] 信封在寫死信處置的同一交易寫入,`_deliver` 只在 `self._finish(...)` 回真(交易確實贏得寫入)才呼叫 `_record_dead_letter`,`inbox_store.py:648-652`;重放後再死信會再插一列(`test_a_replayed_proposal_that_dead_letters_again_gets_a_second_envelope` 綠)。兌現。
- [S501] `FailureClass`/`failure_class()` 對 `LastFailure`/`BlockCode` 逐一分類,`test_every_failure_kind_is_classified` 綠。兌現(注意此函式只在測試裡呼叫,production code 寫死信信封時直接寫字面 `FailureClass.TRANSIENT.value`,不經過 `failure_class()`——這不違反合約,合約只要求"兩個列舉每個成員都有分類"有測試守住,不要求 production 呼叫它)。
- [S502] `InboxStore.replay()`/`_replay_refusal()` 依序核對:表裡還在→處置是死信→未過期→同任務無更新修訂→待處理有名額,查與寫回都在同一個 `immediate_transaction` 裡,`inbox_store.py:718-757`。兌現,雙人同時重放測試綠。
- [S503]–[S506]、[S509] 由 `test_a_replayed_proposal_goes_through_every_gate`、F6 端到端 `test_f6_a_stale_dead_letter_is_revalidated_on_replay` 覆蓋,綠。信封與嘗試共用 `operation_key(proposal)`,`inbox_store.py:706` 起,兌現 [S506]。
- [S504] `precheck()` 新增 `if proposal.policy_version != POLICY_VERSION: return BlockCode.POLICY_VERSION_CHANGED`,排在 `VERSION_CHANGED` 之後,`execution.py:312-313`。逐字兌現計劃第 4 節「順序:排在版本已變之後」。
- [S505] `guardrails.decision_stale()`:`now - proposal.decision_created_at > DECISION_FRESHNESS`,嚴格大於,剛好 15 分不算過時,`guardrails.py:667-670`;開始一筆的交易裡再判一次見下方「偏離」。兌現(邊界測試 `test_a_stale_decision_is_blocked` 綠)。
- [S507] `flow.py:_from_inbox_answer`:`dead_letter` 且未過期回 `None`(等待);過期落到 `_closed`(結案不重新規劃);`policy_version_changed`/`decision_stale` 併入 `_REPLAN_ON_BLOCK` 觸發重新規劃。`test_the_analyzer_waits_on_a_live_dead_letter_and_replans_on_stale_reasons`、`test_an_expired_dead_letter_closes_the_task_without_a_follow_up`、`test_a_new_block_reason_hands_the_task_over_to_a_follow_up` 都綠。兌現。
- [S508] 每個 `DeadLetterAction`(`dead_lettered`/`replay_requested`/`replay_refused`/`replay_requeued`)都經 `_audit_dead_letter` 寫一列,只增不改。兌現。
- [S510] `test_an_old_inbox_gains_the_dead_letter_tables` 綠,建表語句用 `CREATE TABLE IF NOT EXISTS`。兌現。
- [S511] 兩條重跑路徑(`_after_expiry`、`_reconcile_not_found`)命中 `policy_version_changed` 走 `_kept_reason_or_none`,命中 `decision_stale` 走 `_DecisionStale` 例外分別接到 `_not_resent`/`_void_then_fail` 並帶 `BlockCode.DECISION_STALE`。兌現,對應測試綠。
- [S512] `_stale()`→`_stale_in()`:先判 `decision_stale`,不過時就直接放行;過時時逐一查 `APPROVABLE` 兩關的「此刻算數的最新核可」,任一關有效就整體免判、其他檢查照跑;`_run()` 裡 `_stale` 排在 `_gate`(比例)之前,所以「過時又超過比例、沒核可」直接擋成 `decision_stale`,不進待核可,逐字對上計劃例句。兌現。

## 自承偏離覆核

- 偏離(1)(新鮮度排簽發後、比例前):`execution.py:_run`,`amount = guardrails.increase(...)` 之後才呼叫 `self._stale(proposal, signed.tenant, amount)`,而 `signed` 已在呼叫 `_run` 之前完成(取自 `execute()`→`_run(picked, receipt, signed, view)`)。核對後理由屬實:核可查法要驗 `scope_fingerprint(tenant)`,租戶設定要在簽發後才拿到;`實作狀態` 自承的「簽發沒有副作用、只影響同時違反兩條時報哪個原因」屬實,`_stale` 排在 `_gate` 之前,過時同時超比例仍先判 decision_stale,不會先進待核可再簽廢核可。這處偏離寫得清楚、理由站得住。
- 偏離(2)(重跑執行前檢查不另判新鮮度):`precheck()` 只加了 `POLICY_VERSION_CHANGED`,沒有加 `decision_stale` 判斷;新鮮度只在 `_stale_on_rerun`(轉嘗試中交易)判一次。核對 `_after_expiry`/`_reconcile_not_found` 的呼叫順序:`checked = precheck(...)` 不含新鮮度,`_in_flight_again` 裡才拋 `_DecisionStale`。跟自承的「兩處重複、拿掉一處結果不變」一致。寫得清楚。

## 未自承的第三處差異(minor,non-blocking)

## F1 首次判斷用「此刻有效核可」、重送判斷用「開始一筆時用過核可」,兩套判準不同,計劃與「實作狀態」都沒提

severity: minor
blocking: 否 — 兩條路徑各自都有測試釘住且行為在 Systems 筆記裡有解釋,不是誤判或資料損壞,只是「實作狀態」自承清單只列兩處、漏了這一處

引句:「同鍵重送時改看這份提案開始一筆時有沒有用過核可,用過的核可此刻算不算數照舊在重簽時核對」

`_stale`/`_stale_in`(首次判斷,`execution.py:405-420`)用 `_read_approval` 逐一查 `APPROVABLE` 兩關「此刻此金額此範圍指紋算不算數的最新核可」,只要有一關目前仍有效就整體免判新鮮度;`_stale_on_rerun`(重送前判斷,`execution.py:422-429`)改成 `not self.store.used_approvals(tx, proposal)`——查的是「開始一筆那次有沒有實際用過某張核可」,不重新驗那張核可此刻是否仍在範圍指紋、金額、到期之內(那件事留給 `still_latest` 裡的 `_superseded` 另外核對「是否仍是最新」,不是「此刻是否仍算數」的完整核可查法)。

`計劃`「使用者裁定:待核可提案與決策新鮮度」與 [S512] 的文字都只講「有這一關的有效核可就不判新鮮度,照增量 3 的核可查法判」,沒有區分「首次」與「重送」該用哪一種判準;`實作狀態` 節也只自承兩處偏離,沒提到重送改用「用過」而非「查法有效」這第三處差異。這差異本身有 `test_an_approved_resend_is_not_blocked_as_stale` 釘住、且 `執行迴圈.md` 的 WHY 段落解釋清楚(重送前用過的核可,此刻是否仍算數由重簽時的 `_superseded` 另外核對),行為上不會漏掉「核可已不算數」的擋下,只是判斷新鮮度免判的依據換了一種資料來源,跟計劃文字字面上「照增量 3 的核可查法判」不完全一致。建議之後補進「實作狀態」的自承清單,或把 [S512] 的合約句改成同時涵蓋兩種判準,免得下一個 session 只看計劃文字會以為兩處判準相同。

## 筆記與程式比對(三篇既有家 + 新開的死信重放指令)

- `Systems/分析行程流程與檢查點.md` 新增段落逐句對得上 `flow.py` 改動(死信等待邊界、政策已變/決策已過時併入重新規劃、F6 端到端),已核對。
- `Systems/執行迴圈.md` 新增段落(新鮮度排序、有效核可例外、三個判斷點、重跑保留原因、政策版本部署視窗、測試樣本政策版本雷)全部對得上程式,唯獨上面 F1 提到的「首次 vs 重送兩種判準不同」這一句筆記寫了(WHY 第三段),但計劃本身的[S512]與「實作狀態」沒有跟著寫,筆記比計劃更準確地反映了程式,這點值得留意但不是筆記錯。
- `Systems/提案收件口.md` 新增段落(信封/稽核只增不改、重放條件與交易邊界、操作人格式驗證分工、回退前的查法 SQL)逐句核對:回退查法 `SELECT count(*) FROM proposals WHERE state = 'pending' AND disposition = 'dead_letter' AND expires_at > ...` 對照 `_deliver` 只寫 `disposition, dead_letter_reason` 不動 `state`(仍是 `'pending'`)——SQL 語法與欄位都對得上實際 schema。兌現。
- 新開的 `Systems/死信重放指令.md`:責任邊界(「不負責重放條件判斷、放回與稽核」「不碰 DSP、不簽憑證、不讀租戶設定」)對照 `replay.py` 的 import 與函式體(只 import `rtb.domain._checks.is_id` 與 `rtb.executor.inbox_store` 幾個名字,`run()` 只做操作人格式檢查→開店→呼叫 `store.replay()`→印結果),完全對得上;操作人格式驗證比照 `approve.py`/`approval.issue()` 用同一支 `is_id`,退出碼 4/6 跟 `approve.py`/`runner.py` 的 `EXIT_REFUSED`/`EXIT_BUSY` 數值一致。兌現。

## 〈不做〉節核對

- 轉人工的嘗試不進死信:`_record_dead_letter` 只在 `_deliver()` 裡「沒有嘗試紀錄且投遞次數用完」這一個觸發點呼叫,程式裡再無第二處寫 `dead_letters` 表,ESCALATED/resolve 路徑不碰這張表。兌現。
- 不做自動/排程/批次重放:`replay.py` 的 CLI 一次只收一組 `--task-id --revision --operator`,沒有迴圈或排程邏輯。兌現。
- 不做網頁介面、不做告警:程式裡沒有對應檔案。兌現。

## 結論

規格符合度高:[S500]–[S512] 全部逐句兌現且有對應綠測試;兩處自承偏離理由站得住、寫得清楚;三篇既有 Systems 筆記與新開的死信重放指令筆記跟程式行為一致,沒有筆記說一套、程式做另一套的情況。唯一沒有完全自承的落差是 F1:決策新鮮度的「有效核可免判」在首次執行與同鍵重送用了兩種不同判準(此刻查法有效 vs 開始一筆時用過),這差異有測試釘住、行為安全,但計劃的「實作狀態」清單只列了兩處偏離,建議補記或修訂 [S512] 措辭。
