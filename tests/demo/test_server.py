"""一鍵展示伺服器(Phase 12 增量 2b):路由、觸發防護、同時一次、單一情境重跑、核可、報告。

多數測試換掉驅動程式的情境(很快跑完的假情境),真的核可流程用縮小版 F7 真跑。
"""

import hashlib
import http.client
import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from rtb.demo import driver as driver_module
from rtb.demo import server as server_module
from rtb.demo.driver import Driver, Scenario
from rtb.demo.present import numbers_digest
from rtb.demo.server import DemoService, serve
from rtb.demo.state_store import ConfirmationRequest, StateReader

FORM = {"Content-Type": "application/x-www-form-urlencoded", "Sec-Fetch-Site": "same-origin"}


class Gate:
    """假情境用的閘:情境本體等它打開才結束,測試可以看到「在跑」的樣子。"""

    def __init__(self):
        self.opened = threading.Event()

    def scenario(self, code):
        def run(_world):
            self.opened.wait(30)
            return f"{code} 假情境"
        return Scenario(code, f"{code} 假", 60, run, "目標")


def _factory(gate=None, crash=False, verifier=("true",)):
    def make(base, demo_id, keys, state):
        if crash:
            class Broken(Driver):
                def run_all(self, codes=driver_module.ALL_CODES):
                    raise RuntimeError("驅動程式出事")
            cls = Broken
        else:
            cls = Driver
        opened = gate or Gate()
        if gate is None:
            opened.opened.set()  # 沒給閘:假情境立刻跑完
        scenarios = {code: opened.scenario(code) for code in driver_module.ALL_CODES}
        made = cls(base, demo_id, keys, state, user_env=os.environ, scenarios=scenarios)
        made.verifier_command = list(verifier)
        return made
    return make


@pytest.fixture
def service(tmp_path):
    return DemoService(tmp_path / "demos", tmp_path / "state.db", tmp_path / "reports")


@pytest.fixture
def running(service):
    server = serve(service)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _request(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        raw = b"" if body is None else body.encode()
        conn.putheader("Content-Length", str(len(raw)))
        conn.endheaders(raw)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()
    finally:
        conn.close()


def _post(port, path, fields, headers=FORM):
    return _request(port, "POST", path, urlencode(fields), headers)


def _wait(condition, seconds=30.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _finish(service, gate):
    gate.opened.set()
    assert _wait(lambda: not service.running)


# ---- 路由與防護 ----
def test_the_stylesheet_is_served_as_css(running):
    """[S1062]"""
    status, headers, body = _request(running, "GET", "/static/demo.css")
    assert status == 200 and headers["Content-Type"] == "text/css; charset=utf-8" and body


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/"), ("GET", "/static/demo.css"), ("GET", "/approve"), ("GET", "/report"),
    ("POST", "/run"), ("POST", "/run/scenario"), ("POST", "/approve")])
def test_every_route_rejects_a_non_local_host(running, method, path):
    """[S1017] 主機標頭不是本機:每一個路由都拒,錯誤頁帶內容安全政策標頭。"""
    status, headers, _ = _request(running, method, path, host="evil.example:80")
    assert status == 400 and "script-src 'none'" in headers["Content-Security-Policy"]


def test_every_page_carries_a_script_free_content_security_policy(running):
    """[S1023] 每個 HTML 回應(含錯誤頁)帶不准腳本的政策標頭,頁面裡沒有腳本。"""
    for path in ("/", "/report", "/approve", "/nothing"):
        _, headers, body = _request(running, "GET", path)
        assert "script-src 'none'" in headers["Content-Security-Policy"], path
        assert "<script" not in body.lower(), path


def test_every_post_redirects_to_the_current_node(running, service):
    """[S1063] 每一個 POST 都是 303 轉到 /#current。"""
    service.driver_factory = _factory(gate := Gate())
    for path, fields in (("/run", {}), ("/run/scenario", {"scenario": "F3"}),
                         ("/approve", {})):
        try:
            status, headers, _ = _post(running, path, {"token": service.token, **fields})
        finally:
            _finish(service, gate)
        if path != "/approve":
            assert (status, headers["Location"]) == (303, "/#current"), path


