"""有界標籤的指標(Phase 9 增量 2):[S630] 到 [S636]、[S638]、[S640] 到 [S647]、[S649]。

指標模組只讀:呼叫端傳時間窗(或「現在」),從生命週期事件、嘗試紀錄、DSP 呼叫紀錄與分析端紀錄即時
算出一份樣本清單。資料由測試直接寫列(見 rows.py),好讓時間剛好落在窗界上。
"""

import io
import math
import sqlite3
from datetime import timedelta

import pytest

from rtb.executor.attempt_store import DspCallKind, DspCallResult
from rtb.executor.inbox_store import BlockCode, InboxReads
from rtb.ops import metrics as m
from tests.ops.rows import TENANTS, Rows, at

W_START, W_END = at(0), at(minutes=60)


@pytest.fixture
def rows(tmp_path):
    built = Rows(tmp_path)
    yield built
    built.close()


def window(rows, since=W_START, until=W_END):
    return m.collect_window(since, until, executor_db=rows.executor_db,
                            analyzer_db=rows.analyzer_db, tenants=TENANTS)


def snapshot(rows, now):
    return m.collect_snapshot(now, executor_db=rows.executor_db, tenants=TENANTS)


def pick(report, name, **labels):
    return [s for s in report.samples
            if s.name == name and all(s.label_map().get(k) == v for k, v in labels.items())]


def one(report, name, **labels):
    found = pick(report, name, **labels)
    assert len(found) == 1, (name, labels, [(s.name, s.labels) for s in report.samples
                                            if s.name == name])
    return found[0]


def nearest_rank(values, percentile):
    ordered = sorted(values)
    return ordered[max(1, math.ceil(percentile / 100 * len(ordered))) - 1]


def dump(path):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t}").fetchall()  # noqa: S608 - 表名來自 sqlite_master
                for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def a_little_of_everything(rows):
    rows.task("t1", at(-1))
    rows.tool_call(at(0.5), "dsp:campaign")
    rows.event(at(1), "t1", "received", tenant=None)
    rows.event(at(2), "t1", "delivered", deliveries=1)
    rows.attempts("k-t1-1", [("in_flight", at(3)), ("verified", at(4))], task="t1")
    rows.dsp_call(at(3), "write", "responded", status=200)
    rows.event(at(5), "t1", "handed_off")


WINDOW_LIST = ("terminal_event_rate", "final_outcome_rate", "redelivery_rate", "dead_letter_rate",
               "queue_wait_seconds.p50", "approval_wait_seconds.p50",
               "dead_letter_wait_seconds.p50", "execution_seconds.p50",
               "reconciliation_seconds.p50", "reconciliation_success_rate",
               "end_to_end_seconds.p50", "end_to_end_unlinked", "end_to_end_clock_anomaly",
               "dsp_calls", "dsp_call_rate", "dsp_call_latency_ms.p50", "version_conflict_rate",
               "analyzer_calls", "analyzer_call_latency_ms.p50", "blocked", "awaiting_approval",
               "approval_released", "stale_rejections", "duplicates_prevented",
               "model_and_jev")
SNAPSHOT_LIST = ("queue_pending", "queue_oldest_wait_seconds", "unresolved_unknown",
                 "unresolved_unknown_max_age_seconds", "unresolved_committed_unverified",
                 "unresolved_committed_unverified_max_age_seconds", "unresolved_escalated",
                 "unresolved_escalated_max_age_seconds", "in_flight", "in_flight_max_age_seconds",
                 "locked_campaigns", "aggregate_utilization")


