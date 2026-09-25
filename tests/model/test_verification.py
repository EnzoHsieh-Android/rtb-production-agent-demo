"""Phase 11B 增量 1:即時模式的實測命令列(寫啟用紀錄的那支,[S942] 的另一半)。

真的實測由協調者在本機跑;這裡用一支會照參數與環境變化的假 claude(Python 腳本),只驗逐項判定與
「全過才寫紀錄」。代碼審第 1 輪補強:固定附加輸入超過預留常數不寫、記憶用不可混淆的暗號驗、設定來源
對照用命令列蓋不掉的毒值並加正面對照、輸出上限要有撞頂的正面證據、錯誤回應一律判沒過、工具與登入的反例。
不呼叫真的 claude。
"""

import contextlib
import io
import json
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelverify
from tests.model.fakes import FAKE_VERSION

SCRIPT = """#!{python}
import json, os, sys
CONFIG = {config}
args = sys.argv[1:]
home = os.environ.get("HOME", "")
def flag(name):
    return args[args.index(name) + 1] if name in args else None
if CONFIG["echo_token"]:  # 把長期權杖印到標準錯誤:紀錄、帳、日誌與錯誤訊息都不准留下它
    print("token=" + os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "none"), file=sys.stderr)
if args[:2] == ["auth", "status"]:
    empty = "rtb-claude-" in home
    token = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    empty_ok = CONFIG["login_empty_home"] or (CONFIG["login_with_token"] and token)
    ok = CONFIG["login_everywhere"] and (empty_ok or not empty)
    print(json.dumps({{"loggedIn": ok}})); sys.exit(0)
if args == ["--version"]:
    print(CONFIG["version"]); sys.exit(0)
if "--rtb-no-such-flag" in args:
    print("error: unknown option '--rtb-no-such-flag'", file=sys.stderr); sys.exit(1)
prompt = sys.stdin.read()
cap = int(os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "0") or 0)
tools = flag("--tools") or ""
turns = 3 if (tools and CONFIG["contrast_changes"]) or CONFIG["tools_ignored"] else 1
# 真的用了工具:串流輸出裡有 tool_use 與 tool_result(只有一輪也算)
used_tool = turns == 3 or (CONFIG["tool_use_one_turn"] and "ls /" in prompt)
if "ls /" in prompt and CONFIG["cap_log"]:  # 記下工具那兩項拿到的輸出上限
    open(CONFIG["cap_log"], "a").write(f"{{cap}}\\n")
if "ls /" in prompt and not used_tool:  # 拒絕執行指令的回答
    if CONFIG["continued_refusal"]:
        turns = 2  # 回答撞頂、自動續寫一輪(只有訊息,沒有工具)
    if CONFIG["long_refusal"] and cap < 307:
        turns = 2  # 協調者實測:中文拒絕 307 token,上限 200 時撞頂續寫一輪
settings = os.path.join(home, ".claude", "settings.json")
suppressed = flag("--setting-sources") == "" and not CONFIG["suppression_broken"]
poisoned = os.path.exists(settings) and not suppressed and CONFIG["poison_readable"]
peek = os.path.exists(settings) and suppressed and CONFIG["main_peeks"]  # 壓制參數下仍讀到毒值
if poisoned or peek:  # 讀到毒值:照設定裡的 API 位址送一個請求(真的 claude 會 POST /v1/messages)
    import socket, urllib.parse
    url = urllib.parse.urlparse(json.load(open(settings))["env"]["ANTHROPIC_BASE_URL"])
    if CONFIG["url_log"]:
        open(CONFIG["url_log"], "a").write(url.geturl() + "\\n")
    try:
        with socket.create_connection((url.hostname, url.port), timeout=2) as conn:
            conn.sendall(b"POST /v1/messages?beta=true HTTP/1.1\\r\\nAuthorization: Bearer x\\r\\n")
    except OSError:
        pass
control = os.path.exists(settings) and not suppressed and not poisoned
if control and CONFIG["slow_control"]:  # 沒讀到毒值、只是回得慢(服務過載退避重試)
    import time; time.sleep(600)
if control and CONFIG["control_fails_quietly"]:  # 沒讀到毒值、因為別的原因失敗
    print(json.dumps({{"type": "result", "subtype": "error", "is_error": True, "num_turns": 1,
                      "result": "API Error: 529 overloaded"}})); sys.exit(1)
text = "ok"
claude_md = os.path.join(home, ".claude", "CLAUDE.md")
if "RTB-CANARY" in prompt:
    text = "NONE"
    unsuppressed = "--setting-sources" not in args and CONFIG["memory_readable"]
    if (CONFIG["memory_loaded"] or unsuppressed) and os.path.exists(claude_md):
        text = open(claude_md).read().strip()
mode = CONFIG["output"]
if mode in ("over_stop", "default_cap_error", "cap_error", "recovered", "continued",
            "continued_over", "continued_five", "continued_quiet") and "兩千字" not in prompt:
    mode = "cap"  # 這幾種只發生在要它寫長文的那一次
out = {{"cap": cap, "under": min(cap or 500, 5), "over": 500, "over_stop": 5000,
        "default_cap_error": 32000, "cap_error": cap, "recovered": cap, "continued": 4 * cap,
        "continued_over": 4 * cap + 1, "continued_five": 4 * cap,
        "continued_quiet": 4 * cap}}[mode] if cap else 20
if cap and mode == "recovered":
    turns = 2  # 撞頂後自動續寫了一次
if cap and mode.startswith("continued"):
    turns = 5 if mode == "continued_five" else 4  # 撞頂後自動續寫三次(四次請求)
result = {{"type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
          "result": text, "permission_denials": [], "total_cost_usd": 0.0001,
          "usage": {{"input_tokens": CONFIG["input_tokens"], "output_tokens": out,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}}
if cap and mode == "over_stop":
    result["stop_reason"] = "max_tokens"
if cap and mode in ("default_cap_error", "cap_error", "continued", "continued_over",
                    "continued_five"):
    limit = 32000 if mode == "default_cap_error" else cap
    result.update(is_error=True, subtype="error",
                  result=("API Error: Claude's response exceeded the "
                          f"{{limit}} output token maximum"))
    print(json.dumps(result)); sys.exit(1)
if CONFIG["hang_on_poison"] and poisoned:  # 讀到毒設定就一直重試連不到的位址、掛住不回
    import time; time.sleep(600)
if CONFIG["hang_suppressed"] and suppressed and os.path.exists(settings):  # 帶壓制參數也掛住
    import time; time.sleep(600)
if poisoned or CONFIG["api_error"]:
    result.update(is_error=True, subtype="error", result="API Error: 400 bad request",
                  usage={{"input_tokens": 0, "output_tokens": 0}})
    print(json.dumps(result)); sys.exit(1)
quiet = CONFIG["only_result"] and "ls /" in prompt  # 串流裡只有結果事件(看不到任何訊息)
silent = CONFIG["no_assistant"] and "ls /" in prompt  # 有初始事件與結果,沒有任何 assistant 事件
if flag("--output-format") == "stream-json" and not quiet:
    listed = [t for t in tools.split(",") if t]
    if CONFIG["init_tools_leak"] and "ls /" in prompt:
        listed = listed or ["Bash"]  # 參數說關掉了,初始事件卻列出工具
    print(json.dumps({{"type": "system", "subtype": "init", "tools": listed}}))
    if CONFIG["hook_events"]:
        print(json.dumps({{"type": "system", "subtype": "hook_started"}}))
    for _ in range(0 if silent else turns):
        print(json.dumps({{"type": "assistant", "message": {{"content": [
            {{"type": "text", "text": "..."}}]}}}}))
    if used_tool:
        prefix = CONFIG["block_prefix"]
        print(json.dumps({{"type": "assistant", "message": {{"content": [
            {{"type": prefix + "tool_use", "id": "t1", "name": "Bash",
              "input": {{"command": "ls /"}}}}]}}}}))
        print(json.dumps({{"type": "user", "message": {{"content": [
            {{"type": prefix + "tool_result", "tool_use_id": "t1", "content": "bin"}}]}}}}))
print(json.dumps(result))
"""
GOOD = {"login_with_token": False, "echo_token": False, "login_empty_home": True,
        "login_everywhere": True, "version": FAKE_VERSION,
        "contrast_changes": True, "tools_ignored": False, "output": "cap",
        "suppression_broken": False, "poison_readable": True, "hook_events": False,
        "memory_loaded": False, "memory_readable": True, "input_tokens": 3900,
        "api_error": False, "hang_on_poison": False, "hang_suppressed": False,
        "tool_use_one_turn": False, "continued_refusal": False, "long_refusal": False,
        "only_result": False, "init_tools_leak": False, "block_prefix": "",
        "url_log": None, "slow_control": False, "control_fails_quietly": False,
        "no_assistant": False, "main_peeks": False,
        "cap_log": None}


