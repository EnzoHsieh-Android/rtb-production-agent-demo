"""判斷的根據(Phase 12 增量 2b):量到的值、標準、結論。分析端不存根據,用存下的證據呼叫正式規則的
同一批函式與常數重算(協調者 2026-09-24 裁定),重算的結論要跟當時實際的判定一致,對不上的就
不顯示;執行端的根據用它當下記下的核對材料。"""

import itertools
from dataclasses import replace
from datetime import timedelta

import pytest

from rtb.analyzer import policy
from rtb.analyzer.task_store import TaskRow
from rtb.demo import basis
from rtb.domain.task_state import TaskState
from tests.analyzer.policy_before_samples import NOW, _metrics, _state, _task, cases


def _decided(case):
    """照正式決策函式的結果造出「下一列」,等於當時實際寫下的那一列。"""
    decision, reason = policy.explain(case.task, case.evidence, case.now, candidate=None,
                                      allowed=policy.ValidatedCells.NONE)
    if isinstance(decision, policy.ProposalDecision):
        state, proposal = TaskState.PROPOSED, decision.proposal
    elif isinstance(decision, policy.NeedsFreshEvidence):
        state, proposal = TaskState.COLLECTING_EVIDENCE, None
    else:
        state, proposal = TaskState.NO_ACTION, None
    row = TaskRow(task_id="t1", seq=4, state=state, campaign_id="c1", proposal=proposal,
                  error_detail=None, written_at=case.now)
    return row, None if reason is None else reason.value


def _decidable():
    for case in cases():
        if case.task is None:
            continue
        try:
            yield case, *_decided(case)
        except Exception:  # noqa: S112 - 正式決策對這筆丟例外:沒有下一列,不在比對範圍
            continue


def test_every_recomputed_basis_agrees_with_what_the_rule_decided():
    """[條件二] 拿分析端決策的全部樣本:重算得出根據的,最後一組的結論要跟當時實際判定同一類;
    重算不出的(例如缺資料)不給,不造數字。每一組都標明是重算的。"""
    checked = 0
    for case, row, reason in _decidable():
        found = basis.analysis(case.task, case.evidence, row, reason)
        assert found, (row.state, reason)
        assert all(item.source == basis.RECOMPUTED for item in found)
        assert found[-1].conclusion == basis.expected_conclusion(row.state, reason)
        checked += 1
    assert checked > 100


def test_a_basis_that_disagrees_with_what_was_recorded_is_not_shown():
    """[條件二] 存下的結果跟重算對不上(例如當時記的是不調整、重算卻會提案):這一組根據不顯示。"""
    evidence = (_state(100, "active"), _metrics(1.0, 500, 12, 1, 5.0))
    row = TaskRow(task_id="t1", seq=4, state=TaskState.NO_ACTION, campaign_id="c1",
                  proposal=None, error_detail=None, written_at=NOW)
    assert basis.analysis(_task(), evidence, row, "not_underpacing") == ()


def test_the_numbers_come_from_the_rule_itself():
    """[條件一] 數字取自正式規則的常數與函式:新鮮度上限、配速門檻、加額比例。"""
    evidence = (_state(100, "active", timedelta(minutes=3)), _metrics(1.0, 500, 12, 1, 5.0))
    decision, _ = policy.explain(_task(), evidence, NOW, candidate=None,
                                 allowed=policy.ValidatedCells.NONE)
    row = TaskRow(task_id="t1", seq=4, state=TaskState.PROPOSED, campaign_id="c1",
                  proposal=decision.proposal, error_detail=None, written_at=NOW)
    fresh, pace, worth, amount = basis.analysis(_task(), evidence, row, None)
    assert "3.0 分鐘" in fresh.observed
    assert f"{policy.MAX_EVIDENCE_AGE.total_seconds() / 60:.0f} 分鐘" in fresh.standard
    assert f"{policy.UNDERPACING_THRESHOLD:.0%}" in pace.standard
    assert "曝光 500" in worth.observed
    assert "100 → 110" in amount.observed


@pytest.mark.parametrize("changed", ["state", "budget"])
def test_a_proposal_that_differs_from_the_recomputed_one_gets_no_basis(changed):
    evidence = (_state(100, "active"), _metrics(1.0, 500, 12, 1, 5.0))
    decision, _ = policy.explain(_task(), evidence, NOW, candidate=None,
                                 allowed=policy.ValidatedCells.NONE)
    proposal = decision.proposal
    if changed == "budget":
        from types import MappingProxyType
        proposal = replace(proposal, requested_change=MappingProxyType({"new_budget": 150}))
    row = TaskRow(task_id="t1", seq=4,
                  state=TaskState.PROPOSED if changed == "budget" else TaskState.NO_ACTION,
                  campaign_id="c1", proposal=proposal if changed == "budget" else None,
                  error_detail=None, written_at=NOW)
    assert basis.analysis(_task(), evidence, row, None) == ()


def test_the_write_start_basis_is_what_the_executor_recorded():
    """執行端開始一筆時記下的核對材料:單次加額上限、單一廣告上限、總上限與已用額度。"""
    from rtb.executor.attempt_store import FirstRow
    from tests.analyzer.conftest import make_proposal

    first = FirstRow(key="k", task_id="t1", revision=1, campaign_id="c1", tenant="t",
                     reserved_amount=10, ratio_allowance=50, max_budget=1000, aggregate_limit=124,
                     used_before=110, proposal=make_proposal(), snapshot_matches_key=True,
                     written_at="2026-09-24T00:00:00Z")
    found = basis.write_start(first)
    assert [b.source for b in found] == [basis.RECORDED] * 3
    ratio, cap, total = found
    assert "10" in ratio.observed and "50" in ratio.standard
    assert "1000" in cap.standard
    assert "110" in total.observed and "124" in total.standard
    missing = replace(first, aggregate_limit=None, used_before=None)
    assert len(basis.write_start(missing)) == 2  # 舊列沒記的那一組不給,不補 0


