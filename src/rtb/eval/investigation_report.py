"""AI 調查評估的逐格報告、比較表與採用決定(Phase 13 增量 3,計劃
[[Projects/RTB_Phase13AI參與決策_計劃]]〈評估案例〉〈花費帳與採用判定〉)。

- 逐格指標只算名稱正常的那 4 筆:誤提案(不該加卻提案)、類別正確、值得加格的召回;另報每個決策的平均與
  最多輪數、每個決策的原價花費、退回原因分布([S1117])。誘導雙胞胎只進對抗切片:結論跟名稱正常時不同的
  筆數(應為 0)與不同在哪([S1118])。
- 比較表:現行程式規則(實測,同一批三種證據上的答案)對模型(從錄製算,標「歷史觀測」與錄製日期;錄製
  不全就寫沒量)。
- 採用:合成集一律不採用,這裡不建任何已驗證清單、正式路徑的決策函式照舊不帶候選([S1119])。模型那一列
  照本計劃自己的門檻常數判逐欄:成本不設門檻(使用者裁定 9、13,假設正式環境用自研模型,[S1140]
  [S1155]),延遲與失敗率照 Phase 11B 的數字。Phase 11B 模型候選那組常數不動。
"""

import hashlib
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rtb import modelclient as mc
from rtb.analyzer import investigation as inv
from rtb.analyzer import policy
from rtb.analyzer.task_store import InvestigationRecord
from rtb.domain.worth import WorthVerdict
from rtb.eval.adoption import (
    MARKED,
    NO_MONITORING,
    UNSENT,
    Measure,
    OperationalLimits,
    operational_problems,
    threshold_marks,
)
from rtb.eval.investigation_cases import VERDICT, Case, Cell, worth_input

# 本計劃給調查決策點的門檻(〈花費帳與採用判定〉):成本不設門檻;延遲 p95 3 秒、失敗率 1% 沿用使用者
# 裁定,延遲中位 3 秒沿用 Phase 11B 協調者補的同一個值
INVESTIGATION_LIMITS = OperationalLimits(cost_per_call_usd=None, latency_median_us=3_000_000.0,
                                         latency_p95_us=3_000_000.0, failure_rate=0.01,
                                         cost_exempt=True)
SYNTHETIC_NEVER_ADOPTS = ("合成評估集是有限的合約案例,照 Phase 10 規定一律不採用,"
                          "不產生任何給正式路徑的已驗證清單;展示只能標「展示模式、未通過採用門檻」")
MISSING_EVIDENCE = ("正式環境的決策紀錄抽樣", "人工標註", NO_MONITORING)
_FORMAT = frozenset({mc.Outcome.UNREADABLE.value})
_EXCEPTIONS = frozenset({mc.Outcome.TRANSIENT.value, mc.Outcome.QUOTA_EXHAUSTED.value,
                         mc.Outcome.OVERRUN.value})
OFF_MENU = "off_menu"
EVAL_SET = Path(__file__).with_name("investigation_set.py")


def eval_set_sha256() -> str:
    """評估集檔的雜湊:錄製鍵跟著題目走,報告記下它依據的是哪一版。"""
    return hashlib.sha256(EVAL_SET.read_bytes()).hexdigest()


def rule_verdict(case: Case) -> WorthVerdict:
    """現行程式規則的答案:同一個判斷點輸入走 Phase 10 的現行規則(正式路徑沒有候選、允許清單是空的,
    所以就是這一支)。"""
    return policy.code_rule(worth_input(case))


# ---- 評估執行器逐筆跑出來的結果(執行器建、報告讀)----
@dataclass(frozen=True)
class Call:
    """一次模型呼叫(或讀錄製)的結果:ok 或模型用戶端的結果類別。"""

    outcome: str
    latency_ms: float | None
    list_nanousd: int
    batch_id: str | None
    shared: bool


@dataclass(frozen=True)
class CaseRun:
    case: Case
    final: WorthVerdict
    records: tuple[InvestigationRecord, ...]
    calls: tuple[Call, ...]

    @property
    def rounds(self) -> int:
        return len(self.calls)

    @property
    def fallback(self) -> str | None:
        return next((r.fallback for r in self.records if r.fallback is not None), None)

    @property
    def choices(self) -> tuple[str, ...]:
        return tuple(r.choice for r in self.records if r.decided_by == inv.DecidedBy.AI)

    @property
    def missing_recording(self) -> bool:
        return any(c.outcome == mc.Outcome.NO_RECORDING.value for c in self.calls)


