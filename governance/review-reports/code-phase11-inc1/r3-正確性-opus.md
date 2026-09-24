severity: major

**1. addopts 裡的 `-o pythonpath=` 能繞過新加的「pythonpath 只准 src」規則，改了 helper 照樣通過**
severity: major
blocking: 是
引句:「pythonpath = options.get("pythonpath", PYTHONPATH_ALLOWED)」
file: `tools/verify_claims.py:589`、`tools/verify_claims.py:592`、`tools/verify_claims.py:600`
- 原因:
  - 第 2 輪第 3 條的修法只看 `[tool.pytest.ini_options]` 的 `pythonpath` 這個鍵。
  - pytest 的 `-o`/`--override-ini` 也能改 pythonpath，而且同樣寫在 pyproject 的 addopts。
  - 驗證器掃 addopts 時只收 `-p`,其他旗標一律不看。
  - 結果是頂層名稱照樣解析到 `tests/helpers/`,`_module_file` 找不到，就當成第三方套件略過，不擋。
- 重現:`/tmp/p11r3-corr/exp/e1.py`,以小 repo 做底。
  1. pyproject 保留 `pythonpath = ["src"]`,再加 `addopts = '-o "pythonpath=src tests/helpers"'`。
  2. 證據測試寫 `from assertions import check`,讓 test_pause 失敗。基線是 `(1, 擋下 … test_pause 沒通過)`。
  3. 只把 `tests/helpers/assertions.py` 改成 `return True`,清單不動。結果是 `(0, ['通過:5 條宣稱,跑了 2 支證據測試全部通過'])`。
  4. 改用 `--override-ini="pythonpath=…"` 結果相同。
- 建議:
  - addopts 裡出現 `-o`、`--override-ini`(含 `-oxxx` 黏寫)就一律擋，照「看不懂就擋」的原則。
  - 或者只放行一份明列的 key 白名單。
  - 補一條「addopts 用 -o 改 pythonpath」的回歸測試。

**2. conftest 用 `sys.path.insert` 加目錄時，頂層名稱 helper 解析不到，被當成第三方套件靜默略過**
severity: major
blocking: 是
引句:「for base in (root / "src", root):」
file: `tools/verify_claims.py:470`、`tools/verify_claims.py:563`
- 原因:
  - 第 2 輪第 3 條的根因是「repo 內的頂層名稱在 src/ 和根都找不到，就當成外部套件、不擋」。第 3 輪只關掉了 pyproject 的 pythonpath 這一條路。
  - conftest 裡寫 `sys.path.insert(0, …/helpers)`,是舊專案很常見的寫法，不算刻意繞過。這條路還開著。
  - `dependency_closure` 裡 `_module_file` 回 None 的匯入直接丟掉，不留任何擋下原因。
- 重現:`/tmp/p11r3-corr/exp/e2.py`。
  1. `tests/dsp/conftest.py` 寫 `sys.path.insert(0, str(Path(__file__).parent / 'helpers'))`,證據測試 `from assertions import check`。基線 test_pause 失敗，結果是 1。
  2. 把 `tests/dsp/helpers/assertions.py` 弱化成 `return True`,清單不動。結果是 `(0, ['通過:5 條宣稱,跑了 2 支證據測試全部通過'])`。
- 建議:二擇一，或兩個都做。
  - 閉包檔裡只要對 `sys.path` 做 insert、append、extend、指派，或呼叫 `site.addsitedir`,就擋。
  - 解析不到的頂層名稱，要能在 `sys.stdlib_module_names` 或已安裝發行套件的頂層名稱(`importlib.metadata.packages_distributions()`)裡找到才放行，否則擋。

**3. 「屬性指派就擋」只查登錄表或 symbol 所在模組的類別；別的模組用 `模組.登錄表 = …` 擴充登錄表，列舉覆蓋照樣通過**
severity: major
blocking: 是
引句:「if owner is not None and _touches_attribute(node, owner):」
file: `tools/verify_claims.py:415`、`tools/verify_claims.py:660`
- 原因:
  - `read_registry` 呼叫 `_opaque_binding(tree.body, None)` 時 owner 是 None,屬性指派的檢查根本不跑。
  - 而且只看定義登錄表的那一個模組。
  - scope 裡另一個模組寫 `server.WRITE_ACTIONS = (*server.WRITE_ACTIONS, "void")`,執行期登錄表就多了一項，驗證器仍讀到原本的字面值。
  - 這跟第 1 輪「list、set 事後 append」、第 2 輪「屬性指派重綁」是同一族。「外掛模組登記新動作」的寫法也不算刻意繞過。
