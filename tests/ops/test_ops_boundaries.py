"""維運套件的邊界(Phase 9 增量 1):[S617]。

維運套件可以匯入分析端與執行端的讀取介面,反過來分析端、執行端、DSP 都不准匯入它(各目錄的匯入禁令
加一支直接解析原始碼的掃描,不看 noqa)。匯入禁令只能整個模組一起禁,讀寫函式又在同一個模組,所以
另外掃維運套件的語法樹:凡是呼叫分析端、執行端模組裡定義的函式或類別,都要在讀取白名單裡;白名單
裡每一支都要是機械判定的非寫入函式。唯讀連線本身也寫不進去,是最後一道。
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.executor.write_scan import classify

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"
OPS = SRC / "ops"
# 維運套件准呼叫的分析端、執行端名稱:兩個唯讀開法,加上它們開出來的物件的讀取方法與嘗試紀錄的讀取函式
READ_WHITELIST = frozenset({
    "ReadOnlyInbox", "TaskReader",  # 唯讀開法
    "read_transaction", "close",
    "history", "evidence_for", "list_tool_calls", "follow_up_of", "follow_up_to",
    "handed_off_keys",  # 分析端
    "lifecycle_events", "dead_letters_for", "dead_letter_ops_for",  # 收件口
    "trace_rows", "dsp_calls_for",  # 嘗試紀錄
    "failure_class",  # 死信信封的欄位名;同名的是收件口的分類函式,也是機械判定的非寫入函式
})
SCANNED = (SRC / "executor" / "inbox_store.py", SRC / "executor" / "attempt_store.py",
           SRC / "analyzer" / "task_store.py")


def _defined_names(classes=True):
    """分析端、執行端每支模組定義的函式、方法(與類別)名稱。"""
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef) if classes else (
        ast.FunctionDef, ast.AsyncFunctionDef)
    names = set()
    for directory in (SRC / "analyzer", SRC / "executor"):
        for path in directory.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, kinds):
                    names.add(node.name)
    return names


def _called_names(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                yield node.lineno, func.attr, False
            elif isinstance(func, ast.Name):
                yield node.lineno, func.id, True


def _ops_offenders(ops_dir):
    """從嚴:屬性不論是不是當場呼叫(先取方法再呼叫也算)、直接呼叫的名字(維運套件自己定義的除外)、
    直接引用的函式名,只要是分析端、執行端定義的名字,都要在白名單裡。"""
    defined, functions = _defined_names(), _defined_names(classes=False)
    trees = {path: ast.parse(path.read_text(encoding="utf-8"))
             for path in sorted(ops_dir.rglob("*.py"))}
    local = {node.name for tree in trees.values() for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    offenders = []
    for path, tree in trees.items():
        offenders += [f"{path.name}:{line} {name}" for line, name, bare in _called_names(tree)
                      if name in defined and name not in READ_WHITELIST
                      and not (bare and name in local)]
        offenders += _dynamic_lookups(path.name, tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in defined - READ_WHITELIST:
                offenders.append(f"{path.name}:{node.lineno} .{node.attr}")
            elif (isinstance(node, ast.Name) and node.id in functions - READ_WHITELIST
                  and node.id not in local):
                offenders.append(f"{path.name}:{node.lineno} {node.id}")
            elif isinstance(node, ast.ImportFrom):  # 寫入開法、白名單外的函式連匯入都不准
                offenders += [f"{path.name}: 匯入 {a.name}" for a in node.names
                              if a.name in ("InboxStore", "TaskStore", "connect")
                              or (a.name in functions - READ_WHITELIST
                                  and (node.module or "").startswith(("rtb.analyzer",
                                                                      "rtb.executor")))]
    return offenders


# 用字串動態取屬性的寫法(代碼審第 1 輪):名字掃描看不到字串裡的方法名,維運套件本身也用不到,一律不准
DYNAMIC_LOOKUPS = frozenset({"getattr", "attrgetter", "methodcaller", "__getattribute__", "vars",
                             "__dict__"})


def _dynamic_lookups(label, tree):
    found = []
    for node in ast.walk(tree):
        name = (node.id if isinstance(node, ast.Name) else node.attr
                if isinstance(node, ast.Attribute) else node.name
                if isinstance(node, ast.alias) else None)
        if name in DYNAMIC_LOOKUPS:
            found.append(f"{label}:{getattr(node, 'lineno', 0)} 動態取屬性 {name}")
    return found


def _importers_of_ops():
    found = []
    for layer in ("analyzer", "executor", "dsp", "domain"):
        for path in sorted((SRC / layer).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
                found += [f"{layer}/{path.name}: {m}" for m in modules
                          if m == "rtb.ops" or m.startswith("rtb.ops.")]
    return found


# ---- [S617] ----
def test_the_ops_package_is_read_only_and_imported_by_nobody():
    assert list(OPS.glob("*.py")), "維運套件不見了"
    assert _importers_of_ops() == []
    for layer in ("analyzer", "executor", "dsp"):  # 各目錄的匯入禁令真的擋得住
        config = SRC / layer / "ruff.toml"
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(config.parent / "probe.py"), "-"],
            input="import rtb.ops.trace\n", capture_output=True, text=True, timeout=60,
            check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (layer, result.stdout)
    config = OPS / "ruff.toml"  # 維運套件自己不准開資料庫連線、不准碰 DSP 內部
    for banned in ("import sqlite3", "from rtb.sqlitekit import connect", "import rtb.dsp.store"):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(OPS / "probe.py"), "-"],
            input=f"{banned}\n", capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (banned, result.stdout)

    assert _ops_offenders(OPS) == []
    # 白名單裡每一支都是機械判定的非寫入函式(同名的函式只要有一支會寫就不行)
    classified = {}
    for path in SCANNED:
        for qualname, writes in classify(path).items():
            classified.setdefault(qualname.split(".")[-1], set()).add(writes)
    for name in READ_WHITELIST - {"ReadOnlyInbox", "TaskReader"}:
        assert classified.get(name) == {False}, name


@pytest.mark.parametrize("extra", [
    "\ndef _w(s, tx):\n    s.release(tx, None, None, None)\n",
    "\ndef _w(p):\n    return InboxStore(p)\n",
    "\ndef _w(tx):\n    attempt_store.begin(tx, None, None, capability_expires_at=None)\n",
    "\nfrom rtb.executor.inbox_store import InboxStore as _S\n",
    "\ndef _w(s):\n    later = s.release\n    return later\n",  # 先取方法、之後再呼叫
    "\ndef _w():\n    from rtb.executor.attempt_store import begin as _b\n    return _b\n",
    # 用字串動態取屬性(代碼審第 1 輪):維運套件本身用不到,一律不准
    "\ndef _w(s, tx):\n    getattr(s, 'release')(tx, None, None, None)\n",
    "\ndef _w(s):\n    return getattr(s, 'rel' + 'ease')\n",
    "\nimport operator\n\ndef _w(s):\n    return operator.attrgetter('release')(s)\n",
    "\nfrom operator import methodcaller\n\n_w = methodcaller('release', None, None)\n",
    "\ndef _w(s):\n    return s.__getattribute__('release')\n",
    "\ndef _w(s):\n    return vars(type(s))['release']\n",
])
def test_the_ops_scan_catches_a_write_call(tmp_path, extra):
    """[S617] 的殺傷力:在維運套件的副本放一個寫入呼叫,掃描就要找到。"""
    copy = tmp_path / "ops"
    copy.mkdir()
    for path in OPS.glob("*.py"):
        (copy / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with (copy / "trace.py").open("a", encoding="utf-8") as file:
        file.write(extra)
    assert _ops_offenders(copy) != []
