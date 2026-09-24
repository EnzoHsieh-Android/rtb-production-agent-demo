severity: minor
## 審查報告 — code-phase9-inc2 第 2 輪:新測試殺傷力與既有測試改動

severity: minor

已讀取 `/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/p9i2-fmt.md` 並照抄格式。以下是完整報告全文。

---

### 方法
在 `/tmp/rtb-mut` 建 repo 副本(rsync 排除 `.git`,另建乾淨 git 供每次變異後 `git checkout --` 復原,全程未動 `/Users/enzo/rtb-p9i2`)。確認 `/Users/enzo/rtb-p9i2` 的 HEAD(`fc2c3d6`)相對 base(`40b2a01`)的完整 diff 與 `r2-delta-src.patch` + `r2-delta-tests.patch` 完全對應,故直接在副本上對 14 條修正逐一「拿掉/復原成第 1 輪的錯誤行為」,重跑對應新測試確認翻紅;讀取用 `/Users/enzo/rtb-p9i1/.venv/bin/python`。基準:`/Users/enzo/rtb-p9i2` 全量 1619 測試在只讀模式下全過。

### 一、14 條修正的新測試殺傷力(逐條變異結果)

| 修正(對應第 1 輪 finding) | 變異操作 | 對應測試 | 結果 |
|---|---|---|---|
| formula-1/x1-1 執行延遲改依窗內終點事件分段 | `_execution` 改回用 `w.part.finals` 全域最後終點 | `test_execution_latency_is_segmented_by_each_terminal_event_in_the_window`、`test_stale_rejections_...`、`test_window_bounds_...` | 翻紅(3 支) |
| x1-2 程式版本值域只收窗內貢獻樣本 | `seen` 改回加回 `part.finals.values()` | `test_program_versions_come_only_from_records_in_the_window` | 翻紅 |
| formula-3 政策版本標籤不跟查詢當下常數比 | `_Resolver.policy` 改回 `"current" if value == POLICY_VERSION` | `test_policy_version_labels_of_a_past_window_survive_a_policy_change`、`test_metric_labels_and_values_are_bounded` | 翻紅(2 支) |
| reads-1(`task_store._iso`)時間一律要帶時區 | 拿掉 `is_aware` 檢查 | `test_record_tool_call_swallows_database_errors_but_not_programming_errors`、`test_window_readers_refuse_times_without_a_time_zone` | 翻紅(2 支) |
| formula-2/x1-3(`metrics._check_window`) | 拿掉 `_require_aware(since, until)` | `test_metric_times_must_carry_a_time_zone` | 翻紅(改丟 `TypeError` 而非預期 `ValueError`,仍算翻紅) |
| 同上(`collect_snapshot`) | 拿掉 `_require_aware(now)` | 同上 | 翻紅 |
| 同上(CLI `_aware_time`) | 拿掉 `is_aware(value)` 檢查 | 同上(CLI 段) | 翻紅,且重現「裸例外炸出 CLI、非乾淨 `SystemExit`」 |
| spec-1 讀到穩定為止只套端到端 | `collect_window` 改回整份窗內統計都套進三輪比對 | `test_only_end_to_end_is_reread_until_stable` | 翻紅(stable/rounds 從 (True,2) 變 (False,3)) |
| arch-2 事件次數 vs 提案數的附註 | `_event_counts` 拿掉 `EVENT_COUNT_NOTE` | `test_metrics_cover_the_handoff_list_without_writing` | 翻紅 |
| arch-3 `ToolEndpoint` 型別錯改 `ValueError` | `record_tool_call` 改回 `raise TypeError` | `test_analyzer_tool_call_endpoints_are_a_closed_list` | 翻紅 |
| reads-2 缺新索引視同沒升級 | `sqlitekit.missing_schema` 拿掉索引檢查段 | `test_a_read_only_open_without_the_time_indexes_counts_as_not_upgraded` | 翻紅 |
| tests-1 窗界含起點不含終點(Python 層) | `_Window.inside` 改成 `<=` | `test_window_bounds_include_the_start_and_exclude_the_end` | 翻紅(第 1 輪同類變異 18/18 全過,這次翻紅) |
| tests-1 窗界含起點不含終點(SQL 層) | `lifecycle_events_between_query` 改成 `at <= ?` | `test_window_readers_include_the_start_and_exclude_the_end` | 翻紅(第 1 輪同類變異連 609 支全過,這次翻紅) |
| tests-2 端到端鏈尾檢查 | `_end_to_end` 拿掉 `task != chain.last or` | `test_end_to_end_latency_follows_the_follow_up_chain_once`(新增 `f1` 較晚結案的資料點) | 翻紅(720→840;第 1 輪同一支測試曾測不出此變異) |
| tests-3(minor)範例並列取結束時間最新 | `_exemplars` tie-break 拿掉 `.at` 次序鍵 | `test_metric_exemplars_are_real_members_of_the_sample`(新增 beta 租戶 4 筆同耗時資料) | 翻紅 |

