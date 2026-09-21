"""Mock DSP 的儲存層:廣告狀態、版本、操作歷史與冪等紀錄。

不變量:一次操作的「狀態變更、操作歷史、冪等紀錄」在同一個交易裡一起提交或一起回滾;
同一把冪等鍵最多套用一次;寫入交易一律用 BEGIN IMMEDIATE,所以並行的同鍵請求會排隊,
後到的看見已提交的冪等紀錄後直接回原結果。
"""

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rtb.dsp.errors import (
    CampaignNotFound,
    IdempotencyConflict,
    MetricsNotFound,
    StoreBusy,
    UnknownAction,
    ValidationRejected,
    VersionConflict,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY, budget INTEGER NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
    operation_id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
    action TEXT NOT NULL, params_json TEXT NOT NULL, version_after INTEGER NOT NULL,
    received_at TEXT NOT NULL, committed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, operation_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
    campaign_id TEXT NOT NULL, window_name TEXT NOT NULL, impressions INTEGER, clicks INTEGER,
    conversions INTEGER, spend REAL, revenue REAL, PRIMARY KEY (campaign_id, window_name));
"""

BUSY_TIMEOUT_SECONDS = 5.0
SQLITE_INTEGER_MAX = 2**63 - 1
METRIC_WINDOWS = frozenset({"1h", "1d", "7d"})
COUNT_FIELDS = ("impressions", "clicks", "conversions")  # METRIC_FIELDS 的子集:存成整數
AMOUNT_FIELDS = ("spend", "revenue")  # 存成 REAL
METRIC_FIELDS = COUNT_FIELDS + AMOUNT_FIELDS
EXACT_FLOAT_INT_MAX = 2**53  # 超過這個大小的整數放進浮點數會失真
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")  # 用 fullmatch,避免 $ 放行結尾換行


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Campaign:
    id: str
    budget: int
    status: str
    version: int


@dataclass(frozen=True)
class Operation:
    campaign_id: str
    action: str
    params: dict
    expected_version: int
    idempotency_key: str

    def fingerprint(self) -> str:
        body = [self.campaign_id, self.action, self.params, self.expected_version]
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class OperationResult:
    operation_id: int
    campaign_id: str
    action: str
    version_after: int
    committed_at: str
    replayed: bool


@dataclass(frozen=True)
class MetricsRecord:
    """DSP 回報的原始事實;沒有的欄位維持 None,不會被換成 0。"""

    campaign_id: str
    window: str
    impressions: int | None
    clicks: int | None
    conversions: int | None
    spend: float | None
    revenue: float | None


@dataclass(frozen=True)
class HistoryEntry:
    operation_id: int
    action: str
    version_after: int
    received_at: str
    committed_at: str
    idempotency_key: str


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(op: Operation) -> None:
    if not isinstance(op.idempotency_key, str) or not IDEMPOTENCY_KEY_PATTERN.fullmatch(
        op.idempotency_key
    ):
        raise ValidationRejected("冪等鍵必須是 1 到 128 個英數字或 . _ : -")
    if not _is_plain_int(op.expected_version) or op.expected_version < 1:
        raise ValidationRejected("expected_version 必須是正整數")
    if op.action == "update_budget":
        budget = op.params.get("new_budget")
        if not _is_plain_int(budget) or not 0 < budget <= SQLITE_INTEGER_MAX:
            raise ValidationRejected("new_budget 必須是 1 到 2**63-1 的整數")
    elif op.action != "pause_campaign":
        raise UnknownAction(op.action)


def _is_storable_count(value: object) -> bool:
    return _is_plain_int(value) and abs(value) <= SQLITE_INTEGER_MAX


def _is_storable_amount(value: object) -> bool:
    if _is_plain_int(value):
        return abs(value) <= EXACT_FLOAT_INT_MAX
    return isinstance(value, float) and math.isfinite(value)


def _checked_metric(name: str, value: object) -> float | None:
    """只收存進去不會失真、也不會被讀成別的東西的值;沒給的欄位維持 None,不補成 0。

    金額欄位是 REAL,整數讀回會是浮點數(12 變成 12.0);所以整數只收到浮點數能精確表示的大小。
    """
    if value is None:
        return None
    is_valid = _is_storable_count if name in COUNT_FIELDS else _is_storable_amount
    if not is_valid(value):
        raise ValidationRejected(f"{name} 的值不合法:{value!r}")
    return value


def _check_window(window: str | None) -> None:
    if window not in METRIC_WINDOWS:
        raise ValidationRejected("window 必須是 1h、1d 或 7d 其中之一")


def _next_state(campaign: Campaign, op: Operation) -> Campaign:
    if op.action == "update_budget":
        return Campaign(campaign.id, op.params["new_budget"], campaign.status, campaign.version + 1)
    return Campaign(campaign.id, campaign.budget, "paused", campaign.version + 1)


class CampaignStore:
    def __init__(
        self,
        path: Path,
        clock: Callable[[], str] = _utc_now,
        busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
    ):
        self._clock = clock
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout_seconds)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
        except BaseException:
            self._conn.close()  # 設定失敗時不留下沒人關的連線
            raise

    def close(self) -> None:
        self._conn.close()

    def seed_campaign(self, campaign_id: str, budget: int, status: str = "active") -> None:
        self._conn.execute(
            "INSERT INTO campaigns (id, budget, status, version) VALUES (?, ?, ?, 1)",
            (campaign_id, budget, status),
        )

    def get_campaign(self, campaign_id: str) -> Campaign:
        row = self._conn.execute(
            "SELECT id, budget, status, version FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise CampaignNotFound(campaign_id)
        return Campaign(*row)

    def seed_metrics(self, campaign_id: str, window: str, **fields: float | None) -> None:
        unknown = set(fields) - set(METRIC_FIELDS)
        if unknown:
            raise TypeError(f"不認得的指標欄位:{sorted(unknown)}")
        _check_window(window)
        self.get_campaign(campaign_id)  # 不存在的廣告不能留下讀不到的孤兒列
        values = [_checked_metric(name, fields.get(name)) for name in METRIC_FIELDS]
        self._conn.execute(
            "INSERT OR REPLACE INTO metrics (campaign_id, window_name, impressions, clicks, "
            "conversions, spend, revenue) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, window, *values),
        )

    def get_metrics(self, campaign_id: str, window: str | None) -> MetricsRecord:
        _check_window(window)
        self.get_campaign(campaign_id)  # 廣告不存在就回 CampaignNotFound
        row = self._conn.execute(
            "SELECT campaign_id, window_name, impressions, clicks, conversions, spend, revenue "
            "FROM metrics WHERE campaign_id = ? AND window_name = ?", (campaign_id, window),
        ).fetchone()
        if row is None:
            raise MetricsNotFound(f"{campaign_id}/{window}")
        return MetricsRecord(*row)

    def history(self, campaign_id: str) -> list[HistoryEntry]:
        rows = self._conn.execute(
            "SELECT operation_id, action, version_after, received_at, committed_at, "
            "idempotency_key FROM operations WHERE campaign_id = ? ORDER BY operation_id",
            (campaign_id,),
        ).fetchall()
        return [HistoryEntry(*row) for row in rows]

    def get_operation_by_key(self, key: str) -> OperationResult | None:
        return self._recorded_result(key, replayed=False)  # 查詢不是重放

    def execute(self, op: Operation) -> OperationResult:
        _validate(op)
        received_at = self._clock()
        self._begin_write_transaction()
        try:
            result = self._execute_in_transaction(op, received_at)
            self._conn.execute("COMMIT")
            return result
        except BaseException:
            if self._conn.in_transaction:  # SQLite 有時已自行回滾;再回滾會蓋掉真正的原因
                self._conn.execute("ROLLBACK")
            raise

    def _begin_write_transaction(self) -> None:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            primary_code = exc.sqlite_errorcode & 0xFF  # 擴充碼的低 8 位才是主要錯誤碼
            if primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise StoreBusy(str(exc)) from exc
            raise  # 唯讀資料庫、磁碟錯誤等永久故障,不能偽裝成可重試

    def _execute_in_transaction(self, op: Operation, received_at: str) -> OperationResult:
        existing = self._existing_operation(op)
        if existing is not None:
            return existing
        current = self.get_campaign(op.campaign_id)
        if current.version != op.expected_version:
            raise VersionConflict(f"預期 {op.expected_version},目前 {current.version}")
        updated = _next_state(current, op)
        committed_at = self._clock()
        operation_id = self._apply(op, updated, received_at, committed_at)
        self._record_idempotency(op, operation_id)
        return OperationResult(
            operation_id, op.campaign_id, op.action, updated.version, committed_at, False
        )

    def _existing_operation(self, op: Operation) -> OperationResult | None:
        row = self._conn.execute(
            "SELECT fingerprint FROM idempotency_keys WHERE key = ?", (op.idempotency_key,)
        ).fetchone()
        if row is None:
            return None
        if row[0] != op.fingerprint():
            raise IdempotencyConflict(op.idempotency_key)
        return self._recorded_result(op.idempotency_key, replayed=True)

    def _recorded_result(self, key: str, replayed: bool) -> OperationResult | None:
        row = self._conn.execute(
            "SELECT operation_id, campaign_id, action, version_after, committed_at "
            "FROM operations WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        return None if row is None else OperationResult(*row, replayed=replayed)

    def _apply(
        self, op: Operation, updated: Campaign, received_at: str, committed_at: str
    ) -> int:
        self._conn.execute(
            "UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?",
            (updated.budget, updated.status, updated.version, updated.id),
        )
        cursor = self._conn.execute(
            "INSERT INTO operations (campaign_id, action, params_json, version_after, "
            "received_at, committed_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                op.campaign_id,
                op.action,
                json.dumps(op.params, sort_keys=True),
                updated.version,
                received_at,
                committed_at,
                op.idempotency_key,
            ),
        )
        return cursor.lastrowid

    def _record_idempotency(self, op: Operation, operation_id: int) -> None:
        self._conn.execute(
            "INSERT INTO idempotency_keys (key, fingerprint, operation_id) VALUES (?, ?, ?)",
            (op.idempotency_key, op.fingerprint(), operation_id),
        )
