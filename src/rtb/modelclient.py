"""模型用戶端的協調(Phase 11B 增量 1):模式判定、結算規則與對外的一個函式 `call_model`。

共用詞彙(價目表、請求與結果、九類例外、後端介面、預留算法)在 modelcore,Claude Code 後端(唯一啟動
子行程的地方)在 modelclaude,錄製讀寫在 modelrecording,花費帳寫入在 modelledger;這支只把它們串起來,
並把共用詞彙轉手給三個接入點。

`call_model`:給呼叫者、展示編號、批次、系統提示、使用者內容、輸出上限、逾時,回文字、來源、token 數、
原價、延遲與結算狀態。將來的 API 後端實作同一個後端介面,三個接入點不用改。錄製重播不經後端:它要帶回
錄製當時的結果類別與花費。

本模組不讀環境變數、不查 PATH:模式由三支模型入口讀好(即時開關、展示編號、入口在 PATH 上找到的
claude 絕對路徑)經 `settings_from_env` 組成設定往下傳。時間一律用系統時鐘(`_utc_now`),
沒有參數能把「現在」傳進來([S926])。

錄製:檔名是呼叫者、模型、系統提示、使用者內容、輸出上限正規化後的 sha256(不含後端種類);
錄製模式找不到就丟「沒有錄製」,不會退去即時呼叫。即時加錄製模式下,同一批已有同一個鍵就直接讀、
不呼叫;別的批次已有就呼叫前拒絕,不花額度、不覆寫([S931])。

花費帳(金額單位:十億分之一美元,整數):即時呼叫前在單一寫入交易裡讀出這個展示編號與本月的已用、
加上這次的預留,超過 1 美元或 20 美元就不呼叫、丟「本地上限拒絕」(給人看的是「已達上限」)。
上限只管花費帳模組寫死的計入上限呼叫者,已用也只加總它們;Phase 13 的三個呼叫者照記估算成本、
不判上限([S1134])。
預留算法見 modelcore(一次請求的最壞花費,乘上撞頂後自動續寫的次數,再乘安全係數 1.2);這一筆預留的
原價(不含係數)當 Claude Code 的單次花費上限傳進去。回報的用量或花費大得離譜時照預留結算、標超支。
結算:成功取 Claude Code 回報的花費估計與「token 數(含快取)乘價目表」的
較高者([S940]);失敗但讀得出用量時取「預留」與「原價乘 1.2」的較高者,讀不出就照預留;
本地上限拒絕與設定錯誤結算 0;不論成敗,入帳大於預留就照實記、標超支並印錯誤([S934])。
結算寫不進去就有上限地重試,仍失敗那筆留在「未結算」,照預留金額算進它預留時所在的月份,應入帳金額
印到標準錯誤。回傳值與每個例外都帶結算狀態(已結算、未結算、超支、超支又未結算);呼叫前就擋下、
還沒預留的例外沒有結算狀態(None)。花費帳或錄製檔的非模型例外(SQLite、檔案系統、溢位)一律包成
暫時性、無法可靠分類,不讓原生例外漏出去。
"""

