"""故障套件本身的行為(代碼審 r1 t8):排程依序取、偏移復位;猝死點在方法前或後死;
執行迴圈時鐘偏移有套用。
猝死以結束代碼 9 真的死掉,由驅動程式的 F2(寫進平台之後)與 F3(剛拿起訊息)真跑測試守。"""

from datetime import timedelta

import pytest

from rtb.demo.faults import executor as executor_faults
from rtb.demo.faults.delivery import FaultPlan
from rtb.demo.faults.dsp import PlannedDsp
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import Executor
from rtb.executor.inbox_store import utc_now


def test_the_dsp_schedule_is_taken_in_order_and_the_offset_resets(tmp_path):
    plan = FaultPlan(role="dsp", dsp_plan=(("timeout_before_commit", 1.0),
                                          ("transient_5xx", 2.0)), clock_offset_seconds=10.0)
    server = PlannedDsp(tmp_path / "dsp.db", plan, 0.0, 0.0)
    try:
        taken = []
        for _ in range(3):
            taken.append((server.next_fault(), server._offset))
    finally:
        server.server_close()
    assert taken == [("timeout_before_commit", 11.0), ("transient_5xx", 12.0), (None, 10.0)]


class _Death(Exception):
    pass


@pytest.mark.parametrize(("point", "owner", "name", "after"), [
    ("before_dsp_call", DspClient, "write", False),
    ("after_dsp_commit", DspClient, "write", True),
    ("before_verification", Executor, "_verify", False),
    ("after_terminal_commit", Executor, "_verify", True),
    ("after_receiving", Executor, "_take", False),
])
def test_each_crash_point_dies_before_or_after_the_real_call(monkeypatch, point, owner, name,
                                                             after):
    calls = []
    monkeypatch.setattr(owner, name, lambda *_a, **_k: calls.append(name))

    def die(where):
        raise _Death(where)

    monkeypatch.setattr(executor_faults, "_die", die)
    executor_faults.CRASH_POINTS[point](point)

    with pytest.raises(_Death, match=point):
        getattr(owner, name)(object())
    assert calls == ([name] if after else [])


def test_the_executor_clock_offset_is_applied(monkeypatch):
    seen = {}

    def fake_run(argv, *, clock):
        seen["argv"], seen["clock"] = argv, clock
        return 0

    monkeypatch.setattr(executor_faults.runner, "run", fake_run)
    executor_faults.run(["--db", "x"], FaultPlan(role="executor", clock_offset_seconds=300.0))

    drift = seen["clock"]() - utc_now()
    assert timedelta(seconds=299) < drift < timedelta(seconds=301)
    assert seen["argv"] == ["--db", "x"]
