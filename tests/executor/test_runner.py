"""執行迴圈的啟動程式:金鑰、重啟恢復與就緒訊號、忙碌重試、系統錯誤停機。

S54、S133 用真的子行程;其他在測試行程內跑 run(),DSP 用替身、時間可控。
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
    kwargs.setdefault("owner", "executor")  # 跟測試替身的執行者同一個擁有者:同一個工作者
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


# ---- [S127] ----
class _Stop(Exception):
    pass


def _held_in_flight(h):
    """一筆收件表有處理中訊息的嘗試中:處理到呼叫 DSP 那一刻就停下(模擬別的工作者正在做)。"""
    prop = h.submit()

    def stop(*_args):
        raise _Stop

    h.dsp.on_write = stop
    with pytest.raises(_Stop):
        h.process()
    h.dsp.on_write = None
    return operation_key(prop)


def test_restart_recovery_only_touches_attempts_without_an_in_progress_message(h):
    held = _held_in_flight(h)
    orphan = in_flight_row(h.db, h.clock)  # 收件表沒有它:舊資料
    h.clock.advance(seconds=3600)  # 就算持有者的租約早就到期,重啟恢復也不碰(留給對帳原子接手)

    assert run_in_process(h, max_rounds=0, owner="restarted") == 0

    assert state_of(h.db, orphan) == "unknown"
    assert state_of(h.db, held) == "in_flight"


# ---- [S128] ----
def test_restart_recovery_moves_nothing_while_an_in_progress_row_is_unreadable(h, capsys):
    held = _held_in_flight(h)
    orphan = in_flight_row(h.db, h.clock)
    h.query("UPDATE proposals SET payload = '{' WHERE task_id = 't1'")  # 算不出它的鍵
    h.clock.advance(seconds=3600)  # 孤兒夠舊:不是因為「剛寫下」才沒轉

    assert run_in_process(h, max_rounds=0, owner="restarted") == 0

    assert state_of(h.db, orphan) == "in_flight"  # 可能正屬於那則讀不回來的訊息:不轉
    assert state_of(h.db, held) == "in_flight"
    assert "讀不回來的處理中訊息" in capsys.readouterr().err


# ---- [S133] ----
def test_two_runners_start_side_by_side_and_each_recovers_before_ready(tmp_path, clock):
    """不再有單一執行者鎖:兩個啟動程式真的同時啟動(兩個都先起、再一起等就緒),都做完重啟恢復
    才印就緒。"""
    db, config = tmp_path / "executor.db", write_config(tmp_path / "tenants.json")
    orphan = in_flight_row(db, clock)
    alias = tmp_path / "alias.db"
    alias.symlink_to(db)
    procs = [subprocess.Popen(
        [sys.executable, "-m", "rtb.executor.runner", *argv(path, config)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": SRC, **ENV}) for path in (db, alias)]
    try:
        for proc in procs:
            assert read_line(proc) == runner.READY
            assert proc.poll() is None  # 兩個都還活著
        assert state_of(db, orphan) == "unknown"  # 就緒之前已做完重啟恢復
    finally:
        for proc in procs:
            stop(proc)


# ---- [S60] ----
def test_a_broken_tenant_configuration_stops_the_runner_without_blocking(h):
    h.submit()
    os.chmod(h.config, 0o664)  # 群組可寫:不安全

    assert run_in_process(h) == runner.EXIT_HALTED

    # 不擋下:取件已寫處理中,停機後沒人確認,租約到期就會被重新投遞
    assert h.proposals() == [("t1", 1, "pending", "in_progress", None)]
    assert h.attempts() == [] and h.dsp.writes == []

    h.config.write_text("{not json", encoding="utf-8")  # 內容壞掉也一樣
    os.chmod(h.config, 0o600)
    h.clock.advance(seconds=61)  # 租約到期,重啟後能再取到
    assert run_in_process(h) == runner.EXIT_HALTED
    assert h.proposals() == [("t1", 1, "pending", "in_progress", None)]


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


# ---- [S82] ----
def test_each_round_reconciles_oldest_first_before_taking_a_new_proposal(h):
    order = []
    first = h.submit()  # 先開的嘗試,但它的最後一列會比較晚寫
    h.dsp.answers.append(WriteAnswer(None))
    h.dsp.on_write = lambda p, k, _t: h.dsp.apply(p, k)
    h.process()
    h.clock.advance(seconds=10)
    second = h.submit(task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(None))
    h.process()
    h.dsp.on_write = None
    h.clock.advance(seconds=10)
    with h.store.transaction() as tx:  # 讓先開的那把鍵最後一列比較新
        row = attempt_store.latest(tx, operation_key(first))
        attempt_store.record_verification_timeout(tx, row.key, row.seq, h.clock())
    h.submit(task_id="t3", campaign_id="c3")  # 一份新提案

    lookup = h.dsp.operation_record
    h.dsp.operation_record = lambda key, *, on_call: (
        order.append(("lookup", key)), lookup(key, on_call=on_call))[1]
    h.dsp.on_read = lambda campaign: order.append(("read", campaign))

    assert run_in_process(h, max_rounds=1) == 0

    lookups = [key for kind, key in order if kind == "lookup"]
    assert lookups == [operation_key(second), operation_key(first)]  # 依最後一列由舊到新
    assert order.index(("read", "c3")) > max(i for i, e in enumerate(order) if e[0] == "lookup")


# ---- [S86] ----
def test_a_round_with_a_failed_reconcile_call_sleeps(h):
    h.submit()
    h.dsp.answers.append(WriteAnswer(None))
    h.process()  # 一筆結果不明
    h.submit(task_id="t2", campaign_id="c2")  # 一份會被執行的新提案
    h.dsp.lookup_failures = 1
    slept = []

    code = runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp,
                      out=io.StringIO(), sleep=slept.append, max_rounds=1, owner="executor")

    assert code == 0
    assert h.dsp.writes[-1][0].campaign_id == "c2"  # 前置:新提案這輪確實被執行了
    assert slept == [0.01]  # 對帳這輪有 DSP 呼叫失敗:照樣休息一個間隔

    # 對照:這輪沒有失敗、新提案被執行,不休息
    h.submit(task_id="t3", campaign_id="c3")
    slept.clear()
    runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp,
               out=io.StringIO(), sleep=slept.append, max_rounds=1, owner="executor")
    assert slept == []


def test_a_dsp_timeout_too_close_to_the_lease_is_refused(h):
    """判斷有沒有人還在做,假設一次 DSP 呼叫一定在租約時間內結束:逾時設太長就拒絕啟動,
    而且在做任何事之前。"""
    orphan = in_flight_row(h.db, h.clock)
    h.clock.advance(seconds=3600)
    code = runner.run([*argv(h.db, h.config), "--dsp-timeout-seconds", "10"], environ=ENV,
                      clock=h.clock, dsp=h.dsp, out=io.StringIO(), sleep=lambda _s: None,
                      max_rounds=0)
    assert code == runner.EXIT_UNSAFE_CONFIG
    assert state_of(h.db, orphan) == "in_flight"  # 連重啟恢復都沒跑
    assert runner.run([*argv(h.db, h.config), "--dsp-timeout-seconds", "9.9"], environ=ENV,
                      clock=h.clock, dsp=h.dsp, out=io.StringIO(), sleep=lambda _s: None,
                      max_rounds=0) == 0  # 1/6 以內照常啟動


# ---- [S135] ----
def test_a_busy_database_rests_a_round_and_halts_only_after_three_in_a_row(h, monkeypatch):
    """多工作者共用一顆寫入鎖,等鎖逾時是正常競爭:休息一輪再試,連續 3 輪都忙才停機。"""
    from rtb.executor.inbox_store import InboxBusy

    rounds = []
    busy_rounds = {1, 2, 4, 5, 6}  # 第 3 輪成功一次,把連續次數歸零;之後連續 3 輪都忙

    def maybe_busy(_self):
        rounds.append(len(rounds) + 1)
        if rounds[-1] in busy_rounds:
            raise InboxBusy("database is locked")
        return False

    monkeypatch.setattr(runner.Executor, "reconcile_all", maybe_busy)
    slept = []
    code = runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp,
                      out=io.StringIO(), sleep=slept.append, max_rounds=10, owner="executor")

    assert code == runner.EXIT_BUSY  # 忙碌跟系統錯誤分開:稍後再啟動即可
    assert rounds == [1, 2, 3, 4, 5, 6]  # 前兩輪忙沒停;第 6 輪是連續第 3 次才停
    assert slept.count(0.01) >= 4  # 忙的那幾輪都休息了


def test_a_busy_database_at_startup_retries_before_giving_up(h, monkeypatch):
    """啟動時開庫與重啟恢復撞到忙碌:休息再試;第 3 次成功就照常就緒,連續 3 次都忙才結束。"""
    from rtb.executor.inbox_store import InboxBusy

    real = runner.InboxStore
    tries = []

    def busy_then(fail_times):
        def open_store(*args, **kwargs):
            tries.append(1)
            if len(tries) <= fail_times:
                raise InboxBusy("database is locked")
            return real(*args, **kwargs)
        return open_store

    monkeypatch.setattr(runner, "InboxStore", busy_then(2))
    out = io.StringIO()
    assert runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp, out=out,
                      sleep=lambda _s: None, max_rounds=0) == 0
    assert out.getvalue().strip() == runner.READY and len(tries) == 3

    tries.clear()
    monkeypatch.setattr(runner, "InboxStore", busy_then(3))
    assert run_in_process(h) == runner.EXIT_BUSY
    assert len(tries) == 3


def test_a_busy_restart_recovery_at_startup_retries_before_giving_up(h, monkeypatch):
    """開庫成功、但重啟恢復那個交易撞到忙碌:關掉這次開的連線、休息再試,連續 3 次才結束。"""
    from rtb.executor.inbox_store import InboxBusy

    real = runner._recover
    tries, closed = [], []

    def busy_then(fail_times):
        def recover(store, clock, owner):  # Phase 9:重啟恢復多收行程身分,原樣轉交
            tries.append(1)
            if len(tries) <= fail_times:
                real_close = store.close
                store.close = lambda: (closed.append(1), real_close())[1]
                raise InboxBusy("database is locked")
            return real(store, clock, owner)
        return recover

    orphan = in_flight_row(h.db, h.clock)
    h.clock.advance(seconds=3600)
    monkeypatch.setattr(runner, "_recover", busy_then(2))
    out = io.StringIO()
    assert runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp, out=out,
                      sleep=lambda _s: None, max_rounds=0) == 0
    assert out.getvalue().strip() == runner.READY and len(tries) == 3
    assert len(closed) == 2  # 失敗的那兩次都把連線關掉了
    assert state_of(h.db, orphan) == "unknown"  # 第三次真的做完重啟恢復

    tries.clear()
    monkeypatch.setattr(runner, "_recover", busy_then(3))
    assert run_in_process(h) == runner.EXIT_BUSY
    assert len(tries) == 3
