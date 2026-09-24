severity: clean
已完成審查。以下為完整報告全文(可直接存檔):

---

severity: clean

本輪 remerge-diff(增量 1/2/3 三次合併衝突處置新寫的程式與測試)在「架構對齊」鏡頭下檢查的五項,均與專案既有做法一致,未引入第二種做法:

1. **共用參數解析器**:trace、metrics、slo 三支命令列現在都改成 `from rtb.ops.cli import Parser`(`src/rtb/ops/trace.py`、`src/rtb/ops/metrics.py`、`src/rtb/ops/slo.py`),slo 之前直接用 `argparse.ArgumentParser(...)`,本次合入後改成同一份 `Parser`。引句:「parser = Parser(description="服務水準指標的狀態與燒損告警(只讀)")」。
2. **結束代碼表**:`EXIT_BAD_ARGUMENTS = 7` 只在 `src/rtb/ops/cli.py` 定義一份,三支命令列都用 `from rtb.ops.cli import EXIT_BAD_ARGUMENTS as EXIT_BAD_ARGUMENTS` 匯入,沒有另立第二份常數。`docs/rtb-production-agent-demo-knowledge/Systems/追蹤檢視.md` 也同步記下「維運套件的結束代碼表因此是 0/2/3/4/5/6/7/8,8 只有服務水準用(缺資料)」,與程式一致。
3. **時區參數型別**:`aware_time` 只在 `src/rtb/ops/cli.py` 定義一份(原本是 `metrics.py` 的私有函式 `_aware_time`,本次合入時搬移並改名),metrics 與 slo 都改成 `from rtb.ops.cli import Parser, aware_time` 並用它當 `--since`/`--until`/`--now` 的 `type=`。trace.py、metrics.py 裡另外幾處 `datetime.fromisoformat` 是內部字串轉時間戳(讀已知格式的既有紀錄,例如 `_utc`/`_time`),不是命令列參數解析,不算第二套型別。
4. **維運掃描的 builtins 判斷**:`tests/ops/test_ops_boundaries.py` 新增的 `BUILTIN_RUNNERS` 只是在既有 `_dynamic_lookups` 那個唯一的語法樹掃描函式裡,對 `ast.Attribute` 節點多判一個「是不是經 builtins/`__builtins__` 取用」的條件,沿用同一套 `DYNAMIC_LOOKUPS` 名字集合機制,沒有另開第二套掃描邏輯或第二個檔案。全庫搜尋 `builtins`/`__builtins__` 只在這一支測試檔出現,`tests/executor/write_scan.py`、`tests/executor/audit_guard.py` 沒有重複或衝突的判法。
5. **稽核守衛釘住集合的寫法**:`tests/executor/test_audit_tables.py` 把 `assert set(inbox_store._ADDED_COLUMNS) & set(GUARDED) == {"attempts"}` 改成 `== {"attempts", "dsp_calls"}`,沿用同一種「逐欄驗過後釘死集合」的既有 assert 寫法,只是把值更新為目前真實狀態(以 `src/rtb/executor/inbox_store.py:275-276` 的 `_ADDED_COLUMNS = {"attempts": ..., "dsp_calls": ...}` 核對過,確實兩表都在),未新增另一種釘住方式。

驗證:`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest tests/ops/test_ops_boundaries.py tests/ops/test_slo.py tests/ops/test_window_readers.py tests/executor/test_audit_tables.py tests/ops/test_trace.py tests/ops/test_investigation_drill.py -q` → 103 passed。

沒有發現需要標記為 major 以上的架構分歧。
