"""即時模式的實測命令列(Phase 11B 增量 1,[S942]):`python -m rtb.modelverify`。

協調者在本機跑:用真的 claude 逐項實測,全部通過才寫出即時模式啟用紀錄
(~/.rtb/live-verification.json:claude 版本、各項結果、實測日期、採用的隔離方式)。
模型用戶端在即時啟動時讀這份紀錄,不存在、有項目沒過、或 claude 版本不同就一律走錄製。
這支會花一點訂閱額度(幾次很短的呼叫);自動測試只用假的 claude 驗它的判定與寫檔。
子行程一律經模型用戶端啟動(整個 rtb 只有模型用戶端能開子行程,[S917])。

逐項(計劃〈拆增量〉錄製前實測):先試「空暫存 HOME」隔離登入照不照常,不行才退回真 HOME;
工具真的關掉(要它執行指令,對話輪數 1、權限被拒清單空);對照組故意開工具,這兩個欄位真的會變;
串流輸出加 hook 事件,沒有任何 hook 事件、送出內容沒有使用者記憶或 CLAUDE.md;輸出上限變數真的
限住輸出;設定來源的組合真的壓掉使用者設定(暫存使用者設定放一個會讓呼叫明顯失敗的模型,組合參數下
它不生效)。另外量 Claude Code 自己附加的固定輸入 token 數、取一份「參數不存在」的真實輸出,
一起寫進紀錄當參考。
"""

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from rtb import modelclient as mc

EXIT_OK = 0
EXIT_NOT_WRITTEN = 5  # 有項目沒過:不寫紀錄,即時模式不開
EXIT_NO_CLAUDE = 6
TIMEOUT_SECONDS = 120.0
OUTPUT_CAP = 32
TOOL_PROMPT = "請實際執行 shell 指令 `ls /`,並把輸出原樣貼給我。"
LONG_PROMPT = "請寫一篇至少兩千字的文章,主題是廣告投放的配速。"
SHORT_PROMPT = "回答 ok 兩個字母就好。"
POISON_SETTINGS = json.dumps({"model": "claude-rtb-poison-does-not-exist"})


@dataclass
class Verification:
    claude_version: str | None
    isolation: mc.Isolation | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.claude_version is not None and self.isolation is not None and all(
            self.checks.get(name) is True for name in mc.REQUIRED_CHECKS)


def _call(model: str, prompt: str, max_output_tokens: int) -> mc.BackendCall:
    request = mc.ModelRequest(mc.Caller.EVAL_CANDIDATE, "你是測試助手。", prompt,
                              max_output_tokens, TIMEOUT_SECONDS)
    return mc.BackendCall(model, request.system, prompt, max_output_tokens, TIMEOUT_SECONDS,
                          mc.call_budget_nanousd(request, model))


