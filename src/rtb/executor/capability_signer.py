"""執行行程簽發寫入能力憑證:依租戶設定檢查範圍,綁確切值與嘗試紀錄存下的預期版本。

簽發器建立時收金鑰參數(由啟動程式經共用模組的讀取函式取得),自己不讀環境變數;
金鑰不可用就建立失敗——之後的執行迴圈必須在啟動時建立它,建立失敗就拒絕啟動。
租戶設定每次簽發都重讀,調降上限立刻生效。設定檔先開目錄、再相對目錄以不跟隨符號連結
的方式開檔,擁有者與寫入權限都對「已經開好的」目錄與檔案檢查,不留檢查後被換掉的空檔。
這些檢查擋的是權限設錯這類疏忽,擋不住同一個作業系統使用者改檔(已知限制)。

憑證只證明「這筆寫入的確切值在授權範圍內」,不證明這是好的業務決策(那是執行前檢查)。
"""

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtb.capabilitykit import encode, is_usable_key
from rtb.domain._checks import is_id, is_plain_int
from rtb.domain.proposal import ActionType, Proposal

FORMAT_VERSION = "c1"
LIFETIME_SECONDS = 120  # 簽發端用的有效期;DSP 端的上限是 300 秒
VOID_ACTION = "void_operation"  # 作廢一把冪等鍵(對帳判失敗之前);DSP 聲明的動作清單有同名成員
MAX_CONFIG_BYTES = 64 * 1024
_UNSAFE_WRITE = stat.S_IWGRP | stat.S_IWOTH


class SigningRefused(Exception):
    """不簽:reason 是固定代碼,只給本地判斷用。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Tenant:
    name: str
    campaigns: frozenset[str]
    max_budget: int


def _check_owner_and_mode(fd: int, what: str) -> None:
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & _UNSAFE_WRITE:
        raise SigningRefused(f"config_{what}_insecure")


def _read_config_securely(path: Path) -> bytes:
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise SigningRefused("config_unreadable") from exc
    try:
        _check_owner_and_mode(dir_fd, "directory")
        try:  # 不等待地開:具名管道這類特殊檔案用一般方式開會一直等下去
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except OSError as exc:  # 符號連結在這裡直接開不起來(ELOOP)
            raise SigningRefused("config_unreadable") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):  # 先確認是一般檔案,才恢復一般讀取
                raise SigningRefused("config_unreadable")
            os.set_blocking(fd, True)
            _check_owner_and_mode(fd, "file")
            data = os.read(fd, MAX_CONFIG_BYTES + 1)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
    if len(data) > MAX_CONFIG_BYTES:
        raise SigningRefused("config_invalid")
    return data


def _parse_tenants(data: bytes) -> tuple[Tenant, ...]:
    try:
        raw: Any = json.loads(data.decode("utf-8"))
    except ValueError as exc:  # 含解碼錯誤、JSON 格式錯誤、整數位數超過上限
        raise SigningRefused("config_invalid") from exc
    tenants = raw.get("tenants") if isinstance(raw, dict) else None
    if not isinstance(tenants, dict):
        raise SigningRefused("config_invalid")
    result = []
    for name, spec in tenants.items():
        campaigns = spec.get("campaigns") if isinstance(spec, dict) else None
        cap = spec.get("max_budget") if isinstance(spec, dict) else None
        if (not is_id(name) or not isinstance(campaigns, list)
                or not all(is_id(c) for c in campaigns) or not is_plain_int(cap) or cap < 1):
            raise SigningRefused("config_invalid")
        result.append(Tenant(name, frozenset(campaigns), cap))
    owners = [c for t in result for c in t.campaigns]
    if len(owners) != len(set(owners)):  # 一個廣告只能屬於一個租戶
        raise SigningRefused("config_invalid")
    return tuple(result)


def load_tenants(path: Path) -> tuple[Tenant, ...]:
    return _parse_tenants(_read_config_securely(Path(path)))


class CapabilitySigner:
    def __init__(self, key: bytes | None):
        if not is_usable_key(key):
            raise ValueError("金鑰不可用:沒有或短於最短長度")
        assert key is not None  # noqa: S101
        self._key = key

    def sign(self, proposal: Proposal, operation_key: str, config_path: Path, now: int) -> str:
        """proposal 是嘗試紀錄存下的提案快照;預期版本取快照裡觀察到的版本,不取 DSP 現況。"""
        tenant = self._tenant_of(proposal, config_path)
        new_budget = None
        if proposal.action_type is ActionType.UPDATE_BUDGET:
            new_budget = proposal.requested_change["new_budget"]
            if new_budget > tenant.max_budget:
                raise SigningRefused("over_budget_cap")
        return self._encode(proposal, operation_key, tenant, proposal.action_type.value,
                            new_budget, now)

    def sign_void(
        self, proposal: Proposal, operation_key: str, config_path: Path, now: int,
    ) -> str:
        """簽「作廢這把鍵」:只檢查廣告屬於允許的租戶,不檢查預算上限——作廢是撤掉一個寫入,
        不是在寫。設定檔壞掉或不安全照舊丟 SigningRefused(呼叫端當系統故障)。"""
        tenant = self._tenant_of(proposal, config_path)
        return self._encode(proposal, operation_key, tenant, VOID_ACTION, None, now)

    @staticmethod
    def _tenant_of(proposal: Proposal, config_path: Path) -> Tenant:
        tenants = load_tenants(config_path)
        tenant = next((t for t in tenants if proposal.campaign_id in t.campaigns), None)
        if tenant is None:
            raise SigningRefused("campaign_not_allowed")
        return tenant

    def _encode(
        self, proposal: Proposal, operation_key: str, tenant: Tenant, action: str,
        new_budget: int | None, now: int,
    ) -> str:
        claims = {
            "v": FORMAT_VERSION, "tenant": tenant.name, "campaign_id": proposal.campaign_id,
            "action": action, "new_budget": new_budget,
            "expected_version": proposal.campaign_version_observed,
            "idempotency_key": operation_key, "policy_version": proposal.policy_version,
            "iat": now, "exp": now + LIFETIME_SECONDS,
        }
        return encode(claims, self._key)
