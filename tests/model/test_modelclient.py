"""Phase 11B 增量 1:模型用戶端的核心(跟後端無關的部分)與花費帳。

合約 [S900] 到 [S903]、[S907]、[S920] 到 [S922]、[S925] 到 [S927]、[S931]、[S941](計劃
[[Projects/RTB_Phase11B大模型接入_計劃]] 第 7 版;[S932]、[S935] 在 tests/test_suite_isolation.py,
Claude Code 後端的合約在 test_claude_backend.py)。這裡的即時呼叫一律用行程內的假後端;要看子行程的
只用假的 claude 腳本,不呼叫真的 claude。
"""

import inspect
import io
import itertools
import json
import os
import plistlib
import pwd
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelledger_view as view
from rtb import modelrecording as rec
from rtb.ops import metrics
from tests.model.fakes import (
    FakeBackend,
    fake_claude,
    invocations,
    live,
    recorded,
    reply,
    request,
    write_verification,
)
from tests.ops.rows import TENANTS, Rows

ROOT = Path(__file__).resolve().parents[2]
USD = mc.NANOUSD_PER_USD


@pytest.fixture
def dirs(tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    return recordings, tmp_path / "ledger.sqlite"


def call(settings, req, dirs):
    recordings, ledger = dirs
    return mc.call_model(req, settings, recordings_dir=recordings, ledger=ledger)


def rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()


# ---- [S900] ----
def test_without_every_switch_no_claude_process_starts(dirs, tmp_path, monkeypatch):
    script = fake_claude(tmp_path / "bin")
    write_verification()  # 啟用紀錄有效,只看三個開關
    for switch, demo, claude in itertools.product((None, "0", "true", "1"), (None, "demo-1"),
                                                  (None, script)):
        environ = {} if switch is None else {mc.LIVE_ENV: switch}
        settings = mc.settings_from_env(environ, demo, claude)
        expect_live = switch == "1" and demo is not None and claude is not None
        assert (settings.mode is mc.Mode.LIVE) == expect_live, (switch, demo, claude)
        assert (settings.backend is not None) == expect_live
        if switch == "1" and demo is None and claude is not None:
            assert any("即時模式需要展示編號" in note for note in settings.notices)
    # 先用假後端錄一份,再改成錄製模式重播:一個子行程都不啟動
    first = call(live(FakeBackend(reply("答案")), record=True), request(batch_id="b1"), dirs)
    assert first.source is mc.Source.LIVE
    started = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: started.append(a) or real_popen(*a, **k))
    for settings in (recorded(), mc.settings_from_env({mc.LIVE_ENV: "1"}, "demo-1", None),
                     mc.settings_from_env({}, "demo-1", script)):
        replay = call(settings, request(), dirs)
        assert replay.source is mc.Source.RECORDED and replay.text == "答案"
    assert started == [] and invocations(script) == []
    assert [r.effective_nanousd for r in rows(dirs[1])][1:] == [0, 0, 0]  # 重播記 0 元


# ---- [S901] ----
def test_a_missing_recording_fails_instead_of_calling_live(dirs, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _explode)
    with pytest.raises(mc.NoRecording) as failed:
        call(recorded(), request("沒錄過的內容"), dirs)
    assert failed.value.outcome is mc.Outcome.NO_RECORDING
    [row] = rows(dirs[1])
    assert (row.source, row.outcome, row.effective_nanousd) == ("recorded", "no_recording", 0)


def _explode(*_args, **_kwargs):
    raise AssertionError("錄製模式不該啟動子行程")


# ---- [S902] ----
def _spend(dirs, demo, output_tokens):
    """用一次成功的即時呼叫把某個展示編號花到指定金額(原價是輸出 token 乘單價,已用再乘 1.2)。"""
    call(live(FakeBackend(reply("x", input_tokens=0, output_tokens=output_tokens))),
         request(demo_id=demo, max_output_tokens=32_000), dirs)


