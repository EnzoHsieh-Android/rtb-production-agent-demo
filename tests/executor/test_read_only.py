"""執行行程資料庫的唯讀讀法(Phase 9 增量 1):[S610]、[S611]、[S616]、[S621]。

唯讀開法開出來的物件只有唯讀連線、只發唯讀交易(另一個類別、另一個私有發行憑證);讀取函式收寫入
交易或唯讀交易,寫入函式只收寫入交易。哪些是寫入函式由原始碼機械算出,不靠名字。
"""

import inspect
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from rtb.domain.attempt import operation_key
from rtb.executor import attempt_store, inbox_store, observability
from rtb.executor.attempt_store import NotInTransaction, Reservation
from rtb.executor.inbox_store import (
    BlockCode,
    InboxStore,
    PendingProposal,
    ReadOnlyInbox,
    Stop,
    StopKind,
)
from tests.executor.conftest import NOW
from tests.executor.fakes import proposal
from tests.executor.write_scan import read_functions, write_functions

EXECUTOR = Path(__file__).resolve().parents[2] / "src" / "rtb" / "executor"
TENANT = "t-default"
PROP = proposal(task_id="r1")
KEY = operation_key(PROP)
LATER = NOW + timedelta(hours=1)


@pytest.fixture
def store(tmp_path):
    opened = InboxStore(tmp_path / "executor.db")
    yield opened
    opened.close()


@pytest.fixture
def reader(store, tmp_path):  # noqa: ARG001 - 要先有寫入開法建好的資料庫
    opened = ReadOnlyInbox(tmp_path / "executor.db", busy_timeout_seconds=0.1)
    yield opened
    opened.close()


def _populate(store):
    """每一張讀取函式會讀的表都放一點資料:收件、停下、核可使用、嘗試、呼叫紀錄、事件。"""
    store.accept(PROP, lambda: NOW)
    with store.transaction() as tx:
        delivery = store.receive(tx, NOW, "w1")
        store.record_stop(tx, Stop(StopKind.AGGREGATE_LIMIT_REACHED, PROP, KEY, TENANT, 10, 95,
                                   100), NOW)
        store.await_approval(tx, delivery.receipt, NOW, BlockCode.AGGREGATE_LIMIT_REACHED)
    other = proposal(task_id="r2", campaign_id="c2", requested_change={"new_budget": 110})
    with store.transaction() as tx:
        begun = attempt_store.begin(tx, other, NOW, capability_expires_at=NOW + timedelta(
            minutes=5), reservation=Reservation(TENANT, 10, 100))
        attempt_store.record_dsp_call(
            tx, attempt_store.DspCall(attempt_store.DspCallKind.WRITE,
                                      attempt_store.DspCallResult.RESPONDED, 200, 1.5, None),
            attempt_store.CallSubject("r2", 1, "c2", begun.row.key, "h-r2"),
            attempt_store.Actor(attempt_store.Source.EXECUTOR_LOOP, "w1"), NOW)
    return begun.row.key


