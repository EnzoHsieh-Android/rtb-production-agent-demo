"""分析側任務租約:對應 Phase 4 計劃增量 3b 的 S150~S160。

同一個任務被同時推進時,只有持有租約的一方會呼叫外部介面(蒐證、分析、送件);寫入時再核對
租約還是自己的。互斥類測試都讓先拿到租約的一方停在外部呼叫裡面,等另一方真的試過才放行,
不然先拿到的一路做完,另一方之後合法進來,測不到互斥。
"""

import sqlite3
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from rtb.analyzer import flow
from rtb.analyzer import task_store as task_store_module
from rtb.analyzer.task_store import LEASE_DURATION, TaskStore, ToolEndpoint
from rtb.domain.task_state import TaskState
from rtb.sqlitekit import DatabaseBusy
from tests.analyzer.conftest import NOW, Counting, make_evidence, make_proposal

SRC = str(Path(__file__).resolve().parents[2] / "src")
AFTER_EXPIRY = NOW + LEASE_DURATION + timedelta(seconds=1)
WAIT = 5  # 秒;每一個等待都有上限,卡住時測試會紅而不是掛住


def _to_analyzing(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW)
    flow.advance(store, "t1", Counting(returns=(make_evidence(),)), Counting(), Counting(), NOW)
    assert store.latest("t1").state is TaskState.ANALYZING


def _to_proposed(store):
    _to_analyzing(store)
    decide = Counting(returns=flow.ProposalDecision(make_proposal()))
    flow.advance(store, "t1", Counting(), decide, Counting(), NOW)
    assert store.latest("t1").state is TaskState.PROPOSED


class _Blocking:
    """被呼叫時記一筆、通知「已進來」,然後等放行才回傳;模擬一次卡住的付費外部呼叫。"""

    def __init__(self, returns=None, raises=None):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self._returns, self._raises = returns, raises

    def __call__(self, *args):
        self.calls.append(args)
        self.entered.set()
        assert self.release.wait(WAIT), "測試沒有在時限內放行"
        if self._raises is not None:
            raise self._raises
        return self._returns


def _run_in_thread(path, target):
    """每條執行緒自己開連線(SQLite 連線不能跨執行緒),結果或例外放進回傳的字典。"""
    outcome = {}

    def run():
        thread_store = TaskStore(path)
        try:
            outcome["result"] = target(thread_store)
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            thread_store.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


# ---- S150 ----
def test_two_real_threads_advancing_the_same_task_pay_for_the_analysis_once(tmp_path):
    path = tmp_path / "race.db"
    setup = TaskStore(path)
    _to_analyzing(setup)
    before = len(setup.history("t1"))
    setup.close()

    someone_returned = threading.Event()
    calls = []

    def decide(*args):
        calls.append(args)
        # 持有者停在付費呼叫裡,直到另一條執行緒已經試過並回來,互斥才真的被測到
        assert someone_returned.wait(WAIT), "另一條執行緒沒有在時限內回來"
        return flow.NoAction()

    barrier = threading.Barrier(2)

    def advance(thread_store):
        barrier.wait(WAIT)  # 柵欄放在進資料庫交易之前,兩邊一起出發
        try:
            return flow.advance(thread_store, "t1", Counting(), decide, Counting(), NOW)
        finally:
            someone_returned.set()

    runs = [_run_in_thread(path, advance) for _ in range(2)]
    for thread, _ in runs:
        thread.join(WAIT * 2)

    outcomes = [outcome for _, outcome in runs]
    assert all("error" not in outcome for outcome in outcomes), outcomes
    assert len(calls) == 1  # 只有一方花了分析的錢
    final = TaskStore(path)
    assert len(final.history("t1")) == before + 1
    final.close()


# ---- S151 ----
@pytest.mark.parametrize("reach", [None, _to_analyzing, _to_proposed])
def test_a_caller_without_the_lease_calls_no_external_interface(store, reach):
    if reach is None:  # 停在蒐證中
        store.create_task("t1", "c1", NOW)
        flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW)
    else:
        reach(store)
    state = store.latest("t1").state
    rows = len(store.history("t1"))
    assert store.acquire_lease("t1", "someone-else", NOW) is not None

    evidence, decide, submit = Counting(), Counting(), Counting()
    result = flow.advance(store, "t1", evidence, decide, submit, NOW + timedelta(seconds=30))

    assert result is state
    assert (evidence.call_count, decide.call_count, submit.call_count) == (0, 0, 0)
    assert len(store.history("t1")) == rows


