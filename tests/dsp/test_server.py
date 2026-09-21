"""Mock DSP 的 HTTP 事故測試:真的獨立行程、真的網路逾時。"""

import contextlib
import http.client
import json
import os
import signal
import socket
import sqlite3
import threading
import time

import pytest

from rtb.dsp.errors import PermanentError, StoreBusy, TransientError
from rtb.dsp.server import error_entry

BUDGET = "/campaigns/c1/budget"
HEADERS = {"Idempotency-Key": "k1"}


def set_budget(dsp, budget=150, version=1, key="k1", fault=None, timeout=3.0):
    headers = {"Idempotency-Key": key}
    if fault:
        headers["X-Fault"] = fault
    return dsp.request("POST", BUDGET, {"new_budget": budget, "expected_version": version},
                       headers, timeout=timeout)


def wait_until(condition, seconds=5.0):
    """輪詢到條件成立或逾時;取代固定睡眠,慢機器上不會誤紅或偽綠。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def campaign(dsp):
    return dsp.request("GET", "/campaigns/c1")[1]


def history(dsp):
    return dsp.request("GET", "/campaigns/c1/history")[1]["history"]


def test_timeout_before_commit_client_sees_timeout_and_dsp_never_commits_later(start_dsp):
    dsp = start_dsp(fault_injection=True, hang_seconds=0.6)

    with pytest.raises(TimeoutError):
        set_budget(dsp, fault="timeout_before_commit", timeout=0.3)
    time.sleep(0.9)  # 等過處理程式睡醒的時間:客戶端早已離開,DSP 也不可以在這時才提交

    assert campaign(dsp)["version"] == 1 and campaign(dsp)["budget"] == 100
    assert history(dsp) == []
    assert dsp.request("GET", "/operations/k1")[0] == 404


def test_timeout_after_commit_client_sees_timeout_but_dsp_applied_exactly_once(start_dsp):
    dsp = start_dsp(fault_injection=True)

    with pytest.raises(TimeoutError):
        set_budget(dsp, fault="timeout_after_commit", timeout=0.3)

    assert wait_until(lambda: campaign(dsp)["version"] == 2)  # 提交發生在客戶端放棄前後都算數
    assert campaign(dsp)["budget"] == 150
    assert len(history(dsp)) == 1
    status, body = dsp.request("GET", "/operations/k1")
    assert status == 200 and body["version_after"] == 2


def test_retry_with_same_key_after_commit_timeout_replays_and_does_not_apply_twice(start_dsp):
    dsp = start_dsp(fault_injection=True)
    with pytest.raises(TimeoutError):
        set_budget(dsp, fault="timeout_after_commit", timeout=0.3)

    status, body = set_budget(dsp)  # 沒有故障標頭的正常重試

    assert status == 200 and body["replayed"] is True
    assert len(history(dsp)) == 1 and campaign(dsp)["version"] == 2


def test_fault_header_is_rejected_with_400_and_changes_nothing_when_injection_is_off(start_dsp):
    dsp = start_dsp(fault_injection=False)

    status, body = set_budget(dsp, fault="timeout_after_commit")

    assert status == 400 and body["error"] == "fault_injection_disabled"
    assert campaign(dsp)["version"] == 1 and history(dsp) == []


def test_unknown_fault_mode_is_rejected_even_when_injection_is_on(start_dsp):
    dsp = start_dsp(fault_injection=True)

    status, body = set_budget(dsp, fault="timeout_befor_commit")  # 故意拼錯

    assert status == 400 and body["error"] == "unknown_fault_mode"
    assert history(dsp) == []


def test_transient_5xx_does_not_change_state_and_same_key_retry_then_succeeds(start_dsp):
    dsp = start_dsp(fault_injection=True)

    status, body = set_budget(dsp, fault="transient_5xx")
    assert status == 503 and body["retryable"] is True
    assert history(dsp) == []

    status, body = set_budget(dsp)
    assert status == 200 and body["replayed"] is False
    assert len(history(dsp)) == 1


def test_permanent_validation_error_is_not_retryable_and_changes_nothing(start_dsp):
    dsp = start_dsp(fault_injection=True)

    status, body = set_budget(dsp, fault="permanent_validation_error")

    assert status == 422 and body["retryable"] is False
    assert history(dsp) == []


def test_negative_budget_is_a_422_permanent_rejection(start_dsp):
    dsp = start_dsp()

    status, body = set_budget(dsp, budget=-1)

    assert status == 422 and body["error"] == "validation_rejected"


def test_stale_version_gets_409_and_does_not_overwrite_newer_state(start_dsp):
    dsp = start_dsp()
    set_budget(dsp, budget=150, version=1, key="k1")

    status, body = set_budget(dsp, budget=999, version=1, key="k2")

    assert status == 409 and body["error"] == "version_conflict" and body["retryable"] is False
    assert campaign(dsp)["budget"] == 150


def test_same_key_with_different_payload_is_rejected_with_422(start_dsp):
    dsp = start_dsp()
    set_budget(dsp, budget=150, key="k1")

    status, body = set_budget(dsp, budget=999, key="k1")

    assert status == 422 and body["error"] == "idempotency_conflict"
    assert campaign(dsp)["budget"] == 150


def test_missing_idempotency_key_is_rejected_with_400(start_dsp):
    dsp = start_dsp()

    status, body = dsp.request("POST", BUDGET, {"new_budget": 150, "expected_version": 1})

    assert status == 400 and body["error"] == "missing_idempotency_key"


def test_pause_campaign_over_http(start_dsp):
    dsp = start_dsp()

    status, _ = dsp.request("POST", "/campaigns/c1/pause", {"expected_version": 1},
                            {"Idempotency-Key": "p1"})

    assert status == 200 and campaign(dsp)["status"] == "paused"


def test_query_is_answered_immediately_while_a_slow_request_is_in_flight(start_dsp):
    dsp = start_dsp(fault_injection=True, hang_seconds=1.5)
    slow = threading.Thread(target=lambda: _swallow_timeout(dsp))
    slow.start()
    # 同步點:提交完成(版本變 2)代表慢請求已進入 DSP,並正卡在「提交後的長睡眠」
    assert wait_until(lambda: campaign(dsp)["version"] == 2)

    status, _body = dsp.request("GET", "/operations/nothing", timeout=0.5)

    assert status == 404  # 有回應(而不是逾時)就代表伺服器不是單執行緒
    slow.join()


def _swallow_timeout(dsp):
    with contextlib.suppress(TimeoutError):
        set_budget(dsp, fault="timeout_after_commit", timeout=0.3)


def test_concurrent_same_key_requests_over_http_apply_exactly_once(start_dsp):
    # 請求以柵欄同時放出而同時在途;誰先誰後由 BEGIN IMMEDIATE 的排隊決定
    dsp = start_dsp(fault_injection=True, delay_seconds=0.3)
    workers, barrier, results = 20, threading.Barrier(20), []

    def call():
        barrier.wait()
        results.append(set_budget(dsp, fault="delayed_response", timeout=5.0))

    threads = [threading.Thread(target=call) for _ in range(workers)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert all(status == 200 for status, _ in results)
    assert len(history(dsp)) == 1 and campaign(dsp)["version"] == 2
    assert sum(1 for _, body in results if body["replayed"] is False) == 1


def test_idempotency_record_survives_a_real_dsp_process_restart(start_dsp):
    first = start_dsp()
    set_budget(first, budget=150, key="k1")
    first.stop()

    second = start_dsp()  # 同一個資料庫檔,新的行程

    status, body = set_budget(second, budget=150, key="k1")
    assert status == 200 and body["replayed"] is True
    assert len(history(second)) == 1


def _non_loopback_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # 不會真的送出封包,只是問系統走哪張網卡
        ip = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if ip.startswith("127.") else ip


def test_dsp_refuses_connections_on_non_loopback_addresses(start_dsp):
    ip = _non_loopback_ip()
    if ip is None:
        pytest.skip("這台機器沒有非回送的網路介面可供檢查")
    dsp = start_dsp()
    port = int(dsp.base.rsplit(":", 1)[1])

    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((ip, port), timeout=1.0).close()


def raw_post(dsp, path, headers, body=b"", extra_pairs=()):
    """用 http.client 送出 urllib 做不到的畸形請求,回 (狀態碼, 解析後 JSON 或 None)。"""
    host, port = dsp.base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=3)
    conn.putrequest("POST", path, skip_host="Host" in headers, skip_accept_encoding=True)
    for name, value in list(headers.items()) + list(extra_pairs):
        conn.putheader(name, value)
    conn.endheaders(body)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw) if raw else None


def test_budget_above_the_sqlite_limit_gets_a_typed_422_not_a_dropped_connection(start_dsp):
    dsp = start_dsp()

    status, body = set_budget(dsp, budget=2**70)

    assert status == 422 and body["error"] == "validation_rejected"
    assert campaign(dsp)["version"] == 1


@pytest.mark.parametrize("bad_version", [True, 1.0, "1", None])
def test_bad_expected_version_gets_422_and_changes_nothing(start_dsp, bad_version):
    dsp = start_dsp()

    status, body = dsp.request("POST", BUDGET, {"new_budget": 150, "expected_version": bad_version},
                               HEADERS)

    assert status == 422 and body["error"] == "validation_rejected"
    assert campaign(dsp)["version"] == 1 and history(dsp) == []


def test_missing_expected_version_is_a_validation_error_not_a_version_conflict(start_dsp):
    dsp = start_dsp()

    status, body = dsp.request("POST", BUDGET, {"new_budget": 150}, HEADERS)

    assert status == 422 and body["error"] == "validation_rejected"


@pytest.mark.parametrize("length", ["abc", "-5", "1e3"])
def test_invalid_content_length_gets_400_json(start_dsp, length):
    dsp = start_dsp()

    status, body = raw_post(dsp, "/campaigns/c1/pause",
                            {"Idempotency-Key": "p1", "Content-Length": length})

    assert status == 400 and body["error"] == "invalid_content_length"
    assert campaign(dsp)["status"] == "active"


def test_chunked_transfer_encoding_is_rejected_instead_of_silently_ignored(start_dsp):
    dsp = start_dsp()

    status, body = raw_post(dsp, "/campaigns/c1/pause",
                            {"Idempotency-Key": "p1", "Transfer-Encoding": "chunked"}, b"0\r\n\r\n")

    assert status == 411 and body["error"] == "chunked_not_supported"
    assert campaign(dsp)["status"] == "active"


def test_invalid_json_body_gets_400_and_oversized_body_gets_413(start_dsp):
    dsp = start_dsp()
    headers = {"Idempotency-Key": "k1", "Content-Length": "5"}
    assert raw_post(dsp, BUDGET, headers, b"nope!")[1]["error"] == "invalid_json"

    big = b"{" + b" " * (70 * 1024) + b"}"
    status, body = raw_post(dsp, BUDGET, {"Idempotency-Key": "k1", "Content-Length": str(len(big))},
                            big)
    assert status == 413 and body["error"] == "body_too_large"


def test_huge_digit_string_in_json_is_a_400_not_a_dropped_connection(start_dsp):
    dsp = start_dsp()
    body = b'{"new_budget": ' + b"9" * 5000 + b', "expected_version": 1}'

    status, payload = raw_post(dsp, BUDGET, {"Idempotency-Key": "k1",
                                             "Content-Length": str(len(body))}, body)

    assert status == 400 and payload["error"] == "invalid_json"


@pytest.mark.parametrize("bad_key", ["a/b", "a?b", "x" * 129, "has space"])
def test_idempotency_key_with_unsafe_characters_or_length_is_rejected(start_dsp, bad_key):
    dsp = start_dsp()

    status, body = dsp.request("POST", BUDGET, {"new_budget": 150, "expected_version": 1},
                               {"Idempotency-Key": bad_key})

    assert status == 422 and body["error"] == "validation_rejected"
    assert history(dsp) == []


def test_duplicate_idempotency_key_headers_are_rejected(start_dsp):
    dsp = start_dsp()
    body = json.dumps({"new_budget": 150, "expected_version": 1}).encode()

    status, payload = raw_post(dsp, BUDGET, {"Content-Length": str(len(body)),
                                             "Idempotency-Key": "k1"}, body,
                               extra_pairs=[("Idempotency-Key", "k2")])

    assert status == 400 and payload["error"] == "duplicate_header"
    assert history(dsp) == []


def test_requests_with_a_non_loopback_host_header_are_rejected(start_dsp):
    dsp = start_dsp()
    body = json.dumps({"new_budget": 150, "expected_version": 1}).encode()

    status, payload = raw_post(dsp, BUDGET, {"Host": "evil.example", "Idempotency-Key": "k1",
                                             "Content-Length": str(len(body))}, body)

    assert status == 400 and payload["error"] == "invalid_host"
    assert history(dsp) == [] and campaign(dsp)["version"] == 1


def test_unsupported_methods_and_unknown_routes_answer_in_json(start_dsp):
    dsp = start_dsp()

    put_status, put_body = dsp.request("PUT", "/campaigns/c1")
    missing_status, missing_body = dsp.request("GET", "/nowhere")

    assert put_status == 501 and "error" in put_body
    assert missing_status == 404 and missing_body["error"] == "not_found"


def test_unknown_campaign_and_its_history_return_404(start_dsp):
    dsp = start_dsp()

    assert dsp.request("GET", "/campaigns/ghost")[1]["error"] == "campaign_not_found"
    assert dsp.request("GET", "/campaigns/ghost/history")[1]["error"] == "campaign_not_found"


def test_history_entries_carry_received_and_committed_times_over_http(start_dsp):
    dsp = start_dsp()
    set_budget(dsp)

    entry = history(dsp)[0]

    assert entry["received_at"] and entry["committed_at"]
    assert entry["committed_at"] >= entry["received_at"] and entry["version_after"] == 2


def test_stale_version_leaves_version_and_history_untouched(start_dsp):
    dsp = start_dsp()
    set_budget(dsp, budget=150, version=1, key="k1")

    set_budget(dsp, budget=999, version=1, key="k2")

    assert campaign(dsp)["version"] == 2 and len(history(dsp)) == 1


def test_pause_bumps_the_version_and_lookup_by_key_is_not_a_replay(start_dsp):
    dsp = start_dsp()
    dsp.request("POST", "/campaigns/c1/pause", {"expected_version": 1}, {"Idempotency-Key": "p1"})

    status, body = dsp.request("GET", "/operations/p1")

    assert campaign(dsp)["version"] == 2
    assert status == 200 and body["replayed"] is False


def test_injected_and_real_validation_errors_have_different_error_codes(start_dsp):
    dsp = start_dsp(fault_injection=True)

    injected = set_budget(dsp, fault="permanent_validation_error")[1]["error"]
    real = set_budget(dsp, budget=-1)[1]["error"]

    assert injected == "injected_validation_error" and real == "validation_rejected"


def test_lock_contention_past_the_wait_limit_returns_503_retryable_and_reads_still_work(
    start_dsp, tmp_path
):
    dsp = start_dsp(busy_timeout_seconds=0.3)
    holder = sqlite3.connect(tmp_path / "dsp.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")

    status, body = set_budget(dsp)
    read_status, _ = dsp.request("GET", "/campaigns/c1")

    holder.execute("ROLLBACK")
    assert status == 503 and body == {"error": "store_busy", "retryable": True}
    assert read_status == 200
    assert set_budget(dsp)[0] == 200  # 鎖放開後同一把鍵可以重試


def test_unexpected_internal_failure_returns_a_500_json_instead_of_dropping_the_connection(
    start_dsp, tmp_path
):
    dsp = start_dsp()
    for leftover in ("dsp.db-wal", "dsp.db-shm"):  # 連同 WAL 一起清掉,否則資料庫仍讀得出來
        (tmp_path / leftover).unlink(missing_ok=True)
    (tmp_path / "dsp.db").write_bytes(b"not a database" * 200)  # DSP 執行中儲存被弄壞

    status, body = set_budget(dsp)

    assert status == 500 and body["error"] == "internal_error" and body["retryable"] is False


@pytest.mark.parametrize("host", ["LOCALHOST", "Localhost", "127.0.0.1"])
def test_host_header_matching_ignores_case(start_dsp, host):
    dsp = start_dsp()
    port = dsp.base.rsplit(":", 1)[1]

    status, _ = raw_get(dsp, "/campaigns/c1", {"Host": f"{host}:{port}"})

    assert status == 200


def test_http_1_0_request_without_a_host_header_is_allowed_but_a_bad_host_is_not(start_dsp):
    dsp = start_dsp()
    port = int(dsp.base.rsplit(":", 1)[1])

    def send(raw: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
            conn.sendall(raw)
            data = b""
            while chunk := conn.recv(4096):
                data += chunk
            return data

    no_host = send(b"GET /campaigns/c1 HTTP/1.0\r\n\r\n")
    evil_host = send(b"GET /campaigns/c1 HTTP/1.1\r\nHost: evil.example\r\n\r\n")
    assert b" 200 " in no_host.split(b"\r\n")[0]
    assert b" 400 " in evil_host.split(b"\r\n")[0]


def raw_get(dsp, path, headers):
    host, port = dsp.base.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=3)
    conn.putrequest("GET", path, skip_host="Host" in headers, skip_accept_encoding=True)
    for name, value in headers.items():
        conn.putheader(name, value)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, json.loads(body)


def test_content_length_with_surrounding_spaces_is_accepted(start_dsp):
    dsp = start_dsp()
    body = json.dumps({"expected_version": 1}).encode()

    status, _ = raw_post(dsp, "/campaigns/c1/pause",
                         {"Idempotency-Key": "p1", "Content-Length": f" {len(body)} "}, body)

    assert status == 200


def test_absurdly_long_content_length_digits_get_413_not_a_500(start_dsp):
    dsp = start_dsp()

    status, body = raw_post(dsp, "/campaigns/c1/pause",
                            {"Idempotency-Key": "p1", "Content-Length": "9" * 5000})

    assert status == 413 and body["error"] == "body_too_large"


def test_body_that_never_finishes_arriving_gets_408_and_no_traceback_noise(start_dsp):
    dsp = start_dsp(socket_timeout_seconds=1.0)
    port = int(dsp.base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(f"POST /campaigns/c1/pause HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                     "Idempotency-Key: p1\r\nContent-Length: 100\r\n\r\n{".encode())
        reply = b""
        while chunk := conn.recv(4096):
            reply += chunk

    assert b" 408 " in reply.split(b"\r\n")[0] and b"request_timeout" in reply
    assert campaign(dsp)["status"] == "active"


def test_idle_connection_that_sends_nothing_is_closed_by_the_socket_timeout(start_dsp):
    dsp = start_dsp(socket_timeout_seconds=1.0)
    port = int(dsp.base.rsplit(":", 1)[1])

    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", port), timeout=6) as idle:
        closed = idle.recv(1)  # 伺服器逾時後關閉連線,recv 回 b""
    elapsed = time.monotonic() - started

    assert closed == b"" and 0.8 <= elapsed < 4.0


def test_a_burst_of_connections_beyond_the_default_backlog_is_still_accepted(start_dsp):
    dsp = start_dsp()
    port = int(dsp.base.rsplit(":", 1)[1])
    os.kill(dsp.proc.pid, signal.SIGSTOP)  # 凍結 accept 迴圈,連線只能排在佇列裡
    opened = []
    try:
        for _ in range(100):
            with contextlib.suppress(OSError):
                opened.append(socket.create_connection(("127.0.0.1", port), timeout=0.5))
    finally:
        os.kill(dsp.proc.pid, signal.SIGCONT)
        for conn in opened:
            conn.close()

    assert len(opened) >= 50  # 預設佇列只有 5,加大到 128 才會通過


def test_query_string_is_ignored_when_routing(start_dsp):
    dsp = start_dsp()

    status, body = dsp.request("GET", "/campaigns/c1?verbose=1")

    assert status == 200 and body["id"] == "c1"


def test_error_table_lookup_follows_the_inheritance_chain():
    class BusyForAWhile(StoreBusy):
        pass

    assert error_entry(BusyForAWhile()) == (503, "store_busy", True)
    assert error_entry(TransientError()) == (503, "transient_error", True)
    assert error_entry(PermanentError()) == (500, "dsp_error", False)


def seed_metrics(tmp_path, **fields):
    from rtb.dsp.store import CampaignStore

    store = CampaignStore(tmp_path / "dsp.db")
    store.seed_metrics("c1", "1d", **fields)
    store.close()


def test_get_metrics_returns_raw_counts_and_keeps_missing_fields_as_null(start_dsp, tmp_path):
    dsp = start_dsp()
    seed_metrics(tmp_path, impressions=1000, clicks=None, spend=12.5)

    status, body = dsp.request("GET", "/campaigns/c1/metrics?window=1d")

    assert status == 200 and body["impressions"] == 1000 and body["spend"] == 12.5
    assert body["clicks"] is None and body["revenue"] is None
    assert "ctr" not in body  # DSP 只給事實,比率由我們自己算


@pytest.mark.parametrize("query,expected_status,expected_error", [
    ("", 422, "validation_rejected"),
    ("?window=5years", 422, "validation_rejected"),
    ("?window=1d&window=7d", 400, "duplicate_query_parameter"),
    ("?window=7d", 404, "metrics_not_found"),
])
def test_get_metrics_rejects_bad_windows_with_typed_errors(
    start_dsp, tmp_path, query, expected_status, expected_error
):
    dsp = start_dsp()
    seed_metrics(tmp_path, impressions=1)

    status, body = dsp.request("GET", f"/campaigns/c1/metrics{query}")

    assert status == expected_status and body["error"] == expected_error


def test_get_metrics_for_an_unknown_campaign_is_a_404(start_dsp):
    dsp = start_dsp()

    status, body = dsp.request("GET", "/campaigns/ghost/metrics?window=1d")

    assert status == 404 and body["error"] == "campaign_not_found"


def test_a_path_starting_with_two_slashes_is_never_routed_as_if_it_had_a_host(start_dsp):
    # 標準函式庫會把開頭的多個斜線收合;這條測試鎖住結果://網域/... 不會被當成網域而剝掉
    target = "//evil.example/campaigns/c1"
    dsp = start_dsp()
    port = int(dsp.base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
        conn.sendall(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        reply = b""
        while chunk := conn.recv(4096):
            reply += chunk

    assert b" 404 " in reply.split(b"\r\n")[0]
