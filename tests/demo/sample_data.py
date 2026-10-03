# ruff: noqa: RUF001, S106, S108
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from rtb.demo.flow import FLOW_GRAPH
from rtb.demo.page import render_approval, render_page, render_report
from rtb.demo.state import (
    ApprovalForm,
    ChangeSummary,
    Comparison,
    ComparisonRow,
    CurrentStep,
    Decision,
    DecisionBasis,
    DemoState,
    Disposition,
    DspCampaign,
    DspState,
    FlowEdge,
    FlowGraph,
    Hypothesis,
    InjectedFault,
    ModelMode,
    ModelSource,
    ModelStep,
    NodeKind,
    Scenario,
    ScenarioCode,
    ScenarioStatus,
    Stage,
    TimelineStep,
    VerifierResult,
)

NOW = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)
SCENARIO_TITLES = {
    ScenarioCode.F1: "平台沒回應，先查是否成功",
    ScenarioCode.F2: "中途突然停止，重啟後接著做",
    ScenarioCode.F3: "同一工作送兩次，只做一次",
    ScenarioCode.F4: "資料已更新，舊調整不再執行",
    ScenarioCode.F5: "廣告名稱藏指令，系統不受騙",
    ScenarioCode.F6: "多次失敗後交給人工",
    ScenarioCode.F7: "全帳戶加預算超過上限，交人確認",
}
SCENARIO_TESTS = {
    ScenarioCode.F1: (
        "廣告平台沒有回應時，回頭查同一筆操作是否已成功；查清楚前不當成成功，也不再送一次。"
    ),
    ScenarioCode.F2: (
        "程式執行到一半突然中斷時，重新啟動後從上次完成的地方接著做，不重做已完成的動作。"
    ),
    ScenarioCode.F3: "同一筆工作就算送來兩次，也只會分析一次、寫入一次，不會多花錢。",
    ScenarioCode.F4: "廣告資料在判斷後又被更新時，系統會停止原本那次調整，避免蓋掉新資料。",
    ScenarioCode.F5: "廣告名稱藏著要求系統違規的文字時，系統仍只把名稱當資料，不會照著做。",
    ScenarioCode.F6: "同一筆工作嘗試多次仍失敗時，先轉給人工；確認後再從一般流程重新檢查。",
    ScenarioCode.F7: (
        "多筆調整合計超過整個客戶帳戶這段期間加預算的總上限時，"
        "先交由人確認，再重新排隊檢查。"
    ),
}


def make_flow(*, cyclic: bool = False) -> FlowGraph:
    """正式圖是唯一流程來源。循環圖只供驗證測試使用。"""
    if cyclic:
        return FlowGraph(
            FLOW_GRAPH.nodes,
            (*FLOW_GRAPH.edges, FlowEdge("x_done", "a_receive", "錯誤回圈")),
        )
    return FLOW_GRAPH


