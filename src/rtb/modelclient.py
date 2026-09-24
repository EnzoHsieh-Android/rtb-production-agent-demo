"""模型用戶端(Phase 11B 增量 1,計劃第 7 版):後端介面、Claude Code 後端、錄製讀寫、花費帳寫入。

一個對外函式 `call_model`:給呼叫者、展示編號、批次、系統提示、使用者內容、輸出上限、逾時,
回文字、來源、token 數、原價、延遲與結算狀態。後端是「送出一次呼叫」的介面
(`ModelBackend.send`),這一版的即時後端是本機 Claude Code 的非互動模式(`ClaudeCodeBackend`);
將來的 API 後端實作同一個介面,三個接入點不用改。錄製重播不經後端:它要帶回錄製當時的結果類別與花費。

本模組不讀環境變數、不查 PATH:模式由三支模型入口讀好(即時開關、展示編號、入口在 PATH 上找到的
claude 絕對路徑)經 `settings_from_env` 組成設定往下傳。時間一律用系統時鐘(`_utc_now`),
沒有參數能把「現在」傳進來([S926])。

錄製:檔名是呼叫者、模型、系統提示、使用者內容、輸出上限正規化後的 sha256(不含後端種類);
錄製模式找不到就丟「沒有錄製」,不會退去即時呼叫。即時加錄製模式下,同一批已有同一個鍵就直接讀、
不呼叫;別的批次已有就呼叫前拒絕,不花額度、不覆寫([S931])。

花費帳(金額單位:十億分之一美元,整數):即時呼叫前在單一寫入交易裡讀出這個展示編號與本月的已用、
加上這次的預留,超過 1 美元或 20 美元就不呼叫、丟「本地上限拒絕」(給人看的是「已達上限」)。
預留 =(系統提示加使用者內容的 UTF-8 位元組數,加 Claude Code 自己附加的固定輸入)乘三種輸入單價
的最高者,加輸出上限乘輸出單價,再乘安全係數 1.2;這一筆預留的原價(不含係數)當 Claude Code 的
單次花費上限傳進去。結算:成功取 Claude Code 回報的花費估計與「token 數(含快取)乘價目表」的
較高者([S940]);失敗但讀得出用量時取「預留」與「原價乘 1.2」的較高者,讀不出就照預留;
本地上限拒絕與設定錯誤結算 0;不論成敗,入帳大於預留就照實記、標超支並印錯誤([S934])。
結算寫不進去,那筆留在「未結算」,照預留金額算進它預留時所在的月份。回傳值與每個例外都帶結算狀態
(已結算、未結算、超支;沒有預留就沒有結算狀態)。
"""

import contextlib
import hashlib
import json
import logging
import os
import plistlib
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol

from rtb.modelledger_view import CALLS_SELECT, LedgerCall, ledger_path
from rtb.modelledger_view import Backend as Backend  # 三個封閉列舉住在唯讀開法,這裡轉手給呼叫端
from rtb.modelledger_view import Caller as Caller
from rtb.modelledger_view import Outcome as Outcome
from rtb.modelledger_view import Source as Source
from rtb.sqlitekit import DatabaseBusy, connect, immediate_transaction, read_snapshot

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
LIVE_ENV = "RTB_MODEL_LIVE"
RECORD_ENV = "RTB_MODEL_RECORD"
MODEL_ENV = "RTB_MODEL"

NANOUSD_PER_USD = 10**9
PER_MTOK = NANOUSD_PER_USD // 10**6  # 每百萬 token 1 美元 = 每 token 1000 個十億分之一美元


@dataclass(frozen=True)
class Price:
    """每 token 的單價(十億分之一美元)。"""

    input_nanousd: int
    output_nanousd: int
    cache_write_5m_nanousd: int
    cache_write_1h_nanousd: int
    cache_read_nanousd: int

    @property
    def worst_input_nanousd(self) -> int:
        """預留用的輸入單價:一般輸入、5 分鐘與 1 小時快取寫入三者最高。"""
        return max(self.input_nanousd, self.cache_write_5m_nanousd, self.cache_write_1h_nanousd)


