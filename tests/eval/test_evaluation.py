"""Phase 10 增量 2:評估套件。

合約 [S706] 到 [S714]、[S716],以及 [S711] 評分表那半(增量 1 驗了每筆可建構輸入恰好落一格)。
"""

import ast
import hashlib
import itertools
import subprocess
import sys
from pathlib import Path

import pytest

from rtb.analyzer import policy
from rtb.domain.worth import CampaignStatus, WorthCell, WorthInput, WorthVerdict, cell_of
from rtb.eval import adoption, eval_set, generator, record, rubric, scoring

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"
EVAL = SRC / "eval"
EVAL_SET = EVAL / "eval_set.py"
# 合成評估集的雜湊:改了評估集就要同時改這裡,並用決策指令記一筆換批理由與時間(計劃〈外洩紀錄〉)
EVAL_SET_SHA256 = "b9380338e65b22345804a49e27e025a6c613c457717fde14575d0c96ab0d2ac2"


def _scenarios():
    return generator.from_rows(eval_set.ROWS)


def _code_rule_report():
    return scoring.build_report(scoring.SYNTHETIC, scoring.score(_scenarios(), None, None),
                                scoring.eval_set_sha256())


def _call(judge):
    return policy.CandidateCall(judge, 1.0)


class _Answer:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def __call__(self, worth_input, timeout_seconds):
        self.calls += 1
        return self.answer(worth_input) if callable(self.answer) else self.answer


# ---- [S711] 評分表那半 ----
def test_the_rubric_gives_exactly_one_class_for_every_input():
    assert set(rubric.RUBRIC) == set(WorthCell)
    assert WorthVerdict.UNSURE not in rubric.RUBRIC.values()
    assert rubric.RUBRIC == {WorthCell.PAUSED: WorthVerdict.NOT_WORTH,
                             WorthCell.ANOMALY: WorthVerdict.INSUFFICIENT,
                             WorthCell.NO_DELIVERY: WorthVerdict.NOT_WORTH,
                             WorthCell.DELIVERY_WITH_VALUE: WorthVerdict.WORTH,
                             WorthCell.DELIVERY_WITHOUT_VALUE: WorthVerdict.INSUFFICIENT}
    counts, money = (None, -1, 0, 1, 5), (None, -1.0, 0.0, 3.0)
    classes = set()
    for status, impressions, clicks, conversions, revenue, spend in itertools.product(
            CampaignStatus, counts, counts, counts, money, money):
        worth_input = WorthInput(status=status, budget=100, spend=spend, impressions=impressions,
                                 clicks=clicks, conversions=conversions, revenue=revenue)
        verdict = rubric.gold(worth_input)
        assert verdict is rubric.RUBRIC[cell_of(worth_input)]
        classes.add(verdict)
    assert classes == {WorthVerdict.WORTH, WorthVerdict.NOT_WORTH, WorthVerdict.INSUFFICIENT}


# ---- [S706] ----
def test_the_eval_set_is_pinned_by_hash():
    assert hashlib.sha256(EVAL_SET.read_bytes()).hexdigest() == EVAL_SET_SHA256
    assert scoring.eval_set_sha256() == EVAL_SET_SHA256
    # 評估集就是生成器用固定種子的產出,標準答案由程式照評分表算
    assert generator.render(generator.generate(generator.SEED)) == EVAL_SET.read_text("utf-8")
    scenarios = _scenarios()
    assert all(s.gold is rubric.gold(s.worth_input) for s in scenarios)
    assert {s.cell for s in scenarios} == set(WorthCell)


# ---- [S717] ----
def _nonnegative(*values):
    return all(v is not None and v >= 0 for v in values)


