severity: major
以下是本輪(第 3 輪,regress-sonnet 席位)完整報告全文。

---

severity: major

# code-phase9-inc2 第 3 輪驗收與回歸(2026-09-24,regress-sonnet 席)

受審:`r3-delta.patch`(fc2c3d6..HEAD,對照第 2 輪折入的 5 條發現、`r2-intake.md` 判讀為 4 件事)。
跑法:`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest` 在 `/Users/enzo/rtb-p9i2` 本體跑,1624 個測試全過(比第 2 輪 1619 多 5,對應這輪新增的測試)。另在 `/tmp/rtb-p9i2-review`(rsync 複製,自建 git)做逐條突變重現與一次端到端手算實驗,全程未動 `/Users/enzo/rtb-p9i2`。

## 一、逐條驗收(r2-intake.md 折入的 4 件事)

1. **端到端收斂後與其他指標拼成矛盾快照(regress-1/x1-1)**。已修。`collect_window`(`src/rtb/ops/metrics.py:781-803`)改成:`first = _end_to_end(...)` 讀第一輪;`samples = _compute_window(..., first)` 在重讀迴圈**之前**就把整份報告(含端到端)算好、鎖定第一輪;之後 `for rounds in range(2, MAX_ROUNDS+1)` 只用來確認端到端跟 `first` 是否一致,回傳的 `Report` 一律用同一份 `samples`,不論穩定與否。手算重現:把程式碼改回第 2 輪的寫法(`current == previous` 逐輪比、穩定時用 `current` 重算 `_compute_window`)重跑,新測試 `test_a_terminal_written_after_the_first_read_marks_the_report_unstable` 立刻翻紅(`(True, 3) != (False, 3)`),證實新測試真的守住這個修法,不是恰好綠。
2. **時區必帶檢查三處各寫一份(arch-1)**。已修。`src/rtb/domain/_checks.py` 新增 `AWARE_REQUIRED` 常數與 `require_aware(*moments)`;`attempt_store._iso`、`task_store._iso`、`metrics._check_window`/`collect_snapshot`/`_aware_time` 全部改成呼叫這一份,原本三份各自的 `if not is_aware(...): raise ValueError(...)` 都拿掉了。手算重現:把 `task_store.py` 改回本地 inline 判斷式(保留匯入 `require_aware` 但不用),`tests/domain/test_checks.py::test_time_zone_checks_are_not_written_again_elsewhere` 的 AST 掃描立刻翻紅(`assert (own, uses) == ([], []), path.name` 抓到 `is_aware` 又被引用),證實這條機檢真能防止未來漏掉哪一處。
3. **metrics 新結束代碼 7 沒同步套用到 trace.py(arch-2)**。已修。新增 `src/rtb/ops/cli.py`,把 `EXIT_BAD_ARGUMENTS = 7` 與 `class Parser(argparse.ArgumentParser)` 抽成共用模組;`metrics.py`、`trace.py` 都改成從這裡匯入(`trace.py:42-43`、`metrics.py:282-283`),`trace.py:493` 的 `_parse` 也改用 `Parser(...)`。手算重現:把 `trace.py:_parse` 改回 `argparse.ArgumentParser(...)`,新測試 `test_the_trace_command_line_reports_bad_arguments_apart_from_a_missing_database` 立刻翻紅(漏 `--task-id` 時結束代碼從預期的 7 變回 2,跟 `EXIT_NO_DATABASE` 撞號),證實這條測試真的守住兩支 CLI 結束代碼表一致。`docs/rtb-production-agent-demo-knowledge/Systems/追蹤檢視.md`、`.../有界標籤的指標.md` 也同步補了「兩支命令列共用同一個解析器」的說明。
4. **CLI 缺必要旗標沒有測試斷言結束代碼 7(tests-1)**。已修。`tests/ops/test_metrics.py` 新增 `test_missing_or_mismatched_flags_exit_with_the_bad_arguments_code`(漏 `--executor-db`、漏 `--tenants-config`、窗內統計漏 `--analyzer-db`、只給 `--since` 四種組合都斷言 `SystemExit.code == EXIT_BAD_ARGUMENTS`),`tests/ops/test_trace.py` 也新增對應測試涵蓋 `trace.py` 漏 `--task-id`/`--executor-db`/`--dsp-url`。

