"""已交給執行之後的下一步與重新規劃(Phase 5 分析側):對應計劃的 S301 到 S307、S311 到 S321。"""

import threading
from datetime import timedelta

import pytest

from rtb.analyzer import flow, task_store
from rtb.analyzer.task_store import (
    FOLLOW_UP_PREFIX,
    MAX_GENERATION,
    FollowUp,
    InvalidTaskId,
    ReplanReason,
    TaskStore,
    follow_up_id,
    replan_counts,
    trace_for,
)
from rtb.domain.attempt import KEY_PREFIX, operation_key
from rtb.domain.proposal import content_hash
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting, make_evidence, make_proposal

NOOP = Counting()


@pytest.fixture(autouse=True)
def _reset_noop():
    NOOP.calls.clear()


def _handed_off(store, task_id="t1", campaign_id="c1"):
    """把任務推到已交給執行,回傳存著的那份提案。"""
    store.create_task(task_id, campaign_id, NOW)
    flow.advance(store, task_id, NOOP, NOOP, NOOP, NOW)
    flow.advance(store, task_id, Counting(returns=(make_evidence(task_id=task_id),)), NOOP, NOOP,
                 NOW)
    proposal = make_proposal(task_id=task_id, campaign_id=campaign_id)
    flow.advance(store, task_id, NOOP, Counting(returns=flow.ProposalDecision(proposal)), NOOP,
                 NOW)
    state = flow.advance(store, task_id, NOOP, NOOP,
                         Counting(returns=flow.Accepted(replayed=False)), NOW)
    assert state is TaskState.HANDED_OFF
    return proposal


def answer(proposal, state, block_code=None, **overrides):
    """收件口對重送的回應(已解析):預設跟送出的快照一致。"""
    fields = {"replayed": True, "task_id": proposal.task_id, "revision": proposal.revision,
              "content_hash": content_hash(proposal), "state": state, "block_code": block_code}
    fields.update(overrides)
    return flow.Accepted(**fields)


def dsp_operation(proposal, **overrides):
    fields = {"campaign_id": proposal.campaign_id, "action": proposal.action_type.value,
              "new_budget": proposal.requested_change.get("new_budget"),
              "expected_version": proposal.campaign_version_observed}
    fields.update(overrides)
    return flow.DspOperation(**fields)


def step(store, submit, lookup=None, task_id="t1", now=NOW, before_commit=None):
    return flow.advance(store, task_id, NOOP, NOOP, submit, now, before_commit=before_commit,
                        operation_lookup=lookup if lookup is not None else Counting())


# ---- S301 ----
def test_a_handed_off_task_completes_when_the_inbox_reports_handed_off(store):
    proposal = _handed_off(store)
    lookup = Counting()

    result = step(store, Counting(returns=answer(proposal, "handed_off")), lookup)

    assert result is TaskState.COMPLETED
    assert lookup.call_count == 0  # 收件口答得出來就不查 DSP


# ---- S316 已由 test_flow 既有那支守:沒給操作查詢時不呼叫任何協作者 ----


# ---- S311 ----
def test_after_the_retention_window_the_dsp_decides_completion(store):
    proposal = _handed_off(store)
    purged = Counting(raises=flow.SubmitStale("expired_proposal"))
    lookup = Counting(returns=dsp_operation(proposal))

    assert step(store, purged, lookup) is TaskState.COMPLETED
    assert lookup.calls == [(operation_key(proposal),)]  # 用交接時存下的那把鍵

    other = make_proposal(task_id="t2")
    _handed_off(store, "t2")
    wrong = Counting(returns=dsp_operation(other, new_budget=999_999))
    assert step(store, purged, wrong, task_id="t2") is TaskState.BLOCKED
    assert "idempotency_conflict" in store.latest("t2").error_detail
    assert store.follow_up_to("t2") is None


