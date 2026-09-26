"""Phase 13 增量 2 代碼審 r3(最後一輪,governance/review-reports/code-phase13-inc2/r3-*):
每一條一支現場成立、修之前會紅的測試。用假模型與假 DSP,不碰真模型。

Phase 14 增量 3:流程層 AI 那一步(記次期間停止 w1、AI 步時間預算 q2/m1、記次時資料庫錯誤)隨入口撤除,
那幾支刪除;深巢狀回答改成評估的呼叫方式(直接呼叫 Judge)。"""

import json

import pytest

from rtb.analyzer import ai_judge, dsp_client
from rtb.analyzer import investigation as inv
from rtb.analyzer.task_store import TaskRow
from rtb.domain import metrics as m
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW
from tests.analyzer.test_ai_judge import TASK, Calls, Model, base_evidence
from tests.analyzer.test_investigation_review import _daily

WINDOW = {"campaign_id": "c1", "impressions": 100, "clicks": 10, "conversions": 1,
          "spend": 1.0, "revenue": 2.0}


# ---- q1:深巢狀的回答是選項外答案,不轉失敗 ----
def test_a_deeply_nested_choice_is_off_menu_not_a_failure():
    depth = 70_000
    text = ('{"choice":[' + "[" * depth + "]" * depth + '],"reason":"x","evidence":[]}')
    model = Model(text)
    outcome = ai_judge.Judge(model)(TASK, base_evidence(), NOW, inv.AiContext((), Calls()))
    assert len(model.sent) == 1
    assert (outcome.record.kind, outcome.record.fallback) == ("fallback", "off_menu")


def test_a_json_nesting_too_deep_to_parse_is_off_menu(monkeypatch):
    def too_deep(_text):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(inv.json, "loads", too_deep)
    with pytest.raises(inv.OffMenu):
        inv.parse_answer("{}", ("propose",), 3, {})


@pytest.mark.parametrize("choice", [[["check_daily_trend"]], [{"a": 1}], [1, 1], [None]])
def test_query_choices_must_be_strings_before_they_are_deduplicated(choice):
    with pytest.raises(inv.OffMenu):
        inv._choice(choice, tuple(o.value for o in inv.QueryOption), 3)


# ---- m2:補殺存活變異(記次那支隨 Phase 14 增量 3 撤除 record_model_call 一起刪) ----





def test_clicks_equal_to_impressions_is_a_click_rate_of_one_hundred():
    assert m.percent_text(m.exact_click_rate(5, 5)) == "100.0"
    assert m.exact_click_rate(6, 5) is m.Reason.INVALID_DATA
    rows = [{"impressions": 100, "clicks": 100}] * 7
    payload = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND,
                                  dsp_client.check_daily(_daily(rows), "c1"), NOW)
    assert payload["click_rate_change"] == "0.0"


def test_a_negative_daily_revenue_is_na_not_a_stuck_step():
    rows = [{}] * 7
    rows = [dict(r) for r in rows]
    rows[5] = {"revenue": -3.0}
    body = dsp_client.check_daily(_daily(rows), "c1")
    assert body is not None  # 白名單的金額欄沒有下限
    evidence = inv.receipt_evidence("t1", 2, inv.QueryOption.CHECK_DAILY_TREND, body, None, NOW)
    payload = dict(evidence.payload)
    assert payload["revenue_change"] == "na"
    assert payload["conversions_change"] == "0.0"


@pytest.mark.parametrize("field", ["impressions", "clicks", "conversions", "spend", "revenue"])
def test_any_one_day_field_above_the_seven_day_window_is_invalid(field):
    day = {**WINDOW, "window": "1d", field: WINDOW[field] * 1000}  # 只有這一欄超過 7 天窗
    week = {**WINDOW, "window": "7d"}
    assert dsp_client.check_longer_window(day, week) is None, field
    assert dsp_client.check_longer_window({**WINDOW, "window": "1d"},
                                          {**WINDOW, "window": "7d"}) is not None


