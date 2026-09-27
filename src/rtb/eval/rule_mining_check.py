"""規則模式探索的模型回覆解析、去重、同探索集重算與保留側判定(Phase 15 增量 2,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈模型建議、機械核對與人讀報告〉)。

- 封閉 JSON:頂層只有 `version`、`suggestions`;每條只有 `clauses`、`direction`、`support`、
  `counterexample`、`confidence_note`;子句只有 `condition`、`threshold`。
- **整份拒絕**(記 1 筆無效提交):回覆超過 6000 UTF-8 位元組(照收到的字串量,`\\uXXXX` 逸出照六個
  位元組算)、原始文字編不成 UTF-8(孤立代理字元)、括號巢狀超過 `MAX_NESTING` 層(解析前先掃,字串
  裡的括號不算)、解析錯(含超過 4300 位的整數——位數自己數,不看直譯器的整數位數上限設定)、任一
  物件層重複鍵、`NaN`/`Infinity`/`1e400` 等非有限數、頂層/建議/子句任一物件層未知鍵、頂層缺鍵或
  不是物件、錯版本、`suggestions` 不是陣列或超過 K 條。整份拒絕不截前 K。解析期任何例外都收成
  整份拒絕,不往外丟(代碼審 r1:平衡的 500 層巢狀曾讓遞迴檢查爆掉)。巢狀在上限內、出現在單條
  欄位裡的,照型別錯只丟該條;整份與單條的分界由這裡的常數決定,不隨執行緒堆疊大小漂移(代碼審 r2)。
- **只丟該條**(記原因、計一筆無效提交):建議不是物件或缺欄、子句不是陣列或子句不是完整物件、代碼
  型別或格式錯、未知欄位、門檻不屬該欄、零或三個以上子句、同欄重複、未知方向、筆數不是 0 到九位數的
  整數(布林、`1.0` 都不算)、說明型別錯或超過 80 字/240 位元組或含控制字元、格式字元(零寬、方向
  標記)、代理字元、引號、反斜線(系統提示照同一份規則告訴模型);同一正規化鍵重複或同條件押相反方向
  時留先出現的合法條、後條丟。逐條檢查途中的任何例外只丟該條,不往外丟。
- 重算:每條語法合法的建議,在模型看到的同一探索集原資料上,用增量 1 的事件、對照索引、不放回配對與
  彙總函式重算;支持或反例跟模型自報不同記「無法核對」,重算未達有方向 20 對且兩側各 20 支相異廣告
  記「未達樣本下限」,兩者都排出有效清單、計無效提交。有效條目只帶程式重算的數字,不帶模型的說明。
- 保留側:有效條目在保留側獨立重算,照增量 1 凍結的門檻判可保留/不保留;結果不回饋模型。
- 純函式、標準函式庫;不匯入模型用戶端、模型閘道或 DSP。不執行模型文字。
"""

import json
import math
import unicodedata
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any

from rtb.eval import rule_mining_baseline as b
from rtb.eval import rule_mining_vocab as v
from rtb.eval.rule_mining_history import History

REPLY_VERSION = 1
TOP_KEYS = frozenset({"version", "suggestions"})
SUGGESTION_KEYS = frozenset({"clauses", "direction", "support", "counterexample",
                             "confidence_note"})
CLAUSE_KEYS = frozenset({"condition", "threshold"})
MAX_COUNT = 999_999_999  # 筆數至多九位
# 整數位數上限:照標準函式庫預設的整數字串位數上限(4300)寫死,超過就整份拒絕;不讀直譯器當下的設定
# (代碼審 r1:設成 0 時同一份回覆會變成單條剔除)
MAX_INT_DIGITS = 4300
_SAFE_INT_DIGITS = 640  # 直譯器能設的最小位數上限;不超過它的整數直接 int() 一定轉得動
# 說明欄不准的 Unicode 類別:控制(Cc)、格式(Cf,零寬與方向標記)、代理(Cs)。規則也寫在系統提示
NOTE_BANNED_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
# 括號巢狀上限(`{` 與 `[` 合計):合法回覆最深 5 層(頂層物件 → suggestions 陣列 → 建議物件 → clauses
# 陣列 → 子句物件)。上限要遠高於合法深度、又遠低於任何執行緒堆疊下 json 解析器吃得下的深度(代碼審 r2
# 實測 256 KiB 堆疊約 1647 層、musl 預設 128 KiB 約八百層),取 64:分界由這個常數決定,不看環境。
# 只管解析,不改提示或評估版本參數(合法回覆本來就不會碰到它)。
MAX_NESTING = 64

