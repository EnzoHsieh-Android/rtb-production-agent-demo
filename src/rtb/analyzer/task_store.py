"""分析行程自己的 SQLite:任務只增不改的歷史表,加證據表與租約紀錄表。

「目前狀態」永遠是某個任務編號序號最大的那一列;沒有 UPDATE、沒有 DELETE。這樣
「這一步的輸出已安全存好」跟「這一列真的寫進資料庫」是同一件事,不需要另外的旗標。
新列的序號在寫入交易內用目前最大序號加一決定,並且核對「準備要接的那一列」仍是目前
最新的一列,不是就中止、不寫入——這是並行呼叫 advance() 時的最後一道防線。

租約(Phase 4 增量 3b):同一個任務同一時間只讓一個呼叫端去呼叫要花錢的外部介面。租約表
同樣只增不改,取得與放掉各新增一列,「目前的租約」就是這個任務租約序號最大的那一列;
提交時核對目前那一列正是自己的取得列(圍籬),過期被接手的舊持有者寫不進去。

唯讀開法(Phase 9 增量 1):`TaskReader` 只開唯讀連線、不建表不補欄位,一開就進一個不取鎖的快照,
直到關閉;缺表或缺欄位丟 DatabaseNotUpgraded。追蹤檢視用它讀,不跟分析行程搶寫入鎖。

模型說明的領取表與結果表(Phase 11B 增量 2,[S929]):模型說明命令列每次領取一列(自己的遞增領取序號;
任務、修訂、內容雜湊只當查詢欄位、不設唯一),寫結果時在同一個交易裡核對這張收據仍是這份提案最新的
領取、而且還沒有結果,舊持有者寫不進去。結果只增不改。說明不進任何決策、不進收件口,執行端不讀。

追加查詢的原始資料表(Phase 13 增量 2 開,[S1130] [S1151]):蒐證那一步每個追加查詢的原始回應一列
(正規化 JSON),只增不改,跟狀態列、證據在同一個交易、同一道租約與序號圍籬裡寫(提交函式的 `raw`)。
決策路徑只有規則輪定案讀它(同一輪 B/C 蒐證時白名單驗過、跟收據同交易寫的原始回應,Phase 14 增量 2b;
守衛測試只准 `rule_round` 讀);執行端與收件口都不讀([S1114])。

AI 調查紀錄表與模型呼叫記次表(Phase 13 增量 2):**Phase 14 增量 3 起沒有寫入路徑**(AI 退出加額決策,
流程層沒有 AI 那一步;代碼審 r1 鏡頭4 依「沒有入口就刪」拿掉提交函式的 `investigation` 參數、續租
`renew_lease` 與記次 `record_model_call`)。兩張表的建表敘述照留:舊資料庫裡寫過的列唯讀留著,
`investigation_rounds` 讀法給人追查舊資料;不改表結構,免得舊庫升級失敗。租約一律只有取得與放掉,
不續租。

規則輪(Phase 14 增量 2b,[S1405] [S1413]):正式規則分 A/B/C 三步讀四查詢。蒐證那一步把「這一列是第幾輪
的哪一步、哪個政策版本、讀取時刻」跟證據、原始回應同一個交易寫進規則步驟表;分析那一步把續步、重來、
定案寫進規則事件表(細因只存封閉代碼)。兩張都只增不改;進度一律由 `rule_round` 從這兩張的已提交列重算,
不持久化「下一步」。

接續任務(Phase 5):已交給執行的任務因版本已變、決策過期、或收件表已清掉而 DSP 沒有寫入時,
另開一個接續任務重讀現況再決定(任務狀態機不變,原任務轉擋下)。建接續任務、寫接續關係、原任務
結案是同一個交易,走原任務的提交圍籬;接續關係表同樣只增不改,原任務編號是主鍵(一個任務最多
接續一次)。
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from rtb.domain._checks import is_id, require_aware
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.domain.proposal import ActionType, Proposal
from rtb.domain.task_state import TERMINAL_STATES, IllegalTransition, TaskState, can_transition
from rtb.sqlitekit import (
    BUSY_TIMEOUT_SECONDS,
    DatabaseBusy,
    DatabaseNotUpgraded,
    begin_snapshot,
    connect,
    connect_read_only,
    end_snapshot,
    immediate_transaction,
    missing_schema,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, state TEXT NOT NULL,
    campaign_id TEXT NOT NULL, proposal_json TEXT, error_detail TEXT,
    written_at TEXT NOT NULL, PRIMARY KEY (task_id, seq));
CREATE TABLE IF NOT EXISTS evidence (
    task_id TEXT NOT NULL, task_seq INTEGER NOT NULL, evidence_id TEXT NOT NULL,
    kind TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL,
    campaign_version_observed INTEGER, content_hash TEXT NOT NULL, trust_class TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (task_id, task_seq, evidence_id));
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, task_seq INTEGER NOT NULL,
    endpoint TEXT NOT NULL, outcome TEXT NOT NULL, latency_ms REAL NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_leases (
    task_id TEXT NOT NULL, lease_seq INTEGER NOT NULL, owner TEXT, expires_at TEXT NOT NULL,
    PRIMARY KEY (task_id, lease_seq));
CREATE TABLE IF NOT EXISTS follow_ups (
    original_task_id TEXT PRIMARY KEY, follow_up_task_id TEXT UNIQUE,
    generation INTEGER NOT NULL, campaign_id TEXT NOT NULL, reason TEXT NOT NULL,
    outcome TEXT NOT NULL, written_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS no_action_reasons (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY (task_id, seq));
CREATE TABLE IF NOT EXISTS narrative_claims (
    claim_seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL, owner TEXT NOT NULL, claimed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS narrative_results (
    claim_seq INTEGER PRIMARY KEY REFERENCES narrative_claims (claim_seq), outcome TEXT NOT NULL,
    source TEXT, text TEXT, written_at TEXT NOT NULL, dropped INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS investigation_rounds (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, round INTEGER NOT NULL, kind TEXT NOT NULL,
    choice TEXT NOT NULL, decided_by TEXT NOT NULL, reason TEXT, fallback TEXT, reason_code TEXT,
    cited_json TEXT, model_source TEXT, written_at TEXT NOT NULL, PRIMARY KEY (task_id, seq));
CREATE TABLE IF NOT EXISTS investigation_calls (
    task_id TEXT NOT NULL, call_seq INTEGER NOT NULL, lease_seq INTEGER NOT NULL,
    written_at TEXT NOT NULL, PRIMARY KEY (task_id, call_seq));
CREATE TABLE IF NOT EXISTS investigation_raw (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, option TEXT NOT NULL, raw_json TEXT NOT NULL,
    stored_at TEXT NOT NULL, PRIMARY KEY (task_id, seq, option));
CREATE TABLE IF NOT EXISTS rule_steps (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, round_id INTEGER NOT NULL, step TEXT NOT NULL,
    policy_version TEXT NOT NULL, read_at TEXT NOT NULL, PRIMARY KEY (task_id, seq));
CREATE TABLE IF NOT EXISTS rule_events (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, round_id INTEGER NOT NULL, event TEXT NOT NULL,
    detail TEXT, written_at TEXT NOT NULL, PRIMARY KEY (task_id, seq));
CREATE INDEX IF NOT EXISTS narrative_claims_by_proposal
    ON narrative_claims (task_id, revision, content_hash);
CREATE INDEX IF NOT EXISTS follow_ups_by_campaign ON follow_ups (campaign_id);
CREATE INDEX IF NOT EXISTS tool_calls_by_time ON tool_calls (at);
"""
_TASK_COLUMNS = ("task_id, seq, state, campaign_id, proposal_json, error_detail, written_at, "
                 "operation_key")
