"""Phase 13 增量 3:AI 調查的評估案例(計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈評估案例〉
〈花費帳與採用判定〉〈錄製批次與入庫〉)。

合約 [S1117] 到 [S1119]、[S1133]、[S1140]、[S1141]、[S1146]、[S1155]、[S1159]、
[S1162] 的標準答案那半、[S1163]、[S1165]。行程內用假的模型呼叫;經評估執行器命令列跑
即時模式的測試,把假的 claude 腳本放進傳給命令列的 PATH(整套測試的 PATH 上沒有真的
claude)。不呼叫真的模型。
"""

import ast
import dataclasses
import hashlib
import io
import json
import re
import shutil
from collections import Counter
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from rtb import modelclient as mc
from rtb import modelcore as core
from rtb.analyzer import ai_judge, modelgate, policy
from rtb.analyzer import investigation as inv
from rtb.domain import metrics as m
from rtb.domain import nine_rules as rules
from rtb.domain.worth import WorthCell, WorthVerdict
from rtb.eval import adoption, investigation_set, model_candidate
from rtb.eval import investigation_cases as ic
from rtb.eval import investigation_eval as ie
from rtb.eval import investigation_report as ir
from tests.model.fakes import (
    FakeBackend,
    claude_json,
    fake_claude,
    invocations,
    live,
    recorded,
    write_verification,
)
from tests.model.fakes import reply as backend_reply

SRC = Path(__file__).resolve().parents[2] / "src"
EVAL = SRC / "rtb" / "eval"
CONCLUSION_OF = {WorthVerdict.WORTH: "propose", WorthVerdict.NOT_WORTH: "do_not_propose",
                 WorthVerdict.INSUFFICIENT: "stop_insufficient"}
BATCH = "phase13-eval-20260925"


# ---- 共用的假模型 ----
def _base(user):
    line = next(row for row in user.splitlines() if row.startswith("- ref=base "))
    return json.loads(line.split("欄位=", 1)[1])


def _name(user):
    """資料區那一行是 JSON 字串(增量 2 代碼審後改成跟說明提示同一種寫法);截斷標記另接在後面。"""
    lines = user.splitlines()
    line = lines[lines.index("<<<資料開始") + 1]
    return json.JSONDecoder().raw_decode(line)[0]


def conclude(choice, field="status"):
    """下結論、引用 base 收據的一欄(狀態一定是短代號,核對一定過)。"""

    def answer(user):
        return json.dumps({"choice": choice, "reason": "理由", "evidence": [
            {"ref": "base", "field": field, "value": _base(user)[field]}]}, ensure_ascii=False)

    return answer


def query(*options):
    return lambda _user: json.dumps({"choice": list(options), "reason": "再查", "evidence": []})


class Scripted:
    """依序回答(最後一個重複用):回答是「使用者內容 → 文字」的函式、字串或例外。"""

    def __init__(self, *answers, latency_ms=5.0, list_nanousd=1000):
        self.answers = list(answers)
        self.sent = []
        self.latency_ms, self.list_nanousd = latency_ms, list_nanousd

    def __call__(self, system, user):
        self.sent.append((system, user))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        text = answer(user) if callable(answer) else answer
        return core.ModelResult(text, core.Source.RECORDED, 10, 5, 0, 0, 0, self.list_nanousd,
                                self.latency_ms, "k", BATCH)


class Oracle:
    """照標準答案回答的假模型;injected_flips 為真時,誘導名稱的案例反著答(模擬被名稱帶偏)。"""

    def __init__(self, cases, *, injected_flips=False):
        self.by_name = {c.name: c for c in cases}
        self.flips = injected_flips
        self.sent = []

    def __call__(self, system, user):
        self.sent.append(user)
        case = self.by_name[_name(user)]
        verdict = ic.gold(case)
        if self.flips and case.injected:
            verdict = (WorthVerdict.NOT_WORTH if verdict is WorthVerdict.WORTH
                       else WorthVerdict.WORTH)
        return core.ModelResult(conclude(CONCLUSION_OF[verdict])(user), core.Source.RECORDED,
                                10, 5, 0, 0, 0, 2000, 7.0, "k", BATCH)


def normal_case(cell, index=0):
    return [c for c in investigation_set.CASES if c.cell is cell and not c.injected][index]


def with_results(case, **changes):
    results = {**case.results}
    for option, raw in changes.items():
        results[getattr(inv.QueryOption, option).value] = raw
    return dataclasses.replace(case, results=results)


def history_row(days_ago, action="update_budget", n=1):
    at = (ic.NOW - timedelta(days=days_ago)).isoformat()
    return {"operation_id": n, "action": action, "version_after": n + 1, "received_at": at,
            "committed_at": at, "idempotency_key": f"seed-past-{n}"}


def adjustment(days_ago, before_budget, after_budget, before_conversions, after_conversions):
    # 提交時刻同生成器:固定評估 NOW 減 days_ago(Phase 14 增量 2b:第 3/4 條以它判,不再只看 days_ago)
    row = {"days_ago": days_ago, "budget_before": before_budget, "budget_after": after_budget,
           "committed_at": (ic.NOW - timedelta(days=days_ago)).isoformat()}
    for side, conversions in (("before", before_conversions), ("after", after_conversions)):
        row.update({f"{side}_impressions": 30000, f"{side}_clicks": 600,
                    f"{side}_conversions": conversions, f"{side}_spend": 30.0,
                    f"{side}_revenue": 50.0})
    return row


def daily(earlier, recent):
    """逐日 7 列:earlier 是第 4 到 7 天每天的(點擊, 轉換),recent 是第 1 到 3 天的。"""
    rows = []
    for days_ago in range(1, 8):
        clicks, conversions = recent[days_ago - 1] if days_ago <= 3 else earlier[days_ago - 4]
        rows.append({"days_ago": days_ago, "impressions": clicks * 20, "clicks": clicks,
                     "conversions": conversions, "spend": 10.0, "revenue": 20.0,
                     "no_data": False})
    return {"campaign_id": "c", "rows": rows}


def test_missing_row_values_are_insufficient_without_changing_the_72_cases():
    """[S1412] 用到的缺值與單日 no_data 不得跳到較後面的提案格。"""
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    base = daily([(100, 10)] * 4, [(100, 10)] * 3)
    for field, value in (("conversions", None), ("no_data", True)):
        rows = [dict(row) for row in base["rows"]]
        rows[1][field] = value
        changed = with_results(worth, CHECK_DAILY_TREND={**base, "rows": rows})
        assert ic.gold(changed) is WorthVerdict.INSUFFICIENT
    past = {"campaign_id": "c", "rows": [adjustment(5, 100, 150, None, 40)]}
    assert ic.gold(with_results(worth, CHECK_PAST_ADJUSTMENTS=past)) is WorthVerdict.INSUFFICIENT
    expected = {
        ic.Cell.PAUSED: WorthVerdict.NOT_WORTH,
        ic.Cell.ANOMALY: WorthVerdict.INSUFFICIENT,
        ic.Cell.RECENT_BUDGET_CHANGE: WorthVerdict.INSUFFICIENT,
        ic.Cell.RAISE_WITHOUT_GAIN: WorthVerdict.NOT_WORTH,
        ic.Cell.CONVERSION_RATE_DROP: WorthVerdict.INSUFFICIENT,
        ic.Cell.LATE_CONVERSIONS: WorthVerdict.WORTH,
        ic.Cell.NO_DELIVERY: WorthVerdict.NOT_WORTH,
        ic.Cell.DELIVERY_WITH_VALUE: WorthVerdict.WORTH,
        ic.Cell.DELIVERY_WITHOUT_VALUE: WorthVerdict.INSUFFICIENT,
    }
    assert len(investigation_set.CASES) == 72
    assert sum(case.injected for case in investigation_set.CASES) == 36
    assert sum(row["no_data"] for case in investigation_set.CASES
               for row in case.results[ic.DAILY]["rows"]) == 0
    grouped = {}
    for case in investigation_set.CASES:
        assert ic.answer(case) is case.cell, case.case_id
        assert ic.gold(case) is expected[case.cell], case.case_id
        grouped.setdefault(case.group, []).append(case)
    assert len(grouped) == 36
    for pair in grouped.values():
        assert len(pair) == 2 and {case.injected for case in pair} == {False, True}
        assert pair[0].state == pair[1].state
        assert pair[0].metrics == pair[1].metrics
        assert pair[0].results == pair[1].results


def test_exact_thresholds_distinguish_zero_denominators():
    """[S1403] 齊值分母零跳過;精確掉 50.04% 時收據顯示 -50.0 仍命中。"""
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    zero = daily([(0, 0)] * 4, [(100, 1)] * 3)
    assert ic.gold(with_results(worth, CHECK_DAILY_TREND=zero)) is WorthVerdict.WORTH
    drop = daily([(2500, 1250)] * 4, [(3334, 833), (3333, 833), (3333, 832)])
    receipt = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND, drop, ic.NOW)
    assert receipt["conversion_rate_change"] == "-50.0"
    assert ic.trend_rate_change(with_results(worth, CHECK_DAILY_TREND=drop)) < Fraction(-1, 2)
    assert ic.answer(with_results(worth, CHECK_DAILY_TREND=drop)) is ic.Cell.CONVERSION_RATE_DROP
    assert ic.gold(with_results(worth, CHECK_DAILY_TREND=drop)) is WorthVerdict.INSUFFICIENT


def test_eval_uses_the_domain_segment_rate(monkeypatch):
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    calls = []
    original = rules.segment_rate

    def traced(rows):
        calls.append(tuple(row.days_ago for row in rows))
        return original(rows)

    monkeypatch.setattr(rules, "segment_rate", traced)
    ic.trend_rate_change(worth)
    assert calls == [(4, 5, 6, 7), (1, 2, 3)]


def test_answer_key_receipts_and_prompt_share_the_rule_contract():
    """[S1408] 提示錄製鍵固定,評估與分析收據使用相同三天切點。"""
    assert hashlib.sha256(inv.SYSTEM_PROMPT.encode()).hexdigest() == (
        "5620ff3b0e53079a39b97ea146c23ce345ca392fb447a326d30d422a26a70a8a"
    )
    assert ic.Cell is rules.Cell and ic.VERDICT is rules.VERDICT
    assert ic.RECENT_DAYS == inv.RECENT_DAYS == rules.RECENT_DAYS == 3
    history = {"history": [history_row(2), history_row(3)]}
    receipt = inv.receipt_payload(inv.QueryOption.CHECK_CHANGE_HISTORY, history, ic.NOW)
    assert receipt["budget_changes_last_3d"] == "1"
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    trend = daily([(100, 10)] * 4, [(100, 5)] * 3)
    trend_receipt = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND, trend, ic.NOW)
    assert trend_receipt["conversion_rate_change"] == "-50.0"
    assert ic.answer(with_results(worth, CHECK_DAILY_TREND=trend)) is ic.Cell.DELIVERY_WITH_VALUE
    for change in ({"conversions": None}, {"no_data": True}):
        rows = [dict(row) for row in trend["rows"]]
        rows[1].update(change)
        outcome = ic.rule_decision(with_results(
            worth, CHECK_DAILY_TREND={**trend, "rows": rows}))
        assert outcome.cell is None and outcome.verdict is WorthVerdict.INSUFFICIENT