# ---- [S630] ----
def test_metrics_cover_the_handoff_list_without_writing(rows):
    a_little_of_everything(rows)
    before = (dump(rows.executor_db), dump(rows.analyzer_db))
    holders = [sqlite3.connect(path, isolation_level=None)
               for path in (rows.executor_db, rows.analyzer_db)]
    for holder in holders:  # 別人佔著兩邊的寫入鎖:指標照樣讀得到(不取寫入鎖)
        holder.execute("BEGIN IMMEDIATE")
    try:
        report = window(rows)
        now = snapshot(rows, at(minutes=30))
    finally:
        for holder in holders:
            holder.execute("ROLLBACK")
            holder.close()

    assert (dump(rows.executor_db), dump(rows.analyzer_db)) == before
    assert set(WINDOW_LIST) <= {s.name for s in report.samples}
    assert set(SNAPSHOT_LIST) <= {s.name for s in now.samples}
    jev = one(report, "model_and_jev")
    assert jev.status is m.Status.NOT_APPLICABLE and jev.value is None
    assert "確定性" in jev.note  # 附理由:分析是確定性計算,沒有呼叫模型


# ---- [S631] ----
def test_metric_labels_and_values_are_bounded(rows):
    a_little_of_everything(rows)
    rows.event(at(6), "t2", "blocked", policy="demo-pacing-v0", reason="version_changed")
    rows.event(at(7), "t3", "handed_off", campaign="c9", tenant=None)  # 廣告不屬於任何租戶
    rows.event(at(8), "t4", "blocked", reason="made_up_reason")
    for n in range(22):  # 22 種程式版本,時間依序:最早的兩種要併成其他
        rows.dsp_call(at(10 + n), "write", "responded", status=200, version=f"v{n:02d}")
    rows.tool_call(at(9), "legacy:whatever")
    report = window(rows)

    domains = {
        m.LabelKind.TENANT: {"acme", "beta", m.UNKNOWN_TENANT},
        m.LabelKind.STAGE: {s.value for s in m.Stage},
        m.LabelKind.POLICY_VERSION: {m.CURRENT, m.OTHER},
        m.LabelKind.TERMINAL_KIND: {"handed_off", "blocked", "expired", "dead_lettered"},
        m.LabelKind.BLOCK_REASON: {c.value for c in BlockCode} | {m.OTHER},
        m.LabelKind.DSP_CALL_KIND: {k.value for k in DspCallKind} | {m.OTHER},
        m.LabelKind.DSP_CALL_RESULT: {r.value for r in DspCallResult} | {m.OTHER},
        m.LabelKind.ANALYZER_ENDPOINT: {"dsp:campaign", "dsp:metrics", "dsp:evidence",
                                        "dsp:operation", "inbox:submit", m.OTHER},
    }
    versions = set()
    for sample in report.samples:
        kinds = [kind for kind, _ in sample.labels]
        assert len(kinds) == len(set(kinds)), sample
        assert set(kinds) <= m.DECLARED[sample.base], sample
        for kind, value in sample.labels:
            if kind is m.LabelKind.PROGRAM_VERSION:
                versions.add(value)
            else:
                assert value in domains[kind], sample
    # 窗內最後出現時間排前 20 的留下,其餘(0.9.1 最後出現在第 5 秒、v00、v01)併成其他
    assert versions == {f"v{n:02d}" for n in range(2, 22)} | {m.OTHER}
    assert m.MAX_PROGRAM_VERSIONS == 20
    assert one(report, "blocked", block_reason=m.OTHER).numerator == 1
    assert one(report, "terminal_event_rate", policy_version=m.OTHER,
               block_reason="version_changed").numerator == 1
    assert pick(report, "terminal_event_rate", tenant=m.UNKNOWN_TENANT)


# ---- [S632] ----
def outcome_story(rows):
    """窗一 [0, 60 分),窗二 [60, 120 分)。"""
    rows.event(at(minutes=10), "t1", "dead_lettered", reason="delivery_limit")
    rows.event(at(minutes=70), "t1", "replay_requeued", source="admin_command")
    rows.event(at(minutes=75), "t1", "handed_off")
    rows.event(at(minutes=20), "t2", "blocked", digest="hA", reason="version_changed")
    rows.event(at(minutes=25), "t2", "handed_off", digest="hB")  # 同任務同修訂、內容不同
    rows.event(at(minutes=30), "t3", "blocked", reason="aggregate_limit_reached")
    rows.event(at(minutes=40), "t4", "expired")
    rows.event(at(minutes=50), "t6", "dead_lettered", reason="delivery_limit")
    rows.event(at(minutes=52), "t6", "replay_requeued", source="admin_command")
    rows.event(at(minutes=55), "t6", "handed_off")


