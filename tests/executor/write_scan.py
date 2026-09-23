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


def _constant(node):
    """字面常數(字串或數字)的值;不是字面常數回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str | int | float):
        return node.value
    return None


def _formatted(template, args, apply):
    """模板代入參數:參數全是字面常數就代入拼回;含變數時保守回模板本身(模板裡有改寫語句與受守護
    表名照樣抓得到,寧可誤報)。"""
    values = [_constant(arg) for arg in args]
    if any(value is None for value in values):
        return template
    try:
        return apply(template, values)
    except (IndexError, KeyError, TypeError, ValueError):
        return template


def text_of(node):
    """把一段字串拼回來:相鄰字串、用 + 串起來的字串、f-string 的固定片段(代入的值記成 {})、
    字面模板 .format(參數)、字面模板 % 參數,空白壓成一格。照專案一般寫法拆開的 SQL 也還原得回來;
    刻意用變數組表名之類的寫法還原不了,那是刻意繞過,不在「防忘記」的範圍(Phase 8 代碼審第 1 輪
    審查席實測兩種繞法;.format 與 % 是 Phase 9 增量 4 代碼審第 1 輪補的)。"""
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
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = text_of(node.left)
        if template is None:
            return None
        args = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        text = _formatted(template, args, lambda t, v: t % tuple(v))
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr == "format" and not node.keywords):
        template = text_of(node.func.value)
        if template is None:
            return None
        text = _formatted(template, node.args, lambda t, v: t.format(*v))
    else:
        return None
    return " ".join(text.split())


def reconstructed_strings(tree, skip_docstrings=False):
    """一棵語法樹裡拼得回來的每一段字串(最外層的那段,不重複算它的組成片段)。skip_docstrings:
    不算模組、類別、函式的文件字串(說明裡提到 SQL 字樣不是語句)。"""
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp | ast.JoinedStr | ast.Call) and text_of(node) is not None:
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


# ---- 稽核表只增不改(Phase 8 死信兩張表與 Phase 9 增量 4 七張表共用一套) ----
AUDITED = ("write_stops", "approvals", "approval_uses", "lifecycle_events", "dsp_calls",
           "operations", "attempts", "dead_letters", "dead_letter_ops")
NO_ALTER = frozenset({"dead_letters", "dead_letter_ops"})  # Phase 8:連可為空的補欄位都不准
_REWRITES = re.compile(
    # 同一段裡出現任一個 UPDATE 就算:衝突時更新(ON CONFLICT … DO UPDATE)與 UPDATE OR 某動作
    # 都含這個字,不必另列(Phase 9 增量 4 變異檢查:另列的那一條拿掉照樣抓得到)
    r"\bUPDATE\b|\bDELETE\b|\bREPLACE\s+INTO\b|\bINSERT\s+OR\s+REPLACE\b|\bDROP\s+TABLE\b|"
    r"\bALTER\s+TABLE\s+\S+\s+(?:RENAME|DROP)\b", re.IGNORECASE)
_ADD_COLUMN = re.compile(r"\bALTER\s+TABLE\s+(\S+)\s+ADD\s+COLUMN\s+([^;]*)", re.IGNORECASE)
_LOOSENING = re.compile(r"\bNOT\s+NULL\b|\bDEFAULT\b", re.IGNORECASE)
_DYNAMIC_ADD_COLUMN = re.compile(r"\bALTER\s+TABLE\s+(?:\{\}|%s)\s+ADD\s+COLUMN\b",
                                 re.IGNORECASE)


def _table_pattern(tables):
    return re.compile(r"(?<![\w.])(?:\w+\.)?(" + "|".join(tables) + r")\b")


def audit_violations(tree, tables=AUDITED, skip_docstrings=True):
    """違規的每一段文字:同一段拼回來的文字裡出現任一張表名,又出現任一種改寫語句;或對這些表補
    不可為空、帶預設值的欄位(死信兩張表連補欄位都不准)。比法是「同段共現」,不是動詞緊接表名:
    SQLite 的衝突時更新把 UPDATE 跟表名隔開,UPDATE OR REPLACE 也不是動詞緊接表名。"""
    table = _table_pattern(tables)
    found = []
    for text in reconstructed_strings(tree, skip_docstrings=skip_docstrings):
        if not table.search(text):
            continue
        if _REWRITES.search(text):
            found.append(text)
            continue
        for target, definition in _ADD_COLUMN.findall(text):
            named = table.fullmatch(target)
            if named and (named.group(1) in NO_ALTER or _LOOSENING.search(definition)):
                found.append(text)
    return found


def registry_violations(registry, tables=AUDITED):
    """通用補欄位迴圈的登記表:登記在受守護表之下的欄位定義都要可為空、沒有預設值;死信兩張表
    根本不准登記。"""
    return [(name, ddl) for name, columns in registry.items() if name in tables
            for _column, ddl in columns if name in NO_ALTER or _LOOSENING.search(ddl)]


def dynamic_add_column_sites(trees):
    """用變數組表名補欄位的每一處(檔名, 那段文字):表名拼回來只剩占位,守衛看不出是哪張表,
    所以只准一處、而且守衛直接讀它的登記表。"""
    return [(path.name, text) for path, tree in trees.items()
            for text in reconstructed_strings(tree, skip_docstrings=True)
            if _DYNAMIC_ADD_COLUMN.search(text)]
