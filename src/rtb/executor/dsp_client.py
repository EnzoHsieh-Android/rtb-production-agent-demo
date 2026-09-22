"""執行行程的 DSP 讀寫用戶端:比照分析行程的 DSP 用戶端,只做「打 HTTP、轉成結果」,不碰儲存。

經共用 HTTP 用戶端送出(只送得出封閉列舉裡的標頭:冪等鍵與能力憑證)。
- 讀取:廣告不存在(404 campaign_not_found)回 None,讓執行前檢查擋下;其他失敗一律 DspUnavailable。
- 寫入:不丟例外;逾時、斷線、回應讀不懂一律回「沒拿到回應」,交給回應對照表判成結果不明。
- 查操作紀錄:查不到(404 operation_not_found)回 None;其他失敗一律 DspUnavailable。
  查寫入後版本(增量 3 的介面)原樣保留;對帳用的「查完整操作紀錄」另開一支。
- 作廢:不丟例外;回應原樣交給作廢回應對照表判。
"""

from typing import Any

from rtb.domain._checks import is_plain_int
from rtb.domain.proposal import ActionType, Proposal
from rtb.executor.execution import (
    CampaignView,
    DspUnavailable,
    OperationRecord,
    VoidAnswer,
    WriteAnswer,
)
from rtb.httpclient import ClientHeader, request_json

_WRITE_PATHS = {ActionType.UPDATE_BUDGET: "budget", ActionType.PAUSE_CAMPAIGN: "pause"}


_SQLITE_INTEGER_MAX = 2**63 - 1  # 版本會寫進資料庫的整數欄位;超過就當成讀不懂,不讓寫入時崩潰


def _positive_int(value: object) -> int | None:
    return value if is_plain_int(value) and 0 < value <= _SQLITE_INTEGER_MAX else None


class DspClient:
    def __init__(self, base_url: str, timeout_seconds: float):
        self._base, self._timeout = base_url.rstrip("/"), timeout_seconds

    def _get(self, path: str) -> tuple[int, dict[str, Any]]:
        try:
            status, body = request_json(self._base + path, "GET", None, self._timeout)
        except (OSError, ValueError) as exc:  # 逾時、斷線、回應不是 JSON 或太大
            raise DspUnavailable(type(exc).__name__) from exc
        if not isinstance(body, dict):
            raise DspUnavailable("回應不是 JSON 物件")
        return status, body

    def read_campaign(self, campaign_id: str) -> CampaignView | None:
        status, body = self._get(f"/campaigns/{campaign_id}")
        if status == 404 and body.get("error") == "campaign_not_found":
            return None
        budget, version = _positive_int(body.get("budget")), _positive_int(body.get("version"))
        state = body.get("status")
        if status != 200 or budget is None or version is None or not isinstance(state, str):
            raise DspUnavailable(f"讀取回 {status}")
        return CampaignView(budget=budget, status=state, version=version)

    def write(self, proposal: Proposal, key: str, token: str) -> WriteAnswer:
        body: dict[str, Any] = {"expected_version": proposal.campaign_version_observed}
        if proposal.action_type is ActionType.UPDATE_BUDGET:
            body["new_budget"] = proposal.requested_change["new_budget"]
        path = f"/campaigns/{proposal.campaign_id}/{_WRITE_PATHS[proposal.action_type]}"
        headers = {ClientHeader.IDEMPOTENCY_KEY: key, ClientHeader.CAPABILITY: token}
        try:
            status, reply = request_json(self._base + path, "POST", body, self._timeout, headers)
        except (OSError, ValueError):
            return WriteAnswer(None)
        if not isinstance(reply, dict):  # 回應讀不懂:當成沒拿到回應
            return WriteAnswer(None)
        error = reply.get("error")
        return WriteAnswer(status, error if isinstance(error, str) else None,
                           _positive_int(reply.get("version_after")))

    def operation_version(self, key: str) -> int | None:
        """增量 3 的介面,行為照舊:只要寫入後版本讀得懂就回它。

        刻意不轉呼叫 operation_record:那支要求廣告、動作、參數都在,舊版 DSP 的成功回應沒有參數,
        會被誤判成讀不懂。兩支共用的是查詢與「查不到」的判斷(_lookup),不是欄位解析。
        """
        body = self._lookup(key)
        if body is None:
            return None
        version = _positive_int(body.get("version_after"))
        if version is None:
            raise DspUnavailable("查詢回的寫入後版本讀不懂")
        return version

    def _lookup(self, key: str) -> dict[str, Any] | None:
        """查一把鍵的操作紀錄:查不到回 None,非 200 一律 DspUnavailable;欄位解析由呼叫端做。"""
        status, body = self._get(f"/operations/{key}")
        if status == 404 and body.get("error") == "operation_not_found":
            return None
        if status != 200:
            raise DspUnavailable(f"查詢回 {status}")
        return body

    def operation_record(self, key: str) -> OperationRecord | None:
        body = self._lookup(key)
        if body is None:
            return None
        record = _record(body)
        if record is None:  # 對帳要核對完整內容:少一個欄位就不能拿來下判斷
            raise DspUnavailable("查詢回的操作紀錄讀不懂")
        return record

    def void(self, proposal: Proposal, key: str, token: str) -> VoidAnswer:
        body = {"expected_version": proposal.campaign_version_observed}
        headers = {ClientHeader.IDEMPOTENCY_KEY: key, ClientHeader.CAPABILITY: token}
        path = f"/campaigns/{proposal.campaign_id}/void"
        try:
            status, reply = request_json(self._base + path, "POST", body, self._timeout, headers)
        except (OSError, ValueError):
            return VoidAnswer(None)
        if not isinstance(reply, dict):
            return VoidAnswer(None)
        error, state = reply.get("error"), reply.get("state")
        operation = reply.get("operation")
        return VoidAnswer(status, error if isinstance(error, str) else None,
                          state if state in ("voided", "committed") else None,
                          _record(operation) if isinstance(operation, dict) else None)


def _record(body: dict[str, Any]) -> OperationRecord | None:
    """讀不懂就回 None;預期版本缺(DSP 補欄位之前的舊列)照實留空值,由核對當成對不上。"""
    campaign, action, params = body.get("campaign_id"), body.get("action"), body.get("params")
    version = _positive_int(body.get("version_after"))
    if (not isinstance(campaign, str) or not isinstance(action, str)
            or not isinstance(params, dict) or version is None):
        return None
    budget = params.get("new_budget")
    return OperationRecord(
        campaign_id=campaign, action=action,
        new_budget=budget if _positive_int(budget) is not None else None,
        expected_version=_positive_int(body.get("expected_version")), version_after=version)
