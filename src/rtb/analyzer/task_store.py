"""分析行程自己的 SQLite:任務只增不改的歷史表,加證據表。

「目前狀態」永遠是某個任務編號序號最大的那一列;沒有 UPDATE、沒有 DELETE。這樣
「這一步的輸出已安全存好」跟「這一列真的寫進資料庫」是同一件事,不需要另外的旗標。
新列的序號在寫入交易內用目前最大序號加一決定,並且核對「準備要接的那一列」仍是目前
最新的一列,不是就中止、不寫入——這是並行呼叫 advance() 時的最後一道防線。
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from rtb.domain._checks import is_id
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.domain.proposal import ActionType, Proposal
from rtb.domain.task_state import IllegalTransition, TaskState, can_transition
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy, connect, immediate_transaction

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
"""
MAX_ERROR_DETAIL_LENGTH = 2000  # error_detail 進永久不可刪改的表,長度必須有上限



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


@dataclass(frozen=True)
class ToolCall:
    task_id: str
    task_seq: int
    endpoint: str
    outcome: str
    latency_ms: float
    at: datetime


@dataclass(frozen=True)
class TraceRecord:
    """給定一個任務編號的完整軌跡:狀態史、每一步的證據、每一次對外呼叫。"""

    tasks: tuple[TaskRow, ...]
    evidence: tuple[Evidence, ...]
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    seq: int
    state: TaskState
    campaign_id: str
    proposal: Proposal | None
    error_detail: str | None
    written_at: datetime


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


def _row_from_record(record: tuple[str, int, str, str, str | None, str | None, str]) -> TaskRow:
    task_id, seq, state, campaign_id, proposal_json, error_detail, written_at = record
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


class TaskStore:
    def __init__(self, path: Path, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS):
        try:
            self._conn = connect(path, busy_timeout_seconds, SCHEMA)
        except DatabaseBusy as exc:
            raise TaskStoreBusy(str(exc)) from exc
        self._migrate_evidence_payload_column()

    def _migrate_evidence_payload_column(self) -> None:
        """`CREATE TABLE IF NOT EXISTS` 不會幫既有表補欄位:增量 3 建立的舊資料庫只有九欄,
        沒有增量 4 才加的 `payload_json`。每次連線都檢查一次,缺欄位就補上;舊列補
        `'{}'`(誠實反映「這些舊證據沒有留下原始數值,只有雜湊」,不是編造資料)。
        """
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(evidence)")}
        if "payload_json" not in columns:
            with immediate_transaction(self._conn):
                self._conn.execute(
                    "ALTER TABLE evidence ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")

    def close(self) -> None:
        self._conn.close()

    def create_task(self, task_id: str, campaign_id: str, now: datetime) -> None:
        if not is_id(task_id) or not is_id(campaign_id):
            raise InvalidTaskId(f"任務編號或廣告編號格式不合法:{task_id!r}, {campaign_id!r}")
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
            self._conn.execute(
                "INSERT INTO tasks VALUES (?, 1, ?, ?, NULL, NULL, ?)",
                (task_id, TaskState.RECEIVED.value, campaign_id, _iso(now)),
            )

    def latest(self, task_id: str) -> TaskRow | None:
        record = self._conn.execute(
            "SELECT task_id, seq, state, campaign_id, proposal_json, error_detail, written_at "
            "FROM tasks WHERE task_id = ? ORDER BY seq DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return None if record is None else _row_from_record(record)

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
    ) -> bool:
        """核對 expected_seq 仍是目前最新一列,新增一列 new_state 並(可選)附帶證據列。

        回傳是否真的寫入了;False 表示輸給並行的另一次呼叫,這次呼叫沒有寫入任何東西。
        """
        mismatched = [item.task_id for item in evidence if item.task_id != task_id]
        if mismatched:
            raise EvidenceTaskMismatch(
                f"要附帶的證據裡有 {mismatched} 不屬於任務 {task_id}")
        capped_detail = (
            None if error_detail is None else error_detail[:MAX_ERROR_DETAIL_LENGTH])
        with immediate_transaction(self._conn):
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
            self._conn.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, next_seq, new_state.value, campaign_id,
                 None if proposal is None else _proposal_to_json(proposal),
                 capped_detail, _iso(now)),
            )
            for item in evidence:
                self._conn.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, next_seq, item.evidence_id, item.kind.value, item.source,
                     _iso(item.observed_at), item.campaign_version_observed,
                     item.content_hash, item.trust_class.value,
                     json.dumps(dict(item.payload), sort_keys=True, ensure_ascii=True,
                                allow_nan=False)),
                )
            if before_commit is not None:
                before_commit()
        return True

    def record_tool_call(
        self, task_id: str, task_seq: int, endpoint: str, outcome: str, latency_ms: float,
        now: datetime,
    ) -> None:
        """記一筆對外呼叫;這筆寫入自己絕不讓「資料庫層面」的例外往外傳(比照收件口事件表
        `inbox_store._write_event` 的既有做法,只吞 `sqlite3.Error`/`DatabaseBusy`):寫入本身
        失敗(資料庫忙碌、連線已關閉等)就放棄這筆記錄,不能因為記錄失敗而讓包住的那次呼叫
        跟著失敗;但呼叫端自己傳錯參數型別這類程式錯誤要老實丟出來,不能被這裡靜默吞掉。
        """
        try:
            self._conn.execute(
                "INSERT INTO tool_calls (task_id, task_seq, endpoint, outcome, latency_ms, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, task_seq, endpoint, outcome, latency_ms, _iso(now)),
            )
        except (sqlite3.Error, DatabaseBusy):
            return

    def list_tool_calls(self, task_id: str) -> tuple[ToolCall, ...]:
        rows = self._conn.execute(
            "SELECT task_id, task_seq, endpoint, outcome, latency_ms, at FROM tool_calls "
            "WHERE task_id = ? ORDER BY id", (task_id,),
        ).fetchall()
        return tuple(
            ToolCall(task_id=r[0], task_seq=r[1], endpoint=r[2], outcome=r[3],
                     latency_ms=r[4], at=datetime.fromisoformat(r[5].replace("Z", "+00:00")))
            for r in rows
        )

    def history(self, task_id: str) -> tuple[TaskRow, ...]:
        records = self._conn.execute(
            "SELECT task_id, seq, state, campaign_id, proposal_json, error_detail, written_at "
            "FROM tasks WHERE task_id = ? ORDER BY seq", (task_id,),
        ).fetchall()
        return tuple(_row_from_record(r) for r in records)


def trace_for(store: TaskStore, task_id: str) -> TraceRecord:
    """把 tasks(狀態史)、evidence(證據)、tool_calls(呼叫記錄)用任務編號兜成一條軌跡。"""
    history = store.history(task_id)
    evidence = tuple(
        item for row in history for item in store.evidence_for(task_id, row.seq)
    )
    return TraceRecord(tasks=history, evidence=evidence, tool_calls=store.list_tool_calls(task_id))
