"""跨元件追蹤檢視(Phase 9 增量 1):[S604]~[S606]、[S608]、[S613]~[S615]、[S619]。

給任務編號,把分析端(含接續任務)、執行端(生命週期事件、嘗試紀錄、呼叫紀錄、死信)與 DSP 操作紀錄
組成一條時間線:依 UTC 時間、來源固定順序、來源內寫入順序排;三個來源各讀一個快照,讀到兩輪相同
才回傳。只讀。
"""

import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from rtb.analyzer.task_store import FollowUp, ReplanReason, TaskReader
from rtb.domain.attempt import operation_key
from rtb.domain.proposal import content_hash
from rtb.domain.task_state import TaskState
from rtb.executor.execution import CampaignView, Result
from rtb.executor.inbox_store import RETENTION, ReadOnlyInbox
from rtb.ops import trace
from rtb.ops.trace import MISSING, KeyOrigin, Origin, Table
from rtb.sqlitekit import DatabaseNotUpgraded
from tests.executor.conftest import NOW
from tests.executor.fakes import proposal


def build(world, task_id="t1", **kwargs):
    return trace.build_trace(task_id, **world.paths(), dsp_timeout_seconds=1.0, **kwargs)


def _utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _sort_key(segment):
    return (_utc(segment.at), trace.ORIGIN_ORDER.index(segment.origin),
            trace.TABLE_ORDER.index(segment.table), segment.order)


# ---- [S604] ----
def test_a_trace_joins_the_analyzer_the_executor_and_the_dsp_in_time_order(world):
    seq = world.analyze()
    prop, result = world.execute()
    assert result.kind is Result.EXECUTED
    key = operation_key(prop)
    world.dsp.commit(key, (NOW + timedelta(milliseconds=1)).isoformat(), operation_id=42)
    # 接續任務:原任務因版本已變結案、同一個交易開接續任務,接續任務的歷史也串進來
    assert world.analyzer.commit_step("t1", seq, TaskState.BLOCKED, NOW + timedelta(minutes=1),
                                      follow_up=FollowUp(ReplanReason.VERSION_CHANGED))

    result = build(world)
    origins = {segment.origin for segment in result.segments}
    assert origins == set(Origin)
    assert list(result.segments) == sorted(result.segments, key=_sort_key)
    assert len(result.tasks) == 2 and result.tasks[0] == "t1"
    tables = {segment.table for segment in result.segments}
    assert {Table.TASK_HISTORY, Table.TOOL_CALLS, Table.LIFECYCLE_EVENTS, Table.ATTEMPTS,
            Table.DSP_CALLS, Table.DSP_OPERATIONS} <= tables
    assert any(s.table is Table.TASK_HISTORY and s.field("task_id") == result.tasks[1]
               for s in result.segments)
    for segment in result.segments:  # 每一段都帶齊關聯欄位,缺的標明缺
        assert tuple(name for name, _ in segment.fields) == trace.FIELDS
        assert all(value is not None for _, value in segment.fields)
        assert segment.at.endswith("Z")
    op = next(s for s in result.segments if s.table is Table.DSP_OPERATIONS)
    assert (op.field("dsp_operation_id"), op.field("key"), op.field("revision")) == (42, key, 1)
    attempt = next(s for s in result.segments if s.table is Table.ATTEMPTS)
    assert (attempt.field("attempt"), attempt.field("worker"), attempt.field("tenant")) == (
        1, "executor", "t-default")
    history = next(s for s in result.segments if s.table is Table.TASK_HISTORY)
    assert history.field("tenant") is MISSING and history.field("worker") is MISSING
    assert result.stable and dict(result.read_at).keys() == set(Origin)
    json.loads(json.dumps(trace.to_primitives(result)))  # 命令列輸出用的結構化結果


