"""模型花費帳的唯讀開法(Phase 11B 增量 1):給維運的指標與追蹤讀,只讀、不含任何寫入函式。

帳本三張表都只增不改:預留、結算(每筆預留至多一列)、人工核銷(每筆預留至多一列)。建表與寫入在模型用戶端;
這裡只放讀法、欄位清單,以及「一筆呼叫算進已用多少」的規則——模型用戶端檢查上限時也用這一份,兩邊不會
算得不一樣。呼叫者、來源、結果類別三個封閉列舉也放這裡,指標讀它們當有界標籤,不必匯入模型用戶端(維運
行程不因此載入任何送出呼叫的程式)。

已用的規則:沒結算的照預留金額;結算了的照含係數結算金額;有核銷的照核銷金額——但核銷之後才來的結算
(遲到的回應)改算兩者中較高的(計劃〈花費帳與上限〉)。
"""

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from rtb.sqlitekit import DatabaseNotUpgraded, connect_read_only, missing_schema, read_snapshot

LEDGER_RELATIVE = Path(".rtb") / "model-ledger.sqlite"


def ledger_path() -> Path:
    """即時模式唯一的一本帳:使用者家目錄下的固定位置,不論從哪一份簽出、哪個工作目錄啟動([S925])。"""
    return Path.home() / LEDGER_RELATIVE


class Caller(StrEnum):
    """會呼叫模型的三個接入點(封閉列舉)。"""

    EVAL_CANDIDATE = "eval_candidate"
    HYPOTHESIS = "ops_hypothesis"
    NARRATIVE = "analyzer_narrative"


class Source(StrEnum):
    LIVE = "live"
    RECORDED = "recorded"


class Outcome(StrEnum):
    """一次呼叫的結果類別:成功加九類失敗(計劃第 7 版;預留時的花費帳忙碌寫不進帳,只在標準錯誤與結束
    代碼看得到)。"""

    OK = "ok"
    TIMEOUT = "timeout"
    LOCAL_CAP_REFUSED = "local_cap_refused"  # 預留時超過本地上限,沒有呼叫(給人看的是「已達上限」)
    QUOTA_EXHAUSTED = "quota_exhausted"  # 訂閱額度用完(已經呼叫)
    OVERRUN = "overrun"  # Claude Code 回報超過單次花費上限(已經呼叫)
    NO_RECORDING = "no_recording"
    UNREADABLE = "unreadable"
    CONFIG_ERROR = "config_error"  # 確定還沒呼叫模型:執行檔找不到或起不來、沒登入、不認得的模型
    TRANSIENT = "transient"
    LEDGER_BUSY = "ledger_busy"


class Backend(StrEnum):
    """後端種類:這一版只有 Claude Code(即時)與錄製(重播);將來的 API 後端加一個成員。"""

    CLAUDE_CODE = "claude_code"
    RECORDING = "recording"


RESERVATION_COLUMNS = ("id", "reserved_at", "month", "demo_id", "batch_id", "caller", "model",
                       "backend", "source", "reserved_nanousd", "timeout_seconds")
SETTLEMENT_COLUMNS = ("reservation_id", "settled_at", "outcome", "sub_reason", "input_tokens",
                      "output_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens",
                      "cache_read_tokens",
                      "reported_nanousd", "list_nanousd", "settled_nanousd", "by_reservation",
                      "overrun", "cost_mismatch", "latency_ms")
WRITE_OFF_COLUMNS = ("reservation_id", "written_at", "amount_nanousd", "reason", "evidence",
                     "settled_before")
TABLES: Mapping[str, tuple[str, ...]] = {
    "model_reservations": RESERVATION_COLUMNS,
    "model_settlements": SETTLEMENT_COLUMNS,
    "model_write_offs": WRITE_OFF_COLUMNS,
}
INDEXES = ("model_reservations_by_month", "model_reservations_by_demo")

