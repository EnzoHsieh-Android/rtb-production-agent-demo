"""判斷的根據(Phase 12 增量 2b):每一步量到的值、當時生效的標準、比較後的結論。

分析端不存根據,只存證據原文與決策結果;這裡用存下的證據呼叫正式規則的同一批函式與常數重算(協調者
2026-09-24 裁定),先把整個正式決策重跑一次,結果跟當時實際寫下的不一樣就一組都不給(不顯示對不上的
根據)。每一組都標明是重算的。執行端的根據用它開始一筆時自己記下的核對材料、以及它記的平台呼叫紀錄。
拿不到的就不給那一組,不造數字。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from rtb.analyzer import policy
from rtb.analyzer.flow import Decision, NeedsFreshEvidence, NoAction, ProposalDecision
from rtb.analyzer.task_store import TaskRow
from rtb.demo.state_store import Basis
from rtb.domain.evidence import Evidence, EvidenceKind
from rtb.domain.metrics import pacing
from rtb.domain.task_state import TaskState
from rtb.domain.worth import WorthVerdict
from rtb.executor.attempt_store import DspCallKind, DspCallResult, DspCallRow, FirstRow

RECOMPUTED = "依存下的證據重算"
RECORDED = "執行端當下記下"

STALE = "太舊,重新蒐集"
PROPOSE = "照規則算出建議金額"
_NO_ACTION = {
    "missing_state_or_metrics": "缺廣告狀態或成效資料,不調整",
    "pacing_unknown": "算不出花得快還是慢,不調整",
    "not_underpacing": "沒有花太慢,不調整",
    "judged_not_worth": "不值得加,不調整",
    "judged_insufficient": "資料不夠判斷值不值得加,不調整",
}


def expected_conclusion(state: TaskState, reason: str | None) -> str | None:
    """這個結果對應的最後一組結論(給一致性測試對照)。"""
    if state is TaskState.PROPOSED:
        return PROPOSE
    if state is TaskState.COLLECTING_EVIDENCE:
        return STALE
    return _NO_ACTION.get(reason or "")


def _agrees(decision: Decision, reason: policy.NoActionReason | None, decided: TaskRow,
            recorded_reason: str | None) -> bool:
    """[條件二] 正式決策重跑的結果跟當時寫下的那一列一樣:提案就比金額,不調整就比原因。"""
    if decided.state is TaskState.PROPOSED:
        return (isinstance(decision, ProposalDecision) and decided.proposal is not None
                and dict(decision.proposal.requested_change)
                == dict(decided.proposal.requested_change))
    if decided.state is TaskState.COLLECTING_EVIDENCE:
        return isinstance(decision, NeedsFreshEvidence)
    if decided.state is TaskState.NO_ACTION:
        return (isinstance(decision, NoAction) and reason is not None
                and reason.value == recorded_reason)
    return False


def _freshness(evidence: Sequence[Evidence], at: datetime, fresh: bool) -> Basis:
    oldest = max(((at - item.observed_at).total_seconds() for item in evidence), default=0.0)
    limit = policy.MAX_EVIDENCE_AGE.total_seconds() / 60
    return Basis(f"最舊的資料是 {oldest / 60:.1f} 分鐘前量的",
                 f"不超過 {limit:.0f} 分鐘、廣告版本沒變", "夠新" if fresh else STALE, RECOMPUTED)


def analysis(before: TaskRow, evidence: Sequence[Evidence], decided: TaskRow,
             recorded_reason: str | None) -> tuple[Basis, ...]:
    """分析那一步的根據:新鮮度、配速、值不值得加、建議金額,照正式規則的順序,停在做出決定的那一組。
    before 是分析中那一列(證據掛在它底下),decided 是分析之後寫下的那一列(它的時間就是決策時間)。"""
    at, items = decided.written_at, tuple(evidence)
    try:
        decision, reason = policy.explain(before, items, at, candidate=None,
                                          allowed=policy.ValidatedCells.NONE)
    except Exception:  # 正式規則對這份證據丟例外:當時也不會寫下這一列,不給根據
        return ()
    if not _agrees(decision, reason, decided, recorded_reason):
        return ()
    fresh = policy._all_fresh(items, at)  # 正式規則的同一支函式(條件一)
    found = [_freshness(items, at, fresh)]
    if not fresh:
        return tuple(found)
    state = policy._payload(items, EvidenceKind.CAMPAIGN_STATE)
    metrics = policy._payload(items, EvidenceKind.METRICS)
    if state is None or metrics is None:
        lacking = "、".join(n for n, v in (("廣告狀態", state), ("成效資料", metrics)) if v is None)
        found.append(Basis(f"缺{lacking}",
                           "兩樣都要有才判斷", _NO_ACTION["missing_state_or_metrics"], RECOMPUTED))
        return tuple(found)
    budget, spend = state.get("budget"), metrics.get("spend")
    ratio = pacing(spend, budget, policy.ELAPSED_FRACTION_1H)
    below = ratio.below(policy.UNDERPACING_THRESHOLD)
    shown = "算不出來" if ratio.value is None else f"{ratio.value:.0%}"
    pace_conclusion = ("花太慢" if below else _NO_ACTION["not_underpacing"] if below is False
                       else _NO_ACTION["pacing_unknown"])
    found.append(Basis(f"花費 {spend}、預算 {budget}:到現在該花的進度是 {shown}",
                       f"一天預算的 1/{round(1 / policy.ELAPSED_FRACTION_1H)} 當作這一小時該花的,"
                       f"進度低於 {policy.UNDERPACING_THRESHOLD:.0%} 算花太慢",
                       pace_conclusion, RECOMPUTED))
    if not below:
        return tuple(found)
    worth = policy._judge(state, metrics, None, policy.ValidatedCells.NONE)
    worth_conclusion = ("值得加" if worth is WorthVerdict.WORTH
                        else _NO_ACTION["judged_insufficient"]
                        if worth is WorthVerdict.INSUFFICIENT
                        else _NO_ACTION["judged_not_worth"])
    found.append(Basis(f"曝光 {metrics.get('impressions')}、點擊 {metrics.get('clicks')}",
                       "現行程式規則:廣告啟用、曝光與點擊都大於 0 才值得加", worth_conclusion,
                       RECOMPUTED))
    if isinstance(decision, ProposalDecision):
        new = decision.proposal.requested_change["new_budget"]
        found.append(Basis(f"{budget} → {new}",
                           f"加 {policy.BUDGET_INCREASE_FRACTION:.0%}(四捨五入,至少加 1)",
                           PROPOSE, RECOMPUTED))
    return tuple(found)


# 三組核對材料的標準開頭:補執行端判斷點時靠它認出是哪一組
_RATIO, _CAP, _TOTAL = "單次最多加", "單一廣告上限", "全部廣告加起來的總上限"


def write_start(first: FirstRow) -> tuple[Basis, ...]:
    """開始一筆時執行端自己記下的核對材料:這次要加多少、單次最多加多少、單一廣告上限、總上限與已用。
    舊列沒記的那一組不給。"""
    found = []
    amount = first.reserved_amount
    new = None if first.proposal is None else first.proposal.requested_change.get("new_budget")
    if amount is not None and first.ratio_allowance is not None:
        found.append(Basis(f"這次要加 {amount}",
                           f"{_RATIO} {first.ratio_allowance}(現有預算的一半)",
                           "沒超過" if amount <= first.ratio_allowance else "超過,要人確認",
                           RECORDED))
    if new is not None and first.max_budget is not None:
        found.append(Basis(f"加完是 {new}", f"{_CAP} {first.max_budget}",
                           "沒超過" if int(new) <= first.max_budget else "超過,擋下", RECORDED))
    if amount is not None and first.aggregate_limit is not None and first.used_before is not None:
        total = first.used_before + amount
        found.append(Basis(f"已經加出去 {first.used_before},加上這次共 {total}",
                           f"{_TOTAL} {first.aggregate_limit}",
                           "放得下" if total <= first.aggregate_limit
                           else "超過總上限,人確認後放行",
                           RECORDED))
    return tuple(found)


_RESULT_TEXT = {
    DspCallResult.RESPONDED: "平台回覆成功", DspCallResult.TIMEOUT: "等到逾時沒有回覆",
    DspCallResult.CONNECTION_FAILED: "連不上平台", DspCallResult.SERVER_ERROR: "平台內部錯誤",
    DspCallResult.CLIENT_ERROR: "平台拒絕", DspCallResult.UNREADABLE: "平台的回覆看不懂",
}
_KIND_TEXT = {DspCallKind.WRITE: "送出寫入", DspCallKind.LOOKUP_OPERATION: "依編號查平台",
              DspCallKind.VOID: "作廢這個編號", DspCallKind.READ_CAMPAIGN: "讀廣告現況"}


def platform_call(calls: Sequence[DspCallRow], state: str, written_at: str) -> tuple[Basis, ...]:
    """嘗試轉成不明或平台已收到時,最近一次跟平台的往來(執行端記的呼叫紀錄,同一個時鐘)。"""
    earlier = [c for c in calls if c.at <= written_at
               and c.kind in (DspCallKind.WRITE, DspCallKind.LOOKUP_OPERATION)]
    if not earlier:
        return ()
    call = earlier[-1]
    try:
        what = f"{_KIND_TEXT[DspCallKind(call.kind)]}:{_RESULT_TEXT[DspCallResult(call.result)]}"
    except (KeyError, ValueError):
        return ()
    if call.status is not None:
        what += f"(狀態 {call.status})"
    if state == "unknown":
        return (Basis(what, "平台明確回覆成功或拒絕,才知道有沒有寫進去",
                      "不知道有沒有寫進去,回頭去平台查", RECORDED),)
    if state == "committed_unverified":
        return (Basis(what, "平台說收到了或查到這個編號,才算寫進去",
                      "寫進去了,接著比對平台實際狀態", RECORDED),)
    return ()


@dataclass(frozen=True)
class RouteStep:
    """分析端補上的一個中間判斷點:從哪個判斷點、走到哪、憑哪一組根據(協調者 2026-09-24 裁定)。"""

    node: str
    target: str
    basis: Basis
    more: tuple[Basis, ...] = ()  # 同一個判斷點的其他根據(範圍檢查也看單一廣告上限)


NO_MODEL = Basis("分析端目前沒有模型入口", "有模型入口、而且這件工作在允許範圍內,才交給 AI 參考",
                 "用程式規則判斷", RECOMPUTED)


def analysis_route(found: Sequence[Basis]) -> tuple[RouteStep, ...]:  # noqa: PLR0911 - 每個判斷點一個出口
    """把分析那一步重算出的根據,換成它依序走過的判斷點:新鮮度 → 資料齊不齊 → 花得慢不慢 → 交給誰
    判斷 → 值不值得加。根據是 analysis() 在跟當時結果一致時才給的(不一致就是空的,這裡也就不補);
    每一步都照正式規則的判定走,停在做出決定的那一步。"""
    items = list(found)
    if not items:
        return ()
    steps: list[RouteStep] = []
    fresh = items.pop(0)
    if fresh.conclusion != "夠新":
        return (RouteStep("a_fresh", "a_recollect", fresh),)
    steps.append(RouteStep("a_fresh", "a_complete", fresh))
    if not items:
        return ()  # 夠新卻沒有下一組:不是 analysis() 給得出的形狀,不補
    second = items.pop(0)
    if second.conclusion == _NO_ACTION["missing_state_or_metrics"]:
        return (*steps, RouteStep("a_complete", "a_no_action", second))
    steps.append(RouteStep("a_complete", "a_pacing",
                           Basis("廣告狀態與成效資料都有", "兩樣都要有才判斷", "齊全", RECOMPUTED)))
    if second.conclusion != "花太慢":
        return (*steps, RouteStep("a_pacing", "a_no_action", second))
    steps.append(RouteStep("a_pacing", "a_route", second))
    steps.append(RouteStep("a_route", "a_rule", NO_MODEL))
    if not items:
        return ()
    worth = items.pop(0)
    steps.append(RouteStep("a_rule", "a_worth", worth))
    target = "a_propose" if worth.conclusion == "值得加" else "a_no_action"
    return (*steps, RouteStep("a_worth", target, worth))


def write_route(found: Sequence[Basis]) -> tuple[RouteStep, ...]:
    """執行端開始一筆那一列記下的核對材料(write_start 的結果)換成判斷點:範圍檢查(單次上限、單一
    廣告上限)→ 總上限 → 寫入。只補那一列有記錄、而且都通過的那幾步(協調者 2026-09-24 裁定);寫入前
    再確認的版本那一列沒記,不補。"""
    by_standard = {b.standard.split(" ")[0]: b for b in found}
    ratio, cap, total = by_standard.get(_RATIO), by_standard.get(_CAP), by_standard.get(_TOTAL)
    if ratio is None or cap is None or total is None:
        return ()
    if ratio.conclusion != "沒超過" or cap.conclusion != "沒超過" or total.conclusion != "放得下":
        return ()
    return (RouteStep("x_guard", "x_total", ratio, (cap,)), RouteStep("x_total", "x_write", total))
