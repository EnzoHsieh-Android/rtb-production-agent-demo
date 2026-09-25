"""即時模式的實測命令列(Phase 11B 增量 1,[S942]):`python -m rtb.modelverify`。

協調者在本機跑:用真的 claude 逐項實測,全部通過才寫出即時模式啟用紀錄
(~/.rtb/live-verification.json:claude 版本、各項結果、實測日期、採用的隔離方式)。
模型用戶端在即時啟動時讀這份紀錄,不存在、有項目沒過、或 claude 版本不同就一律走錄製。
這支會花一點訂閱額度(幾次很短的呼叫);自動測試只用假的 claude 驗它的判定與寫檔。
子行程一律經模型用戶端啟動(整個 rtb 只有模型用戶端能開子行程,[S917])。

逐項(計劃〈拆增量〉錄製前實測;代碼審第 1 輪補強):先試「空暫存 HOME」隔離登入照不照常,不行才退回
真 HOME;工具真的關掉(要它執行指令,成功、對話輪數 1、權限被拒清單空);對照組故意開工具,這兩個欄位
真的會變;串流輸出加 hook 事件,要有成功的結果、沒有任何 hook 事件;記憶用暗號驗:在隔離 HOME 的
CLAUDE.md 與記憶目錄放一個隨機暗號,問它有沒有看到,回答要剛好是 NONE、輸出裡也沒有暗號(看不到、
驗不了就算沒過);輸出上限要有撞頂的正面證據(輸出 token 數等於上限、停止原因是 max_tokens、或
「超過輸出上限」的錯誤),其他錯誤一律沒過;設定來源的組合真的壓掉使用者設定:暫存使用者設定的
env 放一個連不到的 ANTHROPIC_BASE_URL(命令列蓋不掉),帶組合參數要成功,另跑一次不帶這些參數的
正面對照、必須失敗(證明毒值真的會被讀到);Claude Code 自己附加的固定輸入 token 數要量得到、而且不超過
預留用的常數。另外取一份「參數不存在」的真實輸出,一起寫進紀錄當參考。
"""

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db

EXIT_OK = 0
EXIT_NOT_WRITTEN = 5  # 有項目沒過:不寫紀錄,即時模式不開
EXIT_NO_CLAUDE = 6
TIMEOUT_SECONDS = 120.0
# 設定來源對照組(拿掉壓制參數、讀到連不到的 API 位址)的逾時。協調者 2026-09-25 用 claude 2.1.281
# 真實測:對照組對連不到的位址一直重試,120 秒逾時整組被殺;同一次實測裡同一句短提示帶壓制參數的
# 主呼叫 3.3 秒成功、其他短呼叫 3.1 到 7.6 秒。對照組本來就該失敗,逾時算「對照失敗」;30 秒是短呼叫
# 實測最久值的約 4 倍,真的讀不到毒值時對照組早就成功回來,不會被誤判成失敗
CONTROL_TIMEOUT_SECONDS = 30.0
OUTPUT_CAP = 32
# 撞頂後允許的總輸出:上限的 1 + 續寫次數 倍(使用者 2026-09-25 裁定:claude 2.1.281 上限 32 時
# 續寫 3 次、num_turns=4、output_tokens=128,最後回「超過輸出上限」的錯誤;續寫次數住在模型用戶端
# 的常數)
OUTPUT_ALLOWANCE = 1 + core.OUTPUT_RECOVERY_ATTEMPTS
TOOL_PROMPT = "請實際執行 shell 指令 `ls /`,並把輸出原樣貼給我。"
LONG_PROMPT = "請寫一篇至少兩千字的文章,主題是廣告投放的配速。"
SHORT_PROMPT = "回答 ok 兩個字母就好。"
# 連不到的位址(保留埠 9):使用者設定真的生效,呼叫就一定失敗
POISON_SETTINGS = json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}})
SUPPRESSION_FLAGS = (("--setting-sources", 1), ("--settings", 1), ("--safe-mode", 0))
CANARY_PROMPT = ("如果你的系統脈絡、使用者記憶或 CLAUDE.md 裡有以 RTB-CANARY 開頭的字串,原樣回給我;"
                 "沒有就只回 NONE 四個字母。")