def test_the_stored_key_is_used_even_if_the_proposal_would_hash_differently(store):
    proposal = _handed_off(store)
    assert store.operation_key_for("t1") == operation_key(proposal)
    store._conn.execute(  # 模擬算法改版:存下的鍵跟現在重算的不同
        "UPDATE tasks SET operation_key = 'k1-stored' WHERE operation_key IS NOT NULL")
    store._conn.commit()
    lookup = Counting(returns=dsp_operation(proposal))

    step(store, Counting(raises=flow.SubmitStale("expired_proposal")), lookup)

    assert lookup.calls == [("k1-stored",)]


# ---- S311:四個欄位每一個對不上都不算完成 ----
@pytest.mark.parametrize("change", [
    {"campaign_id": "c9"}, {"action": "pause_campaign"}, {"new_budget": 1},
    {"expected_version": 99}])
def test_every_mismatching_field_is_an_idempotency_conflict(store, change):
    proposal = _handed_off(store)

    lookup = Counting(returns=dsp_operation(proposal, **change))
    step(store, Counting(raises=flow.SubmitStale("expired_proposal")), lookup)

    assert store.latest("t1").state is TaskState.BLOCKED
    assert "idempotency_conflict" in store.latest("t1").error_detail


# ---- S317 ----
@pytest.mark.parametrize("code", ["expired_proposal", "revision_out_of_order"])
def test_after_the_retention_window_a_missing_write_is_replanned(store, code):
    _handed_off(store)

    result = step(store, Counting(raises=flow.SubmitStale(code)), Counting(returns=None))

    assert result is TaskState.BLOCKED
    child = follow_up_id("t1")
    assert store.follow_up_to("t1") == child
    assert store.latest(child).state is TaskState.RECEIVED
    assert "after_retention" in store.latest("t1").error_detail


# ---- S302 ----
@pytest.mark.parametrize("state", ["pending", "in_progress"])
def test_a_handed_off_task_waits_while_the_proposal_is_still_open(store, state):
    proposal = _handed_off(store)
    before = store.latest("t1").seq

    assert step(store, Counting(returns=answer(proposal, state))) is TaskState.HANDED_OFF
    assert store.latest("t1").seq == before


# ---- S312 ----
@pytest.mark.parametrize("failure", [
    flow.SubmitBusy("busy"), RuntimeError("收件口回 500"), TimeoutError(), ValueError("讀不懂")])
def test_a_failed_lookup_or_resend_leaves_the_handed_off_task_for_the_next_round(store, failure):
    _handed_off(store)
    before = store.latest("t1").seq

    assert step(store, Counting(raises=failure)) is TaskState.HANDED_OFF
    purged = Counting(raises=flow.SubmitStale("expired_proposal"))
    assert step(store, purged, Counting(raises=failure)) is TaskState.HANDED_OFF
    assert store.latest("t1").seq == before
    assert store.acquire_lease("t1", "someone-else", NOW) is not None  # 租約已放掉


# ---- S303 ----
def test_a_version_conflict_hands_the_task_over_to_a_follow_up(store):
    proposal = _handed_off(store)

    result = step(store, Counting(returns=answer(proposal, "blocked", "version_changed")))

    assert result is TaskState.BLOCKED
    child = follow_up_id("t1")
    assert child in store.latest("t1").error_detail
    assert store.latest(child).state is TaskState.RECEIVED
    assert store.latest(child).campaign_id == "c1"
    assert store.follow_up_of(child) == "t1"


# ---- S313 ----
def test_an_expired_decision_is_replanned(store):
    proposal = _handed_off(store)

    assert step(store, Counting(returns=answer(proposal, "expired"))) is TaskState.BLOCKED
    assert store.follow_up_to("t1") == follow_up_id("t1")


# ---- S304 ----
@pytest.mark.parametrize(("state", "code"), [
    ("blocked", "not_permitted"), ("blocked", "operation_previously_failed"),
    ("blocked", "campaign_not_found"), ("blocked", "campaign_not_active"),
    ("dead_letter", None)])