import logging
import re
import sqlite3
import sys
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from rtb import modelclaude as cc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelrecording as rec
from rtb.modelclaude import CallTerminated as CallTerminated  # 停止訊號:轉手給入口與閘道([S1154])
from rtb.modelcore import DEFAULT_MODEL as DEFAULT_MODEL
from rtb.modelcore import LIVE_ENV as LIVE_ENV
from rtb.modelcore import MODEL_ENV as MODEL_ENV
from rtb.modelcore import NANOUSD_PER_USD as NANOUSD_PER_USD
from rtb.modelcore import PRICE_PAGE as PRICE_PAGE
from rtb.modelcore import PRICES_CHECKED_ON as PRICES_CHECKED_ON
from rtb.modelcore import RECORD_ENV as RECORD_ENV
from rtb.modelcore import BackendCall as BackendCall  # 共用詞彙轉手給三個接入點(不轉手子行程的東西)
from rtb.modelcore import BackendReply as BackendReply
from rtb.modelcore import Caller as Caller
from rtb.modelcore import ConfigError as ConfigError
from rtb.modelcore import LedgerBusy as LedgerBusy
from rtb.modelcore import LocalCapRefused as LocalCapRefused
from rtb.modelcore import Mode as Mode
from rtb.modelcore import ModelCallFailed as ModelCallFailed
from rtb.modelcore import ModelRequest as ModelRequest
from rtb.modelcore import ModelResult as ModelResult
from rtb.modelcore import ModelTimeout as ModelTimeout
from rtb.modelcore import NoRecording as NoRecording
from rtb.modelcore import Outcome as Outcome
from rtb.modelcore import Overrun as Overrun
from rtb.modelcore import QuotaExhausted as QuotaExhausted
from rtb.modelcore import RecordingConflict as RecordingConflict
from rtb.modelcore import RecordingWriteFailed as RecordingWriteFailed
from rtb.modelcore import SettlementState as SettlementState
from rtb.modelcore import Source as Source
from rtb.modelcore import TransientServiceError as TransientServiceError
from rtb.modelcore import UnknownModel as UnknownModel
from rtb.modelcore import UnreadableModelResponse as UnreadableModelResponse
from rtb.modelledger_view import ledger_path
from rtb.modelrecording import MixedRecordingsDir as MixedRecordingsDir
from rtb.modelrecording import Placeholders as Placeholders
from rtb.modelrecording import check_recordings_dir as check_recordings_dir
from rtb.modelrecording import recording_key as recording_key
from rtb.modelrecording import validated as validated  # 錄製檔的共用驗證(評估批次驗收用)
from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS, DatabaseBusy

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    mode: core.Mode
    model: str
    record: bool
    backend: core.ModelBackend | None  # 即時模式才有
    notices: tuple[str, ...]


def settings_from_env(environ: Mapping[str, str], demo_id: str | None,
                      claude: Path | str | None) -> Settings:
    """三支模型入口共用的模式判定:RTB_MODEL_LIVE=1、展示編號、入口在 PATH 上找到的 claude 絕對路徑、
    而且即時模式啟用紀錄有效([S942]),才是即時;少任一樣就錄製(印原因)。"""
    model = environ.get(core.MODEL_ENV) or core.DEFAULT_MODEL
    if model not in core.PRICES:
        raise core.UnknownModel(
            f"{core.MODEL_ENV}={model} 不在價目表裡(只收:{', '.join(core.PRICES)})")
    if environ.get(core.LIVE_ENV) != "1" or claude is None:
        return Settings(core.Mode.RECORDED, model, record=False, backend=None, notices=())
    # claude 常是會被自動更新改指的符號連結:核版本時就解開、固定用那一支實體檔
    # (版本檢查與後端都用它);
    # 之後換版不會用到沒實測過的版本,那支檔被清掉就是起不來的設定錯誤(結算 0、評估停下)
    pinned = Path(claude).resolve()
    refusal, isolation = _live_refusal(environ, demo_id, pinned)
    if refusal is not None or isolation is None:
        return Settings(core.Mode.RECORDED, model, False, None, (refusal or "即時模式拒絕啟動",))
    return Settings(core.Mode.LIVE, model, environ.get(core.RECORD_ENV) == "1",
                    cc.ClaudeCodeBackend(pinned, environ, isolation), ())


def _live_refusal(environ: Mapping[str, str], demo_id: str | None,
                  claude: Path) -> tuple[str | None, cc.Isolation | None]:
    """即時的其餘前提,依序檢查;回(拒絕原因, 啟用紀錄的隔離方式)。"""
    if not demo_id:
        return "即時模式需要展示編號,這次改用錄製", None
    if core.price_table_stale():
        return (f"價目表查核日期 {core.PRICES_CHECKED_ON} 已超過 90 天,即時模式拒絕啟動,"
                "這次改用錄製;"
                f"請照官方價目頁 {core.PRICE_PAGE} 重新核對後更新"), None
    policy = cc.managed_policy_problem()
    if policy is not None:
        return f"{policy};安全模式不保證略過管理政策,即時模式拒絕啟動,這次改用錄製", None
    problem, isolation = cc.verification_problem(claude, environ)
    if problem is not None:
        return f"{problem};即時模式不開,這次改用錄製(要先跑實測命令列)", None
    if isolation is cc.Isolation.REAL_HOME:
        memory = cc.memory_problem()
        if memory is not None:
            return f"{memory};真 HOME 隔離下有記憶內容,即時模式拒絕啟動,這次改用錄製", None
    return None, isolation


