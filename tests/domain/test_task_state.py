"""任務狀態機:合法與非法轉換、終點狀態沒有出路、表本身完整。"""

import pytest

from rtb.domain.task_state import (
    TERMINAL_STATES,
    TRANSITIONS,
    IllegalTransition,
    TaskState,
    can_transition,
    transition,
)

S = TaskState

LEGAL = [
    (S.RECEIVED, S.COLLECTING_EVIDENCE),
    (S.COLLECTING_EVIDENCE, S.ANALYZING),
    (S.ANALYZING, S.PROPOSED),
    (S.ANALYZING, S.NO_ACTION),
    (S.PROPOSED, S.HANDED_OFF),
    (S.HANDED_OFF, S.COMPLETED),
    (S.HANDED_OFF, S.BLOCKED),
    (S.HANDED_OFF, S.SUPERSEDED),
    (S.ANALYZING, S.COLLECTING_EVIDENCE),  # 證據過期,重新蒐集
    (S.PROPOSED, S.COLLECTING_EVIDENCE),  # 提案過期,重新規劃
]

ILLEGAL = [
    (S.RECEIVED, S.ANALYZING),  # 跳過蒐集證據
    (S.RECEIVED, S.PROPOSED),
    (S.COLLECTING_EVIDENCE, S.PROPOSED),  # 沒分析就提案
    (S.ANALYZING, S.HANDED_OFF),  # 沒提案就交接
    (S.PROPOSED, S.COMPLETED),  # 沒交接就完成
    (S.HANDED_OFF, S.COLLECTING_EVIDENCE),  # 交接之後分析端不能重來,只能走新的版本序號
    (S.HANDED_OFF, S.ANALYZING),
    (S.RECEIVED, S.RECEIVED),  # 不能原地轉換
    (S.ANALYZING, S.ANALYZING),
]


@pytest.mark.parametrize("current,target", LEGAL)
def test_legal_transitions_are_accepted(current, target):
    assert can_transition(current, target) is True
    assert transition(current, target) == target


@pytest.mark.parametrize("current,target", ILLEGAL)
def test_illegal_transitions_are_rejected_with_both_states_named(current, target):
    assert can_transition(current, target) is False
    with pytest.raises(IllegalTransition) as caught:
        transition(current, target)
    assert current.value in str(caught.value) and target.value in str(caught.value)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_way_out(terminal):
    assert TRANSITIONS[terminal] == frozenset()
    for other in TaskState:
        assert can_transition(terminal, other) is False


def test_every_non_terminal_state_can_fail():
    for state in TaskState:
        if state not in TERMINAL_STATES:
            assert can_transition(state, S.FAILED), state


def test_the_table_is_complete_and_has_no_dangling_targets():
    assert set(TRANSITIONS) == set(TaskState)
    for targets in TRANSITIONS.values():
        assert targets <= set(TaskState)


def test_terminal_states_are_exactly_the_five_documented_ones():
    assert TERMINAL_STATES == {S.COMPLETED, S.FAILED, S.BLOCKED, S.NO_ACTION, S.SUPERSEDED}


def test_unknown_or_non_state_values_are_illegal_not_a_crash_or_a_pass():
    with pytest.raises(IllegalTransition):
        transition(S.RECEIVED, "bogus")
    with pytest.raises(IllegalTransition):
        transition("bogus", S.RECEIVED)
    assert can_transition(S.RECEIVED, None) is False


def test_state_names_are_stable_strings_because_they_will_be_stored():
    assert {s.value for s in TaskState} == {
        "received", "collecting_evidence", "analyzing", "proposed", "handed_off",
        "completed", "failed", "blocked", "no_action", "superseded",
    }


def test_the_transition_table_cannot_be_modified_from_outside():
    with pytest.raises(TypeError):
        TRANSITIONS[S.FAILED] = frozenset({S.RECEIVED})  # 「終點沒有出路」要靠結構保證,不只靠測試
    assert can_transition(S.FAILED, S.RECEIVED) is False
