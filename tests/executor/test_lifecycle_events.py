"""提案生命週期事件與嘗試紀錄的來源欄(Phase 9 增量 1):[S600]~[S603]、[S607]、[S612]、[S620]、
[S623]、[S624]。

事件表跟收件表同一個交易寫、不隨收件表清除;每一支會寫收件表那一列的方法寫一列(續租除外)。
"""

import ast
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from rtb import PROGRAM_VERSION
from rtb.domain.attempt import operation_key
from rtb.executor import attempt_store, inbox_store, runner
from rtb.executor.attempt_store import Actor, Source
from rtb.executor.execution import CampaignView, Result
from rtb.executor.inbox_store import (
    MAX_DELIVERIES,
    RETENTION,
    VISIBILITY_TIMEOUT,
    AwaitingOutcome,
    BlockCode,
    InboxStore,
    LastFailure,
    LifecycleKind,
)
from tests.executor.fakes import Harness, proposal
from tests.executor.write_scan import writes_matching

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"
RATIO = BlockCode.BUDGET_INCREASE_TOO_LARGE
EVENT_COLUMNS = ("kind, task_id, revision, reason, source, actor, deliveries, from_existing, "
                 "tenant, campaign_id, key, policy_version, content_hash, program_version")


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def events(h, since=0):
    rows = h.query(f"SELECT id, {EVENT_COLUMNS} FROM lifecycle_events WHERE id > ? "  # noqa: S608
                   "ORDER BY id", (since,))
    names = ["id", *EVENT_COLUMNS.split(", ")]
    return [dict(zip(names, row, strict=True)) for row in rows]


def last_id(h):
    return h.query("SELECT coalesce(max(id), 0) FROM lifecycle_events")[0][0]


def kinds(h, since):
    return [event["kind"] for event in events(h, since)]


def fresh(h, **overrides):
    """以現在的時鐘為準的提案(時鐘撥過保留期之後還要能收)。"""
    now = h.clock()
    overrides.setdefault("decision_created_at", now.isoformat())
    overrides.setdefault("decision_expires_at", (now + timedelta(minutes=30)).isoformat())
    return proposal(**overrides)


def receive(h, owner="w1"):
    with h.store.transaction() as tx:
        return h.store.receive(tx, h.clock(), owner)


def awaiting(h, task_id, owner="w1"):
    """取件後停在比例那一關的待核可,回傳待核可那一份(給處理待核可的三種轉換用)。"""
    delivery = receive(h, owner)
    assert delivery.message.task_id == task_id
    with h.store.transaction() as tx:
        assert h.store.await_approval(tx, delivery.receipt, h.clock(), RATIO)
    with h.store.transaction() as tx:
        return next(item for item in h.store.awaiting(tx, h.clock() + timedelta(hours=2))
                    if item.message.task_id == task_id)


def _write(h, fn):
    before = last_id(h)
    with h.store.transaction() as tx:
        fn(tx)
    return kinds(h, before)


def every_write_path(h):
    """逐一觸發收件口模組每一支寫收件表那一列的方法;回傳 {路徑: 這一次新增的事件種類}。"""
    added, now = {}, h.clock

    before = last_id(h)
    h.store.accept(fresh(h, task_id="a"), now)
    added["accept"] = kinds(h, before)

    before = last_id(h)
    h.store.accept(fresh(h, task_id="a", revision=2, requested_change={"new_budget": 170}), now)
    added["accept_supersede"] = kinds(h, before)

    h.store.accept(fresh(h, task_id="short", decision_expires_at=(
        now() + timedelta(minutes=1)).isoformat()), now)
    h.clock.advance(minutes=2)
    before = last_id(h)
    h.store.accept(fresh(h, task_id="b"), now)
    added["accept_sweep"] = kinds(h, before)[:1]  # 到期標記那一列;之後是 b 自己的收件

    return added | _lease_paths(h) | _awaiting_paths(h) | _existing_key_paths(h) | (
        _dead_letter_paths(h))


