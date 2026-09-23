"""執行迴圈的「處理一筆」:執行一筆(Phase 3 增量 3)的流程合約,DSP 用替身。

端到端(真的 DSP 行程)見 test_execution_e2e.py;啟動程式見 test_runner.py。
"""

import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from rtb.capabilitykit import decode
from rtb.domain.attempt import AttemptState, OutcomeCode, operation_key
from rtb.executor import attempt_store
from rtb.executor.capability_signer import LIFETIME_SECONDS
from rtb.executor.execution import (
    RESPONSE_TABLE,
    CampaignView,
    ExecutorHalted,
    Result,
    WriteAnswer,
    react,
)
from rtb.executor.inbox_store import APPROVABLE, BlockCode
from tests.capability_samples import TEST_KEY
from tests.executor.fakes import Harness, proposal
from tests.executor.test_guardrails import HISTORICAL_CODES

A = AttemptState
C = OutcomeCode


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


SOON = "2026-09-22T12:05:30+00:00"  # 現在 12:05,30 秒後到期


def key_of(prop):
    return operation_key(prop)


def states(h, prop):
    return [(row[2], row[3]) for row in h.attempts() if row[0] == key_of(prop)]


def expiry_of(token):
    return datetime.fromtimestamp(decode(token, TEST_KEY)["exp"], UTC)


# ---- [S42] ----
def test_an_expired_proposal_is_marked_expired_without_touching_the_dsp(h):
    h.submit()
    h.clock.advance(minutes=30)  # 12:35,提案 12:30 到期

    result = h.process()

    assert result.kind is Result.EXPIRED
    assert h.proposals() == [("t1", 1, "expired", None, None)]
    assert h.attempts() == []
    assert h.dsp.reads == [] and h.dsp.writes == []


# ---- [S43] ----
def test_a_proposal_that_expires_while_the_dsp_is_read_is_not_executed(h):
    """讀 DSP 期間剛好跨過到期時間:讀完要再判一次,不能簽發、開嘗試或寫入。

    提案 30 秒後到期、讀取花 45 秒:跨過到期時間,但沒超過租約(60 秒),收據仍有效。"""
    prop = h.submit(decision_expires_at=SOON)
    h.dsp.on_read = lambda _c: h.clock.advance(seconds=45)  # 讀取回來時提案已過期
    assert prop.decision_expires_at > h.clock()  # 前置:開始處理時還沒過期

    assert h.process().kind is Result.EXPIRED
    assert h.attempts() == [] and h.dsp.writes == []
    assert h.proposals()[0][2] == "expired"


def test_an_expiry_during_the_read_wins_over_other_failed_checks(h):
    """讀 DSP 期間過期、DSP 同時回不在投放:照 S42 應標已過期,不是擋下(不在投放)。"""
    prop = h.submit(decision_expires_at=SOON)

    def pause_and_expire(_c):
        current = h.dsp.campaigns["c1"]
        h.dsp.campaigns["c1"] = CampaignView(current.budget, "paused", current.version)
        h.clock.advance(seconds=45)

    h.dsp.on_read = pause_and_expire
    assert prop.decision_expires_at > h.clock()  # 前置:開始處理時還沒過期

    assert h.process().kind is Result.EXPIRED
    assert h.proposals()[0][2:] == ("expired", None, None)


def test_a_proposal_that_expires_while_signing_is_not_executed(h):
    """簽發要讀租戶設定檔,也可能剛好跨過到期時間:開始一筆的交易裡要再判一次。"""
    prop = h.submit(decision_expires_at=SOON)
    real_grant = h.signer.grant

    def slow_grant(*args, **kwargs):
        grant = real_grant(*args, **kwargs)
        h.clock.advance(seconds=45)  # 簽完時提案已過期(還在租約內)
        return grant

    h.signer = type("SlowSigner", (), {"grant": staticmethod(slow_grant)})()
    assert prop.decision_expires_at > h.clock()  # 前置:開始處理時還沒過期

    assert h.process().kind is Result.EXPIRED
    assert h.attempts() == [] and h.dsp.writes == []
    assert h.proposals()[0][2] == "expired"


