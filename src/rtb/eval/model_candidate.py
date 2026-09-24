"""接入點 3:「值不值得加」的模型候選(Phase 11B 增量 1,計劃〈接入點 3〉)。

實作分析端既有的候選介面:把判斷點輸入的七個欄位(只有數字與狀態)格式化成固定模板,
要模型只回一個 JSON 物件、判定是四種之一;解析失敗、判定不在四種裡、以及模型用戶端的
任何例外都往外丟,由既有路由退回現行規則([S908])。輸入型別結構上放不進廣告名稱這類
不可信文字([S919])。

既有路由把例外全部接住,評估紀錄命令列看不出是哪一類,所以候選另記一份旁路紀錄(每次
嘗試的結果類別)。評估跑合成集的固定子集(每格 14 組、每組三個變體,共 210 個情境;依組取、
不拆組,種子寫死);旁路紀錄出現本地上限拒絕、訂閱額度用完、設定錯誤、花費帳忙碌、超支、
無法可靠分類的錯誤或偵測到工具使用,就在那個情境之後停([S924]、[S934])。

批次紀錄:即時跑的那一次產生一個批次編號,每一次嘗試記一列(情境、結果類別、原價、token、
延遲),七個欄位相同的情境同一批只即時呼叫一次、其餘列標「共用前一列」([S933]);去重跟錄製開關
無關(沒開錄製時候選自己記住這一批呼叫過的鍵)。模型用戶端以外的例外也替那個情境補一列(暫時性、
無法可靠分類)並停下,不會讀到上一列。
比較表的模型列一律從批次紀錄算(包含當時的失敗),標「歷史觀測」與錄製日期;「沒有錄製」
「花費帳忙碌」「本地上限拒絕」「設定錯誤」與共用列都沒有呼叫模型,不算進四種比率,另外列件數;
「訂閱額度用完」已經呼叫,算進例外率。每次呼叫成本取該格最高一次的原價([S928])。
"""

import json
import math
import random
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from rtb import modelclient as mc
from rtb.analyzer.policy import CandidateCall, TrialCells, route
from rtb.domain.worth import WorthCell, WorthInput, WorthVerdict
from rtb.eval.adoption import (
    NO_COST_GATE,
    ComparisonRow,
    Measure,
    MeasuredRow,
    OperationalLimits,
)
from rtb.eval.generator import Scenario
from rtb.eval.scoring import ScoredCase

FIELDS = ("status", "budget", "spend", "impressions", "clicks", "conversions", "revenue")
SYSTEM_PROMPT = (
    "你是廣告投放的判斷助手。使用者會給一個配速偏低的廣告的七個欄位,只有狀態與數字。"
    "請判斷這個廣告值不值得加預算,只回一個 JSON 物件,格式是 {\"verdict\": \"答案\"},"
    "答案只能是 worth、not_worth、insufficient_evidence、unsure 四個之一,不要回任何其他文字。"
    "判斷準則由上而下第一個成立的:狀態是暫停 → not_worth;"
    "任一個數字缺值(missing)或為負、點擊多於曝光、轉換多於點擊 → insufficient_evidence;"
    "曝光或點擊不是正數 → not_worth;轉換或營收是正數 → worth;其他 → insufficient_evidence。")
MAX_OUTPUT_TOKENS = 32
TIMEOUT_SECONDS = 30.0
SUBSET_SEED = 20260924
GROUPS_PER_CELL = 14
INVALID_VERDICT = "invalid_verdict"
OK = mc.Outcome.OK.value
# 沒有呼叫模型的結果類別:不算進四種比率,另外列件數(計劃第 6 版〈接入點 3〉母體)
UNSENT = frozenset({mc.Outcome.NO_RECORDING.value, mc.Outcome.LEDGER_BUSY.value,
                    mc.Outcome.LOCAL_CAP_REFUSED.value, mc.Outcome.CONFIG_ERROR.value})
