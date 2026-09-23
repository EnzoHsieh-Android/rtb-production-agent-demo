"""提案:分析端產出的「想做什麼」,只是資料,沒有任何執行能力。

提案是不可信輸入(內容可能受廣告文字或模型輸出影響),所以解析採嚴格白名單:
不認得的欄位一律拒收(防止夾帶工具名稱、網址、憑證或政策覆寫),型別、大小與長度都有上限。
解析結果是「成功或錯誤清單」,不丟例外也不放行;所有問題一次列出,不只回報第一個。
量大小用迭代式、有深度與位元組上限的走訪,不遞迴、不先序列化整份輸入,超大或過深的輸入
會在超過上限的那一刻就被拒絕。
"""

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, TypeGuard

from rtb.domain._checks import ID_PATTERN, is_aware, is_plain_int

# 分析端目前的決策政策版本:分析端產生提案時用它,執行端判人工核可是否還算數也看它(Phase 6
# 增量 3)。兩邊從這裡匯入,只有一個來源,不靠人同步設定檔
POLICY_VERSION = "demo-pacing-v1"
POLICY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,64}")
REASON_PATTERN = re.compile(r"[a-z0-9_]{1,64}")
MAX_PAYLOAD_BYTES = 16 * 1024
MAX_DEPTH = 6
MAX_LIST_ITEMS = 20
MAX_RISK_SUMMARY = 500
MAX_INT = 2**63 - 1
MIN_TIME = datetime(2000, 1, 1, tzinfo=UTC)  # 時間欄位的合理範圍:換成 UTC 不會溢位,也不會離現實太遠
MAX_TIME = datetime(2100, 1, 1, tzinfo=UTC)
MAX_DECISION_LIFETIME = timedelta(hours=1)  # 決策從建立到到期最多一小時:壓低一份提案佔住名額的時間
CONTAINER_OVERHEAD = 8  # 每個容器與每個鍵值的粗估開銷(位元組),讓大量空容器也會被計入


class ActionType(StrEnum):
    UPDATE_BUDGET = "update_budget"
    PAUSE_CAMPAIGN = "pause_campaign"


@dataclass(frozen=True)
class Proposal:
    """自我驗證:直接建構也不能繞過解析的規則(欄位型別與內容都要合法)。"""

    task_id: str
    revision: int
    campaign_id: str
    action_type: ActionType
    requested_change: MappingProxyType[str, Any]
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    campaign_version_observed: int
    decision_created_at: datetime
    decision_expires_at: datetime
    policy_version: str
    risk_summary: str

    def __post_init__(self) -> None:
        # 刻意重用解析的規則(轉成 primitives 再檢查),不另寫第二份欄位驗證;
        # 先確認容器型別,免得 tuple 被悄悄轉成 list 而讓檢查失去嚴格度。
        shape_ok = (
            isinstance(self.action_type, ActionType)
            and isinstance(self.requested_change, MappingProxyType)
            and isinstance(self.reason_codes, tuple)
            and isinstance(self.evidence_refs, tuple)
        )
        try:
            raw = self.to_primitives() if shape_ok else None
        except (AttributeError, TypeError, ValueError):
            raw = None
        if raw is None or _field_errors(raw) or _expiry_errors(raw):
            raise ValueError("提案欄位不合法")

    def to_primitives(self) -> dict[str, Any]:
        """轉成只含字串、整數、串列與字典的形式,可以安全地存成 JSON 再解析回來。"""
        return {
            "task_id": self.task_id, "revision": self.revision, "campaign_id": self.campaign_id,
            "action_type": self.action_type.value, "requested_change": dict(self.requested_change),
            "reason_codes": list(self.reason_codes), "evidence_refs": list(self.evidence_refs),
            "campaign_version_observed": self.campaign_version_observed,
            "decision_created_at": self.decision_created_at.isoformat(),
            "decision_expires_at": self.decision_expires_at.isoformat(),
            "policy_version": self.policy_version, "risk_summary": self.risk_summary,
        }


