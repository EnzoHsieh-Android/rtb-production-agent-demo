"""執行端每一次呼叫 DSP 都留一列分類過的呼叫紀錄(Phase 9 增量 1):[S618]、[S625]。

DSP 用戶端的每一支公開方法都經同一個底層送出點,每一次 HTTP 呼叫觸發一次回呼;執行迴圈掛上回呼、
在獨立的短交易裡寫呼叫紀錄,所以結果寫入回滾時呼叫紀錄照樣留著。
"""

import inspect
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rtb.domain.attempt import operation_key
from rtb.domain.proposal import content_hash
from rtb.executor.attempt_store import DspCallKind, DspCallResult, DspErrorCode
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import DspUnavailable, Result, WriteAnswer
from tests.executor.fakes import Harness, proposal

R = DspCallResult
GOOD_CAMPAIGN = {"budget": 100, "version": 3, "status": "active"}
GOOD_OPERATION = {"operation_id": 7, "campaign_id": "c1", "action": "update_budget",
                  "params": {"new_budget": 150}, "expected_version": 3, "version_after": 4,
                  "committed_at": "2026-09-22T12:05:00+00:00"}


class Scripted:
    """每一個請求照排好的回應回:(狀態碼, 本文位元組, 延遲秒數)。"""

    def __init__(self):
        self.replies = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _handler(self):
        replies = self.replies

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                status, body, delay = replies.pop(0)
                time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # 用戶端逾時先斷線

            do_GET = do_POST = _answer

            def log_message(self, *_args):
                pass

        return Handler

    def reply(self, status, body, delay=0.0):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.replies.append((status, raw, delay))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def dsp():
    server = Scripted()
    yield server
    server.close()


class _Bound:
    """一個用戶端加一份收回呼的清單:每次呼叫都顯式傳 on_call(代碼審第 1 輪:不再掛共用回呼)。"""

    def __init__(self, url, timeout):
        self.client, self.seen = DspClient(url, timeout), []

    def __getattr__(self, name):
        method = getattr(self.client, name)
        return lambda *args: method(*args, on_call=self.seen.append)


def _calls(url, timeout=0.3):
    bound = _Bound(url, timeout)
    return bound, bound.seen


PROP = proposal()
KEY = operation_key(PROP)
METHODS = {
    DspCallKind.READ_CAMPAIGN: lambda c: c.read_campaign("c1"),
    DspCallKind.WRITE: lambda c: c.write(PROP, KEY, "token"),
    DspCallKind.LOOKUP_OPERATION: lambda c: c.operation_record(KEY),
    DspCallKind.VOID: lambda c: c.void(PROP, KEY, "token"),
}


def _call(fn, client):
    try:
        return fn(client)
    except DspUnavailable as exc:
        return exc


# ---- [S618] ----
def test_every_dsp_call_leaves_one_classified_call_record(dsp, tmp_path, clock):
    _each_public_method_fires_once(dsp)
    _failures_are_classified(dsp)
    _the_loop_records_every_call(tmp_path, clock)


def _each_public_method_fires_once(dsp):
    """每一支公開方法各呼叫一次:回呼剛好一次,類別對得上。"""
    client, seen = _calls(dsp.url)
    public = {name for name in dir(DspClient) if not name.startswith("_")}
    assert public == {"read_campaign", "write", "operation_record", "operation_version", "void"}
    for name in public:  # 回呼是每一支方法的必填關鍵字參數,漏傳在型別層就擋下
        parameter = inspect.signature(getattr(DspClient, name)).parameters["on_call"]
        assert (parameter.kind, parameter.default) == (parameter.KEYWORD_ONLY, parameter.empty)
    for name in sorted(public):
        dsp.reply(200, GOOD_OPERATION | GOOD_CAMPAIGN | {"state": "voided"})
        seen.clear()
        getattr(client, name)(*{"read_campaign": ("c1",), "write": (PROP, KEY, "t"),
                                 "void": (PROP, KEY, "t")}.get(name, (KEY,)))
        assert len(seen) == 1, name
        assert seen[0].result is R.RESPONDED and seen[0].status == 200, name
        assert seen[0].latency_ms >= 0


