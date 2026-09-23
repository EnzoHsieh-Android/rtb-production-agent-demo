判定: 不同意

# 事故 F6 合約審計(獨立審計員,無先備脈絡)

審計對象:`docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`(工作目錄未提交版本)開頭欄位裡
「KEY:★INVARIANT★ 事故 F6:……」那一行,及其 `[test:…]` 綁定測試與 `kill_recipes` 裡 `invariant` 為
「事故 F6」的 22 筆殺傷力配方。

驗證方式:讀綁定測試檔的實際斷言(不只看測試名);跑
`python3 scripts/lumos guard kill Systems/提案收件口 "事故 F6" --vault /Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge`
——22 筆全部 `verdict: killed`(附加驗證:`docs/.governance-log.jsonl` 外,另跑
`tests/executor/test_dead_letter.py tests/executor/test_stale_decision.py tests/analyzer/test_dead_letter_wait.py
tests/analyzer/test_f6_end_to_end.py` 在未套壞法的原始碼上是 63 passed,基線乾淨)。再逐條對照
`src/rtb/executor/{inbox_store,execution,guardrails,capability_signer}.py`、`src/rtb/analyzer/flow.py` 的
實際程式行為,檢查措辭是否比實作滿。

## 宣稱拆解與逐條核對

| # | 宣稱 | 測試與斷言(檔:行) | 配方(note) | 守住? |
|---|---|---|---|---|
| 1 | 死信只能由管理指令逐筆重放,不會自己再自動交出去 | `tests/executor/test_dead_letter.py:421-429` `test_a_dead_letter_is_never_handed_out_again`:斷言 `h.process().kind is Result.IDLE`、`disposition==("pending","dead_letter")` 不變、`len(h.dsp.reads)` 不變 | `inbox_store.py` `if deliveries >= MAX_DELIVERIES` → `if False`,note「投遞次數用完也不進死信,讀不到 DSP 的提案永遠被交出去」→ killed | **部分守住**。「不會自動交出去」有直接斷言+配方翻紅。「只能由管理指令」這半句只是程式碼推論(全庫只有 `inbox_store.py:762` 一處清 `dead_letter_reason`,且只被 `InboxStore.replay()` 呼叫,該方法只被 `replay_tool` CLI 呼叫並驗操作人格式),沒有任何測試斷言「除了這個入口沒有別的路徑能清掉死信狀態」 |
| 2 | 重放只把它放回待處理(不改別的欄位、不略過關卡) | `tests/executor/test_dead_letter.py:126-132` `test_a_replay_requeues_only_a_live_dead_letter`:`SELECT state, disposition, dead_letter_reason, deliveries, lease_owner FROM proposals` == `[("pending", None, None, 0, None)]` | `inbox_store.py` UPDATE 語句拿掉 `disposition = NULL` 那一半 → note「重放回報已放回,處置卻還是死信」→ killed | 守住 |
| 3 | 放回後照一般流程重跑每一關(廣告現況與版本、政策版本、決策新鮮度、權限、比例、總曝險) | `tests/executor/test_dead_letter.py:337-347` `test_a_replayed_proposal_goes_through_every_gate`(parametrize 6 種:`version_changed`/`policy_version_changed`/`decision_stale`/`campaign_not_allowed`/`budget_increase_too_large`/`aggregate_limit_reached`),每組斷言 `(result.kind, result.block_code) == (kind, code)` 且 `h.dsp.writes == []` | 對應 6 筆配方(`execution.py` 版本檢查、`execution.py` 政策檢查、`guardrails.py` 新鮮度、`capability_signer.py` 租戶、`execution.py` 比例、`execution.py` 總曝險核可)→ 全部 killed | 守住(「重跑核可關」實際驗到的是「超過門檻又沒核可仍會停下,不被略過」,不是「核可通過後真的寫成」——但宣稱本身就是「沒有略過」,不是「核可會成功」,措辭與驗到的範圍相符) |
| 4 | 廣告版本變 → 擋下 `version_changed`;分析端另開接續任務;舊決策不寫入 DSP | `tests/analyzer/test_f6_end_to_end.py:123-142` `test_f6_a_stale_dead_letter_is_revalidated_on_replay[version]`:`original.state is BLOCKED`、`error_detail` 含 `blocked=version_changed;replan=version_changed`、`child == follow_up_id('t1')`、`STALE_BUDGET not in writes` | `execution.py` `if view.version != proposal.campaign_version_observed` → `if False` → killed | 守住 |
| 5 | 提案政策版本不是現行版本 → 擋下 `policy_version_changed`,同上 | 同上檔 `[policy]` 分支,斷言同構 | `execution.py` `if proposal.policy_version != POLICY_VERSION` → `if False` → killed | 守住 |
| 6 | 決策建立超過 15 分鐘 → 擋下 `decision_stale`;剛好 15 分鐘不算過時 | `tests/executor/test_stale_decision.py:64-78` `test_a_stale_decision_is_blocked`,parametrize `(15min, EXECUTED)` `(15min1s, BLOCKED)`,斷言 `block_code is STALE`、`_block_of(h)==("blocked","decision_stale")`、`h.dsp.writes==[]` | `guardrails.py` `DECISION_FRESHNESS = timedelta(minutes=15) - timedelta(seconds=1)` → note「剛好 15 分鐘也擋成過時(邊界說得比實際嚴)」→ killed | 守住 |
| 7 | 這一次真的需要、此刻有效的核可,決策新鮮度不判 | `tests/executor/test_stale_decision.py:139-148` `test_an_approved_proposal_skips_only_the_freshness_check`:核可比例關卡放回後 `result.kind is EXECUTED`(超過 15 分鐘仍執行);`test_stale_decision.py:230-239` `test_an_unneeded_approval_does_not_exempt_a_stale_decision`:掛著用不到的總曝險核可仍擋成 `STALE` | `execution.py` `if not needed: return "go" if RATIO in live else "block"` → `return "block"`,note「核可過的提案超過 15 分鐘被擋成過時,核可白簽」→ killed;另一筆「決策過時又沒有這一次需要的核可,照樣放行」→ killed | 守住 |
| 8 | 廣告版本、政策版本、決策新鮮度都沒變就照常重放寫成 | `test_f6_a_stale_dead_letter_is_revalidated_on_replay[unchanged]`:`writes==[STALE_BUDGET]`、`original.state is COMPLETED`、`child is None` | `inbox_store.py` UPDATE 條件加 `AND 0` → note「重放回報已放回卻沒真的放回,沒有任何變化的死信也寫不成」→ killed | 守住 |
| 9 | 已不是死信(已放回)→拒絕重放 | `test_dead_letter.py:126-132` 第二次 `replay(h)` → `ReplayOutcome.NOT_DEAD_LETTER` | 與 #10 同一段程式碼(`if row[0]!="pending" or row[1]!=DEAD_LETTER: return NOT_DEAD_LETTER`)守著 | 守住 |
| 10 | 已不是死信(已擋下)→拒絕重放 | `test_dead_letter.py:432-437` `test_a_blocked_proposal_cannot_be_replayed`:先讓提案被 `policy_version_changed` 擋下,再 `replay(h) is ReplayOutcome.NOT_DEAD_LETTER`,`disposition==("pending","blocked")` | `inbox_store.py` `if row[0]!="pending" or row[1]!=Disposition.DEAD_LETTER.value: return NOT_DEAD_LETTER` → `if False`,note「已擋下的提案也能重放,擋下形同虛設」→ killed | 守住 |
| 11 | 已過期 → 拒絕重放 | `test_dead_letter.py:135-141` `test_a_replay_is_refused_after_the_proposal_expires`:`replay(h) is ReplayOutcome.EXPIRED`,`disposition` 不變 | `inbox_store.py` `if row[2] <= _iso(now): return EXPIRED` → `if False` → killed | 守住 |
| 12 | 同任務已有更新的修訂 → 拒絕重放 | `test_dead_letter.py:149-154` `test_a_replay_is_refused_when_the_task_has_a_newer_revision`:先收下 revision 2,`replay(h, revision=1) is ReplayOutcome.SUPERSEDED` | `inbox_store.py` `if self._highest_revision(task_id) > revision: return SUPERSEDED` → `if False` → killed | 守住 |
| 13 | 存的內容讀不回 → 拒絕重放 | `test_dead_letter.py:406-417` `test_a_dead_letter_whose_proposal_cannot_be_read_back_is_not_replayed`:把 `payload` 改成壞 JSON,`replay(h) is ReplayOutcome.UNREADABLE`,稽核記 `replay_refused/unreadable` | `inbox_store.py` `if _parse_payload(row[3]) is None: return UNREADABLE` → `if False` → killed | 守住 |
| 14 | 收件口回死信、決策還沒過期 → 分析端等待 | `tests/analyzer/test_dead_letter_wait.py:22-32` `test_the_analyzer_waits_on_a_live_dead_letter_and_replans_on_stale_reasons`:`live is TaskState.HANDED_OFF`,`store.latest("t1").seq==before`,`follow_up_to is None` | `flow.py` `if answer.state=="dead_letter" and now<proposal.decision_expires_at` → `if False`,note「死信一回來分析端就結案,重放後寫成的任務在分析端已結案」→ killed | 守住 |
| 15 | 決策過期就結案,不重新規劃(不轉人工) | `tests/analyzer/test_dead_letter_wait.py:34-41` `test_an_expired_dead_letter_closes_the_task_without_a_follow_up`:`closed is TaskState.BLOCKED`,`"dead_letter" in error_detail`,`follow_up_to is None` | `flow.py` 拿掉 `now < proposal.decision_expires_at` 條件 → note「過期的死信分析端一直等,任務永遠不結案」→ killed | **多數守住**。「結案、不重新規劃」有直接斷言(`follow_up_to is None`)。「不轉人工」是消極描述:程式碼裡確實沒有任何「轉人工」動作,但沒有專門測試斷言這件事本身,只是「follow_up 為空」間接暗示——算推論帶過 |
| 16 | 每一次進死信、要求重放與結果都寫進只增不改、不被清理的死信信封與稽核 | `test_dead_letter.py:71-82` `test_a_dead_letter_leaves_a_durable_envelope`:信封內容比對,過保留期清理後 `len(envelopes(h))==1`(信封沒被清);`test_dead_letter.py:102-113` `test_a_replayed_proposal_that_dead_letters_again_gets_a_second_envelope`:兩次死信各一列信封,稽核四列;`test_dead_letter.py:204-216` `test_every_dead_letter_operation_is_audited`:稽核五列 `dead_lettered/replay_requested/replay_requeued/replay_requested/replay_refused` | `inbox_store.py` 三筆配方(信封沒寫、保留期清理連信封一起刪、重放放回沒寫稽核)→ 全部 killed | **部分守住**。「每一次進死信/要求重放/結果都寫進稽核」「不被清理」都有直接斷言+配方翻紅。「只增不改」(這兩張表永遠只 INSERT、不 UPDATE)沒有專門測試——只用 `grep` 確認全庫對 `dead_letters`/`dead_letter_ops` 兩表除了建表與索引敘述外只有 `INSERT`/`SELECT`,沒有 `UPDATE`,是程式碼推論,不是斷言 |

