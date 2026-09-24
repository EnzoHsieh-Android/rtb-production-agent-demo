"""總曝險預留(Phase 6 增量 1,事故 F7):對應計劃的 S330 到 S343。

額度從嘗試紀錄推:沒有終點的一直算、已驗證的看驗證完成時間 24 小時內、失敗的不算;舊列(沒有
租戶與金額)以新預算全額算進每一個租戶。並行測試照執行迴圈筆記的兩條 PITFALL 組:停點與柵欄都在
交易外準備好,不在交易裡互等。
"""

import json
import sqlite3
import threading
import time
from datetime import timedelta

import pytest

from rtb.domain.attempt import AttemptState, OutcomeCode, operation_key
from rtb.domain.proposal import MAX_INT, content_hash
from rtb.executor import attempt_store, inbox_server
from rtb.executor.attempt_store import AGGREGATE_WINDOW, Reservation
from rtb.executor.execution import CampaignView, Executor, ExecutorHalted, Result, WriteAnswer
from rtb.executor.inbox_store import BlockCode, InboxStore
from tests.executor.conftest import NOW
from tests.executor.fakes import LOOSE_AGGREGATE_LIMIT, Harness, proposal, write_config

A = AttemptState
C = OutcomeCode
EXPIRES = NOW + timedelta(minutes=2)
TENANT = "t-default"


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def limit(h, value, **kwargs):
    write_config(h.config, aggregate_limit=value, **kwargs)


def first_row(h, prop):
    return h.query("SELECT tenant, reserved_amount FROM attempts WHERE key = ? AND seq = 1",
                   (operation_key(prop),))


def stops(h):
    return h.query("SELECT kind, task_id, revision, key, tenant, campaign_id, amount, used, cap, "
                   "capped FROM write_stops ORDER BY id")


def stop_identity(h):
    return h.query("SELECT content_hash, at FROM write_stops ORDER BY id")


def used(store, now, tenant=TENANT):
    with store.transaction() as tx:
        return attempt_store.aggregate_used(tx, tenant, now)


def begin(store, prop, now=NOW, reservation=None):
    with store.transaction() as tx:
        return attempt_store.begin(tx, prop, now, capability_expires_at=EXPIRES,
                                   reservation=reservation).row


def drive(store, row, path, now=NOW):
    """照 [(目標狀態, 代碼)] 一步步轉換;轉進已提交待驗證帶寫入後版本、轉回嘗試中帶憑證到期。"""
    for target, code in path:
        extra = {"written_version": 4} if target is A.COMMITTED_UNVERIFIED else {}
        if target is A.IN_FLIGHT:
            extra = {"capability_expires_at": EXPIRES}
        with store.transaction() as tx:
            row = attempt_store.transition(tx, row.key, row.seq, target, now, code=code, **extra)
    return row


PATHS = {
    A.IN_FLIGHT: [],
    A.UNKNOWN: [(A.UNKNOWN, None)],
    A.COMMITTED_UNVERIFIED: [(A.COMMITTED_UNVERIFIED, None)],
    A.ESCALATED: [(A.ESCALATED, C.IDEMPOTENCY_CONFLICT)],
}
VERIFY = [(A.COMMITTED_UNVERIFIED, None), (A.VERIFIED, None)]
FAIL = [(A.FAILED, C.VERSION_CONFLICT)]


# ---- [S330] ----
def test_a_budget_increase_reserves_its_amount_with_the_first_attempt(h):
    limit(h, 1000)
    prop = h.submit()  # 100 -> 150

    assert h.process().kind is Result.EXECUTED
    assert first_row(h, prop) == [(TENANT, 50)]
    assert stops(h) == []


def expire_and_settle(h):
    """撥時鐘過提案到期,跑一次處理待核可:沒有核可的待核可確認成已擋下(增量 3 改寫)。"""
    h.clock.advance(hours=1, seconds=1)
    return h.executor().process_awaiting()


# ---- [S331](增量 3 改寫:沒有有效核可時先進待核可,到期才確認成已擋下) ----
@pytest.mark.parametrize(("cap", "second"),
                         [(99, Result.AWAITING_APPROVAL), (100, Result.EXECUTED)])
