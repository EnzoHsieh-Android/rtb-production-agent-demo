"""把系統紀錄觀察成判斷紀錄(Phase 12 設計審 r2 n7):每筆判斷帶來源,白話取自流程圖對應表;
回頭轉換落在展開節點;交叉核對比必經節點的順序,允許的回頭多出現不算對不上(設計審 r3 m6)。"""

from datetime import UTC, datetime, timedelta

import pytest

from rtb.analyzer.policy import NoActionReason
from rtb.analyzer.task_store import ReplanReason
from rtb.demo import flow
from rtb.demo.observe import UNRECOVERABLE, PathBuilder, SourceEvent, missing_from_path
from rtb.domain.attempt import AttemptState, OutcomeCode
from rtb.domain.task_state import TaskState
from rtb.executor.inbox_store import BlockCode, LifecycleKind

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _event(i, primary, detail=None, entity="k1", detail_missing=False):
    return SourceEvent(T0 + timedelta(seconds=i), f"src#{i}", primary, detail, entity,
                       detail_missing)


def test_a_same_key_resend_lands_on_the_unrolled_back_node():
    builder = PathBuilder()
    states = ["IN_FLIGHT", "UNKNOWN", "IN_FLIGHT", "COMMITTED_UNVERIFIED", "VERIFIED"]

    rows = builder.add([_event(i, (AttemptState, s)) for i, s in enumerate(states)])

    assert [r.node for r in rows] == ["x_write", "x_unknown", "x_resend", "x_verify", "x_done"]
    assert rows[2].edge == ("x_unknown", "x_resend")
    assert all(r.origin.startswith("src#") for r in rows)


def test_a_detail_member_gives_the_specific_branch_and_plain_reason():
    rows = PathBuilder().add([_event(0, (LifecycleKind, "BLOCKED"),
                                     (BlockCode, "VERSION_CHANGED"))])

    assert rows[0].node == "x_blocked"
    assert rows[0].edge == ("x_precheck", "x_blocked")
    assert rows[0].reason == flow.OUTCOMES[(BlockCode, "VERSION_CHANGED")].text
    assert rows[0].outcome == "BlockCode.VERSION_CHANGED"


def test_a_no_action_reason_picks_its_branch():
    rows = PathBuilder().add([_event(0, (TaskState, "NO_ACTION"),
                                     (NoActionReason, "NOT_UNDERPACING"), entity="t1")])

    assert rows[0].edge == ("a_pacing", "a_no_action")


def test_a_reason_the_system_did_not_keep_is_shown_as_unrecoverable():
    """[S1048] 系統沒有保存原因的節點:判斷紀錄寫「無法還原」,不留白也不臆測。"""
    rows = PathBuilder().add([_event(0, (TaskState, "NO_ACTION"), None, entity="t1",
                                     detail_missing=True)])

    assert rows[0].node == "a_no_action"
    assert rows[0].reason == UNRECOVERABLE


def test_an_unknown_member_is_shown_as_unrecoverable_not_dropped():
    rows = PathBuilder().add([_event(0, (LifecycleKind, "BLOCKED"), (BlockCode, "made_up"))])

    assert rows[0].node == "x_blocked"
    assert rows[0].reason == UNRECOVERABLE


def test_an_analysis_that_goes_back_for_fresh_evidence_is_unrolled():
    states = ["RECEIVED", "COLLECTING_EVIDENCE", "ANALYZING", "COLLECTING_EVIDENCE"]
    rows = PathBuilder().add([_event(i, (TaskState, s), entity="t1")
                              for i, s in enumerate(states)])

    assert [r.node for r in rows] == ["a_receive", "a_collect", "a_fresh", "a_recollect"]


def test_back_transitions_are_tracked_per_entity():
    """兩把鍵交錯:一把的不明不會讓另一把的送出被當成重送。"""
    rows = PathBuilder().add([_event(0, (AttemptState, "UNKNOWN"), entity="k1"),
                              _event(1, (AttemptState, "IN_FLIGHT"), entity="k2")])

    assert [r.node for r in rows] == ["x_unknown", "x_write"]


