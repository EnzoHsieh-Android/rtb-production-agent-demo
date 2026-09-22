"""模擬 DSP 的寫入能力憑證驗證:S22 到 S28、S32、S34、S37、S38。

多數情境在同一個測試行程裡直接建構伺服器(時間可控、金鑰明確給),跨行程的在 test_server.py。
"""

import base64
import hashlib
import hmac
import http.client
import json
import sqlite3
import threading

import pytest

from rtb.capabilitykit import MIN_KEY_BYTES, encode
from rtb.dsp import capability as dsp_capability
from rtb.dsp.server import ERROR_TABLE, ROUTES, DspServer
from rtb.dsp.store import DEFAULT_TENANT, CampaignStore
from tests.capability_samples import TEST_KEY, claims

NOW = 1_800_000_000


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def serve(tmp_path):
    started = []

    def _serve(key=TEST_KEY, clock=None):
        db = tmp_path / "dsp.db"
        if not db.exists():
            store = CampaignStore(db)
            store.seed_campaign("c1", budget=100)
            store.seed_campaign("c2", budget=100, tenant="t-other")
            store.close()
        srv = DspServer(db, fault_injection=False, hang_seconds=0.05, delay_seconds=0.0,
                        capability_key=key, clock=clock or Clock())
        threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
        started.append(srv)
        return srv

    yield _serve
    for srv in started:
        srv.shutdown()
        srv.server_close()


def call(srv, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
    data = body if isinstance(body, bytes) else (None if body is None else json.dumps(body))
    conn.request(method, path, body=data, headers={"Content-Type": "application/json",
                                                    **(headers or {})})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw) if raw else None


def tables(srv):
    conn = sqlite3.connect(srv.db_path)
    try:
        return (conn.execute("SELECT * FROM campaigns ORDER BY id").fetchall(),
                conn.execute("SELECT * FROM operations").fetchall(),
                conn.execute("SELECT * FROM idempotency_keys").fetchall())
    finally:
        conn.close()


def token(key=TEST_KEY, **kwargs):
    kwargs.setdefault("iat", NOW)
    return encode(claims(**kwargs), key)


def budget(srv, new_budget=150, version=1, idem="k1", cap=None, extra=None):
    headers = {"Idempotency-Key": idem}
    if cap is not None:
        headers["X-Capability"] = cap
    return call(srv, "POST", "/campaigns/c1/budget",
                {"new_budget": new_budget, "expected_version": version, **(extra or {})},
                headers)


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def signed(payload: bytes, key=TEST_KEY):
    return b64(payload) + "." + b64(hmac.new(key, payload, hashlib.sha256).digest())


# ---- [S25] 完全一致就執行 ----
def test_a_request_that_matches_its_capability_exactly_is_applied(serve):
    srv = serve()

    assert budget(srv, cap=token())[0] == 200
    status, body = call(srv, "POST", "/campaigns/c1/pause", {"expected_version": 2},
                        {"Idempotency-Key": "p1", "X-Capability": token(
                            action="pause_campaign", expected_version=2,
                            idempotency_key="p1")})
    assert status == 200 and body["version_after"] == 3


# ---- [S22] ----
@pytest.mark.parametrize("cap,code", [
    (None, "capability_missing"),
    ("", "capability_invalid"),
    ("x" * 3000, "capability_invalid"),
    ("a.b.c", "capability_invalid"),
    ("!!.??", "capability_invalid"),
    (signed(b'{"v":"c1","v":"c1"}'), "capability_invalid"),  # 重複的鍵
])
def test_a_write_without_a_valid_capability_is_refused_and_changes_nothing(serve, cap, code):
    srv = serve()
    before = tables(srv)

    status, body = budget(srv, cap=cap)

    assert (status, body["error"]) == (401, code)
    assert tables(srv) == before


