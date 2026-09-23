"""死信信封、失敗分類、重放與死信操作稽核(Phase 8):S500、S501、S502、S508、S510。

進死信在同一個交易裡寫一列死信信封(只增不改、不被清理);重放是管理指令把還活著的死信放回
待處理,每一個死信操作(進死信、要求重放、重放被拒、重放已放回佇列)都寫一列稽核。
"""

import io
import sqlite3
import threading

import pytest

from rtb.domain.attempt import operation_key
from rtb.domain.proposal import content_hash
from rtb.executor import inbox_store
from rtb.executor import replay as replay_tool
from rtb.executor.execution import CampaignView, Result
from rtb.executor.inbox_store import (
    MAX_DELIVERIES,
    RETENTION,
    BlockCode,
    DeadLetterAction,
    FailureClass,
    InboxRejected,
    InboxStore,
    LastFailure,
    ReplayOutcome,
    failure_class,
)
from tests.executor.fakes import Harness, proposal, write_config

OPERATOR = "ops-alice"


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def dead_letter(h, **overrides):
    """讀不到 DSP 直到投遞次數用完:第 MAX_DELIVERIES + 1 次取件時進死信。"""
    prop = h.submit(**overrides)
    h.dsp.read_failures = MAX_DELIVERIES
    for _ in range(MAX_DELIVERIES):
        assert h.process().kind is Result.DEFERRED
    assert h.process().kind is Result.IDLE  # 這一次取件把它寫成死信
    return prop


def envelopes(h):
    return h.query("SELECT id, task_id, revision, content_hash, key, failure_class, reason, "
                   "last_failure, deliveries FROM dead_letters ORDER BY id")


def audit(h):
    return h.query("SELECT envelope, task_id, revision, action, operator, reason "
                   "FROM dead_letter_ops ORDER BY id")


def replay(h, task_id="t1", revision=1, operator=OPERATOR):
    return h.store.replay(task_id, revision, operator, h.clock)


def disposition(h, task_id="t1"):
    return next(row[2:4] for row in h.proposals() if row[0] == task_id)


# ---- [S500] ----
def test_a_dead_letter_leaves_a_durable_envelope(h):
    prop = dead_letter(h)

    assert disposition(h) == ("pending", "dead_letter")
    assert envelopes(h) == [(1, "t1", 1, content_hash(prop), operation_key(prop), "transient",
                             "delivery_limit", "dsp_unavailable", MAX_DELIVERIES)]

    h.clock.advance(seconds=RETENTION.total_seconds() + 1)  # 過保留期,下一次收件順手清掉
    with pytest.raises(InboxRejected):  # 樣本提案早已過期:拒收,但清理照樣提交
        h.submit(task_id="t9")
    assert h.proposals() == []
    assert len(envelopes(h)) == 1  # 收件表清掉了,信封還在


def test_a_dead_letter_writes_its_envelope_in_the_same_transaction(h, monkeypatch):
    h.submit()
    h.dsp.read_failures = MAX_DELIVERIES
    for _ in range(MAX_DELIVERIES):
        h.process()

    def fail(*_args, **_kwargs):
        raise RuntimeError("寫信封前當機")

    monkeypatch.setattr(InboxStore, "_record_dead_letter", fail)
    with pytest.raises(RuntimeError):
        h.process()

    assert disposition(h) == ("pending", "in_progress")  # 處置沒變成死信:整個交易回滾
    assert envelopes(h) == [] and audit(h) == []


def test_a_replayed_proposal_that_dead_letters_again_gets_a_second_envelope(h):
    dead_letter(h)
    assert replay(h) is ReplayOutcome.REQUEUED
    h.dsp.read_failures = MAX_DELIVERIES
    for _ in range(MAX_DELIVERIES):
        assert h.process().kind is Result.DEFERRED
    assert h.process().kind is Result.IDLE

    assert [row[0] for row in envelopes(h)] == [1, 2]  # 不去重:兩次死信各一列
    assert [row[:4] for row in audit(h)] == [
        (1, "t1", 1, "dead_lettered"), (1, "t1", 1, "replay_requested"),
        (1, "t1", 1, "replay_requeued"), (2, "t1", 1, "dead_lettered")]


