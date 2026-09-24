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
延遲),七個欄位相同的情境同一批只即時呼叫一次、其餘列標「共用前一列」([S933])。
比較表的模型列一律從批次紀錄算(包含當時的失敗),標「歷史觀測」與錄製日期;「沒有錄製」
「花費帳忙碌」「本地上限拒絕」「設定錯誤」與共用列都沒有呼叫模型,不算進四種比率,另外列件數;
「訂閱額度用完」已經呼叫,算進例外率。每次呼叫成本取該格最高一次的原價([S928])。
"""

import json
import random
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rtb import modelclient as mc
from rtb.analyzer.policy import CandidateCall, TrialCells, route
from rtb.domain.worth import WorthCell, WorthInput, WorthVerdict
from rtb.eval.adoption import ComparisonRow, Measure, OperationalLimits
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
UNCLASSIFIED = "unclassified"
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
    tool_use: bool = False  # 錯誤回應帶著工具使用痕跡


class ModelCandidate:
    """候選介面的模型實作;每次呼叫都在 `attempts` 追加一列旁路紀錄。"""

    def __init__(self, settings: mc.Settings, *, recordings_dir: Path, ledger: Path,
                 demo_id: str | None, batch_id: str | None) -> None:
        self._settings, self._recordings, self._ledger = settings, recordings_dir, ledger
        self._demo_id, self._batch_id = demo_id, batch_id
        self.attempts: list[Attempt] = []

    def __call__(self, worth_input: WorthInput, timeout_seconds: float) -> WorthVerdict:
        request = mc.ModelRequest(
            caller=mc.Caller.EVAL_CANDIDATE, system=SYSTEM_PROMPT, user=prompt_for(worth_input),
            max_output_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=timeout_seconds,
            demo_id=self._demo_id, batch_id=self._batch_id)
        try:
            result = mc.call_model(request, self._settings, recordings_dir=self._recordings,
                                   ledger=self._ledger)
        except mc.ModelCallFailed as failed:
            self.attempts.append(Attempt(
                failed.outcome.value, None, False, failed.list_nanousd, None, None,
                failed.latency_ms, failed.recording_batch_id, failed.sub_reason,
                None if failed.settlement is None else failed.settlement.value,
                failed.unclassified, failed.tool_use))
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


@dataclass(frozen=True)
class BatchRecord:
    batch_id: str
    caller: str
    model: str
    price_checked_on: str
    price_page: str
    recorded_on: str
    rows: tuple[BatchRow, ...]


def batch_row(scenario: Scenario, attempt: Attempt) -> BatchRow:
    sent = not attempt.shared and attempt.outcome not in UNSENT
    return BatchRow(scenario.scenario_id, scenario.cell, attempt.outcome,
                    None if attempt.verdict is None else attempt.verdict.value, sent,
                    attempt.shared, attempt.list_nanousd, attempt.input_tokens,
                    attempt.output_tokens, attempt.latency_ms)


def batch_json(batch: BatchRecord) -> str:
    rows = [{**asdict(row), "cell": row.cell.value, "note": SHARED_NOTE if row.shared else ""}
            for row in batch.rows]
    return json.dumps({**asdict(batch), "rows": rows}, ensure_ascii=False, indent=1)


def load_batch(path: Path) -> BatchRecord:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = tuple(BatchRow(**{**{k: v for k, v in row.items() if k != "note"},
                             "cell": WorthCell(row["cell"])}) for row in data.pop("rows"))
    return BatchRecord(**data, rows=rows)


def batches_dir(recordings_dir: Path) -> Path:
    return Path(recordings_dir) / "batches"


def write_batch(recordings_dir: Path, batch: BatchRecord) -> Path:
    """批次紀錄不可變:只新建,同名已存在就失敗。"""
    target = batches_dir(recordings_dir) / f"{batch.batch_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as file:
        file.write(batch_json(batch))
    return target


def new_batch(batch_id: str, model: str, rows: Sequence[BatchRow]) -> BatchRecord:
    return BatchRecord(batch_id, mc.Caller.EVAL_CANDIDATE.value, model,
                       mc.PRICES_CHECKED_ON.isoformat(), mc.PRICE_PAGE,
                       datetime.now(UTC).date().isoformat(), tuple(rows))


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
    if attempt.settlement == mc.SettlementState.OVERRUN.value:
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
        result = route(scenario.worth_input, CandidateCall(candidate, timeout_seconds), trial)
        scored.append(ScoredCase(scenario, result.verdict, result.path))
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


def threshold_marks(row: ComparisonRow, limits: OperationalLimits) -> Mapping[str, str]:
    """逐欄標過或沒過(門檻是使用者裁定的常數,不依結果調整)。"""
    bars = {"cost": limits.cost_per_call_usd, "median": limits.latency_median_us,
            "p95": limits.latency_p95_us, "rate": limits.failure_rate}
    marks = {}
    for name, _label, bar_kind in MARKED:
        measure: Measure = getattr(row, name)
        bar = bars[bar_kind]
        if not measure.measured or measure.value is None:
            marks[name] = "沒量"
        elif bar is None:
            marks[name] = "門檻未定"
        else:
            marks[name] = "過" if measure.value <= bar else "沒過"
    return marks
