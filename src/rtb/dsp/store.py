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
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
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
from rtb.sqlitekit import (
    BUSY_TIMEOUT_SECONDS,
    DatabaseBusy,
    begin_immediate,
    connect,
    read_snapshot,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY, budget INTEGER NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
    tenant TEXT NOT NULL DEFAULT 't-default', name TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS operations (
    operation_id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
    action TEXT NOT NULL, params_json TEXT NOT NULL, version_after INTEGER NOT NULL,
    received_at TEXT NOT NULL, committed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
    policy_version TEXT, expected_version INTEGER, budget_before INTEGER);
CREATE INDEX IF NOT EXISTS operations_by_commit ON operations (committed_at);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, operation_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS voided_keys (key TEXT PRIMARY KEY, voided_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
    campaign_id TEXT NOT NULL, window_name TEXT NOT NULL, impressions INTEGER, clicks INTEGER,
    conversions INTEGER, spend INTEGER, revenue INTEGER, PRIMARY KEY (campaign_id, window_name));
CREATE TABLE IF NOT EXISTS daily_metrics (
    campaign_id TEXT NOT NULL, day_utc TEXT NOT NULL, impressions INTEGER, clicks INTEGER,
    conversions INTEGER, spend INTEGER, revenue INTEGER, no_data INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, day_utc));
CREATE TABLE IF NOT EXISTS daily_templates (
    campaign_id TEXT PRIMARY KEY, impressions INTEGER, clicks INTEGER, conversions INTEGER,
    spend INTEGER, revenue INTEGER);
CREATE TABLE IF NOT EXISTS operation_budget_backfill (
    operation_id INTEGER PRIMARY KEY, budget_before INTEGER NOT NULL);
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
AMOUNT_FIELDS = ("spend", "revenue")  # 存成整數分
METRIC_FIELDS = COUNT_FIELDS + AMOUNT_FIELDS
# 日期鍵只存在 DSP;端點仍用相對日數,避免改動既有收據。
DAILY_DAYS = 7
# 金額整數部分最多 13 位(整數分上限 10**15-1):跟分析端讀取白名單同一個數(rtb.domain._checks 的
# MAX_AMOUNT_WHOLE_DIGITS;DSP 是外部系統的模擬器,刻意不依賴領域層,所以自己一份)。13 位加兩位小數
# 是 15 位有效數字,分析端轉浮點不會差一分;七天加總最多約 7e15 分,也遠低於 SQLite 整數上限。
MAX_AMOUNT_WHOLE_DIGITS = 13
MAX_CENTS = 10 ** (MAX_AMOUNT_WHOLE_DIGITS + 2) - 1
_AMOUNT_TEXT = re.compile(rf"-?(?:0|[1-9][0-9]{{0,{MAX_AMOUNT_WHOLE_DIGITS - 1}}})\.[0-9]{{2}}",
                          re.ASCII)
# 日桶(含樣板)每天的上限是上面兩個上限的七分之一:1d/7d 與加額前後三天的合計在讀取時推算,
# 這樣任何合計都還在分析端讀取白名單的上限內(金額整數 13 位、計數 SQLITE_INTEGER_MAX),
# 不會「每天都存得進、讀較長時間窗卻整份 invalid」(代碼審 r2 鏡頭B 發現 1)。
# 1 小時窗是單一值,照原本的上限。
DAILY_MAX_CENTS = MAX_CENTS // DAILY_DAYS
DAILY_MAX_COUNT = SQLITE_INTEGER_MAX // DAILY_DAYS
MAX_PAST_ADJUSTMENTS = 5
HISTORY_PAGE = 50
ROLLING_DAYS = 30
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")  # 用 fullmatch,避免 $ 放行結尾換行


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _default_clock() -> str:
    """沒注入時鐘時用真實時鐘;呼叫時才查模組的 _utc_now,測試守衛能把它換成離固定日期很遠的時刻,
    讓「忘了注入時鐘又用固定日期種資料」的測試當場翻紅(代碼審 r2 鏡頭A)。"""
    return _utc_now()


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
    spend: str | None
    revenue: str | None


@dataclass(frozen=True)
class DailyRow:
    """一天的成效(第 1 天 = 昨天);缺資料那天五欄都是 None、no_data 為真,不補零、不省略整列。"""

    days_ago: int
    impressions: int | None
    clicks: int | None
    conversions: int | None
    spend: str | None
    revenue: str | None
    no_data: bool


