severity: major

### 1. 唯讀交易沒有指定要用顯式交易包住多筆查詢,追蹤檢視恐怕在同一次組時間線時讀到執行端兩張表不同步的資料
severity: major
blocking: 是 增量 1 的核心目的就是讓事故調查能重建出可信的時間線,若這條路徑本身會讀到跨表不一致的資料,直接抵觸這份計劃要解決的問題,而且沒有合約(S600-S609)在管這件事
引句:「唯讀連線讀到的是那一刻已提交的快照,正在進行中的交易看不到」
說明:設計把「那一刻已提交的快照」這個性質直接歸給「唯讀連線」本身,但用 `mode=ro` 開的連線在 `isolation_level=None`(此專案既有慣例,見 `sqlitekit.py`)下,每一次 `execute()` 若沒有被包在顯式的 `BEGIN` 裡,各自看見呼叫當下最新已提交的狀態,不是連線開啟那一刻的固定快照。我在暫用目錄用專案的 `.venv` 實測驗證:同一條 `mode=ro` 連線上,若不顯式 `BEGIN`,兩次 `SELECT` 之間只要寫入端提交了新資料,第二次 `SELECT` 就會看到新資料;只有在顯式開了一個不取鎖的 `BEGIN` 之後,期間的多次 `SELECT` 才會停在同一個快照,直到那個 `BEGIN` 結束才前進。追蹤檢視要在同一次呼叫裡先後讀執行端的生命週期事件表跟嘗試紀錄表(見「執行端生命週期事件與嘗試紀錄」一段),這兩次讀是分開的函式呼叫。設計沒有寫「唯讀交易物件」內部要不要開這個顯式 `BEGIN`(唯一提到的既有慣例是寫入側的 `immediate_transaction`,只講取鎖版本),也沒有合約測試檢查「同一次追蹤裡,執行端兩張表讀到的是同一個提交時點」。若實作時因為「唯讀交易不取寫入鎖」這句話而略過顯式 `BEGIN`,執行迴圈持續寫入時,追蹤檢視可能拼出「嘗試紀錄已經到終點但看不到對應的已交給執行/已擋下生命週期事件」這種自相矛盾的時間線,而這正是 S604(依時間排序組時間線)與 Phase 9 完成條件「至少一個事故能走完指標→切片→追蹤→稽核」要保證能可靠重建的東西。
file: `src/rtb/sqlitekit.py:53`

### 2. 唯讀交易物件過不了既有的交易憑證型別檢查,設計沒交代要怎麼改
severity: major
blocking: 是 照現有程式碼,S604/S605 要求的「嘗試紀錄與收件口讀取函式同時收寫入交易與唯讀交易」目前完全過不了關,屬於漏掉的合約,而且牽動一個安全邊界(交易憑證)
引句:「嘗試紀錄模組與收件口模組的讀取函式同時收寫入交易與唯讀交易」
說明:`attempt_store._conn()` 目前是 `if type(tx) is not ExecutorTransaction or not tx.is_open`(型別完全相等,不收子類別),`InboxStore._own()` 則額外檢查 `tx.conn is not self._conn`(必須是同一條連線)。這兩處就是模組文件講的「威脅模型是防忘記、不防刻意繞過」的憑證機制(`_EXECUTOR_TRANSACTION_ISSUER`)。設計要求同一批讀取函式(`latest`、`history`、`snapshot`、`unresolved_count`…以及收件口的 `awaiting`、`in_progress_for`、`stops` 等)「同時收」一個新的唯讀交易物件,但唯讀交易物件背後接的是另一條(`mode=ro`)連線,不可能等於 `self._conn`,也不會是 `ExecutorTransaction` 型別——照現在的檢查邏輯,唯讀交易物件傳進去會直接被 `NotInTransaction`/`_own` 拒絕。要讓這條路通,勢必要改這兩處檢查(例如允許第二種型別、或替換掉「同一條連線」這個判斷),但這牽動的是專案自己說明是「防止善意重構繞過」的安全機制,而「最小設計」段落完全沒提要怎麼改、要不要發新的一組憑證、新舊兩種交易物件的邊界在哪。這不是措辭問題,是照字面實作會直接卡死或迫使實作者自行擴大既有憑證檢查的範圍卻沒有設計指引。
file: `src/rtb/executor/attempt_store.py:143`
file: `src/rtb/executor/attempt_store.py:211-212`
file: `src/rtb/executor/inbox_store.py:553`

