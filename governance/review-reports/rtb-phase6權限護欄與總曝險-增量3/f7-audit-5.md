判定:同意

## 驗證方法
沒有採信圖譜或計劃文件的既有結論,把 F7 合約行逐句拆成宣稱清單,對照 `執行迴圈.md` 的 `KEY:★INVARIANT★ 事故 F7` 行、綁定的 15 支測試、以及同一篇 `kill_recipes` 裡歸在這個不變量下的 15 條殺傷力配方,並把 repo 複製到 `/tmp/rtb_audit/repo` 做實驗(未動原始 repo 任何檔案):
- 對 `src/rtb/executor/approval.py` 的 `holds()` 逐一停用其 8 個條件(task_id、revision、content_hash、stage、fingerprint 比對、policy_version、到期、金額上限),用 `tests/executor/test_approval.py` + `test_aggregate_limit.py` 全跑,8 個全部翻紅。
- 對總曝險門檻的邊界比較(`>` vs `>=`)與 24 小時窗口邊界(`>=` vs `>`)分別改壞,`test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it`、`test_a_verified_reservation_leaves_the_window_24_hours_after_verification` 等測試均翻紅。
- 把圖譜登記在「★INVARIANT★ 事故 F7」下的全部 15 條 `kill_recipes`(attempt_store.py 2 條、execution.py 3 條、approval.py 10 條)逐條實際套用並跑對應測試檔,15 條全部依登記的測試名稱翻紅(其中一條原本用錯測試檔跑成漏抓,換到 `test_f7_end_to_end.py` 後確認翻紅,不是配方本身有問題)。

## 逐項核對結果

1. **「租戶 24 小時內加預算的總曝險(已驗證算 24 小時窗口內、還沒結案的一直算)」** — 由 `test_an_unresolved_reservation_counts_no_matter_how_old`(S332)與 `test_a_verified_reservation_leaves_the_window_24_hours_after_verification`(S333)精確驗到秒級邊界(`AGGREGATE_WINDOW` 剛好滿 24 小時仍算、多 1 秒才出窗)。窗口比較符號改壞(`>=`→`>`)測試立即翻紅。

2. **「這一筆加上去會超過門檻時(剛好等於門檻仍放行)」** — `test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it` 用 `cap=99`(擋)/`cap=100`(剛好打平、放行)兩組參數精準對應措辭裡的括號說明;`attempt_store.py` 的 `used + amount > limit` 邊界符號改成 `>=` 立刻翻紅。

3. **「先停在待核可,由人簽核可放行」** — `test_an_approvable_block_waits_for_approval`(S350)、`test_a_valid_approval_lets_the_proposal_through_and_still_reserves`(S353)驗到位;把 execution.py 裡「待核可放回前要核對核可有效」的守衛拆掉(`elif tenant is not None and self._approved(...)` → `elif tenant is not None:`),測試立即翻紅。

4. **「核可綁定提案內容雜湊、關卡、金額上限、到期」** — `test_an_approval_is_void_when_scope_expiry_hash_or_stage_changes` 的 9 組參數化(錯金鑰、換修訂、換內容、換關卡、範圍改變、過期、金額超過、偽造 task_id、偽造 revision)對應 `holds()` 的每一個欄位比對;逐一停用這些比對(改成常真)全部翻紅,包含未在措辭裡明寫但確實獨立守著的 task_id/revision 欄位(次要,見下)。

5. **「範圍指紋:租戶名稱與設定、比例常數」** — `test_an_approval_is_void_once_any_scope_field_changes` 的 7 組參數(tenant_name、campaigns、max_budget、aggregate_limit、ratio 分子/分母/最小加額)逐一對應範圍指紋雜湊材料裡的 7 樣;圖譜裡的 7 條殺傷力配方逐條實測全部翻紅,證實「七樣材料每樣都測到」這句 WHY 說明目前為真,不是空話。

6. **「提案的政策版本也要等於目前版本」** — `test_an_approval_is_void_across_policy_or_tenant_changes` 直接驗到「提案政策版本不是目前版本」與「廣告改掛到設定一樣的另一個租戶」兩種情境;停用 `proposal.policy_version == POLICY_VERSION` 直接比對,以及從指紋材料拿掉 `tenant.name`,兩者都翻紅,精確對應 WHY 段落裡「政策版本是直接比對、不是指紋材料」的措辭區分。

7. **「沒有核可、提案到期就擋下結案」** — `test_an_unapproved_proposal_expires_into_blocked` 對 RATIO 與 AGGREGATE 兩關都參數化驗到。

8. **「多個工作者同時處理也不會超過門檻」** — 由 `test_two_workers_cannot_race_past_the_aggregate_limit`(S336,兩個工作者用柵欄逼出真並行競態)與 `test_f7_many_small_increases_stop_at_the_aggregate_limit`(8 執行緒、3000 筆)兩層驗證;端到端測試直接斷言 `(NEW_BUDGET-BUDGET)*len(h.dsp.writes) <= AGGREGATE_LIMIT`,是對總花費的直接證明,不是間接推論;拿掉 `begin()` 的門檻比較,端到端測試也翻紅(3000 筆全數寫入 vs 預期 1234 筆)。

9. **「「分批」只做到額度釋放後新任務能再通過,沒有自動排程」** — `test_a_new_task_passes_once_the_budget_is_released` 測到「已驗證出窗」與「未結案判失敗」兩種釋放路徑後全新任務能過;沒有自動排程這件事本質上是「沒有東西存在」,無法用正面測試證明,但措辭已誠實地把範圍限縮成「只證明額度釋放後新任務能過」,沒有多講,符合圖譜自己 WHY 段落記載的第一次審計修正。

## 次要(不影響判定)

- KEY 行「核可綁定提案內容雜湊、關卡、金額上限、到期」的散文列舉沒有明寫 `holds()` 裡也獨立比對的 `task_id`、`revision`(計劃裡的 [S354] 條文倒是有寫「任務修訂或內容雜湊對不上」)。這兩個欄位理論上大多數情況被 content_hash 蘊含,只有在核可簽發者本身被攻破、刻意偽造 content_hash 相符但 task_id/revision 不同的核可時才用得上,測試證據已經齊全,只是 KEY 行的白話摘要可以順手把「任務、修訂」也列進去,讀起來更貼合實際防線。
- `test_two_workers_release_an_awaiting_proposal_once`(S372,待核可並行釋放只成功一次)與 F7 的 KEY 行沒有綁在一起;核對後這不影響「不超額」這個核心宣稱(真正把關的仍是 `begin()` 在寫入鎖裡的門檻比較),但如果之後要更嚴謹地涵蓋「多個工作者同時處理」的所有面向,可以考慮把它也列進 F7 的 [test:] 清單。
