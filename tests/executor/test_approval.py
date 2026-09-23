"""人工核可(Phase 6 增量 3):對應計劃的 S350 到 S359、S370 到 S379。

被總曝險已滿或比例過大擋下、還沒有有效核可的提案停在待核可;每輪的處理待核可在核可到了時放回
待處理、到期時確認成已擋下、同任務有更新的修訂時確認成被取代。核可用另一把金鑰簽,綁定提案雜湊、
關卡、範圍指紋與到期;用到核可的那一筆,憑證不能活得比核可久,同鍵重送前也重判。
"""

import io
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from rtb.analyzer.flow import _belongs_to, _from_inbox_answer
from rtb.capabilitykit import APPROVAL_KEY_ENV, KEY_ENV, decode, encode
from rtb.domain.attempt import AttemptState, operation_key
from rtb.domain.proposal import MAX_INT, POLICY_VERSION, content_hash
from rtb.dsp.capability import CapabilityExpired, verified_claims
from rtb.executor import approval, approve, attempt_store, capability_signer, guardrails
from rtb.executor.capability_signer import Tenant, load_tenants
from rtb.executor.execution import Result, WriteAnswer
from rtb.executor.inbox_store import ApprovalUse, BlockCode, InboxBusy, InboxStore
from tests.capability_samples import TEST_APPROVAL_KEY, TEST_KEY
from tests.executor.fakes import LOOSE_AGGREGATE_LIMIT, Harness, proposal, write_config

RATIO = BlockCode.BUDGET_INCREASE_TOO_LARGE
AGGREGATE = BlockCode.AGGREGATE_LIMIT_REACHED
TENANT = "t-default"


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def submit(h, new_budget=151, **overrides):
    """現行政策版本的加預算提案(樣本預設的政策版本不是目前那一版,核可一律不算數)。"""
    overrides.setdefault("policy_version", POLICY_VERSION)
    return h.submit(requested_change={"new_budget": new_budget}, **overrides)


def tenant_of(h, campaign="c1"):
    return next(t for t in load_tenants(h.config) if campaign in t.campaigns)


def approve_it(h, prop, stage, max_increase=1000, expires_in=600, *, key=TEST_APPROVAL_KEY,  # noqa: PLR0913 - 每個參數是核可的一個欄位
               signed_for=None, issued_at=None):
    """照管理工具的做法簽一張核可、寫進核可表;signed_for 用來造出綁錯提案的核可。"""
    issued = int(h.clock().timestamp()) if issued_at is None else issued_at
    token = approval.issue(key, signed_for or prop, stage, tenant_of(h, prop.campaign_id),
                           approver="ops", max_increase=max_increase, issued_at=issued,
                           expires_at=issued + expires_in)
    h.store.add_approval(prop, stage, approval.approval_id(token), token, h.clock())
    return token


def limit(h, value):
    write_config(h.config, aggregate_limit=value)


def waiting(h, stage, task_id="t1", campaign_id="c1"):
    """造一份停在這一關的待核可提案。"""
    if stage is AGGREGATE:
        limit(h, 10)
        prop = submit(h, 150, task_id=task_id, campaign_id=campaign_id)  # 加 50:比例內、超過 10
    else:
        prop = submit(h, 151, task_id=task_id, campaign_id=campaign_id)  # 加 51:超過五成
    result = h.process()
    assert (result.kind, result.block_code) == (Result.AWAITING_APPROVAL, stage)
    return prop


def settle(h):
    return h.executor().process_awaiting()


def row(h, task_id="t1", revision=1):
    return h.query("SELECT state, disposition, block_code, deliveries FROM proposals "
                   "WHERE task_id = ? AND revision = ?", (task_id, revision))[0]


def stops(h):
    return h.query("SELECT kind, task_id, tenant, amount, used, cap FROM write_stops ORDER BY id")


def uses(h):
    return h.query("SELECT approval_id, task_id, tenant, stage, amount, used, cap, capped "
                   "FROM approval_uses ORDER BY id")


def first_row(h, prop):
    return h.query("SELECT tenant, reserved_amount FROM attempts WHERE key = ? AND seq = 1",
                   (operation_key(prop),))


def fresh(h, **overrides):
    """跟著目前時鐘建立的提案(撥過時鐘之後還要收新提案時用)。"""
    now = h.clock()
    return proposal(decision_created_at=now.isoformat(),
                    decision_expires_at=(now + timedelta(minutes=10)).isoformat(), **overrides)


# ---- [S350] ----
@pytest.mark.parametrize("stage", [RATIO, AGGREGATE])
def test_an_approvable_block_waits_for_approval(h, stage):
    waiting(h, stage)

    assert row(h) == ("pending", "awaiting_approval", stage.value, 1)
    amount, used, cap = (51, None, None) if stage is RATIO else (50, 0, 10)
    assert stops(h) == [(stage.value, "t1", TENANT, amount, used, cap)]
    assert h.attempts() == [] and h.dsp.writes == []  # 不開嘗試、不呼叫 DSP
    assert h.process().kind is Result.IDLE  # 取件不會撿到待核可

    h.clock.advance(hours=3)  # 過了保留期,收件時的清除也不清它(任務還沒結束)
    h.store.accept(fresh(h, task_id="t2", campaign_id="c2"), h.clock)
    assert row(h)[1] == "awaiting_approval"


