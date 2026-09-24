"""廣告指標的確定性計算:CTR、CVR、ROAS、Pacing。

純函式,不碰資料庫、網路或模型(由這個目錄的 ruff 設定機械檢查),也不使用 LLM。
每個指標的結果是「有值」或「沒有值加原因」,絕不把「不知道」悄悄變成 0:
- NO_DENOMINATOR:分母為零(例如沒有曝光),是正常的零,不是壞表現。
- MISSING_DATA:輸入缺漏,我們不知道。
- INVALID_DATA:輸入不合理(負數、非有限數、點擊比曝光多),或算出來不是有限數。
判斷優先順序:不合理 > 缺漏 > 分母為零。

這一層用「結果型別」而不是例外:指標的空值是預期中的資料狀態,批次處理不該因為一個
廣告沒資料就中斷;DSP 層用例外,因為那裡的錯誤是協定失敗,呼叫端必須分辨並處理。
"""

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
from typing import TypeGuard

from rtb.domain._checks import is_plain_number


class Reason(StrEnum):
    NO_DENOMINATOR = "no_denominator"
    MISSING_DATA = "missing_data"
    INVALID_DATA = "invalid_data"



@dataclass(frozen=True)
class MetricResult:
    """有值(value 是有限數、reason 為 None),或沒有值(value 為 None、reason 說明原因)。

    沒有實作 < > 等比較,也不能當真值用:誤把空值當數字比大小或寫成 if result:,
    會直接報 TypeError,而不是默默判成 0 或「有值」。
    """

    value: float | None
    reason: Reason | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not isinstance(self.reason, Reason):
                raise ValueError("沒有值的結果必須附上原因")
        elif self.reason is not None or not _is_finite_number(self.value):
            raise ValueError("有值的結果必須是有限數,而且不能附原因")

    def __bool__(self) -> bool:
        raise TypeError("不要把指標結果當真值用;請用 is_known、below() 或 at_least()")

    @property
    def is_known(self) -> bool:
        return self.value is not None

    def below(self, threshold: float) -> bool | None:
        """低於門檻嗎?不知道就回 None(證據不足),絕不回 True 或 False。"""
        _check_threshold(threshold)
        return None if self.value is None else self.value < threshold

    def at_least(self, threshold: float) -> bool | None:
        _check_threshold(threshold)
        return None if self.value is None else self.value >= threshold


def _unknown(reason: Reason) -> MetricResult:
    return MetricResult(value=None, reason=reason)


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    if not is_plain_number(value):
        return False
    return not isinstance(value, float) or math.isfinite(value)  # 整數一定有限,不轉成 float


def _check_threshold(threshold: object) -> None:
    if not _is_finite_number(threshold):
        raise ValueError("門檻必須是有限的數字")


def _is_valid_amount(value: object) -> TypeGuard[int | float]:
    return _is_finite_number(value) and value >= 0


def _checked(*inputs: float | None) -> tuple[float, ...] | Reason:
    """全部有效就回傳(已確定不是空值的)數字;否則回傳問題類別。不合理優先於缺漏。"""
    present = [value for value in inputs if value is not None]
    if any(not _is_valid_amount(value) for value in present):
        return Reason.INVALID_DATA
    if len(present) < len(inputs):
        return Reason.MISSING_DATA
    return tuple(present)


def _ratio(numerator: float, denominator: float) -> MetricResult:
    if denominator == 0:
        return _unknown(Reason.NO_DENOMINATOR)
    try:
        result = numerator / denominator
    except OverflowError:  # 整數相除的結果大到無法表示
        return _unknown(Reason.INVALID_DATA)
    if not math.isfinite(result):  # 例如極小的分母算出無限大:不是「有值」
        return _unknown(Reason.INVALID_DATA)
    return MetricResult(value=result)


def ctr(clicks: int | None, impressions: int | None) -> MetricResult:
    """點擊率 = 點擊 / 曝光。"""
    checked = _checked(clicks, impressions)
    if isinstance(checked, Reason):
        return _unknown(checked)
    clicked, shown = checked
    if clicked > shown:
        return _unknown(Reason.INVALID_DATA)  # 點擊不可能比曝光多
    return _ratio(clicked, shown)


def cvr(conversions: int | None, clicks: int | None) -> MetricResult:
    """轉換率 = 轉換 / 點擊。轉換可以多於點擊(例如瀏覽後轉換),所以不算不合理。"""
    checked = _checked(conversions, clicks)
    if isinstance(checked, Reason):
        return _unknown(checked)
    return _ratio(*checked)


def roas(revenue: float | None, spend: float | None) -> MetricResult:
    """廣告投資報酬率 = 營收 / 花費。"""
    checked = _checked(revenue, spend)
    if isinstance(checked, Reason):
        return _unknown(checked)
    return _ratio(*checked)