## 措辭是否比程式行為滿

對照程式碼後,絕大多數措辭與實作一致,沒有「說得比程式做的滿」的地方:
- 「證據新鮮度以決策建立時間代理」:`guardrails.py:35-38` docstring 原話就是「執行端看不到證據本身,用決策建立時間代替」,一致。
- 「有這一次真的需要、此刻有效的核可則不判」:`execution.py` `_stale_without_approval` 只看呼叫端已篩出的「需要的那幾張」且判 `expires_at`,不是「隨便一張有效核可就免判」,跟措辭一致(這是代碼審第 1 輪從「任一張」改成「這一次需要的」之後的版本,文字也同步改了)。
- 「沒有略過任何一關的旗標」:程式碼裡確實沒有任何 skip/bypass 旗標(`grep -rn "skip\|bypass\|force=True"` 在 `execution.py`/`inbox_store.py`/`flow.py` 無結果),measured 6 個關卡各有配方翻紅,措辭沒有超過驗到的範圍。

唯一偏滿的地方是上表標「部分守住」的三處:「只能由管理指令」「不轉人工」「只增不改」都是**消極/排他性描述**(斷言「不存在別的路徑」),這類描述天然沒辦法只靠一個正面斷言完全鎖死,目前只靠程式碼檢視/grep 頂著,沒有測試把它們釘住。措辭本身沒有錯,但按審計標準(只有推論撐著就算沒守住),這三處還沒有到位。

