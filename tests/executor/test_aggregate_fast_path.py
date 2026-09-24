"""F7 效能:已用額度的資料庫端加總(快路徑)與原算法等價(計劃 [[Projects/F7效能_計劃]],S680 到 S683)。

快路徑只在資料庫加總「屬於這個租戶、金額是整數」那一種,其餘(Phase 6 之前沒記租戶的舊列、金額型別
不是整數或空值、資料庫整數溢位)退回原算法,結果以原算法為準。這裡直接寫嘗試表的列造資料(涵蓋正常
流程寫不出來的舊列與型別異常),在同一份資料上比新舊兩種算法。
"""

import json
import random
import sqlite3
from contextlib import contextmanager
from datetime import timedelta

import pytest

from rtb import sqlitekit
from rtb.domain.attempt import AttemptState
from rtb.domain.proposal import MAX_INT
from rtb.executor import attempt_store
from rtb.executor.attempt_store import AGGREGATE_WINDOW
from rtb.executor.inbox_store import InboxStore
from tests.executor.conftest import NOW

A = AttemptState
SEED = 20260924
DATASETS = 500
TENANTS = ("t-a", "t-b")
OPEN_STATES = (A.IN_FLIGHT, A.UNKNOWN, A.COMMITTED_UNVERIFIED, A.ESCALATED)


@pytest.fixture
def store(tmp_path):
    inbox = InboxStore(tmp_path / "inbox.db")
    yield inbox
    inbox.close()


def _iso(moment):
    return attempt_store.iso(moment)


class Rows:
    """直接寫嘗試表的列:第一列(帶租戶、金額、動作、快照)加上可有可無的終點列。"""

    def __init__(self):
        self.rows = []
        self.count = 0

    def key(self, *, tenant, amount, state, at=NOW - timedelta(hours=1), action="update_budget",  # noqa: PLR0913 - 一把鍵的每個欄位
            snapshot=None, first_state=A.IN_FLIGHT):
        self.count += 1
        key = f"k{self.count}"
        if snapshot is None:
            snapshot = json.dumps({"requested_change": {"new_budget": 40}})
        started = at - timedelta(minutes=1)
        if state in OPEN_STATES:
            first_state = state
        self.rows.append((key, 1, "c1", first_state.value, _iso(started), action, snapshot,
                          tenant, amount))
        if state in (A.VERIFIED, A.FAILED):
            self.rows.append((key, 2, "c1", state.value, _iso(at), None, None, None, None))
        return key


@contextmanager
def written(store, rows):
    """把列寫進(清空後的)嘗試表,交出一個寫入交易給兩種算法讀。"""
    with store.transaction() as tx:
        tx.conn.execute("DELETE FROM attempts")
        tx.conn.executemany(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, action, proposal_json, tenant, reserved_amount) "
            "VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)", rows.rows)
        yield tx


def outcome(function, tx, tenant, now=NOW):
    try:
        return ("value", function(tx, tenant, now))
    except Exception as exc:
        return ("raised", type(exc).__name__, str(exc))