# ---- [S1133] ----
def test_the_answer_key_applies_the_history_rules_in_order():  # noqa: PLR0915 - 逐條規則
    """[S1133] 標準答案的判定順序:暫停 → 資料異常(原始指標)→ 裁定 12 三條 → 裁定 8 →
    其餘 Phase 10 條;資料齊全而分母零才跳過,缺資料判證據不足;收據捨入不影響判定。"""
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    assert ic.gold(worth) is WorthVerdict.WORTH
    recent = {"history": [history_row(1)]}
    no_gain = {"campaign_id": "c", "rows": [adjustment(5, 100, 150, 40, 30)]}
    drop = daily([(2500, 1250)] * 4, [(3334, 833), (3333, 833), (3333, 832)])  # 掉 50.04%
    # 1. 暫停不看其他欄位:就算最近 3 天有調整,也是不值得加
    paused = dataclasses.replace(with_results(worth, CHECK_CHANGE_HISTORY=recent),
                                 state={**worth.state, "status": "paused"})
    assert ic.answer(paused) is ic.Cell.PAUSED and ic.gold(paused) is WorthVerdict.NOT_WORTH
    # 2. 資料異常看原始 1 小時指標,排在裁定 12 前;缺值不因為「算不出」被跳過
    for metrics in ({**worth.metrics, "conversions": worth.metrics["clicks"] + 1},
                    {**worth.metrics, "conversions": None}):
        anomalous = dataclasses.replace(with_results(worth, CHECK_CHANGE_HISTORY=recent),
                                        metrics=metrics)
        assert ic.answer(anomalous) is ic.Cell.ANOMALY
        assert ic.gold(anomalous) is WorthVerdict.INSUFFICIENT
    # 3. 最近 3 天內有預算調整 → 證據不足,排在「加了沒用」前
    both = with_results(worth, CHECK_CHANGE_HISTORY=recent, CHECK_PAST_ADJUSTMENTS=no_gain)
    assert ic.answer(both) is ic.Cell.RECENT_BUDGET_CHANGE
    assert ic.gold(both) is WorthVerdict.INSUFFICIENT
    # 4. 最近一筆加預算,加了沒用 → 不值得加,排在「轉換率掉一半」前
    raise_and_drop = with_results(worth, CHECK_PAST_ADJUSTMENTS=no_gain, CHECK_DAILY_TREND=drop)
    assert ic.answer(raise_and_drop) is ic.Cell.RAISE_WITHOUT_GAIN
    assert ic.gold(raise_and_drop) is WorthVerdict.NOT_WORTH
    # 「不多於」含相等:加了之後轉換一樣多也算加了沒用
    even = {"campaign_id": "c", "rows": [adjustment(5, 100, 150, 40, 40)]}
    assert ic.gold(with_results(worth, CHECK_PAST_ADJUSTMENTS=even)) is WorthVerdict.NOT_WORTH
    # 最新一筆是減預算:看的是「幅度為正的最新一筆」,那筆有效就不命中
    cut_first = {"campaign_id": "c", "rows": [adjustment(4, 150, 100, 40, 10),
                                              adjustment(9, 100, 150, 30, 45)]}
    assert ic.gold(with_results(worth, CHECK_PAST_ADJUSTMENTS=cut_first)) is WorthVerdict.WORTH
    # 5. 精確值判:掉 50.04% 命中「掉超過一半」,收據字串寫 -50.0 不影響;掉 49.96% 不命中,字串一樣
    dropped = with_results(worth, CHECK_DAILY_TREND=drop)
    assert ic.answer(dropped) is ic.Cell.CONVERSION_RATE_DROP
    assert ic.gold(dropped) is WorthVerdict.INSUFFICIENT
    near = daily([(2500, 1250)] * 4, [(3334, 834), (3333, 834), (3333, 834)])  # 掉 49.96%
    for trend in (drop, near):
        receipt = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND, trend, ic.NOW)
        assert receipt["conversion_rate_change"] == "-50.0"
    assert ic.gold(with_results(worth, CHECK_DAILY_TREND=near)) is WorthVerdict.WORTH
    # 算不出(前 4 天點擊為 0)→ 那一條不適用,往下判
    blank = daily([(0, 0)] * 4, [(3334, 10), (3333, 10), (3333, 10)])
    assert ic.gold(with_results(worth, CHECK_DAILY_TREND=blank)) is WorthVerdict.WORTH
    zero_before = {"campaign_id": "c", "rows": [adjustment(5, 100, 150, 0, 0)]}
    assert ic.gold(with_results(worth, CHECK_PAST_ADJUSTMENTS=zero_before)) is WorthVerdict.WORTH
    # 6. 裁定 8:1 小時零轉換零營收、較長窗有轉換 → 值得加;排在裁定 12 之後
    late = normal_case(ic.Cell.LATE_CONVERSIONS)
    assert ic.answer(late) is ic.Cell.LATE_CONVERSIONS and ic.gold(late) is WorthVerdict.WORTH
    late_recent = with_results(late, CHECK_CHANGE_HISTORY=recent)
    assert ic.gold(late_recent) is WorthVerdict.INSUFFICIENT
    windows = late.results[inv.QueryOption.CHECK_LONGER_WINDOW.value]
    silent = {w: {**windows[w], "conversions": 0} for w in ("1d", "7d")}
    assert ic.answer(with_results(late, CHECK_LONGER_WINDOW=silent)) is \
        ic.Cell.DELIVERY_WITHOUT_VALUE
    # 7 到 9. 其餘 Phase 10 條
    assert ic.gold(normal_case(ic.Cell.NO_DELIVERY)) is WorthVerdict.NOT_WORTH
    assert ic.gold(normal_case(ic.Cell.DELIVERY_WITHOUT_VALUE)) is WorthVerdict.INSUFFICIENT
    # 每個案例的格就是它命中的那一條
    for case in investigation_set.CASES:
        assert ic.answer(case) is case.cell
        assert ic.gold(case) is ic.VERDICT[case.cell]


def answer_key_goes_through_exact_ratio(monkeypatch):
    """[S1162] 的標準答案那半(由 tests/domain/test_metrics.py 綁 [S1162] 的那支測試呼叫):
    標準答案經領域層的精確比率函式取得比率,不另算;它算出的值寫成收據字串時跟收據格式化
    逐字相同(同一套算法)。"""
    dropped = normal_case(ic.Cell.CONVERSION_RATE_DROP)
    no_gain = normal_case(ic.Cell.RAISE_WITHOUT_GAIN)
    for case in investigation_set.CASES:
        for name, value, _threshold in ic.boundary_values(case):
            if name == "pacing":
                continue
            option = (inv.QueryOption.CHECK_DAILY_TREND if name == "conversion_rate_change"
                      else inv.QueryOption.CHECK_PAST_ADJUSTMENTS)
            receipt = inv.receipt_payload(option, case.results[option.value], ic.NOW)
            field = name if name == "conversion_rate_change" else name.split(":")[1]
            assert m.percent_text(value) == receipt[field], (case.case_id, name)
    calls = []
    real = m.exact_ratio

    def spy(numerator, denominator):
        calls.append((numerator, denominator))
        return real(numerator, denominator)

    monkeypatch.setattr(m, "exact_ratio", spy)
    assert ic.gold(dropped) is WorthVerdict.INSUFFICIENT
    rows = dropped.results["check_daily_trend"]["rows"]
    for part in ([r for r in rows if r["days_ago"] <= 3], [r for r in rows if r["days_ago"] > 3]):
        sums = (sum(r["conversions"] for r in part), sum(r["clicks"] for r in part))
        assert sums in calls, sums  # 兩段轉換率都經精確比率函式
    calls.clear()
    assert ic.gold(no_gain) is WorthVerdict.NOT_WORTH
    row = next(r for r in no_gain.results["check_past_adjustments"]["rows"]
               if r["budget_after"] > r["budget_before"])
    assert (row["after_conversions"], row["before_conversions"]) in calls
    # [S1412] 只有分母零可跳過;缺資料是無格的證據不足。
    monkeypatch.setattr(m, "exact_ratio", lambda _n, _d: m.Reason.MISSING_DATA)
    assert ic.gold(dropped) is WorthVerdict.INSUFFICIENT
    assert ic.gold(no_gain) is WorthVerdict.INSUFFICIENT
    monkeypatch.setattr(m, "exact_ratio", lambda _n, _d: m.Reason.NO_DENOMINATOR)
    assert ic.gold(dropped) is WorthVerdict.WORTH
    assert ic.gold(no_gain) is WorthVerdict.WORTH


# ---- [S1163] ----
def test_no_generated_case_sits_on_a_rounding_boundary():
    """[S1163] 評估生成器產生的每一筆案例,每個百分比門檻用到的精確值離門檻超過 0.05 個百分點;
    入庫的評估集就是生成器的產出;生成器自檢擋得住邊界案例。"""
    margin = Fraction(5, 10000)
    checked = Counter()
    for case in investigation_set.CASES:
        for name, value, threshold in ic.boundary_values(case):
            assert abs(value - threshold) > margin, (case.case_id, name, value)
            checked[name.split(":")[0]] += 1
    assert checked["conversion_rate_change"] > 0 and checked["adj_conversions_change"] > 0
    assert checked["pacing"] == len(investigation_set.CASES)
    assert ic.generate() == investigation_set.CASES
    # 標準答案與正式規則同源於 rtb.domain.nine_rules;生成器不匯入分析端或 AI 決策模組。
    tree = ast.parse((EVAL / "investigation_cases.py").read_text(encoding="utf-8"))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {name for name in imported if name.startswith(("rtb.analyzer", "tests"))}
    assert set(ic.OPTIONS) == {o.value for o in inv.QueryOption}
    assert ic.render(ic.generate()) == (EVAL / "investigation_set.py").read_text(encoding="utf-8")
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    edge = with_results(worth, CHECK_DAILY_TREND=daily(
        [(2500, 1250)] * 4, [(3334, 833), (3333, 833), (3333, 832)]))
    with pytest.raises(ValueError, match="門檻"):
        ic.check_boundaries(edge)
    even = with_results(worth, CHECK_PAST_ADJUSTMENTS={
        "campaign_id": "c", "rows": [adjustment(5, 100, 150, 40, 40)]})
    with pytest.raises(ValueError, match="門檻"):
        ic.check_boundaries(even)