def _call(kind, result, status, at, key="k"):
    from rtb.executor.attempt_store import DspCallRow

    return DspCallRow(1, at, kind, result, status, None, 5.0, "t1", 1, "c1", key, "executor_loop",
                      None, "v", None)


def test_an_unknown_write_shows_the_last_exchange_with_the_platform():
    """執行端記的平台呼叫:轉成不明那一列帶最近一次寫入的結果(逾時沒回覆);之後的呼叫不算。"""
    calls = [_call("write", "timeout", None, "2026-09-24T00:00:01Z"),
             _call("lookup_operation", "responded", 200, "2026-09-24T00:00:09Z")]
    (found,) = basis.platform_call(calls, "unknown", "2026-09-24T00:00:02Z")
    assert "逾時" in found.observed and found.source == basis.RECORDED
    (later,) = basis.platform_call(calls, "committed_unverified", "2026-09-24T00:00:10Z")
    assert "查平台" in later.observed and "200" in later.observed
    assert basis.platform_call(calls, "unknown", "2026-09-24T00:00:00Z") == ()


def test_a_different_no_action_reason_gets_no_basis():
    """[條件二] 當時記的不調整原因跟重算的不一樣(重算是沒有花太慢、記的是不值得加):不顯示。"""
    evidence = (_state(100, "active"), _metrics(10**6, 500, 12, 1, 5.0))
    row = TaskRow(task_id="t1", seq=4, state=TaskState.NO_ACTION, campaign_id="c1",
                  proposal=None, error_detail=None, written_at=NOW)
    assert basis.analysis(_task(), evidence, row, "not_underpacing")
    assert basis.analysis(_task(), evidence, row, "judged_not_worth") == ()


def test_recomputed_basis_says_so_in_the_agreed_words():
    """[條件三] 顯示時標明是依存下的證據重算(協調者指定的字樣),跟執行端當下記下的分得開。"""
    assert basis.RECOMPUTED == "依存下的證據重算"
    assert basis.RECORDED != basis.RECOMPUTED


# ---- 協調者 2026-09-24 裁定:分析端的中間判斷點用重算補上 ----
_END = {TaskState.PROPOSED: "a_propose", TaskState.NO_ACTION: "a_no_action",
        TaskState.COLLECTING_EVIDENCE: "a_recollect"}


def test_the_filled_in_route_is_real_edges_and_ends_where_the_rule_ended():
    """[裁定條件二] 全部樣本:補上的中間判斷點每一步都是正式圖的邊、一步接一步從新鮮度判斷出發,
    終點跟當時實際結果一樣;每一步都標重算。"""
    from rtb.demo.flow import FLOW_GRAPH

    edges = {(e.source, e.target) for e in FLOW_GRAPH.edges}
    checked = 0
    for case, row, reason in _decidable():
        route = basis.analysis_route(basis.analysis(case.task, case.evidence, row, reason))
        pairs = [(step.node, step.target) for step in route]
        assert pairs[0][0] == "a_fresh" and pairs[-1][1] == _END[row.state], pairs
        assert all(pair in edges for pair in pairs), pairs
        assert all(a[1] == b[0] for a, b in itertools.pairwise(pairs))
        assert all(step.basis.source == basis.RECOMPUTED for step in route)
        checked += 1
    assert checked > 100


def test_the_routing_step_says_there_is_no_model_entry():
    """[裁定條件三] 「交給誰判斷」那一步標程式規則,理由寫分析端目前沒有模型入口,
    不寫得像 AI 判過。"""
    evidence = (_state(100, "active"), _metrics(1.0, 500, 12, 1, 5.0))
    decision, _ = policy.explain(_task(), evidence, NOW, candidate=None,
                                 allowed=policy.ValidatedCells.NONE)
    row = TaskRow(task_id="t1", seq=4, state=TaskState.PROPOSED, campaign_id="c1",
                  proposal=decision.proposal, error_detail=None, written_at=NOW)
    route = basis.analysis_route(basis.analysis(_task(), evidence, row, None))
    step = next(s for s in route if s.node == "a_route")
    assert step.target == "a_rule" and "沒有模型入口" in step.basis.observed
    assert "AI" not in step.basis.conclusion


def test_no_route_is_filled_in_when_the_basis_was_withheld():
    assert basis.analysis_route(()) == ()


def test_the_write_checks_are_filled_in_only_when_recorded_and_passed():
    """[裁定條件四] 執行端的判斷點:開始一筆那一列有記錄、而且都通過的才補(範圍 → 總上限 → 寫入)。"""
    from rtb.executor.attempt_store import FirstRow
    from tests.analyzer.conftest import make_proposal

    first = FirstRow(key="k", task_id="t1", revision=1, campaign_id="c1", tenant="t",
                     reserved_amount=10, ratio_allowance=50, max_budget=10**6,
                     aggregate_limit=124, used_before=110, proposal=make_proposal(),
                     snapshot_matches_key=True, written_at="2026-09-24T00:00:00Z")
    route = basis.write_route(basis.write_start(first))
    assert [(s.node, s.target) for s in route] == [("x_guard", "x_total"), ("x_total", "x_write")]
    assert len(route[0].more) == 1
    assert basis.write_route(basis.write_start(replace(first, used_before=None))) == ()
    assert basis.write_route(basis.write_start(replace(first, used_before=120))) == ()
