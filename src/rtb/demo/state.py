# ruff: noqa: RUF002
"""展示頁與後續展示伺服器共用的不可變狀態介面。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ModelMode(Enum):
    """這次展示的模型呼叫模式。"""

    RECORDED = "錄製"
    LIVE = "即時"
    NOT_CALLED = "這次沒有呼叫 AI"  # 目前分析端沒有模型入口(代碼審 r1 p6:不預設成錄製)


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
    AWAITING_APPROVAL = "等你確認"
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


class NodeOwner(Enum):
    CODE = "程式"
    AI = "AI"
    HUMAN = "人工"
    EXTERNAL = "外部平台"


@dataclass(frozen=True, slots=True)
class FlowNode:
    id: str
    label: str
    kind: NodeKind
    lane: str
    owner: NodeOwner = NodeOwner.CODE


@dataclass(frozen=True, slots=True)
class FlowEdge:
    source: str
    target: str
    label: str


@dataclass(frozen=True, slots=True)
class FlowGraph:
    nodes: tuple[FlowNode, ...]
    edges: tuple[FlowEdge, ...]


class DecisionKind(Enum):
    """這一步是真的判斷,還是只是狀態往前走(後者不需要數字根據)。"""

    JUDGEMENT = "判斷"
    PROGRESS = "狀態前進"


@dataclass(frozen=True, slots=True)
class DecisionBasis:
    """一次判斷所用的實測值、標準與比較結論。"""

    observed: str
    standard: str
    conclusion: str
    source: str | None = None  # 這組根據從哪來,例如「依存下的證據重算」「執行端當下記下」


@dataclass(frozen=True, slots=True)
class Decision:
    node: str
    taken_edge: tuple[str, str] | None  # 對不到圖上的邊時留空,不猜一條(代碼審第 3 輪)
    outcome: str
    reason: str
    at: datetime | None
    basis: tuple[DecisionBasis, ...] = ()
    operation_key: str | None = None
    kind: DecisionKind | None = None  # None:舊資料與範例資料,照判斷顯示
    task_id: str | None = None  # 哪一件工作(同一個情境有好幾件工作時分得開)


@dataclass(frozen=True, slots=True)
class TimelineStep:
    stage: Stage
    at: datetime
    detail: str


@dataclass(frozen=True, slots=True)
class Disposition:
    category: str
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
class ChangeSummary:
    """情境結束時，廣告平台上的預算是否真的變動。金額是平台上的預算原樣整數，沒有幣別。"""

    campaign: str
    before: int | None
    after: int | None
    written: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class InjectedFault:
    """展示刻意製造的故障及其在正式流程圖上的位置。"""

    node: str
    description: str


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
    verified_at: datetime
    demo_id: str


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
    demo_id: str
    numbers_digest: str


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
    # 這次走過的邊的集合(照第一次出現排、每條一次);好幾件工作時不是一條路徑,路徑看 path
    # (代碼審 r1 d9)
    traversed_edges: tuple[tuple[str, str], ...] = ()
    source_demo_id: str | None = None
    # 出處那一次是全部跑一次(True)還是單一情境重跑(False);沒記是空的
    source_full: bool | None = None
    ran_at: datetime | None = None
    model_mode: ModelMode | None = None
    change_summary: ChangeSummary | None = None
    change_overview: str | None = None  # 多個廣告的情境(F7)一行彙總:放行、人工寫入、沒寫入與總額
    trigger: str | None = None
    goal: str | None = None
    queue_wait_seconds: int | None = None
    injected_faults: tuple[InjectedFault, ...] = ()
    operation_key: str | None = None
    platform_apply_count: int | None = None


@dataclass(frozen=True, slots=True)
class CurrentStep:
    scenario: ScenarioCode
    node: str
    started_at: datetime
    last_decision: Decision | None


@dataclass(frozen=True, slots=True)
class DemoState:
    demo_id: str
    started_at: datetime | None  # 還沒有任何展示時是空的
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
    last_full_run_cost_usd: Decimal | None = None
    model_mode_reason: str | None = None
    is_sample: bool = False
    full_demo_id: str | None = None  # 最近一次跑完的全部跑一次;空的就是還沒有完整執行過
    verifier_pending: bool = False  # 完整展示在跑、這次的自動查核還沒跑到
    report_note: str | None = None  # 上一次另存報告失敗的原因(伺服器記下,頁面照實顯示)
