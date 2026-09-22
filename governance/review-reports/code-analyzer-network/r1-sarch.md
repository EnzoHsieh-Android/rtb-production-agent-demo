severity: major

已核對、無問題的兩項:
- `httpclient.py` 跟 `httpkit.py`/`sqlitekit.py` 是同一種設計語言:放在 `src/rtb/` 根層、開頭都是「共用基礎(誰跟誰共用、不各自造輪子)」式 docstring、逾時必填不給預設值、標頭用封閉列舉(呼應既有 `EVENT_CODES` frozenset、`StrEnum` 的封閉集合慣例)。不像 `RequestRejected`/`DatabaseBusy` 在 kit 層自訂例外詞彙表,`httpclient.py` 只回傳 `(status, body)` 把狀態碼解讀權下放給呼叫端——這剛好是 `httpkit.py` 用 `map_exception()` 讓子類別做自己領域對照的鏡像做法,不是另立一套。
- `dsp_client.py`、`inbox_client.py` 都沒有偷碰 `TaskStore`:`dsp_client.py` 只匯入 `TaskRow`(純資料型別,用於符合 `EvidenceSource` 協定簽章,`flow.py` 自己也這樣匯入 `TaskRow`),`inbox_client.py` 完全沒有匯入 `rtb.analyzer.task_store`;兩者都沒有 `TaskStore` 實例或方法呼叫,吻合圖譜 RULE(`docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:30`)。

### 1. record_tool_call 聲稱比照收件口事件表的既有做法,但例外範圍比既有做法寬
severity: major
blocking: 是 這是同一個「盡力而為、寫失敗就放棄」模式的第二種實作範圍,會吞掉既有做法不會吞的例外類別
引句:「這筆寫入自己絕不讓例外往外傳(比照收件口事件表的既有做法)」
`task_store.py` 的 `record_tool_call` 明講是比照收件口事件表(`inbox_store.py` 的 `record_event`)的既有做法,但既有做法的 `_write_event` 用 `except (sqlite3.Error, DatabaseBusy):`(見 file: `src/rtb/executor/inbox_store.py:254`),只吞資料庫相關的失敗;新加的 `record_tool_call` 卻用 `except Exception:`(見 file: `src/rtb/analyzer/task_store.py:253`),把範圍放寬到任何型別的例外都吞,包含跟資料庫無關的程式錯誤(例如呼叫端傳錯型別的參數)。用 `tests/analyzer/test_instrumented.py` 裡「關掉連線觸發寫入失敗」那個測試案例驗證:關閉連線觸發的是 `sqlite3.ProgrammingError`(`sqlite3.Error` 的子類別),既有的窄範圍寫法一樣接得住,不需要放寬到 `except Exception`。這是同一個「不能讓記錄失敗拖垮被包住的呼叫」模式,卻多出第二種、更寬鬆的例外處理寫法,不是同一套。

### 2. InstrumentedSubmit 用查詢重建 task_seq,跟同檔案內 InstrumentedEvidenceSource 的直傳做法不一致
severity: minor
blocking: 否 是 `Submit` 協定既有簽章(只有 `proposal`,沒有 `task`)逼出來的必要選擇,沿用既有的 `store.latest()` 公開介面,不是憑空發明新機制,但殘留的時序落差沒有被記錄
引句:「row = self._store.latest(task_id)」
`instrumented.py` 裡 `InstrumentedEvidenceSource._record` 直接讀傳入的 `task.seq`(呼叫當下那一列的序號),`InstrumentedSubmit._record` 卻是呼叫完 `self._inner(proposal)` 之後,才另外呼叫 `self._store.latest(task_id)` 反查目前最新的一列。這個落差是合理的(`Submit` 協定簽章從增量 3 就只有 `proposal`,不是這個增量能改的),但專案自己的計劃筆記記載的不變量是「呼叫發生在哪一列的檢查點期間,不是呼叫完成後才推算的新序號」(file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase2任務流程_計劃.md:227`),而系統本身明文記載 `advance()` 支援並行呼叫、同一任務可能有多個執行緒同時在跑(file: `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:23`)。在提交(HTTP 往返)這段期間,若另一次並行 `advance()` 已經把這個任務推進到下一列,`InstrumentedSubmit` 事後查到的就會是錯的 `task_seq`,跟計劃筆記講的語意不符。這只影響 `tool_calls`(觀察用的追蹤記錄,不參與狀態機或收件判斷),不是資料正確性風險,所以不擋;但目前沒有任何 RULE/PITFALL/REVISIT 記下這個殘留風險。

### 3. Evidence.payload 的欄位驗證沒有比照既有的「不允許非有限浮點數」慣例
severity: minor
blocking: 否 只是驗證邏輯少做一步,不是另一套做法或跨層呼叫,且目前唯一的生產呼叫路徑(`dsp_client._content_hash`)在建構 `Evidence` 之前就會因為 `allow_nan=False` 先炸掉
引句:「isinstance(key, str) and (item is None or isinstance(item, str | int | float | bool))」
`evidence.py` 既有欄位驗證對數字類欄位一律連 `math.isfinite` 一起檢查(`_is_positive_finite`,file: `src/rtb/domain/evidence.py:98-102`),`proposal.py` 的欄位大小驗證也明確把非有限浮點數當成「不是可序列化成 JSON 的值」拒收(file: `src/rtb/domain/proposal.py:187`)。新加的 `_is_payload` 只檢查型別是不是 `str | int | float | bool | None`,沒有排除 `NaN`/`Infinity`,跟同一個領域層「數值欄位要保證能安全序列化」的既有慣例不完全一致——雖然目前唯一產生 payload 的呼叫路徑(`dsp_client.py` 的 `_content_hash`)會在雜湊那一步用 `allow_nan=False` 先擋下非有限浮點數,`Evidence` 建構式本身若被其他呼叫端直接帶入 `NaN`/`Infinity` 仍會通過驗證。

### 4. 分析行程流程與檢查點節點的 responsibility 文字沒有隨 about_code 擴充同步更新
severity: minor
blocking: 否 檔案本身都有家,只是節點自己的職責描述跟它現在管的檔案清單自相矛盾,屬圖譜衛生問題
引句:「示範用的最小決策規則:讓整條流程能被示範跑完,不是交接文件後面階段要做的真正業務規則。」
這支增量把 `dsp_client.py`、`inbox_client.py`、`policy.py`、`instrumented.py` 都併入既有的 [[Systems/分析行程流程與檢查點]] 節點(about_code 已正確列出,file: `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:11-14`),但該節點頂端的 `responsibility:` 欄位仍寫著「不負責真的網路呼叫……不負責決策規則本身」(file: `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:6`)。`dsp_client.py`/`inbox_client.py` 就是真的網路呼叫,`policy.py`(上面引句所在檔案)就是決策規則本身——節點的職責宣告沒有隨 about_code 一起改,兩者現在互相矛盾。
