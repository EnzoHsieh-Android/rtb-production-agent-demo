"""服務水準指標、目標、錯誤預算與燒損告警(Phase 9 增量 3):[S650] [S651] [S654] [S655] [S656]
到 [S662] [S666]。

燒損的數學與告警邊界用假的計數函式餵(每個窗要多少好事件、有效事件由測試決定);各條服務水準
指標怎麼分好壞,用 rows.py 直接寫紀錄、交給真的計數函式算。
"""

import sqlite3
from datetime import timedelta
from fractions import Fraction

import pytest

from rtb.ops import sli, slo
from rtb.ops.sli import Tally
from tests.ops.rows import Rows, at


@pytest.fixture
def rows(tmp_path):
    built = Rows(tmp_path)
    yield built
    built.close()


def sources(rows, dsp_url="http://127.0.0.1:9"):
    return sli.Sources(rows.executor_db, rows.analyzer_db, dsp_url, 0.2)


NOW = at(minutes=600)
HANDOFF_LIST = {"safe_completion", "unauthorized_side_effects", "harmful_duplicates",
                "unknown_reconciled_in_time", "end_to_end_handoff", "queue_wait"}


def spec(name):
    return next(s for s in slo.SLOS if s.name == name)


def lengths():
    fast, slow = (next(b for b in slo.BURNS if b.name == n) for n in ("fast", "slow"))
    return {"fast_long": slo.scaled(fast.long), "fast_short": slo.scaled(fast.short),
            "slow_long": slo.scaled(slow.long), "slow_short": slo.scaled(slow.short),
            "period": slo.scaled(slo.PERIOD)}


class Planned:
    """假的計數函式:依窗長回預先排好的計數,並記下被問了哪些窗。"""

    def __init__(self, by_length=None, default=None):
        self.by_length, self.asked = by_length or {}, []
        self.default = default or Tally(0, 0)

    def __call__(self, name, since, until):
        self.asked.append((name, since, until))
        return self.by_length.get((name, until - since), self.by_length.get(until - since,
                                                                          self.default))


def status(result, name):
    return next(s for s in result if s.name == name)


# ---- [S650] ----
def test_every_slo_has_a_complete_definition():
    assert {s.name for s in slo.SLOS} == HANDOFF_LIST
    for s in slo.SLOS:
        for text in (s.name, s.formula, s.good, s.valid, s.window, s.exclusions):
            assert isinstance(text, str) and text.strip(), (s.name, text)
        assert isinstance(s.target, Fraction) and 0 < s.target <= 1, s.name
        assert s.demo is True
        if s.name in {"unknown_reconciled_in_time", "queue_wait"}:
            assert isinstance(s.deadline, timedelta) and s.deadline > timedelta(0)
        else:
            assert s.deadline is None, s.name
    assert spec("unknown_reconciled_in_time").deadline == timedelta(minutes=10)
    assert spec("queue_wait").deadline == timedelta(seconds=30)  # 期限不縮短(增量 4 回頭補)
    assert spec("unauthorized_side_effects").zero_target and spec("harmful_duplicates").zero_target
    assert slo.SCALE == 60 and slo.MIN_SAMPLES == 10
    assert slo.scaled(slo.PERIOD) == timedelta(hours=12)


# ---- [S651] ----
def test_safe_completion_classifies_every_terminal_kind(rows):
    good = ("campaign_not_found", "campaign_not_active", "version_changed",
            "policy_version_changed", "decision_stale", "campaign_not_allowed", "over_budget_cap",
            "aggregate_limit_reached", "budget_increase_too_large")
    for n, reason in enumerate(good):
        rows.event(at(minutes=1, seconds=n), f"g{n}", "blocked", reason=reason)
    rows.event(at(minutes=2), "h1", "handed_off")
    rows.event(at(minutes=3), "b1", "blocked", reason="operation_previously_failed")
    rows.event(at(minutes=3), "b2", "expired")
    rows.event(at(minutes=4), "s1", "superseded")  # 排除:分析端送了新修訂
    rows.event(at(minutes=4), "s2", "awaiting_approval", reason="budget_increase_too_large")
    rows.event(at(minutes=10), "d1", "dead_lettered", reason="delivery_limit")  # 窗一
    rows.event(at(minutes=70), "d1", "replay_requeued", source="admin_command")
    rows.event(at(minutes=75), "d1", "handed_off")  # 窗二
    first = sli.count("safe_completion", at(0), at(minutes=60), sources(rows))
    second = sli.count("safe_completion", at(minutes=60), at(minutes=120), sources(rows))

    assert (first.good, first.valid) == (len(good) + 1, len(good) + 4)
    assert (second.good, second.valid) == (1, 1)  # 死信後重放成功:兩個窗各一個事件


