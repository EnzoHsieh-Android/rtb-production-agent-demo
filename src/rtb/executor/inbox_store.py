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

待核可(Phase 6 增量 3):被總曝險已滿或比例過大擋下、還沒有人工核可的提案停在這裡。跟處理中
一樣是暫時狀態、算任務還沒結束;取件不會撿到它,由執行迴圈每輪的「處理待核可」那一步放回待處理、
確認成已擋下或被取代。核可表與核可使用表也在這裡建(只增不改);驗核可在執行行程,不在這裡。

生命週期事件(Phase 9 增量 1):每一支會寫收件表那一列的方法,都在同一個交易裡由同一支內部函式
`_log` 寫一列事件(續租除外,保留期整批刪除也不寫);事件表只增不改、不隨收件表清除,廣告、冪等鍵、
政策版本在寫事件時從那一列的提案內容算出存下。唯讀開法 `ReadOnlyInbox` 只有唯讀連線、只發唯讀
交易,開啟時不建表、不補欄位,資料庫還沒升級就丟 DatabaseNotUpgraded。
"""

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from rtb import PROGRAM_VERSION
from rtb.domain.attempt import AttemptState, OutcomeCode, operation_key
from rtb.domain.proposal import (
    MAX_DECISION_LIFETIME,
    MAX_INT,
    Proposal,
    content_hash,
    parse_proposal,
)
from rtb.executor import attempt_store
from rtb.executor.attempt_store import Actor, Source
from rtb.sqlitekit import (
    BUSY_TIMEOUT_SECONDS,
    DatabaseBusy,
    DatabaseNotUpgraded,
    connect,
    connect_read_only,
    immediate_transaction,
    is_lock_contention,
    missing_schema,
    read_snapshot,
)

__all__ = ["DatabaseNotUpgraded"]  # 唯讀開法丟的例外,呼叫端從這裡拿

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

    處理中與待核可不是確認:處理中租約到期後可以再被取件,待核可是等人簽核可的暫時狀態。已交給執行、
    已擋下、死信三種是確認,確認之後不再被取件(死信可由重放指令放回待處理,見 Phase 8)。
    """

    IN_PROGRESS = "in_progress"  # 取件當下寫:已交出去,租約還在
    HANDED_OFF = "handed_off"  # 已交給執行:這把鍵的嘗試到了已驗證
    BLOCKED = "blocked"  # 執行前檢查沒過或這把鍵的嘗試失敗,附擋下原因代碼
    DEAD_LETTER = "dead_letter"  # 沒有嘗試紀錄、投遞次數用完:不再交出去
    # 可核可的擋法還沒有有效核可(Phase 6 增量 3):暫時狀態,擋下原因欄記是哪一關
    AWAITING_APPROVAL = "awaiting_approval"


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
    # 單筆加預算超過比例上限(Phase 6 增量 2)
    BUDGET_INCREASE_TOO_LARGE = "budget_increase_too_large"
    OPERATION_PREVIOUSLY_FAILED = "operation_previously_failed"  # 同一把鍵先前已判定失敗
    AGGREGATE_LIMIT_REACHED = "aggregate_limit_reached"  # 開始一筆時總曝險額度不夠(Phase 6)
    POLICY_VERSION_CHANGED = "policy_version_changed"  # 提案的政策版本不是現行版本(Phase 8)
    DECISION_STALE = "decision_stale"  # 決策建立超過新鮮度上限(Phase 8)


class StopKind(StrEnum):
    """新寫入被停下的種類(Phase 6 停下紀錄表)。"""

    AGGREGATE_LIMIT_REACHED = "aggregate_limit_reached"  # 總曝險已滿:擋下結案
    TABLE_FULL = "table_full"  # 全表未結案已滿:延後重投
    BUDGET_INCREASE_TOO_LARGE = "budget_increase_too_large"  # 比例過大:進待核可(增量 3)


class LifecycleKind(StrEnum):
    """生命週期事件的種類(Phase 9 增量 1,封閉列舉;資料庫層不設允許值限制,防線在寫事件的函式與
    測試:這張表不清除,之後加種類若要改資料庫層限制得在寫入鎖下整表重建)。每一種都有真實觸發路徑。"""

    RECEIVED = "received"  # 收件
    SUPERSEDED = "superseded"  # 收件時被新修訂取代;或處理待核可時同任務已有更新的修訂
    EXPIRED = "expired"  # 收件時順手標成已過期;或執行迴圈確認成已過期
    DELIVERED = "delivered"  # 取件(不是上一個持有者沒回報)
    RECLAIMED = "reclaimed"  # 租約過期被接手:取件時上一個持有者沒回報,或對帳原子接手
    LEASE_RELEASED = "lease_released"  # 放掉租約,原因代碼是這次沒能開始的原因(可為空)
    HANDED_OFF = "handed_off"
    BLOCKED = "blocked"  # 帶擋下原因;處理待核可時到期也寫這一種,原因是原本那一關
    DEAD_LETTERED = "dead_lettered"  # 帶死信原因
    AWAITING_APPROVAL = "awaiting_approval"  # 帶關卡
    APPROVAL_RELEASED = "approval_released"  # 處理待核可時核可放回待處理
    REPLAY_REQUEUED = "replay_requeued"  # 死信重放放回待處理(管理指令)


# 終點種類的事件:一份提案(任務、修訂、內容雜湊)最後一個終點事件就是它目前的結果(增量 2 的指標
# 用);事件表另有只收這幾種的部分索引,查詢條件照抄同一份清單才對得上索引
TERMINAL_KINDS = frozenset({LifecycleKind.HANDED_OFF, LifecycleKind.BLOCKED, LifecycleKind.EXPIRED,
                            LifecycleKind.DEAD_LETTERED})
_TERMINAL_KIND_LIST = ", ".join(sorted(f"'{kind.value}'" for kind in TERMINAL_KINDS))

# 可以人工核可的兩種擋法(Phase 6 增量 3):都是「量的上限」;硬規則寫進去就是錯的,不能核可
APPROVABLE = frozenset({BlockCode.AGGREGATE_LIMIT_REACHED, BlockCode.BUDGET_INCREASE_TOO_LARGE})


@dataclass(frozen=True)
class Stop:
    """一筆停下紀錄的內容。總曝險已滿與表滿延後記當時已用額度與門檻;比例過大這兩欄是空值。"""

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
AWAITING = "state = 'pending' AND disposition = 'awaiting_approval'"
_AWAITING_P = "p.state = 'pending' AND p.disposition = 'awaiting_approval'"  # 同一句,帶別名
# 保留期清除與「任務是否結束」都看這一句:處理中、待核可也算還沒結束
OPEN = f"(({PENDING}) OR ({IN_PROGRESS}) OR ({AWAITING}))"
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
CREATE INDEX IF NOT EXISTS write_stops_by_tenant ON write_stops (tenant, kind, at);
CREATE INDEX IF NOT EXISTS write_stops_by_campaign ON write_stops (campaign_id, kind, at);
CREATE INDEX IF NOT EXISTS write_stops_by_time ON write_stops (kind, at);
CREATE TABLE IF NOT EXISTS approvals (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL,
    task_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN (APPROVABLE_LIST)), token TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS approvals_by_proposal
    ON approvals (task_id, revision, content_hash, stage);
CREATE TABLE IF NOT EXISTS approval_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL, task_id TEXT NOT NULL,
    revision INTEGER NOT NULL, content_hash TEXT NOT NULL, key TEXT NOT NULL, tenant TEXT NOT NULL,
    stage TEXT NOT NULL, amount INTEGER NOT NULL, used INTEGER, cap INTEGER,
    capped INTEGER NOT NULL, at TEXT NOT NULL,
    UNIQUE (task_id, revision, content_hash, stage));
CREATE TABLE IF NOT EXISTS dead_letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL, key TEXT NOT NULL,
    failure_class TEXT NOT NULL CHECK (failure_class IN (FAILURE_CLASS_LIST)),
    reason TEXT NOT NULL CHECK (reason IN (DEAD_LETTER_REASON_LIST)), last_failure TEXT,
    deliveries INTEGER NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS dead_letters_by_proposal ON dead_letters (task_id, revision);
CREATE TABLE IF NOT EXISTS dead_letter_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT, envelope INTEGER REFERENCES dead_letters (id),
    task_id TEXT NOT NULL, revision INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN (DEAD_LETTER_ACTION_LIST)), operator TEXT NOT NULL,
    reason TEXT, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS dead_letter_ops_by_envelope ON dead_letter_ops (envelope);
