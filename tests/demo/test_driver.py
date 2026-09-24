"""一鍵展示驅動程式(Phase 12 增量 1):真的起行程跑情境,觀察系統紀錄、斷言預期處置。"""

import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rtb.demo import driver as driver_module
from rtb.demo.driver import DONE, INCOMPLETE, Driver, Scenario, ScenarioFailed
from rtb.demo.keys import DemoKeys
from rtb.demo.launcher import StartFailed
from rtb.demo.state_store import StateReader, StateWriter


@pytest.fixture
def state(tmp_path):
    writer = StateWriter(tmp_path / "state.db", "demo-1")
    yield writer
    writer.close()


# 全部跑一次的測試換上一瞬間跑完的比較表產生器
# (真的產生器由 tests/tools/test_forgery_comparison.py 測)
QUICK_COMPARISON = [sys.executable, "-c",
                    "print('{\"rows\": [], \"note\": \"測試\", \"seconds\": 0}')"]


def _driver(tmp_path, state, scenarios=None):
    return Driver(tmp_path / "demos", "demo-1", DemoKeys.generate(), state,
                  user_env=os.environ, scenarios=scenarios)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_real_scenario_path_matches_the_recorded_dispositions(tmp_path, state):
    """[S1009] 真的跑一次 F1:判斷紀錄每筆帶來源,路徑經過不知道有沒有寫進去、回頭查、同編號重送,
    平台上只改一次;跑完行程全部結束。"""
    demo = _driver(tmp_path, state)
    pids = []
    original = driver_module.World.start

    def tracking(self, *args, **kwargs):
        process = original(self, *args, **kwargs)
        pids.append(process.pid)
        return process

    driver_module.World.start = tracking
    try:
        verdict = demo.run_one("F1")
    finally:
        driver_module.World.start = original

    assert verdict.status == DONE, verdict.reason
    reader = StateReader(tmp_path / "state.db")
    try:
        decisions = reader.decisions("demo-1", "F1")
        run = reader.scenario_runs("demo-1")[0]
    finally:
        reader.close()
    nodes = [d.node for d in decisions]
    for stream in driver_module.F1_STREAMS.values():
        assert set(stream[0]) <= set(nodes)
    assert all(d.origin.startswith(("analyzer.", "inbox.")) for d in decisions)
    assert run.status == DONE and run.summary
    deadline = time.monotonic() + 5
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pids and not any(_alive(p) for p in pids)


def test_the_state_file_is_updated_at_every_node(tmp_path, state):
    """[S1011] 觀察到新節點後 3 秒內寫進展示狀態:假情境寫兩列系統紀錄,量寫進展示狀態的時間。"""
    from rtb.analyzer.task_store import TaskStore
    from rtb.domain.task_state import TaskState

    seen_at: dict[int, float] = {}
    written_at: dict[int, float] = {}

    def fake(world):
        store = TaskStore(world.analyzer_db)
        try:
            now = datetime.now().astimezone()
            store.create_task("t1", "c1", now)
            written_at[1] = time.monotonic()
            row = store.latest("t1")
            store.commit_step("t1", row.seq, TaskState.COLLECTING_EVIDENCE, now)
            written_at[2] = time.monotonic()
        finally:
            store.close()
        original = world.state.record_decision

        def timed(code, row):
            seen_at[len(seen_at) + 1] = time.monotonic()
            original(code, row)

        world.state.record_decision = timed
        world.watch(lambda: len(seen_at) >= 2, 10)
        return "ok"

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 20, fake)}).run_one("FX")

    assert verdict.status == DONE
    assert set(seen_at) == {1, 2}
    assert all(seen_at[i] - written_at[i] < 3 for i in (1, 2))


def test_a_scenario_is_done_only_when_its_expected_dispositions_are_seen(tmp_path, state):
    """[S1006] 斷言沒過就標「沒跑完」並寫哪一條沒對上,不標照預期跑完。"""
    def failing(world):
        world._path.streams.extend(("t1", "key", n) for n in ["x_write", "x_verify", "x_done"])
        world.require_streams({("t1", "key"): (("x_write", "x_unknown", "x_done"), ())})
        return "不該到這裡"

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 20, failing)}).run_one("FX")

    assert verdict.status == INCOMPLETE
    assert "x_verify" in verdict.reason and "寫入平台" in verdict.reason
    reader = StateReader(tmp_path / "state.db")
    try:
        assert reader.scenario_runs("demo-1")[0].status == INCOMPLETE
    finally:
        reader.close()


@pytest.mark.parametrize(("raised", "expected"), [
    (StartFailed("子行程結束了"), "行程起不來"),
    (RuntimeError("boom"), "展示故障"),
    (ScenarioFailed("平台真實狀態不對"), "平台真實狀態不對"),
])
def test_a_scenario_that_does_not_finish_is_never_shown_as_done(tmp_path, state, raised, expected):
    """[S1007] 行程起不來、展示自己出錯、斷言沒過,都標「沒跑完」並寫原因。"""
    def broken(_world):
        raise raised

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 20, broken)}).run_one("FX")

    assert verdict.status == INCOMPLETE and expected in verdict.reason


def test_a_scenario_over_its_time_limit_is_marked_incomplete(tmp_path, state):
    started = threading.Event()

    finished = threading.Event()

    def slow(world):
        started.set()
        world.watch(lambda: False, 30)
        finished.set()
        return "不該到這裡"

    begin = time.monotonic()
    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 1, slow)}).run_one("FX")

    assert started.is_set()
    assert verdict.status == INCOMPLETE and "時限" in verdict.reason
    assert time.monotonic() - begin < 5
    assert finished.wait(3)  # 超過時限的情境自己的觀察迴圈也停了,不會在背景一直跑


@pytest.mark.parametrize("code", ["F2", "F3"])
def test_f2_and_f3_restarts_keep_the_platform_accepting_writes(tmp_path, state, code):
    """[S1060] 猝死後執行迴圈與平台一起重啟、帶同樣的時鐘偏移:平台沒有拒收寫入許可,只改一次。"""
    verdict = _driver(tmp_path, state).run_one(code)

    assert verdict.status == DONE, verdict.reason


def test_the_demo_f3_checks_the_work_is_analyzed_once(tmp_path, state):
    """[S1053] 兩個真的並行分析工作者同時推進同一件工作,只有一方進入付費判斷、只分析一次。"""
    calls = []
    original = driver_module._race_two_analyzers

    def spy(world):
        result = original(world)
        calls.append(result)
        return result

    driver_module._race_two_analyzers = spy
    try:
        verdict = _driver(tmp_path, state).run_one("F3")
    finally:
        driver_module._race_two_analyzers = original

    assert verdict.status == DONE, verdict.reason
    assert calls == [(1, 1)]


class _Stub:
    """只有交叉核對會讀的那幾樣:平台寫入與廣告現況、收件口事件、嘗試紀錄、執行迴圈。"""

    def __init__(self, writes=(), budget=110, status="active", events=(), attempts=(),
                 exit_code=9):
        self._writes, self._events, self._attempts = list(writes), list(events), list(attempts)
        self._campaign = {"budget": budget, "status": status}
        self.executor = type("P", (), {"poll": lambda _self: exit_code})()

    def platform_writes(self, campaign=None):
        return [w for w in self._writes if campaign is None or w.campaign_id == campaign]

    def campaign(self, _campaign):
        return self._campaign

    def lifecycle(self, _task=None):
        return self._events

    def attempts(self, _task):
        return self._attempts

    def watch(self, done, _limit):
        return done()


def _write(campaign="c1", budget=110, action="update_budget", key="k"):
    from rtb.ops.side_effects import DspWrite

    return DspWrite(1, key, campaign, "t", action, budget, 1, "2026-09-24T00:00:00+00:00", None)


def _event(kind, deliveries=None, reason=None, campaign="c1", task="t1", event_id=1):
    from rtb.executor.inbox_store import LifecycleEvent

    return LifecycleEvent(event_id, "2026-09-24T00:00:00Z", task, 1, "h", campaign, "k", None,
                          None, kind, reason, "executor_loop", None, deliveries, False, "v")


