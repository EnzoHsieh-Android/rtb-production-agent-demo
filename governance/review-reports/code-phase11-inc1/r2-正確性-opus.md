severity: major

**1. 外掛或 helper 解析到 src/、tests/ 以外的 repo 內檔案時被靜默丟掉，改了照樣印通過**
severity: major
blocking: 是
引句:「+    return rel.split("/")[0] in CODE_ROOTS or rel == ROOT_CONFTEST」
file: `tools/verify_claims.py:440`、`tools/verify_claims.py:485`、`tools/verify_claims.py:471`
- 原因:`_plugin_files` 用 `_module_file` 找得到 `tools/helpers.py` 或 repo 根的 `myplug.py`,所以不會報「不在 repo 裡」。但進了 `dependency_closure` 之後,`_in_code` 判它不在 src/tests,直接 `continue`。結果它不進 need_harness,也沒有任何擋下原因。
- 這違反第 1 輪修法自己寫的規則:「讀不出字面值、或指到 repo 外的套件就擋」。這種檔在 repo 內,卻既不雜湊也不擋。
- 證據測試靜態匯入 `from tools.fake import check` 也是同一條路。repo 根以及 tools/ 都在子行程的 sys.path 上。
- 重現:`/tmp/p11r2-corr/exp/e1.py`,三個變體都先讓 test_pause 失敗,基線都是結束代碼 1。
  - E1:`tests/dsp/conftest.py` 寫 `pytest_plugins = ["tools.helpers"]`。只把 `tools/helpers.py` 換成把失敗改通過的報告鉤子,清單不動,結果是 `(0, ['通過:5 條宣稱,跑了 2 支證據測試全部通過'])`。
  - E1b:pyproject 寫 `addopts = "-p myplug"`,外掛放 repo 根的 `myplug.py`。同樣改成惡意內容後,結果是 0、通過。
  - E2:證據測試匯入 `tools/fake.py` 的 `check`。把 check 弱化成 `return None` 後,結果是 0、通過。
- 建議:
  - `_module_file` 解析得到、但 `_in_code` 為假的檔,一律擋,不要跳過。例如回報「X 在 src/tests 以外,驗證器不算它的閉包」。
  - 或者把閉包放寬成 repo 內任何 .py 都算 harness。
  - 補三條回歸測試:pytest_plugins 指 tools/、addopts 的 -p 指根模組、證據測試匯入 tools/。

**2. addopts 只讀 `[tool.pytest.ini_options]`,pytest 9 原生 `[tool.pytest]` 表的 -p 外掛整段漏掉**
severity: major
blocking: 是
引句:「+    options = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", "")」
file: `tools/verify_claims.py:509`
- 原因:本機與 CI 用的是 pytest 9.1.1,它支援原生的 `[tool.pytest]` 表,addopts 可以直接寫成陣列。
- 設定改寫到這張表之後,pyproject 的雜湊會變一次。重算之後,-p 指到的外掛檔不在閉包裡,之後再改它就看不到。這跟第 1 輪第 2 條是同一類漏洞。
- 重現:`/tmp/p11r2-corr/exp/e3.py` 的 E4。
  1. pyproject 改成 `[tool.pytest]\ntestpaths = ["tests"]\npythonpath = ["src"]\naddopts = ["-p", "tests.plug"]`,`tests/plug.py` 先放無害內容。test_pause 失敗,基線是 1。
  2. 只把 `tests/plug.py` 改成惡意外掛,清單不動,結果是 `(0, ['通過:…'])`。
- 建議:
  - 同時讀 `tool.pytest.addopts` 與 `tool.pytest.ini_options.addopts`。
  - 兩張表都在時,或出現不認得的形態時,直接擋。
  - 補一條原生表的測試。

**3. pyproject 的 `pythonpath` 多列一個目錄時,頂層名稱的 helper 解析不到,被當成外部套件略過**
severity: major
blocking: 是
引句:「+    for base in (root / "src", root):」
file: `tools/verify_claims.py:401`、`tools/verify_claims.py:493`
- 原因:第 1 輪第 2 條(非套件目錄的同層 helper)的修法是「每層都要有 __init__.py」。這只關掉一條讓「頂層名稱」解析到 tests/ 內檔案的路。
- `pythonpath = ["src", "tests/helpers"]` 是 pytest 的正式寫法,效果一樣:`import assertions` 會解析到 `tests/helpers/assertions.py`。
- `_module_file` 只找 src/ 與 repo 根,找不到就當第三方套件忽略,沒有擋下原因。
- 重現:`e3.py` 的 E5。
  1. pyproject 的 pythonpath 加上 `tests/helpers`,證據測試寫 `from assertions import check`。基線 test_pause 失敗,結果是 1。
  2. 把 `tests/helpers/assertions.py` 弱化,清單不動,結果是 `(0, ['通過:…'])`。
- 建議:二擇一。
  - 從 pyproject 讀 `pythonpath`,把每個 repo 內項目都加進 `_module_file` 的搜尋根,並檢查同名衝突。
  - 或者規定 pythonpath 只准 `["src"]`,其他值直接擋。

**4. 星號匯入(以及 match 捕獲、屬性指派)不算綁定:`try: from x import *` 蓋掉登錄表或 symbols 照樣通過**
severity: major
blocking: 是
引句:「+        return (node.asname or node.name).split(".")[0]」
file: `tools/verify_claims.py:361`
- 原因:`from rtb.extra import *` 的 alias 名稱是 `*`,不會對上 `WRITE_ACTIONS` 或 `act`。
- 「try 匯入加速版覆蓋純 Python 版」正是第 1 輪點名的常見寫法。修法只擋了具名匯入,星號版漏了。
- 其他同樣看不到的重綁:
  - `match … case WRITE_ACTIONS:` 捕獲樣式(`read_registry` 照樣回 `{'a'}`)。
  - 模組層 `H.handle = …` 覆蓋 symbols 的類別方法(`_defines` 回 True)。