# 整批停下(計劃第 8 版):本地上限拒絕、訂閱額度用完、設定錯誤、花費帳忙碌、超支;另外看旁路紀錄的
# 結算狀態(超支)、「無法可靠分類」與「偵測到工具使用」標記
STOP_OUTCOMES = frozenset({mc.Outcome.LOCAL_CAP_REFUSED.value, mc.Outcome.QUOTA_EXHAUSTED.value,
                           mc.Outcome.CONFIG_ERROR.value, mc.Outcome.LEDGER_BUSY.value,
                           mc.Outcome.OVERRUN.value})
OVERRUN = "overrun"
OVERRUN_STATES = frozenset({mc.SettlementState.OVERRUN.value,
                            mc.SettlementState.OVERRUN_UNSETTLED.value})
UNCLASSIFIED = "unclassified"
UNEXPECTED = "unexpected_error"  # 模型用戶端以外的例外(子原因)
INTERRUPTED = "interrupted"  # 呼叫途中被中斷(Ctrl-C 等):已經預留、可能已經花錢,預留沒結算
TOOL_USE = "tool_use"
SHARED_NOTE = "共用前一列"
# 使用者 2026-09-24 裁定:每次呼叫成本不超過 0.002 美元、延遲 p95 不超過 3 秒、失敗率不超過 1%;
# 延遲中位數使用者沒裁定,協調者補成跟 p95 同一個值(中位數必定不超過 p95,不加嚴也不放寬)
MODEL_LIMITS = OperationalLimits(cost_per_call_usd=0.002, latency_median_us=3_000_000.0,
                                 latency_p95_us=3_000_000.0, failure_rate=0.01)
_FORMAT_FAILURES = frozenset({INVALID_VERDICT, mc.Outcome.UNREADABLE.value})
_EXCEPTIONS = frozenset({mc.Outcome.TRANSIENT.value, mc.Outcome.QUOTA_EXHAUSTED.value,
                         mc.Outcome.OVERRUN.value})


def _field(value: object) -> str:
    if value is None:
        return "missing"
    return str(getattr(value, "value", value))


def prompt_for(worth_input: WorthInput) -> str:
    """固定模板:只有七個欄位的名稱與值(數字或狀態),同一個輸入永遠得到同一段文字。"""
    return "\n".join(["廣告的七個欄位:",
                      *(f"{name}: {_field(getattr(worth_input, name))}" for name in FIELDS)])


class InvalidVerdict(ValueError):
    """模型的回答不是只有一個 verdict 欄位的 JSON 物件,或判定不在四種裡。"""


def parse_verdict(text: str) -> WorthVerdict:
    try:
        data = json.loads(text.strip())
    except ValueError as bad:
        raise InvalidVerdict("回答不是 JSON") from bad
    if not isinstance(data, dict) or set(data) != {"verdict"} or not isinstance(
            data["verdict"], str):
        raise InvalidVerdict("回答要是只有 verdict 一個欄位的物件")
    try:
        return WorthVerdict(data["verdict"])
    except ValueError as bad:
        raise InvalidVerdict("判定不在四種裡") from bad


@dataclass(frozen=True)
class Attempt:
    """旁路紀錄的一列:一次候選呼叫的結果類別(成功、判定不合格、或模型用戶端的七類之一)。"""

    outcome: str
    verdict: WorthVerdict | None
    shared: bool
    list_nanousd: int
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float | None
    recording_batch_id: str | None
    sub_reason: str | None = None
    settlement: str | None = None  # 已結算、未結算、超支;沒有預留就是 None
    unclassified: bool = False  # 暫時性服務錯誤認不出子類型
    tool_use: bool = False  # 回應帶著工具使用痕跡
    reservation_id: int | None = None  # 被中斷時那筆沒結算的預留(要人核銷時對照)


