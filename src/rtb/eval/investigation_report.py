"""AI 調查評估的逐格報告、比較與採用決定(Phase 13 增量 3,計劃
[[Projects/RTB_Phase13AI參與決策_計劃]]〈評估案例〉〈花費帳與採用判定〉;Phase 14 增量 4 改成
三列分列,計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈評估與報告〉[S1410][S1418])。

- 逐格只算名稱正常的那 4 筆,分三列([S1410]):
  - AI 原始:舊錄製裡模型自己的有效結論。格式錯誤、選項外、呼叫失敗、輪數用完等「無有效答案」另列件數
    與原因,不算誤提案、不算答對,也不拿規則答案頂替。
  - AI+規則否決:**報告層的派生比較**,正式流程已無此機制(裁定 8:AI 退出加額決策)。把 AI 原始的
    propose 跟案例九條結果相交,九條不同意就算否決、原因取九條細因;分母跟 AI 原始相同。
  - 程式規則:正式九條拿案例存的四查詢判。標準答案出自同一套九條,所以程式規則 36/36 與派生列的
    零誤提案都是同源構造,不作品質證據。
  每列寫誤提案筆數/應不提案筆數(各自分母)、類別正確、值得加格召回、錯誤子型;AI 原始另報輪數、原價與
  退回原因([S1117])。
- 對抗切片([S1118]):誘導雙胞胎看 AI 原始(一邊沒有有效答案也算不同);派生列與程式規則另報件數。
- 延遲分單位與量測範圍:模型呼叫用毫秒(錄製當時記的毫秒原值)、九條本機計算用微秒(這次重播在行程內
  量)、整段正式蒐證不在評估裡量。單位寫在欄名,格內不換算;模型那一列照門檻常數以微秒存、以微秒印。
  數字格一律走共用的 `adoption.format_value`(跟 Phase 10 比較表同一支,不出科學記號)。
- 找不到錄製(或沒送出)的筆數另列「缺錄製」,不算 AI 無有效答案;有缺錄製時整份報告標不可採信。
- 無有效答案裡照錄製原文讀得出的本意另列,並給「照本意算」的參考值(非正式口徑)。
- 採用:合成集一律不採用,這裡不建任何已驗證清單、正式路徑的決策函式照舊不帶候選([S1119])。理由照 AI
  原始品質與本計劃門檻寫([S1418]),規則同源全對不改判。模型那一列照本計劃自己的門檻常數判逐欄:成本不設
  門檻(使用者裁定 9、13,假設正式環境用自研模型,[S1140][S1155]),延遲與失敗率照 Phase 11B 的數字。
"""

import hashlib
import json
import statistics
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rtb import modelclient as mc
from rtb.analyzer import investigation as inv
from rtb.analyzer import policy
from rtb.analyzer.task_store import InvestigationRecord
from rtb.domain.worth import WorthVerdict
from rtb.eval import investigation_cases
from rtb.eval.adoption import (
    MARKED,
    NO_MONITORING,
    UNSENT,
    Measure,
    OperationalLimits,
    format_value,
    operational_problems,
    threshold_marks,
)
from rtb.eval.investigation_cases import NOW, VERDICT, Case, Cell, rule_evidence, worth_input

# 本計劃給調查決策點的門檻(〈花費帳與採用判定〉):成本不設門檻;延遲 p95 3 秒、失敗率 1% 沿用使用者
# 裁定,延遲中位 3 秒沿用 Phase 11B 協調者補的同一個值
INVESTIGATION_LIMITS = OperationalLimits(cost_per_call_usd=None, latency_median_us=3_000_000.0,
                                         latency_p95_us=3_000_000.0, failure_rate=0.01,
                                         cost_exempt=True)
SYNTHETIC_NEVER_ADOPTS = ("合成評估集是有限的合約案例,照 Phase 10 規定一律不採用,"
                          "不產生任何給正式路徑的已驗證清單;展示照樣可以用 AI 回答做示範,"
                          "但不進正式決策路徑")
MISSING_EVIDENCE = ("正式環境的決策紀錄抽樣", "人工標註", NO_MONITORING)
_FORMAT = frozenset({mc.Outcome.UNREADABLE.value})
_EXCEPTIONS = frozenset({mc.Outcome.TRANSIENT.value, mc.Outcome.QUOTA_EXHAUSTED.value,
                         mc.Outcome.OVERRUN.value})
OFF_MENU = "off_menu"
NO_CONCLUSION = "no_conclusion"  # 沒有模型結論、也沒有退回紀錄(保險;正常走不到)
PREFLIGHT = "preflight"  # Phase 13 舊前置過濾沒送模型就結案(細因另存在 CaseRun.preflight)
# 無有效答案裡照錄製原文讀得出的本意(非正式口徑,只供揭露)
INTENT_MISPROPOSAL, INTENT_CORRECT, INTENT_WRONG = "misproposal", "correct", "wrong"
INTENT_QUERY, INTENT_UNREADABLE, INTENT_NO_TEXT = "query", "unreadable", "no_text"
INTENT_LABELS = {INTENT_MISPROPOSAL: "本意誤提案", INTENT_CORRECT: "本意答對",
                 INTENT_WRONG: "本意答錯(不是提案)", INTENT_QUERY: "本意是選查詢",
                 INTENT_UNREADABLE: "讀不出本意", INTENT_NO_TEXT: "沒有原文(呼叫失敗等)"}
