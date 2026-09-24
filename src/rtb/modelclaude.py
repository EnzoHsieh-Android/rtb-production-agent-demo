"""Claude Code 後端(Phase 11B 增量 1,計劃〈模型用戶端〉):整個 rtb 裡唯一啟動子行程的模組([S917])。

子行程的參數、環境白名單、隔離方式、清理順序、回應判定五步,以及即時啟動前的檢查(管理政策來源、即時模式
啟用紀錄、claude 版本、真 HOME 隔離時的記憶目錄)。只有模型用戶端與實測命令列准匯入這支。
"""

import contextlib
import json
import logging
import os
import plistlib
import pwd
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import IO, Any

from rtb import modelcore as core
from rtb import modelledger_view as view
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
# managed-settings.d 底下每一支、managed-mcp.json,macOS 的 MDM 設定(系統層與個人層
# `<MANAGED_PREFERENCES>/<帳號>/`),加上家目錄的 remote-settings.json(呼叫時才用 HOME 算)。
# 任一來源含這些鍵就拒絕即時。常數可在測試裡換成暫存目錄。來源清單與鍵名以 2.1.281 為準,每升一版
# claude 要重查一次(計劃〈風險〉)。
MANAGED_DIRS: tuple[Path, ...] = (Path("/Library/Application Support/ClaudeCode"),
                                  Path("/etc/claude-code"))
MDM_PLIST_NAME = "com.anthropic.claudecode.plist"
MDM_PLISTS: tuple[Path, ...] = (Path("/Library/Managed Preferences") / MDM_PLIST_NAME,)
MANAGED_PREFERENCES = Path("/Library/Managed Preferences")  # 個人層:底下的 <帳號>/ 目錄
# policyHelper / policyHelpers:啟動時跑一支程式動態算管理設定,內容事先看不到,出現就拒絕
POLICY_KEYS = ("hooks", "mcpServers", "env", "apiKeyHelper", "policyHelper", "policyHelpers")


class Isolation(StrEnum):
    """claude 子行程跟使用者設定隔開的方式;用哪一種寫在即時模式啟用紀錄([S942])。"""

    EMPTY_HOME = "empty_home"  # 首選:子行程的 HOME 指向每次新建的空暫存目錄
    REAL_HOME = "real_home"  # 退路:真 HOME 加安全模式與設定來源參數,每次即時啟動前查記憶目錄


# 即時模式啟用紀錄必須通過的項目(計劃第 8 版〈模型用戶端〉;實測命令列逐項跑,全過才寫紀錄)
REQUIRED_CHECKS = ("login_ok", "tools_disabled", "tool_detection_contrast", "no_hook_events",
                   "no_memory_or_claude_md", "output_limit_enforced",
                   "setting_sources_suppress_user_settings", "fixed_input_within_reserve")


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
    user = pwd.getpwuid(os.getuid()).pw_name
    for path in (*MDM_PLISTS, MANAGED_PREFERENCES / user / MDM_PLIST_NAME):
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
    """即時模式啟用紀錄:帳號家目錄下(呼叫時才算,不看環境變數 HOME,跟花費帳一樣)。"""
    return view.account_home() / ".rtb" / "live-verification.json"


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


# Linux 的行程表(測試換成暫存目錄模擬);macOS 沒有,退回送訊號判斷
PROC_ROOT = Path("/proc")


def _stat_fields(entry: Path) -> tuple[str, int] | None:
    """一個行程的(狀態, 行程群組);讀不到或格式不對回 None。行程名可能含空白與括號:從最後一個「)」
    之後切。"""
    try:
        stat = (entry / "stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    fields = stat[stat.rfind(")") + 2:].split()
    if len(fields) < 3 or not fields[2].lstrip("-").isdigit():
        return None
    return fields[0], int(fields[2])


def _proc_group_members(group: int) -> bool | None:
    """從行程表找同一個行程群組、狀態不是殭屍(Z)的行程;判不出來回 None(交給送訊號判斷)。
    Linux 上群組只剩還沒領回的主行程(殭屍)時,對群組送 0 號訊號照樣成功,不能用它判;而主行程刻意
    不先領回(領回之後行程編號與群組編號可能被重用),所以要看行程表裡的狀態。
    先自我檢查這份行程表是不是本機、同一個 PID 命名空間的 Linux 格式,判不準就回 None
    (判成「沒有活的」會漏殺):
    ① 核心自己的判斷:/proc/self 指到的編號要等於本行程的編號(它依這份 procfs 所屬的命名空間算;讀的人
      不在那個命名空間時是宿主編號或讀不到;代碼審第 2 輪:只比 stat 會撞到宿主的核心執行緒)。
    ② 本行程的群組編號是 0(群組在命名空間外面)也判不準。
    ③ 行程表裡自己那一筆讀得到、群組也對得上(空的 /proc、沒有 stat 的其他系統)。"""
    pid, pgrp = os.getpid(), os.getpgrp()
    try:
        entries = list(PROC_ROOT.iterdir())
        own = os.readlink(PROC_ROOT / "self")
    except OSError:
        return None
    if own != str(pid) or pgrp == 0:
        return None
    mine = _stat_fields(PROC_ROOT / str(pid))
    if mine is None or mine[1] != pgrp:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        found = _stat_fields(entry)  # 讀的當下剛結束(None):不算
        if found is not None and found[1] == group and found[0] != "Z":
            return True
    return False


