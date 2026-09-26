"""九條加額判斷的純領域輸入、先命中規則與無格診斷。

四查詢在邊界完成白名單驗證後轉成這些不可變資料,這裡不讀 DSP、收據或評估案例。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType

from rtb.domain import metrics as m
from rtb.domain._checks import is_amount_or_none, is_aware, is_count_or_none, is_plain_int
from rtb.domain.worth import CampaignStatus, WorthInput, WorthVerdict, is_anomalous

RECENT_DAYS = 3
DAILY_DAYS = 7
DROP_THRESHOLD = Fraction(-1, 2)
GAIN_THRESHOLD = Fraction(0)


class QueryKind(StrEnum):
    LONGER = "check_longer_window"
    HISTORY = "check_change_history"
    DAILY = "check_daily_trend"
    PAST = "check_past_adjustments"


class Cell(StrEnum):
    PAUSED = "paused"
    ANOMALY = "anomaly"
    RECENT_BUDGET_CHANGE = "recent_budget_change"
    RAISE_WITHOUT_GAIN = "raise_without_gain"
    CONVERSION_RATE_DROP = "conversion_rate_drop"
    LATE_CONVERSIONS = "late_conversions"
    NO_DELIVERY = "no_delivery"
    DELIVERY_WITH_VALUE = "delivery_with_value"
    DELIVERY_WITHOUT_VALUE = "delivery_without_value"


ANSWER_ORDER: tuple[Cell, ...] = tuple(Cell)
VERDICT: Mapping[Cell, WorthVerdict] = MappingProxyType({
    Cell.PAUSED: WorthVerdict.NOT_WORTH,
    Cell.ANOMALY: WorthVerdict.INSUFFICIENT,
    Cell.RECENT_BUDGET_CHANGE: WorthVerdict.INSUFFICIENT,
    Cell.RAISE_WITHOUT_GAIN: WorthVerdict.NOT_WORTH,
    Cell.CONVERSION_RATE_DROP: WorthVerdict.INSUFFICIENT,
    Cell.LATE_CONVERSIONS: WorthVerdict.WORTH,
    Cell.NO_DELIVERY: WorthVerdict.NOT_WORTH,
    Cell.DELIVERY_WITH_VALUE: WorthVerdict.WORTH,
    Cell.DELIVERY_WITHOUT_VALUE: WorthVerdict.INSUFFICIENT,
})
RULE_TEXT: Mapping[Cell, str] = MappingProxyType({
    Cell.PAUSED: "狀態暫停",
    Cell.ANOMALY: "一小時原始指標異常",
    Cell.RECENT_BUDGET_CHANGE: "最近三天調過預算",
    Cell.RAISE_WITHOUT_GAIN: "最近一次加額後三天轉換未增加",
    Cell.CONVERSION_RATE_DROP: "最近三天轉換率低於前四天的一半",
    Cell.LATE_CONVERSIONS: "一小時無價值但長窗有轉換",
    Cell.NO_DELIVERY: "一小時沒有曝光或點擊",
    Cell.DELIVERY_WITH_VALUE: "一小時投放且有價值",
    Cell.DELIVERY_WITHOUT_VALUE: "一小時投放但沒有價值",
})


class RuleReason(StrEnum):
    PAUSED = "paused"
    ANOMALY = "anomaly"
    RECENT_BUDGET_CHANGE = "recent_budget_change"
    RAISE_WITHOUT_GAIN = "raise_without_gain"
    CONVERSION_RATE_DROP = "conversion_rate_drop"
    LATE_CONVERSIONS = "late_conversions"
    NO_DELIVERY = "no_delivery"
    DELIVERY_WITH_VALUE = "delivery_with_value"
    DELIVERY_WITHOUT_VALUE = "delivery_without_value"
    INPUT_INVALID = "input_invalid"
    QUERY_NO_RESULT = "query_no_result"
    MISSING_ROW_VALUE = "missing_row_value"
    INVALID_ROW_VALUE = "invalid_row_value"
    MISSING_DAILY_ROWS = "missing_daily_rows"
    NO_DATA_DAY = "no_data_day"
    CROSS_QUERY_CONFLICT = "cross_query_conflict"


def _check_count(name: str, value: object) -> None:
    if not is_count_or_none(value):
        raise ValueError(f"{name} 必須是整數或缺值")


def _check_amount(name: str, value: object) -> None:
    """跟分析端讀取白名單同一支判準(固定兩位小數字串的定義只在 _checks 一份)。"""
    if not is_amount_or_none(value):
        raise ValueError(f"{name} 必須是有限數字、固定兩位小數字串或缺值")


def _check_rows(name: str, rows: object, row_type: type) -> None:
    if not isinstance(rows, tuple) or any(not isinstance(row, row_type) for row in rows):
        raise ValueError(f"{name} 必須是 {row_type.__name__} 的 tuple")


@dataclass(frozen=True)
class Window:
    impressions: int | None
    clicks: int | None
    conversions: int | None
    spend: int | float | str | None
    revenue: int | float | str | None

    def __post_init__(self) -> None:
        for name in ("impressions", "clicks", "conversions"):
            _check_count(name, getattr(self, name))
        for name in ("spend", "revenue"):
            _check_amount(name, getattr(self, name))


@dataclass(frozen=True)
class LongerWindow:
    one_day: Window
    seven_days: Window

    def __post_init__(self) -> None:
        if not isinstance(self.one_day, Window) or not isinstance(self.seven_days, Window):
            raise ValueError("長窗必須有一日與七日 Window")


@dataclass(frozen=True)
class HistoryRow:
    action: str | None
    committed_at: datetime | None

    def __post_init__(self) -> None:
        if self.action is not None and not isinstance(self.action, str):
            raise ValueError("action 必須是字串或缺值")
        if self.committed_at is not None and not isinstance(self.committed_at, datetime):
            raise ValueError("committed_at 必須是時間或缺值")


@dataclass(frozen=True)
class ChangeHistory:
    rows: tuple[HistoryRow, ...]

    def __post_init__(self) -> None:
        _check_rows("history.rows", self.rows, HistoryRow)


@dataclass(frozen=True)
class DailyRow:
    days_ago: int
    impressions: int | None
    clicks: int | None
    conversions: int | None
    spend: int | float | str | None
    revenue: int | float | str | None
    no_data: bool

    def __post_init__(self) -> None:
        if not is_plain_int(self.days_ago):
            raise ValueError("days_ago 必須是整數")
        for name in ("impressions", "clicks", "conversions"):
            _check_count(name, getattr(self, name))
        for name in ("spend", "revenue"):
            _check_amount(name, getattr(self, name))
        if not isinstance(self.no_data, bool):
            raise ValueError("no_data 必須是布林")


@dataclass(frozen=True)
class DailyTrend:
    rows: tuple[DailyRow, ...]

    def __post_init__(self) -> None:
        _check_rows("daily.rows", self.rows, DailyRow)


@dataclass(frozen=True)
class AdjustmentRow:
    days_ago: int
    budget_before: int | None
    budget_after: int | None
    before_conversions: int | None
    after_conversions: int | None

    def __post_init__(self) -> None:
        for name in ("days_ago", "budget_before", "budget_after", "before_conversions",
                     "after_conversions"):
            value = getattr(self, name)
            if (name == "days_ago" and not is_plain_int(value)) or (
                name != "days_ago" and not is_count_or_none(value)
            ):
                raise ValueError(f"{name} 必須是整數" + ("或缺值" if name != "days_ago" else ""))


@dataclass(frozen=True)
class PastAdjustments:
    rows: tuple[AdjustmentRow, ...]

    def __post_init__(self) -> None:
        _check_rows("past.rows", self.rows, AdjustmentRow)


@dataclass(frozen=True)
class RuleEvidence:
    longer: LongerWindow | None = None
    history: ChangeHistory | None = None
    daily: DailyTrend | None = None
    past: PastAdjustments | None = None

    def __post_init__(self) -> None:
        for name, kind in (("longer", LongerWindow), ("history", ChangeHistory),
                           ("daily", DailyTrend), ("past", PastAdjustments)):
            value = getattr(self, name)
            if value is not None and not isinstance(value, kind):
                raise ValueError(f"{name} 型別不正確")


@dataclass(frozen=True)
class RuleDecision:
    verdict: WorthVerdict
    cell: Cell | None
    reason: RuleReason
    query: QueryKind | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.verdict, WorthVerdict) or
                (self.cell is not None and not isinstance(self.cell, Cell)) or
                not isinstance(self.reason, RuleReason) or
                (self.query is not None and not isinstance(self.query, QueryKind))):
            raise ValueError("規則結論欄位型別不正確")
        if self.cell is not None and (
            self.verdict is not VERDICT[self.cell] or self.reason.value != self.cell.value or
            self.query is not None
        ):
            raise ValueError("命中格與結論不一致")
        if self.cell is None and self.verdict is not WorthVerdict.INSUFFICIENT:
            raise ValueError("無格結論必須是證據不足")


def _hit(cell: Cell) -> RuleDecision:
    return RuleDecision(VERDICT[cell], cell, RuleReason(cell.value))


def _insufficient(reason: RuleReason, query: QueryKind | None = None) -> RuleDecision:
    return RuleDecision(WorthVerdict.INSUFFICIENT, None, reason, query)


def segment_rate(rows: tuple[DailyRow, ...]) -> m.Exact:
    """逐日區段的精確轉換率;任一必要欄缺值便回缺資料。"""
    if any(row.conversions is None or row.clicks is None for row in rows):
        return m.Reason.MISSING_DATA
    return m.exact_ratio(sum(row.conversions for row in rows if row.conversions is not None),
                         sum(row.clicks for row in rows if row.clicks is not None))


def _history_decision(history: ChangeHistory, now: datetime) -> RuleDecision | None:
    cutoff = now - timedelta(days=RECENT_DAYS)
    recent = False
    for row in history.rows:
        if row.action != "update_budget":
            continue
        if not is_aware(row.committed_at):
            return _insufficient(RuleReason.MISSING_ROW_VALUE, QueryKind.HISTORY)
        recent |= row.committed_at > cutoff
    return _hit(Cell.RECENT_BUDGET_CHANGE) if recent else None


def _past_decision(past: PastAdjustments) -> RuleDecision | None:
    if any(_invalid_adjustment_row(row) for row in past.rows):
        return _insufficient(RuleReason.INVALID_ROW_VALUE, QueryKind.PAST)
    for row in past.rows:
        if row.budget_before is None or row.budget_after is None:
            return _insufficient(RuleReason.MISSING_ROW_VALUE, QueryKind.PAST)
        if row.budget_after <= row.budget_before:
            continue
        if row.before_conversions is None or row.after_conversions is None:
            return _insufficient(RuleReason.MISSING_ROW_VALUE, QueryKind.PAST)
        change = m.exact_change(row.before_conversions, row.after_conversions)
        if isinstance(change, Fraction) and change <= GAIN_THRESHOLD:
            return _hit(Cell.RAISE_WITHOUT_GAIN)
        if change is not m.Reason.NO_DENOMINATOR and isinstance(change, m.Reason):
            return _insufficient(RuleReason.INVALID_ROW_VALUE, QueryKind.PAST)
        break
    return None


def _invalid_adjustment_row(row: AdjustmentRow) -> bool:
    return row.days_ago < 0 or any(value is not None and value < 0 for value in (
        row.budget_before, row.budget_after, row.before_conversions, row.after_conversions
    ))


def _invalid_daily_row(row: DailyRow) -> bool:
    values = (row.impressions, row.clicks, row.conversions,
              Fraction(row.spend) if row.spend is not None else None,
              Fraction(row.revenue) if row.revenue is not None else None)
    return (any(value is not None and value < 0 for value in values) or
            (row.impressions is not None and row.clicks is not None and
             row.clicks > row.impressions) or
            (row.clicks is not None and row.conversions is not None and
             row.conversions > row.clicks))


def _daily_decision(daily: DailyTrend) -> RuleDecision | None:  # noqa: PLR0911 - 各缺證據原因需分流
    rows = daily.rows
    if any(_invalid_daily_row(row) for row in rows):
        return _insufficient(RuleReason.INVALID_ROW_VALUE, QueryKind.DAILY)
    if len(rows) != DAILY_DAYS or {row.days_ago for row in rows} != set(range(1, DAILY_DAYS + 1)):
        return _insufficient(RuleReason.MISSING_DAILY_ROWS, QueryKind.DAILY)
    if any(row.no_data for row in rows):
        return _insufficient(RuleReason.NO_DATA_DAY, QueryKind.DAILY)
    if any(row.clicks is None or row.conversions is None for row in rows):
        return _insufficient(RuleReason.MISSING_ROW_VALUE, QueryKind.DAILY)
    recent = tuple(row for row in rows if row.days_ago <= RECENT_DAYS)
    earlier = tuple(row for row in rows if row.days_ago > RECENT_DAYS)
    drop = m.exact_change(segment_rate(earlier), segment_rate(recent))
    if isinstance(drop, Fraction) and drop < DROP_THRESHOLD:
        return _hit(Cell.CONVERSION_RATE_DROP)
    if drop is not m.Reason.NO_DENOMINATOR and isinstance(drop, m.Reason):
        return _insufficient(RuleReason.MISSING_ROW_VALUE, QueryKind.DAILY)
    return None


def _final_decision(worth: WorthInput, longer: LongerWindow) -> RuleDecision:
    assert worth.impressions is not None and worth.clicks is not None  # noqa: S101 - 異常輸入已先排除
    assert worth.conversions is not None and worth.revenue is not None  # noqa: S101 - 同上
    if worth.clicks > 0 and worth.conversions == 0 and worth.revenue == 0:
        windows = (longer.one_day, longer.seven_days)
        if any(window.conversions is not None and window.conversions > 0 for window in windows):
            return _hit(Cell.LATE_CONVERSIONS)
        if any(window.conversions is None for window in windows):
            return _insufficient(RuleReason.MISSING_ROW_VALUE, QueryKind.LONGER)
    if worth.impressions == 0 or worth.clicks == 0:
        return _hit(Cell.NO_DELIVERY)
    if worth.conversions > 0 or worth.revenue > 0:
        return _hit(Cell.DELIVERY_WITH_VALUE)
    return _hit(Cell.DELIVERY_WITHOUT_VALUE)


def decide(worth: WorthInput | None, evidence: RuleEvidence, now: datetime) -> RuleDecision:
    """由上往下先命中,齊值分母零才略過第 4/5 條。"""
    if not is_aware(now):
        raise ValueError("now 必須帶時區")
    if worth is None:
        return _insufficient(RuleReason.INPUT_INVALID)
    if worth.status is CampaignStatus.PAUSED:
        return _hit(Cell.PAUSED)
    if is_anomalous(worth):
        return _hit(Cell.ANOMALY)
    for query, value in ((QueryKind.LONGER, evidence.longer),
                         (QueryKind.HISTORY, evidence.history),
                         (QueryKind.DAILY, evidence.daily), (QueryKind.PAST, evidence.past)):
        if value is None:
            return _insufficient(RuleReason.QUERY_NO_RESULT, query)
    assert evidence.longer is not None and evidence.history is not None  # noqa: S101 - 四查詢已核對
    assert evidence.daily is not None and evidence.past is not None  # noqa: S101 - 同上
    return (_history_decision(evidence.history, now) or _past_decision(evidence.past)
            or _daily_decision(evidence.daily) or _final_decision(worth, evidence.longer))
