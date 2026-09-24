"""有界標籤的指標(Phase 9 增量 2):[S630] 到 [S636]、[S638]、[S640] 到 [S647]、[S649]。

指標模組只讀:呼叫端傳時間窗(或「現在」),從生命週期事件、嘗試紀錄、DSP 呼叫紀錄與分析端紀錄即時
算出一份樣本清單。資料由測試直接寫列(見 rows.py),好讓時間剛好落在窗界上。
"""

import io
import math
import sqlite3
from datetime import timedelta, timezone

import pytest

from rtb.domain import proposal as proposal_module
from rtb.domain.proposal import KNOWN_POLICY_VERSIONS, POLICY_VERSION
from rtb.executor import attempt_store
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
    # Phase 11B 增量 1 起模型與 Jev 指標改從花費帳算(計劃 [[Projects/RTB_Phase11B大模型接入_計劃]]
    # 〈花費帳與上限〉,[S907] 另測有帳的情況);沒給花費帳就是無樣本、附原因,不再標不適用
    assert jev.status is m.Status.NO_SAMPLES and jev.value is None
    assert "花費帳" in jev.note
    for name in ("blocked", "awaiting_approval", "approval_released", "stale_rejections"):
        # 事件次數,跟可觀測查詢停下紀錄的提案數定義不同(代碼審第 1 輪),樣本上寫明
        assert "事件次數" in one(report, name).note, name


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
        m.LabelKind.POLICY_VERSION: {*KNOWN_POLICY_VERSIONS, m.OTHER},
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
    assert POLICY_VERSION in KNOWN_POLICY_VERSIONS  # 改版時新版本要加進清單
    assert {"demo-pacing-v1"} <= set(KNOWN_POLICY_VERSIONS)  # 用過的版本只增不刪


# ---- [S631] 代碼審第 1 輪:程式版本值域只收窗內真的貢獻樣本的紀錄 ----
def test_program_versions_come_only_from_records_in_the_window(rows):
    for n in range(21):  # 窗內 21 種:最早的 v00 併成其他
        rows.dsp_call(at(10 + n), "write", "responded", status=200, version=f"v{n:02d}")
    rows.event(at(5), "t9", "dead_lettered", reason="delivery_limit", version="v00")

    def versions(report):
        return {value for s in report.samples for kind, value in s.labels
                if kind is m.LabelKind.PROGRAM_VERSION}

    before = window(rows)
    assert versions(before) == {f"v{n:02d}" for n in range(1, 21)} | {m.OTHER}
    rows.event(at(minutes=70), "t9", "replay_requeued", source="admin_command")
    rows.event(at(minutes=75), "t9", "handed_off", version="future")  # 窗外、全域最後的終點
    after = window(rows)
    assert versions(after) == versions(before)
    assert pick(after, "dsp_calls") == pick(before, "dsp_calls")


# ---- [S631] 代碼審第 1 輪:政策版本標籤不跟查詢當下的目前版本比,改版後舊窗不變(代使用者裁定) ----
def test_policy_version_labels_of_a_past_window_survive_a_policy_change(rows, monkeypatch):
    rows.event(at(1), "t1", "handed_off")  # 寫入當時的目前版本
    rows.event(at(2), "t2", "blocked", policy="made-up-v9", reason="version_changed")
    before = window(rows)
    labels = {s.label_map()["policy_version"] for s in pick(before, "terminal_event_rate")}
    assert labels == {POLICY_VERSION, m.OTHER}

    upgraded = "demo-pacing-v2"  # 部署改版:常數換新值,清單也加上新值
    grown = (*KNOWN_POLICY_VERSIONS, upgraded)
    for module in (proposal_module, m):
        monkeypatch.setattr(module, "POLICY_VERSION", upgraded, raising=False)
        monkeypatch.setattr(module, "KNOWN_POLICY_VERSIONS", grown, raising=False)
    after = window(rows)
    assert pick(after, "terminal_event_rate") == pick(before, "terminal_event_rate")


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
    # 租戶 beta 四筆都等 30 秒:耗時相同取結束時間最新的(50、40、35 秒),不是任務編號順序
    for n, received in enumerate([0, 10, 20, 5]):
        rows.event(at(received), f"b{n}", "received", tenant=None, campaign="c3")
        rows.event(at(received + 30), f"b{n}", "delivered", deliveries=1, campaign="c3")
    report = window(rows)

    slowest = one(report, "queue_wait_seconds.p95", tenant="acme")
    assert slowest.exemplars == ("q0", "q2", "q4")  # 最慢的三筆:60、50、32 秒
    tied = one(report, "queue_wait_seconds.max", tenant="beta")
    assert (tied.value, tied.count, tied.exemplars) == (30, 4, ("b2", "b1", "b3"))
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
    # 非鏈尾的任務(它自己的最新修訂)結案時間反而晚於鏈尾:「挑最新」的捷徑會選錯,只能靠鏈尾檢查
    rows.event(at(minutes=14), "f1", "handed_off", revision=2)
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
    execution = one(report, "execution_seconds.max", tenant="acme")
    # 依窗內每個終點事件分段:死信前 1→2 分(60 秒)、重放放回後 33→35 分(120 秒);死信等待不在段內
    assert (execution.count, execution.value) == (2, 120)
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


