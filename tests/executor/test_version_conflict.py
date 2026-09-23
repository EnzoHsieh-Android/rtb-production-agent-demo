"""DSP 端的版本衝突回報成「版本已變」、收件口回報擋下原因、兩個寫入者重現衝突、衝突可查。

Phase 5 執行側:S310、S300、S309、S315(執行側那一半)。版本衝突有兩層:執行前檢查發現(收件表記
版本已變),或檢查通過後、寫入前被另一方搶先改(DSP 回 409,嘗試紀錄記版本衝突)。第二層原本一律
確認成「同一操作先前已失敗」,把原因蓋掉,分析端就不知道要重新規劃。
"""

import threading
from datetime import timedelta

import pytest

from rtb.domain.attempt import AttemptState as A
from rtb.domain.attempt import OutcomeCode, operation_key
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor import attempt_store
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import CampaignView, Executor, Result, WriteAnswer
from rtb.executor.inbox_store import BlockCode, InboxStore
from tests.capability_samples import TEST_KEY
from tests.executor.fakes import Harness, proposal, write_config

LATER = "2026-09-22T12:40:00+00:00"


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def _block_of(h, task_id="t1", revision=1):
    return next(row[3:] for row in h.proposals() if row[:2] == (task_id, revision))


def _fail_with_version_conflict(h, prop):
    """另一個工作者搶先開了這把鍵、DSP 回 409:嘗試紀錄停在失敗(版本衝突)。"""
    with h.store.transaction() as tx:
        begun = attempt_store.begin(tx, prop, h.clock(),
                                    capability_expires_at=h.clock() + timedelta(minutes=2))
        attempt_store.transition(tx, begun.row.key, begun.row.seq, A.FAILED, h.clock(),
                                 code=OutcomeCode.VERSION_CONFLICT)


# ---- S310 ----
def test_a_dsp_version_conflict_is_acknowledged_as_version_changed(h):
    # 第一處:終點確認。檢查通過、寫入時 DSP 回 409
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(409, "version_conflict"))
    h.process()
    assert attempt_store_state(h, first) == ("failed", "version_conflict")
    assert _block_of(h) == ("blocked", "version_changed")

    # 第三處:收件口取件時讀到這把鍵已經失敗(版本衝突);換到期時間的新修訂是同一把鍵
    h.store.accept(proposal(revision=2, decision_expires_at=LATER), h.clock)
    assert h.process().kind is Result.IDLE  # 取件時就確認,不交出去
    assert _block_of(h, revision=2) == ("blocked", "version_changed")

    # 第二處:開始一筆時才撞到既有失敗鍵(取件之後、開始之前,另一個工作者搶先失敗了)
    third = h.submit(task_id="t2")
    h.dsp.on_read = lambda _campaign: _fail_with_version_conflict(h, third)
    result = h.process()
    assert (result.kind, result.block_code) == (Result.BLOCKED, BlockCode.VERSION_CHANGED)
    assert _block_of(h, "t2") == ("blocked", "version_changed")


def test_other_failures_are_still_acknowledged_as_previously_failed(h):
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(403, "capability_scope_mismatch"))  # 不是版本衝突的失敗
    h.process()
    assert attempt_store_state(h, first)[0] in ("failed", "escalated")
    key = operation_key(first)
    with h.store.transaction() as tx:
        latest = attempt_store.latest(tx, key)
        if latest.state is not A.FAILED:
            attempt_store.resolve(tx, key, latest.seq, A.FAILED, "operator decided", h.clock())
    h.store.accept(proposal(revision=2, decision_expires_at=LATER), h.clock)
    h.process()
    assert _block_of(h, revision=2) == ("blocked", "operation_previously_failed")


# 使用者 2026-09-23 裁定擴大範圍:另外兩條「重跑執行前檢查」的路徑,檢查回版本已變時也確認成
# 「版本已變」;嘗試結果代碼照舊記「沒發生」(DSP 明確沒寫),只換收件口的擋下原因
def _bump_version(h, campaign="c1"):
    h.dsp.campaigns[campaign] = CampaignView(budget=100, status="active", version=9)


@pytest.mark.parametrize(("expired", "block"), [
    (False, "version_changed"),
    (True, "operation_previously_failed"),  # 過期優先,跟第一次處理時的順序一致(代碼審第 2 輪)
])
def test_a_version_change_found_after_capability_expiry_is_acknowledged_as_version_changed(
        h, expired, block):
    # 提案 20 秒後過期;時鐘只推 30 秒,不超過收件口租約(60 秒),這一輪寫得回去
    first = h.submit(decision_expires_at="2026-09-22T12:05:20+00:00")
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))

    def someone_else_writes(*_):  # 送出之後、重讀之前,別人改了廣告(也可能同時過期)
        _bump_version(h)
        if expired:
            h.clock.advance(seconds=30)

    h.dsp.on_write = someone_else_writes
    h.process()

    assert attempt_store_state(h, first) == ("failed", "not_happened")  # 結果代碼不動
    assert _block_of(h) == ("blocked", block)


