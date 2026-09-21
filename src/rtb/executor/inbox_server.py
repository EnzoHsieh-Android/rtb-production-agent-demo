"""提案收件口(執行行程,獨立行程):不可信的提案從這裡進來。

只做「安全地收、去重、記帳」:不執行提案、不呼叫 DSP、不做政策與租戶檢查。
每個請求依序:Host 檢查(共用基礎)→ 路由 → 故障標頭 → Origin 與 Content-Type →
讀本文 → 嚴格解析(增量 1 的 parse_proposal,收件口自己沒有任何欄位驗證)→ 收件交易。
錯誤回應只含固定的錯誤代碼與必要數字,絕不回顯請求內容。
"""

import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from rtb.domain.proposal import parse_proposal
from rtb.executor.inbox_store import (
    DEFAULT_MAX_PENDING,
    Accepted,
    ContentConflict,
    CreatedInFuture,
    ExpiryTooFar,
    InboxBusy,
    InboxFull,
    InboxRejected,
    InboxStore,
    ProposalExpired,
    RevisionOutOfOrder,
    TooManyRevisions,
    utc_now,
)
from rtb.httpkit import (
    LOOPBACK,
    SOCKET_TIMEOUT_SECONDS,
    JsonHandler,
    KitServer,
    NoResponse,
    RequestRejected,
)
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS

PROPOSALS_PATH = "/proposals"
FAULT_MODES = frozenset({"crash_before_commit", "crash_after_commit"})
REJECTION_STATUS = {
    ContentConflict: 409,
    RevisionOutOfOrder: 409,
    ProposalExpired: 422,
    ExpiryTooFar: 422,
    CreatedInFuture: 422,
    TooManyRevisions: 409,
    InboxFull: 503,
    InboxBusy: 503,
}


def _crash_before_commit() -> None:
    raise NoResponse  # 交易還沒提交就中斷:回滾,呼叫者什麼都收不到


class InboxHandler(JsonHandler):
    server: InboxServer
    require_host = True  # 連 HTTP/1.0 也必須帶回送位址的 Host

    def handle_request(self, method: str) -> tuple[int, dict[str, Any]]:
        if method != "POST" or urlsplit(self.path).path != PROPOSALS_PATH:
            raise RequestRejected(404, "not_found")
        fault = self.read_fault(FAULT_MODES)
        if self.headers.get("Origin") is not None:  # 瀏覽器的跨站請求會帶;程式呼叫者不帶
            raise RequestRejected(403, "origin_not_allowed")
        media_type = (self.single_header("Content-Type") or "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise RequestRejected(415, "unsupported_media_type")
        parsed = parse_proposal(self.read_json())
        if parsed.proposal is None:
            # 解析錯誤清單含請求裡的欄位名稱,不回給呼叫者;格式不合法的請求也不碰資料庫、不記事件
            raise RequestRejected(400, "invalid_proposal")
        proposal = parsed.proposal
        store = InboxStore(self.server.db_path, self.server.max_pending,
                           self.server.busy_timeout_seconds)
        try:
            before = _crash_before_commit if fault == "crash_before_commit" else None
            try:
                result = store.accept(proposal, self.server.clock, before)
            except InboxRejected as rejection:
                if not isinstance(rejection, InboxBusy):
                    store.record_event(rejection.code, proposal, self.server.clock())
                raise
        finally:
            store.close()
        if fault == "crash_after_commit":
            raise NoResponse  # 已提交,但呼叫者收不到回應:重送會得到重送結果
        return (200 if result.replayed else 201), _accepted_body(result)

    def map_exception(self, exc: Exception) -> tuple[int, str, bool] | None:
        if isinstance(exc, InboxRejected):
            return REJECTION_STATUS[type(exc)], exc.code, exc.retryable
        return None

    def error_extras(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, InboxRejected) and exc.highest_revision is not None:
            return {"highest_revision": exc.highest_revision}
        return {}


def _accepted_body(result: Accepted) -> dict[str, Any]:
    # state 才是提案目前的狀態:重送一份已過期的提案,status 仍是 accepted(收過了),
    # 但 state 會是 expired,代表它已經不能再被執行
    return {"status": "accepted", "task_id": result.task_id, "revision": result.revision,
            "state": result.state, "content_hash": result.content_hash,
            "replayed": result.replayed}


class InboxServer(KitServer):
    def __init__(  # noqa: PLR0913 - 啟動參數,全部有預設值,測試與命令列各用一部分
        self,
        db_path: Path,
        max_pending: int = DEFAULT_MAX_PENDING,
        clock: Callable[[], datetime] = utc_now,
        busy_timeout_seconds: float = BUSY_TIMEOUT_SECONDS,
        socket_timeout_seconds: float = SOCKET_TIMEOUT_SECONDS,
        fault_injection: bool = False,
        host: str = LOOPBACK,
    ):
        super().__init__(InboxHandler, socket_timeout_seconds, host, fault_injection)
        self.db_path, self.max_pending, self.clock = db_path, max_pending, clock
        self.busy_timeout_seconds = busy_timeout_seconds
        try:
            InboxStore(db_path, max_pending, busy_timeout_seconds).close()  # 確保資料庫與表已建立
        except BaseException:
            self.server_close()  # 啟動失敗:把已經綁定的通訊埠還回去
            raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="提案收件口")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--host", default=LOOPBACK)
    parser.add_argument("--max-pending", type=int, default=DEFAULT_MAX_PENDING)
    parser.add_argument("--fault-injection", action="store_true")
    parser.add_argument("--busy-timeout-seconds", type=float, default=BUSY_TIMEOUT_SECONDS)
    parser.add_argument("--socket-timeout-seconds", type=float, default=SOCKET_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        server = InboxServer(args.db, args.max_pending, utc_now, args.busy_timeout_seconds,
                             args.socket_timeout_seconds, args.fault_injection, args.host)
    except (ValueError, InboxRejected) as error:
        sys.stderr.write(f"拒絕啟動:{error}\n")
        raise SystemExit(2) from error
    print(f"PORT={server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