# ---- [S647][S649] 代碼審第 1 輪:執行端處理依窗內實際的每個終點事件分段,窗過去之後再查不變 ----
def test_execution_latency_is_segmented_by_each_terminal_event_in_the_window(rows):
    rows.event(at(minutes=0), "t2", "received", tenant=None)
    rows.event(at(minutes=1), "t2", "delivered", deliveries=1)
    rows.event(at(minutes=10), "t2", "dead_lettered", reason="delivery_limit")
    before = window(rows)
    execution = one(before, "execution_seconds.max", tenant="acme")
    assert (execution.count, execution.value, execution.exemplars) == (1, 540, ("t2",))

    rows.event(at(minutes=70), "t2", "replay_requeued", source="admin_command")  # 窗外重放成功
    rows.event(at(minutes=71), "t2", "delivered", deliveries=1)
    rows.event(at(minutes=75), "t2", "handed_off")
    after = window(rows)
    assert ([s for s in after.samples if s.base == "execution_seconds"]
            == [s for s in before.samples if s.base == "execution_seconds"])
    later = one(window(rows, at(minutes=60), at(minutes=120)), "execution_seconds.max",
                tenant="acme")
    assert (later.count, later.value) == (1, 240)  # 從重放放回之後第一次取件算起


# ---- [S635] 代碼審第 1 輪:時間一律要帶時區 ----
def test_metric_times_must_carry_a_time_zone(rows, tmp_path, capsys):
    naive_since, naive_until = at(0).replace(tzinfo=None), at(minutes=60).replace(tzinfo=None)
    for since, until in ((naive_since, naive_until), (at(0), naive_until),
                         (naive_since, at(minutes=60))):
        with pytest.raises(ValueError, match="時區"):
            window(rows, since, until)
    with pytest.raises(ValueError, match="時區"):
        snapshot(rows, naive_since)
    rows.pending("p1", at(0))  # 沒有租戶設定時也要在入口擋下,不靠下游哪一支讀取剛好會丟
    with pytest.raises(ValueError, match="時區"):
        m.collect_snapshot(naive_since, executor_db=rows.executor_db, tenants=())

    config = tmp_path / "tenants.json"
    config.write_text('{"tenants": {"acme": {"campaigns": ["c1"], "max_budget": 100}}}')
    config.chmod(0o600)
    base = ["--executor-db", str(rows.executor_db), "--analyzer-db", str(rows.analyzer_db),
            "--tenants-config", str(config)]
    for times in (["--since", naive_since.isoformat(), "--until", naive_until.isoformat()],
                  ["--since", at(0).isoformat(), "--until", naive_until.isoformat()],
                  ["--now", naive_since.isoformat()]):
        with pytest.raises(SystemExit) as exited:
            m.run([*base, *times], out=io.StringIO(), err=io.StringIO())
        assert exited.value.code == m.EXIT_BAD_ARGUMENTS, times
        assert "時區" in capsys.readouterr().err

    # 非 UTC 偏移:換算成 UTC 後照樣是含起點、不含終點的半開窗
    rows.event(at(0), "t1", "handed_off")
    rows.event(at(minutes=60), "t2", "handed_off")
    taipei = timezone(timedelta(hours=8))
    since, until = at(0).astimezone(taipei), at(minutes=60).astimezone(taipei)
    rate = one(window(rows, since, until), "terminal_event_rate", tenant="acme")
    assert (rate.count, rate.exemplars) == (1, ("t1",))
    out = io.StringIO()
    assert m.run([*base, "--since", since.isoformat(), "--until", until.isoformat()], out=out,
                 err=io.StringIO()) == m.EXIT_OK
    assert "+08:00" in since.isoformat() and '"t2"' not in out.getvalue()


