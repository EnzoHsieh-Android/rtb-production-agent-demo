"""收件表的佇列語意與確認規則(Phase 4 增量 1):S95~S107、S109~S117(S108 在 test_execution)。

處置四態:處理中(取件當下寫,租約還在)、已交給執行、已擋下、死信(後三者是確認)。
租約照 Phase 0:到期時間加序號加擁有者;延長租約、確認與嘗試寫入一律帶收據做條件寫入。
"""

import sqlite3
from datetime import timedelta

import pytest

from rtb.domain.attempt import AttemptState, operation_key
from rtb.executor import attempt_store, inbox_store
from rtb.executor.execution import CampaignView, Executor, Result, WriteAnswer
from rtb.executor.inbox_store import (
    MAX_DELIVERIES,
    RETENTION,
    VISIBILITY_TIMEOUT,
    BlockCode,
    DeadLetterReason,
    Disposition,
    InboxFull,
    InboxStore,
    LastFailure,
    Receipt,
)
from tests.executor.fakes import Harness, proposal

A = AttemptState
LEASE = VISIBILITY_TIMEOUT.total_seconds()


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def receive(h, owner="w1"):
    with h.store.transaction() as tx:
        return h.store.receive(tx, h.clock(), owner)


def row(h, task_id="t1", revision=1):
    return h.query(
        "SELECT state, disposition, block_code, dead_letter_reason, last_failure, lease_seq, "
        "lease_owner, lease_until, deliveries FROM proposals WHERE task_id = ? AND revision = ?",
        (task_id, revision))[0]


def states(h, prop):
    return [(r[2], r[3]) for r in h.attempts() if r[0] == operation_key(prop)]


def unknown_attempt(h, **overrides):
    """開一筆嘗試、寫入沒拿到回應,停在結果不明;訊息停在處理中、租約已延長。"""
    prop = h.submit(**overrides)
    h.dsp.answers.append(WriteAnswer(None))
    assert h.process().kind is Result.EXECUTED
    assert states(h, prop)[-1] == ("unknown", None)  # 前置
    return prop


def escalated_attempt(h):
    prop = h.submit()
    h.dsp.answers.append(WriteAnswer(403, "capability_scope_mismatch"))
    h.process()
    assert states(h, prop)[-1] == ("escalated", "capability_rejected")  # 前置
    return prop


# ---- [S95] ----
PHASE3_SCHEMA = """
CREATE TABLE proposals (
    task_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'superseded', 'expired')),
    payload TEXT NOT NULL, expires_at TEXT NOT NULL, received_at TEXT NOT NULL,
    disposition TEXT CHECK (disposition IN ('handed_off', 'blocked')),
    block_code TEXT CHECK (block_code IN ('version_changed')),
    PRIMARY KEY (task_id, revision));
INSERT INTO proposals VALUES ('t-old', 1, 'h', 'pending', '{}', '2026-09-22T12:30:00.000000Z',
    '2026-09-22T12:00:00.000000Z', 'handed_off', NULL);
INSERT INTO proposals VALUES ('t-old', 2, 'h2', 'pending', '{}', '2026-09-22T12:30:00.000000Z',
    '2026-09-22T12:01:00.000000Z', NULL, NULL);
"""


def _phase3_database(db):
    old = sqlite3.connect(db)
    old.executescript(PHASE3_SCHEMA)
    old.execute("PRAGMA journal_mode=WAL")
    old.executescript(attempt_store.SCHEMA)
    old.close()


def _proposal_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT task_id, revision, disposition, lease_seq, deliveries "
                            "FROM proposals ORDER BY revision").fetchall()
    finally:
        conn.close()


def test_an_old_inbox_database_migrates_to_the_current_dispositions_without_losing_data(tmp_path):
    db = tmp_path / "executor.db"
    _phase3_database(db)
    InboxStore(db).close()
    InboxStore(db).close()  # 已遷移的再開一次不會重做
    assert _proposal_rows(db) == [("t-old", 1, "handed_off", 0, 0), ("t-old", 2, None, 0, 0)]
    conn = sqlite3.connect(db)
    try:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(proposals)")]
        for name in ("lease_until", "lease_seq", "lease_owner", "deliveries"):
            assert columns.count(name) == 1
        # 資料庫層限制已換成現行的處置成員:新成員寫得進去,不認得的照樣擋
        conn.execute("UPDATE proposals SET disposition = 'dead_letter' WHERE revision = 2")
        conn.execute("UPDATE proposals SET disposition = 'in_progress' WHERE revision = 2")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE proposals SET disposition = 'taken'")
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'proposals_rebuilt'"
                            ).fetchall() == []
    finally:
        conn.close()


