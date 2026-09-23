"""寫入能力憑證的測試共用樣本:測試金鑰與明確呼叫的簽章輔助。

刻意不做「共用設定自動附上」:既有合約測試明確呼叫 header() 帶合法憑證、走真實驗證路徑;
拒收測試直接不帶或組壞的,不會被自動簽章蓋掉。
"""

import time

from rtb.capabilitykit import MIN_KEY_BYTES, encode

TEST_KEY = b"test-capability-key-" + b"0" * MIN_KEY_BYTES
TEST_APPROVAL_KEY = b"test-approval-key-" + b"1" * MIN_KEY_BYTES  # 人工核可(Phase 6 增量 3)
DEFAULT_TENANT = "t-default"
LIFETIME = 120


def claims(campaign_id="c1", action="update_budget", new_budget=150, expected_version=1,  # noqa: PLR0913 - 每個參數對應一個聲明欄位
           idempotency_key="k1", tenant=DEFAULT_TENANT, iat=None, exp=None, **overrides):
    issued = int(time.time()) if iat is None else iat
    body = {
        "v": "c1", "tenant": tenant, "campaign_id": campaign_id, "action": action,
        "new_budget": new_budget if action == "update_budget" else None,
        "expected_version": expected_version, "idempotency_key": idempotency_key,
        "policy_version": "demo-pacing-v1", "iat": issued,
        "exp": issued + LIFETIME if exp is None else exp,
    }
    body.update(overrides)
    return body


def header(key=TEST_KEY, **kwargs):
    return {"X-Capability": encode(claims(**kwargs), key)}
