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
