"""追蹤檢視測試的共用場景:分析端資料庫、執行端資料庫(假 DSP 跑執行迴圈)、只回操作紀錄的
DSP 替身。"""

import json
import sqlite3
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rtb.analyzer.task_store import TaskStore
from rtb.domain.attempt import operation_key
from rtb.domain.task_state import TaskState
from tests.executor.conftest import NOW, Clock
from tests.executor.fakes import Harness, proposal

ANALYZER_START = NOW - timedelta(minutes=3)


class OperationsDsp:
    """DSP 的唯讀操作紀錄端點替身:operations 裡有的鍵回 200,沒有的回 404 operation_not_found。"""

    def __init__(self):
        self.operations: dict[str, dict] = {}
        self.requests: list[str] = []
        operations, requests = self.operations, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                key = self.path.rsplit("/", 1)[-1]
                requests.append(key)
                found = operations.get(key)
                status, body = (200, found) if found else (404, {"error": "operation_not_found"})
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def commit(self, key, committed_at, operation_id=1, campaign_id="c1"):
        self.operations[key] = {"operation_id": operation_id, "campaign_id": campaign_id,
                                "action": "update_budget", "version_after": 4,
                                "committed_at": committed_at, "replayed": False}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class World:
    """一個任務走過分析端、執行端與 DSP 的三份資料。"""

    def __init__(self, tmp_path):
        self.clock = Clock()
        self.analyzer_db = tmp_path / "analyzer.db"
        self.analyzer = TaskStore(self.analyzer_db)
        self.h = Harness(tmp_path, self.clock)
        self.executor_db = self.h.db
        self.dsp = OperationsDsp()

    def analyze(self, task_id="t1", revisions=(1,), key_stored=True, at=ANALYZER_START):
        """分析端:建任務、每個修訂提一次案,最後一個修訂交給執行(依參數決定有沒有存下冪等鍵)。
        回傳最新一列的序號。"""
        s = self.analyzer
        s.create_task(task_id, "c1", at)
        seq = 1
        for index, revision in enumerate(revisions):
            prop = proposal(task_id=task_id, revision=revision)
            for state in (TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING):
                assert s.commit_step(task_id, seq, state, at + timedelta(seconds=seq))
                seq += 1
            assert s.commit_step(task_id, seq, TaskState.PROPOSED, at + timedelta(seconds=seq),
                                 proposal=prop)
            seq += 1
            if index == len(revisions) - 1:
                assert s.commit_step(task_id, seq, TaskState.HANDED_OFF,
                                     at + timedelta(seconds=seq), proposal=prop,
                                     operation_key=operation_key(prop) if key_stored else None)
                seq += 1
        s.record_tool_call(task_id, 2, "dsp:campaign", "ok", 3.5, at + timedelta(seconds=2))
        return seq

    def execute(self, **overrides):
        prop = self.h.submit(**overrides)
        result = self.h.process()
        return prop, result

    def paths(self):
        return {"analyzer_db": self.analyzer_db, "executor_db": self.executor_db,
                "dsp_url": self.dsp.url}

    def dump(self):
        """兩個資料庫的全部內容(判斷有沒有被寫過)。"""
        out = {}
        for path in (self.analyzer_db, self.executor_db):
            conn = sqlite3.connect(path)
            try:
                out[path.name] = {t: conn.execute(f"SELECT * FROM {t}").fetchall()  # noqa: S608
                                  for (t,) in conn.execute(
                                      "SELECT name FROM sqlite_master WHERE type = 'table'")}
            finally:
                conn.close()
        return out

    def close(self):
        self.analyzer.close()
        self.h.close()
        self.dsp.close()


@pytest.fixture
def world(tmp_path):
    built = World(tmp_path)
    yield built
    built.close()
