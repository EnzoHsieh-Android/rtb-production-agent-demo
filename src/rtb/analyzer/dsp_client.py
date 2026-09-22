"""真的 EvidenceSource:呼叫 Mock DSP 讀廣告現況與指標。

兩個端點都成功才回傳兩筆證據;任一個失敗(逾時、連線失敗、4xx/5xx)整個函式往外丟例外,
不吞、不回傳半套(增量 3 的 S26 已經保證:EvidenceSource 丟例外時,advance() 不寫入任何
東西,狀態留在原地等下次重試——這支檔只需要老實丟例外,不用自己做任何重試邏輯)。

只做「打 HTTP、轉成 Evidence」,不碰 TaskStore、不做任何持久化。
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from rtb.analyzer.task_store import TaskRow
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.httpclient import request_json


class DspRequestFailed(Exception):
    """DSP 的其中一個端點沒有成功回應(非 2xx、逾時、連線失敗)。"""


def _content_hash(payload: dict[str, Any]) -> str:
    """跟增量 2 收件口的提案內容雜湊同一套規則:鍵排序、無多餘空白、不允許 NaN 的 JSON。"""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _get(base_url: str, path: str, timeout_seconds: float) -> dict[str, Any]:
    status, body = request_json(f"{base_url}{path}", "GET", None, timeout_seconds)
    if status != 200:
        raise DspRequestFailed(f"{path} 回 {status}:{body.get('error', '未知錯誤')}")
    return body


def make_client(
    base_url: str, timeout_seconds: float
) -> Callable[[TaskRow], tuple[Evidence, ...]]:
    """回傳一個符合 EvidenceSource 協定的函式,綁定 DSP 的位址與逾時。"""

    def fetch(task: TaskRow) -> tuple[Evidence, ...]:
        now = datetime.now(UTC)
        state = _get(base_url, f"/campaigns/{task.campaign_id}", timeout_seconds)
        metrics = _get(base_url, f"/campaigns/{task.campaign_id}/metrics?window=1h",
                       timeout_seconds)
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
