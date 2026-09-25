"""展示狀態資料 → 頁面 DemoState(Phase 12 增量 2b):真的跑情境,再用頁面真的畫一次。"""

import itertools
import os
from datetime import UTC, datetime, timedelta

import pytest

from rtb.demo.driver import Driver
from rtb.demo.flow import FLOW_GRAPH
from rtb.demo.keys import DemoKeys
from rtb.demo.page import render_page
from rtb.demo.present import MODEL_MODE_REASON, build_demo_state, numbers_digest
from rtb.demo.state import ScenarioCode, ScenarioStatus
from rtb.demo.state_store import (
    ConfirmationRequest,
    StateReader,
    StateWriter,
    VerifierRun,
)

EDGES = {(e.source, e.target) for e in FLOW_GRAPH.edges}


@pytest.fixture
def ran(tmp_path):
    """真的跑 F2(猝死重啟)與 F4(舊工作擋下、開新工作:路徑會斷開成兩段)。"""
    writer = StateWriter(tmp_path / "state.db", "demo-1")
    demo = Driver(tmp_path / "demos", "demo-1", DemoKeys.generate(), writer, user_env=os.environ)
    for code in ("F2", "F4"):
        assert demo.run_one(code).status == "done"
    writer.record_verifier_run(VerifierRun(
        "demo-1", datetime.now(UTC), True,
        ("宣稱驗證器", "驗證器 sha256:abc123", "提交編號:deadbeef", "通過:5 條宣稱"), ()))
    return tmp_path, writer


def _state(tmp_path, running=False):
    reader = StateReader(tmp_path / "state.db")
    try:
        return build_demo_state(reader, "demo-1", running=running, now=datetime.now(UTC),
                                verifier_demo_id="demo-1", full_demo_id="demo-1")
    finally:
        reader.close()


def test_a_real_run_becomes_a_page_that_renders(ran):
    tmp_path, _ = ran
    state = _state(tmp_path)
    markup = render_page(state, form_token="form-value", refresh_tick=1,  # noqa: S106 - 測試值
                         selected=ScenarioCode.F2)
    assert "寫進平台後執行端當場倒下" in markup
    # F4 的路徑斷成兩段(舊工作擋下、新工作寫入):照樣畫得出來
    assert "220" in render_page(state, form_token="form-value", refresh_tick=2,  # noqa: S106
                                selected=ScenarioCode.F4)
    by_code = {s.code: s for s in state.scenarios}
    assert by_code[ScenarioCode.F2].status is ScenarioStatus.DONE
    assert by_code[ScenarioCode.F1].status is ScenarioStatus.PENDING  # 沒跑的照實標尚未開始
    assert by_code[ScenarioCode.F1].path == () and by_code[ScenarioCode.F1].change_summary is None


def test_paths_use_only_real_edges_and_may_break_off(ran):
    """[協調者裁定 1] 走過的邊都是正式邊、照時間排;F4 有兩件工作,中間斷開不補路。"""
    tmp_path, _ = ran
    f4 = next(s for s in _state(tmp_path).scenarios if s.code is ScenarioCode.F4)
    assert f4.traversed_edges and set(f4.traversed_edges) <= EDGES
    assert any(a[1] != b[0] for a, b in itertools.pairwise(f4.traversed_edges))
    assert all(d.taken_edge is None or d.taken_edge in EDGES for d in f4.path)