def _failed_attempt(failed: mc.ModelCallFailed, *, shared: bool) -> Attempt:
    return Attempt(failed.outcome.value, None, shared, failed.list_nanousd, None, None,
                   failed.latency_ms, failed.recording_batch_id, failed.sub_reason,
                   None if failed.settlement is None else failed.settlement.value,
                   failed.unclassified, failed.tool_use)


UNEXPECTED_ATTEMPT = Attempt(mc.Outcome.TRANSIENT.value, None, False, 0, None, None, None, None,
                             UNEXPECTED, None, unclassified=True)


class ModelCandidate:
    """候選介面的模型實作;每次呼叫都在 `attempts` 追加一列旁路紀錄(模型用戶端以外的例外也追加)。"""

    def __init__(self, settings: mc.Settings, *, recordings_dir: Path, ledger: Path,
                 demo_id: str | None, batch_id: str | None) -> None:
        self._settings, self._recordings, self._ledger = settings, recordings_dir, ledger
        self._demo_id, self._batch_id = demo_id, batch_id
        self.attempts: list[Attempt] = []
        # 即時、沒開錄製時自己去重:這一批呼叫過的鍵 → 那次的結果或失敗
        # (開錄製時模型用戶端照錄製檔去重)
        self._seen: dict[str, mc.ModelResult | mc.ModelCallFailed] = {}

    def _call(self, request: mc.ModelRequest) -> mc.ModelResult:
        dedupe = self._settings.mode is mc.Mode.LIVE and not self._settings.record
        key = mc.recording_key(request.caller, self._settings.model, request.system,
                               request.user, request.max_output_tokens)
        seen = self._seen.get(key) if dedupe else None
        if isinstance(seen, mc.ModelResult):
            return replace(seen, shared=True)
        if isinstance(seen, mc.ModelCallFailed):
            self.attempts.append(_failed_attempt(seen, shared=True))
            raise seen
        try:
            result = mc.call_model(request, self._settings, recordings_dir=self._recordings,
                                   ledger=self._ledger)
        except mc.ModelCallFailed as failed:
            if dedupe:
                self._seen[key] = failed
            raise
        if dedupe:
            self._seen[key] = result
        return result

    def __call__(self, worth_input: WorthInput, timeout_seconds: float) -> WorthVerdict:
        request = mc.ModelRequest(
            caller=mc.Caller.EVAL_CANDIDATE, system=SYSTEM_PROMPT, user=prompt_for(worth_input),
            max_output_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=timeout_seconds,
            demo_id=self._demo_id, batch_id=self._batch_id)
        before = len(self.attempts)
        try:
            result = self._call(request)
        except mc.ModelCallFailed as failed:
            if len(self.attempts) == before:
                self.attempts.append(_failed_attempt(failed, shared=failed.shared))
            raise
        except Exception:
            self.attempts.append(UNEXPECTED_ATTEMPT)
            raise
        except BaseException as stopped:  # 被中斷:這一次已經預留,批次紀錄也要有它(代碼審第 3 輪)
            self.attempts.append(Attempt(INTERRUPTED, None, False, 0, None, None, None, None,
                                         INTERRUPTED, None,
                                         reservation_id=getattr(stopped, "rtb_reservation_id",
                                                                None)))
            raise
        try:
            verdict = parse_verdict(result.text)
        except InvalidVerdict:
            self.attempts.append(self._from_result(INVALID_VERDICT, None, result))
            raise
        self.attempts.append(self._from_result(OK, verdict, result))
        return verdict

    @staticmethod
    def _from_result(outcome: str, verdict: WorthVerdict | None,
                     result: mc.ModelResult) -> Attempt:
        return Attempt(outcome, verdict, result.shared, result.list_nanousd, result.input_tokens,
                       result.output_tokens, result.latency_ms, result.batch_id,
                       settlement=result.settlement.value)


