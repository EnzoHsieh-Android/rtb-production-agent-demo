"""真的 EvidenceSource:呼叫 Mock DSP 讀廣告現況與指標。

兩個端點都成功才回傳三筆證據(廣告現況、指標、廣告文字);任一個失敗(逾時、連線失敗、
4xx/5xx、可信欄位壞掉)整個函式往外丟例外,不吞、不回傳半套(增量 3 的 S26 已經保證:
EvidenceSource 丟例外時,advance() 不寫入任何東西,狀態留在原地等下次重試——這支檔只需要
老實丟例外,不用自己做任何重試邏輯)。

逐欄白名單(Phase 7 增量 1):只有名單上的欄位進證據,名單外的連名稱都不記(欄位名稱本身也是
不可信輸入);可信欄位缺漏、型別不對、超出範圍、廣告編號不是這個任務的,整個讀取失敗——那是
「DSP 壞了」的訊號,不是攻擊者能從廣告文字觸發的。廣告名稱是不可信文字:原樣保存、超過上限就截斷
並標記,不過濾、不拒收(塞字不能讓分析失敗)。

只做「打 HTTP、轉成 Evidence」,不碰 TaskStore、不做任何持久化;`on_call` 是唯一的例外
(選填的鉤子,呼叫端要不要拿它去記 tool_calls 是呼叫端的事,這支檔不知道 TaskStore 存在)。
"""

import hashlib
import json
import math
import time
from collections.abc import Callable
from datetime import datetime
from types import MappingProxyType
from typing import Any

from rtb.analyzer.flow import DspOperation
from rtb.analyzer.task_store import TaskRow, ToolEndpoint
from rtb.domain._checks import is_id, is_plain_int, is_plain_number
from rtb.domain.evidence import MAX_UNTRUSTED_TEXT_LENGTH, Evidence, EvidenceKind, TrustClass
from rtb.domain.proposal import MAX_INT, ActionType
from rtb.httpclient import request_json

OnDspCall = Callable[[TaskRow, ToolEndpoint, str, float], None]
"""(task, endpoint, outcome, latency_ms) -> None;兩個端點各自成功/失敗都各呼叫一次,不是整個
fetch() 完成才呼叫一次——代碼審第 1 輪指出,包一整個 fetch() 只記一筆會讓「現況成功、指標
失敗」這種情況遺失第一個成功呼叫的紀錄。"""


class DspRequestFailed(Exception):
    """DSP 的其中一個端點沒有成功回應(非 2xx、逾時、連線失敗),或回應裡的可信欄位壞掉。"""


CAMPAIGN_STATUSES = frozenset({"active", "paused"})  # DSP 實際回傳的狀態字串
# 用戶端只要 1 小時的指標(示範規則也只按 1 小時換算);回應的時間窗跟請求的不同就當可信欄位不合格
# (2026-09-23 使用者裁定,原本收 1h/1d/7d 任一個,DSP 對 1h 的請求回 7d 也會被當成可信收下)
REQUESTED_WINDOW = "1h"
Check = Callable[[Any], bool]  # 比照提案白名單 CHECKS:每個欄位一支只看值的檢查


def _is_int_between(low: int, value: Any) -> bool:
    return is_plain_int(value) and low <= value <= MAX_INT  # 跟提案、模擬 DSP 同一個資料庫整數上限


def _is_count_or_none(value: Any) -> bool:
    return value is None or (is_plain_int(value) and abs(value) <= MAX_INT)


def _is_finite_or_none(value: Any) -> bool:
    if value is None:
        return True
    try:
        return is_plain_number(value) and math.isfinite(value)
    except OverflowError:  # 大到超出浮點範圍的整數
        return False


STATE_FIELDS: dict[str, Check] = {
    "id": is_id,
    "budget": lambda value: _is_int_between(0, value),
    "status": lambda value: isinstance(value, str) and value in CAMPAIGN_STATUSES,
    "version": lambda value: _is_int_between(1, value),
}
METRICS_FIELDS: dict[str, Check] = {
    "campaign_id": is_id,
    "window": lambda value: value == REQUESTED_WINDOW,
    "impressions": _is_count_or_none,
    "clicks": _is_count_or_none,
    "conversions": _is_count_or_none,
    "spend": _is_finite_or_none,
    "revenue": _is_finite_or_none,
}


