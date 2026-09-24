# ruff: noqa: RUF002
"""展示頁與後續展示伺服器共用的不可變狀態介面。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ModelMode(Enum):
    """這次展示的模型呼叫模式。"""

    RECORDED = "錄製"
    LIVE = "即時"


class ModelSource(Enum):
    """單段模型文字的來源。"""

    LIVE = "即時"
    RECORDED = "錄製"


class ScenarioCode(Enum):
    """Phase 12 的七個事故情境。"""

    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    F4 = "F4"
    F5 = "F5"
    F6 = "F6"
    F7 = "F7"


class ScenarioStatus(Enum):
    """情境本身是否完整跑完；不把系統擋下誤當展示失敗。"""

    PENDING = "尚未開始"
    RUNNING = "正在執行"
    DONE = "結果符合預期"
    INCOMPLETE = "未完成"


class Stage(Enum):
    """任務時間線會顯示的階段。"""

    RECEIVE = "收到工作"
    ANALYZE = "查看資料"
    PROPOSE = "準備調整建議"
    SUBMIT = "送出建議"
    PRECHECK = "寫入前再確認"
    WRITE = "寫入廣告平台"
    UNKNOWN = "平台未明確回覆"
    RECONCILE = "回頭確認結果"
    BLOCK = "停止處理"
    APPROVAL = "等待人工確認"
    REPLAY = "人工重新送入"
    FOLLOW_UP = "建立後續工作"
    COMPLETE = "完成"


class NodeKind(Enum):
    """流程圖節點形狀。"""

    STEP = "一般步驟"
    DECISION = "判斷點"
    TERMINAL = "結束點"


@dataclass(frozen=True, slots=True)
class FlowNode:
    id: str
    label: str
    kind: NodeKind
    lane: str


@dataclass(frozen=True, slots=True)
class FlowEdge:
    source: str
    target: str
    label: str


@dataclass(frozen=True, slots=True)
class FlowGraph:
    nodes: tuple[FlowNode, ...]
    edges: tuple[FlowEdge, ...]


@dataclass(frozen=True, slots=True)
class Decision:
    node: str
    taken_edge: tuple[str, str]
    outcome: str
    reason: str
    at: datetime | None


@dataclass(frozen=True, slots=True)
class TimelineStep:
    stage: Stage
    at: datetime
    detail: str


@dataclass(frozen=True, slots=True)
class Disposition:
    code: str
    explanation: str


@dataclass(frozen=True, slots=True)
class DspCampaign:
    campaign: str
    budget: int
    version: int
    status: str


@dataclass(frozen=True, slots=True)
class DspState:
    campaigns: tuple[DspCampaign, ...]
    operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelStep:
    numbers: tuple[tuple[str, str], ...]
    narrative: str | None
    source: ModelSource
    result_kind: str


@dataclass(frozen=True, slots=True)
class Hypothesis:
    alert: str
    hypotheses: tuple[str, ...]
    next_step: str
    source: ModelSource


@dataclass(frozen=True, slots=True)
class VerifierResult:
    passed: bool
    lines: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    forgery: str
    without_verifier: str
    with_verifier: str


@dataclass(frozen=True, slots=True)
class Comparison:
    rows: tuple[ComparisonRow, ...]
    note: str


@dataclass(frozen=True, slots=True)
class ApprovalForm:
    proposal_hash: str
    numbers: tuple[tuple[str, str], ...]
    narrative: str | None
    source: ModelSource | None


@dataclass(frozen=True, slots=True)
class Scenario:
    code: ScenarioCode
    title: str
    what_it_tests: str
    status: ScenarioStatus
    incomplete_reason: str | None
    timeline: tuple[TimelineStep, ...]
    dispositions: tuple[Disposition, ...]
    dsp: DspState | None
    audit: tuple[str, ...]
    model_step: ModelStep | None
    hypothesis: Hypothesis | None
    path: tuple[Decision, ...]
    current_node: str | None
    result_summary: str = ""
    traversed_edges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CurrentStep:
    scenario: ScenarioCode
    node: str
    started_at: datetime
    last_decision: Decision | None


@dataclass(frozen=True, slots=True)
class DemoState:
    demo_id: str
    started_at: datetime
    model_mode: ModelMode
    model_cost_usd: Decimal | None
    verifier_digest: str | None
    commit: str | None
    running: bool
    scenarios: tuple[Scenario, ...]
    verifier: VerifierResult | None
    known_limits: tuple[str, ...]
    comparison: Comparison | None
    approval: ApprovalForm | None
    flow: FlowGraph
    current: CurrentStep | None
    observed_at: datetime | None = None
