severity: major
# 資安審查報告 — code-phase9-inc1 第 2 輪

severity: major

本輪審查材料為 `governance/review-reports/code-phase9-inc1/r2-snapshot.patch`(增量 1 含第 1 輪修正,基準 `b81f56b..HEAD`),重點針對第 1 輪修正帶進的新面:執行器記憶體待寫清單、HTTP 本文解析失敗新例外、追蹤檢視用冪等鍵接回內容雜湊。找到一個可被攻擊者影響的輸入(提案內容裡的 `task_id`/`revision`/`risk_summary`/`reason_codes`/`evidence_refs`/`policy_version`/決策時間)觸發的追蹤稽核歸屬混淆問題,列為下方發現。其餘兩個重點面向查無可利用漏洞,說明如下:

- **執行器記憶體待寫清單**(`src/rtb/executor/execution.py` 的 `Executor._pending`/`flush_calls`):DSP 用戶端每次 HTTP 呼叫在 `_send` 裡固定觸發剛好一次 `on_call`(讀了全部出口:逾時/斷線、`UnreadableResponse`、`DspUnavailable`、成功,每條路徑都只呼叫一次 `report`),不會因為 DSP 回應內容或延遲被放大成多次呼叫;單輪呼叫量上限由既有常數(`MAX_UNRESOLVED=20`、`MAX_SENDS=3`)卡住,DSP 端無法讓單輪呼叫數無界增加。清單只有在 `flush_calls` 自己的短交易撞到 `InboxBusy` 時才會累積不清空,但這個忙碌狀態是本機 SQLite 寫入鎖競爭(多個執行迴圈工作者互搶),不是 DSP 回應內容能直接操控的外部輸入;且 `runner.py` 的 `_loop` 對 `process_one`/`reconcile_all` 自己的交易撞忙碌會計入 `busy_streak`,連續 `BUSY_LIMIT=3` 輪忙碌就乾淨停機(`EXIT_BUSY`),行程不會無限跑下去持續囤積。未發現「提案內容、DSP 回應、命令列參數」這幾類不可信輸入能讓此清單無界成長耗盡記憶體的路徑。
- **HTTP 本文解析失敗新例外**(`src/rtb/httpclient.py` 的 `UnreadableResponse`、`src/rtb/executor/dsp_client.py` 的 `_send`):`UnreadableResponse(status, message)` 的 `message` 來自 `json.loads` 拋出的 `ValueError`(`JSONDecodeError`/`UnicodeDecodeError`),Python 這兩種例外的字串只帶行列位置或單一位元組值,不會夾帶回應本文全文;`_send` 捕到 `UnreadableResponse` 後只取用 `exc.status`(整數),把 `reply` 顯式傳 `None` 給 `report`/`dsp_error_code`,不會把讀不懂的本文原樣塞進 `DspCall` 或往外傳的例外裡;呼叫端(`_get`/`write`/`void` 的 `no_reply`)也都不重新拋出帶訊息內容的例外或印出。能力憑證(`X-Capability`)與冪等鍵是送出時的請求標頭,不在回應解析路徑上,不會被這支例外夾帶。未發現機密外洩路徑。

---

### 1. 追蹤檢視把 DSP 呼叫紀錄的內容雜湊接回錯提案,冪等鍵重用時會靜默歸屬混淆

severity: major
blocking: 是

引句:「hashes = {(task, revision, key): digest for (task, revision, digest), (key, _) in keys.items()}」

觸發情境:`task_id`、`revision` 只驗格式(`ID_PATTERN`/`_positive_int`),不驗跨時間唯一性(`src/rtb/domain/proposal.py` 的 `CHECKS`),分析端提案內容整體屬於不可信輸入。`operation_key`(冪等鍵)只取 `task_id`、`campaign_id`、`action_type`、`requested_change`、`campaign_version_observed` 五樣算雜湊;`content_hash`(追蹤消歧義用)則多算了 `revision`、`reason_codes`、`evidence_refs`、`decision_created_at`、`decision_expires_at`、`policy_version`、`risk_summary`。只要兩份提案的 `task_id`+`revision` 相同、`campaign_id`/`action_type`/`requested_change`/`campaign_version_observed` 也相同,但 `risk_summary`(或 `reason_codes`/`evidence_refs`/`policy_version`/決策時間)不同——這在 `task_id` 被重用(第一份結案、保留期過後同一把鍵被第二份不相干提案接手,本輪修正本身承認的場景,`src/rtb/ops/trace.py` 的 `RevisionKey` docstring:「任務編號重用、內容不同的第二份會被吞掉」)時完全可能發生,甚至不需要惡意攻擊,只要分析 LLM 對同一任務重跑產生略有出入的 `risk_summary` 文字就會踩到——兩者的 `operation_key` 會相同,但 `content_hash` 不同。

