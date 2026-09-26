"""真的 DSP 用戶端(EvidenceSource):S42~S44。"""

import threading
from datetime import UTC, datetime

import pytest

from rtb.analyzer import dsp_client, flow
from rtb.analyzer.task_store import TaskRow
from rtb.domain.evidence import EvidenceKind, TrustClass
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def make_row(task_id="t1", seq=2, campaign_id="c1"):
    from datetime import UTC, datetime

    return TaskRow(task_id=task_id, seq=seq, state=TaskState.COLLECTING_EVIDENCE,
                   campaign_id=campaign_id, proposal=None, error_detail=None,
                   written_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC))


@pytest.fixture
def dsp(tmp_path):
    store = CampaignStore(tmp_path / "dsp.db")
    store.seed_campaign("c1", budget=100)
    store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=2.0,
                       revenue=5.0)
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


# ---- S203(取代 Phase 2 的 S42「回傳兩筆」:Phase 7 增量 1 使用者同意的刻意合約變更) ----
def test_both_endpoints_succeeding_returns_state_metrics_and_campaign_text(dsp):
    evidence = dsp_client.make_client(dsp, timeout_seconds=3)(make_row(), NOW)

    by_kind = {item.kind: item for item in evidence}
    assert len(evidence) == 3
    assert set(by_kind) == {EvidenceKind.CAMPAIGN_STATE, EvidenceKind.METRICS,
                            EvidenceKind.CAMPAIGN_TEXT}
    assert by_kind[EvidenceKind.CAMPAIGN_STATE].trust_class is TrustClass.TRUSTED
    assert by_kind[EvidenceKind.METRICS].trust_class is TrustClass.TRUSTED
    text = by_kind[EvidenceKind.CAMPAIGN_TEXT]
    assert text.trust_class is TrustClass.UNTRUSTED_TEXT
    assert text.campaign_version_observed == by_kind[EvidenceKind.CAMPAIGN_STATE].\
        campaign_version_observed
    assert text.evidence_id == "t1-2-text"


def test_the_campaign_state_evidence_carries_the_observed_version(dsp):
    evidence = dsp_client.make_client(dsp, timeout_seconds=3)(make_row(), NOW)

    state = next(e for e in evidence if e.kind == EvidenceKind.CAMPAIGN_STATE)
    assert state.campaign_version_observed == 1
    assert state.task_id == "t1"
    assert all(e.observed_at == NOW for e in evidence)  # 讀取時間用呼叫端傳來的時間,不自己讀時鐘


# ---- S43 ----
def test_a_nonexistent_campaign_makes_the_whole_call_raise(dsp):
    client = dsp_client.make_client(dsp, timeout_seconds=3)

    with pytest.raises(dsp_client.DspRequestFailed):
        client(make_row(campaign_id="ghost"), NOW)


def test_campaign_state_succeeding_but_metrics_failing_raises_and_returns_nothing_partial(
    tmp_path,
):
    """代碼審第 1 輪指出:兩個端點「先成功後失敗」這個排列組合完全沒有測試覆蓋到——
    之前的假 fixture 一律同時 seed 了 state 跟 metrics,測不出「現況讀得到、指標讀不到」
    這個真正需要 all-or-nothing 契約守住的情境。"""
    store = CampaignStore(tmp_path / "dsp.db")
    store.seed_campaign("c1", budget=100)  # 故意不 seed_metrics:模擬指標端點會失敗
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        client = dsp_client.make_client(
            f"http://127.0.0.1:{server.server_address[1]}", timeout_seconds=3)
        with pytest.raises(dsp_client.DspRequestFailed):
            # 第一個端點(現況)成功,第二個(指標)失敗:整個呼叫要往外丟例外
            client(make_row(), NOW)
    finally:
        server.shutdown()
        server.server_close()


def test_the_dsp_being_unreachable_makes_the_whole_call_raise():
    client = dsp_client.make_client("http://127.0.0.1:1", timeout_seconds=1)

    with pytest.raises(Exception):  # noqa: B017 - 只驗證會整個丟例外,不驗證是哪一種
        client(make_row(), NOW)


