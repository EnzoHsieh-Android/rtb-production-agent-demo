"""Phase 1 留下的兩條邊界測試,增量 4 第一次有真的 DSP 客戶端時補驗:S52、S53。"""

import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from rtb.analyzer import dsp_client, flow, inbox_client, policy
from rtb.analyzer.task_store import TaskStore
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor.inbox_server import InboxServer


# ---- S53 ----
def test_the_analyzer_package_never_imports_dsp_internals():
    config = Path(__file__).resolve().parents[2] / "src" / "rtb" / "analyzer" / "ruff.toml"
    for banned in ("rtb.dsp", "rtb.executor"):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(config.parent / "probe.py"), "-"],
            input=f"import {banned}\n", capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (banned, result.stdout)


# ---- S52 ----
def test_the_agent_cannot_reach_fault_injection_on_production_style_servers(tmp_path):
    """DSP 與收件口都不帶 --fault-injection 啟動(生產樣態),真的用戶端跑完整套正常流程。"""
    dsp_store = CampaignStore(tmp_path / "dsp.db")
    dsp_store.seed_campaign("c1", budget=100)
    dsp_store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=1.0,
                           revenue=3.0)
    # 兩個伺服器各自進自己的 try/finally:第二個建構或啟動失敗,第一個已經開的監聽 socket
    # 也要關掉,不能等 GC 回收(代碼審第 1 輪指出,舊寫法把兩個建構都放在同一個保護傘外面)。
    dsp = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2, delay_seconds=0.0)
    try:
        threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
        inbox = InboxServer(tmp_path / "inbox.db", max_pending=4, fault_injection=False)
        try:
            threading.Thread(target=inbox.serve_forever, args=(0.02,), daemon=True).start()
            _walk_one_task_end_to_end(tmp_path, dsp, inbox)
        finally:
            inbox.shutdown()
            inbox.server_close()
    finally:
        dsp.shutdown()
        dsp.server_close()


def _walk_one_task_end_to_end(tmp_path, dsp, inbox):
    dsp_url = f"http://127.0.0.1:{dsp.server_address[1]}"
    inbox_url = f"http://127.0.0.1:{inbox.server_address[1]}"
    store = TaskStore(tmp_path / "analyzer.db")
    try:
        now = datetime.now(UTC)
        store.create_task("t1", "c1", now)
        evidence_source = dsp_client.make_client(dsp_url, timeout_seconds=3)
        submit = inbox_client.make_client(inbox_url, timeout_seconds=3)

        state = flow.advance(store, "t1", evidence_source, policy.decide, submit, now)
        assert state is TaskState.COLLECTING_EVIDENCE
        state = flow.advance(store, "t1", evidence_source, policy.decide, submit, now)
        assert state is TaskState.ANALYZING
        state = flow.advance(store, "t1", evidence_source, policy.decide, submit, now)
        assert state in (TaskState.PROPOSED, TaskState.NO_ACTION)
        if state is TaskState.PROPOSED:
            state = flow.advance(store, "t1", evidence_source, policy.decide, submit, now)
            assert state is TaskState.HANDED_OFF
    finally:
        store.close()


def test_fault_headers_sent_to_production_style_servers_are_refused(tmp_path):
    """就算有人手動組出 X-Fault 標頭送給沒開故障注入的伺服器,伺服器自己也會拒絕——
    這是最後一道防線,真正的防線是用戶端從沒有能送出這個標頭的公開介面(見 test_httpclient.py)。
    """
    import http.client

    dsp_store = CampaignStore(tmp_path / "dsp.db")
    dsp_store.seed_campaign("c1", budget=100)
    dsp = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2, delay_seconds=0.0)
    threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", dsp.server_address[1], timeout=3)
        conn.request("GET", "/campaigns/c1", headers={"X-Fault": "timeout_before_commit"})
        resp = conn.getresponse()
        assert resp.status == 400
        conn.close()
    finally:
        dsp.shutdown()
        dsp.server_close()
