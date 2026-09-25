"""分析端驅動命令列(Phase 12 增量 1,正式入口):反覆領取待推進的任務,用正式的 DSP 用戶端與收件口
用戶端呼叫流程推進函式,直到收到停止訊號(或跑滿指定輪數)。跟測試用同一支推進函式、同一套租約;
這是正式程式,不帶任何故障手段([S1000])。

租約長度與呼叫逾時的機械守衛([S1001]):一步最多兩次讀 DSP 或一次送件,呼叫次數 乘 單次逾時 乘 2
要小於租約,否則一步還沒做完租約就到期、被別人接手,同一件工作會花兩次錢;不成立就拒絕啟動。

AI 參與決策(Phase 13 增量 2,計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]):帶 `--ai-judge` 才開,另收
`--demo-id`、`--ledger`、`--recordings-dir`、`--batch-id`、`--hold-submit`;沒開就是原本的行為,守衛照舊
只看 [S1001]。開了之後:
- 守衛兩條都要成立,逐項加總、數字都取自真常數([S1113]):蒐證那一步(取租約等鎖、最多 6 次讀取
  各自的 DSP 逾時加呼叫紀錄等鎖、提交等鎖)與 AI 那一步(續租讀時鐘後的模型逾時、行程群組清理、
  花費帳等鎖、提交等鎖)都要小於租約。
- 模式經模型閘道在啟動時判一次,之後每一輪沿用([S1137]);即時加錄製沒帶批次編號、或錄製目錄混了別批,
  拒絕啟動([S1142])。印出就緒之後、取任何租約之前做一次登入預檢;沒過整趟用程式規則([S1160])。
- 模型呼叫在這個行程的主執行緒;停止訊號在呼叫途中轉成的例外由模型閘道轉手,這裡明接:流程層已用目前
  收據放掉租約、這一步沒寫,以 0 結束([S1116])。每一步都把時鐘函式與停止旗標的查詢函式傳下去。
"""

import argparse
import math
import os
import signal
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Protocol, TextIO

from rtb.analyzer import (
    ai_judge,
    dsp_client,
    flow,
    inbox_client,
    instrumented,
    investigation,
    modelgate,
    policy,
)
from rtb.analyzer.task_store import LEASE_DURATION, TaskRow, TaskStore, ToolEndpoint
from rtb.domain.evidence import Evidence
from rtb.domain.task_state import TaskState
from rtb.stepbudget import (
    CALLS_PER_STEP,
    DEFAULT_TIMEOUT_SECONDS,
    ai_step_worst_seconds,
    collect_step_worst_seconds,
)

READY = "READY"
MIN_TIMEOUT_SECONDS = 0.1  # 比這小的逾時每次呼叫都失敗,等於永遠不推進
EXIT_UNSAFE_CONFIG = 7  # 跟執行端同一個代碼;2 是 argparse 參數錯誤的代碼,不能共用
BACKOFF_CAP_SECONDS = 10.0  # 沒有進展的任務最久隔這麼久再問一次
__all__ = ["CALLS_PER_STEP", "DEFAULT_TIMEOUT_SECONDS", "READY", "StopFlag", "main", "run"]
REST_SLICE_SECONDS = 0.1  # 每輪之間的休息切成小段,每段之間看停止旗標


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
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--interval-seconds", type=float, default=0.5)
    parser.add_argument("--owner", default=None)
    # Phase 13 增量 2:AI 參與決策(沒帶 --ai-judge 就是原本的行為)
    parser.add_argument("--ai-judge", action="store_true", help="分析那一步讓 AI 參與決策")
    parser.add_argument("--demo-id", help="展示編號;即時模式必填")
    parser.add_argument("--ledger", type=Path, help="只在錄製模式能用:花費帳換到別的路徑")
    parser.add_argument("--recordings-dir", type=Path,
                        help="錄製目錄(預設專案根的 recordings/model)")
    parser.add_argument("--batch-id", help="錄製批次;即時加錄製模式必填")
    parser.add_argument("--hold-submit", default="",
                        help="只判不送的廣告編號(逗號分隔):判提案也不送件,以考題結束結案")
    return parser.parse_args(argv)


def _unsafe(args: argparse.Namespace) -> str | None:
    """[S1001] 逾時要是有限、夠大的正數,而且呼叫次數乘逾時乘 2 小於租約;間隔要是非負數、不超過
    租約(代碼審 r2 v3:1e308 原本過了守衛、印了就緒才在休息時崩掉)。NaN 跟任何數比都不成立,所以
    一律用「符合才放行」的寫法(代碼審 r1 d4)。"""
    t, lease = args.timeout_seconds, LEASE_DURATION.total_seconds()
    if not (math.isfinite(t) and t >= MIN_TIMEOUT_SECONDS and CALLS_PER_STEP * t * 2 < lease):
        return (f"拒絕啟動:逾時 {t} 秒不是 {MIN_TIMEOUT_SECONDS} 秒以上的有限數,或 "
                f"{CALLS_PER_STEP} 次呼叫乘逾時乘 2 不小於租約 {lease:.0f} 秒")
    if not 0 <= args.interval_seconds <= lease:
        return f"拒絕啟動:間隔 {args.interval_seconds} 秒不是 0 到租約 {lease:.0f} 秒之間的數"
    return _unsafe_ai(args, lease) if args.ai_judge else _unsafe_hold(args)


