"""比較表與人讀的決定紀錄(Phase 10 增量 2,[S709]、[S710];Phase 11B 增量 1 接上模型候選)。

決定紀錄由純函式 `render` 從結構化的報告、比較表與採用決定產生,結論、理由與缺的證據全部從同一個
採用結果衍生(採用時不印不採用理由),記下它依據的評估集雜湊。延遲量法照前掃:行程內先暖身,再用
perf_counter_ns 量上萬次取中位與 p95,逐格量。現行程式規則實測;Jev 沒有候選,寫「沒量、原因:
未導入」,不編數字、不做假呼叫。

LLM 列(Phase 11B 增量 1):這支是三支模型入口之一,在入口讀模式(RTB_MODEL_LIVE=1、--demo-id、
在 PATH 上找得到 claude 三樣都有才即時,否則重播錄製;找到的 claude 絕對路徑往下傳)。
跑合成集的固定子集;即時跑產生批次紀錄(RTB_MODEL_RECORD=1 時連錄製檔一起寫進錄製目錄),
重播讀錄製目錄裡唯一的一份批次紀錄。模型列的成本、延遲、各種比率一律從批次紀錄算、標「歷史觀測」;
合成集照 Phase 10 規定一律不採用。模型的計分也走既有的計分與合成集報告,模型段另印逐格指標、錯誤
子型與擾動改變的組數;有旗標(未跑完、錄製不全、批次不一致、情境清單不同、沒有批次紀錄)時比較表的
LLM 列寫「沒量(原因:旗標)」。重播時批次紀錄的情境清單要跟這次子集一致。即時模式的花費帳寫死
帳號家目錄那一本,--ledger 只在錄製模式能用;預留時花費帳忙碌以專用結束代碼 9 結束。

命令列照專案的兩層形狀(`run` 回結束代碼、`main` 丟 SystemExit):`python -m rtb.eval.record`。評估套件
不准匯入維運套件(兩邊互不依賴),所以比照執行端的命令列各自建解析器、同一種形狀。
"""

import argparse
import math
import os
import shutil
import statistics
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from rtb import modelclient as mc
from rtb.analyzer.policy import MISSING_FOUR_QUERIES, RoutePath, ValidatedCells, route
from rtb.domain.worth import WorthCell, WorthInput
from rtb.eval import eval_set, model_candidate
from rtb.eval.adoption import (
    Adoption,
    ComparisonRow,
    Measure,
    OperationalLimits,
    decide_adoption,
    format_value,
)
from rtb.eval.generator import from_rows
from rtb.eval.scoring import (
    RULE_NOW,
    CellReport,
    ProductionReport,
    SyntheticReport,
    eval_set_sha256,
    score,
    synthetic_report,
)

EXIT_OK = 0
EXIT_BAD_ARGUMENTS = 2  # 跟 argparse 的參數錯同一個代碼
EXIT_LEDGER_BUSY = 9  # 花費帳忙碌:寫不進帳、沒有送出,由呼叫端看得到(計劃〈模型用戶端〉)
SCOPE_LIMIT = ("這次只評「要不要提案調整」列裡的「值不值得加」子判斷,不回答 Phase 0 路由表標 Jev 的"
               "「證據夠不夠」「下一步查什麼」「繼續或停止」三列。")
LEAKAGE = (
    "合成評估集由生成器照評分格的定義與欄位型別造出,生成器不讀決策規則與它的測試;標準答案由程式照"
    "評分表算。評估套件不被任何其他套件匯入,規則作者的程式讀不到評估集。規則作者看過逐筆答案後回頭"
    "調規則,就要換一批,換批要用決策指令記一筆理由與時間。正式環境隱藏集不進程式庫。"
)
NOT_INTRODUCED = "未導入"
CODE_RULE_APPROACH = "現行程式規則"
WARMUP, RUNS = 2_000, 10_000
MEASURE_NAMES = ("quality", "cost_per_call_usd", "latency_median_us", "latency_p95_us",
                 "format_failure_rate", "exception_rate", "timeout_rate", "fallback_rate")


