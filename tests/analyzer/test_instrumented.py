"""tool_calls 只增不改的呼叫紀錄與 trace_for:S50、S51。"""

from rtb.analyzer.flow import Accepted
from rtb.analyzer.instrumented import InstrumentedEvidenceSource, InstrumentedSubmit
from rtb.analyzer.task_store import TaskRow, trace_for
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, make_evidence, make_proposal


def row(seq=2):
    return TaskRow(task_id="t1", seq=seq, state=TaskState.COLLECTING_EVIDENCE,
                   campaign_id="c1", proposal=None, error_detail=None, written_at=NOW)


# ---- S50 ----
def test_a_successful_call_is_recorded(store):
    store.create_task("t1", "c1", NOW)
    source = InstrumentedEvidenceSource(store, lambda _task: (make_evidence(),), "dsp:evidence")

    source(row())

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].outcome == "ok" and calls[0].endpoint == "dsp:evidence"


def test_a_failing_call_is_recorded_and_the_exception_still_propagates(store):
    import pytest

    store.create_task("t1", "c1", NOW)

    def boom(_task):
        raise RuntimeError("dsp unreachable")

    source = InstrumentedEvidenceSource(store, boom, "dsp:evidence")

    with pytest.raises(RuntimeError):
        source(row())

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].outcome == "RuntimeError"


def test_a_failing_tool_call_write_does_not_affect_the_wrapped_calls_own_result(store):
    store.create_task("t1", "c1", NOW)
    store.close()  # 之後任何一次 execute() 都會丟 sqlite3.ProgrammingError,模擬寫入紀錄本身壞掉
    source = InstrumentedEvidenceSource(store, lambda _task: (make_evidence(),), "dsp:evidence")

    result = source(row())  # record_tool_call 內部寫入壞掉,呼叫本身的結果不受影響

    assert result == (make_evidence(),)


def test_submit_calls_are_recorded_too(store):
    store.create_task("t1", "c1", NOW)
    submit = InstrumentedSubmit(store, lambda _p: Accepted(False), "inbox:submit")

    submit(make_proposal())

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].endpoint == "inbox:submit"


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
