"""分析端驅動命令列(Phase 12 增量 1 [S1000] [S1001]):反覆領取待推進的任務,用正式的 DSP 與收件口
用戶端推進;一步最多兩次讀 DSP 或一次送件,呼叫次數 乘 逾時 乘 2 要小於租約才准啟動。"""

import ast
import io
import itertools
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rtb.analyzer import runner
from rtb.analyzer.task_store import LEASE_DURATION, TaskReader, TaskStore
from rtb.domain.task_state import TERMINAL_STATES, TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor.inbox_server import InboxServer

RUNNER = Path(runner.__file__)


@pytest.fixture
def services(tmp_path):
    dsp_store = CampaignStore(tmp_path / "dsp.db")
    dsp_store.seed_campaign("c1", budget=100)
    dsp_store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=0.5,
                           revenue=5.0)
    dsp_store.seed_campaign("c2", budget=100)
    dsp_store.seed_metrics("c2", "1h", impressions=500, clicks=12, conversions=1, spend=100.0,
                           revenue=5.0)
    dsp_store.close()
    dsp = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                    delay_seconds=0.0)
    inbox = InboxServer(tmp_path / "inbox.db", fault_injection=False)
    for server in (dsp, inbox):
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        yield (f"http://127.0.0.1:{dsp.server_address[1]}",
               f"http://127.0.0.1:{inbox.server_address[1]}")
    finally:
        for server in (dsp, inbox):
            server.shutdown()
            server.server_close()


def _argv(tmp_path, dsp_url, inbox_url, timeout="2"):
    return ["--db", str(tmp_path / "analyzer.db"), "--dsp-url", dsp_url, "--inbox-url", inbox_url,
            "--timeout-seconds", timeout, "--interval-seconds", "0.01"]


def _create(tmp_path, *tasks):
    store = TaskStore(tmp_path / "analyzer.db")
    try:
        for task_id, campaign in tasks:
            store.create_task(task_id, campaign, datetime.now(UTC))
    finally:
        store.close()


def _latest(tmp_path, task_id):
    reader = TaskReader(tmp_path / "analyzer.db")
    try:
        return reader.latest(task_id)
    finally:
        reader.close()


def test_the_analyzer_runner_advances_every_open_task_with_production_clients(tmp_path, services):
    """[S1000] 真的 DSP 與收件口:一件要加預算的任務走到交給執行,一件不慢的走到不調整並存下原因。"""
    _create(tmp_path, ("t1", "c1"), ("t2", "c2"))
    out = io.StringIO()

    code = runner.run(_argv(tmp_path, *services), max_rounds=12, out=out)

    assert code == 0
    assert out.getvalue().splitlines()[0] == runner.READY
    assert _latest(tmp_path, "t1").state is TaskState.HANDED_OFF
    no_action = _latest(tmp_path, "t2")
    assert no_action.state is TaskState.NO_ACTION
    reader = TaskReader(tmp_path / "analyzer.db")
    try:
        assert reader.no_action_reason("t2", no_action.seq) == "not_underpacing"
        assert [c.endpoint for c in reader.list_tool_calls("t1")]  # 用的是會記呼叫紀錄的版本
    finally:
        reader.close()


def test_the_analyzer_runner_uses_only_production_collaborators():
    """[S1000] 分析端驅動命令列只匯入分析端與共用的正式模組,沒有任何故障手段、測試或展示程式。"""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    # 從套件匯入子模組(from rtb.analyzer import x)也算那支子模組(Phase 13 增量 2 變異測試補的洞:
    # 原本只看 ImportFrom 的套件名,匯入分析端任何一支子模組都看不出來)
    modules += [f"{n.module}.{a.name}" for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module == "rtb.analyzer" for a in n.names]
    rtb_modules = {m for m in modules if m.startswith("rtb")}
    # Phase 13 增量 2 照計劃〈要改寫的既有合約〉加:模型閘道、AI 決策模組、小常數模組(與 AI 決策的
    # 模型無關詞彙模組);[S1000] 的合約文字不改
    assert rtb_modules <= {"rtb.analyzer", "rtb.analyzer.task_store", "rtb.analyzer.flow",
                           "rtb.analyzer.policy", "rtb.analyzer.dsp_client",
                           "rtb.analyzer.inbox_client", "rtb.analyzer.instrumented",
                           "rtb.domain.evidence", "rtb.domain.task_state",
                           "rtb.analyzer.modelgate", "rtb.analyzer.ai_judge",
                           "rtb.analyzer.investigation", "rtb.stepbudget"}, rtb_modules
    text = RUNNER.read_text(encoding="utf-8")
    assert not re.search(r"\bfault|X-Fault|rtb\.demo", text, re.IGNORECASE)


