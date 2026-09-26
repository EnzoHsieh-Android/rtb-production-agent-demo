"""Phase 10 增量 2:評估套件。

合約 [S706] 到 [S714]、[S716]、[S717],以及 [S711] 評分表那半(增量 1 驗了每筆可建構輸入恰好落一格)。
採用閘一律 fail-closed:合成集與不完整證據一定不採用。
"""

import ast
import dataclasses
import hashlib
import io
import itertools
import math
import subprocess
import sys
from pathlib import Path

import pytest

from rtb.analyzer import policy
from rtb.domain import _checks
from rtb.domain.worth import CampaignStatus, WorthCell, WorthInput, WorthVerdict, cell_of
from rtb.eval import adoption, eval_set, generator, record, rubric, scoring

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"
EVAL = SRC / "eval"
EVAL_SET = EVAL / "eval_set.py"
# 合成評估集的雜湊:改了評估集就要同時改這裡,並用決策指令記一筆換批理由與時間(計劃〈外洩紀錄〉)
EVAL_SET_SHA256 = "eec515196db7a33b4f7b77f63a32ee55167f505bde210bc8d946b1b9cc4e6119"


def _scenarios():
    return generator.from_rows(eval_set.ROWS)


def _code_rule_report():
    return scoring.synthetic_report(scoring.score(_scenarios(), None, None),
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
            # 只動一欄:沒被動到的欄位之間的漏斗關係照舊成立
            if name not in ("impressions", "clicks"):
                assert i.clicks <= i.impressions, s.scenario_id
            if name not in ("clicks", "conversions"):
                assert i.conversions <= i.clicks, s.scenario_id
        else:
            assert _nonnegative(*values.values()), s.scenario_id
            assert (i.clicks > i.impressions) == (s.fault == "clicks>impressions")
            assert (i.conversions > i.clicks) == (s.fault == "conversions>clicks")
    # 代碼審第 1 輪:點擊多於曝光的故障不另把轉換歸零
    assert any(s.worth_input.conversions > 0 for s in anomalies
               if s.fault == "clicks>impressions")
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
    assert isinstance(report, scoring.SyntheticReport)
    assert [c.cell for c in report.cells] == list(WorthCell)
    for cell in report.cells:
        names = [m.name for m in cell.metrics]
        if cell.cell is WorthCell.DELIVERY_WITH_VALUE:
            assert names == [scoring.RECALL]
        else:  # 兩項都比(計劃要求,冗餘也照做)
            assert names == [scoring.NO_FALSE_PROPOSAL, scoring.CLASS_ACCURACY]
            assert cell.metric(scoring.NO_FALSE_PROPOSAL).numerator == cell.n - cell.false_proposals
        for metric in cell.metrics:
            assert metric.lower_bound is None  # 合成集不套統計信賴
            assert metric.denominator == cell.n > 0
            assert metric.point == metric.numerator / metric.denominator
        assert sum(count for _, _, count in cell.errors) == cell.n - cell.class_correct_count
    # 依最後有效答案計分:候選回「不知道」退回正式規則(Phase 14 起是九條,Phase 10 情境明傳缺四查詢),
    # 最後答案跟正式規則逐格相同;暫停中有投放的不再被誤提案(第 1 條)
    unsure = scoring.synthetic_report(
        scoring.score(_scenarios(), _call(_Answer(WorthVerdict.UNSURE)),
                      policy.TrialCells(frozenset(WorthCell))), scoring.eval_set_sha256())
    for fallback, code in zip(unsure.cells, report.cells, strict=True):
        assert (fallback.false_proposals, fallback.class_correct_count, fallback.errors) == (
            code.false_proposals, code.class_correct_count, code.errors)
        assert fallback.paths[policy.RoutePath.FALLBACK_UNSURE] == fallback.n
    paused = next(c for c in unsure.cells if c.cell is WorthCell.PAUSED)
    assert paused.false_proposals == 0


# ---- [S708] ----
PROVENANCE = scoring.HiddenSetProvenance(
    version="v1", sha256="a" * 64, sampling_source="正式決策紀錄依格抽樣",
    labeling_source="兩位標註員各自標、不一致再裁", candidate_version="cand-1",
    candidate_predates_disclosure=True)


def _template(cell):
    return next(s for s in _scenarios() if s.cell is cell and s.variant == "base")


def _scored(cell, n, correct, false_proposals=0):
    """造一格正式樣本的計分結果:correct 筆答對,false_proposals 筆誤提案,其餘答成另一個錯的類別。"""
    template = _template(cell)
    wrong = next(v for v in (WorthVerdict.NOT_WORTH, WorthVerdict.INSUFFICIENT)
                 if v is not template.gold)
    finals = ([template.gold] * correct + [WorthVerdict.WORTH] * false_proposals
              + [wrong] * (n - correct - false_proposals))
    return [scoring.ScoredCase(template, final, policy.RoutePath.CANDIDATE) for final in finals]


def _production(*groups):
    return scoring.production_report(tuple(c for g in groups for c in g), PROVENANCE)


def _row(cell, latency=10.0, **overrides):
    measured = adoption.Measure.of
    values = {"quality": measured(1.0), "cost_per_call_usd": measured(0.0),
              "latency_median_us": measured(latency), "latency_p95_us": measured(latency),
              "format_failure_rate": measured(0.0), "exception_rate": measured(0.0),
              "timeout_rate": measured(0.0), "fallback_rate": measured(0.0), **overrides}
    return adoption.ComparisonRow(approach="候選", cell=cell, **values)


def _rows(**overrides):
    return {cell: _row(cell, **overrides) for cell in WorthCell}


LIMITS = adoption.OperationalLimits(cost_per_call_usd=0.01, latency_median_us=1000.0,
                                    latency_p95_us=1000.0, failure_rate=0.01)
ALL_GOOD = (_scored(WorthCell.PAUSED, 73, 73), _scored(WorthCell.ANOMALY, 73, 73),
            _scored(WorthCell.NO_DELIVERY, 73, 73), _scored(WorthCell.DELIVERY_WITH_VALUE, 16, 16),
            _scored(WorthCell.DELIVERY_WITHOUT_VALUE, 73, 73))


def test_a_slice_is_validated_only_when_every_bar_is_met():
    report = _production(
        _scored(WorthCell.PAUSED, 73, 73),
        _scored(WorthCell.DELIVERY_WITH_VALUE, 16, 16),
        _scored(WorthCell.NO_DELIVERY, 72, 72),  # 少一筆:下界不過
        _scored(WorthCell.DELIVERY_WITHOUT_VALUE, 73, 72, 1),  # 一筆誤提案:下界不過
        _scored(WorthCell.ANOMALY, 73, 0))  # 沒有誤提案但類別全錯
    result = adoption.decide_adoption(report, _rows(), LIMITS)
    assert result.validated.cells == frozenset({WorthCell.PAUSED, WorthCell.DELIVERY_WITH_VALUE})
    assert result.adopt and result.reasons == ()  # 採用時不印不採用理由
    for cell in report.cells:  # 正式報告每項指標都存下界,採用函式只讀它
        for metric in cell.metrics:
            assert metric.lower_bound == scoring.wilson_lower(metric.numerator, metric.denominator)
    # 召回比的是下界不是點估計:15/16(點估計 0.94)下界不到 0.80
    low_recall = _production(_scored(WorthCell.DELIVERY_WITH_VALUE, 16, 15))
    assert adoption.decide_adoption(low_recall, _rows(), LIMITS).validated.cells == frozenset()
    good = _production(*ALL_GOOD)
    assert adoption.decide_adoption(good, _rows(), LIMITS).validated.cells == frozenset(WorthCell)
    # 門檻是常數,任何一格一欄不合就那一格不驗證;缺列、門檻未定、揭露過,整體都不驗證
    blocked = [
        (_rows(latency=5000.0), LIMITS),
        (_rows(timeout_rate=adoption.Measure.not_measured("沒量")), LIMITS),
        ({cell: row for cell, row in _rows().items() if cell is not WorthCell.PAUSED}, LIMITS),
        (_rows(), adoption.OperationalLimits(None, None, None, None)),
        (_rows(), adoption.OperationalLimits(0.01, None, 1000.0, 0.01)),
        (_rows(latency_median_us=adoption.Measure.of(2000.0)), LIMITS),
        (_rows(latency_p95_us=adoption.Measure.of(2000.0)), LIMITS),
        (_rows(cost_per_call_usd=adoption.Measure.of(0.5)), LIMITS),
        (_rows(exception_rate=adoption.Measure.of(0.5)), LIMITS),
    ]
    for rows, limits in blocked:
        cells = adoption.decide_adoption(good, rows, limits).validated.cells
        assert cells != frozenset(WorthCell), (rows, limits)
    disclosed = scoring.production_report(
        tuple(c for g in ALL_GOOD for c in g),
        dataclasses.replace(PROVENANCE, candidate_predates_disclosure=False))
    assert adoption.decide_adoption(disclosed, _rows(), LIMITS).validated.cells == frozenset()
    assert (adoption.WORTH_RECALL_BAR, adoption.OTHER_BAR) == (0.80, 0.95)
    assert (adoption.WORTH_MIN_SAMPLES, adoption.OTHER_MIN_SAMPLES) == (16, 73)


def test_a_synthetic_report_can_never_be_adopted():
    """代碼審第 1 輪:正式與合成是不同型別;合成集結果換個字串、硬塞下界都進不了採用。"""
    synthetic = scoring.synthetic_report(tuple(c for g in ALL_GOOD for c in g), "x")
    assert adoption.decide_adoption(synthetic, _rows(), LIMITS).validated.cells == frozenset()
    faked = dataclasses.replace(synthetic, eval_set_sha256="production")
    assert adoption.decide_adoption(faked, _rows(), LIMITS).validated.cells == frozenset()
    for issuer in (None, object()):
        with pytest.raises(ValueError):
            scoring.ProductionReport(PROVENANCE, synthetic.cells, {}, 0, issuer)
    with pytest.raises(ValueError):
        dataclasses.replace(PROVENANCE, labeling_source=" ")


# ---- 代碼審第 2 輪 ----
@pytest.mark.parametrize("flag", ["false", "true", 0, 1, None])
def test_the_disclosure_flag_must_be_a_real_bool(flag):
    with pytest.raises(ValueError):
        dataclasses.replace(PROVENANCE, candidate_predates_disclosure=flag)


@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64, "A" * 64 + "0", "x"])
def test_the_hidden_set_hash_must_be_64_hex_digits(digest):
    with pytest.raises(ValueError):
        dataclasses.replace(PROVENANCE, sha256=digest)