def test_a_capability_signed_with_another_key_is_refused(serve):
    srv = serve()
    before = tables(srv)

    status, body = budget(srv, cap=token(key=b"z" * MIN_KEY_BYTES))

    assert (status, body["error"]) == (401, "capability_invalid")
    assert tables(srv) == before


# ---- [S23] ----
@pytest.mark.parametrize("iat,exp", [
    (NOW - 200, NOW - 1),  # 已過期
    (NOW - 10, NOW),  # 到期等於現在:不算還有效
    (NOW, NOW),  # 到期不晚於簽發
    (NOW + 20, NOW + 10),  # 到期早於簽發
    (NOW, NOW + dsp_capability.MAX_LIFETIME_SECONDS + 1),  # 有效期超過上限
    (NOW + dsp_capability.MAX_SKEW_SECONDS + 1, NOW + 100),  # 簽發時間在未來太多
], ids=["expired", "expires-now", "zero-lifetime", "negative-lifetime", "too-long", "future"])
def test_a_capability_outside_its_time_window_is_refused(serve, iat, exp):
    srv = serve()
    before = tables(srv)

    status, body = budget(srv, cap=token(iat=iat, exp=exp))

    assert (status, body["error"]) == (401, "capability_expired")
    assert tables(srv) == before


@pytest.mark.parametrize("iat,exp", [
    (NOW - 5, NOW + 1),
    (NOW, NOW + dsp_capability.MAX_LIFETIME_SECONDS),
    (NOW + dsp_capability.MAX_SKEW_SECONDS, NOW + 60),
], ids=["about-to-expire", "max-lifetime", "max-skew"])
def test_a_capability_at_the_edges_of_its_time_window_is_accepted(serve, iat, exp):
    assert budget(serve(), cap=token(iat=iat, exp=exp))[0] == 200


# ---- [S24] ----
SCOPE_CHANGES = {
    "campaign_id": {"campaign_id": "c2"},
    "action": {"action": "pause_campaign"},
    "idempotency_key": {"idempotency_key": "other"},
    "tenant": {"tenant": "t-other"},
    "new_budget": {"new_budget": 151},
    "expected_version": {"expected_version": 2},
}


def test_each_scope_field_alone_that_does_not_match_the_request_is_refused(serve):
    srv = serve()
    assert set(SCOPE_CHANGES) == set(dsp_capability.SCOPE_FIELDS)  # 從程式自己的清單推導
    before = tables(srv)

    for field, change in SCOPE_CHANGES.items():
        status, body = budget(srv, cap=token(**change))
        assert (status, body["error"]) == (403, "capability_scope_mismatch"), field
    status, body = call(srv, "POST", "/campaigns/ghost/budget",
                        {"new_budget": 150, "expected_version": 1},
                        {"Idempotency-Key": "k1", "X-Capability": token(campaign_id="ghost")})
    assert (status, body["error"]) == (403, "capability_scope_mismatch")  # 廣告不存在也一樣
    assert tables(srv) == before


def test_the_scope_check_compares_each_field_on_its_own():
    """HTTP 上動作與新預算會一起變,這裡直接呼叫比對函式,把六項各自單獨改一項。"""
    base = dsp_capability.Claims(
        tenant="t", campaign_id="c1", action="pause_campaign", new_budget=None,
        expected_version=3, idempotency_key="k1", policy_version="p", iat=NOW, exp=NOW + 60)
    request = dsp_capability.WriteRequest("c1", "pause_campaign", "k1", None, 3)
    dsp_capability.check_scope(base, request, "t")  # 全部一致:通過
    changes = {
        "campaign_id": ({"campaign_id": "c2"}, {}),
        "action": ({}, {"action": "update_budget"}),
        "idempotency_key": ({"idempotency_key": "k2"}, {}),
        "tenant": ({"tenant": "u"}, {}),
        "new_budget": ({}, {"new_budget": 5}),
        "expected_version": ({"expected_version": 4}, {}),
    }
    assert set(changes) == set(dsp_capability.SCOPE_FIELDS)
    from dataclasses import replace

    for claim_change, request_change in changes.values():
        with pytest.raises(dsp_capability.CapabilityScopeMismatch):
            dsp_capability.check_scope(replace(base, **claim_change),
                                       replace(request, **request_change), "t")
    with pytest.raises(dsp_capability.CapabilityScopeMismatch):
        dsp_capability.check_scope(base, request, None)  # 廣告不存在


