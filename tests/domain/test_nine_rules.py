"""Phase 14 增量 1:純領域判斷的無格分流與匯入邊界。"""

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import pytest

from rtb.domain import nine_rules as rules
from rtb.domain.worth import CampaignStatus, WorthInput, WorthVerdict

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _worth(**changes):
    values = {"status": CampaignStatus.ACTIVE, "budget": 100, "spend": 1,
              "impressions": 500, "clicks": 10, "conversions": 1, "revenue": 5}
    return WorthInput(**{**values, **changes})


def _evidence(**changes):
    window = rules.Window(100, 10, 1, 2, 5)
    rows = tuple(rules.DailyRow(day, 100, 10, 1, 2, 5, False) for day in range(1, 8))
    values = {"longer": rules.LongerWindow(window, window),
              "history": rules.ChangeHistory(()), "daily": rules.DailyTrend(rows),
              "past": rules.PastAdjustments(())}
    return rules.RuleEvidence(**{**values, **changes})


def test_fixed_two_decimal_amount_strings():
    for amount in ("5/2", "2.5", "nan", " 2.50"):
        with pytest.raises(ValueError):
            rules.Window(100, 10, 1, amount, "1.00")
        with pytest.raises(ValueError):
            rules.DailyRow(1, 100, 10, 1, "1.00", amount, False)
    assert rules.Window(100, 10, 1, "0.30", "1.00").spend == "0.30"
    assert rules.DailyRow(1, 100, 10, 1, "0.10", "0.20", False).spend == "0.10"


def test_nine_rule_input_and_query_gaps_have_no_cell():
    invalid = rules.decide(None, _evidence(), NOW)
    assert (invalid.cell, invalid.verdict, invalid.reason) == (
        None, WorthVerdict.INSUFFICIENT, rules.RuleReason.INPUT_INVALID)
    absent = rules.decide(_worth(), replace(_evidence(), history=None), NOW)
    assert (absent.cell, absent.reason, absent.query) == (
        None, rules.RuleReason.QUERY_NO_RESULT, rules.QueryKind.HISTORY)
    assert rules.decide(_worth(status=CampaignStatus.PAUSED), rules.RuleEvidence(), NOW).cell is (
        rules.Cell.PAUSED)
    assert rules.decide(_worth(clicks=501), rules.RuleEvidence(), NOW).cell is rules.Cell.ANOMALY


def test_recent_cutoff_is_strict_and_uses_decision_clock():
    at_cutoff = rules.HistoryRow("update_budget", NOW - timedelta(days=rules.RECENT_DAYS))
    later = rules.HistoryRow("update_budget", NOW - timedelta(days=rules.RECENT_DAYS)
                             + timedelta(microseconds=1))
    on_boundary = _evidence(history=rules.ChangeHistory((at_cutoff,)))
    inside_boundary = _evidence(history=rules.ChangeHistory((later,)))
    assert rules.decide(_worth(), on_boundary, NOW).cell is rules.Cell.DELIVERY_WITH_VALUE
    assert rules.decide(_worth(), inside_boundary, NOW).cell is rules.Cell.RECENT_BUDGET_CHANGE


@pytest.mark.parametrize("committed_at", [None, datetime(2026, 9, 24)])
def test_history_missing_time_is_insufficient_in_either_row_order(committed_at):
    recent = rules.HistoryRow("update_budget", NOW - timedelta(days=1))
    missing = rules.HistoryRow("update_budget", committed_at)
    for rows in ((recent, missing), (missing, recent)):
        outcome = rules.decide(_worth(), _evidence(history=rules.ChangeHistory(rows)), NOW)
        assert (outcome.cell, outcome.reason, outcome.query) == (
            None, rules.RuleReason.MISSING_ROW_VALUE, rules.QueryKind.HISTORY)


def test_history_timezone_without_offset_is_insufficient():
    class NoOffset(tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

    row = rules.HistoryRow("update_budget", NOW.replace(tzinfo=NoOffset()))
    outcome = rules.decide(_worth(), _evidence(history=rules.ChangeHistory((row,))), NOW)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.MISSING_ROW_VALUE, rules.QueryKind.HISTORY)