@pytest.mark.parametrize("timeout", ["15", "20", "60"])
def test_the_analyzer_runner_refuses_a_lease_too_short_for_its_calls(tmp_path, timeout):
    """[S1001] 呼叫次數 2 乘 逾時 乘 2 不小於租約 60 秒就拒絕啟動,什麼都不做。"""
    _create(tmp_path, ("t1", "c1"))
    err = io.StringIO()

    code = runner.run(_argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9", timeout),
                      max_rounds=1, err=err)

    assert code == runner.EXIT_UNSAFE_CONFIG
    assert "租約" in err.getvalue()
    assert _latest(tmp_path, "t1").state is TaskState.RECEIVED


def test_the_lease_inequality_matches_the_lease_constant():
    lease = LEASE_DURATION.total_seconds()
    assert runner.CALLS_PER_STEP * 14.9 * 2 < lease <= runner.CALLS_PER_STEP * 15 * 2


def test_the_analyzer_runner_stops_when_asked(tmp_path, services):
    _create(tmp_path, ("t1", "c1"))
    stop = threading.Event()
    stop.set()

    # 有上限:不看停止訊號時會推進而紅,不會卡住
    code = runner.run(_argv(tmp_path, *services), stop=stop, max_rounds=3)

    assert code == 0
    assert _latest(tmp_path, "t1").state is TaskState.RECEIVED


def test_open_tasks_are_the_ones_not_yet_at_an_end(tmp_path):
    store = TaskStore(tmp_path / "analyzer.db")
    try:
        now = datetime.now(UTC)
        for task_id in ("a", "b", "c"):
            store.create_task(task_id, "c1", now)
        row = store.latest("b")
        store.commit_step("b", row.seq, TaskState.COLLECTING_EVIDENCE, now)
        row = store.latest("c")
        store.commit_step("c", row.seq, TaskState.FAILED, now, error_detail="x")
        assert store.open_task_ids() == ("a", "b")
    finally:
        store.close()


# ---- 代碼審 r1 d4/t3/t5/t6/d1/d7/d8/a4 ----
@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "-1", "1e-9"])
def test_the_analyzer_runner_refuses_a_timeout_that_is_not_a_sane_number(tmp_path, timeout):
    """[S1001] 不是有限正數(或小到每次呼叫都失敗)的逾時也拒絕啟動,不印就緒。"""
    _create(tmp_path, ("t1", "c1"))
    out, err = io.StringIO(), io.StringIO()

    code = runner.run(_argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9", timeout),
                      max_rounds=1, out=out, err=err)

    assert code == runner.EXIT_UNSAFE_CONFIG and out.getvalue() == ""


@pytest.mark.parametrize("interval", ["nan", "-5", "inf"])
def test_the_analyzer_runner_refuses_a_bad_interval(tmp_path, interval):
    argv = _argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9")
    argv[argv.index("--interval-seconds") + 1] = interval
    assert runner.run(argv, max_rounds=1, err=io.StringIO()) == runner.EXIT_UNSAFE_CONFIG


def test_the_unsafe_config_exit_code_is_not_the_argparse_one():
    assert runner.EXIT_UNSAFE_CONFIG not in (0, 1, 2)


def test_the_runner_really_submits_through_the_inbox_and_records_every_call(tmp_path, services):
    """[S1000] 真的經收件口:收件口資料庫收到提案;呼叫紀錄有送件與兩個 DSP 端點。"""
    import sqlite3

    from rtb.analyzer.task_store import ToolEndpoint

    _create(tmp_path, ("t1", "c1"))
    runner.run(_argv(tmp_path, *services), max_rounds=6, out=io.StringIO())

    with sqlite3.connect(tmp_path / "inbox.db") as conn:
        assert conn.execute("SELECT task_id FROM proposals").fetchall() == [("t1",)]
    reader = TaskReader(tmp_path / "analyzer.db")
    try:
        endpoints = {c.endpoint for c in reader.list_tool_calls("t1")}
    finally:
        reader.close()
    assert {ToolEndpoint.INBOX_SUBMIT, ToolEndpoint.DSP_CAMPAIGN,
            ToolEndpoint.DSP_METRICS} <= endpoints


