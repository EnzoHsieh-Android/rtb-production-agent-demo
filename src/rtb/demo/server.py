"""一鍵展示伺服器(Phase 12 增量 2b):沿用共用行程基礎的伺服器外殼,只綁本機、回帶內容安全政策的 HTML。

路由(計劃〈展示伺服器與觸發〉):
- GET /(?scenario=F3):展示頁;在跑時每 2 秒整頁重讀(頁面自己帶 meta refresh)。
- GET /static/demo.css:同源樣式表。
- GET /approve:獨立確認頁(不重讀)。
- GET /report:這次展示的整份報告(經伺服器送時連同源樣式表;另存的檔案才把樣式內嵌)。
- POST /run、POST /run/scenario、POST /approve:表單要帶這次伺服器啟動產生的隨機值(hmac 比對)、
  而且是本站頁面送的(共用讀表單的同源檢查);處理完 303 轉到 /#current,核對不過的拒絕照回錯誤頁。

同時只准一次展示在跑:一把鎖包住「檢查有沒有在跑 + 標記開始」,「在跑」只在驅動執行緒的 finally 放掉。
每次重讀都開一次展示狀態庫的唯讀開法,呼叫 build_demo_state 組頁面;單一情境重跑只換掉那一個情境,
完整展示在跑時不套用之前的重跑結果(代碼審 r1 v1)。
核可在伺服器行程內簽:跟驅動程式關確認窗在同一個寫入交易裡互斥,簽之前從收件口重讀那筆建議,欄位
一律取自驅動程式記下的確認請求,不取自表單。伺服器被 Ctrl-C 或 SIGTERM 結束時先停掉正在跑的展示、
等驅動執行緒收完行程(代碼審 r1 v2)。
"""

import argparse
import hmac
import os
import re
import secrets
import signal
import stat
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from urllib.parse import parse_qs, urlsplit

from rtb import modelledger_view
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
from rtb.demo.state import CurrentStep, DemoState, ScenarioCode
from rtb.demo.state_store import (
    NO_CONFIRMATION,
    ConfirmationClosed,
    ConfirmationRequest,
    StateReader,
    StateWriter,
)
from rtb.domain.proposal import Proposal
from rtb.executor import approval
from rtb.executor.approval import ApprovalRefused
from rtb.executor.capability_signer import SigningRefused, load_tenants, tenant_for
from rtb.executor.inbox_store import BlockCode, InboxBusy, InboxStore
from rtb.httpkit import JsonHandler, KitServer, RequestRejected, Response

CURRENT = "/#current"
APPROVER = "demo-operator"
APPROVAL_SECONDS = 300  # 核可有效期:取 min(提案決策到期, 現在 + 這麼多秒)
KEEP_REPORTS = 20  # 全部跑一次、單一情境重跑的報告各留最近這麼多份
STOP_JOIN_SECONDS = 60.0  # 伺服器結束時等驅動執行緒收完行程的上限
_CODES = frozenset(code.value for code in ScenarioCode)
_REPORT_NAME = re.compile(r"demo-(full|rerun)-\d{8}-\d{6}-[0-9a-f]{8}\.html")
# 每個回應都帶:不快取(主頁與確認頁帶表單隨機值)、不猜內容型別、不送來源網址(代碼審 r1 s6)
_SAFE_HEADERS = (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                 ("Referrer-Policy", "no-referrer"))


def default_reports() -> Path:
    """報告放帳號家目錄下(跟花費帳同一個「家」,不看 HOME;代碼審 r1 a4),用到時才算。"""
    return modelledger_view.account_home() / ".rtb" / "demo-reports"


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


@dataclass(frozen=True)
class _View:
    """組一次頁面要的伺服器狀態,在鎖裡一次取齊(不會讀到換到一半的展示編號與重跑對照)。"""

    running: Run | None
    full_demo_id: str | None
    reruns: tuple[tuple[str, str], ...]
    last: Run | None