MAX_ERROR_DETAIL_LENGTH = 2000  # error_detail 進永久不可刪改的表,長度必須有上限
# 暫用,沒有實測校準:要遠大於一步最慢的時間(規則輪 C 最多 5 次讀 DSP、基本那一步兩次、或一次
# 送件,各自的逾時由建用戶端的呼叫端決定);分析端驅動命令列(rtb.analyzer.runner)啟動時斷言這些
# 不等式([S1001] [S1405])
LEASE_DURATION = timedelta(seconds=60)
# 模型說明的領取期限(Phase 11B 增量 2):最新一次領取沒有結果、不到這麼久就跳過,超過才准再領。
# 說明命令列
# 啟動時斷言「模型呼叫的總期限加 1 分鐘餘裕小於它」,所以接手時前一個持有者的呼叫一定已經結束
NARRATIVE_CLAIM_WINDOW = timedelta(minutes=10)
# 同一份提案最多領幾次(代碼審 r1):每次領取至多一次模型呼叫。說明不計花費上限(Phase 13 裁定 13),
# 失敗又
# 准立刻再領,不設上限的話被名稱誘導出讀不懂的回答時每一趟都再付一次;跟 Phase 13 調查一件工作最多 3
# 輪
# 同一個數,夠吸收一兩次暫時性失敗
MAX_NARRATIVE_CLAIMS = 3
# 確定沒送出模型呼叫、沒花錢的結果類別:不算進領取上限(代碼審 r2:花費帳忙碌三次就永遠不再產生說明)。
# 還沒有結果的領取照算(持有者可能已經付了費才崩潰)
UNSENT_NARRATIVE_OUTCOMES = frozenset({"ledger_busy", "local_cap_refused", "config_error",
                                       "no_recording"})
# 接續任務的保留命名空間:一般建任務入口拒收這個開頭的任務編號,只有建接續任務寫得進去
FOLLOW_UP_PREFIX = "fu-"
_FOLLOW_UP_HASH_LENGTH = 24
# 整條接續鏈最多幾代(原任務是第 1 代):暫用,沒有真實衝突頻率數據(計劃 REVISIT 2026-12-31)
MAX_GENERATION = 3


class TaskAlreadyExists(Exception):
    """同一個任務編號已經存在,但這次帶的廣告編號不同:呼叫端的錯誤,不靜默接受。"""


class InvalidTaskId(Exception):
    """任務編號或廣告編號格式不合法。"""


class EvidenceTaskMismatch(Exception):
    """要附帶的證據不屬於這個任務:呼叫端的錯誤,不靜默記成這個任務的。"""


class CorruptedHistoryRow(Exception):
    """歷史表或證據表的一列讀不回來(格式毀損);重試沒有用,呼叫端應轉 FAILED。"""


class TaskStoreBusy(Exception):
    """建立連線時資料庫忙碌到逾時。"""


class TaskNotFound(Exception):
    """這個任務編號完全沒有歷史列。"""


class NarrativeOutcome(StrEnum):
    """一次模型說明的結果類別:成功,或模型用戶端的九類失敗之一(值跟花費帳的結果類別相同;分析端的資料庫
    模組不匯入模型用戶端,這份封閉列舉由測試核對兩邊一致)。"""

    OK = "ok"
    TIMEOUT = "timeout"
    LOCAL_CAP_REFUSED = "local_cap_refused"
    QUOTA_EXHAUSTED = "quota_exhausted"
    OVERRUN = "overrun"
    NO_RECORDING = "no_recording"
    UNREADABLE = "unreadable"
    CONFIG_ERROR = "config_error"
    TRANSIENT = "transient"
    LEDGER_BUSY = "ledger_busy"


@dataclass(frozen=True)
class NarrativeClaim:
    """領取收據:寫結果時要帶它,核對它仍是這份提案最新的領取。"""

    claim_seq: int
    task_id: str
    revision: int
    content_hash: str


@dataclass(frozen=True)
class NarrativeStatus:
    """一份提案的模型說明現況:有成功結果就是那一筆;沒有就是最新一次領取的結果類別(還在等結果是空值)。"""

    outcome: str | None
    text: str | None
    source: str | None
    dropped: int = 0  # 因數字對不回而沒顯示的句數(代碼審 r3:不靜默刪)


class ReplanReason(StrEnum):
    """為什麼要重新規劃;記進接續關係表,給衝突次數查詢分類。"""

    VERSION_CHANGED = "version_changed"  # 收件口回已擋下、原因是版本已變
    EXPIRED = "expired"  # 收件口回決策已過期(收件表還留著)
    AFTER_RETENTION = "after_retention"  # 收件表已清掉、DSP 用存下的鍵查不到寫入
    POLICY_VERSION_CHANGED = "policy_version_changed"  # 收件口回已擋下、原因是政策已變(Phase 8)
    DECISION_STALE = "decision_stale"  # 收件口回已擋下、原因是決策已過時(Phase 8)


class ToolEndpoint(StrEnum):
    """分析端對外呼叫的端點(Phase 9 增量 2,封閉列舉):會變成指標的標籤,不能收任意字串。

    記呼叫紀錄只收「其他」以外的成員;「其他」只給讀舊列用:改成列舉之前寫進去的舊列,值不在列舉裡
    的一律讀成它(只封住新寫入,舊列照樣會帶任意值進標籤)。"""

    DSP_CAMPAIGN = "dsp:campaign"  # 讀廣告現況
    DSP_METRICS = "dsp:metrics"  # 讀成效指標
    DSP_EVIDENCE = "dsp:evidence"  # 整包證據來源(包一層只記一筆;那支包裝已刪,留著讀舊列)
    DSP_OPERATION = "dsp:operation"  # 依冪等鍵查 DSP 操作
    DSP_HISTORY = "dsp:history"  # AI 追加查詢:讀操作歷史(Phase 13)
    DSP_DAILY = "dsp:daily"  # AI 追加查詢:讀逐日成效(Phase 13)
    DSP_ADJUSTMENTS = "dsp:adjustments"  # AI 追加查詢:讀過去調整(Phase 13)
    INBOX_SUBMIT = "inbox:submit"  # 送出提案到收件口
    OTHER = "other"


def _endpoint(raw: str) -> ToolEndpoint:
    try:
        return ToolEndpoint(raw)
    except ValueError:
        return ToolEndpoint.OTHER