@dataclass(frozen=True)
class ParsedProposal:
    """成功時 proposal 有值、errors 為空;失敗時 proposal 為 None、errors 列出所有問題。"""

    proposal: Proposal | None
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        if (self.proposal is None) == (len(self.errors) == 0):
            raise ValueError("解析結果必須是「有提案且沒有錯誤」或「沒有提案且有錯誤」")


def _matches(pattern: re.Pattern[str], value: object) -> TypeGuard[str]:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _positive_int(value: object, maximum: int = MAX_INT) -> bool:
    return is_plain_int(value) and 1 <= value <= maximum


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if not is_aware(parsed):
        return None
    try:
        in_utc = parsed.astimezone(UTC)  # 極端的年份加上時區偏移,換成 UTC 會溢位
    except OverflowError:
        return None
    return parsed if MIN_TIME <= in_utc <= MAX_TIME else None


def _require_time(value: object) -> datetime:
    """只在欄位已通過驗證之後才呼叫;萬一仍不合法,明確失敗而不是傳出 None。"""
    parsed = _parse_time(value)
    if parsed is None:
        raise ValueError("時間欄位不合法")
    return parsed


def _string_list(value: object, pattern: re.Pattern[str]) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= MAX_LIST_ITEMS
        and all(_matches(pattern, item) for item in value)
        and len(set(value)) == len(value)  # 不允許重複項
    )


def _valid_change(raw: dict[str, Any]) -> bool:
    change, action = raw.get("requested_change"), raw.get("action_type")
    if not isinstance(change, dict):
        return False
    if action == ActionType.UPDATE_BUDGET:
        return set(change) == {"new_budget"} and _positive_int(change["new_budget"])
    if action == ActionType.PAUSE_CAMPAIGN:
        return change == {}
    return False


def _valid_action(raw: dict[str, Any]) -> bool:
    value = raw["action_type"]
    return isinstance(value, str) and value in {a.value for a in ActionType}


def _valid_risk_summary(raw: dict[str, Any]) -> bool:
    text = raw["risk_summary"]
    # isprintable 擋掉控制字元、換行、孤立 surrogate 與雙向覆寫字元
    return isinstance(text, str) and len(text) <= MAX_RISK_SUMMARY and text.isprintable()


# 欄位 -> 檢查函式。_field_errors 只在欄位存在時才呼叫,所以檢查函式可以直接讀 raw[欄位]。
CHECKS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "task_id": lambda raw: _matches(ID_PATTERN, raw["task_id"]),
    "revision": lambda raw: _positive_int(raw["revision"], 1_000_000),
    "campaign_id": lambda raw: _matches(ID_PATTERN, raw["campaign_id"]),
    "action_type": _valid_action,
    "requested_change": _valid_change,
    "reason_codes": lambda raw: _string_list(raw["reason_codes"], REASON_PATTERN),
    "evidence_refs": lambda raw: _string_list(raw["evidence_refs"], ID_PATTERN),
    "campaign_version_observed": lambda raw: _positive_int(raw["campaign_version_observed"]),
    "decision_created_at": lambda raw: _parse_time(raw["decision_created_at"]) is not None,
    "decision_expires_at": lambda raw: _parse_time(raw["decision_expires_at"]) is not None,
    "policy_version": lambda raw: _matches(POLICY_PATTERN, raw["policy_version"]),
    "risk_summary": _valid_risk_summary,
}


def _leaf_bytes(value: object) -> int | None:
    """葉節點的粗估位元組數;不是 JSON 能表示的值就回 None。"""
    if value is None or isinstance(value, bool):
        return 5
    if isinstance(value, str):
        return len(value.encode("utf-8", "surrogatepass")) + 2
    if isinstance(value, int):
        return value.bit_length() // 3 + 2  # 位數的粗估,避免對超大整數呼叫 str()
    if isinstance(value, float) and math.isfinite(value):
        return 24
    return None