def _not_found(h):
    del h.dsp.campaigns["c1"]


def _paused(h):
    h.dsp.campaigns["c1"] = CampaignView(budget=100, status="paused", version=3)


def _version_changed(h):
    h.dsp.campaigns["c1"] = CampaignView(budget=120, status="active", version=4)


def _not_allowed(h):
    from tests.executor.fakes import write_config

    write_config(h.config, campaigns=("c2",))


def _over_cap(h):
    from tests.executor.fakes import write_config

    write_config(h.config, max_budget=149)  # 提案要改成 150


def _over_ratio(h):
    # 提案要改成 150;現況 99 時最多加 49
    h.dsp.campaigns["c1"] = CampaignView(budget=99, status="active", version=3)


def _previously_failed(h):
    """同一個邏輯操作先前已失敗(人工判失敗),DSP 版本沒動;換到期時間的新修訂進來。"""
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(403, "capability_scope_mismatch"))
    h.process()
    key = key_of(first)
    with h.store.transaction() as tx:
        seq = attempt_store.latest(tx, key).seq
        attempt_store.resolve(tx, key, seq, A.FAILED, "operator decided", h.clock())
    h.store.accept(proposal(revision=2, decision_expires_at="2026-09-22T12:40:00+00:00"),
                   h.clock)


def _aggregate_full(h):
    from tests.executor.fakes import write_config

    write_config(h.config, aggregate_limit=49)  # 提案從 100 改成 150,要佔 50


BLOCK_TRIGGERS = {
    BlockCode.CAMPAIGN_NOT_FOUND: _not_found,
    BlockCode.CAMPAIGN_NOT_ACTIVE: _paused,
    BlockCode.VERSION_CHANGED: _version_changed,
    BlockCode.CAMPAIGN_NOT_ALLOWED: _not_allowed,
    BlockCode.OVER_BUDGET_CAP: _over_cap,
    # 下面「等於整個列舉」的斷言之後由護欄表三份清單取代(Phase 6 增量 2)
    BlockCode.BUDGET_INCREASE_TOO_LARGE: _over_ratio,
    BlockCode.OPERATION_PREVIOUSLY_FAILED: _previously_failed,
    BlockCode.AGGREGATE_LIMIT_REACHED: _aggregate_full,  # Phase 6:開始一筆時擋,不是執行前檢查
}


# 歷史相容代碼(規則已拿掉、只為讀舊資料而留)沒有觸發路徑,不在這張表(Phase 6 增量 2)
@pytest.mark.parametrize("code", [c for c in BlockCode if c not in HISTORICAL_CODES])
def test_each_failed_precheck_blocks_the_proposal_without_a_write(h, code):
    # 「表的鍵等於整個列舉」由護欄表的三份清單斷言取代(Phase 6 增量 2 [S404]);這張表仍逐一觸發
    if code is not BlockCode.OPERATION_PREVIOUSLY_FAILED:
        h.submit()
    BLOCK_TRIGGERS[code](h)
    attempts_before, writes_before = h.attempts(), len(h.dsp.writes)

    result = h.process()

    if code is BlockCode.OPERATION_PREVIOUSLY_FAILED:  # 取件時讀到既有失敗:直接確認,不交出去
        assert result.kind is Result.IDLE
    elif code in APPROVABLE:  # 可核可的兩關先停在待核可,提案到期才確認成已擋下(Phase 6 增量 3)
        assert (result.kind, result.block_code) == (Result.AWAITING_APPROVAL, code)
        h.clock.advance(hours=1, seconds=1)
        assert h.executor().process_awaiting() == 1
    else:
        assert (result.kind, result.block_code) == (Result.BLOCKED, code)
    blocked = [p for p in h.proposals() if p[3] == "blocked"]
    assert blocked == [("t1", blocked[0][1], "pending", "blocked", code.value)]
    assert h.attempts() == attempts_before  # 不寫嘗試紀錄
    assert len(h.dsp.writes) == writes_before  # 不呼叫 DSP 寫入


