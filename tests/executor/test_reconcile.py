"""執行迴圈的對帳(Phase 3 增量 4):結果不明與已提交待驗證的後續處理,DSP 用替身。

判定表:用冪等鍵查操作紀錄 → 查到就核對完整內容再驗證;查不到就重讀廣告、重跑執行前檢查 →
通過就同鍵重送;業務上不過就先請 DSP 作廢這把鍵,作廢成功才標失敗(沒發生)。
端到端(真的 DSP 行程加故障注入)見 test_execution_e2e.py;每輪的順序與休息見 test_runner.py。
"""

import os

import pytest

from rtb.domain.attempt import AttemptState, OutcomeCode, operation_key
from rtb.executor import attempt_store
from rtb.executor.execution import (
    VOID_TABLE,
    CampaignView,
    ExecutorHalted,
    Result,
    VoidAnswer,
    WriteAnswer,
)
from tests.executor.fakes import Harness, write_config

A = AttemptState
C = OutcomeCode


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def key_of(prop):
    return operation_key(prop)


def states(h, prop):
    return [(row[2], row[3]) for row in h.attempts() if row[0] == key_of(prop)]


def reconcile(h):
    return h.executor().reconcile_all()


def unknown_attempt(h, *, committed=False, **overrides):
    """開一筆嘗試、寫入沒拿到回應,停在結果不明;committed 表示 DSP 其實已經提交。"""
    prop = h.submit(**overrides)
    h.dsp.answers.append(WriteAnswer(None))
    if committed:
        h.dsp.on_write = lambda p, k, _t: h.dsp.apply(p, k)
    h.process()
    h.dsp.on_write = None
    assert states(h, prop) == [("in_flight", None), ("unknown", None)]  # 前置
    return prop


def timeouts(h, prop):
    with h.store.transaction() as tx:
        return attempt_store.latest(tx, key_of(prop)).verification_timeouts


# ---- [S70] ----
def test_reconcile_finds_the_operation_by_key_and_verifies_it(h):
    prop = unknown_attempt(h, committed=True)

    assert reconcile(h) is False

    assert states(h, prop)[2:] == [("committed_unverified", None), ("verified", None)]
    assert [r[5] for r in h.attempts()][2] == 4  # 寫入後版本填操作紀錄的值
    assert len(h.dsp.writes) == 1 and h.dsp.voids == []  # 查到就不重送、不作廢


# ---- [S71] ----
@pytest.mark.parametrize("tamper", [{"campaign_id": "c9"}, {"action": "pause_campaign"},
                                    {"new_budget": 999}, {"expected_version": 2},
                                    {"expected_version": None}])  # 空值:DSP 補欄位前的舊列
def test_reconcile_escalates_a_mismatched_operation_record(h, tamper):
    prop = unknown_attempt(h, committed=True)
    h.dsp.operations[key_of(prop)] = h.dsp.record_for(prop, 4, **tamper)

    reconcile(h)

    assert states(h, prop)[-1] == ("escalated", "idempotency_conflict")


# ---- [S72] ----
def test_reconcile_voids_the_key_before_marking_not_happened(h):
    prop = unknown_attempt(h)
    h.dsp.campaigns["c1"] = CampaignView(100, "active", 4)  # 別人改了:執行前檢查不過

    reconcile(h)

    assert h.dsp.voids == [key_of(prop)]
    assert states(h, prop)[-1] == ("failed", "not_happened")
    assert len(h.dsp.writes) == 1  # 沒有重送


# ---- [S73] ----
def test_reconcile_treats_a_commit_found_while_voiding_as_found(h):
    prop = unknown_attempt(h)
    # 查鍵時還查不到;讀版本之前舊請求剛好提交(版本前進),作廢時 DSP 回已提交
    h.dsp.campaigns["c1"] = CampaignView(prop.requested_change["new_budget"], "active", 4)
    h.dsp.void_answers.append(VoidAnswer(200, None, "committed", h.dsp.record_for(prop, 4)))

    reconcile(h)

    assert states(h, prop)[2:] == [("committed_unverified", None), ("verified", None)]


# ---- [S74] ----
def test_reconcile_resends_with_the_same_key(h):
    prop = unknown_attempt(h)

    reconcile(h)

    assert [w[1] for w in h.dsp.writes] == [key_of(prop), key_of(prop)]  # 同一把鍵,不產生新鍵
    assert states(h, prop)[2:] == [("in_flight", None), ("committed_unverified", None),
                                   ("verified", None)]
    assert [r[4] for r in h.attempts()][2] == 2  # 送出次數加 1
    assert h.dsp.voids == []