def _failures_are_classified(dsp):
    """逾時、連線失敗、5xx、4xx、回應讀不懂(不是 JSON、200 但欄位讀不懂)各自一類,四種呼叫都一樣。"""
    client, seen = _calls(dsp.url)
    for kind, fn in METHODS.items():
        cases = [  # (回應, 預期類別, 預期狀態碼, 回應或例外帶不帶同一個分類)
            ((500, {"error": "internal_error"}, 0.0), R.SERVER_ERROR, 500, False),
            ((404, {"error": "not_found"}, 0.0), R.CLIENT_ERROR, 404, False),
            # 代碼審第 1 輪:本文讀不懂但有狀態碼,照樣記狀態碼
            ((200, b"not json", 0.0), R.UNREADABLE, 200, True),
            ((200, {"unexpected": True}, 0.0), R.UNREADABLE, 200, False),
            ((200, GOOD_OPERATION, 1.0), R.TIMEOUT, None, True),
        ]
        for reply, expected, status, carries in cases:
            dsp.reply(*reply)
            seen.clear()
            answer = _call(fn, client)
            assert [(c.kind, c.result, c.status) for c in seen] == [(kind, expected, status)], (
                kind, reply)
            if carries:  # 沒拿到可用的回應:回應或例外帶同一個分類
                assert getattr(answer, "failure", None) is expected, (kind, reply)
        dead, lost = _calls("http://127.0.0.1:9")
        _call(fn, dead)
        assert [(c.kind, c.result) for c in lost] == [(kind, R.CONNECTION_FAILED)]


def _the_loop_records_every_call(tmp_path, clock):
    """執行迴圈掛上回呼、每次呼叫寫一列;結果寫入回滾時呼叫紀錄照樣留著。"""
    h = Harness(tmp_path, clock)
    try:
        h.submit()
        assert h.process().kind is Result.EXECUTED
        rows = h.query("SELECT call_kind, result, task_id, revision, key, source, actor "
                       "FROM dsp_calls ORDER BY id")
        assert rows == [("read_campaign", "responded", "t1", 1, KEY, "executor_loop", "executor"),
                        ("write", "responded", "t1", 1, KEY, "executor_loop", "executor"),
                        ("read_campaign", "responded", "t1", 1, KEY, "executor_loop",
                         "executor")]

        h.submit(task_id="t2", campaign_id="c2")
        h.dsp.on_write = lambda *_a: h.clock.advance(seconds=61)  # 回應回來時租約已過期
        before = h.query("SELECT count(*) FROM dsp_calls")[0][0]
        assert h.process().kind is Result.LEASE_LOST
        after = h.query("SELECT call_kind FROM dsp_calls ORDER BY id")[before:]
        assert [row[0] for row in after] == ["read_campaign", "write"]
        assert h.query("SELECT state FROM attempts WHERE task_id = 't2' OR key IN "
                       "(SELECT key FROM attempts WHERE task_id = 't2')") == [("in_flight",)]
    finally:
        h.close()


# ---- [S625] ----
def test_dsp_call_records_keep_the_error_code(dsp, tmp_path, clock):
    client, seen = _calls(dsp.url)
    for body, expected in (({"error": "version_conflict"}, DspErrorCode.VERSION_CONFLICT),
                           ({"error": "made_up_code"}, DspErrorCode.OTHER),
                           ({"error": 5}, DspErrorCode.OTHER),
                           ({"version_after": 4}, None)):
        dsp.reply(409 if "error" in body else 200, body)
        seen.clear()
        client.write(PROP, KEY, "token")
        assert [c.error for c in seen] == [expected], body
    dsp.reply(200, GOOD_OPERATION, delay=1.0)  # 沒回應:沒有錯誤代碼
    seen.clear()
    client.write(PROP, KEY, "token")
    assert [(c.result, c.error) for c in seen] == [(R.TIMEOUT, None)]

    h = Harness(tmp_path, clock)
    try:
        h.dsp.answers += [WriteAnswer(409, "version_conflict")]
        h.submit()
        h.process()
        h.dsp.answers += [WriteAnswer(409, "something_new")]
        h.submit(task_id="t2", campaign_id="c2")
        h.process()
        rows = h.query("SELECT task_id, error_code FROM dsp_calls WHERE call_kind = 'write' "
                       "ORDER BY id")
        assert rows == [("t1", "version_conflict"), ("t2", "other")]
        assert h.query("SELECT DISTINCT error_code FROM dsp_calls WHERE call_kind != 'write'") == [
            (None,)]
    finally:
        h.close()


