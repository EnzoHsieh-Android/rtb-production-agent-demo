"""比較表與人讀的決定紀錄(Phase 10 增量 2,[S709]、[S710])。

決定紀錄由純函式 `render` 從結構化的報告、比較表與採用決定產生,記下它依據的評估集雜湊;延遲量法照
前掃:行程內先暖身,再用 perf_counter_ns 量上萬次取中位與 p95。現行程式規則實測;LLM 與 Jev 沒有候選,
寫「沒量、原因:未導入」,不編數字、不做假呼叫。產生:`python -m rtb.eval.record`。
"""

import statistics
import time

from rtb.analyzer.policy import ValidatedCells, route
from rtb.domain.worth import WorthInput
from rtb.eval import eval_set
from rtb.eval.adoption import (
    Adoption,
    ComparisonRow,
    Measure,
    OperationalLimits,
    decide_adoption,
)
from rtb.eval.generator import from_rows
from rtb.eval.scoring import SYNTHETIC, Report, build_report, eval_set_sha256, score

# 使用者 2026-09-24 本人裁定的兩個不採用理由(計劃〈使用者裁定〉)
NOT_ADOPTED_REASONS = (
    "照評分表,這個判斷可以由程式精確算出,本來就該是程式;現行規則不準記成程式缺陷",
    "缺正式環境紀錄、人工標註與候選實測",
)
MISSING_EVIDENCE = (
    "正式環境的決策紀錄抽樣",
    "人工標註",
    "候選模型在每一格的實測(品質、成本、延遲、失敗率)",
    "上線後逐格監測的機制",
)
SCOPE_LIMIT = ("這次只評「要不要提案調整」列裡的「值不值得加」子判斷,不回答 Phase 0 路由表標 Jev 的"
               "「證據夠不夠」「下一步查什麼」「繼續或停止」三列。")
LEAKAGE = (
    "合成評估集由生成器照評分格的定義與欄位型別造出,生成器不讀決策規則與它的測試;標準答案由程式照"
    "評分表算。評估套件不被任何其他套件匯入,規則作者的程式讀不到評估集。規則作者看過逐筆答案後回頭"
    "調規則,就要換一批,換批要用決策指令記一筆理由與時間。正式環境隱藏集不進程式庫。"
)
NOT_INTRODUCED = "未導入"
WARMUP, RUNS = 2_000, 20_000


def measure_latency(inputs: tuple[WorthInput, ...]) -> tuple[float, float]:
    """現行程式規則經路由的每次延遲(微秒):中位、p95。"""
    for index in range(WARMUP):
        route(inputs[index % len(inputs)], None, ValidatedCells.NONE, timeout_seconds=0.0)
    samples = []
    for index in range(RUNS):
        worth_input = inputs[index % len(inputs)]
        started = time.perf_counter_ns()
        route(worth_input, None, ValidatedCells.NONE, timeout_seconds=0.0)
        samples.append((time.perf_counter_ns() - started) / 1000)
    samples.sort()
    return statistics.median(samples), samples[int(len(samples) * 0.95)]


def _not_measured() -> dict[str, Measure]:
    names = ("quality", "cost_per_call_usd", "latency_median_us", "latency_p95_us",
             "format_failure_rate", "exception_rate", "timeout_rate", "fallback_rate")
    return {name: Measure.not_measured(NOT_INTRODUCED) for name in names}


def comparison_rows(code_rule_latency: tuple[float, float]) -> tuple[ComparisonRow, ...]:
    scenarios = from_rows(eval_set.ROWS)
    scored = score(scenarios, None, None, 0.0)
    quality = sum(1 for s in scored if s.final is s.scenario.gold) / len(scored)
    median, p95 = code_rule_latency
    code = ComparisonRow(
        approach="現行程式規則", quality=Measure.of(quality), cost_per_call_usd=Measure.of(0.0),
        latency_median_us=Measure.of(median), latency_p95_us=Measure.of(p95),
        format_failure_rate=Measure.of(0.0), exception_rate=Measure.of(0.0),
        timeout_rate=Measure.of(0.0), fallback_rate=Measure.of(0.0))
    return (code, ComparisonRow(approach="LLM", **_not_measured()),
            ComparisonRow(approach="Jev", **_not_measured()))


def _cell(measure: Measure) -> str:
    return f"{measure.value:.4g}" if measure.measured else f"沒量({measure.reason})"


def render(report: Report, rows: tuple[ComparisonRow, ...], adoption: Adoption) -> str:
    lines = [
        "# 「值不值得加」判斷點:評估與採用決定",
        "",
        f"- 評估集雜湊:{report.eval_set_sha256}",
        f"- 評估集種類:{report.kind}",
        f"- 結論:{'採用' if adoption.adopt else '不採用'} Jev",
        "",
        "## 不採用的理由",
        "",
        *[f"- {reason}" for reason in NOT_ADOPTED_REASONS],
        *[f"- {reason}" for reason in adoption.reasons],
        "",
        f"適用範圍:{SCOPE_LIMIT}",
        "",
        "## 現行程式規則逐格結果",
        "",
        "| 評分格 | 筆數 | 指標 | 分子/分母 | 類別正確 | 錯誤子型(標準答案 → 最後答案:筆數) |",
        "|---|---|---|---|---|---|",
    ]
    for cell in report.cells:
        errors = "、".join(f"{g.value} → {f.value}:{c}" for g, f, c in cell.errors) or "無"
        lines.append(f"| {cell.cell.value} | {cell.n} | {cell.metric} | "
                     f"{cell.numerator}/{cell.denominator} | {cell.class_correct_count}/{cell.n} | "
                     f"{errors} |")
    lines += ["", f"無關欄位擾動後答案改變的組數:{report.perturbation_changed}", "",
              "## 比較表", "",
              "| 做法 | 品質 | 每次成本(美元) | 延遲中位(微秒) | 延遲 p95(微秒) | 格式失敗率 | "
              "例外率 | 逾時率 | 退回率 |",
              "|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {row.approach} | " + " | ".join(_cell(m) for m in row.measures()) + " |"
              for row in rows]
    lines += ["", "延遲是單次量測(本機、行程內),只當量級參考。", "", "## 逐格採用決定", ""]
    lines += [f"- {d.cell.value}:{'驗證過' if d.validated else '未驗證'}"
              f"({';'.join(d.reasons) or '全部達標'})" for d in adoption.cells]
    lines += ["", "## 缺的證據", "", *[f"- {item}" for item in MISSING_EVIDENCE], "",
              "## 外洩紀錄", "", LEAKAGE, ""]
    return "\n".join(lines)


def main() -> None:
    scenarios = from_rows(eval_set.ROWS)
    report = build_report(SYNTHETIC, score(scenarios, None, None, 0.0), eval_set_sha256())
    rows = comparison_rows(measure_latency(tuple(s.worth_input for s in scenarios)))
    adoption = decide_adoption(report, None, OperationalLimits(None, None, None))
    print(render(report, rows, adoption))


if __name__ == "__main__":
    main()