def _read_samples(key):
    """每一支讀取函式的一組參數(第一個參數交易由測試填)。"""
    message = PendingProposal("r1", 1, inbox_store.content_hash(PROP), PROP)
    return {
        "InboxReads.awaiting": ("s", (LATER,)),
        "InboxReads.has_newer_revision": ("s", ("r1", 1)),
        "InboxReads.stop_amount": ("s", (message, BlockCode.AGGREGATE_LIMIT_REACHED)),
        "InboxReads.latest_approval": ("s", (PROP, BlockCode.AGGREGATE_LIMIT_REACHED)),
        "InboxReads.used_approvals": ("s", (PROP,)),
        "InboxReads.in_progress_for": ("s", ("r1", KEY)),
        "InboxReads.in_progress_keys": ("s", ()),
        "InboxReads.awaiting_count": ("s", ()),
        "InboxReads.approval_use_count": ("s", ()),
        "InboxReads.stop_count": ("s", (StopKind.AGGREGATE_LIMIT_REACHED,)),
        "InboxReads.stops": ("s", (StopKind.AGGREGATE_LIMIT_REACHED, TENANT, NOW, LATER)),
        "InboxReads.lifecycle_events": ("s", ("r1",)),
        "InboxReads.last_terminal_event": ("s", ("r1", 1, inbox_store.content_hash(PROP))),
        "InboxReads.dead_letters_for": ("s", ("r1",)),
        "InboxReads.dead_letter_ops_for": ("s", ("r1",)),
        "latest": ("a", (key,)),
        "history": ("a", (key,)),
        "snapshot": ("a", (key,)),
        "trace_rows": ("a", (key,)),
        "first_row_tenant": ("a", (key,)),
        "unresolved_count": ("a", ()),
        "version_conflict_count": ("a", ()),
        "campaigns_with_unresolved": ("a", ()),
        "unresolved_keys": ("a", ()),
        "aggregate_used": ("a", (TENANT, NOW)),
        "aggregate_holdings": ("a", (TENANT, NOW)),
        "counted_first_rows_started": ("a", (TENANT, NOW - timedelta(hours=1), LATER)),
        "dsp_calls_for": ("a", ("r2",)),
    }


def _takes_transaction(module, qualname):
    owner = module
    for part in qualname.split("."):
        owner = getattr(owner, part)
    params = list(inspect.signature(owner).parameters)
    return "tx" in params[:2]


def _nones(fn):
    """只為了走到守衛:必填的位置參數與關鍵字參數都填空值(交易之外)。"""
    params = list(inspect.signature(fn).parameters.values())[1:]
    positional = [None for p in params
                  if p.kind is p.POSITIONAL_OR_KEYWORD and p.default is p.empty]
    keywords = {p.name: None for p in params if p.kind is p.KEYWORD_ONLY and p.default is p.empty}
    return positional, keywords


