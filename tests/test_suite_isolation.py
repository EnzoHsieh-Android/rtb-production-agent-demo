"""整套測試碰不到使用者家目錄的真花費帳(Phase 11B 增量 1,[S932])。

共用夾具(tests/conftest.py)把家目錄指到暫存目錄、清掉模型金鑰與模式開關,並在整套開始時記下真帳狀態、
結束時再比一次;這支測試確認夾具真的生效,也確認到目前為止真帳沒變。
"""

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


def test_subprocess_tests_inherit_the_isolated_environment():
    """子行程測試自己給環境時,要從 os.environ 起頭再覆寫(否則家目錄會退回帳號的真家目錄)。"""
    import ast

    root = Path(__file__).resolve().parent
    offenders = []
    for path in sorted(root.rglob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "env" and isinstance(keyword.value, ast.Dict) and not any(
                        key is None for key in keyword.value.keys):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []
