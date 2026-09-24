"""一鍵展示伺服器(Phase 12 增量 2b):沿用共用行程基礎的伺服器外殼,只綁本機、回帶內容安全政策的 HTML。

路由(計劃〈展示伺服器與觸發〉):
- GET /(?scenario=F3):展示頁;在跑時每 2 秒整頁重讀(頁面自己帶 meta refresh)。
- GET /static/demo.css:同源樣式表。
- GET /approve:獨立確認頁(不重讀)。
- GET /report:這次展示的整份靜態報告。
- POST /run、POST /run/scenario、POST /approve:表單要帶這次伺服器啟動產生的隨機值(hmac 比對)、
  而且是本站頁面送的(共用讀表單的同源檢查);處理完一律 303 轉到 /#current。

同時只准一次展示在跑:一把鎖包住「檢查有沒有在跑 + 標記開始」,「在跑」只在驅動執行緒的 finally 放掉。
每次重讀都開一次展示狀態庫的唯讀開法,呼叫 build_demo_state 組頁面;單一情境重跑只換掉那一個情境。
核可在伺服器行程內簽:簽之前重讀 F7 那筆建議,欄位一律取自驅動程式記下的確認請求,不取自表單。
"""

import argparse
import hmac
import secrets
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from rtb.analyzer.task_store import TaskReader
from rtb.capabilitykit import APPROVAL_KEY_ENV
from rtb.demo import present
from rtb.demo.driver import ALL_CODES, Driver
from rtb.demo.keys import DemoKeys
from rtb.demo.page import (
    CONTENT_SECURITY_POLICY,
    DEMO_CSS,
    render_approval,
    render_page,
    render_report,
)
from rtb.demo.state import DemoState, ScenarioCode
from rtb.demo.state_store import ConfirmationRequest, StateReader, StateWriter
from rtb.domain.proposal import Proposal, content_hash
from rtb.executor import approval
from rtb.executor.capability_signer import load_tenants, tenant_for
from rtb.executor.inbox_store import BlockCode, InboxStore, LifecycleKind, ReadOnlyInbox
from rtb.httpkit import JsonHandler, KitServer, RequestRejected, Response

CURRENT = "/#current"
APPROVER = "demo-operator"
APPROVAL_SECONDS = 300  # 核可有效期:取 min(提案決策到期, 現在 + 這麼多秒)
KEEP_REPORTS = 20  # 全部跑一次、單一情境重跑的報告各留最近這麼多份
DEFAULT_REPORTS = Path.home() / ".rtb" / "demo-reports"
_CODES = frozenset(code.value for code in ScenarioCode)
_AWAITING = LifecycleKind.AWAITING_APPROVAL.value  # 簽之前重讀時,那筆建議要還停在這裡


class DemoBusy(Exception):
    """已有展示在跑(含等人確認與確認逾時之後、自動查核跑完之前)。"""


def new_demo_id(now: datetime) -> str:
    """每按一次一個展示編號:時間加隨機,只有英數與連字號(暫存目錄名、報告檔名都用它)。"""
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"


DriverFactory = Callable[[Path, str, DemoKeys, StateWriter], Driver]


def _real_driver(base: Path, demo_id: str, keys: DemoKeys, state: StateWriter) -> Driver:
    return Driver(base, demo_id, keys, state)


@dataclass
class Run:
    """一次展示(全部跑一次或單一情境重跑)。金鑰只在伺服器記憶體裡。"""

    demo_id: str
    keys: DemoKeys
    driver: Driver
    codes: tuple[str, ...]
    full: bool


