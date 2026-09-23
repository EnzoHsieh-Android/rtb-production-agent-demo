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
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, NoReturn, Protocol

from rtb.domain._checks import is_plain_int
from rtb.domain.attempt import TERMINAL_STATES, AttemptState, OutcomeCode, operation_key
from rtb.domain.proposal import ActionType, Proposal
from rtb.executor import attempt_store
from rtb.executor.attempt_store import AttemptRow
from rtb.executor.capability_signer import LIFETIME_SECONDS, SigningRefused
from rtb.executor.inbox_store import (
    VISIBILITY_TIMEOUT,
    BlockCode,
    CorruptedInboxRow,
    InboxStore,
    LastFailure,
    PendingProposal,
    Receipt,
    block_code_for_failure,
)

A = AttemptState
C = OutcomeCode


@dataclass(frozen=True)
class CampaignView:
    """DSP 上廣告的現況(執行前檢查與執行後驗證要看的欄位)。"""

    budget: int
    status: str
    version: int


@dataclass(frozen=True)
class WriteAnswer:
    """DSP 對一次寫入的回應。status 是 None 代表沒拿到回應(逾時、斷線、回應讀不懂)。"""

    status: int | None
    error: str | None = None
    version_after: int | None = None


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


class DspUnavailable(Exception):
    """讀取沒有成功(不含「廣告不存在」):逾時、斷線、5xx、回應讀不懂。"""


class DspPort(Protocol):
    def read_campaign(self, campaign_id: str) -> CampaignView | None:
        """廣告不存在回 None;其他讀取失敗丟 DspUnavailable。"""
        ...

    def write(self, proposal: Proposal, key: str, token: str) -> WriteAnswer:
        """帶憑證與冪等鍵送出寫入;不丟例外,沒拿到回應就回 status 為 None 的回應。"""
        ...

    def operation_version(self, key: str) -> int | None:
        """用冪等鍵查 DSP 的操作紀錄,回寫入後版本;查不到回 None,查詢失敗丟 DspUnavailable。"""
        ...

    def operation_record(self, key: str) -> OperationRecord | None:
        """用冪等鍵查完整操作紀錄;查不到回 None,查詢失敗丟 DspUnavailable。"""
        ...

    def void(self, proposal: Proposal, key: str, token: str) -> VoidAnswer:
        """請 DSP 作廢這把鍵;不丟例外,沒拿到回應就回 status 為 None 的回應。"""
        ...


class Signer(Protocol):
    def sign(self, proposal: Proposal, operation_key: str, config_path: Path, now: int) -> str: ...

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


def precheck(proposal: Proposal, view: CampaignView | None) -> BlockCode | None:
    """執行前檢查裡看 DSP 現況的三項;「不在投放」排在版本之前,代碼比較有意義。"""
    if view is None:
        return BlockCode.CAMPAIGN_NOT_FOUND
    if view.status != "active":
        return BlockCode.CAMPAIGN_NOT_ACTIVE
    if view.version != proposal.campaign_version_observed:
        return BlockCode.VERSION_CHANGED
    return None


def _version_changed_or_none(live: bool, checked: BlockCode | None) -> BlockCode | None:
    """重跑執行前檢查查到版本已變:收件口確認成「版本已變」(分析端要據此重新規劃),嘗試結果代碼
    照舊記「沒發生」。其他原因(含提案過期、權限不過)回空值,照嘗試結果代碼確認。
    過期與版本已變同時成立時過期優先,跟第一次處理時的順序一致(代碼審第 2 輪)。
    使用者 2026-09-23 裁定把 Phase 5 [S310] 擴到憑證過期後重讀與對帳查不到兩條路徑。"""
    return BlockCode.VERSION_CHANGED if live and checked is BlockCode.VERSION_CHANGED else None


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


