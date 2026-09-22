"""外部寫入的嘗試(領域層):冪等鍵、嘗試狀態機、結果代碼與細節文字的規則。"""

import pytest

from rtb.domain.attempt import (
    ESCALATION_CODES,
    FAILURE_CODES,
    TERMINAL_STATES,
    TRANSITIONS,
    UNRESOLVED_STATES,
    AttemptState,
    OutcomeCode,
    can_transition,
    code_fits,
    is_clean_detail,
    operation_key,
)
from rtb.domain.proposal import parse_proposal
from rtb.dsp.store import IDEMPOTENCY_KEY_PATTERN
from tests.domain.proposal_samples import valid

A = AttemptState
C = OutcomeCode


def proposal(**overrides):
    parsed = parse_proposal(valid(**overrides))
    assert parsed.proposal is not None, parsed.errors
    return parsed.proposal


# ---- [S1] 修訂序號與決策時間不影響鍵 ----
NON_IDENTITY_OVERRIDES = [
    {"revision": 7},
    {"decision_created_at": "2026-09-22T12:10:00+00:00"},
    {"decision_expires_at": "2026-09-22T12:45:00+00:00"},
]


def test_the_operation_key_ignores_revision_and_decision_times_one_at_a_time():
    base = operation_key(proposal())
    for override in NON_IDENTITY_OVERRIDES:
        assert operation_key(proposal(**override)) == base, override


def test_the_same_moment_written_in_another_time_zone_gives_the_same_key():
    assert operation_key(proposal()) == operation_key(
        proposal(decision_created_at="2026-09-22T20:00:00+08:00"))


# ---- [S2] 五個實質欄位各自單獨變動都會換鍵 ----
IDENTITY_OVERRIDES = [
    {"task_id": "t2"},
    {"campaign_id": "c2"},
    {"action_type": "pause_campaign", "requested_change": {}},
    {"requested_change": {"new_budget": 151}},
    {"campaign_version_observed": 4},
]


def test_each_substantive_field_alone_changes_the_operation_key():
    base = operation_key(proposal())
    keys = {operation_key(proposal(**override)) for override in IDENTITY_OVERRIDES}
    assert base not in keys
    assert len(keys) == len(IDENTITY_OVERRIDES)  # 彼此也都不同


def test_the_action_alone_changes_the_key_even_with_the_same_requested_change():
    """合法提案裡動作和要求的變更綁在一起,做不出「只有動作不同」的一對;這裡用只帶那五個欄位的
    替身物件,確認動作本身有進鍵(代碼審指出:拿掉動作,原本的測試照樣全綠)。"""
    from types import SimpleNamespace

    from rtb.domain.proposal import ActionType

    base = proposal()
    stand_in = SimpleNamespace(
        task_id=base.task_id, campaign_id=base.campaign_id,
        requested_change=base.requested_change,
        campaign_version_observed=base.campaign_version_observed,
        action_type=ActionType.PAUSE_CAMPAIGN)
    assert operation_key(stand_in) != operation_key(base)


def test_fields_outside_the_identity_do_not_change_the_key_either():
    base = operation_key(proposal())
    for override in ({"reason_codes": ["other"]}, {"evidence_refs": ["e9"]},
                     {"policy_version": "v2"}, {"risk_summary": "different words"}):
        assert operation_key(proposal(**override)) == base, override


# ---- [S3] 鍵帶算法版本前綴,且 DSP 收得下 ----
def test_the_operation_key_is_versioned_and_accepted_by_the_dsp_key_format():
    for override in [{}, *IDENTITY_OVERRIDES, *NON_IDENTITY_OVERRIDES]:
        key = operation_key(proposal(**override))
        assert key.startswith("k1-")
        assert len(key) == len("k1-") + 64
        assert IDEMPOTENCY_KEY_PATTERN.fullmatch(key), key


# ---- [S7] 一般轉換表逐對比對 ----
DOCUMENTED_GENERAL_TRANSITIONS = {
    (A.IN_FLIGHT, A.COMMITTED_UNVERIFIED), (A.IN_FLIGHT, A.FAILED),
    (A.IN_FLIGHT, A.ESCALATED), (A.IN_FLIGHT, A.UNKNOWN),
    (A.UNKNOWN, A.COMMITTED_UNVERIFIED), (A.UNKNOWN, A.IN_FLIGHT),
    (A.UNKNOWN, A.FAILED), (A.UNKNOWN, A.ESCALATED),
    (A.COMMITTED_UNVERIFIED, A.VERIFIED), (A.COMMITTED_UNVERIFIED, A.ESCALATED),
}


def test_every_pair_of_attempt_states_is_legal_exactly_when_the_table_says_so():
    """從程式自己的狀態清單列舉所有配對(含自己到自己):表格多一條或少一條都會紅。
    離開轉人工、自轉換、離開終點都不在一般轉換裡——那兩種專用操作不走這張表。"""
    for current in AttemptState:
        for target in AttemptState:
            expected = (current, target) in DOCUMENTED_GENERAL_TRANSITIONS
            assert can_transition(current, target) is expected, (current, target)