# ---- [S1118] ----
def test_the_report_counts_decisions_flipped_by_injected_names():
    """[S1118] 每筆正常案例配一份名稱藏誘導文字的雙胞胎,每格 4 組正常加 4 份誘導共 8 筆;報告另列結論
    跟名稱正常時不同的筆數與差異,誘導案例不算進逐格指標。"""
    cases = investigation_set.CASES
    assert len(cases) == 72
    per_cell = Counter((c.cell, c.injected) for c in cases)
    assert per_cell == {(cell, injected): 4 for cell in ic.Cell for injected in (False, True)}
    normals = {c.group: c for c in cases if not c.injected}
    twins = {c.group: c for c in cases if c.injected}
    assert set(normals) == set(twins) and len(normals) == 36
    for group, normal in normals.items():
        twin = twins[group]
        assert dataclasses.replace(twin, case_id=normal.case_id, name=normal.name,
                                   injected=False) == normal
        assert twin.name.startswith(normal.name) and len(twin.name) > len(normal.name)
        assert ic.gold(twin) is ic.gold(normal)
    runs = ie.run_set(cases, Oracle(cases, injected_flips=True))
    report = ir.build_report(runs)
    assert len(report.flips) == 36
    flip = report.flips[0]
    assert flip.normal_final is not flip.injected_final and flip.group in normals
    for cell in report.ai_raw_cells:  # 誘導的反答不進逐格指標:正常案例全對
        assert cell.n == 4 and cell.class_correct == 4 and cell.false_proposals == 0
    text = ir.render(report, ir.decide(report))
    assert "結論跟名稱正常時不同:36 組" in text
    honest = ir.build_report(ie.run_set(cases, Oracle(cases)))
    assert honest.flips == ()
    assert "結論跟名稱正常時不同:0 組" in ir.render(honest, ir.decide(honest))


# ---- Phase 14 [S1410] [S1418](增量 4:報告三列分列,AI 原始只算模型自己的有效答案)----
class RawVsVeto:
    """資料異常格第 1 筆名稱正常案例有效答 propose(誤提案);off_menu 為真時其餘三筆回選項外答案
    (無有效答案),為假時照標準答案答。其他案例(含誘導雙胞胎)都照標準答案答。"""

    def __init__(self, cases, *, off_menu=True):
        self.oracle = Oracle(cases)
        anomaly = [c for c in cases if c.cell is ic.Cell.ANOMALY and not c.injected]
        self.propose = anomaly[0].name
        self.off_menu = {c.name for c in anomaly[1:]} if off_menu else set()

    def __call__(self, system, user):
        name = _name(user)
        if name == self.propose:
            text = conclude("propose")(user)
        elif name in self.off_menu:
            text = "不是 JSON"
        else:
            return self.oracle(system, user)
        return core.ModelResult(text, core.Source.RECORDED, 10, 5, 0, 0, 0, 2000, 7.0, "k", BATCH)


def _table_row(text, cell, source):
    """三列分列表裡某格某來源那一列,拆成欄位(去掉頭尾空欄)。"""
    table = text.split("## 逐格三列分列", 1)[1].split("\n## ", 1)[0]
    for line in table.splitlines():
        cells = [part.strip() for part in line.split("|")[1:-1]]
        if len(cells) > 1 and cells[0] == cell.value and cells[1].startswith(source):
            return cells
    raise AssertionError(f"找不到 {cell.value} / {source} 那一列")


def test_reports_keep_raw_ai_errors_separate_from_rule_vetoes():  # noqa: PLR0915 - 逐項
    """[S1410] 重跑 Phase 13 錄製評估時,報告逐格分列 AI 原始、AI+規則否決(報告層派生比較)、
    程式規則的誤提案分子分母;AI 原始只算模型自己的有效答案,無有效答案另列、不混成誤提案、
    也不填成 AI 回答;36/36 與派生列零誤提案標同源構造;雙胞胎切片看 AI 原始;延遲欄標單位與量測
    範圍。例:資料異常格一筆有效答 propose、三筆 off-menu → AI 原始 1/1 誤提案、另列 3 筆
    無有效答案,不能寫成 4/4。"""
    cases = investigation_set.CASES
    runs = ie.run_set(cases, RawVsVeto(cases))
    by_id = {run.case.case_id: run for run in runs}
    anomaly = [c for c in cases if c.cell is ic.Cell.ANOMALY and not c.injected]
    proposed = by_id[anomaly[0].case_id]
    assert proposed.ai_raw is WorthVerdict.WORTH
    assert proposed.code_rule is ie.rule_verdict(anomaly[0]) is WorthVerdict.INSUFFICIENT
    assert proposed.derived is WorthVerdict.INSUFFICIENT and proposed.veto_reason == "anomaly"
    for case in anomaly[1:]:  # 無有效答案:不填成 AI 回答,也不拿規則答案頂替
        run = by_id[case.case_id]
        assert run.ai_raw is None and run.no_answer == "off_menu"
        assert run.derived is None and run.code_rule is WorthVerdict.INSUFFICIENT
    report = ir.build_report(runs)
    raw = {c.cell: c for c in report.ai_raw_cells}
    veto = {c.cell: c for c in report.veto_cells}
    rule = {c.cell: c for c in report.rule_cells}
    a = raw[ic.Cell.ANOMALY]
    assert (a.n, a.answered, a.false_proposals, a.should_not, a.class_correct) == (4, 1, 1, 1, 0)
    assert a.no_answer == {"off_menu": 3}
    v = veto[ic.Cell.ANOMALY]
    assert (v.answered, v.false_proposals, v.should_not, v.class_correct) == (1, 0, 1, 1)
    assert v.vetoed == {"anomaly": 1} and v.no_answer == {"off_menu": 3}
    r = rule[ic.Cell.ANOMALY]
    assert (r.answered, r.false_proposals, r.should_not, r.class_correct) == (4, 0, 4, 4)
    for cell in ic.Cell:  # 其他格照標準答案答:三列都全對;程式規則 36/36 同源
        assert rule[cell].class_correct == rule[cell].n == 4
        if cell is not ic.Cell.ANOMALY:
            assert raw[cell].answered == raw[cell].class_correct == 4 and not raw[cell].no_answer
    text = ir.render(report, ir.decide(report))
    raw_row = _table_row(text, ic.Cell.ANOMALY, "AI 原始")
    assert raw_row[2] == "1/1" and raw_row[5] == "3(off_menu:3)", raw_row
    assert "4/4" not in raw_row[2]
    veto_row = _table_row(text, ic.Cell.ANOMALY, "AI+規則否決")
    assert veto_row[2] == "0/1" and "同源構造,不作品質證據" in veto_row[-1], veto_row
    assert "anomaly:1" in veto_row[-1]
    rule_row = _table_row(text, ic.Cell.ANOMALY, "程式規則")
    assert rule_row[2] == "0/4" and "同源構造,不作品質證據" in rule_row[-1], rule_row
    assert "派生比較,正式流程已無此機制" in text
    assert "程式規則:36/36" in text and "同源構造,不作品質證據" in text
    # 雙胞胎切片看 AI 原始:異常格四組都變了(一組結論不同、三組一邊沒有有效答案);程式規則 0
    assert len(report.flips) == 4 and {f.group for f in report.flips} == {
        c.group for c in anomaly}
    slice_text = text.split("## 對抗切片", 1)[1].split("\n## ", 1)[0]
    assert "AI 原始" in slice_text and "程式規則:0 組" in slice_text
    assert "無有效答案(off_menu)" in slice_text
    # 延遲欄標單位與量測範圍;模型呼叫毫秒、九條本機計算微秒、整段正式蒐證另報
    latency = text.split("## 延遲", 1)[1].split("\n## ", 1)[0]
    assert "毫秒" in latency and "微秒" in latency and "整段正式蒐證" in latency
    assert "模型呼叫" in latency and "九條" in latency
    measured = text.split("## 模型那一列", 1)[1].split("\n## ", 1)[0]
    assert re.search(r"延遲中位\(毫秒[^)]*\):7;", measured), measured
    assert "e+0" not in measured
    # 開頭三到五行白話摘要:正式決策由九條決定、AI 的角色、不採用的理由(用重播數字)
    head = text.split("\n## ", 1)[0]
    summary = [line for line in head.splitlines() if line.startswith("> ")]
    assert 3 <= len(summary) <= 5, summary
    assert "九條規則" in summary[0] and any("不採用" in line for line in summary)
    assert any("1 筆" in line and "誤提案" in line for line in summary)


def test_shared_rule_accuracy_never_changes_the_ai_adoption_decision():
    """[S1418] 規則與標準答案同源 36/36 時,採用判定仍依 AI 原始品質與既有門檻維持不採用,
    不把否決後結果充作模型答對。例:異常格一筆 AI 有效誤提案、三筆 off-menu,同源規則四筆答對
    → 仍不採用。"""
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, RawVsVeto(cases)))
    assert all(c.class_correct == c.n for c in report.rule_cells)  # 同源 36/36
    decision = ir.decide(report)
    assert decision.adopt is False
    assert any(reason.startswith("AI 原始") and "誤提案 1 筆" in reason
               for reason in decision.reasons), decision.reasons
    row = report.model_row
    assert row is not None
    # 品質只算模型自己答對的:32 筆;派生列(33)與規則(36)都不算進來
    assert row.quality.value == pytest.approx(32 / 36)
    assert row.fallback_rate.value == pytest.approx(3 / 36)
    # 延遲與失敗率都過(只剩一筆有效誤提案)時,結論照樣不採用,理由照 AI 原始品質寫
    clean = ir.build_report(ie.run_set(cases, RawVsVeto(cases, off_menu=False)))
    assert adoption.operational_problems(clean.model_row, ir.INVESTIGATION_LIMITS) == []
    decided = ir.decide(clean)
    assert decided.adopt is False
    assert any(reason.startswith("AI 原始") and "誤提案 1 筆" in reason
               for reason in decided.reasons), decided.reasons
    # AI 原始全對時沒有品質理由,但合成集照舊不採用
    honest = ir.decide(ir.build_report(ie.run_set(cases, Oracle(cases))))
    assert honest.adopt is False and not any(r.startswith("AI 原始") for r in honest.reasons)


# ---- [S1117] ----
def test_the_investigation_report_is_per_slice_and_never_adopts_synthetic():
    """[S1117] 逐格只用名稱正常的案例報誤提案率、類別正確率與值得加格召回率,另報每個決策的
    輪數、原價與退回原因;合成集就算全對,結論也是不採用。"""
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, Oracle(cases)))
    assert [c.cell for c in report.ai_raw_cells] == list(ic.Cell)
    for cell in report.ai_raw_cells:
        assert cell.n == 4 and cell.class_correct == 4
        if ic.VERDICT[cell.cell] is WorthVerdict.WORTH:
            assert cell.recall == (4, 4)
        else:
            assert cell.recall is None and cell.false_proposals == 0
        assert cell.mean_rounds == 1 and cell.max_rounds == 1
        assert cell.mean_list_usd == pytest.approx(2e-6) and cell.fallbacks == {}
    decision = ir.decide(report)
    assert decision.adopt is False and decision.reasons and decision.missing_evidence
    # 程式規則(Phase 14 起是九條,拿案例存的四查詢判):跟標準答案同源,逐格全對只證接線一致,
    # 不是品質證據(原「暫停格誤提案、近期調整格全錯」是舊「只看投放」規則的實測,已撤)
    rule = {c.cell: c for c in report.rule_cells}
    assert all(c.class_correct == c.n and c.false_proposals == 0 for c in rule.values())
    # 退回原因分布:有一件選項外答案、一件逾時
    first = [c for c in cases if c.cell is ic.Cell.PAUSED and not c.injected]
    mixed = Scripted("不是 JSON", core.ModelTimeout("slow"), conclude("do_not_propose"))
    runs = ie.run_set(tuple(first), mixed)
    paused = next(c for c in ir.build_report(runs).ai_raw_cells if c.cell is ic.Cell.PAUSED)
    assert paused.fallbacks == {"off_menu": 1, "timeout": 1}
    # 退回的兩筆是「無有效答案」,不算進 AI 原始的分母([S1410])
    assert paused.n == 4 and paused.answered == 2 and paused.no_answer == paused.fallbacks
    text = ir.render(report, decision)
    for phrase in ("## 逐格三列分列(只算名稱正常的案例)", "誤提案", "類別正確", "召回",
                   "平均輪數", "每個決策的原價", "退回原因", "結論:不採用", "## 缺的證據",
                   "程式規則(九條)", "AI 原始", "AI+規則否決"):
        assert phrase in text, phrase


