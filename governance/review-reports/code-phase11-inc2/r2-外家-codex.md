severity: major

常數真值分支的不可達 else 斷言會讓無斷言證據靜默通過
severity: major
blocking: 是
引句:「elif isinstance(node, ast.If | ast.While) and _constant_false(node.test):」
file: `tools/verify_claims.py:866`
重現: 在 `/tmp/codex-p11i2-r2` 造出故障注入測試，實際路徑只有 `if True: act(fault)`，唯一的 `assert False` 放在不可能執行的 `else`；執行 `PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest tests/tools/test_r2_external_probe.py -vv`，探針確認驗證器回 0 並印「通過」。修法只排除了常數假值的本體，沒有對稱排除常數真值的 `else`。
建議: 對 `if` 的常數真假分別只走可達分支；其餘無法靜態判定的條件依既定原則直接擋下，並補 `if True ... else assert`、`if False ... else assert` 的正反例。

任意物件的 raises 方法都被誤認成 pytest.raises
severity: major
blocking: 是
引句:「if name == "raises" or name.startswith("assert_"):」
file: `tools/verify_claims.py:892`
重現: 在同一複本造出 `Probe().raises(); act(fault)`，其中 `Probe.raises()` 只回傳 `True`，測試沒有 `assert`、沒有 `pytest.raises`；同一條 pytest 指令的探針確認驗證器仍回 0 並印「通過」。原因是判定只取呼叫名稱最後一段，任何 `.raises()` 都命中。
建議: `raises` 只接受可確認為 `pytest.raises` 的屬性呼叫；無法確認來源時按「看不懂就擋」，並加入自訂物件 `.raises()` 的反例。

標準的 autouse 與 usefixtures fixture 會被誤擋
severity: minor
blocking: 否
引句:「used, pending = [test], [a.arg for a in test.args.args]」
file: `tools/verify_claims.py:838`
重現: 在同一複本分別造出 `@pytest.fixture(autouse=True)` 與 `@pytest.mark.usefixtures("inject")`，兩者都在 fixture 內設定宣告的 `DEATH` 手段，證據測試本身正常通過；探針確認驗證器兩案皆回 1，理由都是「沒引用宣告的注入手段 DEATH」。四支探針合跑結果為 `4 passed`，其中這兩支明確斷言上述誤擋。fixture 解析目前只沿函式參數追蹤，漏掉 pytest 的兩種正式啟用方式。
建議: 精確解析字面 `autouse=True` 與 `pytest.mark.usefixtures()`，把對應 fixture 納入遞迴；參數或 fixture 名不是可確定的字面值時再依「看不懂就擋」。

補充確認: 相關既有測試 `tests/tools/test_verify_claims.py tests/dsp/test_store.py` 共 301 支全綠。正式執行 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -B -m tools.claim_hashes claims/` 回 0；執行前後比對完整路徑清單與所有檔案 SHA-256，兩者差異皆為 0，確認沒有新增或改寫檔案。

3 條,blocking 2。