"""模型花費帳的寫入(Phase 11B 增量 1,計劃〈花費帳與上限〉):建表、預留、結算、記 0 元的帳、人工核銷。

讀法與「一筆算進已用多少」的規則在唯讀開法(modelledger_view);這裡只寫,每一筆都在單一寫入交易裡。
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from rtb import modelcore as core
from rtb.modelcore import (
    Backend,
    BackendReply,
    Caller,
    LedgerBusy,
    LocalCapRefused,
    ModelRequest,
    Outcome,
    Source,
)
from rtb.modelledger_view import CALLS_SELECT, LedgerCall
from rtb.sqlitekit import DatabaseBusy, connect, immediate_transaction, read_snapshot

SETTLE_ATTEMPTS = 3  # 結算寫不進去時的嘗試次數(含第一次);仍失敗就把金額印到標準錯誤
# 核銷時兩個開機識別都是推算的開機時刻,差在這個秒數內算同一次開機
SAME_BOOT_SECONDS = 60

# ---- 花費帳 ----
_NO_CHANGE = "SELECT RAISE(ABORT, '花費帳只增不改')"
SCHEMA = "\n".join([
    "CREATE TABLE IF NOT EXISTS model_reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "reserved_at TEXT NOT NULL, month TEXT NOT NULL, demo_id TEXT, batch_id TEXT, "
    "caller TEXT NOT NULL, model TEXT NOT NULL, backend TEXT NOT NULL, source TEXT NOT NULL, "
    "reserved_nanousd INTEGER NOT NULL, timeout_seconds REAL NOT NULL, "
    "owner_pid INTEGER NOT NULL, owner_boot TEXT NOT NULL, reserved_monotonic REAL NOT NULL);",
    "CREATE TABLE IF NOT EXISTS model_settlements (reservation_id INTEGER PRIMARY KEY "
    "REFERENCES model_reservations(id), settled_at TEXT NOT NULL, outcome TEXT NOT NULL, "
    "sub_reason TEXT, input_tokens INTEGER, output_tokens INTEGER, "
    "cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER, cache_read_tokens INTEGER, "
    "reported_nanousd INTEGER, "
    "list_nanousd INTEGER NOT NULL, settled_nanousd INTEGER NOT NULL, "
    "by_reservation INTEGER NOT NULL, overrun INTEGER NOT NULL, "
    "cost_mismatch INTEGER NOT NULL, latency_ms REAL);",
    "CREATE TABLE IF NOT EXISTS model_write_offs (reservation_id INTEGER PRIMARY KEY "
    "REFERENCES model_reservations(id), written_at TEXT NOT NULL, "
    "amount_nanousd INTEGER NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL, "
    "settled_before INTEGER NOT NULL);",
    "CREATE INDEX IF NOT EXISTS model_reservations_by_month ON model_reservations(month);",
    "CREATE INDEX IF NOT EXISTS model_reservations_by_demo ON model_reservations(demo_id);",
    *(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
      f"BEGIN {_NO_CHANGE}; END;"
      for table in ("model_reservations", "model_settlements", "model_write_offs")
      for verb in ("UPDATE", "DELETE")),
])


@dataclass(frozen=True)
class Settlement:
    outcome: Outcome
    sub_reason: str | None
    reply: BackendReply | None
    list_nanousd: int
    settled_nanousd: int
    by_reservation: bool
    overrun: bool
    cost_mismatch: bool
    latency_ms: float | None


def _open(ledger: Path) -> sqlite3.Connection:
    path = Path(ledger)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        conn = connect(path)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_write_offs'"
                            ).fetchone() is None:
                conn.executescript(SCHEMA)  # 只在第一次建表,平常開帳不搶寫入鎖
        except BaseException:
            conn.close()
            raise
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,沒有呼叫") from busy
    except sqlite3.OperationalError as locked:
        if "locked" in str(locked):
            raise LedgerBusy("花費帳忙碌,沒有呼叫") from locked
        raise
    return conn


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _calls(conn: sqlite3.Connection, where: str, params: tuple[object, ...]) -> list[LedgerCall]:
    return [LedgerCall.from_row(row) for row in conn.execute(CALLS_SELECT + where, params)]


def _used(conn: sqlite3.Connection, demo_id: str | None, month: str) -> tuple[int, int]:
    in_month = sum(c.effective_nanousd for c in _calls(conn, "WHERE r.month = ?", (month,)))
    for_demo = 0 if demo_id is None else sum(
        c.effective_nanousd for c in _calls(conn, "WHERE r.demo_id = ?", (demo_id,)))
    return for_demo, in_month


@dataclass(frozen=True)
class Spent:
    month: str
    demo_nanousd: int
    month_nanousd: int


def used_so_far(ledger: Path, demo_id: str | None) -> Spent:
    """這個展示編號與本月(系統時鐘的 UTC 月曆月)的已用。"""
    month = core.utc_now().strftime("%Y-%m")
    conn = _open(ledger)
    try:
        with read_snapshot(conn):
            for_demo, in_month = _used(conn, demo_id, month)
    finally:
        conn.close()
    return Spent(month, for_demo, in_month)


def _insert_reservation(conn: sqlite3.Connection, request: ModelRequest, model: str,
                        backend: Backend, amount: int, now: datetime) -> int:
    """寫預留列;`now` 由呼叫端讀一次傳進來(判上限用的月份就是寫進帳的月份)。順便記下主行程編號、
    開機識別與開機以來秒數,給核銷判斷「還在不在等回應」。"""
    source = Source.RECORDED if backend is Backend.RECORDING else Source.LIVE
    cursor = conn.execute(
        "INSERT INTO model_reservations (reserved_at, month, demo_id, batch_id, caller, model, "
        "backend, source, reserved_nanousd, timeout_seconds, owner_pid, owner_boot, "
        "reserved_monotonic) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_iso(now), now.strftime("%Y-%m"), request.demo_id, request.batch_id,
         Caller(request.caller).value, model, backend.value, source.value, amount,
         request.timeout_seconds, os.getpid(), core.boot_identity(), core.monotonic_now()))
    if cursor.lastrowid is None:
        raise AssertionError("新增預留列沒有拿到編號")
    return cursor.lastrowid


def _insert_settlement(conn: sqlite3.Connection, reservation_id: int, done: Settlement) -> None:
    reply = done.reply
    conn.execute(
        "INSERT INTO model_settlements (reservation_id, settled_at, outcome, sub_reason, "
        "input_tokens, output_tokens, cache_write_5m_tokens, cache_write_1h_tokens, "
        "cache_read_tokens, reported_nanousd, list_nanousd, settled_nanousd, by_reservation, "
        "overrun, cost_mismatch, latency_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (reservation_id, _iso(core.utc_now()), done.outcome.value, done.sub_reason,
         *((None,) * 5 if reply is None or not reply.tokens_known else (
             reply.input_tokens, reply.output_tokens, reply.cache_write_5m_tokens,
             reply.cache_write_1h_tokens, reply.cache_read_tokens)),
         None if reply is None else reply.reported_nanousd,
         done.list_nanousd, done.settled_nanousd, int(done.by_reservation), int(done.overrun),
         int(done.cost_mismatch), done.latency_ms))


def zero(outcome: Outcome, latency_ms: float | None = None, sub_reason: str | None = None,
          reply: BackendReply | None = None) -> Settlement:
    return Settlement(outcome, sub_reason, reply, 0, 0, False, False, False, latency_ms)


def book(ledger: Path, request: ModelRequest, model: str, done: Settlement) -> None:
    """錄製重播與沒有錄製:預留 0 元與結算寫在同一個交易(來源記錄製)。"""
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            reservation = _insert_reservation(conn, request, model, Backend.RECORDING, 0,
                                              core.utc_now())
            _insert_settlement(conn, reservation, done)
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,這筆沒記進帳") from busy
    finally:
        conn.close()


def reserve(ledger: Path, request: ModelRequest, model: str, backend: Backend) -> int:
    """單一寫入交易裡讀已用、加預留、判上限、寫預留列([S902]、[S903])。超過上限記一筆 0 元的
    「本地上限拒絕」並丟例外,不呼叫。"""
    amount = core.reservation_nanousd(request, model)
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            now = core.utc_now()  # 整筆只讀一次系統時鐘
            for_demo, in_month = _used(conn, request.demo_id, now.strftime("%Y-%m"))
            over = (for_demo + amount > core.DEMO_CAP_NANOUSD) or (
                in_month + amount > core.MONTH_CAP_NANOUSD)
            if over:
                refused = _insert_reservation(conn, request, model, backend, 0, now)
                _insert_settlement(conn, refused, zero(Outcome.LOCAL_CAP_REFUSED))
                reservation_id = None
            else:
                reservation_id = _insert_reservation(conn, request, model, backend, amount, now)
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,沒有呼叫") from busy
    finally:
        conn.close()
    if reservation_id is None:
        raise LocalCapRefused(
            f"已達上限:這次展示已用 {for_demo / core.NANOUSD_PER_USD:.4f} 美元、本月已用 "
            f"{in_month / core.NANOUSD_PER_USD:.4f} 美元,加上這次預留 "
            f"{amount / core.NANOUSD_PER_USD:.4f} "
            "美元會超過每次展示 1 美元或每月 20 美元,沒有呼叫")
    return reservation_id


def settle(ledger: Path, reservation_id: int, done: Settlement) -> bool:
    """寫結算列;回傳這筆是不是在人工核銷之後才來的結算。"""
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            written_off = conn.execute(
                "SELECT 1 FROM model_write_offs WHERE reservation_id = ?",
                (reservation_id,)).fetchone() is not None
            _insert_settlement(conn, reservation_id, done)
    finally:
        conn.close()
    return written_off


# ---- 人工核銷 ----
class WriteOffRefused(ValueError):
    """核銷的條件不符;什麼都沒寫。"""


@dataclass(frozen=True)
class WriteOffDone:
    reservation_id: int
    amount_nanousd: int
    before_nanousd: int
    after_nanousd: int


def _nanousd(amount: Decimal) -> int:
    try:
        scaled = Decimal(amount) * core.NANOUSD_PER_USD
    except (InvalidOperation, TypeError) as bad:
        raise WriteOffRefused("金額看不懂") from bad
    if not scaled.is_finite() or scaled < 0:
        raise WriteOffRefused("金額要是不為負的有限數")
    return int(scaled.to_integral_value(rounding="ROUND_CEILING"))


def _owner_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # 行程在,只是不是自己的(編號被重用也算:寧可不核銷)
        return True
    return True


def _same_boot(recorded: str, current: str) -> bool | None:
    """兩個開機識別是不是同一次開機;種類不同或讀不懂回 None(判不出來)。"""
    if recorded.startswith("id:") and current.startswith("id:"):
        return recorded == current
    if recorded.startswith("at:") and current.startswith("at:"):
        try:
            return abs(int(recorded[3:]) - int(current[3:])) <= SAME_BOOT_SECONDS
        except ValueError:
            return None
    return None


def _check_unsettled(call: LedgerCall) -> None:
    """沒結算的預留:牆鐘要過最長請求期限;同一次開機時,主行程還要已經不在、而且開機以來秒數也過了
    期限(兩種時鐘對不上就保守拒絕:改過系統時間、或讀錯)。重開過機就只看牆鐘(主行程一定不在了)。"""
    age = core.utc_now() - datetime.fromisoformat(call.reserved_at)
    if age <= core.LONGEST_REQUEST:
        raise WriteOffRefused(f"預留 {call.id} 還在最長請求期限內,可能還在等回應,不能核銷")
    same = _same_boot(call.owner_boot, core.boot_identity())
    if same is None:
        raise WriteOffRefused(f"預留 {call.id} 判不出是不是同一次開機,不能核銷")
    if not same:
        return
    if _owner_alive(call.owner_pid):
        raise WriteOffRefused(
            f"預留 {call.id} 的主行程 {call.owner_pid} 還活著,可能還在等回應,不能核銷")
    elapsed = core.monotonic_now() - call.reserved_monotonic
    if elapsed <= core.LONGEST_REQUEST.total_seconds():
        raise WriteOffRefused(
            f"預留 {call.id} 牆鐘已過期限、開機以來秒數卻只過了 {elapsed:.0f} 秒:兩種時鐘對不上,"
            "不能核銷")


def _check_write_off(call: LedgerCall) -> None:
    if call.write_off_nanousd is not None:
        raise WriteOffRefused(f"預留 {call.id} 已經核銷過,不能再核銷")
    if call.settled_nanousd is not None and not call.by_reservation:
        raise WriteOffRefused(f"預留 {call.id} 照實際 token 數或 0 元結算,不能核銷")
    if call.settled_nanousd is None:
        _check_unsettled(call)


def write_off(ledger: Path, reservation_id: int, amount: Decimal, reason: str,
              evidence: str) -> WriteOffDone:
    """人工核銷:只追加一列([S923])。只接受已照預留金額結算的列,或預留超過最長請求期限還沒結算的列;
    在單一寫入交易裡重查這筆仍符合條件才寫。原因與依據必填(訂閱後端沒有主控台帳單,依據寫本機紀錄:
    子行程結束狀態、錄製檔、Claude Code 的用量畫面)。"""
    if not reason.strip() or not evidence.strip():
        raise WriteOffRefused("核銷原因與依據都必填")
    nanousd = _nanousd(amount)
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            found = _calls(conn, "WHERE r.id = ?", (reservation_id,))
            if not found:
                raise WriteOffRefused(f"找不到預留 {reservation_id}")
            call = found[0]
            _check_write_off(call)
            conn.execute(
                "INSERT INTO model_write_offs (reservation_id, written_at, amount_nanousd, reason, "
                "evidence, settled_before) VALUES (?, ?, ?, ?, ?, ?)",
                (reservation_id, _iso(core.utc_now()), nanousd, reason.strip(), evidence.strip(),
                 int(call.settled_nanousd is not None)))
            after = _calls(conn, "WHERE r.id = ?", (reservation_id,))[0]
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,沒有核銷") from busy
    finally:
        conn.close()
    return WriteOffDone(reservation_id, nanousd, call.effective_nanousd, after.effective_nanousd)