def _row(send_count=1, code=None, state="verified"):
    from rtb.domain.attempt import AttemptState, OutcomeCode
    from rtb.executor.attempt_store import AttemptRow

    outcome = None if code is None else OutcomeCode(code)
    return AttemptRow("k", 1, "c1", AttemptState(state), outcome,
                      None, send_count, 0, datetime.now().astimezone())


@pytest.mark.parametrize(("writes", "budget", "status"), [
    ([_write(key="k"), _write(key="k2")], 110, "active"),  # 寫兩次
    ([_write()], 121, "active"),  # 讀回來的預算不對
    ([], 100, "active"),  # 沒寫
    ([_write(action="pause_campaign")], 110, "active"),  # 第 2 輪代碼審 c1:操作種類不對
])
def test_the_platform_truth_must_show_exactly_one_write(writes, budget, status):
    with pytest.raises(ScenarioFailed, match="平台真實狀態"):
        driver_module._applied_once(_Stub(writes, budget, status), "c1", 110)
    driver_module._applied_once(_Stub([_write()], 110), "c1", 110)


def test_a_rejected_write_permission_marks_the_restart_incomplete():
    with pytest.raises(ScenarioFailed, match="寫入許可"):
        driver_module._no_rejected_permission(
            _Stub(attempts=[_row(), _row(code="capability_rejected", state="escalated")]), "t1")
    driver_module._no_rejected_permission(_Stub(attempts=[_row()]), "t1")


@pytest.mark.parametrize(("deliveries", "ok"), [([1, 2], True), ([1], False), ([], False)])
def test_the_message_must_have_been_delivered_twice(deliveries, ok):
    stub = _Stub(events=[_event("delivered", d) for d in deliveries])
    if ok:
        driver_module._delivered(stub, "t1", 2)
    else:
        with pytest.raises(ScenarioFailed, match="投遞次數"):
            driver_module._delivered(stub, "t1", 2)


@pytest.mark.parametrize(("counts", "times", "ok"), [
    ([1], 1, True), ([2], 1, False), ([1, 2], 2, True), ([], 1, False)])
def test_the_number_of_sends_to_the_platform_is_checked(counts, times, ok):
    """[第 2 輪代碼審 o1/c3] F2 不重送(只送一次)、F1 要同編號補送一次:看嘗試紀錄的送出次數,不只
    看平台上改了幾次(平台對同編號本來就只套用一次)。"""
    stub = _Stub(attempts=[_row(send_count=c) for c in counts])
    if ok:
        driver_module._sent(stub, "t1", times)
    else:
        with pytest.raises(ScenarioFailed, match="次數"):
            driver_module._sent(stub, "t1", times)


def test_an_executor_that_exits_normally_is_not_taken_for_a_crash():
    stub = _Stub(exit_code=0)
    with pytest.raises(ScenarioFailed, match="不是猝死"):
        driver_module.World.crash_executor(stub, 1)


@pytest.mark.parametrize("code", ["F4", "F5", "F6"])
def test_f4_to_f6_run_to_the_expected_dispositions(tmp_path, state, code):
    """F4 搶先改廣告、F5 對抗性名稱、F6 停下等人處理後重新送入:真的跑,照預期處置跑完。"""
    verdict = _driver(tmp_path, state).run_one(code)

    assert verdict.status == DONE, verdict.reason


def test_a_real_f4_path_matches_the_recorded_dispositions(tmp_path, state):
    """[S1009] F4 的判斷紀錄經過擋下與開新工作,每筆帶來源。"""
    assert _driver(tmp_path, state).run_one("F4").status == DONE
    reader = StateReader(tmp_path / "state.db")
    try:
        decisions = reader.decisions("demo-1", "F4")
    finally:
        reader.close()
    blocked = next(d for d in decisions if d.node == "x_blocked")
    assert blocked.outcome == "BlockCode.VERSION_CHANGED"
    assert any(d.node == "a_followup" and d.origin.startswith("analyzer.follow_ups#")
               for d in decisions)


# 縮小版 F7 的情境總時限:放寬到慢機器也跑得完(CI 上 3 秒曾被時限切斷,判成展示故障、筆數停在半途)。
# 「等人確認不算進總時限」另由 test_waiting_for_approval_does_not_count_against_the_f7_time_limit
# 用一段很短的時限驗,不靠縮小版 F7 撞牆鐘
SMALL_F7_LIMIT_SECONDS = 120


def _small_f7(tmp_path, state, cap, approve=None):
    """F7 縮成 30 個廣告、門檻 124(放行 12 個)跑行為;正式情境的規模另外驗。"""
    run = driver_module.make_f7(campaigns=30, limit=124, workers=3, confirm_cap_seconds=cap)
    demo = _driver(tmp_path, state, {"F7": Scenario("F7", "F7", SMALL_F7_LIMIT_SECONDS, run)})
    stop = threading.Event()
    if approve is not None:
        threading.Thread(target=approve, args=(tmp_path, demo, stop), daemon=True).start()
    try:
        return demo.run_one("F7"), demo
    finally:
        stop.set()


def _approve_when_asked(tmp_path, demo, stop):
    """扮伺服器:讀展示狀態裡的確認請求,在伺服器行程內簽發並寫進 F7 的執行端暫存資料庫。"""
    from rtb.capabilitykit import APPROVAL_KEY_ENV
    from rtb.executor import approval
    from rtb.executor.capability_signer import load_tenants, tenant_for
    from rtb.executor.inbox_store import BlockCode, InboxStore

    while not stop.is_set():
        reader = StateReader(tmp_path / "state.db")
        try:
            pending = reader.confirmation("demo-1")
        finally:
            reader.close()
        if pending is not None:
            _, request = pending
            store = InboxStore(demo.root / "F7" / "inbox.db")
            try:
                proposal = store.find_proposal(request.task_id, request.revision).message.proposal
                tenant = tenant_for(load_tenants(Path(request.tenant_config)),
                                    proposal.campaign_id)
                now = datetime.now().astimezone()
                issued = int(now.timestamp())
                token = approval.issue(
                    demo.keys.signing_bytes(APPROVAL_KEY_ENV), proposal, BlockCode(request.stage),
                    tenant, approver="demo-operator", max_increase=request.max_increase,
                    issued_at=issued, expires_at=issued + 300)
                store.add_approval(proposal, BlockCode(request.stage), approval.approval_id(token),
                                   token, now)
            finally:
                store.close()
            return
        stop.wait(0.2)


def test_f7_is_done_when_the_confirmed_one_is_written(tmp_path, state):
    """[S1047] 確認的那一筆寫進平台、其餘維持等人確認,F7 照預期跑完;逐廣告核對放行 12 + 1 個。"""
    verdict, _ = _small_f7(tmp_path, state, cap=60, approve=_approve_when_asked)

    assert verdict.status == DONE, verdict.reason


def test_waiting_for_approval_does_not_count_against_the_f7_time_limit(tmp_path, state,
                                                                    monkeypatch):
    """[S1008] 情境總時限 1.5 秒,F7 的確認等待(驅動程式那一支)等滿 3 秒:等待不算進總時限,情境
    照樣跑完;不在等人確認時睡同樣久就超時(對照組見 test_a_scenario_over_its_time_limit_…)。"""
    from datetime import UTC

    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))
    monkeypatch.setattr(driver_module, "_confirmed_one_written", lambda _w, _r: False)

    def waits(world):
        driver_module._wait_for_confirmation(world, request, 3)
        return "等完了"

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 1.5, waits)}).run_one("FX")

    assert verdict.status == DONE, verdict.reason


def test_nobody_confirming_ends_f7_incomplete_and_clears_the_form(tmp_path, state):
    """[S1008] 沒人確認:F7 標「沒有人確認」、清掉確認表單。"""
    verdict, _ = _small_f7(tmp_path, state, cap=2)

    assert verdict.status == INCOMPLETE
    assert verdict.reason == "沒有人確認"
    reader = StateReader(tmp_path / "state.db")
    try:
        assert reader.confirmation("demo-1") is None
    finally:
        reader.close()