def _claude(tmp_path, **overrides):
    directory = tmp_path / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "claude"
    script.write_text(SCRIPT.format(python=sys.executable,
                                    config=repr({**GOOD, **overrides})), encoding="utf-8")
    script.chmod(0o755)
    return script


def _run(script):
    out = io.StringIO()
    code = modelverify.run([], out=out, environ={"PATH": str(script.parent),
                                                 "HOME": str(Path.home())})
    return code, out.getvalue()


def test_live_mode_needs_a_current_verification_record_writer(tmp_path):  # noqa: PLR0915
    """實測命令列:全過才寫紀錄,紀錄讓即時模式打得開;任一項沒過就不寫。"""
    script = _claude(tmp_path)
    code, text = _run(script)
    assert code == modelverify.EXIT_OK, text
    record = json.loads(cc.verification_path().read_text(encoding="utf-8"))
    assert record["claude_version"] == FAKE_VERSION and record["isolation"] == "empty_home"
    assert all(record["checks"][name] is True for name in cc.REQUIRED_CHECKS)
    assert record["notes"]["bad_argument_exit_code"] == 1
    assert "unknown option" in record["notes"]["bad_argument_stderr"]
    assert record["notes"]["fixed_input_tokens_seen"] == 3900
    # 實測每一次真的呼叫模型都記進花費帳(算進每月上限;參數不存在那次沒呼叫模型、不記)
    from rtb import modelledger_view as view

    reader = view.ModelLedgerView(mc.live_ledger_path())
    try:
        with reader.read_transaction():
            booked = reader.calls_between("0000", "9999")
    finally:
        reader.close()
    assert len(booked) == 9 and {r.caller for r in booked} == {"live_verification"}
    assert all(r.source == "live" and r.settled_nanousd is not None for r in booked)
    assert len({r.demo_id for r in booked}) == 1
    # 撞到的正是 32 的錯誤訊息,也是撞頂的正面證據
    cc.verification_path().unlink()
    code, text = _run(_claude(tmp_path / "cap_error", output="cap_error"))
    assert code == modelverify.EXIT_OK, text
    settings = mc.settings_from_env({mc.LIVE_ENV: "1"}, "demo-1", script)
    assert settings.mode is mc.Mode.LIVE
    for name, overrides, failing in (
            # 空暫存 HOME 登入不了:退回真 HOME;設定來源與記憶暗號在真 HOME 驗不了 → 不寫
            ("real_home", {"login_empty_home": False}, "setting_sources_suppress_user_settings"),
            ("no_login", {"login_everywhere": False}, "login_ok"),
            ("contrast", {"contrast_changes": False}, "tool_detection_contrast"),
            ("tools_ignored", {"tools_ignored": True}, "tools_disabled"),
            ("tool_use_one_turn", {"tool_use_one_turn": True}, "tools_disabled"),
            ("under_cap", {"output": "under"}, "output_limit_enforced"),
            ("over_cap", {"output": "over"}, "output_limit_enforced"),
            ("api_error", {"api_error": True}, "output_limit_enforced"),
            ("api_error_hooks", {"api_error": True}, "no_hook_events"),
            ("suppression_broken", {"suppression_broken": True},
             "setting_sources_suppress_user_settings"),
            ("poison_never_read", {"poison_readable": False},
             "setting_sources_suppress_user_settings"),
            ("hooks", {"hook_events": True}, "no_hook_events"),
            ("memory", {"memory_loaded": True}, "no_memory_or_claude_md"),
            ("fixed_input", {"input_tokens": core.CLAUDE_FIXED_INPUT_TOKENS + 1},
             "fixed_input_within_reserve"),
            # 代碼審第 2 輪:撞到的不是 32(預設上限)、停止原因對但輸出超過、撞頂後自動續寫
            ("stop_but_over", {"output": "over_stop"}, "output_limit_enforced"),
            ("default_cap_error", {"output": "default_cap_error"}, "output_limit_enforced"),
            ("recovered", {"output": "recovered"}, "output_limit_enforced"),
            # 記憶暗號的正面對照:拿掉參數時也讀不到暗號(放錯位置),這項驗不出東西
            ("memory_never_read", {"memory_readable": False}, "no_memory_or_claude_md"),
            # 量不到輸入用量(欄位缺或型別不對):不能當 0
            ("no_input_usage", {"input_tokens": None}, "fixed_input_within_reserve")):
        cc.verification_path().unlink(missing_ok=True)
        script = _claude(tmp_path / name, **overrides)
        code, text = _run(script)
        assert code == modelverify.EXIT_NOT_WRITTEN, (name, text)
        assert f"- {failing}:沒過" in text, (name, text)
        assert not cc.verification_path().exists(), name


