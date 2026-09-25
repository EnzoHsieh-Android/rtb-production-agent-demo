"""Phase 13 增量 2 代碼審 r3(最後一輪,governance/review-reports/code-phase13-inc2/r3-*):
每一條一支現場成立、修之前會紅的測試。用假模型與假 DSP,不碰真模型。"""

import sqlite3
from datetime import timedelta

import pytest

from rtb import stepbudget
from rtb.analyzer import ai_judge, dsp_client, flow
from rtb.analyzer import investigation as inv
from rtb.analyzer.task_store import TaskRow, TaskStore
from rtb.domain import metrics as m
from rtb.domain.task_state import TaskState
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS
from tests.analyzer.conftest import NOW
from tests.analyzer.test_ai_judge import CITE_BASE, TASK, Model, Renew, base_evidence, reply
from tests.analyzer.test_investigation_review import _daily
from tests.analyzer.test_investigation_review_r2 import _ai, _to_analyzing

WINDOW = {"campaign_id": "c1", "impressions": 100, "clicks": 10, "conversions": 1,
          "spend": 1.0, "revenue": 2.0}


# ---- w1:記完次數、送出之前再看一次停止旗標 ----
def test_a_stop_that_arrives_while_the_call_is_being_recorded_skips_the_model_call():
    stop = {"set": False}
    recorded = []

    def begin_call(limit):
        recorded.append(limit)
        stop["set"] = True  # 記次等鎖期間收到停止
        return 1

    model = Model(reply("propose", evidence=CITE_BASE))
    judge = ai_judge.Judge(model, stop_requested=lambda: stop["set"])
    with pytest.raises(flow.RenewalSkipped):
        judge(TASK, base_evidence(), NOW, flow.AiContext(Renew(), (), begin_call))
    assert recorded == [inv.MAX_ROUNDS]  # 次數已記(設計接受的代價)
    assert model.sent == []


def test_a_stop_during_the_recording_leaves_the_task_analyzing(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "a.db")
    try:
        _to_analyzing(store)
        stop = {"set": False}
        real = store.record_model_call

        def recording(*args):
            count = real(*args)
            stop["set"] = True
            return count

        monkeypatch.setattr(store, "record_model_call", recording)
        model = Model(reply("propose", evidence=CITE_BASE))
        assert _ai(store, ai_judge.Judge(model, stop_requested=lambda: stop["set"])) \
            is TaskState.ANALYZING
        assert model.sent == []
        assert store.investigation_rounds("t1") == ()
    finally:
        store.close()


# ---- q2/m1:AI 那一步的時間預算多算一次記次等鎖 ----
def test_the_ai_step_budget_counts_the_wait_for_recording_the_call():
    assert stepbudget.ai_step_worst_seconds() == (
        stepbudget.MODEL_TIMEOUT_SECONDS
        + stepbudget.GROUP_EXIT_WAITS * stepbudget.GROUP_EXIT_WAIT_SECONDS
        + (stepbudget.LEDGER_RESERVATIONS + stepbudget.SETTLE_ATTEMPTS) * BUSY_TIMEOUT_SECONDS
        + BUSY_TIMEOUT_SECONDS  # 記次
        + BUSY_TIMEOUT_SECONDS)  # 提交
    assert stepbudget.ai_step_worst_seconds() == 55.0
    assert stepbudget.ai_stop_grace_seconds() == 60.0
    assert stepbudget.ai_step_worst_seconds() < 60  # 續租後最壞仍小於租約


# ---- q1:深巢狀的回答是選項外答案,不轉失敗 ----
def test_a_deeply_nested_choice_is_off_menu_not_a_failure(tmp_path):
    depth = 70_000
    text = ('{"choice":[' + "[" * depth + "]" * depth + '],"reason":"x","evidence":[]}')
    store = TaskStore(tmp_path / "a.db")
    try:
        _to_analyzing(store)
        model = Model(text)
        state = _ai(store, ai_judge.Judge(model))
        assert state is not TaskState.FAILED
        assert len(model.sent) == 1
        [(_seq, record)] = store.investigation_rounds("t1")
        assert (record.kind, record.fallback) == ("fallback", "off_menu")
    finally:
        store.close()


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


# ---- m2:補殺存活變異 ----
def test_a_real_write_lock_while_recording_the_call_skips_the_model(tmp_path):
    path = tmp_path / "a.db"
    store = TaskStore(path, busy_timeout_seconds=0.3)
    try:
        store.create_task("t1", "c1", NOW)
        lease = store.acquire_lease("t1", "w1", NOW)
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            assert store.record_model_call(lease, lambda: NOW, inv.MAX_ROUNDS) is None
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        assert store.investigation_call_count("t1") == 0
        assert store.record_model_call(lease, lambda: NOW, inv.MAX_ROUNDS) == 1
    finally:
        store.close()


def test_a_database_error_while_recording_the_call_is_retried_not_failed(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "a.db")
    try:
        _to_analyzing(store)

        def broken(*_args):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(store, "record_model_call", broken)
        model = Model(reply("propose", evidence=CITE_BASE))
        with pytest.raises(sqlite3.OperationalError):
            _ai(store, ai_judge.Judge(model))
        assert store.latest("t1").state is TaskState.ANALYZING
        assert model.sent == []
        monkeypatch.undo()
        assert store.acquire_lease("t1", "w9", NOW + timedelta(seconds=1)) is not None
    finally:
        store.close()


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
