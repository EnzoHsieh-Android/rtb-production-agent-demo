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

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Protocol

from rtb.analyzer.flow import Decision, NeedsFreshEvidence, NoAction, ProposalDecision
from rtb.analyzer.task_store import TaskRow
from rtb.domain._checks import is_plain_number
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass, check_freshness
from rtb.domain.metrics import pacing
from rtb.domain.proposal import MAX_INT, POLICY_VERSION, ActionType, Proposal
from rtb.domain.worth import (
    CampaignStatus,
    WorthCell,
    WorthInput,
    WorthInputInvalid,
    WorthVerdict,
    cell_of,
)

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


class WorthCandidate(Protocol):
    """「值不值得加」的候選判斷(Phase 10)。比照決策函式的介面用型別協定;逾時用專案既有的合作式
    做法:實作必須經共用 HTTP 用戶端呼叫、把 `timeout_seconds` 當它的逾時(逾時丟 TimeoutError)。"""

    def __call__(self, worth_input: WorthInput, timeout_seconds: float) -> WorthVerdict: ...


# 只有信任的呼叫端(評估套件的採用函式、測試)匯入它建已驗證清單;模組內用它建空的 NONE。比照執行端
# 交易物件的簽發者寫法(不是資料類別,就沒有 dataclasses.replace 可繞)
_VALIDATED_CELLS_ISSUER = object()


class ValidatedCells:
    """已驗證、允許呼叫候選的評分格。建構時要帶簽發者哨兵,空清單也一樣(代碼審第 1、2 輪);
    不是資料類別,沒有 dataclasses.replace 可繞;cells 是唯讀屬性。比照執行端交易物件的最小形狀,
    威脅模型是防忘記、不防刻意繞過:刻意去改內部屬性擋不住。正式路徑拿到的是空的 NONE。"""

    __slots__ = ("_cells",)
    NONE: ClassVar[ValidatedCells]

    def __init__(self, cells: frozenset[WorthCell], issuer: object) -> None:
        if issuer is not _VALIDATED_CELLS_ISSUER:
            raise ValueError("已驗證清單只能由採用函式建立")
        self._cells = frozenset(cells)

    @property
    def cells(self) -> frozenset[WorthCell]:
        return self._cells


ValidatedCells.NONE = ValidatedCells(frozenset(), _VALIDATED_CELLS_ISSUER)


@dataclass(frozen=True)
class CandidateCall:
    """一個候選連同它的逾時秒數:有候選才需要逾時,逾時沒有預設值(比照共用 HTTP 用戶端)。"""

    judge: WorthCandidate
    timeout_seconds: float


@dataclass(frozen=True)
class TrialCells:
    """評估入口指定「這次試著呼叫候選」的格(Phase 10 增量 2,[S716])。跟已驗證清單是不同型別:
    只在那一次評估裡用,不會被輸出或存成已驗證清單,正式路徑不會拿到它。"""

    cells: frozenset[WorthCell]


class RoutePath(StrEnum):
    CODE_RULE = "code_rule"
    CANDIDATE = "candidate"
    FALLBACK_EXCEPTION = "fallback_exception"
    FALLBACK_TIMEOUT = "fallback_timeout"
    FALLBACK_INVALID = "fallback_invalid"
    FALLBACK_UNSURE = "fallback_unsure"


@dataclass(frozen=True)
class RouteResult:
    verdict: WorthVerdict
    path: RoutePath


class NoActionReason(StrEnum):
    """評估用的「為什麼沒提案」;不放進決策結果(不做那個型別不加欄位,相等比較照舊)。"""

    STALE_EVIDENCE = "stale_evidence"
    MISSING_STATE_OR_METRICS = "missing_state_or_metrics"
    PACING_UNKNOWN = "pacing_unknown"
    NOT_UNDERPACING = "not_underpacing"
    JUDGED_NOT_WORTH = "judged_not_worth"
    JUDGED_INSUFFICIENT = "judged_insufficient"


