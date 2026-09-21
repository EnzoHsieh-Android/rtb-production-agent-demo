"""抽出共用基礎之後,DSP 沒有留下第二份實作。行為不變由 tests/dsp 的既有測試守著。"""

import ast
import http.client
import inspect
import json
import subprocess
import sys
import threading
from pathlib import Path

import rtb.dsp.server as dsp_server
import rtb.dsp.store as dsp_store
from rtb.dsp.store import CampaignStore
from rtb.httpkit import JsonHandler


def test_the_dsp_server_keeps_no_second_copy_of_the_shared_behaviour():
    handler = dsp_server.DspHandler
    duplicated = {"_check_host", "_read_json", "_read_exactly", "_reply", "_reply_error",
                  "_single_header", "setup", "log_message", "send_error"}

    assert issubclass(handler, JsonHandler)
    assert not duplicated & set(vars(handler)), "DSP 不該再自帶這些共用行為"
    store_source = inspect.getsource(dsp_store)
    assert "connect(" in store_source and "sqlite3.connect" not in store_source
    tree = ast.parse(inspect.getsource(dsp_server))
    attributes = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert "settimeout" not in attributes


def _serve(tmp_path, fault_injection=False):
    CampaignStore(tmp_path / "d.db").seed_campaign("c1", budget=100)
    srv = dsp_server.DspServer(tmp_path / "d.db", fault_injection, hang_seconds=0.05,
                               delay_seconds=0.0)
    threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
    return srv


def _get(srv, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, json.loads(body) if body else None


def test_every_dsp_request_closes_its_store_connection_on_every_path(tmp_path, monkeypatch):
    opened, closed = [], []
    real_init, real_close = CampaignStore.__init__, CampaignStore.close

    def counting_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        opened.append(self)

    def counting_close(self):
        closed.append(self)
        real_close(self)

    monkeypatch.setattr(CampaignStore, "__init__", counting_init)
    monkeypatch.setattr(CampaignStore, "close", counting_close)
    srv = _serve(tmp_path)
    baseline = len(opened)  # 種資料用的那條連線不算
    try:
        assert _get(srv, "/campaigns/c1")[0] == 200  # 成功
        assert _get(srv, "/campaigns/nope")[0] == 404  # 領域例外
        def exploding(_self, _campaign_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(CampaignStore, "get_campaign", exploding)
        assert _get(srv, "/campaigns/c1")[0] == 500  # 非預期例外
    finally:
        srv.shutdown()
        srv.server_close()

    handled = opened[baseline:]
    assert len(handled) >= 3
    assert {id(s) for s in handled} <= {id(s) for s in closed}  # 開過的每一條都關了


def test_the_domain_layer_may_not_import_the_shared_process_modules():
    domain_config = Path(__file__).resolve().parents[2] / "src" / "rtb" / "domain" / "ruff.toml"
    for module in ("rtb.httpkit", "rtb.sqlitekit"):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(domain_config),
             "--stdin-filename", str(domain_config.parent / "probe.py"), "-"],
            input=f"import {module}\n", capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (module, result.stdout)
