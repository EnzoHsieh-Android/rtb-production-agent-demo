# ruff: noqa: RUF001, S106
"""展示頁的 AI 文字(Phase 13 增量 4 [S1121] [S1122] [S1123] 起;Phase 14 增量 3 改寫,計劃
[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈拆增量〉3):真的跑情境(假錄製),從展示狀態庫組頁面。
AI 不參與要不要加預算:頁面沒有 AI 調查卡、AI 決策節點與考題,只在提案的說明格與告警的假說格標 AI
文字;判斷紀錄只有規則輪的根據與九條細因。"""

import html as html_lib
import os
import re
from datetime import UTC, datetime

import pytest

from rtb.demo.driver import RULE_THREE_LABEL, Driver
from rtb.demo.keys import DemoKeys
from rtb.demo.page import RULE_DECIDES, render_page, render_report
from rtb.demo.present import build_demo_state
from rtb.demo.state import ModelMode, ScenarioCode
from rtb.demo.state_store import StateReader, StateWriter
from tests.demo import fake_recordings as fake

BATCH = "phase14-demo-20260926"


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    """F1、F4、F5 讀有說明的假錄製;F2 沒有錄製(說明照實寫沒有回答)。"""
    root = tmp_path_factory.mktemp("ai-page")
    recordings = root / "rec"
    fake.fake_batch(recordings, BATCH, ("normal", "attacked"))
    writer = StateWriter(root / "state.db", "demo-1")
    try:
        demo = Driver(root / "demos", "demo-1", DemoKeys.generate(), writer,
                      user_env=os.environ, recordings_dir=recordings)
        for code in ("F1", "F4", "F5"):
            assert demo.run_one(code).status == "done", code
        empty = Driver(root / "demos2", "demo-1", DemoKeys.generate(), writer,
                       user_env=os.environ, recordings_dir=root / "none")
        assert empty.run_one("F2").status == "done"
    finally:
        writer.close()
    reader = StateReader(root / "state.db")
    try:
        state = build_demo_state(reader, "demo-1", running=False, now=datetime.now(UTC))
    finally:
        reader.close()
    return state


def _scenario(state, code):
    return next(s for s in state.scenarios if s.code is code)


def _text(markup):
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", markup))


def test_pages_have_no_ai_decision_cards_nodes_or_exam(ran):
    """[S1121] [S1122] 改寫:頁面沒有 AI 逐輪判斷卡、AI 選下一步節點、退回標示與考題;判斷紀錄是規則輪
    的根據(九條細因)。"""
    for code in (ScenarioCode.F1, ScenarioCode.F4, ScenarioCode.F5, ScenarioCode.F2):
        page = render_page(ran, form_token="t", selected=code, refresh_tick=0)
        for gone in ('class="ai-rounds"', "AI 逐輪判斷", "AI 選下一步", "AI 選的下一步",
                     "AI 要再查", "只判不送", "考題", "改由程式規則決定", "程式接手",
                     'data-node="a_ai"'):
            assert gone not in page, (code, gone)
        scenario = _scenario(ran, code)
        assert not {"a_ai", "a_ai_query", "a_exam_hold"} & {d.node for d in scenario.path}
    f1 = _scenario(ran, ScenarioCode.F1)
    worth = next(d for d in f1.path if d.taken_edge == ("a_worth", "a_propose"))
    assert any("正式規則是九條" in b.standard for b in worth.basis)


