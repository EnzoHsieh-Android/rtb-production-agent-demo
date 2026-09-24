# ruff: noqa: RUF001, RUF002
"""把展示狀態安全地轉成無腳本 HTML。"""

from __future__ import annotations

import html
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from rtb.demo.state import (
    ApprovalForm,
    ChangeSummary,
    Comparison,
    Decision,
    DecisionKind,
    DemoState,
    Disposition,
    DspState,
    FlowEdge,
    FlowGraph,
    FlowNode,
    Hypothesis,
    ModelMode,
    ModelSource,
    ModelStep,
    NodeKind,
    NodeOwner,
    Scenario,
    ScenarioCode,
    ScenarioStatus,
    TimelineStep,
    VerifierResult,
)

CONTENT_SECURITY_POLICY: Final = (
    "default-src 'none'; script-src 'none'; style-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; img-src 'none'"
)
STYLESHEET_PATH: Final = "/static/demo.css"
DEMO_CSS: Final = (Path(__file__).parent / "static" / "demo.css").read_text(encoding="utf-8")

JARGON_TERMS: Final[tuple[str, ...]] = (
    "死信",
    "猝死",
    "總曝險",
    "冪等",
    "租約",
    "對帳",
    "提示注入",
    "稽核",
    "護欄",
    "核可",
    "重放",
    "提案",
    "收件口",
    "執行端",
    "分析端",
    "過時決策",
    "版本已變",
    "結果不明",
    "能力憑證",
    "驗證器",
    "雜湊",
    "修訂",
    "故障注入",
    "泳道",
    "模型",
)

DISPOSITION_TEXT: Final[dict[tuple[str, str], str]] = {
    ("擋下原因", "STALE_VERSION"): "廣告資料已更新，原本那次調整不再執行。",
    ("擋下原因", "POLICY_CHANGED"): "系統規則已更新，原本的判斷必須重新計算。",
    ("擋下原因", "GUARDRAIL_BLOCKED"): "這次調整超出允許的權限或單筆上限，所以沒有寫入。",
    ("擋下原因", "AGGREGATE_LIMIT"): "整個客戶帳戶這段期間加預算的總上限已滿，所以停止寫入。",
    ("平台回覆", "RESULT_UNKNOWN"): (
        "廣告平台沒有明確回覆，先查同一筆操作是否成功，不直接再送一次。"
    ),
    ("失敗處理", "DEAD_LETTER"): "已嘗試多次仍失敗，這筆工作已轉給人工處理。",
    ("處理待確認的結果", "APPROVAL_REQUIRED"): "變更幅度較大，必須由人逐項確認後才能執行。",
    ("擋下原因", "expired"): "資料已過可使用期限，所以這次調整被擋下。",
    ("處理待確認的結果", "expired"): "人工確認已過期，必須重新檢查後再決定。",
}
_DISPOSITION_LABELS: Final[dict[tuple[str, str], str]] = {
    ("擋下原因", "STALE_VERSION"): "資料已更新",
    ("擋下原因", "POLICY_CHANGED"): "規則已更新",
    ("擋下原因", "GUARDRAIL_BLOCKED"): "超出允許範圍",
    ("擋下原因", "AGGREGATE_LIMIT"): "帳戶加額已達上限",
    ("平台回覆", "RESULT_UNKNOWN"): "平台沒有明確回覆",
    ("失敗處理", "DEAD_LETTER"): "多次失敗，轉給人工",
    ("處理待確認的結果", "APPROVAL_REQUIRED"): "需要人工確認",
    ("擋下原因", "expired"): "資料已過期",
    ("處理待確認的結果", "expired"): "人工確認已過期",
}

_GLOSSARY: Final[tuple[tuple[str, str], ...]] = (
    (
        "廣告投放平台（DSP）",
        "管理廣告狀態與預算的外部系統；這個頁面統一稱為「廣告平台」。",
    ),
    ("AI 說明", "AI 依資料寫給人看的摘要，只供參考，不會替系統做決定。"),
    ("資料版本", "廣告每次更新後的編號，用來避免舊資料蓋掉新資料。"),
    ("UTC", "全球統一時間，讓不同地區看到的紀錄可以互相比對。"),
    ("F1～F7", "七個示範情境的編號，方便在展示與測試紀錄中快速對照。"),
    ("死信", "內部用語，指多次嘗試仍失敗、已轉給人工處理的工作。"),
    ("猝死", "內部用語，指程式執行到一半突然中斷。"),
    ("總曝險", "內部用語，指整個客戶帳戶在指定期間內可增加的預算總上限。"),
    ("冪等", "內部用語，指同一個動作送兩次也只會算一次。"),
    ("租約", "內部用語，指同一時間只讓一個程式處理同一筆工作。"),
    ("對帳", "內部用語，指不確定操作是否成功時，回頭查同一筆操作的結果。"),
    ("提示注入", "內部用語，指廣告名稱藏了想騙 AI 或系統照做的文字。"),
    ("稽核", "內部用語，指保留操作紀錄，方便事後確認發生過什麼事。"),
    ("護欄", "內部用語，指系統不允許超過的權限、金額或比例限制。"),
    ("核可", "內部用語，指由人確認關鍵數字後同意繼續。"),
    ("重放", "內部用語，指把失敗的工作重新送入一般流程。"),
    ("提案", "內部用語，指系統產生、尚未執行的調整建議。"),
    ("收件口", "內部用語，指接收調整建議並先檢查內容的程式。"),
    ("執行端", "內部用語，指通過檢查後，真正把變更寫進廣告平台的程式。"),
    ("分析端", "內部用語，指讀取資料、評估成效並產生調整建議的程式。"),
    ("過時決策", "內部用語，指做出判斷後資料或規則已改變，原判斷不能再用。"),
)

_STATUS_CLASS: Final = {
    ScenarioStatus.PENDING: "status-pending",
    ScenarioStatus.RUNNING: "status-running",
    ScenarioStatus.AWAITING_APPROVAL: "status-awaiting",
    ScenarioStatus.DONE: "status-done",
    ScenarioStatus.INCOMPLETE: "status-incomplete",
}
_STATUS_ICON: Final = {
    ScenarioStatus.PENDING: "○",
    ScenarioStatus.RUNNING: "▶",
    ScenarioStatus.AWAITING_APPROVAL: "◆",
    ScenarioStatus.DONE: "✓",
    ScenarioStatus.INCOMPLETE: "!",
}