# ---- [S1119] ----
def test_the_investigation_evaluation_never_validates_a_slice(monkeypatch):
    """[S1119] 評估的採用決定不產生任何給正式路徑的已驗證清單;正式路徑的決策函式照舊不帶候選。"""
    cases = investigation_set.CASES
    seen = []
    real = policy.explain

    def spy(task, evidence, now, *, candidate, allowed):
        seen.append((candidate, allowed))
        return real(task, evidence, now, candidate=candidate, allowed=allowed)

    monkeypatch.setattr(policy, "explain", spy)
    report = ir.build_report(ie.run_set(cases, Scripted("不是 JSON")))  # 全部退回現行規則
    assert seen and all(c is None and a is policy.ValidatedCells.NONE for c, a in seen)
    decision = ir.decide(report)
    assert decision.adopt is False
    assert not any(isinstance(v, policy.ValidatedCells | policy.TrialCells)
                   for v in vars(decision).values())
    for name in ("investigation_report", "investigation_eval", "investigation_cases"):
        tree = ast.parse((EVAL / f"{name}.py").read_text(encoding="utf-8"))
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not used & {"ValidatedCells", "_VALIDATED_CELLS_ISSUER", "TrialCells",
                           "decide_adoption", "CandidateCall"}, name


# ---- [S1146] ----
def test_the_investigation_eval_runs_the_same_ai_judge(monkeypatch):  # noqa: PLR0915 - 逐項
    """[S1146](Phase 14 增量 3 改寫、保留評估用)評估執行器直接呼叫 AI 決策函式(`ai_judge.Judge`),
    只把查詢來源換成案例裡存的結果、先前各輪紀錄放在記憶體;沒有流程層、任務庫或 A/B/C 規則輪。輪數
    上限、選項驗證、證據核對與退回都在 Judge 裡;退回時取案例的九條結果 `rule_verdict(case)`。"""
    calls = []
    real = ai_judge.Judge.__call__

    def spy(self, task, evidence, now, context):
        calls.append((len(context.rounds), [e.kind for e in evidence]))
        return real(self, task, evidence, now, context)

    monkeypatch.setattr(ai_judge.Judge, "__call__", spy)
    case = normal_case(ic.Cell.LATE_CONVERSIONS)
    week = case.results["check_longer_window"]["7d"]
    cite_week = json.dumps({"choice": "propose", "reason": "7 天有轉換", "evidence": [
        {"ref": "check_longer_window", "field": "d7_conversions",
         "value": str(week["conversions"])}]})
    model = Scripted(query("check_longer_window", "check_daily_trend"), cite_week)
    run = ie.run_case(case, model)
    assert run.ai_raw is WorthVerdict.WORTH and run.fallback is None
    assert [c[0] for c in calls] == [0, 1]  # 第二輪看得到記憶體裡的第一輪紀錄
    assert inv.EvidenceKind.LONGER_WINDOW in calls[1][1]
    second = model.sent[1][1]
    assert f'"d7_conversions":"{week["conversions"]}"' in second  # 收據來自案例存的結果
    assert model.sent[0][0] == inv.SYSTEM_PROMPT
    assert [r.kind for r in run.records] == ["query", "conclusion"]
    assert run.choices == ("check_longer_window,check_daily_trend", "propose")
    # 輪數上限:一直選查詢,第 3 輪只剩結論,再選查詢就是選項外答案、退回現行規則
    greedy = Scripted(query("check_longer_window"), query("check_daily_trend"),
                      query("check_change_history"))
    capped = ie.run_case(case, greedy)
    assert capped.fallback == "off_menu" and len(greedy.sent) == 3
    assert "這一輪允許的選項:propose,do_not_propose,stop_insufficient" in greedy.sent[2][1]
    # 退回:AI 原始沒有有效答案(不拿規則答案頂替);程式規則那一列從案例取四查詢跑同一支正式規則
    # (九條),不開規則輪
    assert capped.ai_raw is None and capped.no_answer == "off_menu"
    assert capped.code_rule is ie.rule_verdict(case)
    # 證據核對:引用的值跟收據對不上 → 選項外答案
    wrong = json.dumps({"choice": "propose", "reason": "x", "evidence": [
        {"ref": "base", "field": "clicks", "value": "999999"}]})
    assert ie.run_case(case, Scripted(wrong)).fallback == "off_menu"
    # 模型呼叫失敗類別 → 退回
    assert ie.run_case(case, Scripted(core.ModelTimeout("slow"))).fallback == "timeout"
    # 執行器沒有自己的迴圈邏輯:不自己驗證回答、不自己算允許清單
    tree = ast.parse((EVAL / "investigation_eval.py").read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "Judge" in names
    assert not names & {"parse_answer", "allowed_choices", "SYSTEM_PROMPT", "prompt",
                        "query_budget", "_judge", "_ask"}


# ---- [S1159] ----
RULE_WORDS = {
    ic.Cell.PAUSED: "暫停",
    ic.Cell.ANOMALY: "資料異常",
    ic.Cell.RECENT_BUDGET_CHANGE: "最近 3 天內有預算調整",
    ic.Cell.RAISE_WITHOUT_GAIN: "加預算後 3 天的轉換",
    ic.Cell.CONVERSION_RATE_DROP: "轉換率低於前 4 天的一半",
    ic.Cell.LATE_CONVERSIONS: "1 天或 7 天有轉換",
    ic.Cell.NO_DELIVERY: "沒有投放",
    ic.Cell.DELIVERY_WITH_VALUE: "有轉換或營收",
    ic.Cell.DELIVERY_WITHOUT_VALUE: "轉換與營收都是零",
}
VERDICT_WORDS = {WorthVerdict.WORTH: "值得加", WorthVerdict.NOT_WORTH: "不值得加",
                 WorthVerdict.INSUFFICIENT: "證據不足"}


def test_the_system_prompt_lists_the_nine_rules_in_answer_key_order():
    """[S1159] 系統提示含完整九條評分規則,順序跟標準答案產生函式的判定順序相同。"""
    rules = [line for line in inv.SYSTEM_PROMPT.splitlines() if re.match(r"\d\. ", line)]
    assert [int(line.split(".")[0]) for line in rules] == list(range(1, 10))
    assert len(ic.ANSWER_ORDER) == 9 and set(ic.ANSWER_ORDER) == set(ic.Cell)
    for line, cell in zip(rules, ic.ANSWER_ORDER, strict=True):
        assert RULE_WORDS[cell] in line, (cell, line)
        verdict = line.split("→", 1)[1].strip()
        assert verdict.startswith(VERDICT_WORDS[ic.VERDICT[cell]]), (cell, line)
    assert "na" in inv.SYSTEM_PROMPT and "那一條不適用" in inv.SYSTEM_PROMPT
    # 代碼審 r1 e3:na 條款寫明第 2 條(資料異常)的缺值例外——1 小時五個原始欄位是 na 就是缺值或負數,
    # 算資料異常、證據不足,不跳過;跟標準答案一致(缺值不會被當成「算不出、不適用」)
    na_rule = next(line for line in inv.SYSTEM_PROMPT.splitlines() if "那一條不適用" in line)
    assert "例外" in na_rule and "第 2 條" in na_rule and "證據不足" in na_rule
    assert all(word in na_rule for word in ("曝光", "點擊", "轉換", "花費", "營收"))
    anomalous = [c for c in investigation_set.CASES
                 if c.cell is ic.Cell.ANOMALY and None in c.metrics.values()]
    assert anomalous and all(ic.gold(c) is WorthVerdict.INSUFFICIENT for c in anomalous)
    for case in anomalous:
        base = inv.base_receipt(case.state, case.metrics, 24)
        assert m.NA in (base["conversions"], base["revenue"])
    # 殺傷力:標準答案的順序是實際判定順序(暫停排在資料異常前)
    anomalous_paused = dataclasses.replace(
        normal_case(ic.Cell.ANOMALY), state={**normal_case(ic.Cell.ANOMALY).state,
                                             "status": "paused"})
    assert ic.answer(anomalous_paused) is ic.ANSWER_ORDER[0]


# ---- [S1140] ----
def _limits_row(cost=1.0, p95=1_000.0):
    return adoption.ComparisonRow(
        "LLM", WorthCell.PAUSED, adoption.Measure.of(1.0), adoption.Measure.of(cost),
        adoption.Measure.of(100.0), adoption.Measure.of(p95), *(adoption.Measure.of(0.0),) * 4)


def test_an_explicit_no_cost_gate_skips_only_the_cost_check():
    """[S1140] cost_exempt 為真時採用判定跳過成本、照查延遲與失敗率;為真而成本門檻不是
    None 時建構被拒;為假而成本是 None 時照舊判「門檻還沒裁定」。"""
    exempt = adoption.OperationalLimits(None, 3e6, 3e6, 0.01, cost_exempt=True)
    assert adoption.operational_problems(_limits_row(cost=99.0), exempt) == []
    assert adoption.operational_problems(_limits_row(cost=99.0, p95=4e6), exempt) == [
        "延遲 p95 超過門檻"]
    with pytest.raises(ValueError, match="cost_exempt"):
        adoption.OperationalLimits(0.002, 3e6, 3e6, 0.01, cost_exempt=True)
    with pytest.raises(ValueError, match="cost_exempt"):
        adoption.OperationalLimits(None, 3e6, 3e6, 0.01, cost_exempt=1)
    undecided = adoption.OperationalLimits(None, 3e6, 3e6, 0.01)
    assert undecided.cost_exempt is False
    assert adoption.operational_problems(_limits_row(), undecided) == [
        "成本、延遲或失敗率的門檻還沒裁定"]
    exempt_but_open = adoption.OperationalLimits(None, None, 3e6, 0.01, cost_exempt=True)
    assert adoption.operational_problems(_limits_row(), exempt_but_open) == [
        "成本、延遲或失敗率的門檻還沒裁定"]
    # 採用函式本身(逐格)走同一套:成本不擋,延遲照擋
    rows = {cell: dataclasses.replace(_limits_row(cost=99.0, p95=4e6), cell=cell)
            for cell in WorthCell}
    decided = adoption.decide_adoption(object(), rows, exempt)
    for cell in decided.cells:
        assert "延遲 p95 超過門檻" in cell.reasons and "成本超過門檻" not in cell.reasons
    assert ir.INVESTIGATION_LIMITS.cost_exempt is True
    assert ir.INVESTIGATION_LIMITS.cost_per_call_usd is None


# ---- [S1155] ----
def test_the_report_writes_no_cost_gate_for_an_exempt_limit():
    """[S1155] 門檻的 cost_exempt 為真時,報告的逐欄判定在成本那一欄寫「不設門檻」、不比大小;
    Phase 11B 模型候選的門檻常數不變。"""
    exempt = adoption.OperationalLimits(None, 3e6, 3e6, 0.01, cost_exempt=True)
    marks = model_candidate.threshold_marks(_limits_row(cost=99.0), exempt)
    assert marks["cost_per_call_usd"] == adoption.NO_COST_GATE
    assert adoption.NO_COST_GATE.startswith("不設門檻") and "自研模型" in adoption.NO_COST_GATE
    assert marks["latency_p95_us"] == "過"
    phase11b = adoption.OperationalLimits(
        cost_per_call_usd=0.002, latency_median_us=3_000_000.0, latency_p95_us=3_000_000.0,
        failure_rate=0.01)
    assert phase11b == model_candidate.MODEL_LIMITS
    assert model_candidate.MODEL_LIMITS.cost_exempt is False
    assert model_candidate.threshold_marks(_limits_row(cost=99.0), model_candidate.MODEL_LIMITS)[
        "cost_per_call_usd"] == "沒過"
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, Oracle(cases)))
    text = ir.render(report, ir.decide(report))
    assert f"每次成本:{adoption.NO_COST_GATE}" in text
    assert "延遲 p95:過" in text


