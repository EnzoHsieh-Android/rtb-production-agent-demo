"""真的 Submit:呼叫提案收件口的 /proposals。

把收件口的 HTTP 回應對照回增量 3 定義的 Accepted/SubmitStale/SubmitBusy,加上這個增量新增
的 SubmitRejectedPermanently(收件口的 too_many_revisions:重送不會解決,不是暫時性)。
其餘狀態碼或連線失敗一律讓例外原樣往外傳,由 flow.py 既有的「未定義例外視為可重試」接住,
不在這裡另外分類。
"""

from collections.abc import Callable
from typing import Any

from rtb.analyzer.flow import Accepted, SubmitBusy, SubmitRejectedPermanently, SubmitStale
from rtb.domain.proposal import Proposal
from rtb.httpclient import request_json

_STALE_CODES = frozenset(
    {"revision_out_of_order", "content_conflict", "expired_proposal", "expiry_too_far",
     "created_in_future"}
)


def _handle_rejection(status: int, body: dict[str, Any]) -> None:
    code = body.get("error")
    if code == "too_many_revisions":
        raise SubmitRejectedPermanently(str(code))
    if code in _STALE_CODES:
        raise SubmitStale(str(code))
    if code in ("inbox_full", "busy"):
        raise SubmitBusy(str(code))
    raise RuntimeError(f"收件口回 {status}:{code}")  # 未定義的狀態,原樣往外傳,不改分類


def make_client(base_url: str, timeout_seconds: float) -> Callable[[Proposal], Accepted]:
    """回傳一個符合 Submit 協定的函式,綁定收件口的位址與逾時。"""

    def submit(proposal: Proposal) -> Accepted:
        status, body = request_json(
            f"{base_url}/proposals", "POST", proposal.to_primitives(), timeout_seconds)
        if status in (200, 201):
            return Accepted(replayed=status == 200)
        _handle_rejection(status, body)
        raise AssertionError("_handle_rejection 對每個非成功狀態都應該丟出例外")

    return submit
