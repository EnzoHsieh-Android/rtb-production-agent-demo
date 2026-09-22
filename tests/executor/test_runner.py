"""執行迴圈的啟動程式:金鑰、單一執行者鎖、重啟恢復與就緒訊號、系統錯誤停機。

S54、S55 用真的子行程;其他在測試行程內跑 run(),DSP 用替身、時間可控。
"""

import io
import os
import select
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rtb.capabilitykit import KEY_ENV, MIN_KEY_BYTES
from rtb.domain.attempt import operation_key
from rtb.executor import attempt_store, runner
from rtb.executor.execution import WriteAnswer
from rtb.executor.inbox_store import InboxStore
from tests.capability_samples import TEST_KEY
from tests.executor.fakes import Harness, proposal, write_config

SRC = str(Path(__file__).resolve().parents[2] / "src")
ENV = {KEY_ENV: TEST_KEY.decode()}


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def argv(db, config, dsp_url="http://127.0.0.1:9"):
    return ["--db", str(db), "--dsp-url", dsp_url, "--tenant-config", str(config),
            "--interval-seconds", "0.01"]


def run_in_process(h, **kwargs):
    kwargs.setdefault("max_rounds", 3)
    return runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp,
                      out=io.StringIO(), sleep=lambda _s: None, **kwargs)


def in_flight_row(db, clock):
    store = InboxStore(db)
    try:
        with store.transaction() as tx:
            return attempt_store.begin(tx, proposal(campaign_id="c9"), clock(),
                                       capability_expires_at=clock()).row.key
    finally:
        store.close()


def state_of(db, key):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT state FROM attempts WHERE key = ? ORDER BY seq DESC LIMIT 1",
                            (key,)).fetchone()[0]
    finally:
        conn.close()


