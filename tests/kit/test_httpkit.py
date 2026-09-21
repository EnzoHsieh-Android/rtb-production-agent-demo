"""共用的 HTTP 基礎:回送位址、Host 檢查、有上限的本文、JSON 錯誤、逾時、未預期例外的 500。"""

import http.client
import json
import socket
import threading
import time

import pytest

from rtb.httpkit import JsonHandler, KitServer, NoResponse, RequestRejected


class ExtraError(Exception):
    extra = 42


RAISING_PATHS = {
    "/extras": ExtraError("has an extra field"),
    "/boom": RuntimeError("secret internal detail"),
    "/mapped": KeyError("mapped"),
    "/mapper-crashes": LookupError("this one makes the mapper itself fail"),
    "/mapper-bad-shape": IndexError("mapper returns garbage"),
}


class EchoHandler(JsonHandler):
    def handle_request(self, method):
        path = self.path
        if path in RAISING_PATHS:
            raise RAISING_PATHS[path]
        if path == "/reject":
            raise RequestRejected(418, "teapot", retryable=True)
        if path == "/silent":
            raise NoResponse
        if path == "/read-twice":
            self.read_json()
            return 200, {"second": self.read_json()}
        if path in ("/echo", "/echo-empty-ok"):
            return 200, {"body": self.read_json(allow_empty=path == "/echo-empty-ok")}
        if path == "/header":
            return 200, {"value": self.single_header("X-One")}
        return 200, {"method": method}

    def error_extras(self, exc):
        return {"extra": exc.extra} if isinstance(exc, ExtraError) else {}

    def map_exception(self, exc):
        if isinstance(exc, ExtraError):
            return 409, "with_extras", False
        if isinstance(exc, KeyError):
            return 409, "mapped_error", False
        if isinstance(exc, LookupError) and not isinstance(exc, IndexError):
            raise RuntimeError("mapper blew up")
        if isinstance(exc, IndexError):
            return "not", "a valid tuple"  # type: ignore[return-value]
        return None


