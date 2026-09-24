"""Phase 11B 增量 1 代碼審第 1 輪:Claude Code 子行程與中斷、回應判定的補強。

- 呼叫途中被中斷(Ctrl-C 的 KeyboardInterrupt、SIGTERM)也要照 [S939] 的順序殺整組、
  確認空了、刪暫存目錄。
- 登入狀態檢查耗掉的時間算進這次呼叫的總期限。
- 不是錯誤的回應,子類型不是 success 就是讀不懂;成功形狀帶工具痕跡要讓評估停下。
- 讀不成 JSON 的非 0 結束,標準錯誤只留本機日誌、不進子原因與錄製檔。
一律用假的 claude 腳本;不呼叫真的 claude。
"""

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from tests.model.fakes import alive, claude_json, fake_claude, live, request

SRC = Path(__file__).resolve().parents[2] / "src"




def _wait_started(script, limit=10.0):
    log = script.with_name("claude.log")
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        found = list(log.glob("*.started"))
        if found:
            return int(found[0].name.split(".")[0])
        time.sleep(0.02)
    raise AssertionError("假 claude 沒有起來")


def _gone(pid, limit=5.0):
    deadline = time.monotonic() + limit
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not alive(pid)


@pytest.fixture
def default_sigint():
    """非互動 shell 的背景工作會把 SIGINT 設成忽略:測試期間裝回預設處理器,結束還原
    (代碼審第 3 輪)。"""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.usefixtures("default_sigint")
def test_an_interrupted_call_kills_the_whole_group(tmp_path):
    """Ctrl-C(KeyboardInterrupt)打斷等待:例外照樣往外丟,但整組先殺掉、確認空了、暫存目錄刪掉。"""
    script = fake_claude(tmp_path / "c", sleep=30, grandchild=True)
    child_env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    started = {}

    def interrupt():
        started["pid"] = _wait_started(script)
        os.kill(os.getpid(), signal.SIGINT)

    timer = threading.Thread(target=interrupt, daemon=True)
    timer.start()
    with pytest.raises(KeyboardInterrupt):
        cc.run_claude([str(script), "-p"], "x", child_env, 20.0)
    timer.join(5)
    grandchild = int((tmp_path / "c" / "grandchild.pid").read_text(encoding="utf-8"))
    assert _gone(started["pid"]) and _gone(grandchild)
    cwd = Path(next((tmp_path / "c" / "claude.log").glob("*.cwd")).read_text().strip())
    assert not cwd.parent.exists()


def test_sigterm_during_a_call_cleans_up_the_same_way(tmp_path):
    """SIGTERM 在呼叫期間轉成例外,走同一條清理;用子行程跑,免得把整套測試殺掉。"""
    script = fake_claude(tmp_path / "c", sleep=30, grandchild=True)
    code = _child_prelude() + (
        "import os, signal, sys, threading, time\n"
        "from pathlib import Path\n"
        "from rtb import modelclaude as cc\n"
        f"log = Path({str(script.with_name('claude.log'))!r})\n"
        "def later():\n"
        "    while not list(log.glob('*.started')):\n"
        "        time.sleep(0.02)\n"
        "    os.kill(os.getpid(), signal.SIGTERM)\n"
        "threading.Thread(target=later, daemon=True).start()\n"
        "try:\n"
        f"    cc.run_claude([{str(script)!r}, '-p'], 'x', dict(os.environ), 20.0)\n"
        "except BaseException as stopped:\n"
        "    print(type(stopped).__name__)\n"
        "    sys.exit(3)\n")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                            text=True, timeout=60, check=False)
    started = int(next((tmp_path / "c" / "claude.log").glob("*.started")).name.split(".")[0])
    grandchild = int((tmp_path / "c" / "grandchild.pid").read_text(encoding="utf-8"))
    assert _gone(started) and _gone(grandchild), result.stdout + result.stderr
    assert result.returncode == 3, result.stdout + result.stderr  # 轉成例外、往外丟
    cwd = Path(next((tmp_path / "c" / "claude.log").glob("*.cwd")).read_text().strip())
    assert not cwd.parent.exists()