CREATE INDEX IF NOT EXISTS approval_uses_by_tenant ON approval_uses (tenant, at);
CREATE INDEX IF NOT EXISTS approval_uses_by_time ON approval_uses (at);
CREATE INDEX IF NOT EXISTS approval_uses_by_key ON approval_uses (key, stage);
CREATE TABLE IF NOT EXISTS lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, task_id TEXT NOT NULL,
    revision INTEGER NOT NULL, content_hash TEXT, campaign_id TEXT, key TEXT, policy_version TEXT,
    tenant TEXT, kind TEXT NOT NULL, reason TEXT, source TEXT NOT NULL, actor TEXT,
    deliveries INTEGER, from_existing INTEGER NOT NULL, program_version TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lifecycle_events_by_time ON lifecycle_events (at);
CREATE INDEX IF NOT EXISTS lifecycle_events_by_task ON lifecycle_events (task_id, id);
CREATE INDEX IF NOT EXISTS lifecycle_events_terminal
    ON lifecycle_events (task_id, revision, content_hash, id) WHERE kind IN (TERMINAL_KIND_LIST);
"""
_PROPOSALS_COLUMNS = {
    "DISPOSITION_COLUMN": _DISPOSITION_COLUMN, "BLOCK_CODE_COLUMN": _BLOCK_CODE_COLUMN,
    "DEAD_LETTER_COLUMN": _DEAD_LETTER_COLUMN, "LAST_FAILURE_COLUMN": _LAST_FAILURE_COLUMN,
    "LEASE_COLUMNS": _LEASE_COLUMNS,
}
for _name, _ddl in _PROPOSALS_COLUMNS.items():
    SCHEMA = SCHEMA.replace(_name, _ddl)
SCHEMA = SCHEMA.replace("APPROVABLE_LIST", ", ".join(sorted(f"'{c.value}'" for c in APPROVABLE)))
SCHEMA = SCHEMA.replace("TERMINAL_KIND_LIST", _TERMINAL_KIND_LIST)
# 收件表現在該有的全部欄位;舊資料庫少了哪幾欄、或處置的資料庫層限制還是舊的,就重建這張表
_PROPOSAL_FIELDS = ("task_id", "revision", "content_hash", "state", "payload", "expires_at",
                    "received_at", "disposition", "block_code", "dead_letter_reason",
                    "last_failure", "lease_until", "lease_seq", "lease_owner", "deliveries")
_FIELD_DEFAULTS = {"lease_seq": "0", "deliveries": "0"}
# 舊資料庫缺的欄位(嘗試表):表 -> [(欄位名, 補欄位定義)]
_ADDED_COLUMNS = {"attempts": list(attempt_store.ADDED_COLUMNS),
                  "dsp_calls": list(attempt_store.DSP_CALL_ADDED_COLUMNS)}
_LIFECYCLE_FIELDS = ("id", "at", "task_id", "revision", "content_hash", "campaign_id", "key",
                     "policy_version", "tenant", "kind", "reason", "source", "actor", "deliveries",
                     "from_existing", "program_version")
# 唯讀開法要求資料庫已經有的表與欄位(缺了就是還沒升級,唯讀連線不能補)
_REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "proposals": _PROPOSAL_FIELDS, "lifecycle_events": _LIFECYCLE_FIELDS,
    "attempts": tuple(name for name, _ in attempt_store.ADDED_COLUMNS),
    "dsp_calls": attempt_store.DSP_CALL_FIELDS, "write_stops": ("tenant",),
    "approvals": ("token",), "approval_uses": ("tenant",), "dead_letters": ("key",),
    "dead_letter_ops": ("operator",),
}
# 唯讀開法要求已經有的索引(指標的窗口讀取與最後終點查法靠它們):唯讀連線不建索引,只被唯讀開過的舊庫
# 缺了會退化成全表掃描,視同還沒升級(Phase 9 增量 2 代碼審第 1 輪);增量 3 對帳服務水準的結果不明窗口
# 讀取同一套
_REQUIRED_INDEXES = ("lifecycle_events_by_time", "lifecycle_events_terminal", "dsp_calls_by_time",
                     "attempts_terminal_by_time", "attempts_unknown_by_time")
_INBOX = Actor(Source.INBOX)  # 收件口觸發的事件:沒有執行者


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


class InboxBusyNotStarted(InboxBusy):
    """開寫入交易(BEGIN IMMEDIATE)就等鎖逾時:交易沒開起來、什麼都沒寫(F7 效能計劃第 2 部分)。

    只有交易入口知道「還沒開起來」,所以用型別表達,不讓呼叫端另記旗標;是收件口忙碌的子類別,
    既有抓收件口忙碌的地方照舊抓得到。執行迴圈只對這一種在限度內重試寫結果。"""


@dataclass(frozen=True)
class PendingProposal:
    task_id: str
    revision: int
    content_hash: str
    proposal: Proposal


@dataclass(frozen=True)
class Receipt:
    """取件或接手時拿到的收據:之後延長租約、確認與嘗試寫入都要帶它做條件寫入。

    tenant 不參與條件寫入:執行迴圈簽發之後(或接手時從嘗試第一列)填上,之後用這張收據寫的
    生命週期事件記簽發當時的租戶;簽發之前是空值。"""

    task_id: str
    revision: int
    content_hash: str
    owner: str
    lease_seq: int
    tenant: str | None = None


@dataclass(frozen=True)
class LifecycleEvent:
    """一列生命週期事件(欄位順序同事件表)。"""

    id: int
    at: str
    task_id: str
    revision: int
    content_hash: str | None
    campaign_id: str | None
    key: str | None
    policy_version: str | None
    tenant: str | None
    kind: str
    reason: str | None
    source: str
    actor: str | None
    deliveries: int | None
    from_existing: bool
    program_version: str


@dataclass(frozen=True)
class DeadLetterRow:
    """一列死信信封(追蹤檢視用)。"""

    id: int
    task_id: str
    revision: int
    content_hash: str
    key: str
    failure_class: str
    reason: str
    last_failure: str | None
    deliveries: int
    at: str


@dataclass(frozen=True)
class DeadLetterOp:
    """一列死信操作稽核(追蹤檢視用)。"""

    id: int
    envelope: int | None
    task_id: str
    revision: int
    action: str
    operator: str
    reason: str | None
    at: str


@dataclass(frozen=True)
class Delivery:
    message: PendingProposal
    receipt: Receipt


@dataclass(frozen=True)
class AwaitingProposal:
    """一份待核可的提案,連同它停在哪一關。"""

    message: PendingProposal
    stage: BlockCode


@dataclass(frozen=True)
class ApprovalUse:
    """核可生效時寫的一列使用紀錄(稽核用);比例過大那一關的已用額度與門檻是空值。"""

    approval_id: str
    proposal: Proposal
    key: str
    tenant: str
    stage: BlockCode
    amount: int
    used: int | None
    limit: int | None


class FailureClass(StrEnum):
    """失敗分類(Phase 8 [S501]):暫時的次數用完進死信,永久的走擋下結案、不進死信。"""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


def failure_class(kind: LastFailure | BlockCode) -> FailureClass:
    """最後卡在哪一步的每一種都是暫時的(讀不到 DSP、全表未結案已滿、工作者沒回報);擋下原因的
    每一種都是永久的(業務上不成立,重試不會變好)。兩個列舉之後加成員,測試逐一列舉會抓到。"""
    if type(kind) is LastFailure:
        return FailureClass.TRANSIENT
    if type(kind) is BlockCode:
        return FailureClass.PERMANENT
    raise TypeError("只分類最後失敗與擋下原因兩個封閉列舉")


class DeadLetterAction(StrEnum):
    """死信操作稽核的動作(Phase 8 [S508])。"""

    DEAD_LETTERED = "dead_lettered"
    REPLAY_REQUESTED = "replay_requested"
    REPLAY_REFUSED = "replay_refused"
    REPLAY_REQUEUED = "replay_requeued"


class ReplayOutcome(StrEnum):
    """重放指令的結果(Phase 8 [S502]):放回待處理,或拒絕的原因。"""

    REQUEUED = "requeued"
    NOT_IN_INBOX = "not_in_inbox"  # 收件表已沒有這一列(過了保留期被清掉,或從來沒收過)
    NOT_DEAD_LETTER = "not_dead_letter"  # 還在,但處置不是死信(例如已被放回、已擋下)
    EXPIRED = "expired"  # 提案已過期
    SUPERSEDED = "superseded"  # 同任務已有更新的修訂:放回舊的會讓已被取代的決策插隊
    UNREADABLE = "unreadable"  # 存的內容讀不回提案:補不了信封,放回也處理不了
    INBOX_FULL = "inbox_full"  # 待處理名額已滿:照收件口既有規則拒絕


# 死信兩張表的允許值清單:列舉定義在建表語句之後,這裡才填進去(模組載入時就完成)
for _name, _members in (("FAILURE_CLASS_LIST", FailureClass),
                        ("DEAD_LETTER_REASON_LIST", DeadLetterReason),
                        ("DEAD_LETTER_ACTION_LIST", DeadLetterAction)):
    SCHEMA = SCHEMA.replace(_name, _in_list(_members))


class AwaitingOutcome(StrEnum):
    """處理待核可那一步對一份待核可提案的三種處置。"""

    EXPIRED = "expired"  # 提案到期:確認成已擋下,擋下原因是原本那一關
    SUPERSEDED = "superseded"  # 同任務已有更新的修訂:被取代,不放回
    RELEASED = "released"  # 有這一關的有效核可:放回待處理、投遞次數歸零


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
    """固定格式的 UTC 字串:字串比大小就等於比時間先後。收件口每一個寫時間的地方都走這一支,
    跟嘗試紀錄、停下紀錄的讀取路徑同一個轉換(沒帶時區就拒絕,不然會被當成本機時間寫錯;
    Phase 6 增量 4 留下的寫入與讀取嚴格度不一致,2026-09-24 統一)。"""
    return attempt_store.iso(moment)


def utc_now() -> datetime:
    return datetime.now(UTC)


if RETENTION <= MAX_DECISION_LIFETIME:  # 設定自相矛盾就不讓模組載入
    raise RuntimeError("RETENTION 必須比提案最長有效期長")


class InboxReads:
    """收件口模組的讀取方法(Phase 9 增量 1):寫入開法(InboxStore)與唯讀開法(ReadOnlyInbox)共用。

    讀取方法的守衛收寫入交易或唯讀交易,兩者都要型別完全相同、還開著、而且是這個物件自己的連線;
    寫入方法在 InboxStore,只收寫入交易。
    """

    _conn: sqlite3.Connection

    def _own_read(self, tx: attempt_store.Readable) -> None:
        if type(tx) not in (attempt_store.ExecutorTransaction, attempt_store.ReadTransaction) or (
                not tx.is_open or tx.conn is not self._conn):
            raise attempt_store.NotInTransaction("只能在這個收件口自己開的交易裡讀")

    def awaiting(
        self, tx: attempt_store.Readable, now: datetime,
    ) -> list[AwaitingProposal]:
        """這一輪有事可做的待核可提案,依收件時間由舊到新:已到期、同任務有更新的修訂、這一關有人
        簽過核可、或同任務已開過嘗試(同一把鍵可能已由另一份修訂在做)。其他待核可不讀,每輪的
        工作量不隨待核可堆積變大(代碼審第 3 輪資安席:失控的來源能堆到全表上限)。這裡是寬的
        預篩,真正的判斷在執行迴圈。讀不回來的列跳過(待核可沒有租約,不會卡住別人;真的資料
        損毀才會走到,留給人看)。"""
        self._own_read(tx)
        result = []
        for task_id, revision, digest, payload, code in self._conn.execute(
                "SELECT p.task_id, p.revision, p.content_hash, p.payload, p.block_code "
                "FROM proposals p WHERE p.state = 'pending' "
                "AND p.disposition = 'awaiting_approval' "
                "AND (p.expires_at <= ? "
                "OR EXISTS (SELECT 1 FROM proposals n WHERE n.task_id = p.task_id "
                "AND n.revision > p.revision) "
                "OR EXISTS (SELECT 1 FROM approvals a WHERE a.task_id = p.task_id "
                "AND a.revision = p.revision AND a.content_hash = p.content_hash "
                "AND a.stage = p.block_code) "
                "OR EXISTS (SELECT 1 FROM attempts f WHERE f.task_id = p.task_id AND f.seq = 1)) "
                "ORDER BY p.received_at, p.task_id, p.revision", (_iso(now),)).fetchall():
            proposal = _parse_payload(payload)
            if proposal is not None:
                result.append(AwaitingProposal(
                    PendingProposal(task_id, revision, digest, proposal), BlockCode(code)))
        return result

    def has_newer_revision(
        self, tx: attempt_store.Readable, task_id: str, revision: int,
    ) -> bool:
        self._own_read(tx)
        return self._highest_revision(task_id) > revision

    def stop_amount(
        self, tx: attempt_store.Readable, message: PendingProposal, stage: BlockCode,
    ) -> int | None:
        """這份提案停在這一關時記下的金額(放回前判核可上限夠不夠用)。"""
        self._own_read(tx)
        row = self._conn.execute(
            "SELECT amount FROM write_stops WHERE kind = ? AND task_id = ? AND revision = ? "
            "AND content_hash = ?",
            (stage.value, message.task_id, message.revision, message.content_hash)).fetchone()
        return None if row is None else int(row[0])

    def latest_approval(
        self, tx: attempt_store.Readable, proposal: Proposal, stage: BlockCode,
    ) -> str | None:
        """同一份提案同一關最後寫進核可表的那一張(自動遞增列號最大);不回頭找舊的。"""
        self._own_read(tx)
        row = self._conn.execute(
            "SELECT token FROM approvals WHERE task_id = ? AND revision = ? AND content_hash = ? "
            "AND stage = ? ORDER BY seq DESC LIMIT 1",
            (proposal.task_id, proposal.revision, content_hash(proposal), stage.value)).fetchone()
        return None if row is None else str(row[0])

    def used_approvals(
        self, tx: attempt_store.Readable, proposal: Proposal,
    ) -> list[tuple[BlockCode, str]]:
        """這份提案用過的核可(關卡、整張核可):同鍵重送前要再核一次它們都還算數。"""
        self._own_read(tx)
        # 同一張核可可能寫過好幾列(每次下達一列),接出來會重複;內容一樣,逐張核對結果不變
        return [(BlockCode(stage), str(token)) for stage, token in self._conn.execute(
            "SELECT u.stage, a.token FROM approval_uses u "
            "JOIN approvals a ON a.approval_id = u.approval_id "
            "WHERE u.task_id = ? AND u.revision = ? AND u.content_hash = ? ORDER BY u.id",
            (proposal.task_id, proposal.revision, content_hash(proposal))).fetchall()]

    def in_progress_for(
        self, tx: attempt_store.Readable, task_id: str, key: str,
    ) -> PendingProposal | None:
        """這把鍵對應、而且處置是處理中的那一列(同一把鍵可能對應多份修訂,只取處理中的)。

        先掃完:找到讀得回來、鍵相符的就回它,同任務別的修訂壞掉不拖累它。找不到而又有讀不回來的
        列,就分不出「真的沒有」還是「就是壞掉那列」,不能回「沒有」讓呼叫端走不帶收據的路。
        """
        self._own_read(tx)
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
        self, tx: attempt_store.Readable,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """所有處理中那幾列的冪等鍵,以及讀不回來的列(任務/修訂):對帳用來找「嘗試已到終點、
        收件表卻還沒確認」的漏網。

        讀不回來的列算不出鍵,不能默默跳過(跳過的已結案訊息會永遠停在處理中);也不能讓它拖累
        健康的鍵。所以兩份都回給呼叫端:先處理健康的,最後再因為有壞列停機讓人看。這是真的資料
        損毀才會走到:收件時存的是驗證過、重新序列化的內容。
        """
        self._own_read(tx)
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

    def awaiting_count(
        self, tx: attempt_store.Readable, *, tenant: str | None = None,
        campaign_id: str | None = None,
    ) -> tuple[int, int]:
        """待核可份數(Phase 6 增量 4 查詢三),以及其中接不到停下紀錄的份數。依租戶、廣告篩時用
        (任務、修訂、內容雜湊、停在哪一關)接停下紀錄;停下紀錄同一份提案同一種類只一列,所以
        每份提案最多接到一列。接不到的只算進不篩的總數,另外回報,不悄悄丟掉。"""
        self._own_read(tx)
        join = ("LEFT JOIN write_stops w ON w.task_id = p.task_id AND w.revision = p.revision "
                "AND w.content_hash = p.content_hash AND w.kind = p.block_code")
        where, params = [_AWAITING_P], []
        for clause, value in (("w.tenant = ?", tenant), ("w.campaign_id = ?", campaign_id)):
            if value is not None:
                where.append(clause)
                params.append(value)
        matched = self._conn.execute(
            f"SELECT count(*) FROM proposals p {join} WHERE {' AND '.join(where)}",  # noqa: S608 - 只拼接固定條件
            params).fetchone()[0]
        unknown = self._conn.execute(
            f"SELECT count(*) FROM proposals p {join} "  # noqa: S608 - 只拼接固定條件
            f"WHERE {_AWAITING_P} AND w.id IS NULL").fetchone()[0]
        return int(matched), int(unknown)

    def approval_use_count(
        self, tx: attempt_store.Readable, *, tenant: str | None = None,
        campaign_id: str | None = None, since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """已核可放行數(查詢三):核可使用表的列數。時間範圍包含起點、不包含終點。"""
        self._own_read(tx)
        sql, params = approval_use_count_query(tenant=tenant, campaign_id=campaign_id,
                                               since=since, until=until)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def stop_count(
        self, tx: attempt_store.Readable, kind: StopKind, *, tenant: str | None = None,
        campaign_id: str | None = None, since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """停下紀錄計數(Phase 6 增量 4 可觀測查詢用);時間範圍包含起點、不包含終點。"""
        self._own_read(tx)
        sql, params = stop_count_query(kind, tenant=tenant, campaign_id=campaign_id,
                                       since=since, until=until)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def stops(
        self, tx: attempt_store.Readable, kind: StopKind, tenant: str,
        since: datetime, until: datetime,
    ) -> tuple[tuple[object, ...], ...]:
        """某租戶在時間範圍內的停下紀錄,依時間排序:(冪等鍵, 任務, 修訂, 內容雜湊, 廣告, 金額,
        當時已用, 當時門檻, 時間)。已用與門檻在沒有額度快照的種類是空值。"""
        self._own_read(tx)
        return tuple(self._conn.execute(
            "SELECT key, task_id, revision, content_hash, campaign_id, amount, used, cap, at "
            "FROM write_stops WHERE kind = ? AND tenant = ? AND at >= ? AND at < ? "
            "ORDER BY at, id",
            (kind.value, tenant, attempt_store.iso(since), attempt_store.iso(until))))

    def lifecycle_events(
        self, tx: attempt_store.Readable, task_id: str,
    ) -> tuple[LifecycleEvent, ...]:
        """一個任務的生命週期事件,依寫入順序(自動遞增序號)。"""
        self._own_read(tx)
        rows = self._conn.execute(
            f"SELECT {', '.join(_LIFECYCLE_FIELDS)} FROM lifecycle_events "  # noqa: S608 - 固定欄位清單
            "WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        return tuple(_event(row) for row in rows)

    def lifecycle_events_between(
        self, tx: attempt_store.Readable, since: datetime, until: datetime,
    ) -> tuple[LifecycleEvent, ...]:
        """時間窗內的生命週期事件(含起點、不含終點),依時間、再依寫入順序;走時間索引(Phase 9
        增量 2 的指標用)。"""
        self._own_read(tx)
        sql, params = lifecycle_events_between_query(since, until)
        return tuple(_event(row) for row in self._conn.execute(sql, params))

    def stop_capped(
        self, tx: attempt_store.Readable, kind: StopKind, task_id: str, revision: int,
        content_hash: str,
    ) -> bool | None:
        """一份提案某一種停下紀錄的已用或門檻有沒有被封頂(數字不是原值);沒有這筆回 None。走停下紀錄
        的唯一鍵(Phase 12 代碼審 r1 d5:展示頁判斷要不要給那組數字)。"""
        self._own_read(tx)
        row = self._conn.execute(
            "SELECT capped FROM write_stops WHERE kind = ? AND task_id = ? AND revision = ? "
            "AND content_hash = ?", (kind.value, task_id, revision, content_hash)).fetchone()
        return None if row is None else bool(row[0])

    def lifecycle_events_after(
        self, tx: attempt_store.Readable, after: int,
    ) -> tuple[LifecycleEvent, ...]:
        """事件編號大於 after 的生命週期事件,依寫入順序,最多一頁(Phase 12 代碼審 r2 a1:展示觀察器
        照編號往後讀,不自己下查詢)。"""
        self._own_read(tx)
        sql, params = lifecycle_events_after_query(after)
        return tuple(_event(row) for row in self._conn.execute(sql, params))

    def approval_uses_for(
        self, tx: attempt_store.Readable, keys: Iterable[str],
    ) -> dict[str, frozenset[str]]:
        """一批冪等鍵各自用過核可的關卡(Phase 9 增量 3 副作用核對用);每批不超過嘗試紀錄模組的
        批量上限,不逐筆查。沒用過核可的鍵不在結果裡。走依鍵與關卡的索引(只讀這個索引就答得出來),
        不每批整表掃(代碼審第 1 輪)。"""
        self._own_read(tx)
        wanted, found = sorted(set(keys)), defaultdict(set)
        for start in range(0, len(wanted), attempt_store.BATCH):
            for key, stage in self._conn.execute(
                    *approval_uses_for_query(wanted[start:start + attempt_store.BATCH])):
                found[key].add(stage)
        return {key: frozenset(stages) for key, stages in found.items()}

    def pending_snapshot(self, tx: attempt_store.Readable) -> tuple[int, str | None]:
        """現在的待處理份數(還沒被取件、也還沒處置)與其中最早的收件時間;收件表有總列數上限,
        查詢成本有上限(Phase 9 增量 2 的佇列現況)。"""
        self._own_read(tx)
        count, oldest = self._conn.execute(
            f"SELECT count(*), min(received_at) FROM proposals WHERE {PENDING}").fetchone()  # noqa: S608 - 固定條件
        return int(count), oldest

    def last_terminal_event(
        self, tx: attempt_store.Readable, task_id: str, revision: int, digest: str,
    ) -> LifecycleEvent | None:
        """一份提案(任務、修訂、內容雜湊)最後一個終點事件;走終點部分索引,不掃全表。"""
        self._own_read(tx)
        sql, params = last_terminal_event_query(task_id, revision, digest)
        row = self._conn.execute(sql, params).fetchone()
        return None if row is None else _event(row)

    def dead_letters_for(
        self, tx: attempt_store.Readable, task_id: str,
    ) -> tuple[DeadLetterRow, ...]:
        self._own_read(tx)
        return tuple(DeadLetterRow(*row) for row in self._conn.execute(
            "SELECT id, task_id, revision, content_hash, key, failure_class, reason, "
            "last_failure, deliveries, at FROM dead_letters WHERE task_id = ? ORDER BY id",
            (task_id,)))

    def dead_letter_ops_for(
        self, tx: attempt_store.Readable, task_id: str,
    ) -> tuple[DeadLetterOp, ...]:
        self._own_read(tx)
        return tuple(DeadLetterOp(*row) for row in self._conn.execute(
            "SELECT id, envelope, task_id, revision, action, operator, reason, at "
            "FROM dead_letter_ops WHERE task_id = ? ORDER BY id", (task_id,)))

    def _highest_revision(self, task_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(revision) FROM proposals WHERE task_id = ?", (task_id,)).fetchone()
        return int(row[0] or 0)


class InboxStore(InboxReads):
    def __init__(
        self,
        path: Path,
        max_pending: int = DEFAULT_MAX_PENDING,
        busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
    ):
        if max_pending < 1:
            raise ValueError("max_pending 必須至少是 1,否則收件口永遠不接受任何提案")
        self._max_pending = max_pending
        # 已用額度查詢「全表沒有未結案舊鍵」的行程內記憶:跟著這條連線走(F7 效能計劃代碼審第 3 輪)
        self._legacy_memo = attempt_store.LegacyMemo()
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
        if (not self._missing_columns() and not self._proposals_outdated()
                and not attempt_store.tenant_index_missing(self._conn)):
            return
        with immediate_transaction(self._conn):
            for table, ddl in self._missing_columns():
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            # 參照後補的租戶欄:補完欄位才建(欄位已齊、只缺這個索引的舊庫也從上面的判斷進來)
            self._conn.execute(attempt_store.TENANT_INDEX)
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

        正常結束就提交,任何例外都回滾;鎖不到丟 InboxBusy(跟收件一樣可以重試)。開交易那一步就鎖不到
        (交易沒開起來、什麼都沒寫)丟它的子類別 InboxBusyNotStarted,執行迴圈靠型別分辨能不能整筆重做
        (F7 效能計劃第 2 部分)。
        """
        issuer = attempt_store._EXECUTOR_TRANSACTION_ISSUER  # 私有憑證:只給這個交易入口用
        tx = attempt_store.ExecutorTransaction(self._conn, issuer, self._legacy_memo)
        began = False
        try:
            with immediate_transaction(self._conn):
                began = True
                yield tx
        except DatabaseBusy as exc:
            if not began:
                raise InboxBusyNotStarted(str(exc)) from exc
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
        self._expire_pending(now)
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
        if supersedes:  # 事件的欄位取自被取代那一列自己的內容:先讀出(寫事件)再標
            self._log(now, proposal.task_id, highest, LifecycleKind.SUPERSEDED, _INBOX)
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
        self._log(now, proposal.task_id, proposal.revision, LifecycleKind.RECEIVED, _INBOX)
        return Accepted(proposal.task_id, proposal.revision, "pending", digest, False)

    def _expire_pending(self, now: datetime) -> None:
        """收件時順手把到期的待處理標成已過期:這條更新不限任務,先查出這次會被標的每一列、逐列寫
        「已過期」事件,再更新(只替本次收件的任務寫會漏掉被順手標到的其他任務)。"""
        swept = self._conn.execute(
            f"SELECT task_id, revision FROM proposals WHERE {PENDING} AND expires_at <= ? "  # noqa: S608 - 固定條件
            "ORDER BY received_at, task_id, revision", (_iso(now),)).fetchall()
        for task_id, revision in swept:
            self._log(now, task_id, revision, LifecycleKind.EXPIRED, _INBOX)
        self._conn.execute(
            f"UPDATE proposals SET state = 'expired' WHERE {PENDING} AND expires_at <= ?",  # noqa: S608 - 固定條件
            (_iso(now),),
        )

    def _log(  # noqa: PLR0913 - 事件的每一欄
        self, now: datetime, task_id: str, revision: int, kind: LifecycleKind, by: Actor, *,
        reason: BlockCode | LastFailure | DeadLetterReason | None = None,
        tenant: str | None = None, from_existing: bool = False,
    ) -> None:
        """寫一列生命週期事件,在呼叫端的交易裡(那次寫入回滾時事件也不在)。

        種類、原因代碼、來源只收封閉列舉的成員。內容雜湊、廣告、冪等鍵、政策版本與投遞次數從收件表
        那一列算出存下(執行前就被擋下的提案沒開過嘗試,收件表兩小時後清掉,只存雜湊就再也查不出
        是哪個廣告);讀不回提案內容的列,那幾欄照實為空。"""
        if type(kind) is not LifecycleKind:
            raise ValueError("事件種類必須是封閉列舉 LifecycleKind 的成員")
        if reason is not None and type(reason) not in (BlockCode, LastFailure, DeadLetterReason):
            raise ValueError("原因代碼必須是擋下原因、最後失敗或死信原因的成員")
        if type(by) is not Actor:
            raise ValueError("來源與執行者必須是 Actor")
        row = self._conn.execute(
            "SELECT content_hash, payload, deliveries FROM proposals "
            "WHERE task_id = ? AND revision = ?", (task_id, revision)).fetchone()
        digest, deliveries = (None, None) if row is None else (row[0], row[2])
        proposal = None if row is None else _parse_payload(row[1])
        self._conn.execute(
            "INSERT INTO lifecycle_events (at, task_id, revision, content_hash, campaign_id, key, "
            "policy_version, tenant, kind, reason, source, actor, deliveries, from_existing, "
            "program_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_iso(now), task_id, revision, digest,
             None if proposal is None else proposal.campaign_id,
             None if proposal is None else operation_key(proposal),
             None if proposal is None else proposal.policy_version, tenant, kind.value,
             None if reason is None else reason.value, by.source.value, by.name, deliveries,
             int(from_existing), PROGRAM_VERSION))

    def _log_held(
        self, receipt: Receipt, now: datetime, kind: LifecycleKind, *,
        reason: BlockCode | LastFailure | None = None, from_existing: bool = False,
    ) -> None:
        """帶收據的寫入成功之後寫事件:執行者是收據的擁有者(執行迴圈),租戶是收據上簽發當時的。"""
        self._log(now, receipt.task_id, receipt.revision, kind,
                  Actor(Source.EXECUTOR_LOOP, receipt.owner), reason=reason,
                  tenant=receipt.tenant, from_existing=from_existing)

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
            delivery = self._deliver(tx, PendingProposal(task_id, revision, digest, proposal),
                                     bool(reclaimed), now, owner)
            if delivery is not None:
                return delivery
        return None

    def _deliver(
        self, tx: attempt_store.ExecutorTransaction, message: PendingProposal, reclaimed: bool,
        now: datetime, owner: str,
    ) -> Delivery | None:
        """取件一份:取得租約拿收據、寫「取件」或「租約過期被接手」事件,再看這把鍵的嘗試紀錄;被
        確認、死信或放掉的回 None(呼叫端改挑下一份)。交出去時事件寫在投遞次數加一之後,記含這一次
        的次數。"""
        task_id, revision = message.task_id, message.revision
        receipt = self._lease(task_id, revision, message.content_hash, now, owner)
        if reclaimed:  # 上一個持有者沒回報就讓租約過期:當機或卡住
            self._write_failure(receipt, LastFailure.NO_REPORT)
        picked = LifecycleKind.RECLAIMED if reclaimed else LifecycleKind.DELIVERED
        by = Actor(Source.EXECUTOR_LOOP, owner)
        existing = attempt_store.latest(tx, operation_key(message.proposal))
        if existing is not None:  # 這把鍵已有嘗試:照確認或放掉的種類再寫一列
            self._log(now, task_id, revision, picked, by)
            signed = replace(receipt, tenant=attempt_store.first_row_tenant(tx, existing.key))
            self._settle_existing(tx, signed, existing.state, existing.code, now)
            return None
        deliveries = self._conn.execute(
            "SELECT deliveries FROM proposals WHERE task_id = ? AND revision = ?",
            (task_id, revision)).fetchone()[0]
        if deliveries >= MAX_DELIVERIES:
            self._log(now, task_id, revision, picked, by)
            if self._finish(receipt, now, "disposition = ?, dead_letter_reason = ?",
                            (Disposition.DEAD_LETTER.value, DeadLetterReason.DELIVERY_LIMIT.value)):
                self._record_dead_letter(task_id, revision, message.content_hash,
                                         message.proposal, deliveries, now, owner)
            return None
        self._conn.execute(
            "UPDATE proposals SET deliveries = deliveries + 1 "
            "WHERE task_id = ? AND revision = ?", (task_id, revision))
        self._log(now, task_id, revision, picked, by)
        return Delivery(message, receipt)

    def _settle_existing(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, state: AttemptState,
        code: OutcomeCode | None, now: datetime,
    ) -> None:
        """這把鍵已有嘗試:終點就確認(事件標依既有結果確認);未結案就放掉租約,交給對帳。都不算投遞。"""
        if state is AttemptState.VERIFIED:
            done = self.ack_handed_off(tx, receipt, now, from_existing=True)
        elif state is AttemptState.FAILED:
            done = self.ack_blocked(tx, receipt, now, block_code_for_failure(code),
                                    from_existing=True)
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

    def _record_dead_letter(  # noqa: PLR0913 - 信封的每一欄
        self, task_id: str, revision: int, digest: str, proposal: Proposal, deliveries: int,
        now: datetime, owner: str,
    ) -> None:
        """進死信的同一個交易裡寫一列死信信封與一列稽核(Phase 8 [S500]、[S508])。信封只增不改、
        不被保留期清理;同一份提案重放後又進死信就另寫一列,不去重(兩次的內容雜湊、冪等鍵、投遞
        次數都一樣,只靠流水編號分得開)。操作人記取件的工作者。"""
        last = self._conn.execute(
            "SELECT last_failure FROM proposals WHERE task_id = ? AND revision = ?",
            (task_id, revision)).fetchone()[0]
        envelope = self._insert_envelope(task_id, revision, digest, proposal, last, deliveries,
                                         now)
        self._audit_dead_letter(envelope, task_id, revision, DeadLetterAction.DEAD_LETTERED,
                                owner, None, now)
        self._log(now, task_id, revision, LifecycleKind.DEAD_LETTERED,
                  Actor(Source.EXECUTOR_LOOP, owner), reason=DeadLetterReason.DELIVERY_LIMIT)

    def _insert_envelope(  # noqa: PLR0913 - 信封的每一欄
        self, task_id: str, revision: int, digest: str, proposal: Proposal, last: str | None,
        deliveries: int, now: datetime,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO dead_letters (task_id, revision, content_hash, key, failure_class, "
            "reason, last_failure, deliveries, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, revision, digest, operation_key(proposal), FailureClass.TRANSIENT.value,
             DeadLetterReason.DELIVERY_LIMIT.value, last, deliveries, _iso(now)))
        assert cursor.lastrowid is not None  # noqa: S101 - 自動遞增列號,插入成功就一定有
        return cursor.lastrowid

    def _backfill_envelope(self, task_id: str, revision: int, now: datetime) -> int | None:
        """升級前就進死信、沒有信封的:重放時補寫一列,稽核才接得到信封(代碼審第 1 輪外家席)。
        不是死信或讀不回提案就不補(之後的條件判斷會拒絕)。"""
        row = self._conn.execute(
            "SELECT content_hash, payload, deliveries, last_failure FROM proposals "
            "WHERE task_id = ? AND revision = ? AND state = 'pending' AND disposition = ?",
            (task_id, revision, Disposition.DEAD_LETTER.value)).fetchone()
        proposal = None if row is None else _parse_payload(row[1])
        if row is None or proposal is None:
            return None
        return self._insert_envelope(task_id, revision, row[0], proposal, row[3], row[2], now)

    def _audit_dead_letter(  # noqa: PLR0913 - 稽核的每一欄
        self, envelope: int | None, task_id: str, revision: int, action: DeadLetterAction,
        operator: str, reason: ReplayOutcome | None, now: datetime,
    ) -> None:
        self._conn.execute(
            "INSERT INTO dead_letter_ops (envelope, task_id, revision, action, operator, reason, "
            "at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (envelope, task_id, revision, action.value, operator,
             None if reason is None else reason.value, _iso(now)))

    def replay(
        self, task_id: str, revision: int, operator: str, clock: Callable[[], datetime],
    ) -> ReplayOutcome:
        """重放管理指令(Phase 8 [S502]):把還活著的死信放回待處理,之後由執行迴圈照一般流程處理,
        沒有任何略過關卡的旗標。條件:收件表那一列還在、處置是死信、提案還沒過期、同任務沒有
        更新的修訂、待處理還有名額;查條件與寫回在同一個立即取得寫入鎖的交易裡,兩人同時重放只有
        一個放回。每次都寫「要求重放」與結果兩列稽核。操作人的格式由重放管理工具檢查(比照核可人
        由核可模組檢查、收件口只存;收件口模組不做欄位驗證)。重放對這份提案最新那一列信封;從沒
        進過死信的,稽核的信封欄是空值。clock 在拿到寫入鎖之後才讀:等鎖期間提案可能過期(代碼審
        第 1 輪外家席)。"""
        try:
            with immediate_transaction(self._conn):
                return self._replay(task_id, revision, operator, clock())
        except DatabaseBusy as exc:
            raise InboxBusy(str(exc)) from exc

    def _replay(self, task_id: str, revision: int, operator: str, now: datetime) -> ReplayOutcome:
        envelope = self._conn.execute(
            "SELECT max(id) FROM dead_letters WHERE task_id = ? AND revision = ?",
            (task_id, revision)).fetchone()[0]
        if envelope is None:
            envelope = self._backfill_envelope(task_id, revision, now)
        self._audit_dead_letter(envelope, task_id, revision, DeadLetterAction.REPLAY_REQUESTED,
                                operator, None, now)
        refused = self._replay_refusal(task_id, revision, now)
        if refused is None:
            self._conn.execute(
                "UPDATE proposals SET disposition = NULL, dead_letter_reason = NULL, "
                "deliveries = 0, lease_until = NULL, lease_owner = NULL "
                "WHERE task_id = ? AND revision = ? AND state = 'pending' AND disposition = ?",
                (task_id, revision, Disposition.DEAD_LETTER.value))
            self._audit_dead_letter(envelope, task_id, revision,
                                    DeadLetterAction.REPLAY_REQUEUED, operator, None, now)
            self._log(now, task_id, revision, LifecycleKind.REPLAY_REQUEUED,
                      Actor(Source.ADMIN_COMMAND, operator))
            return ReplayOutcome.REQUEUED
        self._audit_dead_letter(envelope, task_id, revision, DeadLetterAction.REPLAY_REFUSED,
                                operator, refused, now)
        return refused

    def _replay_refusal(  # noqa: PLR0911 - 每一個重放條件一個出口
        self, task_id: str, revision: int, now: datetime,
    ) -> ReplayOutcome | None:
        row = self._conn.execute(
            "SELECT state, disposition, expires_at, payload FROM proposals "
            "WHERE task_id = ? AND revision = ?",
            (task_id, revision)).fetchone()
        if row is None:
            return ReplayOutcome.NOT_IN_INBOX
        if row[0] != "pending" or row[1] != Disposition.DEAD_LETTER.value:
            return ReplayOutcome.NOT_DEAD_LETTER
        if _parse_payload(row[3]) is None:  # 代碼審第 2 輪外家席:原本照樣放回、稽核記成功
            return ReplayOutcome.UNREADABLE
        if row[2] <= _iso(now):
            return ReplayOutcome.EXPIRED
        if self._highest_revision(task_id) > revision:
            return ReplayOutcome.SUPERSEDED
        # 只看待處理名額:重放不新增列,總列數上限是收新提案用的(代碼審第 1 輪外家兩席)
        pending = self._conn.execute(
            f"SELECT COUNT(*) FROM proposals WHERE {PENDING}").fetchone()[0]  # noqa: S608 - 固定條件
        if pending >= self._max_pending:
            return ReplayOutcome.INBOX_FULL
        return None

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

    def lease_until(self, receipt: Receipt) -> datetime | None:
        """這張收據對應的租約現在到什麼時候(資料庫裡這一列的實際值);已經不是自己的(被接手、已結案、
        序號換了)就回 None。唯讀、不開寫入交易:寫結果撞到忙碌、要決定還能不能再試時讀(F7 效能計劃
        第 2 部分),WAL 模式下讀不必等寫入鎖。

        錯誤照專案唯一的分法(代碼審第 2 輪):讀的時候碰到鎖競爭也回 None(呼叫端當作「不再試」);
        其他資料庫錯誤原樣往外丟,不能被改報成忙碌。租約時間讀得出來卻不是帶時區的 ISO 時間,丟
        CorruptedInboxRow(處理中那一列讀不懂,跟內容讀不回來同一種處理:停下讓人看)。"""
        if type(receipt) is not Receipt:
            raise TypeError("收據必須是取件或接手時拿到的 Receipt")
        try:
            row = self._conn.execute(
                f"SELECT lease_until FROM proposals WHERE task_id = ? AND revision = ? "  # noqa: S608 - 只拼接模組內固定的條件
                f"AND content_hash = ? AND {IN_PROGRESS} AND lease_seq = ? AND lease_owner = ?",
                (receipt.task_id, receipt.revision, receipt.content_hash, receipt.lease_seq,
                 receipt.owner)).fetchone()
        except sqlite3.OperationalError as exc:
            if is_lock_contention(exc):
                return None
            raise
        if row is None or row[0] is None:
            return None
        return _lease_time(row[0], f"{receipt.task_id}/{receipt.revision}")

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
        done = self._held(
            receipt, now,
            "lease_until = ?, lease_owner = NULL, last_failure = coalesce(?, last_failure)",
            (_iso(now), None if failure is None else failure.value))
        if done:  # 原因記表上實際留下的最後一次失敗原因(沒帶原因時沿用舊的;代碼審第 1 輪)
            left = self._conn.execute(
                "SELECT last_failure FROM proposals WHERE task_id = ? AND revision = ?",
                (receipt.task_id, receipt.revision)).fetchone()[0]
            self._log_held(receipt, now, LifecycleKind.LEASE_RELEASED,
                           reason=None if left is None else LastFailure(left))
        return done

    def ack_handed_off(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime, *,
        from_existing: bool = False,
    ) -> bool:
        """from_existing:取件時撞到既有鍵、或開始一筆時鍵已存在,依既有結果確認(事件記「是」)。"""
        self._own(tx)
        done = self._finish(receipt, now, "disposition = ?", (Disposition.HANDED_OFF.value,))
        if done:
            self._log_held(receipt, now, LifecycleKind.HANDED_OFF, from_existing=from_existing)
        return done

    def ack_blocked(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
        code: BlockCode, *, from_existing: bool = False,
    ) -> bool:
        self._own(tx)
        if type(code) is not BlockCode:
            raise ValueError("擋下原因代碼必須是封閉列舉 BlockCode 的成員")
        done = self._finish(receipt, now, "disposition = ?, block_code = ?",
                            (Disposition.BLOCKED.value, code.value))
        if done:
            self._log_held(receipt, now, LifecycleKind.BLOCKED, reason=code,
                           from_existing=from_existing)
        return done

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

    def await_approval(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
        stage: BlockCode,
    ) -> bool:
        """帶收據把處置寫成待核可,擋下原因欄記是哪一關;租約清空(不再歸任何工作者)。"""
        self._own(tx)
        if stage not in APPROVABLE:
            raise ValueError("只有總曝險已滿與比例過大可以等人工核可")
        done = self._finish(receipt, now, "disposition = ?, block_code = ?",
                            (Disposition.AWAITING_APPROVAL.value, stage.value))
        if done:
            self._log_held(receipt, now, LifecycleKind.AWAITING_APPROVAL, reason=stage)
        return done

    def settle_awaiting(
        self, tx: attempt_store.ExecutorTransaction, message: PendingProposal,
        outcome: AwaitingOutcome, now: datetime, *, owner: str | None = None,
    ) -> bool:
        """處理待核可的三種轉換;寫入條件是「這一列現在仍是待核可」,0 列就是別的工作者先處理了。

        待核可沒有租約與收據可以比對,這個條件就是它的並行防線。owner 是呼叫它的執行迴圈擁有者,
        記進事件的執行者;事件的租戶取停在這一關時停下紀錄記的(簽發當時的)。"""
        self._own(tx)
        stage = self._conn.execute(
            f"SELECT block_code FROM proposals WHERE task_id = ? AND revision = ? AND {AWAITING}",  # noqa: S608 - 固定條件
            (message.task_id, message.revision)).fetchone()
        assignment = {
            AwaitingOutcome.EXPIRED: ("disposition = ?", (Disposition.BLOCKED.value,)),
            AwaitingOutcome.SUPERSEDED: ("state = 'superseded', disposition = NULL, "
                                         "block_code = NULL", ()),
            AwaitingOutcome.RELEASED: ("disposition = NULL, block_code = NULL, deliveries = 0, "
                                       "lease_until = NULL, lease_owner = NULL", ()),
        }[outcome]
        cursor = self._conn.execute(
            f"UPDATE proposals SET {assignment[0]} WHERE task_id = ? AND revision = ? "  # noqa: S608 - 只拼接模組內固定的欄位
            f"AND content_hash = ? AND {AWAITING}",
            (*assignment[1], message.task_id, message.revision, message.content_hash))
        if cursor.rowcount != 1:
            return False
        kind, reason = {
            AwaitingOutcome.EXPIRED: (LifecycleKind.BLOCKED, BlockCode(stage[0])),
            AwaitingOutcome.SUPERSEDED: (LifecycleKind.SUPERSEDED, None),
            AwaitingOutcome.RELEASED: (LifecycleKind.APPROVAL_RELEASED, None),
        }[outcome]
        self._log(now, message.task_id, message.revision, kind,
                  Actor(Source.EXECUTOR_LOOP, owner), reason=reason,
                  tenant=self._stopped_tenant(message))
        return True

    def _stopped_tenant(self, message: PendingProposal) -> str | None:
        """這份提案停下時停下紀錄記的租戶(簽發當時的);三種停下種類都查,有多列取最新的。"""
        row = self._conn.execute(
            f"SELECT tenant FROM write_stops WHERE kind IN ({_in_list(StopKind)}) "  # noqa: S608 - 固定列舉值
            "AND task_id = ? AND revision = ? AND content_hash = ? AND tenant IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (message.task_id, message.revision, message.content_hash)).fetchone()
        return None if row is None else str(row[0])

    def record_approval_use(
        self, tx: attempt_store.ExecutorTransaction, use: ApprovalUse, now: datetime,
    ) -> None:
        """核可生效時在開始一筆的同一個交易裡寫一列;同一份提案同一關只記一列。數字比照停下紀錄
        封頂在整數上限並標記。"""
        self._own(tx)
        capped = any(v is not None and v > MAX_INT for v in (use.used, use.limit))
        used, cap = (None if v is None else min(v, MAX_INT) for v in (use.used, use.limit))
        prop = use.proposal
        self._conn.execute(
            "INSERT OR IGNORE INTO approval_uses (approval_id, task_id, revision, content_hash, "
            "key, tenant, stage, amount, used, cap, capped, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (use.approval_id, prop.task_id, prop.revision, content_hash(prop), use.key,
             use.tenant, use.stage.value, min(use.amount, MAX_INT), used, cap, int(capped),
             _iso(now)))

    # ---- 核可管理工具用:各自一個交易 ----
    def find_proposal(self, task_id: str, revision: int) -> AwaitingProposal | None:
        """管理工具要簽核可的那一份提案,連同它現在停在哪一關:只有待核可的才回;讀不回來、
        沒有、或不是待核可都回 None(不能替還沒走到的一關預先簽核可,代碼審第 3 輪外家席)。"""
        try:
            with immediate_transaction(self._conn):
                row = self._conn.execute(
                    f"SELECT content_hash, payload, block_code FROM proposals "  # noqa: S608 - 固定條件
                    f"WHERE task_id = ? AND revision = ? AND {AWAITING}",
                    (task_id, revision)).fetchone()
        except DatabaseBusy as exc:
            raise InboxBusy(str(exc)) from exc
        proposal = None if row is None else _parse_payload(row[1])
        if row is None or proposal is None:
            return None
        return AwaitingProposal(PendingProposal(task_id, revision, row[0], proposal),
                                BlockCode(row[2]))

    def add_approval(
        self, proposal: Proposal, stage: BlockCode, approval_id: str, token: str, now: datetime,
    ) -> None:
        """寫一張核可(只增不改)。驗章在執行行程,這裡只存。每次下達都寫一列,「最新」照自動遞增
        列號:同一秒同樣參數重跑管理工具會簽出一模一樣的一張,它照樣成為最新(依序簽 A、B、再簽 A,
        最新是 A)。所以核可編號不設唯一(代碼審第 1 輪相容席、第 2 輪外家席)。"""
        if stage not in APPROVABLE:
            raise ValueError("只有總曝險已滿與比例過大可以核可")
        try:
            with immediate_transaction(self._conn):
                self._conn.execute(
                    "INSERT INTO approvals (approval_id, task_id, revision, "
                    "content_hash, stage, token, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (approval_id, proposal.task_id, proposal.revision, content_hash(proposal),
                     stage.value, token, _iso(now)))
        except DatabaseBusy as exc:
            raise InboxBusy(str(exc)) from exc

    def ack_expired(
        self, tx: attempt_store.ExecutorTransaction, receipt: Receipt, now: datetime,
    ) -> bool:
        """提案過期沿用既有的「已過期」狀態,不是處置:處置清空,從此不可取件。"""
        self._own(tx)
        done = self._finish(receipt, now, "state = 'expired', disposition = NULL", ())
        if done:
            self._log_held(receipt, now, LifecycleKind.EXPIRED)
        return done

    def take_over(
        self, tx: attempt_store.ExecutorTransaction, message: PendingProposal, now: datetime,
        owner: str,
    ) -> Receipt | None:
        """原子接手租約:處理中而且租約已到期、或擁有者就是自己,才把序號加 1 換成自己。

        不讀現值充當收據:別人持有而且沒到期就回 None,這一輪跳過。"""
        self._own(tx)
        before = self._conn.execute(  # 同一個交易裡先讀舊租約:自己的租約還沒到期就不是「被接手」
            "SELECT lease_until, lease_owner FROM proposals WHERE task_id = ? AND revision = ?",
            (message.task_id, message.revision)).fetchone()
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
        if before[1] != owner or before[0] is None or before[0] <= _iso(now):
            # 舊租約真的到期了,或換了擁有者才記;自己續做不記(比照續租不寫事件,代碼審第 1 輪)
            tenant = attempt_store.first_row_tenant(tx, operation_key(message.proposal))
            self._log(now, message.task_id, message.revision, LifecycleKind.RECLAIMED,
                      Actor(Source.EXECUTOR_LOOP, owner), tenant=tenant)
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