def test_f7_shows_how_many_are_at_each_node(tmp_path, state):
    """[S1057] F7 帶各節點筆數:放行的在完成、其餘在等人確認。"""
    _small_f7(tmp_path, state, cap=1)
    reader = StateReader(tmp_path / "state.db")
    try:
        counts = dict(reader.node_counts("demo-1", "F7"))
    finally:
        reader.close()
    assert counts == {"x_done": 12, "x_wait_approval": 18}


def test_f7_is_checked_campaign_by_campaign():
    """[S1058] 平台上的放行要跟收件口記為完成的那一批一一對上、每個恰好一筆加預算、加額恰好一成、
    數量等於門檻算出的;逐廣告讀回預算與狀態(第 2 輪代碼審 c1)。"""
    def world(writes, done, final=None):
        stub = _Stub(writes=writes, events=[_event("handed_off", campaign=c) for c in done])
        budgets = final or {w.campaign_id: w.new_budget for w in writes}
        stub.campaign = lambda c: {"budget": budgets.get(c, 100), "status": "active"}
        return stub

    ok = world([_write("a"), _write("b")], ["a", "b"])
    driver_module._check_campaign_by_campaign(ok, ["a", "b", "c"], 2)
    for stub, passes in [
        (world([_write("a")], ["a", "b"]), 1),  # 收件口說完成、平台沒寫
        (world([_write("a", 120)], ["a"]), 1),  # 加額不對
        (world([_write("a"), _write("a")], ["a"]), 1),  # 寫兩次
        (world([_write("a"), _write("c")], ["a", "c"]), 1),  # 數量不對
        (world([_write("a", action="pause_campaign")], ["a"]), 1),  # 操作種類不對(c1)
        (world([_write("a")], ["a"], final={"a": 100}), 1),  # 平台上最後的預算不對(c1)
    ]:
        with pytest.raises(ScenarioFailed):
            driver_module._check_campaign_by_campaign(stub, ["a", "b", "c"], passes)


def test_the_demo_f7_is_scaled_down_and_says_so():
    """[S1012] 展示的 F7 是 300 個廣告、門檻 1234(3000 個、12345 的等比例縮小)、8 個工作者,
    打真的模擬 DSP 子行程。"""
    assert (driver_module.F7_CAMPAIGNS, driver_module.F7_LIMIT, driver_module.F7_WORKERS) == (
        300, 1234, 8)
    assert driver_module.F7_LIMIT * 10 + 5 == 12_345
    import inspect
    source = inspect.getsource(driver_module.make_f7)
    assert "world.start_platform()" in source


@pytest.mark.parametrize(("writes", "limit", "confirmed", "ok"), [
    ([110] * 12, 124, False, True),  # 120 ≤ 124 < 130
    ([110] * 11, 124, False, False),  # 還放得下一個卻沒放
    ([110] * 13, 124, False, False),  # 超過門檻
    ([110] * 13, 124, True, True),  # 確認的那一筆刻意超過
    ([110] * 12, 248, False, False),  # 門檻寫成兩倍:個數湊得到,總額抓得到
])
def test_the_total_let_through_fits_the_limit_and_one_more_would_not(writes, limit, confirmed,
                                                                      ok):
    stub = _Stub(writes=[_write(f"k{i}", b) for i, b in enumerate(writes)])
    if ok:
        driver_module._check_total_against_limit(stub, limit, confirmed)
    else:
        with pytest.raises(ScenarioFailed, match="總額"):
            driver_module._check_total_against_limit(stub, limit, confirmed)


# ---- 全部跑一次:依序跑完、行程收乾淨、最後跑驗證器 ----
def test_the_driver_runs_every_scenario_and_leaves_no_process_behind(tmp_path, state):
    """[S1005] 依序跑完每個情境;每個情境結束時它起的行程全部結束(下一個情境開始時查)。"""
    from rtb.demo.launcher import Role

    order, leftovers, pids = [], [], []

    def make(code):
        def run(world):
            leftovers.extend(p for p in pids if _alive(p))
            order.append(code)
            process = world.start(Role.INBOX, ["--db", str(world.dir / "inbox.db")])
            pids.append(process.pid)
            return f"{code} ok"
        return run

    codes = ("F1", "F2", "F3")
    scenarios = {c: Scenario(c, c, 20, make(c)) for c in codes}
    demo = _driver(tmp_path, state, scenarios)
    demo.verifier_command = [sys.executable, "-c", "print('通過:假的驗證器')"]
    demo.comparison_command = QUICK_COMPARISON  # 比較表另有測試;這裡不真跑

    verdicts, _ = demo.run_all(codes)

    assert order == list(codes) and [v.code for v in verdicts] == list(codes)
    assert leftovers == []
    assert not any(_alive(p) for p in pids)


def test_the_default_full_run_is_f1_to_f7_in_order():
    assert driver_module.ALL_CODES == ("F1", "F2", "F3", "F4", "F5", "F6", "F7")
    assert tuple(driver_module.SCENARIOS) == driver_module.ALL_CODES


def _quick(tmp_path, state):
    return _driver(tmp_path, state, {"F1": Scenario("F1", "F1", 20, lambda _w: "ok")})


@pytest.mark.parametrize(("script", "passed", "reasons"), [
    ("print('宣稱驗證器'); print('通過:5 條宣稱,跑了 77 支證據測試全部通過')", True, ()),
    ("import sys; print('宣稱驗證器'); print('擋下(2 條原因):'); print('- a:缺檔'); "
     "print('- b:雜湊不一致'); sys.exit(1)", False, ("a:缺檔", "b:雜湊不一致")),
])
def test_a_full_run_ends_with_the_verifier_output_kept_verbatim(tmp_path, state, script,
                                                                passed, reasons):
    """全部跑一次的最後一步跑驗證器,原樣記下每一行、通過或擋下、擋下原因、時間與展示編號。"""
    demo = _quick(tmp_path, state)
    demo.verifier_command = [sys.executable, "-c", script]
    demo.comparison_command = QUICK_COMPARISON  # 比較表另有測試;這裡不真跑

    _, outcome = demo.run_all(("F1",))

    reader = StateReader(tmp_path / "state.db")
    try:
        stored = reader.latest_verifier_run()
    finally:
        reader.close()
    assert outcome is not None and stored == outcome
    assert stored.passed is passed and stored.reasons == reasons
    assert stored.lines[0] == "宣稱驗證器" and stored.demo_id == "demo-1"


def test_a_verifier_that_hangs_is_recorded_as_not_passed(tmp_path, state):
    demo = _quick(tmp_path, state)
    demo.verifier_command = [sys.executable, "-c", "import time; time.sleep(30)"]
    demo.comparison_command = QUICK_COMPARISON  # 比較表另有測試;這裡不真跑
    demo.verifier_timeout_seconds = 1

    _, outcome = demo.run_all(("F1",))

    assert outcome is not None and not outcome.passed and "逾時" in outcome.reasons[0]


def test_a_single_scenario_rerun_does_not_run_the_verifier(tmp_path, state):
    """[S1021 的驅動程式那一半] 單一情境重跑不啟動驗證器。"""
    demo = _quick(tmp_path, state)
    demo.verifier_command = [sys.executable, "-c", "raise SystemExit('不該跑')"]

    demo.run_one("F1")

    reader = StateReader(tmp_path / "state.db")
    try:
        assert reader.latest_verifier_run() is None
    finally:
        reader.close()


def test_the_default_verifier_is_the_project_one():
    command = driver_module.default_verifier_command()
    assert command[-2:] == [str(driver_module.PROJECT_ROOT / "tools" / "verify_claims.py"),
                            "claims/"]
    assert (driver_module.PROJECT_ROOT / "tools" / "verify_claims.py").is_file()


