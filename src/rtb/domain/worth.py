"""「配速偏低時,這個廣告值不值得加預算」這個判斷點的輸入、輸出與評分格(Phase 10 增量 1)。

判斷點輸入只有決策當下的可信數字欄位,結構上沒有放廣告文字或整批證據的位置:候選拿不到不可信
文字是型別保證,不是呼叫端自律(比照證據型別把信任標記做在建構驗證裡)。建構時逐欄驗值,判準跟
分析端 DSP 用戶端的白名單一樣、用同一支共用檢查:預算是 0 到資料庫整數上限的整數;曝光、點擊、轉換
是整數或缺值、絕對值在上限內;花費、營收是有限數或缺值;布林一律拒絕。

評分格是這個判斷的切片,跟使用者裁定的評分表五條一一對應(由上而下第一個成立的):暫停、資料異常、
沒投放、有價值、沒價值;配速與預算不影響這個判斷,不當切片鍵。「正數」= 通過建構驗證而且大於零;
缺值、零、負數都不是正數。
"""

from dataclasses import dataclass
from enum import StrEnum

from rtb.domain._checks import is_count_or_none, is_finite_or_none, is_int_between


class CampaignStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class WorthVerdict(StrEnum):
    """判斷點的四種答案;「不知道」只有候選會答,路由遇到它就退回現行規則。"""

    WORTH = "worth"
    NOT_WORTH = "not_worth"
    INSUFFICIENT = "insufficient_evidence"
    UNSURE = "unsure"


class WorthCell(StrEnum):
    PAUSED = "paused"
    ANOMALY = "anomaly"
    NO_DELIVERY = "no_delivery"
    DELIVERY_WITH_VALUE = "delivery_with_value"
    DELIVERY_WITHOUT_VALUE = "delivery_without_value"


class WorthInputInvalid(ValueError):
    """判斷點輸入的值驗證不過(歸不了格);決策函式只接這一種,其他例外照舊往外丟。"""


@dataclass(frozen=True)
class WorthInput:
    status: CampaignStatus
    budget: int
    spend: int | float | None
    impressions: int | None
    clicks: int | None
    conversions: int | None
    revenue: int | float | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CampaignStatus):
            raise WorthInputInvalid(f"狀態不是啟用或暫停:{self.status!r}")
        # 逐欄跟分析端 DSP 用戶端白名單用同一支檢查(代碼審第 1 輪:原本六欄共用一條,大額營收被誤拒、
        # 小數曝光被誤收)
        checks = (("budget", is_int_between(0, self.budget)),
                  ("spend", is_finite_or_none(self.spend)),
                  ("impressions", is_count_or_none(self.impressions)),
                  ("clicks", is_count_or_none(self.clicks)),
                  ("conversions", is_count_or_none(self.conversions)),
                  ("revenue", is_finite_or_none(self.revenue)))
        for name, ok in checks:
            if not ok:
                raise WorthInputInvalid(f"{name} 不合分析端 DSP 用戶端白名單的判準")


def is_positive(value: int | float | None) -> bool:
    """只對通過建構驗證的值有意義:缺值、零、負數都不是正數。"""
    return value is not None and value > 0


def is_anomalous(worth_input: WorthInput) -> bool:
    """資料異常(使用者 2026-09-24 裁定,程式判得出的才算):曝光、點擊、轉換、營收、花費任一個是負數
    或缺值,或點擊多於曝光,或轉換多於點擊。數字合不合理(點擊率九成、花費過低)不判。"""
    i = worth_input
    values = (i.impressions, i.clicks, i.conversions, i.revenue, i.spend)
    if any(v is None or v < 0 for v in values):
        return True
    assert i.impressions is not None and i.clicks is not None  # noqa: S101 - 上面已排除缺值
    assert i.conversions is not None  # noqa: S101
    return i.clicks > i.impressions or i.conversions > i.clicks


def cell_of(worth_input: WorthInput) -> WorthCell:
    """評分表由上而下第一個成立的;每一筆可建構的判斷點輸入恰好落在一格([S711])。"""
    if worth_input.status is CampaignStatus.PAUSED:
        return WorthCell.PAUSED
    if is_anomalous(worth_input):
        return WorthCell.ANOMALY
    if not (is_positive(worth_input.impressions) and is_positive(worth_input.clicks)):
        return WorthCell.NO_DELIVERY
    if is_positive(worth_input.conversions) or is_positive(worth_input.revenue):
        return WorthCell.DELIVERY_WITH_VALUE
    return WorthCell.DELIVERY_WITHOUT_VALUE