def test_the_analysis_checks_are_filled_in_from_the_recomputed_basis(ran):
    """[協調者裁定 2] 分析端的中間判斷點用重算補上,每一筆標重算。Phase 13 增量 4:分析那一步開了 AI
    決策,這次沒有錄製,AI 那一步寫「這次改由程式規則決定」,之後照程式規則判
    (不再畫「交給誰判斷」)。"""
    tmp_path, _ = ran
    f2 = next(s for s in _state(tmp_path).scenarios if s.code is ScenarioCode.F2)
    filled = {d.node: d for d in f2.path if d.node in {
        "a_fresh", "a_complete", "a_pacing", "a_rule", "a_worth"}}
    assert set(filled) == {"a_fresh", "a_complete", "a_pacing", "a_rule", "a_worth"}
    assert all(b.source == "依存下的證據重算" for d in filled.values() for b in d.basis)
    assert filled["a_pacing"].taken_edge == ("a_pacing", "a_ai")
    [fallback] = [d for d in f2.path if d.taken_edge == ("a_ai", "a_rule")]
    assert "沒有對應的錄製回應" in fallback.outcome
    assert filled["a_rule"].taken_edge == ("a_rule", "a_worth")
    assert not any(d.node == "a_route" for d in f2.path)
    write_checks = [d for d in f2.path if d.node in {"x_guard", "x_total"}]
    assert [d.taken_edge for d in write_checks] == [("x_guard", "x_total"), ("x_total", "x_write")]
    assert all(b.source == "執行端當下記下" for d in write_checks for b in d.basis)


def test_scenario_fields_come_from_what_the_driver_recorded(ran):
    tmp_path, _ = ran
    state = _state(tmp_path)
    f2 = next(s for s in state.scenarios if s.code is ScenarioCode.F2)
    assert f2.change_summary.before == 100 and f2.change_summary.after == 110
    assert f2.platform_apply_count == 1 and f2.operation_key
    assert [f.node for f in f2.injected_faults] == ["x_write"]
    assert f2.dsp.campaigns[0].budget == 110 and f2.timeline
    # Phase 13 增量 4:說明命令列在錄製模式跑了,沒有錄製就照實記結果類別;沒有告警就沒有假說
    assert f2.model_step is not None and f2.model_step.result_kind == "no_recording"
    assert f2.model_step.narrative is None and f2.hypothesis is None
    assert state.comparison is None and state.model_mode_reason == MODEL_MODE_REASON
    assert state.verifier_digest == "abc123" and state.commit == "deadbeef"
    assert state.is_sample is False


def test_a_pending_confirmation_becomes_the_approval_form(ran):
    tmp_path, writer = ran
    request = ConfirmationRequest("t1", 1, "h" * 64, "/x", "aggregate_limit_reached", 10,
                                  datetime.now(UTC) + timedelta(hours=1),
                                  (("廣告", "c1"), ("金額", "100 → 110")))
    writer.set_confirmation("F7", request)
    form = _state(tmp_path).approval
    assert form.proposal_hash == request.proposal_hash and form.numbers == request.numbers
    assert form.narrative is None and form.source is None
    assert form.numbers_digest == numbers_digest("demo-1", request)
    changed = ConfirmationRequest(*[*request.__dict__.values()][:-1], (("廣告", "c2"),))
    assert numbers_digest("demo-1", changed) != form.numbers_digest


def test_the_path_keeps_only_real_edges_in_order_without_filling_gaps():
    """沒有邊的判斷不進路徑、連續重複的邊只留一次、中間沒看到的邊不補。"""
    from rtb.demo import present
    from rtb.demo.state import Decision

    def step(edge):
        return Decision(node="x", taken_edge=edge, outcome="", reason="", at=None)

    path = (step(("x_write", "p_reply")), step(("x_write", "p_reply")), step(None),
            step(("x_unknown", "x_verify")))
    assert present._traversed(path) == (("x_write", "p_reply"), ("x_unknown", "x_verify"))


def test_each_decision_says_whether_it_is_a_judgement_or_a_state_step():
    """[協調者 2026-09-24] 有邊看起點節點的種類;沒有邊看節點本身的種類:判斷點就是「判斷」,缺了邊
    也不改標成狀態前進(不能把缺口藏起來)。"""
    from rtb.demo import present
    from rtb.demo.state import DecisionKind
    from rtb.demo.state_store import DecisionRow

    at = datetime.now(UTC)
    kinds = [d.kind for d in present._decisions([
        DecisionRow("x_unknown", ("p_reply", "x_unknown"), "o", "r", at, "s"),  # 平台回覆:判斷點
        DecisionRow("x_pick", ("x_pending", "x_pick"), "o", "r", at, "s"),  # 排隊 → 拿起:步驟
        DecisionRow("x_pick", None, "o", "r", at, "s"),  # 沒有邊,節點是判斷點
        DecisionRow("x_reclaimed", None, "o", "r", at, "s"),  # 沒有邊,節點不是判斷點
    ])]
    assert kinds == [DecisionKind.JUDGEMENT, DecisionKind.PROGRESS, DecisionKind.JUDGEMENT,
                     DecisionKind.PROGRESS]