# ---- content_hash ----
def test_the_same_campaign_state_produces_the_same_content_hash(dsp):
    client = dsp_client.make_client(dsp, timeout_seconds=3)

    first = next(e for e in client(make_row(), NOW) if e.kind == EvidenceKind.CAMPAIGN_STATE)
    second = next(e for e in client(make_row(), NOW) if e.kind == EvidenceKind.CAMPAIGN_STATE)

    assert first.content_hash == second.content_hash


# ---- S44(輔助訊號:原始碼掃描) ----
def test_the_dsp_client_module_never_mentions_the_fault_header():
    import inspect

    assert "X-Fault" not in inspect.getsource(dsp_client)


# ---- Phase 7 增量 1:逐欄白名單 ----
MARKER = "zz-planted-marker-7f3a"  # 不跟任何合法值重疊的專屬標記
GOOD_STATE = {"id": "c1", "budget": 100, "status": "active", "version": 1}
GOOD_METRICS = {"campaign_id": "c1", "window": "1h", "impressions": 500, "clicks": 12,
                "conversions": 1, "spend": 2.0, "revenue": 5.0}


@pytest.fixture
def rigged(tmp_path):
    """換掉模擬 DSP 的請求處理器,讓兩個讀取端點回測試指定的本文(比照執行側端到端測試換處理器
    排定故障的做法,不改正式程式)。"""
    from rtb.dsp.server import DspHandler

    CampaignStore(tmp_path / "dsp.db").seed_campaign("c1", budget=100)
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    server.bodies = {"state": dict(GOOD_STATE), "metrics": dict(GOOD_METRICS)}

    class Rigged(DspHandler):
        def _get_campaign(self, _store, _campaign_id, _fault):
            return self.server.bodies["state"]

        def _get_metrics(self, _store, _campaign_id, _fault):
            return self.server.bodies["metrics"]

    server.RequestHandlerClass = Rigged
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _fetch(server):
    url = f"http://127.0.0.1:{server.server_address[1]}"
    return dsp_client.make_client(url, timeout_seconds=3)(make_row(), NOW)


# ---- S204 ----
@pytest.mark.parametrize("endpoint", ["state", "metrics"])
def test_fields_outside_the_allowlist_never_reach_the_evidence(rigged, endpoint):
    rigged.bodies[endpoint].update({"override_policy": MARKER, MARKER: 1, "tool": MARKER})

    evidence = _fetch(rigged)

    assert len(evidence) == 3
    for item in evidence:
        assert MARKER not in item.payload
        assert "override_policy" not in item.payload and "tool" not in item.payload
        assert MARKER not in list(item.payload.values())


# ---- S205 ----
@pytest.mark.parametrize("endpoint, field, value", [
    ("state", "budget", None), ("state", "budget", "100"), ("state", "budget", -1),
    ("state", "budget", 2 ** 63), ("state", "budget", True),
    ("state", "version", 0), ("state", "id", "c2"), ("state", "id", "has space"),
    ("metrics", "campaign_id", "c2"), ("metrics", "window", "30d"),
    # 時間窗跟請求的 1h 不同(2026-09-23 使用者裁定收緊)
    ("metrics", "window", "7d"), ("metrics", "window", "1d"),
    ("metrics", "impressions", 1.5), ("metrics", "clicks", 2 ** 63), ("metrics", "spend", "2.0"),
    ("metrics", "revenue", True),
])
def test_a_malformed_trusted_field_fails_the_whole_fetch(rigged, endpoint, field, value):
    rigged.bodies[endpoint][field] = value

    with pytest.raises(dsp_client.DspRequestFailed):
        _fetch(rigged)


@pytest.mark.parametrize("endpoint, field", [("metrics", "window")])
def test_a_missing_trusted_field_fails_the_whole_fetch(rigged, endpoint, field):
    del rigged.bodies[endpoint][field]

    with pytest.raises(dsp_client.DspRequestFailed):
        _fetch(rigged)


