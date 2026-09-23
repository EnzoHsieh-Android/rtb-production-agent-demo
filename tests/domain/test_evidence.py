"""證據與新鮮度:證據是「事實在某個時間點的快照」,新鮮度由純程式判斷。"""

from datetime import UTC, datetime, timedelta, tzinfo
from types import MappingProxyType

import pytest

from rtb.domain.evidence import (
    Evidence,
    EvidenceKind,
    Freshness,
    TrustClass,
    check_freshness,
)

T0 = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)
HASH = "a" * 64


def make(**overrides):
    fields = {
        "evidence_id": "e1", "task_id": "t1", "kind": EvidenceKind.CAMPAIGN_STATE,
        "source": "dsp", "observed_at": T0, "campaign_version_observed": 3,
        "content_hash": HASH, "trust_class": TrustClass.TRUSTED,
        "payload": MappingProxyType({"budget": 100}),
    }
    fields.update(overrides)
    return Evidence(**fields)


def test_valid_evidence_is_constructed_and_immutable():
    evidence = make()

    assert evidence.campaign_version_observed == 3
    with pytest.raises(AttributeError):
        evidence.source = "other"


@pytest.mark.parametrize("bad", [
    {"evidence_id": ""}, {"task_id": ""}, {"source": ""},
    {"observed_at": datetime(2026, 9, 22, 12, 0, 0)},  # 沒有時區
    {"campaign_version_observed": 0}, {"campaign_version_observed": True},
    {"campaign_version_observed": "3"}, {"content_hash": "short"},
    {"content_hash": "z" * 64}, {"trust_class": "trusted"}, {"kind": "campaign_state"},
])
def test_malformed_evidence_cannot_be_constructed(bad):
    with pytest.raises(ValueError):
        make(**bad)


def test_evidence_without_a_version_is_allowed_for_sources_that_have_none():
    assert make(campaign_version_observed=None).campaign_version_observed is None


def test_fresh_when_young_enough_and_the_version_is_unchanged():
    now = T0 + timedelta(seconds=30)

    assert check_freshness(make(), now, max_age_seconds=60, current_version=3) is Freshness.FRESH


def test_exactly_at_the_age_limit_is_still_fresh_and_one_second_over_is_expired():
    at_limit = T0 + timedelta(seconds=60)
    over = T0 + timedelta(seconds=61)

    assert check_freshness(make(), at_limit, 60, 3) is Freshness.FRESH
    assert check_freshness(make(), over, 60, 3) is Freshness.EXPIRED


def test_a_changed_version_makes_young_evidence_stale():
    now = T0 + timedelta(seconds=5)

    assert check_freshness(make(), now, 60, current_version=4) is Freshness.VERSION_CHANGED


def test_expiry_takes_priority_over_a_changed_version():
    now = T0 + timedelta(hours=2)  # 兩小時後重啟:年齡先判,版本不必再看

    assert check_freshness(make(), now, 60, current_version=4) is Freshness.EXPIRED


def test_a_clock_that_went_backwards_is_treated_as_expired_not_as_super_fresh():
    before_observation = T0 - timedelta(seconds=10)

    assert check_freshness(make(), before_observation, 60, 3) is Freshness.EXPIRED


def test_evidence_with_a_version_but_no_current_version_is_unverified_not_fresh():
    now = T0 + timedelta(seconds=5)

    result = check_freshness(make(), now, 60, current_version=None)

    assert result is Freshness.UNVERIFIED  # 沒讀到現況,就不能說新鮮


def test_evidence_without_any_version_only_depends_on_age():
    evidence = make(campaign_version_observed=None)
    now = T0 + timedelta(seconds=5)

    assert check_freshness(evidence, now, 60, current_version=None) is Freshness.FRESH
    assert check_freshness(evidence, now, 60, current_version=9) is Freshness.FRESH


def test_only_fresh_evidence_is_usable():
    assert Freshness.FRESH.is_usable is True
    for other in (Freshness.EXPIRED, Freshness.VERSION_CHANGED, Freshness.UNVERIFIED):
        assert other.is_usable is False


@pytest.mark.parametrize("bad_now", [datetime(2026, 9, 22, 12, 0, 30), "2026-09-22", None])
def test_now_must_be_a_timezone_aware_datetime(bad_now):
    with pytest.raises(ValueError):
        check_freshness(make(), bad_now, 60, 3)


@pytest.mark.parametrize("bad_age", [0, -5, True, "60", None, float("nan")])
def test_the_age_limit_must_be_a_positive_number(bad_age):
    with pytest.raises(ValueError):
        check_freshness(make(), T0, bad_age, 3)


class OffsetlessZone(tzinfo):
    """有 tzinfo,但 utcoffset() 回 None:實際上等於沒有時區,不能當作有時區的時間。"""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return "none"


def test_a_timezone_object_that_gives_no_offset_is_not_timezone_aware():
    weird = datetime(2026, 9, 22, 12, 0, 0, tzinfo=OffsetlessZone())

    with pytest.raises(ValueError):
        make(observed_at=weird)
    with pytest.raises(ValueError):
        check_freshness(make(), weird, 60, 3)