# ---- [S44](由 Phase 4 增量 1 [S115] 取代:讀不到 DSP 改成放掉租約、計入投遞) ----
def test_an_unreachable_dsp_leaves_the_proposal_pending(h):
    h.submit()
    h.dsp.read_failures = 1

    result = h.process()

    assert result.kind is Result.DEFERRED
    assert h.proposals() == [("t1", 1, "pending", "in_progress", None)]  # 處理中、租約已放掉
    assert h.query("SELECT last_failure FROM proposals") == [("dsp_unavailable",)]
    assert h.attempts() == [] and h.dsp.writes == []
    assert h.process().kind is Result.EXECUTED  # 下一輪 DSP 恢復就照常處理


# ---- [S108](取代 Phase 3 的 S45) ----
def test_a_proposal_superseded_before_taking_is_never_executed(h):
    """取件之後提案已是處理中,新修訂不能再取代它(取代會丟掉正在跑的工作);開始一筆的交易
    核對收據,收據一旦失效(被接手、內容不同、已被取代)就不開始嘗試、不呼叫 DSP。"""
    h.submit()
    # 讀 DSP 的當下,分析行程送來修訂 2:修訂 1 已是處理中,不被取代
    h.dsp.on_read = lambda _c: h.store.accept(proposal(revision=2), h.clock)

    assert h.process().kind is Result.EXECUTED
    assert h.proposals() == [("t1", 1, "pending", "handed_off", None),
                             ("t1", 2, "pending", None, None)]

    # 收據失效:讀 DSP 的當下,另一個工作者接手了這則訊息(過期後原子接手,序號加 1)
    h2 = h.submit(task_id="t2", campaign_id="c2")

    def someone_took_over(_c):
        h.clock.advance(seconds=61)
        with h.store.transaction() as tx:
            message = h.store.in_progress_for(tx, "t2", key_of(h2))
            assert h.store.take_over(tx, message, h.clock(), "other") is not None

    h.dsp.on_read = someone_took_over
    writes = len(h.dsp.writes)

    assert h.process().kind is Result.LEASE_LOST
    assert key_of(h2) not in {row[0] for row in h.attempts()}  # 不開始嘗試
    assert len(h.dsp.writes) == writes  # 不呼叫 DSP


# ---- [S46] ----
def test_no_transaction_is_open_while_the_dsp_is_called(h):
    seen = []

    def no_lock_held(kind):
        other = sqlite3.connect(h.db, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")  # 有人握著寫入鎖就會立刻失敗
            other.execute("ROLLBACK")
        finally:
            other.close()
        seen.append((kind, h.store._conn.in_transaction))

    h.dsp.on_call = no_lock_held
    h.submit()
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))  # 連重讀、重送也一起驗

    h.process()

    kinds = [kind for kind, _ in seen]
    assert kinds.count("write") == 2 and kinds.count("read") >= 3
    assert all(open_ is False for _, open_ in seen)


