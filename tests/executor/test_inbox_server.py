"""提案收件口:對應設計審通過的 S1~S18。每個測試對應計劃裡一條合約。"""

import ast
import http.client
import inspect
import json
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from rtb.executor import inbox_server, inbox_store
from rtb.sqlitekit import connect
from tests.domain.proposal_samples import REJECTED_OVERRIDES, valid


def budget(value, **overrides):
    return valid(requested_change={"new_budget": value}, **overrides)


def test_a_first_valid_proposal_is_accepted_with_201(start_inbox):
    inbox = start_inbox()

    status, data = inbox.post(valid())

    assert status == 201
    assert data["status"] == "accepted" and data["state"] == "pending"
    assert (data["task_id"], data["revision"], data["replayed"]) == ("t1", 1, False)
    assert len(data["content_hash"]) == 64
    assert inbox.rows() == [("t1", 1, "pending")]


# ---- S1 ----
def test_a_resent_identical_proposal_returns_the_current_state_and_adds_nothing_even_when_full_or_superseded(  # noqa: E501
        start_inbox, clock):
    inbox = start_inbox(max_pending=1)
    first = inbox.post(valid())[1]

    status, again = inbox.post(valid())  # 已滿(上限 1、已有 1 個待處理)也照樣回重送結果
    assert (status, again["replayed"], again["state"]) == (200, True, "pending")
    assert again["content_hash"] == first["content_hash"]

    assert inbox.post(budget(160, revision=2))[0] == 201  # 取代 r1
    status, again = inbox.post(valid())
    assert (status, again["state"]) == (200, "superseded")

    clock.advance(hours=1)  # 全部過期
    assert inbox.post(budget(160, revision=2))[1]["state"] == "expired"
    assert len(inbox.rows()) == 2


# ---- S2 ----
def test_a_different_proposal_for_the_same_key_is_rejected_and_the_original_is_untouched(
        start_inbox):
    inbox = start_inbox()
    inbox.post(valid())
    before = inbox.rows("SELECT content_hash, payload FROM proposals")

    status, data = inbox.post(budget(999))

    assert (status, data["error"]) == (409, "content_conflict")
    assert inbox.rows("SELECT content_hash, payload FROM proposals") == before
    assert ("content_conflict",) in inbox.rows("SELECT code FROM inbox_events")


def test_a_different_proposal_for_a_superseded_or_expired_key_is_still_a_conflict(
        start_inbox, clock):
    """2026-09-22 第二輪合約審計指出:原本的衝突測試只打「待處理」的那份;把內容比對改成
    「只在待處理時才比、已退場的直接回現況」,測試照樣綠,換掉內容的請求會被當成重送接受。"""
    inbox = start_inbox()
    inbox.post(valid())
    inbox.post(budget(160, revision=2))  # r1 被取代

    status, data = inbox.post(budget(999))  # 對已被取代的 r1 送不同內容
    assert (status, data["error"]) == (409, "content_conflict")

    clock.advance(hours=1)  # r2 過期
    status, data = inbox.post(budget(777, revision=2))  # 對已過期的 r2 送不同內容
    assert (status, data["error"]) == (409, "content_conflict")


# ---- S3 ----
def test_a_revision_that_is_not_exactly_the_next_one_is_rejected_with_the_current_highest(
        start_inbox):
    inbox = start_inbox()

    status, data = inbox.post(valid(revision=2))  # 第一份必須是 1
    assert (status, data["error"], data["highest_revision"]) == (409, "revision_out_of_order", 0)

    inbox.post(valid())
    for bad in (3, 999_999):  # 解析器本身也把修訂序號限制在 100 萬以內
        status, data = inbox.post(valid(revision=bad))
        assert (status, data["error"], data["highest_revision"]) == (
            409, "revision_out_of_order", 1)
    assert inbox.post(valid(revision=10**9))[0] == 400  # 超過解析器上限:根本進不了收件交易
    assert inbox.rows() == [("t1", 1, "pending")]
    assert inbox.post(valid(revision=2))[0] == 201  # 正確的下一個序號沒有被卡死


# ---- S4 ----
def test_accepting_the_next_revision_supersedes_the_previous_one_even_when_the_inbox_is_full(
        start_inbox):
    inbox = start_inbox(max_pending=1)
    inbox.post(valid())

    status, _ = inbox.post(budget(160, revision=2))

    assert status == 201
    assert inbox.rows() == [("t1", 1, "superseded"), ("t1", 2, "pending")]
    assert inbox.post(valid(task_id="t2"))[1]["error"] == "inbox_full"  # 名額仍然只有 1


