severity: major

1. 可變的 `pytest_plugins` 漏出雜湊閉包，外掛能把失敗證據改成通過
severity: major
blocking: 是
引句:「for binding in _bindings(tree.body, PLUGINS_NAME):」
file: `/tmp/codex-p11i1-r3/tests/tools/test_review_adversarial_r3.py:16`
原因：`plugin_modules()` 只讀 `pytest_plugins` 賦值當下的字面內容。`pytest_plugins = []` 後再呼叫 `.append("tests.dsp.evil")` 不算重新綁定，因此 `evil.py` 沒被納入 harness 或雜湊；pytest 執行時卻會載入它。
重現：執行 `cd /tmp/codex-p11i1-r3 && PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -vv -s tests/tools/test_review_adversarial_r3.py::test_mutated_pytest_plugins_list_is_not_closed_or_hashed`。證據測試刻意失敗且外掛無害時，驗證器回傳 1；只把未列入清單的 `evil.py` 改成將失敗報告改綠的 hook、清單完全不動後，驗證器回傳 0，印出「通過:5 條宣稱,跑了 2 支證據測試全部通過」。
建議：要求 `pytest_plugins` 只能有一次不可變的字串或 tuple 字面賦值；若保留 list 寫法，至少要偵測 `.append()`、`.extend()`、下標指派及其他模組層 mutation，讀不懂便擋下。補一支「清單建立後只改動動態加入的外掛，必須因未納入閉包而擋下」的回歸測試。

2. 推導式被整段跳過，已宣告的類別方法可在載入時失效仍獲通過
severity: major
blocking: 是
引句:「if isinstance(node, _COMPREHENSIONS):」
file: `/tmp/codex-p11i1-r3/tests/tools/test_review_adversarial_r3.py:36`
原因：本輪為避免把推導式區域變數誤認成模組重綁，遇到 comprehension 時只取 `NamedExpr` 目標便 `continue`；其運算式中的 `setattr(Handler, "handle", None)` 因此完全不會到達 `_opaque_binding()`。檔案雜湊是最新的，`_defines()` 仍看見原始方法定義，卻沒有察覺載入模組後該 symbol 已是 `None`。
重現：執行同一複本中的 `test_comprehension_can_replace_a_declared_class_symbol_and_still_pass`。獨立子行程確認 `Handler.handle is None` 為 `True`；驗證器仍回傳 0，印出「通過:5 條宣稱,跑了 2 支證據測試全部通過」。
建議：只排除 comprehension 自己的迭代目標，不要跳過整個節點；仍須走訪元素、條件與 iterable，攔截其中的 `setattr`、`delattr` 和類別屬性寫入。用此反例補 S801 回歸測試。

2 條,blocking 2。