def test_a_production_report_needs_the_candidate_in_every_cell():
    """正式報告每一筆都要交給候選(候選答的或候選退回的都算候選的結果),有任何一筆走現行規則就拒。"""
    code_only = [scoring.ScoredCase(c.scenario, c.final, policy.RoutePath.CODE_RULE)
                 for g in ALL_GOOD for c in g]
    with pytest.raises(ValueError):
        scoring.production_report(tuple(code_only), PROVENANCE)
    one_cell_code = [c if c.scenario.cell is not WorthCell.PAUSED
                     else scoring.ScoredCase(c.scenario, c.final, policy.RoutePath.CODE_RULE)
                     for g in ALL_GOOD for c in g]
    with pytest.raises(ValueError):
        scoring.production_report(tuple(one_cell_code), PROVENANCE)
    # 代碼審第 3 輪:1 筆候選加 72 筆現行規則也不行——正式報告任何一筆都不得走現行規則
    mostly_code = [c if i == 0 else scoring.ScoredCase(c.scenario, c.final,
                                                       policy.RoutePath.CODE_RULE)
                   for g in ALL_GOOD for i, c in enumerate(g)]
    with pytest.raises(ValueError):
        scoring.production_report(tuple(mostly_code), PROVENANCE)
    fallback = [scoring.ScoredCase(c.scenario, c.final, policy.RoutePath.FALLBACK_UNSURE)
                for g in ALL_GOOD for c in g]
    scoring.production_report(tuple(fallback), PROVENANCE)  # 候選退回算候選的結果


