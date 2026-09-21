"""證據與新鮮度:證據是「事實在某個時間點的快照」,不是永遠成立的事實。

新鮮度由純程式判斷,先看年齡,再看版本:過期或版本已變就要重讀。
重讀是安全的,重做副作用才危險;執行的那一刻執行行程仍要重讀現況並重新授權,
新鮮度只是第一道,不是最後一道。
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rtb.domain._checks import is_id, is_plain_int, is_plain_number

HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_SOURCE_LENGTH = 64


class EvidenceKind(StrEnum):
    CAMPAIGN_STATE = "campaign_state"
    METRICS = "metrics"


class TrustClass(StrEnum):
    TRUSTED = "trusted"  # 程式算出來的,或有型別的結構化事實
    UNTRUSTED_TEXT = "untrusted_text"  # 廣告名稱、素材文字等不可信的文字


class Freshness(StrEnum):
    FRESH = "fresh"
    EXPIRED = "expired"
    VERSION_CHANGED = "version_changed"
    UNVERIFIED = "unverified"  # 有版本卻沒讀到現況的版本,不能說新鮮

    @property
    def is_usable(self) -> bool:
        return self is Freshness.FRESH


def _is_aware(value: object) -> bool:
    """有時區,而且時區真的給得出偏移(有些 tzinfo 的 utcoffset 回 None,等於沒有時區)。"""
    return isinstance(value, datetime) and value.utcoffset() is not None


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    task_id: str
    kind: EvidenceKind
    source: str
    observed_at: datetime
    campaign_version_observed: int | None
    content_hash: str
    trust_class: TrustClass

    def __post_init__(self) -> None:
        problems = [
            ("evidence_id", is_id(self.evidence_id)),
            ("task_id", is_id(self.task_id)),
            ("source", isinstance(self.source, str) and 0 < len(self.source) <= MAX_SOURCE_LENGTH),
            ("kind", isinstance(self.kind, EvidenceKind)),
            ("trust_class", isinstance(self.trust_class, TrustClass)),
            ("observed_at", _is_aware(self.observed_at)),
            ("campaign_version_observed", _is_version_or_none(self.campaign_version_observed)),
            ("content_hash", _is_hash(self.content_hash)),
        ]
        bad = [name for name, ok in problems if not ok]
        if bad:
            raise ValueError(f"證據欄位不合法:{', '.join(bad)}")


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and HASH_PATTERN.fullmatch(value) is not None


def _is_version_or_none(value: object) -> bool:
    return value is None or (is_plain_int(value) and value >= 1)


def _is_positive_finite(value: object) -> bool:
    if not is_plain_number(value):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:  # 大到超出浮點範圍的整數,視為不合法
        return False


def _check_inputs(now: object, max_age_seconds: object, current_version: object) -> None:
    if not _is_aware(now):
        raise ValueError("now 必須是有時區的 datetime")
    if not _is_positive_finite(max_age_seconds):
        raise ValueError("max_age_seconds 必須是正的有限數")
    if not _is_version_or_none(current_version):
        raise ValueError("current_version 必須是正整數或 None")


def check_freshness(
    evidence: Evidence, now: datetime, max_age_seconds: float, current_version: int | None
) -> Freshness:
    """先判年齡(時鐘倒退也算過期,保守),再判版本。沒有版本的證據只看年齡。"""
    _check_inputs(now, max_age_seconds, current_version)
    age = (now - evidence.observed_at).total_seconds()
    if age < 0 or age > max_age_seconds:
        return Freshness.EXPIRED
    if evidence.campaign_version_observed is None:
        return Freshness.FRESH
    if current_version is None:
        return Freshness.UNVERIFIED
    if current_version != evidence.campaign_version_observed:
        return Freshness.VERSION_CHANGED
    return Freshness.FRESH
