# 審計結果:事故 F1 執行面合約(逾時/連線中斷 → 結果不明 → 同鍵對帳)

**審計方式**:唯讀讀取原始碼與測試,並用 rsync 把整個 repo(排除 `.venv` `.git`,`.venv` 用符號連結)複製到 `/private/tmp/.../scratchpad/audit-copy`,在複製體上做多輪「拿掉被守的程式 → 清 `__pycache__` → 重跑三支測試 → 比對紅綠 → 還原並用 `diff` 確認與原始檔完全一致」的變異測試。原始 repo 全程未被寫入(每輪變異後都 `diff` 驗證還原無誤)。

---

## 1. 是不是真的業務合約

是真合約,不是巧合行為。三處證據互相印證同一件事,而且是刻意設計:

- 合約文字(`docs/.../2026-09-22_事故-F1-...md:23`):「執行行程遇到外部結果不明(逾時、連線中斷)時標記為結果不明,只用原冪等鍵向 DSP 對帳確認,絕不換新鍵重試」。
- `src/rtb/domain/attempt.py:1-6` 的模組說明原話呼應同一件事:「一個『邏輯操作』永遠只有一把鍵……鍵一旦存進嘗試紀錄,之後一律用存下的那把,不從提案重算」,且 `operation_key()`(20-34 行)是內容決定性算出、`AttemptRow.key` 存一次後全程只讀。
- `src/rtb/executor/execution.py:1-14` 模組說明也明白寫著「對帳(增量 4):結果不明的嘗試用冪等鍵查 DSP 操作紀錄,查到就核對完整內容再驗證;查不到就重讀廣告、重跑執行前檢查,通過就同鍵重送」——這段程式行為是設計出來的,不是偶然出現。

這條改掉(例如對帳重送時算一把新鍵)會直接違反 DSP 冪等鍵設計初衷,屬於破壞性變更。

## 2. 三支測試合起來夠不夠證明這條合約

逐句對照,結論是「大部分夠,但有兩個實測出來的漏洞」:

| 合約子句 | 覆蓋情況 | 依據 |
|---|---|---|
| 逾時/連線中斷 → 標記結果不明 | **有**,且非空話 | 見第 3 點的變異 E:三支測試全部在「前置」那行就變紅 |
| 只用**原**冪等鍵重送 | **有**,但只被 test 1、3 鎖住 | 見第 4 點變異 A:改鍵只讓 `test_f1_timeout_before_commit_...` 和 `test_f1_a_delayed_commit_...` 變紅,`test_f1_timeout_after_commit_...` 完全沒感覺(它根本沒有重送這一步) |
| 「查到就核對內容再驗證;查不到才重送」這個**分流邏輯本身** | **沒有被這三支測試鎖住** | 見第 4 點變異 B:把 `_reconcile_unknown`(`execution.py:494-496`)查 `operation_record` 的鍵改壞,讓它永遠查不到、改走重送分支,三支測試**全部照樣綠燈**——因為重送用的還是同一把鍵,DSP 冪等回放後兩條路徑收斂到一樣的最終狀態序列。真正鎖住「查到就核對、不重送」這個分流的是 `tests/executor/test_reconcile.py:65-72`(`test_reconcile_finds_the_operation_by_key_and_verifies_it`,斷言 `len(h.dsp.writes) == 1`)和 `test_reconcile.py:79-85`(`test_reconcile_escalates_a_mismatched_operation_record`,對帳內容對不上要轉 `idempotency_conflict`),但這兩支是用假 DSP 的單元測試,**不在本次要轉正綁定的三支之內** |
| 預算只改一次 | **字面斷言有**(三支都有 `len(applied(world, prop)) == 1`),**但不是被這條合約獨立鎖住的** | 見第 4 點變異 D:關掉 DSP 端冪等去重(`src/rtb/dsp/store.py:345-347` `_existing_operation`),三支測試**照樣全綠**。原因是三支測試的實際 timing 下,真正擋住「晚到的舊請求重複套用」的是 DSP 的**版本檢查**(`store.py:353-355` `VersionConflict`),不是冪等鍵去重表——這層保護屬於 Phase 1 的 DSP 側合約(合約文件本身第 25 行也寫明「DSP 側的保證已在 Phase 1 完成並另有合約」) |
| 本地最終狀態與 DSP 一致 | **有** | 三支都斷言最終 `("verified", None)` 且 `world.campaign().version == 2`;見第 4 點變異 C |