def _decision(node: str, target: str, index: int) -> Decision:
    edge = next(item for item in FLOW_GRAPH.edges if item.source == node and item.target == target)
    label = next(item.label.replace("模型", "AI") for item in FLOW_GRAPH.nodes if item.id == node)
    outcome = edge.label.replace("模型", "AI")
    explanations = {
        ("p_reply", "x_unknown"): "廣告平台沒有明確回覆，寫入是否成功還不能下結論。",
        ("x_unknown", "x_verify"): "查到同一筆操作已寫入，先核對平台的實際預算。",
        ("x_write", "x_reclaimed"): "處理者中途停止；等處理權到期後由另一位接手。",
        ("i_check", "i_superseded"): "同一工作已有更新的建議，這份舊建議不再排入寫入。",
        ("x_precheck", "x_blocked"): "平台資料版本已改變，舊建議不能寫入。",
        ("x_pick", "x_deadletter"): "已嘗試多次，停止自動處理並交給人。",
        ("h_replay", "r_requeued"): "人同意重送同一份建議，重新排隊前仍要走一般檢查。",
        ("x_total", "x_blocked"): "這次加額會碰到帳戶總上限，停止寫入。",
        ("x_total", "x_wait_approval"): "這次加額會超過帳戶總上限，先請人確認。",
        ("h_approve", "x_approved"): "人已確認這次加額，先放回排隊重新檢查。",
    }
    measurements = {
        "a_fresh": ("資料是 3 分鐘前的", "上限 15 分鐘", "在時限內"),
        "a_complete": ("廣告狀態與花費資料各 1 份", "兩種資料都要有", "資料齊全"),
        "a_pacing": ("預算花了 42%", "此時預期 60%", "落後 18 個百分點"),
        "a_worth": ("建議 500→600 元", "單次上限 +25%", "+20%，在上限內"),
        "i_check": ("這份建議版本 7", "收件紀錄最新版本 7", "可收下"),
        "x_pick": ("已嘗試 1 次", "最多 3 次", "仍可處理"),
        "x_precheck": ("廣告資料版本 7", "建議依據版本 7", "版本相同"),
        "x_guard": ("建議 500→600 元", "單次上限 +25%", "+20%，在上限內"),
        "x_total": ("本次加額 100 元", "帳戶剩餘額度 200 元", "沒有超過總上限"),
        "p_reply": ("平台回覆 1 次", "需要明確成功回覆", "改查平台紀錄"),
        "x_unknown": ("操作鍵查到 1 次套用", "同一鍵最多套用 1 次", "不再重送"),
        "x_verify": ("平台預算 600 元", "建議預算 600 元", "兩者相同"),
        "h_replay": ("人工確認 1 次", "需要人工同意", "同意重新排隊"),
        "h_approve": ("人工確認 1 次", "確認後仍須重新檢查", "同意重新排隊"),
    }
    case_measurements = {
        ("i_check", "i_superseded"): ("這份建議版本 7", "收件紀錄最新版本 8", "舊建議停止"),
        ("x_precheck", "x_blocked"): ("廣告資料版本 8", "建議依據版本 7", "版本不同，停止寫入"),
        ("x_pick", "x_deadletter"): ("已嘗試 3 次", "最多 3 次", "停止自動處理"),
        ("x_total", "x_blocked"): ("本次加額 100 元", "帳戶剩餘額度 50 元", "超出總上限"),
        ("x_total", "x_wait_approval"): (
            "本次加額 100 元", "帳戶剩餘額度 50 元", "超過總上限，等待人確認"
        ),
        ("p_reply", "x_verify"): ("平台明確回覆成功", "需要明確成功回覆", "核對實際預算"),
    }
    observed, standard, conclusion = case_measurements.get(
        (node, target), measurements[node]
    )
    return Decision(
        node=node,
        taken_edge=(node, target),
        outcome=outcome,
        reason=explanations.get((node, target), f"{label}；這次結果是「{outcome}」。"),
        at=NOW + timedelta(seconds=index),
        basis=(DecisionBasis(observed, standard, conclusion),),
    )


def _route(*nodes: str) -> tuple[tuple[tuple[str, str], ...], tuple[Decision, ...]]:
    edges = {(item.source, item.target) for item in FLOW_GRAPH.edges}
    pairs = tuple(pairwise(nodes))
    if not all(pair in edges for pair in pairs):
        raise ValueError("示範路徑必須逐步走正式圖的邊")
    decisions = tuple(
        _decision(source, target, index)
        for index, (source, target) in enumerate(pairs, start=1)
        if next(item for item in FLOW_GRAPH.nodes if item.id == source).kind is NodeKind.DECISION
    )
    return pairs, decisions


_CASE_FACTS: dict[ScenarioCode, tuple[Stage, str, tuple[Disposition, ...], tuple[str, ...]]] = {
    ScenarioCode.F1: (
        Stage.RECONCILE, "查明同一筆操作已寫入，沒有再次送出",
        (Disposition("平台回覆", "RESULT_UNKNOWN", "先回查，確認成功後才結案。"),),
        ("平台沒有明確回覆", "回查同一筆操作，僅找到一次預算寫入"),
    ),
    ScenarioCode.F2: (
        Stage.REPLAY, "處理者中斷，工作等待另一位重新領取",
        (), ("處理者中斷後沒有第二筆寫入", "同一份建議等待重新領取"),
    ),
    ScenarioCode.F3: (
        Stage.BLOCK, "舊建議在收件時被攔下",
        (), ("先前的建議已寫入一次", "重複送來的舊建議沒有再寫入"),
    ),
    ScenarioCode.F4: (
        Stage.BLOCK, "寫入前讀到新的資料版本，停止舊調整",
        (Disposition("擋下原因", "STALE_VERSION", "舊建議沒有寫入，另開工作重新分析。"),),
        ("廣告資料版本由 7 變成 8", "舊建議沒有寫入"),
    ),
    ScenarioCode.F5: (
        Stage.COMPLETE, "廣告名稱中的可疑指令只當資料，照程式規則判斷",
        (), ("可疑文字沒有取得權限", "依程式規則完成一次寫入"),
    ),
    ScenarioCode.F6: (
        Stage.REPLAY, "多次失敗後停下，經人工決定才重新排隊",
        (Disposition("失敗處理", "DEAD_LETTER", "停止自動嘗試，等人決定。"),),
        ("多次失敗，這一輪沒有寫入", "人工同意後同一份建議重新排隊"),
    ),
    ScenarioCode.F7: (
        Stage.APPROVAL, "帳戶加額超過總上限，經人工確認後重新排隊",
        (Disposition("處理待確認的結果", "APPROVAL_REQUIRED",
                     "超過總上限，經人工確認後重新排隊。"),),
        ("加額超過整個帳戶的總上限", "人工確認後放回排隊；這一輪沒有寫入"),
    ),
}

