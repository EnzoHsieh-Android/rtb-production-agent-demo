"""Mock DSP 的 HTTP 介面(獨立行程)。

故障注入只在啟動時帶 --fault-injection 才接受 X-Fault 標頭;旗標關閉時收到標頭一律回 400,
且不改任何狀態。每種故障都由處理程式「決定」提交或不提交,不靠睡眠長短與逾時賽跑。
只綁定本機回送位址,且只接受 Host 標頭指向回送位址的請求(防 DNS rebinding)。
任何沒預期到的例外都回 500 JSON,絕不無聲切斷連線:呼叫端看到斷線,就分不出
「永久錯誤」與「提交前逾時」。
"""

import argparse
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from rtb.capabilitykit import HEADER as CAPABILITY_HEADER
from rtb.capabilitykit import read_key
from rtb.dsp.capability import (
    WriteRequest,
    check_body_fields,
    check_scope,
    verified_claims,
)
from rtb.dsp.errors import (
    CampaignNotFound,
    CapabilityExpired,
    CapabilityInvalid,
    CapabilityMissing,
    CapabilityNotConfigured,
    CapabilityScopeMismatch,
    DspError,
    IdempotencyConflict,
    MetricsNotFound,
    StoreBusy,
    TransientError,
    UnknownAction,
    ValidationRejected,
    VersionConflict,
)
from rtb.dsp.store import CampaignStore, Operation, validate
from rtb.httpkit import (
    SOCKET_TIMEOUT_SECONDS,
    JsonHandler,
    KitServer,
    NoResponse,
    RequestRejected,
)
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS

FAULT_MODES = frozenset(
    {
        "timeout_before_commit",
        "timeout_after_commit",
        "delayed_response",
        "transient_5xx",
        "permanent_validation_error",
    }
)
# 錯誤型別 -> (HTTP 狀態碼, 錯誤代碼, 可否重試);查表沿著繼承鏈找,子類別也查得到
ERROR_TABLE = {
    StoreBusy: (503, "store_busy", True),
    VersionConflict: (409, "version_conflict", False),
    IdempotencyConflict: (422, "idempotency_conflict", False),
    ValidationRejected: (422, "validation_rejected", False),
    UnknownAction: (400, "unknown_action", False),
    CampaignNotFound: (404, "campaign_not_found", False),
    MetricsNotFound: (404, "metrics_not_found", False),
    CapabilityNotConfigured: (503, "capability_not_configured", False),
    CapabilityMissing: (401, "capability_missing", False),
    CapabilityInvalid: (401, "capability_invalid", False),
    CapabilityExpired: (401, "capability_expired", False),
    CapabilityScopeMismatch: (403, "capability_scope_mismatch", False),
}
ROUTES = [
    ("GET", re.compile(r"^/campaigns/([^/]+)$"), "get_campaign"),
    ("GET", re.compile(r"^/campaigns/([^/]+)/history$"), "get_history"),
    ("GET", re.compile(r"^/campaigns/([^/]+)/metrics$"), "get_metrics"),
    ("GET", re.compile(r"^/operations/([^/]+)$"), "get_operation"),
    ("POST", re.compile(r"^/campaigns/([^/]+)/budget$"), "update_budget"),
    ("POST", re.compile(r"^/campaigns/([^/]+)/pause$"), "pause_campaign"),
]


def error_entry(exc: DspError) -> tuple[int, str, bool]:
    for cls in type(exc).__mro__:
        if cls in ERROR_TABLE:
            return ERROR_TABLE[cls]
    if isinstance(exc, TransientError):
        return 503, "transient_error", True
    return 500, "dsp_error", False


