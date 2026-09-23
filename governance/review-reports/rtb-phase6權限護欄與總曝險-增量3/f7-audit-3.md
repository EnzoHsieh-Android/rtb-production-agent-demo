判定:不同意

理由:

**已核對範圍**
- 通讀了 `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md` 裡 F7 的 KEY/WHY 行與 `kill_recipes`(8 筆屬於 F7)、`Projects/RTB_Phase6權限護欄與總曝險_計劃.md` 增量 1、3 全文(S330–S343、S350–S379)、`tests/executor/test_aggregate_limit.py`、`test_f7_end_to_end.py`、`test_approval.py` 全文、交接文件第 16 節 F7 原始描述,以及既有的 `governance/review-reports/.../f7-audit-1.md`、`f7-audit-2.md`(前兩輪已同意的部分不重複列,只驗證是否仍成立)。
- 全程在 `/tmp` 底下用複製出來的臨時副本做實驗(含完整 `.venv`),原 repo 未被寫入,git 全程唯讀;實驗完已清除臨時目錄。

**(1)/(2)/(3) 合約文字裡有一個子句沒有任何綁定測試真的守著,已用改壞實驗證實**

合約文字明講核可綁定「…以及範圍指紋:租戶名稱與設定、**比例常數**、**政策版本**」。程式碼(`src/rtb/executor/approval.py` 的 `scope_fingerprint()`)確實把這兩者算進雜湊:

```python
"ratio": [guardrails.MAX_INCREASE_NUMERATOR, guardrails.MAX_INCREASE_DENOMINATOR,
          guardrails.MIN_INCREASE_STEP],
"policy_version": POLICY_VERSION,
```

`holds()` 在核對核可時會用**當下**的 `scope_fingerprint(tenant)` 重算並比對——所以理論上「核可核發後,比例常數或全域政策版本常數改版,既有核可就該失效」。但:

- 全專案唯一提到這兩個常數的測試只有 `test_admin_and_executor_agree_on_the_scope_fingerprint`([S379]),而這支測試**沒有被綁進 F7 合約行的 `[test:...]` 清單**;而且就算綁進來,它只斷言「管理工具與執行迴圈兩邊各自算出的雜湊值相等/不相等」,從未做過「先核可 → 常數才變 → 再 `settle()` 應為 0(核可失效)」這種行為斷言——連它自己都沒有證明這個失效行為,只證明了兩處計算一致。
- 我在臨時副本做了兩個改壞實驗:
  1. 把 `scope_fingerprint()` 材料裡 `"ratio": [...]` 整段拿掉(比例常數不再影響指紋)。
  2. 把 `"policy_version": POLICY_VERSION,` 那一行拿掉(全域政策版本不再影響指紋)。
  
  兩次改壞後各自跑 `tests/executor/test_aggregate_limit.py`、`test_f7_end_to_end.py`、`test_approval.py` 三個檔案(92 條測試,含全部 F7 綁定測試),**全部維持綠燈**;唯一翻紅的是不在 F7 綁定清單裡的 `test_admin_and_executor_agree_on_the_scope_fingerprint`,而且翻紅原因只是「兩次算出的雜湊值意外相等」這個副作用,不是「核可該失效卻沒失效」被抓到。

也就是說:如果有人不小心讓比例常數或政策版本從指紋材料裡消失(或打錯字導致永遠不生效),F7 合約綁定的整組測試完全接不住,合約文字裡這句具體承諾目前是**沒有行為證據**的。

這跟前兩輪審計抓到、後來已修好的洞是同一類問題(前兩輪抓到「租戶身分」「提案自身 `policy_version` 顯式檢查」沒被綁,已補 [S375] 修好;我自己也重驗了這個修復——把 `scope_fingerprint()` 裡的 `"tenant": tenant.name` 或 `holds()` 裡的 `proposal.policy_version == POLICY_VERSION` 拿掉,`test_an_approval_is_void_across_policy_or_tenant_changes` 確實翻紅,修復有效),但這次「範圍指紋」裡另外明講的「比例常數」與「指紋內的政策版本」兩個子項,沒有被同樣方式補上。

**其餘核對均通過,已用改壞實驗逐一驗證(8 筆 F7 kill_recipes 全部翻紅其對應的命名測試)**
- `test_f7_many_small_increases_stop_at_the_aggregate_limit`:拿掉門檻比較 → 3000 筆全寫進 DSP,斷言翻紅。
- `test_an_unresolved_reservation_counts_no_matter_how_old`(4 個參數化狀態全翻紅)、`test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it`、`test_an_approval_is_void_when_scope_expiry_hash_or_stage_changes`([spoil1])、`test_the_capability_never_outlives_the_approval_it_used`、`test_an_approval_that_expires_mid_flight_lets_nothing_through`(兩個 stage)、`test_an_approval_is_void_across_policy_or_tenant_changes`(政策版本、租戶身分兩種改壞各自驗證)——全部依筆記記載的 old→new 改壞後翻紅,筆記與程式現況一致,審計方法紮實。
- 「剛好等於門檻仍放行、超過才擋」「24 小時窗口剛好滿仍算、多 1 秒不算」「核可剛好在開始一筆那一刻到期即失效」等精確邊界都有對應測試把守,行為與合約措辭一致。
- 「分批」正面半句(額度釋放後**全新任務**能通過)確實由 `test_a_new_task_passes_once_the_budget_is_released` 端到端證明(兩種釋放路徑:24 小時出窗、轉人工判失敗),不再只是底層測試組合推論——第一輪審計抓到的洞已修好。

**(4) 措辭核對**
- 合約本身用詞精確,沒有比程式行為說得更滿:「剛好等於門檻仍放行」「先停在待核可…沒有核可、提案到期就擋下結案」「『分批』只做到額度釋放後新任務能再通過,沒有自動排程」都與程式行為及測試斷言吻合。
- 唯一的問題不是措辭誇大,而是「範圍指紋:…比例常數、政策版本」這句話**程式做到了,但沒有測試證明它做到**——屬於題目要求的「宣稱沒有綁定測試守著」那一類。

**建議補什麼**
1. 在 `test_an_approval_is_void_across_policy_or_tenant_changes`(或新增一支)裡加入行為斷言:核可核發後 `monkeypatch` 修改 `guardrails.MAX_INCREASE_NUMERATOR`/`MAX_INCREASE_DENOMINATOR`/`MIN_INCREASE_STEP` 任一個,再 `settle(h)` 應為 0;以及核可核發後修改 `approval.POLICY_VERSION`(全域常數,不是提案的 `policy_version` 欄位),再 `settle(h)` 應為 0。
2. 把補上的測試(或改寫後的 `test_admin_and_executor_agree_on_the_scope_fingerprint`,把它從「只比對雜湊值」改成「核可失效的 `settle()` 行為驗證」)綁進 `執行迴圈.md` 第 24 行 F7 合約的 `[test:...]` 清單。
3. 補一筆對應的 `kill_recipes` 項目(把 `scope_fingerprint()` 的 `"ratio"` 或 `"policy_version"` 那行拿掉),讓這個回歸有機械化的殺傷力配方守著,跟其餘 8 筆待遇一致。