_FAULTS = {
    ScenarioCode.F1: InjectedFault("p_reply", "讓廣告平台回覆逾時"),
    ScenarioCode.F2: InjectedFault("x_write", "讓處理程式寫入中途停止"),
    ScenarioCode.F3: InjectedFault("i_check", "讓兩位工作者同時搶同一份建議"),
    ScenarioCode.F4: InjectedFault("x_precheck", "在寫入前更新廣告資料版本"),
    ScenarioCode.F5: InjectedFault("a_collect", "在廣告名稱放入誤導指令"),
    ScenarioCode.F6: InjectedFault("x_pick", "讓投遞持續失敗直到次數上限"),
    ScenarioCode.F7: InjectedFault("x_total", "讓帳戶可加額度只剩 50 元"),
}

_CHANGES = {
    ScenarioCode.F1: ChangeSummary("廣告-001", 100, 110, True),
    ScenarioCode.F2: ChangeSummary("廣告-002", 100, 110, True),
    ScenarioCode.F3: ChangeSummary("廣告-003", 100, 100, False, "重複的舊建議在收件時停止"),
    ScenarioCode.F4: ChangeSummary("廣告-004", 100, 100, False, "寫入前資料版本已更新"),
    ScenarioCode.F5: ChangeSummary("廣告-005", 100, 110, True),
    ScenarioCode.F6: ChangeSummary("廣告-006", 100, 100, False, "失敗次數已達上限，交給人工"),
    ScenarioCode.F7: ChangeSummary("廣告-007", 100, 100, False, "人工確認後等待重新檢查"),
}


def _scenario(
    code: ScenarioCode,
    status: ScenarioStatus,
    path: tuple[Decision, ...],
    trace: tuple[tuple[str, str], ...],
    result: str,
) -> Scenario:
    final_stage, detail, dispositions, operations = _CASE_FACTS[code]
    return Scenario(
        code=code,
        title=SCENARIO_TITLES[code],
        what_it_tests=SCENARIO_TESTS[code],
        status=status,
        incomplete_reason="展示等待太久，這個情境沒有在期限內跑完"
        if status is ScenarioStatus.INCOMPLETE else None,
        timeline=(
            TimelineStep(Stage.RECEIVE, NOW, "收到廣告「秋季品牌活動」"),
            TimelineStep(final_stage, NOW + timedelta(seconds=len(trace)), detail),
        ),
        dispositions=dispositions,
        dsp=DspState(
            campaigns=(DspCampaign(_CHANGES[code].campaign,
                                   _CHANGES[code].after or 100, 8, "正在投放"),),
            operations=operations,
        ),
        audit=("按正式流程圖記下每一次判斷", detail),
        model_step=ModelStep(
            numbers=(("預算調整", "+10%"), ("客戶帳戶", "示範客戶 A")),
            narrative=("近期帶來的成果穩定，這次小幅增加預算的風險較低。"
                       if code is not ScenarioCode.F5 else None),
            source=ModelSource.RECORDED,
            result_kind="成功",
        ),
        hypothesis=Hypothesis(
            alert="廣告平台回覆變慢",
            hypotheses=("可能是廣告平台回覆較慢，或本機連線暫時壅塞。",),
            next_step="先查廣告平台的操作紀錄，確認是否已成功",
            source=ModelSource.RECORDED,
        ) if code is ScenarioCode.F1 else None,
        path=path,
        current_node="x_precheck" if status is ScenarioStatus.RUNNING else None,
        result_summary=result,
        traversed_edges=trace,
        source_demo_id="2026-09-24-001",
        ran_at=NOW,
        source_full=True,  # 範例的七個情境都取自同一次完整執行
        model_mode=ModelMode.RECORDED,
        change_summary=_CHANGES[code],
        trigger="排程每 15 分鐘檢查一次廣告花費",
        goal="讓花太慢的廣告跟上進度，但單次與全帳戶都不超過上限",
        queue_wait_seconds=(3 + list(ScenarioCode).index(code)
                            if code is not ScenarioCode.F3 else None),
        injected_faults=(_FAULTS[code],),
        operation_key=f"op-demo-{code.value.lower()}-001",
        platform_apply_count=1 if code in {ScenarioCode.F1, ScenarioCode.F2,
                                             ScenarioCode.F3, ScenarioCode.F5} else 0,
    )


