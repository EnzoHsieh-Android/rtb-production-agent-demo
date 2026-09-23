"""窗口讀取函式都走時間索引(Phase 9 增量 2):[S639]。

每張會被指標依時間窗查詢的表,由擁有它的模組提供「依時間窗列出」的讀取函式(名稱以 _between 結尾),
旁邊配一支同名加 _query 的函式回查詢語句,給這裡看查詢計畫。清單由原始碼機械找出:新加一支窗口讀取
函式卻沒登記它該用的索引,這裡就翻紅。
"""

import inspect
import sqlite3

import pytest

from rtb.analyzer import task_store
from rtb.executor import attempt_store, inbox_store
from tests.ops.rows import Rows, at

EXPECTED_INDEX = {
    (inbox_store, "lifecycle_events_between"): "lifecycle_events_by_time",
    (attempt_store, "dsp_calls_between"): "dsp_calls_by_time",
    (attempt_store, "terminal_rows_between"): "attempts_terminal_by_time",
    (task_store, "tool_calls_between"): "tool_calls_by_time",
}


def _readers():
    found = set()
    for module in (inbox_store, attempt_store, task_store):
        for name, member in inspect.getmembers(module):
            if name.endswith("_between") and inspect.isfunction(member):
                found.add((module, name))
            if inspect.isclass(member) and member.__module__ == module.__name__:
                found |= {(module, n) for n, _ in inspect.getmembers(member, inspect.isfunction)
                          if n.endswith("_between")}
    return found


@pytest.fixture
def rows(tmp_path):
    built = Rows(tmp_path)
    yield built
    built.close()


# ---- [S639] ----
def test_every_window_reader_uses_its_time_index(rows):
    assert _readers() == set(EXPECTED_INDEX)
    for (module, name), index in EXPECTED_INDEX.items():
        sql, params = getattr(module, f"{name}_query")(at(0), at(minutes=60))
        path = rows.analyzer_db if module is task_store else rows.executor_db
        conn = sqlite3.connect(path)
        try:
            steps = [row[-1] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)]
        finally:
            conn.close()
        assert any(f"INDEX {index}" in step for step in steps), (name, steps)
        assert not [s for s in steps if s.startswith("SCAN") and "USING" not in s], (name, steps)
