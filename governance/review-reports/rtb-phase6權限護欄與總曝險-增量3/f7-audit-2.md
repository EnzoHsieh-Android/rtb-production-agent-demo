判定:不同意

以下是逐項核對結果(已在 /tmp/rtb-audit/repo 的隔離副本上實測,repo 原始檔案全程只讀未動)。

**核心問題:F7 合約綁定的測試群守不住「核可因政策版本改變、或廣告換掛到另一個租戶而失效」這個真實存在的行為,而且其中一支綁定測試的名字誤導了讀者以為它守住了。**

1. `test_an_approval_is_void_when_scope_expiry_hash_stage_or_policy_changes`(對應 [S354],綁在 F7 合約行上)這個名字宣稱涵蓋 "hash、stage、or_policy" 改變會讓核可失效,但它綁進去的 9 組 spoil 案例(`_wrong_key`、`_other_revision`、`_other_content`、`_other_stage`、`_scope_changed`、`_expired`、`_amount_over`、兩個 `_forged`)裡沒有一組真的改動政策版本;測試檔裡的 `submit()` 輔助函式預設把每份提案的 `policy_version` 釘死成目前的 `POLICY_VERSION`(見 `tests/executor/test_approval.py:40-43` 的註解「樣本預設的政策版本不是目前那一版,核可一律不算數」正說明它刻意避開這條路)。
   我做了實驗驗證:把 `src/rtb/executor/approval.py` 裡 `approval.holds()` 的 `proposal.policy_version == POLICY_VERSION` 這行整行拿掉,F7 合約綁定的全部 13 支測試(28 個參數化案例)照樣全線通過(`test_f7_many_small_increases_stop_at_the_aggregate_limit`、`test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it` 等一個都沒紅)。真正驗到「政策版本不同,核可不算數」的是 `test_an_approval_is_void_across_policy_or_tenant_changes`([S375],同一個測試檔裡),但這支測試根本沒有出現在 F7 合約行的 `[test:...]` 清單裡。

2. 同一次實驗也發現第二個洞:把 `approval.scope_fingerprint()` 組雜湊用的 material 字典裡 `"tenant": tenant.name` 這個鍵拿掉(核可就不再綁定租戶「身分」本身,只比對廣告清單/上限等數值是否相同),F7 綁定的 13 支測試一樣全數通過、一個沒紅。這正是增量 3 設計文件裡明講的攻擊面(「名稱一起算,廣告改掛到設定值一模一樣的另一個租戶也會失效」,`docs/.../RTB_Phase6權限護欄與總曝險_計劃.md` 第 278 行),但綁在 F7 合約上的測試群完全接不住,同樣只有 [S375] 那支能抓到。

3. 這兩個洞都不在 F7 合約自己的 `kill_recipes`(執行迴圈.md 開頭欄位)六筆改壞清單裡——kill_recipes 列的六筆(門檻比較拿掉、未結案不算額度、無核可當有核可、待核可不驗核可放回、憑證活得比核可久、到期邊界差一秒)我逐一實測全部翻紅,跟筆記記載相符,審計方法本身是紮實的;但清單本身漏掉了「政策版本 / 租戶身分」這兩種對核可有效性一樣關鍵的改壞方式。

**要補什麼:**
- 把 `test_an_approval_is_void_across_policy_or_tenant_changes`([S375])加進 執行迴圈.md 第 24 行 F7 合約的 `[test:...]` 清單——它就在 `tests/executor/test_approval.py` 同一個檔案裡,不用新寫測試,只是沒被綁進來。
- 把 `test_an_approval_is_void_when_scope_expiry_hash_stage_or_policy_changes` 改名(建議拿掉 `_or_policy`,例如 `test_an_approval_is_void_when_scope_expiry_hash_or_stage_changes`),避免它的名字宣稱了自己其實沒測到的東西,誤導下一個讀合約的人。
- 合約措辭「核可綁定提案、關卡、金額上限與到期」建議擴充成「核可綁定提案(含內容雜湊)、關卡、金額上限、到期,以及範圍指紋(租戶設定、比例常數與政策版本)」,並把上面補進去的 `[S375]` 對應這句新增內容——目前的四項列舉不算「說得比程式滿」(沒承諾程式做不到的事),但確實漏掉了程式真的在做、也確實有測試(只是沒綁在這裡)在守的部分,跟第 1 點的誤導名稱疊在一起,構成一個容易被忽略的洞。

**其餘逐項核對均通過,沒有發現缺口(附驗證方式):**
- 24 小時窗口(已驗證算入窗、未結案永遠算入):`test_an_unresolved_reservation_counts_no_matter_how_old`、`test_a_verified_reservation_leaves_the_window_24_hours_after_verification` 把邊界(剛好 24 小時、24 小時多 1 秒)都測到位,語意跟合約文字一致。
- 「加上去超過門檻才停、剛好等於門檻仍放行」:`test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it` 用 cap=99/100 精確卡邊界;我把 `attempt_store.py` 的比較從 `>` 改成 `>=` 立刻翻紅,證實測試真的在守這條邊界。
- 「多個工作者同時處理也不會超過門檻」:`test_two_workers_cannot_race_past_the_aggregate_limit`(2 執行緒精細柵欄,鎖外算額度、鎖內寫入)與 `test_f7_many_small_increases_stop_at_the_aggregate_limit`(8 執行緒、3000 筆真並行壓力)兩層證據疊起來,足以撐住這句話,我也重跑過 baseline 全部通過。
- 「分批」只做到額度釋放後新任務能過、沒有自動排程:`test_a_new_task_passes_once_the_budget_is_released` 明確測了兩種釋放路徑(24 小時出窗、轉人工後判失敗)讓全新任務通過,而被擋下的那份確實原地不動;措辭與行為相符,沒有誇大。
- 核可到期邊界(取件後、開始一筆前剛好過期):`test_an_approval_that_expires_mid_flight_lets_nothing_through` 的注解本身寫著這是第一輪獨立審計抓到、後來修正的邊界;我把 `execution.py` 裡 `now.timestamp() < found.expires_at` 改成 `<=` 也確實翻紅,測試是真的在守這個邊界,不是巧合過關。
- kill_recipes 列的六筆改壞(門檻比較、未結案計數、無核可視為有核可、待核可跳過驗核可、憑證封頂到核可到期、到期邊界差一秒)逐一實測全部如筆記所述翻紅。

涉及檔案:`/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md`(第 24 行合約、第 49 行 kill_recipes)、`/Users/enzo/rtb-production-agent-demo/src/rtb/executor/approval.py`(`holds()` 第 125 行、`scope_fingerprint()` 第 55-65 行)、`/Users/enzo/rtb-production-agent-demo/tests/executor/test_approval.py`(第 235、438 行一帶)。
