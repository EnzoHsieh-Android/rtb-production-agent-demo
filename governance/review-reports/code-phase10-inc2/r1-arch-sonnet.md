severity: major
以下是完整報告全文(已依格式規則書寫):

---

severity: major

### 1. `python -m rtb.eval.record` 沒有沿用專案唯一的命令列入口慣例,自成第二種寫法

severity: major
blocking: 是

引句:「def main() -> None:」

觸發情境:任何人執行 `python -m rtb.eval.record`(或未來要幫它加參數、串進腳本判斷成敗)時。

會出什麼錯的行為:`src/rtb/eval/record.py` 的 `main()` 不接受 `argv`、內部沒有可注入輸出串流的 `run()` 函式、也沒有任何 `EXIT_*` 結束代碼——不管報告產生成功與否,行程一律以隱含的 0 結束(除非未被攔截的例外讓直譯器印出 traceback 並回傳 1,但那不是設計出來的結束代碼表,只是意外)。這跟這個專案裡**其餘全部 8 支** `__main__` 模組的寫法不一致:`src/rtb/ops/trace.py`(`run(...)` 於 `src/rtb/ops/trace.py:480`,`main(argv)` 於 `src/rtb/ops/trace.py:501`)、`src/rtb/ops/metrics.py:900,932`、`src/rtb/ops/slo.py:275,312` 三支維運套件模組共用 `src/rtb/ops/cli.py:18` 的 `Parser` 類與 `src/rtb/ops/cli.py:15` 的 `EXIT_BAD_ARGUMENTS = 7`;而因為架構上不准跨行程互相依賴、不能匯入 `rtb.ops`,`src/rtb/executor/approve.py:40,99`、`src/rtb/executor/runner.py:146,196`、`src/rtb/executor/replay.py:32,61`、`src/rtb/executor/inbox_server.py:176`、`src/rtb/dsp/server.py:331` 這五支各自建自己的 `argparse.ArgumentParser`,但**全部**都還是照同一個「`run(argv, ...) -> int` 回傳明確結束代碼、`main(argv: list[str] | None = None) -> None: raise SystemExit(run(argv))`」的兩層結構寫,結束代碼各有自己的 `EXIT_*` 常數表。也就是說,這個共用慣例並不侷限於維運套件才要照做,而是橫跨分析端、執行端、DSP 三個彼此不能互相匯入的行程都收斂到同一個形狀;`record.py` 是全專案唯一一支不照這個形狀寫、也是唯一無法在不呼叫 `subprocess` 的情況下對「命令列輸出」做注入式測試的模組(測試檔 `tests/eval/test_evaluation.py` 也證實了這點:它只呼叫 `record.render`、`record.comparison_rows`,完全沒有測過 `record.main`)。目前 `record.py` 確實不需要任何參數,所以眼下不會有實際行為錯誤,但這是引入了第二種寫命令列入口的做法,往後只要這支工具需要接一個參數(例如切換到正式環境抽樣集、或指定門檻覆寫),就得從零重新設計,而不是像其他 8 支一樣直接沿用既有骨架擴充。

建議修法:比照既有慣例把 `main()` 拆成 `run(argv: list[str] | None = None, *, out: TextIO | None = None) -> int` 與 `def main(argv: list[str] | None = None) -> None: raise SystemExit(run(argv))`,即使目前不需要參數也先建立可注入輸出、有明確結束代碼(至少一個 `EXIT_OK = 0`)的骨架,讓它跟專案裡其餘 8 支 CLI 入口同形狀。

---

除上述一項外,本次增量的分層與匯入禁令(`src/rtb/eval/ruff.toml` 加五個目錄的 `"rtb.eval"` banned-api、`tests/eval/test_evaluation.py` 的 `test_nothing_outside_the_eval_package_imports_it` 用 `ruff check` 逐層驗證 + 原始碼樹掃描)確實比照了 `src/rtb/ops/` 的雙層防線先例(`tests/ops/test_ops_boundaries.py`);`adoption.py` 匯入 `analyzer.policy._VALIDATED_CELLS_ISSUER` 一事,`policy.py` 本身的文件字串已預先指名「評估套件的採用函式」為唯一信任呼叫端,並比照執行端交易物件的簽發者哨兵寫法,不算跨層直呼;`eval_set.py`(合成評估集常數模組)在本專案沒有同類先例可比(既有的 `tests/*_samples.py` 都是測試專用固定樣本,不是離線工具會在執行期讀取、且帶治理決策紀錄流程的產出物),故未列為分歧;延遲量測(`perf_counter_ns` + 暖身 + 中位/p95)在本專案也無既有先例可供比對。Systems 節點 `docs/rtb-production-agent-demo-knowledge/Systems/評估與Jev決策點.md` 的 `about_code` 七支檔與本次 patch 新增檔案一致,未發現落差。