def test_outcome_rates_count_each_task_once_by_its_final_result(rows):
    outcome_story(rows)
    first = window(rows)
    second = window(rows, at(minutes=60), at(minutes=120))

    finals = pick(first, "final_outcome_rate", tenant="acme")
    assert sum(s.numerator for s in finals) == 5  # t1 最後結果在窗二,不算進窗一
    assert {s.count for s in finals} == {5}
    assert one(first, "final_outcome_rate", terminal_kind="handed_off").numerator == 2
    assert one(first, "final_outcome_rate", terminal_kind="blocked",
               block_reason="version_changed").numerator == 1
    assert one(first, "final_outcome_rate", terminal_kind="expired").value == pytest.approx(0.2)
    assert not pick(first, "final_outcome_rate", terminal_kind="dead_lettered")
    assert one(second, "final_outcome_rate", terminal_kind="handed_off").numerator == 1

    events = pick(first, "terminal_event_rate", tenant="acme")
    assert sum(s.numerator for s in events) == 7  # 事件型:死信後重放再到終點貢獻兩個事件
    blocked_total = sum(s.numerator for s in events if s.label_map()["terminal_kind"] == "blocked")
    assert blocked_total == sum(s.numerator for s in pick(first, "blocked")) == 2

    rows.event(at(minutes=80), "t2", "handed_off", digest="hA")  # 窗一過去之後又有新事件
    again = window(rows)
    assert pick(again, "terminal_event_rate") == pick(first, "terminal_event_rate")


# ---- [S633] ----
def test_latency_percentiles_match_a_direct_sort_and_survive_empty_samples(rows):
    waits = [5, 1, 9, 3, 7, 2, 8]
    for n, wait in enumerate(waits):
        rows.event(at(minutes=1), f"q{n}", "received", tenant=None)
        rows.event(at(minutes=1, seconds=wait), f"q{n}", "delivered", deliveries=1)
    report = window(rows)

    for stat, percentile in (("p50", 50), ("p95", 95), ("p99", 99), ("max", 100)):
        sample = one(report, f"queue_wait_seconds.{stat}", tenant="acme")
        assert sample.value == pytest.approx(nearest_rank(waits, percentile))
        assert sample.count == len(waits)
    tens = [4, 10, 1, 7, 3, 9, 2, 8, 6, 5]  # 十筆(租戶 beta):最近排名跟「無條件捨去」在這裡分得開
    for n, wait in enumerate(tens):
        rows.event(at(minutes=2), f"b{n}", "received", tenant=None, campaign="c3")
        rows.event(at(minutes=2, seconds=wait), f"b{n}", "delivered", deliveries=1, campaign="c3")
    report = window(rows)
    for stat, percentile in (("p50", 50), ("p95", 95), ("p99", 99), ("max", 100)):
        sample = one(report, f"queue_wait_seconds.{stat}", tenant="beta")
        assert sample.value == pytest.approx(nearest_rank(tens, percentile)), stat
    assert one(report, "queue_wait_seconds.p50", tenant="beta").value == 5
    for stat in ("p50", "p95", "p99", "max"):  # 沒有任何核可等待:無樣本,不丟例外
        empty = one(report, f"approval_wait_seconds.{stat}")
        assert empty.status is m.Status.NO_SAMPLES and empty.value is None and empty.count == 0


