"""規則模式探索的封閉條件語彙與評估版本常數(Phase 15 增量 1,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈封閉條件語彙、彙總與窮舉基準〉)。

- 這支檔是「評估版本」的單一出處:三個固定種子、生成版本、K、樣本下限、保留比例、切點、切分與配對
  規則、搶用次序、效果與平均公式、排序鍵、格式化與輸入輸出上限都寫死在這裡,`version_params()` 把它們
  列成一份可雜湊的清單。看任何基準或模型結果前凍結;要改先換 `EVAL_VERSION` 並記理由與可達性預檢
  ([S1515] [S1519] [S1520])。
- 條件只有四個欄位、每欄固定幾個區間;一條條件是一或兩個**不同**欄位的子句 AND。子句鍵是
  `(條件代碼, 門檻代碼全名)`,門檻代碼一律寫成「欄位:區間」(如 `raise_pct:band_2`),不能拿別欄的門檻
  套用;正規化鍵是按子句排序後的元組再接方向。九條正式規則的詞彙不擴充成這裡的語彙。
- 純函式、只用標準函式庫與領域層的精確比率;不匯入模型用戶端、模型閘道或 DSP。
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date
from fractions import Fraction
from itertools import combinations
from types import MappingProxyType
from typing import Any

# v1 → v2:代碼審 c_4/c_5 改重疊與對照池定義;v2 → v3:增量 2 代碼審 r1,說明欄字元規則寫進系統提示
EVAL_VERSION = "phase15-rule-mining-v3"
GENERATOR_VERSION = "rule-mining-history-1"
SEEDS = (15001, 15002, 15003)  # 固定,任一批不得替換;報告全列

K = 10
MIN_DIRECTED = 20  # 有方向配對(正差 + 負差)下限
MIN_DISTINCT_ADS = 20  # 有方向配對裡相異加額廣告、相異對照廣告各自的下限
HOLDOUT_SUPPORT = Fraction(3, 5)
WINDOW_DAYS = 3  # D-3..D-1 與 D+1..D+3,D 本身不算
# 兩筆調整的 D±3 視窗相交就算重疊(v2,代碼審 c_4):前一筆加額的後三日不會落進本事件的前三日
OVERLAP_DAYS = 2 * WINDOW_DAYS

PROMPT_BYTES_LIMIT = 20480  # 本案:系統提示加使用者內容的 UTF-8 位元組
GATEWAY_PROMPT_BYTES = 48 * 1024  # 既有模型用戶端的上限(測試核對 modelcore.MAX_PROMPT_BYTES)
MAX_OUTPUT_TOKENS = 6144
REPLY_BYTES_LIMIT = 6000
NOTE_CHARS = 80
NOTE_BYTES = 240
CALL_TIMEOUT_SECONDS = 60

IMPROVE, NOT_IMPROVE = "improve", "not_improve"
DIRECTIONS = (IMPROVE, NOT_IMPROVE)

DAY_TYPE, PRE_CVR, RAISE_PCT, SPEND_RATIO = "day_type", "pre_cvr", "raise_pct", "spend_ratio"
FIELDS = (DAY_TYPE, PRE_CVR, RAISE_PCT, SPEND_RATIO)  # 已按代碼排序
WEEKDAY, WEEKEND = "day_type:weekday", "day_type:weekend"
# 數值欄的切點(下界含、上界不含;比率刻度),依序切出 band_1 / band_2 / band_3。百分比以整數基點寫
CUTS: Mapping[str, tuple[Fraction, Fraction]] = MappingProxyType({
    PRE_CVR: (Fraction(200, 10000), Fraction(500, 10000)),  # 調整前三日轉換率 <2%、2-5%、≥5%
    RAISE_PCT: (Fraction(2000, 10000), Fraction(5000, 10000)),  # 加額幅度 <20%、20-50%、≥50%
    SPEND_RATIO: (Fraction(6000, 10000), Fraction(9000, 10000)),  # 前三日花費/(3 x 原日預算)
})
THRESHOLDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    DAY_TYPE: (WEEKDAY, WEEKEND),
    **{name: tuple(f"{name}:band_{i}" for i in (1, 2, 3)) for name in (PRE_CVR, RAISE_PCT,
                                                                       SPEND_RATIO)},
})
# 投放規模桶:調整前三日花費整數分
SCALE_CUTS_CENTS = (10000, 50000)
SCALE_BUCKETS = ("lt_10000", "10000_49999", "ge_50000")
CODE = re.compile(r"[a-z0-9_:]{1,24}")

Clause = tuple[str, str]
ConditionKey = tuple[Clause, ...]
NormalizedKey = tuple[ConditionKey, str]


class VocabularyError(ValueError):
    """條件不在封閉語彙內;reason 是固定的原因代碼(模型回覆解析時逐條記)。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def band(field: str, value: Fraction) -> str:
    """數值欄的門檻代碼全名。"""
    low, high = CUTS[field]
    index = 1 if value < low else 2 if value < high else 3
    return f"{field}:band_{index}"


def day_type(day: date) -> str:
    """加額日(UTC)是平日或週末;合成資料沒有國定假日,週六日即假日。"""
    return WEEKEND if day.weekday() >= 5 else WEEKDAY  # 週六是 5


def scale_bucket(spend_cents: int) -> str:
    low, high = SCALE_CUTS_CENTS
    return SCALE_BUCKETS[0] if spend_cents < low else SCALE_BUCKETS[1] if (
        spend_cents < high) else SCALE_BUCKETS[2]