def call_deadline_seconds(timeout_seconds: float) -> float:
    """一次 `call_model` 最壞要多久才回來:模型逾時,加第一次送出前的登入檢查、兩次等行程群組結束,
    以及預留一次、結算最多 SETTLE_ATTEMPTS 次的花費帳等鎖(各等資料庫忙碌逾時)。入口拿來核對自己的
    期限(說明命令列的領取期限);數字都取自真常數,不另寫一份。"""
    ledger_waits = (1 + ledger_db.SETTLE_ATTEMPTS) * BUSY_TIMEOUT_SECONDS
    return (timeout_seconds + cc.LOGIN_CHECK_TIMEOUT_SECONDS + 2 * cc.GROUP_EXIT_WAIT_SECONDS
            + ledger_waits)


# ---- 數字核對(代碼審 r3,協調者代使用者改裁定):只在出現「數詞」時判對不回,常用詞裡的數字字不算 ----
# 負號:半形、減號、全形、長破折號、各種連字號;負號前面緊接英數字時(t-1、3-5 這類)不算負號
_NEGATIVE = "-\u2212\uff0d\u2014\u2013\u2012\u2011\u2010"
# 數字:可帶千分位逗號、小數與科學記號(證據裡的 1e-05 是一個數,不拆成 1 與 5)
_NUMBER = re.compile(rf"(?:(?<![0-9A-Za-z])[{_NEGATIVE}])?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?"
                     r"(?:[eE][-+]?\d+)?")
# 句末標點:全形與半形的 。!?;(代碼審 r2:原本半形寫了兩次,全形的漏了)
_SENTENCE = re.compile(r"[^。!?;\uff01\uff1f\uff1b]*[。!?;\uff01\uff1f\uff1b]?")
# 中文與大寫數字字(繁、簡與常見異體)
_CJK_NUMERALS = ("零〇一二三四五六七八九十百千萬万億亿兆兩两倆俩仨廿卅卌"
                 "壹貳贰參叁参肆伍陸陆柒捌玖拾佰仟")
# 常用詞白名單:詞裡的字碰巧是數字字,意思跟數量無關(「一律」「參考」「什麼」…);比對前先遮掉,
# 不然正常的說明與保留意見會被大量拿掉(代碼審 r3 實測 26 句拿掉 22 句)
COMMON_WORDS: tuple[str, ...] = (
    "進一步", "进一步", "一律", "一致", "一直", "一起", "一些", "一下", "一定", "一般", "一旦",
    "一樣", "一样", "同一", "統一", "统一", "唯一", "萬一", "万一", "十分", "參考", "参考",
    "參與", "参与", "什麼", "什么", "陸續", "陆续")
# 單位與量詞:中文數字字後面緊接任何一個(可以隔空白)就是數詞,當對不回(「一筆」「十二次」)
UNITS: tuple[str, ...] = (
    "美元", "小時", "小时", "分鐘", "分钟", "萬", "万", "千", "億", "亿", "百", "兆", "元", "塊",
    "块", "倍", "筆", "笔", "次", "個", "个", "件", "天", "日", "週", "周", "月", "年", "秒", "%",
    "\uff05", "成", "折", "條", "条", "位", "人", "輪", "轮", "趟", "批", "項", "项")
_UNIT_AFTER_NUMERAL = re.compile(
    rf"[{_CJK_NUMERALS}]\s*(?:{'|'.join(re.escape(unit) for unit in UNITS)})")
# 阿拉伯數字後面只有會改數量級的單位才算數詞(「100 萬」「5 千」);一般單位(次 % 元 倍 天…)照留,
# 數字本身照樣要對得回證據(協調者代使用者再裁定:「點擊 12 次」「加了 10%」是正常寫法)
MAGNITUDE_UNITS: tuple[str, ...] = ("百萬", "百万", "千萬", "千万", "萬", "万", "千", "億", "亿",
                                    "兆")
_MAGNITUDE_AFTER_DIGIT = re.compile(
    rf"\d\s*(?:{'|'.join(re.escape(unit) for unit in MAGNITUDE_UNITS)})")
_NUMERAL_RUN = re.compile(rf"[{_CJK_NUMERALS}]{{2,}}")  # 連續兩個以上中文或大寫數字字
# 阿拉伯數字之間只隔分組字元(1 100 500、1'100'500、1_100_500、窄不斷行空白)
_GROUPED = re.compile(r"\d[ \t'\u2019_\u00a0\u2007\u2009\u202f\u3000]+\d")
_MAGNITUDE = re.compile(r"\d\s*[kKMB](?![A-Za-z])|\d[kKMB]")  # 500k、5M 直接改了數量級
_EXPONENT = re.compile(r"\d[eE][-+]?\d")


