# ruff: noqa: RUF001, S106
from __future__ import annotations

import base64
import hashlib
import html as html_lib
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from rtb.demo.page import (
    CONTENT_SECURITY_POLICY,
    DEMO_CSS,
    FLOW_SCRIPT,
    JARGON_TERMS,
    MODEL_LABEL,
    STYLESHEET_PATH,
    disposition_text,
    escape_text,
    render_approval,
    render_report,
)
from rtb.demo.page import render_page as _render_page
from rtb.demo.state import (
    ApprovalForm,
    CurrentStep,
    Decision,
    DecisionBasis,
    DecisionKind,
    DemoState,
    Disposition,
    ModelMode,
    ModelSource,
    ScenarioCode,
    ScenarioStatus,
)
from tests.demo.sample_data import make_demo_state, make_flow


def render_page(
    state: DemoState,
    *,
    form_token: str,
    refresh_tick: int = 1,
    selected: ScenarioCode | None = None,
) -> str:
    return _render_page(
        state, form_token=form_token, refresh_tick=refresh_tick, selected=selected
    )


def test_escape_text_turns_hostile_input_into_visible_plain_text() -> None:
    hostile = '<script>alert(1)</script> "><img src=x onerror=boom> &quot; " \u202e\u2066\x00'

    escaped = escape_text(hostile)

    assert "<script>" not in escaped
    assert "<img" not in escaped
    assert "onerror=" in escaped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in escaped
    assert "&amp;quot;" in escaped
    assert "&quot;" in escaped
    assert "〔U+202E〕" in escaped
    assert "〔U+2066〕" in escaped
    assert "〔U+0000〕" in escaped


@pytest.mark.parametrize("running", [False, True])
def test_page_never_contains_active_or_external_content(running: bool) -> None:
    html = render_page(make_demo_state(running=running), form_token='惡意"><script>')

    assert html.count('<script>') == 1
    tags = "\n".join(re.findall(r"<[^>]+>", html))
    assert re.search(r"\son[a-z]+\s*=", tags, re.IGNORECASE) is None
    assert re.search(r"\sstyle\s*=", tags, re.IGNORECASE) is None
    assert "javascript:" not in html.lower()
    assert re.search(r'(?:href|src)="(?:https?:)?//', tags, re.IGNORECASE) is None
    assert "<foreignObject" not in html
    assert f'href="{STYLESHEET_PATH}"' in html


def test_focus_reads_scenario_then_flow_then_results_and_collapses_other_details() -> None:
    page = render_page(make_demo_state(), form_token="t")
    focus = page.split('class="focus-panel', 1)[1]
    positions = [focus.index(text) for text in (
        'class="focus-heading"', '觸發條件：', 'class="flow-disclosure"',
        '處理結果概述', 'class="decision-hero"',
        'class="change-summary"', '這次沒走的分支', '操作識別',
    )]
    assert positions == sorted(positions)
    assert '<details class="scenario-intent" open>' in focus
    assert '每一步為什麼這樣判' not in focus


def test_flow_nodes_contain_escaped_step_details_for_hover_touch_and_keyboard() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    changed = replace(first, path=(replace(first.path[0], outcome='<script>危險</script>'),
                                   *first.path[1:]))
    page = render_page(replace(state, scenarios=(changed, *state.scenarios[1:])), form_token="t")
    report = render_report(replace(state, scenarios=(changed, *state.scenarios[1:])))
    for markup in (page, report):
        assert 'class="flow-node ' in markup and 'tabindex="0"' in markup
        assert 'role="button"' in markup and 'aria-expanded="false"' in markup
        assert 'data-flow-popover=' in markup
        assert 'class="flow-popover"' in markup and ' hidden tabindex="-1" role="dialog"' in markup
        assert '&lt;script&gt;危險&lt;/script&gt;' in markup
        assert '<script>危險</script>' not in markup
        assert '量到的值：' in markup and '標準：' in markup and '比較結果：' in markup
        assert 'class="flow-popover-close"' in markup
    assert 'max-height:' in DEMO_CSS and '.flow-popover' in DEMO_CSS
    assert 'Escape' in page and 'mouseenter' in page and 'focusin' in page


def test_flow_popover_scrolls_and_stays_inside_the_viewport() -> None:
    assert re.search(r'\.flow-popover\s*\{[^}]*max-height:[^;]+;[^}]*overflow-y:auto',
                     DEMO_CSS)
    assert 'const availableBelow' in FLOW_SCRIPT
    assert 'const availableAbove' in FLOW_SCRIPT
    assert 'box.style.maxHeight' in FLOW_SCRIPT
    assert 'box.style.left' in FLOW_SCRIPT and 'innerWidth - width - 8' in FLOW_SCRIPT
    assert 'box.style.top' in FLOW_SCRIPT and 'innerHeight - height - 8' in FLOW_SCRIPT


def test_flow_popover_hover_crosses_the_gap_without_closing() -> None:
    assert 'const HOVER_CLOSE_DELAY_MS = 300;' in FLOW_SCRIPT
    for target in ('node', 'box'):
        assert f"{target}.addEventListener('mouseenter'" in FLOW_SCRIPT
        assert f"{target}.addEventListener('mouseleave'" in FLOW_SCRIPT
    assert 'clearTimeout(closeTimer)' in FLOW_SCRIPT
    assert 'setTimeout(' in FLOW_SCRIPT and 'HOVER_CLOSE_DELAY_MS' in FLOW_SCRIPT


def test_flow_popover_wheel_scroll_does_not_close_or_move_the_page() -> None:
    assert re.search(r'\.flow-popover\s*\{[^}]*overscroll-behavior:contain', DEMO_CSS)
    assert "event.target.closest?.('.flow-popover')" in FLOW_SCRIPT
    assert "document.addEventListener('scroll'" in FLOW_SCRIPT
    assert 'rect.bottom <= 0' in FLOW_SCRIPT and 'rect.top >= innerHeight' in FLOW_SCRIPT


def test_page_and_report_remove_demo_mode_from_every_flow_label() -> None:
    state = make_demo_state()
    for markup in (render_page(state, form_token="t"), render_report(state)):
        text = html_lib.unescape(markup)
        assert '展示模式' not in text
        # Phase 14 增量 3:流程圖沒有 AI 決策節點、AI 要再查與考題終點
        assert "AI 選下一步" not in text and "AI 要再查" not in text and "只判不送" not in text
        assert 'flow-detail-F1-a_ai"' not in markup
        # 2026-09-27:Phase 10 候選判斷分支(交給誰判斷、請 AI 提供參考判斷)正式與展示都不走,撤除
        assert "候選" not in text and "參考判斷" not in text and "由誰判斷" not in text
        assert "選擇判法" not in text


def test_missing_ai_answer_is_plain_in_popover_values_and_comparisons() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    missing = "沒有對應的錄製回應"
    basis = DecisionBasis(missing, "正常標準", missing, "錄製回應")
    changed = replace(first, path=(replace(first.path[0], basis=(basis,), outcome=missing),
                                   *first.path[1:]))
    shown = replace(state, scenarios=(changed, *state.scenarios[1:]))
    for markup in (render_page(shown, form_token="t"), render_report(shown)):
        assert "量到的值：AI 這次沒有給出回答" in markup
        assert "比較結果：AI 這次沒有給出回答" in markup
        assert all(word not in markup for word in
                   ("錄製回應", "沒有對應的錄製", "即時呼叫", "即時清單"))


def test_unentered_flow_stage_names_only_the_lanes_not_reached() -> None:
    state = make_demo_state()
    markup = render_page(state, form_token="t")
    assert "人工這次沒有走到" in markup
    assert "廣告平台這次沒有走到" not in markup
    assert "後續階段這次未進入" not in markup
    seventh = render_page(state, form_token="t", selected=ScenarioCode.F7)
    # 範例 F7 在寫入前就停下等人確認:畫了廣告平台泳道但沒碰過,照實點名(增量 4 代碼審 r2 p1)
    assert '<p class="flow-later">廣告平台這次沒有走到</p>' in seventh


def test_scenario_intent_merges_repeated_goal_and_keeps_trigger() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    shared = "同一句目標不可重複"
    changed = replace(first, what_it_tests=shared, goal=shared, trigger="排程觸發")
    markup = render_page(replace(state, scenarios=(changed, *state.scenarios[1:])),
                         form_token="t")
    intent = markup.split('class="scenario-intent"', 1)[1].split('</details>', 1)[0]
    assert intent.count(shared) == 1
    assert "排程觸發" in intent
    assert intent.count('<p>') == 1


def test_interactive_workspace_has_sticky_sidebar_and_mobile_scenario_strip() -> None:
    markup = render_page(make_demo_state(), form_token="t", selected=ScenarioCode.F2)
    assert 'class="demo-workspace"' in markup
    assert markup.index('id="roles"') < markup.index('class="demo-workspace"')
    assert markup.index('id="scenarios"') < markup.index('id="flow"')
    assert 'class="scenario-row is-selected"' in markup
    assert 'href="?scenario=F2#flow"' in markup
    assert markup.count('action="/run/scenario"') == 1
    sidebar = markup.split('class="demo-workspace"', 1)[1].split('</section>', 1)[0]
    assert 'class="scenario-result"' not in sidebar
    assert re.search(r'\.demo-workspace\s*\{[^}]*grid-template-columns:270px minmax\(0,1fr\)',
                     DEMO_CSS)
    assert re.search(r'\.demo-workspace \.scenario-section\s*\{[^}]*position:sticky',
                     DEMO_CSS)
    mobile = DEMO_CSS.split('@media(max-width:760px)', 1)[1]
    assert re.search(r'\.demo-workspace\s*\{[^}]*grid-template-columns:1fr', mobile)
    assert re.search(r'\.demo-workspace \.scenario-index\s*\{[^}]*overflow-x:auto', mobile)
    assert re.search(r'\.demo-workspace \.scenario-row\s*\{[^}]*flex:', mobile)


def test_repeated_flow_node_lists_occurrences_and_large_same_results_merge() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    original = first.path[0]
    few = replace(first, path=(original, replace(original, outcome="另一種結果"), original))
    page = render_page(replace(state, scenarios=(few, *state.scenarios[1:])), form_token="t")
    assert '第 1 次' in page and '第 2 次' in page and '第 3 次' in page
    many = replace(first, path=tuple(replace(original, task_id=f"t{i}") for i in range(4200)))
    page = render_page(replace(state, scenarios=(many, *state.scenarios[1:])), form_token="t")
    assert '共 4200 筆，結果：' in page
    assert page.count('class="decision-card') < 20


