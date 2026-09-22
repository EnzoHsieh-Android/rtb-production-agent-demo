severity: major

# 資安審查:code-analyzer-flow r1(task_store.py / flow.py)

範圍:只看歷史表本身與重跑到手的資料(EvidenceSource/Decide/Submit 在這個增量還是測試假物件,不看網路面)。

### 1. `evidence_for()` 沒有錯誤處理,一旦壞掉任務永久卡死、不會轉 FAILED

severity: major
blocking: 是 這是對「advance() 在中斷後恢復永遠安全」這條文件明講的核心保證的直接反例:一旦踩到,不是進 FAILED 終點,而是每次呼叫 `advance()` 都用同一列壞資料重炸同一個例外,insert-only 的歷史表又沒有 UPDATE/DELETE,這個任務編號永遠無法再往前走。
引句:「evidence = store.evidence_for(row.task_id, row.seq)」

`flow.py` 的 `_from_analyzing` 只把 `c.decide(row, evidence)` 包在 try/except 裡(轉成 FAILED),往前一行的 `store.evidence_for(row.task_id, row.seq)` 完全沒有保護。`evidence_for()` 會用資料庫裡的原始值直接建構 `Evidence(...)`,而 `Evidence.__post_init__`(`src/rtb/domain/evidence.py:58-71`)會用 `is_id()` 驗證 `task_id`、`evidence_id`,格式不合法就丟 `ValueError`。

問題是:`TaskStore.create_task(task_id, campaign_id, now)` 本身完全沒有驗證 `task_id` 的格式或長度(引句見 finding 3),而其他所有識別碼(`Proposal.task_id`、`Evidence.task_id`/`evidence_id`)都套用同一份 `ID_PATTERN`(`src/rtb/domain/_checks.py:11`)。只要呼叫端用一個不符合 `ID_PATTERN` 的 `task_id`(空字串、超過 128 字、帶了 pattern 外的字元)呼叫 `create_task`,這一列就會成功寫進 `tasks` 表(`RECEIVED` 狀態),流程可以順利跑到 `COLLECTING_EVIDENCE`→寫入 `evidence` 表(`commit_step` 對 evidence 列的 `task_id` 一樣沒有驗證,是直接塞進 SQL 參數,不會在寫入時失敗),但只要一進到 `ANALYZING`、呼叫 `evidence_for()` 要把這些證據讀回 `Evidence` 物件時,`is_id(self.task_id)` 就會失敗、丟出未被攔截的 `ValueError`,而且**每一次**重跑 `advance()` 都會在同一個地方炸開,不會被歸類為任何一種預期內錯誤(不是 FAILED、不是可重試的 None)。

這不需要惡意第三方,呼叫端(未來增量 4 的收件口)只要有一次疏忽用了不合規的 `task_id` 呼叫 `create_task`,就永久卡死那個任務;若上游收件邏輯本身就不做嚴格白名單驗證(目前程式庫裡完全找不到會呼叫 `create_task` 的收件口程式碼可以佐證有做),這條路徑是現成的。

建議:`create_task` 進場時就用 `is_id`/`ID_PATTERN` 驗證 `task_id`、`campaign_id`(不合法直接拒絕,不寫入),或至少在 `_from_analyzing` 把 `store.evidence_for(...)` 也納入例外處理、明確轉成 FAILED 而不是讓例外逃出 `advance()`。

### 2. 從歷史表讀回 `Proposal`/`TaskRow` 沒有 domain 層那種「安全邊界」兜底,一列毀損就永久讀不回

severity: major
blocking: 是 後果與 finding 1 相同等級(任務永久卡死、無法恢復),而觸發面更廣——不只是格式不合法的 task_id,任何一種欄位型別錯置或 JSON 損毀都會命中。
引句:「data = json.loads(raw)」

`_proposal_from_json(raw)` 直接 `json.loads(raw)` 再用 `data["..."]` 逐一取值建構 `Proposal(...)`,中間至少三種操作可能在 `Proposal.__post_init__` 自我驗證(它本身其實很紮實,見下方「查證」)有機會介入之前就丟出未被攔截的例外:
- `ActionType(data["action_type"])` 值不在列舉裡 → `ValueError`
- `datetime.fromisoformat(data["decision_created_at"])`(以及 `decision_expires_at`)若不是字串 → `TypeError`;字串格式不對 → `ValueError`
- 缺任何一個鍵 → `KeyError`

這些例外都不是 `Proposal.__post_init__` 丟出來的(它有自己一層 `try: raw = self.to_primitives() ... except (AttributeError, TypeError, ValueError): raw = None` 的保底,參見 file: `src/rtb/domain/proposal.py:67-70`),而是在**呼叫 `Proposal(...)` 之前**,組引數時就先炸了,直接從 `_proposal_from_json` 一路逃到 `_row_from_record`→`latest()`/`evidence_for()` 呼叫端,`flow.advance()` 完全沒有攔截。對照 `rtb.domain.proposal.parse_proposal()` 明確寫了「這是安全邊界,所以再加一層保底,不管什麼例外都不讓它逃出去」(file: `src/rtb/domain/proposal.py:269-277`,尤其 276 行的 `except Exception as exc`),`task_store.py` 讀回同一種「不可信出處」的 JSON 卻沒有做對等的防禦。