# ---- [S654] ----
def test_deadline_based_slis_are_fixed_once_the_window_has_passed(rows):
    t = at(minutes=0)
    rows.attempts("kA", [("in_flight", t), ("unknown", t),
                         ("verified", t + timedelta(minutes=10))])  # 剛好第 10 分鐘:好
    rows.attempts("kB", [("in_flight", t), ("unknown", t)])  # 還沒結案
    rows.attempts("kC", [("in_flight", t), ("unknown", t),
                         ("escalated", t + timedelta(minutes=3))])  # 期限前轉人工:排除
    rows.attempts("kD", [("in_flight", t), ("unknown", t + timedelta(seconds=1)),
                         ("escalated", t + timedelta(minutes=12))])  # 期限後才轉人工:壞
    for n, delay in enumerate((30, 45)):  # 剛好第 30 秒取件是好;45 秒是壞(記在第 30 秒)
        rows.event(at(minutes=2), f"q{n}", "received", tenant=None)
        rows.event(at(minutes=2, seconds=delay), f"q{n}", "delivered", deliveries=1)
    rows.event(at(minutes=2), "q2", "received", tenant=None)  # 一直沒被取件
    rows.event(at(minutes=2), "q3", "received", tenant=None)
    rows.event(at(minutes=2, seconds=40), "q3", "expired")  # 過了期限才被標成已過期
    src = sources(rows)
    window = (at(minutes=5), at(minutes=11))
    unknown = sli.count("unknown_reconciled_in_time", *window, src)
    queue = sli.count("queue_wait", at(minutes=2), at(minutes=3), src)

    assert (unknown.good, unknown.valid) == (1, 3)
    assert sorted(unknown.bad_at) == sorted([slo_iso(t + timedelta(minutes=10)),
                                             slo_iso(t + timedelta(minutes=10, seconds=1))])
    assert (queue.good, queue.valid) == (1, 4)
    assert set(queue.bad_at) == {slo_iso(at(minutes=2, seconds=30))}

    rows.attempt("kB", 3, "verified", t + timedelta(minutes=15))  # 之後才結案
    rows.event(at(minutes=4), "q2", "delivered", deliveries=1)  # 之後才取件
    assert sli.count("unknown_reconciled_in_time", *window, src) == unknown
    assert sli.count("queue_wait", at(minutes=2), at(minutes=3), src) == queue


def slo_iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---- [S655] ----
def test_end_to_end_handoff_sli_is_event_based(rows):
    rows.task("r1", at(0))
    rows.event(at(seconds=100), "r1", "handed_off")  # 100 秒:好
    rows.task("r2", at(0))
    rows.event(at(seconds=10), "r2", "awaiting_approval", reason="budget_increase_too_large")
    rows.event(at(seconds=310), "r2", "approval_released", reason="budget_increase_too_large")
    rows.event(at(seconds=350), "r2", "handed_off")  # 350 秒扣掉 300 秒核可等待:好
    rows.task("r3", at(0))
    rows.follow_up("r3", "f3", at(seconds=60))
    rows.event(at(seconds=60), "r3", "blocked", reason="version_changed")  # 擋下不是有效事件
    rows.event(at(seconds=200), "f3", "handed_off")  # 從根任務算 200 秒:壞
    rows.event(at(seconds=150), "u1", "handed_off")  # 接不上
    rows.task("n1", at(seconds=400))
    rows.event(at(seconds=300), "n1", "handed_off")  # 時鐘異常
    tally = sli.count("end_to_end_handoff", at(0), at(minutes=10), sources(rows))

    assert (tally.good, tally.valid) == (2, 3)
    assert (tally.unlinked, tally.clock_anomaly) == (1, 1)


# ---- [S656] ----
def test_burn_rate_is_the_budget_consumption_speed():
    assert slo.burn_rate(990, 1000, Fraction(99, 100)) == 1  # 錯誤率 1%、目標 99%
    assert slo.burn_rate(990, 1000, Fraction(999, 1000)) == 10  # 同樣 1%、目標 99.9%:十倍
    assert slo.burn_rate(0, 0, Fraction(99, 100)) == 0
    assert slo.error_budget(1000, Fraction(99, 100)) == 10
    assert slo.remaining_budget(1000, 12, Fraction(99, 100)) == -2  # 超支

    counter = Planned({lengths()["period"]: Tally(988, 1000)})
    result = status(slo.evaluate(NOW, counter=counter), "safe_completion")
    assert (result.budget, result.remaining) == (10, -2)


def _fast(counter_long, counter_short, name="safe_completion"):
    counter = Planned({(name, lengths()["fast_long"]): counter_long,
                       (name, lengths()["fast_short"]): counter_short})
    return status(slo.evaluate(NOW, counter=counter), name).fast


# ---- [S657] ----
def test_a_fast_burn_fires_exactly_at_its_threshold():
    # 目標 99%、門檻 14.4:壞事件比例剛好 14.4% 就是門檻
    at_threshold = Tally(1000 - 144, 1000)
    below = Tally(1000 - 143, 1000)
    assert _fast(at_threshold, at_threshold).fired is True
    assert _fast(below, at_threshold).fired is False  # 長窗差一個壞事件
    assert _fast(at_threshold, below).fired is False  # 短窗差一個壞事件
    fired = _fast(Tally(500, 1000), Tally(10, 20))
    assert fired.fired and fired.long.burn == 50 and fired.short.burn == 50


