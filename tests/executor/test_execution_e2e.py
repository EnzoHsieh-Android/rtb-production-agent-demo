"""執行一筆的端到端:真的 Mock DSP(測試行程內的執行緒)+ 真的執行行程 DSP 用戶端。

執行行程的用戶端送不出故障注入標頭(共用用戶端的封閉列舉擋掉),所以故障改由測試在 DSP
伺服器那一側排定:每個寫入請求依序取一個安排(故障模式、DSP 時鐘偏移)。
"""

import sqlite3
import threading

import pytest

from rtb.domain.attempt import operation_key
from rtb.dsp.server import DspHandler, DspServer
from rtb.dsp.store import CampaignStore, Operation
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import DspUnavailable, Executor, OperationRecord, Result
from rtb.executor.inbox_store import InboxStore
from tests.capability_samples import TEST_KEY
from tests.executor.fakes import proposal, write_config


class PlannedHandler(DspHandler):
    def read_fault(self, modes):
        # 排定的故障照順序給每一個會改狀態的請求(寫入與作廢都算)
        if self.command != "POST" or not self.server.plan:
            return None
        fault, offset = self.server.plan.pop(0)
        self.server.offset = offset
        return fault


class PlannedDsp(DspServer):
    def __init__(self, db, clock, delay_seconds=0.0):
        super().__init__(db, fault_injection=True, hang_seconds=0.6, delay_seconds=delay_seconds,
                         capability_key=TEST_KEY,
                         clock=lambda: clock().timestamp() + self.offset)
        self.RequestHandlerClass = PlannedHandler
        self.plan: list[tuple[str | None, int]] = []
        self.offset = 0


class World:
    def __init__(self, tmp_path, clock, dsp_timeout=3.0, delay_seconds=0.0):
        self.clock = clock
        self.dsp_db = tmp_path / "dsp.db"
        CampaignStore(self.dsp_db).seed_campaign("c1", budget=100)
        self.server = PlannedDsp(self.dsp_db, clock, delay_seconds)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.client = DspClient(f"http://127.0.0.1:{self.server.server_address[1]}", dsp_timeout)
        self.store = InboxStore(tmp_path / "executor.db")
        self.config = write_config(tmp_path / "tenants.json")
        self.executor = Executor(self.store, self.client, CapabilitySigner(TEST_KEY),
                                 self.config, clock)

    def submit(self, **overrides):
        prop = proposal(**{"campaign_version_observed": 1, **overrides})
        self.store.accept(prop, self.clock)
        return prop

    def dsp(self):
        return CampaignStore(self.dsp_db)

    def campaign(self):
        store = self.dsp()
        try:
            return store.get_campaign("c1")
        finally:
            store.close()

    def history(self):
        store = self.dsp()
        try:
            return store.history("c1")
        finally:
            store.close()

    def other_party_updates(self, budget=120):
        store = self.dsp()
        try:
            current = store.get_campaign("c1")
            store.execute(Operation("c1", "update_budget", {"new_budget": budget},
                                    current.version, f"other-{budget}"))
        finally:
            store.close()

    def states(self, prop):
        with self.store.transaction() as tx:
            return [(r[0], r[1]) for r in tx.conn.execute(
                "SELECT state, code FROM attempts WHERE key = ? ORDER BY seq",
                (operation_key(prop),))]

    def close(self):
        self.store.close()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def world(tmp_path, clock):
    w = World(tmp_path, clock)
    yield w
    w.close()


# ---- [S52] ----
def test_f4_a_stale_proposal_never_overwrites_a_newer_value(world):
    stale = world.submit()
    world.other_party_updates(budget=120)  # 提案形成之後,另一方先更新了廣告

    result = world.executor.process_one()

    assert result.block_code is not None and result.block_code.value == "version_changed"
    assert world.campaign().budget == 120 and world.states(stale) == []

    # 另一方搶在我們檢查之後、寫入之前改掉:DSP 的版本檢查擋下,值一樣不被覆蓋
    racing = world.submit(task_id="t2", campaign_version_observed=2)
    original_write = world.client.write

    def race_then_write(prop, key, token):
        world.other_party_updates(budget=130)
        return original_write(prop, key, token)

    world.client.write = race_then_write
    world.executor.process_one()
    assert world.states(racing)[-1] == ("failed", "version_conflict")
    assert world.campaign().budget == 130


# ---- [S53] ----
def test_f1_a_timeout_after_commit_is_recorded_as_unknown_and_applied_once(tmp_path, clock):
    world = World(tmp_path, clock, dsp_timeout=0.2)
    try:
        prop = world.submit()
        world.server.plan.append(("timeout_after_commit", 0))

        world.executor.process_one()

        assert world.states(prop) == [("in_flight", None), ("unknown", None)]
        ours = [h for h in world.history() if h.idempotency_key == operation_key(prop)]
        assert len(ours) == 1  # DSP 只套用一次
        assert world.campaign().version == 2
        assert world.client.operation_version(operation_key(prop)) == 2  # 之後同鍵查得到
    finally:
        world.close()


