"""外部寫入嘗試的只增不改歷史表:開始一筆、互斥、轉換、查證逾時、人工處置、上限、重啟恢復。"""

import ast
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rtb.domain.attempt import (
    ESCALATION_CODES,
    FAILURE_CODES,
    UNRESOLVED_STATES,
    AttemptState,
    OutcomeCode,
    can_transition,
    operation_key,
)
from rtb.domain.proposal import parse_proposal
from rtb.executor import attempt_store
from rtb.executor.attempt_store import (
    MAX_ROWS_PER_KEY,
    MAX_SENDS,
    MAX_UNRESOLVED,
    MAX_VERIFICATION_TIMEOUTS,
    CampaignLocked,
    CorruptedAttemptRow,
    HistoryFull,
    IllegalAttemptTransition,
    InvalidOutcome,
    NotInTransaction,
    SendLimitReached,
    TooManyUnresolved,
    VerificationTimeoutLimitReached,
)
from rtb.executor.inbox_store import InboxStore
from tests.domain.proposal_samples import valid

A = AttemptState
C = OutcomeCode
NOW = datetime(2026, 9, 22, 12, 5, tzinfo=UTC)
EXPIRES = NOW + timedelta(minutes=2)  # 這次送出所帶憑證的到期時間(執行一筆起每次送出都要帶)
WRITTEN = 4  # DSP 回報的寫入後版本(轉進已提交待驗證要帶)


def send_fields(target):
    """轉進嘗試中要帶憑證到期時間、轉進已提交待驗證要帶寫入後版本;其他轉換都不帶。"""
    if target is A.IN_FLIGHT:
        return {"capability_expires_at": EXPIRES}
    if target is A.COMMITTED_UNVERIFIED:
        return {"written_version": WRITTEN}
    return {}

# 走到每一種狀態的真實轉換路徑(每一步:目標狀態、結果代碼)
SHORT_PATHS = {
    A.IN_FLIGHT: [],
    A.UNKNOWN: [(A.UNKNOWN, None)],
    A.COMMITTED_UNVERIFIED: [(A.COMMITTED_UNVERIFIED, None)],
    A.VERIFIED: [(A.COMMITTED_UNVERIFIED, None), (A.VERIFIED, None)],
    A.FAILED: [(A.FAILED, C.VERSION_CONFLICT)],
    A.ESCALATED: [(A.ESCALATED, C.IDEMPOTENCY_CONFLICT)],
}
# 繞一圈才到:確認「經過幾次轉換」不影響判斷(最新列一樣帶著廣告編號)
LONG_PATHS = {
    A.IN_FLIGHT: [(A.UNKNOWN, None), (A.IN_FLIGHT, None)],
    A.UNKNOWN: [(A.UNKNOWN, None), (A.IN_FLIGHT, None), (A.UNKNOWN, None)],
    A.COMMITTED_UNVERIFIED: [(A.UNKNOWN, None), (A.COMMITTED_UNVERIFIED, None)],
    A.ESCALATED: [(A.UNKNOWN, None), (A.ESCALATED, C.SEND_LIMIT_REACHED)],
}


@pytest.fixture
def store(tmp_path):
    inbox = InboxStore(tmp_path / "executor.db")
    yield inbox
    inbox.close()


def proposal(**overrides):
    parsed = parse_proposal(valid(**overrides))
    assert parsed.proposal is not None, parsed.errors
    return parsed.proposal


def begin(store, prop, now=NOW):
    with store.transaction() as tx:
        return attempt_store.begin(tx, prop, now, capability_expires_at=EXPIRES)


def latest(store, key):
    with store.transaction() as tx:
        return attempt_store.latest(tx, key)


def history(store, key):
    with store.transaction() as tx:
        return attempt_store.history(tx, key)


def step(store, key, target, code=None, detail=None):
    with store.transaction() as tx:
        current = attempt_store.latest(tx, key)
        return attempt_store.transition(tx, key, current.seq, target, NOW,
                                        code=code, detail=detail, **send_fields(target))


def timeout(store, key):
    seq = latest(store, key).seq
    with store.transaction() as tx:
        return attempt_store.record_verification_timeout(tx, key, seq, NOW)


def walk(store, key, path):
    for target, code in path:
        assert step(store, key, target, code) is not None


def start_in(store, state, *, paths=SHORT_PATHS, **overrides):
    prop = proposal(**overrides)
    key = begin(store, prop).row.key
    walk(store, key, paths[state])
    assert latest(store, key).state is state
    return key


def all_rows(store):
    with store.transaction() as tx:
        return tx.conn.execute("SELECT * FROM attempts ORDER BY key, seq").fetchall()