# ---- 代碼審 r1(Phase 12 增量 2)----
def test_a_write_let_through_by_a_person_is_not_drawn_as_passing_the_checks():
    """[代碼審 r1 d4] 開始寫入那一列的核對材料有沒通過的(人確認後放行):觀察器推出來的「總上限:
    不會超過」那條邊跟根據矛盾,清空;根據照樣留著。"""
    from rtb.demo import present
    from rtb.demo.state_store import Basis, BasisCode, DecisionRow

    over = Basis("已經加出去 120,加上這次共 130", "全部廣告加起來的總上限 124",
                 "超過總上限,人確認後放行", "執行端當下記下", BasisCode.TOTAL_OVER)
    row = DecisionRow("x_write", ("x_total", "x_write"), "o", "r", datetime.now(UTC), "s",
                      (over,), "k1", "程式", "t1")
    (decision,) = present._decisions([row])
    assert decision.taken_edge is None and decision.node == "x_write"
    assert decision.basis[0].conclusion == over.conclusion


def test_decisions_carry_their_task_and_edges_are_a_set():
    """[代碼審 r1 d9] 判斷紀錄帶工作編號;走過的邊是集合(每條一次、照第一次出現排),好幾件工作時
    不把它當成一條路徑。"""
    from rtb.demo import present
    from rtb.demo.state_store import DecisionRow

    at = datetime.now(UTC)
    rows = [DecisionRow("a_collect", ("a_receive", "a_collect"), "o", "r", at, "s", task="t1"),
            DecisionRow("a_collect", ("a_receive", "a_collect"), "o", "r", at, "s", task="t2"),
            DecisionRow("x_pick", ("x_pending", "x_pick"), "o", "r", at, "s", task="t1")]
    path = present._decisions(rows)
    assert [d.task_id for d in path] == ["t1", "t2", "t1"]
    assert present._traversed(path) == (("a_receive", "a_collect"), ("x_pending", "x_pick"))


def test_the_proposal_step_shows_the_amount_before_and_after(ran):
    """[代碼審 r1 t6] 寫好建議那一步帶「原預算 → 建議預算」那一組根據(M34:原本丟掉也測不出來)。"""
    tmp_path, _ = ran
    f2 = next(s for s in _state(tmp_path).scenarios if s.code is ScenarioCode.F2)
    step = next(d for d in f2.path if d.taken_edge == ("a_worth", "a_propose"))
    assert any(b.observed == "100 → 110" for b in step.basis)


def test_nothing_is_current_when_the_demo_is_not_running(ran):
    """[代碼審 r1 s2/p7] 沒在跑就不給「現在進度」(展示狀態庫裡的目前節點是上一次留下的);
    在跑才給。"""
    tmp_path, _ = ran
    assert _state(tmp_path, running=False).current is None
    assert _state(tmp_path, running=True).current is not None


