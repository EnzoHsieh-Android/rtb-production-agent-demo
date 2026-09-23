"""掃原始碼的共用工具,只放兩種通用讀法、名字分開:
- 機械判定「哪些函式會寫資料庫」:只取字串常數、不含文件字串。使用者:Phase 9 增量 1 [S621]、[S617]、
  [S600](收件口、嘗試紀錄的讀寫函式清單,維運套件邊界測試)。
- 把拆開的字串拼回來:相鄰字串、+、f-string、.format、% 拼成完整語句。使用者:稽核表守衛(經同目錄的
  稽核守衛模組)、Phase 8 死信測試(直接匯入,找「把死信改回待處理」那一句在哪個函式)。
稽核表只增不改的判定(哪幾張表、哪些語句算改寫)不在這裡,在同目錄的稽核守衛模組。

不靠名字判斷(取件、處理待核可、接手名字像讀、其實會寫):解析原始碼,一支函式的本體、它用到的
模組內字串常數、以及它呼叫的模組內函式(同一個類別與基底類別的方法、模組層函式,遞移)裡,只要有
寫入語句就算寫入函式。寫入語句是字串裡的 INSERT、UPDATE、DELETE、REPLACE、ALTER、CREATE、DROP
(專案的 SQL 一律大寫;文件字串不算)。跨模組的呼叫不追:各模組各自判定。
"""

import ast
import re
import string
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


_PERCENT_FIELD = re.compile(r"%(?:\((\w+)\))?([-#0 +]*\d*(?:\.\d+)?)([sdifrxXeEgGc%])")


def _format_call(template, node):
    """字面模板 .format(...):位置與具名佔位都認;對得上字面常數就代入,對不上或是變數就把那個佔位
    正規化成裸 {}(模板裡的改寫語句與表名照樣抓得到,寧可誤報)。格式規格帶精度(字串會被截斷)時
    不照規格截,代入完整常數:截掉的可能正是表名(代碼審第 3 輪)。"""
    positional = [_constant(arg) for arg in node.args]
    named = {kw.arg: _constant(kw.value) for kw in node.keywords if kw.arg is not None}
    out, auto = [], 0
    try:
        pieces = list(string.Formatter().parse(template))
    except ValueError:
        return template
    for literal, field, spec, _conversion in pieces:
        out.append(literal)
        if field is None:
            continue
        value = None
        if field == "":
            value = positional[auto] if auto < len(positional) else None
            auto += 1
        elif field.isdigit():
            index = int(field)
            value = positional[index] if index < len(positional) else None
        elif field.isidentifier():
            value = named.get(field)
        out.append(_formatted_value(value, spec or ""))
    return "".join(out)


def _formatted_value(value, spec):
    if value is None:
        return "{}"
    if "." in spec:  # 帶精度:不截,整段代入
        return str(value)
    try:
        return format(value, spec)
    except (TypeError, ValueError):
        return "{}"


def _percent(template, right):
    """字面模板 % 參數:逐一處理每個佔位。字典看名字、元組或單一值照順序;解得出字面常數的就代入,
    解不出的那一個正規化成裸 %s(不因為其中一個是變數就整段放棄代入,代碼審第 3 輪)。"""
    if isinstance(right, ast.Dict):
        mapping = {}
        for key, value in zip(right.keys, right.values, strict=True):
            name = _constant(key) if key is not None else None
            if isinstance(name, str):
                mapping[name] = _constant(value)
        sequence = None
    else:
        mapping = None
        sequence = [_constant(arg) for arg in (right.elts if isinstance(right, ast.Tuple)
                                              else [right])]
    position = 0

    def replace(match):
        nonlocal position
        name, flags, conversion = match.groups()
        if conversion == "%":
            return "%"
        if name is not None:
            value = None if mapping is None else mapping.get(name)
        else:
            value = (sequence[position] if sequence is not None and position < len(sequence)
                     else None)
            position += 1
        if value is None:
            return "%s"
        try:
            return ("%" + flags + conversion) % value
        except (TypeError, ValueError):
            return "%s"

    return _PERCENT_FIELD.sub(replace, template)


def _raw_text(node):  # noqa: PLR0911 - 每一種拼法一個出口
    """拼回來的原始字串(不壓空白):壓空白只在最外層做一次,不然 "UPDATE OR REPLACE " + "表 ..."
    兩側各自壓掉邊界空白會黏成一個字、躲過比對(Phase 9 增量 4 代碼審第 2 輪)。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}"
                       for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _raw_text(node.left), _raw_text(node.right)
        if left is None or right is None:  # 不是字串相加(例如數字)
            return None
        return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = _raw_text(node.left)
        return None if template is None else _percent(template, node.right)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        template = _raw_text(node.func.value)
        return None if template is None else _format_call(template, node)
    return None


def text_of(node):
    """把一段字串拼回來:相鄰字串、用 + 串起來的字串、f-string 的固定片段(代入的值記成 {})、
    字面模板 .format(位置或具名參數)、字面模板 % 參數(單一、元組或字典),最後整段壓一次空白。
    照專案一般寫法拆開的 SQL 也還原得回來;刻意用變數組表名之類的寫法還原不了,那是刻意繞過,
    不在「防忘記」的範圍(Phase 8 代碼審第 1 輪審查席實測兩種繞法;.format 與 % 是 Phase 9 增量 4
    代碼審第 1、2 輪補的)。"""
    raw = _raw_text(node)
    return None if raw is None else " ".join(raw.split())


def reconstructed_strings(tree, skip_docstrings=False):
    """一棵語法樹裡拼得回來的每一段字串(最外層的那段,不重複算它的組成片段)。skip_docstrings:
    不算模組、類別、函式的文件字串(說明裡提到 SQL 字樣不是語句)。"""
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp | ast.JoinedStr | ast.Call) and _raw_text(node) is not None:
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
