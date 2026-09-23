"""人工核可憑證(Phase 6 增量 3):簽、驗章、範圍指紋、有效判斷。

被總曝險已滿或比例過大擋下的提案停在待核可;人用管理工具簽一張核可,執行迴圈驗過才放行那一關。
格式機制跟寫入能力憑證共用(同一個簽章與拆解模組),金鑰是另一把。

範圍指紋在這裡組,執行迴圈與管理工具都呼叫這一支,不讓兩邊各算一套:租戶名稱與它那一段設定、
比例上限的常數、目前政策版本,任何一樣改了舊核可就不算數。這支模組不匯入執行迴圈模組:只用
護欄常數、提案領域、簽發器的租戶型別、共用的簽章機制與欄位檢查,以及收件口模組的擋下原因
列舉(關卡就是那兩個擋下原因),管理工具不必拖進整個執行迴圈。

照「防忘記不防繞過」:核可是對稱簽章,執行端也讀得到核可金鑰,擋不住有權限的人自己簽(計劃
實務隱患有記)。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from rtb.capabilitykit import ClaimValue, TokenRejected, decode, encode
from rtb.domain._checks import is_id, is_plain_int
from rtb.domain.proposal import MAX_INT, POLICY_VERSION, Proposal, content_hash
from rtb.executor import guardrails
from rtb.executor.capability_signer import Tenant
from rtb.executor.inbox_store import APPROVABLE, BlockCode

FORMAT_VERSION = "a1"
ID_LENGTH = 16  # 核可編號:整張核可(含簽章)雜湊的前這麼多個十六進位字元


class ApprovalRefused(Exception):
    """管理工具不簽:reason 是固定代碼。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Approval:
    """驗過章的一張核可。時間都是秒(跟能力憑證的聲明同一個單位)。"""

    approval_id: str
    task_id: str
    revision: int
    content_hash: str
    stage: BlockCode  # 核准的是哪一關:總曝險已滿或比例過大
    fingerprint: str
    approver: str
    max_increase: int  # 允許的加預算金額上限
    issued_at: int
    expires_at: int


def scope_fingerprint(tenant: Tenant) -> str:
    """範圍指紋:租戶名稱與三欄設定、比例上限常數、目前政策版本的雜湊。"""
    material = {
        "tenant": tenant.name, "campaigns": sorted(tenant.campaigns),
        "max_budget": tenant.max_budget, "aggregate_limit": tenant.aggregate_limit,
        "ratio": [guardrails.MAX_INCREASE_NUMERATOR, guardrails.MAX_INCREASE_DENOMINATOR,
                  guardrails.MIN_INCREASE_STEP],
        "policy_version": POLICY_VERSION,
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def approval_id(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()[:ID_LENGTH]


def issue(  # noqa: PLR0913 - 每個參數都是核可要綁的一個欄位
    key: bytes, proposal: Proposal, stage: BlockCode, tenant: Tenant, *, approver: str,
    max_increase: int, issued_at: int, expires_at: int,
) -> str:
    """簽一張核可。到期不能晚於提案本身的到期;只准兩種可核可的關卡。"""
    if stage not in APPROVABLE:
        raise ApprovalRefused("stage_not_approvable")
    if not is_id(approver):
        raise ApprovalRefused("approver_invalid")
    if not is_plain_int(max_increase) or not 0 <= max_increase <= MAX_INT:
        raise ApprovalRefused("max_increase_invalid")
    if not issued_at < expires_at <= int(proposal.decision_expires_at.timestamp()):
        raise ApprovalRefused("expiry_invalid")
    claims: dict[str, ClaimValue] = {
        "v": FORMAT_VERSION, "task_id": proposal.task_id, "revision": proposal.revision,
        "content_hash": content_hash(proposal), "stage": stage.value,
        "fingerprint": scope_fingerprint(tenant), "approver": approver,
        "max_increase": max_increase, "iat": issued_at, "exp": expires_at,
    }
    return encode(claims, key)


def read(token: str, key: bytes | None) -> Approval | None:
    """驗章並拆開;沒有核可金鑰、簽章不對或欄位不對一律回 None(不算數)。"""
    if key is None:
        return None
    try:
        claims = decode(token, key)
    except TokenRejected:
        return None
    ints = ("revision", "max_increase", "iat", "exp")
    strings = ("task_id", "content_hash", "stage", "fingerprint", "approver")
    if (claims.get("v") != FORMAT_VERSION
            or not all(is_plain_int(claims.get(name)) for name in ints)
            or not all(isinstance(claims.get(name), str) for name in strings)
            or claims["stage"] not in {code.value for code in APPROVABLE}):
        return None
    return Approval(
        approval_id(token), str(claims["task_id"]), int(claims["revision"]),  # type: ignore[arg-type]
        str(claims["content_hash"]), BlockCode(claims["stage"]), str(claims["fingerprint"]),
        str(claims["approver"]), int(claims["max_increase"]),  # type: ignore[arg-type]
        int(claims["iat"]), int(claims["exp"]))  # type: ignore[arg-type]


def holds(
    approval: Approval, proposal: Proposal, stage: BlockCode, tenant: Tenant, amount: int,
    now: datetime,
) -> bool:
    """這張核可此刻對這份提案、這一關、這筆金額算不算數。"""
    return (approval.task_id == proposal.task_id and approval.revision == proposal.revision
            and approval.content_hash == content_hash(proposal)
            and approval.stage is stage
            and approval.fingerprint == scope_fingerprint(tenant)
            and proposal.policy_version == POLICY_VERSION
            and now.timestamp() < approval.expires_at
            and amount <= approval.max_increase)
