"""寫入能力憑證的格式機制(模擬 DSP 與執行行程共用):正規化、簽章、拆解。

這支模組只管「怎麼做」,不知道聲明裡有哪些欄位、代表什麼;欄位與範圍在模擬 DSP
(驗證)與執行行程(簽發)各自定義,由跨行程整合測試綁住。

格式:聲明正規化成固定鍵序、無多餘空白的 JSON,與 HMAC-SHA256 簽章各自做 base64url
(不留填充),中間用點連接。驗簽比對的是拆出來的原始聲明位元組,不是解析後重新正規化的
結果;簽章通過之後才解析 JSON,重複的鍵視為格式不對。

金鑰從環境變數讀,而讀取函式只給啟動程式呼叫;伺服器物件與簽發器都把金鑰當參數收。
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

KEY_ENV = "RTB_CAPABILITY_KEY"  # 程式裡唯一出現這個名稱的地方(有測試擋)
# 人工核可金鑰(Phase 6 增量 3):跟簽發金鑰分開,管理工具簽核可、執行迴圈驗核可;格式機制共用
APPROVAL_KEY_ENV = "RTB_APPROVAL_KEY"  # 同上,程式裡唯一出現這個名稱的地方
# 唯讀稽核金鑰(Phase 9 增量 3 代碼審,代使用者裁定):DSP 列操作兩支端點回全租戶明細,只給持有它的
# 維運套件讀。跟簽發金鑰分開:維運套件拿不到能簽寫入憑證的那一把。不走簽章,DSP 用固定時間比對
AUDIT_KEY_ENV = "RTB_DSP_AUDIT_KEY"  # 同上,程式裡唯一出現這個名稱的地方
MIN_KEY_BYTES = 32  # 空字串或很短的金鑰也算得出簽章,但等於沒有防線
MAX_TOKEN_CHARS = 2048
HEADER = "X-Capability"
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")

ClaimValue = str | int | None


class TokenRejected(Exception):
    """憑證拆不開或簽章不對;具體原因只給本地判斷用,不回給呼叫端。"""


class TokenMalformed(TokenRejected):
    """長度、段數、編碼或聲明內容的格式不對。"""


class TokenBadSignature(TokenRejected):
    """簽章與聲明位元組對不上。"""


def is_usable_key(key: object) -> bool:
    return isinstance(key, bytes) and len(key) >= MIN_KEY_BYTES


def read_key(environ: Mapping[str, str], name: str = KEY_ENV) -> bytes | None:
    """從環境讀金鑰(預設讀簽發金鑰);沒有或太短一律回 None(代表沒有可用金鑰)。只給啟動程式呼叫。"""
    raw = environ.get(name)
    if raw is None:
        return None
    key = raw.encode("utf-8")
    return key if is_usable_key(key) else None


def _is_claim_value(value: object) -> bool:
    if value is None or isinstance(value, str):
        return True
    return isinstance(value, int) and not isinstance(value, bool)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(segment: str) -> bytes:
    if not _SEGMENT.fullmatch(segment):
        raise TokenMalformed("編碼段只准網址安全的 Base64 字元,不准填充")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenMalformed("編碼段解不開") from exc


def _signature(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def encode(claims: Mapping[str, ClaimValue], key: bytes) -> str:
    if not is_usable_key(key):
        raise ValueError("金鑰不可用:沒有或短於最短長度")
    for name, value in claims.items():
        if not isinstance(name, str) or not _is_claim_value(value):
            raise TypeError(f"聲明只收字串鍵與字串、整數、空值:{name!r}")
    payload = json.dumps(dict(claims), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return _b64encode(payload) + "." + _b64encode(_signature(key, payload))


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise TokenMalformed("聲明有重複的鍵")
        result[name] = value
    return result


def _refuse_constant(name: str) -> None:
    raise TokenMalformed(f"聲明不准 {name}")


def decode(token: object, key: bytes) -> dict[str, ClaimValue]:
    """拆解並驗簽,回聲明字典;任何問題都丟 TokenRejected 的子類別。"""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
        raise TokenMalformed("憑證不是字串、是空的或太長")
    parts = token.split(".")
    if len(parts) != 2:  # 固定兩段:聲明與簽章
        raise TokenMalformed("憑證必須恰好兩段")
    payload, signature = _b64decode(parts[0]), _b64decode(parts[1])
    if not hmac.compare_digest(_signature(key, payload), signature):  # 常數時間比較
        raise TokenBadSignature("簽章不對")
    try:
        claims = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                            parse_constant=_refuse_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TokenMalformed("聲明不是 JSON") from exc
    if not isinstance(claims, dict) or not all(_is_claim_value(v) for v in claims.values()):
        raise TokenMalformed("聲明必須是只含字串、整數、空值的物件")
    return claims