# ---- [S351] ----
HARD_RULES = {
    BlockCode.CAMPAIGN_NOT_FOUND: lambda h: h.dsp.campaigns.pop("c1"),
    BlockCode.CAMPAIGN_NOT_ACTIVE: lambda h: h.dsp.campaigns.__setitem__(
        "c1", replace(h.dsp.campaigns["c1"], status="paused")),
    BlockCode.VERSION_CHANGED: lambda h: h.dsp.campaigns.__setitem__(
        "c1", replace(h.dsp.campaigns["c1"], version=4)),
    BlockCode.CAMPAIGN_NOT_ALLOWED: lambda h: write_config(h.config, campaigns=("c2",)),
    BlockCode.OVER_BUDGET_CAP: lambda h: write_config(h.config, max_budget=120),
}


@pytest.mark.parametrize("code", list(HARD_RULES))
def test_hard_rules_are_never_approvable(h, code):
    prop = submit(h, 151)  # 同時也超過比例
    approve_it(h, prop, RATIO)
    approve_it(h, prop, AGGREGATE)  # 兩關都有有效核可也一樣
    HARD_RULES[code](h)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, code)
    assert row(h)[1:3] == ("blocked", code.value)
    assert stops(h) == [] and h.attempts() == [] and h.dsp.writes == []


# ---- [S352] ----
@pytest.mark.parametrize("stage", [RATIO, AGGREGATE])
def test_an_unapproved_proposal_expires_into_blocked(h, stage):
    waiting(h, stage)
    assert settle(h) == 0  # 還沒到期、沒有核可:不動
    assert row(h)[1] == "awaiting_approval"

    h.clock.advance(minutes=25)  # 提案 12:30 到期

    assert settle(h) == 1
    assert row(h)[:3] == ("pending", "blocked", stage.value)
    assert settle(h) == 0  # 已經結案,不會再處理


# ---- [S353] ----
def test_a_valid_approval_lets_the_proposal_through_and_still_reserves(h):
    ratio = waiting(h, RATIO)
    approve_it(h, ratio, RATIO, max_increase=51)
    assert settle(h) == 1
    assert row(h) == ("pending", None, None, 0)  # 放回待處理、投遞次數歸零
    assert h.process().kind is Result.EXECUTED  # 重新處理時略過比例那一條
    assert first_row(h, ratio) == [(TENANT, 51)]

    moved = waiting(h, RATIO, task_id="t3", campaign_id="c3")  # 其他規則照跑:版本已變照樣擋
    approve_it(h, moved, RATIO, max_increase=51)
    assert settle(h) == 1
    h.dsp.campaigns["c3"] = replace(h.dsp.campaigns["c3"], version=4)
    assert h.process().block_code is BlockCode.VERSION_CHANGED

    full = waiting(h, AGGREGATE, task_id="t2", campaign_id="c2")  # 已用 51,門檻 10
    approve_it(h, full, AGGREGATE, max_increase=50)
    assert settle(h) == 1
    assert h.process().kind is Result.EXECUTED  # 允許超過門檻
    assert first_row(h, full) == [(TENANT, 50)]  # 照樣寫預留


# ---- [S354] ----
def _wrong_key(h, prop):
    approve_it(h, prop, RATIO, key=b"k" * 40)


def _other_revision(h, prop):
    approve_it(h, prop, RATIO, signed_for=replace(prop, revision=2))


def _other_content(h, prop):
    approve_it(h, prop, RATIO, signed_for=replace(prop, campaign_version_observed=4))


def _other_stage(h, prop):
    token = approval.issue(TEST_APPROVAL_KEY, prop, AGGREGATE, tenant_of(h), approver="ops",
                           max_increase=1000, issued_at=int(h.clock().timestamp()),
                           expires_at=int(h.clock().timestamp()) + 600)
    h.store.add_approval(prop, RATIO, approval.approval_id(token), token, h.clock())


def _scope_changed(h, prop):
    approve_it(h, prop, RATIO)
    write_config(h.config, max_budget=999)


def _expired(h, prop):
    approve_it(h, prop, RATIO, expires_in=60)
    h.clock.advance(seconds=60)


def _amount_over(h, prop):
    approve_it(h, prop, RATIO, max_increase=50)  # 這筆加 51


def _forged(field, value):
    """內容雜湊照抄、只改任務或修訂的核可(雜湊之外的兩欄各自也要比對)。"""
    def spoil(h, prop):
        now = int(h.clock().timestamp())
        claims = {"v": approval.FORMAT_VERSION, "task_id": prop.task_id, "revision": prop.revision,
                  "content_hash": content_hash(prop), "stage": RATIO.value,
                  "fingerprint": approval.scope_fingerprint(tenant_of(h)), "approver": "ops",
                  "max_increase": 1000, "iat": now, "exp": now + 600, field: value}
        token = encode(claims, TEST_APPROVAL_KEY)
        h.store.add_approval(prop, RATIO, approval.approval_id(token), token, h.clock())
    return spoil


@pytest.mark.parametrize("spoil", [_wrong_key, _other_revision, _other_content, _other_stage,
                                   _scope_changed, _expired, _amount_over,
                                   _forged("task_id", "t9"), _forged("revision", 2)])