def _has_delivery(impressions: object, clicks: object) -> bool:
    """現行程式規則:曝光與點擊都是正數就值得加(Phase 2 起的示範規則,抽出來之前原樣的判斷式)。"""
    return is_plain_number(impressions) and is_plain_number(clicks) \
        and impressions > 0 and clicks > 0


def code_rule(worth_input: WorthInput) -> WorthVerdict:
    """現行程式規則:只產出值得加或不值得加,不看狀態與轉換營收(Phase 10 的評估記成程式缺陷)。"""
    if _has_delivery(worth_input.impressions, worth_input.clicks):
        return WorthVerdict.WORTH
    return WorthVerdict.NOT_WORTH


def _candidate_answer(candidate: CandidateCall, worth_input: WorthInput) -> RouteResult:
    try:
        answer = candidate.judge(worth_input, candidate.timeout_seconds)
    except (MemoryError, RecursionError):  # 行程本身出事,不是候選判斷失敗:照舊往外丟
        raise
    except TimeoutError:
        return RouteResult(code_rule(worth_input), RoutePath.FALLBACK_TIMEOUT)
    except Exception:  # 候選是可替換的外部判斷:任何失敗都退回現行規則,不讓它改變流程狀態
        return RouteResult(code_rule(worth_input), RoutePath.FALLBACK_EXCEPTION)
    if not isinstance(answer, WorthVerdict):
        return RouteResult(code_rule(worth_input), RoutePath.FALLBACK_INVALID)
    if answer is WorthVerdict.UNSURE:
        return RouteResult(code_rule(worth_input), RoutePath.FALLBACK_UNSURE)
    return RouteResult(answer, RoutePath.CANDIDATE)


def route(
    worth_input: WorthInput, candidate: CandidateCall | None,
    allowed: ValidatedCells | TrialCells,
) -> RouteResult:
    """只在「有候選、而且輸入所屬評分格在允許清單上」時交給候選,其餘走現行程式規則([S701])。
    允許清單在正式路徑是已驗證清單,在評估入口是待測格清單。"""
    if candidate is None or cell_of(worth_input) not in allowed.cells:
        return RouteResult(code_rule(worth_input), RoutePath.CODE_RULE)
    return _candidate_answer(candidate, worth_input)


def _worth_input(state: dict[str, Any], metrics: dict[str, Any]) -> WorthInput:
    status = state.get("status")
    if not isinstance(status, str) or status not in {s.value for s in CampaignStatus}:
        raise WorthInputInvalid(f"狀態不是啟用或暫停:{status!r}")
    budget: Any = state.get("budget")  # 型別由判斷點輸入的建構驗證把關
    return WorthInput(
        status=CampaignStatus(status), budget=budget, spend=metrics.get("spend"),
        impressions=metrics.get("impressions"), clicks=metrics.get("clicks"),
        conversions=metrics.get("conversions"), revenue=metrics.get("revenue"))


def _judge(
    state: dict[str, Any], metrics: dict[str, Any], candidate: CandidateCall | None,
    allowed: ValidatedCells,
) -> WorthVerdict:
    try:
        worth_input = _worth_input(state, metrics)
    except WorthInputInvalid:  # 歸不了格:照原樣的判斷式走現行規則(正式路徑上白名單已擋掉同樣的值)
        has_delivery = _has_delivery(metrics.get("impressions"), metrics.get("clicks"))
        return WorthVerdict.WORTH if has_delivery else WorthVerdict.NOT_WORTH
    return route(worth_input, candidate, allowed).verdict