def test_only_an_outdated_constraint_also_triggers_a_rebuild(tmp_path):
    """欄位都補齊了、只剩處置的資料庫層限制還是舊的:光看缺欄位不夠,也要重建。"""
    db = tmp_path / "columns-only.db"
    _phase3_database(db)
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        for ddl in ("dead_letter_reason TEXT", "last_failure TEXT", "lease_until TEXT",
                    "lease_seq INTEGER NOT NULL DEFAULT 0", "lease_owner TEXT",
                    "deliveries INTEGER NOT NULL DEFAULT 0"):
            conn.execute(f"ALTER TABLE proposals ADD COLUMN {ddl}")
    finally:
        conn.close()
    InboxStore(db).close()
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE proposals SET disposition = 'dead_letter' WHERE revision = 2")
    finally:
        conn.close()


def test_two_connections_racing_to_migrate_rebuild_only_once(tmp_path, monkeypatch):
    """並行:它看到要遷移、去等寫入鎖的期間,另一條連線先遷移好了;拿到鎖後要再查一次,不能重做。"""
    raced = tmp_path / "raced.db"
    _phase3_database(raced)
    real = inbox_store.immediate_transaction
    rebuilds, calls = [], []
    real_rebuild = InboxStore._rebuild_proposals

    def counting_rebuild(self):
        rebuilds.append(1)
        return real_rebuild(self)

    def someone_else_migrates_first(conn):
        if not calls:
            calls.append(1)
            InboxStore(raced).close()  # 另一條連線先搶到鎖、完成遷移
        return real(conn)

    monkeypatch.setattr(InboxStore, "_rebuild_proposals", counting_rebuild)
    monkeypatch.setattr(inbox_store, "immediate_transaction", someone_else_migrates_first)
    InboxStore(raced).close()
    assert calls and rebuilds == [1]  # 真的走到「要遷移、去拿鎖」,但表只重建一次
    assert _proposal_rows(raced) == [("t-old", 1, "handed_off", 0, 0), ("t-old", 2, None, 0, 0)]


# ---- [S96] ----
def test_receiving_marks_in_progress_and_returns_a_receipt(h):
    assert receive(h) is None  # 沒有可取件的
    h.submit()

    delivery = receive(h)

    assert delivery is not None and delivery.message.task_id == "t1"
    assert delivery.receipt == Receipt("t1", 1, delivery.message.content_hash, "w1", 1)
    state, disposition, *_, seq, owner, until, deliveries = row(h)
    assert (state, disposition, seq, owner, deliveries) == ("pending", "in_progress", 1, "w1", 1)
    assert until == inbox_store._iso(h.clock() + VISIBILITY_TIMEOUT)


# ---- [S97] ----
def test_only_an_expired_lease_makes_the_message_available_again(h):
    h.submit()
    first = receive(h)
    h.clock.advance(seconds=LEASE - 1)
    assert receive(h) is None  # 租約還在:不重複交出去

    h.clock.advance(seconds=1)
    again = receive(h, owner="w2")

    assert again is not None and again.receipt.lease_seq == first.receipt.lease_seq + 1
    assert row(h)[-1] == 2  # 投遞次數加 1


# ---- [S98] ----
def test_an_in_progress_proposal_is_immune_to_the_pending_housekeeping(tmp_path, clock):
    h = Harness(tmp_path, clock, max_pending=2)
    try:
        h.submit()
        receive(h)
        # 不會被新修訂取代(修訂 2 照常收下,成為新的待處理)
        h.store.accept(proposal(revision=2), clock)
        assert row(h)[:2] == ("pending", "in_progress")
        # 不佔待處理名額(上限 2):修訂 2 加 t2 才滿,處理中的修訂 1 不算
        h.submit(task_id="t2", campaign_id="c2")
        with pytest.raises(InboxFull):
            h.submit(task_id="t3", campaign_id="c3")
        # 不會被收件時的到期標記改成已過期(沒處置的 t2 會)
        clock.advance(minutes=40)
        h.submit(task_id="t5", decision_created_at="2026-09-22T12:40:00+00:00",
                 decision_expires_at="2026-09-22T13:10:00+00:00")
        assert row(h)[:2] == ("pending", "in_progress")
        assert row(h, "t2")[0] == "expired"
        # 任務還有處理中的一列:過了保留期也不清(t2 那種全都結束的才清)
        clock.advance(seconds=RETENTION.total_seconds())
        h.submit(task_id="t6", decision_created_at="2026-09-22T14:50:00+00:00",
                 decision_expires_at="2026-09-22T15:20:00+00:00")
        assert h.query("SELECT COUNT(*) FROM proposals WHERE task_id = 't1'") == [(2,)]
        assert h.query("SELECT COUNT(*) FROM proposals WHERE task_id = 't2'") == [(0,)]
    finally:
        h.close()