## 判定理由與建議

**判定:不同意。**

事故 F6 這條 ★INVARIANT★ 裡,16 條可拆解宣稱中有 13 條每一條都能指到「開測試檔讀到的具體斷言」+「跑
`lumos guard kill` 證實會翻紅的配方」,守得很扎實,而且量到的 22 筆配方全數 killed,底層測試套件本身在
乾淨程式碼上跑起來也是綠的,基線沒問題。

但以下三個排他性/消極描述子句只靠程式碼推論,沒有任何測試或機械檢查直接釘住,按專案自己的審計標準
(「只有推論……都算沒守住」)不能算過:

1. 「死信佇列裡的舊工作**只能由**管理指令逐筆重放」——沒有測試證明「沒有別的路徑能把死信改回待處理」,
   只是我人工 grep 全庫確認只有一處清除死信欄位的 UPDATE。建議:加一個結構性測試,例如掃描
   `src/rtb/**/*.py` 斷言「只有 `inbox_store.py` 裡 `_replay` 這個方法會寫 `disposition = NULL, dead_letter_reason = NULL`」,或至少在 `test_dead_letter.py` 補一個嘗試從執行迴圈/收件口 HTTP 端直接把死信改回待處理會被拒絕的測試。

2. 「已過期就結案、不重新規劃(**不轉人工**)」——現有測試只驗到「沒有 follow-up」,沒有驗到「也沒有任何
   轉人工動作」。目前系統裡確實不存在轉人工機制,所以文字沒有說錯,但這個「不存在」本身沒有測試釘住。
   建議:如果系統之後真的加了人工介入通道,要在這裡補一個「過期死信不會出現在待人工處理佇列/不會發出
   人工通知」的迴歸測試;現在沒有這個通道就在筆記裡明講「不轉人工=系統裡沒有這條通道,不是刻意擋下」,
   避免讀者以為有一道機械閂擋著轉人工。