def spawn(db, config, env_key):
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop(KEY_ENV, None)
    if env_key is not None:
        env[KEY_ENV] = env_key
    return subprocess.Popen(
        [sys.executable, "-m", "rtb.executor.runner", *argv(db, config)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def read_line(proc, wait_seconds=15.0):
    deadline, buffer = time.monotonic() + wait_seconds, b""
    fd = proc.stdout.fileno()
    while b"\n" not in buffer:
        ready, _, _ = select.select([fd], [], [], max(deadline - time.monotonic(), 0))
        assert ready, f"子行程在 {wait_seconds} 秒內沒印出一整行(目前:{buffer!r})"
        chunk = os.read(fd, 256)
        assert chunk, f"子行程提前結束(目前:{buffer!r})"
        buffer += chunk
    return buffer.split(b"\n")[0].decode().strip()


def stop(proc):
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    proc.stdout.close()
    proc.stderr.close()


# ---- [S54] ----
@pytest.mark.parametrize("env_key", [None, "x" * (MIN_KEY_BYTES - 1)], ids=["missing", "short"])
def test_the_runner_refuses_to_start_without_a_usable_key(tmp_path, clock, env_key):
    db, config = tmp_path / "executor.db", write_config(tmp_path / "tenants.json")
    key = in_flight_row(db, clock)

    proc = spawn(db, config, env_key)
    try:
        assert proc.wait(timeout=30) == runner.EXIT_NO_KEY
    finally:
        stop(proc)

    assert state_of(db, key) == "in_flight"  # 重啟恢復沒跑


# ---- [S55] ----
def test_the_runner_holds_a_single_instance_lock_and_recovers_before_ready(tmp_path, clock):
    db, config = tmp_path / "executor.db", write_config(tmp_path / "tenants.json")
    before_ready = in_flight_row(db, clock)
    first = spawn(db, config, ENV[KEY_ENV])
    try:
        assert read_line(first) == runner.READY
        assert state_of(db, before_ready) == "unknown"  # 就緒之前已做完重啟恢復

        # 第一個還在跑:這時資料庫裡又有一筆嘗試中(它正在處理的操作)
        store = InboxStore(db)
        try:
            with store.transaction() as tx:
                running = attempt_store.begin(tx, proposal(campaign_id="c8"), clock(),
                                              capability_expires_at=clock()).row.key
        finally:
            store.close()
        alias = tmp_path / "alias.db"
        alias.symlink_to(db)
        for path in (alias, Path(os.path.relpath(db))):
            second = subprocess.Popen(
                [sys.executable, "-m", "rtb.executor.runner", *argv(path, config)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "PYTHONPATH": SRC, **ENV})
            try:
                assert second.wait(timeout=30) == runner.EXIT_LOCKED, path
            finally:
                stop(second)
            assert state_of(db, running) == "in_flight"  # 第二個沒跑重啟恢復
    finally:
        stop(first)


# ---- [S60] ----
def test_a_broken_tenant_configuration_stops_the_runner_without_blocking(h):
    h.submit()
    os.chmod(h.config, 0o664)  # 群組可寫:不安全

    assert run_in_process(h) == runner.EXIT_HALTED

    assert h.proposals() == [("t1", 1, "pending", None, None)]  # 不擋下,留在待處理
    assert h.attempts() == [] and h.dsp.writes == []

    h.config.write_text("{not json", encoding="utf-8")  # 內容壞掉也一樣
    os.chmod(h.config, 0o600)
    assert run_in_process(h) == runner.EXIT_HALTED
    assert h.proposals() == [("t1", 1, "pending", None, None)]


# ---- [S61] ----
def test_other_client_errors_escalate_as_local_request_errors_and_stop_the_runner(h):
    prop = h.submit()
    h.submit(task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(400, "missing_idempotency_key"))

    assert run_in_process(h) == runner.EXIT_HALTED

    rows = h.attempts()
    assert [(r[2], r[3]) for r in rows if r[0] == operation_key(prop)][-1] == (
        "escalated", "local_request_error")
    assert ("t2", 1, "pending", None, None) in h.proposals()  # 停下了,沒有繼續鎖下一個廣告
    assert len(h.dsp.writes) == 1


# ---- [S68] ----
def test_the_runner_stops_when_its_lock_file_is_replaced(h):
    replaced = []

    def replace_lock_file(_seconds):
        if not replaced:
            lock = next(h.tmp_path.glob(".rtb-runner-*.lock"))
            lock.unlink()
            lock.write_bytes(b"")  # 同路徑的新檔:inode 不同
            replaced.append(lock)

    code = runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp,
                      out=io.StringIO(), sleep=replace_lock_file, max_rounds=5)

    assert replaced and code == runner.EXIT_HALTED


# ---- [S69] ----
def test_a_conditional_write_without_progress_stops_the_runner(h):
    prop = h.submit()
    key = operation_key(prop)

    def someone_else_moves_the_key(*_):  # 寫結果之前,這把鍵被別的東西改過
        other = InboxStore(h.db)
        try:
            with other.transaction() as tx:
                attempt_store.transition(tx, key, 1, attempt_store.AttemptState.UNKNOWN, h.clock())
        finally:
            other.close()

    h.dsp.on_write = someone_else_moves_the_key

    assert run_in_process(h) == runner.EXIT_HALTED

    assert [(r[1], r[2]) for r in h.attempts()] == [(1, "in_flight"), (2, "unknown")]


def _run_counting_opens(monkeypatch, db, config, clock):
    opened = []
    monkeypatch.setattr(runner, "InboxStore", lambda *a, **_k: opened.append(a))
    code = runner.run(argv(db, config), environ=ENV, clock=clock, out=io.StringIO(),
                      max_rounds=1, sleep=lambda _s: None)
    return code, opened


def test_a_locked_out_runner_never_opens_the_database_with_sqlite(tmp_path, monkeypatch, clock):
    """拿不到鎖的第二個執行迴圈必須在用 SQLite 開資料庫之前就結束(先鎖後開)。"""
    db, config = tmp_path / "executor.db", write_config(tmp_path / "tenants.json")
    InboxStore(db).close()
    held = runner.RunnerLock(db)
    try:
        code, opened = _run_counting_opens(monkeypatch, Path(os.path.relpath(db)), config, clock)
    finally:
        held.release()
    assert code == runner.EXIT_LOCKED
    assert opened == []


def test_a_hard_linked_database_is_refused_before_sqlite_opens_it(tmp_path, monkeypatch, clock):
    """資料庫檔有硬連結時,收件口與執行迴圈可能各用一個檔名開同一個資料庫、各用一組 WAL 檔,
    可能損壞資料:不論用哪個名字啟動、有沒有別的執行迴圈,都拒絕啟動,也不用 SQLite 開它。"""
    db, config = tmp_path / "executor.db", write_config(tmp_path / "tenants.json")
    InboxStore(db).close()
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    os.link(db, other_dir / "hard.db")  # 連不同目錄的硬連結也擋
    for path in (db, other_dir / "hard.db"):
        code, opened = _run_counting_opens(monkeypatch, path, config, clock)
        assert code == runner.EXIT_UNSAFE_DB, path
        assert opened == []
