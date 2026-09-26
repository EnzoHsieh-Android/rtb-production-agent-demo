"""AI 調查的決策函式(Phase 13 增量 2,計劃 [[Projects/RTB_Phase13AI參與決策_計劃]])。
Phase 14 增量 3(計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈使用者裁定〉8、〈拆增量〉3)
起 AI 退出正式與展示的加額決策,這支檔**只給評估執行器**(`rtb.eval.investigation_eval.run_case`)
直接呼叫:分析端驅動、流程層與一鍵展示都不匯入它(邊界測試守)。留下的是評估實際走到的那一段:

1. 程式先過濾:只用現況、1 小時指標、廣告文字三種證據跑 Phase 13 舊前置過濾(新鮮度、缺資料、
   配速算不出、配速不偏低);沒過就照程式規則結案,不呼叫模型([S1104])。暫停與 1 小時異常
   照 Phase 13 仍問模型,只為從既有錄製還原模型自己的原始答案(原 `raw_replay` 通道;
   正式路徑已不存在,所以不再是選項)。
   追加查詢的收據在入口濾掉,傳給程式規則的證據只有那三種([S1115])。
2. 這件工作已退回過或下過結論(看評估記憶體裡的調查紀錄)→ 退回程式規則,記一列「AI 已用過」。
3. 呼叫模型一次(同一步查完這一輪選的全部查詢,[S1128]);呼叫前經記次回呼,已達上限就退回。
4. 回答過驗證:選查詢 → `QueryMore`(紀錄帶 ai_query);propose → `RuleContinue`,評估記 AI 原始
   `propose`;do_not_propose / stop_insufficient → 不提案,原因「判不值得加」「判證據不足」。
5. 模型呼叫失敗類別與本地驗證不過 → 退回程式規則,記退回原因([S1106]);只用基本三筆判得出的當場結案,
   其餘回 `RuleContinue`,評估改取案例的九條結果 `rule_verdict(case)`。停止訊號轉成的例外
   (CallTerminated,BaseException)與 KeyboardInterrupt 不接,往外丟。不重試。

Phase 14 增量 3 撤除(沒有入口就刪):`--hold-submit` 與考題(`held_rule`、`_held`)、AI 提案規則否決
(`ai_propose_vetoed:`)、登入預檢與停止旗標檢查、續租回呼,以及正式路徑的前置過濾分支。

模型只經模型閘道(分析端唯一准匯入模型用戶端的地方,[S1100]);提案不帶任何模型產生的欄位([S1114])。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rtb.analyzer import investigation as inv
from rtb.analyzer import modelgate, policy
from rtb.analyzer.flow import Decision, NoAction, ProposalDecision, RuleContinue
from rtb.analyzer.investigation import AiContext, AiOutcome, QueryMore
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
                            recorded_ledger: Path | None = None,
                            notify: Callable[[str], None] | None = None) -> modelgate.Gate:
    """開分析端調查的模型閘道:呼叫者標籤在這裡綁死成「分析端調查」(邊界測試的 CALLER_USERS 只准這支
    模組用它)。拒絕照閘道的 GateRefused、UnknownModel 往外丟。"""
    return open_gate(environ, caller=modelgate.Caller.INVESTIGATION, demo_id=demo_id,
                     ledger=ledger, recordings=recordings, batch_id=batch_id,
                     recorded_ledger=recorded_ledger, notify=notify)


def gate_complete(gate: modelgate.Gate) -> Complete:
    """把模型閘道包成「送出一次系統提示加使用者內容」的函式(逾時 15 秒;呼叫者已在開閘道時綁死)。"""

    def complete(system: str, user: str) -> modelgate.ModelResult:
        return gate.complete(system, user, max_output_tokens=MAX_OUTPUT_TOKENS,
                             timeout_seconds=MODEL_TIMEOUT_SECONDS)

    return complete


@dataclass(frozen=True)
class Judge:
    """評估執行器用的 AI 決策函式:只有模型呼叫一樣可替換(錄製重播或協調者授權的即時加錄製,
    都經評估開的模型閘道)。"""

    complete: Complete

    def __call__(self, task: TaskRow, evidence: tuple[Evidence, ...], now: datetime,
                 context: AiContext) -> AiOutcome:
        base = inv.code_rule_evidence(evidence)
        facts = policy.steps(base, now)
        if not facts.fresh or facts.state is None or facts.metrics is None \
                or facts.underpacing is not True:
            # Phase 13 舊前置過濾:判斷點輸入建不起來、配速不偏低由程式規則結案,不送模型;暫停與
            # 1 小時異常照舊問模型(評估要還原錄製當時模型自己的答案)
            decision, reason = _rule(task, base, now)
            return AiOutcome(decision, None, reason)
        state = inv.progress(context.rounds)
        if state.used:
            return _fallback(task, base, now, state, inv.FallbackReason.AI_ALREADY_USED)
        return self._ask(task, base, now, state,
                         _Inputs(evidence, facts.state, facts.metrics, context))

    def _ask(self, task: TaskRow, base: tuple[Evidence, ...], now: datetime,
             state: inv.Progress, inputs: _Inputs) -> AiOutcome:
        receipts = inv.query_receipts(inputs.evidence)
        base_receipt = inv.base_receipt(inputs.state, inputs.metrics, HOURS_PER_BUDGET)
        name, truncated = _campaign_name(inputs.evidence)
        user = inv.prompt(base_receipt, receipts, state, name, truncated)
        if inputs.context.begin_call(inv.MAX_ROUNDS) > inv.MAX_ROUNDS:
            # 這件工作一生的模型呼叫次數(確定要呼叫的那一刻才記)已到上限:改由程式規則決定
            return _fallback(task, base, now, state, inv.FallbackReason.AI_ALREADY_USED)
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
            # AI 的 propose 只是評估裡的原始答案(Phase 14 增量 3:不建提案、不開規則輪);評估執行器
            # 看到紀錄是結論就記 AI 原始「值得加」
            return AiOutcome(RuleContinue(), record)
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


RULE_ROUND = inv.RULE_ROUND  # 退回紀錄的結果代碼:要四查詢才判得出(評估改取案例的九條結果)


def _outcome_code(decision: Decision | RuleContinue) -> str:
    if isinstance(decision, RuleContinue):
        return RULE_ROUND
    if isinstance(decision, ProposalDecision):
        return inv.Conclusion.PROPOSE.value
    return "no_action" if isinstance(decision, NoAction) else "needs_fresh_evidence"


def _fallback(task: TaskRow, base: tuple[Evidence, ...], now: datetime, state: inv.Progress,
              why: inv.FallbackReason, source: str | None = None) -> AiOutcome:
    """退回程式規則(Phase 14 起是九條):只用基本三筆就判得出的(暫停、1 小時異常)當場結案;其餘
    要四查詢,回 RuleContinue,評估執行器改取案例的九條結果(不開規則輪)。這一輪記退回原因類別。"""
    facts = policy.steps(base, now, queries=None)
    decision: Decision | RuleContinue
    if facts.needs_queries:
        decision, reason = RuleContinue(), None
    else:
        decision, reason = _rule(task, base, now)
    record = InvestigationRecord(inv.RecordKind.FALLBACK, state.next_round,
                                 _outcome_code(decision), inv.DecidedBy.RULE, fallback=why.value,
                                 model_source=source)
    return AiOutcome(decision, record, reason)


def _campaign_name(evidence: tuple[Evidence, ...]) -> tuple[str | None, bool]:
    for item in evidence:
        if item.kind is EvidenceKind.CAMPAIGN_TEXT:
            name = item.payload.get("name")
            return (name if isinstance(name, str) else None), item.payload.get("truncated") is True
    return None, False
