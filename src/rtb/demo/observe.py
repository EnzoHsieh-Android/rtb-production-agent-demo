"""把系統紀錄觀察成判斷紀錄(Phase 12 設計審 r2 n7):驅動程式在別的行程外面,沒辦法「當下」知道
執行端走到哪個判斷點,只能讀各暫存資料庫的紀錄。每一筆判斷帶來源(哪一顆資料庫的哪一列),節點、
分支與白話原因取自流程圖對應表;系統沒有保存原因的,原因寫「無法還原」,不留白也不臆測。

讀的一律是分析端與收件口既有的唯讀開法(第 2 輪代碼審 a1):照每張表的列號往後讀新寫的列,
不自己開連線、不對別人的表下查詢;資料庫還沒建好(子行程剛起來)丟的是「還沒升級」,下一輪再讀。

程式狀態表裡往回走的轉換(同一件工作或同一把鍵的前後兩個狀態)落在流程圖的展開節點上。
交叉核對(`missing_from_path`)分兩份清單:必經節點照順序出現;扣掉必經與允許的回頭之後,剩下的
節點一律算對不上(第 2 輪代碼審 o1/c3)。
"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from rtb.analyzer.policy import NoActionReason
from rtb.analyzer.task_store import FollowUpRow, ReplanReason, TaskReader, TaskRow
from rtb.demo import flow
from rtb.demo.state_store import DecisionRow
from rtb.domain.attempt import AttemptState, OutcomeCode
from rtb.domain.task_state import TaskState
from rtb.executor import attempt_store
from rtb.executor.inbox_store import (
    TERMINAL_KINDS,
    BlockCode,
    DatabaseNotUpgraded,
    DeadLetterReason,
    LifecycleKind,
    ReadOnlyInbox,
)

UNRECOVERABLE = "無法還原(系統沒有保存原因)"
GENERATIONS_USED_UP = "需要重新分析,但接續的代數已經用完,不再開新工作"
RECLAIMED_UNKNOWN = "處理到一半中斷、換人接手:不知道平台有沒有寫進去,回頭去平台查"
HOLD_SECONDS = 1.0  # 事件晚這麼久才寫進判斷紀錄:各資料庫提交先後不等於時間先後,等一下再排序
# 執行端自己寫的列(重啟時帶時鐘偏移的就是它們);收件口與管理指令寫的列照真實時鐘
_EXECUTOR_SOURCES = frozenset({attempt_store.Source.EXECUTOR_LOOP.value,
                               attempt_store.Source.STARTUP_RECOVERY.value})
_TERMINAL = frozenset(kind.value for kind in TERMINAL_KINDS)

Member = tuple[type[StrEnum], str]


@dataclass(frozen=True)
class SourceEvent:
    """從某一顆資料庫讀到的一列:主成員(狀態或事件種類)、更細的成員(原因代碼,可沒有)、
    屬於哪一件工作或哪一把鍵(回頭轉換照它分開算)。detail_missing:這種事件本該有原因,系統沒存。
    order:同一時間的先後(資料庫、同庫的先後、列號數字);key、task:冪等鍵與任務,把執行端與分析端
    的事件接起來;note:取代對應表的白話(對應表說不清的情況)。"""

    at: datetime
    origin: str
    primary: Member
    detail: Member | None
    entity: str
    detail_missing: bool = False
    order: tuple[str, int, int] = ("", 0, 0)
    key: str | None = None
    task: str | None = None
    note: str | None = None


def _sort_key(event: SourceEvent) -> tuple[datetime, tuple[str, int, int]]:
    return event.at, event.order


_EDGES = {(e.source, e.target) for e in flow.FLOW_GRAPH.edges}
_EDGE_TEXT = {(e.source, e.target): e.label for e in flow.FLOW_GRAPH.edges}
_INCOMING: dict[str, list[tuple[str, str]]] = {}
for _edge in _EDGES:
    _INCOMING.setdefault(_edge[1], []).append(_edge)
_BACKS = {back.transition: back for back in flow.BACK_TRANSITIONS if back.transition}
_BACK_TEXT = {back.node: back.what for back in flow.BACK_TRANSITIONS}


def _place(member: Member) -> flow.Place | None:
    return flow.OUTCOMES.get(member)


# 系統紀錄對不到的判斷點(平台回覆、寫入前再確認…):判斷紀錄裡不會有這些節點,邊可以經過它們
_OBSERVABLE = ({p.node for p in flow.OUTCOMES.values() if p.node}
               | {p.edge[1] for p in flow.OUTCOMES.values() if p.edge}
               | {b.node for b in flow.BACK_TRANSITIONS})
_OUTGOING: dict[str, list[str]] = {}
for _source, _target in _EDGES:
    _OUTGOING.setdefault(_source, []).append(_target)


def _edge_into(node: str, previous: str | None) -> tuple[str, str] | None:
    """走進這個節點的邊:上一個節點直接連過來就是那條;不然從上一個節點經過紀錄對不到的判斷點找得到
    的那條(寫入 → 平台回覆 → 不明);都沒有就留空,不猜(第 3 輪代碼審 g2:原本退回唯一的進入邊,
    剛拿起就猝死被畫成「寫入 → 中途中斷」)。還沒有上一個節點時,唯一的進入邊從紀錄對不到的判斷點
    出發才用(從看得到的節點出發就等於猜了一步沒看到的事)。"""
    if previous is None:
        incoming = _INCOMING.get(node, [])
        return incoming[0] if len(incoming) == 1 and incoming[0][0] not in _OBSERVABLE else None
    if (previous, node) in _EDGES:
        return (previous, node)
    seen, frontier = {previous}, [previous]
    while frontier:
        step = frontier.pop(0)
        for target in _OUTGOING.get(step, []):
            if target in _OBSERVABLE or target in seen:
                continue
            if (target, node) in _EDGES:
                return (target, node)
            seen.add(target)
            frontier.append(target)
    return None


class PathBuilder:
    """依時間順序把事件換成判斷紀錄;記著上一個節點、每件工作與每把鍵的上一個狀態,以及執行端
    寫入失敗、被接手這兩種要跨事件才看得出來的情況。"""

    def __init__(self) -> None:
        self.last_node: str | None = None  # 正在處理的事件那件工作的上一個節點
        self._last: dict[str, str] = {}  # 每件工作上一個節點(第 3 輪代碼審 g2:原本全情境共用一個)
        self.streams: list[tuple[str, str, str]] = []  # (工作, 哪一條紀錄, 節點):逐條核對用
        self._previous: dict[tuple[str, type[StrEnum]], str] = {}
        self._failed_keys: set[str] = set()  # 嘗試轉成失敗的鍵
        self._failed_tasks: set[str] = set()  # 寫入被平台拒絕、收件口確認成擋下的任務
        self._reclaimed_keys: set[str] = set()  # 剛被接手、還沒有下一個嘗試狀態的鍵

    def add(self, events: Sequence[SourceEvent]) -> list[DecisionRow]:
        rows = []
        for event in sorted(events, key=_sort_key):
            self.last_node = self._last.get(event.task or "")
            row = self._one(event)
            if row is not None:
                rows.append(row)
                self._last[event.task or ""] = row.node
                self.streams.append((event.task or "", event.entity.split(":", 1)[0], row.node))
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

    def _special(self, event: SourceEvent) -> DecisionRow | bool | None:
        """跨事件才看得出來的三種情況;回 True 代表這個事件不另外記(已由別的事件表示)。"""
        enum, name = event.primary
        if enum is AttemptState and event.key is not None:
            reclaimed = event.key in self._reclaimed_keys
            self._reclaimed_keys.discard(event.key)
            if name == AttemptState.FAILED.name:
                self._failed_keys.add(event.key)
            if name == AttemptState.UNKNOWN.name and self._previous.get(
                    (event.entity, AttemptState)) == name:
                # 查證逾時記一列同樣的不明:稍後再查(第 3 輪代碼審 g1),不是又進一次不明
                return self._row(event, "x_recheck", _edge_into("x_recheck", self.last_node),
                                 _BACK_TEXT["x_recheck"])
            if reclaimed and name == AttemptState.UNKNOWN.name:
                # 同一個交易的接手已經畫了「處理到一半中斷」:不套「平台沒有明確回覆」那條邊
                return self._row(event, "x_unknown", None, RECLAIMED_UNKNOWN)
        if enum is LifecycleKind and event.key is not None:
            if name == LifecycleKind.RECLAIMED.name:
                self._reclaimed_keys.add(event.key)
            if name == LifecycleKind.BLOCKED.name and event.key in self._failed_keys:
                # 寫了、平台拒絕:收件口同一個交易確認成擋下,不是寫入前再確認就擋下
                self._failed_tasks.add(event.task or "")
                return True
        if event.task in self._failed_tasks and (
                enum is ReplanReason or (enum is TaskState and name == TaskState.BLOCKED.name)):
            node = "a_followup" if enum is ReplanReason else "a_blocked_end"
            return self._row(event, node, ("x_failed", node), _EDGE_TEXT[("x_failed", node)])
        return None

    def _row(self, event: SourceEvent, node: str, edge: tuple[str, str] | None,
             reason: str) -> DecisionRow:
        member = event.detail or event.primary
        return DecisionRow(node=node, edge=edge, outcome=f"{member[0].__name__}.{member[1]}",
                           reason=reason, at=event.at, origin=event.origin)

    def _one(self, event: SourceEvent) -> DecisionRow | None:
        special = self._special(event)
        back = self._back_node(event)
        if special is True:
            return None
        if isinstance(special, DecisionRow):
            return special
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
        reason = event.note or (UNRECOVERABLE if unrecoverable else chosen.text)
        return DecisionRow(node=node, edge=edge, outcome=f"{member[0].__name__}.{member[1]}",
                           reason=reason, at=event.at, origin=event.origin)


class Timeline:
    """事件先放一下再依時間排序放出去(第 2 輪代碼審 o4:跨輪排序):晚一輪才讀到、時間較早的事件
    照樣排在前面。放多久是固定的一小段,判斷紀錄仍在 3 秒內寫進展示狀態([S1011])。"""

    def __init__(self, hold_seconds: float = HOLD_SECONDS) -> None:
        self._hold = timedelta(seconds=hold_seconds)
        self._waiting: list[SourceEvent] = []

    def push(self, events: Sequence[SourceEvent], now: datetime) -> list[SourceEvent]:
        self._waiting.extend(events)
        self._waiting.sort(key=_sort_key)
        ready = [e for e in self._waiting if e.at <= now - self._hold]
        self._waiting = self._waiting[len(ready):]
        return ready

    def flush(self) -> list[SourceEvent]:
        ready, self._waiting = self._waiting, []
        return ready


def missing_from_path(observed: Sequence[str], required: Sequence[str],
                      allowed: Collection[str] = ()) -> str | None:
    """交叉核對([S1010]):照順序逐一消耗必經節點——看到的節點是下一個必經的就消耗掉,是允許的回頭
    就略過,其他一律算對不上(第 3 輪代碼審 g1/x1:原本只找得到就好,必經節點重複出現、多繞一圈照樣
    過)。回第一個對不上的節點(多出來的,或最後還沒消耗到的必經節點),都對上回空值。"""
    position = 0
    for node in observed:
        if position < len(required) and node == required[position]:
            position += 1
        elif node not in allowed:
            return node
    return None if position == len(required) else required[position]


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _member(enum: type[StrEnum], value: str | None) -> Member | None:
    if value is None:
        return None
    for member in enum:
        if member.value == value:
            return (enum, member.name)
    return (enum, value)  # 不認得的值照原樣留著:對應表查不到,判斷紀錄會寫無法還原


def all_pages[T](read: Callable[[int], Sequence[T]], after: int,
                 position: Callable[[T], int]) -> tuple[list[T], int]:
    """游標式讀取一路讀到空頁為止;回(全部新列, 新的游標)。不看每頁上限是多少(第 3 輪代碼審 a1:
    每頁上限各模組各有一份,這裡再抄一份會對不上),多讀一次空頁換不必同步。"""
    found: list[T] = []
    while True:
        page = read(after)
        found.extend(page)
        if page:
            after = position(page[-1])
        if not page:
            return found, after


class Observer:
    """輪詢一個情境的暫存資料庫,只讀上次之後新寫的列(照每張表的列號往後讀,F7 幾百筆也不必
    每輪整表重讀)。資料庫還不存在或還沒建好就當作還沒有事件。executor_offset:重啟時執行端帶的
    時鐘偏移,它寫的列顯示時扣掉(第 2 輪代碼審 o4)。"""

    def __init__(self, analyzer_db: Path, inbox_db: Path) -> None:
        self._analyzer_db, self._inbox_db = analyzer_db, inbox_db
        self._tasks_after = self._follow_ups_after = self._events_after = self._attempts_after = 0
        self._follow_ups: dict[str, FollowUpRow] = {}
        self.executor_offset = timedelta(0)

    def poll(self) -> list[SourceEvent]:
        return [*self._analyzer(), *self._inbox()]

    def _analyzer(self) -> list[SourceEvent]:
        if not self._analyzer_db.is_file():
            return []
        try:
            reader = TaskReader(self._analyzer_db)
        except DatabaseNotUpgraded:
            return []
        try:  # 同一個快照:看得到原任務結案那一列,就看得到同一個交易寫的接續關係與原因
            follow_ups, self._follow_ups_after = all_pages(
                reader.follow_ups_after, self._follow_ups_after, lambda f: f.rowid)
            tasks, self._tasks_after = all_pages(
                reader.tasks_after, self._tasks_after, lambda pair: pair[0])
            self._follow_ups.update({f.original_task_id: f for f in follow_ups})
            events = [self._follow_up(f) for f in follow_ups if f.follow_up_task_id is not None]
            for rowid, row in tasks:
                event = self._task(reader, rowid, row)
                if event is not None:
                    events.append(event)
            return events
        finally:
            reader.close()

    def _follow_up(self, follow: FollowUpRow) -> SourceEvent:
        """擋下或過期後開新工作:接續關係跟原任務結案那一列同一個交易寫,排在新工作第一列之前。"""
        return SourceEvent(follow.written_at, f"analyzer.follow_ups#{follow.original_task_id}",
                           (ReplanReason, follow.reason.name), None,
                           f"task:{follow.original_task_id}", order=("analyzer", 0, follow.rowid),
                           task=follow.original_task_id)

    def _task(self, reader: TaskReader, rowid: int, row: TaskRow) -> SourceEvent | None:
        note, detail, missing = None, None, False
        if row.state is TaskState.BLOCKED:
            follow = self._follow_ups.get(row.task_id)
            if follow is not None and follow.follow_up_task_id is not None:
                return None  # 由「開新工作」那筆事件表示,不另外記「這件工作結束」
            if follow is not None:
                note = GENERATIONS_USED_UP  # 代數用完:接續關係有記,但沒有開新工作
        if row.state is TaskState.NO_ACTION:  # 原因跟這一列同一個交易寫
            detail = _member(NoActionReason, reader.no_action_reason(row.task_id, row.seq))
            missing = detail is None
        return SourceEvent(row.written_at, f"analyzer.tasks#{row.task_id}/{row.seq}",
                           (TaskState, row.state.name), detail, f"task:{row.task_id}", missing,
                           order=("analyzer", 1, rowid), task=row.task_id, note=note)

    def _shifted(self, at: str, source: str | None) -> datetime:
        moment = _time(at)
        return moment - self.executor_offset if source in _EXECUTOR_SOURCES else moment

    def _inbox(self) -> list[SourceEvent]:
        if not self._inbox_db.is_file():
            return []
        try:
            inbox = ReadOnlyInbox(self._inbox_db)
        except DatabaseNotUpgraded:
            return []
        try:
            with inbox.read_transaction() as tx:
                lifecycle, self._events_after = all_pages(
                    lambda after: inbox.lifecycle_events_after(tx, after), self._events_after,
                    lambda e: e.id)
                attempts, self._attempts_after = all_pages(
                    lambda after: attempt_store.trace_rows_after(tx, after), self._attempts_after,
                    lambda pair: pair[0])
        finally:
            inbox.close()
        events = []
        for event in lifecycle:
            detail: Member | None = None
            if event.kind == LifecycleKind.BLOCKED.value:
                detail = _member(BlockCode, event.reason)
            elif event.kind == LifecycleKind.DEAD_LETTERED.value:
                detail = _member(DeadLetterReason, event.reason)
            # 同一時間:開始類的事件排在嘗試之前,結案類的排在嘗試之後
            rank = 2 if event.kind in _TERMINAL else 0
            events.append(SourceEvent(
                self._shifted(event.at, event.source), f"inbox.lifecycle_events#{event.id}",
                _member(LifecycleKind, event.kind) or (LifecycleKind, ""), detail,
                f"proposal:{event.task_id}/{event.revision}", order=("inbox", rank, event.id),
                key=event.key, task=event.task_id))
        for rowid, attempt in attempts:
            events.append(SourceEvent(
                self._shifted(attempt.written_at, attempt.source),
                f"inbox.attempts#{attempt.key}/{attempt.seq}",
                _member(AttemptState, attempt.state) or (AttemptState, ""),
                _member(OutcomeCode, attempt.code), f"key:{attempt.key}",
                order=("inbox", 1, rowid), key=attempt.key, task=attempt.task_id))
        return events