def _has_live_members(group: int) -> bool:
    """行程群組裡還有沒有活著的行程。Linux 看行程表(同群組、不是殭屍的才算活的);讀不到行程表時
    (macOS)對群組送 0 號訊號:macOS 上群組只剩還沒領回的殭屍時回 EPERM,群組不存在回 ESRCH,
    兩種都算沒有活的。"""
    members = _proc_group_members(group)
    if members is not None:
        return members
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


def _empty_group(pid: int) -> None:
    """殺整組 → 有上限地確認群組裡沒有活著的行程(主行程還沒領回,群組編號不會被重用)。"""
    _kill_group(pid)
    _exited_unreaped(pid, time.monotonic() + GROUP_EXIT_WAIT_SECONDS)
    limit = time.monotonic() + GROUP_EXIT_WAIT_SECONDS
    while _has_live_members(pid):
        if time.monotonic() > limit:
            log.error("claude 行程群組在 %s 秒內沒有全部結束", GROUP_EXIT_WAIT_SECONDS)
            break
        _kill_group(pid)
        time.sleep(0.02)


def _finish(process: subprocess.Popen[bytes], deadline: float) -> tuple[int, bool]:
    """清理順序([S939]):先只等不領回確認主行程結束(逾時就不等了)→ 殺整組 → 有上限地確認群組裡沒有
    活著的行程 → 最後才領回主行程。回(結束代碼, 是否逾時)。"""
    timed_out = not _exited_unreaped(process.pid, deadline)
    _empty_group(process.pid)
    return process.wait(), timed_out


# 呼叫途中會讓行程結束的訊號:Ctrl-C(SIGINT)、SIGTERM、關終端機或 ssh 斷線(SIGHUP)、SIGQUIT。
# claude 在自己的工作階段,收不到終端機的掛斷;主行程若直接死掉就留下孤兒,所以一律轉成例外走清理
STOP_SIGNALS = frozenset({signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT})
CONVERTED_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)  # SIGINT 本來就是例外


def _abandon(process: subprocess.Popen[bytes]) -> None:
    """等待途中被打斷(Ctrl-C、SIGTERM、SIGHUP、SIGQUIT、任何例外):照同樣的順序殺整組、確認空了、
    領回主行程。清理期間暫時擋住這幾個訊號(清完才送達),免得第二次中斷讓整組漏殺。"""
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        if process.returncode is None:  # 已經領回的不再碰(行程編號可能被重用)
            _empty_group(process.pid)
            process.wait()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


class CallTerminated(BaseException):
    """呼叫途中收到 SIGTERM、SIGHUP 或 SIGQUIT:轉成這個例外,走跟 Ctrl-C 同一條清理路徑,再往外丟。"""


@contextlib.contextmanager
def _stop_signals_as_exception() -> Iterator[None]:
    """呼叫期間把 SIGTERM、SIGHUP、SIGQUIT 轉成 `CallTerminated`,結束後還原(只在主執行緒呼叫,
    見 `run_claude`)。原本就是忽略(SIG_IGN,例如 nohup 跑的即時評估)的訊號不動:使用者就是要撐過
    斷線,照常跑完(代碼審第 3 輪)。"""

    def _raise(signum: int, _frame: object) -> None:
        raise CallTerminated(f"呼叫途中收到訊號 {signum}")

    previous = {number: signal.signal(number, _raise) for number in CONVERTED_SIGNALS
                if signal.getsignal(number) is not signal.SIG_IGN}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _spawn_and_wait(args: list[str], streams: tuple[IO[bytes], IO[bytes], IO[bytes]],
                    workdir: Path, child_env: Mapping[str, str],
                    timeout_seconds: float) -> tuple[int, bool]:
    """起 claude(新工作階段)並照 `_finish` 的順序等它、清理。起行程到進清理的 try 之間擋住中斷訊號:
    收到的話留到放開時才送達,那時已在 try 裡、會走 `_abandon`(代碼審第 2 輪)。"""
    stdin, stdout, stderr = streams
    masked = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        process = subprocess.Popen(  # noqa: S603 - 參數固定、不經 shell
            args, stdin=stdin, stdout=stdout, stderr=stderr, cwd=workdir,
            env=child_env, start_new_session=True)
    except OSError as failed:  # 找不到、不能執行、起不來
        signal.pthread_sigmask(signal.SIG_SETMASK, masked)
        raise ConfigError(f"claude 起不來({type(failed).__name__})",
                          sub_reason="cannot_start") from failed
    except BaseException:
        signal.pthread_sigmask(signal.SIG_SETMASK, masked)
        raise
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, masked)
        return _finish(process, time.monotonic() + timeout_seconds)
    except BaseException:
        _abandon(process)
        raise


