"""外部寫入的嘗試(執行行程這一側):冪等鍵、嘗試狀態機、結果代碼與細節文字的規則。

一個「邏輯操作」永遠只有一把鍵,由提案的實質內容決定性算出;鍵一旦存進嘗試紀錄,
之後一律用存下的那把,不從提案重算(算法改版時舊鍵照舊能對帳)。
一般轉換走一張唯讀表;「記一次查證逾時」與「人工處置」是兩種專用操作,不在表裡,
這樣呼叫端不經專用操作就離不開轉人工,也改不了逾時次數。
"""

import hashlib
import json
from enum import StrEnum
from types import MappingProxyType

from rtb.domain.proposal import Proposal

KEY_PREFIX = "k1-"  # 算法版本:之後改算法換前綴,新舊鍵不會撞在一起
MAX_DETAIL = 500


def operation_key(proposal: Proposal) -> str:
    """任務、廣告、動作、要求的變更、觀察到的版本五樣決定鍵;修訂序號與決策時間不算。

    正規化沿用提案內容雜湊的做法(固定鍵序、無多餘空白、不允許 NaN)。提案進領域層時已驗過
    形狀:編號只准 ASCII,要求的變更只有正整數預算或空白,所以不會有同一件事的不同寫法。
    """
    identity = {
        "task_id": proposal.task_id, "campaign_id": proposal.campaign_id,
        "action_type": proposal.action_type.value,
        "requested_change": dict(proposal.requested_change),
        "campaign_version_observed": proposal.campaign_version_observed,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    return KEY_PREFIX + hashlib.sha256(encoded.encode("ascii")).hexdigest()


class IllegalAttemptTransition(ValueError):
    """不合法的嘗試轉換或操作:這是程式錯誤,不是預期中的資料狀態。

    比照任務狀態機的 IllegalTransition:例外定義在領域層,儲存層直接拿來丟。
    """


class AttemptState(StrEnum):
    IN_FLIGHT = "in_flight"  # 已落地、準備或正在呼叫 DSP
    UNKNOWN = "unknown"  # 逾時、斷線或無法判定:不知道 DSP 有沒有提交
    COMMITTED_UNVERIFIED = "committed_unverified"  # DSP 確認提交,還沒比對實際狀態
    VERIFIED = "verified"
    FAILED = "failed"
    ESCALATED = "escalated"  # 轉人工:不是終點,一直鎖住廣告直到人工處置


A = AttemptState

_GENERAL: dict[AttemptState, frozenset[AttemptState]] = {
    A.IN_FLIGHT: frozenset({A.COMMITTED_UNVERIFIED, A.FAILED, A.ESCALATED, A.UNKNOWN}),
    A.UNKNOWN: frozenset({A.COMMITTED_UNVERIFIED, A.IN_FLIGHT, A.FAILED, A.ESCALATED}),
    A.COMMITTED_UNVERIFIED: frozenset({A.VERIFIED, A.ESCALATED}),
    A.VERIFIED: frozenset(),
    A.FAILED: frozenset(),
    A.ESCALATED: frozenset(),  # 只能經由人工處置離開,不走一般轉換
}

if set(_GENERAL) != set(AttemptState):  # 新增狀態卻忘了寫進表:匯入時就失敗
    raise RuntimeError("嘗試轉換表沒有涵蓋所有狀態")

# 唯讀,而且是複本:外部改建表用的字典也影響不到它
TRANSITIONS: MappingProxyType[AttemptState, frozenset[AttemptState]] = MappingProxyType(
    dict(_GENERAL))
TERMINAL_STATES = frozenset({A.VERIFIED, A.FAILED})
UNRESOLVED_STATES = frozenset(AttemptState) - TERMINAL_STATES  # 都會鎖住同一個廣告
TIMEOUT_STATES = frozenset({A.UNKNOWN, A.COMMITTED_UNVERIFIED})  # 可以記查證逾時的狀態
RESOLUTION_OUTCOMES = frozenset({A.VERIFIED, A.FAILED})  # 人工處置只能給這兩種結果


class OutcomeCode(StrEnum):
    # 失敗類
    VERSION_CONFLICT = "version_conflict"
    VALIDATION_REJECTED = "validation_rejected"
    CAMPAIGN_NOT_FOUND = "campaign_not_found"
    OTHER_REJECTION = "other_rejection"
    NOT_HAPPENED = "not_happened"  # 查不到且版本已前進:舊請求不可能再提交
    MANUAL_FAILURE = "manual_failure"  # 只有人工處置能用
    # 轉人工類
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    VERIFICATION_TIMEOUTS_EXHAUSTED = "verification_timeouts_exhausted"
    SEND_LIMIT_REACHED = "send_limit_reached"
    VERIFICATION_MISMATCH = "verification_mismatch"
    CANNOT_PROVE_NOT_HAPPENED = "cannot_prove_not_happened"
    CAPABILITY_REJECTED = "capability_rejected"  # 憑證被 DSP 拒收:本地簽發或設定出錯,要人看


C = OutcomeCode
FAILURE_CODES = frozenset({
    C.VERSION_CONFLICT, C.VALIDATION_REJECTED, C.CAMPAIGN_NOT_FOUND, C.OTHER_REJECTION,
    C.NOT_HAPPENED, C.MANUAL_FAILURE,
})
ESCALATION_CODES = frozenset(OutcomeCode) - FAILURE_CODES

if C.IDEMPOTENCY_CONFLICT not in ESCALATION_CODES:  # 同鍵不同內容一律轉人工
    raise RuntimeError("冪等衝突必須是轉人工類")


def _as_state(value: object) -> AttemptState | None:
    if not isinstance(value, str):
        return None
    try:
        return AttemptState(value)
    except ValueError:
        return None


def _as_code(value: object) -> OutcomeCode | None:
    if not isinstance(value, str):
        return None
    try:
        return OutcomeCode(value)
    except ValueError:
        return None


def can_transition(current: object, target: object) -> bool:
    origin, destination = _as_state(current), _as_state(target)
    return origin is not None and destination is not None and destination in TRANSITIONS[origin]


def code_fits(target: object, code: object, *, by_resolution: bool) -> bool:
    """結果代碼的類別要配目標狀態:失敗類只配失敗、轉人工類只配轉人工,其餘狀態不帶代碼。

    「人工判定失敗」只給人工處置用;一般轉換帶它、或人工處置判失敗卻不帶它,都不合。
    """
    destination = _as_state(target)
    if destination is None:
        return False
    if code is None:
        return destination not in (A.FAILED, A.ESCALATED)
    known = _as_code(code)
    if known is None:
        return False
    if destination is A.FAILED:
        return known in FAILURE_CODES and (known is C.MANUAL_FAILURE) == by_resolution
    if destination is A.ESCALATED:
        return known in ESCALATION_CODES and not by_resolution
    return False


def is_clean_detail(text: object) -> bool:
    """給人看的細節文字:只收 ASCII 可列印字元、有長度上限。

    內容來自 DSP 回應與例外訊息,是不可信的;限定 ASCII 擋掉全形冒號、同形字這類能偽裝成
    系統欄位的文字。這段文字不得被任何判斷邏輯讀取。
    """
    return (isinstance(text, str) and len(text) <= MAX_DETAIL
            and text.isascii() and text.isprintable())
