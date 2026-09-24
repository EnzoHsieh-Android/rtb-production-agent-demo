"""比較表與逐格採用決定(Phase 10 增量 2,[S708]、[S709]、[S710])。

一律 fail-closed:合成集、不完整或不合法的證據一定不採用。一格要同時滿足才進已驗證清單:
①證據是正式環境抽樣集的報告(獨立型別,帶隱藏集出處,候選版本早於揭露);
②該格每一項品質指標在報告裡存的 95% Wilson 下界都過門檻;
③比較表裡「這一格」那一列的成本、延遲中位與 p95、格式失敗率、例外率、逾時率、退回率都有量、合法、
而且不超過事先定好的門檻(門檻由使用者在接上第一個候選時裁定,沒定之前不得採用)。
門檻是常數,不依結果調整。已驗證清單只有這裡建得出來([S716])。
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields

from rtb.analyzer.policy import _VALIDATED_CELLS_ISSUER, ValidatedCells
from rtb.domain.worth import WorthCell
from rtb.eval.scoring import (
    CLASS_ACCURACY,
    NO_FALSE_PROPOSAL,
    RECALL,
    CellReport,
    ProductionReport,
)

# 暫用、待使用者覆核(計劃〈設計〉)。最少樣本是「全對時下界剛好過門檻」的筆數,由下界本身就擋住
# 樣本不夠的格(少一筆、全對也過不了),所以不另寫樣本數檢查
WORTH_RECALL_BAR = 0.80
OTHER_BAR = 0.95
WORTH_MIN_SAMPLES = 16
OTHER_MIN_SAMPLES = 73
BARS: Mapping[WorthCell, Mapping[str, float]] = {
    cell: ({RECALL: WORTH_RECALL_BAR} if cell is WorthCell.DELIVERY_WITH_VALUE
           else {NO_FALSE_PROPOSAL: OTHER_BAR, CLASS_ACCURACY: OTHER_BAR})
    for cell in WorthCell}

# 使用者 2026-09-24 本人裁定的兩個不採用理由(計劃〈使用者裁定〉)
NOT_ADOPTED_REASONS = (
    "照評分表,這個判斷可以由程式精確算出,本來就該是程式;現行規則在某些格判錯,這件事記成現行規則的"
    "程式缺陷(不是需要換判斷者的理由)",
    "缺正式環境紀錄、人工標註與候選實測",
)
NO_MONITORING = "上線後逐格監測的機制"


def _finite_nonnegative(value: float) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) \
        and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class Measure:
    """比較表的一個儲存格:有量就有合法數值(有限、不為負),沒量就沒有數值、而且寫原因([S709])。"""

    measured: bool
    value: float | None
    reason: str | None

    def __post_init__(self) -> None:
        if self.measured and (self.value is None or self.reason is not None
                              or not _finite_nonnegative(self.value)):
            raise ValueError("有量的儲存格要有有限、不為負的數值,不寫沒量的原因")
        if not self.measured and (self.value is not None or not self.reason):
            raise ValueError("沒量的儲存格不能有數值,而且要寫原因")

    @classmethod
    def of(cls, value: float) -> Measure:
        return cls(measured=True, value=value, reason=None)

    @classmethod
    def not_measured(cls, reason: str) -> Measure:
        return cls(measured=False, value=None, reason=reason)


RATE_FIELDS = ("quality", "format_failure_rate", "exception_rate", "timeout_rate",
               "fallback_rate")


@dataclass(frozen=True)
class ComparisonRow:
    """比較表的一列:一種做法在一個評分格上的品質、成本、延遲與各失敗率。"""

    approach: str
    cell: WorthCell
    quality: Measure
    cost_per_call_usd: Measure
    latency_median_us: Measure
    latency_p95_us: Measure
    format_failure_rate: Measure
    exception_rate: Measure
    timeout_rate: Measure
    fallback_rate: Measure

    def __post_init__(self) -> None:
        for name in RATE_FIELDS:
            measure: Measure = getattr(self, name)
            if measure.value is not None and measure.value > 1:
                raise ValueError(f"{name} 是比率,要在 0 到 1 之間")

    def measures(self) -> tuple[Measure, ...]:
        return tuple(getattr(self, f.name) for f in fields(self)
                     if f.name not in ("approach", "cell"))


@dataclass(frozen=True)
class OperationalLimits:
    """成本、延遲、失敗率的門檻;None 表示使用者還沒裁定,沒定之前一格都不採用。"""

    cost_per_call_usd: float | None
    latency_median_us: float | None
    latency_p95_us: float | None
    failure_rate: float | None

    def __post_init__(self) -> None:
        for value in (self.cost_per_call_usd, self.latency_median_us, self.latency_p95_us,
                      self.failure_rate):
            if value is not None and not _finite_nonnegative(value):
                raise ValueError("門檻要是有限、不為負的數字")
        if self.failure_rate is not None and self.failure_rate > 1:
            raise ValueError("失敗率門檻要在 0 到 1 之間")


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
    reasons: tuple[str, ...]  # 不採用時的理由;採用時是空的
    missing_evidence: tuple[str, ...]


def _row_problems(row: ComparisonRow | None, limits: OperationalLimits) -> list[str]:
    if row is None:
        return ["這一格沒有候選實測"]
    if not all(m.measured and m.value is not None and _finite_nonnegative(m.value)
               for m in row.measures()):
        return ["這一格的比較表有沒量或不合法的欄位"]
    if None in (limits.cost_per_call_usd, limits.latency_median_us, limits.latency_p95_us,
                limits.failure_rate):
        return ["成本、延遲或失敗率的門檻還沒裁定"]
    checks = ((row.cost_per_call_usd, limits.cost_per_call_usd, "成本超過門檻"),
              (row.latency_median_us, limits.latency_median_us, "延遲中位超過門檻"),
              (row.latency_p95_us, limits.latency_p95_us, "延遲 p95 超過門檻"),
              (row.format_failure_rate, limits.failure_rate, "格式失敗率超過門檻"),
              (row.exception_rate, limits.failure_rate, "例外率超過門檻"),
              (row.timeout_rate, limits.failure_rate, "逾時率超過門檻"),
              (row.fallback_rate, limits.failure_rate, "退回率超過門檻"))
    return [message for measure, bar, message in checks
            if measure.value is None or bar is None or measure.value > bar]


def _quality_problems(cell: CellReport) -> list[str]:
    problems = []
    for name, bar in BARS[cell.cell].items():
        metric = cell.metric(name)
        low = None if metric is None else metric.lower_bound
        if low is None or not math.isfinite(low):
            problems.append(f"{name} 沒有信賴下界")
        elif low < bar:
            problems.append(f"{name} 下界 {low:.3f} 未達 {bar}")
    return problems


def decide_adoption(
    evidence: object, candidate: Mapping[WorthCell, ComparisonRow] | None,
    limits: OperationalLimits,
) -> Adoption:
    """證據只收正式報告;其他型別(含合成集報告)一律整體不採用。"""
    shared: list[str] = []
    report = evidence if isinstance(evidence, ProductionReport) else None
    if report is None:
        shared.append("沒有正式環境抽樣集的報告:合成集是有限的合約案例,不能產生已驗證清單")
    elif not report.provenance.candidate_predates_disclosure:
        shared.append("候選版本不早於隱藏集揭露,這批不能拿來驗證它")
    if candidate is None:
        shared.append("沒有候選實測")
    cells = {c.cell: c for c in report.cells} if report is not None else {}
    decisions = []
    for cell in WorthCell:
        reasons = list(shared)
        if cell not in cells:
            reasons.append("報告沒有這一格")
        else:
            reasons += _quality_problems(cells[cell])
        reasons += _row_problems(None if candidate is None else candidate.get(cell), limits)
        decisions.append(CellDecision(cell, not reasons, tuple(dict.fromkeys(reasons))))
    # 採用函式是已驗證清單唯一的信任呼叫端:帶分析端的簽發者哨兵建
    validated = ValidatedCells(frozenset(d.cell for d in decisions if d.validated),
                               _VALIDATED_CELLS_ISSUER)
    adopt = bool(validated.cells)
    missing = []
    if report is None:
        missing += ["正式環境的決策紀錄抽樣", "人工標註"]
    if candidate is None:
        missing.append("候選模型在每一格的實測(品質、成本、延遲、失敗率)")
    missing.append(NO_MONITORING)  # 還沒有這個機制
    return Adoption(cells=tuple(decisions), validated=validated, adopt=adopt,
                    reasons=() if adopt else (*NOT_ADOPTED_REASONS, *dict.fromkeys(shared)),
                    missing_evidence=tuple(missing))
