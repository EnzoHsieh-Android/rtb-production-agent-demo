"""指標計算的測試:正常值、零分母、缺資料、不合理資料、邊界值,以及「空值不可被當成 0」。"""

import math
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from rtb.domain import metrics as m
from rtb.domain.metrics import MetricResult, Reason, ctr, cvr, pacing, roas
from tests.eval.test_investigation_eval import answer_key_goes_through_exact_ratio


def test_ratios_return_a_known_value_for_normal_inputs():
    assert ctr(clicks=50, impressions=1000).value == pytest.approx(0.05)
    assert cvr(conversions=5, clicks=50).value == pytest.approx(0.1)
    assert roas(revenue=300.0, spend=100.0).value == pytest.approx(3.0)


def test_a_true_zero_is_a_known_value_not_a_missing_one():
    result = ctr(clicks=0, impressions=1000)

    assert result.is_known and result.value == 0.0 and result.reason is None


def test_zero_denominator_is_reported_with_its_reason_and_no_value():
    for result in (ctr(0, 0), cvr(0, 0), cvr(3, 0), roas(0.0, 0.0), roas(50.0, 0.0)):
        assert result.value is None and result.reason == Reason.NO_DENOMINATOR


def test_clicks_without_any_impressions_is_impossible_data_not_a_zero_denominator():
    assert ctr(5, 0).reason == Reason.INVALID_DATA


@pytest.mark.parametrize("call", [
    lambda: ctr(None, 100), lambda: ctr(5, None), lambda: ctr(None, None),
    lambda: cvr(None, 10), lambda: roas(10.0, None), lambda: pacing(None, 100.0, 0.5),
])
def test_missing_inputs_are_reported_as_missing_data_not_zero(call):
    result = call()

    assert result.value is None and result.reason == Reason.MISSING_DATA


def test_missing_takes_priority_over_a_zero_denominator():
    assert ctr(None, 0).reason == Reason.MISSING_DATA  # 分母為零,但分子是不知道:不能說成正常的零


@pytest.mark.parametrize("call", [
    lambda: ctr(-1, 100), lambda: ctr(5, -100), lambda: ctr(150, 100),
    lambda: roas(float("nan"), 10.0), lambda: roas(10.0, float("inf")),
    lambda: ctr(True, 100), lambda: ctr("5", 100), lambda: pacing(10.0, -5.0, 0.5),
    lambda: pacing(10.0, 100.0, 1.5), lambda: pacing(10.0, 100.0, -0.1),
])
def test_impossible_inputs_are_invalid_data_and_never_crash_the_batch(call):
    result = call()

    assert result.value is None and result.reason == Reason.INVALID_DATA


def test_invalid_takes_priority_over_missing():
    assert ctr(-1, None).reason == Reason.INVALID_DATA


def test_boundary_click_through_rate_of_exactly_one_is_allowed():
    assert ctr(100, 100).value == 1.0


def test_very_large_counts_keep_precision_sensible():
    assert ctr(10**12, 10**13).value == pytest.approx(0.1)


def test_pacing_compares_actual_spend_with_the_spend_expected_so_far():
    # 預算 100、一天過了一半,預期已花 50;實際花 25 就是進度 0.5
    assert pacing(spend=25.0, budget=100.0, elapsed_fraction=0.5).value == pytest.approx(0.5)
    assert pacing(spend=50.0, budget=100.0, elapsed_fraction=0.5).value == pytest.approx(1.0)
    assert pacing(spend=75.0, budget=100.0, elapsed_fraction=0.5).value == pytest.approx(1.5)


def test_pacing_with_nothing_elapsed_or_no_budget_has_no_denominator():
    assert pacing(0.0, 100.0, 0.0).reason == Reason.NO_DENOMINATOR
    assert pacing(0.0, 0.0, 0.5).reason == Reason.NO_DENOMINATOR


def test_pacing_over_the_whole_period_is_allowed_up_to_exactly_one():
    assert pacing(100.0, 100.0, 1.0).value == pytest.approx(1.0)


def test_unknown_metric_cannot_be_compared_like_a_number():
    unknown = ctr(0, 0)

    with pytest.raises(TypeError):
        unknown < 0.01  # noqa: B015 - 刻意示範:誤把空值當數字比大小必須直接報錯


