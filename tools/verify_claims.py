"""宣稱驗證器:依五條安全宣稱的 JSON 證據清單做機械檢查(Phase 11)。

用法:python tools/verify_claims.py claims/(本機與 CI 同一條指令)
結束代碼:0 全通過;1 有擋下;2 無法判定(參數錯、清單讀不懂)。

依序:①格式(每一層白名單、重複鍵、NaN、布林不是整數、claim_id 跟檔名、五條必要宣稱)②存在(路徑
規則、symbols 只認最外層定義)③依賴閉包與雜湊新舊④列舉覆蓋⑤故障注入的證據真的引用了宣告的手段、
而且有斷言⑥自己跑證據測試。前面幾步有擋下就不跑測試。清單不帶任何結果,結果只印在輸出裡;驗證器只做
機械檢查,不判斷證據在語意上夠不夠,也不讀任何審查結論。

驗證器不匯入產品程式:讀程式一律用語法樹,跑測試用子行程。
"""

import argparse
import ast
import contextlib
import functools
import hashlib
import importlib.metadata
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, TypeGuard

EXIT_PASS, EXIT_BLOCK, EXIT_UNDECIDABLE = 0, 1, 2
MANIFEST_VERSION = 1
REQUIRED_CLAIMS = ("aggregate-blast-radius", "concurrency", "idempotency-unknown-outcome",
                   "permission-guardrail", "prompt-injection")
# policy 用了這些詞就是在講「全部」,要有列舉(第 2 輪設計審:只列五個詞,換成「各種」就掃不到)
# 代碼審第 1 輪補「每一X」「每條」「每次」:「每一個」不含「每個」,原清單掃不到
SCOPE_WORDS = ("每一種", "每種", "每一個", "每個", "每一支", "每支", "每一筆", "每筆",
               "每一項", "每項", "每一條", "每條", "每次", "各種", "所有", "全部", "全數",
               "任何", "一律", "凡是", "逐一")
KINDS = frozenset({"unit", "concurrency", "failure_injection", "end_to_end"})
TOP_KEYS = frozenset({"manifest_version", "claim_id", "policy", "scope", "harness",
                      "enumerations", "evidence", "symbols"})
FILE_KEYS = frozenset({"path", "sha256"})
ENUMERATION_KEYS = frozenset({"source"})
EVIDENCE_KEYS = frozenset({"node", "covers", "kind"})
INJECTION_KEY = "injection"  # 只有 failure_injection 的證據帶
PYTEST_CONFIG = "pyproject.toml"
OTHER_PYTEST_CONFIGS = ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg")
CLEARED_VARIABLES = ("PYTHONPATH", "PYTHONSTARTUP")
TIMEOUT_SECONDS = 900
SHA256 = re.compile(r"[0-9a-f]{64}")
CODE_ROOTS = ("src", "tests")  # 依賴閉包只走專案自己的程式:src 算 scope、tests 算 harness
ROOT_CONFTEST = "conftest.py"  # repo 根的 conftest 也會被 pytest 載入(代碼審第 1 輪)
EVIDENCE_ROOT = "tests"  # 證據測試只准放這底下,每層都要是套件
PLUGINS_NAME = "pytest_plugins"
# pytest 內建外掛(抄自 _pytest.config.builtin_plugins,測試斷言跟安裝的 pytest 一致;版本由
# requirements-dev 釘住,不是任意第三方程式),寫在 pytest_plugins 或 -p 都放行
PYTEST_BUILTIN_PLUGINS = frozenset({
    "assertion", "cacheprovider", "capture", "debugging", "doctest", "faulthandler", "fixtures",
    "helpconfig", "junitxml", "legacypath", "logging", "main", "mark", "monkeypatch",
    "pastebin", "pytester", "pytester_assertions", "python", "recwarn", "reports", "runner",
    "setuponly", "setupplan", "skipping", "stepwise", "subtests", "terminal",
    "terminalprogress", "threadexception", "tmpdir", "unittest", "unraisableexception",
    "warnings"})
PYTHONPATH_ALLOWED = ["src"]  # 多列目錄時頂層名稱的解析會跟著變,驗證器不模擬(代碼審第 2 輪)
# [tool.pytest.ini_options] 的白名單(代碼審第 3 輪:addopts 的 -o 能改掉 pythonpath;只准正式 repo
# 現在用到的鍵,其他一律擋)
PYTEST_OPTIONS_ALLOWED = frozenset({"testpaths", "pythonpath"})
SYS_PATH_METHODS = frozenset({"insert", "append", "extend"})
# 驗證器自己塞進 pytest 的唯讀紀錄器:非嚴格 xfail 的測試通過時,JUnit 裡跟一般通過長得一樣
# (標記寫 strict=False 會蓋過 xfail_strict),所以從 pytest 的報告直接記下 wasxfail 的節點
RUNNER = (
    "import json, sys\n"
    "import pytest\n"
    "class Recorder:\n"
    "    def __init__(self):\n"
    "        self.xpassed = []\n"
    "    def pytest_runtest_logreport(self, report):\n"
    "        if report.passed and hasattr(report, 'wasxfail'):\n"
    "            self.xpassed.append(report.nodeid)\n"
    "recorder = Recorder()\n"
    "code = pytest.main(sys.argv[2:], plugins=[recorder])\n"
    "with open(sys.argv[1], 'w', encoding='utf-8') as out:\n"
    "    json.dump(recorder.xpassed, out)\n"
    "sys.exit(int(code))\n"
)


class Undecidable(Exception):
    """清單讀不懂或參數錯:沒辦法開始判。"""


class _CodeUnreadable(Exception):
    """範圍內的程式讀不懂(語法錯、編碼錯):清單讀得懂,算擋下不算無法判定。"""


def _parse_code(root: Path, rel: str) -> ast.Module:
    try:
        return ast.parse((root / rel).read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise _CodeUnreadable(f"{rel} 讀不懂(語法或編碼錯),沒辦法檢查:{exc}") from exc


@dataclass(frozen=True)
class Evidence:
    node: str
    covers: tuple[str, ...]
    kind: str
    injection: str | None = None  # 只有 failure_injection 有

    @property
    def file(self) -> str:
        return self.node.split("::", 1)[0]


@dataclass(frozen=True)
class Manifest:
    claim_id: str
    policy: str
    scope: dict[str, str]  # 路徑 → sha256
    harness: dict[str, str]
    enumerations: tuple[str, ...]  # 「來源檔:常數名」
    evidence: tuple[Evidence, ...]
    symbols: tuple[str, ...]  # 「檔:名稱」或「檔:類別.方法」


# ── ① 格式 ───────────────────────────────────────────────────────────────────

class _Reading:
    """讀檔時記下「讀得懂但不合規則」的事:重複鍵、NaN 與 Infinity。"""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def pairs(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _ in pairs]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            self.problems.append(f"重複的鍵:{', '.join(repeated)}")
        return dict(pairs)

    def constant(self, name: str) -> None:
        self.problems.append(f"不准 {name}")


def read_json(path: Path) -> tuple[Any, list[str]]:
    reading = _Reading()
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text, object_pairs_hook=reading.pairs, parse_constant=reading.constant)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        # 超長整數丟的是 ValueError 不是 JSONDecodeError(第 1 輪設計審實測),一併收
        raise Undecidable(f"{path.name} 讀不懂:{exc}") from exc
    return data, reading.problems