def test_the_aggregate_limit_blocks_at_the_threshold_and_allows_exactly_reaching_it(
        h, cap, second):
    limit(h, cap)
    h.submit()
    other = h.submit(task_id="t2", campaign_id="c2")
    assert h.process().kind is Result.EXECUTED

    result = h.process()

    assert result.kind is second
    if second is Result.AWAITING_APPROVAL:
        assert result.block_code is BlockCode.AGGREGATE_LIMIT_REACHED
        assert h.proposals()[1][3:] == ("awaiting_approval", "aggregate_limit_reached")
        assert first_row(h, other) == []  # 不開嘗試
        assert len(h.dsp.writes) == 1  # 不呼叫 DSP
        assert expire_and_settle(h) == 1
        assert h.proposals()[1][3:] == ("blocked", "aggregate_limit_reached")


def test_a_new_task_passes_once_the_budget_is_released(h):
    """事故 F7 的「分批」只做到這一步:額度釋放後,一份全新的任務能再通過(沒有自動排程,
    被擋的那份不會自己重來)。兩種釋放:已驗證的出了 24 小時窗口、未結案的判成失敗。"""
    limit(h, 50)
    h.submit()  # 加 50:剛好用滿
    assert h.process().kind is Result.EXECUTED
    h.submit(task_id="t2", campaign_id="c2")
    assert h.process().kind is Result.AWAITING_APPROVAL  # 滿了:停下

    h.clock.advance(hours=24, seconds=1)  # 已驗證那筆出窗
    h.store.accept(fresh(h, task_id="t3", campaign_id="c3"), h.clock)
    assert h.process().kind is Result.EXECUTED  # 全新的任務通過

    h.clock.advance(hours=24, seconds=1)  # t3 出窗
    h.dsp.answers.append(WriteAnswer(422, "idempotency_conflict"))  # 轉人工:佔住 50
    held = fresh(h, task_id="t4", campaign_id="c1", campaign_version_observed=4,
                 requested_change={"new_budget": 200})  # c1 已被寫成 150、版本 4
    h.store.accept(held, h.clock)
    assert h.process().kind is Result.EXECUTED
    h.store.accept(fresh(h, task_id="t5", campaign_id="c2"), h.clock)
    assert h.process().kind is Result.AWAITING_APPROVAL  # 轉人工那筆一直算
    with h.store.transaction() as tx:  # 人工判成失敗:還回去
        key = operation_key(held)
        seq = attempt_store.latest(tx, key).seq
        attempt_store.resolve(tx, key, seq, A.FAILED, "operator decided", h.clock())
    h.store.accept(fresh(h, task_id="t6", campaign_id="c3", campaign_version_observed=4,
                         requested_change={"new_budget": 200}), h.clock)
    assert h.process().kind is Result.EXECUTED


def fresh(h, **overrides):
    now = h.clock()
    return proposal(decision_created_at=now.isoformat(),
                    decision_expires_at=(now + timedelta(minutes=10)).isoformat(), **overrides)


# ---- [S332] ----
@pytest.mark.parametrize("state", list(PATHS))
def test_an_unresolved_reservation_counts_no_matter_how_old(store_only, state):
    row = begin(store_only, proposal(), reservation=Reservation(TENANT, 50, 1000))
    row = drive(store_only, row, PATHS[state])

    assert used(store_only, NOW + timedelta(days=30)) == 50  # 一個月後還算

    if state in (A.IN_FLIGHT, A.UNKNOWN):  # 這兩個狀態能直接轉失敗
        drive(store_only, row, FAIL)
        assert used(store_only, NOW) == 0  # 確定失敗就還回去


def test_a_voided_and_failed_reservation_gives_the_amount_back(store_only):
    row = begin(store_only, proposal(), reservation=Reservation(TENANT, 50, 1000))
    drive(store_only, row, [(A.FAILED, C.NOT_HAPPENED)])  # 作廢成功後判失敗用的代碼

    assert used(store_only, NOW) == 0


@pytest.fixture
def store_only(tmp_path):
    store = InboxStore(tmp_path / "executor.db")
    yield store
    store.close()


