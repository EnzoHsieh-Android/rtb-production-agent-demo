severity: major

範圍:`src/rtb/executor/execution.py`、`src/rtb/executor/guardrails.py`(Phase 8:執行前檢查多政策版本與決策新鮮度、[S511]、[S512])。

## F1 有效核可例外([S512])沒有限定「停下那一關」,總曝險/比例核可可以互相頂替新鮮度豁免

severity: major
blocking: 是 — 破壞 [S512] 合約(有效核可只免新鮮度一項,且限定「停下那一關」的那張),讓一份決策已過時、真正卡住它的那一關(比如比例過大)沒有任何有效核可,卻因為另一關(總曝險已滿)身上掛著一張此刻仍有效但根本用不到的舊核可,就被判定「不算過時」,繼續往下跑甚至停進待核可等人放行——放行後會照樣送出 DSP。這正是 Phase 8 要防的事故 F6 情境(舊決策憑舊證據被執行)。

引句:「決策過時、又沒有任一關的有效核可」

（`src/rtb/executor/execution.py` `_stale_in` 的判斷邏輯,對照計劃裡真正要的規則）

引句:「有它停下那一關的有效核可就不判新鮮度,其他檢查照跑」

（`docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md` 這次改動加的 WHY 說明,是 [S512] 的人話版:豁免只給「擋住這份提案的那一關」用的核可,不是「隨便哪一關有張沒過期的核可就算」。）

問題所在:`_stale_in`(`src/rtb/executor/execution.py:461-472`)是這樣判的:

```python
def _stale_in(self, tx, proposal, tenant, amount, now):
    if not guardrails.decision_stale(proposal, now):
        return False
    return not any(
        self._read_approval(self.store.latest_approval(tx, proposal, stage), proposal, stage,
                            tenant, amount, now) is not None
        for stage in APPROVABLE)
```

`APPROVABLE` 是 `{BUDGET_INCREASE_TOO_LARGE, AGGREGATE_LIMIT_REACHED}` 兩關(`src/rtb/executor/inbox_store.py:113`)。這裡對兩關都各查一次「此刻算不算數」(`approval.holds`,`src/rtb/executor/approval.py:116-127`),只要任一關有一張此刻沒過期、範圍指紋對、金額蓋得住的核可,就整份判「不算過時」——完全不管這一關「現在是不是真的卡住這份提案」。

跟正確的做法(`_approvals`,`src/rtb/executor/execution.py:514-531`)對照就看得出差在哪:`_approvals` 給比例上限的核可,只有「這筆真的超過比例」才留;給總曝險的核可,只有「這筆真的會讓已用額度超過門檻」才留;用不到的核可不算。`_stale_in` 沒有做這層過濾,是這次審查漏掉、也沒有變異測試蓋到的洞。

重現(在唯讀 repo 的臨時複本上跑,repo 本身沒有動):一份提案加預算超過比例上限(需要 `RATIO` 核可)、完全沒有 `RATIO` 核可;但它身上掛著一張仍然有效、只是從來用不到的 `AGGREGATE`(總曝險)核可(總曝險門檻很寬鬆,從未觸發那一關)。決策建立超過 15 分鐘之後處理:

```python
def test_unneeded_aggregate_approval_exempts_a_stale_ratio_block(h):
    prop = submit(h, 151)                              # 超過比例,需要 RATIO 核可,沒有
    approve_it(h, prop, AGGREGATE, expires_in=1500)    # 有效核可,但關卡是總曝險(從未觸發)
    h.clock.now = CREATED + timedelta(minutes=20)      # 過時(> 15 分鐘)

    result = h.process()
    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)
```

實測結果:`result.kind == Result.AWAITING_APPROVAL`、`result.block_code == BUDGET_INCREASE_TOO_LARGE`,不是預期的 `(Result.BLOCKED, DECISION_STALE)`。也就是這份過時決策直接被放進待核可,等一張「看起來只要人簽了比例核可就會放行」的隊列——完全繞過了新鮮度擋下,而且待核可畫面上不會顯示它其實已經過時。反方向(掛一張沒用到的 `RATIO` 核可去頂替真正卡住的 `AGGREGATE`)是同一段程式碼、同一個漏洞,邏輯對稱,沒有另外驗證。

範圍確認沒問題的部分:`approval.holds`(`src/rtb/executor/approval.py:116-127`)已經擋掉「無效核可也能免新鮮度」(過期、範圍指紋不對、金額蓋不住都回 None)與「別份提案的核可能免」(`task_id`/`revision`/`content_hash` 三個都要對得上),有專門測試 `test_an_approval_for_another_proposal_does_not_exempt_a_stale_one` 覆蓋且通過。這兩項威脅模型裡提到的疑點查證後不成立,只有「總曝險核可用不到卻免了」(及其反向)這一項是真洞。

重跑路徑(憑證過期後重讀、對帳查不到)用的是另一支函式 `_stale_on_rerun`(`src/rtb/executor/execution.py:474-481`):

```python
def _stale_on_rerun(self, tx, proposal, now):
    return (guardrails.decision_stale(proposal, now)
            and not self.store.used_approvals(tx, proposal))
```

