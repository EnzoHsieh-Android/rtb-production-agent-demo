"""分析行程的流程與檢查點:對應計劃的 S20~S41。"""


import pytest

from rtb.analyzer import flow
from rtb.analyzer.task_store import TaskAlreadyExists, TaskNotFound
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting, make_evidence, make_proposal

NOOP = Counting()  # 不該被呼叫的介面;呼叫了測試就會知道(引數列表非空)


def evidence_source(returns=None, raises=None):
    return Counting(returns=returns if returns is not None else (make_evidence(),), raises=raises)


def decide(returns=None, raises=None):
    return Counting(returns=returns if returns is not None else flow.NoAction(), raises=raises)


def submit(returns=None, raises=None):
    return Counting(returns=returns if returns is not None else flow.Accepted(replayed=False),
                    raises=raises)


# ---- S20/S21/S22:create_task ----
def test_creating_a_new_task_id_writes_a_received_row(store):
    store.create_task("t1", "c1", NOW)

    row = store.latest("t1")
    assert (row.state, row.campaign_id, row.seq) == (TaskState.RECEIVED, "c1", 1)


def test_creating_the_same_task_id_twice_with_the_same_campaign_is_a_no_op(store):
    store.create_task("t1", "c1", NOW)

    store.create_task("t1", "c1", NOW)

    assert store.latest("t1").seq == 1


def test_creating_a_task_id_again_with_a_different_campaign_id_is_an_error(store):
    store.create_task("t1", "c1", NOW)

    with pytest.raises(TaskAlreadyExists):
        store.create_task("t1", "c2", NOW)
    assert store.latest("t1").campaign_id == "c1"


# ---- S23 ----
def test_advancing_a_task_that_was_never_created_fails_loudly(store):
    with pytest.raises(TaskNotFound):
        flow.advance(store, "ghost", NOOP, NOOP, NOOP, NOW)


# ---- S24 ----
def test_leaving_received_writes_a_checkpoint_before_any_collaborator_is_called(store):
    store.create_task("t1", "c1", NOW)

    new_state = flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)

    assert new_state is TaskState.COLLECTING_EVIDENCE
    assert NOOP.call_count == 0


