"""評分表:每個評分格對一個標準答案(Phase 10 增量 2,[S711] 評分表那半)。

五條全由使用者 2026-09-24 本人裁定(原意對照後加「資料自不自洽」),由上而下第一個成立的:暫停 →
不值得加;資料異常 → 證據不足;沒投放 → 不值得加;有價值 → 值得加;沒價值 → 證據不足。評分格就是這
五條(歸格在領域層)。標準答案由程式照這張表算,不派代理標註。
"""

from collections.abc import Mapping
from types import MappingProxyType

from rtb.domain.worth import WorthCell, WorthInput, WorthVerdict, cell_of

RUBRIC: Mapping[WorthCell, WorthVerdict] = MappingProxyType({
    WorthCell.PAUSED: WorthVerdict.NOT_WORTH,
    WorthCell.ANOMALY: WorthVerdict.INSUFFICIENT,
    WorthCell.NO_DELIVERY: WorthVerdict.NOT_WORTH,
    WorthCell.DELIVERY_WITH_VALUE: WorthVerdict.WORTH,
    WorthCell.DELIVERY_WITHOUT_VALUE: WorthVerdict.INSUFFICIENT,
})


def gold(worth_input: WorthInput) -> WorthVerdict:
    return RUBRIC[cell_of(worth_input)]