# ---- S47 的端到端那一半:正常流程造得出的回應 ----
@pytest.mark.parametrize(("plan", "expected"), [
    ([], ("verified", None)),
    ([("permanent_validation_error", 0)], ("failed", "validation_rejected")),
    ([(None, 300), (None, 0)], ("verified", None)),  # 第一張送到時 DSP 時鐘已過期,重簽成功
    ([("transient_5xx", 0)], ("unknown", None)),
])
def test_normal_flow_answers_map_end_to_end(world, plan, expected):
    prop = world.submit()
    world.server.plan.extend(plan)

    assert world.executor.process_one().kind is Result.EXECUTED

    assert world.states(prop)[-1] == expected


def test_a_timeout_before_commit_is_unknown_and_never_commits_later(tmp_path, clock):
    world = World(tmp_path, clock, dsp_timeout=0.2)
    try:
        prop = world.submit()
        world.server.plan.append(("timeout_before_commit", 0))

        world.executor.process_one()

        assert world.states(prop)[-1] == ("unknown", None)
        clock.advance(seconds=1)
        threading.Event().wait(0.8)  # 等 DSP 那邊的處理程式放棄
        assert world.campaign().version == 1
        assert world.client.operation_version(operation_key(prop)) is None
    finally:
        world.close()


def test_the_clock_offset_fixture_really_expires_the_first_capability(world):
    world.submit()
    world.server.plan.extend([(None, 300), (None, 300)])

    world.executor.process_one()

    with world.store.transaction() as tx:
        codes = [r[0] for r in tx.conn.execute("SELECT code FROM attempts ORDER BY seq")]
    assert codes[-1] == "capability_rejected"  # 偏移 300 秒大於憑證有效期,兩張都過期


# ---- 真的 DSP 用戶端:「不存在」與「讀取失敗」的分界(S43、S44 的替身背後那一段) ----
def test_the_real_client_tells_a_missing_campaign_from_a_failed_read(world):
    assert world.client.read_campaign("c-missing") is None  # 404 campaign_not_found
    assert world.client.operation_version("k1-missing") is None  # 404 operation_not_found
    with pytest.raises(DspUnavailable):  # 路由對不上的 404 不是「不存在」
        world.client.read_campaign("c1/extra")


def test_a_missing_campaign_is_blocked_through_the_real_client(world):
    prop = world.submit(campaign_id="c-missing")

    assert world.executor.process_one().kind is Result.BLOCKED
    with world.store.transaction() as tx:
        row = tx.conn.execute("SELECT disposition, block_code FROM proposals WHERE task_id = ?",
                              (prop.task_id,)).fetchone()
    assert row == ("blocked", "campaign_not_found")
    assert world.states(prop) == []


def test_an_oversized_version_from_the_dsp_is_treated_as_unreadable(monkeypatch):
    """版本會寫進資料庫整數欄位;超過 64 位元上限就當成讀不懂(寫入 → 結果不明),不讓寫入時崩潰。"""
    from rtb.executor import dsp_client

    client = dsp_client.DspClient("http://127.0.0.1:9", 1)
    monkeypatch.setattr(dsp_client, "request_json",
                        lambda *_a, **_k: (200, {"version_after": 2**63, "budget": 1,
                                                 "version": 2**63, "status": "active"}))
    assert client.write(proposal(), "k1-x", "t").version_after is None
    with pytest.raises(DspUnavailable):
        client.read_campaign("c1")
    with pytest.raises(DspUnavailable):
        client.operation_version("k1-x")
    monkeypatch.setattr(dsp_client, "request_json",
                        lambda *_a, **_k: (200, {"version_after": 2**63 - 1}))
    assert client.write(proposal(), "k1-x", "t").version_after == 2**63 - 1  # 上限本身可以


# ---- 對帳(增量 4)的端到端 ----
def applied(world, prop):
    return [h for h in world.history() if h.idempotency_key == operation_key(prop)]


# ---- [S79] ----
def test_f1_timeout_before_commit_is_reconciled_by_a_same_key_resend(tmp_path, clock):
    world = World(tmp_path, clock, dsp_timeout=0.2)
    try:
        prop = world.submit()
        world.server.plan.append(("timeout_before_commit", 0))
        world.executor.process_one()
        assert world.states(prop)[-1] == ("unknown", None)  # 前置:逾時、DSP 沒提交

        world.executor.reconcile_all()

        assert world.states(prop)[-1] == ("verified", None)
        assert len(applied(world, prop)) == 1  # 同一把鍵重送,DSP 只套用一次
        threading.Event().wait(0.8)  # 等被掛住的舊請求放棄:它不會晚到提交
        assert len(applied(world, prop)) == 1 and world.campaign().version == 2
    finally:
        world.close()


