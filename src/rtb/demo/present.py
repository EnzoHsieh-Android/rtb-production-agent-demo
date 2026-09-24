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
from datetime import datetime

from rtb.demo import basis as basis_of
from rtb.demo.driver import SCENARIOS
from rtb.demo.flow import FLOW_GRAPH
from rtb.demo.state import (
    ApprovalForm,
    ChangeSummary,
    CurrentStep,
    Decision,
    DecisionBasis,
    DecisionKind,
    DemoState,
    Disposition,
    DspCampaign,
    DspState,
    InjectedFault,
    ModelMode,
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
    ConfirmationRequest,
    DecisionRow,
    ScenarioDetails,
    ScenarioRun,
    StateReader,
)

MODEL_MODE_REASON = "目前分析端沒有模型入口,這次沒有呼叫模型"
KNOWN_LIMITS = (
    "這次只用本機模擬的廣告平台,沒有連到正式平台。",
    "F7 是等比例縮小的規模(300 個廣告、8 個工作者);完整規模由驗證器跑的 F7 測試證明。",
    "分析端目前沒有模型入口:對抗文字只驗了程式規則那一段,模型那一段等之後接上再驗。",
    "判斷的根據裡,分析端那幾組是用存下的證據重算的,不是分析端當下記下的。",
)
_STATUS = {"running": ScenarioStatus.RUNNING, "done": ScenarioStatus.DONE,
           "incomplete": ScenarioStatus.INCOMPLETE,
           "awaiting_confirmation": ScenarioStatus.AWAITING_APPROVAL}
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


def _step(node: str, target: str, at: datetime, items: Sequence[Basis], reason: str,
          key: str | None) -> Decision:
    return Decision(node=node, taken_edge=(node, target), outcome=_EDGE_LABEL[(node, target)],
                    reason=reason, at=at, basis=_basis(items), operation_key=key,
                    kind=_kind(node))


def _filled(row: DecisionRow) -> tuple[Decision, ...]:
    """補上的判斷點(見檔頭);補不了就是空的。最後一步接回這一列本身的節點。"""
    analysis = basis_of.analysis_route(row.basis) if row.node in {
        "a_propose", "a_no_action", "a_recollect"} else ()
    if analysis:
        used = {id(s.basis) for s in analysis}
        leftover = [b for b in row.basis if id(b) not in used]  # 建議金額那一組掛在最後一步
        steps = [_step(s.node, s.target, row.at, (s.basis, *s.more), s.basis.conclusion,
                       row.operation_key) for s in analysis]
        last = steps[-1]
        steps[-1] = Decision(last.node, last.taken_edge, last.outcome, row.reason, row.at,
                             (*last.basis, *_basis(leftover)), row.operation_key, last.kind)
        return tuple(steps) if steps[-1].taken_edge == (analysis[-1].node, row.node) else ()
    if row.node == "x_write":
        route = basis_of.write_route(row.basis)
        return tuple(_step(s.node, s.target, row.at, (s.basis, *s.more), row.reason,
                           row.operation_key) for s in route)
    return ()


def _decisions(rows: Sequence[DecisionRow]) -> tuple[Decision, ...]:
    found: list[Decision] = []
    for row in rows:
        filled = _filled(row)
        if filled:
            found.extend(filled)
            continue
        node = row.edge[0] if row.edge is not None else row.node
        outcome = _EDGE_LABEL.get(row.edge, row.reason) if row.edge is not None else row.reason
        found.append(Decision(node=node, taken_edge=row.edge, outcome=outcome, reason=row.reason,
                              at=row.at, basis=_basis(row.basis),
                              operation_key=row.operation_key, kind=_kind(node)))
    return tuple(found)


def _traversed(path: Sequence[Decision]) -> tuple[tuple[str, str], ...]:
    """真的走過的邊,照時間排;連續重複的只留一次,不補中間沒看到的邊。"""
    edges: list[tuple[str, str]] = []
    for decision in path:
        if decision.taken_edge is not None and (not edges or edges[-1] != decision.taken_edge):
            edges.append(decision.taken_edge)
    return tuple(edges)


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