def test_a_comparison_row_counts_only_for_its_own_cell():
    """同一列掛在五格的鍵下,不能冒充五格都量過。"""
    good = _production(*ALL_GOOD)
    paused_row = _row(WorthCell.PAUSED)
    rows = dict.fromkeys(WorthCell, paused_row)
    result = adoption.decide_adoption(good, rows, LIMITS)
    assert result.validated.cells == frozenset({WorthCell.PAUSED})


def test_huge_integers_are_rejected_without_crashing():
    with pytest.raises(ValueError):
        adoption.Measure.of(10**400)
    with pytest.raises(ValueError):
        adoption.OperationalLimits(10**400, 1.0, 1.0, 0.1)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -0.01, True])
def test_measures_and_limits_reject_non_finite_and_negative_values(bad):
    with pytest.raises(ValueError):
        adoption.Measure.of(bad)
    with pytest.raises(ValueError):
        adoption.OperationalLimits(bad, 1.0, 1.0, 0.1)
    with pytest.raises(ValueError):
        adoption.OperationalLimits(1.0, 1.0, 1.0, bad)


def test_rates_must_stay_between_zero_and_one():
    with pytest.raises(ValueError):
        _row(WorthCell.PAUSED, fallback_rate=adoption.Measure.of(1.5))
    with pytest.raises(ValueError):
        _row(WorthCell.PAUSED, quality=adoption.Measure.of(1.01))
    with pytest.raises(ValueError):
        adoption.OperationalLimits(1.0, 1.0, 1.0, 1.5)


