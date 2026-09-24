"""開了 AI 決策的分析端驅動命令列(Phase 13 增量 2,計劃〈共用模型入口與行程〉〈租約、逾時與停止訊
號〉〈錄製批次與入庫〉〈F5 對抗案例的預期〉):真的 DSP 與收件口(行程內)、假的模型閘道或錄製模式,
不碰真模型([S1113] [S1116] [S1137] [S1142] [S1156] [S1160],以及 [S1128] [S1138] [S1114]的整條路
徑)。"""

import ast
import io
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from rtb import modelcore as core
from rtb import stepbudget
from rtb.analyzer import instrumented, investigation, modelgate, runner
from rtb.analyzer.task_store import TaskReader, TaskStore
from rtb.domain.task_state import TaskState
from rtb.dsp import seed
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor.inbox_server import InboxServer
from tests.model.fakes import FakeBackend, live, recorded

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"
_RECENT = seed.DayFigures(1000, 24, 5, 3.0, 25.0)
_EARLIER = seed.DayFigures(1000, 24, 8, 3.0, 40.0)
PROFILE = seed.HistoryProfile(daily=(_RECENT,) * 3 + (_EARLIER,) * 4)


def _propose(ref="base", field="conversions", value="1"):
    return json.dumps({"choice": "propose", "reason": "有轉換",
                       "evidence": [{"ref": ref, "field": field, "value": value}]})


def _answer(choice, *cited):
    return json.dumps({"choice": choice, "reason": "理由",
                       "evidence": [{"ref": r, "field": f, "value": v} for r, f, v in cited]})


class FakeGate:
    """假的模型閘道(錄製模式的形狀):依序回答,記下送出內容與呼叫順序。"""

    def __init__(self, answers, *, events=None, preflight=modelgate.Preflight.NOT_APPLICABLE,
                 settings=None):
        self.answers = list(answers)
        self.sent, self.events = [], events if events is not None else []
        self.preflight = preflight
        self.settings = settings or recorded()
        self.notices = ()

    @property
    def mode(self):
        return self.settings.mode

    def complete(self, caller, system, user, *, max_output_tokens, timeout_seconds):
        assert caller is modelgate.Caller.INVESTIGATION
        assert timeout_seconds == stepbudget.MODEL_TIMEOUT_SECONDS
        self.sent.append((system, user))
        self.events.append("model")
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return core.ModelResult(answer, core.Source.RECORDED, 0, 0, 0, 0, 0, 0, 1.0, "k", None)

    def preflight_login(self):
        self.events.append("preflight")
        return modelgate.LoginPreflight(self.preflight, None if self.preflight
                                        is not modelgate.Preflight.FAILED else "沒登入")

    def check_recordings(self):
        self.events.append("check_recordings")