def test_advisory_flow_cell_shows_its_existing_ai_explanation() -> None:
    page = render_page(make_demo_state(), form_token="t")
    cell = page.split('id="flow-detail-F1-ai_hypothesis"', 1)[1].split(
        '</div></div><p class="fault-note"', 1)[0]
    assert "廣告平台回覆變慢" in cell
    assert "先查廣告平台的操作紀錄" in cell


def test_running_page_refreshes_and_hides_trigger_forms() -> None:
    html = render_page(make_demo_state(running=True), form_token="表單值", refresh_tick=18)

    assert '<meta http-equiv="refresh" content="2; url=/?scenario=F2&amp;tick=18#flow">' in html
    next_html = render_page(make_demo_state(running=True), form_token="表單值", refresh_tick=19)
    assert "tick=19#flow" in next_html
    assert html != next_html
    assert "展示進行中" in html
    assert 'action="/run"' not in html
    assert 'action="/run/scenario"' not in html
    assert html.count("<svg") == 1
    assert '<g class="flow-node is-current' in html


def test_idle_page_has_one_all_form_and_selected_scenario_form_with_tokens() -> None:
    html = render_page(make_demo_state(running=False), form_token='t"<&')

    assert "展示進行中" not in html
    assert 'action="/run"' in html
    assert html.count('action="/run/scenario"') == 1
    assert html.count(">重跑 F") == 1
    assert html.count('name="token"') == 2
    assert 'value="t&quot;&lt;&amp;"' in html


def test_static_report_has_no_forms_or_refresh() -> None:
    html = render_report(make_demo_state(running=True))

    assert "<form" not in html
    assert 'http-equiv="refresh"' not in html
    assert "<style>" in html
    assert 'rel="stylesheet"' not in html
    assert 'href="/static/demo.css"' not in html


def test_model_text_follows_computed_numbers_and_is_clearly_labelled() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    assert first.model_step is not None
    hostile_step = replace(
        first.model_step,
        narrative="AI 建議：<strong>不是標籤</strong>",
    )
    html = render_page(
        replace(state, scenarios=(replace(first, model_step=hostile_step), *state.scenarios[1:])),
        form_token="token",
    )

    number_at = html.index("量到的值")
    narrative_at = html.index("AI 建議")
    assert number_at < narrative_at
    assert MODEL_LABEL in html  # Phase 13 增量 4:說明卡改用跟 AI 判斷同一個標示
    assert "範例預覽" in html
    assert "&lt;strong&gt;不是標籤&lt;/strong&gt;" in html
    assert "<strong>不是標籤</strong>" not in html


def test_decision_basis_and_missing_data_have_honest_visible_copy() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    first_decision = replace(first.path[0], basis=())
    missing = replace(
        first,
        path=(first_decision, *first.path[1:]),
        change_summary=None,
        trigger=None,
        goal=None,
        queue_wait_seconds=None,
        operation_key=None,
        platform_apply_count=None,
        injected_faults=(),
    )
    html = render_page(replace(state, scenarios=(missing, *state.scenarios[1:])),
                       form_token="token")

    assert "這一步沒有留下數字根據" in html
    assert "最後改了什麼</strong><p>(這次沒有記錄)" in html
    assert "觸發條件：</strong>(這次沒有記錄)" in html
    assert "在排隊等了 (這次沒有記錄)" in html
    assert "操作鍵 (這次沒有記錄)" in html
    assert "平台套用次數 (這次沒有記錄)" in html
    assert escape_text(first_decision.reason) not in html


def test_scenario_summary_fault_handoff_and_unique_execution_ids() -> None:
    state = make_demo_state()
    report = render_report(state)

    assert "從七個情境，查看系統如何判斷" in report
    assert report.count("最後改了什麼") == 7
    assert "沒有改任何預算。原因：" in report
    assert "預算從 100 改成 110（平台預算單位）；已寫入廣告平台" in report
    assert "交給另一段程式；在排隊等了 3 秒" in report
    assert 'class="flow-handoff"' in report
    assert "目前正式預算決策由程式規則執行" in report
    assert "展示故意製造：讓廣告平台回覆逾時" in report
    assert "操作鍵 op-demo-f1-001；平台套用次數 1" in report
    for index, scenario in enumerate(state.scenarios, start=1):
        assert f"第 {index} 個情境" in report
        assert f"情境執行編號 {state.demo_id}-{scenario.code.value}" in report


def test_ai_narrative_is_in_its_step_card_and_missing_ai_is_explained() -> None:
    state = make_demo_state()  # Phase 13 增量 4:沒有說明的情境照實寫(不再看有沒有走過寫說明節點)
    report = render_report(replace(state, scenarios=(
        state.scenarios[0], replace(state.scenarios[1], model_step=None), *state.scenarios[2:])))

    assert 'class="ai-node-card"' in report
    assert MODEL_LABEL in report  # Phase 13 增量 4:跟 AI 判斷同一個標示
    assert "近期帶來的成果穩定" in report
    assert "這次沒有請 AI 寫說明" in report
    assert "沒有候選或不在允許範圍" not in report
    assert "(這次不交給 AI)" not in report  # 2026-09-27 起沒有「交給誰判斷」的分流


def _verifier_cell(markup: str) -> str:
    match = re.search(r"<dt>自動查核[^<]*</dt><dd>(.*?)</dd>", markup)
    assert match is not None, "主頁摘要列要有一格常駐的自動查核"
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))


def test_every_status_has_a_text_label() -> None:
    """[S1035] 每一種情境狀態、自動查核通過或擋下都有文字標示,不只靠顏色(使用者 2026-09-24 裁定:
    主頁摘要列常駐一格自動查核)。"""
    for status in ScenarioStatus:
        state = make_demo_state()
        scenarios = (replace(state.scenarios[0], status=status), *state.scenarios[1:])
        assert status.value in render_page(replace(state, scenarios=scenarios), form_token="token")
    real = replace(make_demo_state(), is_sample=False, full_demo_id="2026-09-24-001")
    assert "✓ 通過" in _verifier_cell(render_page(real, form_token="token"))
    verifier = real.verifier
    assert verifier is not None
    blocked = replace(real, verifier=replace(verifier, passed=False))
    assert "! 發現問題" in _verifier_cell(render_page(blocked, form_token="token"))
    pending = replace(real, verifier=None, verifier_pending=True, running=True)
    assert "這次還沒查核" in _verifier_cell(render_page(pending, form_token="token"))
    never = replace(real, verifier=None, full_demo_id=None)
    assert "還沒有完整執行過" in _verifier_cell(render_page(never, form_token="token"))


def test_the_page_shows_the_verifier_output_verbatim() -> None:
    """[S1031][S1021] 主頁原樣顯示驗證器的每一行,擋下時逐條列出原因,並標明取自哪一次完整執行。"""
    state = replace(make_demo_state(), is_sample=False, full_demo_id="2026-09-24-001")
    verifier = state.verifier
    assert verifier is not None
    blocked = replace(state, verifier=replace(
        verifier, passed=False, lines=("宣稱驗證器", "擋下", "- 缺證據 A", "- 雜湊不符 B"),
        reasons=("缺證據 A", "雜湊不符 B")))
    for markup in (render_page(blocked, form_token="token"), render_report(blocked)):
        assert all(f'<p class="raw-line">{escape_text(line)}</p>' in markup
                   for line in ("宣稱驗證器", "擋下", "- 缺證據 A", "- 雜湊不符 B"))
        assert "<li>缺證據 A</li>" in markup and "<li>雜湊不符 B</li>" in markup
        assert f"展示編號 {verifier.demo_id}" in markup
    main = render_page(blocked, form_token="token")
    assert main.index("自動查核擋下的原因") < main.index('class="meta-details"')  # 常駐,不收起來


def test_approval_has_one_required_checkbox_per_number_without_number_in_attributes() -> None:
    approval = ApprovalForm(
        proposal_hash="hash<&",
        numbers=(("金額", "秘密數字-101"), ("租戶", "秘密數字-202")),
        narrative="請核准<script>",
        source=ModelSource.LIVE,
        demo_id='demo"><img',
        numbers_digest='digest"><script',
    )
    html = render_approval(replace(make_demo_state(), approval=approval), form_token="token")

    checkboxes = re.findall(r'<input type="checkbox"[^>]+>', html)
    assert len(checkboxes) == len(approval.numbers)
    assert all(" required" in checkbox for checkbox in checkboxes)
    assert 'name="confirm_0" value="1"' in html
    assert 'name="confirm_1" value="1"' in html
    assert all("秘密數字" not in checkbox for checkbox in checkboxes)
    assert 'name="demo_id" value="demo&quot;&gt;&lt;img"' in html
    assert 'name="numbers_digest" value="digest&quot;&gt;&lt;script"' in html
    assert 'http-equiv="refresh"' not in html


def test_the_approval_form_does_not_promise_an_ai_narrative_it_never_gets() -> None:
    """Issues/流程圖把AI說明畫在送出建議之前(代碼審 r1 正確性 F2 改寫理由):組表單時就不帶說明,
    一鍵展示也要等情境整段跑完才產說明——表單出現時建議早已送出,所以不能寫成「送出之後才寫」。
    表單不准再叫人「閱讀 AI 產生的參考說明」,要照實寫這兩個原因。"""
    html = render_approval(replace(make_demo_state(), approval=_PENDING), form_token="token")
    assert "閱讀 AI" not in html and "參考說明" not in html
    assert "這張表單不帶 AI 說明" in html and "同意與否只看程式算出的數字" in html
    assert "情境整段跑完才產生說明" in html and "送出之後才另外寫" not in html
    assert "寫入前照樣重新檢查" in html


def test_awaiting_approval_main_page_has_link_and_no_refresh_or_form() -> None:
    state = make_demo_state(running=True)
    scenarios = tuple(
        replace(item, status=ScenarioStatus.AWAITING_APPROVAL)
        if item.code is ScenarioCode.F7
        else item
        for item in state.scenarios
    )
    html = render_page(replace(state, scenarios=scenarios, approval=_PENDING), form_token="token")

    assert 'http-equiv="refresh"' not in html
    assert 'href="/approve"' in html
    assert "重新整理" in html
    assert "等你確認" in html
    assert 'action="/approve"' not in html


_PENDING = ApprovalForm(proposal_hash="h", numbers=(("建議增加", "100"),), narrative=None,
                        source=None, demo_id="demo", numbers_digest="d" * 64)


def test_main_page_waits_whenever_the_approval_page_has_a_form() -> None:
    """[S1046] 主頁跟確認頁用同一個判準:確認頁有表單(確認請求已寫)就算情境狀態還沒改成等你確認,
    主頁也不自動重讀、放去確認的連結(CI 間歇失敗:驅動程式兩筆交易之間的那一刻)。"""
    html = render_page(replace(make_demo_state(running=True), approval=_PENDING), form_token="t")

    assert 'http-equiv="refresh"' not in html
    assert 'href="/approve"' in html


