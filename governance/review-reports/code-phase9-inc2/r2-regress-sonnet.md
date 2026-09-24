severity: major
# 完整報告全文

severity: major

# code-phase9-inc2 第 2 輪驗收與回歸(2026-09-24)

受審:c5a34da..HEAD(單一提交 fc2c3d6,對照 governance/review-reports/code-phase9-inc2/r2-delta-src.patch)。
跑法:`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`,1619 個測試全過。另在 /tmp/p9i2-review-fk92
用讀原始碼 + 手算重現(部分含突變測試:改回原本錯誤的邏輯後重跑測試,確認新補的測試真的會翻紅)逐條驗收。

## 一、逐條驗收(r1 折入的 14 條)

### 1. execution_seconds 用全域最後終點事件歸窗(formula-1 / x1-1)
已修。`_execution`(`src/rtb/ops/metrics.py:634`)改成走 `w.executions`,而 `_executions`
(`src/rtb/ops/metrics.py:626`)是用 `_segments`(`src/rtb/ops/metrics.py:609`)從窗內事件本身切出的處理段,
依「段的終點事件時間」歸窗(`since <= end.at < until`),不再回頭查 `finals` 的全域最後一筆。用
`test_execution_latency_is_segmented_by_each_terminal_event_in_the_window` 重現原始情境(死信後窗外重放
成功):修正後窗一的 execution_seconds 樣本在重放前後完全不變。

### 2. 版本值域把窗外的全域最後終點也收進去(x1-2)
已修。`_compute_window`(`src/rtb/ops/metrics.py:753` 起)建 `seen` 改成只收 `executions`(窗內處理段的終點)、
`part.calls`(本來就是窗內查詢)、`part.reconciled` 裡「確實有 `AttemptState.UNKNOWN` 歷史、真的會出樣本」的
那幾筆,不再收全域 `part.finals`。比原本建議的修法(只加 `w.inside()` 過濾)更進一步,把值域收斂到「真的會
被貼上 `PROGRAM_VERSION` 標籤的那幾種紀錄」,不多不少。`test_program_versions_come_only_from_records_in_the_window`
驗過窗外重放不會把窗內版本擠成 other。

### 3. 政策版本標籤跟查詢當下的常數比(formula-3)
已修。`_Resolver.policy`(`src/rtb/ops/metrics.py:260`)改成回傳版本字串本身(只收
`KNOWN_POLICY_VERSIONS` 這份只增不刪的清單,`src/rtb/domain/proposal.py:29`),不再跟模組級的
`POLICY_VERSION` 常數比。`test_policy_version_labels_of_a_past_window_survive_a_policy_change` 用
monkeypatch 模擬改版,驗過舊窗標籤不變;另有 `assert POLICY_VERSION in KNOWN_POLICY_VERSIONS` 當回歸網,
往後真的改版忘記加清單會直接讓測試翻紅。

### 4. 無時區時間丟未捕捉例外(formula-2 / reads-1 / x1-3)
已修。新增 `_require_aware`(`src/rtb/ops/metrics.py:88`)在 `_check_window`、`collect_snapshot` 入口就擋;
CLI 的 `--since`/`--until`/`--now` 改用 `_aware_time`(`src/rtb/ops/metrics.py:881`)在 `argparse` 解析當下
就驗證時區,搭配 `_Parser`(`src/rtb/ops/metrics.py:873`)把參數錯誤統一導向固定結束代碼;`task_store._iso`
(`src/rtb/analyzer/task_store.py:215-220`)也比照 `attempt_store` 補了 `is_aware` 拒收。混用有時區/無時區的
情況現在在 `_require_aware` 就先擋下,不會再跑到比較式才丟 `TypeError`。四支窗口讀取函式(含分析端)現在
拒收行為一致,`test_window_readers_refuse_times_without_a_time_zone`、`test_metric_times_must_carry_a_time_zone`
兩邊都測了 CLI 與函式入口。