def _size_error(raw: object) -> str | None:
    """不遞迴地走訪整份輸入,超過大小或深度上限的那一刻就回報,不必走完。"""
    stack, total = [(raw, 1)], 0
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            return "too_deep"
        if isinstance(node, dict) and not all(isinstance(key, str) for key in node):
            return "not_json_serializable"  # JSON 的鍵一定是字串
        children = _children(node)
        if children is None:
            size = _leaf_bytes(node)
            if size is None:
                return "not_json_serializable"
            total += size
        else:
            total += CONTAINER_OVERHEAD + CONTAINER_OVERHEAD * len(children)
            stack.extend((child, depth + 1) for child in children)
        if total > MAX_PAYLOAD_BYTES:
            return f"too_large:{total}"
    return None


def _children(node: object) -> list[Any] | None:
    """容器回傳它的子節點(字典的鍵也算),葉節點回傳 None。字典的鍵必須先確認是字串。"""
    if isinstance(node, dict):
        return [part for key, value in node.items() for part in (key, value)]
    if isinstance(node, list):
        return list(node)
    return None


def _is_string_keyed_dict(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _shape_errors(raw: dict[str, Any]) -> list[str]:
    problem = _size_error(raw)
    return [problem] if problem else []


def _field_errors(raw: dict[str, Any]) -> list[str]:
    errors = [f"unknown_field:{str(key)[:64]}" for key in raw if key not in CHECKS]
    for field, check in CHECKS.items():
        if field not in raw:
            errors.append(f"{field}:missing")
        elif not check(raw):
            errors.append(f"{field}:invalid")
    return errors


def _expiry_errors(raw: dict[str, Any]) -> list[str]:
    created = _parse_time(raw["decision_created_at"])
    expires = _parse_time(raw["decision_expires_at"])
    if created is None or expires is None:
        return []
    if expires <= created:
        return ["decision_expires_at:not_after_creation"]
    if expires - created > MAX_DECISION_LIFETIME:
        return ["decision_expires_at:lifetime_too_long"]
    return []


def _build(raw: dict[str, Any]) -> Proposal:
    return Proposal(
        task_id=raw["task_id"], revision=raw["revision"], campaign_id=raw["campaign_id"],
        action_type=ActionType(raw["action_type"]),
        requested_change=MappingProxyType(dict(raw["requested_change"])),
        reason_codes=tuple(raw["reason_codes"]), evidence_refs=tuple(raw["evidence_refs"]),
        campaign_version_observed=raw["campaign_version_observed"],
        decision_created_at=_require_time(raw["decision_created_at"]),
        decision_expires_at=_require_time(raw["decision_expires_at"]),
        policy_version=raw["policy_version"], risk_summary=raw["risk_summary"],
    )


def parse_proposal(raw: object) -> ParsedProposal:
    """把不可信的原始資料解析成提案;絕不丟例外、絕不修改輸入。

    真正的不可信輸入來自 JSON 解析,走不到自訂物件;但這是安全邊界,所以再加一層保底:
    萬一有東西(例如刻意會丟例外的子類別)讓解析出錯,也只回一個可辨認的錯誤,不讓例外逃出去。
    """
    try:
        return _parse(raw)
    except Exception as exc:  # 安全邊界的最後一道保底,錯誤型別記在結果裡
        return ParsedProposal(None, (f"unexpected_failure:{type(exc).__name__}",))


def _parse(raw: object) -> ParsedProposal:
    if not _is_string_keyed_dict(raw):
        return ParsedProposal(None, ("not_an_object",))
    errors = _shape_errors(raw)
    if errors:
        return ParsedProposal(None, tuple(errors))
    errors = _field_errors(raw)
    if not errors:
        errors = _expiry_errors(raw)
    if errors:
        return ParsedProposal(None, tuple(errors))
    return ParsedProposal(_build(raw), ())


def content_hash(proposal: Proposal) -> str:
    """提案內容的 SHA-256:同一份提案的等價寫法得到同一個雜湊。

    先轉成基本型別,時間一律換成 UTC(不同時區寫法的同一時刻得到同一個雜湊),再以鍵排序、
    無多餘空白、不允許 NaN 的 JSON 序列化。串列順序有意義:順序不同就是不同的提案。
    """
    primitives = proposal.to_primitives()
    for field in ("decision_created_at", "decision_expires_at"):
        moment = getattr(proposal, field).astimezone(UTC)
        primitives[field] = moment.isoformat()
    encoded = json.dumps(primitives, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()
