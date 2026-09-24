"""寫結果碰到資料庫忙碌時在限度內重試(F7 效能計劃第 2 部分,使用者 2026-09-25 裁定):
[S687] 到 [S690]。

忙碌由替換 sqlitekit 的開寫入交易(BEGIN IMMEDIATE)造出:跟真的等鎖逾時一樣,交易沒開起來、什麼都
沒寫;不真的等 5 秒。退避經注入的睡眠,測試記下序列、不真的睡。
"""

import io
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from rtb import sqlitekit
from rtb.capabilitykit import KEY_ENV
from rtb.domain.attempt import AttemptState
from rtb.executor import attempt_store, runner
from rtb.executor.execution import (
    RESULT_WRITE_BACKOFF_SECONDS,
    RESULT_WRITE_LEASE_MARGIN,
    Executor,
    ExecutorHalted,
    Result,
    WriteAnswer,
)
from rtb.executor.inbox_store import (
    VISIBILITY_TIMEOUT,
    CorruptedInboxRow,
    InboxBusy,
    InboxBusyNotStarted,
)
from rtb.sqlitekit import DatabaseBusy
from tests.capability_samples import TEST_APPROVAL_KEY, TEST_KEY
from tests.executor.conftest import Clock
from tests.executor.fakes import Harness, proposal


class BusyBegin:
    """接下來 left 次開寫入交易都等鎖逾時(什麼都沒寫);hits 記撞了幾次。

    補寫呼叫紀錄的短交易不算在內(每次 DSP 呼叫當下就補寫,撞忙本來就留待下次、不在這次範圍):
    補寫期間暫停造忙,忙碌只落在記結果的交易上。"""

    def __init__(self, monkeypatch):
        self.left, self.hits, self.paused = 0, 0, False
        real_begin, real_flush = sqlitekit.begin_immediate, Executor.flush_calls

        def begin(conn):
            if self.left and not self.paused:
                self.left -= 1
                self.hits += 1
                raise DatabaseBusy("database is locked")
            real_begin(conn)

        def flush_calls(executor):
            self.paused = True
            try:
                return real_flush(executor)
            finally:
                self.paused = False

        monkeypatch.setattr(sqlitekit, "begin_immediate", begin)
        monkeypatch.setattr(Executor, "flush_calls", flush_calls)

    def arm(self, times):
        self.left = times


def _worker(h, sleeps):
    return Executor(h.store, h.dsp, h.signer, h.config, h.clock, "executor", TEST_APPROVAL_KEY,
                    sleep=sleeps.append)


def _states(h, key_prefix=""):
    return [r[2] for r in h.attempts() if r[0].startswith(key_prefix)]


# ---- [S687] 開寫入交易鎖不到:在限度內重試同一個交易,成功就照常寫完與確認 ----
def test_a_busy_result_write_is_retried_within_its_budget(tmp_path, clock, monkeypatch):
    busy, sleeps = BusyBegin(monkeypatch), []
    h = Harness(tmp_path, clock)
    try:
        worker = _worker(h, sleeps)
        h.submit()
        h.dsp.on_write = lambda *_a: busy.arm(2)  # DSP 回覆之後,記結果的前兩次開交易鎖不到
        assert worker.process_one().kind is Result.EXECUTED
        assert busy.hits == 2
        assert sleeps == [0.05, 0.1]
        assert _states(h) == ["in_flight", "committed_unverified", "verified"]
        assert len(h.dsp.writes) == 1  # 重試不呼叫 DSP
        assert h.proposals()[0][2:4] == ("pending", "handed_off")  # 寫到終點同時確認

        _verification_timeout_is_retried_too(h, worker, busy, sleeps)
    finally:
        h.close()


def _verification_timeout_is_retried_too(h, worker, busy, sleeps):
    """記一次查證逾時那一支也一樣:查證讀取失敗後,記逾時的交易鎖不到一次就重試。"""
    sleeps.clear()
    reads = {"n": 0}

    def on_read(_campaign):
        reads["n"] += 1
        if reads["n"] == 2:  # 第 2 次讀取是寫入後的查證:讓它失敗,記逾時的交易鎖不到一次
            h.dsp.read_failures = 1
            busy.arm(1)

    h.dsp.on_write, h.dsp.on_read = None, on_read
    h.submit(task_id="t2", campaign_id="c2")
    assert worker.process_one().kind is Result.EXECUTED
    assert busy.hits == 3
    assert sleeps == [0.05]
    rows = h.query("SELECT state, verification_timeouts FROM attempts WHERE campaign_id = 'c2' "
                   "ORDER BY seq")
    assert rows[-1] == ("committed_unverified", 1)


