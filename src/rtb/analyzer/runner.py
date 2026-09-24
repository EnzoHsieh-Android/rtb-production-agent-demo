"""分析端驅動命令列(Phase 12 增量 1,正式入口):反覆領取待推進的任務,用正式的 DSP 用戶端與收件口
用戶端呼叫流程推進函式,直到收到停止訊號(或跑滿指定輪數)。跟測試用同一支推進函式、同一套租約;
這是正式程式,不帶任何故障手段([S1000])。

租約長度與呼叫逾時的機械守衛([S1001]):一步最多兩次讀 DSP 或一次送件,呼叫次數 乘 單次逾時 乘 2
要小於租約,否則一步還沒做完租約就到期、被別人接手,同一件工作會花兩次錢;不成立就拒絕啟動。
"""

import argparse
import math
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Protocol, TextIO

from rtb.analyzer import dsp_client, flow, inbox_client, instrumented, policy
from rtb.analyzer.task_store import LEASE_DURATION, TaskRow, TaskStore, ToolEndpoint
from rtb.domain.evidence import Evidence
from rtb.domain.task_state import TaskState

READY = "READY"
CALLS_PER_STEP = 2  # 一步最多兩次讀 DSP(廣告現況、成效指標)或一次送件
MIN_TIMEOUT_SECONDS = 0.1  # 比這小的逾時每次呼叫都失敗,等於永遠不推進
EXIT_UNSAFE_CONFIG = 7  # 跟執行端同一個代碼;2 是 argparse 參數錯誤的代碼,不能共用
BACKOFF_CAP_SECONDS = 10.0  # 沒有進展的任務最久隔這麼久再問一次


class StopFlag:
    """停止旗標:訊號處理器只設一個普通屬性、不碰任何鎖(代碼審 r1 a1:在處理器裡 set 一個
    threading.Event,碰上主執行緒正握著它的鎖就死結)。"""

    def __init__(self) -> None:
        self.requested = False

    def request(self) -> None:  # 不叫 set:維運套件的掃描照名字分類,會跟內建的 set() 撞名
        self.requested = True

    def is_set(self) -> bool:
        return self.requested


class _Stop(Protocol):
    def is_set(self) -> bool: ...


