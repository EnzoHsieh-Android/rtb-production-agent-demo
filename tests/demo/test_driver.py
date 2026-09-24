"""一鍵展示驅動程式(Phase 12 增量 1):真的起行程跑情境,觀察系統紀錄、斷言預期處置。"""

import os
import threading
import time
from datetime import datetime
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