@dataclass
class DemoService:
    """伺服器行程內的展示狀態:在跑旗標、這次啟動的表單隨機值、目前與上一次完整執行的展示編號。"""

    base: Path
    state_db: Path
    reports: Path = field(default_factory=default_reports)
    driver_factory: DriverFactory = _real_driver
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    running: bool = False
    current: Run | None = None
    full_demo_id: str | None = None  # 最近一次跑完的全部跑一次
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
            with self._lock:
                self.current = run
            self.thread = threading.Thread(target=self._drive, args=(run, writer), daemon=True)
            self.thread.start()
        except BaseException:
            self.running = False  # 還沒交給驅動執行緒:這裡放掉
            raise
        return run

    def _drive(self, run: Run, writer: StateWriter) -> None:
        """[S1052] 驅動執行緒:跑完(或出例外)才放掉在跑旗標,只在這個 finally 放。驅動程式自己出事時
        這次沒結案的情境標成沒跑完、照樣換成這一次並另存報告,不默默退回上一次(代碼審 r1 v4)。"""
        try:
            try:
                if run.full:
                    run.driver.run_all(run.codes)
                else:
                    run.driver.run(run.codes)
            except Exception as broken:
                print(f"展示中止:{type(broken).__name__}: {broken}", file=sys.stderr)
                writer.abandon(run.codes, f"展示故障:驅動程式出錯({type(broken).__name__})",
                               datetime.now(UTC))
            self._finished(run)
            self._save_report(run)
        except Exception as broken:  # 連記錄與另存都出事:印出來,旗標照樣放
            print(f"展示收尾失敗:{type(broken).__name__}: {broken}", file=sys.stderr)
        finally:
            writer.close()
            self.running = False

    def _finished(self, run: Run) -> None:
        with self._lock:
            if run.full:
                self.full_demo_id, self.reruns = run.demo_id, {}
            else:
                self.reruns.update(dict.fromkeys(run.codes, run.demo_id))

    def stop(self, timeout: float = STOP_JOIN_SECONDS) -> None:
        """停掉正在跑的展示,等驅動執行緒收完行程(伺服器結束的收尾路徑)。"""
        run, thread = self.current, self.thread
        if run is not None and self.running:
            run.driver.cancel()
        if thread is not None:
            thread.join(timeout)

    # ---- 讀 ----
    def next_tick(self) -> int:
        with self._lock:
            self._ticks += 1
            return self._ticks

    def _view(self, finished: bool) -> _View:
        with self._lock:
            run = self.current
            running = run if self.running and not finished else None
            return _View(running, self.full_demo_id, tuple(self.reruns.items()), run)

    def state(self, *, finished: bool = False) -> DemoState:
        """[S1020][S1021] 目前顯示的展示狀態。
        - 完整展示在跑:整頁只看這一次;之前的重跑結果不套用,驗證器只看這一次的(還沒跑到就是空的)。
        - 單一情境重跑在跑:最近一次完整展示,重跑過的換成重跑那一次的,正在跑的那個換成這一次;
          驗證器沿用最近一次完整展示的。
        - 沒在跑:最近一次完整展示加之後的重跑。
        「現在進度」只取正在跑的那一次,沒在跑就沒有。finished:另存報告時當成已經跑完。"""
        view = self._view(finished)
        now = datetime.now(UTC)
        reader = StateReader(self.state_db)
        try:
            live = view.running
            if live is not None and live.full:
                return present.build_demo_state(
                    reader, live.demo_id, running=True, now=now, verifier_demo_id=live.demo_id,
                    full_demo_id=view.full_demo_id, running_full=True)
            base_id = view.full_demo_id or (view.last.demo_id if view.last else None)
            shown = present.build_demo_state(
                reader, base_id, running=False, now=now, verifier_demo_id=view.full_demo_id,
                full_demo_id=view.full_demo_id)
            swaps = dict(view.reruns)
            if live is not None:
                swaps.update(dict.fromkeys(live.codes, live.demo_id))
            current: CurrentStep | None = None
            for code, demo_id in swaps.items():
                is_live = live is not None and demo_id == live.demo_id
                other = present.build_demo_state(
                    reader, demo_id, running=is_live, now=now, verifier_demo_id=None,
                    full_demo_id=view.full_demo_id)
                shown = replace(shown, scenarios=tuple(
                    next(s for s in other.scenarios if s.code.value == code)
                    if s.code.value == code else s for s in shown.scenarios),
                    approval=other.approval or shown.approval)
                if is_live:
                    current = other.current
            return replace(shown, running=live is not None, current=current)
        finally:
            reader.close()

    # ---- 報告 ----
    def _save_report(self, run: Run) -> Path:
        """[S1039] 展示結束另存一份靜態報告(樣式內嵌);全部跑一次與單一情境重跑各留最近 20 份。
        先寫暫存檔再換名、檔案只給自己讀寫;清理只刪檔名完全符合報告格式、而且不是符號連結的檔
        (代碼審 r1 v7/s4)。"""
        self.reports.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_IMODE(self.reports.stat().st_mode) & 0o077:
            self.reports.chmod(0o700)  # 已經存在、權限比只給自己寬:收緊
        kind = "full" if run.full else "rerun"
        path = self.reports / f"demo-{kind}-{run.demo_id}.html"
        temporary = self.reports / f".{path.name}.{secrets.token_hex(4)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(render_report(self.state(finished=True), inline_styles=True))
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        mine = sorted(item for item in self.reports.iterdir()
                      if _REPORT_NAME.fullmatch(item.name)
                      and item.name.startswith(f"demo-{kind}-") and not item.is_symlink())
        for stale in mine[:-KEEP_REPORTS]:
            stale.unlink(missing_ok=True)
        return path

    # ---- 核可 ----
    def approve(self, form: dict[str, str]) -> None:
        """[S1032][S1033][S1056] 核對全部過了才在伺服器行程內簽發,寫進 F7 的執行端暫存資料庫。
        「那一筆還在等、確認期限沒過、還沒確認過」跟簽發在同一個寫入交易裡(跟驅動程式關窗互斥)。"""
        run = self.current
        if run is None or not self.running:
            raise RequestRejected(409, "no_demo_running")
        writer = StateWriter(self.state_db, run.demo_id)

        def sign(code: str, request: ConfirmationRequest) -> None:
            _check_form(form, run.demo_id, request)
            store = InboxStore(run.driver.root / code / "inbox.db")
            try:
                _sign(store, run.keys, request, _still_waiting(store, request))
            finally:
                store.close()

        try:
            writer.answer_confirmation(datetime.now(UTC), sign)
        finally:
            writer.close()


