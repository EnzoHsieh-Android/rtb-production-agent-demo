# ruff: noqa: RUF001, S106
"""Phase 13 增量 4:展示頁的 AI 步驟(計劃〈展示頁怎麼顯示〉,[S1121] [S1122] [S1123]):真的跑情境
(假錄製),從展示狀態庫組頁面,看每一輪 AI 步驟與標示。"""

import html as html_lib
import os
import re
from datetime import UTC, datetime

import pytest

from rtb.demo.driver import Driver
from rtb.demo.keys import DemoKeys
from rtb.demo.page import (
    CITED_LABEL,
    DEMO_MODE_BANNER,
    JARGON_TERMS,
    MODEL_LABEL,
    render_page,
    render_report,
)
from rtb.demo.present import build_demo_state
from rtb.demo.state import DecidedBy, DecisionKind, ModelMode, ScenarioCode
from rtb.demo.state_store import StateReader, StateWriter
from tests.demo import fake_recordings as fake

BATCH = "phase13-demo-test"


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    """F1:AI 第 1 輪選查詢、第 2 輪判值得加;F2:沒有錄製,退回程式規則。"""
    root = tmp_path_factory.mktemp("ai-page")
    recordings = root / "rec"
    fake.fake_batch(recordings, BATCH, {"normal": [fake.LONGER_WINDOW, fake.PROPOSE]},
                    narrative=fake.NARRATIVE)
    writer = StateWriter(root / "state.db", "demo-1")
    try:
        demo = Driver(root / "demos", "demo-1", DemoKeys.generate(), writer,
                      user_env=os.environ, recordings_dir=recordings)
        assert demo.run_one("F1").status == "done"
        empty = Driver(root / "demos2", "demo-1", DemoKeys.generate(), writer,
                       user_env=os.environ, recordings_dir=root / "none")
        assert empty.run_one("F2").status == "done"
        twin = root / "twin"  # 雙胞胎第 1 輪答選項外、退回程式規則;受攻擊廣告判值得加(代碼審 r1 p3)
        fake.fake_batch(twin, BATCH, {"normal": [fake.OFF_MENU], "attacked": [fake.PROPOSE]})
        held = Driver(root / "demos3", "demo-1", DemoKeys.generate(), writer,
                      user_env=os.environ, recordings_dir=twin)
        assert held.run_one("F5").status == "done"
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


