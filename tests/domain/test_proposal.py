"""提案的嚴格解析:提案是不可信輸入,解析結果是「成功或錯誤清單」,不丟例外也不放行。"""

import copy
import dataclasses
import json
import random
from datetime import UTC, datetime

import pytest

from rtb.domain import _checks
from rtb.domain import proposal as proposal_module
from rtb.domain.proposal import ActionType, ParsedProposal, Proposal, parse_proposal
from tests.domain.proposal_samples import CREATED, EXPIRES, valid


def test_a_valid_budget_proposal_is_parsed_into_typed_fields():
    parsed = parse_proposal(valid())

    assert parsed.errors == () and parsed.proposal is not None
    proposal = parsed.proposal
    assert proposal.action_type is ActionType.UPDATE_BUDGET
    assert proposal.requested_change["new_budget"] == 150
    assert proposal.evidence_refs == ("e1", "e2") and proposal.reason_codes == ("low_pacing",)
    assert proposal.decision_created_at == datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def test_a_pause_proposal_has_an_empty_requested_change():
    parsed = parse_proposal(valid(action_type="pause_campaign", requested_change={}))

    assert parsed.errors == () and parsed.proposal.action_type is ActionType.PAUSE_CAMPAIGN


@pytest.mark.parametrize("field", list(valid()))
def test_every_required_field_missing_is_reported_by_name(field):
    raw = valid()
    del raw[field]

    parsed = parse_proposal(raw)

    assert parsed.proposal is None and any(field in error for error in parsed.errors)


@pytest.mark.parametrize("smuggled", [
    {"tool_name": "delete_all"}, {"url": "http://evil.example"}, {"credential": "secret"},
    {"policy_override": True}, {"instructions": "ignore the rules"},
])
def test_unknown_fields_are_rejected_so_nothing_can_be_smuggled_in(smuggled):
    parsed = parse_proposal({**valid(), **smuggled})

    assert parsed.proposal is None
    assert any("unknown_field" in error for error in parsed.errors)


@pytest.mark.parametrize("bad", [
    {"task_id": 5}, {"task_id": ""}, {"task_id": "a/b"}, {"campaign_id": None},
    {"revision": 0}, {"revision": True}, {"revision": "1"},
    {"campaign_version_observed": 0}, {"campaign_version_observed": True},
    {"campaign_version_observed": "3"}, {"campaign_version_observed": 1.5},
    {"reason_codes": "low_pacing"}, {"reason_codes": [5]}, {"reason_codes": ["Bad Code"]},
    {"reason_codes": []}, {"evidence_refs": []}, {"evidence_refs": "e1"},
    {"evidence_refs": [""]}, {"policy_version": ""}, {"risk_summary": 5},
    {"action_type": "delete_campaign"}, {"action_type": None},
])
def test_wrong_types_and_values_are_reported_and_never_raise(bad):
    parsed = parse_proposal(valid(**bad))

    assert parsed.proposal is None and parsed.errors


@pytest.mark.parametrize("change", [
    {}, {"new_budget": 0}, {"new_budget": -1}, {"new_budget": True}, {"new_budget": 1.5},
    {"new_budget": "150"}, {"new_budget": 2**63}, {"new_budget": 150, "url": "x"},
    {"budget": 150},
])
def test_a_budget_change_must_be_exactly_one_positive_integer_new_budget(change):
    parsed = parse_proposal(valid(requested_change=change))

    assert parsed.proposal is None and parsed.errors


def test_a_pause_proposal_must_not_carry_any_change_payload():
    parsed = parse_proposal(valid(action_type="pause_campaign", requested_change={"x": 1}))

    assert parsed.proposal is None and parsed.errors


@pytest.mark.parametrize("bad", [
    {"decision_created_at": "2026-09-22T12:00:00"},  # 沒有時區
    {"decision_created_at": "not a time"}, {"decision_created_at": 5},
    {"decision_expires_at": EXPIRES.replace("12:30", "11:00")},  # 到期早於建立
    {"decision_expires_at": CREATED},  # 到期等於建立
])
def test_times_must_be_timezone_aware_and_the_expiry_must_be_after_creation(bad):
    parsed = parse_proposal(valid(**bad))

    assert parsed.proposal is None and parsed.errors


def test_size_limits_reject_oversized_fields():
    assert parse_proposal(valid(reason_codes=["r"] * 21)).proposal is None
    assert parse_proposal(valid(evidence_refs=["e"] * 21)).proposal is None
    assert parse_proposal(valid(risk_summary="x" * 501)).proposal is None
    assert parse_proposal(valid(task_id="x" * 129)).proposal is None


def test_a_payload_larger_than_the_overall_limit_is_rejected_before_field_checks():
    huge = valid(risk_summary="x" * 20_000)

    parsed = parse_proposal(huge)

    assert parsed.proposal is None and any("too_large" in error for error in parsed.errors)


