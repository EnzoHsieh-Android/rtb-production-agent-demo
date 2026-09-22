"""執行一筆的端到端:真的 Mock DSP(測試行程內的執行緒)+ 真的執行行程 DSP 用戶端。

執行行程的用戶端送不出故障注入標頭(共用用戶端的封閉列舉擋掉),所以故障改由測試在 DSP
伺服器那一側排定:每個寫入請求依序取一個安排(故障模式、DSP 時鐘偏移)。
"""

import threading

import pytest

from rtb.domain.attempt import operation_key
from rtb.dsp.server import DspHandler, DspServer
from rtb.dsp.store import CampaignStore, Operation
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import DspUnavailable, Executor, Result
from rtb.executor.inbox_store import InboxStore
from tests.capability_samples import TEST_KEY
from tests.executor.fakes import proposal, write_config


class PlannedHandler(DspHandler):
    def read_fault(self, modes):
        if self.command != "POST" or not self.server.plan:
            return None
        fault, offset = self.server.plan.pop(0)
        self.server.offset = offset
        return fault


class PlannedDsp(DspServer):
    def __init__(self, db, clock):
        super().__init__(db, fault_injection=True, hang_seconds=0.6, delay_seconds=0.0,
                         capability_key=TEST_KEY,
                         clock=lambda: clock().timestamp() + self.offset)
        self.RequestHandlerClass = PlannedHandler
        self.plan: list[tuple[str | None, int]] = []
        self.offset = 0


class World:
    def __init__(self, tmp_path, clock, dsp_timeout=3.0):
        self.clock = clock
        self.dsp_db = tmp_path / "dsp.db"
        CampaignStore(self.dsp_db).seed_campaign("c1", budget=100)
        self.server = PlannedDsp(self.dsp_db, clock)
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