def _trusted(body: dict[str, Any], fields: dict[str, Check], campaign_field: str,
             task: TaskRow, endpoint: str) -> dict[str, Any]:
    """只取白名單上的可信欄位;任一欄缺漏或不合格、或廣告編號不是這個任務的,就整個讀取失敗
    (不記欄位值,它是外來輸入)。"""
    bad = [name for name, check in fields.items() if name not in body or not check(body[name])]
    if not bad and body[campaign_field] != task.campaign_id:
        bad = [campaign_field]
    if bad:
        raise DspRequestFailed(f"{endpoint} 的可信欄位不合格:{', '.join(bad)}")
    return {name: body[name] for name in fields}


def _campaign_text(body: dict[str, Any]) -> dict[str, Any]:
    """名稱是不可信文字:是字串就原樣保存,超過上限截斷並標記;缺少或不是字串記空值,不丟例外。"""
    name = body.get("name")
    if not isinstance(name, str):
        return {"name": None, "truncated": False}
    return {"name": name[:MAX_UNTRUSTED_TEXT_LENGTH],
            "truncated": len(name) > MAX_UNTRUSTED_TEXT_LENGTH}


def _content_hash(payload: dict[str, Any]) -> str:
    """跟增量 2 收件口的提案內容雜湊同一套規則:鍵排序、無多餘空白、不允許 NaN 的 JSON。"""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _get(
    base_url: str, path: str, timeout_seconds: float,
    task: TaskRow, on_call: OnDspCall | None, endpoint_name: ToolEndpoint,
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
        state_body = _get(base_url, f"/campaigns/{task.campaign_id}", timeout_seconds,
                          task, on_call, ToolEndpoint.DSP_CAMPAIGN)
        metrics_path = f"/campaigns/{task.campaign_id}/metrics?window={REQUESTED_WINDOW}"
        metrics_body = _get(base_url, metrics_path, timeout_seconds, task, on_call,
                            ToolEndpoint.DSP_METRICS)
        state = _trusted(state_body, STATE_FIELDS, "id", task, "dsp:campaign")
        metrics = _trusted(metrics_body, METRICS_FIELDS, "campaign_id", task, "dsp:metrics")
        text = _campaign_text(state_body)
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
            Evidence(
                evidence_id=f"{task.task_id}-{task.seq}-text", task_id=task.task_id,
                kind=EvidenceKind.CAMPAIGN_TEXT, source="dsp", observed_at=now,
                campaign_version_observed=state["version"],  # 跟現況同一次回應讀到的
                content_hash=_content_hash(text), trust_class=TrustClass.UNTRUSTED_TEXT,
                payload=MappingProxyType(text),
            ),
        )

    return fetch


def _positive_or_none(value: Any) -> bool:
    """預算與版本都從 1 起算(同提案與執行端的核對);0 讀不懂,不拿來判內容不符。"""
    return value is None or _is_int_between(1, value)


def make_operation_lookup(
    base_url: str, timeout_seconds: float,
) -> Callable[[str], DspOperation | None]:
    """依冪等鍵查 DSP 的操作紀錄(Phase 5:收件表已清掉時,分析端查「有沒有寫進去」)。

    查不到(404 operation_not_found)回 None;其他狀態碼、逾時、斷線、欄位讀不懂一律丟
    DspRequestFailed,由流程判成這一輪沒有進展。只取核對內容要用的四個欄位,其餘不讀;
    回應大小上限由共用 HTTP 用戶端守。要記 tool_calls 的呼叫端自己包一層(instrumented.py)。
    """

    def lookup(key: str) -> DspOperation | None:
        if not is_id(key):
            raise DspRequestFailed("冪等鍵格式不合法")
        status, body = request_json(f"{base_url}/operations/{key}", "GET", None, timeout_seconds)
        if status == 404 and body.get("error") == "operation_not_found":
            return None
        if status != 200:
            raise DspRequestFailed(f"/operations 回 {status}:{body.get('error', '未知錯誤')}")
        campaign, action, params = body.get("campaign_id"), body.get("action"), body.get("params")
        if (not is_id(campaign) or action not in {item.value for item in ActionType}
                or not isinstance(params, dict)):
            raise DspRequestFailed("操作紀錄的欄位讀不懂")
        budget, expected = params.get("new_budget"), body.get("expected_version")
        if not _positive_or_none(budget) or not _positive_or_none(expected):
            raise DspRequestFailed("操作紀錄的數字欄位讀不懂")
        return DspOperation(campaign_id=campaign, action=action, new_budget=budget,
                            expected_version=expected)

    return lookup
