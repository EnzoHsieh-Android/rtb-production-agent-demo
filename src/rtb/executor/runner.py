"""執行迴圈的啟動程式:讀金鑰、拿單一執行者鎖、重啟恢復、印就緒訊號,之後每輪處理一筆。

順序是合約:金鑰不可用就以非零代碼結束,連重啟恢復都不跑;拿不到鎖(已有另一個執行迴圈)
也以非零代碼結束,同樣不跑重啟恢復——否則第二個執行迴圈的重啟恢復會把第一個正在跑的嘗試
轉成結果不明,吞掉它的寫入結果。

單一執行者鎖沿用專案既有的 SQLite 鎖,不引入檔案鎖:一條專用連線對一個專門當鎖用的小
SQLite 檔開一個不提交的「立即取得寫入鎖」交易,忙碌等待設 0;行程活著就一直握著,行程死了
作業系統自動放掉。不能直接鎖執行行程資料庫本身:握著它的寫入鎖,收件口就收不了提案。
鎖檔名由資料庫的實體身分(裝置與 inode 編號)決定,放在真實路徑的同一個目錄,所以相對路徑、
符號連結指到同一個資料庫,算出來都是同一把鎖。資料庫檔有硬連結就拒絕啟動:收件口與
執行迴圈若各用一個檔名開同一個資料庫,會各用一組 WAL 檔,可能損壞資料。先拿鎖、
才用 SQLite 開資料庫。鎖檔不走共用連線函式:那個函式會切
WAL,多出的附屬檔在鎖檔被刪掉重建時會跟新檔對不上。程式從不刪鎖檔;每一輪開頭核對它還是
當初那一個,不是就停下(威脅模型是防忘記,不防刻意繞過)。
"""

import argparse
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rtb.capabilitykit import read_key
from rtb.executor import attempt_store
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.dsp_client import DspClient
from rtb.executor.execution import DspPort, Executor, ExecutorHalted, Result
from rtb.executor.inbox_store import InboxBusy, InboxStore, utc_now
from rtb.sqlitekit import DatabaseBusy, begin_immediate

EXIT_NO_KEY = 2
EXIT_LOCKED = 3
EXIT_HALTED = 4
EXIT_UNSAFE_DB = 5  # 資料庫檔有硬連結:同一個資料庫可能被別的檔名以另一組 WAL 開啟
READY = "READY"
_IDLE_RESULTS = frozenset({Result.IDLE, Result.DEFERRED})


class LockHeld(Exception):
    """已有另一個執行迴圈握著這個資料庫的鎖。"""


class RunnerLock:
    def __init__(self, db_path: Path):
        real = Path(os.path.realpath(db_path))
        identity = os.stat(real)
        # 鎖名只看資料庫檔的實體身分(裝置與 inode),不含檔名:同目錄的硬連結也算到同一把鎖。
        # 不同目錄的硬連結會落在不同目錄,算不到同一把——那要刻意做,屬於繞過不是忘記
        self.path = real.parent / f".rtb-runner-{identity.st_dev}-{identity.st_ino}.lock"
        conn = sqlite3.connect(self.path, timeout=0, isolation_level=None)
        try:
            begin_immediate(conn)
        except DatabaseBusy as exc:
            conn.close()
            raise LockHeld(str(self.path)) from exc
        self._conn = conn
        self._inode = os.stat(self.path).st_ino

    def still_ours(self) -> bool:
        try:
            return os.stat(self.path).st_ino == self._inode
        except FileNotFoundError:
            return False

    def release(self) -> None:
        self._conn.close()


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="執行迴圈")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--dsp-url", required=True)
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--dsp-timeout-seconds", type=float, default=5.0)
    return parser.parse_args(argv)


def _loop(
    executor: Executor, lock: RunnerLock, interval: float, max_rounds: int | None,
    sleep: Callable[[float], None],
) -> int:
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        if not lock.still_ours():
            sys.stderr.write("停機:單一執行者鎖檔被換掉了\n")
            return EXIT_HALTED
        try:
            result = executor.process_one()
        except (ExecutorHalted, InboxBusy) as halted:  # 出事就停,讓人注意到;重啟時會做恢復
            sys.stderr.write(f"停機:{halted}\n")
            return EXIT_HALTED
        if result.kind in _IDLE_RESULTS:
            sleep(interval)
    return 0


def run(  # noqa: PLR0913 - 協作者都可替換,測試在行程內跑
    argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] = utc_now, dsp: DspPort | None = None, out: TextIO | None = None,
    max_rounds: int | None = None, sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parse(argv)
    try:  # 只有啟動程式經共用模組讀金鑰環境變數
        signer = CapabilitySigner(read_key(os.environ if environ is None else environ))
    except ValueError as error:
        sys.stderr.write(f"拒絕啟動:{error}\n")
        return EXIT_NO_KEY
    # 先拿鎖、才用 SQLite 開資料庫:同一個資料庫若經硬連結以別的檔名開,SQLite 會用另一組
    # WAL 檔,可能損壞資料。鎖名要資料庫檔的 inode,所以只用作業系統建一個空檔(不截斷既有的)
    os.close(os.open(args.db, os.O_RDONLY | os.O_CREAT, 0o600))
    # 有硬連結就拒絕啟動:收件口若用另一個檔名開同一個資料庫,兩邊各用一組 WAL 檔,
    # 可能損壞資料(SQLite 官方〈How To Corrupt〉的多重連結)。符號連結 SQLite 會自己解析
    if os.stat(args.db).st_nlink > 1:
        sys.stderr.write("拒絕啟動:資料庫檔有硬連結,可能被別的檔名同時開啟\n")
        return EXIT_UNSAFE_DB
    try:
        lock = RunnerLock(args.db)
    except LockHeld:
        sys.stderr.write("拒絕啟動:已有另一個執行迴圈在跑\n")
        return EXIT_LOCKED
    store = InboxStore(args.db)
    try:
        now: datetime = clock()
        with store.transaction() as tx:
            recovery = attempt_store.recover_in_flight(tx, now)
        if recovery.unreadable:
            sys.stderr.write(f"讀不回來的嘗試(要人處理):{', '.join(recovery.unreadable)}\n")
        print(READY, file=out or sys.stdout, flush=True)
        executor = Executor(store, dsp or DspClient(args.dsp_url, args.dsp_timeout_seconds),
                            signer, args.tenant_config, clock)
        return _loop(executor, lock, args.interval_seconds, max_rounds, sleep)
    finally:
        store.close()
        lock.release()


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