# 價目表:照官方價目頁填,查核日期超過 90 天即時模式拒絕啟動([S927])。
# 2026-09-24 查官方頁:Claude Sonnet 5 基本輸入 2 美元、輸出 10 美元、5 分鐘快取寫入 2.5 美元、
# 1 小時快取寫入 4 美元、快取讀取 0.2 美元(每百萬 token;腳註寫明原訂 9/1 調到 3/15 取消,
# 2/10 是正式價)
PRICE_PAGE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICES_CHECKED_ON = date(2026, 9, 24)
PRICES: Mapping[str, Price] = {
    "claude-sonnet-5": Price(2 * PER_MTOK, 10 * PER_MTOK, PER_MTOK * 5 // 2, 4 * PER_MTOK,
                             PER_MTOK // 5)}
PRICE_MAX_AGE = timedelta(days=90)
# 使用者 2026-09-24 裁定的上限與協調者定的安全係數;改它們要走設計審
DEMO_CAP_NANOUSD = 1 * NANOUSD_PER_USD
MONTH_CAP_NANOUSD = 20 * NANOUSD_PER_USD
SAFETY_FACTOR = (6, 5)  # 1.2,用分數避免浮點
# Claude Code 自己附加的固定輸入 token 數:錄製前本機實測⑥量出後改成實測值,量出來之前先用 4000
CLAUDE_FIXED_INPUT_TOKENS = 4000
MAX_TIMEOUT_SECONDS = 120.0
LONGEST_REQUEST = timedelta(seconds=MAX_TIMEOUT_SECONDS) + timedelta(minutes=5)  # 核銷用
MAX_OUTPUT_TOKENS = 32_000
MAX_PROMPT_BYTES = 48 * 1024  # 系統提示加使用者內容
COST_MISMATCH = (1, 5)  # 回報的估計與 token 數算的差超過較小者的兩成就標記

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


def _utc_now() -> datetime:
    """系統時鐘(UTC)。花費帳的月份與價目表期限只看它([S926]);測試替換這一支。"""
    return datetime.now(UTC)


class Mode(StrEnum):
    LIVE = "live"
    RECORDED = "recorded"


# ---- 請求、結果、例外 ----
class SettlementState(StrEnum):
    """一筆呼叫在花費帳裡的結算狀態;沒有預留(呼叫前就擋下、花費帳忙碌)就沒有狀態(None)。"""

    SETTLED = "settled"
    UNSETTLED = "unsettled"  # 結算寫不進去:照預留金額算進已用
    OVERRUN = "overrun"  # 入帳大於預留,或 Claude Code 回報超過單次花費上限([S934])


@dataclass(frozen=True)
class ModelRequest:
    caller: Caller
    system: str
    user: str
    max_output_tokens: int
    timeout_seconds: float
    demo_id: str | None = None  # 即時模式必填;錄製模式可空
    batch_id: str | None = None  # 只有評估用;即時加錄製模式必填


@dataclass(frozen=True)
class ModelResult:
    text: str
    source: Source
    input_tokens: int
    output_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    cache_read_tokens: int
    list_nanousd: int  # 原價(不含安全係數);重播時是錄製當時的原價,花費帳照記 0 元
    latency_ms: float
    key: str
    batch_id: str | None  # 即時:這次的批次;重播:錄製檔的批次
    settlement: SettlementState = SettlementState.SETTLED
    shared: bool = False  # 同一批已有同一個鍵、直接讀它,沒有呼叫


class ModelCallFailed(Exception):
    """模型呼叫失敗;`outcome` 是花費帳記的結果類別,`settlement` 是結算狀態。訊息不含請求內容。"""

    outcome: ClassVar[Outcome]

    def __init__(self, message: str, *, sub_reason: str | None = None,
                 recording_batch_id: str | None = None, reply: BackendReply | None = None,
                 unclassified: bool = False) -> None:
        super().__init__(message)
        self.sub_reason = sub_reason
        self.recording_batch_id = recording_batch_id
        self.reply = reply  # 失敗的回應讀得出的用量與花費估計(讀不出就是 None)
        self.unclassified = unclassified  # 暫時性服務錯誤認不出子類型:評估保守停下
        self.tool_use = False  # 錯誤回應帶著工具使用痕跡:評估保守停下
        self.settlement: SettlementState | None = None
        self.list_nanousd = 0  # 這次(或錄製當時)記的原價;即時失敗照結算規則填
        self.latency_ms: float | None = None


class ModelTimeout(ModelCallFailed, TimeoutError):
    outcome = Outcome.TIMEOUT


class LocalCapRefused(ModelCallFailed):
    outcome = Outcome.LOCAL_CAP_REFUSED


class QuotaExhausted(ModelCallFailed):
    outcome = Outcome.QUOTA_EXHAUSTED


class Overrun(ModelCallFailed):
    """Claude Code 回報超過單次花費上限(已經呼叫)。"""

    outcome = Outcome.OVERRUN


class NoRecording(ModelCallFailed):
    outcome = Outcome.NO_RECORDING


class UnreadableModelResponse(ModelCallFailed):
    outcome = Outcome.UNREADABLE


class ConfigError(ModelCallFailed):
    """確定還沒呼叫模型:執行檔找不到或起不來、沒登入、不認得的模型、呼叫前的本地檢查。"""

    outcome = Outcome.CONFIG_ERROR


class RecordingConflict(ConfigError):
    """即時加錄製:別的批次已經有同一個鍵。呼叫前拒絕,不花額度、不覆寫、不記帳。"""


class TransientServiceError(ModelCallFailed):
    outcome = Outcome.TRANSIENT


class LedgerBusy(ModelCallFailed):
    """預留時花費帳忙碌:沒有呼叫、寫不進帳。結算時忙碌不丟這個(照常回文字、留未結算)。"""

    outcome = Outcome.LEDGER_BUSY


_BY_OUTCOME: Mapping[Outcome, type[ModelCallFailed]] = {
    cls.outcome: cls for cls in (ModelTimeout, LocalCapRefused, QuotaExhausted, Overrun,
                                 NoRecording, UnreadableModelResponse, ConfigError,
                                 TransientServiceError, LedgerBusy)}
_FREE = frozenset({Outcome.LOCAL_CAP_REFUSED, Outcome.CONFIG_ERROR})  # 確定沒呼叫模型:結算 0


# ---- 後端介面 ----
@dataclass(frozen=True)
class BackendCall:
    model: str
    system: str
    user: str
    max_output_tokens: int
    timeout_seconds: float
    budget_nanousd: int  # 這一筆預留的原價,當單次花費上限


@dataclass(frozen=True)
class BackendReply:
    text: str
    input_tokens: int
    output_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    cache_read_tokens: int
    reported_nanousd: int | None  # 後端自己回報的花費估計(沒有就是 None)


class ModelBackend(Protocol):
    """送出一次呼叫。失敗丟 `ModelCallFailed` 的子類別。"""

    kind: Backend

    def send(self, call: BackendCall) -> BackendReply: ...


@dataclass(frozen=True)
class Settings:
    mode: Mode
    model: str
    record: bool
    backend: ModelBackend | None  # 即時模式才有
    notices: tuple[str, ...]


class UnknownModel(ValueError):
    """RTB_MODEL 指定的模型不在價目表裡。"""


def price_table_stale() -> bool:
    return _utc_now().date() - PRICES_CHECKED_ON > PRICE_MAX_AGE


def settings_from_env(environ: Mapping[str, str], demo_id: str | None,
                      claude: Path | str | None) -> Settings:
    """三支模型入口共用的模式判定:RTB_MODEL_LIVE=1、展示編號、入口在 PATH 上找到的 claude 絕對路徑、
    而且即時模式啟用紀錄有效([S942]),才是即時;少任一樣就錄製(印原因)。"""
    model = environ.get(MODEL_ENV) or DEFAULT_MODEL
    if model not in PRICES:
        raise UnknownModel(f"{MODEL_ENV}={model} 不在價目表裡(只收:{', '.join(PRICES)})")
    if environ.get(LIVE_ENV) != "1" or claude is None:
        return Settings(Mode.RECORDED, model, record=False, backend=None, notices=())
    refusal, isolation = _live_refusal(environ, demo_id, Path(claude))
    if refusal is not None or isolation is None:
        return Settings(Mode.RECORDED, model, False, None, (refusal or "即時模式拒絕啟動",))
    return Settings(Mode.LIVE, model, environ.get(RECORD_ENV) == "1",
                    ClaudeCodeBackend(Path(claude), environ, isolation), ())


def _live_refusal(environ: Mapping[str, str], demo_id: str | None,
                  claude: Path) -> tuple[str | None, Isolation | None]:
    """即時的其餘前提,依序檢查;回(拒絕原因, 啟用紀錄的隔離方式)。"""
    if not demo_id:
        return "即時模式需要展示編號,這次改用錄製", None
    if price_table_stale():
        return (f"價目表查核日期 {PRICES_CHECKED_ON} 已超過 90 天,即時模式拒絕啟動,這次改用錄製;"
                f"請照官方價目頁 {PRICE_PAGE} 重新核對後更新"), None
    policy = managed_policy_problem()
    if policy is not None:
        return f"{policy};安全模式不保證略過管理政策,即時模式拒絕啟動,這次改用錄製", None
    problem, isolation = verification_problem(claude, environ)
    if problem is not None:
        return f"{problem};即時模式不開,這次改用錄製(要先跑實測命令列)", None
    if isolation is Isolation.REAL_HOME:
        memory = memory_problem()
        if memory is not None:
            return f"{memory};真 HOME 隔離下有記憶內容,即時模式拒絕啟動,這次改用錄製", None
    return None, isolation


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


def default_recordings_dir() -> Path:
    """錄製目錄的預設值錨在專案根的 recordings/model/(從這支檔往上找 pyproject.toml),從別的工作目錄
    啟動照樣找得到([S920])。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent / "recordings" / "model"
    return here.parents[2] / "recordings" / "model"


def live_ledger_path() -> Path:
    return ledger_path()


# ---- 錄製鍵、預留、佔位符 ----
def _normalized(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def recording_key(caller: Caller, model: str, system: str, user: str,
                  max_output_tokens: int) -> str:
    """錄製鍵只看內容(不看展示編號、批次、時間),同一個情境重跑得到同一個鍵([S921])。"""
    canonical = json.dumps([Caller(caller).value, model, _normalized(system), _normalized(user),
                            int(max_output_tokens)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _with_factor(nanousd: int) -> int:
    numerator, denominator = SAFETY_FACTOR
    return -(-nanousd * numerator // denominator)  # 無條件進位


def _list_price(model: str, reply: BackendReply) -> int:
    """token 數(含三種快取)乘價目表。"""
    price = PRICES[model]
    return (reply.input_tokens * price.input_nanousd + reply.output_tokens * price.output_nanousd
            + reply.cache_write_5m_tokens * price.cache_write_5m_nanousd
            + reply.cache_write_1h_tokens * price.cache_write_1h_nanousd
            + reply.cache_read_tokens * price.cache_read_nanousd)


def call_budget_nanousd(request: ModelRequest, model: str) -> int:
    """這一筆預留的原價(不含安全係數):輸入上限是系統提示加使用者內容的 UTF-8 位元組數
    (token 數不會超過它)加 Claude Code 固定附加的輸入,乘三種輸入單價的最高者;加輸出上限乘
    輸出單價。它也是傳給 Claude Code 的單次花費上限。"""
    price = PRICES[model]
    input_bound = len((request.system + request.user).encode("utf-8")) + CLAUDE_FIXED_INPUT_TOKENS
    return (input_bound * price.worst_input_nanousd
            + request.max_output_tokens * price.output_nanousd)


def reservation_nanousd(request: ModelRequest, model: str) -> int:
    """預留的最壞花費:這一筆的原價乘安全係數 1.2。"""
    return _with_factor(call_budget_nanousd(request, model))


_STEMS = "甲乙丙丁戊己庚辛壬癸"


class Placeholders:
    """把真實編號換成依出現順序的佔位符(任務甲、任務乙…第 11 個起是甲2、乙2…)。對照表只留在本機;
    模型輸出驗證通過之後才用 `restore` 換回真實編號。"""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._by_real: dict[str, str] = {}

    def substitute(self, real: str) -> str:
        if real not in self._by_real:
            n = len(self._by_real)
            suffix = _STEMS[n % 10] + ("" if n < 10 else str(n // 10 + 1))
            self._by_real[real] = self._prefix + suffix
        return self._by_real[real]

    def restore(self, text: str) -> str:
        if not self._by_real:
            return text
        by_placeholder = {v: k for k, v in self._by_real.items()}
        pattern = "|".join(re.escape(p) for p in sorted(by_placeholder, key=len, reverse=True))
        return re.sub(pattern, lambda match: by_placeholder[match.group(0)], text)


# ---- Claude Code 後端 ----
def _usd_text(nanousd: int) -> str:
    return format(Decimal(nanousd) / NANOUSD_PER_USD, "f")


def _nanousd_of(value: object) -> int | None:
    """回報的美元金額 → 十億分之一美元(無條件進位);不是有限、不為負的數字就 None。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    amount = Decimal(repr(value)) * NANOUSD_PER_USD
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
            failure = _BY_OUTCOME[sample.outcome](f"claude 回報錯誤:{sample.sub_reason}",
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


# ---- 花費帳 ----
_NO_CHANGE = "SELECT RAISE(ABORT, '花費帳只增不改')"
SCHEMA = "\n".join([
    "CREATE TABLE IF NOT EXISTS model_reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "reserved_at TEXT NOT NULL, month TEXT NOT NULL, demo_id TEXT, batch_id TEXT, "
    "caller TEXT NOT NULL, model TEXT NOT NULL, backend TEXT NOT NULL, source TEXT NOT NULL, "
    "reserved_nanousd INTEGER NOT NULL, timeout_seconds REAL NOT NULL);",
    "CREATE TABLE IF NOT EXISTS model_settlements (reservation_id INTEGER PRIMARY KEY "
    "REFERENCES model_reservations(id), settled_at TEXT NOT NULL, outcome TEXT NOT NULL, "
    "sub_reason TEXT, input_tokens INTEGER, output_tokens INTEGER, "
    "cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER, cache_read_tokens INTEGER, "
    "reported_nanousd INTEGER, "
    "list_nanousd INTEGER NOT NULL, settled_nanousd INTEGER NOT NULL, "
    "by_reservation INTEGER NOT NULL, overrun INTEGER NOT NULL, "
    "cost_mismatch INTEGER NOT NULL, latency_ms REAL);",
    "CREATE TABLE IF NOT EXISTS model_write_offs (reservation_id INTEGER PRIMARY KEY "
    "REFERENCES model_reservations(id), written_at TEXT NOT NULL, "
    "amount_nanousd INTEGER NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL, "
    "settled_before INTEGER NOT NULL);",
    "CREATE INDEX IF NOT EXISTS model_reservations_by_month ON model_reservations(month);",
    "CREATE INDEX IF NOT EXISTS model_reservations_by_demo ON model_reservations(demo_id);",
    *(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
      f"BEGIN {_NO_CHANGE}; END;"
      for table in ("model_reservations", "model_settlements", "model_write_offs")
      for verb in ("UPDATE", "DELETE")),
])


@dataclass(frozen=True)
class _Settlement:
    outcome: Outcome
    sub_reason: str | None
    reply: BackendReply | None
    list_nanousd: int
    settled_nanousd: int
    by_reservation: bool
    overrun: bool
    cost_mismatch: bool
    latency_ms: float | None


def _open(ledger: Path) -> sqlite3.Connection:
    path = Path(ledger)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        conn = connect(path)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_write_offs'"
                            ).fetchone() is None:
                conn.executescript(SCHEMA)  # 只在第一次建表,平常開帳不搶寫入鎖
        except BaseException:
            conn.close()
            raise
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,沒有呼叫") from busy
    except sqlite3.OperationalError as locked:
        if "locked" in str(locked):
            raise LedgerBusy("花費帳忙碌,沒有呼叫") from locked
        raise
    return conn


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _calls(conn: sqlite3.Connection, where: str, params: tuple[object, ...]) -> list[LedgerCall]:
    return [LedgerCall.from_row(row) for row in conn.execute(CALLS_SELECT + where, params)]


def _used(conn: sqlite3.Connection, demo_id: str | None, month: str) -> tuple[int, int]:
    in_month = sum(c.effective_nanousd for c in _calls(conn, "WHERE r.month = ?", (month,)))
    for_demo = 0 if demo_id is None else sum(
        c.effective_nanousd for c in _calls(conn, "WHERE r.demo_id = ?", (demo_id,)))
    return for_demo, in_month


@dataclass(frozen=True)
class Spent:
    month: str
    demo_nanousd: int
    month_nanousd: int


def used_so_far(ledger: Path, demo_id: str | None) -> Spent:
    """這個展示編號與本月(系統時鐘的 UTC 月曆月)的已用。"""
    month = _utc_now().strftime("%Y-%m")
    conn = _open(ledger)
    try:
        with read_snapshot(conn):
            for_demo, in_month = _used(conn, demo_id, month)
    finally:
        conn.close()
    return Spent(month, for_demo, in_month)


def _insert_reservation(conn: sqlite3.Connection, request: ModelRequest, model: str,
                        backend: Backend, amount: int) -> int:
    now = _utc_now()
    source = Source.RECORDED if backend is Backend.RECORDING else Source.LIVE
    cursor = conn.execute(
        "INSERT INTO model_reservations (reserved_at, month, demo_id, batch_id, caller, model, "
        "backend, source, reserved_nanousd, timeout_seconds) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_iso(now), now.strftime("%Y-%m"), request.demo_id, request.batch_id,
         Caller(request.caller).value, model, backend.value, source.value, amount,
         request.timeout_seconds))
    if cursor.lastrowid is None:
        raise AssertionError("新增預留列沒有拿到編號")
    return cursor.lastrowid


def _insert_settlement(conn: sqlite3.Connection, reservation_id: int, done: _Settlement) -> None:
    reply = done.reply
    conn.execute(
        "INSERT INTO model_settlements (reservation_id, settled_at, outcome, sub_reason, "
        "input_tokens, output_tokens, cache_write_5m_tokens, cache_write_1h_tokens, "
        "cache_read_tokens, reported_nanousd, list_nanousd, settled_nanousd, by_reservation, "
        "overrun, cost_mismatch, latency_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (reservation_id, _iso(_utc_now()), done.outcome.value, done.sub_reason,
         *((None,) * 6 if reply is None else (
             reply.input_tokens, reply.output_tokens, reply.cache_write_5m_tokens,
             reply.cache_write_1h_tokens, reply.cache_read_tokens, reply.reported_nanousd)),
         done.list_nanousd, done.settled_nanousd, int(done.by_reservation), int(done.overrun),
         int(done.cost_mismatch), done.latency_ms))