def run_claude(args: list[str], stdin_text: str, env: Mapping[str, str], timeout_seconds: float,
               *, isolated_home: bool = False,
               home_files: Mapping[str, str] | None = None) -> tuple[int, bytes, bytes]:
    """在新的工作階段(新行程群組)跑一次 claude:工作目錄是新建的空暫存目錄,標準輸入、輸出與錯誤
    都是暫存檔(不用管線:孫行程繼承管線會把成功的呼叫拖到逾時);isolated_home 時子行程的 HOME 指向
    另一個新建的空暫存目錄。只准在主執行緒呼叫(背景執行緒是設定錯誤)。起不來、成功、非 0 結束、
    逾時、等待途中被打斷(Ctrl-C、SIGTERM 轉成的
    例外、任何例外)五條路徑都照 `_finish` 的順序清理,最後一定刪掉暫存目錄([S939])。起不來丟設定錯誤,
    逾時丟逾時,被打斷清完照原樣往外丟;回(結束代碼, 標準輸出, 標準錯誤)。"""
    if threading.current_thread() is not threading.main_thread():
        # 背景執行緒裝不了訊號處理器:主行程收到 SIGHUP 等就直接結束、留下 claude 子行程。直接拒絕
        # (確定沒起行程:設定錯誤、結算 0);要在背景跑模型呼叫就另起一個行程(代碼審第 3 輪)
        raise ConfigError("Claude Code 後端只能在主執行緒呼叫(背景執行緒請另起行程)",
                          sub_reason="not_main_thread")
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
              (files / "stderr").open("wb") as stderr, _stop_signals_as_exception()):
            returncode, timed_out = _spawn_and_wait(
                args, (stdin, stdout, stderr), workdir, child_env, timeout_seconds)
        if timed_out:
            raise ModelTimeout("模型呼叫逾時,已殺掉整個行程群組")
        return returncode, (files / "stdout").read_bytes(), (files / "stderr").read_bytes()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _tokens_of(usage: Mapping[str, Any]) -> tuple[int, int, int, int, int] | None:
    """用量欄位 →(輸入, 輸出, 5 分鐘快取寫入, 1 小時快取寫入, 快取讀取);任一欄讀不懂回 None。
    5 分鐘與 1 小時快取寫入沒有分開回報時整個算 1 小時(較貴,寧多不少);分開的數字加總小於總數時,
    差額也算 1 小時。"""
    counts = [_count(usage.get(name)) for name in ("input_tokens", "output_tokens")]
    cache_read = _count(usage.get("cache_read_input_tokens", 0))
    total = _count(usage.get("cache_creation_input_tokens", 0))  # 讀不懂就是 None,一律讀不懂
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        five, hour = (_count(split.get("ephemeral_5m_input_tokens", 0)),
                      _count(split.get("ephemeral_1h_input_tokens", 0)))
        if five is not None and hour is not None and total is not None:
            hour += max(0, total - five - hour)
    else:
        five, hour = 0, total
    values = [*counts, total, five, hour, cache_read]
    if any(value is None for value in values):
        return None
    first, second, _, third, fourth, fifth = (value or 0 for value in values)
    return first, second, third, fourth, fifth


def _usage_of(data: Mapping[str, Any], text: str) -> BackendReply | None:
    """讀得出用量或回報的花費就組成回應(失敗的回應也讀,結算時取較高者);兩者都讀不出回 None。
    回報的花費跟 token 數分開讀:用量缺欄時 token 數記空(`tokens_known` 為否),回報的花費照樣參與
    取較高者。"""
    reported = _nanousd_of(data.get("total_cost_usd"))
    usage = data.get("usage")
    tokens = _tokens_of(usage) if isinstance(usage, dict) else None
    if tokens is None:
        if reported is None:
            return None
        return BackendReply(text, 0, 0, 0, 0, 0, reported, tokens_known=False)
    return BackendReply(text, *tokens, reported)