# ---- [S333] ----
def test_a_verified_reservation_leaves_the_window_24_hours_after_verification(store_only):
    row = begin(store_only, proposal(), reservation=Reservation(TENANT, 50, 1000))
    verified_at = NOW + timedelta(hours=30)  # 開始一筆超過 24 小時才驗證成功
    drive(store_only, row, VERIFY, now=verified_at)

    assert used(store_only, verified_at + timedelta(minutes=1)) == 50
    assert used(store_only, verified_at + AGGREGATE_WINDOW - timedelta(seconds=1)) == 50
    assert used(store_only, verified_at + AGGREGATE_WINDOW) == 50  # 剛好滿 24 小時還不算「超過」
    assert used(store_only, verified_at + AGGREGATE_WINDOW + timedelta(seconds=1)) == 0


def test_another_tenants_reservation_is_not_counted(store_only):
    begin(store_only, proposal(), reservation=Reservation("someone-else", 50, 1000))

    assert used(store_only, NOW) == 0


# ---- [S334] ----
@pytest.mark.parametrize("change", [
    {"requested_change": {"new_budget": 80}},  # 減預算
    {"action_type": "pause_campaign", "requested_change": {}}])
def test_decreases_and_pauses_do_not_use_the_aggregate_budget(h, change):
    limit(h, 0)  # 門檻 0:任何加預算都會被擋

    prop = h.submit(**change)

    assert h.process().kind is Result.EXECUTED
    assert first_row(h, prop) == [(TENANT, 0)]


# ---- [S335] ----
def test_an_existing_key_is_not_reserved_twice(h):
    limit(h, 50)  # 剛好夠一次
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(None))  # 結果不明:鍵留著、預留一直算
    assert h.process().kind is Result.EXECUTED
    h.store.accept(proposal(revision=2, decision_expires_at="2026-09-22T12:40:00+00:00"),
                   h.clock)  # 同鍵新修訂(內容只換到期時間)

    h.process()

    rows = h.query("SELECT count(*) FROM attempts WHERE key = ? AND seq = 1",
                   (operation_key(first),))
    assert rows == [(1,)]
    assert [s[0] for s in stops(h)] == []  # 沒有因為「再扣一次會超過」而被擋


