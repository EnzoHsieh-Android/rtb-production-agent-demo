"""收件表的處置欄位:執行一筆(Phase 3 增量 3)S39~S41。

「待處理」= 狀態是待處理而且處置是空的;已交給執行、已擋下的提案不再算待處理。
"""

import sqlite3

import pytest

from rtb.domain.proposal import parse_proposal
from rtb.executor import attempt_store, inbox_store
from rtb.executor.inbox_store import (
    RETENTION,
    BlockCode,
    Disposition,
    InboxFull,
    InboxStore,
)
from tests.domain.proposal_samples import valid
from tests.executor.conftest import Clock

OLD_SCHEMA = """
CREATE TABLE proposals (
    task_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'superseded', 'expired')),
    payload TEXT NOT NULL, expires_at TEXT NOT NULL, received_at TEXT NOT NULL,
    PRIMARY KEY (task_id, revision));
CREATE TABLE attempts (
    key TEXT NOT NULL, seq INTEGER NOT NULL, campaign_id TEXT NOT NULL, state TEXT NOT NULL,
    code TEXT, detail TEXT, send_count INTEGER NOT NULL, verification_timeouts INTEGER NOT NULL,
    written_at TEXT NOT NULL,
    task_id TEXT, revision INTEGER, action TEXT, expected_version INTEGER, proposal_json TEXT,
    PRIMARY KEY (key, seq));
INSERT INTO proposals VALUES ('t-old', 1, 'h', 'pending', '{}', '2026-09-22T12:30:00.000000Z',
    '2026-09-22T12:00:00.000000Z');
INSERT INTO attempts VALUES ('k1-old', 1, 'c9', 'unknown', NULL, NULL, 1, 0,
    '2026-09-22T12:00:00.000000Z', 't-old', 1, 'update_budget', 3, NULL);
"""


def proposal(**overrides):
    parsed = parse_proposal(valid(**overrides))
    assert parsed.proposal is not None, parsed.errors
    return parsed.proposal


@pytest.fixture
def store(tmp_path):
    inbox = InboxStore(tmp_path / "executor.db", max_pending=2)
    yield inbox
    inbox.close()


def accept(store, prop, clock=None):
    return store.accept(prop, clock or Clock())


def _receive(store, tx):
    """取件拿收據(Phase 4 增量 1 起,確認一律帶收據)。"""
    delivery = store.receive(tx, Clock()(), "w1")
    assert delivery is not None
    return delivery.receipt


def hand_off(store, prop):
    with store.transaction() as tx:
        receipt = _receive(store, tx)
        assert (receipt.task_id, receipt.revision) == (prop.task_id, prop.revision)
        return store.ack_handed_off(tx, receipt, Clock()())


def block(store, prop, code=BlockCode.VERSION_CHANGED):
    with store.transaction() as tx:
        receipt = _receive(store, tx)
        assert (receipt.task_id, receipt.revision) == (prop.task_id, prop.revision)
        return store.ack_blocked(tx, receipt, Clock()(), code)