def _objects(value: Any, allowed: frozenset[str], where: str) -> tuple[list[dict[str, Any]],
                                                                        list[str]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return [], [f"{where} 要是物件陣列"]
    problems = [f"{where}[{index}] {problem}" for index, item in enumerate(value)
                for problem in _key_problems(item, allowed, allowed)]
    return value, problems


def _files(value: Any, where: str) -> tuple[dict[str, str], list[str]]:
    items, problems = _objects(value, FILE_KEYS, where)
    files: dict[str, str] = {}
    for item in items:
        path, digest = item.get("path"), item.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            problems.append(f"{where} 的 path 要是字串、sha256 要是 64 位小寫十六進位:{path}")
        elif path in files:
            problems.append(f"{where} 重複列了 {path}")
        else:
            files[path] = digest
    return files, problems


def _evidence(value: Any) -> tuple[tuple[Evidence, ...], list[str]]:
    if not isinstance(value, list) or not value:
        return (), ["evidence 要是非空的物件陣列"]
    found: list[Evidence] = []
    problems: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            problems.append(f"evidence[{index}] 要是物件")
            continue
        allowed = EVIDENCE_KEYS | ({INJECTION_KEY} if item.get("kind") == "failure_injection"
                                   else set())
        problems += [f"evidence[{index}] {p}" for p in _key_problems(item, allowed, allowed)]
        node, covers, kind = item.get("node"), item.get("covers"), item.get("kind")
        if not isinstance(node, str) or not _is_strings(covers) or not isinstance(
                kind, str) or kind not in KINDS:  # kind 是陣列時不能拿去查集合
            problems.append(f"evidence[{index}] 的 node、covers、kind 寫法不對:{node}")
            continue
        problems += _node_problems(node, [e.node for e in found])
        injection = item.get(INJECTION_KEY)
        if kind == "failure_injection" and INJECTION_KEY in item and (
                not isinstance(injection, str) or not injection.strip()):  # 缺鍵由上面那道抓
            problems.append(f"evidence[{index}] 的 injection 要寫注入手段:{node}")
            continue
        found.append(Evidence(node, tuple(covers), str(kind),
                              injection if isinstance(injection, str) else None))
    return tuple(found), problems


def _key_problems(item: dict[str, Any], allowed: Iterable[str],
                  required: Iterable[str]) -> list[str]:
    extra = sorted(set(item) - set(allowed))
    missing = sorted(set(required) - set(item))
    return ([f"有不認得的鍵:{', '.join(extra)}"] if extra else []) + (
        [f"缺鍵:{', '.join(missing)}"] if missing else [])


def _node_problems(node: str, earlier: list[str]) -> list[str]:
    if "::" not in node or not node.split("::")[-1]:
        return [f"證據節點要寫到函式(檔案::函式):{node}"]
    if "[" in node:
        return [f"證據節點不准帶參數方括號(只會跑到部分參數):{node}"]
    if node in earlier:
        return [f"重複的證據節點:{node}"]
    return []


def _is_strings(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def parse_manifest(name: str, data: Any) -> tuple[Manifest | None, list[str]]:
    if not isinstance(data, dict):
        return None, ["最外層要是物件"]
    problems = _key_problems(data, TOP_KEYS, TOP_KEYS)
    version, claim_id, policy = (data.get("manifest_version"), data.get("claim_id"),
                                 data.get("policy"))
    if type(version) is not int or version != MANIFEST_VERSION:  # 布林在 Python 裡等於 1
        problems.append(f"manifest_version 要是整數 {MANIFEST_VERSION}")
    if claim_id != name:
        problems.append(f"claim_id {claim_id} 跟檔名 {name} 不一致")
    if not isinstance(policy, str) or not policy.strip():
        problems.append("policy 要是一句白話")
    scope, scope_problems = _files(data.get("scope"), "scope")
    harness, harness_problems = _files(data.get("harness"), "harness")
    enumerations, enumeration_problems = _objects(data.get("enumerations"), ENUMERATION_KEYS,
                                                  "enumerations")
    evidence, evidence_problems = _evidence(data.get("evidence"))
    symbols = data.get("symbols")
    if not _is_strings(symbols):
        problems.append("symbols 要是字串陣列")
    problems += scope_problems + harness_problems + enumeration_problems + evidence_problems
    if problems:
        return None, problems
    return Manifest(name, str(policy), scope, harness,
                    tuple(str(item.get("source")) for item in enumerations), evidence,
                    tuple(symbols) if _is_strings(symbols) else ()), []


def check_manifests(root: Path, claims_dir: Path) -> tuple[list[Manifest], list[str]]:
    """第 1 到 5 步;讀不懂的清單丟 Undecidable。"""
    paths = sorted(claims_dir.glob("*.json"))
    manifests: list[Manifest] = []
    problems: list[str] = []
    for path in paths:
        data, reading = read_json(path)
        manifest, format_problems = parse_manifest(path.stem, data)
        problems += [f"{path.stem}:{p}" for p in reading + format_problems]
        if manifest is not None:
            manifests.append(manifest)
    present = {path.stem for path in paths}
    problems += [f"少了必要宣稱:{claim}" for claim in REQUIRED_CLAIMS if claim not in present]
    problems += repository_problems(root)
    for manifest in manifests:
        problems += [f"{manifest.claim_id}:{p}" for p in check_manifest(root, manifest)]
    return manifests, problems


def repository_problems(root: Path) -> list[str]:
    # pyproject.toml 本身一定在 harness(第 3 步),缺了會在那裡擋,不另查
    return [f"repo 根有 {name}:pytest 設定只准 {PYTEST_CONFIG} 一份"
            for name in OTHER_PYTEST_CONFIGS if (root / name).exists()]


def check_manifest(root: Path, manifest: Manifest) -> list[str]:
    try:
        problems = existence_problems(root, manifest)
        if problems:  # 路徑本身有問題時,閉包與雜湊只會多出一串連帶的原因
            return problems
        return (closure_problems(root, manifest) + enumeration_problems(root, manifest)
                + injection_problems(root, manifest))
    except _CodeUnreadable as exc:
        return [str(exc)]


# ── ② 存在 ───────────────────────────────────────────────────────────────────

def path_problem(root: Path, rel: str) -> str | None:
    """相對 repo 根、/ 分隔;從根逐段比對目錄實際列出的名字(大小寫完全一樣),每段不准是符號連結。"""
    if not rel or rel.startswith("/") or "\\" in rel or Path(rel).is_absolute():
        return f"{rel}:不准絕對路徑或反斜線"
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return f"{rel}:路徑不准有 ..、. 或空段"
    here = root
    for part in parts:
        try:
            names = os.listdir(here)
        except OSError:
            return f"{rel}:不存在"
        if part not in names:  # macOS 預設不分大小寫,只看「存在」會本機過、CI 擋
            return f"{rel}:不存在(每一段的大小寫要跟實際檔名完全一樣)"
        here = here / part
        if here.is_symlink():
            return f"{rel}:路徑上有符號連結"
    return None if here.is_file() else f"{rel}:不是檔案"


def existence_problems(root: Path, manifest: Manifest) -> list[str]:
    paths = [*manifest.scope, *manifest.harness, *(e.file for e in manifest.evidence)]
    problems = [p for p in (path_problem(root, rel) for rel in paths) if p]
    problems += [f"{rel} 同時列在 scope 與 harness"
                 for rel in sorted(set(manifest.scope) & set(manifest.harness))]
    for test_file in sorted({e.file for e in manifest.evidence}):
        problems += _evidence_location_problems(root, test_file)
    for reference, kind in [*((s, "symbols") for s in manifest.symbols),
                            *((e, "enumerations") for e in manifest.enumerations)]:
        problem = _reference_problem(root, reference, kind)
        if problem:
            problems.append(problem)
    return problems


def _evidence_location_problems(root: Path, test_file: str) -> list[str]:
    """證據測試只准在 tests/ 底下,而且到它為止的每層目錄都要有 __init__.py(代碼審第 1 輪:
    不是套件的測試目錄,pytest 會把同層 helper 當頂層模組匯入,閉包解析不到;要求每層是套件,
    比模擬 pytest 的各種匯入模式簡單而且不會猜錯)。"""
    parts = test_file.split("/")
    if parts[0] != EVIDENCE_ROOT or len(parts) < 2:
        return [f"{test_file}:證據測試要放在 {EVIDENCE_ROOT}/ 底下"]
    return [f"{'/'.join(parts[:depth])} 沒有 __init__.py:證據測試所在的每層目錄都要是套件"
            for depth in range(1, len(parts))
            if not (root / "/".join([*parts[:depth], "__init__.py"])).is_file()]


def _reference_problem(root: Path, reference: str, kind: str) -> str | None:
    rel, _, name = reference.partition(":")
    if not name:
        return f"{kind} 寫法要是「檔:名稱」:{reference}"
    problem = path_problem(root, rel)
    if problem:
        return f"{kind} {problem}"
    if kind == "enumerations":
        return None  # 讀不讀得出字面常數在第 4 步
    tree = _parse_code(root, rel)
    opaque = _opaque_binding(tree.body, name.split(".")[0] if "." in name else None)
    if opaque:
        return f"symbols {reference} 所在的模組有{opaque},驗證器不展開、一律擋"
    if not _defines(tree, name):
        return (f"symbols {reference} 不是唯一一次的最外層定義(不在 if 等區塊底下,"
                "模組裡也沒有別處再綁定、del 或用 global 改它)")
    return None


_OWN_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _this_level(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """這一層執行時會碰到的節點:進 if、try、for、with 等區塊,不進 def、class、lambda 的本體。
    推導式有自己的作用域:只排除它自己的迭代目標,元素、條件、被迭代的東西照樣走(代碼審第 3 輪:
    整段跳過會漏掉裡面的 setattr;裡面的 := 會綁到外層,也照樣算);只有型別註記、沒有值的宣告
    不綁定任何東西。"""
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, _COMPREHENSIONS):
            pending += [n for n in ast.iter_child_nodes(node)
                        if not isinstance(n, ast.comprehension)]
            for generator in node.generators:
                pending += [generator.iter, *generator.ifs]
                yield generator  # 只給屬性指派檢查看它的目標;目標名字本身不算外層綁定
            continue
        yield node
        if isinstance(node, ast.AnnAssign) and node.value is None:
            continue
        if not isinstance(node, _OWN_SCOPE):
            pending.extend(ast.iter_child_nodes(node))


def _bindings(body: list[ast.stmt], name: str) -> list[ast.AST]:
    """這一層所有會綁定或刪掉這個名字的地方:def、class、賦值(含 +=、for、with)、匯入、del。"""
    found = [node for node in _this_level(body) if _bound_name(node) == name]
    return sorted(found, key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))


def _bound_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler):
        return node.name
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        return node.id
    if isinstance(node, ast.alias):
        return (node.asname or node.name).split(".")[0]
    return None


