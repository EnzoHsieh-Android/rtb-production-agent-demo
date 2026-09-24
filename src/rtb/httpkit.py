"""HTTP 服務的共用基礎(DSP 與提案收件口都用它,不各寫一套)。

提供:只綁回送位址的伺服器、Host 檢查(防 DNS rebinding)、有上限的請求本文、
逾時、JSON 錯誤回應,以及「任何沒預期到的例外都回 500 JSON,絕不無聲切斷連線」。
呼叫端看到斷線,就分不出「永久錯誤」與「提交前逾時」。

子類別實作 handle_request(回 (狀態碼, 內容));要把自己的例外對應成錯誤回應就覆寫 map_exception。

Phase 12(一鍵展示)新增、不改既有路徑:handle_request 也可以回一個 `Response`(HTML、樣式表、
303 轉址);處理器宣告 `content_security_policy` 就是 HTML 伺服器,HTML 回應與所有錯誤頁(含主機
檢查、未預期例外)都是帶這個標頭的 HTML;`read_form` 讀 urlencoded 表單,沿用讀 JSON 的長度與上限
檢查,重複欄位一律拒。沒宣告的伺服器(收件口、DSP)行為完全照舊。
"""

import contextlib
import html
import json
import math
import re
import socket
import sys
import threading
import traceback
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl

LOOPBACK = "127.0.0.1"
MAX_BODY_BYTES = 64 * 1024
SOCKET_TIMEOUT_SECONDS = 10.0
MAX_LENGTH_DIGITS = 10  # Content-Length 位數上限;更長的必然超過本文上限
LISTEN_BACKLOG = 128
REQUEST_DEADLINE_SECONDS = 30.0  # 整個請求(從連線建立到處理完)的期限;單次閒置逾時擋不住慢速滴入
MAX_CONNECTIONS = 64  # 同時處理中的連線上限;超過的立刻關閉,不排隊也不開新執行緒


class RequestRejected(Exception):
    """請求本身有問題,直接對應成一個 HTTP 錯誤回應。"""

    def __init__(self, status: int, code: str, retryable: bool = False):
        super().__init__(code)
        self.status, self.code, self.retryable = status, code, retryable


class NoResponse(Exception):
    """故障注入用:刻意不回應。"""


FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


@dataclass(frozen=True)
class Response:
    """處理器可以回的回應物件(新增;回 (狀態碼, 內容) 的既有處理器照舊送 JSON)。建立時就驗:標頭名稱
    要是合法的記號、值只能是 Latin-1 而且沒有控制字元(擋 CR/LF 注入標頭;非 Latin-1 會讓基底類別寫到
    一半丟例外、兩個狀態行疊在一起)、本文要是位元組(Phase 12 代碼審 r3 s3/h1)。"""

    status: int
    content_type: str
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int) or not (
                100 <= self.status <= 599):  # HTTP 狀態碼的範圍
            raise ValueError(f"狀態碼不對:{self.status!r}")
        if not isinstance(self.body, bytes):
            raise TypeError("本文要是位元組")
        _check_header("Content-Type", self.content_type)
        for name, value in self.headers:
            _check_header(name, value)

    @classmethod
    def html(cls, status: int, text: str) -> Response:
        return cls(status, "text/html; charset=utf-8", text.encode("utf-8"))

    @classmethod
    def css(cls, text: str) -> Response:
        return cls(200, "text/css; charset=utf-8", text.encode("utf-8"))

    @classmethod
    def redirect(cls, location: str) -> Response:
        """303:表單送出後一律轉到 GET,瀏覽器重讀時不會重送表單。只轉到本站的路徑:單一斜線開頭、
        可見的 ASCII、沒有反斜線(瀏覽器把兩個斜線、斜線加反斜線開頭都當成別的網站)。"""
        if not isinstance(location, str) or not _SITE_PATH.fullmatch(location):
            raise ValueError(f"只能轉址到本站的路徑:{location!r}")
        return cls(303, "text/plain; charset=utf-8", b"", (("Location", location),))


