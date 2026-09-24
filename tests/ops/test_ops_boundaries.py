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
# 維運套件准呼叫的名字 → 准呼叫它的檔案(Phase 11B 增量 1 起逐檔限制,計劃
# [[Projects/RTB_Phase11B大模型接入_計劃]]〈既有邊界怎麼改〉:共用一份名字清單表達不了
# 「模型用戶端的送出呼叫只准假說命令列」這種逐檔規則)。名字是分析端、執行端的兩個唯讀開法與
# 它們開出來的物件的讀取方法、嘗試紀錄的讀取函式,以及花費帳唯讀開法與它的讀取方法;
# 檔案是目前真的在用的那幾支(改用前要先加在這裡)
_ALL_READERS = frozenset({"metrics.py", "side_effects.py", "sli.py", "trace.py"})
_METRICS, _TRACE = frozenset({"metrics.py"}), frozenset({"trace.py"})
_SIDE_EFFECTS, _SLI = frozenset({"side_effects.py"}), frozenset({"sli.py"})
_WALKERS = frozenset({"metrics.py", "sli.py", "trace.py"})  # 讀分析端歷程的三支
READ_WHITELIST: dict[str, frozenset[str]] = {
    # 唯讀開法與交易、關閉
    "ReadOnlyInbox": _ALL_READERS, "TaskReader": _WALKERS,
    "read_transaction": _ALL_READERS, "close": _ALL_READERS,
    # 分析端
    "history": _WALKERS, "evidence_for": _TRACE, "list_tool_calls": _TRACE,
    "follow_up_of": _WALKERS, "follow_up_to": _WALKERS, "handed_off_keys": _TRACE,
    "tool_calls_between": _METRICS,
    "narrative_for": _TRACE,  # Phase 11B 增量 2:追蹤檢視提案那一步的模型說明欄
    # 收件口
    "lifecycle_events": _WALKERS, "dead_letters_for": _TRACE, "dead_letter_ops_for": _TRACE,
    "lifecycle_events_between": frozenset({"metrics.py", "sli.py"}),
    "last_terminal_event": _METRICS, "pending_snapshot": _METRICS,
    "failure_class": _TRACE,  # 死信信封的欄位名;同名的是收件口的分類函式,也是機械判定的非寫入函式
    # 嘗試紀錄
    "trace_rows": frozenset({"metrics.py", "trace.py"}), "dsp_calls_for": _TRACE,
    "dsp_calls_between": _METRICS, "terminal_rows_between": _METRICS,
    "unresolved_keys": _METRICS, "campaigns_with_unresolved": _METRICS,
    "unknown_rows_between": _SLI, "first_rows_for": _SIDE_EFFECTS,
    "approval_uses_for": _SIDE_EFFECTS, "first_rows_for_proposal": _SIDE_EFFECTS,
    "utilization": _METRICS, "load_tenants": frozenset({"metrics.py", "hypothesis.py"}),
    # 模型花費帳的唯讀開法與它的讀取方法:只准指標讀(追蹤到增量 2 才加);模型用戶端的送出呼叫與
    # 花費帳寫入函式不在這裡(增量 2 只准假說命令列)
    "ModelLedgerView": _METRICS, "calls_between": _METRICS, "ledger_path": _METRICS,
}
# 會寫的名字 → 准呼叫它的檔案:只有假說命令列那一格(增量 2 才有這支檔);不套「白名單名字都判成不寫入」
# 那條檢查(計劃第 7 版〈既有邊界怎麼改〉[S912])
HYPOTHESIS = "hypothesis.py"
WRITE_WHITELIST: dict[str, frozenset[str]] = {
    "call_model": frozenset({HYPOTHESIS}), "write_off": frozenset({HYPOTHESIS}),
}
# 假說命令列(增量 2)要用的模型用戶端詞彙:模式判定、錄製目錄與帳檔路徑、請求型別與例外、佔位符。
# 跟寫入名字一樣只准出現在假說命令列那一格,不套「白名單名字都判成不寫入」那條檢查(它們不在掃描的
# 讀取模組裡);模型後端(Claude Code 後端、實測、核銷)照舊誰都不准碰
HYPOTHESIS_ONLY: dict[str, frozenset[str]] = {name: frozenset({HYPOTHESIS}) for name in (
    "settings_from_env", "default_recordings_dir", "live_ledger_path", "ModelRequest", "Settings",
    "Mode", "Caller", "LedgerBusy", "ModelCallFailed", "UnknownModel", "Placeholders",
    "substitute", "restore", "mode", "notices", "check_recordings_dir", "MixedRecordingsDir",
    "traceable_sentences", "record")}