def test_the_generator_covers_every_anomaly_and_matches_the_committed_set():
    scenarios = _scenarios()
    # 重跑生成器逐值比對已提交的常數模組,不只驗雜湊
    assert generator.generate(generator.SEED) == scenarios
    for s in scenarios:  # 用評分表重算的格等於它歸檔的格
        assert s.cell is s.filed_cell, s.scenario_id
    anomalies = [s for s in scenarios if s.filed_cell is WorthCell.ANOMALY]
    assert {s.fault for s in anomalies} == set(generator.FAULTS)
    for s in anomalies:  # 每筆只有它標的那一個故障
        i = s.worth_input
        values = {name: getattr(i, name) for name in generator.AMOUNT_FIELDS}
        if ":" in s.fault:
            name, kind = s.fault.split(":")
            assert (values[name] is None) == (kind == "missing"), s.scenario_id
            assert kind == "missing" or values[name] < 0, s.scenario_id
            assert _nonnegative(*(v for k, v in values.items() if k != name)), s.scenario_id
        else:
            assert _nonnegative(*values.values()), s.scenario_id
            assert (i.clicks > i.impressions) == (s.fault == "clicks>impressions")
            assert (i.conversions > i.clicks) == (s.fault == "conversions>clicks")
    assert all(s.fault == "" for s in scenarios if s.filed_cell is not WorthCell.ANOMALY)
    normal = [s.worth_input for s in scenarios
              if s.filed_cell not in (WorthCell.PAUSED, WorthCell.ANOMALY)]
    assert all(_nonnegative(i.impressions, i.clicks, i.conversions, i.revenue, i.spend)
               and i.impressions >= i.clicks >= i.conversions for i in normal)
    # 計劃釘住的邊界案例
    by_cell = {cell: [s.worth_input for s in scenarios if s.filed_cell is cell]
               for cell in WorthCell}
    assert any(i.revenue > 0 and i.conversions == 0
               for i in by_cell[WorthCell.DELIVERY_WITH_VALUE])
    assert any(i.conversions > 0 and i.revenue == 0
               for i in by_cell[WorthCell.DELIVERY_WITH_VALUE])
    assert any(i.spend is None for i in by_cell[WorthCell.ANOMALY])
    assert any(i.impressions > 0 and i.clicks == 0 for i in by_cell[WorthCell.NO_DELIVERY])