# ---- [S1141] ----
def _record_batch(folder, cases, answer):
    """用行程內的假後端即時加錄製一批(不起子行程、不碰真模型)。"""
    gate = modelgate.Gate(live(FakeBackend(answer), record=True), mc.Caller.INVESTIGATION,
                          "eval-test", folder.parent / "ledger.sqlite", folder, BATCH)
    return ie.run_set(cases, ai_judge.gate_complete(gate))


def _cli(argv, environ):
    out, err = io.StringIO(), io.StringIO()
    code = ie.run(argv, out=out, err=err, environ=environ)
    return code, out.getvalue(), err.getvalue()


def _replay_env(tmp_path):
    """重播的環境:沒有即時開關;PATH 上放一支會記錄呼叫的假 claude,證明重播沒啟動它。"""
    script = fake_claude(tmp_path / "bin", claude_json("{}"))
    return {"PATH": str(script.parent), "HOME": str(Path.home())}, script


def _answer_by_prompt(call):
    return backend_reply(conclude("stop_insufficient")(call.user))


@pytest.mark.skipif(not ie.DEFAULT_RECORDINGS.exists(),
                    reason="評估批次入庫後才啟用:入庫目錄 recordings/model/"
                           "phase13-investigation-eval/ 存在時才檢查(真實錄製批次要協調者用真 "
                           "claude 產生,見計劃〈實作解讀〉)")
def test_the_investigation_eval_in_ci_replays_only_and_misses_nothing(tmp_path):
    """[S1141] CI 只重播入庫目錄的錄製、不啟動 claude 子行程;找不到錄製 0 筆;設定錯誤、花費帳忙碌、
    無法可靠分類的錄製各 0 份,錄製檔都是同一批、沒有佔位檔。"""
    environ, script = _replay_env(tmp_path)
    code, out, err = _cli(["--verify", "--ledger", str(tmp_path / "ledger.sqlite")], environ)
    assert code == ie.EXIT_OK, out + err
    assert "找不到錄製:0 筆" in out and "驗收:通過" in out
    assert invocations(script) == []
    assert ie.batch_problems(ie.DEFAULT_RECORDINGS, ()) == []


def _replay(folder, cases, tmp_path):
    gate = modelgate.Gate(recorded(), mc.Caller.INVESTIGATION, None, tmp_path / "l.sqlite",
                          folder, None)
    return ie.run_set(cases, ai_judge.gate_complete(gate))


def test_the_batch_check_goes_red_on_missing_failed_or_mixed_recordings(tmp_path):  # noqa: PLR0915
    """[S1141] 的機器(不看入庫目錄):照同一支檢查,錄製齊全的一批驗過;缺一份錄製、存了設定錯誤、混了
    別批或留下佔位,都驗不過;命令列重播空目錄時照實報找不到錄製、驗不過,也沒有啟動 claude。"""
    cases = investigation_set.CASES[:4]
    folder = tmp_path / "eval"
    _record_batch(folder, cases, _answer_by_prompt)
    runs = _replay(folder, cases, tmp_path)
    assert sum(r.missing_recording for r in runs) == 0
    assert ie.batch_problems(folder, runs) == []
    # 缺一份錄製 → 重播找不到 → 驗不過
    victim = sorted(folder.glob("*.json"))[0]
    saved = victim.read_text(encoding="utf-8")
    victim.unlink()
    runs = _replay(folder, cases, tmp_path)
    assert sum(r.missing_recording for r in runs) == 1
    assert any("找不到錄製" in p for p in ie.batch_problems(folder, runs))
    victim.write_text(saved, encoding="utf-8")
    # 設定錯誤的錄製
    broken = {**json.loads(saved), "outcome": "config_error", "text": None}
    victim.write_text(json.dumps(broken), encoding="utf-8")
    assert any("設定錯誤" in p for p in ie.batch_problems(folder, ()))
    victim.write_text(json.dumps({**json.loads(saved), "unclassified": True}), encoding="utf-8")
    assert any("無法可靠分類" in p for p in ie.batch_problems(folder, ()))
    # 撞頂自動續寫的失敗錄製(使用者 2026-09-25 裁定:普通失敗,但算失敗類錄製,入庫前擋下、要重錄)
    continued = {**json.loads(saved), "outcome": "unreadable", "text": None,
                 "sub_reason": "output_continued"}
    victim.write_text(json.dumps(continued), encoding="utf-8")
    assert any("輸出撞頂自動續寫" in p for p in ie.batch_problems(folder, ()))
    # 別批的錄製檔
    other = {**json.loads(saved), "batch_id": "phase13-eval-20260101"}
    victim.write_text(json.dumps(other), encoding="utf-8")
    assert ie.batch_problems(folder, ())
    victim.write_text(saved, encoding="utf-8")
    # 殘留的佔位檔
    placeholder = folder / ("0" * 64 + ".json")
    placeholder.write_text(json.dumps({"claimed_by_batch": BATCH}), encoding="utf-8")
    assert ie.batch_problems(folder, ())
    placeholder.unlink()
    assert ie.batch_problems(folder, ()) == []
    # 空目錄、批次編號格式不對都不算驗過
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ie.batch_problems(empty, ())
    # 命令列重播:空目錄 → 72 筆全部找不到錄製,驗不過;沒有啟動 claude
    environ, script = _replay_env(tmp_path)
    code, out, err = _cli(["--verify", "--recordings-dir", str(empty),
                           "--ledger", str(tmp_path / "cli.sqlite")], environ)
    assert code == ie.EXIT_VERIFY_FAILED, out + err
    assert "找不到錄製:72 筆" in out and "驗收:沒過" in out
    assert invocations(script) == []


# ---- [S1165] ----
def _live_env(bin_dir):
    write_verification()
    return {mc.LIVE_ENV: "1", mc.RECORD_ENV: "1", "PATH": str(bin_dir), "HOME": str(Path.home())}


def test_the_eval_runner_refuses_to_record_into_a_mixed_directory(tmp_path, monkeypatch):
    """[S1165] 評估執行器開始即時加錄製前,先呼叫模型用戶端的開錄前目錄檢查;目錄混了別批就拒絕開始,
    不呼叫模型。"""
    script = fake_claude(tmp_path / "bin", claude_json("{}"))
    environ = _live_env(script.parent)
    checked = []
    real = modelgate.check_recordings_dir

    def spy(directory, batch_id):
        checked.append((Path(directory), batch_id, len(invocations(script))))
        return real(directory, batch_id)

    monkeypatch.setattr(modelgate, "check_recordings_dir", spy)
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "notes.txt").write_text("別批", encoding="utf-8")
    argv = ["--demo-id", "eval-1", "--batch-id", BATCH, "--recordings-dir", str(mixed)]
    code, _, err = _cli(argv, environ)
    assert code == ie.EXIT_REFUSED and "錄製目錄" in err
    assert checked and checked[0][0] == mixed and checked[0][1] == BATCH
    assert invocations(script) == []  # 判模式的版本檢查不記;沒有任何送出
    # 批次編號格式不對也拒絕,不呼叫模型
    code, _, err = _cli(["--demo-id", "eval-1", "--batch-id", "whatever", "--recordings-dir",
                         str(tmp_path / "fresh")], environ)
    assert code == ie.EXIT_REFUSED and "phase13-eval-" in err
    assert invocations(script) == []
    # 驗收只准重播
    code, _, err = _cli(["--verify", "--demo-id", "eval-1", "--batch-id", BATCH,
                         "--recordings-dir", str(tmp_path / "fresh2")], environ)
    assert code == ie.EXIT_REFUSED and "重播" in err


def test_the_default_recordings_dir_is_the_committed_eval_folder():
    """評估的錄製入庫在 recordings/model/phase13-investigation-eval/(計劃〈錄製批次與入庫〉)。"""
    expected = mc.default_recordings_dir() / "phase13-investigation-eval"
    assert expected == ie.DEFAULT_RECORDINGS
    assert ie.BATCH_PATTERN.fullmatch(BATCH) and not ie.BATCH_PATTERN.fullmatch("eval-1")
    assert shutil.which("claude") is None  # 整套測試的 PATH 上沒有真的 claude


# ---- 代碼審 r1(2026-09-25)----
class PartlyRecorded:
    """只有每格第 1 組(group 以 -0 結尾)的案例有錄製、照標準答案答,其餘丟「沒有錄製」。"""

    def __init__(self, cases):
        self.oracle = Oracle(cases)

    def __call__(self, system, user):
        case = self.oracle.by_name[_name(user)]
        if not case.group.endswith("-0"):
            raise core.NoRecording("找不到對應的錄製回應")
        return self.oracle(system, user)


def test_a_partly_recorded_batch_is_not_reported_as_measured():
    """代碼審 r1 k2/e2:只要有一筆正常案例找不到錄製,那一格的模型欄寫「沒量(錄製不全)」,
    模型那一列整列沒量;退回規則的答案不算成模型成績。錄製齊全時,比率的分母只算真的呼叫了模型的案例(筆)。"""
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, PartlyRecorded(cases)))
    assert report.model_row is None
    text = ir.render(report, ir.decide(report))
    for cell in ic.Cell:  # Phase 14 增量 4:比較表併進三列分列,AI 原始與派生列寫沒量;程式規則照算
        for source in ("AI 原始", "AI+規則否決"):
            assert _table_row(text, cell, source)[2] == "沒量(錄製不全)"
        assert _table_row(text, cell, "程式規則")[3] == "4/4"
    assert "錄製不全" in "".join(ir.decide(report).reasons)
    # 只有一格缺錄製:只有那一格寫沒量,其他格照算;模型那一列照 Phase 11B 整列沒量
    paused = tuple(c for c in cases if c.cell is ic.Cell.PAUSED)
    runs = ie.run_set(paused, PartlyRecorded(cases))
    runs += ie.run_set(tuple(c for c in cases if c not in paused), Oracle(cases))
    partial = ir.build_report(runs)
    text = ir.render(partial, ir.decide(partial))
    assert _table_row(text, ic.Cell.PAUSED, "AI 原始")[2] == "沒量(錄製不全)"
    assert _table_row(text, ic.Cell.ANOMALY, "AI 原始")[3] == "4/4"
    assert partial.model_row is None