# ---- [S4] ----
def test_beginning_a_new_attempt_commits_the_first_row_with_the_full_proposal_snapshot(
    store, tmp_path,
):
    prop = proposal()
    begun = begin(store, prop)

    assert begun.created is True
    row = begun.row
    assert (row.key, row.seq, row.state) == (operation_key(prop), 1, A.IN_FLIGHT)
    assert (row.campaign_id, row.send_count, row.verification_timeouts) == ("c1", 1, 0)
    # 另開一條連線讀得到:交易已提交,不是只在同一條連線裡看得見
    other = sqlite3.connect(tmp_path / "executor.db")
    first = other.execute(
        "SELECT task_id, revision, action, expected_version, proposal_json, campaign_id "
        "FROM attempts WHERE key = ? AND seq = 1", (row.key,)).fetchone()
    other.close()
    assert first[:4] == ("t1", 1, "update_budget", 3)
    assert first[5] == "c1"
    assert '"evidence_refs"' in first[4] and '"decision_expires_at"' in first[4]
    with store.transaction() as tx:
        assert attempt_store.snapshot(tx, row.key) == prop


def test_nothing_is_visible_to_others_until_the_transaction_commits(store, tmp_path):
    reader = sqlite3.connect(tmp_path / "executor.db")  # 只讀的旁觀者,不搶寫入鎖
    query = "SELECT COUNT(*) FROM attempts"
    try:
        with store.transaction() as tx:
            attempt_store.begin(tx, proposal(), NOW, capability_expires_at=EXPIRES)
            assert reader.execute(query).fetchone()[0] == 0  # 呼叫端的交易還沒提交
        assert reader.execute(query).fetchone()[0] == 1
    finally:
        reader.close()


# ---- [S5] ----
@pytest.mark.parametrize("state", list(AttemptState))
def test_beginning_an_existing_key_returns_its_current_row_unchanged_in_every_state(store, state):
    key = start_in(store, state)
    before = all_rows(store)

    again = begin(store, proposal(revision=2))  # 同一個邏輯操作重新提案:同一把鍵

    assert again.created is False
    assert again.row.key == key and again.row.state is state
    assert all_rows(store) == before


# ---- [S6] ----
@pytest.mark.parametrize("paths", [SHORT_PATHS, LONG_PATHS], ids=["short", "long"])
def test_every_unresolved_state_locks_the_campaign_against_a_new_key(store, paths):
    for state in UNRESOLVED_STATES:
        campaign = f"c-{state.value}"
        start_in(store, state, paths=paths, campaign_id=campaign)
        before = all_rows(store)

        with pytest.raises(CampaignLocked):
            begin(store, proposal(campaign_id=campaign, requested_change={"new_budget": 999}))

        assert all_rows(store) == before


@pytest.mark.parametrize("state", [A.VERIFIED, A.FAILED])
def test_a_finished_attempt_no_longer_locks_the_campaign(store, state):
    start_in(store, state)

    assert begin(store, proposal(requested_change={"new_budget": 999})).created is True


def test_other_campaigns_are_not_locked(store):
    start_in(store, A.UNKNOWN)

    assert begin(store, proposal(campaign_id="c2")).created is True


# ---- [S7](儲存層也照表擋) ----
def test_the_store_refuses_every_transition_the_table_does_not_allow(store, monkeypatch):
    monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", 10_000)  # 窮舉會留下很多未結案的鍵
    for origin in AttemptState:
        for target in AttemptState:
            if can_transition(origin, target):
                continue
            key = start_in(store, origin, campaign_id=f"x-{origin.value}-{target.value}")
            before = all_rows(store)
            code = C.OTHER_REJECTION if target is A.FAILED else (
                C.IDEMPOTENCY_CONFLICT if target is A.ESCALATED else None)
            with pytest.raises(IllegalAttemptTransition):
                step(store, key, target, code)
            assert all_rows(store) == before, (origin, target)


# ---- [S9] ----
def test_a_transition_from_a_stale_sequence_writes_nothing(store):
    key = start_in(store, A.UNKNOWN)
    before = all_rows(store)

    with store.transaction() as tx:
        result = attempt_store.transition(tx, key, 1, A.IN_FLIGHT, NOW)  # 最新是 2

    assert result is None
    assert all_rows(store) == before


