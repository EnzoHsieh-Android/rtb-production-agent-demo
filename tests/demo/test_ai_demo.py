# ruff: noqa: RUF003
"""Phase 13 增量 4 展示串接(計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈展示情境怎麼接 AI〉
〈通用規則:驅動依每件工作的實際結局選預期〉〈F5 對抗案例的預期〉〈錄製批次與入庫〉):真的起行程
跑情境,分析端讀測試自己寫的假錄製(tests/demo/fake_recordings.py),不碰真模型。"""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from rtb.analyzer import runner
from rtb.analyzer.task_store import TaskReader
from rtb.demo import driver as driver_module
from rtb.demo import launcher
from rtb.demo import recordings as demo_recordings
from rtb.demo.driver import (
    AI_NODES,
    DONE,
    NOT_EXERCISED,
    NOT_EXERCISED_TEXT,
    Driver,
    Outcome,
    expectations,
    outcome_of,
)
from rtb.demo.keys import DemoKeys
from rtb.demo.launcher import Role
from rtb.demo.observe import PathBuilder, missing_from_path
from rtb.demo.state_store import StateReader, StateWriter
from rtb.domain.task_state import TaskState
from rtb.modelledger_view import ledger_path
from tests.demo import fake_recordings as fake
from tests.model.fakes import fake_claude, invocations, write_verification

SRC = Path(__file__).resolve().parents[2] / "src"
PROJECT = SRC.parent
BATCH = "phase13-demo-20260925"


@pytest.fixture
def state(tmp_path):
    writer = StateWriter(tmp_path / "state.db", "demo-1")
    yield writer
    writer.close()


def _driver(tmp_path, state, recordings, **kwargs):
    kwargs.setdefault("user_env", os.environ)
    return Driver(tmp_path / "demos", "demo-1", DemoKeys.generate(), state,
                  recordings_dir=recordings, **kwargs)


def _batch(tmp_path, name="rec", **plan):
    directory = tmp_path / name
    fake.fake_batch(directory, BATCH, plan)
    return directory


def _rounds(tmp_path, code, task):
    reader = TaskReader(tmp_path / "demos" / "demo-1" / code / "analyzer.db")
    try:
        return [r for _seq, r in reader.investigation_rounds(task)]
    finally:
        reader.close()


def _read_details(tmp_path, code):
    reader = StateReader(tmp_path / "state.db")
    try:
        return reader.scenario_details("demo-1", code), reader.decisions("demo-1", code)
    finally:
        reader.close()


def _fake_claude(tmp_path):
    """假的 claude:被叫到就記一行,什麼都不做。PATH 最前面放它,真的 claude 永遠輪不到。"""
    folder = tmp_path / "bin"
    folder.mkdir()
    log = tmp_path / "claude-calls.log"
    script = folder / "claude"
    script.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 1\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return folder, log


def _capture_starts(monkeypatch):
    """記下驅動程式每次經啟動器起的行程:(角色, 參數, 這個情境是不是即時)。"""
    seen = []
    real = launcher.start

    def spy(role, args, keys, **kwargs):
        seen.append((role, list(args), kwargs.get("live_model", False)))
        return real(role, args, keys, **kwargs)

    monkeypatch.setattr(driver_module.launcher, "start", spy)
    return seen


def _arg(args, name):
    return args[args.index(name) + 1] if name in args else None


def _tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


# ---- [S1120] 沒列進即時清單:只讀錄製 ----
def _live_ready(tmp_path):
    """一支真的會被叫到的假 claude,加一份測試用的即時模式啟用紀錄(寫在這支測試的帳號家目錄;
    啟動器起的子行程在測試裡也讀這個家目錄):模型變數一漏給子行程,它就會真的判成即時、叫到假 claude
    (代碼審 r1 l3:原本沒有啟用紀錄,漏了也會先退回錄製,測試抓不到)。"""
    script = fake_claude(tmp_path / "bin")
    write_verification()
    env = {**os.environ, "PATH": f"{script.parent}:{os.environ['PATH']}", "RTB_MODEL_LIVE": "1"}
    return script, env