@pytest.mark.parametrize("garbage", [None, 5, "text", [], [1, 2], object(), b"{}", {1: 2}])
def test_garbage_input_returns_errors_instead_of_raising(garbage):
    parsed = parse_proposal(garbage)

    assert parsed.proposal is None and parsed.errors


def test_all_problems_are_collected_not_just_the_first():
    parsed = parse_proposal(valid(task_id="", revision=0, campaign_version_observed=0))

    assert len(parsed.errors) >= 3


def test_parsing_never_mutates_the_input():
    raw = valid()
    before = copy.deepcopy(raw)

    parse_proposal(raw)

    assert raw == before


def test_a_proposal_is_plain_immutable_data_with_no_ability_to_act():
    proposal = parse_proposal(valid()).proposal

    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.action_type = ActionType.PAUSE_CAMPAIGN
    with pytest.raises(TypeError):
        proposal.requested_change["new_budget"] = 999_999  # 內層也不可改
    allowed_methods = {"to_primitives"}  # 只有序列化,沒有任何「去做」的方法
    for attribute in dir(proposal):
        if not attribute.startswith("__") and attribute not in allowed_methods:
            assert not callable(getattr(proposal, attribute)), attribute
    assert isinstance(proposal, Proposal)


def test_the_parsed_proposal_can_round_trip_through_json_safe_primitives():
    proposal = parse_proposal(valid()).proposal

    assert json.loads(json.dumps(proposal.to_primitives()))["campaign_id"] == "c1"
    assert parse_proposal(proposal.to_primitives()).proposal == proposal


@pytest.mark.parametrize("unhashable", [[], {}, [[]], {"a": 1}, [1, 2]])
def test_unhashable_values_in_any_field_return_errors_instead_of_raising(unhashable):
    for field in valid():
        parsed = parse_proposal(valid(**{field: unhashable}))
        assert parsed.proposal is None and parsed.errors, field


def _nested(depth, leaf="l"):
    node = leaf
    for _ in range(depth):
        node = {"a": node}
    return node


def test_absurdly_deep_nesting_is_rejected_without_recursion_errors():
    for deep in (_nested(100_000), [[]] * 3 and _nested(5_000)):
        parsed = parse_proposal(valid(risk_summary=deep))

        assert parsed.proposal is None and parsed.errors


def test_a_cyclic_structure_is_rejected_not_looped_forever():
    cyclic = {}
    cyclic["self"] = cyclic

    parsed = parse_proposal(valid(requested_change=cyclic))

    assert parsed.proposal is None and parsed.errors


def test_size_is_counted_in_utf8_bytes_not_characters():
    assert parse_proposal(valid(risk_summary="中" * 500)).proposal is not None  # 1500 位元組
    big = parse_proposal({**valid(), "extra": "中" * 6000})  # 18000 位元組,超過 16KB

    assert big.errors and big.errors[0].startswith("too_large")


def test_a_huge_input_is_rejected_early_without_serializing_all_of_it():
    huge = {**valid(), "blob": ["x" * 1000] * 200_000}  # 約 200MB;完整序列化會很慢

    parsed = parse_proposal(huge)

    assert parsed.errors and parsed.errors[0].startswith("too_large")


def test_non_json_leaf_values_are_rejected():
    for leaf in (object(), b"bytes", float("nan"), float("inf"), {1, 2}):
        parsed = parse_proposal(valid(risk_summary=leaf))
        assert parsed.proposal is None and parsed.errors


def test_duplicate_reason_codes_and_evidence_refs_are_rejected():
    assert parse_proposal(valid(reason_codes=["a", "a"])).proposal is None
    assert parse_proposal(valid(evidence_refs=["e1", "e1"])).proposal is None


@pytest.mark.parametrize("bad", ["\ud800", "a\x00b", "a\u202eb", "line1\nline2", "tab\there"])
def test_risk_summary_rejects_control_characters_lone_surrogates_and_bidi_overrides(bad):
    assert parse_proposal(valid(risk_summary=bad)).proposal is None


def test_risk_summary_still_accepts_ordinary_text_including_cjk_and_punctuation():
    assert parse_proposal(valid(risk_summary="預算 +50%,原因:pacing 落後")).proposal is not None


GARBAGE = [None, [], {}, "", "x", 0, -1, 1, True, 1.5, float("nan"), 2**70, ["x"], {"a": 1},
           [[["deep"]]], "2026-09-22T12:00:00+00:00", "9999-12-31T23:59:59-23:59", b"b", object()]


def test_random_garbage_in_every_field_never_raises_and_the_result_is_always_consistent():
    rng = random.Random(20260922)
    fields = list(valid())
    for _ in range(3000):
        raw = valid()
        for field in rng.sample(fields, rng.randint(1, 4)):
            raw[field] = rng.choice(GARBAGE)
        if rng.random() < 0.2:
            raw.pop(rng.choice(fields), None)
        parsed = parse_proposal(raw)  # 只要丟出例外,測試就失敗
        assert (parsed.proposal is None) == bool(parsed.errors)