# ---- [S99] ----
def test_receiving_skips_locked_campaigns_without_spending_a_delivery(h):
    unknown_attempt(h)  # 廣告 c1 有未結案嘗試
    h.clock.advance(seconds=1)
    h.submit(task_id="t9", requested_change={"new_budget": 170})  # 同廣告,比較早收到
    h.clock.advance(seconds=1)
    later = h.submit(task_id="t2", campaign_id="c2")

    delivery = receive(h)

    assert delivery.message.task_id == later.task_id  # 跳過鎖住的廣告
    assert row(h, "t9")[1] is None and row(h, "t9")[-1] == 0  # 沒交出去,不算投遞


# ---- [S100] ----
def test_an_acknowledged_proposal_is_never_handed_out_again(h):
    for task_id, campaign, ack in (
        ("t1", "c1", lambda tx, r: h.store.ack_handed_off(tx, r, h.clock())),
        ("t2", "c2", lambda tx, r: h.store.ack_blocked(tx, r, h.clock(),
                                                       BlockCode.VERSION_CHANGED)),
        ("t3", "c3", lambda tx, r: h.store.ack_expired(tx, r, h.clock())),
    ):
        h.submit(task_id=task_id, campaign_id=campaign)
        with h.store.transaction() as tx:
            delivery = h.store.receive(tx, h.clock(), "w1")
            assert ack(tx, delivery.receipt)
    h.submit(task_id="t4", campaign_id="c4")  # 死信:投遞次數用完、每次都沒能開始嘗試
    for _ in range(MAX_DELIVERIES):
        with h.store.transaction() as tx:
            delivery = h.store.receive(tx, h.clock(), "w1")
            assert h.store.release(tx, delivery.receipt, h.clock(), LastFailure.DSP_UNAVAILABLE)
    assert receive(h) is None  # 這次改標死信
    h.clock.advance(seconds=LEASE + 1)  # 租約早就過了

    assert receive(h) is None  # 死信要人重放(Phase 8)才回到待處理,自己不會再交出去
    assert [row(h, t)[:2] for t in ("t1", "t2", "t3", "t4")] == [
        ("pending", "handed_off"), ("pending", "blocked"), ("expired", None),
        ("pending", "dead_letter")]


# ---- [S101] ----
def test_a_stale_or_expired_receipt_can_write_nothing(h):
    h.submit()
    old = receive(h).receipt
    now = h.clock()

    def writes(receipt, at):
        with h.store.transaction() as tx:
            return (h.store.extend(tx, receipt, at), h.store.release(tx, receipt, at, None),
                    h.store.ack_handed_off(tx, receipt, at))

    # 擁有者不對
    assert writes(Receipt(old.task_id, old.revision, old.content_hash, "intruder",
                          old.lease_seq), now) == (False, False, False)
    # 租約已過期、還沒人重新取件:舊工作者照樣寫不進去,也不能續命
    before = row(h)
    assert writes(old, now + VISIBILITY_TIMEOUT + timedelta(seconds=1)) == (False, False, False)
    assert row(h) == before
    # 同一個擁有者重新取件之後(序號加 1):舊收據寫不進去——擋它的是序號,不是擁有者
    h.clock.advance(seconds=LEASE)
    renewed = receive(h, owner="w1").receipt
    assert renewed.lease_seq == old.lease_seq + 1
    assert writes(old, h.clock()) == (False, False, False)
    # 換人取件之後:舊收據與前一張收據都寫不進去,新收據可以
    h.clock.advance(seconds=LEASE)
    new = receive(h, owner="w2").receipt
    assert writes(renewed, h.clock()) == (False, False, False)
    with h.store.transaction() as tx:
        assert h.store.ack_handed_off(tx, new, h.clock())


