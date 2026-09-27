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
from tests.analyzer.test_policy import FULL  # 帶齊一份平穩的四查詢(Phase 14:九條要它才提案)


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
                                 allowed=policy.ValidatedCells.NONE, queries=FULL)
    row = TaskRow(task_id="t1", seq=4, state=TaskState.PROPOSED, campaign_id="c1",
                  proposal=decision.proposal, error_detail=None, written_at=NOW)
    fresh, pace, worth, amount = basis.analysis(_task(), evidence, row, None, queries=FULL)
    assert "3.0 分鐘" in fresh.observed
    assert f"{policy.MAX_EVIDENCE_AGE.total_seconds() / 60:.0f} 分鐘" in fresh.standard
    assert f"{policy.UNDERPACING_THRESHOLD:.0%}" in pace.standard
    assert "曝光 500" in worth.observed
    assert "100 → 110" in amount.observed


@pytest.mark.parametrize("changed", ["state", "budget"])
def test_a_proposal_that_differs_from_the_recomputed_one_gets_no_basis(changed):
    evidence = (_state(100, "active"), _metrics(1.0, 500, 12, 1, 5.0))
    decision, _ = policy.explain(_task(), evidence, NOW, candidate=None,
                                 allowed=policy.ValidatedCells.NONE, queries=FULL)
    proposal = decision.proposal
    if changed == "budget":
        from types import MappingProxyType
        proposal = replace(proposal, requested_change=MappingProxyType({"new_budget": 150}))
    row = TaskRow(task_id="t1", seq=4,
                  state=TaskState.PROPOSED if changed == "budget" else TaskState.NO_ACTION,
                  campaign_id="c1", proposal=proposal if changed == "budget" else None,
                  error_detail=None, written_at=NOW)
    assert basis.analysis(_task(), evidence, row, None, queries=FULL) == ()


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
        # 每一步都標重算(原本「交給誰判斷」那一步是固定說明,2026-09-27 隨候選分支撤除)
        assert all(step.basis.source == basis.RECOMPUTED for step in route)
        checked += 1
    assert checked > 100


def test_a_slow_campaign_goes_straight_to_the_nine_rules():
    """[裁定條件三] 花得偏慢直接交給程式規則(九條),中間沒有「交給誰判斷」那一步,也不寫得像 AI 判過
    (2026-09-27 撤 Phase 10 候選分支;原本這一步是固定說明「分析端目前沒有模型入口」)。"""
    evidence = (_state(100, "active"), _metrics(1.0, 500, 12, 1, 5.0))
    decision, _ = policy.explain(_task(), evidence, NOW, candidate=None,
                                 allowed=policy.ValidatedCells.NONE, queries=FULL)
    row = TaskRow(task_id="t1", seq=4, state=TaskState.PROPOSED, campaign_id="c1",
                  proposal=decision.proposal, error_detail=None, written_at=NOW)
    route = basis.analysis_route(basis.analysis(_task(), evidence, row, None, queries=FULL))
    pairs = [(s.node, s.target) for s in route]
    assert ("a_pacing", "a_rule") in pairs and ("a_rule", "a_worth") in pairs, pairs
    assert not any("a_route" in pair or "a_candidate" in pair for pair in pairs), pairs
    assert not any("AI" in s.basis.conclusion for s in route)


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


# ---- 協調者 2026-09-24 裁定:系統當下記下的資料能補的根據都補,依實際來源標 ----
def test_delivery_counts_come_from_the_inbox_and_its_own_limit():
    """投遞次數是收件口當下記下的;上限讀收件口的常數,不寫死。"""
    from rtb.executor import inbox_store

    (dead,) = basis.lifecycle("dead_lettered", deliveries=inbox_store.MAX_DELIVERIES,
                              reason="delivery_limit", actor=None)
    assert f"最多 {inbox_store.MAX_DELIVERIES} 次" in dead.standard
    assert dead.source == basis.RECORDED_INBOX
    (again,) = basis.lifecycle("reclaimed", deliveries=2, reason=None, actor="w2")
    assert "換人接手" in again.observed and "交出去 2 次" in again.observed
    assert "這是第" not in again.observed  # 接手不會多算一次投遞(代碼審 r1 d10)
    (put_back,) = basis.lifecycle("lease_released", deliveries=1, reason="dsp_unavailable",
                                  actor="w1")
    assert "讀不到平台" in put_back.observed
    (replayed,) = basis.lifecycle("replay_requeued", deliveries=None, reason=None,
                                  actor="demo-operator")
    assert "demo-operator" in replayed.observed
    assert basis.lifecycle("delivered", deliveries=1, reason=None, actor="w1") == ()