def measure_latency(inputs: tuple[WorthInput, ...]) -> tuple[float, float]:
    """現行程式規則經路由的每次延遲(微秒):中位、p95。Phase 14 起規則是九條,Phase 10 情境沒有四查詢,
    量到的是明傳缺四查詢時的短路徑(暫停/異常判完或缺查詢即回),不含正式規則輪 A/B/C 的 DSP 讀取與九條
    全鏈(代碼審 r1 鏡頭4-2);報告同一段照寫。"""
    for index in range(WARMUP):
        route(inputs[index % len(inputs)], None, ValidatedCells.NONE,
              queries=MISSING_FOUR_QUERIES, now=RULE_NOW)
    samples = []
    for index in range(RUNS):
        worth_input = inputs[index % len(inputs)]
        started = time.perf_counter_ns()
        route(worth_input, None, ValidatedCells.NONE, queries=MISSING_FOUR_QUERIES, now=RULE_NOW)
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
            approach=CODE_RULE_APPROACH, cell=cell, quality=Measure.of(quality[cell]),
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
    """數字格走共用的 `adoption.format_value`(單位在欄名,格內不換算;不出科學記號)。"""
    return (format_value(measure.value) if measure.measured and measure.value is not None
            else f"沒量({measure.reason})")


def _metric(metric_name: str, numerator: int, denominator: int, low: float | None) -> str:
    bound = "" if low is None else f",下界 {low:.3f}"
    return f"{metric_name} {numerator}/{denominator}{bound}"


def cell_table(cells: tuple[CellReport, ...]) -> list[str]:
    """逐格結果表(現行規則與模型候選共用):筆數、指標、錯誤子型。"""
    lines = ["| 評分格 | 筆數 | 指標(分子/分母) | 錯誤子型(標準答案 → 最後答案:筆數) |",
             "|---|---|---|---|"]
    for cell in cells:
        metrics = ";".join(_metric(m.name, m.numerator, m.denominator, m.lower_bound)
                           for m in cell.metrics)
        errors = "、".join(f"{g.value} → {f.value}:{c}" for g, f, c in cell.errors) or "無"
        name = cell.cell.value if cell.note is None else f"{cell.cell.value}({cell.note})"
        lines.append(f"| {name} | {cell.n} | {metrics} | {errors} |")
    return lines


def _row_cell(row: ComparisonRow, notes: Mapping[WorthCell, str | None]) -> str:
    """比較表「現行程式規則」列的格名帶逐格結果同一個標註(Phase 14 增量 4 代碼審 r1:單獨截表
    不失警語)。"""
    note = notes.get(row.cell) if row.approach == CODE_RULE_APPROACH else None
    return row.cell.value if note is None else f"{row.cell.value}({note})"


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
    lines += ["", f"適用範圍:{SCOPE_LIMIT}", "", "## 逐格結果", "", *cell_table(report.cells)]
    lines += ["", f"無關欄位擾動後答案改變的組數:{report.perturbation_changed}", "",
              "## 比較表", "",
              "| 做法 | 評分格 | 品質 | 每次成本(美元) | 延遲中位(微秒) | 延遲 p95(微秒) | "
              "格式失敗率 | 例外率 | 逾時率 | 退回率 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    notes = {cell.cell: cell.note for cell in report.cells}
    lines += [f"| {row.approach} | {_row_cell(row, notes)} | "
              + " | ".join(_cell(m) for m in row.measures()) + " |" for row in rows]
    lines += ["", "延遲是單次量測(本機、行程內),只當量級參考;現行程式規則那幾列量的是缺四查詢時"
              "的九條短路徑,不含正式規則輪 A/B/C 的讀取與九條全鏈。現行程式規則三格品質 1 的"
              "意義不同:paused、anomaly 兩格九條用基本資料就判得出(品質 1 有效);"
              "delivery_without_value 的品質 1 是缺四查詢時一律回證據不足、恰好等於標準答案,"
              "不是九條真的判對。", "", "## 逐格採用決定", ""]
    lines += [f"- {d.cell.value}:{'驗證過' if d.validated else '未驗證'}"
              f"({';'.join(d.reasons) or '全部達標'})" for d in adoption.cells]
    lines += ["", "## 缺的證據", "", *[f"- {item}" for item in adoption.missing_evidence], "",
              "## 外洩紀錄", "", LEAKAGE, ""]
    return "\n".join(lines)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="用合成評估集評「值不值得加」判斷點,印出人讀的決定紀錄;LLM 列跑模型候選"
                    "(預設重播錄製,只寫花費帳與即時錄製)")
    parser.add_argument("--demo-id", help="展示編號;即時模式必填,同一個編號共用 1 美元")
    parser.add_argument("--recordings-dir", type=Path,
                        help="錄製目錄(預設專案根的 recordings/model)")
    parser.add_argument("--ledger", type=Path, help="只在錄製模式能用:花費帳換到別的路徑")
    return parser.parse_args(argv)


@dataclass(frozen=True)
class ModelSection:
    rows: Mapping[WorthCell, ComparisonRow] | None  # 有批次紀錄才有
    flags: tuple[str, ...]  # 未跑完、錄製不全、批次不一致、沒有批次紀錄
    lines: tuple[str, ...]
    ledger_busy: bool


