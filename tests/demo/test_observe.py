"""把系統紀錄觀察成判斷紀錄(Phase 12 設計審 r2 n7):每筆判斷帶來源,白話取自流程圖對應表;
回頭轉換落在展開節點;交叉核對比必經節點的順序,允許的回頭多出現不算對不上(設計審 r3 m6)。"""

from datetime import UTC, datetime, timedelta

import pytest

from rtb.analyzer.policy import NoActionReason
from rtb.demo import flow
from rtb.demo.observe import UNRECOVERABLE, PathBuilder, SourceEvent, missing_from_path
from rtb.domain.attempt import AttemptState
from rtb.domain.task_state import TaskState
from rtb.executor.inbox_store import BlockCode, LifecycleKind

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _event(i, primary, detail=None, entity="k1", detail_missing=False):
    return SourceEvent(T0 + timedelta(seconds=i), f"src#{i}", primary, detail, entity,
                       detail_missing)


def test_a_same_key_resend_lands_on_the_unrolled_back_node():
    builder = PathBuilder()
    states = ["IN_FLIGHT", "UNKNOWN", "IN_FLIGHT", "COMMITTED_UNVERIFIED", "VERIFIED"]

    rows = builder.add([_event(i, (AttemptState, s)) for i, s in enumerate(states)])

    assert [r.node for r in rows] == ["x_write", "x_unknown", "x_resend", "x_verify", "x_done"]
    assert rows[2].edge == ("x_unknown", "x_resend")
    assert all(r.origin.startswith("src#") for r in rows)


def test_a_detail_member_gives_the_specific_branch_and_plain_reason():
    rows = PathBuilder().add([_event(0, (LifecycleKind, "BLOCKED"),
                                     (BlockCode, "VERSION_CHANGED"))])

    assert rows[0].node == "x_blocked"
    assert rows[0].edge == ("x_precheck", "x_blocked")
    assert rows[0].reason == flow.OUTCOMES[(BlockCode, "VERSION_CHANGED")].text
    assert rows[0].outcome == "BlockCode.VERSION_CHANGED"


def test_a_no_action_reason_picks_its_branch():
    rows = PathBuilder().add([_event(0, (TaskState, "NO_ACTION"),
                                     (NoActionReason, "NOT_UNDERPACING"), entity="t1")])

    assert rows[0].edge == ("a_pacing", "a_no_action")


def test_a_reason_the_system_did_not_keep_is_shown_as_unrecoverable():
    """[S1048] 系統沒有保存原因的節點:判斷紀錄寫「無法還原」,不留白也不臆測。"""
    rows = PathBuilder().add([_event(0, (TaskState, "NO_ACTION"), None, entity="t1",
                                     detail_missing=True)])

    assert rows[0].node == "a_no_action"
    assert rows[0].reason == UNRECOVERABLE


def test_an_unknown_member_is_shown_as_unrecoverable_not_dropped():
    rows = PathBuilder().add([_event(0, (LifecycleKind, "BLOCKED"), (BlockCode, "made_up"))])

    assert rows[0].node == "x_blocked"
    assert rows[0].reason == UNRECOVERABLE


def test_an_analysis_that_goes_back_for_fresh_evidence_is_unrolled():
    states = ["RECEIVED", "COLLECTING_EVIDENCE", "ANALYZING", "COLLECTING_EVIDENCE"]
    rows = PathBuilder().add([_event(i, (TaskState, s), entity="t1")
                              for i, s in enumerate(states)])

    assert [r.node for r in rows] == ["a_receive", "a_collect", "a_fresh", "a_recollect"]


def test_back_transitions_are_tracked_per_entity():
    """兩把鍵交錯:一把的不明不會讓另一把的送出被當成重送。"""
    rows = PathBuilder().add([_event(0, (AttemptState, "UNKNOWN"), entity="k1"),
                              _event(1, (AttemptState, "IN_FLIGHT"), entity="k2")])

    assert [r.node for r in rows] == ["x_unknown", "x_write"]


@pytest.mark.parametrize(("observed", "missing"), [
    (["x_write", "x_unknown", "x_resend", "x_verify", "x_done"], None),
    (["x_write", "x_unknown", "x_recheck", "x_unknown", "x_resend", "x_verify", "x_done"], None),
    (["x_write", "x_verify", "x_done"], "x_unknown"),
    (["x_write", "x_unknown", "x_done", "x_resend"], "x_verify"),
    (["x_done", "x_verify", "x_resend", "x_unknown", "x_write"], "x_unknown"),  # 都有、順序倒
])
def test_an_extra_allowed_loop_still_counts_as_done(observed, missing):
    """[S1064] 必經節點要照順序出現;允許的回頭(稍後再查)多出現不算對不上。"""
    required = ["x_write", "x_unknown", "x_resend", "x_verify", "x_done"]
    assert missing_from_path(observed, required) == missing


def test_a_follow_up_shows_the_new_task_not_a_dead_end(tmp_path):
    """擋下後開新工作:觀察到的是「開一件新工作」那條邊,不是「被擋下,這件工作結束」。"""
    from rtb.analyzer.task_store import FollowUp, ReplanReason, TaskStore
    from rtb.demo.observe import Observer

    store = TaskStore(tmp_path / "analyzer.db")
    try:
        now = datetime.now(UTC)
        store.create_task("t1", "c1", now)
        for state in (TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING):
            store.commit_step("t1", store.latest("t1").seq, state, now)
        from tests.analyzer.conftest import make_proposal
        store.commit_step("t1", store.latest("t1").seq, TaskState.PROPOSED, now,
                          proposal=make_proposal(task_id="t1", campaign_id="c1"))
        store.commit_step("t1", store.latest("t1").seq, TaskState.HANDED_OFF, now,
                          proposal=make_proposal(task_id="t1", campaign_id="c1"))
        store.commit_step("t1", store.latest("t1").seq, TaskState.BLOCKED, now,
                          error_detail="blocked=version_changed",
                          follow_up=FollowUp(ReplanReason.VERSION_CHANGED))
    finally:
        store.close()

    rows = PathBuilder().add(Observer(tmp_path / "analyzer.db", tmp_path / "none.db").poll())

    nodes = [r.node for r in rows]
    assert "a_followup" in nodes and "a_blocked_end" not in nodes
    follow = next(r for r in rows if r.node == "a_followup")
    assert follow.edge == ("x_blocked", "a_followup")
    assert follow.origin.startswith("analyzer.follow_ups#")
