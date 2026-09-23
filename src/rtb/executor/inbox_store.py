"""提案收件表(執行行程自己的 SQLite):安全地收、去重、記帳,不執行任何提案。

收件鍵是(任務編號, 修訂序號)。所有判斷都在同一個 BEGIN IMMEDIATE 交易內完成:
先把到期的待處理標為已過期,再依序查收件鍵、修訂連號、決策到期時間、全域上限,最後寫入並提交。
主鍵是最後一道防線,但正常路徑靠交易內的先查後寫,並行的同鍵請求會排隊。

事件紀錄只存固定欄位(時間、任務編號、修訂、封閉列舉的事件代碼、內容雜湊),
絕不存請求原文;每種事件代碼各有筆數上限;事件寫入失敗不影響對呼叫者的回應。

這支模組也是整個執行行程資料庫唯一開連線、開交易的地方:外部寫入嘗試的表結構在
`attempt_store`,但由這裡建立;嘗試紀錄的讀寫只能透過 `transaction()` 拿到已在交易中的連線,
所以之後「取件與開始一筆」可以放進同一個交易。交易物件只在這裡建立(建立時要出示憑證),交易結束就作廢。

佇列語意(Phase 4 增量 1):收件表同時是提案佇列。處置有四態——處理中(取件當下寫,租約還在)、
已交給執行、已擋下、死信(後三者是確認)。租約照 Phase 0 的裁定:到期時間加序號加擁有者;
延長租約、確認與嘗試寫入一律帶收據做條件寫入,對不上就更新 0 列,呼叫端放棄這把鍵。
處理中不是待處理:不佔名額、不會被取代、不會被收件時的到期標記改、任務不會被保留期清除。
"""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from rtb.domain.attempt import AttemptState, OutcomeCode, operation_key
from rtb.domain.proposal import (
    MAX_DECISION_LIFETIME,
    MAX_INT,
    Proposal,
    content_hash,
    parse_proposal,
)
from rtb.executor import attempt_store
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy, connect, immediate_transaction

DEFAULT_MAX_PENDING = 8
MAX_REVISIONS_PER_TASK = 50  # 一個任務最多修訂這麼多次;要更多就得換任務編號
MAX_ROWS = 5000  # 收件表總列數上限:取代鏈不佔名額,沒有這個上限就能無限寫入吃光磁碟
RETENTION = timedelta(hours=2)  # 沒有待處理的舊任務超過這個時間就整個清掉;必須比提案能活的時間長
CLOCK_SKEW = timedelta(minutes=5)  # 決策建立時間最多可以比現在晚這麼多
MAX_EVENTS_PER_CODE = 200  # 每種事件代碼各留最新這幾筆,一種事件灌爆不會洗掉別種事件的紀錄
VISIBILITY_TIMEOUT = timedelta(seconds=60)  # 暫用,沒有實測校準:一輪最慢是幾次 DSP 呼叫加本地寫入
MAX_DELIVERIES = 5  # 暫用:沒有嘗試紀錄的提案最多交出去幾次,用完進死信


class Disposition(StrEnum):
    """執行行程對一份提案的處置;空值代表還沒處置(仍待處理)。不動既有的狀態欄位。

    處理中不是確認:租約到期後可以再被取件。其餘三種是確認,確認之後不再被取件。
    """

    IN_PROGRESS = "in_progress"  # 取件當下寫:已交出去,租約還在
    HANDED_OFF = "handed_off"  # 已交給執行:這把鍵的嘗試到了已驗證
    BLOCKED = "blocked"  # 執行前檢查沒過或這把鍵的嘗試失敗,附擋下原因代碼
    DEAD_LETTER = "dead_letter"  # 沒有嘗試紀錄、投遞次數用完:不再交出去


class DeadLetterReason(StrEnum):
    """死信原因(封閉列舉):觸發在取件,不在執行前檢查,所以不混進擋下原因。"""

    DELIVERY_LIMIT = "delivery_limit"


