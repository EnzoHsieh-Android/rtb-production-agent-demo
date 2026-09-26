"""規則模式探索的合成歷史:資料型別、固定種子生成器、評估集文字與雜湊、探索/保留切分、埋入真相清單
(Phase 15 增量 1,計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉)。

- 只收三個固定種子(`rule_mining_vocab.SEEDS`);720 支虛構廣告、35 個連續 UTC 日,每支每天一個
  完整日桶(曝光、點擊、轉換、花費、營收,金額整數分),加額廣告另有含調整前後日預算與 `committed_at`
  的操作。計數照漏斗造(轉換 ≤ 點擊 ≤ 曝光)、當天花費不超過當天日預算;少數廣告有一天 `no_data`。
- 真相清單 `TRUTH` 只給評估器:生成器照它在加額後三天改轉換率,條件的欄位值用評估器同一支語彙函式
  從已生成的前三日資料算(真模式與切點精確對齊,所以召回只是可精確表示時的上界)。誘餌兩類:只在
  探索側埋入的偶合、與真模式相關但本身沒有效果。真相不進評估集文字,也不能進模型提示。
- 另刻意放少量會被排除的事件(前三日或後三日超出觀察期、視窗內另有調整、視窗碰到 no_data),讓排除
  計數有東西可數。
- `render` 產出固定格式文字,`data_sha256` 是它的雜湊;三批的預期雜湊寫死在 `EXPECTED_DATA_SHA256`,
  改生成器就得連版本、雜湊一起換並記理由。合成資料只證流程可運作,不給統計或因果保證。
"""

import hashlib
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from types import MappingProxyType

from rtb.eval import rule_mining_vocab as v

START = date(2026, 8, 3)  # 週一
DAYS = 35
CLOCK = datetime(2026, 9, 7, tzinfo=UTC)  # 評估時鐘:最後一個 UTC 日結束
N_ADS = 720
RAISED_ADS = 360

TRUE_PATTERN = "true_pattern"
DECOY_EXPLORE_ONLY = "decoy_explore_only"
DECOY_CORRELATED = "decoy_correlated"
LIFT, DROP = 1.6, 0.5  # 真模式在加額後三天對轉換率的乘數

# 首次生成後寫死;生成器任何改動都會讓它對不上,須換生成版本與評估版本並記理由
EXPECTED_DATA_SHA256: Mapping[int, str] = MappingProxyType({
    15001: "f820e1a486d8b35d1d81b55c37ce6ed5bb96a9701b0d898020f559fd1adb9f98",
    15002: "2a9e4aac0f4ec0209bfa6e4f7124e5cefb8bcdb259d89886d0a3e856a5c0a9e3",
    15003: "ba7d6ebc8a3a8b4d9c79334a1e2ab5844842eca74ab2c7fdecec90a529c44a2f",
})


@dataclass(frozen=True)
class DayBucket:
    day: date  # UTC 日
    impressions: int | None
    clicks: int | None
    conversions: int | None
    spend_cents: int | None
    revenue_cents: int | None
    no_data: bool = False


@dataclass(frozen=True)
class Adjustment:
    op_id: str
    committed_at: datetime  # UTC
    budget_before_cents: int  # 日預算
    budget_after_cents: int


@dataclass(frozen=True)
class Ad:
    ad_id: str
    daily_budget_cents: int  # 觀察期第一天的日預算
    days: tuple[DayBucket, ...]
    adjustments: tuple[Adjustment, ...]


@dataclass(frozen=True)
class History:
    seed: int
    generator_version: str
    start: date
    day_count: int
    ads: tuple[Ad, ...]


@dataclass(frozen=True)
class TruthItem:
    kind: str
    clauses: v.ConditionKey
    direction: str
    note: str

    @property
    def key(self) -> v.NormalizedKey:
        return v.normalized_key(self.clauses, self.direction)


TRUTH = (
    TruthItem(TRUE_PATTERN, ((v.RAISE_PCT, "raise_pct:band_2"), (v.SPEND_RATIO,
                                                                "spend_ratio:band_3")),
              v.IMPROVE, "預算幾乎花滿的廣告中度加額,後三天轉換率變好"),
    TruthItem(TRUE_PATTERN, ((v.RAISE_PCT, "raise_pct:band_3"),), v.NOT_IMPROVE,
              "大幅加額買到較差的流量,後三天轉換率變差"),
    TruthItem(DECOY_EXPLORE_ONLY, ((v.DAY_TYPE, v.WEEKEND), (v.RAISE_PCT, "raise_pct:band_1")),
              v.IMPROVE, "只在探索側埋入的偶合:保留側沒有這個效果"),
    TruthItem(DECOY_CORRELATED, ((v.SPEND_RATIO, "spend_ratio:band_1"),), v.NOT_IMPROVE,
              "花費比低的廣告多半被大幅加額而看似變差;本身沒有獨立效果"),
)