def _callable(store, qualname):
    if "." in qualname:
        return getattr(store, qualname.split(".")[1])
    return getattr(attempt_store, qualname)


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {table: conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


# ---- [S610] ----
def test_a_read_transaction_is_refused_by_every_write_function(store, reader, tmp_path):
    key = _populate(store)
    before = _tables(tmp_path / "executor.db")
    writers = [("inbox", name) for name in sorted(inbox_store.WRITE_FUNCTIONS)
               if _takes_transaction(inbox_store, name)]
    writers += [("attempt", name) for name in sorted(attempt_store.WRITE_FUNCTIONS)
                if _takes_transaction(attempt_store, name)]
    assert len(writers) >= 15
    with reader.read_transaction() as rtx:
        for where, name in writers:
            fn = _callable(store, name) if where == "inbox" else getattr(attempt_store, name)
            positional, keywords = _nones(fn)
            with pytest.raises(NotInTransaction):
                fn(rtx, *positional, **keywords)
    assert _tables(tmp_path / "executor.db") == before  # 什麼都沒寫

    # 讀取函式同時收兩種交易,結果一樣;清單要跟機械算出的讀取函式一致(新加的讀取函式要補進來)
    samples = _read_samples(key)
    mechanical = {name for module, prefix in ((inbox_store, "InboxReads."), (attempt_store, ""))
                  for name in read_functions(EXECUTOR / f"{module.__name__.rsplit('.', 1)[1]}.py")
                  if name.startswith(prefix) and (prefix or "." not in name)
                  and _takes_transaction(module, name)}
    assert mechanical == set(samples)
    for name, (where, args) in samples.items():
        fn = _callable(store, name) if where == "s" else getattr(attempt_store, name)
        rfn = _callable(reader, name) if where == "s" else fn
        with store.transaction() as tx:
            expected = fn(tx, *args)
        with reader.read_transaction() as rtx:
            assert rfn(rtx, *args) == expected, name

    # 關掉的唯讀交易、別的收件表物件開的唯讀交易都不收
    with reader.read_transaction() as rtx:
        closed = rtx
    with pytest.raises(NotInTransaction):
        reader.lifecycle_events(closed, "r1")
    with pytest.raises(NotInTransaction):
        attempt_store.latest(closed, key)
    with reader.read_transaction() as rtx, pytest.raises(NotInTransaction):
        store.lifecycle_events(rtx, "r1")
    with pytest.raises(NotInTransaction):  # 私有發行憑證:自己造不出唯讀交易
        attempt_store.ReadTransaction(reader._conn, object())


# ---- [S611] ----
def test_a_read_transaction_sees_one_snapshot(store, reader):
    store.accept(PROP, lambda: NOW)
    with reader.read_transaction() as rtx:
        store.accept(proposal(task_id="r9"), lambda: NOW)  # 開始之後、第一次查詢之前別人提交
        first = reader.lifecycle_events(rtx, "r9")
        store.accept(proposal(task_id="r8"), lambda: NOW)
        assert reader.lifecycle_events(rtx, "r8") == first == ()
        assert reader.in_progress_keys(rtx) == ((), ())
        assert len(reader.lifecycle_events(rtx, "r1")) == 1
    with reader.read_transaction() as rtx:
        assert len(reader.lifecycle_events(rtx, "r9")) == 1  # 下一個唯讀交易看得到


# ---- [S616] ----
def test_the_phase6_queries_run_under_a_read_transaction(store, reader, tmp_path):
    _populate(store)
    queries = [
        lambda s, tx: observability.aggregate_stop_count(s, tx, tenant=TENANT),
        lambda s, tx: observability.table_full_deferral_count(s, tx),
        lambda s, tx: observability.approval_counts(s, tx, tenant=TENANT, since=NOW, until=LATER),
        lambda _s, tx: observability.utilization(tx, TENANT, 100, NOW),
        lambda s, tx: observability.aggregate_audit(s, tx, TENANT, 100, NOW, NOW, LATER),
    ]
    with store.transaction() as tx:
        expected = [query(store, tx) for query in queries]
    holder = sqlite3.connect(tmp_path / "executor.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # 別人佔著寫入鎖:唯讀交易不排隊、照樣讀
    try:
        with reader.read_transaction() as rtx:
            assert [query(reader, rtx) for query in queries] == expected
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert expected[4].holding and expected[0] == 1


# ---- [S621] ----
def test_the_write_function_list_is_derived_not_declared():
    assert write_functions(EXECUTOR / "inbox_store.py") == inbox_store.WRITE_FUNCTIONS
    assert write_functions(EXECUTOR / "attempt_store.py") == attempt_store.WRITE_FUNCTIONS
    # 名字像讀、其實會寫的幾支一定在寫入清單裡
    assert {"InboxStore.receive", "InboxStore.settle_awaiting", "InboxStore.take_over"} <= (
        inbox_store.WRITE_FUNCTIONS)
    assert "recover_in_flight" in attempt_store.WRITE_FUNCTIONS


# ---- [S626] ----
def test_the_last_terminal_event_lookup_uses_the_terminal_index(store, reader):
    store.accept(PROP, lambda: NOW)
    digest = inbox_store.content_hash(PROP)
    with store.transaction() as tx:
        delivery = store.receive(tx, NOW, "w1")
        store.ack_blocked(tx, delivery.receipt, NOW, BlockCode.VERSION_CHANGED)
    with reader.read_transaction() as rtx:
        found = reader.last_terminal_event(rtx, "r1", 1, digest)
        assert (found.kind, found.reason) == ("blocked", "version_changed")
        assert reader.last_terminal_event(rtx, "r1", 1, "other-hash") is None
    terminal = {inbox_store.LifecycleKind.HANDED_OFF, inbox_store.LifecycleKind.BLOCKED,
                inbox_store.LifecycleKind.EXPIRED, inbox_store.LifecycleKind.DEAD_LETTERED}
    assert terminal == inbox_store.TERMINAL_KINDS

    sql, params = inbox_store.last_terminal_event_query("r1", 1, digest)
    with store.transaction() as tx:
        plan = " ".join(row[-1] for row in tx.conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))
    assert "lifecycle_events_terminal" in plan, plan
    assert "SCAN lifecycle_events" not in plan.replace("USING", ""), plan