def test_an_approval_is_void_when_scope_expiry_hash_stage_or_policy_changes(h, spoil):
    prop = waiting(h, RATIO)
    spoil(h, prop)

    assert settle(h) == 0
    assert row(h)[1] == "awaiting_approval"


# ---- [S355] ----
def test_an_applied_approval_is_audited(h):
    ratio = waiting(h, RATIO)
    ratio_token = approve_it(h, ratio, RATIO, max_increase=51)
    settle(h)
    h.process()
    full = waiting(h, AGGREGATE, task_id="t2", campaign_id="c2")
    full_token = approve_it(h, full, AGGREGATE, max_increase=50)
    settle(h)
    h.process()
    limit(h, LOOSE_AGGREGATE_LIMIT)
    spare = submit(h, 110, task_id="t3", campaign_id="c3")
    approve_it(h, spare, AGGREGATE)  # 設定改完才簽:核可是有效的
    approve_it(h, spare, RATIO)
    assert h.process().kind is Result.EXECUTED  # 兩關都有核可但都沒用上(沒超過):不記
    assert decode(h.dsp.writes[-1][2], TEST_KEY)["exp"] == int(h.clock().timestamp()) + 120
    assert h.executor().process_awaiting() == 0

    assert uses(h) == [
        (approval.approval_id(ratio_token), "t1", TENANT, RATIO.value, 51, None, None, 0),
        (approval.approval_id(full_token), "t2", TENANT, AGGREGATE.value, 50, 51, 10, 0)]
    with h.store.transaction() as tx:  # 同一份提案同一關只記一列;數字封頂並標記
        h.store.record_approval_use(tx, ApprovalUse(
            "x", full, "k", TENANT, AGGREGATE, 1, 2, 3), h.clock())
        h.store.record_approval_use(tx, ApprovalUse(
            "y", spare, "k", TENANT, AGGREGATE, 1, 2 * MAX_INT, MAX_INT), h.clock())
    assert len(uses(h)) == 3
    assert uses(h)[1][0] == approval.approval_id(full_token)  # 第一次記的那列沒被蓋掉
    assert uses(h)[2][5:] == (MAX_INT, MAX_INT, 1)


# ---- [S356] ----
def test_awaiting_approval_reads_as_still_open(h):
    prop = waiting(h, RATIO)

    answer = h.store.accept(prop, h.clock)  # 分析端重送同一份提案問狀態

    assert (answer.state, answer.block_code) == ("awaiting_approval", None)
    assert _belongs_to(answer, prop)  # 讀得懂
    assert _from_inbox_answer(answer) is None  # 當成等待:這一輪沒有進展,不結案、不重新規劃


# ---- [S357] ----
def _tool_args(h, **overrides):
    values = {"task-id": "t1", "revision": "1", "stage": RATIO.value, "max-increase": "51",
              "approver": "ops", "expires-in-seconds": "600", **overrides}
    args = ["--db", str(h.db), "--tenant-config", str(h.config)]
    for name, value in values.items():
        args += [f"--{name}", value]
    return args


def test_the_approval_tool_signs_with_its_own_key(h):
    waiting(h, RATIO)
    env = {APPROVAL_KEY_ENV: TEST_APPROVAL_KEY.decode()}
    out = io.StringIO()

    assert approve.run(_tool_args(h), environ=env, clock=h.clock, out=out) == 0

    token = h.query("SELECT token FROM approvals")[0][0]
    assert out.getvalue().strip() == approval.approval_id(token)
    assert approval.read(token, TEST_APPROVAL_KEY) is not None
    assert approval.read(token, TEST_KEY) is None  # 不是用簽發金鑰簽的
    signing_only = {KEY_ENV: TEST_KEY.decode()}
    assert approve.run(_tool_args(h), environ=signing_only, clock=h.clock) == approve.EXIT_NO_KEY
    assert approve.run(_tool_args(h, **{"expires-in-seconds": "3600"}), environ=env,
                       clock=h.clock) == approve.EXIT_REFUSED  # 不能晚於提案到期
    assert approve.run(_tool_args(h, **{"task-id": "t9"}), environ=env,
                       clock=h.clock) == approve.EXIT_NOT_FOUND
    assert len(h.query("SELECT token FROM approvals")) == 1
    assert settle(h) == 1
    assert approve.run(_tool_args(h), environ=env,
                       clock=h.clock) == approve.EXIT_NOT_FOUND  # 已放回:不再是待核可


# ---- [S358] ----
def test_an_old_inbox_accepts_the_awaiting_approval_disposition(tmp_path, clock):
    db = tmp_path / "executor.db"
    InboxStore(db).close()
    conn = sqlite3.connect(db)  # 模擬加入待核可之前的收件表:處置的允許值清單沒有它
    try:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'proposals'").fetchone()[0]
        old_sql = sql.replace(", 'awaiting_approval'", "")
        assert old_sql != sql
        conn.executescript(f"DROP TABLE proposals; {old_sql};")
    finally:
        conn.close()

    h = Harness(tmp_path, clock)  # 開啟時重建
    try:
        waiting(h, RATIO)
        assert row(h)[1] == "awaiting_approval"
    finally:
        h.close()