def test_the_demo_uses_recordings_unless_live_is_switched_on(tmp_path, state, monkeypatch):
    """即時開關打開、PATH 最前面有一支真的叫得到的假 claude、也有啟用紀錄:沒列在即時清單的情境,
    分析端與說明、假說命令列實際拿到的環境沒有模型變數——分析端回報錄製、沒有任何原因,
    一次 claude 都沒叫;花費帳記在這個情境的暫存目錄。"""
    recordings = _batch(tmp_path, normal=[fake.PROPOSE])
    script, env = _live_ready(tmp_path)
    starts = _capture_starts(monkeypatch)
    verdict = _driver(tmp_path, state, recordings, user_env=env).run_one("F1")

    assert verdict.status == DONE, verdict.reason
    assert invocations(script) == []  # 一次 claude 都沒叫
    [(_role, args, live)] = [s for s in starts if s[0] is Role.ANALYZER]
    assert "--ai-judge" in args and live is False
    assert _arg(args, "--recordings-dir") == str(recordings)
    ledger = Path(_arg(args, "--recorded-ledger"))
    assert ledger.parent == tmp_path / "demos" / "demo-1" / "F1" and ledger.is_file()
    assert not ledger_path().exists()  # 帳號家目錄下的帳沒有被建立
    rounds = _rounds(tmp_path, "F1", "t1")
    assert [(r.kind, r.choice, r.decided_by, r.model_source) for r in rounds] == [
        ("conclusion", "propose", "ai", "recorded")]
    details, _rows = _read_details(tmp_path, "F1")
    assert (details.model_mode, details.model_mode_reason) == (
        "recorded", "錄製回應,不是即時呼叫(這個情境不在即時清單)")
    narrated = json.loads(details.narrative_json)  # 說明命令列也沒拿到即時開關:錄製、沒有任何原因
    assert (narrated["mode"], narrated["notices"]) == ("recorded", [])


# ---- [S1145] 只有列在即時清單、而且帶 --ai-judge 的分析端拿到模型變數;F7 不准 ----
def test_only_listed_scenarios_get_live_model_env_and_f7_never_does(tmp_path, state, monkeypatch):
    with pytest.raises(ValueError, match="F7"):
        _driver(tmp_path, state, tmp_path / "rec", live={"F7"})
    with pytest.raises(ValueError, match="F9"):
        _driver(tmp_path, state, tmp_path / "rec", live={"F9"})
    assert _driver(tmp_path / "default", state, tmp_path / "rec").live == frozenset()

    script, env = _live_ready(tmp_path)
    starts = _capture_starts(monkeypatch)
    demo = _driver(tmp_path, state, tmp_path / "rec", user_env=env, live={"F1"})
    assert demo.run_one("F2").status == DONE  # 沒列在清單:子行程實際拿不到變數,判成錄製
    assert invocations(script) == []
    assert demo.run_one("F1").status == DONE  # 列在清單:真的判成即時,叫到假 claude
    assert invocations(script)
    analyzers = [live for role, _args, live in starts if role is Role.ANALYZER]
    assert analyzers[-1] is True and not any(analyzers[:-1])  # F2 重啟時起了兩次分析端
    reader = StateReader(tmp_path / "state.db")
    try:  # 模式照分析端就緒之後回報的那一行
        listed = reader.scenario_details("demo-1", "F1")
        unlisted = reader.scenario_details("demo-1", "F2")
    finally:
        reader.close()
    assert (listed.model_mode, listed.model_mode_reason) == (
        "live", "即時呼叫(這個情境列在即時清單)")
    assert (unlisted.model_mode, unlisted.model_mode_reason) == (
        "recorded", "錄製回應,不是即時呼叫(這個情境不在即時清單)")


def test_a_listed_scenario_that_falls_back_books_into_its_temporary_ledger(tmp_path, state,
                                                                            monkeypatch):
    """(代碼審 r1 h1)列在即時清單、即時開關有開,但 PATH 上沒有 claude:閘道判成錄製,帳落在情境的
    暫存目錄,不落帳號家目錄;驅動不猜模式,錄製用的帳檔一律給,原因照分析端回報的寫。"""
    env = {**os.environ, "RTB_MODEL_LIVE": "1"}
    starts = _capture_starts(monkeypatch)
    demo = _driver(tmp_path, state, tmp_path / "rec", user_env=env, live={"F1"})
    assert demo.run_one("F1").status == DONE
    [args] = [a for role, a, _l in starts if role is Role.ANALYZER]
    ledger = tmp_path / "demos" / "demo-1" / "F1" / "model-ledger.db"
    assert _arg(args, "--recorded-ledger") == str(ledger) and "--ledger" not in args
    assert ledger.is_file() and not ledger_path().exists()
    details, _rows = _read_details(tmp_path, "F1")
    assert details.model_mode == "recorded"
    assert details.model_mode_reason.startswith("列在即時清單,但分析端判成錄製回應:")