def tool_calls_between_query(since: datetime, until: datetime) -> tuple[str, tuple[str, ...]]:
    """時間窗內對外呼叫的查詢語句(測試用它看查詢計畫)。"""
    return ("SELECT task_id, task_seq, endpoint, outcome, latency_ms, at FROM tool_calls "
            "WHERE at >= ? AND at < ? ORDER BY at, id", (_iso(since), _iso(until)))


CURSOR_PAGE = 1000  # 游標式讀取一次最多回幾列;呼叫端讀到不滿一頁才算讀完


def tasks_after_query(after: int) -> tuple[str, tuple[int, ...]]:
    """列號大於 after 的任務歷史列(Phase 12 代碼審 r2 a1:展示觀察器照列號往後讀,不自己下查詢)。"""
    return (f"SELECT rowid, {_TASK_COLUMNS} FROM tasks WHERE rowid > ? "  # noqa: S608 - 固定欄位清單
            "ORDER BY rowid LIMIT ?", (after, CURSOR_PAGE))


def follow_ups_after_query(after: int) -> tuple[str, tuple[int, ...]]:
    """列號大於 after 的接續關係(同上)。"""
    return ("SELECT rowid, original_task_id, follow_up_task_id, reason, written_at, generation "
            "FROM follow_ups WHERE rowid > ? ORDER BY rowid LIMIT ?", (after, CURSOR_PAGE))


@dataclass(frozen=True)
class FollowUpRow:
    """一列接續關係:原任務結案時寫;代數用完時沒有開新工作,接續任務是空值。"""

    rowid: int
    original_task_id: str
    follow_up_task_id: str | None
    reason: ReplanReason
    written_at: datetime
    generation: int | None = None  # 接續任務是第幾代(代數用完那一列記的是超過上限的那一代)


class _FollowUpOutcome(StrEnum):
    CREATED = "created"
    LIMIT_REACHED = "limit_reached"


@dataclass(frozen=True)
class FollowUp:
    """commit_step 帶這個:原任務結案的同一個交易裡建接續任務(或記下代數用完)。"""

    reason: ReplanReason


@dataclass(frozen=True)
class ReplanCounts:
    replanned: int  # 建了接續任務的次數
    exhausted: int  # 需要重新規劃但代數用完、只結案的次數
    after_retention: int  # 其中因收件表已清掉而重新規劃的次數


def follow_up_id(task_id: str) -> str:
    """接續任務編號:由原任務編號算出、固定長度,不管原編號多長都不會超過識別碼上限。"""
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return FOLLOW_UP_PREFIX + digest[:_FOLLOW_UP_HASH_LENGTH]


@dataclass(frozen=True)
class ToolCall:
    task_id: str
    task_seq: int
    endpoint: ToolEndpoint
    outcome: str
    latency_ms: float
    at: datetime


@dataclass(frozen=True)
class TraceRecord:
    """給定一個任務編號的完整軌跡:狀態史、每一步的證據、每一次對外呼叫。"""

    tasks: tuple[TaskRow, ...]
    evidence: tuple[Evidence, ...]
    tool_calls: tuple[ToolCall, ...]
    follow_up_of: str | None = None  # 這個任務是接續哪個任務的
    follow_up_to: str | None = None  # 這個任務接續到哪個任務


@dataclass(frozen=True)
class LeaseReceipt:
    """取得租約時拿到的收據:之後提交與放掉都要帶它,核對目前那一列還是這張收據的取得列。"""

    task_id: str
    lease_seq: int
    owner: str


@dataclass(frozen=True)
class InvestigationRecord:
    """AI 那一步的一輪調查紀錄(Phase 13 增量 2)。欄位是字串,詞彙(選項代碼、退回原因)由分析端的調查
    模組定;資料庫模組只存,不解讀。理由是模型產生的不可信文字:只存、只顯示。"""

    kind: str  # query(選查詢)、conclusion(下結論)、fallback(退回程式規則)
    round: int  # 這件工作的第幾輪;退回時記的是這一輪原本會是第幾輪
    choice: str  # 查詢代碼以逗號串起、結論代碼,或退回時規則的結果
    decided_by: str  # ai 或 rule
    reason: str | None = None
    fallback: str | None = None
    reason_code: str | None = None  # 選查詢那一列帶 ai_query(展示觀察器分辨回頭節點)
    cited_json: str | None = None  # AI 引用的收據值(已核對存在)
    model_source: str | None = None  # recorded 或 live


@dataclass(frozen=True)
class RawQuery:
    """一個追加查詢的原始回應(正規化 JSON):跟證據同一個交易寫進調查原始資料表。"""

    option: str
    raw_json: str


@dataclass(frozen=True)
class RuleStep:
    """規則輪一步的蒐證紀錄(Phase 14 增量 2b):輪次、步驟代碼、政策版本、讀取時刻;序號是那一步蒐證
    寫下的分析中那一列。詞彙(步驟代碼)由 `rule_round` 定,這裡只存。"""

    round_id: int
    step: str
    policy_version: str
    read_at: datetime


@dataclass(frozen=True)
class RuleEvent:
    """規則輪的事件(續步、重來、定案):分析那一步寫,序號是它寫下的那一列;detail 是封閉的原因代碼。"""

    round_id: int
    event: str
    detail: str | None = None




@dataclass(frozen=True)
class TaskRow:
    task_id: str
    seq: int
    state: TaskState
    campaign_id: str
    proposal: Proposal | None
    error_detail: str | None
    written_at: datetime


def _tool_call(row: tuple[Any, ...]) -> ToolCall:
    return ToolCall(task_id=row[0], task_seq=row[1], endpoint=_endpoint(row[2]), outcome=row[3],
                    latency_ms=row[4], at=datetime.fromisoformat(row[5].replace("Z", "+00:00")))


def _iso(moment: datetime) -> str:
    """沒帶時區就拒絕(領域層共用的檢查):不然會被當成本機時間換算,窗界整段位移、悄悄漏資料
    (Phase 9 增量 2 代碼審第 1 輪)。"""
    require_aware(moment)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_live(lease: tuple[int, str | None, str], now: datetime) -> bool:
    """有擁有者而且還沒到期;到期時間與現在都是同一種固定格式的 UTC 字串,可以直接比大小。"""
    return lease[1] is not None and lease[2] > _iso(now)


def _proposal_to_json(proposal: Proposal) -> str:
    return json.dumps(proposal.to_primitives(), sort_keys=True, ensure_ascii=True)


def _proposal_from_json(raw: str) -> Proposal:
    data = json.loads(raw)
    return Proposal(
        task_id=data["task_id"], revision=data["revision"], campaign_id=data["campaign_id"],
        action_type=ActionType(data["action_type"]),
        requested_change=MappingProxyType(data["requested_change"]),
        reason_codes=tuple(data["reason_codes"]),
        evidence_refs=tuple(data["evidence_refs"]),
        campaign_version_observed=data["campaign_version_observed"],
        decision_created_at=datetime.fromisoformat(data["decision_created_at"]),
        decision_expires_at=datetime.fromisoformat(data["decision_expires_at"]),
        policy_version=data["policy_version"], risk_summary=data["risk_summary"],
    )


