"""Phase 14 增量 2b:正式規則的規則輪(A/B/C 分步讀四查詢、九條定案、重進入與政策升版)。

合約 [S1401] [S1402] [S1405] [S1406] [S1413] [S1424],與 [S1107] 改寫的證據參照。一律用真的模擬
DSP(行程內、
固定時鐘注入儲存層)與真的分析端資料庫;每一步用呼叫端給的固定 now 推進(2a 教訓:
固定日期一律注入固定時鐘)。
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from rtb import stepbudget
from rtb.analyzer import dsp_client, flow, instrumented, policy, rule_round, runner
from rtb.analyzer.task_store import RuleEvent, RuleStep, ToolEndpoint
from rtb.domain import proposal as proposal_mod
from rtb.domain.evidence import EvidenceKind
from rtb.domain.task_state import TaskState
from rtb.dsp.seed import DEMO_PROFILE, seed_platform_history
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
BASE_METRICS = {"impressions": 500, "clicks": 12, "conversions": 1, "spend": 2.0, "revenue": 5.0}
QUERY_ENDPOINTS = {ToolEndpoint.DSP_HISTORY, ToolEndpoint.DSP_DAILY, ToolEndpoint.DSP_ADJUSTMENTS}


class Clock:
    """DSP 儲存層的可控時鐘(ISO 字串)。"""

    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at.isoformat()


@pytest.fixture
def world(tmp_path):
    clock = Clock(NOW)
    db = tmp_path / "dsp.db"
    dsp = CampaignStore(db, clock=clock)
    dsp.seed_campaign("c1", budget=2400)
    dsp.seed_metrics("c1", "1h", **BASE_METRICS)
    seed_platform_history(dsp, {"c1": DEMO_PROFILE}, NOW)
    server = DspServer(db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0,
                       store_clock=clock)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield World(f"http://127.0.0.1:{server.server_address[1]}", dsp, clock)
    server.shutdown()
    server.server_close()
    dsp.close()


class World:
    def __init__(self, url, dsp, clock):
        self.url, self.dsp, self.clock = url, dsp, clock
        self.sent = []

    def submit(self, proposal):
        self.sent.append(proposal)
        return flow.Accepted(replayed=False)


def step(store, world, at, task_id="t1", submit=None, source=None):
    """推進一步(DSP 的時鐘跟分析端的 now 一起走)。"""
    world.clock.at = at
    return flow.advance(store, task_id, source or instrumented.rule_source(store, world.url, 3.0),
                        policy.decide, submit or world.submit, at,
                        no_action_reason=runner._no_action_reason, rule_decide=rule_round.decide)


def run_until(store, world, at, stop_states, limit=20, **kwargs):
    for _ in range(limit):
        state = step(store, world, at, **kwargs)
        if state in stop_states:
            return state
    raise AssertionError(f"{limit} 步內沒走到 {stop_states}:{store.latest('t1')}")


def calls(store, task_id="t1"):
    return store.list_tool_calls(task_id)


def reads_by_seq(store, task_id="t1"):
    found = {}
    for call in calls(store, task_id):
        found.setdefault(call.task_seq, []).append(call.endpoint)
    return found


def events(store, task_id="t1"):
    return [(event.event, event.detail) for _seq, event in store.rule_events(task_id)]


# ---- 正常路徑:A→B→C→DECIDE,讀取次數與證據參照 ----
def test_underpacing_campaign_reads_three_steps_and_proposes_with_query_receipts(store, world):
    store.create_task("t1", "c1", NOW)
    assert run_until(store, world, NOW, {TaskState.PROPOSED}) is TaskState.PROPOSED
    steps = [(record.round_id, record.step) for _seq, record in store.rule_steps("t1")]
    assert steps == [(1, "A"), (1, "B"), (1, "C")]
    # 宣告的讀取次數 = 實際打 DSP 的次數(2a 教訓;租約守衛用宣告值)
    per_step = {record.step: len(reads_by_seq(store).get(seq - 1, []))
                for seq, record in store.rule_steps("t1")}
    assert per_step == stepbudget.RULE_STEP_READS == {"A": 2, "B": 2, "C": 5}
    assert rule_round.step_reads_match_budget()
    assert [e for e, _d in events(store)] == ["continue", "continue", "decided"]
    proposal = store.latest("t1").proposal
    # 證據編號帶的是蒐證那一列(分析中那一列的前一列)的序號
    # [S1107] 改寫:證據參照 = C 的基本三筆 + 四種成功查詢的收據(B 的歷史與過去調整、C 的長窗與逐日)
    seq_b = next(seq for seq, r in store.rule_steps("t1") if r.step == "B")
    seq_c = next(seq for seq, r in store.rule_steps("t1") if r.step == "C")
    assert set(proposal.evidence_refs) == {
        f"t1-{seq_c - 1}-state", f"t1-{seq_c - 1}-metrics", f"t1-{seq_c - 1}-text",
        f"t1-{seq_b - 1}-check_change_history", f"t1-{seq_b - 1}-check_past_adjustments",
        f"t1-{seq_c - 1}-check_longer_window", f"t1-{seq_c - 1}-check_daily_trend"}
    assert proposal.policy_version == proposal_mod.POLICY_VERSION
    assert world.sent == []  # 只建提案;送件是下一步


# ---- [S1401] ----
@pytest.mark.parametrize(("status", "metrics", "reason"), [
    ("paused", {**BASE_METRICS, "clicks": 2}, "judged_not_worth"),
    ("active", {**BASE_METRICS, "clicks": 600}, "judged_insufficient"),  # 點擊多於曝光
    ("active", {**BASE_METRICS, "spend": 99.0}, "not_underpacing"),  # 資料齊、配速正常
])
def test_paused_and_anomalous_campaigns_finish_from_base_evidence(store, world, status, metrics,
                                                                  reason):
    """暫停、1 小時異常只用基本資料在步驟 A 依第 1/2 條結案;配速正常也在 A 結案;追加讀取 0 次、
    不提案。"""
    world.dsp._conn.execute("UPDATE campaigns SET status = ? WHERE id = 'c1'", (status,))
    world.dsp.seed_metrics("c1", "1h", **metrics)
    store.create_task("t1", "c1", NOW)
    assert run_until(store, world, NOW, {TaskState.NO_ACTION}) is TaskState.NO_ACTION
    assert store.no_action_reason("t1", store.latest("t1").seq) == reason
    assert not {c.endpoint for c in calls(store)} & QUERY_ENDPOINTS
    assert len(calls(store)) == 2
    assert [r.step for _s, r in store.rule_steps("t1")] == ["A"]


# ---- [S1402] ----
@pytest.mark.parametrize(("option", "why"), [
    ("check_change_history", "not_found"), ("check_past_adjustments", "timeout"),
    ("check_daily_trend", "invalid"), ("check_longer_window", "invalid"),
])
def test_missing_query_results_prevent_a_proposal(store, world, monkeypatch, option, why):
    """任一查詢逾時、404、欄位不合格或跨窗不一致成為「沒有結果」:證據不足、提案 0 筆;
    細因記查詢代碼。
    例:歷史 404、1 小時有轉換 → 證據不足。"""
    real = dsp_client.make_query_reader

    def broken(*args, **kwargs):
        reader = real(*args, **kwargs)

        def read(task, chosen):
            return dsp_client.QueryRead(None, why) if chosen == option else reader(task, chosen)

        return read

    monkeypatch.setattr(dsp_client, "make_query_reader", broken)
    store.create_task("t1", "c1", NOW)
    assert run_until(store, world, NOW, {TaskState.NO_ACTION}) is TaskState.NO_ACTION
    assert store.no_action_reason("t1", store.latest("t1").seq) == "judged_insufficient"
    assert events(store)[-1] == ("decided", f"query_no_result:{option}")
    assert world.sent == []


# ---- [S1405] ----
def test_rule_collection_steps_fit_the_lease(store, world):
    """預設逾時 3 秒時各步 2/2/5 讀、最壞 26/26/50 秒皆小於租約 60 秒;自訂逾時讓 C 達 60
    秒就拒啟動並說
    是哪一步。只讓租約與序號持有者提交(別人持有租約時這一步不讀、不寫)。"""
    assert stepbudget.rule_step_worst_seconds(3.0) == {"A": 26.0, "B": 26.0, "C": 50.0}
    ok = runner._unsafe(runner._parse(["--db", "x", "--dsp-url", "u", "--inbox-url", "u"]))
    assert ok is None
    refused = runner._unsafe(runner._parse(["--db", "x", "--dsp-url", "u", "--inbox-url", "u",
                                            "--timeout-seconds", "5"]))
    assert refused is not None and "步驟 C" in refused and "5 次讀取" in refused
    assert runner.run(["--db", str(store_path(store)), "--dsp-url", "u", "--inbox-url", "u",
                       "--timeout-seconds", "5"], max_rounds=0) == runner.EXIT_UNSAFE_CONFIG
    # 租約:別人持有時什麼都不讀
    store.create_task("t1", "c1", NOW)
    step(store, world, NOW)  # 收到 → 蒐集證據
    assert store.acquire_lease("t1", "someone-else", NOW) is not None
    assert step(store, world, NOW) is TaskState.COLLECTING_EVIDENCE
    assert calls(store) == ()


def store_path(store):
    return store._conn.execute("PRAGMA database_list").fetchone()[2]


# ---- [S1406] ----
def test_decisions_recheck_freshness_and_utc_day_boundaries(store, world):
    """C 在 23:59:59 UTC 讀逐日、定案在次日 00:00:01 → 證據不足(不用前一天的日桶提案);A 讀完超過
    15 分鐘才定案 → 作廢這一輪從 A 重讀。"""
    late = datetime(2026, 9, 26, 23, 59, 59, tzinfo=UTC)
    store.create_task("t1", "c1", late)
    for _ in range(5):  # 收到、A、分析 A、B、分析 B
        step(store, world, late)
    assert step(store, world, late) is TaskState.ANALYZING  # C 在 23:59:59 讀
    assert [r.step for _s, r in store.rule_steps("t1")] == ["A", "B", "C"]
    after_midnight = datetime(2026, 9, 27, 0, 0, 1, tzinfo=UTC)
    assert step(store, world, after_midnight) is TaskState.NO_ACTION
    assert events(store)[-1] == ("decided", "day_boundary:check_daily_trend")
    assert world.sent == []


def test_evidence_that_ages_past_fifteen_minutes_restarts_from_step_a(store, world):
    store.create_task("t1", "c1", NOW)
    for _ in range(4):  # 收到、A、分析 A(續步)、B
        step(store, world, NOW)
    later = NOW + policy.MAX_EVIDENCE_AGE + timedelta(seconds=1)
    assert step(store, world, later) is TaskState.COLLECTING_EVIDENCE  # 分析 B:A 已過期
    assert events(store)[-1] == ("restart_stale", None)
    step(store, world, later)
    assert [(r.round_id, r.step) for _s, r in store.rule_steps("t1")][-1] == (2, "A")


# ---- [S1413] ----
def test_rule_round_checkpoints_exclude_stale_evidence(store, world):
    """409 退回後整組從 A 重來,新輪定案不讀舊輪 B 的序號;當機續跑超過 15 分鐘也從 A 重來。"""
    store.create_task("t1", "c1", NOW)
    assert run_until(store, world, NOW, {TaskState.PROPOSED}) is TaskState.PROPOSED
    old_b = next(seq for seq, r in store.rule_steps("t1") if r.step == "B")

    def stale(_proposal):
        raise flow.SubmitStale("revision_out_of_order")

    assert step(store, world, NOW, submit=stale) is TaskState.COLLECTING_EVIDENCE
    assert run_until(store, world, NOW, {TaskState.PROPOSED}) is TaskState.PROPOSED
    rounds = [(r.round_id, r.step, seq) for seq, r in store.rule_steps("t1")]
    assert [(rid, s) for rid, s, _seq in rounds] == [(1, "A"), (1, "B"), (1, "C"),
                                                     (2, "A"), (2, "B"), (2, "C")]
    proposal = store.latest("t1").proposal
    assert not any(ref.startswith(f"t1-{old_b - 1}-") for ref in proposal.evidence_refs)
    # 當機續跑:A 已提交、蒐證 B 時已過 15 分鐘 → 新輪 A
    store.create_task("t2", "c1", NOW)
    for _ in range(3):
        step(store, world, NOW, task_id="t2")
    resumed = NOW + timedelta(minutes=16)
    step(store, world, resumed, task_id="t2")
    assert [(r.round_id, r.step) for _s, r in store.rule_steps("t2")] == [(1, "A"), (2, "A")]


def test_changes_between_a_and_c_restart_twice_then_stop(store, world):
    """A 與 C 的 1 小時原始指標不同:作廢重讀;連續第 3 次變動 → 證據不足結案,沒有第 4 輪。"""
    store.create_task("t1", "c1", NOW)
    bump = iter(range(1, 10))

    def changing(task, now):
        record = rule_round.collect_plan(store.rule_steps(task.task_id),
                                         store.rule_events(task.task_id), now)
        if record[1] is rule_round.Step.C:  # 每次讀 C 之前 1 小時點擊都變
            world.dsp.seed_metrics("c1", "1h", **{**BASE_METRICS, "impressions": 500 + next(bump)})
        return instrumented.rule_source(store, world.url, 3.0)(task, now)

    assert run_until(store, world, NOW, {TaskState.NO_ACTION}, limit=40,
                     source=changing) is TaskState.NO_ACTION
    kinds = [e for e, _d in events(store) if e != "continue"]
    assert kinds == ["restart_changed", "restart_changed", "changed_limit"]
    assert max(r.round_id for _s, r in store.rule_steps("t1")) == 3
    assert store.no_action_reason("t1", store.latest("t1").seq) == "judged_insufficient"


def test_missing_state_at_step_c_does_not_reuse_step_a(store, world):
    """[S1404] C 才讀到缺狀態:以缺現況結案,不沿用 A 的舊現況。"""
    store.create_task("t1", "c1", NOW)
    for _ in range(5):  # 到 B 分析完
        step(store, world, NOW)
    world.dsp._conn.execute("UPDATE campaigns SET status = 'deleted' WHERE id = 'c1'")
    step(store, world, NOW)  # C
    assert step(store, world, NOW) is TaskState.NO_ACTION
    assert store.no_action_reason("t1", store.latest("t1").seq) == "missing_state_or_metrics"


# ---- [S1424] ----
def test_policy_switch_restarts_inflight_analysis(store, world, monkeypatch):
    """升版前只有基本兩讀(分析中那一列沒有規則輪紀錄)→ 開新輪從 A 重讀,不把缺輪次當 DECIDE;回退版
    換新政策版本時,停在九條 B 的輪作廢,從回退版的基本步重讀,不以 B 最後序號定案。"""
    store.create_task("t1", "c1", NOW)
    store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, NOW)
    world.clock.at = NOW
    old = dsp_client.make_client(world.url, 3.0)(store.latest("t1"), NOW)  # 舊版兩讀
    store.commit_step("t1", 2, TaskState.ANALYZING, NOW, evidence=old)  # 舊版兩讀
    assert step(store, world, NOW) is TaskState.COLLECTING_EVIDENCE
    assert events(store) == [("restart_no_round", None)]
    step(store, world, NOW)
    assert [(r.round_id, r.step) for _s, r in store.rule_steps("t1")] == [(1, "A")]
    step(store, world, NOW)
    step(store, world, NOW)  # B
    assert [r.step for _s, r in store.rule_steps("t1")] == ["A", "B"]
    monkeypatch.setattr(rule_round, "POLICY_VERSION", "nine-rules-rollback-v1")
    monkeypatch.setattr(instrumented, "POLICY_VERSION", "nine-rules-rollback-v1")
    assert step(store, world, NOW) is TaskState.COLLECTING_EVIDENCE  # 分析 B:不屬於本版本
    step(store, world, NOW)
    assert [(r.round_id, r.step, r.policy_version) for _s, r in store.rule_steps("t1")][-1] == (
        2, "A", "nine-rules-rollback-v1")


def test_rule_rows_are_only_written_with_analyzing_rows(store):
    store.create_task("t1", "c1", NOW)
    with pytest.raises(ValueError, match="分析中"):
        store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, NOW,
                          rule_step=RuleStep(1, "A", "v", NOW))


def test_base_step_evidence_kinds(store, world):
    store.create_task("t1", "c1", NOW)
    step(store, world, NOW)
    step(store, world, NOW)
    kinds = {e.kind for e in store.evidence_for("t1", store.latest("t1").seq)}
    assert kinds == {EvidenceKind.CAMPAIGN_STATE, EvidenceKind.METRICS, EvidenceKind.CAMPAIGN_TEXT}


# ---- 代碼審 r1 架構對齊-1:進度是純函式,從手造的已提交記錄重算(同 investigation.progress 慣例)----
def _steps(*rows):
    return tuple((seq, RuleStep(round_id, step, version, NOW))
                 for seq, round_id, step, version in rows)


def _events(*rows):
    return tuple((seq, RuleEvent(round_id, event)) for seq, round_id, event in rows)


V = proposal_mod.POLICY_VERSION


def test_progress_is_recomputed_from_plain_records():
    assert rule_round.progress((), ()) == rule_round.Progress(1, False, {}, 0)
    steps = _steps((2, 1, "A", V), (4, 1, "B", V))
    state = rule_round.progress(steps, _events((3, 1, "continue")))
    assert (state.round_id, state.open, state.next_step) == (1, True, rule_round.Step.C)
    closed = rule_round.progress(steps, _events((3, 1, "continue"), (5, 1, "restart_stale")))
    assert (closed.round_id, closed.open, closed.next_step) == (2, False, rule_round.Step.A)
    # 同一步重複只取最新序號
    twice = rule_round.progress(_steps((2, 1, "A", V), (4, 1, "A", V)), ())
    assert twice.steps[rule_round.Step.A][0] == 4
    # 蒐證計畫:先前步驟過期(當機續跑)開新輪 A
    later = NOW + policy.MAX_EVIDENCE_AGE + timedelta(seconds=1)
    ongoing = _events((3, 1, "continue"))
    assert rule_round.collect_plan(steps, ongoing, NOW) == (1, rule_round.Step.C)
    assert rule_round.collect_plan(steps, ongoing, later) == (2, rule_round.Step.A)


def test_change_restarts_do_not_carry_over_a_policy_switch():
    """[S1424] 代碼審 r1 外家 finder-3:舊政策輪的變動重來次數不帶進新政策;
    新政策第一次變動照樣可重讀。"""
    steps = _steps((2, 1, "A", "old-v1"), (4, 1, "B", "old-v1"), (6, 1, "C", "old-v1"),
                   (9, 2, "A", "old-v1"), (11, 2, "B", "old-v1"), (13, 2, "C", "old-v1"),
                   (16, 3, "A", V), (18, 3, "B", V), (20, 3, "C", V))
    events = _events((3, 1, "continue"), (5, 1, "continue"), (7, 1, "restart_changed"),
                     (10, 2, "continue"), (12, 2, "continue"), (14, 2, "restart_changed"),
                     (17, 3, "continue"), (19, 3, "continue"))
    assert rule_round.progress(steps, events).changes == 0
    # 同一政策的連續變動照算
    same = _steps((2, 1, "A", V), (4, 1, "B", V), (6, 1, "C", V), (9, 2, "A", V))
    assert rule_round.progress(same, _events((3, 1, "continue"), (5, 1, "continue"),
                                             (7, 1, "restart_changed"))).changes == 1


def test_step_c_rereads_the_state_after_its_queries(store, world, monkeypatch):
    """代碼審 r1 外家 finder-2:C 步先讀查詢、最後才重讀現況與 1 小時(仍是 5 讀);
    查詢期間另一方改預算,C 的現況就是新版本,A/C 比對抓得到、作廢重讀,不拿舊版本定案。"""
    from rtb.dsp.store import Operation

    real = dsp_client.make_query_reader
    changed = {"done": False}

    def racing(*args, **kwargs):
        reader = real(*args, **kwargs)

        def read(task, option):
            result = reader(task, option)
            if option == "check_daily_trend" and not changed["done"]:
                changed["done"] = True  # 逐日讀完、C 重讀現況之前,另一方改預算
                world.dsp.execute(Operation("c1", "update_budget", {"new_budget": 2500}, 1,
                                            "other-writer-c"))
            return result

        return read

    store.create_task("t1", "c1", NOW)
    for _ in range(5):  # 收到、A、分析 A、B、分析 B
        step(store, world, NOW)
    monkeypatch.setattr(dsp_client, "make_query_reader", racing)
    before = len(calls(store))
    step(store, world, NOW)  # C
    order = [c.endpoint for c in calls(store)[before:]]
    assert len(order) == stepbudget.RULE_STEP_READS["C"] == 5
    assert order[-2:] == [ToolEndpoint.DSP_CAMPAIGN, ToolEndpoint.DSP_METRICS]  # 現況最後讀
    assert step(store, world, NOW) is TaskState.COLLECTING_EVIDENCE
    assert events(store)[-1] == ("restart_changed", None)


# ---- 代碼審 r1 鏡頭2-4:規則步驟表毀損時一律轉 FAILED(不論開不開 AI、分析或蒐證那一步)----
def _corrupt(store):
    store._conn.execute("UPDATE rule_steps SET read_at = 'garbage'")  # 測試直接毀損


def test_a_corrupted_rule_step_row_fails_the_task_instead_of_looping(store, world):
    """(Phase 14 增量 3:原本另跑一次「開 AI」的參數,流程層 AI 分支撤除後只剩規則輪這一種)"""
    store.create_task("t1", "c1", NOW)
    step(store, world, NOW)
    step(store, world, NOW)  # A 蒐完:分析中
    _corrupt(store)
    state = flow.advance(store, "t1", instrumented.rule_source(store, world.url, 3.0),
                         policy.decide, world.submit, NOW, rule_decide=rule_round.decide)
    assert state is TaskState.FAILED
    assert "CorruptedHistoryRow" in store.latest("t1").error_detail


def test_a_corrupted_rule_step_row_fails_the_collect_step(store, world):
    store.create_task("t1", "c1", NOW)
    for _ in range(3):  # 收到、A、分析 A(續步 → 蒐集證據)
        step(store, world, NOW)
    assert store.latest("t1").state is TaskState.COLLECTING_EVIDENCE
    _corrupt(store)
    assert step(store, world, NOW) is TaskState.FAILED


def test_a_corrupted_rule_event_row_fails_the_collect_step(store, world):
    """代碼審 r2 鏡頭2-1:規則事件表讀不回來也是毀損(包成 CorruptedHistoryRow),蒐證那一步
    轉 FAILED。"""
    store.create_task("t1", "c1", NOW)
    for _ in range(3):  # 收到、A、分析 A(續步 → 蒐集證據)
        step(store, world, NOW)
    store._conn.execute("UPDATE rule_events SET round_id = 'garbage'")  # 測試直接毀損
    assert step(store, world, NOW) is TaskState.FAILED
    assert "CorruptedHistoryRow" in store.latest("t1").error_detail
