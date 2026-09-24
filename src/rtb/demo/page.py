# ruff: noqa: RUF001, RUF002
"""把展示狀態安全地轉成無腳本 HTML。"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from rtb.demo.flow_svg import (
    LANE_ORDER,
    decision_cards,
    edge_label,
    escape_text,
    flow_label,
    node_map,
    render_flow,
    validate_flow,
)
from rtb.demo.state import (
    ApprovalForm,
    ChangeSummary,
    Comparison,
    Decision,
    DecisionKind,
    DemoState,
    FlowGraph,
    FlowNode,
    ModelMode,
    ModelSource,
    NodeKind,
    Scenario,
    ScenarioCode,
    ScenarioStatus,
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
    ("版本已變", "內部用語，指廣告在建議寫好之後被改過，要照現況重新判斷。"),
    ("結果不明", "內部用語，指送出寫入後平台沒有明確回覆，還不知道有沒有寫進去。"),
    ("能力憑證", "內部用語，指執行程式寫入前必須帶著的簽章授權，限定能改哪個廣告、改多少。"),
    ("驗證器", "內部用語，指自動查核程式：逐條核對每一項宣稱都有對應的測試證據。"),
    ("雜湊", "內部用語，指把一份內容算成一串固定長度的識別碼，內容一改識別碼就不同。"),
    ("修訂", "內部用語，指同一件工作的第幾版建議。"),
    ("故障注入", "內部用語，指展示時刻意製造的故障，用來看系統怎麼應對。"),
    ("泳道", "內部用語，指流程圖上一條橫列，代表一個負責的角色。"),
    ("模型", "內部用語，指 AI；這個頁面統一稱為「AI」。"),
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


def render_report(state: DemoState, *, inline_styles: bool = True) -> str:
    """產生沒有表單、沒有自動重讀的靜態展示報告。另存的檔案把樣式內嵌(file:// 打開也有版面);
    經伺服器送的連同源樣式表(內容安全政策不准內嵌樣式,代碼審 r1 p2)。"""
    validate_flow(state.flow)
    scenarios = "".join(
        '<article class="report-scenario">'
        f"{_render_focus(scenario, state.flow, id_suffix=scenario.code.value, compact=True)}"
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
        '<a href="#scenarios">情境與流程</a><a href="#report-details">查核、限制與名詞</a></nav>'
        f"{_render_system_map(state.flow)}"
        '<div class="report-workspace">'
        f"{_render_scenario_index(state, state.scenarios[0], False, None)}"
        f'<section class="report-scenarios" aria-label="七個情境的處理流程">{scenarios}</section>'
        f"</div>{_render_report_details(state)}</main>"
    )
    head = _render_head(refresh_url=None, inline_styles=inline_styles)
    return f'<!doctype html><html lang="zh-Hant">{head}<body>{body}</body></html>'


def _render_report_details(state: DemoState) -> str:
    """報告底部:自動查核的完整結果、這次示範的範圍與限制、名詞小辭典(使用者 2026-09-24 裁定:
    小辭典與已知限制只放在另存的報告裡,主頁不放)。"""
    verdict, source = _verifier_summary(state)
    return (
        '<section class="secondary" id="report-details" aria-label="報告補充資訊">'
        '<div class="detail-grid">'
        f"{_detail('自動查核結果', _render_verifier(verdict, source, state.verifier))}"
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
    validate_flow(state.flow)
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
    mode = _MODE_TEXT[state.model_mode]
    cost = "—" if state.model_cost_usd is None else f"{_decimal(state.model_cost_usd)} 美元"
    reason = state.model_mode_reason or "原因未記錄"
    started = "—" if state.started_at is None else _format_time(state.started_at)
    verdict, source = _verifier_summary(state)
    primary: tuple[tuple[str, str, str | None], ...] = (
        ("已完成情境", f"{done} / {len(state.scenarios)}", None),
        ("自動查核", verdict, source),
        ("AI 說明方式", mode, reason),
        ("本次 AI 費用", cost, None),
        ("開始時間", started, None),
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
            ("自動查核（範例值）", verdict, source),
            ("資料來源", "預設範例", None),
            ("AI 費用（範例值）", cost, None),
            ("範例時間", started, None),
        )
    cards = "".join(
        f"<div><dt>{escape_text(label)}</dt><dd>{escape_text(value)}"
        f"{f'<small>{escape_text(detail)}</small>' if detail is not None else ''}</dd></div>"
        for label, value, detail in primary
    )
    last_cost = (
        f"{_decimal(state.last_full_run_cost_usd)} 美元"
        if state.last_full_run_cost_usd is not None
        else "沒有呼叫 AI，沒有費用"  # 代碼審 r2 g7:沒有費用不是沒有完整執行紀錄
        if state.model_mode is ModelMode.NOT_CALLED
        else "尚無完整執行紀錄"
    )
    technical = (
        ("本次展示編號", state.demo_id or "—"),
        ("自動查核資料識別碼", state.verifier_digest or "尚未產生"),
        ("程式版本", state.commit or "尚未記錄"),
        ("上次完整執行 AI 費用", last_cost),
    )
    rows = "".join(
        f"<div><dt>{escape_text(label)}</dt><dd>{escape_text(value)}</dd></div>"
        for label, value in technical
    )
    return (
        f'{notice}<dl class="summary-strip">{cards}</dl>{_render_verifier_detail(state.verifier)}'
        f"{_render_report_note(state.report_note)}"
        '<details class="meta-details">'
        f"<summary>技術資訊（追查時再看）</summary><dl>{rows}</dl></details>"
    )


_MODE_TEXT: Final = {
    ModelMode.RECORDED: "使用預先錄好的內容（沒有即時連線）",
    ModelMode.LIVE: "現場即時產生",
    ModelMode.NOT_CALLED: "這次沒有呼叫 AI",
}


def _verifier_summary(state: DemoState) -> tuple[str, str | None]:
    """[S1031][S1035][S1054] 主頁摘要列常駐的自動查核格:通過或發現問題(文字,不只靠顏色)、取自哪一次;
    完整展示在跑、這次還沒跑到寫「這次還沒查核」;還沒有完整執行過就照實寫。"""
    verifier = state.verifier
    if verifier is None:
        if state.verifier_pending:
            return "這次還沒查核", "這次完整展示跑完七個情境後才查核"
        if state.full_demo_id is None and not state.is_sample:
            return "還沒有完整執行過", None
        return "尚未執行", None
    verdict = "✓ 通過" if verifier.passed else "! 發現問題"
    return verdict, (f"取自 {_format_time(verifier.verified_at)} 那次完整執行"
                     f"（展示編號 {verifier.demo_id}）")


def _render_report_note(note: str | None) -> str:
    """上一次另存報告沒存成:照實顯示原因(代碼審 r2 s3)。"""
    if not note:
        return ""
    return f'<p class="report-note"><strong>注意：</strong>{escape_text(note)}</p>'


def _render_verifier_detail(verifier: VerifierResult | None) -> str:
    """[S1031] 擋下時逐條列原因(常駐);驗證器原樣的每一行收在可展開的區塊。"""
    if verifier is None:
        return ""
    reasons = "".join(f"<li>{escape_text(reason)}</li>" for reason in verifier.reasons)
    blocked = (
        '<div class="verifier-blocked"><strong>自動查核擋下的原因</strong>'
        f"<ul>{reasons}</ul></div>"
        if not verifier.passed else ""
    )
    return (
        f"{blocked}<details class=\"meta-details verifier-output\"><summary>自動查核原樣輸出"
        f"</summary>{_plain_lines(verifier.lines)}</details>"
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
    """只在展示在跑時畫(跑完就沒有「現在進度」;代碼審 r1 p7/s2);POST 轉址的 /#current 對到這裡。"""
    if state.current is None or not state.running:
        return ""
    current = state.current
    scenario = next((item for item in state.scenarios if item.code is current.scenario), None)
    node = node_map(state.flow).get(current.node)
    if scenario is None or node is None:
        raise ValueError("目前進度指向不存在的情境或流程節點")
    observed_at = state.observed_at or current.started_at
    elapsed = max(
        0,
        int((observed_at.astimezone(UTC) - current.started_at.astimezone(UTC)).total_seconds()),
    )
    position = state.scenarios.index(scenario) + 1
    last = _current_last_decision(current.last_decision, state.flow)
    return (
        '<aside class="current-progress" id="current" aria-label="現在進度">'
        '<span class="live-dot" aria-hidden="true"></span><strong>現在進度</strong>'
        f'<span class="progress-scenario">情境 {position} / 共 {len(state.scenarios)}・'
        f"{escape_text(scenario.code.value)} {escape_text(scenario.title)}</span>"
        f'<span class="progress-node">▶ {escape_text(flow_label(node.label))}・'
        f'已經在這一步 {elapsed} 秒</span>'
        f"{last}</aside>"
    )


def _current_last_decision(decision: Decision | None, flow: FlowGraph) -> str:
    if decision is None:
        return '<span class="progress-decision">上一個判斷：尚無</span>'
    node = node_map(flow).get(decision.node)
    label = decision.node if node is None else flow_label(node.label)
    ended = (decision.taken_edge is None and node is not None
             and node.kind is NodeKind.TERMINAL)
    branch = "已結束" if ended else edge_label(flow, decision.taken_edge)
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
    groups: dict[str, list[FlowNode]] = {lane: [] for lane in LANE_ORDER}
    for node in flow.nodes:
        groups.setdefault(node.lane, []).append(node)
    cards = []
    for index, lane in enumerate(LANE_ORDER, start=1):
        title, boundary = _ROLE_COPY[lane]
        steps = "".join(
            f'<li>{escape_text(flow_label(node.label))}</li>' for node in groups[lane]
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
        _render_scenario_row(item, item is focus, interactive, token, state)
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
    state: DemoState,
) -> str:
    running, flow = state.running or _is_awaiting_approval(state), state.flow
    selected_class = " is-selected" if selected else ""
    status_class = _STATUS_CLASS[scenario.status]
    status = f"{_STATUS_ICON[scenario.status]} {scenario.status.value}"
    result = scenario.result_summary or _fallback_result(scenario)
    origin = _origin_kind(scenario)
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
    return flow_label(node.label) if node is not None else node_id


def _fallback_result(scenario: Scenario) -> str:
    """沒有結果摘要時照狀態寫,不拿情境說明或最後一步頂替成結果(代碼審 r1 p4)。"""
    if scenario.status is ScenarioStatus.PENDING:
        return "尚未執行"
    if scenario.status in (ScenarioStatus.RUNNING, ScenarioStatus.AWAITING_APPROVAL):
        return "執行中，還沒有結果"
    if scenario.incomplete_reason:
        return scenario.incomplete_reason
    return "(這次沒有記錄結果)"


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
        f"{render_flow(flow, scenario)}"
        f"{_render_decisions(scenario, flow)}</section>"
    )


