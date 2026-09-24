"""合成評估集的生成器(Phase 10 增量 2)。

只依評分格的定義與欄位型別造數字,不讀決策規則與它的測試(測試掃這支檔的匯入守)。每個評分格造
`BASES_PER_CELL` 組基準情境,每組三個變體:基準原值、只改花費(仍在偏低範圍)、只改預算(花費不變)——後
兩個是無關欄位擾動檢查([S713]),答案應跟基準一樣。正常的格照漏斗順序造(點擊不多於曝光、轉換不多於點擊,沒有負數與
缺值);資料異常格輪流造單一故障:五個欄位各一次缺值、各一次負數,以及只違反「點擊多於曝光」、只違反
「轉換多於點擊」([S717])。計劃釘住的邊界案例放在各格前幾組。種子固定;`render` 產出 `eval_set.py`
的全文,雜湊由測試釘住([S706]),測試另外重跑生成器逐值比對。這是有限的合約案例,不給任何統計保證。
"""

import random
from dataclasses import dataclass

from rtb.domain.proposal import MAX_INT
from rtb.domain.worth import CampaignStatus, WorthCell, WorthInput, WorthVerdict, cell_of
from rtb.eval.rubric import gold

SEED = 20260924
BASES_PER_CELL = 20
VARIANTS = ("base", "spend", "budget")
AMOUNT_FIELDS = ("impressions", "clicks", "conversions", "revenue", "spend")
# 資料異常格的單一故障:欄位:缺值、欄位:負數,以及兩種「多於」
FAULTS = (*(f"{name}:missing" for name in AMOUNT_FIELDS),
          *(f"{name}:negative" for name in AMOUNT_FIELDS),
          "clicks>impressions", "conversions>clicks")
NO_FAULT = ""
# 偏低配速:花費 < 預算乘 1/24 再乘 0.5。基準與擾動都把花費留在這個範圍裡
_UNDERPACED_SHARE = 1 / 24 * 0.5

Row = tuple[str, str, str, str, str, str, int, float | None, int | None, int | None, int | None,
            float | None, str]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    group: str
    variant: str
    filed_cell: WorthCell  # 生成器把它歸在哪一格;測試用評分表重算比對([S717])
    fault: str  # 資料異常格的單一故障;其他格是空字串
    worth_input: WorthInput
    gold: WorthVerdict

    @property
    def cell(self) -> WorthCell:
        return cell_of(self.worth_input)


Fields = dict[str, int | float | None]


def _funnel(rng: random.Random, clicks_positive: bool, conversions_positive: bool) -> Fields:
    """正常資料:曝光 ≥ 點擊 ≥ 轉換 ≥ 0;要正數的就取正數,不要的取零。"""
    impressions = rng.randint(1, 10_000)
    clicks = rng.randint(1, impressions) if clicks_positive else 0
    conversions = rng.randint(1, clicks) if conversions_positive and clicks else 0
    return {"impressions": impressions, "clicks": clicks, "conversions": conversions}


def _revenue(rng: random.Random, positive: bool) -> float:
    return round(rng.uniform(0.01, 5_000.0), 2) if positive else 0.0


def _normal(rng: random.Random, cell: WorthCell, index: int) -> Fields:
    if cell is WorthCell.PAUSED:  # 暫停格:什麼樣的資料都判不值得加,正常與異常都造
        fields = _funnel(rng, rng.random() < 0.7, rng.random() < 0.5)
        if index % 4 == 3:
            fields["clicks"] = None
        return {**fields, "revenue": _revenue(rng, rng.random() < 0.5)}
    if cell is WorthCell.NO_DELIVERY:
        fields = _funnel(rng, False, False)
        if index % 2 == 1:  # 曝光也是零;第 0 組固定是「曝光正、點擊零」的邊界
            fields["impressions"] = 0
        return {**fields, "revenue": _revenue(rng, rng.random() < 0.5)}
    if cell is WorthCell.DELIVERY_WITH_VALUE:
        # 第 0 組營收正轉換零、第 1 組轉換正營收零(邊界),之後輪流
        conversions_positive, revenue_positive = ((False, True), (True, False), (True, True))[
            index % 3]
        return {**_funnel(rng, True, conversions_positive),
                "revenue": _revenue(rng, revenue_positive)}
    return {**_funnel(rng, True, False), "revenue": 0.0}  # 沒價值格:轉換營收都是零