arch-1(`version_conflict_rate` 另開一路讀 `dsp_calls`)第 1 輪判「照規格 [S645] 駁回」;確認 r2 沒有改這段(`src/rtb/ops/metrics.py:734` 仍是 `if call.error == DspErrorCode.VERSION_CONFLICT:`),與駁回一致,不需要新測試。

**結論:14 條修正對應的新測試逐一變異驗證,全數翻紅,沒有發現「補了測試但殺不死回歸」的情況。**

### 二、既有測試預期被改的地方(逐項核對)

1. **[S649] 執行延遲預期**(`execution_seconds.max` 240 秒→120 秒,count 1→2):手算 `t1` 生命週期——死信段 1→2 分(60 秒)、重放後 33→35 分(120 秒),兩段都落窗內、死信等待不落在任一段內——與新語意精確對應,不是調鬆測試遷就實作。合理。
2. **[S631] 政策版本值域**(`{CURRENT, OTHER}`→`{*KNOWN_POLICY_VERSIONS, OTHER}`):新增測試用 `monkeypatch` 模擬部署改版(`POLICY_VERSION`、`KNOWN_POLICY_VERSIONS` 同步換新值),斷言舊窗標籤不變,直接命中 formula-3 原本抓到的「過去窗因日後改版變標籤」。合理。
3. **[S637] 例外型別**(`TypeError`→`ValueError`,match `"ToolEndpoint"`):與執行端既有封閉列舉檢查(`ack_blocked`/`_log_held`/`release`)慣例一致。合理。
4. **分析端 `test_task_store` 例外從 `AttributeError` 改 `ValueError`**:原斷言綁死在「字串沒有 `.astimezone`」這個實作細節上;改成統一驗證「沒帶時區」的 `ValueError`,且同時覆蓋字串與 naive datetime 兩種錯誤輸入,契約層級更清楚、覆蓋面比原本更寬。合理。
5. **缺參數結束代碼 2 改 7**:行為正確,但測試覆蓋有缺口,見下方發現。

### 三、發現

#### 1. CLI 真正缺少必要旗標 / `_parse()` 自己的語意組合檢查,沒有測試直接斷言結束代碼是 7
severity: minor
blocking: 否

引句:「EXIT_BAD_ARGUMENTS = 7  # 參數錯(缺參數、時間沒帶時區);argparse 預設的 2 跟資料庫檔不存在撞號」

`tests/ops/test_metrics.py` 裡唯一驗證 `m.EXIT_BAD_ARGUMENTS` 的地方是 `test_metric_times_must_carry_a_time_zone`(file: `tests/ops/test_metrics.py:581-616`),三組輸入都是「時間沒帶時區」,都經 `_aware_time` 丟 `argparse.ArgumentTypeError` 後走到 `_Parser.error()`。沒有任何測試對真正漏給必要旗標(如漏 `--executor-db`/`--tenants-config`,觸發 argparse「the following arguments are required」)或 `_parse()` 自己的語意組合檢查(`parser.error("窗內統計要 --since、--until 與 --analyzer-db;現況快照要 --now")`,file: `src/rtb/ops/metrics.py:902`)斷言過結束代碼。

觸發情境:操作者少打 `--tenants-config`,或只給 `--since` 沒給 `--analyzer-db`。
會出什麼錯的行為:目前這兩條路徑都經同一個 `_Parser.error()` 覆寫(file: `src/rtb/ops/metrics.py:873-878`),手算驗證(在 `/tmp/rtb-mut` 直接呼叫 `m.run(["--tenants-config","x.json"], ...)`)確實回 `SystemExit(7)`,現狀不是錯的行為;但因為沒有測試釘住這兩條路徑,日後若有人把「缺必要旗標」判斷或那句語意組合檢查改成繞過 `_Parser.error()`(例如換回一個沒覆寫過的 `argparse.ArgumentParser`,或該檢查改成直接 `raise SystemExit(2)`),不會有任何測試翻紅,回歸不會被抓到——手算驗證過:把 `_parse()` 裡 `_Parser(...)` 換回 `argparse.ArgumentParser(...)` 會被現有 `test_metric_times_must_carry_a_time_zone` 間接抓到(因為它也走 `--since` 格式錯誤路徑),但若只單獨動「缺必要旗標」或那句語意組合檢查本身,現有測試組完全沒有觸及。
建議修法:在 `test_metric_times_must_carry_a_time_zone`(或另開一支)補一組「漏給 `--tenants-config`」與「只給 `--since` 不給 `--analyzer-db`」的 `m.run(...)` 呼叫,斷言 `SystemExit.code == m.EXIT_BAD_ARGUMENTS`。

---

材料以外查證用到的檔案:`src/rtb/ops/metrics.py:77,734,873-878,900-916`、`tests/ops/test_metrics.py:508-539,581-616`、`tests/analyzer/test_task_store.py:92-107`、`src/rtb/domain/_checks.py:29-34`(`is_aware` 實作)、`/Users/enzo/rtb-p9i2` 與 `/tmp/rtb-mut` 的 pytest 執行紀錄(1619 測試基準全過;14 條變異各自對應測試翻紅)。