# ---- 使用者 2026-09-25 裁定:空暫存 HOME 用長期權杖登入;輸出上限允許撞頂後續寫三次 ----
TOKEN = "sk-ant-oat01-test-long-lived-token-value"  # noqa: S105 - 測試用的假權杖


def _run_with(script, **extra):
    out = io.StringIO()
    code = modelverify.run([], out=out, environ={"PATH": str(script.parent),
                                                 "HOME": str(Path.home()), **extra})
    return code, out.getvalue()


def test_an_empty_home_logs_in_with_the_long_lived_token(tmp_path):
    """這台機器的訂閱登入在真 HOME:空暫存 HOME 要環境有 CLAUDE_CODE_OAUTH_TOKEN 才登入得了。有權杖時
    實測優先採用空暫存 HOME,記憶與設定來源兩項驗得了、全過;沒有權杖就退回真 HOME、那兩項驗不了。"""
    script = _claude(tmp_path, login_empty_home=False, login_with_token=True)
    code, text = _run_with(script, CLAUDE_CODE_OAUTH_TOKEN=TOKEN)
    assert code == modelverify.EXIT_OK, text
    record = json.loads(cc.verification_path().read_text(encoding="utf-8"))
    assert record["isolation"] == "empty_home"
    cc.verification_path().unlink()
    code, text = _run_with(_claude(tmp_path / "no_token", login_empty_home=False,
                                   login_with_token=True))
    assert code == modelverify.EXIT_NOT_WRITTEN
    assert "- no_memory_or_claude_md:沒過" in text