@dataclass(frozen=True)
class Split:
    explore: frozenset[str]
    holdout: frozenset[str]


def split(ad_ids: Iterable[str]) -> Split:
    """按廣告編號 UTF-8 位元組的 SHA-256 升序(同雜湊再比編號位元組),前 floor(N/2) 支探索、其餘
    保留(奇數多出者進保留)。同一廣告的全部日期與對照資格只在一側。"""
    order = sorted(set(ad_ids), key=lambda a: (hashlib.sha256(a.encode()).digest(), a.encode()))
    half = len(order) // 2
    return Split(explore=frozenset(order[:half]), holdout=frozenset(order[half:]))


# ---- 生成器 ----
_CVR = (0.012, 0.033, 0.075)  # 轉換率等級:落在 pre_cvr 三個區間的中段
_UTIL = (0.45, 0.75, 0.96)  # 日預算花用比等級:落在 spend_ratio 三個區間的中段
_BUDGETS = ((1500, 2500), (8000, 16000), (40000, 80000))  # 日預算(分):三個規模桶
# 加額幅度(基點)各區間的候選值,離切點夠遠
_RAISE_BP = ((1000, 1200, 1500, 1800), (2500, 3000, 3500, 4000, 4500), (6000, 7500, 9000, 10000))
# 花用比等級 → 加額幅度區間的機率(低花用比多半被大幅加額:相關誘餌的來源)
_RAISE_BAND_WEIGHTS = ((15, 15, 70), (55, 30, 15), (40, 50, 10))
_EARLY, _LATE, _OVERLAP, _GAP = 0.04, 0.04, 0.06, 0.15  # 刻意放的排除事件與 no_data 比例
_LAST_FULL = DAYS - 1 - v.WINDOW_DAYS  # 後三日仍在觀察期內的最後一個加額日


@dataclass(frozen=True)
class _Profile:
    cvr: float
    util_tier: int
    util: float
    budget: int
    cpc: float
    ctr: float
    aov: int


def _profile(rng: random.Random) -> _Profile:
    cvr_tier, scale, util_tier = rng.randrange(3), rng.randrange(3), rng.randrange(3)
    low, high = _BUDGETS[scale]
    return _Profile(cvr=_CVR[cvr_tier], util_tier=util_tier, util=_UTIL[util_tier],
                    budget=rng.randint(low, high), cpc=rng.uniform(15, 40),
                    ctr=rng.uniform(0.01, 0.04), aov=rng.randint(2000, 8000))


def _raise_days(rng: random.Random) -> list[int]:
    first = rng.randint(0, 2) if rng.random() < _EARLY else rng.randint(3, 13)
    second = rng.randint(_LAST_FULL + 1, DAYS - 1) if rng.random() < _LATE else rng.randint(
        max(first, 3) + 8, _LAST_FULL)
    days = [first, second]
    if rng.random() < _OVERLAP and second + 2 < DAYS:
        days.append(second + 2)
    return days


def _raise_bp(rng: random.Random, util_tier: int) -> int:
    band_index = rng.choices((0, 1, 2), weights=_RAISE_BAND_WEIGHTS[util_tier])[0]
    return rng.choice(_RAISE_BP[band_index])


def _market(index: int, day: date) -> float:
    """全體廣告共用的轉換率起伏(對照組扣掉的就是它)。"""
    weekend = 0.05 if v.day_type(day) == v.WEEKEND else 0.0
    return 1 + 0.08 * math.sin(2 * math.pi * index / 14) + weekend


def _bucket(rng: random.Random, profile: _Profile, day: date, budget: int, factor: float,
            gap: bool) -> DayBucket:
    if gap:
        return DayBucket(day, None, None, None, None, None, no_data=True)
    spend = int(budget * min(1.0, profile.util * rng.uniform(0.97, 1.03)))
    clicks = max(1, round(spend / profile.cpc * rng.uniform(0.9, 1.1)))
    impressions = max(clicks, round(clicks / profile.ctr * rng.uniform(0.95, 1.05)))
    rate = min(0.5, profile.cvr * factor)
    mean = clicks * rate
    conversions = min(clicks, max(0, round(rng.gauss(mean, math.sqrt(mean * (1 - rate))))))
    revenue = round(conversions * profile.aov * rng.uniform(0.9, 1.1))
    return DayBucket(day, impressions, clicks, conversions, spend, revenue)