EVAL_SET = Path(__file__).with_name("investigation_set.py")
# 每個入庫評估批次錄製當時的評估集雜湊與之後換版的原因(報告實際比對;重錄新批次時補一列)
RECORDED_EVAL_SETS: dict[str, tuple[str, str]] = {
    "phase13-eval-20260925": (
        "8dccc36a19ea3256892007c32c303c497083d3041979aa502d24f7b3d7f5bdac",
        "2026-09-26 案例的過去調整補上帶時區的 committed_at(只給讀取白名單核對,不進模型題目)"),
}
AI_RAW = "AI 原始"
AI_VETO = "AI+規則否決(派生比較,正式流程已無此機制)"
CODE_RULE = "程式規則(九條)"
SAME_SOURCE = "同源構造,不作品質證據"
_CONCLUDED = {inv.Conclusion.PROPOSE.value: WorthVerdict.WORTH,
              inv.Conclusion.DO_NOT_PROPOSE.value: WorthVerdict.NOT_WORTH,
              inv.Conclusion.STOP_INSUFFICIENT.value: WorthVerdict.INSUFFICIENT}
RULE_WARMUP, RULE_RUNS = 200, 2_000  # 九條本機計算的延遲量法:暖身後量這麼多次取中位與 p95


def eval_set_sha256() -> str:
    """評估集檔的雜湊:錄製鍵跟著題目走,報告記下它依據的是哪一版。"""
    return hashlib.sha256(EVAL_SET.read_bytes()).hexdigest()


def rule_verdict(case: Case) -> WorthVerdict:
    """正式程式規則的答案(Phase 14 起是九條):
    同一個判斷點輸入加案例存的四種查詢結果(轉成正式領域型別),
    以案例固定 NOW 判。標準答案也由同一支領域規則產生,所以這一列跟標準答案同源,不作品質證據。"""
    return policy.code_rule(worth_input(case), rule_evidence(case), NOW)


def rule_reason(case: Case) -> str:
    """九條判斷的細因(命中的格,或九格之外的證據不足原因與查詢代碼):派生否決列的否決原因。走評估集
    既有的 `rule_decision(case)` 封裝(代碼審 r1 架構對齊-1),不另開直通領域層的路。"""
    decision = investigation_cases.rule_decision(case)
    return decision.reason.value if decision.query is None else \
        f"{decision.reason.value}({decision.query.value})"


def intent_of(text: str | None) -> WorthVerdict | str:
    """從錄製原文讀模型的本意(只供揭露,非正式口徑):原文裡第一個帶 choice 的 JSON 物件;choice 是三種
    結論之一就回那個結論,是查詢(字串或清單)回 query,讀不出回 unreadable,沒有原文回 no_text。"""
    if text is None:
        return INTENT_NO_TEXT
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            found, _end = decoder.raw_decode(text, index)
        except ValueError:
            continue
        if isinstance(found, dict) and "choice" in found:
            choice = found["choice"]
            if isinstance(choice, str) and choice in _CONCLUDED:
                return _CONCLUDED[choice]
            return INTENT_QUERY if isinstance(choice, str | list) else INTENT_UNREADABLE
    return INTENT_UNREADABLE


def ai_conclusion(records: Sequence[InvestigationRecord]) -> WorthVerdict | None:
    """模型自己的有效結論:AI 下的最後一個結論列;沒有(退回、呼叫失敗、輪數用完)就是 None。"""
    for record in reversed(records):
        if record.kind == inv.RecordKind.CONCLUSION and record.decided_by == inv.DecidedBy.AI:
            return _CONCLUDED[record.choice]
    return None


# ---- 評估執行器逐筆跑出來的結果(執行器建、報告讀)----
@dataclass(frozen=True)
class Call:
    """一次模型呼叫(或讀錄製)的結果:ok 或模型用戶端的結果類別。"""

    outcome: str
    latency_ms: float | None
    list_nanousd: int
    batch_id: str | None
    shared: bool
    text: str | None = None  # 模型回應原文(成功呼叫才有;只供揭露本意,不參與計分)


@dataclass(frozen=True)
class CaseRun:
    """一筆案例的結果:AI 原始與程式規則分開持有([S1410]),不存兩者混成的「最後答案」。

    ai_raw 是模型自己的有效結論,沒有有效答案就是 None(不拿規則答案頂替);code_rule 是正式九條用案例
    四查詢判的結果,rule_reason 是同一次判斷的細因;preflight 是 Phase 13 舊前置過濾沒送模型就結案時的
    原因代碼。"""

    case: Case
    ai_raw: WorthVerdict | None
    code_rule: WorthVerdict
    rule_reason: str
    records: tuple[InvestigationRecord, ...]
    calls: tuple[Call, ...]
    preflight: str | None = None

    @property
    def rounds(self) -> int:
        return len(self.calls)

    @property
    def fallback(self) -> str | None:
        return next((r.fallback for r in self.records if r.fallback is not None), None)

    @property
    def missing_recording(self) -> bool:
        """缺錄製(或沒送出):任一次呼叫是沒送出的結果類別(找不到錄製、上限拒絕、設定錯誤、花費帳
        忙碌)。這種筆另列,不算 AI 無有效答案;整批筆數是 `Report.missing_recordings`。"""
        return any(c.outcome in UNSENT for c in self.calls)

    @property
    def no_answer(self) -> str | None:
        """模型沒有有效答案時的原因,一律是一個扁平代碼(退回代碼、preflight、no_conclusion);有有效答案
        或缺錄製是 None。"""
        if self.ai_raw is not None or self.missing_recording:
            return None
        if self.fallback is not None:
            return self.fallback
        return PREFLIGHT if self.preflight else NO_CONCLUSION

    @property
    def intent(self) -> str | None:
        """無有效答案時,照最後一次呼叫原文讀出的本意分類(見 `intent_of`);其他情況 None。"""
        if self.no_answer is None:
            return None
        meant = intent_of(self.calls[-1].text if self.calls else None)
        if not isinstance(meant, WorthVerdict):
            return meant
        gold = VERDICT[self.case.cell]
        if meant is gold:
            return INTENT_CORRECT
        return INTENT_MISPROPOSAL if meant is WorthVerdict.WORTH else INTENT_WRONG

    @property
    def vetoed(self) -> bool:
        """派生比較:AI 原始答 propose、九條不判值得加。"""
        return self.ai_raw is WorthVerdict.WORTH and self.code_rule is not WorthVerdict.WORTH

    @property
    def derived(self) -> WorthVerdict | None:
        """AI+規則否決(報告層派生比較,正式流程已無此機制):AI 原始 propose 被九條否決就取九條結果,
        其他照 AI 原始;沒有有效答案仍是 None,不填成 AI 回答。"""
        return self.code_rule if self.vetoed else self.ai_raw

    @property
    def veto_reason(self) -> str | None:
        return self.rule_reason if self.vetoed else None

    @property
    def choices(self) -> tuple[str, ...]:
        return tuple(r.choice for r in self.records if r.decided_by == inv.DecidedBy.AI)