# ---- [S647] 代碼審第 1 輪:窗界含起點、不含終點 ----
def test_window_bounds_include_the_start_and_exclude_the_end(rows):
    rows.task("t1", at(minutes=-10))
    rows.event(at(-60), "t1", "delivered", deliveries=1)
    rows.event(at(0), "t1", "handed_off")  # 剛好等於起點:算入
    rows.event(at(minutes=59), "t2", "delivered", deliveries=1)
    rows.event(at(minutes=60), "t2", "handed_off")  # 剛好等於終點:排除
    rows.task("t3", at(0))  # 窗內死信、重放後最終結果剛好落在終點:最終結果與端到端都排除
    rows.event(at(minutes=20), "t3", "delivered", deliveries=1)
    rows.event(at(minutes=30), "t3", "dead_lettered", reason="delivery_limit")
    rows.event(at(minutes=40), "t3", "replay_requeued", source="admin_command")
    rows.event(at(minutes=50), "t3", "delivered", deliveries=1)
    rows.event(at(minutes=60), "t3", "handed_off")
    rows.event(at(-30), "q1", "received", tenant=None)
    rows.event(at(0), "q1", "delivered", deliveries=1)  # 佇列等待結束剛好等於起點:算入
    rows.event(at(minutes=59), "q2", "received", tenant=None)
    rows.event(at(minutes=60), "q2", "delivered", deliveries=1)  # 結束剛好等於終點:排除
    report = window(rows)

    events = pick(report, "terminal_event_rate", tenant="acme")
    assert {s.count for s in events} == {2}
    assert {(s.label_map()["terminal_kind"], s.exemplars) for s in events} == {
        ("handed_off", ("t1",)), ("dead_lettered", ("t3",))}
    final = one(report, "final_outcome_rate", tenant="acme")
    assert (final.count, final.exemplars) == (1, ("t1",))
    queue = one(report, "queue_wait_seconds.max", tenant="acme")
    assert (queue.count, queue.value, queue.exemplars) == (1, 30, ("q1",))
    execution = one(report, "execution_seconds.max", tenant="acme")
    assert (execution.count, execution.value, execution.exemplars) == (2, 600, ("t3", "t1"))
    end_to_end = one(report, "end_to_end_seconds.max")
    assert (end_to_end.count, end_to_end.value, end_to_end.exemplars) == (1, 600, ("t1",))


# ---- [S641] 代碼審第 1 輪:讀到穩定為止只套在端到端,其他指標只讀一次 ----
def test_only_end_to_end_is_reread_until_stable(rows, monkeypatch):
    rows.task("r1", at(0))
    rows.event(at(minutes=12), "r1", "handed_off")
    full_reads = []
    original_calls = attempt_store.dsp_calls_between

    def counting(tx, since, until):
        full_reads.append(since)
        return original_calls(tx, since, until)

    monkeypatch.setattr(attempt_store, "dsp_calls_between", counting)
    original_read = m._read_analyzer
    writes = iter(range(1, 20))
    touch_end_to_end = False

    def busy(*args, **kwargs):  # 每讀一次分析端,執行端就多一筆落在窗內的新寫入
        n = next(writes)
        rows.dsp_call(at(minutes=20 + n), "write", "responded", status=200)
        rows.event(at(minutes=20 + n), f"n{n}", "received", tenant=None)
        if touch_end_to_end:  # 接得上的新結案:端到端每輪都不同
            rows.task(f"e{n}", at(0))
            rows.event(at(minutes=20 + n), f"e{n}", "handed_off")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(m, "_read_analyzer", busy)
    calm = window(rows)
    assert (calm.stable, calm.rounds) == (True, 3)  # 新寫入不影響端到端:兩次重讀都一致,不標不穩定
    assert len(full_reads) == 1  # 非端到端指標只讀一次
    assert one(calm, "end_to_end_seconds.max").count == 1

    touch_end_to_end = True
    busy_report = window(rows)
    assert (busy_report.stable, busy_report.rounds) == (False, 2)  # 一不同就停


# ---- [S641] 代碼審第 2 輪:整份報告都用第一輪;重讀只確認端到端跟第一輪一致 ----
def test_a_terminal_written_after_the_first_read_marks_the_report_unstable(rows, monkeypatch):
    rows.task("r1", at(0))
    rows.event(at(minutes=12), "r1", "handed_off")
    original_read = m._read_analyzer
    calls = []

    def racing(*args, **kwargs):  # 第一輪執行端讀完之後,才寫進一筆窗內、接得上的終點;之後不再變
        calls.append(1)
        if len(calls) == 1:
            rows.task("e1", at(0))
            rows.event(at(minutes=20), "e1", "handed_off")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(m, "_read_analyzer", racing)
    report = window(rows)

    # 後兩輪彼此相等也不算穩定:第二輪跟第一輪不同就停、標不穩定
    assert (report.stable, report.rounds) == (False, 2)
    ends = one(report, "end_to_end_seconds.max")
    terminal = {task for s in pick(report, "terminal_event_rate") for task in s.exemplars}
    assert ends.exemplars == ("r1",) and ends.count == 1  # 報告的數字全部出自第一輪
    assert set(ends.exemplars) <= terminal
    assert terminal == {"r1"}


