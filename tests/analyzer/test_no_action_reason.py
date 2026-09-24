"""分析端決定不提案時存下原因(Phase 12 [S1048],設計審 r2 p1):
展示的判斷紀錄要讀得到「不調整」是哪一種。

原因不放進決策結果(Phase 10 的裁定,決策型別的相等比較照舊);流程層另收一個可省略的「問原因」介面,
只在決策是不提案時問,答案跟結案那一列在同一個交易裡寫進只增的原因表。
"""

import sqlite3

import pytest

from rtb.analyzer import flow
from rtb.analyzer.policy import NoActionReason
from rtb.analyzer.task_store import TaskReader, TaskStore
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting, make_evidence, make_proposal

NOOP = Counting()


@pytest.fixture(autouse=True)
def _reset_noop():
    NOOP.calls.clear()


def _to_analyzing(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", NOOP, NOOP, NOOP, NOW)
    flow.advance(store, "t1", Counting(returns=(make_evidence(),)), NOOP, NOOP, NOW)
    return store.latest("t1")


def _decide_nothing():
    return Counting(returns=flow.NoAction())


@pytest.mark.parametrize("reason", [r for r in NoActionReason
                                    if r is not NoActionReason.STALE_EVIDENCE])
def test_the_analyzer_keeps_why_it_did_not_propose(store, tmp_path, reason):
    """[S1048] 不提案時問到的原因,跟結案那一列一起寫進分析端資料庫,唯讀開法也讀得到。"""
    _to_analyzing(store)

    state = flow.advance(store, "t1", NOOP, _decide_nothing(), NOOP, NOW,
                         no_action_reason=Counting(returns=reason))

    assert state is TaskState.NO_ACTION
    seq = store.latest("t1").seq
    assert store.no_action_reason("t1", seq) == reason.value
    reader = TaskReader(tmp_path / "analyzer.db")
    try:
        assert reader.no_action_reason("t1", seq) == reason.value
    finally:
        reader.close()


def test_the_reason_collaborator_gets_the_same_inputs_as_decide(store):
    row = _to_analyzing(store)
    ask = Counting(returns=NoActionReason.PACING_UNKNOWN)

    flow.advance(store, "t1", NOOP, _decide_nothing(), NOOP, NOW, no_action_reason=ask)

    (args,) = ask.calls
    assert args[0].task_id == row.task_id and args[0].seq == row.seq
    assert args[1] == store.evidence_for("t1", row.seq)
    assert args[2] == NOW


def test_without_a_reason_collaborator_nothing_changes(store):
    _to_analyzing(store)

    state = flow.advance(store, "t1", NOOP, _decide_nothing(), NOOP, NOW)

    assert state is TaskState.NO_ACTION
    assert store.no_action_reason("t1", store.latest("t1").seq) is None


def test_a_failing_reason_lookup_does_not_change_the_outcome(store):
    _to_analyzing(store)

    state = flow.advance(store, "t1", NOOP, _decide_nothing(), NOOP, NOW,
                         no_action_reason=Counting(raises=RuntimeError("boom")))

    assert state is TaskState.NO_ACTION
    assert store.no_action_reason("t1", store.latest("t1").seq) is None


@pytest.mark.parametrize("answer", ["not_underpacing", "<script>alert(1)</script>", 3])
def test_an_answer_that_is_not_a_reason_member_is_not_stored(store, answer):
    """只收列舉成員:原因表不會刪,任意字串寫進去等於開一條注入管道。"""
    _to_analyzing(store)

    state = flow.advance(store, "t1", NOOP, _decide_nothing(), NOOP, NOW,
                         no_action_reason=Counting(returns=answer))

    assert state is TaskState.NO_ACTION
    assert store.no_action_reason("t1", store.latest("t1").seq) is None


def test_the_reason_is_only_asked_when_nothing_is_proposed(store):
    _to_analyzing(store)
    ask = Counting(returns=NoActionReason.JUDGED_NOT_WORTH)

    flow.advance(store, "t1", NOOP, Counting(returns=flow.ProposalDecision(make_proposal())),
                 NOOP, NOW, no_action_reason=ask)

    assert ask.calls == []


def test_a_crash_before_commit_leaves_neither_the_row_nor_the_reason(store):
    row = _to_analyzing(store)

    def boom():
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        flow.advance(store, "t1", NOOP, _decide_nothing(), NOOP, NOW, before_commit=boom,
                     no_action_reason=Counting(returns=NoActionReason.NOT_UNDERPACING))

    assert store.latest("t1").seq == row.seq
    assert store.no_action_reason("t1", row.seq + 1) is None


def test_the_store_refuses_a_reason_on_any_other_step(store):
    row = _to_analyzing(store)

    with pytest.raises(ValueError, match="不提案"):
        store.commit_step("t1", row.seq, TaskState.PROPOSED, NOW, proposal=make_proposal(),
                          no_action_reason=NoActionReason.NOT_UNDERPACING)

    assert store.latest("t1").seq == row.seq


def test_the_reader_answers_none_on_a_database_from_before_the_reason_table(store, tmp_path):
    """唯讀開法不建表:舊資料庫沒有原因表時照樣開得起來,原因一律當作沒存(展示寫「無法還原」)。"""
    _to_analyzing(store)
    flow.advance(store, "t1", NOOP, _decide_nothing(), NOOP, NOW)
    store.close()
    with sqlite3.connect(tmp_path / "analyzer.db") as conn:
        conn.execute("DROP TABLE no_action_reasons")

    reader = TaskReader(tmp_path / "analyzer.db")
    try:
        assert reader.no_action_reason("t1", 4) is None
    finally:
        reader.close()


def test_opening_an_older_database_for_writing_adds_the_reason_table(tmp_path):
    path = tmp_path / "old.db"
    TaskStore(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE no_action_reasons")

    reopened = TaskStore(path)
    try:
        reopened.create_task("t1", "c1", NOW)
        assert reopened.no_action_reason("t1", 1) is None
    finally:
        reopened.close()