def _inject(rng: random.Random, fields: Fields, fault: str) -> Fields:
    """在正常資料上只打一個故障(花費欄的故障在 `generate` 打)。"""
    fields = dict(fields)
    if fault == "clicks>impressions":  # 只動點擊;轉換照舊不多於原本的點擊,所以仍不多於新的點擊
        fields["clicks"] = int(fields["impressions"] or 0) + rng.randint(1, 50)
    elif fault == "conversions>clicks":
        fields["conversions"] = int(fields["clicks"] or 0) + rng.randint(1, 50)
    elif ":" in fault:
        name, kind = fault.split(":")
        if name != "spend":
            fields[name] = None if kind == "missing" else (
                -1.0 if name == "revenue" else -rng.randint(1, 50))
    return fields


def _spend(rng: random.Random, budget: int, fault: str) -> float | None:
    if fault == "spend:missing":
        return None
    if fault == "spend:negative":
        return -round(rng.uniform(0.01, 10.0), 2)
    return round(rng.uniform(0.0, budget * _UNDERPACED_SHARE * 0.99), 2)


def generate(seed: int) -> tuple[Scenario, ...]:
    rng = random.Random(seed)  # noqa: S311 - 造合成案例,不是安全用途
    scenarios = []
    for cell in WorthCell:
        status = CampaignStatus.PAUSED if cell is WorthCell.PAUSED else CampaignStatus.ACTIVE
        for index in range(BASES_PER_CELL):
            group = f"{cell.value}-{index:02d}"
            fault = FAULTS[index % len(FAULTS)] if cell is WorthCell.ANOMALY else NO_FAULT
            base_cell = WorthCell.DELIVERY_WITH_VALUE if cell is WorthCell.ANOMALY else cell
            fields = _inject(rng, _normal(rng, base_cell, index), fault)
            budget = rng.choice((1, 24, 100, 10_000, MAX_INT // 2, MAX_INT))
            perturbed_budget = rng.choice((48, 5_000, 10**9))
            spend = _spend(rng, budget, fault)
            # 三個變體:基準原值;「花費」只改花費(花費缺值的故障沒有別的值可換,照舊;花費負數換另一個
            # 負數);「預算」只改預算、不重抽花費(代碼審第 1 輪)
            perturbed_spend = spend if fault == "spend:missing" else _spend(rng, budget, fault)
            for variant in VARIANTS:
                use_budget = perturbed_budget if variant == "budget" else budget
                use_spend = perturbed_spend if variant == "spend" else spend
                worth_input = WorthInput(status=status, budget=use_budget, spend=use_spend,
                                         **fields)  # type: ignore[arg-type]
                if cell_of(worth_input) is not cell:
                    raise AssertionError(f"生成器造出不屬於 {cell} 的情境:{worth_input}")
                scenarios.append(Scenario(f"{group}-{variant}", group, variant, cell, fault,
                                          worth_input, gold(worth_input)))
    return tuple(scenarios)


def to_row(scenario: Scenario) -> Row:
    i = scenario.worth_input
    return (scenario.scenario_id, scenario.group, scenario.variant, scenario.filed_cell.value,
            scenario.fault, i.status.value, i.budget, i.spend, i.impressions, i.clicks,
            i.conversions, i.revenue, scenario.gold.value)


def from_rows(rows: tuple[Row, ...]) -> tuple[Scenario, ...]:
    return tuple(
        Scenario(scenario_id, group, variant, WorthCell(filed), fault,
                 WorthInput(status=CampaignStatus(status), budget=budget, spend=spend,
                            impressions=impressions, clicks=clicks, conversions=conversions,
                            revenue=revenue),
                 WorthVerdict(answer))
        for (scenario_id, group, variant, filed, fault, status, budget, spend, impressions,
             clicks, conversions, revenue, answer) in rows)


HEADER = '''# ruff: noqa: E501 - 生成的資料列,一列一筆情境
"""合成評估集(Phase 10 增量 2):`rtb.eval.generator.render(generate(SEED))` 的產出,不要手改。

每列:情境編號、擾動組、變體、歸檔的格、單一故障、狀態、預算、花費、曝光、點擊、轉換、營收、
標準答案(程式照評分表算)。
雜湊由測試釘住;換批要同時用決策指令記一筆理由與時間(計劃〈外洩紀錄〉)。
"""

from rtb.eval.generator import Row

ROWS: tuple[Row, ...] = (
'''


def render(scenarios: tuple[Scenario, ...]) -> str:
    return HEADER + "".join(f"    {to_row(s)!r},\n" for s in scenarios) + ")\n"