# ---- [S75] ----
def test_reconcile_escalates_at_the_send_limit(h):
    prop = unknown_attempt(h)
    for _ in range(attempt_store.MAX_SENDS - 1):  # 每次重送都沒拿到回應
        h.dsp.answers.append(WriteAnswer(None))
        reconcile(h)
    assert [r[4] for r in h.attempts()][-1] == attempt_store.MAX_SENDS  # 前置:已達上限

    reconcile(h)

    assert states(h, prop)[-1] == ("escalated", "send_limit_reached")
    assert len(h.dsp.writes) == attempt_store.MAX_SENDS


# ---- [S76] ----
def test_reconcile_counts_failed_lookups_and_escalates_at_the_limit(h):
    prop = unknown_attempt(h)
    h.dsp.lookup_failures = 100

    assert reconcile(h) is True  # 這輪有 DSP 呼叫失敗
    assert states(h, prop)[-1] == ("unknown", None) and timeouts(h, prop) == 1

    for _ in range(attempt_store.MAX_VERIFICATION_TIMEOUTS):
        reconcile(h)
    assert states(h, prop)[-1] == ("escalated", "verification_timeouts_exhausted")
    assert len(h.dsp.writes) == 1


# ---- [S77] ----
def test_reconcile_leaves_escalated_attempts_alone(h):
    prop = h.submit()
    h.dsp.answers.append(WriteAnswer(422, "idempotency_conflict"))
    h.process()
    assert states(h, prop)[-1] == ("escalated", "idempotency_conflict")  # 前置
    before = h.attempts()

    reconcile(h)

    assert h.attempts() == before
    assert h.dsp.lookups == [] and h.dsp.voids == []


# ---- [S83] ----
@pytest.mark.parametrize("passes_precheck", [True, False], ids=["resign", "sign_void"])
def test_reconcile_stops_on_a_broken_tenant_configuration(h, passes_precheck):
    prop = unknown_attempt(h)
    if not passes_precheck:
        h.dsp.campaigns["c1"] = CampaignView(100, "active", 4)
    os.chmod(h.config, 0o664)  # 群組可寫:不安全

    with pytest.raises(ExecutorHalted):
        reconcile(h)

    assert states(h, prop)[-1] == ("unknown", None)
    assert h.dsp.voids == [] and len(h.dsp.writes) == 1


# ---- [S84] ----
def test_reconcile_escalates_when_it_cannot_void(h):
    prop = unknown_attempt(h)
    write_config(h.config, campaigns=("c2", "c3"))  # 廣告已不屬於允許的租戶

    reconcile(h)

    assert states(h, prop)[-1] == ("escalated", "cannot_prove_not_happened")
    assert h.dsp.voids == [] and len(h.dsp.writes) == 1


# ---- [S85] ----
def test_reconcile_counts_failed_reads_and_voids_as_timeouts(h):
    prop = unknown_attempt(h)
    h.dsp.read_failures = 1

    assert reconcile(h) is True
    assert states(h, prop)[-1] == ("unknown", None) and timeouts(h, prop) == 1

    h.dsp.campaigns["c1"] = CampaignView(100, "active", 4)
    h.dsp.void_answers.append(VoidAnswer(503, "store_busy"))
    assert reconcile(h) is True
    assert states(h, prop)[-1] == ("unknown", None) and timeouts(h, prop) == 2
    assert h.dsp.voids == [key_of(prop)]


# ---- [S90] ----
# 預期值照計劃的作廢回應對照表手抄一份,不從程式的對照表產生
VOID_DOCUMENTED = [
    ("voided", VoidAnswer(200, None, "voided"), ("failed", "not_happened"), False),
    ("committed", "committed", ("committed_unverified", None), False),
    ("capability_expired", VoidAnswer(401, "capability_expired"), ("unknown", None), False),
    ("capability_invalid", VoidAnswer(401, "capability_invalid"),
     ("escalated", "capability_rejected"), False),
    ("capability_scope_mismatch", VoidAnswer(403, "capability_scope_mismatch"),
     ("escalated", "capability_rejected"), False),
    ("capability_not_configured", VoidAnswer(503, "capability_not_configured"),
     ("escalated", "capability_rejected"), False),
    ("missing_idempotency_key", VoidAnswer(400, "missing_idempotency_key"),
     ("escalated", "local_request_error"), True),
    ("body_too_large", VoidAnswer(413, "body_too_large"),
     ("escalated", "local_request_error"), True),
    ("store_busy", VoidAnswer(503, "store_busy"), ("unknown", None), False),
    ("server_error", VoidAnswer(500, None), ("unknown", None), False),
    ("no_response", VoidAnswer(None), ("unknown", None), False),
    ("redirect", VoidAnswer(302, None), ("unknown", None), False),
    ("ok_without_state", VoidAnswer(200, None, None), ("unknown", None), False),
    ("committed_without_record", VoidAnswer(200, None, "committed", None), ("unknown", None),
     False),
]