def test_any_recent_history_row_wins_after_all_rows_are_checked():
    recent = rules.HistoryRow("update_budget", NOW - timedelta(days=1))
    old = rules.HistoryRow("update_budget", NOW - timedelta(days=9))
    for rows in ((old, recent), (recent, old)):
        assert rules.decide(_worth(), _evidence(history=rules.ChangeHistory(rows)), NOW).cell \
            is rules.Cell.RECENT_BUDGET_CHANGE


def test_naive_decision_clock_is_rejected_explicitly():
    with pytest.raises(ValueError, match=r"now.*時區"):
        rules.decide(_worth(), _evidence(), datetime(2026, 9, 25))


@pytest.mark.parametrize("before,after", [(None, 150), (100, None)])
def test_past_adjustment_missing_budget_is_insufficient(before, after):
    row = rules.AdjustmentRow(5, before, after, 40, 30)
    outcome = rules.decide(_worth(), _evidence(past=rules.PastAdjustments((row,))), NOW)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.MISSING_ROW_VALUE, rules.QueryKind.PAST)


@pytest.mark.parametrize("changes", [
    {"clicks": -5}, {"impressions": -1}, {"conversions": -1},
    {"spend": -1}, {"revenue": -1}, {"clicks": 101}, {"conversions": 11},
])
def test_invalid_daily_row_is_insufficient_before_segment_aggregation(changes):
    rows = _evidence().daily.rows
    daily = rules.DailyTrend((replace(rows[0], **changes), *rows[1:]))
    outcome = rules.decide(_worth(), _evidence(daily=daily), NOW)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.INVALID_ROW_VALUE, rules.QueryKind.DAILY)


@pytest.mark.parametrize("changes", [
    {"budget_before": -1}, {"budget_after": -1},
    {"before_conversions": -1}, {"after_conversions": -1},
])
def test_invalid_past_adjustment_is_insufficient(changes):
    row = replace(rules.AdjustmentRow(5, 100, 150, 40, 30), **changes)
    outcome = rules.decide(_worth(), _evidence(past=rules.PastAdjustments((row,))), NOW)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.INVALID_ROW_VALUE, rules.QueryKind.PAST)


def test_invalid_older_adjustment_cannot_be_hidden_by_a_newer_rule_hit():
    hit = rules.AdjustmentRow(5, 100, 150, 40, 30)
    invalid = rules.AdjustmentRow(9, 100, 150, -1, 5)
    for rows in ((hit, invalid), (invalid, hit)):
        outcome = rules.decide(_worth(), _evidence(past=rules.PastAdjustments(rows)), NOW)
        assert (outcome.cell, outcome.reason, outcome.query) == (
            None, rules.RuleReason.INVALID_ROW_VALUE, rules.QueryKind.PAST)


@pytest.mark.parametrize("make", [
    lambda: rules.Window(True, 10, 1, 2, 5),
    lambda: rules.LongerWindow("bad", rules.Window(1, 1, 1, 1, 1)),
    lambda: rules.HistoryRow(3, NOW),
    lambda: rules.ChangeHistory(("bad",)),
    lambda: rules.DailyRow(1, 1, 1, 1, 1, 1, "false"),
    lambda: rules.DailyTrend(("bad",)),
    lambda: rules.AdjustmentRow(True, 100, 150, 1, 1),
    lambda: rules.PastAdjustments(("bad",)),
    lambda: rules.RuleEvidence(history="bad"),
    lambda: rules.RuleDecision(WorthVerdict.WORTH, rules.Cell.PAUSED,
                               rules.RuleReason.PAUSED),
])
def test_domain_data_classes_reject_invalid_shapes_at_construction(make):
    with pytest.raises(ValueError):
        make()