class ReadOnlyInbox(InboxReads):
    """收件口模組的唯讀開法(Phase 9 增量 1):只有唯讀連線、沒有寫入交易入口,只發唯讀交易。

    開啟時不建表、不補欄位;資料庫還沒升級到這一版(缺事件表、呼叫紀錄表或新欄位)丟
    DatabaseNotUpgraded,不寫任何東西。唯讀交易開始時下不取鎖的顯式交易開頭並立刻讀一次,同一個
    唯讀交易裡的多次查詢讀同一個快照;執行迴圈佔著寫入鎖時照樣讀得到(WAL)。"""

    def __init__(self, path: Path, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS):
        self._legacy_memo = attempt_store.LegacyMemo()
        self._conn = connect_read_only(path, busy_timeout_seconds)
        try:
            missing = missing_schema(self._conn, _REQUIRED_SCHEMA, _REQUIRED_INDEXES)
        except BaseException:
            self._conn.close()
            raise
        if missing:
            self._conn.close()
            raise DatabaseNotUpgraded("執行行程資料庫還沒升級:缺 " + ", ".join(missing))

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def read_transaction(self) -> Iterator[attempt_store.ReadTransaction]:
        issuer = attempt_store._READ_TRANSACTION_ISSUER  # 私有憑證:只給這個唯讀交易入口用
        tx = attempt_store.ReadTransaction(self._conn, issuer, self._legacy_memo)
        try:
            with read_snapshot(self._conn):
                yield tx
        finally:
            tx.close()


