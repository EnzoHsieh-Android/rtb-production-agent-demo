"""跨元件追蹤檢視(Phase 9 增量 1):給一個任務編號,把三個來源的持久紀錄組成一條時間線。只讀。

- 分析端:任務歷史(含證據編號)與對外呼叫,沿接續關係把接續任務串進來(先往前找到根任務,再往後)。
- 執行端:這些任務的生命週期事件、各修訂對應冪等鍵的嘗試紀錄、DSP 呼叫紀錄、死信信封與操作稽核。
  冪等鍵優先用分析端交給執行時存下的,沒有才從分析端的提案重算,再沒有才用執行端事件記下的,
  每一份修訂都標明鍵從哪來。同一把鍵被多份修訂共用時,嘗試只列一次、歸建立它的修訂(嘗試第一列記的
  任務與修訂),其他修訂標「由這把鍵的既有結果確認」。
- DSP:用冪等鍵讀唯讀的操作紀錄端點,留下操作編號與提交時間;讀不到就照樣組、標明缺這一段。

讀法:兩個資料庫都只經唯讀開法(唯讀連線、不取寫入鎖、不補表)。三個來源不可能同一個快照,所以每一輪
重新開三個快照並記下各自的讀取時間,兩輪的資料內容(不含讀取時間)相同才回傳;最多三輪,仍不同就回傳
最後一輪並標明「讀取期間有新提交,時間線可能不一致」。

模型說明(Phase 11B 增量 2,[S915]、[S930]):分析端提案那一步(已提案那一列)的細節最後多一欄「模型
說明」,放在程式算的數字與證據之後。有成功結果就顯示文字,標「模型產生、僅供參考」與來源(錄製或即時);
沒有成功結果就顯示最新一筆的結果類別(例如「已達上限」);還沒有任何領取(或資料庫還沒有說明表)顯示
「尚未產生」。說明只從分析端唯讀開法讀,追蹤檢視不呼叫模型。

排序:時間一律正規化成 UTC;同一刻先依來源(分析端、執行端、DSP),同來源再依表,同一張表依它自己的
寫入順序(自動遞增序號、嘗試的鍵與序號、任務歷史的序號)。表之間的固定順序只為了穩定,不代表因果。
每一段都帶齊關聯欄位,缺的標明缺,不留白。
"""

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO

from rtb.analyzer.task_store import NarrativeStatus, TaskReader, TaskRow, ToolCall
from rtb.domain.attempt import operation_key
from rtb.domain.proposal import content_hash
from rtb.domain.task_state import TaskState
from rtb.executor import attempt_store
from rtb.executor.attempt_store import AttemptTraceRow, DspCallRow
from rtb.executor.inbox_store import (
    DatabaseNotUpgraded,
    DeadLetterOp,
    DeadLetterRow,
    LifecycleEvent,
    ReadOnlyInbox,
)
from rtb.httpclient import request_json
from rtb.ops.cli import EXIT_BAD_ARGUMENTS as EXIT_BAD_ARGUMENTS  # 參數錯(7,維運套件共用)
from rtb.ops.cli import Parser

EXIT_OK = 0
EXIT_NO_DATABASE = 2  # 資料庫檔不存在
EXIT_NOT_UPGRADED = 3  # 資料庫還沒升級到這一版:先啟動一次執行迴圈(或分析行程)
MAX_ROUNDS = 3
MAX_CHAIN = 8  # 接續鏈最長幾代(分析端上限 3 代;這裡只防資料毀損造成的迴圈)
DEFAULT_DSP_TIMEOUT_SECONDS = 2.0


class Origin(StrEnum):
    ANALYZER = "analyzer"
    EXECUTOR = "executor"
    DSP = "dsp"


class Table(StrEnum):
    """每一段來自哪一張表;成員順序就是同一刻、同一來源時的固定順序。"""

    TASK_HISTORY = "task_history"
    TOOL_CALLS = "tool_calls"
    LIFECYCLE_EVENTS = "lifecycle_events"
    ATTEMPTS = "attempts"
    DSP_CALLS = "dsp_calls"
    DEAD_LETTERS = "dead_letters"
    DEAD_LETTER_OPS = "dead_letter_ops"
    DSP_OPERATIONS = "dsp_operations"