# ---- [S709] ----
def test_unmeasured_candidates_show_no_numbers():
    latency = dict.fromkeys(WorthCell, (18.0, 19.0))
    rows = record.comparison_rows(latency, _code_rule_report())
    assert {(row.approach, row.cell) for row in rows} == {
        (a, c) for a in ("現行程式規則", "LLM", "Jev") for c in WorthCell}
    unmeasured = [m for row in rows for m in row.measures() if not m.measured]
    assert len(unmeasured) == 2 * len(WorthCell) * 8
    assert all(m.value is None and m.reason for m in unmeasured)
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
    assert [c.cell for c in result.cells] == list(WorthCell)
    assert all(not c.validated for c in result.cells)
    rows = record.comparison_rows(dict.fromkeys(WorthCell, (18.0, 19.0)), report)
    text = record.render(report, rows, result)
    assert "不採用" in text and report.eval_set_sha256 in text
    for reason in result.reasons + result.missing_evidence:
        assert reason in text
    for reason in adoption.NOT_ADOPTED_REASONS:
        assert reason in result.reasons
    # 使用者裁定的原意(評分表設計審第 1 輪:舊句會被讀成「不准記成缺陷」)
    assert "記成現行規則的程式缺陷" in text and "不準記成" not in text
    # 結論、理由、缺證據都從同一個採用結果衍生:採用時不印不採用理由
    good = _production(*ALL_GOOD)
    adopted = adoption.decide_adoption(good, _rows(), LIMITS)
    adopted_text = record.render(good, rows, adopted)
    assert adopted.adopt and "不採用的理由" not in adopted_text and "結論:採用" in adopted_text
    assert all(reason not in adopted_text for reason in adoption.NOT_ADOPTED_REASONS)
    for cell in good.cells:  # 正式報告逐項輸出下界
        for metric in cell.metrics:
            assert f"下界 {metric.lower_bound:.3f}" in adopted_text


def test_the_record_command_line_has_the_project_shape():
    out = io.StringIO()
    assert record.run([], out=out) == record.EXIT_OK
    assert "結論:不採用 Jev" in out.getvalue()
    with pytest.raises(SystemExit) as exited:
        record.main(["--help"])
    assert exited.value.code == 0


