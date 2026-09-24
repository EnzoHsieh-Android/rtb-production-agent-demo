"""Phase 11B 增量 1:Claude Code 後端(計劃第 7 版〈模型用戶端〉)。

合約 [S904]、[S905]、[S906]、[S936]、[S939]、[S940]。一律用假的 claude 腳本(/bin/sh,印固定 JSON),
絕對路徑直接傳給模型用戶端;不呼叫真的 claude、不發網路請求。
"""

import contextlib
import os
import signal
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelledger_view as view
from tests.model.fakes import claude_json, fake_claude, invocations, live, request

WHITELIST = {"PATH", "HOME", "USER", "LANG", "CLAUDE_CODE_MAX_OUTPUT_TOKENS"}
SHELL_ADDED = {"PWD", "SHLVL", "_", "OLDPWD"}  # /bin/sh 自己補的,不是模型用戶端傳的


def _rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()


def _call(tmp_path, script, req=None, name="x", isolation=cc.Isolation.EMPTY_HOME):
    recordings = tmp_path / f"{name}-rec"
    recordings.mkdir(exist_ok=True)
    settings = live(cc.ClaudeCodeBackend(script, isolation=isolation))
    return mc.call_model(req or request(), settings, recordings_dir=recordings,
                         ledger=tmp_path / f"{name}.sqlite")


# ---- [S904] ----
QUOTA = "Claude AI usage limit reached|1759000000"
# 認得的錯誤子類型只收有真實樣本的(KNOWN_ERRORS 在增量 1 是空的,等協調者錄製時補)。
# 這裡注入「假設取到了」的樣本,驗分類機制本身;沒注入時一律落到無法可靠分類
SAMPLES = (
    cc.ErrorSample("result", "usage limit reached", mc.Outcome.QUOTA_EXHAUSTED, "quota"),
    cc.ErrorSample("subtype", "error_max_budget_usd", mc.Outcome.OVERRUN, "call_budget"),
    cc.ErrorSample("result", "not logged in", mc.Outcome.CONFIG_ERROR, "not_logged_in"),
    cc.ErrorSample("result", "not found", mc.Outcome.CONFIG_ERROR, "unknown_model"),
    cc.ErrorSample("result", "overloaded", mc.Outcome.TRANSIENT, "overloaded"),
)
CASES = {  # 名稱: (假 claude 的設定, 例外類別, 結果類別, 照預留結算?)
    "missing_exe": ({"missing": True}, mc.ConfigError, "config_error", False),
    "not_executable": ({"executable": False}, mc.ConfigError, "config_error", False),
    "not_logged_in": ({"logged_in": False}, mc.ConfigError, "config_error", False),
    "not_json": ({"output": "Error: something"}, mc.UnreadableModelResponse, "unreadable", True),
    "missing_head": ({"output": {"type": "result", "is_error": False}},
                     mc.UnreadableModelResponse, "unreadable", True),
    # 第 2 步讀不成 JSON:結束代碼非 0 → 暫時性、無法可靠分類(例如參數改名、在解析參數就退出);
    # 結束代碼 0 才是讀不懂
    "not_json_nonzero": ({"output": "boom", "code": 1}, mc.TransientServiceError,
                         "transient", True),
    # 第 3 步先於第 4 步:錯誤回應就算對話輪數 0、缺用量,也照子類型歸類
    "error_without_usage": ({"output": claude_json(QUOTA, is_error=True, num_turns=0,
                                                   usage=False)},
                            mc.QuotaExhausted, "quota_exhausted", True),
    "error_with_tool_turns": ({"output": claude_json(QUOTA, is_error=True, num_turns=2)},
                              mc.QuotaExhausted, "quota_exhausted", True),
    "login_error": ({"output": claude_json("Not logged in · Please run /login", is_error=True),
                     "code": 1}, mc.ConfigError, "config_error", False),
    "unknown_model": ({"output": claude_json("model: claude-nope not found", is_error=True),
                       "code": 1}, mc.ConfigError, "config_error", False),
    "recognised_transient": ({"output": claude_json("overloaded", is_error=True), "code": 1},
                             mc.TransientServiceError, "transient", True),
    "unrecognised": ({"output": claude_json("???", is_error=True, subtype="whatever")},
                     mc.TransientServiceError, "transient", True),
    "nonzero_exit": ({"output": claude_json(), "code": 3}, mc.TransientServiceError,
                     "transient", True),
    "tool_turns": ({"output": claude_json(num_turns=2)}, mc.UnreadableModelResponse,
                   "unreadable", True),
    "timeout": ({"sleep": 5}, mc.ModelTimeout, "timeout", True),
}
UNCLASSIFIED = {"unrecognised", "nonzero_exit", "not_json_nonzero"}
TOOL_USE = {"error_with_tool_turns"}  # 錯誤回應分完子類型,帶工具使用痕跡另加標記