# ---- [S658] ----
def test_a_slow_burn_fires_only_the_slow_alert():
    name = "queue_wait"
    slow_only = Planned({(name, lengths()["slow_long"]): Tally(930, 1000),  # 燒損率 7
                         (name, lengths()["slow_short"]): Tally(93, 100),
                         (name, lengths()["fast_long"]): Tally(93, 100),
                         (name, lengths()["fast_short"]): Tally(93, 100)})
    result = status(slo.evaluate(NOW, counter=slow_only), name)
    assert result.slow.fired is True and result.fast.fired is False

    both = Planned({(name, length): Tally(0, 100) for length in lengths().values()})
    result = status(slo.evaluate(NOW, counter=both), name)
    assert result.slow.fired is True and result.fast.fired is True  # 兩種告警同時為是


# ---- [S659] ----
def test_an_alert_clears_once_the_short_window_recovers():
    recovered = _fast(Tally(500, 1000), Tally(20, 20))
    assert recovered.fired is False and recovered.long.burn == 50
    quiet = _fast(Tally(500, 1000), Tally(0, 0))  # 短窗沒有有效事件:燒損率當 0
    assert quiet.short.burn == 0 and quiet.fired is False and quiet.insufficient is False


# ---- [S660] ----
def test_too_few_events_reports_insufficient_samples():
    sparse = _fast(Tally(0, 9), Tally(0, 9))  # 長窗 9 個有效事件,少於最少樣本 10
    assert sparse.insufficient is True and sparse.fired is False
    enough = _fast(Tally(0, 10), Tally(0, 9))
    assert enough.insufficient is False and enough.fired is True


# ---- [S661] ----
def test_a_single_zero_target_violation_fires_for_the_whole_period():
    name = "unauthorized_side_effects"
    period = lengths()["period"]
    bad_at = slo_iso(NOW - timedelta(hours=11))
    counter = Planned({(name, period): Tally(99, 100, bad_at=(bad_at,), unverifiable=2)})
    result = status(slo.evaluate(NOW, counter=counter), name)
    assert result.violating is True and result.period_bad == 1
    assert result.last_bad == bad_at and result.unverifiable == 2
    assert result.budget == 0 and result.fast is None and result.slow is None  # 不計算燒損率

    later = status(slo.evaluate(NOW, counter=Planned({(name, period): Tally(100, 100)})), name)
    assert later.violating is False  # 壞事件滑出週期之後


# ---- [S662] ----
def test_the_burn_evaluator_reads_only(rows):
    rows.event(at(minutes=599), "t1", "handed_off")
    rows.task("t1", at(minutes=598))
    before = [_dump(p) for p in (rows.executor_db, rows.analyzer_db)]
    holders = [sqlite3.connect(p, isolation_level=None)
               for p in (rows.executor_db, rows.analyzer_db)]
    for holder in holders:
        holder.execute("BEGIN IMMEDIATE")
    asked = []

    def counter(name, since, until):
        asked.append((name, since, until))
        return sli.count(name, since, until, sources(rows))

    try:
        slo.evaluate(NOW, counter=counter)
    finally:
        for holder in holders:
            holder.execute("ROLLBACK")
            holder.close()
    assert [_dump(p) for p in (rows.executor_db, rows.analyzer_db)] == before
    targeted = [s.name for s in slo.SLOS if not s.zero_target]
    expected = {(name, NOW - length, NOW) for name in targeted
                for length in list(lengths().values())[:4]}
    expected |= {(s.name, NOW - lengths()["period"], NOW) for s in slo.SLOS}
    assert set(asked) == expected and len(asked) == len(expected) == 4 * 4 + 6


def _dump(path):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t}").fetchall()  # noqa: S608 - 表名來自 sqlite_master
                for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


# ---- [S666] ----
def test_the_period_is_summed_from_day_sized_windows():
    end = at(minutes=600)
    pieces = slo.period_windows(end, slo.PERIOD)  # 縮短倍數是 1 時的 30 天週期
    assert len(pieces) == 30
    assert pieces[0][0] == end - slo.PERIOD and pieces[-1][1] == end
    assert all(b - a <= timedelta(hours=24) for a, b in pieces)
    assert all(pieces[i][1] == pieces[i + 1][0] for i in range(len(pieces) - 1))  # 首尾相接

    seam = pieces[3][1]  # 剛好落在接縫上的事件
    events = [seam, seam - timedelta(microseconds=1), end - timedelta(days=29, hours=23)]

    def counter(_name, since, until):
        inside = [e for e in events if since <= e < until]
        return Tally(0, len(inside))

    assert slo.period_tally("safe_completion", end, slo.PERIOD, counter).valid == 3
    assert len(slo.period_windows(end, slo.scaled(slo.PERIOD))) == 1