# ---- [S1166] 即時清單裡的情境用這次展示專屬的新錄製目錄 ----
def test_live_scenarios_record_into_a_fresh_per_demo_directory(tmp_path, state, monkeypatch):
    committed = PROJECT / "recordings"
    before = _tree_digest(committed)
    starts = _capture_starts(monkeypatch)
    demo = _driver(tmp_path, state, tmp_path / "rec", live={"F1"})
    assert demo.run_one("F1").status == DONE
    assert demo.run_one("F2").status == DONE
    args = [a for role, a, _live in starts if role is Role.ANALYZER]
    live_dir = Path(_arg(args[0], "--recordings-dir"))
    assert live_dir == tmp_path / "demos" / "demo-1" / "live-recordings" / "F1"
    assert committed not in live_dir.parents and not live_dir.exists()  # 新的、還沒有任何檔
    assert _arg(args[0], "--batch-id") == "demo-live-demo-1"
    assert _arg(args[-1], "--recordings-dir") == str(tmp_path / "rec")
    assert _arg(args[-1], "--batch-id") is None
    assert committed / "model" / "phase13-demo" == driver_module.DEMO_RECORDINGS
    assert _tree_digest(committed) == before  # 入庫的錄製目錄內容不變


# ---- [S1143] F3:賽跑只推蒐證;驅動與展示伺服器不載入 AI 決策、模型閘道、模型用戶端 ----
def test_f3_races_only_the_evidence_step_and_the_driver_never_loads_the_model(
        tmp_path, state, monkeypatch):
    for module in ("rtb.demo.driver", "rtb.demo.server"):
        loaded = subprocess.run(
            [sys.executable, "-c", f"import sys, {module}\nprint('\\n'.join(sorted(sys.modules)))"],
            env={**os.environ, "PYTHONPATH": str(SRC)}, capture_output=True, text=True,
            check=True, timeout=60).stdout.split()
        for banned in ("rtb.analyzer.ai_judge", "rtb.analyzer.modelgate", "rtb.modelclient",
                       "rtb.modelclaude", "rtb.modelcore", "rtb.modelrecording",
                       "rtb.analyzer.runner", "rtb.analyzer.narrate", "rtb.ops.hypothesis"):
            assert banned not in loaded, (module, banned)
    decided = []
    real = driver_module._no_decision

    def spy(*args):
        decided.append(args)
        return real(*args)

    monkeypatch.setattr(driver_module, "_no_decision", spy)
    recordings = _batch(tmp_path, normal=[fake.PROPOSE])
    verdict = _driver(tmp_path, state, recordings).run_one("F3")
    assert verdict.status == DONE, verdict.reason
    assert decided == []  # 兩條賽跑執行緒都沒有呼叫任何決策函式
    [record] = _rounds(tmp_path, "F3", "t1")  # 分析由之後起的分析端主執行緒照一般情境做
    assert (record.kind, record.decided_by) == ("conclusion", "ai")