# ---- [S618] 代碼審第 1 輪:撞到資料庫忙碌不能蓋掉已發生的 DSP 結果 ----
def test_a_busy_database_defers_the_call_record_without_losing_the_dsp_result(
        tmp_path, clock, monkeypatch):
    from rtb.executor import attempt_store
    from rtb.sqlitekit import DatabaseBusy

    real, locked = attempt_store.record_dsp_call, {"on": False}

    def busy_while_locked(*args, **kwargs):
        if locked["on"]:
            raise DatabaseBusy("database is locked")  # 寫呼叫紀錄的短交易等不到鎖
        return real(*args, **kwargs)

    monkeypatch.setattr(attempt_store, "record_dsp_call", busy_while_locked)
    h = Harness(tmp_path, clock)
    try:
        worker = h.executor()  # 待寫清單在這個執行器(行程)的記憶體裡
        h.submit()
        h.dsp.on_write = lambda *_a: locked.update(on=True)  # DSP 寫入之後才開始忙
        assert worker.process_one().kind is Result.EXECUTED  # DSP 結果照樣回到呼叫端、嘗試照常推進
        assert [r[2] for r in h.attempts()] == ["in_flight", "committed_unverified", "verified"]
        assert h.query("SELECT call_kind FROM dsp_calls ORDER BY id") == [("read_campaign",)]

        locked["on"] = False  # 鎖放開:下一次處理一筆之前把欠的補寫上
        assert worker.process_one().kind is Result.IDLE
        rows = h.query("SELECT call_kind, result, task_id, key FROM dsp_calls ORDER BY id")
        assert rows == [("read_campaign", "responded", "t1", KEY),
                        ("write", "responded", "t1", KEY),
                        ("read_campaign", "responded", "t1", KEY)]

        _reconcile_defers_too(h, worker, locked)
        _other_database_errors_still_propagate(h, worker, monkeypatch)
    finally:
        h.close()


def _reconcile_defers_too(h, worker, locked):
    """對帳也一樣:撞忙不丟例外、這一把鍵照常推到終點,下一輪對帳之前補寫。"""
    h.submit(task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(None))
    h.dsp.on_write = None
    assert worker.process_one().kind is Result.EXECUTED
    locked["on"] = True
    assert worker.reconcile_all() is False  # 撞忙不丟例外,這把鍵照常推到終點
    assert [r[2] for r in h.attempts() if r[0] != KEY][-1] == "verified"
    before = h.query("SELECT count(*) FROM dsp_calls")[0][0]
    locked["on"] = False
    worker.reconcile_all()  # 下一輪對帳之前補寫
    assert h.query("SELECT count(*) FROM dsp_calls")[0][0] == before + 4


def _other_database_errors_still_propagate(h, worker, monkeypatch):
    """不是忙碌的資料庫錯誤照舊往外丟。"""
    from rtb.executor import attempt_store

    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(attempt_store, "record_dsp_call", broken)
    h.submit(task_id="t3", campaign_id="c3")
    with pytest.raises(sqlite3.OperationalError):
        worker.process_one()


