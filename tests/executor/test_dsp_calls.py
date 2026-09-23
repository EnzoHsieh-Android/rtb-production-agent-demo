"""執行端每一次呼叫 DSP 都留一列分類過的呼叫紀錄(Phase 9 增量 1):[S618]、[S625]。

DSP 用戶端的每一支公開方法都經同一個底層送出點,每一次 HTTP 呼叫觸發一次回呼;執行迴圈掛上回呼、
在獨立的短交易裡寫呼叫紀錄,所以結果寫入回滾時呼叫紀錄照樣留著。
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rtb.domain.attempt import operation_key
from rtb.executor.attempt_store import DspCall, DspCallKind, DspCallResult, DspErrorCode
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


def _calls(url, timeout=0.3):
    seen: list[DspCall] = []
    client = DspClient(url, timeout)
    client.listen(seen.append)
    return client, seen


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
    public = {name for name in dir(DspClient) if not name.startswith("_")} - {"listen"}
    assert public == {"read_campaign", "write", "operation_record", "operation_version", "void"}
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
        cases = [
            ((500, {"error": "internal_error"}, 0.0), R.SERVER_ERROR, 500),
            ((404, {"error": "not_found"}, 0.0), R.CLIENT_ERROR, 404),
            ((200, b"not json", 0.0), R.UNREADABLE, None),
            ((200, {"unexpected": True}, 0.0), R.UNREADABLE, 200),
            ((200, GOOD_OPERATION, 1.0), R.TIMEOUT, None),
        ]
        for reply, expected, status in cases:
            dsp.reply(*reply)
            seen.clear()
            answer = _call(fn, client)
            assert [(c.kind, c.result, c.status) for c in seen] == [(kind, expected, status)], (
                kind, reply)
            if expected is not R.RESPONDED and status is None:  # 沒拿到回應:回應或例外帶同一個分類
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
def test_dsp_call_records_keep_the_dsp_error_code(dsp, tmp_path, clock):
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
