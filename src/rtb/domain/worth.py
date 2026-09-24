"""「配速偏低時,這個廣告值不值得加預算」這個判斷點的輸入、輸出與評分格(Phase 10 增量 1)。

判斷點輸入只有決策當下的可信數字欄位,結構上沒有放廣告文字或整批證據的位置:候選拿不到不可信
文字是型別保證,不是呼叫端自律(比照證據型別把信任標記做在建構驗證裡)。建構時逐欄驗值,判準跟
分析端 DSP 用戶端的白名單一樣:布林、非有限數、絕對值超過資料庫整數上限、其他型別一律拒絕。

評分格是這個判斷的切片:依狀態、曝光點擊是否都是正數、轉換營收是否有正數分成 4 格;配速與預算不影響
這個判斷,不當切片鍵。「正數」= 通過建構驗證而且大於零;缺值、零、負數都不是正數。
"""

from dataclasses import dataclass
from enum import StrEnum

from rtb.domain._checks import is_plain_number
from rtb.domain.proposal import MAX_INT


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
    NO_DELIVERY = "no_delivery"
    DELIVERY_WITH_VALUE = "delivery_with_value"
    DELIVERY_WITHOUT_VALUE = "delivery_without_value"


class WorthInputInvalid(ValueError):
    """判斷點輸入的值驗證不過(歸不了格);決策函式只接這一種,其他例外照舊往外丟。"""


def _is_amount(value: object) -> bool:
    """缺值,或不是布林的有限數字、絕對值在資料庫整數上限內。NaN 與無限大跟上限比較都不成立,
    這一道比較就把它們擋掉(不另寫有限數檢查:變異檢查證實那一道拿掉結果不變)。"""
    if value is None:
        return True
    return is_plain_number(value) and abs(value) <= MAX_INT


@dataclass(frozen=True)
class WorthInput:
    status: CampaignStatus
    budget: int | float | None
    spend: int | float | None
    impressions: int | float | None
    clicks: int | float | None
    conversions: int | float | None
    revenue: int | float | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CampaignStatus):
            raise WorthInputInvalid(f"狀態不是啟用或暫停:{self.status!r}")
        amounts = {"budget": self.budget, "spend": self.spend, "impressions": self.impressions,
                   "clicks": self.clicks, "conversions": self.conversions, "revenue": self.revenue}
        for name, value in amounts.items():
            if not _is_amount(value):
                raise WorthInputInvalid(f"{name} 不是有限、在整數上限內的數字或缺值")


def is_positive(value: int | float | None) -> bool:
    """只對通過建構驗證的值有意義:缺值、零、負數都不是正數。"""
    return value is not None and value > 0


def cell_of(worth_input: WorthInput) -> WorthCell:
    """每一筆可建構的判斷點輸入恰好落在一格([S711])。"""
    if worth_input.status is CampaignStatus.PAUSED:
        return WorthCell.PAUSED
    if not (is_positive(worth_input.impressions) and is_positive(worth_input.clicks)):
        return WorthCell.NO_DELIVERY
    if is_positive(worth_input.conversions) or is_positive(worth_input.revenue):
        return WorthCell.DELIVERY_WITH_VALUE
    return WorthCell.DELIVERY_WITHOUT_VALUE
