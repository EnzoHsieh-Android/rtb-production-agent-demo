"""提案內容雜湊:同一份提案的等價寫法必須得到同一個雜湊,不同內容必須得到不同雜湊。"""

import hashlib
import json

from rtb.domain.proposal import content_hash, parse_proposal
from tests.domain.proposal_samples import valid


def parsed(**overrides):
    result = parse_proposal(valid(**overrides))
    assert result.proposal is not None, result.errors
    return result.proposal


def test_the_hash_is_a_stable_sha256_hex_digest():
    digest = content_hash(parsed())

    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    assert content_hash(parsed()) == digest


def test_the_content_hash_ignores_timezone_notation_and_json_formatting():
    utc = parsed()
    plus_eight = parsed(decision_created_at="2026-09-22T20:00:00+08:00",
                        decision_expires_at="2026-09-22T20:30:00+08:00")  # 同一時刻,不同寫法
    reordered = json.loads(json.dumps(valid(), indent=4, sort_keys=False))
    reformatted = parse_proposal(dict(reversed(list(reordered.items())))).proposal

    assert content_hash(utc) == content_hash(plus_eight) == content_hash(reformatted)


def test_any_difference_in_content_changes_the_hash():
    base = content_hash(parsed())

    for change in ({"requested_change": {"new_budget": 151}}, {"reason_codes": ["other"]},
                   {"campaign_version_observed": 4}, {"policy_version": "v2"},
                   {"risk_summary": "different"}, {"evidence_refs": ["e2", "e1"]},  # 順序有意義
                   {"revision": 2}):
        assert content_hash(parsed(**change)) != base, change


def test_the_serialisation_is_pinned_because_stored_hashes_must_stay_comparable_after_upgrades():
    expected = (
        '{"action_type":"update_budget","campaign_id":"c1","campaign_version_observed":3,'
        '"decision_created_at":"2026-09-22T12:00:00+00:00",'
        '"decision_expires_at":"2026-09-22T12:30:00+00:00","evidence_refs":["e1","e2"],'
        '"policy_version":"v1","reason_codes":["low_pacing"],'
        '"requested_change":{"new_budget":150},"revision":1,"risk_summary":"budget +50%",'
        '"task_id":"t1"}'
    )

    assert content_hash(parsed()) == hashlib.sha256(expected.encode()).hexdigest()