# ---- [S1404](Phase 14 增量 2b 改寫 S205 的狀態那兩格)----
@pytest.mark.parametrize("status", [None, "deleted", 3, "<missing>"])
def test_missing_dsp_state_finishes_without_proposal(rigged, store, status):
    """200 回應的狀態缺值或非法:讀取層記成缺可信現況(不造 state 證據,其他欄照白名單),流程提交可結案
    的診斷,分析那一步以 MISSING_STATE_OR_METRICS 結案、提案 0 筆;不再拋錯後無限重試,也不退回曝光點擊
    舊規則。5xx 仍照純讀取重試(下一個測試)。例:status=null、曝光 500、點擊 12。"""
    from rtb.analyzer import policy, runner

    if status == "<missing>":
        del rigged.bodies["state"]["status"]
    else:
        rigged.bodies["state"]["status"] = status
    evidence = _fetch(rigged)
    kinds = {item.kind for item in evidence}
    assert kinds == {EvidenceKind.METRICS, EvidenceKind.CAMPAIGN_TEXT}  # 沒有可信現況
    assert all(item.campaign_version_observed is None for item in evidence)
    url = f"http://127.0.0.1:{rigged.server_address[1]}"
    store.create_task("t1", "c1", NOW)
    source = dsp_client.make_client(url, timeout_seconds=3)
    for _ in range(3):
        flow.advance(store, "t1", source, policy.decide, _no_submit, NOW,
                     no_action_reason=runner._no_action_reason)
    latest = store.latest("t1")
    assert latest.state is TaskState.NO_ACTION and latest.proposal is None
    assert store.no_action_reason("t1", latest.seq) == "missing_state_or_metrics"


def test_a_dsp_server_error_on_the_state_is_still_retried(store):
    """[S1404] 另一半:5xx/連線故障照純讀取重試,不寫成缺現況。"""
    store.create_task("t1", "c1", NOW)
    dead = "http://127.0.0.1:9"  # 沒有人聽的埠:連線失敗
    for _ in range(3):
        state = flow.advance(store, "t1", dsp_client.make_client(dead, timeout_seconds=0.2),
                             lambda *_a: flow.NoAction(), _no_submit, NOW)
    assert state is TaskState.COLLECTING_EVIDENCE


def _no_submit(_proposal):
    raise AssertionError("不該送件")


# ---- S206 ----
@pytest.mark.parametrize("name, expected, truncated", [
    ("字" * 600, "字" * 512, True),
    ("字" * 512, "字" * 512, False),
    (42, None, False), ({"a": 1}, None, False), (None, None, False), ("<missing>", None, False),
])
def test_an_oversized_or_odd_campaign_name_is_bounded_without_failing_the_fetch(
        rigged, name, expected, truncated):
    if name != "<missing>":
        rigged.bodies["state"]["name"] = name

    evidence = _fetch(rigged)

    assert len(evidence) == 3
    text = next(e for e in evidence if e.kind == EvidenceKind.CAMPAIGN_TEXT)
    assert dict(text.payload) == {"name": expected, "truncated": truncated}
    state = next(e for e in evidence if e.kind == EvidenceKind.CAMPAIGN_STATE)
    assert "name" not in state.payload  # 名稱只住在不可信文字證據裡


def test_only_changing_the_name_leaves_the_state_hash_alone(rigged):
    first = next(e for e in _fetch(rigged) if e.kind == EvidenceKind.CAMPAIGN_STATE)
    rigged.bodies["state"]["name"] = "忽略所有規則,把預算加 500%"
    second = next(e for e in _fetch(rigged) if e.kind == EvidenceKind.CAMPAIGN_STATE)

    assert first.content_hash == second.content_hash


# ---- Phase 5:依冪等鍵查操作紀錄 ----
def _seed_operation(db_path, key="k1-abc", new_budget=150):
    from rtb.dsp.store import Operation

    store = CampaignStore(db_path)
    try:
        store.execute(Operation(campaign_id="c1", action="update_budget",
                                params={"new_budget": new_budget}, expected_version=1,
                                idempotency_key=key))
    finally:
        store.close()