def test_a_leftover_request_after_the_demo_stopped_does_not_hold_the_main_page() -> None:
    """驅動程式在兩步之間出錯中止時確認請求可能沒人清:展示不在跑就不算等你確認,
    主頁照常給「全部跑一次」與「重跑」(代碼審 r1 正確性席)。"""
    html = render_page(replace(make_demo_state(running=False), approval=_PENDING), form_token="t")

    assert "全部跑一次" in html and "重跑" in html
    assert 'href="/approve"' not in html


def test_main_page_resumes_refreshing_once_the_form_is_gone() -> None:
    """確認送出後(確認請求已清)情境狀態還留在等你確認的那一小段,主頁照常重讀,不停在舊畫面。"""
    state = make_demo_state(running=True)
    scenarios = tuple(replace(item, status=ScenarioStatus.AWAITING_APPROVAL)
                      if item.code is ScenarioCode.F7 else item for item in state.scenarios)
    html = render_page(replace(state, scenarios=scenarios, approval=None), form_token="t")

    assert 'http-equiv="refresh"' in html


def test_content_security_policy_contains_every_required_directive() -> None:
    for directive in (
        "default-src 'none'",
        "script-src 'sha256-",
        "style-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "img-src 'none'",
    ):
        assert directive in CONTENT_SECURITY_POLICY


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_both_colour_schemes_have_enough_contrast() -> None:
    """[S1037] 深色與淺色兩組配色的文字與背景對比至少 4.5 比 1;另外核對樣式表是本機、會縮放的。"""
    css = DEMO_CSS  # 樣式表跟頁面同一份(不靠工作目錄的相對路徑;代碼審 r1 t9)

    assert "prefers-color-scheme: dark" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "@import" not in css
    assert "url(" not in css
    assert "overflow-x: auto" in css
    assert re.search(r"\.flow-scroll\s*\{[^}]*max-height", css) is None
    assert ".flow-scroll { overflow-x: auto; overflow-y: hidden; }" in css
    assert "position: sticky;" in css
    assert "left: 0;" in css
    assert "text-overflow: ellipsis" not in css
    assert "-webkit-line-clamp" not in css
    assert "scroll-padding-top: 14rem" in css
    assert ".focus-heading h2, .report-scenario { scroll-margin-top: 14rem; }" in css
    colour_pairs = re.findall(
        r"--(?P<mode>light|dark)-background:\s*(?P<background>#[0-9a-fA-F]{6});\s*"
        r"--(?P=mode)-text:\s*(?P<text>#[0-9a-fA-F]{6});",
        css,
    )
    assert len(colour_pairs) == 2
    for _, background, text in colour_pairs:
        assert _contrast(background, text) >= 4.5
    semantic_pairs = (
        ("#a8adb9", "#0b0d12"),
        ("#7d8492", "#11141b"),
        ("#9aa8ff", "#222849"),
        ("#ffd166", "#372d17"),
        ("#75e6bd", "#12362c"),
        ("#ff9cae", "#3c1d25"),
        ("#555b67", "#f7f7f8"),
        ("#6b7280", "#ffffff"),
        ("#4454c7", "#eceeff"),
        ("#885600", "#fff2cf"),
        ("#08785b", "#ddf7ee"),
        ("#a72c49", "#ffe6eb"),
    )
    for foreground, background in semantic_pairs:
        assert foreground in css and background in css
        assert _contrast(foreground, background) >= 4.5


def test_unknown_disposition_has_visible_fallback() -> None:
    assert disposition_text("擋下原因", "MYSTERY") == "(沒有白話說明:擋下原因/MYSTERY)"


def test_same_disposition_code_has_category_specific_explanation() -> None:
    assert disposition_text("擋下原因", "expired") != disposition_text(
        "處理待確認的結果", "expired"
    )
    state = make_demo_state()
    first = replace(
        state.scenarios[0],
        dispositions=(
            Disposition("擋下原因", "expired", "資料期限到了"),
            Disposition("處理待確認的結果", "expired", "確認期限到了"),
        ),
    )
    html = render_page(replace(state, scenarios=(first, *state.scenarios[1:])), form_token="t")
    assert "資料已過可使用期限" not in html
    assert "人工確認已過期" not in html
    assert "資料已過可使用期限" in disposition_text("擋下原因", "expired")
    assert "人工確認已過期" in disposition_text("處理待確認的結果", "expired")


def test_report_navigation_and_sample_provenance_are_explicit() -> None:
    state = make_demo_state()
    report = render_report(state)
    assert 'class="sample-notice"' in report
    assert 'class="sample-notice"' not in render_report(replace(state, is_sample=False))
    body = report.split('<body>', 1)[1]
    # 使用者 2026-09-24 裁定:名詞小辭典與已知限制只放在另存的報告裡,主頁不放
    assert 'href="#report-details"' in body and 'id="report-details"' in body
    assert '名詞小辭典' in body and '這次示範的範圍與限制' in body
    main = render_page(state, form_token="token")
    assert '名詞小辭典' not in main and '這次示範的範圍與限制' not in main
    assert report.count('<script>') == 1
    script = report.split('<script>', 1)[1].split('</script>', 1)[0]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in CONTENT_SECURITY_POLICY
    for scenario in state.scenarios:
        code = scenario.code.value
        assert f'href="#flow-{code}"' in report
        assert f'id="flow-{code}"' in report
        assert f'id="zoom-{code}"' in report
        assert f'for="zoom-{code}"' in report


def test_handoff_labels_do_not_cover_nodes_and_arrows_have_room() -> None:
    report = render_report(make_demo_state())
    for svg in re.findall(r'<svg class="flow-graph".*?</svg>', report, re.DOTALL):
        root = ET.fromstring(svg)  # noqa: S314 - generated XML
        nodes = [group for group in root.findall('.//g[@class]')
                 if 'flow-node' in group.get('class', '')]
        handoff = root.find('.//g[@class="flow-handoff"]/rect')
        assert handoff is not None
        x, y, w, h = (int(handoff.get(key, '0')) for key in ('x', 'y', 'width', 'height'))
        for node in nodes:
            nx, ny, nw, nh = (_rect_coordinate(node, key)
                              for key in ('x', 'y', 'width', 'height'))
            assert not _overlaps((x, y, x + w, y + h), (nx, ny, nx + nw, ny + nh))
        for left, right in pairwise(nodes):
            assert (_rect_coordinate(right, 'x') - _rect_coordinate(left, 'x')
                    - _rect_coordinate(left, 'width')) >= 64


def test_cyclic_flow_is_rejected() -> None:
    flow = make_flow(cyclic=True)
    state = replace(make_demo_state(), flow=flow)

    with pytest.raises(ValueError, match="環"):
        render_page(state, form_token="token")


def test_svg_marks_taken_current_and_untaken_paths_without_active_content() -> None:
    html = render_page(make_demo_state(running=True), form_token="token")
    svgs = re.findall(r"<svg\b.*?</svg>", html, re.DOTALL)

    assert svgs
    markup = "".join(svgs)
    assert 'class="flow-edge is-taken"' in markup
    assert 'class="alternate-map"' in html
    assert 'class="branch-route"' in html
    assert 'class="flow-node is-current' in markup
    assert "▶ 進行中" in markup
    assert "✓ 已經過" in markup
    assert "這次沒走的分支" in html
    assert re.search(r"<\s*script\b", markup, re.IGNORECASE) is None
    tags = "\n".join(re.findall(r"<[^>]+>", markup))
    assert re.search(r"\son[a-z]+\s*=", tags, re.IGNORECASE) is None
    assert "foreignObject" not in markup
    assert re.search(r"(?:https?:)?//", markup) is None


def test_decision_table_rows_match_path_and_latest_is_marked() -> None:
    state = make_demo_state(running=True)
    running = next(item for item in state.scenarios if item.status is ScenarioStatus.RUNNING)
    html = render_page(state, form_token="token")
    cards = re.findall(r'class="decision-card(?: is-latest)?"', html)

    assert len(cards) == len(running.path)
    assert html.count("剛剛") >= 1


def test_rendering_the_same_finished_state_is_byte_for_byte_deterministic() -> None:
    state = make_demo_state(running=False)

    assert render_report(state) == render_report(state)


def test_rendering_the_same_running_state_is_byte_for_byte_deterministic() -> None:
    state = make_demo_state(running=True)

    assert render_page(state, form_token="token") == render_page(state, form_token="token")


def test_node_labels_and_decision_reasons_are_plain_text() -> None:
    state = make_demo_state()
    flow = make_flow()
    hostile_nodes = tuple(
        replace(node, label="蒐集證據 <節點>") if node.id == "a_collect" else node
        for node in flow.nodes
    )
    first = state.scenarios[0]
    hostile_path = (
        replace(first.path[0], basis=(replace(first.path[0].basis[0],
                                               observed="不採信 <img src=x onerror=boom>"),)),
        *first.path[1:],
    )
    html = render_page(
        replace(
            state,
            flow=replace(flow, nodes=hostile_nodes),
            scenarios=(replace(first, path=hostile_path), *state.scenarios[1:]),
        ),
        form_token="token",
    )

    assert "&lt;節點&gt;" in html
    assert "<節點>" not in html
    assert "&lt;img src=x onerror=boom&gt;" in html
    assert "<img src=x onerror=boom>" not in html


def test_current_progress_bar_contains_scenario_node_duration_and_last_decision() -> None:
    state = make_demo_state(running=True)
    html = render_page(state, form_token="token")

    assert 'class="current-progress"' in html
    assert "現在進度" in html
    assert "情境 2 / 共 7" in html
    assert "已經在這一步" in html
    assert "上一個判斷" in html
    assert state.current is not None
    assert state.current.last_decision is not None
    assert state.current.last_decision.outcome in html
    assert "秒" in html


def test_idle_selection_changes_the_single_main_graph() -> None:
    html = render_page(
        make_demo_state(running=False),
        form_token="token",
        selected=ScenarioCode.F3,
    )

    assert html.count("<svg") == 1
    assert '<h2 id="flow-title"><span>F3</span>' in html
    assert 'class="scenario-row is-selected"' in html
    assert "舊建議被收件檢查攔下，沒有第二次寫入" in html
    assert 'href="?scenario=F3#flow"' in html


def test_running_scenario_overrides_an_idle_selection() -> None:
    html = render_page(
        make_demo_state(running=True),
        form_token="token",
        selected=ScenarioCode.F3,
    )

    assert '<h2 id="flow-title"><span>F2</span>' in html
    assert 'class="flow-node is-current' in html