# ---- S152 ----
def test_an_expired_holder_cannot_commit_after_a_takeover_even_when_the_sequence_still_matches(
        tmp_path):
    path = tmp_path / "takeover.db"
    setup = TaskStore(path)
    _to_analyzing(setup)
    before = setup.latest("t1")
    setup.close()

    decide_a = _Blocking(returns=flow.ProposalDecision(make_proposal(risk_summary="A")))
    decide_b = _Blocking(returns=flow.ProposalDecision(make_proposal(risk_summary="B")))
    thread_a, outcome_a = _run_in_thread(
        path, lambda s: flow.advance(s, "t1", Counting(), decide_a, Counting(), NOW))
    assert decide_a.entered.wait(WAIT)
    thread_b, outcome_b = _run_in_thread(
        path, lambda s: flow.advance(s, "t1", Counting(), decide_b, Counting(), AFTER_EXPIRY))
    assert decide_b.entered.wait(WAIT)  # B 在 A 過期後接手,也卡在分析裡

    decide_a.release.set()  # A 醒來;此刻序號仍相符,只有租約核對擋得住它
    thread_a.join(WAIT)
    decide_b.release.set()
    thread_b.join(WAIT)

    assert outcome_a == {"result": TaskState.ANALYZING}
    assert outcome_b == {"result": TaskState.PROPOSED}
    check = TaskStore(path)
    history = check.history("t1")
    assert len(history) == before.seq + 1
    assert history[-1].proposal.risk_summary == "B"  # 寫進去的是接手者的結果
    check.close()
    assert len(decide_a.calls) + len(decide_b.calls) == 2  # 照實:接手的情況錢花了兩次


# ---- S153 ----
CHILD = """
import os, sys
from datetime import datetime
from rtb.analyzer import flow
from rtb.analyzer.task_store import TaskStore

db, calls, at = sys.argv[1], sys.argv[2], datetime.fromisoformat(sys.argv[3])

def decide(*_args):
    with open(calls, "a") as fh:
        fh.write("paid\\n")
    os._exit(9)  # 花完錢、提交前猝死:沒有例外、沒有 finally,租約放不掉

def unused(*_args):
    raise AssertionError("不該被呼叫")

flow.advance(TaskStore(db), "t1", unused, decide, unused, at)
"""


def test_a_holder_that_dies_after_paying_is_analysed_again_once_the_lease_expires(tmp_path):
    path, calls = tmp_path / "crash.db", tmp_path / "calls.txt"
    store = TaskStore(path)
    _to_analyzing(store)

    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(path), str(calls), NOW.isoformat()],
        env={"PYTHONPATH": SRC}, capture_output=True, text=True, timeout=30, check=False)

    assert child.returncode == 9, child.stderr  # 真的死在付費呼叫之後
    assert calls.read_text().splitlines() == ["paid"]

    during = Counting(returns=flow.NoAction())
    result = flow.advance(store, "t1", Counting(), during, Counting(), NOW + timedelta(seconds=30))
    assert result is TaskState.ANALYZING
    assert during.call_count == 0  # 租約還沒到期:不再花錢

    after = Counting(returns=flow.NoAction())
    result = flow.advance(store, "t1", Counting(), after, Counting(), AFTER_EXPIRY)
    assert result is TaskState.NO_ACTION
    assert after.call_count == 1  # 照實的限制:到期後接手者再分析一次,共兩次
    store.close()


# ---- S154 ----
@pytest.mark.parametrize("other_decision", [flow.NeedsFreshEvidence(), flow.NoAction()])
def test_a_caller_that_acquires_after_another_commit_returns_without_calling_out(
        store, monkeypatch, other_decision):
    _to_analyzing(store)
    real_acquire = TaskStore.acquire_lease
    state = {"raced": False}

    def acquire_after_someone_else_finishes_a_step(self, task_id, owner, now):
        if not state["raced"]:  # 第 1 步讀完列之後、取得租約之前,另一個呼叫端做完一步
            state["raced"] = True
            other = Counting(returns=other_decision)
            flow.advance(self, task_id, Counting(), other, Counting(), now)
            assert other.call_count == 1
        return real_acquire(self, task_id, owner, now)

    monkeypatch.setattr(TaskStore, "acquire_lease", acquire_after_someone_else_finishes_a_step)
    evidence, decide, submit = Counting(), Counting(), Counting()

    result = flow.advance(store, "t1", evidence, decide, submit, NOW)

    newest = store.latest("t1").state
    assert newest is not TaskState.ANALYZING
    assert result is newest  # 回報重讀到的新狀態,交回呼叫端用新列重來
    assert (evidence.call_count, decide.call_count, submit.call_count) == (0, 0, 0)
    monkeypatch.setattr(TaskStore, "acquire_lease", real_acquire)
    assert store.acquire_lease("t1", "next", NOW) is not None  # 租約已放掉