class AiWorld:
    """一個情境:行程內的 DSP 與收件口、分析端資料庫、假的模型閘道。"""

    def __init__(self, tmp_path, answers, *, tasks=(("t1", "c1"),), extra=(), gate=None):
        self.root = Path(tmp_path)
        self.root.mkdir(parents=True, exist_ok=True)
        store = CampaignStore(self.root / "dsp.db")
        campaigns = sorted({c for _t, c in tasks})
        for campaign in campaigns:
            store.seed_campaign(campaign, budget=100)
            store.seed_metrics(campaign, "1h", impressions=500, clicks=12, conversions=1,
                               spend=0.5, revenue=5.0)
        seed.seed_platform_history(store, dict.fromkeys(campaigns, PROFILE), datetime.now(UTC))
        store.close()
        tasks_db = TaskStore(self.root / "analyzer.db")
        for task_id, campaign in tasks:
            tasks_db.create_task(task_id, campaign, datetime.now(UTC))
        tasks_db.close()
        self.gate = gate or FakeGate(answers)
        self.extra = list(extra)

    @property
    def sent(self):
        return self.gate.sent

    @property
    def model_calls(self):
        return len(self.gate.sent)

    def argv(self, dsp, inbox):
        return ["--db", str(self.root / "analyzer.db"), "--dsp-url", dsp, "--inbox-url", inbox,
                "--timeout-seconds", "2", "--interval-seconds", "0.01", "--ai-judge", *self.extra]

    def run_until_done(self, max_rounds=40, **kwargs):
        dsp = DspServer(self.root / "dsp.db", fault_injection=False, hang_seconds=0.2,
                        delay_seconds=0.0)
        inbox = InboxServer(self.root / "inbox.db", fault_injection=False)
        for server in (dsp, inbox):
            threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
        self.out, self.err = io.StringIO(), io.StringIO()
        try:
            self.code = runner.run(
                self.argv(f"http://127.0.0.1:{dsp.server_address[1]}",
                          f"http://127.0.0.1:{inbox.server_address[1]}"),
                max_rounds=max_rounds, out=self.out, err=self.err,
                open_gate=lambda _env, **_kw: self.gate, **kwargs)
        finally:
            for server in (dsp, inbox):
                server.shutdown()
                server.server_close()
        return self.code

    def reader(self):
        return TaskReader(self.root / "analyzer.db")

    def latest(self, task_id="t1"):
        reader = self.reader()
        try:
            return reader.latest(task_id), reader.no_action_reason(
                task_id, reader.latest(task_id).seq)
        finally:
            reader.close()

    def rounds(self, task_id="t1"):
        reader = self.reader()
        try:
            return reader.investigation_rounds(task_id)
        finally:
            reader.close()

    def calls(self, task_id="t1"):
        reader = self.reader()
        try:
            return reader.list_tool_calls(task_id)
        finally:
            reader.close()

    def endpoints_after_first_answer(self):
        return [c.endpoint.value for c in self.calls()
                if c.endpoint.value in ("dsp:daily", "dsp:metrics", "dsp:history",
                                        "dsp:adjustments")]

    def submitted(self):
        with sqlite3.connect(self.root / "inbox.db") as conn:
            return [r[0] for r in conn.execute("SELECT task_id FROM proposals")]

    def leases(self, task_id="t1"):
        with sqlite3.connect(self.root / "analyzer.db") as conn:
            return conn.execute("SELECT lease_seq, owner FROM task_leases WHERE task_id = ? "
                                "ORDER BY lease_seq", (task_id,)).fetchall()


def collect_with_rounds(tmp_path, rounds):
    """一件工作帶著這些調查紀錄時,蒐證那一步讀了哪些端點(依序)。"""
    world = AiWorld(tmp_path, ["x"])
    store = TaskStore(world.root / "analyzer.db")
    try:
        now = datetime.now(UTC)
        store.commit_step("t1", 1, TaskState.COLLECTING_EVIDENCE, now)
        for index, record in enumerate(rounds):  # 測試直接寫紀錄列(形狀同提交函式寫的)
            store._conn.execute(
                "INSERT INTO investigation_rounds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("t1", 100 + index, record.round, record.kind, record.choice, record.decided_by,
                 None, record.fallback, record.reason_code, None, None, "2026-01-01"))
        dsp = DspServer(world.root / "dsp.db", fault_injection=False, hang_seconds=0.2,
                        delay_seconds=0.0)
        threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
        try:
            source = instrumented.investigation_source(
                store, f"http://127.0.0.1:{dsp.server_address[1]}", 2.0)
            source(store.latest("t1"), now)
        finally:
            dsp.shutdown()
            dsp.server_close()
        return [c.endpoint.value for c in store.list_tool_calls("t1")]
    finally:
        store.close()


# ---- 整條路徑 ----
def test_an_ai_task_queries_then_concludes_and_the_proposal_goes_to_the_inbox(tmp_path):
    world = AiWorld(tmp_path, [_answer(["check_daily_trend"]),
                               _propose("check_daily_trend", "conversions_change", "-37.5")])
    assert world.run_until_done() == 0, world.err.getvalue()
    row, _ = world.latest()
    assert row.state is TaskState.HANDED_OFF
    kinds = [(r.kind, r.choice, r.reason_code) for _s, r in world.rounds()]
    assert kinds == [("query", "check_daily_trend", "ai_query"), ("conclusion", "propose", None)]
    assert world.submitted() == ["t1"]


