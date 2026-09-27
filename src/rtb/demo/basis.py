"""判斷的根據(Phase 12 增量 2b):每一步量到的值、當時生效的標準、比較後的結論。

分析端不存根據,只存證據原文與決策結果;這裡用存下的證據呼叫正式規則的同一批函式與常數重算(協調者
2026-09-24 裁定),先把整個正式決策重跑一次,結果跟當時實際寫下的不一樣就一組都不給(不顯示對不上的
根據)。每一組都標明是重算的。執行端的根據用它開始一筆時自己記下的核對材料、以及它記的平台呼叫紀錄。
拿不到的就不給那一組,不造數字。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rtb.analyzer import policy
from rtb.analyzer.flow import Decision, NeedsFreshEvidence, NoAction, ProposalDecision
from rtb.analyzer.task_store import MAX_GENERATION, TaskRow
from rtb.demo.state_store import Basis, BasisCode
from rtb.domain import nine_rules as rules
from rtb.domain.evidence import Evidence
from rtb.domain.task_state import TaskState
from rtb.domain.worth import WorthVerdict
from rtb.executor import guardrails, inbox_store
from rtb.executor.attempt_store import DspCallKind, DspCallResult, DspCallRow, FirstRow

RECOMPUTED = "依存下的證據重算"
RECORDED = "執行端當下記下"
RECORDED_INBOX = "收件口當下記下"
RECORDED_ANALYZER = "分析端當下記下"

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
    # 標準只寫規則真的做的事:看年齡;版本只跟同一批資料比(跟平台比版本是執行端的事,代碼審 r1 d1)
    return Basis(f"最舊的資料是 {oldest / 60:.1f} 分鐘前量的",
                 f"每筆資料不超過 {limit:.0f} 分鐘;版本只跟同一批資料比",
                 "夠新" if fresh else STALE, RECOMPUTED,
                 BasisCode.FRESH if fresh else BasisCode.STALE)


BASE_RULE_STANDARD = ("九條的第 1、2 條只用基本資料、排在配速之前:暫停 → 不值得加;"
                      "1 小時曝光、點擊、轉換、花費、營收有缺值或負數,"
                      "或點擊多於曝光、轉換多於點擊 → 證據不足")
NINE_RULES_STANDARD = ("正式規則是九條、由上而下先命中:暫停、1 小時資料異常、"
                       "最近 3 天調過預算、上次加額沒換到轉換、轉換率掉一半、長窗有轉換、"
                       "沒投放、有價值、沒價值;第 3 條起要四種追加查詢的有效結果,"
                       "任一沒有結果就證據不足")


def _rule_text(facts: policy.PolicySteps) -> str:
    rule = facts.rule
    if rule is None:
        return "沒有判"
    if rule.cell is not None:
        return f"命中「{rules.RULE_TEXT[rule.cell]}」"
    return f"證據不足({rule.reason.value}{'' if rule.query is None else '/' + rule.query.value})"


def analysis(before: TaskRow, evidence: Sequence[Evidence], decided: TaskRow,  # noqa: PLR0911
             recorded_reason: str | None,
             queries: rules.RuleEvidence = policy.MISSING_FOUR_QUERIES) -> tuple[Basis, ...]:
    """分析那一步的根據:新鮮度、(第 1/2 條)、配速、值不值得加、建議金額,照正式規則的順序,停在做出
    決定的那一組。before 是分析中那一列(證據掛在它底下),decided 是分析之後寫下的那一列(它的時間就是
    決策時間)。每一步的中間事實取自正式規則公開的 `policy.steps`(代碼審 r1 a1),
    這裡只把它寫成人看得懂
    的字。Phase 14 增量 2b:規則輪定案那一列由呼叫端從同一輪已存的原始回應轉好四查詢傳進來(`queries`)
    ,
    跟當時同一份;沒傳就是缺四查詢。"""
    at, items = decided.written_at, tuple(evidence)
    try:
        decision, reason = policy.explain(before, items, at, candidate=None,
                                          allowed=policy.ValidatedCells.NONE, queries=queries)
        facts = policy.steps(items, at, queries=queries)
    except Exception:  # 正式規則對這份證據丟例外:當時也不會寫下這一列,不給根據
        return ()
    if not _agrees(decision, reason, decided, recorded_reason):
        return ()
    found = [_freshness(items, at, facts.fresh)]
    if not facts.fresh:
        return tuple(found)
    state, metrics = facts.state, facts.metrics
    if state is None or metrics is None:
        lacking = "、".join(n for n, v in (("廣告狀態", state), ("成效資料", metrics)) if v is None)
        found.append(Basis(f"缺{lacking}", "兩樣都要有、狀態要是啟用或暫停才判斷",
                           _NO_ACTION["missing_state_or_metrics"], RECOMPUTED, BasisCode.MISSING))
        return tuple(found)
    if facts.settled_by_base:
        insufficient = facts.worth is WorthVerdict.INSUFFICIENT
        found.append(Basis(
            f"狀態 {state.get('status')}、曝光 {metrics.get('impressions')}、點擊 "
            f"{metrics.get('clicks')}:{_rule_text(facts)}", BASE_RULE_STANDARD,
            _NO_ACTION["judged_insufficient" if insufficient else "judged_not_worth"], RECOMPUTED,
            BasisCode.INSUFFICIENT if insufficient else BasisCode.NOT_WORTH))
        return tuple(found)
    budget, spend = state.get("budget"), metrics.get("spend")
    shown = "算不出來" if facts.pacing_ratio is None else f"{facts.pacing_ratio:.0%}"
    below = facts.underpacing
    pace = ((BasisCode.UNDERPACING, "花太慢") if below
            else (BasisCode.NOT_UNDERPACING, _NO_ACTION["not_underpacing"]) if below is False
            else (BasisCode.PACING_UNKNOWN, _NO_ACTION["pacing_unknown"]))
    found.append(Basis(f"花費 {spend}、預算 {budget}:到現在該花的進度是 {shown}",
                       f"一天預算的 1/{round(1 / policy.ELAPSED_FRACTION_1H)} 當作這一小時該花的,"
                       f"進度低於 {policy.UNDERPACING_THRESHOLD:.0%} 算花太慢",
                       pace[1], RECOMPUTED, pace[0]))
    if not below:
        return tuple(found)
    worth = ((BasisCode.WORTH, "值得加") if facts.worth is WorthVerdict.WORTH
             else (BasisCode.INSUFFICIENT, _NO_ACTION["judged_insufficient"])
             if facts.worth is WorthVerdict.INSUFFICIENT
             else (BasisCode.NOT_WORTH, _NO_ACTION["judged_not_worth"]))
    found.append(Basis(f"曝光 {metrics.get('impressions')}、點擊 {metrics.get('clicks')}:"
                       f"{_rule_text(facts)}", NINE_RULES_STANDARD, worth[1], RECOMPUTED, worth[0]))
    if isinstance(decision, ProposalDecision):
        new = decision.proposal.requested_change["new_budget"]
        found.append(Basis(f"{budget} → {new}",
                           f"加 {policy.BUDGET_INCREASE_FRACTION:.0%}(四捨五入,至少加 1)",
                           PROPOSE, RECOMPUTED, BasisCode.PROPOSE))
    return tuple(found)


def write_start(first: FirstRow) -> tuple[Basis, ...]:
    """開始一筆時執行端自己記下的核對材料:這次要加多少、單次最多加多少、單一廣告上限、總上限與已用。
    舊列沒記的那一組不給。"""
    found = []
    amount = first.reserved_amount
    new = None if first.proposal is None else first.proposal.requested_change.get("new_budget")
    if amount is not None and first.ratio_allowance is not None:
        ok = amount <= first.ratio_allowance
        ratio = (f"現有預算的 {guardrails.MAX_INCREASE_NUMERATOR}/"
                 f"{guardrails.MAX_INCREASE_DENOMINATOR},至少 {guardrails.MIN_INCREASE_STEP}")
        found.append(Basis(f"這次要加 {amount}", f"單次最多加 {first.ratio_allowance}({ratio})",
                           "沒超過" if ok else "超過,要人確認", RECORDED,
                           BasisCode.RATIO_OK if ok else BasisCode.RATIO_OVER))
    if new is not None and first.max_budget is not None:
        ok = int(new) <= first.max_budget
        found.append(Basis(f"加完是 {new}", f"單一廣告上限 {first.max_budget}",
                           "沒超過" if ok else "超過,擋下", RECORDED,
                           BasisCode.CAP_OK if ok else BasisCode.CAP_OVER))
    if amount is not None and first.aggregate_limit is not None and first.used_before is not None:
        total = first.used_before + amount
        ok = total <= first.aggregate_limit
        found.append(Basis(f"已經加出去 {first.used_before},加上這次共 {total}",
                           f"全部廣告加起來的總上限 {first.aggregate_limit}",
                           "放得下" if ok else "超過總上限,人確認後放行", RECORDED,
                           BasisCode.TOTAL_OK if ok else BasisCode.TOTAL_OVER))
    return tuple(found)


_RESULT_TEXT = {
    DspCallResult.RESPONDED: "平台回覆成功", DspCallResult.TIMEOUT: "等到逾時沒有回覆",
    DspCallResult.CONNECTION_FAILED: "連不上平台", DspCallResult.SERVER_ERROR: "平台內部錯誤",
    DspCallResult.CLIENT_ERROR: "平台拒絕", DspCallResult.UNREADABLE: "平台的回覆看不懂",
}
_NOT_FOUND = 404
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
        result = _RESULT_TEXT[DspCallResult(call.result)]
        what = f"{_KIND_TEXT[DspCallKind(call.kind)]}:"
    except (KeyError, ValueError):
        return ()
    if call.kind == DspCallKind.LOOKUP_OPERATION and call.status == _NOT_FOUND:
        result = "查不到這一筆"  # 依編號查平台回 404 不是拒絕(代碼審 r1 d10)
    what += result
    if call.status is not None:
        what += f"(狀態 {call.status})"
    if state == "unknown":
        return (Basis(what, "平台明確回覆成功或拒絕,才知道有沒有寫進去",
                      "不知道有沒有寫進去,回頭去平台查", RECORDED),)
    if state == "in_flight":  # 不明之後查不到,同編號重送
        return (Basis(what, "依編號查平台查不到這一筆,才確定沒寫進去",
                      "確定沒寫進去,同編號重送", RECORDED),)
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


def analysis_route(found: Sequence[Basis]) -> tuple[RouteStep, ...]:  # noqa: PLR0911 - 每個判斷點一個出口
    """把分析那一步重算出的根據,換成它依序走過的判斷點:新鮮度 → 資料齊不齊 → 花得慢不慢 → 程式規則
    (九條)→ 值不值得加。原本花得慢之後還有一步固定說明的「交給誰判斷」(Phase 10 候選分流),
    2026-09-27 隨流程圖撤除候選分支拿掉。根據是 analysis() 在跟當時結果一致時才給的(不一致就是空的,
    這裡也就不補);每一步都照結論代碼走(代碼審 r1 a6:不看顯示文字),停在做出決定的那一步。"""
    items = list(found)
    if not items:
        return ()
    steps: list[RouteStep] = []
    fresh = items.pop(0)
    if fresh.code is not BasisCode.FRESH:
        stale = fresh.code is BasisCode.STALE
        return (RouteStep("a_fresh", "a_recollect", fresh),) if stale else ()
    steps.append(RouteStep("a_fresh", "a_complete", fresh))
    if not items:
        return ()  # 夠新卻沒有下一組:不是 analysis() 給得出的形狀,不補
    second = items.pop(0)
    if second.code in (BasisCode.MISSING, BasisCode.NOT_WORTH, BasisCode.INSUFFICIENT):
        # 缺資料,或九條第 1/2 條只用基本資料就結案(Phase 14:排在配速之前)
        return (*steps, RouteStep("a_complete", "a_no_action", second))
    steps.append(RouteStep("a_complete", "a_pacing",
                           Basis("廣告狀態與成效資料都有", "兩樣都要有才判斷", "齊全", RECOMPUTED,
                                 BasisCode.COMPLETE)))
    if second.code is not BasisCode.UNDERPACING:
        return (*steps, RouteStep("a_pacing", "a_no_action", second))
    steps.append(RouteStep("a_pacing", "a_rule", second))
    if not items:
        return ()
    worth = items.pop(0)
    steps.append(RouteStep("a_rule", "a_worth", worth))
    target = "a_propose" if worth.code is BasisCode.WORTH else "a_no_action"
    return (*steps, RouteStep("a_worth", target, worth))


def write_route(found: Sequence[Basis]) -> tuple[RouteStep, ...]:
    """執行端開始一筆那一列記下的核對材料(write_start 的結果)換成判斷點:範圍檢查(單次上限、單一
    廣告上限)→ 總上限 → 寫入。只補那一列有記錄、而且都通過的那幾步(協調者 2026-09-24 裁定);寫入前
    再確認的版本那一列沒記,不補。依結論代碼認(代碼審 r1 a6)。"""
    by_code = {b.code: b for b in found if b.code is not None}
    ratio, cap = by_code.get(BasisCode.RATIO_OK), by_code.get(BasisCode.CAP_OK)
    total = by_code.get(BasisCode.TOTAL_OK)
    if ratio is None or cap is None or total is None:
        return ()
    return (RouteStep("x_guard", "x_total", ratio, (cap,)), RouteStep("x_total", "x_write", total))


def failed_checks(found: Sequence[Basis]) -> bool:
    """開始一筆那一列的核對材料裡有沒通過的(靠人確認放行):這一列的邊不能畫成「都通過」那條。"""
    return any(b.code in _FAILED for b in found)


_FAILED = frozenset({BasisCode.RATIO_OVER, BasisCode.CAP_OVER, BasisCode.TOTAL_OVER})


_FAILURE_TEXT = {"dsp_unavailable": "讀不到平台", "table_full": "全表未結案已滿",
                 "no_report": "處理的人沒回報"}


def lifecycle(kind: str, *, deliveries: int | None, reason: str | None,
              actor: str | None) -> tuple[Basis, ...]:
    """收件口當下記下的:投遞次數(上限讀收件口的常數)、放回排隊的原因、重新送入的操作人。"""
    limit = inbox_store.MAX_DELIVERIES
    if kind == "dead_lettered" and deliveries is not None:
        return (Basis(f"已經交出去 {deliveries} 次,都沒能開始處理", f"最多 {limit} 次",
                      "用完,停下等人處理", RECORDED_INBOX),)
    if kind == "reclaimed" and deliveries is not None:  # 接手不會多算一次投遞(代碼審 r1 d10)
        return (Basis(f"前一個處理的人沒回報,換人接手(到目前交出去 {deliveries} 次)",
                      f"最多交出去 {limit} 次", "還沒到上限,換人接手", RECORDED_INBOX),)
    if kind == "lease_released" and reason in _FAILURE_TEXT:
        return (Basis(f"這一輪沒能開始:{_FAILURE_TEXT[reason]}"
                      + ("" if deliveries is None else f"(第 {deliveries} 次交出去)"),
                      "沒能開始就放回排隊,之後再交出去", "放回排隊", RECORDED_INBOX),)
    if kind == "replay_requeued" and actor:
        return (Basis(f"操作人 {actor} 下重新送入", "只有管理指令能把停下的建議放回排隊",
                      "放回排隊,照一般流程重跑每一關", RECORDED_INBOX),)
    return ()


def stopped(*, amount: int | None, used: int | None, cap: int | None,
            capped: bool = False) -> tuple[Basis, ...]:
    """停下等人確認那一刻收件口記下的:當時已用、這次金額、總上限;沒記的、或已用額度或門檻被封頂
    (數字不是原值)的不給(代碼審 r1 d5)。"""
    if amount is None or used is None or cap is None or capped:
        return ()
    return (Basis(f"已經加出去 {used},這次要加 {amount},共 {used + amount}",
                  f"全部廣告加起來的總上限 {cap}", "超過總上限,停下等人確認", RECORDED_INBOX),)


_REPLAN_TEXT = {"version_changed": "廣告被別人改過", "expired": "建議過期",
                "after_retention": "收件紀錄已清掉、平台也查不到",
                "policy_version_changed": "規則改了", "decision_stale": "建議放太久"}


def follow_up(reason: StrEnum, generation: int | None) -> tuple[Basis, ...]:
    """開新工作:分析端記下的原因與這是第幾代接續(上限讀分析端的常數)。"""
    if generation is None:
        return ()
    return (Basis(f"{_REPLAN_TEXT.get(reason.value, reason.value)};這是第 {generation} 代",
                  f"接續最多 {MAX_GENERATION} 代", "開一件新工作照現況重新分析",
                  RECORDED_ANALYZER),)


# Phase 13 增量 4 的「AI 參與決策那一步」根據(AI 判斷列、程式接手、退回、只判不送、AI 之前的判斷點)
# 隨 Phase 14 增量 3(計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈拆增量〉3)撤除:AI 不參與
# 加額決策,展示只留規則輪的根據、提案說明與告警假說。