# ---- [S501] ----
def test_every_failure_kind_is_classified():
    for failure in LastFailure:
        assert failure_class(failure) is FailureClass.TRANSIENT, failure
    for code in BlockCode:
        assert failure_class(code) is FailureClass.PERMANENT, code
    assert {c.value for c in FailureClass} == {"transient", "permanent"}


# ---- [S502] ----
def test_a_replay_requeues_only_a_live_dead_letter(h):
    dead_letter(h)

    assert replay(h) is ReplayOutcome.REQUEUED
    assert h.query("SELECT state, disposition, dead_letter_reason, deliveries, lease_owner "
                   "FROM proposals") == [("pending", None, None, 0, None)]
    assert replay(h) is ReplayOutcome.NOT_DEAD_LETTER  # 已放回:不再是死信


def test_a_replay_is_refused_after_the_proposal_expires(h):
    dead_letter(h)
    h.clock.now = proposal().decision_expires_at

    assert replay(h) is ReplayOutcome.EXPIRED
    assert disposition(h) == ("pending", "dead_letter")


def test_a_replay_is_refused_when_the_inbox_no_longer_has_the_proposal(h):
    assert replay(h) is ReplayOutcome.NOT_IN_INBOX
    assert audit(h) == [(None, "t1", 1, "replay_requested", OPERATOR, None),
                        (None, "t1", 1, "replay_refused", OPERATOR, "not_in_inbox")]


def test_a_replay_is_refused_when_the_task_has_a_newer_revision(h):
    dead_letter(h)
    h.store.accept(proposal(revision=2, requested_change={"new_budget": 140}), h.clock)

    assert replay(h) is ReplayOutcome.SUPERSEDED
    assert disposition(h) == ("pending", "dead_letter")


def test_a_replay_is_refused_when_the_inbox_is_full(tmp_path, clock):
    h = Harness(tmp_path, clock, max_pending=1)
    try:
        dead_letter(h)
        h.submit(task_id="t2", campaign_id="c2")  # 待處理名額用完

        assert replay(h) is ReplayOutcome.INBOX_FULL
        assert disposition(h) == ("pending", "dead_letter")
    finally:
        h.close()


