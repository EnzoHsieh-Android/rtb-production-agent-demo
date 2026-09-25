"""整個 rtb 只有模型用戶端能啟動子行程(Phase 11B 增量 1,[S917];計劃
[[Projects/RTB_Phase11B大模型接入_計劃]] 第 7 版〈既有邊界怎麼改〉)。

直接解析 src/rtb 每一支檔的語法樹(不看 noqa):匯入 subprocess、multiprocessing、pty、webbrowser、
concurrent.futures 的行程池(含別名與 __import__、importlib 動態載入),或呼叫 os.system、os.popen、
os.fork、os.forkpty、os.exec*、os.spawn*、os.posix_spawn*、asyncio.create_subprocess_*,只准出現在
模型用戶端。
分析端只有模型閘道准匯入模型用戶端的任何一支模組(Phase 13 改寫 [S917],計劃
[[Projects/RTB_Phase13AI參與決策_計劃]]〈要改寫的既有合約〉),分析端目錄不准直接匯入網路或子行程模組;
准匯入模型閘道的分析端模組寫死成清單,流程推進、決策規則與 DSP
用戶端的匯入閉包不含模型用戶端([S1100])。
維運、評估、DSP、執行端的靜態檢查規則另加 subprocess 禁令。
"""

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.analyzer.test_boundaries import (
    NETWORK_MODULES,
    _imported_modules,
    _resolve_from,
    _source_of,
)

SRC = Path(__file__).resolve().parents[1] / "src"
RTB = SRC / "rtb"
ALLOWED = frozenset({"rtb.modelclaude"})  # 模型用戶端的 Claude Code 後端那一支
# 一鍵展示(Phase 12)的啟動器與驅動程式也起子行程,但起的是本專案自己的模組(DSP、收件口、執行迴圈、
# 分析端)與專案內的驗證器,不是 claude;[S917] 的原意由下面那支測試守:它們的原始碼不准提到 claude,
# 也不准匯入模型用戶端
PROJECT_STARTERS = frozenset({"rtb.demo.launcher", "rtb.demo.driver"})
MODEL_CLIENTS = frozenset({"rtb.modelclaude", "rtb.modelclient"})
GATE = "rtb.analyzer.modelgate"  # 分析端唯一准匯入模型用戶端的模組(Phase 13)
# 准匯入模型閘道的分析端模組(寫死,[S1100]):模型說明命令列、分析端驅動命令列、AI 決策函式所在模組
# (Phase 13 增量 2 開檔)
GATE_USERS = frozenset({"rtb.analyzer.narrate", "rtb.analyzer.runner", "rtb.analyzer.ai_judge"})
# 匯入閉包不准含模型用戶端任何一支模組的分析端模組([S1100]);調查詞彙模組與證據來源包裝也不准
# (展示流程圖與觀察器讀調查詞彙,展示伺服器行程不能因此載入模型用戶端)
MODEL_FREE = ("rtb.analyzer.flow", "rtb.analyzer.policy", "rtb.analyzer.dsp_client",
              "rtb.analyzer.investigation", "rtb.analyzer.instrumented", "rtb.demo.flow",
              "rtb.demo.observe", "rtb.demo.launcher")
SPAWN_MODULES = frozenset({"subprocess", "multiprocessing", "pty", "webbrowser",
                           "_posixsubprocess"})
OS_SPAWNERS = ("system", "popen", "exec", "spawn", "posix_spawn", "fork", "forkpty")
ASYNC_SPAWNERS = ("create_subprocess_exec", "create_subprocess_shell")
LOOP_SPAWNERS = ("subprocess_exec", "subprocess_shell")  # 事件迴圈物件的方法(名字不管接在誰後面)
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


def _aliases(tree, module):
    """一支檔裡 `import os as o` 這類別名(含原名)。"""
    names = {module}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.asname for a in node.names if a.name == module and a.asname}
    return names