# ---- [S634] ----
def test_dsp_call_rates_split_timeouts_from_server_errors(rows):
    rows.dsp_call(at(1), "write", "responded", status=200)
    rows.dsp_call(at(2), "write", "responded", status=200)
    rows.dsp_call(at(3), "write", "timeout")
    rows.dsp_call(at(4), "write", "server_error", status=503)
    rows.dsp_call(at(5), "read_campaign", "responded", status=200)
    rows.dsp_call(at(6), "read_campaign", "responded", status=200)
    rows.dsp_call(at(7), "lookup_operation", "timeout")
    report = window(rows)

    assert sum(s.value for s in pick(report, "dsp_calls")) == 7
    assert one(report, "dsp_calls", dsp_call_kind="write", dsp_call_result="timeout").value == 1
    assert one(report, "dsp_calls", dsp_call_kind="write",
               dsp_call_result="server_error").value == 1
    rate = {s.label_map()["dsp_call_result"]: s for s in pick(report, "dsp_call_rate",
                                                              dsp_call_kind="write")}
    assert {k: s.value for k, s in rate.items()} == {"responded": 0.5, "timeout": 0.25,
                                                     "server_error": 0.25}
    assert {s.count for s in rate.values()} == {4}  # 分母是同一呼叫類別的窗內呼叫數
    assert one(report, "dsp_call_rate", dsp_call_kind="lookup_operation",
               dsp_call_result="timeout").value == 1.0


# ---- [S635] ----
def test_a_metrics_window_over_a_day_is_refused(rows):
    with pytest.raises(m.WindowTooLong):
        window(rows, at(0), at(0) + timedelta(hours=24, microseconds=1))
    with pytest.raises(m.WindowTooLong):
        window(rows, at(1), at(0))  # 迄在起之前
    assert window(rows, at(0), at(0) + timedelta(hours=24)).samples

    err = io.StringIO()
    code = m.run(["--executor-db", str(rows.executor_db), "--analyzer-db", str(rows.analyzer_db),
                  "--tenants-config", "unused.json", "--since", at(0).isoformat(),
                  "--until", (at(0) + timedelta(hours=25)).isoformat()],
                 out=io.StringIO(), err=err)
    assert code == m.EXIT_WINDOW_TOO_LONG and "24 小時" in err.getvalue()


# ---- [S636] ----
def test_metric_exemplars_are_real_members_of_the_sample(rows):
    # (收件, 取件) 秒:最慢的是 q0、q2、q4,取件時間最新的卻是 q4、q3、q2(兩種挑法分得開)
    for n, (received, delivered) in enumerate([(0, 60), (55, 65), (20, 70), (69, 71), (40, 72)]):
        rows.event(at(received), f"q{n}", "received", tenant=None)
        rows.event(at(delivered), f"q{n}", "delivered", deliveries=1)
    for n in range(5):  # 重投:分子 r0 到 r4,另有兩份沒重投
        rows.event(at(minutes=2 + n), f"r{n}", "delivered", deliveries=2)
    rows.event(at(minutes=10), "s0", "delivered", deliveries=1)
    rows.event(at(minutes=11), "s1", "delivered", deliveries=1)
    report = window(rows)

    slowest = one(report, "queue_wait_seconds.p95", tenant="acme")
    assert slowest.exemplars == ("q0", "q2", "q4")  # 最慢的三筆:60、50、32 秒
    redelivered = one(report, "redelivery_rate", tenant="acme")
    assert redelivered.numerator == 5
    assert redelivered.exemplars == ("r4", "r3", "r2")  # 分子裡時間最新的三筆
    for sample in report.samples:
        assert len(sample.exemplars) <= 3, sample
    for sample in pick(report, "redelivery_rate"):
        assert set(sample.exemplars) <= {f"r{n}" for n in range(5)}


# ---- [S638] ----
def test_duplicates_prevented_count_only_settlements_from_an_existing_key(rows):
    rows.event(at(1), "t1", "handed_off", from_existing=True)
    rows.event(at(2), "t2", "handed_off")
    rows.event(at(3), "t3", "blocked", reason="operation_previously_failed", from_existing=True)
    rows.event(at(4), "t4", "received", tenant=None)
    report = window(rows)

    duplicates = one(report, "duplicates_prevented", tenant="acme")
    assert duplicates.value == 2 and set(duplicates.exemplars) == {"t1", "t3"}


