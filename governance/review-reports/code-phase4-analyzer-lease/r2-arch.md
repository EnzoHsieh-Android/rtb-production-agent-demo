severity: major

# 第 2 輪架構對齊審查——code-phase4-analyzer-lease

被審:governance/review-reports/code-phase4-analyzer-lease/r2-delta.patch(r2-snapshot.patch 同目錄,repo /Users/enzo/rtb-3b)。

## ① 上輪三條逐條覆核

### 驗收:上輪 F1(flow.py 直接匯入 sqlite3/DatabaseBusy,跨層認資料庫例外)——已修

`flow.py` 的 `import sqlite3` / `from rtb.sqlitekit import DatabaseBusy` 已整段移除(r2-snapshot.patch `src/rtb/analyzer/flow.py` 開頭 import 區塊),吞例外的動作搬進 `task_store.py` 新增的 `release_lease_quietly`(`src/rtb/analyzer/task_store.py:324-330`),吞的範圍(只吞 `sqlite3.Error`/`DatabaseBusy`,程式錯誤照丟)比照同模組既有的 `record_tool_call`(`task_store.py:355-363`),同一個模組內部自己認自己的底層例外,不再跨層。新增測試 `test_the_flow_layer_never_names_database_errors`(`tests/analyzer/test_task_lease.py`)直接檢查原始碼不含這兩個字串,把這條釘成迴歸測試。
引句:「資料庫錯誤留在任務模組」

## F2 上輪 F2(uuid.uuid4().hex 當租約擁有者,專案裡沒有這種做法)
severity: major
blocking: 是 — uuid 用法確實拔掉了,但擁有者的「生成方式」本身變成專案裡另一種新做法,不是單純套用執行側既有寫法
引句:「再加執行緒與呼叫序號,讓」

`uuid.uuid4().hex` 已移除,新做法是 `flow.py:159` 模組層全域 `_OWNER_CALLS = itertools.count()`,配 `_new_owner()`(`flow.py:161-164`)回傳 `f"{os.getpid()}-{threading.get_ident()}-{next(_OWNER_CALLS)}"`。跟執行側對照:
- 執行側(`src/rtb/executor/runner.py:159`)是在**入口組裝層**算一次 `owner = owner or f"{os.getpid()}-{int(time.time())}"`,整個行程只算一次,再當建構參數注入 `Executor`(`src/rtb/executor/execution.py:316` `owner: str = "executor"`,dataclass 欄位),`execution.py` 本身不碰 `os`/`time`。
- 這次改法是在**業務層本身**(`flow.py` 的 `advance()`/`_new_owner()`)直接讀 `os.getpid()`、`threading.get_ident()`,且每次呼叫都重新生成一個擁有者,不是入口算好一次往下注入;而且 `advance()` 的簽章沒有開 `owner` 參數,三個可替換介面(`EvidenceSource`/`Decide`/`Submit`)都走注入,唯獨擁有者是內部硬生成、無法從外部替換或在測試裡直接控制(測試只能靠讀資料庫列反推,見 `test_a_lease_owner_names_the_process_and_thread`)。這是專案裡「入口組裝、業務層只接收注入」這條既有分工之外的第二種做法。

另外 `itertools.count()` 當模組層可變全域狀態來產生唯一性,在 `src/` 裡沒有先例(`grep itertools` 只在測試檔與領域層白名單裡出現,均非拿來當全域計數器用);其餘專案裡的唯一性都是無狀態地就地組字串(`os.getpid()-int(time.time())`、`uuid4`),不靠模組層累積狀態。這點連同上面的注入分工差異,構成「引入第二種做法」,判 major。

補充:這個差異並非全無理由——分析側的 `advance()` 依圖譜筆記(`docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md`)確實會被同一行程內的多條真實執行緒並行呼叫(`test_two_real_threads_advancing_the_same_task_pay_for_the_analysis_once`),若照執行側「整個行程算一次」的做法,同行程內兩條執行緒會共用同一個擁有者字串,`_holds()` 比對 `(lease_seq, owner)` 會分不清是哪一次呼叫的收據,租約互斥會被破壞;所以「每次呼叫產生新擁有者」在語意上是必要的。但這只解釋了「要跟執行側不同」的必要性,沒有解釋「用模組層全域計數器而不是把序號當參數往下傳」的必要性——後者本可用一般函式局部狀態或由呼叫端傳入,不必新開一種全域可變狀態的寫法。判定:uuid 部分已修,但修出新的不一致(擁有者生成方式的分工與唯一性手法都是專案裡沒有先例的第二種做法)。

### 驗收:上輪 F3(acquire_lease/release_lease 跟執行側動詞不同)——已修

沒有真的改名(仍是 `acquire_lease`/`release_lease`),但在 `acquire_lease` docstring(`task_store.py` 新增段落)明確寫出跟執行側 `_lease`/`take_over`/`release` 的對應關係與差異(分析側沒有 `extend` 續租)。上一輪標的是 minor(命名不同但結構對),這種「留原名、補說明」的處理方式對 minor 級已足夠,不需要真的改動介面命名去追平執行側動詞。
引句:「命名對照執行側收件表:取得對應 `_lease`/`take_over`,放掉對應 `release`」

---

不對齊共 1 條,其中 major 1 條(F2:租約擁有者生成方式——入口注入 vs 業務層內部生成,加模組層全域計數器,均無專案先例)。

⚠ 交編排者:`release_lease_quietly` 的「quietly」命名後綴在專案裡沒有先例(`src/` 只此一處使用,`record_tool_call` 這個既有的吞例外前例本身沒有用這種形容詞後綴區分「安靜版本」)。它吞例外的範圍跟 `record_tool_call` 一致(已在上面「驗收:上輪 F1」條目確認),差異只在命名手法,判不出這是不是要另立一套命名慣例,列為觀察,不計入不對齊條數。
引句:「吞的範圍比照 record_tool_call:只吞資料庫層錯誤,程式錯誤不吞」
