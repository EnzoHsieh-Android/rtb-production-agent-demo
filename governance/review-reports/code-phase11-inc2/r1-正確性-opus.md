severity: major

## 1. 故障注入的「引用」判定把不會執行的文字也算進去:只在文件字串、裝飾器或參數預設值出現手段名的測試,驗證器照樣放行
severity: major
blocking: 是
引句:「if isinstance(node, ast.Constant) and isinstance(node.value, str) and (」
file: `tools/verify_claims.py:736`

`_mentions` 用 `ast.walk(function)` 走遍整個函式節點。這會走進三種不會執行、也不會注入任何東西的地方:
- 文件字串
- 裝飾器
- 參數預設值

它還是子字串比對,所以 `"NO_DEATH_HERE"` 也會命中 `DEATH`。

其中「參數預設值也算」直接違反兩處說法。一處是 patch 裡的 Systems 家:「只寫在參數列、本體沒用到不算」。另一處是計劃裡增量 2 的解讀。

「測試說明寫了要注入、本體卻忘了注入」正是「防忘記」要擋的情形。

重現:在 `/tmp/p11i2-corr` 另寫一支探測測試,沿用 `repo`、`_with_fault`、`TEST_FAULTS` 造小 repo,把下列測試接成 `failure_injection`、injection 設成 `DEATH`,然後跑 `verify(repo)`。四支的結束代碼都是 0,輸出「通過:5 條宣稱,跑了 3 支證據測試全部通過」:
- `test_docstring_only`:只有文件字串寫「這支注入 DEATH」,本體是 `assert act("update")`。
- `@pytest.mark.filterwarnings("ignore:DEATH")` 加一個普通測試。
- `def test_default_arg(fault="DEATH"): assert act("update")`。
- `assert act("NO_DEATH_HERE" and "update")`。

建議:
- 判定前先拿掉每個函式本體開頭的文件字串,不走 `decorator_list`、`args.defaults` 與 `kw_defaults`。
- 字串常數改成整串相等,或至少比對詞界。
- 為上面四種寫法各補一個會被擋的參數化案例。

## 2. 「有斷言」的判定把不會執行的斷言也算進去:巢狀函式裡、死分支裡、名字以 assert 開頭的呼叫都算
severity: major
blocking: 是
引句:「if name == "raises" or name.startswith("assert"):」
file: `tools/verify_claims.py:749`

`_asserts` 同樣用 `ast.walk(test)`,因此:
- 會走進從來沒被呼叫的巢狀 `def` 或 `lambda`。
- 會走進 `if False:` 這類死分支。
- 任何名字以 `assert` 開頭的呼叫都算數,例如 `assertion_free()`。

這跟驗證器別處的做法不一致:別處都用 `_this_level` 或 `_OWN_SCOPE` 刻意不進子作用域。

重現:同一個探測檔,injection 設成 `DEATH`,三支都得到結束代碼 0、輸出「通過」:
- `test_comment_and_dead_assert`:只有 `if False: assert ...`。
- `test_nested_assert_never_called`:斷言只在一個沒被呼叫的內層函式裡。
- `test_mock_assert_word`:只有 `assertion_free()`。

它們都符合合約 S806「沒有斷言時應擋下」要擋的情形,卻被放行。

建議:
- 只走測試本體這一層:不進 `FunctionDef`、`AsyncFunctionDef`、`Lambda`、`ClassDef`。
- 條件是常數假值的 `if` 或 `while` 本體不算。
- 「assert 開頭」改成屬性呼叫 `x.assert_*`,或 `assert_` 加底線的輔助函式。
- 補對應的會被擋案例。

## 3. fixture 解析沒照 pytest 的覆蓋規則:類別層 fixture 與 `@pytest.fixture(name=...)` 都會讓驗證器看錯那支 fixture
severity: major
blocking: 是
引句:「測試檔與它路上的 conftest 裡最外層的 fixture;離測試越近的蓋掉越遠的(照 pytest)。」
file: `tools/verify_claims.py:713`、`tools/verify_claims.py:720`

`_fixtures` 有兩個問題:
- 只收最外層的 `FunctionDef`,並用 Python 函式名當鍵(`found[statement.name]`)。
- 沒有像 `_sole_statement` 那樣檢查唯一綁定,也沒照「看不懂就擋」處理。

pytest 實際的解析規則有兩處不同:
- 類別裡的 fixture 蓋掉模組與 conftest 的同名 fixture。
- `name=` 別名蓋掉 conftest 裡的同名 fixture。

結果是驗證器檢查的是含注入手段的那支 fixture,pytest 實際跑的卻是沒注入的那支。

重現:
- 在小 repo 的測試檔裡加一個 `class TestOverride`,裡面放一個 `@pytest.fixture def broken(self): return False` 和 `test_class_override(self, broken)`,injection 設成 `timeout_before_commit`。驗證器結束代碼 0。
- 另加一個 `@pytest.fixture(name="broken") def _plain_override(): return False`,配 `test_name_alias(broken)`。驗證器同樣結束代碼 0。
- 另在 `/tmp/p11i2-corr-alias` 直接跑 pytest,確認語意:conftest 裡的 `crash_after_commit` 被測試檔的 `name=` 別名蓋掉(印出 `GOT PLAIN`),也被類別層同名 fixture 蓋掉(印出 `GOT CLASS`)。

建議:照「看不懂就擋」處理,以下情況一律擋:
- 路上任何 fixture 帶 `name=`。
- 測試所在類別裡有與測試參數同名的 fixture。
- 同一檔案同名 fixture 定義不只一次。

測試檔與 conftest 的最外層也要跑 `_opaque_binding`。

## 4. 雜湊輔助自己會寫檔(`__pycache__`),而它的測試看不到
severity: minor
blocking: 否
引句:「只印,不寫任何檔、不寫回清單:清單照樣要人改」
file: `tests/tools/test_verify_claims.py:1507`

重現:在複本裡刪掉 `tools/__pycache__`,在 repo 根跑 `python -m tools.claim_hashes claims/`。之後多出 `tools/__pycache__/claim_hashes.cpython-314.pyc` 和 `verify_claims.cpython-314.pyc`。這兩個檔被 `.gitignore` 蓋住,所以不影響提交,但跟合約 S817「不應寫任何檔」字面不符。

測試以 `cwd=ROOT` 跑正式 repo 的工具,卻只對 tmp 小 repo 拍快照,所以根本看不到工具在自己所在的樹寫了什麼。

建議:
- 程式開頭設 `sys.dont_write_bytecode = True`,或文件改成 `python -B -m ...`。
- 快照改成一併涵蓋 `tools/` 目錄。

## 5. 雜湊輔助在閉包少列時仍印「沒有雜湊跟現況不一樣的檔」,容易被讀成「清單沒問題」
severity: minor
blocking: 否
引句:「print("沒有雜湊跟現況不一樣的檔", file=stream)」
file: `tools/claim_hashes.py:58`

重現:
- 在複本新增 `tests/dsp/conftest_extra.py`,讓 `tests/dsp/conftest.py` 匯入它。輔助只列出 `conftest.py` 的雜湊,完全沒提到新檔;驗證器則會擋「harness 少列」。
- 新增一支路上的 conftest、而既有檔都沒改時,輸出就只有「沒有雜湊跟現況不一樣的檔」。

「閉包少列的檔它不印」只寫在 Systems 家,工具本身的輸出看不出來。這不算靜默放行,因為驗證器照樣會擋,但輸出有誤導之嫌。

建議:把那行改成「清單已列的檔雜湊都跟現況一樣(少列的檔不在此列,以驗證器為準)」。

## 6. 造假示範的「conftest 改結果」其實跟「改了範圍沒更新雜湊」測的是同一件事
severity: minor
blocking: 否
引句:「(_conftest_rewrites_results, "tests/dsp/conftest.py"),」
file: `tests/tools/test_verify_claims.py`

重現:沿用 `_conftest_rewrites_results` 後,再呼叫一次 `write_manifests(repo)` 把雜湊重算。結果測試本身失敗,conftest 的鉤子把它改成通過,驗證器結束代碼 0,輸出「通過:5 條宣稱,跑了 2 支證據測試全部通過」。

計劃已經承認這是天花板。不過這個參數化案例掛在 `test_every_forgery_in_the_plan_is_blocked` 名下,讀起來像是「conftest 改結果會被擋」。再加上雜湊輔助會直接印出要貼的新雜湊,重貼更容易了。

建議:
- 把這個案例改名,例如「conftest 改了卻沒重算雜湊」。
- 另補一支測試把天花板釘住:重算雜湊後會通過,在測試裡註明歸審查員,免得將來有人以為這一點已被擋。

## 7. 冪等放寬:兩個缺口
severity: minor
blocking: 否
引句:「動作取自下面那張表,新增動作沒補範例會被另一支測試擋。」
file: `tests/dsp/test_store.py:107`、`claims/idempotency-unknown-outcome.json`

先說有效的部分:我把 store 改成只對 `pause_campaign` 跳過重放,新測試會紅,改預算的舊測試仍綠。所以它確實補上了暫停這一塊。

兩個缺口:
- **守表的測試不在清單證據裡。** 新測試逐一跑的是 `WRITE_ACTION_PARAMS` 這張表。守表的那支 `test_every_write_action_on_the_http_routes_has_a_version_check_example` 不在這份清單的 evidence 裡,驗證器不會跑它。如果表裡少了暫停,這支測試與版本測試照樣綠,covers 卻還寫著 `pause_campaign`。實際上 CI 的 checks 工作會跑全套,test_store.py 的雜湊也會變,所以不算靜默放行;但這份清單本身撐不住「每一種」。
- **並行同鍵還是只有改預算。** 「最多套用一次」包含並行搶同一把鍵,但兩支並行測試都只用改預算。

建議:
- 把守表的那支測試加進 evidence。
- 或者在新測試裡斷言 `set(WRITE_ACTION_PARAMS) == set(CAMPAIGN_WRITE_ACTIONS)`。

## 8. Mock-DSP 措辭改窄:KEY 那句與程式一致,但同篇的 WHY 仍寫「每一種寫入」
severity: minor
blocking: 否
引句:「WHY: 每一種寫入都必須帶預期版本、版本不符就拒收」
file: `docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md:36`

先說核對結果:讀了 `server.py` 的 `_void_operation` 與 `store.void`,作廢確實只驗預期版本的格式、不比對,也不改廣告狀態。KEY 那句改窄後的措辭跟程式一致。

問題是同篇上方那條 WHY 還寫「每一種寫入…版本不符就拒收」,沒跟著改窄。而作廢同樣要帶 expected_version,只是不比對,所以這句仍然過寬。

建議:把這條 WHY 也改成「每一種會改廣告狀態的寫入(不含作廢)」。

---

另外查過、沒有問題的:
- 註解不在語法樹裡,所以只寫在註解的手段名不算引用。
- `test_redefined` 同名定義兩次的處理正確。
- 正式清單裡標 `DEATH` 的幾支證據,是靠斷言 stderr 的字串才滿足「引用」,屬於語意層、歸審查員。
- 其餘四種造假示範確實各自觸發對應的擋下。
- 這次新增的 27 支驗證器相關測試在複本全數通過。

8 條,blocking 3
