severity: clean

比對範圍:查詢語句組法(對照同檔 `stop_count_query`)、跨模組在收件口模組 SQL 裡讀嘗試紀錄表的既有先例、測試裡查詢計畫斷言的既有寫法。三項都沒有發現引入第二種做法或跨層直呼。

## 逐項確認

**查詢語句組法**:`approval_use_count_query` 改動後仍是「`where`/`params` 累積 tuple + 迴圈 `for clause, value in (...)` 只在 `value is not None` 時才加」這套跟 `stop_count_query` 完全一樣的組法(`src/rtb/executor/inbox_store.py:1041-1051` 對照 `:1017-1026`),只是多了一段條件式的 `join` 字串前綴。這個「有可選 join 片段 + 同一套 where/params 累積」的寫法,本檔已有先例:`awaiting_count`(`src/rtb/executor/inbox_store.py:936-949`)用 `join = ("LEFT JOIN write_stops w ON ...")` 這一行變數再插入 SQL 字串的組法跟這次改動的 `join, where, params = "", ["1 = 1"], []` 是同一個模式,只是 `awaiting_count` 的 join 不隨篩選條件開關(用 LEFT JOIN 保底不濾掉列)、這次的 join 只在 `campaign_id is not None` 時才建(用一般 JOIN,因為只在依廣告篩時才需要接那一列)——兩處用的 JOIN 種類不同是語意上必須(LEFT JOIN vs 一般 JOIN 對應「篩選是否可選」),不是同一件事卻寫兩套風格。

引句(凍結 patch 原文):「寫成連接而不是子查詢:只依廣告篩才不整張掃核可使用表」——這句設計筆記描述的正是從 EXISTS 子查詢換成 JOIN 這個改動本身,對照程式碼 `src/rtb/executor/inbox_store.py:1046-1047` 的 `join = ("JOIN attempts f ON f.key = u.key AND f.seq = 1 AND f.task_id = u.task_id " "AND f.revision = u.revision")` 屬實。

**跨模組讀嘗試紀錄表的先例**:收件口模組(`inbox_store.py`)在這次改動之前就已經直接下 SQL 讀 `attempts` 表,不是只透過 `attempt_store` 的公開方法轉接。`process_awaiting`(`src/rtb/executor/inbox_store.py:734`)已有 `OR EXISTS (SELECT 1 FROM attempts f WHERE f.task_id = p.task_id AND f.seq = 1))`,這是子查詢形式的先例。而「JOIN attempts f ON f.key = ... AND f.seq = 1」這個 JOIN 形式的先例則是 `attempt_store.py` 自己的 `aggregate_holdings`(`src/rtb/executor/attempt_store.py:400-401`,提交 a8b67a5 引入,早於這批 r3 修正)。這次 `approval_use_count_query` 改成 JOIN 只是把收件口模組原有的「讀 attempts 表」這件事,從子查詢形式換成本專案(在別的模組)已經在用的 JOIN 形式,兩種寫法在 base 都已存在,沒有新開第三種讀法,也沒有繞過模組邊界:設計計劃筆記明講的邊界規則(`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md` 增量 4「最小設計」段:「收件口模組本來就依賴嘗試紀錄模組,反過來沒有」)在這批修正裡沒有被打破——只有收件口模組讀 attempts 表,方向沒變,也沒有 `observability.py` 越過 `inbox_store`/`attempt_store` 自己下 SQL(`approval_counts` 仍是呼叫 `store.awaiting_count`/`store.approval_use_count`,見 `src/rtb/executor/observability.py:106-108`)。

**測試裡查詢計畫斷言的既有寫法**:`tests/executor/test_observability.py` 裡查詢計畫斷言本來就是「`_plan()` 取字串 + `re.search`/`in` 比對」這套機制(先例:`test_observability_queries_use_their_indexes` 用 `index in _plan(...)`;base 版 `test_applied_count_uses_its_index` 已經是 `re.search(rf"\b{index}\b", plan)`)。這次修正做的兩個延伸:

1. 把 `campaign_id` 那一組參數的 `index` 值從單一索引名字串換成一段更長的正則(`r"attempts_first_rows\b.*approval_uses_1 \(task_id=\? AND revision=\?\)"`),塞進同一個 `assert re.search(rf"\b{index}", plan)`——沿用的是同一支斷言函式與同一個 parametrize 骨架,沒有新開一套斷言機制,只是把可比對的字串內容變複雜、順手把原本包住 `index` 的結尾 `\b` 去掉(因為新內容結尾是 `)`,非字元不能接 `\b`)。這是同一招的自然延伸,不是引入第二種比對法。
2. 新增 `assert not re.search(r"\bSCAN u\b", plan), plan`,是本檔第一次出現「斷言查詢計畫沒有掃某張表」,但仍是用同一支 `_plan()` 輔助函式 + `re.search`,沒有另外拉一套機制(例如自己解析 EXPLAIN 輸出結構、或另建新的 helper)。跟既有「只斷言用到指定索引名」的寫法相比是多了一條負向斷言,但呼叫的仍是同一套工具,判斷這是同一招的擴充而非架構分歧。

`test_awaiting_condition_with_alias_matches_the_shared_one`(新增函式)也是純字串比對兩個模組層常數(`inbox_store.AWAITING` 與 `inbox_store._AWAITING_P`),沒有下 SQL、沒有碰資料庫連線,跟本檔其他測試依賴 `store`/`_plan` fixture 的寫法不同,但這是因為它測的是常數同步、本來就不需要資料庫,不是漏接既有 fixture 慣例。

## 結論

三個比對點都沒有發現「引入第二種做法」或「跨層直呼」:JOIN 換子查詢沿用本專案既有兩處先例(`inbox_store.py:734` 子查詢先例、`attempt_store.py:400-401` JOIN 先例);收件口模組讀嘗試紀錄表的方向與既有邊界規則一致,呼叫端(`observability.py`)仍只透過收件口模組的方法轉接;測試裡的查詢計畫斷言沿用同一套 `_plan()` + regex 機制,新增的「沒有全表掃描」斷言是同一招式的延伸不是新機制。沒有 major/blocking 發現。
