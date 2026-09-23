severity: clean

站攻擊者角度,针对這輪(r2→r3)實際改動的三處收斂檢查:(1) 過時決策能不能靠無效/用不到/被取代/剛過期的核可寫進 DSP,現在多了「總曝險需不需要核可改在開始一筆的交易裡重算」這一步,新的判斷點會不會反而開了新洞;(2) 這次新增的重算與核可讀取,握著寫入鎖時的成本有沒有變成全表掃描;(3) 讀不回提案的死信拒絕重放,能不能被拿來卡住別的提案或當成灌爆手段。

## 總曝險需不需要核可,改在開始一筆的交易裡重算——會不會反而放行過時決策

新版把「這一次要不要一張總曝險核可」從開始一筆之前的預判,改成開始一筆的交易裡、握著寫入鎖用當下已用額度重算:

引句:「needed = amount > 0 and (
            _aggregate_used(tx, tenant.name, now) + amount > tenant.aggregate_limit)」

（`_approved_while_stale`,`src/rtb/executor/execution.py:193-194`）。這個公式跟 `attempt_store.begin()` 裡真正決定要不要放行超額的公式逐字一致:

引句:「used = aggregate_used(tx, reservation.tenant, now)
        if used + reservation.amount > reservation.limit:」

（`attempt_store.py:346-348`）。兩處在同一個 `with self.store.transaction()` 底下、同一把 SQLite IMMEDIATE 寫入鎖裡跑,中間沒有任何會改變已用額度的寫入穿插進來,所以 `_approved_while_stale` 判「需要」和 `begin()` 判「超額」永遠算出同一個結果:需要卻沒讀到算數的核可,在 `_approved_while_stale` 就已經擋成 `DECISION_STALE`,不會走到 `begin()`;不需要,`begin()` 也不會拋 `AggregateLimitReached`。追了三條會讓「過時決策 + 核可」出錯的路,都沒有新繞法:

- 掛著一張**用不到**的核可(這次重算不需要總曝險核可、只有比例那一關需要且有效):`_approved_while_stale` 在 `needed=False` 時回的是 `RATIO in live`,不是「隨便一關有核可就放行」,對照新增測試 `test_an_aggregate_approval_freed_up_before_the_write_does_not_exempt_a_stale_decision`——預判要用、進交易時額度被別人釋放,這張沒用上,過時的決策照樣擋(斷言 `h.dsp.writes == []`)。
- 剛好**過期**的核可(查好之後、開始一筆之前過期):`_stop_before_begin` 先判 `_superseded`(核可被取代,放掉重來),`live` 是進了開始一筆交易之後用當下時間重判到期算出來的,不是查好時那個舊時間;`RATIO in held and RATIO not in live` 那一支會先把「查好時有、現在沒了」的比例核可攔下來,新增測試 `test_a_stale_decision_whose_ratio_approval_lapses_before_the_write_is_blocked` 驗到「過時 + 比例核可在開始一筆前過期、沒有新的」直接擋,不停進待核可等新核可。
- **被取代**的核可(查好之後又有人簽了新的一張):`_superseded` 檢查排在 `_stop_before_begin` 之前,被取代就 `DEFERRED` 回滾,不會帶著舊核可走到過時判斷,更不會走到 `begin()`;新增測試 `test_a_replaced_approval_is_reread_before_the_freshness_check` 驗到「先放掉重來、下一輪才用上新核可」,不是「先過時判斷、再管有沒有被取代」。

反過來的方向(該放行卻被錯擋)也查過:預判用不到、實際在開始一筆時才需要總曝險核可,`_approved_while_stale` 的 `needed=True` 分支會即時讀 `latest_approval(tx, proposal, AGGREGATE)` 再驗一次,對照測試 `test_an_aggregate_approval_needed_only_inside_the_write_still_counts`——有效核可照樣算數、寫進 DSP、`approval_uses` 也記到那一關,不會因為預判沒抓到就白白被擋。

`_approved_while_stale` 找到的那張核可會寫回呼叫端傳進來的 `live` 字典(`live[AGGREGATE] = found`),`_take` 裡緊接著用同一個 `live` 算 `reservation.approved` 與之後的 `_audit`,兩處讀的是同一次查詢的結果,沒有查一次判一次、用另一次算稽核的落差。

## 握寫入鎖期間的重算有沒有變成全表掃描