def test_the_budget_caps_stop_the_call_before_it_is_sent(dirs):
    assert (core.DEMO_CAP_NANOUSD, core.MONTH_CAP_NANOUSD) == (1 * USD, 20 * USD)
    assert core.SAFETY_FACTOR == (6, 5)
    _spend(dirs, "demo-1", 30_000)  # 0.3 乘 1.2 = 0.36 美元
    _spend(dirs, "demo-1", 30_000)  # 0.72 美元
    assert ledger_db.used_so_far(dirs[1], "demo-1").demo_nanousd == 720_000_000
    backend = FakeBackend(reply("不該送出"))
    big = request(demo_id="demo-1", max_output_tokens=25_000)  # 預留約 0.3 美元
    assert core.reservation_nanousd(big, mc.DEFAULT_MODEL) > 280_000_000
    with pytest.raises(mc.LocalCapRefused, match="已達上限") as failed:
        call(live(backend), big, dirs)
    assert failed.value.outcome is mc.Outcome.LOCAL_CAP_REFUSED
    assert backend.calls == []
    call(live(backend), request(demo_id="demo-1"), dirs)  # 小的照樣過
    for n in range(53):  # 每月 20 美元:換展示編號也擋得住
        _spend(dirs, f"month-{n}", 30_000)
    assert ledger_db.used_so_far(dirs[1], "fresh").month_nanousd > 19.7 * USD
    backend = FakeBackend(reply("不該送出"))
    with pytest.raises(mc.LocalCapRefused, match="已達上限"):
        call(live(backend), request(demo_id="fresh", max_output_tokens=20_000), dirs)
    assert backend.calls == []
    booked = [r for r in rows(dirs[1]) if r.outcome == "local_cap_refused"]
    assert len(booked) == 2 and all(r.effective_nanousd == 0 for r in booked)


