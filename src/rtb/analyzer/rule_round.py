"""正式規則的規則輪(Phase 14 增量 2b,計劃
[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈正式蒐證、時間與
租約〉,[S1401] [S1402] [S1405] [S1406] [S1413] [S1424]):九條要四種追加查詢,一步塞不下租約,
所以分三步讀。

- 步驟 A 讀現況與 1 小時指標(2 讀);只用基本資料判得出的(過期、缺現況、暫停、1 小時異常、配速算不出或
  不偏低)在 A 就結案,追加讀取 0 次([S1401])。
- 步驟 B 讀操作歷史與過去調整(2 讀);步驟 C 讀逐日、1 天、7 天,並重讀現況與 1 小時指標(5 讀)。
- 定案(DECIDE)在 C 之後:以決策 now 重驗三步全部證據的 15 分鐘新鮮度;A 與 C 比廣告編號、狀態、版本與
  1 小時五項原始指標;C 缺可信現況就以缺現況結案,不沿用 A 的([S1404]);四查詢從同一輪 B/C 已存的原始
  回應轉成領域型別,交給 `policy.explain` 判九條([S1402] [S1406])。
- 進度一律由已提交列重算([S1413]):取最新一輪;這一輪有非「續步」的事件、或任一步不是目前政策版本,就算
  已作廢,下一次蒐證開新輪從 A 讀([S1424])。同一步重複只取最新序號。不持久化「下一步」。
- 重來:證據過期、版本或基本資料變動、409 退回(定案事件已寫,這一輪不再用),都開新輪從 A 全量重讀;
  版本/基本資料變動連續重來最多 2 次,第 2 次重來後再變動就以證據不足結案(MAX_CHANGE_RESTARTS)。

進度與蒐證計畫是純函式(呼叫端先讀出已提交記錄,同 `investigation.progress` 的慣例);`decide` 讀一次
這件工作的規則輪記錄,再視需要讀各步證據與原始回應;不寫入、不讀時鐘。蒐證那一步怎麼打 DSP 在
`instrumented.rule_source`(C 步先讀查詢、最後重讀現況與 1 小時,讓 A/C 比對涵蓋查詢期間的變動)。
Phase 14 增量 3 起 AI 不參與加額決策:正式分析一律走這個規則輪,沒有 AI 開輪、AI 否決或 AI 期證據
(舊資料庫若有 AI 期的分析列,照「不屬於目前這一輪」作廢、從 A 重讀)。
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from rtb.analyzer import investigation as inv
from rtb.analyzer import policy
from rtb.analyzer.flow import (
    Decision,
    NeedsFreshEvidence,
    NoAction,
    RuleContinue,
    RuleOutcome,
)
from rtb.analyzer.task_store import RuleEvent, RuleStep, TaskReads, TaskRow
from rtb.domain import nine_rules as rules
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.domain.proposal import POLICY_VERSION
from rtb.stepbudget import RULE_STEP_READS

MAX_CHANGE_RESTARTS = 2  # 版本/基本資料變動連續重來的上限(計劃:容得下一次競爭後的穩定重讀)
# 1 小時基本資料裡 A 與 C 要逐欄相同的欄位(原始值,不經收據捨入)
COMPARED_STATE = ("id", "status", "version")
COMPARED_METRICS = ("impressions", "clicks", "conversions", "spend", "revenue")


class Step(StrEnum):
    A = "A"
    B = "B"
    C = "C"


# 每一步讀哪些追加查詢(A 只讀基本兩樣;C 另重讀基本兩樣)
STEP_QUERIES: Mapping[Step, tuple[inv.QueryOption, ...]] = {
    Step.A: (),
    Step.B: (inv.QueryOption.CHECK_CHANGE_HISTORY, inv.QueryOption.CHECK_PAST_ADJUSTMENTS),
    Step.C: (inv.QueryOption.CHECK_LONGER_WINDOW, inv.QueryOption.CHECK_DAILY_TREND),
}
READS_BASE = 2  # 現況與 1 小時指標
STEP_BASE: Mapping[Step, bool] = {Step.A: True, Step.B: False, Step.C: True}


def declared_reads(step: Step) -> int:
    """這一步實際打幾次 DSP(基本兩樣加各查詢的讀取次數);租約守衛宣告的次數必須等於它。"""
    return (READS_BASE if STEP_BASE[step] else 0) + sum(
        inv.READS_PER_OPTION[option] for option in STEP_QUERIES[step])


class Event(StrEnum):
    """規則輪事件(封閉列舉):續步、各種重來、定案。"""

    CONTINUE = "continue"  # 規則查詢續步(A→B、B→C),不是資料過期重蒐證
    RESTART_STALE = "restart_stale"  # 證據過期(含當機續跑超過 15 分鐘)
    RESTART_CHANGED = "restart_changed"  # A 與 C 的版本或基本資料不同
    RESTART_NO_ROUND = "restart_no_round"  # 分析中那一列不屬於目前這一輪(舊政策、舊兩讀、舊 AI 期)
    CHANGED_LIMIT = "changed_limit"  # 連續變動超過上限,以證據不足結案
    DECIDED = "decided"  # 定案(提案或不提案);之後 409 退回蒐證也不再用這一輪


@dataclass(frozen=True)
class Progress:
    """從已提交列算出來的規則輪進度。"""

    round_id: int  # 目前這一輪(沒有未作廢的輪時是下一輪要用的編號)
    open: bool  # 這一輪還能續
    steps: Mapping[Step, tuple[int, RuleStep]]  # 這一輪各步最新的(序號, 紀錄)
    changes: int  # 連續幾次因變動重來(最近的事件往回數)

    @property
    def next_step(self) -> Step | None:
        """下一步要讀哪一步;None 表示 A/B/C 齊全,該定案。沒有未作廢的輪就是 A(新輪)。"""
        if not self.open:
            return Step.A
        for step in Step:
            if step not in self.steps:
                return step
        return None


StepRows = Sequence[tuple[int, RuleStep]]
EventRows = Sequence[tuple[int, RuleEvent]]


def _current_rounds(step_rows: StepRows) -> set[int]:
    """每一步都是目前政策版本、步驟代碼認得的輪。"""
    versions: dict[int, bool] = {}
    for _seq, record in step_rows:
        ok = record.policy_version == POLICY_VERSION and record.step in Step.__members__
        versions[record.round_id] = versions.get(record.round_id, True) and ok
    return {round_id for round_id, ok in versions.items() if ok}


def progress(step_rows: StepRows, events: EventRows) -> Progress:
    """照已提交的規則輪步驟與事件重算進度:純函式,呼叫端先讀出記錄(同
    `investigation.progress` 的慣例,代碼審 r1 架構對齊-1)。連續變動重來的次數只數目前
    政策版本的輪,遇到別的事件或別的政策版本的輪就停([S1424]:升版後重來計數歸零,
    代碼審 r1 外家 finder-3)。"""
    current = _current_rounds(step_rows)
    closed = {event.round_id for _seq, event in events if event.event != Event.CONTINUE}
    changes = 0
    for _seq, event in reversed(events):
        if event.event == Event.CONTINUE:
            continue
        if event.event != Event.RESTART_CHANGED or event.round_id not in current:
            break
        changes += 1
    latest = max((record.round_id for _seq, record in step_rows), default=0)
    if latest == 0 or latest in closed or latest not in current:
        return Progress(latest + 1, False, {}, changes)
    steps: dict[Step, tuple[int, RuleStep]] = {}
    for seq, record in sorted(step_rows, key=lambda row: row[0]):  # 同一步重複時留最新
        if record.round_id == latest:
            steps[Step(record.step)] = (seq, record)
    return Progress(latest, True, steps, changes)


def _stale(read_at: datetime, now: datetime) -> bool:
    age = (now - read_at).total_seconds()
    return age < 0 or age > policy.MAX_EVIDENCE_AGE.total_seconds()


def collect_plan(step_rows: StepRows, events: EventRows, now: datetime) -> tuple[int, Step]:
    """蒐證那一步要讀哪一輪的哪一步(純函式):未作廢的輪續讀下一步;沒有未作廢的輪、這一輪已齊全
    (不該發生)、或先前步驟到 now 已過期(當機續跑超過 15 分鐘),就開新輪從 A 讀,舊輪只留審計。"""
    state = progress(step_rows, events)
    step = state.next_step
    if (step is None or step is Step.A or not state.open
            or any(_stale(record.read_at, now) for _seq, record in state.steps.values())):
        return (state.round_id + 1 if state.open else state.round_id), Step.A
    return state.round_id, step


# ---- 分析那一步 ----
def _trusted(evidence: Sequence[Evidence], kind: EvidenceKind) -> Mapping[str, Any] | None:
    for item in evidence:
        if item.kind is kind and item.trust_class is TrustClass.TRUSTED:
            return item.payload
    return None


def _base(evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    return tuple(item for item in evidence if item.kind in inv.CODE_RULE_KINDS)


def _receipts(evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    return tuple(item for item in evidence if item.kind in inv.OPTION_OF_KIND)


def _all_young(evidence: Sequence[Evidence], now: datetime) -> bool:
    """只看年齡的新鮮度(A 與 C 的版本另外比,版本不同是「變動」不是「過期」)。"""
    return not any(_stale(item.observed_at, now) for item in evidence)


def _changed(first: Sequence[Evidence], last: Sequence[Evidence]) -> bool:
    """A 與 C:廣告編號、狀態、版本、1 小時五項原始指標任一不同就是變動。"""
    a_state, c_state = _trusted(first, EvidenceKind.CAMPAIGN_STATE), _trusted(
        last, EvidenceKind.CAMPAIGN_STATE)
    a_metrics, c_metrics = _trusted(first, EvidenceKind.METRICS), _trusted(
        last, EvidenceKind.METRICS)
    if a_state is None or c_state is None or a_metrics is None or c_metrics is None:
        return True
    return (any(a_state.get(name) != c_state.get(name) for name in COMPARED_STATE)
            or any(a_metrics.get(name) != c_metrics.get(name) for name in COMPARED_METRICS))


def _raw(reads: TaskReads, task_id: str, seq: int, option: inv.QueryOption) -> Any:
    """同一輪那一步已存的原始回應(白名單驗過、跟收據同一個交易寫);沒有結果的查詢不存原始回應。"""
    text = reads.raw_query(task_id, seq, option.value)
    return None if text is None else json.loads(text)


def _window(raw: Mapping[str, Any]) -> rules.Window:
    return rules.Window(raw.get("impressions"), raw.get("clicks"), raw.get("conversions"),
                        raw.get("spend"), raw.get("revenue"))


def _moment(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def rule_queries(raws: Mapping[inv.QueryOption, Any], daily_read_at: datetime | None,
                 ) -> rules.RuleEvidence:
    """把白名單驗過的四種原始回應轉成領域型別;沒有結果的是 None(領域判證據不足)。轉不過(不該發生,
    讀取層已驗)也當沒有結果,不猜。"""
    try:
        longer = raws.get(inv.QueryOption.CHECK_LONGER_WINDOW)
        history = raws.get(inv.QueryOption.CHECK_CHANGE_HISTORY)
        daily = raws.get(inv.QueryOption.CHECK_DAILY_TREND)
        past = raws.get(inv.QueryOption.CHECK_PAST_ADJUSTMENTS)
        summary = history.get("summary") if isinstance(history, Mapping) else None
        return rules.RuleEvidence(
            longer=(rules.LongerWindow(_window(longer["1d"]), _window(longer["7d"]))
                    if longer is not None else None),
            history=(rules.ChangeHistory(
                tuple(rules.HistoryRow(row.get("action"), _moment(row.get("committed_at")))
                      for row in history["history"]),
                recent_flag=bool(history.get("truncated") is True and isinstance(summary, Mapping)
                                 and summary.get("has_recent_budget_change") is True))
                     if history is not None else None),
            daily=(rules.DailyTrend(tuple(rules.DailyRow(
                row["days_ago"], row.get("impressions"), row.get("clicks"),
                row.get("conversions"), row.get("spend"), row.get("revenue"), row["no_data"])
                for row in daily["rows"]), read_at=daily_read_at)
                   if daily is not None else None),
            past=(rules.PastAdjustments(tuple(rules.AdjustmentRow(
                row["days_ago"], row.get("budget_before"), row.get("budget_after"),
                row.get("before_conversions"), row.get("after_conversions"),
                _moment(row.get("committed_at"))) for row in past["rows"]))
                  if past is not None else None),
        )
    except (KeyError, TypeError, ValueError):
        return policy.MISSING_FOUR_QUERIES


def _round_queries(reads: TaskReads, task_id: str, seq_b: int, seq_c: int,
                   read_at_c: datetime) -> rules.RuleEvidence:
    raws = {option: _raw(reads, task_id, seq, option)
            for seq, options in ((seq_b, STEP_QUERIES[Step.B]), (seq_c, STEP_QUERIES[Step.C]))
            for option in options}
    return rule_queries(raws, read_at_c)


def decision_inputs(reads: TaskReads, task_id: str,
                    seq: int) -> tuple[tuple[Evidence, ...], rules.RuleEvidence] | None:
    """規則輪 C 蒐完的那一列(序號 seq)定案時用的輸入:C 的基本三筆加同一輪 B、C 的查詢收據,與從同一輪
    已存原始回應轉好的四查詢(展示重算根據用,跟定案那一步同一份;Phase 14 增量 2b)。不是 C 那一列、或
    找不到同一輪的 B,回 None。"""
    steps = reads.rule_steps(task_id)
    mine = next((record for at, record in steps if at == seq), None)
    if mine is None or mine.step != Step.C.value:
        return None
    seq_b = max((at for at, record in steps if record.round_id == mine.round_id
                 and record.step == Step.B.value and at < seq), default=None)
    if seq_b is None:
        return None
    last = reads.evidence_for(task_id, seq)
    evidence = (*_base(last), *_receipts(reads.evidence_for(task_id, seq_b)), *_receipts(last))
    return evidence, _round_queries(reads, task_id, seq_b, seq, mine.read_at)


def _outcome(result: Decision | RuleContinue, round_id: int, event: Event,
             reason: StrEnum | None = None, detail: str | None = None) -> RuleOutcome:
    return RuleOutcome(result, RuleEvent(round_id, event.value, detail), reason)


def _base_outcome(task: TaskRow, evidence: tuple[Evidence, ...], now: datetime,
                  round_id: int) -> RuleOutcome | None:
    """只用基本資料的判斷(A 那一步,C 也先過一次):結案就回結果,需要四查詢回 None。"""
    facts = policy.steps(evidence, now, queries=None)
    if not facts.fresh:
        return _outcome(RuleContinue(), round_id, Event.RESTART_STALE)
    if facts.needs_queries:
        return None
    decision, reason = policy.explain(task, evidence, now, candidate=None,
                                      allowed=policy.ValidatedCells.NONE)
    detail = facts.rule.reason.value if facts.rule is not None else None
    return _outcome(decision, round_id, Event.DECIDED, reason, detail)


def _decide(  # noqa: PLR0911 - 定案前每道檢查一個出口
        task: TaskRow, evidence: tuple[Evidence, ...], now: datetime, reads: TaskReads,
        state: Progress) -> RuleOutcome:
    """A/B/C 齊全:定案(見檔頭)。"""
    seq_a, _a = state.steps[Step.A]
    seq_b, _b = state.steps[Step.B]
    _seq_c, step_c = state.steps[Step.C]
    first = reads.evidence_for(task.task_id, seq_a)
    middle = reads.evidence_for(task.task_id, seq_b)
    last_base, last_receipts = _base(evidence), _receipts(evidence)
    everything = (*first, *middle, *evidence)
    if not _all_young(everything, now):
        return _outcome(RuleContinue(), state.round_id, Event.RESTART_STALE)
    if _trusted(last_base, EvidenceKind.CAMPAIGN_STATE) is None or _trusted(
            last_base, EvidenceKind.METRICS) is None:  # C 缺可信現況:不沿用 A 的舊現況
        return _outcome(NoAction(), state.round_id, Event.DECIDED,
                        policy.NoActionReason.MISSING_STATE_OR_METRICS)
    if _changed(_base(first), last_base):
        if state.changes >= MAX_CHANGE_RESTARTS:
            return _outcome(NoAction(), state.round_id, Event.CHANGED_LIMIT,
                            policy.NoActionReason.JUDGED_INSUFFICIENT)
        return _outcome(RuleContinue(), state.round_id, Event.RESTART_CHANGED)
    settled = _base_outcome(task, last_base, now, state.round_id)
    if settled is not None:
        return settled
    queries = _round_queries(reads, task.task_id, seq_b, task.seq, step_c.read_at)
    receipts = (*_receipts(middle), *last_receipts)
    facts = policy.steps((*last_base, *receipts), now, queries=queries)
    decision, reason = policy.explain(task, (*last_base, *receipts), now, candidate=None,
                                      allowed=policy.ValidatedCells.NONE, queries=queries)
    if isinstance(decision, NeedsFreshEvidence):  # 上面已驗年齡,不該發生;保守重來
        return _outcome(RuleContinue(), state.round_id, Event.RESTART_STALE)
    rule = facts.rule
    detail = None if rule is None else (
        rule.reason.value if rule.query is None else f"{rule.reason.value}:{rule.query.value}")
    return _outcome(decision, state.round_id, Event.DECIDED, reason, detail)


def decide(task: TaskRow, evidence: tuple[Evidence, ...], now: datetime,
           reads: TaskReads) -> RuleOutcome:
    """分析中那一步的規則輪決策(流程層的 RuleDecide):先讀一次這件工作的規則輪記錄,進度由純函式重算;
    各步證據與原始回應只在需要時讀。"""
    step_rows = reads.rule_steps(task.task_id)
    state = progress(step_rows, reads.rule_events(task.task_id))
    mine = next((record for seq, record in step_rows if seq == task.seq), None)
    current = state.steps.get(Step(mine.step)) if mine is not None and state.open and (
        mine.step in Step.__members__) else None
    if mine is None or current is None or current[0] != task.seq:
        # 這一列不屬於目前這一輪(升版前的兩讀、舊政策輪、AI 期證據、被新輪取代):作廢從 A 重讀
        round_id = state.round_id if state.open else max(state.round_id - 1, 0)
        return _outcome(RuleContinue(), round_id, Event.RESTART_NO_ROUND)
    step = Step(mine.step)
    if step is Step.A:
        settled = _base_outcome(task, evidence, now, state.round_id)
        return settled if settled is not None else _outcome(
            RuleContinue(), state.round_id, Event.CONTINUE)
    if step is Step.B:
        first = reads.evidence_for(task.task_id, state.steps[Step.A][0])
        if not _all_young((*first, *evidence), now):
            return _outcome(RuleContinue(), state.round_id, Event.RESTART_STALE)
        return _outcome(RuleContinue(), state.round_id, Event.CONTINUE)
    return _decide(task, evidence, now, reads, state)


def step_reads_match_budget() -> bool:
    """宣告的讀取次數(stepbudget,守衛用)等於各步實際打 DSP 的次數。"""
    return {step.value: declared_reads(step) for step in Step} == dict(RULE_STEP_READS)
