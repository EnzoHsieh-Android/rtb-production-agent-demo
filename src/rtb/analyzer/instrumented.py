"""把真的用戶端包成會記錄 tool_calls 的版本,`flow.advance()` 拿到的一律是包裝過的版本。

`flow.py` 不知道、也不需要知道 tool_calls 這件事——這支檔是唯一知道「証據來源/送出提案」
跟「TaskStore」都存在的地方,保持 dsp_client.py、inbox_client.py 兩支檔繼續不碰 TaskStore。

證據來源只有規則輪的 `rule_source`,經 `dsp_client` 的 `on_call` 鉤子逐端點記;原本整包只記一筆的
`InstrumentedEvidenceSource` 與 `dsp_evidence_source` 沒有正式入口,Phase 14 增量 3 代碼審 r2 照
「沒有入口就刪」刪除。
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from rtb.analyzer import dsp_client, rule_round
from rtb.analyzer import investigation as inv
from rtb.analyzer.flow import Accepted, DspOperation, EvidenceBatch
from rtb.analyzer.task_store import RawQuery, RuleStep, TaskRow, TaskStore, ToolEndpoint
from rtb.domain.evidence import Evidence
from rtb.domain.proposal import POLICY_VERSION, Proposal


class InstrumentedSubmit:
    """包裝一個原始的 Submit,呼叫的同時記一筆 tool_calls。

    `task_seq` 綁定在建構當下傳入的 `task`(呼叫發生時讀到的那一列快照),不是呼叫完成後
    才回頭查 `store.latest()`——`Submit` 協定的簽章只有 proposal,沒有 task,若靠事後查詢,
    並行的另一次 `advance()` 可能已經把同一個任務推進到下一列,查到的就是錯的序號(2026-09-22
    代碼審第 1 輪指出)。呼叫端要在每次呼叫前用當下讀到的 `TaskRow` 建一個新的
    `InstrumentedSubmit`,不能是長壽命、重複給不同任務列共用的單一實例。
    """

    def __init__(
        self, store: TaskStore, inner: Callable[[Proposal], Accepted], endpoint: ToolEndpoint,
        task: TaskRow,
    ):
        self._store, self._inner, self._endpoint, self._task = store, inner, endpoint, task

    def __call__(self, proposal: Proposal) -> Accepted:
        started = time.monotonic()
        try:
            result = self._inner(proposal)
        except Exception as exc:
            self._record(type(exc).__name__, started)
            raise
        self._record("ok", started)
        return result

    def _record(self, outcome: str, started: float) -> None:
        latency_ms = (time.monotonic() - started) * 1000
        self._store.record_tool_call(
            self._task.task_id, self._task.seq, self._endpoint, outcome, latency_ms,
            datetime.now(UTC))


class InstrumentedOperationLookup:
    """包裝依冪等鍵查 DSP 的操作查詢,呼叫的同時記一筆 tool_calls(Phase 5)。

    跟 `InstrumentedSubmit` 一樣綁定建構當下讀到的那一列:協定簽章只有冪等鍵、沒有任務,
    呼叫端每次推進前用當下的 `TaskRow` 建一個新的。"""

    def __init__(
        self, store: TaskStore, inner: Callable[[str], DspOperation | None], endpoint: ToolEndpoint,
        task: TaskRow,
    ):
        self._store, self._inner, self._endpoint, self._task = store, inner, endpoint, task

    def __call__(self, key: str) -> DspOperation | None:
        started = time.monotonic()
        try:
            result = self._inner(key)
        except Exception as exc:
            self._record(type(exc).__name__, started)
            raise
        self._record("ok" if result is not None else "not_found", started)
        return result

    def _record(self, outcome: str, started: float) -> None:
        latency_ms = (time.monotonic() - started) * 1000
        self._store.record_tool_call(
            self._task.task_id, self._task.seq, self._endpoint, outcome, latency_ms,
            datetime.now(UTC))


def rule_source(
    store: TaskStore, base_url: str, timeout_seconds: float,
) -> Callable[[TaskRow, datetime], EvidenceBatch]:
    """正式規則的證據來源(Phase 14 增量 2b,[S1405]):
    照已提交列算出這一輪的下一步(`rule_round.collect_plan`),
    只讀那一步的端點——A 讀現況與 1 小時指標;B 讀操作歷史與過去調整;C 讀逐日、1 天、7 天並重讀現況與
    1 小時指標(逐日與兩窗的跨窗核對在 `dsp_client.read_query_options` 同一處)。
    每次讀取各記一筆呼叫紀錄;
    追加查詢沒有結果記成收據、不讓整步失敗;原始回應與「第幾輪哪一步」跟證據同一個交易寫。5xx、連不上
    照純讀取往外丟,這一步不寫、下次重試。"""

    def _on_call(task: TaskRow, endpoint: ToolEndpoint, outcome: str, latency_ms: float) -> None:
        store.record_tool_call(task.task_id, task.seq, endpoint, outcome, latency_ms,
                               datetime.now(UTC))

    base_source = dsp_client.make_client(base_url, timeout_seconds, on_call=_on_call)
    reader = dsp_client.make_query_reader(base_url, timeout_seconds, on_call=_on_call)

    def fetch(task: TaskRow, now: datetime) -> EvidenceBatch:
        round_id, step = rule_round.collect_plan(
            store.rule_steps(task.task_id), store.rule_events(task.task_id), now)
        options = rule_round.STEP_QUERIES[step]
        receipts, raws = [], []
        reads = dsp_client.read_query_options(
            reader, task, tuple(option.value for option in options), now)
        # 現況與 1 小時指標在這一步的查詢**之後**才讀(代碼審 r1 外家 finder-2):C 的重讀要
        # 涵蓋查詢期間的變動,A/C 比對才抓得到查詢時另一方改的預算;讀取次數不變(C 仍是 5 讀)
        evidence = base_source(task, now) if rule_round.STEP_BASE[step] else ()
        for option in options:
            read = reads[option.value]
            receipt = receipt_or_invalid(task.task_id, task.seq, option, read, now)
            receipts.append(receipt)
            if read.raw is not None and receipt.payload.get("result") != "none":
                raws.append(RawQuery(option.value, inv.canonical_json(read.raw)))
        return EvidenceBatch(evidence + tuple(receipts), tuple(raws),
                             RuleStep(round_id, step.value, POLICY_VERSION, now))

    return fetch


def receipt_or_invalid(task_id: str, seq: int, option: inv.QueryOption,
                       read: dsp_client.QueryRead, now: datetime) -> Evidence:
    """把一次查詢寫成收據;建收據時仍丟 ValueError(例如某個值放不進可信證據)就改記一筆「這個查詢
    沒有結果(invalid)」,這一步照常往下走,不讓整步反覆失敗(代碼審 r1 d1、s1)。"""
    missing = None if read.reason is None else inv.NoResult(read.reason)
    try:
        return inv.receipt_evidence(task_id, seq, option, read.raw, missing, now)
    except (ValueError, ArithmeticError):  # 代碼審 r2 y1:溢位這類算術錯誤也一樣
        return inv.receipt_evidence(task_id, seq, option, None, inv.NoResult.INVALID, now)