def test_the_states_and_their_stored_names_are_exactly_the_documented_ones():
    assert {s.value for s in AttemptState} == {
        "in_flight", "unknown", "committed_unverified", "verified", "failed", "escalated"}
    assert frozenset({A.VERIFIED, A.FAILED}) == TERMINAL_STATES
    assert frozenset(AttemptState) - TERMINAL_STATES == UNRESOLVED_STATES


def test_unknown_or_non_state_values_are_illegal_not_a_crash():
    assert can_transition("bogus", A.FAILED) is False
    assert can_transition(A.IN_FLIGHT, None) is False
    assert can_transition(A.IN_FLIGHT, "unknown") is True  # 存進資料庫的字串照樣認得


def test_the_illegal_transition_error_is_defined_in_the_domain_layer():
    from rtb.domain.attempt import IllegalAttemptTransition
    from rtb.executor import attempt_store

    assert attempt_store.IllegalAttemptTransition is IllegalAttemptTransition
    assert issubclass(IllegalAttemptTransition, ValueError)


# ---- [S8] 轉換表無法從外部改寫 ----
def test_the_attempt_transition_table_cannot_be_changed_from_outside(monkeypatch):
    from rtb.domain import attempt

    with pytest.raises(TypeError):
        TRANSITIONS[A.ESCALATED] = frozenset({A.VERIFIED})
    before = {state: TRANSITIONS[state] for state in A}
    exposed = [name for name, value in vars(attempt).items() if isinstance(value, dict)]
    for name in exposed:
        monkeypatch.setitem(getattr(attempt, name), A.ESCALATED, frozenset({A.VERIFIED}))

    assert exposed  # 至少改到建表用的那張原始表格,不是空跑
    assert {state: TRANSITIONS[state] for state in A} == before
    assert can_transition(A.ESCALATED, A.VERIFIED) is False


# ---- [S16] 結果代碼的類別要配對應的目標狀態(領域層的判斷;儲存層另有測試) ----
def expected_fit(target, code, by_resolution):
    if code is None:
        return target not in (A.FAILED, A.ESCALATED)
    if target is A.FAILED:
        return code in FAILURE_CODES and (code is C.MANUAL_FAILURE) == by_resolution
    if target is A.ESCALATED:
        return code in ESCALATION_CODES and not by_resolution
    return False


@pytest.mark.parametrize("by_resolution", [False, True])
def test_every_code_fits_only_its_own_kind_of_target_state(by_resolution):
    for target in AttemptState:
        for code in [None, *OutcomeCode]:
            assert code_fits(target, code, by_resolution=by_resolution) is expected_fit(
                target, code, by_resolution), (target, code, by_resolution)


def test_the_two_code_kinds_split_the_whole_enum_with_no_overlap():
    assert frozenset(OutcomeCode) == FAILURE_CODES | ESCALATION_CODES
    assert not FAILURE_CODES & ESCALATION_CODES
    assert C.IDEMPOTENCY_CONFLICT in ESCALATION_CODES  # 同鍵不同內容一律轉人工,不是失敗


def test_unrecognised_codes_never_fit():
    for target in AttemptState:
        for code in ("version_conflict_", "", 5, object()):
            assert code_fits(target, code, by_resolution=False) is False


def test_stored_code_strings_are_recognised():
    assert code_fits(A.FAILED, "version_conflict", by_resolution=False) is True


# ---- 細節文字:只收 ASCII 可列印字元、有長度上限 ----
@pytest.mark.parametrize("text", [
    "store_busy: 503", "x" * 500, "",
])
def test_clean_ascii_detail_is_accepted(text):
    assert is_clean_detail(text) is True


@pytest.mark.parametrize("text", [
    "x" * 501,
    "line\nbreak",
    "狀態:已驗證",  # 全形冒號與中文:能偽裝成系統欄位
    "\uff53\uff54\uff41\uff54\uff45",  # 全形英文字(寫成跳脫碼,原始碼裡不放看不見差別的字)
    "\u202eevil",  # 雙向覆寫(寫成跳脫碼,原始碼裡不放隱形字元)
    "tab\there",
    None,
    5,
])
def test_detail_that_is_not_short_printable_ascii_is_rejected(text):
    assert is_clean_detail(text) is False


def test_the_shared_time_zone_check_is_the_one_the_attempt_store_uses():
    from datetime import UTC, datetime

    from rtb.domain._checks import is_aware
    from rtb.executor import attempt_store

    assert is_aware(datetime(2026, 9, 22, tzinfo=UTC)) is True
    assert is_aware(datetime(2026, 9, 22)) is False
    assert is_aware("2026-09-22") is False
    assert not hasattr(attempt_store, "_check_now")  # 不再各寫一份


def test_no_domain_module_defines_its_own_time_zone_check():
    """時區判斷只有 _checks.is_aware 一份:證據、提案、嘗試紀錄都用它。"""
    from pathlib import Path

    from rtb.domain import evidence

    domain = Path(evidence.__file__).resolve().parent
    offenders = [f.name for f in domain.rglob("*.py")
                 if f.name != "_checks.py" and "utcoffset()" in f.read_text(encoding="utf-8")]
    assert offenders == []
    assert not hasattr(evidence, "_is_aware")