@pytest.mark.parametrize(("answer", "expected", "halt"), [d[1:] for d in VOID_DOCUMENTED],
                         ids=[d[0] for d in VOID_DOCUMENTED])
def test_every_void_answer_maps_to_the_documented_state_and_code(h, answer, expected, halt):
    prop = unknown_attempt(h)
    h.dsp.campaigns["c1"] = CampaignView(100, "active", 4)  # 執行前檢查不過:要作廢
    if answer == "committed":
        answer = VoidAnswer(200, None, "committed", h.dsp.record_for(prop, 4))
    h.dsp.void_answers.append(answer)

    if halt:
        with pytest.raises(ExecutorHalted):
            reconcile(h)
    else:
        reconcile(h)

    assert states(h, prop)[2] == expected  # 結果不明之後的第一列
    assert len(h.dsp.writes) == 1  # 作廢這條路從不重送


def test_the_documented_void_answers_reach_every_row_of_the_void_table(h):
    """程式多加一列卻沒補預期值,這裡會紅。"""
    prop_record = h.dsp.record_for(h.submit(), 4)
    answers = [VoidAnswer(200, None, "committed", prop_record) if d[1] == "committed" else d[1]
               for d in VOID_DOCUMENTED]
    reached = {next(r.name for r in VOID_TABLE if r.matches(a)) for a in answers}
    assert reached == {r.name for r in VOID_TABLE}


# ---- [S92] ----
def test_reconcile_fails_a_missing_campaign_without_voiding(h):
    prop = unknown_attempt(h)
    del h.dsp.campaigns["c1"]

    reconcile(h)

    assert states(h, prop)[-1] == ("failed", "campaign_not_found")
    assert h.dsp.voids == []


# ---- [S93] ----
def test_a_voided_operation_blocks_the_same_key_from_a_new_revision(h):
    prop = unknown_attempt(h)
    h.clock.advance(hours=1)  # 提案過期:業務上不過,作廢後標失敗
    reconcile(h)
    assert states(h, prop)[-1] == ("failed", "not_happened")  # 前置
    assert h.dsp.voids == [key_of(prop)]

    again = h.submit(revision=2, decision_created_at="2026-09-22T13:00:00+00:00",
                     decision_expires_at="2026-09-22T13:30:00+00:00")
    assert key_of(again) == key_of(prop)  # 只換到期時間:同一把鍵

    assert h.process().kind is Result.BLOCKED
    assert h.proposals()[-1][3:] == ("blocked", "operation_previously_failed")
    assert len(h.dsp.writes) == 1


def test_an_expired_resend_after_earlier_sends_voids_before_failing(h):
    """實作時決定:憑證過期後業務上不過,這把鍵只送過一次(就是剛被拒的那次)才能直接標失敗;
    送過多次就可能有更早的請求還在路上,要先作廢。"""
    prop = unknown_attempt(h)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))  # 對帳重送:憑證過期

    def pause_after_the_resend(_c):
        if len(h.dsp.writes) == 2:
            h.dsp.campaigns["c1"] = CampaignView(100, "paused", 3)

    h.dsp.on_read = pause_after_the_resend
    reconcile(h)

    assert h.dsp.voids == [key_of(prop)]
    assert states(h, prop)[-1] == ("failed", "not_happened")


def test_one_failed_call_marks_the_whole_round_as_troubled(h):
    """一輪有多筆:只要其中一筆的 DSP 呼叫失敗,整輪就算有失敗(不被後面成功的蓋掉)。"""
    unknown_attempt(h)
    h.clock.advance(seconds=5)
    unknown_attempt(h, task_id="t2", campaign_id="c2")
    h.dsp.lookup_failures = 1  # 只有最舊那一筆的查詢失敗

    assert reconcile(h) is True


def test_a_resend_signs_the_stored_key_not_a_recomputed_one(h, monkeypatch):
    """算法升版後,同一份提案會算出另一把鍵;重送一律用嘗試紀錄存下的那把(憑證與標頭都是)。"""
    from rtb.capabilitykit import decode
    from rtb.executor import execution
    from tests.capability_samples import TEST_KEY

    prop = unknown_attempt(h)
    stored = key_of(prop)
    monkeypatch.setattr(execution, "operation_key", lambda _p: "k2-recomputed")

    reconcile(h)

    sent = [(w[1], decode(w[2], TEST_KEY)["idempotency_key"]) for w in h.dsp.writes]
    assert sent[-1] == (stored, stored)  # 標頭與憑證都用存下的鍵