class Absent(StrEnum):
    MISSING = "missing"  # 這一欄缺:來源沒有記、或這一段本來就不知道
    UNKNOWN = "unknown"  # 呼叫紀錄沒記內容雜湊:同一把鍵可能對應好幾份內容,不任選一個


class KeyOrigin(StrEnum):
    ANALYZER_STORED = "analyzer_stored"  # 分析端交給執行時存下的鍵
    RECOMPUTED = "recomputed"  # 分析端沒存,從分析端的提案用目前的算法重算
    EXECUTOR_RECORDED = "executor_recorded"  # 分析端沒有這份修訂,用執行端事件寫下時算的鍵


MODEL_LABEL = "模型產生、僅供參考"
NOT_YET = "尚未產生"
IN_PROGRESS = "產生中(還沒有結果)"
# 說明沒有成功結果時,結果類別給人看的寫法(類別跟模型用戶端的結果類別同值)
NARRATIVE_SHOWN = {
    "timeout": "逾時", "local_cap_refused": "已達上限", "quota_exhausted": "訂閱額度用完",
    "overrun": "超支", "no_recording": "沒有錄製", "unreadable": "回應讀不懂",
    "config_error": "設定錯誤", "transient": "暫時性服務錯誤", "ledger_busy": "花費帳忙碌",
}
MISSING = Absent.MISSING
UNKNOWN = Absent.UNKNOWN
ORIGIN_ORDER = tuple(Origin)
TABLE_ORDER = tuple(Table)
FIELDS = ("task_id", "revision", "content_hash", "key", "tenant", "campaign_id", "worker",
          "policy_version", "attempt", "dsp_operation_id")


@dataclass(frozen=True)
class Segment:
    """時間線上的一段:時間(UTC)、來源、表、表內寫入順序、發生了什麼、關聯欄位與細節。"""

    at: str
    origin: Origin
    table: Table
    order: tuple[str | int, ...]
    what: str
    fields: tuple[tuple[str, object], ...]
    detail: tuple[tuple[str, object], ...]

    def field(self, name: str) -> object:
        return dict(self.fields)[name]


Ident = tuple[str, int, str | None]  # 一份提案:任務、修訂、內容雜湊(任務編號重用時靠雜湊分開)


@dataclass(frozen=True)
class RevisionKey:
    """一份提案(任務、修訂、內容雜湊)對應的冪等鍵、鍵從哪來、那把鍵的嘗試歸哪一份提案,以及它是
    不是由既有結果確認。關聯一律用三件組,不用任務加修訂(代碼審第 1 輪:任務編號重用、內容不同的
    第二份會被吞掉)。"""

    task_id: str
    revision: int
    content_hash: str | None
    key: str
    key_origin: KeyOrigin
    attempts_owner: Ident | Absent
    settled_by_existing_key: bool


@dataclass(frozen=True)
class Trace:
    task_id: str
    tasks: tuple[str, ...]  # 接續鏈,由根到最新
    segments: tuple[Segment, ...]
    revisions: tuple[RevisionKey, ...]
    read_at: tuple[tuple[Origin, str], ...]  # 最後一輪各來源的讀取時間
    missing: tuple[Origin, ...]  # 讀不到的來源
    stable: bool  # False:三輪都不同,讀取期間有新提交,時間線可能不一致
    rounds: int


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(moment: str | datetime) -> str:
    """固定格式的 UTC 字串(同執行端、分析端的寫法);DSP 的 +00:00 或其他時區一律換算。"""
    value = moment if isinstance(moment, datetime) else datetime.fromisoformat(moment)
    if value.tzinfo is None:  # 三個來源都帶時區;萬一沒有,當成 UTC(不猜本機時區)
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _segment(
    where: tuple[Origin, Table], at: str | datetime, order: tuple[str | int, ...], what: str,
    fields: Mapping[str, object], detail: Mapping[str, object],
) -> Segment:
    filled = tuple((name, MISSING if fields.get(name) is None else fields[name])
                   for name in FIELDS)
    return Segment(_utc(at), where[0], where[1], order, what, filled, tuple(detail.items()))