- 重現:`/tmp/p11r3-corr/exp/e5.py`。
  1. `src/rtb/dsp/extra.py` 做上面那個指派，server.py 匯入 extra,extra.py 也列進 scope。
  2. pytest 實測 `'void' in server.WRITE_ACTIONS` 為真。
  3. 證據只 covers update、pause,驗證器回 `(0, ['通過:5 條宣稱,跑了 2 支證據測試全部通過'])`。
- 建議:
  - 在整個依賴閉包(scope 加 harness)裡，任何屬性名等於列舉常數名或 symbol 類別名的 Attribute 指派、del、setattr、delattr,只要在模組層執行(含 if、try 區塊),一律擋。
  - 正式程式目前沒有 `.CAMPAIGN_WRITE_ACTIONS` 的指派(grep 過),不會誤擋正式清單。

**4. 屬性指派的比對只認 `名字.屬性` 一層：巢狀類別、推導式裡的 setattr 都漏掉**
severity: minor
blocking: 否
引句:「and t.value.id == owner for t in targets)」
file: `tools/verify_claims.py:431`、`tools/verify_claims.py:363`
- 原因:
  - `_touches_attribute` 只在 `t.value` 是 Name 時比對，下面三種寫法都讓 `Outer.Inner().meth()` 在執行期回 2,驗證器卻判 None(放行):
    - `Outer.Inner.meth = …`
    - `setattr(Outer.Inner, …)`
    - Outer 類別本體裡寫 `Inner.meth = …`
  - 第 3 輪新加的「推導式只取 := 目標」讓 `[setattr(H, 'handle', f) for _ in (1,)]` 不再被走到。直接寫 `setattr(H, …)` 會擋，包進推導式就放行。
- 列 minor 的理由：只影響 symbols 的存在判定。證據測試仍然跑在執行期真正生效的程式上，同模組的雜湊也涵蓋替換後的程式。正式清單沒有巢狀 symbol。
- 重現:`/tmp/p11r3-corr/exp/e4.py`(三種巢狀寫法都印 `runtime meth() = 2 | verifier: None`)、`e2.py` 的 E4。
- 建議:
  - 屬性鏈一路往下取到最底的 Name 再比對 owner。
  - 推導式裡碰到 Call 形態的 setattr、delattr 照舊檢查。

**查過、沒問題或已修對的項目**
- 正式五份清單在複本實跑通過:`通過:5 條宣稱,跑了 75 支證據測試全部通過`,驗證器測試 `165 passed`。新規則沒有誤擋正式清單。
- 第 2 輪的修法經造例驗證已修對:
  - 根目錄外掛、tools/ helper 解析到 src、tests 以外會擋。
  - harness 裡手動列的入口會展開成閉包。
  - `[tool.pytest]` 原生表、`import *`、match 都擋。
- 內建外掛名單跟 pytest 9.1.1 的 `builtin_plugins` 完全一致(對稱差為空集合)。pytest 會把這些名字對應到 `_pytest.<名字>`,根目錄同名檔不會被載入。
- 正常寫法放行(`_reference_problem` 回 None):
  - property 的 getter、setter、deleter 鏈
  - `@overload`、`@typing.overload`、類別方法 overload、async overload
  - 推導式變數與登錄表同名
  - 只有型別註記的宣告
  - 巢狀類別的正常定義
- `--junitprefix=` 放在 addopts 之後，命令列後出現的值會蓋過設定。
- `-qp x`、`-rap x` 這類黏寫 pytest 本身不會當外掛載入，所以沒有漏洞。`-p=x` 會被擋。
- 誤擋面(minor,不另列):模組層任何 `類別.任意屬性 = …`(例如 `C.__doc__ = 'x'`)都會讓該類別的全部 symbols 被擋。

4 條,blocking 3。
