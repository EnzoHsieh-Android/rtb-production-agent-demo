severity: minor

驗證方法:把 `/Users/enzo/rtb-3b`(phase8 分支尖端 35fe807)複製到 `/tmp/p8rev/repo`,在複本裡逐條拿掉防線(改 `src/rtb/...`,不動 `/Users/enzo/rtb-3b` 本身),跑對應測試檔,確認翻紅;跑完立刻用備份還原該檔,逐條互不污染。所有指令與輸出見上方過程,這裡只記結論與唯一的假綠發現。

## 逐項結論

### 1. 新測試對症狀翻紅、無假綠

逐一拿掉防線並重跑,全部翻紅(命令與輸出已在審查過程中留存):

- `precheck` 裡 `policy_version != POLICY_VERSION` → `test_a_proposal_from_another_policy_version_is_blocked`、`test_a_rerun_that_hits_a_new_reason_keeps_the_reason[policy-...]`、F6 `[policy]` 組翻紅。
- `_run` 裡第一次 `_stale()` 呼叫(排在簽發後)→ 只有 `test_an_approval_for_another_proposal_does_not_exempt_a_stale_one` 與 `test_a_stale_proposal_over_the_ratio_is_blocked_rather_than_left_waiting` 翻紅,其餘靠 `_too_late`(開始一筆交易裡的第二道)頂住不受影響——這正是設計裡「兩處重複」的用意,兩道防線各自有專屬測試盯,不是重複到沒用。
- `_too_late` 裡的 `_stale_in` 分支(開始一筆交易裡的判斷)→ 專門盯它的 `test_a_decision_that_goes_stale_after_the_precheck_is_blocked_before_the_write` 翻紅,其餘不受影響(第一道頂住)。
- `_in_flight_again` 裡的 `_stale_on_rerun` 呼叫(重跑轉嘗試中那一次判斷)→ `test_a_rerun_that_hits_a_new_reason_keeps_the_reason[stale-...]`、`test_a_reconcile_rerun_that_finds_a_stale_decision_voids_then_keeps_the_reason`、`test_a_resend_that_goes_stale_while_resigning_is_not_sent` 翻紅。
- `_stale_in` 裡「有效核可就不判新鮮度」那段 → `test_an_approved_proposal_skips_only_the_freshness_check`、`test_an_approved_resend_is_not_blocked_as_stale` 翻紅([S512] 兩個方向都有專屬測試釘住)。
- `analyzer/flow.py` 裡 `dead_letter and now < proposal.decision_expires_at` 的等待分支 → `test_the_analyzer_waits_...`、F6 全部四組翻紅(拿掉等待分支後死信立刻被當成過期結案,分析端在死信還能重放時就結案,F6 因而全組失敗)。
- `_REPLAN_ON_BLOCK` 少了 policy/stale 兩個鍵 → `test_a_new_block_reason_hands_the_task_over_to_a_follow_up` 兩個參數化案例與 F6 `[policy]`、`[stale]` 翻紅。
- `inbox_store.py` 的 `NOT_DEAD_LETTER`/`EXPIRED`/`SUPERSEDED`/`INBOX_FULL` 四個拒絕分支,以及 `_record_dead_letter` 只在 `_finish` 真的寫成時才呼叫(同交易保證)→ 各自對應測試逐一翻紅,`test_a_dead_letter_writes_its_envelope_in_the_same_transaction` 專門盯「先讓交易失敗、信封與稽核都不該寫進去」這一點,拿掉同交易保護後六個測試一起翻紅。

沒有發現「斷言測到別的東西」或「用時鐘推過租約導致測到租約失效而非決策過時」這種假綠:F6「決策變舊」只推**執行端**的時鐘(`clock` 參數只餵給 `_execute`/`Executor`,DSP 與收件口用真實時間),對照 `VISIBILITY_TIMEOUT`(60 秒)與推的 16 分鐘,租約續租、取件都在同一個推過的時鐘座標系裡自洽,不會因為時鐘推移而先撞到租約到期;上面刻意拿掉 `_stale`/`_stale_in`/`_stale_on_rerun` 後,F6 的 `[stale]` 組確實會改判成 `EXECUTED`/寫入 DSP(不是變成別的失敗代碼),證明斷言測到的正是決策新鮮度而不是租約。

