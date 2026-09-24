"""整套測試碰不到使用者家目錄的真花費帳(Phase 11B 增量 1,[S932])。

共用夾具(tests/conftest.py)把家目錄指到暫存目錄、清掉模型金鑰與模式開關,並在整套開始時記下真帳狀態、
結束時再比一次;這支測試確認夾具真的生效,也確認到目前為止真帳沒變。
"""

import ast
import os
from pathlib import Path

from rtb.modelledger_view import ledger_path
from tests.conftest import _BEFORE, MODEL_ENV, REAL_HOME, REAL_LEDGER, ledger_state

# 收集階段(pytest_configure 之後、任何夾具之前)看到的環境:開關要已經清掉、PATH 與 HOME 要已經換掉
COLLECTED = {"switches": [name for name in MODEL_ENV if name in os.environ],
             "home": Path.home(), "path": os.environ.get("PATH", "")}


def _account_home_at_collection():
    from rtb import modelledger_view

    try:
        modelledger_view.account_home()
    except RuntimeError:
        return "RuntimeError"
    return "returned"


COLLECTED["account_home"] = _account_home_at_collection()


def test_the_suite_never_touches_the_real_ledger(request, monkeypatch):
    assert Path.home() != REAL_HOME
    assert ledger_path() != REAL_LEDGER
    assert not ledger_path().is_relative_to(REAL_HOME)
    assert not [name for name in MODEL_ENV if name in os.environ]
    assert request.config.stash[_BEFORE] == ledger_state()
    monkeypatch.undo()  # 測試自己的替換器還原,不會把家目錄還原成真的(夾具用自己的替換器)
    assert Path.home() != REAL_HOME


def test_the_suite_can_never_reach_the_real_claude():
    """[S935]:PATH 上找不到任何 claude;即時與錄製開關被清掉;就算入口照常判模式,也只會是錄製。"""
    import shutil

    from rtb import modelclient as mc
    from tests.conftest import SYSTEM_PATH

    assert shutil.which("claude") is None
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[1:] == list(SYSTEM_PATH) and not any(Path(p, "claude").exists() for p in parts)
    for name in ("RTB_MODEL_LIVE", "RTB_MODEL_RECORD"):
        assert name not in os.environ
    assert COLLECTED["switches"] == [] and COLLECTED["home"] != REAL_HOME
    assert COLLECTED["path"].split(os.pathsep)[1:] == list(SYSTEM_PATH)
    live_env = {**os.environ, mc.LIVE_ENV: "1"}
    claude = shutil.which("claude", path=live_env["PATH"])
    assert mc.settings_from_env(live_env, "demo-1", claude).mode is mc.Mode.RECORDED


def test_each_test_gets_its_own_temp_dir_and_empty_policy_sources(tmp_path):
    """代碼審第 1 輪:系統共用暫存目錄與本機的管理政策來源都不該影響測試結果。"""
    import tempfile

    from rtb import modelclaude as cc

    assert Path(tempfile.gettempdir()).is_relative_to(tmp_path.parent.parent)
    for directory in cc.MANAGED_DIRS:
        assert directory.is_relative_to(tmp_path.parent.parent) and not any(directory.iterdir())
    assert all(p.is_relative_to(tmp_path.parent.parent) for p in cc.MDM_PLISTS)


def _is_os_environ(node):
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _starts_from_os_environ(value):
    """env= 的值要是字面 dict、第一個展開的是 os.environ(之後才覆寫);或是逐項走 os.environ.items()
    的 dict 推導式(只濾掉幾個鍵,例如宣稱驗證器的測試拿掉 PYTHONDONTWRITEBYTECODE)。"""
    if isinstance(value, ast.DictComp) and len(value.generators) == 1:
        source = value.generators[0].iter
        return (isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute)
                and source.func.attr == "items" and _is_os_environ(source.func.value))
    if not isinstance(value, ast.Dict) or not value.keys or value.keys[0] is not None:
        return False
    return _is_os_environ(value.values[0])


def _destroyed_names(tree):
    """被清空或刪鍵的變數(env.clear()、del env[...]):這種環境不算從 os.environ 起頭。"""
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "clear" and isinstance(node.func.value, ast.Name)):
            names.add(node.func.value.id)
        elif isinstance(node, ast.Delete):
            names |= {t.value.id for t in node.targets
                      if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)}
    return names