def test_below_and_at_least_answer_unknown_instead_of_guessing_for_missing_values():
    unknown, known = ctr(0, 0), ctr(1, 1000)

    assert unknown.below(0.01) is None and unknown.at_least(0.01) is None
    assert known.below(0.01) is True and known.at_least(0.01) is False


def test_a_decision_helper_treats_unknown_as_insufficient_evidence_not_as_bad_performance():
    def should_pause_for_low_ctr(result, threshold=0.01):
        verdict = result.below(threshold)
        return "insufficient_evidence" if verdict is None else ("pause" if verdict else "keep")

    assert should_pause_for_low_ctr(ctr(0, 0)) == "insufficient_evidence"  # 沒曝光,不是表現差
    assert should_pause_for_low_ctr(ctr(None, 100)) == "insufficient_evidence"
    assert should_pause_for_low_ctr(ctr(1, 1000)) == "pause"
    assert should_pause_for_low_ctr(ctr(50, 1000)) == "keep"


def test_thresholds_must_be_finite_numbers():
    with pytest.raises(ValueError):
        ctr(1, 10).below(math.nan)


def test_invalid_beats_missing_even_for_the_cross_field_elapsed_fraction_check():
    assert pacing(None, 100.0, 2).reason == Reason.INVALID_DATA
    assert pacing(1.0, None, 1.5).reason == Reason.INVALID_DATA


def test_huge_integers_never_crash_and_overflowing_results_are_invalid_not_infinite():
    assert ctr(2**1024, 1).reason == Reason.INVALID_DATA  # 點擊遠多於曝光
    assert ctr(10**400, 10**401).value == pytest.approx(0.1)  # 大整數相除仍然精確
    for result in (roas(10**400, 1), roas(1e300, 1e-300), roas(5, 1e-320)):
        assert result.value is None and result.reason == Reason.INVALID_DATA  # 無限大不是「有值」


def test_pacing_products_that_overflow_or_underflow_are_invalid_not_a_crash_or_a_fake_zero():
    assert pacing(1, 10**400, 0.5).reason == Reason.INVALID_DATA  # 預算大到無法表示
    # 預算與時間比例都不是 0,乘積卻下溢成 0.0:不能說成「分母為零」
    assert pacing(1.0, 1e-200, 1e-200).reason == Reason.INVALID_DATA


def test_lumos_lint_command_never_bypasses_the_nested_domain_config():
    import json

    root = Path(__file__).resolve().parents[2]
    commands = json.loads((root / ".lumos/lint.json").read_text(encoding="utf-8"))["py"]

    for command in commands:  # --config 或 --isolated 會讓 domain/ruff.toml 無聲失效
        assert "--config" not in command and "--isolated" not in command
    # 推送閘會點名傳入改到的檔;不加 --force-exclude,ruff 會無視 pyproject 的排除清單,
    # 把 scripts/ 底下的 lumos 工具複本也當成本專案程式去查(2026-09-22 推送被 22 條誤報擋下)
    assert any("ruff check" in c and "--force-exclude" in c for c in commands)


def test_an_unknown_result_cannot_be_used_as_a_truth_value():
    with pytest.raises(TypeError):
        bool(ctr(0, 0))  # 寫成 if ctr(...): 會把「不知道」當成「有值」,必須直接報錯


@pytest.mark.parametrize("make", [
    lambda: MetricResult(value=1.0, reason=Reason.NO_DENOMINATOR),  # 有值卻又有原因
    lambda: MetricResult(value=None),  # 沒有值卻沒有原因
    lambda: MetricResult(value=float("nan")),
    lambda: MetricResult(value=float("inf")),
    lambda: MetricResult(value=None, reason="typo_reason"),
])
def test_self_contradictory_results_cannot_even_be_constructed(make):
    with pytest.raises(ValueError):
        make()


def test_reasons_are_a_closed_set_of_named_values():
    assert {r.value for r in Reason} == {"no_denominator", "missing_data", "invalid_data"}
    assert ctr(0, 0).reason is Reason.NO_DENOMINATOR


def ruff_on(source: str, virtual_path: str):
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--stdin-filename", str(root / virtual_path), "-"],
        input=source, capture_output=True, text=True, cwd=root,
    )