# ---- [S102] ----
def test_the_delivery_limit_dead_letters_a_message_that_never_started(h):
    h.submit()
    for _ in range(MAX_DELIVERIES):  # 每次都沒能開始嘗試:放掉租約、記下原因
        with h.store.transaction() as tx:
            delivery = h.store.receive(tx, h.clock(), "w1")
            assert h.store.release(tx, delivery.receipt, h.clock(), LastFailure.DSP_UNAVAILABLE)
    assert row(h)[-1] == MAX_DELIVERIES  # 前置:已經交出去 5 次

    assert receive(h) is None  # 第 6 次不交出去,改標死信

    assert row(h)[1:5] == ("dead_letter", None, "delivery_limit", "dsp_unavailable")
    assert row(h)[-1] == MAX_DELIVERIES  # 死信那次不算投遞


# ---- [S103] ----
def _in_progress(h):
    h.submit()
    receive(h)


def _handed_off(h):
    h.submit()
    h.process()


def _blocked(h):
    h.submit()
    h.dsp.campaigns["c1"] = CampaignView(120, "active", 4)
    h.process()


def _awaiting_approval(h):  # Phase 6 增量 3:比例過大又沒有核可
    h.submit(requested_change={"new_budget": 151})
    h.process()


def _dead_letter(h):
    h.submit()
    h.dsp.read_failures = MAX_DELIVERIES
    for _ in range(MAX_DELIVERIES + 1):
        h.process()


def _table_full(h):
    import unittest.mock

    with unittest.mock.patch.object(attempt_store, "MAX_UNRESOLVED", 1):
        unknown_attempt(h, task_id="t0", campaign_id="c2")
        h.submit()
        h.process()


def _no_report(h):
    h.submit()
    receive(h)
    h.clock.advance(seconds=LEASE)
    receive(h)  # 上一個持有者沒回報就過期:回收時記「沒有回報」


DISPOSITION_TRIGGERS = {
    Disposition.IN_PROGRESS: _in_progress, Disposition.HANDED_OFF: _handed_off,
    Disposition.BLOCKED: _blocked, Disposition.DEAD_LETTER: _dead_letter,
    Disposition.AWAITING_APPROVAL: _awaiting_approval,
}
DEAD_LETTER_TRIGGERS = {DeadLetterReason.DELIVERY_LIMIT: _dead_letter}
FAILURE_TRIGGERS = {
    LastFailure.DSP_UNAVAILABLE: _dead_letter, LastFailure.TABLE_FULL: _table_full,
    LastFailure.NO_REPORT: _no_report,
}
TRIGGER_CASES = ([("disposition", m, f) for m, f in DISPOSITION_TRIGGERS.items()]
                 + [("dead_letter_reason", m, f) for m, f in DEAD_LETTER_TRIGGERS.items()]
                 + [("last_failure", m, f) for m, f in FAILURE_TRIGGERS.items()])


@pytest.mark.parametrize(("column", "member", "trigger"), TRIGGER_CASES,
                         ids=[f"{c}-{m.value}" for c, m, _ in TRIGGER_CASES])
def test_every_disposition_and_delivery_reason_has_a_real_trigger(h, column, member, trigger):
    # 從程式自己的列舉逐一列舉:每個成員都要有觸發條件,多一個成員沒補觸發就紅
    assert set(DISPOSITION_TRIGGERS) == set(Disposition)
    assert set(DEAD_LETTER_TRIGGERS) == set(DeadLetterReason)
    assert set(FAILURE_TRIGGERS) == set(LastFailure)
    assert not set(DeadLetterReason) & set(BlockCode)  # 死信原因不混進擋下原因

    trigger(h)

    assert (member.value,) in h.query(f"SELECT {column} FROM proposals WHERE task_id = 't1'")  # noqa: S608 - 欄位名來自上面的固定清單


# ---- [S104] ----
class Crash(Exception):
    """模擬行程在開始一筆之後、呼叫 DSP 之前死掉。"""


def test_a_crash_before_the_dsp_call_is_left_to_reconciliation(h):
    prop = h.submit()

    def crash(*_args):
        raise Crash

    h.dsp.on_write = crash
    with pytest.raises(Crash):
        h.process()
    h.dsp.on_write = None
    assert states(h, prop) == [("in_flight", None)]  # 前置:已開始一筆,DSP 沒套用
    assert h.dsp.operations == {}

    h.clock.advance(seconds=LEASE + 1)  # 租約到期
    assert receive(h, owner="w2") is None  # 取件讀到既有嘗試(廣告鎖住):不再交出去
    assert row(h)[-1] == 1  # 也不算投遞

    Executor(h.store, h.dsp, h.signer, h.config, h.clock, "w2").reconcile_all()

    assert states(h, prop)[-1] == ("verified", None)
    assert row(h)[1] == "handed_off"


