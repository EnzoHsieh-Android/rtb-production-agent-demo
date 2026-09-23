severity: major

### 1. 「已交給執行」重送機制在收件表保留期之後,會把已經成功的任務誤判成過期擋下

severity: major
blocking: 是 這條完成標準本身要求「衝突可查(軌跡、稽核)」,而這個路徑會讓一個真正執行成功的任務永久卡在錯誤的終點狀態,審不出真相,必須在動工前決定怎麼補(延長對照時間或另找真相來源),不是實作細節。
引句:「推進函式替「已交給執行」加一步:把存著的那份提案快照原樣重送一次」
說明:設計把「重送同一份提案給收件口、讀回應本文的處置」當成判斷「已交給執行之後結果如何」唯一的管道。但收件口自己的合約明講收件列只在保留期(2 小時)內存在,清掉之後同一個 (task_id, revision) 的重送會被當成全新提案處理,而提案的決策存活期上限只有 1 小時,保證早於保留期到期。快照裡也承認這個交互:「收件表保留期(2 小時)長於提案最長壽命(1 小時,模組載入時有自我檢查),所以保留期清掉的提案重送會被判過期」——但這段話只從「避免任務永遠卡住」的角度討論,沒有處理「這筆提案其實已經成功交出、甚至已經驗證寫入 DSP」的情況:HANDED_OFF 處置不算 OPEN,一樣會被保留期清掉。
- 輸入→預期:任務 t1 的提案在 T0 送進收件口,執行端隨後把它寫進 DSP 並驗證成功,收件口把 (t1, revision 1) 標成已交給執行。呼叫端(Phase 5 明講不做啟動程式與排程)直到 T0+2.5 小時才第一次對 t1 呼叫 advance() 走「已交給執行之後的下一步」。按 [S301],應該轉 COMPLETED。
- 實際:此時該筆收件列已因保留期被 `_purge_finished_tasks` 整個清掉;重送等同一份全新提案,`decision_expires_at` 必然已過期,收件口回 422 expired_proposal → `SubmitStale`;依快照「收件口拒收「提案已過期」…→任務轉擋下,記原因」,t1 被轉成 BLOCKED(過期),而不是它真正達到的 COMPLETED。BLOCKED 是終點狀態沒有出路(`task_state.py` 的 ★INVARIANT★),這筆稽核紀錄從此回不去正確答案,而且依 [S304]「不建接續任務」,也沒有任何後續機制會發現或修正。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/inbox_store.py:37`(RETENTION = 2 小時)
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/inbox_store.py:407-419`(`_purge_finished_tasks`:HANDED_OFF 不算 OPEN,一樣被清)
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/domain/proposal.py:32`(`MAX_DECISION_LIFETIME = timedelta(hours=1)`)
file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md:52`(既有合約:「收件鍵只在保留期限內唯一…所以 Phase 3 的執行端不能只靠這個組合當跨時間的唯一識別」——這條原本針對執行端寫的限制,Phase 5 設計把分析端的「查結果」也建在同一個會過期的管道上,踩到同一個坑)

### 2. 接續關係表的寫入沒有比照專案既有「只增不改表」的去重保證,當機重跑會留下重複列

severity: major
blocking: 是 這張表是「衝突可查」完成條件的直接載體,而且它的寫入時機被快照自己的當機重跑範例(S306)點名要處理,缺這個保證等於那個範例本身沒有被滿足。
引句:「接續關係存在任務模組一張只增不改的小表(接續任務編號、原任務編號)」
說明:專案裡現有的「只增不改表」(任務歷史表 `tasks`、任務租約表 `task_leases`)全部靠「在同一個交易裡核對序號/租約收據、對不上就整段不寫」達到並行安全與精確一次;`commit_step` 更是刻意把一步要一起落地的東西(狀態、證據、提案、error_detail)全部塞進同一個原子交易,就是為了不必另外處理「半套」的中間態。快照裡「先建接續、再結案原任務:兩步之間當機,下一次推進原任務會重送、再看到版本已變、再建一次(什麼都不做)、再結案」這段,只講到 `create_task` 本身冪等(同編號同廣告再建一次沒事),完全沒提接續關係表這一列要怎麼避免在重跑時被「再新增」一次——沒有主鍵或條件寫入,字面上「一張只增不改的小表」+「再建一次」就是每次重跑都多寫一列。這是專案裡第一張沒有搭配序號/收據去重就宣稱可以安全重跑的「只增不改表」,屬於引入了本專案原本沒有的第二種做法,而不是沿用既有模式。
- 輸入→預期:t1 在 HANDED_OFF 重送後收到 BLOCKED/VERSION_CHANGED;推進函式依序「建接續任務 t1.r2」→「在接續關係表新增一列 (t1, t1.r2)」→ 準備把 t1 轉 BLOCKED 時當機。下一次呼叫 advance(t1):重送仍收到 BLOCKED/VERSION_CHANGED,`create_task(t1.r2, ...)` 冪等不寫新列,但依快照字面「再建一次」再跑一次接續關係表的新增,然後才成功把 t1 轉 BLOCKED。依 [S306]「重跑推進應不重複建接續任務」的精神,關係表裡 t1→t1.r2 應該只有一列。
- 實際:字面實作會在關係表留下兩列一模一樣的 (t1, t1.r2),稽核查「這個原任務接到哪個接續任務」時看到重複紀錄,無從單靠這張表分辨是真的重新規劃了兩次還是同一次的當機重跑殘留。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/analyzer/task_store.py:233-292`(`commit_step`:專案既有「一步要一起落地的東西塞進同一個原子交易、用 expected_seq/租約收據去重」的做法)
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/analyzer/task_store.py:186-202`(`create_task`:唯一一個在快照裡被講清楚「冪等、同編號同廣告再建一次什麼都不做」的部分,關係表沒有對應說明)
