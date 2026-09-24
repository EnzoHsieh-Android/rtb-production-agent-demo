"""Phase 11B 增量 1 代碼審第 2 輪:即時呼叫前後的護欄(後端與子行程)。

- claude 是會被自動更新改指的符號連結:入口核版本時就固定解開後的路徑,跑到一半換版也不會用到沒實測過
  的版本;舊版檔被清掉時是設定錯誤、結算 0。
- 登入檢查用完這次呼叫的期限:沒呼叫模型,算設定錯誤、結算 0、評估不算送出。
- Popen 起了子行程、還沒進清理之前收到中斷,也要殺整組。
- 關終端機(SIGHUP)或 SIGQUIT 跟 SIGTERM 一樣轉成例外、殺整組、放掉錄製佔位。
一律用假的 claude 腳本;不呼叫真的 claude。
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelledger_view as view
from tests.model.fakes import alive, fake_claude, invocations, live, request, write_verification

SRC = Path(__file__).resolve().parents[2] / "src"


def _rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()




def test_a_claude_upgrade_mid_run_never_reaches_an_unverified_version(tmp_path):
    """入口判成即時之後,連結改指新版:之後的呼叫仍用實測過的那一版;那一版被清掉就是設定錯誤。"""
    old = fake_claude(tmp_path / "versions" / "old")
    new = fake_claude(tmp_path / "versions" / "new")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    link = bin_dir / "claude"
    link.symlink_to(old)
    write_verification()
    settings = mc.settings_from_env({mc.LIVE_ENV: "1", "PATH": str(bin_dir)}, "demo-1", link)
    assert settings.mode is mc.Mode.LIVE
    link.unlink()
    link.symlink_to(new)  # 自動更新把連結改指新版
    mc.call_model(request("第一次"), settings, recordings_dir=tmp_path,
                  ledger=tmp_path / "l.sqlite")
    assert len(invocations(old)) == 1 and invocations(new) == []
    old.unlink()  # 舊版檔被清掉
    with pytest.raises(mc.ConfigError):
        mc.call_model(request("第二次"), settings, recordings_dir=tmp_path,
                      ledger=tmp_path / "l.sqlite")
    assert invocations(new) == []
    assert _rows(tmp_path / "l.sqlite")[-1].effective_nanousd == 0


def test_a_login_check_that_eats_the_deadline_is_not_a_model_call(tmp_path):
    """登入檢查就把期限用完:沒呼叫模型,設定錯誤、結算 0(不是逾時、不照預留)。"""
    script = fake_claude(tmp_path / "c", auth_sleep=3)
    with pytest.raises(mc.ConfigError) as failed:
        mc.call_model(request(timeout_seconds=1.0), live(cc.ClaudeCodeBackend(script)),
                      recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")
    assert failed.value.outcome is mc.Outcome.CONFIG_ERROR
    assert invocations(script) == []
    [row] = _rows(tmp_path / "l.sqlite")
    assert row.effective_nanousd == 0


def test_a_login_check_that_leaves_no_time_is_not_a_model_call(tmp_path, monkeypatch):
    """登入檢查自己過了、但剩下的期限是 0:一樣沒呼叫模型,設定錯誤、結算 0。"""
    import time

    script = fake_claude(tmp_path / "c")
    monkeypatch.setattr(cc.ClaudeCodeBackend, "check_login",
                        lambda _self, _timeout=10.0: time.sleep(1.2))
    with pytest.raises(mc.ConfigError):
        mc.call_model(request(timeout_seconds=1.0), live(cc.ClaudeCodeBackend(script)),
                      recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")
    assert invocations(script) == []
    [row] = _rows(tmp_path / "l.sqlite")
    assert row.effective_nanousd == 0


def test_an_interrupt_right_after_popen_still_kills_the_group(tmp_path, monkeypatch):
    """Popen 回來、還沒進清理的 try 之前就收到 Ctrl-C:照樣殺整組。"""
    # 非互動 shell 的背景工作會把 SIGINT 設成忽略:先裝回預設處理器,結束還原(代碼審第 3 輪)
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        _interrupt_right_after_popen(tmp_path, monkeypatch)
    finally:
        signal.signal(signal.SIGINT, previous)


def _interrupt_right_after_popen(tmp_path, monkeypatch):
    script = fake_claude(tmp_path / "c", sleep=30)
    started = []
    real = cc.subprocess.Popen

    def popen_then_interrupt(*args, **kwargs):
        process = real(*args, **kwargs)
        started.append(process.pid)
        signal.raise_signal(signal.SIGINT)  # 送到自己這個執行緒
        return process

    monkeypatch.setattr(cc.subprocess, "Popen", popen_then_interrupt)
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    with pytest.raises(KeyboardInterrupt):
        cc.run_claude([str(script), "-p"], "x", env, 20.0)
    assert started and not alive(started[0])


def test_a_hangup_during_a_recorded_call_kills_the_group_and_frees_the_name(tmp_path):
    """關終端機(SIGHUP):轉成例外、殺整組、放掉錄製佔位;SIGQUIT 同理(用子行程跑,免得殺掉整套)。"""
    for number, name in ((signal.SIGHUP, "hup"), (signal.SIGQUIT, "quit")):
        work = tmp_path / name
        script = fake_claude(work / "c", sleep=30, grandchild=True)
        recordings = work / "rec"
        code = _child_prelude() + (
            "import os, signal, sys, threading, time\n"
            "from pathlib import Path\n"
            "from rtb import modelclaude as cc, modelclient as mc\n"
            f"log = Path({str(script.with_name('claude.log'))!r})\n"
            "def later():\n"
            "    while not list(log.glob('*.started')):\n"
            "        time.sleep(0.02)\n"
            f"    os.kill(os.getpid(), {int(number)})\n"
            "threading.Thread(target=later, daemon=True).start()\n"
            "req = mc.ModelRequest(mc.Caller.EVAL_CANDIDATE, 's', 'u', 50, 20.0, demo_id='d',"
            " batch_id='b1')\n"
            f"settings = mc.Settings(mc.Mode.LIVE, mc.DEFAULT_MODEL, True,"
            f" cc.ClaudeCodeBackend(Path({str(script)!r})), ())\n"
            "try:\n"
            f"    mc.call_model(req, settings, recordings_dir=Path({str(recordings)!r}),"
            f" ledger=Path({str(work / 'l.sqlite')!r}))\n"
            "except BaseException as stopped:\n"
            "    print(type(stopped).__name__)\n"
            "    sys.exit(3)\n")
        result = subprocess.run([sys.executable, "-c", code],
                                env={**os.environ, "PYTHONPATH": str(SRC)}, capture_output=True,
                                text=True, timeout=60, check=False)
        log = script.with_name("claude.log")
        started = int(next(log.glob("*.started")).name.split(".")[0])
        grandchild = int((work / "c" / "grandchild.pid").read_text(encoding="utf-8"))
        assert result.returncode == 3, (name, result.stdout + result.stderr)
        assert not alive(started) and not alive(grandchild), name
        assert list(recordings.glob("*.json")) == [], name  # 佔位放掉了


def test_a_leftover_claim_says_it_may_be_an_interrupted_run(tmp_path):
    """別的批次留下的佔位:拒絕訊息要講「可能是中斷留下的佔位」,不是「已經錄過」。"""
    from rtb import modelrecording as rec

    key = rec.recording_key(mc.Caller.EVAL_CANDIDATE, mc.DEFAULT_MODEL, "固定系統提示", "hello",
                            50)
    (tmp_path / f"{key}.json").write_text(json.dumps({"claimed_by_batch": "old"}),
                                          encoding="utf-8")
    with pytest.raises(mc.RecordingConflict, match="中斷留下的佔位"):
        mc.call_model(request(batch_id="b1"), live(record=True), recordings_dir=tmp_path,
                      ledger=tmp_path / "l.sqlite")


def test_the_claude_backend_refuses_to_run_off_the_main_thread(tmp_path):
    """背景執行緒裝不了訊號處理器(斷線時會留下 claude):直接拒絕,設定錯誤、結算 0、沒起行程。"""
    import threading

    script = fake_claude(tmp_path / "c")
    outcome = {}

    def worker():
        try:
            cc.run_claude([str(script), "-p"], "x", {"PATH": os.environ["PATH"]}, 5.0)
        except BaseException as stopped:
            outcome["direct"] = stopped
        try:
            mc.call_model(request(), live(cc.ClaudeCodeBackend(script)), recordings_dir=tmp_path,
                          ledger=tmp_path / "l.sqlite")
        except BaseException as stopped:
            outcome["call"] = stopped

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(30)
    assert isinstance(outcome.get("direct"), mc.ConfigError)
    assert isinstance(outcome.get("call"), mc.ConfigError)
    assert invocations(script) == []
    [row] = _rows(tmp_path / "l.sqlite")
    assert row.effective_nanousd == 0


def test_an_ignored_hangup_stays_ignored(tmp_path):
    """用 nohup(SIGHUP 原本是忽略)跑的即時評估:斷線時呼叫照常完成,不被轉成例外中止。"""
    script = fake_claude(tmp_path / "c", sleep=1.5)
    code = _child_prelude() + (
        "import os, signal, sys, threading, time\n"
        "from pathlib import Path\n"
        "from rtb import modelclaude as cc\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        f"log = Path({str(script.with_name('claude.log'))!r})\n"
        "def later():\n"
        "    while not list(log.glob('*.started')):\n"
        "        time.sleep(0.02)\n"
        "    os.kill(os.getpid(), signal.SIGHUP)\n"
        "threading.Thread(target=later, daemon=True).start()\n"
        f"code, out, _ = cc.run_claude([{str(script)!r}, '-p'], 'x', dict(os.environ), 20.0)\n"
        "print('done', code)\n"
        "print('still ignored', signal.getsignal(signal.SIGHUP) is signal.SIG_IGN)\n")
    result = subprocess.run([sys.executable, "-c", code],
                            env={**os.environ, "PYTHONPATH": str(SRC)}, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "done 0" in result.stdout and "still ignored True" in result.stdout


def _child_prelude():
    """子行程的程式碼開頭:換掉帳號家目錄的讀法(指到這支測試的暫存家目錄;子行程沒有共用夾具)。"""
    from rtb import modelledger_view
    from tests.conftest import child_prelude

    return child_prelude(modelledger_view.account_home())