def subset(scenarios: Sequence[Scenario]) -> tuple[Scenario, ...]:
    """每格用固定種子取 14 組,每組三個變體全收(依組取、不拆組,擾動不變性照常算)。"""
    rng = random.Random(SUBSET_SEED)  # noqa: S311 - 挑合成情境,不是安全用途
    chosen: set[str] = set()
    for cell in WorthCell:
        groups = sorted({s.group for s in scenarios if s.filed_cell is cell})
        chosen |= set(rng.sample(groups, min(GROUPS_PER_CELL, len(groups))))
    return tuple(s for s in scenarios if s.group in chosen)


# ---- 批次紀錄 ----
@dataclass(frozen=True)
class BatchRow:
    scenario_id: str
    cell: WorthCell
    outcome: str
    verdict: str | None
    sent: bool
    shared: bool
    list_nanousd: int
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float | None
    reservation_id: int | None = None  # 被中斷那一列:沒結算的預留編號


@dataclass(frozen=True)
class BatchRecord:
    batch_id: str
    caller: str
    model: str
    price_checked_on: str
    price_page: str
    recorded_on: str
    rows: tuple[BatchRow, ...]
    interrupted: bool = False  # 跑到一半被中斷或崩掉:只寫了已跑的部分


class BatchInvalid(ValueError):
    """批次紀錄的結構或值域不對(存在版本庫裡、誰都改得到,不信任它)。"""


def batch_row(scenario: Scenario, attempt: Attempt) -> BatchRow:
    sent = not attempt.shared and attempt.outcome not in UNSENT
    return BatchRow(scenario.scenario_id, scenario.cell, attempt.outcome,
                    None if attempt.verdict is None else attempt.verdict.value, sent,
                    attempt.shared, attempt.list_nanousd, attempt.input_tokens,
                    attempt.output_tokens, attempt.latency_ms, attempt.reservation_id)


def batch_json(batch: BatchRecord) -> str:
    rows = [{**asdict(row), "cell": row.cell.value, "note": SHARED_NOTE if row.shared else ""}
            for row in batch.rows]
    return json.dumps({**asdict(batch), "rows": rows}, ensure_ascii=False, indent=1)


_ROW_TEXT = ("scenario_id", "outcome")
_ROW_COUNTS = ("input_tokens", "output_tokens")


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_row(row: dict[str, object]) -> bool:
    latency = row["latency_ms"]
    verdict = row["verdict"]
    reservation = row.get("reservation_id")
    return ((reservation is None or _count(reservation))
            and all(isinstance(row[name], str) for name in _ROW_TEXT)
            and all(row[name] is None or _count(row[name]) for name in _ROW_COUNTS)
            and _count(row["list_nanousd"])
            and isinstance(row["sent"], bool) and isinstance(row["shared"], bool)
            and (verdict is None or verdict in {v.value for v in WorthVerdict})
            and (latency is None or (isinstance(latency, int | float)
                                     and not isinstance(latency, bool)
                                     and math.isfinite(latency) and latency >= 0)))


