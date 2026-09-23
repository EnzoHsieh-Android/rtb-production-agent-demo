"""稽核表只增不改的機械守衛(Phase 9 增量 4):[S675]。

七張稽核表(停下紀錄、核可、核可使用、生命週期事件、DSP 呼叫紀錄、DSP 操作紀錄、嘗試紀錄)只准新增,
比照 Phase 8 死信兩張表那支結構檢查:全庫掃原始碼,把相鄰字串、用 + 串起來、f-string 的固定片段拼回
完整語句再比對。跟 Phase 8 那支不同的是比法:只要同一段拼回來的文字裡出現任一張表的表名,又出現任一種
改寫語句就算違規——SQLite 的「衝突時更新」把 UPDATE 跟表名隔開,UPDATE OR REPLACE 也不是動詞緊接表名。
補欄位只准可為空、沒有預設值的新欄位(帶預設值會讓舊列讀出新值,等於回頭改寫稽核意義)。收件口的
通用補欄位迴圈用 f-string 組表名、拼回來只剩占位,另外直接讀它的登記表核對。

防忘記,不防刻意繞過:用變數組表名、把關鍵字拆到兩個變數再串起來,都繞得過;直接開資料庫改也擋不住。
"""

import ast
import pathlib
import re

import pytest

from rtb.executor import inbox_store
from tests.executor.write_scan import classify, reconstructed_strings

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "rtb"
GUARDED = ("write_stops", "approvals", "approval_uses", "lifecycle_events", "dsp_calls",
           "operations", "attempts")
_TABLE = re.compile(r"(?<![\w.])(?:\w+\.)?(" + "|".join(GUARDED) + r")\b")
# 同一段裡出現任一個 UPDATE 就算:衝突時更新(ON CONFLICT … DO UPDATE)與 UPDATE OR 某動作都含這個字,
# 不必另列(變異檢查時另列的那一條拿掉照樣抓得到,是多餘的)
REWRITES = re.compile(
    r"\bUPDATE\b|\bDELETE\b|\bREPLACE\s+INTO\b|\bINSERT\s+OR\s+REPLACE\b|\bDROP\s+TABLE\b|"
    r"\bALTER\s+TABLE\s+\S+\s+(?:RENAME|DROP)\b", re.IGNORECASE)
ADD_COLUMN = re.compile(r"\bALTER\s+TABLE\s+(\S+)\s+ADD\s+COLUMN\s+([^;]*)", re.IGNORECASE)
LOOSENING = re.compile(r"\bNOT\s+NULL\b|\bDEFAULT\b", re.IGNORECASE)


def audit_violations(tree):
    """一棵語法樹裡違規的每一段文字(不含文件字串:說明裡提到改寫語句不是改寫)。"""
    found = []
    for text in reconstructed_strings(tree, skip_docstrings=True):
        if not _TABLE.search(text):
            continue
        if REWRITES.search(text):
            found.append(text)
        for table, definition in ADD_COLUMN.findall(text):
            if _TABLE.fullmatch(table) and LOOSENING.search(definition):
                found.append(text)
    return found


def registry_violations(registry):
    """通用補欄位迴圈的登記表:登記在七張表之下的欄位定義都要可為空、沒有預設值。"""
    return [(table, ddl) for table, columns in registry.items() if table in GUARDED
            for _name, ddl in columns if LOOSENING.search(ddl)]


def _trees():
    return {path: ast.parse(path.read_text(encoding="utf-8")) for path in SRC.rglob("*.py")}


# ---- [S675] ----
def test_audit_tables_are_only_ever_inserted_into():
    trees = _trees()
    offenders = [(path.name, text) for path, tree in trees.items()
                 for text in audit_violations(tree)]
    assert offenders == []
    assert registry_violations(inbox_store._ADDED_COLUMNS) == []
    assert set(inbox_store._ADDED_COLUMNS) & set(GUARDED) == {"attempts"}  # 登記表今天只有嘗試紀錄
    created = [m.group(1) for tree in trees.values()
               for text in reconstructed_strings(tree, skip_docstrings=True)
               for m in re.finditer(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", text)]
    for table in GUARDED:  # 表名拼錯守衛就守空了:每張都要在某個建表語句裡出現一次
        assert created.count(table) == 1, (table, created)


@pytest.mark.parametrize("source", [
    'x = "UPDATE write_" + "stops SET amount = 0"',
    'x = ("DELETE FROM app" "rovals WHERE 1")',
    'x = f"UPDATE approval_{kind} SET stage = ?"'.replace("{kind}", "uses"),
    'x = "INSERT INTO lifecycle_" "events (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = 2"',
    'x = "UPDATE OR REPLACE dsp_" + "calls SET status = 1"',
    'x = "DROP TABLE IF EXISTS oper" "ations"',
    'x = "REPLACE INTO main.att" "empts VALUES (1)"',
    'x = "INSERT OR REPLACE INTO att" + "empts VALUES (1)"',
    'x = "ALTER TABLE approvals RENAME TO " "old_approvals"',
    'x = "ALTER TABLE attempts DROP " "COLUMN tenant"',
    'x = "ALTER TABLE approvals ADD COLUMN revoked INTEGER NOT NULL " "DEFAULT 1"',
    'x = "ALTER TABLE lifecycle_events ADD COLUMN flag TEXT DEF" "AULT \'y\'"',
])
def test_the_audit_guard_catches_each_rewrite(source):
    """殺傷力配方:每張表至少一條,另外逐一涵蓋各種改寫寫法;拆開字串的寫法,不跟檢查用同一種字面。"""
    assert audit_violations(ast.parse(source)) != []


def test_the_audit_guard_allows_a_plain_new_column_and_other_tables():
    allowed = ('a = "ALTER TABLE operations ADD COLUMN policy_version TEXT"\n'
               'b = "UPDATE proposals SET disposition = NULL"\n'
               'c = "INSERT INTO attempts (key) VALUES (?)"\n'
               'd = "UPDATE campaigns SET budget = ?"\n')
    assert audit_violations(ast.parse(allowed)) == []


def test_the_column_registry_is_checked_for_defaults():
    assert registry_violations({"attempts": [("x", "x INTEGER NOT NULL DEFAULT 1")]}) != []
    assert registry_violations({"approvals": [("y", "y TEXT DEFAULT 'n'")]}) != []
    others = {"attempts": [("z", "z TEXT")], "other": [("w", "w TEXT DEFAULT 1")]}
    assert registry_violations(others) == []


def test_the_shared_scanner_keeps_both_string_readings(tmp_path):
    """共用掃描模組兩種讀法並存:判寫入函式的那支不把文件字串裡的 SQL 字樣當寫入;拼回字串的那支
    把相鄰字串、+、f-string 三種拆法都拼得回來。"""
    module = tmp_path / "probe.py"
    module.write_text('def reads():\n    """這裡提到 UPDATE attempts 只是說明。"""\n'
                      '    return 1\n\n\n'
                      'def writes(conn):\n    conn.execute("DELETE FROM x")\n', encoding="utf-8")
    assert classify(module) == {"reads": False, "writes": True}
    tree = ast.parse('x = "UPDATE dead_" + "letters SET a = 1"\n'
                     'y = f"DELETE   FROM {t} WHERE 1"\n'
                     'z = ("SET disposition = NULL, dead_letter_" "reason = NULL")\n')
    found = reconstructed_strings(tree)
    assert "UPDATE dead_letters SET a = 1" in found
    assert "DELETE FROM {} WHERE 1" in found
    assert any("dead_letter_reason = NULL" in text for text in found)
