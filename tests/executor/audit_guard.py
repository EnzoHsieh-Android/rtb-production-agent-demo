"""稽核表只增不改的判定(Phase 8 死信兩張表與 Phase 9 增量 4 七張表共用一套):哪幾張表受守護、
哪些語句算改寫、補欄位的規則、通用補欄位登記表的核對、動態表名補欄位的處數。兩支守衛測試
(稽核表、死信)都從這裡匯入;把拆開的字串拼回來用共用掃描模組。

比法是「同段共現」:同一段拼回來的文字裡出現任一張受守護表名,又出現任一種改寫語句就算違規。
防忘記,不防刻意繞過(用變數組表名、把關鍵字拆到兩個變數再串起來都繞得過)。
"""

import re

from tests.executor.write_scan import reconstructed_strings

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
# 表名是占位:拼回字串時具名佔位已正規化成裸 {} 或 %s(代碼審第 2 輪)
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