# ---- [S336] ----
def _race_two_workers(h, monkeypatch):
    """兩個工作者都先取完件、在開始一筆的入口會合;第一個進交易算完額度後停住,第二個才去開始一筆。
    取件也要寫入鎖,不先會合的話第二個在取件就卡住,永遠測不到開始一筆那道互斥。"""
    second_started = threading.Event()
    local = threading.local()
    real_used, real_take = attempt_store.aggregate_used, Executor._take
    both_picked = threading.Barrier(2, timeout=10)

    def slow_used(tx, tenant, now, **kw):
        value = real_used(tx, tenant, now, **kw)
        if getattr(local, "first", False):
            assert second_started.wait(10)
            time.sleep(0.3)  # 讓第二個一定已經在等鎖(或在錯誤實作下已經讀完舊額度)
        return value

    def take(self, *args, **kwargs):
        both_picked.wait()
        if not getattr(local, "first", False):
            time.sleep(0.1)  # 讓第一個先拿到寫入鎖、停在算完額度之後
            second_started.set()
        return real_take(self, *args, **kwargs)

    monkeypatch.setattr(attempt_store, "aggregate_used", slow_used)
    monkeypatch.setattr(Executor, "_take", take)
    results = {}

    def run(name, first):
        local.first = first
        store = InboxStore(h.db)
        try:
            results[name] = Executor(store, h.dsp, h.signer, h.config, h.clock, name).process_one()
        finally:
            store.close()

    threads = [threading.Thread(target=run, args=(n, n == "a")) for n in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    return results


def test_two_workers_cannot_race_past_the_aggregate_limit(h, monkeypatch):
    limit(h, 99)
    h.submit()
    h.submit(task_id="t2", campaign_id="c2")

    results = _race_two_workers(h, monkeypatch)

    kinds = sorted(r.kind.value for r in results.values())
    assert kinds == sorted([Result.EXECUTED.value, Result.AWAITING_APPROVAL.value])
    assert len(h.dsp.writes) == 1
    assert used(h.store, h.clock()) <= 99


# ---- [S337] ----
def test_a_missing_aggregate_limit_blocks_only_that_tenant_and_lowering_it_applies_from_the_next_signing(  # noqa: E501
        h):
    h.config.write_text(json.dumps({"tenants": {
        "no-limit": {"campaigns": ["c1"], "max_budget": 1000},
        "has-limit": {"campaigns": ["c2", "c3"], "max_budget": 1000, "aggregate_limit": 1000},
    }}), encoding="utf-8")
    h.submit()
    h.submit(task_id="t2", campaign_id="c2")

    assert h.process().kind is Result.AWAITING_APPROVAL  # 缺欄當 0:只擋 no-limit
    assert h.process().kind is Result.EXECUTED  # 另一個租戶照常
    assert stops(h)[0][0] == "aggregate_limit_reached" and stops(h)[0][4] == "no-limit"

    h.config.write_text(json.dumps({"tenants": {
        "has-limit": {"campaigns": ["c2", "c3"], "max_budget": 1000, "aggregate_limit": 60},
        "no-limit": {"campaigns": ["c1"], "max_budget": 1000}}}), encoding="utf-8")
    h.submit(task_id="t3", campaign_id="c3")
    assert h.process().kind is Result.AWAITING_APPROVAL  # 調降後下一次簽發起生效:50 + 50 > 60

    for broken in (-1, "100", 1.5, MAX_INT + 1, True):
        h.config.write_text(json.dumps({"tenants": {"x": {
            "campaigns": ["c1"], "max_budget": 1000, "aggregate_limit": broken}}}),
            encoding="utf-8")
        h.submit(task_id=f"t-{broken!s}".replace(".", "-").replace("+", "p"))
        with pytest.raises(ExecutorHalted):
            h.process()


def test_an_already_reserved_key_is_not_revoked_by_lowering_the_limit(h):
    limit(h, 1000)
    prop = h.submit()
    h.dsp.answers.append(WriteAnswer(None))  # 結果不明,鍵留著
    h.process()
    limit(h, 0)
    key = operation_key(prop)
    before = [w for w in h.dsp.writes if w[1] == key]

    h.executor().reconcile_all()  # 對帳查不到就用同一把鍵重送

    after = [w for w in h.dsp.writes if w[1] == key]
    assert len(after) == len(before) + 1  # 對帳真的又送了一次,沒被調降收回
    assert stops(h) == []


# ---- [S338] ----
@pytest.mark.parametrize("missing", [["aggregate_limit_reached"],
                                     ["aggregate_limit_reached", "operation_previously_failed"],
                                     # 新值在、舊值缺:只看新值的寫法抓不到這一組
                                     ["operation_previously_failed"]])
def test_an_old_inbox_database_accepts_every_current_block_code(tmp_path, missing):
    db = tmp_path / "executor.db"
    InboxStore(db).close()
    conn = sqlite3.connect(db)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'proposals'").fetchone()[0]
    old_sql = sql
    for value in missing:
        old_sql = old_sql.replace(f", '{value}'", "").replace(f"'{value}', ", "")
    assert old_sql != sql
    conn.executescript(f"ALTER TABLE proposals RENAME TO p_old; {old_sql};"  # noqa: S608 - 測試模擬舊表
                       "INSERT INTO proposals SELECT * FROM p_old; DROP TABLE p_old;")
    conn.close()

    store = InboxStore(db)  # 重建判斷要逐一比對整個列舉
    try:
        sql = store._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'proposals'").fetchone()[0]
        assert all(f"'{code.value}'" in sql for code in BlockCode)
    finally:
        store.close()


# ---- [S339] ----
def test_the_aggregate_block_is_reported_as_not_permitted():
    assert inbox_server._answered_block_code("aggregate_limit_reached") == "not_permitted"


# ---- [S340] 見 test_f7_end_to_end.py ----


# ---- [S341] ----
def test_the_aggregate_query_is_no_slower_than_picking_and_never_overflows(store_only):
    rows = []
    old = NOW - timedelta(days=10)
    for i in range(300_000 - 3000 - 20):  # 窗外的舊歷史:已驗證的舊格式鍵
        rows += [(f"h{i}", 1, "cx", "in_flight", old), (f"h{i}", 2, "cx", "verified", old)]
    for i in range(3000):  # 窗內、舊格式(沒有租戶與金額,要從快照解出新預算)
        rows += [(f"w{i}", 1, "cy", "in_flight", NOW), (f"w{i}", 2, "cy", "verified", NOW)]
    for i in range(20):  # 沒有終點的舊鍵
        rows.append((f"u{i}", 1, f"cu{i}", "in_flight", old))
    snapshot = json.dumps({"requested_change": {"new_budget": 7}})
    with store_only.transaction() as tx:
        tx.conn.executemany(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, action, proposal_json) "
            "VALUES (?, ?, ?, ?, 1, 0, ?, 'update_budget', ?)",
            [(k, s, c, st, attempt_store._iso(t), snapshot) for k, s, c, st, t in rows])

    def timed(fn):
        best = float("inf")
        for _ in range(5):
            with store_only.transaction() as tx:
                started = time.perf_counter()
                result = fn(tx)
                best = min(best, time.perf_counter() - started)
        return best, result

    agg_time, total = timed(lambda tx: attempt_store.aggregate_used(tx, TENANT, NOW))
    pick_time, _ = timed(attempt_store.campaigns_with_unresolved)
    assert total == (3000 + 20) * 7  # 舊列算進每一個租戶
    assert agg_time <= 2 * pick_time + 0.005, (agg_time, pick_time)