# ---- [S359] ----
@pytest.mark.parametrize("stage", [RATIO, AGGREGATE])
def test_an_approval_that_expires_mid_flight_lets_nothing_through(h, monkeypatch, stage):
    prop = waiting(h, stage)
    approve_it(h, prop, stage, max_increase=51, expires_in=10)  # 比租約短,撥過去收據仍有效
    assert settle(h) == 1
    real = h.signer.grant

    def slow(*args, **kwargs):
        granted = real(*args, **kwargs)
        if args[4] is not None:  # 用核可封頂的那次簽發:簽完核可剛好過期
            h.clock.advance(seconds=11)
        return granted

    monkeypatch.setattr(h.signer, "grant", slow)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.AWAITING_APPROVAL, stage)
    assert row(h)[1] == "awaiting_approval"
    assert h.attempts() == [] and h.dsp.writes == []


# ---- [S370] ----
def test_an_approval_covers_only_its_own_stage(h):
    limit(h, 10)
    prop = submit(h, 151)  # 超過比例,也超過總曝險
    assert h.process().block_code is RATIO
    approve_it(h, prop, RATIO, max_increase=51)
    settle(h)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.AWAITING_APPROVAL, AGGREGATE)
    assert [s[0] for s in stops(h)] == [RATIO.value, AGGREGATE.value]  # 每一次超額都有停下紀錄
    assert h.dsp.writes == []
    approve_it(h, prop, AGGREGATE, max_increase=51)
    settle(h)
    assert h.process().kind is Result.EXECUTED
    assert [u[3] for u in uses(h)] == [RATIO.value, AGGREGATE.value]


# ---- [S371] ----
@pytest.mark.parametrize(("amounts", "released"), [((1000, 10), 0), ((10, 1000), 1)])
def test_the_latest_approval_wins_even_when_it_is_smaller(h, amounts, released):
    prop = waiting(h, RATIO)
    same_second = int(h.clock().timestamp())  # 簽發時間相同:以寫進核可表的順序為準
    for amount in amounts:
        approve_it(h, prop, RATIO, max_increase=amount, issued_at=same_second)

    assert settle(h) == released


# ---- [S372] ----
def test_two_workers_release_an_awaiting_proposal_once(h):
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51)
    with h.store.transaction() as tx:
        seen = h.store.awaiting(tx, h.clock())  # 慢的那一方先讀到清單
    tenants = load_tenants(h.config)

    assert h.executor("fast").process_awaiting() == 1
    assert h.executor("slow")._settle_awaiting(seen[0], tenants) is False  # 已不是待核可

    assert row(h) == ("pending", None, None, 0)
    assert h.process().kind is Result.EXECUTED
    assert h.executor("slow")._settle_awaiting(seen[0], tenants) is False  # 處理中也碰不到
    assert len(h.dsp.writes) == 1


# ---- [S373] ----
def test_a_newer_revision_supersedes_an_awaiting_one(h):
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51)  # 就算有有效核可
    submit(h, 120, revision=2)

    assert settle(h) == 1

    assert row(h)[:3] == ("superseded", None, None)
    assert h.process().kind is Result.EXECUTED
    assert [w[0].revision for w in h.dsp.writes] == [2]  # 舊修訂沒插隊


# ---- [S374] ----
@pytest.mark.parametrize(("config", "code"), [
    ({"campaigns": ("c2",)}, BlockCode.CAMPAIGN_NOT_ALLOWED),
    ({"max_budget": 120}, BlockCode.OVER_BUDGET_CAP),
])
def test_hard_rules_win_over_approvable_ones(h, config, code):
    submit(h, 151)
    write_config(h.config, **config)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, code)
    assert row(h)[1] == "blocked" and stops(h) == []


# ---- [S375] ----
def test_an_approval_is_void_across_policy_or_tenant_changes(h):
    old_policy = h.submit(requested_change={"new_budget": 151}, policy_version="v1")
    assert h.process().kind is Result.AWAITING_APPROVAL
    approve_it(h, old_policy, RATIO)
    assert settle(h) == 0  # 提案的政策版本不是目前那一版

    moved = waiting(h, RATIO, task_id="t2", campaign_id="c2")
    approve_it(h, moved, RATIO)
    spec = {"campaigns": ["c1", "c2", "c3"], "max_budget": 1000,
            "aggregate_limit": LOOSE_AGGREGATE_LIMIT}
    h.config.write_text(json.dumps({"tenants": {"t-other": spec}}), encoding="utf-8")
    assert settle(h) == 0  # 改掛到設定一模一樣的另一個租戶
    write_config(h.config)
    assert settle(h) == 1  # 對照:換回原租戶就算數


# ---- [S376] ----
def test_the_capability_never_outlives_the_approval_it_used(h):
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51, expires_in=30)
    approval_expires = int(h.clock().timestamp()) + 30
    settle(h)

    assert h.process().kind is Result.EXECUTED

    token = h.dsp.writes[0][2]
    assert decode(token, TEST_KEY)["exp"] == approval_expires  # 原本的有效期是 120 秒
    assert verified_claims(lambda: token, TEST_KEY, lambda: approval_expires - 1)
    with pytest.raises(CapabilityExpired):  # 開始一筆之後才過期:DSP 以憑證過期拒收
        verified_claims(lambda: token, TEST_KEY, lambda: approval_expires)


