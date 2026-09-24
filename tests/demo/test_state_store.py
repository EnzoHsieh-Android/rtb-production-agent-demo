"""展示狀態暫存資料庫(Phase 12 設計審 r1 a2):驅動執行緒寫,伺服器用唯讀開法讀;不自創檔案協定。"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from rtb.demo.state_store import (
    DecisionRow,
    ScenarioRun,
    StateReader,
    StateWriter,
)

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def writer(tmp_path):
    store = StateWriter(tmp_path / "state.db", "demo-1")
    yield store
    store.close()


def _reader(tmp_path):
    return StateReader(tmp_path / "state.db")


def test_a_scenario_run_is_recorded_from_start_to_finish(tmp_path, writer):
    writer.start_scenario("F1", T0)
    writer.finish_scenario("F1", "done", None, "照預期:只套用一次", T0 + timedelta(seconds=9))

    reader = _reader(tmp_path)
    try:
        assert reader.scenario_runs("demo-1") == (
            ScenarioRun("F1", "done", None, "照預期:只套用一次", T0, T0 + timedelta(seconds=9)),)
    finally:
        reader.close()


def test_every_decision_keeps_its_source_and_updates_the_current_node(tmp_path, writer):
    """每筆判斷帶來源;記一筆就同時更新「目前節點」與「上一個判斷」。"""
    writer.start_scenario("F1", T0)
    writer.record_decision("F1", DecisionRow("x_write", ("x_total", "x_write"), "EXECUTED",
                                             "檢查都通過,送去寫入", T0, "inbox.attempts#1"))
    writer.record_decision("F1", DecisionRow("x_unknown", ("p_reply", "x_unknown"), "UNKNOWN",
                                             "不知道平台有沒有寫進去", T0 + timedelta(seconds=1),
                                             "inbox.attempts#2"))

    reader = _reader(tmp_path)
    try:
        decisions = reader.decisions("demo-1", "F1")
        assert [d.node for d in decisions] == ["x_write", "x_unknown"]
        assert decisions[1].origin == "inbox.attempts#2"
        current = reader.current("demo-1")
        assert current is not None
        assert (current.scenario, current.node, current.entered_at) == (
            "F1", "x_unknown", T0 + timedelta(seconds=1))
        assert current.last_decision == decisions[1]
    finally:
        reader.close()


def test_a_decision_without_a_graph_edge_keeps_the_edge_empty(tmp_path, writer):
    writer.start_scenario("F2", T0)
    writer.record_decision("F2", DecisionRow("x_done", None, "VERIFIED", "比對過", T0, "x#1"))

    reader = _reader(tmp_path)
    try:
        assert reader.decisions("demo-1", "F2")[0].edge is None
    finally:
        reader.close()


def test_node_counts_replace_the_previous_snapshot(tmp_path, writer):
    writer.start_scenario("F7", T0)
    writer.set_node_counts("F7", {"x_wait_approval": 3, "x_done": 1})
    writer.set_node_counts("F7", {"x_wait_approval": 2, "x_done": 2})

    reader = _reader(tmp_path)
    try:
        assert reader.node_counts("demo-1", "F7") == (("x_done", 2), ("x_wait_approval", 2))
    finally:
        reader.close()


def test_the_reader_cannot_write(tmp_path, writer):
    writer.start_scenario("F1", T0)
    reader = _reader(tmp_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute("DELETE FROM scenario_runs")
    finally:
        reader.close()


def test_a_second_demo_in_the_same_file_is_kept_apart(tmp_path, writer):
    writer.start_scenario("F1", T0)
    other = StateWriter(tmp_path / "state.db", "demo-2")
    try:
        other.start_scenario("F1", T0)
        other.finish_scenario("F1", "incomplete", "行程起不來", None, T0)
    finally:
        other.close()

    reader = _reader(tmp_path)
    try:
        assert reader.scenario_runs("demo-1")[0].status == "running"
        assert reader.scenario_runs("demo-2")[0].reason == "行程起不來"
    finally:
        reader.close()