# ---- [S26] ----
def test_a_replay_also_needs_a_valid_capability_and_does_not_apply_twice(serve):
    srv = serve()
    first = budget(srv, cap=token())
    assert first[0] == 200
    after_first = tables(srv)

    assert budget(srv, cap=None)[0] == 401
    assert budget(srv, cap=token(key=b"z" * MIN_KEY_BYTES))[0] == 401
    # 重簽:帶原始預期版本(1),即使 DSP 現在已經是版本 2
    replay = budget(srv, cap=token(iat=NOW + 1))

    assert replay[0] == 200 and replay[1]["replayed"] is True
    assert replay[1]["operation_id"] == first[1]["operation_id"]
    assert tables(srv) == after_first


# ---- [S27] ----
@pytest.mark.parametrize("key", [None, b"", b"s" * (MIN_KEY_BYTES - 1)],
                         ids=["none", "empty", "short"])
def test_without_a_usable_key_the_dsp_refuses_every_write(serve, key):
    srv = serve(key=key)
    before = tables(srv)

    status, body = budget(srv, cap=token())
    assert (status, body["error"]) == (503, "capability_not_configured")
    status, body = call(srv, "POST", "/campaigns/c1/pause", {"expected_version": 1},
                        {"Idempotency-Key": "p1"})
    assert (status, body["error"]) == (503, "capability_not_configured")
    assert tables(srv) == before
    assert call(srv, "GET", "/campaigns/c1")[0] == 200  # 讀取不受影響


# ---- [S28] ----
def _wrong_but_json_native(field):
    if field in ("expected_version", "iat", "exp"):
        return [("text", "3"), ("null", None)]
    if field == "new_budget":
        return [("text", "150"), ("zero", 0)]
    return [("int", 5), ("empty", ""), ("null", None)]


def _claim_cases():
    base = claims(iat=NOW)
    cases = {}
    for field in dsp_capability.CLAIM_FIELDS:  # 從程式自己的欄位清單推導
        missing = dict(base)
        del missing[field]
        cases[f"missing-{field}"] = missing
        # 共用層只收字串、整數、空值:用它放行、但 DSP 自己的型別檢查該擋的值,才測得到 DSP
        for label, value in _wrong_but_json_native(field):
            cases[f"wrong-type-{field}-{label}"] = {**base, field: value}
    cases["extra-field"] = {**base, "scope": "all"}
    cases["unknown-version"] = {**base, "v": "c2"}
    cases["budget-action-without-amount"] = {**base, "new_budget": None}
    cases["pause-with-amount"] = {**base, "action": "pause_campaign", "new_budget": 5}
    cases["bool-version"] = {**base, "expected_version": True}
    return cases