def _unsafe_hold(args: argparse.Namespace) -> str | None:
    if args.hold_submit:
        return "拒絕啟動:--hold-submit 只在 --ai-judge 時可用(只判不送是 AI 考題用的)"
    return None


def _unsafe_ai(args: argparse.Namespace, lease: float) -> str | None:
    """[S1113] 開 AI 時兩條都逐項加總、都要小於租約;數字都取自真常數。"""
    collect = collect_step_worst_seconds(args.timeout_seconds, investigation.MAX_COLLECT_READS)
    if not collect < lease:
        return (f"拒絕啟動:開 AI 時蒐證那一步最壞 {collect:.1f} 秒(取租約等鎖、"
                f"{investigation.MAX_COLLECT_READS} 次讀取各自的逾時加呼叫紀錄等鎖、提交等鎖)"
                f"不小於租約 {lease:.0f} 秒")
    judge = ai_step_worst_seconds()
    if not judge < lease:
        return f"拒絕啟動:AI 那一步最壞 {judge:.1f} 秒不小於租約 {lease:.0f} 秒"
    if investigation.hold_list(args.hold_submit) is None:
        return "拒絕啟動:--hold-submit 要是逗號分隔的廣告編號"
    return None


def _no_action_reason(task: TaskRow, evidence: tuple[Evidence, ...],
                      now: datetime) -> StrEnum | None:
    """正式接線:不提案原因跟決策函式同一套規則(沒有候選、空的允許清單,等於決策函式的判法)。"""
    return policy.explain(task, evidence, now, candidate=None,
                          allowed=policy.ValidatedCells.NONE)[1]


@dataclass(frozen=True)
class _Ai:
    """開了 AI 決策時每一步要傳下去的:AI 決策函式與時鐘函式(續租拿到鎖之後才讀)。"""

    judge: ai_judge.Judge
    clock: Callable[[], datetime]


def _advance_one(store: TaskStore, task_id: str, args: argparse.Namespace,
                 now: datetime, ai: _Ai | None = None) -> tuple[TaskState, int] | None:
    """推進一步,回(推進後的狀態, 最新一列序號);讀到的列在推進前被別人動過就什麼都不做。"""
    row = store.latest(task_id)
    if row is None:
        return None
    plain = instrumented.dsp_evidence_source(store, args.dsp_url, args.timeout_seconds)
    probing = instrumented.investigation_source(store, args.dsp_url, args.timeout_seconds)

    def read_evidence(task: TaskRow, now: datetime) -> tuple[Evidence, ...] | flow.EvidenceBatch:
        return plain(task, now) if ai is None else probing(task, now)

    send = inbox_client.make_client(args.inbox_url, args.timeout_seconds)
    lookup = dsp_client.make_operation_lookup(args.dsp_url, args.timeout_seconds)
    # 呼叫紀錄綁「呼叫當下讀到的那一列」:每一步都用當下的列重建,而且把那一列的序號交給推進函式
    state = flow.advance(
        store, task_id, read_evidence, policy.decide,
        instrumented.InstrumentedSubmit(store, send, ToolEndpoint.INBOX_SUBMIT, row), now,
        owner=args.owner,
        operation_lookup=instrumented.InstrumentedOperationLookup(
            store, lookup, ToolEndpoint.DSP_OPERATION, row),
        no_action_reason=_no_action_reason, expected_seq=row.seq,
        ai_decide=None if ai is None else ai.judge, clock=None if ai is None else ai.clock)
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
        self.failed(task_id, now, interval)

    def failed(self, task_id: str, now: float, interval: float) -> None:
        """沒有進展:這一步出例外也算(代碼審 r2 n4:例外原本讓退避歸零,出錯的任務每輪都推)。"""
        self.misses[task_id] = self.misses.get(task_id, 0) + 1
        wait = min(max(interval, 0.05) * 2 ** self.misses[task_id], BACKOFF_CAP_SECONDS)
        self.due[task_id] = now + wait


GateOpener = ai_judge.GateOpener


