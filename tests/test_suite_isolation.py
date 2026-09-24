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


def _starts_from_os_environ(value):
    """env= 的值要是字面 dict、第一個展開的是 os.environ(之後才覆寫)。"""
    if not isinstance(value, ast.Dict) or not value.keys or value.keys[0] is not None:
        return False
    first = value.values[0]
    return (isinstance(first, ast.Attribute) and first.attr == "environ"
            and isinstance(first.value, ast.Name) and first.value.id == "os")


def _environ_names(tree):
    """整支檔裡每次賦值都是 {**os.environ, ...} 的變數名(env = {...}; run(env=env) 這種寫法)。"""
    good, bad = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    (good if _starts_from_os_environ(node.value) else bad).add(target.id)
    return good - bad


def env_offenders(tree, label):
    names = _environ_names(tree)
    return [f"{label}:{node.lineno}" for node in ast.walk(tree) if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "env" and not _starts_from_os_environ(keyword.value)
            and not (isinstance(keyword.value, ast.Name) and keyword.value.id in names)]


def test_subprocess_tests_inherit_the_isolated_environment():
    """子行程測試自己給環境時,一律寫成 {**os.environ, ...}
    (共用夾具設的帳號家目錄覆寫變數才傳得下去);
    變數、dict(...)、沒從 os.environ 起頭的都抓(代碼審第 2 輪)。"""
    root = Path(__file__).resolve().parent
    offenders = []
    for path in sorted([*root.rglob("test_*.py"), *root.rglob("conftest.py")]):
        offenders += env_offenders(ast.parse(path.read_text(encoding="utf-8")),
                                   str(path.relative_to(root)))
    assert offenders == []
    ok = "env = {**os.environ, 'A': '1'}\nenv.pop('B', None)\nsubprocess.run([], env=env)"
    assert env_offenders(ast.parse(ok), "probe") == []
    for probe in ("env = {'PYTHONPATH': 'src'}\nsubprocess.run([], env=env)",
                  "env = {**os.environ}\nenv = {}\nsubprocess.run([], env=env)",
                  "subprocess.run([], env=dict(os.environ))",
                  "subprocess.run([], env={'A': '1', **os.environ})",
                  "subprocess.run([], env={'PYTHONPATH': 'src'})"):
        assert env_offenders(ast.parse(probe), "probe"), probe


def test_a_child_process_books_into_the_test_home_not_the_account_home():
    """子行程沒有測試的替換:帳號家目錄靠共用夾具設好的覆寫環境變數(只在測試開)傳下去。"""
    import subprocess
    import sys

    src = Path(__file__).resolve().parents[1] / "src"
    code = "from rtb.modelledger_view import ledger_path\nprint(ledger_path())\n"
    result = subprocess.run([sys.executable, "-c", code],
                            env={**os.environ, "PYTHONPATH": str(src)}, capture_output=True,
                            text=True, timeout=60, check=True)
    assert Path(result.stdout.strip()) == ledger_path()
    assert not ledger_path().is_relative_to(REAL_HOME)


def test_the_verification_helper_refuses_outside_the_test_fixture(monkeypatch, tmp_path):
    """測試輔助寫啟用紀錄:不在共用夾具底下(沒有帳號家目錄覆寫)就拒寫,不會寫進真的家目錄。"""
    import pytest

    from rtb import modelledger_view as view
    from tests.conftest import ACCOUNT_HOME_ENV
    from tests.model import fakes

    monkeypatch.delenv(ACCOUNT_HOME_ENV)
    pretend_real = tmp_path / "pretend-real-home"  # 代替真的家目錄(變異檢查時也不會寫到真的)
    monkeypatch.setattr(view, "account_home", lambda: pretend_real)
    with pytest.raises(RuntimeError):
        fakes.write_verification()
    assert not pretend_real.exists()
