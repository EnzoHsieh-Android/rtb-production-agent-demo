"""執行行程簽發寫入能力憑證:依租戶設定檢查範圍,綁確切值與嘗試紀錄存下的預期版本。

簽發器建立時收金鑰參數(由啟動程式經共用模組的讀取函式取得),自己不讀環境變數;
金鑰不可用就建立失敗——之後的執行迴圈必須在啟動時建立它,建立失敗就拒絕啟動。
租戶設定每次簽發都重讀,調降上限立刻生效。設定檔先開目錄、再相對目錄以不跟隨符號連結
的方式開檔,擁有者與寫入權限都對「已經開好的」目錄與檔案檢查,不留檢查後被換掉的空檔。
簽發器帶一份自己的內容快取(F7 效能計劃):安全讀檔的每一道檢查每次照做,讀到的位元組跟上次驗過的完全
相同才沿用上次的解析結果,任何一個位元組不同就重新解析、驗證(不用修改時間判斷)。
這些檢查擋的是權限設錯這類疏忽,擋不住同一個作業系統使用者改檔(已知限制)。

憑證只證明「這筆寫入的確切值在授權範圍內」,不證明這是好的業務決策(那是執行前檢查)。
"""

import json
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtb.capabilitykit import encode, is_usable_key
from rtb.domain._checks import is_id, is_plain_int
from rtb.domain.proposal import MAX_INT, ActionType, Proposal

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
    # 24 小時內加預算總額上限(Phase 6);設定檔缺這欄當 0:這個租戶不准加預算,其他租戶照常
    aggregate_limit: int = 0


@dataclass(frozen=True)
class Grant:
    """簽發結果:憑證與它的到期時間,加上同一次讀設定檔得到的整個租戶(總額上限、租戶名稱、
    人工核可的範圍指紋都從這一個物件取,不另外攤平一份,Phase 6 增量 3)。"""

    token: str
    tenant: Tenant
    expires_at: int  # 秒;有「最晚到期」時取兩者較早的那一個


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
        # 缺欄當 0(只擋這個租戶,不讓整份設定檔停機);有寫但值不對是打錯字,不猜,整份不合法
        limit = spec.get("aggregate_limit", 0)
        if not is_plain_int(limit) or not 0 <= limit <= MAX_INT:
            raise SigningRefused("config_invalid")
        result.append(Tenant(name, frozenset(campaigns), cap, limit))
    owners = [c for t in result for c in t.campaigns]
    if len(owners) != len(set(owners)):  # 一個廣告只能屬於一個租戶
        raise SigningRefused("config_invalid")
    return tuple(result)


def load_tenants(path: Path) -> tuple[Tenant, ...]:
    return _parse_tenants(_read_config_securely(Path(path)))


def tenant_for(tenants: tuple[Tenant, ...], campaign_id: str) -> Tenant | None:
    """這個廣告屬於哪個租戶(一個廣告只屬於一個租戶,解析時已驗);不屬於任何租戶回 None。
    簽發、處理待核可、核可管理工具三處共用,不各寫一份(Phase 6 增量 3 代碼審)。"""
    return next((t for t in tenants if campaign_id in t.campaigns), None)


class CapabilitySigner:
    def __init__(self, key: bytes | None):
        if not is_usable_key(key):
            raise ValueError("金鑰不可用:沒有或短於最短長度")
        assert key is not None  # noqa: S101
        self._key = key
        # 讀租戶設定的內容快取:只記一份(路徑, 上次驗過的位元組, 解析結果);在自己的鎖裡讀、比、換。
        # 放在簽發器上、不放模組層級(狀態由持有者顯式持有;不同簽發器各有各的)
        self._config_lock = threading.Lock()
        self._config_cache: tuple[str, bytes, tuple[Tenant, ...]] | None = None

    def read_tenants(self, config_path: Path) -> tuple[Tenant, ...]:
        """讀租戶設定(簽發、作廢簽發、處理待核可都走這裡)。每次照舊做安全讀檔的每一道檢查;讀到的
        位元組跟快取裡上次驗過的完全相同才回上次的解析結果,不同就解析、驗證,驗過才換進快取;驗不過
        照舊拒絕、快取不動。解析結果不可變(凍結的資料類別與不可變集合),回給多個執行緒共用安全。"""
        key = os.path.abspath(config_path)  # 鍵只是識別用:補成絕對路徑、不解析符號連結
        with self._config_lock:
            data = _read_config_securely(Path(config_path))
            cached = self._config_cache
            if cached is not None and cached[0] == key and cached[1] == data:
                return cached[2]
            tenants = _parse_tenants(data)
            self._config_cache = (key, data, tenants)
            return tenants

    def sign(self, proposal: Proposal, operation_key: str, config_path: Path, now: int) -> str:
        """proposal 是嘗試紀錄存下的提案快照;預期版本取快照裡觀察到的版本,不取 DSP 現況。"""
        return self.grant(proposal, operation_key, config_path, now).token

    def grant(
        self, proposal: Proposal, operation_key: str, config_path: Path, now: int,
        not_after: int | None = None,
    ) -> Grant:
        """同 sign,另外帶回這次讀到的租戶:門檻跟單一廣告上限同一個生效語意(簽發時讀,改設定從
        下一次簽發起生效),開始一筆時不在寫入鎖裡再讀設定檔。

        not_after 是「最晚到期」(Phase 6 增量 3):用到人工核可的那一筆,憑證不能活得比核可久。"""
        tenant = self._tenant_of(proposal, config_path)
        new_budget = None
        if proposal.action_type is ActionType.UPDATE_BUDGET:
            new_budget = proposal.requested_change["new_budget"]
            if new_budget > tenant.max_budget:
                raise SigningRefused("over_budget_cap")
        expires = now + LIFETIME_SECONDS if not_after is None else min(
            now + LIFETIME_SECONDS, not_after)
        token = self._encode(proposal, operation_key, tenant, proposal.action_type.value,
                             new_budget, now, expires)
        return Grant(token, tenant, expires)

    def sign_void(
        self, proposal: Proposal, operation_key: str, config_path: Path, now: int,
    ) -> str:
        """簽「作廢這把鍵」:只檢查廣告屬於允許的租戶,不檢查預算上限——作廢是撤掉一個寫入,
        不是在寫。設定檔壞掉或不安全照舊丟 SigningRefused(呼叫端當系統故障)。"""
        tenant = self._tenant_of(proposal, config_path)
        return self._encode(proposal, operation_key, tenant, VOID_ACTION, None, now,
                            now + LIFETIME_SECONDS)

    def _tenant_of(self, proposal: Proposal, config_path: Path) -> Tenant:
        tenant = tenant_for(self.read_tenants(config_path), proposal.campaign_id)
        if tenant is None:
            raise SigningRefused("campaign_not_allowed")
        return tenant

    def _encode(  # noqa: PLR0913 - 每個參數都是聲明的一個欄位
        self, proposal: Proposal, operation_key: str, tenant: Tenant, action: str,
        new_budget: int | None, now: int, expires: int,
    ) -> str:
        claims = {
            "v": FORMAT_VERSION, "tenant": tenant.name, "campaign_id": proposal.campaign_id,
            "action": action, "new_budget": new_budget,
            "expected_version": proposal.campaign_version_observed,
            "idempotency_key": operation_key, "policy_version": proposal.policy_version,
            "iat": now, "exp": expires,
        }
        return encode(claims, self._key)