@pytest.mark.parametrize("source", [
    "import sqlite3\n", "from sqlite3 import connect\n", "import sqlite3 as db\n",
    "import socket\n", "import urllib.request\n", "from urllib import request\n",
    "import http.client\n", "import http.server\n", "from subprocess import run\n",
    "import anthropic\n", "import openai\n", "import requests\n", "import httpx\n",
    "import urllib3\n", "import aiohttp\n", "from importlib import import_module\n",
    "def f():\n    import sqlite3\n",
    "import os\nos.system('ls')\n", "from os import system\n", "import multiprocessing\n",
    "import ctypes\n", "import ssl\n", "import smtplib\n", "import socketserver\n",
    "import asyncio\nasyncio.create_subprocess_exec\n",
])
def test_domain_layer_is_forbidden_from_importing_database_network_process_or_model_clients(source):
    result = ruff_on(source, "src/rtb/domain/_probe.py")

    assert result.returncode != 0 and "TID251" in result.stdout, source


@pytest.mark.parametrize(("path", "source"), [
    # Phase 11B 增量 1 起 DSP 也禁 subprocess(只有模型用戶端能啟動子行程,計劃
    # [[Projects/RTB_Phase11B大模型接入_計劃]]〈既有邊界怎麼改〉[S917]),
    # DSP 這一格就不再放 subprocess
    ("src/rtb/dsp/_probe.py", "import sqlite3\nimport socket\n"),
    ("tests/_probe.py", "import sqlite3\nimport socket\nimport subprocess\n"),
])
def test_the_ban_applies_only_to_the_domain_layer_not_to_dsp_or_tests(path, source):
    result = ruff_on(source, path)

    assert "TID251" not in result.stdout, result.stdout


def test_the_real_domain_modules_pass_the_same_check():
    root = Path(__file__).resolve().parents[2]

    result = subprocess.run([sys.executable, "-m", "ruff", "check", "src/rtb/domain"],
                            capture_output=True, text=True, cwd=root)

    assert result.returncode == 0, result.stdout


# 領域層匯入的白名單(2026-09-22 合約審計指出:上面的 ruff 禁令是黑名單,一行 `# noqa: TID251`
# 就能跳過,清單沒列的模組也擋不住;這裡直接解析原始碼,不看 noqa 註解)。
PURE_STDLIB_ALLOWLIST = frozenset({
    "abc", "collections", "dataclasses", "datetime", "decimal", "enum", "fractions", "functools",
    "hashlib", "itertools", "json", "math", "re", "string", "types", "typing", "unicodedata",
})
FORBIDDEN_BUILTIN_CALLS = frozenset({"__import__", "eval", "exec", "compile"})