def _replayed_batch(run: model_candidate.ModelRun, recordings: Path
                    ) -> tuple[model_candidate.BatchRecord | None, list[str]]:
    """重播時找唯一一份批次紀錄,並跟這次重播逐列核對:情境清單(列數、順序)要一樣,有錄製的列結果
    類別與原價要跟錄製檔一樣;對不上就掛旗標、不拿它算門檻(代碼審第 2 輪:缺列的批次曾被當成完整)。"""
    flags = []
    found = sorted(model_candidate.batches_dir(recordings).glob("*.json"))
    if not found:
        return None, ["沒有批次紀錄"]
    try:
        batch = model_candidate.load_batch(found[0])
    except model_candidate.BatchInvalid as bad:
        return None, [f"批次紀錄讀不懂({bad})"]
    if len(found) > 1 or not run.recording_batches <= {batch.batch_id}:
        flags.append("批次不一致(錄製檔與批次紀錄的批次編號對不上,或不只一份批次紀錄)")
    if [r.scenario_id for r in batch.rows] != [r.scenario_id for r in run.rows]:
        flags.append("批次紀錄的情境清單跟這次子集不同(缺列、多列或順序不同)")
    elif any(_disagrees(kept, replayed, shared)
             for kept, replayed, shared in zip(batch.rows, run.rows, _expected_shared(run),
                                               strict=True)):
        flags.append("批次紀錄的數字跟錄製檔對不上(結果類別、判定、原價、延遲或共用標記)")
    if batch.interrupted:
        flags.append("未跑完(中斷)")
    if run.missing_recordings:
        flags.append(f"錄製不全({run.missing_recordings} 個情境沒有錄製)")
    return batch, flags


def _expected_shared(run: model_candidate.ModelRun) -> list[bool]:
    """同一批七個欄位相同的情境,第一次之後的都該標共用(照這次子集的提示算,不信批次紀錄自己寫的)。"""
    seen: set[str] = set()
    shared = []
    for case in run.scored:
        prompt = model_candidate.prompt_for(case.scenario.worth_input)
        shared.append(prompt in seen)
        seen.add(prompt)
    return shared


def _disagrees(kept: model_candidate.BatchRow, replayed: model_candidate.BatchRow,
               shared: bool) -> bool:
    """批次紀錄的一列跟重播的同一列,凡是會進門檻計算的欄位(結果類別、判定、原價、延遲、共用與送出)
    有一個對不上就是 True;重播沒有錄製的列(沒得比)只比共用標記(代碼審第 3 輪:只改延遲曾騙過)。"""
    if kept.shared != shared:
        return True
    if replayed.outcome == mc.Outcome.NO_RECORDING.value:
        return False
    expected_sent = not shared and kept.outcome not in model_candidate.UNSENT
    same_latency = (kept.latency_ms is None) == (replayed.latency_ms is None) and (
        kept.latency_ms is None or replayed.latency_ms is None
        or math.isclose(kept.latency_ms, replayed.latency_ms, rel_tol=1e-9, abs_tol=1e-6))
    return not (kept.outcome == replayed.outcome and kept.verdict == replayed.verdict
                and kept.list_nanousd == replayed.list_nanousd and same_latency
                and kept.sent == expected_sent)


def _model_run(settings: mc.Settings, args: argparse.Namespace, errors: TextIO) -> tuple[
        model_candidate.ModelRun, model_candidate.BatchRecord | None, list[str]]:
    recordings = args.recordings_dir or mc.default_recordings_dir()
    live = settings.mode is mc.Mode.LIVE
    # 錄製模式沒帶 --ledger:共用的 recorded_ledger 建暫存帳本並印路徑,不退回真帳本(Phase 14 代碼審
    # r2、r3)
    ledger = mc.recorded_ledger(settings.mode, args.ledger,
                                lambda text: print(text, file=errors)) or mc.live_ledger_path()
    batch_id = f"{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:8]}" if live else None
    candidate = model_candidate.ModelCandidate(settings, recordings_dir=recordings, ledger=ledger,
                                               demo_id=args.demo_id, batch_id=batch_id)
    chosen = model_candidate.subset(from_rows(eval_set.ROWS))
    try:
        run = model_candidate.run_subset(chosen, candidate, model_candidate.TIMEOUT_SECONDS)
    except BaseException:
        # 中斷或崩掉:花過錢的嘗試照樣留批次紀錄(標中斷),再往外丟
        if live and settings.record and batch_id is not None:
            model_candidate.write_batch(recordings, model_candidate.new_batch(
                batch_id, settings.model, model_candidate.partial_rows(chosen, candidate),
                interrupted=True))
        raise
    if not live:
        batch, flags = _replayed_batch(run, recordings)
    else:
        assert batch_id is not None  # noqa: S101 - 即時模式上面一定產生
        batch, flags = model_candidate.new_batch(batch_id, settings.model, run.rows), []
        if settings.record:
            model_candidate.write_batch(recordings, batch)
    if run.stopped is not None:
        reason = _STOP_TEXT.get(run.stopped, run.stopped)
        last = candidate.attempts[-1] if candidate.attempts else None
        if last is not None and last.sub_reason == "recording_conflict" and (
                last.recording_batch_id is not None):
            other = last.recording_batch_id
            reason += (f";錄製目錄裡已有批次 {other} 的錄製或佔位,要重錄就先整批刪掉批次 {other} 的"
                       "錄製檔與批次紀錄")
        flags.insert(0, f"未跑完(停在第 {len(run.rows)} 個情境:{reason})")
    return run, batch, flags