# ---- [S377] ----
def _unknown_after_approval(h, stage, expires_in):
    prop = waiting(h, stage)
    approve_it(h, prop, stage, max_increase=51, expires_in=expires_in)
    settle(h)
    h.dsp.answers.append(WriteAnswer(None))  # 送出後結果不明;假 DSP 沒套用
    assert h.process().kind is Result.EXECUTED
    return prop


def _states(h):
    return [a[2] for a in h.attempts()]


@pytest.mark.parametrize("stage", [RATIO, AGGREGATE])
def test_a_resend_rechecks_the_ratio_and_the_approval_it_used(h, stage):
    _unknown_after_approval(h, stage, expires_in=60)
    h.clock.advance(seconds=61)  # 用過的核可過期

    h.executor().reconcile_all()

    assert _states(h)[-1] == AttemptState.FAILED.value  # 先作廢再判失敗
    assert len(h.dsp.writes) == 1 and len(h.dsp.voids) == 1  # 不重送


def test_a_resend_with_a_still_valid_approval_is_capped_at_its_expiry(h):
    _unknown_after_approval(h, RATIO, expires_in=100)
    approval_expires = int(h.clock().timestamp()) + 100
    h.clock.advance(seconds=5)

    h.executor().reconcile_all()

    assert len(h.dsp.writes) == 2  # 照常同鍵重送
    assert decode(h.dsp.writes[1][2], TEST_KEY)["exp"] == approval_expires


def test_a_resend_needs_the_latest_ratio_approval_to_hold(h):
    prop = _unknown_after_approval(h, RATIO, expires_in=600)
    approve_it(h, prop, RATIO, max_increase=10)  # 更新的那張金額不夠:比例超過又沒有有效核可

    h.executor().reconcile_all()

    assert _states(h)[-1] == AttemptState.FAILED.value
    assert len(h.dsp.writes) == 1


def test_a_resend_after_capability_expiry_rechecks_the_approval(h):
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51, expires_in=10)
    settle(h)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    h.dsp.on_write = lambda *_a: h.clock.advance(seconds=11)  # DSP 回憑證過期時核可也過期了

    h.process()

    assert _states(h)[-1] == AttemptState.FAILED.value
    assert len(h.dsp.writes) == 1  # 不重簽重送


# ---- [S378] ----
def test_the_grant_carries_one_tenant_snapshot(h, monkeypatch):
    reads = []
    real = capability_signer.load_tenants
    monkeypatch.setattr(capability_signer, "load_tenants",
                        lambda path: reads.append(path) or real(path))
    limit(h, 77)
    prop = submit(h, 150)

    granted = h.signer.grant(prop, "k", h.config, int(h.clock().timestamp()))

    assert granted.tenant == Tenant(TENANT, frozenset({"c1", "c2", "c3"}), 1000, 77)
    assert len(reads) == 1  # 同一次讀檔
    assert h.process().kind is Result.EXECUTED
    assert first_row(h, prop) == [(TENANT, 50)]
    submit(h, 150, task_id="t2", campaign_id="c2")
    assert h.process().block_code is AGGREGATE  # 50 + 50 > 77:門檻取自同一個租戶物件
    assert stops(h)[-1][2:] == (TENANT, 50, 50, 77)


# ---- [S379] ----
def test_admin_and_executor_agree_on_the_scope_fingerprint(h, monkeypatch):
    waiting(h, RATIO)
    env = {APPROVAL_KEY_ENV: TEST_APPROVAL_KEY.decode()}

    def tool_fingerprint():
        assert approve.run(_tool_args(h), environ=env, clock=h.clock) == 0
        token = h.query("SELECT token FROM approvals ORDER BY seq DESC LIMIT 1")[0][0]
        return approval.read(token, TEST_APPROVAL_KEY).fingerprint

    first = tool_fingerprint()
    assert first == approval.scope_fingerprint(tenant_of(h))
    monkeypatch.setattr(guardrails, "MIN_INCREASE_STEP", 2)  # 比例常數改了:兩邊一起變
    changed = approval.scope_fingerprint(tenant_of(h))
    assert changed != first and tool_fingerprint() == changed
    monkeypatch.undo()
    monkeypatch.setattr(approval, "POLICY_VERSION", "next-policy")  # 政策版本改了也一起變
    assert approval.scope_fingerprint(tenant_of(h)) not in (first, changed)
    monkeypatch.undo()
    write_config(h.config, max_budget=999)  # 租戶任一欄改了:兩邊一起變
    assert approval.scope_fingerprint(tenant_of(h)) not in (first, changed)
    assert tool_fingerprint() == approval.scope_fingerprint(tenant_of(h))
    assert settle(h) == 1  # 執行迴圈認得管理工具簽的最新那張


# ---- 啟動程式:每輪先對帳、再處理待核可、再處理一筆;核可金鑰經共用模組讀 ----
@pytest.mark.parametrize(("with_key", "writes"), [(True, 1), (False, 0)])
def test_the_runner_releases_an_approved_proposal_in_the_same_round(h, with_key, writes):
    from rtb.executor import runner

    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51)
    env = {KEY_ENV: TEST_KEY.decode()}
    if with_key:
        env[APPROVAL_KEY_ENV] = TEST_APPROVAL_KEY.decode()
    args = ["--db", str(h.db), "--dsp-url", "http://127.0.0.1:9", "--tenant-config",
            str(h.config), "--interval-seconds", "0.01"]

    assert runner.run(args, environ=env, clock=h.clock, dsp=h.dsp, out=io.StringIO(),
                      sleep=lambda _s: None, max_rounds=1, owner="executor") == 0

    assert len(h.dsp.writes) == writes  # 沒有核可金鑰:一張核可都不算數,照樣啟動