def test_a_version_change_found_after_a_repeated_expiry_voids_then_says_version_changed(h):
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(None))  # 第一次送出沒回應:結果不明
    h.process()
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))  # 對帳重送:憑證過期

    def bump_after_the_resend(_campaign):
        if len(h.dsp.writes) == 2:
            _bump_version(h)

    h.dsp.on_read = bump_after_the_resend
    h.executor().reconcile_all()

    assert h.dsp.voids == [operation_key(first)]  # 送過兩次:先作廢才判失敗
    assert attempt_store_state(h, first) == ("failed", "not_happened")
    assert _block_of(h) == ("blocked", "version_changed")


def test_a_version_change_found_while_reconciling_is_acknowledged_as_version_changed(h):
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(None))
    h.process()
    _bump_version(h)  # DSP 查不到這把鍵,重讀時版本已變:先作廢再判失敗

    h.executor().reconcile_all()

    assert h.dsp.voids == [operation_key(first)]
    assert attempt_store_state(h, first) == ("failed", "not_happened")
    assert _block_of(h) == ("blocked", "version_changed")


@pytest.mark.parametrize("change", ["expired", "paused", "expired_and_version_changed"])
def test_other_failed_rechecks_are_still_acknowledged_as_previously_failed(h, change):
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(None))
    h.process()
    if change.startswith("expired"):
        h.clock.advance(hours=1)  # 提案過期:業務上不過,但不是版本已變
    if change == "paused":  # 檢查查到別的原因(不在投放):只有版本已變才換,其他照嘗試結果代碼
        h.dsp.campaigns["c1"] = CampaignView(budget=100, status="paused", version=3)
    if change == "expired_and_version_changed":  # 過期優先,跟第一次處理時的順序一致(代碼審第 2 輪)
        _bump_version(h)

    h.executor().reconcile_all()

    assert attempt_store_state(h, first) == ("failed", "not_happened")
    assert _block_of(h) == ("blocked", "operation_previously_failed")


def attempt_store_state(h, prop):
    with h.store.transaction() as tx:
        row = attempt_store.latest(tx, operation_key(prop))
        return row.state.value, (None if row.code is None else row.code.value)


# ---- S300 ----
def test_a_resend_reports_why_the_proposal_was_blocked(start_inbox):
    from rtb.domain.proposal import parse_proposal
    from tests.domain.proposal_samples import valid

    inbox = start_inbox()
    body = valid()
    first = inbox.post(body)
    assert first[0] == 201 and first[1]["block_code"] is None  # 鍵要存在,沒擋下就是空值

    store = InboxStore(inbox.db)
    try:
        with store.transaction() as tx:
            delivery = store.receive(tx, inbox.clock(), "worker")
            assert delivery is not None
            store.ack_blocked(tx, delivery.receipt, inbox.clock(), BlockCode.VERSION_CHANGED)
    finally:
        store.close()

    status, answer = inbox.post(body)  # 同一份提案重送:回目前狀態與擋下原因
    assert status == 200 and answer["state"] == "blocked"
    assert answer["block_code"] == "version_changed"
    assert set(answer) == {"status", "task_id", "revision", "state", "content_hash", "replayed",
                           "block_code"}
    assert parse_proposal(body).proposal is not None


@pytest.mark.parametrize(("stored", "answered"), [
    (BlockCode.OVER_BUDGET_CAP, "not_permitted"),  # 權限類合併成泛稱:試不出預算上限
    (BlockCode.CAMPAIGN_NOT_ALLOWED, "not_permitted"),  # 也試不出廣告歸哪個租戶
    # Phase 6 增量 2 [S407]:比例上限也是權限類
    (BlockCode.BUDGET_INCREASE_TOO_LARGE, "not_permitted"),
    (BlockCode.CAMPAIGN_NOT_FOUND, "campaign_not_found"),
    (BlockCode.CAMPAIGN_NOT_ACTIVE, "campaign_not_active"),
    (BlockCode.OPERATION_PREVIOUSLY_FAILED, "operation_previously_failed"),
])
def test_a_resend_hides_which_permission_blocked_it(start_inbox, stored, answered):
    """使用者 2026-09-23 裁定:只在回給分析行程的本文合併,收件表內部仍記細分代碼(稽核不失真)。"""
    from tests.domain.proposal_samples import valid

    inbox = start_inbox()
    body = valid()
    inbox.post(body)
    store = InboxStore(inbox.db)
    try:
        with store.transaction() as tx:
            delivery = store.receive(tx, inbox.clock(), "worker")
            assert delivery is not None
            store.ack_blocked(tx, delivery.receipt, inbox.clock(), stored)
    finally:
        store.close()

    assert inbox.post(body)[1]["block_code"] == answered
    assert inbox.rows("SELECT block_code FROM proposals") == [(stored.value,)]