def test_the_token_is_passed_only_to_an_empty_home_child():
    """權杖只在空暫存 HOME 隔離時傳給 claude 子行程(連同原本的白名單);真 HOME 照舊不傳。"""
    source = {"PATH": "/bin", "HOME": "/h", "USER": "u", "LANG": "C",
              "CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "ANTHROPIC_API_KEY": "leak", "OTHER": "x"}
    empty = cc.ClaudeCodeBackend(Path("/bin/claude"), source, cc.Isolation.EMPTY_HOME)
    real = cc.ClaudeCodeBackend(Path("/bin/claude"), source, cc.Isolation.REAL_HOME)
    assert set(empty.child_env(10)) == {*cc.CHILD_ENV, cc.OUTPUT_LIMIT_ENV, cc.OAUTH_TOKEN_ENV}
    assert empty.child_env(10)[cc.OAUTH_TOKEN_ENV] == TOKEN
    assert set(real.child_env(10)) == {*cc.CHILD_ENV, cc.OUTPUT_LIMIT_ENV}
    assert cc.OAUTH_TOKEN_ENV == "CLAUDE_CODE_OAUTH_TOKEN"  # noqa: S105 - 變數名


def test_the_token_never_lands_in_records_ledgers_logs_or_errors(tmp_path, caplog):
    """claude 就算把權杖印到標準錯誤:啟用紀錄(含參數不存在那次的標準錯誤轉存)、花費帳、本機日誌、
    錄製檔與錯誤訊息都不含權杖字串。"""
    import logging

    script = _claude(tmp_path, login_empty_home=False, login_with_token=True, echo_token=True)
    caplog.set_level(logging.DEBUG)
    code, text = _run_with(script, CLAUDE_CODE_OAUTH_TOKEN=TOKEN)
    assert code == modelverify.EXIT_OK, text
    assert TOKEN not in text
    assert TOKEN.encode() not in cc.verification_path().read_bytes()
    ledger = mc.live_ledger_path()
    for suffix in ("", "-wal"):
        path = Path(f"{ledger}{suffix}")
        if path.exists():
            assert TOKEN.encode() not in path.read_bytes()
    # 即時加錄製一次、再讓 claude 以讀不成 JSON 的輸出失敗一次
    environ = {mc.LIVE_ENV: "1", mc.RECORD_ENV: "1", "PATH": str(script.parent),
               "HOME": str(Path.home()), "CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    settings = mc.settings_from_env(environ, "demo-1", script)
    assert settings.mode is mc.Mode.LIVE
    folder = tmp_path / "recordings"
    request = mc.ModelRequest(caller=mc.Caller.EVAL_CANDIDATE, system="s", user="hello",
                              max_output_tokens=50, timeout_seconds=30, demo_id="demo-1",
                              batch_id="b1")
    mc.call_model(request, settings, recordings_dir=folder, ledger=tmp_path / "l.sqlite")
    assert all(TOKEN.encode() not in p.read_bytes() for p in folder.iterdir())
    broken = cc.ClaudeCodeBackend(tmp_path / "broken", environ, cc.Isolation.EMPTY_HOME)
    (tmp_path / "broken").write_text(
        "#!/bin/sh\necho token=$CLAUDE_CODE_OAUTH_TOKEN >&2\necho not-json\nexit 3\n",
        encoding="utf-8")
    (tmp_path / "broken").chmod(0o755)
    code, stdout, stderr = cc.run_claude([str(tmp_path / "broken")], "", broken.child_env(5),
                                         10, isolated_home=True)
    assert TOKEN.encode() not in stdout + stderr
    try:
        cc.judge_output(code, stdout, stderr)
    except core.ModelCallFailed as failed:
        assert TOKEN not in str(failed)
    assert TOKEN not in caplog.text


def test_the_output_limit_allows_three_continuations_within_four_times_the_cap(tmp_path):
    """撞頂後自動續寫三次(四次請求)、總輸出剛好四倍上限、有「超過輸出上限」的錯誤:算限住。多一個
    token、續寫第四次、或沒有撞頂證據,都算沒過。"""
    assert modelverify.OUTPUT_ALLOWANCE == 4 == 1 + core.OUTPUT_RECOVERY_ATTEMPTS
    code, text = _run(_claude(tmp_path / "ok", output="continued"))
    assert code == modelverify.EXIT_OK, text
    for name in ("continued_over", "continued_five", "continued_quiet"):
        cc.verification_path().unlink(missing_ok=True)
        code, text = _run(_claude(tmp_path / name, output=name))
        assert code == modelverify.EXIT_NOT_WRITTEN, (name, text)
        assert "- output_limit_enforced:沒過" in text, (name, text)


def test_the_reservation_pays_for_four_times_the_output_cap():
    """預留(與傳給 Claude Code 的單次花費上限)的輸出部分照上限的四倍算:輸出上限每多 1 個 token,原價
    至少多四份輸出單價。"""
    def budget(tokens):
        request = mc.ModelRequest(caller=mc.Caller.EVAL_CANDIDATE, system="s", user="u",
                                  max_output_tokens=tokens, timeout_seconds=30)
        return core.call_budget_nanousd(request, core.DEFAULT_MODEL)

    price = core.PRICES[core.DEFAULT_MODEL]
    assert budget(101) - budget(100) >= 4 * price.output_nanousd


# ---- 協調者 2026-09-25 真實測:設定來源對照組讀到連不到的位址,claude 一直重試到 120 秒逾時 ----
def _timeouts_by_call():
    with sqlite3.connect(mc.live_ledger_path()) as db:
        return db.execute("select r.timeout_seconds, s.outcome from model_reservations r join "
                          "model_settlements s on s.reservation_id = r.id order by r.id").fetchall()


def test_a_hanging_settings_control_counts_as_the_expected_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(modelverify, "CONTROL_TIMEOUT_SECONDS", 1.0)
    script = _claude(tmp_path, hang_on_poison=True)
    started = time.monotonic()
    code, text = _run(script)
    assert code == modelverify.EXIT_OK, text
    assert "- setting_sources_suppress_user_settings:過" in text
    assert time.monotonic() - started < 60  # 對照組用短逾時,不是主呼叫的 120 秒
    record = json.loads(cc.verification_path().read_text(encoding="utf-8"))
    # 代碼審 r2 m4:過的依據是本機監聽埠收到連線(毒值讀到),不是逾時本身
    assert record["notes"]["settings_control"] == "connected"
    assert (modelverify.CONTROL_TIMEOUT_SECONDS, "timeout") in _timeouts_by_call()


def test_a_hanging_suppressed_settings_call_fails_the_check_without_aborting(tmp_path,
                                                                             monkeypatch):
    monkeypatch.setattr(modelverify, "TIMEOUT_SECONDS", 1.0)
    script = _claude(tmp_path, hang_suppressed=True)
    code, text = _run(script)
    assert code == modelverify.EXIT_NOT_WRITTEN, text
    assert "實測沒跑完" not in text  # 整個實測沒有中斷:每一項都照實印出
    assert "- setting_sources_suppress_user_settings:沒過" in text
    for name in cc.REQUIRED_CHECKS:
        if name != "setting_sources_suppress_user_settings":
            assert f"- {name}:過" in text, (name, text)
    assert not cc.verification_path().exists()
    assert (1.0, "timeout") in _timeouts_by_call()


def test_the_settings_control_timeout_is_shorter_than_the_main_call():
    assert modelverify.CONTROL_TIMEOUT_SECONDS == 30.0
    assert modelverify.CONTROL_TIMEOUT_SECONDS < modelverify.TIMEOUT_SECONDS == 120.0


# ---- 協調者 2026-09-25 真實測:工具那項的中文拒絕撞頂續寫一輪,被「對話輪數 1」誤判成用了工具 ----
def _checks_of(text):
    return {name: f"- {name}:過" in text for name in cc.REQUIRED_CHECKS}


def test_a_refusal_that_continues_after_the_cap_is_not_tool_use(tmp_path):
    code, text = _run(_claude(tmp_path, continued_refusal=True))
    assert code == modelverify.EXIT_OK, text
    assert _checks_of(text)["tools_disabled"]


def test_the_tool_check_leaves_room_for_a_long_refusal(tmp_path):
    assert modelverify.TOOL_CHECK_OUTPUT_TOKENS == 1024
    assert modelverify.TOOL_CHECK_OUTPUT_TOKENS > 307  # 協調者實測的中文拒絕
    caps = tmp_path / "caps.txt"
    code, text = _run(_claude(tmp_path, long_refusal=True, cap_log=str(caps)))
    assert code == modelverify.EXIT_OK, text
    assert caps.read_text().split() == ["1024", "1024"]  # 工具關掉與對照組都用這個上限


def test_a_tool_use_event_fails_the_tool_check_even_in_one_turn(tmp_path):
    code, text = _run(_claude(tmp_path, tool_use_one_turn=True))
    assert code == modelverify.EXIT_NOT_WRITTEN, text
    checks = _checks_of(text)
    assert not checks["tools_disabled"]
    assert checks["tool_detection_contrast"]  # 開 Bash 的對照組照樣判得出用了工具


# ---- 增量 4 代碼審 r2 m3:工具關掉要有正面證據;區塊類型看結尾 ----
def test_the_tool_check_needs_positive_evidence(tmp_path):
    """串流裡只有結果事件(看不到任何訊息)、或初始事件列出的工具清單不是空的,工具關掉那項判沒過
    (驗不了就算沒過)。"""
    for name, overrides in (("only_result", {"only_result": True, "continued_refusal": True}),
                            ("init_tools_leak", {"init_tools_leak": True})):
        code, text = _run(_claude(tmp_path / name, **overrides))
        assert code == modelverify.EXIT_NOT_WRITTEN, (name, text)
        assert not _checks_of(text)["tools_disabled"], (name, text)


def test_prefixed_tool_blocks_count_as_tool_use(tmp_path):
    """區塊類型以 tool_use/tool_result 結尾就算工具(例如 mcp_tool_use、mcp_tool_result):關掉那項
    判沒過,開 Bash 的對照組照樣判得出。"""
    code, text = _run(_claude(tmp_path, tool_use_one_turn=True, block_prefix="mcp_"))
    assert code == modelverify.EXIT_NOT_WRITTEN, text
    checks = _checks_of(text)
    assert not checks["tools_disabled"] and checks["tool_detection_contrast"], text


# ---- 增量 4 代碼審 r2 m4:設定來源對照組要有正面證據(本機監聽埠收到連線) ----
def test_the_poison_points_at_a_listener_of_our_own(tmp_path):
    """毒值 ANTHROPIC_BASE_URL 指向實測命令列自己開的監聽埠(只綁 127.0.0.1、隨機埠);對照組連上
    就證明毒值讀到,這項過。"""
    urls = tmp_path / "urls.txt"
    code, text = _run(_claude(tmp_path, url_log=str(urls)))
    assert code == modelverify.EXIT_OK, text
    [url] = urls.read_text().split()
    host, port = url.removeprefix("http://").rstrip("/").split(":")
    assert host == "127.0.0.1" and int(port) not in (0, 9)
    record = json.loads(cc.verification_path().read_text(encoding="utf-8"))
    assert record["notes"]["settings_control"] == "connected"


def test_a_control_without_a_connection_proves_nothing(tmp_path, monkeypatch):
    """對照組沒連到監聽埠:回得慢而逾時、或因為別的原因失敗,都判不出毒值有沒有讀到——這項沒過
    (以前逾時或失敗就算對照失敗、判過)。"""
    monkeypatch.setattr(modelverify, "CONTROL_TIMEOUT_SECONDS", 1.0)
    for name, overrides in (
            ("slow", {"poison_readable": False, "slow_control": True}),
            ("quiet_failure", {"poison_readable": False, "control_fails_quietly": True})):
        cc.verification_path().unlink(missing_ok=True)
        code, text = _run(_claude(tmp_path / name, **overrides))
        assert code == modelverify.EXIT_NOT_WRITTEN, (name, text)
        assert "- setting_sources_suppress_user_settings:沒過" in text, (name, text)
        assert "實測沒跑完" not in text, name


# ---- 增量 4 代碼審 r3 s1/v3:監聽埠只認 API 請求行;正面證據與監聽埠的收尾各自守住 ----
def test_a_stranger_connection_is_not_proof_the_poison_was_read(tmp_path, monkeypatch):
    """對照組期間別的行程連進監聽埠(不送、或送的不是 API 請求行):不算毒值讀到,這項沒過。"""
    monkeypatch.setattr(modelverify, "CONTROL_TIMEOUT_SECONDS", 1.0)
    real = modelverify._poison_listener
    for name, payload in (("silent", b""), ("junk", b"hello there\r\n"),
                          ("get_root", b"GET / HTTP/1.1\r\n")):

        @contextlib.contextmanager
        def meddled(payload=payload):
            with real() as (port, hit):
                def poke():
                    time.sleep(0.3)
                    with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
                        if payload:
                            conn.sendall(payload)
                        time.sleep(0.6)
                threading.Thread(target=poke, daemon=True).start()
                yield port, hit

        monkeypatch.setattr(modelverify, "_poison_listener", meddled)
        cc.verification_path().unlink(missing_ok=True)
        code, text = _run(_claude(tmp_path / name, poison_readable=False, slow_control=True))
        assert code == modelverify.EXIT_NOT_WRITTEN, (name, text)
        assert "- setting_sources_suppress_user_settings:沒過" in text, (name, text)


def test_the_listener_flags_only_an_api_request_line_and_closes_on_exit():
    """監聽埠:送 API 請求行(POST /v1/…)才設旗標;離開後埠關掉(再連被拒)、背景執行緒停下;
    with 裡丟例外也一樣收乾淨。"""
    with modelverify._poison_listener() as (port, hit):
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(b"hello\r\n")
        time.sleep(0.8)
        assert not hit.is_set()
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(b"POST /v1/messages?beta=true HTTP/1.1\r\n")
        deadline = time.monotonic() + 3
        while not hit.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert hit.is_set()
    for _ in range(2):
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        assert not [t for t in threading.enumerate() if t.name == "rtb-poison-listener"]
        with pytest.raises(RuntimeError), modelverify._poison_listener() as (port, _hit):
            raise RuntimeError("boom")


def test_the_tool_check_needs_an_assistant_event_even_with_an_empty_tool_list(tmp_path):
    """初始事件的工具清單是空的、結果也成功,但串流裡一個 assistant 事件都沒有:看不到訊息就驗不了,
    工具關掉那項判沒過(正面證據的兩半各自要成立)。"""
    code, text = _run(_claude(tmp_path, no_assistant=True))
    assert code == modelverify.EXIT_NOT_WRITTEN, text
    assert "- tools_disabled:沒過" in text, text


def test_the_main_call_reading_the_poison_fails_the_settings_check(tmp_path):
    """帶壓制參數的主呼叫成功了,但監聽埠在主呼叫期間收到 API 請求:毒值沒壓住,這項沒過,
    附註記 poison_read,不再跑對照組。"""
    urls = tmp_path / "urls.txt"
    script = _claude(tmp_path, main_peeks=True, url_log=str(urls))
    environ = {"PATH": str(script.parent), "HOME": str(Path.home())}
    checker = modelverify.Checker(script, environ, core.DEFAULT_MODEL)
    assert checker.login() is cc.Isolation.EMPTY_HOME
    assert checker.settings_suppressed() is False
    assert checker.notes["settings_main"] == "poison_read"
    assert "settings_control" not in checker.notes
    assert len(urls.read_text().split()) == 1  # 只有主呼叫連過,對照組沒跑
