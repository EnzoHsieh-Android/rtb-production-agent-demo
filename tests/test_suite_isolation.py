"""整套測試碰不到使用者家目錄的真花費帳(Phase 11B 增量 1,[S932])。

共用夾具(tests/conftest.py)把家目錄指到暫存目錄、清掉模型金鑰與模式開關,並在整套開始時記下真帳狀態、
結束時再比一次;這支測試確認夾具真的生效,也確認到目前為止真帳沒變。
"""

import ast
import os
from pathlib import Path

from rtb.modelledger_view import ledger_path
from tests.conftest import (
    _BEFORE,
    MODEL_ENV,
    REAL_HOME,
    REAL_LEDGER,
    ledger_state,
    real_rtb_listing,
)

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


# 子行程環境一定要帶著的鍵:家目錄、PATH 與清掉的模型開關(拿掉就退回真的家目錄或找得到真的 claude)
PROTECTED_KEYS = frozenset({"HOME", "PATH", *MODEL_ENV})


def _literal_keys(node):
    """字面字串或字面字串的 tuple/list/set;不是就 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Tuple | ast.List | ast.Set) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return {e.value for e in node.elts}
    return None


def _harmless_filter(condition, key_name):
    """推導式的過濾條件只准是「鍵 != 字面鍵」或「鍵 not in (字面鍵…)」,
    而且拿掉的鍵不能是受保護的鍵。"""
    if not (isinstance(condition, ast.Compare) and len(condition.ops) == 1
            and isinstance(condition.left, ast.Name) and condition.left.id == key_name
            and isinstance(condition.ops[0], ast.NotEq | ast.NotIn)):
        return False
    dropped = _literal_keys(condition.comparators[0])
    return dropped is not None and not dropped & PROTECTED_KEYS


def _environ_comprehension(value):
    """逐項走 os.environ.items() 的 dict 推導式:鍵與值原樣帶過,只准濾掉幾個無害的字面鍵
    (例如宣稱驗證器的測試拿掉 PYTHONDONTWRITEBYTECODE)。"""
    if len(value.generators) != 1:
        return False
    generator = value.generators[0]
    source, target = generator.iter, generator.target
    if not (isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute)
            and source.func.attr == "items" and _is_os_environ(source.func.value)
            and isinstance(target, ast.Tuple) and len(target.elts) == 2
            and all(isinstance(e, ast.Name) for e in target.elts)):
        return False
    key_name, value_name = (e.id for e in target.elts)
    return (isinstance(value.key, ast.Name) and value.key.id == key_name
            and isinstance(value.value, ast.Name) and value.value.id == value_name
            and all(_harmless_filter(c, key_name) for c in generator.ifs))


def _starts_from_os_environ(value):
    """env= 的值要是字面 dict、第一個展開的是 os.environ(之後才覆寫);或是逐項走 os.environ.items()、
    只濾掉無害字面鍵的 dict 推導式。"""
    if isinstance(value, ast.DictComp):
        return _environ_comprehension(value)
    if not isinstance(value, ast.Dict) or not value.keys or value.keys[0] is not None:
        return False
    return _is_os_environ(value.values[0])


# pop 的鍵用常數名寫時,只認 rtb.capabilitykit 的這幾個能力金鑰常數
# (值在下面的測試裡核對過不是受保護的鍵),而且要真的從那裡匯入、檔裡沒有別處重新綁這個名字
# (代碼審第 2 輪:只看名字會被同名變數騙過)
POPPABLE_CONSTANTS = frozenset({"KEY_ENV", "APPROVAL_KEY_ENV", "AUDIT_KEY_ENV"})
CAPABILITY_MODULE = "rtb.capabilitykit"


def _bindings(tree):
    """整支檔裡每個名字被綁定的方式:(種類, 來源) 的清單。種類:import(來源是「模組.原名」)或 other
    (賦值、for、with、函式參數、推導式變數…,一律當成重新綁定)。"""
    bound: dict[str, list[tuple[str, str]]] = {}

    def add(name, how, origin=""):
        bound.setdefault(name, []).append((how, origin))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                add(alias.asname or alias.name, "import", f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.asname or alias.name.split(".")[0], "import",
                    alias.name if alias.asname else alias.name.split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            add(node.id, "other")
        elif isinstance(node, ast.arg):
            add(node.arg, "other")
    return bound


def _only_import_of(bound, name, origin):
    """這個名字在檔裡只有一種綁法:從 origin 匯入(沒有別處重新綁定)。"""
    ways = bound.get(name, [])
    return bool(ways) and all(way == ("import", origin) for way in ways)


def _capability_constant(key, bound):
    """pop 的鍵是真的 rtb.capabilitykit 金鑰常數:
    `from rtb.capabilitykit import KEY_ENV` 之後的 KEY_ENV,
    或 `from rtb import capabilitykit` / `import rtb.capabilitykit as capabilitykit` 之後的
    capabilitykit.KEY_ENV。"""
    if isinstance(key, ast.Name):
        return any(_only_import_of(bound, key.id, f"{CAPABILITY_MODULE}.{constant}")
                   for constant in POPPABLE_CONSTANTS)
    if (isinstance(key, ast.Attribute) and key.attr in POPPABLE_CONSTANTS
            and isinstance(key.value, ast.Name)):
        return _only_import_of(bound, key.value.id, CAPABILITY_MODULE)
    return False


def _popped_protected(node, bound):
    """env.pop(<鍵>) 拿掉的是受保護的鍵,或看不出拿掉什麼(不是字面字串、也不是認得的金鑰常數)。"""
    if not node.args:
        return True
    key = node.args[0]
    if _capability_constant(key, bound):
        return False
    keys = _literal_keys(key)
    return keys is None or bool(keys & PROTECTED_KEYS)


def _destroyed_names(tree):
    """被清空、刪鍵、popitem 或 pop 掉受保護鍵的變數(env.clear()、del env[...]、env.popitem()、
    env.pop('HOME')):這種環境不算從 os.environ 起頭。"""
    bound = _bindings(tree)
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and (
                    node.func.attr in ("clear", "popitem")
                    or (node.func.attr == "pop" and _popped_protected(node, bound)))):
            names.add(node.func.value.id)
        elif isinstance(node, ast.Delete):
            names |= {t.value.id for t in node.targets
                      if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)}
    return names


# 唯一的例外(Phase 12 增量 1 合入主線時):展示啟動器測試要驗「子行程只拿到白名單」([S1003]),
# 環境本來就該由啟動器照白名單組,不能從 os.environ 起頭。只認 launcher.child_env(...) 與
# launcher.command_for(...) 而且 user_env 傳的就是 os.environ:啟動器從它照抄 HOME 與 PATH(下面的
# 測試核對),清掉的模型開關本來就不在裡面。其他寫法(user_env 給別的 dict、別的模組的同名函式)照擋。
LAUNCHER_ENV_BUILDERS = frozenset({"child_env", "command_for"})


def _launcher_env(value):
    """launcher.child_env(..., user_env=os.environ) 或
    launcher.command_for(..., user_env=os.environ)。"""
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr in LAUNCHER_ENV_BUILDERS
            and isinstance(value.func.value, ast.Name) and value.func.value.id == "launcher"
            and any(k.arg == "user_env" and _is_os_environ(k.value) for k in value.keywords))


def _environ_names(tree):
    """整支檔裡每次賦值都是 {**os.environ, ...}(或啟動器照白名單從 os.environ 組的,見上)、之後也沒被
    清空或刪鍵的變數名。`command, env = launcher.command_for(...)` 認第二個名字。"""
    good, bad = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                ok = _starts_from_os_environ(node.value) or (
                    _launcher_env(node.value) and node.value.func.attr == "child_env")
                (good if ok else bad).add(target.id)
            elif isinstance(target, ast.Tuple) and len(target.elts) == 2 and all(
                    isinstance(e, ast.Name) for e in target.elts):
                ok = _launcher_env(node.value) and node.value.func.attr == "command_for"
                (good if ok else bad).add(target.elts[1].id)
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
    from rtb import capabilitykit

    assert not {getattr(capabilitykit, name) for name in POPPABLE_CONSTANTS} & PROTECTED_KEYS
    for ok in ("env = {**os.environ, 'A': '1'}\nenv.pop('B', None)\nsubprocess.run([], env=env)",
               "from rtb.capabilitykit import KEY_ENV\nenv = {**os.environ}\n"
               "env.pop(KEY_ENV, None)\nsubprocess.run([], env=env)",
               "from rtb import capabilitykit\nenv = {**os.environ}\n"
               "env.pop(capabilitykit.KEY_ENV, None)\nsubprocess.run([], env=env)",
               "env = {k: v for k, v in os.environ.items() if k != 'X'}\n"
               "subprocess.run([], env=env)",
               "env = {k: v for k, v in os.environ.items() if k not in ('X', 'Y')}\n"
               "subprocess.run([], env=env)",
               "env = launcher.child_env(r, k, user_env=os.environ)\nsubprocess.run([], env=env)",
               "command, env = launcher.command_for(r, [], k, root=x, faults=None, "
               "user_env=os.environ)\nsubprocess.run(command, env=env)"):
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
                  "env = {k: v for k, v in os.environ.keys()}\nsubprocess.run([], env=env)",
                  # 代碼審:推導式也不准拿掉 HOME、PATH、模型開關,值要原樣帶過
                  "env = {k: v for k, v in os.environ.items() if k != 'HOME'}\n"
                  "subprocess.run([], env=env)",
                  "env = {k: v for k, v in os.environ.items() if k not in ('X', 'PATH')}\n"
                  "subprocess.run([], env=env)",
                  "env = {k: v for k, v in os.environ.items() if k == 'PYTHONPATH'}\n"
                  "subprocess.run([], env=env)",
                  "env = {k: '' for k, v in os.environ.items()}\nsubprocess.run([], env=env)",
                  "env = {k: v for k, v in os.environ.items() if not k.startswith('HO')}\n"
                  "subprocess.run([], env=env)",
                  "env = {k: v for k, v in os.environ.items() if k != 'RTB_MODEL_LIVE'}\n"
                  "subprocess.run([], env=env)",
                  "env = {**os.environ}\nenv.pop('HOME')\nsubprocess.run([], env=env)",
                  "env = {**os.environ}\nenv.pop('PATH', None)\nsubprocess.run([], env=env)",
                  "env = {**os.environ}\nenv.pop(name, None)\nsubprocess.run([], env=env)",
                  # 代碼審第 2 輪:常數名要真的來自 rtb.capabilitykit、沒被重新綁定;popitem 也算刪鍵
                  "KEY_ENV = 'HOME'\nenv = {**os.environ}\nenv.pop(KEY_ENV, None)\n"
                  "subprocess.run([], env=env)",
                  "from os import sep as KEY_ENV\nenv = {**os.environ}\nenv.pop(KEY_ENV, None)\n"
                  "subprocess.run([], env=env)",
                  "from rtb.capabilitykit import KEY_ENV\nfor KEY_ENV in ('HOME',):\n    pass\n"
                  "env = {**os.environ}\nenv.pop(KEY_ENV, None)\nsubprocess.run([], env=env)",
                  "env = {**os.environ}\nenv.pop(KEY_ENV, None)\nsubprocess.run([], env=env)",
                  "import other as capabilitykit\nenv = {**os.environ}\n"
                  "env.pop(capabilitykit.KEY_ENV, None)\nsubprocess.run([], env=env)",
                  "env = {**os.environ}\nenv.popitem()\nsubprocess.run([], env=env)",
                  # 啟動器的例外只認 user_env 就是 os.environ、而且是 launcher 的那兩支
                  "env = launcher.child_env(r, k, user_env={'PATH': '/bin'})\n"
                  "subprocess.run([], env=env)",
                  "env = launcher.child_env(r, k, user_env=base)\nsubprocess.run([], env=env)",
                  "env = launcher.child_env(r, k)\nsubprocess.run([], env=env)",
                  "env = other.child_env(r, k, user_env=os.environ)\nsubprocess.run([], env=env)",
                  "env = launcher.command_for(r, [], k, user_env=os.environ)\n"
                  "subprocess.run([], env=env)",
                  "command, env = launcher.child_env(r, k, user_env=os.environ)\n"
                  "subprocess.run(command, env=env)",
                  "env = launcher.child_env(r, k, user_env=os.environ)\nenv.pop('HOME')\n"
                  "subprocess.run([], env=env)",
                  "subprocess.run([], env=launcher.child_env(r, k, user_env=os.environ))"):
        assert env_offenders(ast.parse(probe), "probe"), probe
    # 例外的前提:啟動器照白名單組環境時,HOME 與 PATH 原樣取自傳進去的 os.environ
    from rtb.demo import launcher
    from rtb.demo.keys import DemoKeys

    for role in launcher.Role:
        built = launcher.child_env(role, DemoKeys.generate(), user_env=os.environ)
        assert built["HOME"] == os.environ["HOME"] and built["PATH"] == os.environ["PATH"]
        assert not {name for name in MODEL_ENV if name in built}


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


def test_the_suite_leaves_no_new_file_under_the_real_rtb_dir(request):
    """[S932](Phase 13 增量 4 代碼審 r1 l3):展示啟動器起的子行程在測試裡也換掉帳號家目錄,整套到目前
    為止真的 ~/.rtb 底下沒有多出任何檔(整套結束時共用夾具再比一次,多了就讓整套失敗)。"""
    from rtb.demo import launcher

    before = dict(item for item in request.config.stash[_BEFORE] if item[0] == "rtb-dir")
    assert before["rtb-dir"] == real_rtb_listing()
    assert "runpy" in " ".join(launcher.module_command("rtb.dsp.server", []))  # 夾具換上了