# ---- [S47] ----
# 預期值照計劃的回應對照表手抄一份,不從程式的對照表產生:程式刪掉或改錯一列,這裡會紅
DOCUMENTED = [
    ("committed", WriteAnswer(200, None, 4), A.COMMITTED_UNVERIFIED, None, False),
    ("version_conflict", WriteAnswer(409, "version_conflict"), A.FAILED, C.VERSION_CONFLICT,
     False),
    ("operation_voided", WriteAnswer(409, "operation_voided"), A.FAILED, C.NOT_HAPPENED, False),
    ("idempotency_conflict", WriteAnswer(422, "idempotency_conflict"), A.ESCALATED,
     C.IDEMPOTENCY_CONFLICT, False),
    ("validation_rejected", WriteAnswer(422, "validation_rejected"), A.FAILED,
     C.VALIDATION_REJECTED, False),
    ("capability_expired", WriteAnswer(401, "capability_expired"), A.UNKNOWN, None, False),
    ("capability_invalid", WriteAnswer(401, "capability_invalid"), A.ESCALATED,
     C.CAPABILITY_REJECTED, False),
    ("capability_missing", WriteAnswer(401, "capability_missing"), A.ESCALATED,
     C.CAPABILITY_REJECTED, False),
    ("capability_scope_mismatch", WriteAnswer(403, "capability_scope_mismatch"), A.ESCALATED,
     C.CAPABILITY_REJECTED, False),
    ("capability_not_configured", WriteAnswer(503, "capability_not_configured"), A.ESCALATED,
     C.CAPABILITY_REJECTED, False),
    ("missing_idempotency_key", WriteAnswer(400, "missing_idempotency_key"), A.ESCALATED,
     C.LOCAL_REQUEST_ERROR, True),
    ("body_too_large", WriteAnswer(413, "body_too_large"), A.ESCALATED, C.LOCAL_REQUEST_ERROR,
     True),
    ("store_busy", WriteAnswer(503, "store_busy"), A.UNKNOWN, None, False),
    ("server_error", WriteAnswer(500, None), A.UNKNOWN, None, False),
    ("no_response", WriteAnswer(None), A.UNKNOWN, None, False),
    ("redirect", WriteAnswer(302, None), A.UNKNOWN, None, False),
    ("ok_without_version", WriteAnswer(200, None, None), A.UNKNOWN, None, False),
]


@pytest.mark.parametrize(("answer", "target", "code", "halt"), [d[1:] for d in DOCUMENTED],
                         ids=[d[0] for d in DOCUMENTED])
def test_every_dsp_answer_maps_to_the_documented_state_and_code(h, answer, target, code, halt):
    prop = h.submit()
    h.dsp.answers.append(answer)
    if target is A.COMMITTED_UNVERIFIED:  # 讓執行後驗證看得到同一個版本
        h.dsp.on_write = lambda p, k, _t: h.dsp.apply(p, k)

    if halt:
        with pytest.raises(ExecutorHalted):
            h.process()
    else:
        h.process()

    first_result = states(h, prop)[1]  # 第 1 列是嘗試中,第 2 列是這次回應的結果
    assert first_result == (target.value, None if code is None else code.value)


def test_the_documented_answers_reach_every_row_of_the_response_table():
    """程式多加一列卻沒補預期值,這裡會紅:每一列都要有上面手抄的例子落進去。"""
    reached = {next(r.name for r in RESPONSE_TABLE if r.matches(d[1])) for d in DOCUMENTED}
    assert reached == {r.name for r in RESPONSE_TABLE}


def test_the_response_table_ends_with_a_catch_all_and_every_status_has_a_row():
    for status in [None, *range(100, 600)]:
        assert react(WriteAnswer(status, "whatever")) is not None
    assert RESPONSE_TABLE[-1].name == "unlisted_status"