四件事逐條讀碼 + 突變重現,全數確認真的修好。

## 二、新發現的回歸

### 1. 端到端改鎖第一輪之後,不穩定時的維運訊息與模組頂部說明仍宣稱印的是「最後一輪」,跟實際回傳的報告內容自相矛盾

severity: major
blocking: 是

引句:「不改採較新的快照,後兩輪彼此相等也不算」
file: `src/rtb/ops/metrics.py:8-9`(模組頂部 docstring:「……最多三輪,仍不同就回最後一輪並標明不穩定。」)
file: `src/rtb/ops/metrics.py:77`(`EXIT_UNSTABLE = 5  # 三輪都讀到不同結果:照樣印出最後一輪,結束代碼標明不穩定`)
file: `src/rtb/ops/metrics.py:919`(`print("讀取期間有新提交:三輪讀到的結果都不同,印出的是最後一輪", file=errors)`)
file: `docs/rtb-production-agent-demo-knowledge/Systems/有界標籤的指標.md:38`(「5 讀取不穩定(照樣印最後一輪)」)

觸發情境:執行迴圈持續在跑(正式環境常態)。操作者下 `python -m rtb.ops.metrics --since ... --until ... --executor-db ... --analyzer-db ... --tenants-config ...` 查一個窗;第一輪讀完後、端到端重讀期間,執行端又寫入一筆窗內、分析端也接得上鏈的新任務。

會出什麼錯的行為:這批修正(第 2 輪折入的 regress-1/x1-1)把 `collect_window` 改成「整份報告(含端到端)一律鎖第一輪,重讀只用來確認端到端跟第一輪一致;都不一致也不改採較新快照」——這個新契約正確寫進了 `collect_window` 自己的 docstring(`src/rtb/ops/metrics.py:785-789`,本輪 patch 有改)。但同一支檔案裡另外三處(模組頂部 docstring、`EXIT_UNSTABLE` 常數旁注解、`run()` 真正印給操作者看的 stderr 訊息)以及外部文件 `有界標籤的指標.md:38`,都還停在第 2 輪之前「不穩定就改印最後一輪」的舊說法,這輪 patch 完全沒有觸碰。實跑重現(`/tmp/rtb-p9i2-review`,不動 repo):構造上述競態情境,`report.stable=False, rounds=3`;`end_to_end_seconds.max` 與 `terminal_event_rate` 的 exemplars 都只有 `('r1',)`,完全不含競態中新寫入的任務 `e1`;但依照 `run()` 現有邏輯,`stable=False` 時仍會印出「讀取期間有新提交:三輪讀到的結果都不同,**印出的是最後一輪**」。這句話明確宣稱印出的是「最後一輪」的資料,實際上印出的是「第一輪」——維運或 runbook 若照這句訊息判斷「現在看到的是最新讀到的那一輪」,會誤以為報告已經反映了新提交,實際上報告仍是舊快照,可能因此誤判資料已經追上、跳過該有的重試或標記。這屬於文件/訊息描述的行為被程式碑碼修正推翻後沒有同步更新,而且是使用者(操作者)真正會看到的一手文字,不是內部註解。

建議修法:把 `src/rtb/ops/metrics.py:8-9`、`:77`、`:919` 三處與 `docs/.../有界標籤的指標.md:38` 全部改成跟 `collect_window` docstring 一致的說法(例如「都不一致也不改採較新快照,照樣回第一輪」),並在 `run()` 加一支測試斷言不穩定時印出的訊息文字反映「仍是第一輪」而非「最後一輪」,避免下次修法再次讓某一處描述漏改。

## 三、新 minor(照寫,不放行判準)

### 2. metrics 直接呼叫 `require_aware` 的兩處(`_check_window`、`collect_snapshot`)遺失原本訊息裡的時間格式範例,僅 CLI 路徑補得回來

severity: minor
blocking: 否

引句:「時間必須帶時區(例:2026-09-24T01:00:00Z 或 2026-09-24T09:00:00+08:00)」
file: `src/rtb/domain/_checks.py:37`(`AWARE_REQUIRED = "時間必須帶時區"`,不含範例)
file: `src/rtb/ops/metrics.py:774`(`require_aware(since, until)`,直接呼叫,未包 try/except 補範例)
file: `src/rtb/ops/metrics.py:820`(`require_aware(now)`,同上)

