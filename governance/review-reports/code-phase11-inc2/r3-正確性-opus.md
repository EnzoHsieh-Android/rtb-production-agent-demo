severity: major

第 2 輪 9 條的修法都有落地。正式清單在複本 `/tmp/p11i2r3-corr` 實跑:「通過:5 條宣稱,跑了 77 支證據測試全部通過」,結束代碼 0,沒有誤擋。驗證器自己的測試 255 支全綠。

這輪新加的規則是把 pytest 的寫法一種一種列出來擋,沒列到的寫法照樣放行並印「通過」。我在複本造了 15 支探針,全部得到 code=0、「通過」。下面 3 條是新規則沒做到「看不懂就擋」的地方。

## 1. parametrize 同名只擋寫在測試或類別上的裝飾器,其他三種常見覆蓋寫法都放行
severity: major
blocking: 是
引句:「for call in _decorator_call(target, "mark.parametrize"):」
file: `tools/verify_claims.py:1021`

**問題:** 這條規則只看測試函式與所在類別 `decorator_list` 裡、形如 `mark.parametrize(...)` 的呼叫。pytest 還有三條正規途徑能用同名參數蓋掉 fixture,驗證器都沒看:
- 模組層的 `pytestmark`
- 類別本體的 `pytestmark`
- 事先存進變數的裝飾器(`@CASES`),或 `pytest_generate_tests` 裡的 `metafunc.parametrize`

結果是驗證器還是去讀 conftest 那支帶手段的 fixture,但 pytest 實際拿到的是 parametrize 給的值。

**重現:** 在複本寫 `tests/tools/test_zz_r3probe.py`,沿用 `repo`、`manifest`、`write_manifests`,新增 `tests/dsp/test_trick.py` 並接成 failure_injection。injection 設成 `after_dsp_commit`,也就是 conftest 裡 `crash_after_commit` 的回傳值。下面四例的測試本體都是 `assert crash_after_commit == "safe"`,實跑都通過,證明 pytest 用的是沒注入的值。四例驗證器都回 code=0、「通過:5 條宣稱,跑了 3 支證據測試全部通過」:
- 模組層 `pytestmark = pytest.mark.parametrize("crash_after_commit", ["safe"])`
- 類別本體 `pytestmark = [pytest.mark.parametrize("crash_after_commit", ["safe"])]`
- `CASES = pytest.mark.parametrize(...)`,測試上寫 `@CASES`
- 測試檔裡的 `pytest_generate_tests` 呼叫 `metafunc.parametrize("crash_after_commit", ["safe"])`

**建議:** 照「看不懂就擋」處理,以下情況一律擋:
- 測試檔或類別本體有 `pytestmark` 綁定
- 測試檔或路上的 conftest 定義了 `pytest_generate_tests`
- 測試或類別的裝飾器不是 `Call`,而且解析不出是 `pytest.mark.*`

正式證據所在的測試檔沒有用 `pytestmark` 或 `pytest_generate_tests`(已 grep 確認),這樣擋不會誤擋正式清單。每種寫法各補一個會被擋的案例。

## 2. 最外層 try 或 if 裡的同名 fixture 會蓋掉 conftest 那支,兩個檢查互相漏接
severity: major
blocking: 是
引句:「if any(not (isinstance(b, ast.FunctionDef) and _is_fixture(b))」
file: `tools/verify_claims.py:902`、`tools/verify_claims.py:809`

**問題:** 兩個檢查各看一半:
- `_fixtures` 只收最外層「直接寫在 body 裡」的 fixture(`fixtures = [s for s in body if _is_fixture(s)]`)。
- `_rebound_fixture_names` 用 `_bindings` 往 try、if 區塊裡看,但遇到帶 fixture 裝飾器的 def 就一律放過。

所以測試檔在 `try:` 或 `if …:` 底下定義一支同名 fixture 時,兩邊都不擋。pytest 用的是測試檔這支,驗證器卻去讀 conftest 那支帶手段的。

**重現:** 同上的探針,injection 設成 `after_dsp_commit`。測試檔最外層寫 `try:` 或 `if sys.platform:`,底下是 `@pytest.fixture def crash_after_commit(): return "safe"`,測試斷言拿到的值 `== "safe"`,實跑通過。兩例驗證器都回 code=0、「通過」。

**建議:** 在 `_rebound_fixture_names` 裡,只有「直接寫在 body 最外層」的 fixture def 才放過。巢狀在區塊裡的 fixture def 也算另外綁定,一律擋。補 try 與 if 各一個案例。

## 3. 死碼規則只認字面常數與 return、raise,換個寫法的死碼照算斷言
severity: major
blocking: 是
引句:「isinstance(st, ast.Return | ast.Raise) for st in statements[:-1]」
file: `tools/verify_claims.py:947`、`tools/verify_claims.py:955`

**問題:** `_dead_code` 有兩處判定太窄:
- **常數條件:** 只擋 `node.test` 本身是 `ast.Constant` 的情況。`if not True`、`if ()`、`if 1 == 2` 一樣是永遠不執行,卻不擋。
- **之後還有敘述:** 只看 `Return` 和 `Raise`。`break`、`continue` 之後的敘述,以及 if/else 兩邊都 return 之後的敘述,同樣到不了,卻不擋。

結果是一支從頭到尾沒執行任何斷言的故障注入測試,因為死碼裡有個 `assert`,就被當成有斷言。

**重現:** 同上的探針,injection 設成 `DEATH`。下面六例的測試本體都先執行 `act("DEATH")`,唯一的 `assert False` 放在不會執行的位置;因為沒執行,這支 pytest 測試跑起來是通過的。六例都得到 code=0、「通過」:
- `if not True:` 底下
- `if ():` 底下
- `if 1 == 2:` 底下
- `for` 迴圈裡 `break` 之後
- `for` 迴圈裡 `continue` 之後
- `if request: return / else: return` 之後

**建議:**
- `Break`、`Continue` 併入「之後還有敘述就擋」。
- 條件式如果完全由字面值組成(沒有 Name、Attribute、Call),一律當常數條件擋。`for` 迭代的是字面值(如 `for _ in ():`)時也照同一條擋。
- if/else 每一支都以 return、raise 結尾、後面卻還有敘述的情況也擋。
- 各補一個會被擋的案例。

---

**查過、這輪沒列為 finding 的:**
- **第 2 輪其他修法:** 以下都已照修,也有參數化案例,我逐一比對過:
  - `IfExp`/`BoolOp` 常數、型別註記、f 字串片段、assert 條件內的手段不算引用
  - 任意物件的 `.raises()` 與不放在 `with` 裡的 `raises` 不算斷言
  - 類別裡有 fixture 或有基底類別就擋
  - 帶預設值的參數不追、最外層賦值或匯入蓋掉 fixture 就擋
  - 非字面的 usefixtures 與 autouse 會擋,字面的會追
- **有意沒報,因為是條件分支、不是這輪新規則帶進來的:**
  - 手段只寫在 `except` 分支裡
  - `try` 本體以 return 結尾,把斷言放在 `else` 裡

  這兩種在執行期不一定跑到,但我的探針也是 code=0,留給作者決定要不要一併處理。
- **誤擋風險:**
  - 新規則裡 `while True:` 與 `x or "default"` 會被判成常數條件而擋下,這是保守方向。
  - 正式清單用到的 fixture(含 conftest 裡的 autouse)目前沒有踩到這兩種寫法,正式清單實跑通過。

3 條,blocking 3。
