"""Phase 12 正式流程圖定義:有向無環、每個系統結果都對得到圖、回頭轉換展開、白話顯示字。"""

from collections.abc import Iterator, Mapping
from enum import StrEnum

import pytest

from rtb.demo import flow
from rtb.demo.state import FlowGraph, NodeKind, NodeOwner
from rtb.domain.attempt import TRANSITIONS as ATTEMPT_TRANSITIONS
from rtb.domain.attempt import AttemptState
from rtb.domain.task_state import TRANSITIONS as TASK_TRANSITIONS
from rtb.domain.task_state import TaskState
from rtb.executor.inbox_store import BlockCode, LifecycleKind, StopKind

GRAPH = flow.FLOW_GRAPH
NODES = {node.id: node for node in GRAPH.nodes}
EDGES = {(edge.source, edge.target): edge for edge in GRAPH.edges}


def _topological_order(graph: FlowGraph) -> list[str]:
    incoming = {node.id: 0 for node in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target] += 1
    ready = [node for node, count in incoming.items() if count == 0]
    order = []
    while ready:
        node = ready.pop()
        order.append(node)
        for edge in graph.edges:
            if edge.source == node:
                incoming[edge.target] -= 1
                if incoming[edge.target] == 0:
                    ready.append(edge.target)
    return order


def test_the_flow_graph_is_a_well_formed_dag() -> None:
    assert len(NODES) == len(GRAPH.nodes), "節點編號重複"
    assert len(EDGES) == len(GRAPH.edges), "同一對節點之間的邊重複(判斷紀錄用起點終點認邊)"
    assert all(s in NODES and t in NODES for s, t in EDGES), "邊指到不存在的節點"
    assert len(_topological_order(GRAPH)) == len(NODES), "流程圖有環"


def test_every_node_is_reachable_from_the_start_and_every_end_is_a_dead_end() -> None:
    reached, pending = set(), [flow.START]
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node)
            pending += [t for s, t in EDGES if s == node]
    assert reached == set(NODES)
    loop_nodes = {back.node for back in flow.BACK_TRANSITIONS}
    for graph_node in GRAPH.nodes:
        leaves = [t for s, t in EDGES if s == graph_node.id]
        if graph_node.kind is NodeKind.TERMINAL or graph_node.id in loop_nodes:
            assert not leaves, f"{graph_node.id} 是結束點或回頭節點,不該再有出去的邊"
        else:
            assert leaves, f"{graph_node.id} 走不下去"
        if graph_node.kind is NodeKind.DECISION:
            assert len(leaves) >= 2, f"判斷點 {graph_node.id} 至少要兩條分支"


def test_the_graph_is_the_type_the_page_draws() -> None:
    assert isinstance(GRAPH, FlowGraph)
    assert {node.lane for node in GRAPH.nodes} <= set(flow.LANES)


def test_every_node_has_one_handler_and_ai_is_limited_to_reference_steps() -> None:
    assert all(isinstance(node.owner, NodeOwner) for node in GRAPH.nodes)
    assert {node.id for node in GRAPH.nodes if node.owner is NodeOwner.AI} == flow.AI_NODES
    assert {node.id for node in GRAPH.nodes if node.owner is NodeOwner.EXTERNAL} == {"p_reply"}
    assert all(node.owner is NodeOwner.HUMAN for node in GRAPH.nodes if node.lane == flow.HUMAN)


@pytest.mark.parametrize("enum", flow.MAPPED_ENUMS, ids=lambda e: e.__name__)
def test_every_system_outcome_maps_onto_the_flow_graph(enum: type[StrEnum]) -> None:
    """[S1024] 十二個列舉的每個成員(以列舉類別加成員名為鍵)都對到一條邊或一個節點。"""
    for member in enum:
        place = flow.OUTCOMES.get((enum, member.name))
        assert place is not None, f"{enum.__name__}.{member.name} 沒有對到流程圖"
        if place.edge is not None:
            assert place.edge in EDGES, f"{enum.__name__}.{member.name} 對到不存在的邊"
        else:
            assert place.node in NODES, f"{enum.__name__}.{member.name} 對到不存在的節點"