def _masked(sentence: str) -> str:
    for word in COMMON_WORDS:
        sentence = sentence.replace(word, "\u25a1" * len(word))
    return sentence


def _numeral_phrase(sentence: str) -> bool:
    """句子裡有沒有數詞(規則寫在上面各常數;上標、圈數字、分數、羅馬數字這類數字符號也算)。"""
    text = _masked(sentence)
    return bool(_NUMERAL_RUN.search(text) or _UNIT_AFTER_NUMERAL.search(text)
                or _MAGNITUDE_AFTER_DIGIT.search(text)
                or _GROUPED.search(text) or _MAGNITUDE.search(text) or _EXPONENT.search(text)
                or any(unicodedata.category(ch) in ("No", "Nl") for ch in text))


def _numbers(text: str) -> set[Decimal]:
    """文字裡的每一個阿拉伯數字:負號保留(-5 對不回 5);全形數字照認(正規式的 \\d 與 Decimal 都認
    Unicode 十進位數字);千分位逗號拿掉;同值不同寫法算同一個(0.50 等於 0.5)。"""
    found = set()
    for match in _NUMBER.finditer(text):
        raw = match.group(0).replace(",", "")
        if raw[0] in _NEGATIVE:
            raw = "-" + raw[1:]
        found.add(Decimal(raw))
    return found


def traceable_sentences(text: str, evidence: str) -> tuple[str, int]:
    """模型文字裡提到的數字要能對回送出去的證據(Phase 13 計劃〈省掉不值得發生的模型工作〉④:11B 的
    說明與假說也照這條):照句末標點切句,句子裡有數詞(`_numeral_phrase`)、或有任何一個阿拉伯數字不在
    證據文字裡,就整句拿掉。回(留下的文字, 拿掉幾句);呼叫端要把拿掉幾句照實標出來,不靜默刪。只比
    數值、不比語意——核對只證明數字出自證據,不證明用對了地方。要在佔位符換回真實編號之前比。"""
    allowed = _numbers(evidence)
    kept, dropped = [], 0
    for sentence in _SENTENCE.findall(text):
        if not sentence:
            continue
        if not _numeral_phrase(sentence) and _numbers(sentence) <= allowed:
            kept.append(sentence)
        else:
            dropped += 1
    return "".join(kept).strip(), dropped


# 說明與假說的系統提示都要附上(核對只認阿拉伯數字,自己推算的比率或時間對不回證據)
NUMERALS_RULE = "數字一律用阿拉伯數字照證據原樣寫,不要自己推算比率或時間。"