# ---- [S105] ----
def test_a_verified_attempt_acknowledges_without_touching_the_dsp(h):
    first = h.submit()
    h.process()
    assert states(h, first)[-1] == ("verified", None)  # 前置
    h.store.accept(proposal(revision=2, decision_expires_at="2026-09-22T12:40:00+00:00"),
                   h.clock)
    reads = len(h.dsp.reads)

    assert receive(h) is None  # 取件時就確認,不交出去

    assert row(h, "t1", 2)[:2] == ("pending", "handed_off")
    assert row(h, "t1", 2)[-1] == 0 and len(h.dsp.reads) == reads


# ---- [S106] ----
def test_a_failed_attempt_blocks_the_new_delivery(h):
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(409, "version_conflict"))
    h.process()
    assert states(h, first)[-1] == ("failed", "version_conflict")  # 前置
    h.store.accept(proposal(revision=2, decision_expires_at="2026-09-22T12:40:00+00:00"),
                   h.clock)

    assert receive(h) is None

    # Phase 5 [S310]:前一筆因 DSP 回版本衝突而失敗,擋下原因寫「版本已變」(原本是同一操作先前已失敗)
    assert row(h, "t1", 2)[:3] == ("pending", "blocked", "version_changed")


# ---- [S107] ----
def test_an_unresolved_attempt_is_never_redelivered_or_dead_lettered(h):
    started = h.clock()
    h.dsp.on_write = lambda *_a: h.clock.advance(seconds=20)  # 開始一筆之後,寫 DSP 花了時間
    prop = unknown_attempt(h)
    h.dsp.on_write = None
    until = row(h)[7]
    # 處理一筆結束時停在未結案:租約已經隨每一筆嘗試寫入延長,不是取件當下的那個到期時間
    assert until == inbox_store._iso(h.clock() + VISIBILITY_TIMEOUT)
    assert until != inbox_store._iso(started + VISIBILITY_TIMEOUT)
    assert row(h)[1] == "in_progress"  # 未結案不確認

    for _ in range(MAX_DELIVERIES + 2):  # 就算沒人續租、租約一再過期
        h.clock.advance(seconds=LEASE + 1)
        assert receive(h, owner="w2") is None

    assert row(h)[1] == "in_progress" and row(h)[-1] == 1  # 沒重新投遞、沒死信
    assert states(h, prop)[-1] == ("unknown", None)


# ---- [S109] ----
def test_reconciliation_renews_escalated_and_acknowledges_finished_messages(h):
    escalated = escalated_attempt(h)
    finished = unknown_attempt(h, task_id="t2", campaign_id="c2")
    h.dsp.apply(finished, operation_key(finished))  # 其實 DSP 已提交
    h.clock.advance(seconds=LEASE - 5)
    writes = len(h.dsp.writes)

    h.executor().reconcile_all()

    assert row(h)[1] == "in_progress"  # 轉人工只續租
    assert row(h)[7] == inbox_store._iso(h.clock() + VISIBILITY_TIMEOUT)
    assert states(h, escalated)[-1] == ("escalated", "capability_rejected")
    assert states(h, finished)[-1] == ("verified", None)  # 推到終點
    assert row(h, "t2")[1] == "handed_off"  # 同一個交易裡確認
    assert len(h.dsp.writes) == writes  # 轉人工不碰 DSP


# ---- [S110] ----
def test_reconciliation_only_takes_over_an_expired_or_own_lease(h):
    prop = unknown_attempt(h)  # 擁有者是 executor,租約剛延長
    other = Executor(h.store, h.dsp, h.signer, h.config, h.clock, "w2")
    lookups = len(h.dsp.lookups)

    other.reconcile_all()  # 別人持有而且沒到期:跳過

    assert len(h.dsp.lookups) == lookups and row(h)[6] == "executor"
    assert states(h, prop)[-1] == ("unknown", None)

    h.clock.advance(seconds=LEASE)
    other.reconcile_all()  # 過期了:原子接手

    assert row(h)[5] == 2  # 接手:序號加 1
    assert len(h.dsp.lookups) == lookups + 1
    assert row(h)[1] == "handed_off"  # 接手之後推到終點並確認