# 寫入函式的宣告清單:測試從原始碼機械算出寫入函式,必須跟這份完全相同(不靠名字判斷:取件、處理
# 待核可、接手名字像讀、其實會寫)
WRITE_FUNCTIONS = frozenset({
    "InboxStore.accept", "InboxStore.replay", "InboxStore.receive", "InboxStore.extend",
    "InboxStore.release", "InboxStore.ack_handed_off", "InboxStore.ack_blocked",
    "InboxStore.record_stop", "InboxStore.await_approval", "InboxStore.settle_awaiting",
    "InboxStore.record_approval_use", "InboxStore.add_approval", "InboxStore.ack_expired",
    "InboxStore.take_over", "InboxStore.record_event",
})


def stop_count_query(
    kind: StopKind, *, tenant: str | None = None, campaign_id: str | None = None,
    since: datetime | None = None, until: datetime | None = None,
) -> tuple[str, tuple[object, ...]]:
    """停下紀錄計數的查詢語句(測試用它看查詢計畫);篩選值一律走參數,只拼接固定條件。"""
    where, params = ["kind = ?"], [kind.value]
    for clause, value in (("tenant = ?", tenant), ("campaign_id = ?", campaign_id),
                          ("at >= ?", None if since is None else attempt_store.iso(since)),
                          ("at < ?", None if until is None else attempt_store.iso(until))):
        if value is not None:
            where.append(clause)
            params.append(value)
    return f"SELECT count(*) FROM write_stops WHERE {' AND '.join(where)}", tuple(params)  # noqa: S608 - 只拼接固定條件