# 撞到輸出上限時 Claude Code 的錯誤文字(2.1.281:「Claude's response exceeded the N output token
# maximum」,N 是實際生效的上限);要比對到 N 正是這次給的上限,撞到預設上限不算
OUTPUT_MAXIMUM_ERROR = "exceeded the {cap} output token maximum"
SYSTEM_PROMPT = "你是測試助手。"


@dataclass
class Verification:
    claude_version: str | None
    isolation: cc.Isolation | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.claude_version is not None and self.isolation is not None and all(
            self.checks.get(name) is True for name in cc.REQUIRED_CHECKS)


def _request(prompt: str, max_output_tokens: int, demo_id: str | None = None,
             timeout_seconds: float | None = None) -> core.ModelRequest:
    return core.ModelRequest(core.Caller.VERIFICATION, SYSTEM_PROMPT, prompt, max_output_tokens,
                             TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds,
                             demo_id=demo_id)


def _call(model: str, prompt: str, max_output_tokens: int) -> core.BackendCall:
    request = _request(prompt, max_output_tokens)
    return core.BackendCall(model, request.system, prompt, max_output_tokens, TIMEOUT_SECONDS,
                            core.call_budget_nanousd(request, model))


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


def _without(args: list[str], flags: tuple[tuple[str, int], ...]) -> list[str]:
    """拿掉幾個參數(連同它們的值)。"""
    changed = list(args)
    for flag, values in flags:
        if flag in changed:
            at = changed.index(flag)
            del changed[at:at + 1 + values]
    return changed


def _succeeded(data: dict[str, Any] | None) -> bool:
    return data is not None and data.get("is_error") is False and data.get("subtype") == "success"