def _decorators(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return set()
    return {ast.unparse(d) for d in node.decorator_list}


def _accessor_chain(bindings: list[ast.AST], name: str) -> list[ast.AST]:
    """同名 def 只在兩種寫法放行,當成最後那一個:前面每個都帶 @overload,或後面每個都帶
    @名字.setter/getter/deleter(property)。其他多次綁定照擋。"""
    if len(bindings) < 2 or not all(
            isinstance(b, ast.FunctionDef | ast.AsyncFunctionDef) for b in bindings):
        return bindings
    overloads = all(_decorators(b) & {"overload", "typing.overload"} for b in bindings[:-1])
    accessors = {f"{name}.{kind}" for kind in ("setter", "getter", "deleter")}
    chained = all(_decorators(b) & accessors for b in bindings[1:])
    return [bindings[-1]] if overloads or chained else bindings


def _opaque_binding(body: list[ast.stmt], owner: str | None) -> str | None:
    """看不懂的綁定方式:import *、match、對類別的屬性指派或 setattr。一律擋,不去展開
    (代碼審第 2 輪:再去理解更多語法會變打地鼠)。"""
    for node in _this_level(body):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return "import *(算不到它帶進哪些名字)"
        if isinstance(node, ast.Match):
            return "match 敘述(捕獲樣式會重綁名字)"
        if owner is not None and _touches_attribute(node, owner):
            return f"對 {owner} 的屬性指派或 setattr"
    return None


def _chain(expr: ast.expr) -> list[str]:
    """屬性鏈上每一段的名字,一路取到最底(代碼審第 3 輪:只看一層會漏掉 Outer.Inner.meth)。"""
    names: list[str] = []
    while True:
        if isinstance(expr, ast.Attribute):
            names.append(expr.attr)
            expr = expr.value
        elif isinstance(expr, ast.Call | ast.Subscript):
            expr = expr.func if isinstance(expr, ast.Call) else expr.value
        else:
            return [*names, expr.id] if isinstance(expr, ast.Name) else names


def attribute_writes(node: ast.AST) -> list[str]:
    """這個節點對屬性做指派、del、setattr、delattr 時,被改的屬性鏈上的每個名字。"""
    if isinstance(node, ast.Assign | ast.Delete):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.For | ast.AsyncFor
                    | ast.comprehension):
        targets = [node.target]  # for、推導式的目標也是指派(代碼審:for H.handle in … 會漏)
    elif isinstance(node, ast.withitem):
        targets = [node.optional_vars] if node.optional_vars is not None else []
    elif isinstance(node, ast.Call) and ast.unparse(node.func) in ("setattr", "delattr"):
        named = [a.value for a in node.args[1:2]
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        return [*(_chain(node.args[0]) if node.args else []), *named]
    else:
        return []
    return [name for target in targets for t in ast.walk(target)
            if isinstance(t, ast.Attribute) and isinstance(t.ctx, ast.Store | ast.Del)
            for name in _chain(t)]


def _touches_attribute(node: ast.AST, owner: str) -> bool:
    return owner in attribute_writes(node)


def _globally_rebound(tree: ast.Module, name: str) -> bool:
    return any(isinstance(node, ast.Global) and name in node.names for node in ast.walk(tree))


def _sole_statement(body: list[ast.stmt], name: str) -> ast.stmt | None:
    """這個名字在這一層只被綁定一次、而且就是直接寫在這一層的那一句時,回傳那一句。"""
    bindings = _accessor_chain(_bindings(body, name), name)
    if len(bindings) != 1:
        return None
    for statement in body:
        if statement is bindings[0] or any(node is bindings[0] for node in ast.walk(statement)
                                           if not isinstance(statement, _OWN_SCOPE)):
            return statement
    return None


def _defines(tree: ast.Module, name: str) -> bool:
    """最外層唯一一次定義(代碼審第 1 輪:只看最外層直接敘述會漏 try、if 裡的重綁、del 與 global)。"""
    *owners, leaf = name.split(".")
    if _globally_rebound(tree, (owners or [leaf])[0]):
        return False
    body: list[ast.stmt] = tree.body
    for index, owner in enumerate(owners):
        node = _sole_statement(body, owner)
        if not isinstance(node, ast.ClassDef):
            return False
        body = node.body
        if _opaque_binding(body, ([*owners, leaf])[index + 1]):  # 類別本體裡改巢狀類別
            return False
    definition = _sole_statement(body, leaf)
    return isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)


# ── ③ 依賴閉包與雜湊新舊 ─────────────────────────────────────────────────────