def test_each_ai_round_shows_evidence_choice_reason_and_takeover(ran):
    """[S1121] 每一輪:程式格式化的證據、允許的選項、選了什麼、標「AI 產生、僅供參考」與來源的理由、
    「AI 引用的收據值(已核對存在)」,以及程式怎麼接手。"""
    f1 = _scenario(ran, ScenarioCode.F1)
    rounds = [d for d in f1.path if d.kind is DecisionKind.AI_JUDGEMENT]
    assert [d.taken_edge for d in rounds] == [("a_ai", "a_ai_query"), ("a_ai", "a_propose")]
    first, second = rounds
    main = first.basis[0]
    assert "base:" in main.observed and "budget=100" in main.observed  # 程式算的收據字串
    assert "check_longer_window" in main.standard and "propose" in main.standard
    assert "選了 check_longer_window" in main.conclusion and "先看 1 天與 7 天" in main.conclusion
    assert main.source == "錄製回應"
    assert "check_longer_window:d1_" in second.basis[0].observed  # 第 2 輪看得到查回來的收據
    assert "check_longer_window" not in second.basis[0].standard.split(":", 1)[1].split("、")[0]
    assert second.basis[1].observed == "base.conversions = 1"  # 引用(已核對存在)
    takeovers = [d for d in f1.path if d.kind is DecisionKind.PROGRESS and d.basis
                 and d.node in {"a_ai_query", "a_propose"}]
    assert [d.node for d in takeovers] == ["a_ai_query", "a_propose"]
    assert "照 AI 選的唯讀查詢去讀" in takeovers[0].basis[0].observed
    assert takeovers[1].basis[0].observed == "100 → 110"  # 照公式算出的金額
    page = render_page(ran, form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    text = _text(page)
    for label in (MODEL_LABEL, "AI 看到的證據", "這一輪允許的選項", CITED_LABEL, "程式接手",
                  "錄製回應", "100 → 110", "base.conversions = 1"):
        assert label in text, label
    cards = page.split('class="decision-trail"', 1)[1].split("</ol>", 1)[0].split("<li ")
    ours = _text("".join(c for c in cards if "ai-round" in c or "程式接手" in c))
    assert ours
    for term in JARGON_TERMS:  # 新加的 AI 步驟文字也照白話規則(術語要緊接括號解釋)
        assert term not in re.sub(rf"{re.escape(term)}（[^（）]+）", "", ours), term


def test_a_fallback_is_labelled_on_the_page(ran):
    """[S1122] 退回的那一輪在那一步標「這次改由程式規則決定」與原因類別,之後照程式規則判。"""
    f2 = _scenario(ran, ScenarioCode.F2)
    [fallback] = [d for d in f2.path if d.taken_edge == ("a_ai", "a_rule")]
    assert fallback.kind is DecisionKind.JUDGEMENT  # 不是 AI 判的
    assert fallback.outcome == "這次改由程式規則決定(原因:沒有對應的錄製回應)"
    after = f2.path[f2.path.index(fallback) + 1:]
    assert [d.taken_edge for d in after[:2]] == [("a_rule", "a_worth"), ("a_worth", "a_propose")]
    assert f2.decided_by is DecidedBy.AI_FALLBACK
    text = _text(render_page(ran, form_token="t", selected=ScenarioCode.F2, refresh_tick=0))
    assert "這次改由程式規則決定(原因:沒有對應的錄製回應)" in text


def test_ai_scenarios_are_labelled_demo_mode_not_adopted(ran):
    """[S1123] 開了 AI 決策的情境,頁面與靜態報告都標「展示模式、未通過採用門檻」;沒跑過的不標。"""
    f1, f3 = _scenario(ran, ScenarioCode.F1), _scenario(ran, ScenarioCode.F3)
    assert f1.ai_enabled and f1.decided_by is DecidedBy.AI_DEMO
    assert f1.model_mode is ModelMode.RECORDED and not f3.ai_enabled
    page = render_page(ran, form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    assert DEMO_MODE_BANNER in page and "這次誰決定:AI(展示模式)".replace(":", "：") in page
    report = render_report(ran)
    banner = f'<div class="ai-banner"><strong>{DEMO_MODE_BANNER}</strong>'
    assert report.count(banner) == 3  # F1、F2、F5 三個開了 AI 的情境,其餘還沒跑
    assert banner not in render_page(ran, form_token="t", selected=ScenarioCode.F3,
                                     refresh_tick=0)
    assert ran.model_mode is ModelMode.RECORDED
    assert ran.model_mode_reason == "錄製回應,不是即時呼叫"  # F1、F5 有對上的錄製回應


def test_the_narrative_shows_computed_numbers_first_and_its_source(ran):
    """[S1027](從增量 1 移過來的頁面串接):錄製模式的說明卡有內容,程式算的數字在前、AI 文字在後。"""
    step = _scenario(ran, ScenarioCode.F1).model_step
    assert step is not None and step.result_kind == "ok"
    assert step.numbers == (("廣告", "c1"), ("建議金額", "100 → 110"))
    assert step.narrative == fake.NARRATIVE
    page = render_page(ran, form_token="t", selected=ScenarioCode.F1, refresh_tick=0)
    card = page.split('class="ai-node-card"', 1)[1]
    assert card.index("100 → 110") < card.index(fake.NARRATIVE[:8])
    missing = _scenario(ran, ScenarioCode.F2).model_step  # 沒有錄製:照實寫結果類別
    assert missing is not None and missing.narrative is None
    assert missing.result_kind == "no_recording"
    assert "沒有對應的錄製回應" in _text(render_page(ran, form_token="t", refresh_tick=0,
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


def test_an_untouched_fault_counts_as_finished_and_hides_the_pivot(ran):
    """(代碼審 r1 p2)「AI 判不提案、故障沒走到」算已完成、另外標幾個沒走到;列表那一列不寫固定的
    故障位置。"""
    from dataclasses import replace

    from rtb.demo.state import ScenarioStatus

    f2 = _scenario(ran, ScenarioCode.F2)
    state = replace(ran, scenarios=tuple(
        replace(s, status=ScenarioStatus.NOT_EXERCISED) if s is f2 else s for s in ran.scenarios))
    text = _text(render_page(state, form_token="t", selected=ScenarioCode.F1, refresh_tick=0))
    assert "3 / 7" in text and "其中 1 個 AI 判不提案（不提出調整建議），故障沒走到" in text
    assert "故障這次沒有走到" in text
    row = text.split("寫進平台之後執行端當場倒下", 1)[1].split("F3", 1)[0]
    assert "轉向" not in row and "故障這次沒有走到" in row


def test_a_held_twin_that_fell_back_shows_the_rule_path_to_the_exam_hold(ran):
    """(代碼審 r1 p3)F5 雙胞胎退回程式規則後照規則判值得加、只判不送:畫出「用程式規則 → 值得加嗎 →
    寫建議 → 只判不送」,帶重算的值得加根據。"""
    f5 = _scenario(ran, ScenarioCode.F5)
    twin = [d.taken_edge for d in f5.path if d.task_id == "t3"]
    for edge in (("a_ai", "a_rule"), ("a_rule", "a_worth"), ("a_worth", "a_propose"),
                 ("a_propose", "a_exam_hold")):
        assert edge in twin, (edge, twin)
    worth = next(d for d in f5.path if d.task_id == "t3" and d.taken_edge == ("a_worth",
                                                                             "a_propose"))
    assert worth.basis and worth.basis[0].source == "依存下的證據重算"


def test_the_new_page_text_explains_its_jargon_on_real_runs(ran):
    """(代碼審 r1 p4)真跑過的 F1、F2、F5 主頁:這一增量加的文字(結果、考題、橫幅、AI 步驟與程式接手、
    說明與假說卡)裡的「模型」「提案」都緊接括號白話解釋。"""
    for code in (ScenarioCode.F1, ScenarioCode.F2, ScenarioCode.F5):
        page = render_page(ran, form_token="t", selected=code, refresh_tick=0)
        focus = page.split('class="focus-panel', 1)[1]
        parts = [focus.split('class="result-evidence"', 1)[1].split("</details>", 1)[0],
                 *re.findall(r'<(?:div|p) class="(?:ai-banner|ai-outcome|ai-exam|ai-node-card)'
                             r'"[^>]*>.*?</(?:div|p)>', focus, re.DOTALL),
                 *[c for c in focus.split("<li ") if "ai-round" in c or "程式接手" in c]]
        text = _text("".join(parts))
        for term in ("模型", "提案"):
            assert term not in re.sub(rf"{term}（[^（）]+）", "", text), (code, term, text[:300])