def _zero(outcome: Outcome, latency_ms: float | None = None, sub_reason: str | None = None,
          reply: BackendReply | None = None) -> _Settlement:
    return _Settlement(outcome, sub_reason, reply, 0, 0, False, False, False, latency_ms)


def _book(ledger: Path, request: ModelRequest, model: str, done: _Settlement) -> None:
    """錄製重播與沒有錄製:預留 0 元與結算寫在同一個交易(來源記錄製)。"""
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            reservation = _insert_reservation(conn, request, model, Backend.RECORDING, 0)
            _insert_settlement(conn, reservation, done)
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,這筆沒記進帳") from busy
    finally:
        conn.close()


def _reserve(ledger: Path, request: ModelRequest, model: str, backend: Backend) -> int:
    """單一寫入交易裡讀已用、加預留、判上限、寫預留列([S902]、[S903])。超過上限記一筆 0 元的
    「本地上限拒絕」並丟例外,不呼叫。"""
    amount = reservation_nanousd(request, model)
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            for_demo, in_month = _used(conn, request.demo_id, _utc_now().strftime("%Y-%m"))
            over = (for_demo + amount > DEMO_CAP_NANOUSD) or (
                in_month + amount > MONTH_CAP_NANOUSD)
            if over:
                refused = _insert_reservation(conn, request, model, backend, 0)
                _insert_settlement(conn, refused, _zero(Outcome.LOCAL_CAP_REFUSED))
                reservation_id = None
            else:
                reservation_id = _insert_reservation(conn, request, model, backend, amount)
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,沒有呼叫") from busy
    finally:
        conn.close()
    if reservation_id is None:
        raise LocalCapRefused(
            f"已達上限:這次展示已用 {for_demo / NANOUSD_PER_USD:.4f} 美元、本月已用 "
            f"{in_month / NANOUSD_PER_USD:.4f} 美元,加上這次預留 {amount / NANOUSD_PER_USD:.4f} "
            "美元會超過每次展示 1 美元或每月 20 美元,沒有呼叫")
    return reservation_id


