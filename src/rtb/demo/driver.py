"""一鍵展示的驅動程式(Phase 12 增量 1):展示伺服器行程內的一條執行緒,依序跑 F1 到 F7(或單一情境)。

每個情境一段獨立的暫存子目錄、自己的一組資料庫與行程,前一個情境的行程全部結束才開下一個。
驅動程式從系統紀錄觀察走到哪(`observe`),每觀察到新節點就寫進展示狀態;情境結束時自己斷言預期
處置:觀察到的路徑要照順序經過必經節點(允許的回頭不算),再拿平台真實狀態比預期寫入;都過才標
「照預期跑完」,否則標「沒跑完」並寫哪一條沒對上。展示自己的失敗(行程起不來、超過時限)也標
「沒跑完」並寫原因,不會顯示成系統擋下。故障一律經展示啟動器排,這裡不碰故障套件。
"""

import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rtb.analyzer import flow, instrumented, policy
from rtb.analyzer.task_store import TaskRow, TaskStore
from rtb.demo import launcher
from rtb.demo.keys import DemoKeys
from rtb.demo.launcher import FaultRequest, Process, Role, StartFailed
from rtb.demo.observe import Observer, PathBuilder, missing_from_path
from rtb.demo.state_store import StateWriter
from rtb.domain.evidence import Evidence
from rtb.domain.proposal import Proposal
from rtb.sqlitekit import connect_read_only

POLL_SECONDS = 0.5  # 觀察到新節點後 3 秒內寫進展示狀態([S1011]):輪詢間隔遠小於 3 秒
DONE, INCOMPLETE = "done", "incomplete"
TENANT = "t-default"
CRASH_EXIT = 9  # 故障套件的猝死點用 os._exit(9)
RACE_WAIT_SECONDS = 10.0
RESTART_SHIFT_SECONDS = 300.0  # 重啟時往後撥:大於租約 60 秒,舊租約已到期可以合法接手
LOOSE_AGGREGATE_LIMIT = 1_000_000_000


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Verdict:
    code: str
    status: str
    reason: str | None
    summary: str | None


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    budget: int
    spend: float  # 花費遠低於預算 = 花得比預期慢,分析端會建議加一成


class ScenarioFailed(Exception):
    """斷言沒過或交叉核對對不上:情境標「沒跑完」,訊息寫哪一條沒對上。"""


