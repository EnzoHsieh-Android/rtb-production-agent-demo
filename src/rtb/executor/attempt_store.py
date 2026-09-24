"""外部寫入嘗試的只增不改歷史表(執行行程資料庫裡的一張表)。

主鍵是(鍵, 序號);一把鍵的目前狀態永遠是序號最大的那一列;沒有 UPDATE、沒有 DELETE。
每一列都帶廣告編號,查「這個廣告有沒有未結案的鍵」只看每把鍵的最新列。第 1 列另外帶
任務、修訂、動作、預期版本與完整提案快照:收件口會清掉過期提案,重新授權要有自己的依據。

這支模組不開連線、不開交易:每個函式只接受執行行程資料庫模組(收件口資料庫模組)的交易入口
發出的交易物件,而且那一筆交易還開著。交易物件建立時要出示入口專用的憑證(執行期檢查,
不只靠掃原始碼),型別必須完全相同(子類別不收),交易結束時入口會把它作廢,所以同一條連線
之後開了別的交易,舊物件也不會復活。自己開的連線、自己開的延遲交易(不排隊搶寫入鎖)都不收。
威脅模型是「防忘記、不防刻意繞過」:憑證擋的是善意重構時不小心繞過入口。這樣「取件與開始一筆」能放在同一個交易裡,並行檢查也一定在寫入鎖之內。

未結案計數不逐把鍵回頭讀最新列:每把鍵恰好有一列序號 1(主鍵保證),結案的鍵恰好有一列終點列
(終點沒有出路,而且資料庫的唯一限制不准同一把鍵寫第二列終點列),所以「未結案數 = 第 1 列數 -
終點列數」,兩個數都走部分索引。只看相減結果的正負擋不住毀損:會被另一把還沒結案的鍵抵銷。

Phase 9 增量 1:
- 每一列記來源、執行者與程式版本(執行迴圈記工作者、啟動恢復記行程身分、人工處置記操作人);寫入
  函式由呼叫端明傳 `by`,舊列與沒傳的列為空值。
- 唯讀交易:收件口模組的唯讀開法發出的另一個類別(另一個私有發行憑證)。讀取函式收寫入交易或唯讀
  交易,寫入函式只收寫入交易;哪些是寫入函式由測試從原始碼機械算出,跟 WRITE_FUNCTIONS 比對。
- DSP 呼叫紀錄表(只增不改):執行迴圈每一次呼叫 DSP 都在獨立的短交易裡寫一列(呼叫類別、結果類別、
  狀態碼、DSP 回的錯誤代碼、耗時與關聯欄位),不跟結果寫入綁在一起。
"""

import json
import sqlite3
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from rtb import PROGRAM_VERSION
from rtb.domain._checks import is_plain_int, require_aware
from rtb.domain.attempt import (
    RESOLUTION_OUTCOMES,
    TERMINAL_STATES,
    TIMEOUT_STATES,
    AttemptState,
    IllegalAttemptTransition,
    OutcomeCode,
    can_transition,
    code_fits,
    is_clean_detail,
    operation_key,
)
from rtb.domain.proposal import Proposal, content_hash, parse_proposal
from rtb.sqlitekit import is_integer_overflow

MAX_SENDS = 3  # 同一把鍵最多送出幾次(含第一次)
MAX_VERIFICATION_TIMEOUTS = 5  # 對帳與執行後驗證的查詢逾時,每把鍵累計
MAX_ROWS_PER_KEY = 50  # 正常路徑最多 14 列;進入轉人工、人工處置與重啟恢復不受這個上限
MAX_UNRESOLVED = 20  # 全表同時未結案的鍵數:滿了就停下所有新寫入,等人處理

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    key TEXT NOT NULL, seq INTEGER NOT NULL, campaign_id TEXT NOT NULL, state TEXT NOT NULL,
    code TEXT, detail TEXT, send_count INTEGER NOT NULL, verification_timeouts INTEGER NOT NULL,
    written_at TEXT NOT NULL,
    task_id TEXT, revision INTEGER, action TEXT, expected_version INTEGER, proposal_json TEXT,
    written_version INTEGER, capability_expires_at TEXT,
    PRIMARY KEY (key, seq));
CREATE INDEX IF NOT EXISTS attempts_first_rows ON attempts (campaign_id) WHERE seq = 1;
CREATE INDEX IF NOT EXISTS attempts_terminal_rows ON attempts (campaign_id)
    WHERE state IN (TERMINAL_LIST);
CREATE UNIQUE INDEX IF NOT EXISTS attempts_one_terminal_per_key ON attempts (key)
    WHERE state IN (TERMINAL_LIST);
CREATE INDEX IF NOT EXISTS attempts_verified_by_time ON attempts (written_at)
    WHERE state = 'verified';
CREATE INDEX IF NOT EXISTS attempts_terminal_by_time ON attempts (written_at)
    WHERE state IN (TERMINAL_LIST);
CREATE INDEX IF NOT EXISTS attempts_unknown_by_time ON attempts (written_at)
    WHERE state = 'unknown';
CREATE INDEX IF NOT EXISTS attempts_first_rows_by_task ON attempts (task_id, revision)
    WHERE seq = 1;
CREATE TABLE IF NOT EXISTS dsp_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, call_kind TEXT NOT NULL,
    result TEXT NOT NULL, status INTEGER, error_code TEXT, latency_ms REAL NOT NULL,
    task_id TEXT, revision INTEGER, campaign_id TEXT, key TEXT, source TEXT, actor TEXT,
    content_hash TEXT,
    program_version TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS dsp_calls_by_time ON dsp_calls (at);
