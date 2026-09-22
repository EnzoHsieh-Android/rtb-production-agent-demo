"""真的 DSP 用戶端(EvidenceSource):S42~S44。"""

import threading
from datetime import UTC, datetime

import pytest

from rtb.analyzer import dsp_client
from rtb.analyzer.task_store import TaskRow
from rtb.domain.evidence import EvidenceKind
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def make_row(task_id="t1", seq=2, campaign_id="c1"):
    from datetime import UTC, datetime

    return TaskRow(task_id=task_id, seq=seq, state=TaskState.COLLECTING_EVIDENCE,
                   campaign_id=campaign_id, proposal=None, error_detail=None,
                   written_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC))


@pytest.fixture
def dsp(tmp_path):
    store = CampaignStore(tmp_path / "dsp.db")
    store.seed_campaign("c1", budget=100)
    store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=2.0,
                       revenue=5.0)
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# ---- S42 ----
def test_both_endpoints_succeeding_returns_both_pieces_of_evidence(dsp):
    evidence = dsp_client.make_client(dsp, timeout_seconds=3)(make_row(), NOW)

    kinds = {item.kind for item in evidence}
    assert kinds == {EvidenceKind.CAMPAIGN_STATE, EvidenceKind.METRICS}
    assert len(evidence) == 2


def test_the_campaign_state_evidence_carries_the_observed_version(dsp):
    evidence = dsp_client.make_client(dsp, timeout_seconds=3)(make_row(), NOW)

    state = next(e for e in evidence if e.kind == EvidenceKind.CAMPAIGN_STATE)
    assert state.campaign_version_observed == 1
    assert state.task_id == "t1"
    assert all(e.observed_at == NOW for e in evidence)  # 讀取時間用呼叫端傳來的時間,不自己讀時鐘


# ---- S43 ----
def test_a_nonexistent_campaign_makes_the_whole_call_raise(dsp):
    client = dsp_client.make_client(dsp, timeout_seconds=3)

    with pytest.raises(dsp_client.DspRequestFailed):
        client(make_row(campaign_id="ghost"), NOW)


def test_campaign_state_succeeding_but_metrics_failing_raises_and_returns_nothing_partial(
    tmp_path,
):
    """代碼審第 1 輪指出:兩個端點「先成功後失敗」這個排列組合完全沒有測試覆蓋到——
    之前的假 fixture 一律同時 seed 了 state 跟 metrics,測不出「現況讀得到、指標讀不到」
    這個真正需要 all-or-nothing 契約守住的情境。"""
    store = CampaignStore(tmp_path / "dsp.db")
    store.seed_campaign("c1", budget=100)  # 故意不 seed_metrics:模擬指標端點會失敗
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        client = dsp_client.make_client(
            f"http://127.0.0.1:{server.server_address[1]}", timeout_seconds=3)
        with pytest.raises(dsp_client.DspRequestFailed):
            # 第一個端點(現況)成功,第二個(指標)失敗:整個呼叫要往外丟例外
            client(make_row(), NOW)
    finally:
        server.shutdown()
        server.server_close()


def test_the_dsp_being_unreachable_makes_the_whole_call_raise():
    client = dsp_client.make_client("http://127.0.0.1:1", timeout_seconds=1)

    with pytest.raises(Exception):  # noqa: B017 - 只驗證會整個丟例外,不驗證是哪一種
        client(make_row(), NOW)


# ---- content_hash ----
def test_the_same_campaign_state_produces_the_same_content_hash(dsp):
    client = dsp_client.make_client(dsp, timeout_seconds=3)

    first = next(e for e in client(make_row(), NOW) if e.kind == EvidenceKind.CAMPAIGN_STATE)
    second = next(e for e in client(make_row(), NOW) if e.kind == EvidenceKind.CAMPAIGN_STATE)

    assert first.content_hash == second.content_hash


# ---- S44(輔助訊號:原始碼掃描) ----
def test_the_dsp_client_module_never_mentions_the_fault_header():
    import inspect

    assert "X-Fault" not in inspect.getsource(dsp_client)