# ---- [S903] ----
def test_concurrent_reservations_never_exceed_the_cap(dirs):
    release = threading.Event()

    def slow(_call):
        release.wait(5)
        return reply("x", input_tokens=0, output_tokens=0)

    backend = FakeBackend(slow)
    each = request(demo_id="demo-1", max_output_tokens=25_000)  # 每筆預留約 0.3 美元
    per_call = core.reservation_nanousd(each, mc.DEFAULT_MODEL)
    assert 3 * per_call < core.DEMO_CAP_NANOUSD < 4 * per_call
    barrier, outcomes = threading.Barrier(8), []

    def worker():
        barrier.wait()
        try:
            outcomes.append(call(live(backend), each, dirs))
        except (mc.LocalCapRefused, mc.LedgerBusy) as refused:  # 忙碌也是沒送出
            outcomes.append(refused)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 10
    while len(backend.calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # 讓還沒預留的都走到預留那一步
    in_flight = ledger_db.used_so_far(dirs[1], "demo-1").demo_nanousd  # 預留都還沒結算
    release.set()
    for thread in threads:
        thread.join(10)
    sent = [o for o in outcomes if isinstance(o, mc.ModelResult)]
    assert len(outcomes) == 8 and len(sent) == len(backend.calls) <= 3
    assert in_flight == len(sent) * per_call <= core.DEMO_CAP_NANOUSD
    assert ledger_db.used_so_far(dirs[1], "demo-1").demo_nanousd <= core.DEMO_CAP_NANOUSD


# ---- [S907] ----
def test_every_model_call_is_booked_and_shows_in_the_metric(dirs, tmp_path):
    backend = FakeBackend(reply("好", input_tokens=10, output_tokens=5, cache_read=3,
                                cache_5m=2, reported_usd=0.0),
                          mc.TransientServiceError("overloaded"))
    call(live(backend, record=True), request("甲", batch_id="b1"), dirs)
    with pytest.raises(mc.TransientServiceError):
        call(live(backend, record=True), request("乙", batch_id="b1"), dirs)
    call(recorded(), request("甲"), dirs)
    with pytest.raises(mc.NoRecording):
        call(recorded(), request("丙"), dirs)
    booked = rows(dirs[1])
    assert [(r.source, r.outcome, r.backend) for r in booked] == [
        ("live", "ok", "claude_code"), ("live", "transient", "claude_code"),
        ("recorded", "ok", "recording"), ("recorded", "no_recording", "recording")]
    first = booked[0]
    assert (first.caller, first.model, first.demo_id, first.batch_id) == (
        "eval_candidate", mc.DEFAULT_MODEL, "demo-1", "b1")
    assert (first.input_tokens, first.output_tokens, first.cache_write_5m_tokens,
            first.cache_write_1h_tokens, first.cache_read_tokens) == (10, 5, 2, 0, 3)
    assert first.list_nanousd == 10 * 2_000 + 5 * 10_000 + 2 * 2_500 + 3 * 200
    assert first.settled_nanousd == -(-first.list_nanousd * 6 // 5)
    assert first.reported_nanousd == 0
    assert first.reserved_nanousd > 0 and first.latency_ms is not None
    assert booked[2].effective_nanousd == 0 and booked[2].latency_ms == first.latency_ms
    data = Rows(tmp_path)
    try:
        now = datetime.now(UTC)
        report = metrics.collect_window(
            now - timedelta(hours=1), now + timedelta(hours=1), executor_db=data.executor_db,
            analyzer_db=data.analyzer_db, tenants=TENANTS, model_ledger=dirs[1])
    finally:
        data.close()
    samples = {(s.label_map()["model_caller"], s.label_map()["model_outcome"],
                s.label_map()["model_source"]): s.value
               for s in report.samples if s.name == "model_and_jev"}
    assert samples == {("eval_candidate", "ok", "live"): 1,
                       ("eval_candidate", "transient", "live"): 1,
                       ("eval_candidate", "ok", "recorded"): 1,
                       ("eval_candidate", "no_recording", "recorded"): 1}


# ---- [S920] ----
def test_recordings_are_found_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert mc.default_recordings_dir() == ROOT / "recordings" / "model"
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "src", checkout / "src")
    shutil.copy(ROOT / "pyproject.toml", checkout / "pyproject.toml")
    recordings = checkout / "recordings" / "model"
    recordings.mkdir(parents=True)
    mc.call_model(request("問題", batch_id="b1"), live(FakeBackend(reply("錄好的答案")), True),
                  recordings_dir=recordings, ledger=tmp_path / "l.sqlite")
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    code = ("from pathlib import Path\nfrom rtb import modelclient as mc\n"
            "req = mc.ModelRequest(caller=mc.Caller.EVAL_CANDIDATE, system='固定系統提示', "
            "user='問題', max_output_tokens=50, timeout_seconds=5.0)\n"
            "settings = mc.settings_from_env({}, None, None)\n"
            "print(mc.default_recordings_dir())\n"
            "print(mc.call_model(req, settings, recordings_dir=mc.default_recordings_dir(), "
            f"ledger=Path({str(tmp_path / 'child.sqlite')!r})).text)\n")  # 子行程不碰帳號的真帳
    env = {**os.environ, "PYTHONPATH": str(checkout / "src")}
    result = subprocess.run([sys.executable, "-c", code], cwd=elsewhere, env=env,
                            capture_output=True, text=True, timeout=60, check=True)
    assert result.stdout.splitlines() == [str(recordings), "錄好的答案"]


# ---- [S921] ----
def test_the_same_scenario_twice_gives_the_same_prompt_and_key():
    """增量 1 的共用部分:錄製鍵只看內容(不含後端種類)、佔位符依出現順序;時間與真實編號不進送出內容。
    接入點 1、2 用真的執行迴圈跑兩次的部分,在增量 2 組出假說與說明的輸入時補進這支測試。"""
    key_params = set(inspect.signature(mc.recording_key).parameters)
    assert key_params == {"caller", "model", "system", "user", "max_output_tokens"}
    base = {"caller": mc.Caller.EVAL_CANDIDATE, "model": mc.DEFAULT_MODEL, "system": "s",
            "user": "u", "max_output_tokens": 10}
    assert mc.recording_key(**base) == mc.recording_key(**base)
    for field, other in (("system", "s2"), ("user", "u2"), ("max_output_tokens", 11),
                         ("caller", mc.Caller.HYPOTHESIS)):
        assert mc.recording_key(**{**base, field: other}) != mc.recording_key(**base), field
    runs = (["t-123", "k-9", "t-123", "t-456"], ["t-777", "k-1", "t-777", "t-000"])
    sent = []
    for ids in runs:
        holder = rec.Placeholders("任務")
        text = " ".join(holder.substitute(i) for i in ids)
        sent.append(text)
        assert holder.restore(text) == " ".join(ids)
    assert sent[0] == sent[1] == "任務甲 任務乙 任務甲 任務丙"
    many = rec.Placeholders("任務")
    names = [many.substitute(f"id-{n}") for n in range(12)]
    assert len(set(names)) == 12
    assert many.restore(" ".join(reversed(names))) == " ".join(
        f"id-{n}" for n in reversed(range(12)))


# ---- [S922] ----
def test_an_unsettled_reservation_counts_in_its_own_month(dirs, monkeypatch):
    clock = [datetime(2026, 8, 31, 23, 59, 50, tzinfo=UTC)]
    monkeypatch.setattr(core, "utc_now", lambda: clock[0])
    req = request(max_output_tokens=1000)
    with pytest.raises(KeyboardInterrupt):  # 行程在呼叫途中被殺:沒有結算
        call(live(FakeBackend(KeyboardInterrupt())), req, dirs)
    reserved = core.reservation_nanousd(req, mc.DEFAULT_MODEL)
    [row] = rows(dirs[1])
    assert row.outcome is None and row.month == "2026-08"
    assert ledger_db.used_so_far(dirs[1], "demo-1").month_nanousd == reserved
    clock[0] = datetime(2026, 9, 1, 0, 0, 10, tzinfo=UTC)
    now = ledger_db.used_so_far(dirs[1], "demo-1")
    assert now.month_nanousd == 0  # 不跨月
    assert now.demo_nanousd == reserved  # 同一個展示編號照樣算


# ---- [S925] ----
def test_live_calls_always_book_into_the_one_ledger(tmp_path, monkeypatch, _isolated_home):
    home_ledger = _isolated_home / ".rtb" / "model-ledger.sqlite"
    assert mc.live_ledger_path() == home_ledger
    code = "from rtb import modelclient as mc\nprint(mc.live_ledger_path())\n"
    seen = set()
    for name in ("checkout-a", "checkout-b"):  # 兩份簽出、兩個不同的工作目錄
        copy = tmp_path / name
        shutil.copytree(ROOT / "src", copy / "src")
        cwd = tmp_path / f"{name}-cwd"
        cwd.mkdir()
        env = {**os.environ, "PYTHONPATH": str(copy / "src")}
        result = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                                capture_output=True, text=True, timeout=60, check=True)
        seen.add(result.stdout.strip())
    # 子行程沒有測試的注入點:印出的是帳號家目錄那一本(只印路徑、不開帳),跟 HOME 無關
    assert seen == {str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".rtb" / "model-ledger.sqlite")}
    # 評估紀錄命令列即時跑(假 claude 在傳進去的 PATH 上):寫進家目錄那一本;即時模式不接受換帳檔
    from rtb.eval import record

    fake_claude(tmp_path / "bin")
    write_verification()
    monkeypatch.chdir(tmp_path)
    environ = {**os.environ, mc.LIVE_ENV: "1", "PATH": str(tmp_path / "bin")}
    err = io.StringIO()
    code = record.run(["--demo-id", "demo-1", "--ledger", str(tmp_path / "other.sqlite")],
                      out=io.StringIO(), err=err, environ=environ)
    assert code != record.EXIT_OK and "即時模式" in err.getvalue()
    assert not (tmp_path / "other.sqlite").exists()
    assert record.run(["--demo-id", "demo-1", "--recordings-dir", str(tmp_path / "rec")],
                      out=io.StringIO(), err=io.StringIO(), environ=environ) == record.EXIT_OK
    booked = rows(home_ledger)
    assert booked and {r.source for r in booked} == {"live"}
    from rtb import modelledger_writeoff

    with pytest.raises(SystemExit):
        modelledger_writeoff.run(["--ledger", str(tmp_path / "x"), "--reservation-id", "1",
                                  "--amount-usd", "0", "--reason", "r", "--evidence", "u"])


