severity: minor
以下為完整報告全文(已讀格式規則,照抄輸出):

---

severity: minor

本輪鏡頭:第 3 輪修正驗收(`governance/review-reports/code-phase9-inc1/r3-delta.patch`,`a2d808e..HEAD`,對應 `r2-intake.md` 折入的 3 件事:待寫清單背壓/忙碌計數/收尾印列、內容雜湊改成呼叫當下存欄位、維運套件禁動態執行)。全套 `.venv/bin/python -m pytest` 在 `/Users/enzo/rtb-p9i1` 跑過,1604 項全過;未在 `/tmp` 做變異實驗(逐條讀碼加追蹤全部呼叫點與離開路徑即可判定,不需要另外造壞境),repo 全程只讀,無任何改動。

## 3 件折入項逐條驗收(讀碼,不只看測試轉綠)

### 1. 待寫清單要有上限、忙碌算進停機計數、收尾不無聲丟
結論:已修。`MAX_PENDING_CALLS = 50`(`file: src/rtb/executor/execution.py:420`)搭配新的 `_backlogged()`(`file: src/rtb/executor/execution.py:477-479`),`process_one()` 到上限就回 `Result.DEFERRED` 不開始新工作;`reconcile_all()` 每把鍵開始前也檢查上限,到了就 `break`(`file: src/rtb/executor/execution.py:1064-1066`)。逐一追蹤過 `execution.py` 全部 10 個 `self.dsp.*` 呼叫點,全都落在 `process_one` 的 `try/finally: self.flush_calls()` 或 `reconcile_all` 每把鍵的 `try/finally: self.flush_calls()` 之內——Python 的 `finally` 不論是正常返回、`LeaseLost` 被接住、還是 `ExecutorHalted`/`CorruptedInboxRow` 沒被接住直接往外炸,都會先跑完再往外傳,所以「例外、`LeaseLost`、停機、對帳中途被打斷」這幾種離開路徑都補得到。`runner._loop` 新增 `behind = not executor.flush_calls()`,只要補寫持續撞忙,`behind` 每輪都是 `True`,累加同一個 `busy_streak` 計數器,滿 `BUSY_LIMIT` 就 `EXIT_BUSY` 停機——驗過到上限後行程不會「既不做事也不退出」:`process_one()` 回 `DEFERRED` 不做事的同一輪,`behind` 照樣為真,忙碌計數照樣累積,不是只在「有做事」的輪次才算。`_serve` 的 `finally` 再補寫一次,失敗才印 `少記 N 列` 與冪等鍵到 stderr。
引句:「behind = not executor.flush_calls()」
引句:「少記 {len(keys)} 列 DSP 呼叫紀錄(結束時資料庫仍忙):」

### 2. DSP 呼叫紀錄雜湊改成呼叫當下存欄位,不再靠冪等鍵回推
結論:已修。`CallSubject` 新增 `content_hash` 欄位,`_calls()` 在建回呼當下就算好 `content_hash(proposal)` 綁進 `subject`(`file: src/rtb/executor/execution.py:451-452`);`record_dsp_call` 直接把它寫進 `dsp_calls.content_hash` 新欄(`file: src/rtb/executor/attempt_store.py:114-120`)。`ops/trace.py` 的 `_call_segment` 已整段拿掉原本用 `(task_id, revision, key)` 三元組回推雜湊的 `hashes` 字典,改成直接讀 `call.content_hash`,`None` 才標 `UNKNOWN`(`file: src/rtb/ops/trace.py:279-288`)。第 2 輪資安席指出的「同把冪等鍵、不同內容雜湊的兩份提案,字典推導式會用後寫的靜默覆蓋前一份」這條路徑已經整個消失,不是修修補補。新測試 `test_calls_for_two_proposals_sharing_a_key_keep_their_own_content_hash` 直接構造「同鍵不同雜湊」的兩份提案驗證每一列各自保留自己的雜湊。
引句:「呼叫紀錄的關聯欄位:這次呼叫是為了哪一份提案。內容雜湊在呼叫當下記下:冪等鍵不含決策時間與」

