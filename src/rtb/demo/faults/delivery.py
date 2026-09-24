"""故障跨行程交付(Phase 12 設計審 r3 m2):啟動器在這次展示的暫存根目錄寫一份故障設定檔,另產生
一次性隨機值,經白名單環境只傳給那一個子行程;子行程啟動時核對設定檔在根目錄內、隨機值相符、根目錄
是這次展示建的(標記檔內容等於設定檔記的展示編號)、目標路徑都在根目錄內,任一不符就拒。

設定檔只存隨機值的雜湊,不存隨機值本身:讀得到暫存目錄的人照樣造不出能通過的一次交付。天花板:
同一個使用者的其他本機程式讀得到環境與暫存目錄,這裡不防本機惡意程式(計劃〈實務隱患〉)。
"""

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtb.dsp.server import FAULT_MODES

ROOT_MARKER = ".rtb-demo-root"
USED_PREFIX = "used-nonce-"  # 用過的一次性隨機值:根目錄裡以它的雜湊命名的標記
_NONCE_BYTES = 32
_DEMO_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


class FaultRefused(Exception):
    """交付核對沒過:子行程不啟動故障(也不退回成沒有故障的正常啟動)。"""


@dataclass(frozen=True)
class FaultPlan:
    """一個子行程的故障安排。dsp_plan 依序給每個會改狀態的請求(故障模式, DSP 時鐘偏移秒數);
    clock_offset_seconds 是整個子行程的時鐘偏移;crash_point 是執行迴圈的猝死點。"""

    role: str
    dsp_plan: tuple[tuple[str, float], ...] = ()
    clock_offset_seconds: float = 0.0
    crash_point: str | None = None


def prepare_root(base: Path, demo_id: str) -> Path:
    """展示編號只收英數與連字號(擋 ../ 與非 ASCII);上層目錄要是自己的、別人不可寫(代碼審 r1 s3:
    別人可寫的共用目錄能把整個根目錄換掉)。"""
    if not _DEMO_ID.fullmatch(demo_id):
        raise ValueError(f"展示編號只收英數與連字號:{demo_id!r}")
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = base.lstat()
    # 跟著連結走核對的是別的目錄,連結的擁有者之後能改指向(代碼審 r2 s3)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"展示根目錄的上層 {base} 是符號連結")
    if info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"展示根目錄的上層 {base} 不是自己的,或別人可寫")
    root = base / demo_id
    root.mkdir(mode=0o700)  # 已經存在就是別次展示的,丟例外不共用
    (root / ROOT_MARKER).write_text(demo_id, encoding="utf-8")
    return root


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def write_plan(root: Path, plan: FaultPlan) -> tuple[Path, str]:
    demo_id = (root / ROOT_MARKER).read_text(encoding="utf-8")
    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    path = root / f"faults-{plan.role}-{secrets.token_hex(4)}.json"
    body = {"demo_id": demo_id, "root": str(root.resolve()), "role": plan.role,
            "dsp_plan": [list(step) for step in plan.dsp_plan],
            "clock_offset_seconds": plan.clock_offset_seconds, "crash_point": plan.crash_point,
            "nonce_sha256": _digest(nonce)}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        json.dump(body, out)
    return path, nonce


def _load(config: Path) -> dict[str, Any]:
    try:
        body = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as bad:
        raise FaultRefused(f"讀不到故障設定檔:{bad}") from bad
    if not isinstance(body, dict):
        raise FaultRefused("故障設定檔格式不對")
    return body


def _root_of(body: dict[str, Any], config: Path) -> Path:
    root = Path(str(body.get("root", ""))).resolve()
    if not root.is_absolute() or not config.resolve().is_relative_to(root):
        raise FaultRefused("故障設定檔不在它記的展示根目錄內")
    try:
        marker = (root / ROOT_MARKER).read_text(encoding="utf-8")
    except OSError as missing:
        raise FaultRefused("根目錄不是展示建的(沒有標記檔)") from missing
    expected = str(body.get("demo_id", "")).encode("utf-8")
    if not hmac.compare_digest(marker.encode("utf-8"), expected):
        raise FaultRefused("根目錄不是這次展示建的(標記檔的展示編號不同)")
    return root


def _plan_of(body: dict[str, Any], role: str) -> FaultPlan:
    """設定檔的欄位:型別不對、故障模式不認得、欄位給錯角色一律拒(代碼審 r1 s5/l3/l4:打錯字的
    故障模式原本會靜默變成沒有故障)。"""
    steps = body.get("dsp_plan", [])
    if not isinstance(steps, list):
        raise FaultRefused("故障排程不是清單")
    plan = []
    for step in steps:
        fault, offset = step  # 不是兩項就丟,外層轉成拒絕
        if fault not in FAULT_MODES and fault is not None:
            raise FaultRefused(f"不認得的故障模式 {fault!r}")
        plan.append((fault, float(offset)))
    crash = body.get("crash_point")
    if crash is not None and not isinstance(crash, str):
        raise FaultRefused("猝死點不是字串")
    if role == "dsp" and crash is not None:
        raise FaultRefused("模擬 DSP 沒有猝死點")
    if role == "executor" and plan:
        raise FaultRefused("執行迴圈沒有故障排程")
    return FaultPlan(role=role, dsp_plan=tuple(plan),
                     clock_offset_seconds=float(body.get("clock_offset_seconds", 0.0)),
                     crash_point=crash)


def load_verified(config: Path, nonce: str | None, role: str, targets: list[Path]) -> FaultPlan:
    """核對全部過了才回故障安排:同一份交付不能用第二次(代碼審 r1 s4)。用過的是一次性隨機值本身:
    在根目錄以「不准已存在」的方式建一個以它的雜湊命名的標記,建不起來就是用過了;設定檔隨即刪掉。
    只看檔名擋不住(第 3 輪代碼審 p2:大寫字尾在不分大小寫的檔案系統、或先複製一份,都能再用一次)。"""
    try:
        body = _load(config)
        root = _root_of(body, config)
        if nonce is None or not hmac.compare_digest(
                _digest(nonce).encode("utf-8"), str(body.get("nonce_sha256")).encode("utf-8")):
            raise FaultRefused("一次性隨機值不相符")
        if body.get("role") != role:
            raise FaultRefused(f"故障設定檔是給 {body.get('role')!r} 的,不是 {role!r}")
        outside = [str(t) for t in targets if not Path(t).resolve().is_relative_to(root)]
        if outside:
            raise FaultRefused(f"目標路徑不在展示根目錄內:{', '.join(outside)}")
        plan = _plan_of(body, role)
    except (TypeError, ValueError) as bad:
        raise FaultRefused(f"故障設定檔內容看不懂:{bad}") from bad
    try:
        os.close(os.open(root / f"{USED_PREFIX}{_digest(nonce)}",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError as used:
        raise FaultRefused("這一份交付已經用過") from used
    with contextlib.suppress(FileNotFoundError):
        config.unlink()
    return plan