def _lease_paths(h):
    added, s, now = {}, h.store, h.clock
    before = last_id(h)
    first = receive(h)
    added["receive"] = kinds(h, before)

    added["release"] = _write(h, lambda tx: s.release(tx, first.receipt, now(),
                                                       LastFailure.DSP_UNAVAILABLE))
    again = receive(h)
    added["extend"] = _write(h, lambda tx: s.extend(tx, again.receipt, now()))
    h.clock.advance(seconds=VISIBILITY_TIMEOUT.total_seconds() + 1)
    before = last_id(h)
    reclaimed = receive(h, "w2")
    added["receive_reclaimed"] = kinds(h, before)
    added["ack_handed_off"] = _write(h, lambda tx: s.ack_handed_off(tx, reclaimed.receipt, now()))

    third = receive(h)
    added["ack_blocked"] = _write(h, lambda tx: s.ack_blocked(
        tx, third.receipt, now(), BlockCode.VERSION_CHANGED))
    h.store.accept(fresh(h, task_id="c"), now)
    fourth = receive(h)
    added["ack_expired"] = _write(h, lambda tx: s.ack_expired(tx, fourth.receipt, now()))
    h.store.accept(fresh(h, task_id="d"), now)
    fifth = receive(h)
    added["await_approval"] = _write(h, lambda tx: s.await_approval(
        tx, fifth.receipt, now(), RATIO))
    return added


def drain(h):
    """把還在待處理的都取出來結案,下一段路徑從乾淨的佇列開始(這些事件不計)。"""
    while (delivery := receive(h, "drain")) is not None:
        with h.store.transaction() as tx:
            h.store.ack_expired(tx, delivery.receipt, h.clock())


def _awaiting_paths(h):
    added, s, now = {}, h.store, h.clock
    for outcome in AwaitingOutcome:
        drain(h)
        task = f"aw-{outcome.value}"
        h.store.accept(fresh(h, task_id=task), now)
        item = awaiting(h, task)
        if outcome is AwaitingOutcome.SUPERSEDED:
            h.store.accept(fresh(h, task_id=task, revision=2), now)
        added[f"settle_awaiting_{outcome.value}"] = _write(h, lambda tx, i=item, o=outcome: (
            s.settle_awaiting(tx, i.message, o, now(), owner="w1")))
    return added


def _existing_key_paths(h):
    """取件撞到既有鍵直接確認;對帳原子接手租約。"""
    added, now = {}, h.clock
    drain(h)
    h.store.accept(fresh(h, task_id="k"), now)
    assert h.executor("w1").process_one().kind is Result.EXECUTED
    h.store.accept(fresh(h, task_id="k", revision=2), now)  # 同一把鍵的下一份修訂
    before = last_id(h)
    assert receive(h) is None
    added["receive_existing_key"] = kinds(h, before)

    h.store.accept(fresh(h, task_id="r"), now)
    delivery = receive(h, "w1")
    h.clock.advance(seconds=VISIBILITY_TIMEOUT.total_seconds() + 1)
    added["take_over"] = _write(h, lambda tx: h.store.take_over(tx, delivery.message, now(), "w2"))
    return added


def _dead_letter_paths(h):
    added, now = {}, h.clock
    drain(h)
    h.store.accept(fresh(h, task_id="dl"), now)
    for _ in range(MAX_DELIVERIES):
        delivery = receive(h)
        with h.store.transaction() as tx:
            h.store.release(tx, delivery.receipt, now(), LastFailure.DSP_UNAVAILABLE)
    before = last_id(h)
    assert receive(h) is None
    added["receive_dead_letter"] = kinds(h, before)
    before = last_id(h)
    assert h.store.replay("dl", 1, "ops-alice", now).value == "requeued"
    added["replay"] = kinds(h, before)
    return added


K = LifecycleKind
EXPECTED = {
    "accept": [K.RECEIVED],
    "accept_supersede": [K.SUPERSEDED, K.RECEIVED],
    "accept_sweep": [K.EXPIRED],
    "receive": [K.DELIVERED],
    "release": [K.LEASE_RELEASED],
    "extend": [],  # 續租不寫事件(計劃明寫排除)
    "receive_reclaimed": [K.RECLAIMED],
    "ack_handed_off": [K.HANDED_OFF],
    "ack_blocked": [K.BLOCKED],
    "ack_expired": [K.EXPIRED],
    "await_approval": [K.AWAITING_APPROVAL],
    "settle_awaiting_expired": [K.BLOCKED],
    "settle_awaiting_superseded": [K.SUPERSEDED],
    "settle_awaiting_released": [K.APPROVAL_RELEASED],
    "receive_existing_key": [K.DELIVERED, K.HANDED_OFF],
    "take_over": [K.RECLAIMED],
    "receive_dead_letter": [K.DELIVERED, K.DEAD_LETTERED],
    "replay": [K.REPLAY_REQUEUED],
}
# 觸發清單涵蓋的方法(收件口模組裡寫收件表那一列的每一支;續租照計劃排除)
COVERED_METHODS = {"accept", "receive", "release", "extend", "ack_handed_off", "ack_blocked",
                   "ack_expired", "await_approval", "settle_awaiting", "take_over", "replay"}