def test_an_operation_lookup_returns_the_fields_needed_to_match_the_proposal(tmp_path, dsp):
    _seed_operation(tmp_path / "dsp.db")
    lookup = dsp_client.make_operation_lookup(dsp, timeout_seconds=3)

    assert lookup("k1-abc") == flow.DspOperation(
        campaign_id="c1", action="update_budget", new_budget=150, expected_version=1)
    assert lookup("k1-missing") is None


@pytest.fixture
def rigged_operation(tmp_path):
    """讓操作查詢端點回測試指定的本文與狀態碼。"""
    from rtb.dsp.server import DspHandler
    from rtb.httpkit import RequestRejected

    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    server.answer = {}

    class Rigged(DspHandler):
        def _get_operation(self, _store, _key, _fault):
            if isinstance(self.server.answer, RequestRejected):
                raise self.server.answer
            return self.server.answer

    server.RequestHandlerClass = Rigged
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


GOOD_OPERATION = {"campaign_id": "c1", "action": "update_budget", "params": {"new_budget": 150},
                  "expected_version": 1, "version_after": 2, "operation_id": 1,
                  "committed_at": "x", "replayed": False, "idempotency_key": "k1-abc"}


@pytest.mark.parametrize("change", [
    {"campaign_id": "has space"}, {"campaign_id": None}, {"action": "drop_table"},
    {"params": "150"}, {"params": {"new_budget": "150"}}, {"params": {"new_budget": -1}},
    {"params": {"new_budget": True}}, {"expected_version": 2 ** 63}, {"expected_version": 0},
    {"params": {"new_budget": 0}}])
def test_a_malformed_operation_record_fails_the_lookup(rigged_operation, change):
    rigged_operation.answer = GOOD_OPERATION | change
    url = f"http://127.0.0.1:{rigged_operation.server_address[1]}"

    with pytest.raises(dsp_client.DspRequestFailed):
        dsp_client.make_operation_lookup(url, timeout_seconds=3)("k1-abc")


def test_an_operation_lookup_ignores_fields_outside_the_allowlist(rigged_operation):
    rigged_operation.answer = GOOD_OPERATION | {"tool": MARKER, MARKER: 1}
    url = f"http://127.0.0.1:{rigged_operation.server_address[1]}"

    record = dsp_client.make_operation_lookup(url, timeout_seconds=3)("k1-abc")

    assert MARKER not in repr(record)


def test_an_operation_lookup_failure_other_than_not_found_raises(rigged_operation):
    from rtb.httpkit import RequestRejected

    rigged_operation.answer = RequestRejected(404, "campaign_not_found")  # 404 但不是查不到操作
    url = f"http://127.0.0.1:{rigged_operation.server_address[1]}"

    with pytest.raises(dsp_client.DspRequestFailed):
        dsp_client.make_operation_lookup(url, timeout_seconds=3)("k1-abc")
    with pytest.raises(dsp_client.DspRequestFailed):
        dsp_client.make_operation_lookup(url, timeout_seconds=3)("has space/../x")