def make_demo_state(*, running: bool = False) -> DemoState:
    observed_at = datetime.now(UTC)
    lead = ("a_receive", "a_collect", "a_fresh", "a_complete", "a_pacing")
    # 說明是送出之後的旁支,不在路上(Issues/流程圖把AI說明畫在送出建議之前)
    via_rule = (*lead, "a_rule", "a_worth", "a_propose", "a_submit", "i_check")
    accepted = (*via_rule, "x_pending", "x_pick", "x_precheck", "x_guard", "x_total")
    routes = (
        (*accepted, "x_write", "p_reply", "x_unknown", "x_verify", "x_done"),
        (*accepted, "x_write", "x_reclaimed"),
        (*via_rule, "i_superseded"),
        (*accepted[:13], "x_blocked", "a_followup"),
        (*lead, "a_rule", "a_worth", "a_propose", "a_submit", "i_check",
         "x_pending", "x_pick", "x_precheck", "x_guard", "x_total", "x_write",
         "p_reply", "x_verify", "x_done"),
        (*accepted[:12], "x_deadletter", "h_replay", "r_requeued"),
        (*accepted, "x_wait_approval", "h_approve", "x_approved"),
    )
    statuses = (
        ScenarioStatus.DONE,
        ScenarioStatus.RUNNING if running else ScenarioStatus.DONE,
        ScenarioStatus.DONE,
        ScenarioStatus.DONE,
        ScenarioStatus.DONE,
        ScenarioStatus.DONE,
        ScenarioStatus.DONE,
    )
    results = (
        "查明已寫入；沒有重送同一筆操作",
        "中途停止後，交由下一位處理者重新領取",
        "舊建議被收件檢查攔下，沒有第二次寫入",
        "資料更新；舊建議停止，開新工作重新分析",
        "可疑文字未改變權限；照程式規則完成寫入",
        "自動處理已停；人工同意後，原建議重新排隊",
        "加額超過總上限；人工確認後重新排隊，這一輪沒有寫入",
    )
    scenarios = tuple(
        _scenario(code, status, *_route(*routes[index])[::-1], results[index])
        for index, (code, status) in enumerate(zip(ScenarioCode, statuses, strict=True))
    )
    if running:
        at = routes[1].index("x_precheck")
        trace, decisions = _route(*routes[1][:at + 1])
        scenarios = tuple(
            replace(item, path=decisions, traversed_edges=trace, current_node="x_precheck",
                    change_summary=None, platform_apply_count=None, injected_faults=(),
                    result_summary="執行中：正在做寫入前檢查")
            if item.code is ScenarioCode.F2 else item
            for item in scenarios
        )
    else:
        scenarios = tuple(replace(item, current_node=None) for item in scenarios)
    current_scenario = scenarios[1]
    return DemoState(
        demo_id="2026-09-24-001",
        is_sample=True,
        started_at=NOW,
        model_mode=ModelMode.RECORDED,
        verifier_digest="check-abc123",
        commit="2026-09-24-demo",
        running=running,
        scenarios=scenarios,
        verifier=VerifierResult(
            True, ("七項檢查全部通過", "查核資料與這次程式版本一致"), (),
            NOW, "2026-09-24-001",
        ),
        known_limits=(
            "這次只使用本機模擬的廣告平台，沒有連到正式平台。",
            "這次沒有測量正式網路環境的實際延遲。",
        ),
        comparison=Comparison(
            rows=(ComparisonRow(
                "報告只寫「已完成」，但沒有可核對的紀錄",
                "可能直接相信這句話", "會因找不到實際紀錄而判定不通過",
            ),),
            note="差別在於：系統會不會自動核對原始紀錄，而不是只相信報告文字。",
        ),
        approval=None,
        flow=make_flow(),
        current=CurrentStep(
            scenario=ScenarioCode.F2, node="x_precheck",
            started_at=observed_at - timedelta(seconds=7),
            last_decision=current_scenario.path[-1] if current_scenario.path else None,
        ) if running else None,
        observed_at=observed_at,
        model_mode_reason="沒有開即時開關",
    )


