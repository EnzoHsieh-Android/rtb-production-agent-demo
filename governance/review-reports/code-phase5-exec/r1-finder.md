severity: minor

# 外家 finder 審查報告(Phase 5 執行側:版本衝突回報成版本已變)

審材:`/Users/enzo/rtb-3b/governance/review-reports/code-phase5-exec/r1-snapshot.patch`(7 個檔,逐 hunk 讀完)。
實驗目錄:`/var/folders/tc/xmllmxtn4q5704lsy80wc1kw0000gn/T/tmp.hod1K0gj5F`(複製 src/tests,repo 未動)。
基線:`tests/executor/` 全部 324 支通過;ruff、mypy 對 `src/rtb/executor` 無問題。

## F1 版本在「憑證過期重送前」或「對帳重送前」被別人改掉時,仍回報成「同一操作先前已失敗」,分析端不會重新規劃

severity: minor
blocking: 否 — spec [S310] 明文只涵蓋 DSP 回 409 的那一種,其他失敗「照舊」;這條不是這份 diff 造成的退步,但同一類版本衝突仍會悄悄漏掉重新規劃
引句:「+    if code is OutcomeCode.VERSION_CONFLICT:」

`block_code_for_failure` 只把結果代碼 `VERSION_CONFLICT` 對應到「版本已變」。但執行端還有兩條路,真正的原因是版本已變,嘗試紀錄卻寫成 `NOT_HAPPENED`(沒發生):

- file: `/Users/enzo/rtb-3b/src/rtb/executor/execution.py:476-492`(`_after_expiry`):DSP 回 401 capability_expired,重讀後 `precheck` 回 VERSION_CHANGED,於是 `passed=False`,寫入 `A.FAILED, code=C.NOT_HAPPENED`(送出超過一次時先走 `_void_then_fail`,結果一樣是 NOT_HAPPENED)。
- file: `/Users/enzo/rtb-3b/src/rtb/executor/execution.py:638-644`(`_reconcile_not_found`):結果不明的鍵在 DSP 查不到,重跑 `precheck` 得到版本已變,接著 `_void_then_fail`,最後同樣是 FAILED/NOT_HAPPENED。

這兩條都經 `_ack_terminal` 寫出 `operation_previously_failed`。主線的分析端 `/Users/enzo/rtb-production-agent-demo/src/rtb/analyzer/flow.py:330-334` 只有看到 `version_changed` 才重新規劃,其他一律走 `_closed`,所以這個任務會直接結案、不重新規劃。Phase 5 要解的 F4 情境(版本已變就重新讀取、重新決策,不能卡住)在這兩條路上沒有被接住。

重現(臨時目錄,`tests/executor/test_repro_p12.py`):第一次寫入時排入 `WriteAnswer(401, "capability_expired")`,在 `on_write` 裡把 c1 的 version 加 1,再 `h.process()`:

```
attempts: [(None, 1), (None, 1), ('not_happened', 1)]
proposals: [('t1', 1, 'pending', 'blocked', 'operation_previously_failed')]
E  At index 1 diff: 'operation_previously_failed' != 'version_changed'
1 failed in 0.11s
```

(斷言期望的是版本已變,實際是同一操作先前已失敗。)

### 已看:三處改動都有殺傷力的測試守住
逐處改回寫死 `BlockCode.OPERATION_PREVIOUSLY_FAILED` 後跑 `tests/executor/`:
- 開始一筆撞到既有失敗鍵(execution.py `_take`):1 支翻紅(test_a_dsp_version_conflict_is_acknowledged_as_version_changed)。
- 終點確認(`_ack_terminal`):2 支翻紅(上一支,加上 test_two_concurrent_writers_reproduce_a_version_conflict)。
- 取件撞到既有失敗鍵(`_settle_existing`):2 支翻紅(test_queue S106,加上 S310 那支)。
S310 三處寫在同一支測試裡,依序斷言;每處單獨改壞都會翻紅,所以沒有假綠。