@pytest.fixture
def server():
    started = []

    def start(**kwargs):
        srv = KitServer(EchoHandler, **kwargs)
        threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
        started.append(srv)
        return srv

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def call(srv, method, path, body=None, headers=None, raw_body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
    payload = raw_body if raw_body is not None else (None if body is None else json.dumps(body))
    conn.request(method, path, body=payload, headers=headers or {})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_the_server_refuses_to_bind_anything_but_loopback():
    with pytest.raises(ValueError):
        KitServer(EchoHandler, host="0.0.0.0")  # noqa: S104 - 測試的就是它會被拒絕


def test_a_request_with_a_foreign_host_header_is_rejected(server):
    srv = server()

    status, data = call(srv, "GET", "/", headers={"Host": "evil.example"})

    assert (status, data["error"]) == (400, "invalid_host")


def test_loopback_host_headers_are_accepted(server):
    srv = server()
    port = srv.server_address[1]

    for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        assert call(srv, "GET", "/", headers={"Host": host})[0] == 200


def test_a_duplicated_single_valued_header_is_rejected(server):
    srv = server()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
    conn.putrequest("GET", "/header")
    conn.putheader("X-One", "a")
    conn.putheader("X-One", "b")
    conn.endheaders()
    resp = conn.getresponse()

    assert (resp.status, json.loads(resp.read())["error"]) == (400, "duplicate_header")


def test_a_valid_json_object_body_is_read(server):
    srv = server()

    assert call(srv, "POST", "/echo", {"a": 1})[1] == {"body": {"a": 1}}


@pytest.mark.parametrize(("raw", "expected"), [
    ("not json", "invalid_json"), ("[1,2]", "invalid_json"), ("", "invalid_json"),
    ("\xff\xfe", "invalid_json"),
])
def test_a_body_that_is_not_a_json_object_is_rejected(server, raw, expected):
    srv = server()

    status, data = call(srv, "POST", "/echo", raw_body=raw.encode("latin-1"))

    assert (status, data["error"]) == (400, expected)


def test_an_empty_body_is_allowed_only_when_the_route_asks_for_it(server):
    srv = server()

    assert call(srv, "POST", "/echo-empty-ok", raw_body=b"")[1] == {"body": {}}


def test_a_body_over_the_limit_is_rejected_before_it_is_read(server):
    srv = server()
    too_big = b'{"x":"' + b"a" * (64 * 1024) + b'"}'

    status, data = call(srv, "POST", "/echo", raw_body=too_big)

    assert (status, data["error"]) == (413, "body_too_large")


def test_a_bad_content_length_is_rejected(server):
    srv = server()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
    conn.putrequest("POST", "/echo")
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    resp = conn.getresponse()

    assert (resp.status, json.loads(resp.read())["error"]) == (400, "invalid_content_length")


def test_chunked_bodies_are_not_supported(server):
    srv = server()

    chunked = {"Transfer-Encoding": "chunked"}
    status, data = call(srv, "POST", "/echo", raw_body=b"{}", headers=chunked)

    assert (status, data["error"]) == (411, "chunked_not_supported")


def test_a_rejection_becomes_a_typed_json_error(server):
    srv = server()

    status, data = call(srv, "GET", "/reject")

    assert (status, data) == (418, {"error": "teapot", "retryable": True})


def test_an_unexpected_exception_is_a_500_that_leaks_nothing_and_the_server_lives(server, capfd):
    srv = server()

    status, data = call(srv, "GET", "/boom")

    assert (status, data) == (500, {"error": "internal_error", "retryable": False})
    assert "secret" not in json.dumps(data)
    assert "secret internal detail" in capfd.readouterr().err  # 細節只進 stderr,不進回應
    assert call(srv, "GET", "/")[0] == 200


def test_a_subclass_can_add_extra_fields_to_its_mapped_error_body(server):
    srv = server()

    status, data = call(srv, "GET", "/extras")

    assert (status, data) == (409, {"error": "with_extras", "retryable": False, "extra": 42})


def test_extra_fields_can_never_override_the_error_code_or_retryability():
    class Sneaky(EchoHandler):
        def error_extras(self, exc):
            return {"error": "forged", "retryable": True, "extra": 1}

    srv = KitServer(Sneaky)
    threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
    try:
        status, data = call(srv, "GET", "/extras")
    finally:
        srv.shutdown()
        srv.server_close()

    assert (status, data["error"], data["retryable"], data["extra"]) == (
        409, "with_extras", False, 1)


def test_a_subclass_can_map_its_own_exceptions(server):
    srv = server()

    assert call(srv, "GET", "/mapped") == (409, {"error": "mapped_error", "retryable": False})


def test_a_stalled_connection_is_dropped_after_the_socket_timeout(server):
    srv = server(socket_timeout_seconds=0.3)
    sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=3)
    sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1")  # 只送一半就不動了
    started = time.monotonic()

    leftover = sock.recv(1024)  # 伺服器放棄之後會關閉連線,recv 才會返回

    assert time.monotonic() - started < 2.0
    assert b"200" not in leftover
    sock.close()
    assert call(srv, "GET", "/")[0] == 200  # 沒有被卡住的執行緒拖垮


def test_a_malformed_request_line_gets_a_json_error_not_an_html_page(server):
    srv = server()
    sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=3)
    sock.sendall(b"NONSENSE\r\n\r\n")
    raw = b""
    while chunk := sock.recv(1024):
        raw += chunk
    sock.close()

    # 不同的 Python 小版本對畸形請求行的回應不同:舊的只回 JSON 本文(HTTP/0.9 形式),
    # 新的(例如 3.14.7)有完整狀態行與標頭;兩種都要接受,本文一定是 JSON
    body = raw.split(b"\r\n\r\n", 1)[-1]
    assert json.loads(body)["error"] == "http_error"
    assert b"<html" not in raw.lower()


