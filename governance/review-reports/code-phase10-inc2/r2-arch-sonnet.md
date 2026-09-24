severity: major
# 完成的稽核

判了 r2-delta.patch 四個指定項目(ProductionReport 私有簽發者、Measure/OperationalLimits 驗值、record 命令列兩層形狀、邊界掃描),逐一跟增量 1 與執行端既有寫法比對:

- **ProductionReport 私有簽發者**:`__slots__` + issuer 哨兵 + 唯讀 `@property`,跟 `analyzer/policy.py` 的 `ValidatedCells`、`executor/attempt_store.py` 的 `ExecutorTransaction` 同形狀,沒有多刻 `__eq__`/`__repr__` 等特殊方法——一致,未列入報告。
- **record 命令列**:已改成 `_parse(argv)` / `run(argv=None, *, out=None) -> int` / `main(argv=None) -> None: raise SystemExit(run(argv))` 三段式,跟 `executor/approve.py`、`executor/replay.py` 同形狀——第 1 輪的發現已修好,未見新落差。
- **邊界掃描**:`test_nothing_outside_the_eval_package_imports_it` 延續 `tests/ops/test_ops_boundaries.py` 的雙層防線(ruff TID251 逐層擋直接匯入 + 原始碼樹 AST 掃描補 ruff 抓不到的動態匯入形式),是同一個防線哲學的自然延伸——未見新落差。
- **Measure/OperationalLimits 驗值**:發現落差,見下方第 1 條(major)。

以下是完整報告全文(已依格式規則書寫):

---

severity: major

### 1. `Measure`/`OperationalLimits` 的合法值檢查沒有沿用 `src/rtb/domain/_checks.py`,自己重刻了一份同樣的型別判準

severity: major
blocking: 是

引句:「isinstance(value, int | float) and not isinstance(value, bool)」

file: `src/rtb/eval/adoption.py:45-47`、`src/rtb/domain/_checks.py:22-24`

觸發情境:任何人要改「數字要不含布林、要有限」這條規則時(例如把布林排除的判準改成別的寫法,或替 `is_plain_number` 修 bug)。

會出什麼錯的行為:`src/rtb/eval/adoption.py` 新增的 `_finite_nonnegative`(用在 `Measure.__post_init__`、`ComparisonRow` 逐格驗值、`OperationalLimits.__post_init__` 三處)逐字重寫了 `src/rtb/domain/_checks.py` 裡 `is_plain_number` 的判準本體——`isinstance(value, int | float) and not isinstance(value, bool)`,兩邊完全相同。`domain/_checks.py` 的檔頭已經明講這支模組存在的理由:「只放…到處要用的判斷…同一個規則只有一份定義,免得各模組改了一處漏另一處」,`eval/adoption.py` 也沒有被禁止匯入 `rtb.domain`(`src/rtb/eval/ruff.toml` 的 banned-api 只擋 `sqlite3`、`rtb.executor`、`rtb.dsp`、`rtb.ops`,沒擋 `rtb.domain`,而且 `eval` 套件本來就已經匯入 `rtb.domain.worth`)。這代表往後有人只改了 `is_plain_number`(例如要多排除 `Decimal` 或改成別的數值型別白名單),`adoption.py` 這份獨立複本不會跟著變,兩邊判準漂移。而且 `_finite_nonnegative` 沒有像 `domain/_checks.py` 的 `is_finite_or_none` 那樣捕捉 `OverflowError`(超大整數轉浮點數時 `math.isfinite` 可能丟出),`domain/_checks.py` 的版本刻意處理了這個邊界,`adoption.py` 這份新寫的沒有——是同一條規則的第二套、且較不完整的實作。

建議修法:`_finite_nonnegative` 改成呼叫既有的 `is_plain_number(value)`(必要時外加 `try/except OverflowError` 或直接借 `is_finite_or_none` 的模式),自己只疊加 `value >= 0` 這個 `eval` 套件特有的「不為負」限制,不要重新表達「不含布林的數字」這個共用判準。

---

除上述一項外,r2-delta.patch 對第 1 輪架構席四個具體標的(ProductionReport 私有簽發者、record 命令列兩層形狀、Metric 結構、逐格比較表、邊界掃描)的修法都收斂到專案既有寫法,沒有帶出新的第二種做法。
