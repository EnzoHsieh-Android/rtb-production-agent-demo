"""窗口讀取函式都走時間索引(Phase 9 增量 2):[S639]。

每張會被指標依時間窗查詢的表,由擁有它的模組提供「依時間窗列出」的讀取函式(名稱以 _between 結尾),
旁邊配一支同名加 _query 的函式回查詢語句,給這裡看查詢計畫。清單由原始碼機械找出:新加一支窗口讀取
函式卻沒登記它該用的索引,這裡就翻紅。
"""

import inspect
import io
import sqlite3
from datetime import timedelta

import pytest

from rtb.analyzer import task_store
from rtb.analyzer.task_store import TaskReader
from rtb.executor import attempt_store, inbox_store
from rtb.executor.inbox_store import ReadOnlyInbox
from rtb.ops import metrics
from rtb.sqlitekit import DatabaseNotUpgraded
from tests.ops.rows import Rows, at

EXPECTED_INDEX = {
    (inbox_store, "lifecycle_events_between"): "lifecycle_events_by_time",
    (attempt_store, "dsp_calls_between"): "dsp_calls_by_time",
    (attempt_store, "terminal_rows_between"): "attempts_terminal_by_time",
    (attempt_store, "unknown_rows_between"): "attempts_unknown_by_time",
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


# ---- [S639] 代碼審第 1 輪:每一支窗口讀取函式含起點、不含終點,時間沒帶時區就拒絕 ----
def test_window_readers_include_the_start_and_exclude_the_end(rows):
    for when, task in ((at(0), "start"), (at(minutes=60), "end")):
        rows.event(when, task, "handed_off")
        rows.dsp_call(when, "write", "responded", status=200, task=task)
        rows.attempts(f"k-{task}", [("in_flight", when - timedelta(seconds=1)), ("verified", when)],
                      task=task)
        rows.tool_call(when, "dsp:campaign", task=task)
        rows.attempts(f"u-{task}", [("in_flight", when - timedelta(seconds=1)), ("unknown", when)],
                      task=task)
    inbox = ReadOnlyInbox(rows.executor_db)
    try:
        with inbox.read_transaction() as tx:
            found = {
                "lifecycle_events_between": [e.task_id for e in inbox.lifecycle_events_between(
                    tx, at(0), at(minutes=60))],
                "dsp_calls_between": [c.task_id for c in attempt_store.dsp_calls_between(
                    tx, at(0), at(minutes=60))],
                "terminal_rows_between": [r.key.removeprefix("k-") for r in
                                          attempt_store.terminal_rows_between(
                                              tx, at(0), at(minutes=60))],
                "unknown_rows_between": [k.removeprefix("u-") for k in
                                         attempt_store.unknown_rows_between(
                                             tx, at(0), at(minutes=60))],
            }
    finally:
        inbox.close()
    reader = TaskReader(rows.analyzer_db)
    try:
        found["tool_calls_between"] = [c.task_id for c in reader.tool_calls_between(
            at(0), at(minutes=60))]
    finally:
        reader.close()

    assert set(found) == {name for _, name in EXPECTED_INDEX}
    assert found == {name: ["start"] for name in found}


def test_window_readers_refuse_times_without_a_time_zone():
    naive = at(0).replace(tzinfo=None)
    for module, name in EXPECTED_INDEX:
        query = getattr(module, f"{name}_query")
        for since, until in ((naive, at(minutes=60)), (at(0), naive.replace(hour=13))):
            with pytest.raises(ValueError, match="時區"):
                query(since, until)


# ---- [S619][S639] 代碼審第 1 輪:缺這一版新加的時間索引,唯讀開法視同還沒升級 ----
def test_a_read_only_open_without_the_time_indexes_counts_as_not_upgraded(tmp_path):
    config = tmp_path / "tenants.json"
    config.write_text('{"tenants": {"acme": {"campaigns": ["c1"], "max_budget": 100}}}')
    config.chmod(0o600)
    for index in sorted(set(EXPECTED_INDEX.values()) | {"lifecycle_events_terminal"}):
        home = tmp_path / index
        home.mkdir()
        built = Rows(home)
        analyzer_side = index == EXPECTED_INDEX[task_store, "tool_calls_between"]
        (built.analyzer if analyzer_side else built.executor).execute(f"DROP INDEX {index}")
        built.close()
        path = built.analyzer_db if analyzer_side else built.executor_db
        before = path.read_bytes()
        opener = TaskReader if analyzer_side else ReadOnlyInbox
        with pytest.raises(DatabaseNotUpgraded, match=index):
            opener(path)
        assert path.read_bytes() == before  # 唯讀開法不替它補索引
        code = metrics.run(["--executor-db", str(built.executor_db), "--analyzer-db",
                            str(built.analyzer_db), "--tenants-config", str(config),
                            "--since", at(0).isoformat(), "--until", at(minutes=60).isoformat()],
                           out=io.StringIO(), err=io.StringIO())
        assert code == metrics.EXIT_NOT_UPGRADED, index