def _domain_imports_and_calls():
    import ast

    domain = Path(__file__).resolve().parents[2] / "src" / "rtb" / "domain"
    imports, calls = [], []
    for file in sorted(domain.rglob("*.py")):  # 遞迴:子資料夾也算領域層(2026-09-22 第二輪審計指出)
        name = str(file.relative_to(domain))
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports += [(name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports.append((name, "." * node.level + (node.module or "")))
            elif isinstance(node, ast.Name):  # 任何引用都算:先存成別名再呼叫也抓得到
                calls.append((name, node.id))
    return imports, calls


def test_domain_layer_imports_only_an_allowlist_of_pure_standard_library_modules():
    imports, calls = _domain_imports_and_calls()

    def allowed(module):
        return (module == "rtb.domain" or module.startswith(("rtb.domain.", "."))
                or module.split(".")[0] in PURE_STDLIB_ALLOWLIST)

    assert [(f, m) for f, m in imports if not allowed(m)] == []
    assert [(f, c) for f, c in calls if c in FORBIDDEN_BUILTIN_CALLS] == []


@pytest.mark.parametrize("reason", list(Reason))
def test_every_kind_of_unknown_result_answers_unknown_to_threshold_comparisons(reason):
    """2026-09-22 第二輪審計指出:原本只拿「分母為零」去比門檻,只特判那一種原因、
    缺資料與不合理資料照樣拿去比大小,測試仍綠。"""
    unknown = MetricResult(value=None, reason=reason)

    assert unknown.below(0.5) is None
    assert unknown.at_least(0.5) is None


@pytest.mark.parametrize("spend,budget,elapsed",
                         [(25.0, 100.0, 0.0), (25.0, 0.0, 0.5), (0.0, 0.0, 0.0)])
def test_pacing_without_a_denominator_is_unknown_even_when_money_was_already_spent(
    spend, budget, elapsed
):
    """2026-09-22 第三輪審計指出:原本只測「還沒花錢」的零分母,把「已經花了錢、但時間或預算
    還是零」順手算成 0 進度,測試照樣綠;而決策規則會把那個 0 讀成「明顯配速不足」。"""
    result = pacing(spend, budget, elapsed)

    assert result.value is None and result.reason == Reason.NO_DENOMINATOR
    assert result.below(0.5) is None


@pytest.mark.parametrize("spend,budget,elapsed", [(50.0, None, 0.5), (50.0, 100.0, None),
                                                  (None, 100.0, 0.5)])
def test_pacing_with_any_single_input_missing_is_missing_data(spend, budget, elapsed):
    """2026-09-22 第四輪合約審計指出:缺漏只測過花費那一欄;寫一個「沒給時間比例就當成整期
    跑完」的捷徑,缺值會被算成有值的配速,測試照樣綠。三個輸入各自缺漏都必須是缺漏。"""
    result = pacing(spend, budget, elapsed)

    assert result.value is None and result.reason == Reason.MISSING_DATA


# ---- Phase 13 增量 2:收據的精確比率與固定字串([S1147] [S1162]) ----

def test_receipt_values_are_fixed_strings_computed_in_the_domain():
    """[S1147] 比率與百分比用百分比刻度、1 位小數、四捨五入到偶數;金額 2 位小數;計數整數;負零寫 0.0;
    算不出寫 na;既有三態結果型別不變。"""
    assert m.receipt_ratio(12, 500) == "2.4"
    assert m.receipt_ratio(247, 2000) == "12.4"  # 浮點會算成 12.35 再捨成 12.3
    assert m.receipt_ratio(245, 2000) == "12.2"  # 12.25 → 偶數 12.2
    assert m.receipt_ratio(0, 500) == "0.0"
    assert m.receipt_change(3, 2) == "-33.3"
    assert m.receipt_change(10_000, 9_996) == "0.0"  # -0.04% 捨入後不寫負零
    assert m.receipt_change(4, 6) == "50.0"
    assert m.receipt_amount(0.5) == "0.50"
    assert m.receipt_amount(5.0) == "5.00"
    assert m.receipt_amount(-0.0) == "0.00"
    assert m.receipt_amount(2.675) == "2.68"  # repr 是 2.675,銀行家捨入到 2.68(8 是偶數)
    assert m.receipt_amount(0.125) == "0.12"
    assert m.receipt_count(5) == "5"
    for bad in (None, -1, True, 1.5, "5"):
        assert m.receipt_count(bad) == m.NA, bad
    for bad in (None, -1.0, float("nan"), float("inf"), True, "1"):
        assert m.receipt_amount(bad) == m.NA, bad
    # 算不出的三態都寫 na
    assert m.receipt_ratio(3, 0) == m.NA
    assert m.receipt_ratio(None, 5) == m.NA
    assert m.receipt_ratio(-1, 5) == m.NA
    assert m.receipt_change(0, 5) == m.NA
    assert m.NA == "na"
    # 既有三態結果型別不變:照舊只收整數與浮點
    with pytest.raises(ValueError):
        MetricResult(value=Fraction(1, 3))  # type: ignore[arg-type]
    assert MetricResult.__dataclass_fields__.keys() == {"value", "reason"}


def test_one_exact_ratio_function_feeds_receipts_and_the_answer_key(monkeypatch):
    """[S1162] 精確比率函式回分數或三態原因代碼之一;收據格式化與標準答案產生函式都經它取得比率;
    金額量化溢位寫 na。"""
    assert m.exact_ratio(12, 500) == Fraction(3, 125)
    assert m.exact_ratio(0.5, 2) == Fraction(1, 4)
    assert m.exact_ratio(3, 0) is Reason.NO_DENOMINATOR
    assert m.exact_ratio(None, 3) is Reason.MISSING_DATA
    assert m.exact_ratio(None, 0) is Reason.MISSING_DATA  # 缺漏優先於分母為零
    assert m.exact_ratio(-1, None) is Reason.INVALID_DATA  # 不合理優先於缺漏
    assert m.exact_ratio(True, 3) is Reason.INVALID_DATA
    assert m.exact_ratio(float("inf"), 3) is Reason.INVALID_DATA
    assert m.exact_ratio(Reason.MISSING_DATA, 3) is Reason.MISSING_DATA  # 巢狀比率照傳原因
    assert m.exact_ratio(Fraction(1, 2), Fraction(1, 4)) == 2
    assert m.exact_change(10_000, 9_996) == Fraction(-1, 2500)
    assert m.exact_click_rate(13, 12) is Reason.INVALID_DATA  # 點擊不可能比曝光多(同既有 ctr)
    seen = []
    real = m.exact_ratio

    def spy(numerator, denominator):
        seen.append((numerator, denominator))
        return real(numerator, denominator)

    monkeypatch.setattr(m, "exact_ratio", spy)
    assert m.receipt_ratio(12, 500) == "2.4"
    assert m.receipt_change(3, 2) == "-33.3"
    assert m.receipt_click_rate(12, 500) == "2.4"
    assert len(seen) == 3
    monkeypatch.undo()
    assert m.receipt_amount(1e27) == m.NA  # Decimal 量化溢位歸資料不合理
    assert m.receipt_amount(10**26) == m.NA
    assert m.receipt_amount(10**20) == "100000000000000000000.00"
    # 標準答案產生函式那半(Phase 13 增量 3):評估的標準答案也只經它取得比率,
    # 算出的值寫成收據字串時跟收據格式化逐字相同
    answer_key_goes_through_exact_ratio(monkeypatch)


# ---- Phase 13 增量 2 代碼審 r1(d1、d2) ----
def test_a_receipt_value_too_long_for_trusted_evidence_is_na():
    """d1:比率寫出來超過可信證據字串上限(128 字)歸資料不合理,寫 na。"""
    assert m.receipt_change(1e-300, 1e300) == m.NA
    assert m.receipt_ratio(10**200, 1) == m.NA
    assert len(m.receipt_ratio(10**123, 1)) == 128  # 剛好放得下就照寫
    assert m.receipt_ratio(10**130, 1) == m.NA  # 整數部分 133 位數
    assert m.receipt_ratio(10**20, 1) != m.NA


def test_money_ratios_use_the_decimal_value_written_on_the_receipt():
    """d2:涉及金額的比率先照收據金額的 Decimal(repr(x)) 轉十進位再算,跟收據上的金額一致。"""
    assert m.receipt_change(0.16, 0.17) == "6.2"
    assert m.receipt_change(0.16, 0.15) == "-6.2"
    assert m.exact_ratio(1.15, 100) == Fraction(115, 10000)
    from rtb.analyzer import investigation as inv

    base = inv.base_receipt({"status": "active", "budget": 2400},
                            {"impressions": 10, "clicks": 1, "conversions": 0, "spend": 1.15,
                             "revenue": 0.0}, 24)
    assert base["pacing"] == "1.2"


def test_percent_text_places_share_one_half_even_rounding():
    """Phase 15 代碼審 a_2:規則探勘的平均差值(百分點四位小數)共用 percent_text,不另寫捨入;
    預設一位小數的既有行為不變。"""
    assert m.percent_text(Fraction(1234567, 10**7)) == "12.3"
    assert m.percent_text(Fraction(1234567, 10**7), places=4) == "12.3457"
    assert m.percent_text(Fraction(1, 2_000_000), places=4) == "0.0000"  # 0.00005 → 偶數 0
    assert m.percent_text(Fraction(3, 2_000_000), places=4) == "0.0002"  # 0.00015 → 偶數 2
    assert m.percent_text(Fraction(-1, 50), places=4) == "-2.0000"
    assert m.percent_text(Fraction(-1, 3_000_000), places=4) == "0.0000"  # 負零寫 0
    assert m.percent_text(Reason.NO_DENOMINATOR, places=4) == m.NA
    for bad in (0, -1, True, 1.0):
        with pytest.raises(ValueError, match="小數位數"):
            m.percent_text(Fraction(1, 2), places=bad)