def test_the_mapped_enums_are_the_eighteen_in_the_plan() -> None:
    """計劃第 4 版的十八個:第 3 版的十二個,加設計審 r2 補的處理待確認的結果、最後失敗原因、
    證據新鮮度、值不值得加的判定、沒提案原因、路由路徑。Phase 13 增量 2 照 Phase 13 計劃改寫
     [S1024]:
    再加 AI 決策退回程式規則的原因,共十九個。Phase 14 增量 3 撤除 AI 決策那一步,退回原因跟著拿掉,
    回到十八個。"""
    assert {f"{e.__module__}.{e.__name__}" for e in flow.MAPPED_ENUMS} == {
        "rtb.executor.inbox_store.Disposition", "rtb.executor.inbox_store.BlockCode",
        "rtb.executor.inbox_store.DeadLetterReason", "rtb.executor.inbox_store.StopKind",
        "rtb.executor.inbox_store.LifecycleKind", "rtb.executor.inbox_store.ReplayOutcome",
        "rtb.domain.attempt.AttemptState", "rtb.domain.attempt.OutcomeCode",
        "rtb.executor.execution.VoidOutcome", "rtb.executor.execution.Result",
        "rtb.domain.task_state.TaskState", "rtb.analyzer.task_store.ReplanReason",
        "rtb.analyzer.policy.RoutePath", "rtb.analyzer.policy.NoActionReason",
        "rtb.executor.inbox_store.AwaitingOutcome", "rtb.executor.inbox_store.LastFailure",
        "rtb.domain.evidence.Freshness", "rtb.domain.worth.WorthVerdict",
    }
    assert len(flow.MAPPED_ENUMS) == 18
    mapped = {key[0] for key in flow.OUTCOMES}
    assert mapped == set(flow.MAPPED_ENUMS), "對應表裡有清單外的列舉"


def test_same_named_members_of_different_enums_are_kept_apart() -> None:
    """[f2] 鍵是(列舉類別, 成員名):擋下原因與停下種類都有總曝險已滿,不會互相蓋掉。"""
    name = "AGGREGATE_LIMIT_REACHED"
    assert (BlockCode, name) in flow.OUTCOMES
    assert (StopKind, name) in flow.OUTCOMES
    assert flow.OUTCOMES[(BlockCode, name)] is not flow.OUTCOMES[(StopKind, name)]


def _backward(
    transitions: Mapping[StrEnum, frozenset[StrEnum]], enum: type[StrEnum],
) -> set[tuple[StrEnum, StrEnum]]:
    order = list(enum)
    return {(a, b) for a, targets in transitions.items() for b in targets
            if order.index(b) < order.index(a)}


@pytest.mark.parametrize(("transitions", "enum"), [
    (TASK_TRANSITIONS, TaskState), (ATTEMPT_TRANSITIONS, AttemptState)])
def test_every_known_back_transition_is_unrolled(
    transitions: Mapping[StrEnum, frozenset[StrEnum]], enum: type[StrEnum],
) -> None:
    """[S1025] 程式狀態轉換表裡每一條往回走的轉換,都列在回頭轉換清單並展開成新節點。"""
    listed = {back.transition for back in flow.BACK_TRANSITIONS if back.transition}
    for source, target in _backward(transitions, enum):
        assert (source, target) in listed, f"{enum.__name__} {source}→{target} 沒列進回頭轉換清單"


# 執行端代表「放回待處理」的生命週期事件種類(設計審 r2 n8):確認後放回、重放放回、被接手、放掉處理權
PUT_BACK_KINDS = (LifecycleKind.APPROVAL_RELEASED, LifecycleKind.REPLAY_REQUEUED,
                  LifecycleKind.RECLAIMED, LifecycleKind.LEASE_RELEASED)


@pytest.mark.parametrize("kind", PUT_BACK_KINDS, ids=lambda k: k.name)
def test_every_put_back_event_is_an_unrolled_back_node(kind: LifecycleKind) -> None:
    """[S1025] 執行端放回待處理的事件種類,各自是一個展開的回頭節點,對應表也指到那個節點。"""
    backs = {back.lifecycle: back.node for back in flow.BACK_TRANSITIONS if back.lifecycle}
    assert kind in backs, f"{kind.name} 沒列進回頭轉換清單"
    assert flow.OUTCOMES[(LifecycleKind, kind.name)].node == backs[kind]


def test_every_back_transition_is_a_new_node_that_names_where_it_returns() -> None:
    for back in flow.BACK_TRANSITIONS:
        assert back.node in NODES and back.returns_to in NODES
        assert back.node != back.returns_to
        assert f"「{NODES[back.returns_to].label}」" in NODES[back.node].label


@pytest.mark.parametrize("enum", flow.MAPPED_ENUMS, ids=lambda e: e.__name__)
def test_every_disposition_has_a_plain_explanation(enum: type[StrEnum]) -> None:
    """[S1026] 對應清單上每個列舉的每個成員都有一句白話說明。"""
    for member in enum:
        place = flow.OUTCOMES.get((enum, member.name))
        assert place is not None and place.text.strip(), f"{enum.__name__}.{member.name} 沒有白話"
        assert member.value not in place.text, "白話說明不該直接用程式代碼"