def _row_from_record(
    record: tuple[str, int, str, str, str | None, str | None, str, str | None],
) -> TaskRow:
    task_id, seq, state, campaign_id, proposal_json, error_detail, written_at, _key = record
    try:
        stamp = datetime.fromisoformat(written_at.replace("Z", "+00:00"))
        proposal = None if proposal_json is None else _proposal_from_json(proposal_json)
        state_enum = TaskState(state)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise CorruptedHistoryRow(f"{task_id} 第 {seq} 列讀不回來:{exc!r}") from exc
    return TaskRow(
        task_id=task_id, seq=seq, state=state_enum, campaign_id=campaign_id,
        proposal=proposal, error_detail=error_detail, written_at=stamp,
    )


class TaskReads:
    """任務歷史模組的讀取方法(Phase 9 增量 1):寫入開法(TaskStore)與唯讀開法(TaskReader)共用。
    讀取方法都只有 SELECT;唯讀開法開出來的物件一開就進一個快照,所有讀取自動落在那個快照裡。"""

    _conn: sqlite3.Connection

    def latest(self, task_id: str) -> TaskRow | None:
        record = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE task_id = ? ORDER BY seq DESC LIMIT 1",  # noqa: S608 - 固定欄位清單
            (task_id,),
        ).fetchone()
        return None if record is None else _row_from_record(record)

    def operation_key_for(self, task_id: str) -> str | None:
        """交給執行那一列存下的冪等鍵;Phase 5 之前寫的列沒有存,回 None。"""
        record = self._conn.execute(
            "SELECT operation_key FROM tasks WHERE task_id = ? AND state = ? "
            "ORDER BY seq DESC LIMIT 1", (task_id, TaskState.HANDED_OFF.value),
        ).fetchone()
        return None if record is None else record[0]

    def follow_up_to(self, task_id: str) -> str | None:
        record = self._conn.execute(
            "SELECT follow_up_task_id FROM follow_ups WHERE original_task_id = ?", (task_id,),
        ).fetchone()
        return None if record is None else record[0]

    def follow_up_of(self, task_id: str) -> str | None:
        record = self._conn.execute(
            "SELECT original_task_id FROM follow_ups WHERE follow_up_task_id = ?", (task_id,),
        ).fetchone()
        return None if record is None else record[0]

    def evidence_for(self, task_id: str, task_seq: int) -> tuple[Evidence, ...]:
        rows = self._conn.execute(
            "SELECT evidence_id, kind, source, observed_at, campaign_version_observed, "
            "content_hash, trust_class, payload_json FROM evidence "
            "WHERE task_id = ? AND task_seq = ? "
            "ORDER BY rowid",  # rowid 保留寫入順序;evidence_id 是呼叫端給的,字母序不等於蒐證順序
            (task_id, task_seq),
        ).fetchall()
        try:
            return tuple(
                Evidence(
                    evidence_id=r[0], task_id=task_id, kind=EvidenceKind(r[1]), source=r[2],
                    observed_at=datetime.fromisoformat(r[3].replace("Z", "+00:00")),
                    campaign_version_observed=r[4], content_hash=r[5], trust_class=TrustClass(r[6]),
                    payload=MappingProxyType(json.loads(r[7])),
                )
                for r in rows
            )
        except (ValueError, KeyError) as exc:
            raise CorruptedHistoryRow(f"{task_id} 第 {task_seq} 步的證據讀不回來:{exc!r}") from exc

    def list_tool_calls(self, task_id: str) -> tuple[ToolCall, ...]:
        rows = self._conn.execute(
            "SELECT task_id, task_seq, endpoint, outcome, latency_ms, at FROM tool_calls "
            "WHERE task_id = ? ORDER BY id", (task_id,),
        ).fetchall()
        return tuple(_tool_call(r) for r in rows)

    def tool_calls_between(self, since: datetime, until: datetime) -> tuple[ToolCall, ...]:
        """時間窗內的對外呼叫(含起點、不含終點),依時間;走時間索引(Phase 9 增量 2 的指標用)。"""
        sql, params = tool_calls_between_query(since, until)
        return tuple(_tool_call(row) for row in self._conn.execute(sql, params))

    def no_action_reason(self, task_id: str, seq: int) -> str | None:
        """不提案那一列存下的原因(Phase 12 設計審 r2 p1);沒存、或資料庫還沒有原因表都回空值。
        唯讀開法不建表,所以先看表在不在:Phase 12 之前的資料庫照樣開得起來。"""
        if self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                              "AND name = 'no_action_reasons'").fetchone() is None:
            return None
        record = self._conn.execute(
            "SELECT reason FROM no_action_reasons WHERE task_id = ? AND seq = ?", (task_id, seq),
        ).fetchone()
        return None if record is None else str(record[0])

    def _has_table(self, name: str) -> bool:
        return self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                                  (name,)).fetchone() is not None

    def investigation_rounds(self, task_id: str) -> tuple[tuple[int, InvestigationRecord], ...]:
        """這件工作已提交的調查紀錄(序號, 紀錄),依序號;唯讀開法遇到還沒有這張表的舊庫回空的。
        Phase 14 增量 3 起沒有新寫入,只讀舊資料庫留下的列(給人追查)。"""
        if not self._has_table("investigation_rounds"):
            return ()
        rows = self._conn.execute(
            "SELECT seq, kind, round, choice, decided_by, reason, fallback, reason_code, "
            "cited_json, model_source FROM investigation_rounds WHERE task_id = ? ORDER BY seq",
            (task_id,)).fetchall()
        return tuple((int(r[0]), InvestigationRecord(*r[1:])) for r in rows)

    def raw_query(self, task_id: str, seq: int, option: str) -> str | None:
        """用(任務、那一輪分析列的序號、選項代碼)取回原始回應;那一輪沒有結果就是空值。給人看,
        以及規則輪定案讀同一輪的原始回應(Phase 14)。"""
        if not self._has_table("investigation_raw"):
            return None
        record = self._conn.execute(
            "SELECT raw_json FROM investigation_raw WHERE task_id = ? AND seq = ? AND option = ?",
            (task_id, seq, option)).fetchone()
        return None if record is None else str(record[0])

    def rule_steps(self, task_id: str) -> tuple[tuple[int, RuleStep], ...]:
        """這件工作已提交的規則輪步驟(序號, 紀錄),依序號;唯讀開法遇到還沒有這張表的舊庫回空的。"""
        if not self._has_table("rule_steps"):
            return ()
        rows = self._conn.execute(
            "SELECT seq, round_id, step, policy_version, read_at FROM rule_steps "
            "WHERE task_id = ? ORDER BY seq", (task_id,)).fetchall()
        try:
            return tuple((int(r[0]), RuleStep(int(r[1]), str(r[2]), str(r[3]),
                                              datetime.fromisoformat(r[4].replace("Z", "+00:00"))))
                         for r in rows)
        except (ValueError, TypeError) as exc:
            raise CorruptedHistoryRow(f"{task_id} 的規則輪步驟讀不回來:{exc!r}") from exc

    def rule_events(self, task_id: str) -> tuple[tuple[int, RuleEvent], ...]:
        """這件工作已提交的規則輪事件(序號, 紀錄),依序號;表不在回空的。"""
        if not self._has_table("rule_events"):
            return ()
        rows = self._conn.execute(
            "SELECT seq, round_id, event, detail FROM rule_events WHERE task_id = ? ORDER BY seq",
            (task_id,)).fetchall()
        try:
            return tuple((int(r[0]), RuleEvent(int(r[1]), str(r[2]), r[3])) for r in rows)
        except (ValueError, TypeError) as exc:  # 同步驟表:讀不回來就是毀損(代碼審 r2 鏡頭2-1)
            raise CorruptedHistoryRow(f"{task_id} 的規則輪事件讀不回來:{exc!r}") from exc

    def handed_off_rows(self) -> tuple[TaskRow, ...]:
        """已送進收件口的每一列(交給執行那幾列,帶提案),依寫入順序;模型說明命令列從這裡找要寫說明的
        提案。"""
        records = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE state = ? AND proposal_json IS NOT NULL "  # noqa: S608 - 固定欄位清單
            "ORDER BY rowid", (TaskState.HANDED_OFF.value,)).fetchall()
        return tuple(_row_from_record(r) for r in records)

    def narrative_for(self, task_id: str, revision: int,
                      content_hash: str) -> NarrativeStatus | None:
        """一份提案的模型說明現況;沒有任何領取回 None(顯示「尚未產生」)。唯讀開法不建表:資料庫還沒有
        說明表(Phase 11B 增量 2 之前)也當沒有領取。"""
        if self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                              "AND name = 'narrative_results'").fetchone() is None:
            return None
        rows = self._conn.execute(
            "SELECT r.outcome, r.text, r.source, coalesce(r.dropped, 0) FROM narrative_claims c "
            "LEFT JOIN narrative_results r ON r.claim_seq = c.claim_seq "
            "WHERE c.task_id = ? AND c.revision = ? AND c.content_hash = ? ORDER BY c.claim_seq",
            (task_id, revision, content_hash)).fetchall()
        if not rows:
            return None
        success = next((r for r in rows if r[0] == NarrativeOutcome.OK.value), None)
        outcome, text, source, dropped = success if success is not None else rows[-1]
        return NarrativeStatus(outcome, text, source, int(dropped))

    def tasks_after(self, after: int) -> tuple[tuple[int, TaskRow], ...]:
        """列號大於 after 的任務歷史列,依寫入順序,最多一頁:(列號, 那一列)。"""
        sql, params = tasks_after_query(after)
        return tuple((int(r[0]), _row_from_record(r[1:])) for r in self._conn.execute(sql, params))

    def follow_ups_after(self, after: int) -> tuple[FollowUpRow, ...]:
        """列號大於 after 的接續關係,依寫入順序,最多一頁。"""
        sql, params = follow_ups_after_query(after)
        return tuple(FollowUpRow(int(r[0]), r[1], r[2], ReplanReason(r[3]),
                                 datetime.fromisoformat(r[4].replace("Z", "+00:00")), int(r[5]))
                     for r in self._conn.execute(sql, params))

    def history(self, task_id: str) -> tuple[TaskRow, ...]:
        records = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE task_id = ? ORDER BY seq",  # noqa: S608 - 固定欄位清單
            (task_id,),
        ).fetchall()
        return tuple(_row_from_record(r) for r in records)

    def open_task_ids(self) -> tuple[str, ...]:
        """最新一列還不是終點狀態的任務,依任務編號排序(Phase 12 分析端驅動命令列每一輪要推進的)。"""
        rows = self._conn.execute(
            "SELECT t.task_id, t.state FROM tasks t WHERE t.seq = "
            "(SELECT max(seq) FROM tasks WHERE task_id = t.task_id) ORDER BY t.task_id",
        ).fetchall()
        return tuple(task_id for task_id, state in rows
                     if TaskState(state) not in TERMINAL_STATES)

    def handed_off_keys(self, task_id: str) -> tuple[tuple[int, str | None], ...]:
        """交給執行那幾列的(序號, 存下的冪等鍵);Phase 5 之前寫的列沒有存鍵,是空值。"""
        return tuple((int(seq), key) for seq, key in self._conn.execute(
            "SELECT seq, operation_key FROM tasks WHERE task_id = ? AND state = ? ORDER BY seq",
            (task_id, TaskState.HANDED_OFF.value)))


