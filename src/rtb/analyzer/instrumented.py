"""把真的用戶端包成會記錄 tool_calls 的版本,`flow.advance()` 拿到的一律是包裝過的版本。

`flow.py` 不知道、也不需要知道 tool_calls 這件事——這支檔是唯一知道「証據來源/送出提案」
跟「TaskStore」都存在的地方,保持 dsp_client.py、inbox_client.py 兩支檔繼續不碰 TaskStore。
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from rtb.analyzer.flow import Accepted
from rtb.analyzer.task_store import TaskRow, TaskStore
from rtb.domain.evidence import Evidence
from rtb.domain.proposal import Proposal


class InstrumentedEvidenceSource:
    """包裝一個原始的 EvidenceSource,呼叫的同時記一筆 tool_calls。"""

    def __init__(
        self,
        store: TaskStore,
        inner: Callable[[TaskRow], tuple[Evidence, ...]],
        endpoint: str,
    ):
        self._store, self._inner, self._endpoint = store, inner, endpoint

    def __call__(self, task: TaskRow) -> tuple[Evidence, ...]:
        started = time.monotonic()
        try:
            result = self._inner(task)
        except Exception as exc:
            self._record(task, type(exc).__name__, started)
            raise
        self._record(task, "ok", started)
        return result

    def _record(self, task: TaskRow, outcome: str, started: float) -> None:
        latency_ms = (time.monotonic() - started) * 1000
        self._store.record_tool_call(
            task.task_id, task.seq, self._endpoint, outcome, latency_ms, datetime.now(UTC))


class InstrumentedSubmit:
    """包裝一個原始的 Submit,呼叫的同時記一筆 tool_calls。

    `Submit` 協定的簽章只有 proposal,沒有 task,所以用 `proposal.task_id` 向 `TaskStore`
    查目前這一列的序號——查不到就代表任務不存在,是呼叫端的錯誤,直接讓例外往外傳,不吞。
    """

    def __init__(self, store: TaskStore, inner: Callable[[Proposal], Accepted], endpoint: str):
        self._store, self._inner, self._endpoint = store, inner, endpoint

    def __call__(self, proposal: Proposal) -> Accepted:
        started = time.monotonic()
        try:
            result = self._inner(proposal)
        except Exception as exc:
            self._record(proposal.task_id, type(exc).__name__, started)
            raise
        self._record(proposal.task_id, "ok", started)
        return result

    def _record(self, task_id: str, outcome: str, started: float) -> None:
        row = self._store.latest(task_id)
        if row is None:
            return  # 任務不存在的情況已經在呼叫本身炸過了,這裡不重複報
        latency_ms = (time.monotonic() - started) * 1000
        self._store.record_tool_call(
            task_id, row.seq, self._endpoint, outcome, latency_ms, datetime.now(UTC))
