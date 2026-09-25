# ruff: noqa: RUF001, RUF002
"""展示頁的流程圖:把一個情境走過的路徑畫成無腳本 SVG(泳道、節點、走過的邊、跳過與回頭的線、
沒走的分支),以及頁面與流程圖共用的幾個小工具(文字跳脫、節點對照、判斷卡彙總)。

從頁面組裝那支檔拆出來(Phase 12 代碼審 r2 a1):頁面組裝只管把展示狀態排成 HTML,流程圖的排版
與 SVG 產生都在這裡。行為照舊,只搬家。
"""

import html
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from rtb.demo.state import (
    Decision,
    FlowEdge,
    FlowGraph,
    FlowNode,
    NodeKind,
    NodeOwner,
    Scenario,
)

_NODE_WIDTH: Final = 144


_AI_WIDTH: Final = 144


_GROUP_WIDTH: Final = 190


_NODE_HEIGHT: Final = 80


_STEP_SPACING: Final = 97


_LANE_HEIGHT: Final = 140


LANE_ORDER: Final = ("分析", "收件", "執行", "廣告平台", "人工")


_GROUP_ID: Final = "analysis_group"


_SECOND_GROUP_ID: Final = "analysis_after_ai"


_GROUP_IDS: Final = frozenset({_GROUP_ID, _SECOND_GROUP_ID})


SHORT_LABELS: Final[dict[str, str]] = {
    "a_receive": "收到工作", "a_collect": "蒐集資料", "a_fresh": "資料夠新？",
    "a_complete": "資料齊全？", "a_pacing": "花費偏慢？", "a_route": "選擇判法",
    "a_candidate": "候選判斷", "a_rule": "規則判斷", "a_worth": "值得加？",
    "a_no_action": "不調整", "a_failed": "工作失敗", "a_propose": "寫好建議",
    "a_narrate": "撰寫說明", "a_submit": "送出建議",
    "i_check": "收件檢查", "i_superseded": "舊建議停下", "x_pending": "等待執行",
    "x_pick": "領取建議", "x_precheck": "寫入前檢查", "x_guard": "權限與加幅",
    "x_total": "帳戶總上限", "x_write": "送去寫入", "p_reply": "平台回覆",
    "x_unknown": "查明結果", "x_verify": "核對預算", "x_done": "完成",
    "x_reclaimed": "換人接手", "x_blocked": "擋下寫入", "a_followup": "開新工作",
    "x_deadletter": "停下等人", "h_replay": "人工決定", "r_requeued": "重新排隊",
    "a_blocked_end": "工作結束", "x_wait_approval": "等待確認",
    "h_approve": "人工確認", "x_approved": "重新排隊", "x_deferred": "放回排隊",
    "x_lease_lost": "放回排隊", "x_expired": "建議過期", "a_recollect": "重新蒐集",
    "a_restale": "重新蒐集", "x_resend": "同筆重送", "x_recheck": "稍後再查",
    "x_existing": "已有寫入", "x_failed": "寫入失敗", "x_escalated": "交給人工",
    "h_replay_refused": "不重新送入", "h_resolve": "人工查明",
    "ai_hypothesis": "推測原因",
    # Phase 13 增量 2:AI 參與決策的判斷點、「AI 要再查」回頭節點、只判不送的終點
    "a_ai": "AI 選下一步", "a_ai_query": "AI 要再查", "a_exam_hold": "只判不送",
}


