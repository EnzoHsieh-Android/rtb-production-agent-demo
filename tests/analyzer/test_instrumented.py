"""tool_calls 只增不改的呼叫紀錄與 trace_for:S50、S51。"""

import threading

import pytest

from rtb.analyzer import dsp_client
from rtb.analyzer.flow import Accepted
from rtb.analyzer.instrumented import (
    InstrumentedEvidenceSource,
    InstrumentedSubmit,
    dsp_evidence_source,
)
from rtb.analyzer.task_store import TaskRow, trace_for
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from tests.analyzer.conftest import NOW, make_evidence, make_proposal


def row(seq=2):
    return TaskRow(task_id="t1", seq=seq, state=TaskState.COLLECTING_EVIDENCE,
                   campaign_id="c1", proposal=None, error_detail=None, written_at=NOW)


# ---- S50 ----
def test_a_successful_call_is_recorded(store):
    store.create_task("t1", "c1", NOW)
    source = InstrumentedEvidenceSource(
        store, lambda _task, _now: (make_evidence(),), "dsp:evidence")

    source(row(), NOW)

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].outcome == "ok" and calls[0].endpoint == "dsp:evidence"


def test_a_failing_call_is_recorded_and_the_exception_still_propagates(store):
    store.create_task("t1", "c1", NOW)

    def boom(_task, _now):
        raise RuntimeError("dsp unreachable")

    source = InstrumentedEvidenceSource(store, boom, "dsp:evidence")

    with pytest.raises(RuntimeError):
        source(row(), NOW)

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].outcome == "RuntimeError"


def test_a_failing_tool_call_write_does_not_affect_the_wrapped_calls_own_result(store):
    store.create_task("t1", "c1", NOW)
    store.close()  # 之後任何一次 execute() 都會丟 sqlite3.ProgrammingError,模擬寫入紀錄本身壞掉
    source = InstrumentedEvidenceSource(
        store, lambda _task, _now: (make_evidence(),), "dsp:evidence")

    result = source(row(), NOW)  # record_tool_call 內部寫入壞掉,呼叫本身的結果不受影響

    assert result == (make_evidence(),)


def test_submit_calls_are_recorded_too(store):
    store.create_task("t1", "c1", NOW)
    submit = InstrumentedSubmit(store, lambda _p: Accepted(False), "inbox:submit", row())

    submit(make_proposal())

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].endpoint == "inbox:submit"


def test_a_failing_submit_call_is_recorded_and_the_exception_still_propagates(store):
    store.create_task("t1", "c1", NOW)

    def boom(_p):
        raise RuntimeError("inbox unreachable")

    submit = InstrumentedSubmit(store, boom, "inbox:submit", row())

    with pytest.raises(RuntimeError):
        submit(make_proposal())

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].outcome == "RuntimeError"


def test_submit_records_the_task_seq_bound_at_construction_not_whatever_is_latest_when_it_finishes(
    store,
):
    """`InstrumentedSubmit` 曾經在呼叫完成後才用 `store.latest()` 回頭查序號——並行的另一次
    `advance()` 若已經把任務推進到下一列,查到的就是錯的 task_seq。改成綁定建構時傳入的
    `task` 快照之後,就算包住的呼叫本身讓任務推進了,記錄的序號還是呼叫發生當下那一列。"""
    store.create_task("t1", "c1", NOW)
    store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, NOW)  # 建出合法的序號 2
    called_at_row = row(seq=2)  # 呼叫「發生當下」讀到的那一列快照

    def advance_task_during_the_call(_p):
        # 模擬提交進行中,另一次並行的 advance() 已經把任務推進到下一列(seq 變成 3)
        store.commit_step("t1", 2, TaskState.ANALYZING, NOW)
        return Accepted(False)

    submit = InstrumentedSubmit(store, advance_task_during_the_call, "inbox:submit",
                                called_at_row)

    submit(make_proposal())

    calls = store.list_tool_calls("t1")
    assert calls[0].task_seq == 2  # 不是完成後查到的「目前最新」(那會是 3)


# ---- dsp_evidence_source:兩個內部端點各自記一筆,不是整個 fetch() 才記一筆 ----
def test_dsp_evidence_source_records_one_call_per_endpoint_on_full_success(store, tmp_path):
    dsp_store = CampaignStore(tmp_path / "dsp.db")
    dsp_store.seed_campaign("c1", budget=100)
    dsp_store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=1.0,
                           revenue=3.0)
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        store.create_task("t1", "c1", NOW)
        source = dsp_evidence_source(
            store, f"http://127.0.0.1:{server.server_address[1]}", timeout_seconds=3)

        source(row(), NOW)

        calls = store.list_tool_calls("t1")
        endpoints = {c.endpoint for c in calls}
        assert len(calls) == 2  # 兩個端點各記一筆,不是整包只記一筆
        assert endpoints == {"dsp:campaign", "dsp:metrics"}
        assert all(c.outcome == "ok" for c in calls)
    finally:
        server.shutdown()
        server.server_close()


def test_dsp_evidence_source_still_records_the_first_endpoints_success_when_the_second_fails(
    store, tmp_path,
):
    """兩個端點各自記錄的重點:第一個(現況)成功、第二個(指標)失敗時,第一筆成功的紀錄
    不能因為整體呼叫最終丟例外就消失不見——這正是代碼審第 1 輪指出、舊版「包整個 fetch()」
    的寫法會遺失的那一筆。"""
    dsp_store = CampaignStore(tmp_path / "dsp.db")
    dsp_store.seed_campaign("c1", budget=100)  # 故意不 seed_metrics
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        store.create_task("t1", "c1", NOW)
        source = dsp_evidence_source(
            store, f"http://127.0.0.1:{server.server_address[1]}", timeout_seconds=3)

        with pytest.raises(dsp_client.DspRequestFailed):
            source(row(), NOW)

        calls = store.list_tool_calls("t1")
        by_endpoint = {c.endpoint: c.outcome for c in calls}
        assert by_endpoint["dsp:campaign"] == "ok"  # 第一個端點的成功紀錄還在
        assert by_endpoint["dsp:metrics"] == "DspRequestFailed"
    finally:
        server.shutdown()
        server.server_close()


# ---- S51 ----
def test_trace_for_returns_the_full_history_evidence_and_calls_for_a_task(store):
    store.create_task("t1", "c1", NOW)
    store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, NOW)
    store.commit_step("t1", 2, TaskState.ANALYZING, NOW, evidence=(make_evidence(),))
    store.record_tool_call("t1", 2, "dsp:evidence", "ok", 12.5, NOW)

    trace = trace_for(store, "t1")

    assert len(trace.tasks) == 3
    assert len(trace.evidence) == 1
    assert len(trace.tool_calls) == 1
    assert trace.tasks[-1].state is TaskState.ANALYZING


def test_trace_for_an_unknown_task_returns_empty_everything(store):
    trace = trace_for(store, "ghost")

    assert trace.tasks == () and trace.evidence == () and trace.tool_calls == ()