# ---- [S341] 旁:F7 效能計劃代碼審第 3 輪,舊鍵偵測在 30 萬把舊鍵上的兩種形狀 ----
def _legacy_history(store, *, window_rows, window_tenant, stuck, writing):
    """30 萬把的歷史(比照 [S341]):窗外已結案的舊格式鍵為主;window_rows 筆窗內已驗證(window_tenant
    是空值時為舊格式);stuck 把沒有終點的舊鍵(跟已結案舊鍵同一個時間,排在它們後面);writing 把
    這個租戶正在寫的鍵。"""
    rows = []
    old = NOW - timedelta(days=10)
    for i in range(300_000 - window_rows - stuck - writing):
        rows += [(f"h{i}", 1, "cx", "in_flight", old, None, None),
                 (f"h{i}", 2, "cx", "verified", old, None, None)]
    for i in range(window_rows):
        amount = None if window_tenant is None else 7
        rows += [(f"w{i}", 1, "cy", "in_flight", NOW, window_tenant, amount),
                 (f"w{i}", 2, "cy", "verified", NOW, None, None)]
    for i in range(stuck):
        rows.append((f"u{i}", 1, f"cu{i}", "in_flight", old, None, None))
    for i in range(writing):
        rows.append((f"n{i}", 1, f"cn{i}", "in_flight", NOW, TENANT, 7))
    snapshot = json.dumps({"requested_change": {"new_budget": 7}})
    with store.transaction() as tx:
        tx.conn.executemany(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, action, proposal_json, tenant, reserved_amount) "
            "VALUES (?, ?, ?, ?, 1, 0, ?, 'update_budget', ?, ?, ?)",
            [(k, s, c, st, attempt_store._iso(t), snapshot, tenant, amount)
             for k, s, c, st, t, tenant, amount in rows])


def _best_of_five(store, fn, *, forget=False):
    best = float("inf")
    for _ in range(5):
        with store.transaction() as tx:
            if forget:  # 量第一次(還沒記住「沒有未結案舊鍵」)的成本
                tx.legacy_memo.no_open_legacy = False
            started = time.perf_counter()
            result = fn(tx)
            best = min(best, time.perf_counter() - started)
    return best, result


def test_all_legacy_keys_closed_with_one_write_in_flight_stays_fast(store_only):
    """升級之後最常見的狀態:舊鍵很多、全都結案了,這個租戶有一把正在寫。第一次(還沒記住)也要在
    [S341] 的預算內,而且不比原算法慢(代碼審第 3 輪:第二步沒閘門時是原算法的 1.8 倍、超出預算)。"""
    _legacy_history(store_only, window_rows=3000, window_tenant=TENANT, stuck=0, writing=1)
    pick, _ = _best_of_five(store_only, attempt_store.campaigns_with_unresolved)
    ref, expected = _best_of_five(
        store_only, lambda tx: attempt_store.aggregate_used_reference(tx, TENANT, NOW))
    first, total = _best_of_five(
        store_only, lambda tx: attempt_store.aggregate_used(tx, TENANT, NOW), forget=True)
    steady, again = _best_of_five(
        store_only, lambda tx: attempt_store.aggregate_used(tx, TENANT, NOW))
    assert total == again == expected == 3001 * 7
    assert first <= 2 * pick + 0.005, (first, pick)
    assert first <= ref * 1.1 + 0.002, (first, ref)
    assert steady <= ref * 1.1 + 0.002, (steady, ref)