def test_other_blocks_close_the_task_without_a_follow_up(store, state, code):
    proposal = _handed_off(store)

    assert step(store, Counting(returns=answer(proposal, state, code))) is TaskState.BLOCKED
    detail = store.latest("t1").error_detail
    assert state in detail
    assert code is None or code in detail
    assert store.follow_up_to("t1") is None


@pytest.mark.parametrize("code", ["content_conflict", "expiry_too_far", "created_in_future"])
def test_a_permanent_stale_rejection_closes_the_task(store, code):
    _handed_off(store)
    lookup = Counting()

    assert step(store, Counting(raises=flow.SubmitStale(code)), lookup) is TaskState.BLOCKED
    assert code in store.latest("t1").error_detail
    assert lookup.call_count == 0
    assert store.follow_up_to("t1") is None


def test_a_permanent_rejection_closes_the_task(store):
    _handed_off(store)

    rejected = Counting(raises=flow.SubmitRejectedPermanently("too_many_revisions"))
    assert step(store, rejected) is TaskState.BLOCKED
    assert store.follow_up_to("t1") is None


# ---- S305 ----
def test_a_superseded_proposal_marks_the_task_superseded(store):
    proposal = _handed_off(store)

    assert step(store, Counting(returns=answer(proposal, "superseded"))) is TaskState.SUPERSEDED


# ---- S306 ----
def test_a_crash_before_the_follow_up_commit_is_recovered_once(store):
    proposal = _handed_off(store)
    conflict = Counting(returns=answer(proposal, "blocked", "version_changed"))

    def crash():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        step(store, conflict, before_commit=crash)
    child = follow_up_id("t1")
    assert store.latest("t1").state is TaskState.HANDED_OFF
    assert store.latest(child) is None
    assert store.follow_up_to("t1") is None

    assert step(store, conflict, now=NOW + timedelta(minutes=2)) is TaskState.BLOCKED
    assert store.latest(child).seq == 1
    assert replan_counts(store).replanned == 1


# ---- S314 ----
def test_a_follow_up_id_collision_is_refused(store):
    with pytest.raises(InvalidTaskId):
        store.create_task(FOLLOW_UP_PREFIX + "x", "c1", NOW)
    store.create_task("c" + FOLLOW_UP_PREFIX, "c1", NOW)  # 只管開頭,不管中間
    store.create_task("t9", FOLLOW_UP_PREFIX + "campaign", NOW)  # 廣告編號不受影響

    proposal = _handed_off(store)
    child = follow_up_id("t1")
    store._conn.execute(  # 模擬命名空間被繞過而已經有一個同名任務(防線)
        "INSERT INTO tasks VALUES (?, 1, 'received', 'c9', NULL, NULL, "
        "'2026-09-22T12:00:00.000000Z', NULL)", (child,))
    store._conn.commit()

    result = step(store, Counting(returns=answer(proposal, "blocked", "version_changed")))

    assert result is TaskState.BLOCKED
    assert "follow_up_id_collision" in store.latest("t1").error_detail
    assert store.follow_up_to("t1") is None
    assert store.latest(child).campaign_id == "c9"


def test_a_follow_up_id_is_fixed_length_and_derived_from_the_original():
    long_id = "a" * 128
    assert follow_up_id(long_id) == follow_up_id(long_id)
    assert len(follow_up_id(long_id)) == len(FOLLOW_UP_PREFIX) + 24
    assert follow_up_id("t1") != follow_up_id("t2")


# ---- S307 ----
def test_the_follow_up_chain_stops_at_the_generation_limit(store):
    proposal = _handed_off(store)
    conflict = answer(proposal, "blocked", "version_changed")
    task = "t1"
    chain = [task]
    for _ in range(MAX_GENERATION + 2):  # 守衛壞掉時用斷言失敗收場,不是無限迴圈
        if task != "t1":
            _drive_to_handed_off(store, task)
            conflict = answer(store.latest(task).proposal, "expired")  # 原因不同也算同一條鏈
        step(store, Counting(returns=conflict), task_id=task)
        child = store.follow_up_to(task)
        if child is None:
            break
        chain.append(child)
        task = child
    else:
        pytest.fail("代數上限沒有擋下接續鏈")

    assert len(chain) == 3  # 原任務加兩次重做
    assert "replan_limit_reached" in store.latest(chain[-1]).error_detail
    assert replan_counts(store).exhausted == 1


