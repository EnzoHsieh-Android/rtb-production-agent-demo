"""執行行程的 DSP 讀寫用戶端:比照分析行程的 DSP 用戶端,只做「打 HTTP、轉成結果」,不碰儲存。

經共用 HTTP 用戶端送出(只送得出封閉列舉裡的標頭:冪等鍵與能力憑證)。
- 讀取:廣告不存在(404 campaign_not_found)回 None,讓執行前檢查擋下;其他失敗一律 DspUnavailable。
- 寫入:不丟例外;逾時、斷線、回應讀不懂一律回「沒拿到回應」,交給回應對照表判成結果不明。
- 查操作紀錄:查不到(404 operation_not_found)回 None;其他失敗一律 DspUnavailable。
  查寫入後版本(增量 3 的介面)原樣保留;對帳用的「查完整操作紀錄」另開一支。
- 作廢:不丟例外;回應原樣交給作廢回應對照表判。

呼叫回呼(Phase 9 增量 1,比照分析端 DSP 用戶端的 on_call):每一支公開方法都經同一個底層送出點
`_send`,每一次 HTTP 呼叫在那裡觸發剛好一次回呼(呼叫類別、結果類別、狀態碼、耗時、DSP 回的錯誤
代碼)。結果類別:傳輸層的例外依型別分逾時、連線失敗、回應讀不懂;有回應時依狀態碼分 4xx、5xx;
狀態 2xx 但欄位讀不懂算回應讀不懂。沒拿到回應時,寫入與作廢的回應、讀取丟的例外也帶同一個分類。
這支檔仍不碰儲存:寫呼叫紀錄的是掛上回呼的執行迴圈。
"""

import time
from collections.abc import Callable
from typing import Any

from rtb.domain._checks import is_plain_int
from rtb.domain.proposal import ActionType, Proposal
from rtb.executor.attempt_store import DspCall, DspCallKind, DspCallResult, dsp_error_code
from rtb.executor.execution import (
    CampaignView,
    DspUnavailable,
    OnDspCall,
    OperationRecord,
    VoidAnswer,
    WriteAnswer,
)
from rtb.httpclient import ClientHeader, request_json

_WRITE_PATHS = {ActionType.UPDATE_BUDGET: "budget", ActionType.PAUSE_CAMPAIGN: "pause"}


_SQLITE_INTEGER_MAX = 2**63 - 1  # 版本會寫進資料庫的整數欄位;超過就當成讀不懂,不讓寫入時崩潰
R = DspCallResult


def _positive_int(value: object) -> int | None:
    return value if is_plain_int(value) and 0 < value <= _SQLITE_INTEGER_MAX else None


def _transport_failure(exc: BaseException) -> DspCallResult:
    """沒拿到回應的是哪一種:逾時(讀取逾時直接丟;連線逾時被 urllib 包在 reason 裡)、回應讀不懂
    (不是 JSON、太大)、其他 OSError 算連線失敗。只看型別與屬性,不匯入網路模組。"""
    if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
        return R.TIMEOUT
    if isinstance(exc, ValueError):
        return R.UNREADABLE
    return R.CONNECTION_FAILED


def _by_status(status: int, readable: bool) -> DspCallResult:
    """有回應時:2xx 看讀不讀得懂,4xx、5xx 照狀態碼;其他(沒跟的轉址等)也算讀不懂。"""
    if 200 <= status < 300:
        return R.RESPONDED if readable else R.UNREADABLE
    if 400 <= status < 500:
        return R.CLIENT_ERROR
    if 500 <= status < 600:
        return R.SERVER_ERROR
    return R.UNREADABLE


