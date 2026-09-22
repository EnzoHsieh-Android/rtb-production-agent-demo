"""共用的 HTTP 用戶端基礎:逾時必填、標頭是封閉列舉、沒有能送任意標頭的路徑。"""

import inspect
import threading

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