- 重現:`e3.py` 的 E3。
  1. server.py 尾端加 `try:\n    from rtb.extra import *\nexcept ImportError:\n    pass`。extra.py 定義 `WRITE_ACTIONS = ("update","pause","void")` 與 `act`,extra.py 也列進 scope。
  2. `read_registry` 回 `{'update','pause'}`,`_defines(...,"act")` 回 True,verify 回 `(0, ['通過:…'])`。
  3. 執行期多出來的 "void" 沒有任何 covers。
- 建議:
  - 登錄表或 symbols 所在的模組這一層,只要有 `import *` 就擋,因為算不到它帶進哪些名字。
  - `ast.MatchAs`、`ast.MatchStar` 的 name 與 `MatchMapping.rest` 納入 `_bound_name`。
  - 對 symbols 的類別名,模組層出現 `類別.屬性 =` 指派也算重綁。

**5. 新的完整走訪會誤擋幾種正常寫法:@overload、property setter、模組層推導式變數、只有型別註記的宣告**
severity: minor
blocking: 否
引句:「+    """這一層執行時會碰到的節點:進 if、try、for、with 等區塊,不進 def、class、lambda 的本體。"""」
file: `tools/verify_claims.py:341`、`tools/verify_claims.py:370`
- 原因:
  - `_this_level` 會走進推導式,但 Python 3 的推導式有自己的作用域。
  - 同名的多個 def 一律算重綁。專案跑 mypy 嚴格模式,`@overload` 和 `@x.setter` 都是正常寫法。
  - 只有型別註記、沒有值的 `act: object` 也被算成綁定。
- 被擋時的訊息是「不是唯一一次的最外層定義」,容易誤導。
- 重現:`/tmp/p11r2-corr/exp/e6.py`,`_defines` 的結果:
  - 誤擋(回 False):property setter、overload、`[act.__name__ for act in ()]`、`act: object` 後面再 def。
  - 正確不擋(回 True):TYPE_CHECKING 區塊匯入別的名字、`__all__`、類別裡同名屬性與方法內的區域變數。
  - 登錄表同名作推導式變數,`read_registry` 回 None(誤擋)。
- 建議:
  - `_OWN_SCOPE` 加進 ListComp、SetComp、DictComp、GeneratorExp。
  - AnnAssign 沒有 value 時不算綁定。
  - 同名 def 全帶 `@overload`、或後續 def 帶 `@<名>.setter/.getter/.deleter` 時,放行最後那一個。
  - 正式清單目前沒踩到,所以列 minor。

**6. pytest 內建外掛(例如 `pytest_plugins = ["pytester"]`)會被當成 repo 外套件擋下**
severity: minor
blocking: 否
引句:「+            problems.append(f"{where} 載入的外掛 {name} 不在 repo 裡,驗證器算不到它的雜湊")」
file: `tools/verify_claims.py:473`
- 原因:`pytester` 是 pytest 文件指定的啟用寫法,它屬於 pytest 本身。pytest 的版本由 requirements-dev 釘住,不是任意第三方程式。
- 重現:`e9.py`,conftest 加 `pytest_plugins = ["pytester"]`,結果是擋下,原因寫「外掛 pytester 不在 repo 裡」。
- 建議:放行 pytest 內建外掛名單(可以從 `_pytest.config.builtin_plugins` 抄成常數)。或者在計劃裡明寫「內建外掛也不准」,並附理由。

**查過、沒有問題的項目**
- 唯讀 xfail 紀錄器:下面四種 strict=False 寫法全部擋下,訊息都是「預期失敗卻通過」:
  - 標記上寫 strict=False。
  - 執行期 `request.node.add_marker`。
  - 模組層 `pytestmark`。
  - fixture 裡 `request.applymarker`。
- 紀錄檔寫入時機:紀錄檔在 `pytest.main` 回來之後才寫。在 unconfigure 裡 `os._exit(0)` 會因為沒有紀錄檔而擋(測試 `test_a_run_that_leaves_no_xfail_record_is_blocked` 有蓋)。
- `-E -s`:旗標位置確實在直譯器那邊。測試直接驗 `sys.flags`,另外有 user site 開著的直譯器路線。
- `sys.argv[2:]` 的切法正確。
- `-p no:xxx` 有排除。`-p=x` 會被擋,不會靜默。`-qp x` 在 pytest 下根本沒載入外掛,不是漏洞。
- `pytest_plugins` 寫在 if 區塊或用 `+=`:讀不出字面值,會擋。
- 證據測試在 tests/ 以外會擋。缺 `__init__.py` 會擋。本 repo 的 tests 各層都有 `__init__.py`,不會誤擋。
- CI 新規則:
  - `defaults:` 全域掃描。
  - claims 工作裡的 `shell:`。
  - 驗證器指令要是單行 run(比對時會去掉 `- ` 前綴與註解)。
  - 目前的 ci.yml 通過。我沒找到新的「沒牙齒」寫法。
- 複本全套 `tests/tools/test_verify_claims.py` 145 支通過。所有實驗都在 `/tmp/p11r2-corr/exp/`,repo 工作樹沒有動(`docs/.governance-log.jsonl` 與 `governance/review-reports/code-phase11-inc1/` 的狀態在我開始前就有)。

6 條,blocking 4。