def rows(tmp_path, sql="SELECT task_id, revision, state, disposition, block_code "
                      "FROM proposals ORDER BY task_id, revision"):
    conn = sqlite3.connect(tmp_path / "executor.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---- [S39] ----
def _old_database(db):
    old = sqlite3.connect(db)
    old.executescript(OLD_SCHEMA)
    old.execute("PRAGMA journal_mode=WAL")
    old.executescript(attempt_store.SCHEMA)  # 索引先建好:開連線不必搶寫入鎖
    old.close()


def _add_new_columns(db):
    other = sqlite3.connect(db, isolation_level=None)
    try:
        for table, columns in (("proposals", ("disposition TEXT", "block_code TEXT")),
                               ("attempts", ("written_version INTEGER",
                                             "capability_expires_at TEXT"))):
            for column in columns:
                other.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    finally:
        other.close()


def _columns(db):
    conn = sqlite3.connect(db)
    try:
        return ([r[1] for r in conn.execute("PRAGMA table_info(proposals)")],
                [r[1] for r in conn.execute("PRAGMA table_info(attempts)")])
    finally:
        conn.close()


def _assert_columns_added_once(db):
    proposal_columns, attempt_columns = _columns(db)
    assert proposal_columns.count("disposition") == proposal_columns.count("block_code") == 1
    assert attempt_columns.count("written_version") == 1
    assert attempt_columns.count("capability_expires_at") == 1


def test_an_old_inbox_database_gains_the_disposition_columns_once_without_losing_data(
    tmp_path, monkeypatch,
):
    # 單獨開啟:補上兩張表的新欄位,既有資料不變,補上的欄位也帶著列舉限制
    db = tmp_path / "executor.db"
    _old_database(db)
    InboxStore(db).close()
    InboxStore(db).close()  # 已補好的再開一次不會重補
    _assert_columns_added_once(db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT task_id, state, disposition, block_code FROM proposals"
                            ).fetchall() == [("t-old", "pending", None, None)]
        assert conn.execute("SELECT key, state, written_version, capability_expires_at "
                            "FROM attempts").fetchall() == [("k1-old", "unknown", None, None)]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE proposals SET block_code = 'whatever'")
    finally:
        conn.close()

    # 並行:它看到缺欄位、去等寫入鎖的期間,另一條連線先補好了;拿到鎖後要再查一次,不能重補
    raced = tmp_path / "raced.db"
    _old_database(raced)
    real = inbox_store.immediate_transaction
    calls = []

    def someone_else_migrates_first(conn):
        if not calls:
            _add_new_columns(raced)
        calls.append(1)
        return real(conn)

    monkeypatch.setattr(inbox_store, "immediate_transaction", someone_else_migrates_first)
    InboxStore(raced).close()
    assert calls  # 真的走到「缺欄位、去拿鎖」那一步
    _assert_columns_added_once(raced)


# ---- [S40] ----
@pytest.mark.parametrize("dispose", [hand_off, block], ids=["handed_off", "blocked"])
def test_taken_and_blocked_proposals_leave_pending_and_replay_their_disposition(
    store, tmp_path, dispose,
):
    first = proposal()
    accept(store, first)
    assert dispose(store, first) is True
    expected = "handed_off" if dispose is hand_off else "blocked"

    # 同鍵重送:回處置、不新增任何一筆
    before = rows(tmp_path)
    again = accept(store, first)
    assert (again.state, again.replayed) == (expected, True)
    assert rows(tmp_path) == before

    # 不佔待處理名額(上限 2):另外兩個任務都收得進來
    accept(store, proposal(task_id="t2"))
    accept(store, proposal(task_id="t3"))
    with pytest.raises(InboxFull):
        accept(store, proposal(task_id="t4"))

    # 不會被新修訂取代:同任務的修訂 2 進來,修訂 1 仍是原狀態加處置
    clock = Clock()
    store_big = InboxStore(tmp_path / "executor.db", max_pending=10)
    try:
        store_big.accept(proposal(revision=2), clock)
        state = rows(tmp_path, "SELECT state, disposition FROM proposals "
                               "WHERE task_id = 't1' AND revision = 1")
        assert state == [("pending", expected)]

        # 到期後不會被收件時的到期標記改成已過期(t2、t3 這種沒處置的才會)
        clock.advance(minutes=40)
        store_big.accept(proposal(task_id="t5", decision_created_at="2026-09-22T12:40:00+00:00",
                                  decision_expires_at="2026-09-22T13:10:00+00:00"), clock)
        assert rows(tmp_path, "SELECT state, disposition FROM proposals "
                               "WHERE task_id = 't1' AND revision = 1") == [("pending", expected)]
        assert rows(tmp_path, "SELECT state FROM proposals WHERE task_id = 't2'") == [
            ("expired",)]

        # 不會被當成待處理:過了保留期,整個任務照樣被清掉
        clock.advance(seconds=RETENTION.total_seconds())
        store_big.accept(proposal(task_id="t6", decision_created_at="2026-09-22T14:50:00+00:00",
                                  decision_expires_at="2026-09-22T15:20:00+00:00"), clock)
        assert rows(tmp_path, "SELECT COUNT(*) FROM proposals WHERE task_id = 't1'") == [(0,)]
    finally:
        store_big.close()


def test_a_pending_proposal_still_counts_as_pending(store):
    """對照組:沒有處置的提案照舊佔名額(確認上面那條測試不是因為上限沒作用才通過)。"""
    accept(store, proposal())
    accept(store, proposal(task_id="t2"))
    with pytest.raises(InboxFull):
        accept(store, proposal(task_id="t3"))


# ---- [S41] ----
def test_block_reasons_are_a_closed_list(store, tmp_path):
    assert {code.value for code in BlockCode} == {
        "campaign_not_found", "campaign_not_active", "version_changed", "campaign_not_allowed",
        "over_budget_cap", "operation_previously_failed",
        "budget_increase_too_large",  # Phase 6 增量 2 新增的單筆比例上限
        "aggregate_limit_reached"}  # Phase 6 增量 1 [S331]:總曝險已滿
    # 「處置恰好兩個成員」由 Phase 4 增量 1 [S103] 取代:四個成員;Phase 6 增量 3 加待核可
    assert {d.value for d in Disposition} == {"in_progress", "handed_off", "blocked",
                                              "dead_letter", "awaiting_approval"}
    prop = proposal()
    accept(store, prop)
    with store.transaction() as tx:
        receipt = _receive(store, tx)
    before = rows(tmp_path)
    for bad in (None, "version_changed", "expired", 3):
        with store.transaction() as tx, pytest.raises(ValueError):
            store.ack_blocked(tx, receipt, Clock()(), bad)
    assert rows(tmp_path) == before
    conn = sqlite3.connect(tmp_path / "executor.db")  # 資料庫自己也擋:繞過模組寫也寫不進去
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE proposals SET disposition = 'blocked', block_code = 'whatever'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE proposals SET disposition = 'taken'")
    finally:
        conn.close()

