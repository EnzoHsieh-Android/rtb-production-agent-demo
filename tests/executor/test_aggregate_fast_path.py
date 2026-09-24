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
            snapshot=None, first_state=A.IN_FLIGHT, started=None, middle=(), key=None):
        """started 可給第一列時間欄的原始值(造型別異常);middle 是終點之前的中間狀態列;
        key 可給鍵的原始值(造二進位的鍵)。"""
        self.count += 1
        key = f"k{self.count}" if key is None else key
        if snapshot is None:
            snapshot = json.dumps({"requested_change": {"new_budget": 40}})
        if started is None:
            started = _iso(at - timedelta(minutes=1))
        if state in OPEN_STATES:
            first_state = state
        self.rows.append((key, 1, "c1", first_state.value, started, action, snapshot,
                          tenant, amount))
        seq = 1
        for step in middle:
            seq += 1
            self.rows.append((key, seq, "c1", step.value, _iso(at - timedelta(seconds=30)),
                              None, None, None, None))
        if state in (A.VERIFIED, A.FAILED):
            self.rows.append((key, seq + 1, "c1", state.value, _iso(at), None, None, None,
                              None))
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
AMOUNTS = (0, 1, 7, 50, 1000, 123_456, None)
HUGE_AMOUNTS = (MAX_INT // 3, MAX_INT - 5, MAX_INT)  # 溢位級:少量出現,純快路徑才過半
ODD_AMOUNTS = ("abc", "x12", 1.5, 2.75, b"\x01", -3)
ODD_TENANTS = ("T-A", "t-a ", "", b"t-a", 1)  # 大小寫、尾端空白、空字串、二進位、整數
ODD_STARTED = (b"\x00", 12345, 1.5)  # 第一列時間欄型別異常(原算法排序時比不了)
HISTORIES = ((), (A.UNKNOWN,), (A.UNKNOWN, A.COMMITTED_UNVERIFIED),
             (A.ESCALATED,))  # 終點之前的中間狀態(多次嘗試、轉人工後再結案)


def random_rows(rng):
    rows = Rows()
    for _ in range(rng.randint(0, 25)):
        roll = rng.random()
        tenant = None if roll < 0.02 else rng.choice(TENANTS)
        if rng.random() < 0.03:
            tenant = rng.choice(ODD_TENANTS)
        amount = rng.choice(AMOUNTS)
        if rng.random() < 0.02:
            amount = rng.choice(HUGE_AMOUNTS)
        if rng.random() < 0.02:
            amount = rng.choice(ODD_AMOUNTS)
        started = rng.choice(ODD_STARTED) if rng.random() < 0.01 else None
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
        middle = rng.choice(HISTORIES) if state in (A.VERIFIED, A.FAILED) else ()
        rows.key(tenant=tenant, amount=amount, state=state, at=NOW - age, action=action,
                 snapshot=snapshot, started=started, middle=middle)
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
    # 代碼審第 1 輪 x1:第一列時間欄是二進位,原算法排序時比不了、丟 TypeError;快路徑要一樣
    "odd_started_time": lambda r: [r.key(tenant="t-a", amount=5, state=A.VERIFIED,
                                         started=b"\x00"),
                                   r.key(tenant="t-a", amount=7, state=A.VERIFIED)],
    "odd_started_integer": lambda r: [r.key(tenant="t-a", amount=5, state=A.VERIFIED,
                                            started=12345),
                                      r.key(tenant="t-a", amount=6, state=A.VERIFIED)],
    # 鍵是二進位、時間跟另一列一樣:原算法排序比到鍵時比不了
    "odd_key_type": lambda r: [r.key(tenant="t-a", amount=5, state=A.VERIFIED, key=b"k-bin",
                                     started="2026-09-22T11:00:00.000000Z"),
                               r.key(tenant="t-a", amount=6, state=A.VERIFIED,
                                     started="2026-09-22T11:00:00.000000Z")],
    "history_rows": lambda r: r.key(tenant="t-a", amount=8, state=A.VERIFIED,
                                    middle=(A.UNKNOWN, A.COMMITTED_UNVERIFIED)),
    "odd_tenants": lambda r: [r.key(tenant=t, amount=9, state=A.VERIFIED)
                              for t in ("T-A", "t-a ", "", b"t-a")],
    "failed_and_paused": lambda r: [r.key(tenant="t-a", amount=30, state=A.FAILED),
                                    r.key(tenant="t-a", amount=0, state=A.VERIFIED,
                                          action="pause")],
}