@pytest.mark.parametrize(("observed", "missing"), [
    (["x_write", "x_unknown", "x_resend", "x_verify", "x_done"], None),
    (["x_write", "x_unknown", "x_recheck", "x_recheck", "x_resend", "x_verify", "x_done"], None),
    # 第 3 輪代碼審 g1/x1:必經節點照順序逐一消耗,重複出現(多繞一圈不明、重送)就是多出來的
    (["x_write", "x_unknown", "x_resend", "x_unknown", "x_resend", "x_verify", "x_done"],
     "x_unknown"),
    (["x_write", "x_verify", "x_done"], "x_verify"),  # 跳過了不明:在確認那一步對不上
    (["x_write", "x_unknown", "x_done", "x_resend"], "x_done"),
    (["x_done", "x_verify", "x_resend", "x_unknown", "x_write"], "x_done"),  # 都有、順序倒
    # 第 2 輪代碼審 o1/c3:扣掉必經與允許的回頭之後,多出來的節點一律算對不上
    (["x_write", "x_unknown", "x_escalated", "h_resolve", "x_resend", "x_verify", "x_done"],
     "x_escalated"),
])
def test_an_extra_allowed_loop_still_counts_as_done(observed, missing):
    """[S1064] 必經節點要照順序出現;允許的回頭(稍後再查)多出現不算對不上,清單外的節點算。"""
    required = ["x_write", "x_unknown", "x_resend", "x_verify", "x_done"]
    assert missing_from_path(observed, required, allowed={"x_recheck"}) == missing
    assert missing_from_path(required[:3], required) == "x_verify"  # 走到一半停了:缺下一步


def test_a_follow_up_shows_the_new_task_not_a_dead_end(tmp_path):
    """擋下後開新工作:觀察到的是「開一件新工作」那條邊,不是「被擋下,這件工作結束」。"""
    from rtb.analyzer.task_store import FollowUp, ReplanReason, TaskStore
    from rtb.demo.observe import Observer

    store = TaskStore(tmp_path / "analyzer.db")
    try:
        now = datetime.now(UTC)
        store.create_task("t1", "c1", now)
        for state in (TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING):
            store.commit_step("t1", store.latest("t1").seq, state, now)
        from tests.analyzer.conftest import make_proposal
        store.commit_step("t1", store.latest("t1").seq, TaskState.PROPOSED, now,
                          proposal=make_proposal(task_id="t1", campaign_id="c1"))
        store.commit_step("t1", store.latest("t1").seq, TaskState.HANDED_OFF, now,
                          proposal=make_proposal(task_id="t1", campaign_id="c1"))
        store.commit_step("t1", store.latest("t1").seq, TaskState.BLOCKED, now,
                          error_detail="blocked=version_changed",
                          follow_up=FollowUp(ReplanReason.VERSION_CHANGED))
    finally:
        store.close()

    rows = PathBuilder().add(Observer(tmp_path / "analyzer.db", tmp_path / "none.db").poll())

    nodes = [r.node for r in rows]
    assert "a_followup" in nodes and "a_blocked_end" not in nodes
    follow = next(r for r in rows if r.node == "a_followup")
    assert follow.edge == ("x_blocked", "a_followup")
    assert follow.origin.startswith("analyzer.follow_ups#")


# ---- 第 2 輪代碼審 n1/o4/f5/a1 ----
def _attempt(i, state, code=None, key="k1", origin_id=None):
    return SourceEvent(T0 + timedelta(seconds=i), f"inbox.attempts#{key}/{i}",
                       (AttemptState, state), None if code is None else (OutcomeCode, code),
                       f"key:{key}", order=("inbox", 1, origin_id or i), key=key, task="t1")


def _lifecycle(i, kind, reason=None, key="k1", origin_id=None):
    detail = None if reason is None else (BlockCode, reason)
    return SourceEvent(T0 + timedelta(seconds=i), f"inbox.lifecycle_events#{origin_id or i}",
                       (LifecycleKind, kind), detail, "proposal:t1/1",
                       order=("inbox", 2 if kind in {"BLOCKED", "HANDED_OFF"} else 0,
                              origin_id or i), key=key, task="t1")


def _analyzer_event(i, primary, entity="task:t1"):
    return SourceEvent(T0 + timedelta(seconds=i), f"analyzer.x#{i}", primary, None, entity,
                       order=("analyzer", 0, i), task="t1")


@pytest.mark.parametrize(("code", "block", "ending", "node"), [
    ("VERSION_CONFLICT", "VERSION_CHANGED", (ReplanReason, "VERSION_CHANGED"), "a_followup"),
    ("VALIDATION_REJECTED", "OPERATION_PREVIOUSLY_FAILED", (TaskState, "BLOCKED"),
     "a_blocked_end"),
])
def test_a_rejected_write_goes_from_the_failure_to_the_analyzer_not_through_the_precheck(
        code, block, ending, node):
    """[n1] 執行端寫了、平台拒絕:同一個交易寫嘗試失敗與生命週期擋下。判斷紀錄走「寫入失敗 → 開新
    工作/結束」,不套「寫入前再確認就擋下」那條邊。"""
    rows = PathBuilder().add([_attempt(0, "IN_FLIGHT"), _attempt(1, "FAILED", code),
                              _lifecycle(1, "BLOCKED", block, origin_id=5),
                              _analyzer_event(2, ending)])

    assert [(r.node, r.edge) for r in rows] == [
        ("x_write", ("x_total", "x_write")), ("x_failed", ("p_reply", "x_failed")),
        (node, ("x_failed", node))]


