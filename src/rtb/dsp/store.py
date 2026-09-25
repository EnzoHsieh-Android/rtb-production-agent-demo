"""Mock DSP 的儲存層:廣告狀態、版本、操作歷史與冪等紀錄。

不變量:一次操作的「狀態變更、操作歷史、冪等紀錄」在同一個交易裡一起提交或一起回滾;
同一把冪等鍵最多套用一次;寫入交易一律用 BEGIN IMMEDIATE,所以並行的同鍵請求會排隊,
後到的看見已提交的冪等紀錄後直接回原結果。

作廢(執行行程對帳判失敗之前的證明):作廢與寫入都在同一把寫入鎖下進行,寫入在拿到鎖之後、
冪等重放判斷之後才查作廢,所以結果只有兩種——寫入先提交(作廢回已提交),或作廢先成功(之後
同鍵寫入一律拒收)。作廢只看鍵,不碰憑證、不碰時間、不進操作指紋;作廢紀錄永久保留。
"""

import contextlib
import hashlib
import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeGuard

from rtb.dsp.errors import (
    AdjustmentsNotFound,
    CampaignNotFound,
    DailyNotFound,
    IdempotencyConflict,
    MetricsNotFound,
    OperationVoided,
    StoreBusy,
    UnknownAction,
    ValidationRejected,
    VersionConflict,
)
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy, begin_immediate, connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY, budget INTEGER NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
    tenant TEXT NOT NULL DEFAULT 't-default', name TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS operations (
    operation_id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
    action TEXT NOT NULL, params_json TEXT NOT NULL, version_after INTEGER NOT NULL,
    received_at TEXT NOT NULL, committed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
    policy_version TEXT, expected_version INTEGER);
CREATE INDEX IF NOT EXISTS operations_by_commit ON operations (committed_at);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, operation_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS voided_keys (key TEXT PRIMARY KEY, voided_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
    campaign_id TEXT NOT NULL, window_name TEXT NOT NULL, impressions INTEGER, clicks INTEGER,
    conversions INTEGER, spend REAL, revenue REAL, PRIMARY KEY (campaign_id, window_name));
CREATE TABLE IF NOT EXISTS daily_metrics (
    campaign_id TEXT NOT NULL, days_ago INTEGER NOT NULL, impressions INTEGER, clicks INTEGER,
    conversions INTEGER, spend REAL, revenue REAL, no_data INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, days_ago));