新增的兩個查詢——`_aggregate_used`(即 `attempt_store.aggregate_used`/`aggregate_holdings`)與 `latest_approval`——原本就是既有函式,這輪只是多在開始一筆的交易裡各呼叫一次,沒有新開查詢。`aggregate_holdings` 的兩段 SQL 分別靠 `attempts_verified_by_time`(`written_at`)與 `attempts_first_rows_by_tenant`/`attempts_first_rows`(`campaign_id` 部分索引,配合 `seq=1`)過濾,不是撈全表(`attempt_store.py:53-58,73`);`latest_approval` 靠 `approvals_by_proposal`(`task_id, revision, content_hash, stage`)這個複合索引,`ORDER BY seq DESC LIMIT 1` 也吃得到索引(`inbox_store.py:183-184`)。而且這兩次重算只在 `decision_stale(proposal, now)` 成立時才跑(`_stop_before_begin` 先算 `stale`,`_approved_while_stale` 只在 `stale` 為真時才被呼叫),不是每一筆開始一筆都多算一次,正常(非過時)路徑的鎖內成本跟這輪之前一樣。沒有找到握鎖期間成本被這輪改動推高到全表掃描的路。

## 讀不回提案的死信拒絕重放,能不能卡住別的提案

新加的 `UNREADABLE` 分支:

引句:「if _parse_payload(row[3]) is None:  # 代碼審第 2 輪外家席:原本照樣放回、稽核記成功
            return ReplayOutcome.UNREADABLE」

只影響 `_replay_refusal` 這一次查的那一列(`WHERE task_id = ? AND revision = ?`,`inbox_store.py:256-259`),判定拒絕之後既不 UPDATE 也不動別的列,跟其他任務、其他修訂的收件表列、待處理名額、總曝險額度都不相交。而 `payload` 這欄是 `submit()` 當下用提案物件自己序列化寫進去的(`json.dumps(proposal.to_primitives(), ...)`,`_payload`,`inbox_store.py:1240`),來源是剛通過網域層解析的提案,不是攻擊者可以直接塞進去的原始字串;順著提案內容(任何欄位值)這條路走不到「序列化完寫進資料庫的 JSON 讀不回來」——會讀不回來的只有直接改資料庫這種不在威脅模型內的手段(現有測試就是直接 `UPDATE proposals SET payload = '{'` 模擬,不是任何提案欄位能觸發的)。所以這個拒絕分支目前只擋自己這一份死信的重放,擋不到、也占不走其他提案的名額或鎖。

## 觀察(非阻擋)

- `decision_stale` 的新鮮度判準用的是提案裡的 `decision_created_at`,執行端沒有獨立的蓋章時間;威脅模型明講被劫持的分析行程送來的提案內容算攻擊者可控,理論上攻擊者可以填一個很新的 `decision_created_at` 讓新鮮度永遠判「不過時」,連帶讓這輪新加的 `_approved_while_stale`/`_block_stale` 整條路徑都沒有機會觸發。這不是這輪(r2→r3)改的東西,是 Phase 8 用「決策建立時間」當新鮮度依據的既有性質,r1/r2 的資安席已經記過同一點,這輪的改動沒有讓它變得更好或更差,僅重申供之後設計覆核參考。
- `_approved_while_stale` 在 `needed=True` 時會在同一個已握鎖的交易裡再呼叫一次 `_aggregate_used`,`begin()` 內部（`reservation.approved=True` 時)通常不會再因超額拋例外,但 `begin()` 自己還是會照原邏輯呼叫一次 `aggregate_used` 判斷 `over_limit`(即使不拋);等於過時且需要總曝險核可的這一種情況,鎖內同一個查詢跑了兩次。兩次都是索引查詢、成本一樣有界,不是全表掃描,只是可以省一次;不影響安全屬性,列出來供效能覆核參考,不算阻擋項。

看過的檔:
`/Users/enzo/rtb-3b/src/rtb/executor/execution.py`
`/Users/enzo/rtb-3b/src/rtb/executor/inbox_store.py`
`/Users/enzo/rtb-3b/src/rtb/executor/attempt_store.py`
`/Users/enzo/rtb-3b/src/rtb/executor/replay.py`
`/Users/enzo/rtb-3b/src/rtb/executor/guardrails.py`
`/Users/enzo/rtb-3b/src/rtb/executor/approval.py`
`/Users/enzo/rtb-3b/tests/executor/test_dead_letter.py`
`/Users/enzo/rtb-3b/tests/executor/test_stale_decision.py`
`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase8死信重放與過時決策_計劃.md`
`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md`
`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`
（另讀取 r1/r2 代碼審報告與 r2-snapshot.patch 以確認這輪與上輪的差異範圍,未計入本輪改動:`/Users/enzo/rtb-3b/governance/review-reports/code-phase8/r2-security.md`、`r2-snapshot.patch`）
