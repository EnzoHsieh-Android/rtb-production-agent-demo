"""護欄表格:Phase 6 增量 2 的 S400 到 S405、S409。

單筆規則只有兩處程式:執行前檢查(看 DSP 現況)與簽發器(看租戶設定)。這裡的護欄表把兩處的
六條規則與順序寫在一起,當成測試資料跑邊界值;表不是第三套檢查。比例上限是使用者 2026-09-23
裁定的值:加的量不得超過 max(現況的五成取下限, 1),全域一個值。
完整性三份清單與舊收件表寫入新代碼([S404]、[S406])依賴增量 1,增量 1 合併後補上。
"""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from rtb.analyzer import policy
from rtb.analyzer.task_store import TaskRow
from rtb.domain.attempt import AttemptState as A
from rtb.domain.attempt import operation_key
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.domain.proposal import MAX_INT
from rtb.domain.task_state import TaskState
from rtb.executor import attempt_store
from rtb.executor.execution import CampaignView, Result, WriteAnswer, precheck
from rtb.executor.inbox_store import BlockCode, InboxStore
from tests.executor.fakes import Harness, proposal, write_config

LATER = "2026-09-22T12:40:00+00:00"


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def _view(budget, version=3, status="active"):
    return CampaignView(budget=budget, status=status, version=version)


def _raise_to(new_budget, **overrides):
    return proposal(requested_change={"new_budget": new_budget}, **overrides)


def _pause():
    return proposal(action_type="pause_campaign", requested_change={})


# ---- S400 ----
@pytest.mark.parametrize(("current", "new_budget", "blocked"), [
    (100, 150, False), (100, 149, False), (100, 151, True),  # 剛好等於、差 1、超過 1
    (3, 4, False), (3, 5, True),  # 3 的五成取下限是 1
    (1, 2, False), (1, 3, True),  # 小額:最小加額 1
    (0, 1, False), (0, 2, True),  # 真實讀取路徑走不到(預算 0 讀成讀不懂),只在這一層驗公式
    (MAX_INT - 1, MAX_INT, False),
])
def test_a_budget_increase_is_bounded_by_the_ratio_cap(current, new_budget, blocked):
    expected = BlockCode.BUDGET_INCREASE_TOO_LARGE if blocked else None
    assert precheck(_raise_to(new_budget), _view(current)) is expected


def test_a_ratio_block_writes_nothing(h):
    h.submit(requested_change={"new_budget": 151})

    result = h.process()

    assert (result.kind, result.block_code) == (Result.BLOCKED, BlockCode.BUDGET_INCREASE_TOO_LARGE)
    assert h.proposals() == [("t1", 1, "pending", "blocked", "budget_increase_too_large")]
    assert h.attempts() == [] and h.dsp.writes == []  # 不寫嘗試紀錄、不呼叫 DSP 寫入


# ---- S401 ----
@pytest.mark.parametrize("current", [1, 100, 10_000])
def test_decreases_and_pauses_are_not_bounded_by_the_ratio_cap(current):
    assert precheck(_raise_to(1), _view(current)) is None  # 減到 1
    assert precheck(_raise_to(current), _view(current)) is None  # 不變
    assert precheck(_pause(), _view(current)) is None


# ---- S402 ----
_NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def _policy_proposal(budget):
    """分析端現行決策規則依這個預算產生的提案(配速極低、有投放,一定出提案)。"""
    task = TaskRow(task_id="t1", seq=3, state=TaskState.ANALYZING, campaign_id="c1",
                   proposal=None, error_detail=None, written_at=_NOW)
    evidence = tuple(
        Evidence(evidence_id=f"t1-3-{suffix}", task_id="t1", kind=kind, source="dsp",
                 observed_at=_NOW, campaign_version_observed=version, content_hash="a" * 64,
                 trust_class=TrustClass.TRUSTED, payload=MappingProxyType(payload))
        for suffix, kind, version, payload in (
            ("state", EvidenceKind.CAMPAIGN_STATE, 7,
             {"id": "c1", "budget": budget, "status": "active", "version": 7}),
            ("metrics", EvidenceKind.METRICS, None,
             {"campaign_id": "c1", "window": "1h", "impressions": 500, "clicks": 12,
              "conversions": 1, "spend": 0.0, "revenue": 5.0}),
        ))
    decision = policy.decide(task, evidence, _NOW)
    return decision.proposal


def test_the_analyzer_policy_never_trips_the_ratio_cap():
    # 單一測試逐一檢查(不參數化成上千項,全套數字才看得出真實變化);失敗時指出第一個違反的預算
    budgets = [*range(1, 1001), 10**6, 10**9, 2**62, MAX_INT - 1, MAX_INT]
    tripped = next((b for b in budgets
                    if precheck(_policy_proposal(b), _view(b, version=7)) is not None), None)
    assert tripped is None, f"預算 {tripped} 時分析端的提案被執行前檢查擋下"


