severity: major
報告格式規則已讀(`/private/tmp/claude-501/.../scratchpad/p9i1-fmt.md`),以下照抄格式輸出全文。全套 pytest(`.venv/bin/python -m pytest`)在 `/Users/enzo/rtb-p9i1` 跑過,1588 項全過;另在 `/tmp` 自己的副本用 monkeypatch 重現兩個新回歸,驗完即刪除,repo 沒有留下任何改動。

完整報告全文如下:

---

severity: major

## 第 1 輪 8 條驗收(逐條重讀原始碼判定,不只看測試是否轉綠)

### 1. dsp-1 / inbox-1 / x1-4:呼叫紀錄撞鎖蓋掉已算好的 DSP 結果
結論:已修。`Executor.flush_calls`(`file: src/rtb/executor/execution.py:454`)把待寫列表複製一份再用新交易寫,寫入撞 `InboxBusy` 時整批留在 `self._pending` 下次再補,不再讓例外冒出去蓋掉呼叫端已經拿到的 DSP 結果;`process_one`/`reconcile_all`/`runner._serve` 三層都在開始前、每把鍵結束、行程收尾各補一次(`file: src/rtb/executor/execution.py:468, :488, :513``file: src/rtb/executor/runner.py:90`)。真正的資料庫錯誤(非忙碌)仍照舊往外丟,`tests/executor/test_dsp_calls.py` 的 `_other_database_errors_still_propagate` 有蓋到。
引句:「資料庫忙碌就留著下次再補,永遠不丟忙碌例外」

### 2. arch-1:`record_dsp_call` 靠模組層級 `ContextVar`,範圍外靜默不記
結論:已修。拿掉了 `_SCOPE: ContextVar` 與掛在 `DspClient` 上的共用 `on_call`,`DspPort` 的每支方法都改收必填關鍵字參數 `on_call`(`file: src/rtb/executor/execution.py:154-179`),每個呼叫點用 `self._calls(proposal, key)` 顯式產生一支綁定當次提案身分的回呼再傳進去。逐一核對 `execution.py` 裡全部 10 個 `self.dsp.*` 呼叫點,傳入的 `(proposal, key)` 都對得上該函式自己收到的參數(見下方回歸段落的交叉檢查),漏傳會被型別檢查擋下,不會再有「範圍外靜默不記」的情況。
引句:「給 DSP 用戶端的回呼,綁好這次呼叫是為了哪份提案(每個呼叫點顯式傳)。」

### 3. tests-1:維運套件邊界掃描只看屬性/名字/匯入,`getattr` 字串派發繞得過
結論:已修。`tests/ops/test_ops_boundaries.py:86` 新增 `_dynamic_lookups`,對 `getattr`、`attrgetter`、`methodcaller`、`__getattribute__`、`vars`、`__dict__` 這批名字直接判違規,不論是不是用字串組出方法名。確認真正的 `src/rtb/ops/*.py` 沒有用到這批名字,不會誤傷既有程式碼;新增的 6 個 `getattr`/`attrgetter`/`methodcaller`/`vars` 繞道測資也全部被 `_ops_offenders` 抓到。
引句:「名字掃描看不到字串裡的方法名,維運套件本身也用不到,一律不准」

### 4. tests-2:`lumos spec-trace` 判 [S625][S626] 懸空,測試函式名跟規格標的對不上
結論:已修。`tests/executor/test_dsp_calls.py:188` 的測試函式改名為 `test_dsp_call_records_keep_the_error_code`,`tests/executor/test_read_only.py:230` 改名為 `test_the_last_terminal_event_lookup_uses_the_terminal_index`,兩支各自緊接在 `# ---- [S625] ----`、`# ---- [S626] ----` 標記下,且測試內容本身確實在驗證規格句描述的行為(錯誤代碼封閉列舉、終點事件查詢走部分索引不全表掃描)。
引句:「def test_dsp_call_records_keep_the_error_code」

### 5. spec-1:`release` 表上用 `coalesce` 留舊原因,事件卻記這次傳入的空值
結論:已修。`file: src/rtb/executor/inbox_store.py:1258` 在 `UPDATE ... coalesce(...)` 之後,改成另外 `SELECT last_failure` 讀回表上實際落地的值,再用這個值記事件,不再直接用呼叫端傳進來、可能是 `None` 的 `failure` 參數。`tests/executor/test_lifecycle_events.py` 新增的 `test_a_lease_release_event_records_the_last_failure_left_on_the_row` 驗證第二次不帶原因時事件仍記第一次的 `table_full`,跟表上一致。
引句:「原因記表上實際留下的最後一次失敗原因」

### 6. x1-1:`take_over` 無條件寫「租約過期被接手」,自己續做也記
結論:已修。`file: src/rtb/executor/inbox_store.py:1454` 在 `UPDATE` 之前先讀 `before = (lease_until, lease_owner)`,更新後只有「擁有者變了,或舊 `lease_until` 是空值/已早於現在」才寫 `RECLAIMED` 事件,同一個擁有者續做自己還沒到期的租約不記。`before` 的讀取與後續 `UPDATE` 在同一個 `store.transaction()`(`immediate_transaction`)裡,SQLite 單寫入鎖保證兩者之間不會被別的交易插隊改掉這一列,不是「先讀後寫」式的競態。新測試 `test_the_same_owner_taking_over_its_live_lease_writes_no_reclaim_event` 同時蓋了「自己續租不記」與「真到期/換人才記」兩種情況。
引句:「舊租約真的到期了,或換了擁有者才記」

