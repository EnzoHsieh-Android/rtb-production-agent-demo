"""執行迴圈的「處理一筆」與「對帳」。

處理一筆:取一份待處理提案,檢查、帶憑證寫入 DSP、記結果、執行後驗證。
對帳(增量 4):結果不明的嘗試用冪等鍵查 DSP 操作紀錄,查到就核對完整內容再驗證;查不到就重讀
廣告、重跑執行前檢查,通過就同鍵重送,業務上不過就先請 DSP 作廢這把鍵,作廢成功才標失敗——
作廢與寫入在 DSP 同一把寫入鎖下排隊,所以作廢成功就證明舊請求永遠不會提交(不靠時間猜)。

事故:照提案當時的舊現況寫入,蓋掉別人較新的改動(F4);執行到已被取代的提案;DSP 回成功就
當作完成。執行端只驗證並執行獲授權的動作:前提不成立就擋下,不重新分析。

寫法比照分析行程的流程:一個同步函式,協作者(時鐘、DSP 讀寫用戶端、簽發器、租戶設定路徑)
用協定注入。時間只有一個來源——注入的時鐘——但不是整輪共用同一個值:到期判斷、每次簽發、
每一筆寫入都在當下讀它,否則過期後的重簽會簽出跟第一張一樣已過期的憑證。
呼叫 DSP 期間不開任何資料庫交易;每一筆寫入各自一個交易,條件是上一筆寫入回傳的序號。

佇列(Phase 4 增量 1):處理一筆最前面是取件,收件表寫「處理中」並回一張收據;之後每一筆嘗試
寫入都在同一個交易裡先核對收據仍有效(同一把租約管訊息與嘗試紀錄,Phase 0 的裁定),嘗試到
終點就在同一個交易裡確認。收據對不上是正常的租約競爭:丟 LeaseLost,這把鍵這一輪放棄;只有
嘗試紀錄自己的序號對不上才照舊停機。

人工核可(Phase 6 增量 3):硬規則一律先判(執行前檢查的三條、簽發的兩條),可核可的兩關最後判
——簽發之後判比例上限,開始一筆時判總曝險。沒有那一關的有效核可就停在待核可,不結案;每輪的
「處理待核可」那一步在核可到了時放回待處理、到期時確認成已擋下。用到核可的那一筆,憑證不能活得
比核可久;同鍵重送前也重判比例與用過的核可。

可觀測(Phase 9 增量 1):嘗試紀錄的每一列記來源與執行者(這個執行迴圈的擁有者);每一個 DSP 呼叫點
都顯式傳一支綁好這份提案身分的回呼,DSP 用戶端每一次 HTTP 呼叫觸發它一次,在獨立的短交易裡寫一列
DSP 呼叫紀錄——結果寫入回滾時呼叫紀錄照樣留著;同一個 DSP 用戶端給好幾個工作者共用時各記各的。
寫紀錄撞到資料庫忙碌不丟例外,先留在這個執行器的待寫清單,之後補寫(代碼審第 1 輪,代使用者裁定)。
簽發之後收據帶上租戶,之後的生命週期事件記簽發當時的租戶。
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, NoReturn, Protocol, TypeVar

from rtb import sqlitekit
from rtb.domain._checks import is_plain_int
from rtb.domain.attempt import TERMINAL_STATES, AttemptState, OutcomeCode, operation_key
from rtb.domain.proposal import POLICY_VERSION, ActionType, Proposal, content_hash
from rtb.executor import approval, attempt_store, guardrails
from rtb.executor.approval import Approval
from rtb.executor.attempt_store import (
    Actor,
    AttemptRow,
    CallSubject,
    DspCall,
    DspCallResult,
    Source,
)
from rtb.executor.capability_signer import Grant, SigningRefused, Tenant, tenant_for
from rtb.executor.inbox_store import (
    APPROVABLE,
    VISIBILITY_TIMEOUT,
    ApprovalUse,
    AwaitingOutcome,
    AwaitingProposal,
    BlockCode,
    CorruptedInboxRow,
    InboxBusy,
    InboxBusyNotStarted,
    InboxStore,
    LastFailure,
    PendingProposal,
    Receipt,
    Stop,
    StopKind,
    block_code_for_failure,
)

A = AttemptState
C = OutcomeCode
RATIO = BlockCode.BUDGET_INCREASE_TOO_LARGE
AGGREGATE = BlockCode.AGGREGATE_LIMIT_REACHED


@dataclass(frozen=True)
class CampaignView:
    """DSP 上廣告的現況(執行前檢查與執行後驗證要看的欄位)。"""

    budget: int
    status: str
    version: int


@dataclass(frozen=True)
class WriteAnswer:
    """DSP 對一次寫入的回應。status 是 None 代表沒拿到回應(逾時、斷線、回應讀不懂);這時 failure
    記是哪一種(Phase 9 增量 1),回應對照表照舊只看 status。"""

    status: int | None
    error: str | None = None
    version_after: int | None = None
    failure: DspCallResult | None = None


@dataclass(frozen=True)
class OperationRecord:
    """DSP 用冪等鍵查到的操作紀錄;預期版本在 DSP 補欄位之前寫的舊列是空值。"""

    campaign_id: str
    action: str
    new_budget: int | None
    expected_version: int | None
    version_after: int


@dataclass(frozen=True)
class VoidAnswer:
    """DSP 對一次作廢的回應。status 是 None 代表沒拿到回應;state 是「已作廢」或「已提交」。"""

    status: int | None
    error: str | None = None
    state: str | None = None
    record: OperationRecord | None = None
    failure: DspCallResult | None = None  # 沒拿到回應時是哪一種(同 WriteAnswer)


class DspUnavailable(Exception):
    """讀取沒有成功(不含「廣告不存在」):逾時、斷線、5xx、4xx、回應讀不懂;failure 記是哪一種。"""

    def __init__(self, message: str = "", failure: DspCallResult | None = None):
        super().__init__(message)
        self.failure = failure


OnDspCall = Callable[[DspCall], None]


class DspPort(Protocol):
    """每一支方法都收必填的 on_call:這次呼叫的每一次 HTTP 呼叫都呼叫它剛好一次(呼叫類別、結果類別、
    狀態碼、耗時、錯誤代碼)。呼叫端綁好這次是為了哪份提案(Phase 9 增量 1 代碼審第 1 輪:改成顯式
    傳,漏傳在型別層就擋下)。"""

    def read_campaign(self, campaign_id: str, *, on_call: OnDspCall) -> CampaignView | None:
        """廣告不存在回 None;其他讀取失敗丟 DspUnavailable。"""
        ...

    def write(
        self, proposal: Proposal, key: str, token: str, *, on_call: OnDspCall,
    ) -> WriteAnswer:
        """帶憑證與冪等鍵送出寫入;不丟例外,沒拿到回應就回 status 為 None 的回應。"""
        ...

    def operation_version(self, key: str, *, on_call: OnDspCall) -> int | None:
        """用冪等鍵查 DSP 的操作紀錄,回寫入後版本;查不到回 None,查詢失敗丟 DspUnavailable。"""
        ...

    def operation_record(self, key: str, *, on_call: OnDspCall) -> OperationRecord | None:
        """用冪等鍵查完整操作紀錄;查不到回 None,查詢失敗丟 DspUnavailable。"""
        ...

    def void(
        self, proposal: Proposal, key: str, token: str, *, on_call: OnDspCall,
    ) -> VoidAnswer:
        """請 DSP 作廢這把鍵;不丟例外,沒拿到回應就回 status 為 None 的回應。"""
        ...


class Signer(Protocol):
    def sign(self, proposal: Proposal, operation_key: str, config_path: Path, now: int) -> str: ...

    def read_tenants(self, config_path: Path) -> tuple[Tenant, ...]:
        """讀租戶設定(簽發器帶內容快取,每次照舊做安全讀檔;F7 效能計劃)。處理待核可經它讀,
        不直接呼叫模組層級的讀設定函式、也不伸手進簽發器的私有欄位。"""
        ...

    def grant(
        self, proposal: Proposal, operation_key: str, config_path: Path, now: int,
        not_after: int | None = None,
    ) -> Grant:
        """同 sign,另外帶回憑證到期時間與同一次讀設定檔得到的整個租戶(Phase 6);not_after 是
        最晚到期(用到人工核可時不能活得比核可久)。"""
        ...

    def sign_void(
        self, proposal: Proposal, operation_key: str, config_path: Path, now: int,
    ) -> str: ...


class LeaseLost(Exception):
    """收據對不上(租約已被接手或已過期),或沒有收據的舊鍵序號被別的工作者先推進:這把鍵這一輪
    放棄,不停機——多工作者下這是正常競爭。"""


class ExecutorHalted(Exception):
    """系統故障(設定檔壞掉、本地請求錯誤、條件寫入沒有進展):這一輪停下,啟動程式以非零代碼結束。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---- DSP 回應對照表(唯一一份;處理一筆的寫結果一定經 react) ----
