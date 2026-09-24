"""執行迴圈的猝死點與時鐘偏移(照事故 F2、F3 端到端測試的組法):把要監控的方法換成「印出死點、
os._exit(9)」再呼叫正式入口——行程真的猝死,沒有例外、沒有回滾、沒有收尾。"""

import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from rtb.demo.faults.delivery import FaultPlan
from rtb.executor import runner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import Executor
from rtb.executor.inbox_store import utc_now

DEATH = "DEATH"


def _die(point: str) -> None:
    print(f"{DEATH} point={point}", file=sys.stderr, flush=True)
    os._exit(9)


def _before(owner: type, name: str, point: str) -> None:
    setattr(owner, name, lambda *_a, **_k: _die(point))


def _after(owner: type, name: str, point: str) -> None:
    original: Callable[..., Any] = getattr(owner, name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        _die(point)

    setattr(owner, name, wrapped)


CRASH_POINTS: dict[str, Callable[[str], None]] = {
    "before_dsp_call": lambda p: _before(DspClient, "write", p),
    "after_dsp_commit": lambda p: _after(DspClient, "write", p),
    "before_verification": lambda p: _before(Executor, "_verify", p),
    "after_terminal_commit": lambda p: _after(Executor, "_verify", p),
    "after_receiving": lambda p: _before(Executor, "_take", p),
}


def run(argv: list[str], plan: FaultPlan) -> int:
    if plan.crash_point is not None:
        CRASH_POINTS[plan.crash_point](plan.crash_point)
    shift = timedelta(seconds=plan.clock_offset_seconds)

    def clock() -> datetime:
        return utc_now() + shift

    return runner.run(argv, clock=clock)
