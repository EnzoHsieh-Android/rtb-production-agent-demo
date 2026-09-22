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
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from rtb.domain._checks import is_plain_int
from rtb.domain.attempt import AttemptState, OutcomeCode, operation_key
from rtb.domain.proposal import ActionType, Proposal
from rtb.executor import attempt_store
from rtb.executor.attempt_store import AttemptRow
from rtb.executor.capability_signer import LIFETIME_SECONDS, SigningRefused
from rtb.executor.inbox_store import BlockCode, InboxStore, PendingProposal

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


@dataclass(frozen=True)
class Executor:
    store: InboxStore
    dsp: DspPort
    signer: Signer
    config_path: Path
    clock: Callable[[], datetime]  # 單一動作的簡單回呼:照專案慣例用 Callable,不另開 Protocol

    def process_one(self) -> Processed:  # noqa: PLR0911 - 每個出口對應設計的一步
        picked = self._pick()
        if picked is None:
            return Processed(Result.IDLE)
        proposal = picked.proposal
        if proposal.decision_expires_at <= self.clock():  # 過期先判,不必去讀 DSP
            return self._dispose(picked, None, Result.EXPIRED)
        try:
            view = self.dsp.read_campaign(proposal.campaign_id)
        except DspUnavailable:
            return Processed(Result.DEFERRED)
        if proposal.decision_expires_at <= self.clock():  # 讀 DSP 期間過期:先於其他檢查
            return self._dispose(picked, None, Result.EXPIRED)
        signed = precheck(proposal, view) or self._sign(proposal)
        if isinstance(signed, BlockCode):
            return self._dispose(picked, signed, Result.BLOCKED)
        taken = self._take(picked, signed)
        if isinstance(taken, Processed):
            return taken
        self._record(proposal, taken, self.dsp.write(proposal, taken.key, signed.token))
        return Processed(Result.EXECUTED, taken.key)

    # ---- 第 1~4 步 ----
    def _pick(self) -> PendingProposal | None:
        """依收到時間由舊到新,跳過「同一個廣告已有未結案嘗試」的,挑第一份(只讀)。"""
        with self.store.transaction() as tx:
            for item in self.store.pending(tx):
                if not attempt_store.unresolved_count(tx, item.proposal.campaign_id):
                    return item
        return None

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

    def _dispose(self, picked: PendingProposal, code: BlockCode | None, kind: Result) -> Processed:
        """標已過期或已擋下;條件是仍待處理且內容雜湊沒變,被取代了就什麼都不改。"""
        args = (picked.task_id, picked.revision, picked.content_hash)
        with self.store.transaction() as tx:
            done = (self.store.mark_expired(tx, *args) if code is None
                    else self.store.block(tx, *args, code))
        return Processed(kind, block_code=code) if done else Processed(Result.DEFERRED)

    def _take(self, picked: PendingProposal, signed: _Signed) -> AttemptRow | Processed:
        """取件與開始一筆在同一個交易裡;鍵已存在就依既有那把鍵的狀態分流。"""
        args = (picked.task_id, picked.revision, picked.content_hash)
        with self.store.transaction() as tx:
            if not self.store.is_pending(tx, *args):  # 已被取代或內容不同
                return Processed(Result.DEFERRED)
            now = self.clock()
            # 讀 DSP、讀設定檔簽發都可能剛好跨過到期時間:在開始一筆的同一個交易裡再判一次
            if picked.proposal.decision_expires_at <= now:
                self.store.mark_expired(tx, *args)
                return Processed(Result.EXPIRED)
            try:
                begun = attempt_store.begin(tx, picked.proposal, now,
                                            capability_expires_at=signed.expires_at)
            except (attempt_store.TooManyUnresolved, attempt_store.CampaignLocked):
                return Processed(Result.DEFERRED)  # 全表已滿是正常觸發;同廣告鎖住是防線
            if begun.created or begun.row.state is not A.FAILED:
                self.store.hand_off(tx, *args)
                return begun.row if begun.created else Processed(
                    Result.HANDED_OFF_TO_EXISTING, begun.row.key)
            code = BlockCode.OPERATION_PREVIOUSLY_FAILED  # 人判過不做的操作不能貼成已交給執行
            self.store.block(tx, *args, code)
            return Processed(Result.BLOCKED, begun.row.key, code)

    # ---- 第 6 步:寫結果 ----
    def _write(
        self, row: AttemptRow, target: AttemptState, *, code: OutcomeCode | None = None,
        written_version: int | None = None, capability_expires_at: datetime | None = None,
    ) -> AttemptRow:
        with self.store.transaction() as tx:
            new = attempt_store.transition(
                tx, row.key, row.seq, target, self.clock(), code=code,
                written_version=written_version, capability_expires_at=capability_expires_at)
        if new is None:  # 這把鍵被別的東西改過:單一執行者下不該發生
            raise ExecutorHalted("no_progress")
        return new

    def _record(self, proposal: Proposal, row: AttemptRow, answer: WriteAnswer) -> bool:
        """寫結果整段(處理一筆與對帳重送共用);回傳這段有沒有 DSP 呼叫沒拿到結論。"""
        reaction = react(answer)
        if reaction.target is A.COMMITTED_UNVERIFIED:
            return self._verify(proposal, self._write(row, reaction.target,
                                                      written_version=answer.version_after))
        if reaction.capability_expired:
            return self._after_expiry(proposal, self._write(row, A.UNKNOWN))
        self._write(row, reaction.target, code=reaction.code)
        if reaction.halt:
            raise ExecutorHalted(str(reaction.code))
        return reaction.target is A.UNKNOWN

    def _after_expiry(self, proposal: Proposal, row: AttemptRow) -> bool:
        """DSP 明確沒寫:重讀 DSP、重跑檢查;通過就重讀時鐘重簽、同鍵重送一次。"""
        try:
            view = self.dsp.read_campaign(proposal.campaign_id)
        except DspUnavailable:
            return True  # 留在結果不明,交給對帳
        passed = (proposal.decision_expires_at > self.clock()
                  and precheck(proposal, view) is None)
        signed = self._sign(proposal, row.key) if passed else None  # 設定檔壞掉在這裡停機
        if not isinstance(signed, _Signed):  # 業務上沒通過:DSP 明確沒寫這一次,不再送
            if row.send_count > 1:  # 送過多次:更早的請求可能還在路上,先作廢才能判失敗
                return self._void_then_fail(proposal, row)
            self._write(row, A.FAILED, code=C.NOT_HAPPENED)
            return False
        try:
            row = self._write(row, A.IN_FLIGHT, capability_expires_at=signed.expires_at)
        except attempt_store.SendLimitReached:
            self._write(row, A.ESCALATED, code=C.SEND_LIMIT_REACHED)
            return False
        answer = self.dsp.write(proposal, row.key, signed.token)
        reaction = react(answer)
        if reaction.capability_expired:  # 用新讀的時間重簽後仍過期:時鐘或設定有問題
            self._write(row, A.ESCALATED, code=C.CAPABILITY_REJECTED)
            return False
        return self._record(proposal, row, answer)

    # ---- 第 7 步:執行後驗證 ----
    def _verify(self, proposal: Proposal, row: AttemptRow) -> bool:
        """回傳有沒有 DSP 呼叫失敗(讀取失敗記一次查證逾時)。"""
        try:
            verified = self._check_applied(proposal, row)
        except DspUnavailable:
            self._verification_timeout(row)
            return True
        if verified:
            self._write(row, A.VERIFIED)
        else:
            self._write(row, A.ESCALATED, code=C.VERIFICATION_MISMATCH)
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

    def _verification_timeout(self, row: AttemptRow) -> None:
        try:
            with self.store.transaction() as tx:
                recorded = attempt_store.record_verification_timeout(
                    tx, row.key, row.seq, self.clock())
        except attempt_store.VerificationTimeoutLimitReached:
            self._write(row, A.ESCALATED, code=C.VERIFICATION_TIMEOUTS_EXHAUSTED)
            return
        if recorded is None:
            raise ExecutorHalted("no_progress")

    # ---- 對帳(增量 4) ----
    def reconcile_all(self) -> bool:
        """結果不明與已提交待驗證的嘗試(轉人工不碰)各對帳一次,依最新一列寫入時間由舊到新。

        回傳這一輪有沒有 DSP 呼叫失敗:有就讓啟動程式這輪結束後休息,DSP 變慢時不連續全速打它。
        """
        with self.store.transaction() as tx:
            keys = attempt_store.awaiting_reconciliation(tx)
        troubled = False
        for key in keys:
            troubled = self._reconcile(key) or troubled
        return troubled

    def _reconcile(self, key: str) -> bool:
        with self.store.transaction() as tx:
            try:
                row = attempt_store.latest(tx, key)
                proposal = attempt_store.snapshot(tx, key)
            except attempt_store.CorruptedAttemptRow as exc:  # 讀不回來:不猜,停下讓人看
                raise ExecutorHalted("unreadable_attempt") from exc
        assert row is not None  # noqa: S101 - 清單來自同一張表
        if row.state is A.COMMITTED_UNVERIFIED:
            return self._verify(proposal, row)
        return self._reconcile_unknown(proposal, row)

    def _reconcile_unknown(self, proposal: Proposal, row: AttemptRow) -> bool:
        try:
            record = self.dsp.operation_record(row.key)
        except DspUnavailable:
            self._verification_timeout(row)
            return True
        if record is not None:
            return self._found(proposal, row, record)
        return self._reconcile_not_found(proposal, row)

    def _reconcile_not_found(self, proposal: Proposal, row: AttemptRow) -> bool:
        """查不到這把鍵:重讀廣告、重跑執行前檢查;通過就同鍵重送,不過就先作廢再判失敗。"""
        try:
            view = self.dsp.read_campaign(proposal.campaign_id)
        except DspUnavailable:
            self._verification_timeout(row)
            return True
        if view is None:  # 模擬 DSP 沒有建立或刪除廣告的介面:不存在就代表從來不存在
            self._write(row, A.FAILED, code=C.CAMPAIGN_NOT_FOUND)
            return False
        blocked = (proposal.decision_expires_at <= self.clock()
                   or precheck(proposal, view) is not None)
        signed = None if blocked else self._sign(proposal, row.key)  # 設定檔壞掉在這裡停機
        if not isinstance(signed, _Signed):  # 業務上不過:先作廢,作廢成功才判失敗
            return self._void_then_fail(proposal, row)
        try:
            row = self._write(row, A.IN_FLIGHT, capability_expires_at=signed.expires_at)
        except attempt_store.SendLimitReached:
            self._write(row, A.ESCALATED, code=C.SEND_LIMIT_REACHED)
            return False
        return self._record(proposal, row, self.dsp.write(proposal, row.key, signed.token))

    def _found(self, proposal: Proposal, row: AttemptRow, record: OperationRecord) -> bool:
        if not record_matches(proposal, record):
            self._write(row, A.ESCALATED, code=C.IDEMPOTENCY_CONFLICT)
            return False
        row = self._write(row, A.COMMITTED_UNVERIFIED, written_version=record.version_after)
        return self._verify(proposal, row)

    def _void_then_fail(self, proposal: Proposal, row: AttemptRow) -> bool:
        now = int(self.clock().timestamp())
        try:
            token = self.signer.sign_void(proposal, row.key, self.config_path, now)
        except SigningRefused as refused:
            if refused.reason != "campaign_not_allowed":  # 設定檔壞掉或不安全:系統故障
                raise ExecutorHalted(refused.reason) from refused
            # 不能作廢,就證明不了舊請求不會晚到提交
            self._write(row, A.ESCALATED, code=C.CANNOT_PROVE_NOT_HAPPENED)
            return False
        answer = self.dsp.void(proposal, row.key, token)
        reaction = react_void(answer)
        if reaction.outcome is VoidOutcome.FOUND:
            assert answer.record is not None  # noqa: S101 - 這一列的條件保證
            return self._found(proposal, row, answer.record)
        if reaction.outcome is VoidOutcome.TIMEOUT:
            self._verification_timeout(row)
            return True
        target = A.FAILED if reaction.outcome is VoidOutcome.FAILED else A.ESCALATED
        self._write(row, target, code=reaction.code)
        if reaction.halt:
            raise ExecutorHalted(str(reaction.code))
        return False
