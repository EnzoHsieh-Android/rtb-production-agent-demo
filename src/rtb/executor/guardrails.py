"""單筆加預算的比例上限(Phase 6 增量 2 的規則;增量 3 從執行迴圈模組搬出來)。

只放常數與判斷,不碰 DSP、資料庫與設定檔:執行迴圈、人工核可的範圍指紋、核可管理工具都從這裡
匯入,管理工具不必拖進整個執行迴圈模組,也不會自己再寫一份常數。
"""

from rtb.domain.proposal import ActionType, Proposal

# 使用者 2026-09-23 裁定:五成、最小加額 1、全域一個值。
# 加的量不得超過 max(現況乘分子除以分母取整數下限, 最小加額),整數運算
MAX_INCREASE_NUMERATOR = 1
MAX_INCREASE_DENOMINATOR = 2
MIN_INCREASE_STEP = 1


def increase(proposal: Proposal, budget: int) -> int:
    """這筆提案加的量(也是它佔的總曝險):新預算減目前預算,小於 0 算 0;暫停是 0。"""
    if proposal.action_type is not ActionType.UPDATE_BUDGET:
        return 0
    return max(0, int(proposal.requested_change["new_budget"]) - budget)


def increase_too_large(proposal: Proposal, budget: int) -> bool:
    """加的量超過比例上限;減預算、暫停是 0,不受影響。budget 是處理一筆開頭讀到的現況。"""
    allowed = max(budget * MAX_INCREASE_NUMERATOR // MAX_INCREASE_DENOMINATOR, MIN_INCREASE_STEP)
    return increase(proposal, budget) > allowed
