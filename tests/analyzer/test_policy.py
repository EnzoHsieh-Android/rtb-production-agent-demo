"""示範用的最小決策規則:S49。重申:這不是交接文件後面階段要做的真正業務邏輯。"""

from types import MappingProxyType

from rtb.analyzer import flow, policy
from rtb.analyzer.task_store import TaskRow
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW


def task_row():
    return TaskRow(task_id="t1", seq=3, state=TaskState.ANALYZING, campaign_id="c1",
                   proposal=None, error_detail=None, written_at=NOW)


def state_evidence(budget=100):
    return Evidence(
        evidence_id="e-state", task_id="t1", kind=EvidenceKind.CAMPAIGN_STATE, source="dsp",
        observed_at=NOW, campaign_version_observed=1, content_hash="a" * 64,
        trust_class=TrustClass.TRUSTED, payload=MappingProxyType({"budget": budget}),
    )


def metrics_evidence(spend=None, impressions=None, clicks=None):
    return Evidence(
        evidence_id="e-metrics", task_id="t1", kind=EvidenceKind.METRICS, source="dsp",
        observed_at=NOW, campaign_version_observed=None, content_hash="b" * 64,
        trust_class=TrustClass.TRUSTED,
        payload=MappingProxyType({"spend": spend, "impressions": impressions, "clicks": clicks}),
    )


def test_underpacing_with_real_delivery_proposes_a_budget_increase():
    evidence = (state_evidence(budget=100), metrics_evidence(spend=1.0, impressions=500, clicks=12))

    decision = policy.decide(task_row(), evidence, now=NOW)

    assert isinstance(decision, flow.ProposalDecision)
    assert decision.proposal.requested_change["new_budget"] == 110  # 漲一成,暫用值


def test_normal_pacing_is_no_action():
    # 一小時預期花費 = 100/24 ≈ 4.17,花了 4.2 已達標,不該提案
    evidence = (state_evidence(budget=100), metrics_evidence(spend=4.2, impressions=500, clicks=12))

    assert isinstance(policy.decide(None, evidence, now=NOW), flow.NoAction)


def test_underpacing_with_zero_delivery_is_no_action_not_a_proposal():
    evidence = (state_evidence(budget=100), metrics_evidence(spend=0.0, impressions=0, clicks=0))

    assert isinstance(policy.decide(None, evidence, now=NOW), flow.NoAction)


def test_zero_budget_never_raises_and_is_no_action():
    evidence = (state_evidence(budget=0), metrics_evidence(spend=1.0, impressions=500, clicks=12))

    assert isinstance(policy.decide(None, evidence, now=NOW), flow.NoAction)


def test_missing_metrics_never_raises_and_is_no_action():
    evidence = (state_evidence(budget=100),
                metrics_evidence(spend=None, impressions=None, clicks=None))

    assert isinstance(policy.decide(None, evidence, now=NOW), flow.NoAction)


def test_no_evidence_at_all_never_raises_and_is_no_action():
    assert isinstance(policy.decide(None, (), now=NOW), flow.NoAction)


def test_missing_state_evidence_never_raises_and_is_no_action():
    evidence = (metrics_evidence(spend=1.0, impressions=500, clicks=12),)

    assert isinstance(policy.decide(None, evidence, now=NOW), flow.NoAction)


def test_a_small_budget_that_would_round_to_no_change_still_strictly_increases():
    # budget=1 時 round(1 * 1.1) == round(1.1) == 1:字面上的公式對小額預算算不出漲幅。
    evidence = (state_evidence(budget=1), metrics_evidence(spend=0.0, impressions=500, clicks=12))

    decision = policy.decide(task_row(), evidence, now=NOW)

    assert isinstance(decision, flow.ProposalDecision)
    assert decision.proposal.requested_change["new_budget"] > 1


def test_a_budget_near_the_proposal_ceiling_is_clamped_instead_of_raising():
    from rtb.domain.proposal import MAX_INT

    near_ceiling = MAX_INT - 1
    evidence = (state_evidence(budget=near_ceiling),
                metrics_evidence(spend=0.0, impressions=500, clicks=12))

    # 漲一成的公式算出來會超過上限,不該丟例外
    decision = policy.decide(task_row(), evidence, now=NOW)

    assert isinstance(decision, flow.ProposalDecision)
    assert decision.proposal.requested_change["new_budget"] == MAX_INT


def test_boolean_impressions_or_clicks_do_not_count_as_real_delivery():
    # bool 是 int 的子類別(True == 1);has_delivery 要跟領域層其他數值檢查一樣排除它,
    # 不然壞掉的資料來源送 True/False 會被誤判成「有在投放」。
    evidence = (state_evidence(budget=100),
                metrics_evidence(spend=0.0, impressions=True, clicks=True))

    assert isinstance(policy.decide(None, evidence, now=NOW), flow.NoAction)


# ---- 決策前先檢查證據新鮮度(2026-09-22 合約審計發現:新鮮度判斷原本沒有任何流程在呼叫) ----
def test_evidence_older_than_the_age_limit_asks_for_fresh_evidence_instead_of_deciding():
    from datetime import timedelta

    evidence = (state_evidence(budget=100), metrics_evidence(spend=0.0, impressions=500, clicks=12))
    later = NOW + policy.MAX_EVIDENCE_AGE + timedelta(seconds=1)

    decision = policy.decide(task_row(), evidence, now=later)

    assert isinstance(decision, flow.NeedsFreshEvidence)  # 明明配速偏低,也不能拿過時的證據提案


def test_evidence_exactly_at_the_age_limit_is_still_used():
    evidence = (state_evidence(budget=100), metrics_evidence(spend=0.0, impressions=500, clicks=12))

    decision = policy.decide(task_row(), evidence, now=NOW + policy.MAX_EVIDENCE_AGE)

    assert isinstance(decision, flow.ProposalDecision)


def test_one_stale_piece_is_enough_to_ask_for_fresh_evidence():
    from dataclasses import replace
    from datetime import timedelta

    old_metrics = replace(metrics_evidence(spend=0.0, impressions=500, clicks=12),
                          observed_at=NOW - policy.MAX_EVIDENCE_AGE - timedelta(seconds=1))
    evidence = (state_evidence(budget=100), old_metrics)

    assert isinstance(policy.decide(task_row(), evidence, now=NOW), flow.NeedsFreshEvidence)


def test_a_restarted_analyzer_does_not_decide_on_evidence_that_went_stale_while_it_was_down(store):
    """事故情境:分析行程在「分析」這一步之前當機,隔很久才重啟;存在歷史表裡的證據已經過時,
    重啟後推進這個任務必須退回重新蒐證,不能拿舊數字做決策。"""
    from datetime import timedelta

    store.create_task("t1", "c1", NOW)
    store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, NOW)
    store.commit_step("t1", 2, TaskState.ANALYZING, NOW, evidence=(
        state_evidence(budget=100), metrics_evidence(spend=0.0, impressions=500, clicks=12)))
    restarted_at = NOW + timedelta(hours=3)

    state = flow.advance(store, "t1", lambda _t, _n: (), policy.decide, lambda _p: None,
                         restarted_at)

    assert state is TaskState.COLLECTING_EVIDENCE
