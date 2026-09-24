"""Claude Code 後端(Phase 11B 增量 1,計劃〈模型用戶端〉):整個 rtb 裡唯一啟動子行程的模組([S917])。

子行程的參數、環境白名單、隔離方式、清理順序、回應判定五步,以及即時啟動前的檢查(管理政策來源、即時模式
啟用紀錄、claude 版本、真 HOME 隔離時的記憶目錄)。只有模型用戶端與實測命令列准匯入這支。
"""

import contextlib
import json
import logging
import os
import plistlib
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from rtb import modelcore as core
from rtb.modelcore import (
    Backend,
    BackendCall,
    BackendReply,
    ConfigError,
    ModelCallFailed,
    ModelTimeout,
    Outcome,
    TransientServiceError,
    UnreadableModelResponse,
)

log = logging.getLogger(__name__)

# Claude Code 後端([S905]、[S906]):參數名稱以實作當下 `claude --help`(2.1.281)為準
CLAUDE_EFFORT = "low"  # 固定思考強度:最低一級,不吃使用者設定
OUTPUT_LIMIT_ENV = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"
# 設定來源只留最少、再給一份空設定,壓掉使用者設定檔的 env 區塊;可行的組合待錄製前本機實測⑦確認
SETTING_SOURCES = ""
EMPTY_SETTINGS = "{}"
CHILD_ENV = ("PATH", "HOME", "USER", "LANG")
LOGIN_CHECK_TIMEOUT_SECONDS = 10.0
GROUP_EXIT_WAIT_SECONDS = 5.0
VERSION_CHECK_TIMEOUT_SECONDS = 10.0
# 管理政策來源(系統管理員層級,安全模式不保證會略過,[S941]):系統層目錄下的 managed-settings.json、
# managed-settings.d 底下每一支、managed-mcp.json,macOS 的 MDM 設定,加上家目錄的
# remote-settings.json(呼叫時才用 HOME 算)。任一來源含這些鍵就拒絕即時。常數可在測試裡換成暫存目錄。
MANAGED_DIRS: tuple[Path, ...] = (Path("/Library/Application Support/ClaudeCode"),
                                  Path("/etc/claude-code"))
MDM_PLISTS: tuple[Path, ...] = (
    Path("/Library/Managed Preferences/com.anthropic.claudecode.plist"),)
POLICY_KEYS = ("hooks", "mcpServers", "env", "apiKeyHelper")


class Isolation(StrEnum):
    """claude 子行程跟使用者設定隔開的方式;用哪一種寫在即時模式啟用紀錄([S942])。"""

    EMPTY_HOME = "empty_home"  # 首選:子行程的 HOME 指向每次新建的空暫存目錄
    REAL_HOME = "real_home"  # 退路:真 HOME 加安全模式與設定來源參數,每次即時啟動前查記憶目錄


# 即時模式啟用紀錄必須通過的項目(計劃第 8 版〈模型用戶端〉;實測命令列逐項跑,全過才寫紀錄)
REQUIRED_CHECKS = ("login_ok", "tools_disabled", "tool_detection_contrast", "no_hook_events",
                   "no_memory_or_claude_md", "output_limit_enforced",
                   "setting_sources_suppress_user_settings")


@dataclass(frozen=True)
class ErrorSample:
    """一種認得的錯誤子類型:比對 claude JSON 輸出的哪一欄(小寫後包含哪段文字)、歸哪一類。"""

    field: str
    contains: str
    outcome: Outcome
    sub_reason: str


# 認得的錯誤子類型:只收在本機錄製時取到真實輸出的樣本(存成測試夾具並寫明比對哪一欄)。
# 取不到真實樣本的子類型程式裡就不認,一律落到「暫時性服務錯誤、無法可靠分類」(照預留結算,
# 評估保守停下)。增量 1 實作時還沒有任何真實樣本,所以是空的,等協調者錄製時補。
KNOWN_ERRORS: tuple[ErrorSample, ...] = ()