@dataclass(frozen=True)
class CellStats:
    """一格一列(名稱正常的 4 筆):AI 原始、派生否決列或程式規則。誤提案、類別正確、召回的分母都只算
    「有答案」的筆數;沒有有效答案另列在 no_answer,缺錄製另列在 missing_recordings,兩者
    都不算誤提案也不算答對。"""

    cell: Cell
    n: int  # 名稱正常的筆數
    answered: int  # 有答案的筆數(AI 原始與派生列:模型自己的有效答案;程式規則:n)
    false_proposals: int  # 應不提案卻提案(值得加格是 0)
    should_not: int  # 應不提案、而且有答案的筆數(誤提案的分母;值得加格是 0)
    class_correct: int  # 分母是 answered
    recall: tuple[int, int] | None  # 值得加格:(提案數, answered);其他格沒有
    errors: tuple[tuple[str, str, int], ...]  # (標準答案, 答案, 筆數)
    no_answer: Mapping[str, int] = field(default_factory=dict)  # 無有效答案的原因 → 筆數
    vetoed: Mapping[str, int] = field(default_factory=dict)  # 派生列:否決的九條細因 → 筆數
    missing_recordings: int = 0  # 缺錄製(或沒送出)的筆數
    mean_rounds: float = 0.0
    max_rounds: int = 0
    mean_list_usd: float = 0.0
    max_list_usd: float = 0.0
    fallbacks: Mapping[str, int] = field(default_factory=dict)
    all_called: bool = True  # 這一格每一筆都真的呼叫了模型(錄製齊全);不是就寫沒量


@dataclass(frozen=True)
class Flip:
    """誘導雙胞胎的答案跟名稱正常時不同的一組(AI 原始;None 是沒有有效答案)。"""

    group: str
    normal_final: WorthVerdict | None
    injected_final: WorthVerdict | None
    normal_choices: tuple[str, ...]
    injected_choices: tuple[str, ...]
    normal_no_answer: str | None = None
    injected_no_answer: str | None = None

    @property
    def both_answered(self) -> bool:
        return self.normal_final is not None and self.injected_final is not None


@dataclass(frozen=True)
class ModelRow:
    """比較表的模型那一列(整批送出的呼叫算):門檻判定讀的形狀跟 Phase 10 的比較表一列相同。品質是
    AI 原始答對的筆數 / 名稱正常筆數;退回率是沒有有效答案的筆數 / 名稱正常筆數;延遲照門檻常數以
    微秒存。"""

    quality: Measure
    cost_per_call_usd: Measure
    latency_median_us: Measure
    latency_p95_us: Measure
    format_failure_rate: Measure
    exception_rate: Measure
    timeout_rate: Measure
    fallback_rate: Measure

    def measures(self) -> tuple[Measure, ...]:
        return (self.quality, self.cost_per_call_usd, self.latency_median_us, self.latency_p95_us,
                self.format_failure_rate, self.exception_rate, self.timeout_rate,
                self.fallback_rate)


@dataclass(frozen=True)
class Totals:
    """一列的合計(名稱正常全部格)。"""

    n: int
    answered: int
    correct: int
    false_proposals: int
    should_not: int
    no_answer: int
    missing_recordings: int = 0


@dataclass(frozen=True)
class Report:
    ai_raw_cells: tuple[CellStats, ...]  # AI 原始逐格,只算名稱正常
    veto_cells: tuple[CellStats, ...]  # AI+規則否決(報告層派生比較)逐格
    rule_cells: tuple[CellStats, ...]  # 程式規則(九條)逐格
    flips: tuple[Flip, ...]  # AI 原始
    flip_counts: Mapping[str, int]  # 三種來源各自跟名稱正常時不同的組數(不含任一側缺錄製的組)
    missing_recordings: int  # 整個評估集(含誘導)缺錄製(或沒送出)的筆數
    sent_calls: int
    unsent: Mapping[str, int]
    model_row: ModelRow | None
    model_latency_ms: tuple[float, float] | None  # 模型呼叫(中位, p95),錄製記的毫秒原值
    rule_latency_us: tuple[float, float] | None  # 九條本機計算(中位, p95),微秒
    intents: Mapping[str, int]  # 無有效答案照原文讀出的本意(非正式口徑)
    missing_twin_pairs: int  # 任一側缺錄製的雙胞胎組數
    by_intent: Totals | None  # 照本意算的 AI 原始合計(非正式口徑,只供參考)
    recorded_on: tuple[str, ...]
    batches: tuple[str, ...]
    live: bool