_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")  # 標頭名稱(RFC 9110 的記號)
_SITE_PATH = re.compile(r"/(?![/\\])[!-\[\]-~]*")  # 單一 / 開頭、可見 ASCII、不含反斜線


def _check_header(name: str, value: str) -> None:
    if not isinstance(name, str) or not _TOKEN.fullmatch(name):
        raise ValueError(f"標頭名稱不對:{name!r}")
    if not isinstance(value, str) or any(
            ord(c) > 0xFF or ord(c) == 0x7F or (ord(c) < 0x20 and c != "\t")  # 控制字元
            for c in value):
        raise ValueError(f"{name} 標頭的值有控制字元或不是 Latin-1")


class KitServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = LISTEN_BACKLOG

    def __init__(
        self,
        handler_class: type[JsonHandler],
        socket_timeout_seconds: float = SOCKET_TIMEOUT_SECONDS,
        host: str = LOOPBACK,
        fault_injection: bool = False,
        request_deadline_seconds: float = REQUEST_DEADLINE_SECONDS,
        max_connections: int = MAX_CONNECTIONS,
    ):
        if host != LOOPBACK:
            raise ValueError("只允許綁定回送位址")
        super().__init__((host, 0), handler_class)
        self.socket_timeout_seconds = socket_timeout_seconds
        self.fault_injection = fault_injection  # 只有啟動時明確開啟,才接受 X-Fault 標頭
        self.request_deadline_seconds = request_deadline_seconds
        if not math.isfinite(request_deadline_seconds) or request_deadline_seconds <= 0:
            raise ValueError("request_deadline_seconds 必須是有限的正數,否則每條連線會被立刻切斷")
        if max_connections < 1:
            raise ValueError("max_connections 必須至少是 1,否則伺服器會拒絕所有連線")
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)  # 已達連線上限:立刻關閉,不佔執行緒
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(
        self,
        request: Any,  # noqa: ARG002 - 沿用基底類別的簽章
        client_address: Any,  # noqa: ARG002 - 沿用基底類別的簽章
    ) -> None:
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            traceback.print_exc(file=sys.stderr)  # 客戶端中途離開不吵;其他例外要看得到


