"""展示啟動器(Phase 12 增量 1):用子行程起各角色,環境一律從白名單組;要排故障的子行程改經
故障啟動器(`rtb.demo.launcher.child`)跑,故障設定檔與一次性隨機值只交給那一個子行程。

全庫只有這個子目錄准匯入故障套件(這裡的 ruff.toml 比上一層少那一條禁令,另有測試比對兩份只差
這一條)。展示的其他模組經這裡的 `FaultRequest`、`write_fault_plan`、`start` 排故障。
"""

import contextlib
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import rtb
from rtb.capabilitykit import APPROVAL_KEY_ENV, AUDIT_KEY_ENV, KEY_ENV
from rtb.demo.faults.delivery import ROOT_MARKER, FaultPlan, prepare_root, write_plan
from rtb.demo.keys import DemoKeys
from rtb.stepbudget import CALLS_PER_STEP, DEFAULT_TIMEOUT_SECONDS, ai_stop_grace_seconds

SRC = str(Path(rtb.__file__).resolve().parents[1])  # 專案沒有安裝成套件,子行程靠它找程式
# 子行程一律 python -P:不把工作目錄(展示根目錄)放進 sys.path,根目錄裡的同名檔蓋不掉標準函式庫
FAULT_NONCE_ENV = "RTB_DEMO_FAULT_NONCE"
EXIT_FAULT_REFUSED = 3
STARTUP_SECONDS = 20.0
STOP_SECONDS = 5.0
_BASICS = ("PATH", "HOME", "LANG", "USER")
_MODEL_VARIABLES = ("RTB_MODEL_LIVE", "RTB_MODEL", "RTB_MODEL_RECORD")

__all__ = ["EXIT_FAULT_REFUSED", "FAULT_NONCE_ENV", "ROOT_MARKER", "FaultRequest", "Role",
           "child_env", "command_for", "prepare_root", "start", "stop_grace_seconds",
           "write_fault_plan"]


class Role(StrEnum):
    DSP = "dsp"
    INBOX = "inbox"
    EXECUTOR = "executor"
    ANALYZER = "analyzer"
    MODEL_ENTRY = "model_entry"


_ROLE_KEYS: dict[Role, tuple[str, ...]] = {
    Role.DSP: (KEY_ENV, AUDIT_KEY_ENV),
    Role.EXECUTOR: (KEY_ENV, APPROVAL_KEY_ENV),
}
_MODULES: dict[Role, str] = {
    Role.DSP: "rtb.dsp.server",
    Role.INBOX: "rtb.executor.inbox_server",
    Role.EXECUTOR: "rtb.executor.runner",
    Role.ANALYZER: "rtb.analyzer.runner",
}
# 啟動成功時子行程印的第一行開頭:伺服器印連接埠,迴圈印就緒
_READY_PREFIX = {Role.DSP: "PORT=", Role.INBOX: "PORT=", Role.EXECUTOR: "READY",
                 Role.ANALYZER: "READY"}


@dataclass(frozen=True)
class FaultRequest:
    """展示其他模組排故障用的請求;啟動器把它交給故障套件寫成設定檔。"""

    role: Role
    dsp_plan: tuple[tuple[str, float], ...] = ()
    clock_offset_seconds: float = 0.0
    crash_point: str | None = None


def child_env(role: Role, keys: DemoKeys, *, user_env: Mapping[str, str],
              fault_nonce: str | None = None) -> dict[str, str]:
    """子行程環境白名單([S1003]):基本四樣照使用者環境有的才帶、PYTHONPATH 固定為專案 src、
    該角色的金鑰;模型入口另帶三個模型變數;排了故障的那一個子行程另帶一次性隨機值。"""
    env = {name: user_env[name] for name in _BASICS if name in user_env}
    env["PYTHONPATH"] = SRC
    env.update({name: keys.text(name) for name in _ROLE_KEYS.get(role, ())})
    if role is Role.MODEL_ENTRY:
        env.update({name: user_env[name] for name in _MODEL_VARIABLES if name in user_env})
    if fault_nonce is not None:
        env[FAULT_NONCE_ENV] = fault_nonce
    return env


def write_fault_plan(root: Path, request: FaultRequest) -> tuple[Path, str]:
    return write_plan(root, FaultPlan(role=request.role.value, dsp_plan=request.dsp_plan,
                                      clock_offset_seconds=request.clock_offset_seconds,
                                      crash_point=request.crash_point))


