"""規則模式探索的比較與人讀報告(Phase 15 增量 3,計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]
〈模型建議、機械核對與人讀報告〉〈拆增量〉第 3 項)。

- 比較:同一條件全集、同一探索集、同一下限與 K。窮舉取前 K 報真模式召回,再取前 n(n = AI 有效建議數)
  跟 AI 比同名額精確度/誤報;兩邊**先取定名單再過保留側,不遞補**。AI 的無效提交(單條剔除、去重、
  反方向、核對不符、未達下限;整份拒絕記 1 筆)在 AI 可計誤報的分子與分母各加一,保留側濾不掉它。
- 類別:精確命中預埋真模式才算命中;預埋的相關誘餌若保留側也成立,另列「觀察成立的相關規律」,
  不進召回、誤報分子或精確度分母;其餘(純噪音、只探索側偶合、誘餌變形、部分重疊、保留側不成立的相關
  誘餌)都計誤報。
- 判定:n=0,或任一方同名額可計分母為零(保留前或保留後),該批判「窮舉勝」,精確度寫「未量」不寫 0%。
- 報告只顯示結構化條件與程式重算的數字;**不讀也不顯示**模型的 `confidence_note` 或任何未核對的
  自由文字(有效條目 `Checked` 本來就不帶說明)。
- 純函式、標準函式庫;不匯入模型用戶端、模型閘道或 DSP。停用模型探勘時這支照留(純程式報告仍可重產)。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from rtb.domain import metrics as m
from rtb.eval import rule_mining_baseline as b
from rtb.eval import rule_mining_check as c
from rtb.eval import rule_mining_history as h
from rtb.eval import rule_mining_prompt as p
from rtb.eval import rule_mining_vocab as v

TITLE = "# AI 找規則模式:評估報告(Phase 15)"
BANNER = ("固定種子合成資料、非統計保證、非自動規則;真模式在語彙切點上,"
          "召回只是可精確表示時的上界")
SAME_CONDITIONS = "程式掃描已找到 AI 清單中的全部條件"
UNMEASURED = "未量"

# AI 欄的狀態
MEASURED = "已量"
WAITING = "等錄製"
BASELINE_ONLY = "只產純基準"
UNVERIFIED = "驗收沒過"  # 有判定可入庫的紀錄,但入庫目錄或重播沒過:不進比較與撤除判斷

# 條目類別
CAT_TRUE = "真模式"
CAT_RELATED = "觀察成立的相關規律"
CAT_RELATED_FAILED = "相關誘餌(保留側不成立)"
CAT_EXPLORE_ONLY = "只探索側誘餌"
CAT_PARTIAL = "部分重疊"
CAT_OTHER = "其他"

# 判定
EXHAUSTIVE_WINS = "窮舉勝"
EXHAUSTIVE_HOLDS = "窮舉不輸 AI"
AI_WINS = "AI 勝過窮舉"

MISMATCH_ALARM = Fraction(1, 4)  # 可解析建議的機械筆數核對失敗比例超過它,先停用模型段

TRUE_KEYS = tuple(t.key for t in h.TRUTH if t.kind == h.TRUE_PATTERN)
_TRUTH_BY_KEY = {t.key: t for t in h.TRUTH}

PLAIN: dict[str, str] = {
    v.WEEKDAY: "加額日是平日", v.WEEKEND: "加額日是週末",
    "raise_pct:band_1": "加額不到 20%", "raise_pct:band_2": "加額 20% 以上未滿 50%",
    "raise_pct:band_3": "加額 50% 以上",
    "pre_cvr:band_1": "調整前三日轉換率不到 2%",
    "pre_cvr:band_2": "調整前三日轉換率 2% 以上未滿 5%",
    "pre_cvr:band_3": "調整前三日轉換率 5% 以上",
    "spend_ratio:band_1": "調整前三日花費不到原預算 60%",
    "spend_ratio:band_2": "調整前三日花費占原預算 60% 以上未滿 90%",
    "spend_ratio:band_3": "調整前三日花費占原預算 90% 以上",
}
PLAIN_DIRECTION = {v.IMPROVE: "加額後較可能改善", v.NOT_IMPROVE: "加額後較可能沒改善"}
HOLDOUT_REASONS = {b.INSUFFICIENT_SAMPLE: "樣本不足", b.LOW_SUPPORT: "支持比例不足",
                   b.WRONG_SIGN: "方向不符", b.UNMEASURED: UNMEASURED}
# 無效提交的原因(原因代碼來自核對模組與語彙模組;沒列到的照代碼顯示)
INVALID_REASONS = {
    c.TOO_LARGE: "回覆超過 6000 位元組(整份拒絕)", c.UNPARSABLE: "解析錯(整份拒絕)",
    c.TOO_DEEP: "巢狀太深(整份拒絕)", c.DUPLICATE_KEY: "重複鍵(整份拒絕)",
    c.NON_FINITE: "非有限數(整份拒絕)", c.NOT_OBJECT: "頂層不是物件(整份拒絕)",
    c.UNKNOWN_KEY: "未知鍵(整份拒絕)", c.MISSING_KEY: "頂層缺鍵(整份拒絕)",
    c.WRONG_VERSION: "版本錯(整份拒絕)", c.NOT_LIST: "建議不是陣列(整份拒絕)",
    c.TOO_MANY: "超過 K 條(整份拒絕)",
    c.BAD_SUGGESTION: "建議不是物件", c.MISSING_FIELD: "缺欄或多欄", c.BAD_CLAUSES: "子句格式錯",
    c.BAD_COUNT: "筆數不是合法整數", c.BAD_NOTE: "說明欄不合規", c.UNREADABLE_SUGGESTION: "讀不懂",
    c.DUPLICATE: "同條件同方向重複", c.OPPOSITE: "同條件押相反方向",
    c.COUNT_MISMATCH: "無法核對(自報筆數與重算不符)", c.BELOW_FLOOR: "未達樣本下限",
    "unknown_condition": "未知欄位", "threshold_not_in_condition": "門檻不屬該欄位",
    "clause_count": "子句數不是一或二", "duplicate_condition": "同欄位重複",
    "unknown_direction": "未知方向", "bad_code": "代碼格式錯", "bad_clause": "子句格式錯",
}


def classify(key: v.NormalizedKey, kept: bool) -> str:
    """條目的真相類別(只看正規化鍵與保留側判定)。"""
    truth = _TRUTH_BY_KEY.get(key)
    if truth is not None and truth.kind == h.TRUE_PATTERN:
        return CAT_TRUE
    if truth is not None and truth.kind == h.DECOY_CORRELATED:
        return CAT_RELATED if kept else CAT_RELATED_FAILED
    if truth is not None and truth.kind == h.DECOY_EXPLORE_ONLY:
        return CAT_EXPLORE_ONLY
    if any(set(key[0]) & set(t.clauses) for t in h.TRUTH):
        return CAT_PARTIAL
    return CAT_OTHER


def partial_of(key: v.NormalizedKey) -> tuple[str, ...]:
    """部分重疊時跟哪幾條預埋條件共用子句(給人看的條件寫法)。"""
    return tuple(v.key_text(t.clauses) for t in h.TRUTH if set(key[0]) & set(t.clauses)
                 and t.key != key)


@dataclass(frozen=True)
class Entry:
    """名單裡的一條:正規化鍵、保留側判定與程式重算的數字(探索側與保留側)。"""

    key: v.NormalizedKey
    holdout: b.Holdout
    support: int = 0
    counter: int = 0
    ties: int = 0
    directed: int = 0
    raised_ads: int = 0
    control_ads: int = 0
    dates: int = 0
    mean: Fraction = Fraction(0)
    holdout_support: int = 0
    holdout_counter: int = 0
    holdout_directed: int = 0

    @property
    def kept(self) -> bool:
        return self.holdout.kept

    @property
    def category(self) -> str:
        return classify(self.key, self.kept)


def baseline_entry(ranked: b.Ranked, explore: b.ConditionStats,
                   holdout: b.ConditionStats) -> Entry:
    held_support, held_counter, _ = b.directional(holdout, ranked.direction)
    return Entry((ranked.key, ranked.direction), b.holdout_verdict(holdout, ranked.direction),
                 ranked.support, ranked.counter, explore.ties, explore.directed,
                 explore.raised_ads, explore.control_ads, explore.dates,
                 ranked.directional_mean, held_support, held_counter, holdout.directed)


def stats_entry(key: v.NormalizedKey, explore: b.ConditionStats,
                holdout: b.ConditionStats) -> Entry:
    """任一條件與方向在兩側重算的樣子(給預埋項的說明用;不必達下限)。"""
    direction = key[1]
    support, counter, mean = b.directional(explore, direction)
    held_support, held_counter, _ = b.directional(holdout, direction)
    return Entry(key, b.holdout_verdict(holdout, direction), support, counter, explore.ties,
                 explore.directed, explore.raised_ads, explore.control_ads, explore.dates,
                 mean if mean is not None else Fraction(0), held_support, held_counter,
                 holdout.directed)


def ai_entry(checked: c.Checked) -> Entry:
    held_support, held_counter, _ = b.directional(checked.holdout_stats, checked.key[1])
    return Entry(checked.key, checked.holdout, checked.support, checked.counter, checked.ties,
                 checked.directed, checked.raised_ads, checked.control_ads, checked.dates,
                 checked.directional_mean, held_support, held_counter,
                 checked.holdout_stats.directed)


# ---- 計分 ----
@dataclass(frozen=True)
class Tally:
    """一份名單的命中、可計誤報(AI 含無效提交)與另列的相關規律。"""

    hits: int
    false: int
    related: int

    @property
    def countable(self) -> int:
        return self.hits + self.false

    @property
    def precision(self) -> Fraction | None:
        return Fraction(self.hits, self.countable) if self.countable else None

    def precision_text(self) -> str:
        return f"{self.hits}/{self.countable}" if self.countable else UNMEASURED

    def false_text(self) -> str:
        return f"{self.false}/{self.countable}" if self.countable else UNMEASURED


@dataclass(frozen=True)
class Scores:
    """一份名單在保留前/後的計分;名單先取定,保留後只從原名單刪、不遞補。"""

    listed: int
    invalid: int
    pre: Tally
    post: Tally
    recall_pre: int
    recall_post: int
    decoy_hits: int  # 只探索側誘餌的精確命中

    def recall_text(self) -> str:
        return f"{self.recall_pre}/{len(TRUE_KEYS)} → {self.recall_post}/{len(TRUE_KEYS)}"


def _tally(entries: Iterable[Entry], invalid: int) -> Tally:
    cats = [e.category for e in entries]
    hits = cats.count(CAT_TRUE)
    related = cats.count(CAT_RELATED)
    return Tally(hits, len(cats) - hits - related + invalid, related)


def score(entries: Sequence[Entry], invalid: int = 0) -> Scores:
    kept = [e for e in entries if e.kept]
    return Scores(len(entries), invalid, _tally(entries, invalid), _tally(kept, invalid),
                  sum(1 for e in entries if e.category == CAT_TRUE),
                  sum(1 for e in kept if e.category == CAT_TRUE),
                  sum(1 for e in entries if e.category == CAT_EXPLORE_ONLY))


@dataclass(frozen=True)
class Verdict:
    label: str
    reasons: tuple[str, ...]
    retire_holds: bool | None  # 這一批的撤除比較成立(窮舉不輸);None = 未量


def judge(base_raw: Scores, base_quota: Scores, ai: Scores | None) -> Verdict:
    """單一種子的撤除比較。召回用窮舉前 K,精確度用同名額前 n。"""
    if ai is None:
        return Verdict(UNMEASURED, ("AI 欄未量",), None)
    if ai.listed == 0:
        return Verdict(EXHAUSTIVE_WINS, ("AI 無有效建議",), True)
    tallies = (base_quota.pre, base_quota.post, ai.pre, ai.post)
    if any(t.countable == 0 for t in tallies):
        return Verdict(EXHAUSTIVE_WINS, ("同名額有一方可計分母為零,精確度未量",), True)
    wins = []
    if ai.recall_pre > base_raw.recall_pre:
        wins.append("保留前召回")
    if ai.recall_post > base_raw.recall_post:
        wins.append("保留後召回")
    for side, mine, theirs in (("保留前", ai.pre, base_quota.pre),
                               ("保留後", ai.post, base_quota.post)):
        assert mine.precision is not None and theirs.precision is not None  # noqa: S101 - 上面已排除零分母
        if mine.precision > theirs.precision:
            wins.append(f"{side}同名額精確度")
    if wins:
        return Verdict(AI_WINS, tuple(wins), False)
    return Verdict(EXHAUSTIVE_HOLDS, (), True)


@dataclass(frozen=True)
class Overlap:
    shared: int
    ai_only: tuple[v.NormalizedKey, ...]
    base_only: int
    all_inside: bool


def overlap(base: Sequence[Entry], ai: Sequence[Entry]) -> Overlap:
    base_keys = {e.key for e in base}
    ai_keys = [e.key for e in ai]
    ai_only = tuple(k for k in ai_keys if k not in base_keys)
    shared = len(ai_keys) - len(ai_only)
    return Overlap(shared, ai_only, len(base_keys) - shared, not ai_only)


def mismatch_alarm(verification: c.Verification) -> bool | None:
    """可解析建議裡機械筆數核對失敗的比例超過 25%;沒有可解析建議回 None(未量)。
    只量數字轉錄一致性。"""
    if verification.recounted == 0:
        return None
    return Fraction(verification.mismatches, verification.recounted) > MISMATCH_ALARM


# ---- 單一種子 ----
@dataclass(frozen=True)
class Source:
    """AI 欄的錄製來源(入庫批次清單與重播帶回的錄製當時數字)。"""

    demo_id: str
    batch_id: str | None
    model: str
    recorded_at: str | None
    list_nanousd: int


@dataclass(frozen=True)
class AiResult:
    status: str
    verification: c.Verification | None = None
    source: Source | None = None
    detail: str | None = None  # 程式產生的狀態細節(例如重播的結果類別),不是模型文字


@dataclass(frozen=True)
class SeedReport:
    seed: int
    data_sha256: str
    explore_ads: int
    holdout_ads: int
    explore_events: int
    holdout_events: int
    explore_exclusions: tuple[tuple[str, int], ...]
    holdout_exclusions: tuple[tuple[str, int], ...]
    prompt_bytes: int
    table_rows: int
    baseline: tuple[Entry, ...]
    ai: AiResult
    ai_entries: tuple[Entry, ...]
    base_raw: Scores
    base_quota: Scores | None
    ai_scores: Scores | None
    verdict: Verdict
    overlap: Overlap | None
    truths: tuple[Entry, ...]  # 每個預埋項在兩側重算的樣子(不論有沒有進名單)


def seed_report(inputs: p.SeedInputs, ai: AiResult) -> SeedReport:
    explore, holdout = inputs.explore.stats, inputs.holdout.stats
    baseline = tuple(baseline_entry(r, explore[r.key], holdout[r.key])
                     for r in b.top_k(explore))
    base_raw = score(baseline)
    verification = ai.verification if ai.status == MEASURED else None
    if verification is None:
        ai_entries: tuple[Entry, ...] = ()
        base_quota, ai_scores, joined = None, None, None
    else:
        ai_entries = tuple(ai_entry(checked) for checked in verification.valid)
        base_quota = score(baseline[:verification.n])
        ai_scores = score(ai_entries, verification.invalid_count)
        joined = overlap(baseline, ai_entries)
    truths = tuple(stats_entry(t.key, explore[t.key[0]], holdout[t.key[0]]) for t in h.TRUTH)
    return SeedReport(
        inputs.history.seed, h.data_sha256(inputs.history), len(inputs.sides.explore),
        len(inputs.sides.holdout), inputs.explore.events, inputs.holdout.events,
        tuple(inputs.explore.exclusions.items()), tuple(inputs.holdout.exclusions.items()),
        p.prompt_bytes(inputs.table),
        sum(1 for s in explore.values() if b.meets_floor(s)), baseline, ai, ai_entries,
        base_raw, base_quota, ai_scores,
        judge(base_raw, base_quota or base_raw, ai_scores), joined, truths)


# ---- 人讀報告 ----
@dataclass(frozen=True)
class AttemptRow:
    """批次清單裡的一次嘗試(命令列從批次清單轉來;欄位已由清單讀取驗過格式,輸出時再逸出)。"""

    seed: int
    sequence: int
    demo_id: str
    batch_id: str
    model: str
    expected_key: str
    recording_key: str | None
    started_at: str
    finished_at: str | None
    outcome: str | None
    status: str


@dataclass(frozen=True)
class Context:
    recordings: str  # 錄製來源目錄(給人看的相對路徑)
    attempts: tuple[AttemptRow, ...] = ()
    checks: tuple[str, ...] = ()  # 錄製批次驗收的逐行結果(已排好版)


def condition_text(key: v.ConditionKey) -> str:
    return "且".join(PLAIN.get(threshold, threshold) for _, threshold in key)


def _key_cell(key: v.NormalizedKey) -> str:
    return f"{condition_text(key[0])}→{PLAIN_DIRECTION[key[1]]}(`{v.key_text(key[0])}` {key[1]})"


def _pp(value: Fraction) -> str:
    return m.percent_text(value, places=p.DIFF_PLACES)


def _holdout_cell(entry: Entry) -> str:
    counts = (f"支持 {entry.holdout_support}/反例 {entry.holdout_counter}/"
              f"有方向 {entry.holdout_directed}")
    if entry.kept:
        return f"保留({counts})"
    reasons = "、".join(HOLDOUT_REASONS.get(r, r) for r in entry.holdout.reasons)
    return f"不保留:{reasons}({counts})"


def _category_cell(entry: Entry) -> str:
    if entry.category == CAT_PARTIAL:
        return f"{CAT_PARTIAL}(與 {'、'.join(f'`{t}`' for t in partial_of(entry.key))})"
    return entry.category


def _entry_rows(entries: Sequence[Entry], others: set[v.NormalizedKey], other_label: str
                ) -> list[str]:
    head = ("| # | 條件 → 方向 | 支持/反例/平手 | 相異加額/對照廣告 | 相異日期 | 平均差值(百分點) "
            f"| 保留側 | 類別 | {other_label} |")
    rows = [head, "|" + "---|" * 9]
    for number, e in enumerate(entries, 1):
        rows.append(
            f"| {number} | {_key_cell(e.key)} | {e.support}/{e.counter}/{e.ties} "
            f"| {e.raised_ads}/{e.control_ads} | {e.dates} | {_pp(e.mean)} | {_holdout_cell(e)} "
            f"| {_category_cell(e)} | {'是' if e.key in others else '否'} |")
    return rows


def _usd(nanousd: int) -> str:
    return f"{Decimal(nanousd) / 10**9:.6f}"


def _ai_label(report: SeedReport) -> str:
    ai = report.ai
    if ai.status == MEASURED:
        return MEASURED
    if ai.detail is not None and ai.status == UNVERIFIED:
        return f"{UNVERIFIED}({ai.detail}),{UNMEASURED}"
    return f"{ai.status},{UNMEASURED}"


def _summary(reports: Sequence[SeedReport]) -> list[str]:
    lines = ["## 結論", ""]
    measured = [r for r in reports if r.ai_scores is not None]
    if len(measured) < len(reports):
        lines.append(f"- 撤除條件(RETIRE-IF):未判——三批 AI 欄未齊({len(measured)}/{len(reports)} "
                     "批已量);未量的批次 AI 欄一律寫「未量」,不當 0%")
    elif all(r.verdict.retire_holds for r in reports):
        lines.append("- 撤除條件(RETIRE-IF):程式可判的部分成立——三批窮舉都不輸 AI;"
                     "還要人確認 AI 有沒有提供經人確認的額外可核對條件,才停用模型探勘")
    else:
        lost = "、".join(str(r.seed) for r in reports if r.verdict.retire_holds is False)
        lines.append(f"- 撤除條件(RETIRE-IF):不成立——種子 {lost} AI 至少一項勝過窮舉")
    for r in reports:
        base = f"窮舉前 {r.base_raw.listed} 真模式召回(保留前 → 後){r.base_raw.recall_text()}"
        if r.ai_scores is None:
            lines.append(f"- {r.seed}:{base};AI {_ai_label(r)}")
            continue
        verdict = r.verdict.label + (f"({'、'.join(r.verdict.reasons)})"
                                     if r.verdict.reasons else "")
        lines.append(f"- {r.seed}:{base};AI 有效 {r.ai_scores.listed} 條、無效提交 "
                     f"{r.ai_scores.invalid} 筆;判定 {verdict}")
        assert r.ai.verification is not None  # noqa: S101 - 已量才有分數
        alarm = mismatch_alarm(r.ai.verification)
        if alarm:
            lines.append(f"  - 機械筆數核對失敗 {r.ai.verification.mismatches}/"
                         f"{r.ai.verification.recounted} 超過 25%:先停用模型段並重審輸出契約")
    return [*lines, ""]


def _versions(reports: Sequence[SeedReport], context: Context) -> list[str]:
    lines = [
        "## 版本與資料", "",
        f"- 評估版本 {v.EVAL_VERSION}(版本雜湊 {p.version_sha256()};系統提示、表頭、語彙與全部參數"
        f"都在雜湊內)、生成版本 {v.GENERATOR_VERSION}",
        f"- 固定種子 {'、'.join(map(str, v.SEEDS))}(不得替換,三批全列);K={v.K}、有方向配對下限 "
        f"{v.MIN_DIRECTED} 且兩側相異廣告各 {v.MIN_DISTINCT_ADS}、保留側支持比例至少 "
        f"{v.HOLDOUT_SUPPORT.numerator}/{v.HOLDOUT_SUPPORT.denominator}",
        f"- 錄製來源:`{context.recordings}`(CI 只重播入庫錄製,不呼叫即時模型)", "",
        "| 種子 | 資料雜湊 | 廣告 探索/保留 | 可推斷加額事件 探索/保留 | 事件層排除 探索 "
        "| 事件層排除 保留 | 送模型表列 | 完整提示位元組 |",
        "|" + "---|" * 8,
    ]
    for r in reports:
        lines.append(
            f"| {r.seed} | {r.data_sha256[:16]} | {r.explore_ads}/{r.holdout_ads} "
            f"| {r.explore_events}/{r.holdout_events} | {_counts(r.explore_exclusions)} "
            f"| {_counts(r.holdout_exclusions)} | {r.table_rows} | {r.prompt_bytes} |")
    lines += ["", "| 種子 | AI 欄 | 展示編號 | 批次 | 模型 | 錄製時間 | 估算花費(美元,原價) |",
              "|" + "---|" * 7]
    for r in reports:
        s = r.ai.source
        if s is None:
            lines.append(f"| {r.seed} | {_ai_label(r)} | — | — | — | — | — |")
        else:
            lines.append(f"| {r.seed} | {_ai_label(r)} | {cell(s.demo_id)} "
                         f"| {cell(s.batch_id or '—')} | {cell(s.model)} "
                         f"| {cell(s.recorded_at or '—')} | {_usd(s.list_nanousd)} |")
    return [*lines, ""]


def _counts(pairs: Sequence[tuple[str, int]]) -> str:
    return " ".join(f"{reason}={count}" for reason, count in pairs) or "無"


def _comparison(reports: Sequence[SeedReport]) -> list[str]:
    lines = [
        "## 窮舉與 AI 比較(三批全列)", "",
        "- 召回:命中預埋真模式數 / 真模式總數(2);窮舉一律用前 K。",
        "- 同名額:n = AI 有效建議數,窮舉取前 n 條比精確度;精確度 = 命中 /(命中 + 可計誤報)。",
        "- AI 的無效提交在 AI 可計誤報的分子與分母各加一,保留側濾不掉它。",
        "- 名單先取定再過保留側,不遞補;「保留前 → 後」兩欄各自列分子分母。",
        f"- 「{CAT_RELATED}」不進召回、誤報分子或精確度分母,另欄列出。", "",
        "| 種子 | 來源 | 有效候選 | 原回覆/可解析/無效提交 | 真模式召回 前K(保留前 → 後) | 名額 "
        "| 精確度(保留前 → 後) | 可計誤報(保留前 → 後) | 相關規律 | 只探索側誘餌命中 | 核對失敗 |",
        "|" + "---|" * 11,
    ]
    for r in reports:
        base = r.base_raw
        lines.append(
            f"| {r.seed} | 窮舉前 K | {base.listed} | — | {base.recall_text()} | {base.listed} "
            f"| {base.pre.precision_text()} → {base.post.precision_text()} "
            f"| {base.pre.false_text()} → {base.post.false_text()} | {base.pre.related} "
            f"| {base.decoy_hits} | — |")
        if r.ai_scores is None or r.base_quota is None:
            lines.append(f"| {r.seed} | AI | {UNMEASURED} | {UNMEASURED} | {UNMEASURED} "
                         f"| {UNMEASURED} | {UNMEASURED} | {UNMEASURED} | {UNMEASURED} "
                         f"| {UNMEASURED} | {UNMEASURED} |")
            continue
        quota, ai = r.base_quota, r.ai_scores
        assert r.ai.verification is not None  # noqa: S101 - 已量才有分數
        ver = r.ai.verification
        raw = "整份拒絕/0/1" if ver.rejected is not None else (
            f"{ver.submitted}/{ver.recounted}/{ver.invalid_count}")
        lines.append(
            f"| {r.seed} | 窮舉前 n | {quota.listed} | — | — | {ai.listed} "
            f"| {quota.pre.precision_text()} → {quota.post.precision_text()} "
            f"| {quota.pre.false_text()} → {quota.post.false_text()} | {quota.pre.related} "
            f"| {quota.decoy_hits} | — |")
        lines.append(
            f"| {r.seed} | AI | {ai.listed} | {raw} | {ai.recall_text()} | {ai.listed} "
            f"| {ai.pre.precision_text()} → {ai.post.precision_text()} "
            f"| {ai.pre.false_text()} → {ai.post.false_text()} | {ai.pre.related} "
            f"| {ai.decoy_hits} | {ver.mismatches} |")
    return [*lines, ""]


def _overlap_lines(reports: Sequence[SeedReport]) -> list[str]:
    lines = ["## AI 清單與窮舉前 K 的重疊", ""]
    for r in reports:
        if r.overlap is None or r.ai_scores is None:
            lines.append(f"- {r.seed}:AI 欄{UNMEASURED}")
            continue
        o = r.overlap
        lines.append(f"- {r.seed}:AI 有效 {r.ai_scores.listed} 條、窮舉前 K {r.base_raw.listed} "
                     f"條;交集 {o.shared}、AI 獨有 {len(o.ai_only)}、窮舉獨有 {o.base_only};"
                     f"AI 清單完全落在窮舉前 K:{'是' if o.all_inside else '否'}")
        if r.ai_scores.listed == 0:
            lines.append("  - AI 無有效建議,窮舉勝")
        elif not o.ai_only:
            lines.append(f"  - {SAME_CONDITIONS}")
        by_key = {e.key: e for e in r.ai_entries}
        lines += [f"  - AI 獨有:{_key_cell(k)};{_category_cell(by_key[k])};"
                  f"{_holdout_cell(by_key[k])}" for k in o.ai_only]
    return [*lines, ""]


def _details(r: SeedReport) -> list[str]:
    lines = [f"### 種子 {r.seed}", "", f"窮舉前 K({r.base_raw.listed} 條,依 Wilson 下界排序):", ""]
    ai_keys = {e.key for e in r.ai_entries}
    lines += _entry_rows(r.baseline, ai_keys, "也在 AI 清單")
    lines.append("")
    verification = r.ai.verification if r.ai.status == MEASURED else None
    if verification is None:
        return [*lines, f"AI 有效建議:{_ai_label(r)}", ""]
    lines += [f"AI 有效建議({verification.n} 條,照回覆順序):", ""]
    if r.ai_entries:
        lines += _entry_rows(r.ai_entries, {e.key for e in r.baseline}, "也在窮舉前 K")
    else:
        lines.append("(無)")
    lines += ["", f"AI 無效提交({verification.invalid_count} 筆,計入 AI 可計誤報):", ""]
    if verification.rejected is not None:
        reason = INVALID_REASONS.get(verification.rejected, verification.rejected)
        lines.append(f"- 整份回覆:{reason}")
    for item in verification.invalid:
        where = f"第 {item.index + 1} 條" if item.index is not None else "整份回覆"
        what = f"(條件 {_key_cell(item.key)})" if item.key is not None else ""
        lines.append(f"- {where}:{INVALID_REASONS.get(item.reason, item.reason)}{what}")
    if not verification.invalid and verification.rejected is None:
        lines.append("(無)")
    return [*lines, ""]


def _explanations(reports: Sequence[SeedReport]) -> list[str]:
    related = next(t for t in h.TRUTH if t.kind == h.DECOY_CORRELATED)
    explore_only = next(t for t in h.TRUTH if t.kind == h.DECOY_EXPLORE_ONLY)
    lines = [
        "## 誘餌與觀察成立的相關規律", "",
        f"- 預埋真模式:{';'.join(_key_cell(k) for k in TRUE_KEYS)}。",
        f"- 相關誘餌:{_key_cell(related.key)}——{related.note}。保留側也成立時列為"
        f"「{CAT_RELATED}」,照實列出但不是獨立的因果規則,不計召回、不計誤報;保留側不成立就計誤報。",
        f"- {CAT_EXPLORE_ONLY}:{_key_cell(explore_only.key)}——{explore_only.note};"
        "進了名單就計誤報。", "",
        "| 種子 | 預埋項 | 探索側 支持/反例 | 保留側 | 在窮舉前 K | 在 AI 清單 |", "|" + "---|" * 6,
    ]
    for r in reports:
        base_keys = {e.key for e in r.baseline}
        ai_keys = {e.key for e in r.ai_entries}
        for truth, e in zip(h.TRUTH, r.truths, strict=True):
            in_ai = ("是" if e.key in ai_keys else "否") if r.ai_scores is not None else UNMEASURED
            lines.append(f"| {r.seed} | {_KIND_TEXT[truth.kind]} `{v.key_text(truth.clauses)}` "
                         f"{truth.direction} | {e.support}/{e.counter} | {_holdout_cell(e)} "
                         f"| {'是' if e.key in base_keys else '否'} | {in_ai} |")
    return [*lines, ""]


_KIND_TEXT = {h.TRUE_PATTERN: "真模式", h.DECOY_CORRELATED: "相關誘餌",
              h.DECOY_EXPLORE_ONLY: "只探索側誘餌"}


LIMITS = (
    "- 這是固定三種子的合成資料,只證明流程可運作,不給統計保證,也不代表真實投放有因果效果。",
    "- 真模式與語彙切點精確對齊,所以召回只是「可精確表示時」的上界,不代表未知門檻下的召回。",
    "- 保留側門檻(有方向 20 對、支持比例 3/5)會讓沒有效果的條件偶爾碰巧過關:代碼審以 60 個種子"
    "量到約 7%。只探索側誘餌若在保留側碰巧過關,照計劃仍計誤報,不調門檻、不換種子。",
    "- 機械核對只證條件與筆數相符,不證規律普遍成立;人仍要看樣本、日期群聚、對照選法、差值、"
    "誘餌與實務可不可解釋。",
    "- 對照是同側、同日、同轉換率區間、同規模桶、整期沒有任何預算調整的廣告;即使有對照,仍可能有"
    "選擇偏差,結論只稱「可觀察關聯」,不稱加額造成效果。",
    "- 報告只列結構化條件與程式重算的數字;模型的信心說明與任何未核對的自由文字都不顯示。",
    "- 採用任何一條都要人確認、開 Issue,再經設計審與代碼審;這份報告不替正式規則宣布生效。",
)


def _attempts(context: Context) -> list[str]:
    lines = ["## 錄製嘗試(批次清單)", ""]
    if not context.attempts:
        return [*lines, "- 尚無嘗試", ""]
    lines += ["鍵只列前 12 碼;全文在批次清單。", "",
              "| 種子 | 序號 | 展示編號 | 批次 | 模型 | 預期鍵 | 錄製鍵 | 開始 | 結束 | 結果 "
              "| 狀態 |",
              "|" + "---|" * 11]
    for a in context.attempts:
        cells = (str(a.seed), str(a.sequence), a.demo_id, a.batch_id, a.model,
                 a.expected_key[:12], (a.recording_key or "—")[:12], a.started_at,
                 a.finished_at or "—", a.outcome or "—", a.status)
        lines.append("| " + " | ".join(cell(x) for x in cells) + " |")
    return [*lines, ""]


_MARKUP = str.maketrans({"|": "\\|", "<": "&lt;", ">": "&gt;", "[": "\\[", "]": "\\]",
                         "`": "\\`", "*": "\\*", "_": "\\_", "#": "\\#", "\\": "\\\\"})


def cell(text: str) -> str:
    """批次清單來的字串放進 Markdown 前逸出:換行與控制字元換成空白,表格、連結、HTML 與強調符號轉義
    (代碼審 r1 資安 F4;清單讀取時也先驗過格式)。"""
    flat = "".join(" " if ch in "\r\n\t" or ord(ch) < 32 else ch for ch in text)
    return flat.translate(_MARKUP)


def _line(text: str) -> str:
    """驗收行裡可能帶清單來的值(路徑、批次):不准換行、不准 HTML;排版用的清單符號保留。"""
    flat = "".join(" " if ord(ch) < 32 else ch for ch in text)
    return flat.replace("<", "&lt;").replace(">", "&gt;")


def render(reports: Sequence[SeedReport], context: Context) -> str:
    lines = [TITLE, "", f"> **{BANNER}。**",
             "> 這份報告只列給人看的候選;任何一條要變成正式規則,都要人確認、開 Issue,"
             "再經設計審與代碼審。", ""]
    lines += _summary(reports) + _versions(reports, context) + _comparison(reports)
    lines += _overlap_lines(reports)
    lines += ["## 逐條(程式重算的數字)", ""]
    for r in reports:
        lines += _details(r)
    lines += [*_explanations(reports), "## 已知限制", "", *LIMITS, "", *_attempts(context),
              "## 錄製批次驗收", "", *(_line(check) for check in context.checks)]
    return "\n".join(lines).rstrip("\n") + "\n"