# ---- [S10] ----
def test_the_attempt_history_has_no_update_or_delete_statements():
    source = Path(attempt_store.__file__).read_text(encoding="utf-8")
    strings = [node.value for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    sql = " ".join(strings).upper()
    assert "INSERT" in sql  # 掃得到 SQL,不是空跑
    for verb in ("UPDATE ", "DELETE ", "REPLACE ", "DROP ", "ALTER "):
        assert verb not in sql, verb


# ---- [S11] ----
def test_send_count_starts_at_one_and_only_a_resend_increments_it_up_to_the_limit(store):
    key = begin(store, proposal()).row.key
    assert latest(store, key).send_count == 1

    for expected in range(2, MAX_SENDS + 1):
        step(store, key, A.UNKNOWN)
        timeout(store, key)
        assert latest(store, key).send_count == expected - 1  # 記逾時不動送出次數
        step(store, key, A.IN_FLIGHT)  # 同鍵重送
        assert latest(store, key).send_count == expected

    step(store, key, A.UNKNOWN)
    before = all_rows(store)
    with pytest.raises(SendLimitReached):
        step(store, key, A.IN_FLIGHT)
    assert all_rows(store) == before
    assert step(store, key, A.ESCALATED, C.SEND_LIMIT_REACHED).state is A.ESCALATED


def test_no_other_transition_changes_the_send_count(store):
    key = start_in(store, A.VERIFIED, paths={A.VERIFIED: [
        (A.UNKNOWN, None), (A.COMMITTED_UNVERIFIED, None), (A.VERIFIED, None)]})
    assert {row.send_count for row in history(store, key)} == {1}


# ---- [S12] ----
def test_verification_timeouts_are_counted_durably_capped_and_only_in_the_two_waiting_states(
    store, tmp_path,
):
    for waiting in (A.UNKNOWN, A.COMMITTED_UNVERIFIED):
        key = start_in(store, waiting, campaign_id=f"w-{waiting.value}")
        for count in range(1, MAX_VERIFICATION_TIMEOUTS + 1):
            row = timeout(store, key)
            assert (row.state, row.verification_timeouts) == (waiting, count)
        reopened = InboxStore(tmp_path / "executor.db")
        try:
            assert latest(reopened, key).verification_timeouts == MAX_VERIFICATION_TIMEOUTS
        finally:
            reopened.close()
        before = all_rows(store)
        with pytest.raises(VerificationTimeoutLimitReached):
            timeout(store, key)
        assert all_rows(store) == before
        assert step(store, key, A.ESCALATED, C.VERIFICATION_TIMEOUTS_EXHAUSTED) is not None

    for other in (A.IN_FLIGHT, A.VERIFIED, A.FAILED, A.ESCALATED):
        key = start_in(store, other, campaign_id=f"o-{other.value}")
        before = all_rows(store)
        with pytest.raises(IllegalAttemptTransition):
            timeout(store, key)
        assert all_rows(store) == before


def test_the_timeout_count_is_carried_forward_by_later_rows(store):
    key = start_in(store, A.UNKNOWN)
    with store.transaction() as tx:
        attempt_store.record_verification_timeout(tx, key, 2, NOW)
    step(store, key, A.COMMITTED_UNVERIFIED)

    assert latest(store, key).verification_timeouts == 1


# ---- Phase 3 的 S13,已由 Phase 4 的 [S127] 取代:這支留作「沒有任何持有鍵」時的邊界 ----
def test_restart_recovery_moves_every_in_flight_key_to_unknown_and_touches_nothing_else(store):
    in_flight = [start_in(store, A.IN_FLIGHT, campaign_id=f"f{i}") for i in range(3)]
    resent = start_in(store, A.IN_FLIGHT, paths=LONG_PATHS, campaign_id="f-resent")
    others = {state: start_in(store, state, campaign_id=f"n-{state.value}")
              for state in AttemptState if state is not A.IN_FLIGHT}
    others_before = {key: history(store, key) for key in others.values()}

    with store.transaction() as tx:
        recovery = attempt_store.recover_in_flight(tx, NOW, held=(), written_before=NOW)

    assert set(recovery.moved) == {*in_flight, resent} and recovery.unreadable == ()
    for key in [*in_flight, resent]:
        row = latest(store, key)
        assert row.state is A.UNKNOWN
    assert latest(store, resent).send_count == 2  # 送出次數跟著帶下去
    assert {key: history(store, key) for key in others.values()} == others_before


def test_restart_recovery_still_works_for_a_key_whose_history_is_full(store):
    healthy = start_in(store, A.IN_FLIGHT, campaign_id="healthy")
    full = start_in(store, A.IN_FLIGHT, campaign_id="full")
    _fill_history(store, full)

    with store.transaction() as tx:
        recovery = attempt_store.recover_in_flight(tx, NOW, held=(), written_before=NOW)

    assert set(recovery.moved) == {healthy, full}
    assert latest(store, healthy).state is A.UNKNOWN
    assert latest(store, full).state is A.UNKNOWN


@pytest.mark.parametrize("bad_time", ["bad", b"bad", 12345], ids=["text", "blob", "int"])
def test_restart_recovery_skips_an_unreadable_key_and_still_recovers_the_healthy_ones(
    store, bad_time,
):
    """SQLite 不強制欄位型別:壞掉的值可能是任何型別,讀不回來一律跳過並回報。"""
    healthy = start_in(store, A.IN_FLIGHT, campaign_id="healthy")
    victim = start_in(store, A.IN_FLIGHT, campaign_id="victim")
    with store.transaction() as tx:  # 測試模擬毀損:時間欄位讀不回來,狀態仍是嘗試中
        tx.conn.execute("DELETE FROM attempts WHERE key = ?", (victim,))
        tx.conn.execute(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at) VALUES (?, 1, 'victim', 'in_flight', 1, 0, ?)",
            (victim, bad_time))

    for _restart in range(2):  # 連續兩次重啟都不能卡住
        with store.transaction() as tx:
            recovery = attempt_store.recover_in_flight(tx, NOW, held=(), written_before=NOW)
        assert recovery.unreadable == (victim,)

    assert latest(store, healthy).state is A.UNKNOWN