### 5. 讀到穩定的迴圈套用到整份窗內統計(spec-1)
「照規格把重讀範圍收斂到端到端」這件事本身已修:`collect_window`(`src/rtb/ops/metrics.py:787` 起)執行端
現在只讀一次(`part`/`analyzer` 固定用第一輪的快照),之後 `for rounds in range(2, MAX_ROUNDS + 1)`
(`src/rtb/ops/metrics.py:801`)只重讀端到端要的兩份輸入、只比對端到端樣本。`test_only_end_to_end_is_reread_until_stable`
驗過非端到端指標只讀一次、無關寫入不會誤判不穩定。但這個修法本身在「多快照混用」上引入了新的自相矛盾,
見下方「二、新發現的回歸」第 1 條——這件事雖然按規格收斂了範圍,但沒有真正達成「所有數字互相一致」的
初衷,只是換了一種不一致的形式,判為沒完全修乾淨,詳見下方。

### 6. 窗界邊界完全沒有測試覆蓋(tests-1)
已修。補了 `test_window_bounds_include_the_start_and_exclude_the_end`
(`tests/ops/test_metrics.py`)與 `test_window_readers_include_the_start_and_exclude_the_end`
(`tests/ops/test_window_readers.py`),事件時間精確等於起訖點都造了資料。實測:把 `_Window.inside`
(`src/rtb/ops/metrics.py:447`)改成 `since <= at <= until`(終點也算)重跑,`test_window_bounds_include_the_start_and_exclude_the_end`
翻紅(`final_outcome_rate` 從 `(1, ("t1",))` 變成 `(2, ("t3","t1"))`),證實邊界迴歸現在會被抓到。