def test_an_incomplete_scenario_does_not_stop_the_full_run(tmp_path, state):
    """全部跑一次時一個情境沒跑完,後面的照樣跑,最後照樣跑驗證器。"""
    seen = []

    def ok(code):
        def run(_world):
            seen.append(code)
            return "ok"
        return run

    def broken(_world):
        seen.append("F2")
        raise ScenarioFailed("故意沒跑完")

    scenarios = {"F1": Scenario("F1", "F1", 20, ok("F1")), "F2": Scenario("F2", "F2", 20, broken),
                 "F3": Scenario("F3", "F3", 20, ok("F3"))}
    demo = _driver(tmp_path, state, scenarios)
    demo.verifier_command = [sys.executable, "-c", "print('通過')"]
    demo.comparison_command = QUICK_COMPARISON  # 比較表另有測試;這裡不真跑

    verdicts, outcome = demo.run_all(("F1", "F2", "F3"))

    assert seen == ["F1", "F2", "F3"]
    assert [v.status for v in verdicts] == [DONE, INCOMPLETE, DONE]
    assert outcome is not None and outcome.passed


# ---- 第 2 輪代碼審(資安席先報):超時之後情境本體還在跑 ----
def test_a_scenario_past_its_time_limit_cannot_start_new_processes(tmp_path, state):
    """時限到了,驅動程式先等情境本體收手再收行程;收手之後情境本體起的新行程一律被拒,
    不會留下沒人收、帶著金鑰的孤兒。"""
    from rtb.demo.launcher import Role

    started = []

    def slow(world):
        time.sleep(1.5)  # 不看停止旗標:模擬一段跑很久的外部呼叫
        started.append(world.start(Role.INBOX, ["--db", str(world.dir / "inbox.db")]).pid)
        return "不該到這裡"

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 1, slow)}).run_one("FX")

    assert verdict.status == INCOMPLETE and "時限" in verdict.reason
    time.sleep(1.0)
    assert started == []


def test_a_timed_out_scenario_cannot_rewrite_its_status(tmp_path, state):
    """時限到了之後情境本體走到等人確認:不能再寫確認請求,也不能把「沒跑完」改寫回進行中。"""
    from datetime import UTC, datetime

    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))

    def slow(world):
        time.sleep(1.5)
        driver_module._wait_for_confirmation(world, request, 5)
        return "不該到這裡"

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 1, slow)}).run_one("FX")

    time.sleep(1.0)
    reader = StateReader(tmp_path / "state.db")
    try:
        assert reader.scenario_runs("demo-1")[0].status == INCOMPLETE
        assert reader.confirmation("demo-1") is None
    finally:
        reader.close()
    assert verdict.status == INCOMPLETE


def test_the_driver_reports_a_timeout_only_after_the_scenario_has_wound_down(tmp_path, state):
    """超過時限後先等情境本體收手(有上限)才回報:回報的時候本體已經結束,不會之後才冒出寫入。"""
    ended = threading.Event()

    def slow(_world):
        time.sleep(1.5)
        ended.set()
        return "不該到這裡"

    _driver(tmp_path, state, {"FX": Scenario("FX", "假", 1, slow)}).run_one("FX")

    assert ended.is_set()


class _ConfirmStub:
    def __init__(self, stop_during_wait, approved_at_close=False, written_after=False,
                 sign_failed=False):
        self.code, self.stop = "F7", threading.Event()
        self.marks, self.cleared, self.waits = [], [], []
        self._stop_during_wait = stop_during_wait
        self._approved, self._written_after = approved_at_close, written_after
        self._failed = sign_failed
        stub = self

        class _State:
            def set_confirmation(self, *_a):
                stub.marks.append("request")

            def mark_status(self, _code, status):
                stub.marks.append(status)

            def clear_confirmation(self, _now=None):
                stub.cleared.append(True)
                return stub._approved

            def confirmation_failed(self):
                return stub._failed

        self.state = _State()

    def wait_paused(self, _done, limit, _on_poll=None):
        self.waits.append(limit)
        if self._stop_during_wait:
            self.stop.set()
        return len(self.waits) > 1 and (self._written_after or self._failed)


def test_a_stopped_scenario_does_not_ask_for_confirmation():
    world = _ConfirmStub(stop_during_wait=False)
    world.stop.set()
    with pytest.raises(driver_module.ScenarioStopped):
        driver_module._wait_for_confirmation(world, _request(), 5)
    assert world.marks == []


def test_a_stop_during_the_confirmation_wait_does_not_mark_running_again():
    from datetime import UTC

    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))
    world = _ConfirmStub(stop_during_wait=True)

    assert driver_module._wait_for_confirmation(world, request, 5) is False
    assert world.marks == ["request", driver_module.AWAITING_CONFIRMATION]
    assert world.cleared == [True]


# ---- 第 2 輪代碼審 o2/v1:一支行程收不掉不能讓其他行程變孤兒 ----
class _Broken:
    def __init__(self, log, name, fails=False):
        self.log, self.name, self.fails = log, name, fails

    def stop(self):
        self.log.append(self.name)
        if self.fails:
            raise PermissionError(1, "Operation not permitted")


def test_closing_a_world_stops_every_process_even_if_one_fails(tmp_path, state):
    world = driver_module.World(tmp_path, "FX", DemoKeys.generate(), state, {}, threading.Event())
    log = []
    world.processes = [_Broken(log, "dsp"), _Broken(log, "inbox", fails=True),
                       _Broken(log, "executor")]

    with pytest.raises(PermissionError):
        world.close()

    assert sorted(log) == ["dsp", "executor", "inbox"]
    assert world.processes == []


def test_a_scenario_whose_cleanup_fails_is_still_finished(tmp_path, state):
    """收尾出錯:情境照樣結案(沒跑完、寫原因),不停在執行中,也不中斷整次展示。"""
    def run(world):
        world.processes.append(_Broken([], "inbox", fails=True))
        return "照預期"

    demo = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 30, run),
                                     "FY": Scenario("FY", "假", 30, lambda _w: "照預期")})
    verdicts = demo.run(["FX", "FY"])

    assert [v.status for v in verdicts] == [INCOMPLETE, DONE]
    assert "收尾" in verdicts[0].reason
    reader = StateReader(tmp_path / "state.db")
    try:
        assert [r.status for r in reader.scenario_runs("demo-1")] == [INCOMPLETE, DONE]
    finally:
        reader.close()


def _request(expires_in=timedelta(hours=1), stage="aggregate_limit_reached"):
    from datetime import UTC

    from rtb.demo.state_store import ConfirmationRequest

    return ConfirmationRequest("t1", 1, "h" * 64, "/x", stage, 10,
                               datetime.now(UTC) + expires_in, (("廣告", "c1"),))


# ---- 第 2 輪代碼審:假綠(展示說照預期,實際上沒驗到該驗的東西) ----
def _streams_world(tmp_path, state, code):
    world = driver_module.World(tmp_path, code, DemoKeys.generate(), state, {},
                                threading.Event())
    world.record = lambda **_kw: None
    return world


def _fill(world, streams):
    world._path.streams[:] = [(task, kind, node) for (task, kind), nodes in streams.items()
                              for node in nodes]


def _normal(expected):
    return {stream: list(required) for stream, (required, _) in expected.items()}


@pytest.mark.parametrize("extra", ["x_resend", "x_escalated"])
def test_a_resend_or_hand_off_in_f2_is_not_done(tmp_path, state, extra):
    """[o1/c3] F2 要證明的是不重送:寫入平台那條紀錄多一次同編號重送或轉人工,就算必經節點都照順序
    出現也判沒跑完。"""
    world = _streams_world(tmp_path, state, "F2")
    streams = _normal(driver_module.F2_STREAMS)
    _fill(world, streams)
    world.require_streams(driver_module.F2_STREAMS)
    key = streams[("t1", "key")]
    key.insert(key.index("x_verify"), extra)
    _fill(world, streams)
    with pytest.raises(ScenarioFailed, match=extra):
        world.require_streams(driver_module.F2_STREAMS)


def _follow():
    from rtb.analyzer.task_store import follow_up_id

    return follow_up_id("t1")