# ---- S5 ----
def test_malformed_or_oversized_bodies_are_rejected_without_writing_or_crashing(start_inbox):
    inbox = start_inbox()
    bad_requests = [
        {"raw": b"not json"}, {"raw": b"[1,2]"}, {"raw": b""},
        {"raw": b'{"x":"' + b"a" * (70 * 1024) + b'"}'},
        {"body": {**valid(), "tool_name": "delete_all"}},
        {"body": valid(revision="1")}, {"body": valid(risk_summary="x" * 20_000)},
    ]

    for request in bad_requests:
        status, data = inbox.post(**request)
        assert 400 <= status < 500 and data["retryable"] is False, request
    assert inbox.rows() == []
    assert inbox.post(valid())[0] == 201  # 行程還活著


# ---- S6 ----
def test_a_full_inbox_answers_503_inbox_full_and_writes_nothing(start_inbox):
    inbox = start_inbox(max_pending=2)
    inbox.post(valid(task_id="t1"))
    inbox.post(valid(task_id="t2"))

    status, data = inbox.post(valid(task_id="t3"))

    assert (status, data["error"], data["retryable"]) == (503, "inbox_full", True)
    assert [row[0] for row in inbox.rows()] == ["t1", "t2"]


# ---- S7 ----
def test_a_busy_database_answers_503_busy_and_leaves_nothing_behind(start_inbox):
    inbox = start_inbox(busy_timeout_seconds=0.1)
    holder = sqlite3.connect(inbox.db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")

    status, data = inbox.post(valid())

    assert (status, data["error"], data["retryable"]) == (503, "busy", True)
    holder.execute("ROLLBACK")
    holder.close()
    assert inbox.rows() == []
    assert inbox.post(valid())[0] == 201


# ---- S8 ----
def _raw_post(inbox, body, fault):
    conn = http.client.HTTPConnection("127.0.0.1", inbox.port, timeout=5)
    conn.request("POST", "/proposals", body=json.dumps(body).encode(),
                 headers={"Content-Type": "application/json", "X-Fault": fault})
    try:
        conn.getresponse()
    except (http.client.RemoteDisconnected, ConnectionError):
        return "no_response"
    finally:
        conn.close()
    return "responded"


def test_a_crash_before_commit_loses_nothing_and_a_crash_after_commit_is_answered_by_the_replay(
        start_inbox):
    inbox = start_inbox(fault_injection=True)

    assert _raw_post(inbox, valid(), "crash_before_commit") == "no_response"
    assert inbox.rows() == []  # 沒提交就沒有任何痕跡,也就不可能已經回過 2xx
    assert inbox.post(valid())[0] == 201  # 重送被當成第一次接受

    assert _raw_post(inbox, valid(task_id="t2"), "crash_after_commit") == "no_response"
    assert ("t2", 1, "pending") in inbox.rows()  # 提交了,只是呼叫者不知道
    status, data = inbox.post(valid(task_id="t2"))
    assert (status, data["replayed"]) == (200, True)


def test_fault_headers_are_refused_unless_the_process_was_started_with_fault_injection(
        start_inbox):
    inbox = start_inbox(fault_injection=False)

    status, data = inbox.post(valid(), headers={"X-Fault": "crash_after_commit"})

    assert (status, data["error"]) == (400, "fault_injection_disabled")
    assert inbox.rows() == []


# ---- S9 ----
def test_concurrent_requests_for_the_same_key_accept_exactly_one(start_inbox):
    inbox = start_inbox()
    results = []

    def run(body):
        results.append(inbox.post(body)[0])

    same = [threading.Thread(target=run, args=(valid(),)) for _ in range(12)]
    for t in same:
        t.start()
    for t in same:
        t.join()
    assert sorted(results) == [200] * 11 + [201]

    results.clear()
    different = [threading.Thread(target=run, args=(budget(200 + i, task_id="t2"),))
                 for i in range(12)]
    for t in different:
        t.start()
    for t in different:
        t.join()
    assert sorted(results) == [201] + [409] * 11
    assert len([r for r in inbox.rows() if r[0] == "t2"]) == 1


# ---- S10 ----
def test_an_already_expired_proposal_is_rejected_with_422_and_not_stored(start_inbox, clock):
    inbox = start_inbox()
    clock.advance(hours=1)  # 現在 13:05,提案 12:30 就到期

    status, data = inbox.post(valid())

    assert (status, data["error"], data["retryable"]) == (422, "expired_proposal", False)
    assert inbox.rows() == []
    assert ("expired_proposal",) in inbox.rows("SELECT code FROM inbox_events")


# ---- S11 ----
def test_a_pending_proposal_that_expires_is_marked_expired_and_frees_its_slot(
        start_inbox, clock):
    inbox = start_inbox(max_pending=1)
    inbox.post(valid())
    clock.advance(minutes=40)  # 12:45:t1 已過期(12:30)
    later = valid(task_id="t2", decision_created_at="2026-09-22T12:40:00+00:00",
                  decision_expires_at="2026-09-22T13:30:00+00:00")

    status, _ = inbox.post(later)

    assert status == 201
    assert inbox.rows() == [("t1", 1, "expired"), ("t2", 1, "pending")]


# ---- S12 ----
def test_the_inbox_rejects_foreign_hosts_origins_content_types_and_non_loopback_binding(
        start_inbox, tmp_path):
    inbox = start_inbox()

    assert inbox.post(valid(), headers={"Host": "evil.example"})[1]["error"] == "invalid_host"
    status, data = inbox.post(valid(), headers={"Origin": "http://evil.example"})
    assert (status, data["error"]) == (403, "origin_not_allowed")
    for content_type in ("text/plain", "application/x-www-form-urlencoded", ""):
        status, data = inbox.post(valid(), headers={"Content-Type": content_type})
        assert (status, data["error"]) == (415, "unsupported_media_type"), content_type
    charset = {"Content-Type": "application/json; charset=utf-8"}
    assert inbox.post(valid(), headers=charset)[0] == 201
    assert inbox.rows() == [("t1", 1, "pending")]
    with pytest.raises(ValueError):
        inbox_server.InboxServer(tmp_path / "other.db", host="0.0.0.0")  # noqa: S104


def test_only_post_to_the_proposals_path_is_served(start_inbox):
    inbox = start_inbox()

    assert inbox.post(valid(), path="/elsewhere")[0] == 404
    conn = http.client.HTTPConnection("127.0.0.1", inbox.port, timeout=3)
    conn.request("GET", "/proposals")
    assert conn.getresponse().status == 404
    conn.close()


# ---- S13 ----
def test_error_responses_never_echo_request_content(start_inbox):
    inbox = start_inbox()
    marker = "SECRET_MARKER_7f3a"
    inbox.post(valid())

    responses = [
        inbox.post({**valid(), marker: "value"}),
        inbox.post(valid(risk_summary=marker + "x" * 600)),
        inbox.post(budget(999, risk_summary=marker)),  # 內容衝突
        inbox.post(valid(revision=5, risk_summary=marker)),  # 序號不對
        inbox.post(raw=marker.encode()),
    ]

    for status, data in responses:
        assert status >= 400
        assert marker not in json.dumps(data)
        assert set(data) <= {"error", "retryable", "highest_revision"}


# ---- S14 ----
def test_the_event_log_is_bounded_fixed_shape_and_never_affects_the_response(
        start_inbox, monkeypatch):
    monkeypatch.setattr(inbox_store, "MAX_EVENTS_PER_CODE", 5)
    inbox = start_inbox()
    inbox.post(valid())
    for i in range(20):
        inbox.post(budget(300 + i))  # 20 次衝突

    columns = [r[1] for r in inbox.rows("PRAGMA table_info(inbox_events)")]
    events = inbox.rows("SELECT code, task_id, revision, content_hash FROM inbox_events")
    assert columns == ["id", "at", "task_id", "revision", "code", "content_hash"]
    assert len(events) == 5
    assert {e[0] for e in events} <= inbox_store.EVENT_CODES

    def failing(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(inbox_store.InboxStore, "_write_event", failing)
    status, data = inbox.post(budget(999))
    assert (status, data["error"]) == (409, "content_conflict")  # 事件寫不進去,回應照舊


def test_an_event_never_stores_untrusted_text_only_a_valid_task_id_or_nothing(start_inbox):
    inbox = start_inbox()

    inbox.post({**valid(task_id="../../etc/passwd; DROP TABLE x")})
    inbox.post(raw=b"garbage")

    for task_id, _revision in inbox.rows("SELECT task_id, revision FROM inbox_events"):
        assert task_id is None


# ---- S15 ----
def test_proposals_survive_a_restart_and_a_resend_is_a_replay(start_inbox):
    first = start_inbox()
    first.post(valid())
    db = first.db
    first.stop()

    second = start_inbox()  # 同一個資料庫檔,新的伺服器
    assert second.db == db

    status, data = second.post(valid())
    assert (status, data["replayed"], data["state"]) == (200, True, "pending")


# ---- S16 ----
def test_every_sample_the_domain_parser_rejects_is_also_rejected_by_the_inbox_and_the_inbox_defines_no_validators(  # noqa: E501
        start_inbox, monkeypatch):
    inbox = start_inbox()
    calls = []
    real = inbox_server.parse_proposal

    def spy(raw):
        calls.append(1)
        return real(raw)

    monkeypatch.setattr(inbox_server, "parse_proposal", spy)

    for override in REJECTED_OVERRIDES:
        status, data = inbox.post(valid(**override))
        assert status == 400 and data["error"] == "invalid_proposal", override
    assert len(calls) == len(REJECTED_OVERRIDES)  # 每個請求都經過同一個解析函式
    assert inbox.rows() == []

    for module in (inbox_server, inbox_store):  # 收件口模組沒有自己的欄位驗證
        tree = ast.parse(inspect.getsource(module))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    and n.module and n.module.startswith("rtb.domain") for a in n.names}
        # 取件要用冪等鍵與嘗試狀態分流(佇列語意);失敗嘗試的結果代碼決定擋下原因(Phase 5 [S310]),
        # 這三個都不是欄位驗證
        allowed = {"parse_proposal", "content_hash", "Proposal", "MAX_DECISION_LIFETIME",
                   "operation_key", "AttemptState", "OutcomeCode"}
        assert imported <= allowed, imported
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import)
                    and any(a.name == "re" for a in n.names)]