@dataclass(frozen=True, eq=False)
class Reaction:
    target: AttemptState
    code: OutcomeCode | None = None
    halt: bool = False  # 記完之後這一輪以系統錯誤結束
    capability_expired: bool = False  # DSP 明確沒寫:記成結果不明後重跑檢查、重簽重送


@dataclass(frozen=True)
class ResponseRule:
    name: str
    matches: Callable[[WriteAnswer], bool]
    reaction: Reaction
    example: WriteAnswer  # 落在這一列的一個回應,給測試逐列驗證


def _status(low: int, high: int, error: str | None = None) -> Callable[[Any], bool]:
    """兩張回應對照表共用:回應只要有狀態碼與錯誤代碼兩個欄位就能用。"""
    def matches(answer: Any) -> bool:
        return (answer.status is not None and low <= answer.status <= high
                and (error is None or answer.error == error))
    return matches


def _committed(answer: WriteAnswer) -> bool:
    version = answer.version_after
    return answer.status == 200 and is_plain_int(version) and version > 0


RESPONSE_TABLE = (
    ResponseRule("committed", _committed, Reaction(A.COMMITTED_UNVERIFIED),
                 WriteAnswer(200, None, 4)),
    # 要排在「409 版本衝突」之前:那一列不看錯誤代碼。這把鍵已被作廢,DSP 明確沒寫、永遠不會寫
    ResponseRule("operation_voided", _status(409, 409, "operation_voided"),
                 Reaction(A.FAILED, C.NOT_HAPPENED), WriteAnswer(409, "operation_voided")),
    ResponseRule("version_conflict", _status(409, 409), Reaction(A.FAILED, C.VERSION_CONFLICT),
                 WriteAnswer(409, "version_conflict")),
    ResponseRule("idempotency_conflict", _status(422, 422, "idempotency_conflict"),
                 Reaction(A.ESCALATED, C.IDEMPOTENCY_CONFLICT),
                 WriteAnswer(422, "idempotency_conflict")),
    ResponseRule("validation_rejected", _status(422, 422),
                 Reaction(A.FAILED, C.VALIDATION_REJECTED),
                 WriteAnswer(422, "validation_rejected")),
    ResponseRule("capability_expired", _status(401, 401, "capability_expired"),
                 Reaction(A.UNKNOWN, capability_expired=True),
                 WriteAnswer(401, "capability_expired")),
    ResponseRule("capability_rejected", _status(401, 401),
                 Reaction(A.ESCALATED, C.CAPABILITY_REJECTED),
                 WriteAnswer(401, "capability_invalid")),
    ResponseRule("capability_scope_mismatch", _status(403, 403),
                 Reaction(A.ESCALATED, C.CAPABILITY_REJECTED),
                 WriteAnswer(403, "capability_scope_mismatch")),
    ResponseRule("capability_not_configured", _status(503, 503, "capability_not_configured"),
                 Reaction(A.ESCALATED, C.CAPABILITY_REJECTED),
                 WriteAnswer(503, "capability_not_configured")),
    ResponseRule("local_request_error", _status(400, 499),
                 Reaction(A.ESCALATED, C.LOCAL_REQUEST_ERROR, halt=True),
                 WriteAnswer(400, "missing_idempotency_key")),
    ResponseRule("dsp_unavailable", _status(500, 599), Reaction(A.UNKNOWN),
                 WriteAnswer(503, "store_busy")),
    ResponseRule("no_response", lambda answer: answer.status is None, Reaction(A.UNKNOWN),
                 WriteAnswer(None)),
    # 每個狀態碼都要有去處:共用用戶端不跟轉址,3xx 會原樣回來;沒列的一律結果不明,留給對帳
    ResponseRule("unlisted_status", lambda _answer: True, Reaction(A.UNKNOWN),
                 WriteAnswer(302, None)),
)


def react(answer: WriteAnswer) -> Reaction:
    return next(rule.reaction for rule in RESPONSE_TABLE if rule.matches(answer))


# ---- 作廢回應對照表(唯一一份;對帳作廢一定經 react_void) ----
class VoidOutcome(StrEnum):
    FAILED = "failed"  # 已作廢:標失敗(沒發生)
    FOUND = "found"  # 作廢時查到舊請求已先提交:走「查到」處理
    TIMEOUT = "timeout"  # 沒有結論:留在結果不明,記一次查證逾時
    ESCALATED = "escalated"


@dataclass(frozen=True, eq=False)
class VoidReaction:
    outcome: VoidOutcome
    code: OutcomeCode | None = None
    halt: bool = False


@dataclass(frozen=True)
class VoidRule:
    name: str
    matches: Callable[[VoidAnswer], bool]
    reaction: VoidReaction


VOID_TABLE = (
    VoidRule("voided", lambda a: a.status == 200 and a.state == "voided",
             VoidReaction(VoidOutcome.FAILED, C.NOT_HAPPENED)),
    VoidRule("committed", lambda a: a.status == 200 and a.state == "committed"
             and a.record is not None, VoidReaction(VoidOutcome.FOUND)),
    VoidRule("capability_expired", _status(401, 401, "capability_expired"),
             VoidReaction(VoidOutcome.TIMEOUT)),  # 下一輪重讀時鐘重簽
    VoidRule("capability_rejected", _status(401, 401),
             VoidReaction(VoidOutcome.ESCALATED, C.CAPABILITY_REJECTED)),
    VoidRule("capability_scope_mismatch", _status(403, 403),
             VoidReaction(VoidOutcome.ESCALATED, C.CAPABILITY_REJECTED)),
    VoidRule("capability_not_configured", _status(503, 503, "capability_not_configured"),
             VoidReaction(VoidOutcome.ESCALATED, C.CAPABILITY_REJECTED)),
    VoidRule("local_request_error", _status(400, 499),
             VoidReaction(VoidOutcome.ESCALATED, C.LOCAL_REQUEST_ERROR, halt=True)),
    # 5xx、逾時、斷線、讀不懂、表上沒列的狀態碼(含 200 卻讀不出結論):留在結果不明
    VoidRule("inconclusive", lambda _a: True, VoidReaction(VoidOutcome.TIMEOUT)),
)


def react_void(answer: VoidAnswer) -> VoidReaction:
    return next(rule.reaction for rule in VOID_TABLE if rule.matches(answer))


# ---- 處理一筆 ----
class Result(StrEnum):
    IDLE = "idle"  # 沒有可處理的提案
    DEFERRED = "deferred"  # 這一輪什麼都沒寫,提案留在待處理(或已被取代)
    EXPIRED = "expired"
    BLOCKED = "blocked"
    HANDED_OFF_TO_EXISTING = "handed_off_to_existing"  # 鍵已存在,由既有那筆嘗試負責
    EXECUTED = "executed"  # 開了一筆嘗試並送出
    AWAITING_APPROVAL = "awaiting_approval"  # 可核可的一關沒有有效核可:停在待核可(Phase 6 增量 3)
    LEASE_LOST = "lease_lost"  # 收據對不上:這一輪放棄這份提案


@dataclass(frozen=True)
class Processed:
    kind: Result
    key: str | None = None
    block_code: BlockCode | None = None


# 簽發器拒簽的原因:這兩種是這份提案的問題(擋下);其他(設定檔壞掉、不安全)是系統故障
_BUSINESS_REFUSALS = {
    "campaign_not_allowed": BlockCode.CAMPAIGN_NOT_ALLOWED,
    "over_budget_cap": BlockCode.OVER_BUDGET_CAP,
}


def _aggregate_used(tx: attempt_store.ExecutorTransaction, tenant: str, now: datetime) -> int:
    """表滿延後也要記當時已用額度;這裡在例外處理裡呼叫,壞快照要自己轉成停機。"""
    try:
        return attempt_store.aggregate_used(tx, tenant, now)
    except attempt_store.CorruptedAttemptRow as exc:
        raise ExecutorHalted("unreadable_attempt") from exc


def precheck(proposal: Proposal, view: CampaignView | None) -> BlockCode | None:
    """執行前檢查的四條硬規則:三條看 DSP 現況,加上 Phase 8 的政策版本;「不在投放」排在版本之前,
    代碼比較有意義。

    比例上限(可核可)不在這裡:Phase 6 增量 3 搬到簽發之後,硬規則一律先判,否則同時違反比例與
    單一廣告上限的提案會先停在待核可、等一張注定用不上的核可。比例的基準仍是這裡讀到的現況:版本
    已變排在前面,走到比例時現況一定是提案觀察到的那個版本。"""
    if view is None:
        return BlockCode.CAMPAIGN_NOT_FOUND
    if view.status != "active":
        return BlockCode.CAMPAIGN_NOT_ACTIVE
    if view.version != proposal.campaign_version_observed:
        return BlockCode.VERSION_CHANGED
    if proposal.policy_version != POLICY_VERSION:  # Phase 8:政策換版後,舊政策下的決策不寫
        return BlockCode.POLICY_VERSION_CHANGED
    return None