# ---- S403 ----
def _campaign(budget=100, version=3, status="active"):
    return lambda h: h.dsp.campaigns.__setitem__("c1", _view(budget, version, status))


def _tenant(**kwargs):
    return lambda h: write_config(h.config, **kwargs)


def _no_campaign(h):
    del h.dsp.campaigns["c1"]


def _as_is(_h):
    pass


# 護欄表:(規則, 環境設定, 提案新預算, 預期擋下代碼或 None)。每一列只讓要驗的那一條在邊界上,
# 其餘五條明確通過:預設現況 100(版本 3、投放中)、新預算 150、租戶上限 1000。
GUARDRAILS = [
    # 1 廣告不存在
    ("campaign_not_found", _no_campaign, 150, BlockCode.CAMPAIGN_NOT_FOUND),
    ("campaign_not_found", _as_is, 150, None),
    # 2 不在投放
    ("campaign_not_active", _campaign(status="paused"), 150, BlockCode.CAMPAIGN_NOT_ACTIVE),
    ("campaign_not_active", _campaign(status="active"), 150, None),
    # 3 版本已變:比對是不等,現況比觀察到的舊也擋
    ("version_changed", _campaign(version=4), 150, BlockCode.VERSION_CHANGED),
    ("version_changed", _campaign(version=2), 150, BlockCode.VERSION_CHANGED),
    ("version_changed", _campaign(version=3), 150, None),
    # 4 比例上限(新):現況 100,上限加 50
    ("budget_increase_too_large", _as_is, 150, None),
    ("budget_increase_too_large", _as_is, 149, None),
    ("budget_increase_too_large", _as_is, 151, BlockCode.BUDGET_INCREASE_TOO_LARGE),
    # 5 不屬於租戶
    ("campaign_not_allowed", _tenant(campaigns=("c2",)), 150, BlockCode.CAMPAIGN_NOT_ALLOWED),
    ("campaign_not_allowed", _tenant(campaigns=("c1", "c2")), 150, None),
    # 6 超過單一廣告上限:現況 900 讓加的量都在比例內,上限 1000
    ("over_budget_cap", _campaign(budget=900), 1000, None),
    ("over_budget_cap", _campaign(budget=900), 999, None),
    ("over_budget_cap", _campaign(budget=900), 1001, BlockCode.OVER_BUDGET_CAP),
]
SINGLE_RULE_CODES = (  # 表上的順序就是程式判斷的順序
    BlockCode.CAMPAIGN_NOT_FOUND, BlockCode.CAMPAIGN_NOT_ACTIVE, BlockCode.VERSION_CHANGED,
    BlockCode.BUDGET_INCREASE_TOO_LARGE, BlockCode.CAMPAIGN_NOT_ALLOWED, BlockCode.OVER_BUDGET_CAP,
)


def _run(h, setup, new_budget):
    h.submit(requested_change={"new_budget": new_budget})
    setup(h)
    return h.process()


@pytest.mark.parametrize(("rule", "setup", "new_budget", "expected"), GUARDRAILS,
                         ids=[f"{row[0]}-{row[2]}-{i}" for i, row in enumerate(GUARDRAILS)])
def test_every_guardrail_holds_at_its_boundaries(h, rule, setup, new_budget, expected):
    result = _run(h, setup, new_budget)

    if expected is None:
        assert result.kind is Result.EXECUTED, rule
        assert len(h.dsp.writes) == 1
    else:
        assert (result.kind, result.block_code) == (Result.BLOCKED, expected), rule
        assert h.attempts() == [] and h.dsp.writes == []


# 三份明列清單:之後誰加新擋下原因都得先決定歸哪一份,忘了就紅
NON_SINGLE_CODES = (  # 不是單筆規則:鍵已存在的分流、總曝險(增量 1)
    BlockCode.OPERATION_PREVIOUSLY_FAILED, BlockCode.AGGREGATE_LIMIT_REACHED,
)
HISTORICAL_CODES = ()  # 規則已拿掉、只為讀舊資料而留在列舉裡的代碼(回退時比例代碼搬來這裡)


# ---- S404 ----
def test_the_guardrail_table_covers_every_block_code():
    lists = (set(SINGLE_RULE_CODES), set(NON_SINGLE_CODES), set(HISTORICAL_CODES))
    assert set().union(*lists) == set(BlockCode)
    assert all(not (a & b) for i, a in enumerate(lists) for b in lists[i + 1:])  # 兩兩不交集
    assert {row[0] for row in GUARDRAILS} == {code.value for code in SINGLE_RULE_CODES}
    for code in SINGLE_RULE_CODES:
        rows = [row for row in GUARDRAILS if row[0] == code.value]
        assert any(row[3] is code for row in rows), code  # 至少一列擋下
        assert any(row[3] is None for row in rows), code  # 至少一列通過
    assert all(row[3] in (None, BlockCode(row[0])) for row in GUARDRAILS)  # 擋下代碼就是那一列的