def test_restart_recovery_leaves_held_keys_alone_even_next_to_an_unreadable_key(store):
    """別的工作者持有的鍵(收件表有處理中訊息)不動;同一次重啟裡讀不回來的鍵照樣跳過並回報,
    健康的孤兒鍵照樣轉。"""
    held = start_in(store, A.IN_FLIGHT, campaign_id="held")
    orphan = start_in(store, A.IN_FLIGHT, campaign_id="orphan")
    victim = start_in(store, A.IN_FLIGHT, campaign_id="victim")
    with store.transaction() as tx:  # 測試模擬毀損:時間欄位讀不回來
        tx.conn.execute("DELETE FROM attempts WHERE key = ?", (victim,))
        tx.conn.execute(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at) VALUES (?, 1, 'victim', 'in_flight', 1, 0, 'bad')",
            (victim,))
    held_before = history(store, held)

    with store.transaction() as tx:
        recovery = attempt_store.recover_in_flight(tx, NOW, held=[held], written_before=NOW)

    assert recovery.moved == (orphan,) and recovery.unreadable == (victim,)
    assert history(store, held) == held_before
    assert latest(store, orphan).state is A.UNKNOWN


def test_restart_recovery_leaves_a_freshly_written_legacy_attempt_alone(store):
    """舊鍵(不在 held 裡)剛寫下嘗試中:可能有工作者正在對帳它、等 DSP 回應,不能當孤兒轉。"""
    fresh = start_in(store, A.IN_FLIGHT, campaign_id="fresh")
    before = history(store, fresh)

    with store.transaction() as tx:
        recovery = attempt_store.recover_in_flight(
            tx, NOW, held=(), written_before=NOW - timedelta(seconds=1))

    assert recovery.moved == () and history(store, fresh) == before


def test_restart_recovery_requires_the_held_keys_and_the_age_cutoff(store):
    """兩個參數各自都必填:漏傳任一個都不能悄悄退回舊語意(改掉別的工作者正在做的嘗試)。"""
    start_in(store, A.IN_FLIGHT)
    with pytest.raises(TypeError), store.transaction() as tx:
        attempt_store.recover_in_flight(tx, NOW, written_before=NOW)  # type: ignore[call-arg]
    with pytest.raises(TypeError), store.transaction() as tx:
        attempt_store.recover_in_flight(tx, NOW, held=())  # type: ignore[call-arg]


def test_restart_recovery_age_cutoff_boundary(store):
    """剛好寫在截止時間那一刻的算夠舊(轉);晚一微秒的算剛寫下(不轉)。"""
    at_cutoff = start_in(store, A.IN_FLIGHT, campaign_id="at")
    with store.transaction() as tx:
        just_after = attempt_store.recover_in_flight(
            tx, NOW, held=(), written_before=NOW - timedelta(microseconds=1))
    assert just_after.moved == ()
    with store.transaction() as tx:
        at = attempt_store.recover_in_flight(tx, NOW, held=(), written_before=NOW)
    assert at.moved == (at_cutoff,)


def test_the_database_itself_refuses_a_second_terminal_row_for_one_key(store):
    """計數算法的前提(每把鍵最多一列終點列)由資料庫的唯一限制保證,不靠事後相減看正負:
    相減會被另一把還沒結案的鍵抵銷成 0(代碼審第 3 輪兩席各自實測)。"""
    finished = start_in(store, A.FAILED, requested_change={"new_budget": 998})
    live = start_in(store, A.IN_FLIGHT)  # 同一個廣告另有一把還沒結案的鍵,會抵銷
    with pytest.raises(sqlite3.IntegrityError), store.transaction() as tx:
        tx.conn.execute(
            "INSERT INTO attempts (key, seq, campaign_id, state, code, send_count, "
            "verification_timeouts, written_at) VALUES (?, 3, 'c1', 'failed', 'other_rejection', "
            "1, 0, '2026-09-22T12:05:00.000000Z')", (finished,))

    assert latest(store, live).state is A.IN_FLIGHT
    with pytest.raises(CampaignLocked):
        begin(store, proposal(requested_change={"new_budget": 999}))


# ---- [S14] [S15] ----
def _race(tmp_path, proposals):
    barrier = threading.Barrier(len(proposals))
    results, errors = [], []

    def worker(prop):
        own = InboxStore(tmp_path / "executor.db")  # 每條執行緒自己的連線
        try:
            barrier.wait()
            results.append(begin(own, prop))
        except CampaignLocked as exc:
            errors.append(exc)
        finally:
            own.close()

    threads = [threading.Thread(target=worker, args=(p,)) for p in proposals]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


def test_concurrent_begins_for_the_same_key_create_exactly_one(store, tmp_path):
    results, errors = _race(tmp_path, [proposal(revision=r) for r in range(1, 13)])

    assert errors == [] and len(results) == 12
    assert sum(1 for r in results if r.created) == 1
    assert len(all_rows(store)) == 1


def test_concurrent_begins_for_different_keys_on_one_campaign_create_exactly_one(
    store, tmp_path,
):
    props = [proposal(requested_change={"new_budget": 100 + i}) for i in range(12)]

    results, errors = _race(tmp_path, props)

    assert len(results) == 1 and results[0].created
    assert len(errors) == 11
    assert len(all_rows(store)) == 1


