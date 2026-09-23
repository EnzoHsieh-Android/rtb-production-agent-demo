"""可觀測(Phase 6 增量 4):S360 到 S369、S380。

唯讀查詢:總曝險停下次數、表滿延後份數、人工核可、額度使用率、總曝險稽核明細。資料一律從既有的耐久
紀錄讀(停下紀錄表、嘗試紀錄、收件表、核可使用表),不另存計數器。
"""

import json
import re
import sqlite3
from datetime import timedelta

import pytest

from rtb.domain.attempt import AttemptState, OutcomeCode, operation_key
from rtb.executor import attempt_store, inbox_store, observability
from rtb.executor.attempt_store import AGGREGATE_WINDOW, Reservation
from rtb.executor.execution import CampaignView
from rtb.executor.inbox_store import InboxStore, Stop, StopKind
from tests.executor.conftest import NOW
from tests.executor.fakes import Harness, proposal

A = AttemptState
C = OutcomeCode
TENANT, OTHER = "t-default", "someone-else"
EXPIRES = NOW + timedelta(minutes=2)
VERIFY = [(A.COMMITTED_UNVERIFIED, None), (A.VERIFIED, None)]
HOUR = timedelta(hours=1)


@pytest.fixture
def store(tmp_path):
    opened = InboxStore(tmp_path / "executor.db")
    yield opened
    opened.close()


def begin(store, prop, now=NOW, reservation=None):
    with store.transaction() as tx:
        return attempt_store.begin(tx, prop, now, capability_expires_at=EXPIRES,
                                   reservation=reservation).row


def drive(store, row, path, now=NOW):
    for target, code in path:
        extra = {"written_version": 4} if target is A.COMMITTED_UNVERIFIED else {}
        with store.transaction() as tx:
            row = attempt_store.transition(tx, row.key, row.seq, target, now, code=code, **extra)
    return row


def stop(store, kind, now=NOW, tenant=TENANT, task="s1", campaign="c1"):
    prop = proposal(task_id=task, campaign_id=campaign)
    with store.transaction() as tx:
        store.record_stop(tx, Stop(kind, prop, operation_key(prop), tenant, 10, 90, 100), now)
    return prop


def read(store, query, *args, **kwargs):
    """停下紀錄表歸收件口模組管:讀它的查詢要帶收件表物件(由它的方法核對交易是它開的)。"""
    with store.transaction() as tx:
        if query in (observability.aggregate_stop_count, observability.table_full_deferral_count,
                     observability.aggregate_audit, observability.approval_counts):
            return query(store, tx, *args, **kwargs)
        return query(tx, *args, **kwargs)


AGG, FULL = StopKind.AGGREGATE_LIMIT_REACHED, StopKind.TABLE_FULL


# ---- [S360] ----
def test_aggregate_stop_count_filters_by_tenant_campaign_and_time(store):
    stop(store, AGG, NOW, task="a1")
    stop(store, AGG, NOW + HOUR, task="a2", campaign="c2")
    stop(store, AGG, NOW + 2 * HOUR, task="a3", tenant=OTHER, campaign="c3")
    stop(store, FULL, NOW, task="f1")  # 別的種類不算

    count = observability.aggregate_stop_count
    assert read(store, count) == 3
    assert read(store, count, tenant=TENANT) == 2
    assert read(store, count, campaign_id="c2") == 1
    assert read(store, count, since=NOW + HOUR) == 2  # 包含起點
    assert read(store, count, until=NOW + HOUR) == 1  # 不包含終點
    assert read(store, count, tenant=TENANT, since=NOW, until=NOW + 2 * HOUR) == 2
    assert read(store, count, tenant="nobody") == 0


# ---- [S361] ----
def test_table_full_deferrals_count_proposals_not_events(store):
    for minute in range(3):  # 同一份提案反覆延後:停下紀錄同一份提案同一種類只記一列
        stop(store, FULL, NOW + timedelta(minutes=minute), task="f1")
    stop(store, FULL, NOW, task="f2", campaign="c2")
    stop(store, AGG, NOW, task="a1")

    count = observability.table_full_deferral_count
    assert read(store, count) == 2
    assert read(store, count, campaign_id="c2") == 1
    assert read(store, count, since=NOW + timedelta(minutes=1)) == 0  # 只記第一次的時間