@pytest.mark.parametrize(("code", "stream", "repeat"), [
    ("F1", ("t1", "key"), ["x_unknown", "x_resend"]),  # 多繞一圈不明、重送
    ("F2", ("t1", "proposal"), ["x_reclaimed"]),  # 被接手兩次
    ("F2", ("t1", "key"), ["x_unknown"]),  # 兩次不明
    ("F4", ("t1", "proposal"), ["x_pick", "x_blocked"]),  # 多擋一次
    ("F4", ("t1", "task"), ["a_followup"]),  # 開兩次新工作
])
def test_a_required_step_seen_again_is_not_done(tmp_path, state, code, stream, repeat):
    """[第 3 輪代碼審 g1/x1] 必經節點照順序逐一消耗:重複出現(多繞一圈)不能因為「找得到」就算過。"""
    expected = {"F1": driver_module.F1_STREAMS, "F2": driver_module.F2_STREAMS,
                "F4": {**driver_module._blocked_then_replanned("t1", ("x_pending", "x_pick")),
                       **driver_module._written(_follow(), ("x_write", "x_verify", "x_done"))},
                }[code]
    world = _streams_world(tmp_path, state, code)
    streams = _normal(expected)
    _fill(world, streams)
    world.require_streams(expected)
    nodes = streams[stream]
    at = nodes.index(repeat[-1]) + 1
    nodes[at:at] = repeat
    _fill(world, streams)
    with pytest.raises(ScenarioFailed, match="對不上"):
        world.require_streams(expected)


def test_each_task_is_checked_on_its_own(tmp_path, state):
    """[g1] 多件工作(F5 的兩個廣告、F4 的新舊工作)按工作分開核對:另一件工作的節點不會拿來湊數,
    沒預期到的紀錄也算對不上。"""
    world = _streams_world(tmp_path, state, "F5")
    streams = _normal(driver_module.F5_STREAMS)
    _fill(world, streams)
    world.require_streams(driver_module.F5_STREAMS)
    streams[("t2", "key")] = ["x_write"]  # 不該調整的那件工作寫了平台
    _fill(world, streams)
    with pytest.raises(ScenarioFailed, match="沒預期"):
        world.require_streams(driver_module.F5_STREAMS)
    del streams[("t2", "key")]
    streams[("t1", "task")].remove("x_done")
    streams[("t2", "task")].append("x_done")  # 完成記在別件工作上
    _fill(world, streams)
    with pytest.raises(ScenarioFailed, match="t1"):
        world.require_streams(driver_module.F5_STREAMS)


@pytest.mark.parametrize(("state_", "handed_off", "verified", "ok"), [
    ("completed", True, True, True),
    ("handed_off", True, True, False),  # 分析端還沒結案(晚幾毫秒)
    ("completed", False, True, False),
    ("completed", True, False, False),
])
def test_a_task_is_finished_only_when_all_three_sides_say_so(state_, handed_off, verified, ok):
    """[第 3 輪代碼審 g1 真跑時抓到的] 分析端問過收件口才結案,比收件口晚:只等平台與收件口就核對,
    分析端那條紀錄會偶發少一步。"""
    from rtb.domain.task_state import TaskState

    stub = _Stub()
    stub.task_history = lambda _t: (type("R", (), {"state": TaskState(state_)})(),)
    stub.handed_off = lambda _t: handed_off
    stub.verified = lambda _t: verified
    assert driver_module.World.finished(stub, "t1") is ok


@pytest.mark.parametrize(("blocked", "replan", "ok"), [
    ("version_changed", "VERSION_CHANGED", True),
    ("policy_version_changed", "VERSION_CHANGED", False),  # 擋下原因換成規則改了
    ("version_changed", "POLICY_VERSION_CHANGED", False),  # 開新工作的原因不對
])
def test_f4_and_f6_check_the_block_reason_not_just_the_block(blocked, replan, ok):
    """[f1] F4、F6 要以版本已變擋下、接續關係的原因也是版本已變:換成別的原因擋下不能判照預期。"""
    from rtb.analyzer.task_store import FollowUpRow, ReplanReason

    stub = _Stub(events=[_event("received"), _event("blocked", reason=blocked)])
    stub.follow_ups = lambda: [FollowUpRow(1, "t1", "t1-next", ReplanReason[replan],
                                           datetime.now().astimezone())]
    if ok:
        assert driver_module._blocked_as_version_changed(stub, "t1") == "t1-next"
    else:
        with pytest.raises(ScenarioFailed, match="版本已變"):
            driver_module._blocked_as_version_changed(stub, "t1")


def test_the_new_task_counts_as_written_only_once_the_inbox_says_so():
    """[f2] 平台上已經是新值、收件口還沒記下完成(晚幾毫秒):還不算,不拿收件口的紀錄去斷言。"""
    from rtb.analyzer.task_store import FollowUpRow, ReplanReason

    stub = _Stub(budget=220)
    stub.budget = lambda _c: 220
    stub.follow_ups = lambda: [FollowUpRow(1, "t1", "t1-next", ReplanReason.VERSION_CHANGED,
                                           datetime.now().astimezone())]
    stub.finished = lambda _t: False  # 平台已是新值,收件口或分析端還沒記下完成
    assert driver_module._follow_up_written(stub, "t1", "c1", 220) is False
    stub.finished = lambda task: task == "t1-next"
    assert driver_module._follow_up_written(stub, "t1", "c1", 220) is True


def test_the_confirmation_wait_is_capped_by_the_decision_expiry_too(monkeypatch):
    """[f3] 等人確認的上限是 min(決策到期減一分鐘, 固定上限):決策 5 分鐘後到期、固定上限 10 分鐘,
    上限是 4 分鐘。"""
    frozen = datetime(2026, 9, 24, 12, 0, tzinfo=driver_module.UTC)
    monkeypatch.setattr(driver_module, "_now", lambda: frozen)
    request = _request()
    request = type(request)(**{**request.__dict__,
                               "decision_expires_at": frozen + timedelta(minutes=5)})
    assert driver_module._confirm_limit(request, 600) == 240
    assert driver_module._confirm_limit(request, 60) == 60


def test_a_zero_aggregate_limit_stays_zero(tmp_path, state):
    """[s4] 明確給總上限 0 就是 0(原本用 or,0 會變成寬值十億)。"""
    import json

    world = driver_module.World(tmp_path, "F7", DemoKeys.generate(), state, {}, threading.Event())
    world.seed([], aggregate_limit=0)
    spec = json.loads(world.tenants.read_text(encoding="utf-8"))["tenants"]["t-default"]
    assert spec["aggregate_limit"] == 0


def test_f5_checks_the_name_really_reached_the_analyzer():
    """[f4] F5 要驗對抗文字真的進了分析端的證據,不只驗平台上的名稱沒被改。"""
    from rtb.domain.evidence import EvidenceKind

    item = type("E", (), {"kind": EvidenceKind.CAMPAIGN_TEXT,
                          "payload": {"name": driver_module.ADVERSARIAL_NAME}})()
    stub = _Stub()
    stub.evidence = lambda _t: [item]
    assert driver_module._names_seen_by_the_analyzer(stub, "t1") == [
        driver_module.ADVERSARIAL_NAME]
    item.payload = {"name": None}
    assert driver_module._names_seen_by_the_analyzer(stub, "t1") == [None]


def test_f5_says_only_what_it_checked():
    """[c5] 分析端目前只走程式規則:F5 不宣稱擋住了提示注入,註明模型那一段待 11B 接上後補驗。"""
    import inspect

    source = inspect.getsource(driver_module._run_f5)
    assert "11B" in source and "擋住" not in source


def test_the_driver_reads_other_systems_only_through_their_exits():
    """[a2] 萬用的查詢讀法拿掉了:斷言走唯讀出口。"""
    assert not hasattr(driver_module.World, "query")


def _waiting_stub(stage="budget_increase_too_large", budget=100):
    from rtb.domain.proposal import content_hash
    from tests.analyzer.conftest import make_proposal

    proposal = make_proposal(task_id="t1", campaign_id="c1")
    row = type("R", (), {"proposal": proposal})()
    event = _event("awaiting_approval", reason=stage)
    event = type(event)(**{**event.__dict__, "content_hash": content_hash(proposal)})
    stub = _Stub()
    stub.latest_by_proposal = lambda: {("t1", 1, event.content_hash): event}
    stub.task_history = lambda _t: (row,)
    stub.budget = lambda _c: budget
    stub.tenants = Path("/x/tenants.json")
    return stub, proposal


