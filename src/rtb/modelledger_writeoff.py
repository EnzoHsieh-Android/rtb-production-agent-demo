"""花費帳的人工核銷命令列(Phase 11B 增量 1,[S923]):`python -m rtb.modelledger_writeoff`。

只追加一列核銷,原列不改。只接受已照預留金額結算的列,或預留超過最長請求期限還沒結算的列;
原因與依據必填(訂閱後端沒有主控台帳單,依據寫本機紀錄)。帳檔寫死家目錄那一本
(~/.rtb/model-ledger.sqlite),沒有參數能換成別本([S925])。
"""

import argparse
import sys
from decimal import Decimal, InvalidOperation
from typing import TextIO

from rtb import modelclient

EXIT_OK = 0
EXIT_REFUSED = 3
EXIT_LEDGER_BUSY = 9  # 跟模型入口的「花費帳忙碌」同一個代碼


def _amount(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as bad:
        raise argparse.ArgumentTypeError(f"看不懂的金額:{text}") from bad


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="核銷一筆模型花費帳(只追加,原列不改)")
    parser.add_argument("--reservation-id", required=True, type=int)
    parser.add_argument("--amount-usd", required=True, type=_amount,
                        help="核銷後這筆算多少美元(依主控台用量)")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--evidence", required=True,
                        help="依據(必填):訂閱後端沒有主控台帳單,寫本機紀錄——子行程結束狀態、"
                             "錄製檔、Claude Code 的用量畫面")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        err: TextIO | None = None) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    try:
        done = modelclient.write_off(modelclient.live_ledger_path(), args.reservation_id,
                                     args.amount_usd, args.reason, args.evidence)
    except modelclient.WriteOffRefused as refused:
        print(f"拒絕:{refused}", file=errors)
        return EXIT_REFUSED
    except modelclient.LedgerBusy as busy:
        print(f"{busy};稍後再試", file=errors)
        return EXIT_LEDGER_BUSY
    usd = modelclient.NANOUSD_PER_USD
    print(f"已核銷預留 {done.reservation_id}:這筆算進已用的金額從 {done.before_nanousd / usd:.6f} "
          f"改成 {done.after_nanousd / usd:.6f} 美元", file=out or sys.stdout)
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