### 3. dsp_calls 補內容雜湊欄對既有資料庫(含本分支早先建的庫)的影響
結論:已修,且路徑審過。`DSP_CALL_ADDED_COLUMNS = (("content_hash", "content_hash TEXT"),)` 被登記進 `inbox_store._ADDED_COLUMNS["dsp_calls"]`(`file: src/rtb/executor/inbox_store.py:273-274`),沿用既有 `attempts` 表補欄位的同一套機制(`_migrate_columns` 在拿到立即寫入鎖後補 `ALTER TABLE`,見 `file: src/rtb/executor/inbox_store.py:808-825`),舊列該欄自動是 `NULL`。寫入端(`InboxStore`)開庫會補;唯讀端(`ReadOnlyInbox`)不補表,`_REQUIRED_SCHEMA["dsp_calls"]` 已含 `content_hash`,缺了就丟 `DatabaseNotUpgraded`、不會拿舊 schema 硬讀(`file: src/rtb/executor/inbox_store.py:1499-1507`),不會有半升級狀態被誤讀。新測試 `test_a_call_record_table_opened_earlier_on_this_branch_gains_the_content_hash_column` 明確用「先開一個庫、手動 `ALTER TABLE ... DROP COLUMN content_hash` 模擬本分支早先建的舊庫、再重開」的方式重現,驗證重開後自動補欄且照常寫入雜湊。
引句:「呼叫紀錄表在同一個增量裡後補的欄位(代碼審第 2 輪):這個分支早先開過的資料庫開啟時補上」

## 附:維運套件禁動態執行(tests-1,已修,非本輪火力重點)
`DYNAMIC_LOOKUPS` 加了 `eval`/`exec`/`compile`/`__import__`/`importlib`/`import_module`,`_dynamic_lookups` 對 `ast.ImportFrom` 也用 `(node.module or "").split(".")[0]` 抓子模組匯入(如 `from importlib.util import find_spec`)。逐一核對新增的 8 個殺傷力測資,`import importlib.util as _iu`、`from importlib.util import find_spec as _fs` 這類只帶子模組名字的繞法也被 `ast.alias`/`ast.ImportFrom` 分支接住。已修。
引句:「用字串動態取屬性、用字串執行程式、動態匯入(代碼審第 1、2 輪):名字掃描看不到字串裡的方法名」

## arch-1(已駁回)
r3-delta.patch 沒有再改 `DspPort`/`execution.py` 的 `on_call` 簽章形狀,確認第 2 輪辯方 Codex 的駁回判定沒有被繞過或事後又悄悄改回去,不重驗。

## 新引入的回歸(blocking,鏡頭指定的 5 個切面逐一查)
無。待寫清單的例外/`LeaseLost`/停機/到上限/對帳中途五種離開路徑、到上限後的忙碌計數累積、收尾印列條件、dsp_calls 雜湊欄對既有庫的相容、追蹤對空值標「不明」——五處都逐一讀碼追過呼叫鏈與離開路徑,沒有找到會做出錯行為、破壞合約或資料損壞的新洞。

---

### 1. `process_one()` 與 `runner._loop` 同一輪對 `flush_calls()` 重複呼叫
severity: minor
blocking: 否

引句:「return not self.flush_calls() and len(self._pending) >= MAX_PENDING_CALLS」
file: `src/rtb/executor/runner.py:69`

觸發情境:待寫清單持續撞忙碌(不論有沒有到上限)。

會出什麼錯的行為:不是錯的行為,是多餘的資料庫嘗試。`process_one()` 一開始先呼叫 `_backlogged()`,內部已經呼叫過一次 `self.flush_calls()`(`file: src/rtb/executor/execution.py:477-479`);同一輪 `runner._loop` 緊接著又呼叫一次 `executor.flush_calls()` 算 `behind`(`file: src/rtb/executor/runner.py:69`)——兩次呼叫在持續忙碌時都會各開一個短交易去撞鎖再失敗一次,對已經在搶鎖的資料庫是不必要的額外負擔,但不影響正確性(`_pending` 的增減與最終是否記到都不受影響)。

建議修法:讓 `process_one()`(或 `_backlogged()`)回傳這一輪是否已經幫忙補寫過,`_loop` 只在沒補寫過的情況(例如提早 `return Processed(IDLE)` 那條路)才自己再呼叫一次 `flush_calls()`,省掉忙碌時的重複嘗試。
