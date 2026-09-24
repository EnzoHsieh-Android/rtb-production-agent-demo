"""一鍵展示伺服器(Phase 12 增量 2b):路由、觸發防護、同時一次、單一情境重跑、核可、報告。

多數測試換掉驅動程式的情境(很快跑完的假情境),真的核可流程用縮小版 F7 真跑。
"""

import contextlib
import hashlib
import http.client
import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest

from rtb.demo import driver as driver_module
from rtb.demo import server as server_module
from rtb.demo.driver import Driver, Scenario
from rtb.demo.present import numbers_digest
from rtb.demo.server import DemoService, serve
from rtb.demo.state_store import ConfirmationRequest, DecisionRow, StateReader

# 起伺服器子行程要把 src 放進 PYTHONPATH:CI 沒有安裝這個套件,pytest 的 pythonpath 設定只影響測試
# 行程自己
SRC = str(Path(server_module.__file__).resolve().parents[2])

FORM = {"Content-Type": "application/x-www-form-urlencoded", "Sec-Fetch-Site": "same-origin"}


class Gate:
    """假情境用的閘:情境本體等它打開才結束,測試可以看到「在跑」的樣子。"""

    def __init__(self):
        self.opened = threading.Event()

    def scenario(self, code):
        def run(world):
            world.state.record_decision(code, DecisionRow(  # 真驅動會留下目前節點
                "a_collect", ("a_receive", "a_collect"), "o", "r", datetime.now(UTC), "fake"))
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
    """預設用假驅動(代碼審 r1 t3:原本預設真驅動、沒有收尾,防線退化時會留下子行程);收尾時停掉
    正在跑的展示、等到不在跑。"""
    made = DemoService(tmp_path / "demos", tmp_path / "state.db", tmp_path / "reports",
                       driver_factory=_factory())
    yield made
    made.stop()
    assert _wait(lambda: not made.running)


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
    單一情境重跑的另外標記、不佔那 20 份。清理只刪檔名完全符合報告格式的檔,不碰使用者放的檔與
    符號連結;報告只給自己讀寫、目錄權限收緊(代碼審 r1 v7/s4)。"""
    import stat

    service.tighten_reports = True  # 預設的報告目錄才收緊(代碼審 r2 s3)
    service.reports.mkdir(parents=True, mode=0o755)
    service.reports.chmod(0o755)
    for i in range(25):
        (service.reports / f"demo-full-20000101-{i:06d}-abcdef01.html").write_text(
            "舊", encoding="utf-8")
    notes = service.reports / "demo-full-1999-notes.html"  # 名字像、格式不符:不刪
    notes.write_text("使用者的筆記", encoding="utf-8")
    target = service.reports.parent / "elsewhere.html"
    target.write_text("別處", encoding="utf-8")
    link = service.reports / "demo-full-19990101-000000-abcdef01.html"  # 最舊、但是符號連結
    link.symlink_to(target)
    _full_then_rerun(service, "F1")
    full = sorted(p for p in service.reports.glob("demo-full-2*.html"))
    reruns = sorted(service.reports.glob("demo-rerun-*.html"))
    assert len(full) == 20 and len(reruns) == 1
    assert notes.exists() and link.is_symlink() and target.exists()
    newest = full[-1].read_text(encoding="utf-8")
    assert "<form" not in newest and 'http-equiv="refresh"' not in newest and "<style>" in newest
    assert stat.S_IMODE(full[-1].stat().st_mode) == 0o600
    assert stat.S_IMODE(service.reports.stat().st_mode) == 0o700
    assert not list(service.reports.glob(".*.tmp"))  # 先寫暫存檔再換名,沒留下半成品


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


def _signed(service):
    """F7 暫存資料庫裡有沒有這次展示簽的核可。"""
    from rtb.executor.inbox_store import ReadOnlyInbox

    inbox = ReadOnlyInbox(service.current.driver.root / "F7" / "inbox.db")
    try:
        with inbox.read_transaction() as tx:
            return tx.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
    finally:
        inbox.close()


def test_the_server_rereads_the_proposal_before_signing(running, f7, monkeypatch):
    """[S1056] 簽之前從收件口重讀那筆建議(跟管理工具同一支 find_proposal):已經不在等人核可、停的
    不是記下的那一關、內容雜湊不同、已經過期,都拒絕、說明原因、不簽。"""
    from dataclasses import replace

    from rtb.executor.inbox_store import BlockCode, InboxStore

    real = InboxStore.find_proposal
    for change, code in (("gone", "no_longer_waiting"), ("hash", "proposal_changed"),
                         ("stage", "stage_changed")):
        def fake(store, task_id, revision, change=change):
            waiting = real(store, task_id, revision)
            if change == "gone" or waiting is None:
                return None
            if change == "hash":
                return replace(waiting, message=replace(waiting.message,
                                                        content_hash="0" * 64))
            other = next(b for b in (BlockCode.AGGREGATE_LIMIT_REACHED,
                                     BlockCode.BUDGET_INCREASE_TOO_LARGE)
                         if b is not waiting.stage)
            return replace(waiting, stage=other)

        monkeypatch.setattr(InboxStore, "find_proposal", fake)
        status, _, body = _post(running, "/approve", _approval_fields(f7)[0])
        assert (status, code in body) == (409, True), (change, body)
        assert _signed(f7) == 0
    monkeypatch.undo()
    _post(running, "/approve", _approval_fields(f7)[0])


def test_an_expired_proposal_is_not_signed(running, f7):
    """[S1056] 那筆建議已經過了決策到期:拒絕、說明過期、不簽(M17:原本拿掉這道檢查測不出來)。"""
    from dataclasses import replace

    from rtb.demo.state_store import StateWriter

    fields, request = _approval_fields(f7)
    reader = StateReader(f7.state_db)
    try:
        code, _ = reader.confirmation(f7.current.demo_id)
    finally:
        reader.close()
    past = replace(request, decision_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    StateWriter(f7.state_db, f7.current.demo_id).set_confirmation(
        code, past, datetime.now(UTC) + timedelta(minutes=5))
    status, _, body = _post(running, "/approve", _approval_fields(f7)[0])
    assert status == 409 and "proposal_expired" in body and _signed(f7) == 0
    StateWriter(f7.state_db, f7.current.demo_id).set_confirmation(
        code, request, datetime.now(UTC) + timedelta(minutes=5))
    assert _post(running, "/approve", fields)[0] == 303


def test_every_confirmation_box_must_be_ticked(running, f7):
    """[S1032] 每一格確認框都要勾:少勾任何一格(含最後一格)都回 403、不簽(M18:原本只試第 0 格)。"""
    _, request = _approval_fields(f7)
    for index in range(len(request.numbers)):
        fields, _ = _approval_fields(f7, **{f"confirm_{index}": None})
        assert _post(running, "/approve", fields)[0] == 403, index
        assert _signed(f7) == 0
    _post(running, "/approve", _approval_fields(f7)[0])


def test_an_approval_without_a_valid_token_or_from_another_site_signs_nothing(running, f7):
    """[S1015] POST /approve 也驗表單隨機值與同源:沒帶、帶錯、別的網站送的,都回 403、不簽(M08)。"""
    fields, _ = _approval_fields(f7)
    no_token = {k: v for k, v in fields.items() if k != "token"}
    for body, headers in ((no_token, FORM), ({**fields, "token": "wrong"}, FORM),
                          (fields, {**FORM, "Sec-Fetch-Site": "cross-site"})):
        assert _post(running, "/approve", body, headers)[0] == 403
        assert _signed(f7) == 0
    _post(running, "/approve", fields)


def test_the_same_confirmation_cannot_be_signed_twice(running, f7):
    """[代碼審 r1 x3/s5/v5] 同一張確認只簽一次:再送一次同一張表單不再簽;確認窗記成已確認過
    (第二次送出轉回展示,見代碼審 r2 v4)。"""
    fields, _ = _approval_fields(f7)
    assert _post(running, "/approve", fields)[0] == 303
    _post(running, "/approve", fields)
    assert _signed(f7) == 1
    reader = StateReader(f7.state_db)
    try:
        assert reader.confirmation_refusal(f7.current.demo_id) == "already_confirmed"
    finally:
        reader.close()


def test_a_signed_approval_expires_within_five_minutes(running, f7):
    """[S1033] 核可到期取 min(提案決策到期, 現在 + 300 秒):提案還有很久才到期時,核可最多五分鐘
    (M21:原本只驗不超過提案到期)。"""
    from rtb.capabilitykit import APPROVAL_KEY_ENV
    from rtb.executor import approval
    from rtb.executor.inbox_store import ReadOnlyInbox

    fields, request = _approval_fields(f7)
    assert request.decision_expires_at > datetime.now(UTC) + timedelta(minutes=6)
    before = int(datetime.now(UTC).timestamp())
    assert _post(running, "/approve", fields)[0] == 303
    inbox = ReadOnlyInbox(f7.current.driver.root / "F7" / "inbox.db")
    try:
        with inbox.read_transaction() as tx:
            token = tx.conn.execute("SELECT token FROM approvals").fetchone()[0]
    finally:
        inbox.close()
    signed = approval.read(token, f7.current.keys.signing_bytes(APPROVAL_KEY_ENV))
    assert signed.expires_at <= before + server_module.APPROVAL_SECONDS + 1


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


# ---- 代碼審 r1(Phase 12 增量 2)----
def test_a_full_run_in_progress_shows_none_of_the_earlier_reruns(service):
    """[代碼審 r1 v1/s1/x1] 先全部跑一次、再重跑 F3 與 F5,然後開新的全部跑一次:新的在跑時整頁只看
    這一次,不把舊的重跑結果換上來(原本 F3、F5 顯示「結果符合預期」)。"""
    service.driver_factory = _factory(gate := Gate())
    gate.opened.set()
    service.start(driver_module.ALL_CODES, full=True)
    assert _wait(lambda: not service.running)
    for code in ("F3", "F5"):
        service.start((code,), full=False)
        assert _wait(lambda: not service.running)
    gate.opened.clear()
    try:
        service.start(driver_module.ALL_CODES, full=True)
        now = service.current.demo_id
        assert _wait(lambda: service.state().current is not None)
        shown = service.state()
        assert {s.source_demo_id for s in shown.scenarios} <= {now, None}
        assert shown.current.scenario.value == "F1"
    finally:
        _finish(service, gate)


def test_progress_during_a_rerun_points_at_the_running_one(service):
    """[代碼審 r1 v1] 已經重跑過 F3、F5,再重跑 F3:「現在進度」指向正在跑的這一次 F3,不被舊的
    F5 蓋掉。"""
    service.driver_factory = _factory(gate := Gate())
    gate.opened.set()
    service.start(driver_module.ALL_CODES, full=True)
    assert _wait(lambda: not service.running)
    for code in ("F3", "F5"):
        service.start((code,), full=False)
        assert _wait(lambda: not service.running)
    gate.opened.clear()
    try:
        service.start(("F3",), full=False)
        assert _wait(lambda: service.state().current is not None)
        assert service.state().current.scenario.value == "F3"
    finally:
        _finish(service, gate)


def test_a_full_run_in_progress_shows_only_its_own_verifier(running, service):
    """[代碼審 r1 x2][S1021] 完整展示在跑:驗證器只顯示這一次的,還沒跑到寫「這次還沒查核」;單一情境
    重跑沿用上一次完整展示的結果。"""
    full, _ = _full_then_rerun(service, "F2")
    assert service.state().verifier.demo_id == full
    service.driver_factory = _factory(gate := Gate())
    try:
        service.start(driver_module.ALL_CODES, full=True)
        shown = service.state()
        assert shown.verifier is None and shown.verifier_pending
        assert "這次還沒查核" in _request(running, "GET", "/")[2]
    finally:
        _finish(service, gate)


def test_nothing_is_current_once_the_demo_has_ended(running, service):
    """[代碼審 r1 s2/p7] 跑完就沒有「現在進度」:狀態裡是空的,頁面也不畫。"""
    _full_then_rerun(service, "F1")
    assert service.state().current is None
    assert 'class="current-progress"' not in _request(running, "GET", "/")[2]


def test_a_crashed_driver_marks_this_demo_incomplete_and_saves_a_report(service):
    """[代碼審 r1 v4] 驅動程式自己出例外:這一次的情境標成沒跑完、寫明原因、另存報告,頁面顯示這一次,
    不默默退回上一次。"""
    service.driver_factory = _factory(crash=True)
    run = service.start(driver_module.ALL_CODES, full=True)
    assert _wait(lambda: not service.running)
    shown = service.state()
    assert all(s.source_demo_id == run.demo_id for s in shown.scenarios)
    assert all("驅動程式出錯" in (s.incomplete_reason or "") for s in shown.scenarios)
    assert list(service.reports.glob(f"demo-full-{run.demo_id}.html"))


def test_the_running_flag_is_released_even_when_saving_the_report_fails(service, tmp_path):
    """[S1052] 在跑旗標在 finally 放:收尾(另存報告)出事也照樣放掉,下一次觸發可以啟動(M14)。"""
    service.reports = tmp_path / "not-a-directory"
    service.reports.write_text("檔案佔住了報告目錄的位置", encoding="utf-8")
    service.start(driver_module.ALL_CODES, full=True)
    assert _wait(lambda: not service.running)
    service.start(("F1",), full=False)
    assert _wait(lambda: not service.running)


def test_the_report_from_the_server_links_the_stylesheet_its_policy_allows(running):
    """[代碼審 r1 p2/v3/s3/t7] 經伺服器送的報告連同源樣式表(內容安全政策 style-src 'self' 擋內嵌
    樣式);另存的檔案才內嵌。"""
    _, headers, body = _request(running, "GET", "/report")
    assert "style-src 'self'" in headers["Content-Security-Policy"]
    assert "<style>" not in body and 'rel="stylesheet" href="/static/demo.css"' in body


def test_every_response_is_not_cached_sniffed_or_referred(running):
    """[代碼審 r1 s6] 每個回應(含錯誤頁)都帶不快取、不猜內容型別、不把來源網址送到別的網站的
    標頭。"""
    for method, path in (("GET", "/"), ("GET", "/approve"), ("GET", "/nothing"),
                         ("POST", "/run")):
        _, headers, _ = _request(running, method, path)
        assert headers["Cache-Control"] == "no-store", path
        assert headers["X-Content-Type-Options"] == "nosniff", path
        # same-origin:不把來源網址送到別的網站,同源表單照常帶 Origin(代碼審 r2 g1)
        assert headers["Referrer-Policy"] == "same-origin", path


@pytest.mark.parametrize(("error", "status", "code"), [
    ("busy", 503, "busy"), ("approval", 409, "expiry_invalid"), ("signing", 409, "unreadable"),
])
def test_approval_errors_are_answered_with_their_reason(running, service, monkeypatch,
                                                        error, status, code):
    """[代碼審 r1 a3] 核可路上的領域例外照既有管理工具的分法回:收件口忙碌 503 可重試,拒簽帶原因
    代碼 409,不變成 500。"""
    from rtb.executor.approval import ApprovalRefused
    from rtb.executor.capability_signer import SigningRefused
    from rtb.executor.inbox_store import InboxBusy

    raised = {"busy": InboxBusy("忙"), "approval": ApprovalRefused("expiry_invalid"),
              "signing": SigningRefused("unreadable")}[error]

    def refuse(_form):
        raise raised

    monkeypatch.setattr(service, "approve", refuse)
    got, _, body = _post(running, "/approve", {"token": service.token})
    assert got == status and code in body


def test_reports_go_under_the_account_home_computed_when_used(monkeypatch, tmp_path):
    """[代碼審 r1 a4] 報告目錄跟花費帳同一個「家」(帳號家目錄,不看 HOME),用到時才算。"""
    from rtb import modelledger_view

    monkeypatch.setattr(modelledger_view, "account_home", lambda: tmp_path / "account")
    assert server_module.default_reports() == tmp_path / "account" / ".rtb" / "demo-reports"
    assert not hasattr(server_module, "DEFAULT_REPORTS")


def test_two_triggers_racing_between_the_check_and_the_mark_start_only_one(tmp_path):
    """[S1014] 讀「在跑」時讓出執行權(競態一定發生):檢查與標記在同一把鎖裡,八個同時到的觸發只有
    一個啟動(M13:原本拿掉鎖也測不出來)。"""
    class Yielding(DemoService):
        @property
        def running(self):
            seen = self.__dict__.get("_flag", False)
            time.sleep(0.01)  # 讀到之後、用到之前讓出執行權:沒有鎖時別的觸發一定也讀到舊值
            return seen

        @running.setter
        def running(self, value):
            self.__dict__["_flag"] = value

    service = Yielding(tmp_path / "demos", tmp_path / "state.db", tmp_path / "reports",
                       driver_factory=_factory(gate := Gate()))
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


def test_an_unknown_scenario_query_leaves_no_trace_of_its_value(running):
    """[S1019] 查詢參數不是七個代碼之一:值不以任何形式出現在頁面上(原樣或跳脫後都沒有;M40)。"""
    body = _request(running, "GET", "/?scenario=%3Cb%3Ezqx7marker")[2]
    assert "zqx7marker" not in body


def test_a_confirmation_after_the_timeout_says_it_timed_out(running, service):
    """[S1061] 確認逾時之後,確認頁與送出確認都說明「已逾時」(原因代碼分得出逾時與沒有確認)。"""
    from rtb.demo.state_store import StateWriter

    service.driver_factory = _factory(gate := Gate())
    try:
        _post(running, "/run", {"token": service.token})
        writer = StateWriter(service.state_db, service.current.demo_id)
        writer.set_confirmation("F7", ConfirmationRequest(
            "t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
            datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),)))
        writer.clear_confirmation()
        for method, fields in (("GET", None), ("POST", {"token": service.token})):
            status, _, body = (_request(running, "GET", "/approve") if method == "GET"
                               else _post(running, "/approve", fields))
            assert status == 409 and "confirmation_timed_out" in body, method
    finally:
        _finish(service, gate)


def test_stopping_the_server_mid_demo_leaves_no_child_processes(tmp_path):
    """[代碼審 r1 v2/t3] 伺服器在展示途中被 SIGTERM 或 Ctrl-C 結束:先停掉展示、等驅動執行緒收完,
    它起的子行程(平台、收件口、執行端、分析端)一個都不留。"""
    import signal
    import subprocess
    import sys

    for signum in (signal.SIGTERM, signal.SIGINT):
        work = tmp_path / signum.name
        popen = subprocess.Popen(
            [sys.executable, "-m", "rtb.demo.server", "--work-dir", str(work),
             "--reports", str(work / "reports")],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env={**os.environ, "PYTHONPATH": SRC})
        try:
            port = int(popen.stdout.readline().strip().removeprefix("PORT="))
            page = _request(port, "GET", "/")[2]
            token = re.search(r'name="token" value="([^"]+)"', page).group(1)
            _post(port, "/run/scenario", {"token": token, "scenario": "F1"})
            assert _wait(lambda work=work: len(_children(work)) >= 2, 60), "子行程沒起來"
            popen.send_signal(signum)
            popen.wait(90)
            assert _wait(lambda work=work: not _children(work), 10), _children(work)
        finally:
            if popen.poll() is None:
                popen.kill()
            for pid in _children(work):
                os.kill(pid, signal.SIGKILL)


def _children(work):
    """命令列裡帶這個工作目錄的行程(伺服器起的子行程都把資料庫放在這底下)。"""
    import subprocess

    listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True,  # noqa: S607
                             text=True, check=False).stdout
    return [int(line.split(None, 1)[0]) for line in listing.splitlines()
            if str(work) in line and "rtb.demo.server" not in line]


# ---- 代碼審 r2(Phase 12 增量 2)----
def test_a_form_sent_the_way_a_browser_sends_it_under_the_page_policy_is_accepted(running,
                                                                                 service):
    """[代碼審 r2 g1] 頁面回應的來源網址政策不能讓瀏覽器把表單的 Origin 送成 null(no-referrer 會):
    政策是 same-origin,同源表單照常帶真的 Origin,同源檢查放行;Origin: null 照樣拒。"""
    _, headers, _ = _request(running, "GET", "/")
    assert headers["Referrer-Policy"] == "same-origin"
    service.driver_factory = _factory(gate := Gate())
    try:
        browser = {"Content-Type": FORM["Content-Type"], "Sec-Fetch-Site": "same-origin",
                   "Origin": f"http://127.0.0.1:{running}"}
        assert _post(running, "/run", {"token": service.token}, browser)[0] == 303
        assert service.running
    finally:
        _finish(service, gate)
    nulled = {**FORM, "Origin": "null"}
    assert _post(running, "/run", {"token": service.token}, nulled)[0] == 403


def test_a_stop_while_f7_waits_for_confirmation_is_not_recorded_as_nobody_confirming(f7):
    """[代碼審 r2 v1] 伺服器收尾取消展示:被取消的情境寫「展示被停止」,不寫成情境自己的失敗(原本
    F7 記成「沒有人確認」);被取消的展示不換成最新一次、不另存報告。"""
    before = (f7.full_demo_id, dict(f7.reruns))
    f7.stop()
    assert _wait(lambda: not f7.running, 60)
    verdict = _f7_verdict(f7)
    assert verdict.status == "incomplete" and verdict.reason != "沒有人確認"
    assert "展示被停止" in verdict.reason
    assert (f7.full_demo_id, dict(f7.reruns)) == before
    assert not list(f7.reports.glob("demo-*.html"))


def test_no_demo_starts_once_the_server_is_shutting_down(service):
    """[代碼審 r2 v2] 收尾開始之後才到的觸發(處理中的 POST /run)不再啟動展示。"""
    service.stop()
    with pytest.raises(server_module.RequestRejected) as refused:
        service.start(driver_module.ALL_CODES, full=True)
    assert refused.value.status == 503 and not service.running


@pytest.mark.parametrize("signals", [("SIGHUP",), ("SIGINT", "SIGINT"), ("SIGTERM", "SIGINT")])
def test_hanging_up_or_signalling_twice_still_leaves_no_child_processes(tmp_path, signals):
    """[代碼審 r2 v2/s1] 關掉終端機(SIGHUP)、收尾中再按一次 Ctrl-C 或再送 SIGTERM:收尾照樣做完,
    子行程一個都不留。"""
    import signal
    import subprocess
    import sys

    work = tmp_path / "work"
    popen = subprocess.Popen(
        [sys.executable, "-m", "rtb.demo.server", "--work-dir", str(work),
         "--reports", str(work / "reports")],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "PYTHONPATH": SRC})
    try:
        port = int(popen.stdout.readline().strip().removeprefix("PORT="))
        page = _request(port, "GET", "/")[2]
        token = re.search(r'name="token" value="([^"]+)"', page).group(1)
        _post(port, "/run/scenario", {"token": token, "scenario": "F7"})  # 行程最多、收得最久
        assert _wait(lambda: len(_children(work)) >= 8, 60), "子行程沒起來"
        for index, name in enumerate(signals):
            if index:
                time.sleep(0.1)
            popen.send_signal(getattr(signal, name))
        popen.wait(90)
        assert _wait(lambda: not _children(work), 10), _children(work)
    finally:
        if popen.poll() is None:
            popen.kill()
        for pid in _children(work):
            os.kill(pid, signal.SIGKILL)


def test_confirming_twice_takes_you_back_to_the_demo(running, f7):
    """[代碼審 r2 v4] 確認頁連按兩次:第二次不停在英文錯誤頁,轉回展示(這次展示已經確認過);只簽
    一張。"""
    fields, _ = _approval_fields(f7)
    assert _post(running, "/approve", fields)[0] == 303
    status, headers, _ = _post(running, "/approve", fields)
    assert (status, headers.get("Location")) == (303, "/#current")
    assert _signed(f7) == 1


@pytest.mark.parametrize("line", [b"GARBAGE\r\n\r\n", b"GET / HTTP/9.9\r\n\r\n",
                                  b"GET /\r\n\r\n"])
def test_a_broken_request_line_still_gets_the_safety_headers(running, line):
    """[代碼審 r2 s2] 請求行壞掉(會被當成 HTTP/0.9)的錯誤回應也帶狀態行與安全標頭。"""
    import socket

    with socket.create_connection(("127.0.0.1", running), timeout=5) as conn:
        conn.sendall(line)
        data = b""
        while chunk := conn.recv(4096):
            data += chunk
    head = data.split(b"\r\n\r\n", 1)[0]
    assert head.startswith(b"HTTP/1.") and b" 500 " not in head.split(b"\r\n")[0]
    for name in (b"Content-Security-Policy", b"Cache-Control: no-store",
                 b"X-Content-Type-Options: nosniff"):
        assert name in head, name


def test_a_reports_directory_the_user_named_is_left_as_it_is(service, tmp_path):
    """[代碼審 r2 s3] 使用者用 --reports 指定的目錄不擅自改權限;只收緊預設目錄,而且不跟著符號連結;
    另存失敗讓頁面看得到。"""
    import stat

    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    service.reports, service.tighten_reports = shared, False
    _full_then_rerun(service, "F1")
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert list(shared.glob("demo-full-*.html"))
    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    real.chmod(0o755)
    link = tmp_path / "linked"
    link.symlink_to(real)
    service.reports, service.tighten_reports = link, True
    _full_then_rerun(service, "F2")
    assert stat.S_IMODE(real.stat().st_mode) == 0o755  # 符號連結不跟著收緊
    blocked = tmp_path / "blocked"
    blocked.write_text("檔案佔住目錄的位置", encoding="utf-8")
    service.reports = blocked
    _full_then_rerun(service, "F3")
    assert "報告沒存成" in service.state().report_note


# ---- 代碼審 r3(Phase 12 增量 2)----
def _server_on_a_terminal(work):
    """在假終端機裡起伺服器(它是那個終端機的 session leader,stdout、stderr 都接在終端機上),回
    (行程編號, 終端機 master, 連接埠)。"""
    import pty
    import sys

    pid, master = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-m", "rtb.demo.server",  # noqa: S606 - 測試起專案內的伺服器
                                   "--work-dir", str(work), "--reports", str(work / "reports")],
                  {**os.environ, "PYTHONPATH": SRC})
    seen = b""
    while b"\n" not in seen:
        seen += os.read(master, 1024)
    return pid, master, int(re.search(rb"PORT=(\d+)", seen).group(1))


def test_closing_the_terminal_leaves_no_child_processes(tmp_path):
    """[代碼審 r3 v1] 真的關掉終端機:終端機掛斷(stderr 寫不出去)、殼層再轉送一次 SIGHUP,收尾照樣
    做完、子行程一個都不留(原本收尾中的提示印到掛斷的終端機丟 EIO,打斷等待,11 支孤兒)。"""
    import signal

    work = tmp_path / "work"
    pid, master, port = _server_on_a_terminal(work)
    try:
        page = _request(port, "GET", "/")[2]
        token = re.search(r'name="token" value="([^"]+)"', page).group(1)
        _post(port, "/run/scenario", {"token": token, "scenario": "F7"})
        assert _wait(lambda: len(_children(work)) >= 8, 60), "子行程沒起來"
        os.close(master)  # 關掉終端機:核心送 SIGHUP 給 session leader,之後寫終端機會 EIO
        master = -1
        time.sleep(0.1)
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGHUP)  # 殼層轉送的第二個 SIGHUP
        assert _wait(lambda: _reaped(pid), 90)
        assert _wait(lambda: not _children(work), 10), _children(work)
    finally:
        if master >= 0:
            os.close(master)
        with contextlib.suppress(ProcessLookupError, ChildProcessError):
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        for child in _children(work):
            os.kill(child, signal.SIGKILL)


def _reaped(pid):
    with contextlib.suppress(ChildProcessError):
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == pid
    return True


def test_two_signals_arriving_together_still_leave_no_child_processes(tmp_path):
    """[代碼審 r3 v2] SIGTERM 和 SIGHUP 同時到:第二個不能在收尾開始前把收尾打斷。"""
    import signal
    import subprocess
    import sys

    work = tmp_path / "work"
    popen = subprocess.Popen(
        [sys.executable, "-m", "rtb.demo.server", "--work-dir", str(work),
         "--reports", str(work / "reports")],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "PYTHONPATH": SRC})
    try:
        port = int(popen.stdout.readline().strip().removeprefix("PORT="))
        page = _request(port, "GET", "/")[2]
        token = re.search(r'name="token" value="([^"]+)"', page).group(1)
        _post(port, "/run/scenario", {"token": token, "scenario": "F7"})
        assert _wait(lambda: len(_children(work)) >= 8, 60), "子行程沒起來"
        os.kill(popen.pid, signal.SIGTERM)
        os.kill(popen.pid, signal.SIGHUP)
        popen.wait(90)
        assert _wait(lambda: not _children(work), 10), _children(work)
    finally:
        if popen.poll() is None:
            popen.kill()
        for pid in _children(work):
            os.kill(pid, signal.SIGKILL)


def test_a_stop_that_lands_while_a_demo_is_being_set_up_leaves_nothing_running(service,
                                                                                 monkeypatch):
    """[代碼審 r3 c1/s1] 收尾剛好落在「通過收尾中檢查」與「記下這一次」之間:這一次不開跑(或被取消
    並等到收完),收尾返回之後沒有驅動執行緒還在跑。"""
    from rtb.demo.state_store import StateWriter

    service.driver_factory = _factory(gate := Gate())
    real = StateWriter.record_demo
    stopper = []

    def slow(self, *, full):
        stopper.append(threading.Thread(target=service.stop, kwargs={"timeout": 5}))
        stopper[0].start()
        time.sleep(0.3)  # 把窗口拉長:收尾在這段時間裡讀目前這一次
        real(self, full=full)

    monkeypatch.setattr(StateWriter, "record_demo", slow)
    try:
        with pytest.raises(server_module.RequestRejected):
            service.start(driver_module.ALL_CODES, full=True)
        stopper[0].join(10)
        assert not service.running
        assert service.thread is None or not service.thread.is_alive()
    finally:
        gate.opened.set()


def _hold_signing(monkeypatch, outcome):
    """讓第一次簽發停在預留之後,等測試放行;outcome 是 None 就簽成功,不然丟那個例外。"""
    reached, release = threading.Event(), threading.Event()
    real = server_module._sign

    def held(*args):
        reached.set()
        release.wait(30)
        if outcome is not None:
            raise outcome
        real(*args)

    monkeypatch.setattr(server_module, "_sign", held)
    return reached, release


@pytest.mark.parametrize("fails", [True, False])
def test_a_second_confirmation_while_the_first_is_signing_gets_the_first_ones_result(
        running, f7, monkeypatch, fails):
    """[代碼審 r3 c2/v4] 第一次還在簽時又按一次:第二次等第一次的結果再回應。第一次簽失敗,第二次
    不能回成功(原本回 303,使用者以為確認了);第一次簽成功,第二次轉回展示。只簽一張。"""
    from rtb.executor.inbox_store import InboxBusy

    reached, release = _hold_signing(monkeypatch, InboxBusy("忙") if fails else None)
    fields, _ = _approval_fields(f7)
    first = []
    worker = threading.Thread(target=lambda: first.append(_post(running, "/approve", fields)))
    worker.start()
    assert reached.wait(10)
    second = []
    other = threading.Thread(target=lambda: second.append(_post(running, "/approve", fields)))
    other.start()
    time.sleep(0.5)
    release.set()
    worker.join(30)
    other.join(30)
    if fails:
        assert first[0][0] == 503 and second[0][0] != 303, (first[0][0], second[0][0])
        assert "previous_confirmation_failed" in second[0][2] and _signed(f7) == 0
        monkeypatch.undo()
        _post(running, "/approve", _approval_fields(f7)[0])
    else:
        assert first[0][0] == 303 and second[0][:2][0] == 303 and _signed(f7) == 1


def test_an_interrupted_wait_during_shutdown_keeps_waiting(service, monkeypatch):
    """[代碼審 r3 v1] 收尾等驅動執行緒時被打斷(例如訊號處理丟例外):照樣接著等到它收完,不提早返回
    讓主行程結束(那樣子行程會變孤兒)。"""
    service.driver_factory = _factory(gate := Gate())
    service.start(driver_module.ALL_CODES, full=True)
    thread = service.thread
    real_join = type(thread).join
    calls = []

    def interrupted(self, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise RuntimeError("被打斷")  # 第一次等待被打斷(任何例外都一樣)
        return real_join(self, timeout)

    monkeypatch.setattr(type(thread), "join", interrupted)
    threading.Timer(0.5, gate.opened.set).start()
    service.stop(timeout=20)
    monkeypatch.undo()
    assert len(calls) >= 2 and not thread.is_alive()
