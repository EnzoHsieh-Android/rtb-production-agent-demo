severity: clean

## 審查方法與嘗試過的攻擊輸入

逐 hunk 讀完 `src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`、`tests/analyzer/test_task_lease.py` 的完整 diff(`/Users/enzo/rtb-3b/governance/review-reports/code-phase4-analyzer-lease/r1-snapshot.patch`),並在 `mktemp -d` 臨時目錄(複製 `src`/`tests`,未動 repo)裡對以下輸入逐一構造重現,想找到能翻紅的案例:

1. **任務第一次取得租約、沒有任何租約列**:`acquire_lease` 的 `current is None` 分支,`next_seq=1`。用臨時 DB 直接呼叫確認正常取得。
2. **租約序號接在放掉列之後**:放掉列 `owner=NULL`,`_is_live` 因 `lease[1] is not None` 為 False 判定非存活,下一次 `acquire_lease` 用 `current[0]+1` 接續序號。用 16 條真執行緒、40 輪、共用一個 DB 檔的壓力測試(`decide`/`submit` 各記一次呼叫)跑完整條 RECEIVED→HANDED_OFF,歷史剛好 5 列、`decide`/`submit` 各恰好呼叫 1 次、0 個例外 — 沒有翻紅。
3. **同一個 `now` 連續推進**:壓力測試每輪都用接近但略有抖動的 `now`,取得/放掉序號正確遞增,未見亂序。
4. **`>` vs `>=` 到期邊界**:`_is_live` 用 `lease[2] > _iso(now)`,在 `now` 恰好等於 `expires_at` 那一刻視為「已過期」(可被接手)。這是設計選擇不是矛盾——`_iso()` 對兩邊都先轉成同一種固定寬度 UTC 字串再比大小,時區換算已在函式內完成,不存在跨時區比錯的問題。測試套件只驗了 `AFTER_EXPIRY`(到期後 1 秒),邊界那一刻沒有專門測試,但我構造不出「剛好等於到期」會導致錯誤結果(不是雙重持有、不是寫壞資料)的案例,只是行為在文件裡沒明講含頭不含尾——不夠格當 blocker/major,如實記錄、不標。
5. **`commit_step` 在租約核對前後丟例外**:直接呼叫 `TaskStore.commit_step(..., lease=held_lease)` 觸發一個 `IllegalTransition`(在租約核對「之後」的交易內丟出)。確認 `immediate_transaction` 正確回滾,交易結束後 `_holds(lease)` 仍是 `True`(沒有半途寫入、租約收據沒被吃掉),流程層 `advance()` 外層的 `except BaseException` 之後呼叫 `release_lease` 一樣能成功放掉。沒有找到殘留鎖死或偽寫入。
6. **`transition()` 丟非法轉換時租約的去向**:`_advance_holding` 裡 `transition(row.state, outcome.new_state)` 在 `commit_step` 之前、在 `advance()` 的 `try` 範圍內呼叫,丟出即被 `except BaseException: _release_keeping_the_original_error(...); raise` 接住,租約會放掉、原例外原樣往外傳。驗證與 docstring 一致。
7. **`advance()` 回傳值語意**:逐一核對「沒有進展」(`outcome is None`)、「拿不到租約」、「重讀後序號變了(被搶先)」、「輸了序號或租約的 `commit_step`」四條路徑,全部回傳原本讀到的 `row.state`(或重讀到的新狀態,交回呼叫端重來),沒有一條把外部呼叫的中間結果誤當成已提交狀態回傳,跟舊語意「沒進展回原狀態」「輸了競爭回原狀態」一致。

用真執行緒重現重度並行(16 threads × 40 輪同一個任務、含隨機睡眠製造交錯)全程 0 例外、0 重複外呼、歷史列數與實際推進步數精確相符 —— 沒能讓它壞掉,承認它對。

## 圖譜鏡頭

先說一個對不上的地方:題目附的 `LUMOS-IMPACT: c2aa8dcf92f12a4694f10e336592da3d60073e70..HEAD` 是對**這個 repo(`/Users/enzo/rtb-production-agent-demo`,`main` 分支,`aa4a5b8`)**算出來的,而這份要審的 diff 其實在**另一個 worktree/分支(`/Users/enzo/rtb-3b`,同一個 GitHub remote,現在在 `0583c59`)**,兩邊改的是不同功能(`main` 是「執行側多工作者」`57b32b7`;`rtb-3b` 是「分析側任務租約」)。所以自動附加的固定席節點裡,牽連檔列 `src/rtb/executor/attempt_store.py|execution.py|runner.py` 的那幾篇(`執行迴圈`、`外部寫入嘗試紀錄`、`Mock-DSP`、`共用行程基礎`、`提案收件口`)跟這份 diff 完全不相干——diff 只動了 `src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`,一行都沒碰 `src/rtb/executor/*`。逐條記為「不影響(檔案不重疊、屬於另一條分支的變更)」。

真正跟這份 diff 有交集的兩篇(在 `rtb-3b` 自己的知識庫裡才看得到最新版本):

- **`Systems/任務流程領域模型.md`**(領域層 `task_state.py`/`proposal.py`/`evidence.py` 的 ★INVARIANT★):diff 沒有改動領域層任何一支檔,只是呼叫既有的 `transition()`/`can_transition()`,而且 `_advance_holding` 在呼叫任何外部介面、寫入之前先用 `transition()` 驗證(丟例外即整段放棄、租約照放),沒有繞過這道防線。「終點狀態沒有出路」「白名單匯入」等合約沒被動到。不影響。
- **`Systems/分析行程流程與檢查點.md`**(這才是 diff 真正的家,`about_code` 就列著 `task_store.py`/`flow.py`):這篇在 `rtb-3b` 的最新版本已經把這次增量的 RULE/PITFALL 寫齊(`[since:2026-09-23]` 帶 `[retire:]`),包括「租約表只增不改、圍籬核對取得列」「帶收據不核對到期時間、不帶收據時擋別人持有中」「60 秒暫用長度沒有機械守衛」「放掉要核對『還是我的』,否則遲來的放掉會蓋過接手者」。逐條核對:實作行為跟這些 RULE 描述一致(見上面第 4、5 點的重現),既有的「每一步都要落地成新的歷史列」「送出永遠是已存好的提案快照」兩條 ★INVARIANT★ 也沒被破壞——`_from_proposed` 仍用 `row.proposal`(原本讀到那一列的快照),不是重新組一份;`kill_recipes` 裡綁定的 `if row is None or row[0] != expected_seq:` 序號核對整行都還在(`task_store.py:267`),沒被鬆動。不影響,反而是這條 PITFALL(「並行呼叫可能兩邊都花錢」)正是這次要補的洞,診斷與修法方向一致。

## 總結

severity: clean
blocking findings: 0。逐 hunk 攻擊邊界值、None/例外路徑、advance() 回傳語意、以及跟圖譜合約的交集,均未能翻紅或證出違反;唯一可記錄的是 LUMOS-IMPACT 自動附加的固定席節點對錯了分支(答非所問),已在圖譜鏡頭段落逐條說明为什么不影響此 diff。