def test_the_s341_history_two_days_later_stays_within_budget(store_only):
    """[S341] 同一份資料、現在往後挪 2 天(窗內那 3000 把舊格式已驗證鍵出了窗):20 把卡住的舊鍵跟
    已結案舊鍵同一個時間、排在它們後面。第 3 輪前的偵測要讀完 29.7 萬把才停,超出預算。"""
    _legacy_history(store_only, window_rows=3000, window_tenant=None, stuck=20, writing=0)
    later = NOW + timedelta(days=2)
    pick, _ = _best_of_five(store_only, attempt_store.campaigns_with_unresolved)
    agg, total = _best_of_five(
        store_only, lambda tx: attempt_store.aggregate_used(tx, TENANT, later), forget=True)
    assert total == 20 * 7  # 窗外的舊格式已驗證不再計入,卡住的 20 把照算
    assert agg <= 2 * pick + 0.005, (agg, pick)


def test_the_aggregate_sum_does_not_overflow(store_only):
    for i in range(2):
        row = begin(store_only, proposal(task_id=f"big{i}", campaign_id=f"c{i + 1}",
                                         requested_change={"new_budget": MAX_INT}))
        drive(store_only, row, VERIFY)

    assert used(store_only, NOW) == 2 * MAX_INT


# ---- [S342] ----
def test_every_stop_leaves_one_durable_record_per_proposal(h):
    limit(h, 10)
    prop = h.submit()

    assert h.process().kind is Result.AWAITING_APPROVAL
    assert stops(h) == [("aggregate_limit_reached", "t1", 1, operation_key(prop), TENANT, "c1",
                         50, 0, 10, 0)]
    assert stop_identity(h) == [(content_hash(prop), attempt_store._iso(h.clock()))]

    with h.store.transaction() as tx:  # 同一份提案再記一次:略過
        h.store.record_stop(tx, inbox_store_stop(prop, 50, 0, 10), h.clock())
    with h.store.transaction() as tx:  # 已用額度超過整數上限:封頂並標記,不丟例外
        h.store.record_stop(tx, inbox_store_stop(
            proposal(task_id="t9"), 1, 2 * MAX_INT, MAX_INT), h.clock())
    assert len(stops(h)) == 2
    assert stops(h)[1][7:] == (MAX_INT, MAX_INT, 1)


def test_a_full_table_deferral_is_recorded_once_per_proposal(h, monkeypatch):
    monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", 0)
    h.submit()

    assert h.process().kind is Result.DEFERRED
    h.clock.advance(seconds=61)  # 租約到期後重投
    h.process()

    assert [s[0] for s in stops(h)] == ["table_full"]
    assert stops(h)[0][7:9] == (0, LOOSE_AGGREGATE_LIMIT)  # 當時已用額度與門檻照樣記


def inbox_store_stop(prop, amount, used_value, cap):
    from rtb.executor.inbox_store import Stop, StopKind
    return Stop(StopKind.AGGREGATE_LIMIT_REACHED, prop, operation_key(prop), TENANT, amount,
                used_value, cap)