# ---- 隨機資料 ----
AMOUNTS = (0, 1, 7, 50, 1000, MAX_INT // 3, MAX_INT - 5, MAX_INT, None)
ODD_AMOUNTS = ("12", "abc", 1.5, 2.75, b"\x01", -3)


def random_rows(rng):
    rows = Rows()
    for _ in range(rng.randint(0, 25)):
        roll = rng.random()
        tenant = None if roll < 0.04 else rng.choice(TENANTS)
        amount = rng.choice(AMOUNTS)
        if rng.random() < 0.03:
            amount = rng.choice(ODD_AMOUNTS)
        action = "pause" if rng.random() < 0.15 else "update_budget"
        snapshot = None
        if tenant is None:
            amount = None
            budget = rng.choice((40, 0, MAX_INT, "x"))
            snapshot = json.dumps({"requested_change": {"new_budget": budget}})
            if rng.random() < 0.1:
                snapshot = "{broken"
        state = rng.choice((A.VERIFIED, A.VERIFIED, A.FAILED, *OPEN_STATES))
        age = rng.choice((timedelta(minutes=5), timedelta(hours=23), AGGREGATE_WINDOW,
                          AGGREGATE_WINDOW + timedelta(seconds=1), timedelta(days=3)))
        rows.key(tenant=tenant, amount=amount, state=state, at=NOW - age, action=action,
                 snapshot=snapshot)
    return rows


# ---- [S680] ----
DETERMINISTIC = {
    "nothing_for_this_tenant": lambda r: r.key(tenant="t-b", amount=50, state=A.VERIFIED),
    "only_verified": lambda r: [r.key(tenant="t-a", amount=a, state=A.VERIFIED) for a in (5, 7)],
    "only_open": lambda r: [r.key(tenant="t-a", amount=a, state=s)
                            for a, s in ((5, A.IN_FLIGHT), (9, A.ESCALATED))],
    "exactly_24_hours": lambda r: r.key(tenant="t-a", amount=11, state=A.VERIFIED,
                                        at=NOW - AGGREGATE_WINDOW),
    "same_moment": lambda r: [r.key(tenant="t-a", amount=a, state=A.VERIFIED, at=NOW)
                              for a in (1, 2, 3)],
    "other_tenant": lambda r: [r.key(tenant="t-b", amount=99, state=A.IN_FLIGHT),
                               r.key(tenant="t-a", amount=1, state=A.VERIFIED)],
    "tenant_with_null_amount": lambda r: r.key(tenant="t-a", amount=None, state=A.VERIFIED),
    "failed_and_paused": lambda r: [r.key(tenant="t-a", amount=30, state=A.FAILED),
                                    r.key(tenant="t-a", amount=0, state=A.VERIFIED,
                                          action="pause")],
}


def test_the_fast_aggregate_used_matches_the_reference_on_the_same_data(store):
    for name, build in DETERMINISTIC.items():
        rows = Rows()
        build(rows)
        with written(store, rows) as tx:
            fast = outcome(attempt_store.aggregate_used, tx, "t-a")
            reference = outcome(attempt_store.aggregate_used_reference, tx, "t-a")
        assert fast == reference, (name, rows.rows)
    rng = random.Random(SEED)
    fast_paths = 0
    for index in range(DATASETS):
        rows = random_rows(rng)
        tenant = rng.choice(TENANTS)
        with written(store, rows) as tx:
            fast = outcome(attempt_store.aggregate_used, tx, tenant)
            reference = outcome(attempt_store.aggregate_used_reference, tx, tenant)
        assert fast == reference, f"種子 {SEED} 第 {index} 份,租戶 {tenant}:{rows.rows}"
        fast_paths += fast[0] == "value"
    assert fast_paths > DATASETS // 2  # 隨機資料大多是乾淨的:真的比到快路徑


# ---- [S681] ----
def _spy_reference(monkeypatch):
    calls = []
    real = attempt_store.aggregate_used_reference

    def spy(tx, tenant, now):
        calls.append(tenant)
        return real(tx, tenant, now)

    monkeypatch.setattr(attempt_store, "aggregate_used_reference", spy)
    return calls


def test_the_fast_aggregate_used_falls_back_only_on_integer_overflow(store, monkeypatch):
    calls = _spy_reference(monkeypatch)
    # 同一段溢位:資料庫回報整數溢位 → 退回原算法、回原算法的結果
    rows = Rows()
    for _ in range(2):
        rows.key(tenant="t-a", amount=MAX_INT, state=A.VERIFIED)
    with written(store, rows) as tx:
        assert attempt_store.aggregate_used(tx, "t-a", NOW) == 2 * MAX_INT
    assert calls == ["t-a"]
    # 兩段各自沒溢位、相加超過上限:照算,不退回
    calls.clear()
    rows = Rows()
    rows.key(tenant="t-a", amount=MAX_INT, state=A.VERIFIED)
    rows.key(tenant="t-a", amount=MAX_INT, state=A.IN_FLIGHT)
    with written(store, rows) as tx:
        assert attempt_store.aggregate_used(tx, "t-a", NOW) == 2 * MAX_INT
    assert calls == []
    # 其他資料庫錯誤照舊往外丟,不退回(不然查詢寫錯會被悄悄吞掉)
    real_queries = attempt_store.aggregate_used_queries

    def broken(tenant, now):
        (sql, params), rest = real_queries(tenant, now)[0], real_queries(tenant, now)[1:]
        return ((sql.replace("reserved_amount", "no_such_column", 1), params), *rest)

    monkeypatch.setattr(attempt_store, "aggregate_used_queries", broken)
    rows = Rows()
    rows.key(tenant="t-a", amount=5, state=A.VERIFIED)
    with written(store, rows) as tx, pytest.raises(sqlite3.OperationalError):
        attempt_store.aggregate_used(tx, "t-a", NOW)
    assert calls == []


def test_only_the_integer_overflow_error_counts_as_overflow():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(MAX_INT,), (1,)])
    with pytest.raises(sqlite3.OperationalError) as overflow:
        conn.execute("SELECT SUM(a) FROM t").fetchone()
    assert sqlitekit.is_integer_overflow(overflow.value)
    with pytest.raises(sqlite3.OperationalError) as typo:
        conn.execute("SELECT SUM(b) FROM t").fetchone()
    assert not sqlitekit.is_integer_overflow(typo.value)
    assert not sqlitekit.is_integer_overflow(sqlite3.IntegrityError("integer overflow"))
    conn.close()