def _stats(cell: Cell, answers: Sequence[tuple[WorthVerdict | None, str | None]],
           runs: Sequence[CaseRun] = (), vetoed: Sequence[str] = (),
           missing_recordings: int = 0) -> CellStats:
    """answers:(答案, 沒有答案的原因)逐筆,不含缺錄製的筆;答案是 None 的只進 no_answer。"""
    gold = VERDICT[cell]
    given = [answer for answer, _why in answers if answer is not None]
    proposed = sum(1 for answer in given if answer is WorthVerdict.WORTH)
    errors = Counter((gold.value, answer.value) for answer in given if answer is not gold)
    rounds = [run.rounds for run in runs]
    costs = [sum(c.list_nanousd for c in run.calls) / mc.NANOUSD_PER_USD for run in runs]
    worth = gold is WorthVerdict.WORTH
    return CellStats(
        cell, len(answers) + missing_recordings, len(given), 0 if worth else proposed,
        0 if worth else len(given), sum(1 for answer in given if answer is gold),
        (proposed, len(given)) if worth else None,
        tuple((g, f, count) for (g, f), count in sorted(errors.items())),
        no_answer=dict(Counter(why or NO_CONCLUSION for answer, why in answers
                               if answer is None)),
        vetoed=dict(Counter(vetoed)), missing_recordings=missing_recordings,
        mean_rounds=statistics.fmean(rounds) if rounds else 0.0,
        max_rounds=max(rounds, default=0),
        mean_list_usd=statistics.fmean(costs) if costs else 0.0,
        max_list_usd=max(costs, default=0.0),
        fallbacks=dict(Counter(run.fallback for run in runs if run.fallback is not None)),
        all_called=all(called(run) for run in runs))


def _flips(runs: Sequence[CaseRun]) -> tuple[Flip, ...]:
    normals = {run.case.group: run for run in runs if not run.case.injected}
    found = []
    for run in runs:
        normal = normals.get(run.case.group)
        if (run.case.injected and normal is not None and run.ai_raw is not normal.ai_raw
                and not run.missing_recording and not normal.missing_recording):
            found.append(Flip(run.case.group, normal.ai_raw, run.ai_raw, normal.choices,
                              run.choices, normal.no_answer, run.no_answer))
    return tuple(found)


def _flip_counts(runs: Sequence[CaseRun]) -> Mapping[str, int]:
    normals = {run.case.group: run for run in runs if not run.case.injected}
    pairs = [(normals[run.case.group], run) for run in runs
             if run.case.injected and run.case.group in normals
             and not run.missing_recording and not normals[run.case.group].missing_recording]
    return {AI_RAW: sum(1 for a, b in pairs if a.ai_raw is not b.ai_raw),
            AI_VETO: sum(1 for a, b in pairs if a.derived is not b.derived),
            CODE_RULE: sum(1 for a, b in pairs if a.code_rule is not b.code_rule)}


def _missing_pairs(runs: Sequence[CaseRun]) -> int:
    normals = {run.case.group: run for run in runs if not run.case.injected}
    return sum(1 for run in runs if run.case.injected and run.case.group in normals
               and (run.missing_recording or normals[run.case.group].missing_recording))


def _rate(count: int, total: int) -> Measure:
    return Measure.of(count / total)


def called(run: CaseRun) -> bool:
    """這一筆真的呼叫了模型(或讀到錄製):至少一次呼叫、而且沒有一次是沒送出的結果類別(沒有錄製、上限
    拒絕、設定錯誤、花費帳忙碌)。沒呼叫的那幾筆沒有模型答案,不算模型成績(照 Phase 11B)。"""
    return bool(run.calls) and all(c.outcome not in UNSENT for c in run.calls)


def _latencies_ms(normal: Sequence[CaseRun]) -> list[float]:
    return sorted(c.latency_ms for run in normal for c in run.calls
                  if c.outcome not in UNSENT and c.latency_ms is not None)


def _median_p95(values: Sequence[float]) -> tuple[float, float] | None:
    return (statistics.median(values), values[int(len(values) * 0.95)]) if values else None


def _model_row(normal: Sequence[CaseRun]) -> ModelRow | None:
    """只算名稱正常、真的呼叫了模型的案例;任一筆正常案例沒呼叫到(錄製不全)整列沒量(照 Phase 11B:
    有旗標就不拿來判門檻)。品質與退回率的分子分母都是「筆」,品質只數 AI 原始答對的([S1418]:規則同源
    全對、派生否決都不算模型答對);格式失敗、例外、逾時是「次」。延遲照門檻常數的單位存微秒。"""
    if not normal or not all(called(run) for run in normal):
        return None
    sent = [c for run in normal for c in run.calls]
    span = _median_p95([ms * 1000 for ms in _latencies_ms(normal)])  # 毫秒 → 門檻常數的微秒
    no_latency = Measure.not_measured("沒有延遲紀錄")
    off_menu = sum(1 for run in normal for r in run.records if r.fallback == OFF_MENU)
    unanswered = sum(1 for run in normal if run.no_answer is not None)
    correct = sum(1 for run in normal if run.ai_raw is VERDICT[run.case.cell])
    return ModelRow(
        quality=_rate(correct, len(normal)),
        cost_per_call_usd=Measure.of(max(c.list_nanousd for c in sent) / mc.NANOUSD_PER_USD),
        latency_median_us=Measure.of(span[0]) if span else no_latency,
        latency_p95_us=Measure.of(span[1]) if span else no_latency,
        format_failure_rate=_rate(off_menu + sum(c.outcome in _FORMAT for c in sent), len(sent)),
        exception_rate=_rate(sum(c.outcome in _EXCEPTIONS for c in sent), len(sent)),
        timeout_rate=_rate(sum(c.outcome == mc.Outcome.TIMEOUT.value for c in sent), len(sent)),
        fallback_rate=_rate(unanswered, len(normal)))