def test_flow_uses_horizontal_swimlanes_and_svg_coordinates_stay_inside_the_viewbox() -> None:
    html = render_page(
        make_demo_state(running=False),
        form_token="token",
        selected=ScenarioCode.F3,
    )
    svg = re.search(r'<svg class="flow-graph".*?</svg>', html, re.DOTALL)

    assert svg is not None
    markup = svg.group(0)
    viewbox = re.search(r'viewBox="0 0 (\d+) (\d+)"', markup)
    assert viewbox is not None
    width, height = (int(value) for value in viewbox.groups())
    assert width <= 1440
    assert height <= 800
    assert markup.count('class="lane-band"') == 2
    assert '<div class="flow-lanes" aria-label="處理角色">' in html
    assert re.findall(r'<div class="flow-lane">([^<]+)</div>', html) == ["分析", "收件"]
    root = ET.fromstring(markup)  # noqa: S314 - renderer output, not untrusted XML
    bands = root.findall('.//g[@class="lane-band"]')
    assert all(band.find("text") is None for band in bands)
    band_ys = [_rect_coordinate(band, "y") for band in bands]
    assert band_ys == sorted(band_ys)
    nodes = root.findall('.//g[@class]')
    node_xs = [
        _rect_coordinate(group, "x")
        for group in nodes if "flow-node" in group.get("class", "")
    ]
    assert node_xs == sorted(node_xs)
    assert min(b - a for a, b in pairwise(node_xs)) >= 97
    assert node_xs[-1] + 94 <= width
    assert 'class="flow-edge is-taken"' in markup
    coordinates = re.findall(r'<text(?: class="[^"]+")? x="(-?\d+)" y="(-?\d+)"', markup)
    assert coordinates
    assert all(0 <= int(x) <= width and 0 <= int(y) <= height for x, y in coordinates)


def _rect_coordinate(group: ET.Element, coordinate: str) -> int:
    rect = group.find("rect")
    assert rect is not None
    return int(rect.get(coordinate, "0"))


def test_analysis_only_flow_hides_later_lanes_and_marks_unentered_stages() -> None:
    state = make_demo_state(running=False)
    scenario = state.scenarios[0]
    cut = replace(scenario, path=tuple(d for d in scenario.path if d.node.startswith("a_")),
                  traversed_edges=tuple(edge for edge in scenario.traversed_edges
                                        if edge[0].startswith("a_") and edge[1].startswith("a_")))
    state = replace(state, scenarios=(cut, *state.scenarios[1:]))
    page = render_page(state, form_token="t", selected=scenario.code, refresh_tick=0)
    markup = re.search(r'<svg class="flow-graph".*?</svg>', page, re.DOTALL)
    assert markup is not None
    assert 'viewBox="0 0' in markup.group(0)
    assert markup.group(0).count('class="lane-band"') == 1
    assert re.findall(r'<div class="flow-lane">([^<]+)</div>', page) == ["分析"]
    assert '收件、執行、廣告平台、人工這次沒有走到' in page
    assert 'height="240"' in markup.group(0)


def test_mobile_layout_contains_long_values_inside_cards_and_scrolls_only_the_flow() -> None:
    css = DEMO_CSS
    assert '.decision-hero__grid' in css and 'minmax(0,1fr)' in css
    assert '.ai-round-card__fields' in css and 'overflow-wrap:anywhere' in css
    assert re.search(r'@media\s*\(max-width:\s*760px\).*?\.decision-hero__grid[^}]*'
                     r'grid-template-columns:\s*1fr', css, re.DOTALL)
    assert re.search(r'@media\s*\(max-width:\s*760px\).*?\.ai-round-card__fields[^}]*'
                     r'grid-template-columns:\s*1fr', css, re.DOTALL)
    assert '.diagram-frame .flow-scroll { overflow-x:auto; }' in css
    assert 'min-width:0' in css
    assert '.ai-banner strong { color: #74428e; }' not in css
    assert '.ai-node-card { border-left:2px solid #af9ccf' not in css


def test_unreached_fault_keeps_its_fixed_label_with_plain_explanation() -> None:
    state = make_demo_state(running=False)
    scenario = replace(state.scenarios[1], status=ScenarioStatus.NOT_EXERCISED)
    state = replace(state, scenarios=(state.scenarios[0], scenario, *state.scenarios[2:]))
    page = render_page(state, form_token="t", selected=ScenarioCode.F2)
    # 固定標示照狀態顯示(「提案」緊接括號解釋);決策摘要不再另寫一次裸詞版本(增量 4 代碼審 r2 v1)
    assert ScenarioStatus.NOT_EXERCISED.value in html_lib.unescape(page)
    assert "AI 判不提案,故障處理這次沒有走到" not in page
    assert not _unexplained(_visible_text_outside_verbatim(page), "提案")


def test_every_scenario_flow_moves_right_and_decision_numbers_match_cards() -> None:
    state = make_demo_state()
    report = render_report(state)
    for scenario, svg in zip(
        state.scenarios,
        re.findall(r'<svg class="flow-graph".*?</svg>', report, re.DOTALL),
        strict=True,
    ):
        root = ET.fromstring(svg)  # noqa: S314 - renderer output, not untrusted XML
        width, height = (int(value) for value in root.get("viewBox", "").split()[2:])
        assert width <= 3500
        assert height <= 800
        nodes = [
            group for group in root.findall('.//g[@class]')
            if "flow-node" in group.get("class", "")
        ]
        node_xs = [_rect_coordinate(group, "x") for group in nodes]
        assert node_xs == sorted(node_xs)
        grouped = [group for group in nodes if "is-group" in group.get("class", "")]
        assert grouped
        assert all("點選看判斷" in "".join(group.itertext()) for group in grouped)
        assert len(node_xs) < len(scenario.traversed_edges) + 1
        badges = [group.find('text[@class="decision-badge"]') for group in nodes]
        numbers = [badge.text for badge in badges if badge is not None]
        covered: list[int] = []
        for badge in numbers:
            assert badge is not None
            range_match = re.fullmatch(r"含判斷 (\d+)–(\d+)", badge)
            if range_match:
                first, last = map(int, range_match.groups())
                covered.extend(range(first, last + 1))
            else:
                covered.append(int(re.match(r"判斷 (\d+)", badge).group(1)))  # 可能帶「・經 N」
        assert covered == list(range(1, len(scenario.path) + 1))
        for group in nodes:
            lines = group.findall('text[@class="node-label"]/tspan')
            assert "".join(line.text or "" for line in lines)
        for group in root.findall('.//g[@class="flow-edge is-taken"]'):
            path = group.find("path")
            assert path is not None
            points = [int(value) for value in re.findall(r"\d+", path.get("d", ""))]
            assert points[0] < points[-2]


def _text_width(value: str, size: int) -> float:
    return sum(
        size if unicodedata.east_asian_width(char) in {"F", "W"} else size * 0.6
        for char in value
    )


def _visible_text_boxes(element: ET.Element) -> list[tuple[float, float, float, float]]:
    boxes = []
    items = [element] if element.tag == "text" else element.findall("text")
    for item in items:
        css_class = item.get("class", "")
        size = 12
        lines = item.findall("tspan") or [item]
        for line in lines:
            title = line.find("title")
            value = line.text or (title.tail if title is not None else "") or ""
            x = float(line.get("x", item.get("x", "0")))
            y = float(line.get("y", item.get("y", "0")))
            width = _text_width(value, size)
            if css_class in {"decision-badge", "node-owner"}:
                boxes.append((x, y - size, x + width, y + size * 0.2))
            else:
                boxes.append((x - width / 2, y - size, x + width / 2, y + size * 0.2))
    return boxes


