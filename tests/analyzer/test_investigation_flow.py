"""蒐證那一步的原始資料與原始資料表(Phase 13 增量 2 [S1130] [S1151]):原始回應跟狀態列同一個交易寫、
只增不改。

Phase 14 增量 3(計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈拆增量〉3):流程層的 AI 那一步
(續租、續租後收據容器、續租沒成、調查紀錄同交易,[S1110] [S1135] [S1149] [S1150] [S1152])隨
`flow.advance(ai_decide=...)` 撤除,那幾支測試一起刪;正式規則輪的原始回應仍走同一個交易,留在這裡。"""

import ast
from pathlib import Path

from rtb.analyzer import flow
from rtb.analyzer.task_store import RawQuery, TaskStore
from rtb.domain.task_state import TaskState
from tests.analyzer.conftest import NOW, Counting, make_evidence

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"


def _to_analyzing(store, raw=()):
    store.create_task("t1", "c1", NOW)
    flow.advance(store, "t1", Counting(), Counting(), Counting(), NOW)
    batch = flow.EvidenceBatch((make_evidence(evidence_id="t1-2-state", task_id="t1"),), raw)
    flow.advance(store, "t1", Counting(returns=batch), Counting(), Counting(), NOW)
    assert store.latest("t1").state is TaskState.ANALYZING


def test_raw_query_rows_are_committed_with_the_evidence_step(store, tmp_path):
    """[S1151] 蒐證那一步的原始資料列跟證據同一個交易寫,序號等於新狀態列;提交沒寫入時連原始資料
    一起沒寫;重做同一步不撞主鍵、不留兩列。"""
    raw = (RawQuery("check_daily_trend", '{"rows":[]}'),)
    _to_analyzing(store, raw)
    analyzing = store.latest("t1")
    assert store.raw_query("t1", analyzing.seq, "check_daily_trend") == '{"rows":[]}'
    # 輸掉提交(別人剛推進過):原始資料也沒寫
    lost = TaskStore(tmp_path / "lost.db")
    try:
        lost.create_task("t1", "c1", NOW)
        flow.advance(lost, "t1", Counting(), Counting(), Counting(), NOW)
        collecting = lost.latest("t1")

        def racing(_task, _now):
            rival = TaskStore(tmp_path / "lost.db")
            try:
                assert rival.commit_step("t1", collecting.seq, TaskState.ANALYZING, NOW)
            finally:
                rival.close()
            return flow.EvidenceBatch((make_evidence(evidence_id="x", task_id="t1"),), raw)

        flow.advance(lost, "t1", racing, Counting(), Counting(), NOW)
        count = lost._conn.execute("SELECT count(*) FROM investigation_raw").fetchone()[0]
        assert count == 0
        # 重做:再蒐證一次(回到蒐集證據之後)不撞主鍵,每一輪各一列
        flow.advance(lost, "t1", Counting(), Counting(returns=flow.NeedsFreshEvidence()),
                     Counting(), NOW)
        flow.advance(lost, "t1", Counting(returns=flow.EvidenceBatch((), raw)), Counting(),
                     Counting(), NOW)
        rows = lost._conn.execute("SELECT seq FROM investigation_raw").fetchall()
        assert rows == [(lost.latest("t1").seq,)]
    finally:
        lost.close()


def test_every_query_result_is_archived_under_its_option_code(store):
    """[S1130] 原始回應只增不改地存進調查原始資料表,用(任務、那一輪分析列序號、選項代碼)取回的跟
    寫入的正規化 JSON 逐位元組相同;沒有結果的那一輪取不到;現行決策函式、AI 決策函式與提示不讀這
    張表。"""
    text = '{"campaign_id":"c1","rows":[{"days_ago":1,"no_data":true}],"x":"\\u00e9"}'
    _to_analyzing(store, (RawQuery("check_daily_trend", text),))
    seq = store.latest("t1").seq
    assert store.raw_query("t1", seq, "check_daily_trend") == text
    assert store.raw_query("t1", seq, "check_past_adjustments") is None  # 那一輪沒有結果
    assert store.raw_query("t1", seq - 1, "check_daily_trend") is None
    source = (SRC / "analyzer" / "task_store.py").read_text(encoding="utf-8")
    assert "UPDATE investigation" not in source and "DELETE FROM investigation" not in source
    # Phase 14 增量 2b 改寫(代碼審 r1 鏡頭2):決策路徑只有規則輪定案讀這張表(同一輪 B/C、白名單驗過、
    # 跟收據同交易寫的原始回應);分析端其餘每支檔都不准讀。白名單式掃描,新檔自動納入
    readers = set()
    for path in sorted((SRC / "analyzer").glob("*.py")):
        if path.name == "task_store.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str)}
        if {"raw_query", "investigation_raw"} & names:
            readers.add(path.stem)
    assert readers == {"rule_round"}
