"""Phase 11B 增量 1 代碼審第 1 輪:Claude Code 子行程與中斷、回應判定的補強。

- 呼叫途中被中斷(Ctrl-C 的 KeyboardInterrupt、SIGTERM)也要照 [S939] 的順序殺整組、
  確認空了、刪暫存目錄。
- 登入狀態檢查耗掉的時間算進這次呼叫的總期限。
- 不是錯誤的回應,子類型不是 success 就是讀不懂;成功形狀帶工具痕跡要讓評估停下。
- 讀不成 JSON 的非 0 結束,標準錯誤只留本機日誌、不進子原因與錄製檔。
一律用假的 claude 腳本;不呼叫真的 claude。
"""

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
from tests.model.fakes import claude_json, fake_claude, live, request

SRC = Path(__file__).resolve().parents[2] / "src"


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # macOS:還沒領回的殭屍
        return False
    return True


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
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


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