def pacing(
    spend: float | None, budget: float | None, elapsed_fraction: float | None
) -> MetricResult:
    """預算花費進度 = 實際花費 / 到目前為止預期該花的金額(預算 x 已過去的時間比例)。

    elapsed_fraction 必須在 0 到 1 之間。1.0 代表剛好符合預期,小於 1 是落後,大於 1 是超前。
    時間比例為 0 時預期花費是 0,回分母為零(即使已經花了錢:那是語意上的邊角,不另立類別)。
    """
    if _is_valid_amount(elapsed_fraction) and elapsed_fraction > 1:
        return _unknown(Reason.INVALID_DATA)  # 單欄位的不合理,即使別的欄位缺漏也優先
    checked = _checked(spend, budget, elapsed_fraction)
    if isinstance(checked, Reason):
        return _unknown(checked)
    spent, planned, elapsed = checked
    try:
        expected = planned * elapsed
    except OverflowError:  # 預算是超過浮點範圍的整數
        return _unknown(Reason.INVALID_DATA)
    if expected == 0 and planned != 0 and elapsed != 0:
        return _unknown(Reason.INVALID_DATA)  # 兩者都不是 0 乘積卻下溢成 0,不是真的分母為零
    return _ratio(spent, expected)


# ---- 收據的精確比率與固定字串(Phase 13 增量 2,[S1147] [S1162]) ----
# 送給模型與展示頁的收據數值一律是字串;比率用分數精確算(不經浮點除法:247/2000 用浮點會算成 12.35
# 再捨成 12.3,精確值加銀行家捨入是 12.4)。既有的 MetricResult 只收整數與浮點,不放分數進去(放寬它
# 等於改所有既有呼叫者的保證),所以精確比率另回「分數或三態原因代碼之一」。收據格式化與標準答案產生
# 函式都經 `exact_ratio` 取得比率,評估端不另算。
NA = "na"  # 算不出(分母為零、缺資料、資料不合理)一律寫它;可信證據的字元集不收斜線,所以不寫 n/a
Exact = Fraction | Reason
_WORST_FIRST = (Reason.INVALID_DATA, Reason.MISSING_DATA, Reason.NO_DENOMINATOR)
_CENTS = Decimal("0.01")


def _exact_input(value: object) -> Fraction | Reason:
    """一個比率輸入:缺值是缺資料;布林、負數、非有限數、不是數字是資料不合理;分數照收(巢狀比率)。"""
    if isinstance(value, Reason):
        return value
    if value is None:
        return Reason.MISSING_DATA
    if isinstance(value, Fraction):
        return value if value >= 0 else Reason.INVALID_DATA
    if not _is_valid_amount(value):
        return Reason.INVALID_DATA
    try:
        return Fraction(value)
    except (OverflowError, ValueError):
        return Reason.INVALID_DATA


def exact_ratio(numerator: object, denominator: object) -> Exact:
    """分子 ÷ 分母的精確分數,或三態原因代碼之一(不合理 > 缺漏 > 分母為零,同這支檔的既有順序)。
    點擊率、轉換率、配速比、變化百分比都由它算;輸入可以是前一次比率的結果(原因照傳)。"""
    top, bottom = _exact_input(numerator), _exact_input(denominator)
    reasons = {item for item in (top, bottom) if isinstance(item, Reason)}
    for reason in _WORST_FIRST:
        if reason in reasons:
            return reason
    assert isinstance(top, Fraction) and isinstance(bottom, Fraction)  # noqa: S101 - 上面已排除
    if bottom == 0:
        return Reason.NO_DENOMINATOR
    return top / bottom


def exact_change(before: object, after: object) -> Exact:
    """變化比例 =(後段減前段)除以前段,等於後段除以前段再減 1;前段為 0 是分母為零。
    百分比刻度由格式化乘上。"""
    ratio = exact_ratio(after, before)
    return ratio if isinstance(ratio, Reason) else ratio - 1


def exact_click_rate(clicks: object, impressions: object) -> Exact:
    """點擊率;點擊多於曝光是資料不合理(跟既有 ctr 同一條)。"""
    if (_is_valid_amount(clicks) and _is_valid_amount(impressions)
            and not isinstance(clicks, bool) and clicks > impressions):
        return Reason.INVALID_DATA
    return exact_ratio(clicks, impressions)


def percent_text(value: Exact) -> str:
    """分數寫成百分比刻度、固定 1 位小數、四捨五入到偶數;負零寫 0.0;原因代碼寫 na。"""
    if isinstance(value, Reason):
        return NA
    tenths = round(value * 1000)  # 分數的 round 是四捨五入到偶數
    sign = "-" if tenths < 0 else ""
    return f"{sign}{abs(tenths) // 10}.{abs(tenths) % 10}"


def receipt_ratio(numerator: object, denominator: object) -> str:
    return percent_text(exact_ratio(numerator, denominator))


def receipt_click_rate(clicks: object, impressions: object) -> str:
    return percent_text(exact_click_rate(clicks, impressions))


def receipt_change(before: object, after: object) -> str:
    return percent_text(exact_change(before, after))


def receipt_amount(value: object) -> str:
    """金額(花費、營收)固定 2 位小數:平台端存的是浮點,先 Decimal(repr(x)) 再量化(銀行家捨入)。
    這是收據裡唯一經過浮點的一段;缺值、負數、非有限數、量化溢位(約 1e26 以上)都寫 na。"""
    if not _is_valid_amount(value):
        return NA
    try:
        exact = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
        cents = exact.quantize(_CENTS, rounding=ROUND_HALF_EVEN, context=Context(prec=28))
    except InvalidOperation:
        return NA
    return str(cents.copy_abs() if cents == 0 else cents)


def receipt_count(value: object) -> str:
    """計數(曝光、點擊、轉換、筆數、天數)照寫整數;缺值、負數、布林、小數都寫 na。"""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    return NA