# ---- [S712] ----
OTHER_LAYERS = ("analyzer", "executor", "dsp", "domain", "ops")
PROBES = {
    "direct": "import rtb.eval.eval_set\n",
    "dunder_variable": "name = 'rtb.' + 'eval'\nx = __import__(name)\n",
    "dunder_alias": "loader = __import__\nx = loader('rtb.eval')\n",
    "builtins_alias": "from builtins import __import__ as load\nx = load('rtb.eval')\n",
    "import_module_variable": ("import importlib\nname = 'rtb.eval'\n"
                               "x = importlib.import_module(name)\n"),
    "relative": "from ..eval import eval_set\n",
    "dunder": "x = __import__('rtb.eval.eval_set')\n",
    "import_module": "import importlib\nx = importlib.import_module('rtb.eval.eval_set')\n",
    "aliased": "from importlib import import_module as im\nx = im('rtb.eval')\n",
}


def _imported_names(node, package):
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:
            parts = package.split(".")[: len(package.split(".")) - node.level + 1]
            base = ".".join([*parts, *([node.module] if node.module else [])])
        return [base, *(f"{base}.{a.name}" for a in node.names)]
    return []


def _dynamic_uses(node, dynamic):
    """任何 __import__ 或 import_module(含別名)的呼叫都算(代碼審第 2 輪);對 __import__ 的任何引用、
    從 builtins 匯入也算(代碼審第 3 輪)。"""
    found = []
    if isinstance(node, ast.Call):
        func = node.func
        called = (func.id if isinstance(func, ast.Name)
                  else func.attr if isinstance(func, ast.Attribute) else None)
        if called in dynamic:
            found.append(f"動態匯入 {called}")
    if (isinstance(node, ast.Name) and node.id == "__import__") or (
            isinstance(node, ast.Attribute) and node.attr == "__import__"):
        found.append("引用 __import__")
    if isinstance(node, ast.ImportFrom) and node.module == "builtins":
        found.append("從 builtins 匯入")
    return found


def _eval_imports(path, tree):
    """一支檔對評估套件的匯入(直接、相對依檔案所在套件換算),加上任何動態匯入的寫法——受保護五層
    不准動態匯入。"""
    package = ".".join(path.relative_to(SRC.parent).with_suffix("").parts[:-1])
    dynamic = {"__import__", "import_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic |= {a.asname or a.name for a in node.names if a.name == "import_module"}
    found = []
    for node in ast.walk(tree):
        found += [n for n in _imported_names(node, package)
                  if n == "rtb.eval" or n.startswith("rtb.eval.")]
        found += _dynamic_uses(node, dynamic)
    return found


def test_nothing_outside_the_eval_package_imports_it():
    for layer in OTHER_LAYERS:  # 各目錄的匯入禁令真的擋得住直接匯入
        config = SRC / layer / "ruff.toml"
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(config.parent / "probe.py"), "-"],
            input=PROBES["direct"], capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (layer, result.stdout)
        # 代碼審第 2 輪:受保護五層直接禁止動態匯入模組
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(config.parent / "probe.py"), "-"],
            input="import importlib\n", capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (layer, result.stdout)
        for kind, source in PROBES.items():  # 掃描認得五種寫法(代碼審第 1 輪補動態匯入)
            probe = SRC / layer / "probe.py"
            assert _eval_imports(probe, ast.parse(source)), (layer, kind)
    # 代碼審第 3 輪:領域層舊的 importlib.import_module 條目刪掉(新的 importlib 已涵蓋)
    assert "importlib.import_module" not in (SRC / "domain" / "ruff.toml").read_text("utf-8")
    # 雜湊格式只有一份判準(證據與正式報告共用)
    assert not hasattr(scoring, "_SHA256")
    assert scoring.is_sha256 is _checks.is_sha256
    importers = [f"{path.name}: {name}" for path in SRC.rglob("*.py") if EVAL not in path.parents
                 for name in _eval_imports(path, ast.parse(path.read_text("utf-8")))]
    assert importers == []