def _script(tmp_path, name, options):
    options = dict(options)
    if options.pop("missing", False):
        return tmp_path / name / "no-such-claude"
    return fake_claude(tmp_path / name, **options)


def test_failed_calls_are_settled_by_their_kind(  # noqa: PLR0915 - 五步判定的逐條情境
        tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "KNOWN_ERRORS", SAMPLES)
    req = request(max_output_tokens=200, timeout_seconds=1.0)
    reserved = core.reservation_nanousd(req, mc.DEFAULT_MODEL)
    for name, (options, error, outcome, charged) in CASES.items():
        script = _script(tmp_path, name, options)
        with pytest.raises(error) as failed:
            _call(tmp_path, script, req, name)
        assert failed.value.outcome.value == outcome, name
        assert failed.value.unclassified is (name in UNCLASSIFIED), name
        assert failed.value.tool_use is (name in TOOL_USE), name
        [row] = _rows(tmp_path / f"{name}.sqlite")
        assert row.outcome == outcome, name
        assert row.effective_nanousd == (reserved if charged else 0), name
        assert failed.value.settlement is mc.SettlementState.SETTLED, name
    assert "stderr" in _rows(tmp_path / "not_json_nonzero.sqlite")[0].sub_reason
    # 沒登入:呼叫前的本地檢查就擋下,模型那一條指令根本沒跑
    assert invocations(tmp_path / "not_logged_in" / "claude") == []
    # 沒有真實樣本時(KNOWN_ERRORS 是空的),額度用完這類也認不出:暫時性、無法可靠分類
    monkeypatch.setattr(cc, "KNOWN_ERRORS", ())
    assert cc.KNOWN_ERRORS == ()
    with pytest.raises(mc.TransientServiceError) as failed:
        _call(tmp_path, fake_claude(tmp_path / "raw", claude_json(QUOTA, is_error=True)), req,
              "raw")
    assert failed.value.unclassified
    # 失敗但讀得出用量:取「預留」與「原價乘 1.2」較高者
    monkeypatch.setattr(cc, "KNOWN_ERRORS", SAMPLES)
    heavy = claude_json(QUOTA, is_error=True, input_tokens=1000, output_tokens=90_000)
    with pytest.raises(mc.QuotaExhausted) as failed:
        _call(tmp_path, fake_claude(tmp_path / "heavy", heavy), req, "heavy")
    [row] = _rows(tmp_path / "heavy.sqlite")
    listed = 1000 * 2_000 + 90_000 * 10_000
    assert row.effective_nanousd == listed * 6 // 5 > reserved and row.overrun
    assert failed.value.settlement is mc.SettlementState.OVERRUN
    # 成功:原價與含係數兩欄
    script = fake_claude(tmp_path / "ok", claude_json(input_tokens=1000, output_tokens=100,
                                                      cost_usd=0.0))
    good = _call(tmp_path, script, req, "ok")
    [row] = _rows(tmp_path / "ok.sqlite")
    listed = 1000 * 2_000 + 100 * 10_000
    assert good.text == '{"verdict": "not_worth"}'
    assert row.list_nanousd == listed and row.settled_nanousd == listed * 6 // 5
    assert good.settlement is mc.SettlementState.SETTLED
    # 結算寫不進去:照常回文字,那筆留在未結算、照預留算
    monkeypatch.setattr(ledger_db, "settle", _busy)
    busy = _call(tmp_path, script, req, "busy")
    [row] = _rows(tmp_path / "busy.sqlite")
    assert row.outcome is None and row.effective_nanousd == reserved
    assert busy.text and busy.settlement is mc.SettlementState.UNSETTLED