def test_the_confirmation_shows_the_stage_it_really_stopped_at():
    """[s2] 給人看的「關卡」由收件口記的那一關產生,跟簽章用的是同一個;查不到廣告就算沒跑完,
    不補 0。"""
    stub, _ = _waiting_stub("budget_increase_too_large")
    waiting = driver_module._earliest_waiting(stub)
    assert dict(waiting.request.numbers)["關卡"] == driver_module.STAGE_TEXT[
        driver_module.BlockCode.BUDGET_INCREASE_TOO_LARGE]
    assert waiting.request.stage == "budget_increase_too_large"
    stub, _ = _waiting_stub(budget=None)
    with pytest.raises(ScenarioFailed, match="找不到"):
        driver_module._earliest_waiting(stub)


@pytest.mark.parametrize(("signer", "used", "ok"), [
    ("demo", True, True),
    ("other", True, False),  # 不是這次展示簽的
    ("demo", False, False),  # 寫進平台時沒用到這一關的核可
    (None, True, False),  # 沒有核可
])
def test_the_confirmed_one_must_carry_this_demos_approval(signer, used, ok):
    """[c2] 確認的那一筆要有這次展示簽發、內容相符的核可,而且真的用它寫進去;只看平台出現寫入
    不算。"""
    from contextlib import contextmanager

    from rtb.capabilitykit import APPROVAL_KEY_ENV
    from rtb.domain.attempt import operation_key
    from rtb.executor import approval
    from rtb.executor.capability_signer import Tenant
    from rtb.executor.inbox_store import BlockCode

    stub, proposal = _waiting_stub("aggregate_limit_reached")
    waiting = driver_module._earliest_waiting(stub)
    keys = DemoKeys.generate()
    stub.keys = keys
    issued = int(proposal.decision_expires_at.timestamp()) - 120
    token = None if signer is None else approval.issue(
        (keys if signer == "demo" else DemoKeys.generate()).signing_bytes(APPROVAL_KEY_ENV),
        proposal, BlockCode.AGGREGATE_LIMIT_REACHED, Tenant("t", frozenset({"c1"}), 1000, 500),
        approver="demo-operator", max_increase=10, issued_at=issued, expires_at=issued + 60)

    class Inbox:
        def latest_approval(self, _tx, _proposal, _stage):
            return token

        def approval_uses_for(self, _tx, keys_):
            return {k: frozenset({"aggregate_limit_reached"}) for k in keys_} if used else {}

    @contextmanager
    def inbox_tx():
        yield Inbox(), None

    stub._inbox_tx = inbox_tx
    assert operation_key(proposal)
    if ok:
        driver_module._approved_by_this_demo(stub, waiting)
    else:
        with pytest.raises(ScenarioFailed, match="核可"):
            driver_module._approved_by_this_demo(stub, waiting)


def test_the_confirmed_one_counts_as_written_only_once_the_inbox_says_so():
    """[f2] F7 確認的那一筆:平台上出現寫入、收件口還沒記下完成時還不算。"""
    stub = _Stub(writes=[_write("c1")])
    stub.handed_off = lambda _t: False
    assert driver_module._confirmed_one_written(stub, _request()) is False
    stub.handed_off = lambda task: task == "t1"
    assert driver_module._confirmed_one_written(stub, _request()) is True


# ---- 第 3 輪代碼審 s2/p1/x2:驗證器逾時或取消,不留孤兒、保留已印的輸出 ----
FAKE_VERIFIER = """
import os, signal, subprocess, sys, time
grand = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         start_new_session=True)
open(sys.argv[1], "w").write(str(grand.pid))
def stop(*_):
    os.killpg(grand.pid, signal.SIGKILL)  # 跟真的驗證器一樣:收到 SIGTERM 收掉自己起的群組
    raise SystemExit(1)
signal.signal(signal.SIGTERM, stop)
print("宣稱驗證器", flush=True)
print("提交編號:abc", flush=True)
time.sleep(60)
"""


def _gone(pid, within=5.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_verifier_that_times_out_leaves_no_process_and_keeps_its_output(tmp_path):
    """逾時:驗證器另開行程群組起的孫行程也要結束;已經印出來的每一行照樣留下。"""
    marker = tmp_path / "grand.pid"
    run = driver_module.run_verifier([sys.executable, "-c", FAKE_VERIFIER, str(marker)],
                                     "demo-1", 2)
    assert not run.passed and "逾時" in run.reasons[0]
    assert run.lines[:2] == ("宣稱驗證器", "提交編號:abc")
    assert _gone(int(marker.read_text()))


def test_a_cancel_stops_a_running_verifier(tmp_path):
    """整次展示被取消:跑到一半的驗證器也收掉,不等它跑完。"""
    marker = tmp_path / "grand.pid"
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    began = time.monotonic()
    run = driver_module.run_verifier([sys.executable, "-c", FAKE_VERIFIER, str(marker)],
                                     "demo-1", 60, stop=stop)
    assert time.monotonic() - began < 15
    assert not run.passed and "取消" in run.reasons[0]
    assert _gone(int(marker.read_text()))


# ---- 增量 2b:判斷的根據、誰判的、操作鍵(真的跑) ----
def test_real_decisions_carry_their_basis(tmp_path, state):
    """F5 兩件工作:提案那一步與不調整那一步都帶重算的根據、最後一組結論跟實際判定一致;寫入平台
    那一步帶執行端記下的核對材料與操作鍵。"""
    from rtb.demo import basis

    assert _driver(tmp_path, state).run_one("F5").status == DONE
    reader = StateReader(tmp_path / "state.db")
    try:
        decisions = reader.decisions("demo-1", "F5")
    finally:
        reader.close()
    by_node = {d.node: d for d in decisions}
    assert by_node["a_propose"].basis[-1].conclusion == basis.PROPOSE
    assert by_node["a_no_action"].basis[-1].conclusion == "沒有花太慢,不調整"
    assert {b.source for b in by_node["a_propose"].basis} == {basis.RECOMPUTED}
    write = by_node["x_write"]
    assert write.basis and {b.source for b in write.basis} == {basis.RECORDED}
    assert write.operation_key and write.actor == "程式"
    assert by_node["a_receive"].operation_key is None


def _details(tmp_path, code):
    reader = StateReader(tmp_path / "state.db")
    try:
        return reader.scenario_details("demo-1", code)
    finally:
        reader.close()


def test_a_scenario_records_what_changed_and_how_it_was_set_up(tmp_path, state):
    """情境細節:為什麼開始(照實寫驅動程式直接建工作)、目標、刻意製造的故障與位置、排隊等了幾秒、
    追蹤的操作鍵與平台上套用幾次、最後改了什麼(平台預算原樣整數,沒有幣別)。"""
    assert _driver(tmp_path, state).run_one("F2").status == DONE
    details = _details(tmp_path, "F2")
    assert details.trigger == driver_module.TRIGGER and "代替排程" in details.trigger
    assert details.goal
    assert [node for node, _ in details.injected_faults] == ["x_write"]
    assert details.operation_key and details.platform_apply_count == 1
    assert isinstance(details.queue_wait_seconds, int) and details.queue_wait_seconds >= 0
    change = details.change
    assert (change.campaign, change.before, change.after, change.written) == ("c1", 100, 110, True)
    # 平台最後的樣子與操作紀錄(平台唯讀端點)、收件口的處置,給頁面的平台與處置兩欄
    assert details.platform == (("c1", 110, 2, "active"),)
    assert len(details.platform_operations) == 1 and "110" in details.platform_operations[0]
    assert details.dispositions == ()  # 照預期寫進去,沒有擋下或停下


def test_a_replayed_scenario_keeps_its_audit_and_dispositions(tmp_path, state):
    """F6:死信操作稽核(誰、做了什麼)與收件口的擋下、停下原因照實記下。"""
    assert _driver(tmp_path, state).run_one("F6").status == DONE
    details = _details(tmp_path, "F6")
    assert any("demo-operator" in line and "重新送入" in line for line in details.audit)
    codes = {code for _, code, _ in details.dispositions}
    assert {"delivery_limit", "version_changed"} <= codes


def test_a_blocked_then_replanned_scenario_shows_the_final_write(tmp_path, state):
    assert _driver(tmp_path, state).run_one("F4").status == DONE
    details = _details(tmp_path, "F4")
    change = details.change
    # 「之前」是被追蹤那把鍵寫入前的平台值:別的寫入者先把 100 改成 200,本系統只把 200 改成 220
    # (代碼審 r1 d3:原本取造資料時的 100,看起來像一次加了 120%)
    assert (change.before, change.after, change.written) == (200, 220, True)
    assert details.platform_apply_count == 1  # 另一個寫入者那一筆不是這把鍵,不算


def test_f7_records_an_overview_and_the_confirmed_one(tmp_path, state):
    """F7 兩樣都放(協調者裁定):一行彙總(放行幾個、人工確認後寫入幾個、沒寫入幾個、加了多少與總上限),
    加上人工確認那一筆的明細。"""
    verdict, _ = _small_f7(tmp_path, state, cap=60, approve=_approve_when_asked)
    assert verdict.status == DONE, verdict.reason
    details = _details(tmp_path, "F7")
    assert "放行 12 個" in details.change_overview
    assert "人工確認後寫入 1 個" in details.change_overview
    assert "沒寫入 17 個" in details.change_overview and "總上限 124" in details.change_overview
    assert details.change.written and (details.change.before, details.change.after) == (100, 110)


def test_a_mismatch_between_live_decisions_and_the_trace_marks_the_scenario_incomplete(
        tmp_path, state):
    """[S1010] 情境結束後觀察到的紀錄跟必經節點對不上(多一步、少一步、順序不對),或平台真實狀態跟
    預期寫入對不上:驅動程式把情境標「沒跑完」並寫哪一步對不上。"""
    def mismatched(world):
        world._path.streams.extend(("t1", "key", n) for n in ["x_write", "x_resend", "x_done"])
        world.require_streams({("t1", "key"): (("x_write", "x_verify", "x_done"), ())})
        return "不該到這裡"

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 20, mismatched)}).run_one("FX")

    assert verdict.status == INCOMPLETE and "對不上" in verdict.reason
    assert "x_resend" in verdict.reason


