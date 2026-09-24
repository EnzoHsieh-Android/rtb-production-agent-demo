"""Phase 11B 增量 1:即時模式的實測命令列(寫啟用紀錄的那支,[S942] 的另一半)。

真的實測由協調者在本機跑;這裡用一支會照參數與環境變化的假 claude(Python 腳本),只驗逐項判定與
「全過才寫紀錄」。不呼叫真的 claude。
"""

import io
import json
import sys
from pathlib import Path

from rtb import modelclient as mc
from rtb import modelverify
from tests.model.fakes import FAKE_VERSION

SCRIPT = """#!{python}
import json, os, sys
CONFIG = {config}
args = sys.argv[1:]
home = os.environ.get("HOME", "")
if args[:2] == ["auth", "status"]:
    empty = "rtb-claude-" in home
    print(json.dumps({{"loggedIn": CONFIG["login_empty_home"] if empty else True}}))
    sys.exit(0)
if args == ["--version"]:
    print(CONFIG["version"]); sys.exit(0)
if "--rtb-no-such-flag" in args:
    print("error: unknown option '--rtb-no-such-flag'", file=sys.stderr); sys.exit(1)
sys.stdin.read()
cap = int(os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "0") or 0)
tools = args[args.index("--tools") + 1] if "--tools" in args else ""
turns = 3 if tools and CONFIG["contrast_changes"] else 1
poisoned = os.path.exists(os.path.join(home, ".claude", "settings.json"))
out = 500 if not CONFIG["output_capped"] else min(cap or 500, 20)
result = {{"type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
          "result": "ok", "permission_denials": [], "total_cost_usd": 0.0001,
          "usage": {{"input_tokens": 3900, "output_tokens": out,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}}
if poisoned and CONFIG["poison_breaks"]:
    result.update(is_error=True, subtype="error", result="model not found")
    print(json.dumps(result)); sys.exit(1)
if args[args.index("--output-format") + 1] == "stream-json":
    print(json.dumps({{"type": "system", "subtype": "init"}}))
    if CONFIG["hook_events"]:
        print(json.dumps({{"type": "system", "subtype": "hook_started"}}))
    if CONFIG["memory"]:
        print(json.dumps({{"type": "system", "subtype": "note", "text": "loaded CLAUDE.md"}}))
print(json.dumps(result))
"""
GOOD = {"login_empty_home": True, "version": FAKE_VERSION, "contrast_changes": True,
        "output_capped": True, "poison_breaks": False, "hook_events": False, "memory": False}


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
    record = json.loads(mc.verification_path().read_text(encoding="utf-8"))
    assert record["claude_version"] == FAKE_VERSION and record["isolation"] == "empty_home"
    assert all(record["checks"][name] is True for name in mc.REQUIRED_CHECKS)
    assert record["notes"]["bad_argument_exit_code"] == 1
    assert "unknown option" in record["notes"]["bad_argument_stderr"]
    assert record["notes"]["fixed_input_tokens_seen"] == 3900
    settings = mc.settings_from_env({mc.LIVE_ENV: "1"}, "demo-1", script)
    assert settings.mode is mc.Mode.LIVE
    # 空暫存 HOME 登入不了:退回真 HOME;但設定來源那一項在真 HOME 驗不了 → 不寫
    for name, overrides, failing in (
            ("real_home", {"login_empty_home": False}, "setting_sources_suppress_user_settings"),
            ("tools", {"contrast_changes": False}, "tool_detection_contrast"),
            ("output", {"output_capped": False}, "output_limit_enforced"),
            ("poison", {"poison_breaks": True}, "setting_sources_suppress_user_settings"),
            ("hooks", {"hook_events": True}, "no_hook_events"),
            ("memory", {"memory": True}, "no_memory_or_claude_md")):
        mc.verification_path().unlink(missing_ok=True)
        script = _claude(tmp_path / name, **overrides)
        code, text = _run(script)
        assert code == modelverify.EXIT_NOT_WRITTEN, name
        assert f"- {failing}:沒過" in text, (name, text)
        assert not mc.verification_path().exists(), name