def _module_file(root: Path, dotted: str) -> str | None:
    parts = dotted.split(".")
    for base in (root / "src", root):
        for candidate in (base.joinpath(*parts).with_suffix(".py"),
                          base.joinpath(*parts, "__init__.py")):
            if candidate.is_file():
                return candidate.relative_to(root).as_posix()
    return None


def _package_parts(rel: str) -> list[str]:
    parts = rel.split("/")[:-1]
    return parts[1:] if parts[:1] == ["src"] else parts


def imported_modules(tree: ast.AST, package: list[str]) -> set[str]:
    """檔案裡任何位置的匯入(含函式內、TYPE_CHECKING 區塊、相對匯入)。`from a.b import x` 的 x
    若是子模組也算(命名空間套件常這樣寫,只解析 a.b 會漏掉 x)。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = package[:len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            names |= {module} | {f"{module}.{alias.name}".lstrip(".") for alias in node.names}
    return {name for name in names if name}


def _ancestor_inits(root: Path, rel: str) -> set[str]:
    """上層套件的 __init__.py:匯入子模組時 Python 會隱式執行它們。"""
    parts = rel.split("/")
    start = 2 if parts[0] == "src" else 1
    found = set()
    for depth in range(start, len(parts)):
        init = "/".join([*parts[:depth], "__init__.py"])
        if init != rel and (root / init).is_file():
            found.add(init)
    return found


def _in_code(rel: str) -> bool:
    return rel.split("/")[0] in CODE_ROOTS or rel == ROOT_CONFTEST


def _literal_names(value: ast.expr | None) -> list[str] | None:
    """只收字串組成的 tuple 字面(單一字串、list 都不收)。"""
    if isinstance(value, ast.Tuple) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts):
        return [str(e.value) for e in value.elts if isinstance(e, ast.Constant)]
    return None


def plugin_modules(tree: ast.Module, rel: str) -> tuple[list[str], list[str]]:
    """最外層 pytest_plugins 的字面字串(pytest 會照字串載入,匯入分析看不到)。只准直接寫在最外層
    的一次 tuple 字面,模組裡不准再出現這個名字(代碼審第 3 輪:list 之後 append、+= 都會漏)。"""
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == PLUGINS_NAME]
    if not uses:
        return [], []
    statement = _sole_statement(tree.body, PLUGINS_NAME)
    value = statement.value if isinstance(statement, ast.Assign) else None
    found = _literal_names(value)  # 只收 tuple 字面
    if found is None or len(uses) != 1:
        return [], [f"{rel} 的 {PLUGINS_NAME} 只准最外層一次 tuple 字面,驗證器算不到它載入的外掛"]
    return found, []


def _plugin_files(root: Path, names: list[str], where: str) -> tuple[list[str], list[str]]:
    files, problems = [], []
    for name in names:
        if name in PYTEST_BUILTIN_PLUGINS:
            continue
        found = _module_file(root, name)
        if found is None:
            problems.append(f"{where} 載入的外掛 {name} 不在 repo 裡,驗證器算不到它的雜湊")
        else:
            files.append(found)
    return files, problems


def dependency_closure(root: Path, starts: Iterable[str]) -> tuple[set[str], list[str]]:
    """從起點沿靜態匯入與 pytest_plugins 在 src、tests(與根 conftest)內遞迴;回傳 repo 根相對
    路徑與讀不出來的原因。"""
    pending, seen, problems = list(starts), set(), []
    while pending:
        rel = pending.pop()
        if rel in seen or not _in_code(rel) or not (root / rel).is_file():
            continue
        seen.add(rel)
        pending += sorted(_ancestor_inits(root, rel))
        if not rel.endswith(".py"):
            continue
        tree = _parse_code(root, rel)
        modules = imported_modules(tree, _package_parts(rel))
        found_files = [found for module in modules
                       if (found := _module_file(root, module)) is not None]
        names, plugin_problems = plugin_modules(tree, rel)
        files, missing = _plugin_files(root, names, rel)
        pending += found_files + files
        problems += plugin_problems + missing + _outside_code(found_files + files, rel)
        problems += _unknown_modules(root, modules, rel) + _import_path_changes(tree, rel)
    return seen, problems


@functools.cache
def _installed_top_levels() -> frozenset[str]:
    return frozenset(importlib.metadata.packages_distributions())


def _unknown_modules(root: Path, modules: set[str], importer: str) -> list[str]:
    """預設擋:頂層名稱在 repo 裡、標準函式庫或已安裝套件都找不到,就不知道它實際載入什麼
    (代碼審第 3 輪:原本當成第三方套件略過)。"""
    unknown = set()
    for module in modules:
        top = module.split(".")[0]
        if _module_file(root, top) is None and top not in sys.stdlib_module_names and (
                top not in _installed_top_levels()):
            unknown.add(top)
    return [f"{importer} 匯入的 {top} 解析不到(不是 repo 內、標準函式庫或已安裝套件)"
            for top in sorted(unknown)]


def _import_path_changes(tree: ast.Module, rel: str) -> list[str]:
    """動 sys.path 或呼叫 site.addsitedir 會讓頂層名稱解析到驗證器沒算的檔,一律擋。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = ast.unparse(node.func)
            if called.startswith("sys.path.") and called.split(".")[-1] in SYS_PATH_METHODS:
                return [f"{rel} 改了 sys.path({called}),驗證器不跟著解析,一律擋"]
            if called.split(".")[-1] == "addsitedir":
                return [f"{rel} 呼叫了 addsitedir,驗證器不跟著解析,一律擋"]
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, ast.AugAssign | ast.AnnAssign) else [])
        if any(ast.unparse(t).startswith("sys.path") for t in targets):
            return [f"{rel} 改了 sys.path,驗證器不跟著解析,一律擋"]
    return []


def _outside_code(files: list[str], importer: str) -> list[str]:
    """解析得到、但不在 src、tests 或根 conftest 的 repo 內檔:pytest 照樣載入,驗證器不算它的
    閉包,所以擋(代碼審第 2 輪:原本被靜默略過)。"""
    return [f"{importer} 用到的 {rel} 在 src、tests 以外,驗證器不算它的閉包"
            for rel in files if not _in_code(rel)]


def pytest_config_problems(root: Path) -> list[str]:
    """pytest 設定白名單:只准 [tool.pytest.ini_options] 的 testpaths 與 pythonpath(["src"])
    (代碼審第 3 輪:addopts 的 -o 能改掉 pythonpath、-p 能載入外掛;正式 repo 只用這兩個鍵)。"""
    try:
        config = tomllib.loads((root / PYTEST_CONFIG).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"{PYTEST_CONFIG} 讀不懂:{exc}"]
    table = config.get("tool", {}).get("pytest", {})
    problems = [f"{PYTEST_CONFIG} 有 [tool.pytest] 原生表的設定({key}):只准寫在 "
                "[tool.pytest.ini_options]" for key in sorted(table) if key != "ini_options"]
    options = table.get("ini_options", {})
    problems += [f"{PYTEST_CONFIG} 的 [tool.pytest.ini_options] 有 {key}:只准 "
                 f"{', '.join(sorted(PYTEST_OPTIONS_ALLOWED))}"
                 for key in sorted(options) if key not in PYTEST_OPTIONS_ALLOWED]
    pythonpath = options.get("pythonpath", PYTHONPATH_ALLOWED)
    if (pythonpath if isinstance(pythonpath, list) else [pythonpath]) != PYTHONPATH_ALLOWED:
        problems.append(f"{PYTEST_CONFIG} 的 pythonpath 只准 {PYTHONPATH_ALLOWED}:{pythonpath}")
    return problems


