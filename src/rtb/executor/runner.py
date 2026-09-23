"""執行迴圈的啟動程式:讀金鑰、重啟恢復、印就緒訊號,之後每輪先對帳、再處理一筆。

可以同時跑好幾個執行迴圈(Phase 4 增量 3a):互斥交給收件表的租約與收據,同一則訊息同一時間
只有一張有效收據,每一筆寫入都核對它。不再拿單一執行者鎖。

順序是合約:金鑰不可用就以非零代碼結束,連重啟恢復都不跑。重啟恢復只轉「收件表沒有對應處理中
訊息」的嘗試中(舊資料);有處理中訊息的是別的工作者正在做、或租約到期後由對帳原子接手的,不碰。

資料庫檔有硬連結就拒絕啟動,而且在用 SQLite 開它之前:收件口與執行迴圈若各用一個檔名開同一個
資料庫,會各用一組 WAL 檔,可能損壞資料(SQLite 官方〈How To Corrupt〉的多重連結)。符號連結與
相對路徑 SQLite 會自己解析成同一個檔。

資料庫忙碌(等鎖逾時)不再一撞就停:多個工作者共用一顆寫入鎖,排隊等鎖是正常事件。啟動時與每一輪
都是休息一下再試,連續 BUSY_LIMIT 次都忙才結束。

新舊版本不能混跑:舊版啟動時會拿單一執行者鎖、把所有嘗試中轉結果不明,看不到新版的工作者。
上線前一次停掉所有舊版;舊版的程式已寫死,這一點擋不住,是部署規則(見 Phase 4 計劃增量 3a)。
"""

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rtb.capabilitykit import read_key
from rtb.executor import attempt_store
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import DspPort, Executor, ExecutorHalted, Result
from rtb.executor.inbox_store import VISIBILITY_TIMEOUT, InboxBusy, InboxStore, utc_now

EXIT_NO_KEY = 2
EXIT_HALTED = 4
EXIT_UNSAFE_DB = 5  # 資料庫檔有硬連結:同一個資料庫可能被別的檔名以另一組 WAL 開啟
EXIT_UNSAFE_CONFIG = 7  # DSP 逾時太長:活著的工作者可能被當成已經不在做而被接手
LEASE_MARGIN = 6  # 租約至少要是一次 DSP 呼叫逾時的這麼多倍
EXIT_BUSY = 6  # 資料庫連續 BUSY_LIMIT 次都忙(啟動時或每一輪):乾淨結束,稍後再啟動;跟系統錯誤分開
BUSY_LIMIT = 3  # 連續幾次忙碌才放棄;暫用,沒有實測
READY = "READY"
_IDLE_RESULTS = frozenset({Result.IDLE, Result.DEFERRED, Result.LEASE_LOST})


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="執行迴圈")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--dsp-url", required=True)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--dsp-timeout-seconds", type=float, default=5.0)
    return parser.parse_args(argv)


def _loop(
    executor: Executor, interval: float, max_rounds: int | None,
    sleep: Callable[[float], None],
) -> int:
    rounds = busy_streak = 0
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        try:
            troubled = executor.reconcile_all()  # 先對帳,再處理新提案
            result = executor.process_one()
        except InboxBusy as busy:  # 多工作者下等鎖逾時是正常競爭:這一輪休息,連續幾次才停
            busy_streak += 1
            if busy_streak >= BUSY_LIMIT:
                sys.stderr.write(f"停機:資料庫連續 {busy_streak} 輪忙碌:{busy}\n")
                return EXIT_BUSY
            sleep(interval)
            continue
        except ExecutorHalted as halted:  # 出事就停,讓人注意到;重啟時會做恢復
            sys.stderr.write(f"停機:{halted}\n")
            return EXIT_HALTED
        busy_streak = 0
        # 對帳有 DSP 呼叫失敗就照樣休息:DSP 變慢時不連續全速打它
        if troubled or result.kind in _IDLE_RESULTS:
            sleep(interval)
    return 0


@dataclass(frozen=True)
class _Opened:
    store: InboxStore
    recovery: attempt_store.Recovery
    unreadable_messages: tuple[str, ...]  # 讀不回來的處理中列:算不出鍵,這次重啟恢復一把都不轉