def test_missing_windows_and_daily_days_remain_outside_nine_cells():
    no_value = _worth(conversions=0, revenue=0)
    missing = rules.Window(100, 10, None, 2, 5)
    outcome = rules.decide(no_value, _evidence(longer=rules.LongerWindow(missing, missing)), NOW)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.MISSING_ROW_VALUE, rules.QueryKind.LONGER)
    rows = _evidence().daily.rows
    gap = rules.decide(_worth(), _evidence(daily=rules.DailyTrend(rows[:-1])), NOW)
    assert (gap.cell, gap.reason) == (None, rules.RuleReason.MISSING_DAILY_ROWS)
    no_data = rules.decide(_worth(), _evidence(daily=rules.DailyTrend(
        (replace(rows[0], no_data=True), *rows[1:]))), NOW)
    assert (no_data.cell, no_data.reason) == (None, rules.RuleReason.NO_DATA_DAY)


def test_domain_nine_rules_imports_only_the_allowlisted_stdlib_and_domain():
    path = Path(rules.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = {"collections.abc", "dataclasses", "datetime", "enum", "fractions", "types"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed or node.module.startswith("rtb.domain")


# ---- Phase 14 增量 2b:第 3/4 條以決策 now 消化過去調整的提交時刻([S1415] 領域那半)----
def _raise(committed_at, before=10, after=10, budget=(100, 150)):
    days_ago = max(0, (NOW - committed_at).days)
    return rules.AdjustmentRow(days_ago, budget[0], budget[1], before, after, committed_at)


def _past(*rows):
    return _evidence(past=rules.PastAdjustments(rows))


def test_past_adjustment_commit_time_is_judged_on_the_decision_clock():
    # 未滿三天:第 3 條(即使歷史清單沒有這筆,例如被截斷)
    recent = _raise(NOW - timedelta(days=2))
    assert rules.decide(_worth(), _past(recent), NOW).cell is rules.Cell.RECENT_BUDGET_CHANGE
    # 剛滿三天但 D+3 還沒完整(DSP 回了後段數字也不採信):第 4 條證據不足。計劃例:提交 T,
    # 定案在 T+3d+10s(T 不在 UTC 午夜,D+4 日 00:00 還沒到)
    noon = datetime(2026, 9, 25, 12, tzinfo=UTC)
    just = _raise(noon - timedelta(days=3, seconds=10))
    outcome = rules.decide(_worth(), _past(just), noon)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.MISSING_ROW_VALUE, rules.QueryKind.PAST)
    # D+3 完整、前後轉換 10→10:第 4 條不值得加
    done = _raise(datetime(2026, 9, 20, 12, tzinfo=UTC))
    assert rules.decide(_worth(), _past(done), datetime(2026, 9, 24, tzinfo=UTC)).cell is (
        rules.Cell.RAISE_WITHOUT_GAIN)
    assert rules.decide(_worth(), _past(done), datetime(2026, 9, 23, 23, 59, 59,
                                                         tzinfo=UTC)).reason is (
        rules.RuleReason.MISSING_ROW_VALUE)


def test_past_adjustment_without_or_after_the_decision_time_is_insufficient():
    missing = rules.AdjustmentRow(5, 100, 150, 10, 12)
    outcome = rules.decide(_worth(), _past(missing), NOW)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.MISSING_ROW_VALUE, rules.QueryKind.PAST)
    # 晚於決策 now 的提交時刻:第 3 條先命中(近期),第 4 條也判不合格;不論哪條都不提案
    future = _raise(NOW + timedelta(hours=1))
    assert rules.decide(_worth(), _past(future), NOW).verdict is WorthVerdict.INSUFFICIENT
    assert rules._past_decision(rules.PastAdjustments((future,)), NOW).reason is (
        rules.RuleReason.INVALID_ROW_VALUE)
    with pytest.raises(ValueError):
        rules.AdjustmentRow(5, 100, 150, 10, 12, "2026-09-20")


def test_truncated_history_recent_flag_counts_as_a_recent_change():
    flagged = rules.ChangeHistory((), recent_flag=True)
    assert rules.decide(_worth(), _evidence(history=flagged), NOW).cell is (
        rules.Cell.RECENT_BUDGET_CHANGE)
    with pytest.raises(ValueError):
        rules.ChangeHistory((), recent_flag="yes")


