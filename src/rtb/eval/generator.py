"""合成評估集的生成器(Phase 10 增量 2)。

只依評分格的定義與欄位型別造數字,不讀決策規則與它的測試(測試掃這支檔的匯入守)。每個評分格造
`BASES_PER_CELL` 組基準情境,每組三個變體:基準、只改花費(仍在偏低範圍)、只改預算——後兩個是無關欄位
擾動檢查([S713]),答案應跟基準一樣。種子固定;`render` 產出 `eval_set.py` 的全文,評估集就是它的產出,
雜湊由測試釘住([S706])。這是有限的合約案例,不是從某個母體抽樣,不給任何統計保證。
"""

import random
from dataclasses import dataclass

from rtb.domain.proposal import MAX_INT
from rtb.domain.worth import CampaignStatus, WorthCell, WorthInput, WorthVerdict, cell_of
from rtb.eval.rubric import gold

SEED = 20260924
BASES_PER_CELL = 20
VARIANTS = ("base", "spend", "budget")
# 偏低配速:花費 < 預算乘 1/24 再乘 0.5。基準與擾動都把花費留在這個範圍裡
_UNDERPACED_SHARE = 1 / 24 * 0.5
_NOT_POSITIVE = (0, -1, None)

Row = tuple[str, str, str, str, int, float, int | None, int | None, int | None, float | None,
            str]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    group: str
    variant: str
    worth_input: WorthInput
    gold: WorthVerdict

    @property
    def cell(self) -> WorthCell:
        return cell_of(self.worth_input)


def _count(rng: random.Random, positive: bool, upper: int = 10_000) -> int | None:
    return rng.randint(1, max(upper, 1)) if positive else rng.choice(_NOT_POSITIVE)


def _funnel(rng: random.Random, impressions: bool, clicks: bool,
            conversions: bool) -> tuple[int | None, int | None, int | None]:
    """正數的計數照漏斗順序造:點擊不多於曝光、轉換不多於點擊(前一層不是正數就不設上限)。"""
    shown = _count(rng, impressions)
    clicked = _count(rng, clicks, shown if isinstance(shown, int) and shown > 0 else 10_000)
    converted = _count(rng, conversions,
                       clicked if isinstance(clicked, int) and clicked > 0 else 10_000)
    return shown, clicked, converted


def _money(rng: random.Random, positive: bool) -> float | None:
    if positive:
        return round(rng.uniform(0.01, 5_000.0), 2)
    choice = rng.choice(_NOT_POSITIVE)
    return None if choice is None else float(choice)


def _fields(rng: random.Random, cell: WorthCell) -> dict[str, int | float | None]:
    """依格的定義造曝光、點擊、轉換、營收(暫停格四欄隨便取,看不看都一樣)。"""
    if cell is WorthCell.PAUSED:
        delivery = rng.random() < 0.5
        shown, clicked, converted = _funnel(rng, delivery, delivery, rng.random() < 0.5)
        return {"impressions": shown, "clicks": clicked, "conversions": converted,
                "revenue": _money(rng, rng.random() < 0.5)}
    if cell is WorthCell.NO_DELIVERY:
        impressions_positive, clicks_positive = rng.choice(((True, False), (False, True),
                                                            (False, False)))
        shown, clicked, converted = _funnel(rng, impressions_positive, clicks_positive,
                                            rng.random() < 0.5)
        return {"impressions": shown, "clicks": clicked, "conversions": converted,
                "revenue": _money(rng, rng.random() < 0.5)}
    with_value = cell is WorthCell.DELIVERY_WITH_VALUE
    conversions_positive, revenue_positive = (
        rng.choice(((True, False), (False, True), (True, True))) if with_value else (False, False))
    shown, clicked, converted = _funnel(rng, True, True, conversions_positive)
    return {"impressions": shown, "clicks": clicked, "conversions": converted,
            "revenue": _money(rng, revenue_positive)}


def _spend(rng: random.Random, budget: int) -> float:
    return round(rng.uniform(0.0, budget * _UNDERPACED_SHARE * 0.99), 2)


def generate(seed: int) -> tuple[Scenario, ...]:
    rng = random.Random(seed)  # noqa: S311 - 造合成案例,不是安全用途
    scenarios = []
    for cell in WorthCell:
        status = CampaignStatus.PAUSED if cell is WorthCell.PAUSED else CampaignStatus.ACTIVE
        for index in range(BASES_PER_CELL):
            group = f"{cell.value}-{index:02d}"
            fields = _fields(rng, cell)
            budget = rng.choice((1, 24, 100, 10_000, MAX_INT // 2, MAX_INT))
            perturbed_budget = rng.choice((48, 5_000, 10**9))
            for variant in VARIANTS:
                use_budget = perturbed_budget if variant == "budget" else budget
                worth_input = WorthInput(
                    status=status, budget=use_budget, spend=_spend(rng, use_budget), **fields)
                if cell_of(worth_input) is not cell:
                    raise AssertionError(f"生成器造出不屬於 {cell} 的情境:{worth_input}")
                scenarios.append(Scenario(f"{group}-{variant}", group, variant, worth_input,
                                          gold(worth_input)))
    return tuple(scenarios)


def to_row(scenario: Scenario) -> Row:
    i = scenario.worth_input
    return (scenario.scenario_id, scenario.group, scenario.variant, i.status.value,
            i.budget, i.spend, i.impressions, i.clicks, i.conversions, i.revenue,
            scenario.gold.value)  # type: ignore[return-value]


def from_rows(rows: tuple[Row, ...]) -> tuple[Scenario, ...]:
    return tuple(
        Scenario(scenario_id, group, variant,
                 WorthInput(status=CampaignStatus(status), budget=budget, spend=spend,
                            impressions=impressions, clicks=clicks, conversions=conversions,
                            revenue=revenue),
                 WorthVerdict(answer))
        for (scenario_id, group, variant, status, budget, spend, impressions, clicks,
             conversions, revenue, answer) in rows)


HEADER = '''# ruff: noqa: E501 - 生成的資料列,一列一筆情境
"""合成評估集(Phase 10 增量 2):`rtb.eval.generator.render(generate(SEED))` 的產出,不要手改。

每列:情境編號、擾動組、變體、狀態、預算、花費、曝光、點擊、轉換、營收、標準答案(程式照評分表算)。
雜湊由測試釘住;換批要同時用決策指令記一筆理由與時間(計劃〈外洩紀錄〉)。
"""

from rtb.eval.generator import Row

ROWS: tuple[Row, ...] = (
'''


def render(scenarios: tuple[Scenario, ...]) -> str:
    return HEADER + "".join(f"    {to_row(s)!r},\n" for s in scenarios) + ")\n"
