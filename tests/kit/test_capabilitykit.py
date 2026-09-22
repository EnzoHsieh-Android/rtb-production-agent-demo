"""寫入能力憑證的共用格式機制:正規化、簽章、拆解。不知道聲明欄位代表什麼。"""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest

from rtb import capabilitykit
from rtb.capabilitykit import (
    KEY_ENV,
    MAX_TOKEN_CHARS,
    MIN_KEY_BYTES,
    TokenBadSignature,
    TokenMalformed,
    decode,
    encode,
    read_key,
)

KEY = b"k" * MIN_KEY_BYTES
CLAIMS = {"v": "c1", "campaign_id": "c1", "new_budget": 150, "note": None}


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def forge(payload: bytes, key: bytes = KEY) -> str:
    """用同一把金鑰對任意位元組簽章:用來造「簽章正確但內容畸形」的憑證。"""
    return b64(payload) + "." + b64(hmac.new(key, payload, hashlib.sha256).digest())


# ---- 讀金鑰 ----
def test_the_key_is_read_from_the_one_documented_environment_variable():
    assert KEY_ENV == "RTB_CAPABILITY_KEY"
    assert read_key({KEY_ENV: "x" * MIN_KEY_BYTES}) == b"x" * MIN_KEY_BYTES


@pytest.mark.parametrize("environ", [{}, {KEY_ENV: ""}, {KEY_ENV: "x" * (MIN_KEY_BYTES - 1)}],
                         ids=["missing", "empty", "short"])
def test_a_missing_or_short_key_reads_as_no_key(environ):
    assert read_key(environ) is None


# ---- [S36] 只收 JSON 原生型別,驗簽比對原始位元組 ----
def test_the_codec_refuses_non_json_values_and_verifies_the_raw_bytes():
    for bad in (datetime(2026, 9, 22, tzinfo=UTC), 1.5, True, [1], {"a": 1}):
        with pytest.raises(TypeError):
            encode({"v": "c1", "x": bad}, KEY)

    token = encode(CLAIMS, KEY)
    payload_b64, sig_b64 = token.split(".")
    raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
    assert raw == json.dumps(CLAIMS, sort_keys=True, separators=(",", ":")).encode()
    # 語意相同、位元組不同(多了空白)的內容,即使簽章是對的,也要以原始位元組為準
    spaced = json.dumps(CLAIMS, sort_keys=True).encode()
    assert decode(forge(spaced), KEY) == CLAIMS  # 自己的簽章對自己的位元組:通過
    with pytest.raises(TokenBadSignature):
        decode(b64(spaced) + "." + sig_b64, KEY)  # 別人的簽章配上語意相同的位元組:不通過


def test_a_round_trip_returns_the_same_claims():
    assert decode(encode(CLAIMS, KEY), KEY) == CLAIMS


def test_encoding_refuses_an_unusable_key():
    with pytest.raises(ValueError):
        encode(CLAIMS, b"short")


# ---- [S22] 格式與簽章 ----
@pytest.mark.parametrize("token", [
    "", "only-one-segment", "a.b.c", "a.", ".b", "ab=.cd", "a b.cd", "你好.cd",
    "x" * (MAX_TOKEN_CHARS + 1), None, 5,
])
def test_malformed_tokens_are_refused_as_malformed(token):
    with pytest.raises(TokenMalformed):
        decode(token, KEY)


def test_a_token_signed_with_another_key_is_refused():
    with pytest.raises(TokenBadSignature):
        decode(encode(CLAIMS, b"o" * MIN_KEY_BYTES), KEY)


def test_a_tampered_payload_is_refused():
    token = encode(CLAIMS, KEY)
    other = b64(json.dumps({**CLAIMS, "new_budget": 999}, sort_keys=True,
                           separators=(",", ":")).encode())
    with pytest.raises(TokenBadSignature):
        decode(other + "." + token.split(".")[1], KEY)


@pytest.mark.parametrize("payload", [
    b'{"v":"c1","v":"c2"}',  # 重複的鍵
    b"[1,2]",  # 不是物件
    b"not json",
    b'{"v":NaN}',
    b'{"v":true}',  # 布林不是允許的型別
    b'{"v":1.5}',
    b'{"v":{"a":1}}',
    b"\xff\xfe",
])
def test_a_correctly_signed_but_malformed_payload_is_refused(payload):
    with pytest.raises(TokenMalformed):
        decode(forge(payload), KEY)


def test_the_signature_comparison_is_constant_time():
    import inspect

    assert "compare_digest" in inspect.getsource(capabilitykit)