def _sort_key(segment: Segment) -> tuple[str, int, int, tuple[str | int, ...]]:
    return (segment.at, ORIGIN_ORDER.index(segment.origin), TABLE_ORDER.index(segment.table),
            segment.order)


# ---- 分析端 ----
@dataclass(frozen=True)
class _AnalyzerPart:
    tasks: tuple[str, ...]
    segments: tuple[Segment, ...]
    keys: tuple[tuple[Ident, str, KeyOrigin], ...]  # (提案, 冪等鍵, 鍵從哪來)


def _chain(reader: TaskReader, task_id: str) -> list[str]:
    root = task_id
    for _ in range(MAX_CHAIN):
        parent = reader.follow_up_of(root)
        if parent is None:
            break
        root = parent
    chain = [root]
    for _ in range(MAX_CHAIN):
        child = reader.follow_up_to(chain[-1])
        if child is None or child in chain:
            break
        chain.append(child)
    return chain


def _history_segment(
    reader: TaskReader, index: int, row: TaskRow, stored: str | None,
) -> Segment:
    prop = row.proposal
    fields: dict[str, object] = {"task_id": row.task_id, "campaign_id": row.campaign_id,
                                 "key": stored}
    if prop is not None:
        fields |= {"revision": prop.revision, "content_hash": content_hash(prop),
                   "policy_version": prop.policy_version}
    evidence = tuple(item.evidence_id for item in reader.evidence_for(row.task_id, row.seq))
    detail: dict[str, object] = {"seq": row.seq, "error_detail": row.error_detail,
                                 "evidence": evidence}
    if prop is not None and row.state is TaskState.PROPOSED:  # 提案那一步:說明放在最後
        detail["model_narrative"] = _narrative(
            reader.narrative_for(row.task_id, prop.revision, content_hash(prop)))
    return _segment((Origin.ANALYZER, Table.TASK_HISTORY), row.written_at, (index, row.seq),
                    row.state.value, fields, detail)


def _narrative(status: NarrativeStatus | None) -> dict[str, object]:
    if status is None:
        return {"result": None, "shown": NOT_YET}
    if status.outcome == "ok" and status.text is not None:
        return {"label": MODEL_LABEL, "source": status.source, "text": status.text}
    if status.outcome is None:
        return {"result": None, "shown": IN_PROGRESS}
    return {"result": status.outcome, "shown": NARRATIVE_SHOWN.get(status.outcome, "其他失敗")}


def _tool_segment(index: int, position: int, call: ToolCall) -> Segment:
    return _segment((Origin.ANALYZER, Table.TOOL_CALLS), call.at, (index, position),
                    call.endpoint, {"task_id": call.task_id},
                    {"outcome": call.outcome, "latency_ms": call.latency_ms,
                     "task_seq": call.task_seq})


def _analyzer_task(
    reader: TaskReader, index: int, task: str,
) -> tuple[list[Segment], list[tuple[Ident, str, KeyOrigin]]]:
    stored = dict(reader.handed_off_keys(task))
    segments: list[Segment] = []
    keys: dict[Ident, tuple[str, KeyOrigin]] = {}
    for row in reader.history(task):
        key = stored.get(row.seq)
        if row.proposal is not None:
            ident = (task, row.proposal.revision, content_hash(row.proposal))
            if key is not None:
                keys[ident] = (key, KeyOrigin.ANALYZER_STORED)
            else:
                keys.setdefault(ident, (operation_key(row.proposal), KeyOrigin.RECOMPUTED))
        segments.append(_history_segment(reader, index, row, key))
    segments += [_tool_segment(index, position, call)
                 for position, call in enumerate(reader.list_tool_calls(task))]
    return segments, [(ident, key, origin) for ident, (key, origin) in sorted(
        keys.items(), key=lambda item: _ident_order(item[0]))]


def _ident_order(ident: Ident) -> tuple[str, int, str]:
    return ident[0], ident[1], ident[2] or ""


def _read_analyzer(path: Path, task_id: str) -> _AnalyzerPart:
    """一個快照:唯讀開法一開就進同一個快照,直到關閉。"""
    reader = TaskReader(path)
    try:
        tasks = _chain(reader, task_id)
        segments: list[Segment] = []
        keys: list[tuple[Ident, str, KeyOrigin]] = []
        for index, task in enumerate(tasks):
            found, revisions = _analyzer_task(reader, index, task)
            segments += found
            keys += revisions
        return _AnalyzerPart(tuple(tasks), tuple(segments), tuple(keys))
    finally:
        reader.close()