@dataclass(frozen=True, slots=True)
class _FlowView:
    nodes: tuple[FlowNode, ...]
    active_edges: tuple[FlowEdge, ...]
    visited: frozenset[str]
    groups: tuple[tuple[str, tuple[FlowNode, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class _LaneBand:
    name: str
    top: int
    height: int


def escape_text(s: str) -> str:
    """逸出 HTML，並把不可見控制字元改成可辨識的 Unicode 代碼。"""
    visible = "".join(_visible_character(character) for character in s)
    return html.escape(visible, quote=True)


def _visible_character(character: str) -> str:
    if unicodedata.category(character).startswith("C"):
        return f"〔U+{ord(character):04X}〕"
    return character


def flow_label(label: str) -> str:
    """正式圖的內部稱呼在展示層換成讀者熟悉的字。"""
    return label.replace("模型", "AI")


def _ai_boundary(scenario: Scenario) -> str:
    """這個情境 AI 參與到哪；金額照舊由程式算。"""
    if scenario.ai_enabled:
        return ("這個情境讓 AI 參與決定下一步；"
                "金額、廣告與動作照舊由程式決定，AI 答不出或答錯就改由程式規則決定；"
                "AI 說明與推測只供參考。")
    return "目前正式預算決策由程式規則執行；AI 候選另行評估，AI 說明與推測供參考。"


def render_flow(  # noqa: PLR0915 - 流程圖組裝包含泳道、邊、節點與判斷框
    flow: FlowGraph, scenario: Scenario, node_details: Mapping[str, str] | None = None,
) -> str:
    view = _flow_view(flow, scenario)
    if not view.nodes:
        return '<p class="empty flow-empty">這個情境還沒有走過的路徑紀錄。</p>'
    if scenario.hypothesis is not None:
        advisory = FlowNode("ai_hypothesis", "AI 推測可能原因(只供參考)",
                            NodeKind.STEP, "分析", NodeOwner.AI)
        view = _FlowView((*view.nodes, advisory), view.active_edges,
                         view.visited | {advisory.id}, view.groups)
    positions, lanes, width = _flow_layout(view.nodes)
    height = len(lanes) * _LANE_HEIGHT + 100
    lane_bands = _render_lane_bands(lanes, width)
    lane_labels = "".join(
        f'<div class="flow-lane">{escape_text(lane.name)}</div>' for lane in lanes
    )
    marker_id = f"arrow-{scenario.code.value}"
    lanes_of = {node.id: node.lane for node in view.nodes}
    active_edges = "".join(
        _render_active_edge(edge, positions, marker_id, lanes_of)
        for edge in view.active_edges
        if edge.target in positions
    )
    sequence: dict[str, int] = {}  # 節點 → 它自己第一張判斷卡(代碼審 r1 p5)
    entered: dict[str, int] = {}  # 節點 → 第一次被走到的那張卡(它自己的,或走進它的那條邊的)
    for index, (item, _count) in enumerate(decision_cards(scenario.path), start=1):
        sequence.setdefault(item.node, index)
        for node_id in item.taken_edge or (item.node,):
            entered.setdefault(node_id, index)
    groups = dict(view.groups)
    details = node_details or {}
    popovers: list[str] = []
    for node in view.nodes:
        parts = [details[item.id] for item in groups.get(node.id, ()) if item.id in details]
        content = "".join(parts) if parts else details.get(
            node.id, '<p>這一步沒有留下判斷紀錄。</p>'
        )
        popover_id = f"flow-detail-{scenario.code.value}-{node.id}"
        popovers.append(
            f'<div class="flow-popover" id="{escape_text(popover_id)}" hidden '
            'role="dialog" aria-label="這一步的判斷內容">'
            '<button class="flow-popover-close" type="button" aria-label="關閉判斷內容">×</button>'
            f'<h4>{escape_text(flow_label(node.label))}</h4>{content}</div>'
        )
    nodes = "".join(
        _render_node(
            node,
            positions[node.id],
            node.id in view.visited,
            node.id == scenario.current_node or scenario.current_node in {
                item.id for item in groups.get(node.id, ())
            },
            _badge(sequence.get(node.id), entered.get(node.id)),
            tuple(sequence[item.id] for item in groups.get(node.id, ()) if item.id in sequence),
            groups.get(node.id, ()),
            tuple(fault.description for fault in scenario.injected_faults
                  if fault.node == node.id or fault.node in {
                      item.id for item in groups.get(node.id, ())
                  }),
            f"flow-detail-{scenario.code.value}-{node.id}",
        )
        for node in view.nodes
    )
    back = "".join(_render_back_edge(view.nodes, node.id, positions, width, height, marker_id)
                   for node in view.nodes)  # 路徑中段的回頭轉換也畫(代碼審 r2 g6)
    queued = "x_pending" in {node.id for node in view.nodes}
    handoff = _render_handoff(positions, queued=queued)
    wait = ("(這次沒有記錄)" if scenario.queue_wait_seconds is None else
            f"{scenario.queue_wait_seconds} 秒")
    faults = "".join(
        f'<p class="fault-note">展示故意製造：{escape_text(fault.description)}'
        f'（位置：{escape_text(SHORT_LABELS.get(fault.node, fault.node))}）</p>'
        for fault in scenario.injected_faults
    ) or '<p class="fault-note is-empty">這個情境沒有安排故障</p>'
    queue_note = (
        f"交給另一段程式；在排隊等了 {wait}"
        if queued else "交給另一段程式；收件後沒有進入執行佇列"
    )
    later = ('<p class="flow-later">後續階段這次未進入</p>'
             if len(lanes) < len(LANE_ORDER) else '')
    return (
        '<div class="flow-toolbar"><p><strong>實線</strong>是這次走過的路徑；'
        '淡色虛線是沒走的分支；回頭箭頭表示回到前一步。</p>'
        '<p class="flow-legend"><span class="legend-code">程式（Code）</span>'
        '<span class="legend-ai">AI（只供參考）</span><span class="legend-human">人工</span>'
        '<span class="legend-external">外部平台</span>'
        '<span>✓ 已經過</span><span>▶ 正在處理</span></p>'
        f'<p class="ai-boundary">{escape_text(_ai_boundary(scenario))}</p></div>'
        f'<p class="queue-note">{escape_text(queue_note)}</p>{later}'
        '<div class="diagram-view">'
        f'<input class="diagram-zoom" type="checkbox" id="zoom-{scenario.code.value}">'
        '<div class="diagram-actions"><span>處理流程 · 可左右捲動閱讀</span>'
        f'<label for="zoom-{scenario.code.value}"><span class="zoom-out">完整總覽</span>'
        '<span class="zoom-in">清楚閱讀</span></label></div>'
        f'<div class="diagram-frame lane-count-{len(lanes)}">'
        '<div class="flow-lanes" aria-label="處理角色">'
        f'{lane_labels}</div><div class="flow-scroll" tabindex="0" '
        'role="region" aria-label="處理流程，可使用左右方向鍵捲動"><div class="flow-canvas">'
        f'<svg class="flow-graph" role="group" aria-label="{escape_text(scenario.code.value)} '
        f'處理流程圖" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}"><defs>'
        f'<marker id="{marker_id}" markerWidth="8" '
        'markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" '
        'class="arrow-head"></path></marker></defs>'
        f"{lane_bands}{active_edges}{handoff}{back}{nodes}</svg></div></div></div>"
        f"{''.join(popovers)}</div>"
        f"{faults}"
    )


def _badge(own: int | None, entered: int | None) -> str | None:
    """節點上的編號:它自己第一張判斷卡。節點照第一次走到的時間排,它自己的卡比走到它的那張晚好幾張
    時,標出是經哪一張走到的,讀起來不會以為後面的步驟先發生(代碼審 r2 g4)。"""
    if own is None:
        return None
    if entered is not None and own > entered + 1:
        return f"判斷 {own}・經 {entered}"
    return f"判斷 {own}"


def _render_handoff(positions: dict[str, tuple[int, int]], *, queued: bool) -> str:
    if "i_check" not in positions:
        return ""
    source = next((node for node in ("a_submit", _SECOND_GROUP_ID, _GROUP_ID)
                   if node in positions), None)
    if source is None:
        return ""
    x = (positions[source][0] + (_GROUP_WIDTH if source in _GROUP_IDS else _NODE_WIDTH)
         + positions["i_check"][0]) // 2
    y = positions[source][1] + 110
    return (
        '<g class="flow-handoff"><title>交給另一段程式</title>'
        f'<rect x="{x - 25}" y="{y - 22}" width="50" height="44" rx="4"></rect>'
        f'<text x="{x}" y="{y - 2}">∕∕</text>'
        f'<text x="{x}" y="{y + 14}">{"佇列" if queued else "收件"}</text></g>'
    )


def render_untaken_branches(flow: FlowGraph, scenario: Scenario) -> str:
    """每條未走分支只畫到最近的結束點或這次已走過的節點。"""
    nodes = node_map(flow)
    outgoing: dict[str, list[FlowEdge]] = defaultdict(list)
    for edge in flow.edges:
        outgoing[edge.source].append(edge)
    visited = {item for pair in scenario.traversed_edges for item in pair}
    rows: list[str] = []
    taken = {decision.taken_edge for decision in scenario.path}
    seen: set[tuple[str, str]] = set()
    for decision in scenario.path:
        for edge in outgoing[decision.node]:
            pair = (edge.source, edge.target)
            if pair == decision.taken_edge or pair in taken or pair in seen:
                continue  # 同一條沒走的分支只列一次;別的工作真的走過的不算沒走(代碼審 r1 p8)
            seen.add(pair)
            chain = _branch_to_endpoint(edge.target, outgoing, nodes, visited)
            steps = "".join(
                '<span class="branch-connector" aria-hidden="true">→</span>'
                '<span class="branch-step">'
                f'{escape_text(SHORT_LABELS.get(node_id, flow_label(nodes[node_id].label)))}'
                '</span>'
                for node_id in chain
            )
            source = SHORT_LABELS.get(decision.node, decision.node)
            rows.append(
                '<div class="branch-route">'
                f'<span class="branch-source">{escape_text(source)}</span>'
                f'<span class="branch-connector" aria-hidden="true">→</span>'
                f'<span class="branch-step">{escape_text(flow_label(edge.label))}</span>'
                f'{steps}</div>'
            )
    if not rows:
        return ""
    return (
        '<details class="report-disclosure"><summary>這次沒走的分支'
        f'<span class="disclosure-count">{len(rows)} 條分支</span></summary>'
        '<div class="alternate-map">'
        '<p>淡色虛線只畫到下一個結束點，或接回這次走過的節點。</p>'
        f'{"".join(rows)}</div></details>'
    )


def _branch_to_endpoint(
    start: str,
    outgoing: dict[str, list[FlowEdge]],
    nodes: dict[str, FlowNode],
    visited: set[str],
) -> tuple[str, ...]:
    pending: list[tuple[str, ...]] = [(start,)]
    while pending:
        path = pending.pop(0)
        last = path[-1]
        if last in visited or nodes[last].kind is NodeKind.TERMINAL or not outgoing[last]:
            return path
        pending.extend((*path, edge.target) for edge in outgoing[last] if edge.target not in path)
    return (start,)


def _flow_layout(
    nodes: tuple[FlowNode, ...],
) -> tuple[dict[str, tuple[int, int]], tuple[_LaneBand, ...], int]:
    """只保留最後一條有節點的泳道以前的列；群組加寬，其餘節點保持可讀間距。"""
    positions: dict[str, tuple[int, int]] = {}
    x = 24
    for node in nodes:
        positions[node.id] = (x, 50 + LANE_ORDER.index(node.lane) * _LANE_HEIGHT + 30)
        x += _node_width(node) + 64
    last_lane = max(LANE_ORDER.index(node.lane) for node in nodes)
    lanes = tuple(_LaneBand(lane, 50 + index * _LANE_HEIGHT, _LANE_HEIGHT - 4)
                  for index, lane in enumerate(LANE_ORDER[:last_lane + 1]))
    last = nodes[-1]
    return positions, lanes, positions[last.id][0] + _node_width(last) + 12


def _flow_view(flow: FlowGraph, scenario: Scenario) -> _FlowView:
    nodes = node_map(flow)
    edge_map = {(edge.source, edge.target): edge for edge in flow.edges}
    # 只畫真的走過的邊:可以斷開、可以分好幾段(不同工作、回頭之後),同一條邊只畫一次;
    # 沒有紀錄的地方照實斷開,不從圖上替沒看到的判斷補路(代碼審第 3 輪 g2)
    trace_pairs = tuple(dict.fromkeys(scenario.traversed_edges))
    if any(pair not in edge_map for pair in trace_pairs):
        raise ValueError("情境路徑含有正式圖不存在的分支")
    trace = tuple(edge_map[pair] for pair in trace_pairs)
    # 節點照第一次出現的時間排:判斷紀錄本身就照時間排;沒有進來那條邊的判斷點插在它自己的時間位置,
    # 不追加到最後(代碼審 r1 p5)
    ordered_ids: list[str] = []

    def add(*node_ids: str) -> None:
        ordered_ids.extend(n for n in node_ids if n in nodes and n not in ordered_ids)

    consumed = 0  # 走過的邊照時間排:走到某一筆判斷帶的那條邊之前,先把之前的邊都放上去
    for decision in scenario.path:
        pair = decision.taken_edge
        if pair is not None and pair in trace_pairs[consumed:]:
            until = trace_pairs.index(pair, consumed) + 1
            for edge in trace[consumed:until]:
                add(edge.source, edge.target)
            consumed = until
        else:
            add(*(pair or (decision.node,)))
    for edge in trace[consumed:]:
        add(edge.source, edge.target)
    ordered_ids, trace, groups = _group_analysis(nodes, ordered_ids, trace)
    visited = frozenset(ordered_ids)
    return _FlowView(
        tuple(nodes[node_id] for node_id in ordered_ids),
        trace,
        visited,
        groups,
    )


def _group_analysis(
    nodes: dict[str, FlowNode], ordered_ids: list[str], trace: tuple[FlowEdge, ...],
) -> tuple[list[str], tuple[FlowEdge, ...], tuple[tuple[str, tuple[FlowNode, ...]], ...]]:
    groups: list[tuple[str, tuple[FlowNode, ...]]] = []
    if "a_submit" in ordered_ids:
        last = ordered_ids.index("a_submit")
        analysis = ordered_ids[:last + 1]
        replacement: dict[str, str] = {}
        code_run: list[str] = []

        def flush_code_run() -> None:
            if len(code_run) < 2:
                code_run.clear()
                return
            group_id = _GROUP_ID if not groups else _SECOND_GROUP_ID
            grouped = tuple(nodes[item] for item in code_run)
            groups.append((group_id, grouped))
            replacement.update(dict.fromkeys(code_run, group_id))
            nodes[group_id] = FlowNode(group_id, "程式分析步驟", NodeKind.STEP,
                                       "分析", NodeOwner.CODE)
            code_run.clear()

        for node_id in analysis:
            if nodes[node_id].owner is NodeOwner.CODE:
                code_run.append(node_id)
            else:
                flush_code_run()
        flush_code_run()
        mapped = [replacement.get(node_id, node_id) for node_id in ordered_ids]
        ordered_ids = [node_id for index, node_id in enumerate(mapped)
                       if index == 0 or node_id != mapped[index - 1]]
        trace = tuple(
            FlowEdge(replacement.get(edge.source, edge.source),
                     replacement.get(edge.target, edge.target), edge.label)
            for edge in trace
            if replacement.get(edge.source, edge.source) != replacement.get(
                edge.target, edge.target
            )
        )
    return ordered_ids, trace, tuple(groups)


def _render_lane_bands(lanes: tuple[_LaneBand, ...], width: int) -> str:
    return "".join(
        f'<g class="lane-band"><rect x="8" y="{lane.top}" '
        f'width="{width - 16}" height="{lane.height}" rx="10"></rect></g>'
        for lane in lanes
    )


def _render_active_edge(
    edge: FlowEdge, positions: dict[str, tuple[int, int]], marker_id: str,
    lanes_of: dict[str, str],
) -> str:
    source_x, source_y = positions[edge.source]
    target_x, target_y = positions[edge.target]
    start_x = source_x + _edge_start_offset(edge.source)
    end_x = target_x
    start_y = source_y + (100 if edge.source in _GROUP_IDS else _NODE_HEIGHT) // 2
    end_y = target_y + _NODE_HEIGHT // 2
    skipped = [
        node_id for node_id, (x, _y) in positions.items()
        if node_id not in (edge.source, edge.target)
        and min(source_x, target_x) < x < max(source_x, target_x)
        and lanes_of.get(node_id) in (lanes_of.get(edge.source), lanes_of.get(edge.target))
    ]
    if not skipped and end_x > start_x:
        middle_x = (start_x + end_x) // 2
        path = (
            f"M {start_x} {start_y} L {middle_x} {start_y} "
            f"L {middle_x} {end_y} L {end_x} {end_y}"
        )
        return (
            '<g class="flow-edge is-taken">'
            f'<title>{escape_text(flow_label(edge.label))}</title>'
            f'<path d="{path}" marker-end="url(#{marker_id})"></path></g>'
        )
    # 要跨過同一泳道的其他節點(或往回接):走節點列上方的空隙、畫成虛線,不從節點底下穿過(代碼審 r1 p5)
    gap_y = source_y - 24  # 在「判斷 N」編號字(節點上方 5 到 19)再上面,不劃過編號(代碼審 r2 g3)
    # 從節點寬的 3/4 處拉上去、落下來:編號字從左邊畫起,長的「判斷 N・經 M」也碰不到(代碼審 r3 p1)
    rise_x = source_x + _edge_start_offset(edge.source) * 3 // 4
    drop_x = target_x + _NODE_WIDTH * 3 // 4
    attach_y = target_y if target_y >= source_y else target_y + _NODE_HEIGHT
    path = (
        f"M {rise_x} {source_y} L {rise_x} {gap_y} L {drop_x} {gap_y} "
        f"L {drop_x} {attach_y}"
    )
    note = f"（跳過中間 {len(skipped)} 個節點）" if skipped else "（往回接）"
    shown = f"跳過 {len(skipped)} 個" if skipped else "往回接"
    return (
        '<g class="flow-edge is-taken is-skip">'
        f'<title>{escape_text(flow_label(edge.label) + note)}</title>'
        f'<path d="{path}" marker-end="url(#{marker_id})"></path>'
        f'<text class="skip-note" x="{(rise_x + drop_x) // 2}" y="{gap_y - 3}">'
        f"{escape_text(shown)}</text></g>"
    )


def _edge_start_offset(node_id: str) -> int:
    return (_GROUP_WIDTH if node_id in _GROUP_IDS else
            _AI_WIDTH if node_id in {"a_candidate", "a_narrate", "a_ai"} else _NODE_WIDTH)


def _node_width(node: FlowNode) -> int:
    return (_GROUP_WIDTH if node.id in _GROUP_IDS else
            _AI_WIDTH if node.owner is NodeOwner.AI else _NODE_WIDTH)


def _render_back_edge(
    nodes: tuple[FlowNode, ...], node_id: str, positions: dict[str, tuple[int, int]],
    width: int, height: int, marker_id: str,
) -> str:
    from rtb.demo.flow import BACK_TRANSITIONS

    back = next((item for item in BACK_TRANSITIONS if item.node == node_id), None)
    if back is None:
        return ""
    target = back.returns_to if back.returns_to in positions else _GROUP_ID
    if target not in positions:
        return ""
    source_x, source_y = positions[back.node]
    target_x, target_y = positions[target]
    top = back.lane == "分析"
    rail_y = 24 if top else height - 25
    source_end = source_y if top else source_y + _NODE_HEIGHT
    target_end = target_y if top else target_y + (100 if target in _GROUP_IDS else _NODE_HEIGHT)
    start_x = source_x + _NODE_WIDTH // 2
    end_x = target_x + _node_width(next(node for node in nodes if node.id == target)) // 2
    path = (f"M {start_x} {source_end} L {start_x} {rail_y} "
            f"L {end_x} {rail_y} L {end_x} {target_end}")
    destination = "分析群組中的「" + SHORT_LABELS.get(back.returns_to, "收到工作") + "」" if (
        target == _GROUP_ID
    ) else SHORT_LABELS.get(back.returns_to, back.returns_to)
    label = "回到：" + destination
    label_x = max(88, min(width - 90, (start_x + end_x) // 2))
    label_y = rail_y - 5 if top else rail_y + 17
    return (
        '<g class="flow-return">'
        f'<title>{escape_text(label)}</title>'
        f'<path d="{path}" marker-end="url(#{marker_id})"></path>'
        f'<text x="{label_x}" y="{label_y}">{escape_text(label)}</text></g>'
    )


def _render_node(  # noqa: PLR0913 - 節點要帶狀態、編號、群組與故障標記
    node: FlowNode,
    position: tuple[int, int],
    visited: bool,
    current: bool,
    badge_text: str | None,
    group_numbers: tuple[int, ...],
    grouped: tuple[FlowNode, ...],
    faults: tuple[str, ...],
    popover_id: str,
) -> str:
    x, y = position
    state_class = "is-current" if current else "is-visited" if visited else "is-future"
    shape = _node_shape(node.kind, x, y, _node_width(node), 100 if grouped else _NODE_HEIGHT)
    short = ("確認資料與建議" if grouped else
             SHORT_LABELS.get(node.id, flow_label(node.label)))
    lines = _svg_lines(short, 24 if grouped else 20)
    first_y = y + 34 - (len(lines) - 1) * 9
    label = "".join(
        f'<tspan x="{x + _node_width(node) // 2}" y="{first_y + index * 15}">'
        f'{escape_text(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    state = "▶ 進行中" if current else "✓ 已經過" if visited else "○ 未進行"
    badge = (
        f'<text class="decision-badge" x="{x + 4}" y="{y - 7}">{escape_text(badge_text)}</text>'
        if badge_text is not None
        else ""
    )
    if group_numbers:
        covered = (f"判斷 {group_numbers[0]}" if len(group_numbers) == 1 else
                   f"含判斷 {group_numbers[0]}–{group_numbers[-1]}")
        badge = (f'<text class="decision-badge" x="{x + 4}" y="{y - 7}">'
                 f'{covered}</text>')
    details = (
        f'<text class="group-detail" x="{x + _GROUP_WIDTH // 2}" y="{y + 61}">'
        f'{len(grouped)} 步・點選看判斷</text>'
        if grouped else ""
    )
    title = (
        "；".join(flow_label(item.label) for item in grouped)
        if grouped else flow_label(node.label)
    )
    marker = (
        f'<text class="fault-marker" x="{x + _node_width(node) - 13}" y="{y + 20}">!</text>'
        if faults else ""
    )
    return (
        f'<g class="flow-node {state_class} kind-{node.kind.name.lower()} '
        f'owner-{node.owner.name.lower()}{" is-group" if grouped else ""}" '
        f'tabindex="0" role="button" aria-expanded="false" '
        f'aria-controls="{escape_text(popover_id)}" '
        f'data-flow-popover="{escape_text(popover_id)}">'
        f"<title>{escape_text(title)}</title>{shape}{badge}{marker}"
        f'<text class="node-label">{label}</text>'
        f'{details}<text class="node-state" x="{x + _node_width(node) // 2}" '
        f'y="{y + (82 if grouped else 62)}">{state}</text></g>'
    )


def _svg_lines(text: str, limit: int) -> tuple[str, ...]:
    lines: list[str] = []
    current = ""
    units = 0
    for character in text:
        character_units = 2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
        if current and units + character_units > limit:
            lines.append(current)
            current, units = "", 0
        current += character
        units += character_units
    if current:
        lines.append(current)
    return tuple(lines) or ("",)


def _node_shape(kind: NodeKind, x: int, y: int, width: int, height: int) -> str:
    radius = height // 2 if kind is NodeKind.TERMINAL else (
        16 if kind is NodeKind.DECISION else 12
    )
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}"></rect>'
    )


def validate_flow(flow: FlowGraph) -> None:
    nodes = node_map(flow)
    if len(nodes) != len(flow.nodes):
        raise ValueError("流程圖節點 id 不可重複")
    successors: dict[str, list[str]] = defaultdict(list)
    indegree = dict.fromkeys(nodes, 0)
    for edge in flow.edges:
        if edge.source not in nodes or edge.target not in nodes:
            raise ValueError("流程圖邊指向不存在的節點")
        successors[edge.source].append(edge.target)
        indegree[edge.target] += 1
    ready = [node.id for node in flow.nodes if indegree[node.id] == 0]
    visited = 0
    while ready:
        current = ready.pop(0)
        visited += 1
        for target in successors[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(flow.nodes):
        raise ValueError("流程圖含有環，無法畫成 DAG")


def node_map(flow: FlowGraph) -> dict[str, FlowNode]:
    return {node.id: node for node in flow.nodes}


def edge_label(flow: FlowGraph, pair: tuple[str, str] | None) -> str:
    if pair is None:
        return "這一步對不到圖上的分支"
    return next(
        (flow_label(edge.label) for edge in flow.edges if (edge.source, edge.target) == pair),
        f"{pair[0]} → {pair[1]}",
    )


_CARD_LIMIT: Final = 60  # 判斷超過這麼多筆(F7 那種幾千筆)就依節點與分支彙總成一張卡,標筆數


def decision_cards(path: tuple[Decision, ...]) -> tuple[tuple[Decision, int], ...]:
    """要畫的判斷卡:筆數不多就逐筆;很多就依(節點, 分支)彙總,留第一筆的根據、照第一次出現排
    (代碼審 r1 p8:F7 單頁原本 4.7MB)。"""
    if len(path) <= _CARD_LIMIT:
        return tuple((decision, 1) for decision in path)
    first: dict[tuple[str, tuple[str, str] | None], Decision] = {}
    counts: dict[tuple[str, tuple[str, str] | None], int] = {}
    for decision in path:
        key = (decision.node, decision.taken_edge)
        first.setdefault(key, decision)
        counts[key] = counts.get(key, 0) + 1
    return tuple((decision, counts[key]) for key, decision in first.items())
