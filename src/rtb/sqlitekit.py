"""SQLite 的共用做法:WAL、忙碌逾時、手動交易(BEGIN IMMEDIATE),忙碌與永久故障分得開。

DSP 與提案收件口都用它,不各寫一套。每個執行緒或請求自己開一條連線,不共用。

唯讀連線(Phase 9 增量 1):以 SQLite 官方 URI 的 mode=ro 開,並明講這是 URI——把「mode=ro」字串
丟給一般的連線函式不會報錯,而是在磁碟上悄悄新建一個以那串文字為檔名的空資料庫(前掃實測)。唯讀
快照用不取鎖的顯式交易開頭,開頭之後立刻讀一次:SQLite 的讀取快照從第一次讀取才定,不先讀的話,
開頭與第一次查詢之間別人提交的寫入也會被看到。
"""

import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_SECONDS = 5.0


class DatabaseBusy(Exception):
    """資料庫正被別的寫入佔用,等待逾時。呼叫者決定要不要重試。"""


class DatabaseNotUpgraded(Exception):
    """唯讀開法發現資料庫還沒升級到這一版(缺表或缺欄位):唯讀連線不能補,要先用寫入開法開一次。"""


def is_lock_contention(exc: sqlite3.OperationalError) -> bool:
    """這個資料庫錯誤是不是鎖競爭(忙碌或被鎖):專案唯一的「忙碌對永久故障」分類,其他模組要分也用這支。"""
    primary_code = exc.sqlite_errorcode & 0xFF  # 擴充碼的低 8 位才是主要錯誤碼
    return primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


INTEGER_OVERFLOW_MESSAGE = "integer overflow"


def is_integer_overflow(exc: BaseException) -> bool:
    """資料庫的整數加總(SUM)溢位:一般錯誤碼、訊息是「integer overflow」(本機 3.53.3 實測)。
    SQLite 對整數溢位只回一般錯誤碼,跟查詢寫錯同一個碼,只能再看訊息文字;這是專案唯一要看訊息的
    判斷,集中在這裡跟「是不是鎖競爭」並排,SQLite 改了措辭只改一處(F7 效能計劃)。"""
    return (isinstance(exc, sqlite3.OperationalError)
            and exc.sqlite_errorcode & 0xFF == sqlite3.SQLITE_ERROR
            and str(exc) == INTEGER_OVERFLOW_MESSAGE)


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
        if is_lock_contention(exc):  # 剛建立資料庫時切換 WAL 要獨佔鎖,撞上別人也是「忙碌」
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
        if is_lock_contention(exc):
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


def connect_read_only(
    path: Path, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
) -> sqlite3.Connection:
    """唯讀連線:任何寫入都失敗;檔案不存在丟 FileNotFoundError,不建新檔。不建表、不切日誌模式。"""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    uri = target.resolve().as_uri() + "?mode=ro"  # as_uri 會把檔名裡的 # ? 空白編碼掉
    return sqlite3.connect(uri, uri=True, isolation_level=None, timeout=busy_timeout_seconds)


def begin_snapshot(conn: sqlite3.Connection) -> None:
    """不取鎖的顯式交易開頭,並立刻讀一次把快照定下來(之後同一個交易裡的查詢都讀這個快照)。"""
    conn.execute("BEGIN")
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()


def end_snapshot(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.execute("ROLLBACK")  # 唯讀交易沒有東西要提交;回滾同樣結束快照


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """唯讀交易:進入時定下快照,離開時結束(不論有沒有例外)。"""
    begin_snapshot(conn)
    try:
        yield
    finally:
        end_snapshot(conn)


def missing_schema(
    conn: sqlite3.Connection, required: Mapping[str, Iterable[str]],
    indexes: Iterable[str] = (),
) -> list[str]:
    """required 是 {表: 欄位},indexes 是要有的索引名;回傳缺的表、「表.欄位」與「索引 名稱」(唯讀開法
    判斷資料庫升級了沒有:唯讀連線不建索引,缺索引的查詢會退化成全表掃描,也算還沒升級)。"""
    missing = []
    for table, columns in required.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not present:
            missing.append(table)
            continue
        missing += [f"{table}.{column}" for column in columns if column not in present]
    existing = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}
    missing += [f"索引 {name}" for name in indexes if name not in existing]
    return missing
