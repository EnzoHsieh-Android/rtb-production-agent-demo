"""一鍵展示驅動程式(Phase 12 增量 1):真的起行程跑情境,觀察系統紀錄、斷言預期處置。"""

import os
import threading
import time
from datetime import datetime

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