def test_a_mapper_that_itself_fails_still_yields_a_json_500_not_a_silent_disconnect(server, capfd):
    srv = server()

    assert call(srv, "GET", "/mapper-crashes") == (
        500, {"error": "internal_error", "retryable": False})
    assert call(srv, "GET", "/mapper-bad-shape") == (
        500, {"error": "internal_error", "retryable": False})
    assert "mapper blew up" in capfd.readouterr().err  # 原因要看得到
    assert call(srv, "GET", "/")[0] == 200


def _raw(srv, request: bytes) -> bytes:
    sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=3)
    sock.sendall(request)
    raw = b""
    while chunk := sock.recv(1024):
        raw += chunk
    sock.close()
    return raw


def test_two_host_headers_are_rejected_even_when_the_first_one_is_fine(server):
    srv = server()
    port = srv.server_address[1]

    raw = _raw(srv, (f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nHost: evil.example\r\n"
                     "Connection: close\r\n\r\n").encode())

    assert b" 400 " in raw.split(b"\r\n")[0] and b"duplicate_header" in raw


def test_two_content_length_headers_are_rejected(server):
    srv = server()
    port = srv.server_address[1]

    raw = _raw(srv, (f"POST /echo HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 2\r\n"
                     "Content-Length: 9\r\nConnection: close\r\n\r\n{}").encode())

    assert b" 400 " in raw.split(b"\r\n")[0] and b"duplicate_header" in raw


def test_a_client_that_hangs_up_before_sending_the_whole_body_gets_incomplete_body(server):
    srv = server()
    port = srv.server_address[1]
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.sendall(f"POST /echo HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 50\r\n\r\n"
                 '{"a":'.encode())
    sock.shutdown(socket.SHUT_WR)  # 宣告了 50 個位元組卻只送幾個就結束
    raw = b""
    while chunk := sock.recv(1024):
        raw += chunk
    sock.close()

    assert b"incomplete_body" in raw