def _recover(store: InboxStore, clock: Callable[[], datetime]) -> _Opened:
    with store.transaction() as tx:
        held, unreadable = store.in_progress_keys(tx)
        if unreadable:  # 任何一筆嘗試中都可能屬於那則讀不回來的訊息:不轉,對帳第一輪會停機讓人看
            return _Opened(store, attempt_store.Recovery((), ()), unreadable)
        now = clock()
        recovery = attempt_store.recover_in_flight(
            tx, now, held=held, written_before=now - VISIBILITY_TIMEOUT)
        return _Opened(store, recovery, ())


def _open_and_recover(
    db: Path, clock: Callable[[], datetime], sleep: Callable[[float], None], interval: float,
) -> _Opened | None:
    """開庫(可能要做重建表遷移)並做重啟恢復;忙碌就休息再試,連續 BUSY_LIMIT 次都忙回 None。"""
    for attempt in range(BUSY_LIMIT):
        if attempt:
            sleep(interval)
        try:
            store = InboxStore(db)
        except InboxBusy:
            continue
        try:
            return _recover(store, clock)
        except InboxBusy:
            store.close()
    return None


def run(  # noqa: PLR0913 - 協作者都可替換,測試在行程內跑
    argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = utc_now, dsp: DspPort | None = None, out: TextIO | None = None,
    max_rounds: int | None = None, sleep: Callable[[float], None] = time.sleep,
    owner: str | None = None,
) -> int:
    args = _parse(argv)
    # 判斷「有沒有人還在做」看的是租約(舊鍵看那一列寫下多久),都假設一次 DSP 呼叫一定在租約
    # 時間內結束。這個假設要擋在啟動時,不能只靠預設值剛好成立(代碼審第 3 輪資安席)
    if args.dsp_timeout_seconds * LEASE_MARGIN >= VISIBILITY_TIMEOUT.total_seconds():
        sys.stderr.write(f"拒絕啟動:DSP 逾時 {args.dsp_timeout_seconds} 秒太長,"
                         f"要小於租約的 1/{LEASE_MARGIN}\n")
        return EXIT_UNSAFE_CONFIG
    try:  # 只有啟動程式經共用模組讀金鑰環境變數
        signer = CapabilitySigner(read_key(os.environ if environ is None else environ))
    except ValueError as error:
        sys.stderr.write(f"拒絕啟動:{error}\n")
        return EXIT_NO_KEY
    # 用 SQLite 開資料庫之前先查硬連結:要看連結數,所以只用作業系統建一個空檔(不截斷既有的)
    os.close(os.open(args.db, os.O_RDONLY | os.O_CREAT, 0o600))
    # 有硬連結就拒絕啟動:收件口若用另一個檔名開同一個資料庫,兩邊各用一組 WAL 檔,
    # 可能損壞資料(SQLite 官方〈How To Corrupt〉的多重連結)。符號連結 SQLite 會自己解析
    if os.stat(args.db).st_nlink > 1:
        sys.stderr.write("拒絕啟動:資料庫檔有硬連結,可能被別的檔名同時開啟\n")
        return EXIT_UNSAFE_DB
    opened = _open_and_recover(args.db, clock, sleep, args.interval_seconds)
    if opened is None:
        sys.stderr.write(f"拒絕啟動:資料庫連續 {BUSY_LIMIT} 次忙碌,稍後再試\n")
        return EXIT_BUSY
    store = opened.store
    try:
        if opened.recovery.unreadable:
            sys.stderr.write(f"讀不回來的嘗試(要人處理):{', '.join(opened.recovery.unreadable)}\n")
        if opened.unreadable_messages:
            sys.stderr.write("重啟恢復跳過:有讀不回來的處理中訊息(要人處理):"
                             f"{', '.join(opened.unreadable_messages)}\n")
        print(READY, file=out or sys.stdout, flush=True)
        # 租約擁有者:行程編號加啟動時間。重啟後是新的擁有者,上一次留下的租約要等到期才接手
        owner = owner or f"{os.getpid()}-{int(time.time())}"
        executor = Executor(store, dsp or DspClient(args.dsp_url, args.dsp_timeout_seconds),
                            signer, args.tenant_config, clock, owner)
        return _loop(executor, args.interval_seconds, max_rounds, sleep)
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
