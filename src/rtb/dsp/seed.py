"""展示種子只寫 UTC 日桶、完整日樣板與預算操作;視窗與過去加額由 DSP 讀取時從同一批日桶推算。

只寫模擬平台;不讀時鐘(「現在」由呼叫端給,跟過去操作的提交時間同一個基準)。金額照 DSP 自己的
整數分寫法(rtb.dsp.store 的 cents_of/money_text),不另寫一套加總或格式化(代碼審 r1 架構對齊)。
"""

from collections.abc import Mapping
from dataclasses import astuple, dataclass
from datetime import datetime

from rtb.dsp.errors import MetricsNotFound
from rtb.dsp.store import (
    AMOUNT_FIELDS,
    DAILY_DAYS,
    METRIC_FIELDS,
    CampaignStore,
    DailyRow,
    HistorySeed,
    PastBudgetChange,
    cents_of,
    money_text,
)

__all__ = ["DEMO_PROFILE", "AdjustmentSeed", "DayFigures", "HistoryProfile", "PastBudgetChange",
           "consistency_problems", "seed_platform_history"]


@dataclass(frozen=True)
class DayFigures:
    """一段期間的成效五欄(曝光、點擊、轉換、花費、營收)。"""

    impressions: int
    clicks: int
    conversions: int
    spend: float | str
    revenue: float | str

    def as_fields(self) -> dict[str, int | float | str | None]:
        return dict(zip(METRIC_FIELDS, astuple(self), strict=True))


@dataclass(frozen=True)
class AdjustmentSeed:
    """一筆過去預算操作;前值取操作當時的廣告現況。"""

    days_ago: int
    budget_after: int


@dataclass(frozen=True)
class HistoryProfile:
    """一個廣告的歷史:7 天逐日(第 1 天 = 昨天;None 是那天缺資料)、由新到舊的過去調整,與選填的
    完整日樣板(種完之後每個新完成的 UTC 日由它推出;不給就是之後的日子沒資料,不照抄前一天)。"""

    daily: tuple[DayFigures | None, ...]
    adjustments: tuple[AdjustmentSeed, ...] = ()
    template: DayFigures | None = None


# 展示情境的種子(驅動程式對每個廣告都種這一份):每天一樣的成效、沒有過去調整(有資料、零筆)。
# 沒有過去操作,所以 F1 到 F7 對平台寫入的既有斷言(寫入恰好一次、全平台只有那一筆)不受影響。
# 樣板同一天的成效:展示跨 UTC 午夜(台北 08:00)後新完成的那天照樣有資料([S1426])
_DEMO_DAY = DayFigures(impressions=12_000, clicks=288, conversions=24, spend=12.0, revenue=120.0)
DEMO_PROFILE = HistoryProfile(daily=(_DEMO_DAY,) * DAILY_DAYS, template=_DEMO_DAY)


def _entry(campaign_id: str, profile: HistoryProfile) -> HistorySeed:
    """只種日桶與樣板,1d/7d 由同一批分值推算。"""
    return HistorySeed(campaign_id,
                       [None if day is None else day.as_fields() for day in profile.daily],
                       None if profile.template is None else profile.template.as_fields())


def seed_platform_history(store: CampaignStore, profiles: Mapping[str, HistoryProfile],
                          now: datetime) -> None:
    """全平台同交易種逐日與預算操作;視窗和前後對照由日桶及操作紀錄推算。"""
    changes = [PastBudgetChange(campaign_id, a.days_ago, a.budget_after)
               for campaign_id, profile in profiles.items() for a in profile.adjustments]
    store.seed_history([_entry(campaign_id, profile) for campaign_id, profile in profiles.items()],
                       changes, now)


def _window(store: CampaignStore, campaign_id: str,
            window: str) -> dict[str, int | str | None] | None:
    try:
        record = store.get_metrics(campaign_id, window)
    except MetricsNotFound:
        return None
    return {name: getattr(record, name) for name in METRIC_FIELDS}


def _total(rows: list[DailyRow]) -> dict[str, int | str | None]:
    """逐日列的五欄合計(金額以整數分加、照 DSP 寫法格式化);任一列那欄缺值,那欄就是 None。"""
    total: dict[str, int | str | None] = {}
    for name in METRIC_FIELDS:
        values = [getattr(row, name) for row in rows]
        if any(value is None for value in values):
            total[name] = None
        elif name in AMOUNT_FIELDS:
            total[name] = money_text(sum(cents_of(value) for value in values))
        else:
            total[name] = sum(values)
    return total


def consistency_problems(store: CampaignStore, campaign_id: str, now: datetime) -> list[str]:
    """[S1127] 的一致性,回對不上的地方(空的就是一致):逐日第 1 天等於 1 天窗(那天沒資料時 1 天窗
    五欄 null)、有資料的天合計等於 7 天窗(全沒資料時沒有 7 天窗)。

    逐日與兩窗都由 DSP 同一批日桶在讀取時推算,這裡核對的是兩條讀取投影(逐日端點與視窗端點)對得上。
    舊版第三條「最近加額找得到對應操作紀錄」在單一來源下恆真(過去調整本來就從操作紀錄取),
    代碼審 r1 鏡頭1 指出後撤掉。"""
    if now.utcoffset() is None:
        raise ValueError("now 必須帶時區")
    problems = []
    daily = store.get_daily(campaign_id)
    first = dict.fromkeys(METRIC_FIELDS) if daily[0].no_data else _total(daily[:1])
    if first != _window(store, campaign_id, "1d"):
        problems.append("逐日第 1 天不等於 1 天窗")
    kept = [row for row in daily if not row.no_data]
    if (_total(kept) if kept else None) != _window(store, campaign_id, "7d"):
        problems.append("7 天加總不等於 7 天窗")
    return problems
