severity: major

pytest 外掛實作未納入 harness 雜湊，可把失敗證據改成通過
severity: major
blocking: 是
引句:「+    closure = dependency_closure(root, {*manifest.scope, *references, *tests, *conftests})」
查證: 設計允許由 `pytest_plugins` 或 `addopts -p` 載入外掛，並要求會改變結果的檔案受雜湊保護；見 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:69`、`:70`。實作只把 conftest、設定檔及 AST `import` 閉包列入 harness，沒有解析它們以字串指定的外掛模組。反例在 `/tmp/codex-p11i1/tests/tools/test_verify_claims_adversarial.py:12`。
重現: 在複本建立已雜湊的 conftest，令其以 `pytest_plugins = ('tests.dsp.pass_plugin',)` 載入未列入 harness 的外掛；產品函式刻意回傳 `False`，證據測試本應失敗，外掛卻在 `pytest_runtest_makereport` 將 failure 改成 passed。執行 `cd /tmp/codex-p11i1 && PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -vv tests/tools/test_verify_claims_adversarial.py`，該反例通過；其中明確斷言驗證器回傳 0 且輸出「通過」。這表示外掛程式碼改動不會造成證據過期，卻能左右 JUnit 結果。
建議: 解析 conftest 的 `pytest_plugins` 與 pyproject `addopts` 中每個 `-p`，將 repo 內外掛模組及其靜態依賴強制納入 harness；無法可靠解析、模組位於 repo 外或由動態值產生時直接擋下。加入「只修改外掛實作、不更新清單」必須擋下的回歸測試。

無套件測試目錄的同層靜態匯入未進閉包，弱化 helper 後仍會通過
severity: major
blocking: 是
引句:「+    for base in (root / "src", root):」
查證: 設計要求證據測試沿靜態匯入遞迴涵蓋假物件、樣本與共用夾具；見 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:59`。但 `_module_file` 只從 `src/` 與 repo 根解析名稱；pytest 對沒有 `__init__.py` 的測試目錄可讓 `import assertions` 解析至測試檔同層，而掃描器找不到該檔。反例在 `/tmp/codex-p11i1/tests/tools/test_verify_claims_adversarial.py:38`。
重現: 在複本建立沒有 `tests/__init__.py`、`tests/dsp/__init__.py` 的測試目錄；證據測試寫 `import assertions`，同層 `assertions.py` 將原應驗證錯誤產品結果的 helper 弱化成直接返回。清單刻意不列該 helper。以上同一條 pytest 指令顯示反例通過，並確認驗證器回傳 0、印出「通過」。連同既有測試執行時共 `105 passed`，現有測試未涵蓋此解析方式。
建議: 依匯入者與 pytest import mode 模擬實際解析：對非套件測試模組，至少檢查其所在目錄的同名 `.py`／套件；若同一名稱有多個可能來源則擋下而非猜測。補一支修改同層 helper 後必須報雜湊過期的回歸測試。

2 條,blocking 2。