# ---- [S80] ----
def test_f1_timeout_after_commit_is_reconciled_from_the_operation_record(tmp_path, clock):
    world = World(tmp_path, clock, dsp_timeout=0.2)
    try:
        prop = world.submit()
        world.server.plan.append(("timeout_after_commit", 0))
        world.executor.process_one()
        assert world.states(prop)[-1] == ("unknown", None)  # 前置:逾時,但 DSP 已提交

        world.executor.reconcile_all()

        assert world.states(prop)[-2:] == [("committed_unverified", None), ("verified", None)]
        assert len(applied(world, prop)) == 1 and world.campaign().version == 2
    finally:
        world.close()


# ---- [S81] ----
def test_f1_a_delayed_commit_and_a_resend_apply_once(tmp_path, clock):
    world = World(tmp_path, clock, dsp_timeout=0.2, delay_seconds=0.6)
    try:
        prop = world.submit()
        world.server.plan.append(("delayed_response", 0))
        world.executor.process_one()
        assert world.states(prop)[-1] == ("unknown", None)  # 前置:用戶端先逾時
        assert applied(world, prop) == []  # 前置:DSP 還沒提交(舊請求還在睡)

        world.executor.reconcile_all()  # 查不到 → 同鍵重送

        threading.Event().wait(0.8)  # 等晚到的舊請求醒來:同鍵重放,不再套用
        assert world.states(prop)[-1] == ("verified", None)
        assert len(applied(world, prop)) == 1 and world.campaign().version == 2
    finally:
        world.close()


# ---- [S91] ----
def test_a_voided_key_answer_is_not_mistaken_for_a_version_conflict(world):
    prop = world.submit()
    store = world.dsp()
    try:
        store.void(operation_key(prop))  # 這把鍵在 DSP 已被作廢
    finally:
        store.close()

    world.executor.process_one()

    assert world.states(prop)[-1] == ("failed", "not_happened")
    assert world.campaign().version == 1


# ---- [S94] 的用戶端那一半 ----
def test_the_real_client_reads_the_full_operation_record(world):
    prop = world.submit()
    world.executor.process_one()
    key = operation_key(prop)

    assert world.client.operation_record(key) == OperationRecord(
        campaign_id="c1", action="update_budget", new_budget=150, expected_version=1,
        version_after=2)
    assert world.client.operation_record("k1-missing") is None

    import sqlite3
    conn = sqlite3.connect(world.dsp_db)
    conn.execute("UPDATE operations SET expected_version = NULL")  # 補欄位之前寫的舊列
    conn.commit()
    conn.close()
    old = world.client.operation_record(key)
    assert old is not None and old.expected_version is None


def test_the_real_client_voids_a_key_through_the_dsp(world):
    prop = world.submit(campaign_version_observed=1)
    token = CapabilitySigner(TEST_KEY).sign_void(
        prop, operation_key(prop), world.config, int(world.clock().timestamp()))

    answer = world.client.void(prop, operation_key(prop), token)

    assert (answer.status, answer.state, answer.record) == (200, "voided", None)



def test_a_void_call_that_times_out_comes_back_as_no_answer(world):
    """作廢呼叫逾時要吞成「沒拿到回應」交給對照表(留在結果不明),不是讓例外炸穿對帳迴圈。"""
    prop = world.submit(campaign_version_observed=1)
    token = CapabilitySigner(TEST_KEY).sign_void(
        prop, operation_key(prop), world.config, int(world.clock().timestamp()))
    world.server.plan.append(("timeout_before_commit", 0))  # DSP 掛住比用戶端逾時久

    answer = world.client.void(prop, operation_key(prop), token)

    assert (answer.status, answer.state) == (None, None)
    conn = sqlite3.connect(world.dsp_db)
    try:  # DSP 真的沒有留下作廢紀錄(再作廢一次分不出來:作廢是冪等的,兩種情況都回已作廢)
        assert conn.execute("SELECT count(*) FROM voided_keys WHERE key = ?",
                            (operation_key(prop),)).fetchone()[0] == 0
    finally:
        conn.close()


def test_an_old_style_operation_answer_still_reads_back_its_version(monkeypatch):
    """兩支查詢共用查詢與「查不到」判斷,但欄位要求各自照舊:
    舊版 DSP 的成功回應沒有參數,查寫入後版本照樣讀得回來;核對完整內容那支才算讀不懂。"""
    from rtb.executor import dsp_client

    old = {"operation_id": 1, "campaign_id": "c1", "action": "update_budget",
           "version_after": 5, "committed_at": "2026-09-23T00:00:00+00:00", "replayed": False}
    client = dsp_client.DspClient("http://127.0.0.1:9", 1)
    monkeypatch.setattr(dsp_client, "request_json", lambda *_a, **_k: (200, old))

    assert client.operation_version("k1-x") == 5
    with pytest.raises(DspUnavailable):
        client.operation_record("k1-x")