def test_the_timeout_reaches_the_clients(tmp_path):
    """[S1000][S1001] 逾時參數真的傳到用戶端:對一個只收連線、永不回應的位址,一步在逾時左右
    就結束。"""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    silent = f"http://127.0.0.1:{listener.getsockname()[1]}"
    try:
        _create(tmp_path, ("t1", "c1"))
        argv = _argv(tmp_path, silent, silent, timeout="0.3")
        runner.run(argv, max_rounds=1, out=io.StringIO(), err=io.StringIO())  # 收到工作 → 蒐集
        began = time.monotonic()
        runner.run(argv, max_rounds=1, out=io.StringIO(), err=io.StringIO())
        elapsed = time.monotonic() - began
    finally:
        listener.close()
    assert elapsed < 3, elapsed


def test_the_analyzer_runner_exits_cleanly_on_sigterm(tmp_path):
    import signal
    import subprocess
    import sys

    _create(tmp_path, ("t1", "c1"))
    child = subprocess.Popen(
        [sys.executable, "-m", "rtb.analyzer.runner", *_argv(tmp_path, "http://127.0.0.1:9",
                                                             "http://127.0.0.1:9")],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(RUNNER.parents[2])})
    try:
        assert child.stdout.readline().strip() == runner.READY
        child.send_signal(signal.SIGTERM)
        assert child.wait(5) == 0
    finally:
        child.kill()
        child.stdout.close()


def test_a_stop_request_is_honoured_before_the_next_task(tmp_path, monkeypatch):
    """停止旗標每件任務之前都看:第一件推進時要求停止,同一輪其餘的任務不再推進。"""
    _create(tmp_path, ("t1", "c1"), ("t2", "c1"), ("t3", "c1"))
    stop = runner.StopFlag()
    seen = []

    def one(_store, task_id, _args, _now):
        seen.append(task_id)
        stop.request()

    monkeypatch.setattr(runner, "_advance_one", one)
    runner.run(_argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9"), stop=stop,
               max_rounds=3, out=io.StringIO())
    assert seen == ["t1"]


def test_one_broken_task_does_not_stop_the_others(tmp_path, monkeypatch):
    _create(tmp_path, ("t1", "c1"), ("t2", "c1"))
    seen = []

    def one(_store, task_id, _args, _now):
        seen.append(task_id)
        if task_id == "t1":
            raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_advance_one", one)
    err = io.StringIO()
    code = runner.run(_argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9"),
                      max_rounds=1, out=io.StringIO(), err=err)
    assert code == 0 and seen == ["t1", "t2"] and "t1" in err.getvalue()


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_every_terminal_state_is_not_open_but_handed_off_is(tmp_path, terminal):
    store = TaskStore(tmp_path / "analyzer.db")
    try:
        now = datetime.now(UTC)
        store.create_task("a", "c1", now)
        store.create_task("b", "c1", now)
        _walk_to(store, "a", terminal, now)
        _walk_to(store, "b", TaskState.HANDED_OFF, now)
        assert store.open_task_ids() == ("b",)
    finally:
        store.close()


_PATHS = {
    TaskState.COMPLETED: [TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING, TaskState.PROPOSED,
                          TaskState.HANDED_OFF, TaskState.COMPLETED],
    TaskState.FAILED: [TaskState.FAILED],
    TaskState.BLOCKED: [TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING, TaskState.PROPOSED,
                        TaskState.HANDED_OFF, TaskState.BLOCKED],
    TaskState.NO_ACTION: [TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING, TaskState.NO_ACTION],
    TaskState.SUPERSEDED: [TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING,
                           TaskState.PROPOSED, TaskState.HANDED_OFF, TaskState.SUPERSEDED],
    TaskState.HANDED_OFF: [TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING,
                           TaskState.PROPOSED, TaskState.HANDED_OFF],
}


def _walk_to(store, task_id, target, now):
    from tests.analyzer.conftest import make_proposal

    for state in _PATHS[target]:
        extra = {}
        if state in (TaskState.PROPOSED, TaskState.HANDED_OFF):
            extra["proposal"] = make_proposal(task_id=task_id, campaign_id="c1")
        if state in (TaskState.FAILED, TaskState.BLOCKED):
            extra["error_detail"] = "x"
        store.commit_step(task_id, store.latest(task_id).seq, state, now, **extra)