def test_same_time_rows_keep_their_write_order_by_number():
    """[o4] 同一個時間的兩列照列號數字排(第 9 列在第 10 列之前),不照來源字串排。"""
    ninth = SourceEvent(T0, "inbox.attempts#k1/9", (AttemptState, "UNKNOWN"), None, "key:k1",
                        order=("inbox", 1, 9), key="k1")
    tenth = SourceEvent(T0, "inbox.attempts#k1/10", (AttemptState, "IN_FLIGHT"), None, "key:k1",
                        order=("inbox", 1, 10), key="k1")

    rows = PathBuilder().add([tenth, ninth])

    assert [r.node for r in rows] == ["x_unknown", "x_resend"]


def test_a_reclaim_explains_the_unknown_that_follows_it():
    """[o4] 重啟後接手(生命週期「被接手」)與嘗試轉成不知道有沒有寫進去同一個交易:先畫中斷換人接手
    (寫入 → 中途中斷),再畫不知道有沒有寫進去;不套「平台沒有明確回覆」那條邊。"""
    rows = PathBuilder().add([_attempt(0, "IN_FLIGHT"), _attempt(5, "UNKNOWN", origin_id=2),
                              _lifecycle(5, "RECLAIMED", origin_id=3)])

    assert [(r.node, r.edge) for r in rows] == [
        ("x_write", ("x_total", "x_write")), ("x_reclaimed", ("x_write", "x_reclaimed")),
        ("x_unknown", None)]
    assert rows[2].reason != flow.OUTCOMES[(AttemptState, "UNKNOWN")].text


def test_events_are_held_back_briefly_so_a_late_earlier_one_is_still_in_order():
    """[o4] 跨輪排序:各資料庫提交的先後不等於時間先後,晚一輪才讀到的較早事件照樣排在前面。"""
    from rtb.demo.observe import Timeline

    timeline = Timeline(hold_seconds=1.0)
    later = _attempt(10, "IN_FLIGHT")
    earlier = _analyzer_event(9, (TaskState, "HANDED_OFF"))

    assert timeline.push([later], now=T0 + timedelta(seconds=10.5)) == []
    released = timeline.push([earlier], now=T0 + timedelta(seconds=11.5))
    assert [e.origin for e in released] == [earlier.origin, later.origin]
    assert timeline.flush() == []


def test_the_executor_clock_offset_is_taken_off_what_it_wrote(tmp_path):
    """[o4] 重啟時執行端帶時鐘偏移:它寫的列顯示時扣掉偏移,不會看起來像等了 5 分鐘。"""
    from rtb.demo.observe import Observer
    from tests.ops.rows import Rows

    built = Rows(tmp_path)
    try:
        built.event(T0 + timedelta(seconds=300), "t1", "reclaimed")  # 執行迴圈寫的,帶偏移
        built.event(T0 + timedelta(seconds=1), "t1", "received", source="inbox", actor=None)
    finally:
        built.close()
    observer = Observer(tmp_path / "analyzer.db", tmp_path / "executor.db")
    observer.executor_offset = timedelta(seconds=300)

    at = {e.primary[1]: e.at for e in observer.poll()}

    assert at == {"RECLAIMED": T0, "RECEIVED": T0 + timedelta(seconds=1)}


def test_a_follow_up_that_ran_out_of_generations_ends_the_work(tmp_path):
    """[f5] 接續關係記的是代數用完(沒有開新工作):判斷紀錄記「被擋下,結束」並寫原因,不記開新工作。"""
    from rtb.demo.observe import GENERATIONS_USED_UP, Observer
    from tests.ops.rows import Rows, iso

    built = Rows(tmp_path)
    try:
        built.task("fu-x", T0)
        built.analyzer.execute("INSERT INTO tasks (task_id, seq, state, campaign_id, written_at) "
                               "VALUES ('fu-x', 2, 'blocked', 'c1', ?)", (iso(T0),))
        built.analyzer.execute(
            "INSERT INTO follow_ups (original_task_id, follow_up_task_id, generation, "
            "campaign_id, reason, outcome, written_at) VALUES ('fu-x', NULL, 4, 'c1', "
            "'version_changed', 'limit_reached', ?)", (iso(T0),))
    finally:
        built.close()

    rows = PathBuilder().add(Observer(tmp_path / "analyzer.db", tmp_path / "executor.db").poll())

    assert [r.node for r in rows] == ["a_receive", "a_blocked_end"]
    assert rows[-1].reason == GENERATIONS_USED_UP


