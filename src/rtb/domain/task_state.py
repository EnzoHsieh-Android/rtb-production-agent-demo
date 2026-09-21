"""任務狀態機(分析行程這一側):合法轉換是一張表,不散落在各處的條件判斷裡。

狀態是系統已知的事實;檢查點只是「這一步的輸出已安全存好」的進度標記,兩者不同。
執行行程那一側的操作狀態(接受、驗證、執行中、成功、失敗、結果不明)之後另外定義。
"""

from enum import StrEnum
from types import MappingProxyType


class TaskState(StrEnum):
    RECEIVED = "received"
    COLLECTING_EVIDENCE = "collecting_evidence"
    ANALYZING = "analyzing"
    PROPOSED = "proposed"
    HANDED_OFF = "handed_off"  # 執行行程的收件表已接受提案,之後的操作狀態歸執行行程
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NO_ACTION = "no_action"
    SUPERSEDED = "superseded"


class IllegalTransition(ValueError):
    """不合法的狀態轉換:這是程式錯誤,不是預期中的資料狀態,所以丟例外。"""


S = TaskState

# 正常流程與重新規劃的路。任何非終點狀態另外都可以轉成失敗(下面統一加上)。
_FLOW: dict[TaskState, frozenset[TaskState]] = {
    S.RECEIVED: frozenset({S.COLLECTING_EVIDENCE}),
    S.COLLECTING_EVIDENCE: frozenset({S.ANALYZING}),
    S.ANALYZING: frozenset({S.PROPOSED, S.NO_ACTION, S.COLLECTING_EVIDENCE}),
    S.PROPOSED: frozenset({S.HANDED_OFF, S.COLLECTING_EVIDENCE}),
    S.HANDED_OFF: frozenset({S.COMPLETED, S.BLOCKED, S.SUPERSEDED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
    S.BLOCKED: frozenset(),
    S.NO_ACTION: frozenset(),
    S.SUPERSEDED: frozenset(),
}

TERMINAL_STATES = frozenset(state for state, targets in _FLOW.items() if not targets)

if set(_FLOW) != set(TaskState):  # 新增狀態卻忘了寫進表:匯入時就失敗,不等到執行時才 KeyError
    raise RuntimeError("狀態轉換表沒有涵蓋所有狀態")

# 唯讀:「終點狀態沒有出路」要靠結構保證,不能被外部改寫
TRANSITIONS: MappingProxyType = MappingProxyType({
    state: targets if state in TERMINAL_STATES else targets | {S.FAILED}
    for state, targets in _FLOW.items()
})


def _name(value: object) -> str:
    return str(getattr(value, "value", value))


def can_transition(current: object, target: object) -> bool:
    try:
        return TaskState(target) in TRANSITIONS[TaskState(current)]
    except ValueError:  # 不是任何一個已知狀態
        return False


def transition(current: object, target: object) -> TaskState:
    if not can_transition(current, target):
        raise IllegalTransition(f"不合法的轉換:{_name(current)} -> {_name(target)}")
    return TaskState(target)
