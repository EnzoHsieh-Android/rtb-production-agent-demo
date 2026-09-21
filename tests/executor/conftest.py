"""收件口測試的共用工具:在測試行程內以執行緒啟動伺服器,時間可控,資料庫檔可直接查。"""

import contextlib
import http.client
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from rtb.executor.inbox_server import InboxServer

NOW = datetime(2026, 9, 22, 12, 5, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class Inbox:
    def __init__(self, db, clock, **kwargs):
        self.db, self.clock = db, clock
        self.server = InboxServer(db, clock=clock, **kwargs)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, body=None, *, raw=None, headers=None, path="/proposals", timeout=5):
        merged = {"Content-Type": "application/json", **(headers or {})}
        payload = raw if raw is not None else json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request("POST", path, body=payload, headers=merged)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, (json.loads(data) if data else None)
        finally:
            conn.close()

    def rows(self, sql="SELECT task_id, revision, state FROM proposals ORDER BY task_id, revision"):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def start_inbox(tmp_path, clock):
    started = []

    def start(**kwargs):
        inbox = Inbox(tmp_path / "inbox.db", clock, **kwargs)
        started.append(inbox)
        return inbox

    yield start
    for inbox in started:
        with contextlib.suppress(Exception):
            inbox.stop()
