"""比較表與逐格採用決定(Phase 10 增量 2,[S708]、[S709]、[S710])。

一格要同時滿足才進已驗證清單:①正式環境抽樣集上候選實測、樣本數夠、該格每一項品質指標的 95% Wilson
信賴下界都過門檻;②成本、延遲、格式失敗率、例外率、逾時率、退回率都有量測、而且不超過事先定好的門檻
(門檻由使用者在接上第一個候選時裁定,沒定之前不得採用);比較表裡任何「沒量」的欄位都擋採用。門檻是
常數,不依結果調整。已驗證清單只有這裡建得出來([S716])。
"""

import math
from dataclasses import dataclass, fields

from rtb.analyzer.policy import _VALIDATED_CELLS_ISSUER, ValidatedCells
from rtb.domain.worth import WorthCell
from rtb.eval.scoring import PRODUCTION, RECALL, CellReport, Report

# 暫用、待使用者覆核(計劃〈設計〉)。最少樣本是「全對時下界剛好過門檻」的筆數,由下界本身就擋住
# 樣本不夠的格(少一筆、全對也過不了),所以不另寫樣本數檢查——變異檢查證實另寫那一道拿掉結果不變
WORTH_RECALL_BAR = 0.80
OTHER_BAR = 0.95
WORTH_MIN_SAMPLES = 16
OTHER_MIN_SAMPLES = 73
Z_95 = 1.959963984540054


def wilson_lower(successes: int, n: int, z: float = Z_95) -> float:
    """比例的 95% Wilson 信賴下界。"""
    if n <= 0:
        raise ValueError("樣本數要大於零")
    p = successes / n
    denominator = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (center - margin) / denominator


@dataclass(frozen=True)
class Measure:
    """比較表的一個儲存格:有量就有數值,沒量就沒有數值、而且寫原因([S709])。"""

    measured: bool
    value: float | None
    reason: str | None

    def __post_init__(self) -> None:
        if self.measured and (self.value is None or self.reason is not None):
            raise ValueError("有量的儲存格要有數值、不寫沒量的原因")
        if not self.measured and (self.value is not None or not self.reason):
            raise ValueError("沒量的儲存格不能有數值,而且要寫原因")

    @classmethod
    def of(cls, value: float) -> Measure:
        return cls(measured=True, value=value, reason=None)

    @classmethod
    def not_measured(cls, reason: str) -> Measure:
        return cls(measured=False, value=None, reason=reason)


@dataclass(frozen=True)
class ComparisonRow:
    approach: str
    quality: Measure
    cost_per_call_usd: Measure
    latency_median_us: Measure
    latency_p95_us: Measure
    format_failure_rate: Measure
    exception_rate: Measure
    timeout_rate: Measure
    fallback_rate: Measure

    def measures(self) -> tuple[Measure, ...]:
        return tuple(getattr(self, f.name) for f in fields(self) if f.name != "approach")


@dataclass(frozen=True)
class OperationalLimits:
    """成本、延遲、失敗率的門檻;None 表示使用者還沒裁定,沒定之前一格都不採用。"""

    cost_per_call_usd: float | None
    latency_p95_us: float | None
    failure_rate: float | None


@dataclass(frozen=True)
class CellDecision:
    cell: WorthCell
    validated: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Adoption:
    cells: tuple[CellDecision, ...]
    validated: ValidatedCells
    adopt: bool
    reasons: tuple[str, ...]


def _operational_problems(row: ComparisonRow | None, limits: OperationalLimits) -> list[str]:
    if row is None:
        return ["沒有候選實測"]
    problems = [f"比較表有沒量的欄位:{row.approach}" for m in row.measures() if not m.measured][:1]
    if None in (limits.cost_per_call_usd, limits.latency_p95_us, limits.failure_rate):
        problems.append("成本、延遲或失敗率的門檻還沒裁定")
    if problems:
        return problems
    assert limits.cost_per_call_usd is not None  # noqa: S101 - 上面已確認三個門檻都有值
    assert limits.latency_p95_us is not None  # noqa: S101
    assert limits.failure_rate is not None  # noqa: S101
    checks = ((row.cost_per_call_usd, limits.cost_per_call_usd, "成本超過門檻"),
              (row.latency_p95_us, limits.latency_p95_us, "延遲 p95 超過門檻"),
              (row.format_failure_rate, limits.failure_rate, "格式失敗率超過門檻"),
              (row.exception_rate, limits.failure_rate, "例外率超過門檻"),
              (row.timeout_rate, limits.failure_rate, "逾時率超過門檻"),
              (row.fallback_rate, limits.failure_rate, "退回率超過門檻"))
    return [message for measure, bar, message in checks
            if measure.value is not None and measure.value > bar]


def _quality_problems(cell: CellReport) -> list[str]:
    if cell.metric == RECALL:
        low = wilson_lower(cell.numerator, cell.n)
        return [] if low >= WORTH_RECALL_BAR else [f"召回率下界 {low:.3f} 未達 {WORTH_RECALL_BAR}"]
    # 這三格的標準答案都不是「值得加」,每一次誤提案同時也是一次類別錯誤,所以類別正確率的下界過了,
    # 「不誤提案的比例」下界必然也過;只比類別正確率(變異檢查證實另比誤提案率那一道拿掉結果不變)。
    # 誤提案率照樣在報告裡逐格列出
    correct = wilson_lower(cell.class_correct_count, cell.n)
    return [] if correct >= OTHER_BAR else [f"類別正確率下界 {correct:.3f} 未達 {OTHER_BAR}"]


def decide_adoption(
    report: Report, candidate: ComparisonRow | None, limits: OperationalLimits
) -> Adoption:
    shared = _operational_problems(candidate, limits)
    if report.kind != PRODUCTION:
        shared = ["只有合成集:合成集是有限的合約案例,不能產生已驗證清單", *shared]
    decisions = []
    for cell in report.cells:
        reasons = tuple(shared + _quality_problems(cell))
        decisions.append(CellDecision(cell.cell, not reasons, reasons))
    # 採用函式是已驗證清單唯一的信任呼叫端:帶分析端的簽發者哨兵建(代碼審第 2 輪的寫法)
    validated = ValidatedCells(frozenset(d.cell for d in decisions if d.validated),
                               _VALIDATED_CELLS_ISSUER)
    return Adoption(cells=tuple(decisions), validated=validated, adopt=bool(validated.cells),
                    reasons=tuple(dict.fromkeys(shared)))
