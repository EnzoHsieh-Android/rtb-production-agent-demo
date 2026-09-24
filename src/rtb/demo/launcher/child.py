"""故障啟動器:在子行程裡核對故障交付,過了才帶著故障跑正式入口(Phase 12 設計審 r3 m2)。

用法只給展示啟動器:`python -m rtb.demo.launcher.child <角色> <故障設定檔> -- <正式入口參數>`,
一次性隨機值經環境變數給。核對不過一律以結束代碼 3 結束,不會退回成沒有故障的正常啟動。
"""

import os
import sys
from pathlib import Path

from rtb.demo.faults import dsp as dsp_faults
from rtb.demo.faults import executor as executor_faults
from rtb.demo.faults.delivery import FaultPlan, FaultRefused, load_verified
from rtb.demo.launcher import EXIT_FAULT_REFUSED, FAULT_NONCE_ENV, Role

_PATH_OPTIONS = ("--db", "--tenant-config")


def _targets(args: list[str]) -> list[Path]:
    return [Path(args[i + 1]) for i, arg in enumerate(args[:-1]) if arg in _PATH_OPTIONS]


def _verified(argv: list[str]) -> tuple[Role, FaultPlan, list[str]]:
    if len(argv) < 3 or argv[2] != "--":  # 角色、設定檔、分隔
        raise FaultRefused("用法:<角色> <故障設定檔> -- <參數>")
    try:
        role = Role(argv[0])
    except ValueError as bad:
        raise FaultRefused(f"不認得的角色 {argv[0]!r}") from bad
    if role not in (Role.DSP, Role.EXECUTOR):
        raise FaultRefused(f"{role} 沒有故障手段")
    args = argv[3:]
    plan = load_verified(Path(argv[1]), os.environ.get(FAULT_NONCE_ENV), role.value,
                         _targets(args))
    if plan.crash_point is not None and plan.crash_point not in executor_faults.CRASH_POINTS:
        raise FaultRefused(f"不認得的猝死點 {plan.crash_point!r}")
    return role, plan, args


def main(argv: list[str] | None = None) -> int:
    try:
        role, plan, args = _verified(sys.argv[1:] if argv is None else argv)
    except FaultRefused as refused:
        sys.stderr.write(f"拒絕啟動故障:{refused}\n")
        return EXIT_FAULT_REFUSED
    if role is Role.DSP:
        dsp_faults.serve(args, plan)
        return 0
    return executor_faults.run(args, plan)


if __name__ == "__main__":
    raise SystemExit(main())