# ---- S25 ----
def test_a_successful_evidence_fetch_commits_evidence_and_the_next_state_together(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    source = evidence_source()

    new_state = flow.advance(store, "t1", source, NOOP, NOOP, NOW)

    assert new_state is TaskState.ANALYZING
    row = store.latest("t1")
    assert store.evidence_for("t1", row.seq) == source._returns


# ---- S26 ----
def test_a_failing_evidence_fetch_leaves_no_trace_and_the_task_stays_ready_to_retry(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    source = evidence_source(raises=RuntimeError("dsp unreachable"))

    new_state = flow.advance(store, "t1", source, NOOP, NOOP, NOW)

    assert new_state is TaskState.COLLECTING_EVIDENCE
    assert store.latest("t1").seq == 2  # 沒有新增第 3 列
    assert store.evidence_for("t1", 2) == ()


def _to_analyzing(store, task_id="t1", campaign_id="c1"):
    store.create_task(task_id, campaign_id, NOW)
    flow.advance(store, task_id, NOOP, NOOP, NOOP, NOW)
    flow.advance(store, task_id, evidence_source(), NOOP, NOOP, NOW)
    return store.latest(task_id)


# ---- S27 ----
def test_analyzing_with_no_action_ends_the_task(store):
    _to_analyzing(store)

    new_state = flow.advance(store, "t1", NOOP, decide(returns=flow.NoAction()), NOOP, NOW)

    assert new_state is TaskState.NO_ACTION


# ---- S28 ----
def test_analyzing_with_a_proposal_decision_stores_the_snapshot_and_moves_to_proposed(store):
    _to_analyzing(store)
    proposal = make_proposal()

    new_state = flow.advance(
        store, "t1", NOOP, decide(returns=flow.ProposalDecision(proposal)), NOOP, NOW)

    assert new_state is TaskState.PROPOSED
    assert store.latest("t1").proposal == proposal


# ---- S29 ----
def test_analyzing_that_needs_fresh_evidence_goes_back_to_collecting_evidence(store):
    _to_analyzing(store)

    new_state = flow.advance(
        store, "t1", NOOP, decide(returns=flow.NeedsFreshEvidence()), NOOP, NOW)

    assert new_state is TaskState.COLLECTING_EVIDENCE


# ---- S30 ----
def test_a_decide_exception_ends_the_task_as_failed_instead_of_retrying_forever(store):
    _to_analyzing(store)

    new_state = flow.advance(store, "t1", NOOP, decide(raises=ValueError("bad policy")), NOOP, NOW)

    assert new_state is TaskState.FAILED
    assert "bad policy" in store.latest("t1").error_detail
    # 再呼叫一次:FAILED 是終點,不會再呼叫任何介面、也不會產生第二個結果
    d = decide()
    assert flow.advance(store, "t1", NOOP, d, NOOP, NOW) is TaskState.FAILED
    assert d.call_count == 0


def _to_proposed(store, proposal=None, task_id="t1"):
    _to_analyzing(store, task_id)
    proposal = proposal or make_proposal(task_id=task_id)
    flow.advance(store, task_id, NOOP, decide(returns=flow.ProposalDecision(proposal)), NOOP, NOW)
    return proposal


# ---- S31 ----
def test_proposed_always_resubmits_the_stored_snapshot_never_a_freshly_built_one(store):
    proposal = _to_proposed(store)
    s = submit()

    flow.advance(store, "t1", NOOP, NOOP, s, NOW)

    assert s.calls == [(proposal,)]


# ---- S32 ----
@pytest.mark.parametrize("replayed", [False, True])
def test_accepted_or_replayed_both_hand_off(store, replayed):
    _to_proposed(store)

    new_state = flow.advance(store, "t1", NOOP, NOOP, submit(returns=flow.Accepted(replayed)), NOW)

    assert new_state is TaskState.HANDED_OFF
    assert store.latest("t1").proposal is not None


# ---- S33 ----
def test_stale_goes_back_to_collecting_evidence(store):
    _to_proposed(store)

    new_state = flow.advance(store, "t1", NOOP, NOOP, submit(raises=flow.SubmitStale()), NOW)

    assert new_state is TaskState.COLLECTING_EVIDENCE


# ---- S34 ----
def test_busy_leaves_the_task_untouched_for_a_later_retry(store):
    _to_proposed(store)
    before = store.latest("t1")

    new_state = flow.advance(store, "t1", NOOP, NOOP, submit(raises=flow.SubmitBusy()), NOW)

    assert new_state is TaskState.PROPOSED
    assert store.latest("t1") == before


# ---- S35 ----
def test_an_unrecognised_submit_failure_is_treated_as_retryable_not_fatal(store):
    _to_proposed(store)
    before = store.latest("t1")

    new_state = flow.advance(store, "t1", NOOP, NOOP, submit(raises=TimeoutError()), NOW)

    assert new_state is TaskState.PROPOSED
    assert store.latest("t1") == before


# ---- S36 ----
@pytest.mark.parametrize("terminal_reacher", [
    lambda store: flow.advance(store, "t1", NOOP, decide(returns=flow.NoAction()), NOOP, NOW),
])
def test_advancing_a_terminal_or_handed_off_task_calls_no_collaborator(store, terminal_reacher):
    _to_analyzing(store)
    terminal_reacher(store)

    result = flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)

    assert result is TaskState.NO_ACTION
    assert NOOP.call_count == 0


def test_advancing_a_handed_off_task_calls_no_collaborator(store):
    _to_proposed(store)
    flow.advance(store, "t1", NOOP, NOOP, submit(), NOW)

    result = flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)

    assert result is TaskState.HANDED_OFF
    assert NOOP.call_count == 0