# ---- [S688] 重試用完仍然鎖不到:照現行行為往外丟忙碌,不多寫、留給對帳 ----
def test_a_result_write_that_stays_busy_gives_up_as_before(tmp_path, clock, monkeypatch):
    busy, sleeps = BusyBegin(monkeypatch), []
    h = Harness(tmp_path, clock)
    try:
        h.submit()
        h.dsp.on_write = lambda *_a: busy.arm(99)
        with pytest.raises(InboxBusy):
            _worker(h, sleeps).process_one()
        busy.arm(0)  # 查資料庫之前先解除造忙
        assert busy.hits == 4  # 第一次加重試 3 次
        assert sleeps == [0.05, 0.1, 0.2]
        assert _states(h) == ["in_flight"]  # 停在寫入前的狀態,沒有多寫任何一列
        assert h.proposals()[0][2:4] == ("pending", "in_progress")
        assert len(h.dsp.writes) == 1
    finally:
        h.close()


# ---- [S689] 只有開寫入交易鎖不到才重試 ----
def test_only_a_busy_begin_on_a_result_write_is_retried(tmp_path, clock, monkeypatch):
    busy, sleeps = BusyBegin(monkeypatch), []
    h = Harness(tmp_path, clock)
    try:
        _busy_after_the_transaction_began_is_not_retried(h, _worker(h, sleeps), sleeps,
                                                         monkeypatch)
        _busy_writing_in_flight_is_not_retried(h, _worker(h, sleeps), busy, sleeps, monkeypatch)
    finally:
        h.close()


