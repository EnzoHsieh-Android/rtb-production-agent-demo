"""Phase 11B 增量 1:即時模式的實測命令列(寫啟用紀錄的那支,[S942] 的另一半)。

真的實測由協調者在本機跑;這裡用一支會照參數與環境變化的假 claude(Python 腳本),只驗逐項判定與
「全過才寫紀錄」。代碼審第 1 輪補強:固定附加輸入超過預留常數不寫、記憶用不可混淆的暗號驗、設定來源
對照用命令列蓋不掉的毒值並加正面對照、輸出上限要有撞頂的正面證據、錯誤回應一律判沒過、工具與登入的反例。
不呼叫真的 claude。
"""

import io
import json
import sys
from pathlib import Path

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
if args[:2] == ["auth", "status"]:
    empty = "rtb-claude-" in home
    ok = CONFIG["login_everywhere"] and (CONFIG["login_empty_home"] or not empty)
    print(json.dumps({{"loggedIn": ok}})); sys.exit(0)
if args == ["--version"]:
    print(CONFIG["version"]); sys.exit(0)
if "--rtb-no-such-flag" in args:
    print("error: unknown option '--rtb-no-such-flag'", file=sys.stderr); sys.exit(1)
prompt = sys.stdin.read()
cap = int(os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "0") or 0)
tools = flag("--tools") or ""
turns = 3 if (tools and CONFIG["contrast_changes"]) or CONFIG["tools_ignored"] else 1
settings = os.path.join(home, ".claude", "settings.json")
suppressed = flag("--setting-sources") == "" and not CONFIG["suppression_broken"]
poisoned = os.path.exists(settings) and not suppressed and CONFIG["poison_readable"]
text = "ok"
claude_md = os.path.join(home, ".claude", "CLAUDE.md")
if "RTB-CANARY" in prompt:
    text = "NONE"
    if CONFIG["memory_loaded"] and os.path.exists(claude_md):
        text = open(claude_md).read().strip()
out = {{"cap": cap, "under": min(cap or 500, 5), "over": 500}}[CONFIG["output"]] if cap else 20
result = {{"type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
          "result": text, "permission_denials": [], "total_cost_usd": 0.0001,
          "usage": {{"input_tokens": CONFIG["input_tokens"], "output_tokens": out,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}}
if poisoned or CONFIG["api_error"]:
    result.update(is_error=True, subtype="error", result="API Error: 400 bad request",
                  usage={{"input_tokens": 0, "output_tokens": 0}})
    print(json.dumps(result)); sys.exit(1)
if flag("--output-format") == "stream-json":
    print(json.dumps({{"type": "system", "subtype": "init"}}))
    if CONFIG["hook_events"]:
        print(json.dumps({{"type": "system", "subtype": "hook_started"}}))
print(json.dumps(result))
"""
GOOD = {"login_empty_home": True, "login_everywhere": True, "version": FAKE_VERSION,
        "contrast_changes": True, "tools_ignored": False, "output": "cap",
        "suppression_broken": False, "poison_readable": True, "hook_events": False,
        "memory_loaded": False, "input_tokens": 3900, "api_error": False}


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


def test_live_mode_needs_a_current_verification_record_writer(tmp_path):
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
    settings = mc.settings_from_env({mc.LIVE_ENV: "1"}, "demo-1", script)
    assert settings.mode is mc.Mode.LIVE
    for name, overrides, failing in (
            # 空暫存 HOME 登入不了:退回真 HOME;設定來源與記憶暗號在真 HOME 驗不了 → 不寫
            ("real_home", {"login_empty_home": False}, "setting_sources_suppress_user_settings"),
            ("no_login", {"login_everywhere": False}, "login_ok"),
            ("contrast", {"contrast_changes": False}, "tool_detection_contrast"),
            ("tools_ignored", {"tools_ignored": True}, "tools_disabled"),
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
             "fixed_input_within_reserve")):
        cc.verification_path().unlink(missing_ok=True)
        script = _claude(tmp_path / name, **overrides)
        code, text = _run(script)
        assert code == modelverify.EXIT_NOT_WRITTEN, (name, text)
        assert f"- {failing}:沒過" in text, (name, text)
        assert not cc.verification_path().exists(), name
