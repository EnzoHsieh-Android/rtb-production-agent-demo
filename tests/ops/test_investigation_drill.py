"""調查實演(Phase 9 增量 4):[S670] 到 [S674]。

注入一個事故,照「指標異常 → 切片 → 下鑽 → 時間線 → 查 DSP 歷史」走一遍,每一步只用增量 1 到 3 的
指標、追蹤與查詢,不直接查資料庫。一支測試函式、五個依序的斷言區塊(前一步失敗後面不跑)。

事故:租戶甲 12 個廣告的 DSP 寫入連續 12 分鐘回 5xx(每把鍵前兩次寫入提交前 503、事故後第三次成功),
另一個廣告第一次寫入走「提交後逾時」;租戶乙 4 個廣告同時正常運作。12 分鐘夾在對帳期限 10 分鐘與
決策新鮮度 15 分鐘之間。組法照執行端的真 DSP 端到端測試:真的模擬 DSP 伺服器(故障在伺服器端依冪等鍵
排定,驗憑證的時鐘接虛擬時鐘)、收件口、兩個執行迴圈物件、未修改的執行端 DSP 用戶端;不帶分析行程。
虛擬時鐘從系統時間起跳(DSP 提交時間是系統時間,副作用核對依它歸窗),每讀一次前進 1 毫秒。
"""

import json
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from rtb.analyzer.task_store import TaskStore
from rtb.domain.attempt import operation_key
from rtb.dsp.server import DspHandler, DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor import attempt_store, inbox_store
from rtb.executor.attempt_store import MAX_SENDS
from rtb.executor.capability_signer import CapabilitySigner, load_tenants
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import Executor, Result
from rtb.executor.inbox_store import InboxStore, ReadOnlyInbox
from rtb.ops import metrics, side_effects, sli, slo, trace
from tests.capability_samples import TEST_KEY
from tests.executor.conftest import Clock
from tests.executor.fakes import proposal

A_BAD = [f"a{n:02d}" for n in range(1, 13)]  # 租戶甲:吃兩次 5xx 的 12 個廣告
A_TIMEOUT = "a13"  # 租戶甲:提交後逾時的那一個
B_OK = [f"b{n:02d}" for n in range(1, 5)]  # 租戶乙:正常
INCIDENT = timedelta(minutes=12)
HANG, CLIENT_TIMEOUT = 0.6, 0.3  # 提交後逾時:DSP 掛住比用戶端逾時久,用戶端才會真的逾時
VERSIONS = ("0.9.1-a", "0.9.1-b")  # 兩個工作者掛的程式版本
AUDIT = ("audit-" + "d" * 32).encode()  # DSP 列操作端點的唯讀稽核金鑰(增量 3 代碼審起必帶)


class KeyedHandler(DspHandler):
    """比照執行端真 DSP 端到端測試替換「讀故障」那一步;改成依冪等鍵取排定(每把鍵自己一串)。"""

    def read_fault(self, modes):
        if self.command != "POST":
            return None
        with self.server.plan_lock:
            planned = self.server.plan.get(self.headers.get("Idempotency-Key"), [])
            return planned.pop(0) if planned else None


