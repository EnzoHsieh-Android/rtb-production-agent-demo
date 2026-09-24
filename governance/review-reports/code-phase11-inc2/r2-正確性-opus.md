severity: major

## 1. 「可執行部分」仍把不會執行的分支算進去:常數條件的 IfExp／and／or、常數真值 if 的 else、區域變數型別註記、return 之後的程式、f 字串片段
severity: major
blocking: 是
引句:「elif isinstance(node, ast.If | ast.While) and _constant_false(node.test):」
file: `tools/verify_claims.py:866`、`tools/verify_claims.py:876`

`_executed` 只剪掉一種死碼:條件是常數假值的 `if`／`while` 本體。另外幾種同類的死碼,還有一種字串片段,都沒擋:

- **條件運算式的死分支。** `x if False else y` 的 `x`,以及 `False and "DEATH"` 的右邊,都照算。
- **常數真值 `if` 的 `else`。** `if True: … else: fault = "DEATH"` 的 else 照算。
- **區域變數的型別註記。** `fault: "DEATH" = "update"`。函式裡區域變數的註記 Python 根本不會求值,但它照樣走進 `AnnAssign.annotation`。
- **return 之後的程式。** 也就是無法到達的程式,裡面的 `assert` 算成斷言。
- **f 字串片段。** `f"{p}DEATH"` 拆開後,裡面的 `Constant("DEATH")` 整串相等就命中。這其實是「子字串」問題換了個樣子重現。

重現:在複本 `/tmp/p11i2r2-corr` 寫探測測試,沿用 `repo`、`manifest`、`write_manifests`,新增 `tests/dsp/test_trick.py`,把它接成 `failure_injection`、injection 設成 `DEATH`,再跑 `verify(repo)`。下列六支都得到 code=0,輸出「通過:5 條宣稱,跑了 3 支證據測試全部通過」:
- `fault = 'DEATH' if False else 'update'; assert act(fault)`
- `fault = False and 'DEATH'; assert act('update') or fault`
- `if True: fault='update' else: fault='DEATH'`
- `fault: 'DEATH' = 'update'`
- `act(fault); return; assert fault`:只有 return 後面有斷言。
- `fault = f'{p}DEATH'`,其中 `p='NO_'`

建議:照「看不懂就擋」處理,不要再逐一理解語法:
- 測試或 fixture 本體裡只要有常數條件(`if`／`while`／`IfExp`,或 `BoolOp` 裡帶常數運算元),或 `return`／`raise` 後面還有敘述,就直接擋。
- 不走 `AnnAssign.annotation`。
- `JoinedStr` 裡的片段不算整串。
- 為上面各種寫法各補一個會被擋的參數化案例。

## 2. 斷言條件裡的手段字串也算「引用」:`assert 'DEATH' not in out` 沒注入卻印通過
severity: major
blocking: 是
引句:「pending.append(node.test)」
file: `tools/verify_claims.py:865`

第 1 輪修法只排除了 assert 的訊息;assert 的條件式仍送進 `_mentions`。結果是:測試只「斷言某個輸出裡沒有手段名」,就被當成有引用手段。這是正常寫法,不需要刻意繞過。

重現:同上的探測方式,測試本體是 `out = str(act('update')); assert 'DEATH' not in out`,injection 設成 `DEATH`。結果 code=0,輸出「通過」。

另外查過正式清單:10 支故障注入證據的手段名,全部都在非 assert 的程式裡命中,沒有一支是靠斷言條件過關的。所以改這條不會誤擋正式清單。

建議:`_mentions` 用的走法不要進 `Assert` 節點;`_asserts` 另外走。為 `assert 'DEATH' not in out` 補一個會被擋的案例。

## 3. 沒放進 `with` 的 `pytest.raises(...)` 被當成斷言
severity: major
blocking: 是
引句:「if name == "raises" or name.startswith("assert_"):」
file: `tools/verify_claims.py:892`

只要呼叫名是 `raises` 就算斷言。問題是單獨一行 `pytest.raises(ZeroDivisionError)` 只建出一個 context manager,不進入它,什麼都不檢查。「忘了寫 with」是很常見的失誤,正是「防忘記」該擋的。任何 `x.raises()` 也一樣會被算成斷言。

重現:測試本體是 `fault='DEATH'; pytest.raises(ZeroDivisionError); act(fault)`。結果 code=0,輸出「通過」,但這支測試沒有任何會執行的斷言。

建議:
- 只在 `raises` 呼叫出現在 `With`／`AsyncWith` 的 `withitem.context_expr` 時才算。
- 或者呼叫時第二個參數是可呼叫物(舊式寫法)時才算。
- 補一個「沒放進 with 的 raises 會被擋」的案例。