def _settle(ledger: Path, reservation_id: int, done: _Settlement) -> bool:
    """寫結算列;回傳這筆是不是在人工核銷之後才來的結算。"""
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            written_off = conn.execute(
                "SELECT 1 FROM model_write_offs WHERE reservation_id = ?",
                (reservation_id,)).fetchone() is not None
            _insert_settlement(conn, reservation_id, done)
    finally:
        conn.close()
    return written_off


# ---- 錄製檔 ----
@dataclass(frozen=True)
class Recording:
    key: str
    caller: str
    model: str
    backend: str
    batch_id: str | None
    recorded_on: str
    outcome: str
    sub_reason: str | None
    text: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_write_5m_tokens: int | None
    cache_write_1h_tokens: int | None
    cache_read_tokens: int | None
    reported_nanousd: int | None
    list_nanousd: int
    latency_ms: float


def _load_recording(path: Path) -> Recording | None:
    if not path.is_file():
        return None
    try:
        recording = Recording(**json.loads(path.read_text(encoding="utf-8")))
        Outcome(recording.outcome)
    except (ValueError, TypeError) as bad:
        raise NoRecording(f"錄製檔讀不懂:{path.name}") from bad
    return recording


def _save_recording(path: Path, recording: Recording) -> None:
    """只新建、不覆寫:先寫暫存檔,再用硬連結原子地放到正式檔名(已存在就失敗)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(asdict(recording), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    try:
        os.link(temporary, path)
    except FileExistsError as exists:
        raise RecordingConflict("錄製檔已存在,不覆寫") from exists
    finally:
        temporary.unlink()


# ---- 結算規則 ----
def _mismatch(reported: int | None, computed: int) -> bool:
    if reported is None:
        return False
    numerator, denominator = COST_MISMATCH
    return abs(reported - computed) * denominator > min(reported, computed) * numerator


def _settlement_for(outcome: tuple[BackendReply | None, ModelCallFailed | None], model: str,
                    reserved: int, latency_ms: float) -> _Settlement:
    reply, failure = outcome
    if failure is not None and failure.outcome in _FREE:  # 確定沒呼叫模型:封閉集合,結算 0
        return _zero(failure.outcome, latency_ms, failure.sub_reason)
    usage = reply if failure is None else failure.reply
    computed = None if usage is None else _list_price(model, usage)
    listed = None if usage is None or computed is None else max(
        computed, usage.reported_nanousd or 0)  # 取高者([S940])
    mismatch = usage is not None and computed is not None and _mismatch(
        usage.reported_nanousd, computed)
    if failure is None:
        if listed is None:
            raise AssertionError("成功一定有用量")
        settled = _with_factor(listed)
        return _Settlement(Outcome.OK, None, reply, listed, settled, False, settled > reserved,
                           mismatch, latency_ms)
    # 其他失敗:讀得出用量就取「預留」與「原價乘 1.2」較高者,讀不出照預留(寧多不少);原價欄同理
    charged = reserved if listed is None else max(reserved, _with_factor(listed))
    overrun = charged > reserved or failure.outcome is Outcome.OVERRUN
    return _Settlement(failure.outcome, failure.sub_reason, usage,
                       reserved if listed is None else max(reserved, listed), charged,
                       charged == reserved, overrun, mismatch, latency_ms)


# ---- 對外的一個函式 ----
def _check_request(request: ModelRequest, settings: Settings) -> None:
    if not isinstance(request.caller, Caller):
        raise ValueError("呼叫者必須是封閉列舉的成員")
    if settings.model not in PRICES:
        raise UnknownModel(f"{settings.model} 不在價目表裡")
    if not 0 < request.timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"逾時要在 0 到 {MAX_TIMEOUT_SECONDS} 秒之間")
    if not 1 <= request.max_output_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError(f"輸出上限要在 1 到 {MAX_OUTPUT_TOKENS} 之間")
    if len((request.system + request.user).encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError(f"系統提示加使用者內容超過 {MAX_PROMPT_BYTES} 位元組")


def _check_live(request: ModelRequest, settings: Settings) -> ModelBackend:
    if price_table_stale():
        raise ConfigError(f"價目表查核日期 {PRICES_CHECKED_ON} 已超過 90 天,拒絕即時呼叫",
                          sub_reason="local_check")
    if not request.demo_id:
        raise ConfigError("即時模式需要展示編號", sub_reason="local_check")
    if settings.backend is None:
        raise ConfigError("即時模式沒有後端", sub_reason="local_check")
    if settings.record and not request.batch_id:
        raise ValueError("即時加錄製模式要帶批次編號")
    return settings.backend


def _replay(request: ModelRequest, model: str, path: Path, ledger: Path,
            shared: bool) -> ModelResult:
    try:
        recording = _load_recording(path)
    except NoRecording:
        _book(ledger, request, model, _zero(Outcome.NO_RECORDING))
        raise
    if recording is None:
        _book(ledger, request, model, _zero(Outcome.NO_RECORDING))
        raise NoRecording("找不到對應的錄製回應(不會改走即時呼叫)")
    outcome = Outcome(recording.outcome)
    _book(ledger, request, model, _zero(outcome, recording.latency_ms, recording.sub_reason))
    if outcome is not Outcome.OK or recording.text is None:
        failure = _BY_OUTCOME[outcome](f"錄製的結果:{outcome.value}",
                                       sub_reason=recording.sub_reason,
                                       recording_batch_id=recording.batch_id)
        failure.list_nanousd, failure.latency_ms = recording.list_nanousd, recording.latency_ms
        failure.settlement = SettlementState.SETTLED  # 重播記 0 元,當場結算
        failure.unclassified = recording.sub_reason == "unclassified"
        raise failure
    return ModelResult(recording.text, Source.RECORDED, recording.input_tokens or 0,
                       recording.output_tokens or 0, recording.cache_write_5m_tokens or 0,
                       recording.cache_write_1h_tokens or 0, recording.cache_read_tokens or 0,
                       recording.list_nanousd, recording.latency_ms, recording.key,
                       recording.batch_id, shared=shared)


def _send(backend: ModelBackend, call: BackendCall) -> tuple[
        BackendReply | None, ModelCallFailed | None]:
    try:
        return backend.send(call), None
    except ModelCallFailed as failed:
        return None, failed
    except Exception as unexpected:  # 後端的意外錯誤:暫時性、無法可靠分類、照預留結算
        failure = TransientServiceError(f"後端意外錯誤({type(unexpected).__name__})",
                                        sub_reason="unclassified", unclassified=True)
        failure.__cause__ = unexpected
        return None, failure


def _live(request: ModelRequest, settings: Settings, key: str, path: Path,
          ledger: Path) -> ModelResult:
    backend = _check_live(request, settings)
    if settings.record:
        existing = _load_recording(path)
        if existing is not None:
            if existing.batch_id == request.batch_id:
                return _replay(request, settings.model, path, ledger, shared=True)
            raise RecordingConflict("別的批次已經錄過同一個鍵;換一批要整批重錄、舊批整批刪掉")
    reservation_id = _reserve(ledger, request, settings.model, backend.kind)
    reserved = reservation_nanousd(request, settings.model)
    call = BackendCall(settings.model, request.system, request.user, request.max_output_tokens,
                       request.timeout_seconds, call_budget_nanousd(request, settings.model))
    started = time.monotonic()
    reply, failure = _send(backend, call)
    latency_ms = (time.monotonic() - started) * 1000
    done = _settlement_for((reply, failure), settings.model, reserved, latency_ms)
    settled = _try_settle(ledger, reservation_id, done)
    if done.overrun:
        log.error("預留 %s 超支:入帳 %s 大於預留 %s,或 Claude Code 回報超過單次花費上限"
                  "(照實記帳);評估應整批停下", reservation_id, done.settled_nanousd, reserved)
    state = (SettlementState.OVERRUN if done.overrun else
             SettlementState.SETTLED if settled else SettlementState.UNSETTLED)
    if settings.record:
        _save_recording(path, _recording_of(key, request, settings.model, backend.kind, done))
    if failure is not None:
        failure.list_nanousd, failure.latency_ms = done.list_nanousd, latency_ms
        failure.settlement = state
        raise failure
    if reply is None:
        raise AssertionError("沒有失敗就一定有回應")
    return ModelResult(reply.text, Source.LIVE, reply.input_tokens, reply.output_tokens,
                       reply.cache_write_5m_tokens, reply.cache_write_1h_tokens,
                       reply.cache_read_tokens, done.list_nanousd, latency_ms, key,
                       request.batch_id, settlement=state)


def _recording_of(key: str, request: ModelRequest, model: str, backend: Backend,
                  done: _Settlement) -> Recording:
    reply = done.reply
    return Recording(
        key, Caller(request.caller).value, model, backend.value, request.batch_id,
        _utc_now().date().isoformat(), done.outcome.value, done.sub_reason,
        None if reply is None else reply.text, None if reply is None else reply.input_tokens,
        None if reply is None else reply.output_tokens,
        None if reply is None else reply.cache_write_5m_tokens,
        None if reply is None else reply.cache_write_1h_tokens,
        None if reply is None else reply.cache_read_tokens,
        None if reply is None else reply.reported_nanousd, done.list_nanousd,
        done.latency_ms or 0.0)


def _try_settle(ledger: Path, reservation_id: int, done: _Settlement) -> bool:
    try:
        late = _settle(ledger, reservation_id, done)
    except (DatabaseBusy, LedgerBusy, sqlite3.Error):
        log.warning("預留 %s 的結算寫不進花費帳,照預留金額算", reservation_id)
        return False
    if late:
        log.error("預留 %s 已被人工核銷,之後才來了結算(遲到的回應):已用改算兩者中較高的金額,"
                  "請對照本機紀錄", reservation_id)
    return True


def call_model(request: ModelRequest, settings: Settings, *, recordings_dir: Path,
               ledger: Path) -> ModelResult:
    """送出一次模型呼叫(或讀錄製)。失敗丟 `ModelCallFailed` 的子類別;每一次呼叫或讀取錄製都記一筆帳
    ([S907]);沒呼叫也沒讀到的(別的批次已錄、花費帳忙碌、呼叫前的本地檢查)不記。"""
    _check_request(request, settings)
    key = recording_key(request.caller, settings.model, request.system, request.user,
                        request.max_output_tokens)
    path = Path(recordings_dir) / f"{key}.json"
    if settings.mode is Mode.RECORDED:
        return _replay(request, settings.model, path, ledger, shared=False)
    return _live(request, settings, key, path, ledger)



# ---- 人工核銷 ----
class WriteOffRefused(ValueError):
    """核銷的條件不符;什麼都沒寫。"""


@dataclass(frozen=True)
class WriteOffDone:
    reservation_id: int
    amount_nanousd: int
    before_nanousd: int
    after_nanousd: int


def _nanousd(amount: Decimal) -> int:
    try:
        scaled = Decimal(amount) * NANOUSD_PER_USD
    except (InvalidOperation, TypeError) as bad:
        raise WriteOffRefused("金額看不懂") from bad
    if not scaled.is_finite() or scaled < 0:
        raise WriteOffRefused("金額要是不為負的有限數")
    return int(scaled.to_integral_value(rounding="ROUND_CEILING"))


def _check_write_off(call: LedgerCall) -> None:
    if call.write_off_nanousd is not None:
        raise WriteOffRefused(f"預留 {call.id} 已經核銷過,不能再核銷")
    if call.settled_nanousd is not None and not call.by_reservation:
        raise WriteOffRefused(f"預留 {call.id} 照實際 token 數或 0 元結算,不能核銷")
    if call.settled_nanousd is None:
        age = _utc_now() - datetime.fromisoformat(call.reserved_at)
        if age <= LONGEST_REQUEST:
            raise WriteOffRefused(f"預留 {call.id} 還在最長請求期限內,可能還在等回應,不能核銷")


def write_off(ledger: Path, reservation_id: int, amount: Decimal, reason: str,
              evidence: str) -> WriteOffDone:
    """人工核銷:只追加一列([S923])。只接受已照預留金額結算的列,或預留超過最長請求期限還沒結算的列;
    在單一寫入交易裡重查這筆仍符合條件才寫。原因與依據必填(訂閱後端沒有主控台帳單,依據寫本機紀錄:
    子行程結束狀態、錄製檔、Claude Code 的用量畫面)。"""
    if not reason.strip() or not evidence.strip():
        raise WriteOffRefused("核銷原因與依據都必填")
    nanousd = _nanousd(amount)
    conn = _open(ledger)
    try:
        with immediate_transaction(conn):
            found = _calls(conn, "WHERE r.id = ?", (reservation_id,))
            if not found:
                raise WriteOffRefused(f"找不到預留 {reservation_id}")
            call = found[0]
            _check_write_off(call)
            conn.execute(
                "INSERT INTO model_write_offs (reservation_id, written_at, amount_nanousd, reason, "
                "evidence, settled_before) VALUES (?, ?, ?, ?, ?, ?)",
                (reservation_id, _iso(_utc_now()), nanousd, reason.strip(), evidence.strip(),
                 int(call.settled_nanousd is not None)))
            after = _calls(conn, "WHERE r.id = ?", (reservation_id,))[0]
    except DatabaseBusy as busy:
        raise LedgerBusy("花費帳忙碌,沒有核銷") from busy
    finally:
        conn.close()
    return WriteOffDone(reservation_id, nanousd, call.effective_nanousd, after.effective_nanousd)
