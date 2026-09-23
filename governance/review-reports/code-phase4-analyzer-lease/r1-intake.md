# code-phase4-analyzer-lease r1 收貨紀錄(2026-09-23)

九席全到後才讀、才動程式。Codex 兩席(finder、veto)報告從 `codex exec -m gpt-5.6-sol --sandbox read-only` 原始輸出截最終回覆存檔,內容不動;兩席都寫明唯讀沙箱建不了臨時目錄、沒實跑測試,只做逐 hunk 靜態核對。
圖譜鏡頭異常:派工標記行的 hook 以主倉(main,正在做執行側 3a)為工作目錄算牽連,附上的固定席是執行側節點,跟本分支分析側 diff 不重疊;正確性、併發、邊界、圖譜四席都在報告裡指出並改查本分支的 Systems/分析行程流程與檢查點.md 與 Systems/任務流程領域模型.md,判未破壞其規則與合約。

| id | 重現命令 | 輸出摘錄 | 結論 | 處置 |
|---|---|---|---|---|
| arch-F1 | `grep -n 'import sqlite3\|DatabaseBusy' src/rtb/analyzer/flow.py` | 18:import sqlite3、35:from rtb.sqlitekit import DatabaseBusy、192:except (sqlite3.Error, DatabaseBusy) | HIT | 折:吞錯搬進任務模組 `release_lease_quietly`,推進函式不再認得資料庫錯誤;新測試 test_the_flow_layer_never_names_database_errors |
| arch-F2 | `grep -rn uuid src`;`grep -n 'owner = ' src/rtb/executor/runner.py` | 全 src 只有 flow.py 用 uuid;執行側 owner = f"{os.getpid()}-{int(time.time())}" | HIT | 折:擁有者改「行程編號-執行緒-呼叫序號」;新測試 test_a_lease_owner_names_the_process_and_thread |
| arch-F3 | 讀 src/rtb/executor/inbox_store.py 的 _lease/release/take_over | 執行側動詞 _lease、extend、release、take_over | HIT | 折:acquire_lease 說明寫明對照執行側哪個動詞(本輪有 major,accepted 必空) |
| graph-F1 | 讀 flow.py 模組開頭說明 | 沒提租約 | HIT | 折:模組說明補租約一段 |
| graph-F2 | 讀分析行程筆記「commit_step 是所有寫入唯一的入口」RULE | 字面未排除租約表 | HIT | 折:RULE 補範圍 |
| edge-F1 | `store.acquire_lease('ghost','x',NOW)` 於空資料庫 | 回 LeaseReceipt 並寫一列 ('ghost', 1, 'x', …) | HIT | 折:沒有任務丟 TaskNotFound 不寫列;新測試 test_acquiring_a_lease_for_an_unknown_task_writes_nothing |
| race-F1 | 席位附的壓測(busy 0.05 秒、20 任務 × 15 輪沒進展) | 8 次 DatabaseBusy 從 advance 丟出 | HIT(採信席位重現;預設 5 秒未觀察到) | 折:分析行程筆記補 PITFALL 與 REVISIT:2026-12-31 量多任務輪詢下的忙碌頻率 |

refuted:none。
變異檢查(拿掉新防護、清 __pycache__、跑對應測試):不查任務在不在、擁有者不帶行程、擁有者每次相同、例外路徑不放掉、放掉失敗不吞、推進層又匯入 sqlite3——六道全紅。

引句錨定:race-F1 的引句取自派工詞裡的變更主題摘要、graph-F1/F2 的引句取自 diff 範圍外的模組說明與圖譜筆記,quote-check 判錨不到。三條都由編排者照上表機械重現為 HIT 才採信(不是只憑引句)。三份報告(correct、race、graph)跑過 `lumos report-normalize --write`,只搬檔級 severity 行的位置。