# ---- [S1157] [S1167] 依結局選預期、AI 節點兩組都允許 ----
def test_ai_nodes_are_allowed_on_the_analysis_path(tmp_path, state):
    proposal = driver_module.F1_STREAMS
    for path in (["a_receive", "a_collect", "a_fresh", "a_ai", "a_ai_query", "a_fresh", "a_ai",
                  "a_propose", "x_pending", "x_done"],
                 ["a_receive", "a_collect", "a_fresh", "a_rule", "a_propose", "x_pending",
                  "x_done"]):
        required, allowed = expectations("t1", Outcome.PROPOSE, proposal)[("t1", "task")]
        assert missing_from_path(path, required, allowed) is None
    for end, outcome in (("a_no_action", Outcome.NO_PROPOSE), ("a_exam_hold", Outcome.EXAM_HOLD)):
        required, allowed = expectations("t1", outcome, proposal)[("t1", "task")]
        assert set(AI_NODES) <= set(allowed)
        assert missing_from_path(["a_receive", "a_collect", "a_fresh", "a_ai", "a_ai_query",
                                  "a_fresh", "a_ai", end], required, allowed) is None
    # 真的跑:第 1 輪選查詢、第 2 輪判值得加,路徑多出 AI 節點照樣照預期跑完
    recordings = _batch(tmp_path, normal=[fake.LONGER_WINDOW, fake.PROPOSE])
    verdict = _driver(tmp_path, state, recordings).run_one("F1")
    assert verdict.status == DONE, verdict.reason
    _, rows = _read_details(tmp_path, "F1")
    nodes = [r.node for r in rows if r.task == "t1"]
    assert nodes.count("a_ai") == 2 and "a_ai_query" in nodes and nodes.count("a_fresh") == 2


def test_the_driver_picks_expectations_by_each_task_outcome(tmp_path, state):
    rows = [_row("t1", 1, TaskState.RECEIVED), _row("t1", 2, TaskState.NO_ACTION)]
    assert outcome_of(rows, "judged_insufficient") is Outcome.NO_PROPOSE
    assert outcome_of(rows, "exam_hold") is Outcome.EXAM_HOLD
    assert outcome_of([*rows[:1], _row("t1", 2, TaskState.PROPOSED)], None) is Outcome.PROPOSE
    assert outcome_of(rows[:1], None) is None
    no = expectations("t1", Outcome.NO_PROPOSE, driver_module.F1_STREAMS)
    assert set(no) == {("t1", "task")}  # 只核分析端那一條
    assert no[("t1", "task")][0][-1] == "a_no_action"
    # 不提案的工作卻有收件口紀錄:核對算對不上
    world = driver_module.World.__new__(driver_module.World)
    world._path = PathBuilder()
    world._path.streams = [("t1", "task", n) for n in ("a_receive", "a_collect", "a_fresh",
                                                       "a_no_action")]
    world._path.streams.append(("t1", "proposal", "x_pending"))
    world.record = lambda **_kwargs: None
    with pytest.raises(driver_module.ScenarioFailed, match="收件口"):
        world.require_streams(no)
    world._path.streams.pop()
    world.require_streams(no)
    # 真的跑:AI 判證據不足 → 等到不提案結案就放行,故障斷言(寫入恰好一次)不做
    recordings = _batch(tmp_path, normal=[fake.INSUFFICIENT])
    verdict = _driver(tmp_path, state, recordings).run_one("F1")
    assert (verdict.status, verdict.summary) == (NOT_EXERCISED, NOT_EXERCISED_TEXT)


def _row(task, seq, state):
    from datetime import UTC, datetime

    from rtb.analyzer.task_store import TaskRow
    return TaskRow(task, seq, state, "c1", None, None, datetime.now(UTC))


# ---- [S1144] AI 合法判不提案:故障處理這次沒有走到 ----
def test_a_legit_ai_no_propose_marks_the_fault_as_not_exercised(tmp_path, state):
    recordings = _batch(tmp_path, normal=[fake.INSUFFICIENT])
    verdict = _driver(tmp_path, state, recordings).run_one("F2")
    assert verdict.status == NOT_EXERCISED  # 不是沒跑完,也不是照預期演示了故障
    # 合約的「AI 判不提案,故障處理這次沒有走到」,「提案」照白話規則緊接括號解釋(代碼審 r1 p4)
    assert verdict.summary == NOT_EXERCISED_TEXT and verdict.reason is None
    assert NOT_EXERCISED_TEXT.replace("\uff08不提出調整建議\uff09", "") == \
        "AI 判不提案,故障處理這次沒有走到"
    details, _rows = _read_details(tmp_path, "F2")
    assert details.outcome_note.startswith("AI 的判斷:stop_insufficient")  # AI 的判斷另列一行
    assert details.decided_by == "ai"
    reader = StateReader(tmp_path / "state.db")
    try:
        [run] = [r for r in reader.scenario_runs("demo-1") if r.code == "F2"]
    finally:
        reader.close()
    assert run.status == NOT_EXERCISED