會出什麼錯的行為:`_read_executor` 的 `hashes` 字典鍵是 `(task_id, revision, operation_key)` 三元組,不含 `content_hash`。當上述碰撞發生,`keys` 裡兩筆不同 `Ident=(task,revision,content_hash)` 的項目在轉成 `hashes` 字典時會落到同一把鍵,字典推導式會用後處理到的那筆靜默覆蓋前一筆(Python dict comprehension 語意)。結果是 `_call_segment` 在 `ops/trace.py` 追蹤檢視輸出的 `Table.DSP_CALLS` 段落裡,兩份不同提案共享同一把冪等鍵時,其中一份真正發生過的 DSP 呼叫紀錄會被顯示成另一份提案的 `content_hash`——而且沒有任何錯誤、警示或缺值標記(對照同一支檔案裡 `RevisionKey.settled_by_existing_key` 對「鍵被既有結果確認」有明確布林旗標,這裡完全沒有對應的碰撞偵測)。這正好抵銷了第 1 輪加 `content_hash` 消歧義、要解決「任務編號重用時嘗試紀錄歸屬」的目的——稽核人員(判斷某筆可疑 DSP 寫入到底出自哪份提案內容)看到的追蹤結果可能是錯的一份。

file: `src/rtb/ops/trace.py:348`(`hashes` 字典建構)、`src/rtb/ops/trace.py:277-283`(`_call_segment` 用 `(task_id, revision, key)` 查 `hashes`,`ident = (call.task_id or "", call.revision or 0, call.key or "")`)

補充佐證:本輪同一份 patch 新增的回歸測試 `test_a_reused_task_and_revision_with_new_content_is_traced_separately`(`governance/review-reports/code-phase9-inc1/r2-delta-tests.patch:975-1039`)確實測了「任務與修訂重用」,但第二份提案刻意帶了不同的 `campaign_id="c2"`,導致兩份提案的 `operation_key` 本來就不同,並未涵蓋「`operation_key` 相同、只有 `content_hash` 不同」這個真正會撞 `hashes` 字典鍵的情境,所以現有測試沒能抓到這個洞。

建議修法:`hashes` 改用完整的 `Ident (task_id, revision, content_hash)` 當鍵、或直接讓 `dsp_calls` 表在寫入時就存下 `content_hash`(不要靠 `operation_key` 回推),兩者任一都能避免碰撞;若仍要靠 `(task_id, revision, key)` 回推,至少要在偵測到同一把鍵對應多個不同 `content_hash` 時標記成缺值或警示(比照 `settled_by_existing_key` 的作法),不能靜默覆蓋。

---

Files inspected(除 `r2-snapshot.patch`/`r2-delta-src.patch`/`r2-delta-tests.patch` 全文外,交叉核對的實際原始碼):`/Users/enzo/rtb-p9i1/src/rtb/ops/trace.py`、`/Users/enzo/rtb-p9i1/src/rtb/executor/execution.py`、`/Users/enzo/rtb-p9i1/src/rtb/executor/runner.py`、`/Users/enzo/rtb-p9i1/src/rtb/executor/dsp_client.py`、`/Users/enzo/rtb-p9i1/src/rtb/httpclient.py`、`/Users/enzo/rtb-p9i1/src/rtb/executor/attempt_store.py`、`/Users/enzo/rtb-p9i1/src/rtb/executor/inbox_store.py`、`/Users/enzo/rtb-p9i1/src/rtb/domain/proposal.py`、`/Users/enzo/rtb-p9i1/src/rtb/domain/attempt.py`。
