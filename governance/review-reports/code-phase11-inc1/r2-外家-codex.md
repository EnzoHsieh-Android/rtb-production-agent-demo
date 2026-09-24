severity: major

Repo 根的 pytest 外掛會被閉包丟棄，失敗證據可被改成通過
severity: major
blocking: 是
引句:「return rel.split("/")[0] in CODE_ROOTS or rel == ROOT_CONFTEST」
file: `/tmp/codex-p11i1-r2/tests/tools/test_review_adversarial_r2.py:16`
原因:`addopts -p evil_plugin` 能正常解析到 repo 根的 `evil_plugin.py`，但 `_in_code()` 只接受 `src/`、`tests/` 和根 `conftest.py`，因此外掛不進 harness，也不受雜湊保護。pytest 仍會載入它。
重現:`cd /tmp/codex-p11i1-r2 && PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -vv -s tests/tools/test_review_adversarial_r2.py::test_root_addopts_plugin_is_dropped_from_the_harness_closure`；證據測試被刻意改成失敗，未列入清單的根外掛把結果改綠，驗證器回傳 0 並印出「通過:5 條宣稱,跑了 2 支證據測試全部通過」。
建議:凡 `_plugin_files()` 已解析為 repo 內檔案，就必須不受 `_in_code()` 根目錄限制地加入 harness 閉包，並遞迴追蹤其 repo 內匯入；補「只改根外掛、清單不更新必須擋下」的回歸測試。

模式比對的捕獲名稱不算重綁，列舉在執行期多出項目仍會通過
severity: major
blocking: 是
引句:「這一層所有會綁定或刪掉這個名字的地方:def、class、賦值(含 +=、for、with)、匯入、del。」
file: `/tmp/codex-p11i1-r2/tests/tools/test_review_adversarial_r2.py:41`
原因:`_bound_name()` 只認 `ast.Name`、定義、例外處理器與 import alias，漏掉 `ast.MatchAs.name`、`ast.MatchStar.name` 和 `ast.MatchMapping.rest`。因此 `case {"actions": WRITE_ACTIONS}` 在模組層確實重綁登錄表，驗證器卻仍讀取前面的舊 tuple。
重現:執行上述反例檔的 `test_match_capture_rebinding_of_registry_is_not_seen`；執行期 `WRITE_ACTIONS` 是 `('update', 'pause', 'void')`，證據只 covers `update,pause`，驗證器仍回傳 0 並印通過。
建議:用具作用域意識的 binding visitor 收齊模式比對捕獲名稱；同時補 registry 與 symbol 各一支 match-capture 回歸測試。無法靜態展開的綁定形式應擋下。

手動列入 harness 的動態入口不會展開自己的靜態依賴
severity: major
blocking: 是
引句:「root, {*manifest.scope, *references, *tests, *conftests, *addopts})」
file: `/tmp/codex-p11i1-r2/tests/tools/test_review_adversarial_r2.py:93`
原因:依賴閉包的起點沒有 `manifest.harness`。測試以動態匯入載入 `launcher.py` 時，作者可依設計手動把 launcher 列進 harness；但 launcher 靜態匯入的 `hidden.py` 仍不會被要求列入或雜湊。
重現:執行反例檔的 `test_declared_dynamic_harness_entry_does_not_close_over_static_imports`；清單已列 `launcher.py`，其 `hidden.py` 在清單寫好後被改動，驗證器仍回傳 0 並印通過。
建議:把已宣告的 harness Python 檔也加入閉包起點，遞迴追蹤其 repo 內靜態匯入；至少要讓作者手動補上的動態入口真正形成依賴閉包。

Comprehension 的區域迴圈變數被誤認成模組重綁
severity: minor
blocking: 否
引句:「_OWN_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)」
file: `/tmp/codex-p11i1-r2/tests/tools/test_review_adversarial_r2.py:72`
原因:Python 3 的 comprehension 有自己的作用域，但 `_this_level()` 會走進 `ListComp` 等節點，把其中的 `act for act in ()` 當成模組層對 `act` 的第二次綁定。
重現:執行反例檔的 `test_comprehension_target_does_not_rebind_a_module_symbol_but_is_blocked`；合法的 `_unused = [act for act in ()]` 沒有改掉模組函式，驗證器卻回傳 1，稱 `act`「不是唯一一次的最外層定義」。
建議:改用作用域感知的 AST visitor，排除 comprehension 自己的迴圈目標；但仍須辨識 comprehension 內會綁到外層的 named expression，不能單純跳過整個節點。

合法的 --junitprefix 設定會讓所有通過測試被誤報成未收集
severity: minor
blocking: 否
引句:「return ".".join([file.removesuffix(".py").replace("/", "."), *classes]), function」
file: `/tmp/codex-p11i1-r2/tests/tools/test_review_adversarial_r2.py:122`
原因:pytest 允許在 `addopts` 使用 `--junitprefix=ci`，此時 JUnit 的 classname 會帶前綴；`judge_junit()` 卻只接受未加前綴的精確 classname。這個選項既未禁止，也未被驗證器覆寫。
重現:執行反例檔的 `test_normal_junit_prefix_option_makes_passing_tests_look_uncollected`；兩支證據測試實際通過，驗證器回傳 1，誤報 `test_update`、`test_pause`「沒收集到」。連同正式回歸測試執行結果為 `145 passed`，五支反例亦全部如預期重現。
建議:驗證器呼叫 pytest 時明確加上 `--junitprefix=`，固定自己解析的 JUnit 格式；或解析有效前綴後再對回節點。補一支含 `--junitprefix` 的正常設定測試。

5 條,blocking 3。