"""證據與新鮮度:證據是「事實在某個時間點的快照」,不是永遠成立的事實。

新鮮度由純程式判斷,先看年齡,再看版本:過期或版本已變就要重讀。
重讀是安全的,重做副作用才危險;執行的那一刻執行行程仍要重讀現況並重新授權,
新鮮度只是第一道,不是最後一道。

信任邊界(Phase 7 增量 1):不可信文字(廣告名稱這類)只能住在「廣告文字」這一種證據裡;
標成可信的證據,字串只能是短代號。自由文字能不能被當成可信,由型別保證,不靠每個用戶端自律。
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeGuard

from rtb.domain._checks import is_aware, is_id, is_plain_int, is_plain_number, is_sha256

MAX_SOURCE_LENGTH = 64
MAX_PAYLOAD_ITEMS = 32  # 證據的原始欄位數;DSP 的現況、指標回應都是固定的小字典,遠低於這個上限
MAX_UNTRUSTED_TEXT_LENGTH = 512  # 不可信文字證據裡每個字串的字元上限(2026-09-23 使用者裁定)
PayloadValue = str | int | float | bool | None


class EvidenceKind(StrEnum):
    CAMPAIGN_STATE = "campaign_state"
    METRICS = "metrics"
    CAMPAIGN_TEXT = "campaign_text"  # 廣告名稱這類不可信文字;只能配不可信文字的信任標記
    # Phase 13 增量 2 的四種收據(AI 追加查詢的結果,程式從原始回應算出的扁平數字字串與短代號):
    # 只給 AI 決策函式與展示頁讀;現行決策規則、不提案原因與建提案拿到的證據只有上面三種(AI 決策
    # 函式在入口濾掉),原始回應另存調查原始資料表,不進證據表
    LONGER_WINDOW = "longer_window"
    CHANGE_HISTORY = "change_history"
    DAILY_TREND = "daily_trend"
    PAST_ADJUSTMENTS = "past_adjustments"


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
    payload: MappingProxyType[str, PayloadValue]
    """讀到的原始欄位(例如現況的 budget/status、指標的 impressions/spend),決策要用實際數字
    判斷時讀這裡;`content_hash` 只用來判斷「有沒有變」,不是給決策讀的。"""

    def __post_init__(self) -> None:
        problems = [
            ("evidence_id", is_id(self.evidence_id)),
            ("task_id", is_id(self.task_id)),
            ("source", isinstance(self.source, str) and 0 < len(self.source) <= MAX_SOURCE_LENGTH),
            ("payload", _is_payload(self.payload)),
            ("kind", isinstance(self.kind, EvidenceKind)),
            ("trust_class", isinstance(self.trust_class, TrustClass)),
            ("observed_at", is_aware(self.observed_at)),
            ("campaign_version_observed", _is_version_or_none(self.campaign_version_observed)),
            ("content_hash", is_sha256(self.content_hash)),
            ("trust_class", _trust_matches_kind(self.kind, self.trust_class)),
            ("payload", _strings_fit_trust(self.payload, self.trust_class)),
        ]
        bad = list(dict.fromkeys(name for name, ok in problems if not ok))  # 同名只報一次
        if bad:
            raise ValueError(f"證據欄位不合法:{', '.join(bad)}")


def _is_payload_value(item: object) -> bool:
    if item is None or isinstance(item, str | bool):
        return True
    if isinstance(item, int | float):
        # bool 已經在上面排除;NaN/Infinity 不是合法 JSON 數字字面值,型別名稱
        # PayloadValue 又自稱「JSON 安全」,兩邊要對得上,跟同檔案 `_is_positive_finite`
        # 的既有做法一致(2026-09-22 代碼審發現這裡漏掉,跟數值驗證的既有慣例不一致)。
        try:
            return math.isfinite(item)
        except OverflowError:  # 大到超出浮點範圍的整數,視為不合法(同檔 _is_positive_finite 的寫法)
            return False
    return False


def _is_payload(value: object) -> TypeGuard[MappingProxyType[str, PayloadValue]]:
    if not isinstance(value, MappingProxyType) or len(value) > MAX_PAYLOAD_ITEMS:
        return False
    return all(isinstance(key, str) and _is_payload_value(item) for key, item in value.items())


def _trust_matches_kind(kind: object, trust: object) -> bool:
    """不可信文字若且唯若廣告文字:不可信文字掛不到現況或指標底下,廣告文字也標不成可信。"""
    return (kind is EvidenceKind.CAMPAIGN_TEXT) == (trust is TrustClass.UNTRUSTED_TEXT)


def _strings_fit_trust(payload: object, trust: object) -> bool:
    """可信證據的鍵與字串值都只能是短代號(跟識別碼同一個格式),自由文字躲不進欄位名稱;
    不可信文字的字串值有長度上限。"""
    if not isinstance(payload, MappingProxyType):
        return False  # 型別不對由 _is_payload 報,這裡只是不要在壞輸入上往下走
    strings = [item for item in payload.values() if isinstance(item, str)]
    if trust is TrustClass.UNTRUSTED_TEXT:
        return all(len(item) <= MAX_UNTRUSTED_TEXT_LENGTH for item in strings)
    return all(is_id(item) for item in [*payload.keys(), *strings])


def _is_version_or_none(value: object) -> TypeGuard[int | None]:
    return value is None or (is_plain_int(value) and value >= 1)


def _is_positive_finite(value: object) -> bool:
    if not is_plain_number(value):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:  # 大到超出浮點範圍的整數,視為不合法
        return False


def _check_inputs(now: object, max_age_seconds: object, current_version: object) -> None:
    if not is_aware(now):
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