def command_for(role: Role, args: Sequence[str], keys: DemoKeys, *, root: Path,
                faults: FaultRequest | None,
                user_env: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    if role not in _MODULES:
        raise ValueError(f"{role} 沒有固定的正式入口")
    if faults is None:
        return ([sys.executable, "-P", "-m", _MODULES[role], *args],
                child_env(role, keys, user_env=user_env))
    if faults.role is not role:
        raise ValueError(f"故障是排給 {faults.role} 的,不是 {role}")
    config, nonce = write_fault_plan(root, faults)
    return ([sys.executable, "-P", "-m", "rtb.demo.launcher.child", role.value, str(config),
             "--",
             *args], child_env(role, keys, user_env=user_env, fault_nonce=nonce))


def stop_grace_seconds(role: Role, args: Sequence[str]) -> float:
    """收到 SIGTERM 之後等多久才硬殺。分析端會做完手上這一步(最多兩次呼叫,各自有逾時)才停,給它
    兩倍逾時再加一秒,不在一步中途硬殺(代碼審 r2 n3);其他角色照固定時限。帶 --ai-judge 時(Phase 13
    [S1136])至少再給「續租等鎖加上續租後 AI 那一步的最壞耗時」,用跟分析端守衛同一組常數算。"""
    if role is not Role.ANALYZER:
        return STOP_SECONDS
    timeout = DEFAULT_TIMEOUT_SECONDS
    if "--timeout-seconds" in args:
        timeout = float(args[list(args).index("--timeout-seconds") + 1])
    grace = max(STOP_SECONDS, CALLS_PER_STEP * timeout + 1)
    return max(grace, ai_stop_grace_seconds()) if "--ai-judge" in args else grace


class StartFailed(Exception):
    """子行程沒在時限內印出就緒那一行(起不來、拒絕啟動、第一行不對或太慢)。"""


class Process:
    """一個已啟動的子行程:獨立的行程群組,stop 連同它開的孫行程一起結束。"""

    def __init__(self, popen: subprocess.Popen[str], first_line: str,
                 reader: threading.Thread | None = None,
                 grace_seconds: float = STOP_SECONDS) -> None:
        self._popen, self._reader = popen, reader
        self.grace_seconds = grace_seconds
        self.first_line = first_line
        self.pid = popen.pid
        port = first_line.removeprefix("PORT=") if first_line.startswith("PORT=") else None
        self.url = None if port is None else f"http://127.0.0.1:{port}"

    def poll(self) -> int | None:
        return self._popen.poll()

    def wait(self, timeout: float) -> int:
        return self._popen.wait(timeout)

    def _group_alive(self) -> bool:
        self._popen.poll()  # 領頭已經結束就先收掉,不讓殭屍把群組看成還有人
        try:
            os.killpg(self._popen.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _wait_group(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._group_alive():
                return True
            time.sleep(0.05)
        return not self._group_alive()

    def _signal_group(self, signum: int) -> None:
        # macOS 對「只剩沒被收屍的領頭」的群組回 EPERM 而不是 ESRCH(代碼審 r2 o2/v1):兩種都不丟,
        # 群組清空沒有由 _group_alive 判
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self._popen.pid, signum)

    def stop(self, grace_seconds: float | None = None) -> None:
        """不論領頭死活都對整個群組送 SIGTERM;期限到了群組還有人就一律 SIGKILL,確認群組清空之後
        才關標準輸出(孫行程握著管線寫端時先關會卡住,代碼審 r1 l1/x1)。領頭已經結束就先收屍。"""
        grace_seconds = self.grace_seconds if grace_seconds is None else grace_seconds
        self._popen.poll()
        self._signal_group(signal.SIGTERM)
        if not self._wait_group(grace_seconds):
            self._signal_group(signal.SIGKILL)
            self._wait_group(grace_seconds)
        if self._popen.poll() is None:
            self._popen.wait(grace_seconds)
        if self._reader is not None:
            self._reader.join(grace_seconds)  # 寫端都關了,讀取執行緒讀到結尾就結束
        if self._popen.stdout is not None and (self._reader is None
                                               or not self._reader.is_alive()):
            self._popen.stdout.close()


def _reader_for(popen: subprocess.Popen[str]) -> tuple[threading.Thread, queue.Queue[str]]:
    lines: queue.Queue[str] = queue.Queue()

    def read() -> None:
        if popen.stdout is not None:
            for line in popen.stdout:
                lines.put(line.strip())
        lines.put(_EOF)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread, lines


_EOF = "\x00EOF"


def _first_line(popen: subprocess.Popen[str], lines: queue.Queue[str], prefix: str,
                seconds: float) -> str:
    """一個總期限(不是每一行重新計時);讀到結尾就取結束代碼;第一行不是預期的開頭直接判失敗
    (代碼審 r1 l5)。"""
    deadline = time.monotonic() + seconds
    try:
        line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
    except queue.Empty as slow:
        raise StartFailed(f"{seconds:.0f} 秒內沒有就緒") from slow
    if line == _EOF:
        try:
            code = popen.wait(max(0.1, min(1.0, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            raise StartFailed("標準輸出關掉了,行程卻還沒結束") from None
        raise StartFailed(f"子行程結束了,結束代碼 {code}")
    if not line.startswith(prefix):
        raise StartFailed(f"第一行不是就緒訊息:{line[:80]!r}")
    return line


def _spawn(command: Sequence[str], env: Mapping[str, str], cwd: Path, prefix: str,  # noqa: PLR0913 - 起行程要的每一樣
           log_path: Path, *, startup_seconds: float = STARTUP_SECONDS,
           grace_seconds: float = STOP_SECONDS) -> Process:
    with open(log_path, "a", encoding="utf-8") as log:  # 子行程拿到自己的一份描述子
        popen = subprocess.Popen(list(command), env=dict(env), cwd=cwd, stdout=subprocess.PIPE,  # noqa: S603 - 指令是固定的 python 模組加參數
                                 stderr=log, text=True, start_new_session=True)
    reader, lines = _reader_for(popen)
    try:
        first = _first_line(popen, lines, prefix, startup_seconds)
    except StartFailed:
        Process(popen, "", reader).stop()
        raise
    return Process(popen, first, reader, grace_seconds)


def start(role: Role, args: Sequence[str], keys: DemoKeys, *, root: Path,
          faults: FaultRequest | None, user_env: Mapping[str, str]) -> Process:
    command, env = command_for(role, args, keys, root=root, faults=faults, user_env=user_env)
    log = root / f"{role.value}-{os.getpid()}-{threading.get_ident()}.log"
    return _spawn(command, env, root, _READY_PREFIX[role], log,
                  grace_seconds=stop_grace_seconds(role, args))