def test_the_inbox_uses_the_domain_parser_for_every_request_module_level_check():
    assert inbox_server.parse_proposal.__module__ == "rtb.domain.proposal"


# ---- S18 ----
def test_a_stalled_connection_is_dropped_after_the_socket_timeout(start_inbox):
    inbox = start_inbox(socket_timeout_seconds=0.3)
    sock = socket.create_connection(("127.0.0.1", inbox.port), timeout=3)
    sock.sendall(b"POST /proposals HTTP/1.1\r\nHost: 127.0.0.1")
    started = time.monotonic()

    sock.recv(1024)

    assert time.monotonic() - started < 2.0
    sock.close()
    assert inbox.post(valid())[0] == 201




# ---- 第 1 輪代碼審折入 ----
def test_an_expired_pending_proposal_is_marked_even_when_the_request_that_noticed_it_is_rejected(
        start_inbox, clock):
    inbox = start_inbox()
    inbox.post(valid())
    clock.advance(minutes=40)  # t1 已過期(12:30)

    status, _ = inbox.post(valid(task_id="t2", revision=2))  # 序號不對,會被拒收

    assert status == 409
    assert inbox.rows() == [("t1", 1, "expired")]  # 拒收不能讓過期標記跟著回滾


def test_a_proposal_expiring_exactly_now_is_expired(start_inbox, clock):
    inbox = start_inbox()
    inbox.post(valid())
    clock.now = clock.now.replace(hour=12, minute=30)  # 剛好等於到期時間

    assert inbox.post(valid(task_id="t2", decision_expires_at="2026-09-22T12:30:00+00:00"))[
        1]["error"] == "expired_proposal"
    inbox.post(valid(task_id="t3", revision=2))
    assert inbox.rows()[0] == ("t1", 1, "expired")  # 到期時間等於現在也算已過期