class Drill:
    def __init__(self, tmp_path):
        # 從系統時間起跳:DSP 提交時間是系統時間,副作用核對依它歸窗;每讀一次前進 1 毫秒,
        # 同一輪裡依序發生的事時間都不同
        self.clock = Clock(start=datetime.now(UTC), tick=timedelta(milliseconds=1))
        self.t0 = self.clock.now + timedelta(minutes=5)
        self.dsp_db, self.executor_db = tmp_path / "dsp.db", tmp_path / "executor.db"
        self.analyzer_db = tmp_path / "analyzer.db"
        TaskStore(self.analyzer_db).close()  # 實演不帶分析行程:空的分析端資料庫
        seed = CampaignStore(self.dsp_db)
        tenants = {"tenant-a": ["a00", *A_BAD, A_TIMEOUT], "tenant-b": ["b00", *B_OK]}
        for tenant, campaigns in tenants.items():
            for campaign in campaigns:
                seed.seed_campaign(campaign, budget=100, tenant=tenant)
        seed.close()
        self.config = tmp_path / "tenants.json"
        self.config.write_text(json.dumps({"tenants": {
            name: {"campaigns": ids, "max_budget": 1000, "aggregate_limit": 10**9}
            for name, ids in tenants.items()}}), encoding="utf-8")
        self.config.chmod(0o600)
        self.tenants = load_tenants(self.config)
        self.server = DspServer(self.dsp_db, fault_injection=True, hang_seconds=HANG,
                                delay_seconds=0.0, capability_key=TEST_KEY, audit_key=AUDIT,
                                clock=lambda: self.clock().timestamp())
        self.server.RequestHandlerClass = KeyedHandler
        self.server.plan, self.server.plan_lock = {}, threading.Lock()
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.store = InboxStore(self.executor_db, max_pending=20)  # 預設 8,T0 一次送 17 筆
        client = DspClient(self.url, CLIENT_TIMEOUT)
        self.workers = [Executor(self.store, client, CapabilitySigner(TEST_KEY), self.config,
                                 self.clock, f"worker-{v}") for v in VERSIONS]
        self.props = {}

    def close(self):
        self.store.close()
        self.server.shutdown()
        self.server.server_close()

    def submit(self, campaign, created):
        prop = proposal(task_id=f"t-{campaign}", campaign_id=campaign,
                        requested_change={"new_budget": 140}, campaign_version_observed=1,
                        decision_created_at=created.isoformat(),
                        decision_expires_at=(created + timedelta(minutes=30)).isoformat())
        self.store.accept(prop, self.clock)
        self.props[campaign] = prop
        return prop

    def process(self, monkeypatch):
        """兩個工作者在同一個執行緒輪流各取一筆,直到都沒有待處理(誰拿到哪一筆由輪流順序決定)。"""
        idle = [False, False]
        while not all(idle):
            for n, worker in enumerate(self.workers):
                with as_version(monkeypatch, VERSIONS[n]):
                    idle[n] = worker.process_one().kind is Result.IDLE

    def reconcile(self, monkeypatch):
        for n, worker in enumerate(self.workers):
            with as_version(monkeypatch, VERSIONS[n]):
                worker.reconcile_all()

    def sends(self, campaign):
        reader = ReadOnlyInbox(self.executor_db)
        try:
            with reader.read_transaction() as tx:
                row = attempt_store.latest(tx, operation_key(self.props[campaign]))
        finally:
            reader.close()
        return None if row is None else (row.state.value, row.send_count)

    def sources(self):
        return sli.Sources(self.executor_db, self.analyzer_db, self.url, 2.0, AUDIT)

    def window(self, since, until):
        return metrics.collect_window(since, until, executor_db=self.executor_db,
                                      analyzer_db=self.analyzer_db, tenants=self.tenants)


def jump_to(clock, moment):
    """把虛擬時鐘一步推到某一刻(只能往前)。"""
    assert moment >= clock.now
    clock.advance(seconds=(moment - clock.now).total_seconds())


@contextmanager
def as_version(monkeypatch, version):
    """程式版本是行程載入時定的常數:比照事故 F6 端到端換政策版本,輪到哪個工作者前換成它的版本。"""
    with monkeypatch.context() as patch:
        for module in (attempt_store, inbox_store):
            patch.setattr(module, "PROGRAM_VERSION", version)
        yield


@pytest.fixture
def drill(tmp_path):
    built = Drill(tmp_path)
    yield built
    built.close()


def test_the_drill_clock_is_the_shared_test_clock(drill):
    """代碼審第 1 輪:實演的時鐘沿用執行端測試共用的 Clock,只多「每讀一次前進 1 毫秒」與執行緒鎖。"""
    assert isinstance(drill.clock, Clock)
    first, second = drill.clock(), drill.clock()
    assert second - first == timedelta(milliseconds=1)


def pick(report, name, **labels):
    return [s for s in report.samples
            if s.name == name and all(s.label_map().get(k) == v for k, v in labels.items())]