class TaskStore(TaskReads):
    def __init__(self, path: Path, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS):
        try:
            self._conn = connect(path, busy_timeout_seconds, SCHEMA)
        except DatabaseBusy as exc:
            raise TaskStoreBusy(str(exc)) from exc
        try:
            self._migrate_evidence_payload_column()
            self._migrate_operation_key_column()
        except DatabaseBusy as exc:
            self._conn.close()
            raise TaskStoreBusy(str(exc)) from exc
        except BaseException:
            self._conn.close()  # 補欄位失敗時不留下沒人關的連線(三支資料庫模組同一寫法)
            raise

    def _migrate_evidence_payload_column(self) -> None:
        """`CREATE TABLE IF NOT EXISTS` 不會幫既有表補欄位:增量 3 建立的舊資料庫只有九欄,
        沒有增量 4 才加的 `payload_json`。每次連線都檢查一次,缺欄位就補上;舊列補
        `'{}'`(誠實反映「這些舊證據沒有留下原始數值,只有雜湊」,不是編造資料)。
        """
        self._add_column_if_missing(
            "evidence", "payload_json",
            "ALTER TABLE evidence ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")

    def _migrate_operation_key_column(self) -> None:
        """Phase 5 在任務表加「交接時用的冪等鍵」;舊資料庫補欄位,舊列照實留空值。"""
        self._add_column_if_missing(
            "tasks", "operation_key", "ALTER TABLE tasks ADD COLUMN operation_key TEXT")

    def _add_column_if_missing(self, table: str, column: str, ddl: str) -> None:
        """先不拿鎖看一次(已補過就不必搶寫入鎖),缺欄位才拿寫入鎖、拿到之後再看一次:多個工作者
        同時開舊資料庫時,另一個可能剛補完(Phase 5 代碼審第 3 輪外家席)。"""
        if column in self._columns(table):
            return
        with immediate_transaction(self._conn):
            if column not in self._columns(table):
                self._conn.execute(ddl)

    def _columns(self, table: str) -> set[str]:
        return {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}

    def close(self) -> None:
        self._conn.close()

    def create_task(self, task_id: str, campaign_id: str, now: datetime) -> None:
        if not is_id(task_id) or not is_id(campaign_id):
            raise InvalidTaskId(f"任務編號或廣告編號格式不合法:{task_id!r}, {campaign_id!r}")
        if task_id.startswith(FOLLOW_UP_PREFIX):  # 只管任務編號這一個參數,共用格式檢查不動
            raise InvalidTaskId(f"{FOLLOW_UP_PREFIX} 開頭的任務編號保留給接續任務:{task_id!r}")
        with immediate_transaction(self._conn):
            existing = self._conn.execute(
                "SELECT campaign_id FROM tasks WHERE task_id = ? ORDER BY seq DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != campaign_id:
                    raise TaskAlreadyExists(
                        f"{task_id} 已存在,廣告編號是 {existing[0]},不是 {campaign_id}")
                return
            self._insert_first_row(task_id, campaign_id, now)

    def _insert_first_row(self, task_id: str, campaign_id: str, now: datetime) -> None:
        self._conn.execute(
            f"INSERT INTO tasks ({_TASK_COLUMNS}) VALUES (?, 1, ?, ?, NULL, NULL, ?, NULL)",  # noqa: S608 - 固定欄位清單
            (task_id, TaskState.RECEIVED.value, campaign_id, _iso(now)),
        )

    def commit_step(  # noqa: PLR0913 - 每個關鍵字參數都對應計劃裡不同狀態要附帶的資料
        self,
        task_id: str,
        expected_seq: int,
        new_state: TaskState,
        now: datetime,
        *,
        evidence: Sequence[Evidence] = (),
        proposal: Proposal | None = None,
        error_detail: str | None = None,
        before_commit: Callable[[], None] | None = None,
        lease: LeaseReceipt | None = None,
        operation_key: str | None = None,
        follow_up: FollowUp | None = None,
        no_action_reason: StrEnum | None = None,
        raw: Sequence[RawQuery] = (),
        rule_step: RuleStep | None = None,
        rule_event: RuleEvent | None = None,
    ) -> bool:
        """核對租約與 expected_seq 仍是目前最新一列,新增一列 new_state 並(可選)附帶證據列。

        帶收據:目前那一列租約必須正是這張收據的取得列,寫入成功就在同一個交易裡放掉租約;
        只核對號碼不核對到期時間(時間只有呼叫端步驟開頭傳進來的那個,判不出自己過期沒有;
        過期但沒人接手時寫進去也沒有第二個人花過錢)。不帶收據:別人持有有效租約就不寫,
        往後忘了帶收據的呼叫端也蓋不過正在花錢的持有者。
        回傳是否真的寫入了;False 表示輸給並行的另一次呼叫,這次呼叫沒有寫入任何東西。

        帶 follow_up:原任務這一列(結案)跟接續任務、接續關係在同一個交易裡寫,同一道圍籬;
        錯誤說明後面補上接續任務編號、代數用完、或接續編號衝突(防線,不建立)。

        帶 no_action_reason:只准跟不提案那一列一起寫,同一個交易寫進只增的原因表(Phase 12)。

        帶 raw(Phase 13 增量 2):追加查詢的原始回應,序號是新的那一列;輸給接手者時連它一起沒寫。
        (調查紀錄的 `investigation` 參數隨 Phase 14 增量 3 撤除,見檔頭。)

        帶 rule_step / rule_event(Phase 14 增量 2b):規則輪這一步的蒐證紀錄(只准跟分析中那一列一起寫)
        、
        分析那一步的續步/重來/定案事件;同一個交易、同一道圍籬。
        """
        if rule_step is not None and new_state is not TaskState.ANALYZING:
            raise ValueError(f"規則輪步驟只能跟分析中那一列一起寫,這一步是 {new_state}")
        if no_action_reason is not None and new_state is not TaskState.NO_ACTION:
            raise ValueError(f"原因只能跟不提案那一列一起寫,這一步是 {new_state}")
        mismatched = [item.task_id for item in evidence if item.task_id != task_id]
        if mismatched:
            raise EvidenceTaskMismatch(
                f"要附帶的證據裡有 {mismatched} 不屬於任務 {task_id}")
        capped_detail = (
            None if error_detail is None else error_detail[:MAX_ERROR_DETAIL_LENGTH])
        with immediate_transaction(self._conn):
            if not self._lease_allows(task_id, lease, now):
                return False
            row = self._conn.execute(
                "SELECT seq, state, campaign_id FROM tasks WHERE task_id = ? "
                "ORDER BY seq DESC LIMIT 1", (task_id,),
            ).fetchone()
            if row is None or row[0] != expected_seq:
                return False
            current_state, campaign_id = TaskState(row[1]), row[2]
            if not can_transition(current_state, new_state):
                raise IllegalTransition(f"不合法的轉換:{current_state} -> {new_state}")
            next_seq = expected_seq + 1
            if follow_up is not None:
                note = self._write_follow_up(task_id, campaign_id, follow_up.reason, now)
                capped_detail = f"{error_detail or ''};{note}"[:MAX_ERROR_DETAIL_LENGTH]
            self._conn.execute(
                f"INSERT INTO tasks ({_TASK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608 - 固定欄位清單
                (task_id, next_seq, new_state.value, campaign_id,
                 None if proposal is None else _proposal_to_json(proposal),
                 capped_detail, _iso(now), operation_key),
            )
            self._insert_attachments(task_id, next_seq, evidence, no_action_reason)
            self._insert_raw(task_id, next_seq, raw, now)
            self._insert_rule(task_id, next_seq, rule_step, rule_event, now)
            if lease is not None:
                self._append_release(lease, now)
            if before_commit is not None:
                before_commit()
        return True

    def _insert_attachments(self, task_id: str, seq: int, evidence: Sequence[Evidence],
                            no_action_reason: StrEnum | None) -> None:
        """跟新的一列同一個交易寫的附帶資料:證據列,與不提案的原因。"""
        if no_action_reason is not None:
            self._conn.execute("INSERT INTO no_action_reasons VALUES (?, ?, ?)",
                               (task_id, seq, no_action_reason.value))
        for item in evidence:
            self._conn.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, seq, item.evidence_id, item.kind.value, item.source,
                 _iso(item.observed_at), item.campaign_version_observed,
                 item.content_hash, item.trust_class.value,
                 json.dumps(dict(item.payload), sort_keys=True, ensure_ascii=True,
                            allow_nan=False)),
            )

    def _insert_raw(self, task_id: str, seq: int, raw: Sequence[RawQuery], now: datetime) -> None:
        for item in raw:
            self._conn.execute("INSERT INTO investigation_raw VALUES (?, ?, ?, ?, ?)",
                               (task_id, seq, item.option, item.raw_json, _iso(now)))

    def _insert_rule(self, task_id: str, seq: int, step: RuleStep | None,
                     event: RuleEvent | None, now: datetime) -> None:
        if step is not None:
            if type(step.round_id) is not int or step.round_id < 1:
                raise ValueError("規則輪的輪次要是正整數")
            self._conn.execute("INSERT INTO rule_steps VALUES (?, ?, ?, ?, ?, ?)",
                               (task_id, seq, step.round_id, step.step, step.policy_version,
                                _iso(step.read_at)))
        if event is not None:
            if type(event.round_id) is not int or event.round_id < 0:
                raise ValueError("規則輪事件的輪次要是非負整數")
            self._conn.execute("INSERT INTO rule_events VALUES (?, ?, ?, ?, ?, ?)",
                               (task_id, seq, event.round_id, event.event, event.detail,
                                _iso(now)))

    def _write_follow_up(
        self, task_id: str, campaign_id: str, reason: ReplanReason, now: datetime,
    ) -> str:
        """在呼叫端的交易裡建接續任務與接續關係;回傳要補進原任務錯誤說明的一段話。"""
        head = f"replan={reason.value}"
        link = self._conn.execute(
            "SELECT follow_up_task_id FROM follow_ups WHERE original_task_id = ?", (task_id,),
        ).fetchone()
        if link is not None:  # 防線:同一個交易寫原任務結案,正常不會已有關係;沿用、不再寫
            return f"{head};follow_up={link[0] or 'none'}"
        parent = self._conn.execute(
            "SELECT generation FROM follow_ups WHERE follow_up_task_id = ?", (task_id,),
        ).fetchone()
        generation = 2 if parent is None else int(parent[0]) + 1
        child = follow_up_id(task_id)
        if not is_id(child):  # 格式檢查照做,只跳過一般入口的保留命名空間檢查
            raise InvalidTaskId(f"接續任務編號格式不合法:{child!r}")
        if generation > MAX_GENERATION:
            outcome, recorded_child, note = (
                _FollowUpOutcome.LIMIT_REACHED, None, f"replan_limit_reached={MAX_GENERATION}")
        elif self._conn.execute(
                "SELECT 1 FROM tasks WHERE task_id = ? LIMIT 1", (child,)).fetchone() is not None:
            return f"{head};follow_up_id_collision={child}"  # 防線:不建立、不記關係
        else:
            self._insert_first_row(child, campaign_id, now)
            outcome, recorded_child, note = _FollowUpOutcome.CREATED, child, f"follow_up={child}"
        self._conn.execute(
            "INSERT INTO follow_ups VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, recorded_child, generation, campaign_id, reason.value, outcome.value,
             _iso(now)),
        )
        return f"{head};{note}"

    def claim_narrative(self, task_id: str, revision: int, content_hash: str, *, owner: str,
                        now: datetime) -> NarrativeClaim | None:
        """在一個寫入交易裡查與寫([S929]):已有成功結果不再領;已領滿 MAX_NARRATIVE_CLAIMS 次不再領;
        最新一次領取有結果而且是失敗就立刻再領;
        最新一次領取沒有結果、不到領取期限就跳過(回 None,不呼叫模型);超過期限才再領。"""
        with immediate_transaction(self._conn):
            rows = self._conn.execute(
                "SELECT c.claim_seq, c.claimed_at, r.outcome FROM narrative_claims c "
                "LEFT JOIN narrative_results r ON r.claim_seq = c.claim_seq "
                "WHERE c.task_id = ? AND c.revision = ? AND c.content_hash = ? "
                "ORDER BY c.claim_seq", (task_id, revision, content_hash)).fetchall()
            if any(r[2] == NarrativeOutcome.OK.value for r in rows):
                return None
            sent = sum(1 for r in rows if r[2] not in UNSENT_NARRATIVE_OUTCOMES)
            if sent >= MAX_NARRATIVE_CLAIMS:
                return None
            if rows and rows[-1][2] is None and rows[-1][1] > _iso(now - NARRATIVE_CLAIM_WINDOW):
                return None
            cursor = self._conn.execute(
                "INSERT INTO narrative_claims (task_id, revision, content_hash, owner, claimed_at) "
                "VALUES (?, ?, ?, ?, ?)", (task_id, revision, content_hash, owner, _iso(now)))
            if cursor.lastrowid is None:
                raise AssertionError("新增領取列沒有拿到序號")
            return NarrativeClaim(cursor.lastrowid, task_id, revision, content_hash)

    def narrative_claim_count(self, task_id: str, revision: int, content_hash: str) -> int:
        """這份提案算進領取上限的領取次數(確定沒送出的不算;領滿 MAX_NARRATIVE_CLAIMS 次就
        不再領)。"""
        rows = self._conn.execute(
            "SELECT r.outcome FROM narrative_claims c "
            "LEFT JOIN narrative_results r ON r.claim_seq = c.claim_seq "
            "WHERE c.task_id = ? AND c.revision = ? AND c.content_hash = ?",
            (task_id, revision, content_hash)).fetchall()
        return sum(1 for (outcome,) in rows if outcome not in UNSENT_NARRATIVE_OUTCOMES)

    def has_narrative(self, task_id: str, revision: int, content_hash: str) -> bool:
        """這份提案已經有成功的說明。"""
        return self._conn.execute(
            "SELECT 1 FROM narrative_claims c "
            "JOIN narrative_results r ON r.claim_seq = c.claim_seq "
            "WHERE c.task_id = ? AND c.revision = ? AND c.content_hash = ? AND r.outcome = ? "
            "LIMIT 1", (task_id, revision, content_hash, NarrativeOutcome.OK.value),
        ).fetchone() is not None

    def record_narrative(self, claim: NarrativeClaim, outcome: NarrativeOutcome, *,
                         text: str | None, source: str | None, now: datetime,
                         dropped: int = 0) -> bool:
        """追加一筆結果;同一個交易裡核對這張收據仍是這份提案最新的領取、而且還沒有結果,不是就不寫
        (被接手的舊持有者寫不進去)。成功一定帶文字、失敗一定不帶。回傳有沒有寫進去。"""
        if type(outcome) is not NarrativeOutcome:
            raise ValueError("結果類別必須是 NarrativeOutcome 的成員")
        if (outcome is NarrativeOutcome.OK) != (text is not None):
            raise ValueError("成功一定帶說明文字、失敗一定不帶")
        with immediate_transaction(self._conn):
            latest = self._conn.execute(
                "SELECT max(claim_seq) FROM narrative_claims "
                "WHERE task_id = ? AND revision = ? AND content_hash = ?",
                (claim.task_id, claim.revision, claim.content_hash)).fetchone()
            if latest is None or latest[0] != claim.claim_seq:
                return False
            if self._conn.execute("SELECT 1 FROM narrative_results WHERE claim_seq = ?",
                                  (claim.claim_seq,)).fetchone() is not None:
                return False
            self._conn.execute("INSERT INTO narrative_results VALUES (?, ?, ?, ?, ?, ?)",
                               (claim.claim_seq, outcome.value, source, text, _iso(now), dropped))
        return True

    def acquire_lease(self, task_id: str, owner: str, now: datetime) -> LeaseReceipt | None:
        """目前沒人持有(沒有租約列、目前那一列是放掉列、或已過期)就新增一列取得列、回傳收據;
        有人持有就什麼都不寫、回傳 None;沒有這個任務丟 TaskNotFound,不替它寫租約列。

        命名對照執行側收件表:取得對應 `_lease`/`take_over`,放掉對應 `release`。不續租(Phase 13 的
        續租隨 AI 那一步在 Phase 14 增量 3 撤除);一次推進只做一步。"""
        with immediate_transaction(self._conn):
            if self._conn.execute(
                    "SELECT 1 FROM tasks WHERE task_id = ? LIMIT 1", (task_id,)).fetchone() is None:
                raise TaskNotFound(task_id)
            current = self._current_lease(task_id)
            if current is not None and _is_live(current, now):
                return None
            next_seq = 1 if current is None else current[0] + 1
            self._conn.execute(
                "INSERT INTO task_leases VALUES (?, ?, ?, ?)",
                (task_id, next_seq, owner, _iso(now + LEASE_DURATION)),
            )
        return LeaseReceipt(task_id, next_seq, owner)

    def release_lease(self, lease: LeaseReceipt, now: datetime) -> bool:
        """目前那一列還是這張收據的取得列才新增放掉列;已被接手就什麼都不寫、回傳 False。
        不帶條件的話,舊持有者遲來的放掉列會排在接手者的取得列之後,讓第三方在接手者還在
        花錢時拿到租約(計劃增量 3b 第 1 輪設計審)。"""
        with immediate_transaction(self._conn):
            if not self._holds(lease):
                return False
            self._append_release(lease, now)
        return True

    def release_lease_quietly(self, lease: LeaseReceipt, now: datetime) -> None:
        """例外路徑用:照 release_lease 放掉,放掉本身的資料庫層失敗吞掉(租約等到期),讓呼叫端
        原本的例外照舊傳出去。吞的範圍比照 record_tool_call:只吞資料庫層錯誤,程式錯誤不吞。"""
        try:
            self.release_lease(lease, now)
        except (sqlite3.Error, DatabaseBusy):
            return

    def _current_lease(self, task_id: str) -> tuple[int, str | None, str] | None:
        record = self._conn.execute(
            "SELECT lease_seq, owner, expires_at FROM task_leases WHERE task_id = ? "
            "ORDER BY lease_seq DESC LIMIT 1", (task_id,),
        ).fetchone()
        return None if record is None else (int(record[0]), record[1], record[2])

    def _holds(self, lease: LeaseReceipt) -> bool:
        current = self._current_lease(lease.task_id)
        return current is not None and current[:2] == (lease.lease_seq, lease.owner)

    def _lease_allows(self, task_id: str, lease: LeaseReceipt | None, now: datetime) -> bool:
        if lease is not None:
            return lease.task_id == task_id and self._holds(lease)
        current = self._current_lease(task_id)
        return current is None or not _is_live(current, now)

    def _append_release(self, lease: LeaseReceipt, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO task_leases VALUES (?, ?, NULL, ?)",
            (lease.task_id, lease.lease_seq + 1, _iso(now)),
        )

    def record_tool_call(
        self, task_id: str, task_seq: int, endpoint: ToolEndpoint, outcome: str,
        latency_ms: float, now: datetime,
    ) -> None:
        """記一筆對外呼叫;這筆寫入自己絕不讓「資料庫層面」的例外往外傳(比照收件口事件表
        `inbox_store._write_event` 的既有做法,只吞 `sqlite3.Error`/`DatabaseBusy`):寫入本身
        失敗(資料庫忙碌、連線已關閉等)就放棄這筆記錄,不能因為記錄失敗而讓包住的那次呼叫
        跟著失敗;但呼叫端自己傳錯參數型別這類程式錯誤要老實丟出來,不能被這裡靜默吞掉。
        端點只收列舉成員(一般字串連長得一樣的也拒,Phase 9 增量 2),「其他」只給讀舊列用。
        """
        if type(endpoint) is not ToolEndpoint:  # 比照執行端封閉列舉的既有慣例丟 ValueError
            raise ValueError("端點必須是 ToolEndpoint 的成員")
        if endpoint is ToolEndpoint.OTHER:
            raise ValueError("「其他」只給讀舊列用,不能寫")
        try:
            self._conn.execute(
                "INSERT INTO tool_calls (task_id, task_seq, endpoint, outcome, latency_ms, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, task_seq, endpoint, outcome, latency_ms, _iso(now)),
            )
        except (sqlite3.Error, DatabaseBusy):
            return


