"""示範用的最小決策規則:讓整條流程能被示範跑完,不是交接文件後面階段要做的真正業務規則。

規則:先檢查每一筆證據的新鮮度,任一筆不新鮮就回 NeedsFreshEvidence(退回重新蒐證),不拿
過時的數字做決策——分析行程當機很久才重啟時,歷史表裡的證據可能早就過時。分析端手上最新的
版本資訊就是這批證據自己讀到的版本,所以這裡實際起作用的只有年齡;「跟 DSP 現況比版本」是
執行行程執行前重讀 DSP 時的事(Phase 3),不在這裡假裝做了。
接著用增量 1 metrics.py 既有的 pacing() 判斷配速,缺值或不知道一律 NoAction,不猜、不丟例外。
配速明顯偏低(暫用門檻 0.5)且曝光、點擊都大於零(真的有在投放,不是設定壞了)才提案調高預算
(固定漲一成,暫用值)。

已知限制:這支示範規則永遠把提案的修訂序號當成 1,不會追蹤同一個任務先前送過幾次修訂;
一個任務被收件口退回(SubmitStale)之後重新分析,示範規則不會自動送出下一個修訂——這個限制
不影響 S49 的合約(規則本身的判斷邏輯),留給接上真正決策邏輯的後面階段一併解決。
"""

from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from rtb.analyzer.flow import Decision, NeedsFreshEvidence, NoAction, ProposalDecision
from rtb.analyzer.task_store import TaskRow
from rtb.domain._checks import is_plain_number
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass, check_freshness
from rtb.domain.metrics import pacing
from rtb.domain.proposal import MAX_INT, ActionType, Proposal

POLICY_VERSION = "demo-pacing-v1"
UNDERPACING_THRESHOLD = 0.5  # 暫用值:配速低於這個比例才算「明顯偏低」
BUDGET_INCREASE_FRACTION = 0.1  # 暫用值:提案調高一成
ELAPSED_FRACTION_1H = 1 / 24  # 這個增量只讀 1 小時窗,對應一天預算的 1/24
DECISION_LIFETIME = timedelta(minutes=30)  # 決策有效期,遠低於增量 3 的 1 小時上限
MAX_EVIDENCE_AGE = timedelta(minutes=15)  # 暫用值:證據超過這個年齡就重新蒐證,比決策有效期短


def _payload(evidence: tuple[Evidence, ...], kind: EvidenceKind) -> dict[str, Any] | None:
    """決策只讀可信證據(Phase 7 增量 3):按種類找之外再加信任標記。證據型別的成對規則已讓不可信
    文字掛不到現況或指標底下,這一條是把意圖寫在讀取端,不靠上游自律;廣告文字仍在證據裡、
    仍被提案引用、仍做新鮮度判斷,只是不拿來決定動作或金額。"""
    for item in evidence:
        if item.kind == kind and item.trust_class is TrustClass.TRUSTED:
            return dict(item.payload)
    return None


def _all_fresh(evidence: tuple[Evidence, ...], now: datetime) -> bool:
    versions = [e.campaign_version_observed for e in evidence
                if e.kind == EvidenceKind.CAMPAIGN_STATE
                and e.campaign_version_observed is not None]
    latest_known = max(versions) if versions else None  # 分析端知道的最新版本,見檔頭說明
    return all(
        check_freshness(item, now, MAX_EVIDENCE_AGE.total_seconds(), latest_known).is_usable
        for item in evidence
    )


def decide(task: TaskRow | None, evidence: tuple[Evidence, ...], now: datetime) -> Decision:
    """`now` 由流程層傳進來(`advance()` 手上、也寫進歷史列的同一個時間),這裡不自己讀系統時鐘。"""
    if not _all_fresh(evidence, now):
        return NeedsFreshEvidence()
    state = _payload(evidence, EvidenceKind.CAMPAIGN_STATE)
    metrics = _payload(evidence, EvidenceKind.METRICS)
    if state is None or metrics is None:
        return NoAction()

    budget = state.get("budget")
    spend = metrics.get("spend")
    impressions, clicks = metrics.get("impressions"), metrics.get("clicks")
    underpacing = pacing(spend, budget, ELAPSED_FRACTION_1H).below(UNDERPACING_THRESHOLD)
    has_delivery = is_plain_number(impressions) and is_plain_number(clicks) \
        and impressions > 0 and clicks > 0
    if underpacing is not True or not has_delivery:
        return NoAction()

    if task is None:
        raise AssertionError("有真的證據可以決策,task 不該是 None")
    if not is_plain_number(budget):  # underpacing 已經驗過,這裡只是給型別檢查看
        raise AssertionError("underpacing 為 True 時 budget 一定是數字")
    # 小額預算乘 1.1 四捨五入可能還是原值(例如 1、2),那就不是「調高」;用 max 保證至少 +1。
    # 上限截在 Proposal 允許的最大值:budget 已經逼近上限時,示範規則寧可送出「漲到上限」的
    # 提案,也不要讓 Proposal 建構式丟例外、被上層的廣義例外處理悶成 FAILED。
    new_budget = min(max(round(budget * (1 + BUDGET_INCREASE_FRACTION)), int(budget) + 1),
                     MAX_INT)
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