# ---- [S926] ----
def test_the_ledger_ignores_the_command_line_now(dirs, monkeypatch):
    time_words = {"now", "today", "clock", "at", "when", "month", "until", "since"}
    for function in (mc.call_model, mc.settings_from_env, ledger_db.used_so_far,
                     ledger_db.write_off,
                     core.reservation_nanousd):
        assert not time_words & set(inspect.signature(function).parameters), function
    assert not time_words & set(mc.ModelRequest.__dataclass_fields__)
    before = datetime.now(UTC)
    call(live(), request(), dirs)
    after = datetime.now(UTC)
    [row] = rows(dirs[1])
    assert before <= datetime.fromisoformat(row.reserved_at) <= after
    assert row.month == before.strftime("%Y-%m")
    monkeypatch.setattr(core, "utc_now", lambda: before + timedelta(days=40))
    assert ledger_db.used_so_far(dirs[1], "demo-1").month_nanousd == 0


# ---- [S927] ----
def test_a_stale_price_table_refuses_live_mode(dirs, tmp_path, monkeypatch):
    script = fake_claude(tmp_path / "bin")
    write_verification()
    environ = {mc.LIVE_ENV: "1"}
    checked = datetime.combine(mc.PRICES_CHECKED_ON, datetime.min.time(), tzinfo=UTC)
    monkeypatch.setattr(core, "utc_now", lambda: checked + timedelta(days=90, hours=12))
    assert mc.settings_from_env(environ, "demo-1", script).mode is mc.Mode.LIVE
    monkeypatch.setattr(core, "utc_now", lambda: checked + timedelta(days=91, hours=1))
    stale = mc.settings_from_env(environ, "demo-1", script)
    assert stale.mode is mc.Mode.RECORDED and stale.backend is None
    assert any("價目表" in note and "90" in note for note in stale.notices)
    backend = FakeBackend(reply("不該送出"))
    with pytest.raises(mc.ConfigError, match="價目表"):  # 直接給即時設定也一樣拒絕
        call(live(backend), request(), dirs)
    assert backend.calls == [] and not dirs[1].exists()  # 沒記帳
    assert timedelta(days=90) == core.PRICE_MAX_AGE
    assert isinstance(mc.PRICES_CHECKED_ON, date)
    with pytest.raises(mc.UnknownModel):  # 模型代號只收價目表裡有的
        mc.settings_from_env({**environ, mc.MODEL_ENV: "claude-imaginary-9"}, "demo-1", script)


