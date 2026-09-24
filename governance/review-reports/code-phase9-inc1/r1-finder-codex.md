severity: major

### 1. 同一工作者續作也被永久記成租約過期接手
severity: major
blocking: 是
引句:「self._log(now, message.task_id, message.revision, LifecycleKind.RECLAIMED,」
file: `src/rtb/executor/execution.py:1044`
file: `src/rtb/executor/inbox_store.py:1435`

觸發情境：未結案嘗試仍由同一個 `owner` 持有，租約尚未到期，下一輪對帳再次呼叫 `take_over()`。更新條件因 `lease_owner = owner` 成立，之後無條件寫入 `RECLAIMED`。

錯誤行為：沒有租約過期、也沒有其他工作者接手，卻永久留下「租約過期被接手」事件；未結案持續數輪時還會重複寫，污染重投率、接手次數與生命週期時間線。

建議修法：在更新前或以 `RETURNING` 原子取得舊的 `lease_until`／`lease_owner`，只有舊租約確實已到期時才寫 `RECLAIMED`。同一 owner 在未到期租約上的續作應走續租或直接沿用，不記事件。

### 2. 任務編號重用時追蹤會吞掉其中一份提案
severity: major
blocking: 是
引句:「found.setdefault((event.task_id, event.revision),」
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:228`

觸發情境：收件表清除舊任務後，相同任務編號與修訂號收到內容不同的新提案；兩份提案有不同內容雜湊及冪等鍵。

錯誤行為：`_revision_keys()` 只用 `(task_id, revision)` 當字典鍵，`setdefault` 永遠保留第一把冪等鍵。後一份提案的嘗試紀錄與 DSP 操作不會被讀取，追蹤會輸出不完整甚至錯誤歸屬的事故時間線。這也直接違反規格要求用內容雜湊區分重用後的兩份內容。

建議修法：整條追蹤關聯改用 `(task_id, revision, content_hash)`；`RevisionKey`、分析端鍵清單、事件回退鍵與嘗試歸屬都必須保留內容雜湊。不可在取得嘗試紀錄前先以任務與修訂去重。

### 3. 有 HTTP 500 的畸形回應會被記成沒有狀態碼的 unreadable
severity: major
blocking: 是
引句:「except (OSError, ValueError) as exc:  # 逾時、斷線、回應不是 JSON 或太大」
file: `src/rtb/httpclient.py:68`
file: `src/rtb/httpclient.py:70`

觸發情境：DSP 或中間代理實際回覆 HTTP 4xx／5xx，但本文不是合法 JSON或超過大小上限。`request_json()` 在已取得 HTTP 狀態後解析本文並丟出 `ValueError`。

錯誤行為：`DspClient._send()` 把這條路徑當成沒有回應，紀錄 `status = NULL`、結果為 `unreadable`，而不是 `client_error`／`server_error`。後續以呼叫紀錄計算的 4xx、5xx 比率因此少算，事故切片也會錯誤分類。

建議修法：讓 HTTP 層在本文解析失敗時仍攜帶已取得的狀態碼，或把「取得狀態」與「解析本文」拆開；先依狀態碼分類 4xx／5xx，只有 2xx 本文或欄位無法解析時才記 `unreadable`。

### 4. 呼叫紀錄遇到資料庫忙碌會永久漏帳
severity: major
blocking: 是
引句:「self._on_call(DspCall(kind, result, status, (time.monotonic() - started) * 1000,」
file: `src/rtb/executor/runner.py:69`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:230`

觸發情境：DSP 已回應後，回呼用獨立短交易寫 `dsp_calls`，但另一個工作者持有 SQLite 寫入鎖超過逾時。

錯誤行為：回呼丟出的 `InboxBusy` 取代原本的 DSP 結果；執行迴圈把它當一般鎖競爭休息後繼續，該次呼叫資料已經丟失。若是寫入呼叫，DSP 可能已提交，而本地呼叫帳永久沒有這列；規格只接受「行程在回應後、記錄前當機」的不可避免缺口，不包含可恢復的正常鎖競爭。

建議修法：保留這次已完成的 `DspCall` 並針對紀錄交易重試；重試耗盡時不可走一般 `InboxBusy` 分支靜默略過，至少應停機並明確告警。若要機械保證不因正常鎖競爭漏帳，可在外呼前先耐久寫入呼叫意圖，再以另一列記完成結果。