# 重跑的執行前檢查查到這幾種,收件口照實確認成那個原因(分析端據此重新規劃);其他照嘗試結果
# 代碼確認。決策已過時不在執行前檢查裡,由轉回嘗試中的交易直接帶出(見 _in_flight_again)
_KEPT_ON_RERUN = frozenset({BlockCode.VERSION_CHANGED, BlockCode.POLICY_VERSION_CHANGED})


def _kept_reason_or_none(live: bool, checked: BlockCode | None) -> BlockCode | None:
    """重跑執行前檢查查到版本已變或政策已變:收件口確認成那個原因,嘗試結果代碼照舊記「沒發生」。
    其他原因(含提案過期、權限不過)回空值,照嘗試結果代碼確認。
    過期跟這幾種同時成立時過期優先,跟第一次處理時的順序一致(代碼審第 2 輪)。
    使用者 2026-09-23 裁定把 Phase 5 [S310] 擴到憑證過期後重讀與對帳查不到兩條路徑;政策已變與
    決策已過時照同一個做法,使用者 2026-09-24 裁定(Phase 8 [S511])。"""
    return checked if live and checked in _KEPT_ON_RERUN else None


def intent_holds(proposal: Proposal, view: CampaignView) -> bool:
    if proposal.action_type is ActionType.UPDATE_BUDGET:
        return bool(view.budget == proposal.requested_change["new_budget"])
    return view.status == "paused"


def record_matches(proposal: Proposal, record: OperationRecord) -> bool:
    """DSP 同鍵查到的操作,內容要跟快照一致:冪等鍵是執行端算的,DSP 不會拿內容反算這把鍵。
    舊列沒有預期版本(空值)一律當成對不上。"""
    return (record.campaign_id == proposal.campaign_id
            and record.action == proposal.action_type.value
            and record.new_budget == proposal.requested_change.get("new_budget")
            and record.expected_version == proposal.campaign_version_observed)


@dataclass(frozen=True)
class _Signed:
    token: str
    expires_at: datetime
    tenant: Tenant  # 同一次讀設定檔得到的整個租戶:總額上限、租戶名稱、核可範圍指紋都從這裡取
    approvals: tuple[Approval, ...] = ()  # 同鍵重送時這張憑證靠的核可:轉嘗試中的交易裡再核一次


class _ApprovalSuperseded(Exception):
    """轉嘗試中的交易裡發現重送靠的核可已不是那一關最新的:不重送。"""


class _DecisionStale(Exception):
    """轉嘗試中的交易裡發現決策已過時(又沒有用過核可):不重送,收件口確認成決策已過時。"""


def _no_progress(key: str, receipt: Receipt | None) -> NoReturn:
    """嘗試紀錄的序號對不上(條件寫入沒進展)。

    有收據:收據剛核對過還有效,序號卻被改過,代表有人繞過租約寫了嘗試紀錄——系統錯誤,停機。
    沒有收據(收件表沒有對應處理中訊息的舊鍵,沒有租約可搶):多個工作者會同時對帳它,序號輸家是
    正常競爭,放棄這把鍵、不停機。每一次呼叫 DSP 寫入之前都先有一筆成功的轉嘗試中寫入,所以兩邊
    最多一個能走到 DSP 寫入。
    """
    if receipt is None:
        raise LeaseLost(key)
    raise ExecutorHalted("no_progress")


# 寫結果碰到開交易鎖不到時的重試(F7 效能計劃第 2 部分,使用者 2026-09-25 裁定):
# - 最多重試 3 次,每次重試前依序退避這幾秒
# - 每次再開交易之前,這張收據的租約剩餘時間要夠等一次鎖(sqlitekit 的等鎖上限)加退避加固定餘裕,
#   不夠就不再試
# - 餘裕涵蓋拿到鎖之後交易本體跑到提交的時間(平常毫秒級),以及磁碟同步變慢與時鐘讀取的誤差
RESULT_WRITE_RETRIES = 3
RESULT_WRITE_BACKOFF_SECONDS = (0.05, 0.1, 0.2)
RESULT_WRITE_LEASE_MARGIN = timedelta(seconds=5)
_T = TypeVar("_T")

# 待寫呼叫紀錄的硬上限(暫用,代使用者裁定,代碼審第 2 輪):到上限就不開始新的一筆處理或對帳(不送新的
# DSP 呼叫),只做補寫,補寫成功才恢復。一筆處理最多幾次 DSP 呼叫,所以上限可能多出那幾列。
MAX_PENDING_CALLS = 50


@dataclass(frozen=True)
class _PendingCall:
    """還沒寫進去的一列呼叫紀錄:時間是呼叫當下的,不是補寫時的。"""

    call: DspCall
    subject: CallSubject
    at: datetime