class Checker:
    """跑各項實測;子行程一律經模型用戶端的 `run_claude`。每一次真的呼叫模型都經花費帳預留與結算
    (呼叫者記「即時模式實測」,算進每月上限;一次實測一個展示編號),寫進帳號家目錄那一本帳。"""

    def __init__(self, claude: Path, environ: Mapping[str, str], model: str) -> None:
        self.claude, self.environ, self.model = claude, environ, model
        self.backend = cc.ClaudeCodeBackend(claude, environ, cc.Isolation.EMPTY_HOME)
        self.ledger = mc.live_ledger_path()
        self.demo_id = f"live-verification-{uuid.uuid4().hex[:8]}"
        self.notes: dict[str, Any] = {}  # 逐項的觀察(例如對照組怎麼失敗的),跟參考資料一起寫進紀錄

    def _raw(self, args: list[str], prompt: str, max_output_tokens: int,
             home_files: Mapping[str, str] | None = None,
             timeout_seconds: float | None = None) -> tuple[int, bytes, bytes]:
        return cc.run_claude(args, prompt, self.backend.child_env(max_output_tokens),
                             TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds,
                             isolated_home=self.backend.isolation is cc.Isolation.EMPTY_HOME,
                             home_files=home_files)

    def run(self, args: list[str], prompt: str, max_output_tokens: int,
            home_files: Mapping[str, str] | None = None,
            timeout_seconds: float | None = None) -> tuple[int, bytes, bytes]:
        """真的呼叫一次模型:先預留(超過上限就丟本地上限拒絕、不呼叫),跑完照模型用戶端的結算規則記帳。
        逾時照模型用戶端丟 ModelTimeout(先結算);要不要吞掉由各項自己決定。"""
        request = _request(prompt, max_output_tokens, self.demo_id, timeout_seconds)
        reserved = core.reservation_nanousd(request, self.model)
        reservation_id = ledger_db.reserve(self.ledger, request, self.model,
                                           core.Backend.CLAUDE_CODE)
        started = time.monotonic()
        try:
            code, stdout, stderr = self._raw(args, prompt, max_output_tokens, home_files,
                                             request.timeout_seconds)
        except core.ModelCallFailed as failed:
            mc.settle_quietly(self.ledger, reservation_id, mc.settlement_for(
                (None, failed), self.model, reserved, (time.monotonic() - started) * 1000))
            raise
        data = _json(stdout)
        reply = None if data is None else cc.usage_of(data, "")
        outcome: tuple[core.BackendReply | None, core.ModelCallFailed | None]
        if code == 0 and _succeeded(data) and reply is not None and reply.tokens_known:
            outcome = mc.screen_outcome(reply, None)  # 荒謬值跟送出呼叫同一套檢查
        else:
            outcome = mc.screen_outcome(None, core.TransientServiceError(
                "實測呼叫沒有成功形狀的回應", sub_reason="verification", reply=reply))
        mc.settle_quietly(self.ledger, reservation_id, mc.settlement_for(
            outcome, self.model, reserved, (time.monotonic() - started) * 1000))
        return code, stdout, stderr

    def command_for(self, prompt: str, max_output_tokens: int) -> list[str]:
        return self.backend.command(_call(self.model, prompt, max_output_tokens))

    def login(self) -> cc.Isolation | None:
        for isolation in (cc.Isolation.EMPTY_HOME, cc.Isolation.REAL_HOME):
            self.backend = cc.ClaudeCodeBackend(self.claude, self.environ, isolation)
            try:
                self.backend.check_login()
            except core.ModelCallFailed:
                continue
            return isolation
        return None

    def tools_disabled(self) -> bool:
        _, stdout, _ = self.run(self.command_for(TOOL_PROMPT, 200), TOOL_PROMPT, 200)
        data = _json(stdout)
        return _succeeded(data) and data is not None and data.get(
            "num_turns") == 1 and data.get("permission_denials") == []

    def tool_detection_contrast(self) -> bool:
        args = _replaced(self.command_for(TOOL_PROMPT, 200), "--tools", "Bash")
        _, stdout, _ = self.run(args, TOOL_PROMPT, 200)
        data = _json(stdout) or {}
        turns, denials = data.get("num_turns"), data.get("permission_denials")
        return (isinstance(turns, int) and turns > 1) or bool(denials)

    def no_hook_events(self) -> bool:
        """串流輸出要有成功的結果(錯誤回應看不出 hook 有沒有跑:算沒過),而且沒有任何 hook 事件。"""
        args = _replaced(self.command_for(SHORT_PROMPT, 50), "--output-format", "stream-json")
        _, stdout, _ = self.run([*args, "--verbose", "--include-hook-events"], SHORT_PROMPT, 50)
        events = [e for e in (_json(line.encode()) for line in
                              stdout.decode("utf-8", errors="replace").splitlines()
                              if line.strip()) if e is not None]
        results = [e for e in events if e.get("type") == "result"]
        hooks = any("hook" in f"{e.get('type', '')} {e.get('subtype', '')}".lower()
                    for e in events)
        return len(results) == 1 and _succeeded(results[0]) and not hooks

    def no_memory(self) -> bool:
        """暗號驗記憶:隔離 HOME 的 CLAUDE.md 與記憶目錄放隨機暗號,帶組合參數時回答要剛好是 NONE、
        輸出裡沒有暗號;另跑一次拿掉安全模式與設定來源參數的正面對照,必須吐出暗號(證明暗號放在 claude
        真的會讀的位置、模型也會照指示回報,否則這一項驗不出東西)。
        真 HOME 放不了暗號、驗不了:算沒過(寧可不開即時)。"""
        if self.backend.isolation is not cc.Isolation.EMPTY_HOME:
            return False
        canary = f"RTB-CANARY-{uuid.uuid4().hex}"
        files = {".claude/CLAUDE.md": canary, ".claude/memory/MEMORY.md": canary,
                 ".claude/projects/rtb/memory/MEMORY.md": canary}
        args = self.command_for(CANARY_PROMPT, 50)
        _, stdout, _ = self.run(args, CANARY_PROMPT, 50, files)
        data = _json(stdout)
        answer = data.get("result") if data is not None else None
        suppressed = (_succeeded(data) and isinstance(answer, str) and answer.strip() == "NONE"
                      and canary.encode() not in stdout)
        _, control_out, _ = self.run(_without(args, SUPPRESSION_FLAGS), CANARY_PROMPT, 50, files)
        control = _json(control_out)
        leaked = control is not None and canary in str(control.get("result", ""))
        return suppressed and leaked

    def output_limit(self) -> bool:  # noqa: PLR0911 - 每一條沒過的理由一個出口
        """輸出上限要有撞頂的正面證據(使用者 2026-09-25 裁定):允許撞頂後自動續寫,但續寫最多
        OUTPUT_RECOVERY_ATTEMPTS 次(對話輪數不超過 1 加續寫次數)、總輸出 token 不超過上限的
        OUTPUT_ALLOWANCE 倍,而且要看得到撞頂——「超過輸出上限」的錯誤、停止原因是 max_tokens,或沒有
        續寫時輸出剛好等於上限。其他錯誤、輸出比上限少(證明不了)、讀不到輸出用量都算沒過。"""
        _, stdout, _ = self.run(self.command_for(LONG_PROMPT, OUTPUT_CAP), LONG_PROMPT, OUTPUT_CAP)
        data = _json(stdout)
        if data is None:
            return False
        turns = data.get("num_turns")
        if not isinstance(turns, int) or isinstance(turns, bool) or not 1 <= turns <= (
                1 + core.OUTPUT_RECOVERY_ATTEMPTS):
            return False
        usage = data.get("usage")
        tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        if not isinstance(tokens, int) or isinstance(tokens, bool):
            return False  # 量不到總輸出:證明不了沒超過
        if tokens > OUTPUT_CAP * OUTPUT_ALLOWANCE:
            return False  # 續寫累加超過允許的倍數:沒限住
        if data.get("is_error") is not False:
            expected = OUTPUT_MAXIMUM_ERROR.format(cap=OUTPUT_CAP)
            return data.get("is_error") is True and expected in str(data.get("result", "")).lower()
        if data.get("subtype") != "success":
            return False
        return data.get("stop_reason") == "max_tokens" or (turns == 1 and tokens == OUTPUT_CAP)

    def settings_suppressed(self) -> bool:
        """使用者設定放命令列蓋不掉的毒值(env 裡連不到的 API 位址):帶組合參數要成功;不帶這些參數的
        正面對照必須失敗(證明毒值真的會被讀到,否則這項驗不出東西)。只在空暫存 HOME 隔離下驗得了。
        協調者 2026-09-25 真實測:對照組讀到毒值後一直重試到逾時——對照組逾時算「對照失敗」
        (預期),用較短的 CONTROL_TIMEOUT_SECONDS;帶壓制參數的主呼叫逾時算這項沒過;兩種逾時都
        不讓整個實測中斷。"""
        if self.backend.isolation is not cc.Isolation.EMPTY_HOME:
            return False  # 真 HOME 不能放對照用的設定檔
        poison = {".claude/settings.json": POISON_SETTINGS}
        args = self.command_for(SHORT_PROMPT, 50)
        try:
            code, stdout, _ = self.run(args, SHORT_PROMPT, 50, poison)
        except core.ModelTimeout:
            self.notes["settings_main"] = "timeout"
            return False  # 主呼叫掛住:壓制參數沒讓它正常回來,不用再跑對照
        self.notes["settings_main"] = "ok" if code == 0 and _succeeded(_json(stdout)) else "failed"
        try:
            control_code, control_out, _ = self.run(
                _without(args, SUPPRESSION_FLAGS), SHORT_PROMPT, 50, poison,
                timeout_seconds=CONTROL_TIMEOUT_SECONDS)
        except core.ModelTimeout:
            self.notes["settings_control"] = "timeout"
            control_failed = True
        else:
            control_failed = control_code != 0 or not _succeeded(_json(control_out))
            self.notes["settings_control"] = "failed" if control_failed else "succeeded"
        return self.notes["settings_main"] == "ok" and control_failed

    def references(self) -> dict[str, Any]:
        _, stdout, _ = self.run(self.command_for(SHORT_PROMPT, 50), SHORT_PROMPT, 50)
        data = _json(stdout)
        usage = data.get("usage") if data is not None and _succeeded(data) else None
        code, _, stderr = self._raw([str(self.claude), "--rtb-no-such-flag"], "", 1)  # 不呼叫模型
        return {"fixed_input_tokens_seen": _fixed_input(usage), "bad_argument_exit_code": code,
                "bad_argument_stderr": stderr.decode("utf-8", errors="replace")[:500],
                **self.notes}