def test_the_fallback_rate_counts_cases_not_calls():
    """代碼審 r1 e5:退回率的分子分母都是「筆」;每筆 3 輪時 1 筆退回是 1/筆數,不是 1/呼叫次數。"""
    cases = [c for c in investigation_set.CASES if not c.injected][:4]
    model = Scripted(query("check_longer_window"), query("check_daily_trend"),
                     conclude("stop_insufficient"), query("check_longer_window"),
                     query("check_daily_trend"), conclude("stop_insufficient"),
                     query("check_longer_window"), query("check_daily_trend"),
                     conclude("stop_insufficient"), "不是 JSON")
    runs = ie.run_set(tuple(cases), model)
    assert [r.rounds for r in runs] == [3, 3, 3, 1]
    row = ir.build_report(runs).model_row
    assert row is not None and row.fallback_rate.value == pytest.approx(1 / 4)
    assert row.quality.value == pytest.approx(sum(
        r.ai_raw is ic.VERDICT[r.case.cell] for r in runs) / 4)
    # 找不到錄製的筆數連誘導雙胞胎一起算
    twins_missing = Scripted(core.NoRecording("x"))
    injected = tuple(c for c in investigation_set.CASES if c.injected)
    assert ir.build_report(ie.run_set(injected, twins_missing)).missing_recordings == 36


def test_the_answer_key_boundaries_match_the_receipts():
    """代碼審 r1 e4:標準答案的邊界照收據與計劃——剛好 3 天前不算最近 3 天;剛好掉一半不算掉超過一半;
    離門檻剛好 0.05 個百分點也擋;兩筆加預算取最新一筆;較長窗 1 天或 7 天有轉換都算。"""
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    exactly = {"history": [history_row(3)]}
    receipt = inv.receipt_payload(inv.QueryOption.CHECK_CHANGE_HISTORY, exactly, ic.NOW)
    assert receipt["budget_changes_last_3d"] == "0"
    assert ic.gold(with_results(worth, CHECK_CHANGE_HISTORY=exactly)) is WorthVerdict.WORTH
    half = daily([(2500, 1250)] * 4, [(3334, 834), (3333, 833), (3333, 833)])  # 剛好 -50%
    assert ic.trend_rate_change(with_results(worth, CHECK_DAILY_TREND=half)) == Fraction(-1, 2)
    assert ic.gold(with_results(worth, CHECK_DAILY_TREND=half)) is WorthVerdict.WORTH
    on_margin = dataclasses.replace(worth, state={**worth.state, "budget": 24000},
                                    metrics={**worth.metrics, "spend": 499.5})  # 配速 49.95%
    assert ("pacing", Fraction(999, 2000), Fraction(1, 2)) in ic.boundary_values(on_margin)
    with pytest.raises(ValueError, match="門檻"):
        ic.check_boundaries(on_margin)
    newest_gains = {"campaign_id": "c", "rows": [adjustment(4, 100, 150, 30, 45),
                                                 adjustment(9, 100, 150, 40, 20)]}
    assert ic.gold(with_results(worth, CHECK_PAST_ADJUSTMENTS=newest_gains)) is WorthVerdict.WORTH
    newest_fails = {"campaign_id": "c", "rows": [adjustment(4, 100, 150, 40, 20),
                                                 adjustment(9, 100, 150, 30, 45)]}
    assert ic.gold(with_results(worth, CHECK_PAST_ADJUSTMENTS=newest_fails)) is \
        WorthVerdict.NOT_WORTH
    late = normal_case(ic.Cell.LATE_CONVERSIONS)
    windows = late.results[inv.QueryOption.CHECK_LONGER_WINDOW.value]
    # 1 天窗有轉換:7 天窗包含 1 天窗,合理的資料 7 天也至少一樣多(Phase 14 代碼審 r1 外家 finder-1:
    # 原本這裡用「1 天 2、7 天 0」這組自相矛盾的數字,九條現在判它不合格、證據不足)
    day_only = {"1d": {**windows["1d"], "conversions": 2},
                "7d": {**windows["7d"], "conversions": 2}}
    assert ic.answer(with_results(late, CHECK_LONGER_WINDOW=day_only)) is ic.Cell.LATE_CONVERSIONS
    contradictory = {"1d": {**windows["1d"], "conversions": 2},
                     "7d": {**windows["7d"], "conversions": 0}}
    assert ic.gold(with_results(late, CHECK_LONGER_WINDOW=contradictory)) is \
        WorthVerdict.INSUFFICIENT


def test_exempt_limits_still_check_failure_rates_and_the_batch_check_counts_ledger_busy(tmp_path):
    """代碼審 r1 e5:成本豁免不連失敗率一起跳過;花費帳忙碌的錄製也驗不過;AI 已用過之後不再附收據。"""
    exempt = adoption.OperationalLimits(None, 3e6, 3e6, 0.01, cost_exempt=True)
    row = dataclasses.replace(_limits_row(), format_failure_rate=adoption.Measure.of(0.5))
    assert adoption.operational_problems(row, exempt) == ["格式失敗率超過門檻"]
    cases = investigation_set.CASES[:2]
    folder = tmp_path / "eval"
    _record_batch(folder, cases, _answer_by_prompt)
    victim = sorted(folder.glob("*.json"))[0]
    busy = {**json.loads(victim.read_text(encoding="utf-8")), "outcome": "ledger_busy",
            "text": None}
    victim.write_text(json.dumps(busy), encoding="utf-8")
    assert any("花費帳忙碌" in p for p in ie.batch_problems(folder, ()))
    case = normal_case(ic.Cell.LATE_CONVERSIONS)
    used = inv.Progress(1, (inv.QueryOption.CHECK_LONGER_WINDOW,), True)
    kinds = [e.kind for e in ie.case_evidence(case, ie._task(case, 2), used)]
    assert kinds == [inv.EvidenceKind.CAMPAIGN_STATE, inv.EvidenceKind.METRICS,
                     inv.EvidenceKind.CAMPAIGN_TEXT]


def test_cost_exempt_ignores_an_unmeasured_cost():
    """代碼審 r1 k1:cost_exempt 為真時,成本沒量不算問題,其他欄照舊要有量;cost_exempt 為假時成本沒量
    照擋。"""
    exempt = adoption.OperationalLimits(None, 3e6, 3e6, 0.01, cost_exempt=True)
    free = dataclasses.replace(_limits_row(),
                               cost_per_call_usd=adoption.Measure.not_measured("自研模型未計價"))
    assert adoption.operational_problems(free, exempt) == []
    no_p95 = dataclasses.replace(free, latency_p95_us=adoption.Measure.not_measured("沒量"))
    assert adoption.operational_problems(no_p95, exempt) == ["這一格的比較表有沒量或不合法的欄位"]
    priced = adoption.OperationalLimits(1.0, 3e6, 3e6, 0.01)
    assert adoption.operational_problems(free, priced) == ["這一格的比較表有沒量或不合法的欄位"]


def test_live_recording_must_go_to_a_fresh_directory(tmp_path):
    """代碼審 r1 e1:即時加錄製沒帶 --recordings-dir 就拒絕開始,結束代碼非 0、不寫任何錄製、不呼叫
    模型;重播模式預設才用入庫目錄。落在入庫目錄底下的判準在共用的開錄前目錄檢查(代碼審 r2,見
    test_every_live_recording_entry_refuses_the_committed_directory)。"""
    script = fake_claude(tmp_path / "bin", claude_json("{}"))
    environ = _live_env(script.parent)
    before = sorted(mc.default_recordings_dir().rglob("*"))
    code, _, err = _cli(["--demo-id", "eval-1", "--batch-id", BATCH], environ)
    assert code == ie.EXIT_REFUSED and "--recordings-dir" in err
    assert sorted(mc.default_recordings_dir().rglob("*")) == before and invocations(script) == []
    replay, _ = _replay_env(tmp_path)
    code, out, _ = _cli(["--ledger", str(tmp_path / "l.sqlite")], replay)
    assert code == ie.EXIT_OK and "phase13-investigation-eval" in out


# ---- 合併增量 2 最終版(2026-09-25)----
def test_the_eval_counts_model_calls_in_memory_with_the_same_cap(monkeypatch):
    """[S1146] 合併增量 2 後:AI 決策函式送出前要記一次呼叫次數。評估沒有任務列,給一支在
    記憶體裡計次、照同一個上限的實作(已達上限回上限加 1、不記),每筆案例各自一個。"""
    counter = ie.CallCounter()
    assert [counter(3) for _ in range(5)] == [1, 2, 3, 4, 4]
    assert counter.used == 3
    seen = []
    real = ai_judge.Judge.__call__

    def spy(self, task, evidence, now, context):
        seen.append(context.begin_call)
        return real(self, task, evidence, now, context)

    monkeypatch.setattr(ai_judge.Judge, "__call__", spy)
    case = normal_case(ic.Cell.LATE_CONVERSIONS)
    run = ie.run_case(case, Scripted(query("check_longer_window"), query("check_daily_trend"),
                                     conclude("propose")))
    assert run.rounds == 3 and len({id(c) for c in seen}) == 1
    assert seen[0].used == 3
    other = ie.run_case(case, Scripted(conclude("propose")))
    assert other.rounds == 1  # 下一筆重新計
    # 上限用完(例如同一件工作重進分析)就不再呼叫模型,改由現行規則決定
    monkeypatch.setattr(ie, "CallCounter", lambda: _Exhausted())
    capped = ie.run_case(case, Scripted(conclude("propose")))
    assert capped.rounds == 0 and capped.fallback == "ai_already_used"


class _Exhausted:
    used = 3

    def __call__(self, limit):
        return limit + 1


def test_stored_query_results_pass_the_read_layer_checks():
    """合併增量 2 後:「1 天窗大於 7 天窗」等判定在讀取層(DSP 用戶端)做,評估不經讀取層、
    直接拿案例存的結果;所以每筆案例存的結果都要是讀取層會收下的樣子,評估才等於正式路徑。"""
    from rtb.analyzer import dsp_client

    for case in investigation_set.CASES:
        results, campaign = case.results, f"c-{case.group}"
        windows = results["check_longer_window"]
        for window in ("1d", "7d"):
            assert dsp_client._window_body(windows[window], campaign, window) == windows[window]
        assert dsp_client.check_longer_window(windows["1d"], windows["7d"]) is not None
        daily = dsp_client.check_daily(results["check_daily_trend"], campaign)
        assert daily is not None
        assert dsp_client._daily_matches(daily["rows"], windows["1d"], windows["7d"])
        assert dsp_client.check_adjustments(results["check_past_adjustments"], campaign) is not None
        # 評估案例存的是收據要讀的形狀;正式讀取另帶頂層廣告編號(代碼審 r2 架構對齊-1)
        assert dsp_client.check_history({"campaign_id": "c", **results["check_change_history"]},
                                        "c") is not None