def lifecycle_events_between_query(
    since: datetime, until: datetime,
) -> tuple[str, tuple[object, ...]]:
    """時間窗內生命週期事件的查詢語句(測試用它看查詢計畫)。"""
    return (f"SELECT {', '.join(_LIFECYCLE_FIELDS)} FROM lifecycle_events "  # noqa: S608 - 固定欄位清單
            "WHERE at >= ? AND at < ? ORDER BY at, id",
            (attempt_store.iso(since), attempt_store.iso(until)))


def lifecycle_events_after_query(after: int) -> tuple[str, tuple[object, ...]]:
    """事件編號大於 after 的生命週期事件查詢語句(測試用它看查詢計畫)。"""
    return (f"SELECT {', '.join(_LIFECYCLE_FIELDS)} FROM lifecycle_events "  # noqa: S608 - 固定欄位清單
            "WHERE id > ? ORDER BY id LIMIT ?", (after, attempt_store.CURSOR_PAGE))


def last_terminal_event_query(
    task_id: str, revision: int, digest: str,
) -> tuple[str, tuple[object, ...]]:
    """找一份提案最後一個終點事件的查詢語句(測試用它看查詢計畫);種類條件照抄部分索引的清單。"""
    return (f"SELECT {', '.join(_LIFECYCLE_FIELDS)} FROM lifecycle_events "  # noqa: S608 - 固定欄位與列舉值
            "WHERE task_id = ? AND revision = ? AND content_hash = ? "
            f"AND kind IN ({_TERMINAL_KIND_LIST}) ORDER BY id DESC LIMIT 1",
            (task_id, revision, digest))


