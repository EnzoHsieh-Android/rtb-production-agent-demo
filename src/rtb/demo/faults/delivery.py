"""故障跨行程交付(Phase 12 設計審 r3 m2):啟動器在這次展示的暫存根目錄寫一份故障設定檔,另產生
一次性隨機值,經白名單環境只傳給那一個子行程;子行程啟動時核對設定檔在根目錄內、隨機值相符、根目錄
是這次展示建的(標記檔內容等於設定檔記的展示編號)、目標路徑都在根目錄內,任一不符就拒。

設定檔只存隨機值的雜湊,不存隨機值本身:讀得到暫存目錄的人照樣造不出能通過的一次交付。天花板:
同一個使用者的其他本機程式讀得到環境與暫存目錄,這裡不防本機惡意程式(計劃〈實務隱患〉)。
"""

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_MARKER = ".rtb-demo-root"
_NONCE_BYTES = 32


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
    root = Path(base) / demo_id
    root.mkdir(parents=True, mode=0o700)  # 已經存在就是別次展示的,丟例外不共用
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
    if not hmac.compare_digest(marker, str(body.get("demo_id", ""))):
        raise FaultRefused("根目錄不是這次展示建的(標記檔的展示編號不同)")
    return root


def load_verified(config: Path, nonce: str | None, role: str, targets: list[Path]) -> FaultPlan:
    body = _load(config)
    root = _root_of(body, config)
    if nonce is None or not hmac.compare_digest(_digest(nonce), str(body.get("nonce_sha256"))):
        raise FaultRefused("一次性隨機值不相符")
    if body.get("role") != role:
        raise FaultRefused(f"故障設定檔是給 {body.get('role')!r} 的,不是 {role!r}")
    outside = [str(t) for t in targets if not Path(t).resolve().is_relative_to(root)]
    if outside:
        raise FaultRefused(f"目標路徑不在展示根目錄內:{', '.join(outside)}")
    return FaultPlan(role=role,
                     dsp_plan=tuple((str(f), float(o)) for f, o in body.get("dsp_plan", [])),
                     clock_offset_seconds=float(body.get("clock_offset_seconds", 0.0)),
                     crash_point=body.get("crash_point"))