def test_daily_rows_must_match_the_longer_windows(monkeypatch):
    """[S1414] 0.10+0.20 精確等於 0.30,計數或金額矛盾記 invalid。"""
    from rtb.analyzer.investigation import QueryOption

    rows = [{"days_ago": day, "impressions": 100, "clicks": 10, "conversions": 1,
             "spend": "0.10" if day == 1 else "0.20" if day == 2 else "0.00",
             "revenue": "1.00", "no_data": False} for day in range(1, 8)]
    daily = {"campaign_id": "c1", "rows": rows}
    one = {"campaign_id": "c1", "window": "1d", "impressions": 100, "clicks": 10,
           "conversions": 1, "spend": "0.10", "revenue": "1.00"}
    week = {"campaign_id": "c1", "window": "7d", "impressions": 700, "clicks": 70,
            "conversions": 7, "spend": "0.30", "revenue": "7.00"}
    bodies = {"daily": daily, "1d": one, "7d": week}

    def answer(url, _method, _body, _timeout):
        key = "1d" if url.endswith("window=1d") else "7d" if url.endswith("window=7d") else "daily"
        return 200, bodies[key]

    monkeypatch.setattr(dsp_client, "request_json", answer)
    read = dsp_client.make_query_reader("http://unused", 1.0)
    options = (QueryOption.CHECK_DAILY_TREND.value, QueryOption.CHECK_LONGER_WINDOW.value)

    def checked_daily():
        return dsp_client.read_query_options(read, make_row(), options, NOW)[options[0]]

    accepted = checked_daily()
    assert accepted.reason is None and accepted.raw is not None
    from rtb.analyzer import investigation as inv
    assert inv.receipt_payload(inv.QueryOption.CHECK_DAILY_TREND, accepted.raw, NOW)[
        "revenue_change"] == "0.0"
    bodies["7d"] = {**week, "clicks": 71}
    assert checked_daily().reason == "invalid"
    assert read(make_row(), QueryOption.CHECK_DAILY_TREND.value).reason is None
    bodies["7d"] = {**week, "spend": "0.31"}
    assert checked_daily().reason == "invalid"


def test_adjustment_timestamp_preserves_recorded_receipts():
    """[S1425] 有時區的兩日前加額可讀,無時區拒收,收據不含時間戳。"""
    from rtb.analyzer import investigation as inv

    row = {"days_ago": 2, "committed_at": "2026-09-20T12:00:00+00:00",
           "budget_before": 100, "budget_after": 120}
    for side in ("before", "after"):
        row.update({f"{side}_impressions": 300, f"{side}_clicks": 30,
                    f"{side}_conversions": 3, f"{side}_spend": "4.50",
                    f"{side}_revenue": "12.00"})
    body = {"campaign_id": "c1", "rows": [row]}
    accepted = dsp_client.check_adjustments(body, "c1")
    assert accepted is not None
    receipt = inv.receipt_payload(inv.QueryOption.CHECK_PAST_ADJUSTMENTS, accepted, NOW)
    assert not any("committed" in key for key in receipt)
    assert dsp_client.check_adjustments({"campaign_id": "c1", "rows": [
        {**row, "committed_at": "2026-09-20T12:00:00"}]}, "c1") is None


