"""展示套件的匯入邊界(Phase 12 [S1002] [S1050]):正式程式不匯入展示套件;故障套件只准展示啟動器碰。"""

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
DEMO = SRC / "rtb" / "demo"
LAUNCHER = DEMO / "launcher"
FAULTS_MODULE = "rtb.demo.faults"
LAYERS = ("analyzer", "domain", "dsp", "eval", "executor", "ops")


def _imports(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names += [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
    return names


def _python_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def test_nothing_outside_the_demo_package_imports_it():
    """[S1002] src 裡展示套件以外的任何模組都不匯入展示套件。"""
    offenders = [f"{path.relative_to(SRC)} → {name}"
                 for path in _python_files(SRC) if not path.is_relative_to(DEMO)
                 for name in _imports(ast.parse(path.read_text(encoding="utf-8")))
                 if name == "rtb.demo" or name.startswith("rtb.demo.")]
    assert offenders == []


def test_only_the_launcher_may_reach_the_fault_package():
    """[S1050] src 全部(不含 tests)除了啟動器子目錄與故障套件自己,沒有任何地方匯入或在字串裡提到
    故障套件(用 -m 字串啟動子行程也算)。"""
    faults = DEMO / "faults"
    offenders = [str(path.relative_to(SRC)) for path in _python_files(SRC)
                 if not path.is_relative_to(LAUNCHER) and not path.is_relative_to(faults)
                 and FAULTS_MODULE in path.read_text(encoding="utf-8")]
    offenders += [f"{path.relative_to(SRC)} → {name}" for path in _python_files(SRC)
                  if not path.is_relative_to(LAUNCHER) and not path.is_relative_to(faults)
                  for name in _imports(ast.parse(path.read_text(encoding="utf-8")))
                  if name == FAULTS_MODULE or name.startswith(FAULTS_MODULE + ".")]
    assert offenders == []


def _banned(config: Path) -> dict[str, object]:
    return tomllib.loads(config.read_text(encoding="utf-8"))["lint"]["flake8-tidy-imports"][
        "banned-api"]


@pytest.mark.parametrize("subdir", [LAUNCHER, DEMO / "faults"], ids=["launcher", "faults"])
def test_the_launcher_ruff_bans_differ_from_the_demo_ones_by_the_fault_package_only(subdir):
    """[S1050] ruff 子目錄設定會整張蓋掉上一層的禁令表:啟動器(與故障套件自己,套件內互相匯入)那份
    重列全部禁令、只少故障套件一條。"""
    demo, allowed = _banned(DEMO / "ruff.toml"), _banned(subdir / "ruff.toml")
    assert FAULTS_MODULE in demo
    assert {k: v for k, v in demo.items() if k != FAULTS_MODULE} == allowed


def _ruff_flags(config: Path, source: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(config), "--stdin-filename",
         str(config.parent / "probe.py"), "-"],
        input=source, capture_output=True, text=True, timeout=60, check=False)
    return result.returncode == 1 and "TID251" in result.stdout


@pytest.mark.parametrize(("config", "source", "flagged"), [
    (DEMO / "ruff.toml", "import rtb.demo.faults\n", True),
    (DEMO / "ruff.toml", "import importlib\n", True),
    (LAUNCHER / "ruff.toml", "import rtb.demo.faults\n", False),
    (LAUNCHER / "ruff.toml", "import importlib\n", True),
])
def test_ruff_enforces_the_fault_package_ban(config, source, flagged):
    assert _ruff_flags(config, source) is flagged


@pytest.mark.parametrize("layer", LAYERS)
def test_every_layer_bans_importing_the_demo_package(layer):
    assert _ruff_flags(SRC / "rtb" / layer / "ruff.toml", "import rtb.demo\n")