# ---- [S16] ----
def _origin_for(target):
    for origin in AttemptState:
        if can_transition(origin, target):
            return origin
    return None


def _code_fits_generally(target, code):
    if code is None:
        return target not in (A.FAILED, A.ESCALATED)
    if target is A.FAILED:
        return code in FAILURE_CODES and code is not C.MANUAL_FAILURE
    if target is A.ESCALATED:
        return code in ESCALATION_CODES
    return False


def test_every_code_is_accepted_only_with_its_own_kind_of_target_state_and_detail_is_clean_ascii(
    store, monkeypatch,
):
    """從程式自己的狀態清單與代碼清單列舉每一種組合,在儲存層真的寫一次。"""
    monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", 10_000)  # 窮舉會留下很多未結案的鍵
    case = 0
    for target in AttemptState:
        origin = _origin_for(target)
        if origin is None:
            continue
        for code in [None, *OutcomeCode, "not_a_code"]:
            case += 1
            key = start_in(store, origin, campaign_id=f"k{case}")
            before = all_rows(store)
            if code != "not_a_code" and _code_fits_generally(target, code):
                assert step(store, key, target, code).code == code
            else:
                with pytest.raises(InvalidOutcome):
                    step(store, key, target, code)
                assert all_rows(store) == before, (target, code)

    key = start_in(store, A.IN_FLIGHT, campaign_id="detail")
    for dirty in ("狀態:已驗證", "a\nb", "x" * 501):
        with pytest.raises(InvalidOutcome):
            step(store, key, A.FAILED, C.OTHER_REJECTION, dirty)
    assert step(store, key, A.FAILED, C.OTHER_REJECTION, "dsp said 400").detail == "dsp said 400"


# ---- [S17] ----
def _resolve(store, key, outcome, reason):
    seq = latest(store, key).seq
    with store.transaction() as tx:
        return attempt_store.resolve(tx, key, seq, outcome, reason, NOW)


@pytest.mark.parametrize("outcome,code", [(A.VERIFIED, None), (A.FAILED, C.MANUAL_FAILURE)])
def test_only_an_escalated_attempt_can_be_resolved_and_only_with_a_reason(store, outcome, code):
    for state in AttemptState:
        if state is A.ESCALATED:
            continue
        key = start_in(store, state, campaign_id=f"r-{state.value}")
        before = all_rows(store)
        with pytest.raises(IllegalAttemptTransition):
            _resolve(store, key, outcome, "checked the dsp by hand")
        assert all_rows(store) == before

    key = start_in(store, A.ESCALATED, campaign_id="esc")
    before = all_rows(store)
    for bad_reason in ("", "   ", None, "查過了", "a\nb"):
        with pytest.raises(InvalidOutcome):
            _resolve(store, key, outcome, bad_reason)
    for bad_outcome in (A.IN_FLIGHT, A.UNKNOWN, A.COMMITTED_UNVERIFIED, A.ESCALATED):
        with pytest.raises(IllegalAttemptTransition):
            _resolve(store, key, bad_outcome, "checked the dsp by hand")
    assert all_rows(store) == before

    row = _resolve(store, key, outcome, "checked the dsp by hand")
    assert (row.state, row.code, row.detail) == (outcome, code, "checked the dsp by hand")


def test_manual_failure_code_cannot_be_used_by_a_general_transition(store):
    key = start_in(store, A.IN_FLIGHT)
    with pytest.raises(InvalidOutcome):
        step(store, key, A.FAILED, C.MANUAL_FAILURE)


# ---- [S18] ----
def _fill_history(store, key):
    """用合法的方式把歷史列灌到上限:只為了測上限,借用直寫表格模擬舊資料。"""
    with store.transaction() as tx:
        row = attempt_store.latest(tx, key)
        for seq in range(row.seq + 1, MAX_ROWS_PER_KEY + 1):
            tx.conn.execute(
                "INSERT INTO attempts (key, seq, campaign_id, state, code, detail, send_count, "
                "verification_timeouts, written_at) VALUES (?, ?, ?, ?, NULL, NULL, ?, 0, ?)",
                (key, seq, row.campaign_id, row.state.value, row.send_count,
                 "2026-09-22T12:05:00Z"),
            )


def test_a_capped_history_still_accepts_escalation_and_resolution_but_nothing_else(store):
    key = start_in(store, A.UNKNOWN)
    _fill_history(store, key)
    assert len(history(store, key)) == MAX_ROWS_PER_KEY
    before = all_rows(store)

    for target, code in [(A.IN_FLIGHT, None), (A.COMMITTED_UNVERIFIED, None),
                         (A.FAILED, C.NOT_HAPPENED)]:
        with pytest.raises(HistoryFull):
            step(store, key, target, code)
    with pytest.raises(HistoryFull):
        timeout(store, key)
    assert all_rows(store) == before

    assert step(store, key, A.ESCALATED, C.CANNOT_PROVE_NOT_HAPPENED) is not None
    assert _resolve(store, key, A.FAILED, "checked the dsp by hand").state is A.FAILED


