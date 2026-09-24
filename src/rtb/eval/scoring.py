"""逐筆計分與逐格報告(Phase 10 增量 2,[S707]、[S713]、[S714])。

逐筆走跟正式路徑相同的組合:判斷點輸入經分析端的路由函式(含包裝與退回),依「最後有效的答案」
計分,不是依候選原始回答計分——候選回「不知道」退回現行規則後提案,照樣算一次誤提案。現行規則用同一套
計分(不傳候選)。每格事先指定指標,分母是那一格的樣本數,不會是零:「值得加」格報召回率,其他四格報
「不誤提案的比例」與類別正確率。

合成集與正式環境抽樣集是兩種不同的報告型別(代碼審第 1 輪:只靠一個字串區分,合成集結果改個字串就能
被當成正式):合成集報告不含信賴下界;正式報告必帶隱藏集出處,只能經 `production_report` 建出來,
每項指標同時存分子、分母、點估計與 95% Wilson 下界,採用函式只讀它存的下界。
"""

import hashlib
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rtb.analyzer.policy import CandidateCall, RoutePath, TrialCells, ValidatedCells, route
from rtb.domain._checks import is_sha256
from rtb.domain.worth import WorthCell, WorthVerdict
from rtb.eval.generator import Scenario

RECALL = "recall"  # 「值得加」格:該加的有加到多少
NO_FALSE_PROPOSAL = "no_false_proposal"  # 其他格:1 減誤提案率(不該加卻提案,判錯就是多花錢)
CLASS_ACCURACY = "class_accuracy"  # 其他格:最後有效答案等於標準答案的比例
EVAL_SET = Path(__file__).with_name("eval_set.py")
Z_95 = 1.959963984540054
# 經過候選的路徑(候選答的,或候選失敗、逾時、答不知道後退回現行規則的)
_THROUGH_CANDIDATE = frozenset(RoutePath) - {RoutePath.CODE_RULE}


def eval_set_sha256() -> str:
    return hashlib.sha256(EVAL_SET.read_bytes()).hexdigest()


def wilson_lower(successes: int, n: int, z: float = Z_95) -> float:
    """比例的 95% Wilson 信賴下界。"""
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("樣本數要大於零,成功數要在 0 到樣本數之間")
    p = successes / n
    denominator = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (center - margin) / denominator


@dataclass(frozen=True)
class ScoredCase:
    scenario: Scenario
    final: WorthVerdict
    path: RoutePath


def score(
    scenarios: tuple[Scenario, ...], candidate: CandidateCall | None, trial: TrialCells | None,
) -> tuple[ScoredCase, ...]:
    """評估入口:待測格清單只在這次評估裡用,不產生、不改動已驗證清單([S716])。"""
    allowed: TrialCells | ValidatedCells = ValidatedCells.NONE if trial is None else trial
    scored = []
    for scenario in scenarios:
        result = route(scenario.worth_input, candidate, allowed)
        scored.append(ScoredCase(scenario, result.verdict, result.path))
    return tuple(scored)


@dataclass(frozen=True)
class Metric:
    name: str
    numerator: int
    denominator: int
    lower_bound: float | None  # 只有正式報告有;合成集一律沒有

    @property
    def point(self) -> float:
        return self.numerator / self.denominator


@dataclass(frozen=True)
class CellReport:
    cell: WorthCell
    n: int
    metrics: tuple[Metric, ...]
    false_proposals: int  # 不該加卻說值得加的筆數(「值得加」格是 0)
    class_correct_count: int
    errors: tuple[tuple[WorthVerdict, WorthVerdict, int], ...]  # (標準答案, 最後答案, 筆數)
    paths: Mapping[RoutePath, int]

    def metric(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)


@dataclass(frozen=True)
class SyntheticReport:
    """合成集的報告:有限的合約案例,不套統計信賴、不能產生已驗證清單。"""

    eval_set_sha256: str
    cells: tuple[CellReport, ...]
    confusion: Mapping[tuple[WorthVerdict, WorthVerdict], int]
    perturbation_changed: int  # 擾動組裡最後答案不一致的組數([S713])


@dataclass(frozen=True)
class HiddenSetProvenance:
    """正式環境隱藏抽樣集的出處(逐筆情境與答案不進程式庫,這裡只記版本與出處)。"""

    version: str
    sha256: str
    sampling_source: str  # 從哪裡、怎麼依格抽樣的
    labeling_source: str  # 誰、怎麼人工標註的
    candidate_version: str
    candidate_predates_disclosure: bool  # 候選版本早於這批被揭露;揭露過的一批不得再驗證新候選

    def __post_init__(self) -> None:
        texts = (self.version, self.sha256, self.sampling_source, self.labeling_source,
                 self.candidate_version)
        if not all(isinstance(t, str) and t.strip() for t in texts):
            raise ValueError("正式報告的隱藏集出處每一欄都要寫")
        if not is_sha256(self.sha256):
            raise ValueError("隱藏集雜湊要是 64 位小寫十六進位")
        if type(self.candidate_predates_disclosure) is not bool:
            raise ValueError("候選是否早於揭露要是真的布林值")