# ---- [S635] 代碼審第 2 輪:命令列參數錯一律是參數錯那一號,不跟資料庫檔不存在撞號 ----
def test_missing_or_mismatched_flags_exit_with_the_bad_arguments_code(rows, capsys):
    since, until = at(0).isoformat(), at(minutes=60).isoformat()
    for argv in (
        ["--analyzer-db", str(rows.analyzer_db), "--tenants-config", "t.json",
         "--since", since, "--until", until],  # 漏 --executor-db
        ["--executor-db", str(rows.executor_db), "--analyzer-db", str(rows.analyzer_db),
         "--since", since, "--until", until],  # 漏 --tenants-config
        ["--executor-db", str(rows.executor_db), "--tenants-config", "t.json",
         "--since", since, "--until", until],  # 窗內統計沒給 --analyzer-db
        ["--executor-db", str(rows.executor_db), "--analyzer-db", str(rows.analyzer_db),
         "--tenants-config", "t.json", "--since", since],  # 只給 --since
    ):
        with pytest.raises(SystemExit) as exited:
            m.run(argv, out=io.StringIO(), err=io.StringIO())
        assert exited.value.code == m.EXIT_BAD_ARGUMENTS != m.EXIT_NO_DATABASE, argv
        assert "參數錯誤" in capsys.readouterr().err


# ---- [S641] 代碼審第 3 輪:每次重讀都要跟第一輪完全一致才算穩定,比的是完整樣本不是摘要 ----
def _scripted_end_to_end(monkeypatch, *rounds):
    """端到端每一輪算出的完整樣本照劇本給(第一輪是報告用的那一份)。"""
    script = iter(rounds)
    monkeypatch.setattr(m, "_end_to_end_members", lambda *_args: next(script))


def _ends(*pairs):
    return m._EndToEnd(tuple(m._Member(at(minutes=10 + n).isoformat(), task, False, value)
                             for n, (task, value) in enumerate(pairs)), (), ())


@pytest.mark.parametrize("story", ["A-B-A", "A-A-B", "same-summary"])
def test_any_reread_that_differs_from_the_first_marks_the_report_unstable(
        rows, tmp_path, monkeypatch, story):
    first = _ends(("a", 100), ("b", 200), ("c", 300), ("d", 400))
    moved = _ends(("b", 200), ("c", 300), ("d", 400))
    # 換掉的任務不在前三個範例裡、耗時也一樣:摘要(樣本數、分位數、範例)完全相同,任務卻不同
    lookalike = _ends(("z", 100), ("b", 200), ("c", 300), ("d", 400))
    rounds = {"A-B-A": (first, moved, first), "A-A-B": (first, first, moved),
              "same-summary": (first, lookalike, lookalike)}[story]
    assert (m._end_to_end_samples(lookalike) == m._end_to_end_samples(first)) and lookalike != first
    _scripted_end_to_end(monkeypatch, *rounds)
    report = window(rows)
    assert report.stable is False
    assert one(report, "end_to_end_seconds.max").exemplars == ("d", "c", "b")  # 仍是第一輪

    config = tmp_path / "tenants.json"
    config.write_text('{"tenants": {"acme": {"campaigns": ["c1"], "max_budget": 100}}}')
    config.chmod(0o600)
    _scripted_end_to_end(monkeypatch, *rounds)
    err = io.StringIO()
    code = m.run(["--executor-db", str(rows.executor_db), "--analyzer-db", str(rows.analyzer_db),
                  "--tenants-config", str(config), "--since", at(0).isoformat(),
                  "--until", at(minutes=60).isoformat()], out=io.StringIO(), err=err)
    assert code == m.EXIT_UNSTABLE
    assert "第一輪" in err.getvalue() and "最後一輪" not in err.getvalue()


def test_a_report_is_stable_only_when_every_reread_matches_the_first(rows, monkeypatch):
    same = _ends(("a", 100))
    _scripted_end_to_end(monkeypatch, same, same, same)
    report = window(rows)
    assert (report.stable, report.rounds) == (True, m.MAX_ROUNDS)


def test_a_broken_model_ledger_only_empties_the_model_metric(rows, tmp_path):
    """花費帳還沒建表或讀取出錯:只有「模型與 Jev」變成無樣本並寫原因,其他指標照常
    (代碼審第 1 輪)。"""
    a_little_of_everything(rows)
    half_built = tmp_path / "ledger.sqlite"
    sqlite3.connect(half_built).close()  # 檔案在、表還沒建
    not_a_db = tmp_path / "garbage.sqlite"
    not_a_db.write_bytes(b"this is not a database at all" * 100)
    for ledger in (half_built, not_a_db):
        report = m.collect_window(W_START, W_END, executor_db=rows.executor_db,
                                  analyzer_db=rows.analyzer_db, tenants=TENANTS,
                                  model_ledger=ledger)
        jev = one(report, "model_and_jev")
        assert jev.status is m.Status.NO_SAMPLES and "花費帳" in jev.note, ledger
        assert pick(report, "terminal_event_rate"), ledger