# 整份拒絕的原因
TOO_LARGE = "reply_too_large"
UNPARSABLE = "unparsable"  # JSON 解析錯,含超長整數
TOO_DEEP = "nesting_too_deep"  # 括號巢狀超過 MAX_NESTING
DUPLICATE_KEY = "duplicate_key"
NON_FINITE = "non_finite"
NOT_OBJECT = "top_not_object"
UNKNOWN_KEY = "unknown_key"
MISSING_KEY = "missing_top_key"
WRONG_VERSION = "wrong_version"
NOT_LIST = "suggestions_not_list"
TOO_MANY = "too_many_suggestions"
# 只丟該條的原因(另有語彙模組 VocabularyError 的原因代碼)
BAD_SUGGESTION = "suggestion_not_object"
MISSING_FIELD = "missing_field"
BAD_CLAUSES = "bad_clauses"
BAD_COUNT = "bad_count"
BAD_NOTE = "bad_note"
UNREADABLE_SUGGESTION = "unreadable_suggestion"  # 逐條檢查途中出意外(防線,正常走不到)
DUPLICATE = "duplicate_suggestion"
OPPOSITE = "opposite_direction"
COUNT_MISMATCH = "count_mismatch"  # 無法核對
BELOW_FLOOR = "below_floor"  # 未達樣本下限


class _Rejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Suggestion:
    """語法合法的一條建議:正規化鍵與模型自報的支持/反例(說明不留,只在原始回覆)。"""

    index: int  # 在回覆 suggestions 裡的位置(0 起)
    key: v.NormalizedKey
    support: int
    counter: int


@dataclass(frozen=True)
class Invalid:
    """一筆無效提交;index 是 None 表示整份回覆被拒絕。"""

    index: int | None
    reason: str
    key: v.NormalizedKey | None = None


@dataclass(frozen=True)
class Parsed:
    rejected: str | None  # 整份拒絕的原因
    submitted: int  # 回覆裡的建議條數(整份拒絕時 0)
    accepted: tuple[Suggestion, ...]  # 語法合法且去重後
    dropped: tuple[Invalid, ...]


# ---- 解析 ----
def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _Rejected(DUPLICATE_KEY)
        result[key] = value
    return result


def _constant(_name: str) -> float:
    raise _Rejected(NON_FINITE)  # NaN、Infinity、-Infinity


def _float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # 1e400 這類溢位成 inf
        raise _Rejected(NON_FINITE)
    return value


def _int(text: str) -> int:
    digits = len(text.lstrip("-"))
    if digits > MAX_INT_DIGITS:
        raise _Rejected(UNPARSABLE)
    # 不超過直譯器最小可設上限的直接轉;更長的經十進位轉,不受直譯器的整數位數設定影響
    return int(text) if digits <= _SAFE_INT_DIGITS else int(Decimal(text))


def _finite(data: object) -> bool:
    """解析後再驗一次沒有非有限數(第二道;第一道在解析時)。用顯式堆疊,不遞迴:任何深度都不爆。"""
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return True


def nesting_depth(text: str) -> int:
    """JSON 文字的最大括號巢狀深度(`{`、`[` 合計),字串內容(含逸出的引號)裡的括號不算。線性掃描,
    不遞迴;不驗語法(那是 json.loads 的事),只給解析前的深度閘用。"""
    depth = deepest = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


def _load(text: str) -> object:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as broken:  # 孤立代理字元:不是合法的 UTF-8 文字
        raise _Rejected(UNPARSABLE) from broken
    if size > v.REPLY_BYTES_LIMIT:
        raise _Rejected(TOO_LARGE)
    if nesting_depth(text) > MAX_NESTING:  # 先擋,json 解析器的遞迴深度不再決定分界
        raise _Rejected(TOO_DEEP)
    try:
        data = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant,
                          parse_float=_float, parse_int=_int)
    except (ValueError, RecursionError, MemoryError) as broken:  # 解析錯(防線:深度已先擋)
        raise _Rejected(UNPARSABLE) from broken
    if not _finite(data):
        raise _Rejected(NON_FINITE)
    return data