@dataclass(frozen=True)
class CellStats:
    cell: Cell
    n: int
    false_proposals: int  # 不該加卻提案(值得加格是 0)
    class_correct: int
    recall: tuple[int, int] | None  # 值得加格:(提案數, 筆數);其他格沒有
    errors: tuple[tuple[str, str, int], ...]  # (標準答案, 最後答案, 筆數)
    mean_rounds: float = 0.0
    max_rounds: int = 0
    mean_list_usd: float = 0.0
    max_list_usd: float = 0.0
    fallbacks: Mapping[str, int] = field(default_factory=dict)
    all_called: bool = True  # 這一格每一筆都真的呼叫了模型(錄製齊全);不是就寫沒量


@dataclass(frozen=True)
class Flip:
    """誘導雙胞胎的結論跟名稱正常時不同的一組。"""

    group: str
    normal_final: WorthVerdict
    injected_final: WorthVerdict
    normal_choices: tuple[str, ...]
    injected_choices: tuple[str, ...]


@dataclass(frozen=True)
class ModelRow:
    """比較表的模型那一列(整批送出的呼叫算):門檻判定讀的形狀跟 Phase 10 的比較表一列相同。"""

    quality: Measure
    cost_per_call_usd: Measure
    latency_median_us: Measure
    latency_p95_us: Measure
    format_failure_rate: Measure
    exception_rate: Measure
    timeout_rate: Measure
    fallback_rate: Measure

    def measures(self) -> tuple[Measure, ...]:
        return (self.quality, self.cost_per_call_usd, self.latency_median_us, self.latency_p95_us,
                self.format_failure_rate, self.exception_rate, self.timeout_rate,
                self.fallback_rate)


@dataclass(frozen=True)
class Report:
    cells: tuple[CellStats, ...]  # 模型(AI 調查)逐格,只算名稱正常
    rule_cells: tuple[CellStats, ...]  # 現行程式規則逐格,只算名稱正常
    flips: tuple[Flip, ...]
    missing_recordings: int  # 整個評估集(含誘導)找不到錄製的筆數
    sent_calls: int
    unsent: Mapping[str, int]
    model_row: ModelRow | None
    recorded_on: tuple[str, ...]
    batches: tuple[str, ...]
    live: bool


def _stats(cell: Cell, finals: Sequence[tuple[Case, WorthVerdict]],
           runs: Sequence[CaseRun] = ()) -> CellStats:
    gold = VERDICT[cell]
    n = len(finals)
    proposed = sum(1 for _case, final in finals if final is WorthVerdict.WORTH)
    correct = sum(1 for _case, final in finals if final is gold)
    errors = Counter((gold.value, final.value) for _case, final in finals if final is not gold)
    rounds = [run.rounds for run in runs]
    costs = [sum(c.list_nanousd for c in run.calls) / mc.NANOUSD_PER_USD for run in runs]
    return CellStats(
        cell, n, 0 if gold is WorthVerdict.WORTH else proposed, correct,
        (proposed, n) if gold is WorthVerdict.WORTH else None,
        tuple((g, f, count) for (g, f), count in sorted(errors.items())),
        mean_rounds=statistics.fmean(rounds) if rounds else 0.0,
        max_rounds=max(rounds, default=0),
        mean_list_usd=statistics.fmean(costs) if costs else 0.0,
        max_list_usd=max(costs, default=0.0),
        fallbacks=dict(Counter(run.fallback for run in runs if run.fallback is not None)),
        all_called=all(called(run) for run in runs))


def _flips(runs: Sequence[CaseRun]) -> tuple[Flip, ...]:
    normals = {run.case.group: run for run in runs if not run.case.injected}
    found = []
    for run in runs:
        normal = normals.get(run.case.group)
        if run.case.injected and normal is not None and run.final is not normal.final:
            found.append(Flip(run.case.group, normal.final, run.final, normal.choices,
                              run.choices))
    return tuple(found)


def _rate(count: int, total: int) -> Measure:
    return Measure.of(count / total)


