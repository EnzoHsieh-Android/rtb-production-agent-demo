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
from enum import StrEnum
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