### 已看:「版本已變」語意擴大,會不會被別的程式誤讀成「沒呼叫過 DSP」
- DSP 的寫入流程先查冪等鍵、再比版本(`/Users/enzo/rtb-3b/src/rtb/dsp/store.py:369-381`)。同一把鍵重送時,不論是回放還是冪等衝突,都在比版本之前就處理掉了,所以只要是 409 版本衝突,就一定表示這把鍵在 DSP 沒有寫入紀錄、也沒有冪等紀錄。版本號只會往上加,還在路上的舊請求晚到時一樣會被 409 擋下。「版本已變」底下實際代表的是「DSP 沒寫」,這個意思沒有改變。
- 分析端收件表被清掉之後查 DSP(主線 flow.py `_from_dsp`):409 的鍵查不到,會走「清除後重新規劃」,跟 spec 第 46 行「寫入被 DSP 拒絕不會有操作紀錄」一致。
- 執行側用到 `BlockCode.VERSION_CHANGED` 的地方只有 `precheck` 和這次的共用函式。對帳、保留期清除 `_purge_finished_tasks`、死信(投遞次數)都不看擋下原因。嘗試紀錄沒有 DELETE(attempt_store.py 第 3 行宣告,grep 也確認),所以 `version_conflict_count` 的稽核來源不會被清。
- 接續任務的冪等鍵有把 task_id 算進去(`/Users/enzo/rtb-3b/src/rtb/domain/attempt.py:26-31`),新任務不會撞到舊的失敗鍵。
- 人工把失敗判定寫成 `MANUAL_FAILURE`(attempt_store.py:431),照舊寫成「同一操作先前已失敗」。

### 已看:收件口回應帶 block_code(S300)
- 新收件走 201,`Accepted` 的預設值讓 `block_code` 是 null,鍵確實存在;重送已擋下的提案時回的是原因代碼。主線分析端 `_belongs_to` 要求「已擋下 ⇔ block_code 是字串」,這份 diff 產出的回應符合。
- 把 `CASE WHEN disposition = ? THEN block_code END` 改成直接回 `block_code`,測試全部照樣通過。原因是 `block_code` 只由 `ack_blocked` 寫入,而且跟處置同時寫成已擋下,所以這個 CASE 目前是多餘的防線,不影響正確性。

### 已看:併發與測試穩定性
- S309 用柵欄讓兩邊都讀完 DSP、通過執行前檢查之後才一起寫。斷言看的是嘗試表裡的 `("failed","version_conflict")`,也看 DSP 歷史只有一筆,所以不會把「執行前檢查擋下」誤當成衝突。
- `_settle_existing` 多讀的 `existing.code` 跟 `state` 來自同一列、同一個交易,沒有交錯的空窗。

⚠ 交編排者:
1. F1 要不要升級,需要設計面裁定。spec [S310] 明文寫「其他失敗應照舊」,依字面這份 diff 合規;可是 Phase 5 的目標是「版本已變就重新規劃」,憑證過期後重送、對帳重送這兩條路也是版本已變,卻被記成 NOT_HAPPENED 而結案。要嘛補進 spec(例如作廢或失敗時帶「因版本已變」的結果代碼),要嘛在計劃筆記寫明刻意不涵蓋,並附回頭條件。
2. 上線切換:這份 diff 部署之前,已經因 409 失敗、被確認成 `operation_previously_failed` 的收件列會保持舊代碼。部署後分析端重送,只會看到舊代碼而結案、不重新規劃。收件表保留期是 2 小時,影響範圍有上限,但計劃裡沒看到這段說明。
3. 部署順序:主線分析端的 `_belongs_to` 要求已擋下時一定帶字串 `block_code`。如果分析端比這份執行側先上線,舊執行端回的已擋下沒有這個鍵,分析端會永遠當成「讀不懂、下一輪再問」。這在 diff 之外,請確認兩邊一起上線。

總結:最高等級為 minor,擋推送 0 條(F1 是否升級見 ⚠1)。