# ---- 防線:停下紀錄不見就不放回;只有兩種關卡能進待核可;兩次簽發之間租戶設定改了 ----
def test_an_awaiting_proposal_without_its_stop_record_is_not_released(h):
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51)
    with h.store.transaction() as tx:  # 測試模擬:停下紀錄不見了,不知道要核的金額
        tx.conn.execute("DELETE FROM write_stops")

    assert settle(h) == 0


def test_only_the_two_approvable_stages_can_wait(h):
    submit(h)
    with h.store.transaction() as tx:
        delivery = h.store.receive(tx, h.clock(), "w")
        with pytest.raises(ValueError):
            h.store.await_approval(tx, delivery.receipt, h.clock(), BlockCode.VERSION_CHANGED)


def test_a_tenant_change_between_the_two_signings_starts_over(h, monkeypatch):
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51)
    settle(h)
    real = h.signer.grant

    def changing(*args, **kwargs):
        if args[4] is not None:  # 封頂重簽之前,有人改了租戶設定(照樣合法)
            write_config(h.config, max_budget=999)
        return real(*args, **kwargs)

    monkeypatch.setattr(h.signer, "grant", changing)

    assert h.process().kind is Result.DEFERRED  # 核可是照舊設定驗的:放掉租約,下一輪重來
    assert h.attempts() == [] and h.dsp.writes == []
    assert row(h)[1] == "in_progress"


# ---- 代碼審第 1 輪折入 ----
def test_an_approval_signed_after_the_lookup_is_honoured_before_writing(h, monkeypatch):
    """查好核可之後、開始一筆之前,有人又簽了一張更新(更小)的:以最新那張為準(外家席)。"""
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=1000)
    settle(h)
    real = h.signer.grant

    def meanwhile(*args, **kwargs):
        if args[4] is not None:  # 封頂重簽的時候,管理員補簽一張金額不夠的
            approve_it(h, prop, RATIO, max_increase=10)
        return real(*args, **kwargs)

    monkeypatch.setattr(h.signer, "grant", meanwhile)

    assert h.process().kind is Result.DEFERRED
    assert h.attempts() == [] and h.dsp.writes == [] and uses(h) == []
    monkeypatch.undo()
    assert h.process().block_code is RATIO  # 下一輪照最新那張重判:金額不夠,回待核可
    assert row(h)[1] == "awaiting_approval"


def test_a_resend_drops_an_approval_whose_tenant_scope_changed(h, monkeypatch):
    """重送的兩次簽發之間租戶設定改了:核可照舊設定驗的,不算數、不重送(外家席)。"""
    _unknown_after_approval(h, RATIO, expires_in=100)
    real = h.signer.grant

    def changing(*args, **kwargs):
        if args[4] is not None:
            write_config(h.config, max_budget=999)
        return real(*args, **kwargs)

    monkeypatch.setattr(h.signer, "grant", changing)

    h.executor().reconcile_all()

    assert _states(h)[-1] == AttemptState.FAILED.value
    assert len(h.dsp.writes) == 1


def test_an_unneeded_aggregate_approval_does_not_shorten_the_capability(h):
    """額度在核可之後釋放,這一筆其實不必靠核可:憑證照原本的有效期、也不記使用(相容席)。"""
    limit(h, 60)
    other = submit(h, 150, task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(422, "idempotency_conflict"))  # 轉人工:佔住 50 的額度
    assert h.process().kind is Result.EXECUTED
    prop = submit(h, 150)  # 50 + 50 > 60
    assert h.process().block_code is AGGREGATE
    approve_it(h, prop, AGGREGATE, max_increase=50, expires_in=30)
    settle(h)
    with h.store.transaction() as tx:  # 人工把佔額度那筆判成失敗:額度還回去
        key = operation_key(other)
        seq = attempt_store.latest(tx, key).seq
        attempt_store.resolve(tx, key, seq, AttemptState.FAILED, "operator decided", h.clock())

    assert h.process().kind is Result.EXECUTED

    assert decode(h.dsp.writes[-1][2], TEST_KEY)["exp"] == int(h.clock().timestamp()) + 120
    assert uses(h) == []


def test_a_hard_rule_at_the_capping_signature_still_blocks(h, monkeypatch):
    """封頂重簽時廣告已改掛到別的租戶:照硬規則擋下,不拿第一次的憑證去寫(假綠席)。"""
    prop = waiting(h, RATIO)
    approve_it(h, prop, RATIO, max_increase=51)
    settle(h)
    real = h.signer.grant

    def moved(*args, **kwargs):
        if args[4] is not None:
            write_config(h.config, campaigns=("c2", "c3"))
        return real(*args, **kwargs)

    monkeypatch.setattr(h.signer, "grant", moved)

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, BlockCode.CAMPAIGN_NOT_ALLOWED)
    assert h.attempts() == [] and h.dsp.writes == []


