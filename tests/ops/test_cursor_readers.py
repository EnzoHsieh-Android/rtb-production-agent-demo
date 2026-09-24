"""游標式讀取(Phase 12 增量 1 第 2 輪代碼審 a1):一鍵展示的觀察器照每張表的列號往後讀新寫的列,
改經分析端與收件口既有的唯讀開法,不自己對別人的表下查詢。

讀取函式由擁有那張表的模組提供(名稱以 _after 結尾),旁邊配一支同名加 _query 的函式回查詢語句,
給這裡看查詢計畫:照列號往後讀,不整表掃。清單由原始碼機械找出。
"""

import inspect
import sqlite3

import pytest

from rtb.analyzer import task_store
from rtb.analyzer.task_store import ReplanReason, TaskReader
from rtb.domain.task_state import TaskState
from rtb.executor import attempt_store, inbox_store
from rtb.executor.inbox_store import ReadOnlyInbox
from tests.ops.rows import Rows, at

EXPECTED = {(task_store, "tasks_after"), (task_store, "follow_ups_after"),
            (inbox_store, "lifecycle_events_after"), (attempt_store, "trace_rows_after")}


def _readers():
    found = set()
    for module in (inbox_store, attempt_store, task_store):
        for name, member in inspect.getmembers(module):
            if name.endswith("_after") and inspect.isfunction(member):
                found.add((module, name))
            if inspect.isclass(member) and member.__module__ == module.__name__:
                found |= {(module, n) for n, _ in inspect.getmembers(member, inspect.isfunction)
                          if n.endswith("_after")}
    return found


@pytest.fixture
def rows(tmp_path):
    built = Rows(tmp_path)
    yield built
    built.close()


def test_every_cursor_reader_walks_the_row_number(rows):
    assert _readers() == EXPECTED
    for module, name in EXPECTED:
        sql, params = getattr(module, f"{name}_query")(3)
        path = rows.analyzer_db if module is task_store else rows.executor_db
        conn = sqlite3.connect(path)
        try:
            steps = [row[-1] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)]
        finally:
            conn.close()
        assert not [s for s in steps if s.startswith("SCAN") and "USING" not in s], (name, steps)


def test_task_rows_and_follow_ups_come_after_the_cursor_in_write_order(rows):
    for i in range(3):
        rows.task(f"t{i}", at(i))
    rows.follow_up("t0", "t0-next", at(5))
    rows.analyzer.execute(
        "INSERT INTO follow_ups (original_task_id, follow_up_task_id, generation, campaign_id, "
        "reason, outcome, written_at) VALUES ('t1', NULL, 4, 'c1', 'version_changed', "
        "'limit_reached', ?)", (at(6).isoformat(),))
    reader = TaskReader(rows.analyzer_db)
    try:
        first = reader.tasks_after(0)
        later = reader.tasks_after(first[0][0])
        follow_ups = reader.follow_ups_after(0)
    finally:
        reader.close()
    assert [row.task_id for _, row in first] == ["t0", "t1", "t2"]
    assert all(row.state is TaskState.RECEIVED for _, row in first)
    assert [row.task_id for _, row in later] == ["t1", "t2"]
    assert [(f.original_task_id, f.follow_up_task_id, f.reason) for f in follow_ups] == [
        ("t0", "t0-next", ReplanReason.VERSION_CHANGED),
        ("t1", None, ReplanReason.VERSION_CHANGED)]  # 代數用完:沒有開新工作


def test_lifecycle_events_and_attempts_come_after_the_cursor_with_who_wrote_them(rows):
    rows.event(at(0), "t1", "received", source="inbox", actor=None)
    rows.event(at(1), "t1", "delivered")
    rows.attempts("k1", [("in_flight", at(2)), ("unknown", at(3))], task="t1")
    inbox = ReadOnlyInbox(rows.executor_db)
    try:
        with inbox.read_transaction() as tx:
            events = inbox.lifecycle_events_after(tx, 0)
            after_first = inbox.lifecycle_events_after(tx, events[0].id)
            attempts = attempt_store.trace_rows_after(tx, 0)
            later = attempt_store.trace_rows_after(tx, attempts[0][0])
    finally:
        inbox.close()
    assert [(e.kind, e.source) for e in events] == [("received", "inbox"),
                                                    ("delivered", "executor_loop")]
    assert [e.kind for e in after_first] == ["delivered"]
    assert [(row.key, row.seq, row.state, row.source) for _, row in attempts] == [
        ("k1", 1, "in_flight", "executor_loop"), ("k1", 2, "unknown", "executor_loop")]
    assert [row.seq for _, row in later] == [2]
