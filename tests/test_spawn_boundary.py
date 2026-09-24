"""整個 rtb 只有模型用戶端能啟動子行程(Phase 11B 增量 1,[S917];計劃
[[Projects/RTB_Phase11B大模型接入_計劃]] 第 7 版〈既有邊界怎麼改〉)。

直接解析 src/rtb 每一支檔的語法樹(不看 noqa):匯入 subprocess、multiprocessing、pty、webbrowser、
concurrent.futures 的行程池(含別名與 __import__、importlib 動態載入),或呼叫 os.system、os.popen、
os.fork、os.forkpty、os.exec*、os.spawn*、os.posix_spawn*、asyncio.create_subprocess_*,只准出現在
模型用戶端。
分析端只有模型說明命令列(增量 2)准匯入模型用戶端,分析端目錄不准直接匯入網路或子行程模組。
維運、評估、DSP、執行端的靜態檢查規則另加 subprocess 禁令。
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.analyzer.test_boundaries import NETWORK_MODULES, _imported_modules

SRC = Path(__file__).resolve().parents[1] / "src"
RTB = SRC / "rtb"
ALLOWED = frozenset({"rtb.modelclaude"})  # 模型用戶端的 Claude Code 後端那一支
SPAWN_MODULES = frozenset({"subprocess", "multiprocessing", "pty", "webbrowser"})
OS_SPAWNERS = ("system", "popen", "exec", "spawn", "posix_spawn", "fork", "forkpty")
ASYNC_SPAWNERS = ("create_subprocess_exec", "create_subprocess_shell")
POOL_NAMES = ("ProcessPoolExecutor",)  # concurrent.futures 的行程池


def _module_name(path, src=SRC):
    return ".".join(path.relative_to(src).with_suffix("").parts).removesuffix(".__init__")


def _dynamic(node):
    """__import__ 或 importlib 動態載入子行程模組的名字(字面字串)。"""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(
        func, ast.Attribute) else None
    if name not in ("__import__", "import_module") or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return "(不是字面字串)"


def spawn_offenders(tree, label):
    """一支檔裡啟動子行程的寫法:匯入子行程模組、從 os 或 asyncio 取啟動函式、concurrent.futures 的
    行程池、webbrowser(含別名與屬性),以及用 __import__ 或 importlib 動態載入這些名字。"""
    found = []
    for node in ast.walk(tree):
        loaded = _dynamic(node)
        if loaded is not None and (loaded.split(".")[0] in SPAWN_MODULES | {"os", "asyncio",
                                                                            "concurrent"}
                                   or loaded == "(不是字面字串)"):
            found.append(f"{label}: 動態載入 {loaded}")
        if isinstance(node, ast.Import):
            found += [f"{label}: 匯入 {a.name}" for a in node.names
                      if a.name.split(".")[0] in SPAWN_MODULES]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in SPAWN_MODULES:
                found.append(f"{label}: 從 {node.module} 匯入")
            elif root in ("os", "asyncio"):
                found += [f"{label}: 從 {root} 匯入 {a.name}" for a in node.names
                          if a.name.startswith(OS_SPAWNERS) or a.name in ASYNC_SPAWNERS]
            elif root == "concurrent":
                found += [f"{label}: 行程池 {a.name}" for a in node.names if a.name in POOL_NAMES]
        elif isinstance(node, ast.Attribute) and node.attr in POOL_NAMES:
            found.append(f"{label}:{node.lineno} 行程池 {node.attr}")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (
                (node.value.id == "os" and node.attr.startswith(OS_SPAWNERS))
                or (node.value.id == "asyncio" and node.attr in ASYNC_SPAWNERS)):
            found.append(f"{label}:{node.lineno} {node.value.id}.{node.attr}")
    return found


def test_only_the_model_client_starts_claude():
    offenders, starters = [], set()
    for path in sorted(RTB.rglob("*.py")):
        module = _module_name(path)
        found = spawn_offenders(ast.parse(path.read_text(encoding="utf-8")), module)
        if found:
            starters.add(module)
        if module not in ALLOWED:
            offenders += found
    assert offenders == []
    assert starters == ALLOWED  # 模型用戶端真的在用,掃描不是空轉
    # 分析端:沒有檔案匯入模型用戶端(增量 2 起只准模型說明命令列),也不直接匯入網路或子行程模組
    analyzer_offenders = []
    for path in sorted((RTB / "analyzer").glob("*.py")):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _imported_modules(SRC, module, tree)
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        if "rtb.modelclient" in imported and module != "rtb.analyzer.narrate":
            analyzer_offenders.append(f"{module}: 匯入模型用戶端")
        analyzer_offenders += [f"{module}: {m}" for m in imported
                               if m.split(".")[0] in NETWORK_MODULES | SPAWN_MODULES]
    assert analyzer_offenders == []
    # 殺傷力:各種寫法都抓得到
    for probe in ("import subprocess", "from subprocess import run", "import multiprocessing",
                  "import pty", "import os\nos.system('x')", "import os\nos.execvp('x', [])",
                  "import os\nos.posix_spawn('x', [], {})", "from os import spawnv",
                  "import asyncio\nasyncio.create_subprocess_exec('x')",
                  "from asyncio import create_subprocess_shell", "import os\nos.fork()",
                  "import os\nos.forkpty()", "import webbrowser",
                  "from concurrent.futures import ProcessPoolExecutor",
                  "import concurrent.futures as cf\ncf.ProcessPoolExecutor()",
                  "import subprocess as sp", "x = __import__('subprocess')",
                  "import importlib\nimportlib.import_module('pty')",
                  "name = 'sub' + 'process'\nx = __import__(name)"):
        assert spawn_offenders(ast.parse(probe), "probe"), probe


@pytest.mark.parametrize("layer", ["ops", "eval", "dsp", "executor"])
def test_layer_rules_ban_starting_subprocesses(layer):
    """[S917] 的靜態檢查規則那半:四個目錄的 ruff 設定擋得住直接匯入 subprocess。"""
    config = RTB / layer / "ruff.toml"
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(config),
         "--stdin-filename", str(config.parent / "probe.py"), "-"],
        input="import subprocess\n", capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 1 and "TID251" in result.stdout, (layer, result.stdout)
