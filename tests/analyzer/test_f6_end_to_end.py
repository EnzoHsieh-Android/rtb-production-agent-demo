"""事故 F6 端到端:Phase 8 的 S509。

舊工作進了死信,之後廣告、政策或決策變了;重放時必須重新驗證並拒絕舊決策,分析端改為重新規劃,
不能直接寫入。四組:廣告版本變、政策版本變、決策變舊,各自被擋下並給對應原因、分析端另開接續
任務,DSP 上沒有舊決策的值;沒有任何變化的那一組重放後照常寫成、任務完成。
組法照事故 F4 端到端:真的模擬 DSP、收件口、分析行程與執行迴圈。讀不到 DSP 用連不上的位址造;
「決策變舊」用執行端時鐘往後推 16 分鐘(其他服務照真實時間,DSP 只容許 30 秒的時鐘誤差,
所以只給執行端推,而且這一組在簽發之後、送出之前就擋下,不會帶著推過的時間去 DSP)。
"""

import io
import json
import sqlite3
import threading
from datetime import timedelta

import pytest

from rtb.analyzer.task_store import TaskStore, follow_up_id
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor import replay as replay_tool
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import Executor
from rtb.executor.inbox_server import InboxServer
from rtb.executor.inbox_store import MAX_DELIVERIES, InboxStore
from tests.analyzer.conftest import seed_rule_history
from tests.analyzer.test_f4_end_to_end import (
    OTHER_BUDGET,
    STALE_BUDGET,
    _advance,
    _now,
    _other_writer_changes_the_budget,
)
from tests.capability_samples import TEST_KEY
from tests.executor.fakes import write_config

UNREACHABLE = "http://127.0.0.1:9"  # 沒有人在聽:讀 DSP 立刻失敗,投遞次數用完進死信
EXPECTED_BLOCK = {"version": "version_changed", "policy": "policy_version_changed",
                  "stale": "decision_stale"}


def _execute(tmp_path, client, clock=_now):
    store = InboxStore(tmp_path / "inbox.db")
    try:
        Executor(store, client, CapabilitySigner(TEST_KEY),
                 write_config(tmp_path / "tenants.json"), clock).process_one()
    finally:
        store.close()


def run_f6(tmp_path, monkeypatch, change, finish_child=False):
    dsp_db = tmp_path / "dsp.db"
    seeding = CampaignStore(dsp_db)
    seeding.seed_campaign("c1", budget=100)
    seeding.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=0.5,
                         revenue=5.0)
    seed_rule_history(seeding, "c1")  # 正式規則的四查詢(Phase 14 增量 2b)
    seeding.close()
    dsp = DspServer(dsp_db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0,
                    capability_key=TEST_KEY)
    try:
        threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
        inbox = InboxServer(tmp_path / "inbox.db", fault_injection=False)
        try:
            threading.Thread(target=inbox.serve_forever, args=(0.02,), daemon=True).start()
            return _walk(tmp_path, monkeypatch, change,
                         f"http://127.0.0.1:{dsp.server_address[1]}",
                         f"http://127.0.0.1:{inbox.server_address[1]}", finish_child)
        finally:
            inbox.shutdown()
            inbox.server_close()
    finally:
        dsp.shutdown()
        dsp.server_close()


