"""分析端對死信與兩個新擋下原因的處理(Phase 8):S507。

Phase 5 [S304] 的死信那一半改掉:死信可能被重放、之後真的寫進 DSP,所以收件口回死信而決策還沒
過期時分析端當成還在處理、等待;決策已過期才結案、不建接續任務。過期沒由分析端用自己的時鐘
比對提案快照的到期時間(收件口不會把死信轉成已過期)。收件口回「政策已變」或「決策已過時」就建
接續任務重新規劃。
"""

from datetime import timedelta

import pytest

from rtb.analyzer.task_store import ReplanReason, follow_up_id, replan_counts
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting
from tests.analyzer.test_replan import _handed_off, answer, step

EXPIRES = NOW + timedelta(minutes=30)  # 樣本提案的決策到期時間


# ---- [S507] ----
def test_the_analyzer_waits_on_a_live_dead_letter_and_replans_on_stale_reasons(store):
    proposal = _handed_off(store)
    before = store.latest("t1").seq

    live = step(store, Counting(returns=answer(proposal, "dead_letter")),
                now=EXPIRES - timedelta(seconds=1))

    assert live is TaskState.HANDED_OFF  # 還沒過期:可能被重放,等
    assert store.latest("t1").seq == before
    assert store.follow_up_to("t1") is None


def test_an_expired_dead_letter_closes_the_task_without_a_follow_up(store):
    proposal = _handed_off(store)

    closed = step(store, Counting(returns=answer(proposal, "dead_letter")), now=EXPIRES)

    assert closed is TaskState.BLOCKED  # 到期那一刻起算已過期(跟收件口判過期同一個邊界)
    assert "dead_letter" in store.latest("t1").error_detail
    assert store.follow_up_to("t1") is None  # 死信是暫時失敗用完,不自動重做


@pytest.mark.parametrize(("code", "reason"), [
    ("policy_version_changed", ReplanReason.POLICY_VERSION_CHANGED),
    ("decision_stale", ReplanReason.DECISION_STALE),
])
def test_a_new_block_reason_hands_the_task_over_to_a_follow_up(store, code, reason):
    proposal = _handed_off(store)

    result = step(store, Counting(returns=answer(proposal, "blocked", code)))

    assert result is TaskState.BLOCKED
    child = follow_up_id("t1")
    detail = store.latest("t1").error_detail
    assert f"blocked={code}" in detail and f"replan={reason.value}" in detail
    assert store.latest(child).state is TaskState.RECEIVED
    assert store.follow_up_of(child) == "t1"
    assert replan_counts(store).replanned == 1
    assert store._conn.execute("SELECT reason FROM follow_ups").fetchall() == [(reason.value,)]