# ---- [S713] ----
def test_irrelevant_fields_do_not_change_the_answer():
    assert _code_rule_report().perturbation_changed == 0
    by_group = {}
    for s in _scenarios():
        by_group.setdefault(s.group, {})[s.variant] = s.worth_input
    for group, variants in by_group.items():  # 三個變體齊全,擾動只改指定的那一欄
        assert set(variants) == set(generator.VARIANTS), group
        base = variants["base"]
        for variant, only in (("spend", "spend"), ("budget", "budget")):
            other = variants[variant]
            changed = {f.name for f in dataclasses.fields(base)
                       if getattr(base, f.name) != getattr(other, f.name)}
            assert changed <= {only}, (group, variant, changed)
    # 殺傷力:看預算下判斷的候選,擾動後答案會變,報告要數得出來
    by_budget = _Answer(lambda i: WorthVerdict.WORTH if (i.budget or 0) > 500
                        else WorthVerdict.NOT_WORTH)
    report = scoring.synthetic_report(
        scoring.score(_scenarios(), _call(by_budget), policy.TrialCells(frozenset(WorthCell))),
        scoring.eval_set_sha256())
    assert report.perturbation_changed > 0


# ---- [S714](Phase 14 [S1417] 改寫:舊「只看投放」的逐格錯法撤掉,按明示缺四查詢的九條重算)----
def test_phase_ten_scenarios_explicitly_report_missing_queries():
    """Phase 10 舊 Scenario 經 score → route → code_rule:轉接器明傳 MISSING_FOUR_QUERIES,
    暫停/異常先判,
    其餘回證據不足;五格逐格報法保留(分子、分母、錯誤子型),每格標「舊資料不足以評估九條規則」,
    標準答案不動。例:active、有投放但無四查詢 → 證據不足。"""
    report = _code_rule_report()
    cells = {c.cell: c for c in report.cells}
    assert all(c.note == scoring.MISSING_QUERIES_NOTE == "舊資料不足以評估九條規則"
               for c in report.cells)
    assert all(set(c.paths) == {policy.RoutePath.CODE_RULE} for c in report.cells)
    # 標準答案不動(Phase 10 使用者裁定的評分表)
    gold = {cell: {s.gold for s in _scenarios() if s.cell is cell} for cell in WorthCell}
    assert gold == {WorthCell.PAUSED: {WorthVerdict.NOT_WORTH},
                    WorthCell.ANOMALY: {WorthVerdict.INSUFFICIENT},
                    WorthCell.NO_DELIVERY: {WorthVerdict.NOT_WORTH},
                    WorthCell.DELIVERY_WITH_VALUE: {WorthVerdict.WORTH},
                    WorthCell.DELIVERY_WITHOUT_VALUE: {WorthVerdict.INSUFFICIENT}}
    # 第 1/2 條只用 1 小時資料判得出:暫停、異常全對
    for cell in (WorthCell.PAUSED, WorthCell.ANOMALY):
        assert cells[cell].class_correct_count == cells[cell].n and cells[cell].errors == ()
    # 其餘一律證據不足:沒有任何一格誤提案;要四查詢的格按錯誤子型逐格報
    assert sum(c.false_proposals for c in report.cells) == 0
    no_delivery = cells[WorthCell.NO_DELIVERY]
    assert no_delivery.errors == ((WorthVerdict.NOT_WORTH, WorthVerdict.INSUFFICIENT,
                                   no_delivery.n),)
    with_value = cells[WorthCell.DELIVERY_WITH_VALUE]
    assert with_value.metric(scoring.RECALL).numerator == 0
    assert with_value.errors == ((WorthVerdict.WORTH, WorthVerdict.INSUFFICIENT, with_value.n),)
    without = cells[WorthCell.DELIVERY_WITHOUT_VALUE]
    assert without.class_correct_count == without.n
    # 單筆:active、有投放、無四查詢 → 九條的證據不足;沒有「只看投放」的暗門
    active = next(s for s in _scenarios() if s.cell is WorthCell.DELIVERY_WITH_VALUE)
    assert policy.code_rule(active.worth_input, policy.MISSING_FOUR_QUERIES,
                            scoring.RULE_NOW) is WorthVerdict.INSUFFICIENT
    with pytest.raises(TypeError):
        policy.code_rule(active.worth_input)  # type: ignore[call-arg]
    # 正式環境抽樣報告的情境也經 score 明傳缺四查詢,候選退回格一樣標註(代碼審 r1 鏡頭4-1)
    production = scoring.production_report(
        scoring.score(_scenarios(), _call(_Answer(WorthVerdict.UNSURE)),
                      policy.TrialCells(frozenset(WorthCell))), PROVENANCE)
    assert all(c.note == scoring.MISSING_QUERIES_NOTE for c in production.cells)
    # 報告程式改簽章後不斷線:逐格表每格帶標註
    table = "\n".join(record.cell_table(report.cells))
    assert table.count("舊資料不足以評估九條規則") == len(WorthCell)