class LastFailure(StrEnum):
    """一則訊息最後一次沒能開始嘗試的原因,死信時留給人看它卡在哪一步。

    定義在收件表模組:收件表是下層,不借執行迴圈的結果列舉(反過來匯入會變成循環匯入)。
    """

    DSP_UNAVAILABLE = "dsp_unavailable"  # 讀不到 DSP 現況
    TABLE_FULL = "table_full"  # 全表未結案已滿
    NO_REPORT = "no_report"  # 租約過期被回收:工作者當機或沒回報


class BlockCode(StrEnum):
    """擋下原因(封閉列舉):每一種都有真實觸發路徑,見執行迴圈的執行前檢查、簽發與開始一筆。

    加成員之後,舊收件表會在開啟時照允許值清單逐一比對、缺哪個就重建;成員一旦用過就不能拿掉
    (舊列記著它,重建時會撞允許值限制)。"""

    CAMPAIGN_NOT_FOUND = "campaign_not_found"
    CAMPAIGN_NOT_ACTIVE = "campaign_not_active"
    VERSION_CHANGED = "version_changed"
    CAMPAIGN_NOT_ALLOWED = "campaign_not_allowed"
    OVER_BUDGET_CAP = "over_budget_cap"
    OPERATION_PREVIOUSLY_FAILED = "operation_previously_failed"  # 同一把鍵先前已判定失敗
    AGGREGATE_LIMIT_REACHED = "aggregate_limit_reached"  # 開始一筆時總曝險額度不夠(Phase 6)


class StopKind(StrEnum):
    """新寫入被停下的種類(Phase 6 停下紀錄表)。"""

    AGGREGATE_LIMIT_REACHED = "aggregate_limit_reached"  # 總曝險已滿:擋下結案
    TABLE_FULL = "table_full"  # 全表未結案已滿:延後重投


@dataclass(frozen=True)
class Stop:
    """一筆停下紀錄的內容;兩種停法都記當時已用額度與門檻(沒有簽發到租戶的舊路徑才是空值)。"""

    kind: StopKind
    proposal: Proposal
    key: str
    tenant: str | None
    amount: int
    used: int | None
    limit: int | None


def block_code_for_failure(code: OutcomeCode | None) -> BlockCode:
    """失敗嘗試對應的擋下原因:DSP 回版本衝突寫「版本已變」(分析端要據此重新規劃),其他失敗照舊寫
    「同一操作先前已失敗」。執行端終點確認、開始時撞到既有失敗鍵、取件時撞到既有失敗鍵三處共用。"""
    if code is OutcomeCode.VERSION_CONFLICT:
        return BlockCode.VERSION_CHANGED
    return BlockCode.OPERATION_PREVIOUSLY_FAILED


def _in_list(values: type[StrEnum]) -> str:
    return ", ".join(f"'{member.value}'" for member in values)


# 補欄位時用的定義:資料庫自己也只收列舉值,繞過模組直接寫也寫不進去
_DISPOSITION_COLUMN = f"disposition TEXT CHECK (disposition IN ({_in_list(Disposition)}))"
_BLOCK_CODE_COLUMN = f"block_code TEXT CHECK (block_code IN ({_in_list(BlockCode)}))"
_DEAD_LETTER_COLUMN = (
    f"dead_letter_reason TEXT CHECK (dead_letter_reason IN ({_in_list(DeadLetterReason)}))")
_LAST_FAILURE_COLUMN = f"last_failure TEXT CHECK (last_failure IN ({_in_list(LastFailure)}))"
_LEASE_COLUMNS = ("lease_until TEXT, lease_seq INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, "
                  "deliveries INTEGER NOT NULL DEFAULT 0")