CREATE INDEX IF NOT EXISTS dsp_calls_by_task ON dsp_calls (task_id, id);
"""
_COLUMNS = ("key, seq, campaign_id, state, code, detail, send_count, verification_timeouts, "
            "written_at, written_version, capability_expires_at")
# 執行一筆(Phase 3 增量 3)新增的欄位;舊資料庫由執行行程資料庫模組照補欄位做法補上
ADDED_COLUMNS = (
    ("written_version", "written_version INTEGER"),  # DSP 回報的寫入後版本
    ("capability_expires_at", "capability_expires_at TEXT"),  # 最近一次送出所帶憑證的到期時間
    # 總曝險預留(Phase 6):只寫在第一列,開始一筆時算出之後不變;舊列不回填,空值由額度查詢保守計入
    ("tenant", "tenant TEXT"),
    ("reserved_amount", "reserved_amount INTEGER"),
    # 誰寫的(Phase 9 增量 1):來源、執行者、程式版本;舊列為空值
    ("source", "source TEXT"),
    ("actor", "actor TEXT"),
    ("program_version", "program_version TEXT"),
    # 開始時的核對材料(Phase 9 增量 3,只寫在第一列、只增不改;副作用核對用,別處不讀):
    # 比例允許加的量、單一廣告上限、總額上限、判門檻用的已用額度。舊列為空
    ("ratio_allowance", "ratio_allowance INTEGER"),
    ("max_budget", "max_budget INTEGER"),
    ("aggregate_limit", "aggregate_limit INTEGER"),
    ("aggregate_used", "aggregate_used INTEGER"),
)
# DSP 呼叫紀錄表的欄位(唯讀開法用它判斷資料庫升級了沒有)
DSP_CALL_FIELDS = ("id", "at", "call_kind", "result", "status", "error_code", "latency_ms",
                   "task_id", "revision", "campaign_id", "key", "source", "actor",
                   "program_version", "content_hash")
# 呼叫紀錄表在同一個增量裡後補的欄位(代碼審第 2 輪):這個分支早先開過的資料庫開啟時補上
DSP_CALL_ADDED_COLUMNS = (("content_hash", "content_hash TEXT"),)
# 可觀測查詢(Phase 6 增量 4)用的第一列按租戶與開始時間索引。參照後補的租戶欄,不能放進 SCHEMA:
# 開庫是先跑整份建表建索引、後補欄位,舊資料庫一開就會找不到欄位;由補欄位流程補完欄位之後才建
TENANT_INDEX = ("CREATE INDEX IF NOT EXISTS attempts_first_rows_by_tenant "
                "ON attempts (tenant, written_at) WHERE seq = 1")
# 已驗證的預留在驗證完成後多久內仍佔額度(暫用值,沒有真實花費數據校準,見 Phase 6 計劃)
AGGREGATE_WINDOW = timedelta(hours=24)
# 終點狀態是固定的列舉值,寫成字面值,查詢條件才對得上部分索引的條件
_TERMINAL_LIST = ", ".join(f"'{state.value}'" for state in sorted(TERMINAL_STATES))
SCHEMA = SCHEMA.replace("TERMINAL_LIST", _TERMINAL_LIST)


class AttemptRejected(Exception):
    """這次操作被拒絕,而且沒有寫入任何東西。"""


class NotInTransaction(AttemptRejected):
    """傳進來的不是執行行程資料庫交易入口發出、而且還開著的交易:這是程式錯誤。"""


class CampaignLocked(AttemptRejected):
    """同一個廣告已有另一把鍵的未結案嘗試。"""


class AggregateLimitReached(AttemptRejected):
    """這筆加預算加上租戶已用額度會超過總曝險門檻(Phase 6):不開嘗試。"""

    def __init__(self, used: int, limit: int):
        super().__init__(f"已用 {used},門檻 {limit}")
        self.used, self.limit = used, limit


@dataclass(frozen=True)
class Reservation:
    """開始一筆時要記的預留:租戶、這筆加預算的金額(減預算與暫停是 0)、簽發時讀到的門檻。

    approved:呼叫端手上有這一筆「總曝險已滿」的有效人工核可(Phase 6 增量 3;金額不超過核可
    上限由呼叫端驗過)。有核可時照樣算已用額度、照樣寫預留,只是超過門檻也放行。"""

    tenant: str
    amount: int
    limit: int
    approved: bool = False
    # 開始時的核對材料(Phase 9 增量 3):比例允許加的量(暫停為空)與簽發時讀到的單一廣告上限
    ratio_allowance: int | None = None
    max_budget: int | None = None


class TooManyUnresolved(AttemptRejected):
    """全表未結案的鍵數已達上限。"""


class InvalidOutcome(AttemptRejected):
    """結果代碼與目標狀態不配、代碼不認得、細節文字或處置理由不乾淨。"""


class SendLimitReached(AttemptRejected):
    """同一把鍵的送出次數已達上限,只能轉人工。"""


class VerificationTimeoutLimitReached(AttemptRejected):
    """查證逾時次數已達上限,只能轉人工。"""


class HistoryFull(AttemptRejected):
    """這把鍵的歷史列已達上限;只剩轉人工與人工處置寫得進去。"""


class IncompleteRow(AttemptRejected):
    """轉進嘗試中沒帶憑證到期時間,或轉進已提交待驗證沒帶寫入後版本。"""


class CorruptedAttemptRow(Exception):
    """歷史列讀不回來(格式毀損,或快照已對不上它的鍵)。"""


_EXECUTOR_TRANSACTION_ISSUER = object()  # 只有收件口資料庫模組的交易入口拿它建交易物件(有測試擋)


class ExecutorTransaction:
    """執行行程資料庫交易入口發出的一筆交易;交易結束時由入口作廢。"""

    __slots__ = ("_open", "conn")

    def __init__(self, conn: sqlite3.Connection, issuer: object) -> None:
        if issuer is not _EXECUTOR_TRANSACTION_ISSUER:
            raise NotInTransaction("交易物件只能由執行行程資料庫交易入口發出")
        self.conn = conn
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open and self.conn.in_transaction


_READ_TRANSACTION_ISSUER = object()  # 只有收件口模組的唯讀開法拿它建唯讀交易(另一個私有憑證)


class ReadTransaction:
    """唯讀開法發出的唯讀交易:只能給讀取函式用;寫入函式照舊只收 ExecutorTransaction。"""

    __slots__ = ("_open", "conn")

    def __init__(self, conn: sqlite3.Connection, issuer: object) -> None:
        if issuer is not _READ_TRANSACTION_ISSUER:
            raise NotInTransaction("唯讀交易只能由收件口模組的唯讀開法發出")
        self.conn = conn
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open and self.conn.in_transaction


class Source(StrEnum):
    """一列紀錄是誰寫的(Phase 9 增量 1,生命週期事件與嘗試紀錄共用):封閉列舉。"""

    INBOX = "inbox"  # 收件口:收件、收件時的取代與到期標記,執行者為空
    EXECUTOR_LOOP = "executor_loop"  # 執行迴圈:執行者是它的擁有者(行程編號加啟動時間)
    STARTUP_RECOVERY = "startup_recovery"  # 啟動恢復:執行者是啟動中的行程身分
    ADMIN_COMMAND = "admin_command"  # 管理指令(重放、人工處置):執行者是命令列傳入的操作人


@dataclass(frozen=True)
class Actor:
    """來源加執行者;來源只收封閉列舉的成員(寫錯成字串在建立時就擋下)。"""

    source: Source
    name: str | None = None

    def __post_init__(self) -> None:
        if type(self.source) is not Source:
            raise ValueError("來源必須是封閉列舉 Source 的成員")
        if self.name is not None and not isinstance(self.name, str):
            raise ValueError("執行者必須是字串或空值")


class DspCallKind(StrEnum):
    """執行端呼叫 DSP 的類別。"""

    READ_CAMPAIGN = "read_campaign"
    WRITE = "write"
    VOID = "void"
    LOOKUP_OPERATION = "lookup_operation"


class DspCallResult(StrEnum):
    """一次 DSP 呼叫的結果類別:傳輸層的例外依型別分逾時、連線失敗、回應讀不懂;有回應時依狀態碼
    分 4xx、5xx;狀態 2xx 但欄位讀不懂算回應讀不懂;其他狀態碼(例如沒跟的轉址)也算讀不懂。"""

    RESPONDED = "responded"
    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection_failed"
    SERVER_ERROR = "server_error"  # 5xx
    CLIENT_ERROR = "client_error"  # 4xx
    UNREADABLE = "unreadable"


class DspErrorCode(StrEnum):
    """DSP 回應本文的錯誤代碼(Phase 9 增量 1 [S625]):封閉列舉,不認得的記「其他」,不原樣存外來
    字串。清單照模擬 DSP 與共用 HTTP 伺服器會回的代碼;執行端不匯入 DSP 的程式,所以另列一份。"""

    VERSION_CONFLICT = "version_conflict"
    OPERATION_VOIDED = "operation_voided"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    VALIDATION_REJECTED = "validation_rejected"
    CAPABILITY_EXPIRED = "capability_expired"
    CAPABILITY_INVALID = "capability_invalid"
    CAPABILITY_MISSING = "capability_missing"
    CAPABILITY_SCOPE_MISMATCH = "capability_scope_mismatch"
    CAPABILITY_NOT_CONFIGURED = "capability_not_configured"
    CAMPAIGN_NOT_FOUND = "campaign_not_found"
    OPERATION_NOT_FOUND = "operation_not_found"
    MISSING_IDEMPOTENCY_KEY = "missing_idempotency_key"
    STORE_BUSY = "store_busy"
    INTERNAL_ERROR = "internal_error"
    OTHER = "other"


def dsp_error_code(reply: object) -> DspErrorCode | None:
    """回應本文沒有 error 欄(或不是物件)回空值;有但不在列舉裡(含不是字串)回「其他」。"""
    if not isinstance(reply, dict) or "error" not in reply:
        return None
    error = reply["error"]
    known = {code.value for code in DspErrorCode} - {DspErrorCode.OTHER.value}
    return DspErrorCode(error) if isinstance(error, str) and error in known else DspErrorCode.OTHER


@dataclass(frozen=True)
class DspCall:
    """DSP 用戶端對每一次 HTTP 呼叫回報的一筆:類別、結果、狀態碼(沒拿到回應為空)、耗時、
    錯誤代碼。"""

    kind: DspCallKind
    result: DspCallResult
    status: int | None
    latency_ms: float
    error: DspErrorCode | None = None


@dataclass(frozen=True)
class CallSubject:
    """呼叫紀錄的關聯欄位:這次呼叫是為了哪一份提案。內容雜湊在呼叫當下記下:冪等鍵不含決策時間與
    說明文字、內容雜湊含,兩份提案可以同鍵不同雜湊,事後從鍵回推會撞(代碼審第 2 輪兩席)。"""

    task_id: str | None
    revision: int | None
    campaign_id: str | None
    key: str | None
    content_hash: str | None


@dataclass(frozen=True)
class DspCallRow:
    id: int
    at: str
    kind: str
    result: str
    status: int | None
    error: str | None
    latency_ms: float
    task_id: str | None
    revision: int | None
    campaign_id: str | None
    key: str | None
    source: str | None
    actor: str | None
    program_version: str
    content_hash: str | None


@dataclass(frozen=True)
class AttemptTraceRow:
    """追蹤檢視用的一列嘗試紀錄:第一列的任務、修訂與租戶跟著每一列帶出來(同一把鍵只列一次)。"""

    key: str
    seq: int
    state: str
    code: str | None
    send_count: int
    written_at: str
    task_id: str | None
    revision: int | None
    campaign_id: str
    tenant: str | None
    source: str | None
    actor: str | None
    program_version: str | None
    content_hash: str | None = None  # 建立這把鍵那份提案的內容雜湊(第一列的快照算出;讀不回為空)


@dataclass(frozen=True)
class Recovery:
    moved: tuple[str, ...]  # 轉成結果不明的鍵
    unreadable: tuple[str, ...]  # 歷史列讀不回來而跳過的鍵:要人處理,但不拖垮其他鍵


@dataclass(frozen=True)
class AttemptRow:
    key: str
    seq: int
    campaign_id: str
    state: AttemptState
    code: OutcomeCode | None
    detail: str | None
    send_count: int
    verification_timeouts: int
    written_at: datetime
    # 以下兩欄轉進時必帶、之後的列往下帶;增量 3 以前寫的列是空值
    written_version: int | None = None
    capability_expires_at: datetime | None = None


@dataclass(frozen=True)
class Begun:
    row: AttemptRow
    created: bool  # False:鍵已存在,原樣回傳目前那一列——呼叫端依狀態分支,絕不因此直接再送
    # 靠人工核可超過門檻放行時,當時的已用額度與門檻(寫核可使用紀錄用);沒超過是空值
    over_limit: AggregateLimitReached | None = None


def _iso(moment: datetime) -> str:
    require_aware(moment)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso(moment: datetime) -> str:
    """寫進嘗試紀錄與停下紀錄的時間格式;沒帶時區就拒絕(不然會被當成本機時間,範圍篩錯)。
    收件口模組的停下紀錄讀取方法也用這支,兩張表的比較規則一致(Phase 6 增量 4 代碼審第 2 輪)。"""
    return _iso(moment)


def _conn(tx: ExecutorTransaction) -> sqlite3.Connection:
    """寫入函式的守衛:只收寫入交易(唯讀交易傳進來丟 NotInTransaction,不會跑到資料庫層才炸)。"""
    if type(tx) is not ExecutorTransaction or not tx.is_open:
        raise NotInTransaction("嘗試紀錄只能在執行行程資料庫交易入口開的交易裡讀寫")
    return tx.conn


Readable = ExecutorTransaction | ReadTransaction


def _read_conn(tx: Readable) -> sqlite3.Connection:
    """讀取函式的守衛:寫入交易或唯讀交易,型別完全相同、而且還開著。"""
    if type(tx) not in (ExecutorTransaction, ReadTransaction) or not tx.is_open:
        raise NotInTransaction("嘗試紀錄只能在執行行程資料庫交易入口或唯讀開法開的交易裡讀")
    return tx.conn


def _by(by: Actor | None) -> tuple[str | None, str | None]:
    if by is None:
        return None, None
    if type(by) is not Actor:
        raise ValueError("寫入者必須是 Actor")
    return by.source.value, by.name


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _row(record: tuple[Any, ...]) -> AttemptRow:
    key, seq, campaign_id, state, code, detail, sends, timeouts, written_at, written, expires = (
        record)
    try:
        if written is not None and not is_plain_int(written):
            raise TypeError("寫入後版本不是整數")
        return AttemptRow(
            key=key, seq=seq, campaign_id=campaign_id, state=AttemptState(state),
            code=None if code is None else OutcomeCode(code), detail=detail,
            send_count=sends, verification_timeouts=timeouts,
            written_at=_parse_time(written_at), written_version=written,
            capability_expires_at=None if expires is None else _parse_time(expires),
        )
    # SQLite 不強制欄位型別:壞值可能是任何型別,讀不回來一律當成毀損
    except (ValueError, TypeError, AttributeError) as exc:
        raise CorruptedAttemptRow(f"{key} 第 {seq} 列讀不回來:{exc!r}") from exc


def latest(tx: Readable, key: str) -> AttemptRow | None:
    record = _read_conn(tx).execute(
        f"SELECT {_COLUMNS} FROM attempts WHERE key = ? ORDER BY seq DESC LIMIT 1",  # noqa: S608 - 只拼接模組內固定的欄位清單
        (key,),
    ).fetchone()
    return None if record is None else _row(record)


def history(tx: Readable, key: str) -> tuple[AttemptRow, ...]:
    records = _read_conn(tx).execute(
        f"SELECT {_COLUMNS} FROM attempts WHERE key = ? ORDER BY seq",  # noqa: S608 - 只拼接模組內固定的欄位清單
        (key,)).fetchall()
    return tuple(_row(record) for record in records)


def _parsed_snapshot(key: str, text: str | None) -> Proposal:
    """第 1 列存的提案快照經同一個解析器還原;沒有、不是 JSON、解析不回來都丟毀損例外。"""
    if text is None:
        raise CorruptedAttemptRow(f"{key} 沒有提案快照")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptedAttemptRow(f"{key} 的提案快照不是 JSON") from exc
    parsed = parse_proposal(raw)
    if parsed.proposal is None:
        raise CorruptedAttemptRow(f"{key} 的提案快照解析不回來:{parsed.errors}")
    return parsed.proposal


def _snapshot_matches_key(key: str, proposal: Proposal) -> bool:
    """快照的鍵一致性:這份提案仍算得出這把鍵(snapshot 與批量讀第一列共用這一條)。"""
    return operation_key(proposal) == key


def snapshot(tx: Readable, key: str) -> Proposal:
    """讀回第 1 列存的完整提案,經同一個解析器還原成領域層的提案物件,並核對它仍算得出這把鍵。"""
    record = _read_conn(tx).execute(
        "SELECT proposal_json FROM attempts WHERE key = ? AND seq = 1", (key,)).fetchone()
    proposal = _parsed_snapshot(key, None if record is None else record[0])
    if not _snapshot_matches_key(key, proposal):
        raise CorruptedAttemptRow(f"{key} 的提案快照已對不上它的鍵")
    return proposal


def unresolved_count_query(campaign_id: str | None = None) -> tuple[str, tuple[str, ...]]:
    """未結案數 = 第 1 列數 - 終點列數(可限定一個廣告);兩邊都走部分索引。"""
    where = "" if campaign_id is None else " AND campaign_id = ?"
    params = () if campaign_id is None else (campaign_id, campaign_id)
    query = (
        f"SELECT (SELECT COUNT(*) FROM attempts WHERE seq = 1{where}) - "  # noqa: S608 - 只拼接固定條件
        f"(SELECT COUNT(*) FROM attempts WHERE state IN ({_TERMINAL_LIST}){where})"
    )
    return query, params


def unresolved_count(tx: Readable, campaign_id: str | None = None) -> int:
    query, params = unresolved_count_query(campaign_id)
    count = int(_read_conn(tx).execute(query, params).fetchone()[0])
    if count < 0:  # 唯一限制之外的最後一道:算法前提不成立就當毀損,不放行
        raise CorruptedAttemptRow("未結案計數為負,歷史表有鍵出現多列終點列")
    return count


def version_conflict_count(tx: Readable, campaign_id: str | None = None) -> int:
    """DSP 回版本衝突的嘗試次數(唯讀、可依廣告篩):嘗試紀錄只增不改、不會被清,是執行側衝突
    的稽核來源。執行前檢查擋下的「版本已變」只記在收件表、會被清,長期計數等 Phase 9。"""
    where = "" if campaign_id is None else " AND campaign_id = ?"
    params: tuple[str, ...] = (OutcomeCode.VERSION_CONFLICT.value,) + (
        () if campaign_id is None else (campaign_id,))
    return int(_read_conn(tx).execute(
        f"SELECT COUNT(*) FROM attempts WHERE code = ?{where}",  # noqa: S608 - 只拼接固定條件
        params).fetchone()[0])


def campaigns_with_unresolved(tx: Readable) -> frozenset[str]:
    """已有未結案嘗試的廣告(取件要排除它們);未結案 = 第 1 列在、終點列不在。"""
    records = _read_conn(tx).execute(
        "SELECT DISTINCT f.campaign_id FROM attempts f WHERE f.seq = 1 AND NOT EXISTS "  # noqa: S608 - 只拼接固定條件
        f"(SELECT 1 FROM attempts t WHERE t.key = f.key AND t.state IN ({_TERMINAL_LIST}))"
    ).fetchall()
    return frozenset(record[0] for record in records)


def unresolved_keys(tx: Readable) -> tuple[str, ...]:
    """所有未結案的鍵(含嘗試中與轉人工),依最新一列寫入時間由舊到新:對帳每輪都要接手它們。"""
    records = _read_conn(tx).execute(
        "SELECT a.key FROM attempts a WHERE a.state NOT IN "  # noqa: S608 - 只拼接固定條件
        f"({_TERMINAL_LIST}) AND a.seq = "
        "(SELECT MAX(b.seq) FROM attempts b WHERE b.key = a.key) ORDER BY a.written_at, a.key"
    ).fetchall()
    return tuple(record[0] for record in records)


def _expiry(value: object) -> str:
    """轉進嘗試中必帶:這次送出所帶憑證的到期時間。對帳不再拿它當證明(改用 DSP 端作廢,
    見 Phase 3 計劃增量 4),保留給人工處置時參考。"""
    if not isinstance(value, datetime):
        raise IncompleteRow("轉進嘗試中要帶這次送出所帶憑證的到期時間")
    return _iso(value)


def begin(
    tx: ExecutorTransaction, proposal: Proposal, now: datetime, *,
    capability_expires_at: datetime | None,
    reservation: Reservation | None = None,
    by: Actor | None = None,
) -> Begun:
    """開始一筆:鍵與第 1 列的欄位全由這份提案算出,呼叫端不能另外指定。

    帶預留時,金額大於 0 就在同一個交易裡(立即取得寫入鎖)先算租戶已用額度,加上這筆超過門檻
    丟 AggregateLimitReached、什麼都不寫;鍵已存在時不再預留也不再檢查(那把鍵第一次就扣過)。"""
    conn = _conn(tx)
    require_aware(now)
    source, actor = _by(by)
    key = operation_key(proposal)
    current = latest(tx, key)
    if current is not None:
        return Begun(current, created=False)
    expires = _expiry(capability_expires_at)
    if unresolved_count(tx, proposal.campaign_id):
        raise CampaignLocked(proposal.campaign_id)
    if unresolved_count(tx) >= MAX_UNRESOLVED:
        raise TooManyUnresolved()
    over_limit = None
    used: int | None = None  # 判門檻用的已用額度;加的量是 0 時不判門檻,不記
    if reservation is not None and reservation.amount > 0:
        used = aggregate_used(tx, reservation.tenant, now)
        if used + reservation.amount > reservation.limit:
            over_limit = AggregateLimitReached(used, reservation.limit)
            if not reservation.approved:
                raise over_limit
    snapshot_json = json.dumps(proposal.to_primitives(), sort_keys=True, ensure_ascii=True,
                               allow_nan=False)
    conn.execute(
        "INSERT INTO attempts (key, seq, campaign_id, state, send_count, verification_timeouts, "
        "written_at, task_id, revision, action, expected_version, proposal_json, "
        "capability_expires_at, tenant, reserved_amount, source, actor, program_version, "
        "ratio_allowance, max_budget, aggregate_limit, aggregate_used) "
        "VALUES (?, 1, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, proposal.campaign_id, AttemptState.IN_FLIGHT.value, _iso(now),
         proposal.task_id, proposal.revision, proposal.action_type.value,
         proposal.campaign_version_observed, snapshot_json, expires,
         None if reservation is None else reservation.tenant,
         None if reservation is None else reservation.amount, source, actor, PROGRAM_VERSION,
         None if reservation is None else reservation.ratio_allowance,
         None if reservation is None else reservation.max_budget,
         None if reservation is None else reservation.limit, used),
    )
    row = latest(tx, key)
    assert row is not None  # 剛寫入的那一列  # noqa: S101
    return Begun(row, created=True, over_limit=over_limit)


def tenant_index_missing(conn: sqlite3.Connection) -> bool:
    """補欄位流程的判斷之一:欄位已補齊的舊資料庫(增量 1 開過的)也要能補到這個索引。"""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' "
                        "AND name = 'attempts_first_rows_by_tenant'").fetchone() is None


def _verified_candidates(tenant: str, now: datetime) -> tuple[str, tuple[Any, ...]]:
    """已用額度「已驗證」那一段選哪些列(原算法的逐筆明細與快路徑共用這一份):驗證完成時間在 24 小時
    窗內的已驗證列(剛好滿 24 小時還不算「超過」),接回的第一列屬於這個租戶或沒記租戶。從已驗證列的
    時間部分索引出發,依主鍵接回第一列。"""
    return ("FROM attempts v JOIN attempts f ON f.key = v.key AND f.seq = 1 "
            "WHERE v.state = ? AND v.written_at >= ? AND (f.tenant = ? OR f.tenant IS NULL)",
            (AttemptState.VERIFIED.value, _iso(now - AGGREGATE_WINDOW), tenant))


def _open_candidates(tenant: str) -> tuple[str, tuple[Any, ...]]:
    """已用額度「未結案」那一段選哪些列(兩種算法共用):這個租戶或沒記租戶、還沒有終點列的第一列。"""
    return ("FROM attempts f WHERE f.seq = 1 AND (f.tenant = ? OR f.tenant IS NULL) AND NOT EXISTS "  # noqa: S608 - 只拼接固定條件
            f"(SELECT 1 FROM attempts t WHERE t.key = f.key AND t.state IN ({_TERMINAL_LIST}))",
            (tenant,))


# 快路徑每一段一支查詢、一次掃描回三個數:這個租戶、金額是整數而且大於 0 的列的加總(資料庫的 SUM
# 溢位時丟「整數溢位」;不准用 TOTAL,它溢位時回失真的小數而不丟例外),候選裡沒記租戶的舊列數,
# 候選裡屬於這個租戶、原算法可能處理不了的列數。偵測只看型別與租戶欄,不把候選列取回程式。
# 原算法對這個租戶的列會讀的欄位只有三個(代碼審第 1 輪 x1 逐一核過):金額(整數轉型)、第一列時間與鍵
# (計入的列依這兩欄排序,型別混在一起會丟 TypeError);其他身分欄只是原樣帶出,不比較也不轉型。
# 所以金額型別不是整數或空值、第一列時間或鍵不是文字,都交回原算法
_FAST_SELECT = (
    "SELECT SUM(CASE WHEN f.tenant = ? AND typeof(f.reserved_amount) = 'integer' "
    "AND f.reserved_amount > 0 THEN f.reserved_amount END), "
    "COUNT(CASE WHEN f.tenant IS NULL THEN 1 END), "
    "COUNT(CASE WHEN f.tenant = ? AND (typeof(f.reserved_amount) NOT IN ('integer', 'null') "
    "OR typeof(f.written_at) <> 'text' OR typeof(f.key) <> 'text') THEN 1 END) ")


# 便宜偵測第一步只瞄最新幾筆沒記租戶的第一列:窗內剛驗證完的舊鍵、剛卡住的舊鍵通常在這裡
LEGACY_RECENT_PEEK = 8


def legacy_recent_query(now: datetime) -> tuple[str, tuple[Any, ...]]:
    """便宜偵測第一步(只回、不執行):最新的至多 LEGACY_RECENT_PEEK 筆沒記租戶的第一列裡,有沒有候選
    (窗內已驗證、或還沒有終點)。工作量固定幾筆;[S341] 那種窗內有大量舊格式已驗證列的資料,在這一步
    就查到,不必走第二步從最舊的讀起。"""
    return ("SELECT 1 FROM (SELECT f.key AS legacy_key FROM attempts f WHERE f.seq = 1 "  # noqa: S608 - 只拼接固定條件
            "AND f.tenant IS NULL ORDER BY f.written_at DESC LIMIT ?) WHERE EXISTS (SELECT 1 "
            "FROM attempts v WHERE v.key = legacy_key AND v.state = ? AND v.written_at >= ?) "
            "OR NOT EXISTS (SELECT 1 FROM attempts t WHERE t.key = legacy_key "
            f"AND t.state IN ({_TERMINAL_LIST})) "
            "LIMIT 1",
            (LEGACY_RECENT_PEEK, AttemptState.VERIFIED.value, _iso(now - AGGREGATE_WINDOW)))


def legacy_open_query() -> tuple[str, tuple[Any, ...]]:
    """便宜偵測第二步(只回、不執行):有沒有還沒有終點、沒記租戶的舊鍵(Phase 6 之前開的鍵卡在
    轉人工之類,會長期留在未結案那段)。從沒記租戶的第一列出發(按租戶的第一列索引,由舊到新:卡住的
    通常是最舊的),每一列用「一把鍵最多一列終點」的唯一索引查有沒有終點,碰到第一把沒終點的就停。

    不設筆數上限,所以不會漏(代碼審第 2 輪資安席、鏡頭 1:第 1 版只看最新 64 筆,比它們更早的卡住
    舊鍵永遠查不到,退回之前白掃一遍)。工作量的上限是舊鍵總數:新寫的第一列一定記租戶,舊鍵不會再
    增加,已結案的也不會再變回未結案。最壞情形是卡住的舊鍵夾在大量已結案舊鍵中間,要讀過前面每一把
    (殘餘風險,見驗收紀錄)。窗內已驗證、第一列又不在最新幾筆裡的舊鍵不在這兩步查,照舊由加總那一趟
    的精確偵測發現再退回。"""
    return ("SELECT 1 FROM attempts f WHERE f.seq = 1 AND f.tenant IS NULL AND NOT EXISTS "  # noqa: S608 - 只拼接固定條件
            f"(SELECT 1 FROM attempts t WHERE t.key = f.key AND t.state IN ({_TERMINAL_LIST})) "
            "ORDER BY f.written_at LIMIT 1", ())


def _legacy_candidate_seen(conn: sqlite3.Connection, now: datetime) -> bool:
    """兩步便宜偵測:先瞄最新幾筆,沒有再從最舊的找卡住的舊鍵。"""
    for sql, params in (legacy_recent_query(now), legacy_open_query()):
        if conn.execute(sql, params).fetchone() is not None:
            return True
    return False


def aggregate_used_queries(
    tenant: str, now: datetime,
) -> tuple[tuple[str, tuple[Any, ...]], tuple[str, tuple[Any, ...]]]:
    """快路徑兩段的查詢語句與參數(只回、不執行;給測試看查詢計畫):(已驗證那一段, 未結案那一段)。"""
    verified, verified_params = _verified_candidates(tenant, now)
    unresolved, unresolved_params = _open_candidates(tenant)
    return ((_FAST_SELECT + verified, (tenant, tenant, *verified_params)),
            (_FAST_SELECT + unresolved, (tenant, tenant, *unresolved_params)))


def _fast_part(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int | None:
    """一段的快路徑加總;候選裡有舊列或型別異常的金額就回 None(整次改走原算法)。"""
    total, legacy, odd = conn.execute(sql, params).fetchone()
    if legacy or odd:
        return None
    return 0 if total is None else int(total)  # 空集合的加總是空值,補成 0


def aggregate_used(tx: Readable, tenant: str, now: datetime) -> int:
    """租戶已用額度。開始一筆與可觀測查詢都從這個入口拿(既有並行測試靠攔截它造競態,Phase 6 增量 4
    設計審第 2 輪;名稱與位置不能動)。

    F7 效能計劃:常見情形在資料庫端加總(快路徑),只接「屬於這個租戶、金額是整數」那一種;候選裡有
    Phase 6 之前沒記租戶的舊列、屬於這個租戶的金額型別不是整數或空值、或資料庫回報整數溢位,就退回
    原算法(`aggregate_used_reference`),結果以它為準。其他資料庫錯誤照舊往外丟(查詢寫錯不能被當成
    溢位悄悄吞掉)。兩段在程式裡相加:程式的整數沒有上限,兩段各自沒溢位、相加超過資料庫上限也照算。
    這是翻案:Phase 6 增量 1 裁定「在程式裡用整數累加」,理由是資料庫加總會溢位停機;這裡加了退路。"""
    if type(tenant) is not str:  # 資料庫比租戶會把整數參數轉型(1 = '1'),程式不會:兩種算法才會一樣
        raise TypeError("租戶名稱要是字串")
    conn = _read_conn(tx)
    # 先跑便宜偵測、不先算全表未結案數:退回時原算法自己會再算一次,先算就白算(未結案數要數全表的
    # 第一列與終點列,比偵測貴得多)
    if _legacy_candidate_seen(conn, now):
        return aggregate_used_reference(tx, tenant, now)  # 有卡住的舊鍵:不先加總,直接走原算法
    open_keys = unresolved_count(tx)  # 原算法同一個條件:全表有未結案才查未結案那段
    (verified_sql, verified_params), (open_sql, open_params) = aggregate_used_queries(tenant, now)
    try:
        total = _fast_part(conn, verified_sql, verified_params)
        if total is not None and open_keys:
            part = _fast_part(conn, open_sql, open_params)
            total = None if part is None else total + part
    except sqlite3.OperationalError as exc:
        if not is_integer_overflow(exc):
            raise
        total = None
    if total is None:
        return aggregate_used_reference(tx, tenant, now)
    return total


def aggregate_used_reference(tx: Readable, tenant: str, now: datetime) -> int:
    """原算法:目前計入的每一筆(`aggregate_holdings`)在程式裡用整數累加。快路徑判不準時退回這裡,
    測試也拿它當對照。"""
    return sum(holding.amount for holding in aggregate_holdings(tx, tenant, now))


def aggregate_holdings(
    tx: Readable, tenant: str, now: datetime,
) -> tuple[CountedFirstRow, ...]:
    """租戶已用額度的逐筆明細(Phase 6):從嘗試紀錄推,不另存狀態。

    - 已驗證:驗證完成時間在窗口內的才算(從已驗證列的部分索引出發,只跟窗口內筆數成正比)。
    - 沒有終點:不論多久都算(全表最多 MAX_UNRESOLVED 把;先用計數判斷是不是 0)。
    - 失敗:不算,等於還回去。
    舊列(Phase 6 之前)沒有租戶與金額:改預算以新預算全額計、算進每一個租戶(分不出加減,寧可多擋)。
    選哪些列跟快路徑共用同一份篩選片段;算多少只在逐列計入函式。"""
    conn = _read_conn(tx)
    # 租戶在資料庫裡就過濾(這個租戶的列與沒有租戶的舊列),不把全系統的列撈進程式:查詢在全域
    # 寫入鎖裡,多撈的列都是握鎖時間(代碼審第 2 輪資安席實測 30 萬列時一次 0.3 秒)
    verified, verified_params = _verified_candidates(tenant, now)
    rows = conn.execute(f"SELECT {_first_row_columns('f')} {verified}",
                        verified_params).fetchall()
    if unresolved_count(tx):
        unresolved, unresolved_params = _open_candidates(tenant)
        rows += conn.execute(f"SELECT {_first_row_columns('f')} {unresolved}",
                             unresolved_params).fetchall()
    # 同一次查詢順便帶出身分欄位:可觀測查詢的「目前佔額度的」直接用這份,不再依鍵回查(鍵的數量
    # 沒有上限,逐一當查詢參數會超過 SQLite 的參數上限;代碼審第 2 輪兩席 Codex)
    return _counted_rows(rows, tenant)


@dataclass(frozen=True)
class CountedFirstRow:
    """照逐列計入規則算進這個租戶的一把鍵的第一列(Phase 6 增量 4 可觀測查詢用)。"""

    key: str
    task_id: str | None
    revision: int | None
    campaign_id: str
    started_at: str
    state: str  # 這把鍵目前(最新一列)的狀態
    amount: int
    legacy: bool  # Phase 6 之前沒有租戶的舊列:照既有規則算進每一個租戶


_FIRST_ROW_FIELDS = ("key", "task_id", "revision", "campaign_id", "written_at", "tenant",
                     "reserved_amount", "action", "proposal_json")


def _first_row_columns(alias: str) -> str:
    """第一列的欄位,加上這把鍵目前的狀態:同一次查詢帶出,沿主鍵找最新一列,不逐鍵回查
    (大量紀錄時逐鍵查詢會在寫入鎖裡跑太久;代碼審第 3 輪外家否決席)。"""
    fields = [f"{alias}.{name}" for name in _FIRST_ROW_FIELDS]
    fields.insert(5, f"(SELECT s.state FROM attempts s WHERE s.key = {alias}.key "  # noqa: S608 - 別名是模組內固定字串
                     "ORDER BY s.seq DESC LIMIT 1)")
    return ", ".join(fields)


def first_rows_started_query(
    tenant: str, since: datetime, until: datetime,
) -> tuple[str, tuple[Any, ...]]:
    """範圍內開始、這個租戶或沒有租戶的舊列的第一列。拆成兩段合起來,兩段都用得上按租戶的索引
    (寫成「租戶相符或租戶是空值」會讓索引用不上)。"""
    start, end = _iso(since), _iso(until)
    part = (f"SELECT {_first_row_columns('f')} FROM attempts f WHERE f.seq = 1 AND {{}} "  # noqa: S608 - 只拼接固定條件
            "AND f.written_at >= ? AND f.written_at < ?")
    return (f"{part.format('f.tenant = ?')} UNION ALL {part.format('f.tenant IS NULL')}",
            (tenant, start, end, start, end))


def counted_first_rows_started(
    tx: Readable, tenant: str, since: datetime, until: datetime,
) -> tuple[CountedFirstRow, ...]:
    """範圍內開始、照逐列計入規則金額大於 0 的鍵(不看 24 小時窗口),依開始時間排序。"""
    sql, params = first_rows_started_query(tenant, since, until)
    return _counted_rows(_read_conn(tx).execute(sql, params), tenant)


def _counted_rows(rows: Iterable[tuple[Any, ...]], tenant: str) -> tuple[CountedFirstRow, ...]:
    counted = []
    for key, task_id, revision, campaign_id, started_at, state, *rest in rows:
        amount = _counted(rest, tenant)  # 跟已用額度同一條逐列計入規則,不另寫一份
        if amount > 0:  # 減預算、暫停、別的租戶
            counted.append(CountedFirstRow(key, task_id, revision, campaign_id, started_at,
                                           state, amount, rest[0] is None))
    return tuple(sorted(counted, key=lambda row: (row.started_at, row.key)))


def _counted(row: Sequence[Any], tenant: str) -> int:
    owner, amount, action, snapshot = row
    if owner is not None:
        return int(amount) if owner == tenant and amount is not None else 0
    if action != "update_budget":  # 舊列:暫停不佔額度
        return 0
    try:
        budget = json.loads(snapshot)["requested_change"]["new_budget"]
    except (TypeError, ValueError, KeyError) as exc:
        raise CorruptedAttemptRow("舊嘗試的提案快照讀不出新預算,無法保守計入額度") from exc
    if not is_plain_int(budget):
        raise CorruptedAttemptRow("舊嘗試的提案快照新預算不是整數")
    return budget


def _append(  # noqa: PLR0913 - 每個欄位都是新列的一部分
    tx: ExecutorTransaction, previous: AttemptRow, state: AttemptState, now: datetime, *,
    code: OutcomeCode | None = None, detail: str | None = None,
    send_count: int | None = None, verification_timeouts: int | None = None,
    capped: bool = True, written_version: int | None = None, expires: str | None = None,
    by: Actor | None = None,
) -> AttemptRow:
    if capped and previous.seq >= MAX_ROWS_PER_KEY:
        raise HistoryFull(previous.key)
    carried_expiry = (None if previous.capability_expires_at is None
                      else _iso(previous.capability_expires_at))
    source, actor = _by(by)
    _conn(tx).execute(
        f"INSERT INTO attempts ({_COLUMNS}, source, actor, program_version) "  # noqa: S608 - 只拼接模組內固定的欄位清單
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (previous.key, previous.seq + 1, previous.campaign_id, state.value,
         None if code is None else OutcomeCode(code).value, detail,
         previous.send_count if send_count is None else send_count,
         previous.verification_timeouts if verification_timeouts is None
         else verification_timeouts,
         _iso(now),
         previous.written_version if written_version is None else written_version,
         carried_expiry if expires is None else expires, source, actor, PROGRAM_VERSION),
    )
    row = latest(tx, previous.key)
    assert row is not None  # noqa: S101
    return row


def _current(
    tx: ExecutorTransaction, key: str, expected_seq: int, now: datetime,
) -> AttemptRow | None:
    """呼叫端帶的預期序號仍是最新一列才回傳;不是就回 None(沒有進展,不寫入)。"""
    _conn(tx)
    require_aware(now)
    if not is_plain_int(expected_seq):  # True == 1 在 Python 成立,不能靠 == 比對
        raise TypeError("預期序號必須是整數")
    row = latest(tx, key)
    return row if row is not None and row.seq == expected_seq else None


def _send_fields(
    destination: AttemptState, written_version: object, capability_expires_at: object,
) -> tuple[int | None, str | None]:
    """轉進嘗試中必帶憑證到期時間、轉進已提交待驗證必帶寫入後版本;其他轉換不准夾帶。"""
    expires = None
    if destination is AttemptState.IN_FLIGHT:
        expires = _expiry(capability_expires_at)
    elif capability_expires_at is not None:
        raise InvalidOutcome("只有轉進嘗試中才帶憑證到期時間")
    if destination is AttemptState.COMMITTED_UNVERIFIED:
        if not is_plain_int(written_version) or written_version < 1:
            raise IncompleteRow("轉進已提交待驗證要帶 DSP 回報的寫入後版本")
        return written_version, expires
    if written_version is not None:
        raise InvalidOutcome("只有轉進已提交待驗證才帶寫入後版本")
    return None, expires


def transition(  # noqa: PLR0913 - 代碼、細節與送出欄位是轉換本身要記的內容
    tx: ExecutorTransaction, key: str, expected_seq: int, target: AttemptState, now: datetime,
    *, code: OutcomeCode | None = None, detail: str | None = None,
    written_version: int | None = None, capability_expires_at: datetime | None = None,
    by: Actor | None = None,
) -> AttemptRow | None:
    """一般轉換:照唯讀轉換表;回傳新列,預期序號已不是最新就回 None。"""
    row = _current(tx, key, expected_seq, now)
    if row is None:
        return None
    if not can_transition(row.state, target):
        raise IllegalAttemptTransition(f"不合法的轉換:{row.state} -> {target}")
    if not code_fits(target, code, by_resolution=False):
        raise InvalidOutcome(f"結果代碼 {code!r} 不配目標狀態 {target}")
    if detail is not None and not is_clean_detail(detail):
        raise InvalidOutcome("細節文字只收有長度上限的 ASCII 可列印字元")
    destination = AttemptState(target)
    written, expires = _send_fields(destination, written_version, capability_expires_at)
    sends = row.send_count
    if row.state is AttemptState.UNKNOWN and destination is AttemptState.IN_FLIGHT:
        if sends >= MAX_SENDS:
            raise SendLimitReached(key)
        sends += 1
    return _append(tx, row, destination, now, code=code, detail=detail, send_count=sends,
                   capped=destination is not AttemptState.ESCALATED,
                   written_version=written, expires=expires, by=by)


def record_verification_timeout(
    tx: ExecutorTransaction, key: str, expected_seq: int, now: datetime, *,
    by: Actor | None = None,
) -> AttemptRow | None:
    """記一次查證逾時:狀態不變、次數加 1,兩件事是同一次寫入。"""
    row = _current(tx, key, expected_seq, now)
    if row is None:
        return None
    if row.state not in TIMEOUT_STATES:
        raise IllegalAttemptTransition(f"{row.state} 不能記查證逾時")
    if row.verification_timeouts >= MAX_VERIFICATION_TIMEOUTS:
        raise VerificationTimeoutLimitReached(key)
    return _append(tx, row, row.state, now,
                   verification_timeouts=row.verification_timeouts + 1, by=by)


def resolve(  # noqa: PLR0913 - 人工處置要記的每一樣,含操作人
    tx: ExecutorTransaction, key: str, expected_seq: int, outcome: AttemptState, reason: str,
    now: datetime, *, by: Actor | None = None,
) -> AttemptRow | None:
    """人工處置:只對轉人工有效,結果只能是已驗證或失敗,理由必填;不受歷史列上限。by 記操作人。"""
    row = _current(tx, key, expected_seq, now)
    if row is None:
        return None
    if row.state is not AttemptState.ESCALATED or outcome not in RESOLUTION_OUTCOMES:
        raise IllegalAttemptTransition(f"人工處置不能把 {row.state} 處置成 {outcome}")
    if not is_clean_detail(reason) or not reason.strip():
        raise InvalidOutcome("處置理由必填,只收有長度上限的 ASCII 可列印字元")
    destination = AttemptState(outcome)
    code = OutcomeCode.MANUAL_FAILURE if destination is AttemptState.FAILED else None
    return _append(tx, row, destination, now, code=code, detail=reason, capped=False, by=by)


def recover_in_flight(
    tx: ExecutorTransaction, now: datetime, *, held: Collection[str], written_before: datetime,
    by: Actor | None = None,
) -> Recovery:
    """重啟恢復:目前是嘗試中、不在 held 裡、而且最新一列寫在 written_before 之前的鍵轉成結果
    不明,其他鍵完全不動。

    held 是收件表裡有處理中訊息的鍵(由呼叫端在同一個交易裡查好傳進來,這個模組不反過來依賴
    收件表):那些是別的工作者正在做的、或租約到期後由對帳原子接手的,重啟恢復不碰。
    不在 held 裡的是舊鍵(沒有收件表訊息),但它不一定沒人在做:對帳舊鍵時也會先寫嘗試中再呼叫
    DSP。沒有租約可看,改看這一列寫下多久:呼叫端傳「現在減一個租約時間」,活著的工作者一次
    DSP 呼叫一定在那之前寫完結果。兩個參數都必填、沒有預設值:漏傳時不能悄悄退回舊語意。

    不受歷史列上限:一把鍵撞上限若讓整批回滾,同一次重啟裡健康的鍵也會留在嘗試中。每次進入
    嘗試中最多被恢復一次(恢復後就不是嘗試中了),所以豁免不會讓表無限長。
    歷史列讀不回來的鍵跳過並回報:不然一把壞鍵會讓每次重啟都在同一點整批失敗。
    """
    conn = _conn(tx)
    require_aware(now)
    keys = [record[0] for record in conn.execute(
        "SELECT a.key FROM attempts a WHERE a.state = ? AND a.seq = "
        "(SELECT MAX(b.seq) FROM attempts b WHERE b.key = a.key) ORDER BY a.key",
        (AttemptState.IN_FLIGHT.value,),
    ).fetchall()]
    moved, unreadable = [], []
    require_aware(written_before)
    for key in keys:
        if key in held:
            continue
        try:
            row = latest(tx, key)
        except CorruptedAttemptRow:
            unreadable.append(key)
            continue
        assert row is not None  # noqa: S101
        if row.written_at > written_before:  # 剛寫下的:可能有工作者正在對帳這把舊鍵
            continue
        _append(tx, row, AttemptState.UNKNOWN, now, capped=False, by=by)
        moved.append(key)
    return Recovery(tuple(moved), tuple(unreadable))


# ---- DSP 呼叫紀錄與追蹤檢視用的讀取(Phase 9 增量 1) ----
def record_dsp_call(
    tx: ExecutorTransaction, call: DspCall, subject: CallSubject, by: Actor, now: datetime,
) -> None:
    """寫一列 DSP 呼叫紀錄(只增不改)。呼叫端在呼叫之後用一個獨立的短交易寫,不跟結果寫入綁在
    一起:結果寫入可能因收據失效回滾,呼叫本身確實發生了,不能跟著消失。"""
    conn = _conn(tx)
    if type(call.kind) is not DspCallKind or type(call.result) is not DspCallResult or (
            call.error is not None and type(call.error) is not DspErrorCode):
        raise ValueError("呼叫類別、結果類別與錯誤代碼都必須是封閉列舉的成員")
    source, actor = _by(by)
    conn.execute(
        "INSERT INTO dsp_calls (at, call_kind, result, status, error_code, latency_ms, task_id, "
        "revision, campaign_id, key, source, actor, program_version, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_iso(now), call.kind.value, call.result.value, call.status,
         None if call.error is None else call.error.value, float(call.latency_ms),
         subject.task_id, subject.revision, subject.campaign_id, subject.key, source, actor,
         PROGRAM_VERSION, subject.content_hash))


def dsp_calls_between_query(since: datetime, until: datetime) -> tuple[str, tuple[str, ...]]:
    """時間窗內 DSP 呼叫紀錄的查詢語句(測試用它看查詢計畫);含起點、不含終點。"""
    return (f"SELECT {', '.join(DSP_CALL_FIELDS)} FROM dsp_calls "  # noqa: S608 - 固定欄位清單
            "WHERE at >= ? AND at < ? ORDER BY at, id", (_iso(since), _iso(until)))


def dsp_calls_between(tx: Readable, since: datetime, until: datetime) -> tuple[DspCallRow, ...]:
    """時間窗內的 DSP 呼叫紀錄,依呼叫時間;走時間索引(Phase 9 增量 2 的指標用)。"""
    sql, params = dsp_calls_between_query(since, until)
    return tuple(DspCallRow(*row) for row in _read_conn(tx).execute(sql, params))


@dataclass(frozen=True)
class TerminalRow:
    """一把鍵的終點列(已驗證或失敗):指標依它的寫入時間歸窗,再回頭讀那把鍵的整串。"""

    key: str
    state: str
    written_at: str
    program_version: str | None


def terminal_rows_between_query(
    since: datetime, until: datetime,
) -> tuple[str, tuple[str, ...]]:
    """時間窗內終點列的查詢語句(測試用它看查詢計畫);狀態條件照抄部分索引的清單。"""
    return ("SELECT key, state, written_at, program_version FROM attempts "  # noqa: S608 - 只拼接固定的狀態清單
            f"WHERE state IN ({_TERMINAL_LIST}) AND written_at >= ? AND written_at < ? "
            "ORDER BY written_at, key", (_iso(since), _iso(until)))


def terminal_rows_between(
    tx: Readable, since: datetime, until: datetime,
) -> tuple[TerminalRow, ...]:
    """時間窗內到了終點的鍵(每把鍵最多一列終點列,有唯一索引守);走終點時間的部分索引。"""
    sql, params = terminal_rows_between_query(since, until)
    return tuple(TerminalRow(*row) for row in _read_conn(tx).execute(sql, params))


def unknown_rows_between_query(
    since: datetime, until: datetime,
) -> tuple[str, tuple[str, ...]]:
    """時間窗內進結果不明的列的查詢語句(測試用它看查詢計畫)。"""
    return ("SELECT DISTINCT key FROM attempts WHERE state = 'unknown' "
            "AND written_at >= ? AND written_at < ? ORDER BY key", (_iso(since), _iso(until)))


def unknown_rows_between(tx: Readable, since: datetime, until: datetime) -> tuple[str, ...]:
    """時間窗內寫過結果不明列的鍵(Phase 9 增量 3 對帳服務水準的候選);走結果不明的部分索引。
    呼叫端再用鍵讀整串,判第一次進結果不明是不是落在它要的範圍。"""
    sql, params = unknown_rows_between_query(since, until)
    return tuple(row[0] for row in _read_conn(tx).execute(sql, params))


@dataclass(frozen=True)
class FirstRow:
    """一把鍵的第一列:開始一筆時記下的提案快照、租戶、預留金額與四樣核對材料(副作用核對用)。
    快照讀不回來時 proposal 為空;舊列沒有的欄位為空。snapshot_matches_key 沿用 snapshot 的鍵一致性
    檢查:快照讀不回來、或讀得回來卻算不出這把鍵,都是假(代碼審第 1 輪:批量讀原本沒核對,毀損
    的快照會被當成可信的提案內容)。"""

    key: str
    task_id: str | None
    revision: int | None
    campaign_id: str
    tenant: str | None
    reserved_amount: int | None
    ratio_allowance: int | None
    max_budget: int | None
    aggregate_limit: int | None
    used_before: int | None  # 表上的欄位名是 aggregate_used(判門檻用的已用額度)
    proposal: Proposal | None
    snapshot_matches_key: bool
    written_at: str


_FIRST_ROW_READ = ("key, task_id, revision, campaign_id, tenant, reserved_amount, "
                   "ratio_allowance, max_budget, aggregate_limit, aggregate_used, proposal_json, "
                   "written_at")
BATCH = 500  # 一批最多幾把鍵:遠低於 SQLite 參數上限


def _first_row(row: Sequence[Any]) -> FirstRow:
    try:
        parsed: Proposal | None = _parsed_snapshot(row[0], row[10])
    except CorruptedAttemptRow:
        parsed = None
    return FirstRow(key=row[0], task_id=row[1], revision=row[2], campaign_id=row[3],
                    tenant=row[4], reserved_amount=row[5], ratio_allowance=row[6],
                    max_budget=row[7], aggregate_limit=row[8], used_before=row[9],
                    proposal=parsed,
                    snapshot_matches_key=parsed is not None and _snapshot_matches_key(
                        row[0], parsed),
                    written_at=row[11])


def first_rows_for(tx: Readable, keys: Iterable[str]) -> dict[str, FirstRow]:
    """一批冪等鍵各自的第一列(Phase 9 增量 3 副作用核對用);每批不超過 BATCH 把,不逐筆查。
    找不到第一列的鍵不在結果裡。"""
    conn, wanted, found = _read_conn(tx), sorted(set(keys)), {}
    for start in range(0, len(wanted), BATCH):
        chunk = wanted[start:start + BATCH]
        marks = ", ".join("?" * len(chunk))
        for row in conn.execute(
                f"SELECT {_FIRST_ROW_READ} FROM attempts WHERE seq = 1 AND key IN ({marks})",  # noqa: S608 - 固定欄位與佔位符
                chunk):
            found[row[0]] = _first_row(row)
    return found


def first_rows_for_proposal_query(task_id: str, revision: int) -> tuple[str, tuple[object, ...]]:
    """同一份提案(任務與修訂)的所有第一列的查詢語句(測試用它看查詢計畫)。"""
    return (f"SELECT {_FIRST_ROW_READ} FROM attempts "  # noqa: S608 - 固定欄位
            "WHERE seq = 1 AND task_id = ? AND revision = ? ORDER BY written_at, key",
            (task_id, revision))


def first_rows_for_proposal(tx: Readable, task_id: str, revision: int) -> tuple[FirstRow, ...]:
    """同一個任務與修訂的每一把鍵的第一列(重複有害副作用往回查同一份提案);走第一列依任務與修訂
    的部分索引。內容雜湊由呼叫端從快照比。"""
    sql, params = first_rows_for_proposal_query(task_id, revision)
    return tuple(_first_row(row) for row in _read_conn(tx).execute(sql, params))


def dsp_calls_for(tx: Readable, task_id: str) -> tuple[DspCallRow, ...]:
    """一個任務的 DSP 呼叫紀錄,依寫入順序。"""
    return tuple(DspCallRow(*row) for row in _read_conn(tx).execute(
        f"SELECT {', '.join(DSP_CALL_FIELDS)} FROM dsp_calls WHERE task_id = ? ORDER BY id",  # noqa: S608 - 固定欄位清單
        (task_id,)))


def trace_rows(tx: Readable, key: str) -> tuple[AttemptTraceRow, ...]:
    """一把鍵的每一列嘗試,連同第一列記的任務、修訂、租戶與提案內容雜湊(那把鍵歸建立它的那份提案;
    任務編號重用時靠內容雜湊分開,代碼審第 1 輪)。"""
    rows = _read_conn(tx).execute(
        "SELECT a.key, a.seq, a.state, a.code, a.send_count, a.written_at, f.task_id, "
        "f.revision, a.campaign_id, f.tenant, a.source, a.actor, a.program_version, "
        "f.proposal_json FROM attempts a JOIN attempts f ON f.key = a.key AND f.seq = 1 "
        "WHERE a.key = ? ORDER BY a.seq", (key,)).fetchall()
    return tuple(AttemptTraceRow(
        key=row[0], seq=row[1], state=row[2], code=row[3], send_count=row[4], written_at=row[5],
        task_id=row[6], revision=row[7], campaign_id=row[8], tenant=row[9], source=row[10],
        actor=row[11], program_version=row[12], content_hash=_snapshot_hash(row[13]))
        for row in rows)


CURSOR_PAGE = 1000  # 游標式讀取一次最多回幾列;呼叫端讀到不滿一頁才算讀完


def trace_rows_after_query(after: int) -> tuple[str, tuple[int, ...]]:
    """列號大於 after 的嘗試列查詢語句(測試用它看查詢計畫)。"""
    return ("SELECT a.rowid, a.key, a.seq, a.state, a.code, a.send_count, a.written_at, f.task_id, "
            "f.revision, a.campaign_id, f.tenant, a.source, a.actor, a.program_version, "
            "f.proposal_json FROM attempts a LEFT JOIN attempts f ON f.key = a.key AND f.seq = 1 "
            "WHERE a.rowid > ? ORDER BY a.rowid LIMIT ?", (after, CURSOR_PAGE))


def trace_rows_after(tx: Readable, after: int) -> tuple[tuple[int, AttemptTraceRow], ...]:
    """列號大於 after 的嘗試列,依寫入順序,最多一頁:(列號, 追蹤檢視同一形狀的一列,含誰寫的)
    (Phase 12 代碼審 r2 a1:展示觀察器照列號往後讀,不自己下查詢)。"""
    sql, params = trace_rows_after_query(after)
    return tuple((int(row[0]), AttemptTraceRow(
        key=row[1], seq=row[2], state=row[3], code=row[4], send_count=row[5], written_at=row[6],
        task_id=row[7], revision=row[8], campaign_id=row[9], tenant=row[10], source=row[11],
        actor=row[12], program_version=row[13], content_hash=_snapshot_hash(row[14])))
        for row in _read_conn(tx).execute(sql, params))


def _snapshot_hash(snapshot: str | None) -> str | None:
    try:
        parsed = parse_proposal(json.loads(snapshot or "null")).proposal
    except (ValueError, TypeError):
        return None
    return None if parsed is None else content_hash(parsed)


def first_row_tenant(tx: Readable, key: str) -> str | None:
    """這把鍵開始一筆時記的租戶(簽發當時的);沒有這把鍵或舊列回空值。"""
    row = _read_conn(tx).execute(
        "SELECT tenant FROM attempts WHERE key = ? AND seq = 1", (key,)).fetchone()
    return None if row is None else row[0]


# 寫入函式的宣告清單:測試從原始碼機械算出寫入函式,必須跟這份完全相同(不靠名字判斷)。用函式物件
# 取名字、不寫字串:這支模組的字串裡不准出現交易指令的字樣(既有測試守「不自己開交易」)
WRITE_FUNCTIONS = frozenset(fn.__name__ for fn in (
    begin, transition, record_verification_timeout, resolve, recover_in_flight, record_dsp_call))