@dataclass
class DemoService:
    """伺服器行程內的展示狀態:在跑旗標、這次啟動的表單隨機值、目前與上一次完整執行的展示編號。"""

    base: Path
    state_db: Path
    reports: Path = DEFAULT_REPORTS
    driver_factory: DriverFactory = _real_driver
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    running: bool = False
    current: Run | None = None
    full_demo_id: str | None = None  # 最近一次全部跑一次
    reruns: dict[str, str] = field(default_factory=dict)  # 之後重跑過的情境 → 那次的展示編號
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _ticks: int = 0
    thread: threading.Thread | None = None

    def __post_init__(self) -> None:
        StateWriter(self.state_db, "init").close()  # 先建表:之後頁面只用唯讀開法讀

    # ---- 觸發 ----
    def start(self, codes: Sequence[str], *, full: bool) -> Run:
        """[S1013][S1014] 檢查有沒有在跑與標記開始在同一把鎖裡;在跑時一律拒。"""
        with self._lock:
            if self.running:
                raise DemoBusy("已有展示在跑")
            self.running = True
        try:
            keys = DemoKeys.generate()
            demo_id = new_demo_id(datetime.now(UTC))
            writer = StateWriter(self.state_db, demo_id)
            run = Run(demo_id, keys, self.driver_factory(self.base, demo_id, keys, writer),
                      tuple(codes), full)
            self.current = run
            self.thread = threading.Thread(target=self._drive, args=(run, writer), daemon=True)
            self.thread.start()
        except BaseException:
            self.running = False  # 還沒交給驅動執行緒:這裡放掉
            raise
        return run

    def _drive(self, run: Run, writer: StateWriter) -> None:
        """[S1052] 驅動執行緒:跑完(或出例外)才放掉在跑旗標,只在這個 finally 放。"""
        try:
            if run.full:
                run.driver.run_all(run.codes)
            else:
                run.driver.run(run.codes)
            self._finished(run)
            self._save_report(run)
        except Exception as broken:  # 驅動程式自己出事:記下來,旗標照樣放
            print(f"展示中止:{type(broken).__name__}: {broken}", file=sys.stderr)
        finally:
            writer.close()
            self.running = False

    def _finished(self, run: Run) -> None:
        if run.full:
            self.full_demo_id, self.reruns = run.demo_id, {}
        else:
            self.reruns.update(dict.fromkeys(run.codes, run.demo_id))

    # ---- 讀 ----
    def next_tick(self) -> int:
        with self._lock:
            self._ticks += 1
            return self._ticks

    def state(self, *, finished: bool = False) -> DemoState:
        """[S1020] 目前顯示的展示狀態:最近一次全部跑一次,重跑過的情境換成重跑那一次的。
        在跑時跟著正在跑的那一次。每次都開一次唯讀開法。finished:另存報告時當成已經跑完。"""
        now = datetime.now(UTC)
        reader = StateReader(self.state_db)
        try:
            running = self.running and not finished
            base_id = (self.current.demo_id if running and self.current and self.current.full
                       else self.full_demo_id or (self.current.demo_id if self.current else "none"))
            shown = present.build_demo_state(reader, base_id, running=running, now=now)
            swaps = dict(self.reruns)
            if running and self.current is not None and not self.current.full:
                swaps.update(dict.fromkeys(self.current.codes, self.current.demo_id))
            for code, demo_id in swaps.items():
                other = present.build_demo_state(reader, demo_id, running=running, now=now)
                shown = replace(shown, scenarios=tuple(
                    next(s for s in other.scenarios if s.code.value == code)
                    if s.code.value == code else s for s in shown.scenarios),
                    current=other.current if running else shown.current,
                    approval=other.approval or shown.approval)
            return shown
        finally:
            reader.close()

    # ---- 報告 ----
    def _save_report(self, run: Run) -> Path:
        """[S1039] 展示結束另存一份靜態報告;全部跑一次與單一情境重跑各留最近 20 份。"""
        self.reports.mkdir(parents=True, exist_ok=True, mode=0o700)
        kind = "full" if run.full else "rerun"
        path = self.reports / f"demo-{kind}-{run.demo_id}.html"
        path.write_text(render_report(self.state(finished=True)), encoding="utf-8")
        old = sorted(self.reports.glob(f"demo-{kind}-*.html"))
        for stale in old[:-KEEP_REPORTS]:
            stale.unlink(missing_ok=True)
        return path

    # ---- 核可 ----
    def approve(self, form: dict[str, str]) -> None:
        """[S1032][S1033][S1056] 核對全部過了才在伺服器行程內簽發,寫進 F7 的執行端暫存資料庫。"""
        run, code, request = self._confirmed(form)
        world = run.driver.root / code
        proposal = _still_waiting(world, request)
        _sign(run.keys, world, request, proposal)

    def _confirmed(self, form: dict[str, str]) -> tuple[Run, str, ConfirmationRequest]:
        """[S1032] 表單的展示編號、提案雜湊、數字摘要都對得上等人確認的那一筆,每個數字都勾了。"""
        run = self.current
        if run is None or not self.running:
            raise RequestRejected(409, "no_demo_running")
        reader = StateReader(self.state_db)
        try:
            pending = reader.confirmation(run.demo_id)
        finally:
            reader.close()
        if pending is None:
            raise RequestRejected(409, "no_confirmation_or_expired")
        code, request = pending
        if (not _same(form.get("demo_id", ""), run.demo_id)
                or not _same(form.get("proposal_hash", ""), request.proposal_hash)
                or not _same(form.get("numbers_digest", ""),
                             present.numbers_digest(run.demo_id, request))
                or any(form.get(f"confirm_{i}") != "1" for i in range(len(request.numbers)))):
            raise RequestRejected(403, "confirmation_mismatch")
        return run, code, request