### 2. 改到既有測試——一處放寬未被察覺的覆蓋率退化(找到假綠,但範圍小)

逐條核對:

- `tests/executor/fakes.py` 的 `proposal()` 預設 `policy_version` 改成現行版本:必要改動(Phase 8 起執行前檢查會擋非現行版本的提案,樣本預設的 `"v1"` 會讓所有沿用這個工廠函式的既有測試在走到執行迴圈時全部先被新規則擋下)。跑過全套 1529 個測試確認沒有任何測試因為這個預設值改變而"意外通過"或"意外跳過"該驗的東西——這一處是乾淨的必要改動。
- `tests/executor/test_replan.py` 拿掉 `("dead_letter", None)` 那組參數:必要改動且不是放寬——原本那組驗的是「死信結案、不重新規劃」,現在死信改成「未過期先等、過期才結案」,原本的斷言在新規則下不成立;拿掉的東西被 `tests/analyzer/test_dead_letter_wait.py` 兩支新測試接住,而且新測試把「等待」與「過期結案」拆成兩個獨立場景各自驗證,覆蓋範圍比原來更細,不是變鬆。
- `test_inbox_disposition.py` 的擋下原因清單補兩個值:機械性配合列舉擴充,原本驗的「清單封閉」仍然驗到(兩邊都要相等)。
- `test_execution.py`/`test_guardrails.py` 的新增觸發項與三份清單分類:新增而非取代,原有斷言結構未改。
- `test_approval.py` 的最終對照數從 1 改成 2:核對過改動邏輯,這是把「换回原租戶」與「政策也换回」兩件事在同一步驗完,語意上是加強不是放寬(換回租戶時第一份提案的政策也已經換回、兩份一起放回待處理,2 是正確的合計數,不是放寬容忍度)。

**但 `test_an_approval_is_void_across_policy_or_tenant_changes` 這一支改寫後,原本要驗的一行 `holds()` 裡的 `proposal.policy_version == POLICY_VERSION` 顯式檢查,已經沒有任何測試能單獨盯住它——這是一處退化,只是退化到「冗餘檢查」而非「行為漏洞」。**

## F1 test_an_approval_is_void_across_policy_or_tenant_changes 改寫後的覆蓋率退化

引句:「Phase 8 起政策版本不是現行版本的提案在執行前檢查就擋下、進不了待核可;改成照實際會發生的順序造:用現行版本停在待核可、簽了核可,之後政策換版」

severity: minor
blocking: 否 — 目前程式行為沒有錯(`holds()` 裡的檢查仍存在、仍生效,只是變成永遠冗餘於範圍指紋),不是「會做出錯的行為」,是覆蓋率退化。

舊版測試用 `h.submit(..., policy_version="v1")`(提案自己的政策版本欄位固定寫錯,同時全域 `POLICY_VERSION` 常數不變)來讓 `approval.holds()` 裡 `proposal.policy_version == POLICY_VERSION` 這一行單獨失敗,而 `scope_fingerprint(tenant)`(見 `src/rtb/executor/approval.py:55-65`,材料裡含 `"policy_version": POLICY_VERSION`)因為全域常數沒變而維持一致,所以舊測試能單獨盯住那一行顯式比較。Phase 8 讓執行前檢查先擋下非現行版本的提案,舊寫法造不出「停進待核可」的前提,只能改用 `monkeypatch.setattr(approval, "POLICY_VERSION", "demo-pacing-v2")`——但這個做法同時改變了 `scope_fingerprint()` 用的全域常數,核可核發時算出的指紋與重新驗證時算出的指紋因此不同,`fingerprint` 比對那一行本身就已經讓 `holds()` 回 False。