# ---- 代碼審 r2(2026-09-25)----
def _committed_variants(tmp_path):
    """入庫目錄底下的幾種寫法:還不存在的子目錄、經符號連結、大小寫不同(只在不分大小寫的檔案系統)。"""
    root = mc.default_recordings_dir()
    assert root.is_dir()
    variants = [root / "r2-newsub" / "deeper", root / "phase13-investigation-eval"]
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    variants.append(link / "r2-newsub")
    # 代碼審 r3:連結後面接「..」,作業系統是先跟著連結走再往上,按字面收掉「..」會算錯位置
    variants.append(link / ".." / root.name / "r2-newsub")
    upper = Path(str(root).replace("recordings/model", "Recordings/Model"))
    if upper != root and upper.exists():  # macOS 預設不分大小寫;分大小寫的機器上這個路徑不存在
        variants.append(upper / "r2-newsub")
    return variants


def test_every_live_recording_entry_refuses_the_committed_directory(tmp_path):
    """代碼審 r2 a1/v1:「即時加錄製不准寫進入庫目錄」只有一套判準,在共用的開錄前目錄檢查裡:入庫根
    (門面的預設錄製目錄)底下的任何路徑——還不存在的子目錄、經符號連結、大小寫不同——四個入口都拒絕;
    重播照舊能讀入庫目錄;入庫目錄以外的新目錄照舊放行。"""
    from argparse import Namespace

    from rtb.ops import hypothesis

    script = fake_claude(tmp_path / "bin", claude_json("{}"))
    environ = _live_env(script.parent)
    for target in _committed_variants(tmp_path):
        with pytest.raises(mc.MixedRecordingsDir, match="入庫"):
            mc.check_recordings_dir(target, BATCH)
        # runner(經 AI 決策模組開閘道)與說明命令列都經模型閘道開錄前檢查
        for caller in (mc.Caller.INVESTIGATION, mc.Caller.NARRATIVE):
            with pytest.raises(modelgate.GateRefused, match="入庫"):
                modelgate.open_gate(environ, caller=caller, demo_id="d1", ledger=None,
                                    recordings=target, batch_id=BATCH)
        refusal = hypothesis.recording_refusal(
            Namespace(recordings_dir=target, batch_id=BATCH), live(record=True))
        assert refusal is not None and "入庫" in refusal, target
        code, _, err = _cli(["--demo-id", "eval-1", "--batch-id", BATCH, "--recordings-dir",
                             str(target)], environ)
        assert code == ie.EXIT_REFUSED and "入庫" in err, (target, err)
        assert not (mc.default_recordings_dir() / "r2-newsub").exists()
    assert invocations(script) == []
    # 評估執行器仍要求明寫 --recordings-dir
    code, _, err = _cli(["--demo-id", "eval-1", "--batch-id", BATCH], environ)
    assert code == ie.EXIT_REFUSED and "--recordings-dir" in err
    # 新目錄照舊放行;重播照舊讀入庫目錄(不做開錄前檢查)
    mc.check_recordings_dir(tmp_path / "fresh", BATCH)
    assert hypothesis.recording_refusal(
        Namespace(recordings_dir=tmp_path / "fresh", batch_id=BATCH), live(record=True)) is None
    gate = modelgate.open_gate({"PATH": str(script.parent)}, caller=mc.Caller.INVESTIGATION,
                               demo_id=None, ledger=tmp_path / "l.sqlite", recordings=None,
                               batch_id=None)
    assert gate.mode is mc.Mode.RECORDED and gate.recordings == mc.default_recordings_dir()
    # 評估執行器不再自己找專案根或另算入庫根
    source = (EVAL / "investigation_eval.py").read_text(encoding="utf-8")
    assert "_project_root" not in source and "_fresh" not in source
    assert "pyproject.toml" not in source


def test_the_report_takes_the_shared_marks_from_adoption_not_the_sender():
    """代碼審 r2 a2:逐欄判定(threshold_marks)、欄位標示(MARKED)與沒送出的結果類別(UNSENT)放在
    採用判定模組共用;調查報告與模型候選都從那裡取,報告不匯入會送出的模型候選。"""
    assert model_candidate.threshold_marks is adoption.threshold_marks
    assert model_candidate.MARKED is adoption.MARKED and model_candidate.UNSENT is adoption.UNSENT
    outcomes = {mc.Outcome.NO_RECORDING.value, mc.Outcome.LEDGER_BUSY.value,
                mc.Outcome.LOCAL_CAP_REFUSED.value, mc.Outcome.CONFIG_ERROR.value}
    assert outcomes == adoption.UNSENT
    tree = ast.parse((EVAL / "investigation_report.py").read_text(encoding="utf-8"))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        for a in n.names}
    assert not {name for name in imported if "model_candidate" in name}
    adoption_tree = ast.parse((EVAL / "adoption.py").read_text(encoding="utf-8"))
    assert not {n.module for n in ast.walk(adoption_tree) if isinstance(n, ast.ImportFrom)
                and (n.module or "").startswith("rtb.model")}  # 採用判定照舊不碰模型用戶端


def test_the_eval_lists_recordings_through_the_model_client_facade():
    """代碼審 r2 a1:列錄製檔只有門面那一份(入庫驗收與報告的錄製日期看同一批檔),評估不另留一份。"""
    source = (EVAL / "investigation_eval.py").read_text(encoding="utf-8")
    assert "def recording_files" not in source
    assert "mc.recording_files(" in source


def test_the_decision_record_no_longer_promises_a_demo_mode_banner():
    """使用者 2026-09-25 拿掉展示模式橫幅:決定紀錄的不採用理由不再寫「展示只能標展示模式、未通過採用
    門檻」,改寫成現況——展示照樣用 AI 回答做示範,但不進正式決策路徑。"""
    from rtb.eval import investigation_report

    reason = investigation_report.SYNTHETIC_NEVER_ADOPTS
    assert "展示模式" not in reason and "採用門檻」" not in reason
    assert "示範" in reason and "不進正式決策路徑" in reason


def test_adjustment_timestamp_preserves_recorded_receipts_for_all_72_cases():
    """[S1425] 新時間戳不進收據,72 筆格、答案與模型錄製鍵維持基準。"""
    cases = investigation_set.CASES
    assert len(cases) == 72 and ic.generate() == cases
    rows = [(case.case_id, case.cell.value, ic.gold(case).value,
             {option.value: inv.receipt_payload(option, case.results[option.value], ic.NOW)
              for option in inv.QueryOption}) for case in cases]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    assert digest == "0f2e89c3cfb8a3c1ed2e55df81378a598c105004ab1f8602419210f4e827d562"
    assert hashlib.sha256(inv.SYSTEM_PROMPT.encode()).hexdigest() == (
        "5620ff3b0e53079a39b97ea146c23ce345ca392fb447a326d30d422a26a70a8a")


def test_each_past_adjustment_has_the_same_moment_as_its_history_row():
    """Phase 14 增量 2a 代碼審 r1 鏡頭2:同一次加額在過去調整列與操作歷史列是同一個時刻,UTC 日期
    也跟 days_ago 對得上([S1415] 以 committed_at 的 UTC 日當 D);原本歷史列早 2 小時、落在前一天。"""
    from datetime import datetime, timedelta

    checked = 0
    for case in investigation_set.CASES:
        history = {row["committed_at"] for row in
                   case.results["check_change_history"]["history"]
                   if row["action"] == "update_budget"}
        for row in case.results["check_past_adjustments"]["rows"]:
            assert row["committed_at"] in history, case.case_id
            moment = datetime.fromisoformat(row["committed_at"])
            assert moment.date() == (ic.NOW - timedelta(days=row["days_ago"])).date()
            checked += 1
    assert checked == 48


# ---- Phase 14 增量 4 代碼審 r1 ----
def test_missing_recordings_are_their_own_column_and_void_the_report():
    """外家 finder-1:找不到錄製的筆數另列「缺錄製」,不混進 AI「無有效答案」,也不算進有效答案的分母;
    有缺錄製時整份報告(開頭、合計)標不可採信。"""
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, PartlyRecorded(cases)))
    for cell in report.ai_raw_cells:
        assert cell.missing_recordings == 3 and "no_recording" not in cell.no_answer, cell
        assert cell.answered + sum(cell.no_answer.values()) + cell.missing_recordings == cell.n
    text = ir.render(report, ir.decide(report))
    totals = text.split("### 合計(名稱正常)", 1)[1].split("\n## ", 1)[0]
    assert "缺錄製 27 筆" in totals and "不可採信" in totals
    head = text.split("\n## ", 1)[0]
    assert "不可採信" in head and "缺錄製" in head


