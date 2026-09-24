"""Phase 12 正式流程圖定義:有向無環、每個系統結果都對得到圖、回頭轉換展開、白話顯示字。"""

from enum import StrEnum

import pytest

from rtb.demo import flow
from rtb.demo.state import FlowGraph, NodeKind
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


def test_the_flow_graph_is_a_well_formed_dag():
    assert len(NODES) == len(GRAPH.nodes), "節點編號重複"
    assert len(EDGES) == len(GRAPH.edges), "同一對節點之間的邊重複(判斷紀錄用起點終點認邊)"
    assert all(s in NODES and t in NODES for s, t in EDGES), "邊指到不存在的節點"
    assert len(_topological_order(GRAPH)) == len(NODES), "流程圖有環"


def test_every_node_is_reachable_from_the_start_and_every_end_is_a_dead_end():
    reached, pending = set(), [flow.START]
    while pending:
        node = pending.pop()
        if node not in reached:
            reached.add(node)
            pending += [t for s, t in EDGES if s == node]
    assert reached == set(NODES)
    loop_nodes = {back.node for back in flow.BACK_TRANSITIONS}
    for node in GRAPH.nodes:
        leaves = [t for s, t in EDGES if s == node.id]
        if node.kind is NodeKind.TERMINAL or node.id in loop_nodes:
            assert not leaves, f"{node.id} 是結束點或回頭節點,不該再有出去的邊"
        else:
            assert leaves, f"{node.id} 走不下去"
        if node.kind is NodeKind.DECISION:
            assert len(leaves) >= 2, f"判斷點 {node.id} 至少要兩條分支"


def test_the_graph_is_the_type_the_page_draws():
    assert isinstance(GRAPH, FlowGraph)
    assert {node.lane for node in GRAPH.nodes} <= set(flow.LANES)


@pytest.mark.parametrize("enum", flow.MAPPED_ENUMS, ids=lambda e: e.__name__)
def test_every_system_outcome_maps_onto_the_flow_graph(enum: type[StrEnum]):
    """[S1024] 十二個列舉的每個成員(以列舉類別加成員名為鍵)都對到一條邊或一個節點。"""
    for member in enum:
        place = flow.OUTCOMES.get((enum, member.name))
        assert place is not None, f"{enum.__name__}.{member.name} 沒有對到流程圖"
        if place.edge is not None:
            assert place.edge in EDGES, f"{enum.__name__}.{member.name} 對到不存在的邊"
        else:
            assert place.node in NODES, f"{enum.__name__}.{member.name} 對到不存在的節點"


def test_the_mapped_enums_are_the_eighteen_in_the_plan():
    """計劃第 4 版的十八個:第 3 版的十二個,加設計審 r2 補的處理待確認的結果、最後失敗原因、
    證據新鮮度、值不值得加的判定、沒提案原因、路由路徑。"""
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
    mapped = {key[0] for key in flow.OUTCOMES}
    assert mapped == set(flow.MAPPED_ENUMS), "對應表裡有清單外的列舉"


def test_same_named_members_of_different_enums_are_kept_apart():
    """[f2] 鍵是(列舉類別, 成員名):擋下原因與停下種類都有總曝險已滿,不會互相蓋掉。"""
    name = "AGGREGATE_LIMIT_REACHED"
    assert (BlockCode, name) in flow.OUTCOMES
    assert (StopKind, name) in flow.OUTCOMES
    assert flow.OUTCOMES[(BlockCode, name)] is not flow.OUTCOMES[(StopKind, name)]


def _backward(transitions, enum):
    order = list(enum)
    return {(a, b) for a, targets in transitions.items() for b in targets
            if order.index(b) < order.index(a)}


@pytest.mark.parametrize(("transitions", "enum"), [
    (TASK_TRANSITIONS, TaskState), (ATTEMPT_TRANSITIONS, AttemptState)])
def test_every_known_back_transition_is_unrolled(transitions, enum):
    """[S1025] 程式狀態轉換表裡每一條往回走的轉換,都列在回頭轉換清單並展開成新節點。"""
    listed = {back.transition for back in flow.BACK_TRANSITIONS if back.transition}
    for source, target in _backward(transitions, enum):
        assert (source, target) in listed, f"{enum.__name__} {source}→{target} 沒列進回頭轉換清單"


# 執行端代表「放回待處理」的生命週期事件種類(設計審 r2 n8):確認後放回、重放放回、被接手、放掉處理權
PUT_BACK_KINDS = (LifecycleKind.APPROVAL_RELEASED, LifecycleKind.REPLAY_REQUEUED,
                  LifecycleKind.RECLAIMED, LifecycleKind.LEASE_RELEASED)


@pytest.mark.parametrize("kind", PUT_BACK_KINDS, ids=lambda k: k.name)
def test_every_put_back_event_is_an_unrolled_back_node(kind):
    """[S1025] 執行端放回待處理的事件種類,各自是一個展開的回頭節點,對應表也指到那個節點。"""
    backs = {back.lifecycle: back.node for back in flow.BACK_TRANSITIONS if back.lifecycle}
    assert kind in backs, f"{kind.name} 沒列進回頭轉換清單"
    assert flow.OUTCOMES[(LifecycleKind, kind.name)].node == backs[kind]


def test_every_back_transition_is_a_new_node_that_names_where_it_returns():
    for back in flow.BACK_TRANSITIONS:
        assert back.node in NODES and back.returns_to in NODES
        assert back.node != back.returns_to
        assert f"「{NODES[back.returns_to].label}」" in NODES[back.node].label


@pytest.mark.parametrize("enum", flow.MAPPED_ENUMS, ids=lambda e: e.__name__)
def test_every_disposition_has_a_plain_explanation(enum: type[StrEnum]):
    """[S1026] 對應清單上每個列舉的每個成員都有一句白話說明。"""
    for member in enum:
        place = flow.OUTCOMES.get((enum, member.name))
        assert place is not None and place.text.strip(), f"{enum.__name__}.{member.name} 沒有白話"
        assert member.value not in place.text, "白話說明不該直接用程式代碼"


def _display_texts():
    yield from (node.label for node in GRAPH.nodes)
    yield from (edge.label for edge in GRAPH.edges)
    yield from flow.LANES
    yield from (place.text for place in flow.OUTCOMES.values())


def test_display_text_explains_every_banned_term():
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
def test_the_banned_term_check_needs_a_bracket_right_after(text, expected):
    assert flow.unexplained_terms(text) == expected


def test_the_banned_terms_cover_the_ruling():
    assert set(flow.BANNED_TERMS) >= {"死信", "猝死", "總曝險", "冪等", "租約", "對帳", "護欄",
                                      "接續任務", "修訂", "結果不明"}


def _covered(node: str, target: str, enum: type[StrEnum]) -> bool:
    return any(place.edge == (node, target) or place.node == target
               for (e, _), place in flow.OUTCOMES.items() if e is enum)


def test_every_decision_node_is_bound_to_an_enum():
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
