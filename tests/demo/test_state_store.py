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


def test_a_confirmation_request_keeps_every_source_the_server_signs_from(tmp_path, writer):
    """[S1033] 簽一張確認要的全部來源由驅動程式記進展示狀態,伺服器從這裡讀,不經表單。"""
    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest(
        task_id="t007", revision=1, proposal_hash="h" * 64, tenant_config="/root/F7/tenants.json",
        stage="aggregate_limit_reached", max_increase=10, decision_expires_at=T0,
        numbers=(("金額", "100 → 110"), ("關卡", "全部加起來超過總上限"), ("租戶", "t-default")))
    writer.start_scenario("F7", T0)
    writer.set_confirmation("F7", request)

    reader = _reader(tmp_path)
    try:
        assert reader.confirmation("demo-1") == ("F7", request)
    finally:
        reader.close()
    writer.clear_confirmation()
    reader = _reader(tmp_path)
    try:
        assert reader.confirmation("demo-1") is None
    finally:
        reader.close()


# ---- 增量 2b:判斷的根據、誰判的、操作鍵;情境的細節 ----
def test_a_decision_keeps_its_basis_actor_and_operation_key(tmp_path, writer):
    """每筆判斷可帶多組根據(量到的值、標準、結論、這組根據從哪來)、誰判的、關聯的操作鍵;沒有的留空。"""
    from rtb.demo.state_store import Basis

    basis = (Basis("花了預算的 0.2%", "預期至少花 2.1%(一天的 1/24 乘 0.5)", "花太慢",
                   "依存下的證據重算"),)
    writer.start_scenario("F1", T0)
    writer.record_decision("F1", DecisionRow("a_pacing", None, "x", "y", T0, "o", basis=basis,
                                             operation_key="k1", actor="程式"))
    writer.record_decision("F1", DecisionRow("x_write", None, "x", "y", T0, "o2"))

    reader = _reader(tmp_path)
    try:
        first, second = reader.decisions("demo-1", "F1")
    finally:
        reader.close()
    assert first.basis == basis and first.operation_key == "k1" and first.actor == "程式"
    assert second.basis == () and second.operation_key is None


def test_scenario_details_are_kept_and_missing_ones_stay_empty(tmp_path, writer):
    from rtb.demo.state_store import ChangeRecord, ScenarioDetails

    details = ScenarioDetails(
        trigger="展示驅動程式直接建立工作", goal="加一成", queue_wait_seconds=0,
        injected_faults=(("x_write", "寫進平台後執行端當場倒下"),), operation_key="k1",
        platform_apply_count=1,
        change=ChangeRecord("c1", before=100, after=110, written=True, reason=None),
        change_overview="放行 123 個、人工確認後寫入 1 個、沒寫入 176 個;加了 1240,總上限 1234",
        platform=(("c1", 110, 2, "active"),), platform_operations=("#1 c1 加預算 → 110",),
        audit=("demo-operator 重新送入",), dispositions=(("擋下原因", "version_changed", "x"),))
    writer.start_scenario("F2", T0)
    writer.set_scenario_details("F2", details)
    writer.start_scenario("F3", T0)

    reader = _reader(tmp_path)
    try:
        assert reader.scenario_details("demo-1", "F2") == details
        assert reader.scenario_details("demo-1", "F3") is None
    finally:
        reader.close()


# ---- 代碼審 r1(Phase 12 增量 2)----
def _request():
    from rtb.demo.state_store import ConfirmationRequest

    return ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                               datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))


def test_a_failed_signing_leaves_the_confirmation_window_open(tmp_path, writer):
    """[代碼審 r1 x3] 簽發跟確認窗在同一個交易:簽的過程出錯整個退回,窗照樣開著、可以再試;簽成功
    之後同一個窗不能再簽,關窗時回報有人簽過。"""
    from rtb.demo.state_store import ALREADY_CONFIRMED, ConfirmationClosed

    writer.set_confirmation("F7", _request(), datetime.now(UTC) + timedelta(minutes=5))

    def broken(_code, _request):
        raise RuntimeError("收件口寫不進去")

    with pytest.raises(RuntimeError):
        writer.answer_confirmation(datetime.now(UTC), broken)
    reader = _reader(tmp_path)
    try:
        assert reader.confirmation("demo-1") is not None
    finally:
        reader.close()
    signed = []
    writer.answer_confirmation(datetime.now(UTC), lambda code, _req: signed.append(code))
    with pytest.raises(ConfirmationClosed) as again:
        writer.answer_confirmation(datetime.now(UTC), lambda code, _req: signed.append(code))
    assert again.value.code == ALREADY_CONFIRMED and signed == ["F7"]
    assert writer.clear_confirmation() is True


def test_a_confirmation_past_its_window_is_refused(writer):
    from rtb.demo.state_store import CONFIRMATION_TIMED_OUT, ConfirmationClosed

    writer.set_confirmation("F7", _request(), datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(ConfirmationClosed) as closed:
        writer.answer_confirmation(datetime.now(UTC), lambda _code, _req: None)
    assert closed.value.code == CONFIRMATION_TIMED_OUT
    assert writer.clear_confirmation() is False


def test_an_older_state_database_gets_the_task_column(tmp_path):
    """[代碼審 r1 d9] 之前版本建的展示狀態庫沒有判斷紀錄的工作欄:打開時補上,舊列讀回來工作
    是空的。"""
    from rtb.demo.state_store import DecisionRow, StateWriter

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, demo_id TEXT NOT NULL, "
        "code TEXT NOT NULL, node TEXT NOT NULL, edge_json TEXT, outcome TEXT NOT NULL, "
        "reason TEXT NOT NULL, at TEXT NOT NULL, origin TEXT NOT NULL, "
        "basis_json TEXT NOT NULL DEFAULT '[]', operation_key TEXT, "
        "actor TEXT NOT NULL DEFAULT '程式')")
    conn.execute("INSERT INTO decisions (demo_id, code, node, outcome, reason, at, origin) "
                 "VALUES ('d', 'F1', 'a_collect', 'o', 'r', ?, 's')",
                 (datetime.now(UTC).isoformat(),))
    conn.commit()
    conn.close()
    writer = StateWriter(path, "d")
    writer.record_decision("F1", DecisionRow("x_pick", None, "o", "r", datetime.now(UTC), "s",
                                             task="t9"))
    from rtb.demo.state_store import StateReader

    reader = StateReader(path)
    try:
        assert [row.task for row in reader.decisions("d", "F1")] == [None, "t9"]
    finally:
        reader.close()
