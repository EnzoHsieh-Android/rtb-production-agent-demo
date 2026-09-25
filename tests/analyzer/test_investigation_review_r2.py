"""Phase 13 增量 2 代碼審 r2 的修正(governance/review-reports/code-phase13-inc2/r2-*):每一條一支現場
成立、修之前會紅的測試。用假模型與假 DSP,不碰真模型。"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from rtb.analyzer import ai_judge, dsp_client, flow, instrumented
from rtb.analyzer import investigation as inv
from rtb.analyzer.task_store import InvestigationRecord, TaskRow, TaskStore
from rtb.domain import metrics as m
from rtb.domain.evidence import quoted_untrusted
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting
from tests.analyzer.test_ai_judge import (
    CITE_BASE,
    TASK,
    Calls,
    Model,
    Renew,
    base_evidence,
    reply,
)
from tests.analyzer.test_investigation_reads import scripted  # noqa: F401 - 假 DSP 夾具
from tests.analyzer.test_investigation_review import DAILY_ROW, _BusyCommits, _daily

LATER = NOW + timedelta(seconds=40)


def _to_analyzing(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW)
    flow.advance(store, "t1", Counting(returns=base_evidence()), Counting(), Counting(), NOW)
    assert store.latest("t1").state is TaskState.ANALYZING


def _ai(store, judge, at=NOW):
    return flow.advance(store, "t1", Counting(), Counting(), Counting(), at, owner="w1",
                        ai_decide=judge, clock=lambda: LATER)


# ---- x1/y2/v1:寫不進資料庫的模型字串一律是選項外答案 ----
@pytest.mark.parametrize("reason", ["\ud800 理由", "私用", "未指定\U000e0080x", "\udfff"])
def test_a_reason_that_cannot_be_stored_is_off_menu_on_the_first_call(tmp_path, reason):
    store = TaskStore(tmp_path / "a.db")
    try:
        _to_analyzing(store)
        model = Model(json.dumps({"choice": "do_not_propose", "reason": reason,
                                  "evidence": [{"ref": "base", "field": "conversions",
                                                "value": "1"}]}))
        assert _ai(store, ai_judge.Judge(model)) is TaskState.PROPOSED  # 退回規則:有價值格提案
        assert len(model.sent) == 1
        [(_seq, record)] = store.investigation_rounds("t1")
        assert (record.kind, record.fallback) == ("fallback", "off_menu")
    finally:
        store.close()


# ---- y1:收據的加總與平均精確算,不經浮點 ----
def test_huge_json_integers_in_the_daily_rows_never_stall_the_evidence_step(scripted):  # noqa: F811
    state = {"id": "c1", "budget": 100, "status": "active", "version": 1, "name": "n",
             "tenant": "t", "extra": 1}
    metrics = {"campaign_id": "c1", "window": "1h", "impressions": 500, "clicks": 12,
               "conversions": 1, "spend": 0.5, "revenue": 5.0}
    rows = [{} for _ in range(7)]
    rows[3]["spend"] = rows[4]["spend"] = 10**308
    scripted.answers.update({
        "/campaigns/c1": (200, json.dumps(state).encode()),
        "/campaigns/c1/metrics": (200, json.dumps(metrics).encode()),
        "/campaigns/c1/daily": (200, json.dumps(_daily(rows)).encode()),
    })
    store = TaskStore.__new__(TaskStore)  # 不用:下面用真的暫存資料庫
    del store
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as folder:
        tasks = TaskStore(Path(folder) / "a.db")
        try:
            now = datetime.now(UTC)
            tasks.create_task("t1", "c1", now)
            tasks.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, now,
                              investigation=InvestigationRecord("query", 1, "check_daily_trend",
                                                                "ai"))
            source = instrumented.investigation_source(
                tasks, f"http://127.0.0.1:{scripted.server_address[1]}", 2.0)
            assert flow.advance(tasks, "t1", source, Counting(), Counting(), now) \
                is TaskState.ANALYZING
        finally:
            tasks.close()
    assert inv._sum([{"spend": 10**308}, {"spend": 10**308}, {"spend": 1.0}], "spend") == \
        2 * 10**308 + 1


def test_the_receipt_fallback_also_catches_arithmetic_errors(monkeypatch):
    def overflow(*_args):
        raise OverflowError("int too large to convert to float")

    monkeypatch.setattr(inv, "receipt_payload", overflow)
    evidence = instrumented.receipt_or_invalid(
        "t1", 2, inv.QueryOption.CHECK_DAILY_TREND, dsp_client.QueryRead(_daily([{}] * 7)), NOW)
    assert dict(evidence.payload)["reason"] == "invalid"


# ---- x2/y3:資料區的跳脫對 BMP 以外的字寫成代理對,讀回來就是原名稱 ----
@pytest.mark.parametrize("name", ["a\U000e0041b", "\U0001d173", "春季\U000e0001促銷", "a\ud800b"])
def test_the_data_block_escape_reads_back_as_the_original_name(name):
    quoted = quoted_untrusted(name)
    assert json.loads(quoted) == name
    assert "\n" not in quoted


def test_two_different_names_never_escape_to_the_same_text():
    assert quoted_untrusted("" + "1") != quoted_untrusted("\U000e0041")
    assert quoted_untrusted("春季促銷") == '"春季促銷"'  # 中文照原樣


# ---- v2/y4:只有點擊多於曝光算不可能、看整段加總、只讓點擊率寫 na ----
def test_view_through_conversions_are_legal_and_every_trend_field_is_computed():
    rows = [{"impressions": 1000, "clicks": 30, "conversions": 3, "revenue": 20.0}] * 7
    rows = [dict(r) for r in rows]
    rows[4] = {"impressions": 50, "clicks": 0, "conversions": 1, "revenue": 20.0}
    payload = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND,
                                  dsp_client.check_daily(_daily(rows), "c1"), NOW)
    assert payload["conversions_change"] == "20.0"
    assert payload["revenue_change"] == "0.0"
    assert payload["click_rate_change"] == "1.7"
    assert payload["conversion_rate_change"] == "-10.0"


def test_one_impossible_day_does_not_poison_a_plausible_segment():
    rows = [{"impressions": 100, "clicks": 10}] * 7
    rows = [dict(r) for r in rows]
    rows[0] = {"impressions": 10, "clicks": 20}  # 單日點擊多於曝光,整段加總仍合理
    payload = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND,
                                  dsp_client.check_daily(_daily(rows), "c1"), NOW)
    assert payload["click_rate_change"] != "na"


def test_only_the_click_rate_is_na_when_a_segment_has_more_clicks_than_impressions():
    rows = [{"impressions": 100, "clicks": 10}] * 3 + [{"impressions": 100, "clicks": 200}] * 4
    payload = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND,
                                  dsp_client.check_daily(_daily(rows), "c1"), NOW)
    assert payload["click_rate_change"] == "na"
    for field in ("conversions_change", "revenue_change", "conversion_rate_change"):
        assert payload[field] != "na", field
    adjustment = {"days_ago": 4, "budget_before": 90, "budget_after": 100,
                  **{f"before_{k}": v for k, v in (("impressions", 10), ("clicks", 90),
                                                   ("conversions", 3), ("spend", 4.5),
                                                   ("revenue", 12.0))},
                  **{f"after_{k}": v for k, v in (("impressions", 300), ("clicks", 3),
                                                  ("conversions", 30), ("spend", 4.5),
                                                  ("revenue", 24.0))}}
    payload = inv.receipt_payload(inv.QueryOption.CHECK_PAST_ADJUSTMENTS,
                                  {"campaign_id": "c1", "rows": [adjustment]}, NOW)
    assert payload["adj1_conversions_change"] == "900.0"
    assert payload["adj1_revenue_change"] == "100.0"


def test_a_one_day_window_larger_than_the_seven_day_window_is_invalid():
    """(r3 g1:判定搬到讀取層 dsp_client,整份回應不收、記 invalid)"""
    window = {"campaign_id": "c1", "impressions": 100, "clicks": 10, "conversions": 1,
              "spend": 1.0, "revenue": 2.0}
    assert dsp_client.check_longer_window(
        {**window, "window": "1d", "impressions": 100_000}, {**window, "window": "7d"}) is None
    fine = {"1d": {**window, "window": "1d"}, "7d": {**window, "window": "7d", "clicks": 20}}
    assert dsp_client.check_longer_window(fine["1d"], fine["7d"]) == fine
    ok = inv.receipt_evidence("t1", 2, inv.QueryOption.CHECK_LONGER_WINDOW, fine, None, NOW)
    assert dict(ok.payload)["d1_conversion_rate"] == "10.0"
    clicky = {"1d": {**window, "window": "1d", "clicks": 200, "impressions": 100},
              "7d": {**window, "window": "7d", "clicks": 300, "impressions": 1000}}
    payload = inv.receipt_payload(inv.QueryOption.CHECK_LONGER_WINDOW, clicky, NOW)
    assert payload["d1_click_rate"] == "na" and payload["d7_click_rate"] == "30.0"


# ---- v3:呼叫次數在確定要呼叫模型的那一刻才記 ----
def test_stops_before_the_call_do_not_use_up_the_model_calls(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "a.db")
    try:
        _to_analyzing(store)
        stop = {"set": False}
        real = inv.prompt
        stops = {"left": 3}

        def building(*args, **kwargs):
            text = real(*args, **kwargs)
            if stops["left"] > 0:
                stops["left"] -= 1
                stop["set"] = True
            return text

        monkeypatch.setattr(inv, "prompt", building)
        model = Model(reply(["check_daily_trend"]))
        judge = ai_judge.Judge(model, stop_requested=lambda: stop["set"])
        for index in range(4):
            stop["set"] = False
            _ai(store, judge, NOW + timedelta(seconds=index))
        assert len(model.sent) == 1  # 前三次都在呼叫前停下,第四次照常問模型
        assert store.investigation_call_count("t1") == 1
        [(_seq, record)] = store.investigation_rounds("t1")
        assert record.kind == "query"
    finally:
        store.close()


def test_repaying_after_busy_commits_is_still_capped(tmp_path):
    _BusyCommits.failures = 5
    store = _BusyCommits(tmp_path / "a.db")
    try:
        _to_analyzing(store)
        model = Model(reply(["check_daily_trend"]))
        judge = ai_judge.Judge(model)
        for index in range(12):
            if store.latest("t1").state is not TaskState.ANALYZING:
                break
            try:
                _ai(store, judge, NOW + timedelta(seconds=index))
            except Exception:  # noqa: S112 - 提交忙碌:下一輪再推
                continue
        assert len(model.sent) <= inv.MAX_ROUNDS
        assert store.investigation_call_count("t1") == len(model.sent)
    finally:
        store.close()


def test_a_call_that_cannot_be_recorded_is_not_made(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "a.db")
    try:
        _to_analyzing(store)
        monkeypatch.setattr(store, "record_model_call", lambda *_a: None)
        model = Model(reply("propose", evidence=CITE_BASE))
        assert _ai(store, ai_judge.Judge(model)) is TaskState.ANALYZING
        assert model.sent == []
        assert store.investigation_rounds("t1") == ()
    finally:
        store.close()


# ---- v4:補殺存活變異 ----
def test_the_last_allowed_call_still_asks_the_model():
    model = Model(reply("propose", evidence=CITE_BASE))
    outcome = ai_judge.Judge(model)(TASK, base_evidence(), NOW,
                                    flow.AiContext(Renew(), (), Calls(start=inv.MAX_ROUNDS - 1)))
    assert len(model.sent) == 1 and outcome.record.kind == "conclusion"


def test_a_receipt_value_of_exactly_the_limit_fits_and_one_more_is_na():
    assert m._fits("1" * m.MAX_RECEIPT_CHARS) == "1" * m.MAX_RECEIPT_CHARS
    assert m._fits("1" * (m.MAX_RECEIPT_CHARS + 1)) == m.NA
    assert len(m.percent_text(m.exact_ratio(10**123, 1))) == 128
    assert m.percent_text(m.exact_ratio(10**124, 1)) == m.NA


def test_an_invalid_receipt_archives_no_raw_response(monkeypatch, scripted, tmp_path):  # noqa: F811
    real = inv.receipt_payload
    monkeypatch.setattr(inv, "receipt_payload", lambda *a: {**real(*a), "bad": "x" * 200})
    state = {"id": "c1", "budget": 100, "status": "active", "version": 1, "name": "n"}
    metrics = {"campaign_id": "c1", "window": "1h", "impressions": 500, "clicks": 12,
               "conversions": 1, "spend": 0.5, "revenue": 5.0}
    scripted.answers.update({
        "/campaigns/c1": (200, json.dumps(state).encode()),
        "/campaigns/c1/metrics": (200, json.dumps(metrics).encode()),
        "/campaigns/c1/daily": (200, json.dumps(_daily([{}] * 7)).encode()),
    })
    tasks = TaskStore(tmp_path / "a.db")
    try:
        now = datetime.now(UTC)
        tasks.create_task("t1", "c1", now)
        source = instrumented.investigation_source(
            tasks, f"http://127.0.0.1:{scripted.server_address[1]}", 2.0)
        tasks.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, now,
                          investigation=InvestigationRecord("query", 1, "check_daily_trend", "ai"))
        batch = source(tasks.latest("t1"), now)
        assert batch.raw == ()
        receipts = inv.query_receipts(batch.evidence)
        assert receipts[inv.QueryOption.CHECK_DAILY_TREND]["reason"] == "invalid"
    finally:
        tasks.close()


# ---- z2:追加查詢的呼叫紀錄記法對齊基本讀取 ----
def test_a_timed_out_query_is_logged_with_the_exception_name(monkeypatch):
    def slow(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(dsp_client, "request_json", slow)
    calls = []
    read = dsp_client.make_query_reader(
        "http://127.0.0.1:9", 1.0,
        on_call=lambda _t, endpoint, outcome, _ms: calls.append((endpoint.value, outcome)))(
        TaskRow("t1", 2, TaskState.COLLECTING_EVIDENCE, "c1", None, None, NOW),
        inv.QueryOption.CHECK_DAILY_TREND.value)
    assert read.reason == "timeout"  # 收據照樣記「逾時」
    assert calls == [("dsp:daily", "TimeoutError")]  # 呼叫紀錄跟基本讀取一樣記例外類別名


def test_the_daily_row_fixture_is_whitelisted():
    shape = {k: v for k, v in DAILY_ROW.items() if k != "days_ago"}
    assert dsp_client.check_daily(_daily([shape] * 7), "c1") is not None


def test_only_utf8_encodable_text_counts_as_storable():
    assert inv.storable("春季 理由") is True
    assert inv.storable("\ud800") is False
    assert inv.storable("a\udfffb") is False


def test_a_segment_with_more_conversions_than_clicks_still_has_a_conversion_rate():
    rows = [{"impressions": 100, "clicks": 2, "conversions": 5}] * 3 + \
        [{"impressions": 100, "clicks": 10, "conversions": 1}] * 4
    payload = inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND,
                                  dsp_client.check_daily(_daily(rows), "c1"), NOW)
    assert payload["conversion_rate_change"] == "2400.0"  # 瀏覽後轉換合法,照算


def test_a_model_call_is_not_recorded_by_a_worker_that_lost_its_lease(tmp_path):
    store = TaskStore(tmp_path / "a.db")
    try:
        store.create_task("t1", "c1", NOW)
        lease = store.acquire_lease("t1", "w1", NOW)
        assert store.record_model_call(lease, lambda: NOW, inv.MAX_ROUNDS) == 1
        store.release_lease(lease, NOW)
        assert store.record_model_call(lease, lambda: NOW, inv.MAX_ROUNDS) is None
        assert store.investigation_call_count("t1") == 1
    finally:
        store.close()