def _same(given: str, expected: str) -> bool:
    """固定時間比對(表單值可能不是 ASCII,先換成位元組)。"""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _check_form(form: dict[str, str], demo_id: str, request: ConfirmationRequest) -> None:
    """[S1032] 表單的展示編號、提案雜湊、數字摘要都對得上等人確認的那一筆,每個數字都勾了。"""
    if (not _same(form.get("demo_id", ""), demo_id)
            or not _same(form.get("proposal_hash", ""), request.proposal_hash)
            or not _same(form.get("numbers_digest", ""),
                         present.numbers_digest(demo_id, request))
            or any(form.get(f"confirm_{i}") != "1" for i in range(len(request.numbers)))):
        raise RequestRejected(403, "confirmation_mismatch")


def _sign(store: InboxStore, keys: DemoKeys, request: ConfirmationRequest,
          proposal: Proposal) -> None:
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
    store.add_approval(proposal, stage, approval.approval_id(token), token, now)


def _still_waiting(store: InboxStore, request: ConfirmationRequest) -> Proposal:
    """[S1056] 簽之前從收件口重讀(跟管理工具同一支 find_proposal,代碼審 r1 a2):那筆建議仍在等人
    核可、停的就是確認請求記的那一關、內容雜湊相同、還沒過期,才回收件口存的那份提案。"""
    waiting = store.find_proposal(request.task_id, request.revision)
    if waiting is None:
        raise RequestRejected(409, "no_longer_waiting")
    if waiting.stage.value != request.stage:
        raise RequestRejected(409, "stage_changed")
    if waiting.message.content_hash != request.proposal_hash:
        raise RequestRejected(409, "proposal_changed")
    if datetime.now(UTC) >= request.decision_expires_at:
        raise RequestRejected(409, "proposal_expired")
    return waiting.message.proposal


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
        run = service.current
        reason = NO_CONFIRMATION
        if run is not None:
            reader = StateReader(service.state_db)
            try:
                reason = reader.confirmation_refusal(run.demo_id)
            finally:
                reader.close()
        raise RequestRejected(409, reason)
    return Response.html(200, render_approval(state, form_token=service.token))