def _structure(data: object) -> list[object]:
    """整份拒絕的結構檢查:三個物件層的未知鍵、頂層缺鍵、版本、陣列與條數。"""
    if not isinstance(data, dict):
        raise _Rejected(NOT_OBJECT)
    if set(data) - TOP_KEYS:
        raise _Rejected(UNKNOWN_KEY)
    if set(data) != TOP_KEYS:
        raise _Rejected(MISSING_KEY)
    version = data["version"]
    if type(version) is not int or version != REPLY_VERSION:
        raise _Rejected(WRONG_VERSION)
    suggestions = data["suggestions"]
    if not isinstance(suggestions, list):
        raise _Rejected(NOT_LIST)
    if any(_unknown_keys(item) for item in suggestions):
        raise _Rejected(UNKNOWN_KEY)
    if len(suggestions) > v.K:
        raise _Rejected(TOO_MANY)
    return suggestions


def _unknown_keys(item: object) -> bool:
    """建議物件或它的子句物件有未知鍵(不是物件的另在逐條檢查丟)。"""
    if not isinstance(item, dict):
        return False
    clauses = item.get("clauses")
    return bool(set(item) - SUGGESTION_KEYS) or (isinstance(clauses, list) and any(
        isinstance(c, dict) and bool(set(c) - CLAUSE_KEYS) for c in clauses))


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_COUNT


def _note(value: object) -> bool:
    """字元先驗(代理字元在這裡就擋掉),再量 UTF-8 位元組。"""
    return (isinstance(value, str) and len(value) <= v.NOTE_CHARS
            and not any(ch in '"\\' or unicodedata.category(ch) in NOTE_BANNED_CATEGORIES
                        for ch in value)
            and len(value.encode("utf-8")) <= v.NOTE_BYTES)


