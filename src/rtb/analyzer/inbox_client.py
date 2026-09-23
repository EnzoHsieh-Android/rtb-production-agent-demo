"""真的 Submit:呼叫提案收件口的 /proposals。

把收件口的 HTTP 回應對照回增量 3 定義的 Accepted/SubmitStale/SubmitBusy,加上這個增量新增
的 SubmitRejectedPermanently(收件口的 too_many_revisions:重送不會解決,不是暫時性)。
其餘狀態碼或連線失敗一律讓例外原樣往外傳,由 flow.py 既有的「未定義例外視為可重試」接住,
不在這裡另外分類。

成功回應本文的任務編號、修訂、內容雜湊、處置、擋下原因照型別讀進 Accepted(Phase 5);型別不對的
欄位當成沒有(None),由流程判成「回應不屬於這份提案」、這一輪沒有進展,不在這裡丟例外。
"""

from collections.abc import Callable
from typing import Any

from rtb.analyzer.flow import Accepted, SubmitBusy, SubmitRejectedPermanently, SubmitStale
from rtb.domain._checks import is_plain_int
from rtb.domain.proposal import Proposal
from rtb.httpclient import request_json

_PERMANENT_CODES: dict[int, frozenset[str]] = {409: frozenset({"too_many_revisions"})}
_STALE_CODES: dict[int, frozenset[str]] = {
    409: frozenset({"revision_out_of_order", "content_conflict"}),
    422: frozenset({"expired_proposal", "expiry_too_far", "created_in_future"}),
}
_BUSY_CODES: dict[int, frozenset[str]] = {503: frozenset({"inbox_full", "busy"})}


def _handle_rejection(status: int, body: dict[str, Any]) -> None:
    """S46~S48 的分類是「狀態碼加錯誤代碼」兩者都要對得上,不是只看錯誤代碼字串——單看代碼
    容易被不對應的狀態碼(例如打錯代碼的 500)誤分類成可重試或永久拒收;狀態碼跟代碼對不上
    的,都當成未定義的失敗,原樣往外傳給 flow.py 既有的「未定義例外視為可重試」接住。
    """
    code = body.get("error")
    if code in _PERMANENT_CODES.get(status, frozenset()):
        raise SubmitRejectedPermanently(str(code))
    if code in _STALE_CODES.get(status, frozenset()):
        raise SubmitStale(str(code))
    if code in _BUSY_CODES.get(status, frozenset()):
        raise SubmitBusy(str(code))
    raise RuntimeError(f"收件口回 {status}:{code}")  # 未定義的狀態,原樣往外傳,不改分類


def _text(body: dict[str, Any], name: str) -> str | None:
    value = body.get(name)
    return value if isinstance(value, str) else None


def _accepted(status: int, body: dict[str, Any]) -> Accepted:
    revision = body.get("revision")
    return Accepted(
        replayed=status == 200, task_id=_text(body, "task_id"),
        revision=revision if is_plain_int(revision) else None,
        content_hash=_text(body, "content_hash"), state=_text(body, "state"),
        block_code=_text(body, "block_code"))


def make_client(base_url: str, timeout_seconds: float) -> Callable[[Proposal], Accepted]:
    """回傳一個符合 Submit 協定的函式,綁定收件口的位址與逾時。"""

    def submit(proposal: Proposal) -> Accepted:
        status, body = request_json(
            f"{base_url}/proposals", "POST", proposal.to_primitives(), timeout_seconds)
        if status in (200, 201):
            return _accepted(status, body)
        _handle_rejection(status, body)
        raise AssertionError("_handle_rejection 對每個非成功狀態都應該丟出例外")

    return submit
