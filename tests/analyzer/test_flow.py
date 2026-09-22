"""分析行程的流程與檢查點:對應計劃的 S20~S41。"""


import threading

import pytest

from rtb.analyzer import flow
from rtb.analyzer.task_store import TaskAlreadyExists, TaskNotFound, TaskStore
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting, make_evidence, make_proposal

NOOP = Counting()  # 不該被呼叫的介面;呼叫了測試就會知道(引數列表非空)


@pytest.fixture(autouse=True)
def _reset_noop():
    """NOOP 是模組層級的共用物件:每個測試開始前清空 .calls,避免前一個測試的呼叫記錄
    汙染這個測試(2026-09-22 代碼審用污染對照實驗證實過這個風險)。"""
    NOOP.calls.clear()


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


def test_advance_reports_no_progress_without_writing_when_it_loses_the_race(store, monkeypatch):
    store.create_task("t1", "c1", NOW)

    monkeypatch.setattr(TaskStore, "commit_step", lambda *_args, **_kwargs: False)
    result = flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)

    assert result is TaskState.RECEIVED  # 老實回報輸掉競爭前的狀態,不是它本來想寫的新狀態
    assert store.latest("t1").seq == 1  # 什麼都沒寫


def test_many_real_threads_racing_advance_on_the_same_task_commit_exactly_once(tmp_path):
    path = tmp_path / "race.db"
    TaskStore(path).create_task("t1", "c1", NOW)
    results = []
    barrier = threading.Barrier(20)  # 逼所有執行緒真的同時讀到同一列,不是各自錯開陸續前進

    def run():
        thread_store = TaskStore(path)  # 每條執行緒自己的連線,比對照 inbox 並行測試的做法
        try:
            barrier.wait(timeout=5)
            results.append(flow.advance(thread_store, "t1", NOOP, NOOP, NOOP, NOW))
        finally:
            thread_store.close()

    threads = [threading.Thread(target=run) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r is TaskState.COLLECTING_EVIDENCE]
    losers = [r for r in results if r is TaskState.RECEIVED]
    assert len(winners) == 1 and len(losers) == 19  # 恰好一個成功,其餘老實回報沒有進展
    final = TaskStore(path)
    assert final.latest("t1").seq == 2  # 只寫了一列,不是 20 列
    final.close()


# ---- EvidenceSource 回傳形狀不對時的行為(2026-09-22 代碼審用真實多執行緒測試意外揪出) ----
def test_an_evidence_source_that_returns_the_wrong_shape_is_treated_as_a_failed_fetch(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    bad = Counting(returns=None)  # 沒回 tuple[Evidence, ...],是介面實作的錯,不是「沒有證據」

    new_state = flow.advance(store, "t1", bad, NOOP, NOOP, NOW)

    assert new_state is TaskState.COLLECTING_EVIDENCE  # 跟丟例外一樣:重試永遠安全,不寫入
    assert store.latest("t1").seq == 2


# ---- S39/S40:崩潰恢復(逐一涵蓋 RECEIVED、COLLECTING_EVIDENCE、ANALYZING、PROPOSED 四步) ----
def _boom():
    raise RuntimeError("crash before commit")


def test_a_crash_before_commit_while_leaving_received_loses_nothing(store):
    store.create_task("t1", "c1", NOW)

    with pytest.raises(RuntimeError):
        flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW, before_commit=_boom)
    assert store.latest("t1").state is TaskState.RECEIVED  # 交易整個沒提交,什麼都沒變

    result = flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)  # 沒有中斷地重跑一次
    assert result is TaskState.COLLECTING_EVIDENCE