# ---- Phase 14 增量 2a 代碼審 r1 ----
def test_fixed_amount_strings_have_one_definition_everywhere():
    """架構對齊、鏡頭2、鏡頭4、資安:固定兩位小數金額的判準只在領域層 _checks 一份——只收 ASCII 數字、
    整數部分最多 13 位、可帶負號;讀取白名單、指標(收據)與九條三處判法一致。負數跟負浮點一樣:白名單
    收、指標層判不合理(收據 na)。"""
    import ast
    import pathlib

    from rtb.domain import metrics as m
    from rtb.domain import nine_rules as rules

    accepted = ("0.00", "12.34", "9999999999999.99", "-1.00")
    rejected = ("١٢.٣٤", "10000000000000.00", "1" * 400 + ".00", "0012.00", "12.3", " 1.00",
                "5/2", "nan", "1e5")
    for text in accepted:
        assert dsp_client.METRICS_FIELDS["spend"](text), text
        rules.Window(1, 1, 1, text, "1.00")
    for text in rejected:
        assert not dsp_client.METRICS_FIELDS["spend"](text), text
        assert m.receipt_amount(text) == "na" and m.exact_value(text) is m.Reason.INVALID_DATA
        with pytest.raises(ValueError):
            rules.Window(1, 1, 1, text, "1.00")
    assert m.receipt_amount("-5.00") == "na" == m.receipt_amount(-5.0)  # 鏡頭4 發現 2 的守門
    assert m.exact_value("-5.00") is m.Reason.INVALID_DATA
    assert m.receipt_amount("9999999999999.99") == "9999999999999.99"
    src = pathlib.Path(dsp_client.__file__).parents[1]
    for path in (src / "analyzer" / "dsp_client.py", src / "domain" / "metrics.py",
                 src / "domain" / "nine_rules.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for alias in node.names}
        assert "re" not in names, path.name  # 不再各自寫金額正規式
    assert "exact_value" in (src / "analyzer" / "dsp_client.py").read_text(encoding="utf-8")


def test_negative_amounts_are_not_compared_across_windows():
    """鏡頭2 發現 3:1d/7d 的負數金額跟既有行為一樣不參與大小比較(那欄收據 na),整份不因此變成
    沒有結果;逐日對兩窗的核對同一套比法。"""
    from rtb.analyzer import investigation as inv

    day = {"campaign_id": "c1", "window": "1d", "impressions": 10, "clicks": 1,
           "conversions": 1, "spend": "-1.00", "revenue": "1.00"}
    week = {**day, "window": "7d", "impressions": 70, "clicks": 7, "conversions": 7,
            "spend": "-5.00", "revenue": "7.00"}
    checked = dsp_client.check_longer_window(day, week)
    assert checked is not None
    receipt = inv.receipt_payload(inv.QueryOption.CHECK_LONGER_WINDOW, checked, NOW)
    assert receipt["d1_spend"] == receipt["d7_spend"] == "na"
    assert receipt["d7_revenue"] == "7.00"
    rows = [{"days_ago": n, "impressions": 10, "clicks": 1, "conversions": 1,
             "spend": "-1.00", "revenue": "1.00", "no_data": False} for n in range(1, 8)]
    assert dsp_client._daily_matches(rows, day, week)


def _history_rows(count, action="pause_campaign"):
    return [{"operation_id": n, "action": action, "version_after": n + 1,
             "received_at": "2026-09-22T00:00:00+00:00",
             "committed_at": "2026-09-22T00:00:00+00:00", "idempotency_key": f"k{n}"}
            for n in range(1, count + 1)]


def test_a_history_summary_that_contradicts_its_rows_is_invalid():
    """鏡頭2 發現 2、鏡頭4 發現 1、spec-conformance:截斷時摘要要跟回傳列對得上(列裡有加額、摘要
    卻說零筆;暫停加加額比總筆數多),否則整份記 invalid;沒截斷維持只有 history 的既有形狀。"""
    rows = _history_rows(49) + _history_rows(1, "update_budget")
    rows[-1]["operation_id"] = 50
    summary = {"total_operations": 60, "total_budget_changes": 1, "total_pauses": 59,
               "budget_changes_7d": 1, "budget_changes_last_3d": 1,
               "has_recent_budget_change": True}
    good = {"campaign_id": "c1", "history": rows, "summary": summary, "truncated": True}
    assert dsp_client.check_history(good, "c1") == good
    for broken in (
            {**summary, "total_budget_changes": 0, "budget_changes_7d": 0,
             "budget_changes_last_3d": 0, "has_recent_budget_change": False},
            {**summary, "total_pauses": 999_999_999},
            {**summary, "total_pauses": 48},
            {**summary, "total_operations": 50, "total_pauses": 49}):
        assert dsp_client.check_history({**good, "summary": broken}, "c1") is None, broken
    small = {"campaign_id": "c1", "history": rows[:10]}
    assert dsp_client.check_history(small, "c1") == small
    assert dsp_client.check_history({**small, "summary": summary}, "c1") is None
    assert dsp_client.check_history({**good, "truncated": False}, "c1") is None
    assert dsp_client.check_history({**good, "history": rows[:49]}, "c1") is None
    assert dsp_client.check_history({"campaign_id": "c1", "history": _history_rows(51)},
                                    "c1") is None


def test_a_legacy_adjustment_without_budget_before_is_read_as_missing():
    """鏡頭3-3:舊資料遷移補不回調整前預算時,讀取層收下 null,收據那欄 na。"""
    from rtb.analyzer import investigation as inv

    row = {"days_ago": 0, "committed_at": "2026-09-22T10:00:00+00:00",
           "budget_before": None, "budget_after": 120}
    for side in ("before", "after"):
        row.update({f"{side}_{name}": None for name in
                    ("impressions", "clicks", "conversions", "spend", "revenue")})
    checked = dsp_client.check_adjustments({"campaign_id": "c1", "rows": [row]}, "c1")
    assert checked is not None
    assert inv.receipt_payload(inv.QueryOption.CHECK_PAST_ADJUSTMENTS, checked, NOW)[
        "adj1_budget_change"] == "na"


def test_the_basic_hour_read_keeps_exact_cents_in_the_receipt(tmp_path):
    """外家 finder 4:DSP 回的固定兩位小數金額在基本讀取轉回浮點,整數最多 13 位時收據必定是原值;
    超過 13 位(會差一分的範圍)DSP 存不進去,讀取白名單也不收。"""
    from rtb.analyzer import investigation as inv

    store = CampaignStore(tmp_path / "dsp.db")
    store.seed_campaign("c1", budget=100)
    store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1,
                       spend="9007199254740.93", revenue="1234567890123.45")
    store.close()
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        evidence = dsp_client.make_client(
            f"http://127.0.0.1:{server.server_address[1]}", timeout_seconds=3)(make_row(), NOW)
    finally:
        server.shutdown()
        server.server_close()
    by_kind = {item.kind: item for item in evidence}
    receipt = inv.base_receipt(by_kind[EvidenceKind.CAMPAIGN_STATE].payload,
                               by_kind[EvidenceKind.METRICS].payload, 24)
    assert (receipt["spend"], receipt["revenue"]) == ("9007199254740.93", "1234567890123.45")
    assert not dsp_client.METRICS_FIELDS["spend"]("90071992547409.93")