def run_incident(d, monkeypatch):
    """排程(虛擬時間,T0 是事故開始);每一刻結束時斷言送出次數。"""
    for campaign in ("a00", "b00"):  # T0 前 5 分鐘:事故前的正常寫入
        d.submit(campaign, d.clock.now)
    d.process(monkeypatch)
    for campaign in A_BAD:
        d.server.plan[operation_key(d.submit(campaign, d.t0))] = ["transient_5xx"] * 2
    d.server.plan[operation_key(d.submit(A_TIMEOUT, d.t0))] = ["timeout_after_commit"]
    for campaign in B_OK:
        d.submit(campaign, d.t0)
    jump_to(d.clock, d.t0)
    d.process(monkeypatch)  # T0:只取件
    assert {d.sends(c) for c in A_BAD} == {("unknown", 1)}
    assert d.sends(A_TIMEOUT) == ("unknown", 1)
    assert {d.sends(c) for c in B_OK} == {("verified", 1)}

    jump_to(d.clock, d.t0 + timedelta(minutes=5))
    d.reconcile(monkeypatch)  # T0 + 5 分鐘:只對帳
    assert {d.sends(c) for c in A_BAD} == {("unknown", 2)}
    assert d.sends(A_TIMEOUT) == ("verified", 1)  # 查到就結案,不重送

    jump_to(d.clock, d.t0 + timedelta(minutes=10, seconds=1))
    now = d.clock.now
    alert = slo.evaluate(now, counter=lambda n, s, u: sli.count(n, s, u, d.sources()))

    jump_to(d.clock, d.t0 + INCIDENT + timedelta(seconds=1))
    d.reconcile(monkeypatch)  # 事故結束:只對帳
    assert {d.sends(c) for c in A_BAD} == {("verified", 3)}
    return alert


# ---- [S670] 到 [S674] ----
def test_investigation_drill_walks_from_alert_to_dsp_history(drill, monkeypatch):
    d = drill
    # 前提:任一不成立是劇本要重排,不是被測程式壞
    assert len(A_BAD) >= slo.MIN_SAMPLES and MAX_SENDS == 3 and CLIENT_TIMEOUT < HANG
    assert sli.RECONCILE_DEADLINE < INCIDENT < timedelta(minutes=15)
    started = time.monotonic()
    alert = run_incident(d, monkeypatch)
    step1_metrics_show_the_incident(d, alert)
    task = step2_and_3_slices_and_exemplar(d)
    step4_and_5_timeline_and_dsp_history(d, task)
    print(f"實演耗時 {time.monotonic() - started:.1f} 秒")  # 量實演本身跑多久


def step1_metrics_show_the_incident(d, alert):
    status = {s.name: s for s in alert}
    assert status["unknown_reconciled_in_time"].fast.fired, "第 1 步:快燒告警"
    for name in ("unauthorized_side_effects", "harmful_duplicates"):
        assert status[name].violating is False and status[name].period_valid > 0, name
    pre = d.window(d.t0 - timedelta(minutes=5), d.t0)
    incident = d.window(d.t0, d.t0 + INCIDENT + timedelta(minutes=1))
    bad = ("server_error", "timeout")
    assert not [s for s in pick(pre, "dsp_calls") if s.label_map()["dsp_call_result"] in bad]
    assert sum(s.value for s in pick(incident, "dsp_calls", dsp_call_kind="write",
                                     dsp_call_result="server_error")) == 24
    assert sum(s.value for s in pick(incident, "dsp_calls", dsp_call_kind="write",
                                     dsp_call_result="timeout")) == 1
    assert pick(pre, "reconciliation_seconds.p95")[0].status is metrics.Status.NO_SAMPLES
    p95 = pick(incident, "reconciliation_seconds.p95", tenant="tenant-a")
    assert sum(s.count for s in p95) == 13 and max(s.value for s in p95) > 600
    snapshot = metrics.collect_snapshot(d.clock.now, executor_db=d.executor_db,
                                        tenants=d.tenants)
    assert pick(snapshot, "unresolved_escalated")[0].value == 0  # 沒有任何一把鍵轉人工