# ---- [S1113] ----
def test_the_runner_refuses_a_model_timeout_that_outlives_the_lease(tmp_path, monkeypatch):
    """[S1113] 帶 --ai-judge:蒐證那一步(取租約等鎖 + 6 次讀取各自的逾時加呼叫紀錄等鎖 + 提交等鎖
    )或 AI那一步(模型逾時 15 + 清理 10 + 花費帳等鎖 20 + 提交等鎖 5)不小於租約就拒絕啟動;數字取
    自真常數。沒帶 --ai-judge 照舊只守 [S1001]。"""
    def refused(timeout, *extra):
        err = io.StringIO()
        code = runner.run(["--db", str(tmp_path / "a.db"), "--dsp-url", "http://127.0.0.1:9",
                           "--inbox-url", "http://127.0.0.1:9", "--timeout-seconds", timeout,
                           *extra], max_rounds=0, out=io.StringIO(), err=err,
                          open_gate=lambda _e, **_k: FakeGate(["x"]))
        return code == runner.EXIT_UNSAFE_CONFIG, err.getvalue()

    assert investigation.MAX_COLLECT_READS == 6
    assert stepbudget.collect_step_worst_seconds(3.0, 6) == 58.0
    assert stepbudget.ai_step_worst_seconds() == 50.0
    assert refused("4", "--ai-judge")[0]  # 5 + 6 乘 9 + 5 = 64
    assert "租約" in refused("4", "--ai-judge")[1]
    assert not refused("3", "--ai-judge")[0]
    assert not refused("5")[0]  # 沒開 AI:5 秒照舊合法
    assert refused("15")[0]  # [S1001] 照舊
    monkeypatch.setattr(stepbudget, "MODEL_TIMEOUT_SECONDS", 26.0)  # 26 + 10 + 20 + 5 = 61
    assert refused("1", "--ai-judge")[0]
    monkeypatch.undo()
    monkeypatch.setattr(investigation, "MAX_COLLECT_READS", 7)  # 5 + 7 乘 8 + 5 = 66
    assert refused("3", "--ai-judge")[0]
    tree = ast.parse((SRC / "analyzer" / "runner.py").read_text(encoding="utf-8"))
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                and isinstance(n.value, int | float) and not isinstance(n.value, bool)}
    assert not literals & {15, 50, 58, 20, 6}  # 守衛不另寫一份數字


# ---- [S1116] ----
def test_a_stop_signal_during_a_model_call_ends_the_runner_cleanly(tmp_path):
    """[S1116] 模型呼叫途中收到停止訊號:runner 明接模型閘道轉手的例外,放掉租約、這一步不寫、以0 
    結束。"""
    world = AiWorld(tmp_path, [modelgate.CallTerminated("SIGTERM")])
    assert world.run_until_done() == 0
    row, _ = world.latest()
    assert row.state is TaskState.ANALYZING and world.rounds() == ()
    leases = world.leases()
    assert leases[-1][1] is None and leases[-2][1] is not None  # 最後是放掉(續租那一列之後)
    assert leases[-2][0] == leases[-3][0] + 1  # 放掉之前那一列是續租
    source = (SRC / "analyzer" / "runner.py").read_text(encoding="utf-8")
    assert "except modelgate.CallTerminated" in source


# ---- [S1137] ----
def test_the_runner_decides_the_model_mode_once_at_startup(tmp_path, monkeypatch):
    """[S1137] 模式只在啟動時判一次,之後每一輪沿用,不再執行 claude 版本檢查。"""
    calls = []
    real = modelgate.mc.settings_from_env

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(modelgate.mc, "settings_from_env", counting)
    world = AiWorld(tmp_path, [], tasks=(("t1", "c1"), ("t2", "c1")))
    world.gate = None
    opened = []

    def opener(environ, **kwargs):
        opened.append(kwargs)
        return modelgate.open_gate(environ, **kwargs)

    dsp = DspServer(world.root / "dsp.db", fault_injection=False, hang_seconds=0.2,
                    delay_seconds=0.0)
    inbox = InboxServer(world.root / "inbox.db", fault_injection=False)
    for server in (dsp, inbox):
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        code = runner.run([*world.argv(f"http://127.0.0.1:{dsp.server_address[1]}",
                                       f"http://127.0.0.1:{inbox.server_address[1]}"),
                           "--recordings-dir", str(tmp_path / "rec"),
                           "--ledger", str(tmp_path / "l.sqlite")],
                          max_rounds=30, out=io.StringIO(), err=io.StringIO(), environ={},
                          open_gate=opener)
    finally:
        for server in (dsp, inbox):
            server.shutdown()
            server.server_close()
    assert code == 0 and len(opened) == 1 and len(calls) == 1
    reader = TaskReader(world.root / "analyzer.db")
    try:
        fallbacks = [r.fallback for t in ("t1", "t2") for _s, r in reader.investigation_rounds(t)]
    finally:
        reader.close()
    assert fallbacks == ["no_recording", "no_recording"]  # 每一輪都照同一個錄製模式跑