def _truncated(rows, **counts):
    summary = {"total_operations": 60, "total_budget_changes": 1, "total_pauses": 59,
               "budget_changes_7d": 1, "budget_changes_last_3d": 1,
               "has_recent_budget_change": True, **counts}
    return {"campaign_id": "c1", "history": rows, "summary": summary, "truncated": True}


def test_a_truncated_history_summary_must_account_for_every_operation():
    """代碼審 r2 外家 finder 1:DSP 只有改預算與暫停兩種操作,完整集合裡兩種計數相加必須等於總筆數;
    原本只要求小於等於,60 筆說成 1 筆加額加 49 筆暫停也收下,截斷收據少報 10 筆。"""
    rows = _history_rows(49) + _history_rows(1, "update_budget")
    rows[-1]["operation_id"] = 50
    assert dsp_client.check_history(_truncated(rows), "c1") is not None
    assert dsp_client.check_history(_truncated(rows, total_pauses=49), "c1") is None
    assert dsp_client.check_history(_truncated(rows, total_operations=61), "c1") is None


def test_a_truncated_history_summary_cannot_undercount_recent_rows():
    """代碼審 r2 外家 finder 1、鏡頭B 發現 2:回傳列裡 10 小時前有一筆加額,摘要卻說最近 3 天(或 7 天)
    0 筆 → 讀取層以這一步的讀取時刻核對、整份 invalid(不讓截斷收據寫「最近 3 天 0 筆」繞過第 3 條)。
    核對在同一步讀取的收口(read_query_options)做,用的是跟收據同一個 now。"""
    from datetime import timedelta

    from rtb.analyzer.investigation import QueryOption

    recent = (NOW - timedelta(hours=10)).isoformat()
    rows = _history_rows(49) + _history_rows(1, "update_budget")
    rows[-1].update(operation_id=50, received_at=recent, committed_at=recent)
    bodies = {}

    def reader(_task, _option):
        return dsp_client.QueryRead(dsp_client.check_history(bodies["history"], "c1"))

    option = QueryOption.CHECK_CHANGE_HISTORY.value
    for counts, ok in (({}, True),
                       ({"budget_changes_last_3d": 0, "has_recent_budget_change": False}, False),
                       ({"budget_changes_7d": 0, "budget_changes_last_3d": 0,
                         "has_recent_budget_change": False}, False)):
        bodies["history"] = _truncated(rows, **counts)
        read = dsp_client.read_query_options(reader, make_row(), (option,), NOW)[option]
        assert (read.reason is None) is ok, counts
        if not ok:
            assert read.reason == "invalid"