# ---- [S640] ----
def test_redelivery_rate_counts_reclaimed_proposals(rows):
    rows.event(at(minutes=-5), "t1", "delivered", deliveries=1)  # 首次取件在窗外
    rows.event(at(minutes=10), "t1", "reclaimed", deliveries=2)  # 接手在窗內
    rows.event(at(minutes=5), "t2", "delivered", deliveries=1)
    rows.event(at(minutes=5), "t3", "delivered", deliveries=1)
    rows.event(at(minutes=20), "t3", "delivered", deliveries=2)
    rows.event(at(minutes=-10), "t4", "delivered", deliveries=1)  # 整個在窗外
    report = window(rows)

    rate = one(report, "redelivery_rate", tenant="acme")
    assert (rate.numerator, rate.count) == (2, 3)
    assert set(rate.exemplars) == {"t1", "t3"}


# ---- [S641] ----
def test_end_to_end_latency_follows_the_follow_up_chain_once(rows):
    rows.task("r1", at(0))
    rows.follow_up("r1", "f1", at(minutes=5))
    rows.follow_up("f1", "f2", at(minutes=8), generation=2)
    rows.event(at(minutes=5), "r1", "blocked", reason="version_changed")  # 不是鏈尾
    rows.event(at(minutes=8), "f1", "blocked", reason="version_changed")
    rows.event(at(minutes=10), "f2", "expired")  # 鏈尾的舊修訂
    rows.event(at(minutes=12), "f2", "handed_off", revision=2)
    rows.task("s1", at(minutes=30))  # 兩邊時鐘不同步:結案比建立早
    rows.event(at(minutes=20), "s1", "handed_off")
    rows.event(at(minutes=15), "u1", "handed_off")  # 分析端沒有這個任務:接不上
    rows.task("v1", at(0))
    rows.event(at(minutes=20), "v1", "expired")  # 舊修訂在窗內結案,最新修訂在窗外:這條鏈不算
    rows.event(at(minutes=70), "v1", "handed_off", revision=2)
    report = window(rows)

    latency = one(report, "end_to_end_seconds.max")
    assert (latency.count, latency.value) == (1, 720)
    assert latency.exemplars == ("f2",)
    assert one(report, "end_to_end_clock_anomaly").value == 1
    assert one(report, "end_to_end_unlinked").value == 1
    assert report.stable


# ---- [S642] ----
def test_approval_wait_is_split_from_execution_latency(rows):
    stage = BlockCode.BUDGET_INCREASE_TOO_LARGE.value
    rows.event(at(minutes=0), "t1", "received", tenant=None)
    rows.event(at(minutes=1), "t1", "delivered", deliveries=1)
    rows.event(at(minutes=2), "t1", "awaiting_approval", reason=stage)
    rows.event(at(minutes=7), "t1", "approval_released", reason=stage)
    rows.event(at(minutes=8), "t1", "delivered", deliveries=1)
    rows.event(at(minutes=10), "t1", "handed_off")
    rows.event(at(minutes=2), "t2", "delivered", deliveries=1)
    rows.event(at(minutes=3), "t2", "awaiting_approval", reason=stage)
    rows.event(at(minutes=33), "t2", "blocked", reason=stage)  # 沒等到核可就到期
    report = window(rows)

    waits = one(report, "approval_wait_seconds.max", tenant="acme", block_reason=stage)
    assert (waits.count, waits.value) == (2, 1800)
    assert one(report, "approval_wait_seconds.p50", block_reason=stage).value == 300
    execution = one(report, "execution_seconds.max", tenant="acme")
    assert (execution.count, execution.value) == (2, 240)  # 9 分鐘扣掉 5 分鐘核可等待
    assert one(report, "execution_seconds.p50", tenant="acme").value == 60