def _open_ai_gate(args: argparse.Namespace, environ: Mapping[str, str], open_gate: GateOpener,
                  errors: TextIO) -> modelgate.Gate | None:
    """啟動時經模型閘道判一次模式([S1137]);即時加錄製沒帶批次編號、或錄製目錄混了別批就拒絕
    ([S1142])。拒絕時印原因、回 None,什麼模型都沒呼叫。"""
    try:
        gate = ai_judge.open_investigation_gate(
            environ, demo_id=args.demo_id, ledger=args.ledger, recordings=args.recordings_dir,
            batch_id=args.batch_id, open_gate=open_gate)
    except (modelgate.UnknownModel, modelgate.GateRefused) as refused:
        errors.write(f"拒絕啟動:{refused}\n")
        return None
    if gate.mode is modelgate.Mode.LIVE and gate.settings.record:
        if not args.batch_id:
            errors.write("拒絕啟動:即時加錄製模式要帶 --batch-id\n")
            return None
        try:
            gate.check_recordings()
        except modelgate.GateRefused as mixed:
            errors.write(f"拒絕啟動:錄製目錄不能開錄({mixed})\n")
            return None
    for notice in gate.notices:
        errors.write(notice + "\n")
    return gate


def run(  # noqa: PLR0913 - 協作者都可替換,測試在行程內跑
    argv: list[str] | None = None, *, max_rounds: int | None = None,
    stop: _Stop | None = None, out: TextIO | None = None, err: TextIO | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
    environ: Mapping[str, str] | None = None, open_gate: GateOpener = modelgate.open_gate,
) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    problem = _unsafe(args)
    if problem is not None:
        errors.write(problem + "\n")
        return EXIT_UNSAFE_CONFIG
    gate = None
    if args.ai_judge:
        gate = _open_ai_gate(args, os.environ if environ is None else environ, open_gate, errors)
        if gate is None:
            return EXIT_UNSAFE_CONFIG
    args.owner = args.owner or default_owner()
    stop = stop or StopFlag()
    store = TaskStore(args.db)
    try:
        print(READY, file=out or sys.stdout, flush=True)
        ai = None if gate is None else _Ai(_judge_for(args, gate, stop, errors), clock)
        _loop(store, args, ai, _Loop(stop, max_rounds, clock, sleep, monotonic, errors))
        return 0
    except modelgate.CallTerminated:  # 模型呼叫途中收到停止:流程層已放掉租約、這一步沒寫
        return 0
    finally:
        store.close()


def _judge_for(args: argparse.Namespace, gate: modelgate.Gate, stop: _Stop,
               errors: TextIO) -> ai_judge.Judge:
    """印出就緒之後、取任何租約之前做一次登入預檢([S1160]);沒過就整趟用程式規則。"""
    checked = gate.preflight_login()
    if checked.outcome is modelgate.Preflight.FAILED:
        errors.write(f"登入預檢沒過,這一趟改由程式規則決定:{checked.reason}\n")
    held = investigation.hold_list(args.hold_submit) or frozenset()
    return ai_judge.Judge(ai_judge.gate_complete(gate),
                          preflight_ok=checked.outcome is not modelgate.Preflight.FAILED,
                          stop_requested=stop.is_set, hold=held)


@dataclass(frozen=True)
class _Loop:
    stop: _Stop
    max_rounds: int | None
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]
    errors: TextIO


def _loop(store: TaskStore, args: argparse.Namespace, ai: _Ai | None, loop: _Loop) -> None:
    backoff, rounds, stop, monotonic = _Backoff(), 0, loop.stop, loop.monotonic
    while not stop.is_set() and (loop.max_rounds is None or rounds < loop.max_rounds):
        for task_id in store.open_task_ids():
            if stop.is_set():  # 每件任務之前都看:停止延遲的上限是一步,不是一輪
                break
            if not backoff.ready(task_id, monotonic()):
                continue
            try:
                result = (_advance_one(store, task_id, args, loop.clock()) if ai is None
                          else _advance_one(store, task_id, args, loop.clock(), ai))
            except Exception as problem_:  # 一件任務出事不停掉整個驅動;照樣退避,之後再試
                loop.errors.write(f"{task_id} 這一步沒有進展:{problem_!r}\n")
                backoff.failed(task_id, monotonic(), args.interval_seconds)
                continue
            backoff.update(task_id, result, monotonic(), args.interval_seconds)
        rounds += 1
        _rest(stop, args.interval_seconds, loop.sleep, monotonic)


def _rest(stop: _Stop, seconds: float, sleep: Callable[[float], None],
          monotonic: Callable[[], float]) -> None:
    """每輪之間的休息:切成小段、每段之間看停止旗標。time.sleep 被訊號打斷後會把剩下的睡完
    (PEP 475),整段睡的話 SIGTERM 要等滿一個間隔(代碼審 r2 v3)。"""
    deadline = monotonic() + seconds
    while not stop.is_set():
        left = deadline - monotonic()
        if left <= 0:
            return
        sleep(min(REST_SLICE_SECONDS, left))


def main(argv: list[str] | None = None) -> None:
    stop = StopFlag()

    def ask_to_stop(_signal: int, _frame: FrameType | None) -> None:
        stop.request()

    signal.signal(signal.SIGTERM, ask_to_stop)
    signal.signal(signal.SIGINT, ask_to_stop)
    raise SystemExit(run(argv, stop=stop))


if __name__ == "__main__":
    main()