def test_the_normal_worst_case_path_stays_well_under_the_cap(store):
    key = begin(store, proposal()).row.key
    for _ in range(MAX_SENDS - 1):
        step(store, key, A.UNKNOWN)
        step(store, key, A.IN_FLIGHT)
    step(store, key, A.UNKNOWN)
    step(store, key, A.COMMITTED_UNVERIFIED)
    for _ in range(MAX_VERIFICATION_TIMEOUTS):
        timeout(store, key)
    step(store, key, A.ESCALATED, C.VERIFICATION_TIMEOUTS_EXHAUSTED)
    _resolve(store, key, A.VERIFIED, "checked the dsp by hand")

    assert len(history(store, key)) == 14 < MAX_ROWS_PER_KEY


# ---- [S19] ----
def test_the_attempt_store_neither_reaches_the_network_nor_opens_its_own_connection():
    source = Path(attempt_store.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    network = {"socket", "http", "urllib", "ssl", "requests", "httpx", "rtb.httpkit",
               "rtb.httpclient", "rtb.sqlitekit"}
    assert {m for m in imported if m.split(".")[0] in network or m in network} == set()
    names = {node.attr if isinstance(node, ast.Attribute) else node.id
             for node in ast.walk(tree) if isinstance(node, (ast.Attribute, ast.Name))}
    assert not names & {"connect", "begin_immediate", "immediate_transaction"}
    strings = " ".join(node.value for node in ast.walk(tree)
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)).upper()
    assert "BEGIN" not in strings and "COMMIT" not in strings and "ROLLBACK" not in strings


def test_only_a_transaction_from_the_executor_database_entry_is_accepted(store, tmp_path):
    """自己開的連線、自己下 BEGIN(延遲交易,不排隊搶寫入鎖)都不算;
    交易物件的連線已不在交易中也不算。"""
    bare = sqlite3.connect(tmp_path / "executor.db", isolation_level=None)
    try:
        bare.execute("BEGIN")
        with pytest.raises(NotInTransaction):
            attempt_store.begin(bare, proposal(), NOW, capability_expires_at=EXPIRES)
        bare.execute("ROLLBACK")
    finally:
        bare.close()
    with store.transaction() as tx:
        pass
    with pytest.raises(NotInTransaction):
        # 交易已結束,拿舊物件再寫也不行
        attempt_store.begin(tx, proposal(), NOW, capability_expires_at=EXPIRES)
    assert all_rows(store) == []


def test_a_transaction_object_cannot_be_built_without_the_entry_s_issuer(tmp_path):
    """執行期就擋:不是交易入口發的(不管是直接建、換別名建、或子類別)都不收。"""
    bare = sqlite3.connect(tmp_path / "executor.db", isolation_level=None)
    try:
        bare.execute("BEGIN")
        with pytest.raises(NotInTransaction):
            attempt_store.ExecutorTransaction(bare, object())
        alias = attempt_store.ExecutorTransaction
        with pytest.raises(NotInTransaction):
            alias(bare, None)
        bare.execute("ROLLBACK")
    finally:
        bare.close()


def test_a_subclass_of_the_transaction_object_is_not_accepted(store):
    class Imitation(attempt_store.ExecutorTransaction):
        pass

    with store.transaction() as tx:
        fake = object.__new__(Imitation)  # 不經建構子,把真交易物件的內容整份複製過去
        for slot in attempt_store.ExecutorTransaction.__slots__:
            object.__setattr__(fake, slot, object.__getattribute__(tx, slot))
        assert fake.is_open  # 除了型別,其他都跟真的一樣
        with pytest.raises(NotInTransaction):
            attempt_store.begin(fake, proposal(), NOW, capability_expires_at=EXPIRES)
    assert all_rows(store) == []


def test_a_finished_transaction_object_stays_dead_even_when_the_connection_opens_another(store):
    with store.transaction() as stale:
        pass
    with store.transaction(), pytest.raises(NotInTransaction):
        attempt_store.begin(stale, proposal(), NOW, capability_expires_at=EXPIRES)
    assert all_rows(store) == []


def test_only_the_executor_database_module_refers_to_the_issuer():
    """原始碼層面也看一次:憑證只准在嘗試紀錄模組定義、在收件口資料庫模組使用。"""
    src = Path(attempt_store.__file__).resolve().parents[1]
    users = sorted({file.name for file in src.rglob("*.py")
                    if "_EXECUTOR_TRANSACTION_ISSUER" in file.read_text(encoding="utf-8")})
    assert users == ["attempt_store.py", "inbox_store.py"]
    assert not hasattr(attempt_store, "EXECUTOR_TRANSACTION_ISSUER")  # 名稱是私有的


