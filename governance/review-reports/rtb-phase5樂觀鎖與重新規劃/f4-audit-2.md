結論:同意

## 合約文字(執行迴圈.md summary 第 21 行)

「提案形成之後若另一方先更新了廣告,帶著舊預期版本的提案不會寫進 DSP(執行前檢查擋下,或檢查通過後 DSP 以版本衝突拒收),收件口回報版本已變;分析行程(有給操作查詢時)另開接續任務重讀現況再規劃,整條接續鏈最多 3 代,不會覆蓋較新的值。」

對照外部規格 F4(`帶著舊 expectedVersion 的寫入被拒,系統重新取現況、不覆蓋新值`)與 Phase 5 DoD(concurrent writers 可重現衝突、conflict trace 可查、文件區分 version check / freshness / idempotency),這句是規格的精確化而非超譯——WHY 段落坦承原措辭寫得像「無條件保證」,已改正為兩層擋法、「有給操作查詢時」、「最多 3 代」三個限定,這三個限定都能在程式碼裡找到對應的守衛(`precheck` 的 version 檢查、`DspStore` 的 CAS 檢查、`flow.advance` 的 `operation_lookup is None` 早退、`task_store._write_follow_up` 的 `MAX_GENERATION`),不是文件單方面加碼。

## 逐子句核對

1. **「舊預期版本的提案不會寫進 DSP」「不會覆蓋較新的值」**
   守衛:`test_f4_a_stale_proposal_is_replanned_from_the_current_state`(`tests/analyzer/test_f4_end_to_end.py`)。直接斷言 `STALE_BUDGET not in [...]`、最終 `(budget, version) == (FRESH_BUDGET, 3)`,是唯一真正端到端驗證「舊值從沒寫進去、最終值是新決策」的測試。判斷:夠格,且斷言具體到值而非只看狀態字串。

2. **「(執行前檢查擋下,或檢查通過後 DSP 以版本衝突拒收)」兩層擋法**
   守衛:同一支測試用 `race` 參數化成 `precheck`/`dsp_write` 兩格,各自斷言 `stale_attempts`(precheck 格是 `[]`——連嘗試都沒開;dsp_write 格是 `[("in_flight", None), ("failed", "version_conflict")]`——DSP 才擋)。PITFALL 註記也明講「只看端到端結果分不出哪層擋」,測試確實用嘗試紀錄的內容區分了兩層。另外 `test_a_dsp_version_conflict_is_acknowledged_as_version_changed`、`test_two_concurrent_writers_reproduce_a_version_conflict`(真實併發、真實 HTTP DSP)專責 DSP 層;`test_each_failed_precheck_blocks_the_proposal_without_a_write` 專責 precheck 層(對 `VERSION_CHANGED` 斷言零嘗試、零 DSP 寫入)。判斷:夠格,兩層各有專責測試,不是互相替代的巧合綠燈。

3. **「收件口回報版本已變」**
   守衛:上述測試都斷言 `block_code == "version_changed"` 或 `error_detail` 含 `blocked=version_changed`。判斷:夠格。

4. **「分析行程(有給操作查詢時)另開接續任務重讀現況再規劃」**
   守衛(給了查詢時建接續任務):`test_a_version_conflict_hands_the_task_over_to_a_follow_up`、端到端測試的 `child_proposal.campaign_version_observed == 2`、`new_budget == FRESH_BUDGET`。
   守衛(沒給查詢時不動):`test_advancing_a_handed_off_task_calls_no_collaborator`——這支正是 WHY 段落提到「第一次獨立審計指出沒有綁定測試,補綁後再審」的那支,對應 `flow.py` 的 `if row.state is TaskState.HANDED_OFF and operation_lookup is None: return row.state`。判斷:夠格,兩個分支(給/不給)都有對應測試,不是只測一半就宣稱整句。

5. **「整條接續鏈最多 3 代」**
   守衛:`test_the_follow_up_chain_stops_at_the_generation_limit`,實跑到代數上限、斷言 `len(chain) == 3` 且 `error_detail` 含 `replan_limit_reached`。判斷:夠格,且此測試不在作者 kill_recipes 清單裡(作者沒對這條防線做過改壞驗證)。

6. `test_each_failed_precheck_blocks_the_proposal_without_a_write`、`test_after_the_retention_window_a_missing_write_is_replanned` 屬輔助/邊界測試(前者涵蓋所有 BlockCode 的「無寫入」通則,後者是收件表已清掉、靠冪等鍵查 DSP 那個 `after_retention` 分支),都對應句子裡「另開接續任務重讀現況」的完整觸發條件,沒有多餘或無關的綁定。

## 我做的改壞實驗(臨時副本:`/tmp/f4-audit/repo`,rsync 排除 `.git`/`.venv`,`PYTHONPATH=src` 用專案 venv 跑 pytest)

先跑基線:綁定清單 8 支測試(15 個參數化案例)全綠。

**實驗一(代數上限差一錯誤,作者未想到)**
檔案:`src/rtb/analyzer/task_store.py:414`
`if generation > MAX_GENERATION:` → `if generation > MAX_GENERATION + 1:`(允許多跑一代)
跑綁定清單全部 8 支測試 → 14 passed, **1 failed**:`test_the_follow_up_chain_stops_at_the_generation_limit` 斷言 `len(chain) == 3` 得到 `4`,精準翻紅;其餘 7 支不受影響。結論:「最多 3 代」這條防線有效被守住,且只有它自己的綁定測試會叫。

**實驗二(precheck 呼叫點被整段繞過,作者也未想到——不同於作者原本改 `precheck` 函式內版本比較那一行)**
檔案:`src/rtb/executor/execution.py:362`
`signed = precheck(proposal, view) or self._sign(proposal)` → `signed = self._sign(proposal)`(完全跳過執行前檢查,直接簽發)
跑綁定清單 → **4 failed**:`test_f4_a_stale_proposal_is_replanned_from_the_current_state[precheck]`、`test_each_failed_precheck_blocks_the_proposal_without_a_write[campaign_not_found / campaign_not_active / version_changed]`。其中 `[version_changed]` 那格直接證明「precheck 擋下、不呼叫 DSP」這個子句是被真正驗證的(結果從 `BLOCKED` 變 `EXECUTED`,不是巧合綠燈)。改完立即還原。

兩個改壞法都各自被對應的綁定測試精準翻紅、且都不在作者的 `kill_recipes` 清單裡(作者只驗過 `dsp/store.py` 的 CAS 檢查、`execution.py` 的 precheck 內版本比較、`inbox_store.py` 的 `VERSION_CONFLICT` 分流、`flow.py` 的 block_code 判斷與 `operation_lookup is None` 守衛,共 5 個 recipe/4 支測試),補上了對「代數上限」與「precheck 呼叫點本身」這兩條防線的獨立驗證,結果都翻紅,沒有發現空頭綁定。

## 結論理由

合約句子可驗證、跟外部規格 F4 吻合(是精確化而非超譯)、三個限定詞都各自有測試守住其存在與否的分支差異,新增的兩種改壞法也都被精準命中而非誤觸。同意此條 KEY 合約與其綁定測試清單成立,無需補測試。
