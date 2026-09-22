"""提案收件表(執行行程自己的 SQLite):安全地收、去重、記帳,不執行任何提案。

收件鍵是(任務編號, 修訂序號)。所有判斷都在同一個 BEGIN IMMEDIATE 交易內完成:
先把到期的待處理標為已過期,再依序查收件鍵、修訂連號、決策到期時間、全域上限,最後寫入並提交。
主鍵是最後一道防線,但正常路徑靠交易內的先查後寫,並行的同鍵請求會排隊。

事件紀錄只存固定欄位(時間、任務編號、修訂、封閉列舉的事件代碼、內容雜湊),
絕不存請求原文;每種事件代碼各有筆數上限;事件寫入失敗不影響對呼叫者的回應。

這支模組也是整個執行行程資料庫唯一開連線、開交易的地方:外部寫入嘗試的表結構在
`attempt_store`,但由這裡建立;嘗試紀錄的讀寫只能透過 `transaction()` 拿到已在交易中的連線,
所以之後「取件與開始一筆」可以放進同一個交易。交易物件只在這裡建立(建立時要出示憑證),交易結束就作廢。
"""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from rtb.domain.proposal import MAX_DECISION_LIFETIME, Proposal, content_hash, parse_proposal
from rtb.executor import attempt_store
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy, connect, immediate_transaction

DEFAULT_MAX_PENDING = 8
MAX_REVISIONS_PER_TASK = 50  # 一個任務最多修訂這麼多次;要更多就得換任務編號
MAX_ROWS = 5000  # 收件表總列數上限:取代鏈不佔名額,沒有這個上限就能無限寫入吃光磁碟
RETENTION = timedelta(hours=2)  # 沒有待處理的舊任務超過這個時間就整個清掉;必須比提案能活的時間長
CLOCK_SKEW = timedelta(minutes=5)  # 決策建立時間最多可以比現在晚這麼多
MAX_EVENTS_PER_CODE = 200  # 每種事件代碼各留最新這幾筆,一種事件灌爆不會洗掉別種事件的紀錄


class Disposition(StrEnum):
    """執行行程對一份提案的處置;空值代表還沒處置(仍待處理)。不動既有的狀態欄位。"""

    HANDED_OFF = "handed_off"  # 已交給執行:由那把鍵的嘗試負責
    BLOCKED = "blocked"  # 執行前檢查沒過,附擋下原因代碼


class BlockCode(StrEnum):
    """擋下原因(封閉列舉):每一種都有真實觸發路徑,見執行迴圈的執行前檢查。"""

    CAMPAIGN_NOT_FOUND = "campaign_not_found"
    CAMPAIGN_NOT_ACTIVE = "campaign_not_active"
    VERSION_CHANGED = "version_changed"
    CAMPAIGN_NOT_ALLOWED = "campaign_not_allowed"
    OVER_BUDGET_CAP = "over_budget_cap"
    OPERATION_PREVIOUSLY_FAILED = "operation_previously_failed"  # 同一把鍵先前已判定失敗


def _in_list(values: type[StrEnum]) -> str:
    return ", ".join(f"'{member.value}'" for member in values)


# 補欄位時用的定義:資料庫自己也只收列舉值,繞過模組直接寫也寫不進去
_DISPOSITION_COLUMN = f"disposition TEXT CHECK (disposition IN ({_in_list(Disposition)}))"
_BLOCK_CODE_COLUMN = f"block_code TEXT CHECK (block_code IN ({_in_list(BlockCode)}))"
# 「待處理」= 狀態待處理而且沒有處置;收件口每一條規則都用這一句,不各寫一份
PENDING = "state = 'pending' AND disposition IS NULL"
EVENT_CODES = frozenset(
    {"content_conflict", "revision_out_of_order", "inbox_full", "expired_proposal",
     "expiry_too_far", "created_in_future", "too_many_revisions"}
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    task_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'superseded', 'expired')),
    payload TEXT NOT NULL, expires_at TEXT NOT NULL, received_at TEXT NOT NULL,
    DISPOSITION_COLUMN, BLOCK_CODE_COLUMN,
    PRIMARY KEY (task_id, revision));
CREATE TABLE IF NOT EXISTS inbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, task_id TEXT, revision INTEGER,
    code TEXT NOT NULL, content_hash TEXT);