def usage_of(data: Mapping[str, Any], text: str) -> BackendReply | None:
    """給實測命令列用的讀用量(同一套規則)。"""
    return _usage_of(data, text)


_HEAD = {"type": str, "subtype": str, "is_error": bool}


def _parse_output(returncode: int, stdout: bytes, stderr: bytes) -> dict[str, Any]:
    """第 2 步:標準輸出讀成 JSON 物件,而且至少有類型、子類型、是否錯誤三欄。讀不成時:結束代碼
    非 0 歸暫時性服務錯誤、無法可靠分類(例如參數改名、在解析參數就退出);結束代碼 0 才是讀不懂。
    標準錯誤可能有本機路徑或帳號:只寫本機日誌,不進子原因、花費帳與錄製檔。"""
    try:
        data = json.loads(stdout.decode("utf-8"))
    except ValueError:
        data = None
    if not isinstance(data, dict) or any(not isinstance(data.get(name), kind)
                                         for name, kind in _HEAD.items()):
        if stderr.strip():
            log.warning("claude 的標準錯誤(只留本機日誌):%s",
                        stderr.decode("utf-8", errors="replace").strip()[:2000])
        if returncode != 0:
            raise TransientServiceError(f"claude 結束代碼 {returncode},輸出讀不成 JSON",
                                        sub_reason="unparseable_exit", unclassified=True)
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
    (對話輪數 1、權限被拒清單空,[S936];不過就標工具使用)、子類型要是 success、欄位齊全
    ⑤結束代碼非 0 → 暫時性、無法可靠分類。"""
    data = _parse_output(returncode, stdout, stderr)
    if data["is_error"]:
        raise _reported_error(data)
    result = data.get("result")
    reply = _usage_of(data, result if isinstance(result, str) else "")
    turns, denials = data.get("num_turns"), data.get("permission_denials")
    if isinstance(turns, bool) or turns != 1 or denials != []:
        failure = UnreadableModelResponse("回應有工具使用的痕跡", sub_reason="tool_use",
                                          reply=reply)
        failure.tool_use = True
        log.error("claude 成功形狀的回應帶著工具使用的痕跡:評估應整批停下")
        raise failure
    if data["subtype"] != "success":
        raise UnreadableModelResponse("不是錯誤的回應,子類型卻不是 success",
                                      sub_reason="not_success", reply=reply)
    if not isinstance(result, str) or reply is None or not reply.tokens_known:
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

    def check_login(self, timeout_seconds: float = LOGIN_CHECK_TIMEOUT_SECONDS) -> None:
        """登入狀態檢查。期限是 10 秒與這次呼叫剩下時間的較小者;不論哪一個卡住都還沒呼叫模型,一律是
        設定錯誤(結算 0、評估不算送出、整批停下;代碼審第 2 輪)。"""
        args = [str(self._executable), "auth", "status", "--json"]
        budget = min(LOGIN_CHECK_TIMEOUT_SECONDS, timeout_seconds)
        try:
            code, stdout, _ = run_claude(args, "", self.child_env(1), budget,
                                         isolated_home=self.isolation is Isolation.EMPTY_HOME)
            status = json.loads(stdout.decode("utf-8"))
        except ModelTimeout as slow:
            if budget < LOGIN_CHECK_TIMEOUT_SECONDS:
                raise ConfigError("登入狀態檢查用完了這次呼叫的期限,沒有呼叫模型",
                                  sub_reason="login_check_timeout") from slow
            raise ConfigError("claude 登入狀態檢查逾時", sub_reason="not_logged_in") from slow
        except ValueError as bad:
            raise ConfigError("claude 登入狀態讀不懂", sub_reason="not_logged_in") from bad
        if code != 0 or not isinstance(status, dict) or status.get("loggedIn") is not True:
            raise ConfigError("claude 沒登入", sub_reason="not_logged_in")

    def send(self, call: BackendCall) -> BackendReply:
        """登入檢查(第一次)花掉的時間從這次呼叫的總期限扣掉。"""
        deadline = time.monotonic() + call.timeout_seconds
        if not self._logged_in:
            self.check_login(call.timeout_seconds)
            self._logged_in = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConfigError("登入狀態檢查用完了這次呼叫的期限,沒有呼叫模型",
                              sub_reason="login_check_timeout")
        code, stdout, stderr = run_claude(
            self.command(call), call.user, self.child_env(call.max_output_tokens),
            remaining, isolated_home=self.isolation is Isolation.EMPTY_HOME)
        return judge_output(code, stdout, stderr)
