"""提案收件表(執行行程自己的 SQLite):安全地收、去重、記帳,不執行任何提案。

收件鍵是(任務編號, 修訂序號)。所有判斷都在同一個 BEGIN IMMEDIATE 交易內完成:
先把到期的待處理標為已過期,再依序查收件鍵、修訂連號、決策到期時間、全域上限,最後寫入並提交。
主鍵是最後一道防線,但正常路徑靠交易內的先查後寫,並行的同鍵請求會排隊。

事件紀錄只存固定欄位(時間、任務編號、修訂、封閉列舉的事件代碼、內容雜湊),
絕不存請求原文;每種事件代碼各有筆數上限;事件寫入失敗不影響對呼叫者的回應。
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rtb.domain.proposal import MAX_DECISION_LIFETIME, Proposal, content_hash
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy, connect, immediate_transaction

DEFAULT_MAX_PENDING = 8
MAX_REVISIONS_PER_TASK = 50  # 一個任務最多修訂這麼多次;要更多就得換任務編號
MAX_ROWS = 5000  # 收件表總列數上限:取代鏈不佔名額,沒有這個上限就能無限寫入吃光磁碟
RETENTION = timedelta(hours=2)  # 沒有待處理的舊任務超過這個時間就整個清掉;必須比提案能活的時間長
CLOCK_SKEW = timedelta(minutes=5)  # 決策建立時間最多可以比現在晚這麼多
MAX_EVENTS_PER_CODE = 200  # 每種事件代碼各留最新這幾筆,一種事件灌爆不會洗掉別種事件的紀錄
EVENT_CODES = frozenset(
    {"content_conflict", "revision_out_of_order", "inbox_full", "expired_proposal",
     "expiry_too_far", "created_in_future", "too_many_revisions"}
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    task_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'superseded', 'expired')),
    payload TEXT NOT NULL, expires_at TEXT NOT NULL, received_at TEXT NOT NULL,
    PRIMARY KEY (task_id, revision));
CREATE TABLE IF NOT EXISTS inbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, task_id TEXT, revision INTEGER,
    code TEXT NOT NULL, content_hash TEXT);
"""


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
            self._conn = connect(path, busy_timeout_seconds, SCHEMA)
        except DatabaseBusy as exc:
            raise InboxBusy(str(exc)) from exc

    def close(self) -> None:
        self._conn.close()

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
            "UPDATE proposals SET state = 'expired' WHERE state = 'pending' AND expires_at <= ?",
            (_iso(now),),
        )
        self._purge_finished_tasks(now)
        existing = self._conn.execute(
            "SELECT content_hash, state FROM proposals WHERE task_id = ? AND revision = ?",
            (proposal.task_id, proposal.revision),
        ).fetchone()
        if existing is not None:
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
            "INSERT INTO proposals VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (proposal.task_id, proposal.revision, digest, _payload(proposal),
             _iso(proposal.decision_expires_at), _iso(now)),
        )
        return Accepted(proposal.task_id, proposal.revision, "pending", digest, False)

    def _check_capacity(self, supersedes: bool) -> None:
        """待處理數與總列數都要有空間;取代自己任務的舊提案會先釋放一個名額。"""
        pending = self._conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE state = 'pending'").fetchone()[0]
        total = self._conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        if pending - (1 if supersedes else 0) >= self._max_pending or total >= MAX_ROWS:
            raise InboxFull()

    def _purge_finished_tasks(self, now: datetime) -> None:
        """整個清掉「沒有待處理、而且最後一次收件已超過保留期限」的任務。

        只清整個任務,不清單一修訂:修訂連號是拿最高修訂算的,清一半會讓序號倒退。保留期限比
        提案能活的時間長,所以被清掉的提案再送來一定已過期,不會被當成新提案收下。
        """
        self._conn.execute(
            "DELETE FROM proposals WHERE task_id IN (SELECT task_id FROM proposals "
            "GROUP BY task_id HAVING SUM(state = 'pending') = 0 AND MAX(received_at) < ?)",
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
            "SELECT state FROM proposals WHERE task_id = ? AND revision = ?",
            (task_id, revision),
        ).fetchone()
        return bool(row and row[0] == "pending")

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