### 7. x1-2:`_revision_keys` 用「任務+修訂」去重,內容雜湊不同的第二份被吞掉
結論:已修。`file: src/rtb/ops/trace.py:103, :109` 把關聯鍵從 `(task, revision)` 換成 `Ident = (task, revision, content_hash)` 三件組,`_analyzer_task`、`_revision_keys`、`_revisions`、`owners` 全部跟著換血,`_call_segment` 額外用 `hashes` 映射把雜湊接回沒存內容雜湊的 `dsp_calls` 段落。
引句:「關聯一律用三件組,不用任務加修訂」

### 8. x1-3:`httpclient.py:68-70` 本文解析失敗丟 `ValueError`,狀態碼遺失
結論:已修。`file: src/rtb/httpclient.py:80-81` 新增 `UnreadableResponse(ValueError)` 子類別帶 `status` 欄位,`_parsed` 解析失敗時改丟這個子類別;`dsp_client.py:_send` 先攔 `UnreadableResponse` 用 `_by_status(exc.status, False)` 分類、記下狀態碼,其餘 `(OSError, ValueError)` 才落回原本「沒拿到回應」的分類。因為是 `ValueError` 子類別,既有只接 `(OSError, ValueError)` 的呼叫端(如 `trace.py:_read_dsp`)行為不變。**已額外驗證**:這個分類重新歸類只影響寫進 `dsp_calls` 的觀測欄位,`react()`/`react_void()` 只看 `WriteAnswer.status`/`VoidAnswer.status`(仍固定是 `None`),不看 `failure` 分類欄位,既有重試與結果判斷(`RESPONSE_TABLE`/`VOID_TABLE`)不受影響。
引句:「拿到了狀態碼、但本文不是合法 JSON 或超過上限」

## 新引入的回歸

### 9. 待寫清單撞忙碌時,永遠吞掉例外沒有任何信號——會不受限長大,行程結束時也可能整批無聲消失
severity: major
blocking: 是
引句:「所以只有行程當機才會少記」
file: `src/rtb/executor/execution.py:465`
file: `src/rtb/executor/runner.py:90`

觸發情境一(無聲長大):把 `record_dsp_call` 換成永遠丟 `DatabaseBusy`,但收件/對帳主流程本身沒有鎖競爭(即只有「補寫呼叫紀錄」這個短交易一直拿不到鎖,主要的 `store.transaction()` 都正常提交)。連跑 20 輪 `process_one()`,每一輪業務都正常完成,完全沒有 `InboxBusy` 冒到 `runner._loop`——因為 `flush_calls` 在 `except InboxBusy: return`(`execution.py:465`)這裡把忙碌吞掉,`_loop` 的 `busy_streak`(`runner.py:62-79`)只算 `reconcile_all`/`process_awaiting`/`process_one` 直接丟出來的 `InboxBusy`,從不計入 `flush_calls` 自己吞掉的次數。實測(`/tmp` 副本,已刪除)20 輪下 `worker._pending` 長度是 `[3, 6, 9, 10, 11, ..., 26]`,單調遞增、無上限、無任何 log、無任何停機,行程可以無限期帶著這包越滾越大的記憶體資料跑下去。

觸發情境二(收尾無聲消失):同樣持續忙碌,`process_one()` 正常完成並回 `EXECUTED`,此時 `worker._pending` 已累積 3 筆真實發生過的 DSP 呼叫紀錄。手動呼叫一次 `worker.flush_calls()`(模擬 `runner._serve` finally 收尾那一下)——沒有丟出任何例外,`dsp_calls` 表仍是 0 列,`worker._pending` 原封不動留在記憶體。若行程在這個時間點真的結束(`EXIT_BUSY`、`EXIT_HALTED`,或單純 `max_rounds` 跑完自然返回),這 3 筆紀錄就永久消失,而且完全沒有錯誤訊息、沒有 exit code 差異、`dsp_calls` 表上不會留下任何「這裡曾經漏過」的痕跡。`execution.py:455-456` 的註解斷言「所以只有行程當機才會少記」,但 `runner.py:90` 自己的 docstring 其實已經承認「還是忙就只能放掉(跟當機一樣少記那幾列)」——兩處說法互相矛盾,而且不管哪一種說法,實際行為都是**完全靜默**:沒有計數器、沒有 log、沒有告警,維運端無從得知這個行程這輩子漏記過幾筆呼叫紀錄。

會出什麼錯的行為:`dsp_calls` 是 SLO/對帳追蹤(`lumos search`/`ops/trace.py`)唯一的觀測資料來源,增量 2 的重投率、版本衝突率等公式全部從這張表算;無聲漏記幾筆或行程長時間帶著無界成長的緩衝跑,會讓這些指標悄悄失真,且沒有任何跡象可供事後排查是哪個時段漏了。這正是本輪要修的「撞鎖蓋掉已算好結果」同一類問題(讓資料庫忙碌狀態悄悄侵蝕正確性紀錄),只是換了個更隱蔽的形式殘留下來。

建議修法:`flush_calls` 撞 `InboxBusy` 時至少要留一個可觀測信號——例如記一次 warning log 帶上目前 `len(self._pending)`,或維護一個「連續補寫失敗輪數」計數,超過門檻比照 `runner._loop` 的 `busy_streak`/`BUSY_LIMIT` 邏輯真正停機(而不是無限期悄悄扛著);行程收尾（`runner._serve` 的 `finally`）補寫仍失敗時至少要把還剩幾筆、哪些鍵印到 stderr,不要讓「跟當機一樣少記」變成完全沒有痕跡的沉默丟棄。