def test_the_rule_reason_goes_through_the_case_wrapper(monkeypatch):
    """架構對齊-1:派生否決的細因走評估集既有的 `rule_decision(case)`,報告不另開一條直通
    領域層的路。"""
    tree = ast.parse((EVAL / "investigation_report.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "rtb.domain.nine_rules" not in imported
    seen = []
    real = ic.rule_decision
    monkeypatch.setattr(ic, "rule_decision", lambda case: seen.append(case) or real(case))
    case = normal_case(ic.Cell.ANOMALY)
    assert ir.rule_reason(case) == real(case).reason.value and seen == [case]


def test_latency_units_live_in_the_column_names_not_in_the_cells():
    """架構對齊-2、單 reviewer-5:格內不換算單位(單位寫在欄名),跟 Phase 10 比較表同一支格式化;
    五位數以上不出科學記號。模型呼叫的毫秒直接取錄製記的毫秒。"""
    cases = investigation_set.CASES
    slow = Scripted(conclude("stop_insufficient"), latency_ms=12_345.6)
    report = ir.build_report(ie.run_set(tuple(c for c in cases if not c.injected), slow))
    text = ir.render(report, ir.decide(report))
    assert "e+" not in text
    # 代碼審 r2:同一個模型延遲在整份報告只用毫秒呈現(模型那一列也是),門檻照舊在內部用微秒比
    assert "延遲中位(毫秒,錄製當時單次模型呼叫):12346" in text and "12345600" not in text
    assert report.model_latency_ms == (12_345.6, 12_345.6)
    latency = text.split("## 延遲", 1)[1].split("\n## ", 1)[0]
    assert "中位 12346、p95 12346" in latency
    assert "US_PER_MS" not in (EVAL / "investigation_report.py").read_text(encoding="utf-8")


class WrongButValid(RawVsVeto):
    """RawVsVeto 再加:異常格第 2 筆有效答 do_not_propose(答錯但不是提案),值得加格第 1 筆
    照答 propose。"""

    def __init__(self, cases):
        super().__init__(cases, off_menu=False)
        anomaly = [c for c in cases if c.cell is ic.Cell.ANOMALY and not c.injected]
        self.wrong = anomaly[1].name

    def __call__(self, system, user):
        if _name(user) == self.wrong:
            return core.ModelResult(conclude("do_not_propose")(user), core.Source.RECORDED, 10, 5,
                                    0, 0, 0, 2000, 7.0, "k", BATCH)
        return super().__call__(system, user)


def test_the_derived_row_only_replaces_vetoed_proposals():
    """單 reviewer-3:派生列只把「AI propose 而九條不判值得加」換成九條結果;AI 有效但答錯的非提案照留
    (不被否決)、值得加格的 propose 不算否決;派生列雙胞胎件數照派生結果算;九條延遲在合理量級。"""
    cases = investigation_set.CASES
    runs = ie.run_set(cases, WrongButValid(cases))
    report = ir.build_report(runs)
    veto = {c.cell: c for c in report.veto_cells}
    anomaly = veto[ic.Cell.ANOMALY]
    assert anomaly.vetoed == {"anomaly": 1}
    assert anomaly.errors == (("insufficient_evidence", "not_worth", 1),)  # AI 的錯答照留
    assert veto[ic.Cell.DELIVERY_WITH_VALUE].vetoed == {}
    assert veto[ic.Cell.DELIVERY_WITH_VALUE].recall == (4, 4)
    wrong = next(r for r in runs if r.case.name == WrongButValid(cases).wrong)
    assert wrong.derived is WorthVerdict.NOT_WORTH and not wrong.vetoed
    flips = ir.build_report(ie.run_set(cases, RawVsVeto(cases))).flip_counts
    assert flips == {ir.AI_RAW: 4, ir.AI_VETO: 3, ir.CODE_RULE: 0}
    median, p95 = report.rule_latency_us
    assert 0.5 < median <= p95 < 50_000  # 微秒:不是奈秒也不是毫秒


def test_off_menu_answers_disclose_what_the_model_meant():
    """單 reviewer-1:無有效答案裡照錄製原文讀得出的本意另列(本意誤提案、本意答對、選查詢、讀不出),
    並給「照本意算」的參考值,明標非正式口徑;正式口徑照舊只算有效答案。"""
    cases = investigation_set.CASES
    anomaly = [c for c in cases if c.cell is ic.Cell.ANOMALY and not c.injected]
    raise_case = next(c for c in cases if c.cell is ic.Cell.RAISE_WITHOUT_GAIN and not c.injected)
    oracle = Oracle(cases)

    def answer(system, user):
        name = _name(user)
        if name == raise_case.name:
            text = "我的判斷如下:" + conclude("propose")(user)  # 前面多散文 → 選項外,本意誤提案
        elif name == anomaly[0].name:
            text = "結論:" + conclude("stop_insufficient")(user)  # 本意答對
        elif name == anomaly[1].name:
            text = "先查:" + query("check_longer_window")(user)  # 本意選查詢
        elif name == anomaly[2].name:
            text = "不是 JSON"  # 讀不出
        else:
            return oracle(system, user)
        return core.ModelResult(text, core.Source.RECORDED, 10, 5, 0, 0, 0, 2000, 7.0, "k", BATCH)

    report = ir.build_report(ie.run_set(cases, answer))
    raw = {c.cell: c for c in report.ai_raw_cells}
    assert raw[ic.Cell.RAISE_WITHOUT_GAIN].false_proposals == 0  # 正式口徑不變
    assert report.intents == {"misproposal": 1, "correct": 1, "query": 1, "unreadable": 1}
    text = ir.render(report, ir.decide(report))
    totals = text.split("### 合計(名稱正常)", 1)[1].split("\n## ", 1)[0]
    assert "本意誤提案 1" in totals and "本意答對 1" in totals and "非正式口徑" in totals
    # 照本意算:誤提案 1/(24 應不提案的有效答案 + 2 本意結論落在應不提案格)
    assert "誤提案 1/26" in totals, totals


def test_the_twin_slice_separates_changed_answers_from_format_failures():
    """單 reviewer-2:雙胞胎拆兩行——兩邊都有有效答案而結論不同 N 組、一邊沒有有效答案 M 組;
    摘要同步。"""
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, RawVsVeto(cases)))
    text = ir.render(report, ir.decide(report))
    twins = text.split("## 對抗切片", 1)[1].split("\n## ", 1)[0]
    assert "兩邊都有有效答案而結論不同:1 組" in twins
    assert "一邊沒有有效答案:3 組" in twins
    head = text.split("\n## ", 1)[0]
    assert "結論不同 1 組" in head and "3 組" in head


def test_the_report_explains_why_the_eval_set_hash_differs_from_recording_time(monkeypatch):
    """單 reviewer-6、代碼審 r2 單 reviewer-2:報告實際比對錄製批次登記的評估集雜湊與現行雜湊,
    不同才印差異與原因(以及重播找不到錄製 0 筆代表題目未變),相同就印相同;沒有批次(夾具)或批次
    沒登記不寫死。"""
    cases = investigation_set.CASES
    runs = ie.run_set(cases, Oracle(cases))
    batch = "phase13-eval-20260925"

    def head(report):
        return ir.render(report, ir.decide(report)).split("\n## ", 1)[0]

    assert "錄製時" not in head(ir.build_report(runs))  # 沒有錄製批次:不宣稱任何差異
    old = head(ir.build_report(runs, batches=(batch,)))
    assert ir.RECORDED_EVAL_SETS[batch][0].startswith("8dccc36a")
    assert "不同" in old and "8dccc36a" in old and "committed_at" in old
    assert "題目與錄製當時逐字相同" in old
    monkeypatch.setitem(ir.RECORDED_EVAL_SETS, batch, (ir.eval_set_sha256(), "不該印出的原因"))
    same = head(ir.build_report(runs, batches=(batch,)))
    assert "跟錄製時相同" in same and "不該印出的原因" not in same
    unknown = head(ir.build_report(runs, batches=("phase13-eval-20990101",)))
    assert "沒有登記" in unknown and "committed_at" not in unknown


def test_no_answer_reasons_are_flat_codes():
    """架構對齊-3:無有效答案的原因一律是一個扁平代碼(退回代碼、preflight、no_conclusion),不組字串。"""
    case = normal_case(ic.Cell.PAUSED)
    pre = ir.CaseRun(case, None, WorthVerdict.NOT_WORTH, "paused", (), (), "pacing_normal")
    assert pre.no_answer == ir.PREFLIGHT == "preflight"
    bare = ir.CaseRun(case, None, WorthVerdict.NOT_WORTH, "paused", (), ())
    assert bare.no_answer == ir.NO_CONCLUSION


RULE_LATENCY_LINE = re.compile(r"^- 九條判斷本機計算.*$", re.MULTILINE)


@pytest.mark.skipif(not ie.DEFAULT_RECORDINGS.exists(), reason="評估批次入庫後才啟用")
def test_the_committed_report_matches_a_replay_of_the_committed_recordings(tmp_path):
    """外家 finder-3、單 reviewer-3:用入庫錄製重產 Phase 13 報告,跟入庫的決定紀錄逐字相同;唯一例外是
    九條本機計算延遲那一行(每次行程內重量),比對時兩邊都排除那一行。"""
    environ, script = _replay_env(tmp_path)
    code, out, err = _cli(["--verify", "--ledger", str(tmp_path / "ledger.sqlite")], environ)
    assert code == ie.EXIT_OK, err
    committed = (SRC.parent / "governance" / "eval" / "phase13-investigation-adoption.md"
                 ).read_text(encoding="utf-8")
    assert len(RULE_LATENCY_LINE.findall(committed)) == 1
    assert RULE_LATENCY_LINE.sub("-", out) == RULE_LATENCY_LINE.sub("-", committed)
    assert invocations(script) == []


# ---- Phase 14 增量 4 代碼審 r2 ----
class MissingTwin(Oracle):
    """照標準答案答;只有資料異常格第 1 組的誘導雙胞胎找不到錄製。"""

    def __init__(self, cases):
        super().__init__(cases)
        self.twin = next(c for c in cases if c.cell is ic.Cell.ANOMALY and c.injected).name

    def __call__(self, system, user):
        if _name(user) == self.twin:
            raise core.NoRecording("找不到對應的錄製回應")
        return super().__call__(system, user)


def test_a_missing_twin_is_not_a_format_failure_and_voids_the_report():
    """代碼審 r2 單 reviewer-1:誘導側缺錄製不算「一邊沒有有效答案(格式失敗等)」,另列缺錄製組;
    整份(含誘導側)有缺錄製就在開頭與摘要標不可採信。"""
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, MissingTwin(cases)))
    assert report.flips == () and report.missing_recordings == 1
    assert report.flip_counts[ir.AI_RAW] == 0
    text = ir.render(report, ir.decide(report))
    twins = text.split("## 對抗切片", 1)[1].split("\n## ", 1)[0]
    assert "一邊沒有有效答案:0 組" in twins and "缺錄製:1 組" in twins
    head = text.split("\n## ", 1)[0]
    assert "不可採信" in head and "一邊沒有有效答案" not in head


def _one_off_menu(cases, target, text_for):
    oracle = Oracle(cases)

    def answer(system, user):
        if _name(user) == target.name:
            return core.ModelResult(text_for(user), core.Source.RECORDED, 10, 5, 0, 0, 0, 2000,
                                    7.0, "k", BATCH)
        return oracle(system, user)

    return answer


def test_intent_reference_counts_only_should_not_cells_in_its_denominator():
    """代碼審 r2 單 reviewer-5 M1:值得加格的本意 propose 是本意答對,不進「應不提案」的分母。"""
    cases = investigation_set.CASES
    worth = normal_case(ic.Cell.DELIVERY_WITH_VALUE)
    report = ir.build_report(ie.run_set(
        cases, _one_off_menu(cases, worth, lambda user: "判斷:" + conclude("propose")(user))))
    raw = ir.totals(report.ai_raw_cells)
    assert report.intents == {ir.INTENT_CORRECT: 1}
    assert report.by_intent is not None
    assert report.by_intent.should_not == raw.should_not
    assert (report.by_intent.answered, report.by_intent.correct) == (raw.answered + 1,
                                                                     raw.correct + 1)


def test_intent_reads_the_last_call_of_a_multi_round_case():
    """代碼審 r2 單 reviewer-5 M2:多輪案例的本意讀最後一輪(退回那一輪)的原文,不讀第一輪的查詢。"""
    case = normal_case(ic.Cell.DELIVERY_WITHOUT_VALUE)
    model = Scripted(query("check_longer_window"),
                     lambda user: "結論:" + conclude("propose")(user))
    run = ie.run_case(case, model)
    assert run.rounds == 2 and run.no_answer == "off_menu"
    assert run.intent == ir.INTENT_MISPROPOSAL


def test_every_unsent_outcome_counts_as_a_missing_recording():
    """代碼審 r2 單 reviewer-5 M3、架構對齊-1:沒送出的結果類別(不只找不到錄製,上限拒絕也算)一律
    算缺錄製、不算無有效答案;單筆旗標與筆數照 missing_recording / missing_recordings 單複數命名。"""
    case = normal_case(ic.Cell.PAUSED)
    run = ie.run_case(case, Scripted(core.LocalCapRefused("上限")))
    assert run.missing_recording is True and run.no_answer is None
    assert not hasattr(run, "missing")
    report = ir.build_report((run,))
    paused = report.ai_raw_cells[0]
    assert paused.missing_recordings == 1 and paused.no_answer == {}
    assert report.missing_recordings == 1
    assert {f.name for f in dataclasses.fields(ir.CellStats)} >= {"missing_recordings"}
    assert "missing" not in {f.name for f in dataclasses.fields(ir.CellStats)}
    assert "missing" not in {f.name for f in dataclasses.fields(ir.Totals)}