# ---- [S111] ----
def test_expiry_and_blocking_after_receiving_use_the_receipt(h):
    h.submit()
    h.submit(task_id="t2", campaign_id="c2")
    with h.store.transaction() as tx:
        first = h.store.receive(tx, h.clock(), "w1").receipt
        second = h.store.receive(tx, h.clock(), "w1").receipt
    assert row(h)[1] == row(h, "t2")[1] == "in_progress"  # 前置:已不是「仍待處理」
    stale = Receipt(first.task_id, first.revision, first.content_hash, "w1", 99)

    with h.store.transaction() as tx:
        assert not h.store.ack_expired(tx, stale, h.clock())
        assert h.store.ack_expired(tx, first, h.clock())
        assert h.store.ack_blocked(tx, second, h.clock(), BlockCode.VERSION_CHANGED)

    assert row(h)[:2] == ("expired", None)
    assert row(h, "t2")[:3] == ("pending", "blocked", "version_changed")


# ---- [S112] ----
@pytest.mark.parametrize(("outcome", "expected"), [
    (A.VERIFIED, ("handed_off", None)), (A.FAILED, ("blocked", "operation_previously_failed"))])
def test_a_manual_resolution_is_acknowledged_by_the_next_reconciliation(h, outcome, expected):
    prop = escalated_attempt(h)
    key = operation_key(prop)
    with h.store.transaction() as tx:  # 人工處置只寫嘗試紀錄,不碰收件表
        seq = attempt_store.latest(tx, key).seq
        attempt_store.resolve(tx, key, seq, outcome, "operator decided", h.clock())
    assert row(h)[1] == "in_progress"  # 前置

    h.executor().reconcile_all()

    assert row(h)[1:3] == expected


# ---- [S113] ----
def test_a_replay_reports_in_progress_and_dead_letter(h):
    prop = h.submit()
    first = receive(h).receipt
    before = h.proposals()

    again = h.store.accept(prop, h.clock)

    assert (again.state, again.replayed) == ("in_progress", True)
    assert h.proposals() == before
    with h.store.transaction() as tx:  # 確認掉,免得它的租約過期後搶在前面被取件
        h.store.ack_handed_off(tx, first, h.clock())

    dead = h.submit(task_id="t2", campaign_id="c2")
    for _ in range(MAX_DELIVERIES):
        with h.store.transaction() as tx:
            delivery = h.store.receive(tx, h.clock(), "w1")
            h.store.release(tx, delivery.receipt, h.clock(), LastFailure.DSP_UNAVAILABLE)
        h.clock.advance(seconds=1)
    h.clock.advance(seconds=LEASE)
    receive(h)
    assert row(h, "t2")[1] == "dead_letter"  # 前置
    assert h.store.accept(dead, h.clock).state == "dead_letter"


# ---- [S114] ----
def test_only_a_message_without_an_attempt_is_handed_out_for_processing(h):
    verified = h.submit()
    h.process()
    h.store.accept(proposal(revision=2, decision_expires_at="2026-09-22T12:40:00+00:00"),
                   h.clock)  # 同一把鍵、已有終點結果
    fresh = h.submit(task_id="t2", campaign_id="c2")  # 沒有嘗試紀錄
    assert states(h, verified)[-1] == ("verified", None)  # 前置

    delivery = receive(h)

    assert delivery.message.task_id == fresh.task_id  # 只有沒有嘗試紀錄的被交出去
    assert row(h, "t1", 2)[1] == "handed_off" and row(h, "t1", 2)[-1] == 0


# ---- [S115] ----
def test_a_message_that_cannot_start_is_retried_until_dead_lettered(h):
    h.submit()
    h.dsp.read_failures = MAX_DELIVERIES

    kinds = [h.process().kind for _ in range(MAX_DELIVERIES)]

    assert kinds == [Result.DEFERRED] * MAX_DELIVERIES  # 每次放掉租約,下一輪能再取
    assert row(h)[4] == "dsp_unavailable" and row(h)[-1] == MAX_DELIVERIES
    assert h.process().kind is Result.IDLE  # 用完投遞次數:死信
    assert row(h)[1:4] == ("dead_letter", None, "delivery_limit")
    assert h.attempts() == [] and h.dsp.writes == []