def test_a_crash_before_commit_while_collecting_evidence_loses_nothing(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    before = store.latest("t1")

    with pytest.raises(RuntimeError):
        flow.advance(store, "t1", evidence_source(), NOOP, NOOP, NOW, before_commit=_boom)
    assert store.latest("t1") == before  # 沒有新增證據列,也沒有新增狀態列

    result = flow.advance(store, "t1", evidence_source(), NOOP, NOOP, NOW)
    assert result is TaskState.ANALYZING


def test_a_crash_before_commit_while_analyzing_loses_nothing(store):
    row = _to_analyzing(store)
    proposal = make_proposal()

    with pytest.raises(RuntimeError):
        flow.advance(store, "t1", NOOP, decide(returns=flow.ProposalDecision(proposal)), NOOP,
                     NOW, before_commit=_boom)
    assert store.latest("t1") == row  # 沒有新增提案快照,也沒有新增狀態列

    result = flow.advance(store, "t1", NOOP, decide(returns=flow.ProposalDecision(proposal)),
                          NOOP, NOW)
    assert result is TaskState.PROPOSED


def test_a_crash_before_commit_while_proposed_loses_nothing(store):
    _to_proposed(store)
    before = store.latest("t1")
    s = submit()

    with pytest.raises(RuntimeError):
        flow.advance(store, "t1", NOOP, NOOP, s, NOW, before_commit=_boom)
    assert store.latest("t1") == before  # 沒有交接,submit 已經被呼叫過(這是外部呼叫本身的副作用,
    # 不是這個增量要保護的範圍——增量 4 接真的收件口之後,重送同一份提案本來就安全)

    result = flow.advance(store, "t1", NOOP, NOOP, s, NOW)
    assert result is TaskState.HANDED_OFF


def test_resuming_after_a_crash_after_commit_never_repeats_or_diverges_from_the_committed_step(
        store):
    proposal = _to_proposed(store)
    s = submit(returns=flow.Accepted(replayed=False))
    flow.advance(store, "t1", NOOP, NOOP, s, NOW)  # 成功送出並交接

    # 模擬「行程在提交後才當機」:呼叫端不知道,重呼叫 advance()
    result = flow.advance(store, "t1", NOOP, NOOP, s, NOW)

    assert result is TaskState.HANDED_OFF  # 已經是終點,advance() 不會再呼叫 submit
    assert s.calls == [(proposal,)]  # 只送出過一次,不是兩次


SQL_KEYWORDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "PRAGMA",
                "BEGIN", "COMMIT", "ROLLBACK")


def _sql_literals(source):
    import ast

    tree = ast.parse(source)
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.strip().upper().startswith(SQL_KEYWORDS)
    ]


# ---- S41 ----
def test_the_history_table_has_no_update_or_delete_statements():
    import inspect

    from rtb.analyzer import task_store

    sql_literals = _sql_literals(inspect.getsource(task_store))

    assert len(sql_literals) >= 4  # 守衛的守衛:真的有找到多條 SQL 字面量,不是空跑或篩選漏光
    assert not any("UPDATE " in sql.upper() or "DELETE " in sql.upper() for sql in sql_literals)


def test_the_update_or_delete_guard_actually_catches_a_planted_statement():
    import inspect

    from rtb.analyzer import task_store

    planted = '\n_PLANTED = "UPDATE tasks SET state = 1"\n'
    source = inspect.getsource(task_store) + planted

    sql_literals = _sql_literals(source)

    assert any("UPDATE " in sql.upper() for sql in sql_literals)  # 守衛真的抓得到種下去的違規


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


# ---- Submit/Decide 回傳合約之外的型別:不是暫時性失敗,是介面本身的合約違反 ----
def test_submit_returning_anything_other_than_accepted_raises_instead_of_being_treated_as_success(
        store):
    _to_proposed(store)

    with pytest.raises(flow._BrokenCollaborator):
        flow.advance(store, "t1", NOOP, NOOP, Counting(returns="ok"), NOW)
    assert store.latest("t1").state is TaskState.PROPOSED  # 沒有半途寫出 HANDED_OFF


def test_decide_returning_an_unrecognised_type_raises_instead_of_being_treated_as_needs_evidence(
        store):
    _to_analyzing(store)

    with pytest.raises(flow._BrokenCollaborator):
        flow.advance(store, "t1", NOOP, Counting(returns="not a decision"), NOOP, NOW)
    assert store.latest("t1").state is TaskState.ANALYZING  # 沒有被誤判成 NeedsFreshEvidence


# ---- 第 1 輪代碼審補上的防護(evidence 任務歸屬、序號合法性、輸入驗證、長度上限、讀回毀損) ----
def test_commit_step_rejects_evidence_that_belongs_to_a_different_task(store):
    from rtb.analyzer.task_store import EvidenceTaskMismatch

    store.create_task("t1", "c1", NOW)
    row = store.latest("t1")
    foreign = make_evidence(task_id="other-task")

    with pytest.raises(EvidenceTaskMismatch):
        store.commit_step("t1", row.seq, TaskState.COLLECTING_EVIDENCE, NOW, evidence=(foreign,))
    assert store.latest("t1").seq == 1  # 沒有寫入