def _environ_names(tree):
    """整支檔裡每次賦值都是 {**os.environ, ...}、之後也沒被清空或刪鍵的變數名。"""
    good, bad = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    (good if _starts_from_os_environ(node.value) else bad).add(target.id)
    return good - bad - _destroyed_names(tree)


def env_offenders(tree, label):
    names = _environ_names(tree)
    return [f"{label}:{node.lineno}" for node in ast.walk(tree) if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "env" and not _starts_from_os_environ(keyword.value)
            and not (isinstance(keyword.value, ast.Name) and keyword.value.id in names)]


def _test_files():
    root = Path(__file__).resolve().parent
    return root, sorted([*root.rglob("test_*.py"), *root.rglob("conftest.py")])


def test_subprocess_tests_inherit_the_isolated_environment():
    """子行程測試自己給環境時,一律寫成 {**os.environ, ...}(HOME、PATH 與清掉的模型開關才傳得下去);
    變數、dict(...)、別的 dict 展開、之後清空或刪鍵的都抓(代碼審第 2、3 輪)。"""
    root, files = _test_files()
    offenders = []
    for path in files:
        offenders += env_offenders(ast.parse(path.read_text(encoding="utf-8")),
                                   str(path.relative_to(root)))
    assert offenders == []
    for ok in ("env = {**os.environ, 'A': '1'}\nenv.pop('B', None)\nsubprocess.run([], env=env)",
               "env = {k: v for k, v in os.environ.items() if k != 'X'}\n"
               "subprocess.run([], env=env)"):
        assert env_offenders(ast.parse(ok), "probe") == [], ok
    for probe in ("env = {'PYTHONPATH': 'src'}\nsubprocess.run([], env=env)",
                  "env = {**os.environ}\nenv = {}\nsubprocess.run([], env=env)",
                  "subprocess.run([], env=dict(os.environ))",
                  "subprocess.run([], env={'A': '1', **os.environ})",
                  "subprocess.run([], env={'PYTHONPATH': 'src'})",
                  "subprocess.run([], env={**base})",
                  "base = {}\nsubprocess.run([], env={**base, 'A': '1'})",
                  "env = {**os.environ}\nenv.clear()\nsubprocess.run([], env=env)",
                  "env = {**os.environ}\ndel env['HOME']\nsubprocess.run([], env=env)",
                  "env = {k: v for k, v in base.items()}\nsubprocess.run([], env=env)",
                  "env = {k: v for k, v in os.environ.keys()}\nsubprocess.run([], env=env)"):
        assert env_offenders(ast.parse(probe), "probe"), probe


# 碰得到帳號家目錄(花費帳、啟用紀錄)的模組:子行程的程式碼提到它們,就要先換掉帳號家目錄的讀法
ACCOUNT_SENSITIVE = ("modelclient", "modelledger", "modelverify", "modelclaude", "modelcore",
                     "rtb.eval", "tests.model")


def _code_of(call, assigned):
    """subprocess 呼叫裡 [..., "-c", <程式碼>, ...] 的程式碼(變數就找它在檔裡的賦值)。"""
    if not call.args or not isinstance(call.args[0], ast.List):
        return None
    items = call.args[0].elts
    for index, item in enumerate(items[:-1]):
        if isinstance(item, ast.Constant) and item.value == "-c":
            code = items[index + 1]
            if isinstance(code, ast.Name):
                return " ".join(ast.unparse(v) for v in assigned.get(code.id, []))
            return ast.unparse(code)
    return None


def child_code_offenders(tree, label):
    """子行程的程式碼提到會碰帳號家目錄的模組,卻沒有先換掉 account_home(子行程沒有共用夾具)。
    換法是共用的 child_prelude(或自己寫 account_home 的替換)。"""
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            code = _code_of(node, assigned)
            if code and any(m in code for m in ACCOUNT_SENSITIVE) and not (
                    "account_home" in code or "child_prelude(" in code):
                offenders.append(f"{label}:{node.lineno}")
    return offenders


def test_child_processes_replace_the_account_home_first():
    """帳號家目錄只讀帳號資料庫(不看任何環境變數,代碼審第 3 輪):子行程碰到模型用戶端時,
    要在自己的 -c 開頭先換掉 account_home(共用 conftest.child_prelude)。"""
    root, files = _test_files()
    offenders = []
    for path in files:
        offenders += child_code_offenders(ast.parse(path.read_text(encoding="utf-8")),
                                          str(path.relative_to(root)))
    assert offenders == []
    for probe in ("subprocess.run([sys.executable, '-c', 'from rtb import modelclient'])",
                  "code = 'from rtb import modelledger'\n"
                  "subprocess.run([sys.executable, '-c', code])",
                  "subprocess.run([sys.executable, '-c', f'from rtb.eval import record\\n{x}'])"):
        assert child_code_offenders(ast.parse(probe), "probe"), probe
    ok = "subprocess.run([sys.executable, '-c', child_prelude(h) + 'from rtb import modelclient'])"
    assert child_code_offenders(ast.parse(ok), "probe") == []


