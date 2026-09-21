"""Mock DSP 的 HTTP 介面(獨立行程)。

故障注入只在啟動時帶 --fault-injection 才接受 X-Fault 標頭;旗標關閉時收到標頭一律回 400,
且不改任何狀態。每種故障都由處理程式「決定」提交或不提交,不靠睡眠長短與逾時賽跑。
只綁定本機回送位址,且只接受 Host 標頭指向回送位址的請求(防 DNS rebinding)。
任何沒預期到的例外都回 500 JSON,絕不無聲切斷連線:呼叫端看到斷線,就分不出
「永久錯誤」與「提交前逾時」。
"""

import argparse
import json
import re
import sys
import time
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from rtb.dsp.errors import (
    CampaignNotFound,
    DspError,
    IdempotencyConflict,
    MetricsNotFound,
    StoreBusy,
    TransientError,
    UnknownAction,
    ValidationRejected,
    VersionConflict,
)
from rtb.dsp.store import BUSY_TIMEOUT_SECONDS, CampaignStore, Operation

LOOPBACK = "127.0.0.1"
MAX_BODY_BYTES = 64 * 1024
SOCKET_TIMEOUT_SECONDS = 10.0
MAX_LENGTH_DIGITS = 10  # Content-Length 位數上限;更長的必然超過本文上限
LISTEN_BACKLOG = 128
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


class RequestRejected(Exception):
    """請求本身有問題,直接對應成一個 HTTP 錯誤回應。"""

    def __init__(self, status: int, code: str, retryable: bool = False):
        super().__init__(code)
        self.status, self.code, self.retryable = status, code, retryable


class _NoResponse(Exception):
    """內部用:故障注入要求不回應。"""


