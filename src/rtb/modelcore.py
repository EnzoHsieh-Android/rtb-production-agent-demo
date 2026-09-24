"""模型用戶端的共用詞彙(Phase 11B 增量 1):價目表與上限常數、系統時鐘、請求與結果、九類例外與
結算狀態、「送出一次呼叫」的後端介面與預留算法。後端、錄製、帳本、協調四支模組都從這裡取詞彙。

測試要換掉時鐘或上限時,替換這個模組上的名字(其他模組一律在呼叫時用 `core.名字` 讀)。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import ClassVar, Protocol

from rtb.modelledger_view import Backend as Backend  # 封閉列舉住在唯讀開法,這裡轉手給呼叫端
from rtb.modelledger_view import Caller as Caller
from rtb.modelledger_view import Outcome as Outcome
from rtb.modelledger_view import Source as Source

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

def utc_now() -> datetime:
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


BY_OUTCOME: Mapping[Outcome, type[ModelCallFailed]] = {
    cls.outcome: cls for cls in (ModelTimeout, LocalCapRefused, QuotaExhausted, Overrun,
                                 NoRecording, UnreadableModelResponse, ConfigError,
                                 TransientServiceError, LedgerBusy)}
# 確定沒呼叫模型:結算 0
FREE_OUTCOMES = frozenset({Outcome.LOCAL_CAP_REFUSED, Outcome.CONFIG_ERROR})


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


class UnknownModel(ValueError):
    """RTB_MODEL 指定的模型不在價目表裡。"""


def price_table_stale() -> bool:
    return utc_now().date() - PRICES_CHECKED_ON > PRICE_MAX_AGE


def with_factor(nanousd: int) -> int:
    numerator, denominator = SAFETY_FACTOR
    return -(-nanousd * numerator // denominator)  # 無條件進位


def list_price(model: str, reply: BackendReply) -> int:
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
    return with_factor(call_budget_nanousd(request, model))