實測:在複本裡把 `src/rtb/executor/approval.py` 的 `and proposal.policy_version == POLICY_VERSION` 這一行整行拿掉(只留 fingerprint 比對與到期、金額判斷),跑全套測試(`.venv/bin/python -m pytest -p no:cacheprovider -q tests/`)——1529 個全部通過,包含這支改寫後的 `test_an_approval_is_void_across_policy_or_tenant_changes`。也就是說,這支測試名義上驗「核可跨政策/租戶變更失效」,但它現在驗到的其實只是 fingerprint 那一行,`holds()` 裡那個顯式的 `policy_version` 比較行,在改寫前後都對不上任何獨立測試——改寫前它被同一支測試單獨盯住,改寫後它悄悄變成「死碼式冗餘」,整個倉庫沒有任何測試會在拿掉它之後翻紅。

這不是本次 diff 新引入的行為缺陷(這行 `holds()` 檢查是 Phase 6 增量 3 的舊碼,`git log -- src/rtb/executor/approval.py` 顯示這行在 `96a7d93`(增量 3)就已存在),但屬於任務指定要查的「改到既有測試」項目之一,而且改寫確實讓覆蓋率退化(舊測試能單獨殺死這一行的變異,新測試不能),值得記錄:之後如果有人想把 `holds()` 的 fingerprint 材料改成不含 `policy_version`(例如覺得跟顯式欄位重複而精簡掉),不會有任何測試擋下這個變化。

## 3. 事故 F6 端到端四組

四組(`unchanged`/`version`/`policy`/`stale`)各自對應獨立的變更手段(`_other_writer_changes_the_budget`——沿用 F4 端到端同一支已驗證過的輔助函式改預算版本;`monkeypatch.setattr("rtb.executor.execution.POLICY_VERSION", ...)`;把只餵給執行端的 `clock` 往後推 16 分鐘),`_facts()` 分別核對 `original.state`、`error_detail` 裡的 `blocked=`/`replan=` 代碼、`child` 接續任務、DSP 上的實際寫入內容(`writes`)。已用拿掉對應防線的方式逐一翻紅驗證(見第 1 節),四組各自真的走到設計要測的那條路,「決策變舊」只推執行端時鐘沒有讓測試走捷徑(拿掉決策新鮮度判斷後,`[stale]` 組會變成寄出並寫入 DSP,而不是變成別的假性失敗)。

## 4. [S500]–[S512] 逐條核對

逐條核對計劃筆記(`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase8死信重放與過時決策_計劃.md` 第 93–105 行)列出的 `[test:...]` 名稱,均能在 `tests/executor/test_dead_letter.py`、`tests/executor/test_stale_decision.py`、`tests/analyzer/test_dead_letter_wait.py`、`tests/analyzer/test_f6_end_to_end.py` 裡逐字找到對應函式名,且測試檔內用 `# ---- [S5xx] ----` 標記緊鄰對應測試,標記與內容一致,沒有標錯號或張冠李戴的情況;S500(同交易信封)、S501(封閉分類)、S502(重放拒絕四原因)、S503(重放照全部關卡)、S504/S505(政策/新鮮度)、S506(信封與嘗試鍵接起來)、S507(等待/重新規劃)、S508(稽核只增不改)、S509(F6 端到端)、S510(舊庫遷移)、S511(重跑保留新原因)、S512(有效核可只免新鮮度一項)全數逐條用拿掉防線的方式驗證過,均翻紅。

## 摘要

新測試對症狀翻紅、沒有找到假綠(F6 的時鐘推移是乾淨的,只影響執行端、不會誤測成租約失效);既有測試改動裡有一處放寬覆蓋率但不影響現在的正確性——`test_an_approval_is_void_across_policy_or_tenant_changes` 改寫後,`approval.holds()` 裡 `proposal.policy_version == POLICY_VERSION` 這行顯式檢查失去了能單獨盯住它的測試(拿掉整行,全套 1529 個測試仍全過),原因是改寫改用的 monkeypatch 手法同時連動了 scope fingerprint,兩個判斷條件從此绑在一起,不是本次 Phase 8 新引入的邏輯缺陷,但值得記錄,列為 minor、不 blocking。
