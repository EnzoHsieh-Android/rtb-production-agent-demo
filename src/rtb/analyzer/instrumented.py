"""把真的用戶端包成會記錄 tool_calls 的版本,`flow.advance()` 拿到的一律是包裝過的版本。

`flow.py` 不知道、也不需要知道 tool_calls 這件事——這支檔是唯一知道「証據來源/送出提案」
跟「TaskStore」都存在的地方,保持 dsp_client.py、inbox_client.py 兩支檔繼續不碰 TaskStore。
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from rtb.analyzer import dsp_client
from rtb.analyzer.flow import Accepted, DspOperation
from rtb.analyzer.task_store import TaskRow, TaskStore
from rtb.domain.evidence import Evidence
from rtb.domain.proposal import Proposal


class InstrumentedEvidenceSource:
    """包裝一個原始的 EvidenceSource,呼叫的同時記一筆 tool_calls。

    給「內部只有一次 HTTP 呼叫」的 EvidenceSource 用;`dsp_client` 內部有兩個端點,各自成功
    /失敗要分開記錄,不能只包整個 `__call__` 記一筆(否則「現況成功、指標失敗」會遺失第一筆
    成功紀錄,見下面 `dsp_evidence_source` 的做法)。
    """

    def __init__(
        self,
        store: TaskStore,
        inner: Callable[[TaskRow, datetime], tuple[Evidence, ...]],
        endpoint: str,
    ):
        self._store, self._inner, self._endpoint = store, inner, endpoint

    def __call__(self, task: TaskRow, now: datetime) -> tuple[Evidence, ...]:
        started = time.monotonic()
        try:
            result = self._inner(task, now)
        except Exception as exc:
            self._record(task, type(exc).__name__, started)
            raise
        self._record(task, "ok", started)
        return result

    def _record(self, task: TaskRow, outcome: str, started: float) -> None:
        latency_ms = (time.monotonic() - started) * 1000
        self._store.record_tool_call(
            task.task_id, task.seq, self._endpoint, outcome, latency_ms, datetime.now(UTC))


def dsp_evidence_source(
    store: TaskStore, base_url: str, timeout_seconds: float,
) -> Callable[[TaskRow, datetime], tuple[Evidence, ...]]:
    """建真的 DSP 用戶端,兩個內部端點(現況、指標)各自成功/失敗都各記一筆 tool_calls——
    不是像 `InstrumentedEvidenceSource` 那樣整個呼叫包一層才記一筆。呼叫端(`flow.advance()`
    要用的 EvidenceSource)拿到的就是這個函式本身,不用再另外包一層。
    """

    def _on_call(task: TaskRow, endpoint: str, outcome: str, latency_ms: float) -> None:
        store.record_tool_call(task.task_id, task.seq, endpoint, outcome, latency_ms,
                               datetime.now(UTC))

    return dsp_client.make_client(base_url, timeout_seconds, on_call=_on_call)


class InstrumentedSubmit:
    """包裝一個原始的 Submit,呼叫的同時記一筆 tool_calls。

    `task_seq` 綁定在建構當下傳入的 `task`(呼叫發生時讀到的那一列快照),不是呼叫完成後
    才回頭查 `store.latest()`——`Submit` 協定的簽章只有 proposal,沒有 task,若靠事後查詢,
    並行的另一次 `advance()` 可能已經把同一個任務推進到下一列,查到的就是錯的序號(2026-09-22
    代碼審第 1 輪指出)。呼叫端要在每次呼叫前用當下讀到的 `TaskRow` 建一個新的
    `InstrumentedSubmit`,不能是長壽命、重複給不同任務列共用的單一實例。
    """

    def __init__(
        self, store: TaskStore, inner: Callable[[Proposal], Accepted], endpoint: str,
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
        self, store: TaskStore, inner: Callable[[str], DspOperation | None], endpoint: str,
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