# ---- [S1142] ----
def test_the_runner_refuses_live_recording_without_a_batch_id_or_into_a_mixed_directory(tmp_path):
    """[S1142] 即時加錄製模式沒帶 --batch-id、或開錄前目錄檢查判定目錄混了別批:拒絕啟動,不呼叫模
    型。"""
    backend = FakeBackend()
    settings = live(backend, record=True)

    def attempt(*extra, recordings=tmp_path / "rec"):
        def opener(_environ, **kwargs):
            return modelgate.Gate(settings, "demo-1", tmp_path / "l.sqlite", recordings,
                                  kwargs["batch_id"])

        err = io.StringIO()
        code = runner.run(["--db", str(tmp_path / "a.db"), "--dsp-url", "http://127.0.0.1:9",
                           "--inbox-url", "http://127.0.0.1:9", "--ai-judge", *extra],
                          max_rounds=0, out=io.StringIO(), err=err, open_gate=opener)
        return code, err.getvalue()

    code, text = attempt()
    assert code == runner.EXIT_UNSAFE_CONFIG and "--batch-id" in text
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "notes.txt").write_text("別的東西", encoding="utf-8")
    code, text = attempt("--batch-id", "b1", recordings=mixed)
    assert code == runner.EXIT_UNSAFE_CONFIG and "錄製目錄" in text
    code, _ = attempt("--batch-id", "b1", recordings=tmp_path / "clean")
    assert code == 0
    assert backend.calls == []


# ---- [S1156] ----
def test_held_campaigns_are_judged_but_never_submitted(tmp_path):
    """[S1156] --hold-submit 清單裡的廣告被判值得加:不論 AI 判的還是退回規則判的,都不送件、以考
    題結束結案,調查紀錄照記誰判的;清單外的廣告照舊送件。"""
    world = AiWorld(tmp_path / "ai", [_propose()], tasks=(("t1", "c1"), ("t2", "c2")),
                    extra=["--hold-submit", "c1"])
    assert world.run_until_done() == 0
    held, reason = world.latest("t1")
    assert (held.state, reason) == (TaskState.NO_ACTION, "exam_hold")
    assert [(r.decided_by, r.choice) for _s, r in world.rounds("t1")] == [("ai", "propose")]
    assert world.latest("t2")[0].state is TaskState.HANDED_OFF
    assert world.submitted() == ["t2"]
    fallback = AiWorld(tmp_path / "rule", [core.ModelTimeout("t")],
                       extra=["--hold-submit", "c1"])
    assert fallback.run_until_done() == 0
    assert fallback.latest()[1] == "exam_hold"
    assert [(r.decided_by, r.fallback) for _s, r in fallback.rounds()] == [("rule", "timeout")]
    assert fallback.submitted() == []
    err = io.StringIO()
    assert runner.run(["--db", str(tmp_path / "x.db"), "--dsp-url", "u", "--inbox-url", "u",
                       "--hold-submit", "c1"], max_rounds=0, err=err,
                      out=io.StringIO()) == runner.EXIT_UNSAFE_CONFIG  # 沒開 AI 不准只判不送