_PRODUCTION_ISSUER = object()  # 只有 production_report 建得出正式報告


class ProductionReport:
    """正式環境抽樣集的報告:只能經 `production_report` 建出來,每項指標都有信賴下界。比照已驗證清單
    的最小形狀(必填簽發者、唯讀屬性);防忘記、不防刻意繞過。"""

    __slots__ = ("_cells", "_confusion", "_perturbation_changed", "_provenance")

    def __init__(self, provenance: HiddenSetProvenance, cells: tuple[CellReport, ...],
                 confusion: Mapping[tuple[WorthVerdict, WorthVerdict], int],
                 perturbation_changed: int, issuer: object) -> None:
        if issuer is not _PRODUCTION_ISSUER:
            raise ValueError("正式報告只能由 production_report 建立")
        self._provenance, self._cells = provenance, cells
        self._confusion, self._perturbation_changed = confusion, perturbation_changed

    @property
    def provenance(self) -> HiddenSetProvenance:
        return self._provenance

    @property
    def cells(self) -> tuple[CellReport, ...]:
        return self._cells

    @property
    def confusion(self) -> Mapping[tuple[WorthVerdict, WorthVerdict], int]:
        return self._confusion

    @property
    def perturbation_changed(self) -> int:
        return self._perturbation_changed


def _metrics(cell: WorthCell, n: int, proposed: int, correct: int,
             with_bounds: bool) -> tuple[Metric, ...]:
    def metric(name: str, successes: int) -> Metric:
        return Metric(name, successes, n, wilson_lower(successes, n) if with_bounds else None)

    if cell is WorthCell.DELIVERY_WITH_VALUE:
        return (metric(RECALL, proposed),)
    return (metric(NO_FALSE_PROPOSAL, n - proposed), metric(CLASS_ACCURACY, correct))


def _cell_report(cell: WorthCell, cases: list[ScoredCase], with_bounds: bool) -> CellReport:
    n = len(cases)
    proposed = sum(1 for c in cases if c.final is WorthVerdict.WORTH)
    correct = sum(1 for c in cases if c.final is c.scenario.gold)
    errors = Counter((c.scenario.gold, c.final) for c in cases if c.final is not c.scenario.gold)
    return CellReport(
        cell=cell, n=n, metrics=_metrics(cell, n, proposed, correct, with_bounds),
        false_proposals=0 if cell is WorthCell.DELIVERY_WITH_VALUE else proposed,
        class_correct_count=correct,
        errors=tuple((gold, final, count) for (gold, final), count in sorted(errors.items())),
        paths=dict(Counter(c.path for c in cases)))


def _parts(scored: tuple[ScoredCase, ...], with_bounds: bool) -> tuple[
        tuple[CellReport, ...], dict[tuple[WorthVerdict, WorthVerdict], int], int]:
    by_cell: dict[WorthCell, list[ScoredCase]] = {cell: [] for cell in WorthCell}
    finals_by_group: dict[str, set[WorthVerdict]] = {}
    for case in scored:
        by_cell[case.scenario.cell].append(case)
        finals_by_group.setdefault(case.scenario.group, set()).add(case.final)
    cells = tuple(_cell_report(cell, cases, with_bounds) for cell, cases in by_cell.items()
                  if cases)
    confusion = dict(Counter((c.scenario.gold, c.final) for c in scored))
    changed = sum(1 for finals in finals_by_group.values() if len(finals) > 1)
    return cells, confusion, changed


def synthetic_report(scored: tuple[ScoredCase, ...], sha256: str) -> SyntheticReport:
    cells, confusion, changed = _parts(scored, with_bounds=False)
    return SyntheticReport(sha256, cells, confusion, changed)


def production_report(
    scored: tuple[ScoredCase, ...], provenance: HiddenSetProvenance
) -> ProductionReport:
    """正式計分入口:每一筆都要交給候選,候選答的或候選退回的(逾時、例外、不知道、非法回傳)都算候選
    的結果、照最後有效答案計分;任何一筆走現行規則就拒(代碼審第 2、3 輪:只要求每格一筆,1 筆候選加
    72 筆現行規則就能讓現行規則替候選過關)。"""
    if not scored or any(c.path not in _THROUGH_CANDIDATE for c in scored):
        raise ValueError("正式報告每一筆都要交給候選,不得有走現行規則的")
    cells, confusion, changed = _parts(scored, with_bounds=True)
    return ProductionReport(provenance, cells, confusion, changed, _PRODUCTION_ISSUER)