# ---- [S48] ----
def test_an_expired_capability_is_recorded_rechecked_and_resent_once(h):
    prop = h.submit()
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))

    result = h.process()

    assert result.kind is Result.EXECUTED
    assert states(h, prop) == [("in_flight", None), ("unknown", None), ("in_flight", None),
                               ("committed_unverified", None), ("verified", None)]
    rows = [r for r in h.attempts() if r[0] == key_of(prop)]
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5]  # 每一筆用前一筆回傳的序號,一筆接一筆
    assert rows[2][4] == 2  # 重送算一次送出
    assert len(h.dsp.writes) == 2 and h.dsp.reads.count("c1") == 3  # 重讀一次再重送

    # 業務上沒通過:重讀時版本已變 → 失敗(沒發生且不再送)
    other = h.submit(task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    h.dsp.on_write = lambda *_: h.dsp.campaigns.__setitem__(
        "c2", CampaignView(budget=100, status="active", version=9))
    h.process()
    assert states(h, other)[-1] == ("failed", "not_happened")
    assert len([w for w in h.dsp.writes if w[1] == key_of(other)]) == 1

    # 重簽後仍過期 → 轉人工(憑證被拒)
    third = h.submit(task_id="t3", campaign_id="c3")
    h.dsp.on_write = None
    h.dsp.answers += [WriteAnswer(401, "capability_expired"),
                      WriteAnswer(401, "capability_expired")]
    h.process()
    assert states(h, third)[-1] == ("escalated", "capability_rejected")


def test_a_resigned_capability_after_expiry_signs_the_stored_key(h, monkeypatch):
    """憑證過期後的重簽也只用嘗試紀錄存下的鍵:算法升版時從提案重算會簽出另一把鍵。"""
    from rtb.capabilitykit import decode
    from rtb.executor import execution

    prop = h.submit()
    stored = key_of(prop)
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    monkeypatch.setattr(execution, "operation_key", lambda _p: "k2-recomputed")

    h.process()

    resend = h.dsp.writes[-1]
    assert (resend[1], decode(resend[2], TEST_KEY)["idempotency_key"]) == (stored, stored)


def test_a_resigned_capability_uses_a_freshly_read_clock(h):
    """一整輪共用同一個時間值的話,重簽會簽出跟第一張一樣已過期的憑證。"""
    h.submit()
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    h.dsp.on_read = lambda _c: h.clock.advance(seconds=50)  # 每次讀 DSP 都慢(仍在租約內)

    h.process()

    first, second = (expiry_of(w[2]) for w in h.dsp.writes)
    assert second - first >= timedelta(seconds=50)


# ---- [S49] ----
@pytest.mark.parametrize("action", ["update_budget", "pause_campaign"])
def test_verification_at_the_written_version_compares_the_intent_for_every_action(h, action):
    from rtb.domain.proposal import ActionType

    assert {a.value for a in ActionType} == {"update_budget", "pause_campaign"}
    change = {"new_budget": 150} if action == "update_budget" else {}
    good = h.submit(action_type=action, requested_change=change)
    h.process()
    assert states(h, good)[-1] == ("verified", None)

    # 版本對上了但實際狀態不符意圖(DSP 回報成功卻沒改到)
    bad = h.submit(task_id="t2", campaign_id="c2", action_type=action, requested_change=change)

    def wrote_nothing(p, k, _t):
        current = h.dsp.campaigns["c2"]
        h.dsp.campaigns["c2"] = CampaignView(current.budget, current.status, current.version + 1)
        h.dsp.operations[k] = h.dsp.record_for(p, current.version + 1)

    h.dsp.on_write = wrote_nothing
    h.dsp.answers.append(WriteAnswer(200, None, 4))
    h.process()
    assert states(h, bad)[-1] == ("escalated", "verification_mismatch")


# ---- [S50] ----
def test_verification_after_a_later_write_uses_the_operation_record(h):
    ours = h.submit()

    def someone_else_writes_after(p, k, _t):  # 我們的寫入套用後,別人又改了一次
        h.dsp.apply(p, k)
        current = h.dsp.campaigns["c1"]
        h.dsp.campaigns["c1"] = CampaignView(999, "active", current.version + 1)

    h.dsp.on_write = someone_else_writes_after
    h.dsp.answers.append(WriteAnswer(200, None, 4))
    h.process()
    assert states(h, ours)[-1] == ("verified", None)
    assert h.dsp.lookups == [key_of(ours)]

    # 查不到操作紀錄 → 轉人工
    missing = h.submit(task_id="t2", campaign_id="c2")
    h.dsp.on_write = lambda *_: h.dsp.campaigns.__setitem__("c2", CampaignView(999, "active", 9))
    h.dsp.answers.append(WriteAnswer(200, None, 4))
    h.process()
    assert states(h, missing)[-1] == ("escalated", "verification_mismatch")


# ---- [S87] ----
def test_verification_after_a_later_write_checks_the_full_operation_record(h):
    """版本後來又前進時,同鍵查到的操作紀錄要核對完整內容,不只比寫入後版本。"""
    ours = h.submit()

    def applied_with_other_content_then_moved(p, k, _t):
        h.dsp.apply(p, k)
        h.dsp.operations[k] = h.dsp.record_for(p, 4, new_budget=999)  # 同鍵、版本對,內容不對
        h.dsp.campaigns["c1"] = CampaignView(999, "active", 5)

    h.dsp.on_write = applied_with_other_content_then_moved
    h.dsp.answers.append(WriteAnswer(200, None, 4))
    h.process()

    assert h.dsp.lookups == [key_of(ours)]  # 前置:走的是「版本已前進」那一支
    assert states(h, ours)[-1] == ("escalated", "verification_mismatch")


# ---- [S51] ----
def test_a_failed_verification_read_records_a_timeout_and_keeps_the_written_version(h):
    prop = h.submit()
    h.dsp.on_write = lambda *_: setattr(h.dsp, "read_failures", 1)  # 寫入之後的讀取失敗

    h.process()

    rows = [r for r in h.attempts() if r[0] == key_of(prop)]
    assert [(r[2], r[5]) for r in rows] == [("in_flight", None), ("committed_unverified", 4),
                                          ("committed_unverified", 4)]
    with h.store.transaction() as tx:
        assert attempt_store.latest(tx, key_of(prop)).verification_timeouts == 1
    # 之後的列(轉成已驗證或轉人工)也帶著寫入後版本
    h.dsp.on_write = None
    other = h.submit(task_id="t2", campaign_id="c2")
    h.process()
    assert [(r[2], r[5]) for r in h.attempts() if r[0] == key_of(other)][-1] == ("verified", 4)

    # 轉人工那一列也帶著:回報成功、版本對上,實際狀態卻沒改到
    mismatch = h.submit(task_id="t3", campaign_id="c3")

    def wrote_nothing(p, k, _t):
        current = h.dsp.campaigns["c3"]
        h.dsp.campaigns["c3"] = CampaignView(current.budget, current.status, 4)
        h.dsp.operations[k] = h.dsp.record_for(p, 4)

    h.dsp.on_write = wrote_nothing
    h.dsp.answers.append(WriteAnswer(200, None, 4))
    h.process()
    last = [(r[2], r[5]) for r in h.attempts() if r[0] == key_of(mismatch)][-1]
    assert last == ("escalated", 4)


# ---- [S57] ----
def test_a_locked_campaign_does_not_block_other_campaigns(h):
    stuck = h.submit()  # 最舊的一份,廣告 c1 會卡在結果不明
    h.dsp.answers.append(WriteAnswer(None))
    h.process()
    assert states(h, stuck)[-1] == ("unknown", None)
    h.clock.advance(seconds=1)
    h.submit(task_id="t9", campaign_id="c1", requested_change={"new_budget": 170})  # 同廣告
    h.clock.advance(seconds=1)
    later = h.submit(task_id="t2", campaign_id="c2")

    result = h.process()

    assert result.kind is Result.EXECUTED and result.key == key_of(later)
    assert ("t9", 1, "pending", None, None) in h.proposals()  # 鎖住的廣告那份原封不動


# ---- [S58] ----
def test_a_proposal_for_an_already_attempted_operation_follows_the_existing_outcome(h):
    first = h.submit()
    h.process()
    assert states(h, first)[-1] == ("verified", None)
    # 只換到期時間的新修訂:同一把鍵;版本也對得上(DSP 已前進,所以改成讓讀取看到舊版本)
    h.dsp.campaigns["c1"] = CampaignView(100, "active", 3)
    h.store.accept(proposal(revision=2, decision_expires_at="2026-09-22T12:40:00+00:00"),
                   h.clock)
    writes = len(h.dsp.writes)

    result = h.process()

    # Phase 4 增量 1:取件時就讀到既有嘗試已驗證,直接確認,不交出去
    assert result.kind is Result.IDLE
    assert ("t1", 2, "pending", "handed_off", None) in h.proposals()
    assert len(states(h, first)) == 3 and len(h.dsp.writes) == writes  # 不開新嘗試、不送


def test_an_existing_failed_operation_blocks_the_new_revision(h):
    _previously_failed(h)

    result = h.process()

    assert result.kind is Result.IDLE  # 取件時讀到既有失敗,直接確認,不交出去
    assert ("t1", 2, "pending", "blocked", "operation_previously_failed") in h.proposals()


# ---- [S59](由 Phase 4 增量 1 [S111] 取代:擋下改以收據為條件) ----
def test_blocking_a_proposal_superseded_meanwhile_changes_nothing(h):
    prop = h.submit()
    h.dsp.campaigns["c1"] = CampaignView(100, "active", 4)  # 會被擋下:版本已變

    def someone_took_over(_c):
        h.clock.advance(seconds=61)
        with h.store.transaction() as tx:
            message = h.store.in_progress_for(tx, "t1", key_of(prop))
            h.store.take_over(tx, message, h.clock(), "other")

    h.dsp.on_read = someone_took_over

    result = h.process()

    assert result.kind is Result.LEASE_LOST
    assert h.proposals() == [("t1", 1, "pending", "in_progress", None)]  # 擋下沒寫進去


# ---- [S62](由 Phase 4 增量 1 [S115] 取代:全表已滿改成放掉租約、計入投遞) ----
def test_a_full_unresolved_table_leaves_the_proposal_pending(h, monkeypatch):
    monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", 1)
    h.submit()
    h.dsp.answers.append(WriteAnswer(None))  # 第一份卡在結果不明,佔滿全表名額
    h.process()
    waiting = h.submit(task_id="t2", campaign_id="c2")
    attempts_before, writes_before = h.attempts(), len(h.dsp.writes)

    result = h.process()  # 不以系統錯誤結束

    assert result.kind is Result.DEFERRED
    assert ("t2", 1, "pending", "in_progress", None) in h.proposals()
    assert ("table_full",) in h.query("SELECT last_failure FROM proposals WHERE task_id = 't2'")
    assert h.attempts() == attempts_before and len(h.dsp.writes) == writes_before
    assert key_of(waiting) not in {row[0] for row in h.attempts()}


# ---- [S64] ----
@pytest.mark.parametrize("answer", [
    WriteAnswer(302, None), WriteAnswer(307, None), WriteAnswer(100, None),
    WriteAnswer(201, None, 4), WriteAnswer(204, None), WriteAnswer(200, None, None),
    WriteAnswer(200, None, 0),
], ids=["302", "307", "100", "201", "204", "200-no-version", "200-bad-version"])
def test_an_unlisted_status_is_recorded_as_unknown(h, answer):
    prop = h.submit()
    h.dsp.answers.append(answer)

    h.process()

    assert states(h, prop) == [("in_flight", None), ("unknown", None)]


# ---- [S65] ----
def test_every_send_records_the_capability_expiry(h):
    prop = h.submit()
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    h.dsp.on_read = lambda _c: h.clock.advance(seconds=30)

    h.process()

    with h.store.transaction() as tx:
        rows = attempt_store.history(tx, key_of(prop))
    sends = [r for r in rows if r.state is A.IN_FLIGHT]
    assert [r.capability_expires_at for r in sends] == [expiry_of(w[2]) for w in h.dsp.writes]
    assert sends[1].capability_expires_at > sends[0].capability_expires_at  # 新憑證的到期時間
    token_life = decode(h.dsp.writes[0][2], TEST_KEY)
    assert token_life["exp"] - token_life["iat"] == LIFETIME_SECONDS
    assert rows[1].capability_expires_at == sends[0].capability_expires_at  # 往下帶


# ---- [S67] ----
def test_an_expired_capability_recheck_stops_on_broken_config_and_escalates_at_the_send_limit(
    h, monkeypatch,
):
    prop = h.submit()
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    h.dsp.on_write = lambda *_: os.chmod(h.config, 0o620)  # 送出之後設定檔被改成群組可寫

    with pytest.raises(ExecutorHalted):
        h.process()

    assert states(h, prop)[-1] == ("unknown", None)  # 不下判斷,留在結果不明

    os.chmod(h.config, 0o600)
    h.dsp.on_write = None
    monkeypatch.setattr(attempt_store, "MAX_SENDS", 1)  # 已送過一次就達上限
    other = h.submit(task_id="t2", campaign_id="c2")
    h.dsp.answers.append(WriteAnswer(401, "capability_expired"))
    h.process()
    assert states(h, other)[-1] == ("escalated", "send_limit_reached")
