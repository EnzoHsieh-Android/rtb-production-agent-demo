"""六條服務水準指標在一個時間窗裡的好事件與有效事件(Phase 9 增量 3)。只讀。

全部是事件型、依事件時間歸窗:同一個已經過去的窗,之後再查答案不變。判好壞只用事件發生當下就
寫下、之後不變的資料,不用查詢當下的設定。每條的公式、好事件、有效事件、排除項寫在設定表
(服務水準模組)與各計算函式旁。

- 安全完成率:窗內的終點事件;已交給執行與業務上該擋的擋下是好,死信、已過期、同一操作先前已失敗
  是壞。死信後重放再到終點,兩個終點事件各算一次。
- 結果不明在期限內對帳完成:每把進過結果不明的鍵,事件時間 = min(結案, 第一次進結果不明 + 期限);
  期限前轉人工的排除;期限當刻或之後轉人工是壞,之後的人工結案不覆寫。
- 端到端交給執行:窗內每一個已交給執行的事件,從根任務在分析端建立算起,扣掉鏈上的人工核可等待與
  死信等待,不超過上限是好;接不上根任務、算出負值的不計入、另報。跨兩個資料庫讀到兩輪相同為止,
  三輪都不同回最後一輪並標不穩定。
- 佇列等待:每份收件的提案,事件時間 = min(第一次取件, 收件 + 期限)。
- 未授權或違反護欄的副作用、重複有害副作用:見副作用核對模組。
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rtb.analyzer.task_store import TaskReader
from rtb.domain.attempt import AttemptState
from rtb.executor import attempt_store
from rtb.executor.attempt_store import AttemptRow
from rtb.executor.inbox_store import (
    TERMINAL_KINDS,
    BlockCode,
    LifecycleEvent,
    LifecycleKind,
    ReadOnlyInbox,
)
from rtb.ops import side_effects
from rtb.ops.side_effects import Tally, iso

RECONCILE_DEADLINE = timedelta(minutes=10)  # 結果不明對帳的期限(業務承諾,示範不縮短)
QUEUE_DEADLINE = timedelta(seconds=30)  # 佇列等待的期限(業務承諾,示範不縮短)
END_TO_END_LIMIT = timedelta(minutes=2)  # 端到端交給執行的上限
MAX_ROUNDS = 3
MAX_CHAIN = 8  # 接續鏈最長幾代(分析端上限 3 代;這裡只防資料毀損造成的迴圈)
# 業務上該擋的擋下:除了「同一操作先前已失敗」(寫入失敗)以外的每一種擋下原因
BUSINESS_BLOCKS = frozenset(code.value for code in BlockCode) - {
    BlockCode.OPERATION_PREVIOUSLY_FAILED.value}
_DELIVERIES = frozenset({LifecycleKind.DELIVERED, LifecycleKind.RECLAIMED})
_APPROVAL_ENDS = frozenset({LifecycleKind.APPROVAL_RELEASED, *TERMINAL_KINDS})


@dataclass(frozen=True)
class Sources:
    executor_db: Path
    analyzer_db: Path
    dsp_url: str
    dsp_timeout_seconds: float = 2.0
    dsp_audit_key: str | None = None  # DSP 列操作端點的唯讀稽核金鑰;沒有就讀不到、照實標資料來源缺


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _tally(events: Iterable[tuple[str, bool]], unlinked: int = 0, clock_anomaly: int = 0,
           stable: bool = True) -> Tally:
    """(事件時間, 是不是好事件) -> 計數;壞事件留下時間(目標為零的一條要報最近一個)。"""
    found = list(events)
    return Tally(sum(good for _, good in found), len(found),
                 tuple(at for at, good in found if not good), unlinked=unlinked,
                 clock_anomaly=clock_anomaly, stable=stable)


# ---- 安全完成率 ----
def _safe(event: LifecycleEvent) -> bool:
    if event.kind == LifecycleKind.HANDED_OFF:
        return True
    return event.kind == LifecycleKind.BLOCKED and event.reason in BUSINESS_BLOCKS


def safe_completion(since: datetime, until: datetime, sources: Sources) -> Tally:
    """有效事件是窗內的終點事件(被取代、待核可、放回、重放放回都不是終點,不算)。擋下原因不認得的
    不在「業務上該擋」的清單裡,算壞(從嚴)。"""
    inbox = ReadOnlyInbox(sources.executor_db)
    try:
        with inbox.read_transaction() as tx:
            events = inbox.lifecycle_events_between(tx, since, until)
    finally:
        inbox.close()
    return _tally((e.at, _safe(e)) for e in events if e.kind in TERMINAL_KINDS)


# ---- 結果不明在期限內對帳完成 ----
def _reconciled(history: Sequence[AttemptRow]) -> tuple[datetime, bool] | None:
    """一把鍵的事件:(事件時間, 好不好);期限前轉人工的排除回空。期限當刻轉人工是壞事件(記在期限
    那一刻),之後同刻或更晚的人工結案不覆寫(代碼審第 1 輪:原本只判「小於期限」,期限當刻轉人工再
    同刻結案會被算成好)。"""
    unknown = next((r for r in history if r.state == AttemptState.UNKNOWN), None)
    if unknown is None:
        return None
    deadline = unknown.written_at + RECONCILE_DEADLINE
    for row in history:
        if row.written_at > deadline:
            break
        if row.state == AttemptState.ESCALATED:
            if row.written_at < deadline:
                return None  # 期限前已交給人,不是對帳慢
            return deadline, False  # 期限當刻才交給人:期限已到還沒結案
        if row.state in (AttemptState.VERIFIED, AttemptState.FAILED):
            return row.written_at, True  # 剛好在期限那一刻結案也算好
    return deadline, False  # 期限那一刻還沒結案:壞事件記在期限那一刻,之後結案也不改


def unknown_reconciled_in_time(since: datetime, until: datetime, sources: Sources) -> Tally:
    """候選是第一次進結果不明落在 [起 - 期限, 迄) 的鍵(用結果不明的時間索引找),再用鍵讀整串;
    工作量跟候選數成正比。"""
    inbox = ReadOnlyInbox(sources.executor_db)
    try:
        with inbox.read_transaction() as tx:
            keys = attempt_store.unknown_rows_between(tx, since - RECONCILE_DEADLINE, until)
            histories = [attempt_store.history(tx, key) for key in keys]
    finally:
        inbox.close()
    events = []
    for history in histories:
        found = _reconciled(history)
        if found is not None and since <= found[0] < until:
            events.append((iso(found[0]), found[1]))
    return _tally(events)


# ---- 佇列等待 ----
def queue_wait(since: datetime, until: datetime, sources: Sources) -> Tally:
    """候選是收件落在 [起 - 期限, 迄) 的提案(用事件的時間索引找),再查各自的第一次取件。之後才
    被標成已過期或被取代都不改:期限那一刻還沒取件就是壞事件。"""
    inbox = ReadOnlyInbox(sources.executor_db)
    try:
        with inbox.read_transaction() as tx:
            window = inbox.lifecycle_events_between(tx, since - QUEUE_DEADLINE, until)
            received = [e for e in window if e.kind == LifecycleKind.RECEIVED]
            histories = {task: inbox.lifecycle_events(tx, task)
                         for task in sorted({e.task_id for e in received})}
    finally:
        inbox.close()
    events = []
    for arrival in received:
        ident = (arrival.revision, arrival.content_hash)
        deadline = _time(arrival.at) + QUEUE_DEADLINE
        first = next((e for e in histories[arrival.task_id] if e.kind in _DELIVERIES
                      and (e.revision, e.content_hash) == ident and e.id > arrival.id), None)
        taken = None if first is None else _time(first.at)
        good = taken is not None and taken <= deadline
        moment = taken if good and taken is not None else deadline
        if since <= moment < until:
            events.append((iso(moment), good))
    return _tally(events)


# ---- 端到端交給執行 ----
@dataclass(frozen=True)
class _Chain:
    tasks: tuple[str, ...]  # 由根到尾
    created: str | None  # 根任務在分析端建立的時間;分析端沒有這個任務為空


def _chain(reader: TaskReader, task: str) -> _Chain:
    root = task
    for _ in range(MAX_CHAIN):
        parent = reader.follow_up_of(root)
        if parent is None:
            break
        root = parent
    tasks = [root]
    for _ in range(MAX_CHAIN):
        child = reader.follow_up_to(tasks[-1])
        if child is None or child in tasks:
            break
        tasks.append(child)
    history = reader.history(root)
    return _Chain(tuple(tasks), iso(history[0].written_at) if history else None)


def _waits(events: Iterable[LifecycleEvent], until: str) -> float:
    """人工核可等待(進待核可 → 放回或終點)與死信等待(進死信 → 重放放回)的秒數,只算到 until。"""
    total = 0.0
    opened: dict[tuple[str, int, str | None], LifecycleEvent] = {}
    for event in sorted(events, key=lambda e: (e.at, e.id)):
        if event.at > until:
            break
        ident = (event.task_id, event.revision, event.content_hash)
        start = opened.get(ident)
        if start is not None and event.kind in (
                _APPROVAL_ENDS if start.kind == LifecycleKind.AWAITING_APPROVAL
                else frozenset({LifecycleKind.REPLAY_REQUEUED})):
            total += (_time(event.at) - _time(start.at)).total_seconds()
            del opened[ident]
        if event.kind in (LifecycleKind.AWAITING_APPROVAL, LifecycleKind.DEAD_LETTERED):
            opened.setdefault(ident, event)  # 死信同時結束待核可、開始死信等待
    return total


def _handoff_round(
    since: datetime, until: datetime, sources: Sources,
) -> tuple[tuple[tuple[str, bool], ...], int, int]:
    """一輪:執行端快照讀窗內已交給執行的事件,分析端快照讀接續鏈,再在同一個執行端快照裡讀鏈上
    每個任務的事件算等待。"""
    inbox = ReadOnlyInbox(sources.executor_db)
    try:
        with inbox.read_transaction() as tx:
            handoffs = [e for e in inbox.lifecycle_events_between(tx, since, until)
                        if e.kind == LifecycleKind.HANDED_OFF]
            reader = TaskReader(sources.analyzer_db)
            try:
                chains = {task: _chain(reader, task) for task in {e.task_id for e in handoffs}}
            finally:
                reader.close()
            chain_events = {task: inbox.lifecycle_events(tx, task)
                            for chain in chains.values() for task in chain.tasks}
    finally:
        inbox.close()
    events, unlinked, anomalies = [], 0, 0
    for handoff in handoffs:
        chain = chains[handoff.task_id]
        if chain.created is None:
            unlinked += 1
            continue
        waited = _waits((e for task in chain.tasks for e in chain_events[task]), handoff.at)
        spent = (_time(handoff.at) - _time(chain.created)).total_seconds() - waited
        if spent < 0:
            anomalies += 1
            continue
        events.append((handoff.at, spent <= END_TO_END_LIMIT.total_seconds()))
    return tuple(events), unlinked, anomalies


def end_to_end_handoff(since: datetime, until: datetime, sources: Sources) -> Tally:
    """跨兩個資料庫:每輪重開快照,兩輪相同才回;三輪都不同回最後一輪並標不穩定(比照追蹤檢視與
    指標;代碼審第 1 輪)。"""
    previous, stable = None, False
    for _ in range(MAX_ROUNDS):
        current = _handoff_round(since, until, sources)
        if current == previous:
            stable = True
            break
        previous = current
    assert previous is not None  # noqa: S101 - 至少讀過一輪
    events, unlinked, anomalies = previous
    return _tally(events, unlinked=unlinked, clock_anomaly=anomalies, stable=stable)


# ---- 目標為零的兩條 ----
def unauthorized_side_effects(since: datetime, until: datetime, sources: Sources) -> Tally:
    return side_effects.unauthorized(since, until, sources.executor_db, sources.dsp_url,
                                     sources.dsp_timeout_seconds, sources.dsp_audit_key)


def harmful_duplicates(since: datetime, until: datetime, sources: Sources) -> Tally:
    return side_effects.duplicates(since, until, sources.executor_db, sources.dsp_url,
                                   sources.dsp_timeout_seconds, sources.dsp_audit_key)


COUNTERS: Mapping[str, Callable[[datetime, datetime, Sources], Tally]] = {
    "safe_completion": safe_completion,
    "unknown_reconciled_in_time": unknown_reconciled_in_time,
    "end_to_end_handoff": end_to_end_handoff,
    "queue_wait": queue_wait,
    "unauthorized_side_effects": unauthorized_side_effects,
    "harmful_duplicates": harmful_duplicates,
}


def count(name: str, since: datetime, until: datetime, sources: Sources) -> Tally:
    """一條服務水準指標在窗 [since, until) 的計數。"""
    return COUNTERS[name](since, until, sources)
