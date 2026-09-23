severity: clean

鏡頭:合約與圖譜一致。立場:三個月後的接手者,只看 `git -C /Users/enzo/rtb-3b diff main..HEAD -- docs/` 改到的三篇筆記(`Systems/提案收件口.md`、`Systems/執行迴圈.md`、`Systems/外部寫入嘗試紀錄.md`)與對應程式(`src/rtb/executor/{execution,inbox_store,attempt_store,inbox_server}.py`)。repo:`/Users/enzo/rtb-3b`,分支 `phase5-exec`(HEAD `f76a8b7`)。

### 已看:① 三篇新增 RULE 逐句對程式

- `Systems/執行迴圈.md` 新增 RULE(版本衝突三處寫擋下原因「版本已變」,共用 `block_code_for_failure`):對照 `src/rtb/executor/execution.py` 兩處(`_take` 撞到既有失敗鍵 → `code = block_code_for_failure(begun.row.code)`;`_ack_terminal` → `block_code_for_failure(row.code)`)與 `src/rtb/executor/inbox_store.py` 的 `_settle_existing`(取件撞到既有失敗鍵,新傳入 `code` 參數)三處,逐字相符;`block_code_for_failure` 本體在 `inbox_store.py`:DSP 回 `OutcomeCode.VERSION_CONFLICT` 才回 `BlockCode.VERSION_CHANGED`,其餘回 `OPERATION_PREVIOUSLY_FAILED`。跟執行前檢查用同一個 `BlockCode.VERSION_CHANGED` 代碼(RULE 講的「同一個代碼」屬實)。
- `Systems/提案收件口.md` 新增 RULE(重送回應多帶 `block_code`,只有已擋下有值,其餘為 null 但鍵存在):對照 `inbox_store.py` `_accept_in_transaction` 的重送分支,SQL 用 `CASE WHEN disposition = ? THEN block_code END` 確保只有 `disposition = 'blocked'` 時才帶值,其餘回 `NULL`;dataclass `Accepted.block_code: str | None = None` 預設 `None`(首次收下走的是無此欄的建構呼叫,同樣是 `None`);`inbox_server.py` 的 `_accepted_body` 一律帶 `"block_code": result.block_code` 這把鍵(不省略)。跟 RULE 描述一致。
- `Systems/外部寫入嘗試紀錄.md` 新增 RULE(`version_conflict_count(tx, campaign_id=None)` 唯讀查詢,依 code 篩沒有索引):對照 `attempt_store.py` 新函式,SQL `SELECT COUNT(*) FROM attempts WHERE code = ?{where}`;`attempts` 表只有 `attempts_first_rows`、`attempts_terminal_rows` 兩個索引(都建在 `campaign_id`),沒有 `code` 欄的索引,跟 RULE 講的「沒有索引,是事後查帳用」相符。

三句 RULE 都逐字對得上程式行為,沒有找到語意落差。

### 已看:② 既有 ★INVARIANT★ 與其 kill_recipes 是否仍逐字存在、仍有殺傷力

- 用 `python3` 解析三篇筆記 frontmatter 的 `kill_recipes`(提案收件口 2 條、執行迴圈 9 條),逐條核對每條 `old` 字串在改動後的 `src/rtb/executor/{inbox_store,execution}.py`、`src/rtb/analyzer/task_store.py` 裡是否逐字存在:**11 條全部存在**,這份 diff 沒有動到任何一條配方鎖定的那幾行。
- 進一步用 `scripts/lumos guard kill`(worktree 隔離、baseline 綠、套壞法、跑綁定測試)在改動後的 HEAD(`f76a8b7`)上實跑:
  - `lumos guard kill 提案收件口` → 2 配方全數 `killed`(同一修訂只收一份、修訂必須連號)。
  - `lumos guard kill 執行迴圈` → 9 配方全數 `killed`(F1 兩條、F2 四條、F3 三條)。
  - 工具警告「python repo 有未提交變更——不會進沙盒」,查過那個未提交變更只有 `docs/.governance-log.jsonl`(治理帳,非 python 檔),不影響 kill 以 HEAD 為基準的結果。
