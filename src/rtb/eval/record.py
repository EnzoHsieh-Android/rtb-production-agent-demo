"""比較表與人讀的決定紀錄(Phase 10 增量 2,[S709]、[S710])。

決定紀錄由純函式 `render` 從結構化的報告、比較表與採用決定產生,結論、理由與缺的證據全部從同一個
採用結果衍生(採用時不印不採用理由),記下它依據的評估集雜湊。延遲量法照前掃:行程內先暖身,再用
perf_counter_ns 量上萬次取中位與 p95,逐格量。現行程式規則實測;LLM 與 Jev 沒有候選,寫「沒量、原因:
未導入」,不編數字、不做假呼叫。

命令列照專案的兩層形狀(`run` 回結束代碼、`main` 丟 SystemExit):`python -m rtb.eval.record`。評估套件
不准匯入維運套件(兩邊互不依賴),所以比照執行端的命令列各自建解析器、同一種形狀。
"""

import argparse
import statistics
import sys
import time
from typing import TextIO

from rtb.analyzer.policy import ValidatedCells, route
from rtb.domain.worth import WorthCell, WorthInput
from rtb.eval import eval_set
from rtb.eval.adoption import (
    Adoption,
    ComparisonRow,
    Measure,
    OperationalLimits,
    decide_adoption,
)
from rtb.eval.generator import from_rows
from rtb.eval.scoring import (
    ProductionReport,
    SyntheticReport,
    eval_set_sha256,
    score,
    synthetic_report,
)

EXIT_OK = 0
SCOPE_LIMIT = ("這次只評「要不要提案調整」列裡的「值不值得加」子判斷,不回答 Phase 0 路由表標 Jev 的"
               "「證據夠不夠」「下一步查什麼」「繼續或停止」三列。")
LEAKAGE = (
    "合成評估集由生成器照評分格的定義與欄位型別造出,生成器不讀決策規則與它的測試;標準答案由程式照"
    "評分表算。評估套件不被任何其他套件匯入,規則作者的程式讀不到評估集。規則作者看過逐筆答案後回頭"
    "調規則,就要換一批,換批要用決策指令記一筆理由與時間。正式環境隱藏集不進程式庫。"
)
NOT_INTRODUCED = "未導入"
WARMUP, RUNS = 2_000, 10_000
MEASURE_NAMES = ("quality", "cost_per_call_usd", "latency_median_us", "latency_p95_us",
                 "format_failure_rate", "exception_rate", "timeout_rate", "fallback_rate")


def measure_latency(inputs: tuple[WorthInput, ...]) -> tuple[float, float]:
    """現行程式規則經路由的每次延遲(微秒):中位、p95。"""
    for index in range(WARMUP):
        route(inputs[index % len(inputs)], None, ValidatedCells.NONE)
    samples = []
    for index in range(RUNS):
        worth_input = inputs[index % len(inputs)]
        started = time.perf_counter_ns()
        route(worth_input, None, ValidatedCells.NONE)
        samples.append((time.perf_counter_ns() - started) / 1000)
    samples.sort()
    return statistics.median(samples), samples[int(len(samples) * 0.95)]


def comparison_rows(
    latency: dict[WorthCell, tuple[float, float]], report: SyntheticReport
) -> tuple[ComparisonRow, ...]:
    """逐格一列:現行規則每格的品質(類別正確率)、成本 0、量到的延遲、失敗率 0;
    LLM 與 Jev 全部沒量。"""
    rows = []
    quality = {c.cell: c.class_correct_count / c.n for c in report.cells}
    for cell in WorthCell:
        median, p95 = latency[cell]
        rows.append(ComparisonRow(
            approach="現行程式規則", cell=cell, quality=Measure.of(quality[cell]),
            cost_per_call_usd=Measure.of(0.0), latency_median_us=Measure.of(median),
            latency_p95_us=Measure.of(p95), format_failure_rate=Measure.of(0.0),
            exception_rate=Measure.of(0.0), timeout_rate=Measure.of(0.0),
            fallback_rate=Measure.of(0.0)))
    for approach in ("LLM", "Jev"):
        rows += [ComparisonRow(approach=approach, cell=cell,
                               **{name: Measure.not_measured(NOT_INTRODUCED)
                                  for name in MEASURE_NAMES})
                 for cell in WorthCell]
    return tuple(rows)