def _same(given: str, expected: str) -> bool:
    """固定時間比對(表單值可能不是 ASCII,先換成位元組)。"""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _sign(keys: DemoKeys, world: Path, request: ConfirmationRequest, proposal: Proposal) -> None:
    """[S1033] 簽發的欄位一律取自驅動程式記下的確認請求:關卡、最大加額、租戶設定、決策到期;確認人
    固定 demo-operator;到期取 min(提案決策到期, 現在 + 固定秒數)。"""
    stage = BlockCode(request.stage)
    tenant = tenant_for(load_tenants(Path(request.tenant_config)), proposal.campaign_id)
    if tenant is None:
        raise RequestRejected(409, "campaign_has_no_tenant")
    now = datetime.now(UTC)
    expires = min(int(request.decision_expires_at.timestamp()),
                  int((now + timedelta(seconds=APPROVAL_SECONDS)).timestamp()))
    token = approval.issue(keys.signing_bytes(APPROVAL_KEY_ENV), proposal, stage, tenant,
                           approver=APPROVER, max_increase=request.max_increase,
                           issued_at=int(now.timestamp()), expires_at=expires)
    store = InboxStore(world / "inbox.db")
    try:
        store.add_approval(proposal, stage, approval.approval_id(token), token, now)
    finally:
        store.close()


def _still_waiting(world: Path, request: ConfirmationRequest) -> Proposal:
    """[S1056] 簽之前重讀:那筆建議仍在等人確認、修訂與內容雜湊相同、還沒過期,才回提案內容。"""
    inbox = ReadOnlyInbox(world / "inbox.db")
    try:
        with inbox.read_transaction() as tx:
            events = inbox.lifecycle_events(tx, request.task_id)
    finally:
        inbox.close()
    latest = [e for e in events if e.revision == request.revision]
    if not latest or latest[-1].kind != _AWAITING:
        raise RequestRejected(409, "no_longer_waiting")
    if latest[-1].content_hash != request.proposal_hash:
        raise RequestRejected(409, "proposal_changed")
    if datetime.now(UTC) >= request.decision_expires_at:
        raise RequestRejected(409, "proposal_expired")
    reader = TaskReader(world / "analyzer.db")
    try:
        history = reader.history(request.task_id)
    finally:
        reader.close()
    for row in reversed(history):
        if row.proposal is not None and content_hash(row.proposal) == request.proposal_hash:
            return row.proposal
    raise RequestRejected(409, "proposal_unreadable")


def _selected(query: str) -> ScenarioCode | None:
    """[S1019] 查詢參數 scenario 只收七個代碼之一;其他值、空值、重複參數都當沒選。"""
    values = parse_qs(query, keep_blank_values=True).get("scenario", [])
    return ScenarioCode(values[0]) if len(values) == 1 and values[0] in _CODES else None


def _page(service: DemoService, query: str) -> Response:
    state = service.state()
    return Response.html(200, render_page(
        state, form_token=service.token, refresh_tick=service.next_tick(),
        selected=None if state.running else _selected(query)))


def _approval_page(service: DemoService, _query: str) -> Response:
    state = service.state()
    if state.approval is None:
        raise RequestRejected(409, "no_confirmation_or_expired")
    return Response.html(200, render_approval(state, form_token=service.token))


_GETS: dict[str, Callable[[DemoService, str], Response]] = {
    "/": _page,
    "/static/demo.css": lambda _service, _query: Response.css(DEMO_CSS),  # [S1062]
    "/approve": _approval_page,
    "/report": lambda service, _query: Response.html(200, render_report(service.state())),
}


def _post(service: DemoService, path: str, form: dict[str, str]) -> Response:
    """[S1015][S1016][S1063] 每個 POST 同一套防護,處理完 303 轉到 /#current。"""
    if not _same(form.get("token", ""), service.token):
        raise RequestRejected(403, "bad_form_token")
    try:
        if path == "/run":
            service.start(ALL_CODES, full=True)
        elif path == "/run/scenario":
            code = form.get("scenario", "")
            if code not in _CODES:
                raise RequestRejected(400, "unknown_scenario")
            service.start((code,), full=False)
        else:
            service.approve(form)
    except DemoBusy:
        pass  # [S1013] 已有展示在跑:不再啟動,回到那一次的進度
    return Response.redirect(CURRENT)


def make_handler(service: DemoService) -> type[JsonHandler]:
    class DemoHandler(JsonHandler):
        require_host = True  # [S1017] 連 HTTP/1.0 也要帶本機的主機標頭
        content_security_policy = CONTENT_SECURITY_POLICY  # [S1023] 每個回應都帶

        def handle_request(self, method: str) -> Response:
            parts = urlsplit(self.path)
            if method == "GET" and parts.path in _GETS:
                return _GETS[parts.path](service, parts.query)
            if method == "POST" and parts.path in {"/run", "/run/scenario", "/approve"}:
                return _post(service, parts.path, self.read_form())  # 同源、重複欄位、上限
            raise RequestRejected(404, "not_found")

    return DemoHandler


def serve(service: DemoService) -> KitServer:
    """[S1018] 只綁回送位址(共用伺服器外殼只收 127.0.0.1,連接埠由系統給)。"""
    return KitServer(make_handler(service))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False, description="一鍵展示伺服器(只綁本機)")
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="暫存目錄:每次展示的資料庫與行程都放這底下")
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    args = parser.parse_args(argv)
    args.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    service = DemoService(args.work_dir / "demos", args.work_dir / "state.db", args.reports)
    server = serve(service)
    print(f"PORT={server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

