severity: major

斷言訊息提到注入名稱，就會把未注入的測試判成通過
severity: major
blocking: 是
引句:「token in node.value」
位置: `tools/verify_claims.py:736`
重現: 在 `/tmp/codex-p11i2` 的小型 repo 加入 `assert act("update"), "DEATH should have been injected"`，清單宣告 `injection: DEATH`；測試完全沒有送入 DEATH，但驗證器輸出「通過:5 條宣稱,跑了 3 支證據測試全部通過」。這不是故意改驗證器，只是正常的失敗診斷訊息，卻被 `_mentions` 當成注入行為。
建議: 不要計算 `Assert.msg`、docstring、型別註記及裝飾器中的字串；字串注入至少必須出現在測試或 fixture 的可執行值路徑，例如呼叫參數或實際被讀取的賦值，並加一支斷言訊息反例測試。

未呼叫的巢狀函式內有 assert，也會讓無斷言證據靜默通過
severity: major
blocking: 是
引句:「測試本體有 assert、用 pytest.raises,或呼叫 assert 開頭的輔助函式。」
位置: `tools/verify_claims.py:749`
重現: 造出測試，本體只執行 `act(fault)`，另在從未呼叫的巢狀函式 `never_called()` 裡放 `assert False`。執行指定 pytest 反例後，測試本身通過，驗證器也輸出整體「通過」。原因是 `_asserts` 對整棵 `FunctionDef` 使用 `ast.walk`，連未執行的巢狀作用域都算。
建議: 走訪測試本體時遇到巢狀 `FunctionDef`、`AsyncFunctionDef`、`Lambda`、`ClassDef` 就停止下鑽；只有測試自身可執行作用域的 `assert`、`pytest.raises` 或允許的斷言呼叫才能成立。

「只印不寫」工具實際會在 repo 產生 bytecode 檔
severity: major
blocking: 是
引句:「只印,不寫任何檔、不寫回清單:清單照樣要人改,改動照樣進提交讓審查員看到。」
位置: `tools/claim_hashes.py:15`
位置: `tests/tools/test_verify_claims.py:1505`
重現: 確認 `/tmp/codex-p11i2/tools/__pycache__` 不存在後，執行 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m tools.claim_hashes claims/`；工具雖印出正常結果，卻新增 `tools/__pycache__/claim_hashes.cpython-314.pyc` 和 `tools/__pycache__/verify_claims.cpython-314.pyc`。現有測試只快照臨時的 `repo` fixture，但子行程以專案 `ROOT` 為 cwd 並從那裡匯入工具，因此完全漏看真正被寫入的位置。
建議: 將正式用法改成 `python -B -m tools.claim_hashes claims/`，並讓 S817 測試在沒有既存 `__pycache__` 的工具複本中執行、比較整個執行根目錄前後內容。

合法的 pytest fixture 別名會在測試可通過時被誤擋
severity: minor
blocking: 否
引句:「found[statement.name] = statement」
位置: `tools/verify_claims.py:713`
重現: 造出 `@pytest.fixture(name="fault_setup") def _fault_setup(monkeypatch): ...`，測試以 `fault_setup` 參數使用它。直接呼叫第 6 步執行證據得到 `runtime_problems=[]`，但完整驗證器在第 5 步擋下並稱「沒引用宣告的注入手段 DEATH」。pytest 使用裝飾器公開的 `fault_setup` 名稱；驗證器卻只以函式內部名稱 `_fault_setup` 建索引。
建議: 解析 `pytest.fixture(name="<字面值>")` 並以公開名稱登錄；若 `name` 不是字面字串，依既定「看不懂就擋」原則回報不支援的 fixture 寫法，而不是誤報沒有注入。

4 條,blocking 3