# ---- [S618] 代碼審第 1 輪:顯式傳回呼,共用一個 DSP 用戶端的工作者各記各的 ----
def test_calls_owed_during_a_job_are_written_when_the_job_ends(tmp_path, clock, monkeypatch):
    """每一份工作(處理一筆、對帳一把鍵)結束時補寫:工作中每次呼叫都撞忙,結束時鎖放開就補上。"""
    from rtb.executor import attempt_store
    from rtb.sqlitekit import DatabaseBusy

    real, misses = attempt_store.record_dsp_call, {"left": 0}

    def busy_during_the_job(*args, **kwargs):
        if misses["left"]:
            misses["left"] -= 1  # 每次撞忙的補寫只碰到第一列就丟,所以一次補寫扣一次
            raise DatabaseBusy("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(attempt_store, "record_dsp_call", busy_during_the_job)
    h = Harness(tmp_path, clock)
    try:
        worker = h.executor()
        h.submit()
        misses["left"] = 3  # 讀、寫、查證讀:三次呼叫當下都補寫不進去
        assert worker.process_one().kind is Result.EXECUTED
        assert h.query("SELECT count(*) FROM dsp_calls") == [(3,)]
        assert worker.unrecorded() == ()

        h.submit(task_id="t2", campaign_id="c2")
        h.dsp.answers.append(WriteAnswer(None))  # 結果不明:留一把要對帳的鍵
        assert worker.process_one().kind is Result.EXECUTED
        before = h.query("SELECT count(*) FROM dsp_calls")[0][0]
        misses["left"] = 4  # 對帳這把鍵:查操作紀錄、讀、重送、查證讀,當下都補寫不進去
        worker.reconcile_all()
        assert h.query("SELECT count(*) FROM dsp_calls")[0][0] == before + 4
        assert worker.unrecorded() == ()
    finally:
        h.close()


def _owe_three_calls(h, worker, monkeypatch):
    """處理一筆,三次呼叫(讀、寫、查證讀)都補寫不進去,欠在待寫清單上。"""
    from rtb.executor import attempt_store
    from rtb.sqlitekit import DatabaseBusy

    real = attempt_store.record_dsp_call

    def busy(*_args, **_kwargs):
        raise DatabaseBusy("database is locked")

    monkeypatch.setattr(attempt_store, "record_dsp_call", busy)
    h.submit()
    assert worker.process_one().kind is Result.EXECUTED
    assert len(worker.unrecorded()) == 3
    monkeypatch.setattr(attempt_store, "record_dsp_call", real)
    return real


def test_an_interrupt_after_the_flush_commits_does_not_write_the_calls_twice(
        tmp_path, clock, monkeypatch):
    """補寫提交之後、清單拿掉之前被 Ctrl+C 打斷:例外會展開堆疊、跑啟動程式收尾的補寫,不能把同一批
    再寫一次(呼叫紀錄表沒有能去重的鍵;代碼審第 3 輪外家席)。"""
    h = Harness(tmp_path, clock)
    try:
        worker = h.executor()
        _owe_three_calls(h, worker, monkeypatch)
        real = h.store.transaction

        @contextmanager
        def interrupted_after_commit():
            with real() as tx:
                yield tx
            raise KeyboardInterrupt  # 提交已完成,例外才到

        monkeypatch.setattr(h.store, "transaction", interrupted_after_commit)
        with pytest.raises(KeyboardInterrupt):
            worker.flush_calls()
        monkeypatch.setattr(h.store, "transaction", real)
        assert worker.flush_calls() is True  # 收尾補寫
        rows = h.query("SELECT id FROM dsp_calls")
        assert len(rows) == 3  # 沒有重複列
        assert worker.unrecorded() == ()
    finally:
        h.close()


def test_a_flush_whose_commit_fails_puts_the_batch_back(tmp_path, clock, monkeypatch):
    """每一列都寫了、提交那一步才出錯:交易回滾,這一批照樣放回(不是寫完就當作已記)。"""
    h = Harness(tmp_path, clock)
    try:
        worker = h.executor()
        _owe_three_calls(h, worker, monkeypatch)
        real = h.store.transaction

        @contextmanager
        def commit_fails():
            with real() as tx:
                yield tx
                raise sqlite3.OperationalError("disk I/O error")  # 提交之前:交易回滾

        monkeypatch.setattr(h.store, "transaction", commit_fails)
        with pytest.raises(sqlite3.OperationalError):
            worker.flush_calls()
        monkeypatch.setattr(h.store, "transaction", real)
        assert len(worker.unrecorded()) == 3
        assert worker.flush_calls() is True
        assert h.query("SELECT count(*) FROM dsp_calls") == [(3,)]
    finally:
        h.close()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, sqlite3.OperationalError])
