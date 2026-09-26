"""執行前檢查多兩項(Phase 8):政策版本與決策新鮮度。S504、S505、S511、S512。

政策版本不等於現行版本就擋成「政策已變」;現在比決策建立晚超過 15 分鐘就擋成「決策已過時」,
剛好 15 分鐘通過。新鮮度在開始一筆的交易裡再判一次(檢查通過之後才跨線的也擋)。兩條重跑路徑
(憑證過期後重讀、對帳查不到寫入)命中新原因時不再送,收件口確認成新原因(使用者 2026-09-24
裁定)。有它停下那一關的有效核可的提案不判新鮮度,其他檢查照跑(使用者 2026-09-24 裁定)。
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from rtb.domain.attempt import operation_key
from rtb.domain.proposal import POLICY_VERSION
from rtb.executor import approval, attempt_store
from rtb.executor.execution import CampaignView, Executor, Result, WriteAnswer
from rtb.executor.inbox_store import BlockCode
from tests.capability_samples import TEST_APPROVAL_KEY
from tests.executor.conftest import NOW
from tests.executor.fakes import Harness, proposal, write_config
from tests.executor.test_approval import approve_it, settle, submit, tenant_of, waiting

CREATED = NOW - timedelta(minutes=5)  # 樣本提案的決策建立時間(12:00)
STALE = BlockCode.DECISION_STALE
RATIO = BlockCode.BUDGET_INCREASE_TOO_LARGE


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def _block_of(h, task_id="t1"):
    return next(row[3:] for row in h.proposals() if row[0] == task_id)


def _attempt(h, prop):
    with h.store.transaction() as tx:
        row = attempt_store.latest(tx, operation_key(prop))
    return None if row is None else (row.state.value, None if row.code is None else row.code.value)


# ---- [S504] ----
def test_a_proposal_from_another_policy_version_is_blocked(h):
    h.submit(policy_version="demo-pacing-v0")

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, BlockCode.POLICY_VERSION_CHANGED)
    assert _block_of(h) == ("blocked", "policy_version_changed")
    assert h.dsp.writes == []


def test_old_policy_proposals_are_blocked_after_the_rule_change(h):
    """[S1416] 九條政策上線換新版本:舊「有投放就加」政策建的提案,即使廣告版本沒變、還在 30 分鐘內,
    執行端照 [S504] 擋成政策已變,DSP 寫入 0 次;舊值留在已知版本清單只供指標分類。"""
    from rtb.domain.proposal import KNOWN_POLICY_VERSIONS
    assert POLICY_VERSION != "demo-pacing-v1"
    assert KNOWN_POLICY_VERSIONS[:1] == ("demo-pacing-v1",)
    assert KNOWN_POLICY_VERSIONS[-1] == POLICY_VERSION
    h.submit(policy_version="demo-pacing-v1")

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, BlockCode.POLICY_VERSION_CHANGED)
    assert h.dsp.writes == []


def test_the_current_policy_version_passes(h):
    h.submit(policy_version=POLICY_VERSION)

    assert h.process().kind is Result.EXECUTED


# ---- [S505] ----
@pytest.mark.parametrize(("age", "expected"), [
    (timedelta(minutes=15), Result.EXECUTED),  # 剛好 15 分鐘:通過
    (timedelta(minutes=15, seconds=1), Result.BLOCKED),
])
def test_a_stale_decision_is_blocked(h, age, expected):
    h.submit()
    h.clock.now = CREATED + age

    result = h.process()

    assert result.kind is expected
    if expected is Result.BLOCKED:
        assert result.block_code is STALE
        assert _block_of(h) == ("blocked", "decision_stale")
        assert h.dsp.writes == []


def test_a_decision_that_goes_stale_after_the_precheck_is_blocked_before_the_write(
        h, monkeypatch):
    h.submit()
    h.clock.now = CREATED + timedelta(minutes=15)  # 執行前檢查那一刻剛好 15 分鐘:通過
    original = Executor._approvals

    def slow_lookup(self, *args, **kwargs):  # 查核可花了一秒:進開始一筆的交易時已過線
        found = original(self, *args, **kwargs)
        h.clock.advance(seconds=1)
        return found

    monkeypatch.setattr(Executor, "_approvals", slow_lookup)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)
    assert h.attempts() == []  # 沒開嘗試
    assert h.dsp.writes == []


# ---- [S511] ----
@pytest.mark.parametrize(("change", "block"), [
    ("stale", "decision_stale"),
    ("policy", "policy_version_changed"),
])
def test_a_rerun_that_hits_a_new_reason_keeps_the_reason(h, monkeypatch, change, block):
    first = h.submit()
    h.clock.now = CREATED + timedelta(minutes=14, seconds=50)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))

    def the_rule_changes_before_the_reread(*_):
        if change == "stale":  # 只推 20 秒:不超過收件口租約(60 秒),這一輪寫得回去
            h.clock.advance(seconds=20)
        else:
            monkeypatch.setattr("rtb.executor.execution.POLICY_VERSION", "demo-pacing-v2")

    h.dsp.on_write = the_rule_changes_before_the_reread
    h.process()

    assert _attempt(h, first) == ("failed", "not_happened")  # 憑證過期是 DSP 明確拒收
    assert _block_of(h) == ("blocked", block)
    assert len(h.dsp.writes) == 1  # 不再送


def test_a_reconcile_rerun_that_finds_a_stale_decision_voids_then_keeps_the_reason(h):
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(None))  # 第一次送出沒回應:結果不明
    h.process()
    h.clock.now = CREATED + timedelta(minutes=16)

    h.executor().reconcile_all()

    assert h.dsp.voids == [operation_key(first)]  # DSP 查不到:先作廢才判失敗(既有規則)
    assert _attempt(h, first) == ("failed", "not_happened")
    assert _block_of(h) == ("blocked", "decision_stale")


# ---- [S512] ----
def test_an_approved_proposal_skips_only_the_freshness_check(h):
    prop = waiting(h, RATIO)  # 12:05 停在比例過大
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, RATIO)
    assert settle(h) == 1  # 放回待處理

    result = h.process()

    assert result.kind is Result.EXECUTED  # 超過 15 分鐘仍能執行
    assert len(h.dsp.writes) == 1


def test_an_approved_proposal_still_runs_the_other_checks(h):
    prop = waiting(h, RATIO)
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, RATIO)
    settle(h)
    h.dsp.campaigns["c1"] = CampaignView(budget=100, status="paused", version=3)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, BlockCode.CAMPAIGN_NOT_ACTIVE)


def test_a_proposal_without_a_valid_approval_is_still_blocked_as_stale(h):
    submit(h, 120)  # 比例內:不需要核可
    h.clock.now = CREATED + timedelta(minutes=20)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)


def test_an_approval_for_another_proposal_does_not_exempt_a_stale_one(h):
    prop = waiting(h, RATIO)
    other = submit(h, 151, task_id="t2", campaign_id="c2")
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, other, RATIO, signed_for=prop)  # t2 那一關掛的是替 t1 簽的核可:不算數

    result = h.process()  # t1 在待核可,取到的是 t2:沒有有效核可、又過時

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)


def test_a_stale_proposal_over_the_ratio_is_blocked_rather_than_left_waiting(h):
    """過時又超過比例、沒有核可:直接擋成決策已過時,不停在待核可等一張注定用不上的核可。"""
    submit(h, 151)
    h.clock.now = CREATED + timedelta(minutes=16)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)
    assert h.proposals()[0][3:] == ("blocked", "decision_stale")


def test_a_resend_that_goes_stale_while_resigning_is_not_sent(h, monkeypatch):
    first = h.submit()
    h.clock.now = CREATED + timedelta(minutes=14, seconds=50)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    original = Executor._resign

    def slow_resign(self, *args, **kwargs):  # 重跑的檢查通過之後、轉回送出中之前跨過 15 分鐘
        signed = original(self, *args, **kwargs)
        h.clock.advance(seconds=20)
        return signed

    monkeypatch.setattr(Executor, "_resign", slow_resign)
    h.process()

    assert _attempt(h, first) == ("failed", "not_happened")
    assert _block_of(h) == ("blocked", "decision_stale")
    assert len(h.dsp.writes) == 1  # 沒有重送


def test_an_approved_resend_is_not_blocked_as_stale(h):
    prop = waiting(h, RATIO)
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, RATIO)
    settle(h)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))

    h.process()  # 第一次送出憑證過期,重讀重跑:用過核可,不判新鮮度,同鍵重送

    assert len(h.dsp.writes) == 2
    assert _attempt(h, prop)[0] == "verified"


# ---- 代碼審第 1 輪:有效核可只放行它核准、而且這一次真的需要的那一關 ----
AGGREGATE = BlockCode.AGGREGATE_LIMIT_REACHED


def test_an_unneeded_approval_does_not_exempt_a_stale_decision(h):
    """同一份提案掛著一張用不到的總曝險核可(比例與總曝險都沒觸發):照樣擋成決策已過時。"""
    prop = submit(h, 120)
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, AGGREGATE)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)
    assert h.dsp.writes == []


def test_an_approval_for_the_other_gate_does_not_exempt_a_stale_decision(h):
    """過時、超過比例、沒有比例的核可;掛著的是總曝險那一關的核可:擋成決策已過時,不停待核可。"""
    limit_all_but(h)
    prop = submit(h, 151)
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, AGGREGATE)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)


def test_a_stale_decision_approved_for_one_gate_is_blocked_at_the_next(h):
    """比例那一關核可放回之後,開始一筆時撞到總曝險:過時的決策不停進待核可去等另一張核可。
    不改設定檔造總曝險已滿(門檻是核可範圍指紋的一部分,改了比例那張就不算數,測不到這條路)。"""
    write_config(h.config, aggregate_limit=60)
    prop = waiting(h, RATIO)  # t1 加 51:超過比例、額度內
    submit(h, 150, task_id="t2", campaign_id="c2")  # 另一份加 50:比例內,先用掉額度
    assert h.process().kind is Result.EXECUTED
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, RATIO)
    assert settle(h) == 1

    result = h.process()  # 50 + 51 > 60:總曝險已滿

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)
    assert len(h.dsp.writes) == 1  # 只有 t2 那一筆


def test_a_resend_whose_approval_expires_before_the_transition_is_not_sent(h, monkeypatch):
    prop = waiting(h, RATIO)
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, RATIO, expires_in=30)
    settle(h)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    original = Executor._resign

    def slow_resign(self, *args, **kwargs):  # 重簽時核可還算數,轉回嘗試中之前過期
        signed = original(self, *args, **kwargs)
        h.clock.advance(seconds=31)
        return signed

    monkeypatch.setattr(Executor, "_resign", slow_resign)
    h.process()

    assert len(h.dsp.writes) == 1  # 沒有重送
    assert _attempt(h, prop) == ("failed", "not_happened")
    assert _block_of(h) == ("blocked", "decision_stale")


def limit_all_but(h):
    """總曝險門檻給寬鬆值(比例過大那一關先觸發)。"""
    write_config(h.config)


# ---- 代碼審第 1 輪測試席:核可本身也比對提案的政策版本(不靠範圍指紋連帶) ----
def test_an_approval_does_not_hold_for_a_proposal_from_another_policy_version(h):
    prop = proposal(policy_version="demo-pacing-v0", requested_change={"new_budget": 151})
    tenant = tenant_of(h)
    token = approval.issue(TEST_APPROVAL_KEY, prop, RATIO, tenant, approver="ops",
                           max_increase=1000, issued_at=int(h.clock().timestamp()),
                           expires_at=int(h.clock().timestamp()) + 60)
    found = approval.read(token, TEST_APPROVAL_KEY)

    assert not approval.holds(found, prop, RATIO, tenant, 51, h.clock())
    current = replace(prop, policy_version=POLICY_VERSION)  # 對照:只差政策版本就算數
    token = approval.issue(TEST_APPROVAL_KEY, current, RATIO, tenant, approver="ops",
                           max_increase=1000, issued_at=int(h.clock().timestamp()),
                           expires_at=int(h.clock().timestamp()) + 60)
    assert approval.holds(approval.read(token, TEST_APPROVAL_KEY), current, RATIO, tenant, 51,
                          h.clock())


# ---- 代碼審第 2 輪:「這一次需不需要總曝險核可」以開始一筆的交易裡重算的為準 ----
def _usage(monkeypatch, *values):
    """總曝險已用額度:第一次讀(開始一筆前的預判)給第一個值,之後(開始一筆的交易裡)給第二個。"""
    reads = iter(values)
    monkeypatch.setattr(attempt_store, "aggregate_used",
                        lambda _tx, _tenant, _now, **_kw: next(reads, values[-1]))


def test_an_aggregate_approval_freed_up_before_the_write_does_not_exempt_a_stale_decision(
        h, monkeypatch):
    """預判要用總曝險核可,進開始一筆的交易時額度已被釋放:這張沒用上,過時的決策照樣擋。"""
    write_config(h.config, aggregate_limit=100)
    prop = submit(h, 120)  # 加 20:比例內
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, AGGREGATE)
    _usage(monkeypatch, 90, 0)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)
    assert h.dsp.writes == []


def test_an_aggregate_approval_needed_only_inside_the_write_still_counts(h, monkeypatch):
    """預判用不到總曝險核可,進開始一筆的交易時額度滿了:最新那張有效核可照樣算數,照裁定放行。
    但憑證是照「用不到」簽的,沒壓到核可到期:這一輪放掉重來,下一輪預判拿到核可、壓短憑證再寫
    (代碼審第 3 輪外家兩席:當場放行的話,憑證比核可多活,違反增量 3 [S376])。"""
    write_config(h.config, aggregate_limit=100)
    prop = submit(h, 120)
    h.clock.now = CREATED + timedelta(minutes=20)
    token = approve_it(h, prop, AGGREGATE, expires_in=30)
    _usage(monkeypatch, 0, 90)

    assert h.process().kind is Result.DEFERRED  # 這一輪放掉,沒開嘗試、沒送 DSP
    assert h.attempts() == [] and h.dsp.writes == []

    assert h.process().kind is Result.EXECUTED  # 下一輪:預判要用,憑證壓到核可到期
    assert len(h.dsp.writes) == 1
    assert h.query("SELECT stage FROM approval_uses") == [("aggregate_limit_reached",)]
    capability = h.query("SELECT capability_expires_at FROM attempts WHERE seq = 1")[0][0]
    assert capability <= attempt_store.iso(
        datetime.fromtimestamp(approval.read(token, TEST_APPROVAL_KEY).expires_at, UTC))


def test_a_replaced_approval_is_reread_before_the_freshness_check(h, monkeypatch):
    """查好的那張在開始一筆之前過期、但已有人簽了更新的一張:先照既有規則放掉重來,不先擋成過時。"""
    prop = waiting(h, RATIO)
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, RATIO, expires_in=30)
    settle(h)
    original = Executor._approvals

    def renewed_meanwhile(self, *args, **kwargs):
        found = original(self, *args, **kwargs)
        h.clock.advance(seconds=31)  # 查好的那張過期
        approve_it(h, prop, RATIO, expires_in=300)  # 同時有人簽了新的一張
        return found

    monkeypatch.setattr(Executor, "_approvals", renewed_meanwhile)
    assert h.process().kind is Result.DEFERRED
    monkeypatch.setattr(Executor, "_approvals", original)

    assert h.process().kind is Result.EXECUTED  # 下一輪用上新的那張


def test_a_stale_decision_whose_ratio_approval_lapses_before_the_write_is_blocked(
        h, monkeypatch):
    """查好的比例核可在開始一筆之前過期、沒有新的:決策又已過時,擋下,不回待核可等新核可。"""
    prop = waiting(h, RATIO)
    h.clock.now = CREATED + timedelta(minutes=20)
    approve_it(h, prop, RATIO, expires_in=30)
    settle(h)
    original = Executor._approvals

    def lapse_meanwhile(self, *args, **kwargs):
        found = original(self, *args, **kwargs)
        h.clock.advance(seconds=31)
        return found

    monkeypatch.setattr(Executor, "_approvals", lapse_meanwhile)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, STALE)
