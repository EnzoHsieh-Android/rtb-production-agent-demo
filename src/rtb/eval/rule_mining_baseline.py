"""規則模式探索的效果計算、配對、彙總、保留側判定與窮舉前 K(Phase 15 增量 1,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉〈封閉條件語彙、彙總與窮舉基準〉)。

- 每個加額事件以操作的 UTC 日為 D,只取 D-3..D-1 與 D+1..D+3 六個完整日(D 本身不算)。視窗超出
  觀察期、同一廣告另一筆調整(加額或降額)的 D±3 視窗與本事件相交(相距 ≤ 6 天)、資料異常、缺值
  (含 no_data)、轉換率分母為零都不推斷,逐原因計數。
- 轉換率 = 轉換 / 點擊,一律經 `rtb.domain.metrics.exact_ratio` 取精確分數;組的變化是後三日率減
  前三日率的**絕對差**,配對差值是加額組變化減對照組變化。判斷與核對不讀任何捨入字串。
- 對照只取同一側、**整個觀察期沒有任何預算調整**(加額或降額,和事件側對稱)的廣告;
  配對須同側、同 UTC 日 D、同前三日轉換率區間、同投放規模桶。每條條件先按
  `(D, 加額廣告編號位元組, committed_at, 操作識別碼)` 處理事件,對照按編號位元組
  升序取第一支未用者,每支對照在該條件至多用一次;找不到記「無對照」。
- 差值為零是平手:不算支持也不算反例。有方向配對數 = 正差 + 負差;支持比例、平均差值分母、樣本下限、
  兩側相異廣告數與相異日期數都只看有方向配對。
- 窮舉基準:兩方向合併,依 Wilson 95% 下界(直接呼叫 `rtb.eval.scoring.wilson_lower`)、方向化平均
  差值、有方向配對數、條件鍵、方向代碼排序;同條件兩方向只留較高者,取前 K。
- 純函式、標準函式庫;不匯入 DSP、模型閘道或模型用戶端。這是可重算的合成比較,不是因果證明。
"""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

from rtb.domain import metrics as m
from rtb.eval import rule_mining_vocab as v
from rtb.eval.rule_mining_history import Ad, Adjustment, DayBucket, History
from rtb.eval.scoring import wilson_lower

INCOMPLETE_WINDOW = "incomplete_window"
OVERLAPPING_ADJUSTMENT = "overlapping_adjustment"
ANOMALOUS_DATA = "anomalous_data"
MISSING_VALUE = "missing_value"
ZERO_DENOMINATOR = "zero_denominator"
NO_CONTROL = "no_control"
# 保留側判定的原因
INSUFFICIENT_SAMPLE = "insufficient_sample"
LOW_SUPPORT = "low_support"
WRONG_SIGN = "wrong_sign"
UNMEASURED = "unmeasured"

_REASON_FROM_METRIC = {m.Reason.NO_DENOMINATOR: ZERO_DENOMINATOR,
                       m.Reason.MISSING_DATA: MISSING_VALUE,
                       m.Reason.INVALID_DATA: ANOMALOUS_DATA}


@dataclass(frozen=True)
class _Window:
    pre_rate: Fraction
    change: Fraction  # 後三日率 - 前三日率(絕對差)
    pre_spend: int  # 前三日花費(分)


@dataclass(frozen=True)
class RaiseEvent:
    ad_id: str
    op_id: str
    committed_at: datetime
    day: date  # D(UTC)
    clauses: frozenset[v.Clause]  # 這筆事件在四個欄位的值
    pre_band: str
    scale: str
    change: Fraction

    def matches(self, key: v.ConditionKey) -> bool:
        return all(clause in self.clauses for clause in key)

    @property
    def order(self) -> tuple[date, bytes, datetime, str]:
        return self.day, self.ad_id.encode(), self.committed_at, self.op_id


@dataclass(frozen=True)
class Control:
    ad_id: str
    change: Fraction


