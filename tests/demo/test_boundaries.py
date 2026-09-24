"""展示套件的匯入邊界(Phase 12 [S1002] [S1050]):正式程式不匯入展示套件;故障套件只准展示啟動器碰。"""

import ast
import os
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


def _imports(tree: ast.AST, package: str = "") -> list[str]:
    """匯入的模組名;相對匯入依所在套件還原成絕對名(代碼審 r1 s2:`from . import demo`
    原本看不見)。"""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parts, node.module] if node.module else parts)
            else:
                base = node.module or ""
            names += [base, *(f"{base}.{a.name}" for a in node.names)]
    return names


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts[:-1])


def _python_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def test_nothing_outside_the_demo_package_imports_it():
    """[S1002] src 裡展示套件以外的任何模組都不匯入展示套件。"""
    offenders = [f"{path.relative_to(SRC)} → {name}"
                 for path in _python_files(SRC) if not path.is_relative_to(DEMO)
                 for name in _imports(ast.parse(path.read_text(encoding="utf-8")),
                                      _package_of(path))
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
                  for name in _imports(ast.parse(path.read_text(encoding="utf-8")),
                                       _package_of(path))
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
    (DEMO / "ruff.toml", "import rtb.eval\n", True),  # 代碼審 r1 a6
    (DEMO / "ruff.toml", "import rtb.ops.trace\n", False),  # 准:展示讀唯讀出口
])
def test_ruff_enforces_the_fault_package_ban(config, source, flagged):
    assert _ruff_flags(config, source) is flagged


@pytest.mark.parametrize("layer", LAYERS)
def test_every_layer_bans_importing_the_demo_package(layer):
    assert _ruff_flags(SRC / "rtb" / layer / "ruff.toml", "import rtb.demo\n")


# ---- 代碼審 r1 s2:最上層共用模組與相對匯入的間接路徑 ----
def test_the_top_level_shared_modules_ban_the_demo_package_too():
    """src/rtb 最上層的共用模組(sqlitekit、httpkit…)吃的是 pyproject 的設定,那裡也要禁展示套件;
    展示套件自己的 ruff.toml 覆蓋掉。"""
    probe = SRC / "rtb" / "probe.py"
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--stdin-filename", str(probe), "-"],
        input="import rtb.demo\n", capture_output=True, text=True, timeout=60, check=False,
        cwd=ROOT)
    assert result.returncode == 1 and "TID251" in result.stdout, result.stdout


@pytest.mark.parametrize(("source", "package", "expected"), [
    ("from . import demo\n", "rtb", "rtb.demo"),
    ("from .demo import keys\n", "rtb", "rtb.demo.keys"),
    ("from ..demo.faults import dsp\n", "rtb.analyzer", "rtb.demo.faults.dsp"),
    ("from .. import demo\n", "rtb.analyzer", "rtb.demo"),
])
def test_relative_imports_are_resolved_before_the_check(source, package, expected):
    assert expected in _imports(ast.parse(source), package)


def test_importing_every_production_entry_never_loads_the_demo_package():
    """實測:子行程匯入每一支正式入口與共用模組之後,sys.modules 裡沒有任何展示套件的模組。"""
    entries = ["rtb.analyzer.runner", "rtb.executor.runner", "rtb.executor.inbox_server",
               "rtb.executor.approve", "rtb.executor.replay", "rtb.dsp.server", "rtb.ops.trace",
               "rtb.ops.metrics", "rtb.ops.slo", "rtb.eval.record", "rtb.sqlitekit",
               "rtb.httpkit", "rtb.httpclient", "rtb.capabilitykit"]
    code = ("import importlib, sys\n"
            f"for name in {entries!r}:\n"
            "    importlib.import_module(name)\n"
            "print(sorted(m for m in sys.modules\n"
            "             if m == 'rtb.demo' or m.startswith('rtb.demo.')))\n")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=60, check=False, env={"PYTHONPATH": str(SRC),
                                                          "PATH": os.environ["PATH"]})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
