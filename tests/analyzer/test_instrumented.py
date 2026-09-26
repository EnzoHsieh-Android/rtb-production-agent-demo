"""tool_calls 只增不改的呼叫紀錄與 trace_for:S50、S51。"""

import contextlib
import threading

import pytest

from rtb.analyzer import dsp_client
from rtb.analyzer.flow import Accepted, DspOperation
from rtb.analyzer.instrumented import (
    InstrumentedOperationLookup,
    InstrumentedSubmit,
    rule_source,
)
from rtb.analyzer.task_store import TaskRow, ToolEndpoint, trace_for
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from tests.analyzer.conftest import NOW, make_evidence, make_proposal


def row(seq=2):
    return TaskRow(task_id="t1", seq=seq, state=TaskState.COLLECTING_EVIDENCE,
                   campaign_id="c1", proposal=None, error_detail=None, written_at=NOW)


# ---- S50 ----
# (代碼審 r2 協調者裁定:整包只記一筆的 InstrumentedEvidenceSource 沒有正式入口、已刪;證據來源這三條
# 改走正式入口 rule_source 的 A 步,送件那兩條照舊測 InstrumentedSubmit)
@pytest.fixture
def dsp(tmp_path):
    """不開故障注入的模擬 DSP,c1 預算 100、有 1 小時指標(同目錄 test_dsp_client.py 的寫法)。"""
    store = CampaignStore(tmp_path / "dsp.db")
    store.seed_campaign("c1", budget=100)
    store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=1.0,
                       revenue=3.0)
    store.close()
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_successful_call_is_recorded(store, dsp):
    """規則輪 A 的基本讀取:兩個內部端點各自記一筆成功,不是整包只記一筆。"""
    store.create_task("t1", "c1", NOW)
    rule_source(store, dsp, 3)(row(), NOW)

    calls = store.list_tool_calls("t1")
    assert len(calls) == 2
    assert {c.endpoint for c in calls} == {"dsp:campaign", "dsp:metrics"}
    assert all(c.outcome == "ok" for c in calls)


def test_a_failing_call_is_recorded_and_the_exception_still_propagates(store):
    """DSP 連不上:這次讀取記一筆失敗,例外照樣往外丟(這一步不寫、下次重試)。"""
    store.create_task("t1", "c1", NOW)

    with pytest.raises(OSError):
        rule_source(store, "http://127.0.0.1:1", 1)(row(), NOW)

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].endpoint == "dsp:campaign" and calls[0].outcome != "ok"


def test_a_failing_tool_call_write_does_not_affect_the_wrapped_calls_own_result(store, dsp):
    """呼叫紀錄的寫入本身壞掉(這裡直接拿掉紀錄表),證據照樣讀到、跟紀錄正常時一樣。"""
    store.create_task("t1", "c1", NOW)
    expected = rule_source(store, dsp, 3)(row(), NOW)
    store._conn.execute("DROP TABLE tool_calls")

    result = rule_source(store, dsp, 3)(row(), NOW)

    assert result.evidence and result.evidence == expected.evidence


def test_submit_calls_are_recorded_too(store):
    store.create_task("t1", "c1", NOW)
    submit = InstrumentedSubmit(store, lambda _p: Accepted(False), ToolEndpoint.INBOX_SUBMIT, row())

    submit(make_proposal())

    calls = store.list_tool_calls("t1")
    assert len(calls) == 1 and calls[0].endpoint == "inbox:submit"


def test_a_failing_submit_call_is_recorded_and_the_exception_still_propagates(store):
    store.create_task("t1", "c1", NOW)

    def boom(_p):
        raise RuntimeError("inbox unreachable")

    submit = InstrumentedSubmit(store, boom, ToolEndpoint.INBOX_SUBMIT, row())

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

    submit = InstrumentedSubmit(store, advance_task_during_the_call,
                                ToolEndpoint.INBOX_SUBMIT,
                                called_at_row)

    submit(make_proposal())

    calls = store.list_tool_calls("t1")
    assert calls[0].task_seq == 2  # 不是完成後查到的「目前最新」(那會是 3)


# ---- 規則輪 A 的兩個內部端點各自記錄:第二個失敗時第一筆成功照樣留著 ----
def test_rule_step_a_still_records_the_first_endpoints_success_when_the_second_fails(
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
        source = rule_source(store, f"http://127.0.0.1:{server.server_address[1]}", 3)

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
    store.record_tool_call("t1", 2, ToolEndpoint.DSP_EVIDENCE, "ok", 12.5, NOW)

    trace = trace_for(store, "t1")

    assert len(trace.tasks) == 3
    assert len(trace.evidence) == 1
    assert len(trace.tool_calls) == 1
    assert trace.tasks[-1].state is TaskState.ANALYZING


def test_trace_for_an_unknown_task_returns_empty_everything(store):
    trace = trace_for(store, "ghost")

    assert trace.tasks == () and trace.evidence == () and trace.tool_calls == ()


# ---- S318 的呼叫紀錄那一半:依冪等鍵查 DSP 也進呼叫紀錄 ----
@pytest.mark.parametrize(("inner", "outcome"), [
    (lambda _key: None, "not_found"), (lambda _key: 1 / 0, "ZeroDivisionError"),
    (lambda _key: DspOperation("c1", "update_budget", 150, 1), "ok")])
def test_operation_lookups_are_recorded_in_the_trace(store, inner, outcome):
    store.create_task("t1", "c1", NOW)
    lookup = InstrumentedOperationLookup(store, inner, ToolEndpoint.DSP_OPERATION, row())

    with contextlib.suppress(ZeroDivisionError):
        lookup("k1-abc")

    calls = trace_for(store, "t1").tool_calls
    assert [(c.endpoint, c.outcome, c.task_seq) for c in calls] == [("dsp:operation", outcome, 2)]