def _display_texts() -> Iterator[str]:
    yield from (node.label for node in GRAPH.nodes)
    yield from (edge.label for edge in GRAPH.edges)
    yield from flow.LANES
    yield from (place.text for place in flow.OUTCOMES.values())


def test_display_text_explains_every_banned_term() -> None:
    """[S1042] 顯示文字出現禁用術語時,同一段緊接括號白話解釋。"""
    problems = [(text, term) for text in _display_texts()
                for term in flow.unexplained_terms(text)]
    assert not problems


@pytest.mark.parametrize(("text", "expected"), [
    ("回頭對帳", ["對帳"]),
    ("回頭對帳(去平台查這筆有沒有寫進去)", []),
    ("回頭對帳 (去平台查)", ["對帳"]),
    ("對帳()", ["對帳"]),
    ("死信與總曝險", ["死信", "總曝險"]),
    ("寫入成功", []),
    ("對帳\uff08去平台查\uff09", []),
])
def test_the_banned_term_check_needs_a_bracket_right_after(
    text: str, expected: list[str],
) -> None:
    assert flow.unexplained_terms(text) == expected


def test_the_banned_terms_cover_the_ruling() -> None:
    assert set(flow.BANNED_TERMS) >= {"死信", "猝死", "總曝險", "冪等", "租約", "對帳", "護欄",
                                      "接續任務", "修訂", "結果不明"}


def _covered(node: str, target: str, enum: type[StrEnum]) -> bool:
    return any(place.edge == (node, target) or place.node == target
               for (e, _), place in flow.OUTCOMES.items() if e is enum)


def test_every_decision_node_is_bound_to_an_enum() -> None:
    """[S1055] 每個判斷點綁一個對應清單上的列舉;除了明列的例外分支(通過、或由別的紀錄決定),
    每條分支都有那個列舉的成員對到這條邊或它通往的節點。"""
    decisions = {node.id for node in GRAPH.nodes if node.kind is NodeKind.DECISION}
    assert set(flow.DECISION_ENUMS) == decisions
    for node, (enum, unbound) in flow.DECISION_ENUMS.items():
        assert enum in flow.MAPPED_ENUMS
        targets = [t for s, t in EDGES if s == node]
        assert set(unbound) < set(targets), f"{node} 的例外分支不在它的分支裡,或全部都是例外"
        for target in targets:
            if target not in unbound:
                assert _covered(node, target, enum), f"{node}→{target} 沒有 {enum.__name__}"


def test_handed_off_means_different_things_on_the_two_sides():
    """分析端的「已交給執行」是收件口收下(排隊等執行);執行端的處置與生命週期「已交給執行」是這把鍵
    的寫入已確認(完成)。第一版把後者對到「寫入廣告平台」,真的跑 F1 時判斷紀錄順序才露出來。"""
    assert flow.OUTCOMES[(TaskState, "HANDED_OFF")].edge == ("i_check", "x_pending")
    assert flow.OUTCOMES[(LifecycleKind, "HANDED_OFF")].node == "x_done"
    from rtb.executor.inbox_store import Disposition

    assert flow.OUTCOMES[(Disposition, "HANDED_OFF")].node == "x_done"


# ---- 設計審之後的代碼審 r1 d2/d3:分析端每一種出口都要在圖上有對應的邊 ----
def _answer(state, block_code=None):
    from rtb.analyzer import flow as analyzer_flow
    from tests.analyzer.conftest import make_proposal

    proposal = make_proposal()
    from rtb.domain.proposal import content_hash

    return analyzer_flow.Accepted(replayed=True, task_id=proposal.task_id,
                                  revision=proposal.revision,
                                  content_hash=content_hash(proposal), state=state,
                                  block_code=block_code), proposal


# 收件口回應的每一種處置 → 分析端結果在圖上走的邊(手寫的對照表;下面的測試逐一窮舉程式的出口,
# 表上少一種就紅)
_INBOX_EXITS = {
    ("handed_off", None): ("x_verify", "x_done"),
    ("superseded", None): ("i_check", "i_superseded"),
    ("expired", None): ("x_expired", "a_followup"),
    ("blocked", "version_changed"): ("x_blocked", "a_followup"),
    ("blocked", "policy_version_changed"): ("x_blocked", "a_followup"),
    ("blocked", "decision_stale"): ("x_blocked", "a_followup"),
    ("blocked", "not_permitted"): ("x_blocked", "a_blocked_end"),
    ("blocked", "campaign_not_found"): ("x_blocked", "a_blocked_end"),
    ("blocked", "campaign_not_active"): ("x_blocked", "a_blocked_end"),
    ("blocked", "operation_previously_failed"): ("x_blocked", "a_blocked_end"),
    ("dead_letter", None): ("x_deadletter", "a_blocked_end"),  # 過期之後才結案
}