def test_deliberately_sending_no_response_closes_the_connection_with_nothing_written(server):
    srv = server()
    port = srv.server_address[1]

    raw = _raw(srv, f"GET /silent HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())

    assert raw == b""  # 沒有狀態行、沒有本文:與「回 500」是可以分辨的


def test_a_subclass_can_lower_the_body_limit():
    class Small(EchoHandler):
        max_body_bytes = 10

    srv = KitServer(Small)
    threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
    try:
        status, data = call(srv, "POST", "/echo", raw_body=b'{"k":"0123456789"}')
    finally:
        srv.shutdown()
        srv.server_close()

    assert (status, data["error"]) == (413, "body_too_large")


def test_reading_the_body_a_second_time_is_a_programming_error_not_a_hang(server, capfd):
    srv = server()

    started = time.monotonic()
    status, data = call(srv, "POST", "/read-twice", {"a": 1})

    assert time.monotonic() - started < 2.0  # 不會卡到逾時
    assert (status, data["error"]) == (500, "internal_error")
    assert "read_json" in capfd.readouterr().err


def test_a_json_parser_that_runs_out_of_recursion_is_a_400_not_a_500(server, monkeypatch):
    srv = server()

    real_loads = json.loads

    def exhausted(raw, *args, **kwargs):
        if raw == b'{"a": 1}':  # 只對伺服器收到的請求本文動手,測試用戶端解析回應照常
            raise RecursionError
        return real_loads(raw, *args, **kwargs)

    monkeypatch.setattr(json, "loads", exhausted)  # 有些 Python 版本對深層巢狀會這樣

    status, data = call(srv, "POST", "/echo", {"a": 1})

    assert (status, data["error"]) == (400, "invalid_json")


def test_a_client_that_drips_bytes_is_cut_off_by_the_whole_request_deadline(server):
    srv = server(socket_timeout_seconds=5.0, request_deadline_seconds=0.5)
    sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
    started = time.monotonic()
    closed = False
    try:
        sock.sendall(b"POST /echo HTTP/1.1\r\nHost: 127.0.0.1")
        for _ in range(60):  # 每 0.1 秒滴一個位元組:每次都在單次閒置逾時之內
            time.sleep(0.1)
            sock.sendall(b"x")
    except OSError:
        closed = True  # 伺服器已經切斷
    elapsed = time.monotonic() - started
    sock.close()

    assert closed and elapsed < 3.0
    assert call(srv, "GET", "/")[0] == 200


def test_connections_beyond_the_cap_are_dropped_and_slots_come_back(server):
    srv = server(max_connections=2, socket_timeout_seconds=5.0)
    port = srv.server_address[1]
    held = [socket.create_connection(("127.0.0.1", port), timeout=3) for _ in range(2)]
    time.sleep(0.2)  # 讓伺服器接受並佔住兩個名額

    third = socket.create_connection(("127.0.0.1", port), timeout=3)
    third.settimeout(2.0)
    assert third.recv(10) == b""  # 超過上限:立刻被關閉
    third.close()

    held[0].close()
    time.sleep(0.3)
    assert call(srv, "GET", "/")[0] == 200  # 名額還回來了
    held[1].close()


def test_a_handler_can_require_a_host_header_even_for_http_1_0(server):
    class Strict(EchoHandler):
        require_host = True

    strict = KitServer(Strict)
    threading.Thread(target=strict.serve_forever, args=(0.02,), daemon=True).start()
    lenient = server()
    try:
        assert b" 400 " in _raw(strict, b"GET / HTTP/1.0\r\n\r\n").split(b"\r\n")[0]
        assert b" 200 " in _raw(lenient, b"GET / HTTP/1.0\r\n\r\n").split(b"\r\n")[0]
    finally:
        strict.shutdown()
        strict.server_close()


@pytest.mark.parametrize("value", [0, -1])
def test_a_connection_cap_below_one_is_refused_at_construction(value):
    with pytest.raises(ValueError):
        KitServer(EchoHandler, max_connections=value)


def test_no_deadline_timer_threads_are_left_behind_after_requests_finish(server):
    srv = server(request_deadline_seconds=30.0)

    for _ in range(20):
        call(srv, "GET", "/")
    call(srv, "GET", "/boom")  # 例外路徑也要收乾淨
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and [
            t for t in threading.enumerate() if isinstance(t, threading.Timer)]:
        time.sleep(0.05)

    assert [t for t in threading.enumerate() if isinstance(t, threading.Timer)] == []


def test_connection_slots_come_back_after_a_handler_error_and_over_limit_sockets_are_shut(
        server, monkeypatch):
    srv = server(max_connections=1)
    shut_ports = []
    real_shutdown = KitServer.shutdown_request

    def recording(self, request):
        shut_ports.append(request.getpeername()[1])  # 在真的關閉之前記下對端埠
        real_shutdown(self, request)

    monkeypatch.setattr(KitServer, "shutdown_request", recording)

    assert call(srv, "GET", "/boom")[0] == 500  # 處理程式出錯之後名額仍然要還
    assert call(srv, "GET", "/")[0] == 200
    held = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=3)
    time.sleep(0.2)
    over = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=3)
    over_port = over.getsockname()[1]
    over.settimeout(2.0)
    assert over.recv(10) == b""
    over.close()
    held.close()

    assert over_port in shut_ports  # 超過上限的連線被明確關閉,不是靠引用計數碰巧回收


@pytest.mark.parametrize("value", [0, -1.0, float("nan"), float("inf")])
def test_a_deadline_that_is_not_a_finite_positive_number_is_refused_at_construction(value):
    with pytest.raises(ValueError):
        KitServer(EchoHandler, request_deadline_seconds=value)