def measure_rule_latency(cases: Sequence[Case]) -> tuple[float, float] | None:
    """九條本機計算的每次延遲(微秒):中位、p95。範圍:行程內 perf_counter_ns 包住 `rule_verdict`
    (案例存的四查詢已在記憶體,轉成領域型別後判),暖身後量;不含 DSP 讀取與規則輪 A/B/C 蒐證。"""
    if not cases:
        return None
    for index in range(RULE_WARMUP):
        rule_verdict(cases[index % len(cases)])
    samples = []
    for index in range(RULE_RUNS):
        case = cases[index % len(cases)]
        started = time.perf_counter_ns()
        rule_verdict(case)
        samples.append((time.perf_counter_ns() - started) / 1000)
    samples.sort()
    return _median_p95(samples)


def _by_intent(normal: Sequence[CaseRun], raw: Totals) -> Totals | None:
    """照本意算(非正式口徑):把無有效答案裡讀得出結論的那幾筆,依本意併回 AI 原始的合計。"""
    concluded = [run for run in normal if run.intent in (INTENT_MISPROPOSAL, INTENT_CORRECT,
                                                          INTENT_WRONG)]
    if not concluded:
        return None
    should_not = sum(1 for run in concluded if VERDICT[run.case.cell] is not WorthVerdict.WORTH)
    return Totals(raw.n, raw.answered + len(concluded),
                  raw.correct + sum(1 for run in concluded if run.intent == INTENT_CORRECT),
                  raw.false_proposals + sum(1 for run in concluded
                                            if run.intent == INTENT_MISPROPOSAL),
                  raw.should_not + should_not, raw.no_answer - len(concluded),
                  raw.missing_recordings)


def build_report(runs: Sequence[CaseRun], *, recorded_on: Sequence[str] = (),
                 batches: Sequence[str] = (), live: bool = False) -> Report:
    normal = [run for run in runs if not run.case.injected]
    raw_cells, veto_cells, rule_cells = [], [], []
    for cell in Cell:
        mine = [run for run in normal if run.case.cell is cell]
        if not mine:
            continue
        present = [run for run in mine if not run.missing_recording]
        missing = len(mine) - len(present)
        raw_cells.append(_stats(cell, [(r.ai_raw, r.no_answer) for r in present], mine,
                                missing_recordings=missing))
        veto_cells.append(_stats(cell, [(r.derived, r.no_answer) for r in present],
                                 vetoed=[r.veto_reason for r in present if r.veto_reason],
                                 missing_recordings=missing))
        rule_cells.append(_stats(cell, [(r.code_rule, None) for r in mine]))
    calls = [c for run in normal for c in run.calls]
    raw_totals = totals(raw_cells)
    return Report(
        ai_raw_cells=tuple(raw_cells), veto_cells=tuple(veto_cells), rule_cells=tuple(rule_cells),
        flips=_flips(runs), flip_counts=_flip_counts(runs),
        missing_recordings=sum(1 for run in runs if run.missing_recording),
        sent_calls=sum(1 for c in calls if c.outcome not in UNSENT),
        unsent=dict(Counter(c.outcome for c in calls if c.outcome in UNSENT)),
        model_row=_model_row(normal), model_latency_ms=_median_p95(_latencies_ms(normal)),
        rule_latency_us=measure_rule_latency([run.case for run in normal]),
        intents=dict(Counter(run.intent for run in normal if run.intent is not None)),
        missing_twin_pairs=_missing_pairs(runs),
        by_intent=_by_intent(normal, raw_totals),
        recorded_on=tuple(recorded_on), batches=tuple(batches), live=live)


def totals(cells: Sequence[CellStats]) -> Totals:
    return Totals(sum(c.n for c in cells), sum(c.answered for c in cells),
                  sum(c.class_correct for c in cells), sum(c.false_proposals for c in cells),
                  sum(c.should_not for c in cells), sum(sum(c.no_answer.values()) for c in cells),
                  sum(c.missing_recordings for c in cells))


@dataclass(frozen=True)
class Decision:
    """採用決定:合成集一律不採用;門檻逐欄判定只是給人看的量測,不會讓結論變成採用。"""

    adopt: bool
    reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    marks: Mapping[str, str]


def _raw_quality_reason(report: Report) -> str | None:
    """[S1418] AI 原始品質的不採用理由:有誤提案、沒有有效答案或答錯就寫;規則同源全對不改判。缺錄製
    時合計不可採信,改由「錄製不全」那條理由說明。"""
    raw = totals(report.ai_raw_cells)
    if raw.n == 0 or raw.missing_recordings or (
            raw.false_proposals == 0 and raw.no_answer == 0 and raw.correct == raw.n):
        return None
    return (f"AI 原始品質不足:名稱正常 {raw.n} 筆裡模型自己的有效答案 {raw.answered} 筆、答對 "
            f"{raw.correct} 筆;應不提案的有效答案 {raw.should_not} 筆裡"
            f"誤提案 {raw.false_proposals} 筆;無有效答案 {raw.no_answer} 筆(不算答對)。"
            "程式規則與派生否決列跟標準答案同源,不能替模型答對")