def _scenario(code: ScenarioCode, run: ScenarioRun | None, rows: Sequence[DecisionRow],
              details: ScenarioDetails | None, demo_id: str,
              current_node: str | None) -> Scenario:
    definition = SCENARIOS[code.value]
    path = _decisions(rows)
    details = details or ScenarioDetails()
    change = details.change
    return Scenario(
        code=code, title=definition.title, what_it_tests=_explains(code.value),
        status=ScenarioStatus.PENDING if run is None else _STATUS[run.status],
        incomplete_reason=run.reason if run is not None and run.status == "incomplete" else None,
        timeline=_timeline(rows),
        dispositions=tuple(Disposition(*item) for item in details.dispositions),
        dsp=DspState(tuple(DspCampaign(*c) for c in details.platform),
                     details.platform_operations) if details.platform else None,
        audit=details.audit, model_step=None, hypothesis=None, path=path,
        current_node=current_node, result_summary=(run.summary or "") if run else "",
        traversed_edges=_traversed(path), source_demo_id=demo_id,
        ran_at=None if run is None else run.started_at,
        model_mode=None if run is None else ModelMode.RECORDED,
        change_summary=None if change is None else ChangeSummary(
            change.campaign, change.before, change.after, change.written, change.reason),
        change_overview=details.change_overview, trigger=details.trigger, goal=details.goal,
        queue_wait_seconds=details.queue_wait_seconds,
        injected_faults=tuple(InjectedFault(n, d) for n, d in details.injected_faults),
        operation_key=details.operation_key, platform_apply_count=details.platform_apply_count,
    )


def _line_value(lines: Sequence[str], prefix: str) -> str | None:
    return next((line.removeprefix(prefix).strip() for line in lines if line.startswith(prefix)),
                None)


def build_demo_state(reader: StateReader, demo_id: str, *, running: bool,
                     now: datetime) -> DemoState:
    """讀一次展示狀態庫(同一個快照),組成頁面要的 DemoState。"""
    runs = {run.code: run for run in reader.scenario_runs(demo_id)}
    current = reader.current(demo_id)
    scenarios = tuple(
        _scenario(code, runs.get(code.value), reader.decisions(demo_id, code.value),
                  reader.scenario_details(demo_id, code.value), demo_id,
                  current.node if running and current is not None
                  and current.scenario == code.value else None)
        for code in ScenarioCode)
    verifier = reader.latest_verifier_run()
    lines = verifier.lines if verifier is not None else ()
    pending = reader.confirmation(demo_id)
    approval = None if pending is None else ApprovalForm(
        proposal_hash=pending[1].proposal_hash, numbers=pending[1].numbers, narrative=None,
        source=None, demo_id=demo_id, numbers_digest=numbers_digest(demo_id, pending[1]))
    step = None
    if current is not None and current.scenario in {c.value for c in ScenarioCode}:
        last = current.last_decision
        step = CurrentStep(ScenarioCode(current.scenario), current.node, current.entered_at,
                           None if last is None else _decisions([last])[-1])
    started = min((run.started_at for run in runs.values()), default=now)
    return DemoState(
        demo_id=demo_id, started_at=started, model_mode=ModelMode.RECORDED,
        model_cost_usd=None, verifier_digest=_line_value(lines, "驗證器 sha256:"),
        commit=_line_value(lines, "提交編號:"), running=running, scenarios=scenarios,
        verifier=None if verifier is None else VerifierResult(
            verifier.passed, verifier.lines, verifier.reasons, verifier.verified_at,
            verifier.demo_id),
        known_limits=KNOWN_LIMITS, comparison=None, approval=approval, flow=FLOW_GRAPH,
        current=step, observed_at=now, model_mode_reason=MODEL_MODE_REASON, is_sample=False,
    )
