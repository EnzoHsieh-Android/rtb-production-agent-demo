判定:不同意

理由:

**已核對範圍**
- 讀了 `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md` 裡 F7 的 KEY 行、WHY 行、kill_recipes;`Projects/RTB_Phase6權限護欄與總曝險_計劃.md` 增量 1、3 全文(S330–S343、S350–S379);`tests/executor/test_aggregate_limit.py`、`test_f7_end_to_end.py`、`test_approval.py` 全文;交接文件第 16 節原始 F7 描述。
- 綁定行列出的 12 支測試全部存在(不是空引用),`pytest tests/executor/test_aggregate_limit.py tests/executor/test_f7_end_to_end.py tests/executor/test_approval.py` 91 條全綠;複驗了官方列出的 5 條 kill recipe 之一(`attempt_store.py` 的 `unresolved_count(tx)` 改 `False`),確實會讓 `test_an_unresolved_reservation_counts_no_matter_how_old` 全部翻紅,官方卷證屬實。
- 已用臨時目錄(`/tmp/rtb-audit`,複製自本 repo,git 唯讀)做改壞實驗,不影響原 repo。

**(1) 有宣稱缺綁定測試守著:「分批」的正面半句**

合約文字:「『分批』只做到額度釋放後新任務能再通過,沒有自動排程。」WHY 行把這句寫成已核實的措辭修正,[S330]/[S332]/[S333] 也各自列在「主線寫設計」段落裡。但實際檢查發現:
- `test_a_verified_reservation_leaves_the_window_24_hours_after_verification`(S333)與 `test_an_unresolved_reservation_counts_no_matter_how_old`(S332)都只直接呼叫 `attempt_store.used()` 這個底層函式,不經過 `h.submit()` + `h.process()` 的完整執行路徑。
- 我在 `test_aggregate_limit.py`、`test_approval.py`、`test_f7_end_to_end.py` 三支檔案裡找不到任何一支測試真的做到:「額度因窗口到期或未結案轉失敗而釋放 → 一份全新提交的任務(不是原本被擋的那份)因此通過」這個完整流程。
- `test_an_unneeded_aggregate_approval_does_not_shorten_the_capability` 最接近,但它是「已核可待放行的同一份提案」在額度釋放後仍能放行,不是「額度釋放後新任務能再通過」。

也就是說,這句話目前只能由兩支各自獨立的底層單元測試(S332、S333)「組合推論」出來,不是被一支端到端測試直接證明——這正是本專案自己在 F1/F2 WHY 行裡會主動揭露的那種缺口(例如「這五支測試只是重申斷言…不是三支端到端測試單獨證明的」),但 F7 的 WHY 行沒有揭露這一點。

**建議補的測試**:在 `test_aggregate_limit.py` 加一支端到端測試——門檻剛好被一筆提案占滿 → 用撥時鐘超過 24 小時窗口,或把占額度的未結案嘗試 `resolve` 成失敗,真的釋放額度 → 再 `h.submit()` 一份全新 task_id 的提案 → `h.process()` 斷言 `Result.EXECUTED`。這才是「新任務」而非「同一份提案重跑」的直接證據。

**(2) 有綁定測試驗不到它被綁來證明的東西:`test_an_approval_that_expires_mid_flight_lets_nothing_through`**

`execution.py` 的 `_take()` 裡有兩層核可有效性檢查:
- 外層 `_gate`/`_approvals` 用 `approval.holds()`(内含 `now.timestamp() < approval.expires_at`),這個邊界有精確測試守著(`test_an_approval_is_void_when_scope_expiry_hash_stage_or_policy_changes` 的 `_expired` 分支:`expires_in=60` 後正好撥 60 秒,让 now 精確等於到期時間,斷言仍不算數)。
- 內層(專門對付「查好核可到開始一筆之間過期」這個競態)在 `_take()` 裡另外重判一次:
  ```python
  live = {stage: found for stage, found in held.items()
          if now.timestamp() < found.expires_at}
  ```
  這一行就是 [S359]、綁定測試 `test_an_approval_that_expires_mid_flight_lets_nothing_through` 名義上要守的東西。

我把這一行的 `<` 改成 `<=`(核可剛好在到期那一秒仍被當成有效)後,在臨時目錄跑了**整個測試套件(1462 支)**,全部通過,包括這支被點名要守它的測試——因為該測試用的手法是「核可到期後,又額外撥 11 秒(`expires_in=10` 之後 `advance(seconds=11)`)」,只驗到「明顯過期」的情況,沒有驗到「剛好等於到期那一刻」這個真正的邊界。這跟同一份計劃裡「剛好滿 24 小時還不算超過」那種精確邊界測試的寫法形成對比——本專案明明有能力寫出邊界測試(如 `test_a_verified_reservation_leaves_the_window_24_hours_after_verification`、`_expired` 分支本身),但這條內層競態檢查漏了。

**建議補的測試**:仿照 `_expired` 的手法,在 `test_an_approval_that_expires_mid_flight_lets_nothing_through` 或新增一支測試裡,讓核可的 `expires_at` 精確等於 `_take()` 交易裡讀到的 `now`(而不是提前很久就過期),斷言仍判定為不算數、回待核可。

**(3) 改壞方式綁定測試接不住,已用實驗證實**

除了(2)的 `<=` 邊界改壞不會翻紅之外,我也做了對照組確認其餘防線是紮實的(供覆核):
- 整段刪掉「比例核可在取件後過期」的重判(`RATIO in held and RATIO not in live` 那段)→ `test_an_approval_that_expires_mid_flight_lets_nothing_through[budget_increase_too_large]` 立刻翻紅。
- 拿掉「查好核可之後又有人補簽更新核可」的重判(`_superseded` 那段)→ `test_an_approval_signed_after_the_lookup_is_honoured_before_writing` 立刻翻紅。
- `_audit()` 裡把「總曝險核可只在真的超過門檻時才記使用」的條件拿掉 → `test_a_capability_shortened_by_an_unused_approval_recovers_with_a_full_lifetime` 翻紅(以例外形式,但確實變紅)。

這三個對照都被接住,說明整體防線設計是紮實的,只有(2)那個特定的邊界是漏洞。

**(4) 措辭核對**

- 整體上措辭誠實、甚至比原交接文件保守:交接文件寫「到門檻停止、分批或轉人工」,轉正後改成精確的「先停在待核可,由人簽核可放行…沒有核可、提案到期就擋下結案」,並老實承認「分批」沒有自動排程,沒有誇大。
- 唯一容易讓人誤讀的一句是「一到門檻,新的加預算就停下」——字面容易讀成「達到門檻那一刻就擋」,但程式與綁定測試(`test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it` 的 `cap=100` 分支)證明的其實是「剛好等於門檻仍放行,超過才擋」。這不是程式錯,是文字比行為說得更滿一點點。建議改成「超過門檻,新的加預算就停下(剛好等於門檻仍放行)」,和綁定測試斷言完全對齊,不留解讀空間。

**結論**:F7 這條合約的機制本身(預留、窗口、核可、並行、到期)實作紮實、測試涵蓋面廣,12 支綁定測試都是真跑且會抓真問題(不是裝飾);但(1)「分批」正面半句缺一支真正的端到端測試,目前是組合推論;(2)「核可到期就擋下」在競態重判那一層有一個精確邊界沒有測試覆蓋,已用改壞實驗證實整套 1462 支測試都接不住;(4)有一句措辭可以更精確。建議補上(1)(2)的測試、調整(4)的措辭後再轉正/簽核。