def test_a_flush_that_fails_before_commit_puts_the_batch_back(tmp_path, clock, monkeypatch,
                                                              failure):
    """交易還沒提交就失敗(被打斷,或忙碌以外的資料庫錯誤):這一批原樣放回清單最前面,例外照丟。"""
    from rtb.executor import attempt_store

    h = Harness(tmp_path, clock)
    try:
        worker = h.executor()
        real = _owe_three_calls(h, worker, monkeypatch)
        owed = worker.unrecorded()
        seen = {"n": 0}

        def fails_on_the_second_row(*args, **kwargs):
            seen["n"] += 1
            if seen["n"] == 2:
                raise failure("disk I/O error")
            return real(*args, **kwargs)

        monkeypatch.setattr(attempt_store, "record_dsp_call", fails_on_the_second_row)
        with pytest.raises(failure):
            worker.flush_calls()
        assert worker.unrecorded() == owed  # 原樣放回,順序不變
        assert h.query("SELECT count(*) FROM dsp_calls") == [(0,)]  # 第一列跟著回滾
        monkeypatch.setattr(attempt_store, "record_dsp_call", real)
        assert worker.flush_calls() is True
        assert h.query("SELECT call_kind FROM dsp_calls ORDER BY id") == [
            ("read_campaign",), ("write",), ("read_campaign",)]
    finally:
        h.close()


def test_two_workers_sharing_one_dsp_client_record_their_own_calls(tmp_path, clock):
    from rtb.executor.execution import DspPort

    for name in ("read_campaign", "write", "operation_version", "operation_record", "void"):
        parameter = inspect.signature(getattr(DspPort, name)).parameters["on_call"]
        assert parameter.kind is parameter.KEYWORD_ONLY and parameter.default is parameter.empty
    assert not hasattr(DspPort, "listen") and not hasattr(DspClient, "listen")
    h = Harness(tmp_path, clock)
    try:
        h.submit(task_id="a1", campaign_id="c1")
        h.submit(task_id="a2", campaign_id="c2")
        first, second = h.executor("w1"), h.executor("w2")  # 同一個假 DSP
        assert first.process_one().kind is Result.EXECUTED
        assert second.process_one().kind is Result.EXECUTED
        rows = set(h.query("SELECT task_id, actor FROM dsp_calls"))
        assert rows == {("a1", "w1"), ("a2", "w2")}
    finally:
        h.close()


# ---- [S618] 代碼審第 1 輪:有錯誤狀態碼、本文讀不懂時照狀態碼分類 ----
def test_an_error_status_with_an_unreadable_body_keeps_its_status(dsp):
    client, seen = _calls(dsp.url)
    for kind, fn in METHODS.items():
        for status, expected in ((500, R.SERVER_ERROR), (503, R.SERVER_ERROR),
                                 (404, R.CLIENT_ERROR), (200, R.UNREADABLE)):
            dsp.reply(status, b"<html>oops</html>")
            seen.clear()
            answer = _call(fn, client)
            assert [(c.kind, c.result, c.status) for c in seen] == [(kind, expected, status)], (
                kind, status)
            assert getattr(answer, "failure", None) is expected  # 回應或例外帶同一個分類


def test_a_call_record_table_opened_earlier_on_this_branch_gains_the_content_hash_column(
        tmp_path, clock):
    """內容雜湊欄在同一個增量後補(代碼審第 2 輪):早先開過的資料庫開啟時補上,之後照常寫。"""
    Harness(tmp_path, clock).close()
    conn = sqlite3.connect(tmp_path / "executor.db")
    conn.execute("ALTER TABLE dsp_calls DROP COLUMN content_hash")
    conn.commit()
    conn.close()
    h = Harness(tmp_path, clock)
    try:
        prop = h.submit()
        assert h.process().kind is Result.EXECUTED
        assert set(h.query("SELECT content_hash FROM dsp_calls")) == {(content_hash(prop),)}
    finally:
        h.close()