def _dynamic_and_loop_offenders(tree, label):
    """動態載入子行程模組、事件迴圈的 subprocess_exec / subprocess_shell 方法。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in LOOP_SPAWNERS:
            found.append(f"{label}:{node.lineno} 事件迴圈 {node.attr}")
        loaded = _dynamic(node)
        if loaded is not None and (loaded.split(".")[0] in SPAWN_MODULES | {"os", "asyncio",
                                                                            "concurrent"}
                                   or loaded == "(不是字面字串)"):
            found.append(f"{label}: 動態載入 {loaded}")
    return found


def spawn_offenders(tree, label):
    """一支檔裡啟動子行程的寫法:匯入子行程模組、從 os 或 asyncio 取啟動函式、concurrent.futures 的
    行程池、webbrowser(含別名與屬性;os 與 asyncio 的別名也解開),事件迴圈的 subprocess_exec /
    subprocess_shell 方法,以及用 __import__ 或 importlib 動態載入這些名字。"""
    found = _dynamic_and_loop_offenders(tree, label)
    os_names, asyncio_names = _aliases(tree, "os"), _aliases(tree, "asyncio")
    for node in ast.walk(tree):
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
                (node.value.id in os_names and node.attr.startswith(OS_SPAWNERS))
                or (node.value.id in asyncio_names and node.attr in ASYNC_SPAWNERS)):
            found.append(f"{label}:{node.lineno} {node.value.id}.{node.attr}")
    return found


def test_only_the_model_client_starts_claude():
    offenders, starters = [], set()
    for path in sorted(RTB.rglob("*.py")):
        module = _module_name(path)
        found = spawn_offenders(ast.parse(path.read_text(encoding="utf-8")), module)
        if found:
            starters.add(module)
        if module not in ALLOWED | PROJECT_STARTERS:
            offenders += found
    assert offenders == []
    assert starters == ALLOWED | PROJECT_STARTERS  # 都真的在用,掃描不是空轉
    assert analyzer_model_offenders() == []
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


def model_client_modules():
    """模型用戶端的每一支模組:src/rtb/ 底下檔名以 model 開頭的(實作當下列舉,不寫死)。"""
    return frozenset(f"rtb.{path.stem}" for path in RTB.glob("model*.py"))


def analyzer_model_offenders(analyzer=RTB / "analyzer"):
    """分析端目錄:只有模型閘道准匯入模型用戶端的任何一支模組;任何檔都不准直接匯入網路或子行程模組。"""
    clients, offenders = model_client_modules(), []
    for path in sorted(analyzer.glob("*.py")):
        module = _module_name(path) if path.is_relative_to(SRC) else f"rtb.analyzer.{path.stem}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _imported_modules(SRC, module, tree)
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        touched = {m for m in imported if any(m == c or m.startswith(c + ".") for c in clients)}
        touched |= {f"rtb.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    and n.module == "rtb" for a in n.names if f"rtb.{a.name}" in clients}
        if touched and module != GATE:
            offenders.append(f"{module}: 匯入模型用戶端 {sorted(touched)}")
        offenders += [f"{module}: {m}" for m in imported
                      if m.split(".")[0] in NETWORK_MODULES | SPAWN_MODULES]
    return offenders


def closure_of(roots, src=SRC):
    pending, closure = list(roots), set()
    while pending:
        module = pending.pop()
        if module in closure:
            continue
        closure.add(module)
        path = _source_of(src, module)
        if path is None:
            continue
        parts = module.split(".")
        pending += [".".join(parts[:i]) for i in range(1, len(parts))]
        pending += sorted(_imported_modules(src, module, ast.parse(
            path.read_text(encoding="utf-8"))) - closure)
    return closure


def gate_offenders(analyzer=RTB / "analyzer"):
    """匯入模型閘道的分析端模組只准是寫死的那幾支。"""
    offenders = []
    for path in sorted(analyzer.glob("*.py")):
        module = f"rtb.analyzer.{path.stem}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _imported_modules(SRC, module, tree)
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        if GATE in imported and module not in GATE_USERS:
            offenders.append(f"{module}: 匯入模型閘道")
    return offenders


def closure_offenders(src=SRC):
    """分析端每一支不在准許名單的模組(閘道本身除外),匯入閉包都不准含閘道或模型用戶端的任何一支模組
    (代碼審 r1:原本只查三支,別的模組經說明命令列轉手就拿得到閘道)。"""
    clients = frozenset(f"rtb.{path.stem}" for path in (src / "rtb").glob("model*.py"))
    offenders = []
    for path in sorted((src / "rtb" / "analyzer").glob("*.py")):
        module = _module_name(path, src)
        if module in GATE_USERS or module == GATE:
            continue
        reached = closure_of([module], src) & (clients | {GATE})
        if reached:
            offenders.append(f"{module}: 閉包含 {sorted(reached)}")
    return offenders


def gate_module_objects(tree):
    """閘道綁定的模型用戶端模組物件(import rtb.modelX、from rtb import modelX):
    閘道只准轉手型別與函式,
    不准把模組物件公開(代碼審 r1:modelgate.mc.ledger_db 就能繞過閘道直接寫帳)。"""
    found = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
             if a.name.startswith("rtb.model")]
    found += [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "rtb"
              for a in n.names if a.name.startswith("model")]
    return found


def test_the_analyzer_reaches_the_model_only_through_the_gateway(tmp_path):
    """[S1100] 分析端只經模型閘道碰模型用戶端;准匯入閘道的分析端模組寫死;其他每一支分析端模組的
    匯入閉包都不含閘道或模型用戶端的任何一支模組;閘道不公開模型用戶端的模組物件。"""
    clients = model_client_modules()
    assert {"rtb.modelclient", "rtb.modelclaude", "rtb.modelcore", "rtb.modelledger",
            "rtb.modelledger_view", "rtb.modelrecording", "rtb.modelverify",
            "rtb.modelledger_writeoff"} <= clients
    assert analyzer_model_offenders() == []
    assert gate_offenders() == []
    assert (RTB / "analyzer" / "modelgate.py").is_file()
    assert closure_offenders() == []
    for root in MODEL_FREE:
        reached = closure_of([root])
        assert not reached & (clients | {GATE}), (root, sorted(reached & (clients | {GATE})))
    assert "rtb.modelclient" in closure_of([GATE])
    gate_tree = ast.parse((RTB / "analyzer" / "modelgate.py").read_text(encoding="utf-8"))
    assert gate_module_objects(gate_tree) == []
    assert gate_module_objects(ast.parse("from rtb import modelclient as mc\n"))
    # 殺傷力:分析端副本裡任何一支檔直接碰模型用戶端的任何模組、或白名單外的檔匯入閘道,都抓得到
    for probe in ("from rtb import modelclient\n", "import rtb.modelcore\n",
                  "from rtb.modelledger_view import Caller\n", "from rtb import modelrecording\n",
                  "from rtb.modelclient import call_model\n"):
        copy = tmp_path / f"a{abs(hash(probe))}"
        copy.mkdir()
        (copy / "policy.py").write_text(probe, encoding="utf-8")
        assert analyzer_model_offenders(copy), probe
    copy = tmp_path / "gate_user"
    copy.mkdir()
    (copy / "flow.py").write_text("from rtb.analyzer import modelgate\n", encoding="utf-8")
    assert gate_offenders(copy)
    # 經說明命令列轉手:別支分析端模組匯入它,閉包就含閘道
    shadow = tmp_path / "src"
    shutil.copytree(SRC, shadow)
    with (shadow / "rtb" / "analyzer" / "instrumented.py").open("a", encoding="utf-8") as file:
        file.write("\nfrom rtb.analyzer.narrate import modelgate as _mg  # noqa: E402,F401\n")
    assert closure_offenders(shadow)


def caller_offenders(tree, module):
    """用了不屬於自己的呼叫者標籤:Caller.成員(不論接在誰後面)、Caller("值")、Caller["成員"]。"""
    values = {"eval_candidate": "EVAL_CANDIDATE", "ops_hypothesis": "HYPOTHESIS",
              "analyzer_narrative": "NARRATIVE", "live_verification": "VERIFICATION",
              "analyzer_investigation": "INVESTIGATION"}
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in CALLER_USERS and (
                isinstance(node.value, ast.Name | ast.Attribute)
                and (getattr(node.value, "id", None) or getattr(node.value, "attr", None))
                == "Caller"):
            used.add(node.attr)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", getattr(
                node.func, "attr", None)) == "Caller" and node.args and isinstance(
                node.args[0], ast.Constant) and node.args[0].value in values:
            used.add(values[node.args[0].value])
        elif isinstance(node, ast.Subscript) and getattr(node.value, "id", getattr(
                node.value, "attr", None)) == "Caller" and isinstance(
                node.slice, ast.Constant) and node.slice.value in CALLER_USERS:
            used.add(node.slice.value)
    return sorted(f"{module}: 用了 {name}" for name in used if module not in CALLER_USERS[name])


CALLER_TAKERS = frozenset({"open_gate", "ModelRequest", "Gate"})  # 收呼叫者標籤的建構與開閘道


def _literal_member(node):
    """`<…>.Caller.成員` 或 `Caller.成員` 的字面寫法;回成員名,不是就回 None。"""
    if not (isinstance(node, ast.Attribute) and node.attr in CALLER_USERS):
        return None
    base = node.value
    name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(
        base, ast.Attribute) else None
    return node.attr if name == "Caller" else None


PICKERS = frozenset({"list", "tuple", "sorted", "iter", "next", "reversed", "enumerate"})


def _mentions_caller(node):
    return any((isinstance(n, ast.Name) and n.id == "Caller")
               or (isinstance(n, ast.Attribute) and n.attr == "Caller") for n in ast.walk(node))


def _relabel_offenders(call, name, module):
    """建好之後換標籤或挑成員的寫法(代碼審 r3):replace(…, caller=…)(送出點裡的 replace 連 ** 展開也
    不准)、__setattr__(…, "caller", …)、把 Caller 攤成清單或迭代器再挑(list(Caller)[1]、
    next(iter(Caller)))。"""
    found = []
    spread = module in CALL_MODEL_USERS and any(k.arg is None for k in call.keywords)
    if name == "replace" and (spread or any(k.arg == "caller" for k in call.keywords)):
        found.append(f"{module}:{call.lineno} 用 replace 換呼叫者")
    if name in ("__setattr__", "setattr") and any(
            isinstance(a, ast.Constant) and a.value == "caller" for a in call.args):
        found.append(f"{module}:{call.lineno} 用 {name} 換呼叫者")
    if name in PICKERS and any(_mentions_caller(a) for a in call.args):
        found.append(f"{module}:{call.lineno} 把 Caller 攤開來挑成員")
    return found


def _given_callers(node, name):
    given = [k.value for k in node.keywords if k.arg == "caller"]
    if not given and name == "ModelRequest" and node.args:
        given = [node.args[0]]
    if not given and name == "Gate" and len(node.args) > 1:
        given = [node.args[1]]
    return given


def _taker_offenders(node, name, module):
    """開閘道、建請求、建閘道帶的呼叫者要是字面的、准用的成員;用 ** 展開就看不出來,一律不准。"""
    if any(k.arg is None for k in node.keywords):
        return [f"{module}:{node.lineno} {name} 用 ** 展開,看不出呼叫者"]
    given = _given_callers(node, name)
    if not given and name == "open_gate":
        return [f"{module}:{node.lineno} open_gate 沒帶呼叫者"]
    found = []
    for value in given:
        member = _literal_member(value)
        if member is None:
            found.append(f"{module}:{node.lineno} {name} 的呼叫者不是字面的 Caller.成員")
        elif module not in CALLER_USERS[member]:
            found.append(f"{module}:{node.lineno} {name} 用了 {member}")
    return found


def caller_argument_offenders(tree, module):
    """開閘道、建請求、建閘道時帶的呼叫者:一定要是字面的 Caller.成員(不准變數、別名、getattr、展開),
    而且要是這支模組准用的成員(代碼審 r2:標籤在開閘道時綁死,送出時不收);建好之後也不准換標籤、
    不准把 Caller 攤開來挑成員(代碼審 r3)。"""
    found = [f"{module}:{node.lineno} 用 __members__ 挑呼叫者" for node in ast.walk(tree)
             if isinstance(node, ast.Attribute) and node.attr == "__members__"
             and _mentions_caller(node.value)]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(
            func, ast.Attribute) else None
        found += _relabel_offenders(node, name, module)
        if name in CALLER_TAKERS:
            found += _taker_offenders(node, name, module)
    return found


def test_each_caller_label_is_used_only_by_its_own_module():
    """代碼審 r1:花費上限依請求自報的呼叫者判,每個呼叫者標籤只准它自己的那支模組用。"""
    from rtb.modelledger_view import Caller

    assert set(CALLER_USERS) == {member.name for member in Caller}
    offenders = []
    for path in sorted(RTB.rglob("*.py")):
        module = _module_name(path)
        if module not in CALLER_EXEMPT:
            offenders += caller_offenders(ast.parse(path.read_text(encoding="utf-8")), module)
    assert offenders == []
    arguments = []
    for path in sorted(RTB.rglob("*.py")):
        module = _module_name(path)
        if module not in CALLER_EXEMPT and module != GATE:
            arguments += caller_argument_offenders(ast.parse(path.read_text(encoding="utf-8")),
                                                   module)
    assert arguments == []
    for probe in ("from rtb.modelclient import Caller as K\nr = ModelRequest(caller=K.NARRATIVE)\n",
                  "r = mc.ModelRequest(caller=getattr(mc.Caller, 'NARRATIVE'))\n",
                  "c = mc.Caller.EVAL_CANDIDATE\nr = mc.ModelRequest(caller=c)\n",
                  "g = modelgate.open_gate({}, caller=modelgate.Caller.HYPOTHESIS)\n",
                  "g = modelgate.open_gate({}, demo_id=None)\n",
                  "r = core.ModelRequest(core.Caller.VERIFICATION, 's', 'u', 1, 1.0)\n",
                  # 代碼審 r3:展開、建好之後換標籤、挑成員
                  "r = mc.ModelRequest(**{'caller': mc.Caller('analyzer_' + 'narrative')})\n",
                  "g = modelgate.Gate(**dict(caller=modelgate.Caller.INVESTIGATION))\n",
                  "import dataclasses\nr2 = dataclasses.replace(r, caller=mc.Caller.HYPOTHESIS)\n",
                  "from dataclasses import replace\nr2 = replace(r, **extra)\n",
                  "object.__setattr__(r, 'caller', mc.Caller.HYPOTHESIS)\n",
                  "setattr(r, 'caller', x)\n",
                  "c = list(mc.Caller)[1]\n", "c = next(iter(mc.Caller))\n",
                  "c = sorted(Caller)[0]\n", "c = mc.Caller.__members__['NARRATIVE']\n"):
        assert caller_argument_offenders(ast.parse(probe), "rtb.eval.model_candidate"), probe
    # 標籤清單照舊可以整份拿來當指標的有界標籤(不是挑成員)
    assert caller_argument_offenders(ast.parse(
        "labels = frozenset(c.value for c in Caller)\n"), "rtb.ops.metrics") == []
    assert caller_argument_offenders(ast.parse(
        "r = mc.ModelRequest(caller=mc.Caller.EVAL_CANDIDATE)\n"), "rtb.eval.model_candidate") == []
    for probe in ("from rtb import modelclient as mc\nx = mc.Caller.NARRATIVE\n",
                  "from rtb.modelclient import Caller\nx = Caller('ops_hypothesis')\n",
                  "from rtb.modelclient import Caller\nx = Caller['INVESTIGATION']\n"):
        assert caller_offenders(ast.parse(probe), "rtb.eval.model_candidate"), probe


@pytest.mark.parametrize("layer", ["ops", "eval", "dsp", "executor"])
def test_layer_rules_ban_starting_subprocesses(layer):
    """[S917] 的靜態檢查規則那半:四個目錄的 ruff 設定擋得住直接匯入 subprocess。"""
    config = RTB / layer / "ruff.toml"
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(config),
         "--stdin-filename", str(config.parent / "probe.py"), "-"],
        input="import subprocess\n", capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 1 and "TID251" in result.stdout, (layer, result.stdout)


# ---- 代碼審第 1 輪補強 ----
BACKEND_NAMES = frozenset({"run_claude", "ClaudeCodeBackend", "check_login", "claude_version",
                           "judge_output"})
BACKEND_USERS = frozenset({"rtb.modelclaude", "rtb.modelclient", "rtb.modelverify"})


# 送出呼叫的地方:模型用戶端本身、評估的模型候選、分析端模型閘道(Phase 13 改寫)、經閘道送出的模型說明
# 命令列與維運的假說命令列(Phase 11B [S912])。送出的名字除了 call_model,還有閘道的 open_gate 與
# complete(代碼審 r1:閘道是第二個送出入口)
# Phase 13 增量 2 加:AI 決策模組(開閘道、送出)與開了 AI 決策的分析端驅動命令列(把開閘道函式交給它)
# Phase 13 增量 3 加:調查評估執行器(呼叫同一支 AI 決策函式,[S1146];[S918] 照計劃改寫)
CALL_MODEL_USERS = frozenset({"rtb.modelclient", "rtb.eval.model_candidate",
                              "rtb.analyzer.modelgate", "rtb.analyzer.narrate",
                              "rtb.ops.hypothesis", "rtb.analyzer.ai_judge",
                              "rtb.analyzer.runner", "rtb.eval.investigation_eval"})
SEND_CALLS = frozenset({"call_model", "open_gate", "complete"})
# 會送出模型呼叫的命令列模組:匯入它就能經它的 run 轉手送出,匯入本身就算送出點(代碼審 r2)
# Phase 13 增量 3 代碼審 r1:調查評估執行器的 run 即時模式會送出,匯入它也算送出點
SENDING_ENTRIES = frozenset({"rtb.analyzer.narrate", "rtb.ops.hypothesis",
                             "rtb.analyzer.ai_judge", "rtb.analyzer.runner",
                             "rtb.eval.investigation_eval"})
# 每個呼叫者標籤只准哪幾支模組用(代碼審 r1:花費上限看請求自報的呼叫者,標籤要綁住模組才守得住
# 「誰都不能自稱不計入」)。定義它的唯讀開法與只拿來列上限清單的花費帳寫入不算使用;Phase 13 增量 2 的
# AI 決策模組開檔時把它加進「分析端調查」那一格
CALLER_USERS: dict[str, frozenset[str]] = {
    "EVAL_CANDIDATE": frozenset({"rtb.eval.model_candidate"}),
    "HYPOTHESIS": frozenset({"rtb.ops.hypothesis"}),
    "NARRATIVE": frozenset({"rtb.analyzer.narrate"}),
    "VERIFICATION": frozenset({"rtb.modelverify"}),
    "INVESTIGATION": frozenset({"rtb.analyzer.ai_judge"}),  # Phase 13 增量 2:AI 決策模組開閘道
}
CALLER_EXEMPT = frozenset({"rtb.modelledger_view", "rtb.modelledger"})


def backend_offenders(tree, label, module=None):
    """匯入或取用 Claude Code 後端(模組本身或它的啟動函式、後端類別)的地方;另抓送出呼叫的函式
    (call_model,只准模型用戶端本身與接入點)與經別的模組轉手的模型用戶端屬性鏈(x.modelclient)。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"{label}: 匯入 {a.name}" for a in node.names
                      if a.name in ("rtb.modelclaude", "rtb.modelverify")]
            found += [f"{label}: 匯入送出命令列 {a.name}" for a in node.names
                      if a.name in SENDING_ENTRIES and module not in CALL_MODEL_USERS]
        elif isinstance(node, ast.ImportFrom):
            # 相對匯入先接成完整名稱(代碼審 r3:最上層模組寫 from .analyzer import narrate 就繞過)
            module_name = (_resolve_from(SRC, module, node) if node.level and module
                           else node.module or "")
            names = {a.name for a in node.names}
            if module_name in ("rtb.modelclaude", "rtb.modelverify") or (
                    module_name == "rtb" and names & {"modelclaude", "modelverify"}):
                found.append(f"{label}: 從 {module_name} 匯入 {sorted(names)}")
            found += [f"{label}: 匯入 {n}" for n in names & BACKEND_NAMES]
            if names & SEND_CALLS and module not in CALL_MODEL_USERS:
                found.append(f"{label}: 匯入 {sorted(names & SEND_CALLS)}")
            entries = {f"{module_name}.{n}" for n in names} | {module_name}
            if entries & SENDING_ENTRIES and module not in CALL_MODEL_USERS:
                found.append(f"{label}: 匯入送出命令列 {sorted(entries & SENDING_ENTRIES)}")
        elif isinstance(node, ast.Attribute) and (node.attr in BACKEND_NAMES or (
                node.attr in SEND_CALLS and module not in CALL_MODEL_USERS)):
            found.append(f"{label}:{node.lineno} 取用 {node.attr}")
        elif isinstance(node, ast.Attribute) and node.attr == "modelclient" and not (
                isinstance(node.value, ast.Name) and node.value.id == "rtb"):
            found.append(f"{label}:{node.lineno} 經別的模組轉手模型用戶端")
        elif isinstance(node, ast.Name) and node.id in BACKEND_NAMES:
            found.append(f"{label}:{node.lineno} 取用 {node.id}")
    return found


