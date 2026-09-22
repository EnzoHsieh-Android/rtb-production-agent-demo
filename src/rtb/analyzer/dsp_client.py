"""真的 EvidenceSource:呼叫 Mock DSP 讀廣告現況與指標。

兩個端點都成功才回傳兩筆證據;任一個失敗(逾時、連線失敗、4xx/5xx)整個函式往外丟例外,
不吞、不回傳半套(增量 3 的 S26 已經保證:EvidenceSource 丟例外時,advance() 不寫入任何
東西,狀態留在原地等下次重試——這支檔只需要老實丟例外,不用自己做任何重試邏輯)。

只做「打 HTTP、轉成 Evidence」,不碰 TaskStore、不做任何持久化;`on_call` 是唯一的例外
(選填的鉤子,呼叫端要不要拿它去記 tool_calls 是呼叫端的事,這支檔不知道 TaskStore 存在)。
"""

import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime
from types import MappingProxyType
from typing import Any

from rtb.analyzer.task_store import TaskRow
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.httpclient import request_json

OnDspCall = Callable[[TaskRow, str, str, float], None]
"""(task, endpoint, outcome, latency_ms) -> None;兩個端點各自成功/失敗都各呼叫一次,不是整個
fetch() 完成才呼叫一次——代碼審第 1 輪指出,包一整個 fetch() 只記一筆會讓「現況成功、指標
失敗」這種情況遺失第一個成功呼叫的紀錄。"""


class DspRequestFailed(Exception):
    """DSP 的其中一個端點沒有成功回應(非 2xx、逾時、連線失敗)。"""


def _content_hash(payload: dict[str, Any]) -> str:
    """跟增量 2 收件口的提案內容雜湊同一套規則:鍵排序、無多餘空白、不允許 NaN 的 JSON。"""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _get(
    base_url: str, path: str, timeout_seconds: float,
    task: TaskRow, on_call: OnDspCall | None, endpoint_name: str,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        status, body = request_json(f"{base_url}{path}", "GET", None, timeout_seconds)
        if status != 200:
            raise DspRequestFailed(f"{path} 回 {status}:{body.get('error', '未知錯誤')}")
    except Exception as exc:
        if on_call is not None:
            on_call(task, endpoint_name, type(exc).__name__, (time.monotonic() - started) * 1000)
        raise
    if on_call is not None:
        on_call(task, endpoint_name, "ok", (time.monotonic() - started) * 1000)
    return body


def make_client(
    base_url: str, timeout_seconds: float, on_call: OnDspCall | None = None
) -> Callable[[TaskRow, datetime], tuple[Evidence, ...]]:
    """回傳一個符合 EvidenceSource 協定的函式,綁定 DSP 的位址與逾時。

    `on_call` 不填就是原本的行為(不記錄任何東西);要記 tool_calls 的呼叫端(見
    `instrumented.dsp_evidence_source`)傳一個綁定 TaskStore 的鉤子進來。
    """

    def fetch(task: TaskRow, now: datetime) -> tuple[Evidence, ...]:
        # 證據的讀取時間用呼叫端(流程層)傳來的時間,不自己讀系統時鐘,見 flow.EvidenceSource
        state = _get(base_url, f"/campaigns/{task.campaign_id}", timeout_seconds,
                    task, on_call, "dsp:campaign")
        metrics = _get(base_url, f"/campaigns/{task.campaign_id}/metrics?window=1h",
                       timeout_seconds, task, on_call, "dsp:metrics")
        return (
            Evidence(
                evidence_id=f"{task.task_id}-{task.seq}-state", task_id=task.task_id,
                kind=EvidenceKind.CAMPAIGN_STATE, source="dsp", observed_at=now,
                campaign_version_observed=state["version"],
                content_hash=_content_hash(state), trust_class=TrustClass.TRUSTED,
                payload=MappingProxyType(state),
            ),
            Evidence(
                evidence_id=f"{task.task_id}-{task.seq}-metrics", task_id=task.task_id,
                kind=EvidenceKind.METRICS, source="dsp", observed_at=now,
                campaign_version_observed=None,
                content_hash=_content_hash(metrics), trust_class=TrustClass.TRUSTED,
                payload=MappingProxyType(metrics),
            ),
        )

    return fetch