# ---- [S716] ----
def test_evaluation_calls_the_candidate_without_validating_anything():
    candidate = _Answer(WorthVerdict.NOT_WORTH)
    trial = policy.TrialCells(frozenset({WorthCell.PAUSED}))
    scored = scoring.score(_scenarios(), _call(candidate), trial)
    paused = [s for s in scored if s.scenario.cell is WorthCell.PAUSED]
    assert candidate.calls == len(paused) > 0
    assert all(s.path is policy.RoutePath.CANDIDATE for s in paused)
    assert policy.ValidatedCells.NONE.cells == frozenset()
    report = scoring.synthetic_report(scored, scoring.eval_set_sha256())
    assert adoption.decide_adoption(report, _rows(), LIMITS).validated.cells == frozenset()
    # 已驗證清單只有採用函式建得出來:原始碼裡只有這兩處建構它
    builders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name | ast.Attribute)
                    and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
                    == "ValidatedCells"):
                builders.append(path.relative_to(SRC).as_posix())
    assert sorted(builders) == ["analyzer/policy.py", "eval/adoption.py"]


# ---- Phase 14 增量 4 代碼審 r1(外家 finder-2、單 reviewer-4、架構對齊-2)----
def test_the_phase_ten_record_says_what_did_not_run_and_marks_old_data(tmp_path):
    """模型候選一次都沒被呼叫時,不寫「跑了 N 個情境」而照實寫沒有跑;比較表「現行程式規則」五列每列標
    「舊資料不足以評估九條規則」,表下註明品質 1 是缺四查詢短路徑剛好等於標準答案。數字格一律走共用的
    格式化(不出科學記號)。"""
    out, err = io.StringIO(), io.StringIO()
    code = record.run(["--recordings-dir", str(tmp_path / "empty"),
                       "--ledger", str(tmp_path / "l.sqlite")],
                      out=out, err=err, environ={"PATH": "", "HOME": str(tmp_path)})
    assert code == record.EXIT_OK, err.getvalue()
    text = out.getvalue()
    assert "跑了" not in text and "沒有跑" in text
    rows = [line for line in text.splitlines() if line.startswith("| 現行程式規則")]
    assert len(rows) == len(WorthCell)
    assert all(scoring.MISSING_QUERIES_NOTE in row for row in rows), rows
    # 代碼審 r2:暫停、異常兩格九條用基本資料就判得出(品質 1 有效),只有沒價值格是恰好等於標準答案
    assert "paused、anomaly 兩格九條用基本資料就判得出" in text
    assert "delivery_without_value 的品質 1 是缺四查詢時一律回證據不足" in text
    assert "暫停、異常判得出,沒價值格" not in text
    assert record._cell is not None and adoption.format_value(12_345_600.0) == "12345600"
    assert adoption.format_value(0.8341) == "0.8341" and "e+" not in adoption.format_value(4.2e6)