# ---- [S605] ----
def test_the_trace_view_reads_while_the_executor_holds_the_write_lock(world, monkeypatch):
    world.analyze()
    world.execute()
    before = world.dump()
    refused = []

    def spy(opener):
        class Spied(opener):
            def close(self):  # 關掉之前試寫一次:唯讀連線要拒絕
                try:
                    self._conn.execute("CREATE TABLE probe (x)")
                except sqlite3.OperationalError as exc:
                    refused.append((opener.__name__, "readonly" in str(exc)))
                super().close()
        return Spied

    monkeypatch.setattr(trace, "ReadOnlyInbox", spy(trace.ReadOnlyInbox))
    monkeypatch.setattr(trace, "TaskReader", spy(trace.TaskReader))
    with world.h.store.transaction():  # 執行迴圈佔著寫入鎖(BEGIN IMMEDIATE)
        holder = sqlite3.connect(world.analyzer_db, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")  # 分析端也被佔著
        try:
            result = build(world)
        finally:
            holder.execute("ROLLBACK")
            holder.close()
    assert result.stable and any(s.origin is Origin.EXECUTOR for s in result.segments)
    assert world.dump() == before  # 什麼都沒寫
    assert set(refused) == {("ReadOnlyInbox", True), ("TaskReader", True)}  # 用的是唯讀連線
    assert len(refused) == 2 * result.rounds


# ---- [S606] ----
def test_a_trace_marks_the_missing_dsp_segment(world):
    world.analyze()
    world.execute()
    result = trace.build_trace("t1", analyzer_db=world.analyzer_db,
                               executor_db=world.executor_db, dsp_url="http://127.0.0.1:9",
                               dsp_timeout_seconds=0.5)
    assert result.missing == (Origin.DSP,)
    origins = {segment.origin for segment in result.segments}
    assert origins == {Origin.ANALYZER, Origin.EXECUTOR}
    assert trace.to_primitives(result)["missing"] == ["dsp"]


# ---- [S608] ----
def test_trace_order_uses_utc_then_source_then_write_order(world):
    world.analyze(at=NOW)  # 分析端的時間跟執行端同一刻(都是 Z 格式)
    prop, _ = world.execute()
    key = operation_key(prop)
    # DSP 用 +08:00 寫、字串排序會排到最後;實際時間比執行端早一秒
    world.dsp.commit(key, "2026-09-22T20:04:59.000000+08:00")

    result = build(world)
    at = [s.at for s in result.segments]
    assert at == sorted(at, key=_utc)
    op = next(s for s in result.segments if s.table is Table.DSP_OPERATIONS)
    assert op.at == "2026-09-22T12:04:59.000000Z"
    same = [s for s in result.segments if _utc(s.at) == NOW]
    ranks = [(trace.ORIGIN_ORDER.index(s.origin), trace.TABLE_ORDER.index(s.table), s.order)
             for s in same]
    assert ranks == sorted(ranks)
    assert same[0].origin is Origin.ANALYZER  # 同一刻:分析端在前
    events = [s.what for s in same if s.table is Table.LIFECYCLE_EVENTS]
    assert events == ["received", "delivered", "handed_off"]  # 同一刻照寫入順序,不是字母序
    # 排序不能靠讀進來的先後:反過來餵進排序,結果要一模一樣(同一刻由來源、表、寫入順序定)
    backwards = list(result.segments)
    backwards.reverse()
    assert sorted(backwards, key=trace._sort_key) == list(result.segments)


# ---- [S613] ----
def test_a_proposal_blocked_before_any_attempt_is_still_traceable_after_the_purge(world):
    prop, result = world.execute(task_id="gone", campaign_id="c-missing", policy_version="p-9")
    assert result.kind is Result.BLOCKED
    world.clock.advance(seconds=RETENTION.total_seconds() + 60)
    now = world.clock()
    world.h.store.accept(proposal(task_id="other", decision_created_at=now.isoformat(),
                                  decision_expires_at=(now + timedelta(minutes=5)).isoformat()),
                         world.clock)
    assert world.h.query("SELECT count(*) FROM proposals WHERE task_id = 'gone'") == [(0,)]

    result = build(world, "gone")
    blocked = [s for s in result.segments if s.what == "blocked"]
    assert len(blocked) == 1
    assert (blocked[0].field("campaign_id"), blocked[0].field("key"),
            blocked[0].field("policy_version")) == ("c-missing", operation_key(prop), "p-9")
    assert dict(blocked[0].detail)["reason"] == "campaign_not_found"
    assert not any(s.table is Table.ATTEMPTS for s in result.segments)


# ---- [S614] ----
def test_a_shared_operation_key_is_attributed_to_the_revision_that_created_it(world):
    world.analyze(revisions=(1, 2))  # 修訂 1 沒交給執行(只能重算),修訂 2 交給執行時存了鍵
    first, _ = world.execute()
    world.h.submit(revision=2)  # 同一把鍵:取件時依既有結果確認
    with world.h.store.transaction() as tx:
        assert world.h.store.receive(tx, world.clock(), "w2") is None

    result = build(world)
    key = operation_key(first)
    attempts = [s for s in result.segments if s.table is Table.ATTEMPTS]
    assert attempts and {(s.field("task_id"), s.field("revision")) for s in attempts} == {("t1", 1)}
    seqs = [s.field("attempt") for s in attempts]
    assert seqs == sorted(set(seqs))  # 嘗試不重複列
    revisions = {(r.task_id, r.revision): r for r in result.revisions}
    assert revisions["t1", 1].key == revisions["t1", 2].key == key
    assert revisions["t1", 1].key_origin is KeyOrigin.RECOMPUTED
    assert revisions["t1", 2].key_origin is KeyOrigin.ANALYZER_STORED
    assert revisions["t1", 1].attempts_owner == revisions["t1", 2].attempts_owner == (
        "t1", 1, content_hash(first))
    assert (revisions["t1", 1].settled_by_existing_key,
            revisions["t1", 2].settled_by_existing_key) == (False, True)


# ---- [S615] ----
def test_the_trace_rereads_until_stable_and_flags_a_moving_target(world, monkeypatch):
    world.analyze()
    world.execute()
    opened = []
    real = trace._read_executor

    def counting(*args, **kwargs):
        opened.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(trace, "_read_executor", counting)
    calm = build(world)
    assert (calm.stable, calm.rounds, len(opened)) == (True, 2, 2)  # 沒有新提交:第二輪就判穩定
    assert dict(calm.read_at).keys() == set(Origin)

    revision = iter(range(2, 10))

    def moving(*args, **kwargs):
        read = real(*args, **kwargs)
        world.h.submit(revision=next(revision))  # 每讀完一輪,執行端就多一筆提交
        return read

    monkeypatch.setattr(trace, "_read_executor", moving)
    busy = build(world)
    assert (busy.stable, busy.rounds) == (False, 3)
    assert trace.to_primitives(busy)["stable"] is False
    seen = {s.field("revision") for s in busy.segments if s.table is Table.LIFECYCLE_EVENTS}
    assert seen == {1, 2, 3}  # 每一輪都重開新快照:最後一輪看得到前兩輪之後的提交,看不到第三次


# ---- [S619] ----
def test_a_read_only_open_of_an_old_database_fails_cleanly(world):
    world.analyze()
    world.execute()
    world.h.close()
    conn = sqlite3.connect(world.executor_db)
    conn.execute("DROP TABLE lifecycle_events")
    conn.close()
    before = world.dump()

    with pytest.raises(DatabaseNotUpgraded):
        ReadOnlyInbox(world.executor_db)
    err = io.StringIO()
    code = trace.run(["--task-id", "t1", "--analyzer-db", str(world.analyzer_db),
                      "--executor-db", str(world.executor_db), "--dsp-url", world.dsp.url],
                     out=io.StringIO(), err=err)
    assert code == trace.EXIT_NOT_UPGRADED
    assert "請先啟動一次執行迴圈" in err.getvalue()
    assert world.dump() == before  # 不補表、不寫任何東西

    world.analyzer.close()
    conn = sqlite3.connect(world.analyzer_db)
    conn.execute("DROP TABLE follow_ups")
    conn.close()
    before = world.dump()
    with pytest.raises(DatabaseNotUpgraded):
        TaskReader(world.analyzer_db)
    assert world.dump() == before


# ---- [S614] 代碼審第 1 輪:任務與修訂重用、內容不同的兩份提案要各自追得到 ----
def test_a_reused_task_and_revision_with_new_content_is_traced_separately(world):
    first, result = world.execute(task_id="re")
    assert result.kind is Result.EXECUTED
    world.clock.advance(seconds=RETENTION.total_seconds() + 60)
    now = world.clock()
    fresh = {"decision_created_at": now.isoformat(),
             "decision_expires_at": (now + timedelta(minutes=5)).isoformat()}
    world.h.store.accept(proposal(task_id="other", **fresh), world.clock)  # 順手清掉舊的 re
    assert world.h.query("SELECT count(*) FROM proposals WHERE task_id = 're'") == [(0,)]
    world.h.process()  # 先把順手收的那份處理掉
    second, result = world.execute(task_id="re", campaign_id="c2", **fresh)  # 同任務同修訂
    assert result.kind is Result.EXECUTED
    for prop in (first, second):
        world.dsp.commit(operation_key(prop), now.isoformat(), campaign_id=prop.campaign_id)

    traced = build(world, "re")
    revisions = {(r.task_id, r.revision, r.content_hash): r for r in traced.revisions}
    assert set(revisions) == {("re", 1, content_hash(first)), ("re", 1, content_hash(second))}
    for prop in (first, second):
        digest = content_hash(prop)
        mine = revisions["re", 1, digest]
        assert mine.key == operation_key(prop) and not mine.settled_by_existing_key
        assert mine.attempts_owner == ("re", 1, digest)
        for table in (Table.ATTEMPTS, Table.DSP_CALLS, Table.DSP_OPERATIONS):
            owned = [s for s in traced.segments
                     if s.table is table and s.field("key") == operation_key(prop)]
            assert owned, (table, prop.campaign_id)
            assert {s.field("content_hash") for s in owned} == {digest}, table


# ---- [S614] 代碼審第 2 輪:同一把冪等鍵、內容雜湊不同的兩份提案,呼叫紀錄各帶自己的雜湊 ----
def test_calls_for_two_proposals_sharing_a_key_keep_their_own_content_hash(world):
    world.h.dsp.campaigns["c1"] = CampaignView(budget=100, status="paused", version=3)
    first, result = world.execute(task_id="sk")  # 廣告暫停:執行前擋下,只讀過一次 DSP
    assert result.kind is Result.BLOCKED
    world.clock.advance(seconds=RETENTION.total_seconds() + 60)
    now = world.clock()
    fresh = {"decision_created_at": now.isoformat(),
             "decision_expires_at": (now + timedelta(minutes=5)).isoformat()}
    world.h.store.accept(proposal(task_id="other", campaign_id="c3", **fresh), world.clock)
    world.h.process()
    world.h.dsp.campaigns["c1"] = CampaignView(budget=100, status="active", version=3)
    second, result = world.execute(task_id="sk", **fresh)  # 只改決策時間:同一把冪等鍵
    assert result.kind is Result.EXECUTED
    assert operation_key(first) == operation_key(second)
    assert content_hash(first) != content_hash(second)

    rows = world.h.query("SELECT content_hash FROM dsp_calls WHERE task_id = 'sk' ORDER BY id")
    assert [r[0] for r in rows] == [content_hash(first)] + [content_hash(second)] * 3
    calls = [s for s in build(world, "sk").segments if s.table is Table.DSP_CALLS]
    assert [s.field("content_hash") for s in calls] == [r[0] for r in rows]

    # 呼叫紀錄沒記雜湊時標「不明」,不從別處任選一個
    conn = sqlite3.connect(world.executor_db)
    conn.execute("UPDATE dsp_calls SET content_hash = NULL WHERE task_id = 'sk'")
    conn.commit()
    conn.close()
    calls = [s for s in build(world, "sk").segments if s.table is Table.DSP_CALLS]
    assert {s.field("content_hash") for s in calls} == {trace.UNKNOWN}
