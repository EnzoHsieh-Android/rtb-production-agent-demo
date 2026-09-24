severity: clean
已完成三個查核點:領域層分層、ops 套件既有結構、有沒有留下第二份檢查/第二套結束代碼表。查法:讀 r3-delta.patch 全文、比對各層 `ruff.toml` 匯入邊界、`git`(唯讀)歷史、`ruff check`、跑相關測試子集,並回頭核對 r2-arch-sonnet.md 那兩條有沒有被真正收斂而非另開一套。

關鍵查證:
- `src/rtb/domain/ruff.toml` 只禁領域層碰資料庫/網路/行程/動態匯入/模型客戶端,沒禁被其他層匯入;`require_aware` 是純函式(不碰 I/O),且域層本來就有「predicate（`can_transition`)+ raise 包一層（`transition`丟`IllegalTransition`)」的既有配對寫法（`src/rtb/domain/task_state.py:70-78`），`is_aware`(predicate)+`require_aware`(raise 包一層)是同一種寫法搬進 `_checks.py`,不是新開一套。
- `_checks.py` 本來就是「一個規則一份定義」且已被 executor/analyzer/ops 三層照舊匯入(`grep` 到 executor、analyzer、ops 多處 `from rtb.domain._checks import ...`,這是 patch 之前就有的既有匯入方向),`require_aware` 走同一條路,`ruff check` 對六支改動檔案全過,沒有違反任何 banned-api。
- `src/rtb/ops/cli.py` 的角色與命名跟頂層 `sqlitekit.py`/`httpkit.py`(「DSP 與提案收件口都用它,不各寫一套」)同一種「共用小工具、公開路徑、非底線開頭」慣例,只是把範圍縮到 `ops` 套件內;`trace.py`、`metrics.py` 兩支都改成 `from rtb.ops.cli import EXIT_BAD_ARGUMENTS as EXIT_BAD_ARGUMENTS` / `Parser`,兩表都只剩一份 `EXIT_BAD_ARGUMENTS=7` 定義(`grep -rn "EXIT_BAD_ARGUMENTS\s*="` 全庫只有 `cli.py` 一處),`class Parser`/`class _Parser` 全庫也只剩 `cli.py` 一個定義,沒有殘留舊份。
- `tests/domain/test_checks.py` 用 AST 掃描三個原呼叫端(`attempt_store.py`、`task_store.py`、`ops/metrics.py`),斷言不准再自定義同名判斷式、不准直接用 `is_aware`,只准呼叫共用的 `require_aware`——這條機檢已經在跑且通過。
- 執行 `PYTHONPATH=src pytest tests/domain/test_checks.py tests/ops/test_metrics.py tests/ops/test_trace.py tests/executor/test_attempt_store.py tests/analyzer/test_task_store.py`:105 passed。
- 額外核對 `domain/evidence.py`、`domain/proposal.py` 裡另外兩處帶「時區」字樣的檢查(`__post_init__` 的欄位清單驗證、`_check_inputs` 的 `"now 必須是有時區的 datetime"`),兩者結構與用途都跟本次合併的「ISO 字串轉換前擋裸時間」不同,且是 r3 delta 沒有觸碰的既有程式碼,不在本輪 delta 引入的範圍內。

沒有找到本輪修法引入的新分層違規、ops 結構不合,或殘留的第二份檢查/第二套結束代碼表。

---

severity: clean

本輪 delta(r3)驗證通過:共用時區檢查 `require_aware` 放進 `src/rtb/domain/_checks.py` 符合領域層既有「predicate + raise 包一層」寫法與既有跨層匯入方向(`ruff check` 全過,無 banned-api 違規);`src/rtb/ops/cli.py` 的定位與命名比照專案既有「共用小工具、不各寫一套」慣例(`sqlitekit.py`/`httpkit.py`),`trace.py`、`metrics.py` 均已改用同一份 `Parser`/`EXIT_BAD_ARGUMENTS`,全庫掃描確認沒有殘留第二份時區檢查、第二個 `_Parser`/`Parser` 類別或第二套 `EXIT_BAD_ARGUMENTS` 定義;`tests/domain/test_checks.py` 的 AST 掃描機檢與相關測試子集(105 passed)均綠。r2-arch-sonnet.md 的兩條(時區檢查三處各寫一份、trace.py 未同步套用結束代碼區分)在本輪都已收斂成單一定義且未見新的不一致。