# ---- [S931] ----
def test_recording_never_overwrites_an_existing_file(dirs):
    backend = FakeBackend(reply("第一次的答案"))
    first = call(live(backend, record=True), request(batch_id="b1"), dirs)
    [path] = list(dirs[0].glob("*.json"))
    saved = path.read_bytes()
    again = call(live(backend, record=True), request(batch_id="b1"), dirs)  # 同一批:讀它
    assert len(backend.calls) == 1
    assert again.shared and again.text == first.text and again.source is mc.Source.RECORDED
    with pytest.raises(mc.RecordingConflict):  # 別的批次:呼叫前就拒絕
        call(live(backend, record=True), request(batch_id="b2"), dirs)
    assert len(backend.calls) == 1 and path.read_bytes() == saved
    assert [(r.source, r.effective_nanousd == 0) for r in rows(dirs[1])] == [
        ("live", False), ("recorded", True)]
    with pytest.raises(ValueError, match="批次"):  # 即時加錄製一定要帶批次編號
        call(live(backend, record=True), request(batch_id=None), dirs)


# ---- [S941] ----
HOOKS = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}


def test_managed_hooks_refuse_live_mode(  # noqa: PLR0915 - 每個管理政策來源一個情境
        tmp_path, monkeypatch):
    """每一種管理政策來源各一個案例;路徑常數換成暫存目錄(家目錄那一個照呼叫時的 HOME 算)。"""
    script = fake_claude(tmp_path / "bin")
    write_verification()
    environ = {mc.LIVE_ENV: "1"}
    system = tmp_path / "system"
    system.mkdir()
    plist = tmp_path / "com.anthropic.claudecode.plist"
    managed_prefs = tmp_path / "Managed Preferences"
    user = pwd.getpwuid(os.getuid()).pw_name
    user_plist = managed_prefs / user / "com.anthropic.claudecode.plist"
    monkeypatch.setattr(cc, "MANAGED_DIRS", (system,))
    monkeypatch.setattr(cc, "MDM_PLISTS", (plist,))
    monkeypatch.setattr(cc, "MANAGED_PREFERENCES", managed_prefs, raising=False)
    remote = Path.home() / ".claude" / "remote-settings.json"
    assert mc.settings_from_env(environ, "demo-1", script).mode is mc.Mode.LIVE  # 沒有任何來源
    cases = [  # (來源檔, 內容)
        (system / "managed-settings.json", HOOKS),
        (system / "managed-settings.d" / "10-team.json", {"env": {"X": "1"}}),
        (system / "managed-settings.d" / "20-more.json", {"apiKeyHelper": "/bin/echo k"}),
        (system / "managed-mcp.json", {"mcpServers": {"x": {"command": "y"}}}),
        (plist, HOOKS),
        (user_plist, {"env": {"X": "1"}}),  # 個人層的 MDM 設定檔
        (system / "managed-settings.d" / "30-helper.json",
         {"policyHelper": {"path": "/usr/local/bin/corp-policy"}}),  # 啟動時動態算管理設定
        (system / "managed-settings.d" / "40-helpers.json",
         {"policyHelpers": {"darwin": {"path": "/x"}}}),
        (remote, {"mcpServers": {"x": {"command": "y"}}}),
    ]
    for path, content in cases:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".plist":
            path.write_bytes(plistlib.dumps(content))
        else:
            path.write_text(json.dumps(content), encoding="utf-8")
        refused = mc.settings_from_env(environ, "demo-1", script)
        assert refused.mode is mc.Mode.RECORDED and refused.backend is None, path
        assert any("管理政策" in note or "MDM" in note for note in refused.notices), path
        if path.suffix == ".plist":  # 換成無害的內容:這個來源就不再擋
            path.write_bytes(plistlib.dumps({"x": 1}))
        else:
            path.write_text(json.dumps({"permissions": {"deny": ["Bash"]}}), encoding="utf-8")
        assert mc.settings_from_env(environ, "demo-1", script).mode is mc.Mode.LIVE, path
    (system / "managed-settings.json").write_text("not json", encoding="utf-8")  # 讀不懂也拒絕
    assert mc.settings_from_env(environ, "demo-1", script).mode is mc.Mode.RECORDED
    assert invocations(script) == []  # 設定檢查不花額度:沒有呼叫模型