CREATE TABLE IF NOT EXISTS past_adjustment_seeds (campaign_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS past_adjustments (
    campaign_id TEXT NOT NULL, rank INTEGER NOT NULL, days_ago INTEGER NOT NULL,
    budget_before INTEGER NOT NULL, budget_after INTEGER NOT NULL,
    before_impressions INTEGER, before_clicks INTEGER, before_conversions INTEGER,
    before_spend REAL, before_revenue REAL,
    after_impressions INTEGER, after_clicks INTEGER, after_conversions INTEGER,
    after_spend REAL, after_revenue REAL, PRIMARY KEY (campaign_id, rank));
"""

SQLITE_INTEGER_MAX = 2**63 - 1
# 列操作端點一次最多回幾筆(Phase 9 增量 3 副作用核對翻頁用)。設計寫「最多 5000 筆」;實際取 50:
# 讀它的共用 HTTP 用戶端回應上限是 64 KB,每筆最長約 700 位元組,5000 筆會超過上限整頁讀不回來
OPERATION_PAGE = 50
DEFAULT_TENANT = "t-default"  # 沒指定租戶的廣告(含補欄位前的舊資料)都屬於它
# 廣告名稱的字元上限:真實 DSP 的名稱都有上限。最壞情況(每字都要代理對、回應用 ASCII 逃脫時
# 每字 12 位元組)4096 字約 48 KB,仍低於分析端 64 KB 的回應上限,名稱塞不爆回應
MAX_CAMPAIGN_NAME_LENGTH = 4096
DEFAULT_CAMPAIGN_NAME = ""  # 沒給名稱的廣告(含補欄位前的舊資料)名稱是空字串
METRIC_WINDOWS = frozenset({"1h", "1d", "7d"})
COUNT_FIELDS = ("impressions", "clicks", "conversions")  # METRIC_FIELDS 的子集:存成整數
AMOUNT_FIELDS = ("spend", "revenue")  # 存成 REAL
METRIC_FIELDS = COUNT_FIELDS + AMOUNT_FIELDS
# 逐日成效與過去調整(Phase 13 增量 2):只由展示種子寫,端點只讀。用「第幾天前」不用日期:送給模型的
# 內容要逐位元組穩定(錄製鍵),也不送時間戳
DAILY_DAYS = 7
MAX_PAST_ADJUSTMENTS = 5
MIN_ADJUSTMENT_AGE_DAYS = 3  # 要有調整後 3 天才算得出前後對照
EXACT_FLOAT_INT_MAX = 2**53  # 超過這個大小的整數放進浮點數會失真
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")  # 用 fullmatch,避免 $ 放行結尾換行


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _moment(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def commit_text(moment: datetime) -> str:
    """提交時間的固定寫法:換算成 UTC 的 isoformat(+00:00;微秒是 0 時省略)。寫入操作紀錄與依時間
    找起點都用這一支,字串比較才等於時間比較(代碼審第 1 輪:時鐘可注入 -05:00 這種偏移,原樣存下
    會讓字串順序跟時間順序對不上)。同一秒內省略微秒的那種寫法排在帶微秒的前面('+' 小於 '.'),
    剛好也是時間順序;預設時鐘一直是這種寫法,所以既有資料不用搬。沒帶時區的當 UTC。"""
    value = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def operation_cursor_query(since: datetime) -> tuple[str, tuple[str]]:
    """依時間找起點的查詢語句(測試用它看查詢計畫):提交時間大於等於 since 的第一筆。提交時間跟
    寫入時同一種寫法(commit_text),字串比較就是時間比較。"""
    return ("SELECT operation_id FROM operations WHERE committed_at >= ? "
            "ORDER BY committed_at, operation_id LIMIT 1",
            (commit_text(since),))


@dataclass(frozen=True)
class Campaign:
    id: str
    budget: int
    status: str
    version: int
    name: str  # 不可信文字:只在建檔時設定,沒有寫入端點能改(比照租戶);不給預設值,改狀態時必須沿用


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
    idempotency_key: str = ""
    params: dict[str, object] | None = None
    expected_version: int | None = None  # 補欄位之前寫的舊操作沒有記,誠實回空值


@dataclass(frozen=True)
class VoidResult:
    state: str  # "voided":已作廢(之後同鍵寫入一律拒收);"committed":這把鍵已先提交
    operation: OperationResult | None = None


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
class DailyRow:
    """一天的成效(第 1 天 = 昨天);缺資料那天五欄都是 None、no_data 為真,不補零、不省略整列。"""

    days_ago: int
    impressions: int | None
    clicks: int | None
    conversions: int | None
    spend: float | None
    revenue: float | None
    no_data: bool


@dataclass(frozen=True)
class PastAdjustment:
    """3 天以前的一筆預算調整,帶調整前 3 天與後 3 天的成效總量(各五欄)。"""

    days_ago: int
    budget_before: int
    budget_after: int
    before: Mapping[str, float | None]
    after: Mapping[str, float | None]


@dataclass(frozen=True)
class PastBudgetChange:
    """只給展示種子用的過去操作:某個廣告在幾天前把預算改成多少。"""

    campaign_id: str
    days_ago: int
    new_budget: int


@dataclass(frozen=True)
class HistorySeed:
    """一個廣告要種的歷史(展示種子用):成效窗(1 天、7 天)、7 天逐日、過去調整。"""

    campaign_id: str
    windows: Mapping[str, Mapping[str, float | None]]
    daily: Sequence[Mapping[str, float | None] | None]
    adjustments: Sequence[PastAdjustment]


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


def validate_key_and_version(key: object, expected_version: object) -> None:
    """寫入與作廢共用的格式檢查:冪等鍵格式、預期版本是正整數。"""
    if not isinstance(key, str) or not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise ValidationRejected("冪等鍵必須是 1 到 128 個英數字或 . _ : -")
    if not is_plain_int(expected_version) or not 0 < expected_version <= SQLITE_INTEGER_MAX:
        raise ValidationRejected("expected_version 必須是正整數且不超過資料庫整數上限")


def _validate(op: Operation) -> None:
    validate_key_and_version(op.idempotency_key, op.expected_version)
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


def _figures(values: Mapping[str, float | None]) -> list[float | None]:
    """成效五欄照固定順序取出並驗值(跟 1 小時窗同一套);多給的欄位拒絕。"""
    unknown = set(values) - set(METRIC_FIELDS)
    if unknown:
        raise TypeError(f"不認得的指標欄位:{sorted(unknown)}")
    return [_checked_metric(name, values.get(name)) for name in METRIC_FIELDS]


def _check_window(window: str | None) -> None:
    if window not in METRIC_WINDOWS:
        raise ValidationRejected("window 必須是 1h、1d 或 7d 其中之一")


def _check_campaign_name(name: object) -> None:
    """名稱只收字串、不過濾內容;超過上限、不是字串、或含寫不進資料庫的字元(孤立的代理字元,
    SQLite 只收合法 UTF-8)一律拒絕建檔。"""
    if not isinstance(name, str) or len(name) > MAX_CAMPAIGN_NAME_LENGTH:
        raise ValidationRejected(f"廣告名稱必須是最多 {MAX_CAMPAIGN_NAME_LENGTH} 字元的字串")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValidationRejected("廣告名稱含寫不進資料庫的字元") from exc


def _next_state(campaign: Campaign, op: Operation) -> Campaign:
    # 名稱照抄只是因為 Campaign 必填;名稱不被寫入改掉,靠的是 _apply 的 UPDATE 根本不寫名稱欄
    if op.action == "update_budget":
        budget = op.params.get("new_budget")
        if not is_plain_int(budget):  # _validate 已檢查過;這裡讓型別檢查也能確認
            raise ValidationRejected("new_budget 必須是整數")
        return Campaign(campaign.id, budget, campaign.status, campaign.version + 1, campaign.name)
    return Campaign(campaign.id, campaign.budget, "paused", campaign.version + 1, campaign.name)


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
        設定,DSP 沒有任何改廣告租戶的寫入介面)、廣告名稱(舊廣告補空字串,同樣只在建檔時設定)、
        操作紀錄的政策版本與預期版本(舊操作留空值)。
        """
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(campaigns)")}
        op_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(operations)")}
        if {"tenant", "name"} <= columns and {"policy_version", "expected_version"} <= op_columns:
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
            if "name" not in columns:  # 舊廣告沒有名稱,誠實補空字串
                self._conn.execute(
                    "ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_CAMPAIGN_NAME}'")
            op_columns = {r[1] for r in self._conn.execute("PRAGMA table_info(operations)")}
            if "policy_version" not in op_columns:  # 舊操作沒有紀錄政策版本,誠實留空值
                self._conn.execute("ALTER TABLE operations ADD COLUMN policy_version TEXT")
            if "expected_version" not in op_columns:  # 舊操作沒有紀錄預期版本,誠實留空值
                self._conn.execute("ALTER TABLE operations ADD COLUMN expected_version INTEGER")
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()

    def seed_campaign(
        self, campaign_id: str, budget: int, status: str = "active",
        tenant: str = DEFAULT_TENANT, name: str = DEFAULT_CAMPAIGN_NAME,
    ) -> None:
        _check_campaign_name(name)
        self._conn.execute(
            "INSERT INTO campaigns (id, budget, status, version, tenant, name) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (campaign_id, budget, status, tenant, name),
        )

    def tenant_of(self, campaign_id: str) -> str | None:
        """廣告屬於哪個租戶;廣告不存在回 None(驗證憑證時算範圍不符,不是 404)。"""
        row = self._conn.execute(
            "SELECT tenant FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return None if row is None else str(row[0])

    def get_campaign(self, campaign_id: str) -> Campaign:
        row = self._conn.execute(
            "SELECT id, budget, status, version, name FROM campaigns WHERE id = ?", (campaign_id,)
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
        self._write_metrics(campaign_id, window, fields)

    def _write_metrics(self, campaign_id: str, window: str,
                       fields: Mapping[str, float | None]) -> None:
        _check_window(window)
        values = _figures(fields)
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

    def seed_daily(self, campaign_id: str,
                   days: Sequence[Mapping[str, float | None] | None]) -> None:
        """種逐日成效(展示種子用):恰好 7 天,第 1 天在最前;None 是那天缺資料(列照樣在)。"""
        self.get_campaign(campaign_id)
        with self._seed_transaction():
            self._write_daily(campaign_id, days)

    def _write_daily(self, campaign_id: str,
                     days: Sequence[Mapping[str, float | None] | None]) -> None:
        if len(days) != DAILY_DAYS:
            raise ValidationRejected(f"逐日成效要恰好 {DAILY_DAYS} 天")
        for days_ago, day in enumerate(days, start=1):
            values = [None] * len(METRIC_FIELDS) if day is None else _figures(day)
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, days_ago, *values, int(day is None)))

    def get_daily(self, campaign_id: str) -> list[DailyRow]:
        self.get_campaign(campaign_id)
        rows = self._conn.execute(
            "SELECT days_ago, impressions, clicks, conversions, spend, revenue, no_data "
            "FROM daily_metrics WHERE campaign_id = ? ORDER BY days_ago", (campaign_id,)).fetchall()
        if not rows:
            raise DailyNotFound(campaign_id)
        return [DailyRow(row[0], row[1], row[2], row[3], row[4], row[5], bool(row[6]))
                for row in rows]

    def seed_past_adjustments(self, campaign_id: str,
                              adjustments: Sequence[PastAdjustment]) -> None:
        """種過去調整(展示種子用):最多 5 筆、由新到舊、每筆至少 3 天前、調整前後的預算是非負整數;
        零筆也記「種過了」。重種會先清掉這個廣告舊的過去調整(代碼審 r1 d4:重種成較少筆時,序號較大的
        舊列留著、順序也亂掉)。"""
        self.get_campaign(campaign_id)
        with self._seed_transaction():
            self._write_adjustments(campaign_id, adjustments)

    def _write_adjustments(self, campaign_id: str,
                           adjustments: Sequence[PastAdjustment]) -> None:
        ages = [item.days_ago for item in adjustments]
        if (len(adjustments) > MAX_PAST_ADJUSTMENTS
                or any(not is_plain_int(age) or age < MIN_ADJUSTMENT_AGE_DAYS for age in ages)
                or ages != sorted(ages)):
            raise ValidationRejected("過去調整最多 5 筆、由新到舊、每筆至少 3 天前")
        if any(not is_plain_int(b) or not 0 <= b <= SQLITE_INTEGER_MAX
               for item in adjustments for b in (item.budget_before, item.budget_after)):
            raise ValidationRejected("調整前後的預算要是非負整數")
        self._conn.execute("INSERT OR IGNORE INTO past_adjustment_seeds VALUES (?)", (campaign_id,))
        self._conn.execute("DELETE FROM past_adjustments WHERE campaign_id = ?", (campaign_id,))
        for rank, item in enumerate(adjustments, start=1):
            self._conn.execute(
                "INSERT INTO past_adjustments VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, rank, item.days_ago, item.budget_before, item.budget_after,
                 *_figures(item.before), *_figures(item.after)))

    def get_past_adjustments(self, campaign_id: str) -> list[PastAdjustment]:
        self.get_campaign(campaign_id)
        if self._conn.execute("SELECT 1 FROM past_adjustment_seeds WHERE campaign_id = ?",
                              (campaign_id,)).fetchone() is None:
            raise AdjustmentsNotFound(campaign_id)
        rows = self._conn.execute(
            "SELECT days_ago, budget_before, budget_after, before_impressions, before_clicks, "
            "before_conversions, before_spend, before_revenue, after_impressions, after_clicks, "
            "after_conversions, after_spend, after_revenue FROM past_adjustments "
            "WHERE campaign_id = ? ORDER BY rank", (campaign_id,)).fetchall()
        return [PastAdjustment(row[0], row[1], row[2], dict(zip(METRIC_FIELDS, row[3:8],
                                                               strict=True)),
                               dict(zip(METRIC_FIELDS, row[8:13], strict=True))) for row in rows]

    def seed_history(self, entries: Sequence[HistorySeed], changes: Sequence[PastBudgetChange],
                     now: datetime) -> None:
        """**只准展示種子呼叫**:全平台的 1 天與 7 天窗、逐日成效、過去調整與過去操作在同一個交易裡
        寫,
        全有或全無(代碼審 r1 d4:原本逐廣告各自提交,最後一步被拒時前面已經寫進去,資料違反
        [S1127])。"""
        for entry in entries:
            self.get_campaign(entry.campaign_id)
        with self._seed_transaction():
            for entry in entries:
                for window, fields in entry.windows.items():
                    self._write_metrics(entry.campaign_id, window, fields)
                self._write_daily(entry.campaign_id, entry.daily)
                self._write_adjustments(entry.campaign_id, entry.adjustments)
            self._write_past_operations(changes, now)

    def seed_past_operations(self, changes: Sequence[PastBudgetChange], now: datetime) -> None:
        """**只准展示種子呼叫**(Phase 13 [S1153],測試掃全庫守):把過去幾天的預算調整寫進操作紀錄。
        既有唯一的寫入路徑把提交時間取成時鐘讀數、而且不早於上一筆,寫不出「10 天前」;所以另開這支,
        只准在平台還沒有任何操作時呼叫(展示每個情境都重建資料庫,種子在最前面),一次收全平台要種的
        過去操作,依時間先後寫(最舊的先寫),寫完提交時間與操作編號的順序一致、單調不減。廣告的預算
        與版本照每一筆往前推,跟真的寫過一樣;既有寫入路徑一行不改。"""
        with self._seed_transaction():
            self._write_past_operations(changes, now)

    def _write_past_operations(self, changes: Sequence[PastBudgetChange], now: datetime) -> None:
        if any(not is_plain_int(c.days_ago) or c.days_ago < 1 for c in changes):
            raise ValidationRejected("過去的操作至少要是 1 天前")
        if not changes:
            return
        if self._conn.execute("SELECT 1 FROM operations LIMIT 1").fetchone() is not None:
            raise ValidationRejected("平台已經有操作:只准在還沒有任何操作時種過去的操作")
        ordered = sorted(enumerate(changes), key=lambda pair: (-pair[1].days_ago, pair[0]))
        for index, change in ordered:
            at = commit_text(now - timedelta(days=change.days_ago))
            current = self.get_campaign(change.campaign_id)
            op = Operation(change.campaign_id, "update_budget",
                           {"new_budget": change.new_budget}, current.version,
                           f"seed-past-{index}")
            _validate(op)
            operation_id = self._apply(op, _next_state(current, op), at, at)
            self._record_idempotency(op, operation_id)

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

    @contextlib.contextmanager
    def _seed_transaction(self) -> Iterator[None]:
        """種子的多列寫入:一起提交或一起回滾(同寫入路徑用 BEGIN IMMEDIATE)。"""
        self._begin_write_transaction()
        try:
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def _begin_write_transaction(self) -> None:
        try:
            begin_immediate(self._conn)
        except DatabaseBusy as exc:
            raise StoreBusy(str(exc)) from exc

    def void(self, key: str) -> VoidResult:
        """作廢一把冪等鍵:已有操作紀錄就不作廢、回已提交;沒有就記下作廢(再作廢也回已作廢)。"""
        validate_key_and_version(key, 1)
        voided_at = self._clock()
        self._begin_write_transaction()
        try:
            committed = self._recorded_result(key, replayed=False)
            if committed is None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO voided_keys (key, voided_at) VALUES (?, ?)",
                    (key, voided_at))
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise
        return VoidResult("voided") if committed is None else VoidResult("committed", committed)

    def _execute_in_transaction(self, op: Operation, received_at: str) -> OperationResult:
        existing = self._existing_operation(op)
        if existing is not None:
            return existing
        # 這段查詢必須留在寫入交易內(已經拿到寫入鎖):搬到交易外的話,舊請求會先查到「沒作廢」、
        # 等作廢完成之後才提交,就繞過了作廢
        if self._conn.execute(
                "SELECT 1 FROM voided_keys WHERE key = ?", (op.idempotency_key,)).fetchone():
            raise OperationVoided(op.idempotency_key)
        current = self.get_campaign(op.campaign_id)
        if current.version != op.expected_version:
            raise VersionConflict(f"預期 {op.expected_version},目前 {current.version}")
        updated = _next_state(current, op)
        committed_at = self._not_before_last(self._clock())
        operation_id = self._apply(op, updated, received_at, committed_at)
        self._record_idempotency(op, operation_id)
        return OperationResult(
            operation_id, op.campaign_id, op.action, updated.version, committed_at, False,
            op.idempotency_key, dict(op.params),
            op.expected_version if is_plain_int(op.expected_version) else None,
        )

    def _not_before_last(self, reading: str) -> str:
        """提交時間取時鐘讀數與上一筆提交時間較大的那個(Phase 9 增量 3):依操作編號翻頁才等於依
        時間翻頁。時鐘倒退時提交時間會被墊高到上一筆。在寫入交易裡讀,上一筆不會被別人插隊。
        回傳前統一成固定寫法(commit_text)。"""
        moment = _moment(reading)
        row = self._conn.execute(
            "SELECT committed_at FROM operations ORDER BY operation_id DESC LIMIT 1").fetchone()
        if row is not None and _moment(row[0]) > moment:
            moment = _moment(row[0])
        return commit_text(moment)

    def operation_cursor(self, since: datetime) -> int:
        """提交時間大於等於 since 的第一筆之前的最後一個操作編號;沒有更早的回 0,沒有任何一筆
        大於等於 since 回目前最大的編號(之後列操作就從這裡往下)。走提交時間索引。"""
        sql, params = operation_cursor_query(since)
        first = self._conn.execute(sql, params).fetchone()
        if first is None:
            row = self._conn.execute("SELECT max(operation_id) FROM operations").fetchone()
        else:
            row = self._conn.execute("SELECT max(operation_id) FROM operations "
                                     "WHERE operation_id < ?", (first[0],)).fetchone()
        return int(row[0] or 0)

    def operations_after(self, cursor: int) -> tuple[list[dict[str, Any]], int | None]:
        """操作編號大於 cursor 的操作,依編號由小到大最多 OPERATION_PAGE 筆,連同廣告建檔時的租戶;
        第二個值是下一頁的游標(這一頁最後一筆的編號),沒有下一頁為空。"""
        rows = self._conn.execute(
            "SELECT o.operation_id, o.idempotency_key, o.campaign_id, c.tenant, o.action, "
            "o.params_json, o.expected_version, o.committed_at, o.policy_version "
            "FROM operations o LEFT JOIN campaigns c ON c.id = o.campaign_id "
            "WHERE o.operation_id > ? ORDER BY o.operation_id LIMIT ?",
            (cursor, OPERATION_PAGE)).fetchall()
        found = [{"operation_id": r[0], "idempotency_key": r[1], "campaign_id": r[2],
                  "tenant": r[3], "action": r[4],
                  "new_budget": json.loads(r[5]).get("new_budget"),
                  "expected_version": r[6], "committed_at": r[7], "policy_version": r[8]}
                 for r in rows]
        return found, (found[-1]["operation_id"] if len(found) == OPERATION_PAGE else None)

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
            "SELECT operation_id, campaign_id, action, version_after, committed_at, params_json, "
            "expected_version FROM operations WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        operation_id, campaign_id, action, version_after, committed_at, params, expected = row
        return OperationResult(
            operation_id, campaign_id, action, version_after, committed_at, replayed,
            key, json.loads(params), expected if is_plain_int(expected) else None,
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
            "received_at, committed_at, idempotency_key, policy_version, expected_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                op.campaign_id,
                op.action,
                json.dumps(op.params, sort_keys=True),
                updated.version,
                received_at,
                committed_at,
                op.idempotency_key,
                op.policy_version,
                op.expected_version,
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