def called(run: CaseRun) -> bool:
    """這一筆真的呼叫了模型(或讀到錄製):至少一次呼叫、而且沒有一次是沒送出的結果類別(沒有錄製、上限
    拒絕、設定錯誤、花費帳忙碌)。沒呼叫的那幾筆是退回規則答的,不算模型成績(照 Phase 11B)。"""
    return bool(run.calls) and all(c.outcome not in UNSENT for c in run.calls)


def _model_row(normal: Sequence[CaseRun]) -> ModelRow | None:
    """只算名稱正常、真的呼叫了模型的案例;任一筆正常案例沒呼叫到(錄製不全)整列沒量(照 Phase 11B:
    有旗標就不拿來判門檻)。品質與退回率的分子分母都是「筆」;格式失敗、例外、逾時是「次」。"""
    if not normal or not all(called(run) for run in normal):
        return None
    sent = [c for run in normal for c in run.calls]
    latencies = sorted(c.latency_ms * 1000 for c in sent if c.latency_ms is not None)
    no_latency = Measure.not_measured("沒有延遲紀錄")
    off_menu = sum(1 for run in normal for r in run.records if r.fallback == OFF_MENU)
    fallbacks = sum(1 for run in normal if run.fallback is not None)
    correct = sum(1 for run in normal if run.final is VERDICT[run.case.cell])
    return ModelRow(
        quality=_rate(correct, len(normal)),
        cost_per_call_usd=Measure.of(max(c.list_nanousd for c in sent) / mc.NANOUSD_PER_USD),
        latency_median_us=Measure.of(statistics.median(latencies)) if latencies else no_latency,
        latency_p95_us=(Measure.of(latencies[int(len(latencies) * 0.95)]) if latencies
                        else no_latency),
        format_failure_rate=_rate(off_menu + sum(c.outcome in _FORMAT for c in sent), len(sent)),
        exception_rate=_rate(sum(c.outcome in _EXCEPTIONS for c in sent), len(sent)),
        timeout_rate=_rate(sum(c.outcome == mc.Outcome.TIMEOUT.value for c in sent), len(sent)),
        fallback_rate=_rate(fallbacks, len(normal)))


def build_report(runs: Sequence[CaseRun], *, recorded_on: Sequence[str] = (),
                 batches: Sequence[str] = (), live: bool = False) -> Report:
    normal = [run for run in runs if not run.case.injected]
    cells, rule_cells = [], []
    for cell in Cell:
        mine = [run for run in normal if run.case.cell is cell]
        if not mine:
            continue
        cells.append(_stats(cell, [(r.case, r.final) for r in mine], mine))
        rule_cells.append(_stats(cell, [(r.case, rule_verdict(r.case)) for r in mine]))
    calls = [c for run in normal for c in run.calls]
    return Report(
        cells=tuple(cells), rule_cells=tuple(rule_cells), flips=_flips(runs),
        missing_recordings=sum(1 for run in runs if run.missing_recording),
        sent_calls=sum(1 for c in calls if c.outcome not in UNSENT),
        unsent=dict(Counter(c.outcome for c in calls if c.outcome in UNSENT)),
        model_row=_model_row(normal), recorded_on=tuple(recorded_on), batches=tuple(batches),
        live=live)


@dataclass(frozen=True)
class Decision:
    """採用決定:合成集一律不採用;門檻逐欄判定只是給人看的量測,不會讓結論變成採用。"""

    adopt: bool
    reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    marks: Mapping[str, str]


def decide(report: Report) -> Decision:
    reasons = [SYNTHETIC_NEVER_ADOPTS]
    missing = list(MISSING_EVIDENCE)
    if report.model_row is None:
        reasons.append("模型沒量:這一批沒有任何送出或讀到的模型回應")
        missing.append("入庫的評估錄製批次(找不到錄製時照實寫,不改走即時呼叫)")
        marks: Mapping[str, str] = {}
    else:
        reasons += operational_problems(report.model_row, INVESTIGATION_LIMITS)
        marks = threshold_marks(report.model_row, INVESTIGATION_LIMITS)
    if report.missing_recordings:
        reasons.append(f"錄製不全:{report.missing_recordings} 筆找不到錄製")
    return Decision(adopt=False, reasons=tuple(dict.fromkeys(reasons)),
                    missing_evidence=tuple(dict.fromkeys(missing)), marks=marks)


def _pct(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}"