### 3. 追蹤檢視要同時匯入分析端與執行端,踩到既有「行程不得互相匯入」的機械閘,設計沒說新模組放哪裡
severity: major
blocking: 是 這正是本次審查指定要判的「分層與依賴方向」;現有 ruff 設定是機械擋,若新模組放進既有目錄會直接讓 lint 擋下,設計必須先講清楚落點才算完整
引句:「追蹤檢視是命令列入口加一支函式(回結構化結果),比照既有命令列入口寫法」
說明:追蹤檢視要「依序讀三個來源」——分析端任務歷史(`rtb.analyzer.task_store`)、執行端生命週期事件與嘗試紀錄(`rtb.executor.inbox_store`/`attempt_store`)、加上 DSP——等於一個模組要同時匯入 `rtb.analyzer` 與 `rtb.executor` 兩邊的內部讀取介面。但專案既有機械規則明確禁止這件事:`src/rtb/analyzer/ruff.toml` 用 `flake8-tidy-imports.banned-api` 把 `"rtb.executor"` 標成禁止匯入,理由寫的是「兩個行程的程式碼不得互相依賴」(對稱地,`src/rtb/executor/ruff.toml`、`src/rtb/dsp/ruff.toml` 也各自禁止匯入別的行程);這條規則按目錄生效,不是全域擋,所以新模組能不能同時匯入兩邊,完全看它被放進哪個目錄。設計只說「追蹤檢視是新模組,開一篇 Systems 家」,完全沒交代這支程式碼的套件路徑落在哪裡、要不要新增一份 `ruff.toml` 或調整既有兩份的 banned-api 清單。若實作時比照「比照既有命令列入口寫法」把它放進 `rtb/executor/`(因為它需要 `InboxStore`、`ExecutorTransaction` 等執行端內部機制,直覺上會放在那裡),一 `import rtb.analyzer.task_store` 就會被既有機械閘擋下,逼著臨時決定要不要開洞破壞「行程拆分」的既有不變量——這個決定designer理應在設計裡先做,不該留給實作者現場拍板。
file: `src/rtb/analyzer/ruff.toml:1,6`
file: `src/rtb/executor/ruff.toml:1-6`

### 4. 增量 1 的唯讀連線沒有回頭接既有 observability.py,增量 2 的即時指標若沿用現有查詢介面會持續握寫入鎖
severity: major
blocking: 是 這是增量 1 跟增量 2 大綱接不上的地方:增量 2 講明指標全部即時算,而目前唯一能算這些指標的既有查詢介面自己就寫著要在 Phase 9 解決握寫入鎖的問題,增量 1 卻沒有處理它
引句:「全部從事件與紀錄即時算」
說明:`src/rtb/executor/observability.py` 是目前執行端唯一的可觀測唯讀查詢模組(總曝險停下次數、表滿延後、人工核可、額度使用率、總曝險稽核明細),它的文件自己寫明現況與待辦:「每支都收執行行程資料庫交易入口開的交易(會握寫入鎖,跟 Phase 5 的 `version_conflict_count` 同一種做法)」,並且直接點名「Phase 9 接告警或輪詢時要一併決定範圍上限與不握寫入鎖的讀法」。增量 2 的大綱要「即時算」一整批指標(含額度使用率、護欄/權限/人工核可擋下數等——這些現有資料來源就是 `observability.py` 已經在查的東西),若照這份 Increment 1 設計實作完就直接進 Increment 2,`observability.py` 這批既有函式仍然只收 `ExecutorTransaction`(見它對 `ExecutorTransaction` 的匯入與逐支函式簽名),沒有被納入這次「讀取函式同時收寫入交易與唯讀交易」的範圍。結果是:輪詢式的即時指標計算會不斷透過 `store.transaction()` 拿寫入鎖去跑這些查詢,跟執行迴圈的正常寫入互相排隊——增量 1 明明已經為了同樣的理由(避免佔寫入鎖)造了一整套唯讀連線機制,卻沒有把既有、已經被自己文件點名要修的這批查詢接進去,讓增量 2 一開始就繼承這個尚未解決的隱患,屬於「大綱本身跟增量 1 接不上」。
file: `src/rtb/executor/observability.py:6-7`
file: `src/rtb/executor/observability.py:11`