def test_daily_read_on_another_utc_day_is_insufficient():
    rows = _evidence().daily.rows
    late = rules.DailyTrend(rows, read_at=datetime(2026, 9, 24, 23, 59, 59, tzinfo=UTC))
    outcome = rules.decide(_worth(), _evidence(daily=late), datetime(2026, 9, 25, 0, 0, 1,
                                                                     tzinfo=UTC))
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.DAY_BOUNDARY, rules.QueryKind.DAILY)
    same = rules.DailyTrend(rows, read_at=datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC))
    assert rules.decide(_worth(), _evidence(daily=same), NOW + timedelta(minutes=1)).cell is (
        rules.Cell.DELIVERY_WITH_VALUE)


# ---- 代碼審 r1 外家 finder-1:負數或不自洽的查詢結果一律證據不足(不讓另一個正數把它變成提案)----
@pytest.mark.parametrize(("one_day", "seven_days"), [
    (rules.Window(10, 10, -1, "0.00", "0.00"), rules.Window(70, 70, 5, "0.00", "0.00")),  # 負轉換
    (rules.Window(10, 10, 0, "0.00", "0.00"), rules.Window(70, 70, -5, "0.00", "0.00")),
    (rules.Window(10, 10, 0, "-1.00", "0.00"), rules.Window(70, 70, 5, "0.00", "0.00")),
    # 點擊多於曝光、轉換多於點擊、1 天多於 7 天
    (rules.Window(10, 20, 0, "0.00", "0.00"), rules.Window(70, 70, 5, "0.00", "0.00")),
    (rules.Window(10, 5, 6, "0.00", "0.00"), rules.Window(70, 70, 7, "0.00", "0.00")),
    (rules.Window(100, 10, 0, "0.00", "0.00"), rules.Window(70, 70, 5, "0.00", "0.00")),
])
def test_negative_or_inconsistent_longer_windows_are_insufficient(one_day, seven_days):
    no_value = _worth(conversions=0, revenue=0)  # 第 6 條本來會看長窗轉換提案
    outcome = rules.decide(no_value, _evidence(longer=rules.LongerWindow(one_day, seven_days)), NOW)
    assert (outcome.cell, outcome.verdict, outcome.reason, outcome.query) == (
        None, WorthVerdict.INSUFFICIENT, rules.RuleReason.INVALID_ROW_VALUE, rules.QueryKind.LONGER)
    # 有價值的 1 小時(第 8 條)也不得因長窗不合格而提案
    assert rules.decide(_worth(), _evidence(longer=rules.LongerWindow(one_day, seven_days)),
                        NOW).verdict is WorthVerdict.INSUFFICIENT


def test_every_invalid_query_result_blocks_a_proposal_whatever_rule_would_hit():
    """全面:四查詢任一有負數或不自洽,結論都不是值得加(第 3 到 9 條都要有效結果)。"""
    rows = _evidence().daily.rows
    bad = {
        "daily": _evidence(daily=rules.DailyTrend((replace(rows[0], clicks=-1), *rows[1:]))),
        "past": _evidence(past=rules.PastAdjustments((rules.AdjustmentRow(
            5, 100, 150, -1, 5, NOW - timedelta(days=5)),))),
        "past_future": _evidence(past=rules.PastAdjustments((rules.AdjustmentRow(
            5, 100, 150, 1, 5, NOW + timedelta(days=1)),))),
        "history_future": _evidence(history=rules.ChangeHistory((rules.HistoryRow(
            "update_budget", NOW + timedelta(days=1)),))),
    }
    for name, evidence in bad.items():
        assert rules.decide(_worth(), evidence, NOW).verdict is WorthVerdict.INSUFFICIENT, name


def test_a_cross_day_daily_read_reports_the_day_boundary_even_with_a_bad_row():
    """代碼審 r2 外家 finder-1:逐日讀取與決策不同 UTC 日時,就算另有壞列,細因也記跨日(跨日是更根本的
    原因:那批日桶整份不該用)。"""
    rows = _evidence().daily.rows
    daily = rules.DailyTrend((replace(rows[0], clicks=-1), *rows[1:]),
                             read_at=NOW - timedelta(days=1))
    outcome = rules.decide(_worth(), _evidence(daily=daily), NOW)
    assert (outcome.cell, outcome.reason, outcome.query) == (
        None, rules.RuleReason.DAY_BOUNDARY, rules.QueryKind.DAILY)
