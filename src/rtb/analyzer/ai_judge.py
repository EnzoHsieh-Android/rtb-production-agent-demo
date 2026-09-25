"""AI 決策函式(Phase 13 增量 2,計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈一件工作的一生〉
〈退回程式規則〉〈提案與金額〉):開了 AI 決策時,流程層在「分析中」那一步呼叫這裡。

1. 程式先過濾:只用現況、1 小時指標、廣告文字三種證據跑現行規則的前四道(新鮮度、缺資料、配速算不出、
   配速不偏低);沒過就照現行規則結案或重蒐證,不呼叫模型([S1104])。追加查詢的收據在入口濾掉,傳給現行
   決策函式、不提案原因與建提案的證據只有那三種([S1115])。
2. 這件工作已退回過或下過結論(看已提交的調查紀錄)→ 直接用現行規則,記一列「AI 已用過」([S1138]);
   登入預檢沒過 → 整趟用現行規則,記「登入預檢沒過」([S1160])。
3. 續租前、呼叫模型前各看一次停止旗標,已收到就丟 RenewalSkipped、這一步不寫([S1161]);經流程層的
   續租回呼續租一次([S1135]),再呼叫模型一次(同一步查完這一輪選的全部查詢,[S1128])。
4. 回答過驗證:選查詢 → 回蒐集證據(紀錄帶 ai_query);propose → 照同一個公式建提案([S1107]);
   do_not_propose / stop_insufficient → 不提案,原因沿用「判不值得加」「判證據不足」。
5. 模型呼叫失敗類別與本地驗證不過 → 現行規則照同一批三種證據決定,記退回原因([S1106]);停止訊號轉成的
   例外(CallTerminated,BaseException)與 KeyboardInterrupt 不接,往外丟。不重試。
6. --hold-submit 清單裡的廣告:不論 AI 或規則判提案,一律改成不提案、原因「考題結束」([S1156])。

模型只經模型閘道(分析端唯一准匯入模型用戶端的地方,[S1100]);提案不帶任何模型產生的欄位([S1114])。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rtb.analyzer import investigation as inv
from rtb.analyzer import modelgate, policy
from rtb.analyzer.flow import (
    AiContext,
    AiOutcome,
    Decision,
    NoAction,
    ProposalDecision,
    QueryMore,
    RenewalSkipped,
)
from rtb.analyzer.task_store import InvestigationRecord, TaskRow
from rtb.domain.evidence import Evidence, EvidenceKind
from rtb.stepbudget import MODEL_TIMEOUT_SECONDS

MAX_OUTPUT_TOKENS = 1024
HOURS_PER_BUDGET = round(1 / policy.ELAPSED_FRACTION_1H)  # 配速比的分母:一天預算的 1/24
Complete = Callable[[str, str], modelgate.ModelResult]


GateOpener = Callable[..., modelgate.Gate]


def open_investigation_gate(environ: Mapping[str, str], *, demo_id: str | None,  # noqa: PLR0913 - 閘道要的每一樣
                            ledger: Path | None, recordings: Path | None, batch_id: str | None,
                            open_gate: GateOpener = modelgate.open_gate,
                            recorded_ledger: Path | None = None) -> modelgate.Gate:
    """開分析端調查的模型閘道:呼叫者標籤在這裡綁死成「分析端調查」(邊界測試的 CALLER_USERS 只准這支
    模組用它)。拒絕照閘道的 GateRefused、UnknownModel 往外丟。"""
    return open_gate(environ, caller=modelgate.Caller.INVESTIGATION, demo_id=demo_id,
                     ledger=ledger, recordings=recordings, batch_id=batch_id,
                     recorded_ledger=recorded_ledger)


def gate_complete(gate: modelgate.Gate) -> Complete:
    """把模型閘道包成「送出一次系統提示加使用者內容」的函式(逾時 15 秒;呼叫者已在開閘道時綁死)。"""

    def complete(system: str, user: str) -> modelgate.ModelResult:
        return gate.complete(system, user, max_output_tokens=MAX_OUTPUT_TOKENS,
                             timeout_seconds=MODEL_TIMEOUT_SECONDS)

    return complete


@dataclass(frozen=True)
class Judge:
    """一個 runner(或評估執行器)用的 AI 決策函式:模型呼叫、登入預檢結果、停止旗標、
    只判不送的清單。"""

    complete: Complete
    preflight_ok: bool = True
    stop_requested: Callable[[], bool] = field(default=lambda: False)
    hold: frozenset[str] = frozenset()

    def __call__(self, task: TaskRow, evidence: tuple[Evidence, ...], now: datetime,
                 context: AiContext) -> AiOutcome:
        return _held(self._judge(task, evidence, now, context), task, self.hold)

    def _judge(self, task: TaskRow, evidence: tuple[Evidence, ...], now: datetime,
               context: AiContext) -> AiOutcome:
        base = inv.code_rule_evidence(evidence)
        facts = policy.steps(base, now)
        if not facts.fresh or facts.state is None or facts.metrics is None \
                or facts.underpacing is not True:
            decision, reason = _rule(task, base, now)
            return AiOutcome(decision, None, reason)
        state = inv.progress(context.rounds)
        if state.used:
            return _fallback(task, base, now, state, inv.FallbackReason.AI_ALREADY_USED)
        if not self.preflight_ok:
            return _fallback(task, base, now, state, inv.FallbackReason.PREFLIGHT_FAILED)
        if self.stop_requested():
            raise RenewalSkipped("已收到停止:不續租、不呼叫模型")
        context.renew()
        if self.stop_requested():
            raise RenewalSkipped("已收到停止:不呼叫模型")
        return self._ask(task, base, now, state,
                         _Inputs(evidence, facts.state, facts.metrics, context))

    def _ask(self, task: TaskRow, base: tuple[Evidence, ...], now: datetime,
             state: inv.Progress, inputs: _Inputs) -> AiOutcome:
        receipts = inv.query_receipts(inputs.evidence)
        base_receipt = inv.base_receipt(inputs.state, inputs.metrics, HOURS_PER_BUDGET)
        name, truncated = _campaign_name(inputs.evidence)
        user = inv.prompt(base_receipt, receipts, state, name, truncated)
        if self.stop_requested():  # 組提示期間收到停止:不呼叫模型、這一步不寫(代碼審 r1 c1)
            raise RenewalSkipped("已收到停止:不呼叫模型")
        if inputs.context.begin_call(inv.MAX_ROUNDS) > inv.MAX_ROUNDS:
            # 這件工作一生的模型呼叫次數(確定要呼叫的那一刻才記,代碼審 r1 s2、r2 v3)已到上限:
            # 模型付過費、提交卻一直沒寫進去時,不再重付,改由程式規則決定
            return _fallback(task, base, now, state, inv.FallbackReason.AI_ALREADY_USED)
        if self.stop_requested():  # 記次等鎖期間收到停止(代碼審 r3 w1):已記的次數照算,不呼叫
            raise RenewalSkipped("已收到停止:不呼叫模型")
        try:
            result = self.complete(inv.SYSTEM_PROMPT, user)
        except modelgate.ModelCallFailed as failed:
            return _fallback(task, base, now, state, inv.FallbackReason(failed.outcome.value))
        refs: dict[str, inv.Receipt] = {inv.BASE_REF: base_receipt}
        refs.update({o.value: receipts[o] for o in state.queried if o in receipts})
        allowed = inv.allowed_choices(state)
        try:
            answer = inv.parse_answer(result.text, allowed, inv.query_budget(state), refs)
        except inv.OffMenu:
            return _fallback(task, base, now, state, inv.FallbackReason.OFF_MENU,
                             result.source.value)
        record = InvestigationRecord(
            inv.RecordKind.QUERY if answer.conclusion is None else inv.RecordKind.CONCLUSION,
            state.next_round, answer.conclusion or ",".join(q.value for q in answer.queries),
            inv.DecidedBy.AI, reason=answer.reason,
            reason_code=inv.AI_QUERY if answer.conclusion is None else None,
            cited_json=answer.cited_json(), model_source=result.source.value)
        if answer.conclusion is None:
            return AiOutcome(QueryMore(), record)
        if answer.conclusion is inv.Conclusion.PROPOSE:
            return AiOutcome(ProposalDecision(policy.build_proposal(task, base, inputs.state, now)),
                             record)
        reason = (policy.NoActionReason.JUDGED_NOT_WORTH
                  if answer.conclusion is inv.Conclusion.DO_NOT_PROPOSE
                  else policy.NoActionReason.JUDGED_INSUFFICIENT)
        return AiOutcome(NoAction(), record, reason)


@dataclass(frozen=True)
class _Inputs:
    evidence: tuple[Evidence, ...]
    state: Any
    metrics: Any
    context: AiContext


def _rule(task: TaskRow, base: tuple[Evidence, ...],
          now: datetime) -> tuple[Decision, policy.NoActionReason | None]:
    """現行規則(沒有候選、空的允許清單),只拿現況、1 小時指標、廣告文字三種證據。"""
    return policy.explain(task, base, now, candidate=None, allowed=policy.ValidatedCells.NONE)


def _outcome_code(decision: Decision) -> str:
    if isinstance(decision, ProposalDecision):
        return inv.Conclusion.PROPOSE.value
    return "no_action" if isinstance(decision, NoAction) else "needs_fresh_evidence"


def _fallback(task: TaskRow, base: tuple[Evidence, ...], now: datetime, state: inv.Progress,
              why: inv.FallbackReason, source: str | None = None) -> AiOutcome:
    """退回現行規則:照同一批三種證據決定,結果跟完全沒開 AI 時一字不差;這一輪記退回原因類別。"""
    decision, reason = _rule(task, base, now)
    record = InvestigationRecord(inv.RecordKind.FALLBACK, state.next_round,
                                 _outcome_code(decision), inv.DecidedBy.RULE, fallback=why.value,
                                 model_source=source)
    return AiOutcome(decision, record, reason)


def _held(outcome: AiOutcome, task: TaskRow, hold: frozenset[str]) -> AiOutcome:
    """只判不送(--hold-submit):清單裡的廣告被判提案時,改成不提案、原因考題結束;紀錄照記誰判的。"""
    if task.campaign_id in hold and isinstance(outcome.result, ProposalDecision):
        return AiOutcome(NoAction(), outcome.record, policy.NoActionReason.EXAM_HOLD)
    return outcome


def _campaign_name(evidence: tuple[Evidence, ...]) -> tuple[str | None, bool]:
    for item in evidence:
        if item.kind is EvidenceKind.CAMPAIGN_TEXT:
            name = item.payload.get("name")
            return (name if isinstance(name, str) else None), item.payload.get("truncated") is True
    return None, False
