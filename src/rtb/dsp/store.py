"""Mock DSP 的儲存層:廣告狀態、版本、操作歷史與冪等紀錄。

不變量:一次操作的「狀態變更、操作歷史、冪等紀錄」在同一個交易裡一起提交或一起回滾;
同一把冪等鍵最多套用一次;寫入交易一律用 BEGIN IMMEDIATE,所以並行的同鍵請求會排隊,
後到的看見已提交的冪等紀錄後直接回原結果。
"""

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeGuard

from rtb.dsp.errors import (
    CampaignNotFound,
    IdempotencyConflict,
    MetricsNotFound,
    StoreBusy,
    UnknownAction,
    ValidationRejected,
    VersionConflict,
)
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy, begin_immediate, connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY, budget INTEGER NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
    tenant TEXT NOT NULL DEFAULT 't-default');
CREATE TABLE IF NOT EXISTS operations (
    operation_id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
    action TEXT NOT NULL, params_json TEXT NOT NULL, version_after INTEGER NOT NULL,
    received_at TEXT NOT NULL, committed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
    policy_version TEXT);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, operation_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
    campaign_id TEXT NOT NULL, window_name TEXT NOT NULL, impressions INTEGER, clicks INTEGER,
    conversions INTEGER, spend REAL, revenue REAL, PRIMARY KEY (campaign_id, window_name));