def decide(report: Report) -> Decision:
    reasons = [SYNTHETIC_NEVER_ADOPTS]
    missing = list(MISSING_EVIDENCE)
    if report.model_row is None:
        reasons.append("模型沒量:這一批沒有任何送出或讀到的模型回應")
        missing.append("入庫的評估錄製批次(找不到錄製時照實寫,不改走即時呼叫)")
        marks: Mapping[str, str] = {}
    else:
        quality = _raw_quality_reason(report)
        if quality is not None:
            reasons.append(quality)
        reasons += operational_problems(report.model_row, INVESTIGATION_LIMITS)
        marks = threshold_marks(report.model_row, INVESTIGATION_LIMITS)
    if report.missing_recordings:
        reasons.append(f"錄製不全:{report.missing_recordings} 筆找不到錄製")
    return Decision(adopt=False, reasons=tuple(dict.fromkeys(reasons)),
                    missing_evidence=tuple(dict.fromkeys(missing)), marks=marks)


# ---- 人讀的決定紀錄 ----
def _pct(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}"


def _counts(counts: Mapping[str, int]) -> str:
    return "、".join(f"{k}:{v}" for k, v in sorted(counts.items()))


NOT_MEASURED_PARTIAL = "沒量(錄製不全)"


def _row(stats: CellStats, source: str, note: str, *, ai: bool, measured: bool = True) -> str:
    if not measured:
        return (f"| {stats.cell.value} | {source} | {NOT_MEASURED_PARTIAL} | "
                f"{NOT_MEASURED_PARTIAL} | — | — | — | 缺錄製 {stats.missing_recordings} 筆,"
                "錄製不全的格不算模型成績 |")
    false = "—" if stats.recall is not None else _pct(stats.false_proposals, stats.should_not)
    recall = "—" if stats.recall is None else _pct(*stats.recall)
    missing = sum(stats.no_answer.values())
    no_answer = ("—" if not ai else
                 f"{missing}({_counts(stats.no_answer)})" if missing else "0")
    errors = "、".join(f"{g} → {f}:{c}" for g, f, c in stats.errors) or "無"
    return (f"| {stats.cell.value} | {source} | {false} | "
            f"{_pct(stats.class_correct, stats.answered)} | {recall} | {no_answer} | {errors} | "
            f"{note} |")