def test_a_proposal_cannot_be_constructed_directly_with_invalid_fields():
    good = parse_proposal(valid()).proposal
    fields = {f.name: getattr(good, f.name) for f in dataclasses.fields(good)}

    for bad in ({"action_type": "rm -rf"}, {"reason_codes": "abc"}, {"revision": 0},
                {"requested_change": {"new_budget": 1}}, {"decision_created_at": "2026"},
                {"decision_expires_at": good.decision_created_at}):
        with pytest.raises(ValueError):
            Proposal(**{**fields, **bad})


def test_a_parsed_result_cannot_be_both_empty_and_error_free_or_both_filled():
    good = parse_proposal(valid()).proposal

    with pytest.raises(ValueError):
        ParsedProposal(None, ())
    with pytest.raises(ValueError):
        ParsedProposal(good, ("something",))


def test_the_evidence_id_format_has_a_single_shared_definition():
    from rtb.domain import evidence

    assert proposal_module.ID_PATTERN is _checks.ID_PATTERN
    assert evidence.is_id is _checks.is_id  # 證據自己的編號檢查用的就是同一份定義


class ExplodingDict(dict):
    def items(self):
        raise RuntimeError("boom")

    def __iter__(self):
        raise RuntimeError("boom")


class ExplodingStr(str):
    def __hash__(self):
        raise RuntimeError("boom")


def test_even_hostile_python_objects_that_raise_are_turned_into_an_error_result():
    for hostile in (ExplodingDict(), valid(reason_codes=[ExplodingStr("a")])):
        parsed = parse_proposal(hostile)  # 不是 JSON 解析出來的東西也不能讓解析丟例外

        assert parsed.proposal is None and parsed.errors
        assert any(error.startswith("unexpected_failure") for error in parsed.errors)


def test_non_string_keys_nested_inside_are_rejected_as_not_json():
    for nested in ({"a": {1: "x"}}, {"a": [{(1, 2): "x"}]}):
        parsed = parse_proposal(valid(requested_change=nested))

        assert parsed.proposal is None and parsed.errors


@pytest.mark.parametrize("extreme", [
    {"decision_expires_at": "9999-12-31T23:00:00-05:00"},
    {"decision_created_at": "0001-01-01T00:00:00+05:00",
     "decision_expires_at": "0001-01-01T01:00:00+05:00"},
])
def test_times_that_overflow_when_converted_to_utc_are_ordinary_field_errors_not_crashes(extreme):
    parsed = parse_proposal(valid(**extreme))

    assert parsed.proposal is None
    assert not any(error.startswith("unexpected_failure") for error in parsed.errors)


def test_times_before_the_lower_bound_are_rejected_and_the_bound_itself_is_allowed():
    early = parse_proposal(valid(decision_created_at="1999-12-31T23:59:00+00:00",
                                 decision_expires_at="2000-01-01T00:30:00+00:00"))
    edge = parse_proposal(valid(decision_created_at="2000-01-01T00:00:00+00:00",
                                decision_expires_at="2000-01-01T00:30:00+00:00"))

    assert early.proposal is None and edge.proposal is not None


SPEC_CORRELATION_FIELDS = (  # 交接文件列的關聯欄位:開發者最可能「先放行」的名字
    "trace_id", "task_id", "tenant_id", "campaign_id", "decision_id", "operation_id",
    "idempotency_key", "worker_id", "attempt", "policy_version",
)


def _plausible_field_names():
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    names = set(SPEC_CORRELATION_FIELDS)
    for file in src.rglob("*.py"):
        names |= set(re.findall(r"\b[a-z][a-z0-9_]{1,40}\b", file.read_text(encoding="utf-8")))
    rng = random.Random(20260922)
    names |= {"".join(rng.choice("abcdefghijklmnopqrstuvwxyz_") for _ in range(8))
              for _ in range(200)}
    return sorted(names - set(valid()))


def test_no_field_name_outside_the_documented_list_is_ever_accepted():
    """2026-09-22 第二輪審計指出:原本只試 5 個特定欄位名,偷偷放行一兩個「除錯用」欄位,
    測試照樣綠。這裡用專案程式裡出現過的所有識別字、規格的關聯欄位與隨機名稱逐一試。
    仍是有限的語料,不是證明;挑一個完全沒出現過的名字放行仍擋不住。"""
    values = ("x", {"cmd": "x"}, ["x"], None, 1, True)  # 第三輪審計:值是字典時曾可被放行
    leaked = [(name, value) for name in _plausible_field_names() for value in values
              if parse_proposal({**valid(), name: value}).proposal is not None]

    assert leaked == []