def test_the_next_revision_is_accepted_after_the_previous_one_has_expired(start_inbox, clock):
    inbox = start_inbox(max_pending=1)
    inbox.post(valid())
    clock.advance(minutes=40)
    later = valid(revision=2, decision_created_at="2026-09-22T12:40:00+00:00",
                  decision_expires_at="2026-09-22T13:30:00+00:00")

    status, _ = inbox.post(later)

    assert status == 201
    assert inbox.rows() == [("t1", 1, "expired"), ("t1", 2, "pending")]


def test_an_expiry_far_beyond_now_cannot_pin_a_slot(start_inbox):
    inbox = start_inbox(max_pending=2)
    far = {"decision_created_at": "2099-01-01T00:00:00+00:00",
           "decision_expires_at": "2099-01-01T00:30:00+00:00"}  # 有效期合法,但離現在很遠

    status, data = inbox.post(valid(**far))

    assert (status, data["error"]) == (422, "expiry_too_far")
    assert inbox.rows() == []


def test_the_current_time_is_read_only_after_the_write_lock_is_held(tmp_path):
    seen = []
    holder = connect(tmp_path / "s.db", schema=inbox_store.SCHEMA)
    store = inbox_store.InboxStore(tmp_path / "s.db")
    proposal = inbox_server.parse_proposal(valid()).proposal

    def clock():
        try:  # 我們的寫入交易已經持有鎖,別的連線就拿不到
            holder.execute("BEGIN IMMEDIATE")
            holder.execute("ROLLBACK")
            seen.append("lock_free")
        except sqlite3.OperationalError:
            seen.append("lock_held")
        return inbox_store.utc_now().replace(year=2026, month=9, day=22, hour=12, minute=5)

    holder.execute("PRAGMA busy_timeout=0")
    store.accept(proposal, clock)

    assert seen == ["lock_held"]
    holder.close()
    store.close()


