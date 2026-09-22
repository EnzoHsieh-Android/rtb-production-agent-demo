"""模擬 DSP 這一側的寫入能力憑證:聲明欄位集合、時間窗與範圍檢查。

DSP 是外部系統的模擬器,刻意不依賴領域層,欄位定義自己一份;動作名稱用路由表既有的
update_budget、pause_campaign。執行行程那一份由跨行程整合測試綁住。
驗證失敗一律丟型別化的憑證錯誤,由錯誤對照表轉成固定代碼,不回顯聲明內容。
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeGuard

from rtb.capabilitykit import TokenRejected, decode, is_usable_key
from rtb.dsp.errors import (
    CapabilityExpired,
    CapabilityInvalid,
    CapabilityMissing,
    CapabilityNotConfigured,
    CapabilityScopeMismatch,
)
from rtb.dsp.store import is_plain_int

FORMAT_VERSION = "c1"
CLAIM_FIELDS = ("v", "tenant", "campaign_id", "action", "new_budget", "expected_version",
                "idempotency_key", "policy_version", "iat", "exp")
SCOPE_FIELDS = ("campaign_id", "action", "idempotency_key", "tenant", "new_budget",
                "expected_version")
ACTIONS = ("update_budget", "pause_campaign")
# 每個寫入端點只收這些本文欄位;多出的一律拒收(租戶不在裡面:沒有端點能改廣告的租戶)
BODY_FIELDS = {
    "update_budget": frozenset({"new_budget", "expected_version"}),
    "pause_campaign": frozenset({"expected_version"}),
}
MAX_LIFETIME_SECONDS = 300
MAX_SKEW_SECONDS = 30
# 政策版本只記不驗,但 DSP 是最後一道防線:格式自己再擋一次,不讓稽核欄位被塞任意內容
POLICY_VERSION_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}")


@dataclass(frozen=True)
class Claims:
    tenant: str
    campaign_id: str
    action: str
    new_budget: int | None
    expected_version: int
    idempotency_key: str
    policy_version: str  # DSP 不懂政策:只記不驗
    iat: int
    exp: int


@dataclass(frozen=True)
class WriteRequest:
    campaign_id: str
    action: str
    idempotency_key: str
    new_budget: object
    expected_version: object


def _nonempty_str(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _parse_claims(raw: Mapping[str, object]) -> Claims:
    if set(raw) != set(CLAIM_FIELDS) or raw["v"] != FORMAT_VERSION:
        raise CapabilityInvalid("聲明欄位集合或格式版本不對")
    action, budget = raw["action"], raw["new_budget"]
    budget_ok = (is_plain_int(budget) and budget > 0) if action == "update_budget" else (
        budget is None)
    texts = (raw["tenant"], raw["campaign_id"], raw["idempotency_key"], raw["policy_version"])
    numbers = (raw["expected_version"], raw["iat"], raw["exp"])
    if (action not in ACTIONS or not budget_ok or not all(_nonempty_str(t) for t in texts)
            or not all(is_plain_int(n) for n in numbers)
            or not POLICY_VERSION_PATTERN.fullmatch(str(raw["policy_version"]))):
        raise CapabilityInvalid("聲明欄位型別不對")
    return Claims(
        tenant=str(raw["tenant"]), campaign_id=str(raw["campaign_id"]), action=str(action),
        new_budget=budget if is_plain_int(budget) else None,
        expected_version=int(str(raw["expected_version"])),
        idempotency_key=str(raw["idempotency_key"]), policy_version=str(raw["policy_version"]),
        iat=int(str(raw["iat"])), exp=int(str(raw["exp"])),
    )


def _check_time(claims: Claims, now: int) -> None:
    lifetime = claims.exp - claims.iat
    if (lifetime <= 0 or lifetime > MAX_LIFETIME_SECONDS
            or claims.iat > now + MAX_SKEW_SECONDS or claims.exp <= now):
        raise CapabilityExpired("憑證不在有效時間窗內")


def verified_claims(
    read_token: Callable[[], str | None], key: bytes | None, clock: Callable[[], float],
) -> Claims:
    """金鑰已設定 → 標頭存在 → 格式與簽章 → 聲明 → 時間;範圍要等讀完本文再比。

    標頭與時鐘都用函式傳進來、在金鑰檢查之後才讀:讀標頭本身也會拒收(例如標頭重複),
    時鐘也可能出錯,先讀就會蓋掉「DSP 沒有可用金鑰」這個更嚴重的狀況。
    """
    if not is_usable_key(key):
        raise CapabilityNotConfigured("DSP 沒有可用的金鑰")
    assert key is not None  # is_usable_key 已確認  # noqa: S101
    token = read_token()
    if token is None:
        raise CapabilityMissing("寫入沒有帶憑證")
    try:
        raw = decode(token, key)
    except TokenRejected as exc:
        raise CapabilityInvalid("憑證格式或簽章不對") from exc
    claims = _parse_claims(raw)
    _check_time(claims, int(clock()))
    return claims


def check_body_fields(action: str, body: dict[str, object]) -> set[str]:
    """回傳本文裡端點白名單以外的欄位。"""
    return set(body) - BODY_FIELDS[action]


def check_scope(claims: Claims, request: WriteRequest, tenant_of_campaign: str | None) -> None:
    """六項範圍都要等於聲明;廣告不存在(查不到租戶)也算範圍不符。"""
    matches = (
        claims.campaign_id == request.campaign_id,
        claims.action == request.action,
        claims.idempotency_key == request.idempotency_key,
        tenant_of_campaign is not None and claims.tenant == tenant_of_campaign,
        claims.new_budget == request.new_budget
        and (request.new_budget is None or is_plain_int(request.new_budget)),
        is_plain_int(request.expected_version)
        and claims.expected_version == request.expected_version,
    )
    if not all(matches):
        raise CapabilityScopeMismatch("請求與憑證的範圍不符")