def _policy_files() -> list[Path]:
    found: list[Path] = []
    for directory in MANAGED_DIRS:
        found += [directory / "managed-settings.json", directory / "managed-mcp.json"]
        dropins = directory / "managed-settings.d"
        if dropins.is_dir():
            found += sorted(p for p in dropins.iterdir() if p.is_file())
    found.append(Path.home() / ".claude" / "remote-settings.json")
    return found


def _policy_hits(path: Path, data: object) -> str | None:
    if not isinstance(data, dict):
        return f"管理政策來源讀不懂:{path}"
    hits = [key for key in POLICY_KEYS if data.get(key)]
    return f"管理政策來源有 {'、'.join(hits)}:{path}" if hits else None


def managed_policy_problem() -> str | None:
    """不花額度的設定檢查([S941]):任一管理政策來源存在而且含 hook、MCP、env 或金鑰輔助程式設定就回
    原因;讀不懂也算(寧可不即時)。"""
    for path in _policy_files():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return f"管理政策來源讀不懂:{path}"
        hit = _policy_hits(path, data)
        if hit is not None:
            return hit
    for path in MDM_PLISTS:
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                data = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException, ValueError):
            return f"MDM 設定讀不懂:{path}"
        hit = _policy_hits(path, data)
        if hit is not None:
            return hit
    return None


def verification_path() -> Path:
    """即時模式啟用紀錄(呼叫時才用 HOME 算,不做成模組層常數)。"""
    return Path.home() / ".rtb" / "live-verification.json"


def memory_paths() -> list[Path]:
    """真 HOME 隔離時每次即時啟動前要查的記憶位置(呼叫時才用 HOME 算);以實作當下的 Claude Code 為準,
    錄製前本機實測③核對。"""
    root = Path.home() / ".claude"
    return [root / "CLAUDE.md", root / "memory", *sorted(root.glob("projects/*/memory"))]


def memory_problem() -> str | None:
    for path in memory_paths():
        if path.is_file() and path.stat().st_size > 0:
            return f"記憶檔有內容:{path}"
        if path.is_dir() and any(path.iterdir()):
            return f"記憶目錄有內容:{path}"
    return None


def claude_version(claude: Path, environ: Mapping[str, str] | None = None) -> str | None:
    """跑一次 claude 的版本指令(白名單環境、新行程群組、固定逾時);讀不到回 None。"""
    backend = ClaudeCodeBackend(claude, environ, Isolation.REAL_HOME)
    try:
        code, stdout, _ = run_claude([str(claude), "--version"], "", backend.child_env(1),
                                     VERSION_CHECK_TIMEOUT_SECONDS)
    except ModelCallFailed:
        return None
    lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
    return lines[0].strip() if code == 0 and lines else None


def verification_problem(claude: Path, environ: Mapping[str, str] | None = None) -> tuple[
        str | None, Isolation | None]:
    """即時模式啟用紀錄的判定([S942]):紀錄不存在、讀不懂、任一項沒過、或版本跟現在不同都回原因。"""
    path = verification_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"沒有即時模式啟用紀錄:{path}", None
    except (OSError, ValueError):
        return f"即時模式啟用紀錄讀不懂:{path}", None
    checks = record.get("checks") if isinstance(record, dict) else None
    if not isinstance(checks, dict) or any(checks.get(name) is not True
                                           for name in REQUIRED_CHECKS):
        return "即時模式啟用紀錄有沒過的項目", None
    try:
        isolation = Isolation(record.get("isolation"))
    except ValueError:
        return "即時模式啟用紀錄的隔離方式讀不懂", None
    current = claude_version(claude, environ)
    if current is None or current != record.get("claude_version"):
        return (f"claude 版本({current})跟啟用紀錄({record.get('claude_version')})不同,"
                "要重跑實測"), None
    return None, isolation


# ---- Claude Code 後端 ----
def _usd_text(nanousd: int) -> str:
    return format(Decimal(nanousd) / core.NANOUSD_PER_USD, "f")


