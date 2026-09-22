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

        def step():  # 整批共用同一個時間:推進者很自然的寫法,不能因此卡在蒐證與分析之間彈跳
            return flow.advance(store, "t1", evidence_source, policy.decide, submit, now)

        assert step() is TaskState.COLLECTING_EVIDENCE
        assert step() is TaskState.ANALYZING
        state = step()
        assert state in (TaskState.PROPOSED, TaskState.NO_ACTION)
        if state is TaskState.PROPOSED:
            assert step() is TaskState.HANDED_OFF
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


NETWORK_MODULES = frozenset({
    "urllib", "http", "socket", "ssl", "socketserver", "asyncio", "requests", "httpx", "urllib3",
    "aiohttp", "ftplib", "smtplib", "xmlrpc", "rtb.httpkit",
})


def test_the_analyzer_reaches_the_network_only_through_the_shared_client():
    """2026-09-22 第二輪合約審計指出:標頭的封閉列舉只保護走共用用戶端的程式;分析行程裡
    另寫一個直接用 urllib 發請求的小工具,就能把任意標頭(含故障注入)送出去,原本的測試
    與原始碼掃描都看不到。這裡直接解析原始碼:分析行程不准自己匯入網路模組,也不准動態匯入。"""
    import ast

    analyzer = Path(__file__).resolve().parents[2] / "src" / "rtb" / "analyzer"
    offenders = []
    for file in sorted(analyzer.rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif (isinstance(node, ast.Name) and node.id in ("__import__", "import_module")) or (
                isinstance(node, ast.Attribute) and node.attr == "import_module"
            ) or (isinstance(node, ast.alias) and node.name == "import_module"):
                offenders.append(f"{file.name}: 動態匯入")  # 連引用或別名都不准,不只直接呼叫
            offenders += [f"{file.name}: {m}" for m in modules
                          if m in NETWORK_MODULES or m.split(".")[0] in NETWORK_MODULES]
    assert offenders == []