def _clause(raw: object) -> Clause:
    if not (isinstance(raw, tuple) and len(raw) == 2):  # 子句是一對代碼
        raise VocabularyError("bad_clause")
    condition, threshold = raw
    if not (isinstance(condition, str) and isinstance(threshold, str)
            and CODE.fullmatch(condition) and CODE.fullmatch(threshold)):
        raise VocabularyError("bad_code")
    if condition not in THRESHOLDS:
        raise VocabularyError("unknown_condition")
    if threshold not in THRESHOLDS[condition]:
        raise VocabularyError("threshold_not_in_condition")
    return condition, threshold


def condition_key(clauses: Iterable[object]) -> ConditionKey:
    """一或兩個不同欄位的子句 → 排序後的條件鍵;語彙外、同欄重複或矛盾、零或三個以上子句都丟錯。"""
    parsed = [_clause(raw) for raw in clauses]
    if not 1 <= len(parsed) <= 2:  # 至多兩個不同欄位
        raise VocabularyError("clause_count")
    if len({condition for condition, _ in parsed}) != len(parsed):
        raise VocabularyError("duplicate_condition")
    return tuple(sorted(parsed))


def normalized_key(clauses: Iterable[object], direction: object) -> NormalizedKey:
    key = condition_key(clauses)
    if direction not in DIRECTIONS:
        raise VocabularyError("unknown_direction")
    assert isinstance(direction, str)  # noqa: S101 - 上一行已確認
    return key, direction


def all_conditions() -> tuple[ConditionKey, ...]:
    """封閉條件全集(單欄 + 兩個不同欄的組合),按正規化鍵升序;模型可選與窮舉掃的是同一份。"""
    singles: list[ConditionKey] = [((name, code),) for name in FIELDS
                                   for code in THRESHOLDS[name]]
    pairs: list[ConditionKey] = [((a, x), (b, y)) for a, b in combinations(FIELDS, 2)
             for x in THRESHOLDS[a] for y in THRESHOLDS[b]]
    return tuple(sorted(singles + pairs))


def key_text(key: ConditionKey) -> str:
    """彙總表與報告的條件寫法:門檻代碼全名以 & 相連(欄位代碼就是冒號前那段)。"""
    return "&".join(threshold for _, threshold in key)


def version_params() -> dict[str, Any]:
    """評估版本的完整參數清單(JSON 可序列化);雜湊由提示模組連同系統提示一起算。"""
    return {
        "eval_version": EVAL_VERSION, "generator_version": GENERATOR_VERSION,
        "seeds": list(SEEDS), "k": K, "min_directed": MIN_DIRECTED,
        "min_distinct_ads": MIN_DISTINCT_ADS, "holdout_support": str(HOLDOUT_SUPPORT),
        "window_days": WINDOW_DAYS,
        "split": "sha256(ad_id utf-8) 升序,同雜湊再比編號位元組;前 floor(N/2) 探索,其餘保留",
        "control_pool": "同側、整個觀察期沒有任何預算調整(加額或降額)的廣告;每條件每支至多一次",
        "pair_fields": ["side", "utc_day", "pre_cvr_band", "scale_bucket"],
        "scale_cuts_cents": list(SCALE_CUTS_CENTS), "scale_buckets": list(SCALE_BUCKETS),
        "cuts": {name: [str(c) for c in CUTS[name]] for name in sorted(CUTS)},
        "thresholds": {name: list(THRESHOLDS[name]) for name in FIELDS},
        "day_type": "UTC 星期六、日為 weekend,其餘 weekday",
        "overlap": "同一廣告另一筆調整(加額或降額)的 D±3 視窗與本事件的 D±3 視窗相交,"
                   "即相距 ≤ 2 x 3 = 6 天,就排除",
        "event_order": ["utc_day", "raised_ad_id_utf8", "committed_at", "op_id"],
        "control_order": "ad_id utf-8 位元組升序,取第一支未用者",
        "exclusions": ["incomplete_window", "overlapping_adjustment", "anomalous_data",
                       "missing_value", "zero_denominator", "no_control"],  # 判斷先後
        "effect": "(加額後三日率-加額前三日率)-(對照後三日率-對照前三日率),"
                  "率=轉換/點擊,rtb.domain.metrics.exact_ratio 精確分數",
        "mean": "有方向差值合計 / 有方向配對數;平手不計支持與反例",
        "distinct_counts": "兩側相異廣告數與相異 UTC 日期數只數有方向配對",
        "ranking": ["wilson_lower(支持, 支持+反例) 降序", "方向化精確平均差值降序",
                    "有方向配對數降序", "正規化條件鍵升序", "方向代碼升序"],
        "wilson": "rtb.eval.scoring.wilson_lower,Z_95=1.959963984540054",
        "directions_merge": "同一條件兩方向只留排序較高者,再取全域前 K",
        "formats": {"percent": "rtb.domain.metrics.percent_text(一位小數 half-even)",
                    "diff": "rtb.domain.metrics.percent_text(places=4):百分點四位小數 half-even"},
        "prompt_bytes_limit": PROMPT_BYTES_LIMIT, "gateway_prompt_bytes": GATEWAY_PROMPT_BYTES,
        "max_output_tokens": MAX_OUTPUT_TOKENS, "reply_bytes_limit": REPLY_BYTES_LIMIT,
        "note_chars": NOTE_CHARS, "note_bytes": NOTE_BYTES,
        "note_rule": "說明不含引號、反斜線與 Unicode 類別 Cc、Cf、Cs 的字元;"
                     "違反只丟該條、計無效提交",
        "call_timeout_seconds": CALL_TIMEOUT_SECONDS,
    }
