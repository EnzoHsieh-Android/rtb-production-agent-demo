severity: minor

鏡頭:併發與資源(最壞時序的安排者)。標的:/Users/enzo/rtb-3b 的 `governance/review-reports/code-phase4-analyzer-lease/r1-snapshot.patch`(`src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`、`tests/analyzer/test_task_lease.py`)。

## 驗證方法
在 `mktemp -d` 複製 `src`/`tests`/`pyproject.toml`,一律用 `git -C` 或直接跑 pytest/python,不動 repo:
1. `PYTHONPATH=<臨時目錄>/src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest tests/analyzer/test_task_lease.py tests/analyzer/test_flow.py tests/analyzer/test_task_store.py -q -p no:cacheprovider` → `67 passed`。
2. 自寫 12 執行緒同時對同一任務打 `advance()`(`decide()` 內用鎖記「同時在裡面幾個」)、自寫 6 個**真 OS 行程**(排除 GIL 疑慮)同時打同一任務、都在 `_Blocking`/`decide` 呼叫前用 `time.sleep`/barrier 放大交錯窗口:兩種情況下 `max_concurrent` 都是 1、`overlap detected: False`,只有一個呼叫端真的付費,歷史列數與作者的 S150 一致(未觀察到互斥被打破)。
3. 自寫「20 個不同任務 × 15 輪沒有進展的 advance()」,調低 `busy_timeout_seconds=0.05` 放大鎖競爭:0.15 秒內出現 8 次 `DatabaseBusy` 從 `advance()` 直接往外丟(見 F1)。

腳本存於 `/private/tmp/claude-501/-Users-enzo-rtb-production-agent-demo/5d833242-be96-4298-aba3-6f1eb8999a86/scratchpad/{stress_threads.py,stress_procs.py,stress_lock_churn.py}`。

## 讀-判-寫是否在同一個立即寫入鎖交易裡
`src/rtb/sqlitekit.py` 的 `connect()` 用 `isolation_level=None`(交易一律手動控制),`immediate_transaction()` 進入即 `BEGIN IMMEDIATE`(鎖不到丟 `DatabaseBusy`)、正常結束 `COMMIT`、任何例外 `ROLLBACK`。逐一確認:
- `TaskStore.commit_step`(`src/rtb/analyzer/task_store.py:233`):`_lease_allows` 檢查、`expected_seq` 檢查、寫 `tasks`/`evidence`、`_append_release`(帶收據時)全部在同一個 `with immediate_transaction(self._conn):` 區塊裡,順序上「先核對租約、再核對序號、再寫」,原子。
- `TaskStore.acquire_lease`(:294):讀目前租約列、判活著與否、寫新的取得列,同一個交易。
- `TaskStore.release_lease`(:308):讀目前租約列、核對還是自己的取得列(`_holds`)、寫放掉列,同一個交易。
三者各自原子,沒有找到讀-判-寫被拆成兩個交易的縫隙。

## 兩條以上連線的交錯
- 12 執行緒／6 行程同時打同一個任務:互斥沒被打破,只花一次錢(見上方驗證 2)。
- `_advance_holding`(`flow.py:156`)在拿到租約之後會 `store.latest()` 重讀一次列號,跟第一次讀到的不同就放掉租約、回傳新狀態(:166-169),不會拿舊列去呼叫外部介面——這一段是純讀取,重讀當下不可能有別人在寫,因為「持有活的租約」本身就會擋住所有其它 `commit_step` 呼叫(不管帶不帶收據),邏輯上唯一能讓重讀不一致的窗口只在「讀完列、還沒拿到租約」之間,程式碼已經處理這段(對應作者測試 S154)。
- 過期接手(S152/S159)在我自己的交錯注入下沒有重現任何雙寫或資料錯亂,`history` 列數與作者宣稱一致。

## `_release_keeping_the_original_error` 吞的範圍
`except (sqlite3.Error, DatabaseBusy): return`(`flow.py:194-196`)跟同模組既有的 `record_tool_call`(`task_store.py:356`)、以及收件口 `inbox_store._write_event` 的既有慣例完全一致,不是這次新開的寬鬆例外範圍;`sqlite3.ProgrammingError` 雖然也是 `sqlite3.Error` 子類、理論上會被一起吞,但這是專案既有慣例(非這次 diff 新增的偏離),不算本次改動的問題。

