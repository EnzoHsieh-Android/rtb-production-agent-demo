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
    for node in driver_module.F1_REQUIRED:
        assert node in nodes
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
        world.observed.extend(["x_write", "x_verify", "x_done"])
        world.require_path(["x_write", "x_unknown", "x_done"])
        return "不該到這裡"

    verdict = _driver(tmp_path, state, {"FX": Scenario("FX", "假", 20, failing)}).run_one("FX")

    assert verdict.status == INCOMPLETE
    assert "x_unknown" in verdict.reason
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
    """只有交叉核對會讀的那幾樣:平台寫入、預算、查詢、執行迴圈。"""

    def __init__(self, writes=(), budget=110, rows=(), exit_code=9):
        self._writes, self._budget, self._rows = list(writes), budget, list(rows)
        self.inbox_db = "inbox.db"
        self.executor = type("P", (), {"poll": lambda _self: exit_code})()

    def platform_writes(self, _campaign):
        return self._writes

    def budget(self, _campaign):
        return self._budget

    def query(self, _db, _sql, _params=()):
        return self._rows

    def watch(self, done, _limit):
        return done()


@pytest.mark.parametrize(("writes", "budget"), [
    ([("k", 110), ("k2", 110)], 110), ([("k", 110)], 121), ([], 100)])
def test_the_platform_truth_must_show_exactly_one_write(writes, budget):
    with pytest.raises(ScenarioFailed, match="平台真實狀態"):
        driver_module._applied_once(_Stub(writes, budget), "c1", 110)
    driver_module._applied_once(_Stub([("k", 110)], 110), "c1", 110)


def test_a_rejected_write_permission_marks_the_restart_incomplete():
    with pytest.raises(ScenarioFailed, match="寫入許可"):
        driver_module._no_rejected_permission(_Stub(rows=[(None,), ("capability_rejected",)]))
    driver_module._no_rejected_permission(_Stub(rows=[(None,)]))


@pytest.mark.parametrize(("rows", "ok"), [([(2,)], True), ([(1,)], False), ([(2,), (2,)], False)])
def test_the_message_must_have_been_delivered_twice(rows, ok):
    if ok:
        driver_module._delivered(_Stub(rows=rows), 2)
    else:
        with pytest.raises(ScenarioFailed, match="投遞次數"):
            driver_module._delivered(_Stub(rows=rows), 2)


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


def _small_f7(tmp_path, state, cap, approve=None):
    """F7 縮成 30 個廣告、門檻 124(放行 12 個)跑行為;正式情境的規模另外驗。"""
    run = driver_module.make_f7(campaigns=30, limit=124, workers=3, confirm_cap_seconds=cap)
    demo = _driver(tmp_path, state, {"F7": Scenario("F7", "F7", 3, run)})
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


def test_waiting_for_approval_does_not_count_against_the_f7_time_limit(tmp_path, state):
    """[S1008] 情境總時限 3 秒,等人確認 5 秒:等待不算進總時限;沒人確認就標「沒有人確認」、
    清掉確認表單。"""
    verdict, _ = _small_f7(tmp_path, state, cap=5)

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
    """[S1058] 平台上的放行要跟收件口記為完成的那一批一一對上、每個恰好加一成、數量等於門檻
    算出的。"""
    def world(writes, done):
        stub = _Stub()
        stub.all_platform_writes = lambda: writes
        stub.query = lambda _db, _sql, _params=(): [(c,) for c in done]
        return stub

    ok = world([("a", "update_budget", 110), ("b", "update_budget", 110)], ["a", "b"])
    driver_module._check_campaign_by_campaign(ok, ["a", "b", "c"], 2)
    for writes, done, passes in [
        ([("a", "update_budget", 110)], ["a", "b"], 1),  # 收件口說完成、平台沒寫
        ([("a", "update_budget", 120)], ["a"], 1),  # 加額不對
        ([("a", "update_budget", 110), ("a", "update_budget", 110)], ["a"], 1),  # 寫兩次
        ([("a", "update_budget", 110), ("c", "update_budget", 110)], ["a", "c"], 1),  # 數量不對
    ]:
        with pytest.raises(ScenarioFailed):
            driver_module._check_campaign_by_campaign(world(writes, done), ["a", "b", "c"],
                                                      passes)


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
    stub = _Stub()
    stub.all_platform_writes = lambda: [(f"k{i}", "update_budget", b) for i, b in enumerate(writes)]
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


def test_a_timed_out_scenario_cannot_rewrite_its_status(tmp_path, state, monkeypatch):
    """時限到了之後情境本體走到等人確認:不能再寫確認請求,也不能把「沒跑完」改寫回進行中。"""
    from datetime import UTC, datetime

    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))
    monkeypatch.setattr(driver_module, "_earliest_waiting", lambda _world: request)

    def slow(world):
        time.sleep(1.5)
        driver_module._wait_for_confirmation(world, 5)
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
    def __init__(self, stop_during_wait):
        self.code, self.stop = "F7", threading.Event()
        self.marks, self.cleared = [], []
        self._stop_during_wait = stop_during_wait
        stub = self

        class _State:
            def set_confirmation(self, *_a):
                stub.marks.append("request")

            def mark_status(self, _code, status):
                stub.marks.append(status)

            def clear_confirmation(self):
                stub.cleared.append(True)

        self.state = _State()

    def wait_paused(self, _done, _limit, _on_poll=None):
        if self._stop_during_wait:
            self.stop.set()
        return False


def test_a_stopped_scenario_does_not_ask_for_confirmation(monkeypatch):
    world = _ConfirmStub(stop_during_wait=False)
    world.stop.set()
    monkeypatch.setattr(driver_module, "_earliest_waiting", lambda _w: pytest.fail("不該查"))
    with pytest.raises(driver_module.ScenarioStopped):
        driver_module._wait_for_confirmation(world, 5)
    assert world.marks == []


def test_a_stop_during_the_confirmation_wait_does_not_mark_running_again(monkeypatch):
    from datetime import UTC

    from rtb.demo.state_store import ConfirmationRequest

    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1), (("廣告", "c1"),))
    monkeypatch.setattr(driver_module, "_earliest_waiting", lambda _w: request)
    world = _ConfirmStub(stop_during_wait=True)

    assert driver_module._wait_for_confirmation(world, 5) is False
    assert world.marks == ["request", driver_module.AWAITING_CONFIRMATION]
    assert world.cleared == [True]