# ---- 執行端 ----
@dataclass(frozen=True)
class _ExecutorPart:
    segments: tuple[Segment, ...]
    revisions: tuple[RevisionKey, ...]
    owned: tuple[tuple[str, Ident], ...]  # 有嘗試的鍵與它歸的那份提案(讀 DSP 用)


def _event_segment(event: LifecycleEvent) -> Segment:
    fields = {"task_id": event.task_id, "revision": event.revision,
              "content_hash": event.content_hash, "key": event.key, "tenant": event.tenant,
              "campaign_id": event.campaign_id, "worker": event.actor,
              "policy_version": event.policy_version}
    return _segment((Origin.EXECUTOR, Table.LIFECYCLE_EVENTS), event.at, (event.id,), event.kind,
                    fields, {"reason": event.reason, "source": event.source,
                             "deliveries": event.deliveries,
                             "from_existing": event.from_existing,
                             "program_version": event.program_version})


def _attempt_segment(row: AttemptTraceRow) -> Segment:
    fields = {"task_id": row.task_id, "revision": row.revision, "key": row.key,
              "content_hash": row.content_hash,
              "tenant": row.tenant, "campaign_id": row.campaign_id, "worker": row.actor,
              "attempt": row.seq}
    return _segment((Origin.EXECUTOR, Table.ATTEMPTS), row.written_at, (row.key, row.seq),
                    row.state, fields, {"code": row.code, "send_count": row.send_count,
                                        "source": row.source,
                                        "program_version": row.program_version})


def _call_segment(call: DspCallRow) -> Segment:
    """內容雜湊讀那一列自己在呼叫當下記的;沒記的標「不明」,不從冪等鍵回推(同鍵可以不同雜湊,
    代碼審第 2 輪)。"""
    fields = {"task_id": call.task_id, "revision": call.revision, "key": call.key,
              "content_hash": UNKNOWN if call.content_hash is None else call.content_hash,
              "campaign_id": call.campaign_id, "worker": call.actor}
    return _segment((Origin.EXECUTOR, Table.DSP_CALLS), call.at, (call.id,), call.kind, fields,
                    {"result": call.result, "status": call.status, "error": call.error,
                     "latency_ms": call.latency_ms, "source": call.source,
                     "program_version": call.program_version})


def _letter_segments(
    letters: Iterable[DeadLetterRow], ops: Iterable[DeadLetterOp],
) -> list[Segment]:
    found = [_segment((Origin.EXECUTOR, Table.DEAD_LETTERS), letter.at, (letter.id,),
                      "dead_letter_envelope",
                      {"task_id": letter.task_id, "revision": letter.revision,
                       "content_hash": letter.content_hash, "key": letter.key},
                      {"envelope": letter.id, "reason": letter.reason,
                       "last_failure": letter.last_failure, "deliveries": letter.deliveries,
                       "failure_class": letter.failure_class})
             for letter in letters]
    found += [_segment((Origin.EXECUTOR, Table.DEAD_LETTER_OPS), op.at, (op.id,), op.action,
                       {"task_id": op.task_id, "revision": op.revision, "worker": op.operator},
                       {"envelope": op.envelope, "reason": op.reason})
              for op in ops]
    return found


def _revision_keys(
    analyzer: _AnalyzerPart, events: Iterable[LifecycleEvent],
) -> dict[Ident, tuple[str, KeyOrigin]]:
    """每一份提案(任務、修訂、內容雜湊)的冪等鍵:分析端存下的 → 分析端提案重算 → 執行端事件
    記下的。"""
    found = {ident: (key, origin) for ident, key, origin in analyzer.keys}
    for event in events:
        if event.key is not None:
            found.setdefault((event.task_id, event.revision, event.content_hash),
                             (event.key, KeyOrigin.EXECUTOR_RECORDED))
    return found