def _cell(measure: Measure) -> str:
    return f"{measure.value:.4g}" if measure.measured else f"沒量({measure.reason})"


def _metric(metric_name: str, numerator: int, denominator: int, low: float | None) -> str:
    bound = "" if low is None else f",下界 {low:.3f}"
    return f"{metric_name} {numerator}/{denominator}{bound}"


def render(report: SyntheticReport | ProductionReport, rows: tuple[ComparisonRow, ...],
           adoption: Adoption) -> str:
    kind = "正式環境抽樣集" if isinstance(report, ProductionReport) else "合成集"
    sha = (report.provenance.sha256 if isinstance(report, ProductionReport)
           else report.eval_set_sha256)
    lines = [
        "# 「值不值得加」判斷點:評估與採用決定",
        "",
        f"- 評估集雜湊:{sha}",
        f"- 評估集種類:{kind}",
        f"- 結論:{'採用' if adoption.adopt else '不採用'} Jev",
    ]
    if adoption.adopt:
        lines.append(f"- 已驗證的格:{'、'.join(sorted(c.value for c in adoption.validated.cells))}")
    else:
        lines += ["", "## 不採用的理由", "", *[f"- {reason}" for reason in adoption.reasons]]
    lines += ["", f"適用範圍:{SCOPE_LIMIT}", "", "## 逐格結果", "",
              "| 評分格 | 筆數 | 指標(分子/分母) | 錯誤子型(標準答案 → 最後答案:筆數) |",
              "|---|---|---|---|"]
    for cell in report.cells:
        metrics = ";".join(_metric(m.name, m.numerator, m.denominator, m.lower_bound)
                           for m in cell.metrics)
        errors = "、".join(f"{g.value} → {f.value}:{c}" for g, f, c in cell.errors) or "無"
        lines.append(f"| {cell.cell.value} | {cell.n} | {metrics} | {errors} |")
    lines += ["", f"無關欄位擾動後答案改變的組數:{report.perturbation_changed}", "",
              "## 比較表", "",
              "| 做法 | 評分格 | 品質 | 每次成本(美元) | 延遲中位(微秒) | 延遲 p95(微秒) | "
              "格式失敗率 | 例外率 | 逾時率 | 退回率 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {row.approach} | {row.cell.value} | "
              + " | ".join(_cell(m) for m in row.measures()) + " |" for row in rows]
    lines += ["", "延遲是單次量測(本機、行程內),只當量級參考。", "", "## 逐格採用決定", ""]
    lines += [f"- {d.cell.value}:{'驗證過' if d.validated else '未驗證'}"
              f"({';'.join(d.reasons) or '全部達標'})" for d in adoption.cells]
    lines += ["", "## 缺的證據", "", *[f"- {item}" for item in adoption.missing_evidence], "",
              "## 外洩紀錄", "", LEAKAGE, ""]
    return "\n".join(lines)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用合成評估集評「值不值得加」判斷點,印出人讀的決定紀錄(只讀,不寫任何東西)")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, *, out: TextIO | None = None) -> int:
    _parse(argv)
    scenarios = from_rows(eval_set.ROWS)
    report = synthetic_report(score(scenarios, None, None), eval_set_sha256())
    latency = {cell: measure_latency(tuple(s.worth_input for s in scenarios if s.cell is cell))
               for cell in WorthCell}
    rows = comparison_rows(latency, report)
    adoption = decide_adoption(report, None, OperationalLimits(None, None, None, None))
    print(render(report, rows, adoption), file=out or sys.stdout)
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