## 4. 類別層 fixture 的擋下規則不全:類別裡的 `name=`、繼承來的 fixture、經由別的 fixture 間接要求的,都沒擋
severity: major
blocking: 是
引句:「clashes |= {s.name for s in body if _is_fixture(s) and isinstance(s, ast.FunctionDef)」
file: `tools/verify_claims.py:819-832`、`tools/verify_claims.py:800-815`

目前的規則有三個地方跟 pytest 實際的解析不一樣:
- **只比對直接參數。** `_class_fixture_clash` 只看測試直接參數與類別裡「函式名」同名的 fixture。pytest 對每個測試項目計算完整的 fixture 閉包,所以類別層的 fixture 也會蓋掉其他 fixture 間接要求的同名 fixture。
- **類別裡的 `name=` 沒擋。** `_fixtures` 只在測試檔與 conftest 的最外層擋 `name=`。
- **不看基底類別。** 父類別定義的 fixture 會被繼承,但驗證器不看。

重現:以下三例 injection 都設成 `after_dsp_commit`(conftest 裡的 `crash_after_commit` 會回傳它)。三例結果都是 code=0、輸出「通過」。每例的測試本體都斷言拿到的值 `== 'safe'`,而且實跑通過,證明 pytest 用的是沒注入的那支:
- **間接要求:** 模組層 `layered(crash_after_commit)`,`class TestTrick` 裡定義 `crash_after_commit` 回傳 `'safe'`,測試是 `test_trick(self, layered)`。
- **類別裡的別名:** `class TestTrick` 裡寫 `@pytest.fixture(name='crash_after_commit') def _p(self)`。
- **繼承:** `class Base` 裡定義 `crash_after_commit`,`class TestTrick(Base)` 繼承它。

建議:照「看不懂就擋」:
- 測試在類別裡時,只要路上任一類別有 fixture,或有 `object` 以外的基底類別,就擋。
- `name=` 的檢查也套用到類別本體。

## 5. 還有幾條 pytest 覆蓋規則沒照顧到:帶預設值的參數、parametrize 同名覆蓋、模組層的別名或匯入
severity: major
blocking: 是
引句:「used, pending = [test], [a.arg for a in test.args.args]」
file: `tools/verify_claims.py:835-844`、`tools/verify_claims.py:815`

有三種寫法會讓 pytest 實際用的不是驗證器看的那支 fixture:
- **帶預設值的參數。** pytest 不把帶預設值的參數當成 fixture 請求,`_used_functions` 卻照樣追過去。
- **parametrize 同名。** `@pytest.mark.parametrize("<fixture 名>", …)` 會直接蓋掉 fixture。
- **模組層的別名或匯入。** 測試檔最外層的 `crash_after_commit = _plain`,或 `from … import plain as crash_after_commit`,都會被 pytest 當成同名 fixture 登錄,蓋掉 conftest 那支。`_fixtures` 只收帶裝飾器的 `def`,看不到這兩種寫法。

重現:以下四例 injection 都設成 `after_dsp_commit`,結果都是 code=0、輸出「通過」。後兩例在測試裡斷言拿到的值 `== 'safe'`,實跑通過:
- `def test_trick(crash_after_commit=None)`
- `@pytest.mark.parametrize('crash_after_commit', [None])`
- 測試檔最外層寫 `crash_after_commit = _plain`
- `from tests.dsp.plainfx import plain as crash_after_commit`

建議:
- `_used_functions` 不追帶預設值的參數。
- 測試帶 parametrize,而且參數名跟用到的 fixture 同名時就擋。
- 測試檔與 conftest 最外層,凡是用賦值或匯入綁定了已用 fixture 的名字,就擋。
- 各補一個案例。

## 6. 正常寫法被擋,而且擋下原因寫錯:`usefixtures`、只能用關鍵字傳的 fixture 參數
severity: minor
blocking: 否
引句:「測試與它用的 fixture 沒引用宣告的注入手段」
file: `tools/verify_claims.py:838`

這兩種寫法 pytest 都會照常解析,但驗證器不追:
- `@pytest.mark.usefixtures('crash_after_commit')` 是很常見的注入寫法。
- `def test(*, crash_after_commit)` 這種只能用關鍵字傳的參數,pytest 也會當成 fixture 請求。

所以驗證器擋下,理由卻寫「沒引用宣告的注入手段」。方向偏保守,不算靜默放行,但讀的人會被誤導去補手段。正式清單目前沒有這兩種寫法,所以沒被誤擋。

重現:兩例 injection 都設成 `after_dsp_commit`,結果都是 code=1,原因是「沒引用宣告的注入手段 after_dsp_commit」。

建議:要嘛把這兩種寫法納入追蹤,要嘛改成明講「用了 usefixtures／只能用關鍵字傳的參數,驗證器不解析、一律擋」。

---

查過、沒有問題的:
- **第 1 輪的修法有效。** 第 1 輪列的文件字串、裝飾器、預設值、assert 訊息、子字串、沒被呼叫的巢狀 def、`if False`、`assertion_free()`、`x.assert_*` 都已照修,對應的參數化案例在。53 支相關測試在複本全數通過。
- **正式清單的手段名是真的注入值。** `after_receiving`、`after_dsp_commit`、`before_dsp_call` 都是傳給 `die_at(world, …)` 的死點名(`tests/executor/test_crash_recovery.py:270`、`:304`、`:362`),會在子行程的 `point == …` 分支實際掛上猝死。`CRASH_CHILD` 是實際以 `python -c` 執行的子程式(`tests/dsp/test_store.py:425`),真正的注入是字串裡的 `store._record_idempotency = die`。四個值都是在非 assert 的程式裡命中。複本實跑正式清單:「通過:5 條宣稱,跑了 77 支證據測試全部通過」。
- **for／with／推導式的屬性目標都會擋。** `for TestTrick.test_trick in …`、`with … as TestTrick.test_trick`、推導式目標、tuple 目標,都擋下「對 TestTrick 的屬性指派」。先把類別指給別名再改屬性的寫法沒擋,但這是刻意繞過,不在「防忘記」範圍內。
- **-B 與 dont_write_bytecode 沒問題。** 帶 -B 的複本沒寫任何檔。不帶 -B 時只多出輔助自己的 .pyc,跟文件講的一致。清單壞掉會回 2,直接跑腳本會回 2 並印出正確指令。

6 條,blocking 5