def _revisions(
    keys: Mapping[Ident, tuple[str, KeyOrigin]], owners: Mapping[str, Ident],
) -> tuple[RevisionKey, ...]:
    result = []
    for ident, (key, origin) in sorted(keys.items(), key=lambda item: _ident_order(item[0])):
        owner = owners.get(key)
        result.append(RevisionKey(*ident, key, origin, MISSING if owner is None else owner,
                                  owner is not None and owner != ident))
    return tuple(result)


def _read_executor(path: Path, analyzer: _AnalyzerPart) -> _ExecutorPart:
    """一個快照:唯讀交易開頭就定下快照,整段查詢都讀它。"""
    inbox = ReadOnlyInbox(path)
    try:
        with inbox.read_transaction() as tx:
            events = [e for task in analyzer.tasks for e in inbox.lifecycle_events(tx, task)]
            keys = _revision_keys(analyzer, events)
            attempts = {key: attempt_store.trace_rows(tx, key)
                        for key in sorted({key for key, _ in keys.values()})}
            calls = [c for task in analyzer.tasks for c in attempt_store.dsp_calls_for(tx, task)]
            letters = [x for task in analyzer.tasks for x in inbox.dead_letters_for(tx, task)]
            ops = [x for task in analyzer.tasks for x in inbox.dead_letter_ops_for(tx, task)]
    finally:
        inbox.close()
    owners: dict[str, Ident] = {
        key: (rows[0].task_id or "", rows[0].revision or 0, rows[0].content_hash)
        for key, rows in attempts.items() if rows}
    segments = [_event_segment(e) for e in events]
    segments += [_attempt_segment(row) for rows in attempts.values() for row in rows]
    segments += [_call_segment(c) for c in calls] + _letter_segments(letters, ops)
    return _ExecutorPart(tuple(segments), _revisions(keys, owners), tuple(sorted(owners.items())))


# ---- DSP ----
def _read_dsp(
    url: str, timeout: float, owned: Iterable[tuple[str, Ident]],
) -> tuple[Segment, ...] | None:
    """用冪等鍵讀 DSP 的唯讀操作紀錄端點;查不到(404)的鍵沒有這一段;逾時、斷線、其他狀態碼或
    讀不懂回 None(整個 DSP 來源標缺)。"""
    segments = []
    for key, (task, revision, digest) in owned:
        try:
            status, body = request_json(f"{url.rstrip('/')}/operations/{key}", "GET", None,
                                        timeout)
        except (OSError, ValueError):
            return None
        if not isinstance(body, dict):
            return None
        if status == 404 and body.get("error") == "operation_not_found":
            continue
        committed, operation = body.get("committed_at"), body.get("operation_id")
        if status != 200 or not isinstance(committed, str):
            return None
        try:
            at = _utc(committed)
        except ValueError:
            return None
        segments.append(_segment(
            (Origin.DSP, Table.DSP_OPERATIONS), at,
            (operation if isinstance(operation, int) else 0,), "dsp_operation",
            {"task_id": task, "revision": revision, "content_hash": digest, "key": key,
             "campaign_id": body.get("campaign_id"), "dsp_operation_id": operation},
            {"action": body.get("action"), "version_after": body.get("version_after")}))
    return tuple(segments)


# ---- 讀到穩定為止 ----
@dataclass(frozen=True)
class _Round:
    content: tuple[Any, ...]  # 比對用:不含讀取時間
    tasks: tuple[str, ...]
    segments: tuple[Segment, ...]
    revisions: tuple[RevisionKey, ...]
    missing: tuple[Origin, ...]
    read_at: tuple[tuple[Origin, str], ...]


def _read_round(
    task_id: str, analyzer_db: Path, executor_db: Path, dsp_url: str, timeout: float,
    clock: Callable[[], datetime],
) -> _Round:
    """每一輪重新開三個來源的快照(關掉上一輪的),各自記下讀取時間。"""
    analyzer_at = _utc(clock())
    analyzer = _read_analyzer(analyzer_db, task_id)
    executor_at = _utc(clock())
    executor = _read_executor(executor_db, analyzer)
    dsp_at = _utc(clock())
    dsp = _read_dsp(dsp_url, timeout, executor.owned)
    missing = () if dsp is not None else (Origin.DSP,)
    segments = tuple(sorted((*analyzer.segments, *executor.segments, *(dsp or ())),
                            key=_sort_key))
    return _Round((analyzer.tasks, segments, executor.revisions, missing), analyzer.tasks,
                  segments, executor.revisions, missing,
                  ((Origin.ANALYZER, analyzer_at), (Origin.EXECUTOR, executor_at),
                   (Origin.DSP, dsp_at)))