@dataclass(frozen=True)
class PolicySteps:
    """決策規則每一步的中間事實,照規則的順序,停在做出決定的那一步(後面的是空的)。給 `explain`
    自己用,也給展示頁重算判斷的根據(Phase 12 代碼審 r1 a1:外面不再直接呼叫這裡的私有函式、
    也不再自己另算一次配速)。唯讀:不寫任何東西、不讀時鐘。"""

    fresh: bool  # 每一筆證據都夠新
    state: MappingProxyType[str, Any] | None  # 可信的廣告現況(沒有就是空的)
    metrics: MappingProxyType[str, Any] | None  # 可信的成效資料
    pacing_ratio: float | None  # 到現在該花的進度(算不出來是空的)
    underpacing: bool | None  # 花太慢嗎;算不出來是空的
    worth: WorthVerdict | None  # 值不值得加(只在花太慢時判)


def steps(
    evidence: tuple[Evidence, ...], now: datetime, *,
    candidate: CandidateCall | None = None, allowed: ValidatedCells = ValidatedCells.NONE,
) -> PolicySteps:
    """照規則的順序一步一步算,停在做出決定的那一步;候選只在花太慢時才會被呼叫(跟原本一樣)。"""
    if not _all_fresh(evidence, now):
        return PolicySteps(False, None, None, None, None, None)
    state = _payload(evidence, EvidenceKind.CAMPAIGN_STATE)
    metrics = _payload(evidence, EvidenceKind.METRICS)
    frozen = (None if state is None else MappingProxyType(state),
              None if metrics is None else MappingProxyType(metrics))
    if state is None or metrics is None:
        return PolicySteps(True, *frozen, None, None, None)
    ratio = pacing(metrics.get("spend"), state.get("budget"), ELAPSED_FRACTION_1H)
    underpacing = ratio.below(UNDERPACING_THRESHOLD)
    if not underpacing:
        return PolicySteps(True, *frozen, ratio.value, underpacing, None)
    return PolicySteps(True, *frozen, ratio.value, True,
                       _judge(state, metrics, candidate, allowed))


def explain(  # noqa: PLR0911 - 每個出口對應一種不做的原因
    task: TaskRow | None, evidence: tuple[Evidence, ...], now: datetime, *,
    candidate: CandidateCall | None, allowed: ValidatedCells,
) -> tuple[Decision, NoActionReason | None]:
    """決策結果加「為什麼沒提案」(評估用,[S705]);決策結果那一半就是 `decide` 的回傳值,
    `decide` 對某筆輸入丟例外時這裡丟同一種。候選與允許清單都要明寫(`decide` 傳沒有候選、空清單),
    非空的只給評估入口用。每一步的中間事實由 `steps` 算(同一份邏輯)。"""
    facts = steps(evidence, now, candidate=candidate, allowed=allowed)
    if not facts.fresh:
        return NeedsFreshEvidence(), NoActionReason.STALE_EVIDENCE
    state, metrics = facts.state, facts.metrics
    if state is None or metrics is None:
        return NoAction(), NoActionReason.MISSING_STATE_OR_METRICS

    budget = state.get("budget")
    if facts.underpacing is None:
        return NoAction(), NoActionReason.PACING_UNKNOWN
    if facts.underpacing is False:
        return NoAction(), NoActionReason.NOT_UNDERPACING
    verdict = facts.worth
    if verdict is WorthVerdict.INSUFFICIENT:
        return NoAction(), NoActionReason.JUDGED_INSUFFICIENT
    if verdict is not WorthVerdict.WORTH:
        return NoAction(), NoActionReason.JUDGED_NOT_WORTH

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
    return ProposalDecision(proposal), None


def decide(task: TaskRow | None, evidence: tuple[Evidence, ...], now: datetime) -> Decision:
    """`now` 由流程層傳進來(`advance()` 手上、也寫進歷史列的同一個時間),這裡不自己讀系統時鐘。
    正式路徑沒有候選、允許清單是空的,所以「值不值得加」永遠走現行程式規則([S704])。"""
    return explain(task, evidence, now, candidate=None, allowed=ValidatedCells.NONE)[0]