def _walk(tmp_path, monkeypatch, change, dsp_url, inbox_url, finish_child=False):
    analyzer = TaskStore(tmp_path / "analyzer.db")
    try:
        analyzer.create_task("t1", "c1", _now())
        _advance(analyzer, "t1", dsp_url, inbox_url)  # 分析完、送出提案:停在已交給執行
        for _ in range(MAX_DELIVERIES + 1):  # DSP 一直讀不到:投遞次數用完進死信
            _execute(tmp_path, DspClient(UNREACHABLE, 0.2))
        waited = _advance(analyzer, "t1", dsp_url, inbox_url)  # 死信、決策還沒過期:等

        clock = _now
        if change == "version":
            _other_writer_changes_the_budget(tmp_path / "dsp.db")
        elif change == "policy":  # 執行端先升級到新政策版本
            monkeypatch.setattr("rtb.executor.execution.POLICY_VERSION", "demo-pacing-v2")
        elif change == "stale":
            def clock():
                return _now() + timedelta(minutes=16)
        replayed = replay_tool.run(
            ["--db", str(tmp_path / "inbox.db"), "--task-id", "t1", "--revision", "1",
             "--operator", "ops-f6"], clock=clock, out=io.StringIO())
        _execute(tmp_path, DspClient(dsp_url, 3.0), clock)
        _advance(analyzer, "t1", dsp_url, inbox_url)
        child = analyzer.follow_up_to("t1")
        if finish_child and child is not None:  # 接續任務重讀、規則輪提案、執行、完成
            _advance(analyzer, child, dsp_url, inbox_url)
            _execute(tmp_path, DspClient(dsp_url, 3.0))
            _advance(analyzer, child, dsp_url, inbox_url)
        return _facts(tmp_path, analyzer, waited, replayed)
    finally:
        analyzer.close()


def _facts(tmp_path, analyzer, waited, replayed):
    conn = sqlite3.connect(tmp_path / "dsp.db")
    try:
        writes = [json.loads(params)["new_budget"] for (params,) in conn.execute(
            "SELECT params_json FROM operations WHERE campaign_id = 'c1' ORDER BY operation_id")]
    finally:
        conn.close()
    conn = sqlite3.connect(tmp_path / "inbox.db")
    try:
        actions = [a for (a,) in conn.execute("SELECT action FROM dead_letter_ops ORDER BY id")]
    finally:
        conn.close()
    child = analyzer.follow_up_to("t1")
    return {"waited": waited, "replayed": replayed, "writes": writes, "actions": actions,
            "original": analyzer.latest("t1"), "child": child,
            "child_row": None if child is None else analyzer.latest(child)}


# ---- [S509] ----
@pytest.mark.parametrize("change", ["unchanged", "version", "policy", "stale"])
def test_f6_a_stale_dead_letter_is_revalidated_on_replay(tmp_path, monkeypatch, change):
    facts = run_f6(tmp_path, monkeypatch, change)

    assert facts["waited"] is TaskState.HANDED_OFF  # 死信還能重放:分析端沒結案
    assert facts["replayed"] == 0
    assert facts["actions"] == ["dead_lettered", "replay_requested", "replay_requeued"]

    if change == "unchanged":  # 沒有任何變化:重放照常寫成
        assert facts["writes"] == [STALE_BUDGET]
        assert facts["original"].state is TaskState.COMPLETED
        assert facts["child"] is None
        return

    code = EXPECTED_BLOCK[change]
    assert facts["original"].state is TaskState.BLOCKED
    assert facts["original"].error_detail == (
        f"blocked={code};replan={code};follow_up={follow_up_id('t1')}")
    assert facts["child"] == follow_up_id("t1")
    assert STALE_BUDGET not in facts["writes"]  # 舊決策從沒寫進 DSP
    assert facts["writes"] == ([OTHER_BUDGET] if change == "version" else [])


def test_f6_a_stale_decision_follow_up_is_replanned_and_executed(tmp_path, monkeypatch):
    """接續任務的正路(Phase 14 代碼審 r1 鏡頭3-2):F4 那種「另一方剛改預算」會被九條第 3 條擋下,
    所以用「決策變舊」這種不涉及預算調整的重新規劃——接續任務重讀現況、走完規則輪 A/B/C、判值得加、
    提案、執行成功、完成;平台上是接續任務照新現況算的值,原任務的舊決策沒寫進去。"""
    facts = run_f6(tmp_path, monkeypatch, "stale", finish_child=True)

    assert facts["original"].state is TaskState.BLOCKED
    assert facts["child"] == follow_up_id("t1")
    assert facts["child_row"].state is TaskState.COMPLETED
    assert facts["writes"] == [STALE_BUDGET]  # 預算沒變:接續任務照 100 算出同一個 110,只寫一次
