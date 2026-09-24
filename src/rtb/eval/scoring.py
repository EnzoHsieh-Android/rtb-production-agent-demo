"""逐筆計分與逐格報告(Phase 10 增量 2,[S707]、[S713]、[S714])。

逐筆走跟正式路徑相同的組合:判斷點輸入經分析端的路由函式(含包裝與退回),依「最後有效的答案」
計分,不是依候選原始回答計分——候選回「不知道」退回現行規則後提案,照樣算一次誤提案。現行規則用同一套
計分(不傳候選)。每格事先指定指標,分母是那一格的樣本數,不會是零:「值得加」格報召回率,其他三格報
誤提案率與類別正確率。合成集不套統計信賴,信賴下界只留給之後的正式環境抽樣集。
"""

import hashlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rtb.analyzer.policy import CandidateCall, RoutePath, TrialCells, ValidatedCells, route
from rtb.domain.worth import WorthCell, WorthVerdict
from rtb.eval.generator import Scenario

SYNTHETIC = "synthetic"  # 合成集:有限的合約案例,不能產生已驗證清單
PRODUCTION = "production"  # 正式環境抽樣集:隱藏、人工標註,不進程式庫
RECALL = "recall"
FALSE_PROPOSAL_RATE = "false_proposal_rate"
EVAL_SET = Path(__file__).with_name("eval_set.py")


def eval_set_sha256() -> str:
    return hashlib.sha256(EVAL_SET.read_bytes()).hexdigest()


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
class CellReport:
    cell: WorthCell
    n: int
    metric: str  # RECALL 或 FALSE_PROPOSAL_RATE
    numerator: int  # 召回:說值得加的筆數;誤提案率:不該加卻說值得加的筆數
    denominator: int
    class_correct_count: int  # 最後有效答案等於標準答案的筆數
    errors: tuple[tuple[WorthVerdict, WorthVerdict, int], ...]  # (標準答案, 最後答案, 筆數)
    paths: Mapping[RoutePath, int]
    lower_bound: float | None  # 只有正式環境抽樣集才有;合成集一律沒有

    @property
    def class_correct(self) -> float | None:
        return None if self.metric == RECALL else self.class_correct_count / self.n


@dataclass(frozen=True)
class Report:
    kind: str
    eval_set_sha256: str
    cells: tuple[CellReport, ...]
    confusion: Mapping[tuple[WorthVerdict, WorthVerdict], int]
    perturbation_changed: int  # 擾動組裡最後答案不一致的組數([S713])


def _cell_report(cell: WorthCell, cases: list[ScoredCase]) -> CellReport:
    n = len(cases)
    proposed = sum(1 for c in cases if c.final is WorthVerdict.WORTH)
    correct = sum(1 for c in cases if c.final is c.scenario.gold)
    errors = Counter((c.scenario.gold, c.final) for c in cases if c.final is not c.scenario.gold)
    return CellReport(
        cell=cell, n=n,
        metric=RECALL if cell is WorthCell.DELIVERY_WITH_VALUE else FALSE_PROPOSAL_RATE,
        numerator=proposed, denominator=n, class_correct_count=correct,
        errors=tuple((gold, final, count) for (gold, final), count in sorted(errors.items())),
        paths=dict(Counter(c.path for c in cases)), lower_bound=None)


def build_report(kind: str, scored: tuple[ScoredCase, ...], sha256: str) -> Report:
    by_cell: dict[WorthCell, list[ScoredCase]] = {cell: [] for cell in WorthCell}
    for case in scored:
        by_cell[case.scenario.cell].append(case)
    finals_by_group: dict[str, set[WorthVerdict]] = {}
    for case in scored:
        finals_by_group.setdefault(case.scenario.group, set()).add(case.final)
    return Report(
        kind=kind, eval_set_sha256=sha256,
        cells=tuple(_cell_report(cell, cases) for cell, cases in by_cell.items() if cases),
        confusion=dict(Counter((c.scenario.gold, c.final) for c in scored)),
        perturbation_changed=sum(1 for finals in finals_by_group.values() if len(finals) > 1))