def test_a_resend_needs_the_approval_it_used_even_after_a_newer_one(h):
    """重送要當初用過的那張全程算數:它過期了,就算之後補簽了足額的新核可也不重送(保守,
    核可使用紀錄記的那張才是放行依據;假綠席要求把這個情境跟金額不夠分開驗)。"""
    prop = _unknown_after_approval(h, RATIO, expires_in=10)
    h.clock.advance(seconds=11)
    approve_it(h, prop, RATIO, max_increase=100)

    h.executor().reconcile_all()

    assert _states(h)[-1] == AttemptState.FAILED.value
    assert len(h.dsp.writes) == 1


def test_running_the_approval_tool_twice_in_the_same_second_is_harmless(h):
    waiting(h, RATIO)
    env = {APPROVAL_KEY_ENV: TEST_APPROVAL_KEY.decode()}

    assert approve.run(_tool_args(h), environ=env, clock=h.clock) == 0
    assert approve.run(_tool_args(h), environ=env, clock=h.clock) == 0  # 簽出一模一樣的那張

    assert len(h.query("SELECT token FROM approvals")) == 2  # 每次下達都記一列,不報錯
    assert settle(h) == 1


# ---- 代碼審第 2 輪折入 ----
def test_reissuing_an_earlier_approval_makes_it_the_latest_again(h):
    """同一秒依序下達 A(上限 10)、B(上限 1000)、再下達 A:最新是 A(外家席)。"""
    waiting(h, RATIO)
    env = {APPROVAL_KEY_ENV: TEST_APPROVAL_KEY.decode()}
    for amount in ("10", "1000", "10"):
        assert approve.run(_tool_args(h, **{"max-increase": amount}), environ=env,
                           clock=h.clock) == 0

    assert settle(h) == 0  # 最後下達的是上限 10 的那張:加 51 不夠


def test_the_approval_tool_reports_a_busy_database_while_finding_the_proposal(h, monkeypatch):
    waiting(h, RATIO)

    def busy(*_args, **_kwargs):
        raise InboxBusy("locked")

    monkeypatch.setattr(InboxStore, "find_proposal", busy)
    env = {APPROVAL_KEY_ENV: TEST_APPROVAL_KEY.decode()}

    assert approve.run(_tool_args(h), environ=env, clock=h.clock) == approve.EXIT_BUSY


def test_a_resend_honours_an_approval_signed_while_resigning(h, monkeypatch):
    """重送重簽期間管理員補簽一張更新(更小)的核可:以最新為準,不重送(外家席)。"""
    prop = _unknown_after_approval(h, RATIO, expires_in=600)
    real = h.signer.grant

    def meanwhile(*args, **kwargs):
        if args[4] is not None:
            approve_it(h, prop, RATIO, max_increase=10)
        return real(*args, **kwargs)

    monkeypatch.setattr(h.signer, "grant", meanwhile)

    h.executor().reconcile_all()

    assert _states(h)[-1] == AttemptState.FAILED.value
    assert len(h.dsp.writes) == 1


def test_a_capability_shortened_by_an_unused_approval_recovers_with_a_full_lifetime(
        h, monkeypatch):
    """預判要用總曝險核可、開始一筆時額度剛好被別人釋放:憑證已按核可到期壓短。這個窄窗不改
    (要在寫入鎖裡讀設定檔重簽);後果有界:DSP 回憑證過期時,照既有做法重簽重送,這次沒有
    用過的核可,憑證拿回正常效期(前輪驗收席)。"""
    limit(h, 60)
    other = submit(h, 150, task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(422, "idempotency_conflict"))  # 轉人工:佔住 50
    h.process()
    prop = submit(h, 150)
    assert h.process().block_code is AGGREGATE
    approve_it(h, prop, AGGREGATE, max_increase=50, expires_in=30)
    settle(h)
    real_used = attempt_store.aggregate_used
    released = []

    def release_meanwhile(tx, tenant, now):
        value = real_used(tx, tenant, now)
        if not released:  # 預判讀完之後,另一個工作者把佔額度那筆判成失敗
            released.append(True)
            key = operation_key(other)
            seq = attempt_store.latest(tx, key).seq
            attempt_store.resolve(tx, key, seq, AttemptState.FAILED, "operator decided", now)
        return value

    monkeypatch.setattr(attempt_store, "aggregate_used", release_meanwhile)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))  # 壓短的憑證真的過期了
    issued = int(h.clock().timestamp())

    assert h.process().kind is Result.EXECUTED

    assert decode(h.dsp.writes[-2][2], TEST_KEY)["exp"] == issued + 30  # 窄窗:被壓短
    assert decode(h.dsp.writes[-1][2], TEST_KEY)["exp"] == issued + 120  # 重送拿回正常效期
    final = h.query("SELECT state FROM attempts WHERE key = ? ORDER BY seq DESC LIMIT 1",
                    (operation_key(prop),))
    assert final == [(AttemptState.VERIFIED.value,)] and uses(h) == []