def test_a_stop_for_confirmation_shows_the_recorded_totals():
    (found,) = basis.stopped(amount=10, used=120, cap=124)
    assert "120" in found.observed and "10" in found.observed and "124" in found.standard
    assert found.source == basis.RECORDED_INBOX
    assert basis.stopped(amount=10, used=None, cap=124) == ()  # 沒記的不補 0


def test_a_follow_up_shows_its_reason_and_generation_against_the_limit():
    from rtb.analyzer.task_store import MAX_GENERATION, ReplanReason

    (found,) = basis.follow_up(ReplanReason.VERSION_CHANGED, generation=2)
    assert "第 2 代" in found.observed and f"最多 {MAX_GENERATION} 代" in found.standard
    assert found.source == basis.RECORDED_ANALYZER


def test_a_resend_shows_the_lookup_that_found_nothing():
    calls = [_call("lookup_operation", "client_error", 404, "2026-09-24T00:00:05Z")]
    (found,) = basis.platform_call(calls, "in_flight", "2026-09-24T00:00:06Z")
    assert "依編號查平台" in found.observed and "404" in found.observed
    assert "同編號" in found.conclusion


# ---- 代碼審 r1(Phase 12 增量 2)----
def _proposed(evidence):
    decision, _ = policy.explain(_task(), evidence, NOW, candidate=None,
                                 allowed=policy.ValidatedCells.NONE, queries=FULL)
    return TaskRow(task_id="t1", seq=4, state=TaskState.PROPOSED, campaign_id="c1",
                   proposal=decision.proposal, error_detail=None, written_at=NOW)


def test_the_basis_uses_only_the_rules_public_steps():
    """[代碼審 r1 a1] 展示端不呼叫分析端規則的私有函式,也不自己另算一次配速:中間事實全部取自正式
    規則公開的 steps。"""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(basis))
    private = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
               and isinstance(node.value, ast.Name) and node.value.id == "policy"
               and node.attr.startswith("_")]
    assert private == []
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names}
    assert "pacing" not in imported


def test_every_intermediate_conclusion_matches_the_rules_own_steps():
    """[代碼審 r1 d8] 全部樣本逐組核對:新鮮度、配速、值不值得加的結論代碼,等於正式規則 steps 的
    中間判定(不只比最後一組)。"""
    from rtb.demo.state_store import BasisCode
    from rtb.domain.worth import WorthVerdict

    checked = 0
    for case, row, reason in _decidable():
        found = {b.code: b for b in basis.analysis(case.task, case.evidence, row, reason)}
        facts = policy.steps(case.evidence, row.written_at)
        assert (BasisCode.FRESH in found) is facts.fresh
        assert (BasisCode.STALE in found) is (not facts.fresh)
        if facts.settled_by_base:  # 九條第 1/2 條排在配速之前(Phase 14):只給那一組,不給配速
            assert BasisCode.UNDERPACING not in found and BasisCode.NOT_UNDERPACING not in found
            assert found[basis.BasisCode.INSUFFICIENT if facts.worth is WorthVerdict.INSUFFICIENT
                         else basis.BasisCode.NOT_WORTH].standard == basis.BASE_RULE_STANDARD
        elif facts.underpacing is not None and facts.fresh and facts.metrics is not None:
            assert (BasisCode.UNDERPACING in found) is facts.underpacing
        if facts.worth is not None:
            assert (BasisCode.WORTH in found) is (facts.worth is WorthVerdict.WORTH)
        checked += 1
    assert checked > 100