# ---- S406 ----
def _increment_1_inbox(db, clock):
    """用增量 1 版的允許值清單(含總曝險已滿、不含比例上限)建舊收件表,照增量 1 [S338] 的手法;
    舊列帶著增量 1 的擋下原因,重建時要原樣搬過去(代碼審外家席)。回傳舊列內容。"""
    old = InboxStore(db)
    old.accept(proposal(task_id="old"), clock)
    with old.transaction() as tx:
        delivery = old.receive(tx, clock(), "worker")
        assert delivery is not None
        assert old.ack_blocked(tx, delivery.receipt, clock(), BlockCode.AGGREGATE_LIMIT_REACHED)
    old.close()
    conn = sqlite3.connect(db)
    try:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'proposals'").fetchone()[0]
        old_sql = sql.replace(", 'budget_increase_too_large'", "").replace(
            "'budget_increase_too_large', ", "")
        assert old_sql != sql and "'aggregate_limit_reached'" in old_sql  # 真的是增量 1 版
        conn.executescript(f"ALTER TABLE proposals RENAME TO p_old; {old_sql};"  # noqa: S608 - 測試模擬舊表
                           "INSERT INTO proposals SELECT * FROM p_old; DROP TABLE p_old;")
        assert conn.execute("SELECT disposition, block_code FROM proposals").fetchall() == [
            ("blocked", "aggregate_limit_reached")]
        return conn.execute("SELECT * FROM proposals").fetchall()
    finally:
        conn.close()


def _block_new_by_ratio(store, clock):
    store.accept(proposal(task_id="new"), clock)
    with store.transaction() as tx:
        delivery = store.receive(tx, clock(), "worker")
        assert delivery is not None and delivery.message.proposal.task_id == "new"
        assert store.ack_blocked(tx, delivery.receipt, clock(),
                                 BlockCode.BUDGET_INCREASE_TOO_LARGE)


def test_an_old_inbox_accepts_the_new_block_code_after_opening(tmp_path, clock):
    db = tmp_path / "executor.db"
    before = _increment_1_inbox(db, clock)

    store = InboxStore(db)
    try:
        old_rows = "SELECT * FROM proposals WHERE task_id = 'old'"
        assert store._conn.execute(old_rows).fetchall() == before  # 重建後舊列不變
        _block_new_by_ratio(store, clock)
        assert store._conn.execute(
            "SELECT disposition, block_code FROM proposals WHERE task_id = 'new'").fetchall() == [
            ("blocked", "budget_increase_too_large")]
        assert store._conn.execute(old_rows).fetchall() == before  # 寫入新代碼之後仍不變
    finally:
        store.close()


# ---- S405 ----
@pytest.mark.parametrize(("setups", "new_budget", "expected"), [
    ((_campaign(status="paused"),), 151, BlockCode.CAMPAIGN_NOT_ACTIVE),  # 不在投放 > 比例
    ((_campaign(status="paused", version=4),), 150, BlockCode.CAMPAIGN_NOT_ACTIVE),  # > 版本已變
    ((_campaign(version=4),), 151, BlockCode.VERSION_CHANGED),  # 版本已變 > 比例
    ((_tenant(campaigns=("c2",)),), 151, BlockCode.BUDGET_INCREASE_TOO_LARGE),  # 比例 > 租戶
    ((_tenant(campaigns=("c2",), max_budget=120),), 150, BlockCode.CAMPAIGN_NOT_ALLOWED),
])
def test_the_first_failing_guardrail_wins(h, setups, new_budget, expected):
    h.submit(requested_change={"new_budget": new_budget})
    for setup in setups:
        setup(h)
    result = h.process()
    assert (result.kind, result.block_code) == (Result.BLOCKED, expected)


# ---- S409 ----
def test_an_already_failed_key_is_blocked_as_previously_failed(h):
    first = h.submit()
    h.dsp.answers.append(WriteAnswer(403, "capability_scope_mismatch"))  # 不是版本衝突的失敗
    h.process()
    key = operation_key(first)
    with h.store.transaction() as tx:
        seq = attempt_store.latest(tx, key).seq
        attempt_store.resolve(tx, key, seq, A.FAILED, "operator decided", h.clock())
    h.store.accept(proposal(revision=2, decision_expires_at=LATER), h.clock)
    attempts_before, writes_before = h.attempts(), len(h.dsp.writes)

    assert h.process().kind is Result.IDLE  # 取件時就確認,不交出去
    assert h.proposals()[-1][3:] == ("blocked", "operation_previously_failed")
    assert h.attempts() == attempts_before and len(h.dsp.writes) == writes_before


def test_the_policy_helper_really_makes_an_increase():
    """S402 的前置:替身證據真的讓決策規則出提案,而且是加預算。"""
    made = _policy_proposal(100)
    assert made.requested_change["new_budget"] == 110
    assert replace(made, revision=1).campaign_version_observed == 7
    assert made.decision_expires_at > _NOW - timedelta(seconds=1)
