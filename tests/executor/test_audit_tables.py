"""稽核表只增不改的機械守衛(Phase 9 增量 4):[S675]。

七張稽核表(停下紀錄、核可、核可使用、生命週期事件、DSP 呼叫紀錄、DSP 操作紀錄、嘗試紀錄)只准新增;
Phase 8 死信兩張表跟它們共用同一套判定(代碼審第 1 輪),判定本身在共用掃描模組。
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
from tests.executor.write_scan import (
    AUDITED,
    audit_violations,
    classify,
    dynamic_add_column_sites,
    reconstructed_strings,
    registry_violations,
)

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "rtb"
GUARDED = AUDITED  # 七張稽核表加 Phase 8 死信兩張表,共用同一套判定


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
    # 九張表共用一套(代碼審第 1 輪):清單少一張就等於那張沒人守
    assert set(GUARDED) == {"write_stops", "approvals", "approval_uses", "lifecycle_events",
                            "dsp_calls", "operations", "attempts", "dead_letters",
                            "dead_letter_ops"}
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


@pytest.mark.parametrize("source", [
    # 代碼審第 1 輪:字面模板 .format(字面參數) 與 字面 % 字面 也要拼得回來
    'x = "UPDATE {} SET status = 1".format("oper" "ations")',
    'x = "UPDATE %s SET status = 1" % "operations"',
    'x = "UPDATE %s SET status = %d" % ("operations", 1)',
    # 參數含變數時保守判定:模板本身有改寫語句與受守護表名就算
    'x = "UPDATE operations SET status = {}".format(value)',
    'x = "DELETE FROM operations WHERE id = %s" % value',
])
def test_the_audit_guard_sees_through_format_and_percent(source):
    assert audit_violations(ast.parse(source)) != []


def test_only_one_place_adds_columns_by_a_variable_table_name():
    """動態表名的補欄位只准一處(收件口的通用補欄位迴圈,守衛直接讀它的登記表);新開第二處就紅,
    逼人接進同一個登記表。"""
    sites = dynamic_add_column_sites(_trees())
    assert [name for name, _ in sites] == ["inbox_store.py"], sites
    probe = ast.parse(  # 殺傷力配方:另開登記表給操作紀錄補不可為空、帶預設值的欄位
        'EXTRA = {"operations": [("x", "x INTEGER NOT NULL DEFAULT 1")]}\n'
        'def migrate(conn):\n'
        '    for table, columns in EXTRA.items():\n'
        '        for _name, ddl in columns:\n'
        '            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")\n')
    assert len(dynamic_add_column_sites({**_trees(), pathlib.Path("probe.py"): probe})) == 2


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