def _shape_problem(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return BAD_SUGGESTION
    if set(raw) != SUGGESTION_KEYS:
        return MISSING_FIELD
    clauses = raw["clauses"]
    if not isinstance(clauses, list) or not all(
            isinstance(c, dict) and set(c) == CLAUSE_KEYS for c in clauses):
        return BAD_CLAUSES
    return None


def _suggestion(index: int, raw: object) -> Suggestion | Invalid:
    try:
        return _checked_suggestion(index, raw)
    except (ValueError, TypeError, RecursionError):  # 防線:單條出意外只丟該條
        return Invalid(index, UNREADABLE_SUGGESTION)


def _checked_suggestion(index: int, raw: object) -> Suggestion | Invalid:
    problem = _shape_problem(raw)
    if problem is not None or not isinstance(raw, dict):
        return Invalid(index, problem or BAD_SUGGESTION)
    clauses = raw["clauses"]
    try:
        key = v.normalized_key([(c["condition"], c["threshold"]) for c in clauses],
                               raw["direction"])
    except v.VocabularyError as bad:
        return Invalid(index, bad.reason)
    if not (_count(raw["support"]) and _count(raw["counterexample"])):
        return Invalid(index, BAD_COUNT, key)
    if not _note(raw["confidence_note"]):
        return Invalid(index, BAD_NOTE, key)
    return Suggestion(index, key, raw["support"], raw["counterexample"])


def _deduplicated(items: Iterable[Suggestion | Invalid]
                  ) -> tuple[tuple[Suggestion, ...], tuple[Invalid, ...]]:
    """留先出現的合法條;同鍵重複、同條件押相反方向的後條丟。"""
    kept: dict[v.ConditionKey, Suggestion] = {}
    dropped: list[Invalid] = []
    for item in items:
        if isinstance(item, Invalid):
            dropped.append(item)
            continue
        first = kept.get(item.key[0])
        if first is None:
            kept[item.key[0]] = item
        else:
            reason = DUPLICATE if first.key == item.key else OPPOSITE
            dropped.append(Invalid(item.index, reason, item.key))
    return tuple(kept.values()), tuple(dropped)


def parse_reply(text: str) -> Parsed:
    """解析一份模型回覆。整份拒絕回 `rejected` 原因;否則逐條驗、去重。"""
    try:
        suggestions = _structure(_load(text))
    except _Rejected as rejected:
        return Parsed(rejected.reason, 0, (), ())
    except (ValueError, TypeError, RecursionError, MemoryError):  # 防線:解析期意外一律整份拒絕
        return Parsed(UNPARSABLE, 0, (), ())
    accepted, dropped = _deduplicated(_suggestion(i, raw) for i, raw in enumerate(suggestions))
    return Parsed(None, len(suggestions), accepted,
                  tuple(sorted(dropped, key=lambda d: d.index or 0)))


# ---- 重算與保留側 ----
class Recounter:
    """一側的重算器:用增量 1 的事件、對照索引、不放回配對與彙總函式,逐條件重算(不讀彙總表)。"""

    def __init__(self, history: History, side: Collection[str]) -> None:
        self._events, _ = b.side_events(history, side)
        self._index = b.control_index(history, side, {e.day for e in self._events})

    def stats(self, key: v.ConditionKey) -> b.ConditionStats:
        matching = [e for e in self._events if e.matches(key)]
        pairs, missing = b.pair_condition(key, matching, self._index)
        return b.stats_of(key, len(matching), pairs, missing)


@dataclass(frozen=True)
class Checked:
    """有效建議:只帶程式重算的數字與保留側判定。"""

    key: v.NormalizedKey
    support: int
    counter: int
    ties: int
    directed: int
    raised_ads: int
    control_ads: int
    dates: int
    directional_mean: Fraction
    holdout: b.Holdout
    holdout_stats: b.ConditionStats


@dataclass(frozen=True)
class Verification:
    rejected: str | None
    submitted: int  # 原回覆條數
    recounted: int  # 進到重算的條數(語法合法且去重後)
    valid: tuple[Checked, ...]
    invalid: tuple[Invalid, ...]

    @property
    def n(self) -> int:
        """AI 有效建議數。"""
        return len(self.valid)

    @property
    def invalid_count(self) -> int:
        """無效提交數:整份拒絕記 1 筆;否則每條單條剔除、去重、核對不符、未達下限各記 1 筆。"""
        return 1 if self.rejected is not None else len(self.invalid)

    @property
    def mismatches(self) -> int:
        """機械筆數核對失敗數(只量數字轉錄一致性)。"""
        return sum(1 for i in self.invalid if i.reason == COUNT_MISMATCH)


def _check(item: Suggestion, explore: Recounter, holdout: Recounter) -> Checked | Invalid:
    condition, direction = item.key
    stats = explore.stats(condition)
    support, counter, mean = b.directional(stats, direction)
    if (support, counter) != (item.support, item.counter):
        return Invalid(item.index, COUNT_MISMATCH, item.key)
    if not b.meets_floor(stats) or mean is None:
        return Invalid(item.index, BELOW_FLOOR, item.key)
    held = holdout.stats(condition)
    return Checked(item.key, support, counter, stats.ties, stats.directed, stats.raised_ads,
                   stats.control_ads, stats.dates, mean, b.holdout_verdict(held, direction), held)


def verify(parsed: Parsed, explore: Recounter, holdout: Recounter) -> Verification:
    """核對一份已解析的回覆:逐條在探索側重算、有效的再判保留側。"""
    checked = [_check(item, explore, holdout) for item in parsed.accepted]
    valid = tuple(c for c in checked if isinstance(c, Checked))
    failed = [c for c in checked if isinstance(c, Invalid)]
    invalid = tuple(sorted([*parsed.dropped, *failed], key=lambda d: d.index or 0))
    return Verification(parsed.rejected, parsed.submitted, len(parsed.accepted), valid, invalid)


def reasons(verification: Verification) -> Mapping[str, int]:
    """無效提交逐原因計數(整份拒絕時只有那一個原因)。"""
    if verification.rejected is not None:
        return {verification.rejected: 1}
    counts: dict[str, int] = {}
    for item in verification.invalid:
        counts[item.reason] = counts.get(item.reason, 0) + 1
    return dict(sorted(counts.items()))