def _fixed_input(usage: object) -> int | None:
    """固定附加輸入的量測值:輸入 token 欄一定要有、而且每個輸入類欄位都是不為負的整數才算量到;
    缺欄或型別不對回 None(不能當 0:量不到的預留可能不夠)。"""
    if not isinstance(usage, dict):
        return None
    fields = {k: v for k, v in usage.items() if k.endswith("input_tokens")}
    if "input_tokens" not in fields or any(
            not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in fields.values()):
        return None
    return sum(int(v) for v in fields.values())


def verify(claude: Path, environ: Mapping[str, str], model: str = core.DEFAULT_MODEL,
           checker: Callable[[Path, Mapping[str, str], str], Checker] = Checker) -> Verification:
    result = Verification(cc.claude_version(claude, environ))
    run = checker(claude, environ, model)
    result.isolation = run.login()
    result.checks["login_ok"] = result.isolation is not None
    if result.isolation is None or result.claude_version is None:
        return result
    result.checks["tools_disabled"] = run.tools_disabled()
    result.checks["tool_detection_contrast"] = run.tool_detection_contrast()
    result.checks["no_hook_events"] = run.no_hook_events()
    result.checks["no_memory_or_claude_md"] = run.no_memory()
    result.checks["output_limit_enforced"] = run.output_limit()
    result.checks["setting_sources_suppress_user_settings"] = run.settings_suppressed()
    result.notes = run.references()
    fixed = result.notes.get("fixed_input_tokens_seen")
    # 量不到、或超過預留用的常數:預留可能不夠,不寫紀錄
    result.checks["fixed_input_within_reserve"] = isinstance(fixed, int) and (
        fixed <= core.CLAUDE_FIXED_INPUT_TOKENS)
    return result