def step2_and_3_slices_and_exemplar(d):
    incident = d.window(d.t0, d.t0 + INCIDENT + timedelta(minutes=1))
    failures = [s for s in pick(incident, "dsp_calls", dsp_call_kind="write")
                if s.label_map()["dsp_call_result"] in ("server_error", "timeout")]
    assert {s.label_map()["tenant"] for s in failures} == {"tenant-a"}, "第 2 步:依租戶"
    per_version = {v: sum(s.value for s in failures if s.label_map()["program_version"] == v
                          and s.label_map()["dsp_call_result"] == "server_error")
                   for v in VERSIONS}
    assert all(count > 0 for count in per_version.values()), per_version
    slowest = max(pick(incident, "reconciliation_seconds.p95", tenant="tenant-a"),
                  key=lambda s: s.value)
    task = slowest.exemplars[0]
    assert task in {f"t-{c}" for c in A_BAD}, "第 3 步:範例是吃過兩次 5xx 的任務"
    return task


def _token(segment):
    detail = dict(segment.detail)
    if segment.table is trace.Table.DSP_CALLS:
        return f"call:{segment.what}:{detail['result']}"
    return f"{segment.table.value}:{segment.what}"


def _contains_in_order(tokens, expected):
    position = 0
    for token in tokens:
        if position < len(expected) and token.startswith(expected[position]):
            position += 1
    return position == len(expected)


def step4_and_5_timeline_and_dsp_history(d, task):
    seen = trace.build_trace(task, analyzer_db=d.analyzer_db, executor_db=d.executor_db,
                             dsp_url=d.url, clock=d.clock)
    tokens = [_token(s) for s in seen.segments]
    expected = ["lifecycle_events:received", "lifecycle_events:delivered", "call:read_campaign",
                "attempts:in_flight", "call:write:server_error", "attempts:unknown",
                "call:lookup_operation", "call:read_campaign", "call:write:server_error",
                "attempts:unknown", "call:lookup_operation", "call:read_campaign",
                "call:write:responded", "attempts:committed_unverified"]
    assert _contains_in_order(tokens, expected), ("第 4 步:時間線", tokens)
    # 已驗證與收件口結案寫在同一個交易、同一個時間:兩者誰先只是追蹤檢視的固定表序,不代表因果,
    # 只斷言兩個都在「已提交待驗證」之後(主線裁定:斷言不依賴同一時刻跨表的排序)
    after = tokens[tokens.index("attempts:committed_unverified"):]
    assert {"attempts:verified", "lifecycle_events:handed_off"} <= set(after), tokens
    assert seen.stable and [s.at for s in seen.segments] == sorted(s.at for s in seen.segments)
    for segment in seen.segments:
        detail = dict(segment.detail)
        if segment.origin is trace.Origin.EXECUTOR and detail.get("source") == "executor_loop":
            assert segment.field("worker") != trace.MISSING, segment
            assert detail["program_version"] in VERSIONS, segment

    key = operation_key(next(p for c, p in d.props.items() if f"t-{c}" == task))
    writes = [t for t in tokens if t.startswith("call:write")]
    assert writes == ["call:write:server_error"] * 2 + ["call:write:responded"], "第 5 步"
    assert [s.table for s in seen.segments].count(trace.Table.DSP_OPERATIONS) == 1
    _, operation = side_effects.get_json(d.url, f"/operations/{key}", 2.0)
    assert operation["params"]["new_budget"] == 140  # 三次送出只落地一次,新預算等於提案

    late = trace.build_trace(f"t-{A_TIMEOUT}", analyzer_db=d.analyzer_db,
                             executor_db=d.executor_db, dsp_url=d.url, clock=d.clock)
    late_tokens = [_token(s) for s in late.segments]
    assert [t for t in late_tokens if t.startswith("call:write")] == ["call:write:timeout"]
    assert [s.table for s in late.segments].count(trace.Table.DSP_OPERATIONS) == 1  # 沒重送卻有操作