def _drive_to_handed_off(store, task_id):
    flow.advance(store, task_id, NOOP, NOOP, NOOP, NOW)
    flow.advance(store, task_id, Counting(returns=(make_evidence(task_id=task_id),)), NOOP, NOOP,
                 NOW)
    proposal = make_proposal(task_id=task_id)
    flow.advance(store, task_id, NOOP, Counting(returns=flow.ProposalDecision(proposal)), NOOP,
                 NOW)
    flow.advance(store, task_id, NOOP, NOOP, Counting(returns=flow.Accepted(replayed=False)), NOW)


# ---- S318 ----
def test_the_trace_links_both_ways_along_the_follow_up_chain(store):
    proposal = _handed_off(store)
    step(store, Counting(returns=answer(proposal, "blocked", "version_changed")))
    child = follow_up_id("t1")

    assert trace_for(store, "t1").follow_up_to == child
    assert trace_for(store, "t1").follow_up_of is None
    assert trace_for(store, child).follow_up_of == "t1"
    assert trace_for(store, child).follow_up_to is None


# ---- S319 ----
def _hold_then_answer(path, proposal, in_submit, taken_over, results):
    """過期持有者:送件卡住,等接手者拿到租約之後才拿到版本已變的回應、試著結案。"""

    def slow_submit(_proposal):
        in_submit.set()
        taken_over.wait(5)
        return answer(proposal, "blocked", "version_changed")

    own = TaskStore(path)
    try:
        results["old"] = step(own, slow_submit)
    finally:
        own.close()


def test_an_expired_holder_cannot_create_a_follow_up_after_a_takeover(store):
    proposal = _handed_off(store)
    in_submit, taken_over = threading.Event(), threading.Event()
    results, path = {}, store_path(store)
    worker = threading.Thread(
        target=_hold_then_answer, args=(path, proposal, in_submit, taken_over, results))
    worker.start()
    assert in_submit.wait(5)
    later = NOW + timedelta(minutes=2)  # 租約 60 秒已過期
    other = TaskStore(path)
    try:
        assert step(other, Counting(returns=answer(proposal, "in_progress")), now=later) \
            is TaskState.HANDED_OFF
        assert other.acquire_lease("t1", "taker", later) is not None  # 接手者仍持有
    finally:
        taken_over.set()
        worker.join(5)
        other.close()

    assert results["old"] is TaskState.HANDED_OFF  # 過期持有者一列都寫不進去
    assert store.latest(follow_up_id("t1")) is None
    assert store.follow_up_to("t1") is None


def store_path(store):
    return store._conn.execute("PRAGMA database_list").fetchone()[2]


# ---- S320 ----
@pytest.mark.parametrize("overrides", [
    {"task_id": "someone-else"}, {"revision": 2}, {"content_hash": "b" * 64},
    {"task_id": None}, {"state": "no-such-state"},
    {"state": "handed_off", "block_code": "version_changed"},
    {"state": "blocked", "block_code": None}])
def test_a_resend_answer_for_another_proposal_is_ignored(store, overrides):
    proposal = _handed_off(store)
    before = store.latest("t1").seq
    fields = {"state": "handed_off"} | overrides
    state = fields.pop("state")
    block = fields.pop("block_code", None)

    assert step(store, Counting(returns=answer(proposal, state, block, **fields))) \
        is TaskState.HANDED_OFF
    assert store.latest("t1").seq == before


def test_an_unknown_block_code_is_not_written_into_the_history(store):
    proposal = _handed_off(store)
    before = store.latest("t1").seq
    forged = "over_budget_cap\x1b[31m\ninjected:approved"

    assert step(store, Counting(returns=answer(proposal, "blocked", forged))) \
        is TaskState.HANDED_OFF
    assert store.latest("t1").seq == before