由於 `proposal_json` 在正常寫入路徑上永遠來自一個已通過驗證的 `Proposal`(`_proposal_to_json` 只在 `commit_step` 裡被呼叫,資料源頭是 `decide()` 回傳的 `ProposalDecision.proposal`),這一列理論上不會壞——但這正是題目要問的「歷史表本身有沒有洞」:`TaskStore` 開的是一個一般的 SQLite 檔案(`src/rtb/analyzer/task_store.py` 的 `connect(path, ...)`),沒有任何執行期機制阻止別的行程直接用 `sqlite3.connect()` 打開同一個檔案、用原生 SQL 寫入一列格式不對的 `proposal_json`。新增的 `ruff.toml`(`extend`/`banned-api`)只擋 Python 匯入層級的靜態依賴,不是資料庫層級的存取控制或程序隔離;`dsp`、`executor` 若拿到（或誤用/寫死）同一個 db 路徑,一次寫入格式不對的 `proposal_json` 或 `state`(`TaskState(state)` 同樣沒有保底,見 `src/rtb/analyzer/task_store.py` 的 `_row_from_record`)就能讓 analyzer 對那個任務的所有後續 `advance()` 呼叫永久失敗。

建議:`_row_from_record`/`_proposal_from_json` 比照 `parse_proposal()` 加一層 `try/except Exception`,把反序列化失敗轉成明確、可分類的錯誤(例如視同資料毀損、轉 FAILED 或至少不讓例外未分類地逃出 `TaskStore`),而不是讓呼叫端毫無防備地接住任意例外型別。

### 3. `task_id`/`campaign_id` 在 `TaskStore` 這一層完全沒有格式或長度限制

severity: minor
blocking: 否 目前沒有直接可利用的注入路徑(SQL 全走參數化,見 finding 5),只是把「格式驗證」這道防線在 TaskStore 這一層整個拿掉,實際傷害要靠 finding 1 的機制才會發作。
引句:「def create_task(self, task_id: str, campaign_id: str, now: datetime) -> None:」

`create_task` 對 `task_id`、`campaign_id` 沒有任何格式、長度或字元集檢查(不像 `Proposal`/`Evidence` 都套 `ID_PATTERN`),`TaskRow` 這個 dataclass 也沒有 `__post_init__` 自我驗證。額外的小問題是 `TaskAlreadyExists` 的訊息直接用未經驗證的值組字串:

`f"{task_id} 已存在,廣告編號是 {existing[0]},不是 {campaign_id}")`

若 `task_id`/`campaign_id` 帶換行或控制字元,寫進任何日誌或回應時有日誌注入/格式錯亂的風險;此訊息也會把「這個任務編號綁定的真正 campaign_id」原樣揭露給呼叫端,若上層沒有做好授權檢查,等於是一個 task_id→campaign_id 的列舉 oracle(是否可被利用取決於增量 4 收件口的存取控制,不在本次審查範圍內,但建議收件口驗證 task_id 格式後才呼叫 `create_task`)。

### 4. `error_detail` 存 `repr(exc)`,沒有長度上限、進了永久不可刪改的歷史表

severity: minor
blocking: 否 這個增量的 `Decide` 還是測試假物件,不處理任何外部/不可信資料,目前不會夾帶敏感內容;風險是增量 4 接上真實 `Decide`(要吃真實證據、政策計算)之後才會發作,現在先標起來比較便宜。
引句:「return _Step(TaskState.FAILED, error_detail=repr(exc))」

`error_detail` 欄位是 `TEXT`,沒有長度上限(`error_detail TEXT` 無 `CHECK`/截斷),`repr(exc)` 會把例外的完整 `args` 原樣序列化。一旦增量 4 的 `Decide` 開始處理真實證據(可能包含第三方 API 回應內容),任何一次把敏感內容意外塞進例外訊息(例如政策計算某處把整包 evidence payload 丟進 `raise ValueError(payload)`)就會被 `repr()` 原封不動寫進 `tasks.error_detail`,而這張表 `INSERT` 之後沒有 `UPDATE`/`DELETE`(見 `test_the_history_table_has_no_update_or_delete_statements`,`tests/analyzer/test_flow.py`),等於永久留存、無法事後遮罩或刪除。

建議:在寫入前對 `error_detail` 做長度截斷,並在增量 4 接上真實 `Decide` 前,盤點 `Decide` 實作是否可能把敏感內容放進例外訊息(而不是只放例外型別/代號)。

### 5. SQL 語句一律參數化,沒有字串拼接

severity: clean
blocking: 否 逐條確認 `task_store.py` 裡每一條 SQL(`SELECT`/`INSERT`/`CREATE`)都用 `?` 佔位符搭配參數 tuple,沒有 f-string、`%`、`.format()` 或字串相加把資料併進 SQL 文字本身;`CREATE TABLE` 的欄位定義是靜態常數,不含任何外部輸入。
引句:「"INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",」

檢查了:`create_task`、`latest`、`evidence_for`、`commit_step` 四個方法內的全部 SQL 文字與其參數繫結方式;唯一用字串插值組出來的地方是 `TaskAlreadyExists` 的例外訊息(不是 SQL),已在 finding 3 另外記錄。