def test_an_approval_signed_just_before_the_window_closes_is_still_waited_for():
    """[代碼審 r1 x3/s5/v5] 關確認窗跟簽發互斥:關窗前一刻已經簽了,就再等它寫進平台,不直接判成
    沒有人確認;沒簽就不多等。"""
    from datetime import UTC

    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))
    signed = _ConfirmStub(stop_during_wait=False, approved_at_close=True, written_after=True)
    assert driver_module._wait_for_confirmation(signed, request, 5) is True
    assert signed.waits[1] == driver_module.APPROVED_WRITE_SECONDS
    unsigned = _ConfirmStub(stop_during_wait=False)
    assert driver_module._wait_for_confirmation(unsigned, request, 5) is False
    assert len(unsigned.waits) == 1


# ---- 代碼審 r1(Phase 12 增量 2)----
def test_f7_without_a_confirmation_still_names_its_key_and_counts_zero_writes(tmp_path, state):
    """[代碼審 r1 d7] 沒人確認的那一筆從沒開始寫入,嘗試紀錄沒有鍵:改從生命週期事件取鍵,平台上
    確定套用 0 次(不寫成「沒有記錄」)。"""
    verdict, _ = _small_f7(tmp_path, state, cap=2)
    assert verdict.reason == "沒有人確認"
    details = _details(tmp_path, "F7")
    assert details.operation_key and details.platform_apply_count == 0
    assert details.change is not None and details.change.written is False


def test_scenario_details_are_written_after_the_processes_are_closed(tmp_path):
    """[代碼審 r1 d6] 情境細節寫不進展示狀態庫:子行程照樣先收掉,情境照樣結案(標沒跑完、寫原因),
    不停在執行中。"""
    class Broken(StateWriter):
        def set_scenario_details(self, code, details):
            raise RuntimeError("磁碟滿了")

    writer = Broken(tmp_path / "state.db", "demo-1")
    pids = []

    def run(world):
        pids.append(world.start_platform().pid)
        return "跑完"

    verdict = _driver(tmp_path, writer, {"FX": Scenario("FX", "假", 30, run)}).run_one("FX")
    assert verdict.status == INCOMPLETE and "情境細節寫不進" in verdict.reason
    assert pids and not _alive(pids[0])
    reader = StateReader(tmp_path / "state.db")
    try:
        (row,) = reader.scenario_runs("demo-1")
    finally:
        reader.close()
    assert row.status == INCOMPLETE and row.finished_at is not None


# ---- 代碼審 r2(Phase 12 增量 2)----
def test_a_cancel_that_lands_before_the_scenario_starts_still_stops_it(tmp_path):
    """[代碼審 r2 v2/s1] 取消落在「檢查 stop」與「記下正在跑的情境」之間:情境照樣收到停止,不會
    跑到時限;記成展示被停止。"""
    class Racing(StateWriter):
        def start_scenario(self, code, at):
            demo.cancel()  # 取消剛好落在這裡:驅動程式還沒記下正在跑的情境
            super().start_scenario(code, at)

    def waits(world):
        world.stop.wait(20)
        raise ScenarioFailed("情境自己的失敗")

    writer = Racing(tmp_path / "state.db", "demo-1")
    demo = _driver(tmp_path, writer, {"FX": Scenario("FX", "假", 30, waits)})
    started = time.monotonic()
    verdict = demo.run_one("FX")
    assert time.monotonic() - started < 10
    assert verdict.status == INCOMPLETE and "展示被停止" in verdict.reason


# ---- 代碼審 r3(Phase 12 增量 2)----
def test_a_confirmation_whose_signing_failed_is_not_waited_for_or_called_unconfirmed():
    """[代碼審 r3 v3] 關窗時有人正在簽、後來簽失敗:驅動程式看到簽發失敗就不再等,原因照實寫
    「有人確認但簽發失敗」,不寫成沒有人確認。"""
    from datetime import UTC

    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))
    failed = _ConfirmStub(stop_during_wait=False, approved_at_close=True, sign_failed=True)
    with pytest.raises(ScenarioFailed, match="有人確認但簽發失敗"):
        driver_module._wait_for_confirmation(failed, request, 5)


# ---- 增量 3:前後比較表 ----
def _fake_comparison(rows=(("只填已完成", "pytest 結束代碼 0:2 passed", "擋下:缺 result"),)):
    import json

    payload = json.dumps({"rows": [dict(zip(("forgery", "without_verifier", "with_verifier"),
                                            row, strict=True)) for row in rows],
                          "note": "比的是有沒有機械驗證", "seconds": 1.5}, ensure_ascii=False)
    return [sys.executable, "-c", f"print({payload!r})"]


def _comparison(tmp_path):
    reader = StateReader(tmp_path / "state.db")
    try:
        return reader.comparison_run("demo-1"), reader.latest_verifier_run()
    finally:
        reader.close()


def test_a_full_run_records_the_comparison_after_the_verifier(tmp_path, state):
    """[S1041] 全部跑一次的最後一步(驗證器之後)產生前後比較表,記進展示狀態(每列兩欄、說明、
    花了幾秒)。"""
    demo = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 5, lambda _w: "好")})
    demo.verifier_command = ["true"]
    demo.comparison_command = _fake_comparison()
    demo.run_all(("FX",))
    comparison, verifier = _comparison(tmp_path)
    assert comparison.rows == (("只填已完成", "pytest 結束代碼 0:2 passed", "擋下:缺 result"),)
    # 花了幾秒用驅動程式自己量的,不採產生器自報的 1.5(代碼審 r1 s3)
    assert comparison.note == "比的是有沒有機械驗證" and comparison.seconds < 1.5
    assert comparison.generated_at >= verifier.verified_at


