# ruff: noqa: S106
"""Phase 12 模型入口那幾條驗收([S1027] 到 [S1030],計劃
[[Projects/RTB_Phase12一鍵展示與HTML報告_計劃]]):說明、假說兩支命令列與分析端是這次展示的模型入口。真的起行程跑情境,分析端與說明命令列讀測試自己寫的假
錄製(tests/demo/fake_recordings.py),不碰真模型;帳號家目錄是測試夾具換上的暫存目錄。"""

import json
import os
import shutil
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from rtb.demo import driver as driver_module
from rtb.demo import launcher, present
from rtb.demo.driver import DONE, Driver
from rtb.demo.keys import DemoKeys
from rtb.demo.page import MODEL_LABEL, render_page
from rtb.demo.server import DemoService
from rtb.demo.state import ModelSource, ScenarioCode
from rtb.demo.state_store import ScenarioDetails, StateReader, StateWriter
from rtb.modelledger_view import ledger_path
from tests.demo import fake_recordings as fake
from tests.model.fakes import fake_claude, invocations, write_verification

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


def _page_state(tmp_path, demo_id="demo-1"):
    reader = StateReader(tmp_path / "state.db")
    try:
        return build(reader, demo_id)
    finally:
        reader.close()


def build(reader, demo_id):
    return present.build_demo_state(reader, demo_id, running=False, now=datetime.now(UTC))


def _scenario(page_state, code):
    return next(s for s in page_state.scenarios if s.code is code)


def _arg(args, name):
    return args[args.index(name) + 1] if name in args else None


def _capture(monkeypatch, on_first_start=None):
    """記下驅動程式起的每支模型入口拿到的參數:常駐的分析端經 launcher.start,一次跑完的說明與假說經
    launcher.run_entry。on_first_start:第一次起行程時先做的事(在情境本體起任何行程之前)。"""
    seen = []
    real_start, real_entry = launcher.start, launcher.run_entry

    def start(role, args, keys, **kwargs):
        if on_first_start is not None and not seen:
            on_first_start()
        seen.append((role.value, list(args)))
        return real_start(role, args, keys, **kwargs)

    def run_entry(entry, args, keys, **kwargs):
        seen.append((entry, list(args)))
        return real_entry(entry, args, keys, **kwargs)

    monkeypatch.setattr(driver_module.launcher, "start", start)
    monkeypatch.setattr(driver_module.launcher, "run_entry", run_entry)
    return seen


