"""分析行程自己的 SQLite:任務只增不改的歷史表,加證據表。

「目前狀態」永遠是某個任務編號序號最大的那一列;沒有 UPDATE、沒有 DELETE。這樣
「這一步的輸出已安全存好」跟「這一列真的寫進資料庫」是同一件事,不需要另外的旗標。
新列的序號在寫入交易內用目前最大序號加一決定,並且核對「準備要接的那一列」仍是目前
最新的一列,不是就中止、不寫入——這是並行呼叫 advance() 時的最後一道防線。
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.domain.proposal import ActionType, Proposal
from rtb.domain.task_state import TaskState
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, connect, immediate_transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT NOT NULL, seq INTEGER NOT NULL, state TEXT NOT NULL,
    campaign_id TEXT NOT NULL, proposal_json TEXT, error_detail TEXT,
    written_at TEXT NOT NULL, PRIMARY KEY (task_id, seq));
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_seq INTEGER NOT NULL,
    kind TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL,
    campaign_version_observed INTEGER, content_hash TEXT NOT NULL, trust_class TEXT NOT NULL);
"""


class TaskAlreadyExists(Exception):
    """同一個任務編號已經存在,但這次帶的廣告編號不同:呼叫端的錯誤,不靜默接受。"""


class TaskNotFound(Exception):
    """這個任務編號完全沒有歷史列。"""


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
    stamp = datetime.fromisoformat(written_at.replace("Z", "+00:00"))
    return TaskRow(
        task_id=task_id, seq=seq, state=TaskState(state), campaign_id=campaign_id,
        proposal=None if proposal_json is None else _proposal_from_json(proposal_json),
        error_detail=error_detail, written_at=stamp,
    )


class TaskStore:
    def __init__(self, path: Path, busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS):
        self._conn = connect(path, busy_timeout_seconds, SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def create_task(self, task_id: str, campaign_id: str, now: datetime) -> None:
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
            "content_hash, trust_class FROM evidence WHERE task_id = ? AND task_seq = ? "
            "ORDER BY evidence_id",
            (task_id, task_seq),
        ).fetchall()
        return tuple(
            Evidence(
                evidence_id=r[0], task_id=task_id, kind=EvidenceKind(r[1]), source=r[2],
                observed_at=datetime.fromisoformat(r[3].replace("Z", "+00:00")),
                campaign_version_observed=r[4], content_hash=r[5], trust_class=TrustClass(r[6]),
            )
            for r in rows
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
    ) -> bool:
        """核對 expected_seq 仍是目前最新一列,新增一列 new_state 並(可選)附帶證據列。

        回傳是否真的寫入了;False 表示輸給並行的另一次呼叫,這次呼叫沒有寫入任何東西。
        """
        with immediate_transaction(self._conn):
            current = self._conn.execute(
                "SELECT MAX(seq) FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            if current != expected_seq:
                return False
            next_seq = expected_seq + 1
            campaign_id = self._conn.execute(
                "SELECT campaign_id FROM tasks WHERE task_id = ? AND seq = ?",
                (task_id, expected_seq),
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, next_seq, new_state.value, campaign_id,
                 None if proposal is None else _proposal_to_json(proposal),
                 error_detail, _iso(now)),
            )
            for item in evidence:
                self._conn.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (item.evidence_id, task_id, next_seq, item.kind.value, item.source,
                     _iso(item.observed_at), item.campaign_version_observed,
                     item.content_hash, item.trust_class.value),
                )
            if before_commit is not None:
                before_commit()
        return True