def _busy_after_the_transaction_began_is_not_retried(h, worker, sleeps, monkeypatch):
    """交易開起來之後才撞到的忙碌(例如寫到一半):是收件口忙碌、不是開交易鎖不到,照舊往外丟。"""
    real, calls = attempt_store.transition, {"n": 0, "armed": False}

    def transition(*args, **kwargs):
        if calls["armed"]:
            calls["n"] += 1
            raise DatabaseBusy("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(attempt_store, "transition", transition)
    h.submit()
    h.dsp.on_write = lambda *_a: calls.update(armed=True)
    with pytest.raises(InboxBusy) as raised:
        worker.process_one()
    assert not isinstance(raised.value, InboxBusyNotStarted)
    assert calls["n"] == 1
    assert sleeps == []
    monkeypatch.setattr(attempt_store, "transition", real)
    h.dsp.on_write = None


def _busy_writing_in_flight_is_not_retried(h, worker, busy, sleeps, monkeypatch):
    """憑證過期後同鍵重送前轉回嘗試中:不是「記下平台回覆」,不在這次範圍,鎖不到照舊往外丟。"""
    real = Executor._in_flight_again

    def armed(self, *args, **kwargs):
        busy.arm(1)  # 下一次開交易就是轉回嘗試中的那一個
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Executor, "_in_flight_again", armed)
    h.submit(task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    before = busy.hits
    with pytest.raises(InboxBusyNotStarted):
        worker.process_one()
    assert busy.hits == before + 1
    assert sleeps == []
    assert _states(h)[-1] == "unknown"  # 記下的是過期那一次的結果不明,轉回嘗試中沒寫進去


# ---- [S690] 租約快到期或讀不到:不再重試 ----
def test_a_result_write_stops_retrying_when_the_lease_is_about_to_expire(tmp_path, monkeypatch):
    busy = BusyBegin(monkeypatch)

    def near_expiry(h):
        h.clock.advance(seconds=52)  # 租約剩 8 秒,小於 5 秒等鎖上限 + 退避 + 5 秒餘裕

    def lease_left(h):
        h.clock.advance(seconds=40)  # 對照:租約剩 20 秒,夠等一次鎖加餘裕,照常重試

    def taken_over(h):
        h.query("UPDATE proposals SET lease_owner = 'w2'")  # 讀不到自己的租約(被別人接手)

    assert _one_busy_write(tmp_path / "near", busy, near_expiry) == (False, [])
    assert _one_busy_write(tmp_path / "left", busy, lease_left) == (True, [0.05])
    assert _one_busy_write(tmp_path / "taken", busy, taken_over) == (False, [])


def _one_busy_write(directory, busy, before_busy):
    """DSP 寫入之後先做 before_busy,再讓記結果的開交易鎖不到一次;回(有沒有寫完, 退避序列)。"""
    directory.mkdir()
    h, sleeps = Harness(directory, Clock()), []  # 每一種情境各自的時鐘,互不累加
    try:
        h.submit()

        def on_write(*_a):
            before_busy(h)
            busy.arm(1)

        h.dsp.on_write = on_write
        try:
            done = _worker(h, sleeps).process_one().kind is Result.EXECUTED
        except InboxBusyNotStarted:
            done = False
        return done, sleeps
    finally:
        h.close()


# ---- [S687] 入口接線:啟動程式把自己的睡眠交給執行迴圈,退避才真的會睡 ----
def test_the_runner_hands_its_sleep_to_the_result_write_retry(tmp_path, clock, monkeypatch):
    busy = BusyBegin(monkeypatch)
    h = Harness(tmp_path, clock)
    try:
        h.submit()
        h.dsp.on_write = lambda *_a: busy.arm(1)
        slept = []
        code = runner.run(
            ["--db", str(h.db), "--dsp-url", "http://127.0.0.1:9", "--tenant-config",
             str(h.config), "--interval-seconds", "0.01"],
            environ={KEY_ENV: TEST_KEY.decode()}, clock=h.clock, dsp=h.dsp,
            out=io.StringIO(), sleep=slept.append, max_rounds=1, owner="executor")
        assert code == 0
        assert busy.hits == 1
        assert slept == [0.05]  # 退避經啟動程式的睡眠;這輪有進展,不另休息一個間隔
        assert _states(h) == ["in_flight", "committed_unverified", "verified"]
    finally:
        h.close()


# ---- [S690] 沒有收據的舊鍵:沒有租約可讀,只受次數限制 ----
def test_a_legacy_key_without_a_receipt_retries_on_count_alone(tmp_path, clock, monkeypatch):
    busy, sleeps = BusyBegin(monkeypatch), []
    h = Harness(tmp_path, clock)
    try:
        prop = proposal()
        with h.store.transaction() as tx:  # 收件表沒有對應處理中訊息的舊鍵(Phase 3 時代留下)
            row = attempt_store.begin(tx, prop, h.clock(), capability_expires_at=h.clock()).row
            attempt_store.transition(tx, row.key, row.seq, AttemptState.COMMITTED_UNVERIFIED,
                                     h.clock(), written_version=4)
        clock.advance(hours=1)  # 若誤用租約期限,早就過期、一次都不會重試
        h.dsp.on_read = lambda _c: busy.arm(99)  # 查證讀取之後,記結果的開交易一直鎖不到
        with pytest.raises(InboxBusyNotStarted):
            _worker(h, sleeps).reconcile_all()
        busy.arm(0)
        assert busy.hits == 4  # 第一次加重試 3 次:只受次數限制
        assert sleeps == [0.05, 0.1, 0.2]
        assert _states(h) == ["in_flight", "committed_unverified"]  # 沒有多寫任何一列

        sleeps.clear()
        h.dsp.on_read = lambda _c: busy.arm(2)  # 鎖不到兩次之後拿到:照常寫完
        _worker(h, sleeps).reconcile_all()
        assert sleeps == [0.05, 0.1]
        assert len(_states(h)) == 3
    finally:
        h.close()


# ---- 代碼審第 2 輪:[S690] 期限的邊界,與讀租約的錯誤處理 ----
def test_the_lease_deadline_boundary_is_pinned(tmp_path, monkeypatch):
    """剩餘時間剛好等於「等鎖上限 + 退避 + 餘裕」要重試;少 1 微秒就停。"""
    busy = BusyBegin(monkeypatch)
    needed = (timedelta(seconds=sqlitekit.BUSY_TIMEOUT_SECONDS + RESULT_WRITE_BACKOFF_SECONDS[0])
              + RESULT_WRITE_LEASE_MARGIN)
    advance = VISIBILITY_TIMEOUT - needed  # 取件時的租約是一個租約時間;時鐘沒動過

    def exactly(h):
        h.clock.advance(seconds=advance.total_seconds())

    def one_microsecond_short(h):
        h.clock.advance(seconds=advance.total_seconds(), microseconds=1)

    assert _one_busy_write(tmp_path / "exact", busy, exactly) == (True, [0.05])
    assert _one_busy_write(tmp_path / "short", busy, one_microsecond_short) == (False, [])


def _received(h):
    h.submit()
    with h.store.transaction() as tx:
        return h.store.receive(tx, h.clock(), "executor").receipt


def test_lease_until_answers_only_for_the_same_lease(tmp_path, clock):
    """租約序號或擁有者對不上(同一個擁有者過期後自己又接手也一樣,序號換了)就回空值。"""
    h = Harness(tmp_path, clock)
    try:
        receipt = _received(h)
        assert h.store.lease_until(receipt) == clock() + VISIBILITY_TIMEOUT
        assert h.store.lease_until(replace(receipt, lease_seq=receipt.lease_seq + 1)) is None
        assert h.store.lease_until(replace(receipt, lease_seq=receipt.lease_seq - 1)) is None
        assert h.store.lease_until(replace(receipt, owner="w2")) is None
    finally:
        h.close()


class _LeaseReadFails:
    """包住收件表的連線:讀租約那一句丟指定的資料庫錯誤,其他照常。"""

    def __init__(self, conn, error):
        self._real, self._error = conn, error

    def execute(self, sql, *args):
        if sql.startswith("SELECT lease_until"):
            raise self._error
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_lease_read_error_is_classified_like_the_rest_of_the_project(tmp_path, clock,
                                                                         monkeypatch):
    """讀租約碰到鎖競爭:當作讀不到、不再試;其他資料庫錯誤原樣往外丟,不能被改報成忙碌。"""
    busy = BusyBegin(monkeypatch)
    contention = sqlite3.OperationalError("database is locked")
    contention.sqlite_errorcode = sqlite3.SQLITE_BUSY
    permanent = sqlite3.OperationalError("no such table: proposals")
    permanent.sqlite_errorcode = sqlite3.SQLITE_ERROR
    for name, error, expected in (("busy", contention, InboxBusyNotStarted),
                                  ("broken", permanent, sqlite3.OperationalError)):
        directory = tmp_path / name
        directory.mkdir()
        h, sleeps = Harness(directory, clock), []
        try:
            h.submit()
            h.dsp.on_write = lambda *_a, h=h, error=error: (
                setattr(h.store, "_conn", _LeaseReadFails(h.store._conn, error)), busy.arm(1))
            with pytest.raises(expected) as raised:
                _worker(h, sleeps).process_one()
            if expected is sqlite3.OperationalError:
                assert raised.value is error  # 永久錯誤原樣往外丟,不換成忙碌
            assert sleeps == []
        finally:
            h.store._conn = getattr(h.store._conn, "_real", h.store._conn)
            h.close()


@pytest.mark.parametrize("stored", ["garbage", "2026-09-25T10:00:00"])
def test_an_unreadable_lease_time_halts_instead_of_crashing(tmp_path, clock, monkeypatch,
                                                            stored):
    """租約時間讀得出來卻不是帶時區的 ISO 時間:丟收件表既有的「處理中那一列讀不懂」,執行迴圈照既有
    做法停下讓人看,不是丟 ValueError 或 TypeError 讓行程崩潰。"""
    h = Harness(tmp_path, clock)
    try:
        receipt = _received(h)
        h.query("UPDATE proposals SET lease_until = ?", (stored,))
        with pytest.raises(CorruptedInboxRow):
            h.store.lease_until(receipt)
    finally:
        h.close()

    busy, sleeps = BusyBegin(monkeypatch), []
    directory = tmp_path / "loop"
    directory.mkdir()
    h = Harness(directory, clock)
    try:
        h.submit()
        h.dsp.on_write = lambda *_a: (
            h.query("UPDATE proposals SET lease_until = ?", (stored,)), busy.arm(1))
        with pytest.raises(ExecutorHalted) as raised:
            _worker(h, sleeps).process_one()
        assert str(raised.value) == "unreadable_message"
        assert isinstance(raised.value.__cause__, CorruptedInboxRow)
        assert sleeps == []
    finally:
        h.close()