# ---- [S942] ----
def test_live_mode_needs_a_current_verification_record(tmp_path):
    script = fake_claude(tmp_path / "bin")
    environ = {mc.LIVE_ENV: "1"}

    def mode():
        return mc.settings_from_env(environ, "demo-1", script)

    assert cc.verification_path() == Path.home() / ".rtb" / "live-verification.json"
    refused = mode()  # 沒有紀錄
    assert refused.mode is mc.Mode.RECORDED and any("啟用紀錄" in n for n in refused.notices)
    for name in cc.REQUIRED_CHECKS:  # 任一項沒過
        write_verification(**{name: False})
        assert mode().mode is mc.Mode.RECORDED, name
    write_verification(version="1.0.0 (Claude Code)")  # 版本不同:Claude Code 升級後自動失效
    refused = mode()
    assert refused.mode is mc.Mode.RECORDED and any("版本" in n for n in refused.notices)
    cc.verification_path().write_text("{", encoding="utf-8")  # 讀不懂
    assert mode().mode is mc.Mode.RECORDED
    write_verification(isolation="somewhere")
    assert mode().mode is mc.Mode.RECORDED
    write_verification()
    ok = mode()
    assert ok.mode is mc.Mode.LIVE and ok.backend.isolation is cc.Isolation.EMPTY_HOME
    write_verification(isolation="real_home")  # 退路隔離:每次即時啟動前查記憶目錄
    assert mode().backend.isolation is cc.Isolation.REAL_HOME
    memory = Path.home() / ".claude" / "projects" / "x" / "memory"
    memory.mkdir(parents=True)
    assert mode().mode is mc.Mode.LIVE  # 空的記憶目錄不算
    (memory / "MEMORY.md").write_text("- 使用者的記憶", encoding="utf-8")
    refused = mode()
    assert refused.mode is mc.Mode.RECORDED and any("記憶" in n for n in refused.notices)
