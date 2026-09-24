severity: clean
# 資安審查報告 — code-phase9-inc2 增量 2 第 3 輪(r3-snapshot.patch,重點審第 2 輪修正新增的兩面)

severity: clean

以攻擊者視角複查第 2 輪修正新增的兩個面(其餘範圍與判準同前兩輪,`r1-資安-sonnet.md`、`r2-資安-sonnet.md` 均判 clean),沒有找到可被不可信輸入利用的洞:

- **`src/rtb/ops/cli.py` 共用命令列錯誤處理沒有新增回顯面,也不洩漏路徑或機密**:新檔案只是把第 1 輪就存在、且已被 `r2-資安-sonnet.md` 判過 clean 的 `_Parser.error` 原樣搬到共用模組,邏輯逐字未變(引句:「self.exit(EXIT_BAD_ARGUMENTS, f"{self.prog}: 參數錯誤:{message}\n")」)。這輪新增的是第二個消費者 `src/rtb/ops/trace.py`——其 `_parse` 改成 `parser = Parser(description="一個任務的跨元件追蹤(只讀)")`(引句:「parser = Parser(description="一個任務的跨元件追蹤(只讀)")」),但 `trace.py` 的參數(`--task-id`、`--analyzer-db`、`--executor-db`、`--dsp-url`、`--dsp-timeout-seconds`)都沒有自訂 `type=` 轉換函式會在失敗時把值嵌進例外訊息,argparse 對「缺參數」只印固定文字(如 the following arguments are required),不會回顯任何值。全庫 `grep` 沒有任何呼叫端把提案內容、DSP 回應或 LLM 產出文字自動組裝成這兩支 CLI 的 argv(`src/rtb/ops/metrics.py`、`src/rtb/ops/trace.py` 之外沒有第三處匯入 `ops.metrics`/`ops.trace`/`ops.cli`,也沒有 `subprocess` 呼叫),`--since`/`--until`/`--now`/`--task-id` 等值全是操作者在自己終端機手動輸入,唯一會把輸入原文回顯進錯誤訊息的 `metrics.py:_aware_time`(引句:「raise argparse.ArgumentTypeError(f"{naive}(例:{text}Z 或 {text}+08:00)") from naive」)也只是操作者自己打的字回顯給自己看,不構成注入或機密洩漏面;`run()` 裡 `FileNotFoundError`/`DatabaseNotUpgraded` 印出的路徑同樣是操作者自己傳入的 `--executor-db`/`--analyzer-db`,不是攻擊者能控制或看不到的祕密。這輪的改動範圍是「消除三支模組各自定義同一段邏輯」的重構,沒有擴大可被外部輸入觸發的面。
- **共用時區檢查(`require_aware`)沒有讓分析端寫入路徑對不可信時間的處理變寬鬆**:新函式(引句:「if not all(is_aware(moment) for moment in moments): raise ValueError(AWARE_REQUIRED)」)與例外型別、訊息文字(`AWARE_REQUIRED = "時間必須帶時區"`)跟被取代的三份各自定義完全一致,`src/rtb/analyzer/task_store.py:220` 的 `_iso` 現在呼叫它但邏輯等價(引句:「require_aware(moment)\n    return moment.astimezone(UTC).strftime」)。核對了所有生產程式碼呼叫點(`src/rtb/ops/metrics.py:774,820,872`、`src/rtb/executor/attempt_store.py:379,537,723,827,834`、`src/rtb/analyzer/task_store.py:220`),沒有任何一處以零個參數呼叫 `require_aware`(空參數會讓 `all()` 對空迭代器回真、悄悄放行),不存在「因為改成可變參數而繞過檢查」的退化面。分析端寫入路徑裡唯一可能來自不可信來源(DSP/提案)的時間是 `Evidence.observed_at`,它在 `src/rtb/domain/evidence.py:65` 的 `__post_init__` 用同一份 `is_aware` 守門且本輪未被觸動,沒帶時區的 `Evidence` 物件根本建構不出來,不會等到 `task_store._iso` 才拋例外,也就不存在「讓寫入卡在分析端、造成漏記或選擇性失敗」的新面;其餘呼叫 `_iso` 的 `now` 都是分析端/執行端行程自己的時鐘(`create_task`、`acquire_lease`、`record_tool_call` 等),不受外部輸入影響。

執行 `PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest tests/domain/test_checks.py tests/ops/test_metrics.py tests/ops/test_trace.py tests/executor/test_attempt_store.py tests/analyzer/test_task_store.py` 105 個測試全過,其中 `tests/domain/test_checks.py` 額外用 AST 掃描確認三處呼叫端沒有各自再定義一份時區判斷式(引句:「for moments in ((aware.replace(tzinfo=None),), (aware, aware.replace(tzinfo=None)),」),行為與上述分析一致。

檔案位置(供交叉核對,未在材料 patch 內、屬查證用):
- `src/rtb/domain/_checks.py:25-26` `ID_PATTERN`/`is_id` 定義,確認 `task_id` 這類可能被自動化工具拿去組 argv 的識別碼值域本就有界
- `src/rtb/domain/evidence.py:65` `Evidence.__post_init__` 對 `observed_at` 的 `is_aware` 守門(先於本輪、未被觸動)
- 全庫 `grep -rln "ops.metrics\|ops.trace\|ops\.cli\|subprocess" src/rtb/` 只命中 `ops/metrics.py`、`ops/trace.py` 自己與一份不相關的 ruff 設定檔,確認沒有第三方呼叫端把不可信資料組裝成這兩支 CLI 的參數

以上兩個本輪新面在材料範圍內都有對應且正確生效的邊界控制,沒有可被利用的洞。