def test_the_only_place_that_issues_a_transaction_opens_it_with_the_write_lock():
    """收件口資料庫模組裡,交易物件只在 transaction() 建立,而且那個函式用排隊搶寫入鎖的
    immediate_transaction 開交易;改成自己下普通的開始交易(不排隊)就會紅。"""
    from rtb.executor import inbox_store

    tree = ast.parse(Path(inbox_store.__file__).read_text(encoding="utf-8"))
    issuing = {
        func.name for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef)
        and any(isinstance(node, ast.Name | ast.Attribute)
                and getattr(node, "attr", getattr(node, "id", None))
                == "_EXECUTOR_TRANSACTION_ISSUER" for node in ast.walk(func))
    }
    assert issuing == {"transaction"}
    function = next(f for f in ast.walk(tree)
                    if isinstance(f, ast.FunctionDef) and f.name == "transaction")
    called = {getattr(n.func, "id", getattr(n.func, "attr", None))
              for n in ast.walk(function) if isinstance(n, ast.Call)}
    assert "immediate_transaction" in called
    source = ast.get_source_segment(Path(inbox_store.__file__).read_text(encoding="utf-8"),
                                    function) or ""
    assert "BEGIN" not in source.upper().replace("IMMEDIATE_TRANSACTION", "")


def test_writes_outside_a_transaction_are_refused(tmp_path):
    conn = sqlite3.connect(tmp_path / "bare.db", isolation_level=None)
    try:
        with pytest.raises(NotInTransaction):
            attempt_store.begin(conn, proposal(), NOW, capability_expires_at=EXPIRES)
    finally:
        conn.close()


# ---- 時間、序號與毀損資料 ----
NAIVE = datetime(2026, 9, 22, 12, 5)  # 沒有時區


def test_a_time_without_a_time_zone_is_refused_by_every_write_and_nothing_is_written(store):
    key = start_in(store, A.UNKNOWN)
    escalated = start_in(store, A.ESCALATED, campaign_id="esc")
    in_flight = start_in(store, A.IN_FLIGHT, campaign_id="fl")
    before = all_rows(store)
    writes = [
        lambda tx: attempt_store.begin(tx, proposal(campaign_id="new"), NAIVE,
                                       capability_expires_at=EXPIRES),
        lambda tx: attempt_store.transition(tx, key, 2, A.IN_FLIGHT, NAIVE),
        lambda tx: attempt_store.record_verification_timeout(tx, key, 2, NAIVE),
        lambda tx: attempt_store.resolve(tx, escalated, 2, A.FAILED, "checked by hand", NAIVE),
        lambda tx: attempt_store.recover_in_flight(tx, NAIVE, held=(), written_before=NOW),
    ]
    for write in writes:
        with pytest.raises(ValueError), store.transaction() as tx:
            write(tx)
    assert all_rows(store) == before
    assert latest(store, in_flight).state is A.IN_FLIGHT


@pytest.mark.parametrize("seq", [True, 1.0, "1", None])
def test_an_expected_sequence_that_is_not_a_plain_integer_is_refused(store, seq):
    key = begin(store, proposal()).row.key
    before = all_rows(store)

    with pytest.raises(TypeError), store.transaction() as tx:
        attempt_store.transition(tx, key, seq, A.UNKNOWN, NOW)

    assert all_rows(store) == before


def _tamper_snapshot(store, key, text):
    with store.transaction() as tx:
        tx.conn.execute("DELETE FROM attempts WHERE key = ?", (key,))  # 測試模擬毀損,只在測試裡
        tx.conn.execute(
            "INSERT INTO attempts VALUES (?, 1, 'c1', 'in_flight', NULL, NULL, 1, 0, "
            "'2026-09-22T12:05:00.000000Z', 't1', 1, 'update_budget', 3, ?, "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",  # Phase 9 補的
            # 來源、執行者、程式版本(增量 1)與開始時的四樣核對材料(增量 3)
            (key, text))


def test_a_snapshot_that_is_not_json_reads_back_as_a_corrupted_row(store):
    key = begin(store, proposal()).row.key
    _tamper_snapshot(store, key, "{not valid json!!")

    with pytest.raises(CorruptedAttemptRow), store.transaction() as tx:
        attempt_store.snapshot(tx, key)


def test_a_snapshot_whose_content_no_longer_matches_its_key_reads_back_as_corrupted(store):
    import json

    key = begin(store, proposal()).row.key
    swapped = proposal(requested_change={"new_budget": 999999}).to_primitives()
    _tamper_snapshot(store, key, json.dumps(swapped))

    with pytest.raises(CorruptedAttemptRow), store.transaction() as tx:
        attempt_store.snapshot(tx, key)


# ---- [S20] ----
def test_after_beginning_everything_is_keyed_by_the_stored_key_and_the_snapshot_rebuilds_the_proposal(  # noqa: E501 - 測試名要跟計劃條款綁定的名字一字不差
    store,
):
    import inspect

    prop = proposal(decision_created_at="2026-09-22T20:00:00+08:00")
    key = begin(store, prop).row.key
    with store.transaction() as tx:
        rebuilt = attempt_store.snapshot(tx, key)
    assert rebuilt == prop
    assert type(rebuilt) is type(prop)
    assert type(rebuilt.requested_change) is type(prop.requested_change)  # 唯讀對應,能再建提案

    for name in ("transition", "record_verification_timeout", "resolve", "latest",
                 "history", "snapshot"):
        params = inspect.signature(getattr(attempt_store, name)).parameters
        assert "key" in params, name
        assert "proposal" not in params, name