_SCALED_DOWN: Final = "規模縮小：這是等比例縮小的展示；完整規模由自動查核跑的 F7 測試證明。"


def _origin_kind(scenario: Scenario) -> str:
    """[S1054] 這個情境的結果取自哪一種執行:照結果所屬那一次展示記下的種類寫(完整執行、單一情境
    重跑);沒記種類的只寫取自哪一次,不猜成完整執行(代碼審 r2 g2/x1)。"""
    if scenario.source_demo_id is None or scenario.ran_at is None:
        return "還沒有執行紀錄"
    if scenario.source_full is None:
        return "取自展示"
    return "取自完整執行" if scenario.source_full else "取自單一情境重跑"


def _scenario_origin_text(scenario: Scenario) -> str:
    """[S1054] 逐情境出處:展示編號、時間、AI 模式;F7 另註明規模縮小([S1012])。"""
    if scenario.source_demo_id is None or scenario.ran_at is None:
        return "這個情境還沒有執行紀錄"
    mode = (
        "AI 說明方式未記錄"
        if scenario.model_mode is None
        else "這次沒有呼叫 AI"
        if scenario.model_mode is ModelMode.NOT_CALLED
        else f"AI 採{scenario.model_mode.value}方式"
    )
    scale = f"；{_SCALED_DOWN}" if scenario.code is ScenarioCode.F7 else ""
    kind = _origin_kind(scenario)
    place = (f"的第 {list(ScenarioCode).index(scenario.code) + 1} 個情境"
             if scenario.source_full else "")
    return (
        f"{kind}（展示編號 {scenario.source_demo_id}）{place}，"
        f"情境執行編號 {scenario.source_demo_id}-{scenario.code.value}；"
        f"執行時間 {_format_time(scenario.ran_at)}，{mode}{scale}"
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


def _render_decisions(scenario: Scenario, flow: FlowGraph) -> str:
    nodes = node_map(flow)
    shown = decision_cards(scenario.path)
    cards = "".join(
        _decision_card(decision, index, len(shown), nodes, flow, count)
        for index, (decision, count) in enumerate(shown, start=1)
    )
    if not cards:
        cards = '<li class="empty">還沒有判斷紀錄。</li>'
    grouped = len(shown) != len(scenario.path)
    heading = ("判斷很多，依步驟與分支彙總，每張卡標出筆數，根據取第一筆。" if grouped
               else "由左到右，就是系統這次實際判斷的順序。")
    return (
        '<details class="report-disclosure"><summary>每一步為什麼這樣判'
        f'<span class="disclosure-count">{len(scenario.path)} 個判斷</span></summary>'
        '<div class="decision-heading">'
        f"<p>{heading}</p></div>"
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
    count: int = 1,
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
        f'<div><p class="decision-node">{escape_text(flow_label(node.label))}'
        f'{f"（共 {count} 筆）" if count > 1 else ""}</p>'
        f'<p class="decision-outcome">{escape_text(decision.outcome)}</p>'
        f'{basis_block}'
        f'{operation}'
        f"<time>{escape_text(at)}</time></div></li>"
    )


def _detail(title: str, content: str) -> str:
    return (
        f'<details class="detail-card"><summary>{escape_text(title)}</summary>'
        f"<div>{content}</div></details>"
    )


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _plain_lines(lines: tuple[str, ...]) -> str:
    return "".join(f'<p class="raw-line">{escape_text(line)}</p>' for line in lines)


def _model_text(text: str | None, source: ModelSource | None) -> str:
    if text is None:
        return '<p class="empty">沒有 AI 說明。</p>'
    source_text = "內容來源未記錄" if source is None else f"{source.value}內容"
    return (
        '<div class="model-note"><p class="model-warning">AI 產生，只供參考，不會控制系統・'
        f"{escape_text(source_text)}</p><p>{escape_text(text)}</p></div>"
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


def _render_verifier(verdict: str, source: str | None, verifier: VerifierResult | None) -> str:
    """[S1031][S1035] 報告裡的自動查核:通過或發現問題的文字、取自哪一次、擋下原因逐條、
    原樣每一行。"""
    css_class = ("status-pending" if verifier is None
                 else "status-done" if verifier.passed else "status-incomplete")
    note = f'<p class="source-note">{escape_text(source)}</p>' if source else ""
    if verifier is None:
        return f'<p class="status {css_class}">{escape_text(verdict)}</p>{note}'
    reasons = "".join(f"<li>{escape_text(reason)}</li>" for reason in verifier.reasons)
    return (
        f'<p class="status {css_class}">自動查核{escape_text(verdict)}</p>{note}'
        f"{f'<ul>{reasons}</ul>' if reasons else ''}{_plain_lines(verifier.lines)}"
    )


def _render_comparison(comparison: Comparison | None) -> str:
    """[S1041] 前後比較表:每一列造假手法、沒有自動查核、有自動查核並排,加說明(比的是有沒有機械
    驗證)。還沒產生就照實寫。"""
    if comparison is None:
        return '<p class="empty">前後比較這次還沒有產生(完整執行跑完自動查核之後才產生)。</p>'
    rows = "".join(
        f"<tr><td>{escape_text(row.forgery)}</td><td>{escape_text(row.without_verifier)}</td>"
        f"<td>{escape_text(row.with_verifier)}</td></tr>"
        for row in comparison.rows
    )
    table = (
        '<div class="table-scroll"><table><thead><tr><th>造假手法</th>'
        "<th>沒有自動查核</th><th>有自動查核</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
        if comparison.rows else ""
    )
    return f'<p class="comparison-note">{escape_text(comparison.note)}</p>{table}'


def _render_limits(items: tuple[str, ...]) -> str:
    content = "".join(f"<li>{escape_text(item)}</li>" for item in items)
    return f'<ul class="limits-list">{content}</ul>'


def _render_glossary() -> str:
    items = "".join(
        f"<dt>{escape_text(term)}</dt><dd>{escape_text(explanation)}</dd>"
        for term, explanation in _GLOSSARY
    )
    return f'<dl class="glossary">{items}</dl>'