def build_trace(
    task_id: str, *, analyzer_db: Path, executor_db: Path, dsp_url: str,
    dsp_timeout_seconds: float = DEFAULT_DSP_TIMEOUT_SECONDS,
    clock: Callable[[], datetime] = _now,
) -> Trace:
    """讀到兩輪資料內容相同才回傳,最多三輪;沒有新提交時第二輪就判穩定。資料庫檔不存在丟
    FileNotFoundError;還沒升級丟 DatabaseNotUpgraded(都不寫任何東西)。"""
    previous: _Round | None = None
    for rounds in range(1, MAX_ROUNDS + 1):
        current = _read_round(task_id, Path(analyzer_db), Path(executor_db), dsp_url,
                              dsp_timeout_seconds, clock)
        if previous is not None and current.content == previous.content:
            return _trace(task_id, current, stable=True, rounds=rounds)
        previous = current
    assert previous is not None  # noqa: S101 - 至少讀過一輪
    return _trace(task_id, previous, stable=False, rounds=MAX_ROUNDS)


def _trace(task_id: str, chosen: _Round, *, stable: bool, rounds: int) -> Trace:
    return Trace(task_id, chosen.tasks, chosen.segments, chosen.revisions, chosen.read_at,
                 chosen.missing, stable, rounds)


def to_primitives(trace: Trace) -> dict[str, Any]:
    """命令列輸出用的結構化結果(可以直接轉成 JSON)。"""
    return {
        "task_id": trace.task_id, "tasks": list(trace.tasks), "stable": trace.stable,
        "rounds": trace.rounds,
        "note": None if trace.stable else "讀取期間有新提交,時間線可能不一致",
        "missing": [origin.value for origin in trace.missing],
        "read_at": {origin.value: at for origin, at in trace.read_at},
        "revisions": [{"task_id": r.task_id, "revision": r.revision,
                       "content_hash": r.content_hash, "key": r.key,
                       "key_origin": r.key_origin.value,
                       "attempts_owner": (r.attempts_owner if r.attempts_owner is MISSING
                                          else list(r.attempts_owner)),
                       "settled_by_existing_key": r.settled_by_existing_key}
                      for r in trace.revisions],
        "segments": [{"at": s.at, "origin": s.origin.value, "table": s.table.value,
                      "order": list(s.order), "what": s.what, "fields": dict(s.fields),
                      "detail": {k: list(v) if isinstance(v, tuple) else v
                                 for k, v in s.detail}}
                     for s in trace.segments],
    }


# ---- 命令列入口 ----
def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(description="一個任務的跨元件追蹤(只讀)")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--analyzer-db", required=True, type=Path)
    parser.add_argument("--executor-db", required=True, type=Path)
    parser.add_argument("--dsp-url", required=True)
    parser.add_argument("--dsp-timeout-seconds", type=float,
                        default=DEFAULT_DSP_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def run(
    argv: list[str] | None = None, *, clock: Callable[[], datetime] = _now,
    out: TextIO | None = None, err: TextIO | None = None,
) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    try:
        result = build_trace(args.task_id, analyzer_db=args.analyzer_db,
                             executor_db=args.executor_db, dsp_url=args.dsp_url,
                             dsp_timeout_seconds=args.dsp_timeout_seconds, clock=clock)
    except FileNotFoundError as missing:
        print(f"找不到資料庫檔:{missing}", file=errors)
        return EXIT_NO_DATABASE
    except DatabaseNotUpgraded as old:
        print(f"{old}。請先啟動一次執行迴圈(分析端資料庫則先跑一次分析行程),讓它補上新表與"
              "新欄位;追蹤檢視只讀,不替它補", file=errors)
        return EXIT_NOT_UPGRADED
    print(json.dumps(to_primitives(result), ensure_ascii=False, indent=2), file=out or sys.stdout)
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
