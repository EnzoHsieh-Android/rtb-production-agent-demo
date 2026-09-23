"""共用的 HTTP 用戶端基礎:逾時必填、標頭是封閉列舉、沒有能送任意標頭的路徑。"""

import inspect
import socket
import threading
import time
from collections.abc import Mapping

import pytest

from rtb import httpclient
from rtb.httpkit import JsonHandler, KitServer


class EchoHandler(JsonHandler):
    def handle_request(self, method):
        if self.path == "/slow":
            import time
            time.sleep(2)
            return 200, {}
        if self.path == "/boom":
            return 500, {"error": "internal_error"}
        headers = {name: self.headers.get(name) for name in self.headers}
        return 200, {"method": method, "headers": headers, "body": self.read_json(allow_empty=True)}


@pytest.fixture
def server():
    started = []

    def start():
        srv = KitServer(EchoHandler)
        threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
        started.append(srv)
        return srv

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def url(srv, path):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


# ---- S54:逾時必填,真的會放棄 ----
def test_request_json_has_no_default_timeout():
    params = inspect.signature(httpclient.request_json).parameters
    assert "timeout_seconds" in params
    assert params["timeout_seconds"].default is inspect.Parameter.empty


def test_a_request_that_never_responds_gives_up_after_the_timeout(server):
    srv = server()
    import time

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        httpclient.request_json(url(srv, "/slow"), "GET", None, timeout_seconds=0.3)

    assert time.monotonic() - started < 1.5


def test_a_successful_request_returns_status_and_decoded_body(server):
    srv = server()

    status, body = httpclient.request_json(url(srv, "/x"), "GET", None, timeout_seconds=3)

    assert status == 200 and body["method"] == "GET"


def test_a_body_is_sent_as_json_and_content_type_is_set(server):
    srv = server()

    status, body = httpclient.request_json(
        url(srv, "/x"), "POST", {"a": 1}, timeout_seconds=3)

    assert status == 200
    assert body["body"] == {"a": 1}
    assert body["headers"]["Content-Type"] == "application/json"


def test_a_non_2xx_status_is_returned_not_raised(server):
    srv = server()

    status, body = httpclient.request_json(url(srv, "/boom"), "GET", None, timeout_seconds=3)

    assert status == 500 and body["error"] == "internal_error"


def test_a_connection_failure_raises(server):
    srv = server()
    port = srv.server_address[1]
    srv.shutdown()
    srv.server_close()

    with pytest.raises(OSError):
        httpclient.request_json(f"http://127.0.0.1:{port}/x", "GET", None, timeout_seconds=1)


# ---- headers 是封閉列舉,不接受任意字串 ----
def test_headers_must_be_clientheader_members(server):
    srv = server()

    _status, body = httpclient.request_json(
        url(srv, "/x"), "GET", None, timeout_seconds=3,
        headers={httpclient.ClientHeader.IDEMPOTENCY_KEY: "k1"})

    assert body["headers"]["Idempotency-Key"] == "k1"


def test_a_header_key_that_is_not_a_clientheader_member_is_rejected(server):
    srv = server()

    with pytest.raises(TypeError):
        httpclient.request_json(
            url(srv, "/x"), "GET", None, timeout_seconds=3, headers={"X-Fault": "boom"})


# ---- S44:三支檔的原始碼都不出現 X-Fault(輔助訊號,主防線是上面的封閉列舉) ----
@pytest.mark.parametrize("module_name", ["rtb.httpclient"])
def test_the_client_module_source_never_mentions_the_fault_header(module_name):
    import importlib

    module = importlib.import_module(module_name)
    source = inspect.getsource(module)

    assert "X-Fault" not in source


def test_the_closed_header_enum_contains_exactly_the_documented_members():
    """2026-09-22 第四輪合約審計指出:直接在封閉列舉加一個值是故障注入標頭的成員,驗證照樣
    通過。這裡把成員整份寫死:要新增任何標頭,必須同時改這支測試,留下有意識的決定。
    2026-09-23 執行一筆(Phase 3 增量 3)有意識地加了能力憑證。
    2026-09-24 Phase 9 增量 3 代碼審第 2 輪有意識地加了 DSP 唯讀稽核金鑰:它不是故障注入標頭,
    只給維運套件讀 DSP 列操作端點用,不再借用能力憑證的標頭。"""
    assert {member.value for member in httpclient.ClientHeader} == {
        "Idempotency-Key", "X-Capability", "X-Dsp-Audit-Key"}