- 另外實跑 `pytest tests/executor tests/domain`(621 個)與新測試檔 `tests/executor/test_version_conflict.py` 全綠;新增的 5 支測試(`test_a_dsp_version_conflict_is_acknowledged_as_version_changed`、`test_other_failures_are_still_acknowledged_as_previously_failed`、`test_a_resend_reports_why_the_proposal_was_blocked`、`test_two_concurrent_writers_reproduce_a_version_conflict`、`test_conflict_counts_are_queryable`)都能被 pytest 收集且通過,跟三篇 RULE 的 `[test:]` 標記一一對得上。

既有 INVARIANT 與其配方在改動後仍逐字存在、仍有殺傷力,沒有發現迴歸。

### 已看:③ 預告合約 Verification/事故F4_舊版本寫入被拒不覆蓋.md

- 讀了該筆記:摘要仍寫「把觀察到的版本一路帶進執行寫入、衝突後重新規劃屬 Phase 5,還沒開工」;`RTB_Phase5樂觀鎖與重新規劃_計劃.md` 裡明講核對後前半句不準(版本已經一路帶進執行寫入,見 Phase3 的 `test_f4_a_stale_proposal_never_overwrites_a_newer_value`),並寫「開工時照程式訂正那句摘要」。
- 這份 diff(`git diff main..HEAD -- docs/`)沒有動到 `Verification/事故F4_舊版本寫入被拒不覆蓋.md`,而這個提交確實是 Phase 5 執行側的「開工」(commit message:「DSP 端的版本衝突回報成版本已變,收件口重送時帶擋下原因」)。
- 判斷:這句摘要要不要在這個提交一起訂正是編排取捨,不是程式或合約的錯——訂正只是把一句已經過時的敘述改對,不依賴這份 diff 新加的任何行為,而且 F4 端到端合約本身(`[S308]`)還沒轉正、要等接續任務那一段(分析側,主線在做)完成才會 settle。留給主線在 F4 端到端轉正時一併訂正也說得通,不算這份 diff 的缺陷。列在下面「⚠ 交編排者」供裁量,不算 finding。

### 已看:④ 計劃的合約句 S300、S310、S309、S315(執行側)跟實作是否一致

- `[S300]` 處置已擋下才帶擋下原因,其餘不帶:`inbox_store.py` 的 `CASE WHEN` 與 `Accepted` 預設值符合;`test_a_resend_reports_why_the_proposal_was_blocked` 驗到「鍵要存在,沒擋下就是空值」與擋下後帶 `"version_changed"`。一致。
- `[S310]` 三處寫死改成依代碼分流:核對過三個呼叫點(見①),測試 `test_a_dsp_version_conflict_is_acknowledged_as_version_changed` 明確分三段各測一處(終點確認、開始撞鍵、取件撞鍵)。一致。
- `[S309]` 兩套獨立執行系統同時改同一廣告、DSP 只認預期版本相符的一方:`test_two_concurrent_writers_reproduce_a_version_conflict` 起真的 `DspServer`(非替身)、兩個獨立 `InboxStore`+`Executor`,用 `threading.Barrier` 逼真正並行寫,斷言只有一方 `verified`、另一方 `attempts.code == version_conflict` 且收件口 `block_code == version_changed`,DSP 側只留一筆 `update_budget`、`version == 2`。一致,而且是這份 diff 裡對「可以真的重現」講得最實的一支測試。
- `[S315]`(執行側那一半)唯讀查詢回報正確的 DSP 版本衝突次數、可依廣告篩:`version_conflict_count` 實作與 `test_conflict_counts_are_queryable` 核對過(3 筆版本衝突、依 `c1`/`c2`/`c9` 篩出 2/1/0,另一筆 403 失敗不算入)。一致;計劃裡 S315 分析端那一半(重新規劃/用完/過保留期次數)確實沒在這份 diff,但計劃與交辦說明都明講分析側在主線另外做,不算這份 diff 的缺漏。

四條合約句跟實作行為一致,沒有找到落差。

⚠ 交編排者:`Verification/事故F4_舊版本寫入被拒不覆蓋.md` 的摘要前半句「還沒把觀察到的版本帶進執行寫入」已經過時(計劃自己也這樣講),這份 diff 沒有訂正它——訂正成本低、不影響任何合約或測試,建議在這個提交或下一個緊接的提交裡補一行 `WHY:` 或直接改摘要句,而不是拖到 F4 端到端(`S308`)全部轉正才動。這件事沒有翻紅任何測試,只是文件债,不列進 finding。

沒有 blocker/major/minor finding,共 0 條 blocking。