# ---- [S1027] 說明卡:程式算的數字在前,再來是 AI 標示與文字;頁面不列模式 ----
def test_the_model_step_shows_computed_numbers_first_and_labels_the_text(tmp_path, state):
    recordings = tmp_path / "rec"
    fake.fake_batch(recordings, BATCH, {"normal": [fake.PROPOSE]}, narrative=fake.NARRATIVE)
    assert _driver(tmp_path, state, recordings).run_one("F1").status == DONE
    shown = _page_state(tmp_path)
    step = _scenario(shown, ScenarioCode.F1).model_step
    assert step is not None and step.result_kind == "ok" and step.source is ModelSource.RECORDED
    page = render_page(shown, form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    card = page.split('class="ai-node-card"', 1)[1]
    numbers, label = card.index("100 → 110"), card.index(MODEL_LABEL)
    assert numbers < label < card.index(fake.NARRATIVE[:8])
    assert "錄製回應" not in card and "即時" not in card
    failed = replace(step, narrative=None, result_kind="no_recording")  # 沒有成功結果:顯示結果類別
    page = render_page(replace(shown, scenarios=tuple(
        replace(s, model_step=failed) if s.code is ScenarioCode.F1 else s
        for s in shown.scenarios)), form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    card = page.split('class="ai-node-card"', 1)[1].split("</div>", 1)[0]
    assert "AI 這次沒有給出回答" in card and MODEL_LABEL not in card


# ---- [S1028] 模型入口保留實際模式,頁面不區分來源 ----
def test_the_page_hides_model_mode_while_entries_keep_their_actual_choice(
    tmp_path, state, monkeypatch,
):
    """F1 列在即時清單、即時開關有開,但 PATH 上沒有 claude:驅動照清單會以為是即時,三支模型入口
    自己判成錄製。頁面不顯示分析端模式與說明來源;記錄仍保留入口實際判出的模式與原因。
    假說命令列印的那一欄照原樣存。即時清單的錄製目錄在情境起第一支行程前先放好假錄製,讓退回錄製的
    入口讀得到回應(說明卡顯示 AI 回答,但不顯示來源)。"""
    recordings = tmp_path / "rec"
    fake.fake_batch(recordings, BATCH, {"normal": [fake.PROPOSE]}, narrative=fake.NARRATIVE)
    live_dir = tmp_path / "demos" / "demo-1" / "live-recordings" / "F1"
    seen = _capture(monkeypatch, lambda: shutil.copytree(recordings, live_dir))
    demo = _driver(tmp_path, state, tmp_path / "unused", live={"F1"},
                   user_env={**os.environ, "RTB_MODEL_LIVE": "1"})
    assert demo.ai_setup("F1").live is True  # 驅動以為這個情境是即時
    assert demo.run_one("F1").status == DONE
    reader = StateReader(tmp_path / "state.db")
    try:
        details = reader.scenario_details("demo-1", "F1")
        shown = build(reader, "demo-1")
    finally:
        reader.close()
    # 分析端:模式行回報錄製與原因,頁面隱藏
    assert details.model_mode == "recorded"
    f1 = _scenario(shown, ScenarioCode.F1)
    assert f1.model_mode_reason.startswith("列在即時清單,但分析端判成錄製回應:")
    page = render_page(shown, form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    assert f1.model_mode_reason not in page and "AI 採錄製回應" not in page
    # 說明命令列:內部回報錄製來源,頁面說明卡只標 AI 產生
    narrated = json.loads(details.narrative_json)
    assert (narrated["mode"], narrated["source"]) == ("recorded", "recorded")
    assert f1.model_step.source is ModelSource.RECORDED
    assert MODEL_LABEL in page.split('class="ai-node-card"', 1)[1]
    assert "錄製回應" not in page and "即時清單" not in page
    # 假說命令列:印出的那一欄照原樣存(這次沒有告警),頁面照它寫
    assert json.loads(details.hypothesis_json)["status"] == "no_alert"
    assert f1.hypothesis is None and f1.hypothesis_note == present.NO_ALERT
    # 告警響了、入口回報即時時,假說卡的來源照入口寫(情境是不是列在清單都一樣)
    found, _note = present._hypothesis(ScenarioDetails(ai_enabled=True, hypothesis_json=json.dumps(
        {"status": "failed", "reason": "no_recording", "alerts": ["x"], "mode": "live"})))
    assert found.source is ModelSource.LIVE
    assert {name for name, _args in seen} >= {"analyzer", "narrate", "hypothesis"}


# ---- [S1029] 每按一次觸發一個新展示編號,這次展示的每一支模型入口拿到同一個 ----
def test_one_demo_id_per_trigger_reaches_every_model_entry(tmp_path, monkeypatch):
    recordings = tmp_path / "rec"
    fake.fake_batch(recordings, BATCH, {"normal": [fake.PROPOSE]}, narrative=fake.NARRATIVE)
    seen = _capture(monkeypatch)
    made = []

    def factory(base, demo_id, keys, writer):
        made.append(demo_id)
        return Driver(base, demo_id, keys, writer, user_env=os.environ, recordings_dir=recordings)

    service = DemoService(tmp_path / "demos", tmp_path / "state.db", tmp_path / "reports",
                          driver_factory=factory)
    try:
        by_trigger = []
        for _ in range(2):
            before = len(seen)
            service.start(("F1",), full=False)
            deadline = time.monotonic() + 120
            while service.running and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not service.running
            by_trigger.append(seen[before:])
    finally:
        service.stop()
    assert len(made) == 2 and made[0] != made[1]  # 兩次觸發,兩個展示編號
    for demo_id, entries in zip(made, by_trigger, strict=True):
        ids = {name: _arg(args, "--demo-id") for name, args in entries
               if name in ("analyzer", "narrate", "hypothesis")}
        assert set(ids) == {"analyzer", "narrate", "hypothesis"}, entries
        assert set(ids.values()) == {demo_id}  # 每一支模型入口拿到這次的同一個編號


# ---- [S1030] 沒開即時開關(RTB_MODEL_LIVE=true 也算沒開):帳記在這次展示的暫存目錄 ----
def test_recorded_demos_book_into_a_temporary_ledger(tmp_path, state, monkeypatch):
    """即時開關寫成 true(不是 1)、情境也列在即時清單、PATH 最前面有叫得到的假 claude 與啟用紀錄:
    模型用戶端同一支判定把 true 當沒開,三支模型入口都判成錄製,一次 claude 都沒叫;帳記在這個情境的
    暫存目錄,帳號家目錄下的帳沒有被建立。"""
    recordings = tmp_path / "rec"
    fake.fake_batch(recordings, BATCH, {"normal": [fake.PROPOSE]}, narrative=fake.NARRATIVE)
    script = fake_claude(tmp_path / "bin")
    write_verification()
    env = {**os.environ, "PATH": f"{script.parent}:{os.environ['PATH']}",
           "RTB_MODEL_LIVE": "true"}
    live_dir = tmp_path / "demos" / "demo-1" / "live-recordings" / "F1"
    seen = _capture(monkeypatch, lambda: shutil.copytree(recordings, live_dir))
    assert _driver(tmp_path, state, tmp_path / "unused", user_env=env,
                   live={"F1"}).run_one("F1").status == DONE
    assert invocations(script) == []
    ledger = tmp_path / "demos" / "demo-1" / "F1" / "model-ledger.db"
    for name, args in seen:
        if name in ("analyzer", "narrate", "hypothesis"):
            assert _arg(args, "--recorded-ledger") == str(ledger), name
    assert ledger.is_file() and not ledger_path().exists()
    reader = StateReader(tmp_path / "state.db")
    try:
        details = reader.scenario_details("demo-1", "F1")
    finally:
        reader.close()
    assert details.model_mode == "recorded"
    assert json.loads(details.narrative_json)["mode"] == "recorded"
