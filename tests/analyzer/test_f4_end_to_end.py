"""事故 F4 端到端:Phase 5 的 S308。

提案形成之後,另一方先改了同一個廣告。舊決策不能覆蓋新值:執行端要擋下,分析端要另開接續任務,
重讀現況、用新版本產生提案並執行成功。兩種搶先都跑:
- 執行前檢查擋下:分析完、執行前就被改了,執行端重讀時版本已變。
- 送出前一刻被搶先:執行前檢查通過之後、送出之前才被改,DSP 回版本衝突(靠執行側 S310 分流成
  「版本已變」)。
組法照事故 F5 端到端:真的模擬 DSP、收件口、分析行程與執行迴圈,各服務都用真實時間。
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from rtb.analyzer import dsp_client, flow, inbox_client, instrumented, policy
from rtb.analyzer.task_store import TaskStore, follow_up_id
from rtb.domain.attempt import operation_key
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore, Operation
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import Executor
from rtb.executor.inbox_server import InboxServer
from rtb.executor.inbox_store import InboxStore
from tests.capability_samples import TEST_KEY
from tests.executor.fakes import write_config

STALE_BUDGET = 110  # 原任務依預算 100 算出的決策(漲一成):不准出現在 DSP 上
OTHER_BUDGET = 200  # 另一方搶先改成的值
FRESH_BUDGET = 220  # 接續任務依新現況(預算 200)算出的決策
TERMINAL = (TaskState.HANDED_OFF, TaskState.NO_ACTION, TaskState.FAILED, TaskState.BLOCKED,
            TaskState.COMPLETED)


def _now():
    return datetime.now(UTC)  # 各服務與簽發都用真實時間:DSP 只容許 30 秒的時鐘誤差


def _other_writer_changes_the_budget(dsp_db):
    """另一方直接在 DSP 改預算:自帶鍵、預期版本 1(原任務讀到的版本)。"""
    store = CampaignStore(dsp_db)
    try:
        store.execute(Operation(campaign_id="c1", action="update_budget",
                                params={"new_budget": OTHER_BUDGET}, expected_version=1,
                                idempotency_key="other-writer-1"))
    finally:
        store.close()


class _PreemptedClient(DspClient):
    """執行前檢查已經讀過 DSP、通過了;送出前一刻另一方搶先改(只搶第一次)。"""

    def __init__(self, base_url, timeout_seconds, dsp_db):
        super().__init__(base_url, timeout_seconds)
        self._dsp_db, self._preempted = dsp_db, False

    def write(self, prop, key, token):
        if not self._preempted:
            self._preempted = True
            _other_writer_changes_the_budget(self._dsp_db)
        return super().write(prop, key, token)


def run_f4(tmp_path, race):
    dsp_db = tmp_path / "dsp.db"
    seeding = CampaignStore(dsp_db)
    seeding.seed_campaign("c1", budget=100)
    # 花費 0.5 在預算 100 與 200 下都明顯偏低:接續任務重讀之後仍會出提案
    seeding.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=0.5,
                         revenue=5.0)
    seeding.close()
    # 兩個伺服器各自進自己的 try/finally(比照 F5 端到端):第二個起不來,第一個也要關
    dsp = DspServer(dsp_db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0,
                    capability_key=TEST_KEY)
    try:
        threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
        inbox = InboxServer(tmp_path / "inbox.db", fault_injection=False)
        try:
            threading.Thread(target=inbox.serve_forever, args=(0.02,), daemon=True).start()
            return _walk(tmp_path, f"http://127.0.0.1:{dsp.server_address[1]}",
                         f"http://127.0.0.1:{inbox.server_address[1]}", race)
        finally:
            inbox.shutdown()
            inbox.server_close()
    finally:
        dsp.shutdown()
        dsp.server_close()


def _walk(tmp_path, dsp_url, inbox_url, race):
    dsp_db = tmp_path / "dsp.db"
    analyzer = TaskStore(tmp_path / "analyzer.db")
    try:
        analyzer.create_task("t1", "c1", _now())
        _advance(analyzer, "t1", dsp_url, inbox_url)  # 分析完、送出提案:停在已交給執行
        if race == "precheck":
            _other_writer_changes_the_budget(dsp_db)  # 執行前就被改:執行前檢查擋下
            _execute(tmp_path, DspClient(dsp_url, 3.0))
        else:
            _execute(tmp_path, _PreemptedClient(dsp_url, 3.0, dsp_db))
        _advance(analyzer, "t1", dsp_url, inbox_url)  # 查到版本已變:擋下、另開接續任務

        child = analyzer.follow_up_to("t1")
        if child is not None:
            _advance(analyzer, child, dsp_url, inbox_url)  # 重讀現況、送新提案
            _execute(tmp_path, DspClient(dsp_url, 3.0))
            _advance(analyzer, child, dsp_url, inbox_url)  # 收件口回已交給執行:完成
        return _facts(tmp_path, analyzer, child)
    finally:
        analyzer.close()


def _advance(analyzer, task_id, dsp_url, inbox_url):
    evidence_source = instrumented.dsp_evidence_source(analyzer, dsp_url, 3)
    send = inbox_client.make_client(inbox_url, 3)
    lookup = dsp_client.make_operation_lookup(dsp_url, 3)
    for _ in range(8):
        row = analyzer.latest(task_id)
        # 呼叫紀錄綁「呼叫當下讀到的那一列」:每一步都用當下的列重建
        state = flow.advance(
            analyzer, task_id, evidence_source, policy.decide,
            instrumented.InstrumentedSubmit(analyzer, send, "inbox:submit", row), _now(),
            operation_lookup=instrumented.InstrumentedOperationLookup(
                analyzer, lookup, "dsp:operation", row))
        if state in TERMINAL:
            return state
    return analyzer.latest(task_id).state


def _execute(tmp_path, client):
    store = InboxStore(tmp_path / "inbox.db")
    try:
        Executor(store, client, CapabilitySigner(TEST_KEY),
                 write_config(tmp_path / "tenants.json"), _now).process_one()
    finally:
        store.close()


def _facts(tmp_path, analyzer, child):
    conn = sqlite3.connect(tmp_path / "dsp.db")
    try:
        writes = [(key, json.loads(params)["new_budget"], version) for key, params, version in
                  conn.execute("SELECT idempotency_key, params_json, version_after "
                               "FROM operations WHERE campaign_id = 'c1' ORDER BY operation_id")]
    finally:
        conn.close()
    dsp = CampaignStore(tmp_path / "dsp.db")
    try:
        campaign = dsp.get_campaign("c1")
    finally:
        dsp.close()
    original = analyzer.latest("t1")
    stale = next(row.proposal for row in analyzer.history("t1") if row.proposal)
    conn = sqlite3.connect(tmp_path / "inbox.db")
    try:  # 執行端對舊決策那把鍵做過什麼:執行前檢查擋下就一筆都沒開
        stale_attempts = conn.execute(
            "SELECT state, code FROM attempts WHERE key = ? ORDER BY seq",
            (operation_key(stale),)).fetchall()
    finally:
        conn.close()
    return {
        "stale_attempts": stale_attempts,
        "writes": writes, "budget": campaign.budget, "version": campaign.version,
        "original": original, "child": child,
        "child_row": None if child is None else analyzer.latest(child),
        "child_proposal": None if child is None else next(
            (row.proposal for row in reversed(analyzer.history(child)) if row.proposal), None),
        "child_of": None if child is None else analyzer.follow_up_of(child),
    }


# ---- S308 ----
@pytest.mark.parametrize("race", ["precheck", "dsp_write"])
def test_f4_a_stale_proposal_is_replanned_from_the_current_state(tmp_path, race):
    facts = run_f4(tmp_path, race)

    # 原任務:擋下、指到接續任務;兩種搶先的錯誤說明一樣(送出前一刻被搶先靠執行側 S310 分流)
    child = follow_up_id("t1")
    assert facts["original"].state is TaskState.BLOCKED
    assert facts["original"].error_detail == (
        f"blocked=version_changed;replan=version_changed;follow_up={child}")
    assert facts["child"] == child and facts["child_of"] == "t1"

    # 執行端走的是這個情境要測的那一層:執行前檢查擋下就沒開嘗試、沒送 DSP;
    # 送出前一刻被搶先是 DSP 的版本檢查擋下(嘗試記版本衝突)
    expected_attempts = {"precheck": [],
                         "dsp_write": [("in_flight", None), ("failed", "version_conflict")]}
    assert facts["stale_attempts"] == expected_attempts[race]

    # 接續任務:重讀現況、用新版本產生提案,執行成功
    assert facts["child_proposal"].campaign_version_observed == 2
    assert facts["child_proposal"].requested_change["new_budget"] == FRESH_BUDGET
    assert facts["child_row"].state is TaskState.COMPLETED

    # DSP:剛好兩筆寫入(另一方、接續任務),最終值是接續任務的決策,舊決策從沒寫進去
    assert [(new_budget, version) for _key, new_budget, version in facts["writes"]] == [
        (OTHER_BUDGET, 2), (FRESH_BUDGET, 3)]
    assert facts["writes"][0][0] == "other-writer-1"
    assert (facts["budget"], facts["version"]) == (FRESH_BUDGET, 3)
    assert STALE_BUDGET not in [new_budget for _key, new_budget, _v in facts["writes"]]