class JsonHandler(BaseHTTPRequestHandler):
    server: KitServer
    max_body_bytes = MAX_BODY_BYTES
    require_host = False  # 子類別可改成 True:連 HTTP/1.0 也必須帶合法的 Host
    content_security_policy: str | None = None  # 有值就是 HTML 伺服器:錯誤頁也回 HTML 並帶這個標頭
    _body_read = False  # 每個請求一個處理程式實例,所以這個旗標只屬於這一個請求

    def setup(self) -> None:
        super().setup()
        # 讀不到請求就放棄,不佔住執行緒;另外整個請求有總期限,慢速滴入也會被切斷
        self.connection.settimeout(self.server.socket_timeout_seconds)
        self._deadline = threading.Timer(self.server.request_deadline_seconds, self._abort)
        self._deadline.daemon = True
        self._deadline.start()

    def finish(self) -> None:
        self._deadline.cancel()
        super().finish()

    def _abort(self) -> None:
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def log_message(self, format: str, *args: Any) -> None:  # 不把每個請求印到終端
        pass

    def send_error(
        self,
        code: int,
        message: str | None = None,  # noqa: ARG002 - 沿用基底類別的簽章
        explain: str | None = None,  # noqa: ARG002 - 沿用基底類別的簽章
    ) -> None:
        self.reply_error(code, "http_error", False)  # 基底類別的錯誤頁也走同一個出口

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    # ---- 子類別實作 ----
    def handle_request(self, method: str) -> tuple[int, dict[str, Any]] | Response:
        raise NotImplementedError

    def map_exception(
        self,
        exc: Exception,  # noqa: ARG002 - 預設實作不看,子類別覆寫時才用
    ) -> tuple[int, str, bool] | None:
        """把自己的例外對應成 (狀態碼, 錯誤代碼, 可否重試);不認得就回 None。"""
        return None

    def error_extras(
        self,
        exc: Exception,  # noqa: ARG002 - 預設實作不看,子類別覆寫時才用
    ) -> dict[str, Any]:
        """對應成錯誤回應時要多帶的固定欄位(例如 highest_revision);預設沒有。"""
        return {}

    # ---- 流程 ----
    def _dispatch(self, method: str) -> None:
        try:
            self.check_host()
            result = self.handle_request(method)
            if isinstance(result, Response):
                self.send(result)
            else:
                status, payload = result
                self.reply(status, payload)
        except RequestRejected as exc:
            self.reply_error(exc.status, exc.code, exc.retryable)
        except NoResponse:
            return  # 故障注入:刻意不回應
        except Exception as exc:
            self._reply_unexpected(exc)

    def _reply_unexpected(self, exc: Exception) -> None:
        """自己的例外對應失敗、或回傳形狀不對,也一律落到 500 JSON,不能無聲斷線。"""
        try:
            mapped = self.map_exception(exc)
            if mapped is not None:
                status, code, retryable = mapped
                if self.content_security_policy is not None:
                    self.reply_error(status, code, retryable)
                    return
                # 額外欄位放在前面:子類別不能藉由同名的鍵覆寫錯誤代碼或可否重試
                self.reply(status, {**self.error_extras(exc), "error": code,
                                    "retryable": retryable})
                return
        except Exception:
            traceback.print_exc(file=sys.stderr)  # 對應本身出錯:記下來,改回 500
        else:
            traceback.print_exc(file=sys.stderr)
        self.reply_error(500, "internal_error", False)

    def check_host(self) -> None:
        port = self.server.server_address[1]
        host = self.single_header("Host")
        if host is None and self.request_version == "HTTP/1.0" and not self.require_host:
            return  # 舊式探針可能不送 Host;瀏覽器(DNS rebinding 的來源)一定會送
        if host is None or host.lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise RequestRejected(400, "invalid_host")

    def single_header(self, name: str) -> str | None:
        values = self.headers.get_all(name) or []
        if len(values) > 1:
            raise RequestRejected(400, "duplicate_header")
        return values[0] if values else None

    def read_fault(self, modes: frozenset[str]) -> str | None:
        """讀 X-Fault 標頭:旗標沒開就一律拒絕(不改任何狀態),不認得的模式也拒絕。"""
        mode = self.single_header("X-Fault")
        if mode is None:
            return None
        if not self.server.fault_injection:
            raise RequestRejected(400, "fault_injection_disabled")
        if mode not in modes:
            raise RequestRejected(400, "unknown_fault_mode")
        return mode

    def _require_same_site(self) -> None:
        """表單要是本站頁面送的(第 3 輪代碼審 s3):主機標頭檢查擋得住 DNS rebinding,擋不住別的網站
        送來的表單。瀏覽器送表單會帶 Origin 或 Sec-Fetch-Site;帶了的每一個都要指向本站,兩個都沒有
        就不當成本站頁面送的。"""
        site, origin = self.single_header("Sec-Fetch-Site"), self.single_header("Origin")
        host = self.single_header("Host")
        if site is None and origin is None:
            raise RequestRejected(403, "cross_site_form")
        if site is not None and site.strip().lower() != "same-origin":
            raise RequestRejected(403, "cross_site_form")
        if origin is not None and (
                host is None or origin.strip().lower() != f"http://{host.strip()}".lower()):
            raise RequestRejected(403, "cross_site_form")

    def _read_body(self, reader: str) -> bytes:
        """請求本文:只能讀一次、不收分塊、長度合法且不超過上限(讀 JSON 與讀表單共用)。"""
        if self._body_read:
            raise RuntimeError(f"{reader} 只能呼叫一次:請求本文讀過就沒了")
        self._body_read = True
        if self.headers.get("Transfer-Encoding"):
            raise RequestRejected(411, "chunked_not_supported")
        raw_length = (self.single_header("Content-Length") or "0").strip()
        if not raw_length.isascii() or not raw_length.isdigit():
            raise RequestRejected(400, "invalid_content_length")
        if len(raw_length) > MAX_LENGTH_DIGITS or int(raw_length) > self.max_body_bytes:
            raise RequestRejected(413, "body_too_large")
        return self.read_exactly(int(raw_length))

    def read_form(self) -> dict[str, str]:
        """讀 urlencoded 表單(瀏覽器的表單送出):先確認是本站送的表單,同一個欄位出現兩次一律拒
        (哪一個算數說不清),不是 UTF-8 也拒。

        媒體類型的分層:讀表單在這裡擋非表單格式(415),因為瀏覽器跨站送得出表單格式;讀 JSON 不擋
        媒體類型(既有行為,收件口與 DSP 的用戶端都是本專案的程式,各自的處理器不收瀏覽器表單)。"""
        self._require_same_site()
        kind = (self.single_header("Content-Type") or "").split(";")[0].strip().lower()
        if kind != FORM_CONTENT_TYPE:
            raise RequestRejected(415, "unsupported_media_type")
        raw = self._read_body("read_form")
        try:
            pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, errors="strict")
        except (UnicodeDecodeError, ValueError) as exc:
            raise RequestRejected(400, "invalid_form") from exc
        fields: dict[str, str] = {}
        for name, value in pairs:
            if name in fields:
                raise RequestRejected(400, "duplicate_field")
            fields[name] = value
        return fields

    def read_json(self, allow_empty: bool = False) -> dict[str, Any]:
        raw = self._read_body("read_json")
        try:
            body = json.loads(raw or (b"{}" if allow_empty else b""))
        except (ValueError, RecursionError) as exc:  # 含語法錯、超長數字、非 UTF-8、過深巢狀
            raise RequestRejected(400, "invalid_json") from exc
        if not isinstance(body, dict):
            raise RequestRejected(400, "invalid_json")
        return body

    def read_exactly(self, length: int) -> bytes:
        try:
            raw = self.rfile.read(length)
        except TimeoutError as exc:  # 客戶端宣告了長度卻遲遲不送完
            raise RequestRejected(408, "request_timeout") from exc
        if len(raw) < length:
            raise RequestRejected(400, "incomplete_body")
        return raw

    # ---- 回應 ----
    def reply_error(self, status: int, code: str, retryable: bool) -> None:
        if self.content_security_policy is not None:  # HTML 伺服器:錯誤頁也是 HTML,不洩漏內部細節
            self.send(Response.html(status, "<!doctype html><meta charset=\"utf-8\">"
                                            f"<title>錯誤</title><p>錯誤 {status}:"
                                            f"{html.escape(code)}</p>"))
            return
        self.reply(status, {"error": code, "retryable": retryable})

    def send(self, response: Response) -> None:
        """唯一的送出路徑(JSON 的 reply 也走這裡,第 3 輪代碼審 a4)。宣告了內容安全政策的伺服器,
        每個回應都帶政策標頭,不看內容類型(r3 h2:原本只認 text/html 開頭,Text/HTML、xhtml、svg
        都漏掉)。全部標頭在送出狀態行之前驗完,驗不過就還沒寫出任何一個位元組。"""
        extra = list(response.headers)
        if self.content_security_policy is not None:
            extra.insert(0, ("Content-Security-Policy", self.content_security_policy))
        for name, value in extra:
            _check_header(name, value)
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(response.body)
        except OSError:
            pass  # 客戶端已逾時離開,回應寫不出去是正常情況

    def reply(self, status: int, payload: dict[str, Any]) -> None:
        self.send(Response(status, "application/json", json.dumps(payload).encode()))