class DspClient:
    def __init__(self, base_url: str, timeout_seconds: float, on_call: OnDspCall | None = None):
        self._base, self._timeout = base_url.rstrip("/"), timeout_seconds
        self._on_call = on_call

    def listen(self, on_call: OnDspCall) -> None:
        self._on_call = on_call

    def _send[T](  # noqa: PLR0913 - 一次呼叫要的全部:類別、方法、路徑、本文、標頭、兩個解讀
        self, kind: DspCallKind, method: str, path: str, body: dict[str, Any] | None,
        headers: dict[ClientHeader, str] | None,
        read: Callable[[int, dict[str, Any] | None], tuple[T, bool]],
        no_reply: Callable[[DspCallResult], T],
    ) -> T:
        """唯一的送出點:送一次、量耗時(單調時鐘)、分類,觸發一次回呼,再把結果交回。

        read 解讀有回應的情況,回 (結果, 讀不讀得懂);丟 DspUnavailable 時照樣先觸發回呼。
        no_reply 處理沒拿到回應(回一個「沒拿到回應」的結果,或丟 DspUnavailable)。"""
        started = time.monotonic()
        try:
            status, reply = request_json(self._base + path, method, body, self._timeout, headers)
        except (OSError, ValueError) as exc:  # 逾時、斷線、回應不是 JSON 或太大
            failure = _transport_failure(exc)
            self._report(kind, failure, None, started, None)
            return no_reply(failure)
        parsed = reply if isinstance(reply, dict) else None
        try:
            result, readable = read(status, parsed)
        except DspUnavailable:
            self._report(kind, _by_status(status, False), status, started, parsed)
            raise
        self._report(kind, _by_status(status, readable), status, started, parsed)
        return result

    def _report(
        self, kind: DspCallKind, result: DspCallResult, status: int | None, started: float,
        reply: dict[str, Any] | None,
    ) -> None:
        if self._on_call is not None:
            self._on_call(DspCall(kind, result, status, (time.monotonic() - started) * 1000,
                                  dsp_error_code(reply)))

    def _get[T](
        self, kind: DspCallKind, path: str,
        read: Callable[[int, dict[str, Any]], T],
    ) -> T:
        """讀取類(讀廣告、查操作紀錄):沒拿到回應、回應不是 JSON 物件一律丟 DspUnavailable。"""
        def interpret(status: int, reply: dict[str, Any] | None) -> tuple[T, bool]:
            if reply is None:
                raise DspUnavailable("回應不是 JSON 物件", _by_status(status, False))
            return read(status, reply), True

        def unavailable(failure: DspCallResult) -> T:
            raise DspUnavailable(failure.value, failure)

        return self._send(kind, "GET", path, None, None, interpret, unavailable)

    def read_campaign(self, campaign_id: str) -> CampaignView | None:
        def read(status: int, body: dict[str, Any]) -> CampaignView | None:
            if status == 404 and body.get("error") == "campaign_not_found":
                return None
            budget, version = _positive_int(body.get("budget")), _positive_int(body.get("version"))
            state = body.get("status")
            if status != 200 or budget is None or version is None or not isinstance(state, str):
                raise DspUnavailable(f"讀取回 {status}", _by_status(status, False))
            return CampaignView(budget=budget, status=state, version=version)

        return self._get(DspCallKind.READ_CAMPAIGN, f"/campaigns/{campaign_id}", read)

    def write(self, proposal: Proposal, key: str, token: str) -> WriteAnswer:
        body: dict[str, Any] = {"expected_version": proposal.campaign_version_observed}
        if proposal.action_type is ActionType.UPDATE_BUDGET:
            body["new_budget"] = proposal.requested_change["new_budget"]
        path = f"/campaigns/{proposal.campaign_id}/{_WRITE_PATHS[proposal.action_type]}"
        headers = {ClientHeader.IDEMPOTENCY_KEY: key, ClientHeader.CAPABILITY: token}

        def read(status: int, reply: dict[str, Any] | None) -> tuple[WriteAnswer, bool]:
            if reply is None:  # 回應讀不懂:當成沒拿到回應
                return WriteAnswer(None, failure=R.UNREADABLE), False
            error = reply.get("error")
            version = _positive_int(reply.get("version_after"))
            return (WriteAnswer(status, error if isinstance(error, str) else None, version),
                    version is not None)

        return self._send(DspCallKind.WRITE, "POST", path, body, headers, read,
                          lambda failure: WriteAnswer(None, failure=failure))

    def operation_version(self, key: str) -> int | None:
        """增量 3 的介面,行為照舊:只要寫入後版本讀得懂就回它。

        刻意不轉呼叫 operation_record:那支要求廣告、動作、參數都在,舊版 DSP 的成功回應沒有參數,
        會被誤判成讀不懂。兩支共用的是查詢與「查不到」的判斷(_lookup),不是欄位解析。
        """
        def parse(body: dict[str, Any]) -> int:
            version = _positive_int(body.get("version_after"))
            if version is None:
                raise DspUnavailable("查詢回的寫入後版本讀不懂", R.UNREADABLE)
            return version

        return self._lookup(key, parse)

    def _lookup[T](self, key: str, parse: Callable[[dict[str, Any]], T]) -> T | None:
        """查一把鍵的操作紀錄:查不到回 None,非 200 一律 DspUnavailable;欄位解析由呼叫端給。"""
        def read(status: int, body: dict[str, Any]) -> T | None:
            if status == 404 and body.get("error") == "operation_not_found":
                return None
            if status != 200:
                raise DspUnavailable(f"查詢回 {status}", _by_status(status, False))
            return parse(body)

        return self._get(DspCallKind.LOOKUP_OPERATION, f"/operations/{key}", read)

    def operation_record(self, key: str) -> OperationRecord | None:
        def parse(body: dict[str, Any]) -> OperationRecord:
            record = _record(body)
            if record is None:  # 對帳要核對完整內容:少一個欄位就不能拿來下判斷
                raise DspUnavailable("查詢回的操作紀錄讀不懂", R.UNREADABLE)
            return record

        return self._lookup(key, parse)

    def void(self, proposal: Proposal, key: str, token: str) -> VoidAnswer:
        body = {"expected_version": proposal.campaign_version_observed}
        headers = {ClientHeader.IDEMPOTENCY_KEY: key, ClientHeader.CAPABILITY: token}
        path = f"/campaigns/{proposal.campaign_id}/void"

        def read(status: int, reply: dict[str, Any] | None) -> tuple[VoidAnswer, bool]:
            if reply is None:
                return VoidAnswer(None, failure=R.UNREADABLE), False
            error, state = reply.get("error"), reply.get("state")
            operation = reply.get("operation")
            record = _record(operation) if isinstance(operation, dict) else None
            answer = VoidAnswer(status, error if isinstance(error, str) else None,
                                state if state in ("voided", "committed") else None, record)
            return answer, answer.state == "voided" or (
                answer.state == "committed" and record is not None)

        return self._send(DspCallKind.VOID, "POST", path, body, headers, read,
                          lambda failure: VoidAnswer(None, failure=failure))


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
