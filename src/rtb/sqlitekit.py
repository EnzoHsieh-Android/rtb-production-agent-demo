"""SQLite 的共用做法:WAL、忙碌逾時、手動交易(BEGIN IMMEDIATE),忙碌與永久故障分得開。

DSP 與提案收件口都用它,不各寫一套。每個執行緒或請求自己開一條連線,不共用。
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_SECONDS = 5.0


class DatabaseBusy(Exception):
    """資料庫正被別的寫入佔用,等待逾時。呼叫者決定要不要重試。"""


def _is_lock_contention(exc: sqlite3.OperationalError) -> bool:
    primary_code = exc.sqlite_errorcode & 0xFF  # 擴充碼的低 8 位才是主要錯誤碼
    return primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def connect(
    path: Path, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS, schema: str = ""
) -> sqlite3.Connection:
    """開連線(isolation_level=None:交易一律手動控制,不會悄悄開啟)、啟用 WAL、建立表。"""
    conn = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout_seconds)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        if schema:
            conn.executescript(schema)
    except sqlite3.OperationalError as exc:
        conn.close()  # 設定失敗時不留下沒人關的連線
        if _is_lock_contention(exc):  # 剛建立資料庫時切換 WAL 要獨佔鎖,撞上別人也是「忙碌」
            raise DatabaseBusy(str(exc)) from exc
        raise
    except BaseException:
        conn.close()
        raise
    return conn


def begin_immediate(conn: sqlite3.Connection) -> None:
    """開寫入交易;鎖不到就丟 DatabaseBusy。唯讀資料庫、磁碟錯誤等永久故障原樣往外丟。"""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_lock_contention(exc):
            raise DatabaseBusy(str(exc)) from exc
        raise


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """寫入交易:進入時 BEGIN IMMEDIATE(鎖不到丟 DatabaseBusy),正常結束就提交,任何例外都回滾。"""
    begin_immediate(conn)
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:  # SQLite 有時已自行回滾;再回滾會蓋掉真正的原因
            conn.execute("ROLLBACK")
        raise
