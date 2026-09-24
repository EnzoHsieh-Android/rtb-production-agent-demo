"""故障啟動器:在子行程裡核對故障交付,過了才帶著故障跑正式入口(Phase 12 設計審 r3 m2)。

用法只給展示啟動器:`python -m rtb.demo.launcher.child <角色> <故障設定檔> -- <正式入口參數>`,
一次性隨機值經環境變數給。核對不過一律以結束代碼 3 結束,不會退回成沒有故障的正常啟動。
"""

import argparse
import os
import sys
from pathlib import Path

from rtb.demo.faults import dsp as dsp_faults
from rtb.demo.faults import executor as executor_faults
from rtb.demo.faults.delivery import FaultPlan, FaultRefused, load_verified
from rtb.demo.launcher import EXIT_FAULT_REFUSED, FAULT_NONCE_ENV, Role
from rtb.dsp import server as dsp_server
from rtb.executor import runner as executor_runner

_PATH_OPTIONS = ("--db", "--tenant-config")


def _parsed(role: Role, args: list[str]) -> argparse.Namespace:
    """用正式入口同一支 parser 解析(正式入口不收縮寫),目標路徑拿解析後的值核對;路徑選項重複給
    (後一個會蓋掉前一個)一律拒(代碼審 r1 s1/l2/x2:只比對字串,等號寫法與縮寫都繞得過)。"""
    for option in _PATH_OPTIONS:
        given = [a for a in args if a == option or a.startswith(option + "=")]
        if len(given) > 1:
            raise FaultRefused(f"路徑選項 {option} 給了不只一次")
    parser = dsp_server.build_parser() if role is Role.DSP else executor_runner.build_parser()
    try:
        return parser.parse_args(args)
    except SystemExit as bad:
        raise FaultRefused("正式入口的參數看不懂") from bad


def _targets(parsed: argparse.Namespace) -> list[Path]:
    return [Path(value) for name in ("db", "tenant_config")
            if (value := getattr(parsed, name, None)) is not None]


def _verified(argv: list[str]) -> tuple[Role, FaultPlan, list[str], argparse.Namespace]:
    if len(argv) < 3 or argv[2] != "--":  # 角色、設定檔、分隔
        raise FaultRefused("用法:<角色> <故障設定檔> -- <參數>")
    try:
        role = Role(argv[0])
    except ValueError as bad:
        raise FaultRefused(f"不認得的角色 {argv[0]!r}") from bad
    if role not in (Role.DSP, Role.EXECUTOR):
        raise FaultRefused(f"{role} 沒有故障手段")
    args = argv[3:]
    parsed = _parsed(role, args)
    nonce = os.environ.pop(FAULT_NONCE_ENV, None)  # 核對完就不留在子行程環境裡(代碼審 r1 s4)
    plan = load_verified(Path(argv[1]), nonce, role.value, _targets(parsed))
    if plan.crash_point is not None and plan.crash_point not in executor_faults.CRASH_POINTS:
        raise FaultRefused(f"不認得的猝死點 {plan.crash_point!r}")
    return role, plan, args, parsed


def main(argv: list[str] | None = None) -> int:
    try:
        role, plan, args, parsed = _verified(sys.argv[1:] if argv is None else argv)
    except FaultRefused as refused:
        sys.stderr.write(f"拒絕啟動故障:{refused}\n")
        return EXIT_FAULT_REFUSED
    if role is Role.DSP:
        dsp_faults.serve(parsed, plan)
        return 0
    return executor_faults.run(args, plan)


if __name__ == "__main__":
    raise SystemExit(main())
