"""提案測試共用的樣本:領域層的解析測試與收件口測試用同一批,不各寫一份。"""

CREATED = "2026-09-22T12:00:00+00:00"
EXPIRES = "2026-09-22T12:30:00+00:00"


def valid(**overrides):
    raw = {
        "task_id": "t1", "revision": 1, "campaign_id": "c1", "action_type": "update_budget",
        "requested_change": {"new_budget": 150}, "reason_codes": ["low_pacing"],
        "evidence_refs": ["e1", "e2"], "campaign_version_observed": 3,
        "decision_created_at": CREATED, "decision_expires_at": EXPIRES,
        "policy_version": "v1", "risk_summary": "budget +50%",
    }
    raw.update(overrides)
    return raw


# 解析器一定要拒絕的樣本:每一筆是「合法提案加上一處壞掉」的覆寫
REJECTED_OVERRIDES = [
    {"task_id": 5}, {"task_id": ""}, {"task_id": "a/b"}, {"campaign_id": None},
    {"revision": 0}, {"revision": True}, {"revision": "1"},
    {"campaign_version_observed": 0}, {"campaign_version_observed": True},
    {"campaign_version_observed": "3"}, {"campaign_version_observed": 1.5},
    {"reason_codes": "low_pacing"}, {"reason_codes": [5]}, {"reason_codes": ["Bad Code"]},
    {"reason_codes": []}, {"evidence_refs": []}, {"evidence_refs": "e1"},
    {"evidence_refs": [""]}, {"policy_version": ""}, {"risk_summary": 5},
    {"action_type": "delete_campaign"}, {"action_type": None},
    {"requested_change": {}}, {"requested_change": {"new_budget": 0}},
    {"requested_change": {"new_budget": True}}, {"requested_change": {"new_budget": "150"}},
    {"requested_change": {"new_budget": 150, "url": "x"}},
    {"decision_created_at": "2026-09-22T12:00:00"},
    {"decision_expires_at": "2026-09-22T11:00:00+00:00"},
    {"tool_name": "delete_all"}, {"url": "http://evil.example"}, {"credential": "secret"},
    {"risk_summary": "x" * 501}, {"reason_codes": ["r"] * 21},
    # 時間的極端值:換成 UTC 會溢位、或離現實太遠
    {"decision_expires_at": "9999-12-31T23:00:00-05:00"},
    {"decision_created_at": "0001-01-01T00:00:00+05:00",
     "decision_expires_at": "0001-01-01T01:00:00+05:00"},
    {"decision_expires_at": "2126-09-22T12:30:00+00:00"},  # 到期時間遠在天邊
    {"decision_expires_at": "2026-09-22T13:00:01+00:00"},  # 有效期超過一小時
]