# ---- 代碼審第 3 輪折入 ----
def test_the_approval_tool_signs_only_the_stage_the_proposal_waits_at(h):
    """同時超過比例與總曝險:停在比例時不能預先簽總曝險那一關,放行後照樣第二次停下(外家席)。"""
    limit(h, 10)
    submit(h, 151)
    assert h.process().block_code is RATIO
    env = {APPROVAL_KEY_ENV: TEST_APPROVAL_KEY.decode()}

    assert approve.run(_tool_args(h, stage=AGGREGATE.value), environ=env,
                       clock=h.clock) == approve.EXIT_REFUSED
    assert approve.run(_tool_args(h), environ=env, clock=h.clock) == 0
    settle(h)
    assert h.process().block_code is AGGREGATE  # 第二次停下,另寫一列停下紀錄
    assert [s[0] for s in stops(h)] == [RATIO.value, AGGREGATE.value]
    assert h.dsp.writes == []
    h.executor().process_one()  # 已是待核可:取件撿不到
    assert approve.run(_tool_args(h, stage=AGGREGATE.value), environ=env,
                       clock=h.clock) == 0
    assert approve.run(_tool_args(h), environ=env,
                       clock=h.clock) == approve.EXIT_REFUSED  # 比例那一關已經過了


def test_a_resend_checks_the_latest_approval_in_the_same_transaction_as_going_in_flight(
        h, monkeypatch):
    """重送的最後核對跟轉嘗試中同一個交易:核對之後、轉嘗試中之前補簽的更新核可也擋得到。"""
    prop = _unknown_after_approval(h, RATIO, expires_in=600)
    real_extend = InboxStore.extend
    sneaked = []

    def extend(store, tx, receipt, now):  # 轉嘗試中那個交易一開頭:管理員剛好補簽了一張更小的
        if not sneaked:
            sneaked.append(True)
            issued = int(h.clock().timestamp())
            token = approval.issue(TEST_APPROVAL_KEY, prop, RATIO, tenant_of(h), approver="ops",
                                   max_increase=10, issued_at=issued, expires_at=issued + 600)
            tx.conn.execute(  # 同一條連線、同一個交易裡寫(模擬別的連線剛提交的那一列)
                "INSERT INTO approvals (approval_id, task_id, revision, content_hash, stage, "
                "token, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (approval.approval_id(token), prop.task_id, prop.revision, content_hash(prop),
                 RATIO.value, token, h.clock().isoformat()))
        return real_extend(store, tx, receipt, now)

    monkeypatch.setattr(InboxStore, "extend", extend)

    h.executor().reconcile_all()

    assert sneaked
    assert _states(h)[-1] == AttemptState.FAILED.value
    assert len(h.dsp.writes) == 1


def test_an_awaiting_revision_whose_operation_already_ran_is_released(h):
    """同一把鍵的兩份修訂都取到收據:第二份先停在待核可,第一份已開嘗試並完成;處理待核可把
    第二份放回,取件照那把鍵的狀態確認成已交給執行(外家席,事故 F3)。"""
    first = submit(h, 151)
    with h.store.transaction() as tx:  # 兩份都先取件(模擬並行的兩個工作者)
        one = h.store.receive(tx, h.clock(), "w1")
    second = submit(h, 151, revision=2, decision_expires_at="2026-09-22T12:40:00+00:00")
    assert operation_key(first) == operation_key(second)  # 只差修訂與到期:同一把鍵
    with h.store.transaction() as tx:
        two = h.store.receive(tx, h.clock(), "w2")
    assert (one.message.revision, two.message.revision) == (1, 2)
    approve_it(h, first, RATIO, max_increase=51)
    seen = []

    def meanwhile(*_args):  # 修訂 1 正在呼叫 DSP(嘗試中):另一個工作者處理修訂 2
        if not seen:
            seen.append(h.executor("w2")._process(two.message, two.receipt))

    h.dsp.on_write = meanwhile
    assert h.executor("w1")._process(one.message, one.receipt).kind is Result.EXECUTED
    assert [p.kind for p in seen] == [Result.AWAITING_APPROVAL]  # 修訂 2 沒有自己的核可

    assert settle(h) == 1
    assert h.process().kind is Result.IDLE  # 取件讀到既有已驗證的鍵:直接確認
    assert row(h, revision=2)[1] == "handed_off"
    assert len(h.dsp.writes) == 1


def test_only_actionable_awaiting_proposals_are_read_each_round(h):
    """沒有核可、沒到期、沒有新修訂、沒開過嘗試的待核可,每輪不讀(資安席:堆積不拖慢每一輪)。"""
    for i in range(1, 4):
        waiting(h, RATIO, task_id=f"t{i}", campaign_id=f"c{i}")
    approve_it(h, proposal(task_id="t2", campaign_id="c2", policy_version=POLICY_VERSION,
                           requested_change={"new_budget": 151}), RATIO, max_increase=51)
    approve_it(h, proposal(task_id="t3", campaign_id="c3", policy_version=POLICY_VERSION,
                           requested_change={"new_budget": 151}), AGGREGATE)  # 別關的不算

    with h.store.transaction() as tx:
        seen = [item.message.task_id for item in h.store.awaiting(tx, h.clock())]
        later = [item.message.task_id
                 for item in h.store.awaiting(tx, h.clock() + timedelta(hours=1))]

    assert seen == ["t2"]
    assert later == ["t1", "t2", "t3"]  # 到期的每一份都讀得到