_NODE_WIDTH: Final = 144
_AI_WIDTH: Final = 144
_GROUP_WIDTH: Final = 190
_NODE_HEIGHT: Final = 80
_STEP_SPACING: Final = 97
_LANE_HEIGHT: Final = 140
_LANE_ORDER: Final = ("分析", "收件", "執行", "廣告平台", "人工")
_FLOW_HEIGHT: Final = len(_LANE_ORDER) * _LANE_HEIGHT + 100
_GROUP_ID: Final = "analysis_group"
_SECOND_GROUP_ID: Final = "analysis_after_ai"
_GROUP_IDS: Final = frozenset({_GROUP_ID, _SECOND_GROUP_ID})

_SHORT_LABELS: Final[dict[str, str]] = {
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


def _flow_label(label: str) -> str:
    """正式圖的內部稱呼在展示層換成讀者熟悉的字。"""
    return label.replace("模型", "AI")


def disposition_text(category: str, code: str) -> str:
    """取得處置代碼的白話說明。"""
    return DISPOSITION_TEXT.get((category, code), f"(沒有白話說明:{category}/{code})")


def render_page(
    state: DemoState,
    *,
    form_token: str,
    refresh_tick: int,
    selected: ScenarioCode | None = None,
) -> str:
    """產生可觸發展示、可顯示即時進度的完整頁面；每次重讀須傳入新的 tick。"""
    return _render_document(
        state,
        form_token=form_token,
        interactive=True,
        selected=selected,
        refresh_tick=refresh_tick,
        inline_styles=False,
    )


def render_report(state: DemoState) -> str:
    """產生沒有表單、沒有自動重讀的靜態展示報告。"""
    _validate_flow(state.flow)
    scenarios = "".join(
        '<article class="report-scenario">'
        f"{_render_focus(scenario, state.flow, id_suffix=scenario.code.value, compact=True)}"
        f"{_detail('來源與操作識別', _render_scenario_origin(scenario))}"
        "</article>"
        for scenario in state.scenarios
    )
    body = (
        '<main class="report"><header class="hero"><div><p class="eyebrow">'
        "廣告預算安全調整示範</p><h1>RTB Agent Workflow｜流程報告</h1>"
        '<p class="lede">從七個情境，查看系統如何判斷、執行，'
        '以及在例外發生時停下或恢復。</p></div>'
        f"{_render_summary(state)}</header>"
        '<nav class="report-nav" aria-label="報告區段"><a href="#roles">角色分工</a>'
        '<a href="#scenarios">情境與流程</a></nav>'
        f"{_render_system_map(state.flow)}"
        '<div class="report-workspace">'
        f"{_render_scenario_index(state, state.scenarios[0], False, None)}"
        f'<section class="report-scenarios" aria-label="七個情境的處理流程">{scenarios}</section>'
        '</div></main>'
    )
    head = _render_head(refresh_url=None, inline_styles=True)
    return f'<!doctype html><html lang="zh-Hant">{head}<body>{body}</body></html>'


def _render_report_details(state: DemoState) -> str:
    return (
        '<section class="secondary" aria-label="報告補充資訊"><div class="detail-grid">'
        f"{_detail('自動查核結果', _render_verifier(state.verifier))}"
        f"{_detail('有無自動查核的差別', _render_comparison(state.comparison))}"
        f"{_detail('這次示範的範圍與限制', _render_limits(state.known_limits))}"
        f"{_detail('名詞小辭典', _render_glossary())}</div></section>"
    )


def render_approval(state: DemoState, *, form_token: str) -> str:
    """產生不會自動重讀的獨立人工確認頁。"""
    if state.approval is None:
        raise ValueError("沒有等待人工確認的資料")
    if not state.approval.numbers_digest:
        raise ValueError("人工確認缺少當次數字摘要識別值")
    body = (
        '<main class="approval-page"><header class="approval-page-heading">'
        '<p class="eyebrow">需要你決定</p><h1>逐項確認這次調整</h1>'
        '<p class="lede">這一頁不會自動重新整理，勾選到一半的內容會保留。</p>'
        '</header>'
        '<p class="approval-context">目前在人工確認：分析已提出建議，執行已檢查權限和金額；'
        '確認後會回到待處理流程，再重新檢查才可能寫入。</p>'
        f"{_render_approval_form(state.approval, form_token)}"
        '<p class="back-link"><a href="/#flow">返回展示主頁</a></p></main>'
    )
    head = _render_head(refresh_url=None, inline_styles=False)
    return f'<!doctype html><html lang="zh-Hant">{head}<body>{body}</body></html>'


def _render_document(
    state: DemoState,
    *,
    form_token: str | None,
    interactive: bool,
    selected: ScenarioCode | None,
    refresh_tick: int,
    inline_styles: bool,
) -> str:
    _validate_flow(state.flow)
    focus = _selected_scenario(state, selected)
    awaiting = _is_awaiting_approval(state)
    refresh_url = None
    if interactive and state.running and not awaiting:
        refresh_url = f"/?scenario={focus.code.value}&tick={refresh_tick}#flow"
    head = _render_head(refresh_url=refresh_url, inline_styles=inline_styles)
    current = _render_current_progress(state) if interactive else ""
    actions = _render_actions(state, form_token, focus) if interactive else ""
    body = (
        f'{current}<main><header class="hero"><div><p class="eyebrow">'
        "廣告預算安全調整示範</p>"
        "<h1>RTB Agent Workflow</h1>"
        '<p class="lede">每個情境都顯示資料檢查、判斷理由、是否真的寫入，'
        "以及最後結果好不好。七個情境是同一次展示依序跑的七個子案例。</p>"
        f"</div>{_render_summary(state)}{actions}</header>"
        f"{_render_system_map(state.flow)}"
        f"{_render_scenario_index(state, focus, interactive, form_token)}"
        f"{_render_focus(focus, state.flow)}"
        '</main>'
    )
    return f'<!doctype html><html lang="zh-Hant">{head}<body>{body}</body></html>'


def _render_head(*, refresh_url: str | None, inline_styles: bool) -> str:
    meta_refresh = (
        f'<meta http-equiv="refresh" content="2; url={escape_text(refresh_url)}">'
        if refresh_url is not None
        else ""
    )
    styles = (
        f"<style>{DEMO_CSS}</style>"
        if inline_styles
        else f'<link rel="stylesheet" href="{STYLESHEET_PATH}">'
    )
    return (
        '<head><meta charset="utf-8"><meta name="viewport" '
        f'content="width=device-width, initial-scale=1">{meta_refresh}'
        f"<title>RTB Agent Workflow</title>{styles}</head>"
    )


def _selected_scenario(state: DemoState, selected: ScenarioCode | None) -> Scenario:
    if not state.scenarios:
        raise ValueError("展示至少需要一個情境")
    if state.running and state.current is not None:
        selected = state.current.scenario
    if selected is not None:
        match = next((item for item in state.scenarios if item.code is selected), None)
        if match is not None:
            return match
    return state.scenarios[0]


def _render_summary(state: DemoState) -> str:
    done = sum(item.status is ScenarioStatus.DONE for item in state.scenarios)
    mode = (
        "使用預先錄好的內容（沒有即時連線）"
        if state.model_mode is ModelMode.RECORDED
        else "現場即時產生"
    )
    cost = "—" if state.model_cost_usd is None else f"{_decimal(state.model_cost_usd)} 美元"
    reason = state.model_mode_reason or "原因未記錄"
    primary: tuple[tuple[str, str, str | None], ...] = (
        ("已完成情境", f"{done} / {len(state.scenarios)}", None),
        ("AI 說明方式", mode, reason),
        ("本次 AI 費用", cost, None),
        ("開始時間", _format_time(state.started_at), None),
    )
    notice = ""
    if state.is_sample:
        notice = (
            '<p class="sample-notice"><strong>範例預覽</strong>'
            '本頁使用預設範例資料。情境結果、AI 文字、費用與查核狀態皆供版面展示，'
            '不代表本次實際執行。</p>'
        )
        primary = (
            ("展示情境", f"{len(state.scenarios)} 個", None),
            ("資料來源", "預設範例", None),
            ("AI 費用（範例值）", cost, None),
            ("範例時間", _format_time(state.started_at), None),
        )
    cards = "".join(
        f"<div><dt>{escape_text(label)}</dt><dd>{escape_text(value)}"
        f"{f'<small>{escape_text(detail)}</small>' if detail is not None else ''}</dd></div>"
        for label, value, detail in primary
    )
    last_cost = (
        "尚無完整執行紀錄"
        if state.last_full_run_cost_usd is None
        else f"{_decimal(state.last_full_run_cost_usd)} 美元"
    )
    technical = (
        ("本次展示編號", state.demo_id),
        ("自動查核資料識別碼", state.verifier_digest or "尚未產生"),
        ("程式版本", state.commit or "尚未記錄"),
        ("上次完整執行 AI 費用", last_cost),
    )
    rows = "".join(
        f"<div><dt>{escape_text(label)}</dt><dd>{escape_text(value)}</dd></div>"
        for label, value in technical
    )
    return (
        f'{notice}<dl class="summary-strip">{cards}</dl><details class="meta-details">'
        f"<summary>技術資訊（追查時再看）</summary><dl>{rows}</dl></details>"
    )


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _budget(value: int) -> str:
    """廣告平台上的預算原樣整數,沒有幣別(不換算、不自訂匯率)。"""
    return f"{value:,}"


def _render_actions(state: DemoState, token: str | None, focus: Scenario) -> str:
    if _is_awaiting_approval(state):
        return (
            '<div class="hero-action awaiting-actions"><p class="running-notice">'
            '<span aria-hidden="true">◆</span> 等你確認</p>'
            '<a class="primary-link" href="/approve">前往人工確認頁</a>'
            f'<a class="refresh-link" href="/?scenario={escape_text(focus.code.value)}#flow">'
            "重新整理</a></div>"
        )
    if state.running:
        return '<p class="running-notice"><span aria-hidden="true">▶</span> 展示進行中</p>'
    if token is None:
        raise ValueError("互動頁缺少表單值")
    return f'<div class="hero-action">{_form("/run", "全部跑一次", token, None)}</div>'


def _is_awaiting_approval(state: DemoState) -> bool:
    return any(item.status is ScenarioStatus.AWAITING_APPROVAL for item in state.scenarios)


def _form(action: str, label: str, token: str, scenario: str | None) -> str:
    hidden = f'<input type="hidden" name="token" value="{escape_text(token)}">'
    if scenario is not None:
        hidden += f'<input type="hidden" name="scenario" value="{escape_text(scenario)}">'
    suffix = f" {escape_text(scenario)}" if scenario else ""
    return (
        f'<form method="post" action="{action}">{hidden}'
        f'<button type="submit">{escape_text(label)}{suffix}</button></form>'
    )


def _render_current_progress(state: DemoState) -> str:
    if state.current is None:
        return ""
    current = state.current
    scenario = next((item for item in state.scenarios if item.code is current.scenario), None)
    node = _node_map(state.flow).get(current.node)
    if scenario is None or node is None:
        raise ValueError("目前進度指向不存在的情境或流程節點")
    observed_at = state.observed_at or state.started_at
    elapsed = max(
        0,
        int((observed_at.astimezone(UTC) - current.started_at.astimezone(UTC)).total_seconds()),
    )
    position = state.scenarios.index(scenario) + 1
    last = _current_last_decision(current.last_decision, state.flow)
    return (
        '<aside class="current-progress" aria-label="現在進度">'
        '<span class="live-dot" aria-hidden="true"></span><strong>現在進度</strong>'
        f'<span class="progress-scenario">情境 {position} / 共 {len(state.scenarios)}・'
        f"{escape_text(scenario.code.value)} {escape_text(scenario.title)}</span>"
        f'<span class="progress-node">▶ {escape_text(_flow_label(node.label))}・'
        f'已經在這一步 {elapsed} 秒</span>'
        f"{last}</aside>"
    )


def _current_last_decision(decision: Decision | None, flow: FlowGraph) -> str:
    if decision is None:
        return '<span class="progress-decision">上一個判斷：尚無</span>'
    node = _node_map(flow).get(decision.node)
    label = decision.node if node is None else _flow_label(node.label)
    branch = _edge_label(flow, decision.taken_edge)
    return (
        '<span class="progress-decision">上一個判斷（剛剛）：'
        f"{escape_text(label)} → {escape_text(decision.outcome)}（{escape_text(branch)}）</span>"
    )


_ROLE_COPY: Final = {
    "分析": ("讀取資料，決定是否提出建議", "只能建議，不能直接改預算"),
    "收件": ("檢查建議內容，收下後交給執行", "收下才完成跨行程交接"),
    "執行": ("重新確認資料、權限和金額", "通過檢查才寫入廣告平台"),
    "廣告平台": ("回覆寫入結果並提供實際狀態", "沒回覆時要查，不能猜成功"),
    "人工": ("確認較大調整或處理無法判明的結果", "同意後仍回到一般流程"),
}
_INCIDENT_NODE: Final = {
    ScenarioCode.F1: "x_unknown",
    ScenarioCode.F2: "x_reclaimed",
    ScenarioCode.F3: "i_check",
    ScenarioCode.F4: "x_precheck",
    ScenarioCode.F5: "a_candidate",
    ScenarioCode.F6: "x_deadletter",
    ScenarioCode.F7: "x_total",
}


def _render_system_map(flow: FlowGraph) -> str:
    groups: dict[str, list[FlowNode]] = {lane: [] for lane in _LANE_ORDER}
    for node in flow.nodes:
        groups.setdefault(node.lane, []).append(node)
    cards = []
    for index, lane in enumerate(_LANE_ORDER, start=1):
        title, boundary = _ROLE_COPY[lane]
        steps = "".join(
            f'<li>{escape_text(_flow_label(node.label))}</li>' for node in groups[lane]
        )
        cards.append(
            f'<li class="role-card"><span class="role-number">{index:02d}</span>'
            f'<h3>{escape_text(lane)}</h3><p>{escape_text(title)}</p>'
            f'<strong>{escape_text(boundary)}</strong>'
            f'<details><summary>流程節點 · {len(groups[lane])}</summary>'
            f'<ol>{steps}</ol></details></li>'
        )
    return (
        '<section class="system-map" id="roles" aria-labelledby="map-title">'
        '<div class="section-heading"><div><p class="section-kicker">整個系統怎麼運作</p>'
        '<h2 id="map-title">角色分工</h2></div>'
        '<p>依處理順序排列；展開角色可查看完整流程節點。</p></div>'
        f'<ol class="role-map">{"".join(cards)}</ol>'
        '<p class="map-note">分析行程提出建議；收件成功後，執行行程獨立重新檢查，'
        '才可能寫入廣告平台。人工確認會讓工作回到待處理流程。</p></section>'
    )


def _render_scenario_index(
    state: DemoState,
    focus: Scenario,
    interactive: bool,
    token: str | None,
) -> str:
    rows = "".join(
        _render_scenario_row(
            item, item is focus, interactive, token, state.running or _is_awaiting_approval(state),
            state.flow,
        )
        for item in state.scenarios
    )
    return (
        '<section class="scenario-section" id="scenarios" aria-labelledby="scenarios-title">'
        '<div class="section-heading"><div><p class="section-kicker">情境總覽</p>'
        '<h2 id="scenarios-title">選擇情境</h2></div>'
        "<p>選一個情境，查看結果與處理路徑。</p></div>"
        f'<ol class="scenario-index">{rows}</ol></section>'
    )


def _render_scenario_row(
    scenario: Scenario,
    selected: bool,
    interactive: bool,
    token: str | None,
    running: bool,
    flow: FlowGraph,
) -> str:
    selected_class = " is-selected" if selected else ""
    status_class = _STATUS_CLASS[scenario.status]
    status = f"{_STATUS_ICON[scenario.status]} {scenario.status.value}"
    result = scenario.result_summary or _fallback_result(scenario)
    origin = _scenario_origin_text(scenario)
    content = (
        f'<span class="scenario-code">{escape_text(scenario.code.value)}</span>'
        f'<span class="scenario-name">{escape_text(scenario.title)}</span>'
        f'<span class="scenario-pivot">轉向：{escape_text(_pivot_label(scenario, flow))}</span>'
        f'<span class="scenario-result">{escape_text(result)}'
        f'<small>{escape_text(origin)}</small></span>'
        f'<span class="status {status_class}">{escape_text(status)}</span>'
    )
    if interactive:
        content = (
            f'<a class="scenario-link" href="?scenario={scenario.code.value}#flow">{content}</a>'
        )
    else:
        content = f'<a class="scenario-link" href="#flow-{scenario.code.value}">{content}</a>'
    rerun = ""
    if interactive and not running:
        if token is None:
            raise ValueError("互動頁缺少表單值")
        rerun = _form("/run/scenario", "重跑", token, scenario.code.value)
    return f'<li class="scenario-row{selected_class}">{content}{rerun}</li>'


def _pivot_label(scenario: Scenario, flow: FlowGraph) -> str:
    node_id = _INCIDENT_NODE[scenario.code]
    node = next((item for item in flow.nodes if item.id == node_id), None)
    return _flow_label(node.label) if node is not None else node_id


def _fallback_result(scenario: Scenario) -> str:
    if scenario.incomplete_reason:
        return scenario.incomplete_reason
    if scenario.path:
        return scenario.path[-1].outcome
    return scenario.what_it_tests


def _render_focus(
    scenario: Scenario,
    flow: FlowGraph,
    *,
    id_suffix: str | None = None,
    compact: bool = False,
) -> str:
    status_class = _STATUS_CLASS[scenario.status]
    status = f"{_STATUS_ICON[scenario.status]} {scenario.status.value}"
    reason = (
        '<p class="incomplete"><strong>沒跑完：</strong>'
        f"{escape_text(scenario.incomplete_reason)}</p>"
        if scenario.incomplete_reason
        else ""
    )
    section_id = "flow" if id_suffix is None else f"flow-{id_suffix}"
    title_id = "flow-title" if id_suffix is None else f"flow-title-{id_suffix}"
    compact_class = " is-compact" if compact else ""
    return (
        f'<section class="focus-panel{compact_class}" id="{escape_text(section_id)}" '
        f'aria-labelledby="{escape_text(title_id)}">'
        '<div class="focus-heading"><div><p class="section-kicker">情境詳情</p>'
        f'<h2 id="{escape_text(title_id)}"><span>{escape_text(scenario.code.value)}</span> '
        f"{escape_text(scenario.title)}</h2>"
        f"<p>{escape_text(scenario.what_it_tests)}</p>"
        f"{_render_scenario_origin(scenario)}{reason}</div>"
        f'<span class="status {status_class}">{escape_text(status)}</span></div>'
        f"{_render_change_summary(scenario.change_summary, scenario.change_overview)}"
        f"{_render_result_evidence(scenario)}"
        '<details class="report-disclosure"><summary>觸發條件與目標</summary>'
        '<div class="scenario-intent">'
        f'<p><strong>為什麼開始跑：</strong>{escape_text(scenario.trigger or "(這次沒有記錄)")}</p>'
        f'<p><strong>目標：</strong>{escape_text(scenario.goal or "(這次沒有記錄)")}</p>'
        '</div></details>'
        f"{_render_flow(flow, scenario)}"
        f"{_render_decisions(scenario, flow)}</section>"
    )


def _scenario_origin_text(scenario: Scenario) -> str:
    if scenario.source_demo_id is None or scenario.ran_at is None:
        return "這個結果尚未記錄完整執行來源"
    mode = (
        "AI 說明方式未記錄"
        if scenario.model_mode is None
        else f"AI 採{scenario.model_mode.value}方式"
    )
    return (
        f"這次展示（編號 {scenario.source_demo_id}）裡的第 "
        f"{list(ScenarioCode).index(scenario.code) + 1} 個情境，"
        f"情境執行編號 {scenario.source_demo_id}-{scenario.code.value}；"
        f"執行時間 {_format_time(scenario.ran_at)}，{mode}"
    )


def _render_scenario_origin(scenario: Scenario) -> str:
    return f'<p class="source-note">{escape_text(_scenario_origin_text(scenario))}</p>'


def _render_change_summary(change: ChangeSummary | None, overview: str | None = None) -> str:
    extra = f'<p class="change-overview">{escape_text(overview)}</p>' if overview else ""
    if change is None and overview:
        return f'<div class="change-summary"><strong>最後改了什麼</strong>{extra}</div>'
    if change is None:
        return (
            '<div class="change-summary"><strong>最後改了什麼</strong>'
            '<p>(這次沒有記錄)</p></div>'
        )
    if not change.written:
        detail = f"沒有改任何預算。原因：{change.reason or '(這次沒有記錄)'}"
    else:
        before = "(這次沒有記錄)" if change.before is None else _budget(change.before)
        after = "(這次沒有記錄)" if change.after is None else _budget(change.after)
        detail = f"預算從 {before} 改成 {after}（平台預算單位）；已寫入廣告平台。"
    return (
        '<div class="change-summary"><strong>最後改了什麼</strong>'
        f'{extra}<p>廣告：{escape_text(change.campaign)}；{escape_text(detail)}</p></div>'
    )


def _render_result_evidence(scenario: Scenario) -> str:
    apply_count = (
        "(這次沒有記錄)" if scenario.platform_apply_count is None
        else str(scenario.platform_apply_count)
    )
    result = scenario.result_summary or _fallback_result(scenario)
    return (
        '<div class="result-evidence"><strong>這次結果：</strong>'
        f'<span>{escape_text(result)}</span>'
        '<details class="operation-details"><summary>操作識別</summary><p>同一筆操作的證據：'
        f'操作鍵 {escape_text(scenario.operation_key or "(這次沒有記錄)")}；'
        f'平台套用次數 {escape_text(apply_count)}</p></details></div>'
    )


def _render_flow(flow: FlowGraph, scenario: Scenario) -> str:
    view = _flow_view(flow, scenario)
    if not view.nodes:
        return '<p class="empty flow-empty">這個情境還沒有走過的路徑紀錄。</p>'
    if scenario.hypothesis is not None:
        advisory = FlowNode("ai_hypothesis", "AI 推測可能原因(只供參考)",
                            NodeKind.STEP, "分析", NodeOwner.AI)
        view = _FlowView((*view.nodes, advisory), view.active_edges,
                         view.visited | {advisory.id}, view.groups)
    positions, lanes, width = _flow_layout(view.nodes)
    lane_bands = _render_lane_bands(lanes, width)
    lane_labels = "".join(
        f'<div class="flow-lane">{escape_text(lane.name)}</div>' for lane in lanes
    )
    marker_id = f"arrow-{scenario.code.value}"
    active_edges = "".join(
        _render_active_edge(edge, positions, marker_id)
        for edge in view.active_edges
        if edge.target in positions
    )
    sequence = {item.node: index for index, item in enumerate(scenario.path, start=1)}
    groups = dict(view.groups)
    nodes = "".join(
        _render_node(
            node,
            positions[node.id],
            node.id in view.visited,
            node.id == scenario.current_node or scenario.current_node in {
                item.id for item in groups.get(node.id, ())
            },
            sequence.get(node.id),
            tuple(sequence[item.id] for item in groups.get(node.id, ()) if item.id in sequence),
            groups.get(node.id, ()),
            tuple(fault.description for fault in scenario.injected_faults
                  if fault.node == node.id or fault.node in {
                      item.id for item in groups.get(node.id, ())
                  }),
        )
        for node in view.nodes
    )
    back = _render_back_edge(view.nodes, positions, width, marker_id)
    queued = "x_pending" in {node.id for node in view.nodes}
    handoff = _render_handoff(positions, queued=queued)
    wait = ("(這次沒有記錄)" if scenario.queue_wait_seconds is None else
            f"{scenario.queue_wait_seconds} 秒")
    faults = "".join(
        f'<p class="fault-note">展示故意製造：{escape_text(fault.description)}'
        f'（位置：{escape_text(_SHORT_LABELS.get(fault.node, fault.node))}）</p>'
        for fault in scenario.injected_faults
    ) or '<p class="fault-note is-empty">展示故意製造：(這次沒有記錄)</p>'
    queue_note = (
        f"交給另一段程式；在排隊等了 {wait}"
        if queued else "交給另一段程式；收件後沒有進入執行佇列"
    )
    return (
        '<div class="flow-toolbar"><p><strong>實線</strong>是這次走過的路徑；'
        '淡色虛線是沒走的分支；回頭箭頭表示回到前一步。</p>'
        '<p class="flow-legend"><span class="legend-code">程式（Code）</span>'
        '<span class="legend-ai">AI（只供參考）</span><span class="legend-human">人工</span>'
        '<span class="legend-external">外部平台</span>'
        '<span>✓ 已經過</span><span>▶ 正在處理</span></p>'
        '<p class="ai-boundary">目前正式預算決策由程式規則執行；'
        'AI 候選另行評估，AI 說明與推測供參考。</p></div>'
        f'<p class="queue-note">{escape_text(queue_note)}</p>'
        '<div class="diagram-view">'
        f'<input class="diagram-zoom" type="checkbox" id="zoom-{scenario.code.value}">'
        '<div class="diagram-actions"><span>處理流程 · 可左右捲動閱讀</span>'
        f'<label for="zoom-{scenario.code.value}"><span class="zoom-out">完整總覽</span>'
        '<span class="zoom-in">清楚閱讀</span></label></div>'
        '<div class="diagram-frame"><div class="flow-lanes" aria-label="處理角色">'
        f'{lane_labels}</div><div class="flow-scroll" tabindex="0" '
        'role="region" aria-label="處理流程，可使用左右方向鍵捲動"><div class="flow-canvas">'
        f'<svg class="flow-graph" role="img" aria-label="{escape_text(scenario.code.value)} '
        f'處理流程圖" viewBox="0 0 {width} {_FLOW_HEIGHT}" '
        f'width="{width}" height="{_FLOW_HEIGHT}"><defs>'
        f'<marker id="{marker_id}" markerWidth="8" '
        'markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" '
        'class="arrow-head"></path></marker></defs>'
        f"{lane_bands}{active_edges}{handoff}{back}{nodes}</svg></div></div></div></div>"
        f"{faults}{_render_untaken_branches(flow, scenario)}"
    )


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


def _render_untaken_branches(flow: FlowGraph, scenario: Scenario) -> str:
    """每條未走分支只畫到最近的結束點或這次已走過的節點。"""
    nodes = _node_map(flow)
    outgoing: dict[str, list[FlowEdge]] = defaultdict(list)
    for edge in flow.edges:
        outgoing[edge.source].append(edge)
    visited = {item for pair in scenario.traversed_edges for item in pair}
    rows: list[str] = []
    for decision in scenario.path:
        for edge in outgoing[decision.node]:
            if (edge.source, edge.target) == decision.taken_edge:
                continue
            chain = _branch_to_endpoint(edge.target, outgoing, nodes, visited)
            steps = "".join(
                '<span class="branch-connector" aria-hidden="true">→</span>'
                '<span class="branch-step">'
                f'{escape_text(_SHORT_LABELS.get(node_id, _flow_label(nodes[node_id].label)))}'
                '</span>'
                for node_id in chain
            )
            source = _SHORT_LABELS.get(decision.node, decision.node)
            rows.append(
                '<div class="branch-route">'
                f'<span class="branch-source">{escape_text(source)}</span>'
                f'<span class="branch-connector" aria-hidden="true">→</span>'
                f'<span class="branch-step">{escape_text(_flow_label(edge.label))}</span>'
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
    """五列各佔 140px；群組加寬，其餘節點保持可讀間距。"""
    positions: dict[str, tuple[int, int]] = {}
    x = 24
    for node in nodes:
        positions[node.id] = (x, 50 + _LANE_ORDER.index(node.lane) * _LANE_HEIGHT + 30)
        x += _node_width(node) + 64
    lanes = tuple(_LaneBand(lane, 50 + index * _LANE_HEIGHT, _LANE_HEIGHT - 4)
                  for index, lane in enumerate(_LANE_ORDER))
    last = nodes[-1]
    return positions, lanes, positions[last.id][0] + _node_width(last) + 12


def _flow_view(flow: FlowGraph, scenario: Scenario) -> _FlowView:
    nodes = _node_map(flow)
    edge_map = {(edge.source, edge.target): edge for edge in flow.edges}
    # 只畫真的走過的邊:可以斷開、可以分好幾段(不同工作、回頭之後),照時間排;
    # 沒有紀錄的地方照實斷開,不從圖上替沒看到的判斷補路(代碼審第 3 輪 g2)
    trace_pairs = scenario.traversed_edges
    if any(pair not in edge_map for pair in trace_pairs):
        raise ValueError("情境路徑含有正式圖不存在的分支")
    trace = tuple(edge_map[pair] for pair in trace_pairs)
    ordered_ids: list[str] = []
    for edge in trace:
        if edge.source not in ordered_ids:
            ordered_ids.append(edge.source)
        if edge.target not in ordered_ids:
            ordered_ids.append(edge.target)
    for decision in scenario.path:  # 有判斷紀錄、但進來的邊沒記下的節點照樣畫出來
        if decision.node in nodes and decision.node not in ordered_ids:
            ordered_ids.append(decision.node)
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
    edge: FlowEdge, positions: dict[str, tuple[int, int]], marker_id: str
) -> str:
    source_x, source_y = positions[edge.source]
    target_x, target_y = positions[edge.target]
    start_x = source_x + (_GROUP_WIDTH if edge.source in _GROUP_IDS else
                          _AI_WIDTH if edge.source in {"a_candidate", "a_narrate"}
                          else _NODE_WIDTH)
    end_x = target_x
    start_y = source_y + (100 if edge.source in _GROUP_IDS else _NODE_HEIGHT) // 2
    end_y = target_y + _NODE_HEIGHT // 2
    middle_x = (start_x + end_x) // 2
    path = (
        f"M {start_x} {start_y} L {middle_x} {start_y} "
        f"L {middle_x} {end_y} L {end_x} {end_y}"
    )
    return (
        '<g class="flow-edge is-taken">'
        f'<title>{escape_text(_flow_label(edge.label))}</title>'
        f'<path d="{path}" marker-end="url(#{marker_id})"></path></g>'
    )


def _node_width(node: FlowNode) -> int:
    return (_GROUP_WIDTH if node.id in _GROUP_IDS else
            _AI_WIDTH if node.owner is NodeOwner.AI else _NODE_WIDTH)


def _render_back_edge(
    nodes: tuple[FlowNode, ...], positions: dict[str, tuple[int, int]],
    width: int, marker_id: str,
) -> str:
    from rtb.demo.flow import BACK_TRANSITIONS

    if not nodes:
        return ""
    back = next((item for item in BACK_TRANSITIONS if item.node == nodes[-1].id), None)
    if back is None:
        return ""
    target = back.returns_to if back.returns_to in positions else _GROUP_ID
    if target not in positions:
        return ""
    source_x, source_y = positions[back.node]
    target_x, target_y = positions[target]
    top = back.lane == "分析"
    rail_y = 24 if top else _FLOW_HEIGHT - 25
    source_end = source_y if top else source_y + _NODE_HEIGHT
    target_end = target_y if top else target_y + (100 if target in _GROUP_IDS else _NODE_HEIGHT)
    start_x = source_x + _NODE_WIDTH // 2
    end_x = target_x + _node_width(next(node for node in nodes if node.id == target)) // 2
    path = (f"M {start_x} {source_end} L {start_x} {rail_y} "
            f"L {end_x} {rail_y} L {end_x} {target_end}")
    destination = "分析群組中的「" + _SHORT_LABELS.get(back.returns_to, "收到工作") + "」" if (
        target == _GROUP_ID
    ) else _SHORT_LABELS.get(back.returns_to, back.returns_to)
    label = "回到：" + destination
    label_x = max(88, min(width - 90, (start_x + end_x) // 2))
    label_y = rail_y - 5 if top else rail_y + 17
    return (
        '<g class="flow-return">'
        f'<title>{escape_text(label)}</title>'
        f'<path d="{path}" marker-end="url(#{marker_id})"></path>'
        f'<text x="{label_x}" y="{label_y}">{escape_text(label)}</text></g>'
    )


def _render_node(  # noqa: PLR0913 - SVG needs state and grouped decision metadata
    node: FlowNode,
    position: tuple[int, int],
    visited: bool,
    current: bool,
    decision_number: int | None,
    group_numbers: tuple[int, ...],
    grouped: tuple[FlowNode, ...],
    faults: tuple[str, ...],
) -> str:
    x, y = position
    state_class = "is-current" if current else "is-visited" if visited else "is-future"
    shape = _node_shape(node.kind, x, y, _node_width(node), 100 if grouped else _NODE_HEIGHT)
    short = ("確認資料與建議" if grouped else
             _SHORT_LABELS.get(node.id, _flow_label(node.label)))
    lines = _svg_lines(short, 24 if grouped else 20)
    first_y = y + 34 - (len(lines) - 1) * 9
    label = "".join(
        f'<tspan x="{x + _node_width(node) // 2}" y="{first_y + index * 15}">'
        f'{escape_text(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    state = "▶ 進行中" if current else "✓ 已經過" if visited else "○ 未進行"
    badge = (
        f'<text class="decision-badge" x="{x + 4}" y="{y - 7}">判斷 {decision_number}</text>'
        if decision_number is not None
        else ""
    )
    if group_numbers:
        covered = (f"判斷 {group_numbers[0]}" if len(group_numbers) == 1 else
                   f"含判斷 {group_numbers[0]}–{group_numbers[-1]}")
        badge = (f'<text class="decision-badge" x="{x + 4}" y="{y - 7}">'
                 f'{covered}</text>')
    details = (
        f'<text class="group-detail" x="{x + _GROUP_WIDTH // 2}" y="{y + 61}">'
        f'{len(grouped)} 步・細節見下方卡片</text>'
        if grouped else ""
    )
    title = (
        "；".join(_flow_label(item.label) for item in grouped)
        if grouped else _flow_label(node.label)
    )
    marker = (
        f'<text class="fault-marker" x="{x + _node_width(node) - 13}" y="{y + 20}">!</text>'
        if faults else ""
    )
    return (
        f'<g class="flow-node {state_class} kind-{node.kind.name.lower()} '
        f'owner-{node.owner.name.lower()}{" is-group" if grouped else ""}">'
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


def _validate_flow(flow: FlowGraph) -> None:
    nodes = _node_map(flow)
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


def _node_map(flow: FlowGraph) -> dict[str, FlowNode]:
    return {node.id: node for node in flow.nodes}


def _edge_label(flow: FlowGraph, pair: tuple[str, str] | None) -> str:
    if pair is None:
        return "這一步對不到圖上的分支"
    return next(
        (_flow_label(edge.label) for edge in flow.edges if (edge.source, edge.target) == pair),
        f"{pair[0]} → {pair[1]}",
    )


def _render_decisions(scenario: Scenario, flow: FlowGraph) -> str:
    nodes = _node_map(flow)
    cards = "".join(
        _decision_card(decision, index, len(scenario.path), nodes, flow)
        for index, decision in enumerate(scenario.path, start=1)
    )
    if not cards:
        cards = '<li class="empty">還沒有判斷紀錄。</li>'
    return (
        '<details class="report-disclosure"><summary>每一步為什麼這樣判'
        f'<span class="disclosure-count">{len(scenario.path)} 個判斷</span></summary>'
        '<div class="decision-heading">'
        "<p>由左到右，就是系統這次實際判斷的順序。</p></div>"
        f'<div class="decision-scroll"><ol class="decision-trail">{cards}</ol></div></details>'
        f'{_render_ai_node_card(scenario)}'
    )


def _render_ai_node_card(scenario: Scenario) -> str:
    visited = {item for pair in scenario.traversed_edges for item in pair}
    narrative = scenario.model_step.narrative if scenario.model_step is not None else None
    content = "這次沒有請 AI 寫說明" if "a_narrate" not in visited or not narrative else narrative
    return (
        '<div class="ai-node-card"><strong>AI 寫說明</strong>'
        '<p>AI 寫的，只供參考</p>'
        f'<blockquote>{escape_text(content)}</blockquote></div>'
    )


def _decision_card(
    decision: Decision,
    index: int,
    total: int,
    nodes: dict[str, FlowNode],
    flow: FlowGraph,
) -> str:
    node = nodes.get(decision.node)
    if node is None or (decision.taken_edge is not None and not any(
        (edge.source, edge.target) == decision.taken_edge for edge in flow.edges
    )):
        raise ValueError("判斷紀錄指向不存在的節點或分支")
    latest = " is-latest" if index == total else ""
    at = "時間未記錄" if decision.at is None else _format_time(decision.at)
    basis = (
        "".join(
            '<li><span>量到的值：'
            f'{escape_text(item.observed)}</span><span>標準：{escape_text(item.standard)}</span>'
            f'<strong>比較結果：{escape_text(item.conclusion)}</strong>'
            + (f'<small class="basis-source">{escape_text(item.source)}</small>'
               if item.source else "")
            + '</li>'
            for item in decision.basis
        )
        if decision.basis else '<li>這一步沒有留下數字根據</li>'
    )
    operation = (
        f'<p class="decision-operation">操作鍵：{escape_text(decision.operation_key)}</p>'
        if decision.operation_key else ""
    )
    progress = decision.kind is DecisionKind.PROGRESS
    basis_block = (  # 狀態往前走不是判斷,不列根據欄,免得看起來像缺資料
        '<p class="decision-kind">狀態前進</p>' if progress
        else f'<div class="decision-basis"><strong>根據</strong><ul>{basis}</ul></div>'
    )
    kind_class = " is-progress" if progress else ""
    return (
        f'<li class="decision-card{latest}{kind_class}">'
        f'<div class="decision-number">{index:02d}</div>'
        f'<div><p class="decision-node">{escape_text(_flow_label(node.label))}</p>'
        f'<p class="decision-outcome">{escape_text(decision.outcome)}</p>'
        f'{basis_block}'
        f'{operation}'
        f"<time>{escape_text(at)}</time></div></li>"
    )


def _render_secondary(scenario: Scenario, state: DemoState) -> str:
    return (
        '<section class="secondary" aria-labelledby="details-title">'
        '<div class="section-heading"><div><p class="section-kicker">補充資訊</p>'
        '<h2 id="details-title">需要時再看細節</h2></div>'
        "<p>想追查完整過程時再展開，不影響上方主流程。</p></div>"
        '<div class="detail-grid">'
        f"{_detail('事情發生的順序', _render_timeline(scenario.timeline))}"
        f"{_detail('系統怎麼處理，以及原因', _render_dispositions(scenario.dispositions))}"
        f"{_detail('廣告平台最後狀態', _render_dsp(scenario.dsp))}"
        f"{_detail('系統留下的操作紀錄', _render_audit(scenario.audit))}"
        f"{_detail('AI 產生的說明（只供參考）', _render_model_step(scenario.model_step))}"
        f"{_detail('AI 推測的可能原因（只供參考）', _render_hypothesis(scenario.hypothesis))}"
        f"{_detail('自動查核結果', _render_verifier(state.verifier))}"
        f"{_detail('有無自動查核的差別', _render_comparison(state.comparison))}"
        f"{_detail('這次示範的範圍與限制', _render_limits(state.known_limits))}"
        f"{_detail('名詞小辭典', _render_glossary())}"
        "</div></section>"
    )


def _detail(title: str, content: str) -> str:
    return (
        f'<details class="detail-card"><summary>{escape_text(title)}</summary>'
        f"<div>{content}</div></details>"
    )


def _render_timeline(steps: tuple[TimelineStep, ...]) -> str:
    items = "".join(
        "<li><time>"
        f"{escape_text(_format_time(step.at))}</time><strong>"
        f"{escape_text(step.stage.value)}</strong>"
        f"<p>{escape_text(step.detail)}</p></li>"
        for step in steps
    )
    return f'<ol class="timeline">{items}</ol>'


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _render_dispositions(items: tuple[Disposition, ...]) -> str:
    content = "".join(
        f"<li><code>{escape_text(_disposition_label(item))}</code>"
        f"<p>{escape_text(disposition_text(item.category, item.code))} "
        f"{escape_text(item.explanation)}</p></li>"
        for item in items
    )
    return f'<ul class="definition-list">{content}</ul>'


def _disposition_label(item: Disposition) -> str:
    return _DISPOSITION_LABELS.get((item.category, item.code), "其他處理")


def _render_dsp(dsp: DspState | None) -> str:
    if dsp is None:
        return '<p class="empty">尚無資料。</p>'
    rows = "".join(
        f'<tr><td>{escape_text(item.campaign)}</td><td class="number">'
        f'{escape_text(_budget(item.budget))}</td><td class="number">'
        f"{escape_text(str(item.version))}</td><td>{escape_text(item.status)}</td></tr>"
        for item in dsp.campaigns
    )
    operations = _plain_lines(dsp.operations)
    return (
        '<div class="table-scroll"><table><thead><tr><th>廣告編號</th>'
        "<th>預算（元）</th><th>資料版本</th><th>目前狀態</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>{operations}"
    )


def _render_audit(items: tuple[str, ...]) -> str:
    return _plain_lines(items)


def _plain_lines(lines: tuple[str, ...]) -> str:
    return "".join(f'<p class="raw-line">{escape_text(line)}</p>' for line in lines)


def _render_model_step(step: ModelStep | None) -> str:
    if step is None:
        return '<p class="empty">尚未產生。</p>'
    return (
        f"<p><strong>AI 說明產生結果：</strong>{escape_text(step.result_kind)}</p>"
        f"{_render_numbers(step.numbers)}{_model_text(step.narrative, step.source)}"
    )


def _render_numbers(numbers: tuple[tuple[str, str], ...]) -> str:
    items = "".join(
        f"<div><dt>{escape_text(label)}</dt><dd>{escape_text(value)}</dd></div>"
        for label, value in numbers
    )
    return f'<dl class="numbers">{items}</dl>'


def _model_text(text: str | None, source: ModelSource | None) -> str:
    if text is None:
        return '<p class="empty">沒有 AI 說明。</p>'
    source_text = "內容來源未記錄" if source is None else f"{source.value}內容"
    return (
        '<div class="model-note"><p class="model-warning">AI 產生，只供參考，不會控制系統・'
        f"{escape_text(source_text)}</p><p>{escape_text(text)}</p></div>"
    )


def _render_hypothesis(item: Hypothesis | None) -> str:
    if item is None:
        return '<p class="empty">目前沒有需要調查的可能原因。</p>'
    hypotheses = "".join(f"<li>{escape_text(text)}</li>" for text in item.hypotheses)
    return (
        f"<p><strong>發現的狀況：</strong>{escape_text(item.alert)}</p><ul>{hypotheses}</ul>"
        f"<p><strong>建議先做：</strong>{escape_text(item.next_step)}</p>"
        '<p class="model-warning">AI 產生，只供參考，不會控制系統・'
        f"{escape_text(item.source.value)}內容</p>"
    )


def _render_approval_form(approval: ApprovalForm, token: str) -> str:
    confirmations = "".join(
        f'<label><input type="checkbox" name="confirm_{index}" value="1" required>'
        f"<span>{escape_text(label)}：{escape_text(value)}</span></label>"
        for index, (label, value) in enumerate(approval.numbers)
    )
    return (
        '<section class="approval" aria-labelledby="approval-title">'
        '<p class="section-kicker">需要人工確認</p><h2 id="approval-title">請確認這次調整</h2>'
        "<p>先逐項確認程式算出的數字，再閱讀 AI 產生的參考說明。</p>"
        f'<p class="source-note">當次數字摘要識別值：{escape_text(approval.numbers_digest)}</p>'
        f'<form method="post" action="/approve"><input type="hidden" name="token" '
        f'value="{escape_text(token)}"><input type="hidden" name="proposal_hash" '
        f'value="{escape_text(approval.proposal_hash)}"><input type="hidden" name="demo_id" '
        f'value="{escape_text(approval.demo_id)}"><input type="hidden" '
        f'name="numbers_digest" value="{escape_text(approval.numbers_digest)}">'
        '<div class="confirmations">'
        f"{confirmations}</div>{_model_text(approval.narrative, approval.source)}"
        '<button type="submit">確認並送出</button></form></section>'
    )


def _render_verifier(verifier: VerifierResult | None) -> str:
    if verifier is None:
        return '<p class="status status-pending">○ 尚未執行</p>'
    label = "✓ 自動查核通過" if verifier.passed else "! 自動查核發現問題"
    css_class = "status-done" if verifier.passed else "status-incomplete"
    lines = _plain_lines(verifier.lines)
    reasons = "".join(f"<li>{escape_text(reason)}</li>" for reason in verifier.reasons)
    source = (
        f"取自 {_format_time(verifier.verified_at)} 那次完整執行"
        f"（展示編號 {verifier.demo_id}）"
    )
    return (
        f'<p class="status {css_class}">{label}</p>'
        f'<p class="source-note">{escape_text(source)}</p>{lines}<ul>{reasons}</ul>'
    )


def _render_limits(items: tuple[str, ...]) -> str:
    content = "".join(f"<li>{escape_text(item)}</li>" for item in items)
    return f'<ul class="limits-list">{content}</ul>'


def _render_comparison(comparison: Comparison | None) -> str:
    if comparison is None:
        return '<p class="empty">沒有比較資料。</p>'
    rows = "".join(
        f"<tr><td>{escape_text(row.forgery)}</td><td>{escape_text(row.without_verifier)}</td>"
        f"<td>{escape_text(row.with_verifier)}</td></tr>"
        for row in comparison.rows
    )
    return (
        f'<p class="comparison-note">{escape_text(comparison.note)}</p>'
        '<div class="table-scroll"><table><thead><tr><th>不實說法</th>'
        "<th>沒有自動查核</th><th>有自動查核</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _render_glossary() -> str:
    items = "".join(
        f"<dt>{escape_text(term)}</dt><dd>{escape_text(explanation)}</dd>"
        for term, explanation in _GLOSSARY
    )
    return f'<dl class="glossary">{items}</dl>'
