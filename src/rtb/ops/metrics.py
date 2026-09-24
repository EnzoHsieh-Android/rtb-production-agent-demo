"""有界標籤的指標(Phase 9 增量 2):從只增不改的紀錄即時算,不另存計數器。只讀。

兩種查詢,時間一律由呼叫端傳入,不自己讀時鐘,而且一定要帶時區(沒帶會被當成本機時間,窗界整段位移):
- 窗內統計:起、迄(含起點、不含終點),迄減起不得超過 24 小時。
- 現況快照:「現在」;不套時間窗,窗外開始、至今還卡著的也算。

讀法跟追蹤檢視一樣只經唯讀開法(唯讀連線、不取寫入鎖、不補表)。執行端只讀一個快照,除端到端以外的
指標都出自它(單一快照本來就自洽)。端到端要把執行端的終點跟分析端的接續鏈拼起來,兩個資料庫不可能同一
個快照:報告的數字(含端到端)一律用第一輪,之後再重開兩邊的快照重讀兩輪,每一輪端到端的完整樣本都
跟第一輪相同才標穩定;任何一輪不同就停、標不穩定,照樣是第一輪的數字。現況快照只讀執行端一個快照。

每個樣本:名稱、標籤、數值、樣本數、狀態、分子(比率與計數才有)、範例(最多 3 個任務編號)、租戶靠
查詢當下設定反查的份數、附註。任何比率分母為 0、延遲沒有樣本,都回「無樣本」,不是 0、不丟例外。
- 比率的分母是跟它「分組標籤」相同的母體;「類別標籤」切分子(例:DSP 呼叫比率依呼叫類別分組、
  依結果類別切分子)。每一項的分組與類別寫在各自的計算函式旁。
- 延遲依那一段的結束時間歸窗,還沒結束的不算;分位數用「排序後取最近排名」。事件型計數依事件時間歸窗。
- 最終結果率與端到端依「查詢當下的最終結果」,重放之後同一個過去的窗會變;其餘是事件型,窗過去就不變
  (執行端處理也是:依窗內實際出現的每個終點事件分段,不看查詢當下的最終終點)。
- 擋下、進待核可、核可放回、過時決策拒絕是「事件次數」:同一份提案重投再擋一次算兩次。可觀測查詢的
  停下紀錄是「同一份提案同一種類只記一次」的提案數,兩者定義不同、數字不必相等,不要拿來互相核對。

標籤只准用宣告的種類(每個指標宣告哪幾種見 DECLARED,跟計劃的對照表一致),值域也有界:租戶是設定檔的
名稱加「未知」;政策版本是版本字串本身,只收只增不刪的已知政策版本清單裡的值,其他歸其他;程式版本只看
窗內真的貢獻樣本的紀錄,超過 20 種時依窗內最後出現時間排,較早的併成其他;其餘是封閉列舉,讀到列舉外的
值歸成其他。
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO

from rtb.analyzer.task_store import TaskReader, ToolCall, ToolEndpoint
from rtb.domain._checks import require_aware
from rtb.domain.attempt import AttemptState
from rtb.domain.proposal import KNOWN_POLICY_VERSIONS
from rtb.executor import attempt_store, observability
from rtb.executor.attempt_store import (
    AttemptRow,
    AttemptTraceRow,
    DspCallKind,
    DspCallResult,
    DspCallRow,
    DspErrorCode,
    TerminalRow,
)
from rtb.executor.capability_signer import SigningRefused, Tenant, load_tenants
from rtb.executor.inbox_store import (
    TERMINAL_KINDS,
    BlockCode,
    DatabaseNotUpgraded,
    LifecycleEvent,
    LifecycleKind,
    ReadOnlyInbox,
)
from rtb.modelledger_view import Caller, ModelLedgerView, Outcome, Source, ledger_path
from rtb.ops.cli import EXIT_BAD_ARGUMENTS as EXIT_BAD_ARGUMENTS  # 參數錯(7,維運套件共用)
from rtb.ops.cli import Parser, aware_time

MAX_WINDOW = timedelta(hours=24)
MAX_PROGRAM_VERSIONS = 20
MAX_EXEMPLARS = 3
MAX_ROUNDS = 3
MAX_CHAIN = 8  # 接續鏈最長幾代(分析端上限 3 代;這裡只防資料毀損造成的迴圈)
UNKNOWN_TENANT = "unknown"
OTHER = "other"
EXIT_OK = 0
EXIT_NO_DATABASE = 2
EXIT_NOT_UPGRADED = 3
EXIT_WINDOW_TOO_LONG = 4
EXIT_UNSTABLE = 5  # 重讀時端到端跟第一輪不同:照樣印出第一輪的數字,結束代碼標明不穩定
EXIT_BAD_CONFIG = 6
EVENT_COUNT_NOTE = ("事件次數:同一份提案每擋一次、每進一次待核可都算一次(重投再擋算兩次);"
                    "可觀測查詢的停下紀錄是同提案同種類只記一次的提案數,定義不同,數字不必相等")
# 模型與 Jev(Phase 11B 增量 1 起):從模型花費帳的唯讀開法算,依呼叫者、結果類別、來源分次數
MODEL_NOTE = ("模型呼叫次數(依預留時間歸窗),依呼叫者、結果類別、來源(即時或錄製重播)分;還沒結算的"
              "結果類別記 unsettled;花費與延遲在花費帳")
NO_LEDGER_NOTE = "無樣本:沒有指定模型花費帳"
MISSING_LEDGER_NOTE = "無樣本:模型花費帳不存在(還沒有任何模型呼叫)"
UNSETTLED = "unsettled"


class WindowTooLong(ValueError):
    """窗超過 24 小時,或迄不在起之後。"""


class LabelKind(StrEnum):
    TENANT = "tenant"
    STAGE = "stage"
    BLOCK_REASON = "block_reason"  # 擋下原因與關卡(可核可的關卡也是擋下原因的成員)
    TERMINAL_KIND = "terminal_kind"
    DSP_CALL_KIND = "dsp_call_kind"
    DSP_CALL_RESULT = "dsp_call_result"
    PROGRAM_VERSION = "program_version"
    POLICY_VERSION = "policy_version"
    ANALYZER_ENDPOINT = "analyzer_endpoint"
    MODEL_CALLER = "model_caller"
    MODEL_OUTCOME = "model_outcome"
    MODEL_SOURCE = "model_source"


class Stage(StrEnum):
    """交接文件 14.4 的 task phase(封閉列舉)。"""

    ANALYSIS = "analysis"
    INTAKE = "intake"
    QUEUE = "queue"
    APPROVAL = "approval"
    EXECUTION = "execution"
    RECONCILIATION = "reconciliation"


class Status(StrEnum):
    OK = "ok"
    NO_SAMPLES = "no_samples"
    NOT_APPLICABLE = "not_applicable"


K = LabelKind
Label = tuple[LabelKind, str]
Ident = tuple[str, int, str]  # 一份提案:任務、修訂、內容雜湊(沒記雜湊為空字串)

# 每個指標宣告的標籤種類(跟計劃〈每個指標帶哪些標籤〉的對照表一致)
_TENANT_STAGE = frozenset({K.TENANT, K.STAGE})
_REASONED = frozenset({K.TENANT, K.BLOCK_REASON, K.STAGE})
_VERSIONED = frozenset({K.TENANT, K.PROGRAM_VERSION, K.STAGE})
_STAGE_ONLY = frozenset({K.STAGE})
DECLARED: Mapping[str, frozenset[LabelKind]] = {
    "terminal_event_rate": frozenset({K.TENANT, K.TERMINAL_KIND, K.BLOCK_REASON,
                                      K.POLICY_VERSION, K.STAGE}),
    "final_outcome_rate": frozenset({K.TENANT, K.TERMINAL_KIND, K.BLOCK_REASON, K.STAGE}),
    "redelivery_rate": _TENANT_STAGE, "dead_letter_rate": _TENANT_STAGE,
    "queue_wait_seconds": _TENANT_STAGE, "approval_wait_seconds": _REASONED,
    "dead_letter_wait_seconds": _TENANT_STAGE, "execution_seconds": _VERSIONED,
    "reconciliation_seconds": _VERSIONED, "reconciliation_success_rate": _VERSIONED,
    "end_to_end_seconds": _STAGE_ONLY, "end_to_end_unlinked": _STAGE_ONLY,
    "end_to_end_clock_anomaly": _STAGE_ONLY,
    "dsp_calls": frozenset({K.TENANT, K.DSP_CALL_KIND, K.DSP_CALL_RESULT, K.PROGRAM_VERSION,
                            K.STAGE}),
    "dsp_call_rate": frozenset({K.DSP_CALL_KIND, K.DSP_CALL_RESULT, K.STAGE}),
    "dsp_call_latency_ms": frozenset({K.DSP_CALL_KIND, K.PROGRAM_VERSION, K.STAGE}),
    "version_conflict_rate": _VERSIONED,
    "analyzer_calls": frozenset({K.ANALYZER_ENDPOINT, K.STAGE}),
    "analyzer_call_latency_ms": frozenset({K.ANALYZER_ENDPOINT, K.STAGE}),
    "blocked": _REASONED, "awaiting_approval": _REASONED, "approval_released": _REASONED,
    "stale_rejections": _REASONED, "duplicates_prevented": _TENANT_STAGE,
    "queue_pending": _STAGE_ONLY, "queue_oldest_wait_seconds": _STAGE_ONLY,
    **{f"{name}{suffix}": _STAGE_ONLY
       for name in ("unresolved_unknown", "unresolved_committed_unverified",
                    "unresolved_escalated", "in_flight")
       for suffix in ("", "_max_age_seconds")},
    "locked_campaigns": _STAGE_ONLY, "aggregate_utilization": _TENANT_STAGE,
    "model_and_jev": frozenset({K.MODEL_CALLER, K.MODEL_OUTCOME, K.MODEL_SOURCE, K.STAGE}),
}
STALE_REASONS = frozenset({BlockCode.VERSION_CHANGED, BlockCode.POLICY_VERSION_CHANGED,
                           BlockCode.DECISION_STALE})
_DELIVERIES = frozenset({LifecycleKind.DELIVERED, LifecycleKind.RECLAIMED})
_UNRESOLVED_NAMES = {AttemptState.UNKNOWN: "unresolved_unknown",
                     AttemptState.COMMITTED_UNVERIFIED: "unresolved_committed_unverified",
                     AttemptState.ESCALATED: "unresolved_escalated",
                     AttemptState.IN_FLIGHT: "in_flight"}
_STATS = (("p50", 50), ("p95", 95), ("p99", 99), ("max", 100))


@dataclass(frozen=True)
class Sample:
    name: str
    labels: tuple[Label, ...]
    value: float | None
    count: int
    status: Status = Status.OK
    numerator: int | None = None
    exemplars: tuple[str, ...] = ()
    tenant_by_lookup: int = 0  # 其中幾份的租戶是用查詢當下的租戶設定反查的
    note: str | None = None

    @property
    def base(self) -> str:
        """延遲的四個統計量(名稱後綴 .p50 等)共用同一組宣告。"""
        return self.name.split(".", 1)[0]

    def label_map(self) -> dict[str, str]:
        return {kind.value: value for kind, value in self.labels}


@dataclass(frozen=True)
class Report:
    samples: tuple[Sample, ...]
    stable: bool
    rounds: int


@dataclass(frozen=True)
class _Member:
    """指標裡的一份:它的時間(歸窗與挑範例用)、任務、租戶是不是反查的、數值(延遲才有)。"""

    at: str
    task: str | None
    by_lookup: bool = False
    value: float = 0.0


def _iso(moment: datetime) -> str:
    """跟執行端、分析端寫進紀錄的同一種固定格式 UTC 字串:字串比較就是時間比較。"""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _ident(event: LifecycleEvent) -> Ident:
    return event.task_id, event.revision, event.content_hash or ""


def _labels(stage: Stage, *pairs: tuple[LabelKind, str | None]) -> tuple[Label, ...]:
    """標籤依種類的宣告順序排,值為空的那一種不帶;階段每個樣本都帶。"""
    found = dict(pairs)
    found[K.STAGE] = stage.value
    return tuple((kind, value) for kind in LabelKind
                 if (value := found.get(kind)) is not None)


def _closed(value: str | None, members: frozenset[str]) -> str:
    return value if value is not None and value in members else OTHER


_BLOCK_CODES = frozenset(code.value for code in BlockCode)
_CALL_KINDS = frozenset(kind.value for kind in DspCallKind)
_CALL_RESULTS = frozenset(result.value for result in DspCallResult)
_ENDPOINTS = frozenset(endpoint.value for endpoint in ToolEndpoint)
_MODEL_CALLERS = frozenset(caller.value for caller in Caller)
_MODEL_OUTCOMES = frozenset(outcome.value for outcome in Outcome) | {UNSETTLED}
_MODEL_SOURCES = frozenset(source.value for source in Source)


def _reason(value: str | None) -> str:
    return _closed(value, _BLOCK_CODES)


# ---- 標籤值 ----
@dataclass(frozen=True)
class _Resolver:
    tenants: frozenset[str]
    owners: Mapping[str, str]  # 廣告 -> 租戶(查詢當下的設定)
    versions: frozenset[str]  # 留下來的程式版本;其他併成其他

    def tenant(self, recorded: str | None, campaign: str | None) -> tuple[str, bool]:
        """紀錄上有租戶就用它(不在現在的設定裡歸未知);沒記的用廣告反查,並標明是反查的。"""
        if recorded is not None:
            return (recorded if recorded in self.tenants else UNKNOWN_TENANT), False
        return self.owners.get(campaign or "", UNKNOWN_TENANT), True

    def version(self, value: str | None) -> str:
        return value if value in self.versions else OTHER

    @staticmethod
    def policy(value: str | None) -> str:
        """政策版本標籤是版本字串本身(在只增不刪的已知清單裡才算),不跟查詢當下的目前版本比:改版後
        同一個過去的窗標籤不變(代碼審第 1 輪,代使用者裁定)。"""
        return value if value is not None and value in KNOWN_POLICY_VERSIONS else OTHER


def _resolver(tenants: Sequence[Tenant], seen: Iterable[tuple[str | None, str]]) -> _Resolver:
    """程式版本依窗內最後出現的時間排(版本字串不保證可排序),留最新的 20 種。"""
    last: dict[str, str] = {}
    for version, at in seen:
        if version is not None and at > last.get(version, ""):
            last[version] = at
    kept = sorted(last, key=lambda v: (last[v], v), reverse=True)[:MAX_PROGRAM_VERSIONS]
    return _Resolver(frozenset(t.name for t in tenants),
                     {c: t.name for t in tenants for c in t.campaigns}, frozenset(kept))


# ---- 樣本組裝 ----
def _exemplars(members: Iterable[_Member], slowest: bool = False) -> tuple[str, ...]:
    """比率與計數取分子裡時間最新的;延遲取最慢的(耗時相同取結束時間最新的)。同一任務只列一次。"""
    order = sorted(members, key=(lambda x: (x.value, x.at)) if slowest else (lambda x: x.at),
                   reverse=True)
    found: list[str] = []
    for member in order:
        if member.task is not None and member.task not in found:
            found.append(member.task)
    return tuple(found[:MAX_EXEMPLARS])


def _counts(
    name: str, stage: Stage, groups: Mapping[tuple[Label, ...], list[_Member]],
    note: str | None = None,
) -> list[Sample]:
    if not groups:
        return [Sample(name, _labels(stage), 0, 0, numerator=0, note=note)]
    return [Sample(name, labels, len(members), len(members), numerator=len(members),
                   exemplars=_exemplars(members),
                   tenant_by_lookup=sum(x.by_lookup for x in members), note=note)
            for labels, members in sorted(groups.items())]


def _rates(
    name: str, stage: Stage,
    population: Mapping[tuple[Label, ...], Sequence[_Member]],
    hits: Mapping[tuple[tuple[Label, ...], tuple[Label, ...]], Sequence[_Member]],
) -> list[Sample]:
    """population:分組標籤 -> 母體;hits:(分組標籤, 類別標籤) -> 分子。分組有母體但某類別沒有分子的
    不出樣本(只報出現過的類別);沒有任何母體回一個無樣本。"""
    if not population:
        return [Sample(name, _labels(stage), None, 0, Status.NO_SAMPLES)]
    samples = []
    for (group, category), members in sorted(hits.items()):
        total = population[group]
        labels = tuple(sorted((*group, *category), key=lambda pair: list(LabelKind).index(pair[0])))
        samples.append(Sample(name, labels, len(members) / len(total), len(total),
                              numerator=len(members), exemplars=_exemplars(members),
                              tenant_by_lookup=sum(x.by_lookup for x in total)))
    for group, total in sorted(population.items()):
        if not any(g == group for g, _ in hits):  # 有母體、分子為 0
            samples.append(Sample(name, group, 0.0, len(total), numerator=0,
                                  tenant_by_lookup=sum(x.by_lookup for x in total)))
    return samples


def _nearest_rank(ordered: Sequence[float], percentile: int) -> float:
    return ordered[max(1, math.ceil(percentile / 100 * len(ordered))) - 1]


def _latencies(
    name: str, stage: Stage, groups: Mapping[tuple[Label, ...], Sequence[_Member]],
) -> list[Sample]:
    if not groups:
        return [Sample(f"{name}.{stat}", _labels(stage), None, 0, Status.NO_SAMPLES)
                for stat, _ in _STATS]
    samples = []
    for labels, members in sorted(groups.items()):
        ordered = sorted(member.value for member in members)
        exemplars = _exemplars(members, slowest=True)
        lookups = sum(x.by_lookup for x in members)
        samples += [Sample(f"{name}.{stat}", labels, _nearest_rank(ordered, percentile),
                           len(ordered), exemplars=exemplars, tenant_by_lookup=lookups)
                    for stat, percentile in _STATS]
    return samples


def _seconds(start: str, end: str) -> float:
    return (_time(end) - _time(start)).total_seconds()


# ---- 讀:執行端一個快照 ----
@dataclass(frozen=True)
class _ExecutorPart:
    events: tuple[LifecycleEvent, ...]  # 窗內事件
    histories: Mapping[Ident, tuple[LifecycleEvent, ...]]  # 窗內事件涉及的提案的全部事件
    task_revisions: Mapping[str, int]  # 窗內事件涉及的任務,事件裡出現過的最新修訂
    finals: Mapping[Ident, LifecycleEvent]  # 窗內有終點事件的提案,全域最後一個終點事件
    calls: tuple[DspCallRow, ...]  # 只讀端到端的那幾輪是空的
    reconciled: tuple[tuple[TerminalRow, tuple[AttemptTraceRow, ...]], ...]  # 同上


def _read_executor(path: Path, since: datetime, until: datetime, *,
                   full: bool = True) -> _ExecutorPart:
    """執行端一個快照。full 為假時只讀端到端要的(窗內事件、涉及任務的事件、最後終點),
    給重讀的那幾輪。"""
    inbox = ReadOnlyInbox(path)
    try:
        with inbox.read_transaction() as tx:
            events = inbox.lifecycle_events_between(tx, since, until)
            by_task = {task: inbox.lifecycle_events(tx, task)
                       for task in sorted({e.task_id for e in events})}
            finals = {}
            for ident in sorted({_ident(e) for e in events if e.kind in TERMINAL_KINDS}):
                # 查法:窗內的候選逐一用終點部分索引取全域最後一個,工作量跟候選數成正比
                last = inbox.last_terminal_event(tx, *ident)
                if last is not None:
                    finals[ident] = last
            calls = attempt_store.dsp_calls_between(tx, since, until) if full else ()
            reconciled = tuple((row, attempt_store.trace_rows(tx, row.key))
                               for row in (attempt_store.terminal_rows_between(tx, since, until)
                                           if full else ()))
    finally:
        inbox.close()
    histories: dict[Ident, list[LifecycleEvent]] = defaultdict(list)
    revisions: dict[str, int] = {}
    for task, found in by_task.items():
        for event in found:
            histories[_ident(event)].append(event)
            revisions[task] = max(revisions.get(task, event.revision), event.revision)
    return _ExecutorPart(events, {k: tuple(v) for k, v in histories.items()}, revisions, finals,
                         calls, reconciled)


# ---- 讀:分析端一個快照 ----
@dataclass(frozen=True)
class _Chain:
    root: str
    last: str
    created: str | None  # 根任務在分析端建立的時間;分析端沒有這個任務為空(接不上)


@dataclass(frozen=True)
class _AnalyzerPart:
    tool_calls: tuple[ToolCall, ...]
    chains: Mapping[str, _Chain]


def _chain(reader: TaskReader, task: str) -> _Chain:
    root = task
    for _ in range(MAX_CHAIN):
        parent = reader.follow_up_of(root)
        if parent is None:
            break
        root = parent
    last = root
    for _ in range(MAX_CHAIN):
        child = reader.follow_up_to(last)
        if child is None or child == root:
            break
        last = child
    history = reader.history(root)
    created = _iso(history[0].written_at) if history else None
    return _Chain(root, last, created)


def _read_analyzer(path: Path, since: datetime, until: datetime,
                   tasks: Iterable[str], *, calls: bool = True) -> _AnalyzerPart:
    """分析端一個快照。calls 為假時只讀接續鏈(端到端重讀的那幾輪),對外呼叫只在第一輪讀一次。"""
    reader = TaskReader(path)
    try:
        return _AnalyzerPart(reader.tool_calls_between(since, until) if calls else (),
                             {task: _chain(reader, task) for task in sorted(set(tasks))})
    finally:
        reader.close()


# ---- 算:窗內統計 ----
@dataclass
class _Window:
    since: str
    until: str
    part: _ExecutorPart
    analyzer: _AnalyzerPart
    labeler: _Resolver
    executions: Sequence[tuple[LifecycleEvent, LifecycleEvent]]  # 窗內的執行端處理段(開始, 終點)
    end_to_end: Sequence[Sample]  # 端到端另外讀到穩定為止,這裡只把樣本放進清單
    samples: list[Sample] = field(default_factory=list)

    def inside(self, at: str) -> bool:
        return self.since <= at < self.until

    def tenant_of(self, event: LifecycleEvent) -> tuple[str, bool]:
        return self.labeler.tenant(event.tenant, event.campaign_id)

    def member(self, event: LifecycleEvent, value: float = 0.0) -> _Member:
        return _Member(event.at, event.task_id, self.tenant_of(event)[1], value)


def _event_counts(w: _Window) -> None:
    """擋下(依原因)、進待核可、核可放回、過時決策拒絕(版本已變、政策已變、決策已過時三種執行前
    擋下)、擋掉的重複(依既有結果確認為是):依事件時間歸窗。

    前四項是生命週期事件的「事件次數」(同一份提案重投再擋算兩次),刻意不呼叫可觀測查詢的停下紀錄:
    那邊同一份提案同一種類只記一次,數的是提案數,定義不同;樣本附註寫明(代碼審第 1 輪)。"""
    groups: dict[str, dict[tuple[Label, ...], list[_Member]]] = defaultdict(
        lambda: defaultdict(list))
    for event in w.part.events:
        tenant = (K.TENANT, w.tenant_of(event)[0])
        reason = (K.BLOCK_REASON, _reason(event.reason))
        if event.kind == LifecycleKind.BLOCKED:
            groups["blocked"][_labels(Stage.EXECUTION, tenant, reason)].append(w.member(event))
            if event.reason in STALE_REASONS:
                groups["stale_rejections"][_labels(Stage.EXECUTION, tenant, reason)].append(
                    w.member(event))
        elif event.kind == LifecycleKind.AWAITING_APPROVAL:
            groups["awaiting_approval"][_labels(Stage.APPROVAL, tenant, reason)].append(
                w.member(event))
        elif event.kind == LifecycleKind.APPROVAL_RELEASED:
            groups["approval_released"][_labels(Stage.APPROVAL, tenant, reason)].append(
                w.member(event))
        if event.from_existing:
            groups["duplicates_prevented"][_labels(Stage.EXECUTION, tenant)].append(
                w.member(event))
    for name, stage in (("blocked", Stage.EXECUTION), ("awaiting_approval", Stage.APPROVAL),
                        ("approval_released", Stage.APPROVAL),
                        ("stale_rejections", Stage.EXECUTION),
                        ("duplicates_prevented", Stage.EXECUTION)):
        w.samples += _counts(name, stage, groups[name],
                             None if name == "duplicates_prevented" else EVENT_COUNT_NOTE)


def _terminal_event_rate(w: _Window) -> None:
    """事件型:母體是窗內的終點事件,依租戶分組;類別是終點種類、擋下原因(擋下才有)與政策版本。"""
    population: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    hits: dict[tuple[tuple[Label, ...], tuple[Label, ...]], list[_Member]] = defaultdict(list)
    for event in w.part.events:
        if event.kind not in TERMINAL_KINDS:
            continue
        group = _labels(Stage.EXECUTION, (K.TENANT, w.tenant_of(event)[0]))
        category = ((K.TERMINAL_KIND, event.kind),
                    *(((K.BLOCK_REASON, _reason(event.reason)),)
                      if event.kind == LifecycleKind.BLOCKED else ()),
                    (K.POLICY_VERSION, w.labeler.policy(event.policy_version)))
        population[group].append(w.member(event))
        hits[group, category].append(w.member(event))
    w.samples += _rates("terminal_event_rate", Stage.EXECUTION, population, hits)


def _final_outcome_rate(w: _Window) -> None:
    """依查詢當下最終結果:每份提案只算一次,先取它全域最後一個終點事件,再看落不落在窗內(重放之後
    會變)。依租戶分組;類別是終點種類與擋下原因。"""
    population: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    hits: dict[tuple[tuple[Label, ...], tuple[Label, ...]], list[_Member]] = defaultdict(list)
    for final in w.part.finals.values():
        if not w.inside(final.at):
            continue
        group = _labels(Stage.EXECUTION, (K.TENANT, w.tenant_of(final)[0]))
        category = ((K.TERMINAL_KIND, final.kind),
                    *(((K.BLOCK_REASON, _reason(final.reason)),)
                      if final.kind == LifecycleKind.BLOCKED else ()))
        population[group].append(w.member(final))
        hits[group, category].append(w.member(final))
    w.samples += _rates("final_outcome_rate", Stage.EXECUTION, population, hits)


def _per_proposal_rate(
    w: _Window, name: str, stage: Stage, in_population: frozenset[str],
    counts: Callable[[LifecycleEvent], bool],
) -> None:
    """母體是窗內有 in_population 種類事件的提案(依租戶分組);counts(事件) 為真的算進分子。"""
    first: dict[Ident, LifecycleEvent] = {}
    hit: dict[Ident, LifecycleEvent] = {}
    for event in w.part.events:
        if event.kind in in_population:
            first.setdefault(_ident(event), event)
        if counts(event):
            hit[_ident(event)] = event
    population: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    hits: dict[tuple[tuple[Label, ...], tuple[Label, ...]], list[_Member]] = defaultdict(list)
    for ident, event in first.items():
        group = _labels(stage, (K.TENANT, w.tenant_of(event)[0]))
        population[group].append(w.member(event))
        if ident in hit:
            hits[group, ()].append(w.member(hit[ident]))
    w.samples += _rates(name, stage, population, hits)


def _redelivery_and_dead_letter_rates(w: _Window) -> None:
    # 重投率:母體是窗內有取件或租約過期被接手事件的提案;窗內這類事件的投遞次數大於 1 就算重投
    _per_proposal_rate(w, "redelivery_rate", Stage.QUEUE, _DELIVERIES,
                       lambda e: e.kind in _DELIVERIES and (e.deliveries or 0) > 1)
    # 死信率:母體是窗內有終點事件的提案;窗內真的發生過死信就算(重放後成功照樣算)
    _per_proposal_rate(w, "dead_letter_rate", Stage.QUEUE, TERMINAL_KINDS,
                       lambda e: e.kind == LifecycleKind.DEAD_LETTERED)


def _intervals(
    history: Sequence[LifecycleEvent], opens: str, closes: frozenset[str],
) -> list[tuple[LifecycleEvent, LifecycleEvent]]:
    """一份提案的事件裡,從 opens 種類到下一個 closes 種類的每一段(沒結束的不算)。"""
    found, start = [], None
    for event in history:
        if event.kind == opens and start is None:
            start = event
        elif event.kind in closes and start is not None:
            found.append((start, event))
            start = None
    return found


_APPROVAL_ENDS = frozenset({LifecycleKind.APPROVAL_RELEASED, *TERMINAL_KINDS})
_DEAD_LETTER_ENDS = frozenset({LifecycleKind.REPLAY_REQUEUED})


def _waits(w: _Window) -> None:
    """佇列等待(收件 → 第一次取件)、人工核可等待(進待核可 → 核可放回或到期)、死信等待(進死信 →
    重放放回):依結束時間歸窗。"""
    queue: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    approval: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    dead: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    for history in w.part.histories.values():
        received = next((e for e in history if e.kind == LifecycleKind.RECEIVED), None)
        delivered = next((e for e in history if e.kind in _DELIVERIES), None)
        if received is not None and delivered is not None and w.inside(delivered.at):
            tenant = (K.TENANT, w.tenant_of(delivered)[0])
            queue[_labels(Stage.QUEUE, tenant)].append(
                w.member(delivered, _seconds(received.at, delivered.at)))
        for start, end in _intervals(history, LifecycleKind.AWAITING_APPROVAL, _APPROVAL_ENDS):
            if w.inside(end.at):
                labels = _labels(Stage.APPROVAL, (K.TENANT, w.tenant_of(end)[0]),
                                 (K.BLOCK_REASON, _reason(start.reason)))
                approval[labels].append(w.member(end, _seconds(start.at, end.at)))
        for start, end in _intervals(history, LifecycleKind.DEAD_LETTERED, _DEAD_LETTER_ENDS):
            if w.inside(end.at):
                dead[_labels(Stage.QUEUE, (K.TENANT, w.tenant_of(end)[0]))].append(
                    w.member(end, _seconds(start.at, end.at)))
    w.samples += _latencies("queue_wait_seconds", Stage.QUEUE, queue)
    w.samples += _latencies("approval_wait_seconds", Stage.APPROVAL, approval)
    w.samples += _latencies("dead_letter_wait_seconds", Stage.QUEUE, dead)


def _overlap(start: str, end: str, spans: Iterable[tuple[str, str]]) -> float:
    total = 0.0
    for left, right in spans:
        low, high = max(start, left), min(end, right)
        if low < high:
            total += _seconds(low, high)
    return total


def _segments(history: Sequence[LifecycleEvent]) -> list[tuple[LifecycleEvent, LifecycleEvent]]:
    """一份提案的執行端處理段:從第一次取件(或最近一次重放放回之後的第一次取件)到下一個終點事件。
    每個終點事件各自結束一段;終點之後要等重放放回後再取件才開新的一段(沒開始的終點不成段)。"""
    found: list[tuple[LifecycleEvent, LifecycleEvent]] = []
    start: LifecycleEvent | None = None
    for event in history:
        if event.kind in _DELIVERIES and start is None:
            start = event
        elif event.kind == LifecycleKind.REPLAY_REQUEUED:
            start = None
        elif event.kind in TERMINAL_KINDS:
            if start is not None:
                found.append((start, event))
            start = None
    return found


def _executions(part: _ExecutorPart, since: str, until: str,
                ) -> list[tuple[LifecycleEvent, LifecycleEvent]]:
    """窗內實際出現的每個終點事件各成一段,依那個終點事件的時間歸窗(代碼審第 1 輪:原本用查詢當下的
    全域最後終點,窗一死信、窗外重放成功後,窗一的樣本會消失)。"""
    return [(start, end) for history in part.histories.values()
            for start, end in _segments(history) if since <= end.at < until]


def _execution(w: _Window) -> None:
    """執行端處理:每一段扣掉段內的人工核可等待與死信等待;分組是租戶與終點事件的程式版本。"""
    groups: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    for start, end in w.executions:
        history = w.part.histories.get(_ident(end), ())
        waits = [(s.at, e.at) for s, e in
                 _intervals(history, LifecycleKind.AWAITING_APPROVAL, _APPROVAL_ENDS)
                 + _intervals(history, LifecycleKind.DEAD_LETTERED, _DEAD_LETTER_ENDS)]
        spent = _seconds(start.at, end.at) - _overlap(start.at, end.at, waits)
        labels = _labels(Stage.EXECUTION, (K.TENANT, w.tenant_of(end)[0]),
                         (K.PROGRAM_VERSION, w.labeler.version(end.program_version)))
        groups[labels].append(w.member(end, spent))
    w.samples += _latencies("execution_seconds", Stage.EXECUTION, groups)


def _reconciliation(w: _Window) -> None:
    """對帳:一把鍵第一次進結果不明 → 那把鍵結案(依結案時間歸窗);報耗時與成功比率(結案為已驗證)。
    分組是租戶與結案那一列的程式版本。"""
    times: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    population: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    hits: dict[tuple[tuple[Label, ...], tuple[Label, ...]], list[_Member]] = defaultdict(list)
    for terminal, history in w.part.reconciled:
        unknown = next((r for r in history if r.state == AttemptState.UNKNOWN), None)
        if unknown is None or not history:
            continue
        tenant, by_lookup = w.labeler.tenant(history[0].tenant, history[0].campaign_id)
        labels = _labels(Stage.RECONCILIATION, (K.TENANT, tenant),
                         (K.PROGRAM_VERSION, w.labeler.version(terminal.program_version)))
        member = _Member(terminal.written_at, history[0].task_id, by_lookup,
                         _seconds(unknown.written_at, terminal.written_at))
        times[labels].append(member)
        population[labels].append(member)
        if terminal.state == AttemptState.VERIFIED:
            hits[labels, ()].append(member)
    w.samples += _latencies("reconciliation_seconds", Stage.RECONCILIATION, times)
    w.samples += _rates("reconciliation_success_rate", Stage.RECONCILIATION, population, hits)


@dataclass(frozen=True)
class _EndToEnd:
    """端到端的完整樣本(每筆的任務、結束時間與數值)。穩定判定逐輪比它,不比樣本數、分位數、前三個
    範例這種有損的摘要:不同任務可能剛好摘要相同(代碼審第 3 輪)。"""

    samples: tuple[_Member, ...]
    anomalies: tuple[_Member, ...]
    unlinked: tuple[_Member, ...]


def _end_to_end_members(since: str, until: str, part: _ExecutorPart,
                        chains: Mapping[str, _Chain]) -> _EndToEnd:
    """端到端(依查詢當下最終結果):沿接續關係回溯到根任務,從根任務在分析端建立 → 鏈上最後一個
    任務最新修訂的最終終點;每條鏈在窗內只算一次,被取代的修訂與非鏈尾的任務不另成樣本。分析端
    沒有這個任務的另報接不上;算出負值(兩邊時鐘不同步)不計入、另報時鐘異常。只看這兩份輸入,
    給讀到穩定為止的那幾輪逐輪比。"""
    ends: dict[str, tuple[_Chain, LifecycleEvent]] = {}
    unlinked: dict[str, LifecycleEvent] = {}
    for (task, revision, _), final in sorted(part.finals.items()):
        if not since <= final.at < until:
            continue
        chain = chains.get(task)
        if chain is None or chain.created is None:
            unlinked[task] = final
            continue
        if task != chain.last or revision != part.task_revisions.get(task, revision):
            continue
        seen = ends.get(chain.root)
        if seen is None or final.at > seen[1].at:
            ends[chain.root] = (chain, final)
    samples: list[_Member] = []
    anomalies: list[_Member] = []
    for chain, final in ends.values():
        assert chain.created is not None  # noqa: S101 - 上面已把沒有建立時間的歸成接不上
        spent = _seconds(chain.created, final.at)
        (anomalies if spent < 0 else samples).append(_Member(final.at, final.task_id, False, spent))
    return _EndToEnd(_in_order(samples), _in_order(anomalies),
                     _in_order(_Member(e.at, e.task_id) for e in unlinked.values()))


def _in_order(members: Iterable[_Member]) -> tuple[_Member, ...]:
    return tuple(sorted(members, key=lambda x: (x.at, x.task or "")))


def _end_to_end_samples(ends: _EndToEnd) -> tuple[Sample, ...]:
    stage = _labels(Stage.ANALYSIS)
    found = _latencies("end_to_end_seconds", Stage.ANALYSIS,
                       {stage: ends.samples} if ends.samples else {})
    for name, members in (("end_to_end_unlinked", ends.unlinked),
                          ("end_to_end_clock_anomaly", ends.anomalies)):
        found.append(Sample(name, stage, len(members), len(members), numerator=len(members),
                            exemplars=_exemplars(members)))
    return tuple(found)


def _dsp(w: _Window) -> None:
    """DSP 呼叫(依呼叫時間歸窗;租戶一律用廣告以查詢當下的設定反查,呼叫紀錄沒有租戶欄)。
    - 次數:依租戶、呼叫類別、結果類別、程式版本。
    - 比率:依呼叫類別分組(分母是同一呼叫類別的窗內呼叫數),依結果類別切分子。
    - 耗時:依呼叫類別與程式版本。
    - 版本衝突率:窗內寫入呼叫(依租戶與程式版本分組)中,錯誤代碼是版本衝突的比率;分子分母同一批。"""
    counts: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    kinds: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    results: dict[tuple[tuple[Label, ...], tuple[Label, ...]], list[_Member]] = defaultdict(list)
    latency: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    writes: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    conflicts: dict[tuple[tuple[Label, ...], tuple[Label, ...]], list[_Member]] = defaultdict(list)
    for call in w.part.calls:
        tenant, by_lookup = w.labeler.tenant(None, call.campaign_id)
        kind = (K.DSP_CALL_KIND, _closed(call.kind, _CALL_KINDS))
        result = (K.DSP_CALL_RESULT, _closed(call.result, _CALL_RESULTS))
        version = (K.PROGRAM_VERSION, w.labeler.version(call.program_version))
        member = _Member(call.at, call.task_id, by_lookup, call.latency_ms)
        counts[_labels(Stage.EXECUTION, (K.TENANT, tenant), kind, result, version)].append(member)
        group = _labels(Stage.EXECUTION, kind)
        kinds[group].append(member)
        results[group, (result,)].append(member)
        latency[_labels(Stage.EXECUTION, kind, version)].append(member)
        if call.kind == DspCallKind.WRITE:
            write_group = _labels(Stage.EXECUTION, (K.TENANT, tenant), version)
            writes[write_group].append(member)
            if call.error == DspErrorCode.VERSION_CONFLICT:
                conflicts[write_group, ()].append(member)
    w.samples += _counts("dsp_calls", Stage.EXECUTION, counts)
    w.samples += _rates("dsp_call_rate", Stage.EXECUTION, kinds, results)
    w.samples += _latencies("dsp_call_latency_ms", Stage.EXECUTION, latency)
    w.samples += _rates("version_conflict_rate", Stage.EXECUTION, writes, conflicts)


def _analyzer_calls(w: _Window) -> None:
    """分析端對外呼叫:依端點的次數與耗時(毫秒),依呼叫時間歸窗。"""
    groups: dict[tuple[Label, ...], list[_Member]] = defaultdict(list)
    for call in w.analyzer.tool_calls:
        at = _iso(call.at)
        endpoint = _closed(call.endpoint.value, _ENDPOINTS)
        groups[_labels(Stage.ANALYSIS, (K.ANALYZER_ENDPOINT, endpoint))].append(
            _Member(at, call.task_id, False, call.latency_ms))
    w.samples += _counts("analyzer_calls", Stage.ANALYSIS, groups)
    w.samples += _latencies("analyzer_call_latency_ms", Stage.ANALYSIS, groups)


def _add_end_to_end(w: _Window) -> None:
    w.samples += w.end_to_end


def _compute_window(
    since: datetime, until: datetime, part: _ExecutorPart, analyzer: _AnalyzerPart,
    tenants: Sequence[Tenant], end_to_end: Sequence[Sample],
) -> tuple[Sample, ...]:
    start, end = _iso(since), _iso(until)
    executions = _executions(part, start, end)
    # 程式版本的值域只收窗內真的貢獻帶版本樣本的紀錄(代碼審第 1 輪:原本連窗外的全域最後終點也收,
    # 窗外重放一次就能把窗內版本擠成其他)
    seen: list[tuple[str | None, str]] = [(e.program_version, e.at) for _, e in executions]
    seen += [(c.program_version, c.at) for c in part.calls]
    seen += [(row.program_version, row.written_at) for row, history in part.reconciled
             if any(r.state == AttemptState.UNKNOWN for r in history)]
    w = _Window(start, end, part, analyzer, _resolver(tenants, seen), executions, end_to_end)
    for step in (_terminal_event_rate, _final_outcome_rate, _redelivery_and_dead_letter_rates,
                 _waits, _execution, _reconciliation, _add_end_to_end, _dsp, _analyzer_calls,
                 _event_counts):
        step(w)
    return tuple(w.samples)


def _model_samples(model_ledger: Path | None, since: datetime,
                   until: datetime) -> list[Sample]:
    """模型與 Jev:經花費帳唯讀開法讀窗內的呼叫(依預留時間),依呼叫者、結果類別、來源數次數
    ([S907])。"""
    if model_ledger is None:
        return [Sample("model_and_jev", (), None, 0, Status.NO_SAMPLES, note=NO_LEDGER_NOTE)]
    try:
        reader = ModelLedgerView(model_ledger)
    except FileNotFoundError:
        return [Sample("model_and_jev", (), None, 0, Status.NO_SAMPLES, note=MISSING_LEDGER_NOTE)]
    try:
        with reader.read_transaction():
            calls = reader.calls_between(
                since.astimezone(UTC).isoformat(timespec="microseconds"),
                until.astimezone(UTC).isoformat(timespec="microseconds"))
    finally:
        reader.close()
    counts: dict[tuple[Label, ...], int] = defaultdict(int)
    for call in calls:
        counts[_labels(Stage.ANALYSIS, (K.MODEL_CALLER, _closed(call.caller, _MODEL_CALLERS)),
                       (K.MODEL_OUTCOME, _closed(call.outcome or UNSETTLED, _MODEL_OUTCOMES)),
                       (K.MODEL_SOURCE, _closed(call.source, _MODEL_SOURCES)))] += 1
    if not counts:
        return [Sample("model_and_jev", _labels(Stage.ANALYSIS), None, 0, Status.NO_SAMPLES,
                       note=MODEL_NOTE)]
    return [Sample("model_and_jev", labels, count, count, note=MODEL_NOTE)
            for labels, count in sorted(counts.items())]


def _check_window(since: datetime, until: datetime) -> None:
    require_aware(since, until)  # 時間一律要帶時區(領域層共用的檢查)
    if until <= since:
        raise WindowTooLong("窗的迄必須在起之後")
    if until - since > MAX_WINDOW:
        raise WindowTooLong("窗不得超過 24 小時")


def collect_window(
    since: datetime, until: datetime, *, executor_db: Path, analyzer_db: Path,
    tenants: Sequence[Tenant], model_ledger: Path | None = None,
) -> Report:
    """窗內統計。報告的每一個數字(含端到端)都出自第一輪讀到的執行端與分析端輸入。端到端跨兩個資料庫,
    之後再重讀兩輪,只用來確認端到端的完整樣本跟第一輪一致:每一輪都一致才標穩定;任何一輪不同就停、
    標不穩定,照樣回第一輪的結果——不改採較新的快照(代碼審第 2 輪:改採較新的會讓同一份報告端到端
    有、終點事件率沒有);第二輪不同、第三輪又回到第一輪也不算(第 3 輪:讀取期間資料已經動過)。
    時間沒帶時區丟 ValueError(訊息不含範例,範例只在命令列那層);資料庫檔不存在丟
    FileNotFoundError;還沒升級丟 DatabaseNotUpgraded(都不寫任何東西)。"""
    _check_window(since, until)
    start, end = _iso(since), _iso(until)
    part = _read_executor(Path(executor_db), since, until)
    analyzer = _read_analyzer(Path(analyzer_db), since, until,
                              (task for task, _, _ in part.finals))
    first = _end_to_end_members(start, end, part, analyzer.chains)
    samples = (*_compute_window(since, until, part, analyzer, tenants,
                                _end_to_end_samples(first)),
               *_model_samples(model_ledger, since, until))
    for rounds in range(2, MAX_ROUNDS + 1):
        ends = _read_executor(Path(executor_db), since, until, full=False)
        chains = _read_analyzer(Path(analyzer_db), since, until,
                                (task for task, _, _ in ends.finals), calls=False).chains
        if _end_to_end_members(start, end, ends, chains) != first:
            return Report(samples, False, rounds)
    return Report(samples, True, MAX_ROUNDS)


# ---- 算:現況快照 ----
def _entered(history: Sequence[AttemptRow]) -> datetime:
    """這把鍵進入目前狀態的時間:最後一段同狀態連續列的第一列。"""
    state, entered = history[-1].state, history[-1].written_at
    for row in reversed(history):
        if row.state != state:
            break
        entered = row.written_at
    return entered


def collect_snapshot(now: datetime, *, executor_db: Path, tenants: Sequence[Tenant]) -> Report:
    """現況快照:佇列現況、四種沒有終點的嘗試狀態、被未結案鍵鎖住的廣告數、總曝險使用率。不套時間窗。
    「現在」沒帶時區丟 ValueError(訊息不含範例,範例只在命令列那層)。"""
    require_aware(now)
    inbox = ReadOnlyInbox(Path(executor_db))
    try:
        with inbox.read_transaction() as tx:
            pending, oldest = inbox.pending_snapshot(tx)
            histories = [attempt_store.history(tx, key)
                         for key in attempt_store.unresolved_keys(tx)]
            locked = attempt_store.campaigns_with_unresolved(tx)
            usage = [(t, observability.utilization(tx, t.name, t.aggregate_limit, now))
                     for t in tenants]
    finally:
        inbox.close()
    queue = _labels(Stage.QUEUE)
    samples = [Sample("queue_pending", queue, pending, pending),
               Sample("queue_oldest_wait_seconds", queue, None, 0, Status.NO_SAMPLES)
               if oldest is None else
               Sample("queue_oldest_wait_seconds", queue, (now - _time(oldest)).total_seconds(),
                      pending)]
    reconciling = _labels(Stage.RECONCILIATION)
    for state, name in _UNRESOLVED_NAMES.items():
        ages = [(now - _entered(h)).total_seconds()
                for h in histories if h and h[-1].state == state]
        samples.append(Sample(name, reconciling, len(ages), len(ages)))
        samples.append(Sample(f"{name}_max_age_seconds", reconciling, max(ages), len(ages))
                       if ages else Sample(f"{name}_max_age_seconds", reconciling, None, 0,
                                           Status.NO_SAMPLES))
    samples.append(Sample("locked_campaigns", reconciling, len(locked), len(locked)))
    for tenant, used in usage:
        labels = _labels(Stage.EXECUTION, (K.TENANT, tenant.name))
        samples.append(Sample("aggregate_utilization", labels, used.used / used.limit, used.limit,
                              numerator=used.used) if used.limit > 0 else
                       Sample("aggregate_utilization", labels, None, 0, Status.NO_SAMPLES))
    return Report(tuple(samples), True, 1)


# ---- 輸出與命令列入口 ----
def to_primitives(result: Report) -> dict[str, Any]:
    return {"stable": result.stable, "rounds": result.rounds,
            "note": None if result.stable else "讀取期間有新提交,數字可能不一致",
            "samples": [{"name": s.name, "labels": s.label_map(), "value": s.value,
                         "count": s.count, "status": s.status.value, "numerator": s.numerator,
                         "exemplars": list(s.exemplars), "tenant_by_lookup": s.tenant_by_lookup,
                         "note": s.note} for s in result.samples]}



def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(description="有界標籤的指標(只讀)")
    parser.add_argument("--executor-db", required=True, type=Path)
    parser.add_argument("--analyzer-db", type=Path, help="窗內統計才要")
    parser.add_argument("--tenants-config", required=True, type=Path)
    parser.add_argument("--since", type=aware_time)
    parser.add_argument("--until", type=aware_time)
    parser.add_argument("--now", type=aware_time, help="給了就是現況快照")
    parser.add_argument("--model-ledger", type=Path,
                        help="模型花費帳(窗內統計的模型與 Jev 指標用;預設家目錄那一本)")
    args = parser.parse_args(argv)
    if args.now is None and (args.since is None or args.until is None or args.analyzer_db is None):
        parser.error("窗內統計要 --since、--until 與 --analyzer-db;現況快照要 --now")
    return args


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        err: TextIO | None = None) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    try:
        if args.now is None:
            _check_window(args.since, args.until)  # 先驗窗,不必先讀設定檔
        tenants = load_tenants(args.tenants_config)
        result = (collect_snapshot(args.now, executor_db=args.executor_db, tenants=tenants)
                  if args.now is not None else
                  collect_window(args.since, args.until, executor_db=args.executor_db,
                                 analyzer_db=args.analyzer_db, tenants=tenants,
                                 model_ledger=args.model_ledger or ledger_path()))
    except WindowTooLong as refused:
        print(f"拒絕:{refused}(窗內統計最長 24 小時)", file=errors)
        return EXIT_WINDOW_TOO_LONG
    except SigningRefused as bad:
        print(f"租戶設定檔讀不了:{bad}", file=errors)
        return EXIT_BAD_CONFIG
    except FileNotFoundError as missing:
        print(f"找不到資料庫檔:{missing}", file=errors)
        return EXIT_NO_DATABASE
    except DatabaseNotUpgraded as old:
        print(f"{old}。請先啟動一次執行迴圈(分析端資料庫則先跑一次分析行程),讓它補上新表與"
              "新欄位;指標只讀,不替它補", file=errors)
        return EXIT_NOT_UPGRADED
    print(json.dumps(to_primitives(result), ensure_ascii=False, indent=2), file=out or sys.stdout)
    if not result.stable:
        print("讀取期間有新提交:重讀的端到端跟第一輪不同,印出的照樣是第一輪的數字", file=errors)
        return EXIT_UNSTABLE
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