def default_owner() -> str:
    """租約擁有者:比照執行端用行程編號加啟動時間,重啟之後是新的擁有者。"""
    return f"analyzer-{os.getpid()}-{int(time.time())}"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False,
                                     description="分析端驅動:推進所有還沒結束的任務")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--dsp-url", required=True)
    parser.add_argument("--inbox-url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    parser.add_argument("--interval-seconds", type=float, default=0.5)
    parser.add_argument("--owner", default=None)
    return parser.parse_args(argv)


def _unsafe(args: argparse.Namespace) -> str | None:
    """[S1001] 逾時要是有限、夠大的正數,而且呼叫次數乘逾時乘 2 小於租約;間隔要是有限的非負數。
    NaN 跟任何數比都不成立,所以一律用「符合才放行」的寫法(代碼審 r1 d4)。"""
    t, lease = args.timeout_seconds, LEASE_DURATION.total_seconds()
    if not (math.isfinite(t) and t >= MIN_TIMEOUT_SECONDS and CALLS_PER_STEP * t * 2 < lease):
        return (f"拒絕啟動:逾時 {t} 秒不是 {MIN_TIMEOUT_SECONDS} 秒以上的有限數,或 "
                f"{CALLS_PER_STEP} 次呼叫乘逾時乘 2 不小於租約 {lease:.0f} 秒")
    if not (math.isfinite(args.interval_seconds) and args.interval_seconds >= 0):
        return f"拒絕啟動:間隔 {args.interval_seconds} 秒不是有限的非負數"
    return None


def _no_action_reason(task: TaskRow, evidence: tuple[Evidence, ...],
                      now: datetime) -> StrEnum | None:
    """正式接線:不提案原因跟決策函式同一套規則(沒有候選、空的允許清單,等於決策函式的判法)。"""
    return policy.explain(task, evidence, now, candidate=None,
                          allowed=policy.ValidatedCells.NONE)[1]


def _advance_one(store: TaskStore, task_id: str, args: argparse.Namespace,
                 now: datetime) -> tuple[TaskState, int] | None:
    """推進一步,回(推進後的狀態, 最新一列序號);讀到的列在推進前被別人動過就什麼都不做。"""
    row = store.latest(task_id)
    if row is None:
        return None
    source = instrumented.dsp_evidence_source(store, args.dsp_url, args.timeout_seconds)

    def read_evidence(task: TaskRow, now: datetime) -> tuple[Evidence, ...]:
        return source(task, now)

    send = inbox_client.make_client(args.inbox_url, args.timeout_seconds)
    lookup = dsp_client.make_operation_lookup(args.dsp_url, args.timeout_seconds)
    # 呼叫紀錄綁「呼叫當下讀到的那一列」:每一步都用當下的列重建,而且把那一列的序號交給推進函式
    state = flow.advance(
        store, task_id, read_evidence, policy.decide,
        instrumented.InstrumentedSubmit(store, send, ToolEndpoint.INBOX_SUBMIT, row), now,
        owner=args.owner,
        operation_lookup=instrumented.InstrumentedOperationLookup(
            store, lookup, ToolEndpoint.DSP_OPERATION, row),
        no_action_reason=_no_action_reason, expected_seq=row.seq)
    latest = store.latest(task_id)
    return state, (latest.seq if latest is not None else row.seq)


@dataclass
class _Backoff:
    """沒有進展(狀態與序號都沒變)的任務退避:每次沒進展等待加倍,上限 BACKOFF_CAP_SECONDS;
    一有進展就歸零(代碼審 r1 d7:等待中的任務每輪都寫兩列租約、一筆送件紀錄,表無上限長大)。"""

    seen: dict[str, tuple[TaskState, int]] = field(default_factory=dict)
    misses: dict[str, int] = field(default_factory=dict)
    due: dict[str, float] = field(default_factory=dict)

    def ready(self, task_id: str, now: float) -> bool:
        return self.due.get(task_id, 0.0) <= now

    def update(self, task_id: str, result: tuple[TaskState, int] | None, now: float,
               interval: float) -> None:
        if result is None or result != self.seen.get(task_id):
            self.seen[task_id] = result if result is not None else (TaskState.RECEIVED, 0)
            self.misses[task_id], self.due[task_id] = 0, now
            return
        self.misses[task_id] = self.misses.get(task_id, 0) + 1
        wait = min(max(interval, 0.05) * 2 ** self.misses[task_id], BACKOFF_CAP_SECONDS)
        self.due[task_id] = now + wait


def run(  # noqa: PLR0913 - 協作者都可替換,測試在行程內跑
    argv: list[str] | None = None, *, max_rounds: int | None = None,
    stop: _Stop | None = None, out: TextIO | None = None, err: TextIO | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    problem = _unsafe(args)
    if problem is not None:
        errors.write(problem + "\n")
        return EXIT_UNSAFE_CONFIG
    args.owner = args.owner or default_owner()
    stop = stop or StopFlag()
    store = TaskStore(args.db)
    backoff = _Backoff()
    try:
        print(READY, file=out or sys.stdout, flush=True)
        rounds = 0
        while not stop.is_set() and (max_rounds is None or rounds < max_rounds):
            for task_id in store.open_task_ids():
                if stop.is_set():  # 每件任務之前都看:停止延遲的上限是一步,不是一輪
                    break
                if not backoff.ready(task_id, monotonic()):
                    continue
                try:
                    result = _advance_one(store, task_id, args, clock())
                except Exception as problem_:  # 一件任務出事不停掉整個驅動;下一輪再試
                    errors.write(f"{task_id} 這一步沒有進展:{problem_!r}\n")
                    result = None
                backoff.update(task_id, result, monotonic(), args.interval_seconds)
            rounds += 1
            if not stop.is_set():
                sleep(args.interval_seconds)
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    stop = StopFlag()

    def ask_to_stop(_signal: int, _frame: FrameType | None) -> None:
        stop.request()

    signal.signal(signal.SIGTERM, ask_to_stop)
    signal.signal(signal.SIGINT, ask_to_stop)
    raise SystemExit(run(argv, stop=stop))


if __name__ == "__main__":
    main()