這支改看「這份提案開始一筆時真的用過的核可」(`self.store.used_approvals`,對應 `_audit` 只在 `RATIO in live` 或 `AGGREGATE in live and begun.over_limit is not None` 才寫入使用紀錄,`src/rtb/executor/execution.py:737-753`),範圍天生就只包含「真正用得上」的那些,沒有 `_stale_in` 這個漏洞。這是本次覆核發現的不一致:兩條處理一筆的判斷點(簽發後、開始一筆的交易)用的是有漏洞的「查有沒有任一關此刻算數的核可」;兩條重跑路徑用的是正確的「查真正用過的核可」。兩邊本該是同一個規則([S512]:有它停下那一關的有效核可),卻用了兩套不同、寬嚴不一的判法。

建議修法方向(不代替作者裁定,只是把洞的形狀畫清楚):`_stale_in` 應該只在「這一關現在真的會擋下這份提案」時才去查那一關的核可——比照 `_approvals` 已有的過濾(比例看 `guardrails.increase_too_large`,總曝險看預判已用額度是否超過門檻),而不是對 `APPROVABLE` 兩關一視同仁地查「此刻算不算數」。

## 其餘檢查項目(未成立的疑點與確認無誤的部分)

**四條路徑的順序與優先權**(處理一筆、開始一筆的交易、憑證過期後重讀、對帳查不到)逐一追過:過期永遠排最前(`_process` 第 408、414 行,`_too_late` 第 719 行先判到期再判新鮮度,重跑兩條路徑的 `live` 閘門一樣把過期擋在最前面),跟第一次處理時的順序、以及既有的版本已變優先權一致,沒有找到順序被打亂的情況。政策版本擋在執行前檢查、緊接版本已變之後(`precheck`,`src/rtb/executor/execution.py:313-323`),新鮮度排在簽發之後、比例判斷之前,跟「實作狀態」節寫的偏離(1)一致;過時又超過比例、沒有核可的直接判決策已過時、不停待核可,`test_a_stale_proposal_over_the_ratio_is_blocked_rather_than_left_waiting` 覆蓋且通過。收件口最後確認成的原因在四條路徑上都是透過 `ack_blocked`/`_ack_terminal` 帶著明確的 `BlockCode.DECISION_STALE`(或 `POLICY_VERSION_CHANGED`)寫入,沒有找到「原因被吃掉、確認成別的代碼」的情況。

**[S511]**(重跑命中政策已變、決策已過時時嘗試判沒發生、收件口確認成新原因):追過 `_after_expiry` 與 `_reconcile_not_found` 兩條路徑,`_kept_reason_or_none` 與新加的 `_DecisionStale` 例外都正確地把新原因一路帶到 `_not_resent`/`_void_then_fail`,最終寫進 `block_code`。送過兩次的一律先作廢(`_void_then_fail` 對帳路徑不分次數一律先作廢,因為 DSP 結果本來就不明;`_not_resent` 只在 `row.send_count > 1` 才先作廢)。憑證過期路徑送過一次直接判「沒發生」是安全的:capability_expired 是 DSP 當下明確拒收(同步回應,不是逾時或不明),不是「結果不明」,所以不必先作廢求證——這是 Phase 6 就有的既有不變量,Phase 8 只是把 `DECISION_STALE`/`POLICY_VERSION_CHANGED` 這兩個新原因掛進同一條既有分流,沒有改動這個安全性前提。

**F1/F2/F3/F4/F7 事故合約**:跑過整套 `tests/executor`、`tests/analyzer`(835 個測試全綠,含這幾個事故的綁定測試)。特別針對 F3(「同一把鍵已由另一份修訂開過嘗試,取件照那把鍵的狀態確認,不要求第二張核可」)另外寫了一個變異重現,故意把第二份修訂放回待處理前的時間撥過 15 分鐘新鮮度窗口,想驗證 Phase 8 新加的新鮮度判斷點是不是會在 F3 的既有鍵判斷之前把它擋成決策已過時、蓋掉正確的「已交給執行」結果。結果是不會:F3 的去重判斷發生在 `receive()`(`src/rtb/executor/inbox_store.py:617-657`,第 640-643 行 `existing = attempt_store.latest(...)` 一命中就直接 `_settle_existing` 並 `continue` 到下一份候選),這一步在 `_process`/`_run`/`_stale` 之前就把這把鍵解決掉了,Phase 8 的新判斷點根本沒有機會介入。F1(對帳只用原鍵、不重算)、F2(確認前當機的復原路徑)、F4(照舊版本寫入被版本已變擋下)這三個合約 Phase 8 沒有碰到相關程式碑,追過程式碼與既有測試都還在。F7(總曝險到門檻停在待核可、由人核可放行)受 F1 提到的同一個 `_stale_in` 漏洞影響的方向是「讓待核可更容易被繞過新鮮度」,不是「讓 F7 本身的核可放行流程失效」——F7 既有測試都還通過,但這也表示 F7 的核可放行機制正是 F1 漏洞可以被拿來頂替的那一關,兩者是同一個洞的兩面。

## 結論

一個 major、blocking 的發現(F1):[S512] 的有效核可新鮮度豁免沒有限定在「真正卡住這份提案的那一關」,比例與總曝險兩關的有效核可可以互相頂替,讓一份已經過時、真正需要的那張核可根本不存在的提案繞過新鮮度擋下。已用測試重現(見上,`_stale_in` 邏輯錯誤,`src/rtb/executor/execution.py:461-472`);同一份提案的重跑路徑(`_stale_on_rerun`)用的是不同、範圍正確的判法,沒有這個洞,兩邊判法不一致本身也該一併修掉。其他三項覆核(四條路徑順序與收件口確認原因、[S511] 語意、F1/F2/F3/F4/F7 既有事故合約)追過程式碼與測試,沒有發現被 Phase 8 新判斷點弄壞的地方。
