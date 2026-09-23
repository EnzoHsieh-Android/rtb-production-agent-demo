severity: clean

### 已看:嘗試轉失敗的所有路徑與結果代碼分流(①)

逐一核對 `src/rtb/domain/attempt.py` 的 `FAILURE_CODES`(VERSION_CONFLICT、VALIDATION_REJECTED、
CAMPAIGN_NOT_FOUND、OTHER_REJECTION、NOT_HAPPENED、MANUAL_FAILURE)與 `execution.py` 裡所有寫
`A.FAILED` 的呼叫點:

- `RESPONSE_TABLE`(第 166 行起):`operation_voided` → `C.NOT_HAPPENED`、`version_conflict` →
  `C.VERSION_CONFLICT`、`validation_rejected` → `C.VALIDATION_REJECTED`。
- `_after_expiry`(491 行):送過多次先作廢再判失敗,或直接 `A.FAILED, code=C.NOT_HAPPENED`。
- `_reconcile_not_found`(639 行):`A.FAILED, code=C.CAMPAIGN_NOT_FOUND`。
- `attempt_store.resolve`(431 行):人工處置判失敗固定 `C.MANUAL_FAILURE`。
- `OTHER_REJECTION` 目前沒有任何 production 呼叫點會產生(只在測試直接呼叫 `attempt_store.
  transition` 用到),但 `block_code_for_failure` 對它一樣落入預設分支,行為正確。

確認 `block_code_for_failure`(`inbox_store.py` 87-91 行)只把 `OutcomeCode.VERSION_CONFLICT`
配「版本已變」,其餘(含上面列的 NOT_HAPPENED / MANUAL_FAILURE / VALIDATION_REJECTED /
CAMPAIGN_NOT_FOUND / OTHER_REJECTION)都落到 else 分支回「同一操作先前已失敗」,跟題目要求一致。

對帳路徑:`reconcile_all` → `_reconcile`(572 行)在 `row.state in TERMINAL_STATES` 時呼叫
`_ack_terminal`(587 行);一般處理路徑寫到終點也是 `_write` → `_ack_terminal`(461 行)。兩者
共用同一個已改過的 `_ack_terminal`(426-436 行),不是獨立第四處。全文搜尋
`OPERATION_PREVIOUSLY_FAILED` 與 `ack_blocked` 呼叫點(`execution.py:381,420,434`、
`inbox_store.py:500`),381 行那處的 `code` 是 `precheck`/`_sign` 回的執行前檢查擋下原因(跟
「先前已失敗」無關),其餘三處全部走 `block_code_for_failure`,沒有第四處寫死舊代碼。

### 已看:收件口重送回應的 block_code 在各種處置下的值(②)

`_accept_in_transaction`(inbox_store.py 383-390 行)的
`CASE WHEN disposition = ? THEN block_code END`(參數是 `Disposition.BLOCKED.value`):

- `pending`(disposition 為 NULL)、`expired`/`superseded`(這兩個是 `state` 欄位值、
  disposition 同樣是 NULL,`coalesce(disposition, state)` 才會回到它們):`NULL = 'blocked'`
  在 SQL 裡不成立(NULL 比較永遠不是 TRUE),CASE 落到隱含 ELSE,回 `NULL` → JSON `null`。
- `in_progress`、`handed_off`、`dead_letter`:disposition 有值但不是 `'blocked'`,同樣回
  `NULL` → `null`。
- `blocked`:回實際 `block_code` 欄位值。

「已擋下但 block_code 欄是空(舊資料)」這個情境本身查不到成立的路徑:`ack_blocked`
(inbox_store.py 568-576 行)是唯一把 `disposition` 寫成 `'blocked'` 的地方,而且跟
`block_code = ?` 在同一條 `UPDATE`(`_finish(receipt, now, "disposition = ?, block_code = ?",
(Disposition.BLOCKED.value, code.value))`)裡原子寫入;`code` 還先經 `type(code) is not
BlockCode` 檢查擋掉非法值。往前查 `git show b4a2077:.../inbox_store.py`(執行迴圈剛接上
收件表的那一版),`block_code` 欄位與這個原子寫法從一開始就在,不存在「先有 disposition=
blocked 欄位、後來才補 block_code 欄」的遷移縫隙,SQLite `CHECK` 也不允許非法值直接寫入。
所以在這個程式碼庫裡沒有「已擋下但 block_code 為空」的實際資料列;若真的靠外部工具手動改
資料庫繞過模組寫入,CASE 表達式一樣會安全回 `null`,不會拋例外或回錯值。

### 已看:回應本文多一個鍵對既有讀者的影響(③)

`src/rtb/analyzer/inbox_client.py` 的 `submit()`(39-46 行)對成功回應(`status in (200,
201)`)完全不解析 body,只用 `status` 建構 `Accepted(replayed=status == 200)`;失敗回應才用
`body.get("error")`。`Accepted`(`flow.py` 74-77 行)本身沒有 `block_code` 欄位。測試替身
(`tests/analyzer/test_flow.py`、`tests/analyzer/test_instrumented.py`)一律直接建構
`flow.Accepted(replayed=...)`,不比對回應本文的鍵集合。`tests/analyzer/test_inbox_client.py`
也只斷言 `result.replayed`。多這個鍵對分析側現有讀者(含測試替身)沒有影響;分析側真要用
`block_code` 是另一份主線變更的事,不在這份 diff 範圍內。

### 已看:回歸測試實跑

在臨時目錄(非 repo 根)複製 `src`/`tests` 跑
`PYTHONPATH=<tmp>/src .venv/bin/python -m pytest -p no:cacheprovider tests/executor/
test_version_conflict.py tests/executor/test_queue.py tests/executor/test_inbox_server.py
tests/executor/test_attempt_store.py`:151 通過、1 個不相關失敗
(`test_the_executor_and_dsp_packages_may_not_import_each_other`,因為臨時目錄沒有複製
`pyproject.toml`,ruff 讀不到設定檔導致的環境問題,跟這份 diff 的邏輯無關,repo 根跑不會有
這個問題)。包含新增的併發重現測試 `test_two_concurrent_writers_reproduce_a_version_conflict`
與 `test_conflict_counts_are_queryable` 都通過。

### 圖譜鏡頭

`Systems/執行迴圈.md` 的 RULE(30 行,`[since:2026-09-23]`,今天確認)與 `Systems/提案收件口.md`
的 RULE(35 行,同日期)、`Systems/外部寫入嘗試紀錄.md`(25 行)三篇都精確描述這份 diff 的行為
(三處共用 `block_code_for_failure`、DSP 版本衝突才寫「版本已變」、其他失敗照舊、
`version_conflict_count` 是唯讀稽核不是長期指標),跟程式碼核對後一致,沒有衝突。唯一要提的是
`執行迴圈.md` 第 50 行(流程步驟列表,沒有日期標記)寫「鍵已存在就依既有那把鍵的狀態分流
(失敗 → 擋下「同一操作先前已失敗」)」,沒提版本衝突的例外——這是同一篇筆記內舊的流程摘要
沒跟著第 30 行的新 RULE 更新,屬於摘要粗略、不算寫錯,不影響本次判準(第 30 行的 RULE 才是
現行依據)。

程式碼裡沒有相依/測試機械反查命中(受影響測試 0、共改 0、呼叫者 0),已用上面三個節點自己查
補上,結論如上。

沒有 blocker 或 major,結案。