# ---- [S600] ----
def test_every_inbox_write_path_writes_one_lifecycle_event(h):
    added = every_write_path(h)
    assert added == EXPECTED

    # 清單要跟程式對得上:機械找出每一支(本體或它呼叫的同類方法)寫收件表的公開方法
    writers = writes_matching(SRC / "executor" / "inbox_store.py",
                              r"\b(UPDATE proposals|INSERT INTO proposals)\b")
    assert {name.split(".")[-1] for name in writers} == COVERED_METHODS

    # 那次寫入回滾時事件也不在
    drain(h)
    h.store.accept(fresh(h, task_id="rb"), h.clock)
    delivery = receive(h)
    before = last_id(h)
    with pytest.raises(RuntimeError), h.store.transaction() as tx:
        assert h.store.ack_blocked(tx, delivery.receipt, h.clock(), BlockCode.VERSION_CHANGED)
        raise RuntimeError("故障注入:確認之後、提交之前")
    assert events(h, before) == []
    assert h.query("SELECT disposition FROM proposals WHERE task_id = 'rb'") == [("in_progress",)]


# ---- [S601] ----
def test_every_lifecycle_event_kind_has_a_real_trigger(h):
    every_write_path(h)
    seen = events(h)
    assert {event["kind"] for event in seen} == {kind.value for kind in LifecycleKind}
    assert {event["source"] for event in seen} <= {source.value for source in Source}
    reasons = {member.value for enum in (BlockCode, LastFailure, inbox_store.DeadLetterReason)
               for member in enum}
    assert {event["reason"] for event in seen} - {None} <= reasons

    h.store.accept(fresh(h, task_id="x"), h.clock)
    delivery = receive(h)
    with h.store.transaction():  # 寫事件的函式只收列舉成員
        for bad in ({"kind": "handed_off"}, {"reason": "version_changed"},
                    {"by": ("executor_loop", "w1")}):
            args = {"kind": LifecycleKind.HANDED_OFF, "reason": None,
                    "by": Actor(Source.EXECUTOR_LOOP, "w1"), **bad}
            with pytest.raises(ValueError):
                h.store._log(h.clock(), delivery.message.task_id, 1, args["kind"], args["by"],
                             reason=args["reason"])
    with pytest.raises(ValueError):
        Actor("executor_loop", "w1")  # type: ignore[arg-type]


# ---- [S602] ----
def test_lifecycle_events_survive_the_inbox_retention_purge(h):
    h.submit(task_id="p1")
    assert h.process().kind is Result.EXECUTED
    before = events(h)
    assert [event["kind"] for event in before] == ["received", "delivered", "handed_off"]

    h.clock.advance(seconds=RETENTION.total_seconds() + 60)
    h.store.accept(fresh(h, task_id="p2"), h.clock)  # 收件時順手整批清掉已結案的舊任務
    assert h.query("SELECT count(*) FROM proposals WHERE task_id = 'p1'") == [(0,)]
    assert events(h)[:len(before)] == before  # 一列不少、一欄不改

    source = (SRC / "executor" / "inbox_store.py").read_text(encoding="utf-8")
    assert "UPDATE lifecycle_events" not in source
    assert "DELETE FROM lifecycle_events" not in source


# ---- [S603] ----
def test_attempt_rows_record_their_source_and_actor(h):
    h.submit(task_id="s1")
    assert h.executor("worker-9").process_one().kind is Result.EXECUTED
    rows = h.query("SELECT source, actor FROM attempts ORDER BY seq")
    assert rows and set(rows) == {("executor_loop", "worker-9")}

    # 啟動恢復記行程身分;人工處置記操作人
    with h.store.transaction() as tx:
        begun = attempt_store.begin(tx, proposal(task_id="s2", campaign_id="c2"), h.clock(),
                                    capability_expires_at=h.clock() + timedelta(minutes=1))
    key = begun.row.key
    h.clock.advance(minutes=5)
    opened = runner._recover(h.store, h.clock, "boot-1234")
    assert key in opened.recovery.moved
    with h.store.transaction() as tx:
        row = attempt_store.latest(tx, key)
        row = attempt_store.transition(tx, key, row.seq, attempt_store.AttemptState.ESCALATED,
                                       h.clock(), code=attempt_store.OutcomeCode.SEND_LIMIT_REACHED,
                                       by=Actor(Source.EXECUTOR_LOOP, "w1"))
        attempt_store.resolve(tx, key, row.seq, attempt_store.AttemptState.FAILED, "checked dsp",
                              h.clock(), by=Actor(Source.ADMIN_COMMAND, "ops-alice"))
    rows = h.query("SELECT state, source, actor FROM attempts WHERE key = ? ORDER BY seq", (key,))
    assert rows[1:] == [("unknown", "startup_recovery", "boot-1234"),
                        ("escalated", "executor_loop", "w1"),
                        ("failed", "admin_command", "ops-alice")]

    # 機械守衛:正式程式每一處寫嘗試紀錄都明傳來源與執行者
    assert _calls_missing_keyword(
        {"begin", "transition", "record_verification_timeout", "resolve", "recover_in_flight"},
        "by", receiver="attempt_store") == []