class DspHandler(BaseHTTPRequestHandler):
    server: "DspServer"

    def setup(self) -> None:
        self.timeout = self.server.socket_timeout_seconds  # 讀不到請求就放棄,不佔住執行緒
        super().setup()

    def log_message(self, *_args) -> None:  # 不把每個請求印到終端
        pass

    def send_error(self, code, message=None, explain=None) -> None:
        self._reply_error(code, "http_error", False)  # 基底類別的錯誤頁也用 JSON

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        store = None
        try:
            self._check_host()
            fault = self._read_fault()
            handler, campaign_or_key = self._route(method)
            store = CampaignStore(self.server.db_path,
                                  busy_timeout_seconds=self.server.busy_timeout_seconds)
            self._reply(200, getattr(self, "_" + handler)(store, campaign_or_key, fault))
        except RequestRejected as exc:
            self._reply_error(exc.status, exc.code, exc.retryable)
        except DspError as exc:
            self._reply_error(*error_entry(exc))
        except _NoResponse:
            return  # 故障注入:刻意不回應
        except Exception:  # 沒預期到的例外:記下來,並回型別化的 500
            traceback.print_exc(file=sys.stderr)
            self._reply_error(500, "internal_error", False)
        finally:
            if store is not None:
                store.close()

    def _check_host(self) -> None:
        port = self.server.server_address[1]
        host = self.headers.get("Host")
        if host is None and self.request_version == "HTTP/1.0":
            return  # 舊式探針可能不送 Host;瀏覽器(DNS rebinding 的來源)一定會送
        if host is None or host.lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise RequestRejected(400, "invalid_host")

    def _single_header(self, name: str) -> str | None:
        values = self.headers.get_all(name) or []
        if len(values) > 1:
            raise RequestRejected(400, "duplicate_header")
        return values[0] if values else None

    def _read_fault(self) -> str | None:
        mode = self._single_header("X-Fault")
        if mode is None:
            return None
        if not self.server.fault_injection:
            raise RequestRejected(400, "fault_injection_disabled")
        if mode not in FAULT_MODES:
            raise RequestRejected(400, "unknown_fault_mode")
        return mode

    def _route(self, method: str):
        for route_method, pattern, name in ROUTES:
            match = pattern.match(urlsplit(self.path).path)
            if route_method == method and match:
                return name, match.group(1)
        raise RequestRejected(404, "not_found")

    def _read_json(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise RequestRejected(411, "chunked_not_supported")
        raw_length = (self.headers.get("Content-Length") or "0").strip()
        if not raw_length.isascii() or not raw_length.isdigit():
            raise RequestRejected(400, "invalid_content_length")
        if len(raw_length) > MAX_LENGTH_DIGITS or int(raw_length) > MAX_BODY_BYTES:
            raise RequestRejected(413, "body_too_large")
        raw = self._read_exactly(int(raw_length))
        try:
            body = json.loads(raw or b"{}")
        except ValueError as exc:  # 含 JSONDecodeError、超長數字、非 UTF-8
            raise RequestRejected(400, "invalid_json") from exc
        if not isinstance(body, dict):
            raise RequestRejected(400, "invalid_json")
        return body

    def _read_exactly(self, length: int) -> bytes:
        try:
            raw = self.rfile.read(length)
        except TimeoutError as exc:  # 客戶端宣告了長度卻遲遲不送完
            raise RequestRejected(408, "request_timeout") from exc
        if len(raw) < length:
            raise RequestRejected(400, "incomplete_body")
        return raw

    # ---- 讀取介面 ----
    def _get_campaign(self, store, campaign_id, _fault):
        return asdict(store.get_campaign(campaign_id))

    def _get_history(self, store, campaign_id, _fault):
        store.get_campaign(campaign_id)  # 不存在就回 404
        return {"history": [asdict(h) for h in store.history(campaign_id)]}

    def _get_metrics(self, store, campaign_id, _fault):
        windows = parse_qs(urlsplit(self.path).query).get("window", [])
        if len(windows) > 1:
            raise RequestRejected(400, "duplicate_query_parameter")
        return asdict(store.get_metrics(campaign_id, windows[0] if windows else None))

    def _get_operation(self, store, key, _fault):
        result = store.get_operation_by_key(key)
        if result is None:
            raise RequestRejected(404, "operation_not_found")
        return asdict(result)

    # ---- 寫入介面 ----
    def _update_budget(self, store, campaign_id, fault):
        body = self._read_json()
        params = {"new_budget": body.get("new_budget")}
        op = self._operation(campaign_id, "update_budget", params, body)
        return self._write(store, op, fault)

    def _pause_campaign(self, store, campaign_id, fault):
        body = self._read_json()
        return self._write(store, self._operation(campaign_id, "pause_campaign", {}, body), fault)

    def _operation(self, campaign_id: str, action: str, params: dict, body: dict) -> Operation:
        key = self._single_header("Idempotency-Key")
        if not key:
            raise RequestRejected(400, "missing_idempotency_key")
        return Operation(campaign_id, action, params, body.get("expected_version"), key)

    def _write(self, store: CampaignStore, op: Operation, fault: str | None) -> dict:
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
            raise _NoResponse

    # ---- 回應 ----
    def _reply_error(self, status: int, code: str, retryable: bool) -> None:
        self._reply(status, {"error": code, "retryable": retryable})

    def _reply(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass  # 客戶端已逾時離開,回應寫不出去是正常情況


class DspServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = LISTEN_BACKLOG

    def __init__(self, db_path: Path, fault_injection: bool, hang_seconds: float,
                 delay_seconds: float, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
                 socket_timeout_seconds: float = SOCKET_TIMEOUT_SECONDS):
        super().__init__((LOOPBACK, 0), DspHandler)
        self.db_path, self.fault_injection = db_path, fault_injection
        self.hang_seconds, self.delay_seconds = hang_seconds, delay_seconds
        self.busy_timeout_seconds = busy_timeout_seconds
        self.socket_timeout_seconds = socket_timeout_seconds

    def handle_error(self, request, client_address) -> None:
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            traceback.print_exc(file=sys.stderr)  # 客戶端中途離開不吵;其他例外要看得到


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
                       args.busy_timeout_seconds, args.socket_timeout_seconds)
    print(f"PORT={server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