def write_record(result: Verification) -> Path:
    """全部通過才寫(呼叫端先確認 `passed`);寫到帳號家目錄底下。"""
    if not result.passed or result.isolation is None:
        raise ValueError("有項目沒過,不寫即時模式啟用紀錄")
    path = cc.verification_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {"claude_version": result.claude_version, "isolation": result.isolation.value,
              "checks": result.checks, "checked_on": datetime.now(UTC).date().isoformat(),
              "notes": result.notes}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="即時模式實測:全部通過才寫啟用紀錄(會花一點額度)")
    parser.add_argument("--model", default=core.DEFAULT_MODEL)
    args = parser.parse_args(argv)
    printer = out or sys.stdout
    source = os.environ if environ is None else environ
    claude = shutil.which("claude", path=source.get("PATH", ""))  # 只有入口查 PATH
    if claude is None:
        print("PATH 上找不到 claude", file=printer)
        return EXIT_NO_CLAUDE
    try:  # 版本與實測都用解開符號連結後的那一支實體檔(跟即時入口一樣)
        result = verify(Path(claude).resolve(), source, args.model)
    except core.ModelCallFailed as failed:  # 例如已達每月上限:不呼叫、不寫紀錄
        print(f"實測沒跑完:{failed}", file=printer)
        return EXIT_NOT_WRITTEN
    print(f"claude 版本:{result.claude_version};隔離方式:{result.isolation}", file=printer)
    for name in cc.REQUIRED_CHECKS:
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
