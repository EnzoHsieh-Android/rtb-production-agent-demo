"""把系統紀錄觀察成判斷紀錄(Phase 12 設計審 r2 n7):驅動程式在別的行程外面,沒辦法「當下」知道
執行端走到哪個判斷點,只能讀各暫存資料庫的紀錄。每一筆判斷帶來源(哪一顆資料庫的哪一列),節點、
分支與白話原因取自流程圖對應表;系統沒有保存原因的,原因寫「無法還原」,不留白也不臆測。

程式狀態表裡往回走的轉換(同一件工作或同一把鍵的前後兩個狀態)落在流程圖的展開節點上。
交叉核對(`missing_from_path`)只比必經節點的順序:允許的回頭多出現幾次都不算對不上(設計審 r3 m6)。
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from rtb.analyzer.policy import NoActionReason
from rtb.analyzer.task_store import ReplanReason
from rtb.demo import flow
from rtb.demo.state_store import DecisionRow
from rtb.domain.attempt import AttemptState, OutcomeCode
from rtb.domain.task_state import TaskState
from rtb.executor.inbox_store import BlockCode, DeadLetterReason, LifecycleKind
from rtb.sqlitekit import connect_read_only

UNRECOVERABLE = "無法還原(系統沒有保存原因)"

Member = tuple[type[StrEnum], str]


@dataclass(frozen=True)
class SourceEvent:
    """從某一顆資料庫讀到的一列:主成員(狀態或事件種類)、更細的成員(原因代碼,可沒有)、
    屬於哪一件工作或哪一把鍵(回頭轉換照它分開算)。detail_missing:這種事件本該有原因,系統沒存。"""

    at: datetime
    origin: str
    primary: Member
    detail: Member | None
    entity: str
    detail_missing: bool = False


_EDGES = {(e.source, e.target) for e in flow.FLOW_GRAPH.edges}
_INCOMING: dict[str, list[tuple[str, str]]] = {}
for _edge in _EDGES:
    _INCOMING.setdefault(_edge[1], []).append(_edge)
_BACKS = {back.transition: back for back in flow.BACK_TRANSITIONS if back.transition}


def _place(member: Member) -> flow.Place | None:
    return flow.OUTCOMES.get(member)


def _edge_into(node: str, previous: str | None) -> tuple[str, str] | None:
    if previous is not None and (previous, node) in _EDGES:
        return (previous, node)
    incoming = _INCOMING.get(node, [])
    return incoming[0] if len(incoming) == 1 else None


class PathBuilder:
    """依時間順序把事件換成判斷紀錄;記著上一個節點與每件工作、每把鍵的上一個狀態。"""

    def __init__(self) -> None:
        self.last_node: str | None = None
        self._previous: dict[tuple[str, type[StrEnum]], str] = {}

    def add(self, events: Sequence[SourceEvent]) -> list[DecisionRow]:
        rows = []
        for event in sorted(events, key=lambda e: (e.at, e.origin)):
            row = self._one(event)
            if row is not None:
                rows.append(row)
                self.last_node = row.node
        return rows

    def _back_node(self, event: SourceEvent) -> str | None:
        enum, name = event.primary
        before = self._previous.get((event.entity, enum))
        self._previous[(event.entity, enum)] = name
        if before is None:
            return None
        try:
            back = _BACKS.get((enum[before], enum[name]))
        except KeyError:
            return None
        return None if back is None else back.node

    def _one(self, event: SourceEvent) -> DecisionRow | None:
        back = self._back_node(event)
        primary = _place(event.primary)
        if primary is None:
            return None
        detail = _place(event.detail) if event.detail is not None else None
        unrecoverable = event.detail_missing or (event.detail is not None and detail is None)
        chosen = detail or primary
        if back is not None:
            node, edge = back, _edge_into(back, self.last_node)
        elif chosen.edge is not None:
            node, edge = chosen.edge[1], chosen.edge
        else:
            node = chosen.node or ""
            edge = _edge_into(node, self.last_node)
        member = event.detail if detail is not None and event.detail else event.primary
        return DecisionRow(node=node, edge=edge, outcome=f"{member[0].__name__}.{member[1]}",
                           reason=UNRECOVERABLE if unrecoverable else chosen.text,
                           at=event.at, origin=event.origin)


def missing_from_path(observed: Sequence[str], required: Sequence[str]) -> str | None:
    """必經節點要照順序出現在觀察到的路徑裡(中間夾別的節點、回頭多走幾次都可以);
    回第一個對不上的必經節點,全部對上回空值。"""
    position = 0
    for node in required:
        try:
            position = list(observed).index(node, position) + 1
        except ValueError:
            return node
    return None


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _member(enum: type[StrEnum], value: str | None) -> Member | None:
    if value is None:
        return None
    for member in enum:
        if member.value == value:
            return (enum, member.name)
    return (enum, value)  # 不認得的值照原樣留著:對應表查不到,判斷紀錄會寫無法還原


class Observer:
    """輪詢一個情境的暫存資料庫,只讀上次之後新寫的列(照每張表的 rowid 往後讀,F7 幾百筆也不必
    每輪整表重讀)。資料庫或表還不存在就當作還沒有事件。"""

    def __init__(self, analyzer_db: Path, inbox_db: Path) -> None:
        self._analyzer_db, self._inbox_db = analyzer_db, inbox_db
        self._after: dict[str, int] = {}

    def poll(self) -> list[SourceEvent]:
        return [*self._analyzer(), *self._follow_ups(), *self._inbox_events(), *self._attempts()]

    def _rows(self, path: Path, table: str, columns: str) -> list[tuple[object, ...]]:
        """這張表 rowid 大於上次讀到的那些列;第一個欄位是 rowid。"""
        if not path.is_file():
            return []
        cursor = f"{path}:{table}"
        conn = connect_read_only(path)
        try:
            rows = conn.execute(
                f"SELECT rowid, {columns} FROM {table} "  # noqa: S608 - 表名與欄位是本模組的常數
                "WHERE rowid > ? ORDER BY rowid",
                (self._after.get(cursor, 0),)).fetchall()
        except sqlite3.OperationalError:  # 表還沒建好(子行程剛起來):下一輪再讀
            return []
        finally:
            conn.close()
        if rows:
            self._after[cursor] = int(rows[-1][0])
        return rows

    def _reason(self, task_id: str, seq: int) -> str | None:
        conn = connect_read_only(self._analyzer_db)
        try:
            row = conn.execute(
                "SELECT reason FROM no_action_reasons WHERE task_id = ? AND seq = ?",
                (task_id, seq)).fetchone()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
        return None if row is None else str(row[0])

    def _replanned(self, task_id: str) -> bool:
        conn = connect_read_only(self._analyzer_db)
        try:
            return conn.execute("SELECT 1 FROM follow_ups WHERE original_task_id = ?",
                                (task_id,)).fetchone() is not None
        except sqlite3.OperationalError:
            return False
        finally:
            conn.close()

    def _follow_ups(self) -> list[SourceEvent]:
        """擋下或過期後開新工作:接續關係跟原任務結案那一列同一個交易寫。"""
        return [SourceEvent(_time(str(at)), f"analyzer.follow_ups#{original}",
                            _member(ReplanReason, str(reason)) or (ReplanReason, ""), None,
                            f"task:{original}")
                for _, original, reason, at in self._rows(
                    self._analyzer_db, "follow_ups", "original_task_id, reason, written_at")]

    def _analyzer(self) -> list[SourceEvent]:
        events = []
        for _, task_id, seq, state, at in self._rows(self._analyzer_db, "tasks",
                                                    "task_id, seq, state, written_at"):
            # 擋下之後另開新工作:由「開新工作」那筆事件表示,不另外記一個「這件工作結束」
            if state == TaskState.BLOCKED.value and self._replanned(str(task_id)):
                continue
            detail, missing = None, False
            # 原因跟這一列同一個交易寫:看得到這列就看得到原因
            if state == TaskState.NO_ACTION.value:
                detail = _member(NoActionReason, self._reason(str(task_id), int(str(seq))))
                missing = detail is None
            events.append(SourceEvent(_time(str(at)), f"analyzer.tasks#{task_id}/{seq}",
                                      _member(TaskState, str(state)) or (TaskState, ""), detail,
                                      f"task:{task_id}", missing))
        return events

    def _inbox_events(self) -> list[SourceEvent]:
        events = []
        for event_id, at, kind, reason, task_id, revision in self._rows(
                self._inbox_db, "lifecycle_events", "at, kind, reason, task_id, revision"):
            detail: Member | None = None
            if kind == LifecycleKind.BLOCKED.value:
                detail = _member(BlockCode, None if reason is None else str(reason))
            elif kind == LifecycleKind.DEAD_LETTERED.value:
                detail = _member(DeadLetterReason, None if reason is None else str(reason))
            events.append(SourceEvent(_time(str(at)), f"inbox.lifecycle_events#{event_id}",
                                      _member(LifecycleKind, str(kind)) or (LifecycleKind, ""),
                                      detail, f"proposal:{task_id}/{revision}"))
        return events

    def _attempts(self) -> list[SourceEvent]:
        return [SourceEvent(_time(str(at)), f"inbox.attempts#{key}/{seq}",
                            _member(AttemptState, str(state)) or (AttemptState, ""),
                            _member(OutcomeCode, None if code is None else str(code)),
                            f"key:{key}")
                for _, key, seq, state, code, at in self._rows(
                    self._inbox_db, "attempts", "key, seq, state, code, written_at")]