def _event_clauses(pre: Sequence[DayBucket], day: date, before: int, after: int
                   ) -> frozenset[v.Clause] | None:
    """加額事件四個欄位的值,跟評估器同一套語彙函式;前三日缺資料或沒點擊就不埋效果(事件會被排除)。"""
    if len(pre) < v.WINDOW_DAYS or any(b.no_data for b in pre):
        return None
    clicks = sum(b.clicks or 0 for b in pre)
    if clicks == 0:
        return None
    conversions = sum(b.conversions or 0 for b in pre)
    spend = sum(b.spend_cents or 0 for b in pre)
    return frozenset({
        (v.DAY_TYPE, v.day_type(day)),
        (v.PRE_CVR, v.band(v.PRE_CVR, Fraction(conversions, clicks))),
        (v.RAISE_PCT, v.band(v.RAISE_PCT, Fraction(after - before, before))),
        (v.SPEND_RATIO, v.band(v.SPEND_RATIO, Fraction(spend, v.WINDOW_DAYS * before))),
    })


def _effect(clauses: frozenset[v.Clause] | None, explore: bool) -> float:
    factor = 1.0
    if clauses is None:
        return factor
    for item in TRUTH:
        if not set(item.clauses) <= clauses:
            continue
        if item.kind == TRUE_PATTERN:
            factor *= LIFT if item.direction == v.IMPROVE else DROP
        elif item.kind == DECOY_EXPLORE_ONLY and explore:
            factor *= LIFT
    return factor


def _make_ad(rng: random.Random, ad_id: str, raised: bool, explore: bool) -> Ad:
    profile = _profile(rng)
    plan = {day: _raise_bp(rng, profile.util_tier) for day in _raise_days(rng)} if raised else {}
    gaps = {rng.randrange(DAYS)} if rng.random() < _GAP else set()
    budget, buckets, adjustments = profile.budget, list[DayBucket](), list[Adjustment]()
    lift: dict[int, float] = {}
    for index in range(DAYS):
        day = START + timedelta(days=index)
        if index in plan:
            after = budget + (budget * plan[index] + 5000) // 10000
            moment = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
                minutes=rng.randrange(60, 23 * 60))
            adjustments.append(Adjustment(f"{ad_id}-op{len(adjustments) + 1}", moment, budget,
                                          after))
            factor = _effect(_event_clauses(buckets[-v.WINDOW_DAYS:], day, budget, after), explore)
            for later in range(index + 1, index + 1 + v.WINDOW_DAYS):
                lift[later] = lift.get(later, 1.0) * factor
            budget = after
        buckets.append(_bucket(rng, profile, day, budget,
                               _market(index, day) * lift.get(index, 1.0), index in gaps))
    return Ad(ad_id, profile.budget, tuple(buckets), tuple(adjustments))


def generate(seed: int) -> History:
    """固定種子的合成歷史;只收 `SEEDS` 裡的三個種子。"""
    if seed not in v.SEEDS:
        raise ValueError(f"只收固定種子 {v.SEEDS},任一批不得替換:{seed}")
    rng = random.Random(seed)  # noqa: S311 - 合成歷史用固定種子,不是密碼學用途
    ids = tuple(f"rm{seed}-{i:04d}" for i in range(N_ADS))
    explore = split(ids).explore
    raised = frozenset(rng.sample(ids, RAISED_ADS))
    ads = tuple(_make_ad(rng, ad_id, ad_id in raised, ad_id in explore) for ad_id in ids)
    return History(seed, v.GENERATOR_VERSION, START, DAYS, ads)


# ---- 評估集文字與雜湊 ----
def _bucket_line(ad_id: str, bucket: DayBucket) -> str:
    if bucket.no_data:
        return f"N|{ad_id}|{bucket.day.isoformat()}"
    values = (bucket.impressions, bucket.clicks, bucket.conversions, bucket.spend_cents,
              bucket.revenue_cents)
    return "|".join(["D", ad_id, bucket.day.isoformat(), *(str(x) for x in values)])


def render(history: History) -> str:
    """評估集的固定文字:標頭、每支廣告一行(A)、每天一行(D;no_data 寫 N)、每筆操作一行(O)。"""
    lines = [f"rule-mining-history|{history.generator_version}|seed={history.seed}|"
             f"start={history.start.isoformat()}|days={history.day_count}|ads={len(history.ads)}"]
    for ad in history.ads:
        lines.append(f"A|{ad.ad_id}|{ad.daily_budget_cents}")
        lines += [_bucket_line(ad.ad_id, bucket) for bucket in ad.days]
        lines += [f"O|{ad.ad_id}|{a.op_id}|{a.committed_at.isoformat()}|{a.budget_before_cents}|"
                  f"{a.budget_after_cents}" for a in ad.adjustments]
    return "\n".join(lines) + "\n"


def data_sha256(history: History) -> str:
    return hashlib.sha256(render(history).encode()).hexdigest()