def _conftests(root: Path, test_file: str) -> set[str]:
    parts = test_file.split("/")[:-1]
    candidates = ["/".join([*parts[:depth], ROOT_CONFTEST]) for depth in range(len(parts) + 1)]
    return {c for c in candidates if (root / c).is_file()}


def closure_problems(root: Path, manifest: Manifest) -> list[str]:
    tests = {e.file for e in manifest.evidence}
    conftests = set().union(*(_conftests(root, t) for t in tests))
    references = {r.partition(":")[0] for r in (*manifest.symbols, *manifest.enumerations)}
    problems = pytest_config_problems(root)
    declared = {*manifest.scope, *manifest.harness}  # 手動列的動態入口也要展開(代碼審第 2 輪)
    problems += [f"{rel} 在 src、tests 以外,驗證器不算它的閉包" for rel in sorted(declared)
                 if rel.endswith(".py") and not _in_code(rel)]
    closure, closure_reasons = dependency_closure(
        root, {*declared, *references, *tests, *conftests})
    problems += closure_reasons + _rewrites_across(root, closure, manifest)
    need_scope = {rel for rel in closure if rel.startswith("src/")}  # 起點本身也在閉包裡
    need_harness = {rel for rel in closure if not rel.startswith("src/")} | {PYTEST_CONFIG}
    problems += [f"scope 少列 {rel}(依賴閉包)" for rel in sorted(need_scope - set(manifest.scope))]
    problems += [f"harness 少列 {rel}(依賴閉包)"
                 for rel in sorted(need_harness - set(manifest.harness))]
    for rel, digest in sorted({**manifest.scope, **manifest.harness}.items()):
        if hashlib.sha256((root / rel).read_bytes()).hexdigest() != digest:
            problems.append(f"{rel} 的雜湊跟清單不一致(證據可能是舊版本的)")
    return problems


def _rewrites_across(root: Path, closure: set[str], manifest: Manifest) -> list[str]:
    """整個閉包裡,模組這一層(含 if、try 區塊與推導式)對列舉常數名或 symbol 路徑上任一名字做
    屬性指派、del、setattr、delattr,都擋(代碼審第 3 輪:別的模組用「模組.登錄表 = …」擴充)。"""
    watched = {source.partition(":")[2] for source in manifest.enumerations}
    watched |= {part for symbol in manifest.symbols
                for part in symbol.partition(":")[2].split(".")}
    problems = []
    for rel in sorted(r for r in closure if r.endswith(".py")):
        body = _parse_code(root, rel).body
        hits = sorted({name for node in _this_level(body) for name in attribute_writes(node)}
                      & watched)
        problems += [f"{rel} 在模組層對 {name} 做屬性指派、del 或 setattr,驗證器不展開、一律擋"
                     for name in hits]
    return problems


# ── ④ 列舉覆蓋 ───────────────────────────────────────────────────────────────

def _literal_strings(value: ast.expr | None) -> set[str] | None:
    """只收不可變的:tuple 字面,或 frozenset(字面)(代碼審第 1 輪:list、set 可以事後 append)。"""
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and (
            value.func.id == "frozenset" and len(value.args) == 1 and not value.keywords):
        value = value.args[0]
        if not isinstance(value, ast.Tuple | ast.List | ast.Set):
            return None
    elif not isinstance(value, ast.Tuple):
        return None
    items = [e.value for e in value.elts if isinstance(e, ast.Constant)]
    if len(items) != len(value.elts) or not all(isinstance(i, str) for i in items):
        return None
    return {str(item) for item in items}


def read_registry(root: Path, source: str) -> set[str] | None:
    """最外層唯一一次綁定是普通或帶型別註記的賦值,值是字串字面的 tuple 或 frozenset;模組裡別處
    再綁定、del 或用 global 改它都不算(代碼審第 1 輪)。"""
    rel, _, name = source.partition(":")
    tree = _parse_code(root, rel)
    if _globally_rebound(tree, name) or _opaque_binding(tree.body, None):
        return None
    statement = _sole_statement(tree.body, name)
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(
            statement.targets[0], ast.Name):
        return _literal_strings(statement.value)
    if isinstance(statement, ast.AnnAssign):
        return _literal_strings(statement.value)
    return None


def enumeration_problems(root: Path, manifest: Manifest) -> list[str]:
    words = [word for word in SCOPE_WORDS if word in manifest.policy]
    problems = [f"policy 用了範圍詞「{words[0]}」卻沒有列舉(沒有登錄表可列舉就要改寫宣稱)"
                ] if words and not manifest.enumerations else []
    items: set[str] = set()
    for source in manifest.enumerations:
        found = read_registry(root, source)
        if found is None:
            rel = source.partition(":")[0]
            opaque = _opaque_binding(_parse_code(root, rel).body, None)
            why = f"所在的模組有{opaque}" if opaque else "動態組出來、從別處匯入或被重新指派"
            problems.append(f"{source} 讀不出字面常數({why})")
        else:
            items |= found
    covered = {item for e in manifest.evidence for item in e.covers}
    problems += [f"列舉項目 {item} 沒有任何證據 covers" for item in sorted(items - covered)]
    problems += [f"covers 寫了列舉裡沒有的 {item}" for item in sorted(covered - items)]
    return problems


# ── ⑤ 故障注入 ───────────────────────────────────────────────────────────────

def _test_function(tree: ast.Module, node: str) -> ast.FunctionDef | None:
    *owners, name = node.split("::")[1:]
    body: list[ast.stmt] = tree.body
    for owner in owners:
        found = _sole_statement(body, owner)
        if not isinstance(found, ast.ClassDef):
            return None
        body = found.body
    function = _sole_statement(body, name)
    return function if isinstance(function, ast.FunctionDef) else None


def _fixture_like(decorator: ast.expr) -> bool:
    """裝飾器最後一段名字以 fixture 結尾(pytest.fixture、fixture、fake_fixture);usefixtures
    不算(代碼審第 3 輪:原本比子字串,會把掛 usefixtures 的測試當成 fixture)。"""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
    return name.endswith("fixture")


def _is_fixture(statement: ast.stmt) -> bool:
    """長得像 fixture;來源對不對另由 _fixture_source_problems 擋。"""
    return isinstance(statement, ast.FunctionDef) and any(
        _fixture_like(d) for d in statement.decorator_list)


def _pytest_names(tree: ast.Module, attribute: str) -> tuple[set[str], set[str]]:
    """最外層 `import pytest [as x]` 的名字,與 `from pytest import <attribute> [as y]` 的名字。"""
    modules: set[str] = set()
    functions: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            modules |= {a.asname or a.name for a in statement.names if a.name == "pytest"}
        elif isinstance(statement, ast.ImportFrom) and statement.module == "pytest" and (
                not statement.level):
            functions |= {a.asname or a.name for a in statement.names if a.name == attribute}
    return modules, functions


def _from_pytest(expr: ast.expr, names: tuple[set[str], set[str]], attribute: str) -> bool:
    """expr 是 pytest.<attribute>(經 import pytest [as x])或 from pytest import 進來的那個名字。"""
    if isinstance(expr, ast.Attribute) and expr.attr == attribute and isinstance(
            expr.value, ast.Name):
        return expr.value.id in names[0]
    return isinstance(expr, ast.Name) and expr.id in names[1]


def _fixture_decorator(decorator: ast.expr, names: tuple[set[str], set[str]]) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return _from_pytest(target, names, "fixture")