def _nanousd_of(value: object) -> int | None:
    """回報的美元金額 → 十億分之一美元(無條件進位);不是有限、不為負的數字就 None。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    amount = Decimal(repr(value)) * core.NANOUSD_PER_USD
    if not amount.is_finite() or amount < 0:
        return None
    return int(amount.to_integral_value(rounding="ROUND_CEILING"))


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _has_live_members(group: int) -> bool:
    """行程群組裡還有沒有活著的行程。macOS 上群組只剩還沒領回的殭屍時回 EPERM,群組不存在回 ESRCH,
    兩種都算沒有活的。"""
    try:
        os.killpg(group, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _kill_group(group: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGKILL)


def _exited_unreaped(pid: int, deadline: float) -> bool:
    """只等不領回:主行程在期限內結束就回 True(沒領回前它的行程編號與群組編號不會被重用)。"""
    while True:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)


def _finish(process: subprocess.Popen[bytes], deadline: float) -> tuple[int, bool]:
    """清理順序([S939]):先只等不領回確認主行程結束(逾時就不等了)→ 殺整組 → 有上限地確認群組裡沒有
    活著的行程 → 最後才領回主行程。回(結束代碼, 是否逾時)。"""
    timed_out = not _exited_unreaped(process.pid, deadline)
    _kill_group(process.pid)
    _exited_unreaped(process.pid, time.monotonic() + GROUP_EXIT_WAIT_SECONDS)
    limit = time.monotonic() + GROUP_EXIT_WAIT_SECONDS
    while _has_live_members(process.pid):
        if time.monotonic() > limit:
            log.error("claude 行程群組在 %s 秒內沒有全部結束", GROUP_EXIT_WAIT_SECONDS)
            break
        _kill_group(process.pid)
        time.sleep(0.02)
    return process.wait(), timed_out


def run_claude(args: list[str], stdin_text: str, env: Mapping[str, str], timeout_seconds: float,
               *, isolated_home: bool = False,
               home_files: Mapping[str, str] | None = None) -> tuple[int, bytes, bytes]:
    """在新的工作階段(新行程群組)跑一次 claude:工作目錄是新建的空暫存目錄,標準輸入、輸出與錯誤
    都是暫存檔(不用管線:孫行程繼承管線會把成功的呼叫拖到逾時);isolated_home 時子行程的 HOME 指向
    另一個新建的空暫存目錄。起不來、成功、非 0 結束、逾時四條路徑都照 `_finish` 的順序清理,最後一定
    刪掉暫存目錄([S939])。起不來丟設定錯誤,逾時丟逾時;回(結束代碼, 標準輸出, 標準錯誤)。"""
    base = Path(tempfile.mkdtemp(prefix="rtb-claude-"))
    workdir, files = base / "work", base / "io"
    workdir.mkdir()
    files.mkdir()
    child_env = dict(env)
    if isolated_home:
        (base / "home").mkdir()
        child_env["HOME"] = str(base / "home")
        for relative, content in (home_files or {}).items():  # 只給實測命令列放對照用的設定檔
            target = base / "home" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    try:
        (files / "stdin").write_text(stdin_text, encoding="utf-8")
        with ((files / "stdin").open("rb") as stdin, (files / "stdout").open("wb") as stdout,
              (files / "stderr").open("wb") as stderr):
            try:
                process = subprocess.Popen(  # noqa: S603 - 參數固定、不經 shell
                    args, stdin=stdin, stdout=stdout, stderr=stderr, cwd=workdir,
                    env=child_env, start_new_session=True)
            except OSError as failed:  # 找不到、不能執行、起不來
                raise ConfigError(f"claude 起不來({type(failed).__name__})",
                                  sub_reason="cannot_start") from failed
            returncode, timed_out = _finish(process, time.monotonic() + timeout_seconds)
        if timed_out:
            raise ModelTimeout("模型呼叫逾時,已殺掉整個行程群組")
        return returncode, (files / "stdout").read_bytes(), (files / "stderr").read_bytes()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _usage_of(data: Mapping[str, Any], text: str) -> BackendReply | None:
    """讀得出用量就組成回應(失敗的回應也讀,結算時取較高者);讀不出回 None。
    5 分鐘與 1 小時快取寫入沒有分開回報時,整個算 1 小時(較貴,寧多不少)。"""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    counts = [_count(usage.get(name)) for name in ("input_tokens", "output_tokens")]
    cache_read = _count(usage.get("cache_read_input_tokens", 0))
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        writes = [_count(split.get("ephemeral_5m_input_tokens", 0)),
                  _count(split.get("ephemeral_1h_input_tokens", 0))]
    else:
        writes = [0, _count(usage.get("cache_creation_input_tokens", 0))]
    values = [*counts, *writes, cache_read]
    if any(value is None for value in values):
        return None
    numbers = [value or 0 for value in values]
    return BackendReply(text, numbers[0], numbers[1], numbers[2], numbers[3], numbers[4],
                        _nanousd_of(data.get("total_cost_usd")))


_HEAD = {"type": str, "subtype": str, "is_error": bool}


def _stderr_head(stderr: bytes) -> str:
    lines = stderr.decode("utf-8", errors="replace").strip().splitlines()[:3]
    return ("stderr: " + " | ".join(lines))[:200] if lines else "stderr: (空)"


def _parse_output(returncode: int, stdout: bytes, stderr: bytes) -> dict[str, Any]:
    """第 2 步:標準輸出讀成 JSON 物件,而且至少有類型、子類型、是否錯誤三欄。讀不成時:結束代碼
    非 0 歸暫時性服務錯誤、無法可靠分類(例如參數改名、在解析參數就退出),標準錯誤前幾行進子原因;
    結束代碼 0 才是讀不懂。"""
    try:
        data = json.loads(stdout.decode("utf-8"))
    except ValueError:
        data = None
    if not isinstance(data, dict) or any(not isinstance(data.get(name), kind)
                                         for name, kind in _HEAD.items()):
        if returncode != 0:
            raise TransientServiceError(f"claude 結束代碼 {returncode},輸出讀不成 JSON",
                                        sub_reason=_stderr_head(stderr), unclassified=True)
        raise UnreadableModelResponse("claude 的輸出讀不成含類型、子類型、是否錯誤的 JSON")
    return data


def _tool_use_seen(data: Mapping[str, Any]) -> bool:
    """錯誤回應的工具使用痕跡:對話輪數大於 1 或權限被拒清單非空(輪數 0 或缺欄位不算)。"""
    turns, denials = data.get("num_turns"), data.get("permission_denials")
    return (isinstance(turns, int) and not isinstance(turns, bool) and turns > 1) or (
        isinstance(denials, list) and bool(denials))


def _reported_error(data: Mapping[str, Any]) -> ModelCallFailed:
    """第 3 步:是錯誤的回應先分子類型(只認有真實樣本的,見 KNOWN_ERRORS);認不出的歸暫時性服務錯誤並標
    「無法可靠分類」。分完之後有工具使用痕跡就另加標記、印錯誤要人看。"""
    reply = _usage_of(data, "")
    failure: ModelCallFailed | None = None
    for sample in KNOWN_ERRORS:
        if sample.contains in str(data.get(sample.field, "")).lower():
            failure = core.BY_OUTCOME[sample.outcome](f"claude 回報錯誤:{sample.sub_reason}",
                                                  sub_reason=sample.sub_reason, reply=reply)
            break
    if failure is None:
        failure = TransientServiceError("claude 回報認不出的錯誤", sub_reason="unclassified",
                                        reply=reply, unclassified=True)
    if _tool_use_seen(data):
        failure.tool_use = True
        log.error("claude 的錯誤回應帶著工具使用的痕跡(對話輪數或權限被拒清單):評估應整批停下")
    return failure


def judge_output(returncode: int, stdout: bytes, stderr: bytes = b"") -> BackendReply:
    """回應判定五步(前一步不過就不看後面,[S904]):①起不起得來(在 `run_claude`)②讀成 JSON、有類型、
    子類型、是否錯誤三欄 ③是錯誤就先分子類型(再看有沒有工具使用痕跡)④不是錯誤才做工具使用偵測
    (對話輪數 1、權限被拒清單空,[S936])與欄位齊全 ⑤結束代碼非 0 → 暫時性、無法可靠分類。"""
    data = _parse_output(returncode, stdout, stderr)
    if data["is_error"]:
        raise _reported_error(data)
    turns, denials = data.get("num_turns"), data.get("permission_denials")
    if isinstance(turns, bool) or turns != 1 or denials != []:
        raise UnreadableModelResponse("回應有工具使用的痕跡", sub_reason="tool_use")
    result = data.get("result")
    reply = _usage_of(data, result if isinstance(result, str) else "")
    if not isinstance(result, str) or reply is None:
        raise UnreadableModelResponse("成功的回應缺結果文字或用量", reply=reply)
    if returncode != 0:
        raise TransientServiceError(f"claude 結束代碼 {returncode}", sub_reason="exit_code",
                                    reply=reply, unclassified=True)
    return reply


class ClaudeCodeBackend:
    """即時後端:本機 Claude Code 的非互動模式(子行程)。參數固定寫在程式裡([S906]),
    子行程環境只帶白名單([S905]),每次在新建的空暫存目錄、新的工作階段跑([S939])。
    第一次呼叫前跑一次登入狀態檢查(同樣的白名單環境、新行程群組、逾時 10 秒;一個後端物件只跑
    一次,入口一個行程建一個),沒登入就是設定錯誤、不呼叫模型。"""

    kind = Backend.CLAUDE_CODE

    def __init__(self, executable: Path, source_env: Mapping[str, str] | None = None,
                 isolation: Isolation = Isolation.EMPTY_HOME) -> None:
        self._executable = Path(executable)
        self._source_env = source_env
        self.isolation = isolation
        self._logged_in = False

    def child_env(self, max_output_tokens: int) -> dict[str, str]:
        source = os.environ if self._source_env is None else self._source_env
        env = {name: source[name] for name in CHILD_ENV if name in source}
        env[OUTPUT_LIMIT_ENV] = str(max_output_tokens)
        return env

    def command(self, call: BackendCall) -> list[str]:
        return [str(self._executable), "-p", "--output-format", "json", "--model", call.model,
                "--effort", CLAUDE_EFFORT, "--system-prompt", call.system, "--tools", "",
                "--strict-mcp-config", "--disable-slash-commands", "--safe-mode",
                "--setting-sources", SETTING_SOURCES, "--settings", EMPTY_SETTINGS,
                "--no-session-persistence", "--max-budget-usd", _usd_text(call.budget_nanousd)]

    def check_login(self) -> None:
        args = [str(self._executable), "auth", "status", "--json"]
        try:
            code, stdout, _ = run_claude(args, "", self.child_env(1), LOGIN_CHECK_TIMEOUT_SECONDS,
                                         isolated_home=self.isolation is Isolation.EMPTY_HOME)
            status = json.loads(stdout.decode("utf-8"))
        except ModelTimeout as slow:
            raise ConfigError("claude 登入狀態檢查逾時", sub_reason="not_logged_in") from slow
        except ValueError as bad:
            raise ConfigError("claude 登入狀態讀不懂", sub_reason="not_logged_in") from bad
        if code != 0 or not isinstance(status, dict) or status.get("loggedIn") is not True:
            raise ConfigError("claude 沒登入", sub_reason="not_logged_in")

    def send(self, call: BackendCall) -> BackendReply:
        if not self._logged_in:
            self.check_login()
            self._logged_in = True
        code, stdout, stderr = run_claude(
            self.command(call), call.user, self.child_env(call.max_output_tokens),
            call.timeout_seconds, isolated_home=self.isolation is Isolation.EMPTY_HOME)
        return judge_output(code, stdout, stderr)
