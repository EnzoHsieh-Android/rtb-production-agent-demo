"""展示啟動器(Phase 12 增量 1):用子行程起各角色,環境一律從白名單組;要排故障的子行程改經
故障啟動器(`rtb.demo.launcher.child`)跑,故障設定檔與一次性隨機值只交給那一個子行程。

全庫只有這個子目錄准匯入故障套件(這裡的 ruff.toml 比上一層少那一條禁令,另有測試比對兩份只差
這一條)。展示的其他模組經這裡的 `FaultRequest`、`write_fault_plan`、`start` 排故障。
"""

import os
import queue
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import rtb
from rtb.capabilitykit import APPROVAL_KEY_ENV, AUDIT_KEY_ENV, KEY_ENV
from rtb.demo.faults.delivery import ROOT_MARKER, FaultPlan, prepare_root, write_plan
from rtb.demo.keys import DemoKeys

SRC = str(Path(rtb.__file__).resolve().parents[1])  # 專案沒有安裝成套件,子行程靠它找程式
FAULT_NONCE_ENV = "RTB_DEMO_FAULT_NONCE"
EXIT_FAULT_REFUSED = 3
STARTUP_SECONDS = 20.0
STOP_SECONDS = 5.0
_BASICS = ("PATH", "HOME", "LANG", "USER")
_MODEL_VARIABLES = ("RTB_MODEL_LIVE", "RTB_MODEL", "RTB_MODEL_RECORD")

__all__ = ["EXIT_FAULT_REFUSED", "FAULT_NONCE_ENV", "ROOT_MARKER", "FaultRequest", "Role",
           "child_env", "command_for", "prepare_root", "start", "write_fault_plan"]


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
        return ([sys.executable, "-m", _MODULES[role], *args],
                child_env(role, keys, user_env=user_env))
    if faults.role is not role:
        raise ValueError(f"故障是排給 {faults.role} 的,不是 {role}")
    config, nonce = write_fault_plan(root, faults)
    return ([sys.executable, "-m", "rtb.demo.launcher.child", role.value, str(config), "--",
             *args], child_env(role, keys, user_env=user_env, fault_nonce=nonce))


class Process:
    """一個已啟動的子行程:獨立的行程群組,stop 連同它開的孫行程一起結束。"""

    def __init__(self, popen: subprocess.Popen[str], first_line: str) -> None:
        self._popen = popen
        self.first_line = first_line
        self.pid = popen.pid
        port = first_line.removeprefix("PORT=") if first_line.startswith("PORT=") else None
        self.url = None if port is None else f"http://127.0.0.1:{port}"

    def poll(self) -> int | None:
        return self._popen.poll()

    def wait(self, timeout: float) -> int:
        return self._popen.wait(timeout)

    def stop(self) -> None:
        if self._popen.poll() is None:
            try:
                os.killpg(self._popen.pid, signal.SIGTERM)
                self._popen.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(self._popen.pid, signal.SIGKILL)
                self._popen.wait(STOP_SECONDS)
            except ProcessLookupError:
                pass
        if self._popen.stdout is not None:
            self._popen.stdout.close()


class StartFailed(Exception):
    """子行程沒在時限內印出就緒那一行(起不來、拒絕啟動或太慢)。"""


def _first_line(popen: subprocess.Popen[str], prefix: str) -> str:
    lines: queue.Queue[str] = queue.Queue()

    def read() -> None:
        if popen.stdout is None:
            lines.put("")
            return
        for line in popen.stdout:
            lines.put(line.strip())
        lines.put("")

    threading.Thread(target=read, daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=STARTUP_SECONDS)
        except queue.Empty as slow:
            raise StartFailed(f"{STARTUP_SECONDS} 秒內沒有就緒") from slow
        if line.startswith(prefix):
            return line
        if line == "" and popen.poll() is not None:
            raise StartFailed(f"子行程結束了,結束代碼 {popen.returncode}")


def start(role: Role, args: Sequence[str], keys: DemoKeys, *, root: Path,
          faults: FaultRequest | None, user_env: Mapping[str, str]) -> Process:
    command, env = command_for(role, args, keys, root=root, faults=faults, user_env=user_env)
    log = open(root / f"{role.value}-{os.getpid()}-{threading.get_ident()}.log", "a",  # noqa: SIM115 - 交給子行程,父行程不再寫
               encoding="utf-8")
    popen = subprocess.Popen(command, env=env, cwd=root, stdout=subprocess.PIPE, stderr=log,  # noqa: S603 - 指令是固定的 python -m 模組加參數
                             text=True, start_new_session=True)
    log.close()
    try:
        first = _first_line(popen, _READY_PREFIX[role])
    except StartFailed:
        Process(popen, "").stop()
        raise
    return Process(popen, first)