@dataclass(frozen=True)
class PastAdjustment:
    """最近一次加額,含 D 前後各三個完整 UTC 日的成效。"""

    days_ago: int
    budget_before: int | None  # 舊資料庫遷移補不回前值時是 None(誠實缺證據,不猜)
    budget_after: int
    before: Mapping[str, int | str | None]
    after: Mapping[str, int | str | None]
    committed_at: str = ""


@dataclass(frozen=True)
class PastBudgetChange:
    """只給展示種子用的過去操作:某個廣告在幾天前把預算改成多少。"""

    campaign_id: str
    days_ago: int
    new_budget: int


@dataclass(frozen=True)
class HistorySeed:
    """一個廣告要種的歷史:七個帶 UTC 日期的完整日桶,與選填的完整日樣板(之後每個新完成的 UTC 日由
    它推出;沒給樣板的廣告,新完成的日子就是沒資料)。"""

    campaign_id: str
    daily: Sequence[Mapping[str, int | float | str | None] | None]
    template: Mapping[str, int | float | str | None] | None = None


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


def cents_of(value: object) -> int:
    """DSP 收進來的十進位金額只在這裡換算成整數分(展示種子的核對也用這一支):整數、有限浮點或固定兩位
    小數字串(只收 ASCII 數字);多於兩位小數、整數部分超過 13 位一律拒收(ValidationRejected),
    不讓極端值進資料庫(代碼審 r1:1e300 這類值原本在讀取端才出事)。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValidationRejected(f"金額不合法:{value!r}")
    if isinstance(value, str) and _AMOUNT_TEXT.fullmatch(value) is None:
        raise ValidationRejected(f"金額字串必須固定兩位小數、整數部分最多 "
                                 f"{MAX_AMOUNT_WHOLE_DIGITS} 位:{value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationRejected(f"金額不合法:{value!r}")
    try:
        amount = Decimal(str(value)).scaleb(2)
    except InvalidOperation as exc:
        raise ValidationRejected(f"金額不合法:{value!r}") from exc
    if (not amount.is_finite() or amount != amount.to_integral_value()
            or abs(amount) > MAX_CENTS):
        raise ValidationRejected(f"金額不是可儲存的整數分(多於兩位小數或整數部分超過 "
                                 f"{MAX_AMOUNT_WHOLE_DIGITS} 位):{value!r}")
    return int(amount)


def money_text(cents: int | None) -> str | None:
    """整數分寫成固定兩位小數字串(DSP 對外唯一的金額寫法);缺值維持 None。"""
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole}.{fraction:02d}"


def _legacy_cents(value: object) -> int | None:
    """舊 REAL 資料在遷移邊界依原收據的銀行家捨入換成整數分。舊版收任何有限浮點,新版存不下的
    (非有限、整數部分超過 13 位)改記缺值,不讓整個升級回滾、DSP 起不來(代碼審 r1 外家 finder 3)。"""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        rounded = Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN,
                                                context=Context(prec=60))
        return cents_of(f"{rounded:.2f}")
    except (InvalidOperation, ValidationRejected):
        return None


def _checked_metric(name: str, value: object) -> int | None:
    """計數存整數,金額換成整數分;缺值維持 None。"""
    if value is None:
        return None
    if name in COUNT_FIELDS and _is_storable_count(value):
        return value
    if name not in COUNT_FIELDS:
        return cents_of(value)
    raise ValidationRejected(f"{name} 的值不合法:{value!r}")


def _figures(values: Mapping[str, int | float | str | None]) -> list[int | None]:
    """成效五欄照固定順序取出並驗值(跟 1 小時窗同一套);多給的欄位拒絕。"""
    unknown = set(values) - set(METRIC_FIELDS)
    if unknown:
        raise TypeError(f"不認得的指標欄位:{sorted(unknown)}")
    return [_checked_metric(name, values.get(name)) for name in METRIC_FIELDS]


def _daily_figures(values: Mapping[str, int | float | str | None]) -> list[int | None]:
    """日桶與樣板的五欄:同 _figures,另加每天的上限(DAILY_MAX_COUNT、DAILY_MAX_CENTS),保證任何
    七天合計都在分析端讀取白名單上限內;超過就 ValidationRejected。"""
    figures = _figures(values)
    for name, value in zip(METRIC_FIELDS, figures, strict=True):
        limit = DAILY_MAX_CENTS if name in AMOUNT_FIELDS else DAILY_MAX_COUNT
        if value is not None and abs(value) > limit:
            raise ValidationRejected(f"逐日 {name} 超過每天上限(七天合計要在讀取白名單上限內)")
    return figures


Bucket = tuple[int | None, int | None, int | None, int | None, int | None, bool]  # 五欄 + no_data


def _bounded_bucket(values: Sequence[int | None], no_data: object) -> Bucket:
    """舊資料(遷移的相對日數列、前一版已存的日期鍵日桶與樣板)的五欄套每天上限:超過的欄記缺值
    (同存不下的舊金額),不讓升級失敗,1d/7d 合計也仍在讀取白名單上限內(代碼審 r3 外家 finder 1)。
    五欄都缺值的那天就是沒資料(no_data 為真),不回「五欄全空卻說有資料」,讀取層才不會整週拒收
    (代碼審 r3 鏡頭A 發現 3)。遷移與讀取推算用同一支,兩邊一致。"""
    impressions, clicks, conversions, spend, revenue = (
        None if value is not None and abs(value) > (
            DAILY_MAX_CENTS if name in AMOUNT_FIELDS else DAILY_MAX_COUNT) else value
        for name, value in zip(METRIC_FIELDS, values, strict=True))
    empty = all(value is None for value in (impressions, clicks, conversions, spend, revenue))
    return impressions, clicks, conversions, spend, revenue, bool(no_data) or empty


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


DERIVED_WINDOWS = frozenset({"1d", "7d"})  # 有日桶的廣告,這兩個窗在讀取時由日桶推算
_NO_DATA: Bucket = (None, None, None, None, None, True)


def _recent_days(today: date) -> list[date]:
    """最近七個完整 UTC 日,第 1 天(昨天)在最前。"""
    return [today - timedelta(days=age) for age in range(1, DAILY_DAYS + 1)]


def _template_bucket(template: Sequence[int | None]) -> Bucket:
    """樣板推出的完整日;前一版存下的超額樣板同樣套每天上限(_bounded_bucket)。"""
    return _bounded_bucket(template, False)


def derive_days(stored: Mapping[date, Bucket], newest: date,
                template: Sequence[int | None] | None, today: date,
                days: Sequence[date]) -> dict[date, Bucket | None]:
    """純函式:每個要的日期是哪一桶。今天以後(還沒完成)沒有桶;存下的照存下的;比最新存下日期還新的
    已完成日由樣板推出,沒有樣板就是沒資料——不把前一天(含沒資料那天)照抄下去(代碼審 r1 鏡頭1)。
    其餘(被保留期清掉、或從沒種過)沒有桶。讀取端與寫入端的持久化都用這一支,結果必然一致。"""
    derived: dict[date, Bucket | None] = {}
    for day in days:
        if day >= today:
            derived[day] = None
        elif day in stored:
            derived[day] = stored[day]
        elif day > newest:
            derived[day] = _NO_DATA if template is None else _template_bucket(template)
        else:
            derived[day] = None
    return derived


def window_figures(window: str | None,
                   buckets: Sequence[Bucket | None]) -> list[int | None] | None:
    """由最近七個完整日(第 1 天在最前)算 1d/7d 窗的五欄(金額是整數分)。1d:第 1 天沒資料就五欄
    null。7d:沒資料的天按窗口契約不計;七天全沒資料就沒有這個窗(回 None,端點 404,同舊種子不種
    7d 窗的行為,代碼審 r1 鏡頭1)。有資料的天任一欄缺值,那一欄就是 null。加總用 Python 整數,
    不寫回資料庫,不會溢位成例外;超過白名單上限由讀取層記 invalid。"""
    chosen = buckets[:1] if window == "1d" else buckets
    kept = [bucket for bucket in chosen if bucket is not None and not bucket[5]]
    if not kept:
        return None if window == "7d" else [None] * len(METRIC_FIELDS)
    fields: list[int | None] = []
    for index in range(len(METRIC_FIELDS)):
        values = [bucket[index] for bucket in kept]
        fields.append(None if any(value is None for value in values)
                      else sum(value for value in values if value is not None))
    return fields


def three_day_figures(buckets: Sequence[Bucket | None]) -> dict[str, int | str | None]:
    """加額 D 的前段或後段三個完整日的五欄合計;任一天沒有桶或沒資料,五欄都是 null(第 4 條判證據
    不足);某欄缺值那欄 null。金額回固定兩位小數字串。"""
    if len(buckets) != 3:
        raise ValueError("需要恰好三個 UTC 日")
    if any(bucket is None or bucket[5] for bucket in buckets):
        return dict.fromkeys(METRIC_FIELDS)
    amounts: dict[str, int | str | None] = {}
    for index, name in enumerate(METRIC_FIELDS):
        values = [bucket[index] for bucket in buckets if bucket is not None]
        if any(value is None for value in values):
            amounts[name] = None
            continue
        total = sum(value for value in values if value is not None)
        amounts[name] = money_text(total) if name in AMOUNT_FIELDS else total
    return amounts


class CampaignStore:
    def __init__(
        self,
        path: Path,
        clock: Callable[[], str] | None = None,
        busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
    ):
        self._clock = clock if clock is not None else _default_clock
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
        daily_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(daily_metrics)")}
        metric_columns = {row[1]: row[2].upper() for row in self._conn.execute(
            "PRAGMA table_info(metrics)")}
        if ({"tenant", "name"} <= columns
                and {"policy_version", "expected_version", "budget_before"} <= op_columns
                and "day_utc" in daily_columns and metric_columns.get("spend") == "INTEGER"):
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
            self._migrate_budget_before(op_columns)
            self._migrate_money_columns()
            self._migrate_daily_dates()
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def _migrate_budget_before(self, columns: set[str]) -> None:
        """舊資料庫的操作紀錄沒有調整前預算。操作紀錄是只增不改的稽核表,不回頭改寫:補得回來的前值
        (舊種子快照、同廣告前一筆操作接龍)另記在補值表;補不回來的(例如遷移前 3 天內的第一筆真實
        加額,舊種子表從來不收)不猜,讀取時誠實回調整前預算 null,由領域判證據不足
        (代碼審 r1 鏡頭3-3)。"""
        if "budget_before" in columns:
            return
        self._conn.execute("ALTER TABLE operations ADD COLUMN budget_before INTEGER")
        legacy = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'past_adjustments'").fetchone() is not None
        for (campaign_id,) in self._conn.execute("SELECT id FROM campaigns").fetchall():
            seeded = (self._conn.execute(
                "SELECT budget_before, budget_after FROM past_adjustments "
                "WHERE campaign_id = ? ORDER BY days_ago DESC", (campaign_id,)).fetchall()
                if legacy else [])
            previous: int | None = None
            rows = self._conn.execute(
                "SELECT operation_id, action, params_json FROM operations "
                "WHERE campaign_id = ? ORDER BY operation_id", (campaign_id,)).fetchall()
            for operation_id, action, params_json in rows:
                if action != "update_budget":
                    continue
                budget = json.loads(params_json).get("new_budget")
                if seeded and seeded[0][1] == budget:
                    previous = seeded.pop(0)[0]
                if is_plain_int(previous):
                    self._conn.execute(
                        "INSERT OR IGNORE INTO operation_budget_backfill (operation_id, "
                        "budget_before) VALUES (?, ?)", (operation_id, previous))
                previous = budget if is_plain_int(budget) else None
        if legacy:
            self._conn.execute("DROP TABLE past_adjustments")
            self._conn.execute("DROP TABLE IF EXISTS past_adjustment_seeds")

    def _migrate_money_columns(self) -> None:
        columns = {row[1]: row[2].upper() for row in self._conn.execute(
            "PRAGMA table_info(metrics)")}
        if columns.get("spend") != "REAL":
            return
        self._conn.execute("ALTER TABLE metrics RENAME TO metrics_legacy")
        self._conn.execute(
            "CREATE TABLE metrics (campaign_id TEXT NOT NULL, window_name TEXT NOT NULL, "
            "impressions INTEGER, clicks INTEGER, conversions INTEGER, spend INTEGER, "
            "revenue INTEGER, PRIMARY KEY (campaign_id, window_name))")
        old = self._conn.execute("SELECT campaign_id, window_name, impressions, clicks, "
                                 "conversions, spend, revenue FROM metrics_legacy").fetchall()
        for campaign, window, imp, clicks, conv, spend, revenue in old:
            self._conn.execute("INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (campaign, window, imp, clicks, conv,
                                _legacy_cents(spend), _legacy_cents(revenue)))
        self._conn.execute("DROP TABLE metrics_legacy")

    def _migrate_daily_dates(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(daily_metrics)")}
        if "days_ago" not in columns:
            return
        self._conn.execute("ALTER TABLE daily_metrics RENAME TO daily_metrics_legacy")
        self._conn.execute(
            "CREATE TABLE daily_metrics (campaign_id TEXT NOT NULL, day_utc TEXT NOT NULL, "
            "impressions INTEGER, clicks INTEGER, conversions INTEGER, spend INTEGER, "
            "revenue INTEGER, no_data INTEGER NOT NULL, PRIMARY KEY (campaign_id, day_utc))")
        today = _moment(self._clock()).astimezone(UTC).date()
        old = self._conn.execute("SELECT campaign_id, days_ago, impressions, clicks, "
                                 "conversions, spend, revenue, no_data "
                                 "FROM daily_metrics_legacy").fetchall()
        for campaign_id, age, imp, clicks, conv, spend, revenue, no_data in old:
            bucket = _bounded_bucket(
                [imp, clicks, conv, _legacy_cents(spend), _legacy_cents(revenue)], no_data)
            self._conn.execute("INSERT INTO daily_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (campaign_id, (today - timedelta(days=age)).isoformat(),
                                *bucket[:5], int(bucket[5])))
        self._conn.execute("DROP TABLE daily_metrics_legacy")

    def close(self) -> None:
        self._conn.close()

    def seed_campaign(
        self, campaign_id: str, budget: int, status: str = "active",
        tenant: str = DEFAULT_TENANT, name: str = DEFAULT_CAMPAIGN_NAME,
    ) -> None:
        _check_campaign_name(name)
        if not is_plain_int(budget) or not 0 <= budget <= SQLITE_INTEGER_MAX:
            raise ValidationRejected("初始預算必須是非負整數")
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

    def seed_metrics(self, campaign_id: str, window: str,
                     **fields: int | float | str | None) -> None:
        unknown = set(fields) - set(METRIC_FIELDS)
        if unknown:
            raise TypeError(f"不認得的指標欄位:{sorted(unknown)}")
        _check_window(window)
        self.get_campaign(campaign_id)  # 不存在的廣告不能留下讀不到的孤兒列
        if window in DERIVED_WINDOWS and self._newest_day(campaign_id) is not None:
            # 有日桶的廣告 1d/7d 一律由日桶推算;另種一份只會被蓋住、看起來像種進去了
            raise ValidationRejected("這個廣告有逐日資料,1d/7d 由日桶推算,不另種")
        self._write_metrics(campaign_id, window, fields)

    def _write_metrics(self, campaign_id: str, window: str,
                       fields: Mapping[str, int | float | str | None]) -> None:
        _check_window(window)
        values = _figures(fields)
        self._conn.execute(
            "INSERT OR REPLACE INTO metrics (campaign_id, window_name, impressions, clicks, "
            "conversions, spend, revenue) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, window, *values),
        )

    def _today(self) -> date:
        """讀一次時鐘的 UTC 日期。每個讀取端點只讀一次,整個回應用同一個日期快照(代碼審 r1 外家
        finder 1:原本物化與投影各讀一次,午夜落在中間就自相矛盾)。"""
        return _moment(self._clock()).astimezone(UTC).date()

    def get_metrics(self, campaign_id: str, window: str | None) -> MetricsRecord:
        """1h 讀種下的列;有日桶的廣告 1d/7d 在讀取時由日桶與時鐘推算(不寫資料庫),其餘讀種下的列。"""
        _check_window(window)
        with read_snapshot(self._conn):
            self.get_campaign(campaign_id)  # 廣告不存在就回 CampaignNotFound
            if window in DERIVED_WINDOWS:
                today = self._today()
                buckets = self._buckets(campaign_id, _recent_days(today), today)
                if buckets is not None:
                    values = window_figures(window, [buckets[day] for day in _recent_days(today)])
                    if values is None:
                        raise MetricsNotFound(f"{campaign_id}/{window}")
                    impressions, clicks, conversions, spend, revenue = values
                    return MetricsRecord(campaign_id, str(window), impressions, clicks,
                                         conversions, money_text(spend), money_text(revenue))
            row = self._conn.execute(
                "SELECT campaign_id, window_name, impressions, clicks, conversions, spend, "
                "revenue FROM metrics WHERE campaign_id = ? AND window_name = ?",
                (campaign_id, window)).fetchone()
        if row is None:
            raise MetricsNotFound(f"{campaign_id}/{window}")
        return MetricsRecord(row[0], row[1], row[2], row[3], row[4],
                             money_text(row[5]), money_text(row[6]))

    def seed_daily(self, campaign_id: str,
                   days: Sequence[Mapping[str, int | float | str | None] | None],
                   now: datetime | None = None,
                   template: Mapping[str, int | float | str | None] | None = None) -> None:
        """種逐日成效(展示種子用):恰好 7 天,第 1 天在最前;None 是那天缺資料(列照樣在)。
        template 是之後新完成的每一天的成效樣板;不給就是之後的日子沒資料。"""
        self.get_campaign(campaign_id)
        at = now or _moment(self._clock())
        with self._seed_transaction():
            self._write_daily(campaign_id, days, at, template)
            self._prune_daily(campaign_id, at.astimezone(UTC).date())

    def _write_daily(self, campaign_id: str,
                     days: Sequence[Mapping[str, int | float | str | None] | None],
                     now: datetime,
                     template: Mapping[str, int | float | str | None] | None) -> None:
        if len(days) != DAILY_DAYS:
            raise ValidationRejected(f"逐日成效要恰好 {DAILY_DAYS} 天")
        if now.utcoffset() is None:
            raise ValidationRejected("逐日種子的 now 必須帶時區")
        today = now.astimezone(UTC).date()
        for days_ago, day in enumerate(days, start=1):
            values = [None] * len(METRIC_FIELDS) if day is None else _daily_figures(day)
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, (today - timedelta(days=days_ago)).isoformat(),
                 *values, int(day is None)))
        if template is None:
            self._conn.execute("DELETE FROM daily_templates WHERE campaign_id = ?",
                               (campaign_id,))
        else:
            self._conn.execute("INSERT OR REPLACE INTO daily_templates VALUES (?, ?, ?, ?, ?, ?)",
                               (campaign_id, *_daily_figures(template)))

    def _prune_daily(self, campaign_id: str, today: date) -> None:
        """保留最近 30 完整日,以及最近加額當日兩側的六個比較日。只在寫入交易裡呼叫。"""
        last = self._latest_raise(campaign_id)
        protected: list[str] = [""] * 6
        if last is not None:
            day = _moment(last[3]).astimezone(UTC).date()
            protected = [(day + timedelta(days=offset)).isoformat()
                         for offset in (-3, -2, -1, 1, 2, 3)]
        cutoff = (today - timedelta(days=ROLLING_DAYS)).isoformat()
        self._conn.execute(
            "DELETE FROM daily_metrics WHERE campaign_id = ? AND day_utc < ? "
            "AND day_utc NOT IN (?, ?, ?, ?, ?, ?)", (campaign_id, cutoff, *protected))

    def _persist_days(self, campaign_id: str, today: date) -> None:
        """寫入端的持久化(只在寫入交易裡呼叫):把讀取時會推出來的新完成日桶照樣寫下,再做 30 日保留。
        寫下的值跟讀取推導的一模一樣(同一支 derive_days),所以寫入前後任何讀取的答案都不變;
        最新日期在拿到寫入鎖之後才讀,並行的另一個寫入先補完也只會讓這裡什麼都不做
        (代碼審 r1 外家 finder 2:原本在鎖外記住最新日期,別人清掉那一天後展開 None 就丟例外)。"""
        newest = self._newest_day(campaign_id)
        if newest is None:
            return
        days = [newest + timedelta(days=n) for n in range(1, (today - newest).days)]
        derived = derive_days({}, newest, self._template(campaign_id), today, days)
        for day in days:
            bucket = derived[day]
            if bucket is not None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO daily_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (campaign_id, day.isoformat(), *bucket[:5], int(bucket[5])))
        self._prune_daily(campaign_id, today)

    def _newest_day(self, campaign_id: str) -> date | None:
        last = self._conn.execute("SELECT max(day_utc) FROM daily_metrics WHERE campaign_id = ?",
                                  (campaign_id,)).fetchone()[0]
        return None if last is None else date.fromisoformat(last)

    def _template(self, campaign_id: str) -> tuple[int | None, ...] | None:
        row = self._conn.execute(
            "SELECT impressions, clicks, conversions, spend, revenue FROM daily_templates "
            "WHERE campaign_id = ?", (campaign_id,)).fetchone()
        return None if row is None else tuple(row)

    def _buckets(self, campaign_id: str, days: Sequence[date],
                 today: date) -> dict[date, Bucket | None] | None:
        """指定日期的日桶(存下的列,或由最新存下日期、樣板與今天推出的);這個廣告沒有任何日桶回 None。
        只讀,呼叫端包在讀取快照裡。"""
        newest = self._newest_day(campaign_id)
        if newest is None:
            return None
        stored = {
            date.fromisoformat(row[0]): _bounded_bucket(row[1:6], row[6])
            for row in self._conn.execute(
                "SELECT day_utc, impressions, clicks, conversions, spend, revenue, no_data "
                "FROM daily_metrics WHERE campaign_id = ? AND day_utc >= ? AND day_utc <= ?",
                (campaign_id, min(days).isoformat(), max(days).isoformat()))}
        return derive_days(stored, newest, self._template(campaign_id), today, days)

    def get_daily(self, campaign_id: str) -> list[DailyRow]:
        """最近七個完整 UTC 日(第 1 天 = 昨天),讀取時由日桶與時鐘推算,不寫資料庫。"""
        with read_snapshot(self._conn):
            self.get_campaign(campaign_id)
            today = self._today()
            days = _recent_days(today)
            buckets = self._buckets(campaign_id, days, today)
        if buckets is None or all(buckets[day] is None for day in days):
            raise DailyNotFound(campaign_id)
        found = []
        for age, day in enumerate(days, start=1):
            bucket = buckets[day]
            found.append(DailyRow(age, None, None, None, None, None, True) if bucket is None
                         else DailyRow(age, *bucket[:3], money_text(bucket[3]),
                                       money_text(bucket[4]), bucket[5]))
        return found

    def _latest_raise(self, campaign_id: str) -> tuple[int, int | None, int, str] | None:
        """只由操作紀錄取最後一次加額;後續減額不影響它。調整前預算先看操作紀錄本身,再看遷移補值表;
        都沒有(補不回來)的那筆若比任何已知加額新,就回它、前值 None:分不出是不是加額,不能跳過
        它去回更舊的加額,也不能讓它消失(代碼審 r1 鏡頭3-3)。"""
        rows = self._conn.execute(
            "SELECT o.operation_id, COALESCE(o.budget_before, b.budget_before), o.params_json, "
            "o.committed_at FROM operations o "
            "LEFT JOIN operation_budget_backfill b ON b.operation_id = o.operation_id "
            "WHERE o.campaign_id = ? AND o.action = 'update_budget' "
            "ORDER BY o.operation_id DESC", (campaign_id,)).fetchall()
        for operation_id, before, params, at in rows:
            after = json.loads(params).get("new_budget")
            if not is_plain_int(after):
                continue
            if before is None or after > before:
                return operation_id, before, after, at
        return None

    def get_past_adjustments(self, campaign_id: str) -> list[PastAdjustment]:
        """最近一次加額與 D 前後各三個完整 UTC 日;讀取時由日桶與時鐘推算,不寫資料庫。"""
        with read_snapshot(self._conn):
            self.get_campaign(campaign_id)
            if self._newest_day(campaign_id) is None:
                raise AdjustmentsNotFound(campaign_id)
            latest = self._latest_raise(campaign_id)
            if latest is None:
                return []
            _, before_budget, after_budget, at = latest
            day = _moment(at).astimezone(UTC).date()
            today = self._today()
            prior = [day - timedelta(days=n) for n in (1, 2, 3)]
            future = [day + timedelta(days=n) for n in (1, 2, 3)]
            buckets = self._buckets(campaign_id, prior + future, today)
        assert buckets is not None  # noqa: S101 - 上面已確認有日桶
        before = three_day_figures([buckets[d] for d in prior])
        after = (three_day_figures([buckets[d] for d in future])
                 if today > day + timedelta(days=3) else dict.fromkeys(METRIC_FIELDS))
        return [PastAdjustment((today - day).days, before_budget, after_budget, before, after, at)]

    def seed_history(self, entries: Sequence[HistorySeed], changes: Sequence[PastBudgetChange],
                     now: datetime) -> None:
        """**只准展示種子呼叫**:全平台的日桶、視窗投影與預算操作同交易提交。"""
        for entry in entries:
            self.get_campaign(entry.campaign_id)
        with self._seed_transaction():
            for entry in entries:
                self._write_daily(entry.campaign_id, entry.daily, now, entry.template)
            self._write_past_operations(changes, now)
            for entry in entries:
                self._prune_daily(entry.campaign_id, now.astimezone(UTC).date())

    def seed_past_operations(self, changes: Sequence[PastBudgetChange], now: datetime) -> None:
        """**只准展示種子呼叫**:平台尚無操作時按時間種歷史操作;
        與正常寫入共用同一筆操作紀錄及前後預算欄位。"""
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
            operation_id = self._apply(op, current, _next_state(current, op), at, at)
            self._record_idempotency(op, operation_id)

    def history(self, campaign_id: str) -> list[HistoryEntry]:
        rows = self._conn.execute(
            "SELECT operation_id, action, version_after, received_at, committed_at, "
            "idempotency_key FROM operations WHERE campaign_id = ? ORDER BY operation_id",
            (campaign_id,),
        ).fetchall()
        return [HistoryEntry(*row) for row in rows]

    def history_limited(
        self, campaign_id: str,
    ) -> tuple[list[HistoryEntry], dict[str, int | bool] | None]:
        """HTTP 只給最近 50 列;超過 50 筆才另給由未截斷集合算出的摘要(沒截斷回 None,端點維持既有
        形狀)。摘要與列在同一個讀取快照裡讀,中間插進來的寫入兩邊都看不到(代碼審 r1 外家否決 2)。
        摘要的「最近 3 天/7 天」以 DSP 讀取時刻切:只是截斷時的保守旗標,正式規則仍以決策 now 判。"""
        with read_snapshot(self._conn):
            self.get_campaign(campaign_id)
            now = _moment(self._clock()).astimezone(UTC)
            seven = commit_text(now - timedelta(days=7))
            recent = commit_text(now - timedelta(days=3))
            counts = self._conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN action = 'update_budget' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN action = 'pause_campaign' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN action = 'update_budget' AND committed_at >= ? THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN action = 'update_budget' AND committed_at > ? THEN 1 ELSE 0 END) "
                "FROM operations WHERE campaign_id = ?",
                (seven, recent, campaign_id)).fetchone()
            rows = self._conn.execute(
                "SELECT operation_id, action, version_after, received_at, committed_at, "
                "idempotency_key FROM operations WHERE campaign_id = ? "
                "ORDER BY operation_id DESC LIMIT ?", (campaign_id, HISTORY_PAGE)).fetchall()
        entries = [HistoryEntry(*row) for row in reversed(rows)]
        if counts[0] <= HISTORY_PAGE:
            return entries, None
        summary: dict[str, int | bool] = {
            "total_operations": counts[0], "total_budget_changes": counts[1] or 0,
            "total_pauses": counts[2] or 0, "budget_changes_7d": counts[3] or 0,
            "budget_changes_last_3d": counts[4] or 0,
            "has_recent_budget_change": bool(counts[4]),
        }
        return entries, summary

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
        operation_id = self._apply(op, current, updated, received_at, committed_at)
        self._record_idempotency(op, operation_id)
        # 寫入端負責把新完成的日桶持久化並做保留(讀取端只推算、不寫):同一個交易,值跟讀取推導的一樣
        self._persist_days(op.campaign_id, _moment(committed_at).astimezone(UTC).date())
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
        self, op: Operation, current: Campaign, updated: Campaign,
        received_at: str, committed_at: str
    ) -> int:
        self._conn.execute(
            "UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?",
            (updated.budget, updated.status, updated.version, updated.id),
        )
        cursor = self._conn.execute(
            "INSERT INTO operations (campaign_id, action, params_json, version_after, "
            "received_at, committed_at, idempotency_key, policy_version, expected_version, "
            "budget_before) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                current.budget if op.action == "update_budget" else None,
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