# ---- [S21] ----
def test_beginning_is_refused_when_too_many_attempts_are_unresolved(store):
    for i in range(MAX_UNRESOLVED):
        begin(store, proposal(campaign_id=f"u{i}"))
    before = all_rows(store)

    with pytest.raises(TooManyUnresolved):
        begin(store, proposal(campaign_id="one-too-many"))
    assert all_rows(store) == before

    step(store, operation_key(proposal(campaign_id="u0")), A.FAILED, C.OTHER_REJECTION)
    assert begin(store, proposal(campaign_id="one-too-many")).created is True


def _naive_unresolved(store, campaign=None):
    with store.transaction() as tx:
        rows = tx.conn.execute(
            "SELECT key, state, campaign_id FROM attempts a "
            "WHERE seq = (SELECT MAX(seq) FROM attempts b WHERE b.key = a.key)").fetchall()
    return sum(1 for _key, state, camp in rows
               if AttemptState(state) in UNRESOLVED_STATES and campaign in (None, camp))


def test_the_unresolved_counts_agree_with_reading_every_keys_latest_row(store, monkeypatch):
    monkeypatch.setattr(attempt_store, "MAX_UNRESOLVED", 10_000)
    for i, state in enumerate([*AttemptState, *AttemptState]):
        start_in(store, state, paths=LONG_PATHS if state in LONG_PATHS and i % 2 else SHORT_PATHS,
                 campaign_id=f"m{i % 4}-{i}")
    key = start_in(store, A.ESCALATED, campaign_id="m0-resolved")
    _resolve(store, key, A.VERIFIED, "checked the dsp by hand")

    campaigns = [None, *(f"m{i % 4}-{i}" for i in range(12))]
    expected = {campaign: _naive_unresolved(store, campaign) for campaign in campaigns}
    assert expected[None] > 0  # 真的有未結案的鍵,不是 0 等於 0
    with store.transaction() as tx:
        actual = {campaign: attempt_store.unresolved_count(tx, campaign) for campaign in campaigns}
    assert actual == expected


def test_the_unresolved_counts_use_the_partial_indexes_instead_of_rereading_every_key(store):
    with store.transaction() as tx:
        for campaign in (None, "c1"):
            query, params = attempt_store.unresolved_count_query(campaign)
            plan = " ".join(str(row[3]) for row in tx.conn.execute(
                "EXPLAIN QUERY PLAN " + query, params))
            assert "INDEX" in plan, plan
            assert "CORRELATED" not in plan.upper(), plan


def test_the_time_is_stored_as_given(store):
    later = NOW + timedelta(minutes=3)
    key = begin(store, proposal(), later).row.key
    assert latest(store, key).written_at == later


# ---- 執行一筆 [S66] ----
def test_entering_committed_unverified_requires_the_written_version(store, tmp_path):
    key = begin(store, proposal()).row.key
    before = all_rows(store)
    for bad in (None, 0, True, "4", 4.0):
        with store.transaction() as tx, pytest.raises(attempt_store.IncompleteRow):
            attempt_store.transition(tx, key, 1, A.COMMITTED_UNVERIFIED, NOW, written_version=bad)
    with store.transaction() as tx, pytest.raises(InvalidOutcome):  # 別的轉換不能夾帶寫入後版本
        attempt_store.transition(tx, key, 1, A.UNKNOWN, NOW, written_version=4)
    assert all_rows(store) == before

    committed = step(store, key, A.COMMITTED_UNVERIFIED)
    assert committed.written_version == WRITTEN
    verified = step(store, key, A.VERIFIED)  # 之後的列往下帶,跨重新開啟資料庫讀得回來
    reopened = InboxStore(tmp_path / "executor.db")
    try:
        with reopened.transaction() as tx:
            assert attempt_store.latest(tx, key).written_version == WRITTEN
    finally:
        reopened.close()
    assert verified.written_version == WRITTEN


def test_every_new_in_flight_row_requires_the_capability_expiry(store):
    """S65 的儲存層那一半:開始一筆與重送都要帶這次送出所帶憑證的到期時間,缺了就拒絕寫入。"""
    naive = datetime(2026, 9, 22, 12, 7)
    for bad in (None, naive, "2026-09-22T12:07:00Z"):
        with store.transaction() as tx, pytest.raises((attempt_store.IncompleteRow, ValueError)):
            attempt_store.begin(tx, proposal(), NOW, capability_expires_at=bad)
    assert all_rows(store) == []
    key = start_in(store, A.UNKNOWN)
    with store.transaction() as tx, pytest.raises(attempt_store.IncompleteRow):
        attempt_store.transition(tx, key, 2, A.IN_FLIGHT, NOW)
    later = EXPIRES + timedelta(minutes=5)
    with store.transaction() as tx:
        resent = attempt_store.transition(tx, key, 2, A.IN_FLIGHT, NOW, capability_expires_at=later)
    assert resent.capability_expires_at == later
    assert step(store, key, A.UNKNOWN).capability_expires_at == later  # 往下帶給對帳用