class Preflight(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"  # 錄製模式:不呼叫任何東西


@dataclass(frozen=True)
class LoginPreflight:
    outcome: Preflight
    reason: str | None = None


def preflight_login(settings: Settings) -> LoginPreflight:
    """啟動時的登入預檢(Phase 13 [S1160]):即時模式做一次登入檢查並設後端的已登入旗標,之後每次送出
    不再檢查;沒過回「沒過」與原因(呼叫端整趟改用程式規則,不中途再試)。錄製模式回「不適用」,不呼叫
    任何東西。名字刻意不叫 check_login:那是後端的名字,邊界測試不准模型用戶端以外的地方碰它。"""
    backend = settings.backend
    if settings.mode is not core.Mode.LIVE or backend is None:
        return LoginPreflight(Preflight.NOT_APPLICABLE)
    if not isinstance(backend, cc.ClaudeCodeBackend):  # 將來的 API 後端沒有登入這回事
        return LoginPreflight(Preflight.PASSED)
    try:
        backend.preflight()
    except core.ConfigError as refused:
        return LoginPreflight(Preflight.FAILED, str(refused))
    return LoginPreflight(Preflight.PASSED)


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


# ---- 結算規則 ----
def _mismatch(reported: int | None, computed: int) -> bool:
    if reported is None:
        return False
    numerator, denominator = core.COST_MISMATCH
    return abs(reported - computed) * denominator > min(reported, computed) * numerator


def settlement_for(outcome: tuple[core.BackendReply | None, core.ModelCallFailed | None],
                    model: str, reserved: int, latency_ms: float) -> ledger_db.Settlement:
    reply, failure = outcome
    if failure is not None and failure.outcome in core.FREE_OUTCOMES:  # 確定沒呼叫模型:結算 0
        return ledger_db.zero(failure.outcome, latency_ms, failure.sub_reason)
    usage = reply if failure is None else failure.reply
    if failure is not None and failure.absurd_usage:
        usage = None  # 離譜的數字不當入帳依據:照預留結算(可核銷)、標超支
    computed = None if usage is None else core.list_price(model, usage)
    listed = None if usage is None or computed is None else max(
        computed, usage.reported_nanousd or 0)  # 取高者([S940])
    mismatch = usage is not None and usage.tokens_known and computed is not None and _mismatch(
        usage.reported_nanousd, computed)
    if failure is None:
        if listed is None:
            raise AssertionError("成功一定有用量")
        settled = core.with_factor(listed)
        return ledger_db.Settlement(core.Outcome.OK, None, reply, listed, settled, False,
                                    settled > reserved,
                           mismatch, latency_ms)
    # 其他失敗:讀得出用量就取「預留」與「原價乘 1.2」較高者,讀不出照預留(寧多不少);原價欄同理
    charged = reserved if listed is None else max(reserved, core.with_factor(listed))
    overrun = (charged > reserved or failure.outcome is core.Outcome.OVERRUN
               or failure.absurd_usage)
    return ledger_db.Settlement(failure.outcome, failure.sub_reason, usage,
                       reserved if listed is None else max(reserved, listed), charged,
                       charged == reserved, overrun, mismatch, latency_ms)


# ---- 對外的一個函式 ----
def _check_request(request: core.ModelRequest, settings: Settings) -> None:
    if not isinstance(request.caller, core.Caller):
        raise ValueError("呼叫者必須是封閉列舉的成員")
    if settings.model not in core.PRICES:
        raise core.UnknownModel(f"{settings.model} 不在價目表裡")
    if not 0 < request.timeout_seconds <= core.MAX_TIMEOUT_SECONDS:
        raise ValueError(f"逾時要在 0 到 {core.MAX_TIMEOUT_SECONDS} 秒之間")
    if not 1 <= request.max_output_tokens <= core.MAX_OUTPUT_TOKENS:
        raise ValueError(f"輸出上限要在 1 到 {core.MAX_OUTPUT_TOKENS} 之間")
    if len((request.system + request.user).encode("utf-8")) > core.MAX_PROMPT_BYTES:
        raise ValueError(f"系統提示加使用者內容超過 {core.MAX_PROMPT_BYTES} 位元組")


def _check_live(request: core.ModelRequest, settings: Settings) -> core.ModelBackend:
    if core.price_table_stale():
        raise core.ConfigError(f"價目表查核日期 {core.PRICES_CHECKED_ON} 已超過 90 天,拒絕即時呼叫",
                          sub_reason="local_check")
    if not request.demo_id:
        raise core.ConfigError("即時模式需要展示編號", sub_reason="local_check")
    if settings.backend is None:
        raise core.ConfigError("即時模式沒有後端", sub_reason="local_check")
    if settings.record and not request.batch_id:
        raise ValueError("即時加錄製模式要帶批次編號")
    return settings.backend


def _replay(request: core.ModelRequest, model: str, path: Path, ledger: Path, key: str,
            *, shared: bool) -> core.ModelResult:
    try:
        recording = rec.load_recording(path, key=key, caller=request.caller, model=model)
    except core.NoRecording:
        ledger_db.book(ledger, request, model, ledger_db.zero(core.Outcome.NO_RECORDING))
        raise
    except rec.PendingRecording:
        recording = None  # 還在錄:對重播來說就是還沒有
    if recording is None:
        ledger_db.book(ledger, request, model, ledger_db.zero(core.Outcome.NO_RECORDING))
        raise core.NoRecording("找不到對應的錄製回應(不會改走即時呼叫)")
    outcome = core.Outcome(recording.outcome)
    ledger_db.book(ledger, request, model,
                   ledger_db.zero(outcome, recording.latency_ms, recording.sub_reason))
    state = (core.SettlementState.SETTLED if recording.settlement is None
             else core.SettlementState(recording.settlement))  # 錄製當時的狀態:重播停在同一處
    if outcome is not core.Outcome.OK or recording.text is None:
        failure = core.BY_OUTCOME[outcome](f"錄製的結果:{outcome.value}",
                                           sub_reason=recording.sub_reason,
                                           recording_batch_id=recording.batch_id)
        failure.list_nanousd, failure.latency_ms = recording.list_nanousd, recording.latency_ms
        failure.settlement = state
        failure.unclassified, failure.tool_use = recording.unclassified, recording.tool_use
        failure.shared = shared
        raise failure
    return core.ModelResult(recording.text, core.Source.RECORDED, recording.input_tokens or 0,
                            recording.output_tokens or 0, recording.cache_write_5m_tokens or 0,
                            recording.cache_write_1h_tokens or 0, recording.cache_read_tokens or 0,
                            recording.list_nanousd, recording.latency_ms, recording.key,
                            recording.batch_id, settlement=state, shared=shared)


def _send(backend: core.ModelBackend, call: core.BackendCall) -> tuple[
        core.BackendReply | None, core.ModelCallFailed | None]:
    """送出並把荒謬值夾到上限(任何後端都一樣):成功形狀帶荒謬值改判讀不懂。"""
    try:
        raw = backend.send(call)
    except core.ModelCallFailed as failed:
        return screen_outcome(None, failed)
    except Exception as unexpected:  # 後端的意外錯誤:暫時性、無法可靠分類、照預留結算
        failure = core.TransientServiceError(f"後端意外錯誤({type(unexpected).__name__})",
                                             sub_reason="unclassified", unclassified=True)
        failure.__cause__ = unexpected
        return None, failure
    return screen_outcome(raw, None)


def screen_outcome(reply: core.BackendReply | None, failure: core.ModelCallFailed | None) -> tuple[
        core.BackendReply | None, core.ModelCallFailed | None]:
    """荒謬值檢查(送出呼叫與實測命令列共用,代碼審第 3 輪):成功形狀帶荒謬值改判讀不懂;失敗帶荒謬值
    就標記。兩種都照預留結算(可核銷)、標超支,原始數字只寫錯誤日誌、不入帳。"""
    if failure is not None:
        if failure.reply is not None:
            _, failure.absurd_usage = core.clamp_reply(failure.reply)
            if failure.absurd_usage:
                _log_absurd(failure.reply)
        return None, failure
    if reply is None:
        raise AssertionError("沒有失敗就一定有回應")
    clamped, absurd = core.clamp_reply(reply)
    if absurd:
        _log_absurd(reply)
        absurd_failure = core.UnreadableModelResponse(
            "回報的 token 數或花費大得離譜(超過上限的千倍),照預留結算並標超支",
            sub_reason="absurd_usage", reply=clamped)
        absurd_failure.absurd_usage = True
        return None, absurd_failure
    return clamped, None


def _log_absurd(reply: core.BackendReply) -> None:
    log.error("後端回報的用量或花費大得離譜(輸入 %s、輸出 %s、快取寫入 %s/%s、快取讀取 %s、自報 %s "
              "十億分之一美元):不入帳、照預留結算並標超支,請人看", reply.input_tokens,
              reply.output_tokens, reply.cache_write_5m_tokens, reply.cache_write_1h_tokens,
              reply.cache_read_tokens, reply.reported_nanousd)


def _wait_for_same_batch(request: core.ModelRequest, model: str, path: Path, ledger: Path,
                         key: str) -> core.ModelResult:
    """同一批的另一個呼叫正在錄同一個鍵:等它錄完(有上限)再讀,不再呼叫一次。"""
    deadline = time.monotonic() + request.timeout_seconds + 5
    while time.monotonic() < deadline:
        try:
            if rec.load_recording(path, key=key, caller=request.caller, model=model) is None:
                break
        except rec.PendingRecording:
            time.sleep(0.05)
            continue
        except core.NoRecording:
            break
        return _replay(request, model, path, ledger, key, shared=True)
    raise core.RecordingConflict("同一批的同一個鍵正在錄、等不到錄完(或已被刪),這次不呼叫")


def _existing(request: core.ModelRequest, model: str, path: Path, ledger: Path,
              key: str) -> core.ModelResult:
    """檔名已被佔住:同一批已錄好就直接讀;同一批還在錄就等;別的批次(或讀不懂、懸空的符號連結)
    呼叫前拒絕,不花額度、不覆寫([S931])。"""
    conflict = "別的批次已經錄過同一個鍵;換一批要整批重錄、舊批整批刪掉"
    try:
        existing = rec.load_recording(path, key=key, caller=request.caller, model=model)
    except rec.PendingRecording as pending:
        if pending.batch_id == request.batch_id:
            return _wait_for_same_batch(request, model, path, ledger, key)
        raise core.RecordingConflict(
            f"錄製檔名被別的批次({pending.batch_id})佔住、還沒錄完:可能是中斷留下的佔位,確認沒有行程"
            f"還在跑之後刪掉 {path.name} 再重跑", sub_reason="recording_conflict",
            recording_batch_id=pending.batch_id) from None
    except core.NoRecording as bad:
        raise core.RecordingConflict(f"錄製檔名已被佔住但讀不懂({bad}),這次不呼叫") from bad
    if existing is None or existing.batch_id != request.batch_id:
        raise core.RecordingConflict(conflict, sub_reason="recording_conflict",
                                     recording_batch_id=None if existing is None
                                     else existing.batch_id)
    return _replay(request, model, path, ledger, key, shared=True)


def _claim(request: core.ModelRequest, path: Path) -> None:
    try:
        rec.claim(path, request.batch_id)
    except FileExistsError:
        raise
    except OSError as failed:
        raise core.ConfigError(f"錄製檔名佔不到({type(failed).__name__}),沒有呼叫",
                               sub_reason="recording_unwritable") from failed


def _live(request: core.ModelRequest, settings: Settings, key: str, path: Path,
          ledger: Path) -> core.ModelResult:
    backend = _check_live(request, settings)
    if settings.record:
        try:
            _claim(request, path)  # 呼叫前就佔住檔名:兩個批次同時錄同一個鍵,只有一個會呼叫
        except FileExistsError:
            return _existing(request, settings.model, path, ledger, key)
        try:
            return _call(request, settings, backend, key, path, ledger)
        except BaseException:
            rec.release(path)  # 沒錄成(預留被拒、被打斷…):放掉佔位;已錄成的不會動
            raise
    return _call(request, settings, backend, key, path, ledger)


def _call(request: core.ModelRequest, settings: Settings,
          backend: core.ModelBackend, key: str, path: Path, ledger: Path) -> core.ModelResult:
    reservation_id = ledger_db.reserve(ledger, request, settings.model, backend.kind)
    reserved = core.reservation_nanousd(request, settings.model)
    call = core.BackendCall(settings.model, request.system, request.user,
                            request.max_output_tokens, request.timeout_seconds,
                            core.call_budget_nanousd(request, settings.model))
    started = time.monotonic()
    try:
        reply, failure = _send(backend, call)
    except BaseException as stopped:  # Ctrl-C、SIGTERM 等:已經預留(可能已經花錢),留下預留編號
        stopped.add_note(f"預留 {reservation_id} 沒結算(呼叫途中被中斷)")
        stopped.rtb_reservation_id = reservation_id  # type: ignore[attr-defined]
        raise
    latency_ms = (time.monotonic() - started) * 1000
    done = settlement_for((reply, failure), settings.model, reserved, latency_ms)
    settled = _try_settle(ledger, reservation_id, done)
    if done.overrun:
        log.error("預留 %s 超支:入帳 %s 大於預留 %s,或 Claude Code 回報超過單次花費上限"
                  "(照實記帳);評估應整批停下", reservation_id, done.settled_nanousd, reserved)
    state = _state(overrun=done.overrun, settled=settled)
    result = None
    if failure is not None:
        failure.list_nanousd, failure.latency_ms = done.list_nanousd, latency_ms
        failure.settlement = state
    elif reply is not None:
        result = core.ModelResult(reply.text, core.Source.LIVE, reply.input_tokens,
                                  reply.output_tokens, reply.cache_write_5m_tokens,
                                  reply.cache_write_1h_tokens, reply.cache_read_tokens,
                                  done.list_nanousd, latency_ms, key, request.batch_id,
                                  settlement=state)
    if settings.record:
        _save(path, _recording_of(key, request, settings.model, backend.kind, done,
                                  (state, failure)), result, failure)
    if failure is not None:
        raise failure
    if result is None:
        raise AssertionError("沒有失敗就一定有回應")
    return result


def _state(*, overrun: bool, settled: bool) -> core.SettlementState:
    if overrun:
        return core.SettlementState.OVERRUN if settled else core.SettlementState.OVERRUN_UNSETTLED
    return core.SettlementState.SETTLED if settled else core.SettlementState.UNSETTLED


def _save(path: Path, recording: rec.Recording, result: core.ModelResult | None,
          failure: core.ModelCallFailed | None) -> None:
    """錄製檔寫不進去:另立一類(不是設定錯誤:已經呼叫了),帶上結算狀態與原價,成功的回應留著。"""
    try:
        rec.save_recording(path, recording)
    except OSError as broken:
        wrapped = core.RecordingWriteFailed(
            f"錄製檔寫不進去({type(broken).__name__});回應已拿到、帳已結算", result, failure)
        source = result if result is not None else failure
        if source is not None:
            wrapped.settlement, wrapped.list_nanousd = source.settlement, source.list_nanousd
            wrapped.latency_ms = source.latency_ms
            wrapped.tool_use = failure is not None and failure.tool_use
        raise wrapped from broken


def _recording_of(key: str, request: core.ModelRequest, model: str,
                  backend: core.Backend, done: ledger_db.Settlement,
                  outcome: tuple[core.SettlementState, core.ModelCallFailed | None]
                  ) -> rec.Recording:
    state, failure = outcome
    reply = done.reply
    tokens = None if reply is None or not reply.tokens_known else reply
    return rec.Recording(
        key, core.Caller(request.caller).value, model, backend.value, request.batch_id,
        core.utc_now().date().isoformat(), done.outcome.value, done.sub_reason,
        None if failure is not None or reply is None else reply.text,
        None if tokens is None else tokens.input_tokens,
        None if tokens is None else tokens.output_tokens,
        None if tokens is None else tokens.cache_write_5m_tokens,
        None if tokens is None else tokens.cache_write_1h_tokens,
        None if tokens is None else tokens.cache_read_tokens,
        None if reply is None else reply.reported_nanousd, done.list_nanousd,
        done.latency_ms or 0.0, failure is not None and failure.tool_use,
        failure is not None and failure.unclassified, state.value)


def settle_quietly(ledger: Path, reservation_id: int, done: ledger_db.Settlement) -> bool:
    """寫結算列(有上限地重試,仍失敗印應入帳金額);給實測命令列用,跟 call_model 同一套。"""
    return _try_settle(ledger, reservation_id, done)


def _try_settle(ledger: Path, reservation_id: int, done: ledger_db.Settlement) -> bool:
    """寫結算列,寫不進去有上限地重試;仍失敗就把應入帳金額印到標準錯誤(帳裡照預留算,人要知道差多少)。"""
    for attempt in range(1, ledger_db.SETTLE_ATTEMPTS + 1):
        try:
            late = ledger_db.settle(ledger, reservation_id, done)
        except (DatabaseBusy, core.LedgerBusy, sqlite3.Error, OSError, OverflowError):
            log.warning("預留 %s 的結算第 %s 次寫不進花費帳", reservation_id, attempt)
            if attempt < ledger_db.SETTLE_ATTEMPTS:
                time.sleep(0.05 * attempt)
            continue
        if late:
            log.error("預留 %s 已被人工核銷,之後才來了結算(遲到的回應):已用改算兩者中較高的金額,"
                      "請對照本機紀錄", reservation_id)
        return True
    sys.stderr.write(
        f"預留 {reservation_id} 的結算寫不進花費帳({ledger_db.SETTLE_ATTEMPTS} 次):"
        "帳裡照預留金額算;"
        f"應入帳 {done.settled_nanousd / core.NANOUSD_PER_USD:.6f} 美元"
        f"{'(超支)' if done.overrun else ''},請人工對帳\n")
    return False


def call_model(request: core.ModelRequest, settings: Settings, *, recordings_dir: Path,
               ledger: Path) -> core.ModelResult:
    """送出一次模型呼叫(或讀錄製)。失敗丟 `ModelCallFailed` 的子類別;每一次呼叫或讀取錄製都記一筆帳
    ([S907]);沒呼叫也沒讀到的(別的批次已錄、花費帳忙碌、呼叫前的本地檢查)不記。"""
    _check_request(request, settings)
    key = rec.recording_key(request.caller, settings.model, request.system, request.user,
                            request.max_output_tokens)
    path = Path(recordings_dir) / f"{key}.json"
    try:
        if settings.mode is core.Mode.RECORDED:
            return _replay(request, settings.model, path, ledger, key, shared=False)
        return _live(request, settings, key, path, ledger)
    except core.ModelCallFailed:  # 逾時也是 OSError 的子類別:模型用戶端自己的例外照原樣丟
        raise
    except (sqlite3.Error, OSError, OverflowError) as broken:  # 花費帳或檔案系統的非模型例外
        failure = core.TransientServiceError(
            f"花費帳或錄製檔出錯({type(broken).__name__}),沒有呼叫或結果不明",
            sub_reason="unclassified", unclassified=True)
        raise failure from broken  # 結算狀態 None:出錯在預留之前(預留之後的步驟各自接住)