def test_campaign_status_decides_first_and_the_standard_says_so():
    """[代碼審 r1 d1] 標準文字只寫規則真的做的事、不寫跟平台比版本。Phase 14 改寫:正式規則是九條,
    第 1 條先看狀態——暫停中的廣告就算有曝光點擊也在配速之前判不值得加,根據照實寫第 1/2 條;
    啟用中、帶齊四查詢才判到值得加。"""
    metrics = _metrics(1.0, 500, 12, 1, 5.0)
    active = basis.analysis(_task(), (_state(100, "active"), metrics),
                            _proposed((_state(100, "active"), metrics)), None, queries=FULL)
    paused_row = TaskRow(task_id="t1", seq=4, state=TaskState.NO_ACTION, campaign_id="c1",
                         proposal=None, error_detail=None, written_at=NOW)
    paused = basis.analysis(_task(), (_state(100, "paused"), metrics), paused_row,
                            "judged_not_worth", queries=FULL)
    assert [b.conclusion for b in active][-1] == basis.PROPOSE
    assert len(paused) == 2 and paused[1].standard == basis.BASE_RULE_STANDARD
    assert "暫停" in paused[1].observed or "paused" in paused[1].observed
    assert all("啟用" not in b.standard for b in active)
    assert "版本沒變" not in paused[0].standard and "同一批資料" in paused[0].standard
    assert active[2].standard == basis.NINE_RULES_STANDARD


def test_the_standards_follow_the_live_constants(monkeypatch):
    """[代碼審 r1 t6] 根據的標準讀正式規則當下的常數:改了常數,文字跟著變(不是抄死的數字)。"""
    from datetime import timedelta as delta

    from rtb.executor import guardrails, inbox_store
    from rtb.executor.attempt_store import FirstRow
    from tests.analyzer.conftest import make_proposal

    evidence = (_state(100, "active", timedelta(minutes=3)), _metrics(1.0, 500, 12, 1, 5.0))
    monkeypatch.setattr(policy, "MAX_EVIDENCE_AGE", delta(minutes=17))
    monkeypatch.setattr(policy, "BUDGET_INCREASE_FRACTION", 0.2)
    row = _proposed(evidence)  # 當時的決策也照改過的常數(重算要跟它一致才給根據)
    found = basis.analysis(_task(), evidence, row, None, queries=FULL)
    assert "17 分鐘" in found[0].standard and "加 20%" in found[-1].standard
    monkeypatch.setattr(inbox_store, "MAX_DELIVERIES", 9)
    (dead,) = basis.lifecycle("dead_lettered", deliveries=9, reason=None, actor=None)
    assert "最多 9 次" in dead.standard
    monkeypatch.setattr(guardrails, "MAX_INCREASE_DENOMINATOR", 3)
    first = FirstRow(key="k", task_id="t1", revision=1, campaign_id="c1", tenant="t",
                     reserved_amount=10, ratio_allowance=33, max_budget=10**6,
                     aggregate_limit=124, used_before=0, proposal=make_proposal(),
                     snapshot_matches_key=True, written_at="2026-09-24T00:00:00Z")
    assert "1/3" in basis.write_start(first)[0].standard


def test_wording_matches_what_actually_happened():
    """[代碼審 r1 d10] 依編號查平台回 404 是「查不到這一筆」不是拒絕;單次上限寫「至少 1」;
    (原本還斷言「交給誰判斷」是固定說明,那一步 2026-09-27 撤除。)"""
    lookup = [_call("lookup_operation", "client_error", 404, "2026-09-24T00:00:05Z")]
    (found,) = basis.platform_call(lookup, "in_flight", "2026-09-24T00:00:06Z")
    assert "查不到這一筆" in found.observed and "拒絕" not in found.observed


def test_a_capped_stop_record_gives_no_numbers():
    """[代碼審 r1 d5] 停下紀錄的已用或門檻被封頂(不是原值):不給這組根據。"""
    assert basis.stopped(amount=10, used=120, cap=124, capped=True) == ()
    assert basis.stopped(amount=10, used=120, cap=124)
