"""展示狀態資料 → 頁面的 DemoState(Phase 12 增量 2b):頁面只吃 `state.py` 的不可變介面,
這裡把驅動程式寫進展示狀態庫的東西照實換過去。

原則(協調者 2026-09-24 裁定):
- 路徑照實給:每一筆判斷放它真的走的那條邊,沒有邊就是空;走過的邊照時間排,可以斷開、分段,不補路。
- 例外只有兩種補上的判斷點,都標明「依存下的證據重算」或「執行端當下記下」:分析端的中間判斷點用重算的
  根據補(新鮮度 → 資料齊不齊 → 花得慢不慢 → 交給誰判斷 → 值不值得加;交給誰判斷一律是程式規則,因為
  分析端目前沒有模型入口);執行端的範圍檢查與總上限只在開始一筆那一列有記錄、而且都通過時補。
- 拿不到的留空值,不造數字:模型那一段、事後推測、對照表都是空的。
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from rtb.demo import basis as basis_of
from rtb.demo.driver import SCENARIOS
from rtb.demo.flow import FLOW_GRAPH
from rtb.demo.state import (
    ApprovalForm,
    ChangeSummary,
    Comparison,
    ComparisonRow,
    CurrentStep,
    DecidedBy,
    Decision,
    DecisionBasis,
    DecisionKind,
    DemoState,
    Disposition,
    DspCampaign,
    DspState,
    Hypothesis,
    InjectedFault,
    ModelMode,
    ModelSource,
    ModelStep,
    NodeKind,
    Scenario,
    ScenarioCode,
    ScenarioStatus,
    Stage,
    TimelineStep,
    VerifierResult,
)
from rtb.demo.state_store import (
    Basis,
    BasisCode,
    ComparisonRun,
    ConfirmationRequest,
    DecisionRow,
    ScenarioDetails,
    ScenarioRun,
    StateReader,
)

AI_ACTOR = "AI"  # 判斷紀錄裡 AI 判的那一列(觀察器照調查紀錄寫)
MODEL_MODE_REASON = "錄製回應,不是即時呼叫"
NOTHING_RECORDED = "這次一筆對得上的錄製回應都沒有,AI 那一步全部改由程式規則決定"
NOT_CALLED_REASON = "還沒有開了 AI 決策的情境跑過,這次沒有呼叫 AI"
def known_limits() -> tuple[str, ...]:
    """這次示範的範圍與限制;F7 的規模取自驅動程式當下的常數(代碼審 r2 d2:不寫死)。"""
    from rtb.demo import driver

    return (
        "這次只用本機模擬的廣告平台,沒有連到正式平台。",
        f"F7 是等比例縮小的規模({driver.F7_CAMPAIGNS} 個廣告、{driver.F7_WORKERS} 個工作者);"
        "完整規模由自動查核跑的 F7 測試證明。",
        "分析那一步讓 AI 參與決定下一步:金額、廣告與動作照舊由程式決定,"
        "AI 只選下一步(查哪種唯讀資料、值不值得加);AI 答不出或答錯格式時改由"
        "程式規則決定。",
        "判斷的根據裡,分析程式那幾組是用存下的資料重算的,不是當時記下的。",
    )


_STATUS = {"running": ScenarioStatus.RUNNING, "done": ScenarioStatus.DONE,
           "incomplete": ScenarioStatus.INCOMPLETE,
           "awaiting_confirmation": ScenarioStatus.AWAITING_APPROVAL,
           "not_exercised": ScenarioStatus.NOT_EXERCISED}
_DECIDED_BY = {"rule": DecidedBy.RULE, "ai": DecidedBy.AI_DEMO,
               "ai_fallback": DecidedBy.AI_FALLBACK}
# 分析端模式行的「rule」:即時的登入預檢沒過,整趟改由程式規則決定(沒有呼叫 AI)
_MODES = {"recorded": ModelMode.RECORDED, "live": ModelMode.LIVE, "rule": ModelMode.NOT_CALLED}
_STAGE = {
    "a_receive": Stage.RECEIVE, "a_collect": Stage.ANALYZE, "a_fresh": Stage.ANALYZE,
    "a_recollect": Stage.ANALYZE, "a_propose": Stage.PROPOSE, "x_pending": Stage.SUBMIT,
    "x_write": Stage.WRITE, "x_resend": Stage.WRITE, "x_unknown": Stage.UNKNOWN,
    "x_recheck": Stage.RECONCILE, "x_verify": Stage.RECONCILE, "x_reclaimed": Stage.RECONCILE,
    "x_blocked": Stage.BLOCK, "a_blocked_end": Stage.BLOCK, "a_no_action": Stage.BLOCK,
    "x_deadletter": Stage.BLOCK, "x_wait_approval": Stage.APPROVAL, "r_requeued": Stage.REPLAY,
    "a_followup": Stage.FOLLOW_UP, "x_done": Stage.COMPLETE,
}
_EDGE_LABEL = {(e.source, e.target): e.label for e in FLOW_GRAPH.edges}
_DECISION_NODES = frozenset(n.id for n in FLOW_GRAPH.nodes if n.kind is NodeKind.DECISION)


def _kind(node: str) -> DecisionKind:
    """判斷點上的那一步是「判斷」,其他是「狀態前進」;沒有邊的看節點本身(協調者 2026-09-24:缺了邊的
    判斷照樣標判斷,不把缺口藏成狀態前進)。"""
    return DecisionKind.JUDGEMENT if node in _DECISION_NODES else DecisionKind.PROGRESS


def numbers_digest(demo_id: str, request: ConfirmationRequest) -> str:
    """確認頁的數字摘要識別值:展示編號、提案內容雜湊、給人看的每一項數字的雜湊(設計審 r1 x3:核可綁
    展示編號與驅動程式記下的數字快照)。伺服器驗確認送出時用同一支函式重算比對。"""
    body = json.dumps([demo_id, request.proposal_hash, [list(p) for p in request.numbers]],
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _basis(items: Sequence[Basis]) -> tuple[DecisionBasis, ...]:
    return tuple(DecisionBasis(b.observed, b.standard, b.conclusion, b.source) for b in items)


def _step(node: str, target: str, row: DecisionRow, items: Sequence[Basis],
          reason: str) -> Decision:
    return Decision(node=node, taken_edge=(node, target), outcome=_EDGE_LABEL[(node, target)],
                    reason=reason, at=row.at, basis=_basis(items), operation_key=row.operation_key,
                    kind=_kind(node), task_id=row.task)


_AI_ITEMS = frozenset({BasisCode.AI_ROUND, BasisCode.AI_CITED, BasisCode.AI_FALLBACK})


def _ai_steps(row: DecisionRow) -> tuple[Decision, ...]:
    """AI 那一步(Phase 13 增量 4):先補 AI 之前的程式判斷點(新鮮度 → 齊全 → 花太慢),再放「AI 判斷」
    那一張(退回時是「改由程式規則決定」那一張,種類是判斷,不是 AI 判斷)。"""
    before = [b for b in row.basis if b.code not in _AI_ITEMS]
    steps = [_step(s.node, s.target, row, (s.basis,), s.basis.conclusion)
             for s in basis_of.ai_route(before)]
    edge = row.edge
    if edge is None:
        return tuple(steps)
    kind = DecisionKind.AI_JUDGEMENT if row.actor == AI_ACTOR else DecisionKind.JUDGEMENT
    outcome = row.reason if kind is DecisionKind.JUDGEMENT else _EDGE_LABEL.get(edge, row.reason)
    steps.append(Decision(node=edge[0], taken_edge=edge, outcome=outcome, reason=row.reason,
                          at=row.at, basis=_basis([b for b in row.basis if b.code in _AI_ITEMS]),
                          operation_key=row.operation_key, kind=kind, task_id=row.task))
    return tuple(steps)


def _route(row: DecisionRow, route: Sequence[basis_of.RouteStep]) -> tuple[Decision, ...]:
    """照補出來的判斷點畫;最後一步接回這一列本身的節點,接不回就不補。"""
    used = {id(s.basis) for s in route}
    leftover = [b for b in row.basis if id(b) not in used]  # 建議金額那一組掛在最後一步
    steps = [_step(s.node, s.target, row, (s.basis, *s.more), s.basis.conclusion)
             for s in route]
    last = steps[-1]
    steps[-1] = replace(last, reason=row.reason, basis=(*last.basis, *_basis(leftover)))
    return tuple(steps) if steps[-1].taken_edge == (route[-1].node, row.node) else ()


def _filled(row: DecisionRow) -> tuple[Decision, ...]:
    """補上的判斷點(見檔頭);補不了就是空的。最後一步接回這一列本身的節點。"""
    if row.edge is not None and row.edge[0] == "a_ai" and row.node in {"a_ai", "a_rule"}:
        return _ai_steps(row)
    held = basis_of.rule_route(row.basis, held=True) if row.node == "a_exam_hold" else ()
    if held:  # 退回之後程式規則判值得加、只判不送(代碼審 r1 p3)
        return _route(row, held)
    if any(b.code is BasisCode.AI_TAKEOVER for b in row.basis):
        # 程式接手那一列:狀態往前走(照 AI 的選擇做事),根據照樣列出做了什麼
        return (Decision(node=row.node, taken_edge=None, outcome=row.reason, reason=row.reason,
                         at=row.at, basis=_basis(row.basis), operation_key=row.operation_key,
                         kind=DecisionKind.PROGRESS, task_id=row.task),)
    if row.node not in {"a_propose", "a_no_action", "a_recollect"}:
        return _written(row)
    rule = basis_of.rule_route(row.basis)  # 退回之後的程式規則那兩步
    if rule:
        return _route(row, rule)
    analysis = basis_of.analysis_route(row.basis)
    return _route(row, analysis) if analysis else ()


def _written(row: DecisionRow) -> tuple[Decision, ...]:
    if row.node == "x_write":
        route = basis_of.write_route(row.basis)
        return tuple(_step(s.node, s.target, row, (s.basis, *s.more), row.reason)
                     for s in route)
    return ()


def _decisions(rows: Sequence[DecisionRow]) -> tuple[Decision, ...]:
    found: list[Decision] = []
    for row in rows:
        filled = _filled(row)
        if filled:
            found.extend(filled)
            continue
        edge = row.edge
        if row.node == "x_write" and basis_of.failed_checks(row.basis):
            # 核對材料有沒通過的(人確認後放行):觀察器推出來的那條「都通過」的邊跟根據矛盾,
            # 清空,不替沒看到的判斷補路(代碼審 r1 d4)
            edge = None
        node = edge[0] if edge is not None else row.node
        outcome = _EDGE_LABEL.get(edge, row.reason) if edge is not None else row.reason
        # 帶沒通過的核對材料的那一步是真的判斷(人確認後放行),照判斷顯示根據(代碼審 r2 d1)
        kind = (DecisionKind.JUDGEMENT if basis_of.failed_checks(row.basis) else _kind(node))
        found.append(Decision(node=node, taken_edge=edge, outcome=outcome, reason=row.reason,
                              at=row.at, basis=_basis(row.basis),
                              operation_key=row.operation_key, kind=kind, task_id=row.task))
    return tuple(found)


def _traversed(path: Sequence[Decision]) -> tuple[tuple[str, str], ...]:
    """真的走過的邊的集合,照第一次出現排、每條一次;不補中間沒看到的邊。好幾件工作時它不是一條
    路徑(路徑看判斷紀錄,每筆帶工作編號;代碼審 r1 d9)。"""
    return tuple(dict.fromkeys(d.taken_edge for d in path if d.taken_edge is not None))


def _timeline(rows: Sequence[DecisionRow]) -> tuple[TimelineStep, ...]:
    steps: list[TimelineStep] = []
    for row in rows:
        stage = _STAGE.get(row.node)
        if stage is not None and (not steps or steps[-1].stage is not stage):
            steps.append(TimelineStep(stage, row.at, row.reason))
    return tuple(steps)


def _explains(code: str) -> str:
    """情境在驗什麼:取情境本體說明的第一段(去掉代號);沒寫說明的用目標。"""
    scenario = SCENARIOS[code]
    doc = (scenario.run.__doc__ or "").strip().split("\n\n")[0].replace("\n", "")
    return doc.split(":", 1)[1] if doc.startswith(code) and ":" in doc else doc or (
        scenario.goal or "")


def _scenario(code: ScenarioCode, run: ScenarioRun | None, rows: Sequence[DecisionRow],  # noqa: PLR0913 - 出處多帶一個展示種類
              details: ScenarioDetails | None, demo_id: str,
              current_node: str | None, full: bool | None) -> Scenario:
    definition = SCENARIOS[code.value]
    path = _decisions(rows)
    details = details or ScenarioDetails()
    change = details.change
    hypothesis, hypothesis_note = _hypothesis(details)
    return Scenario(
        code=code, title=definition.title, what_it_tests=_explains(code.value),
        status=ScenarioStatus.PENDING if run is None else _STATUS[run.status],
        incomplete_reason=run.reason if run is not None and run.status == "incomplete" else None,
        timeline=_timeline(rows),
        dispositions=tuple(Disposition(*item) for item in details.dispositions),
        dsp=DspState(tuple(DspCampaign(*c) for c in details.platform),
                     details.platform_operations) if details.platform else None,
        audit=details.audit, model_step=_model_step(details), hypothesis=hypothesis,
        path=path,
        current_node=current_node, result_summary=(run.summary or "") if run else "",
        traversed_edges=_traversed(path), source_demo_id=demo_id,
        source_full=None if run is None else full,
        ran_at=None if run is None else run.started_at,
        model_mode=None if run is None else _mode(details),
        change_summary=None if change is None else ChangeSummary(
            change.campaign, change.before, change.after, change.written, change.reason),
        change_overview=details.change_overview, trigger=details.trigger, goal=details.goal,
        queue_wait_seconds=details.queue_wait_seconds,
        injected_faults=tuple(InjectedFault(n, d) for n, d in details.injected_faults),
        operation_key=details.operation_key, platform_apply_count=details.platform_apply_count,
        ai_enabled=details.ai_enabled, decided_by=_DECIDED_BY.get(details.decided_by or ""),
        model_mode_reason=details.model_mode_reason, outcome_note=details.outcome_note,
        exam=details.exam, answered_rounds=details.answered_rounds,
        hypothesis_note=hypothesis_note if details.ai_enabled else None,
    )


def _mode(details: ScenarioDetails) -> ModelMode:
    """這個情境分析端實際判出的模式(分析端就緒之後印的那一行);沒開 AI 決策是「這次沒有呼叫 AI」。"""
    if not details.ai_enabled:
        return ModelMode.NOT_CALLED
    return _MODES.get(details.model_mode or "", ModelMode.RECORDED)


def _json(text: str | None) -> dict[str, Any] | None:
    try:
        found = json.loads(text) if text else None
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


_SOURCES = {"recorded": ModelSource.RECORDED, "live": ModelSource.LIVE}


def _model_step(details: ScenarioDetails) -> ModelStep | None:
    """[S1027] 模型說明:程式算的數字在前,模型文字與來源在後;沒有成功結果就給結果類別。"""
    found = _json(details.narrative_json)
    if found is None:
        return None
    numbers = tuple((str(k), str(v)) for k, v in found.get("numbers", []))
    text = found.get("text") if found.get("outcome") == "ok" else None
    return ModelStep(numbers, text if isinstance(text, str) else None,
                     _SOURCES.get(str(found.get("source")), ModelSource.RECORDED),
                     str(found.get("outcome") or found.get("error") or "沒有結果"))


NO_RECORD = "這次沒有記錄"
NOT_ASKED = "沒問到,不知道有沒有告警"
NO_ALERT = "這次沒有告警,沒有請 AI 推測原因"


def _hypothesis(details: ScenarioDetails) -> tuple[Hypothesis | None, str | None]:
    """原因假說命令列的結果(代碼審 r1 p1):只有告警響、問過 AI 才有假說卡;沒有記錄(沒跑、取消、
    舊資料)、命令列沒跑完(不知道有沒有告警)、明確沒有告警,各自照實寫一句,都不畫 AI 節點。"""
    found = _json(details.hypothesis_json)
    if found is None:
        return None, NO_RECORD
    status = found.get("status")
    if status == "no_alert":
        return None, NO_ALERT
    source = _SOURCES.get(str(found.get("source") or found.get("mode")), ModelSource.RECORDED)
    alerts = "、".join(str(a) for a in found.get("alerts", [])) or "服務水準告警"
    if status == "ok":
        return Hypothesis(alerts, tuple(str(h) for h in found.get("hypotheses", [])),
                          str(found.get("next_step_shown") or found.get("next_step") or ""),
                          source), None
    if status == "failed" and found.get("reason"):  # 告警響了、問了 AI,沒給出假說
        return Hypothesis(alerts, (), f"AI 沒有給出推測(原因:{found['reason']})", source), None
    why = found.get("error") or found.get("reason") or "命令列沒有跑完"
    return None, f"{NOT_ASKED}({why})"


def _comparison(run: ComparisonRun | None) -> Comparison | None:
    """[S1041] 前後比較表取自跟自動查核同一次完整執行;說明後面標明取自哪一次、產生花了幾秒。"""
    if run is None:
        return None
    took = "" if run.seconds is None else f",產生花了 {run.seconds:.0f} 秒"
    source = (f"(取自 {run.generated_at.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC 那次完整執行,"
              f"展示編號 {run.demo_id}{took})")
    return Comparison(tuple(ComparisonRow(*row) for row in run.rows), f"{run.note}{source}")


def _line_value(lines: Sequence[str], prefix: str) -> str | None:
    return next((line.removeprefix(prefix).strip() for line in lines if line.startswith(prefix)),
                None)


def build_demo_state(  # noqa: PLR0913 - 伺服器依在跑的是哪一種展示分別指定驗證器與出處
        reader: StateReader, demo_id: str | None, *, running: bool, now: datetime,
                     verifier_demo_id: str | None = None, full_demo_id: str | None = None,
                     running_full: bool = False) -> DemoState:
    """讀一次展示狀態庫(同一個快照),組成頁面要的 DemoState。demo_id 空的是還沒有任何展示。
    驗證器只取 verifier_demo_id 那一次的(完整展示在跑時就是這一次、還沒跑到就是空的;代碼審 r1 x2);
    「現在進度」只在在跑時給(代碼審 r1 s2/p7)。"""
    shown_id = demo_id or ""
    runs = {run.code: run for run in reader.scenario_runs(shown_id)}
    current = reader.current(shown_id) if running else None
    full = reader.demo_full(shown_id)
    scenarios = tuple(
        _scenario(code, runs.get(code.value), reader.decisions(shown_id, code.value),
                  reader.scenario_details(shown_id, code.value), shown_id,
                  current.node if current is not None
                  and current.scenario == code.value else None, full)
        for code in ScenarioCode)
    verifier = None if verifier_demo_id is None else reader.verifier_run(verifier_demo_id)
    compared = None if verifier_demo_id is None else reader.comparison_run(verifier_demo_id)
    lines = verifier.lines if verifier is not None else ()
    pending = reader.confirmation(shown_id)
    approval = None if pending is None else ApprovalForm(
        proposal_hash=pending[1].proposal_hash, numbers=pending[1].numbers, narrative=None,
        source=None, demo_id=shown_id, numbers_digest=numbers_digest(shown_id, pending[1]))
    step = None
    if current is not None and current.scenario in {c.value for c in ScenarioCode}:
        last = current.last_decision
        step = CurrentStep(ScenarioCode(current.scenario), current.node, current.entered_at,
                           None if last is None else _decisions([last])[-1])
    started = min((run.started_at for run in runs.values()), default=None)
    mode, mode_reason = _demo_mode(scenarios)
    return DemoState(
        demo_id=shown_id, started_at=started, model_mode=mode,
        model_cost_usd=None, verifier_digest=_line_value(lines, "驗證器 sha256:"),
        commit=_line_value(lines, "提交編號:"), running=running, scenarios=scenarios,
        verifier=None if verifier is None else VerifierResult(
            verifier.passed, verifier.lines, verifier.reasons, verifier.verified_at,
            verifier.demo_id),
        known_limits=known_limits(), comparison=_comparison(compared), approval=approval,
        flow=FLOW_GRAPH,
        current=step, observed_at=now, model_mode_reason=mode_reason, is_sample=False,
        full_demo_id=full_demo_id, verifier_pending=running_full and verifier is None,
    )


def _demo_mode(scenarios: Sequence[Scenario]) -> tuple[ModelMode, str]:
    """頂端摘要的模型模式:照各情境分析端實際判出的模式;有情境即時就寫哪幾個即時,其餘錄製;全部錄製寫
    「錄製回應,不是即時呼叫」;還沒有開了 AI 的情境跑過就是沒有呼叫 AI。"""
    ran = [s for s in scenarios if s.model_mode not in (None, ModelMode.NOT_CALLED)]
    if not ran:
        return ModelMode.NOT_CALLED, NOT_CALLED_REASON
    live = [s.code.value for s in ran if s.model_mode is ModelMode.LIVE]
    if live:
        return ModelMode.LIVE, f"{'、'.join(live)} 即時呼叫,其餘情境{MODEL_MODE_REASON}"
    if all(s.answered_rounds == 0 for s in ran):  # 照實寫:一筆錄製都沒對上(代碼審 r1 t4)
        return ModelMode.RECORDED, f"{MODEL_MODE_REASON};{NOTHING_RECORDED}"
    return ModelMode.RECORDED, MODEL_MODE_REASON