# ---- S309 ----
@pytest.fixture
def real_dsp(tmp_path):
    CampaignStore(tmp_path / "dsp.db").seed_campaign("c1", budget=100)
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0, capability_key=TEST_KEY)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path / "dsp.db"
    server.shutdown()
    server.server_close()


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)  # 真 DSP 只容許 30 秒時鐘誤差:兩套系統都用真實時間


class _RacingClient(DspClient):
    """兩邊都讀過 DSP、執行前檢查都通過,在柵欄會合後才一起寫。"""

    barrier: threading.Barrier

    def write(self, prop, key, token, *, on_call):
        # 另一邊若在柵欄前就失敗(例如慢機器上讀 DSP 逾時),這邊等 5 秒後丟 BrokenBarrierError,
        # 由 run() 收進結果、斷言時直接印出,不會悄悄卡住
        self.barrier.wait(5)
        return super().write(prop, key, token, on_call=on_call)


def _one_writer(tmp_path, url, config, name, budget):
    """一套獨立的執行系統(自己的收件表與嘗試紀錄),共用同一個 DSP;回傳結果、最後嘗試、處置。"""
    store = InboxStore(tmp_path / f"{name}.db")
    try:
        created = _now()
        store.accept(proposal(
            task_id=name, requested_change={"new_budget": budget}, campaign_version_observed=1,
            decision_created_at=created.isoformat(),
            decision_expires_at=(created + timedelta(minutes=10)).isoformat()), _now)
        result = Executor(store, _RacingClient(url, 3.0), CapabilitySigner(TEST_KEY), config,
                          _now, owner=name).process_one()
        with store.transaction() as tx:
            attempt = tx.conn.execute(
                "SELECT state, code FROM attempts ORDER BY seq DESC LIMIT 1").fetchone()
            block = tx.conn.execute("SELECT disposition, block_code FROM proposals").fetchone()
        return result.kind, attempt, block
    finally:
        store.close()


def test_two_concurrent_writers_reproduce_a_version_conflict(tmp_path, real_dsp):
    url, dsp_db = real_dsp
    config = write_config(tmp_path / "tenants.json")
    _RacingClient.barrier = threading.Barrier(2)
    outcomes = {}

    def run(name, budget):
        # 執行緒裡的例外不會傳回主執行緒:收進結果,斷言失敗時直接看得到原因(代碼審第 1 輪併發席)
        try:
            outcomes[name] = _one_writer(tmp_path, url, config, name, budget)
        except BaseException as exc:
            outcomes[name] = ("error", repr(exc))

    # 兩份新預算都在比例上限內(Phase 6 增量 2 起,從 100 最多加到 150)
    threads = [threading.Thread(target=run, args=args) for args in (("a", 150), ("b", 140))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)

    assert len(outcomes) == 2 and all(o[0] != "error" for o in outcomes.values()), outcomes
    attempts = sorted(outcome[1] for outcome in outcomes.values())
    assert attempts == [("failed", "version_conflict"), ("verified", None)]  # 只有一方寫進去
    loser = next(outcome for outcome in outcomes.values() if outcome[1][0] == "failed")
    assert loser[2] == ("blocked", "version_changed")  # 收件口擋下原因記版本已變
    dsp = CampaignStore(dsp_db)
    try:
        assert [entry.action for entry in dsp.history("c1")] == ["update_budget"]
        assert dsp.get_campaign("c1").version == 2
    finally:
        dsp.close()


# ---- S315(執行側) ----
def test_conflict_counts_are_queryable(h):
    h.dsp.campaigns["c2"] = h.dsp.campaigns["c1"]
    for task_id, campaign in (("t1", "c1"), ("t2", "c2"), ("t3", "c1")):
        h.submit(task_id=task_id, campaign_id=campaign)
        h.dsp.answers.append(WriteAnswer(409, "version_conflict"))
        h.process()
    h.submit(task_id="t4")
    h.dsp.answers.append(WriteAnswer(403, "capability_scope_mismatch"))  # 別種失敗不算
    h.process()

    with h.store.transaction() as tx:
        assert attempt_store.version_conflict_count(tx) == 3
        assert attempt_store.version_conflict_count(tx, "c1") == 2
        assert attempt_store.version_conflict_count(tx, "c2") == 1
        assert attempt_store.version_conflict_count(tx, "c9") == 0
