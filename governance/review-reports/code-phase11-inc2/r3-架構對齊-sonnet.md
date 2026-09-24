severity: major

## _dead_code 另立一套不設界的 AST 走訪,跟驗證器全檔既有的「本體可執行的部分」慣例不一致,會誤擋合法測試

severity: major
blocking: 是

引句:「for node in ast.walk(function):」

驗證器全檔對「這一層/本體會執行的節點」一律用有邊界的走訪:`_executed()`、`_this_level()` 都明文停在 `_OWN_SCOPE`(`FunctionDef`、`AsyncFunctionDef`、`ClassDef`、`Lambda`)不往下走,理由(既有 PITFALL,r3 沒動這段)是「只看本體可執行的部分……沒被呼叫的巢狀 def 與 lambda……都不算」。但 r3 新增的 `_dead_code()`(`tools/verify_claims.py:943`)偵測死碼/常數條件時改用 `ast.walk(function)` 整棵子樹全走,不分這一層還是巢狀 def/lambda 裡面。這是同一份檔案裡對同一類問題(「這段程式本體算不算會執行」)另立的第二套走訪邏輯,沒有建立在既有 `_executed`/`_OWN_SCOPE` 之上。

file: `tools/verify_claims.py:943`(`_dead_code` 定義)、`tools/verify_claims.py:946`(`ast.walk(function)`)、`tools/verify_claims.py:1046`(`injection_problems` 呼叫 `map(_dead_code, used)` 產生擋下原因)

重現:在複本 `/tmp/p11i2r3-arch` 用目前的 `tools/verify_claims.py` 直接呼叫内部函式與 `injection_problems`:一支測試本體乾乾淨淨(`fault = "DEATH"`;`assert act(fault)`),只在裡面定義了一個**從未被呼叫**的巢狀輔助函式 `_reference_impl_not_called()`(內含 `if True: return ...`,單純示意/文件用途,常見寫法),不影響測試實際執行的路徑。

- 用 `vc._dead_code(func)` 直接測:回傳 `"test_trick 有常數條件(死碼)"`(應該回傳 `None`,因為那段 `if True` 在沒被呼叫的巢狀 def 裡)。
- 用 `vc._executed(func)` 對照:走訪結果裡完全看不到那個巢狀 `If` 節點,證明既有的有界走訪正確排除了它。
- 端對端用 `vc.injection_problems(root, manifest)` 對一個放在 `tests/dsp/test_repro.py` 的等價測試跑,結果是:
  `['tests/dsp/test_repro.py::test_pause_then_crash:test_pause_then_crash 有常數條件(死碼),驗證器不照 pytest 解析、一律擋']`
  ——這條路徑就是正式 `verify_claims.py` CLI 在第 5 步(故障注入)實際會走的函式,證明這不是我人為繞過內部邊界湊出來的假象,而是真的會讓一支語意上完全合格的故障注入證據被判定為「看不懂,一律擋」,屬於誤擋正式清單的路徑(未來任何一份正式 claims 只要它引用的測試或 fixture 裡有個從沒被呼叫的巢狀 def/lambda,含有 `if`/`while`/條件運算式或常數 `and`/`or`,或 `return`/`raise` 之後還有敘述,都會被誤擋,而這類「文件性、從未呼叫」的巢狀函式在既有規範裡是明文放行的寫法)。

建議:把 `_dead_code` 改成建立在既有 `_executed(function)` 之上(例如對 `_executed(function)` 逐一產生的節點做同樣的常數條件/死碼判斷,而不是 `ast.walk(function)` 整棵子樹走),讓它跟檔案裡其餘「本體可執行的部分」判斷共用同一個邊界定義,而不是另開一套不分層級的掃描。

---

1 條,blocking 1。
