severity: major

## F1 交易內才補抓的總曝險核可沒有壓短能力憑證

severity: major  
blocking: 是 — 核可到期後，DSP 仍可能接受依該核可放行的過時決策，破壞既有「能力憑證不得活得比所用核可久」合約

引句:「算數就補進 live(開始一筆照它記核可使用),不算數就不放行」

第 2 輪修正現在會在開始一筆的交易裡重算曝險；若鎖外預判不需要總曝險核可、鎖內才發現需要，`_approved_while_stale()` 會讀取最新核可並放進 `live`。file: `src/rtb/executor/execution.py:726`

但能力憑證早在 `_gate()` 裡簽好；只有當核可已出現在鎖外的 `held` 時，才會用 `not_after=min(...)` 重簽並把憑證期限壓到核可期限。file: `src/rtb/executor/execution.py:473` file: `src/rtb/executor/execution.py:482`

交易內補抓核可後只執行：

- `live[AGGREGATE] = found`
- 以 `approved=True` 開始嘗試
- 寫入核可使用紀錄

卻沒有同步重簽或放掉收據重來。file: `src/rtb/executor/execution.py:739` file: `src/rtb/executor/execution.py:675` file: `src/rtb/executor/execution.py:765`

因此這條路會把該總曝險核可記成「實際使用」，卻仍持有一般 120 秒壽命的能力憑證。開始一筆的交易提交後才呼叫 DSP；若核可在交易完成與 DSP 驗證憑證之間到期，DSP 仍會接受憑證並寫入。file: `src/rtb/executor/execution.py:432` file: `src/rtb/executor/execution.py:436`

這直接違反既有合約：「用到人工核可的那一筆，憑證不能活得比核可久」。file: `src/rtb/executor/capability_signer.py:136`；圖譜也把它列在事故 F7 的不變量及守衛測試中。file: `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md:24`

新測試只驗證交易內補抓後成功執行、且寫了 `approval_uses`，沒有核對送給 DSP 的憑證到期時間，所以會假綠。file: `tests/executor/test_stale_decision.py:338`

修正方向是在交易內才發現需要核可時不要直接開始嘗試：放掉收據後重跑，讓下一輪鎖外預判取得核可並以其期限重簽；或提供不破壞「開始一筆不在鎖內讀設定檔」規則、但能保證憑證期限不超過核可期限的等價方案。

其餘末輪修正靜態核對已折入：總曝險是否需要核可以交易內已用額度決定；鎖外核可被取代時先放掉重來；過時決策的比例核可在開始前失效時直接擋下；讀不回提案的死信拒絕重放。[S511] 的兩條重跑仍會保留政策已變／決策已過時的新原因。唯讀環境沒有可寫的暫存目錄，pytest 在建立捕捉用暫存檔前即停止，因此本輪未能補跑動態重現。
