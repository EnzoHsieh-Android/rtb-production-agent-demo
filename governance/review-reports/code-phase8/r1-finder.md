severity: major

## F1 重放用的是取得寫鎖前的時間，過期提案仍會被放回

severity: major

blocking: 是 — 違反 [S502] 的未過期條件，並產生錯誤的「已放回」稽核紀錄。

引句:「outcome = store.replay(args.task_id, args.revision, args.operator, clock())」

管理工具在進入 `immediate_transaction` 前便呼叫時鐘。若另一個寫入者持鎖直到提案過期，重放取得鎖後仍用過期前的舊時間判斷，於是把實際已過期的死信放回並記成 `replay_requeued`。執行迴圈後續可能再次擋下，但重放結果與操作稽核已經錯誤。應把時鐘函式傳進 `replay`，取得寫鎖後才讀時間。

file: `src/rtb/executor/replay.py:48`

file: `src/rtb/executor/inbox_store.py:721`

## F2 總列數已滿時錯拒不會新增列的重放

severity: major

blocking: 是 — 合法且仍有效的死信會無法重放，破壞 [S502]。

引句:「self._check_capacity(supersedes=False)」

重放只是把既有 `proposals` 列由死信改回待處理，不會新增該表列；但共用容量檢查同時要求 `total < MAX_ROWS`。因此只要歷史／結案列令總數到達 5000，即使待處理數為零，重放仍回 `INBOX_FULL`。此處只應檢查待處理名額，不應套用接收新提案所需的總列數限制。

file: `src/rtb/executor/inbox_store.py:580`

## F3 升級前的死信沒有信封，卻仍可成功重放

severity: major

blocking: 是 — 重放後無法以信封接到嘗試紀錄，破壞耐久追蹤與 [S506]。

引句:「重放對這份提案最新那一列信封;從沒進過死信的,稽核的信封欄是空值。」

舊資料庫升級時只建立空的 `dead_letters` 表，原有死信列不會補信封。對這類真實死信呼叫重放時，`SELECT max(id)` 得到 `NULL`，但拒絕條件沒有檢查信封存在，仍會放回並將兩筆操作稽核的 `envelope` 寫成空值。待收件列日後清除，便無法由稽核接到信封的冪等鍵與新嘗試。應在升級時補信封，或至少拒絕沒有信封的既有死信。

file: `src/rtb/executor/inbox_store.py:727`

file: `src/rtb/executor/inbox_store.py:732`

## F4 重跑交易只檢查曾用過核可，沒有確認核可此刻仍有效

severity: major

blocking: 是 — 已過期核可仍可豁免決策新鮮度，直接違反 [S512]。

引句:「and not self.store.used_approvals(tx, proposal))」

`_resign` 在交易外驗核可；真正轉回嘗試中的交易只查是否存在歷史 `approval_uses`。核可若在重簽後、等待寫鎖期間到期，`_stale_on_rerun` 仍會豁免新鮮度，而 `_superseded` 只核對最新核可編號，也不核對到期時間。結果是過時決策被轉回嘗試中並呼叫 DSP；即使 DSP 最後因憑證過期拒絕，也已違反「沒有有效核可就擋成決策已過時」及不再送出的要求。最終交易必須以該交易的時間重新驗證實際用過的核可。

file: `src/rtb/executor/execution.py:474`

file: `src/rtb/executor/execution.py:641`