def test_the_observer_reads_only_through_the_read_only_exits():
    """[a1] 觀察器經分析端與收件口的唯讀開法讀,不自己開連線、不對別人的表下查詢。"""
    import ast
    from pathlib import Path

    from rtb.demo import observe

    tree = ast.parse(Path(observe.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"sqlite3", "rtb.sqlitekit"}
    assert "TaskReader" in observe.__dict__ and "ReadOnlyInbox" in observe.__dict__


def test_a_close_event_written_with_an_attempt_result_comes_after_it(tmp_path):
    """[o4/n1] 收件口的結案事件與嘗試結果同一個交易、同一個時間:結案排在嘗試之後(寫入失敗先記下,
    收件口的擋下才認得出是「寫了、平台拒絕」)。"""
    from rtb.demo.observe import Observer
    from tests.ops.rows import Rows

    built = Rows(tmp_path)
    try:
        built.event(T0, "t1", "delivered", key="k1")
        built.attempt("k1", 1, "in_flight", T0 + timedelta(seconds=1), task="t1")
        built.event(T0 + timedelta(seconds=2), "t1", "blocked", key="k1", reason="version_changed")
        built.attempt("k1", 2, "failed", T0 + timedelta(seconds=2), code="version_conflict")
    finally:
        built.close()

    rows = PathBuilder().add(Observer(tmp_path / "analyzer.db", tmp_path / "executor.db").poll())

    assert [r.node for r in rows] == ["x_pick", "x_write", "x_failed"]


def test_a_verification_timeout_on_an_unknown_write_is_a_recheck_not_a_second_unknown():
    """[g1] 查證逾時在嘗試紀錄記一列同樣的「不明」:那是「稍後再查」,不是又進一次不知道有沒有寫進去
    (必經節點逐一消耗之後,畫成第二次不明會把正常的系統判成沒跑完)。"""
    rows = PathBuilder().add([_attempt(0, "IN_FLIGHT"), _attempt(1, "UNKNOWN"),
                              _attempt(2, "UNKNOWN"), _attempt(3, "IN_FLIGHT")])

    assert [(r.node, r.edge) for r in rows][1:] == [
        ("x_unknown", ("p_reply", "x_unknown")), ("x_recheck", ("x_unknown", "x_recheck")),
        ("x_resend", None)]


def test_each_row_is_kept_with_its_task_and_stream():
    """[g1] 多件工作的情境要按工作、按來源分開核對:每一筆判斷記下屬於哪件工作、哪一條紀錄。"""
    builder = PathBuilder()
    builder.add([_analyzer_event(0, (TaskState, "RECEIVED")), _lifecycle(1, "RECEIVED"),
                 _attempt(2, "IN_FLIGHT")])

    assert builder.streams == [("t1", "task", "a_receive"), ("t1", "proposal", "x_pending"),
                               ("t1", "key", "x_write")]


def test_a_crash_right_after_pickup_is_not_drawn_as_an_interrupted_write():
    """[第 3 輪代碼審 g2] F3 剛拿起就猝死、重啟後被接手:上一步是「拿起」,圖上沒有「拿起 → 中途中斷」
    這條邊,邊留空;不退回唯一的進入邊畫成「寫入 → 中途中斷」(那時根本還沒寫)。"""
    rows = PathBuilder().add([_lifecycle(0, "RECEIVED"), _lifecycle(1, "DELIVERED"),
                              _lifecycle(2, "RECLAIMED")])

    assert rows[-1].node == "x_reclaimed" and rows[-1].edge is None


def test_the_previous_step_is_kept_per_task():
    """[g2] 上一個節點按工作分開記:另一件工作剛走到的節點,不會被拿來當這件工作的上一步。"""
    other = SourceEvent(T0 + timedelta(seconds=1), "inbox.attempts#k9/1",
                        (AttemptState, "IN_FLIGHT"), None, "key:k9", order=("inbox", 1, 9),
                        key="k9", task="t9")
    rows = PathBuilder().add([_attempt(0, "IN_FLIGHT"), other, _attempt(2, "UNKNOWN")])

    assert rows[-1].edge == ("p_reply", "x_unknown")  # 從這件工作自己的寫入經平台回覆
    assert PathBuilder().add([other, _lifecycle(3, "RECLAIMED")])[-1].edge is None


def test_cursor_reads_go_on_until_an_empty_page():
    """[第 3 輪代碼審 a1] 游標式讀取讀到空頁才停,不看每頁上限是多少(上限各模組各有一份,這裡不再抄):
    一頁沒滿也照樣再讀一次。"""
    from rtb.demo.observe import all_pages

    pages = {0: [1, 2], 2: [3], 3: []}
    found, after = all_pages(lambda cursor: pages[cursor], 0, lambda row: row)
    assert (found, after) == ([1, 2, 3], 3)