@dataclass(frozen=True)
class Executor:
    store: InboxStore
    dsp: DspPort
    signer: Signer
    config_path: Path
    clock: Callable[[], datetime]  # 單一動作的簡單回呼:照專案慣例用 Callable,不另開 Protocol
    owner: str = "executor"  # 租約擁有者:啟動程式傳行程編號加啟動時間
    approval_key: bytes | None = None  # 人工核可金鑰(啟動程式讀);沒有就一張核可都不算數
    # 寫結果重試的退避(F7 效能計劃第 2 部分):只有啟動程式接真的睡眠,其他建構點預設不睡——退避只是
    # 禮讓,不影響正確性;測試傳假的睡眠記下序列
    sleep: Callable[[float], None] = field(default=lambda _seconds: None, repr=False,
                                           compare=False)
    # 撞到資料庫忙碌、還沒寫進去的呼叫紀錄(代使用者裁定,代碼審第 1 輪):只在這個行程的記憶體裡
    _pending: list[_PendingCall] = field(default_factory=list, init=False, repr=False,
                                         compare=False)

    @property
    def _by(self) -> Actor:
        return Actor(Source.EXECUTOR_LOOP, self.owner)

    def _calls(self, proposal: Proposal, key: str) -> OnDspCall:
        """給 DSP 用戶端的回呼,綁好這次呼叫是為了哪份提案(每個呼叫點顯式傳)。"""
        subject = CallSubject(proposal.task_id, proposal.revision, proposal.campaign_id, key,
                              content_hash(proposal))

        def on_call(call: DspCall) -> None:
            self._pending.append(_PendingCall(call, subject, self.clock()))
            self.flush_calls()

        return on_call

    def flush_calls(self) -> bool:
        """把欠著的呼叫紀錄用一個新的短交易寫進去,回傳是否排空;資料庫忙碌就留著下次再補,永遠不丟
        忙碌例外(丟出去會蓋掉已經發生的 DSP 結果、對帳整輪被放棄;代碼審第 1 輪三席)。補寫仍忙由
        啟動程式跟主交易忙碌一樣計數(代碼審第 2 輪)。其他資料庫錯誤照舊往外丟。

        先把這一批從清單拿出來再開交易,交易沒成就原樣放回清單最前面(代碼審第 3 輪,代使用者裁定):
        提交之後才被 Ctrl+C 打斷時,例外會展開堆疊、跑啟動程式收尾的補寫,這批要是還在清單上就會
        重寫一次(呼叫紀錄表沒有能去重的鍵)。放回的條件:這一批還沒寫完(交易一定回滾了),或是一般例外
        (提交本身失敗也會回滾)。寫完之後才到的中斷不放回:訊號例外要等提交那一步回來才丟,那時已經
        提交。所以不會重寫;中斷落在「拿出來之後、放回之前」或「寫完之後、提交之前」這兩段極短的空檔,
        那一批就少記,跟當機一樣。"""
        if not self._pending:
            return True
        batch = self._pending[:]
        del self._pending[:]
        written = False
        try:
            with self.store.transaction() as tx:
                for item in batch:
                    attempt_store.record_dsp_call(tx, item.call, item.subject, self._by, item.at)
                written = True
        except InboxBusy:
            self._pending[:0] = batch
            return False
        except BaseException as exc:
            if not written or isinstance(exc, Exception):
                self._pending[:0] = batch
            raise
        return True

    def _backlogged(self) -> bool:
        """先補寫一次;待寫清單仍到上限就不開始新的工作(背壓)。"""
        return not self.flush_calls() and len(self._pending) >= MAX_PENDING_CALLS

    def owes_calls(self) -> bool:
        """還有沒寫進去的呼叫紀錄。每一次呼叫、每一份工作結束都立刻補寫,所以清單不空就等於這一輪最後
        一次補寫仍忙(啟動程式用它計忙碌,不自己再補寫一次;代碼審第 3 輪)。"""
        return bool(self._pending)

    def unrecorded(self) -> tuple[str | None, ...]:
        """還沒寫進去的呼叫紀錄的冪等鍵(一列一個;啟動程式收尾時印出少記哪些)。"""
        return tuple(item.subject.key for item in self._pending)

    def process_one(self) -> Processed:
        if self._backlogged():
            return Processed(Result.DEFERRED)
        with self.store.transaction() as tx:
            delivery = self.store.receive(tx, self.clock(), self.owner)
        if delivery is None:
            return Processed(Result.IDLE)
        try:
            return self._process(delivery.message, delivery.receipt)
        except LeaseLost:
            return Processed(Result.LEASE_LOST)
        except CorruptedInboxRow as exc:  # 處理中那一列讀不懂(例如租約時間):跟對帳一樣停下
            raise ExecutorHalted("unreadable_message") from exc
        finally:
            self.flush_calls()

    def _process(self, picked: PendingProposal, receipt: Receipt) -> Processed:
        proposal = picked.proposal
        if proposal.decision_expires_at <= self.clock():  # 過期先判,不必去讀 DSP
            return self._settle(receipt, None, Result.EXPIRED)
        try:
            view = self.dsp.read_campaign(proposal.campaign_id,
                                          on_call=self._calls(proposal, operation_key(proposal)))
        except DspUnavailable:
            return self._release(receipt, LastFailure.DSP_UNAVAILABLE)
        if proposal.decision_expires_at <= self.clock():  # 讀 DSP 期間過期:先於其他檢查
            return self._settle(receipt, None, Result.EXPIRED)
        signed = precheck(proposal, view) or self._sign(proposal)  # 硬規則一律先判
        if isinstance(signed, BlockCode):
            return self._settle(receipt, signed, Result.BLOCKED)
        assert view is not None  # 廣告不存在時執行前檢查已擋下  # noqa: S101
        receipt = replace(receipt, tenant=signed.tenant.name)  # 之後的事件記簽發當時的租戶
        return self._run(picked, receipt, signed, view)

    def _run(
        self, picked: PendingProposal, receipt: Receipt, signed: _Signed, view: CampaignView,
    ) -> Processed:
        """硬規則都過了:判可核可的兩關、開始一筆、送出。"""
        proposal = picked.proposal
        amount = guardrails.increase(proposal, view.budget)
        gated = self._gate(receipt, proposal, signed, view, amount)
        if isinstance(gated, Processed):
            return gated
        signed, held = gated
        allowance = (guardrails.increase_allowance(view.budget)  # 開始一筆記下判比例用的量
                     if proposal.action_type is ActionType.UPDATE_BUDGET else None)
        taken = self._take(picked, receipt, signed, (amount, allowance), held)
        if isinstance(taken, Processed):
            return taken
        # 停在未結案就不確認:每一筆嘗試寫入都順手續租,所以租約已經延長,交給對帳
        answer = self.dsp.write(proposal, taken.key, signed.token,
                                on_call=self._calls(proposal, taken.key))
        self._record(proposal, taken, answer, receipt)
        return Processed(Result.EXECUTED, taken.key)

    def _sign(
        self, proposal: Proposal, key: str | None = None, not_after: int | None = None,
    ) -> _Signed | BlockCode:
        """key 給對帳重送用:嘗試已經開過,一律用存下來的那把鍵簽,不從提案重算(增量 1 的規則)。
        not_after:用到人工核可時的最晚到期(Phase 6 增量 3)。"""
        now = int(self.clock().timestamp())  # 每次簽發都重讀時鐘
        try:
            grant = self.signer.grant(proposal, key or operation_key(proposal),
                                      self.config_path, now, not_after)
        except SigningRefused as refused:
            if refused.reason in _BUSINESS_REFUSALS:
                return _BUSINESS_REFUSALS[refused.reason]
            raise ExecutorHalted(refused.reason) from refused
        return _Signed(grant.token, datetime.fromtimestamp(grant.expires_at, UTC), grant.tenant)

    # ---- 決策新鮮度(Phase 8) ----
    def _stale_without_approval(
        self, proposal: Proposal, approvals: Iterable[Approval], now: datetime,
    ) -> bool:
        """決策過時、而且這一次靠的核可沒有一張此刻還算數。有效核可只放行它核准、而且這一次真的
        需要的那一關(使用者 2026-09-24 裁定;代碼審第 1 輪三席:原本任一關任一張有效核可都能免):
        處理一筆傳進來的是開始一筆前查好、用得到的那幾張(照增量 3 的核可查法,用不到的總曝險
        核可已經拿掉),同鍵重送傳進來的是重簽時核對過、這次重送靠的那幾張。過時的決策碰到還要
        一張新核可的關卡,由呼叫端直接擋下,不停進待核可。"""
        return guardrails.decision_stale(proposal, now) and not any(
            now.timestamp() < found.expires_at for found in approvals)

    # ---- 人工核可(Phase 6 增量 3) ----
    def _gate(
        self, receipt: Receipt, proposal: Proposal, signed: _Signed, view: CampaignView,
        amount: int,
    ) -> tuple[_Signed, dict[BlockCode, Approval]] | Processed:
        """硬規則都過了之後:判比例上限(可核可),並查好開始一筆可能用到的核可。沒超過比例就用
        不到那一張;超過又沒有有效核可就停在待核可。用到核可時重簽一次,憑證封頂在核可到期。"""
        held = self._approvals(proposal, signed.tenant, amount)
        if not guardrails.increase_too_large(proposal, view.budget):
            held.pop(RATIO, None)
        elif RATIO not in held:
            if guardrails.decision_stale(proposal, self.clock()):  # 過時的決策不等新核可(Phase 8)
                return self._settle(receipt, BlockCode.DECISION_STALE, Result.BLOCKED)
            return self._await(receipt, proposal, RATIO, self._ratio_stop(proposal, signed, amount))
        if not held:
            return signed, held
        capped = self._sign(proposal, not_after=min(a.expires_at for a in held.values()))
        if isinstance(capped, BlockCode):  # 兩次簽發之間設定檔改了:照硬規則擋下
            return self._settle(receipt, capped, Result.BLOCKED)
        if approval.scope_fingerprint(capped.tenant) != approval.scope_fingerprint(signed.tenant):
            return self._release(receipt, None)  # 核可是照上一次讀到的租戶設定驗的:下一輪重來
        return capped, held

    def _read_approval(
        self, token: str | None, proposal: Proposal, stage: BlockCode, tenant: Tenant,
        amount: int, now: datetime,
    ) -> Approval | None:
        """最新那一張核可此刻算不算數;不算數回 None(不回頭找舊的)。"""
        found = None if token is None else approval.read(token, self.approval_key)
        if found is None or not approval.holds(found, proposal, stage, tenant, amount, now):
            return None
        return found

    def _approvals(
        self, proposal: Proposal, tenant: Tenant, amount: int,
    ) -> dict[BlockCode, Approval]:
        """這份提案兩關各自最新、而且此刻算數的核可。開始一筆前先查好:用到核可要先封頂憑證。

        總曝險那一張只在「照現在的已用額度會超過門檻」時才留下:用不到的核可不該把憑證效期壓短
        (代碼審第 1 輪相容席)。這裡算的已用額度只是預判,開始一筆的交易裡照舊重算;預判沒超過、
        實際超過時照樣進待核可,下一輪再用上這張。"""
        with self.store.transaction() as tx:
            now = self.clock()
            tokens = {stage: self.store.latest_approval(tx, proposal, stage)
                      for stage in APPROVABLE}
            if tokens[AGGREGATE] is not None and (
                    _aggregate_used(tx, tenant.name, now) + amount <= tenant.aggregate_limit):
                tokens[AGGREGATE] = None
        held = {stage: self._read_approval(token, proposal, stage, tenant, amount, now)
                for stage, token in tokens.items()}
        return {stage: found for stage, found in held.items() if found is not None}

    @staticmethod
    def _ratio_stop(proposal: Proposal, signed: _Signed, amount: int) -> Stop:
        return Stop(StopKind.BUDGET_INCREASE_TOO_LARGE, proposal, operation_key(proposal),
                    signed.tenant.name, amount, None, None)

    def _await(
        self, receipt: Receipt, proposal: Proposal, stage: BlockCode, stop: Stop,
    ) -> Processed:
        with self.store.transaction() as tx:
            return self._await_in(tx, receipt, proposal, stage, stop, self.clock())

    def _await_in(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, proposal: Proposal,
        stage: BlockCode, stop: Stop, now: datetime,
    ) -> Processed:
        """停在待核可:處置寫待核可、擋下原因記這一關,同一個交易裡寫一列停下紀錄。收據失效就
        什麼都不寫(丟 LeaseLost 讓交易回滾)。"""
        if not self.store.await_approval(tx, receipt, now, stage):
            raise LeaseLost(operation_key(proposal))
        self.store.record_stop(tx, stop, now)
        return Processed(Result.AWAITING_APPROVAL, operation_key(proposal), stage)

    def process_awaiting(self) -> int:
        """每輪處理待核可:到期確認成已擋下;同任務有更新的修訂就確認成被取代;有這一關的有效
        核可就放回待處理、投遞次數歸零;其他不動。逐筆各自一個交易,回傳這一輪轉換了幾份。

        驗核可在這裡(執行行程),不在收件表模組:收件表只收、去重、記帳,不做授權。"""
        with self.store.transaction() as tx:
            waiting = self.store.awaiting(tx, self.clock())
        if not waiting:
            return 0
        try:  # 設定檔只在有待核可時讀一次(經簽發器,內容沒變就沿用);壞掉或不安全是系統故障
            tenants = self.signer.read_tenants(self.config_path)
        except SigningRefused as refused:
            raise ExecutorHalted(refused.reason) from refused
        return sum(self._settle_awaiting(item, tenants) for item in waiting)

    def _settle_awaiting(self, item: AwaitingProposal, tenants: tuple[Tenant, ...]) -> bool:
        proposal = item.message.proposal
        tenant = tenant_for(tenants, proposal.campaign_id)
        with self.store.transaction() as tx:
            now = self.clock()
            if proposal.decision_expires_at <= now:
                outcome = AwaitingOutcome.EXPIRED
            elif self.store.has_newer_revision(tx, proposal.task_id, proposal.revision):
                outcome = AwaitingOutcome.SUPERSEDED  # 分析端已改送新修訂:舊的放回會插隊
            elif attempt_store.latest(tx, operation_key(proposal)) is not None:
                # 同一把鍵已由另一份修訂開過嘗試(兩份都取到收據的競態):放回待處理,取件照既有
                # 規則依那把鍵的狀態確認,不要求第二張核可(代碼審第 3 輪外家席,事故 F3)
                outcome = AwaitingOutcome.RELEASED
            elif tenant is not None and self._approved(tx, item, tenant, now):
                outcome = AwaitingOutcome.RELEASED
            else:
                return False
            return self.store.settle_awaiting(tx, item.message, outcome, now, owner=self.owner)

    def _approved(
        self, tx: attempt_store.ExecutorTransaction, item: AwaitingProposal, tenant: Tenant,
        now: datetime,
    ) -> bool:
        """放回前判核可:金額用停在這一關時記下的那個(重新處理時會照當下現況再判一次)。"""
        amount = self.store.stop_amount(tx, item.message, item.stage)
        if amount is None:
            return False
        token = self.store.latest_approval(tx, item.message.proposal, item.stage)
        return self._read_approval(
            token, item.message.proposal, item.stage, tenant, amount, now) is not None

    def _resign(
        self, proposal: Proposal, view: CampaignView | None, key: str,
    ) -> _Signed | BlockCode | None:
        """同鍵重送前的簽發(憑證過期後重送、對帳查不到後重送共用):硬規則之後比照處理一筆重判
        比例,並核對這份提案用過的核可此刻都還算數;回 None 代表業務上不過(比例超過而沒有有效
        核可,或用過的核可已不算數),呼叫端照既有做法先作廢再判失敗。用到核可時憑證封頂在核可
        到期。總曝險不重判:預留在開始一筆時已寫下,一直算在已用額度裡(Phase 6 增量 3)。"""
        signed = self._sign(proposal, key)
        if isinstance(signed, BlockCode):
            return signed
        assert view is not None  # 呼叫端先跑過執行前檢查  # noqa: S101
        amount = guardrails.increase(proposal, view.budget)
        with self.store.transaction() as tx:
            now = self.clock()
            needed: list[tuple[BlockCode, str | None]] = list(
                self.store.used_approvals(tx, proposal))
            if guardrails.increase_too_large(proposal, view.budget):
                needed.append((RATIO, self.store.latest_approval(tx, proposal, RATIO)))
        found = [self._read_approval(token, proposal, stage, signed.tenant, amount, now)
                 for stage, token in needed]
        if any(item is None for item in found):
            return None
        if not found:
            return signed
        capped = self._sign(proposal, key,
                            not_after=min(item.expires_at for item in found if item is not None))
        if isinstance(capped, _Signed) and approval.scope_fingerprint(
                capped.tenant) != approval.scope_fingerprint(signed.tenant):
            return None  # 核可是照上一次讀到的租戶設定驗的:範圍變了就不算數(代碼審第 1 輪外家席)
        if not isinstance(capped, _Signed):
            return capped
        # 靠的核可仍是最新這件事,在轉嘗試中的同一個交易裡核對(代碼審第 2、3 輪外家席):跟處理
        # 一筆在開始一筆交易裡的核對是同一個判斷點,之後才呼叫 DSP
        return replace(capped, approvals=tuple(item for item in found if item is not None))

    def _in_flight_again(
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None, signed: _Signed,
    ) -> AttemptRow:
        """同鍵重送前轉回嘗試中;靠核可的,同一個交易裡核對每張仍是那一關最新的;決策新鮮度也在
        這個交易裡用它的時間再判一次(Phase 8,跟開始一筆的交易同一個判斷點)。"""
        def still_latest(tx: attempt_store.ExecutorTransaction) -> None:
            if self._stale_without_approval(proposal, signed.approvals, self.clock()):
                raise _DecisionStale(row.key)
            if any(self._superseded(tx, proposal, item.stage, item) for item in signed.approvals):
                raise _ApprovalSuperseded(row.key)
        return self._write(row, A.IN_FLIGHT, receipt, capability_expires_at=signed.expires_at,
                           guard=still_latest)

    def _settle(self, receipt: Receipt, code: BlockCode | None, kind: Result) -> Processed:
        """確認成已過期或已擋下;以收據為條件(提案已經是處理中,不再以「仍待處理」為條件)。"""
        with self.store.transaction() as tx:
            now = self.clock()
            done = (self.store.ack_expired(tx, receipt, now) if code is None
                    else self.store.ack_blocked(tx, receipt, now, code))
        return Processed(kind, block_code=code) if done else Processed(Result.LEASE_LOST)

    def _release(self, receipt: Receipt, failure: LastFailure | None) -> Processed:
        """沒能開始嘗試:記下原因、放掉租約,下一輪能再取(每取一次算一次投遞,用完進死信)。"""
        with self.store.transaction() as tx:
            done = self.store.release(tx, receipt, self.clock(), failure)
        return Processed(Result.DEFERRED) if done else Processed(Result.LEASE_LOST)

    def _take(  # noqa: PLR0911 - 每個出口對應開始一筆的一種結果
        self, picked: PendingProposal, receipt: Receipt, signed: _Signed,
        increase: tuple[int, int | None], held: dict[BlockCode, Approval],
    ) -> AttemptRow | Processed:
        """開始一筆:在核對收據仍有效的同一個交易裡做;鍵已存在就依既有那把鍵的狀態分流。

        總曝險(Phase 6):預留金額 = 新預算減處理一筆開頭讀到的目前預算(版本已變排在前面,走到
        這裡的目前預算一定是提案觀察到的那一版),小於 0 算 0;額度不夠又沒有有效核可就停在
        待核可並寫停下紀錄。held 是開始前查好的核可:在這個交易裡用當下時間再判一次到期。
        increase 是(加的量, 比例允許加的量;暫停為空),後者只記進第一列給副作用核對(增量 3)。"""
        amount, allowance = increase
        with self.store.transaction() as tx:
            now = self.clock()
            if not self.store.extend(tx, receipt, now):  # 已被取代、內容不同、或租約已不是我的
                return Processed(Result.LEASE_LOST)
            # 讀 DSP、讀設定檔簽發都可能剛好跨過到期時間:在開始一筆的同一個交易裡再判一次
            if picked.proposal.decision_expires_at <= now:
                self.store.ack_expired(tx, receipt, now)
                return Processed(Result.EXPIRED)
            if any(self._superseded(tx, picked.proposal, stage, found)
                   for stage, found in held.items()):  # 查好之後又有人簽了更新的核可:下一輪重判
                self.store.release(tx, receipt, now, None)
                return Processed(Result.DEFERRED)
            live = {stage: found for stage, found in held.items()
                    if now.timestamp() < found.expires_at}
            stopped = self._stop_before_begin(tx, receipt, picked.proposal, signed, amount, held,
                                              live, now)
            if stopped is not None:
                return stopped
            reservation = attempt_store.Reservation(
                signed.tenant.name, amount, signed.tenant.aggregate_limit,
                approved=AGGREGATE in live, ratio_allowance=allowance,
                max_budget=signed.tenant.max_budget)
            try:
                begun = attempt_store.begin(tx, picked.proposal, now,
                                            capability_expires_at=signed.expires_at,
                                            reservation=reservation, by=self._by)
            except attempt_store.TooManyUnresolved:  # 正常觸發:等未結案數降下來
                snapshot = attempt_store.AggregateLimitReached(
                    _aggregate_used(tx, reservation.tenant, now), reservation.limit)
                self.store.record_stop(tx, self._stop(
                    StopKind.TABLE_FULL, picked.proposal, reservation, snapshot), now)
                self.store.release(tx, receipt, now, LastFailure.TABLE_FULL)
                return Processed(Result.DEFERRED)
            except attempt_store.AggregateLimitReached as full:  # 總曝險已滿又沒有有效核可
                return self._await_in(tx, receipt, picked.proposal, AGGREGATE, self._stop(
                    StopKind.AGGREGATE_LIMIT_REACHED, picked.proposal, reservation, full), now)
            except attempt_store.CorruptedAttemptRow as exc:  # 算額度時讀到壞掉的舊快照
                raise ExecutorHalted("unreadable_attempt") from exc  # 比照對帳:不猜,停下讓人看
            except attempt_store.CampaignLocked:  # 同廣告兩份提案被兩個工作者同時取出時會走到
                self.store.release(tx, receipt, now, None)
                return Processed(Result.DEFERRED)
            if begun.created:
                self._audit(tx, picked.proposal, begun, live, reservation, now)
                return begun.row
            return self._existing_key(tx, begun.row, receipt, now)

    def _stop_before_begin(  # noqa: PLR0913 - 開始一筆的交易裡重判要用的每一樣
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, proposal: Proposal,
        signed: _Signed, amount: int, held: dict[BlockCode, Approval],
        live: dict[BlockCode, Approval], now: datetime,
    ) -> Processed | None:
        """比例的核可在取件後過期就回待核可;決策已過時(Phase 8)又沒有這一次真的需要的有效
        核可就擋下,不停進待核可等新的。live 可能被補上或拿掉總曝險那一張。"""
        stale = guardrails.decision_stale(proposal, now)
        if RATIO in held and RATIO not in live:
            if stale:  # 過時的決策不等新核可
                return self._block_stale(tx, receipt, now)
            return self._await_in(tx, receipt, proposal, RATIO,
                                  self._ratio_stop(proposal, signed, amount), now)
        if not stale:
            return None
        verdict = self._stale_verdict(tx, proposal, signed.tenant, amount, live, now)
        if verdict == "block":
            return self._block_stale(tx, receipt, now)
        if verdict == "retry":  # 憑證沒壓到核可到期:放掉重來,下一輪預判拿到核可再簽
            self.store.release(tx, receipt, now, None)
            return Processed(Result.DEFERRED)
        return None

    def _block_stale(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
    ) -> Processed:
        self.store.ack_blocked(tx, receipt, now, BlockCode.DECISION_STALE)
        return Processed(Result.BLOCKED, block_code=BlockCode.DECISION_STALE)

    def _stale_verdict(
        self, tx: attempt_store.ExecutorTransaction, proposal: Proposal, tenant: Tenant,
        amount: int, live: dict[BlockCode, Approval], now: datetime,
    ) -> Literal["go", "block", "retry"]:
        """決策已過時:只有「這一次真的需要、而且此刻有效」的核可才放行(使用者 2026-09-24
        裁定)。總曝險需不需要核可以這個交易裡、握著寫入鎖重算的已用額度為準,跟開始一筆的判斷
        同一個數字(代碼審第 2 輪三席:開始前另一個交易的預判會跟實際不同)。需要時:開始前就查好
        的那張(憑證已壓到它的到期)放行;開始前沒查到、最新那張此刻算數,憑證沒壓短,放掉重來,
        不當場放行(代碼審第 3 輪外家兩席:憑證會比核可活得久,違反增量 3 [S376]);都沒有就擋下,
        不停進待核可等新的。比例那一關到這裡要嘛不需要、要嘛 live 裡有有效核可。"""
        needed = amount > 0 and (
            _aggregate_used(tx, tenant.name, now) + amount > tenant.aggregate_limit)
        if not needed:
            return "go" if RATIO in live else "block"
        if AGGREGATE in live:
            return "go"
        found = self._read_approval(self.store.latest_approval(tx, proposal, AGGREGATE),
                                    proposal, AGGREGATE, tenant, amount, now)
        return "block" if found is None else "retry"

    def _superseded(
        self, tx: attempt_store.ExecutorTransaction, proposal: Proposal, stage: BlockCode,
        held: Approval,
    ) -> bool:
        """開始一筆前查好的那張已不是這一關最後寫進核可表的那張(代碼審第 1 輪外家席)。"""
        token = self.store.latest_approval(tx, proposal, stage)
        return token is None or approval.approval_id(token) != held.approval_id

    def _audit(
        self, tx: attempt_store.ExecutorTransaction, proposal: Proposal,
        begun: attempt_store.Begun, live: dict[BlockCode, Approval],
        reservation: attempt_store.Reservation, now: datetime,
    ) -> None:
        """核可生效就在開始一筆的同一個交易裡寫核可使用紀錄:比例那一關有核可就記;總曝險那一關
        只在真的超過門檻、靠核可放行時記(有核可但沒用上不記)。"""
        uses = []
        if RATIO in live:
            uses.append(ApprovalUse(live[RATIO].approval_id, proposal, begun.row.key,
                                    reservation.tenant, RATIO, reservation.amount, None, None))
        if AGGREGATE in live and begun.over_limit is not None:
            uses.append(ApprovalUse(live[AGGREGATE].approval_id, proposal, begun.row.key,
                                    reservation.tenant, AGGREGATE, reservation.amount,
                                    begun.over_limit.used, begun.over_limit.limit))
        for use in uses:
            self.store.record_approval_use(tx, use, now)

    def _existing_key(
        self, tx: attempt_store.ExecutorTransaction, row: AttemptRow, receipt: Receipt,
        now: datetime,
    ) -> Processed:
        """開始一筆時鍵已存在:依既有那把鍵的狀態分流(在呼叫端的同一個交易裡);確認的事件標
        「依既有結果確認」。"""
        if row.state is A.VERIFIED:
            if not self.store.ack_handed_off(tx, receipt, now, from_existing=True):
                raise LeaseLost(row.key)
            return Processed(Result.HANDED_OFF_TO_EXISTING, row.key)
        if row.state is A.FAILED:  # 人判過不做的操作不能貼成已交給執行
            code = block_code_for_failure(row.code)
            if not self.store.ack_blocked(tx, receipt, now, code, from_existing=True):
                raise LeaseLost(row.key)
            return Processed(Result.BLOCKED, row.key, code)
        self.store.release(tx, receipt, now, None)  # 防線:未結案的鍵歸對帳管
        return Processed(Result.DEFERRED)

    @staticmethod
    def _stop(
        kind: StopKind, proposal: Proposal, reservation: attempt_store.Reservation,
        full: attempt_store.AggregateLimitReached | None,
    ) -> Stop:
        return Stop(kind, proposal, operation_key(proposal), reservation.tenant or None,
                    reservation.amount, None if full is None else full.used,
                    None if full is None else full.limit)

    def _ack_terminal(
        self, tx: attempt_store.ExecutorTransaction, row: AttemptRow, receipt: Receipt,
        now: datetime, block_code: BlockCode | None = None,
    ) -> None:
        """確認也是帶收據的條件寫入:0 列就是收據失效,跟其他寫入一樣放棄這把鍵。
        block_code 給重跑執行前檢查的路徑用:檢查查到的原因比嘗試結果代碼更具體時由呼叫端指定。"""
        if row.state is A.VERIFIED:
            done = self.store.ack_handed_off(tx, receipt, now)
        elif row.state is A.FAILED:
            done = self.store.ack_blocked(tx, receipt, now,
                                          block_code or block_code_for_failure(row.code))
        else:
            return
        if not done:
            raise LeaseLost(row.key)

    # ---- 第 6 步:寫結果 ----
    def _retry_busy_begin(self, receipt: Receipt | None, attempt: Callable[[], _T]) -> _T:
        """記下平台回覆的那一個交易,開交易就鎖不到(什麼都沒寫)時在限度內整筆重做(F7 效能計劃第 2
        部分):最多重試 RESULT_WRITE_RETRIES 次,退避經注入的睡眠;每次再試之前讀這張收據的租約實際
        到期時間,剩餘不夠等一次鎖加退避加餘裕、或已經不是自己的租約,就不再試、照舊往外丟忙碌。
        沒有收據的舊鍵沒有租約可讀,只受次數限制(輸家靠嘗試紀錄的序號條件寫 0 列)。只接開交易鎖不到
        這一種;其他錯誤(含交易開起來之後的忙碌)一律照舊往外丟。計數是這次呼叫的區域變數。"""
        retries = 0
        while True:
            try:
                return attempt()
            except InboxBusyNotStarted:
                if retries >= RESULT_WRITE_RETRIES:
                    raise
                pause = RESULT_WRITE_BACKOFF_SECONDS[retries]
                if receipt is not None and not self._lease_allows(receipt, pause):
                    raise
                retries += 1
                self.sleep(pause)

    def _lease_allows(self, receipt: Receipt, pause: float) -> bool:
        """租約還夠不夠再試一次:剩餘時間(資料庫裡的到期時間減注入時鐘的現在)至少要等一次鎖加退避
        加餘裕。讀不到租約就不夠。"""
        until = self.store.lease_until(receipt)
        if until is None:
            return False
        needed = (timedelta(seconds=sqlitekit.BUSY_TIMEOUT_SECONDS + pause)
                  + RESULT_WRITE_LEASE_MARGIN)
        return until - self.clock() >= needed

    def _write(  # noqa: PLR0913 - 關鍵字參數都是這次寫入要記的欄位,各有預設值
        self, row: AttemptRow, target: AttemptState, receipt: Receipt | None, *,
        code: OutcomeCode | None = None, written_version: int | None = None,
        capability_expires_at: datetime | None = None, block_code: BlockCode | None = None,
        guard: Callable[[attempt_store.ExecutorTransaction], None] | None = None,
    ) -> AttemptRow:
        """嘗試寫入:同一個交易裡先核對收據並順手續租(對不上丟 LeaseLost),寫到終點就同時確認。

        續租就是 Phase 0 要的續期機制:活著的工作者每寫一筆就把租約往後推,慢的 DSP 呼叫不會
        讓它被當成當機;真的當機就不再續,租約到期後別人才能接手。
        receipt 是 None 只給 Phase 3 時代留下、收件表沒有處理中那一列的鍵用(沒有訊息可確認)。
        記下平台回覆的寫入(目標不是嘗試中)開交易鎖不到時在限度內重試;轉回嘗試中不在範圍(F7 效能
        計劃第 2 部分〈不改〉),鎖不到照舊往外丟。"""
        def once() -> AttemptRow:
            return self._write_once(row, target, receipt, code=code,
                                    written_version=written_version,
                                    capability_expires_at=capability_expires_at,
                                    block_code=block_code, guard=guard)
        if target is A.IN_FLIGHT:
            return once()
        return self._retry_busy_begin(receipt, once)

    def _write_once(  # noqa: PLR0913 - 同 _write
        self, row: AttemptRow, target: AttemptState, receipt: Receipt | None, *,
        code: OutcomeCode | None, written_version: int | None,
        capability_expires_at: datetime | None, block_code: BlockCode | None,
        guard: Callable[[attempt_store.ExecutorTransaction], None] | None,
    ) -> AttemptRow:
        with self.store.transaction() as tx:
            now = self.clock()
            if receipt is not None and not self.store.extend(tx, receipt, now):
                raise LeaseLost(row.key)
            if guard is not None:  # 不能轉就丟例外(核可被取代、決策已過時),交易回滾
                guard(tx)
            new = attempt_store.transition(
                tx, row.key, row.seq, target, now, code=code,
                written_version=written_version, capability_expires_at=capability_expires_at,
                by=self._by)
            if new is None:
                _no_progress(row.key, receipt)
            if receipt is not None and new.state in TERMINAL_STATES:
                self._ack_terminal(tx, new, receipt, now, block_code)
        return new

    def _record(
        self, proposal: Proposal, row: AttemptRow, answer: WriteAnswer, receipt: Receipt | None,
    ) -> bool:
        """寫結果整段(處理一筆與對帳重送共用);回傳這段有沒有 DSP 呼叫沒拿到結論。"""
        reaction = react(answer)
        if reaction.target is A.COMMITTED_UNVERIFIED:
            return self._verify(proposal, self._write(
                row, reaction.target, receipt, written_version=answer.version_after), receipt)
        if reaction.capability_expired:
            return self._after_expiry(proposal, self._write(row, A.UNKNOWN, receipt), receipt)
        self._write(row, reaction.target, receipt, code=reaction.code)
        if reaction.halt:
            raise ExecutorHalted(str(reaction.code))
        return reaction.target is A.UNKNOWN

    def _after_expiry(  # noqa: PLR0911 - 每個出口對應重送前的一種結果
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None,
    ) -> bool:
        """DSP 明確沒寫:重讀 DSP、重跑檢查;通過就重讀時鐘重簽、同鍵重送一次。"""
        try:
            view = self.dsp.read_campaign(
                proposal.campaign_id, on_call=self._calls(proposal, row.key))
        except DspUnavailable:
            return True  # 留在結果不明,交給對帳
        live = proposal.decision_expires_at > self.clock()
        checked = precheck(proposal, view)
        signed = (self._resign(proposal, view, row.key)  # 設定檔壞掉在這裡停機
                  if live and checked is None else None)
        if not isinstance(signed, _Signed):  # 業務上沒通過:DSP 明確沒寫這一次,不再送
            return self._not_resent(proposal, row, receipt, _kept_reason_or_none(live, checked))
        try:
            row = self._in_flight_again(proposal, row, receipt, signed)
        except attempt_store.SendLimitReached:
            self._write(row, A.ESCALATED, receipt, code=C.SEND_LIMIT_REACHED)
            return False
        except _ApprovalSuperseded:
            return self._not_resent(proposal, row, receipt, None)
        except _DecisionStale:
            return self._not_resent(proposal, row, receipt, BlockCode.DECISION_STALE)
        answer = self.dsp.write(proposal, row.key, signed.token,
                                on_call=self._calls(proposal, row.key))
        reaction = react(answer)
        if reaction.capability_expired:  # 用新讀的時間重簽後仍過期:時鐘或設定有問題
            self._write(row, A.ESCALATED, receipt, code=C.CAPABILITY_REJECTED)
            return False
        return self._record(proposal, row, answer, receipt)

    def _not_resent(
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None,
        block: BlockCode | None,
    ) -> bool:
        """憑證過期後業務上不能再送:送過多次時更早的請求可能還在路上,先作廢才能判失敗。"""
        if row.send_count > 1:
            return self._void_then_fail(proposal, row, receipt, block)
        self._write(row, A.FAILED, receipt, code=C.NOT_HAPPENED, block_code=block)
        return False

    # ---- 第 7 步:執行後驗證 ----
    def _verify(self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None) -> bool:
        """回傳有沒有 DSP 呼叫失敗(讀取失敗記一次查證逾時)。"""
        try:
            verified = self._check_applied(proposal, row)
        except DspUnavailable:
            self._verification_timeout(row, receipt)
            return True
        if verified:
            self._write(row, A.VERIFIED, receipt)
        else:
            self._write(row, A.ESCALATED, receipt, code=C.VERIFICATION_MISMATCH)
        return False

    def _check_applied(self, proposal: Proposal, row: AttemptRow) -> bool:
        view = self.dsp.read_campaign(
            proposal.campaign_id, on_call=self._calls(proposal, row.key))
        written = row.written_version
        if view is None or written is None:
            return False
        if view.version == written:
            return intent_holds(proposal, view)
        if view.version > written:  # 之後有人又改了:只證明我們的寫入套用過一次,內容也要對
            record = self.dsp.operation_record(
                row.key, on_call=self._calls(proposal, row.key))
            return (record is not None and record.version_after == written
                    and record_matches(proposal, record))
        return False

    def _verification_timeout(self, row: AttemptRow, receipt: Receipt | None) -> None:
        """記一次查證逾時;開交易鎖不到時跟寫結果一樣在限度內重試(次數用完轉人工的那一筆寫入是
        寫結果函式,自己另外重試、另外判期限)。"""
        def once() -> AttemptRow | None:
            with self.store.transaction() as tx:
                now = self.clock()
                if receipt is not None and not self.store.extend(tx, receipt, now):
                    raise LeaseLost(row.key)
                return attempt_store.record_verification_timeout(tx, row.key, row.seq, now,
                                                                 by=self._by)
        try:
            recorded = self._retry_busy_begin(receipt, once)
        except attempt_store.VerificationTimeoutLimitReached:
            self._write(row, A.ESCALATED, receipt, code=C.VERIFICATION_TIMEOUTS_EXHAUSTED)
            return
        if recorded is None:
            _no_progress(row.key, receipt)

    # ---- 對帳(Phase 3 增量 4;Phase 4 增量 1 接上收件表) ----
    def reconcile_all(self) -> bool:
        """每一輪處理兩種鍵:未結案的嘗試(含嘗試中與轉人工),以及嘗試已到終點、收件表卻還是
        處理中的(人工處置只寫嘗試紀錄,或中途當機留下的漏網)。依最新一列寫入時間由舊到新。

        回傳這一輪有沒有 DSP 呼叫失敗:有就讓啟動程式這輪結束後休息,DSP 變慢時不連續全速打它。
        對帳之前先補寫欠著的呼叫紀錄;每一把鍵開始前看待寫清單,到上限就不再對帳(不送新的 DSP 呼叫),
        所以一開始就滿或對帳到一半滿了都會停。每一把鍵結束時也補寫一次。
        """
        self.flush_calls()
        with self.store.transaction() as tx:
            keys = list(attempt_store.unresolved_keys(tx))
            in_progress, unreadable = self.store.in_progress_keys(tx)
            for key in in_progress:
                row = attempt_store.latest(tx, key)
                if row is not None and row.state in TERMINAL_STATES and key not in keys:
                    keys.append(key)
        troubled = False
        corrupted = list(unreadable)
        for key in keys:
            if len(self._pending) >= MAX_PENDING_CALLS:  # 開始前或上一把鍵結束時剛補寫過
                break
            try:
                troubled = self._reconcile(key) or troubled
            except LeaseLost:
                continue  # 租約被接手:這把鍵這一輪放棄
            except CorruptedInboxRow as exc:  # 這把鍵找不到、同任務又有壞列:先記下,別拖累其他鍵
                corrupted.append(str(exc))
            finally:
                self.flush_calls()  # 這一把鍵結束時補寫欠著的呼叫紀錄
        if corrupted:  # 健康的都處理完了,才因為讀不回來的處理中列停下讓人看(不能默默跳過)
            raise ExecutorHalted("unreadable_message")
        return troubled

    def _reconcile(self, key: str) -> bool:
        with self.store.transaction() as tx:
            now = self.clock()
            try:
                row = attempt_store.latest(tx, key)
                proposal = attempt_store.snapshot(tx, key)
            except attempt_store.CorruptedAttemptRow as exc:  # 讀不回來:不猜,停下讓人看
                raise ExecutorHalted("unreadable_attempt") from exc
            assert row is not None  # noqa: S101 - 清單來自同一張表
            taken = self._take_over(tx, proposal, key, now)
            if taken is False:  # 別人持有而且沒到期:跳過這一輪
                return False
            receipt = taken
            if row.state in TERMINAL_STATES:
                if receipt is not None:
                    self._ack_terminal(tx, row, receipt, now)
                return False
            if row.state is A.ESCALATED:  # 只能由人工處置離開:接手就是續租
                return False
            if row.state is A.IN_FLIGHT:
                if receipt is None and row.written_at > now - VISIBILITY_TIMEOUT:
                    return False  # 舊鍵剛寫下嘗試中:可能有別的工作者正在對帳它,不碰
                # 過期工作者留下的嘗試中(有收據:租約已過期被接手;沒收據:寫下超過一個租約時間
                # 的舊鍵):先轉成結果不明,再照結果不明對帳。舊鍵不必等重啟,對帳自己收
                moved = attempt_store.transition(tx, key, row.seq, A.UNKNOWN, now, by=self._by)
                if moved is None:
                    _no_progress(key, receipt)
                row = moved
        if row.state is A.COMMITTED_UNVERIFIED:
            return self._verify(proposal, row, receipt)
        return self._reconcile_unknown(proposal, row, receipt)

    def _take_over(
        self, tx: attempt_store.ExecutorTransaction, proposal: Proposal, key: str, now: datetime,
    ) -> Receipt | Literal[False] | None:
        """原子接手這把鍵對應的處理中訊息。回收據;沒有處理中訊息(Phase 3 留下的舊資料)回 None;
        別人持有而且沒到期回 False。"""
        # 讀不回來又找不到這把鍵時丟 CorruptedInboxRow:不能當成舊資料走不帶收據的路;
        # 由對帳入口記下,處理完其他鍵後停機
        message = self.store.in_progress_for(tx, proposal.task_id, key)
        if message is None:
            return None
        receipt = self.store.take_over(tx, message, now, self.owner)
        if receipt is None:
            return False
        # 接手的這把鍵開始一筆時已經簽發過:之後的事件記那時的租戶
        return replace(receipt, tenant=attempt_store.first_row_tenant(tx, key))

    def _reconcile_unknown(
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None,
    ) -> bool:
        try:
            record = self.dsp.operation_record(
                row.key, on_call=self._calls(proposal, row.key))
        except DspUnavailable:
            self._verification_timeout(row, receipt)
            return True
        if record is not None:
            return self._found(proposal, row, record, receipt)
        return self._reconcile_not_found(proposal, row, receipt)

    def _reconcile_not_found(  # noqa: PLR0911 - 每個出口對應重送前的一種結果
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None,
    ) -> bool:
        """查不到這把鍵:重讀廣告、重跑執行前檢查;通過就同鍵重送,不過就先作廢再判失敗。"""
        try:
            view = self.dsp.read_campaign(
                proposal.campaign_id, on_call=self._calls(proposal, row.key))
        except DspUnavailable:
            self._verification_timeout(row, receipt)
            return True
        if view is None:  # 模擬 DSP 沒有建立或刪除廣告的介面:不存在就代表從來不存在
            self._write(row, A.FAILED, receipt, code=C.CAMPAIGN_NOT_FOUND)
            return False
        live = proposal.decision_expires_at > self.clock()
        checked = precheck(proposal, view)
        signed = (self._resign(proposal, view, row.key)  # 設定檔壞掉在這裡停機
                  if live and checked is None else None)
        if not isinstance(signed, _Signed):  # 業務上不過:先作廢,作廢成功才判失敗
            return self._void_then_fail(proposal, row, receipt,
                                        _kept_reason_or_none(live, checked))
        try:
            row = self._in_flight_again(proposal, row, receipt, signed)
        except attempt_store.SendLimitReached:
            self._write(row, A.ESCALATED, receipt, code=C.SEND_LIMIT_REACHED)
            return False
        except _ApprovalSuperseded:
            return self._void_then_fail(proposal, row, receipt, None)
        except _DecisionStale:
            return self._void_then_fail(proposal, row, receipt, BlockCode.DECISION_STALE)
        return self._record(proposal, row, self.dsp.write(
            proposal, row.key, signed.token, on_call=self._calls(proposal, row.key)),
                            receipt)

    def _found(
        self, proposal: Proposal, row: AttemptRow, record: OperationRecord,
        receipt: Receipt | None,
    ) -> bool:
        if not record_matches(proposal, record):
            self._write(row, A.ESCALATED, receipt, code=C.IDEMPOTENCY_CONFLICT)
            return False
        row = self._write(row, A.COMMITTED_UNVERIFIED, receipt,
                          written_version=record.version_after)
        return self._verify(proposal, row, receipt)

    def _void_then_fail(
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None,
        block_code: BlockCode | None = None,
    ) -> bool:
        now = int(self.clock().timestamp())
        try:
            token = self.signer.sign_void(proposal, row.key, self.config_path, now)
        except SigningRefused as refused:
            if refused.reason != "campaign_not_allowed":  # 設定檔壞掉或不安全:系統故障
                raise ExecutorHalted(refused.reason) from refused
            # 不能作廢,就證明不了舊請求不會晚到提交
            self._write(row, A.ESCALATED, receipt, code=C.CANNOT_PROVE_NOT_HAPPENED)
            return False
        answer = self.dsp.void(proposal, row.key, token,
                               on_call=self._calls(proposal, row.key))
        reaction = react_void(answer)
        if reaction.outcome is VoidOutcome.FOUND:
            assert answer.record is not None  # noqa: S101 - 這一列的條件保證
            return self._found(proposal, row, answer.record, receipt)
        if reaction.outcome is VoidOutcome.TIMEOUT:
            self._verification_timeout(row, receipt)
            return True
        target = A.FAILED if reaction.outcome is VoidOutcome.FAILED else A.ESCALATED
        self._write(row, target, receipt, code=reaction.code, block_code=block_code)
        if reaction.halt:
            raise ExecutorHalted(str(reaction.code))
        return False