# ---- [S1168] F4、F6:原任務照舊擋下,接續任務 AI 判證據不足 ----
@pytest.mark.parametrize("code", ["F4", "F6"])
def test_f4_and_f6_accept_an_insufficient_follow_up(tmp_path, state, code):
    recordings = _batch(tmp_path, normal=[fake.PROPOSE], follow_up=[fake.INSUFFICIENT])
    demo = _driver(tmp_path, state, recordings)
    verdict = demo.run_one(code)
    assert verdict.status == DONE, verdict.reason
    assert "故障照預期,接續任務 AI 判證據不足" in verdict.summary
    details, _rows = _read_details(tmp_path, code)
    assert details.outcome_note.startswith("故障照預期,接續任務 AI 判證據不足")
    assert details.platform_operations and all("→ 200" in op for op in details.platform_operations)


# ---- [S1124] F5:程式層不變量與模型考題 ----
def test_f5_checks_program_invariants_and_reports_the_model_exam(tmp_path, state, monkeypatch):  # noqa: PLR0915 - 兩次真跑
    starts = _capture_starts(monkeypatch)
    passing = _batch(tmp_path, "pass", normal=[fake.PROPOSE], attacked=[fake.PROPOSE])
    verdict = _driver(tmp_path / "a", state, passing).run_one("F5")
    assert verdict.status == DONE, verdict.reason
    [args] = [a for role, a, _l in starts if role is Role.ANALYZER]
    assert _arg(args, "--hold-submit") == driver_module.F5_TWIN.campaign_id
    base = tmp_path / "a" / "demos" / "demo-1" / "F5"
    reader = TaskReader(base / "analyzer.db")
    try:
        twin = reader.history("t3")
        assert twin[-1].state is TaskState.NO_ACTION
        # 雙胞胎是真任務、只判不送
        assert reader.no_action_reason("t3", twin[-1].seq) == "exam_hold"
        assert [r.choice for _s, r in reader.investigation_rounds("t3")] == ["propose"]
    finally:
        reader.close()
    first = _exam_line(tmp_path)
    assert first.startswith("錄製當時的 AI 回答:考題通過")

    failing = _batch(tmp_path, "fail", normal=[fake.PROPOSE], attacked=[fake.INSUFFICIENT])
    (tmp_path / "b").mkdir()
    state_b = StateWriter(tmp_path / "b" / "state.db", "demo-1")
    try:
        verdict = _driver(tmp_path / "b", state_b, failing).run_one("F5")
    finally:
        state_b.close()
    assert verdict.status == DONE, verdict.reason  # 情境照預期跑完,考題另列
    assert "AI 判不提案\uff08不提出調整建議\uff09,平台上沒有任何寫入" in verdict.summary
    for term in ("模型", "提案"):  # 結果摘要照白話規則(代碼審 r1 p4)
        assert term not in re.sub(rf"{term}\uff08[^\uff08\uff09]+\uff09", "", verdict.summary)
    assert "考題沒通過" in _exam_line(tmp_path / "b")
    change = _f5_details(tmp_path / "b").change  # 沒改預算的原因照實寫 AI 判不提案
    assert change.written is False and change.reason == driver_module.NOT_PROPOSED_TEXT


def _f5_details(root):
    reader = StateReader(root / "state.db")
    try:
        return reader.scenario_details("demo-1", "F5")
    finally:
        reader.close()


def _exam_line(root):
    return _f5_details(root).exam


def test_f5_twin_falling_back_makes_the_exam_uncomparable(tmp_path, state):
    verdict = _driver(tmp_path, state, tmp_path / "empty").run_one("F5")  # 沒有錄製:兩件都退回
    assert verdict.status == DONE, verdict.reason
    assert "退回,無法比較" in _exam_line(tmp_path)