class DspHandler(JsonHandler):
    server: DspServer

    def handle_request(self, method: str) -> tuple[int, dict[str, Any]]:
        fault = self.read_fault(FAULT_MODES)
        handler, campaign_or_key = self._route(method)
        store = CampaignStore(
            self.server.db_path, busy_timeout_seconds=self.server.busy_timeout_seconds
        )
        try:
            return 200, getattr(self, "_" + handler)(store, campaign_or_key, fault)
        finally:
            store.close()

    def map_exception(self, exc: Exception) -> tuple[int, str, bool] | None:
        return error_entry(exc) if isinstance(exc, DspError) else None

    def _route(self, method: str) -> tuple[str, str]:
        for route_method, pattern, name in ROUTES:
            match = pattern.match(urlsplit(self.path).path)
            if route_method == method and match:
                return name, match.group(1)
        raise RequestRejected(404, "not_found")

    # ---- 讀取介面 ----
    def _get_campaign(
        self, store: CampaignStore, campaign_id: str, _fault: str | None
    ) -> dict[str, Any]:
        return asdict(store.get_campaign(campaign_id))

    def _get_history(
        self, store: CampaignStore, campaign_id: str, _fault: str | None
    ) -> dict[str, Any]:
        store.get_campaign(campaign_id)  # 不存在就回 404
        return {"history": [asdict(h) for h in store.history(campaign_id)]}

    def _get_metrics(
        self, store: CampaignStore, campaign_id: str, _fault: str | None
    ) -> dict[str, Any]:
        windows = parse_qs(urlsplit(self.path).query).get("window", [])
        if len(windows) > 1:
            raise RequestRejected(400, "duplicate_query_parameter")
        return asdict(store.get_metrics(campaign_id, windows[0] if windows else None))

    def _get_operation(
        self, store: CampaignStore, key: str, _fault: str | None
    ) -> dict[str, Any]:
        result = store.get_operation_by_key(key)
        if result is None:
            raise RequestRejected(404, "operation_not_found")
        return asdict(result)

    # ---- 寫入介面 ----
    def _update_budget(
        self, store: CampaignStore, campaign_id: str, fault: str | None
    ) -> dict[str, Any]:
        return self._authorized_write(store, campaign_id, "update_budget", fault)

    def _pause_campaign(
        self, store: CampaignStore, campaign_id: str, fault: str | None
    ) -> dict[str, Any]:
        return self._authorized_write(store, campaign_id, "pause_campaign", fault)

    def _authorized_write(
        self, store: CampaignStore, campaign_id: str, action: str, fault: str | None
    ) -> dict[str, Any]:
        """先驗憑證(金鑰、標頭、格式、簽章、聲明、時間),再讀本文、驗操作內容,再比範圍,最後才寫。

        每一步失敗都發生在任何寫入之前:廣告表、操作紀錄、冪等鍵表三者都不動。
        """
        claims = verified_claims(lambda: self.single_header(CAPABILITY_HEADER),
                                 self.server.capability_key, self.server.clock)
        body = self.read_json(allow_empty=True)
        if check_body_fields(action, body):
            raise RequestRejected(400, "unexpected_field")
        params = {"new_budget": body.get("new_budget")} if action == "update_budget" else {}
        op = replace(self._operation(campaign_id, action, params, body),
                     policy_version=claims.policy_version)  # 只記不驗,供稽核
        validate(op)  # 不合法的值照舊回 422,不被當成範圍不符
        check_scope(claims, WriteRequest(campaign_id, action, op.idempotency_key,
                                         params.get("new_budget"), op.expected_version),
                    store.tenant_of(campaign_id))
        return self._write(store, op, fault)

    def _operation(
        self, campaign_id: str, action: str, params: dict[str, Any], body: dict[str, Any]
    ) -> Operation:
        key = self.single_header("Idempotency-Key")
        if not key:
            raise RequestRejected(400, "missing_idempotency_key")
        return Operation(campaign_id, action, params, body.get("expected_version"), key)

    def _write(
        self, store: CampaignStore, op: Operation, fault: str | None
    ) -> dict[str, Any]:
        self._apply_fault_before_commit(fault)
        result = asdict(store.execute(op))
        if fault == "timeout_after_commit":
            time.sleep(self.server.hang_seconds)  # 已提交,但回應遲到
        return result

    def _apply_fault_before_commit(self, fault: str | None) -> None:
        if fault == "delayed_response":
            time.sleep(self.server.delay_seconds)
        elif fault == "transient_5xx":
            raise RequestRejected(503, "injected_transient_error", retryable=True)
        elif fault == "permanent_validation_error":
            raise RequestRejected(422, "injected_validation_error")
        elif fault == "timeout_before_commit":
            time.sleep(self.server.hang_seconds)  # 不論客戶端是否還在,都不提交
            raise NoResponse


class DspServer(KitServer):
    def __init__(self, db_path: Path, fault_injection: bool, hang_seconds: float,  # noqa: PLR0913 - 啟動參數逐一對應命令列
                 delay_seconds: float, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
                 socket_timeout_seconds: float = SOCKET_TIMEOUT_SECONDS,
                 capability_key: bytes | None = None,
                 clock: Callable[[], float] = time.time):
        """capability_key 由啟動程式讀好傳進來;伺服器物件本身不讀環境變數。沒給就拒收所有寫入。"""
        super().__init__(DspHandler, socket_timeout_seconds, fault_injection=fault_injection)
        self.db_path = db_path
        self.capability_key, self.clock = capability_key, clock
        self.hang_seconds, self.delay_seconds = hang_seconds, delay_seconds
        self.busy_timeout_seconds = busy_timeout_seconds


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Mock DSP")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--fault-injection", action="store_true")
    parser.add_argument("--hang-seconds", type=float, default=2.0)
    parser.add_argument("--delay-seconds", type=float, default=0.3)
    parser.add_argument("--busy-timeout-seconds", type=float, default=BUSY_TIMEOUT_SECONDS)
    parser.add_argument("--socket-timeout-seconds", type=float, default=SOCKET_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    CampaignStore(args.db).close()  # 確保資料庫與表已建立
    server = DspServer(args.db, args.fault_injection, args.hang_seconds, args.delay_seconds,
                       args.busy_timeout_seconds, args.socket_timeout_seconds,
                       capability_key=read_key(os.environ))  # 只有啟動程式讀環境變數
    print(f"PORT={server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