def test_a_child_process_books_into_the_test_home_not_the_account_home(_isolated_home):
    """子行程沒有共用夾具:用 conftest.child_prelude 在 -c 開頭換掉帳號家目錄的讀法。"""
    import subprocess
    import sys

    from tests.conftest import child_prelude

    src = Path(__file__).resolve().parents[1] / "src"
    code = child_prelude(_isolated_home) + (
        "from rtb.modelledger_view import ledger_path\nprint(ledger_path())\n")
    result = subprocess.run([sys.executable, "-c", code],
                            env={**os.environ, "PYTHONPATH": str(src)}, capture_output=True,
                            text=True, timeout=60, check=True)
    assert Path(result.stdout.strip()) == ledger_path()
    assert not ledger_path().is_relative_to(REAL_HOME)


def test_the_account_home_reads_no_environment_variable(monkeypatch):
    """帳號家目錄只讀帳號資料庫:怎麼設環境變數都搬不走花費帳與啟用紀錄(代碼審第 3 輪)。"""
    import inspect
    import subprocess
    import sys

    from tests.conftest import PRODUCT_ACCOUNT_HOME

    src = Path(__file__).resolve().parents[1] / "src"
    code = ("from rtb import modelledger_view as v\n"  # 故意不換 account_home:只印路徑、不開帳
            "print(v.account_home())\n")
    elsewhere = str(Path(os.environ["HOME"]) / "elsewhere")  # 測試的暫存家目錄底下
    decoys = {"RTB_TEST_ACCOUNT_HOME": elsewhere, "PYTEST_CURRENT_TEST": "x", "HOME": elsewhere}
    result = subprocess.run([sys.executable, "-c", code],
                            env={**os.environ, "PYTHONPATH": str(src), **decoys},
                            capture_output=True, text=True, timeout=60, check=True)
    assert Path(result.stdout.strip()) == REAL_HOME
    for name, value in decoys.items():
        monkeypatch.setenv(name, value)
    assert PRODUCT_ACCOUNT_HOME() == REAL_HOME
    assert "environ" not in inspect.getsource(PRODUCT_ACCOUNT_HOME)


def test_inside_pytest_the_account_home_refuses_until_the_fixture_sets_it():
    """pytest 一啟動就把帳號家目錄換成「還沒設好就丟錯」:收集階段、夾具之前都碰不到真的家目錄。"""
    import pytest

    from tests.conftest import unset_account_home

    assert COLLECTED["account_home"] == "RuntimeError"
    with pytest.raises(RuntimeError):
        unset_account_home()


def test_the_verification_helper_refuses_the_product_account_home(monkeypatch, tmp_path):
    """測試輔助寫啟用紀錄:帳號家目錄還是產品那一支(沒被夾具換掉)就拒寫。"""
    import pytest

    from rtb import modelledger_view as view
    from tests.model import fakes

    stand_in = tmp_path / "stand-in"  # 代替產品那一支(變異檢查時也不會寫到真的)

    def product():
        return stand_in

    monkeypatch.setattr(fakes, "PRODUCT_ACCOUNT_HOME", product)
    monkeypatch.setattr(fakes, "REAL_HOME", tmp_path / "somewhere-else")
    monkeypatch.setattr(view, "account_home", product)
    with pytest.raises(RuntimeError):
        fakes.write_verification()
    assert not stand_in.exists()


def test_the_verification_helper_refuses_the_real_home(monkeypatch, tmp_path):
    """測試輔助寫啟用紀錄:算出來的路徑在真的家目錄底下就拒寫(就算帳號家目錄被換成別的函式)。"""
    import pytest

    from rtb import modelledger_view as view
    from tests.model import fakes

    pretend_real = tmp_path / "pretend-real-home"  # 代替真的家目錄(變異檢查時也不會寫到真的)
    monkeypatch.setattr(fakes, "REAL_HOME", pretend_real)
    monkeypatch.setattr(view, "account_home", lambda: pretend_real)
    with pytest.raises(RuntimeError):
        fakes.write_verification()
    assert not pretend_real.exists()
