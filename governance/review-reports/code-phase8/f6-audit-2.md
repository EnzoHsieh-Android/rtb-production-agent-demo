判定: 同意

## 方法
把 `Systems/提案收件口.md`（工作目錄版本）開頭那一行 `KEY:★INVARIANT★ 事故 F6:...` 拆成 23 條獨立宣稱；每條去對應測試檔開實際斷言（不只看測試名），並比對 `kill_recipes` 裡標 `"invariant": "事故 F6"` 的配方是否真的會讓那個斷言翻紅。另外把整個 repo 複製到 `/tmp/f6audit/rtb-3b`（含 `.git`，`--no-verify` 快照一個 commit 純粹為了讓 `lumos guard kill` 能以 HEAD 為基準跑沙盒；沒有動到 `/Users/enzo/rtb-3b` 本身的工作目錄或暫存區），跑：

```
lumos --vault /tmp/f6audit/rtb-3b/docs/rtb-production-agent-demo-knowledge guard kill "Systems/提案收件口" "事故 F6" --json
```

結果：25 條配方全部 `verdict: killed`（無 `survived`）。

## 宣稱拆解與逐條核對

| # | 宣稱 | 測試與斷言(檔:行) | 配方(note) | 守住? |
|---|---|---|---|---|
| 1 | 死信定義=沒有嘗試紀錄、投遞次數用完的提案 | `inbox_store.py:63` 常數註解與 `test_dead_letter.py:45-52` `dead_letter()` 幫手：讀失敗 MAX_DELIVERIES 次後 `assert h.process().kind is Result.IDLE`、`disposition==("pending","dead_letter")` | `inbox_store.py` `if deliveries >= MAX_DELIVERIES:`→`if False:`，`test_a_dead_letter_is_never_handed_out_again` | 是 |
| 2 | 只能由管理指令逐筆重放 | `test_dead_letter.py:461-464` `test_only_the_replay_method_revives_a_dead_letter`：AST 掃全庫，含 `dead_letter_reason = NULL` 這句字串的函式只能是 `inbox_store.py::_replay` | `inbox_store.py` 放租約時順手加回 `disposition = NULL, dead_letter_reason = NULL`，`test_only_the_replay_method_revives_a_dead_letter` | 是 |
| 3 | 不會自己再交出去（執行迴圈不撿、不重讀 DSP） | `test_dead_letter.py:424-433` `test_a_dead_letter_is_never_handed_out_again`：`h.process().kind is Result.IDLE`、`len(h.dsp.reads)==reads_before`；`test_dead_letter.py:476-490` `test_no_periodic_work_or_resend_revives_a_dead_letter`：`process_one/process_awaiting/reconcile_all` 跑完後 `disposition==("pending","dead_letter")` 且信封/稽核不變 | 兩條：①上面同一條 note；② `inbox_store.py` 取件 SQL 加 `OR disposition = 'dead_letter'`，`test_no_periodic_work_or_resend_revives_a_dead_letter` | 是 |
| 4 | 重放只把它放回待處理（處置、死信原因、投遞次數、租約都清空/歸零） | `test_dead_letter.py:129-136` `test_a_replay_requeues_only_a_live_dead_letter`：`SELECT state, disposition, dead_letter_reason, deliveries, lease_owner FROM proposals` == `("pending", None, None, 0, None)` | `inbox_store.py` UPDATE 少清 `disposition` 那一句，`test_a_replay_requeues_only_a_live_dead_letter` | 是 |
| 5 | 之後照一般流程重跑每一關（列舉六項：廣告版本、政策版本、決策新鮮度、權限、比例與總曝險、核可） | `test_dead_letter.py:328-351` `GATES` 字典六項 parametrize `test_a_replayed_proposal_goes_through_every_gate`：逐一改變條件後 `(result.kind, result.block_code)` 對到對應碼、`h.dsp.writes==[]` | 六條分別打 `execution.py`(版本)、`capability_signer.py`(權限)、`execution.py`(比例)、`execution.py`(總曝險)，皆 `test_a_replayed_proposal_goes_through_every_gate` | 是 |
| 6 | 重放不是第二條略過關卡的通道（沒有 bypass 旗標） | `_replay()` 簽章只有 `task_id, revision, operator, clock`，無任何 skip 參數（讀碼確認）；行為面由 #5 六條配方共同佐證：拿掉任一關檢查都會翻紅，代表重放跑的是同一套檢查，不是繞過 | 同 #5 六條 | 是（間接：無獨立配方直接示範「加一個 skip 旗標」，但六個關卡各自被拆開驗證+讀碼確認函式無此參數，足以支撐） |
| 7 | 廣告版本變了就擋下給對應原因 | `test_dead_letter.py:340-351` gate `version_changed`：`(Result.BLOCKED, BlockCode.VERSION_CHANGED)` | `execution.py` `if view.version != proposal.campaign_version_observed:`→`if False:` | 是 |
| 8 | 政策版本不是現行版本就擋下給對應原因 | 同上 gate `policy_version_changed`；另 `test_f6_end_to_end.py:123-142` `change=="policy"` 端到端驗證 | `execution.py` policy 檢查→`if False:` | 是 |
| 9 | 決策建立超過 15 分鐘就擋下給對應原因（含邊界：剛好 15 分鐘不算過時） | `test_stale_decision.py:64-79` `test_a_stale_decision_is_blocked`：`(15min,EXECUTED)`、`(15min1s,BLOCKED, STALE)` | `guardrails.py` `DECISION_FRESHNESS - 1s`（把邊界說得更嚴），`test_a_stale_decision_is_blocked` | 是 |
| 9b | 有這一次真的需要、此刻有效的核可則不判新鮮度 | `test_stale_decision.py:139-149` `skips_only_the_freshness_check`（超過15分鐘仍 EXECUTED）；`:230-240` `unneeded_approval_does_not_exempt`（掛著用不到的核可仍 BLOCKED/STALE） | `execution.py` `if not needed: return "go" if RATIO in live else "block"` → 兩處分別改成永遠 `"go"`／永遠 `"block"` | 是 |
| 10 | 分析端另開接續任務重新規劃（限版本變/政策變/決策過時三種原因） | `test_f6_end_to_end.py:136-140`：三種 change 都 `facts["child"] == follow_up_id("t1")`；`test_dead_letter_wait.py:48-60` `test_a_new_block_reason_hands_the_task_over_to_a_follow_up`：`store.latest(child).state is TaskState.RECEIVED`、`follow_ups` 表寫入原因 | `flow.py` 拿掉 `"decision_stale": ReplanReason.DECISION_STALE` 映射，`test_a_new_block_reason_hands_the_task_over_to_a_follow_up` | 是 |
| 11 | 舊決策不會寫進 DSP | `test_f6_end_to_end.py:141` `assert STALE_BUDGET not in facts["writes"]` | 涵蓋在 #7#8#9 配方（擋下即不送） | 是 |
| 12 | 都沒變就照常寫成 | `test_f6_end_to_end.py:130-134`：`change=="unchanged"` 時 `writes==[STALE_BUDGET]`、`original.state is COMPLETED`、`child is None` | 若任一關檢查被關掉（#5系列）會讓其他組也誤判成"照常"，反向佐證此組必須靠六關全通過才行 | 是 |
| 13 | 已不是死信（已放回/已擋下）拒絕重放 | `test_dead_letter.py:129-136`（已放回後第二次 `NOT_DEAD_LETTER`）；`:435-441` `test_a_blocked_proposal_cannot_be_replayed`（`disposition==("pending","blocked")`→`replay(h) is ReplayOutcome.NOT_DEAD_LETTER`） | `inbox_store.py` `if row[0]!="pending" or row[1]!=DEAD_LETTER: return NOT_DEAD_LETTER`→`if False:` | 是 |
| 14 | 已過期拒絕重放 | `test_dead_letter.py:138-144` `test_a_replay_is_refused_after_the_proposal_expires`：`replay(h) is ReplayOutcome.EXPIRED`，`disposition` 不變 | `inbox_store.py` expired 檢查→`if False:` | 是 |
| 15 | 同任務已有更新修訂拒絕重放 | `test_dead_letter.py:152-158` `test_a_replay_is_refused_when_the_task_has_a_newer_revision`：`replay(h) is ReplayOutcome.SUPERSEDED` | `inbox_store.py` 新修訂檢查→`if False:` | 是 |
| 16 | 存的內容讀不回拒絕重放 | `test_dead_letter.py:409-421`：payload 損毀後 `replay(h) is ReplayOutcome.UNREADABLE`，`disposition` 不變，稽核只有 requested+refused | `inbox_store.py` `_parse_payload` 檢查→`if False:` | 是 |
| 17 | 收件口回死信而決策未過期時分析端等待 | `test_dead_letter_wait.py:22-31`：`live is TaskState.HANDED_OFF`、任務 seq 不變、無 follow-up | `flow.py` `if answer.state=="dead_letter" and now<expires_at:`→`if False:` | 是 |
| 18 | 已過期就結案、不重新規劃 | `test_dead_letter_wait.py:34-41`：`closed is TaskState.BLOCKED`，`follow_up_to("t1") is None` | `flow.py` 拿掉過期時間比較，變成一律結案（同上那條或另一條「一直等」配方，兩個方向都各有配方覆蓋） | 是 |
| 19 | 每次進死信都寫進死信信封 | `test_dead_letter.py:74-86` `test_a_dead_letter_leaves_a_durable_envelope`：`envelopes(h)` 恰好一列且欄位齊 | `inbox_store.py` `_record_dead_letter(...)`→`pass` | 是 |
| 20 | 每次要求重放與其結果都寫進稽核 | `test_dead_letter.py:207-220` `test_every_dead_letter_operation_is_audited`：四筆稽核（dead_lettered/requested/requeued/requested/refused）逐列比對 | `inbox_store.py` 重放放回那一句稽核寫入→`pass` | 是 |
| 21 | 同一份提案重放後再進死信另寫一列，不去重 | `test_dead_letter.py:105-117` `test_a_replayed_proposal_that_dead_letters_again_gets_a_second_envelope`：`[row[0] for row in envelopes(h)] == [1, 2]` | 涵蓋於 #19 配方（拿掉寫入信封就不會有第二列） | 是 |
| 22 | 死信信封與稽核只增不改 | `test_dead_letter.py:466-474` `test_the_dead_letter_tables_are_only_ever_inserted_into`：regex 掃全庫 `UPDATE/DELETE/REPLACE/DROP/ALTER ... dead_letters/dead_letter_ops`，`offenders==[]` | `inbox_store.py` 重放時多寫一句 `UPDATE dead_letters SET deliveries=0 ...`，`test_the_dead_letter_tables_are_only_ever_inserted_into` | 是 |
| 23 | 死信信封與稽核不被保留期清理 | `test_dead_letter.py:74-86`：`RETENTION` 過後收件表清掉，`len(envelopes(h))==1`（信封還在） | `inbox_store.py` 保留期清理語句後加 `DELETE FROM dead_letters`，`test_a_dead_letter_leaves_a_durable_envelope` | 是 |