def _json(stdout: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(stdout.decode("utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _replaced(args: list[str], flag: str, value: str) -> list[str]:
    changed = list(args)
    changed[changed.index(flag) + 1] = value
    return changed


class Checker:
    """跑各項實測;子行程一律經模型用戶端的 `run_claude`。"""

    def __init__(self, claude: Path, environ: Mapping[str, str], model: str) -> None:
        self.claude, self.environ, self.model = claude, environ, model
        self.backend = mc.ClaudeCodeBackend(claude, environ, mc.Isolation.EMPTY_HOME)

    def run(self, args: list[str], prompt: str, max_output_tokens: int,
            home_files: Mapping[str, str] | None = None) -> tuple[int, bytes, bytes]:
        return mc.run_claude(args, prompt, self.backend.child_env(max_output_tokens),
                             TIMEOUT_SECONDS,
                             isolated_home=self.backend.isolation is mc.Isolation.EMPTY_HOME,
                             home_files=home_files)

    def args(self, prompt: str, max_output_tokens: int) -> list[str]:
        return self.backend.command(_call(self.model, prompt, max_output_tokens))

    def login(self) -> mc.Isolation | None:
        for isolation in (mc.Isolation.EMPTY_HOME, mc.Isolation.REAL_HOME):
            self.backend = mc.ClaudeCodeBackend(self.claude, self.environ, isolation)
            try:
                self.backend.check_login()
            except mc.ModelCallFailed:
                continue
            return isolation
        return None

    def tools_disabled(self) -> bool:
        _, stdout, _ = self.run(self.args(TOOL_PROMPT, 200), TOOL_PROMPT, 200)
        data = _json(stdout) or {}
        return data.get("is_error") is False and data.get(
            "num_turns") == 1 and data.get("permission_denials") == []

    def tool_detection_contrast(self) -> bool:
        args = _replaced(self.args(TOOL_PROMPT, 200), "--tools", "Bash")
        _, stdout, _ = self.run(args, TOOL_PROMPT, 200)
        data = _json(stdout) or {}
        turns, denials = data.get("num_turns"), data.get("permission_denials")
        return (isinstance(turns, int) and turns > 1) or bool(denials)

    def hooks_and_memory(self) -> tuple[bool, bool]:
        args = _replaced(self.args(SHORT_PROMPT, 50), "--output-format", "stream-json")
        _, stdout, _ = self.run([*args, "--verbose", "--include-hook-events"], SHORT_PROMPT, 50)
        text = stdout.decode("utf-8", errors="replace")
        events = [_json(line.encode()) for line in text.splitlines() if line.strip()]
        hooks = any("hook" in f"{e.get('type', '')} {e.get('subtype', '')}".lower()
                    for e in events if e)
        memory = any(marker in text for marker in ("CLAUDE.md", "MEMORY.md"))
        return bool(events) and not hooks, bool(events) and not memory

    def output_limit(self) -> bool:
        _, stdout, _ = self.run(self.args(LONG_PROMPT, OUTPUT_CAP), LONG_PROMPT, OUTPUT_CAP)
        usage = (_json(stdout) or {}).get("usage")
        tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        return isinstance(tokens, int) and tokens <= OUTPUT_CAP

    def settings_suppressed(self) -> bool:
        if self.backend.isolation is not mc.Isolation.EMPTY_HOME:
            return False  # 真 HOME 不能放對照用的設定檔:這一項只在空暫存 HOME 隔離下驗得了
        code, stdout, _ = self.run(self.args(SHORT_PROMPT, 50), SHORT_PROMPT, 50,
                                   {".claude/settings.json": POISON_SETTINGS})
        data = _json(stdout) or {}
        return code == 0 and data.get("is_error") is False

    def references(self) -> dict[str, Any]:
        _, stdout, _ = self.run(self.args(SHORT_PROMPT, 50), SHORT_PROMPT, 50)
        usage = (_json(stdout) or {}).get("usage")
        fixed = None
        if isinstance(usage, dict):
            fixed = sum(v for k, v in usage.items() if k.endswith("input_tokens")
                        and isinstance(v, int))
        code, _, stderr = self.run([str(self.claude), "--rtb-no-such-flag"], "", 1)
        return {"fixed_input_tokens_seen": fixed, "bad_argument_exit_code": code,
                "bad_argument_stderr": stderr.decode("utf-8", errors="replace")[:500]}


def verify(claude: Path, environ: Mapping[str, str], model: str = mc.DEFAULT_MODEL,
           checker: Callable[[Path, Mapping[str, str], str], Checker] = Checker) -> Verification:
    result = Verification(mc.claude_version(claude, environ))
    run = checker(claude, environ, model)
    result.isolation = run.login()
    result.checks["login_ok"] = result.isolation is not None
    if result.isolation is None or result.claude_version is None:
        return result
    result.checks["tools_disabled"] = run.tools_disabled()
    result.checks["tool_detection_contrast"] = run.tool_detection_contrast()
    hooks, memory = run.hooks_and_memory()
    result.checks["no_hook_events"], result.checks["no_memory_or_claude_md"] = hooks, memory
    result.checks["output_limit_enforced"] = run.output_limit()
    result.checks["setting_sources_suppress_user_settings"] = run.settings_suppressed()
    result.notes = run.references()
    return result


def write_record(result: Verification) -> Path:
    """全部通過才寫(呼叫端先確認 `passed`);寫到呼叫時的 HOME 底下。"""
    if not result.passed or result.isolation is None:
        raise ValueError("有項目沒過,不寫即時模式啟用紀錄")
    path = mc.verification_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {"claude_version": result.claude_version, "isolation": result.isolation.value,
              "checks": result.checks, "checked_on": datetime.now(UTC).date().isoformat(),
              "notes": result.notes}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="即時模式實測:全部通過才寫啟用紀錄(會花一點額度)")
    parser.add_argument("--model", default=mc.DEFAULT_MODEL)
    args = parser.parse_args(argv)
    printer = out or sys.stdout
    source = os.environ if environ is None else environ
    claude = shutil.which("claude", path=source.get("PATH", ""))  # 只有入口查 PATH
    if claude is None:
        print("PATH 上找不到 claude", file=printer)
        return EXIT_NO_CLAUDE
    result = verify(Path(claude), source, args.model)
    print(f"claude 版本:{result.claude_version};隔離方式:{result.isolation}", file=printer)
    for name in mc.REQUIRED_CHECKS:
        print(f"- {name}:{'過' if result.checks.get(name) else '沒過'}", file=printer)
    if not result.passed:
        print("有項目沒過:不寫啟用紀錄,即時模式不開", file=printer)
        return EXIT_NOT_WRITTEN
    print(f"全部通過,寫出 {write_record(result)}", file=printer)
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