# ---- 代碼審 r2(Phase 12 增量 2)----
def test_a_write_let_through_by_a_person_shows_its_failed_check_on_the_page():
    """[代碼審 r2 d1] 人確認後放行的那一筆照「判斷」顯示,頁面上看得到「超過總上限,人確認後放行」
    那一組根據(原本歸成狀態前進,根據整組藏掉)。"""
    from dataclasses import replace

    from rtb.demo import present
    from rtb.demo.page import render_page
    from rtb.demo.state import DecisionKind
    from rtb.demo.state_store import Basis, BasisCode, DecisionRow
    from tests.demo.sample_data import make_demo_state

    over = Basis("已經加出去 120,加上這次共 130", "全部廣告加起來的總上限 124",
                 "超過總上限,人確認後放行", "執行端當下記下", BasisCode.TOTAL_OVER)
    (decision,) = present._decisions([DecisionRow(
        "x_write", ("x_total", "x_write"), "o", "r", datetime.now(UTC), "s", (over,), "k1",
        "程式", "t1")])
    assert decision.kind is DecisionKind.JUDGEMENT
    state = make_demo_state()
    first = replace(state.scenarios[0], path=(decision,), traversed_edges=())
    html = render_page(replace(state, scenarios=(first, *state.scenarios[1:])),
                       form_token="t", refresh_tick=1,  # noqa: S106 - 測試值
                       selected=ScenarioCode.F1)
    assert "人確認後放行" in html


def test_the_known_limits_follow_the_demo_f7_size(monkeypatch):
    """[代碼審 r2 d2] 已知限制裡的 F7 規模取自驅動程式的常數,不寫死。"""
    from rtb.demo import driver, present

    monkeypatch.setattr(driver, "F7_CAMPAIGNS", 40)
    monkeypatch.setattr(driver, "F7_WORKERS", 3)
    assert any("40 個廣告、3 個工作者" in line for line in present.known_limits())


def test_where_a_result_came_from_is_what_that_demo_was(tmp_path):
    """[代碼審 r2 g2/x1] 出處照結果所屬那一次展示記下的種類判定,不靠比對展示編號猜:第二次完整
    展示在跑時標完整執行;從沒跑過完整展示時的單一重跑標單一情境重跑。"""
    writer = StateWriter(tmp_path / "state.db", "rerun-1")
    writer.record_demo(full=False)
    writer.start_scenario("F1", datetime.now(UTC))
    full = StateWriter(tmp_path / "state.db", "full-2")
    full.record_demo(full=True)
    full.start_scenario("F2", datetime.now(UTC))
    reader = StateReader(tmp_path / "state.db")
    try:
        rerun = build_demo_state(reader, "rerun-1", running=True, now=datetime.now(UTC))
        second = build_demo_state(reader, "full-2", running=True, now=datetime.now(UTC),
                                  full_demo_id="full-1", running_full=True)
    finally:
        reader.close()
    assert rerun.scenarios[0].source_full is False
    assert second.scenarios[1].source_full is True


# ---- 增量 3:前後比較表 ----
def test_the_comparison_comes_from_the_same_full_run_as_the_verifier(tmp_path):
    """[S1041] 頁面的前後比較取自跟自動查核同一次完整執行,說明後面標明取自哪一次、產生花了幾秒;完整
    展示在跑、還沒產生時是空的。"""
    from rtb.demo.state_store import ComparisonRun

    writer = StateWriter(tmp_path / "state.db", "full-1")
    writer.record_comparison(ComparisonRun(
        "full-1", datetime.now(UTC),
        (("只填已完成", "pytest 結束代碼 0:2 passed", "擋下:缺 result"),),
        "比的是有沒有機械驗證", 12.3))
    reader = StateReader(tmp_path / "state.db")
    try:
        shown = build_demo_state(reader, "rerun-2", running=False, now=datetime.now(UTC),
                                 verifier_demo_id="full-1", full_demo_id="full-1")
        running = build_demo_state(reader, "full-3", running=True, now=datetime.now(UTC),
                                   verifier_demo_id="full-3", running_full=True)
    finally:
        reader.close()
    assert [(r.forgery, r.without_verifier, r.with_verifier) for r in shown.comparison.rows] == [
        ("只填已完成", "pytest 結束代碼 0:2 passed", "擋下:缺 result")]
    assert shown.comparison.note.startswith("比的是有沒有機械驗證")
    assert "展示編號 full-1" in shown.comparison.note and "12 秒" in shown.comparison.note
    assert running.comparison is None