def test_only_the_model_client_modules_touch_the_claude_backend():
    offenders = []
    for path in sorted(RTB.rglob("*.py")):
        module = _module_name(path)
        if module not in BACKEND_USERS:
            offenders += backend_offenders(ast.parse(path.read_text(encoding="utf-8")), module,
                                           module)
    assert offenders == []
    for probe in ("from rtb.modelclaude import run_claude",
                  "from rtb import modelclaude", "import rtb.modelverify",
                  "from rtb.modelclient import x\nx.run_claude([], '', {}, 1)",
                  "from rtb import modelclient as m\nm.ClaudeCodeBackend",
                  # 代碼審第 2 輪:經核銷命令列轉手的模型用戶端、直接取送出函式
                  "from rtb import modelledger_writeoff as w\nw.modelclient.call_model(1)",
                  "from rtb import modelledger_writeoff as w\nclient = w.modelclient",
                  "from rtb.modelclient import call_model",
                  "import rtb.modelclient\nrtb.modelclient.call_model(1)",
                  # 代碼審 r1:經分析端模型閘道送出
                  "from rtb.analyzer import modelgate as g\ng.open_gate({}, demo_id=None, "
                  "ledger=None, recordings=None)",
                  "def f(gate):\n    return gate.complete(1, 's', 'u')",
                  "from rtb.analyzer.modelgate import open_gate",
                  # 代碼審 r2:經說明或假說命令列的 run 轉手送出
                  "from rtb.analyzer import narrate\nnarrate.run(['--db', 'x'])",
                  "import rtb.ops.hypothesis",
                  "from rtb.ops.hypothesis import run",
                  "from rtb.ops import hypothesis as h\nh.run([])"):
        assert backend_offenders(ast.parse(probe), "probe"), probe
    # 代碼審 r3:最上層模組用同層相對匯入轉手
    for probe in ("from .analyzer import narrate\nnarrate.run([])",
                  "from .ops import hypothesis", "from .analyzer.modelgate import open_gate"):
        assert backend_offenders(ast.parse(probe), "rtb.httpkit", "rtb.httpkit"), probe
    # 最上層共用模組吃的是 pyproject 那張禁令表:也禁閘道與兩支送出命令列
    for banned in ("from rtb.analyzer import modelgate", "from rtb.analyzer import narrate",
                   "import rtb.ops.hypothesis"):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(SRC.parent / "pyproject.toml"),
             "--stdin-filename", str(RTB / "probe.py"), "-"],
            input=f"{banned}\n", capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, banned
    # 代碼審 r1:分析端與維運以外的各層(含展示與它的啟動器)也禁匯入模型閘道
    for config in (RTB / "dsp" / "ruff.toml", RTB / "eval" / "ruff.toml",
                   RTB / "demo" / "ruff.toml", RTB / "demo" / "launcher" / "ruff.toml",
                   RTB / "executor" / "ruff.toml", RTB / "domain" / "ruff.toml"):
        for banned in ("from rtb.analyzer import modelgate", "import rtb.analyzer.modelgate",
                       "from rtb.analyzer import narrate", "import rtb.ops.hypothesis",
                       "from rtb.ops import hypothesis"):
            result = subprocess.run(
                [sys.executable, "-m", "ruff", "check", "--config", str(config),
                 "--stdin-filename", str(config.parent / "probe.py"), "-"],
                input=f"{banned}\n", capture_output=True, text=True, timeout=60, check=False)
            assert result.returncode == 1 and "TID251" in result.stdout, (config, banned)
    # 四個目錄的靜態檢查規則也擋模型用戶端各模組與實測命令列(維運的假說命令列在增量 2 另開例外)
    for layer in ("dsp", "executor", "domain", "ops"):
        config = RTB / layer / "ruff.toml"
        for banned in ("rtb.modelclient", "rtb.modelclaude", "rtb.modelverify",
                       # 代碼審第 2 輪:拆檔後的帳本寫入、錄製讀寫、共用詞彙與核銷命令列
                       "rtb.modelcore", "rtb.modelledger", "rtb.modelrecording",
                       "rtb.modelledger_writeoff"):
            result = subprocess.run(
                [sys.executable, "-m", "ruff", "check", "--config", str(config),
                 "--stdin-filename", str(config.parent / "probe.py"), "-"],
                input=f"import {banned}\n", capture_output=True, text=True, timeout=60,
                check=False)
            assert result.returncode == 1 and "TID251" in result.stdout, (layer, banned)


