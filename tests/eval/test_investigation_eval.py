"""Phase 13 增量 3:AI 調查的評估案例(計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈評估案例〉
〈花費帳與採用判定〉〈錄製批次與入庫〉)。

合約 [S1117] 到 [S1119]、[S1133]、[S1140]、[S1141]、[S1146]、[S1155]、[S1159]、
[S1162] 的標準答案那半、[S1163]、[S1165]。行程內用假的模型呼叫;經評估執行器命令列跑
即時模式的測試,把假的 claude 腳本放進傳給命令列的 PATH(整套測試的 PATH 上沒有真的
claude)。不呼叫真的模型。
"""

import ast
import dataclasses
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
    lines = user.splitlines()
    return lines[lines.index("<<<資料開始") + 1]


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
    row = {"days_ago": days_ago, "budget_before": before_budget, "budget_after": after_budget}
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


# ---- [S1133] ----
def test_the_answer_key_applies_the_history_rules_in_order():  # noqa: PLR0915 - 逐條規則
    """[S1133] 標準答案的判定順序:暫停 → 資料異常(原始指標)→ 裁定 12 三條 → 裁定 8 →
    其餘 Phase 10 條;算不出的那一條不適用、往下判;捨入後的收據字串不影響判定。"""
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
    # 精確比率函式說「算不出」時,兩條門檻都不適用:答案往下掉到「有價值 → 值得加」
    monkeypatch.setattr(m, "exact_ratio", lambda _n, _d: m.Reason.MISSING_DATA)
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
    # 生成器與標準答案不讀決策規則與 AI 決策模組(比照 Phase 10 生成器);選項代碼跟調查詞彙一致
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
    for cell in report.cells:  # 誘導的反答不進逐格指標:正常案例全對
        assert cell.n == 4 and cell.class_correct == 4 and cell.false_proposals == 0
    text = ir.render(report, ir.decide(report))
    assert "結論跟名稱正常時不同:36 筆" in text
    honest = ir.build_report(ie.run_set(cases, Oracle(cases)))
    assert honest.flips == ()
    assert "結論跟名稱正常時不同:0 筆" in ir.render(honest, ir.decide(honest))