def test_every_analyzer_exit_after_hand_off_has_an_edge():
    """拿分析端「已交給執行之後」每一種出口(收件口的每種處置、收件紀錄已清掉後查平台、重送被拒)
    比對圖上的邊:程式會走的路圖上都要有。"""
    from datetime import timedelta

    from rtb.analyzer import flow as analyzer_flow

    exits = set()
    for state in sorted(analyzer_flow._KNOWN_STATES - analyzer_flow._OPEN_STATES):
        codes = sorted(analyzer_flow._BLOCK_CODES) if state == "blocked" else [None]
        for code in codes:
            answer, proposal = _answer(state, code)
            late = proposal.decision_expires_at + timedelta(seconds=1)
            if analyzer_flow._from_inbox_answer(answer, proposal, late) is not None:
                exits.add((state, code))
    assert exits == set(_INBOX_EXITS), "收件口處置跟對照表對不上"
    for edge in _INBOX_EXITS.values():
        assert edge in EDGES, f"程式會走 {edge},圖上沒有這條邊"
    # 收件紀錄已清掉:平台查不到 → 開新工作;查到同編號不同內容 → 擋下結束;重送被拒 → 擋下結束
    for edge in [("x_pending", "a_followup"), ("x_pending", "a_blocked_end"),
                 ("i_check", "a_blocked_end")]:
        assert edge in EDGES


def test_a_failed_write_ends_as_blocked_or_a_new_task_never_as_analysis_failure():
    """執行端寫入失敗,收件口確認的是擋下(版本衝突記成版本已變,其餘記成先前已失敗):分析端不會
    轉成「這件工作出錯」。"""
    assert ("x_failed", "a_failed") not in EDGES
    assert ("x_failed", "a_blocked_end") in EDGES
    assert ("x_failed", "a_followup") in EDGES


def test_a_proposal_expired_at_intake_is_recollected_in_the_same_task():
    """收件時已過期,收件口回 422,分析端在同一件工作裡重新蒐集(不是開新工作);排隊中被順手標成
    已過期的,從排隊那一步走到過期。"""
    assert ("i_check", "x_expired") not in EDGES
    assert ("i_check", "a_restale") in EDGES
    assert ("x_pending", "x_expired") in EDGES


# ---- 第 2 輪代碼審 n2:分析端出口跟圖上的邊雙向對照 ----
_TARGET_OF_STEP = {"COMPLETED": "x_done", "SUPERSEDED": "i_superseded"}
# 進入分析端出口節點、但不是收件口處置的邊:各自由哪一段程式走到
_OTHER_EXITS = {
    ("i_check", "a_restale"): "送件被收件口以過期拒收,同一件工作重新蒐集",
    ("i_check", "a_blocked_end"): "已交出去之後重送被拒收(永久拒絕)",
    ("x_pending", "a_followup"): "收件紀錄已清掉、平台也查不到:開新工作",
    ("x_pending", "a_blocked_end"): "收件紀錄已清掉、平台同編號卻是不同內容",
    ("x_failed", "a_followup"): "寫入被平台以版本不對拒絕,收件口確認成版本已變",
    ("x_failed", "a_blocked_end"): "寫入被平台拒絕,收件口確認成先前已失敗",
}


def test_each_inbox_answer_leads_where_the_table_says():
    """[n2] 正向:分析端對每一種收件口處置實際的下一步(開新工作、結案、完成、被取代),要跟對照表的
    終點一致;改成開新工作的處置(例如把權限不足改成重新規劃)在這裡翻紅。"""
    from datetime import timedelta

    from rtb.analyzer import flow as analyzer_flow

    for (state, code), (_, target) in _INBOX_EXITS.items():
        answer, proposal = _answer(state, code)
        step = analyzer_flow._from_inbox_answer(
            answer, proposal, proposal.decision_expires_at + timedelta(seconds=1))
        assert step is not None, (state, code)
        if step.follow_up is not None:
            actual = "a_followup"
        else:
            actual = _TARGET_OF_STEP.get(step.new_state.name, "a_blocked_end")
        assert actual == target, (state, code, step)


def test_every_edge_into_an_analyzer_exit_is_one_the_program_takes():
    """[n2] 反向:圖上每一條進入開新工作、擋下結束、重新蒐集的邊,都要是收件口處置對照表或已知的程式
    出口;圖上多畫一條程式不會走的邊在這裡翻紅。"""
    exits = {"a_followup", "a_blocked_end", "a_restale"}
    known = {e for e in _INBOX_EXITS.values() if e[1] in exits} | set(_OTHER_EXITS)
    into = {e for e in EDGES if e[1] in exits}
    assert into == known