# ---- S155 ----
def test_no_progress_or_an_in_process_error_releases_the_lease(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW)

    failing = Counting(raises=TimeoutError("DSP 沒回應"))
    assert flow.advance(store, "t1", failing, Counting(), Counting(), NOW) is (
        TaskState.COLLECTING_EVIDENCE)
    receipt = store.acquire_lease("t1", "probe", NOW)  # 沒有進展:不必等到期
    assert receipt is not None
    assert store.release_lease(receipt, NOW)

    def crash():
        raise RuntimeError("提交前當機")

    with pytest.raises(RuntimeError):
        flow.advance(store, "t1", Counting(returns=(make_evidence(),)), Counting(), Counting(),
                     NOW, before_commit=crash)
    assert store.acquire_lease("t1", "probe", NOW) is not None  # 行程內例外:也放掉了


# ---- S156 ----
def test_a_commit_without_a_receipt_cannot_write_over_a_live_holder(store):
    store.create_task("t1", "c1", NOW)
    receipt = store.acquire_lease("t1", "holder", NOW)

    assert not store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, NOW)
    assert store.latest("t1").seq == 1

    assert store.release_lease(receipt, NOW)
    assert store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, NOW)  # 沒人持有:照舊


# ---- S157 ----
OLD_SCHEMA = """
CREATE TABLE tasks (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, state TEXT NOT NULL,
    campaign_id TEXT NOT NULL, proposal_json TEXT, error_detail TEXT,
    written_at TEXT NOT NULL, PRIMARY KEY (task_id, seq));
CREATE TABLE evidence (
    task_id TEXT NOT NULL, task_seq INTEGER NOT NULL, evidence_id TEXT NOT NULL,
    kind TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL,
    campaign_version_observed INTEGER, content_hash TEXT NOT NULL, trust_class TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (task_id, task_seq, evidence_id));
CREATE TABLE tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, task_seq INTEGER NOT NULL,
    endpoint TEXT NOT NULL, outcome TEXT NOT NULL, latency_ms REAL NOT NULL, at TEXT NOT NULL);
INSERT INTO tasks VALUES ('t1', 1, 'received', 'c1', NULL, NULL, '2026-09-22T12:00:00.000000Z');
INSERT INTO tool_calls (task_id, task_seq, endpoint, outcome, latency_ms, at)
    VALUES ('t1', 1, 'dsp:state', 'ok', 1.5, '2026-09-22T12:00:00.000000Z');
"""


def test_opening_an_old_task_database_adds_the_lease_table_without_touching_history(tmp_path):
    import inspect

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.close()

    store = TaskStore(path)
    tables = {row[0] for row in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "task_leases" in tables
    assert [(r.seq, r.state) for r in store.history("t1")] == [(1, TaskState.RECEIVED)]
    # 舊列的端點不在列舉裡,讀成其他(Phase 9 增量 2 [S648])
    assert [c.endpoint for c in store.list_tool_calls("t1")] == [ToolEndpoint.OTHER]
    assert store.acquire_lease("t1", "first", NOW) is not None  # 新表真的可用
    store.close()

    source = inspect.getsource(task_store_module).upper()
    assert "UPDATE " not in source and "DELETE " not in source  # 租約表同樣只增不改


# ---- S158 ----
def test_an_expired_holder_that_nobody_took_over_still_commits(store):
    store.create_task("t1", "c1", NOW)
    receipt = store.acquire_lease("t1", "slow", NOW)

    assert store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, AFTER_EXPIRY + timedelta(
        minutes=5), lease=receipt)  # 過期但沒人接手:沒有第二個人花錢,照樣寫得進去
    assert store.latest("t1").seq == 2