def test_commit_step_rejects_a_new_state_that_is_not_a_legal_transition(store):
    from rtb.domain.task_state import IllegalTransition

    store.create_task("t1", "c1", NOW)
    row = store.latest("t1")

    with pytest.raises(IllegalTransition):
        store.commit_step("t1", row.seq, TaskState.HANDED_OFF, NOW)  # RECEIVED 不能直接跳到這裡
    assert store.latest("t1").seq == 1


@pytest.mark.parametrize(("task_id", "campaign_id"), [
    ("", "c1"), ("t1", ""), ("t/1", "c1"), ("t1", "c" * 200),
])
def test_create_task_rejects_malformed_ids(store, task_id, campaign_id):
    from rtb.analyzer.task_store import InvalidTaskId

    with pytest.raises(InvalidTaskId):
        store.create_task(task_id, campaign_id, NOW)


def test_error_detail_is_capped_before_it_enters_the_permanent_table(store):
    from rtb.analyzer.task_store import MAX_ERROR_DETAIL_LENGTH

    _to_analyzing(store)
    huge = "x" * (MAX_ERROR_DETAIL_LENGTH * 2)

    flow.advance(store, "t1", NOOP, decide(raises=ValueError(huge)), NOOP, NOW)

    assert len(store.latest("t1").error_detail) <= MAX_ERROR_DETAIL_LENGTH


def test_evidence_for_preserves_the_order_evidence_was_collected_in_not_alphabetical_order(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    ordered = (make_evidence(evidence_id="z-last"), make_evidence(evidence_id="a-first"))

    flow.advance(store, "t1", evidence_source(returns=ordered), NOOP, NOOP, NOW)

    row = store.latest("t1")
    assert store.evidence_for("t1", row.seq) == ordered  # 不是照 evidence_id 字母排序


def test_a_corrupted_evidence_row_fails_the_task_instead_of_crashing_advance(store, monkeypatch):
    from rtb.analyzer.task_store import CorruptedHistoryRow

    _to_analyzing(store)

    def broken(_self, _task_id, _seq):
        raise CorruptedHistoryRow("boom")

    monkeypatch.setattr(TaskStore, "evidence_for", broken)

    new_state = flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)

    assert new_state is TaskState.FAILED
    assert "boom" in store.latest("t1").error_detail


# ---- 補測試證明力(第 1 輪代碼審 s3、x1 指出這幾件事沒被斷言過) ----
def test_evidence_for_does_not_leak_evidence_from_an_earlier_round_after_a_reweave(store):
    round_one = (make_evidence(evidence_id="round1"),)
    _to_analyzing(store)  # 第一輪蒐證(evidence_source() 產生的預設證據)
    needs_fresh = decide(returns=flow.NeedsFreshEvidence())
    flow.advance(store, "t1", NOOP, needs_fresh, NOOP, NOW)  # 退回重蒐

    round_two = (make_evidence(evidence_id="round2"),)
    new_state = flow.advance(store, "t1", evidence_source(returns=round_two), NOOP, NOOP, NOW)

    assert new_state is TaskState.ANALYZING
    row = store.latest("t1")
    assert store.evidence_for("t1", row.seq) == round_two  # 只看得到這一輪的,看不到第一輪的
    assert round_one not in (store.evidence_for("t1", row.seq),)


def test_decide_receives_the_evidence_that_was_actually_collected_this_round(store):
    given = (make_evidence(evidence_id="e-given"),)
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    flow.advance(store, "t1", evidence_source(returns=given), NOOP, NOOP, NOW)
    d = decide()

    flow.advance(store, "t1", NOOP, d, NOOP, NOW)

    assert d.calls[0][1] == given  # decide(task, evidence) 的第二個引數就是剛蒐到的證據


def test_evidence_source_receives_the_task_row_it_is_gathering_for(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    source = evidence_source()

    flow.advance(store, "t1", source, NOOP, NOOP, NOW)

    assert source.calls[0][0].task_id == "t1"
    assert source.calls[0][0].campaign_id == "c1"


# ---- S46a/S46b:too_many_revisions 是永久拒收,不是暫時性 stale(增量 4) ----
def test_a_permanent_submit_rejection_fails_the_task_instead_of_looping_forever(store):
    _to_proposed(store)

    new_state = flow.advance(
        store, "t1", NOOP, NOOP, submit(raises=flow.SubmitRejectedPermanently("too many")), NOW)

    assert new_state is TaskState.FAILED
    assert "too many" in store.latest("t1").error_detail
    # FAILED 是終點:再呼叫一次不會又跑 submit
    s = submit()
    assert flow.advance(store, "t1", NOOP, NOOP, s, NOW) is TaskState.FAILED
    assert s.call_count == 0
