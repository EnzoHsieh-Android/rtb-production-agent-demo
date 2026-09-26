"""分析端的追加查詢讀法(Phase 13 增量 2,計劃〈新增的兩種模擬資料〉〈新證據種類與現行規則隔離〉):
兩支新端點的回應格式寫死、用戶端逐列驗證;查不到或欄位不合格記成「這個查詢沒有結果」,整步不失敗([
S1148] [S1132] [S1108])。"""

import copy
import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import combinations

import pytest

from rtb import stepbudget
from rtb.analyzer import dsp_client, investigation
from rtb.analyzer.investigation import QueryOption
from rtb.analyzer.task_store import LEASE_DURATION, TaskRow
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from tests.dsp.test_investigation_data import _seeded

TASK = TaskRow("t1", 2, TaskState.COLLECTING_EVIDENCE, "c1", None, None,
               datetime(2026, 9, 25, tzinfo=UTC))


def test_every_query_combination_uses_its_declared_dsp_reads_within_the_lease(monkeypatch):
    """[S1113] 用真讀取器與假 HTTP 計數,枚舉 AI 同一步可選的所有查詢組合。"""
    rows = [{"days_ago": day, "impressions": 100, "clicks": 10, "conversions": 1,
             "spend": "1.00", "revenue": "2.00", "no_data": False}
            for day in range(1, 8)]
    calls = []

    def answer(url, _method, _body, _timeout):
        calls.append(url)
        if url.endswith("/daily"):
            return 200, {"campaign_id": "c1", "rows": rows}
        for window, factor in (("1d", 1), ("7d", 7)):
            if url.endswith(f"window={window}"):
                return 200, {"campaign_id": "c1", "window": window,
                             "impressions": 100 * factor, "clicks": 10 * factor,
                             "conversions": factor, "spend": f"{factor}.00",
                             "revenue": f"{factor * 2}.00"}
        return 404, {"error": "missing"}

    monkeypatch.setattr(dsp_client, "request_json", answer)
    reader = dsp_client.make_query_reader("http://unused", 3.0)
    for size in range(investigation.MAX_QUERIES + 1):
        for options in combinations(QueryOption, size):
            calls.clear()
            dsp_client.read_query_options(reader, TASK, tuple(option.value for option in options),
                                           TASK.written_at)
            assert len(calls) == sum(investigation.READS_PER_OPTION[option]
                                     for option in options), options
    assert stepbudget.collect_step_worst_seconds(3.0, investigation.MAX_COLLECT_READS) < (
        LEASE_DURATION.total_seconds())


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def real_dsp(tmp_path):
    store = _seeded(tmp_path, now=datetime.now(UTC))
    store.seed_campaign("c2", budget=10)  # 沒種逐日與過去調整
    store.close()
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_new_endpoints_have_a_fixed_row_shape_and_the_client_checks_every_row(real_dsp):
    """[S1148] 頂層帶廣告編號與 rows;逐日固定 7 列、缺資料那天五欄 null 並帶 no_data 真;過去調整
    最多 5 列;沒種回 404,用戶端記成 not_found;任一列多欄少欄記成 invalid。"""
    status, daily = _get(real_dsp, "/campaigns/c1/daily")
    assert status == 200 and set(daily) == {"campaign_id", "rows"} and daily["campaign_id"] == "c1"
    assert [row["days_ago"] for row in daily["rows"]] == list(range(1, 8))
    gap = daily["rows"][2]
    assert gap["no_data"] is True
    fields = ("impressions", "clicks", "conversions", "spend", "revenue")
    assert [gap[f] for f in fields] == [None] * 5
    assert daily["rows"][0]["no_data"] is False
    status, adjustments = _get(real_dsp, "/campaigns/c1/adjustments")
    assert status == 200 and len(adjustments["rows"]) == 1
    assert adjustments["rows"][0]["budget_before"] == 100
    assert adjustments["rows"][0]["budget_after"] == 110
    assert adjustments["rows"][0]["committed_at"].endswith("+00:00")
    assert _get(real_dsp, "/campaigns/c2/daily")[0] == 404
    assert _get(real_dsp, "/campaigns/c2/adjustments")[0] == 404
    reader = dsp_client.make_query_reader(real_dsp, 3.0)
    ok = reader(TASK, QueryOption.CHECK_DAILY_TREND)
    assert ok.reason is None and ok.raw["rows"] == daily["rows"]
    missing = reader(
        TaskRow("t2", 2, TaskState.COLLECTING_EVIDENCE, "c2", None, None, TASK.written_at),
        QueryOption.CHECK_PAST_ADJUSTMENTS)
    assert (missing.reason, missing.raw) == ("not_found", None)
    # 逐列白名單:多欄、少欄、型別不對、頂層廣告編號不符都是 invalid
    good = {"daily": daily, "adjustments": adjustments}
    for mutate in (lambda b: b["rows"][0].update(extra=1), lambda b: b["rows"][0].pop("clicks"),
                   lambda b: b["rows"][0].update(clicks="1"), lambda b: b.update(campaign_id="c9"),
                   lambda b: b["rows"].pop(), lambda b: b["rows"][1].update(no_data="yes")):
        bad = copy.deepcopy(good["daily"])
        mutate(bad)
        assert dsp_client.check_daily(bad, "c1") is None, bad
    assert dsp_client.check_daily(good["daily"], "c1") is not None
    for mutate in (lambda b: b["rows"][0].update(extra=1), lambda b: b["rows"][0].pop("days_ago"),
                   lambda b: b["rows"][0].update(committed_at="2026-09-20T12:00:00"),
                   lambda b: b.update(rows=[b["rows"][0]] * 6)):
        bad = copy.deepcopy(good["adjustments"])
        mutate(bad)
        assert dsp_client.check_adjustments(bad, "c1") is None, bad
    assert dsp_client.check_adjustments(good["adjustments"], "c1") is not None