# 「待處理」= 狀態待處理而且沒有處置;收件口每一條規則都用這一句,不各寫一份
PENDING = "state = 'pending' AND disposition IS NULL"
IN_PROGRESS = "state = 'pending' AND disposition = 'in_progress'"
# 保留期清除與「任務是否結束」都看這一句:處理中也算還沒結束
OPEN = f"(({PENDING}) OR ({IN_PROGRESS}))"
EVENT_CODES = frozenset(
    {"content_conflict", "revision_out_of_order", "inbox_full", "expired_proposal",
     "expiry_too_far", "created_in_future", "too_many_revisions"}
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    task_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'superseded', 'expired')),
    payload TEXT NOT NULL, expires_at TEXT NOT NULL, received_at TEXT NOT NULL,
    DISPOSITION_COLUMN, BLOCK_CODE_COLUMN, DEAD_LETTER_COLUMN, LAST_FAILURE_COLUMN, LEASE_COLUMNS,
    PRIMARY KEY (task_id, revision));
CREATE TABLE IF NOT EXISTS inbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, task_id TEXT, revision INTEGER,
    code TEXT NOT NULL, content_hash TEXT);
CREATE TABLE IF NOT EXISTS write_stops (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, task_id TEXT NOT NULL,
    revision INTEGER NOT NULL, content_hash TEXT NOT NULL, key TEXT NOT NULL, tenant TEXT,
    campaign_id TEXT NOT NULL, amount INTEGER NOT NULL, used INTEGER, cap INTEGER,
    capped INTEGER NOT NULL, at TEXT NOT NULL,
    UNIQUE (kind, task_id, revision, content_hash));
