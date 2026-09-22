"""真的收件口用戶端(Submit):S45~S48、S46a。"""

import threading

import pytest

from rtb.analyzer import flow, inbox_client
from rtb.domain.proposal import parse_proposal
from rtb.executor.inbox_server import InboxServer
from tests.domain.proposal_samples import valid


def make_proposal(**overrides):
    result = parse_proposal(valid(**overrides))
    assert result.proposal is not None, result.errors
    return result.proposal


@pytest.fixture
def inbox(tmp_path):
    from datetime import UTC, datetime

    fixed_now = datetime(2026, 9, 22, 12, 5, tzinfo=UTC)  # 跟提案樣本的固定時間對得上
    server = InboxServer(tmp_path / "inbox.db", max_pending=2, clock=lambda: fixed_now)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def client(url, timeout=3):
    return inbox_client.make_client(url, timeout_seconds=timeout)


# ---- S45 ----
def test_first_acceptance_maps_to_accepted_not_replayed(inbox):
    result = client(inbox)(make_proposal())

    assert isinstance(result, flow.Accepted) and result.replayed is False


def test_a_resend_maps_to_accepted_replayed(inbox):
    submit = client(inbox)
    proposal = make_proposal()
    submit(proposal)

    result = submit(proposal)

    assert isinstance(result, flow.Accepted) and result.replayed is True


# ---- S46 ----
def test_a_content_conflict_maps_to_submit_stale(inbox):
    submit = client(inbox)
    submit(make_proposal())

    with pytest.raises(flow.SubmitStale):
        submit(make_proposal(requested_change={"new_budget": 999}))


def test_an_expired_proposal_maps_to_submit_stale(inbox):
    # 建立時間之後、但早於收件口目前時鐘(12:05)——已過期,不是「早於建立時間」那種不合法
    expired = make_proposal(decision_expires_at="2026-09-22T12:03:00+00:00")

    with pytest.raises(flow.SubmitStale):
        client(inbox)(expired)


# ---- S46a ----
def test_too_many_revisions_maps_to_a_permanent_rejection_not_stale(inbox, monkeypatch):
    from rtb.executor import inbox_store

    monkeypatch.setattr(inbox_store, "MAX_REVISIONS_PER_TASK", 1)
    submit = client(inbox)
    submit(make_proposal())

    with pytest.raises(flow.SubmitRejectedPermanently):
        submit(make_proposal(revision=2))


# ---- S47 ----
def test_a_full_inbox_maps_to_submit_busy(inbox):
    submit = client(inbox)
    submit(make_proposal(task_id="t1"))
    submit(make_proposal(task_id="t2"))

    with pytest.raises(flow.SubmitBusy):
        submit(make_proposal(task_id="t3"))


# ---- S48 ----
def test_an_unreachable_inbox_propagates_unmapped():
    submit = inbox_client.make_client("http://127.0.0.1:1", timeout_seconds=1)

    with pytest.raises(Exception) as excinfo:
        submit(make_proposal())
    assert excinfo.type not in (flow.SubmitStale, flow.SubmitBusy, flow.SubmitRejectedPermanently)


# ---- 輔助訊號 ----
def test_the_inbox_client_module_never_mentions_the_fault_header():
    import inspect

    assert "X-Fault" not in inspect.getsource(inbox_client)