# ---- [S1158] F7 與 F5 雙胞胎每一輪只有一個錄製鍵,等於 F1 同一輪 ----
def test_f7_shares_one_recording_key_with_f1():
    answers = [fake.LONGER_WINDOW, fake.PROPOSE]
    f1, _n = fake.run_family("normal", answers)
    many, _n = fake.run_family("normal", answers, campaigns=4)
    assert len(f1) == 2  # 兩輪
    for index, prompt in enumerate(f1):
        this_round = {p for p in many if p.count("ref=") == prompt.count("ref=")}
        assert this_round == {prompt}, index  # F7 每一件這一輪送的內容逐位元組相同
    assert len(many) == 4 * len(f1)
    assert (driver_module.F5_TWIN.budget, driver_module.F5_TWIN.spend,
            driver_module.F5_TWIN.name) == (driver_module.F1_CAMPAIGN.budget,
                                            driver_module.F1_CAMPAIGN.spend,
                                            driver_module.F1_CAMPAIGN.name)
    seeded = driver_module.f7_campaigns(3)
    assert all((c.budget, c.spend, c.name) == (driver_module.F1_CAMPAIGN.budget,
                                                driver_module.F1_CAMPAIGN.spend,
                                                driver_module.F1_CAMPAIGN.name) for c in seeded)


# ---- [S1164] 展示批次入庫前:F1–F6 找不到錄製 0 筆,批次本身也過那兩條 ----
def test_a_demo_batch_replays_f1_to_f6_without_a_missing_recording(tmp_path, monkeypatch):
    complete = tmp_path / "complete"  # 假錄製冒充正式後端錄的一批(只在這支測試)
    fake.fake_batch(complete, BATCH, {"normal": [fake.PROPOSE], "attacked": [fake.PROPOSE],
                                      "follow_up": [fake.PROPOSE]}, narrative=fake.NARRATIVE,
                    backend="claude_code")
    result = demo_recordings.check_demo_batch(complete, BATCH, tmp_path / "work")
    assert result.missing == 0 and result.problems == (), result
    assert result.passed and [v[1] for v in result.verdicts] == [DONE] * 6, result.verdicts

    empty = demo_recordings.check_demo_batch(tmp_path / "none", BATCH, tmp_path / "work2",
                                             codes=("F1",))
    assert empty.missing >= 1 and not empty.passed  # 空目錄會過前兩條,過不了這一條
    # 代碼審 r1 l2:分析端起不來,這個情境一次都沒問 AI、帳根本沒建:不准算過
    real = launcher.start

    def broken(role, args, keys, **kwargs):
        if role is Role.ANALYZER:
            raise launcher.StartFailed("simulated slow start")
        return real(role, args, keys, **kwargs)

    monkeypatch.setattr(driver_module.launcher, "start", broken)
    down = demo_recordings.check_demo_batch(complete, BATCH, tmp_path / "work3", codes=("F1",))
    assert down.missing == 0 and not down.passed
    assert any("F1 沒有跑完" in p for p in down.problems)
    assert any("F1 的花費帳不在" in p for p in down.problems)


def test_the_replay_needs_every_scenario_finished_with_a_ledger_that_has_calls(tmp_path):
    """(代碼審 r1 k1/l2/s3)重播本身的問題:情境沒跑完、帳不在、帳裡沒有呼叫紀錄,都不准算過。"""
    missing, problems = demo_recordings.replay_problems(
        (("F1", "incomplete", "startup failed"), ("F2", NOT_EXERCISED, None)),
        {"F1": tmp_path / "none.db"})
    assert missing == 0
    assert problems == ("F1 沒有跑完:incomplete(startup failed)",
                        "F1 的花費帳不在或讀不了:這個情境一次都沒有問 AI")
    assert not demo_recordings.BatchCheck(0, problems).passed


def test_the_batch_itself_must_be_one_clean_batch(tmp_path):
    directory = tmp_path / "rec"
    fake.fake_batch(directory, BATCH, {"normal": [fake.PROPOSE]}, backend="claude_code")
    assert demo_recordings.batch_problems(directory, BATCH) == ()
    assert demo_recordings.batch_problems(directory, "phase13-demo-20260101")  # 別的批次
    assert any("批次編號要是 phase13-demo-YYYYMMDD" in p
               for p in demo_recordings.batch_problems(directory, "demo-x"))
    for outcome in ("config_error", "ledger_busy"):
        bad = fake.recording(fake.core.Caller.INVESTIGATION, "s", outcome, 5, "", BATCH,
                             outcome=fake.core.Outcome(outcome), backend="claude_code")
        path = fake.write(directory, bad)
        assert any(demo_recordings.batch_problems(directory, BATCH))
        path.unlink()
    unsure = fake.recording(fake.core.Caller.INVESTIGATION, "s", "u", 5, "ok", BATCH,
                            backend="claude_code")
    path = fake.write(directory, unsure)
    body = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**body, "unclassified": True}), encoding="utf-8")
    assert any("無法可靠分類" in p for p in demo_recordings.batch_problems(directory, BATCH))
    path.write_text(json.dumps({**body, "backend": "fake"}), encoding="utf-8")  # 代碼審 r1 s2
    assert any("不是正式後端錄" in p for p in demo_recordings.batch_problems(directory, BATCH))
    path.unlink()
    placeholder = directory / f"{'0' * 64}.json"
    placeholder.write_text(json.dumps({"claimed_by_batch": BATCH}), encoding="utf-8")
    assert demo_recordings.batch_problems(directory, BATCH)  # 殘留的佔位檔


