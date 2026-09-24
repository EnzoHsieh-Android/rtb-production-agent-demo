"""流程層接 AI 那一步(Phase 13 增量 2,計劃〈租約、逾時與停止訊號〉〈調查紀錄與狀態同一交易〉):續
租是新增一列、拿鎖之後才讀時鐘;流程層持有目前收據容器,續租成功當下就換;續租沒成丟 RenewalSkipped
、這一步不寫;調查紀錄與原始資料跟狀態列同一個交易寫([S1110] [S1130] [S1135] [S1149] [S1150] [S11
51] [S1152])。"""

import ast
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from rtb.analyzer import flow
from rtb.analyzer.task_store import (
    LEASE_DURATION,
    InvestigationRecord,
    LeaseReceipt,
    RawQuery,
    TaskStore,
)
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting, make_evidence, make_proposal

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"
LATER = NOW + timedelta(seconds=40)
RECORD = InvestigationRecord("query", 1, "check_daily_trend", "ai", reason="看趨勢",
                             reason_code="ai_query", model_source="recorded")


def _to_analyzing(store, raw=()):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW)
    batch = flow.EvidenceBatch((make_evidence(evidence_id="t1-2-state", task_id="t1"),), raw)
    flow.advance(store, "t1", Counting(returns=batch), Counting(), Counting(), NOW)
    assert store.latest("t1").state is TaskState.ANALYZING


def _leases(store):
    return store._conn.execute(
        "SELECT lease_seq, owner, expires_at FROM task_leases WHERE task_id = 't1' "
        "ORDER BY lease_seq").fetchall()


def _ai(store, judge, clock=lambda: LATER):
    return flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW, owner="w1",
                        ai_decide=judge, clock=clock)


class Judge:
    """假的 AI 決策函式:可以先續租、再「呼叫模型」、再回結果或丟例外。"""

    def __init__(self, outcome=None, *, renew=True, raises=None):
        self.outcome, self.renew, self.raises = outcome, renew, raises
        self.model_calls = 0
        self.contexts = []

    def __call__(self, task, evidence, now, context):
        self.contexts.append(context)
        if self.renew:
            context.renew()
        self.model_calls += 1
        if self.raises is not None:
            raise self.raises
        return self.outcome


def test_the_ai_step_renews_its_lease_before_calling_the_model(store, tmp_path):  # noqa: PLR0915
    """[S1135] 續租:目前最新租約列正是手上收據時,新增一列序號加 1、擁有者相同,回新收據;條件不符
    時什麼都不寫、不呼叫模型、這一步不寫入。"""
    store.create_task("t1", "c1", NOW)
    lease = store.acquire_lease("t1", "w1", NOW)
    renewed = store.renew_lease(lease, lambda: LATER)
    assert renewed == LeaseReceipt("t1", lease.lease_seq + 1, "w1")
    assert _leases(store)[-1][:2] == (renewed.lease_seq, "w1")
    assert store.renew_lease(lease, lambda: LATER) is None  # 舊收據已不是最新那一列
    before = _leases(store)
    assert store.renew_lease(LeaseReceipt("t1", renewed.lease_seq, "w2"), lambda: LATER) is None
    assert _leases(store) == before
    # 流程層:AI 那一步先續租才呼叫模型;續租條件不符就不呼叫、不寫
    other = TaskStore(tmp_path / "flow.db")
    try:
        _to_analyzing(other)
        judge = Judge(flow.AiOutcome(flow.NoAction(), None))
        _ai(other, judge)
        rows = _leases(other)
        assert [r[1] for r in rows[-3:]] == ["w1", "w1", None]  # 取得、續租、放掉
        assert rows[-2][0] == rows[-3][0] + 1
        assert other.latest("t1").state is TaskState.NO_ACTION
        assert judge.model_calls == 1
        # 續租條件不符(被接手):不呼叫模型、這一步不寫
        _to_analyzing(taken := TaskStore(tmp_path / "taken.db"))
        try:
            refused = Judge(flow.AiOutcome(flow.NoAction(), None))

            def rival_first(task, evidence, now, context):
                rival = TaskStore(tmp_path / "taken.db")
                try:
                    rival.acquire_lease("t1", "w2", LATER + 2 * LEASE_DURATION)
                finally:
                    rival.close()
                return refused(task, evidence, now, context)

            assert _ai(taken, rival_first) is TaskState.ANALYZING
            assert refused.model_calls == 0
        finally:
            taken.close()
    finally:
        other.close()


