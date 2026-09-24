severity: clean
Ruff and the full wiring/verifier test suite (130 tests) pass in the isolated copy, confirming the reuse of `test_static_wiring.ci_problems`/`parse_run_commands` and the new `tools/ruff.toml` banned-api rule both function as intended. No files in the working tree were modified; verification was done in `/tmp/p11i1-review-copy`.

## 最終報告

severity: clean

檢查範圍與比對對象:tools/verify_claims.py(589 行)、tests/tools/test_verify_claims.py(832 行)整份逐段讀過,對照 tools/mypy_sarif.py(同層非產品碼工具)、tests/test_static_wiring.py(CI 接線檢查)、src/rtb/ops/cli.py 與 src/rtb/ops/slo.py(命令列結束代碼、argparse 慣例),另讀 src/rtb/domain|ops|executor|dsp 四份既有 ruff.toml 與 pyproject.toml 的 `[tool.ruff]` 段。逐項如下,均未發現「引入第二種既有做法」或「跨層直呼」:

**分層與依賴方向**:驗證器刻意不匯入 `rtb`,由新增的 `tools/ruff.toml`(`extend = "../pyproject.toml"` + `banned-api` 擋 `"rtb"`)加上 `tests/tools/test_verify_claims.py::test_the_verifier_and_the_product_do_not_import_each_other` 的語法樹掃描雙重守住。`tools/ruff.toml` 的寫法(`extend` 相對路徑按自己深度調整為一層、`[lint.flake8-tidy-imports.banned-api]` 區塊)跟 `src/rtb/domain|ops|executor|dsp` 既有的逐層 ruff.toml 完全同一套機制,只是把既有「逐個 submodule 禁」的細粒度換成「整個 rtb 頂層禁」,這在 tools/ 完全在 rtb 套件之外的前提下是對的收斂,不是另一套做法。

**CI 解析有沒有重造輪子**:`tests/tools/test_verify_claims.py` 第 20 行 `from tests.test_static_wiring import ci_problems, parse_run_commands`,S813 那組測試(`claims_job_problems`)直接重用這兩個既有函式做「不准 `|| true`／`continue-on-error`／`if:`／`needs:` 之類讓檢查沒牙齒」的判斷,只在其上另加一個本地的 `jobs_of()` 把 CI 文字切成「工作名 → 工作內文」。這段新增是必要的:`test_static_wiring.py` 既有的 `EXPECTED_STEPS`/`runs_tool` 機制只回答「指令有沒有出現在 CI 裡任何一步」,不回答「是不是被隔離在單獨的平行工作、沒有 `needs`」——這是 S813 特有的、既有機制答不了的合約,所以新增 job 級解析合理,且沒有繞開共用的 shell-safety 判斷邏輯。

**雜湊與 JSON 讀取**:全庫 sha256 用法(`src/rtb/eval/scoring.py`、`executor/approval.py`、`domain/attempt.py`、`dsp/store.py`、`analyzer/task_store.py` 等)一律是直接 `hashlib.sha256(...).hexdigest()`,沒有共用的雜湊工具函式可重用;`verify_claims.py`/`test_verify_claims.py` 各自的 `_sha256`/`sha()` helper 跟既有寫法一致。JSON 讀取同理,`src/rtb/*` 各處都是直接 `json.loads`,沒有現成的共用讀取層;`verify_claims.py` 的 `read_json` 是為了抓重複鍵/NaN/布林當整數這種既有 `json.loads` 用法答不了的需求,不是自造已有工具的替代品。

**命令列與結束代碼慣例**:`verify_claims.py` 用 `run(argv, *, out=None, timeout=...) -> int` + `main(argv=None) -> None: raise SystemExit(run(argv))` 的入口切法,這其實是 `src/rtb/*` 幾乎所有一次性命令列(`ops/slo.py`、`ops/trace.py`、`ops/metrics.py`、`executor/approve.py`、`executor/replay.py`、`executor/runner.py`、`eval/record.py`)的主流慣例,`tools/mypy_sarif.py`(單一 `main() -> int`,沒有分離的 `run()`)反而是既有的少數例外。驗證器沒有沿用 `ops/cli.py` 的 `Parser`/`EXIT_BAD_ARGUMENTS=7`,但這是對的——那支類別匯入 `rtb.domain._checks`,沿用就會違反「驗證器不匯入產品程式」的邊界;`verify_claims.py` 改用純 `argparse.ArgumentParser` 並自己攔 `SystemExit` 轉成 `EXIT_UNDECIDABLE`,拿到的結束代碼(2)跟 argparse 預設錯誤碼一致,是跟 `mypy_sarif.py` 同一層級(獨立 dev-tool,各自訂自己的 0/1/2 或 0/2)的做法延伸,不是另立門派。

**其他確認**:CI 新增的 `claims` 工作在步驟命名、checkout/setup-python 版本、安裝指令上原樣照抄 `checks` 工作,沒有另創格式。`subprocess.Popen`/`subprocess.run` 全部走參數列表、沒有 `shell=True`,跟 `mypy_sarif.py` 一致。已在 `/tmp/p11i1-review-copy`(複本,不影響 repo)跑過 `pytest tests/test_static_wiring.py tests/tools/test_verify_claims.py`,130 條全過;`ruff check tools/verify_claims.py tests/tools/test_verify_claims.py` 全過,確認新增的 `tools/ruff.toml` 邊界規則與既有接線檢查函式重用都如預期運作。

0 條,blocking 0。
