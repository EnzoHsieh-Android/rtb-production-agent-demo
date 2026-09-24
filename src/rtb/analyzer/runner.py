"""分析端驅動命令列(Phase 12 增量 1,正式入口):反覆領取待推進的任務,用正式的 DSP 用戶端與收件口
用戶端呼叫流程推進函式,直到收到停止訊號(或跑滿指定輪數)。跟測試用同一支推進函式、同一套租約;
這是正式程式,不帶任何故障手段([S1000])。

租約長度與呼叫逾時的機械守衛([S1001]):一步最多兩次讀 DSP 或一次送件,呼叫次數 乘 單次逾時 乘 2
要小於租約,否則一步還沒做完租約就到期、被別人接手,同一件工作會花兩次錢;不成立就拒絕啟動。
"""

import argparse
import signal
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import TextIO

from rtb.analyzer import dsp_client, flow, inbox_client, instrumented, policy
from rtb.analyzer.task_store import LEASE_DURATION, TaskRow, TaskStore, ToolEndpoint
from rtb.domain.evidence import Evidence

READY = "READY"
CALLS_PER_STEP = 2  # 一步最多兩次讀 DSP(廣告現況、成效指標)或一次送件
EXIT_UNSAFE_CONFIG = 2


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分析端驅動:推進所有還沒結束的任務")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--dsp-url", required=True)
    parser.add_argument("--inbox-url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    parser.add_argument("--interval-seconds", type=float, default=0.5)
    parser.add_argument("--owner", default="analyzer-runner")
    return parser.parse_args(argv)


def _no_action_reason(task: TaskRow, evidence: tuple[Evidence, ...],
                      now: datetime) -> StrEnum | None:
    """正式接線:不提案原因跟決策函式同一套規則(沒有候選、空的允許清單,等於決策函式的判法)。"""
    return policy.explain(task, evidence, now, candidate=None,
                          allowed=policy.ValidatedCells.NONE)[1]


def _advance_one(store: TaskStore, task_id: str, args: argparse.Namespace,
                 now: datetime) -> None:
    row = store.latest(task_id)
    if row is None:
        return
    source = instrumented.dsp_evidence_source(store, args.dsp_url, args.timeout_seconds)

    def read_evidence(task: TaskRow, now: datetime) -> tuple[Evidence, ...]:
        return source(task, now)

    send = inbox_client.make_client(args.inbox_url, args.timeout_seconds)
    lookup = dsp_client.make_operation_lookup(args.dsp_url, args.timeout_seconds)
    # 呼叫紀錄綁「呼叫當下讀到的那一列」:每一步都用當下的列重建
    flow.advance(
        store, task_id, read_evidence, policy.decide,
        instrumented.InstrumentedSubmit(store, send, ToolEndpoint.INBOX_SUBMIT, row), now,
        owner=args.owner,
        operation_lookup=instrumented.InstrumentedOperationLookup(
            store, lookup, ToolEndpoint.DSP_OPERATION, row),
        no_action_reason=_no_action_reason)


def run(
    argv: list[str] | None = None, *, max_rounds: int | None = None,
    stop: threading.Event | None = None, out: TextIO | None = None, err: TextIO | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    if CALLS_PER_STEP * args.timeout_seconds * 2 >= LEASE_DURATION.total_seconds():
        errors.write(f"拒絕啟動:{CALLS_PER_STEP} 次呼叫 乘 逾時 {args.timeout_seconds} 秒 乘 2 "
                     f"不小於租約 {LEASE_DURATION.total_seconds():.0f} 秒\n")
        return EXIT_UNSAFE_CONFIG
    stop = stop or threading.Event()
    store = TaskStore(args.db)
    try:
        print(READY, file=out or sys.stdout, flush=True)
        rounds = 0
        while not stop.is_set() and (max_rounds is None or rounds < max_rounds):
            for task_id in store.open_task_ids():
                try:
                    _advance_one(store, task_id, args, clock())
                except Exception as problem:  # 一件任務出事不停掉整個驅動;下一輪再試
                    errors.write(f"{task_id} 這一步沒有進展:{problem!r}\n")
            rounds += 1
            stop.wait(args.interval_seconds)
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    stop = threading.Event()

    def ask_to_stop(_signal: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, ask_to_stop)
    signal.signal(signal.SIGINT, ask_to_stop)
    raise SystemExit(run(argv, stop=stop))


if __name__ == "__main__":
    main()