def test_the_renewal_reads_the_clock_after_taking_the_lock(tmp_path):
    """[S1150] 續租在交易裡拿到鎖之後才讀時鐘;新到期時間從那個時間算,不用這一步開頭的時間。"""
    path = tmp_path / "analyzer.db"
    store = TaskStore(path)
    try:
        store.create_task("t1", "c1", NOW)
        lease = store.acquire_lease("t1", "w1", NOW)
        seen = []

        def clock():
            probe = sqlite3.connect(path, timeout=0)
            try:
                probe.execute("BEGIN IMMEDIATE")
                seen.append("lock_free")
                probe.execute("ROLLBACK")
            except sqlite3.OperationalError:
                seen.append("lock_held")
            finally:
                probe.close()
            return LATER

        renewed = store.renew_lease(lease, clock)
        assert seen == ["lock_held"]
        expires = _leases(store)[-1][2]
        assert renewed is not None
        assert expires.startswith((LATER + LEASE_DURATION).strftime("%Y-%m-%dT%H:%M:%S"))
    finally:
        store.close()


def test_the_flow_commits_and_releases_with_the_renewed_receipt(store, tmp_path):
    """[S1149] 續租成功當下換掉容器:續租後丟一般例外時 FAILED 寫得進去;續租後丟停止訊號這類例外
    時,放掉的是新收據;正常結果也用新收據提交。"""
    _to_analyzing(store)
    assert _ai(store, Judge(raises=RuntimeError("收據格式化出錯"))) is TaskState.FAILED
    assert store.latest("t1").state is TaskState.FAILED
    rows = _leases(store)
    assert rows[-1][1] is None and rows[-1][0] == rows[-2][0] + 1  # 提交時放掉的是續租那一列

    class Stop(BaseException):
        pass

    second = TaskStore(tmp_path / "second.db")
    try:
        _to_analyzing(second)
        with pytest.raises(Stop):
            _ai(second, Judge(raises=Stop()))
        rows = _leases(second)
        assert [r[1] for r in rows[-3:]] == ["w1", "w1", None]
        assert rows[-1][0] == rows[-2][0] + 1  # 放掉的是新收據那一列,不是舊的
        assert second.latest("t1").state is TaskState.ANALYZING  # 這一步沒寫
        assert second.acquire_lease("t1", "w9", NOW) is not None  # 真的放掉了,別人拿得到
    finally:
        second.close()


def test_a_busy_renewal_skips_the_step_without_failing_the_task(store, monkeypatch):
    """[S1152] 續租條件不符或等鎖逾時:續租回呼丟 RenewalSkipped,流程層在通用例外之前接住,這一步
    不寫、放掉租約、不呼叫模型,工作不轉失敗。"""
    _to_analyzing(store)
    monkeypatch.setattr(store, "renew_lease", lambda _lease, _clock: None)
    judge = Judge(flow.AiOutcome(flow.NoAction(), None))
    assert _ai(store, judge) is TaskState.ANALYZING
    assert judge.model_calls == 0
    assert store.latest("t1").state is TaskState.ANALYZING
    assert _leases(store)[-1][1] is None  # 放掉了
    assert store.investigation_rounds("t1") == ()


def test_every_model_round_is_committed_with_its_step(store, tmp_path):
    """[S1110] 回蒐集證據、已提案、不提案、退回規則四種轉換,調查紀錄都跟狀態列同一個交易、同序號
    寫;提交輸給接手者時連紀錄一起沒寫。"""
    cases = [
        (flow.AiOutcome(flow.QueryMore(), RECORD), TaskState.COLLECTING_EVIDENCE),
        (flow.AiOutcome(flow.ProposalDecision(make_proposal(task_id="t1", campaign_id="c1")),
                        InvestigationRecord("conclusion", 2, "propose", "ai")),
         TaskState.PROPOSED),
        (flow.AiOutcome(flow.NoAction(), InvestigationRecord("conclusion", 1, "do_not_propose",
                                                             "ai")), TaskState.NO_ACTION),
        (flow.AiOutcome(flow.NoAction(), InvestigationRecord(
            "fallback", 1, "no_action", "rule", fallback="off_menu")), TaskState.NO_ACTION),
    ]
    for index, (outcome, expected) in enumerate(cases):
        db = TaskStore(tmp_path / f"case{index}.db")
        try:
            _to_analyzing(db)
            assert _ai(db, Judge(outcome)) is expected
            latest = db.latest("t1")
            assert db.investigation_rounds("t1") == ((latest.seq, outcome.record),)
            if expected is TaskState.COLLECTING_EVIDENCE:
                assert db.investigation_reason_code("t1", latest.seq) == "ai_query"
        finally:
            db.close()
    # 提交輸給接手者:續租之後、提交之前被別人接手(租約到期被拿走),紀錄與狀態列都沒寫
    _to_analyzing(store)

    def taken_over(_task, _evidence, _now, context):
        context.renew()
        rival = TaskStore(tmp_path / "analyzer.db")
        try:
            assert rival.acquire_lease("t1", "w2", LATER + 2 * LEASE_DURATION) is not None
        finally:
            rival.close()
        return flow.AiOutcome(flow.QueryMore(), RECORD)

    assert _ai(store, taken_over) is TaskState.ANALYZING
    assert store.investigation_rounds("t1") == ()