def _fixture_source_problems(tree: ast.Module, fixtures: list[ast.stmt], rel: str) -> list[str]:
    """代碼審第 3 輪:名字像 fixture 的裝飾器,只認證明來自 pytest 的(照 pytest.raises 的做法);
    來源不明的普通裝飾器不會讓 pytest 執行那支函式,採信就會把沒執行的手段算成注入。"""
    names = _pytest_names(tree, "fixture")
    return [f"{rel} 的 {s.name} 用了來源不明的 fixture 裝飾器"
            for s in fixtures if isinstance(s, ast.FunctionDef) and any(
                _fixture_like(d) and not _fixture_decorator(d, names)
                for d in s.decorator_list)]


def _renames(statement: ast.stmt) -> bool:
    """@pytest.fixture(name=…) 把 fixture 改名,pytest 照新名字解析。"""
    return isinstance(statement, ast.FunctionDef) and any(
        isinstance(d, ast.Call) and any(k.arg == "name" for k in d.keywords)
        for d in statement.decorator_list)


def _fixtures(root: Path, test_file: str) -> tuple[dict[str, ast.FunctionDef], list[str]]:
    """測試檔與它路上的 conftest 裡最外層的 fixture;離測試越近的蓋掉越遠的(照 pytest)。
    看不懂就擋(代碼審):任何 fixture 用 name= 改名、同一檔同名 fixture 不只一次、檔案這一層有
    import * 或 match。"""
    found: dict[str, ast.FunctionDef] = {}
    problems: list[str] = []
    for rel in [*sorted(_conftests(root, test_file), key=len), test_file]:
        tree = _parse_code(root, rel)
        body = tree.body
        opaque = _opaque_binding(body, None)
        if opaque:
            problems.append(f"{rel} 有{opaque}")
        fixtures = [s for s in body if _is_fixture(s)]
        problems += _fixture_source_problems(tree, fixtures, rel)
        if _bindings(body, "pytest_generate_tests"):
            problems.append(f"{rel} 定義了 pytest_generate_tests(能用同名參數蓋掉 fixture)")
        problems += [f"{rel} 的 fixture {s.name} 用 name= 改名"
                     for s in fixtures if isinstance(s, ast.FunctionDef) and _renames(s)]
        names = [s.name for s in fixtures if isinstance(s, ast.FunctionDef)]
        problems += [f"{rel} 裡 fixture {name} 定義不只一次"
                     for name in sorted({n for n in names if names.count(n) > 1})]
        found.update({s.name: s for s in fixtures if isinstance(s, ast.FunctionDef)})
    return found, problems


def _decorator_call(function: ast.FunctionDef | ast.ClassDef, suffix: str) -> list[ast.Call]:
    return [d for d in function.decorator_list
            if isinstance(d, ast.Call) and ast.unparse(d.func).endswith(suffix)]


def _owner_classes(tree: ast.Module, node: str) -> list[ast.ClassDef]:
    owners: list[ast.ClassDef] = []
    body: list[ast.stmt] = tree.body
    for owner in node.split("::")[1:-1]:
        found = _sole_statement(body, owner)
        if not isinstance(found, ast.ClassDef):
            return owners
        owners.append(found)
        body = found.body
    return owners


def _is_pytest_mark(decorator: ast.expr, modules: set[str], marks: set[str]) -> bool:
    """pytest.mark.X、pytest.mark.X(...)(經 import pytest [as x]),或 from pytest import mark 的
    mark.X、mark.X(...)。"""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not isinstance(target, ast.Attribute):
        return False
    mark = target.value
    if isinstance(mark, ast.Attribute) and mark.attr == "mark" and isinstance(mark.value, ast.Name):
        return mark.value.id in modules
    return isinstance(mark, ast.Name) and mark.id in marks


def _decorator_problems(tree: ast.Module, targets: list[ast.FunctionDef | ast.ClassDef]
                        ) -> list[str]:
    """代碼審第 3 輪:測試與所在類別的裝飾器只認 pytest.mark.*;存進變數的裝飾器(@CASES)可能是
    parametrize,看不懂就擋。模組層與類別本體的 pytestmark 同理。"""
    modules, marks = _pytest_names(tree, "mark")
    problems = [f"{target.name} 的裝飾器 {ast.unparse(d)} 不是 pytest.mark.*,驗證器看不懂"
                for target in targets for d in target.decorator_list
                if not _is_pytest_mark(d, modules, marks)]
    bodies: list[tuple[str, list[ast.stmt]]] = [("測試檔", tree.body)]
    bodies += [(f"類別 {t.name}", t.body) for t in targets if isinstance(t, ast.ClassDef)]
    problems += [f"{where}有 pytestmark(可能用同名參數蓋掉 fixture)"
                 for where, body in bodies if _bindings(body, "pytestmark")]
    problems += [f"類別 {t.name} 定義了 pytest_generate_tests(能用同名參數蓋掉 fixture)"
                 for t in targets if isinstance(t, ast.ClassDef)
                 and _bindings(t.body, "pytest_generate_tests")]
    return problems


def _class_problems(owners: list[ast.ClassDef]) -> list[str]:
    """測試在類別裡:路上任一類別本體有 fixture、或有 object 以外的基底類別,都看不懂(代碼審第 2 輪:
    類別層 fixture 會蓋掉模組層的,基底類別會帶進別處定義的 fixture 與方法)。"""
    problems = []
    for owner in owners:
        if any(_is_fixture(s) for s in owner.body):
            problems.append(f"測試所在類別本體有 fixture({owner.name})")
        if any(not (isinstance(b, ast.Name) and b.id == "object") for b in owner.bases):
            problems.append(f"測試所在類別有基底類別({owner.name})")
    return problems


def _literal_argnames(call: ast.Call) -> list[str] | None:
    first = call.args[0] if call.args else None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return [n.strip() for n in first.value.split(",") if n.strip()]
    if isinstance(first, ast.Tuple | ast.List) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in first.elts):
        return [str(e.value) for e in first.elts if isinstance(e, ast.Constant)]
    return None


def _requested(function: ast.FunctionDef) -> list[str]:
    """pytest 會拿來找 fixture 的參數:沒有預設值的參數(帶預設值的不是 fixture 請求)。"""
    positional = [*function.args.posonlyargs, *function.args.args]
    required = positional[:len(positional) - len(function.args.defaults)]
    keyword = [a for a, d in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True)
               if d is None]
    return [a.arg for a in (*required, *keyword)]


def _usefixtures(targets: list[ast.FunctionDef | ast.ClassDef]) -> tuple[list[str], list[str]]:
    names: list[str] = []
    problems: list[str] = []
    for target in targets:
        for call in _decorator_call(target, "mark.usefixtures"):
            if all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in call.args):
                names += [str(a.value) for a in call.args if isinstance(a, ast.Constant)]
            else:
                problems.append(f"{target.name} 的 usefixtures 名字不是字面字串,驗證器追不到")
    return names, problems


def _autouse(fixtures: dict[str, ast.FunctionDef]) -> tuple[list[str], list[str]]:
    names: list[str] = []
    problems: list[str] = []
    for name, fixture in fixtures.items():
        for call in (d for d in fixture.decorator_list
                     if isinstance(d, ast.Call) and _fixture_like(d)):
            for keyword in call.keywords:
                if keyword.arg != "autouse":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value,
                                                                            bool):
                    names += [name] if keyword.value.value else []
                else:
                    problems.append(f"fixture {name} 的 autouse 不是字面 True 或 False")
    return names, problems