def test_two_simultaneous_replays_requeue_once(h):
    dead_letter(h)
    barrier = threading.Barrier(2)
    results = []

    def one_operator(name):
        store = InboxStore(h.db)
        try:
            barrier.wait()
            results.append(store.replay("t1", 1, name, h.clock))
        finally:
            store.close()

    threads = [threading.Thread(target=one_operator, args=(f"ops-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(r.value for r in results) == ["not_dead_letter", "requeued"]


@pytest.mark.parametrize("operator", ["", "has space", "x" * 200])
def test_a_replay_without_a_valid_operator_is_refused_without_an_audit_row(h, operator):
    dead_letter(h)
    before = audit(h)

    assert replay_tool.run(_replay_args(h, operator=operator), clock=h.clock,
                           err=io.StringIO()) == replay_tool.EXIT_REFUSED

    assert audit(h) == before
    assert disposition(h) == ("pending", "dead_letter")


# ---- [S508] ----
def test_every_dead_letter_operation_is_audited(h):
    dead_letter(h)
    replay(h)
    replay(h)  # 被拒:已不是死信

    assert audit(h) == [
        (1, "t1", 1, "dead_lettered", "executor", None),
        (1, "t1", 1, "replay_requested", OPERATOR, None),
        (1, "t1", 1, "replay_requeued", OPERATOR, None),
        (1, "t1", 1, "replay_requested", OPERATOR, None),
        (1, "t1", 1, "replay_refused", OPERATOR, "not_dead_letter")]
    assert {a.value for a in DeadLetterAction} == {
        "dead_lettered", "replay_requested", "replay_refused", "replay_requeued"}


# ---- [S510] ----
def test_an_old_inbox_gains_the_dead_letter_tables(tmp_path, clock):
    db = tmp_path / "executor.db"
    old = InboxStore(db)
    old.accept(proposal(task_id="old"), clock)
    with old.transaction() as tx:
        delivery = old.receive(tx, clock(), "worker")
        assert old.ack_blocked(tx, delivery.receipt, clock(), BlockCode.VERSION_CHANGED)
    old.close()
    conn = sqlite3.connect(db)
    try:  # 退回 Phase 8 之前:沒有兩張新表、允許值清單沒有兩個新擋下原因
        conn.executescript("DROP TABLE dead_letters; DROP TABLE dead_letter_ops;")
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'proposals'").fetchone()[0]
        old_sql = sql.replace(", 'policy_version_changed', 'decision_stale'", "")
        assert old_sql != sql
        conn.executescript(f"ALTER TABLE proposals RENAME TO p_old; {old_sql};"  # noqa: S608 - 測試模擬舊表
                           "INSERT INTO proposals SELECT * FROM p_old; DROP TABLE p_old;")
        before = conn.execute("SELECT * FROM proposals").fetchall()
    finally:
        conn.close()

    store = InboxStore(db)
    try:
        assert store._conn.execute("SELECT * FROM proposals").fetchall() == before
        tables = {name for (name,) in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"dead_letters", "dead_letter_ops"} <= tables
        store.accept(proposal(task_id="new"), clock)
        with store.transaction() as tx:
            delivery = store.receive(tx, clock(), "worker")
            assert store.ack_blocked(tx, delivery.receipt, clock(), BlockCode.DECISION_STALE)
    finally:
        store.close()


def test_the_dead_letter_tables_have_their_lookup_indexes(h):
    plan = " ".join(row[-1] for row in h.query(
        "EXPLAIN QUERY PLAN SELECT max(id) FROM dead_letters WHERE task_id = ? AND revision = ?",
        ("t1", 1)))
    assert "dead_letters_by_proposal" in plan
    plan = " ".join(row[-1] for row in h.query(
        "EXPLAIN QUERY PLAN SELECT * FROM dead_letter_ops WHERE envelope = ?", (1,)))
    assert "dead_letter_ops_by_envelope" in plan


def test_a_replay_takes_no_shortcut_past_the_freshness_check(h):
    """重放不略過任何關卡:放回之後照一般流程取件,決策過時就擋(死信佇列不是第二條通道)。"""
    dead_letter(h)
    replay(h)
    h.clock.advance(minutes=11)  # 12:16:決策已過時、提案還沒到期

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, BlockCode.DECISION_STALE)
    assert h.dsp.writes == []


# ---- [S502] 管理指令 ----
def _replay_args(h, **overrides):
    args = {"db": str(h.db), "task-id": "t1", "revision": "1", "operator": OPERATOR, **overrides}
    return [part for name, value in args.items() for part in (f"--{name}", value)]


def test_the_replay_tool_requeues_a_dead_letter_and_reports_refusals(h):
    dead_letter(h)
    out = io.StringIO()

    assert replay_tool.run(_replay_args(h), clock=h.clock, out=out) == 0
    assert out.getvalue().strip() == "requeued"
    assert disposition(h) == ("pending", None)

    err = io.StringIO()
    assert replay_tool.run(_replay_args(h), clock=h.clock, err=err) == replay_tool.EXIT_REFUSED
    assert "not_dead_letter" in err.getvalue()
    assert replay_tool.run(_replay_args(h, operator="has space"), clock=h.clock,
                           err=io.StringIO()) == replay_tool.EXIT_REFUSED
    assert [row[3] for row in audit(h)] == [
        "dead_lettered", "replay_requested", "replay_requeued", "replay_requested",
        "replay_refused"]  # 操作人不合格式那一次不寫稽核


# ---- [S503] ----
def _other_version(h, _mp):
    h.dsp.campaigns["c1"] = CampaignView(budget=100, status="active", version=4)


def _policy_upgraded(_h, mp):
    mp.setattr("rtb.executor.execution.POLICY_VERSION", "demo-pacing-v2")


def _stale(h, _mp):
    h.clock.advance(minutes=11)  # 12:16:決策已過時、提案還沒到期


def _not_allowed(h, _mp):
    write_config(h.config, campaigns=("c2",))


def _over_ratio(h, _mp):
    h.dsp.campaigns["c1"] = CampaignView(budget=99, status="active", version=3)


def _aggregate_full(h, _mp):
    write_config(h.config, aggregate_limit=49)  # 提案從 100 改成 150,要佔 50


GATES = {  # 放回之後每一關照一般流程判;可核可的兩關停在待核可(沒有核可就不寫)
    "version_changed": (_other_version, Result.BLOCKED, BlockCode.VERSION_CHANGED),
    "policy_version_changed": (_policy_upgraded, Result.BLOCKED, BlockCode.POLICY_VERSION_CHANGED),
    "decision_stale": (_stale, Result.BLOCKED, BlockCode.DECISION_STALE),
    "campaign_not_allowed": (_not_allowed, Result.BLOCKED, BlockCode.CAMPAIGN_NOT_ALLOWED),
    "budget_increase_too_large": (_over_ratio, Result.AWAITING_APPROVAL,
                                  BlockCode.BUDGET_INCREASE_TOO_LARGE),
    "aggregate_limit_reached": (_aggregate_full, Result.AWAITING_APPROVAL,
                                BlockCode.AGGREGATE_LIMIT_REACHED),
}


@pytest.mark.parametrize("gate", list(GATES))
def test_a_replayed_proposal_goes_through_every_gate(h, monkeypatch, gate):
    change, kind, code = GATES[gate]
    dead_letter(h)
    assert replay(h) is ReplayOutcome.REQUEUED
    change(h, monkeypatch)

    result = h.process()

    assert (result.kind, result.block_code) == (kind, code)
    assert h.dsp.writes == []  # 不呼叫 DSP 寫入


# ---- [S506] ----
def test_a_replayed_attempt_links_back_to_its_envelope(h):
    prop = dead_letter(h)
    replay(h)

    assert h.process().kind is Result.EXECUTED

    linked = h.query(
        "SELECT d.id, a.seq, a.state, p.disposition FROM dead_letters d "
        "JOIN attempts a ON a.key = d.key "
        "JOIN proposals p ON p.task_id = d.task_id AND p.revision = d.revision "
        "ORDER BY a.seq")
    assert linked == [(1, 1, "in_flight", "handed_off"), (1, 2, "committed_unverified",
                                                          "handed_off"),
                      (1, 3, "verified", "handed_off")]  # 處置是收件表現在的值
    assert h.dsp.writes[0][1] == operation_key(prop)  # 送出的就是信封上那把鍵


# ---- 代碼審第 1 輪 ----
def test_a_replay_reads_the_clock_after_taking_the_write_lock(h):
    dead_letter(h)
    seen = []

    def clock():
        seen.append(h.store._conn.in_transaction)
        return h.clock()

    assert h.store.replay("t1", 1, OPERATOR, clock) is ReplayOutcome.REQUEUED
    assert seen == [True]  # 等鎖期間提案可能過期:拿到鎖之後才讀時間


def test_a_replay_is_not_refused_just_because_the_table_is_long(h, monkeypatch):
    """重放不新增列,只看待處理名額;總列數上限是收新提案用的。"""
    dead_letter(h)
    h.submit(task_id="t2", campaign_id="c2")
    h.process()  # t2 寫成、結案:列還在
    monkeypatch.setattr(inbox_store, "MAX_ROWS", 2)

    assert replay(h) is ReplayOutcome.REQUEUED


def test_a_dead_letter_from_before_the_upgrade_gets_its_envelope_on_replay(h):
    """升級前就進死信的沒有信封:第一次重放時補寫,稽核照樣接得到信封。"""
    prop = dead_letter(h)
    with h.store.transaction() as tx:
        tx.conn.execute("DELETE FROM dead_letter_ops")
        tx.conn.execute("DELETE FROM dead_letters")

    assert replay(h) is ReplayOutcome.REQUEUED

    assert envelopes(h) == [(2, "t1", 1, content_hash(prop), operation_key(prop), "transient",
                             "delivery_limit", "dsp_unavailable", MAX_DELIVERIES)]
    assert [row[:4] for row in audit(h)] == [(2, "t1", 1, "replay_requested"),
                                            (2, "t1", 1, "replay_requeued")]


def test_a_dead_letter_whose_proposal_cannot_be_read_back_is_not_replayed(h):
    """讀不回提案的死信:補不了信封、之後也處理不了,拒絕重放(代碼審第 2 輪外家席)。"""
    dead_letter(h)
    with h.store.transaction() as tx:
        tx.conn.execute("DELETE FROM dead_letter_ops")
        tx.conn.execute("DELETE FROM dead_letters")
        tx.conn.execute("UPDATE proposals SET payload = '{'")

    assert replay(h) is ReplayOutcome.UNREADABLE
    assert disposition(h) == ("pending", "dead_letter")
    assert [row[3:] for row in audit(h)] == [("replay_requested", OPERATOR, None),
                                            ("replay_refused", OPERATOR, "unreadable")]