@dataclass(frozen=True)
class Pair:
    op_id: str
    raised_ad: str
    control_ad: str
    day: date
    diff: Fraction  # 加額組變化 - 對照組變化


@dataclass(frozen=True)
class ConditionStats:
    key: v.ConditionKey
    events: int  # 符合條件的可推斷加額事件
    pairs: int  # 總配對 = 正差 + 負差 + 平手
    positive: int
    negative: int
    ties: int
    raised_ads: int  # 有方向配對裡的相異加額廣告
    control_ads: int  # 有方向配對裡的相異對照廣告
    dates: int  # 有方向配對裡的相異 UTC 日
    diff_sum: Fraction  # 有方向差值合計(精確)
    no_control: int

    @property
    def directed(self) -> int:
        return self.positive + self.negative


@dataclass(frozen=True)
class SideSummary:
    events: int  # 可推斷的加額事件
    exclusions: Mapping[str, int]  # 事件層排除原因(無對照另在每條件的 no_control)
    stats: Mapping[v.ConditionKey, ConditionStats]


@dataclass(frozen=True)
class Holdout:
    kept: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Ranked:
    key: v.ConditionKey
    direction: str
    support: int
    counter: int
    wilson: float
    directional_mean: Fraction
    directed: int

    @property
    def sort_key(self) -> tuple[float, Fraction, int, v.ConditionKey, str]:
        return -self.wilson, -self.directional_mean, -self.directed, self.key, self.direction


@dataclass(frozen=True)
class Reach:
    """分母可達性:只看有方向配對數與兩側相異廣告數,不看正負差。"""

    directed: int
    raised_ads: int
    control_ads: int

    @property
    def meets(self) -> bool:
        return (self.directed >= v.MIN_DIRECTED and self.raised_ads >= v.MIN_DISTINCT_ADS
                and self.control_ads >= v.MIN_DISTINCT_ADS)


# ---- 視窗與事件 ----
def _is_raise(adjustment: Adjustment) -> bool:
    return adjustment.budget_after_cents > adjustment.budget_before_cents