# ---- [S116] ----
def test_a_lost_lease_skips_the_key_without_halting(h):
    prop = h.submit()

    def someone_takes_over(*_args):  # DSP 寫入期間,租約過期被別人接手
        h.clock.advance(seconds=LEASE + 1)
        with h.store.transaction() as tx:
            message = h.store.in_progress_for(tx, "t1", operation_key(prop))
            h.store.take_over(tx, message, h.clock(), "w2")

    h.dsp.on_write = someone_takes_over

    assert h.process().kind is Result.LEASE_LOST  # 放棄,不停機
    assert states(h, prop) == [("in_flight", None)]  # 過期工作者沒寫進結果

    # 對帳:第一把鍵的租約在對帳途中被接手,放棄它,照樣處理第二把
    h.dsp.on_write = None
    second = unknown_attempt(h, task_id="t2", campaign_id="c2")
    h.dsp.apply(second, operation_key(second))
    with h.store.transaction() as tx:  # 讓第一把鍵的租約回到 executor 手上,且還沒到期
        message = h.store.in_progress_for(tx, "t1", operation_key(prop))
        h.clock.advance(seconds=LEASE + 1)
        h.store.take_over(tx, message, h.clock(), "executor")
    lookup = h.dsp.operation_record

    def steal_first(key, *, on_call):
        if key == operation_key(prop):
            with h.store.transaction() as tx:
                message = h.store.in_progress_for(tx, "t1", key)
                h.clock.advance(seconds=LEASE + 1)
                h.store.take_over(tx, message, h.clock(), "w3")
        return lookup(key, on_call=on_call)

    h.dsp.operation_record = steal_first

    h.executor().reconcile_all()

    assert states(h, second)[-1] == ("verified", None)  # 第二把照樣推到終點


# ---- [S117] ----
def test_reconciliation_recovers_an_in_flight_attempt_it_took_over(h):
    prop = h.submit()

    def crash(*_args):
        raise Crash

    h.dsp.on_write = crash
    with pytest.raises(Crash):
        h.process()
    h.dsp.on_write = None
    h.clock.advance(seconds=LEASE)

    Executor(h.store, h.dsp, h.signer, h.config, h.clock, "w2").reconcile_all()

    assert states(h, prop)[:2] == [("in_flight", None), ("unknown", None)]  # 先轉結果不明
    assert states(h, prop)[-1] == ("verified", None)
    assert row(h)[6] is None and row(h)[1] == "handed_off"


def test_the_lease_is_longer_than_the_slowest_single_dsp_call():
    """可見性逾時要比一次 DSP 呼叫的用戶端逾時(預設 5 秒)長得多,活著的工作者才不會被當成當機。"""
    from rtb.executor import runner

    default_timeout = runner._parse(["--db", "x", "--dsp-url", "u", "--tenant-config", "c"]
                                    ).dsp_timeout_seconds
    assert timedelta(seconds=default_timeout * 6) < VISIBILITY_TIMEOUT


# ---- 代碼審第 1 輪補的防線 ----
def test_an_unreadable_in_progress_message_halts_instead_of_bypassing_the_lease(h):
    """處理中那一列讀不回來時,對帳不能當成「沒有處理中訊息」走不帶收據的路:停下讓人看。"""
    from rtb.executor.execution import ExecutorHalted

    prop = unknown_attempt(h)
    h.clock.advance(seconds=LEASE + 1)
    h.query("UPDATE proposals SET payload = '{' WHERE task_id = 't1'")
    before = states(h, prop)

    with pytest.raises(ExecutorHalted, match="unreadable_message"):
        h.executor().reconcile_all()
    assert states(h, prop) == before  # 嘗試紀錄一筆都沒寫


def test_a_failed_acknowledgement_gives_up_the_key_instead_of_claiming_success(h, monkeypatch):
    """確認也是帶收據的條件寫入:0 列代表收據失效。開始一筆撞到既有已驗證的鍵時,確認寫不進去
    就要回「失去租約」,不能回報已交給執行。"""
    first = h.submit()
    h.process()
    with h.store.transaction() as tx:
        verified = attempt_store.latest(tx, operation_key(first))
    assert verified.state is A.VERIFIED  # 前置
    h.submit(task_id="t2", campaign_id="c2")
    monkeypatch.setattr(attempt_store, "begin",
                        lambda *_a, **_k: attempt_store.Begun(verified, created=False))
    monkeypatch.setattr(h.store, "ack_handed_off", lambda *_a, **_k: False)

    assert h.process().kind is Result.LEASE_LOST
    assert row(h, task_id="t2")[1] == "in_progress"  # 沒被當成已確認