class _Scripted(BaseHTTPRequestHandler):
    """回固定內容的假 DSP:記下每一個請求的方法與路徑。"""

    def do_GET(self):
        self.server.seen.append(("GET", self.path))
        status, body = self.server.answers.get(
            self.path, self.server.answers.get(self.path.split("?")[0], self.server.default))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.seen.append(("POST", self.path))
        self.send_response(500)
        self.end_headers()

    def log_message(self, *_args):
        return


@pytest.fixture
def scripted():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Scripted)
    server.seen, server.answers, server.default = [], {}, (404, b'{"error":"x"}')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_a_missing_daily_or_adjustment_result_does_not_fail_the_step(scripted):
    """[S1132] 查不到 → not_found、欄位不合格 → invalid,都是「這個查詢沒有結果」,不丟例外。"""
    base = f"http://127.0.0.1:{scripted.server_address[1]}"
    scripted.answers["/campaigns/c1/adjustments"] = (200, b'{"campaign_id":"c1","rows":[{"x":1}]}')
    reader = dsp_client.make_query_reader(base, 1.0)
    daily = reader(TASK, QueryOption.CHECK_DAILY_TREND)
    adjustments = reader(TASK, QueryOption.CHECK_PAST_ADJUSTMENTS)
    assert (daily.reason, adjustments.reason) == ("not_found", "invalid")
    assert daily.raw is None and adjustments.raw is None


def test_each_query_option_maps_to_its_read_only_endpoints(scripted):
    """[S1108] 單查逐日只讀逐日端點,其餘選項只打自身端點。"""
    base = f"http://127.0.0.1:{scripted.server_address[1]}"
    scripted.default = (200, b"{}")  # 每一支都回得到(內容不合格),讀取不會在第一支就停
    rows = [{"days_ago": n, "impressions": 100, "clicks": 10, "conversions": 1,
             "spend": "1.00", "revenue": "2.00", "no_data": False}
            for n in range(1, 8)]
    scripted.answers["/campaigns/c1/daily"] = (200, json.dumps(
        {"campaign_id": "c1", "rows": rows}).encode())
    for window, factor in (("1d", 1), ("7d", 7)):
        scripted.answers[f"/campaigns/c1/metrics?window={window}"] = (200, json.dumps({
            "campaign_id": "c1", "window": window, "impressions": 100 * factor,
            "clicks": 10 * factor, "conversions": factor, "spend": f"{factor}.00",
            "revenue": f"{factor * 2}.00"}).encode())
    expected = {
        QueryOption.CHECK_LONGER_WINDOW: [("GET", "/campaigns/c1/metrics?window=1d"),
                                          ("GET", "/campaigns/c1/metrics?window=7d")],
        QueryOption.CHECK_CHANGE_HISTORY: [("GET", "/campaigns/c1/history")],
        QueryOption.CHECK_DAILY_TREND: [("GET", "/campaigns/c1/daily")],
        QueryOption.CHECK_PAST_ADJUSTMENTS: [("GET", "/campaigns/c1/adjustments")],
    }
    calls = []
    reader = dsp_client.make_query_reader(
        base, 1.0, on_call=lambda _task, endpoint, _outcome, _ms: calls.append(endpoint))
    for option, paths in expected.items():
        scripted.seen.clear()
        reader(TASK, option)
        assert scripted.seen == paths, option
        assert all(method == "GET" for method, _ in scripted.seen)
    assert set(expected) == set(QueryOption)
    assert len(calls) == 5