def test_the_fast_aggregate_used_matches_the_reference_on_the_same_data(store, monkeypatch):
    real_reference = attempt_store.aggregate_used_reference
    fell_back = []
    monkeypatch.setattr(attempt_store, "aggregate_used_reference",
                        lambda tx, tenant, now: fell_back.append(1) or real_reference(tx, tenant,
                                                                                   now))
    for name, build in DETERMINISTIC.items():
        rows = Rows()
        build(rows)
        with written(store, rows) as tx:
            fast = outcome(attempt_store.aggregate_used, tx, "t-a")
            reference = outcome(real_reference, tx, "t-a")
        assert fast == reference, (name, rows.rows)
    rng = random.Random(SEED)
    pure_fast = fell_back_values = values = 0
    for index in range(DATASETS):
        rows = random_rows(rng)
        tenant = rng.choice(TENANTS)
        fell_back.clear()
        with written(store, rows) as tx:
            fast = outcome(attempt_store.aggregate_used, tx, tenant)
            reference = outcome(real_reference, tx, tenant)
        assert fast == reference, f"種子 {SEED} 第 {index} 份,租戶 {tenant}:{rows.rows}"
        pure_fast += not fell_back  # 只數完全沒呼叫原算法的(退回的不算)
        fell_back_values += bool(fell_back) and fast[0] == "value"
        values += fast[0] == "value"
    assert pure_fast > DATASETS // 2, pure_fast  # 純快路徑真的過半
    assert fell_back_values > 0 and pure_fast + fell_back_values == values  # 退回也真的比到


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
                           and "CONSTANT ROW" not in step
                           and "(subquery" not in step)  # 掃的是有上限的子查詢結果,不是表
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
    # 只回語句的那支(給測試看查詢計畫):加上參數跑 EXPLAIN,跟追蹤到的計畫一致(代碼審第 1 輪 a1)
    with written(store, history) as tx:
        for sql, params in attempt_store.aggregate_used_queries("t-a", NOW):
            plan = tuple(row[3] for row in tx.conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))
            assert plan in plans, (plan, plans)


# ---- 代碼審第 1 輪 s1:退回路徑握鎖內的工作量不超過原算法 ----
def _vm_steps(conn, function):
    """函式執行期間 SQLite 虛擬機跑了幾步(每 100 步記一次):握鎖內工作量的確定性量法。"""
    steps = [0]

    def tick():
        steps[0] += 1
        return 0

    conn.set_progress_handler(tick, 100)
    try:
        value = function()
    finally:
        conn.set_progress_handler(None, 100)
    return value, steps[0]


def test_falling_back_on_a_legacy_row_does_no_more_work_than_the_reference(store):
    """候選裡有一筆沒記租戶的舊列(轉人工、沒終點,會一直留著):快路徑要先用便宜的偵測發現它、
    直接走原算法,不先把整段已驗證列加總一遍;退回路徑的虛擬機步數最多比原算法多一點點(偵測本身,
    只跟舊列數有關、不跟窗內筆數有關)。"""
    rows = Rows()
    for index in range(4000):
        rows.key(tenant="t-a", amount=index % 90 + 1, state=A.VERIFIED)
    old = NOW - AGGREGATE_WINDOW - timedelta(days=400)
    for _ in range(attempt_store.LEGACY_PROBE_LIMIT * 3):  # 很多窗外已結案的舊列(不是候選)
        rows.key(tenant=None, amount=None, state=A.VERIFIED, at=old)
    rows.key(tenant=None, amount=None, state=A.ESCALATED)  # 舊列:沒終點,不受窗口限制
    with written(store, rows) as tx:
        fast, fast_steps = _vm_steps(tx.conn, lambda: attempt_store.aggregate_used(tx, "t-a", NOW))
        slow, slow_steps = _vm_steps(
            tx.conn, lambda: attempt_store.aggregate_used_reference(tx, "t-a", NOW))
    assert fast == slow
    assert fast_steps <= slow_steps + 5, (fast_steps, slow_steps)


def test_many_closed_legacy_rows_still_take_the_fast_path(store, monkeypatch):
    """窗外已結案的舊列很多(超過便宜偵測的上限)也不觸發退回,偵測本身的工作量有上限。"""
    def forbidden(*_args):
        raise AssertionError("乾淨的候選不該呼叫原算法")

    monkeypatch.setattr(attempt_store, "aggregate_used_reference", forbidden)
    rows = Rows()
    _clean(rows)
    old = NOW - AGGREGATE_WINDOW - timedelta(days=400)
    for _ in range(attempt_store.LEGACY_PROBE_LIMIT * 20):
        rows.key(tenant=None, amount=None, state=A.VERIFIED, at=old)
    with written(store, rows) as tx:
        assert attempt_store.aggregate_used(tx, "t-a", NOW) == 5 + 7 + 11 + 13
        sql, params = attempt_store.legacy_candidate_query(NOW)
        _, probe_steps = _vm_steps(tx.conn, lambda: tx.conn.execute(sql, params).fetchall())
    assert probe_steps <= attempt_store.LEGACY_PROBE_LIMIT  # 每 100 步記一次:偵測只看有上限的幾筆


# ---- 代碼審第 1 輪 e3 ----
def test_the_tenant_must_be_a_string(store):
    """資料庫比較租戶時會把整數參數轉型(1 = '1' 成立),程式比較不會;等價的前提寫死在入口。"""
    rows = Rows()
    rows.key(tenant="1", amount=50, state=A.VERIFIED)
    with written(store, rows) as tx:
        for tenant in (1, True, b"1", None):
            with pytest.raises(TypeError):
                attempt_store.aggregate_used(tx, tenant, NOW)