# ---- S159 ----
def test_a_late_release_from_a_replaced_holder_writes_nothing(tmp_path):
    path = tmp_path / "late.db"
    setup = TaskStore(path)
    setup.create_task("t1", "c1", NOW)
    flow.advance(setup, "t1", Counting(), Counting(), Counting(), NOW)
    setup.close()

    evidence_a = _Blocking(raises=TimeoutError("DSP 很慢,最後失敗"))
    thread_a, outcome_a = _run_in_thread(
        path, lambda s: flow.advance(s, "t1", evidence_a, Counting(), Counting(), NOW))
    assert evidence_a.entered.wait(WAIT)

    store = TaskStore(path)
    receipt_b = store.acquire_lease("t1", "b", AFTER_EXPIRY)  # B 在 A 過期後接手
    assert receipt_b is not None
    evidence_a.release.set()  # A 的蒐證失敗(沒有進展),走放掉租約
    thread_a.join(WAIT)
    assert outcome_a == {"result": TaskState.COLLECTING_EVIDENCE}

    assert store.acquire_lease("t1", "c", AFTER_EXPIRY) is None  # B 的租約仍有效
    assert store.commit_step("t1", 2, TaskState.ANALYZING, AFTER_EXPIRY, lease=receipt_b)

    crashing = TaskStore(path)
    receipt_c = crashing.acquire_lease("t1", "c", AFTER_EXPIRY)
    receipt_d = crashing.acquire_lease("t1", "d", AFTER_EXPIRY + LEASE_DURATION * 2)
    assert not crashing.release_lease(receipt_c, AFTER_EXPIRY)  # 直接呼叫也一樣不寫
    assert crashing.acquire_lease("t1", "e", AFTER_EXPIRY + LEASE_DURATION * 2) is None
    assert crashing.release_lease(receipt_d, AFTER_EXPIRY)
    crashing.close()
    store.close()


# ---- S160 ----
class _PlannedCrash(Exception):
    pass


def test_a_failed_release_never_masks_the_original_error(store, monkeypatch):
    store.create_task("t1", "c1", NOW)

    def busy(*_args, **_kwargs):
        raise DatabaseBusy("放掉租約時資料庫忙碌")

    def crash():
        raise _PlannedCrash

    monkeypatch.setattr(TaskStore, "release_lease", busy)
    with pytest.raises(_PlannedCrash):
        flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW, before_commit=crash)


# ---- 代碼審第 1 輪折入 ----
def test_acquiring_a_lease_for_an_unknown_task_writes_nothing(store):
    from rtb.analyzer.task_store import TaskNotFound

    with pytest.raises(TaskNotFound):
        store.acquire_lease("ghost", "someone", NOW)
    assert store._conn.execute("SELECT count(*) FROM task_leases").fetchone() == (0,)


def test_the_owner_is_what_the_caller_passes_in_and_only_a_label(store):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW, owner="worker-7")

    owners = {row[0] for row in store._conn.execute(
        "SELECT owner FROM task_leases WHERE owner IS NOT NULL")}
    assert owners == {"worker-7"}  # 比照執行側:擁有者由呼叫端(往後的啟動程式)傳入


def test_callers_sharing_one_owner_are_still_fenced_by_the_lease_sequence(store):
    store.create_task("t1", "c1", NOW)
    stale = store.acquire_lease("t1", "same", NOW)
    fresh = store.acquire_lease("t1", "same", AFTER_EXPIRY)  # 同一個擁有者在過期後重新取得
    assert fresh is not None and fresh.lease_seq == stale.lease_seq + 1

    assert not store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, AFTER_EXPIRY, lease=stale)
    assert not store.release_lease(stale, AFTER_EXPIRY)
    assert store.acquire_lease("t1", "other", AFTER_EXPIRY) is None  # 新租約沒被舊收據放掉
    assert store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, AFTER_EXPIRY, lease=fresh)


def test_a_programming_error_while_releasing_is_not_swallowed(store, monkeypatch):
    store.create_task("t1", "c1", NOW)

    def broken(*_args, **_kwargs):
        raise AttributeError("放掉租約的程式本身壞了")

    def crash():
        raise _PlannedCrash

    monkeypatch.setattr(TaskStore, "release_lease", broken)
    with pytest.raises(AttributeError):  # 只吞資料庫層錯誤,程式錯誤要老實傳出來
        flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW, before_commit=crash)


def test_the_flow_layer_never_names_database_errors():
    import inspect

    source = inspect.getsource(flow)
    assert "sqlite3" not in source and "DatabaseBusy" not in source  # 資料庫錯誤留在任務模組