# ---- [S1117] ----
def test_the_investigation_report_is_per_slice_and_never_adopts_synthetic():
    """[S1117] 逐格只用名稱正常的案例報誤提案率、類別正確率與值得加格召回率,另報每個決策的
    輪數、原價與退回原因;合成集就算全對,結論也是不採用。"""
    cases = investigation_set.CASES
    report = ir.build_report(ie.run_set(cases, Oracle(cases)))
    assert [c.cell for c in report.cells] == list(ic.Cell)
    for cell in report.cells:
        assert cell.n == 4 and cell.class_correct == 4
        if ic.VERDICT[cell.cell] is WorthVerdict.WORTH:
            assert cell.recall == (4, 4)
        else:
            assert cell.recall is None and cell.false_proposals == 0
        assert cell.mean_rounds == 1 and cell.max_rounds == 1
        assert cell.mean_list_usd == pytest.approx(2e-6) and cell.fallbacks == {}
    decision = ir.decide(report)
    assert decision.adopt is False and decision.reasons and decision.missing_evidence
    # 現行規則實測:暫停格有投放就提案(誤提案),沒價值格判值得加
    rule = {c.cell: c for c in report.rule_cells}
    assert rule[ic.Cell.PAUSED].false_proposals == 4
    assert rule[ic.Cell.RECENT_BUDGET_CHANGE].class_correct == 0
    # 退回原因分布:有一件選項外答案、一件逾時
    first = [c for c in cases if c.cell is ic.Cell.PAUSED and not c.injected]
    mixed = Scripted("不是 JSON", core.ModelTimeout("slow"), conclude("do_not_propose"))
    runs = ie.run_set(tuple(first), mixed)
    paused = next(c for c in ir.build_report(runs).cells if c.cell is ic.Cell.PAUSED)
    assert paused.fallbacks == {"off_menu": 1, "timeout": 1}
    assert paused.n == 4
    text = ir.render(report, decision)
    for phrase in ("## 逐格結果(只算名稱正常的案例;", "誤提案", "類別正確", "召回",
                   "平均輪數", "每個決策的原價", "退回原因", "結論:不採用", "## 缺的證據",
                   "現行程式規則(實測)"):
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
    """[S1146] 評估執行器呼叫增量 2 的同一支 AI 決策函式,只把查詢來源換成案例裡存的結果、
    續租回呼換成永遠成功、先前各輪紀錄放在記憶體;輪數上限、選項驗證、證據核對與退回跟正式
    路徑相同。"""
    calls = []
    real = ai_judge.Judge.__call__

    def spy(self, task, evidence, now, context):
        calls.append((len(context.rounds), [e.kind for e in evidence]))
        context.renew()  # 永遠成功:不丟 RenewalSkipped
        return real(self, task, evidence, now, context)

    monkeypatch.setattr(ai_judge.Judge, "__call__", spy)
    case = normal_case(ic.Cell.LATE_CONVERSIONS)
    week = case.results["check_longer_window"]["7d"]
    cite_week = json.dumps({"choice": "propose", "reason": "7 天有轉換", "evidence": [
        {"ref": "check_longer_window", "field": "d7_conversions",
         "value": str(week["conversions"])}]})
    model = Scripted(query("check_longer_window", "check_daily_trend"), cite_week)
    run = ie.run_case(case, model)
    assert run.final is WorthVerdict.WORTH and run.fallback is None
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
    assert capped.final is ie.rule_verdict(case)
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
    table = text.split("## 比較表", 1)[1].split("###", 1)[0]
    for cell in ic.Cell:
        row = next(line for line in table.splitlines() if line.startswith(f"| {cell.value} |"))
        assert row.endswith("| 沒量(錄製不全) |"), row
    assert "錄製不全" in "".join(ir.decide(report).reasons)
    # 只有一格缺錄製:只有那一格寫沒量,其他格照算;模型那一列照 Phase 11B 整列沒量
    paused = tuple(c for c in cases if c.cell is ic.Cell.PAUSED)
    runs = ie.run_set(paused, PartlyRecorded(cases))
    runs += ie.run_set(tuple(c for c in cases if c not in paused), Oracle(cases))
    partial = ir.build_report(runs)
    table = ir.render(partial, ir.decide(partial)).split("## 比較表", 1)[1].split("###", 1)[0]
    lines = {line.split("|")[1].strip(): line for line in table.splitlines()
             if line.startswith("| ")}
    assert lines["paused"].endswith("| 沒量(錄製不全) |")
    assert lines["anomaly"].endswith("| 4/4 |")
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
        r.final is ic.VERDICT[r.case.cell] for r in runs) / 4)
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
    day_only = {"1d": {**windows["1d"], "conversions": 2},
                "7d": {**windows["7d"], "conversions": 0}}
    assert ic.answer(with_results(late, CHECK_LONGER_WINDOW=day_only)) is ic.Cell.LATE_CONVERSIONS


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


def test_live_recording_must_go_to_a_fresh_directory(tmp_path, monkeypatch):
    """代碼審 r1 e1:即時加錄製沒帶 --recordings-dir、或路徑落在入庫目錄(recordings/model/
    底下)就拒絕開始,結束代碼非 0、不寫任何錄製、不呼叫模型;重播模式預設才用入庫目錄。"""
    root = tmp_path / "recordings" / "model"
    monkeypatch.setattr(ie, "COMMITTED_ROOT", root)
    monkeypatch.setattr(ie, "DEFAULT_RECORDINGS", root / "phase13-investigation-eval")
    script = fake_claude(tmp_path / "bin", claude_json("{}"))
    environ = _live_env(script.parent)
    base = ["--demo-id", "eval-1", "--batch-id", BATCH]
    code, _, err = _cli(base, environ)
    assert code == ie.EXIT_REFUSED and "新的錄製目錄" in err
    code, _, err = _cli([*base, "--recordings-dir", str(root / "other")], environ)
    assert code == ie.EXIT_REFUSED and "入庫目錄" in err
    code, _, err = _cli([*base, "--recordings-dir", str(root / "x" / ".." / "y")], environ)
    assert code == ie.EXIT_REFUSED
    assert not root.exists() and invocations(script) == []
    replay, _ = _replay_env(tmp_path)
    code, out, _ = _cli(["--ledger", str(tmp_path / "l.sqlite")], replay)
    assert code == ie.EXIT_OK and "phase13-investigation-eval" in out