觸發情境:直接呼叫 `rtb.ops.metrics.collect_window`/`collect_snapshot`(不經 CLI 的 `_aware_time`)且傳入無時區時間——目前全庫只有測試這樣呼叫,CLI 路徑因為 `--since`/`--until`/`--now` 已先經 `_aware_time` 攔截並補回範例文字(`src/rtb/ops/metrics.py:871-874`),不會走到這支訊息。

會出什麼錯的行為:第 2 輪修正曾在 `metrics.py` 自訂 `_require_aware(*moments)`,訊息帶時間格式範例;這輪合併進 `rtb.domain._checks.require_aware` 後訊息改成固定的「時間必須帶時區」,不再附範例。目前沒有非測試呼叫端受影響(全庫搜尋除 `tests/` 外沒有其他程式碼直接呼叫這兩支函式),但這是這輪合併帶來的可觀察行為變化,程式庫使用者(非 CLI)日後遇到會少一句「怎麼補時區」的提示。

建議修法:若要保留範例提示,讓 `_check_window`/`collect_snapshot` 也比照 `_aware_time` 包一層 try/except 補回範例文字;或者接受這是刻意的取捨(共用訊息本來就不該替每個呼叫端客製化文案),在 `collect_window`/`collect_snapshot` 的 docstring 提一句「時區錯誤訊息不含範例,範例只在 CLI 層」,避免以後有人誤以為這是漏改。

## 四、任務指定的三個火力點,逐一交代

- **穩定判定有沒有標錯的情境**:讀碼 + 手算未發現「一輪一致就標穩定」誤判的情況——`for rounds in range(2, MAX_ROUNDS+1)` 只要有一輪 `_end_to_end(...) == first` 就回穩定,`samples` 永遠鎖第一輪,回傳內容本身不會因為判成穩定或不穩定而改變(見上方逐條驗收 1)。真正的問題不是「標錯穩定與否」,而是「標不穩定之後印給人看的那句話描述錯了報告內容出自哪一輪」,已列為上方第 1 條 major。
- **共用時區檢查換掉三處後,例外型別與訊息有沒有被下游依賴而改變行為**:三處例外型別統一是 `ValueError`,跟換掉之前一致,沒有型別上的行為變化;訊息文字方面,`attempt_store._iso`、`task_store._iso` 跟換掉前逐字相同(都是「時間必須帶時區」),只有 `metrics.py` 的兩個直接呼叫點訊息變短、丟了範例,見上方第 2 條 minor,目前沒有下游呼叫端依賴這句話的內容(全庫搜尋只有測試用 `match="時區"` 這種子字串比對,新舊訊息都含這兩字,不受影響)。
- **`src/rtb/ops/cli.py` 對兩支命令列既有結束代碼的影響**:`EXIT_OK`/`EXIT_NO_DATABASE`/`EXIT_NOT_UPGRADED` 在 `metrics.py`、`trace.py` 的數值(0/2/3)都沒變;`EXIT_BAD_ARGUMENTS=7` 全庫現在只在 `cli.py` 定義一次,兩支都匯入同一份。`trace.py` 因為這次改用共用 `Parser`,漏必要參數的結束代碼從舊的 2(跟 `EXIT_NO_DATABASE` 撞號)改成 7——這是本輪要修的行為,已用突變重現確認測試會抓到回退;全庫搜尋(含 `docs/`)沒有找到依賴 `trace.py` 舊行為(漏參數回 2)的既有呼叫端或文件,`docs/.../追蹤檢視.md` 也已同步改結束代碼表。沒有找到新的撞號或既有呼叫端被波及。

## 測試

`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`:1624 個測試全過(`/Users/enzo/rtb-p9i2` 本體跑,未修改任何檔案)。逐條突變重現與端到端手算實驗在 `/tmp/rtb-p9i2-review`(rsync 複製、自建獨立 git)進行,全程 `git -C /tmp/rtb-p9i2-review ...`,未使用 repo 根的 git 指令,未動 `/Users/enzo/rtb-p9i2` 任何檔案。