"""

SQLITE_INTEGER_MAX = 2**63 - 1
DEFAULT_TENANT = "t-default"  # 沒指定租戶的廣告(含補欄位前的舊資料)都屬於它
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
    params: dict[str, object]  # 來自不可信的請求:先當作未驗證的值,_validate 會檢查內容
    expected_version: object  # 同上:先當作未驗證的值,_validate 確認是正整數
    idempotency_key: str
    policy_version: str | None = None  # 憑證上的政策版本:只記不驗,供稽核;不進指紋

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


def is_plain_int(value: object) -> TypeGuard[int]:
    """是整數而且不是布林;DSP 自己一份(刻意不依賴領域層),DSP 內部各模組共用這一份。"""
    return isinstance(value, int) and not isinstance(value, bool)


def validate(op: Operation) -> None:
    """操作內容的合法性(冪等鍵格式、預期版本、動作、預算)。寫入端點在比對憑證範圍之前先跑,
    讓不合法的值照舊回 422 而不是被當成範圍不符。"""
    _validate(op)


def _validate(op: Operation) -> None:
    if not isinstance(op.idempotency_key, str) or not IDEMPOTENCY_KEY_PATTERN.fullmatch(
        op.idempotency_key
    ):
        raise ValidationRejected("冪等鍵必須是 1 到 128 個英數字或 . _ : -")
    if not is_plain_int(op.expected_version) or op.expected_version < 1:
        raise ValidationRejected("expected_version 必須是正整數")
    if op.action == "update_budget":
        budget = op.params.get("new_budget")
        if not is_plain_int(budget) or not 0 < budget <= SQLITE_INTEGER_MAX:
            raise ValidationRejected("new_budget 必須是 1 到 2**63-1 的整數")
    elif op.action != "pause_campaign":
        raise UnknownAction(op.action)


def _is_storable_count(value: object) -> TypeGuard[int]:
    return is_plain_int(value) and abs(value) <= SQLITE_INTEGER_MAX


def _is_storable_amount(value: object) -> TypeGuard[int | float]:
    if is_plain_int(value):
        return abs(value) <= EXACT_FLOAT_INT_MAX
    return isinstance(value, float) and math.isfinite(value)


def _checked_metric(name: str, value: object) -> float | None:
    """只收存進去不會失真、也不會被讀成別的東西的值;沒給的欄位維持 None,不補成 0。

    金額欄位是 REAL,整數讀回會是浮點數(12 變成 12.0);所以整數只收到浮點數能精確表示的大小。
    """
    if value is None:
        return None
    if name in COUNT_FIELDS and _is_storable_count(value):
        return value
    if name not in COUNT_FIELDS and _is_storable_amount(value):
        return value
    raise ValidationRejected(f"{name} 的值不合法:{value!r}")


def _check_window(window: str | None) -> None:
    if window not in METRIC_WINDOWS:
        raise ValidationRejected("window 必須是 1h、1d 或 7d 其中之一")


def _next_state(campaign: Campaign, op: Operation) -> Campaign:
    if op.action == "update_budget":
        budget = op.params.get("new_budget")
        if not is_plain_int(budget):  # _validate 已檢查過;這裡讓型別檢查也能確認
            raise ValidationRejected("new_budget 必須是整數")
        return Campaign(campaign.id, budget, campaign.status, campaign.version + 1)
    return Campaign(campaign.id, campaign.budget, "paused", campaign.version + 1)


class CampaignStore:
    def __init__(
        self,
        path: Path,
        clock: Callable[[], str] = _utc_now,
        busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
    ):
        self._clock = clock
        try:
            self._conn = connect(path, busy_timeout_seconds, SCHEMA)
        except DatabaseBusy as exc:
            raise StoreBusy(str(exc)) from exc
        try:
            self._migrate_columns()
        except DatabaseBusy as exc:
            self._conn.close()
            raise StoreBusy(str(exc)) from exc
        except BaseException:
            self._conn.close()  # 補欄位失敗時不留下沒人關的連線(三支資料庫模組同一寫法)
            raise

    def _migrate_columns(self) -> None:
        """`CREATE TABLE IF NOT EXISTS` 不會幫既有表補欄位:沿用分析行程歷史表的補欄位做法,
        每次連線檢查一次,缺就在交易內加欄位:廣告表的租戶(舊廣告補預設租戶;租戶只在建檔時
        設定,DSP 沒有任何改廣告租戶的寫入介面)、操作紀錄的政策版本(舊操作留空值)。
        """
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(campaigns)")}
        op_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(operations)")}
        if "tenant" in columns and "policy_version" in op_columns:
            return
        try:
            begin_immediate(self._conn)
        except DatabaseBusy as exc:
            raise StoreBusy(str(exc)) from exc
        try:
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(campaigns)")}
            if "tenant" not in columns:  # 等鎖期間別的連線可能已經補好
                self._conn.execute(
                    "ALTER TABLE campaigns ADD COLUMN tenant TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_TENANT}'")
            op_columns = {r[1] for r in self._conn.execute("PRAGMA table_info(operations)")}
            if "policy_version" not in op_columns:  # 舊操作沒有紀錄政策版本,誠實留空值
                self._conn.execute("ALTER TABLE operations ADD COLUMN policy_version TEXT")
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()

    def seed_campaign(
        self, campaign_id: str, budget: int, status: str = "active",
        tenant: str = DEFAULT_TENANT,
    ) -> None:
        self._conn.execute(
            "INSERT INTO campaigns (id, budget, status, version, tenant) VALUES (?, ?, ?, 1, ?)",
            (campaign_id, budget, status, tenant),
        )

    def tenant_of(self, campaign_id: str) -> str | None:
        """廣告屬於哪個租戶;廣告不存在回 None(驗證憑證時算範圍不符,不是 404)。"""
        row = self._conn.execute(
            "SELECT tenant FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return None if row is None else str(row[0])

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
            begin_immediate(self._conn)
        except DatabaseBusy as exc:
            raise StoreBusy(str(exc)) from exc

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
        if row is None:
            return None
        operation_id, campaign_id, action, version_after, committed_at = row
        return OperationResult(
            operation_id, campaign_id, action, version_after, committed_at, replayed
        )

    def _apply(
        self, op: Operation, updated: Campaign, received_at: str, committed_at: str
    ) -> int:
        self._conn.execute(
            "UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?",
            (updated.budget, updated.status, updated.version, updated.id),
        )
        cursor = self._conn.execute(
            "INSERT INTO operations (campaign_id, action, params_json, version_after, "
            "received_at, committed_at, idempotency_key, policy_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                op.campaign_id,
                op.action,
                json.dumps(op.params, sort_keys=True),
                updated.version,
                received_at,
                committed_at,
                op.idempotency_key,
                op.policy_version,
            ),
        )
        operation_id = cursor.lastrowid
        if operation_id is None:  # INSERT 一定會有列編號;沒有代表出了預期外的事,讓交易回滾
            raise RuntimeError("INSERT 沒有回傳列編號")
        return operation_id

    def _record_idempotency(self, op: Operation, operation_id: int) -> None:
        self._conn.execute(
            "INSERT INTO idempotency_keys (key, fingerprint, operation_id) VALUES (?, ?, ?)",
            (op.idempotency_key, op.fingerprint(), operation_id),
        )