def approval_uses_for_query(keys: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """一批冪等鍵的核可使用查詢語句(測試用它看查詢計畫)。依鍵與關卡的索引是代碼審第 1 輪加的:
    設計時怕它讓「已核可放行數」依廣告篩的查詢改走它,實測查詢計畫不變(那支查詢在接續條件上比到
    任務與修訂,唯一限制的索引比得到兩欄,優先於只比得到一欄的依鍵索引),Phase 6 釘住查詢計畫的
    測試照舊守著。"""
    marks = ", ".join("?" * len(keys))
    return (f"SELECT key, stage FROM approval_uses WHERE key IN ({marks})",  # noqa: S608 - 只拼佔位符
            tuple(keys))


def approval_use_count_query(
    *, tenant: str | None = None, campaign_id: str | None = None,
    since: datetime | None = None, until: datetime | None = None,
) -> tuple[str, tuple[object, ...]]:
    """已核可放行數的查詢語句(測試用它看查詢計畫)。用核可使用表的租戶、任務、修訂、操作鍵與時間;
    依廣告篩時用操作鍵接回這筆放行開始的那一列(第一列)取廣告——核可使用紀錄跟開始一筆寫在同一個
    交易,那一列一定在,而且只增不改。不接停下紀錄:核可先簽好就放行時沒有停下紀錄,等待期間換了
    租戶時停下紀錄留的是舊租戶(代碼審第 1 輪)。要比操作鍵、不能只比任務與修訂:收件表清掉已結案
    任務後,同一組任務與修訂可能再來一份內容不同的提案,舊內容的第一列還在(代碼審第 2 輪外家席)。
    寫成連接而不是子查詢:只依廣告篩時才能從「第一列按廣告」的索引出發,不整張掃核可使用表。"""
    join, where, params = "", ["1 = 1"], []
    if campaign_id is not None:
        join = ("JOIN attempts f ON f.key = u.key AND f.seq = 1 AND f.task_id = u.task_id "
                "AND f.revision = u.revision")
    for clause, value in (("u.tenant = ?", tenant), ("f.campaign_id = ?", campaign_id),
                          ("u.at >= ?", None if since is None else attempt_store.iso(since)),
                          ("u.at < ?", None if until is None else attempt_store.iso(until))):
        if value is not None:
            where.append(clause)
            params.append(value)
    return (f"SELECT count(*) FROM approval_uses u {join} WHERE {' AND '.join(where)}",  # noqa: S608 - 只拼接固定條件
            tuple(params))


def _event(row: tuple[object, ...]) -> LifecycleEvent:
    """事件表一列(欄位順序同 _LIFECYCLE_FIELDS)轉成事件;依既有結果確認存成 0/1,讀回布林。"""
    values = list(row)
    values[_LIFECYCLE_FIELDS.index("from_existing")] = bool(
        values[_LIFECYCLE_FIELDS.index("from_existing")])
    return LifecycleEvent(*values)  # type: ignore[arg-type]


def _lease_time(text: object, where: str) -> datetime:
    """處理中那一列的租約到期時間:要是帶時區的 ISO 時間字串,否則當這一列讀不懂。"""
    try:
        moment = datetime.fromisoformat(text)  # type: ignore[arg-type]  # 型別不對也在這裡攔
    except (TypeError, ValueError) as exc:
        raise CorruptedInboxRow(where) from exc
    if moment.tzinfo is None:
        raise CorruptedInboxRow(where)
    return moment


def _parse_payload(payload: str) -> Proposal | None:
    """存的內容讀不回提案就當沒有(不讓一列壞資料卡住全部)。"""
    try:
        return parse_proposal(json.loads(payload)).proposal
    except ValueError:
        return None


def _payload(proposal: Proposal) -> str:
    return json.dumps(proposal.to_primitives(), sort_keys=True, ensure_ascii=True)
