"""評分表:每個評分格對一個標準答案(Phase 10 增量 2,[S711] 評分表那半)。

「證據不足」那格與三類答案是使用者 2026-09-24 本人裁定;其餘格是設計審依審查意見補齊的暫用值,待使用
者覆核(計劃〈設計〉)。標準答案由程式照這張表算,不派代理標註。
"""

from collections.abc import Mapping
from types import MappingProxyType

from rtb.domain.worth import WorthCell, WorthInput, WorthVerdict, cell_of

RUBRIC: Mapping[WorthCell, WorthVerdict] = MappingProxyType({
    WorthCell.PAUSED: WorthVerdict.NOT_WORTH,  # 暫停中:不看其他欄位
    WorthCell.NO_DELIVERY: WorthVerdict.NOT_WORTH,  # 含有曝光沒點擊、有轉換沒點擊(暫用)
    WorthCell.DELIVERY_WITH_VALUE: WorthVerdict.WORTH,
    WorthCell.DELIVERY_WITHOUT_VALUE: WorthVerdict.INSUFFICIENT,  # 使用者裁定;缺值與負數比照(暫用)
})


def gold(worth_input: WorthInput) -> WorthVerdict:
    return RUBRIC[cell_of(worth_input)]
