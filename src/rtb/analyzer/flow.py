"""分析行程的流程與檢查點:一次只做一步,每步落地才推進狀態。

事故:分析行程在流程跑到一半時當機重啟,不確定卡在哪一步、外部呼叫(蒐證、送出提案)有
沒有真的發生。`advance()` 是唯一的驅動函式,每次呼叫只做狀態機的下一步,把結果連同新
狀態列一起提交(檢查點);重複呼叫 `advance()` 在中斷後恢復是安全的。

三個可替換介面比照收件口既有的風格:成功回傳值,預期內的失敗用型別化例外。
`EvidenceSource`、`Submit` 的失敗一律視為暫時性、可以放心重試(前者是純讀取,後者的
真實實作——收件口——保證同一份提案重送永遠安全);只有 `Decide` 自己丟出例外時才轉
FAILED,因為那是對已經到手的證據做純計算,重跑同一份證據只會再次得到同樣的例外。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from rtb.analyzer.task_store import TaskNotFound, TaskRow, TaskStore
from rtb.domain.evidence import Evidence
from rtb.domain.proposal import Proposal
from rtb.domain.task_state import TERMINAL_STATES, TaskState, transition


@dataclass(frozen=True)
class NoAction:
    """這個任務不需要任何動作。"""


@dataclass(frozen=True)
class ProposalDecision:
    """決定要送出的提案。"""

    proposal: Proposal


@dataclass(frozen=True)
class NeedsFreshEvidence:
    """決策層自己判斷手上的證據不夠新,要求重新蒐證;不是例外。"""


Decision = NoAction | ProposalDecision | NeedsFreshEvidence


class EvidenceSource(Protocol):
    def __call__(self, task: TaskRow) -> tuple[Evidence, ...]: ...


class Decide(Protocol):
    def __call__(self, task: TaskRow, evidence: tuple[Evidence, ...]) -> Decision: ...


@dataclass(frozen=True)
class Accepted:
    """`Submit` 的成功結果。"""

    replayed: bool


class SubmitStale(Exception):
    """這份提案已經過期或被取代(對應收件口的 409/422)。"""


class SubmitBusy(Exception):
    """暫時性、可以重試(對應收件口的 503)。"""


class Submit(Protocol):
    def __call__(self, proposal: Proposal) -> Accepted: ...


@dataclass(frozen=True)
class _Collaborators:
    evidence_source: EvidenceSource
    decide: Decide
    submit: Submit


@dataclass(frozen=True)
class _Step:
    """一步的結果:新狀態,以及要跟著這一列一起提交的東西(都可省略)。"""

    new_state: TaskState
    evidence: tuple[Evidence, ...] = ()
    proposal: Proposal | None = None
    error_detail: str | None = None


_StepOutcome = _Step | None


def advance(  # noqa: PLR0913 - 三個可替換介面加時間與中斷鉤子,全部都是必要的參數
    store: TaskStore,
    task_id: str,
    evidence_source: EvidenceSource,
    decide: Decide,
    submit: Submit,
    now: datetime,
    before_commit: Callable[[], None] | None = None,
) -> TaskState:
    """讀任務目前的狀態,做狀態機的下一步,回傳新狀態(或沒有進展時的原狀態)。"""
    row = store.latest(task_id)
    if row is None:
        raise TaskNotFound(task_id)
    if row.state in TERMINAL_STATES or row.state is TaskState.HANDED_OFF:
        return row.state

    collaborators = _Collaborators(evidence_source, decide, submit)
    outcome = _STEPS[row.state](store, row, collaborators)
    if outcome is None:  # 這一步的結果是「不寫入,留在原狀態」
        return row.state
    transition(row.state, outcome.new_state)  # 非法轉換在這裡就會炸,不會靜默寫出壞資料
    committed = store.commit_step(
        task_id, row.seq, outcome.new_state, now, before_commit=before_commit,
        evidence=outcome.evidence, proposal=outcome.proposal, error_detail=outcome.error_detail,
    )
    return outcome.new_state if committed else row.state


def _from_received(_store: TaskStore, _row: TaskRow, _c: _Collaborators) -> _StepOutcome:
    return _Step(TaskState.COLLECTING_EVIDENCE)


def _from_collecting_evidence(_store: TaskStore, row: TaskRow, c: _Collaborators) -> _StepOutcome:
    try:
        evidence = c.evidence_source(row)
    except Exception:  # 純讀取,重試永遠安全:不寫入,留在原狀態
        return None
    return _Step(TaskState.ANALYZING, evidence=evidence)


def _from_analyzing(store: TaskStore, row: TaskRow, c: _Collaborators) -> _StepOutcome:
    evidence = store.evidence_for(row.task_id, row.seq)
    try:
        decision = c.decide(row, evidence)
    except Exception as exc:  # 對已到手的證據做純計算,重跑只會再犯同樣的錯:直接轉 FAILED
        return _Step(TaskState.FAILED, error_detail=repr(exc))
    if isinstance(decision, NoAction):
        return _Step(TaskState.NO_ACTION)
    if isinstance(decision, ProposalDecision):
        return _Step(TaskState.PROPOSED, proposal=decision.proposal)
    return _Step(TaskState.COLLECTING_EVIDENCE)


def _from_proposed(_store: TaskStore, row: TaskRow, c: _Collaborators) -> _StepOutcome:
    if row.proposal is None:
        raise AssertionError("PROPOSED 狀態的列一定帶著提案快照")
    try:
        c.submit(row.proposal)
    except SubmitStale:
        return _Step(TaskState.COLLECTING_EVIDENCE)
    except SubmitBusy:
        return None
    except Exception:  # 未定義的失敗一律視為可重試:重送同一份提案永遠安全
        return None
    return _Step(TaskState.HANDED_OFF, proposal=row.proposal)


_STEPS: dict[TaskState, Callable[[TaskStore, TaskRow, _Collaborators], _StepOutcome]] = {
    TaskState.RECEIVED: _from_received,
    TaskState.COLLECTING_EVIDENCE: _from_collecting_evidence,
    TaskState.ANALYZING: _from_analyzing,
    TaskState.PROPOSED: _from_proposed,
}