def test_a_comparison_that_fails_or_hangs_does_not_break_the_demo(tmp_path, state):
    """比較表產生失敗或逾時:照實寫「這次沒產生:原因」,整次展示照樣跑完;逾時連它起的孫行程一起收掉。"""
    marker = tmp_path / "grandchild.pid"
    demo = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 5, lambda _w: "好")})
    demo.verifier_command = ["true"]
    demo.comparison_command = [sys.executable, "-c", (
        "import subprocess, time\nchild = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\ntime.sleep(60)\n")]
    demo.comparison_timeout_seconds = 2
    started = time.monotonic()
    verdicts, _ = demo.run_all(("FX",))
    assert time.monotonic() - started < 30 and verdicts[0].status == DONE
    comparison, _ = _comparison(tmp_path)
    assert comparison.rows == () and comparison.note.startswith("這次沒產生:")
    assert "逾時" in comparison.note
    time.sleep(0.5)
    assert not _alive(int(marker.read_text()))
    failed = driver_module.run_comparison(["false"], "demo-2", 5)
    assert failed.rows == () and failed.note.startswith("這次沒產生:")
    unreadable = driver_module.run_comparison([sys.executable, "-c", "print('不是 JSON')"],
                                              "demo-2", 5)
    assert unreadable.rows == () and "讀不懂" in unreadable.note


def test_a_cancelled_demo_generates_no_comparison(tmp_path, state):
    demo = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 5, lambda _w: "好")})
    demo.comparison_command = _fake_comparison()
    demo.cancel()
    demo.run_all(("FX",))
    assert _comparison(tmp_path)[0] is None


# ---- 增量 3 代碼審 r1 ----
def test_the_comparison_generator_gets_only_the_whitelisted_environment(monkeypatch):
    """[S1003][代碼審 r1 s1/a2、r3 v2] 比較表產生器跟驗證器用同一套白名單環境(PATH、HOME、LANG、
    LC_ALL、LC_CTYPE),外面的金鑰與 PYTEST_、PYTHON 開頭的變數都帶不進去。"""
    monkeypatch.setenv("SOME_SECRET_TOKEN", "abc123")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p evil")
    listing = [sys.executable, "-c", (
        "import json, os\nprint(json.dumps({'rows': [], 'note': ','.join(sorted(os.environ)),"
        " 'seconds': 0}))")]
    keys = set(driver_module.run_comparison(listing, "d", 10).note.split(","))
    assert "LC_ALL" in keys
    assert keys <= {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}, keys
    assert driver_module.tool_environment() == {
        k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE") if k in os.environ}


def test_the_comparison_generator_is_the_repos_own_even_if_another_tools_package_exists(tmp_path):
    """[代碼審 r1 s1、r2 a1] 產生器從 repo 根以套件方式跑(-E -s -m tools.forgery_comparison,cwd 是
    repo 根);tools 是正式套件、repo 根排在匯入路徑第一項,別處(site-packages 形態的路徑)另有一個正式
    的 tools 套件也頂替不了。"""
    import subprocess

    command = driver_module.default_comparison_command()
    assert command[1:] == ["-E", "-s", "-X", "utf8", "-m", "tools.forgery_comparison"]
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True,
                   capture_output=True)
    python = venv / "bin" / "python"
    site = Path(subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()"
                                "['purelib'])"], check=True, capture_output=True,
                               text=True).stdout.strip())
    evil = site / "tools"
    evil.mkdir(parents=True)
    (evil / "__init__.py").write_text("", encoding="utf-8")
    (evil / "forgery_comparison.py").write_text(
        "print('{\"rows\": [], \"note\": \"hijacked\", \"seconds\": 0}')\n", encoding="utf-8")
    result = driver_module.run_comparison(driver_module.default_comparison_command(str(python)),
                                          "d", 120)
    assert result.note != "hijacked", result
    # 這個 venv 沒有 pytest:跑到的是 repo 那一份產生器,照實寫缺 pytest
    assert "環境缺 pytest" in result.note, result


@pytest.mark.parametrize(("printed", "reason"), [
    ("print('x' * 2_000_000)", "上限"),
    ("print('{\"rows\": [], \"note\": \"n\", \"seconds\": ' + '9' * 400 + '}')", None),
    ("print('[' * 200000)", "讀不懂"),
])
def test_an_oversized_or_odd_generator_output_is_recorded_not_raised(printed, reason):
    """[代碼審 r1 s3] 產生器的輸出設上限、任何讀不懂都記「這次沒產生」,不丟出去;花幾秒用驅動程式
    自己量的。"""
    result = driver_module.run_comparison([sys.executable, "-c", printed], "d", 30)
    if reason is None:
        assert result.note == "n" and result.seconds is not None and result.seconds < 30
    else:
        assert result.rows == () and result.note.startswith("這次沒產生:") and reason in result.note


# ---- 增量 3 代碼審 r2 ----
def test_the_tools_run_in_utf8_whatever_the_locale_says(monkeypatch):
    """[代碼審 r2 s2] 伺服器的語系設定彼此不一致(LANG 是 latin-1、LC_ALL 是 UTF-8):產生器照樣產出、
    中文不亂碼(子行程 -X utf8、驅動程式照 UTF-8 讀)。"""
    monkeypatch.setenv("LANG", "en_US.ISO8859-1")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    result = driver_module.run_comparison(driver_module.default_comparison_command(), "d", 120)
    assert len(result.rows) == 6, result.note
    assert result.rows[-1][0].startswith("conftest 偷改測試結果")
    assert all(row[2].startswith("擋下:") for row in result.rows[:5]), result.rows  # r3 v3
    assert "-X" in driver_module.default_verifier_command()
    assert driver_module.default_verifier_command()[1:3] == ["-X", "utf8"]


def test_bytes_that_are_not_utf8_do_not_stall_the_reader():
    """[代碼審 r2 s2] 輸出含不合法的位元組:讀的那一邊照樣讀完(換成替代字元),不會死在解碼、讓子行程
    寫到一半卡到逾時。"""
    started = time.monotonic()
    result = driver_module.run_comparison(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff' + b'a' * 300000)"],
        "d", 20)
    assert time.monotonic() - started < 10
    assert result.rows == () and "讀不懂" in result.note


# ---- 增量 3 代碼審 r3 ----
_CHILD_PRINTS_CHINESE = (
    "import subprocess, sys\n"
    "out = subprocess.run([sys.executable, '-c', 'print(\"無\")'],\n"
    "                     capture_output=True).stdout\n"
    "ok = out.strip() == '無'.encode('utf-8')\n"
    "print('通過' if ok else '擋下')\nprint('- 子行程印出 ' + repr(out))\n"
    "sys.exit(0 if ok else 1)\n")


def test_the_verifiers_own_children_see_a_consistent_locale(monkeypatch):
    """[代碼審 r3 v2] 伺服器的 LANG 是 latin-1、LC_ALL 是 UTF-8:驗證器再往下開的子行程也要拿到一致的
    語系(白名單照抄 LC_ALL、LC_CTYPE),印中文不變成跳脫字元、驗證器不誤判沒過。"""
    monkeypatch.setenv("LANG", "en_US.ISO8859-1")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    outcome = driver_module.run_verifier([sys.executable, "-c", _CHILD_PRINTS_CHINESE], "d", 60)
    assert outcome.passed, outcome.lines
    assert {"LC_ALL", "LC_CTYPE"} & set(driver_module._TOOL_VARIABLES) == {"LC_ALL", "LC_CTYPE"}


def test_a_verifier_that_prints_bytes_that_are_not_utf8_is_read_to_the_end():
    """[代碼審 r3 v3] 驗證器印出不合法的位元組:照樣讀完(換成替代字元),判定照結束代碼,不卡住。"""
    started = time.monotonic()
    outcome = driver_module.run_verifier(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\n' + b'a' * 300000"
         " + b'\\n'); sys.stdout.flush(); print('通過')"], "d", 20)
    assert time.monotonic() - started < 10
    assert outcome.passed and "\ufffd" in outcome.lines[0]
