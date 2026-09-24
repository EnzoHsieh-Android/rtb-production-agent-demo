"""共用 HTTP 基礎的新增出口與入口(Phase 12 增量 2b 前置,代碼審 r3 m5、r2 n15):處理器可以回一個
回應物件(HTML、樣式表、303 轉址);宣告 HTML 的伺服器錯誤頁也是 HTML 並帶內容安全政策標頭;讀表單沿用
讀 JSON 的長度與上限檢查,重複欄位一律拒。既有回 (狀態碼, 內容) 的處理器與 JSON 錯誤頁不變。"""

import http.client
import threading

import pytest

from rtb.httpkit import MAX_BODY_BYTES, JsonHandler, KitServer, RequestRejected, Response

CSP = "script-src 'none'; default-src 'none'"


class OwnError(Exception):
    pass


class PageHandler(JsonHandler):
    content_security_policy = CSP

    def map_exception(self, exc):
        return (409, "own_error", False) if isinstance(exc, OwnError) else None

    def handle_request(self, method):
        if self.path == "/page":
            return Response.html(200, "<p>頁面</p>")
        if self.path == "/style":
            return Response.css("body { color: black; }")
        if self.path == "/go" and method == "POST":
            return Response.redirect("/#current")
        if self.path == "/form" and method == "POST":
            return Response.html(200, repr(sorted(self.read_form().items())))
        if self.path == "/boom":
            raise RuntimeError("secret internal detail")
        if self.path == "/reject":
            raise RequestRejected(403, "cross_site")
        if self.path == "/own":
            raise OwnError("secret internal detail")
        return 200, {"still": "json"}


class PlainHandler(JsonHandler):
    def handle_request(self, method):
        if self.path == "/page":
            return Response.html(200, "<p>頁面</p>")
        return 200, {"method": method}


@pytest.fixture
def start():
    servers = []

    def run(handler):
        srv = KitServer(handler)
        threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]

    yield run
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _request(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        raw = b"" if body is None else body
        conn.putheader("Content-Length", str(len(raw)))
        conn.endheaders(raw)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def test_a_handler_can_return_an_html_page_with_the_policy_header(start):
    port = start(PageHandler)
    status, headers, body = _request(port, "GET", "/page")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Content-Security-Policy"] == CSP
    assert body.decode() == "<p>頁面</p>"


def test_a_stylesheet_is_served_as_css(start):
    status, headers, body = _request(start(PageHandler), "GET", "/style")
    assert status == 200 and headers["Content-Type"] == "text/css; charset=utf-8"
    assert body == b"body { color: black; }"


def test_a_redirect_is_a_303_with_its_location(start):
    status, headers, body = _request(start(PageHandler), "POST", "/go")
    assert status == 303 and headers["Location"] == "/#current" and body == b""


@pytest.mark.parametrize(("path", "host", "status"), [
    ("/page", "evil.example:80", 400),  # 主機標頭不是本機
    ("/boom", None, 500),  # 未預期例外
    ("/reject", None, 403),  # 處理器自己拒絕
    ("/own", None, 409),  # 子類別自己的例外對應
    ("/page", None, 501),  # 基底類別的錯誤頁(不支援的方法)
])
def test_an_html_server_answers_errors_as_html_with_the_policy_header(start, path, host,
                                                                      status):
    method = "PUT" if status == 501 else "GET"
    got, headers, body = _request(start(PageHandler), method, path, host=host)
    assert got == status
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Content-Security-Policy"] == CSP
    assert b"secret internal detail" not in body


def test_a_json_answer_from_an_html_server_keeps_its_json(start):
    status, headers, _ = _request(start(PageHandler), "GET", "/json")
    assert status == 200 and headers["Content-Type"] == "application/json"


def test_a_plain_json_server_keeps_json_errors_and_adds_no_policy(start):
    """既有的 JSON 伺服器(沒宣告內容安全政策)錯誤照舊是 JSON;回應物件回 HTML 時不帶政策標頭。"""
    port = start(PlainHandler)
    status, headers, body = _request(port, "GET", "/page", host="evil.example:80")
    assert status == 400 and headers["Content-Type"] == "application/json"
    assert body == b'{"error": "invalid_host", "retryable": false}'
    _, headers, _ = _request(port, "GET", "/page")
    assert "Content-Security-Policy" not in headers


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def test_the_form_reader_reads_urlencoded_fields(start):
    status, _, body = _request(start(PageHandler), "POST", "/form",
                               b"token=abc&scenario=F3&note=%E4%B8%AD", FORM)
    assert status == 200
    assert body.decode() == repr([("note", "中"), ("scenario", "F3"), ("token", "abc")])


@pytest.mark.parametrize(("body", "headers", "status"), [
    (b"token=a&token=b", FORM, 400),  # 同一個欄位兩次
    (b"a=1", {"Content-Type": "application/json"}, 415),  # 不是表單
    (b"a=%FF", FORM, 400),  # 不是 UTF-8
    (b"a=\xff", FORM, 400),  # 本文本身就不是 UTF-8
    (b"x" * (MAX_BODY_BYTES + 1), FORM, 413),  # 超過本文上限
])
def test_the_form_reader_refuses_duplicate_fields_and_oversized_bodies(start, body, headers,
                                                                      status):
    """[S1051] 讀表單沿用讀 JSON 的長度與上限檢查,同一個欄位出現兩次一律拒。"""
    got, _, _ = _request(start(PageHandler), "POST", "/form", body, headers)
    assert got == status