def test_spawn_aliases_and_loop_helpers_are_caught():
    for probe in ("import os as o\no.system('x')", "import os as o\no.execvp('x', [])",
                  "from os import system as s\ns('x')", "import _posixsubprocess",
                  "import asyncio\nloop = asyncio.get_event_loop()\nloop.subprocess_exec(1)",
                  "def f(loop):\n    return loop.subprocess_shell('x')"):
        assert spawn_offenders(ast.parse(probe), "probe"), probe


def claude_mentions(module, source):
    """[S917] 本專案模組的啟動者不准碰 claude:原始碼提到 claude(不分大小寫)或匯入模型用戶端就算。"""
    tree = ast.parse(source)
    imported = _imported_modules(SRC, module, tree)
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    found = [f"{module}: 匯入 {m}" for m in imported
             if any(m == c or m.startswith(c + ".") for c in MODEL_CLIENTS)]
    if "claude" in source.lower():
        found.append(f"{module}: 原始碼提到 claude")
    return found


def test_the_demo_starters_never_start_claude():
    """展示的啟動器與驅動程式准起子行程(起的是本專案的模組與驗證器),但它們的原始碼不准提到 claude、
    不准匯入模型用戶端:[S917]「只有模型用戶端會啟動 claude」照樣成立。"""
    offenders = []
    for module in sorted(PROJECT_STARTERS):
        path = RTB.joinpath(*module.split(".")[1:])
        path = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
        offenders += claude_mentions(module, path.read_text(encoding="utf-8"))
    assert offenders == []
    for probe in ("import subprocess\nsubprocess.run(['claude'])",
                  "import subprocess\nsubprocess.run(['CLAUDE', '-p'])",
                  "from rtb import modelclient",
                  "import rtb.modelclaude",
                  "from rtb.modelclient import run"):
        assert claude_mentions("rtb.demo.driver", probe), probe


def test_importing_the_eval_runner_counts_as_a_send_point():
    """Phase 13 增量 3 代碼審 r1:調查評估執行器即時模式會送出,匯入它就算送出點;既有的評估模組
    匯入它、經它的 run 轉手送出要被擋。"""
    assert "rtb.eval.investigation_eval" in SENDING_ENTRIES
    for probe in ("from rtb.eval import investigation_eval\n",
                  "import rtb.eval.investigation_eval\n",
                  "from rtb.eval.investigation_eval import run\n"):
        found = backend_offenders(ast.parse(probe), "scoring.py", "rtb.eval.scoring")
        assert found, probe
