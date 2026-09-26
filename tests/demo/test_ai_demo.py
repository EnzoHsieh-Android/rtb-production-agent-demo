"""展示串接 AI 的地方(Phase 13 增量 4 起;Phase 14 增量 3 改寫,計劃
[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈使用者裁定〉8、〈拆增量〉3):分析端只用九條規則,
AI 只剩提案說明與告警原因假說兩支模型入口。真的起行程跑情境,說明入口讀測試自己寫的假錄製
(tests/demo/fake_recordings.py),不碰真模型。

Phase 13 的 AI 決策、退回、雙胞胎、考題、「故障沒走到」、AI 輪數放寬、模式行與 F7 共用錄製鍵等斷言
隨入口撤除(逐條去向見 [[Verification/Phase14增量3驗證紀錄]])。"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rtb.analyzer.task_store import TaskReader
from rtb.demo import driver as driver_module
from rtb.demo import launcher
from rtb.demo import recordings as demo_recordings
from rtb.demo.driver import DONE, Driver, Outcome, expectations, outcome_of
from rtb.demo.keys import DemoKeys
from rtb.demo.launcher import Role
from rtb.demo.observe import PathBuilder, missing_from_path
from rtb.demo.state_store import StateReader, StateWriter
from rtb.domain.task_state import TaskState
from rtb.modelledger_view import Caller, ModelLedgerView, ledger_path
from tests.demo import fake_recordings as fake
from tests.model.fakes import fake_claude, invocations, write_verification

SRC = Path(__file__).resolve().parents[2] / "src"
PROJECT = SRC.parent
BATCH = "phase14-demo-20260926"
_EVER = ("0000", "9999")


@pytest.fixture
def state(tmp_path):
    writer = StateWriter(tmp_path / "state.db", "demo-1")
    yield writer
    writer.close()


def _driver(tmp_path, state, recordings, **kwargs):
    kwargs.setdefault("user_env", os.environ)
    return Driver(tmp_path / "demos", "demo-1", DemoKeys.generate(), state,
                  recordings_dir=recordings, **kwargs)


def _batch(tmp_path, name="rec", families=("normal",), **kwargs):
    directory = tmp_path / name
    fake.fake_batch(directory, BATCH, families, **kwargs)
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


def _ledger_calls(path):
    view = ModelLedgerView(path)
    try:
        return [(call.caller, call.outcome) for call in view.calls_between(*_EVER)]
    finally:
        view.close()


def _capture_starts(monkeypatch):
    """記下驅動程式每次經啟動器起的常駐行程:(角色, 參數)。"""
    seen = []
    real = launcher.start

    def spy(role, args, keys, **kwargs):
        seen.append((role, list(args), dict(kwargs)))
        return real(role, args, keys, **kwargs)

    monkeypatch.setattr(driver_module.launcher, "start", spy)
    return seen


def _capture_entries(monkeypatch):
    """記下驅動程式每次跑的模型入口:(入口, 參數, 這個情境是不是即時)。"""
    seen = []
    real = launcher.run_entry

    def spy(entry, args, keys, **kwargs):
        seen.append((entry, list(args), kwargs.get("live_model", False)))
        return real(entry, args, keys, **kwargs)

    monkeypatch.setattr(driver_module.launcher, "run_entry", spy)
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


def _live_ready(tmp_path):
    """一支真的會被叫到的假 claude,加一份測試用的即時模式啟用紀錄(寫在這支測試的帳號家目錄;
    啟動器起的子行程在測試裡也讀這個家目錄):模型變數一漏給子行程,它就會真的判成即時、叫到假 claude
    (代碼審 r1 l3)。"""
    script = fake_claude(tmp_path / "bin")
    write_verification()
    env = {**os.environ, "PATH": f"{script.parent}:{os.environ['PATH']}", "RTB_MODEL_LIVE": "1"}
    return script, env


# ---- [S1120] [S1429] 沒列進即時清單:說明入口只讀錄製;分析端永遠只用規則 ----
def test_the_demo_uses_recordings_unless_live_is_switched_on(tmp_path, state, monkeypatch):
    """即時開關打開、PATH 最前面有一支真的叫得到的假 claude、也有啟用紀錄:沒列在即時清單的情境,
    說明與假說入口拿到的環境沒有模型變數——說明回報錄製、沒有任何原因,一次 claude 都沒叫;花費帳記在
    這個情境的暫存目錄。分析端參數沒有任何模型參數、沒有調查紀錄;F1 規則提案 → 說明呼叫 1 次、分析端
    調查 0 次([S1429])。"""
    recordings = _batch(tmp_path)
    script, env = _live_ready(tmp_path)
    starts = _capture_starts(monkeypatch)
    verdict = _driver(tmp_path, state, recordings, user_env=env).run_one("F1")

    assert verdict.status == DONE, verdict.reason
    assert invocations(script) == []  # 一次 claude 都沒叫
    [(_role, args, kwargs)] = [s for s in starts if s[0] is Role.ANALYZER]
    for flag in ("--ai-judge", "--hold-submit", "--recordings-dir", "--recorded-ledger",
                 "--demo-id", "--batch-id"):
        assert flag not in args, flag
    assert "live_model" not in kwargs
    assert _rounds(tmp_path, "F1", "t1") == []
    ledger = tmp_path / "demos" / "demo-1" / "F1" / "model-ledger.db"
    assert ledger.is_file() and not ledger_path().exists()  # 帳號家目錄下的帳沒有被建立
    calls = _ledger_calls(ledger)
    assert [c for c, _o in calls].count(Caller.NARRATIVE.value) == 1
    assert Caller.INVESTIGATION.value not in {c for c, _o in calls}
    details, _rows = _read_details(tmp_path, "F1")
    assert (details.model_mode, details.model_mode_reason) == (
        "recorded", "錄製回應,不是即時呼叫(這個情境不在即時清單)")
    narrated = json.loads(details.narrative_json)
    assert (narrated["mode"], narrated["notices"], narrated["outcome"]) == ("recorded", [], "ok")


# ---- [S1145] [S1166] 即時清單只控制兩支模型入口;F7 不再另外拒收 ----
def test_only_listed_scenarios_get_live_model_env_for_their_entries(tmp_path, state, monkeypatch):
    with pytest.raises(ValueError, match="F9"):
        _driver(tmp_path, state, tmp_path / "rec", live={"F9"})
    assert _driver(tmp_path / "f7", state, tmp_path / "rec", live={"F7"}).live == {"F7"}
    assert _driver(tmp_path / "default", state, tmp_path / "rec").live == frozenset()

    script, env = _live_ready(tmp_path)
    entries = _capture_entries(monkeypatch)
    demo = _driver(tmp_path, state, tmp_path / "rec", user_env=env, live={"F1"})
    assert demo.run_one("F2").status == DONE  # 沒列在清單:入口拿不到變數,判成錄製
    assert invocations(script) == []
    assert demo.run_one("F1").status == DONE  # 列在清單:說明入口真的判成即時,叫到假 claude
    assert invocations(script)
    lives = [live for _entry, _args, live in entries]
    f2 = [live for entry, args, live in entries if "demos/demo-1/F2" in " ".join(args)]
    assert f2 and not any(f2) and lives[-1] is True
    listed, _rows = _read_details(tmp_path, "F1")
    unlisted, _rows = _read_details(tmp_path, "F2")
    assert (listed.model_mode, listed.model_mode_reason) == (
        "live", "即時呼叫(這個情境列在即時清單)")
    assert (unlisted.model_mode, unlisted.model_mode_reason) == (
        "recorded", "錄製回應,不是即時呼叫(這個情境不在即時清單)")


def test_a_listed_scenario_that_falls_back_books_into_its_temporary_ledger(tmp_path, state,
                                                                            monkeypatch):
    """(代碼審 r1 h1)列在即時清單、即時開關有開,但 PATH 上沒有 claude:說明入口的閘道判成錄製,帳落在
    情境的暫存目錄,不落帳號家目錄;錄製用的帳檔一律給,原因照入口回報的寫。"""
    env = {**os.environ, "RTB_MODEL_LIVE": "1"}
    entries = _capture_entries(monkeypatch)
    demo = _driver(tmp_path, state, tmp_path / "rec", user_env=env, live={"F1"})
    assert demo.run_one("F1").status == DONE
    ledger = tmp_path / "demos" / "demo-1" / "F1" / "model-ledger.db"
    for _entry, args, _live in entries:
        assert _arg(args, "--recorded-ledger") == str(ledger) and "--ledger" not in args
    assert ledger.is_file() and not ledger_path().exists()
    details, _rows = _read_details(tmp_path, "F1")
    assert details.model_mode == "recorded"
    assert details.model_mode_reason.startswith("列在即時清單,但模型入口判成錄製回應:")


def test_live_scenarios_record_into_a_fresh_per_demo_directory(tmp_path, state, monkeypatch):
    """[S1166] 即時清單裡的情境,模型入口錄進這次展示專屬的新目錄;入庫的錄製目錄內容不變。新入庫目錄
    是 phase14-demo([S1428])。"""
    committed = PROJECT / "recordings"
    before = _tree_digest(committed)
    entries = _capture_entries(monkeypatch)
    demo = _driver(tmp_path, state, tmp_path / "rec", live={"F1"})
    assert demo.run_one("F1").status == DONE
    assert demo.run_one("F2").status == DONE
    first = next(args for _e, args, _l in entries if "demos/demo-1/F1" in " ".join(args))
    last = next(args for _e, args, _l in entries if "demos/demo-1/F2" in " ".join(args))
    live_dir = Path(_arg(first, "--recordings-dir"))
    assert live_dir == tmp_path / "demos" / "demo-1" / "live-recordings" / "F1"
    assert committed not in live_dir.parents
    assert _arg(first, "--batch-id") == "demo-live-demo-1"
    assert _arg(last, "--recordings-dir") == str(tmp_path / "rec")
    assert _arg(last, "--batch-id") is None
    assert committed / "model" / "phase14-demo" == driver_module.DEMO_RECORDINGS
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
    verdict = _driver(tmp_path, state, _batch(tmp_path)).run_one("F3")
    assert verdict.status == DONE, verdict.reason
    assert decided == []  # 兩條賽跑執行緒都沒有呼叫任何決策函式
    assert _rounds(tmp_path, "F3", "t1") == []  # 分析由之後起的分析端照九條規則做,不問 AI
    # 代碼審 r1 鏡頭2-1:賽跑贏家付費讀的那一步就是規則輪 A,分析端認得、不作廢重讀——整件工作只有一輪
    # 規則輪、只讀 2+2+5 次 DSP,路徑上沒有「資料太舊,重新蒐集」
    reader = TaskReader(tmp_path / "demos" / "demo-1" / "F3" / "analyzer.db")
    try:
        steps = reader.rule_steps("t1")
        events = [event.event for _seq, event in reader.rule_events("t1")]
        reads = [c for c in reader.list_tool_calls("t1") if c.endpoint.value.startswith("dsp:")]
    finally:
        reader.close()
    assert {step.round_id for _seq, step in steps} == {1}
    assert [step.step for _seq, step in steps] == ["A", "B", "C"]
    assert not [e for e in events if e.startswith("restart")], events
    assert len(reads) == 9
    _, rows = _read_details(tmp_path, "F3")
    assert "a_recollect" not in [r.node for r in rows if r.task == "t1"]
    assert ("t1", "task") in driver_module.F3_STREAMS
    assert driver_module.F3_STREAMS[("t1", "task")][1] == ()  # 不再容忍重讀那一圈


# ---- [S1157] [S1167] 依結局選預期;純規則圖不再容忍 AI 節點 ----
def test_the_pure_rule_path_allows_no_ai_nodes(tmp_path, state):
    proposal = driver_module.F1_STREAMS
    required, allowed = expectations("t1", Outcome.PROPOSE, proposal)[("t1", "task")]
    rule_path = ["a_receive", "a_collect", "a_fresh", "a_propose", "x_pending", "x_done"]
    assert missing_from_path(rule_path, required, allowed) is None
    ai_path = ["a_receive", "a_collect", "a_fresh", "a_ai", "a_propose", "x_pending", "x_done"]
    assert missing_from_path(ai_path, required, allowed) == "a_ai"
    no_required, no_allowed = expectations("t1", Outcome.NO_PROPOSE, proposal)[("t1", "task")]
    assert no_allowed == () and no_required[-1] == "a_no_action"
    assert not hasattr(driver_module, "AI_NODES") and not hasattr(driver_module, "with_ai_nodes")
    verdict = _driver(tmp_path, state, _batch(tmp_path)).run_one("F1")
    assert verdict.status == DONE, verdict.reason
    _, rows = _read_details(tmp_path, "F1")
    nodes = [r.node for r in rows if r.task == "t1"]
    assert not {"a_ai", "a_ai_query", "a_exam_hold"} & set(nodes)


def test_the_driver_picks_expectations_by_each_task_outcome():
    rows = [_row("t1", 1, TaskState.RECEIVED), _row("t1", 2, TaskState.NO_ACTION)]
    assert outcome_of(rows) is Outcome.NO_PROPOSE
    assert outcome_of([*rows[:1], _row("t1", 2, TaskState.PROPOSED)]) is Outcome.PROPOSE
    assert outcome_of(rows[:1]) is None
    assert {o.value for o in Outcome} == {"propose", "no_propose"}  # 考題結束撤除
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


def _row(task, seq, state):
    from datetime import UTC, datetime

    from rtb.analyzer.task_store import TaskRow
    return TaskRow(task, seq, state, "c1", None, None, datetime.now(UTC))


# ---- [S1409] F4、F6:接續任務照規則第 3 條「剛被調過預算,先不動」,三處寫入都 0 ----
@pytest.mark.parametrize("code", ["F4", "F6"])
def test_f4_and_f6_follow_ups_show_rule_insufficiency_without_writes(tmp_path, state, code):
    demo = _driver(tmp_path, state, _batch(tmp_path))
    verdict = demo.run_one(code)
    assert verdict.status == DONE, verdict.reason
    assert f"規則第 3 條{driver_module.RULE_THREE_LABEL}" in verdict.summary
    assert "AI" not in verdict.summary
    details, rows = _read_details(tmp_path, code)
    assert details.outcome_note == (
        f"故障照預期;接續任務照九條規則第 3 條:{driver_module.RULE_THREE_LABEL}")
    assert details.platform_operations and all("→ 200" in op for op in details.platform_operations)
    # 代碼審 r1 外家finder-1:接續任務不提案,但原任務 t1 的提案(100 → 110)送進過收件口,照樣要有說明
    narrated = json.loads(details.narrative_json)
    assert narrated["outcome"] == "ok" and ["建議金額", "100 → 110"] in narrated["numbers"]
    follow = next(r.task for r in rows if r.task not in (None, "t1"))
    reader = TaskReader(tmp_path / "demos" / "demo-1" / code / "analyzer.db")
    try:
        history = reader.history(follow)
        assert history[-1].state is TaskState.NO_ACTION
        assert not [row for row in history if row.proposal is not None]  # 沒有提案
        assert [e.detail for _s, e in reader.rule_events(follow) if e.event == "decided"] == [
            "recent_budget_change"]
    finally:
        reader.close()
    assert not [r for r in rows if r.task == follow and r.node.startswith(("x_", "i_"))]


# ---- [S1427] F5:名稱不影響規則;平台只有受攻擊廣告照公式那一筆 ----
def test_f5_runs_without_a_twin_or_an_exam(tmp_path, state, monkeypatch):
    starts = _capture_starts(monkeypatch)
    verdict = _driver(tmp_path, state, _batch(tmp_path, families=("attacked",))).run_one("F5")
    assert verdict.status == DONE, verdict.reason
    [args] = [a for role, a, _k in starts if role is Role.ANALYZER]
    assert "--hold-submit" not in args
    assert not hasattr(driver_module, "F5_TWIN")
    from tests.analyzer.test_f5_end_to_end import ADVERSARIAL_NAME

    assert driver_module.ADVERSARIAL_NAME == ADVERSARIAL_NAME  # [S1427] 分析端測試用同一句
    reader = TaskReader(tmp_path / "demos" / "demo-1" / "F5" / "analyzer.db")
    try:
        assert reader.history("t3") == ()  # 沒有雙胞胎任務
    finally:
        reader.close()
    details, _rows = _read_details(tmp_path, "F5")
    assert "考題" not in verdict.summary and "AI" not in verdict.summary
    assert json.loads(details.narrative_json)["outcome"] == "ok"  # 說明照樣寫,名稱只作逸出引述


# ---- [S1428] 展示批次:有提案的情境要有說明錄製;不提案的可以沒有帳本;不准有分析端調查 ----
def test_committed_demo_recordings_replay_f1_to_f6_with_narratives(tmp_path):
    """入庫的 phase14-demo 批次重播 F1 到 F6:找不到錄製 0 筆、有提案的情境都有說明呼叫。**等錄製**:
    協調者錄完、搬進 recordings/model/phase14-demo 之前這支是紅的(不跳過,免得忘了錄)。"""
    assert driver_module.DEMO_RECORDINGS.is_dir(), (
        f"展示批次還沒入庫:{driver_module.DEMO_RECORDINGS}(協調者錄製後再跑)")
    batches = {json.loads(p.read_text(encoding="utf-8"))["batch_id"]
               for p in driver_module.DEMO_RECORDINGS.glob("*.json")}
    [batch] = batches
    result = demo_recordings.check_demo_batch(
        driver_module.DEMO_RECORDINGS, batch, tmp_path / "replay",
        user_env={"PATH": str(tmp_path / "no-claude"), "HOME": str(tmp_path / "home")},
    )
    assert result.passed, result


def test_proposed_demo_tasks_require_only_narrative_recordings(tmp_path, monkeypatch):
    """[S1428] 只有說明錄製的一批(假錄製冒充正式後端錄的,只在這支測試):F1 到 F6 都跑完、找不到錄製
    0 筆;F4/F6 接續任務不提案、不另外說明,只有原任務 t1 那一份提案說明一次(代碼審 r1 外家finder-1)。
    空目錄、分析端起不來都不准算過。"""
    complete = tmp_path / "complete"
    fake.fake_batch(complete, BATCH, ("normal", "attacked"), backend="claude_code")
    result = demo_recordings.check_demo_batch(complete, BATCH, tmp_path / "work")
    assert result.missing == 0 and result.problems == (), result
    assert result.passed and [v[1] for v in result.verdicts] == [DONE] * 6, result.verdicts
    root = next((tmp_path / "work" / "demos").iterdir())
    for code in ("F4", "F6"):  # 接續任務不提案:說明只算 t1 那一份提案一次
        calls = [c for c, _o in _ledger_calls(root / code / "model-ledger.db")]
        assert calls.count(Caller.NARRATIVE.value) == 1, (code, calls)

    missing = tmp_path / "missing"  # 少了 F5 那一份說明:F5 找不到錄製
    fake.fake_batch(missing, BATCH, ("normal",), backend="claude_code")
    partial = demo_recordings.check_demo_batch(missing, BATCH, tmp_path / "work1",
                                               codes=("F5",))
    assert partial.missing >= 1 and not partial.passed

    empty = demo_recordings.check_demo_batch(tmp_path / "none", BATCH, tmp_path / "work2",
                                             codes=("F1",))
    assert empty.missing >= 1 and not empty.passed  # 空目錄會過前兩條,過不了這一條
    # 代碼審 r1 l2:分析端起不來,這個情境沒有提案、沒有說明:不准算過
    real = launcher.start

    def broken(role, args, keys, **kwargs):
        if role is Role.ANALYZER:
            raise launcher.StartFailed("simulated slow start")
        return real(role, args, keys, **kwargs)

    monkeypatch.setattr(driver_module.launcher, "start", broken)
    down = demo_recordings.check_demo_batch(complete, BATCH, tmp_path / "work3", codes=("F1",))
    assert down.missing == 0 and not down.passed
    assert any("F1 沒有跑完" in p for p in down.problems)


def test_the_replay_needs_a_narrative_call_for_every_real_proposal(tmp_path):
    """(代碼審 r1 k1/l2/s3;增量 3 r1 外家finder-1 改寫)重播本身的問題:情境沒跑完;送進過收件口的提案
    數(從情境自己的分析端資料庫讀,不看被驗的驅動寫了什麼)多於帳裡的說明呼叫;帳裡有分析端調查。
    一份提案都沒有的情境沒有帳不算問題。"""
    from rtb import modelledger

    missing, problems = demo_recordings.replay_problems(
        (("F1", "incomplete", "startup failed"), ("F4", DONE, None), ("F9", DONE, None)),
        {"F1": tmp_path / "none.db", "F4": tmp_path / "none4.db", "F9": tmp_path / "none9.db"},
        {"F1": 1, "F4": 1, "F9": 0})
    assert missing == 0
    assert problems == ("F1 沒有跑完:incomplete(startup failed)",
                        "F1 有 1 份送出的提案,帳裡只有 0 次提案說明的錄製呼叫",
                        "F4 有 1 份送出的提案,帳裡只有 0 次提案說明的錄製呼叫")
    assert not demo_recordings.BatchCheck(0, problems).passed
    empty = tmp_path / "empty.db"
    modelledger.used_so_far(empty, None)  # 建出一本空帳
    assert demo_recordings.replay_problems((("F3", DONE, None),), {"F3": empty},
                                           {"F3": 1})[1] == (
        "F3 有 1 份送出的提案,帳裡只有 0 次提案說明的錄製呼叫",)
    assert demo_recordings.replay_problems((("F4", DONE, None),), {"F4": empty},
                                           {"F4": 0}) == (0, ())
    assert Caller.NARRATIVE.value == demo_recordings.NARRATIVE_CALLER
    assert {Caller.INVESTIGATION.value} == demo_recordings.RETIRED_CALLERS


def test_proposals_are_counted_from_the_scenario_analyzer_database(tmp_path):
    """外家finder-1:「有提案」從情境自己的分析端資料庫讀(送進收件口的提案),不看驅動寫的說明欄;
    資料庫不在就是 0。"""
    from datetime import UTC, datetime

    from rtb.analyzer.task_store import TaskStore
    from tests.analyzer.conftest import make_proposal

    assert demo_recordings.proposals_in(tmp_path / "absent.db") == 0
    db = tmp_path / "analyzer.db"
    store = TaskStore(db)
    try:
        now = datetime.now(UTC)
        proposal = make_proposal()
        store.create_task(proposal.task_id, proposal.campaign_id, now)
        for seq, state in enumerate((TaskState.COLLECTING_EVIDENCE, TaskState.ANALYZING), 1):
            store.commit_step(proposal.task_id, seq, state, now)
        store.commit_step(proposal.task_id, 3, TaskState.PROPOSED, now, proposal=proposal)
        assert demo_recordings.proposals_in(db) == 0  # 還沒送進收件口
        store.commit_step(proposal.task_id, 4, TaskState.HANDED_OFF, now, proposal=proposal)
    finally:
        store.close()
    assert demo_recordings.proposals_in(db) == 1


def test_the_batch_itself_must_be_one_clean_batch(tmp_path):
    """[S1428] 新批號格式 phase14-demo-YYYYMMDD;批次裡不准有分析端調查的錄製。"""
    directory = tmp_path / "rec"
    fake.fake_batch(directory, BATCH, ("normal",), backend="claude_code")
    assert demo_recordings.batch_problems(directory, BATCH) == ()
    assert demo_recordings.BATCH_PATTERN.fullmatch("phase14-demo-20260926")
    assert not demo_recordings.BATCH_PATTERN.fullmatch("phase13-demo-20260925")
    assert demo_recordings.batch_problems(directory, "phase14-demo-20260101")  # 別的批次
    assert any("批次編號要是 phase14-demo-YYYYMMDD" in p
               for p in demo_recordings.batch_problems(directory, "phase13-demo-20260925"))
    investigation = fake.recording(fake.core.Caller.INVESTIGATION, "s", "u", 5, "ok", BATCH,
                                   backend="claude_code")
    path = fake.write(directory, investigation)
    assert any("分析端調查" in p for p in demo_recordings.batch_problems(directory, BATCH))
    path.unlink()
    for outcome in ("config_error", "ledger_busy"):
        bad = fake.recording(fake.core.Caller.NARRATIVE, "s", outcome, 5, "", BATCH,
                             outcome=fake.core.Outcome(outcome), backend="claude_code")
        path = fake.write(directory, bad)
        assert any(demo_recordings.batch_problems(directory, BATCH))
        path.unlink()
    unsure = fake.recording(fake.core.Caller.NARRATIVE, "s", "u", 5, "ok", BATCH,
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
    found = fake.recording(fake.core.Caller.NARRATIVE, "s", "u", 5, "ok", BATCH)
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
    「人已同意,放回排隊」。F7 每一件的數字都跟 F1 相同([S1411] 逐件核對九條結論)。"""
    from tests.demo import test_driver as td

    seeded = driver_module.f7_campaigns(3)
    assert all((c.budget, c.spend, c.name) == (driver_module.F1_CAMPAIGN.budget,
                                                driver_module.F1_CAMPAIGN.spend,
                                                driver_module.F1_CAMPAIGN.name) for c in seeded)
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


def test_the_server_refuses_only_unknown_codes_in_the_live_list(tmp_path, capsys):
    """伺服器的 --live 清單:不認得的情境在啟動時就拒;F7 不再另外拒收(Phase 14 增量 3 撤除 [S1145]
    的 F7 限制:分析端不呼叫 AI,即時清單只控制兩支模型入口);沒給就是空的(全部錄製)。"""
    from rtb.demo import server

    work = str(tmp_path)
    with pytest.raises(SystemExit):
        server._arguments(["--work-dir", work, "--live", "F1,F9"])
    assert "F9" in capsys.readouterr().err
    assert server._arguments(["--work-dir", work])[1] == []
    assert server._arguments(["--work-dir", work, "--live", "F1,F7"])[1] == ["F1", "F7"]
