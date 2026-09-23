"""掃原始碼的共用工具:機械判定「哪些函式會寫資料庫」(Phase 9 增量 1 [S621]、[S617]、[S600] 共用),
以及把拆開的字串拼回來(Phase 8 死信守衛與 Phase 9 增量 4 稽核表守衛共用)。兩種讀法並存、名字分開:
判寫入函式用的只取字串常數、不含文件字串;拼回字串的那支把相鄰字串、+、f-string 拼成完整語句。

不靠名字判斷(取件、處理待核可、接手名字像讀、其實會寫):解析原始碼,一支函式的本體、它用到的
模組內字串常數、以及它呼叫的模組內函式(同一個類別與基底類別的方法、模組層函式,遞移)裡,只要有
寫入語句就算寫入函式。寫入語句是字串裡的 INSERT、UPDATE、DELETE、REPLACE、ALTER、CREATE、DROP
(專案的 SQL 一律大寫;文件字串不算)。跨模組的呼叫不追:各模組各自判定。
"""

import ast
import re
from pathlib import Path

WRITE_SQL = r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|CREATE|DROP)\b"


def _strings(node):
    """節點裡的字串常數(含 f 字串的固定片段),不含函式與類別的文件字串。"""
    docstrings = set()
    for inner in ast.walk(node):
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = inner.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [inner.value for inner in ast.walk(node)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
            and id(inner) not in docstrings]


class _Module:
    def __init__(self, path: Path):
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.functions = {}  # 限定名 -> 函式節點
        self.classes = {}  # 類別名 -> (基底類別名清單, {方法名: 限定名})
        self.constants = {}  # 模組層名稱 -> 它的字串內容
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                methods = {}
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[item.name] = f"{node.name}.{item.name}"
                        self.functions[f"{node.name}.{item.name}"] = item
                self.classes[node.name] = (bases, methods)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and node.value is not None:
                        self.constants.setdefault(target.id, []).extend(_strings(node.value))

    def method(self, cls, name):
        """沿基底類別找方法(只找同一個模組裡定義的類別)。"""
        if cls not in self.classes:
            return None
        bases, methods = self.classes[cls]
        if name in methods:
            return methods[name]
        for base in bases:
            found = self.method(base, name)
            if found:
                return found
        return None

    def callees(self, qualname):
        node, cls = self.functions[qualname], qualname.rpartition(".")[0] or None
        found = set()
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in ("self", "cls") and cls):
                target = self.method(cls, func.attr)
                if target:
                    found.add(target)
            elif isinstance(func, ast.Name) and func.id in self.functions:
                found.add(func.id)
        return found

    def own_writes(self, qualname, pattern):
        node = self.functions[qualname]
        texts = _strings(node)
        for inner in ast.walk(node):  # 用到的模組層字串常數(例如建表語句)
            if isinstance(inner, ast.Name) and inner.id in self.constants:
                texts += self.constants[inner.id]
        return any(re.search(pattern, text) for text in texts)


def classify(path: Path, pattern: str = WRITE_SQL) -> dict[str, bool]:
    """每一支公開函式(模組層函式與公開類別的公開方法)是不是寫入函式:{限定名: 會寫}。"""
    module = _Module(path)
    memo: dict[str, bool] = {}

    def writes(qualname, seen):
        if qualname in memo:
            return memo[qualname]
        if qualname in seen:
            return False
        seen = seen | {qualname}
        result = module.own_writes(qualname, pattern) or any(
            writes(callee, seen) for callee in module.callees(qualname))
        memo[qualname] = result
        return result

    public = {}
    for qualname in module.functions:
        parts = qualname.split(".")
        if all(not part.startswith("_") for part in parts):
            public[qualname] = writes(qualname, frozenset())
    return public


def writes_matching(path: Path, pattern: str) -> set[str]:
    return {name for name, writes in classify(path, pattern).items() if writes}


def write_functions(path: Path) -> set[str]:
    return writes_matching(path, WRITE_SQL)


def read_functions(path: Path) -> set[str]:
    return {name for name, writes in classify(path).items() if not writes}


def text_of(node):
    """把一段字串拼回來:相鄰字串、用 + 串起來的字串、f-string 的固定片段(代入的值記成 {}),
    空白壓成一格。照專案一般寫法拆開的 SQL 也還原得回來;刻意用變數組表名之類的寫法還原不了,
    那是刻意繞過,不在「防忘記」的範圍(Phase 8 代碼審第 1 輪審查席實測兩種繞法)。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(part.value if isinstance(part, ast.Constant) else "{}"
                       for part in node.values)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = text_of(node.left), text_of(node.right)
        if left is None or right is None:  # 不是字串相加(例如數字)
            return None
        text = left + right
    else:
        return None
    return " ".join(text.split())


def reconstructed_strings(tree, skip_docstrings=False):
    """一棵語法樹裡拼得回來的每一段字串(最外層的那段,不重複算它的組成片段)。skip_docstrings:
    不算模組、類別、函式的文件字串(說明裡提到 SQL 字樣不是語句)。"""
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp | ast.JoinedStr) and text_of(node) is not None:
            inside.update(id(sub) for sub in ast.walk(node) if sub is not node)
    if skip_docstrings:
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value,
                                                                         ast.Constant):
                    inside.add(id(body[0].value))
    return [text for node in ast.walk(tree)
            if id(node) not in inside and (text := text_of(node)) is not None]