def test_a_submit_returning_the_wrong_type_fails_loudly(store):
    _handed_off(store)

    with pytest.raises(flow._BrokenCollaborator):
        step(store, Counting(returns="not-an-Accepted"))
    assert store.latest("t1").state is TaskState.HANDED_OFF


def test_the_error_detail_names_the_inbox_answer_the_replan_reason_and_the_outcome(store):
    proposal = _handed_off(store)

    step(store, Counting(returns=answer(proposal, "blocked", "version_changed")))

    assert store.latest("t1").error_detail == (
        f"blocked=version_changed;replan=version_changed;follow_up={follow_up_id('t1')}")


def test_closing_a_task_that_already_has_a_limit_reached_link_does_not_crash(store):
    _handed_off(store)
    row = store.latest("t1")
    store._conn.execute(  # 防線:已記過代數用完、又被要求結案一次
        "INSERT INTO follow_ups VALUES ('t1', NULL, 4, 'c1', 'expired', 'limit_reached', 'x')")
    store._conn.commit()

    assert store.commit_step("t1", row.seq, TaskState.BLOCKED, NOW, error_detail="expired",
                             follow_up=FollowUp(ReplanReason.EXPIRED))
    assert store.latest("t1").error_detail == "expired;replan=expired;follow_up=none"


def test_an_operation_lookup_returning_the_wrong_type_fails_loudly(store):
    _handed_off(store)

    with pytest.raises(flow._BrokenCollaborator):
        step(store, Counting(raises=flow.SubmitStale("expired_proposal")),
             Counting(returns="not-a-DspOperation"))
    assert store.latest("t1").state is TaskState.HANDED_OFF


def test_a_malformed_follow_up_id_is_never_written(store, monkeypatch):
    proposal = _handed_off(store)
    monkeypatch.setattr(task_store, "follow_up_id", lambda _task_id: "fu- has space")

    with pytest.raises(InvalidTaskId):
        step(store, Counting(returns=answer(proposal, "blocked", "version_changed")))
    assert store.latest("t1").state is TaskState.HANDED_OFF
    assert store.follow_up_to("t1") is None


# ---- S321 ----
def test_old_rows_may_recompute_the_key_only_while_the_algorithm_is_unchanged(store):
    assert KEY_PREFIX == "k1-", (
        "冪等鍵算法改版前,必須先替 Phase 5 之前就交給執行、沒有存鍵的舊任務回填鍵;"
        "否則分析端會用新算法重算、查錯鍵(見 Phase 5 計劃實務隱患)")
    proposal = _handed_off(store)
    store._conn.execute("UPDATE tasks SET operation_key = NULL")  # 模擬 Phase 5 之前寫的列
    store._conn.commit()

    assert store.operation_key_for("t1") is None
    lookup = Counting(returns=dsp_operation(proposal))
    step(store, Counting(raises=flow.SubmitStale("expired_proposal")), lookup)
    assert lookup.calls == [(operation_key(proposal),)]


# ---- S315 分析側 ----
def test_conflict_counts_are_queryable(store):
    for task_id, campaign, how in (("a", "c1", "version_changed"), ("b", "c1", "retention"),
                                   ("c", "c2", "version_changed"), ("d", "c2", "other")):
        proposal = _handed_off(store, task_id, campaign)
        if how == "retention":
            step(store, Counting(raises=flow.SubmitStale("expired_proposal")),
                 Counting(returns=None), task_id=task_id)
        elif how == "other":
            step(store, Counting(returns=answer(proposal, "blocked", "not_permitted")),
                 task_id=task_id)
        else:
            step(store, Counting(returns=answer(proposal, "blocked", how)), task_id=task_id)

    everything = replan_counts(store)
    assert (everything.replanned, everything.exhausted, everything.after_retention) == (3, 0, 1)
    only_c1 = replan_counts(store, campaign_id="c1")
    assert (only_c1.replanned, only_c1.after_retention) == (2, 1)