@dataclass
class World:
    """一個情境的暫存子目錄、資料庫與行程;離開時把它起的行程全部結束。"""

    root: Path
    code: str
    keys: DemoKeys
    state: StateWriter
    user_env: Mapping[str, str]
    stop: threading.Event
    processes: list[Process] = field(default_factory=list)
    observed: list[str] = field(default_factory=list)
    dsp: Process = field(init=False)
    inbox: Process = field(init=False)
    executor: Process = field(init=False)
    analyzer: Process = field(init=False)

    def __post_init__(self) -> None:
        self.dir = self.root / self.code
        self.dir.mkdir(mode=0o700)
        self.dsp_db, self.inbox_db = self.dir / "dsp.db", self.dir / "inbox.db"
        self.analyzer_db, self.tenants = self.dir / "analyzer.db", self.dir / "tenants.json"
        self._observer = Observer(self.analyzer_db, self.inbox_db)
        self._path = PathBuilder()

    def seed(self, campaigns: Sequence[Campaign], aggregate_limit: int | None = None) -> None:
        from rtb.dsp.store import CampaignStore

        store = CampaignStore(self.dsp_db)
        try:
            for c in campaigns:
                store.seed_campaign(c.campaign_id, budget=c.budget)
                store.seed_metrics(c.campaign_id, "1h", impressions=500, clicks=12,
                                   conversions=1, spend=c.spend, revenue=5.0)
        finally:
            store.close()
        # 沒寫總上限的租戶一筆都放不出去(執行端當成額度 0):不是要展示總上限的情境給一個寬的
        spec: dict[str, object] = {"campaigns": [c.campaign_id for c in campaigns],
                                   "max_budget": 1_000_000,
                                   "aggregate_limit": aggregate_limit or LOOSE_AGGREGATE_LIMIT}
        descriptor = os.open(self.tenants, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            json.dump({"tenants": {TENANT: spec}}, out)

    def start(self, role: Role, args: Sequence[str],
              faults: FaultRequest | None = None) -> Process:
        process = launcher.start(role, list(args), self.keys, root=self.root, faults=faults,
                                 user_env=self.user_env)
        self.processes.append(process)
        return process

    def start_platform(self, faults: FaultRequest | None = None) -> Process:
        self.dsp = self.start(Role.DSP, ["--db", str(self.dsp_db), "--hang-seconds", "1.5",
                                         "--delay-seconds", "0.1"], faults)
        return self.dsp

    def start_inbox(self) -> Process:
        self.inbox = self.start(Role.INBOX, ["--db", str(self.inbox_db)])
        return self.inbox

    def start_executor(self, faults: FaultRequest | None = None,
                       extra: Sequence[str] = ()) -> Process:
        self.executor = self.start(
            Role.EXECUTOR, ["--db", str(self.inbox_db), "--dsp-url", str(self.dsp.url),
                            "--tenant-config", str(self.tenants), "--interval-seconds", "0.05",
                            *extra], faults)
        return self.executor

    def start_analyzer(self) -> Process:
        self.analyzer = self.start(
            Role.ANALYZER, ["--db", str(self.analyzer_db), "--dsp-url", str(self.dsp.url),
                            "--inbox-url", str(self.inbox.url), "--interval-seconds", "0.1"])
        return self.analyzer

    def start_services(self, dsp_faults: FaultRequest | None = None,
                       executor_args: Sequence[str] = (),
                       executor_faults: FaultRequest | None = None) -> None:
        self.start_platform(dsp_faults)
        self.start_inbox()
        self.start_executor(executor_faults, executor_args)
        self.start_analyzer()

    def stop_process(self, process: Process) -> None:
        process.stop()
        self.processes.remove(process)

    def restart_shifted(self, shift_seconds: float) -> None:
        """猝死之後:執行迴圈與模擬 DSP 一起重啟,帶同樣的時鐘偏移(設計審 r3 m3:只撥執行迴圈,
        DSP 會把寫入許可當成來自未來而拒收);DSP 的資料在它的資料庫裡,重啟不丟。分析端改接新的
        DSP 位址。"""
        for process in (self.executor, self.analyzer, self.dsp):
            self.stop_process(process)
        self.start_platform(FaultRequest(Role.DSP, clock_offset_seconds=shift_seconds))
        self.start_executor(FaultRequest(Role.EXECUTOR, clock_offset_seconds=shift_seconds))
        self.start_analyzer()

    def crash_executor(self, limit_seconds: float) -> None:
        """等執行迴圈在排定的猝死點真的死掉(結束代碼 9)。"""
        if not self.watch(lambda: self.executor.poll() is not None, limit_seconds):
            raise ScenarioFailed("時限內執行迴圈沒有在排定的地方猝死")
        if self.executor.poll() != CRASH_EXIT:
            raise ScenarioFailed(f"執行迴圈結束代碼是 {self.executor.poll()},不是猝死")

    def create_task(self, task_id: str, campaign_id: str) -> None:
        store = TaskStore(self.analyzer_db)
        try:
            store.create_task(task_id, campaign_id, _now())
        finally:
            store.close()

    def record(self) -> None:
        for row in self._path.add(self._observer.poll()):
            self.state.record_decision(self.code, row)
            self.observed.append(row.node)

    def watch(self, done: Callable[[], bool], limit_seconds: float) -> bool:
        """輪詢到 done() 成立或超過時限;每一輪都把新觀察到的判斷寫進展示狀態。"""
        deadline = time.monotonic() + limit_seconds
        while time.monotonic() < deadline and not self.stop.is_set():
            self.record()
            if done():
                self.record()
                return True
            self.stop.wait(POLL_SECONDS)
        self.record()
        return False

    def query(self, db: Path, sql: str,
              params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        if not db.is_file():
            return []
        conn = connect_read_only(db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def attempt_states(self, campaign_id: str) -> list[str]:
        return [str(s) for (s,) in self.query(
            self.inbox_db, "SELECT state FROM attempts WHERE campaign_id = ? ORDER BY rowid",
            (campaign_id,))]

    def platform_writes(self, campaign_id: str) -> list[tuple[str, int]]:
        """平台真實狀態(獨立來源):這個廣告被套用的寫入(冪等鍵, 新預算)。"""
        return [(str(key), int(json.loads(str(params)).get("new_budget", -1)))
                for key, params in self.query(
                    self.dsp_db, "SELECT idempotency_key, params_json FROM operations "
                    "WHERE campaign_id = ? ORDER BY operation_id", (campaign_id,))]

    def budget(self, campaign_id: str) -> int | None:
        rows = self.query(self.dsp_db, "SELECT budget FROM campaigns WHERE id = ?",
                          (campaign_id,))
        return None if not rows else int(str(rows[0][0]))

    def require_path(self, required: Sequence[str]) -> None:
        missing = missing_from_path(self.observed, required)
        if missing is not None:
            raise ScenarioFailed(f"觀察到的路徑沒有照順序經過必經的一步:{missing}")

    def close(self) -> None:
        for process in reversed(self.processes):
            process.stop()
        self.processes.clear()


# ---- 情境 ----
@dataclass(frozen=True)
class Scenario:
    code: str
    title: str
    time_limit_seconds: float
    run: Callable[[World], str]  # 回一句結果;斷言沒過丟 ScenarioFailed


F1_REQUIRED = ("a_propose", "x_pending", "x_write", "x_unknown", "x_resend", "x_verify",
               "x_done")


def _run_f1(world: World) -> str:
    """F1:平台第一次寫入逾時、其實沒提交;執行端記成不知道有沒有寫進去,回頭去平台查,用同一個
    編號再送一次,平台上只改一次。"""
    world.seed([Campaign("c1", budget=100, spend=0.5)])
    world.start_services(FaultRequest(Role.DSP, dsp_plan=(("timeout_before_commit", 0.0),)),
                         executor_args=["--dsp-timeout-seconds", "0.5"])
    world.create_task("t1", "c1")
    if not world.watch(lambda: "verified" in world.attempt_states("c1"), 60):
        raise ScenarioFailed("時限內沒有看到寫入被確認")
    world.require_path(F1_REQUIRED)
    _applied_once(world, "c1", 110)  # 預算 100 加一成
    return "第一次送出沒有回音,回頭查過再用同一個編號補送,平台上只改了一次(100 → 110)"


def _applied_once(world: World, campaign_id: str, budget: int) -> None:
    writes = world.platform_writes(campaign_id)
    if len(writes) != 1 or world.budget(campaign_id) != budget:
        raise ScenarioFailed(f"平台真實狀態不對:套用 {len(writes)} 次、"
                             f"預算 {world.budget(campaign_id)}")


def _delivered(world: World, times: int) -> None:
    deliveries = [int(str(d)) for (d,) in world.query(world.inbox_db,
                                                        "SELECT deliveries FROM proposals")]
    if deliveries != [times]:
        raise ScenarioFailed(f"投遞次數不是 {times} 次:{deliveries}")


def _no_rejected_permission(world: World) -> None:
    codes = [str(c) for (c,) in world.query(world.inbox_db, "SELECT code FROM attempts")]
    if "capability_rejected" in codes:  # [S1060]:只撥執行迴圈不撥平台,寫入許可會被拒收
        raise ScenarioFailed("重啟後平台拒收了寫入許可(時鐘沒對齊)")


F2_REQUIRED = ("x_write", "x_unknown", "x_verify", "x_done")


def _run_f2(world: World) -> str:
    """F2:執行迴圈在平台已經改好、還沒記下結果時猝死;重啟後從平台的操作紀錄查到已經寫進去,
    不重送,平台上只改一次。"""
    world.seed([Campaign("c1", budget=100, spend=0.5)])
    world.start_services(executor_faults=FaultRequest(Role.EXECUTOR,
                                                      crash_point="after_dsp_commit"))
    world.create_task("t1", "c1")
    world.crash_executor(30)
    world.restart_shifted(RESTART_SHIFT_SECONDS)
    if not world.watch(lambda: "verified" in world.attempt_states("c1"), 60):
        raise ScenarioFailed("重啟後時限內沒有看到寫入被確認")
    world.require_path(F2_REQUIRED)
    _no_rejected_permission(world)
    _applied_once(world, "c1", 110)  # 預算 100 加一成
    return "寫進平台後執行端當場倒下;重啟後查平台紀錄確認已經寫進去,沒有重送,平台上只改一次"


def _race_two_analyzers(world: World) -> tuple[int, int]:
    """兩個真的並行分析工作者同時推進同一件工作(比照分析端租約測試:柵欄讓兩邊一起出發,持有者
    停在付費呼叫裡直到另一邊試過回來)。回(進入付費呼叫的次數, 這一步寫進幾列)。"""
    before = _history_after_first_step(world.analyzer_db)
    paid: list[int] = []
    someone_returned, barrier = threading.Event(), threading.Barrier(2)

    def worker() -> None:
        own = TaskStore(world.analyzer_db)
        real = instrumented.dsp_evidence_source(own, str(world.dsp.url), 3)

        def paid_evidence(task: TaskRow, now: datetime) -> tuple[Evidence, ...]:
            paid.append(1)
            someone_returned.wait(RACE_WAIT_SECONDS)  # 持有者停在付費呼叫裡,等另一邊試過回來
            return real(task, now)

        try:
            barrier.wait(RACE_WAIT_SECONDS)  # 兩邊一起出發
            flow.advance(own, "t1", paid_evidence, policy.decide, _no_submit, _now(),
                         owner=f"race-{threading.get_ident()}")
        finally:
            someone_returned.set()
            own.close()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(RACE_WAIT_SECONDS * 2)
    return len(paid), _history_after_first_step(world.analyzer_db, advance=False) - before


def _history_after_first_step(db: Path, *, advance: bool = True) -> int:
    """(先推一步:收到工作 → 蒐集資料,這一步不呼叫任何介面、不花錢)回這件工作寫了幾列。"""
    store = TaskStore(db)
    try:
        if advance:
            flow.advance(store, "t1", _no_evidence, _no_decision, _no_submit, _now())
        return len(store.history("t1"))
    finally:
        store.close()


# 推進協定用參數名比對型別,名字要跟協定一樣
def _no_evidence(task: TaskRow, now: datetime) -> tuple[Evidence, ...]:  # noqa: ARG001
    raise AssertionError("這一步不該蒐集資料")


def _no_decision(task: TaskRow, evidence: tuple[Evidence, ...],  # noqa: ARG001
                 now: datetime) -> flow.Decision:  # noqa: ARG001
    raise AssertionError("這一步不該分析")


def _no_submit(proposal: Proposal) -> flow.Accepted:  # noqa: ARG001
    raise AssertionError("這一步不該送件")


F3_REQUIRED = ("x_pick", "x_reclaimed", "x_write", "x_done")


def _run_f3(world: World) -> str:
    """F3:同一則訊息投遞兩次(執行迴圈剛拿起就猝死,重啟後被接手再投遞一次),平台上只改一次;
    另外兩個分析工作者同時搶同一件工作,只有一方花錢分析。"""
    world.seed([Campaign("c1", budget=100, spend=0.5)])
    world.start_platform()
    world.start_inbox()
    world.create_task("t1", "c1")
    paid, rows = _race_two_analyzers(world)
    if paid != 1 or rows != 1:
        raise ScenarioFailed(f"兩個分析工作者同時搶:進入付費呼叫 {paid} 次、寫了 {rows} 列")
    world.start_executor(FaultRequest(Role.EXECUTOR, crash_point="after_receiving"))
    world.start_analyzer()
    world.crash_executor(30)
    world.restart_shifted(RESTART_SHIFT_SECONDS)
    if not world.watch(lambda: "verified" in world.attempt_states("c1"), 60):
        raise ScenarioFailed("重啟後時限內沒有看到寫入被確認")
    world.require_path(F3_REQUIRED)
    _no_rejected_permission(world)
    _delivered(world, 2)
    _applied_once(world, "c1", 110)  # 預算 100 加一成
    return "兩個分析工作者同時搶只有一方花錢;同一則訊息投遞兩次,平台上只改一次"


SCENARIOS: dict[str, Scenario] = {
    "F1": Scenario("F1", "送出去沒有回音,平台到底改了沒?", 90, _run_f1),
    "F2": Scenario("F2", "寫進平台之後執行端當場倒下", 120, _run_f2),
    "F3": Scenario("F3", "同一件工作被處理兩次會不會重複花錢、重複改", 120, _run_f3),
}


class Driver:
    """依序跑情境。keys 由展示伺服器每次展示產生一組、經記憶體交進來(設計審 r2 n6)。"""

    def __init__(  # noqa: PLR0913 - 協作者都可替換,測試在行程內跑
            self, base: Path, demo_id: str, keys: DemoKeys, state: StateWriter, *,
                 user_env: Mapping[str, str] | None = None,
                 scenarios: Mapping[str, Scenario] | None = None,
                 stop: threading.Event | None = None) -> None:
        self.root = launcher.prepare_root(base, demo_id)
        self.keys, self.state = keys, state
        self.user_env = dict(os.environ if user_env is None else user_env)
        self.scenarios = dict(SCENARIOS if scenarios is None else scenarios)
        self.stop = stop or threading.Event()
        self._current: World | None = None

    def cancel(self) -> None:
        """停掉整次展示:不再開下一個情境,正在跑的情境也收掉。"""
        self.stop.set()
        if self._current is not None:
            self._current.stop.set()

    def run(self, codes: Sequence[str]) -> list[Verdict]:
        return [self.run_one(code) for code in codes if not self.stop.is_set()]

    def run_one(self, code: str) -> Verdict:
        scenario = self.scenarios[code]
        self.state.start_scenario(code, _now())
        world = World(self.root, code, self.keys, self.state, self.user_env, threading.Event())
        self._current = world
        verdict = self._attempt(scenario, world)
        self._current = None
        self.state.finish_scenario(code, verdict.status, verdict.reason, verdict.summary, _now())
        return verdict

    def _attempt(self, scenario: Scenario, world: World) -> Verdict:
        result: list[Verdict] = []

        def body() -> None:
            try:
                result.append(Verdict(scenario.code, DONE, None, scenario.run(world)))
            except ScenarioFailed as failed:
                result.append(Verdict(scenario.code, INCOMPLETE, str(failed), None))
            except StartFailed as broken:
                result.append(Verdict(scenario.code, INCOMPLETE, f"展示故障:行程起不來({broken})",
                                      None))
            except Exception as broken:
                result.append(Verdict(scenario.code, INCOMPLETE,
                                      f"展示故障:{type(broken).__name__}: {broken}", None))

        worker = threading.Thread(target=body, daemon=True)
        try:
            worker.start()
            worker.join(scenario.time_limit_seconds)
            if worker.is_alive():
                world.stop.set()  # 只停這個情境的觀察迴圈,不影響整次展示
                limit = f"{scenario.time_limit_seconds:.0f}"
                return Verdict(scenario.code, INCOMPLETE, f"展示故障:超過情境總時限 {limit} 秒",
                               None)
            return result[0]
        finally:
            world.close()