def test_the_generator_reads_neither_the_decision_rule_nor_its_tests():
    tree = ast.parse((EVAL / "generator.py").read_text("utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {a.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for a in node.names}
    assert not {m for m in imported if m and m.startswith(("rtb.analyzer", "tests"))}


# ---- [S707] ----
def test_the_eval_report_is_per_slice_and_marks_thin_slices():
    report = _code_rule_report()
    assert [c.cell for c in report.cells] == list(WorthCell)
    for cell in report.cells:
        assert cell.lower_bound is None  # 合成集不套統計信賴
        assert cell.denominator == cell.n > 0
        if cell.cell is WorthCell.DELIVERY_WITH_VALUE:
            assert cell.metric == scoring.RECALL
        else:
            assert cell.metric == scoring.FALSE_PROPOSAL_RATE
            assert cell.class_correct is not None
        assert sum(count for _, _, count in cell.errors) == cell.n - cell.class_correct_count
    # 依最後有效答案計分:候選回「不知道」退回現行規則,暫停中有投放的照樣被計成誤提案
    unsure = scoring.build_report(
        scoring.SYNTHETIC,
        scoring.score(_scenarios(), _call(_Answer(WorthVerdict.UNSURE)),
                      policy.TrialCells(frozenset(WorthCell))), scoring.eval_set_sha256())
    paused = next(c for c in unsure.cells if c.cell is WorthCell.PAUSED)
    code_paused = next(c for c in report.cells if c.cell is WorthCell.PAUSED)
    assert paused.numerator == code_paused.numerator > 0
    assert paused.paths[policy.RoutePath.FALLBACK_UNSURE] == paused.n


# ---- [S708] ----
def _production_cell(cell, n, correct, false_proposals):
    return scoring.CellReport(
        cell=cell, n=n, metric=(scoring.RECALL if cell is WorthCell.DELIVERY_WITH_VALUE
                                else scoring.FALSE_PROPOSAL_RATE),
        numerator=(correct if cell is WorthCell.DELIVERY_WITH_VALUE else false_proposals),
        denominator=n, class_correct_count=correct, errors=(), paths={},
        lower_bound=None)


def _measured_row(latency=10.0):
    measured = adoption.Measure.of
    return adoption.ComparisonRow(
        approach="候選", quality=measured(1.0), cost_per_call_usd=measured(0.0),
        latency_median_us=measured(latency), latency_p95_us=measured(latency),
        format_failure_rate=measured(0.0), exception_rate=measured(0.0),
        timeout_rate=measured(0.0), fallback_rate=measured(0.0))


LIMITS = adoption.OperationalLimits(cost_per_call_usd=0.01, latency_p95_us=1000.0,
                                    failure_rate=0.01)


def test_a_slice_is_validated_only_when_every_bar_is_met():
    good = scoring.Report(kind=scoring.PRODUCTION, eval_set_sha256="x", cells=(
        _production_cell(WorthCell.PAUSED, 73, 73, 0),
        _production_cell(WorthCell.DELIVERY_WITH_VALUE, 16, 16, 0),
        _production_cell(WorthCell.NO_DELIVERY, 72, 72, 0),  # 少一筆:樣本數不夠
        _production_cell(WorthCell.DELIVERY_WITHOUT_VALUE, 73, 72, 1),  # 一筆誤提案:下界不過
    ), confusion={}, perturbation_changed=0)
    result = adoption.decide_adoption(good, _measured_row(), LIMITS)
    assert result.validated.cells == frozenset({WorthCell.PAUSED, WorthCell.DELIVERY_WITH_VALUE})
    assert adoption.wilson_lower(73, 73) >= 0.95 > adoption.wilson_lower(72, 72)
    assert adoption.wilson_lower(16, 16) >= 0.80 > adoption.wilson_lower(15, 15)
    # 門檻是常數:延遲超過、門檻還沒定、有任何一欄沒量,一格都不驗證
    for row, limits in ((_measured_row(latency=5000.0), LIMITS),
                        (_measured_row(), adoption.OperationalLimits(None, None, None)),
                        (adoption.ComparisonRow(**{**vars(_measured_row()),
                                                   "timeout_rate": adoption.Measure.not_measured(
                                                       "沒量")}), LIMITS)):
        assert adoption.decide_adoption(good, row, limits).validated.cells == frozenset()
    # 比的是信賴下界不是點估計:召回 15/16(點估計 0.94)下界不到 0.80;
    # 類別正確率另外要過:證據不足格全答「不值得加」,沒有誤提案但類別全錯
    mixed = scoring.Report(kind=scoring.PRODUCTION, eval_set_sha256="x", cells=(
        _production_cell(WorthCell.DELIVERY_WITH_VALUE, 16, 15, 0),
        _production_cell(WorthCell.DELIVERY_WITHOUT_VALUE, 73, 0, 0),
    ), confusion={}, perturbation_changed=0)
    assert adoption.decide_adoption(mixed, _measured_row(), LIMITS).validated.cells == frozenset()
    assert (adoption.WORTH_RECALL_BAR, adoption.OTHER_BAR) == (0.80, 0.95)
    assert (adoption.WORTH_MIN_SAMPLES, adoption.OTHER_MIN_SAMPLES) == (16, 73)


# ---- [S709] ----
def test_unmeasured_candidates_show_no_numbers():
    rows = record.comparison_rows(code_rule_latency=(18.0, 19.0))
    unmeasured = [m for row in rows for m in vars(row).values()
                  if isinstance(m, adoption.Measure) and not m.measured]
    assert unmeasured
    assert all(m.value is None and m.reason for m in unmeasured)
    assert {row.approach for row in rows} == {"現行程式規則", "LLM", "Jev"}
    with pytest.raises(ValueError):
        adoption.Measure(measured=False, value=1.0, reason="未導入")
    with pytest.raises(ValueError):
        adoption.Measure(measured=False, value=None, reason="")


# ---- [S710] ----
def test_the_adoption_decision_is_no_without_measured_validated_slices():
    report = _code_rule_report()
    result = adoption.decide_adoption(report, None, LIMITS)
    assert not result.adopt
    assert result.validated.cells == frozenset()
    assert all(not c.validated for c in result.cells)
    text = record.render(report, record.comparison_rows(code_rule_latency=(18.0, 19.0)), result)
    assert "不採用" in text and report.eval_set_sha256 in text
    for reason in record.NOT_ADOPTED_REASONS + record.MISSING_EVIDENCE:
        assert reason in text
    # 使用者裁定的原意(評分表設計審第 1 輪:舊句會被讀成「不准記成缺陷」)
    assert "記成現行規則的程式缺陷" in text and "不準記成" not in text


# ---- [S712] ----
OTHER_LAYERS = ("analyzer", "executor", "dsp", "domain", "ops")


def test_nothing_outside_the_eval_package_imports_it():
    for layer in OTHER_LAYERS:  # 各目錄的匯入禁令真的擋得住
        config = SRC / layer / "ruff.toml"
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(config.parent / "probe.py"), "-"],
            input="import rtb.eval.eval_set\n", capture_output=True, text=True, timeout=60,
            check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (layer, result.stdout)
    importers = []
    for path in SRC.rglob("*.py"):
        if EVAL in path.parents:
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            importers += [f"{path.name}: {n}" for n in names
                          if n == "rtb.eval" or n.startswith("rtb.eval.")]
    assert importers == []


# ---- [S713] ----
def test_irrelevant_fields_do_not_change_the_answer():
    assert _code_rule_report().perturbation_changed == 0
    groups = {s.group for s in _scenarios()}
    assert all({s.variant for s in _scenarios() if s.group == g} == set(generator.VARIANTS)
               for g in groups)
    # 殺傷力:看預算下判斷的候選,擾動後答案會變,報告要數得出來
    by_budget = _Answer(lambda i: WorthVerdict.WORTH if (i.budget or 0) > 500
                        else WorthVerdict.NOT_WORTH)
    report = scoring.build_report(
        scoring.SYNTHETIC,
        scoring.score(_scenarios(), _call(by_budget), policy.TrialCells(frozenset(WorthCell))),
        scoring.eval_set_sha256())
    assert report.perturbation_changed > 0


# ---- [S714] ----
def test_the_code_rule_is_scored_per_slice_like_a_candidate():
    report = _code_rule_report()
    cells = {c.cell: c for c in report.cells}
    # 現行規則不看狀態:暫停中有投放的被提案;不看轉換營收與資料自洽:沒價值與資料異常格判錯
    assert cells[WorthCell.PAUSED].numerator > 0
    assert (WorthVerdict.NOT_WORTH, WorthVerdict.WORTH) in {(g, f) for g, f, _ in
                                                           cells[WorthCell.PAUSED].errors}
    without = cells[WorthCell.DELIVERY_WITHOUT_VALUE]
    assert without.numerator == without.n and without.class_correct_count == 0
    anomaly = cells[WorthCell.ANOMALY]
    assert anomaly.class_correct_count == 0 and 0 < anomaly.numerator < anomaly.n
    assert cells[WorthCell.NO_DELIVERY].class_correct_count == cells[WorthCell.NO_DELIVERY].n
    assert cells[WorthCell.DELIVERY_WITH_VALUE].numerator == cells[
        WorthCell.DELIVERY_WITH_VALUE].n
    assert all(set(c.paths) == {policy.RoutePath.CODE_RULE} for c in report.cells)


# ---- [S716] ----
def test_evaluation_calls_the_candidate_without_validating_anything():
    candidate = _Answer(WorthVerdict.NOT_WORTH)
    trial = policy.TrialCells(frozenset({WorthCell.PAUSED}))
    scored = scoring.score(_scenarios(), _call(candidate), trial)
    paused = [s for s in scored if s.scenario.cell is WorthCell.PAUSED]
    assert candidate.calls == len(paused) > 0
    assert all(s.path is policy.RoutePath.CANDIDATE for s in paused)
    assert policy.ValidatedCells.NONE.cells == frozenset()
    report = scoring.build_report(scoring.SYNTHETIC, scored, scoring.eval_set_sha256())
    assert adoption.decide_adoption(report, _measured_row(), LIMITS).validated.cells == frozenset()
    # 已驗證清單只有採用函式建得出來:原始碼裡只有這兩處建構它
    builders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name | ast.Attribute)
                    and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
                    == "ValidatedCells"):
                builders.append(path.relative_to(SRC).as_posix())
    assert sorted(builders) == ["analyzer/policy.py", "eval/adoption.py"]
