severity: major

未呼叫的內嵌函式會讓有效證據被誤擋
severity: major
blocking: 是
引句:「for node in ast.walk(function):」
file: `tools/verify_claims.py:946`
file: `/tmp/codex-p11i2-r3/tests/tools/test_r3_external_probe.py:28`

重現:在測試內放一個從未呼叫、含 `if True` 的輔助函式；測試本身正常注入 `DEATH` 並斷言成功。執行 `PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest tests/tools/test_r3_external_probe.py -vv`，實際 pytest 節點通過，但驗證器回 1，理由是 `test_trick 有常數條件(死碼)`。`ast.walk(function)` 穿進了本來明確不該分析的內嵌作用域，與 `_executed` 排除未呼叫巢狀函式的規則不一致。

建議:讓 `_dead_code` 使用會避開內嵌 `FunctionDef`、`AsyncFunctionDef`、`Lambda`、`ClassDef` 的作用域走訪；補一支未呼叫內嵌函式含常數條件、外層證據仍須通過的測試。

`break` 或 `continue` 後的不可達斷言會讓無斷言證據靜默通過
severity: major
blocking: 是
引句:「isinstance(st, ast.Return | ast.Raise) for st in statements[:-1]」
file: `tools/verify_claims.py:952`
file: `/tmp/codex-p11i2-r3/tests/tools/test_r3_external_probe.py:46`

重現:造出一支測試，迴圈第一行執行 `break`，唯一的 `assert False` 放在其後，外層只呼叫 `act(fault)`。同一條探針指令確認真實 pytest 節點通過，且驗證器回 0、印出「通過」；因此證據執行時沒有任何斷言，仍被第 5 步放行。修法只把 `return`、`raise` 視為終止控制流，漏了同樣會使同一區塊後續敘述不可達的 `break`、`continue`。

建議:在迴圈區塊中把 `break`、`continue` 納入「後方仍有敘述就看不懂並擋下」的終止控制流檢查，並各補一個唯一斷言位於其後的反例。

任意名稱以 fixture 結尾的裝飾器可偽造 autouse 注入
severity: major
blocking: 是
引句:「for call in _decorator_call(fixture, "fixture"):」
file: `tools/verify_claims.py:786`
file: `tools/verify_claims.py:819`
file: `/tmp/codex-p11i2-r3/tests/tools/test_r3_external_probe.py:64`

重現:造出一般的 `@fake_fixture(autouse=True)` 裝飾器，它只原樣回傳函式，pytest 不會執行被裝飾的 `mode()`；`mode()` 內放 `DEATH`，實際測試只執行 `assert act("update")`。探針確認真實 pytest 節點通過，驗證器也回 0 並印「通過」。原因是 `_is_fixture` 只檢查裝飾器文字包含 `fixture`，而 `_decorator_call(..., "fixture")` 又只檢查名稱尾碼，於是把無法確認來源的普通裝飾器採信成 pytest autouse fixture，將未執行函式裡的手段算成有效注入；這違反「看不懂就擋」。

建議:像 `pytest.raises` 一樣追蹤 pytest 的實際匯入名稱，只接受可證明來源是 `pytest.fixture` 或 `from pytest import fixture` 的裝飾器；名稱相似但來源不明時直接擋下。補一支非 pytest 的 `*fixture` 裝飾器不得提供注入證據的反例。

另在複本執行正式相關測試：`tests/tools/test_verify_claims.py tests/dsp/test_store.py`，結果為 `326 passed`。

3 條,blocking 3。