# ---- [S643] ----
def test_snapshots_include_old_stuck_items_and_exclude_in_flight_from_unknown(rows):
    now = at(minutes=600)
    rows.attempts("k1", [("in_flight", at(minutes=-2 * 24 * 60))], campaign="c1")  # 兩天前卡住
    rows.attempts("k2", [("in_flight", at(minutes=1)), ("unknown", at(minutes=2)),
                         ("unknown", at(minutes=50))], campaign="c1")
    rows.attempts("k3", [("in_flight", at(minutes=3)), ("committed_unverified", at(minutes=5))],
                  campaign="c2")
    rows.attempts("k4", [("in_flight", at(minutes=4)), ("escalated", at(minutes=6))],
                  campaign="c3")
    rows.attempts("k5", [("in_flight", at(minutes=4)), ("verified", at(minutes=6))],
                  campaign="c2")
    rows.pending("p1", at(minutes=-3 * 24 * 60))
    rows.pending("p2", at(minutes=500))
    rows.pending("p3", at(minutes=1), disposition="in_progress")
    report = snapshot(rows, now)

    def value(name):
        return one(report, name).value

    assert value("in_flight") == 1 and value("unresolved_unknown") == 1  # 嘗試中不算結果不明
    assert value("unresolved_committed_unverified") == 1 and value("unresolved_escalated") == 1
    assert value("in_flight_max_age_seconds") == 2 * 24 * 3600 + 600 * 60
    assert value("unresolved_unknown_max_age_seconds") == (now - at(minutes=2)).total_seconds()
    assert value("locked_campaigns") == 3
    assert value("queue_pending") == 2
    assert value("queue_oldest_wait_seconds") == 3 * 24 * 3600 + 600 * 60
    assert {s.label_map()["tenant"] for s in pick(report, "aggregate_utilization")} == {
        "acme", "beta"}


# ---- [S644] ----
def test_reconciliation_time_starts_at_the_first_unknown(rows):
    rows.attempts("kr", [("in_flight", at(minutes=0)), ("unknown", at(minutes=1)),
                         ("in_flight", at(minutes=3)), ("unknown", at(minutes=4)),
                         ("committed_unverified", at(minutes=9)), ("verified", at(minutes=10))],
                  task="tr")
    rows.attempts("ks", [("in_flight", at(minutes=0)), ("unknown", at(minutes=2)),
                         ("failed", at(minutes=5))], task="ts")
    rows.attempts("kt", [("in_flight", at(minutes=0)), ("verified", at(minutes=1))], task="tt")
    report = window(rows)

    longest = one(report, "reconciliation_seconds.max", tenant="acme")
    assert (longest.count, longest.value) == (2, 540)  # 從第一次結果不明(1 分)算到結案(10 分)
    assert longest.exemplars == ("tr", "ts")
    success = one(report, "reconciliation_success_rate", tenant="acme")
    assert (success.numerator, success.count) == (1, 2)


# ---- [S645] ----
def test_version_conflict_rate_is_windowed(rows):
    rows.dsp_call(at(minutes=-5), "write", "client_error", status=409, error="version_conflict")
    rows.dsp_call(at(minutes=10), "write", "responded", status=200)
    rows.dsp_call(at(minutes=15), "void", "client_error", status=409, error="version_conflict")
    rows.dsp_call(at(minutes=20), "write", "client_error", status=409, error="version_conflict")
    rows.dsp_call(at(minutes=30), "write", "client_error", status=409, error="version_conflict")
    rows.dsp_call(at(minutes=70), "write", "client_error", status=409, error="version_conflict")
    report = window(rows)

    rate = one(report, "version_conflict_rate", tenant="acme")
    assert (rate.numerator, rate.count) == (2, 3)
    assert all(s.value <= 1 for s in pick(report, "version_conflict_rate"))


# ---- [S646] ----
def test_dead_letter_rate_counts_replayed_dead_letters(rows):
    rows.event(at(minutes=10), "t1", "dead_lettered", reason="delivery_limit")
    rows.event(at(minutes=12), "t1", "replay_requeued", source="admin_command")
    rows.event(at(minutes=20), "t1", "handed_off")
    rows.event(at(minutes=15), "t2", "handed_off")
    rows.event(at(minutes=16), "t3", "blocked", reason="version_changed")
    report = window(rows)

    rate = one(report, "dead_letter_rate", tenant="acme")
    assert (rate.numerator, rate.count) == (1, 3)  # 重放後成功,死信照樣算
    assert rate.exemplars == ("t1",)


