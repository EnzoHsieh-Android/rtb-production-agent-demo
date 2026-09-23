"""單筆加預算的比例上限(Phase 6 增量 2 的規則;增量 3 從執行迴圈模組搬出來)。

只放常數與判斷,不碰 DSP、資料庫與設定檔:執行迴圈、人工核可的範圍指紋、核可管理工具都從這裡
匯入,管理工具不必拖進整個執行迴圈模組,也不會自己再寫一份常數。
"""

from datetime import datetime, timedelta

from rtb.domain.proposal import ActionType, Proposal

# 使用者 2026-09-23 裁定:五成、最小加額 1、全域一個值。
# 加的量不得超過 max(現況乘分子除以分母取整數下限, 最小加額),整數運算
MAX_INCREASE_NUMERATOR = 1
MAX_INCREASE_DENOMINATOR = 2
MIN_INCREASE_STEP = 1

# 使用者 2026-09-23 裁定:決策建立超過 15 分鐘就當成過時(Phase 8)。跟分析端證據年齡上限同一個值,
# 所以證據最多 30 分鐘舊。執行前檢查、開始一筆的交易、兩條重跑路徑都呼叫下面這一支,不各算一套
DECISION_FRESHNESS = timedelta(minutes=15)


def increase(proposal: Proposal, budget: int) -> int:
    """這筆提案加的量(也是它佔的總曝險):新預算減目前預算,小於 0 算 0;暫停是 0。"""
    if proposal.action_type is not ActionType.UPDATE_BUDGET:
        return 0
    return max(0, int(proposal.requested_change["new_budget"]) - budget)


def increase_allowance(budget: int) -> int:
    """比例上限允許加的量:max(現況乘分子除以分母取整數下限, 最小加額)。判比例與開始一筆記下的
    核對材料(Phase 9 增量 3)共用這一支,記的是算出的量、不是常數。"""
    return max(budget * MAX_INCREASE_NUMERATOR // MAX_INCREASE_DENOMINATOR, MIN_INCREASE_STEP)


def increase_too_large(proposal: Proposal, budget: int) -> bool:
    """加的量超過比例上限;減預算、暫停是 0,不受影響。budget 是處理一筆開頭讀到的現況。"""
    return increase(proposal, budget) > increase_allowance(budget)


def decision_stale(proposal: Proposal, now: datetime) -> bool:
    """現在比決策建立晚超過新鮮度上限;剛好等於上限不算過時。執行端看不到證據本身,用決策建立
    時間代替(分析端決策時證據最多 15 分鐘舊)。有這一關有效核可的提案不判,由呼叫端決定。"""
    return now - proposal.decision_created_at > DECISION_FRESHNESS