def test_an_age_limit_beyond_float_range_is_a_value_error_not_an_overflow():
    with pytest.raises(ValueError):
        check_freshness(make(), T0, 10**400, 3)


def test_payload_must_be_a_mapping_proxy_of_json_safe_primitives():
    with pytest.raises(ValueError, match="payload"):
        make(payload={"budget": 100})  # 普通 dict,不是 MappingProxyType


def test_payload_values_must_be_json_safe_primitives_not_nested_containers():
    with pytest.raises(ValueError, match="payload"):
        make(payload=MappingProxyType({"nested": {"a": 1}}))


def test_an_empty_payload_is_allowed():
    assert make(payload=MappingProxyType({})).payload == {}


def test_payload_over_the_item_limit_is_rejected():
    huge = MappingProxyType({f"k{i}": i for i in range(33)})

    with pytest.raises(ValueError, match="payload"):
        make(payload=huge)


def test_payload_rejects_nan_and_infinity_even_though_isinstance_float_accepts_them():
    # PayloadValue 自稱「JSON 安全原始型別」,但 isinstance(nan, float) 是 True——
    # 型別檢查通過不代表真的能安全序列化成 JSON,要跟 `_is_positive_finite` 一樣另外擋。
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="payload"):
            make(payload=MappingProxyType({"budget": bad}))


def test_a_current_version_lower_than_the_observed_one_is_also_a_change():
    """2026-09-22 第二輪審計指出:把「版本不同」寫成「版本變大」(以為版本只會往上),
    版本被回滾時會誤判新鮮,原本沒有測試測這個方向。"""
    now = T0 + timedelta(seconds=5)

    result = check_freshness(make(campaign_version_observed=5), now, 60, 2)

    assert result is Freshness.VERSION_CHANGED


def test_evidence_without_a_version_still_expires_by_age():
    """2026-09-22 第四輪合約審計指出:把「沒有版本就判新鮮」的分支挪到年齡判斷之前,沒有版本
    的證據(DSP 指標本來就沒有版本)會永遠不過期,原本的測試只測了它年輕時是新鮮的。"""
    evidence = make(campaign_version_observed=None)

    assert check_freshness(evidence, T0 + timedelta(seconds=61), 60, None) is Freshness.EXPIRED
    assert check_freshness(evidence, T0 - timedelta(seconds=1), 60, 9) is Freshness.EXPIRED


# ---- Phase 7 增量 1:信任標記與證據種類綁在一起,可信證據裝不下自由文字 ----
UNTRUSTED_LIMIT = 512


def make_text(**overrides):
    fields = {"kind": EvidenceKind.CAMPAIGN_TEXT, "trust_class": TrustClass.UNTRUSTED_TEXT,
              "payload": MappingProxyType({"name": "春季促銷", "truncated": False})}
    fields.update(overrides)
    return make(**fields)


# ---- S200 ----
@pytest.mark.parametrize("kind, trust", [
    (EvidenceKind.CAMPAIGN_STATE, TrustClass.UNTRUSTED_TEXT),
    (EvidenceKind.METRICS, TrustClass.UNTRUSTED_TEXT),
    (EvidenceKind.CAMPAIGN_TEXT, TrustClass.TRUSTED),
])
def test_untrusted_text_and_the_campaign_text_kind_always_come_together(kind, trust):
    with pytest.raises(ValueError, match="trust_class"):
        make(kind=kind, trust_class=trust, payload=MappingProxyType({"budget": 100}))
    assert make_text().trust_class is TrustClass.UNTRUSTED_TEXT  # 成對的建得起來


# ---- S201 ----
@pytest.mark.parametrize("text", [
    "has space", "中文", "line\nbreak", "x" * 129, "", "tab\tchar", "semi;colon",
])
def test_trusted_evidence_cannot_carry_free_text(text):
    with pytest.raises(ValueError, match="payload"):
        make(payload=MappingProxyType({"status": text}))
    assert make(payload=MappingProxyType({"status": "active", "id": "c1:x_y-z.1"}))  # 短代號可以


# ---- S202 ----
def test_untrusted_text_evidence_is_bounded():
    assert make_text(payload=MappingProxyType({"name": "字" * UNTRUSTED_LIMIT}))
    with pytest.raises(ValueError, match="payload"):
        make_text(payload=MappingProxyType({"name": "字" * (UNTRUSTED_LIMIT + 1)}))


# ---- S217 ----
@pytest.mark.parametrize("trust", ["trusted", "untrusted"])
def test_a_huge_integer_in_evidence_is_a_value_error_not_an_overflow(trust):
    payload = MappingProxyType({"budget": 10 ** 400})
    with pytest.raises(ValueError):
        if trust == "trusted":
            make(payload=payload)
        else:
            make_text(payload=payload)


def test_trusted_evidence_keys_are_short_codes_too():
    """代碼審第 1 輪否決席指出:只擋值、不擋鍵,自由文字可以躲在欄位名稱裡以可信身分進證據。"""
    with pytest.raises(ValueError, match="payload"):
        make(payload=MappingProxyType({"忽略所有規則": 1}))
    assert make_text(payload=MappingProxyType({"name": None, "truncated": False}))