# 唯讀開法要求資料庫已經有的表與欄位。不提案原因表 no_action_reasons 刻意不列:Phase 12 之前的資料庫
# 沒有這張表,列進來舊庫就開不起來;讀原因的方法自己看表在不在、沒有就回空值(展示寫「無法還原」,
# Phase 12 設計審 r2 p1、代碼審 r1 a2)
_REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "tasks": ("task_id", "seq", "state", "proposal_json", "operation_key"),
    "evidence": ("payload_json",), "tool_calls": ("latency_ms",), "task_leases": ("owner",),
    "follow_ups": ("follow_up_task_id",),
}
# 唯讀開法要求已經有的索引:唯讀連線不建索引,缺了窗口讀取會退化成全表掃描,視同還沒升級(Phase 9 增量 2
# 代碼審第 1 輪)
_REQUIRED_INDEXES = ("tool_calls_by_time",)


class TaskReader(TaskReads):
    """唯讀開法:只有唯讀連線;開啟時不建表、不補欄位;一開就進同一個快照,直到 close。"""

    def __init__(self, path: Path, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS):
        self._conn = connect_read_only(path, busy_timeout_seconds)
        try:
            missing = missing_schema(self._conn, _REQUIRED_SCHEMA, _REQUIRED_INDEXES)
            if missing:
                raise DatabaseNotUpgraded("分析行程資料庫還沒升級:缺 " + ", ".join(missing))
            begin_snapshot(self._conn)
        except BaseException:
            self._conn.close()
            raise

    def close(self) -> None:
        try:
            end_snapshot(self._conn)
        finally:
            self._conn.close()