def write_examples() -> None:
    output = Path("/tmp")
    running_html = render_page(
        make_demo_state(running=True), form_token="example-token", refresh_tick=1001
    )
    report_html = render_report(make_demo_state(running=False))
    idle_html = render_page(
        make_demo_state(running=False),
        form_token="example-token",
        refresh_tick=1002,
        selected=ScenarioCode.F3,
    )
    awaiting_state = make_demo_state(running=True)
    waiting_route = (
        "a_receive", "a_collect", "a_fresh", "a_complete", "a_pacing",
        "a_rule", "a_worth", "a_propose", "a_submit", "i_check",
        "x_pending", "x_pick", "x_precheck", "x_guard", "x_total", "x_wait_approval",
    )
    waiting_trace, waiting_decisions = _route(*waiting_route)
    awaiting_scenarios = tuple(
        replace(
            item, status=ScenarioStatus.AWAITING_APPROVAL,
            path=waiting_decisions, traversed_edges=waiting_trace,
            current_node="x_wait_approval",
            result_summary="等你確認：數字已算好，確認後才會繼續",
            incomplete_reason=None,
        ) if item.code is ScenarioCode.F7 else item
        for item in awaiting_state.scenarios
    )
    awaiting_state = replace(
        awaiting_state,
        scenarios=awaiting_scenarios,
        current=CurrentStep(
            scenario=ScenarioCode.F7,
            node="x_wait_approval",
            started_at=NOW + timedelta(seconds=8),
            last_decision=awaiting_scenarios[-1].path[-1],
        ),
        approval=ApprovalForm(
            proposal_hash="example-hash",
            numbers=(("目前預算", "1,250 元"), ("建議增加", "100 元")),
            narrative="這次加額已接近帳戶上限，請先確認數字。",
            source=ModelSource.RECORDED,
            demo_id=awaiting_state.demo_id,
            numbers_digest=hashlib.sha256(
                "目前預算=1,250 元|建議增加=100 元".encode()
            ).hexdigest(),
        ),
    )
    approval_html = render_approval(awaiting_state, form_token="example-token")
    awaiting_html = render_page(awaiting_state, form_token="example-token", refresh_tick=1003)
    (output / "demo-page-running.html").write_text(
        running_html.replace('href="/static/demo.css"', 'href="static/demo.css"'),
        encoding="utf-8",
    )
    (output / "demo-page-report.html").write_text(
        report_html.replace('href="/static/demo.css"', 'href="static/demo.css"'),
        encoding="utf-8",
    )
    (output / "demo-page-idle.html").write_text(
        idle_html.replace('href="/static/demo.css"', 'href="static/demo.css"'),
        encoding="utf-8",
    )
    (output / "demo-page-approval.html").write_text(
        approval_html.replace('href="/static/demo.css"', 'href="static/demo.css"'),
        encoding="utf-8",
    )
    (output / "demo-page-awaiting.html").write_text(
        awaiting_html.replace('href="/static/demo.css"', 'href="static/demo.css"'),
        encoding="utf-8",
    )
    (output / "demo-preview-running.html").write_text(
        re.sub(r'<meta http-equiv="refresh"[^>]*>', "", running_html).replace(
            'href="/static/demo.css"', 'href="static/demo.css"'
        ),
        encoding="utf-8",
    )
    (output / "demo-preview-report.html").write_text(report_html, encoding="utf-8")
    static = output / "static"
    static.mkdir(exist_ok=True)
    shutil.copyfile("src/rtb/demo/static/demo.css", static / "demo.css")


if __name__ == "__main__":
    write_examples()