# ---- g1:1 天窗大於 7 天窗在讀取層判,回 QueryRead(None, "invalid") ----
def test_a_contradictory_longer_window_is_rejected_by_the_reader(monkeypatch):
    day = {**WINDOW, "window": "1d", "clicks": 50}
    week = {**WINDOW, "window": "7d"}

    def answer(url, _method, _body, _timeout, headers=None):
        assert headers is None
        return 200, (week if url.endswith("window=7d") else day)

    monkeypatch.setattr(dsp_client, "request_json", answer)
    calls = []
    read = dsp_client.make_query_reader(
        "http://127.0.0.1:9", 1.0,
        on_call=lambda _t, endpoint, outcome, _ms: calls.append((endpoint.value, outcome)))(
        TaskRow("t1", 2, TaskState.COLLECTING_EVIDENCE, "c1", None, None, NOW),
        inv.QueryOption.CHECK_LONGER_WINDOW.value)
    assert (read.raw, read.reason) == (None, "invalid")
    assert calls == [("dsp:metrics", "ok"), ("dsp:metrics", "ok")]
    assert not hasattr(inv, "contradictory")  # 判定只在讀取層一處


# ---- g2:200 但本文讀不懂,呼叫紀錄記例外類別名 ----
def test_an_unreadable_ok_response_is_logged_with_the_exception_name(monkeypatch):
    class Unreadable(ValueError):
        status = 200

    def unreadable(*_args, **_kwargs):
        raise Unreadable("not json")

    monkeypatch.setattr(dsp_client, "request_json", unreadable)
    calls = []
    read = dsp_client.make_query_reader(
        "http://127.0.0.1:9", 1.0,
        on_call=lambda _t, endpoint, outcome, _ms: calls.append((endpoint.value, outcome)))(
        TaskRow("t1", 2, TaskState.COLLECTING_EVIDENCE, "c1", None, None, NOW),
        inv.QueryOption.CHECK_DAILY_TREND.value)
    assert read.reason == "invalid"  # 收據照舊記「欄位不合格」
    assert calls == [("dsp:daily", "Unreadable")]  # 呼叫紀錄跟基本讀取一樣記例外類別名


# ---- 協調者 2026-09-25 真錄製:提示把查詢寫成「一串查詢代碼」,模型回了逗號串成的一個字串 ----
ALL_CHOICES = (*(o.value for o in inv.QueryOption), "propose", "do_not_propose",
               "stop_insufficient")


def _examples():
    return [json.loads(line) for line in inv.SYSTEM_PROMPT.splitlines()
            if line.startswith('{"choice"')]


def test_the_prompt_spells_out_the_choice_format_with_examples():
    """系統提示明寫:結論是一個字串代碼;查詢是 JSON 陣列(只選一個也是陣列),不要用逗號串在同一個
    字串裡;附兩個照得過驗證的範例(一個結論、一個兩項查詢的陣列)。"""
    prompt = inv.SYSTEM_PROMPT
    assert "一串查詢代碼" not in prompt
    rule = next(line for line in prompt.splitlines() if "JSON 陣列" in line)
    assert "字串" in rule and "只選一個" in rule and "逗號" in rule
    examples = _examples()
    conclusions = [e for e in examples if isinstance(e["choice"], str)]
    queries = [e for e in examples if isinstance(e["choice"], list)]
    assert conclusions and queries
    assert all(len(e["choice"]) >= 2 for e in queries)  # 範例示範「多個」也是陣列元素,不是逗號串
    base = {"conversions": "3", "clicks": "40"}
    for example in examples:
        answer = inv.parse_answer(json.dumps(example, ensure_ascii=False), ALL_CHOICES, 3,
                                  {"base": base})
        assert answer.conclusion is not None or len(answer.queries) >= 2, example


def test_a_comma_joined_query_string_is_still_off_menu():
    """不放寬解析:協調者真錄製拿到的回答(逗號串成一個字串)照舊判選項外答案。"""
    recorded = ('{"choice": "check_change_history,check_daily_trend", '
                '"reason": "要看調整紀錄與趨勢", "evidence": []}')
    with pytest.raises(inv.OffMenu):
        inv.parse_answer(recorded, ALL_CHOICES, 3, {})