# ---- [S343] ----
def test_old_attempts_count_against_every_tenant_until_they_leave_the_window(tmp_path):
    db = tmp_path / "executor.db"
    store = InboxStore(db)
    unresolved = begin(store, proposal())  # 不帶預留 = 舊格式(沒有租戶與金額)
    pause = begin(store, proposal(task_id="p", campaign_id="c2", action_type="pause_campaign",
                                  requested_change={}))
    verified = drive(store, begin(store, proposal(task_id="v", campaign_id="c3",
                                                   requested_change={"new_budget": 70})), VERIFY)
    store.close()
    conn = sqlite3.connect(db)  # 模擬 Phase 6 之前的資料庫:拿掉兩個新欄位
    # Phase 6 之前的資料庫沒有按租戶的索引(增量 4 加的),先拿掉才拿得掉它參照的租戶欄
    conn.execute("DROP INDEX IF EXISTS attempts_first_rows_by_tenant")
    conn.execute("ALTER TABLE attempts DROP COLUMN tenant")
    conn.execute("ALTER TABLE attempts DROP COLUMN reserved_amount")
    conn.commit()
    conn.close()

    store = InboxStore(db)  # 開啟時補欄位、不回填
    try:
        assert store._conn.execute(
            "SELECT count(*) FROM attempts WHERE tenant IS NOT NULL "
            "OR reserved_amount IS NOT NULL").fetchone() == (0,)
        for tenant in ("a", "b"):  # 算進每一個租戶:改預算以新預算全額,暫停 0
            assert used(store, NOW, tenant) == 150 + 70
        assert used(store, NOW + AGGREGATE_WINDOW + timedelta(seconds=1), "a") == 150  # 已驗證出窗
        assert unresolved.key != pause.key != verified.key
    finally:
        store.close()


def test_a_campaign_view_is_used_as_the_base_of_the_reservation(h):
    limit(h, 1000)
    h.dsp.campaigns["c1"] = CampaignView(budget=120, status="active", version=3)
    prop = h.submit()

    h.process()

    assert first_row(h, prop) == [(TENANT, 30)]  # 150 - 120,不是 150 - 100


def test_an_unreadable_old_snapshot_halts_instead_of_crashing(h):
    """算額度時讀到舊格式、快照壞掉的列:比照對帳讀不回來的慣例乾淨停機,不讓例外往外炸。"""
    with h.store.transaction() as tx:
        tx.conn.execute(  # 測試模擬毀損:Phase 6 之前格式、快照不是 JSON、已驗證且在窗內
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, action, proposal_json) VALUES "
            "('old', 1, 'c9', 'in_flight', 1, 0, ?, 'update_budget', '{not json'), "
            "('old', 2, 'c9', 'verified', 1, 0, ?, 'update_budget', NULL)",
            (attempt_store._iso(h.clock()), attempt_store._iso(h.clock())))
    h.submit()

    with pytest.raises(ExecutorHalted):
        h.process()
    assert h.attempts() == [("old", 1, "in_flight", None, 1, None),
                            ("old", 2, "verified", None, 1, None)]


def test_a_lost_receipt_during_an_aggregate_block_writes_no_stop(h, monkeypatch):
    """收據失效代表這份提案已被別的工作者接手:由它處置、由它記;這邊整個交易回滾、不重複記。"""
    from rtb.executor.execution import LeaseLost

    limit(h, 10)
    h.submit()
    monkeypatch.setattr(InboxStore, "await_approval", lambda *_a, **_k: False)

    assert h.process().kind is Result.LEASE_LOST
    assert stops(h) == []
    assert LeaseLost  # 例外型別存在(process_one 接住後回 LEASE_LOST)


def test_an_unreadable_old_snapshot_halts_when_the_table_is_full_too(h, monkeypatch):
    monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", 0)
    with h.store.transaction() as tx:
        tx.conn.execute(  # 測試模擬毀損:舊格式、快照不是 JSON、沒有終點
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, action, proposal_json) VALUES "
            "('old', 1, 'c9', 'in_flight', 1, 0, ?, 'update_budget', '{not json')",
            (attempt_store._iso(h.clock()),))
    h.submit()

    with pytest.raises(ExecutorHalted):
        h.process()


def test_other_tenants_rows_are_filtered_in_the_database(store_only, monkeypatch):
    """額度查詢在全域寫入鎖裡:別的租戶的列要在資料庫裡就濾掉,不撈進程式逐列判斷(握鎖時間)。"""
    monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", 10**6)  # 這支要大量沒有終點的別家列
    for i in range(1000):
        row = begin(store_only, proposal(task_id=f"x{i}", campaign_id=f"cx{i}"),
                    reservation=Reservation("someone-else", 5, 10**9))
        if i % 2:
            drive(store_only, row, VERIFY)
    seen = []
    real = attempt_store._counted
    monkeypatch.setattr(attempt_store, "_counted",
                        lambda row, tenant: seen.append(row) or real(row, tenant))

    assert used(store_only, NOW) == 0
    assert seen == []