def _busy(*_args, **_kwargs):
    from rtb.sqlitekit import DatabaseBusy

    raise DatabaseBusy("locked")


# ---- [S905] ----
def test_the_claude_subprocess_gets_only_whitelisted_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RTB_DSP_AUDIT_KEY", "secret-audit-token-0123456789")
    monkeypatch.setenv("RTB_CAPABILITY_KEY", "secret-signing-key-0123456789")
    monkeypatch.setenv("LANG", "zh_TW.UTF-8")
    monkeypatch.setenv("USER", "someone")
    script = fake_claude(tmp_path / "c")
    _call(tmp_path, script, request(max_output_tokens=77))  # 預設隔離:空暫存 HOME
    _call(tmp_path, script, request(max_output_tokens=77), "real",
          cc.Isolation.REAL_HOME)  # 退路隔離:真 HOME
    empty, real = invocations(script)
    for seen in (empty, real):
        env = seen["env"]
        assert set(env) - SHELL_ADDED <= WHITELIST, set(env) - SHELL_ADDED - WHITELIST
        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "77"
        assert env["LANG"] == "zh_TW.UTF-8" and env["USER"] == "someone"
        assert "secret" not in "".join(env.values())
    assert real["env"]["HOME"] == os.environ["HOME"]
    fresh = Path(empty["env"]["HOME"])
    assert fresh != Path(os.environ["HOME"]) and fresh.parent.name.startswith("rtb-claude-")
    assert not fresh.exists()  # 每次新建、用完就刪


# ---- [S906] ----
REQUIRED_FLAGS = ("-p", "--strict-mcp-config", "--disable-slash-commands", "--safe-mode",
                  "--no-session-persistence", "--setting-sources", "--settings")
FORBIDDEN_FLAGS = ("--bare", "--dangerously-skip-permissions", "--allow-dangerously-skip-"
                   "permissions", "--allowedTools", "--allowed-tools", "--mcp-config",
                   "--add-dir", "--append-system-prompt", "--continue", "--resume")


def _value(args, flag):
    return args[args.index(flag) + 1]


def test_the_claude_command_disables_every_tool(tmp_path):
    script = fake_claude(tmp_path / "c")
    req = request("使用者內容:七個欄位", max_output_tokens=40)
    _call(tmp_path, script, req)
    [seen] = invocations(script)
    args = seen["args"]
    for flag in REQUIRED_FLAGS:
        assert flag in args, flag
    assert not [a for a in args if a in FORBIDDEN_FLAGS]
    assert _value(args, "--output-format") == "json"
    assert _value(args, "--model") == mc.DEFAULT_MODEL
    assert _value(args, "--effort") == cc.CLAUDE_EFFORT == "low"
    assert _value(args, "--system-prompt") == req.system
    assert _value(args, "--tools") == ""
    assert _value(args, "--setting-sources") == cc.SETTING_SOURCES == ""  # 最少的設定來源
    assert _value(args, "--settings") == "{}"  # 空設定
    budget = Decimal(_value(args, "--max-budget-usd")) * mc.NANOUSD_PER_USD
    assert budget == core.call_budget_nanousd(req, mc.DEFAULT_MODEL)
    # 預留的原價:(提示位元組數 + Claude Code 固定附加 4000)乘三種輸入單價最高者
    # (1 小時快取寫入 4 美元)加輸出上限乘輸出單價(變異檢查補的斷言:不能只跟同一支函式比)
    prompt_bytes = len((req.system + req.user).encode("utf-8"))
    assert core.CLAUDE_FIXED_INPUT_TOKENS == 4000
    assert budget == (prompt_bytes + 4000) * 4_000 + 40 * 10_000
    assert core.call_budget_nanousd(req, mc.DEFAULT_MODEL) * 6 == pytest.approx(
        core.reservation_nanousd(req, mc.DEFAULT_MODEL) * 5, abs=6)
    assert seen["stdin"] == req.user and req.user not in args  # 使用者內容只從標準輸入送
    assert seen["cwd_listing"] == []  # 每次新建的空暫存目錄
    assert Path(seen["cwd"]).parent.name.startswith("rtb-claude-")
    assert not Path(seen["cwd"]).exists()
    _call(tmp_path, script, req, "again")
    cwds = {Path(s["cwd"]) for s in invocations(script)}
    assert len(cwds) == 2  # 每次一個新的