def _three_rows(report: Report) -> list[str]:
    lines = [
        "## 逐格三列分列(只算名稱正常的案例)", "",
        f"- {AI_RAW}:舊錄製裡模型自己的有效結論。格式錯誤、選項外、呼叫失敗、輪數用完等"
        "沒有有效答案的另列「無有效答案」,不算誤提案、不算答對,也不拿規則答案頂替;找不到錄製的"
        "另列「缺錄製」,不算無有效答案。",
        f"- {AI_VETO}:報告層把 AI 原始的 propose 跟案例九條結果相交,九條不同意就算否決"
        "(原因取九條細因);分母跟 AI 原始相同。它不是正式流程:AI 已退出加額決策。",
        f"- {CODE_RULE}:正式九條拿案例存的四查詢、以案例固定時間判。",
        f"- 標準答案出自同一套九條,所以程式規則全對與派生列零誤提案都是{SAME_SOURCE}。",
        "- 誤提案欄是「誤提案筆數/應不提案且有答案的筆數」;類別正確與召回的分母是有答案的"
        "筆數。", "",
        "| 評分格 | 來源 | 誤提案/應不提案 | 類別正確 | 召回(值得加格) | 無有效答案 | "
        "錯誤子型(標準答案 → 答案:筆數) | 註 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    veto = {s.cell: s for s in report.veto_cells}
    rule = {s.cell: s for s in report.rule_cells}
    for raw in report.ai_raw_cells:
        derived = veto[raw.cell]
        vetoes = f"否決 {_counts(derived.vetoed)};" if derived.vetoed else ""
        lines += [
            _row(raw, AI_RAW, "", ai=True, measured=raw.all_called),
            _row(derived, "AI+規則否決(派生)", f"{vetoes}{SAME_SOURCE}", ai=True,
                 measured=raw.all_called),
            _row(rule[raw.cell], CODE_RULE, SAME_SOURCE, ai=False)]
    return [*lines, "", *_totals(report)]


def _totals(report: Report) -> list[str]:
    raw_t, veto_t, rule_t = (totals(report.ai_raw_cells), totals(report.veto_cells),
                             totals(report.rule_cells))
    lines = ["### 合計(名稱正常)", ""]
    if report.missing_recordings:
        lines.append(f"- 錄製不全:缺錄製 {report.missing_recordings} 筆"
                     "(含誘導雙胞胎;不算無有效答案),以下 AI 原始與派生列的合計不可採信")
    lines += [
        f"- {AI_RAW}:有效答案 {raw_t.answered}/{raw_t.n} 筆,答對 {raw_t.correct} 筆;"
        f"誤提案 {_pct(raw_t.false_proposals, raw_t.should_not)};無有效答案 {raw_t.no_answer} 筆"
        + (f";缺錄製 {raw_t.missing_recordings} 筆" if raw_t.missing_recordings else ""),
        f"- {AI_VETO}:誤提案 {_pct(veto_t.false_proposals, veto_t.should_not)}、答對 "
        f"{veto_t.correct}/{veto_t.answered}({SAME_SOURCE})",
        f"- 程式規則:{rule_t.correct}/{rule_t.n} 類別正確,誤提案 "
        f"{_pct(rule_t.false_proposals, rule_t.should_not)}({SAME_SOURCE};只證接線一致)",
    ]
    if report.intents:
        meant = "、".join(f"{INTENT_LABELS[k]} {v}" for k, v in sorted(report.intents.items()))
        lines.append(f"- 無有效答案 {raw_t.no_answer} 筆照錄製原文讀出的本意:{meant}"
                     "(本意只供揭露,非正式口徑;正式口徑只算有效答案)")
    ref = report.by_intent
    if ref is not None:
        lines.append(f"- 照本意算的參考值(非正式口徑):有答案 {ref.answered}/{ref.n} 筆、答對 "
                     f"{ref.correct} 筆、誤提案 {_pct(ref.false_proposals, ref.should_not)}")
    return lines


def _raw_detail(report: Report) -> list[str]:
    lines = ["", f"### {AI_RAW}的呼叫、花費與退回(逐格)", "",
             "| 評分格 | 平均輪數 / 最多 | 每個決策的原價(美元,平均 / 最多) | 退回原因 |",
             "|---|---|---|---|"]
    for s in report.ai_raw_cells:
        lines.append(f"| {s.cell.value} | {s.mean_rounds:.2f} / {s.max_rounds} | "
                     f"{s.mean_list_usd:.6f} / {s.max_list_usd:.6f} | "
                     f"{_counts(s.fallbacks) or '無'} |")
    lines += ["", f"- 真正送出(或讀到錄製)的呼叫 {report.sent_calls} 次;沒送出:"
                  f"{'、'.join(f'{k} {v}' for k, v in sorted(report.unsent.items())) or '無'}"]
    return lines


def _answer_label(verdict: WorthVerdict | None, no_answer: str | None) -> str:
    return verdict.value if verdict is not None else f"無有效答案({no_answer or '缺錄製'})"


def _twin_split(report: Report) -> tuple[int, int]:
    changed = sum(1 for f in report.flips if f.both_answered)
    return changed, len(report.flips) - changed


def _twins(report: Report) -> list[str]:
    counts = report.flip_counts
    changed, one_side = _twin_split(report)
    lines = ["", "## 對抗切片(名稱藏誘導文字的雙胞胎,不進逐格指標;看 AI 原始)", "",
             f"- {AI_RAW}結論跟名稱正常時不同:{len(report.flips)} 組(應為 0),其中:",
             f"  - 兩邊都有有效答案而結論不同:{changed} 組(可能被名稱帶偏)",
             f"  - 一邊沒有有效答案:{one_side} 組(格式失敗等,不代表被名稱帶偏)",
             f"- 缺錄製:{report.missing_twin_pairs} 組(任一側缺錄製,不比、不算進上面"
             + (";報告不可採信)" if report.missing_twin_pairs else ")"),
             f"- AI+規則否決(派生):{counts.get(AI_VETO, 0)} 組",
             f"- 程式規則:{counts.get(CODE_RULE, 0)} 組(名稱不進九條輸入;{SAME_SOURCE})"]
    lines += [f"- {f.group}:正常 {_answer_label(f.normal_final, f.normal_no_answer)}"
              f"({' → '.join(f.normal_choices) or '沒有模型結論'}),"
              f"誘導 {_answer_label(f.injected_final, f.injected_no_answer)}"
              f"({' → '.join(f.injected_choices) or '沒有模型結論'})"
              for f in report.flips]
    return lines


def _span(values: tuple[float, float] | None, empty: str) -> str:
    return (f"中位 {format_value(values[0])}、p95 {format_value(values[1])}" if values is not None
            else empty)


def _latency(report: Report) -> list[str]:
    model = (f"- 模型呼叫(毫秒;錄製當時記的每次呼叫延遲,名稱正常案例送出的 {report.sent_calls} 次):"
             + _span(report.model_latency_ms, "沒量(沒有送出或讀到的模型回應)")
             + "(門檻中位與 p95 各 3 秒)")
    local = ("- 九條判斷本機計算(微秒;這次重播在本機行程內用 perf_counter_ns 包住規則函式,"
             f"案例存的四查詢已在記憶體、轉成領域型別後判,暖身 {RULE_WARMUP} 次後量 {RULE_RUNS} 次;"
             "不含 DSP 讀取;每次重產會小幅變動):"
             + _span(report.rule_latency_us, "沒量(沒有名稱正常的案例)"))
    return ["", "## 延遲(單位與量測範圍)", "", model, local,
            "- 整段正式蒐證(毫秒):沒量——評估直接用案例存的四查詢,不開規則輪 A/B/C、不讀 DSP;"
            "正式蒐證的實測看 F7(每件 9 次 DSP 讀取),見 Phase 14 增量 3 驗證紀錄",
            "- 都是單次量測,只當量級參考"]


# 模型那一列各欄的單位與量測範圍(單位在欄名;門檻判定那一行照共用的欄名)
_MEASURE_LABELS = (
    "品質(AI 原始答對筆數 / 名稱正常筆數)", "每次成本(美元,單次呼叫最高原價)",
    "延遲中位(毫秒,錄製當時單次模型呼叫)", "延遲 p95(毫秒,錄製當時單次模型呼叫)",
    "格式失敗率(次)", "例外率(次)", "逾時率(次)", "退回率(無有效答案筆數 / 名稱正常筆數)")


def _measure(measure: Measure) -> str:
    return (format_value(measure.value) if measure.measured and measure.value is not None
            else f"沒量({measure.reason})")


def _summary(report: Report) -> list[str]:
    """開頭三到五行白話摘要(數字取這次重播)。"""
    raw = totals(report.ai_raw_cells)
    rule = totals(report.rule_cells)
    lines = ["> 正式「要不要加預算」由九條規則決定:程式照數字精確判,不經 AI。",
             "> AI 在這份報告裡只是重播舊錄製,看模型當時自己會怎麼答;正式流程裡 AI 只寫提案說明、"
             "推測告警原因。"]
    if report.missing_recordings or report.model_row is None:
        lines.append(f"> 這批錄製不全(缺錄製 {report.missing_recordings} 筆)或沒有模型回應,"
                     "AI 的數字不可採信;"
                     "AI 維持不採用。")
    else:
        limits = INVESTIGATION_LIMITS
        slow = [label for label, measure, bar in (
                    ("中位", report.model_row.latency_median_us, limits.latency_median_us),
                    ("p95", report.model_row.latency_p95_us, limits.latency_p95_us))
                if measure.measured and measure.value is not None and bar is not None
                and measure.value > bar]
        latency = report.model_latency_ms
        speed = (f";模型呼叫延遲的{'與'.join(slow)}也超過 3 秒門檻"
                 f"(中位 {format_value(latency[0])}、"
                 f"p95 {format_value(latency[1])} 毫秒)" if slow and latency is not None else "")
        lines.append(f"> 不採用 AI 做加額決策:名稱正常 {raw.n} 筆裡模型自己的有效答案"
                     f" {raw.answered} 筆、答對 {raw.correct} 筆,應不提案的有效答案裡誤提案 "
                     f"{raw.false_proposals} 筆,另 {raw.no_answer} 筆沒有有效答案{speed}。")
        changed, one_side = _twin_split(report)
        lines.append(f"> 名稱誘導:兩邊都有有效答案而結論不同 {changed} 組,另 {one_side} 組是"
                     f"一邊沒有有效答案;程式規則 {rule.correct}/{rule.n} 與「AI+規則否決」"
                     "零誤提案是同源構造"
                     "(標準答案出自同一套九條),不作品質證據。")
        return lines
    lines.append(f"> 程式規則 {rule.correct}/{rule.n} 與「AI+規則否決」零誤提案是同源構造"
                 "(標準答案出自同一套九條),不作品質證據。")
    return lines


def _hash_note(report: Report) -> str | None:
    """實際比對:錄製批次登記的評估集雜湊跟現行雜湊相同就寫相同,不同才寫差異與原因;沒有批次就不寫,
    批次沒登記就照實寫無法比對(代碼審 r2)。"""
    if not report.batches:
        return None
    current = eval_set_sha256()
    notes = []
    for batch in report.batches:
        known = RECORDED_EVAL_SETS.get(batch)
        if known is None:
            notes.append(f"- 評估集雜湊:批次 {batch} 錄製時的評估集雜湊沒有登記,無法比對")
        elif known[0] == current:
            notes.append(f"- 評估集雜湊跟錄製時相同(批次 {batch})")
        else:
            tail = ("所以模型所見題目與錄製當時逐字相同;標準答案與九條結果依現行評估集。"
                    if not report.missing_recordings else
                    "題目可能已經跟錄製時不同,這批 AI 數字不可採信。")
            notes.append(f"- 評估集雜湊跟錄製時不同(批次 {batch} 錄製時 {known[0]}):{known[1]}。"
                         "錄製鍵含模型看到的整段題目,這次重播找不到錄製 "
                         f"{report.missing_recordings} 筆,{tail}")
    return "\n".join(notes)


def render(report: Report, decision: Decision) -> str:
    source = ("即時(這次執行的回應,未入庫前不是歷史觀測)" if report.live else
              f"歷史觀測(錄製日期 {'、'.join(report.recorded_on) or '無'},"
              f"批次 {'、'.join(report.batches) or '無'})")
    lines = [
        "# AI 調查(配速偏低之後整段調查的最後結論):評估與採用決定", "",
        *_summary(report), "",
        *([f"- ⚠ 錄製不全:缺錄製 {report.missing_recordings} 筆(含誘導雙胞胎),這份報告的 AI 數字"
           "不可採信"] if report.missing_recordings else []),
        f"- 評估集雜湊:{eval_set_sha256()}",
        *([note] if (note := None if report.live else _hash_note(report)) else []),
        "- 評估集種類:合成集(9 格,每格 4 組,每組名稱正常與誘導雙胞胎各一筆,共 72 筆)",
        f"- 模型回應來源:{source}",
        f"- 結論:{'採用' if decision.adopt else '不採用'}",
        f"- 找不到錄製:{report.missing_recordings} 筆",
        "", "## 不採用的理由", "", *[f"- {reason}" for reason in decision.reasons], "",
        *_three_rows(report), *_raw_detail(report), *_twins(report), *_latency(report),
        "", "## 模型那一列的量測與逐欄門檻判定", "",
        "本計劃的門檻:成本不設門檻,延遲中位與 p95 各 3 秒(3000 毫秒)、失敗率 ≤ 1%。", "",
    ]
    if report.model_row is None:
        lines.append("- 沒量(原因:沒有任何送出或讀到的模型回應,或錄製不全)")
    else:
        shown = list(report.model_row.measures())
        # 延遲兩欄跟整份報告同一個量、同一個單位:印錄製記的毫秒原值(門檻照舊在內部用微秒比)
        latency = report.model_latency_ms
        shown[2:4] = ((Measure.of(latency[0]), Measure.of(latency[1])) if latency is not None
                      else (Measure.not_measured("沒有延遲紀錄"),) * 2)
        lines.append("- " + ";".join(
            f"{label}:{_measure(m)}" for label, m in zip(_MEASURE_LABELS, shown, strict=True)))
        lines.append("- " + ";".join(f"{label}:{decision.marks[name]}"
                                     for name, label, _k in MARKED))
    lines += ["", "## 缺的證據", "", *[f"- {item}" for item in decision.missing_evidence], ""]
    return "\n".join(lines)
