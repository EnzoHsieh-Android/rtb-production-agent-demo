"""Phase 12 展示的正式流程圖:節點、判斷點、分支,每個系統結果列舉成員對到圖上的哪裡,以及白話顯示字。

流程圖是有向無環圖:程式裡往回走的轉換(重新蒐集資料、重送、放回排隊、開新工作)各展開成一個「回頭」
節點,標明回到哪一步,不畫回原節點。展示走了不只一次時,判斷紀錄表逐次列出,圖上只有一份。

對應表的鍵是(列舉類別, 成員名):不同列舉的同名成員(擋下原因與停下種類都有總上限已滿)不互相蓋掉。
顯示字照使用者 2026-09-24 的白話裁定寫:禁用術語非用不可時要緊接括號白話解釋(`unexplained_terms`)。
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from rtb.analyzer.policy import NoActionReason, RoutePath
from rtb.analyzer.task_store import ReplanReason
from rtb.demo.state import FlowEdge, FlowGraph, FlowNode, NodeKind
from rtb.domain.attempt import AttemptState, OutcomeCode
from rtb.domain.evidence import Freshness
from rtb.domain.task_state import TaskState
from rtb.domain.worth import WorthVerdict
from rtb.executor.execution import Result, VoidOutcome
from rtb.executor.inbox_store import (
    AwaitingOutcome,
    BlockCode,
    DeadLetterReason,
    Disposition,
    LastFailure,
    LifecycleKind,
    ReplayOutcome,
    StopKind,
)

BANNED_TERMS = ("死信", "猝死", "總曝險", "冪等", "租約", "對帳", "護欄", "接續任務", "修訂",
                "結果不明")
_EXPLAINED = re.compile("[(\uff08][^()\uff08\uff09]+[)\uff09]")  # 半形或全形括號,裡面要有字


def unexplained_terms(text: str) -> list[str]:
    """顯示文字裡沒有緊接括號白話解釋的禁用術語(依清單順序,每個詞最多列一次)。"""
    return [term for term in BANNED_TERMS
            if any(not _EXPLAINED.match(text, m.end()) for m in re.finditer(term, text))]


ANALYZE, INBOX, EXECUTE, PLATFORM, HUMAN = "分析", "收件", "執行", "廣告平台", "人工"
LANES = (ANALYZE, INBOX, EXECUTE, PLATFORM, HUMAN)
START = "a_receive"

S, D, T = NodeKind.STEP, NodeKind.DECISION, NodeKind.TERMINAL
_NODES: tuple[tuple[str, str, NodeKind, str], ...] = (
    ("a_receive", "收到工作", S, ANALYZE),
    ("a_collect", "蒐集廣告的最新資料", S, ANALYZE),
    ("a_fresh", "資料夠新嗎?", D, ANALYZE),
    ("a_complete", "廣告狀態和成效資料都齊全嗎?", D, ANALYZE),
    ("a_pacing", "預算花得比預期慢嗎?", D, ANALYZE),
    ("a_route", "由誰判斷值不值得加預算?", D, ANALYZE),
    ("a_candidate", "請模型候選判斷", D, ANALYZE),
    ("a_rule", "用程式規則判斷", S, ANALYZE),
    ("a_worth", "值得加預算嗎?", D, ANALYZE),
    ("a_no_action", "不調整,結束", T, ANALYZE),
    ("a_failed", "這件工作出錯,結束", T, ANALYZE),
    ("a_propose", "寫好調整建議", S, ANALYZE),
    ("a_narrate", "請模型寫一段說明給確認的人看(僅供參考)", S, ANALYZE),
    ("a_submit", "把建議送去執行", S, ANALYZE),
    ("a_blocked_end", "被擋下,這件工作結束", T, ANALYZE),
    ("i_check", "收件檢查:這份建議能收嗎?", D, INBOX),
    ("i_superseded", "已經有更新的建議,舊的不處理", T, INBOX),
    ("x_pending", "排隊等執行", S, INBOX),
    ("x_pick", "拿起下一份建議:是不是已經試太多次?", D, EXECUTE),
    ("x_deadletter", "試太多次都沒能開始,先停下等人處理", S, EXECUTE),
    ("x_existing", "同一筆已經有人在寫,交給那一筆", T, EXECUTE),
    ("x_precheck", "寫入前再確認:廣告還在、版本沒變、規則沒變、建議沒放太久?", D, EXECUTE),
    ("x_expired", "建議已經過期,不再處理", S, EXECUTE),
    ("x_guard", "在允許的廣告、預算上限與單次加幅之內嗎?", D, EXECUTE),
    ("x_total", "全部廣告加起來會不會超過總上限?", D, EXECUTE),
    ("x_blocked", "擋下,不寫入", S, EXECUTE),
    ("x_wait_approval", "等人確認這次加預算", S, EXECUTE),
    ("x_write", "寫入廣告平台", S, EXECUTE),
    ("x_unknown", "不知道有沒有寫進去:回頭去平台查", D, EXECUTE),
    ("x_verify", "比對平台上的實際預算是不是跟建議一樣", D, EXECUTE),
    ("x_failed", "這筆寫入失敗(確定沒改到)", S, EXECUTE),
    ("x_escalated", "交給人判斷,這個廣告先鎖住不再動", S, EXECUTE),
    ("x_done", "完成:平台上的預算已照建議改好", T, EXECUTE),
    ("p_reply", "廣告平台怎麼回覆?", D, PLATFORM),
    ("h_approve", "人工確認:同意這次加預算嗎?", D, HUMAN),
    ("h_replay", "人工決定:要不要重新送入?", D, HUMAN),
    ("h_replay_refused", "不重新送入,結束", T, HUMAN),
    ("h_resolve", "人工查明:到底有沒有寫進去?", D, HUMAN),
)


@dataclass(frozen=True, slots=True)
class BackTransition:
    """一條往回走的轉換展開成的節點:`node` 沒有出去的邊,標籤寫明回到 `returns_to` 那一步。
    `transition` 是它在程式狀態轉換表裡對應的那一條,`lifecycle` 是執行端記下它的那種生命週期事件
    (放回待處理的四種);兩者都沒有的(例如開新工作)就是空。"""

    node: str
    what: str
    lane: str
    returns_to: str
    transition: tuple[StrEnum, StrEnum] | None = None
    lifecycle: LifecycleKind | None = None


BACK_TRANSITIONS = (
    BackTransition("a_recollect", "資料太舊,重新蒐集", ANALYZE, "a_collect",
                   (TaskState.ANALYZING, TaskState.COLLECTING_EVIDENCE)),
    BackTransition("a_restale", "送出時資料已經過時,重新蒐集", ANALYZE, "a_collect",
                   (TaskState.PROPOSED, TaskState.COLLECTING_EVIDENCE)),
    BackTransition("a_followup", "開一件新工作,照廣告現在的樣子重新分析", ANALYZE, "a_receive"),
    BackTransition("x_deferred", "這一輪先不處理,放回排隊", EXECUTE, "x_pending",
                   lifecycle=LifecycleKind.LEASE_RELEASED),
    BackTransition("x_lease_lost", "處理權被別人接手,這一輪放手", EXECUTE, "x_pending"),
    BackTransition("x_reclaimed", "處理的人中途沒回報,換人重新拿起", EXECUTE, "x_pick",
                   lifecycle=LifecycleKind.RECLAIMED),
    BackTransition("x_approved", "人已同意,放回排隊", EXECUTE, "x_pending",
                   lifecycle=LifecycleKind.APPROVAL_RELEASED),
    BackTransition("r_requeued", "同一份建議重新放回排隊", HUMAN, "x_pending",
                   lifecycle=LifecycleKind.REPLAY_REQUEUED),
    BackTransition("x_resend", "用同一個編號再送一次", EXECUTE, "x_write",
                   (AttemptState.UNKNOWN, AttemptState.IN_FLIGHT)),
    BackTransition("x_recheck", "這次查不出結論,稍後再查", EXECUTE, "x_unknown"),
)

_EDGES: tuple[tuple[str, str, str], ...] = (
    ("a_receive", "a_collect", "開始"),
    ("a_collect", "a_fresh", "拿到資料"),
    ("a_fresh", "a_recollect", "太舊"),
    ("a_fresh", "a_complete", "夠新"),
    ("a_complete", "a_no_action", "缺資料"),
    ("a_complete", "a_pacing", "齊全"),
    ("a_pacing", "a_no_action", "不慢或算不出來"),
    ("a_pacing", "a_route", "偏慢"),
    ("a_route", "a_rule", "沒有候選或不在允許範圍"),
    ("a_route", "a_candidate", "交給模型候選"),
    ("a_candidate", "a_worth", "候選給出答案"),
    ("a_candidate", "a_rule", "候選出狀況,改用程式規則"),
    ("a_rule", "a_worth", "規則的結果"),
    ("a_worth", "a_no_action", "不值得或資料不夠判斷"),
    ("a_worth", "a_propose", "值得"),
    ("a_worth", "a_failed", "分析出錯"),
    ("a_propose", "a_narrate", "附上模型說明"),
    ("a_propose", "a_submit", "直接送出"),
    ("a_narrate", "a_submit", "送出"),
    ("a_submit", "i_check", "送到收件"),
    ("i_check", "x_pending", "收下"),
    ("i_check", "a_restale", "資料已過時"),
    ("i_check", "a_failed", "拒收(內容有衝突或格式不對)"),
    ("i_check", "i_superseded", "已有更新的建議"),
    ("i_check", "x_expired", "送到時已過期"),
    ("x_pending", "x_pick", "輪到它"),
    ("x_pending", "a_followup", "紀錄已清掉、平台也查不到"),
    ("x_pick", "x_deadletter", "試太多次了"),
    ("x_pick", "x_precheck", "還能處理"),
    ("x_pick", "x_existing", "同一筆已經有人在寫"),
    ("x_pick", "x_lease_lost", "處理權被接手"),
    ("x_deadletter", "h_replay", "等人決定"),
    ("h_replay", "r_requeued", "重新送入"),
    ("h_replay", "h_replay_refused", "不送入"),
    ("x_precheck", "x_blocked", "沒過"),
    ("x_precheck", "x_expired", "已過期"),
    ("x_precheck", "x_deferred", "這一輪先不處理"),
    ("x_precheck", "x_guard", "通過"),
    ("x_guard", "x_blocked", "超出允許範圍"),
    ("x_guard", "x_wait_approval", "一次加太多,要人確認"),
    ("x_guard", "x_total", "在範圍內"),
    ("x_total", "x_blocked", "總上限已用完"),
    ("x_total", "x_wait_approval", "會超過總上限,要人確認"),
    ("x_total", "x_write", "不會超過"),
    ("x_wait_approval", "h_approve", "等人確認"),
    ("h_approve", "x_approved", "同意"),
    ("h_approve", "x_blocked", "放太久沒人確認"),
    ("h_approve", "i_superseded", "等的時候有了更新的建議"),
    ("x_write", "p_reply", "送出"),
    ("x_write", "x_reclaimed", "處理到一半中斷"),
    ("p_reply", "x_verify", "說收到了"),
    ("p_reply", "x_failed", "拒絕"),
    ("p_reply", "x_unknown", "沒有明確回覆(逾時或斷線)"),
    ("p_reply", "x_escalated", "請求本身有問題"),
    ("x_unknown", "x_verify", "查到已經寫進去"),
    ("x_unknown", "x_resend", "確定沒寫進去"),
    ("x_unknown", "x_failed", "已作廢,確定不會發生"),
    ("x_unknown", "x_recheck", "這次查不出來"),
    ("x_unknown", "x_escalated", "查不出也證明不了"),
    ("x_verify", "x_done", "一致"),
    ("x_verify", "x_escalated", "不一致或一直查不到"),
    ("x_escalated", "h_resolve", "等人查明"),
    ("h_resolve", "x_done", "有寫進去"),
    ("h_resolve", "x_failed", "沒寫進去"),
    ("x_failed", "a_failed", "記成失敗"),
    ("x_blocked", "a_followup", "廣告或規則變了、建議放太久"),
    ("x_blocked", "a_blocked_end", "其他原因"),
    ("x_expired", "a_followup", "照現況重新分析"),
)


def _graph() -> FlowGraph:
    labels = {node_id: label for node_id, label, _, _ in _NODES}
    back_nodes = tuple(
        FlowNode(b.node, f"{b.what}(回到「{labels[b.returns_to]}」)", S, b.lane)
        for b in BACK_TRANSITIONS)
    nodes = tuple(FlowNode(*row) for row in _NODES) + back_nodes
    return FlowGraph(nodes, tuple(FlowEdge(*row) for row in _EDGES))


FLOW_GRAPH = _graph()


@dataclass(frozen=True, slots=True)
class Place:
    """一個列舉成員在圖上的位置(節點或邊,二擇一)與它的白話顯示字。"""

    text: str
    node: str | None = None
    edge: tuple[str, str] | None = None


def _at(node: str, text: str) -> Place:
    return Place(text, node=node)


def _on(source: str, target: str, text: str) -> Place:
    return Place(text, edge=(source, target))


MAPPED_ENUMS: tuple[type[StrEnum], ...] = (
    Disposition, BlockCode, DeadLetterReason, StopKind, LifecycleKind, ReplayOutcome,
    AttemptState, OutcomeCode, VoidOutcome, Result, TaskState, ReplanReason, RoutePath,
    NoActionReason, AwaitingOutcome, LastFailure, Freshness, WorthVerdict,
)

_PLACES: dict[type[StrEnum], dict[str, Place]] = {
    Disposition: {
        "IN_PROGRESS": _at("x_pick", "執行端已拿起,正在處理"),
        "HANDED_OFF": _at("x_done", "這把鍵的寫入已經確認完成"),
        "BLOCKED": _at("x_blocked", "擋下,不寫入"),
        "DEAD_LETTER": _at("x_deadletter", "試太多次都沒能開始,先停下等人處理"),
        "AWAITING_APPROVAL": _at("x_wait_approval", "等人確認"),
    },
    BlockCode: {
        "CAMPAIGN_NOT_FOUND": _on("x_precheck", "x_blocked", "找不到這個廣告"),
        "CAMPAIGN_NOT_ACTIVE": _on("x_precheck", "x_blocked", "廣告已經沒在投放"),
        "VERSION_CHANGED": _on("x_precheck", "x_blocked", "建議寫好之後,廣告被別人改過"),
        "CAMPAIGN_NOT_ALLOWED": _on("x_guard", "x_blocked", "這個廣告不在允許調整的範圍"),
        "OVER_BUDGET_CAP": _on("x_guard", "x_blocked", "調完會超過這個廣告的預算上限"),
        "BUDGET_INCREASE_TOO_LARGE": _on("x_guard", "x_wait_approval", "一次加太多,要人確認"),
        "OPERATION_PREVIOUSLY_FAILED": _on("x_precheck", "x_blocked", "同一筆之前已經確定失敗"),
        "AGGREGATE_LIMIT_REACHED": _on("x_total", "x_wait_approval",
                                       "全部加起來會超過總上限,要人確認"),
        "POLICY_VERSION_CHANGED": _on("x_precheck", "x_blocked", "建議寫好之後,規則改了"),
        "DECISION_STALE": _on("x_precheck", "x_blocked", "建議放太久,已經不算數"),
    },
    DeadLetterReason: {
        "DELIVERY_LIMIT": _on("x_pick", "x_deadletter", "送了太多次都沒能開始處理"),
    },
    StopKind: {
        "AGGREGATE_LIMIT_REACHED": _on("x_total", "x_blocked", "總上限已經用完,擋下"),
        "TABLE_FULL": _on("x_precheck", "x_deferred", "待處理的太多,這一輪先放回排隊"),
        "BUDGET_INCREASE_TOO_LARGE": _on("x_guard", "x_wait_approval", "一次加太多,停下等人確認"),
    },
    LifecycleKind: {
        "RECEIVED": _on("i_check", "x_pending", "收件收下"),
        "SUPERSEDED": _at("i_superseded", "已經有更新的建議,舊的不處理"),
        "EXPIRED": _at("x_expired", "建議已經過期"),
        "DELIVERED": _on("x_pending", "x_pick", "輪到它,被拿起"),
        "RECLAIMED": _at("x_reclaimed", "前一個處理的人沒回報,換人接手"),
        "LEASE_RELEASED": _at("x_deferred", "這一輪沒能開始,放回排隊"),
        "HANDED_OFF": _at("x_done", "寫入已經確認完成,這份建議處理完了"),
        "BLOCKED": _at("x_blocked", "擋下"),
        "DEAD_LETTERED": _at("x_deadletter", "停下等人處理"),
        "AWAITING_APPROVAL": _at("x_wait_approval", "開始等人確認"),
        "APPROVAL_RELEASED": _at("x_approved", "人已同意,放回排隊"),
        "REPLAY_REQUEUED": _at("r_requeued", "人工重新送入,放回排隊"),
    },
    ReplayOutcome: {
        "REQUEUED": _on("h_replay", "r_requeued", "重新放回排隊"),
        "NOT_IN_INBOX": _on("h_replay", "h_replay_refused", "收件紀錄已經不在了"),
        "NOT_DEAD_LETTER": _on("h_replay", "h_replay_refused", "它不是停下等人處理的那種"),
        "EXPIRED": _on("h_replay", "h_replay_refused", "建議已經過期"),
        "SUPERSEDED": _on("h_replay", "h_replay_refused", "已經有更新的建議"),
        "UNREADABLE": _on("h_replay", "h_replay_refused", "存下來的內容讀不回來"),
        "INBOX_FULL": _on("h_replay", "h_replay_refused", "排隊已滿"),
    },
    AttemptState: {
        "IN_FLIGHT": _at("x_write", "正在送去平台"),
        "UNKNOWN": _at("x_unknown", "不知道平台有沒有寫進去"),
        "COMMITTED_UNVERIFIED": _at("x_verify", "平台說收到了,還沒比對實際狀態"),
        "VERIFIED": _at("x_done", "比對過,平台上確實改好了"),
        "FAILED": _at("x_failed", "確定沒改到"),
        "ESCALATED": _at("x_escalated", "交給人判斷,廣告先鎖住"),
    },
    OutcomeCode: {
        "VERSION_CONFLICT": _on("p_reply", "x_failed", "平台說廣告版本不對"),
        "VALIDATION_REJECTED": _on("p_reply", "x_failed", "平台說內容不合格"),
        "CAMPAIGN_NOT_FOUND": _on("p_reply", "x_failed", "平台找不到這個廣告"),
        "OTHER_REJECTION": _on("p_reply", "x_failed", "平台以其他原因拒絕"),
        "NOT_HAPPENED": _on("x_unknown", "x_failed", "確定這筆不會發生"),
        "MANUAL_FAILURE": _on("h_resolve", "x_failed", "人查明沒寫進去"),
        "IDEMPOTENCY_CONFLICT": _on("p_reply", "x_escalated", "同一個編號送了不同內容"),
        "VERIFICATION_TIMEOUTS_EXHAUSTED": _on("x_verify", "x_escalated", "一直查不到結論"),
        "SEND_LIMIT_REACHED": _on("x_unknown", "x_escalated", "重送次數用完"),
        "VERIFICATION_MISMATCH": _on("x_verify", "x_escalated", "平台上的預算跟建議不一樣"),
        "CANNOT_PROVE_NOT_HAPPENED": _on("x_unknown", "x_escalated", "證明不了這筆沒發生"),
        "CAPABILITY_REJECTED": _on("p_reply", "x_escalated", "平台不收這次的寫入許可"),
        "LOCAL_REQUEST_ERROR": _on("p_reply", "x_escalated", "我們自己送出的請求有錯"),
    },
    VoidOutcome: {
        "FAILED": _on("x_unknown", "x_failed", "作廢成功,確定沒寫進去"),
        "FOUND": _on("x_unknown", "x_verify", "查到已經寫進去"),
        "TIMEOUT": _on("x_unknown", "x_recheck", "這次查不出結論,稍後再查"),
        "ESCALATED": _on("x_unknown", "x_escalated", "查不出來,交給人"),
    },
    Result: {
        "IDLE": _at("x_pending", "沒有要處理的建議"),
        "DEFERRED": _at("x_deferred", "這一輪什麼都沒寫,先放回排隊"),
        "EXPIRED": _at("x_expired", "建議已經過期"),
        "BLOCKED": _at("x_blocked", "擋下,不寫入"),
        "HANDED_OFF_TO_EXISTING": _at("x_existing", "同一筆已經有人在寫,交給那一筆"),
        "EXECUTED": _on("x_total", "x_write", "檢查都通過,送去寫入"),
        "AWAITING_APPROVAL": _at("x_wait_approval", "停下等人確認"),
        "LEASE_LOST": _at("x_lease_lost", "處理權被別人接手,這一輪放手"),
    },
    TaskState: {
        "RECEIVED": _at("a_receive", "收到工作"),
        "COLLECTING_EVIDENCE": _at("a_collect", "正在蒐集資料"),
        "ANALYZING": _at("a_fresh", "正在分析資料"),
        "PROPOSED": _at("a_propose", "建議寫好了"),
        "HANDED_OFF": _on("i_check", "x_pending", "執行端已收下"),
        "COMPLETED": _at("x_done", "完成"),
        "FAILED": _at("a_failed", "出錯,結束"),
        "BLOCKED": _at("a_blocked_end", "被擋下,結束"),
        "NO_ACTION": _at("a_no_action", "不調整,結束"),
        "SUPERSEDED": _at("i_superseded", "被更新的建議取代"),
    },
    ReplanReason: {
        "VERSION_CHANGED": _on("x_blocked", "a_followup", "廣告被別人改過,照現況重新分析"),
        "EXPIRED": _on("x_expired", "a_followup", "建議過期,重新分析"),
        "AFTER_RETENTION": _on("x_pending", "a_followup",
                               "收件紀錄已清掉、平台也查不到這筆寫入,重新分析"),
        "POLICY_VERSION_CHANGED": _on("x_blocked", "a_followup", "規則改了,照新規則重新分析"),
        "DECISION_STALE": _on("x_blocked", "a_followup", "建議放太久,重新分析"),
    },
    RoutePath: {
        "CODE_RULE": _on("a_route", "a_rule", "用程式規則判斷"),
        "CANDIDATE": _on("a_candidate", "a_worth", "模型候選給出答案"),
        "FALLBACK_EXCEPTION": _on("a_candidate", "a_rule", "模型候選出錯,改用程式規則"),
        "FALLBACK_TIMEOUT": _on("a_candidate", "a_rule", "模型候選太慢,改用程式規則"),
        "FALLBACK_INVALID": _on("a_candidate", "a_rule", "模型候選的答案看不懂,改用程式規則"),
        "FALLBACK_UNSURE": _on("a_candidate", "a_rule", "模型候選沒把握,改用程式規則"),
    },
    NoActionReason: {
        "STALE_EVIDENCE": _on("a_fresh", "a_recollect", "資料太舊,要重新蒐集"),
        "MISSING_STATE_OR_METRICS": _on("a_complete", "a_no_action", "缺廣告狀態或成效資料"),
        "PACING_UNKNOWN": _on("a_pacing", "a_no_action", "算不出預算花得快還是慢"),
        "NOT_UNDERPACING": _on("a_pacing", "a_no_action", "預算沒有花得比預期慢"),
        "JUDGED_NOT_WORTH": _on("a_worth", "a_no_action", "判斷不值得加"),
        "JUDGED_INSUFFICIENT": _on("a_worth", "a_no_action", "資料不夠判斷"),
    },
}

_PLACES.update({
    AwaitingOutcome: {
        "EXPIRED": _on("h_approve", "x_blocked", "放太久沒人確認,擋下"),
        "SUPERSEDED": _on("h_approve", "i_superseded", "等的時候有了更新的建議"),
        "RELEASED": _on("h_approve", "x_approved", "人已同意,放回排隊"),
    },
    LastFailure: {
        "DSP_UNAVAILABLE": _on("x_precheck", "x_deferred", "讀不到廣告平台的現況,這一輪先放回"),
        "TABLE_FULL": _on("x_precheck", "x_deferred", "待處理的太多,這一輪先放回"),
        "NO_REPORT": _at("x_reclaimed", "處理的人中途沒回報"),
    },
    Freshness: {
        "FRESH": _on("a_fresh", "a_complete", "資料夠新"),
        "EXPIRED": _on("a_fresh", "a_recollect", "資料超過有效時間"),
        "VERSION_CHANGED": _on("a_fresh", "a_recollect", "讀到資料之後廣告又被改過"),
        "UNVERIFIED": _on("a_fresh", "a_recollect", "沒讀到廣告現在的版本,不能算新"),
    },
    WorthVerdict: {
        "WORTH": _on("a_worth", "a_propose", "值得加"),
        "NOT_WORTH": _on("a_worth", "a_no_action", "不值得加"),
        "INSUFFICIENT": _on("a_worth", "a_no_action", "資料不夠判斷"),
        "UNSURE": _on("a_candidate", "a_rule", "模型候選沒把握,改用程式規則"),
    },
})

OUTCOMES: dict[tuple[type[StrEnum], str], Place] = {
    (enum, name): place for enum, places in _PLACES.items() for name, place in places.items()}

# 每個判斷點綁一個列舉(設計審 r2 n9):除了列在後面的例外分支,每條分支都有那個列舉的成員對到這條邊或
# 它通往的節點。例外分支是「檢查通過、往下走」或由別的紀錄決定的那一條,旁邊寫明。
DECISION_ENUMS: dict[str, tuple[type[StrEnum], tuple[str, ...]]] = {
    "a_fresh": (Freshness, ()),
    "a_complete": (NoActionReason, ("a_pacing",)),  # 齊全:往下走
    "a_pacing": (NoActionReason, ("a_route",)),  # 偏慢:往下走
    "a_route": (RoutePath, ("a_candidate",)),  # 交給候選:路由結果在候選那一步才定
    "a_candidate": (RoutePath, ()),
    "a_worth": (WorthVerdict, ("a_failed",)),  # 分析出錯:任務狀態記成失敗
    "i_check": (LifecycleKind, ("a_restale", "a_failed")),  # 送件時過時與拒收:任務狀態記
    "x_pick": (Result, ("x_deadletter", "x_precheck")),  # 試太多次:死信原因記;還能處理:往下走
    "x_precheck": (Result, ("x_guard",)),  # 通過:往下走
    "x_guard": (Result, ("x_total",)),  # 在範圍內:往下走
    "x_total": (Result, ()),
    "p_reply": (AttemptState, ()),
    "x_unknown": (VoidOutcome, ("x_resend",)),  # 確定沒寫進去:嘗試狀態從不明回到送出中
    "x_verify": (AttemptState, ()),
    "h_approve": (AwaitingOutcome, ()),
    "h_replay": (ReplayOutcome, ()),
    "h_resolve": (AttemptState, ()),
}