def _utc_day(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def _complete(history: History, day: date) -> bool:
    last = history.start + timedelta(days=history.day_count - 1)
    reach = timedelta(days=v.WINDOW_DAYS)
    return history.start <= day - reach and day + reach <= last


def _bucket_problem(buckets: Sequence[DayBucket | None]) -> str | None:
    """資料異常優先於缺值(同領域層指標的順序)。"""
    rows = [(b.impressions, b.clicks, b.conversions, b.spend_cents, b.revenue_cents)
            for b in buckets if b is not None and not b.no_data]
    present = [x for row in rows for x in row if x is not None]
    if any(type(x) is not int or x < 0 for x in present) or any(
            imp is not None and clk is not None and clk > imp for imp, clk, *_ in rows):
        return ANOMALOUS_DATA
    if len(rows) < len(buckets) or len(present) < 5 * len(rows):  # 每桶五個欄位
        return MISSING_VALUE
    return None


def _window(days: Mapping[date, DayBucket], day: date) -> _Window | str:
    offsets = range(1, v.WINDOW_DAYS + 1)
    pre = [days.get(day - timedelta(days=i)) for i in reversed(offsets)]
    post = [days.get(day + timedelta(days=i)) for i in offsets]
    problem = _bucket_problem(pre + post)
    if problem is not None:
        return problem
    before = [b for b in pre if b is not None]
    after = [b for b in post if b is not None]
    pre_rate = m.exact_ratio(sum(b.conversions or 0 for b in before),
                             sum(b.clicks or 0 for b in before))
    post_rate = m.exact_ratio(sum(b.conversions or 0 for b in after),
                              sum(b.clicks or 0 for b in after))
    for rate in (pre_rate, post_rate):
        if isinstance(rate, m.Reason):
            return _REASON_FROM_METRIC[rate]
    assert isinstance(pre_rate, Fraction) and isinstance(post_rate, Fraction)  # noqa: S101
    return _Window(pre_rate, post_rate - pre_rate, sum(b.spend_cents or 0 for b in before))


def _event(history: History, ad: Ad, adjustment: Adjustment,
           days: Mapping[date, DayBucket]) -> RaiseEvent | str:
    day = _utc_day(adjustment.committed_at)
    if not _complete(history, day):
        return INCOMPLETE_WINDOW
    if any(other is not adjustment and abs((_utc_day(other.committed_at) - day).days)
           <= v.OVERLAP_DAYS for other in ad.adjustments):
        return OVERLAPPING_ADJUSTMENT
    window = _window(days, day)
    if isinstance(window, str):
        return window
    before = adjustment.budget_before_cents
    raise_pct = m.exact_ratio(adjustment.budget_after_cents - before, before)
    spend_ratio = m.exact_ratio(window.pre_spend, v.WINDOW_DAYS * before)
    if not (isinstance(raise_pct, Fraction) and isinstance(spend_ratio, Fraction)):
        return ANOMALOUS_DATA
    pre_band = v.band(v.PRE_CVR, window.pre_rate)
    clauses = frozenset({(v.DAY_TYPE, v.day_type(day)), (v.PRE_CVR, pre_band),
                         (v.RAISE_PCT, v.band(v.RAISE_PCT, raise_pct)),
                         (v.SPEND_RATIO, v.band(v.SPEND_RATIO, spend_ratio))})
    return RaiseEvent(ad.ad_id, adjustment.op_id, adjustment.committed_at, day, clauses,
                      pre_band, v.scale_bucket(window.pre_spend), window.change)


def _side_ads(history: History, side: Collection[str]) -> list[Ad]:
    return sorted((ad for ad in history.ads if ad.ad_id in side), key=lambda a: a.ad_id.encode())


def side_events(history: History, side: Collection[str]
                ) -> tuple[tuple[RaiseEvent, ...], dict[str, int]]:
    """一側的可推斷加額事件(按固定次序)與逐原因排除計數。"""
    events, excluded = [], Counter[str]()
    for ad in _side_ads(history, side):
        days = {bucket.day: bucket for bucket in ad.days}
        for adjustment in filter(_is_raise, ad.adjustments):
            result = _event(history, ad, adjustment, days)
            if isinstance(result, str):
                excluded[result] += 1
            else:
                events.append(result)
    return tuple(sorted(events, key=lambda e: e.order)), dict(sorted(excluded.items()))


ControlIndex = Mapping[tuple[date, str, str], tuple[Control, ...]]


def control_index(history: History, side: Collection[str],
                  days: Iterable[date] | None = None) -> ControlIndex:
    """同側、整期沒有任何調整的對照:(D, 前三日轉換率區間, 規模桶) → 按編號位元組升序的對照。"""
    wanted = sorted(set(days) if days is not None else {
        history.start + timedelta(days=i) for i in range(history.day_count)})
    index: dict[tuple[date, str, str], list[Control]] = {}
    for ad in _side_ads(history, side):
        if ad.adjustments:  # 整期有任何調整(加額或降額)都不當對照(v2,代碼審 c_5)
            continue
        by_day = {bucket.day: bucket for bucket in ad.days}
        for day in wanted:
            window = _window(by_day, day) if _complete(history, day) else INCOMPLETE_WINDOW
            if isinstance(window, str):
                continue
            slot = (day, v.band(v.PRE_CVR, window.pre_rate), v.scale_bucket(window.pre_spend))
            index.setdefault(slot, []).append(Control(ad.ad_id, window.change))
    return {slot: tuple(controls) for slot, controls in index.items()}


def pair_condition(key: v.ConditionKey, events: Iterable[RaiseEvent], index: ControlIndex
                   ) -> tuple[tuple[Pair, ...], int]:
    """一條條件的不放回配對:(配對, 找不到對照的事件數)。"""
    used: set[str] = set()
    pairs, missing = [], 0
    for event in sorted((e for e in events if e.matches(key)), key=lambda e: e.order):
        control = next((c for c in index.get((event.day, event.pre_band, event.scale), ())
                        if c.ad_id not in used), None)
        if control is None:
            missing += 1
            continue
        used.add(control.ad_id)
        pairs.append(Pair(event.op_id, event.ad_id, control.ad_id, event.day,
                          event.change - control.change))
    return tuple(pairs), missing


def stats_of(key: v.ConditionKey, events: int, pairs: Sequence[Pair], no_control: int
             ) -> ConditionStats:
    directed = [p for p in pairs if p.diff != 0]
    positive = sum(1 for p in directed if p.diff > 0)
    return ConditionStats(
        key=key, events=events, pairs=len(pairs), positive=positive,
        negative=len(directed) - positive, ties=len(pairs) - len(directed),
        raised_ads=len({p.raised_ad for p in directed}),
        control_ads=len({p.control_ad for p in directed}),
        dates=len({p.day for p in directed}), diff_sum=sum((p.diff for p in directed),
                                                           Fraction(0)),
        no_control=no_control)


def summarize(history: History, side: Collection[str]) -> SideSummary:
    """一側逐條件(封閉全集)的彙總。"""
    events, excluded = side_events(history, side)
    index = control_index(history, side, {e.day for e in events})
    stats = {}
    for key in v.all_conditions():
        matching = [e for e in events if e.matches(key)]
        pairs, missing = pair_condition(key, matching, index)
        stats[key] = stats_of(key, len(matching), pairs, missing)
    return SideSummary(events=len(events), exclusions=excluded, stats=stats)


# ---- 下限、方向、保留側 ----
def reach_of(stats: ConditionStats) -> Reach:
    return Reach(stats.directed, stats.raised_ads, stats.control_ads)


def meets_floor(stats: ConditionStats) -> bool:
    return reach_of(stats).meets


def directional(stats: ConditionStats, direction: str) -> tuple[int, int, Fraction | None]:
    """(支持, 反例, 方向化精確平均差值);改善的支持是正差,未改善相反。

    沒有有方向配對時平均是 None。"""
    if direction == v.IMPROVE:
        support, counter, sign = stats.positive, stats.negative, 1
    elif direction == v.NOT_IMPROVE:
        support, counter, sign = stats.negative, stats.positive, -1
    else:
        raise v.VocabularyError("unknown_direction")
    mean = sign * stats.diff_sum / stats.directed if stats.directed else None
    return support, counter, mean


def holdout_verdict(stats: ConditionStats, direction: str) -> Holdout:
    """保留側:有方向配對與兩側相異廣告達下限、支持比例 ≥ 3/5、方向化平均差值 > 0 才保留。"""
    support, _, mean = directional(stats, direction)
    reasons = [] if meets_floor(stats) else [INSUFFICIENT_SAMPLE]
    if mean is None:
        reasons.append(UNMEASURED)
    else:
        if Fraction(support, stats.directed) < v.HOLDOUT_SUPPORT:
            reasons.append(LOW_SUPPORT)
        if mean <= 0:
            reasons.append(WRONG_SIGN)
    return Holdout(kept=not reasons, reasons=tuple(reasons))


# ---- 窮舉前 K ----
def _ranked(stats: ConditionStats, direction: str) -> Ranked:
    support, counter, mean = directional(stats, direction)
    assert mean is not None  # noqa: S101 - 只對達下限(有方向配對 > 0)的條件呼叫
    return Ranked(stats.key, direction, support, counter,
                  wilson_lower(support, stats.directed), mean, stats.directed)


def rank(stats: Mapping[v.ConditionKey, ConditionStats]) -> tuple[Ranked, ...]:
    """達下限的條件各留排序較高的一個方向,再全域排序。"""
    best = [min((_ranked(s, d) for d in v.DIRECTIONS), key=lambda r: r.sort_key)
            for s in stats.values() if meets_floor(s)]
    return tuple(sorted(best, key=lambda r: r.sort_key))


def top_k(stats: Mapping[v.ConditionKey, ConditionStats], k: int = v.K) -> tuple[Ranked, ...]:
    return rank(stats)[:k]