def test_ai_scenarios_have_no_demo_mode_banner(ran):
    """[S1123] 詳情頁與靜態報告不掛展示模式橫幅;模型模式照說明入口回報的寫。"""
    f1, f3 = _scenario(ran, ScenarioCode.F1), _scenario(ran, ScenarioCode.F3)
    assert f1.ai_enabled and not f3.ai_enabled
    assert f1.model_mode is ModelMode.RECORDED
    page = render_page(ran, form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    assert 'class="ai-banner"' not in page
    assert "展示模式、未通過採用門檻" not in page
    assert "展示模式、未採用" not in page
    report = render_report(ran)
    assert 'class="ai-banner"' not in report
    assert ran.model_mode is ModelMode.RECORDED
    assert ran.model_mode_reason == "錄製回應,不是即時呼叫"


def test_decision_hero_says_the_rules_decide(ran):
    f5 = _scenario(ran, ScenarioCode.F5)
    page = render_page(ran, form_token="t", selected=ScenarioCode.F5, refresh_tick=0)
    hero = _text(page.split('class="decision-hero"', 1)[1].split('</section>', 1)[0])
    assert "這次誰決定" in hero and RULE_DECIDES in hero
    assert "AI 選的下一步" not in hero and "退回原因" not in hero
    assert f5.result_summary in hero


def test_f4_follow_up_shows_rule_three_without_ai(ran):
    """[S1409] F4 接續任務的結局標示只寫規則第 3 條,不標 AI 決策來源。"""
    f4 = _scenario(ran, ScenarioCode.F4)
    assert RULE_THREE_LABEL in (f4.outcome_note or "")
    page = render_page(ran, form_token="t", selected=ScenarioCode.F4, refresh_tick=0)
    note = page.split('class="ai-outcome"', 1)[1].split("</p>", 1)[0]
    assert RULE_THREE_LABEL in _text(note) and "AI" not in _text(note)


def test_the_narrative_shows_computed_numbers_first_without_mode_source(ran):
    """[S1027] 說明卡的數字在 AI 文字前,且頁面不標錄製或即時。"""
    step = _scenario(ran, ScenarioCode.F1).model_step
    assert step is not None and step.result_kind == "ok"
    assert step.numbers == (("廣告", "c1"), ("建議金額", "100 → 110"))
    assert step.narrative == fake.NARRATIVE
    page = render_page(ran, form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    card = page.split('class="ai-node-card"', 1)[1]
    assert card.index("100 → 110") < card.index(fake.NARRATIVE[:8])
    assert "錄製回應" not in card and "即時" not in card
    missing = _scenario(ran, ScenarioCode.F2).model_step  # 沒有錄製:照實寫結果類別
    assert missing is not None and missing.narrative is None
    assert missing.result_kind == "no_recording"
    assert "AI 這次沒有給出回答" in _text(render_page(ran, form_token="t", refresh_tick=0,
                                                     selected=ScenarioCode.F2))


def test_the_hypothesis_card_says_which_kind_of_nothing(ran):
    """(代碼審 r1 p1)假說卡:沒有記錄、命令列沒跑完(不知道有沒有告警)、明確沒有告警,各寫各的;這三種
    都不畫「AI 推測可能原因」節點。只有告警響、問過 AI 才有假說卡的內容。"""
    from dataclasses import replace

    from rtb.demo import present
    from rtb.demo.state_store import ScenarioDetails

    cases = {None: present.NO_RECORD,
             '{"status": "not_run", "error": "命令列逾時(120 秒)"}':
                 f"{present.NOT_ASKED}(命令列逾時(120 秒))",
             '{"status": "failed", "error": "命令列逾時"}': f"{present.NOT_ASKED}(命令列逾時)",
             '{"status": "no_alert", "message": "x"}': present.NO_ALERT}
    for stored, note in cases.items():
        found, shown = present._hypothesis(ScenarioDetails(ai_enabled=True, hypothesis_json=stored))
        assert found is None and shown == note, stored
    found, shown = present._hypothesis(ScenarioDetails(ai_enabled=True, hypothesis_json=(
        '{"status": "failed", "reason": "no_recording", "alerts": ["x"], "mode": "recorded"}')))
    assert shown is None and found.hypotheses == () and "no_recording" in found.next_step
    f1 = _scenario(ran, ScenarioCode.F1)
    assert f1.hypothesis is None and f1.hypothesis_note == present.NO_ALERT  # 真跑:沒有告警
    for note in cases.values():
        page = render_page(replace(ran, scenarios=tuple(
            replace(s, hypothesis=None, hypothesis_note=note) if s is f1 else s
            for s in ran.scenarios)), form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
        assert note in _text(page) and "推測原因" not in page.split('class="flow-graph"')[1].split(
            "</svg>")[0]


def test_the_new_page_text_explains_its_jargon_on_real_runs(ran):
    """(代碼審 r1 p4)真跑過的 F1、F2、F4、F5 主頁:結果、決策摘要、結局標示、說明與假說卡裡的「模型」
    「提案」都緊接括號白話解釋。"""
    for code in (ScenarioCode.F1, ScenarioCode.F2, ScenarioCode.F4, ScenarioCode.F5):
        page = render_page(ran, form_token="t", selected=code, refresh_tick=0)
        focus = page.split('class="focus-panel', 1)[1]
        parts = [focus.split('class="result-evidence"', 1)[1].split("</details>", 1)[0],
                 focus.split('class="decision-hero"', 1)[1].split("</section>", 1)[0],
                 *re.findall(r'<(?:div|p) class="(?:ai-banner|ai-outcome|ai-node-card)'
                             r'"[^>]*>.*?</(?:div|p)>', focus, re.DOTALL)]
        text = _text("".join(parts))
        for term in ("模型", "提案"):
            assert term not in re.sub(rf"{term}（[^（）]+）", "", text), (code, term, text[:300])


def _later(markup):
    found = re.search(r'<p class="flow-later">([^<]*)</p>', markup)
    return None if found is None else html_lib.unescape(found.group(1))


def test_f2_that_wrote_to_the_platform_does_not_say_the_platform_was_not_reached(ran):
    """r2 p1:F2 寫進平台之後執行端才倒下(沒有平台回覆那一格),「這次沒有走到」不得點名廣告平台。"""
    page = render_page(ran, form_token="t", selected=ScenarioCode.F2, refresh_tick=0)
    assert "x_write" in {d.node for d in _scenario(ran, ScenarioCode.F2).path} or (
        "x_total", "x_write") in _scenario(ran, ScenarioCode.F2).traversed_edges
    later = _later(page)
    assert later is not None and "廣告平台" not in later and "人工" in later