def trace_for(store: TaskReads, task_id: str) -> TraceRecord:
    """把 tasks(狀態史)、evidence(證據)、tool_calls(呼叫記錄)用任務編號兜成一條軌跡。"""
    history = store.history(task_id)
    evidence = tuple(
        item for row in history for item in store.evidence_for(task_id, row.seq)
    )
    return TraceRecord(tasks=history, evidence=evidence, tool_calls=store.list_tool_calls(task_id),
                       follow_up_of=store.follow_up_of(task_id),
                       follow_up_to=store.follow_up_to(task_id))


def replan_counts(store: TaskStore, campaign_id: str | None = None) -> ReplanCounts:
    """分析端的衝突指標(最小版):只讀接續關係表(不會被清、帶廣告編號並建索引)。

    DSP 回版本衝突的次數在執行端嘗試紀錄,由執行端的查詢算;收件表清掉後原因分不出來的那一類
    只算得出「因保留期而重新規劃」,算不出其中幾次是版本衝突(計劃〈衝突可查〉)。"""
    where, params = ("", ()) if campaign_id is None else ("WHERE campaign_id = ?", (campaign_id,))
    created, limit, retention = store._conn.execute(  # 同模組的唯讀統計
        "SELECT coalesce(sum(outcome = 'created'), 0), "  # noqa: S608 - 條件是固定字串
        "coalesce(sum(outcome = 'limit_reached'), 0), "
        "coalesce(sum(outcome = 'created' AND reason = 'after_retention'), 0) "
        f"FROM follow_ups {where}", params,
    ).fetchone()
    return ReplanCounts(replanned=int(created), exhausted=int(limit),
                        after_retention=int(retention))