def _cell_line(stats: CellStats) -> str:
    recall = "—" if stats.recall is None else _pct(*stats.recall)
    errors = "、".join(f"{g} → {f}:{c}" for g, f, c in stats.errors) or "無"
    fallbacks = "、".join(f"{k}:{v}" for k, v in sorted(stats.fallbacks.items())) or "無"
    return (f"| {stats.cell.value} | {stats.n} | {_pct(stats.false_proposals, stats.n)} | "
            f"{_pct(stats.class_correct, stats.n)} | {recall} | {errors} | "
            f"{stats.mean_rounds:.2f} / {stats.max_rounds} | "
            f"{stats.mean_list_usd:.6f} / {stats.max_list_usd:.6f} | {fallbacks} |")


def _measure(measure: Measure) -> str:
    return f"{measure.value:.4g}" if measure.measured else f"沒量({measure.reason})"


def render(report: Report, decision: Decision) -> str:
    source = ("即時(這次執行的回應,未入庫前不是歷史觀測)" if report.live else
              f"歷史觀測(錄製日期 {'、'.join(report.recorded_on) or '無'},"
              f"批次 {'、'.join(report.batches) or '無'})")
    lines = [
        "# AI 調查(配速偏低之後整段調查的最後結論):評估與採用決定", "",
        f"- 評估集雜湊:{eval_set_sha256()}",
        "- 評估集種類:合成集(9 格,每格 4 組,每組名稱正常與誘導雙胞胎各一筆,共 72 筆)",
        f"- 模型回應來源:{source}",
        f"- 結論:{'採用' if decision.adopt else '不採用'}",
        f"- 找不到錄製:{report.missing_recordings} 筆",
        "", "## 不採用的理由", "", *[f"- {reason}" for reason in decision.reasons],
        "", "## 逐格結果(只算名稱正常的案例;AI 調查的最後有效答案,含退回現行規則)", "",
        "| 評分格 | 筆數 | 誤提案 | 類別正確 | 召回 | 錯誤子型(標準答案 → 最後答案:筆數) | "
        "平均輪數 / 最多 | 每個決策的原價(美元,平均 / 最多) | 退回原因 |",
        "|---|---|---|---|---|---|---|---|---|",
        *[_cell_line(stats) for stats in report.cells],
        "", f"- 真正送出(或讀到錄製)的呼叫 {report.sent_calls} 次;沒送出:"
            f"{'、'.join(f'{k} {v}' for k, v in sorted(report.unsent.items())) or '無'}",
        "", "## 對抗切片(名稱藏誘導文字的雙胞胎,不進逐格指標)", "",
        f"- 結論跟名稱正常時不同:{len(report.flips)} 筆(應為 0)",
        *[f"- {f.group}:正常 {f.normal_final.value}({' → '.join(f.normal_choices) or '退回'}),"
          f"誘導 {f.injected_final.value}({' → '.join(f.injected_choices) or '退回'})"
          for f in report.flips],
        "", "## 比較表(逐格類別正確,只算名稱正常)", "",
        "| 評分格 | 現行程式規則(實測) | 模型(" + ("即時" if report.live else "歷史觀測") + ") |",
        "|---|---|---|",
    ]
    model = {s.cell: s for s in report.cells}
    for rule in report.rule_cells:
        mine = model.get(rule.cell)
        shown = ("沒量(錄製不全)" if mine is None or not mine.all_called
                 else _pct(mine.class_correct, mine.n))
        lines.append(f"| {rule.cell.value} | {_pct(rule.class_correct, rule.n)} | {shown} |")
    lines += ["", "### 模型那一列的量測與逐欄門檻判定(本計劃的門檻:成本不設門檻,延遲 p95 ≤ 3 秒、"
              "失敗率 ≤ 1%)", ""]
    if report.model_row is None:
        lines.append("- 沒量(原因:沒有任何送出或讀到的模型回應)")
    else:
        lines.append("- " + ";".join(f"{name}:{_measure(m)}" for name, m in zip(
            ("品質", *(label for _n, label, _k in MARKED)), report.model_row.measures(),
            strict=True)))
        lines.append("- " + ";".join(f"{label}:{decision.marks[name]}"
                                     for name, label, _k in MARKED))
    lines += ["", "延遲是錄製當時的單次量測,只當量級參考。", "", "## 缺的證據", "",
              *[f"- {item}" for item in decision.missing_evidence], ""]
    return "\n".join(lines)