def test_raw_query_rows_are_committed_with_the_evidence_step(store, tmp_path):
    """[S1151] 蒐證那一步的原始資料列跟證據同一個交易寫,序號等於新狀態列;提交沒寫入時連原始資料
    一起沒寫;重做同一步不撞主鍵、不留兩列。"""
    raw = (RawQuery("check_daily_trend", '{"rows":[]}'),)
    _to_analyzing(store, raw)
    analyzing = store.latest("t1")
    assert store.raw_query("t1", analyzing.seq, "check_daily_trend") == '{"rows":[]}'
    # 輸掉提交(別人剛推進過):原始資料也沒寫
    lost = TaskStore(tmp_path / "lost.db")
    try:
        lost.create_task("t1", "c1", NOW)
        flow.advance(lost, "t1", Counting(), Counting(), Counting(), NOW)
        collecting = lost.latest("t1")

        def racing(_task, _now):
            rival = TaskStore(tmp_path / "lost.db")
            try:
                assert rival.commit_step("t1", collecting.seq, TaskState.ANALYZING, NOW)
            finally:
                rival.close()
            return flow.EvidenceBatch((make_evidence(evidence_id="x", task_id="t1"),), raw)

        flow.advance(lost, "t1", racing, Counting(), Counting(), NOW)
        count = lost._conn.execute("SELECT count(*) FROM investigation_raw").fetchone()[0]
        assert count == 0
        # 重做:再蒐證一次(回到蒐集證據之後)不撞主鍵,每一輪各一列
        flow.advance(lost, "t1", Counting(), Counting(), Counting(), NOW, owner="w1",
                     ai_decide=Judge(flow.AiOutcome(flow.QueryMore(), RECORD)), clock=lambda: LATER)
        flow.advance(lost, "t1", Counting(returns=flow.EvidenceBatch((), raw)), Counting(),
                     Counting(), NOW)
        rows = lost._conn.execute("SELECT seq FROM investigation_raw").fetchall()
        assert rows == [(lost.latest("t1").seq,)]
    finally:
        lost.close()


def test_every_query_result_is_archived_under_its_option_code(store):
    """[S1130] 原始回應只增不改地存進調查原始資料表,用(任務、那一輪分析列序號、選項代碼)取回的跟
    寫入的正規化 JSON 逐位元組相同;沒有結果的那一輪取不到;現行決策函式、AI 決策函式與提示不讀這
    張表。"""
    text = '{"campaign_id":"c1","rows":[{"days_ago":1,"no_data":true}],"x":"\\u00e9"}'
    _to_analyzing(store, (RawQuery("check_daily_trend", text),))
    seq = store.latest("t1").seq
    assert store.raw_query("t1", seq, "check_daily_trend") == text
    assert store.raw_query("t1", seq, "check_past_adjustments") is None  # 那一輪沒有結果
    assert store.raw_query("t1", seq - 1, "check_daily_trend") is None
    source = (SRC / "analyzer" / "task_store.py").read_text(encoding="utf-8")
    assert "UPDATE investigation" not in source and "DELETE FROM investigation" not in source
    for module in ("policy", "ai_judge", "investigation", "flow"):
        tree = ast.parse((SRC / "analyzer" / f"{module}.py").read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str)}
        assert not {"raw_query", "investigation_raw"} & names, module