# ---- [S936] ----
def test_any_sign_of_tool_use_is_unreadable(tmp_path):
    req = request(max_output_tokens=100)
    reserved = core.reservation_nanousd(req, mc.DEFAULT_MODEL)
    for name, output in (("turns", claude_json(num_turns=2)),
                         ("denied", claude_json(denials=[{"tool_name": "Bash"}])),
                         ("zero_turns", claude_json(num_turns=0))):
        script = fake_claude(tmp_path / name, output)
        with pytest.raises(mc.UnreadableModelResponse):
            _call(tmp_path, script, req, name)
        [row] = _rows(tmp_path / f"{name}.sqlite")
        assert (row.outcome, row.sub_reason, row.effective_nanousd) == (
            "unreadable", "tool_use", reserved), name


# ---- [S939] ----
def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_timeout_kills_the_whole_group_before_cleanup(tmp_path):
    before = set(Path(tempfile.gettempdir()).glob("rtb-claude-*"))
    script = fake_claude(tmp_path / "slow", sleep=30, grandchild=True)
    started = time.monotonic()
    with pytest.raises(mc.ModelTimeout):
        _call(tmp_path, script, request(timeout_seconds=1.0), "slow")
    assert time.monotonic() - started < 15
    [seen] = invocations(script)
    pid = int((tmp_path / "slow" / "grandchild.pid").read_text(encoding="utf-8"))
    assert not _alive(pid)  # 孫行程也被殺了
    assert not Path(seen["cwd"]).parent.exists()  # 殺整組、確認群組空,再刪暫存目錄
    # 孫行程繼承了輸出、還在跑:成功的呼叫不被拖成逾時,孫行程照樣被殺
    script = fake_claude(tmp_path / "lingering", grandchild=True)
    started = time.monotonic()
    assert _call(tmp_path, script, request(timeout_seconds=5.0), "lingering").text
    assert time.monotonic() - started < 4
    pid = int((tmp_path / "lingering" / "grandchild.pid").read_text(encoding="utf-8"))
    assert not _alive(pid)
    for name, options in (("fine", {}), ("failing", {"output": claude_json(), "code": 2}),
                          ("missing", {"executable": False})):
        script = fake_claude(tmp_path / name, **options)
        with contextlib.suppress(mc.TransientServiceError, mc.ConfigError):
            _call(tmp_path, script, request(), name)
        for seen in invocations(script):
            assert not Path(seen["cwd"]).parent.exists(), name
    left = set(Path(tempfile.gettempdir()).glob("rtb-claude-*")) - before
    assert not left  # 四條路徑都刪乾淨
    assert signal.SIGKILL  # 強制結束訊號(殺整個行程群組)


# ---- [S940] ----
def test_the_cost_is_the_higher_of_reported_and_computed(tmp_path):
    computed = 1000 * 2_000 + 100 * 10_000 + 400 * 4_000 + 5000 * 200  # 含兩種快取
    for name, reported_usd, expected, mismatch in (
            ("reported_higher", 0.02, 20_000_000, True),
            ("computed_higher", 0.000001, computed, True),
            ("close", 0.00616, 6_160_000, False)):
        output = claude_json(input_tokens=1000, output_tokens=100, cache_creation=400,
                             cache_read=5000, cost_usd=reported_usd)
        script = fake_claude(tmp_path / name, output)
        result = _call(tmp_path, script, request(max_output_tokens=3000), name)
        [row] = _rows(tmp_path / f"{name}.sqlite")
        assert result.list_nanousd == row.list_nanousd == expected, name
        assert row.settled_nanousd == -(-expected * 6 // 5), name
        assert (row.cache_write_5m_tokens, row.cache_write_1h_tokens,
                row.cache_read_tokens) == (0, 400, 5000)  # 沒分開回報的快取寫入整個算 1 小時
        assert row.reported_nanousd == round(reported_usd * 1e9) or name == "close"
        assert row.cost_mismatch is mismatch, name