def test_the_fake_recordings_never_go_into_the_committed_directory():
    """(代碼審 r1 t3)假錄製產生器拒絕寫進入庫錄製目錄;預設寫的後端是 fake,批次檢查一律拒收。"""
    found = fake.recording(fake.core.Caller.INVESTIGATION, "s", "u", 5, "ok", BATCH)
    assert found.backend == fake.FAKE_BACKEND
    for target in (PROJECT / "recordings" / "model", PROJECT / "recordings" / "model" / "x"):
        with pytest.raises(ValueError, match="入庫"):
            fake.write(target, found)
    assert not (PROJECT / "recordings" / "model" / "x").exists()


def test_the_batch_check_reads_recordings_only_through_the_model_client_facade():
    """(代碼審 r1 h2)批次檢查那一支關了匯入禁令(ruff 只能整檔關),這裡逐條核對:模型用戶端只經門面,
    不碰錄製模組、閘道、決策模組與命令列。"""
    import ast

    tree = ast.parse((SRC / "rtb" / "demo" / "recordings.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {f"{node.module}.{a.name}" for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) for a in node.names}
    model = {name for name in imported if ".model" in name or name.startswith("rtb.model")}
    assert model <= {"rtb.modelclient", "rtb.modelledger_view",
                     "rtb.modelledger_view.ModelLedgerView", "rtb.modelledger_view.Outcome"}
    assert "rtb.modelclient" in {f"{n.module}.{a.name}" for n in ast.walk(tree)
                                 if isinstance(n, ast.ImportFrom) for a in n.names} | {
        f"rtb.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        and n.module == "rtb" for a in n.names}


# ---- 代碼審 r1:誰決定、時限、停止、故障沒走到的邊界 ----
def _record(kind, decided_by, choice="propose", fallback=None):
    from rtb.analyzer.task_store import InvestigationRecord

    return InvestigationRecord(kind, 1, choice, decided_by, fallback=fallback)


def test_who_decided_follows_each_task_final_conclusion():
    """(代碼審 r1 k3)AI 選過查詢、下一輪退回由程式規則決定 → 標 AI 退回程式規則,不標 AI。"""
    assert driver_module.decided_by([_record("fallback", "rule", fallback="no_recording")]) \
        == "ai_fallback"
    assert driver_module.decided_by([_record("fallback", "rule")]) == "ai_fallback"
    assert driver_module.decided_by([_record("conclusion", "ai")]) == "ai"
    assert driver_module.decided_by([]) == "rule"
    # 每件工作最後一列:一件是 AI 的結論、另一件選過查詢之後退回 → AI 退回程式規則
    assert driver_module.decided_by([_record("conclusion", "ai"),
                                     _record("fallback", "rule")]) == "ai_fallback"


def test_the_ai_allowance_is_capped_in_recorded_mode(tmp_path):
    """(代碼審 r1 d1)錄製模式整段放寬不超過 60 秒(F7 三百件照件數放寬會多二十分鐘);即時照每輪
    最壞值。"""
    from rtb.stepbudget import ai_step_worst_seconds

    world = driver_module.World.__new__(driver_module.World)
    world.ai = driver_module.AiSetup("demo-1", live=False, recordings=tmp_path)
    assert world.round_seconds() == driver_module.RECORDED_ROUND_SECONDS
    assert world.allow(240, tasks=300) == 240 + driver_module.RECORDED_EXTRA_CAP_SECONDS
    assert world.allow(60, tasks=1) == 60 + 3 * driver_module.RECORDED_ROUND_SECONDS
    world.ai = driver_module.AiSetup("demo-1", live=True, recordings=tmp_path)
    assert world.allow(60, tasks=2) == 60 + 2 * 3 * ai_step_worst_seconds()
    world.ai = None
    assert world.allow(60, tasks=300) == 60


def test_a_rule_no_propose_after_a_fallback_is_a_failure_not_an_untouched_fault():
    """(代碼審 r1 d2)受測工作退回程式規則、規則判不提案:對不上(沒跑完),不是「故障沒走到」。"""
    world = driver_module.World.__new__(driver_module.World)
    world.rounds = lambda _task: (_record("fallback", "rule", "no_action", "no_recording"),)
    with pytest.raises(driver_module.ScenarioFailed, match="不是 AI 判的"):
        world.not_exercised("t1", "c1")
    world.rounds = lambda _task: (_record("query", "ai", "check_longer_window"),
                                  _record("conclusion", "rule", "do_not_propose"))
    with pytest.raises(driver_module.ScenarioFailed):
        world.not_exercised("t1", "c1")


def test_cancelling_during_the_model_entries_ends_the_scenario_quickly(tmp_path, state,
                                                                       monkeypatch):
    """(代碼審 r1 l1)說明命令列卡住時整次展示被取消:命令列整組收掉,情境在 60 秒內結束,不接著跑
    假說。"""
    import threading
    import time

    started = threading.Event()
    real = launcher.entry_command

    def slow(entry, args):
        started.set()
        return [sys.executable, "-c", "import time\ntime.sleep(100)"] if entry else real(entry,
                                                                                        args)

    monkeypatch.setattr(launcher, "entry_command", slow)
    demo = _driver(tmp_path, state, tmp_path / "rec")
    worker = threading.Thread(target=demo.run_one, args=("F1",), daemon=True)
    worker.start()
    assert started.wait(120)
    begin = time.monotonic()
    demo.cancel()
    worker.join(60)
    assert not worker.is_alive() and time.monotonic() - begin < 60
    details, _rows = _read_details(tmp_path, "F1")
    assert details.hypothesis_json is None  # 取消之後不接著跑假說


def test_f7_checks_every_task_path_by_its_outcome(tmp_path, state, monkeypatch):
    """(代碼審 r1 k2)F7 也照通用規則逐件核路徑:每一件的分析端紀錄都在預期組裡,確認放行的那一件多走
    「人已同意,放回排隊」。"""
    from tests.demo import test_driver as td

    checked = []
    real = driver_module.World.require_streams

    def spy(self, expected):
        checked.append(dict(expected))
        return real(self, expected)

    monkeypatch.setattr(driver_module.World, "require_streams", spy)
    verdict, _demo = td._small_f7(tmp_path, state, cap=60, approve=td._approve_when_asked)
    assert verdict.status == DONE, verdict.reason
    [expected] = checked
    assert {task for task, kind in expected if kind == "task"} == {
        f"t{i:04d}" for i in range(30)}
    approved = [k for k, (required, allowed) in expected.items()
                if k[1] == "proposal" and "x_approved" in allowed]
    assert len(approved) == 1


def test_the_driver_reads_the_runner_model_line_with_the_same_prefix():
    assert driver_module.MODEL_LINE == runner.MODEL_LINE
    assert driver_module.PREFLIGHT_FAILED == runner.PREFLIGHT_FAILED_MODE
    from rtb.analyzer import ai_judge
    from rtb.demo import basis
    assert basis.HOURS_PER_BUDGET == ai_judge.HOURS_PER_BUDGET


def test_the_server_refuses_f7_or_unknown_codes_in_the_live_list(tmp_path, capsys):
    """[S1145] 伺服器的 --live 清單:F7 或不認得的情境在啟動時就拒;沒給就是空的(全部錄製)。"""
    from rtb.demo import server

    work = str(tmp_path)
    for bad, named in (("F7", "F7"), ("F1,F9", "F9")):
        with pytest.raises(SystemExit):
            server._arguments(["--work-dir", work, "--live", bad])
        assert named in capsys.readouterr().err
    assert server._arguments(["--work-dir", work])[1] == []
    assert server._arguments(["--work-dir", work, "--live", "F1,F5"])[1] == ["F1", "F5"]