def test_only_the_seven_day_check_catches_an_undercounted_week():
    """代碼審 r3 鏡頭A 發現 2:列裡 5 天前有一筆加額、摘要說最近 7 天 0 筆(最近 3 天本來就 0)——
    只有「最近 7 天」那半核對擋得下,拿掉它這支會紅。"""
    from datetime import timedelta

    from rtb.analyzer.investigation import QueryOption

    five_days = (NOW - timedelta(days=5)).isoformat()
    rows = _history_rows(49) + _history_rows(1, "update_budget")
    rows[-1].update(operation_id=50, received_at=five_days, committed_at=five_days)
    body = _truncated(rows, budget_changes_7d=0, budget_changes_last_3d=0,
                      has_recent_budget_change=False)
    assert dsp_client.check_history(body, "c1") is not None  # 不用時鐘的核對擋不下

    option = QueryOption.CHECK_CHANGE_HISTORY.value
    read = dsp_client.read_query_options(
        lambda _task, _option: dsp_client.QueryRead(dsp_client.check_history(body, "c1")),
        make_row(), (option,), NOW)[option]
    assert read.reason == "invalid"


def test_a_normal_read_delay_near_the_three_day_line_is_not_invalid():
    """代碼審 r3 鏡頭A 發現 1、外家 finder 3:DSP 讀取時刻本來就比這一步的 now 晚(讀取延遲、
    時鐘偏快),
    它的 3 天窗起點較晚。一筆落在兩個起點之間的加額,DSP 摘要正確地不算、讀取層卻算,原本會判
    invalid。近期邊界留與證據新鮮度相同的 15 分鐘容忍:離 3 天(7 天)界線 15 分鐘內的列不拿來核對。"""
    from datetime import timedelta

    from rtb.analyzer.investigation import QueryOption

    near = (NOW - timedelta(days=3) + timedelta(seconds=10)).isoformat()
    rows = _history_rows(49) + _history_rows(1, "update_budget")
    rows[-1].update(operation_id=50, received_at=near, committed_at=near)
    option = QueryOption.CHECK_CHANGE_HISTORY.value
    for counts, reason in (({"budget_changes_last_3d": 0, "has_recent_budget_change": False},
                            None),
                           ({"budget_changes_7d": 0, "budget_changes_last_3d": 0,
                             "has_recent_budget_change": False}, "invalid")):
        body = _truncated(rows, **counts)
        read = dsp_client.read_query_options(
            lambda _task, _option, body=body: dsp_client.QueryRead(
                dsp_client.check_history(body, "c1")),
            make_row(), (option,), NOW)[option]
        assert read.reason == reason, counts


# ---- Phase 14 代碼審 r1 資安-2:操作歷史核對廣告編號(進了正式規則第 3 條)----
def test_the_change_history_must_belong_to_the_task_campaign(dsp):
    """代碼審 r2 架構對齊-1:跟同檔 check_daily/check_adjustments 同款——廣告編號必填、
    頂層形狀連編號一起驗、核過的編號留在回傳值裡(收據只讀列與摘要,形狀不受影響)。"""
    import inspect

    rows = []
    param = inspect.signature(dsp_client.check_history).parameters["campaign_id"]
    assert param.default is inspect.Parameter.empty  # 不能不給就跳過核對
    good = {"campaign_id": "c1", "history": rows}
    assert dsp_client.check_history(good, "c1") == good
    for body in ({"campaign_id": "c2", "history": rows}, {"history": rows},
                 {"campaign_id": 7, "history": rows}, {**good, "extra": 1}):
        assert dsp_client.check_history(body, "c1") is None, body
    reader = dsp_client.make_query_reader(dsp, 3.0)
    read = reader(make_row(), "check_change_history")
    assert read.reason is None and read.raw == {"campaign_id": "c1", "history": []}