def _rebound_fixture_names(root: Path, test_file: str, names: set[str]) -> list[str]:
    """測試檔與路上 conftest 的最外層,用賦值或匯入綁定了用到的 fixture 名字:pytest 看到的可能不是
    那個 fixture(代碼審第 2 輪)。只放過直接寫在最外層的 fixture def;藏在 try、if 等區塊裡的
    fixture def 也算另外綁定(代碼審第 3 輪:_fixtures 只收最外層,兩邊會互相漏接)。"""
    problems = []
    for rel in [*sorted(_conftests(root, test_file), key=len), test_file]:
        body = _parse_code(root, rel).body
        for name in sorted(names):
            if any(not (isinstance(b, ast.FunctionDef) and _is_fixture(b)
                        and any(b is statement for statement in body))
                   for b in _bindings(body, name)):
                problems.append(f"{rel} 在最外層另外綁定了 fixture 名字 {name}")
    return problems


def _used_functions(test: ast.FunctionDef, fixtures: dict[str, ast.FunctionDef],
                    extra: list[str]) -> list[ast.FunctionDef]:
    """測試本身,加上它請求的 fixture、usefixtures 與 autouse 的 fixture,再遞迴 fixture 請求的
    fixture;帶預設值的參數不追。"""
    used, pending = [test], [*_requested(test), *extra]
    while pending:
        fixture = fixtures.get(pending.pop())
        if fixture is not None and fixture not in used:
            used.append(fixture)
            pending += _requested(fixture)
    return used


def _executed(function: ast.FunctionDef) -> Iterator[ast.AST]:
    """函式本體這一層會執行的節點:不含文件字串、裝飾器、參數預設值、區域變數的型別註記;不進沒被
    呼叫的巢狀 def、lambda、class;assert 只給出節點本身、不進它的條件與訊息(條件裡的手段不算引用,
    代碼審第 2 輪);f 字串只進它的運算式、不看字面片段。"""
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and (
            isinstance(body[0].value.value, str)):
        body = body[1:]
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (*_OWN_SCOPE, ast.Assert)):
            continue
        if isinstance(node, ast.AnnAssign):
            pending += [node.target, *([node.value] if node.value is not None else [])]
        elif isinstance(node, ast.JoinedStr):
            pending += [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
        else:
            pending.extend(ast.iter_child_nodes(node))


def _literal_only(expr: ast.expr) -> bool:
    """整個運算式沒有名字、屬性、呼叫:值在寫下時就定了(not True、()、1 == 2 都算)。"""
    return not any(isinstance(n, ast.Name | ast.Attribute | ast.Call) for n in ast.walk(expr))


def _ends_flow(statement: ast.stmt) -> bool:
    """之後的敘述到不了:return、raise、break、continue,或 if/else 每一支都以這些結尾。"""
    if isinstance(statement, ast.Return | ast.Raise | ast.Break | ast.Continue):
        return True
    return isinstance(statement, ast.If) and bool(statement.body) and bool(statement.orelse) and (
        _ends_flow(statement.body[-1]) and _ends_flow(statement.orelse[-1]))


def _dead_code(function: ast.FunctionDef) -> str | None:
    """看不懂就擋:條件或 for 迭代的東西只由字面組成,帶常數運算元的 and、or,以及流程結束之後還有
    敘述。代碼審第 3 輪改成白名單(條件裡沒有名字、屬性、呼叫就當常數),並建立在 _executed 之上:
    沒被呼叫的巢狀 def、lambda、class 不算執行,跟引用判定同一個邊界。"""
    for node in (function, *_executed(function)):
        if isinstance(node, ast.If | ast.While | ast.IfExp) and _literal_only(node.test):
            return f"{function.name} 有常數條件(死碼)"
        if isinstance(node, ast.For | ast.AsyncFor) and _literal_only(node.iter):
            return f"{function.name} 的 for 迭代字面值(常數條件)"
        if isinstance(node, ast.BoolOp) and any(_literal_only(v) for v in node.values):
            return f"{function.name} 有帶常數運算元的 and 或 or(常數條件)"
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(node, field, None)
            if isinstance(statements, list) and any(
                    isinstance(st, ast.stmt) and _ends_flow(st) for st in statements[:-1]):
                return f"{function.name} 的 return、raise、break 或 continue 之後還有敘述(死碼)"
    return None


def _mentions(functions: list[ast.FunctionDef], token: str) -> bool:
    """字串要整串相等(代碼審:子字串會讓 NO_DEATH_HERE 命中 DEATH);名字與屬性照舊。"""
    for function in functions:
        for node in _executed(function):
            if isinstance(node, ast.Constant) and node.value == token:
                return True
            if (isinstance(node, ast.Name) and node.id == token) or (
                    isinstance(node, ast.Attribute) and node.attr == token):
                return True
    return False


def _is_pytest_raises(expr: ast.expr, names: tuple[set[str], set[str]]) -> bool:
    return isinstance(expr, ast.Call) and _from_pytest(expr.func, names, "raises")


def _asserts(test: ast.FunctionDef, raises: tuple[set[str], set[str]]) -> bool:
    """測試本體這一層有 assert、放在 with 裡的 pytest.raises(代碼審第 2 輪:任意物件的 .raises()
    與不在 with 裡的呼叫都不算),或呼叫 assert_ 開頭的輔助函式、x.assert_* 屬性。"""
    for node in _executed(test):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.With | ast.AsyncWith) and any(
                _is_pytest_raises(item.context_expr, raises) for item in node.items):
            return True
        if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1].startswith(
                "assert_"):
            return True
    return False


def _resolution_problems(root: Path, tree: ast.Module, evidence: Evidence,
                         test: ast.FunctionDef) -> tuple[list[ast.FunctionDef], list[str]]:
    fixtures, problems = _fixtures(root, evidence.file)
    owners = _owner_classes(tree, evidence.node)
    problems += _class_problems(owners)
    problems += _decorator_problems(tree, [test, *owners])
    extra, usefixture_problems = _usefixtures([test, *owners])
    auto, autouse_problems = _autouse(fixtures)
    problems += usefixture_problems + autouse_problems
    parametrized: set[str] = set()
    targets: list[ast.FunctionDef | ast.ClassDef] = [test, *owners]
    for target in targets:
        for call in _decorator_call(target, "mark.parametrize"):
            names = _literal_argnames(call)
            if names is None:
                problems.append(f"{target.name} 的 parametrize 參數名不是字面字串")
            else:
                parametrized |= set(names)
    used = _used_functions(test, fixtures, [*extra, *auto])
    used_names = {f.name for f in used[1:]} | (set(_requested(test)) & set(fixtures))
    problems += [f"parametrize 的參數 {name} 跟 fixture 同名"
                 for name in sorted(parametrized & used_names)]
    problems += _rebound_fixture_names(root, evidence.file, used_names)
    return used, problems


def injection_problems(root: Path, manifest: Manifest) -> list[str]:
    """只證明有注入、有斷言,不證明注入有意義(天花板,歸審查員)。"""
    problems = []
    for evidence in manifest.evidence:
        if evidence.kind != "failure_injection":
            continue
        tree = _parse_code(root, evidence.file)
        owners = evidence.node.split("::")[1:-1]
        opaque = _opaque_binding(tree.body, owners[0] if owners else None)
        if opaque:  # 看不懂就擋:可能把測試名字換掉(代碼審第 2 輪)
            problems.append(f"{evidence.node}:測試檔有{opaque},驗證器不展開、一律擋")
            continue
        test = _test_function(tree, evidence.node)
        if test is None:
            problems.append(f"{evidence.node}:在 {evidence.file} 找不到這支測試函式")
            continue
        used, unreadable = _resolution_problems(root, tree, evidence, test)
        unreadable += [why for why in map(_dead_code, used) if why]
        if unreadable:
            problems += [f"{evidence.node}:{why},驗證器不照 pytest 解析、一律擋"
                         for why in unreadable]
            continue
        if not _mentions(used, str(evidence.injection)):
            problems.append(f"{evidence.node}:測試與它用的 fixture 沒引用宣告的注入手段 "
                            f"{evidence.injection}")
        if not _asserts(test, _pytest_names(tree, "raises")):
            problems.append(f"{evidence.node}:故障注入測試本體沒有斷言")
    return problems