""".replace("DISPOSITION_COLUMN", _DISPOSITION_COLUMN).replace("BLOCK_CODE_COLUMN",
                                                               _BLOCK_CODE_COLUMN)
# 舊資料庫缺的欄位:表 -> [(欄位名, 補欄位定義)]
_ADDED_COLUMNS = {
    "proposals": [("disposition", _DISPOSITION_COLUMN), ("block_code", _BLOCK_CODE_COLUMN)],
    "attempts": list(attempt_store.ADDED_COLUMNS),
}


class InboxRejected(Exception):
    """收件口拒收這份提案;code 是給呼叫者的固定錯誤代碼。"""

    code = "rejected"
    retryable = False

    def __init__(self, message: str = "", highest_revision: int | None = None):
        super().__init__(message or self.code)
        self.highest_revision = highest_revision


class ContentConflict(InboxRejected):
    code = "content_conflict"


class RevisionOutOfOrder(InboxRejected):
    code = "revision_out_of_order"


class InboxFull(InboxRejected):
    code = "inbox_full"
    retryable = True


class ProposalExpired(InboxRejected):
    code = "expired_proposal"


class ExpiryTooFar(InboxRejected):
    """到期時間離現在超過一小時:否則一份提案能長期佔住名額。"""

    code = "expiry_too_far"


class CreatedInFuture(InboxRejected):
    """決策建立時間比現在晚太多:時間欄位不能拿來偽造出一份「還很新」的提案。"""

    code = "created_in_future"


class TooManyRevisions(InboxRejected):
    """同一個任務修訂太多次。"""

    code = "too_many_revisions"


class InboxBusy(InboxRejected):
    """資料庫忙碌到逾時:沒收到,可以重送。與 InboxFull 是不同的錯誤。"""

    code = "busy"
    retryable = True


@dataclass(frozen=True)
class PendingProposal:
    task_id: str
    revision: int
    content_hash: str
    proposal: Proposal


@dataclass(frozen=True)
class Accepted:
    task_id: str
    revision: int
    state: str
    content_hash: str
    replayed: bool


def _check_revision_and_times(proposal: Proposal, highest: int, now: datetime) -> None:
    """修訂必須連號且有上限,決策的建立與到期時間必須落在現在附近的合理範圍。"""
    if proposal.revision != highest + 1:
        raise RevisionOutOfOrder(highest_revision=highest)
    if proposal.revision > MAX_REVISIONS_PER_TASK:
        raise TooManyRevisions(highest_revision=highest)
    if proposal.decision_expires_at <= now:
        raise ProposalExpired()
    if proposal.decision_expires_at > now + MAX_DECISION_LIFETIME:
        raise ExpiryTooFar()
    if proposal.decision_created_at > now + CLOCK_SKEW:
        raise CreatedInFuture()


def _iso(moment: datetime) -> str:
    """固定格式的 UTC 字串:字串比大小就等於比時間先後。"""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_now() -> datetime:
    return datetime.now(UTC)


if RETENTION <= MAX_DECISION_LIFETIME:  # 設定自相矛盾就不讓模組載入
    raise RuntimeError("RETENTION 必須比提案最長有效期長")


class InboxStore:
    def __init__(
        self,
        path: Path,
        max_pending: int = DEFAULT_MAX_PENDING,
        busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
    ):
        if max_pending < 1:
            raise ValueError("max_pending 必須至少是 1,否則收件口永遠不接受任何提案")
        self._max_pending = max_pending
        try:
            self._conn = connect(path, busy_timeout_seconds, SCHEMA + attempt_store.SCHEMA)
        except DatabaseBusy as exc:
            raise InboxBusy(str(exc)) from exc
        try:
            self._migrate_columns()
        except DatabaseBusy as exc:
            self._conn.close()
            raise InboxBusy(str(exc)) from exc
        except BaseException:
            self._conn.close()  # 補欄位失敗時不留下沒人關的連線
            raise

    def _missing_columns(self) -> list[tuple[str, str]]:
        missing = []
        for table, columns in _ADDED_COLUMNS.items():
            present = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            missing += [(table, ddl) for name, ddl in columns if name not in present]
        return missing

    def _migrate_columns(self) -> None:
        """沿用 DSP 與分析行程的補欄位做法:每次連線檢查,缺才在交易內補,拿到鎖後再查一次
        (等鎖期間別的連線可能已經補好)。舊資料的新欄位一律是空值:處置空=仍待處理。"""
        if not self._missing_columns():
            return
        with immediate_transaction(self._conn):
            for table, ddl in self._missing_columns():
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[attempt_store.ExecutorTransaction]:
        """執行行程資料庫的寫入交易:給嘗試紀錄這類同一個檔裡的其他表用。

        正常結束就提交,任何例外都回滾;鎖不到丟 InboxBusy(跟收件一樣可以重試)。
        """
        issuer = attempt_store._EXECUTOR_TRANSACTION_ISSUER  # 私有憑證:只給這個交易入口用
        tx = attempt_store.ExecutorTransaction(self._conn, issuer)
        try:
            with immediate_transaction(self._conn):
                yield tx
        except DatabaseBusy as exc:
            raise InboxBusy(str(exc)) from exc
        finally:
            tx.close()  # 交易結束就作廢:同一條連線之後開別的交易,舊物件也不能再用

    def accept(
        self,
        proposal: Proposal,
        clock: Callable[[], datetime],
        before_commit: Callable[[], None] | None = None,
    ) -> Accepted:
        """收下提案,或丟出 InboxRejected 的子類別。

        clock 在拿到寫入鎖之後才讀:等鎖可能很久,先讀的話會拿過時的時間判斷到期。
        所有拒收都發生在任何寫入之前,所以拒收時照樣提交:交易裡唯一的改動是順手做的維護(把到期
        的待處理標為已過期、清掉過了保留期限的舊任務),不該因為請求被拒收而丟掉。
        before_commit 只給故障注入用。
        """
        rejection: InboxRejected | None = None
        try:
            with immediate_transaction(self._conn):
                try:
                    outcome = self._accept_in_transaction(proposal, clock())
                except InboxRejected as rejected:
                    rejection = rejected
                if rejection is None and before_commit is not None:
                    before_commit()
        except DatabaseBusy as exc:
            raise InboxBusy(str(exc)) from exc
        if rejection is not None:
            raise rejection
        return outcome

    def _accept_in_transaction(self, proposal: Proposal, now: datetime) -> Accepted:
        digest = content_hash(proposal)
        self._conn.execute(
            f"UPDATE proposals SET state = 'expired' WHERE {PENDING} AND expires_at <= ?",  # noqa: S608 - 固定條件
            (_iso(now),),
        )
        self._purge_finished_tasks(now)
        existing = self._conn.execute(
            "SELECT content_hash, coalesce(disposition, state) FROM proposals "
            "WHERE task_id = ? AND revision = ?",
            (proposal.task_id, proposal.revision),
        ).fetchone()
        if existing is not None:  # 有處置時回處置,沒有才回原狀態
            if existing[0] == digest:
                return Accepted(proposal.task_id, proposal.revision, existing[1], digest, True)
            raise ContentConflict()
        highest = self._highest_revision(proposal.task_id)
        _check_revision_and_times(proposal, highest, now)
        supersedes = self._has_pending(proposal.task_id, highest)
        self._check_capacity(supersedes)
        if supersedes:
            self._conn.execute(
                "UPDATE proposals SET state = 'superseded' WHERE task_id = ? AND revision = ?",
                (proposal.task_id, highest),
            )
        self._conn.execute(
            "INSERT INTO proposals (task_id, revision, content_hash, state, payload, expires_at, "
            "received_at) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (proposal.task_id, proposal.revision, digest, _payload(proposal),
             _iso(proposal.decision_expires_at), _iso(now)),
        )
        return Accepted(proposal.task_id, proposal.revision, "pending", digest, False)

    def _check_capacity(self, supersedes: bool) -> None:
        """待處理數與總列數都要有空間;取代自己任務的舊提案會先釋放一個名額。"""
        pending = self._conn.execute(
            f"SELECT COUNT(*) FROM proposals WHERE {PENDING}").fetchone()[0]  # noqa: S608 - 固定條件
        total = self._conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        if pending - (1 if supersedes else 0) >= self._max_pending or total >= MAX_ROWS:
            raise InboxFull()

    def _purge_finished_tasks(self, now: datetime) -> None:
        """整個清掉「沒有待處理、而且最後一次收件已超過保留期限」的任務。

        只清整個任務,不清單一修訂:修訂連號是拿最高修訂算的,清一半會讓序號倒退。保留期限比
        提案能活的時間長,所以被清掉的提案再送來一定已過期,不會被當成新提案收下。
        """
        self._conn.execute(
            "DELETE FROM proposals WHERE task_id IN (SELECT task_id FROM proposals "  # noqa: S608 - 固定條件
            f"GROUP BY task_id HAVING SUM({PENDING}) = 0 AND MAX(received_at) < ?)",
            (_iso(now - RETENTION),),
        )

    def _highest_revision(self, task_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(revision) FROM proposals WHERE task_id = ?", (task_id,)).fetchone()
        return int(row[0] or 0)

    def _has_pending(self, task_id: str, revision: int) -> bool:
        if revision == 0:
            return False
        row = self._conn.execute(
            f"SELECT 1 FROM proposals WHERE task_id = ? AND revision = ? AND {PENDING}",  # noqa: S608 - 固定條件
            (task_id, revision),
        ).fetchone()
        return row is not None

    # ---- 執行迴圈用:都在 transaction() 開的交易裡做,條件都是「仍待處理且內容雜湊沒變」 ----
    def _own(self, tx: attempt_store.ExecutorTransaction) -> None:
        if type(tx) is not attempt_store.ExecutorTransaction or not tx.is_open or (
                tx.conn is not self._conn):
            raise attempt_store.NotInTransaction("只能在這個收件口自己開的交易裡處置提案")

    def pending(self, tx: attempt_store.ExecutorTransaction) -> tuple[PendingProposal, ...]:
        """待處理的提案,依收到時間由舊到新。存的內容讀不回提案的列跳過(不讓一列壞資料卡住全部)。"""
        self._own(tx)
        found = []
        for task_id, revision, digest, payload in self._conn.execute(
                f"SELECT task_id, revision, content_hash, payload FROM proposals WHERE {PENDING} "  # noqa: S608 - 固定條件
                "ORDER BY received_at, task_id, revision"):
            try:
                parsed = parse_proposal(json.loads(payload))
            except ValueError:
                continue
            if parsed.proposal is not None:
                found.append(PendingProposal(task_id, revision, digest, parsed.proposal))
        return tuple(found)

    def _dispose(
        self, tx: attempt_store.ExecutorTransaction, task_id: str, revision: int, digest: str,
        assignment: str, values: tuple[str, ...],
    ) -> bool:
        self._own(tx)
        cursor = self._conn.execute(
            f"UPDATE proposals SET {assignment} WHERE task_id = ? AND revision = ? "  # noqa: S608 - 只拼接模組內固定的欄位
            f"AND content_hash = ? AND {PENDING}",
            (*values, task_id, revision, digest),
        )
        return cursor.rowcount == 1

    def hand_off(
        self, tx: attempt_store.ExecutorTransaction, task_id: str, revision: int, digest: str,
    ) -> bool:
        return self._dispose(tx, task_id, revision, digest, "disposition = ?",
                             (Disposition.HANDED_OFF.value,))

    def block(
        self, tx: attempt_store.ExecutorTransaction, task_id: str, revision: int, digest: str,
        code: BlockCode,
    ) -> bool:
        if type(code) is not BlockCode:
            raise ValueError("擋下原因代碼必須是封閉列舉 BlockCode 的成員")
        return self._dispose(tx, task_id, revision, digest, "disposition = ?, block_code = ?",
                             (Disposition.BLOCKED.value, code.value))

    def mark_expired(
        self, tx: attempt_store.ExecutorTransaction, task_id: str, revision: int, digest: str,
    ) -> bool:
        """提案過期沿用既有的「已過期」狀態,不是擋下原因。"""
        return self._dispose(tx, task_id, revision, digest, "state = 'expired'", ())

    def is_pending(
        self, tx: attempt_store.ExecutorTransaction, task_id: str, revision: int, digest: str,
    ) -> bool:
        self._own(tx)
        return self._conn.execute(
            "SELECT 1 FROM proposals WHERE task_id = ? AND revision = ? AND content_hash = ? "  # noqa: S608 - 固定條件
            f"AND {PENDING}", (task_id, revision, digest)).fetchone() is not None

    def record_event(self, code: str, proposal: Proposal, now: datetime) -> None:
        """盡力而為:寫不進去(忙碌、磁碟滿)就放棄,不影響對呼叫者的回應。

        只記「格式合法的提案被拒收」:格式不合法的請求不記,不然任何人都能用垃圾請求沖掉紀錄。
        跟最近一筆完全相同的事件不重複記,同一個拒收重複很多次只留一筆。
        """
        if code not in EVENT_CODES:
            raise ValueError("事件代碼必須是封閉列舉的成員")
        try:
            self._write_event(_iso(now), proposal.task_id, proposal.revision, code,
                              content_hash(proposal))
        except (sqlite3.Error, DatabaseBusy):
            return

    def _write_event(
        self, at: str, task_id: str, revision: int, code: str, digest: str
    ) -> None:
        with immediate_transaction(self._conn):
            latest = self._conn.execute(
                "SELECT code, task_id, revision, content_hash FROM inbox_events "
                "ORDER BY id DESC LIMIT 1").fetchone()
            if latest == (code, task_id, revision, digest):
                return
            self._conn.execute(
                "INSERT INTO inbox_events (at, task_id, revision, code, content_hash) "
                "VALUES (?, ?, ?, ?, ?)", (at, task_id, revision, code, digest))
            self._conn.execute(  # 每種代碼只留最新的幾筆,在同一個交易內刪除更舊的
                "DELETE FROM inbox_events WHERE code = ? AND id NOT IN "
                "(SELECT id FROM inbox_events WHERE code = ? ORDER BY id DESC LIMIT ?)",
                (code, code, MAX_EVENTS_PER_CODE))


def _payload(proposal: Proposal) -> str:
    return json.dumps(proposal.to_primitives(), sort_keys=True, ensure_ascii=True)