def _raw_claims_token(body):
    """繞過 encode 的型別限制(例如塞串列),直接簽一份畸形聲明。"""
    return signed(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


@pytest.mark.parametrize("name", sorted(_claim_cases()))
def test_claims_with_missing_extra_or_mistyped_fields_are_refused_without_a_500(serve, name):
    srv = serve()
    before = tables(srv)

    status, body = budget(srv, cap=_raw_claims_token(_claim_cases()[name]))

    assert (status, body["error"]) == (401, "capability_invalid"), name
    assert tables(srv) == before


def test_without_a_key_duplicate_capability_headers_still_answer_not_configured(serve):
    """金鑰已設定是第一關:沒金鑰時,標頭重複也要回 capability_not_configured。"""
    import socket

    srv = serve(key=None)
    body = b'{"new_budget": 150, "expected_version": 1}'
    with socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=3) as conn:
        port = srv.server_address[1]
        head = f"POST /campaigns/c1/budget HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n".encode()
        conn.sendall(head + b"Idempotency-Key: k1\r\nX-Capability: a\r\nX-Capability: b\r\n"
                     b"Content-Type: application/json\r\n"
                     + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        reply = b""
        while chunk := conn.recv(4096):
            reply += chunk
    assert b" 503 " in reply.split(b"\r\n")[0] and b"capability_not_configured" in reply


def test_the_policy_version_of_an_applied_write_is_recorded(serve):
    """政策版本 DSP 只記不驗:套用成功的操作要留下實際收到的政策版本,供稽核。"""
    srv = serve()
    assert budget(srv, cap=token(policy_version="audit-v99"))[0] == 200
    conn = sqlite3.connect(srv.db_path)
    try:
        assert conn.execute("SELECT policy_version FROM operations").fetchall() == [("audit-v99",)]
    finally:
        conn.close()


def test_the_dsp_capability_module_keeps_no_second_integer_check():
    """DSP 內「整數但不是布林」只有儲存層一份:驗證模組用的就是同一個函式物件,
    原始碼裡也不自己判斷布林。"""
    import inspect

    from rtb.dsp import store

    assert dsp_capability.is_plain_int is store.is_plain_int
    assert "bool)" not in inspect.getsource(dsp_capability)  # 沒有自己寫 isinstance(..., bool)


def test_the_key_is_checked_before_the_clock_is_read(serve):
    """金鑰已設定是第一關:沒有金鑰時連時鐘都不讀,時鐘出錯也蓋不掉「沒有可用金鑰」。"""
    def broken_clock():
        raise RuntimeError("clock unavailable")

    status, body = budget(serve(key=None, clock=broken_clock), cap=token())
    assert (status, body["error"]) == (503, "capability_not_configured")


@pytest.mark.parametrize("cap,code", [
    (None, "capability_missing"),
    ("a.b.c", "capability_invalid"),
    ("BAD-CLAIMS", "capability_invalid"),
], ids=["no-header", "malformed", "bad-claims"])
def test_every_earlier_check_answers_before_the_clock_is_read(serve, cap, code):
    """時鐘是最後一關:有金鑰時,標頭、格式、聲明的問題都要在讀時鐘之前回報。"""
    reads = []

    def broken_clock():
        reads.append(1)
        raise RuntimeError("clock unavailable")

    if cap == "BAD-CLAIMS":
        cap = _raw_claims_token({**claims(iat=NOW), "v": "c9"})
    status, body = budget(serve(clock=broken_clock), cap=cap)

    assert (status, body["error"]) == (401, code)
    assert reads == []


@pytest.mark.parametrize("policy", ["", "x" * 65, "a b", "政策", "v1\n"])
def test_a_policy_version_outside_the_dsp_format_is_invalid(serve, policy):
    srv = serve()
    before = tables(srv)

    status, body = budget(srv, cap=_raw_claims_token(claims(iat=NOW, policy_version=policy)))

    assert (status, body["error"]) == (401, "capability_invalid")
    assert tables(srv) == before


# ---- [S32] ----
def test_every_capability_rejection_has_its_status_and_echoes_nothing(serve):
    documented = {
        "capability_not_configured": 503, "capability_missing": 401,
        "capability_invalid": 401, "capability_expired": 401,
        "capability_scope_mismatch": 403,
    }
    from_table = {code: (status, retryable) for status, code, retryable in ERROR_TABLE.values()
                  if code.startswith("capability_")}
    assert {code: status for code, (status, _r) in from_table.items()} == documented
    assert not any(retryable for _s, retryable in from_table.values())

    srv, off = serve(), serve(key=None)
    secret_bits = ["t-default", "demo-pacing-v1", "c1"]
    tok = token(tenant="t-other")
    for status, body in (budget(off, cap=tok), budget(srv), budget(srv, cap="a.b.c"),
                         budget(srv, cap=token(iat=NOW - 500, exp=NOW - 1)),
                         budget(srv, cap=tok)):
        assert status in documented.values()
        assert set(body) == {"error", "retryable"}
        text = json.dumps(body)
        assert tok not in text and not any(bit in text for bit in secret_bits)


# ---- [S34] ----
def test_an_old_dsp_database_gains_the_tenant_column_without_losing_data(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE campaigns (id TEXT PRIMARY KEY, budget INTEGER NOT NULL, "
        "status TEXT NOT NULL, version INTEGER NOT NULL);"
        "INSERT INTO campaigns VALUES ('old', 77, 'active', 4);"
        "CREATE TABLE operations (operation_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "campaign_id TEXT NOT NULL, action TEXT NOT NULL, params_json TEXT NOT NULL, "
        "version_after INTEGER NOT NULL, received_at TEXT NOT NULL, committed_at TEXT NOT NULL, "
        "idempotency_key TEXT NOT NULL UNIQUE);"
        "INSERT INTO operations (campaign_id, action, params_json, version_after, received_at, "
        "committed_at, idempotency_key) VALUES ('old', 'pause_campaign', '{}', 4, 't', 't', 'o1');")
    conn.close()

    store = CampaignStore(db)
    try:
        assert store.tenant_of("old") == DEFAULT_TENANT
        campaign = store.get_campaign("old")
        assert (campaign.budget, campaign.status, campaign.version) == (77, "active", 4)
        assert store.get_operation_by_key("o1") is not None  # 舊操作還在
    finally:
        store.close()
    raw = sqlite3.connect(db)
    try:  # 舊操作沒有紀錄政策版本:誠實留空值
        assert raw.execute("SELECT policy_version FROM operations").fetchall() == [(None,)]
    finally:
        raw.close()
    reopened = CampaignStore(db)  # 第二次開啟不重複加欄位
    reopened.close()


# ---- [S37] ----
@pytest.mark.parametrize("path,body,cap_kwargs", [
    ("/campaigns/c1/budget", {"new_budget": 150, "expected_version": 1, "tenant": "x"}, {}),
    ("/campaigns/c1/pause", {"expected_version": 1, "note": "x"},
     {"action": "pause_campaign"}),
])
def test_a_write_body_with_an_unexpected_field_is_refused(serve, path, body, cap_kwargs):
    srv = serve()
    before = tables(srv)

    status, _ = call(srv, "POST", path, body,
                     {"Idempotency-Key": "k1", "X-Capability": token(**cap_kwargs)})

    assert status == 400
    assert tables(srv) == before


# ---- [S38] ----
def test_no_dsp_write_endpoint_can_change_a_campaigns_tenant(serve):
    srv = serve()
    writes = [name for method, _pattern, name in ROUTES if method == "POST"]
    assert sorted(writes) == ["pause_campaign", "update_budget"]  # 新增寫入端點就要回來補

    assert budget(srv, cap=token())[0] == 200
    assert call(srv, "POST", "/campaigns/c1/pause", {"expected_version": 2},
                {"Idempotency-Key": "p1", "X-Capability": token(
                    action="pause_campaign", expected_version=2, idempotency_key="p1")})[0] == 200
    store = CampaignStore(srv.db_path)
    try:
        assert store.tenant_of("c1") == DEFAULT_TENANT
    finally:
        store.close()
    assert "tenant" not in dsp_capability.BODY_FIELDS["update_budget"]
    assert "tenant" not in dsp_capability.BODY_FIELDS["pause_campaign"]


def test_a_malformed_body_under_a_valid_capability_keeps_its_old_error(serve):
    status, body = call(serve(), "POST", "/campaigns/c1/budget", b"not json",
                        {"Idempotency-Key": "k1", "X-Capability": token()})
    assert (status, body["error"]) == (400, "invalid_json")