def _calls_missing_keyword(names, keyword, receiver=None):
    missing = []
    for path in sorted((SRC / "executor").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in names):
                continue
            if receiver is not None and not (isinstance(node.func.value, ast.Name)
                                             and node.func.value.id == receiver):
                continue
            if keyword not in {k.arg for k in node.keywords}:
                missing.append(f"{path.name}:{node.lineno} {node.func.attr}")
    return missing


# ---- [S607] ----
def test_an_old_executor_database_gains_the_lifecycle_table_and_columns(tmp_path):
    db = tmp_path / "old.db"
    InboxStore(db).close()
    conn = sqlite3.connect(db)
    conn.executescript(
        "DROP TABLE lifecycle_events; DROP TABLE dsp_calls;"
        "ALTER TABLE attempts DROP COLUMN source; ALTER TABLE attempts DROP COLUMN actor;"
        "ALTER TABLE attempts DROP COLUMN program_version;"
        "INSERT INTO attempts (key, seq, campaign_id, state, send_count, verification_timeouts,"
        " written_at) VALUES ('k-old', 1, 'c1', 'verified', 1, 0, '2026-09-01T00:00:00.000000Z');")
    conn.close()

    store = InboxStore(db)
    try:
        with store.transaction() as tx:
            tables = {r[0] for r in tx.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            old = tx.conn.execute("SELECT source, actor, program_version FROM attempts "
                                  "WHERE key = 'k-old'").fetchall()
            indexes = {r[0] for r in tx.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'")}
    finally:
        store.close()
    assert {"lifecycle_events", "dsp_calls"} <= tables
    assert old == [(None, None, None)]
    assert {"lifecycle_events_by_time", "dsp_calls_by_time"} <= indexes


# ---- [S612] ----
def test_inbox_side_events_record_no_worker_and_cover_every_swept_row(h):
    soon = (h.clock() + timedelta(minutes=1)).isoformat()
    h.store.accept(fresh(h, task_id="old-1", decision_expires_at=soon), h.clock)
    h.store.accept(fresh(h, task_id="old-2", decision_expires_at=soon), h.clock)
    h.clock.advance(minutes=2)
    before = last_id(h)
    h.store.accept(fresh(h, task_id="new"), h.clock)  # 不是那兩個任務的收件,也要逐列寫
    added = events(h, before)
    assert [(e["kind"], e["task_id"]) for e in added] == [
        ("expired", "old-1"), ("expired", "old-2"), ("received", "new")]
    assert {(e["source"], e["actor"]) for e in added} == {("inbox", None)}

    before = last_id(h)
    delivery = receive(h, "worker-7")
    with h.store.transaction() as tx:
        h.store.ack_blocked(tx, delivery.receipt, h.clock(), BlockCode.CAMPAIGN_NOT_ACTIVE)
    loop = events(h, before)
    assert {(e["source"], e["actor"]) for e in loop} == {("executor_loop", "worker-7")}

    # 處理待核可由執行迴圈呼叫,傳自己的擁有者(機械守衛:正式程式都明傳)
    assert _calls_missing_keyword({"settle_awaiting"}, "owner") == []


# ---- [S620] ----
def test_a_superseded_event_carries_the_old_revisions_own_fields(h):
    old = fresh(h, task_id="sp", requested_change={"new_budget": 120}, policy_version="p-old",
                campaign_id="c1")
    new = fresh(h, task_id="sp", revision=2, requested_change={"new_budget": 180},
                campaign_id="c2")
    h.store.accept(old, h.clock)
    before = last_id(h)
    h.store.accept(new, h.clock)
    superseded, received = events(h, before)
    assert superseded["kind"] == "superseded" and superseded["revision"] == 1
    assert (superseded["campaign_id"], superseded["key"], superseded["policy_version"]) == (
        "c1", operation_key(old), "p-old")
    assert (received["campaign_id"], received["key"], received["policy_version"]) == (
        "c2", operation_key(new), new.policy_version)


# ---- [S623] ----
def test_every_new_record_carries_the_program_version(h):
    h.submit(task_id="v1")
    assert h.process().kind is Result.EXECUTED
    for table in ("lifecycle_events", "attempts", "dsp_calls"):
        versions = h.query(f"SELECT DISTINCT program_version FROM {table}")  # noqa: S608
        assert versions == [(PROGRAM_VERSION,)], table
    assert isinstance(PROGRAM_VERSION, str) and PROGRAM_VERSION


# ---- [S624] ----
def test_settlements_from_an_existing_key_are_flagged(h):
    # 一般第一次確認:否
    h.submit(task_id="e1")
    assert h.process().kind is Result.EXECUTED
    first = [e for e in events(h) if e["kind"] == "handed_off"]
    assert [e["from_existing"] for e in first] == [0]

    # 取件時撞到既有鍵直接確認:是
    h.submit(task_id="e1", revision=2)
    before = last_id(h)
    assert receive(h) is None
    settled = [e for e in events(h, before) if e["kind"] == "handed_off"]
    assert [(e["revision"], e["from_existing"]) for e in settled] == [(2, 1)]

    # 開始一筆時鍵已存在,依既有結果確認:是(兩份修訂都已取件、還沒開過嘗試)
    h.submit(task_id="e2", campaign_id="c2")
    one = receive(h, "w1")
    h.submit(task_id="e2", revision=2, campaign_id="c2")
    two = receive(h, "w2")
    assert h.executor("w1")._process(one.message, one.receipt).kind is Result.EXECUTED
    # 讓第二份照樣過執行前檢查,走到開始一筆才發現鍵已存在
    h.dsp.campaigns["c2"] = CampaignView(budget=100, status="active", version=3)
    before = last_id(h)
    result = h.executor("w2")._process(two.message, two.receipt)
    assert result.kind is Result.HANDED_OFF_TO_EXISTING
    settled = [e for e in events(h, before) if e["kind"] == "handed_off"]
    assert [(e["revision"], e["from_existing"], e["actor"]) for e in settled] == [(2, 1, "w2")]
    assert h.query("SELECT count(*) FROM lifecycle_events WHERE from_existing NOT IN (0, 1)") == [
        (0,)]


# ---- [S600] 代碼審第 1 輪:放掉租約的原因是表上實際留下的最後一次失敗原因 ----
def test_a_lease_release_event_records_the_last_failure_left_on_the_row(h):
    h.submit(task_id="lf")
    first = receive(h)
    with h.store.transaction() as tx:
        assert h.store.release(tx, first.receipt, h.clock(), LastFailure.TABLE_FULL)
    second = receive(h)
    with h.store.transaction() as tx:
        assert h.store.release(tx, second.receipt, h.clock(), None)  # 這次沒帶原因
    released = [e for e in events(h) if e["kind"] == "lease_released"]
    assert [e["reason"] for e in released] == ["table_full", "table_full"]


# ---- [S600] 代碼審第 1 輪:自己的租約還沒到期時接手,不是「租約過期被接手」 ----
def test_the_same_owner_taking_over_its_live_lease_writes_no_reclaim_event(h):
    h.submit(task_id="own")
    delivery = receive(h, "w1")
    for _ in range(2):  # 同一個擁有者連續兩輪對帳接手自己的租約
        with h.store.transaction() as tx:
            assert h.store.take_over(tx, delivery.message, h.clock(), "w1") is not None
    assert [e["kind"] for e in events(h)] == ["received", "delivered"]

    for owner in ("w1", "w2"):  # 舊租約真的到期了才記:自己接手自己過期的租約也算,換人更算
        h.clock.advance(seconds=VISIBILITY_TIMEOUT.total_seconds() + 1)
        with h.store.transaction() as tx:
            assert h.store.take_over(tx, delivery.message, h.clock(), owner) is not None
    reclaimed = [(e["kind"], e["actor"]) for e in events(h) if e["kind"] == "reclaimed"]
    assert reclaimed == [("reclaimed", "w1"), ("reclaimed", "w2")]