_STOP_TEXT = {"local_cap_refused": "已達上限", "config_error": "設定錯誤",
              "quota_exhausted": "訂閱額度用完", "ledger_busy": "花費帳忙碌",
              "overrun": "超支", model_candidate.UNCLASSIFIED: "無法可靠分類的錯誤",
              model_candidate.TOOL_USE: "偵測到工具使用"}


def _cell_line(cell: WorthCell, row: ComparisonRow, means: Mapping[WorthCell, float]) -> str:
    marks = model_candidate.threshold_marks(row, model_candidate.MODEL_LIMITS)
    parts = [f"{label}:{marks[name]}" for name, label, _ in model_candidate.MARKED]
    mean = means.get(cell)
    reference = "" if mean is None else f";平均每次成本 {mean:.6f} 美元(只供參考)"
    return f"- {cell.value}:{';'.join(parts)}{reference}"


def _subset_line(run: model_candidate.ModelRun) -> str:
    """模型一次都沒被呼叫到時照實寫沒有跑,不寫「跑了 N 個情境」(Phase 14 增量 4 代碼審 r1)。"""
    subset = (f"{len(run.rows)} 個情境(子集每格 {model_candidate.GROUPS_PER_CELL} 組、"
              "每組 3 個變體)")
    if not any(row.outcome not in model_candidate.UNSENT for row in run.rows):
        return f"- 候選子集 {subset}:沒有跑——模型一次都沒被呼叫到(沒有錄製或呼叫前就被擋下)"
    return f"- 跑了 {subset}"


def model_section(settings: mc.Settings, args: argparse.Namespace,
                  errors: TextIO) -> ModelSection:
    run, batch, flags = _model_run(settings, args, errors)
    rows = None if batch is None else model_candidate.model_rows(batch, run.scored)
    lines = ["", f"## 模型候選({settings.model})", "",
             f"- 模式:{'即時' if settings.mode is mc.Mode.LIVE else '重播錄製回應(不是即時呼叫)'}",
             _subset_line(run)]
    if batch is not None:
        shared = sum(1 for r in batch.rows if r.shared)
        sent = sum(1 for r in batch.rows if r.sent)
        live_unsaved = settings.mode is mc.Mode.LIVE and not settings.record
        source = (f"- 來源:即時、未存檔(批次 {batch.batch_id} 只在這次執行的記憶體裡,"
                  f"價目表查核 {batch.price_checked_on})" if live_unsaved else
                  f"- 來源:歷史觀測(錄製日期 {batch.recorded_on},批次 {batch.batch_id},"
                  f"價目表查核 {batch.price_checked_on})")
        lines += [source,
                  f"- 批次紀錄 {len(batch.rows)} 列:真正送出 {sent} 次、"
                  f"{model_candidate.SHARED_NOTE} {shared} 列、"
                  f"沒送出 {model_candidate.unsent_counts(batch) or '無'}"]
        lines += ["", "### 逐格門檻判定(每次成本 ≤ 0.002 美元、延遲 p95 ≤ 3 秒、失敗率 ≤ 1% "
                  "是使用者裁定;延遲中位 ≤ 3 秒是協調者補的)", ""]
        if flags:  # 有旗標的批次不拿來判門檻(代碼審第 3 輪:改過的延遲曾讓 p95 變成「過」)
            lines.append("- 不判:這一批有旗標(見下),門檻不用它算")
        else:
            means = model_candidate.mean_costs(batch)
            lines += [_cell_line(cell, row, means) for cell, row in (rows or {}).items()]
    lines += ["", *_model_scores(run)]
    lines += ["", *[f"- 旗標:{flag}" for flag in flags],
              "- 模型候選:不採用(合成集是有限的合約案例,照 Phase 10 規定一律不採用"
              + ("" if not flags else ";另有上面的旗標") + ")"]
    if rows is not None and not flags:
        lines.append("- 上面「不採用的理由」是 Phase 10 的固定文字(合成集一律列缺正式紀錄、"
                     "人工標註與"
                     "候選實測);候選實測已有,見上")
    busy = run.stopped == mc.Outcome.LEDGER_BUSY.value
    return ModelSection(None if flags else rows, tuple(flags), tuple(lines), busy)