@pytest.mark.parametrize(("fields", "headers", "status"), [
    ({}, FORM, 403),  # 沒帶表單隨機值
    ({"token": "wrong"}, FORM, 403),
    ({"TOKEN": ""}, {**FORM, "Sec-Fetch-Site": "cross-site"}, 403),  # 別的網站送的
    ({"TOKEN": ""}, {"Content-Type": FORM["Content-Type"], "Origin": "http://evil.example"}, 403),
])
def test_a_cross_site_or_malformed_trigger_is_refused(running, service, fields, headers, status):
    """[S1015] 沒帶或帶錯表單值、不是本站送的:拒絕,不啟動驅動程式。"""
    if "TOKEN" in fields:
        fields = {"token": service.token}
    got, _, _ = _post(running, "/run", fields, headers)
    assert got == status and service.current is None and not service.running


@pytest.mark.parametrize("code", ["F8", "", "<script>"])
def test_an_unknown_scenario_code_starts_nothing(running, service, code):
    """[S1015] 情境代碼只收七個之一。"""
    got, _, _ = _post(running, "/run/scenario", {"token": service.token, "scenario": code})
    assert got == 400 and service.current is None


def test_form_tokens_are_compared_in_constant_time(running, service, monkeypatch):
    """[S1016] 表單隨機值用 hmac.compare_digest 比對。"""
    seen = []
    real = server_module.hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(server_module.hmac, "compare_digest", spy)
    _post(running, "/run", {"token": "wrong"})
    assert (b"wrong", service.token.encode()) in seen


def test_an_unknown_scenario_query_is_treated_as_no_selection(running):
    """[S1019] 查詢參數不是七個代碼之一(含重複參數):當沒選,不回錯誤、不把值放進頁面。"""
    for query in ("?scenario=%3Cscript%3Evalue", "?scenario=F3&scenario=F4", "?scenario="):
        status, _, body = _request(running, "GET", f"/{query}")
        assert status == 200 and "<script>value" not in body


def test_the_demo_server_binds_loopback_and_page_reads_write_nothing(running, service):
    """[S1018] 只綁回送位址;頁面、報告、樣式表、確認頁的讀取不寫展示狀態庫。"""
    def fingerprint():
        return hashlib.sha256(service.state_db.read_bytes()).hexdigest()

    before = fingerprint()
    for path in ("/", "/report", "/static/demo.css", "/approve", "/?scenario=F2"):
        _request(running, "GET", path)
    assert fingerprint() == before
    server = serve(service)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


# ---- 同時一次 ----
def test_a_second_trigger_while_a_demo_runs_is_refused(running, service):
    """[S1013] 已經在跑:全部跑一次與單一情境重跑都不再啟動,回到正在跑的那一次。"""
    service.driver_factory = _factory(gate := Gate())
    try:
        _post(running, "/run", {"token": service.token})
        first = service.current.demo_id
        _post(running, "/run", {"token": service.token})
        _post(running, "/run/scenario", {"token": service.token, "scenario": "F2"})
        assert service.current.demo_id == first
    finally:
        _finish(service, gate)