## 3. 測試會不會假綠

不會假綠,前置條件是真的。用「拿掉被守的程式」驗證(每次變異前都清 `__pycache__`):

把 `RESPONSE_TABLE` 裡 `no_response` 那列的目標從 `A.UNKNOWN` 改成 `A.FAILED`(`execution.py:177-178`,即讓「沒拿到回應」不再映射成結果不明),三支測試**立刻全部在前置行紅掉**:

```
test_f1_timeout_after_commit_is_reconciled_from_the_operation_record 失敗於:
>  assert world.states(prop)[-1] == ("unknown", None)  # 前置:逾時,但 DSP 已提交
E  AssertionError: assert ('failed', 'not_happened') == ('unknown', None)
```

這證明:三支測試裡真的有一個逾時發生(`World(..., dsp_timeout=0.2)` vs `DspServer(..., hang_seconds=0.6)`,見 `test_execution_e2e.py:34,131,235,254,271`),而且真的走到了被測分支,不是斷言碰巧為真。另外 `test_f1_a_delayed_commit_and_a_resend_apply_once` 在對帳前還多斷言 `applied(world, prop) == []`(`test_execution_e2e.py:277`,「前置:DSP 還沒提交」),對照 `server.py:227-236` 的 fault 注入邏輯(`delayed_response` 在提交前先睡 `delay_seconds`,`timeout_before_commit` 提交前睡完直接 `raise NoResponse` 完全不提交,`timeout_after_commit` 先提交、提交後才睡),三種 DSP 真實狀態(未提交/已提交/延遲提交)各自被對應到正確的測試,前置條件成立。

## 4. 轉正後哪些改動會讓這三支測試變紅(kill 配方,已實測)

會變紅:
- **改回鍵**:`execution.py:524` `self.dsp.write(proposal, row.key, signed.token)` 把 `row.key` 換成任何非原始鍵的值(例如 `row.key + "-resend"`)→ `test_f1_timeout_before_commit_...` 和 `test_f1_a_delayed_commit_...` 變紅(拿到 `capability_rejected`,因為簽發的憑證範圍綁的是舊鍵)。
- **拿掉「查到就驗證」**:`execution.py:526-531` `_found()` 最後一行 `return self._verify(proposal, row)` 改成 `return False`(找到操作紀錄後不再驗證)→ `test_f1_timeout_after_commit_...` 變紅。
- **拿掉「結果不明」判定本身**:`execution.py:177-178` `no_response` 規則的目標從 `A.UNKNOWN` 改掉 → 三支全紅(見第 3 點)。

不會變紅(轉正前建議一併正視的盲區):
- 把 `_reconcile_unknown`(`execution.py:494-496`)查 `operation_record` 用的鍵改壞 → 三支全綠(見第 2 點)。
- 關掉 DSP 冪等去重表(`store.py:345-347`)→ 三支全綠,因為版本檢查頂上了(見第 2 點)。

## 5. 結論

**有條件轉正。**條件:(1)轉正筆記裡明講「查到操作紀錄就核對內容再驗證、不重送」這半句的獨立鎖定,實際落在 `tests/executor/test_reconcile.py:65` 與 `:79`(假 DSP 單元測試),不是這三支 e2e 測試單獨證明的,綁定時把這兩支也一併列進去,或另補一支能被「查詢鍵改壞」變異殺死的 e2e 測試;(2)「預算只改一次」這句在合約文字裡註明其真正的強制層是 Phase 1 的 DSP 冪等去重(`store.py` `_existing_operation`),這三支測試只是重申斷言、不是獨立守住它——若之後 DSP 那層被改壞,這三支不會變紅,別誤以為轉正後這條合約單獨扛得住。其餘子句(逾時/連線中斷→結果不明、同鍵重送、最終狀態一致)三支測試證據紮實、前置條件經變異驗證為真,可以放心轉正。