# ---- S37/S38:並行與序號 ----
def test_two_concurrent_advance_calls_on_the_same_task_never_both_commit_conflicting_outcomes(
        store):
    store.create_task("t1", "c1", NOW)
    row = store.latest("t1")
    # 模擬兩個呼叫端都讀到同一列(seq=1)之後,其中一個先提交
    assert store.commit_step("t1", row.seq, TaskState.COLLECTING_EVIDENCE, NOW)

    # 第二個呼叫端仍然拿著舊的 row(seq=1)試著提交,應該被擋下、什麼都沒寫
    committed = store.commit_step("t1", row.seq, TaskState.COLLECTING_EVIDENCE, NOW)

    assert committed is False
    assert store.latest("t1").seq == 2  # 只有第一個成功的那次


def test_the_next_sequence_number_is_decided_inside_the_write_transaction(store):
    store.create_task("t1", "c1", NOW)
    row = store.latest("t1")

    store.commit_step("t1", row.seq, TaskState.COLLECTING_EVIDENCE, NOW)

    assert store.latest("t1").seq == row.seq + 1


# ---- S39/S40:崩潰恢復 ----
def test_resuming_after_a_crash_before_commit_at_every_step_converges_to_the_uninterrupted_outcome(
        store):
    store.create_task("t1", "c1", NOW)

    def boom():
        raise RuntimeError("crash before commit")

    with pytest.raises(RuntimeError):
        flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW, before_commit=boom)
    assert store.latest("t1").state is TaskState.RECEIVED  # 交易整個沒提交,什麼都沒變

    # 沒有中斷地重跑一次,結果應該跟從沒中斷過一樣
    result = flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    assert result is TaskState.COLLECTING_EVIDENCE


def test_resuming_after_a_crash_after_commit_never_repeats_or_diverges_from_the_committed_step(
        store):
    proposal = _to_proposed(store)
    s = submit(returns=flow.Accepted(replayed=False))
    flow.advance(store, "t1", NOOP, NOOP, s, NOW)  # 成功送出並交接

    # 模擬「行程在提交後才當機」:呼叫端不知道,重呼叫 advance()
    result = flow.advance(store, "t1", NOOP, NOOP, s, NOW)

    assert result is TaskState.HANDED_OFF  # 已經是終點,advance() 不會再呼叫 submit
    assert s.calls == [(proposal,)]  # 只送出過一次,不是兩次


# ---- S41 ----
def test_the_history_table_has_no_update_or_delete_statements():
    import ast
    import inspect

    from rtb.analyzer import task_store

    tree = ast.parse(inspect.getsource(task_store))
    sql_literals = [
        node.value for node in ast.walk(tree)
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "SELECT" in node.value.upper())
        or (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.strip().upper().startswith(("INSERT", "CREATE")))
    ]
    assert sql_literals  # 守衛的守衛:真的有找到 SQL 字面量,不是空跑
    assert not any("UPDATE " in sql.upper() or "DELETE " in sql.upper() for sql in sql_literals)


# ---- 每一步的新狀態一律經過網域層的 transition() 計算,不是自己另刻邏輯 ----
def test_every_step_computes_its_new_state_through_the_domain_transition_function(
        store, monkeypatch):
    from rtb.domain import task_state

    store.create_task("t1", "c1", NOW)
    calls = []
    real = task_state.transition

    def spying(current, target):
        calls.append((current, target))
        return real(current, target)

    monkeypatch.setattr(flow, "transition", spying)

    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)

    assert calls == [(TaskState.RECEIVED, TaskState.COLLECTING_EVIDENCE)]


def test_an_illegal_transition_from_the_domain_layer_is_never_swallowed(store, monkeypatch):
    from rtb.domain.task_state import IllegalTransition

    store.create_task("t1", "c1", NOW)
    monkeypatch.setattr(flow, "transition", lambda *_: (_ for _ in ()).throw(IllegalTransition))

    with pytest.raises(IllegalTransition):
        flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    assert store.latest("t1").state is TaskState.RECEIVED  # 沒有寫入半筆壞資料