### 7. 端到端鏈尾檢查被拿掉測試仍綠(tests-2)
已修。`test_end_to_end_latency_follows_the_follow_up_chain_once` 補了一筆「非鏈尾任務(f1)自己的最新修訂
結案時間反而晚於真正鏈尾(f2)」的資料(`tests/ops/test_metrics.py` 第 370 行附近)。實測:把
`_end_to_end`(`src/rtb/ops/metrics.py:687`)的 `task != chain.last or` 拿掉重跑,該測試翻紅
(`end_to_end_seconds.max` 從 `(1, 720)` 變成 `(1, 840.0)`,證實鏈尾檢查真的在守,不是靠「挑最新」矇混過關。

### 8. 延遲範例並列規則沒有測試(tests-3,minor)
已修。`test_metric_exemplars_are_real_members_of_the_sample` 補了租戶 beta 四筆耗時完全相同(30 秒)、
結束時間不同的樣本(`tests/ops/test_metrics.py` 第 318 行附近)。實測:把 `_exemplars`
(`src/rtb/ops/metrics.py:278-281`)的排序鍵從 `(x.value, x.at)` 改回只比 `x.value`(拿掉 tie-break)重跑,
該測試翻紅(`queue_wait_seconds.max` 的 exemplars 從 `("b2","b1","b3")` 變成 `("b0","b1","b2")`)。

### 9. blocked/awaiting_approval/approval_released 跟 observability.py 是兩套算法(arch-2)
已修(依 r1 折入的處置,不是要求統一算法,而是要求兩邊定義寫清楚)。`_event_counts`
(`src/rtb/ops/metrics.py:456-459` 的文件字串)已明講這四項是「事件次數,同提案重投再擋算兩次」,跟
`observability.py` 的停下紀錄(同提案同種類只記一次)定義不同;每個樣本也真的帶上 `EVENT_COUNT_NOTE`
(`src/rtb/ops/metrics.py:78`)。`test_metrics_cover_the_handoff_list_without_writing` 斷言四個樣本的
`note` 都含「事件次數」三字。`duplicates_prevented` 正確地沒有被塞這個附註(它不是在跟 observability.py
的東西比較)。

### 10. ToolEndpoint 型別檢查丟 TypeError,既有慣例丟 ValueError(arch-3,minor)
已修。`record_tool_call`(`src/rtb/analyzer/task_store.py:586-587`)改成丟 `ValueError`,測試
`test_analyzer_tool_call_endpoints_are_a_closed_list`(`tests/analyzer/test_tool_call_endpoints.py:24`)
也同步改成 `pytest.raises(ValueError, match="ToolEndpoint")`,跟執行端既有列舉檢查慣例一致。

### 11. 新索引只在寫入開法第一次開啟時建立,唯讀開舊庫查詢會退化成全表掃描(reads-2,minor)
已修。`missing_schema`(`src/rtb/sqlitekit.py:107-123`)新增 `indexes` 參數,對照 `sqlite_master` 查缺的
索引;`ReadOnlyInbox`(`src/rtb/executor/inbox_store.py:287-288`、`:1523`)與 `TaskReader`
(`src/rtb/analyzer/task_store.py:608`、`:617`)都補了各自的 `_REQUIRED_INDEXES`,缺了就丟
`DatabaseNotUpgraded`。因為 `connect()`(`src/rtb/sqlitekit.py:32-40`)每次寫入開法都會重跑
`executescript(schema)`(裡面是 `CREATE INDEX IF NOT EXISTS`),只有「純粹只被唯讀開過」的舊庫才會踩到這個
擋——用過寫入開法開過一次就會自癒,不會誤擋已經正常運作、只是唯讀端還沒重開過的資料庫。
`test_a_read_only_open_without_the_time_indexes_counts_as_not_upgraded` 逐一 DROP 每個索引驗過。

## 二、新發現的回歸

### 1. 端到端延遲收斂到只重讀自己之後,同一份報告裡端到端與其他指標可能出自不同時間點的快照,互相矛盾
severity: major
blocking: 是
引句:「current = _end_to_end(start, end, ends, chains)」
file: `src/rtb/ops/metrics.py:800-810`

`collect_window`(`src/rtb/ops/metrics.py:787` 起)現在只有端到端(`_end_to_end`)會在穩定迴圈裡重讀執行端與
分析端;`final_outcome_rate`(`src/rtb/ops/metrics.py:507-521`,依 `w.part.finals`)、`terminal_event_rate`
(`src/rtb/ops/metrics.py:490` 起,依 `w.part.events`)這些「其餘指標」永遠固定用第一輪(`part`/`analyzer`)
的快照,不會再讀第二次。這正是 spec-1 要的收斂——但收斂的代價是:回傳的**同一份** `Report` 裡,
`end_to_end_seconds` 可能用的是第 2、3 輪(較新)讀到的執行端資料,而 `final_outcome_rate`/`terminal_event_rate`
永遠停在第 1 輪(較舊)的資料,兩者不再保證出自同一個快照。

觸發情境:執行迴圈持續在跑(正式環境常態)。呼叫 `collect_window` 查一個窗,第一輪讀到窗內只有 `r1` 一筆
`handed_off`。在端到端重讀分析端接續鏈的期間,執行端又寫入了兩筆全新的、窗內完成、且分析端也接得上鏈的
任務 `e1`、`e2`(第一次取件、交給執行都發生在同一份呼叫期間)。端到端的重讀迴圈會撈到 `e1`、`e2`,判定
三輪都不同(`stable=False`,3 輪跑完),回傳的 `end_to_end_seconds` 樣本因此含 `e1`、`e2`;但同一份報告裡
`terminal_event_rate`、`final_outcome_rate` 完全不知道 `e1`、`e2` 存在(它們的 `handed_off` 事件是在第 1 輪
讀完之後才寫入的)。

手算驗證(在 `/tmp/p9i2-review-fk92/exp_snapshot_mismatch.py` 實跑,複製了 `tests/ops/rows.py` 的資料治具,
不動 repo):
```
stable: False rounds: 3
end_to_end_seconds.max samples: [(3, ('e2', 'e1', 'r1'))]
final_outcome_rate 樣本(count 加總): 1 [.., 1, ('r1',)]
terminal_event_rate 樣本(count 加總): 1 [.., 1, ('r1',)]
end_to_end 認得但 final_outcome_rate 不認得的任務: {'e2', 'e1'}
end_to_end 認得但 terminal_event_rate 不認得的任務: {'e2', 'e1'}
```
同一次呼叫、同一個窗,`end_to_end_seconds` 說窗內有 3 筆終點事件參與的鏈,`terminal_event_rate`(這是
模組文件自己聲明「事件型,窗過去就不變」、應該完整列出窗內每個終點事件的指標)卻只看到 1 筆——不是
「查詢當下最終結果會變」這種被明文允許的情況(那是指同一個窗**在不同時間**查兩次結果不同),而是
**同一次**查詢、**同一份**回傳結果內部自相矛盾。這比 spec-1 原本要修的問題(整份重讀成本高、誤判不穩定)
更嚴重:舊設計雖然貴,但保證回傳的一份報告裡所有數字出自同一對快照;新設計省了成本,卻讓「同一份報告
自相矛盾」這個 spec-1 自己點名要保留的性質(「換來所有數字出自同一對快照」)在端到端真的需要重讀時
(也就是唯一會觸發 3 輪重讀的情境)被打破。

建議修法:端到端重讀到穩定後,若確實發生過與第一輪不同的結果(即 `rounds > 2` 或 `current != 初次讀到的值`),
應該用「最後一輪」重讀到的執行端快照重新算一次**全部**指標(不只端到端),或者反過來限制端到端只能引用
第一輪 `part`/`analyzer` 快照裡已經存在的任務(重讀只用來偵測「這兩份快照組出來的結果穩不穩」,不能讓
穩定迴圈本身成為引入「快照外任務」的旁門);兩者擇一,但不能讓「端到端看得到、其他指標看不到」的任務
同時出現在同一份回傳結果裡。

## 三、任務指定的其餘回歸檢查點(未發現新問題)

- **多次重放、待核可、租約被接手混合情境下的分段是否正確**:在 `/tmp/p9i2-review-fk92/exp_segments.py`
  用讀件→租約被接手(reclaimed,不重開段)→進待核可→核可放回→死信→重放→再讀件→交給執行 的完整鏈路
  實跑,`execution_seconds.max` 正確得到兩段各 240 秒(第一段 360 秒扣掉待核可 120 秒;第二段原始
  240 秒),`dead_letter_wait_seconds`(840 秒)、`approval_wait_seconds`(120 秒)都各自歸位、沒有
  跨段重複扣或漏扣。
- **等待扣除的邊界重疊**:`_overlap`(`src/rtb/ops/metrics.py:600-606`)用半開區間 `low < high` 判斷,
  死信等待的起點正好是前一段的終點、重放後第一次取件正好是下一段的起點,兩邊算出來 `low == high` 都不
  觸發疊加,經上一點的實跑驗證過沒有重複扣也沒有漏扣。
- **政策版本已知清單的標籤有界性**:`KNOWN_POLICY_VERSIONS` 是原始碼裡的固定 tuple(`src/rtb/domain/proposal.py:29`),
  不是外部輸入或執行期可變資料,值域仍然有界;`_Resolver.policy` 對不在清單裡的值一律歸 `other`。
- **結束代碼 2 改 7 對既有呼叫者的影響**:全庫搜尋除了 `tests/` 與文件外沒有其他程式碼呼叫
  `rtb.ops.metrics`(這是 Phase 9 增量 2 新模組,尚未接進任何排程腳本或 CI),既有測試已同步改成斷言
  `EXIT_BAD_ARGUMENTS`;文件(`docs/rtb-production-agent-demo-knowledge/Systems/有界標籤的指標.md:38`)
  的結束代碼表也已更新,沒有發現既有呼叫者會被撞號影響。
- **缺索引視同沒升級會不會誤擋**:見上方「一、11」,寫入開法每次都會重跑 `CREATE INDEX IF NOT EXISTS`,
  只有純唯讀開過的舊庫會踩到,不會誤擋已經自癒過的資料庫。

## 測試

`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`:1619 個測試全過(在 /Users/enzo/rtb-p9i2
本體跑,未修改任何檔案)。突變測試(改回原本的錯誤邏輯後重跑)在 /tmp/p9i2-review-fk92 的暫存副本進行,
未使用 git 指令、未動 repo 任何檔案,實驗結束後已清除暫存副本。
