"""模擬 DSP 的故障排程(照事故 F1 端到端測試的組法):每個會改狀態的請求依序取一個安排
(故障模式, DSP 時鐘偏移);沒排到的請求照常。用戶端送不出故障標頭,故障只能排在伺服器這一側。"""

import argparse
import os
import threading
import time
from pathlib import Path
from typing import Any

from rtb.demo.faults.delivery import FaultPlan
from rtb.dsp.server import DspHandler, DspServer, read_keys
from rtb.dsp.store import CampaignStore


class _PlannedHandler(DspHandler):
    server: PlannedDsp

    def read_fault(self, modes: frozenset[str]) -> str | None:  # noqa: ARG002 - 故障照排程給,不看標頭
        if self.command != "POST":
            return None
        return self.server.next_fault()


class PlannedDsp(DspServer):
    def __init__(self, db: Path, plan: FaultPlan, hang_seconds: float, delay_seconds: float,
                 **keys: Any) -> None:
        self._base_offset = plan.clock_offset_seconds
        self._offset = plan.clock_offset_seconds
        self._plan = list(plan.dsp_plan)
        self._lock = threading.Lock()
        super().__init__(db, fault_injection=True, hang_seconds=hang_seconds,
                         delay_seconds=delay_seconds,
                         clock=lambda: time.time() + self._offset, **keys)
        self.RequestHandlerClass = _PlannedHandler

    def next_fault(self) -> str | None:
        with self._lock:
            if not self._plan:
                self._offset = self._base_offset
                return None
            fault, offset = self._plan.pop(0)
            self._offset = self._base_offset + offset
            return fault or None


def serve(args: argparse.Namespace, plan: FaultPlan) -> None:
    """帶故障排程的模擬 DSP:參數表與金鑰讀法跟正式入口同一份(代碼審 r1 a3),只換伺服器類別。"""
    capability_key, audit_key = read_keys(os.environ)
    CampaignStore(args.db).close()
    server = PlannedDsp(args.db, plan, args.hang_seconds, args.delay_seconds,
                        busy_timeout_seconds=args.busy_timeout_seconds,
                        socket_timeout_seconds=args.socket_timeout_seconds,
                        capability_key=capability_key, audit_key=audit_key)
    print(f"PORT={server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