def test_a_step_on_a_row_that_moved_on_does_nothing(tmp_path, services, monkeypatch):
    """[代碼審 r1 d1] 驅動先讀列、推進函式再讀列,中間被別的驅動推進時,這一步什麼都不做:送件紀錄
    不會記到舊列上。"""
    from rtb.analyzer import flow, instrumented, policy

    _create(tmp_path, ("t1", "c1"))
    argv = _argv(tmp_path, *services)
    runner.run(argv, max_rounds=3, out=io.StringIO())  # 收到 → 蒐集 → 分析 → 已提案
    store = TaskStore(tmp_path / "analyzer.db")
    assert store.latest("t1").state is TaskState.PROPOSED
    real = instrumented.dsp_evidence_source

    def meanwhile(own_store, url, timeout):
        other = TaskStore(tmp_path / "analyzer.db")
        try:  # 別的驅動在這兩次讀列之間把工作推到已交給執行
            flow.advance(other, "t1", real(other, url, timeout), policy.decide,
                         instrumented.InstrumentedSubmit(
                             other, runner.inbox_client.make_client(services[1], 2),
                             runner.ToolEndpoint.INBOX_SUBMIT, other.latest("t1")),
                         datetime.now(UTC), owner="other")
        finally:
            other.close()
        return real(own_store, url, timeout)

    monkeypatch.setattr(runner.instrumented, "dsp_evidence_source", meanwhile)
    args = runner._parse(argv)
    runner._advance_one(store, "t1", args, datetime.now(UTC))
    submits = [c for c in store.list_tool_calls("t1")
               if c.endpoint is runner.ToolEndpoint.INBOX_SUBMIT]
    store.close()
    assert len(submits) == 1  # 只有別的驅動那一次;這一步沒有在舊列上重送


def test_waiting_tasks_back_off_instead_of_writing_every_round(tmp_path, services):
    """[代碼審 r1 d7] 已交給執行、收件口還在待處理的任務沒有進展時退避,不會每輪都寫兩列租約與一筆
    送件紀錄。"""
    import sqlite3

    _create(tmp_path, ("t1", "c1"))
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    runner.run(_argv(tmp_path, *services), max_rounds=60, out=io.StringIO(), sleep=sleep,
               monotonic=lambda: clock["now"])
    with sqlite3.connect(tmp_path / "analyzer.db") as conn:
        leases = conn.execute("SELECT count(*) FROM task_leases").fetchone()[0]
    # 走到已交給執行約 5 步(10 列);之後 55 輪沒有退避會再多 110 列
    assert leases < 40, leases


def test_the_default_owner_is_unique_per_process():
    args = runner._parse(["--db", "x", "--dsp-url", "u", "--inbox-url", "u"])
    assert args.owner is None
    assert runner.default_owner() != "analyzer-runner" and "-" in runner.default_owner()


def test_the_timeout_reaches_the_inbox_client_too(tmp_path, services):
    """[S1001] 送件那一步的逾時也照參數:收件口只收連線不回應,送件那一步在逾時左右結束。"""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    silent = f"http://127.0.0.1:{listener.getsockname()[1]}"
    try:
        _create(tmp_path, ("t1", "c1"))
        argv = _argv(tmp_path, services[0], silent, timeout="0.3")
        runner.run(argv, max_rounds=3, out=io.StringIO(), err=io.StringIO())  # 走到已提案
        assert _latest(tmp_path, "t1").state is TaskState.PROPOSED
        began = time.monotonic()
        runner.run(argv, max_rounds=1, out=io.StringIO(), err=io.StringIO())  # 送件
        elapsed = time.monotonic() - began
    finally:
        listener.close()
    assert elapsed < 3, elapsed


# ---- 代碼審 r2 v3/n3/n4:休息可被打斷、間隔有上限、出錯也退避、退避有上限、查操作也照逾時 ----
@pytest.mark.parametrize("interval", ["1e308", "61"])
def test_the_analyzer_runner_refuses_an_interval_longer_than_the_lease(tmp_path, interval):
    """[v3] 間隔要有上限(不超過租約):1e308 原本過了守衛、印了就緒才在休息時崩掉。"""
    argv = _argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9")
    argv[argv.index("--interval-seconds") + 1] = interval
    out = io.StringIO()

    def rest(_seconds):  # 放行了就會進休息:當場失敗,不等 1e308 秒
        pytest.fail("不該啟動")

    code = runner.run(argv, max_rounds=1, out=out, err=io.StringIO(), sleep=rest)
    assert code == runner.EXIT_UNSAFE_CONFIG and out.getvalue() == ""


