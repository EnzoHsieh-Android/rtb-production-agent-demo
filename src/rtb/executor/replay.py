"""死信重放管理工具(Phase 8):人對一份死信下重放,把它放回待處理。

比照核可管理工具的命令列入口:收資料庫路徑、任務、修訂與操作人,出錯以非零代碼結束。透過收件口
模組開資料庫、呼叫它的重放方法;條件判斷、放回與稽核都在那個方法的同一個交易裡,這裡不重寫一份。
放回之後由執行迴圈照一般流程處理,一關都不略過:這支工具不碰 DSP、不簽憑證、不讀租戶設定。
不做自動重放、排程或批次重放(一次一份)。
"""

import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rtb.domain._checks import is_id
from rtb.executor.inbox_store import InboxBusy, InboxStore, ReplayOutcome, utc_now

EXIT_REFUSED = 4  # 重放被拒(原因印在標準錯誤)或操作人不合格式;跟核可工具同一個代碼
EXIT_BUSY = 6  # 資料庫忙碌:稍後再試(跟執行迴圈、核可工具同一個代碼)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False, description="把一份死信放回待處理")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--operator", required=True)
    return parser.parse_args(argv)


def run(
    argv: list[str] | None = None, *, clock: Callable[[], datetime] = utc_now,
    out: TextIO | None = None, err: TextIO | None = None,
) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    # 比照核可工具驗核可人:不合格式就拒絕,不寫稽核(沒有可信的操作人可記)
    if not is_id(args.operator):
        errors.write("拒絕重放:操作人必須是合法的識別字串\n")
        return EXIT_REFUSED
    try:
        store = InboxStore(args.db)
    except InboxBusy:
        errors.write("資料庫忙碌,稍後再試\n")
        return EXIT_BUSY
    try:
        outcome = store.replay(args.task_id, args.revision, args.operator, clock)
    except InboxBusy:
        errors.write("資料庫忙碌,稍後再試\n")
        return EXIT_BUSY
    finally:
        store.close()
    if outcome is not ReplayOutcome.REQUEUED:
        errors.write(f"拒絕重放:{outcome.value}\n")
        return EXIT_REFUSED
    print(outcome.value, file=out or sys.stdout, flush=True)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
