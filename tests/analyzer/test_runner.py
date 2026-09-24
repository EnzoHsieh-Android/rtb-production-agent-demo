"""分析端驅動命令列(Phase 12 增量 1 [S1000] [S1001]):反覆領取待推進的任務,用正式的 DSP 與收件口
用戶端推進;一步最多兩次讀 DSP 或一次送件,呼叫次數 乘 逾時 乘 2 要小於租約才准啟動。"""

import ast
import io
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rtb.analyzer import runner
from rtb.analyzer.task_store import LEASE_DURATION, TaskReader, TaskStore
from rtb.domain.task_state import TaskState
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
    rtb_modules = {m for m in modules if m.startswith("rtb")}
    assert rtb_modules <= {"rtb.analyzer", "rtb.analyzer.task_store", "rtb.analyzer.flow",
                           "rtb.analyzer.policy", "rtb.analyzer.dsp_client",
                           "rtb.analyzer.inbox_client", "rtb.analyzer.instrumented",
                           "rtb.domain.evidence"}, rtb_modules
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