@dataclass(frozen=True)
class Executor:
    store: InboxStore
    dsp: DspPort
    signer: Signer
    config_path: Path
    clock: Callable[[], datetime]  # 單一動作的簡單回呼:照專案慣例用 Callable,不另開 Protocol
    owner: str = "executor"  # 租約擁有者:啟動程式傳行程編號加啟動時間

    def process_one(self) -> Processed:
        with self.store.transaction() as tx:
            delivery = self.store.receive(tx, self.clock(), self.owner)
        if delivery is None:
            return Processed(Result.IDLE)
        try:
            return self._process(delivery.message, delivery.receipt)
        except LeaseLost:
            return Processed(Result.LEASE_LOST)

    def _process(self, picked: PendingProposal, receipt: Receipt) -> Processed:
        proposal = picked.proposal
        if proposal.decision_expires_at <= self.clock():  # 過期先判,不必去讀 DSP
            return self._settle(receipt, None, Result.EXPIRED)
        try:
            view = self.dsp.read_campaign(proposal.campaign_id)
        except DspUnavailable:
            return self._release(receipt, LastFailure.DSP_UNAVAILABLE)
        if proposal.decision_expires_at <= self.clock():  # 讀 DSP 期間過期:先於其他檢查
            return self._settle(receipt, None, Result.EXPIRED)
        signed = precheck(proposal, view) or self._sign(proposal)
        if isinstance(signed, BlockCode):
            return self._settle(receipt, signed, Result.BLOCKED)
        taken = self._take(picked, receipt, signed)
        if isinstance(taken, Processed):
            return taken
        # 停在未結案就不確認:每一筆嘗試寫入都順手續租,所以租約已經延長,交給對帳
        self._record(proposal, taken, self.dsp.write(proposal, taken.key, signed.token), receipt)
        return Processed(Result.EXECUTED, taken.key)

    def _sign(self, proposal: Proposal, key: str | None = None) -> _Signed | BlockCode:
        """key 給對帳重送用:嘗試已經開過,一律用存下來的那把鍵簽,不從提案重算(增量 1 的規則)。"""
        now = int(self.clock().timestamp())  # 每次簽發都重讀時鐘
        try:
            token = self.signer.sign(proposal, key or operation_key(proposal),
                                     self.config_path, now)
        except SigningRefused as refused:
            if refused.reason in _BUSINESS_REFUSALS:
                return _BUSINESS_REFUSALS[refused.reason]
            raise ExecutorHalted(refused.reason) from refused
        return _Signed(token, datetime.fromtimestamp(now + LIFETIME_SECONDS, UTC))

    def _settle(self, receipt: Receipt, code: BlockCode | None, kind: Result) -> Processed:
        """確認成已過期或已擋下;以收據為條件(提案已經是處理中,不再以「仍待處理」為條件)。"""
        with self.store.transaction() as tx:
            now = self.clock()
            done = (self.store.ack_expired(tx, receipt, now) if code is None
                    else self.store.ack_blocked(tx, receipt, now, code))
        return Processed(kind, block_code=code) if done else Processed(Result.LEASE_LOST)

    def _release(self, receipt: Receipt, failure: LastFailure) -> Processed:
        """沒能開始嘗試:記下原因、放掉租約,下一輪能再取(每取一次算一次投遞,用完進死信)。"""
        with self.store.transaction() as tx:
            done = self.store.release(tx, receipt, self.clock(), failure)
        return Processed(Result.DEFERRED) if done else Processed(Result.LEASE_LOST)

    def _take(  # noqa: PLR0911 - 每個出口對應鍵已存在分流的一格
        self, picked: PendingProposal, receipt: Receipt, signed: _Signed,
    ) -> AttemptRow | Processed:
        """開始一筆:在核對收據仍有效的同一個交易裡做;鍵已存在就依既有那把鍵的狀態分流。"""
        with self.store.transaction() as tx:
            now = self.clock()
            if not self.store.extend(tx, receipt, now):  # 已被取代、內容不同、或租約已不是我的
                return Processed(Result.LEASE_LOST)
            # 讀 DSP、讀設定檔簽發都可能剛好跨過到期時間:在開始一筆的同一個交易裡再判一次
            if picked.proposal.decision_expires_at <= now:
                self.store.ack_expired(tx, receipt, now)
                return Processed(Result.EXPIRED)
            try:
                begun = attempt_store.begin(tx, picked.proposal, now,
                                            capability_expires_at=signed.expires_at)
            except attempt_store.TooManyUnresolved:  # 正常觸發:等未結案數降下來
                self.store.release(tx, receipt, now, LastFailure.TABLE_FULL)
                return Processed(Result.DEFERRED)
            except attempt_store.CampaignLocked:  # 同廣告兩份提案被兩個工作者同時取出時會走到
                self.store.release(tx, receipt, now, None)
                return Processed(Result.DEFERRED)
            if begun.created:
                return begun.row
            state = begun.row.state
            if state is A.VERIFIED:
                if not self.store.ack_handed_off(tx, receipt, now):
                    raise LeaseLost(begun.row.key)
                return Processed(Result.HANDED_OFF_TO_EXISTING, begun.row.key)
            if state is A.FAILED:  # 人判過不做的操作不能貼成已交給執行
                code = block_code_for_failure(begun.row.code)
                if not self.store.ack_blocked(tx, receipt, now, code):
                    raise LeaseLost(begun.row.key)
                return Processed(Result.BLOCKED, begun.row.key, code)
            self.store.release(tx, receipt, now, None)  # 防線:未結案的鍵歸對帳管
            return Processed(Result.DEFERRED)

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
    def _write(  # noqa: PLR0913 - 關鍵字參數都是這次寫入要記的欄位,各有預設值
        self, row: AttemptRow, target: AttemptState, receipt: Receipt | None, *,
        code: OutcomeCode | None = None, written_version: int | None = None,
        capability_expires_at: datetime | None = None, block_code: BlockCode | None = None,
    ) -> AttemptRow:
        """嘗試寫入:同一個交易裡先核對收據並順手續租(對不上丟 LeaseLost),寫到終點就同時確認。

        續租就是 Phase 0 要的續期機制:活著的工作者每寫一筆就把租約往後推,慢的 DSP 呼叫不會
        讓它被當成當機;真的當機就不再續,租約到期後別人才能接手。
        receipt 是 None 只給 Phase 3 時代留下、收件表沒有處理中那一列的鍵用(沒有訊息可確認)。"""
        with self.store.transaction() as tx:
            now = self.clock()
            if receipt is not None and not self.store.extend(tx, receipt, now):
                raise LeaseLost(row.key)
            new = attempt_store.transition(
                tx, row.key, row.seq, target, now, code=code,
                written_version=written_version, capability_expires_at=capability_expires_at)
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

    def _after_expiry(self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None) -> bool:
        """DSP 明確沒寫:重讀 DSP、重跑檢查;通過就重讀時鐘重簽、同鍵重送一次。"""
        try:
            view = self.dsp.read_campaign(proposal.campaign_id)
        except DspUnavailable:
            return True  # 留在結果不明,交給對帳
        live = proposal.decision_expires_at > self.clock()
        checked = precheck(proposal, view)
        signed = (self._sign(proposal, row.key)  # 設定檔壞掉在這裡停機
                  if live and checked is None else None)
        if not isinstance(signed, _Signed):  # 業務上沒通過:DSP 明確沒寫這一次,不再送
            block = _version_changed_or_none(live, checked)
            if row.send_count > 1:  # 送過多次:更早的請求可能還在路上,先作廢才能判失敗
                return self._void_then_fail(proposal, row, receipt, block)
            self._write(row, A.FAILED, receipt, code=C.NOT_HAPPENED, block_code=block)
            return False
        try:
            row = self._write(row, A.IN_FLIGHT, receipt, capability_expires_at=signed.expires_at)
        except attempt_store.SendLimitReached:
            self._write(row, A.ESCALATED, receipt, code=C.SEND_LIMIT_REACHED)
            return False
        answer = self.dsp.write(proposal, row.key, signed.token)
        reaction = react(answer)
        if reaction.capability_expired:  # 用新讀的時間重簽後仍過期:時鐘或設定有問題
            self._write(row, A.ESCALATED, receipt, code=C.CAPABILITY_REJECTED)
            return False
        return self._record(proposal, row, answer, receipt)

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
        view = self.dsp.read_campaign(proposal.campaign_id)
        written = row.written_version
        if view is None or written is None:
            return False
        if view.version == written:
            return intent_holds(proposal, view)
        if view.version > written:  # 之後有人又改了:只證明我們的寫入套用過一次,內容也要對
            record = self.dsp.operation_record(row.key)
            return (record is not None and record.version_after == written
                    and record_matches(proposal, record))
        return False

    def _verification_timeout(self, row: AttemptRow, receipt: Receipt | None) -> None:
        try:
            with self.store.transaction() as tx:
                now = self.clock()
                if receipt is not None and not self.store.extend(tx, receipt, now):
                    raise LeaseLost(row.key)
                recorded = attempt_store.record_verification_timeout(tx, row.key, row.seq, now)
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
        """
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
            try:
                troubled = self._reconcile(key) or troubled
            except LeaseLost:
                continue  # 租約被接手:這把鍵這一輪放棄
            except CorruptedInboxRow as exc:  # 這把鍵找不到、同任務又有壞列:先記下,別拖累其他鍵
                corrupted.append(str(exc))
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
                moved = attempt_store.transition(tx, key, row.seq, A.UNKNOWN, now)
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
        return False if receipt is None else receipt

    def _reconcile_unknown(
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None,
    ) -> bool:
        try:
            record = self.dsp.operation_record(row.key)
        except DspUnavailable:
            self._verification_timeout(row, receipt)
            return True
        if record is not None:
            return self._found(proposal, row, record, receipt)
        return self._reconcile_not_found(proposal, row, receipt)

    def _reconcile_not_found(
        self, proposal: Proposal, row: AttemptRow, receipt: Receipt | None,
    ) -> bool:
        """查不到這把鍵:重讀廣告、重跑執行前檢查;通過就同鍵重送,不過就先作廢再判失敗。"""
        try:
            view = self.dsp.read_campaign(proposal.campaign_id)
        except DspUnavailable:
            self._verification_timeout(row, receipt)
            return True
        if view is None:  # 模擬 DSP 沒有建立或刪除廣告的介面:不存在就代表從來不存在
            self._write(row, A.FAILED, receipt, code=C.CAMPAIGN_NOT_FOUND)
            return False
        live = proposal.decision_expires_at > self.clock()
        checked = precheck(proposal, view)
        signed = (self._sign(proposal, row.key)  # 設定檔壞掉在這裡停機
                  if live and checked is None else None)
        if not isinstance(signed, _Signed):  # 業務上不過:先作廢,作廢成功才判失敗
            return self._void_then_fail(proposal, row, receipt,
                                        _version_changed_or_none(live, checked))
        try:
            row = self._write(row, A.IN_FLIGHT, receipt, capability_expires_at=signed.expires_at)
        except attempt_store.SendLimitReached:
            self._write(row, A.ESCALATED, receipt, code=C.SEND_LIMIT_REACHED)
            return False
        return self._record(proposal, row, self.dsp.write(proposal, row.key, signed.token),
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
        answer = self.dsp.void(proposal, row.key, token)
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
