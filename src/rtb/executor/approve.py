"""人工核可管理工具(Phase 6 增量 3):人對一份待核可的提案簽一張核可,寫進核可表。

比照既有命令列入口:收資料庫路徑與核可參數、啟動時經共用模組讀核可金鑰、出錯以非零代碼結束。
透過收件口模組開資料庫、呼叫它的「寫一張核可」方法;範圍指紋用核可模組那一支(跟執行迴圈同一段
程式),讀同一份租戶設定檔。不做網頁介面、不做會簽與撤銷(撤銷靠等到期)。
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rtb.capabilitykit import APPROVAL_KEY_ENV, read_key
from rtb.executor import approval
from rtb.executor.capability_signer import SigningRefused, load_tenants, tenant_for
from rtb.executor.inbox_store import APPROVABLE, BlockCode, InboxBusy, InboxStore, utc_now

EXIT_NO_KEY = 2
EXIT_NOT_FOUND = 3  # 沒有這份待核可的提案,或它的廣告不屬於設定檔裡的任何租戶
EXIT_REFUSED = 4  # 參數不合法(關卡、金額、到期)或設定檔壞掉
EXIT_BUSY = 6  # 資料庫忙碌:稍後再試(跟執行迴圈同一個代碼)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False, description="人工核可一份待核可的提案")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--stage", required=True, choices=sorted(c.value for c in APPROVABLE))
    parser.add_argument("--max-increase", required=True, type=int)
    parser.add_argument("--approver", required=True)
    parser.add_argument("--expires-in-seconds", required=True, type=int)
    return parser.parse_args(argv)


def run(
    argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = utc_now, out: TextIO | None = None,
) -> int:
    args = _parse(argv)
    key = read_key(os.environ if environ is None else environ, APPROVAL_KEY_ENV)
    if key is None:
        sys.stderr.write("拒絕簽核可:沒有可用的核可金鑰\n")
        return EXIT_NO_KEY
    try:
        store = InboxStore(args.db)
    except InboxBusy:
        sys.stderr.write("資料庫忙碌,稍後再試\n")
        return EXIT_BUSY
    try:  # 找提案與寫核可都可能等不到寫入鎖:同一個固定代碼,稍後再試
        return _sign(store, key, args, clock, out or sys.stdout)
    except InboxBusy:
        sys.stderr.write("資料庫忙碌,稍後再試\n")
        return EXIT_BUSY
    finally:
        store.close()


def _sign(
    store: InboxStore, key: bytes, args: argparse.Namespace, clock: Callable[[], datetime],
    out: TextIO,
) -> int:
    waiting = store.find_proposal(args.task_id, args.revision)
    if waiting is None:
        sys.stderr.write("找不到這份待核可的提案\n")
        return EXIT_NOT_FOUND
    stage = BlockCode(args.stage)
    if waiting.stage is not stage:  # 只能簽它現在停的那一關:每一次超額放行都有人看過那一次
        sys.stderr.write(f"這份提案現在停在 {waiting.stage.value},不是這一關\n")
        return EXIT_REFUSED
    proposal = waiting.message.proposal
    try:
        tenants = load_tenants(args.tenant_config)
    except SigningRefused as refused:
        sys.stderr.write(f"拒絕簽核可:租戶設定檔不能用({refused.reason})\n")
        return EXIT_REFUSED
    tenant = tenant_for(tenants, proposal.campaign_id)
    if tenant is None:
        sys.stderr.write("這份提案的廣告不屬於任何租戶\n")
        return EXIT_NOT_FOUND
    now = clock()
    issued = int(now.timestamp())
    try:
        token = approval.issue(key, proposal, stage, tenant, approver=args.approver,
                               max_increase=args.max_increase, issued_at=issued,
                               expires_at=issued + args.expires_in_seconds)
    except approval.ApprovalRefused as refused:
        sys.stderr.write(f"拒絕簽核可:{refused.reason}\n")
        return EXIT_REFUSED
    store.add_approval(proposal, stage, approval.approval_id(token), token, now)
    print(approval.approval_id(token), file=out, flush=True)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