SCANNED = (SRC / "executor" / "inbox_store.py", SRC / "executor" / "attempt_store.py",
           SRC / "analyzer" / "task_store.py", SRC / "executor" / "observability.py",
           SRC / "executor" / "capability_signer.py", SRC / "modelledger_view.py")
# 掃描範圍:分析端、執行端兩個目錄,加上模型用戶端各模組與花費帳唯讀開法(Phase 11B 增量 1),
# 以及實測命令列與核銷命令列(代碼審第 1 輪:維運檔不准直接跑即時實測、改寫啟用紀錄或核銷)
MODEL_FILES = ("modelclient", "modelcore", "modelclaude", "modelrecording", "modelledger",
               "modelledger_view", "modelverify", "modelledger_writeoff")
SCAN_FILES = tuple(SRC / f"{name}.py" for name in MODEL_FILES)
_SCAN_MODULES = ("rtb.analyzer", "rtb.executor", *(f"rtb.{name}" for name in MODEL_FILES))


def _defined_names(classes=True):
    """分析端、執行端每支模組定義的函式、方法(與類別)名稱。"""
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef) if classes else (
        ast.FunctionDef, ast.AsyncFunctionDef)
    names = set()
    paths = [*(SRC / "analyzer").glob("*.py"), *(SRC / "executor").glob("*.py"), *SCAN_FILES]
    for path in paths:
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
        allowed = {name for table in (READ_WHITELIST, WRITE_WHITELIST, HYPOTHESIS_ONLY)
                   for name, files in table.items() if path.name in files}
        if path.name != HYPOTHESIS:  # 任何維運檔都不准匯入假說命令列(防經它的函式轉手)
            offenders += [f"{path.name}: 匯入假說命令列" for name in _imports(tree, "rtb.ops")
                          if name.split(".")[-1] == HYPOTHESIS.removesuffix(".py")]
        offenders += [f"{path.name}:{line} {name}" for line, name, bare in _called_names(tree)
                      if name in defined and name not in allowed
                      and not (bare and name in local)]
        offenders += _dynamic_lookups(path.name, tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in defined - allowed:
                offenders.append(f"{path.name}:{node.lineno} .{node.attr}")
            elif (isinstance(node, ast.Name) and node.id in functions - allowed
                  and node.id not in local):
                offenders.append(f"{path.name}:{node.lineno} {node.id}")
            elif isinstance(node, ast.ImportFrom):  # 寫入開法、白名單外的函式連匯入都不准
                offenders += [f"{path.name}: 匯入 {a.name}" for a in node.names
                              if a.name in ("InboxStore", "TaskStore", "connect")
                              or (a.name in functions - allowed
                                  and (node.module or "").startswith(_SCAN_MODULES))]
    return offenders


def _imports(tree, package):
    """一支維運檔匯入的模組與名字(相對匯入依層數接回套件名)。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package if node.level else (node.module or "")
            if node.level and node.module:
                base = f"{package}.{node.module}"
            found += [base, *(f"{base}.{a.name}" for a in node.names)]
    return found


# 用字串動態取屬性、用字串執行程式、動態匯入(代碼審第 1、2 輪):名字掃描看不到字串裡的方法名,
# 維運套件本身也用不到,一律不准
DYNAMIC_LOOKUPS = frozenset({"getattr", "attrgetter", "methodcaller", "__getattribute__", "vars",
                             "__dict__", "eval", "exec", "compile", "__import__", "importlib",
                             "import_module", "builtins", "__builtins__"})
# 內建的執行函式:直接呼叫、從 builtins 匯入都算;builtins 模組本身不准匯入或引用(上面的禁用名,別名與
# 變數也就拿不到),所以別的物件上同名的方法不算(副作用核對用正規式模組的 compile,整合增量 3 時撞到;
# 整合代碼審第 1 輪:原本只認字面的 builtins.,別名繞得過)
BUILTIN_RUNNERS = frozenset({"eval", "exec", "compile", "__import__"})


def _dynamic_lookups(label, tree):
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [] if node.attr in BUILTIN_RUNNERS else [node.attr]
        elif isinstance(node, ast.alias):
            names = [node.name, node.name.split(".")[0]]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        found += [f"{label}:{getattr(node, 'lineno', 0)} 動態取屬性或執行 {name}"
                  for name in names if name in DYNAMIC_LOOKUPS]
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
    for banned in ("import sqlite3", "from rtb.sqlitekit import connect", "import rtb.dsp.store",
                   "import subprocess",  # 子行程禁令:Phase 11B 增量 1([S917])
                   "import rtb.modelverify", "import rtb.modelledger_writeoff"):
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
    assert not set(READ_WHITELIST) & set(WRITE_WHITELIST)  # 會寫的名字不混進讀取白名單
    assert not set(READ_WHITELIST) & set(HYPOTHESIS_ONLY)
    assert all(files == {HYPOTHESIS} for files in (*WRITE_WHITELIST.values(),
                                                    *HYPOTHESIS_ONLY.values()))
    classes = {"ReadOnlyInbox", "TaskReader", "ModelLedgerView"}  # 類別不判
    for name in set(READ_WHITELIST) - classes:
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
    # 用字串執行程式或動態匯入(代碼審第 2 輪)
    "\ndef _w(s):\n    return eval('s.rel' + 'ease')\n",
    "\ndef _w(s):\n    exec('s.release()')\n",
    "\ndef _w():\n    return compile('x', 'y', 'eval')\n",
    "\ndef _w():\n    return __import__('rtb.executor.inbox_store')\n",
    "\nimport importlib\n",
    "\nfrom importlib import import_module\n",
    "\ndef _w():\n    import importlib.util\n    return importlib.util\n",
    "\nimport importlib.util as _iu\n",  # 只有子模組的名字
    "\nfrom importlib.util import find_spec as _fs\n",  # 從子模組匯入別的名字
    # 整合增量 3 後:經 builtins 模組取用內建執行函式也算(擋在匯入與引用 builtins 那一步)
    "\nimport builtins\n\ndef _w():\n    return builtins.compile('x', 'y', 'eval')\n",
    "\nfrom builtins import eval as _e\n",
    # 整合代碼審第 1 輪:經別名或變數取到 builtins 也要抓(擋在匯入與引用那一步)
    "\nimport builtins as b\n\ndef _w():\n    return b.eval('1')\n",
    "\nimport builtins\n\ndef _w():\n    x = builtins\n    return x.eval('1')\n",
    "\nfrom builtins import compile as c\n\ndef _w():\n    return c('x', 'y', 'eval')\n",
    "\ndef _w():\n    return __builtins__['eval']('1')\n",
    # Phase 11B 增量 1:模型用戶端的送出呼叫與花費帳寫入,維運套件在增量 1 一律不准碰
    "\ndef _w(r, s):\n    return call_model(r, s)\n",
    "\nfrom rtb.modelledger import write_off as _w\n",
    "\ndef _w(m):\n    return m.used_so_far\n",
    # 經維運內部函式轉手:任何維運檔都不准匯入假說命令列(計劃第 7 版 [S912])
    "\nfrom rtb.ops.hypothesis import ask as _a\n",
    "\nfrom . import hypothesis\n",
    "\nfrom .hypothesis import ask\n",
    "\nimport rtb.ops.hypothesis\n",
    # 實測命令列與核銷命令列也在掃描範圍(代碼審第 1 輪:維運檔能直接跑即時實測、改寫啟用紀錄)
    "\ndef _w(p):\n    return write_record(verify(p, {}))\n",
    "\nfrom rtb.modelverify import verify as _v\n",
    "\ndef _w(p):\n    return write_off(p, 1, 0, 'r', 'e')\n",
    # 花費帳的讀法只准指標用(白名單逐檔):追蹤檢視用了也要抓
    "\ndef _w(v):\n    return v.calls_between('a', 'b')\n",
    "\ndef _w(p):\n    return ModelLedgerView(p)\n",
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


def test_the_ops_scan_allows_methods_that_share_a_builtin_name(tmp_path):
    """整合增量 3 後:副作用核對用正規式模組的 compile 編譯固定樣式,不是內建的 compile;禁用的是
    內建執行函式本身(直接呼叫或經 builtins 取用),物件上同名的方法不算。"""
    copy = tmp_path / "ops"
    copy.mkdir()
    for path in OPS.glob("*.py"):
        (copy / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with (copy / "trace.py").open("a", encoding="utf-8") as file:
        file.write("\nimport re\n\n_P = re.compile('x')\n")
    assert _ops_offenders(copy) == []


def test_ops_command_lines_refuse_abbreviated_options_by_default():
    """[代碼審 r2 a3] 不收縮寫放在維運共用的解析器裡當預設,之後新增的命令列不會漏掉。"""
    from rtb.ops.cli import Parser

    assert Parser().allow_abbrev is False
