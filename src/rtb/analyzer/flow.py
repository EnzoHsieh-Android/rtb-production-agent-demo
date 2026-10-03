"""分析行程的流程與檢查點:一次只做一步,每步落地才推進狀態。

事故:分析行程在流程跑到一半時當機重啟,不確定卡在哪一步、外部呼叫(蒐證、送出提案)有
沒有真的發生。`advance()` 是唯一的驅動函式,每次呼叫只做狀態機的下一步,把結果連同新
狀態列一起提交(檢查點);重複呼叫 `advance()` 在中斷後恢復是安全的。

可替換介面(起初是蒐證、決策、送出三個,後來加了操作查詢、不提案原因、規則輪)比照收件口既有的風格:成功回傳值,預期內的失敗用型別化例外。
`EvidenceSource`、`Submit` 的失敗一律視為暫時性、可以放心重試(前者是純讀取,後者的
真實實作——收件口——保證同一份提案重送永遠安全);只有 `Decide` 自己丟出例外、或
歷史資料本身毀損讀不回來時才轉 FAILED,因為重跑同一份輸入只會再次得到同樣的結果。

呼叫任何外部介面之前先在任務上取得租約(Phase 4 增量 3b,事故 F3「重複投遞不重複分析費用」):
同一個任務同一時間只有一個持有有效租約的呼叫端會花錢;拿不到租約就回原狀態、什麼都不呼叫。

已交給執行之後(Phase 5,事故 F4):呼叫端有給「操作查詢」時才往下走,先重送同一份提案問收件口
(它的處置是執行端驗證過的結果);收件表已清掉才用交接時存下的冪等鍵查 DSP。版本已變、決策過期、
或清掉後 DSP 查不到寫入,就在原任務結案的同一個交易裡建接續任務重新規劃。沒給操作查詢照 Phase 4
的行為停在已交給執行。

正式規則的規則輪(Phase 14 增量 2b,計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]):呼叫端給了
`rule_decide` 時,分析中那一步一律問它;它回「結果(三種決策之一,或續步/重來 `RuleContinue`)+ 規則
事件 + 不提案原因」。續步沿用「分析中 → 蒐集證據」轉換,不新增狀態;蒐證那一步的規則輪紀錄由證據來源
放在 `EvidenceBatch.rule_step`,跟證據同一個交易寫。流程層不匯入決策規則與規則輪模組。

AI 不參與決策(Phase 14 增量 3,計劃〈使用者裁定〉8):原本 Phase 13 的 `ai_decide` 參數、續租回呼、模型
呼叫記次與 AI 那一步整段撤除;AI 調查只剩評估執行器直接呼叫 `ai_judge.Judge`(不經這裡),它用的結果型別
(`AiContext`、`AiOutcome`、`QueryMore`)移到 `investigation`。這裡只留規則輪也用的 `NoAction`、
`ProposalDecision`、`RuleContinue`。

三個介面用 `typing.Protocol`(不是全域慣用的 `Callable[[Args], Ret]`):它們各自有具名的
多個參數與語意(不是單純「一個函式」),`Protocol` 讓型別檢查器能核對實作簽章、也讓文件
掛在介面本身,是刻意的選擇,不是要在專案裡另立一套慣用法;現有 `Callable` 用法(單一動作
的簡單回呼)不受影響。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from rtb.analyzer.task_store import (
    CorruptedHistoryRow,
    FollowUp,
    LeaseReceipt,
    RawQuery,
    ReplanReason,
    RuleEvent,
    RuleStep,
    TaskNotFound,
    TaskReads,
    TaskRow,
    TaskStore,
)
from rtb.domain.attempt import operation_key
from rtb.domain.evidence import Evidence
from rtb.domain.proposal import Proposal, content_hash
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


@dataclass(frozen=True)
class RuleContinue:
    """規則輪(Phase 14 增量 2b):這一步回到蒐集證據,下一步讀規則輪的下一步(A→B→C),或作廢這一輪從 A
    重來;是哪一種由一起寫的規則事件說明,不是「資料過期重蒐證」。沿用既有「分析中 → 蒐集證據」
    轉換。"""


@dataclass(frozen=True)
class RuleOutcome:
    """規則輪決策函式的外包型別:結果(既有三種決策之一,或續步/重來)、跟狀態列同一個交易寫的規則事件、
    不提案原因。決策型別本身不動(相等比較照舊)。"""

    result: Decision | RuleContinue
    event: RuleEvent | None
    no_action_reason: StrEnum | None = None


class RuleDecide(Protocol):
    def __call__(self, task: TaskRow, evidence: tuple[Evidence, ...], now: datetime,
                 reads: TaskReads) -> RuleOutcome:
        """分析中那一步的規則輪決策:reads 只拿來讀這件工作已提交的規則輪列與各步的證據、原始回應
        (進度由已提交列重算,不持久化下一步);不寫入、不讀時鐘、不打 DSP。"""
        ...


@dataclass(frozen=True)
class EvidenceBatch:
    """證據來源的外包型別(Phase 13):證據,加上追加查詢的原始回應(跟證據同一個交易寫)。"""

    evidence: tuple[Evidence, ...]
    raw: tuple[RawQuery, ...] = ()
    rule_step: RuleStep | None = None  # 規則輪這一步的蒐證紀錄(Phase 14 增量 2b)


class EvidenceSource(Protocol):
    def __call__(self, task: TaskRow, now: datetime) -> tuple[Evidence, ...] | EvidenceBatch:
        """`now` 就是這批證據的讀取時間(`advance()` 手上的同一個時間);證據來源不自己讀系統時鐘,
        否則推進者整批共用一個時間時,證據會比決策用的時間還晚,年齡變負而被判過期。"""
        ...


class Decide(Protocol):
    def __call__(
        self, task: TaskRow, evidence: tuple[Evidence, ...], now: datetime
    ) -> Decision:
        """`now` 是 `advance()` 手上、也會寫進歷史列的同一個時間;決策層不自己讀系統時鐘。"""
        ...


class ExplainNoAction(Protocol):
    def __call__(
        self, task: TaskRow, evidence: tuple[Evidence, ...], now: datetime
    ) -> StrEnum | None:
        """決策是不提案時問「為什麼」(Phase 12 設計審 r2 p1):輸入跟 `Decide` 相同,
        回不提案原因列舉的成員。
        原因不放進決策結果(Phase 10 裁定,決策型別的相等比較照舊),所以另開這個可省略的介面;
        流程層不匯入決策規則(規則匯入流程層的決策型別),型別只約束成列舉成員。"""
        ...


@dataclass(frozen=True)
class Accepted:
    """`Submit` 的成功結果。後五個欄位是收件口回應本文的內容(Phase 5),已交給執行之後那一步
    要核對它們屬於送出的這份提案;只看有沒有收下的呼叫端與測試替身可以不給。"""

    replayed: bool
    task_id: str | None = None
    revision: int | None = None
    content_hash: str | None = None
    state: str | None = None  # 收件口的處置,沒有處置時是提案本身的狀態
    block_code: str | None = None  # 只有處置是已擋下時才有值


class SubmitStale(Exception):
    """這份提案已經過期或被取代(對應收件口的 409/422)。"""


class SubmitBusy(Exception):
    """暫時性、可以重試(對應收件口的 503)。"""


class SubmitRejectedPermanently(Exception):
    """收件口的拒收原因不是重送就能解決的(對應收件口的 too_many_revisions):退回蒐證、送下一個
    修訂只會再次碰到同一個上限,重試不會變好。跟 `Decide` 自己丟例外(對已到手的證據做純計算,
    重跑只會再犯同樣的錯)是同一種道理,所以一樣轉 FAILED,不是 SubmitStale。
    """


class Submit(Protocol):
    def __call__(self, proposal: Proposal) -> Accepted: ...


@dataclass(frozen=True)
class DspOperation:
    """DSP 依冪等鍵查到的操作紀錄(分析端只拿核對內容要用的四個欄位)。"""

    campaign_id: str
    action: str
    new_budget: int | None
    expected_version: int | None


class OperationLookup(Protocol):
    def __call__(self, key: str) -> DspOperation | None:
        """查不到回 None;逾時、斷線、讀不懂一律丟例外(這一輪沒有進展)。"""
        ...


class _BrokenCollaborator(Exception):
    """協作介面回傳了合約之外的型別:這是協作介面本身的錯,不是暫時性失敗,不能重試。"""


class _Lease:
    """目前收據容器:提交、放掉、例外路徑一律讀它(Phase 13 的續租換收據已隨 AI 那一步撤除)。"""

    def __init__(self, current: LeaseReceipt) -> None:
        self.current = current


@dataclass(frozen=True)
class _Collaborators:
    evidence_source: EvidenceSource
    decide: Decide
    submit: Submit
    operation_lookup: OperationLookup | None = None  # 只有已交給執行那一步讀
    no_action_reason: ExplainNoAction | None = None  # 只有分析中、決策是不提案時問
    rule_decide: RuleDecide | None = None  # 給了:分析中那一步走規則輪


@dataclass(frozen=True)
class _Step:
    """一步的結果:新狀態,以及要跟著這一列一起提交的東西(都可省略)。"""

    new_state: TaskState
    evidence: tuple[Evidence, ...] = ()
    proposal: Proposal | None = None
    error_detail: str | None = None
    operation_key: str | None = None
    follow_up: FollowUp | None = None
    no_action_reason: StrEnum | None = None
    raw: tuple[RawQuery, ...] = ()
    rule_step: RuleStep | None = None
    rule_event: RuleEvent | None = None


_StepOutcome = _Step | None


def advance(  # noqa: PLR0913 - 三個可替換介面加時間、中斷鉤子、租約擁有者與操作查詢
    store: TaskStore,
    task_id: str,
    evidence_source: EvidenceSource,
    decide: Decide,
    submit: Submit,
    now: datetime,
    before_commit: Callable[[], None] | None = None,
    owner: str = "analyzer",  # 租約擁有者:比照執行側由啟動程式傳入工作者身分,這裡只當標籤
    operation_lookup: OperationLookup | None = None,  # 不給:已交給執行的任務停在原地
    no_action_reason: ExplainNoAction | None = None,  # 不給:不提案照舊結案,不存原因
    expected_seq: int | None = None,  # 給了:目前那一列不是呼叫端讀到的那一列就什麼都不做
    rule_decide: RuleDecide | None = None,  # 給了:分析中那一步走規則輪(Phase 14)
) -> TaskState:
    """讀任務目前的狀態,做狀態機的下一步,回傳新狀態(或沒有進展時的原狀態)。

    呼叫任何外部介面之前先在任務上取得租約(Phase 4 增量 3b):拿不到就回原狀態、什麼都不
    呼叫;這一步做完(寫入、沒進展或行程內例外)都放掉。擋住舊持有者的是租約序號(每次取得都
    不同),不是擁有者:多個呼叫端共用同一個擁有者也分得開。行程真的猝死放不掉,租約留到到期,
    接手者會再呼叫一次——至少一次的代價。
    """
    row = store.latest(task_id)
    if row is None:
        raise TaskNotFound(task_id)
    if expected_seq is not None and row.seq != expected_seq:
        # 呼叫端(分析端驅動)用它讀到的那一列建了呼叫紀錄的包裝;中間被別人推進過就不拿舊包裝
        # 做新的一步,不然送件紀錄會記到舊列上(Phase 12 代碼審 r1 d1)
        return row.state
    if row.state in TERMINAL_STATES:
        return row.state
    if row.state is TaskState.HANDED_OFF and operation_lookup is None:
        return row.state

    acquired = store.acquire_lease(task_id, owner, now)
    if acquired is None:  # 別人正持有:不花錢,跟「沒有進展」一樣回原狀態
        return row.state
    lease = _Lease(acquired)
    try:
        return _advance_holding(
            store, row,
            _Collaborators(evidence_source, decide, submit, operation_lookup, no_action_reason,
                           rule_decide),
            now, before_commit, lease)
    except BaseException:
        store.release_lease_quietly(lease.current, now)  # 放掉失敗也不蓋掉原本的例外
        raise


def _advance_holding(
    store: TaskStore,
    row: TaskRow,
    collaborators: _Collaborators,
    now: datetime,
    before_commit: Callable[[], None] | None,
    lease: _Lease,
) -> TaskState:
    current = store.latest(row.task_id)
    if current is None or current.seq != row.seq:
        # 讀列之後、取得租約之前,別人已做完一步:不拿舊列呼叫外部,也不在這一輪改用新列
        # (送件的呼叫紀錄綁的是呼叫端在呼叫前讀到的那一列),交回呼叫端用新列重來
        store.release_lease(lease.current, now)
        return row.state if current is None else current.state
    outcome = _STEPS[row.state](store, row, collaborators, now)
    if outcome is None:  # 這一步的結果是「不寫入,留在原狀態」
        store.release_lease(lease.current, now)
        return row.state
    transition(row.state, outcome.new_state)  # 非法轉換在這裡就會炸,不會靜默寫出壞資料
    committed = store.commit_step(
        row.task_id, row.seq, outcome.new_state, now, before_commit=before_commit,
        evidence=outcome.evidence, proposal=outcome.proposal, error_detail=outcome.error_detail,
        lease=lease.current, operation_key=outcome.operation_key, follow_up=outcome.follow_up,
        no_action_reason=outcome.no_action_reason,
        raw=outcome.raw, rule_step=outcome.rule_step, rule_event=outcome.rule_event,
    )
    if not committed:  # 輸了序號或租約:還是自己的才放掉(條件寫在 release_lease 裡)
        store.release_lease(lease.current, now)
    return outcome.new_state if committed else row.state


def _from_received(
    _store: TaskStore, _row: TaskRow, _c: _Collaborators, _now: datetime
) -> _StepOutcome:
    return _Step(TaskState.COLLECTING_EVIDENCE)


def _from_collecting_evidence(
    _store: TaskStore, row: TaskRow, c: _Collaborators, now: datetime
) -> _StepOutcome:
    try:
        got = c.evidence_source(row, now)
    except CorruptedHistoryRow as exc:
        # 證據來源要讀已存的規則輪紀錄才知道讀哪一步,那些紀錄毀損:重試沒有用,跟分析那一步一樣
        # 轉 FAILED(代碼審 r1 鏡頭2-4)
        return _Step(TaskState.FAILED, error_detail=repr(exc))
    except Exception:  # 純讀取,重試永遠安全:不寫入,留在原狀態
        return None
    try:
        batch = got if isinstance(got, EvidenceBatch) else EvidenceBatch(got)
        if not isinstance(batch.evidence, tuple) or not isinstance(batch.raw, tuple):
            # 形狀不對也當成這次沒拿到證據:EvidenceSource 是純讀取,重試永遠安全,
            # 不必為了型別錯誤另外走 FAILED(那是 Decide/Submit 才有的合約違反處理)。
            raise TypeError(f"EvidenceSource 必須回傳 tuple[Evidence, ...],得到 {type(got)!r}")
    except Exception:  # 純讀取,重試永遠安全:不寫入,留在原狀態
        return None
    return _Step(TaskState.ANALYZING, evidence=batch.evidence, raw=batch.raw,
                 rule_step=batch.rule_step)


def _from_analyzing(
    store: TaskStore, row: TaskRow, c: _Collaborators, now: datetime
) -> _StepOutcome:
    try:
        evidence = store.evidence_for(row.task_id, row.seq)
    except CorruptedHistoryRow as exc:  # 存好的資料本身毀損,重試沒有用:直接轉 FAILED
        return _Step(TaskState.FAILED, error_detail=repr(exc))
    if c.rule_decide is not None:  # 規則輪(Phase 14):正式分析一律走這裡
        return _from_rule(store, row, c.rule_decide, evidence, now)
    try:
        decision = c.decide(row, evidence, now)
    except Exception as exc:  # 對已到手的證據做純計算,重跑只會再犯同樣的錯:直接轉 FAILED
        return _Step(TaskState.FAILED, error_detail=repr(exc))
    if isinstance(decision, NoAction):
        return _Step(TaskState.NO_ACTION, no_action_reason=_why_not(c, row, evidence, now))
    if isinstance(decision, ProposalDecision):
        return _Step(TaskState.PROPOSED, proposal=decision.proposal)
    if isinstance(decision, NeedsFreshEvidence):
        return _Step(TaskState.COLLECTING_EVIDENCE)
    raise _BrokenCollaborator(f"Decide 回傳了合約之外的型別:{type(decision)!r}")


def _from_rule(store: TaskStore, row: TaskRow, rule_decide: RuleDecide,
               evidence: tuple[Evidence, ...], now: datetime) -> _StepOutcome:
    """規則輪那一步(Phase 14 增量 2b):已存資料毀損 → FAILED(同決策);其餘例外同決策丟例外轉
    FAILED。"""
    try:
        outcome = rule_decide(row, evidence, now, store)
        if not isinstance(outcome, RuleOutcome):
            raise _BrokenCollaborator(f"規則輪決策函式回傳了合約之外的型別:{type(outcome)!r}")
    except _BrokenCollaborator:
        raise
    except Exception as exc:  # 對已存的證據做純計算,重跑只會再犯同樣的錯:直接轉 FAILED
        return _Step(TaskState.FAILED, error_detail=repr(exc))
    result, event = outcome.result, outcome.event
    if isinstance(result, NoAction):
        return _Step(TaskState.NO_ACTION, no_action_reason=outcome.no_action_reason,
                     rule_event=event)
    if isinstance(result, ProposalDecision):
        return _Step(TaskState.PROPOSED, proposal=result.proposal, rule_event=event)
    if isinstance(result, NeedsFreshEvidence | RuleContinue):
        return _Step(TaskState.COLLECTING_EVIDENCE, rule_event=event)
    raise _BrokenCollaborator(f"規則輪決策函式回傳了合約之外的結果:{type(result)!r}")


def _why_not(
    c: _Collaborators, row: TaskRow, evidence: tuple[Evidence, ...], now: datetime
) -> StrEnum | None:
    """不提案的原因只是給人看的附帶資料:問不到、出錯或答非列舉成員都當作沒有原因,不改變「不提案」
    這個結果(原因表不會刪,任意字串寫進去等於開一條注入管道)。"""
    if c.no_action_reason is None:
        return None
    try:
        answer = c.no_action_reason(row, evidence, now)
    except Exception:  # 附帶資料查不出來不影響流程,跟證據來源失敗一樣不轉 FAILED
        return None
    return answer if isinstance(answer, StrEnum) else None


def _from_proposed(
    _store: TaskStore, row: TaskRow, c: _Collaborators, _now: datetime
) -> _StepOutcome:
    if row.proposal is None:
        raise AssertionError("PROPOSED 狀態的列一定帶著提案快照")
    try:
        result = c.submit(row.proposal)
    except SubmitRejectedPermanently as exc:
        return _Step(TaskState.FAILED, error_detail=repr(exc))
    except SubmitStale:
        return _Step(TaskState.COLLECTING_EVIDENCE)
    except SubmitBusy:
        return None
    except Exception:  # 未定義的失敗一律視為可重試:重送同一份提案永遠安全
        return None
    if not isinstance(result, Accepted):
        raise _BrokenCollaborator(f"Submit 回傳了合約之外的型別:{type(result)!r}")
    # 交接這一列存下冪等鍵:之後收件表清掉要查 DSP 時用存下的這把,不從提案重算
    return _Step(TaskState.HANDED_OFF, proposal=row.proposal,
                 operation_key=operation_key(row.proposal))


# 收件口回應的處置 → 下一步;不在表裡的處置當成回應讀不懂(這一輪沒有進展)
# 待核可(Phase 6 增量 3)跟處理中一樣是等待:人核可後執行端接續處理,到期才確認成已擋下
_OPEN_STATES = frozenset({"pending", "in_progress", "awaiting_approval"})
# 死信(Phase 8 改 Phase 5 [S304] 的死信那一半):決策還沒過期就等(可能被重放),過期才結案
_KNOWN_STATES = _OPEN_STATES | {"blocked", "dead_letter", "handed_off", "expired", "superseded"}
_PURGED_CODES = frozenset({"expired_proposal", "revision_out_of_order"})  # 收件表已清掉
_VERSION_CHANGED = "version_changed"
# 收件口回給分析行程的擋下原因(權限類合併成 not_permitted,使用者 2026-09-23 裁定):不在這份
# 清單的代碼當成回應讀不懂,不寫進只增不改的歷史表(對方回什麼字串都原樣寫進稽核紀錄是注入管道)
# 這幾種擋下原因要重新規劃(另開接續任務重讀現況);Phase 8 加政策已變與決策已過時
_REPLAN_ON_BLOCK = {
    _VERSION_CHANGED: ReplanReason.VERSION_CHANGED,
    "policy_version_changed": ReplanReason.POLICY_VERSION_CHANGED,
    "decision_stale": ReplanReason.DECISION_STALE,
}
_BLOCK_CODES = frozenset({*_REPLAN_ON_BLOCK, "not_permitted", "campaign_not_found",
                          "campaign_not_active", "operation_previously_failed"})


def _from_handed_off(
    store: TaskStore, row: TaskRow, c: _Collaborators, now: datetime
) -> _StepOutcome:
    if row.proposal is None or c.operation_lookup is None:
        raise AssertionError("已交給執行的列一定帶著提案快照;沒給操作查詢不會走到這一步")
    try:
        answer = c.submit(row.proposal)
    except SubmitStale as stale:
        if str(stale) in _PURGED_CODES:
            return _from_dsp(store, row, c.operation_lookup)
        return _closed(f"rejected={stale}")
    except SubmitRejectedPermanently as exc:
        return _closed(f"rejected={exc}")
    except Exception:  # 忙碌、未定義失敗、網路失敗:重送同一份提案永遠安全,下一輪再問
        return None
    if not isinstance(answer, Accepted):  # 同送出提案那一步:協作介面本身的錯,不能重試
        raise _BrokenCollaborator(f"Submit 回傳了合約之外的型別:{type(answer)!r}")
    if not _belongs_to(answer, row.proposal):
        return None  # 別的提案的回應、或處置與原因對不上:不拿來結案
    return _from_inbox_answer(answer, row.proposal, now)


def _belongs_to(answer: Accepted, proposal: Proposal) -> bool:
    same = (answer.task_id == proposal.task_id and answer.revision == proposal.revision
            and answer.content_hash == content_hash(proposal))
    consistent = answer.state in _KNOWN_STATES and (
        answer.block_code in _BLOCK_CODES if answer.state == "blocked"
        else answer.block_code is None)
    return same and consistent


def _from_inbox_answer(  # noqa: PLR0911 - 收件口每一種處置一個出口
    answer: Accepted, proposal: Proposal, now: datetime,
) -> _StepOutcome:
    if answer.state == "handed_off":
        return _Step(TaskState.COMPLETED)
    if answer.state in _OPEN_STATES:
        return None
    if answer.state == "dead_letter" and now < proposal.decision_expires_at:
        # 收件口不會把死信轉成已過期(待核可會,死信不會):過期沒由分析端用自己的時鐘比對快照的
        # 到期時間,邊界跟收件口判過期一樣(到期那一刻起算過期)。收件表清掉之後照 [S317]
        return None
    if answer.state == "superseded":
        return _Step(TaskState.SUPERSEDED)
    if answer.state == "blocked" and answer.block_code in _REPLAN_ON_BLOCK:
        return _replan(_REPLAN_ON_BLOCK[answer.block_code], f"blocked={answer.block_code}")
    if answer.state == "expired":
        return _replan(ReplanReason.EXPIRED, "expired")
    return _closed(f"{answer.state}={answer.block_code}")


def _from_dsp(store: TaskStore, row: TaskRow, lookup: OperationLookup) -> _StepOutcome:
    """收件表已清掉:清掉時執行端早就結束(處理中與轉人工不會被清),DSP 那邊不會再有新寫入。"""
    proposal = row.proposal
    if proposal is None:
        raise AssertionError("已交給執行的列一定帶著提案快照")
    # Phase 5 之前寫的列沒有存鍵:用目前的算法重算,只在算法沒改版時正確(S321 的測試釘住)
    key = store.operation_key_for(row.task_id) or operation_key(proposal)
    try:
        record = lookup(key)
    except Exception:  # 逾時、斷線、讀不懂:這一輪沒有進展
        return None
    if record is None:  # 從來沒寫進去:重讀現況再決定
        return _replan(ReplanReason.AFTER_RETENTION, "inbox_purged;dsp_operation_not_found")
    if not isinstance(record, DspOperation):
        raise _BrokenCollaborator(f"OperationLookup 回傳了合約之外的型別:{type(record)!r}")
    if _matches(record, proposal):
        return _Step(TaskState.COMPLETED)
    return _closed("inbox_purged;idempotency_conflict")


def _matches(record: DspOperation, proposal: Proposal) -> bool:
    """跟執行端對帳核對同一組欄位:冪等鍵是執行端算的,DSP 不會拿內容反算這把鍵。"""
    return (record.campaign_id == proposal.campaign_id
            and record.action == proposal.action_type.value
            and record.new_budget == proposal.requested_change.get("new_budget")
            and record.expected_version == proposal.campaign_version_observed)


def _replan(reason: ReplanReason, detail: str) -> _Step:
    return _Step(TaskState.BLOCKED, error_detail=detail, follow_up=FollowUp(reason))


def _closed(detail: str) -> _Step:
    return _Step(TaskState.BLOCKED, error_detail=detail)


_STEPS: dict[TaskState, Callable[[TaskStore, TaskRow, _Collaborators, datetime], _StepOutcome]] = {
    TaskState.RECEIVED: _from_received,
    TaskState.COLLECTING_EVIDENCE: _from_collecting_evidence,
    TaskState.ANALYZING: _from_analyzing,
    TaskState.PROPOSED: _from_proposed,
    TaskState.HANDED_OFF: _from_handed_off,
}