# ---- [S647] ----
def test_zero_denominators_and_latency_attribution_are_uniform(rows):
    rows.event(at(minutes=-10), "t1", "received", tenant=None)
    rows.event(at(minutes=5), "t1", "delivered", deliveries=1)  # 開始在窗外、結束在窗內
    rows.event(at(minutes=50), "t2", "received", tenant=None)
    rows.event(at(minutes=70), "t2", "delivered", deliveries=1)  # 結束在下一窗
    rows.event(at(minutes=55), "t3", "received", tenant=None)  # 還沒取件:不算進任何窗
    first = window(rows)
    second = window(rows, at(minutes=60), at(minutes=120))

    assert one(first, "queue_wait_seconds.max", tenant="acme").value == 15 * 60
    assert one(first, "queue_wait_seconds.max", tenant="acme").count == 1
    assert one(second, "queue_wait_seconds.max", tenant="acme").value == 20 * 60
    for name in ("final_outcome_rate", "dead_letter_rate", "dsp_call_rate",
                 "version_conflict_rate", "reconciliation_success_rate", "terminal_event_rate"):
        empty = one(first, name)
        assert empty.status is m.Status.NO_SAMPLES and empty.value is None, name


# ---- [S649] ----
def test_stale_rejections_dead_letter_wait_and_bounded_final_result_lookup(rows, monkeypatch):
    for n, reason in enumerate(("version_changed", "policy_version_changed", "decision_stale",
                                "over_budget_cap")):
        rows.event(at(minutes=40 + n), f"b{n}", "blocked", reason=reason)
    rows.event(at(minutes=0), "t1", "received", tenant=None)
    rows.event(at(minutes=1), "t1", "delivered", deliveries=1)
    rows.event(at(minutes=2), "t1", "dead_lettered", reason="delivery_limit")
    rows.event(at(minutes=32), "t1", "replay_requeued", source="admin_command")
    rows.event(at(minutes=33), "t1", "delivered", deliveries=1)
    rows.event(at(minutes=35), "t1", "handed_off")
    for n in range(30):  # 窗外的舊提案:不該被逐一查最後終點
        rows.event(at(minutes=-120), f"old{n}", "handed_off")
    calls = []
    original = InboxReads.last_terminal_event

    def counting(self, tx, task_id, revision, digest):
        calls.append(task_id)
        return original(self, tx, task_id, revision, digest)

    monkeypatch.setattr(InboxReads, "last_terminal_event", counting)
    report = window(rows)

    stale = pick(report, "stale_rejections", tenant="acme")
    assert {s.label_map()["block_reason"]: s.value for s in stale} == {
        "version_changed": 1, "policy_version_changed": 1, "decision_stale": 1}
    wait = one(report, "dead_letter_wait_seconds.max", tenant="acme")
    assert (wait.count, wait.value) == (1, 1800)
    assert one(report, "execution_seconds.max", tenant="acme").value == 240  # 34 分鐘扣 30 分鐘
    assert not any(task.startswith("old") for task in calls)
    assert len(calls) == 5 * report.rounds  # 窗內候選 5 份,每輪各查一次


def test_the_command_line_reports_fixed_exit_codes(rows, tmp_path):
    config = tmp_path / "tenants.json"
    config.write_text('{"tenants": {"acme": {"campaigns": ["c1"], "max_budget": 100}}}')
    config.chmod(0o600)
    base = ["--executor-db", str(rows.executor_db), "--analyzer-db", str(rows.analyzer_db),
            "--tenants-config", str(config)]
    out = io.StringIO()
    assert m.run([*base, "--since", at(0).isoformat(), "--until", at(minutes=60).isoformat()],
                 out=out, err=io.StringIO()) == m.EXIT_OK
    assert '"samples"' in out.getvalue()
    assert m.run([*base, "--now", at(0).isoformat()], out=io.StringIO(),
                 err=io.StringIO()) == m.EXIT_OK
    err = io.StringIO()
    missing = ["--executor-db", str(tmp_path / "nope.db"), "--analyzer-db", str(rows.analyzer_db),
               "--tenants-config", str(config), "--now", at(0).isoformat()]
    assert m.run(missing, out=io.StringIO(), err=err) == m.EXIT_NO_DATABASE