## 措辭 vs 程式行為
- 讀 `inbox_store.py:63`、`:648-652`、`:735-771` 與 `guardrails.py` 的 `DECISION_FRESHNESS`：宣稱裡「還沒開始嘗試、投遞次數用完」「只把它放回待處理」「證據新鮮度以決策建立時間代理」「剛好 15 分鐘」的邊界，都和程式常數/註解逐字對得上，沒有說得比實際寬。
- 「沒有略過任何一關的旗標」是唯一一句沒有專屬機械配方直接示範「加一個 skip 旗標會怎樣」的宣稱；但 `_replay()` 簽章讀下來本身沒有任何旗標參數，且六個關卡各自獨立被 `test_a_replayed_proposal_goes_through_every_gate` 打過，行為上等於已經證明「重放跑的是同一條路徑、沒有分支」。判定仍算守住，但這句話比其他句更依賴「讀碼＋間接佐證」而非單一斷言，值得留意。
- 「分析端另開接續任務重新規劃」在原文只綁在版本/政策/過時三種原因，測試（`EXPECTED_BLOCK` 只有這三種、`test_a_new_block_reason_hands_the_task_over_to_a_follow_up` 只 parametrize 這兩種+e2e 補第三種）範圍一致，沒有把權限/比例/總曝險也算進去，措辭沒有過滿。

## 次要建議
1. 可以幫「沒有略過任何一關的旗標」補一條專屬 kill 配方（例如在 `_replay` 或 `process_one` 插入一個假想的 `skip_gates` 分支硬編碼成 `False`），讓這句話也有直接翻紅的機械證據，而不只靠讀碼佐證，會更紮實。
2. 表格裡 #6、#12、#18、#21 目前靠其他條目的配方間接覆蓋，不是各自獨有配方；不影響本次判定，但下次再加子句時建議逐句核對是否已有專屬配方，避免日後拆得更細時漏測。

## 摘要
把「事故 F6」那一行拆成 23 條具體宣稱，一條條回頭讀測試斷言和 kill 配方，25 個配方在隔離沙盒裡跑全部翻紅，措辭也跟程式常數、註解和邊界值逐字對得上，沒有說得比實際寬；唯一比較弱的一句（沒有略過任何一關的旗標）只是缺一條專屬配方、靠讀碼佐證頂著，不影響整體判定：同意這條合約每句宣稱都有測試真的守著。