3. 「死信信封與稽核**只增不改**」——只有「不被保留期清理」有測試,「永遠不會被 UPDATE」沒有測試釘住。
   建議:加一個測試直接對 `dead_letters`/`dead_letter_ops` 兩表跑一次 schema 檢查或 monkeypatch 斷言
   `InboxStore` 除了 `INSERT` 之外不對這兩張表下 `UPDATE`/`DELETE`(可以用 sqlite 的 `PRAGMA` 或攔截
   `execute` 呼叫做黑名單檢查),把「只增不改」從程式碼慣例升格成有機械守衛的合約。

其餘 13 條(重放後每一關重跑、四種拒絕重放原因、廣告版本/政策版本/決策新鮮度三種擋下原因與接續任務、
未變照常寫成、死信信封與稽核的正面部分)測試斷言與配方都對得上宣稱,措辭也沒有比程式行為誇大,判定
上不需要修改用詞,只需要補上面三項的機械守衛或明講「這是推論,不是有守衛的合約」。

## 摘要

事故 F6 的死信重放合約在「重放要重新過每一關、拒絕四種不該重放的死信、擋下原因對到接續任務、信封稽核
留痕」這些核心業務行為上都有真斷言和真的會翻紅的配方守著,基礎打得很穩;但「只能由管理指令走這條路」
「不轉人工」「信封稽核表永遠不會被改」這三句排他性描述目前只靠我人工看程式碼推出來,沒有專門測試釘住,
所以整體判定不同意,建議照上面三條補測試或改措辭後再轉正。