# 一筆預留連同它的結算與核銷;WHERE 由呼叫端接上(欄位固定,參數用佔位)
CALLS_SELECT = (
    "SELECT r.id, r.reserved_at, r.month, r.demo_id, r.batch_id, r.caller, r.model, r.backend, "
    "r.source, r.reserved_nanousd, r.timeout_seconds, s.settled_at, s.outcome, s.sub_reason, "
    "s.input_tokens, s.output_tokens, s.cache_write_5m_tokens, s.cache_write_1h_tokens, "
    "s.cache_read_tokens, "
    "s.reported_nanousd, s.list_nanousd, s.settled_nanousd, s.by_reservation, s.overrun, "
    "s.cost_mismatch, s.latency_ms, w.amount_nanousd, w.settled_before "
    "FROM model_reservations r "
    "LEFT JOIN model_settlements s ON s.reservation_id = r.id "
    "LEFT JOIN model_write_offs w ON w.reservation_id = r.id ")


@dataclass(frozen=True)
class LedgerCall:
    """一筆模型呼叫:預留、結算(可能還沒有)、核銷(可能沒有)。金額單位是十億分之一美元。"""

    id: int
    reserved_at: str
    month: str
    demo_id: str | None
    batch_id: str | None
    caller: str
    model: str
    backend: str
    source: str
    reserved_nanousd: int
    timeout_seconds: float
    settled_at: str | None
    outcome: str | None
    sub_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_write_5m_tokens: int | None
    cache_write_1h_tokens: int | None
    cache_read_tokens: int | None
    reported_nanousd: int | None  # Claude Code 回報的花費估計
    list_nanousd: int | None
    settled_nanousd: int | None
    by_reservation: bool | None
    overrun: bool | None  # 入帳金額大於這一筆的預留,或 Claude Code 回報超過單次花費上限([S934])
    cost_mismatch: bool | None  # 回報的估計與 token 數算的差超過兩成
    latency_ms: float | None
    write_off_nanousd: int | None
    write_off_settled_before: bool | None

    @classmethod
    def from_row(cls, row: tuple[object, ...]) -> LedgerCall:
        values = dict(zip((f.name for f in fields(cls)), row, strict=True))
        for name in _BOOLEANS:  # 布林欄在 SQLite 裡存成 0/1
            values[name] = None if values[name] is None else bool(values[name])
        return cls(**values)  # type: ignore[arg-type]

    @property
    def effective_nanousd(self) -> int:
        """這筆算進已用的金額(規則見檔頭)。"""
        if self.write_off_nanousd is None:
            return self.reserved_nanousd if self.settled_nanousd is None else self.settled_nanousd
        if self.settled_nanousd is None or self.write_off_settled_before:
            return self.write_off_nanousd
        return max(self.write_off_nanousd, self.settled_nanousd)


_BOOLEANS = ("by_reservation", "overrun", "cost_mismatch", "write_off_settled_before")


class ModelLedgerView:
    """花費帳的唯讀開法:唯讀連線、不取寫入鎖、不建表;檔案不存在丟 FileNotFoundError,還沒建齊表丟
    DatabaseNotUpgraded。"""

    def __init__(self, path: Path) -> None:
        self._conn = connect_read_only(Path(path))
        try:
            missing = missing_schema(self._conn, TABLES, INDEXES)
        except BaseException:
            self._conn.close()
            raise
        if missing:
            self._conn.close()
            raise DatabaseNotUpgraded(f"花費帳缺:{', '.join(missing)}")

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        with read_snapshot(self._conn):
            yield self._conn

    def calls_between(self, start: str, end: str) -> tuple[LedgerCall, ...]:
        """預留時間在 [start, end) 的呼叫(ISO 字串比較;時間一律存成 UTC),依預留順序。"""
        rows = self._conn.execute(
            CALLS_SELECT + "WHERE r.reserved_at >= ? AND r.reserved_at < ? ORDER BY r.id",
            (start, end)).fetchall()
        return tuple(LedgerCall.from_row(row) for row in rows)

    def close(self) -> None:
        self._conn.close()
