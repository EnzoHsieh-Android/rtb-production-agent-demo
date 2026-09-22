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

    decision = policy.decide(task_row(), evidence)

    assert isinstance(decision, flow.ProposalDecision)
    assert decision.proposal.requested_change["new_budget"] == 110  # 漲一成,暫用值


def test_normal_pacing_is_no_action():
    # 一小時預期花費 = 100/24 ≈ 4.17,花了 4.2 已達標,不該提案
    evidence = (state_evidence(budget=100), metrics_evidence(spend=4.2, impressions=500, clicks=12))

    assert isinstance(policy.decide(None, evidence), flow.NoAction)


def test_underpacing_with_zero_delivery_is_no_action_not_a_proposal():
    evidence = (state_evidence(budget=100), metrics_evidence(spend=0.0, impressions=0, clicks=0))

    assert isinstance(policy.decide(None, evidence), flow.NoAction)


def test_zero_budget_never_raises_and_is_no_action():
    evidence = (state_evidence(budget=0), metrics_evidence(spend=1.0, impressions=500, clicks=12))

    assert isinstance(policy.decide(None, evidence), flow.NoAction)


def test_missing_metrics_never_raises_and_is_no_action():
    evidence = (state_evidence(budget=100),
                metrics_evidence(spend=None, impressions=None, clicks=None))

    assert isinstance(policy.decide(None, evidence), flow.NoAction)


def test_no_evidence_at_all_never_raises_and_is_no_action():
    assert isinstance(policy.decide(None, ()), flow.NoAction)


def test_missing_state_evidence_never_raises_and_is_no_action():
    evidence = (metrics_evidence(spend=1.0, impressions=500, clicks=12),)

    assert isinstance(policy.decide(None, evidence), flow.NoAction)
