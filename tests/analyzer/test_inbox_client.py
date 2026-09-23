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


def test_an_expiry_too_far_in_the_future_maps_to_submit_stale(inbox):
    # created→expires 只差 59 分(領域層的「決策存活最多 1 小時」不會擋),但到期時間離收件口
    # 目前時鐘(12:05)超過 1 小時,是收件口自己另外設的上限。
    too_far = make_proposal(decision_created_at="2026-09-22T12:09:00+00:00",
                            decision_expires_at="2026-09-22T13:08:00+00:00")

    with pytest.raises(flow.SubmitStale):
        client(inbox)(too_far)


def test_a_decision_created_too_far_in_the_future_maps_to_submit_stale(inbox):
    # 建立時間比收件口目前時鐘(12:05)晚超過容許的 5 分鐘時鐘偏差,但到期時間本身仍在正常範圍。
    from_the_future = make_proposal(decision_created_at="2026-09-22T12:20:00+00:00",
                                    decision_expires_at="2026-09-22T12:50:00+00:00")

    with pytest.raises(flow.SubmitStale):
        client(inbox)(from_the_future)


# ---- 狀態碼跟錯誤代碼要一起對,不能只看代碼字串 ----
def test_a_permanent_rejection_code_under_the_wrong_status_is_not_misclassified():
    from rtb.analyzer import inbox_client as _inbox_client

    with pytest.raises(RuntimeError):
        _inbox_client._handle_rejection(500, {"error": "too_many_revisions"})


def test_a_stale_code_reported_under_the_wrong_status_is_not_misclassified():
    from rtb.analyzer import inbox_client as _inbox_client

    # content_conflict 是 409,不是 503
    with pytest.raises(RuntimeError):
        _inbox_client._handle_rejection(503, {"error": "content_conflict"})


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


# ---- Phase 5:重送的回應本文帶回處置,給「已交給執行之後」那一步核對 ----
def test_a_resend_reports_the_disposition_and_identifies_the_proposal(inbox):
    from rtb.domain.proposal import content_hash

    submit = client(inbox)
    proposal = make_proposal()
    submit(proposal)

    result = submit(proposal)

    assert (result.task_id, result.revision, result.content_hash, result.state) == (
        proposal.task_id, proposal.revision, content_hash(proposal), "pending")
    assert result.block_code is None


@pytest.mark.parametrize(("body", "expected"), [
    ({"state": "blocked", "block_code": "version_changed"}, ("blocked", "version_changed")),
    ({"state": "blocked", "block_code": 7}, ("blocked", None)),
    ({"state": ["handed_off"], "block_code": None}, (None, None))])
def test_the_block_code_in_the_response_body_is_read(body, expected):
    base = {"task_id": "t1", "revision": 1, "content_hash": "a" * 64, "replayed": True}

    result = inbox_client._accepted(200, base | body)

    assert (result.state, result.block_code) == expected
    assert (result.task_id, result.revision, result.content_hash) == ("t1", 1, "a" * 64)


@pytest.mark.parametrize("revision", [True, "1", 1.0, None])
def test_a_revision_that_is_not_a_plain_integer_is_read_as_missing(revision):
    """布林 True 等於 1:型別守衛拿掉的話,偽造的 revision=true 會被當成修訂 1 而核對通過。"""
    body = {"task_id": "t1", "revision": revision, "content_hash": "a" * 64,
            "state": "handed_off", "block_code": None, "replayed": True}

    assert inbox_client._accepted(200, body).revision is None