# ---- [S1160] ----
def test_the_runner_checks_the_login_once_before_taking_any_lease(tmp_path, monkeypatch):
    """[S1160] 即時模式:印出就緒之後、取任何租約之前經閘道呼叫一次登入預檢;沒過整趟用程式規則、
    記退回原因「登入預檢沒過」、不呼叫模型。錄製模式回不適用、不呼叫任何東西。"""
    events = []
    gate = FakeGate([_propose()], events=events, preflight=modelgate.Preflight.FAILED,
                    settings=live(FakeBackend()))
    real_acquire = TaskStore.acquire_lease

    def acquire(self, *args):
        events.append("lease")
        return real_acquire(self, *args)

    class Out(io.StringIO):
        def write(self, text):
            if text.strip() == runner.READY:
                events.append("ready")
            return super().write(text)

    monkeypatch.setattr(TaskStore, "acquire_lease", acquire)
    world2 = AiWorld(tmp_path / "live2", [], tasks=(("t1", "c1"), ("t2", "c1")), gate=gate)
    dsp = DspServer(world2.root / "dsp.db", fault_injection=False, hang_seconds=0.2,
                    delay_seconds=0.0)
    inbox = InboxServer(world2.root / "inbox.db", fault_injection=False)
    for server in (dsp, inbox):
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        runner.run(world2.argv(f"http://127.0.0.1:{dsp.server_address[1]}",
                               f"http://127.0.0.1:{inbox.server_address[1]}"),
                   max_rounds=30, out=Out(), err=io.StringIO(), open_gate=lambda _e, **_k: gate)
    finally:
        for server in (dsp, inbox):
            server.shutdown()
            server.server_close()
    assert events[:3] == ["ready", "preflight", "lease"]
    assert events.count("preflight") == 1 and "model" not in events
    fallbacks = [r.fallback for t in ("t1", "t2") for _s, r in world2.rounds(t)]
    assert fallbacks == ["preflight_failed", "preflight_failed"]
    assert world2.submitted() == ["t1", "t2"]  # 整趟照程式規則:有價值格照舊提案送件
    # 錄製模式:預檢回不適用,不呼叫任何東西
    touched = []
    monkeypatch.setattr(modelgate.mc.cc.ClaudeCodeBackend, "preflight",
                        lambda _self: touched.append("backend"))
    result = modelgate.Gate(recorded(), None, tmp_path / "l", tmp_path / "r").preflight_login()
    assert result.outcome is modelgate.Preflight.NOT_APPLICABLE and touched == []


# ---- [S1114] ----
def test_the_executor_never_sees_model_rounds(tmp_path):
    """[S1114] 執行端與收件口不讀調查紀錄表;AI 選的提案送進收件口的內容跟規則的提案同一組欄位,不
    帶任何模型產生的欄位。"""
    offenders = []
    for path in sorted((SRC / "executor").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {word}" for word in (
            "investigation_rounds", "investigation_raw", "InvestigationRecord", "ai_judge",
            "rtb.analyzer.investigation") if word in text]
    assert offenders == []
    world = AiWorld(tmp_path, [_propose()])
    world.run_until_done()
    with sqlite3.connect(world.root / "inbox.db") as conn:
        [raw] = [r[0] for r in conn.execute("SELECT payload FROM proposals")]
    fields = set(json.loads(raw))
    assert fields == {"task_id", "revision", "campaign_id", "action_type", "requested_change",
                      "reason_codes", "evidence_refs", "campaign_version_observed",
                      "decision_created_at", "decision_expires_at", "policy_version",
                      "risk_summary"}
    assert "理由" not in raw and "有轉換" not in raw
    assert json.loads(raw)["reason_codes"] == ["low_pacing"]


def recorded_runner_books_into_the_given_ledger(tmp_path):
    """[S1102] 的分析端驅動命令列那半:錄製模式收到帳檔參數,帳記在指定路徑。"""
    world = AiWorld(tmp_path / "ledger-case", [])
    world.gate = None
    ledger = tmp_path / "runner-ledger.sqlite"
    dsp = DspServer(world.root / "dsp.db", fault_injection=False, hang_seconds=0.2,
                    delay_seconds=0.0)
    inbox = InboxServer(world.root / "inbox.db", fault_injection=False)
    for server in (dsp, inbox):
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        code = runner.run([*world.argv(f"http://127.0.0.1:{dsp.server_address[1]}",
                                       f"http://127.0.0.1:{inbox.server_address[1]}"),
                           "--demo-id", "demo-7", "--ledger", str(ledger),
                           "--recordings-dir", str(tmp_path / "none")],
                          max_rounds=20, out=io.StringIO(), err=io.StringIO(), environ={})
    finally:
        for server in (dsp, inbox):
            server.shutdown()
            server.server_close()
    assert code == 0
    return ledger