def load_batch(path: Path) -> BatchRecord:
    """讀批次紀錄並驗結構與值域;不合丟 BatchInvalid(讀的一方當「讀不懂」、掛旗標)。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_rows = data.pop("rows")
        if not isinstance(raw_rows, list) or not all(isinstance(r, dict) for r in raw_rows):
            raise BatchInvalid("批次紀錄的 rows 不是物件清單")
        rows = []
        for row in raw_rows:
            fields = {k: v for k, v in row.items() if k != "note"}
            if not _valid_row(fields):
                raise BatchInvalid("批次紀錄有一列的欄位型別或值域不對")
            rows.append(BatchRow(**{**fields, "cell": WorthCell(fields["cell"])}))
        batch = BatchRecord(**data, rows=tuple(rows))
    except BatchInvalid:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as bad:
        raise BatchInvalid(f"批次紀錄讀不懂:{type(bad).__name__}") from bad
    if not all(isinstance(getattr(batch, name), str) for name in (
            "batch_id", "caller", "model", "price_checked_on", "price_page", "recorded_on")) \
            or not isinstance(batch.interrupted, bool):
        raise BatchInvalid("批次紀錄的表頭欄位型別不對")
    return batch


def batches_dir(recordings_dir: Path) -> Path:
    return Path(recordings_dir) / "batches"


def write_batch(recordings_dir: Path, batch: BatchRecord) -> Path:
    """批次紀錄不可變:只新建,同名已存在就失敗。"""
    target = batches_dir(recordings_dir) / f"{batch.batch_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as file:
        file.write(batch_json(batch))
    return target


def new_batch(batch_id: str, model: str, rows: Sequence[BatchRow], *,
              interrupted: bool = False) -> BatchRecord:
    return BatchRecord(batch_id, mc.Caller.EVAL_CANDIDATE.value, model,
                       mc.PRICES_CHECKED_ON.isoformat(), mc.PRICE_PAGE,
                       datetime.now(UTC).date().isoformat(), tuple(rows), interrupted)


def partial_rows(scenarios: Sequence[Scenario], candidate: ModelCandidate) -> tuple[BatchRow, ...]:
    """跑到一半被中斷時,已經留下旁路紀錄的那幾個情境的列(旁路紀錄與情境一一對應、依序)。"""
    return tuple(batch_row(s, a) for s, a in zip(scenarios, candidate.attempts, strict=False))


# ---- 跑子集 ----
@dataclass(frozen=True)
class ModelRun:
    scored: tuple[ScoredCase, ...]
    rows: tuple[BatchRow, ...]
    stopped: str | None  # 整批停下的原因(結果類別或供應商月上限);跑完是 None
    recording_batches: frozenset[str] = field(default_factory=frozenset)
    missing_recordings: int = 0


def _stop_reason(attempt: Attempt) -> str | None:
    if attempt.outcome in STOP_OUTCOMES:
        return attempt.outcome
    if attempt.settlement in OVERRUN_STATES:
        return OVERRUN
    if attempt.unclassified:
        return UNCLASSIFIED
    if attempt.tool_use:
        return TOOL_USE
    return None


def run_subset(scenarios: Sequence[Scenario], candidate: ModelCandidate,
               timeout_seconds: float) -> ModelRun:
    """逐情境經既有路由呼叫候選(待測格是全部五格);旁路紀錄出現該停的類別就在那個情境之後停。"""
    scored, rows, stopped = [], [], None
    trial = TrialCells(frozenset(WorthCell))
    for scenario in scenarios:
        before = len(candidate.attempts)
        result = route(scenario.worth_input, CandidateCall(candidate, timeout_seconds), trial)
        scored.append(ScoredCase(scenario, result.verdict, result.path))
        if len(candidate.attempts) == before:  # 候選沒留下這次的列(不該發生):補一列、停下
            candidate.attempts.append(UNEXPECTED_ATTEMPT)
        attempt = candidate.attempts[-1]
        rows.append(batch_row(scenario, attempt))
        stopped = _stop_reason(attempt)
        if stopped is not None:
            break
    batches = frozenset(a.recording_batch_id for a in candidate.attempts
                        if a.recording_batch_id is not None)
    missing = sum(1 for a in candidate.attempts if a.outcome == mc.Outcome.NO_RECORDING.value)
    return ModelRun(tuple(scored), tuple(rows), stopped, batches, missing)


# ---- 比較表的模型列 ----
def _p95(ordered: Sequence[float]) -> float:
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def _rate(rows: Sequence[BatchRow], hit: frozenset[str] | None = None,
          outcome: str | None = None) -> Measure:
    count = sum(1 for r in rows if (hit is not None and r.outcome in hit) or r.outcome == outcome)
    return Measure.of(count / len(rows))


def _row_for(cell: WorthCell, sent: Sequence[BatchRow], quality: Measure) -> ComparisonRow:
    if not sent:
        missing = Measure.not_measured("這一格沒有送出的呼叫")
        return ComparisonRow("LLM", cell, quality, missing, missing, missing, missing, missing,
                             missing, missing)
    latencies = sorted(r.latency_ms * 1000 for r in sent if r.latency_ms is not None)
    no_latency = Measure.not_measured("沒有延遲紀錄")
    fallbacks = sum(1 for r in sent if r.outcome != OK or r.verdict == WorthVerdict.UNSURE.value)
    return ComparisonRow(
        "LLM", cell, quality,
        cost_per_call_usd=Measure.of(max(r.list_nanousd for r in sent) / mc.NANOUSD_PER_USD),
        latency_median_us=Measure.of(statistics.median(latencies)) if latencies else no_latency,
        latency_p95_us=Measure.of(_p95(latencies)) if latencies else no_latency,
        format_failure_rate=_rate(sent, _FORMAT_FAILURES),
        exception_rate=_rate(sent, _EXCEPTIONS),
        timeout_rate=_rate(sent, outcome=mc.Outcome.TIMEOUT.value),
        fallback_rate=Measure.of(fallbacks / len(sent)))


def model_rows(batch: BatchRecord, scored: Sequence[ScoredCase]) -> dict[WorthCell, ComparisonRow]:
    """每格一列:成本、延遲、各種比率從批次紀錄真正送出的那些列算;品質是這次計分的類別正確率。"""
    rows = {}
    for cell in WorthCell:
        cases = [c for c in scored if c.scenario.cell is cell]
        quality = (Measure.of(sum(c.final is c.scenario.gold for c in cases) / len(cases))
                   if cases else Measure.not_measured("這一格沒有計分"))
        sent = [r for r in batch.rows if r.cell is cell and r.sent]
        rows[cell] = _row_for(cell, sent, quality)
    return rows


def mean_costs(batch: BatchRecord) -> dict[WorthCell, float]:
    """每格送出的呼叫的平均原價(美元),只供參考;門檻比的是最高一次。"""
    means = {}
    for cell in WorthCell:
        sent = [r.list_nanousd for r in batch.rows if r.cell is cell and r.sent]
        if sent:
            means[cell] = statistics.fmean(sent) / mc.NANOUSD_PER_USD
    return means


def unsent_counts(batch: BatchRecord) -> dict[str, int]:
    """沒有送出請求的列(不含共用列)依結果類別的件數。"""
    return dict(Counter(r.outcome for r in batch.rows if not r.sent and not r.shared))


MARKED = (("cost_per_call_usd", "每次成本", "cost"), ("latency_median_us", "延遲中位", "median"),
          ("latency_p95_us", "延遲 p95", "p95"), ("format_failure_rate", "格式失敗率", "rate"),
          ("exception_rate", "例外率", "rate"), ("timeout_rate", "逾時率", "rate"),
          ("fallback_rate", "退回率", "rate"))


def threshold_marks(row: MeasuredRow, limits: OperationalLimits) -> Mapping[str, str]:
    """逐欄標過或沒過(門檻是使用者裁定的常數,不依結果調整)。門檻的 cost_exempt 為真時,成本那一欄
    寫「不設門檻」、不比大小(Phase 13 [S1155])。"""
    bars = {"cost": limits.cost_per_call_usd, "median": limits.latency_median_us,
            "p95": limits.latency_p95_us, "rate": limits.failure_rate}
    marks = {}
    for name, _label, bar_kind in MARKED:
        measure: Measure = getattr(row, name)
        bar = bars[bar_kind]
        if bar_kind == "cost" and limits.cost_exempt:
            marks[name] = NO_COST_GATE
        elif not measure.measured or measure.value is None:
            marks[name] = "沒量"
        elif bar is None:
            marks[name] = "門檻未定"
        else:
            marks[name] = "過" if measure.value <= bar else "沒過"
    return marks
