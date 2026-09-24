"""展示種子的逐日成效與過去調整(Phase 13 增量 2,計劃〈新增的兩種模擬資料〉,[S1127] [S1153])。

AI 追加查詢用的兩種模擬資料只由這裡寫,頁面標「模擬資料」;不從真實操作紀錄推算(模擬平台的成效本來
就是種出來的,推算出來的前後對照一樣是假的)。這裡負責讓幾份資料互相一致,不讓 AI 看到自相矛盾的數字:

- 逐日成效第 1 天等於 1 天窗、7 天加總等於 7 天窗(缺資料的天不進加總)。
- 過去調整的筆數等於操作歷史裡 **3 天以前**的預算調整筆數:每一筆過去調整,在操作歷史的同樣幾天前寫
  一筆預算調整(經 DSP 只給種子用的過去日期寫法,全平台一次、最舊的先寫)。情境中途另一個寫入者或
  執行端真的寫的那一筆落在最近 3 天內,不影響這條。

只寫模擬平台;不讀時鐘(「現在」由呼叫端給,跟過去操作的提交時間同一個基準)。
"""

from collections.abc import Mapping
from dataclasses import astuple, dataclass
from datetime import datetime, timedelta

from rtb.dsp.errors import MetricsNotFound
from rtb.dsp.store import (
    DAILY_DAYS,
    METRIC_FIELDS,
    MIN_ADJUSTMENT_AGE_DAYS,
    CampaignStore,
    PastAdjustment,
    PastBudgetChange,
    commit_text,
)

__all__ = ["DEMO_PROFILE", "AdjustmentSeed", "DayFigures", "HistoryProfile", "PastBudgetChange",
           "consistency_problems", "seed_platform_history"]


@dataclass(frozen=True)
class DayFigures:
    """一段期間的成效五欄(曝光、點擊、轉換、花費、營收)。"""

    impressions: int
    clicks: int
    conversions: int
    spend: float
    revenue: float

    def as_fields(self) -> dict[str, float | None]:
        return dict(zip(METRIC_FIELDS, astuple(self), strict=True))


@dataclass(frozen=True)
class AdjustmentSeed:
    """一筆過去調整:幾天前、調整前後的預算、調整前與後各 3 天的成效總量。"""

    days_ago: int
    budget_before: int
    budget_after: int
    before: DayFigures
    after: DayFigures


@dataclass(frozen=True)
class HistoryProfile:
    """一個廣告的歷史:7 天逐日(第 1 天 = 昨天;None 是那天缺資料),與由新到舊的過去調整。"""

    daily: tuple[DayFigures | None, ...]
    adjustments: tuple[AdjustmentSeed, ...] = ()


# 展示情境的種子(驅動程式對每個廣告都種這一份):每天一樣的成效、沒有過去調整(有資料、零筆)。
# 沒有過去操作,所以 F1 到 F7 對平台寫入的既有斷言(寫入恰好一次、全平台只有那一筆)不受影響
_DEMO_DAY = DayFigures(impressions=12_000, clicks=288, conversions=24, spend=12.0, revenue=120.0)
DEMO_PROFILE = HistoryProfile(daily=(_DEMO_DAY,) * DAILY_DAYS)


def _total(days: list[DayFigures]) -> dict[str, float | None]:
    return {name: sum(getattr(day, name) for day in days) for name in METRIC_FIELDS}


def _seed_one(store: CampaignStore, campaign_id: str, profile: HistoryProfile) -> None:
    days = [day for day in profile.daily if day is not None]
    first = profile.daily[0]
    if first is not None:
        store.seed_metrics(campaign_id, "1d", **first.as_fields())
    if days:
        store.seed_metrics(campaign_id, "7d", **_total(days))
    store.seed_daily(campaign_id, [None if day is None else day.as_fields()
                                   for day in profile.daily])
    store.seed_past_adjustments(campaign_id, [
        PastAdjustment(a.days_ago, a.budget_before, a.budget_after, a.before.as_fields(),
                       a.after.as_fields()) for a in profile.adjustments])


def seed_platform_history(store: CampaignStore, profiles: Mapping[str, HistoryProfile],
                          now: datetime) -> None:
    """替全平台的廣告種歷史:各自的窗與逐日、過去調整,再一次把全部過去調整寫進操作歷史。廣告要先建好;
    平台還不能有任何操作(過去日期寫法的前提)。"""
    changes = []
    for campaign_id, profile in profiles.items():
        _seed_one(store, campaign_id, profile)
        changes += [PastBudgetChange(campaign_id, a.days_ago, a.budget_after)
                    for a in profile.adjustments]
    if changes:
        store.seed_past_operations(changes, now)


def _window(store: CampaignStore, campaign_id: str, window: str) -> dict[str, float | None] | None:
    try:
        record = store.get_metrics(campaign_id, window)
    except MetricsNotFound:
        return None
    return {name: getattr(record, name) for name in METRIC_FIELDS}


def consistency_problems(store: CampaignStore, campaign_id: str, now: datetime) -> list[str]:
    """[S1127] 的三條一致性,回對不上的地方(空的就是一致)。"""
    problems = []
    daily = store.get_daily(campaign_id)
    first = None if daily[0].no_data else {name: getattr(daily[0], name) for name in METRIC_FIELDS}
    if first != _window(store, campaign_id, "1d"):
        problems.append("逐日第 1 天不等於 1 天窗")
    kept = [DayFigures(*(getattr(row, name) for name in METRIC_FIELDS))
            for row in daily if not row.no_data]
    if (_total(kept) if kept else None) != _window(store, campaign_id, "7d"):
        problems.append("7 天加總不等於 7 天窗")
    cutoff = commit_text(now - timedelta(days=MIN_ADJUSTMENT_AGE_DAYS))
    old = [h for h in store.history(campaign_id)
           if h.action == "update_budget" and h.committed_at <= cutoff]
    if len(old) != len(store.get_past_adjustments(campaign_id)):
        problems.append("過去調整筆數不等於操作歷史裡 3 天以前的預算調整筆數")
    return problems