def _model_scores(run: model_candidate.ModelRun) -> list[str]:
    """模型逐格結果:只算真的呼叫了模型(或同批共用別人的答案)的情境;沒呼叫的(沒有錄製、上限拒絕、
    設定錯誤、花費帳忙碌)不進模型計分(否則印的是現行規則的答案)。另列實際作答、退回、沒呼叫件數。"""
    called = [case for case, row in zip(run.scored, run.rows, strict=False)
              if row.outcome not in model_candidate.UNSENT]
    lines = ["### 模型逐格結果", ""]
    if not called:
        return [*lines, "- 沒量(原因:模型一次都沒被呼叫到,例如沒有錄製或呼叫前就被擋下)"]
    scored = synthetic_report(tuple(called), eval_set_sha256())  # 模型的計分走既有的計分與報告
    counts = []
    for cell in WorthCell:
        pairs = [(c, r) for c, r in zip(run.scored, run.rows, strict=False)
                 if c.scenario.cell is cell]
        unsent = sum(1 for _, r in pairs if r.outcome in model_candidate.UNSENT)
        answered = sum(1 for c, r in pairs if r.outcome not in model_candidate.UNSENT
                       and c.path is RoutePath.CANDIDATE)
        fallback = len(pairs) - unsent - answered
        counts.append(f"| {cell.value} | {answered} | {fallback} | {unsent} |")
    return [*lines, *cell_table(scored.cells), "",
            "| 評分格 | 實際作答 | 退回 | 沒呼叫 |", "|---|---|---|---|", *counts, "",
            f"- 無關欄位擾動後答案改變的組數(模型):{scored.perturbation_changed}(只算有呼叫的情境)"]


def with_model_rows(rows: tuple[ComparisonRow, ...], model: ModelSection) -> tuple[
        ComparisonRow, ...]:
    """比較表的 LLM 列換成模型候選實測;有旗標時寫「沒量(原因:旗標)」。"""
    if model.rows is not None:
        return tuple(model.rows[row.cell] if row.approach == "LLM" else row for row in rows)
    if not model.flags:
        return rows
    reason = "原因:" + "、".join(flag.split("(")[0] for flag in model.flags)
    return tuple(ComparisonRow(approach="LLM", cell=row.cell,
                               **{name: Measure.not_measured(reason) for name in MEASURE_NAMES})
                 if row.approach == "LLM" else row for row in rows)


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        err: TextIO | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    try:
        source = os.environ if environ is None else environ
        claude = shutil.which("claude", path=source.get("PATH", ""))  # 只有入口查 PATH
        settings = mc.settings_from_env(source, args.demo_id, claude)
    except mc.UnknownModel as unknown:
        print(f"參數錯誤:{unknown}", file=errors)
        return EXIT_BAD_ARGUMENTS
    if settings.mode is mc.Mode.LIVE and args.ledger is not None:
        print("即時模式的花費帳寫死在家目錄那一本,不接受 --ledger", file=errors)
        return EXIT_BAD_ARGUMENTS
    for notice in settings.notices:
        print(notice, file=errors)
    scenarios = from_rows(eval_set.ROWS)
    report = synthetic_report(score(scenarios, None, None), eval_set_sha256())
    latency = {cell: measure_latency(tuple(s.worth_input for s in scenarios if s.cell is cell))
               for cell in WorthCell}
    model = model_section(settings, args, errors)
    if model.ledger_busy:
        print("花費帳忙碌:寫不進帳,已停止呼叫模型", file=errors)
        return EXIT_LEDGER_BUSY
    rows = with_model_rows(comparison_rows(latency, report), model)
    decided = (decide_adoption(report, None, OperationalLimits(None, None, None, None))
               if model.rows is None else
               decide_adoption(report, dict(model.rows), model_candidate.MODEL_LIMITS))
    print(render(report, rows, decided) + "\n".join(model.lines), file=out or sys.stdout)
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
