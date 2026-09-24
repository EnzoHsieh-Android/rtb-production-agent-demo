"""一次展示的三把金鑰(能力、稽核、核可),Phase 12 設計審 r3 m1。

環境變數只裝得下文字,子行程照共用讀法 `read_key` 把文字轉回 UTF-8 位元組;所以金鑰一開始就是
`secrets.token_urlsafe` 的文字,伺服器行程內簽發也用同一條轉法(`signing_bytes`),兩邊的值才相同。
用位元組金鑰的話,含 NUL 的傳不進環境、不是合法 UTF-8 的讀不回來(r3 實測 2000 把全部失敗)。
"""

import secrets
from dataclasses import dataclass

from rtb.capabilitykit import APPROVAL_KEY_ENV, AUDIT_KEY_ENV, KEY_ENV, MIN_KEY_BYTES

_TOKEN_BYTES = MIN_KEY_BYTES  # token_urlsafe(32) 是 43 個 ASCII 字元,UTF-8 長度 43 ≥ 32


@dataclass(frozen=True, repr=False)
class DemoKeys:
    capability: str
    audit: str
    approval: str

    @classmethod
    def generate(cls) -> DemoKeys:
        return cls(*(secrets.token_urlsafe(_TOKEN_BYTES) for _ in range(3)))

    def text(self, env_name: str) -> str:
        """這把金鑰放進子行程環境的文字。"""
        return {KEY_ENV: self.capability, AUDIT_KEY_ENV: self.audit,
                APPROVAL_KEY_ENV: self.approval}[env_name]

    def signing_bytes(self, env_name: str) -> bytes:
        """伺服器行程內簽發用的位元組:跟子行程 `read_key` 讀回的一樣。"""
        return self.text(env_name).encode("utf-8")

    def __repr__(self) -> str:
        return "DemoKeys(<三把展示金鑰,不印出>)"