## F1 沒有進展的 `advance()` 從 0 個寫入交易變成 2 個,而且搶的是整個資料庫檔案的鎖
severity: minor
blocking: 否 — 沒有資料錯亂、沒有吞掉不該吞的例外,只是既有例外(`DatabaseBusy`)出現頻率變高;呼叫端本就要處理 `advance()` 可能丟 `DatabaseBusy`(commit_step 一直都會丟),不是新的例外類型
引句:「沒進展、提交失敗、行程內例外都放掉,放掉帶「還是我的」條件,放掉失敗吞資料庫錯誤保留原例外」
在這之前,「沒有進展」(`EvidenceSource` 拋例外、`Decide` 回 `NeedsFreshEvidence` 之外的情形、`SubmitBusy`)是純讀取,完全不進 `immediate_transaction`。這次改動後,同一種「沒有進展」在 `_advance_holding`(`flow.py:171-172`)一定會先 `store.acquire_lease()`(:144)、再 `store.release_lease()`(:172),兩個各自獨立的 `BEGIN IMMEDIATE` 交易——而 `BEGIN IMMEDIATE` 鎖的是整份資料庫檔案,不是單一 `task_id` 的列,所以即使是完全不相干的兩個任務,只要都在跑「沒有進展」的輪詢,也會互搶同一把寫入鎖。用 `busy_timeout_seconds=0.05`(刻意調低以在短時間內放大鎖競爭,正式環境預設 `BUSY_TIMEOUT_SECONDS=5.0` 寬鬆很多)重現:20 個任務各跑 15 輪「沒有進展」的 `advance()`,0.15 秒內就出現 8 次 `DatabaseBusy` 未經任何 `try/except` 直接從 `advance()` 頂層丟出(`acquire_lease()` 本身不在任何 try 區塊裡,`flow.py:144-146`)。表態記錄裡 py-hotpath 判 `na` 的理由只講了「一輪只挑一份提案、對帳最多掃二十把鍵、都是小查詢」,沒有評估這個「所有任務共搶同一把 DB 級鎖、且原本零寫入的分支現在也要搶」的頻率變化;`分析行程流程與檢查點.md` 目前記的已知缺口只有「租約長度沒有機械守衛」與「歷史表無界成長」兩條,沒有這條。Phase 4 的主題正是佇列與多工作者(多任務同時被推進是常態,不是單任務並行這種邊角案例),建議在該筆記補一條 PITFALL/REVISIT,或評估是否要把「沒有進展」的放租約失敗也視為可接受地跳過(目前是老實傳出去,不算錯,只是量沒人估過)。

## 圖譜鏡頭:固定席逐條判
LUMOS-IMPACT 自動附加的 7 個固定席節點,其「牽連檔」全部指向 `src/rtb/executor/{attempt_store.py,execution.py,runner.py}`、`tests/executor/fakes.py`——這是 `rtb-production-agent-demo`(這個對話的 cwd)自己最近幾個提交(「執行側多工作者」)踩到的檔案,跟這次審查標的完全不重疊:標的是另一個 repo `/Users/enzo/rtb-3b` 底下的 `src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`(分析行程,不是執行行程)。逐條判:

- `Systems/執行迴圈.md`、`Systems/外部寫入嘗試紀錄.md`、`Systems/任務流程領域模型.md`、`Systems/共用行程基礎.md`:牽連檔全部是 executor 側,這次 diff 一行都沒碰,不影響。
- `Systems/Mock-DSP.md`:牽連檔同樣是 executor 側,程式碼不重疊;但其 ★INVARIANT★「同一把冪等鍵最多只套用一次…並行搶同一把鍵也只有一個真的套用」「一次寫入操作要嘛全部生效、要嘛全部沒發生」是這次分析側租約設計明講借用的對照組——去 `rtb-3b` 讀到的家節點 `分析行程流程與檢查點.md` 裡寫「概念照執行側(租約序號、擁有者、到期、收據、條件寫入),只換寫法」,以及「帶收據的提交只核對『目前那一列是我的取得列』,不核對到期時間(跟執行側 [S101] 刻意不同)」——後者是**刻意的設計偏離**,不是抄漏了,已經在第 1 輪設計審裡定案,我讀程式碼確認 `commit_step`/`_lease_allows` 確實沒有核對到期時間,跟筆記講的一致。概念相關,程式碼不相關,不需要為這次 diff 另外動這篇筆記。
- `Systems/提案收件口.md`:牽連檔是 executor 側,不影響。
- `Systems/分析行程流程與檢查點.md`:這才是這次 diff 兩支檔案(`task_store.py`、`flow.py`)真正的家,但它在**這個 repo(`rtb-production-agent-demo`)裡的版本停在 2026-09-22**,還沒有任何租約/Phase 4 增量 3b 的內容(`updated: 2026-09-22`),所以 LUMOS-IMPACT 才會判它「間接相依」而不是直接命中——這個 repo 的圖譜還沒追上 `rtb-3b` 那個工作分支。去標的 repo `/Users/enzo/rtb-3b` 讀同名節點,`updated: 2026-09-23`,裡面的 RULE/PITFALL(租約表只增不改、`advance()` 取得-重讀-放掉的完整條件、帶收據不核對到期、放掉要核對「還是我的」、租約長度沒有機械守衛且已標 `REVISIT:2026-12-31`)逐條核對程式碼都對得上,是同一次工作已經寫回的筆記(`RULE:` 欄位齊全、`[since:2026-09-23]` 都有)。不算矛盾,只是提醒:這篇筆記若之後要併回 `rtb-production-agent-demo` 這個 repo,兩邊版本要記得合併,不在這次程式碼審查範圍內。

## 總結
severity: minor
blocking 條數:0(F1 為 minor 且不擋)。多執行緒與多行程的租約互斥、讀-判-寫的交易原子性、過期接手與遲來放掉的邊界,壓測與程式碼核對都與作者宣稱一致,沒有重現任何雙寫、資料錯亂或吞掉不該吞的例外。