# ---- [S682] ----
def test_legacy_rows_and_odd_amount_types_fall_back_to_the_reference(store, monkeypatch):
    calls = _spy_reference(monkeypatch)
    cases = {
        "legacy_in_window": lambda r: r.key(tenant=None, amount=None, state=A.VERIFIED),
        "legacy_open": lambda r: r.key(tenant=None, amount=None, state=A.IN_FLIGHT),
        "legacy_unreadable": lambda r: r.key(tenant=None, amount=None, state=A.VERIFIED,
                                             snapshot="{broken"),
        "text_amount": lambda r: r.key(tenant="t-a", amount="abc", state=A.VERIFIED),
        "real_amount": lambda r: r.key(tenant="t-a", amount=1.5, state=A.VERIFIED),
        "blob_amount": lambda r: r.key(tenant="t-a", amount=b"\x01", state=A.IN_FLIGHT),
    }
    for name, build in cases.items():
        calls.clear()
        rows = Rows()
        rows.key(tenant="t-a", amount=10, state=A.VERIFIED)
        build(rows)
        with written(store, rows) as tx:
            fast = outcome(attempt_store.aggregate_used, tx, "t-a")
            reference = outcome(attempt_store.aggregate_used_reference, tx, "t-a")
        assert fast == reference, name
        assert calls[0] == "t-a", name  # 快路徑那一次呼叫退回了原算法
    # 窗外已結案的舊列與型別異常列不在候選集合:不觸發退回
    calls.clear()
    rows = Rows()
    rows.key(tenant="t-a", amount=10, state=A.VERIFIED)
    old = NOW - AGGREGATE_WINDOW - timedelta(days=700)
    rows.key(tenant=None, amount=None, state=A.VERIFIED, at=old)
    rows.key(tenant="t-a", amount="abc", state=A.VERIFIED, at=old)
    rows.key(tenant=None, amount=None, state=A.FAILED, at=NOW)
    with written(store, rows) as tx:
        assert attempt_store.aggregate_used(tx, "t-a", NOW) == 10
    assert calls == []


# ---- [S683] ----
def _traced_plans(tx, function):
    statements = []
    tx.conn.set_trace_callback(statements.append)
    try:
        value = function()
    finally:
        tx.conn.set_trace_callback(None)
    plans = []
    for sql in statements:
        if not sql.lstrip().upper().startswith("SELECT"):
            continue
        plans.append(tuple(row[3] for row in tx.conn.execute(f"EXPLAIN QUERY PLAN {sql}")))
    return value, statements, plans


def _clean(rows):
    for amount in (5, 7, 11):
        rows.key(tenant="t-a", amount=amount, state=A.VERIFIED)
    rows.key(tenant="t-a", amount=13, state=A.IN_FLIGHT)
    rows.key(tenant="t-b", amount=17, state=A.VERIFIED)


def test_the_fast_aggregate_used_takes_the_fast_path_on_clean_data(store, monkeypatch):
    def forbidden(*_args):
        raise AssertionError("乾淨資料不該呼叫原算法")

    monkeypatch.setattr(attempt_store, "aggregate_used_reference", forbidden, raising=False)
    rows = Rows()
    _clean(rows)
    with written(store, rows) as tx:
        value, statements, plans = _traced_plans(
            tx, lambda: attempt_store.aggregate_used(tx, "t-a", NOW))
    assert value == 5 + 7 + 11 + 13
    assert plans, statements
    for plan in plans:
        for step in plan:
            whole_table = (step.startswith("SCAN ") and "INDEX" not in step
                           and "CONSTANT ROW" not in step)
            assert not whole_table, (step, statements)
    verified = next(p for p in plans if any("attempts_verified_by_time" in s for s in p))
    assert any(s.startswith("SEARCH f USING") and ("PRIMARY KEY" in s or "autoindex" in s)
               for s in verified), verified
    # 窗外塞大量已結案歷史:查詢支數與查詢計畫都不變
    history = Rows()
    _clean(history)
    old = NOW - AGGREGATE_WINDOW - timedelta(days=30)
    for index in range(3000):
        history.key(tenant="t-a", amount=index + 1, state=A.VERIFIED, at=old)
        history.key(tenant="t-b", amount=index + 1, state=A.FAILED, at=old)
    with written(store, history) as tx:
        again, statements_later, plans_later = _traced_plans(
            tx, lambda: attempt_store.aggregate_used(tx, "t-a", NOW))
    assert again == value
    assert len(statements_later) == len(statements)
    assert plans_later == plans