def test_client_headers_are_exactly_idempotency_key_and_capability():
    """執行一筆 S63:執行行程要經共用用戶端送出能力憑證。

    名稱跟共用格式模組的一致:兩邊不互相匯入,由這裡比對。第三個成員是 DSP 唯讀稽核金鑰
    (Phase 9 增量 3 代碼審第 2 輪),不是故障注入標頭,名稱同樣跟共用格式模組比對。"""
    from rtb.capabilitykit import AUDIT_HEADER, HEADER

    assert {member.value for member in httpclient.ClientHeader} == {
        "Idempotency-Key", HEADER, AUDIT_HEADER}
    assert httpclient.ClientHeader.CAPABILITY.value == HEADER  # 兩份字串必須一致
    assert httpclient.ClientHeader.AUDIT_KEY.value == AUDIT_HEADER
    with pytest.raises(TypeError):
        httpclient.request_json("http://127.0.0.1:9/x", "GET", None, 1,
                                headers={"X-Fault": "transient_5xx"})


# ---- headers 只驗證一次:自訂物件不能在驗證跟送出之間變臉 ----
class _ShiftingHeaders(Mapping):
    """`.keys()` 第一次回傳合法鍵、之後改回不合法的鍵——模擬「驗證讀一次、送出又讀一次」
    的兩段式走訪可以被繞過;只走訪一次(`dict(headers)` 具現化)的寫法不會被這招影響。"""

    def __init__(self):
        self._reads = 0

    def __getitem__(self, key):
        return "k1"

    def keys(self):
        self._reads += 1
        if self._reads > 1:
            return ["X-Fault"]
        return [httpclient.ClientHeader.IDEMPOTENCY_KEY]

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return 1


def test_a_header_mapping_that_changes_between_reads_cannot_smuggle_an_unvalidated_header(server):
    srv = server()

    status, body = httpclient.request_json(
        url(srv, "/x"), "GET", None, timeout_seconds=3, headers=_ShiftingHeaders())

    assert status == 200
    assert "X-Fault" not in body["headers"]


# ---- 不自動跟隨重新導向 ----
def _raw_response_server(response_bytes: bytes, *, hold_open: bool = False):
    """起一個最陽春的 TCP 伺服器,原樣回傳給定的位元組——用來組出 JsonHandler 送不出來的
    回應(3xx、超大本文、分段慢速送出),不代表這是專案自己的 HTTP 伺服器實作。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    def serve():
        conn, _ = sock.accept()
        with conn:
            conn.recv(65536)  # 讀掉請求,不解析
            try:
                if hold_open:
                    for i in range(0, len(response_bytes), 4):
                        conn.sendall(response_bytes[i : i + 4])
                        time.sleep(0.15)
                else:
                    conn.sendall(response_bytes)
            except OSError:
                pass  # 客戶端等到逾時就會提早斷線,這裡送不完是預期中的事,不是測試本身的錯

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, sock


# 第三輪審計:原本只測 302,對 307/308 開例外就測不到
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_3xx_response_is_not_followed_and_becomes_an_http_error(code):
    body = b'{"error":"moved"}'
    response = (
        f"HTTP/1.1 {code} Redirect\r\n".encode()
        + b"Location: http://127.0.0.1:1/elsewhere\r\n"
        b"Content-Type: application/json\r\n" + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    port, sock = _raw_response_server(response)
    try:
        status, decoded = httpclient.request_json(
            f"http://127.0.0.1:{port}/x", "GET", None, timeout_seconds=3)
        assert status == code
        assert decoded == {"error": "moved"}
    finally:
        sock.close()


# ---- 回應本文有位元組上限 ----
def test_a_response_body_over_the_byte_cap_is_rejected():
    # 合法 JSON 字串,單純太長;不用巨大數字字面值,避免撞到 Python 自己對超長整數字面值
    # 的解析上限(那是另一件事,不是這裡要驗證的位元組上限)。
    oversized = b'"' + b"x" * (httpclient.MAX_RESPONSE_BYTES + 1) + b'"'
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(oversized)}\r\n\r\n".encode()
        + oversized
    )
    port, sock = _raw_response_server(response)
    try:
        with pytest.raises(ValueError, match="超過上限"):
            httpclient.request_json(f"http://127.0.0.1:{port}/x", "GET", None, timeout_seconds=3)
    finally:
        sock.close()


# ---- 慢速持續送資料仍會在總期限後放棄,不是只看單次 socket 操作 ----
def test_a_slow_drip_response_gives_up_once_the_total_deadline_passes():
    body = b'{"ok": true, "pad": "' + b"x" * 20 + b'"}'  # 夠多段落,drip 總時間才拉得開
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    port, sock = _raw_response_server(response, hold_open=True)
    # 每段間隔 0.15 秒、每次只送 4 位元組:單次 socket 讀取永遠不會撞到逾時(遠小於下面的
    # timeout_seconds),但整個回應要送完(約 10 段 * 0.15 秒 ≈ 1.5 秒)遠超過總期限。
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            httpclient.request_json(
                f"http://127.0.0.1:{port}/x", "GET", None, timeout_seconds=0.5)
        elapsed = time.monotonic() - started
        assert 0.4 < elapsed < 3  # 真的等到超過期限才放棄,但沒有等到整包收完(~1.5 秒)才放棄
    finally:
        sock.close()