def test_two_concurrent_triggers_start_only_one_demo(service):
    """[S1014] 兩個真的同時到的觸發只有一個啟動。"""
    service.driver_factory = _factory(gate := Gate())
    barrier, started, refused = threading.Barrier(8), [], []

    def trigger():
        barrier.wait(5)
        try:
            started.append(service.start(driver_module.ALL_CODES, full=True))
        except server_module.DemoBusy:
            refused.append(1)

    threads = [threading.Thread(target=trigger) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    try:
        assert len(started) == 1 and len(refused) == 7
    finally:
        _finish(service, gate)


def test_a_crashed_driver_releases_the_running_flag(service):
    """[S1052] 驅動程式出例外:在跑旗標照樣放掉,下一次觸發可以正常啟動。"""
    service.driver_factory = _factory(crash=True)
    service.start(driver_module.ALL_CODES, full=True)
    assert _wait(lambda: not service.running)
    service.driver_factory = _factory(gate := Gate())
    service.start(driver_module.ALL_CODES, full=True)
    _finish(service, gate)


def test_the_page_really_refetches_while_a_demo_runs(running, service):
    """[S1045] 在跑時頁面每 2 秒整頁重讀,重讀網址每次都不同;跑完之後不再重讀。"""
    service.driver_factory = _factory(gate := Gate())
    try:
        _post(running, "/run", {"token": service.token})
        pages = [_request(running, "GET", "/")[2] for _ in range(2)]
        urls = [re.search(r'http-equiv="refresh" content="2; url=([^"]+)"', p) for p in pages]
        assert all(urls) and urls[0].group(1) != urls[1].group(1)
    finally:
        _finish(service, gate)
    assert 'http-equiv="refresh"' not in _request(running, "GET", "/")[2]


# ---- 單一情境重跑、報告 ----
def _full_then_rerun(service, code):
    service.driver_factory = _factory(gate := Gate())
    gate.opened.set()
    service.start(driver_module.ALL_CODES, full=True)
    assert _wait(lambda: not service.running)
    full = service.current.demo_id
    service.start((code,), full=False)
    assert _wait(lambda: not service.running)
    return full, service.current.demo_id


def test_a_scenario_rerun_keeps_the_other_six_scenarios_intact(service):
    """[S1020] 單一情境重跑:只換掉那一個情境,其他六個(含出處)原樣保留。"""
    full, rerun = _full_then_rerun(service, "F3")
    origins = {s.code.value: s.source_demo_id for s in service.state().scenarios}
    assert origins.pop("F3") == rerun
    assert set(origins.values()) == {full}


def test_a_single_scenario_rerun_does_not_run_the_verifier_and_labels_the_old_result(service):
    """[S1021] 重跑不跑驗證器;頁面的驗證器結果還是那一次全部跑一次的(展示編號)。"""
    full, _ = _full_then_rerun(service, "F2")
    reader = StateReader(service.state_db)
    try:
        runs = reader.latest_verifier_run()
    finally:
        reader.close()
    assert runs.demo_id == full
    assert service.state().verifier.demo_id == full


def test_a_finished_demo_saves_a_static_report_and_keeps_twenty(service):
    """[S1039] 展示結束另存一份靜態報告(沒有表單、沒有自動重讀、樣式內嵌);全部跑一次只留最近 20 份,
    單一情境重跑的另外標記、不佔那 20 份。"""
    service.reports.mkdir(parents=True)
    for i in range(25):
        (service.reports / f"demo-full-2000{i:02d}-old.html").write_text("舊", encoding="utf-8")
    _full_then_rerun(service, "F1")
    full = sorted(service.reports.glob("demo-full-*.html"))
    reruns = sorted(service.reports.glob("demo-rerun-*.html"))
    assert len(full) == 20 and len(reruns) == 1
    newest = full[-1].read_text(encoding="utf-8")
    assert "<form" not in newest and 'http-equiv="refresh"' not in newest and "<style>" in newest


# ---- 確認:真跑縮小版 F7 ----
@pytest.fixture
def f7(service):
    def make(base, demo_id, keys, state):
        made = Driver(base, demo_id, keys, state, user_env=os.environ, scenarios={
            "F7": Scenario("F7", "F7", 60, driver_module.make_f7(
                campaigns=30, limit=124, workers=3, confirm_cap_seconds=60))})
        made.verifier_command = ["true"]
        return made

    service.driver_factory = make
    service.start(("F7",), full=False)
    assert _wait(lambda: service.state().approval is not None, 120)
    yield service
    assert _wait(lambda: not service.running, 120)


def _approval_fields(service, **changes):
    reader = StateReader(service.state_db)
    try:
        _, request = reader.confirmation(service.current.demo_id)
    finally:
        reader.close()
    fields = {"token": service.token, "demo_id": service.current.demo_id,
              "proposal_hash": request.proposal_hash,
              "numbers_digest": numbers_digest(service.current.demo_id, request),
              **{f"confirm_{i}": "1" for i in range(len(request.numbers))}}
    fields.update(changes)
    return {k: v for k, v in fields.items() if v is not None}, request


def _f7_verdict(service):
    reader = StateReader(service.state_db)
    try:
        runs = [r for r in reader.scenario_runs(service.current.demo_id) if r.code == "F7"]
    finally:
        reader.close()
    return runs[0]


def test_nothing_refreshes_while_waiting_for_confirmation(running, f7):
    """[S1046] 等你確認時主頁與確認頁都沒有自動重讀。"""
    for path in ("/", "/approve"):
        status, _, body = _request(running, "GET", path)
        assert status == 200 and 'http-equiv="refresh"' not in body, path
    _post(running, "/approve", _approval_fields(f7)[0])


@pytest.mark.parametrize("change", [
    {"confirm_0": None},  # 少勾一個
    {"numbers_digest": "0" * 64},  # 數字摘要不是驅動程式記下的那一份
    {"proposal_hash": "f" * 64},
    {"demo_id": "another-demo"},
])
def test_an_approval_needs_every_computed_number_confirmed(running, f7, change):
    """[S1032] 每個數字都勾了、展示編號與提案雜湊與數字摘要都對得上才簽,否則拒,不簽。"""
    fields, _ = _approval_fields(f7, **change)
    status, _, _ = _post(running, "/approve", fields)
    assert status == 403
    assert f7.state().approval is not None  # 還在等人確認:沒有簽
    _post(running, "/approve", _approval_fields(f7)[0])


def test_the_approval_signature_takes_its_fields_from_the_demo_state_not_the_form(running, f7):
    """[S1033] 簽發的欄位取自驅動程式記下的確認請求:表單另外塞關卡與加額也不採用;確認人固定。"""
    from rtb.capabilitykit import APPROVAL_KEY_ENV
    from rtb.executor import approval
    from rtb.executor.inbox_store import BlockCode, ReadOnlyInbox

    fields, request = _approval_fields(f7, max_increase="999999",
                                       stage="budget_increase_too_large")
    keys, world = f7.current.keys, f7.current.driver.root / "F7"
    status, _, _ = _post(running, "/approve", fields)
    assert status == 303
    assert _wait(lambda: _f7_verdict(f7).status == "done", 120), _f7_verdict(f7)
    inbox = ReadOnlyInbox(world / "inbox.db")
    try:
        with inbox.read_transaction() as tx:
            from rtb.analyzer.task_store import TaskReader

            reader = TaskReader(world / "analyzer.db")
            try:
                proposal = next(r.proposal for r in reversed(reader.history(request.task_id))
                                if r.proposal is not None)
            finally:
                reader.close()
            token = inbox.latest_approval(tx, proposal, BlockCode(request.stage))
    finally:
        inbox.close()
    signed = approval.read(token, keys.signing_bytes(APPROVAL_KEY_ENV))
    assert signed.max_increase == request.max_increase and signed.stage.value == request.stage
    assert signed.approver == "demo-operator"
    assert signed.expires_at <= int(request.decision_expires_at.timestamp())


def test_the_server_rereads_the_proposal_before_signing(running, f7, monkeypatch):
    """[S1056] 簽之前重讀那筆建議:已經不在等人確認(例如到期結案)就拒,不簽。"""
    from rtb.executor.inbox_store import LifecycleKind

    monkeypatch.setattr(server_module, "_AWAITING", LifecycleKind.HANDED_OFF.value)
    fields, _ = _approval_fields(f7)
    status, _, body = _post(running, "/approve", fields)
    assert status == 409 and "no_longer_waiting" in body
    monkeypatch.undo()
    _post(running, "/approve", _approval_fields(f7)[0])


def test_a_trigger_after_a_confirmation_timeout_is_refused_until_the_demo_ends(running, service):
    """[S1061] 確認逾時之後、自動查核跑完之前:觸發一律拒;確認頁與送出確認說明已逾時。"""
    service.driver_factory = _factory(gate := Gate())
    try:
        _post(running, "/run", {"token": service.token})
        first = service.current.demo_id
        writer_request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached",
                                             10, datetime.now(UTC) + timedelta(hours=1),
                                             (("廣告", "c1"),))
        from rtb.demo.state_store import StateWriter

        writer = StateWriter(service.state_db, first)
        writer.set_confirmation("F7", writer_request)
        writer.clear_confirmation()  # 逾時:清掉確認表單,鎖照樣握著
        _post(running, "/run", {"token": service.token})
        assert service.current.demo_id == first
        assert _request(running, "GET", "/approve")[0] == 409
        assert _post(running, "/approve", {"token": service.token})[0] == 409
    finally:
        _finish(service, gate)


def test_only_a_single_known_scenario_code_selects_a_scenario():
    """[S1019] 只有恰好一個、而且是七個代碼之一的 scenario 才算選了;重複、空值、未知一律當沒選。"""
    from rtb.demo.state import ScenarioCode

    assert server_module._selected("scenario=F3") is ScenarioCode.F3
    for query in ("scenario=F3&scenario=F4", "scenario=", "scenario=F9", "", "tick=3"):
        assert server_module._selected(query) is None, query


def test_a_request_without_a_host_header_is_refused_even_on_http_1_0(running):
    """[S1017] 沒帶主機標頭的舊式請求也拒(展示伺服器要求一定帶本機主機標頭)。"""
    import socket

    with socket.create_connection(("127.0.0.1", running), timeout=5) as conn:
        conn.sendall(b"GET / HTTP/1.0\r\n\r\n")
        data = b""
        while chunk := conn.recv(4096):
            data += chunk
    assert data.startswith(b"HTTP/1.0 400") and b"script-src 'none'" in data