def test_an_invalid_proposal_is_a_400_even_when_the_database_is_locked_and_writes_no_event(
        start_inbox):
    inbox = start_inbox(busy_timeout_seconds=0.1)
    holder = sqlite3.connect(inbox.db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")

    status, data = inbox.post({**valid(), "tool_name": "x"})

    assert (status, data["error"]) == (400, "invalid_proposal")  # 沒碰資料庫,所以不會被鎖擋住
    holder.execute("ROLLBACK")
    holder.close()
    assert inbox.rows("SELECT code FROM inbox_events") == []  # 無效請求不進事件紀錄


def test_repeating_the_identical_rejection_does_not_flood_the_event_log(start_inbox):
    inbox = start_inbox()
    inbox.post(valid())
    for _ in range(10):
        inbox.post(budget(999))  # 同一個衝突重複十次

    events = inbox.rows("SELECT code, task_id, revision, content_hash FROM inbox_events")
    assert len(events) == 1
    assert events[0][0] == "content_conflict" and len(events[0][3]) == 64


def test_the_events_of_the_other_rejections_are_recorded_with_their_codes(start_inbox):
    inbox = start_inbox(max_pending=1)
    inbox.post(valid())
    inbox.post(valid(task_id="t2", revision=4))  # 序號不對
    inbox.post(valid(task_id="t3"))  # 滿了
    far = {"decision_created_at": "2099-01-01T00:00:00+00:00",
           "decision_expires_at": "2099-01-01T00:30:00+00:00"}
    inbox.post(valid(task_id="t4", **far))

    codes = {row[0] for row in inbox.rows("SELECT code FROM inbox_events")}
    assert codes == {"revision_out_of_order", "inbox_full", "expiry_too_far"}


def test_a_request_that_cannot_get_the_lock_leaves_no_half_written_row(start_inbox):
    inbox = start_inbox(busy_timeout_seconds=0.1)
    holder = sqlite3.connect(inbox.db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")

    inbox.post(valid())
    holder.execute("ROLLBACK")
    holder.close()

    assert inbox.rows() == [] and inbox.rows("SELECT code FROM inbox_events") == []


@pytest.mark.parametrize("value", [0, -1])
def test_a_non_positive_pending_limit_is_refused_at_startup(tmp_path, value):
    with pytest.raises(ValueError):
        inbox_server.InboxServer(tmp_path / "x.db", max_pending=value)


def test_starting_while_the_database_is_locked_refuses_cleanly_and_releases_the_port(
        tmp_path, capsys):
    path = tmp_path / "locked.db"
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE t (id INTEGER)")
    holder.execute("BEGIN EXCLUSIVE")  # 還沒切成 WAL,建表前的初始化會撞上獨佔鎖

    with pytest.raises(SystemExit) as exit_info:
        inbox_server.main(["--db", str(path), "--busy-timeout-seconds", "0.05"])

    assert exit_info.value.code == 2
    assert "拒絕啟動" in capsys.readouterr().err
    holder.execute("ROLLBACK")
    holder.close()


def test_a_request_without_a_host_header_is_refused_even_over_http_1_0(start_inbox):
    inbox = start_inbox()
    sock = socket.create_connection(("127.0.0.1", inbox.port), timeout=3)
    sock.sendall(b"POST /proposals HTTP/1.0\r\nContent-Type: application/json\r\n"
                 b"Content-Length: 2\r\n\r\n{}")
    raw = b""
    while chunk := sock.recv(1024):
        raw += chunk
    sock.close()

    assert b" 400 " in raw.split(b"\r\n")[0] and b"invalid_host" in raw
    assert inbox.rows() == []


def test_edge_cases_of_headers_and_paths_are_all_refused(start_inbox):
    inbox = start_inbox(fault_injection=True)

    assert inbox.post(valid(), headers={"Origin": ""})[1]["error"] == "origin_not_allowed"
    unknown = inbox.post(valid(), headers={"X-Fault": "no_such_mode"})
    assert unknown[1]["error"] == "unknown_fault_mode"
    assert inbox.post(valid(), path="/proposals/x")[0] == 404
    assert inbox.post(valid(), path="/proposalsx")[0] == 404
    assert inbox.post(valid(), headers={"Content-Type": "application/jsonx"})[0] == 415
    assert inbox.post(valid(), headers={"Content-Type": "APPLICATION/JSON"})[0] == 201


def test_the_executor_and_dsp_packages_may_not_import_each_other():
    root = Path(__file__).resolve().parents[2] / "src" / "rtb"
    for folder, banned in (("executor", "rtb.dsp"), ("dsp", "rtb.executor")):
        config = root / folder / "ruff.toml"
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(config.parent / "probe.py"), "-"],
            input=f"import {banned}\n", capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (folder, result.stdout)


def test_a_failed_startup_releases_the_listening_socket(tmp_path, monkeypatch):
    path = tmp_path / "locked.db"
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE t (id INTEGER)")
    holder.execute("BEGIN EXCLUSIVE")
    closed = []
    real_close = inbox_server.KitServer.server_close
    monkeypatch.setattr(inbox_server.KitServer, "server_close",
                        lambda self: (closed.append(1), real_close(self))[1])

    with pytest.raises(inbox_store.InboxRejected):
        inbox_server.InboxServer(path, busy_timeout_seconds=0.05)

    assert closed == [1]
    holder.execute("ROLLBACK")
    holder.close()


def test_a_rejected_request_never_reaches_the_before_commit_hook(tmp_path):
    store = inbox_store.InboxStore(tmp_path / "s.db")
    proposal = inbox_server.parse_proposal(valid()).proposal
    other = inbox_server.parse_proposal(budget(999)).proposal
    calls = []
    clock = lambda: inbox_store.utc_now().replace(  # noqa: E731
        year=2026, month=9, day=22, hour=12, minute=5)
    store.accept(proposal, clock)

    with pytest.raises(inbox_store.ContentConflict):
        store.accept(other, clock, before_commit=lambda: calls.append(1))

    assert calls == []
    store.close()


def test_flooding_one_kind_of_event_cannot_wash_out_the_other_kinds(start_inbox, monkeypatch):
    monkeypatch.setattr(inbox_store, "MAX_EVENTS_PER_CODE", 3)
    inbox = start_inbox()
    inbox.post(valid(task_id="t9", revision=4))  # 一筆「序號不對」的事件
    inbox.post(valid())
    for i in range(30):
        inbox.post(budget(300 + i))  # 30 種不同的衝突

    counts = dict(inbox.rows("SELECT code, COUNT(*) FROM inbox_events GROUP BY code"))
    assert counts == {"revision_out_of_order": 1, "content_conflict": 3}


# ---- 第 3 輪:列數上限與清理 ----
def _chain(inbox, task_id, count):
    """同一個任務連續送 count 個修訂(每個取代前一個),回傳最後一次的回應。"""
    result = None
    for revision in range(1, count + 1):
        result = inbox.post(valid(task_id=task_id, revision=revision,
                                  requested_change={"new_budget": 100 + revision}))
    return result


def test_a_task_cannot_be_revised_without_limit(start_inbox, monkeypatch):
    monkeypatch.setattr(inbox_store, "MAX_REVISIONS_PER_TASK", 4)
    inbox = start_inbox()

    assert _chain(inbox, "t1", 4)[0] == 201
    status, data = inbox.post(valid(revision=5, requested_change={"new_budget": 999}))

    assert (status, data["error"], data["retryable"]) == (409, "too_many_revisions", False)
    assert len(inbox.rows()) == 4


def test_the_table_cannot_grow_past_the_row_limit_and_old_finished_tasks_are_purged(
        start_inbox, clock, monkeypatch):
    monkeypatch.setattr(inbox_store, "MAX_ROWS", 6)
    inbox = start_inbox(max_pending=8)
    for task in ("a", "b", "c"):
        assert _chain(inbox, task, 2)[0] == 201  # 三個任務、每個兩個修訂:6 列,各有 1 個待處理

    status, data = inbox.post(valid(task_id="d"))
    assert (status, data["error"], data["retryable"]) == (503, "inbox_full", True)  # 列數滿了

    clock.advance(hours=1, minutes=30)  # 全部到期(待處理也標為已過期),但還沒到保留期限
    assert inbox.post(valid(task_id="d", decision_created_at="2026-09-22T13:30:00+00:00",
                            decision_expires_at="2026-09-22T14:00:00+00:00"))[0] == 503

    clock.advance(hours=1)  # 超過保留期限:整批「沒有待處理」的舊任務被清掉
    fresh = valid(task_id="d", decision_created_at="2026-09-22T14:30:00+00:00",
                  decision_expires_at="2026-09-22T15:00:00+00:00")
    assert inbox.post(fresh)[0] == 201
    assert [row[0] for row in inbox.rows()] == ["d"]


def test_a_purged_proposal_that_is_sent_again_is_expired_never_accepted_as_new(
        start_inbox, clock):
    inbox = start_inbox()
    inbox.post(valid())
    clock.advance(hours=4)  # 到期並超過保留期限
    inbox.post(valid(task_id="other", decision_created_at="2026-09-22T16:00:00+00:00",
                     decision_expires_at="2026-09-22T16:30:00+00:00"))  # 觸發清理

    status, data = inbox.post(valid())  # 舊提案原封不動再送

    assert (status, data["error"]) == (422, "expired_proposal")
    assert [row[0] for row in inbox.rows()] == ["other"]


def test_a_task_with_a_pending_proposal_is_never_purged(start_inbox, clock):
    inbox = start_inbox()
    inbox.post(valid())
    fresh = valid(task_id="t2", decision_created_at="2026-09-22T12:05:00+00:00",
                  decision_expires_at="2026-09-22T13:00:00+00:00")
    inbox.post(fresh)
    clock.advance(minutes=50)  # t1 已過期但未達保留期限;t2 仍待處理

    inbox.post(valid(task_id="t3", decision_created_at="2026-09-22T12:55:00+00:00",
                     decision_expires_at="2026-09-22T13:30:00+00:00"))

    assert {row[0] for row in inbox.rows()} == {"t1", "t2", "t3"}


def test_the_retention_period_is_longer_than_any_proposal_can_live():
    assert inbox_store.RETENTION > inbox_store.MAX_DECISION_LIFETIME


def test_a_creation_time_in_the_future_is_refused(start_inbox):
    inbox = start_inbox()
    future = {"decision_created_at": "2026-09-22T12:40:00+00:00",
              "decision_expires_at": "2026-09-22T13:00:00+00:00"}  # 現在 12:05,建立時間在 35 分鐘後

    status, data = inbox.post(valid(**future))

    assert (status, data["error"]) == (422, "created_in_future")
    assert inbox.rows() == []


def test_purging_never_deletes_a_task_that_still_has_a_pending_row(tmp_path):
    store = inbox_store.InboxStore(tmp_path / "s.db")
    conn = sqlite3.connect(tmp_path / "s.db", isolation_level=None)
    conn.execute(  # 直接塞一列「很久以前收到、到期時間卻很遠」的待處理(正常流程做不出來)
        "INSERT INTO proposals (task_id, revision, content_hash, state, payload, expires_at, "
        "received_at) VALUES ('old', 1, 'h', 'pending', '{}', "
        "'2099-01-01T00:00:00.000000Z', '2026-09-22T01:00:00.000000Z')")
    proposal = inbox_server.parse_proposal(valid(task_id="new")).proposal

    store.accept(proposal, lambda: inbox_store.utc_now().replace(
        year=2026, month=9, day=22, hour=12, minute=5))

    assert ("old",) in conn.execute("SELECT task_id FROM proposals").fetchall()
    conn.close()
    store.close()