def test_a_stop_signal_cuts_the_rest_between_rounds_short(tmp_path):
    """[v3] 每輪之間的休息收到 SIGTERM 要馬上醒:time.sleep 被訊號打斷後會睡滿(PEP 475)。"""
    import signal
    import subprocess
    import sys

    argv = _argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9")
    argv[argv.index("--interval-seconds") + 1] = "20"
    child = subprocess.Popen(
        [sys.executable, "-m", "rtb.analyzer.runner", *argv], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(RUNNER.parents[2])})
    try:
        assert child.stdout.readline().strip() == runner.READY
        time.sleep(1.0)  # 沒有任務:第一輪馬上進休息
        began = time.monotonic()
        child.send_signal(signal.SIGTERM)
        assert child.wait(5) == 0
        assert time.monotonic() - began < 2
    finally:
        child.kill()
        child.stdout.close()


def _fake_time():
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    return clock, sleep


def test_a_task_that_keeps_failing_backs_off_too(tmp_path, monkeypatch):
    """[n4] 例外也算一次沒進展:一件每次都出錯的任務不會每輪都被推(每輪寫一行錯誤、多寫租約)。"""
    _create(tmp_path, ("t1", "c1"))
    clock, sleep = _fake_time()
    calls = []

    def broken(_store, _task_id, _args, _now):
        calls.append(clock["now"])
        raise RuntimeError("資料毀損")

    monkeypatch.setattr(runner, "_advance_one", broken)
    argv = _argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9")
    argv[argv.index("--interval-seconds") + 1] = "0.1"
    runner.run(argv, max_rounds=600, out=io.StringIO(), err=io.StringIO(), sleep=sleep,
               monotonic=lambda: clock["now"])
    assert len(calls) < 30, len(calls)


def test_backoff_never_waits_longer_than_its_cap(tmp_path, monkeypatch):
    """[n4] 沒有進展時等待加倍,但兩次推進的間隔不超過上限(否則等待中的任務第 20 次要等約 6 天,
    F4、F6 等不到新工作)。"""
    _create(tmp_path, ("t1", "c1"))
    clock, sleep = _fake_time()
    calls = []

    def stuck(_store, _task_id, _args, _now):
        calls.append(clock["now"])
        return TaskState.HANDED_OFF, 5

    monkeypatch.setattr(runner, "_advance_one", stuck)
    argv = _argv(tmp_path, "http://127.0.0.1:9", "http://127.0.0.1:9")
    argv[argv.index("--interval-seconds") + 1] = "0.1"
    runner.run(argv, max_rounds=1200, out=io.StringIO(), sleep=sleep,
               monotonic=lambda: clock["now"])
    gaps = [b - a for a, b in itertools.pairwise(calls)]
    # 寫死 10 秒(計劃的上限值),不引用程式裡的常數:常數被改大時這條才會翻紅
    assert len(calls) > 5 and max(gaps) <= 10.0 + 0.2, gaps


def test_the_timeout_reaches_the_operation_lookup_too(tmp_path, services):
    """[n4/M37lookup] 收件紀錄清掉之後改查平台的那一次呼叫也照逾時參數:平台只收連線不回應,
    那一步在逾時左右結束。"""
    import socket

    from rtb.httpkit import JsonHandler, KitServer

    class Purged(JsonHandler):
        def handle_request(self, _method):
            self.read_json(allow_empty=True)
            return 422, {"error": "expired_proposal", "retryable": False}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    silent = f"http://127.0.0.1:{listener.getsockname()[1]}"
    inbox = KitServer(Purged)
    threading.Thread(target=inbox.serve_forever, args=(0.02,), daemon=True).start()
    try:
        _create(tmp_path, ("t1", "c1"))
        runner.run(_argv(tmp_path, *services), max_rounds=4, out=io.StringIO(),
                   err=io.StringIO())
        assert _latest(tmp_path, "t1").state is TaskState.HANDED_OFF
        argv = _argv(tmp_path, silent, f"http://127.0.0.1:{inbox.server_address[1]}",
                     timeout="0.3")
        began = time.monotonic()
        runner.run(argv, max_rounds=1, out=io.StringIO(), err=io.StringIO())
        elapsed = time.monotonic() - began
    finally:
        listener.close()
        inbox.shutdown()
        inbox.server_close()
    assert elapsed < 3, elapsed