def test_the_login_check_counts_against_the_call_deadline(tmp_path, monkeypatch):
    """登入檢查花掉的時間要從這次呼叫的總期限扣掉,不是再給一份完整的逾時。直接看傳給本體呼叫的期限
    (不比牆鐘時間:機器忙時假 claude 的啟動時間會蓋過差距,代碼審第 3 輪)。"""
    script = fake_claude(tmp_path / "c", auth_sleep=0.5)
    budgets = []
    real = cc.run_claude

    def recording(args, stdin_text, env, timeout_seconds, **kwargs):
        budgets.append((args[1] if len(args) > 1 else "", timeout_seconds))
        return real(args, stdin_text, env, timeout_seconds, **kwargs)

    monkeypatch.setattr(cc, "run_claude", recording)
    started = time.monotonic()
    mc.call_model(request(timeout_seconds=8.0), live(cc.ClaudeCodeBackend(script)),
                  recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")
    (login, login_budget), (_, call_budget) = budgets
    assert login == "auth" and login_budget == 8.0
    assert call_budget <= 8.0 - 0.5  # 至少扣掉登入等待的 0.5 秒
    assert call_budget >= 8.0 - (time.monotonic() - started) - 0.01


def test_a_success_needs_the_success_subtype(tmp_path):
    """是否錯誤欄寫 false、子類型卻不是 success 的回應,不算成功。"""
    script = fake_claude(tmp_path / "c", claude_json(subtype="error_during_execution"))
    with pytest.raises(mc.UnreadableModelResponse):
        mc.call_model(request(), live(cc.ClaudeCodeBackend(script)), recordings_dir=tmp_path,
                      ledger=tmp_path / "l.sqlite")


def test_success_shaped_tool_use_is_flagged(tmp_path):
    """成功形狀的回應帶多輪對話:除了讀不懂,也要標「偵測到工具使用」(評估據此停下)。"""
    script = fake_claude(tmp_path / "c", claude_json(num_turns=3))
    with pytest.raises(mc.UnreadableModelResponse) as failed:
        mc.call_model(request(), live(cc.ClaudeCodeBackend(script)), recordings_dir=tmp_path,
                      ledger=tmp_path / "l.sqlite")
    assert failed.value.tool_use is True


def test_stderr_stays_out_of_the_ledger_and_recordings(tmp_path, caplog):
    """讀不成 JSON 的非 0 結束:標準錯誤可能有本機路徑或帳號,只留本機日誌,不進子原因與錄製檔。"""
    secret = "/Users/someone/secret-path token-XYZ"  # noqa: S105 - 假的
    script = fake_claude(tmp_path / "c", "boom", code=2, stderr=secret)
    recordings = tmp_path / "rec"
    recordings.mkdir()
    with pytest.raises(mc.TransientServiceError) as failed:
        mc.call_model(request(batch_id="b1"), live(cc.ClaudeCodeBackend(script), record=True),
                      recordings_dir=recordings, ledger=tmp_path / "l.sqlite")
    assert failed.value.unclassified and secret not in (failed.value.sub_reason or "")
    assert all(secret not in p.read_text(encoding="utf-8") for p in recordings.glob("*.json"))
    assert secret.encode() not in (tmp_path / "l.sqlite").read_bytes()
    assert any(secret in r.getMessage() for r in caplog.records)  # 本機日誌留得到


def _child_prelude():
    """子行程的程式碼開頭:換掉帳號家目錄的讀法(指到這支測試的暫存家目錄;子行程沒有共用夾具)。"""
    from rtb import modelledger_view
    from tests.conftest import child_prelude

    return child_prelude(modelledger_view.account_home())


# ---- Linux 上的行程群組判斷(CI 在 Linux 每次呼叫白等 5 秒) ----
def _fake_proc(root, entries, *, with_self=True):
    """假的 /proc:每筆 (pid, 狀態, pgrp) 寫一個 <pid>/stat
    (格式同 Linux:pid (comm) 狀態 ppid pgrp …;comm 故意含空白與括號)。預設也放本行程自己那一筆
    (產品碼先用它自我檢查這份行程表是不是本機、同一個命名空間的)。"""
    root.mkdir(parents=True, exist_ok=True)
    if with_self:
        entries = [*entries, (os.getpid(), "R", os.getpgrp())]
        if not (root / "self").is_symlink():
            (root / "self").symlink_to(str(os.getpid()))  # 像核心的 /proc/self:指到讀的人自己
    for pid, state, pgrp in entries:
        (root / str(pid)).mkdir(parents=True, exist_ok=True)
        (root / str(pid) / "stat").write_text(
            f"{pid} (claude (x) y) {state} 1 {pgrp} {pgrp} 0 -1 4194304 0 0 0\n", encoding="ascii")
    (root / "thread-self").mkdir(exist_ok=True)  # 不是數字的項目要略過


def test_linux_group_check_ignores_the_unreaped_leader(tmp_path, monkeypatch):
    """Linux 上群組只剩還沒領回的主行程(殭屍)時,對群組送 0 號訊號會成功;要改看 /proc,
    同一個 pgrp、狀態不是 Z 的才算活的。讀不到 /proc 就退回原本的送訊號判斷。"""
    monkeypatch.setattr(cc.os, "killpg", lambda _group, _sig: None)  # 像 Linux:永遠成功
    proc = tmp_path / "proc"
    _fake_proc(proc, [(4242, "Z", 4242), (5000, "S", 7777)])
    monkeypatch.setattr(cc, "PROC_ROOT", proc, raising=False)
    assert cc._has_live_members(4242) is False  # 只剩殭屍主行程;別的群組不算
    _fake_proc(proc, [(4300, "S", 4242)])
    assert cc._has_live_members(4242) is True  # 同群組有睡著的孫行程
    monkeypatch.setattr(cc, "PROC_ROOT", tmp_path / "no-proc", raising=False)
    assert cc._has_live_members(4242) is True  # 讀不到 /proc:退回送訊號判斷


@pytest.mark.parametrize("shape", ["empty", "no_stat", "other_namespace", "self_wrong_group",
                                   "self_points_elsewhere"])
def test_a_proc_that_does_not_look_like_ours_falls_back_to_signals(tmp_path, monkeypatch, shape):
    """/proc 讀得到但不像本機的 Linux 行程表(空的、沒有 stat、另一個 PID 命名空間、
    自己的群組對不上):不能判成「沒有活的」,要退回送訊號判斷(代碼審:判錯的方向會漏殺)。"""
    monkeypatch.setattr(cc.os, "killpg", lambda _group, _sig: None)  # 送訊號說:群組還有活的
    proc = tmp_path / "proc"
    proc.mkdir()
    if shape == "no_stat":
        for pid in (os.getpid(), 4242):
            (proc / str(pid)).mkdir()
            (proc / str(pid) / "status").write_text("State: S\n", encoding="ascii")
    elif shape == "other_namespace":
        _fake_proc(proc, [(1, "S", 1)], with_self=False)
    elif shape == "self_wrong_group":
        _fake_proc(proc, [(os.getpid(), "R", os.getpgrp() + 12345)], with_self=False)
        (proc / "self").symlink_to(str(os.getpid()))
    elif shape == "self_points_elsewhere":  # 核心說讀的人是別的編號:不是同一個命名空間
        _fake_proc(proc, [(os.getpid(), "R", os.getpgrp())], with_self=False)
        (proc / "self").symlink_to("40000")
    monkeypatch.setattr(cc, "PROC_ROOT", proc)
    assert cc._proc_group_members(4242) is None
    assert cc._has_live_members(4242) is True


@pytest.mark.parametrize(("shape", "self_link"), [("host_proc_collision", "40001"),
                                                 ("group_outside_namespace", "2")])
def test_a_proc_seen_from_another_namespace_falls_back_to_signals(tmp_path, monkeypatch, shape,
                                                                  self_link):
    """本行程在另一個 PID 命名空間裡(2 號、群組 0 表示群組在命名空間外面),代碼審第 2 輪:
    - host_proc_collision:unshare -pf 沒另掛 /proc,看到的是宿主的行程表;宿主的 2 號剛好是 kthreadd
      (群組也是 0),只比 stat 會通過;claude 群組在宿主是 40057、卡在 D。
      核心的 /proc/self 指到宿主編號。
    - group_outside_namespace:另掛了 /proc(/proc/self 對得上),但群組在外面,一樣判不準。
    兩種都要退回送訊號判斷,不能判成「沒有活的」。"""
    monkeypatch.setattr(cc.os, "killpg", lambda _group, _sig: None)  # 送訊號說:群組還有活的
    proc = tmp_path / "proc"
    entries = ([(1, "S", 1), (2, "S", 0), (3, "I", 0), (57, "I", 0), (40057, "D", 40057)]
               if shape == "host_proc_collision" else [(2, "S", 0), (57, "I", 0)])
    _fake_proc(proc, entries, with_self=False)
    (proc / "self").symlink_to(self_link)
    monkeypatch.setattr(cc, "PROC_ROOT", proc)
    with monkeypatch.context() as ids:  # 只在這兩次呼叫期間換掉本行程的編號
        ids.setattr(cc.os, "getpid", lambda: 2)
        ids.setattr(cc.os, "getpgrp", lambda: 0)
        members = cc._proc_group_members(57)
        live = cc._has_live_members(57)
    assert members is None and live is True


@pytest.mark.skipif(sys.platform != "linux", reason="只有 Linux 有這種格式的 /proc")
def test_the_real_proc_is_parsed():
    """真的 /proc:自己的群組有活的;一個已結束、還沒領回的新工作階段子行程,它的群組沒有活的。"""
    assert cc._proc_group_members(os.getpgrp()) is True
    process = subprocess.Popen(["/bin/sh", "-c", "exit 0"], start_new_session=True)
    try:
        assert cc._exited_unreaped(process.pid, time.monotonic() + 10)
        assert cc._proc_group_members(process.pid) is False
        assert cc._has_live_members(process.pid) is False
    finally:
        process.wait()


def test_the_liveness_helper_treats_a_zombie_as_gone(tmp_path):
    """測試共用的存活判斷跟產品碼同一套:讀得到、而且產品碼認得的 /proc/<pid>/stat 就看是不是殭屍;
    讀不到或欄位不足(產品碼判不出來)才送訊號。"""
    proc = tmp_path / "proc"
    _fake_proc(proc, [(4242, "Z", 4242), (4300, "S", 4242)])
    assert alive(4242, proc_root=proc) is False
    assert alive(4300, proc_root=proc) is True
    assert alive(os.getpid(), proc_root=tmp_path / "no-proc") is True
    assert alive(2**22 + 12345, proc_root=tmp_path / "no-proc") is False
    short = tmp_path / "short"  # 欄位不足:產品碼判不出來 → 退回送訊號(本行程還活著)
    (short / str(os.getpid())).mkdir(parents=True)
    (short / str(os.getpid()) / "stat").write_text(f"{os.getpid()} (x) Z\n", encoding="ascii")
    assert alive(os.getpid(), proc_root=short) is True


def test_linux_like_group_check_does_not_wait_out_the_limit(tmp_path, monkeypatch):
    """模擬 Linux(送 0 號訊號對殭屍主行程也成功、/proc 看得到它是 Z):呼叫結束後清理不能白等到
    GROUP_EXIT_WAIT_SECONDS 才放手。"""
    real_killpg = os.killpg
    proc = tmp_path / "proc"
    proc.mkdir()

    def linux_killpg(group, sig):
        if sig == 0:
            return  # Linux:群組只剩殭屍主行程時照樣成功
        with contextlib.suppress(ProcessLookupError, PermissionError):
            real_killpg(group, sig)

    real_popen = cc.subprocess.Popen

    def popen_with_proc(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        _fake_proc(proc, [(process.pid, "Z", process.pid)])  # 結束後就是殭屍主行程
        return process

    monkeypatch.setattr(cc.os, "killpg", linux_killpg)
    monkeypatch.setattr(cc.subprocess, "Popen", popen_with_proc)
    monkeypatch.setattr(cc, "PROC_ROOT", proc, raising=False)
    script = fake_claude(tmp_path / "c", claude_json())
    process = real_popen([str(script), "-p"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, start_new_session=True)
    _fake_proc(proc, [(process.pid, "Z", process.pid)])
    assert cc._exited_unreaped(process.pid, time.monotonic() + 10)
    started = time.monotonic()
    cc._empty_group(process.pid)
    assert time.monotonic() - started < 0.5, "清理白等到群組上限"
    process.wait()
    started = time.monotonic()
    cc.run_claude([str(script), "-p"], "x", {"PATH": os.environ["PATH"]}, 20.0)
    assert time.monotonic() - started < cc.GROUP_EXIT_WAIT_SECONDS