def _overlaps(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return (
        first[0] < second[2]
        and first[2] > second[0]
        and first[1] < second[3]
        and first[3] > second[1]
    )


def test_all_flow_text_fits_its_shape_or_clear_space() -> None:  # noqa: C901, PLR0912, PLR0915
    report = render_report(make_demo_state())
    for svg in re.findall(r'<svg class="flow-graph".*?</svg>', report, re.DOTALL):
        root = ET.fromstring(svg)  # noqa: S314 - renderer output, not untrusted XML
        node_shapes = []
        for group in root.findall('.//g[@class]'):
            if "flow-node" not in group.get("class", ""):
                continue
            rect = group.find("rect")
            polygon = group.find("polygon")
            if rect is not None:
                x, y = float(rect.get("x", "0")), float(rect.get("y", "0"))
                width = float(rect.get("width", "0"))
                height = float(rect.get("height", "0"))
            else:
                assert polygon is not None
                points = [
                    tuple(map(float, pair.split(",")))
                    for pair in polygon.get("points", "").split()
                ]
                x, y = min(pair[0] for pair in points), min(pair[1] for pair in points)
                width = max(pair[0] for pair in points) - x
                height = max(pair[1] for pair in points) - y
            node_shapes.append((group, (x, y, x + width, y + height), polygon is not None))

        for group, shape, diamond in node_shapes:
            for text in group.findall("text"):
                text_class = text.get("class", "")
                for box in _visible_text_boxes(text):
                    if text_class != "decision-badge":
                        assert shape[0] + 8 <= box[0] < box[2] <= shape[2] - 8
                        assert shape[1] + 8 <= box[1] < box[3] <= shape[3] - 8
                        if diamond:
                            for height in (box[1], box[3]):
                                half_width = (shape[2] - shape[0]) / 2 * (
                                    1 - abs(height - (shape[1] + shape[3]) / 2)
                                    / ((shape[3] - shape[1]) / 2)
                                )
                                assert box[0] >= (shape[0] + shape[2]) / 2 - half_width + 3
                                assert box[2] <= (shape[0] + shape[2]) / 2 + half_width - 3
                    for other_group, other_shape, _ in node_shapes:
                        if other_group is not group:
                            assert not _overlaps(box, other_shape)

        for group in root.findall('.//g[@class]'):
            if not any(name in group.get("class", "") for name in ("flow-edge", "flow-branch")):
                continue
            rect = group.find("rect")
            if rect is None:
                continue
            x, y = float(rect.get("x", "0")), float(rect.get("y", "0"))
            width, height = float(rect.get("width", "0")), float(rect.get("height", "0"))
            for box in _visible_text_boxes(group):
                assert x + 4 <= box[0] < box[2] <= x + width - 4
                assert y + 3 <= box[1] < box[3] <= y + height - 3
                assert all(not _overlaps(box, shape) for _, shape, _ in node_shapes)


def test_back_arrows_return_to_formal_target_outside_the_lanes() -> None:
    from rtb.demo.flow import BACK_TRANSITIONS

    state = make_demo_state()
    svgs = re.findall(r'<svg class="flow-graph".*?</svg>', render_report(state), re.DOTALL)
    backs = {item.node: item for item in BACK_TRANSITIONS}
    for scenario, svg in zip(state.scenarios, svgs, strict=True):
        root = ET.fromstring(svg)  # noqa: S314 - renderer output, not untrusted XML
        final = scenario.traversed_edges[-1][1]
        returned = root.find('.//g[@class="flow-return"]')
        if final not in backs:
            assert returned is None
            continue
        assert returned is not None
        path = returned.find("path")
        assert path is not None and "stroke" not in path.attrib
        coordinates = [int(value) for value in re.findall(r"\d+", path.get("d", ""))]
        height = int(root.get("viewBox", "").split()[3])
        assert coordinates[3] in {24, height - 25}
        assert coordinates[5] in {24, height - 25}
        label = returned.find("text")
        assert label is not None and label.text is not None
        assert label.text.startswith("回到：")
        if backs[final].returns_to.startswith("a_"):
            assert "分析群組" in label.text
        if scenario.code in {ScenarioCode.F4, ScenarioCode.F6, ScenarioCode.F7}:
            assert returned is not None


def test_owner_palette_has_labels_shapes_and_aa_text_contrast() -> None:
    from rtb.demo.flow import FLOW_GRAPH
    from rtb.demo.flow_svg import SHORT_LABELS

    css = DEMO_CSS  # 樣式表跟頁面同一份(不靠工作目錄的相對路徑;代碼審 r1 t9)
    assert {node.id for node in FLOW_GRAPH.nodes} <= set(SHORT_LABELS)
    fills_by_mode: list[dict[str, str]] = [{}, {}]
    for owner in ("code", "ai", "human", "external"):
        palettes = re.findall(
            rf"\.flow-node\.owner-{owner}\s*\{{[^}}]*--owner-fill:\s*(#[0-9a-f]{{6}});"
            rf"[^}}]*--owner-stroke:\s*#[0-9a-f]{{6}};"
            rf"[^}}]*--owner-text:\s*(#[0-9a-f]{{6}});",
            css,
        )
        assert len(palettes) == 2
        for mode, (fill, text) in enumerate(palettes):
            assert _contrast(fill, text) >= 4.5
            fills_by_mode[mode][owner] = fill
        assert f'owner-{owner}' in render_report(make_demo_state()) or owner == "ai"
        legend = re.findall(rf"\.legend-{owner}\s*\{{[^}}]*background:\s*(#[0-9a-f]{{6}});", css)
        assert legend == [pair[0] for pair in palettes]
    assert all(len(set(fills.values())) == 4 for fills in fills_by_mode)
    assert '.flow-node.owner-ai rect { stroke-dasharray:' in css
    assert '.flow-node.owner-external rect { stroke-dasharray:' in css
    assert '.flow-node .group-ai-stripe' not in css
    assert 'fill: white !important' not in css


def test_each_sample_keeps_ai_and_human_steps_as_separate_coloured_nodes() -> None:
    from rtb.demo.flow import FLOW_GRAPH
    from rtb.demo.state import NodeOwner

    state = make_demo_state()
    official = {node.id: node for node in FLOW_GRAPH.nodes}
    svgs = re.findall(r'<svg class="flow-graph".*?</svg>', render_report(state), re.DOTALL)
    for scenario, svg in zip(state.scenarios, svgs, strict=True):
        root = ET.fromstring(svg)  # noqa: S314 - renderer output, not untrusted XML
        drawn = [group for group in root.findall('.//g[@class]')
                 if "flow-node" in group.get("class", "")]
        routed_ids = {item for edge in scenario.traversed_edges for item in edge}
        # 說明是送出之後的旁支(Issues/流程圖把AI說明畫在送出建議之前),走過的路上沒有 AI 節點;
        # Phase 10 候選節點已撤。F1 畫得出的 AI 格只有告警原因推測
        expected_ai_ids: set[str] = set()
        expected_human_ids = (
            {"h_replay", "r_requeued"} if scenario.code is ScenarioCode.F6 else
            {"h_approve"} if scenario.code is ScenarioCode.F7 else set()
        )
        assert {node_id for node_id in routed_ids
                if official[node_id].owner is NodeOwner.AI} == expected_ai_ids
        assert {node_id for node_id in routed_ids
                if official[node_id].owner is NodeOwner.HUMAN} == expected_human_ids
        for owner in (NodeOwner.AI, NodeOwner.HUMAN):
            expected = {official[node_id].label.replace("模型", "AI") for node_id in routed_ids
                        if official[node_id].owner is owner}
            shown = {group.findtext("title") for group in drawn
                     if f"owner-{owner.name.lower()}" in group.get("class", "")}
            assert expected <= shown, (scenario.code, owner, expected - shown)
            assert all("is-group" not in group.get("class", "") for group in drawn
                       if f"owner-{owner.name.lower()}" in group.get("class", ""))
        for group in drawn:
            if "is-group" in group.get("class", ""):
                assert "owner-code" in group.get("class", "")
                assert "AI" not in (group.findtext("title") or "")
        ai_nodes = [group for group in drawn if "owner-ai" in group.get("class", "")]
        assert all(group.find('text[@class="node-owner"]') is None for group in drawn)
        if scenario.code is ScenarioCode.F1:
            assert {group.findtext("title") for group in ai_nodes} == {
                "AI 推測可能原因(只供參考)"}

    f7 = state.scenarios[-1]
    assert f7.code is ScenarioCode.F7
    assert f7.traversed_edges[-3:] == (
        ("x_total", "x_wait_approval"),
        ("x_wait_approval", "h_approve"),
        ("h_approve", "x_approved"),
    )


def test_example_scenarios_have_seven_distinct_routes_and_human_results() -> None:
    state = make_demo_state(running=False)

    assert len({scenario.traversed_edges for scenario in state.scenarios}) == 7
    assert all(scenario.result_summary.strip() for scenario in state.scenarios)
    report = render_report(state)
    assert report.count('<svg class="flow-graph"') == 7
    assert "客戶帳戶每日" not in report
    for scenario in state.scenarios:
        assert f'aria-label="{scenario.code.value} 處理流程圖"' in report
        for decision in scenario.path:
            assert all(escape_text(item.observed) in report for item in decision.basis)
    for fragment in ("onerror=boom", "不執行", "不是標籤", "https://evil.test"):
        assert fragment not in report


def test_no_current_means_no_progress_bar_even_when_running() -> None:
    html = render_page(replace(make_demo_state(running=True), current=None), form_token="token")

    assert 'class="current-progress"' not in html


def test_model_mode_cost_progress_and_utc_are_visible_in_summary() -> None:
    state = replace(
        make_demo_state(),
        model_mode=ModelMode.RECORDED,
        model_cost_usd=Decimal("0.42"),
        is_sample=False,
    )
    html = render_page(state, form_token="token")

    assert "AI 說明方式" not in html and "錄製回應（沒有即時連線）" not in html
    assert "0.42 美元" in html
    assert "沒有開即時開關" not in html
    assert "上次完整執行 AI 費用" in html
    assert "UTC" in html
    assert "7 / 7" in html


def test_page_hides_model_source_embedded_in_outcome_and_progress_text() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    for mode_source in ("錄製回應", "即時呼叫"):
        note = f"AI 的判斷:值得加預算。理由(AI 產生、僅供參考,{mode_source}):依收據判斷"
        changed = replace(first, outcome_note=note)
        shown = replace(state, scenarios=(changed, *state.scenarios[1:]))
        for page in (render_page(shown, form_token="t", selected=ScenarioCode.F1),
                     render_report(shown)):
            assert mode_source not in page
            assert "理由(AI 產生、僅供參考):依收據判斷" in page
    running = make_demo_state(running=True)
    assert running.current is not None and running.current.last_decision is not None
    current = replace(running.current, last_decision=replace(
        running.current.last_decision, outcome="沒有對應的錄製回應"))
    live_page = render_page(replace(running, current=current), form_token="t")
    assert "AI 這次沒有給出回答" in live_page and "錄製回應" not in live_page


def test_current_step_accepts_timezone_aware_timestamp() -> None:
    current = CurrentStep(
        scenario=make_demo_state().scenarios[0].code,
        node="a_collect",
        started_at=datetime.now(UTC) - timedelta(seconds=3),
        last_decision=Decision(
            node="a_fresh",
            taken_edge=("a_fresh", "a_complete"),
            outcome="是",
            reason="證據夠新",
            at=None,
        ),
    )

    assert current.started_at.tzinfo is UTC


def _visible_text_without_glossary(markup: str) -> str:
    without_glossary = re.sub(
        r'<dl class="glossary">.*?</dl>',
        "",
        markup,
        flags=re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_glossary)
    return re.sub(r"\s+", " ", html_lib.unescape(without_tags)).strip()


def _visible_text_outside_verbatim(markup: str) -> str:
    """去掉驗證器原樣輸出那幾行(機器輸出照原樣顯示,不改寫)之後看得到的文字。"""
    return _visible_text_without_glossary(
        re.sub(r'<p class="raw-line">.*?</p>', "", markup, flags=re.DOTALL))


def _unexplained(text: str, term: str) -> bool:
    if term == "模型":
        text = text.replace("模型考題沒通過 （AI 回答的考題未過，與情境是否完成分開看）", "")
    return term in re.sub(rf"{re.escape(term)}（[^（）]+）", "", text)


def test_example_pages_explain_or_confine_internal_jargon_to_the_glossary() -> None:
    """主頁沒有名詞小辭典,專有名詞要緊接括號白話解釋;報告裡留下的專有名詞都在小辭典裡。"""
    state = make_demo_state(running=False)
    for markup in (render_page(make_demo_state(running=True), form_token="token"),
                   render_page(state, form_token="token", selected=ScenarioCode.F3)):
        assert '<dl class="glossary">' not in markup
        text = _visible_text_outside_verbatim(markup)
        for term in JARGON_TERMS:
            assert not _unexplained(text, term), f"{term!r} 在主頁要緊接一段括號白話解釋"


def test_the_glossary_explains_every_kept_term() -> None:
    """[S1043] 報告底部有名詞小辭典;報告上留下來(沒有緊接括號解釋)的每一個專有名詞,小辭典都有一句
    白話解釋。"""
    report = render_report(make_demo_state(running=False))
    glossary = re.search(r'<dl class="glossary">(.*?)</dl>', report, re.DOTALL)
    assert glossary is not None
    explained = {html_lib.unescape(term) for term in re.findall(r"<dt>(.*?)</dt>",
                                                                  glossary.group(1))}
    text = _visible_text_without_glossary(report)
    kept = {term for term in JARGON_TERMS if _unexplained(text, term)}
    assert kept <= explained, kept - explained
    assert set(JARGON_TERMS) <= explained  # 禁用清單上的詞一律有解釋,之後新出現在頁面上也不會漏


def test_formal_flow_map_and_every_sample_route_match() -> None:
    from rtb.demo.flow import FLOW_GRAPH, LANES

    state = make_demo_state()
    assert state.flow is FLOW_GRAPH
    # Phase 13 增量 2 加的 AI 選下一步、AI 要再查(回頭)、只判不送三個節點與六條邊,Phase 14 增量 3
    # 撤除:51 → 48、78 → 72(代碼審 r1 d2/d3 修過流程圖的邊:68 → 72)。2026-09-27 撤 Phase 10 候選
    # 分支(交給誰判斷、模型候選兩個節點,五條邊換成「偏慢 → 程式規則」一條):48 → 46、72 → 68。
    # 2026-09-28 說明改成送出之後的旁支(寫好建議 → 說明 → 送出、寫好建議 → 送出兩條路併成
    # 寫好建議 → 送出,另加送出 → 說明):68 → 67
    assert len(state.flow.nodes) == 46
    assert len(state.flow.edges) == 67
    markup = render_page(state, form_token="token")
    assert markup.count('class="role-card"') == len(LANES)
    assert "分析行程提出建議" in markup
    edge_pairs = {(edge.source, edge.target) for edge in FLOW_GRAPH.edges}
    decision_nodes = {node.id for node in FLOW_GRAPH.nodes if node.kind.name == "DECISION"}
    for scenario in state.scenarios:
        assert scenario.traversed_edges
        assert all(pair in edge_pairs for pair in scenario.traversed_edges)
        assert all(a[1] == b[0] for a, b in pairwise(scenario.traversed_edges))
        assert {item.node for item in scenario.path} == {
            pair[0] for pair in scenario.traversed_edges if pair[0] in decision_nodes
        }


def test_renderer_rejects_a_route_that_does_not_exist_in_the_formal_graph() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    broken = replace(
        first, traversed_edges=(*first.traversed_edges[:-1], ("x_verify", "a_receive"))
    )
    with pytest.raises(ValueError, match="不存在"):
        render_page(replace(state, scenarios=(broken, *state.scenarios[1:])), form_token="token")


# ---- 後端資料介面對齊(2b):無幣別金額、F7 彙總、根據來源、對不到的邊留空 ----
def _with_first(state, **changes):
    first = replace(state.scenarios[0], **changes)
    return replace(state, scenarios=(first, *state.scenarios[1:]))


def test_budget_is_shown_as_the_platform_integer_without_a_currency() -> None:
    report = render_report(make_demo_state())
    summary = report.split("最後改了什麼", 1)[1].split("</div>", 1)[0]
    assert "元" not in summary and "分" not in summary
    assert "（平台預算單位）" in summary


def test_a_multi_campaign_overview_is_shown_above_the_single_change() -> None:
    overview = "放行 123 個各加一成、人工確認後寫入 1 個、沒寫入 176 個;總額 1233/1234"
    report = render_report(_with_first(make_demo_state(), change_overview=overview))
    assert overview in report
    assert report.index(overview) < report.index("廣告：", report.index(overview))


def test_each_basis_shows_where_it_came_from() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    decision = replace(first.path[0], basis=(DecisionBasis("3 分鐘前", "上限 15 分鐘", "夠新",
                                                           source="依存下的證據重算"),))
    report = render_report(_with_first(state, path=(decision, *first.path[1:])))
    assert "依存下的證據重算" in report


def test_a_decision_without_a_matching_edge_is_shown_not_dropped() -> None:
    state = make_demo_state()
    first = state.scenarios[0]
    decision = replace(first.path[-1], taken_edge=None)
    report = render_report(_with_first(state, path=(*first.path[:-1], decision)))
    assert report.count('class="decision-card') >= len(first.path)


def test_a_broken_or_segmented_path_is_drawn_as_it_happened() -> None:
    """真資料的路徑可以斷開、分好幾段(回頭之後、另一件工作),頁面照實畫,不要求接得起來。"""
    state = make_demo_state()
    first = state.scenarios[0]
    pairs = first.traversed_edges
    segmented = (pairs[0], *pairs[2:])  # 拿掉中間一段,兩段接不起來
    report = render_report(_with_first(state, traversed_edges=segmented))
    assert "情境路徑的步驟沒有接起來" not in report
    assert first.title in report


def test_no_recorded_path_means_no_guessed_route() -> None:
    """沒有走過的邊、也沒有判斷紀錄時,不從圖的第一個節點替它猜一條路。"""
    report = render_report(_with_first(make_demo_state(), traversed_edges=(), path=(),
                                       current_node=None))
    assert "這個情境還沒有走過的路徑紀錄" in report


def test_a_progress_step_does_not_show_an_empty_basis() -> None:
    """狀態往前走的步驟不列根據欄,不再顯示「沒有留下數字根據」;真的判斷照舊顯示。"""
    state = make_demo_state()
    first = state.scenarios[0]
    steps = tuple(replace(item, basis=(), kind=DecisionKind.PROGRESS) for item in first.path)
    report = render_report(_with_first(state, path=steps))
    section = report.split(first.title, 1)[1].split("</article>", 1)[0]
    assert "這一步沒有留下數字根據" not in section
    assert "狀態前進" in section
    judged = tuple(replace(item, basis=(), kind=DecisionKind.JUDGEMENT) for item in first.path)
    report = render_report(_with_first(state, path=judged))
    section = report.split(first.title, 1)[1].split("</article>", 1)[0]
    assert "這一步沒有留下數字根據" in section


# ---- 代碼審 r1(Phase 12 增量 2)----
def test_every_scenario_shows_where_its_result_came_from() -> None:
    """[S1054][S1012] 主頁與報告逐情境顯示出處:展示編號、時間,重跑的標明取自單一情境重跑;
    F7 註明規模縮小;沒跑過的照實寫;自動查核那一格沒有完整執行過時照實寫。"""
    state = replace(make_demo_state(), is_sample=False, full_demo_id="2026-09-24-001")
    f3 = state.scenarios[2]
    rerun = replace(f3, source_demo_id="2026-09-25-rerun", source_full=False)
    unrun = replace(state.scenarios[3], source_demo_id=None, ran_at=None,
                    status=ScenarioStatus.PENDING)
    shown = replace(state, scenarios=(*state.scenarios[:2], rerun, unrun,
                                      *state.scenarios[4:]))
    report = render_report(shown)
    for scenario in shown.scenarios[:3] + shown.scenarios[4:]:
        assert f"情境執行編號 {scenario.source_demo_id}-{scenario.code.value}" in report
        assert "執行時間" in report
    assert "取自單一情境重跑（展示編號 2026-09-25-rerun）" in report
    assert "AI 採錄製回應" not in report and "AI 說明方式" not in report
    assert "這個情境還沒有執行紀錄" in report
    f7 = render_page(shown, form_token="token", selected=ScenarioCode.F7)
    assert "規模縮小" in f7 and "完整規模由自動查核跑的 F7 測試證明" in f7
    assert "取自單一情境重跑" in render_page(
        shown, form_token="token", selected=ScenarioCode.F3
    )  # 選中情境的其餘明細仍可追查出處
    never = replace(shown, verifier=None, full_demo_id=None)
    assert "還沒有完整執行過" in _verifier_cell(render_page(never, form_token="token"))


def test_the_page_and_stylesheet_load_nothing_external() -> None:
    """[S1036] 頁面、報告與樣式表都不請求外部資源:沒有外部網址、@import、外部字型、url()。"""
    state = make_demo_state()
    for markup in (render_page(state, form_token="token"), render_report(state),
                   render_report(state, inline_styles=False)):
        tags = "\n".join(re.findall(r"<[^>]+>", markup))
        assert re.search(r'(?:href|src|action)="(?:[a-z]+:)?//', tags, re.IGNORECASE) is None
        assert "@import" not in markup
        assert re.search(r"url\((?!#)", markup) is None  # 只准指到頁面自己的箭頭標記
    assert "@import" not in DEMO_CSS and "url(" not in DEMO_CSS and "@font-face" not in DEMO_CSS


def test_the_report_shows_every_scenario_path_and_decisions() -> None:
    """[S1040] 報告有七個情境代碼,每個情境各自一張路徑圖與一份判斷紀錄(卡片數等於判斷數)。"""
    state = make_demo_state()
    report = render_report(state)
    for scenario in state.scenarios:
        code = scenario.code.value
        section = report.split(f'id="flow-{code}"', 1)[1].split('class="report-scenario"', 1)[0]
        assert f'aria-label="{code} 處理流程圖"' in section
        assert section.count('class="decision-card') == len(scenario.path)


HOSTILE = '"><script>alert(1)</script><img src=x onerror=boom>&amp;‮'


def test_untrusted_text_never_becomes_markup() -> None:
    """[S1022] 廣告名稱、判斷結果、確認頁數字、驗證器輸出與原因裡的腳本標籤、事件屬性、實體編碼
    與雙向控制字元,都以看得見的文字出現,不形成標籤或屬性。"""
    state = replace(make_demo_state(), is_sample=False, full_demo_id="x")
    first = state.scenarios[0]
    change = first.change_summary
    assert change is not None and state.verifier is not None
    path = (replace(first.path[0], outcome=HOSTILE), *first.path[1:])
    hostile = replace(
        state,
        scenarios=(replace(first, change_summary=replace(change, campaign=HOSTILE), path=path),
                   *state.scenarios[1:]),
        verifier=replace(state.verifier, passed=False, lines=(HOSTILE,), reasons=(HOSTILE,)),
        approval=ApprovalForm("h" * 64, ((HOSTILE, HOSTILE),), None, None, "d", "n" * 64),
    )
    pages = (render_page(hostile, form_token="token", selected=ScenarioCode.F1),
             render_report(hostile), render_approval(hostile, form_token="token"))
    for markup in pages:
        assert markup.count("<script>") == (0 if '<main class="approval-page">' in markup else 1)
        assert "<script>" not in markup.replace(f"<script>{FLOW_SCRIPT}</script>", "")
        assert "<img" not in markup
        assert "〔U+202E〕" in markup and "&amp;amp;" in markup
        assert escape_text(HOSTILE) in markup


def test_the_scenario_list_is_pinned_in_both_two_column_workspaces() -> None:
    """桌面報告與互動頁都把清單釘在左欄,窄畫面則取消 sticky。"""
    rules = re.findall(r"([^{}]+)\{[^}]*position:\s*sticky[^}]*\}", DEMO_CSS)
    pinned = [selector.strip() for selector in rules if "scenario-section" in selector]
    assert pinned == [".report-workspace .scenario-section",
                      ".demo-workspace .scenario-section"]
    assert 'class="demo-workspace"' in render_page(make_demo_state(), form_token="t")


def test_results_are_not_invented_for_scenarios_that_have_not_finished() -> None:
    """[代碼審 r1 p4] 還沒跑的寫尚未執行、跑到一半的寫執行中,不拿情境說明或最後一步頂替成結果。"""
    state = make_demo_state()
    first = state.scenarios[0]
    for status, text in ((ScenarioStatus.PENDING, "尚未執行"),
                         (ScenarioStatus.RUNNING, "執行中，還沒有結果"),
                         (ScenarioStatus.AWAITING_APPROVAL, "執行中，還沒有結果")):
        shown = replace(state, scenarios=(replace(first, status=status, result_summary=""),
                                          *state.scenarios[1:]))
        markup = render_page(shown, form_token="t", selected=ScenarioCode.F1)
        assert f"這次結果：</strong><span>{text}</span>" in markup
        assert escape_text(first.path[-1].outcome) not in markup.split("這次結果：", 1)[1][:200]


def test_progress_is_shown_only_while_running_and_carries_the_current_anchor() -> None:
    """[代碼審 r1 p7/s2/v6] 沒在跑就沒有「現在進度」;在跑時那一塊帶 id="current"(POST 轉址
    對到它)。"""
    running = make_demo_state(running=True)
    assert 'id="current"' in render_page(running, form_token="t")
    idle = replace(running, running=False)
    assert 'class="current-progress"' not in render_page(idle, form_token="t")


def test_plain_wording_for_normal_situations() -> None:
    """[代碼審 r1 p9/p6] 沒安排故障、結束點、還沒有展示、沒有呼叫 AI:照實寫,不像缺資料或出錯。"""
    state = replace(make_demo_state(), is_sample=False, model_mode=ModelMode.NOT_CALLED,
                    started_at=None, demo_id="")
    first = replace(state.scenarios[0], injected_faults=())
    others = tuple(replace(item, model_mode=ModelMode.NOT_CALLED) for item in state.scenarios[1:])
    # Phase 13 增量 4:說明卡不再只在走過「寫說明」節點時才顯示,沒呼叫 AI 的情境也就沒有說明與推測
    first = replace(first, model_mode=ModelMode.NOT_CALLED, model_step=None, hypothesis=None)
    markup = render_page(replace(state, scenarios=(first, *others)),
                         form_token="t", selected=ScenarioCode.F1)
    assert "這個情境沒有安排故障" in markup and "(這次沒有記錄)</p>" not in markup.split(
        "fault-note", 1)[1][:80]
    assert "<dt>開始時間</dt><dd>—</dd>" in markup and "<dd>none</dd>" not in markup
    assert "AI 說明方式" not in markup and "錄製" not in markup
    running = make_demo_state(running=True)
    assert running.current is not None
    ended = replace(running, current=replace(running.current, last_decision=Decision(
        node="x_done", taken_edge=None, outcome="完成", reason="", at=None)))
    assert "（已結束）" in render_page(ended, form_token="t")


def _node_boxes(svg: str) -> dict[str, tuple[int, int]]:
    """流程圖上每個節點的標題 → (x, y)。"""
    root = ET.fromstring(svg)  # noqa: S314 - 渲染器自己的輸出
    found = {}
    for group in root.findall(".//g[@class]"):
        if "flow-node" in group.get("class", ""):
            title = group.find("title")
            found[(title.text or "") if title is not None else ""] = (
                _rect_coordinate(group, "x"), _rect_coordinate(group, "y"))
    return found


def _svg(markup: str) -> str:
    match = re.search(r'<svg class="flow-graph".*?</svg>', markup, re.DOTALL)
    assert match is not None
    return match.group(0)


def _step(node: str, edge: tuple[str, str] | None) -> Decision:
    return Decision(node=node, taken_edge=edge, outcome="o", reason="r", at=None)


def test_a_node_without_its_incoming_edge_is_drawn_at_its_time_position() -> None:
    """[代碼審 r1 p5] 節點照第一次出現的時間排:沒記到進來那條邊的判斷點插在它自己的時間位置,
    不追加到最右邊。"""
    from rtb.demo.flow import FLOW_GRAPH

    labels = {node.id: node.label for node in FLOW_GRAPH.nodes}
    path = (_step("x_pick", ("x_pick", "x_precheck")), _step("x_reclaimed", None),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_guard", ("x_guard", "x_total")))
    state = make_demo_state()
    first = replace(state.scenarios[0], path=path,
                    traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
    boxes = _node_boxes(_svg(render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                                         form_token="t", selected=ScenarioCode.F1)))
    xs = {node: boxes[labels[node]][0] for node in ("x_precheck", "x_reclaimed", "x_guard")}
    assert xs["x_precheck"] < xs["x_reclaimed"] < xs["x_guard"]


def test_node_numbers_point_at_the_first_card_for_that_node() -> None:
    """[代碼審 r1 p5] 同一個節點走過好幾次:節點上的「判斷 N」是它第一次出現的那張卡。"""
    path = (_step("x_pick", ("x_pick", "x_precheck")),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_guard", ("x_guard", "x_total")), _step("x_pick", ("x_pick", "x_precheck")))
    state = make_demo_state()
    first = replace(state.scenarios[0], path=path,
                    traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
    svg = _svg(render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                           form_token="t", selected=ScenarioCode.F1))
    badges = re.findall(r'class="decision-badge"[^>]*>判斷 (\d+)<', svg)
    assert badges == ["1", "2", "3"]


def test_a_line_that_skips_nodes_in_its_lane_goes_above_them() -> None:
    """[代碼審 r1 p5] 同一泳道要跨過其他節點的線,走節點列上方的空隙、畫成虛線,不從節點底下穿過。"""
    path = (_step("x_pick", ("x_pick", "x_precheck")),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_guard", ("x_guard", "x_total")), _step("x_pick", ("x_pick", "x_deadletter")))
    state = make_demo_state()
    first = replace(state.scenarios[0], path=path,
                    traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
    svg = _svg(render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                           form_token="t", selected=ScenarioCode.F1))
    skips = re.findall(r'<g class="flow-edge is-taken is-skip"><title>([^<]*)</title>'
                       r'<path d="([^"]+)"', svg)
    assert skips and "跳過中間" in skips[0][0]
    from rtb.demo.flow import FLOW_GRAPH

    lane_top = _node_boxes(svg)[next(n.label for n in FLOW_GRAPH.nodes if n.id == "x_pick")][1]
    gap = int(re.findall(r"-?\d+", skips[0][1])[3])  # 第二個點的 y:上方空隙那一段
    assert lane_top - 30 < gap < lane_top  # 在這一列節點的上緣之上、泳道之內


def test_many_decisions_are_summarised_and_each_edge_is_drawn_once() -> None:
    """[代碼審 r1 p8] 幾千筆判斷(F7):走過的邊依(起點, 終點)只畫一次,沒走的分支只列一次,判斷卡依
    節點與分支彙總並標筆數;頁面不會隨筆數長大。"""
    loop = (_step("x_pick", ("x_pick", "x_precheck")),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_guard", ("x_guard", "x_total")), _step("x_total", ("x_total", "x_write")))
    state = make_demo_state()

    def page(times: int) -> str:
        path = loop * times
        first = replace(state.scenarios[0], path=path,
                        traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
        return render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                           form_token="t", selected=ScenarioCode.F1)

    big = page(1000)
    assert big.count('class="flow-edge is-taken') == 4
    assert big.count('class="decision-card') == 4 and "（共 1000 筆）" in big
    assert len(big) < 2 * len(page(20))
    routes = re.findall(r'<div class="branch-route">.*?</div>', big)
    assert len(routes) == len(set(routes))


# ---- 代碼審 r2(Phase 12 增量 2)----
def test_the_origin_label_follows_the_kind_of_demo_it_came_from() -> None:
    """[代碼審 r2 g2/x1] 出處照結果所屬展示記下的種類寫:完整執行、單一情境重跑;不知道種類的不寫成
    完整執行。"""
    state = replace(make_demo_state(), is_sample=False, full_demo_id=None)
    first = state.scenarios[0]
    for full, text in ((True, "取自完整執行"), (False, "取自單一情境重跑"), (None, "取自展示")):
        shown = replace(state, scenarios=(replace(first, source_full=full), *state.scenarios[1:]))
        markup = render_page(shown, form_token="t", selected=ScenarioCode.F1)
        origin = markup.split('class="source-note">', 1)[1].split("</p>", 1)[0]
        assert origin.startswith(text), origin
        if full is None:
            assert "取自完整執行" not in origin


def test_a_skip_line_passes_above_the_node_numbers_and_says_how_many_it_skips() -> None:
    """[代碼審 r2 g3] 跨過同一泳道的虛線走在「判斷 N」編號字的上方,不劃過編號;跳過幾個節點在
    畫面上看得到。"""
    path = (_step("x_pick", ("x_pick", "x_precheck")),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_guard", ("x_guard", "x_total")), _step("x_pick", ("x_pick", "x_deadletter")))
    state = make_demo_state()
    first = replace(state.scenarios[0], path=path,
                    traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
    svg = _svg(render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                           form_token="t", selected=ScenarioCode.F1))
    gap = int(re.findall(r"-?\d+", re.findall(
        r'<g class="flow-edge is-taken is-skip">.*?<path d="([^"]+)"', svg)[0])[3])
    badge_tops = [int(y) - 12 for y in re.findall(r'class="decision-badge" x="\d+" y="(\d+)"', svg)]
    assert badge_tops and gap < min(badge_tops)
    assert re.search(r'<text class="skip-note"[^>]*>跳過 3 個</text>', svg)


def test_node_numbers_that_jump_ahead_say_how_the_flow_got_there() -> None:
    """[代碼審 r2 g4] 節點照第一次走到的時間排;它自己的第一張判斷卡比較晚時,編號旁標出是經哪一張
    卡走到的,讀起來不會以為後面的步驟先發生。"""
    path = (_step("x_pick", ("x_pick", "x_precheck")),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_guard", ("x_guard", "x_total")),
            _step("x_pick", ("x_pick", "x_precheck")),
            _step("x_total", ("x_total", "x_write")))
    state = make_demo_state()
    first = replace(state.scenarios[0], path=path,
                    traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
    svg = _svg(render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                           form_token="t", selected=ScenarioCode.F1))
    badges = re.findall(r'class="decision-badge"[^>]*>([^<]+)<', svg)
    assert badges == ["判斷 1", "判斷 2", "判斷 3", "判斷 5・經 3"]


def test_the_fifth_summary_cell_has_its_own_border_on_narrow_screens() -> None:
    """[代碼審 r2 g5] 窄螢幕摘要列兩欄時,第 5 格(開始時間)有上框線、沒有左框線。"""
    narrow = "".join(block.split("}\n", 1)[0]
                     for block in DEMO_CSS.split("@media(max-width:760px)")[1:])
    assert re.search(r"\.summary-strip>div:nth-child\(5\)\s*\{[^}]*border-top:1px solid[^}]*"
                     r"border-left:0", narrow)


def test_a_step_back_in_the_middle_of_a_path_still_shows_where_it_returns() -> None:
    """[代碼審 r2 g6] 回頭轉換落在路徑中段(換人接手之後還有下一步)也畫回頭線、標「回到:…」。"""
    path = (_step("x_pick", ("x_pick", "x_precheck")),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_reclaimed", None),
            _step("x_guard", ("x_guard", "x_total")))
    state = make_demo_state()
    first = replace(state.scenarios[0], path=path,
                    traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
    svg = _svg(render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                           form_token="t", selected=ScenarioCode.F1))
    assert re.search(r'<g class="flow-return">.*?回到：領取建議', svg, re.DOTALL)


def test_the_cost_line_says_no_ai_was_called_instead_of_no_run() -> None:
    """[代碼審 r2 g7] 這次沒有呼叫 AI:技術資訊的費用照實寫,不寫「尚無完整執行紀錄」。"""
    state = replace(make_demo_state(), is_sample=False, model_mode=ModelMode.NOT_CALLED,
                    model_cost_usd=None, last_full_run_cost_usd=None, full_demo_id="d")
    markup = render_page(state, form_token="t")
    assert "尚無完整執行紀錄" not in markup and "沒有呼叫 AI，沒有費用" in markup


# ---- 代碼審 r3(Phase 12 增量 2)----
def test_a_long_node_number_is_not_crossed_by_its_own_skip_line() -> None:
    """[代碼審 r3 p1] 「判斷 N・經 M」這種長編號不會被自己那個節點往上拉的跳線劃過。"""
    path = (_step("x_pending", ("x_pending", "x_pick")),
            _step("a_receive", ("a_receive", "a_collect")),
            _step("x_pick", ("x_pick", "x_precheck")),
            _step("x_precheck", ("x_precheck", "x_guard")),
            _step("x_guard", ("x_guard", "x_total")),
            _step("x_pick", ("x_pick", "x_deadletter")))
    state = make_demo_state()
    first = replace(state.scenarios[0], path=path,
                    traversed_edges=tuple(d.taken_edge for d in path if d.taken_edge))
    svg = _svg(render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                           form_token="t", selected=ScenarioCode.F1))
    badges = [(int(x), text) for x, text in re.findall(
        r'class="decision-badge" x="(\d+)" y="\d+">([^<]+)<', svg) if "經" in text]
    assert badges, "要有一個長編號"
    lines = [int(re.findall(r"-?\d+", d)[0]) for d in re.findall(
        r'<g class="flow-edge is-taken is-skip">.*?<path d="([^"]+)"', svg)]
    for x, text in badges:
        right = x + _text_width(text, 12)
        assert all(not (x <= line <= right) for line in lines), (x, right, lines)


def test_the_page_uses_only_public_names_from_the_flow_drawing() -> None:
    """[代碼審 r3 a1] 頁面組裝與測試只用流程圖那支檔公開的名字(照拆檔慣例),不匯入底線開頭的
    私有實作。"""
    import ast
    import inspect

    from rtb.demo import page

    imported = [alias.name for node in ast.walk(ast.parse(inspect.getsource(page)))
                if isinstance(node, ast.ImportFrom) and node.module
                and node.module.startswith("rtb.demo") for alias in node.names]
    assert imported and not [name for name in imported if name.startswith("_")]


# ---- 增量 3:前後比較表 ----
def test_the_report_shows_the_comparison_side_by_side() -> None:
    """[S1041] 報告裡的前後比較表:每一列造假手法、沒有自動查核、有自動查核並排,加說明;外來文字跳脫。
    主頁不放(使用者裁定補充資訊只留在報告)。"""
    from rtb.demo.state import Comparison, ComparisonRow

    comparison = Comparison((ComparisonRow("只填已完成", "pytest 結束代碼 0:2 passed",
                                           "擋下:缺 result"),
                             ComparisonRow(HOSTILE, "pytest 結束代碼 0:2 passed", "通過")),
                            "比的是有沒有機械驗證")
    state = replace(make_demo_state(), comparison=comparison)
    report = render_report(state)
    table = report.split("有無自動查核的差別", 1)[1]
    assert "<th>沒有自動查核</th><th>有自動查核</th>" in table
    assert ("<tr><td>只填已完成</td><td>pytest 結束代碼 0:2 passed</td><td>擋下:缺 result</td></tr>"
            in table)
    assert "比的是有沒有機械驗證" in table and report.count("<script>") == 1
    assert "有無自動查核的差別" not in render_page(state, form_token="t")
    empty = render_report(replace(state, comparison=None))
    assert "前後比較這次還沒有產生" in empty


# ---- 增量 4 代碼審 r2(2026-09-25) ----
def _later(markup: str) -> str | None:
    found = re.search(r'<p class="flow-later">([^<]*)</p>', markup)
    return None if found is None else html_lib.unescape(found.group(1))


def test_unentered_lanes_follow_the_roles_actually_touched() -> None:
    """r2 p1:「這次沒有走到」看這件工作實際碰過的角色,不是畫了幾條泳道。寫進平台(x_write)就算碰過
    廣告平台,即使沒有留下平台回覆;中間沒有節點的泳道也要點名。"""
    state = make_demo_state()
    f2 = next(s for s in state.scenarios if s.code is ScenarioCode.F2)
    wrote = Decision("x_write", ("x_total", "x_write"), "送出", "", None)
    crashed = replace(f2, path=(*f2.path, wrote),
                      traversed_edges=(*f2.traversed_edges, ("x_total", "x_write")))
    shown = replace(state, scenarios=tuple(crashed if s is f2 else s for s in state.scenarios))
    assert _later(render_page(shown, form_token="t", selected=ScenarioCode.F2)) == (
        "人工這次沒有走到")
    # F6 範例:從執行直接交給人工決定,中間的廣告平台泳道畫了但沒有節點 → 要點名
    assert _later(render_page(state, form_token="t", selected=ScenarioCode.F6)) == (
        "廣告平台這次沒有走到")


def test_repeated_flow_cell_names_the_task_and_its_own_round() -> None:
    """r2 p4:同一格有好幾件工作時,浮出框寫「工作 t3 第 2 次」,按每件工作各自數(Phase 14 增量 3:
    AI 那格數「輪」的說法隨 AI 決策節點撤除)。"""
    state = make_demo_state()
    first = state.scenarios[0]
    step = next(d for d in first.path if d.node == "x_pick")  # 分析那幾格併成一組,取執行端的格
    changed = replace(first, path=(replace(step, task_id="t1"), replace(step, task_id="t3"),
                                   replace(step, task_id="t3")))
    page = render_page(replace(state, scenarios=(changed, *state.scenarios[1:])),
                       form_token="t")
    box = page.split(f'id="flow-detail-F1-{step.node}"', 1)[1].split('class="flow-popover"', 1)[0]
    labels = re.findall(r'<li class="flow-occurrence"><strong>([^<]+)</strong>', box)
    assert labels == ["工作 t1 第 1 次", "工作 t3 第 1 次", "工作 t3 第 2 次"]


def test_narrow_scenario_strip_scrolls_the_selected_card_into_view() -> None:
    """r2 p5:窄螢幕的情境列載入時把選中的卡捲進可視區(只動那一列的 scrollLeft,不捲整頁)。"""
    assert "revealSelected" in FLOW_SCRIPT
    assert ".scenario-row.is-selected" in FLOW_SCRIPT and "scrollLeft" in FLOW_SCRIPT


def test_flow_popover_is_keyboard_operable() -> None:
    """r2 p2/p3(不靠瀏覽器的那一半;行為由 test_flow_popover_browser 在瀏覽器裡驗):框可聚焦、
    鍵盤釘住後焦點移進框、Esc 收框並把焦點還給格子、框內按鍵不觸發格子的開關;hover 開的框不因焦點
    留在格子而不收。"""
    page = render_page(make_demo_state(), form_token="t")
    assert re.search(r'<div class="flow-popover" id="[^"]+" hidden tabindex="-1"', page)
    assert "box.focus(" in FLOW_SCRIPT
    assert "returnFocus" in FLOW_SCRIPT
    assert "openedByFocus" in FLOW_SCRIPT
    assert "closest?.('.flow-popover')" in FLOW_SCRIPT


def test_saved_report_carries_a_hash_bound_content_security_policy() -> None:
    """r2 s3:另存的單檔報告(樣式內嵌)在 <head> 帶 meta CSP:腳本與內嵌樣式各用自己的 sha256,
    沒有 unsafe-inline;經伺服器送的報告與頁面照舊用回應標頭。"""
    report = render_report(make_demo_state(), inline_styles=True)
    head = report.split("</head>", 1)[0]
    found = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', head)
    assert found is not None
    policy = html_lib.unescape(found.group(1))
    assert "unsafe-inline" not in policy and "default-src 'none'" in policy

    def digest(text: str) -> str:
        return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()

    scripts = re.findall(r"<script>(.*?)</script>", report, re.DOTALL)
    styles = re.findall(r"<style>(.*?)</style>", report, re.DOTALL)
    assert scripts and styles
    for script in scripts:
        assert f"script-src 'sha256-{digest(script)}'" in policy
    for style in styles:
        assert f"'sha256-{digest(style)}'" in policy.split("style-src", 1)[1].split(";", 1)[0]
    assert found.start() < head.index("<style>")  # 政策要在內嵌樣式之前宣告
    served = render_report(make_demo_state(), inline_styles=False)
    assert 'http-equiv="Content-Security-Policy"' not in served


def test_cross_role_touches_come_from_the_flow_graph() -> None:
    """(代碼審 r3 a2)「走到這格就算碰過另一個角色」的對應表:鍵都是流程圖存在的節點或回頭轉移,值用
    流程圖的角色常數(泳道改名或節點改名時,這張表不會靜默失效)。"""
    import ast
    from pathlib import Path

    from rtb.demo import flow, flow_svg

    known = {node.id for node in flow.FLOW_GRAPH.nodes} | {
        back.node for back in flow.BACK_TRANSITIONS}
    assert flow_svg._ALSO_TOUCHES
    for node_id, lane in flow_svg._ALSO_TOUCHES.items():
        assert node_id in known, node_id
        assert lane in flow.LANES, lane
    assert set(flow_svg._ALSO_TOUCHES.values()) == {flow.PLATFORM}
    tree = ast.parse(Path(flow_svg.__file__).read_text(encoding="utf-8"))
    [table] = [n.value for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
               and getattr(n.target, "id", "") == "_ALSO_TOUCHES"]
    assert isinstance(table, ast.Dict)
    assert all(isinstance(value, ast.Name) and value.id == "PLATFORM" for value in table.values)