# ---- [S363] ----
@pytest.mark.parametrize(("reserved", "limit", "expected"), [
    (50, 200, (50, 200, 150, True)),
    (50, 30, (50, 30, 0, True)),  # 剩餘小於 0 回 0
    (50, 0, (50, 0, 0, False)),  # 門檻 0:不准加預算、不除以 0
    (0, 0, (0, 0, 0, False)),
])
def test_utilization_matches_the_reservation_ledger(store, reserved, limit, expected):
    if reserved:
        begin(store, proposal(), reservation=Reservation(TENANT, reserved, 10**9))
    got = read(store, observability.utilization, TENANT, limit, NOW)

    assert (got.used, got.limit, got.remaining, got.increases_allowed) == expected


def test_utilization_uses_the_given_now_for_the_window(store):
    row = begin(store, proposal(), reservation=Reservation(TENANT, 50, 10**9))
    drive(store, row, VERIFY)

    edge = NOW + AGGREGATE_WINDOW
    assert read(store, observability.utilization, TENANT, 100, edge).used == 50
    assert read(store, observability.utilization, TENANT, 100,
                edge + timedelta(seconds=1)).used == 0


# ---- [S364] ----
def _snapshot(store):
    tables = [name for (name,) in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return {table: sorted(store._conn.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608 - 表名來自資料庫自己
            for table in tables}


def test_observability_queries_write_nothing(store):
    begin(store, proposal(), reservation=Reservation(TENANT, 50, 10**9))
    stop(store, AGG, task="a1")
    stop(store, FULL, task="f1")
    before = _snapshot(store)
    assert len(before) >= 4  # 真的比對到多張表

    read(store, observability.aggregate_stop_count, tenant=TENANT)
    read(store, observability.table_full_deferral_count)
    read(store, observability.utilization, TENANT, 100, NOW)
    read(store, observability.aggregate_audit, TENANT, 100, NOW, NOW - HOUR, NOW + HOUR)

    assert _snapshot(store) == before


# ---- [S365] ----
def test_f7_is_explainable_with_the_observability_queries(tmp_path, clock):
    """小規模的 F7:30 個廣告各加 10,門檻 105,放行 10 筆、停下 20 筆。"""
    h = Harness(tmp_path, clock, max_pending=30)
    try:
        ids = [f"k{i:02d}" for i in range(30)]
        h.dsp.campaigns = {c: CampaignView(budget=100, status="active", version=3) for c in ids}
        h.config.write_text(json.dumps({"tenants": {"acct": {
            "campaigns": ids, "max_budget": 1000, "aggregate_limit": 105}}}), encoding="utf-8")
        for i, campaign in enumerate(ids):
            h.store.accept(proposal(task_id=f"t{i}", campaign_id=campaign,
                                    requested_change={"new_budget": 110}), h.clock)
        while h.process().kind.value != "idle":
            pass
        now = h.clock()
        started = {row[0] for row in h.query("SELECT key FROM attempts WHERE seq = 1")}

        with h.store.transaction() as tx:
            stopped = observability.aggregate_stop_count(h.store, tx, tenant="acct")
            use = observability.utilization(tx, "acct", 105, now)
            audit = observability.aggregate_audit(h.store, tx, "acct", 105, now, now - HOUR,
                                                  now + HOUR)

        assert (stopped, use.used, use.remaining) == (20, 100, 5)
        assert len(h.dsp.writes) == 10
        assert {entry.key for entry in audit.passed} == started
        assert len(audit.stopped) == 20 and not {e.key for e in audit.stopped} & started
        assert sum(entry.amount for entry in audit.holding) == 100
    finally:
        h.close()


# ---- [S367] ----
def test_the_aggregate_audit_lists_what_passed_and_what_was_stopped(store):
    passed = begin(store, proposal(task_id="p1"), NOW, Reservation(TENANT, 50, 10**9))
    begin(store, proposal(task_id="x1", campaign_id="c2"), NOW, Reservation(OTHER, 70, 10**9))
    begin(store, proposal(task_id="late", campaign_id="c3"), NOW + 3 * HOUR,
          Reservation(TENANT, 20, 10**9))  # 範圍外,但目前佔額度
    stopped = stop(store, AGG, NOW + timedelta(minutes=5), task="a1", campaign="c4")
    stop(store, FULL, NOW, task="f1", campaign="c5")  # 表滿延後不是阻擋
    stop(store, AGG, NOW, task="a2", tenant=OTHER, campaign="c6")

    audit = read(store, observability.aggregate_audit, TENANT, 100, NOW + 3 * HOUR,
                 NOW, NOW + HOUR)

    assert [(e.key, e.task_id, e.revision, e.amount, e.counted_now, e.legacy)
            for e in audit.passed] == [(passed.key, "p1", 1, 50, True, False)]
    assert [(e.key, e.task_id) for e in audit.stopped] == [(operation_key(stopped), "a1")]
    # 不受範圍限制,而且每一筆都帶身分(代碼審第 1 輪兩席 Codex:原本只帶鍵與金額)
    assert sorted((e.task_id, e.revision, e.amount, e.counted_now) for e in audit.holding) == [
        ("late", 1, 20, True), ("p1", 1, 50, True)]
    assert (audit.utilization.used, audit.utilization.remaining) == (70, 30)
    with pytest.raises(ValueError, match="範圍"):
        read(store, observability.aggregate_audit, TENANT, 100, NOW, None, NOW)


def test_the_audit_uses_one_now_for_the_window_edge(store):
    row = begin(store, proposal(), NOW, Reservation(TENANT, 50, 10**9))
    drive(store, row, VERIFY)
    for moment, counted in ((NOW + AGGREGATE_WINDOW, True),
                            (NOW + AGGREGATE_WINDOW + timedelta(seconds=1), False)):
        audit = read(store, observability.aggregate_audit, TENANT, 100, moment,
                     NOW - HOUR, NOW + HOUR)
        use = read(store, observability.utilization, TENANT, 100, moment)
        assert audit.passed[0].counted_now is counted
        assert (bool(audit.holding), audit.utilization.used) == (counted, use.used)


# ---- [S368] ----
def _legacy_database(tmp_path):
    """加 100、有租戶的減預算、暫停、舊加預算(改成 30)、舊減預算(900 減到 500)、已出窗口的
    已驗證寫入、表滿延後。舊列照 Phase 6 之前的格式造:拿掉租戶與預留欄後再開庫補回。"""
    db = tmp_path / "executor.db"
    store = InboxStore(db)
    begin(store, proposal(task_id="old-up", campaign_id="c7", requested_change={"new_budget": 30}))
    begin(store, proposal(task_id="old-down", campaign_id="c8",
                          requested_change={"new_budget": 500}))
    store.close()
    conn = sqlite3.connect(db)
    # Phase 6 之前的資料庫沒有按租戶的索引(增量 4 加的),先拿掉才拿得掉它參照的租戶欄
    conn.execute("DROP INDEX IF EXISTS attempts_first_rows_by_tenant")
    conn.execute("ALTER TABLE attempts DROP COLUMN tenant")
    conn.execute("ALTER TABLE attempts DROP COLUMN reserved_amount")
    conn.commit()
    conn.close()
    store = InboxStore(db)
    begin(store, proposal(task_id="up"), NOW, Reservation(TENANT, 100, 10**9))
    begin(store, proposal(task_id="down", campaign_id="c2",
                          requested_change={"new_budget": 50}), NOW, Reservation(TENANT, 0, 10**9))
    begin(store, proposal(task_id="pause", campaign_id="c3", action_type="pause_campaign",
                          requested_change={}), NOW, Reservation(TENANT, 0, 10**9))
    gone = begin(store, proposal(task_id="gone", campaign_id="c4"), NOW - 2 * AGGREGATE_WINDOW,
                 Reservation(TENANT, 40, 10**9))
    drive(store, gone, VERIFY, now=NOW - 2 * AGGREGATE_WINDOW)
    stop(store, FULL, NOW, task="full", campaign="c5")
    return store


def test_the_audit_reconciles_with_used_across_every_kind_of_row(tmp_path):
    store = _legacy_database(tmp_path)
    try:
        audit = read(store, observability.aggregate_audit, TENANT, 10**6, NOW,
                     NOW - 3 * AGGREGATE_WINDOW, NOW + HOUR)
        passed = {e.task_id: (e.amount, e.counted_now, e.legacy) for e in audit.passed}

        assert sum(e.amount for e in audit.holding) == 630  # 100 + 舊 30 + 舊 500
        assert {e.task_id: e.legacy for e in audit.holding} == {
            "up": False, "old-up": True, "old-down": True}
        assert audit.utilization.used == 630
        assert passed == {"up": (100, True, False), "old-up": (30, True, True),
                          "old-down": (500, True, True), "gone": (40, False, False)}
        assert audit.stopped == ()  # 表滿延後不列
        # 舊列算進每一個租戶:別的租戶的清單也看得到它們、並標明是舊列
        other = read(store, observability.aggregate_audit, OTHER, 10**6, NOW,
                     NOW - 3 * AGGREGATE_WINDOW, NOW + HOUR)
        assert {e.task_id for e in other.passed} == {"old-up", "old-down"}
        assert all(e.legacy for e in other.passed)
    finally:
        store.close()


def test_the_audit_reports_the_current_state_of_each_key(store):
    row = begin(store, proposal(), NOW, Reservation(TENANT, 50, 10**9))
    drive(store, row, [(A.FAILED, C.NOT_HAPPENED)])

    audit = read(store, observability.aggregate_audit, TENANT, 100, NOW, NOW - HOUR, NOW + HOUR)

    assert [(e.state, e.counted_now) for e in audit.passed] == [("failed", False)]


# ---- [S369] ----
def _plan(store, sql, params):
    return " ".join(row[-1] for row in store._conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))


# 只說「用了索引」不夠:停下紀錄表的唯一限制本來就以種類開頭,查詢計畫會顯示用了它,實際上只篩掉
# 種類、同種類的列全部掃過。所以斷言用到的是本節指定的那一個索引。
@pytest.mark.parametrize(("filters", "index"), [
    ({"tenant": TENANT}, "write_stops_by_tenant"),
    ({"campaign_id": "c1"}, "write_stops_by_campaign"),
    ({"since": NOW, "until": NOW + HOUR}, "write_stops_by_time"),
])
def test_observability_queries_use_their_indexes(store, filters, index):
    sql, params = inbox_store.stop_count_query(AGG, **filters)
    assert index in _plan(store, sql, params)

    sql, params = attempt_store.first_rows_started_query(TENANT, NOW, NOW + HOUR)
    plan = _plan(store, sql, params)
    assert plan.count("attempts_first_rows_by_tenant") == 2, plan  # 兩段都用上


# 已核可放行數(查詢三):依租戶、依時間走核可使用表的索引;依廣告走「第一列按廣告」的部分索引
@pytest.mark.parametrize(("filters", "index"), [
    ({"tenant": TENANT}, "approval_uses_by_tenant"),
    ({"tenant": TENANT, "since": NOW}, "approval_uses_by_tenant"),
    ({"since": NOW, "until": NOW + HOUR}, "approval_uses_by_time"),
    # 依廣告:先走第一列按廣告的索引,再用核可使用表唯一限制的前兩欄(任務、修訂)查;
    # 操作鍵已決定任務與修訂,多比這兩欄就是為了這一步用得上索引
    ({"campaign_id": "c1"},
     r"attempts_first_rows\b.*approval_uses_1 \(task_id=\? AND revision=\?\)"),
])
def test_applied_count_uses_its_index(store, filters, index):
    sql, params = inbox_store.approval_use_count_query(**filters)
    plan = _plan(store, sql, params)
    assert re.search(rf"\b{index}", plan), plan
    # 只看索引名會假綠:子查詢用上索引、外層照樣整張掃核可使用表(代碼審第 2 輪外家席、資安席)
    assert not re.search(r"\bSCAN u\b", plan), plan


def test_awaiting_condition_with_alias_matches_the_shared_one():
    """帶別名的待核可條件是另寫一份字面:共用那句改了,這裡要跟著改。"""
    aliased = " AND ".join(f"p.{part}" for part in inbox_store.AWAITING.split(" AND "))
    assert aliased == inbox_store._AWAITING_P


# ---- [S366]、[S380] ----
def _indexes(conn):
    return {name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL")}


OBSERVABILITY_INDEXES = {"write_stops_by_tenant", "write_stops_by_campaign", "write_stops_by_time",
                         "attempts_first_rows_by_tenant", "approval_uses_by_tenant",
                         "approval_uses_by_time"}


@pytest.mark.parametrize("old", ["no_tenant_column", "column_but_no_index"])
def test_an_old_attempts_table_gains_the_tenant_index_after_the_column(tmp_path, old):
    db = tmp_path / "executor.db"
    store = InboxStore(db)
    begin(store, proposal(), NOW, Reservation(TENANT, 50, 10**9))
    store.close()
    conn = sqlite3.connect(db)
    for name in OBSERVABILITY_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    if old == "no_tenant_column":
        conn.execute("ALTER TABLE attempts DROP COLUMN tenant")
        conn.execute("ALTER TABLE attempts DROP COLUMN reserved_amount")
    conn.commit()
    before = conn.execute("SELECT key, seq, state FROM attempts").fetchall()
    conn.close()

    reopened = InboxStore(db)  # 不丟例外
    try:
        assert _indexes(reopened._conn) >= OBSERVABILITY_INDEXES
        assert reopened._conn.execute("SELECT key, seq, state FROM attempts").fetchall() == before
    finally:
        reopened.close()


def test_an_old_database_gains_the_observability_indexes(tmp_path):
    db = tmp_path / "executor.db"
    store = InboxStore(db)
    stop(store, AGG, task="a1")
    store.close()
    conn = sqlite3.connect(db)
    for name in OBSERVABILITY_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()
    before = conn.execute("SELECT * FROM write_stops").fetchall()
    conn.close()

    reopened = InboxStore(db)
    try:
        assert _indexes(reopened._conn) >= OBSERVABILITY_INDEXES
        assert reopened._conn.execute("SELECT * FROM write_stops").fetchall() == before
    finally:
        reopened.close()


def test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox(tmp_path, store):
    """停下紀錄表歸收件口模組管:別的收件表開的交易讀不到(代碼審第 1 輪架構席)。"""
    other = InboxStore(tmp_path / "other.db")
    try:
        with other.transaction() as tx:
            with pytest.raises(attempt_store.NotInTransaction):
                store.stop_count(tx, AGG)
            with pytest.raises(attempt_store.NotInTransaction):
                store.stops(tx, AGG, TENANT, NOW, NOW + HOUR)
            with pytest.raises(attempt_store.NotInTransaction):
                store.awaiting_count(tx)
            with pytest.raises(attempt_store.NotInTransaction):
                store.approval_use_count(tx)
    finally:
        other.close()


def test_the_audit_handles_more_holdings_than_sqlite_bind_parameters(store):
    """目前佔額度的筆數沒有上限:不能把每把鍵當成一個查詢參數(代碼審第 2 輪兩席 Codex)。
    參數上限依 SQLite 編譯版本不同(舊版 999、新版可到 25 萬),這裡把這條連線的上限調低到 100。"""
    store._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 100)
    many = 110
    at = attempt_store._iso(NOW)
    snapshot = json.dumps({"requested_change": {"new_budget": 7}})
    rows = []
    for i in range(many):  # 窗口內已驗證、帶這個租戶與預留金額的鍵
        rows += [(f"h{i}", 1, "in_flight", TENANT, 1), (f"h{i}", 2, "verified", None, None)]
    with store.transaction() as tx:
        tx.conn.executemany(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, task_id, revision, action, proposal_json, "
            "tenant, reserved_amount) VALUES (?, ?, 'cx', ?, 1, 0, ?, 't', 1, 'update_budget', ?, "
            "?, ?)",
            [(k, seq, st, at, snapshot, tenant, amount) for k, seq, st, tenant, amount in rows])

    audit = read(store, observability.aggregate_audit, TENANT, 10**9, NOW, NOW - HOUR,
                 NOW + HOUR)

    assert len(audit.holding) == many and audit.utilization.used == many


def test_stop_queries_reject_times_without_a_timezone(store):
    """沒帶時區的時間會被當成本機時間、範圍篩錯:一律拒絕,跟嘗試紀錄同一條規則(代碼審第 2 輪)。"""
    naive = NOW.replace(tzinfo=None)
    with store.transaction() as tx:
        with pytest.raises(ValueError, match="時區"):
            observability.aggregate_stop_count(store, tx, since=naive)
        with pytest.raises(ValueError, match="時區"):
            observability.aggregate_audit(store, tx, TENANT, 100, NOW, naive, NOW)


def test_the_audit_does_not_look_up_each_key_one_by_one(store, monkeypatch):
    """目前狀態跟清單同一次查詢帶出,不逐鍵回查:大量紀錄時逐鍵查詢會在寫入鎖裡跑太久
    (代碼審第 3 輪外家否決席實測 30 萬筆 6.6 秒,超過資料庫忙碌等待上限)。"""
    row = begin(store, proposal(), NOW, Reservation(TENANT, 50, 10**9))
    drive(store, row, [(A.FAILED, C.NOT_HAPPENED)])
    begin(store, proposal(task_id="p2", campaign_id="c2"), NOW, Reservation(TENANT, 30, 10**9))

    def refuse(*_args):
        raise AssertionError("稽核明細不該逐鍵查最新一列")

    monkeypatch.setattr(attempt_store, "latest", refuse)
    audit = read(store, observability.aggregate_audit, TENANT, 100, NOW, NOW - HOUR, NOW + HOUR)

    assert sorted((e.task_id, e.state) for e in audit.passed) == [("p2", "in_flight"),
                                                                  ("t1", "failed")]
    assert [(e.task_id, e.state) for e in audit.holding] == [("p2", "in_flight")]


# ---- [S362] 查詢三 人工核可 ----
RATIO = StopKind.BUDGET_INCREASE_TOO_LARGE
Counts = observability.ApprovalCounts


def _awaiting(store, task, campaign, stage=AGG, tenant=TENANT, with_stop=True):
    """讓一份提案停在待核可(照執行迴圈的做法:處置寫待核可、同一個交易寫停下紀錄)。"""
    from rtb.executor.inbox_store import BlockCode

    prop = proposal(task_id=task, campaign_id=campaign)
    store.accept(prop, lambda: NOW)
    with store.transaction() as tx:
        delivery = store.receive(tx, NOW, "worker")
        assert delivery is not None and delivery.message.task_id == task
        assert store.await_approval(tx, delivery.receipt, NOW, BlockCode(stage.value))
        if with_stop:
            store.record_stop(tx, Stop(stage, prop, operation_key(prop), tenant, 10, None, None),
                              NOW)
    return prop


def _applied(store, prop, stages, tenant=TENANT, at=NOW):
    """照執行迴圈的做法放行:開始一筆與核可使用紀錄在同一個交易寫。"""
    from rtb.executor.inbox_store import ApprovalUse, BlockCode

    with store.transaction() as tx:
        attempt_store.begin(tx, prop, at, capability_expires_at=EXPIRES,
                            reservation=Reservation(tenant, 10, 10**9))
        for stage in stages:
            store.record_approval_use(tx, ApprovalUse("ap-1", prop, operation_key(prop), tenant,
                                                      BlockCode(stage.value), 10, None, None), at)


def test_approval_counts_cover_waiting_and_applied(store):
    _awaiting(store, "w1", "c1")  # 總曝險已滿那一關
    _awaiting(store, "w2", "c2", stage=RATIO)  # 比例過大那一關
    _awaiting(store, "w3", "c3", tenant=OTHER)
    _awaiting(store, "w4", "c4", with_stop=False)  # 接不到停下紀錄:只進總數與未知租戶數
    stop(store, RATIO, task="w1", campaign="c1")  # 同一份提案先前在另一關停過:不重複數
    applied = proposal(task_id="a1", campaign_id="c5")
    stop(store, AGG, task="a1", campaign="c5")  # 比例那一關沒有停下紀錄:依廣告篩也要算到
    _applied(store, applied, [AGG, RATIO])  # 同一份提案兩關各一列
    _applied(store, proposal(task_id="a2", campaign_id="c6"), [AGG], tenant=OTHER)
    # 同任務的上一份修訂在別的廣告開過嘗試、沒用核可:放行只算到這一份修訂的廣告
    _applied(store, proposal(task_id="a3", campaign_id="c9"), [])
    _applied(store, proposal(task_id="a3", revision=2, campaign_id="c7"), [RATIO], at=NOW + HOUR)
    # 同任務同修訂、內容不同(收件表清掉已結案任務後重用):舊內容的第一列不算這筆放行
    _applied(store, proposal(task_id="a4", campaign_id="c10",
                                    requested_change={"new_budget": 111}), [])
    _applied(store, proposal(task_id="a4", campaign_id="c11",
                                    requested_change={"new_budget": 222}), [AGG])

    counts = observability.approval_counts
    assert read(store, counts) == Counts(4, 1, 5)  # 待核可、其中接不到停下紀錄的、已核可放行
    assert read(store, counts, tenant=TENANT) == Counts(2, 1, 4)
    assert read(store, counts, campaign_id="c2") == Counts(1, 1, 0)
    assert read(store, counts, campaign_id="c5") == Counts(0, 1, 2)
    assert read(store, counts, since=NOW + HOUR) == Counts(4, 1, 1)  # 範圍只管已核可放行(含起點)
    assert read(store, counts, until=NOW + HOUR) == Counts(4, 1, 4)  # 不含終點
    assert read(store, counts, tenant="nobody") == Counts(0, 1, 0)
    assert read(store, counts, campaign_id="c9") == Counts(0, 1, 0)
    assert read(store, counts, campaign_id="c10") == Counts(0, 1, 0)
    assert read(store, counts, campaign_id="c11") == Counts(0, 1, 1)


def test_applied_count_follows_the_tenant_recorded_when_the_approval_was_used(store):
    """等待期間廣告換了租戶:停下紀錄留舊租戶,放行記新租戶。這筆放行屬於新租戶、還是這個廣告。"""
    prop = proposal(task_id="m1", campaign_id="c8")
    stop(store, AGG, tenant=OTHER, task="m1", campaign="c8")
    _applied(store, prop, [AGG], tenant=TENANT)

    counts = observability.approval_counts
    assert read(store, counts, tenant=TENANT, campaign_id="c8").applied == 1
    assert read(store, counts, tenant=OTHER, campaign_id="c8").applied == 0


@pytest.mark.parametrize("outcome", ["released", "expired", "superseded"])
def test_settled_awaiting_proposals_stop_counting(store, outcome):
    from rtb.executor.inbox_store import AwaitingOutcome

    _awaiting(store, "w1", "c1")
    assert read(store, observability.approval_counts) == Counts(1, 0, 0)
    later = NOW + timedelta(days=365)  # 已到期:處理待核可那一步一定讀得到它
    with store.transaction() as tx:
        [found] = store.awaiting(tx, later)
        assert store.settle_awaiting(tx, found.message, AwaitingOutcome(outcome), later)

    assert read(store, observability.approval_counts) == Counts(0, 0, 0)