def test_two_receipts_for_the_same_key_still_write_the_dsp_once(h):
    """取件只排除「已有嘗試」的廣告:同一把鍵的兩份修訂可能各拿一張收據(開始一筆之前)。
    第二張收據在第一份寫 DSP 的途中插進來(版本還沒變、檢查會過),開始一筆時被擋下、不再呼叫
    DSP。擋住它的有兩道:同一把鍵已有嘗試(鍵唯一),以及同廣告已有未結案嘗試(廣告鎖);
    這支測試守的是「兩道都在時不會重複寫」,鍵唯一本身由嘗試紀錄的測試直接驗。"""
    h.submit()
    a = receive(h, owner="executor")  # 修訂 1 已取件、還沒開始嘗試(處理中,不會被取代)
    h.submit(revision=2, decision_expires_at="2026-09-22T12:40:00+00:00")  # 只換到期時間:同一把鍵
    b = receive(h, owner="w2")
    assert a is not None and b is not None  # 前置:兩張收據同時有效
    assert operation_key(a.message.proposal) == operation_key(b.message.proposal)
    second = Executor(h.store, h.dsp, h.signer, h.config, h.clock, "w2")
    interleaved = []

    def second_runs_during_the_first_write(*_args):
        h.dsp.on_write = None
        interleaved.append(second._process(b.message, b.receipt))

    h.dsp.on_write = second_runs_during_the_first_write
    first_run = h.executor()._process(a.message, a.receipt)

    assert first_run.kind is Result.EXECUTED
    assert interleaved and interleaved[0].kind is not Result.EXECUTED  # 第二張收據沒開新嘗試
    assert len(h.dsp.writes) == 1


def test_another_revisions_unreadable_row_does_not_halt_a_healthy_key(h):
    """同一任務另一份修訂的處理中列壞掉,但這把鍵自己那一列好好的:走正式的對帳入口,
    健康的鍵照常接手推進,全部處理完才因為有壞列停下讓人看(壞列不能默默跳過)。"""
    from rtb.executor.execution import ExecutorHalted

    first = h.submit()
    receive(h, owner="w9")  # 修訂 1 處理中(之後把它弄壞)
    h.submit(revision=2, campaign_id="c2", decision_expires_at="2026-09-22T12:40:00+00:00")
    second = unknown_attempt_for_revision(h, revision=2)
    assert operation_key(first) != operation_key(second)  # 前置:兩份修訂是不同的鍵
    h.dsp.apply(second, operation_key(second))  # DSP 其實已提交:對帳查得到
    h.query("UPDATE proposals SET payload = '{' WHERE task_id = 't1' AND revision = 1")

    with pytest.raises(ExecutorHalted, match="unreadable_message"):
        h.executor().reconcile_all()

    assert states(h, second)[-1] == ("verified", None)  # 健康的鍵在停機前已推到終點
    assert row(h, revision=2)[1] == "handed_off"
    # 找不到相符的、又有讀不回來的列:不能回「沒有」
    with h.store.transaction() as tx, pytest.raises(inbox_store.CorruptedInboxRow):
        h.store.in_progress_for(tx, "t1", operation_key(first))


def unknown_attempt_for_revision(h, revision):
    """取出指定修訂、寫入沒拿到回應,停在結果不明。"""
    h.dsp.answers.append(WriteAnswer(None))
    assert h.process().kind is Result.EXECUTED
    rows = h.query("SELECT payload FROM proposals WHERE task_id = 't1' AND revision = ?",
                   (revision,))
    return inbox_store._parse_payload(rows[0][0])


def test_an_unreadable_row_among_finished_messages_halts_the_reconciliation(h):
    """已結案、確認前當機、內容又壞掉的處理中列:對帳的掃描不能默默跳過它(那樣它會永遠卡住)。"""
    from rtb.executor.execution import ExecutorHalted

    prop = h.submit()
    h.dsp.answers.append(WriteAnswer(409, "version_conflict"))
    h.process()
    assert states(h, prop)[-1][0] == "failed"  # 前置:已到終點
    h.query("UPDATE proposals SET disposition = 'in_progress', payload = '{' WHERE task_id = 't1'")

    with pytest.raises(ExecutorHalted, match="unreadable_message"):
        h.executor().reconcile_all()


def test_a_finished_write_whose_acknowledgement_fails_gives_up_the_key(h, monkeypatch):
    """處理一筆寫到終點時,確認也是帶收據的條件寫入:寫不進去要回「失去租約」,不能回報已執行完。"""
    h.submit()
    monkeypatch.setattr(h.store, "ack_handed_off", lambda *_a, **_k: False)

    assert h.process().kind is Result.LEASE_LOST
    assert row(h)[1] == "in_progress"