# ── ⑥ 真的跑 ─────────────────────────────────────────────────────────────────

def clean_environment() -> dict[str, str]:
    """能改測試結果卻不在任何檔案裡的東西先清掉(第 2 輪設計審實測 PYTEST_ADDOPTS 帶 -p 能載入
    把失敗改成通過的外掛)。"""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTEST_") and key not in CLEARED_VARIABLES}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _classname(node: str) -> tuple[str, str]:
    file, *classes, function = node.split("::")
    return ".".join([file.removesuffix(".py").replace("/", "."), *classes]), function


def judge_junit(nodes: list[str], cases: list[ElementTree.Element]) -> list[str]:
    problems = []
    for node in nodes:
        classname, function = _classname(node)
        mine = [c for c in cases if c.get("classname") == classname and (
            c.get("name") == function or str(c.get("name")).startswith(f"{function}["))]
        if not mine:
            problems.append(f"{node} 沒收集到(可能被取消選取)")
        for case in mine:
            failed = case.find("failure") if case.find("failure") is not None else case.find(
                "error")
            if failed is not None:
                problems.append(f"{node} 沒通過:{failed.get('message', '').strip()}")
            elif (skipped := case.find("skipped")) is not None:
                problems.append(f"{node} 被跳過或標成預期失敗:{skipped.get('message', '')}")
    return problems


def _xpassed(nodes: list[str], recorded: Path) -> list[str]:
    try:
        passed = json.loads(recorded.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["pytest 沒留下預期失敗的紀錄,判不出有沒有預期失敗卻通過的測試"]
    return [f"{node} 預期失敗卻通過(非嚴格的 xfail,JUnit 裡看起來跟通過一樣)"
            for node in nodes if any(p == node or str(p).startswith(f"{node}[") for p in passed)]


def run_evidence(root: Path, nodes: list[str], timeout: float) -> list[str]:
    with tempfile.TemporaryDirectory() as temporary:
        junit, recorded = Path(temporary) / "junit.xml", Path(temporary) / "xpassed.json"
        # -E 不讀 PYTHON 開頭的變數、-s 不開 user site(代碼審第 1 輪:CI 的 setup-python 沒有
        # venv,user site 裡的 usercustomize 能在清完環境後再自己設 PYTEST_ADDOPTS)
        # 兩個 -c 不同義、順序不能動:第一個是直譯器的「執行這段碼」(RUNNER 讀 sys.argv[1] 當紀錄
        # 檔、其餘原樣交給 pytest),第二個是交給 pytest 的設定檔旗標
        command = [sys.executable, "-E", "-s", "-c", RUNNER, str(recorded),
                   "-c", PYTEST_CONFIG, "--rootdir", ".",
                   "-p", "no:cacheprovider", "-o", "xfail_strict=true", "-q",
                   f"--junitxml={junit}", *nodes]
        process = subprocess.Popen(  # noqa: S603 - 指令全由驗證器組,節點編號當參數傳
            command, cwd=root, env=clean_environment(), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)  # 整組結束,不留測試另起的孫行程
            process.communicate()
            return [f"證據測試超過總時限 {timeout:g} 秒,逾時(不是通過)"]
        except BaseException:
            # 被要求停止(SIGTERM 轉成的例外、Ctrl-C):證據測試在另一個行程群組,不跟著收到訊號,
            # 先整組結束再往外丟(Phase 12 代碼審 r3 s2:一鍵展示逾時只殺到驗證器,pytest 變孤兒)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            raise
        if process.returncode == 4:
            return _not_found(nodes, output)
        if not junit.is_file():
            return [f"pytest 沒產出結果(結束代碼 {process.returncode}):{output[-2000:]}"]
        cases = list(ElementTree.parse(junit).iter("testcase"))  # noqa: S314 - 驗證器自己叫 pytest 寫在自建暫存目錄的檔
        problems = judge_junit(nodes, cases) + _xpassed(nodes, recorded)
    if process.returncode != 0 and not problems:
        problems.append(f"pytest 結束代碼 {process.returncode}:{output[-2000:]}")
    return problems


def _not_found(nodes: list[str], output: str) -> list[str]:
    """任一個編號找不到,pytest 回 4 而且整批都不跑;從它的輸出指出是哪一個。"""
    lines = [line.rstrip() for line in output.splitlines() if line.startswith("ERROR: not found:")]
    missing = [node for node in nodes if any(line.endswith(f"/{node}") for line in lines)]
    if missing:
        return [f"pytest 找不到證據節點(整批都沒跑):{node}" for node in missing]
    return [f"pytest 無法開始(結束代碼 4):{output[-2000:]}"]


# ── 輸出與命令列 ─────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit(root: Path) -> str:
    try:
        result = subprocess.run(  # 固定指令,只讀目前提交編號
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,  # noqa: S607
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return "無"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "無"


def _header(root: Path, claims_dir: Path, out: TextIO) -> None:
    print("宣稱驗證器", file=out)
    print(f"驗證器 sha256:{_sha256(Path(__file__))}", file=out)
    print("清單 sha256:", file=out)
    for path in sorted(claims_dir.glob("*.json")):
        print(f"  {path.name} {_sha256(path)}", file=out)
    print(f"提交編號:{_commit(root)}", file=out)


def verify(root: Path, claims_dir: Path, out: TextIO, *,
           timeout: float = TIMEOUT_SECONDS) -> int:
    _header(root, claims_dir, out)
    try:
        manifests, problems = check_manifests(root, claims_dir)
    except Undecidable as exc:
        print(f"無法判定:{exc}", file=out)
        return EXIT_UNDECIDABLE
    nodes = list(dict.fromkeys(e.node for m in manifests for e in m.evidence))
    if problems:
        print("前五步有擋下,沒跑證據測試", file=out)
    else:
        problems = run_evidence(root, nodes, timeout)
    if problems:
        print(f"擋下({len(problems)} 條原因):", file=out)
        for problem in problems:
            print(f"- {problem}", file=out)
        return EXIT_BLOCK
    print(f"通過:{len(manifests)} 條宣稱,跑了 {len(nodes)} 支證據測試全部通過", file=out)
    return EXIT_PASS


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        timeout: float = TIMEOUT_SECONDS) -> int:
    parser = argparse.ArgumentParser(description="依證據清單做機械檢查,自己跑證據測試")
    parser.add_argument("claims_dir", help="證據清單目錄(repo 根底下的 claims/)")
    stream = out or sys.stdout
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 參數錯也是「沒辦法開始判」
        return exc.code if isinstance(exc.code, int) else EXIT_UNDECIDABLE
    claims_dir = Path(args.claims_dir).resolve()
    if not claims_dir.is_dir():
        print(f"無法判定:找不到清單目錄 {args.claims_dir}", file=stream)
        return EXIT_UNDECIDABLE
    return verify(claims_dir.parent, claims_dir, stream, timeout=timeout)


def _stop_requested(_signum: int, _frame: object) -> None:
    raise SystemExit(EXIT_UNDECIDABLE)  # 跑到一半被停下:沒判完


def main(argv: list[str] | None = None) -> None:
    signal.signal(signal.SIGTERM, _stop_requested)
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