def _report(service: DemoService, _query: str) -> Response:
    """經伺服器送的報告連同源樣式表(內容安全政策不准內嵌樣式;代碼審 r1 p2)。"""
    return Response.html(200, render_report(service.state(), inline_styles=False))


_GETS: dict[str, Callable[[DemoService, str], Response]] = {
    "/": _page,
    "/static/demo.css": lambda _service, _query: Response.css(DEMO_CSS),  # [S1062]
    "/approve": _approval_page,
    "/report": _report,
}


def _post(service: DemoService, path: str, form: dict[str, str]) -> Response:
    """[S1015][S1016][S1063] 每個 POST 同一套防護;處理完 303 轉到 /#current(核對不過的拒絕
    回錯誤頁)。"""
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


class DemoHandler(JsonHandler):
    """展示頁的處理器(跟收件口、DSP 同一種接線:伺服器子類別帶設定,處理器宣告 server 型別)。"""

    server: DemoServer
    require_host = True  # [S1017] 連 HTTP/1.0 也要帶本機的主機標頭
    content_security_policy = CONTENT_SECURITY_POLICY  # [S1023] 每個回應都帶
    extra_headers = _SAFE_HEADERS

    def handle_request(self, method: str) -> Response:
        parts = urlsplit(self.path)
        service = self.server.service
        if method == "GET" and parts.path in _GETS:
            return _GETS[parts.path](service, parts.query)
        if method == "POST" and parts.path in {"/run", "/run/scenario", "/approve"}:
            return _post(service, parts.path, self.read_form())  # 同源、重複欄位、上限
        raise RequestRejected(404, "not_found")

    def map_exception(self, exc: Exception) -> tuple[int, str, bool] | None:
        """核可路上的領域例外照既有管理工具的分法回:收件口忙碌可以重試,拒簽與確認窗已關帶原因代碼
        (代碼審 r1 a3)。"""
        if isinstance(exc, InboxBusy):
            return 503, exc.code, True
        if isinstance(exc, ApprovalRefused | SigningRefused):
            return 409, exc.reason, False
        if isinstance(exc, ConfirmationClosed):
            return 409, exc.code, False
        return None


class DemoServer(KitServer):
    """[S1018] 只綁回送位址(共用伺服器外殼只收 127.0.0.1,連接埠由系統給)。"""

    def __init__(self, service: DemoService) -> None:
        super().__init__(DemoHandler)
        self.service = service


def serve(service: DemoService) -> DemoServer:
    return DemoServer(service)


def _terminate(_signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(128 + signal.SIGTERM)  # 跟 Ctrl-C 走同一條收尾路徑


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False, description="一鍵展示伺服器(只綁本機)")
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="暫存目錄:每次展示的資料庫與行程都放這底下")
    parser.add_argument("--reports", type=Path, default=None,
                        help="報告目錄(預設帳號家目錄下的 .rtb/demo-reports)")
    args = parser.parse_args(argv)
    args.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    service = DemoService(args.work_dir / "demos", args.work_dir / "state.db",
                          args.reports or default_reports())
    server = serve(service)
    signal.signal(signal.SIGTERM, _terminate)
    print(f"PORT={server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.stop()  # [代碼審 r1 v2] 停掉正在跑的展示,等驅動執行緒收完它起的行程


if __name__ == "__main__":
    main()