"""
_PROPOSALS_COLUMNS = {
    "DISPOSITION_COLUMN": _DISPOSITION_COLUMN, "BLOCK_CODE_COLUMN": _BLOCK_CODE_COLUMN,
    "DEAD_LETTER_COLUMN": _DEAD_LETTER_COLUMN, "LAST_FAILURE_COLUMN": _LAST_FAILURE_COLUMN,
    "LEASE_COLUMNS": _LEASE_COLUMNS,
}
for _name, _ddl in _PROPOSALS_COLUMNS.items():
    SCHEMA = SCHEMA.replace(_name, _ddl)
# 收件表現在該有的全部欄位;舊資料庫少了哪幾欄、或處置的資料庫層限制還是舊的,就重建這張表
_PROPOSAL_FIELDS = ("task_id", "revision", "content_hash", "state", "payload", "expires_at",
                    "received_at", "disposition", "block_code", "dead_letter_reason",
                    "last_failure", "lease_until", "lease_seq", "lease_owner", "deliveries")
_FIELD_DEFAULTS = {"lease_seq": "0", "deliveries": "0"}
# 舊資料庫缺的欄位(嘗試表):表 -> [(欄位名, 補欄位定義)]
_ADDED_COLUMNS = {"attempts": list(attempt_store.ADDED_COLUMNS)}


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


class CorruptedInboxRow(Exception):
    """處理中那一列的提案內容讀不回來:不能當成「沒有這一列」,否則呼叫端會繞過租約。"""


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
class Receipt:
    """取件或接手時拿到的收據:之後延長租約、確認與嘗試寫入都要帶它做條件寫入。"""

    task_id: str
    revision: int
    content_hash: str
    owner: str
    lease_seq: int


@dataclass(frozen=True)
class Delivery:
    message: PendingProposal
    receipt: Receipt


@dataclass(frozen=True)
class Accepted:
    task_id: str
    revision: int
    state: str
    content_hash: str
    replayed: bool
    block_code: str | None = None  # 只有處置是已擋下時有值:分析端據此決定要不要重新規劃


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

    def _proposals_outdated(self) -> bool:
        """收件表少了欄位,或處置的資料庫層限制還只認舊成員(加欄位改不了 CHECK 約束)。"""
        present = {row[1] for row in self._conn.execute("PRAGMA table_info(proposals)")}
        if not set(_PROPOSAL_FIELDS) <= present:
            return True
        sql = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'proposals'").fetchone()
        if sql is None:
            return True
        # 逐一比對目前列舉的每一個值:只看某個特定值,下次再加成員就又看不出來(Phase 6 設計審)
        wanted = [f"'{member.value}'" for member in (*Disposition, *BlockCode)]
        return any(value not in sql[0] for value in wanted)

    def _migrate_columns(self) -> None:
        """沿用 DSP 與分析行程的補欄位做法:每次連線檢查,缺才在交易內補,拿到鎖後再查一次
        (等鎖期間別的連線可能已經補好)。舊資料的新欄位一律是空值:處置空=仍待處理。

        收件表的處置欄位有資料庫層限制,加成員只能照 SQLite 官方的重建表步驟(建新表、抄資料、
        換名),整段在同一個立即取得寫入鎖的交易裡。這段只寫在這裡,不放進共用資料庫工具:
        專案裡只有這一處需要。
        """
        if not self._missing_columns() and not self._proposals_outdated():
            return
        with immediate_transaction(self._conn):
            for table, ddl in self._missing_columns():
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            if self._proposals_outdated():
                self._rebuild_proposals()

    def _rebuild_proposals(self) -> None:
        present = {row[1] for row in self._conn.execute("PRAGMA table_info(proposals)")}
        source = ", ".join(
            name if name in present else _FIELD_DEFAULTS.get(name, "NULL")
            for name in _PROPOSAL_FIELDS)
        table_ddl = SCHEMA.split(";")[0].replace(
            "CREATE TABLE IF NOT EXISTS proposals", "CREATE TABLE proposals_rebuilt")
        self._conn.execute(table_ddl)
        self._conn.execute(
            f"INSERT INTO proposals_rebuilt ({', '.join(_PROPOSAL_FIELDS)}) "  # noqa: S608 - 只拼接模組內固定的欄位清單
            f"SELECT {source} FROM proposals")
        self._conn.execute("DROP TABLE proposals")
        self._conn.execute("ALTER TABLE proposals_rebuilt RENAME TO proposals")

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
            "SELECT content_hash, coalesce(disposition, state), "
            "CASE WHEN disposition = ? THEN block_code END FROM proposals "
            "WHERE task_id = ? AND revision = ?",
            (Disposition.BLOCKED.value, proposal.task_id, proposal.revision),
        ).fetchone()
        if existing is not None:  # 有處置時回處置,沒有才回原狀態;已擋下另帶擋下原因
            if existing[0] == digest:
                return Accepted(proposal.task_id, proposal.revision, existing[1], digest, True,
                                existing[2])
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
        """整個清掉「沒有待處理也沒有處理中、而且最後一次收件已超過保留期限」的任務。

        處理中(含等人工的)算還沒結束:等人工可能比保留期久,清掉就會讓去重帳本悄悄重置。

        只清整個任務,不清單一修訂:修訂連號是拿最高修訂算的,清一半會讓序號倒退。保留期限比
        提案能活的時間長,所以被清掉的提案再送來一定已過期,不會被當成新提案收下。
        """
        self._conn.execute(
            "DELETE FROM proposals WHERE task_id IN (SELECT task_id FROM proposals "  # noqa: S608 - 固定條件
            f"GROUP BY task_id HAVING SUM({OPEN}) = 0 AND MAX(received_at) < ?)",
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

    # ---- 執行迴圈用:都在 transaction() 開的交易裡做 ----
    def _own(self, tx: attempt_store.ExecutorTransaction) -> None:
        if type(tx) is not attempt_store.ExecutorTransaction or not tx.is_open or (
                tx.conn is not self._conn):
            raise attempt_store.NotInTransaction("只能在這個收件口自己開的交易裡處置提案")

    def receive(
        self, tx: attempt_store.ExecutorTransaction, now: datetime, owner: str,
    ) -> Delivery | None:
        """取件:交出最舊一份可取件、而且這把鍵還沒有嘗試紀錄的提案,連同收據。

        呼叫端在交易裡、拿到寫入鎖之後才讀 now。依序:排除有未結案嘗試的廣告(同一個交易裡算,
        不算投遞)→ 挑最舊可取件 → 先取得租約拿收據 → 看這把鍵的嘗試紀錄:終點就用收據確認、
        不交出去;未結案(防線)放掉租約;沒有嘗試才可能交出去——投遞次數已達上限就寫死信。
        每一份被確認、死信或放掉的,都改挑下一份。沒有可交的回 None。
        """
        self._own(tx)
        locked = attempt_store.campaigns_with_unresolved(tx)
        for task_id, revision, digest, payload, reclaimed in self._conn.execute(
                "SELECT task_id, revision, content_hash, payload, "  # noqa: S608 - 固定條件
                f"(disposition IS NOT NULL AND lease_owner IS NOT NULL) FROM proposals "
                f"WHERE ({PENDING}) OR ({IN_PROGRESS} AND lease_until <= ?) "
                "ORDER BY received_at, task_id, revision", (_iso(now),)).fetchall():
            proposal = _parse_payload(payload)
            if proposal is None or proposal.campaign_id in locked:
                continue
            receipt = self._lease(task_id, revision, digest, now, owner)
            if reclaimed:  # 上一個持有者沒回報就讓租約過期:當機或卡住
                self._write_failure(receipt, LastFailure.NO_REPORT)
            existing = attempt_store.latest(tx, operation_key(proposal))
            if existing is not None:
                self._settle_existing(tx, receipt, existing.state, existing.code, now)
                continue
            deliveries = self._conn.execute(
                "SELECT deliveries FROM proposals WHERE task_id = ? AND revision = ?",
                (task_id, revision)).fetchone()[0]
            if deliveries >= MAX_DELIVERIES:
                self._finish(receipt, now, "disposition = ?, dead_letter_reason = ?",
                             (Disposition.DEAD_LETTER.value, DeadLetterReason.DELIVERY_LIMIT.value))
                continue
            self._conn.execute(
                "UPDATE proposals SET deliveries = deliveries + 1 "
                "WHERE task_id = ? AND revision = ?", (task_id, revision))
            return Delivery(PendingProposal(task_id, revision, digest, proposal), receipt)
        return None

    def _settle_existing(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, state: AttemptState,
        code: OutcomeCode | None, now: datetime,
    ) -> None:
        """這把鍵已有嘗試:終點就確認;未結案就放掉租約,交給對帳。都不算投遞。"""
        if state is AttemptState.VERIFIED:
            done = self.ack_handed_off(tx, receipt, now)
        elif state is AttemptState.FAILED:
            done = self.ack_blocked(tx, receipt, now, block_code_for_failure(code))
        else:
            done = self.release(tx, receipt, now, None)
        assert done  # noqa: S101 - 收據是同一個交易裡剛拿到的,條件寫入必然成立

    def _lease(self, task_id: str, revision: int, digest: str, now: datetime,
               owner: str) -> Receipt:
        self._conn.execute(
            "UPDATE proposals SET disposition = ?, lease_seq = lease_seq + 1, lease_owner = ?, "
            "lease_until = ? WHERE task_id = ? AND revision = ?",
            (Disposition.IN_PROGRESS.value, owner, _iso(now + VISIBILITY_TIMEOUT),
             task_id, revision))
        seq = self._conn.execute(
            "SELECT lease_seq FROM proposals WHERE task_id = ? AND revision = ?",
            (task_id, revision)).fetchone()[0]
        return Receipt(task_id, revision, digest, owner, int(seq))

    def _write_failure(self, receipt: Receipt, failure: LastFailure) -> None:
        self._conn.execute(
            "UPDATE proposals SET last_failure = ? WHERE task_id = ? AND revision = ?",
            (failure.value, receipt.task_id, receipt.revision))

    def _held(self, receipt: Receipt, now: datetime, assignment: str,
              values: tuple[object, ...]) -> bool:
        """帶收據的條件寫入:處理中、序號與擁有者相符、租約還沒到期;任何一項不符就 0 列。"""
        if type(receipt) is not Receipt:
            raise TypeError("收據必須是取件或接手時拿到的 Receipt")
        cursor = self._conn.execute(
            f"UPDATE proposals SET {assignment} WHERE task_id = ? AND revision = ? "  # noqa: S608 - 只拼接模組內固定的欄位
            f"AND content_hash = ? AND {IN_PROGRESS} AND lease_seq = ? AND lease_owner = ? "
            "AND lease_until > ?",
            (*values, receipt.task_id, receipt.revision, receipt.content_hash,
             receipt.lease_seq, receipt.owner, _iso(now)))
        return cursor.rowcount == 1

    def _finish(self, receipt: Receipt, now: datetime, assignment: str,
                values: tuple[object, ...]) -> bool:
        return self._held(receipt, now, f"{assignment}, lease_until = NULL, lease_owner = NULL",
                          values)

    def extend(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
    ) -> bool:
        """帶收據續租;嘗試寫入前在同一個交易裡先做這一步,核對與續租是同一個條件寫入。"""
        self._own(tx)
        return self._held(receipt, now, "lease_until = ?", (_iso(now + VISIBILITY_TIMEOUT),))

    def release(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
        failure: LastFailure | None,
    ) -> bool:
        """放掉租約(到期時間設成現在),下一輪就能再取;沒能開始嘗試的原因記在最後一次失敗。

        擁有者清空:下次被取時不算「沒回報」。"""
        self._own(tx)
        if failure is not None and type(failure) is not LastFailure:
            raise ValueError("失敗原因必須是封閉列舉 LastFailure 的成員")
        return self._held(
            receipt, now,
            "lease_until = ?, lease_owner = NULL, last_failure = coalesce(?, last_failure)",
            (_iso(now), None if failure is None else failure.value))

    def ack_handed_off(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
    ) -> bool:
        self._own(tx)
        return self._finish(receipt, now, "disposition = ?", (Disposition.HANDED_OFF.value,))

    def ack_blocked(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
        code: BlockCode,
    ) -> bool:
        self._own(tx)
        if type(code) is not BlockCode:
            raise ValueError("擋下原因代碼必須是封閉列舉 BlockCode 的成員")
        return self._finish(receipt, now, "disposition = ?, block_code = ?",
                            (Disposition.BLOCKED.value, code.value))

    def record_stop(
        self, tx: attempt_store.ExecutorTransaction, stop: Stop, now: datetime,
    ) -> None:
        """寫一列停下紀錄(只增不改、不清理):同一份提案同一種類只記一列,重投時再記一次就略過。

        已用額度與門檻寫入時封頂在資料庫整數上限並標記(舊資料保守加總可能超過,寫不進去會讓
        整個擋下交易回滾、提案反覆重試又沒留證據)。"""
        self._own(tx)
        capped = any(v is not None and v > MAX_INT for v in (stop.used, stop.limit))
        used, cap = (None if v is None else min(v, MAX_INT) for v in (stop.used, stop.limit))
        prop = stop.proposal
        self._conn.execute(
            "INSERT OR IGNORE INTO write_stops (kind, task_id, revision, content_hash, key, "
            "tenant, campaign_id, amount, used, cap, capped, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (stop.kind.value, prop.task_id, prop.revision, content_hash(prop), stop.key,
             stop.tenant, prop.campaign_id, min(stop.amount, MAX_INT), used, cap, int(capped),
             _iso(now)))

    def ack_expired(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
    ) -> bool:
        """提案過期沿用既有的「已過期」狀態,不是處置:處置清空,從此不可取件。"""
        self._own(tx)
        return self._finish(receipt, now, "state = 'expired', disposition = NULL", ())

    def in_progress_for(
        self, tx: attempt_store.ExecutorTransaction, task_id: str, key: str,
    ) -> PendingProposal | None:
        """這把鍵對應、而且處置是處理中的那一列(同一把鍵可能對應多份修訂,只取處理中的)。

        先掃完:找到讀得回來、鍵相符的就回它,同任務別的修訂壞掉不拖累它。找不到而又有讀不回來的
        列,就分不出「真的沒有」還是「就是壞掉那列」,不能回「沒有」讓呼叫端走不帶收據的路。
        """
        self._own(tx)
        unreadable = None
        for revision, digest, payload in self._conn.execute(
                f"SELECT revision, content_hash, payload FROM proposals WHERE task_id = ? "  # noqa: S608 - 固定條件
                f"AND {IN_PROGRESS} ORDER BY revision", (task_id,)).fetchall():
            proposal = _parse_payload(payload)
            if proposal is None:
                unreadable = unreadable or f"{task_id}/{revision}"
            elif operation_key(proposal) == key:
                return PendingProposal(task_id, revision, digest, proposal)
        if unreadable is not None:
            raise CorruptedInboxRow(unreadable)
        return None

    def in_progress_keys(
        self, tx: attempt_store.ExecutorTransaction,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """所有處理中那幾列的冪等鍵,以及讀不回來的列(任務/修訂):對帳用來找「嘗試已到終點、
        收件表卻還沒確認」的漏網。

        讀不回來的列算不出鍵,不能默默跳過(跳過的已結案訊息會永遠停在處理中);也不能讓它拖累
        健康的鍵。所以兩份都回給呼叫端:先處理健康的,最後再因為有壞列停機讓人看。這是真的資料
        損毀才會走到:收件時存的是驗證過、重新序列化的內容。
        """
        self._own(tx)
        keys, unreadable = [], []
        for task_id, revision, payload in self._conn.execute(
                f"SELECT task_id, revision, payload FROM proposals WHERE {IN_PROGRESS} "  # noqa: S608 - 固定條件
                "ORDER BY received_at, task_id, revision").fetchall():
            proposal = _parse_payload(payload)
            if proposal is None:
                unreadable.append(f"{task_id}/{revision}")
            else:
                keys.append(operation_key(proposal))
        return tuple(keys), tuple(unreadable)

    def take_over(
        self, tx: attempt_store.ExecutorTransaction, message: PendingProposal, now: datetime,
        owner: str,
    ) -> Receipt | None:
        """原子接手租約:處理中而且租約已到期、或擁有者就是自己,才把序號加 1 換成自己。

        不讀現值充當收據:別人持有而且沒到期就回 None,這一輪跳過。"""
        self._own(tx)
        cursor = self._conn.execute(
            "UPDATE proposals SET lease_seq = lease_seq + 1, lease_owner = ?, lease_until = ? "  # noqa: S608 - 固定條件
            f"WHERE task_id = ? AND revision = ? AND content_hash = ? AND {IN_PROGRESS} "
            "AND (lease_until <= ? OR lease_owner = ?)",
            (owner, _iso(now + VISIBILITY_TIMEOUT), message.task_id, message.revision,
             message.content_hash, _iso(now), owner))
        if cursor.rowcount != 1:
            return None
        seq = self._conn.execute(
            "SELECT lease_seq FROM proposals WHERE task_id = ? AND revision = ?",
            (message.task_id, message.revision)).fetchone()[0]
        return Receipt(message.task_id, message.revision, message.content_hash, owner, int(seq))

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


def _parse_payload(payload: str) -> Proposal | None:
    """存的內容讀不回提案就當沒有(不讓一列壞資料卡住全部)。"""
    try:
        return parse_proposal(json.loads(payload)).proposal
    except ValueError:
        return None


def _payload(proposal: Proposal) -> str:
    return json.dumps(proposal.to_primitives(), sort_keys=True, ensure_ascii=True)
