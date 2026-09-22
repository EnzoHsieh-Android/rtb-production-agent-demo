"""示範用的最小決策規則:讓整條流程能被示範跑完,不是交接文件後面階段要做的真正業務規則。

規則:用增量 1 metrics.py 既有的 pacing() 判斷配速,缺值或不知道一律 NoAction,不猜、不丟例外。
配速明顯偏低(暫用門檻 0.5)且曝光、點擊都大於零(真的有在投放,不是設定壞了)才提案調高預算
(固定漲一成,暫用值)。

已知限制:這支示範規則永遠把提案的修訂序號當成 1,不會追蹤同一個任務先前送過幾次修訂;
一個任務被收件口退回(SubmitStale)之後重新分析,示範規則不會自動送出下一個修訂——這個限制
不影響 S49 的合約(規則本身的判斷邏輯),留給接上真正決策邏輯的後面階段一併解決。
"""

from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

from rtb.analyzer.flow import Decision, NoAction, ProposalDecision
from rtb.analyzer.task_store import TaskRow
from rtb.domain.evidence import Evidence, EvidenceKind
from rtb.domain.metrics import pacing
from rtb.domain.proposal import ActionType, Proposal

POLICY_VERSION = "demo-pacing-v1"
UNDERPACING_THRESHOLD = 0.5  # 暫用值:配速低於這個比例才算「明顯偏低」
BUDGET_INCREASE_FRACTION = 0.1  # 暫用值:提案調高一成
ELAPSED_FRACTION_1H = 1 / 24  # 這個增量只讀 1 小時窗,對應一天預算的 1/24
DECISION_LIFETIME = timedelta(minutes=30)  # 決策有效期,遠低於增量 3 的 1 小時上限


def _payload(evidence: tuple[Evidence, ...], kind: EvidenceKind) -> dict[str, Any] | None:
    for item in evidence:
        if item.kind == kind:
            return dict(item.payload)
    return None


def decide(task: TaskRow | None, evidence: tuple[Evidence, ...]) -> Decision:
    state = _payload(evidence, EvidenceKind.CAMPAIGN_STATE)
    metrics = _payload(evidence, EvidenceKind.METRICS)
    if state is None or metrics is None:
        return NoAction()

    budget = state.get("budget")
    spend = metrics.get("spend")
    impressions, clicks = metrics.get("impressions"), metrics.get("clicks")
    underpacing = pacing(spend, budget, ELAPSED_FRACTION_1H).below(UNDERPACING_THRESHOLD)
    has_delivery = isinstance(impressions, int | float) and isinstance(clicks, int | float) \
        and impressions > 0 and clicks > 0
    if underpacing is not True or not has_delivery:
        return NoAction()

    if task is None:
        raise AssertionError("有真的證據可以決策,task 不該是 None")
    if not isinstance(budget, int | float):  # underpacing 已經驗過,這裡只是給型別檢查看
        raise AssertionError("underpacing 為 True 時 budget 一定是數字")
    new_budget = round(budget * (1 + BUDGET_INCREASE_FRACTION))
    now = datetime.now(UTC)
    proposal = Proposal(
        task_id=task.task_id, revision=1, campaign_id=task.campaign_id,
        action_type=ActionType.UPDATE_BUDGET,
        requested_change=MappingProxyType({"new_budget": new_budget}),
        reason_codes=("low_pacing",), evidence_refs=tuple(item.evidence_id for item in evidence),
        campaign_version_observed=state.get("version") or 1,
        decision_created_at=now, decision_expires_at=now + DECISION_LIFETIME,
        policy_version=POLICY_VERSION,
        risk_summary=f"budget +{int(BUDGET_INCREASE_FRACTION * 100)}%",
    )
    return ProposalDecision(proposal)
