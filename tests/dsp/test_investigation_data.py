"""模擬 DSP 的逐日成效與過去調整(Phase 13 增量 2,計劃〈新增的兩種模擬資料〉;Phase 14 增量 2a 改成
UTC 日桶與讀取時推算):日桶只由展示種子與寫入端寫、兩支只准 GET 且不寫資料庫的讀取端點、只給種子用的
過去日期寫法,以及種子的一致性([S1126] [S1127] [S1148] [S1153])。

固定日期的測試一律注入固定時鐘(代碼審 r1 鏡頭1:原本用真時鐘配固定 NOW,過了 UTC 午夜就翻紅)。"""

import ast
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rtb.dsp import seed
from rtb.dsp.errors import (
    AdjustmentsNotFound,
    DailyNotFound,
    MetricsNotFound,
    ValidationRejected,
)
from rtb.dsp.store import CampaignStore, Operation

SRC = Path(__file__).resolve().parents[2] / "src"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
DAY = seed.DayFigures(impressions=100, clicks=10, conversions=2, spend=1.5, revenue=8.0)
PROFILE = seed.HistoryProfile(
    daily=(DAY, DAY, None, DAY, DAY, DAY, DAY),
    adjustments=(
        seed.AdjustmentSeed(4, 110),
        seed.AdjustmentSeed(9, 100),
    ))


def _clock(moment):
    return lambda: moment.isoformat()


# 代碼審 r2 鏡頭A:「真時鐘配固定 NOW」這類測試要今天就紅,不要等真實日期走到某天才紅。本檔所有
# 同一個行程裡建的儲存層(含 DspServer 每個請求開的那個),沒注入時鐘就拿到「NOW 之後 400 天」,
# 忘了注入時鐘又用固定日期種資料的測試會當場翻紅。子行程(start_dsp)不受這個夾具影響:用它的
# 測試必須跟日期無關(每一天都成立),並在說明寫明。
FAR_FUTURE = NOW + timedelta(days=400)


@pytest.fixture(autouse=True)
def _default_clock_far_from_now(monkeypatch):
    from rtb.dsp import store as store_module

    monkeypatch.setattr(store_module, "_utc_now", _clock(FAR_FUTURE))


def _seeded(tmp_path, profile=PROFILE, campaigns=("c1",), now=NOW):
    store = CampaignStore(tmp_path / "dsp.db", clock=_clock(now))
    for campaign in campaigns:
        store.seed_campaign(campaign, budget=90)
        store.seed_metrics(campaign, "1h", impressions=500, clicks=12, conversions=1, spend=0.5,
                           revenue=5.0)
    seed.seed_platform_history(store, dict.fromkeys(campaigns, profile), now)
    return store


def _table_dump(path):
    with sqlite3.connect(path) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
                for table in ("daily_metrics", "operations", "campaigns", "metrics")}


ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def test_the_daily_and_past_adjustment_endpoints_are_get_only_and_idempotent(tmp_path, start_dsp):
    """[S1126] 兩支端點只接受 GET;讀取不寫資料庫(種子是 9/25、伺服器用真時鐘,跨日也只在記憶體推算),
    調整回提交時間。子行程用真時鐘,本檔的時鐘守衛管不到它:這支的斷言在任何日期都成立(沒資料的
    日子回 no_data 列、過去調整不看日期),新加斷言也要維持這一點(代碼審 r2 鏡頭A)。"""
    _seeded(tmp_path).close()
    dsp = start_dsp(seed=False)
    materialized = _table_dump(tmp_path / "dsp.db")
    for _ in range(3):
        for path in ("/campaigns/c1/daily", "/campaigns/c1/adjustments"):
            status, body = dsp.request("GET", path)
            assert status == 200, body
            if path.endswith("/daily"):
                assert not ISO_DATE.search(json.dumps(body))
            else:
                assert all(ISO_DATE.search(row["committed_at"]) for row in body["rows"])
        assert _table_dump(tmp_path / "dsp.db") == materialized
    for path in ("/campaigns/c1/daily", "/campaigns/c1/adjustments"):
        for method in ("POST", "PUT", "DELETE"):
            status, _ = dsp.request(method, path, {} if method != "DELETE" else None)
            assert status in (404, 405, 501), (method, path, status)  # 只准 GET
    assert _table_dump(tmp_path / "dsp.db") == materialized
    from rtb.dsp.server import ROUTES

    new = {name: method for method, _p, name in ROUTES if name in ("get_daily", "get_adjustments")}
    assert new == {"get_daily": "GET", "get_adjustments": "GET"}


def test_seeded_daily_and_adjustment_data_agree_with_windows_and_history(tmp_path):  # noqa: PLR0915
    """[S1127] 逐日第 1 天等於 1 天窗、7 天加總等於 7 天窗;情境中當天的新寫入不影響這條一致性。
    展示種子(驅動程式實際用的那一份)也照這條。"""
    store = _seeded(tmp_path, campaigns=("c1", "c2"))
    try:
        for campaign in ("c1", "c2"):
            assert seed.consistency_problems(store, campaign, NOW) == []
        week = store.get_metrics("c1", "7d")
        assert (week.impressions, week.conversions, week.spend) == (600, 12, "9.00")
        assert len([h for h in store.history("c1") if h.action == "update_budget"]) == 2
        # 當天另一個寫入者真的寫一筆預算調整:只落在最近 3 天內,一致性不變
        version = store.get_campaign("c1").version
        store.execute(Operation("c1", "update_budget", {"new_budget": 120}, version, "today-1"))
        assert seed.consistency_problems(store, "c1", NOW) == []
        # 有日桶的廣告不准另種 1d/7d(會被推算值蓋住,看起來像種進去了)
        with pytest.raises(ValidationRejected):
            store.seed_metrics("c1", "7d", impressions=1, clicks=1, conversions=1, spend=1.0,
                               revenue=1.0)
    finally:
        store.close()
    # 殺傷力:兩條讀取投影任一條算錯都抓得到(逐日端點、視窗端點)
    broken = CampaignStore(tmp_path / "dsp.db", clock=_clock(NOW))
    try:
        from rtb.dsp import store as store_module

        real = store_module.window_figures
        store_module.window_figures = lambda window, buckets: (
            None if (fields := real(window, buckets)) is None
            else [fields[0] + 1 if fields[0] is not None else 1, *fields[1:]])
        try:
            assert seed.consistency_problems(broken, "c1", NOW) == [
                "逐日第 1 天不等於 1 天窗", "7 天加總不等於 7 天窗"]
        finally:
            store_module.window_figures = real
    finally:
        broken.close()
    from rtb.demo import driver

    driver.seed_platform(tmp_path / "demo.db", [driver.Campaign("c9", 100, 0.5)])
    demo = CampaignStore(tmp_path / "demo.db")  # 展示種子用真時鐘,讀取也用真時鐘
    try:
        assert seed.consistency_problems(demo, "c9", NOW) == []
        assert len(demo.get_daily("c9")) == 7
        assert demo.get_past_adjustments("c9") == []  # 展示種子:有資料、零筆
    finally:
        demo.close()


def test_seeded_past_operations_keep_commit_times_monotonic(tmp_path):  # noqa: PLR0915
    """[S1153] 只給種子用的過去日期寫法:只准平台還沒有任何操作時呼叫,寫完提交時間隨操作編號單調
    不減;既有寫入路徑不變。只有展示種子模組呼叫它。"""
    store = CampaignStore(tmp_path / "dsp.db")
    try:
        for campaign in ("c1", "c2"):
            store.seed_campaign(campaign, budget=90)
        store.seed_past_operations([seed.PastBudgetChange("c1", 4, 110),
                                    seed.PastBudgetChange("c2", 10, 95),
                                    seed.PastBudgetChange("c1", 9, 100)], NOW)
        rows = [(h.committed_at, h.version_after) for h in store.history("c1")]
        assert rows == [((NOW - timedelta(days=9)).isoformat(), 2),
                        ((NOW - timedelta(days=4)).isoformat(), 3)]
        assert store.get_campaign("c1").budget == 110
        ordered = [row for c in ("c1", "c2") for row in store.history(c)]
        ordered.sort(key=lambda h: h.operation_id)
        stamps = [h.committed_at for h in ordered]
        assert stamps == sorted(stamps) and len(stamps) == 3
        with pytest.raises(ValidationRejected):  # 已經有操作:不准再種
            store.seed_past_operations([seed.PastBudgetChange("c2", 20, 80)], NOW)
        # 既有寫入路徑照舊:提交時間取時鐘讀數,不早於上一筆
        version = store.get_campaign("c2").version
        result = store.execute(Operation("c2", "update_budget", {"new_budget": 99}, version, "k"))
        assert result.committed_at >= stamps[-1]
    finally:
        store.close()
    empty = CampaignStore(tmp_path / "other.db")
    try:
        empty.seed_campaign("c1", budget=90)
        with pytest.raises(ValidationRejected):  # 距今不到 1 天的不是「過去」
            empty.seed_past_operations([seed.PastBudgetChange("c1", 0, 100)], NOW)
    finally:
        empty.close()
    callers = []
    for path in sorted((SRC / "rtb").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in ("seed_past_operations",
                                                                 "seed_history"):
                callers.append(path.relative_to(SRC).as_posix())
    assert sorted(set(callers)) == ["rtb/dsp/seed.py"]


# ---- Phase 13 增量 2 代碼審 r1(d4、d5) ----
_ADJ = seed.AdjustmentSeed(4, 110)


def test_seeding_history_is_all_or_nothing(tmp_path):
    """d4-1:平台已經有操作時整份種子被拒,前面的逐日與過去調整也都沒寫進去。"""
    store = CampaignStore(tmp_path / "dsp.db")
    try:
        store.seed_campaign("c1", budget=90)
        store.execute(Operation("c1", "update_budget", {"new_budget": 95}, 1, "live-1"))
        with pytest.raises(ValidationRejected):
            seed.seed_platform_history(store, {"c1": seed.HistoryProfile(
                daily=(DAY,) * 7, adjustments=(_ADJ,))}, NOW)
        with pytest.raises(AdjustmentsNotFound):
            store.get_past_adjustments("c1")
        with pytest.raises(DailyNotFound):
            store.get_daily("c1")
        with pytest.raises(MetricsNotFound):
            store.get_metrics("c1", "7d")
    finally:
        store.close()


def test_reseeding_past_adjustments_replaces_the_old_rows(tmp_path):
    """原 d4-2 改寫:調整只有操作紀錄這一處,減額不留下另一份舊快照。"""
    store = CampaignStore(tmp_path / "dsp.db")
    try:
        store.seed_campaign("c1", budget=90)
        store.seed_daily("c1", [DAY.as_fields()] * 7, now=NOW)
        store.execute(Operation("c1", "update_budget", {"new_budget": 100}, 1, "raise"))
        store.execute(Operation("c1", "update_budget", {"new_budget": 95}, 2, "lower"))
        assert [(a.budget_before, a.budget_after) for a in store.get_past_adjustments("c1")] == [
            (90, 100)]
        tables = {row[0] for row in store._conn.execute("SELECT name FROM sqlite_master")}
        assert "past_adjustments" not in tables and "past_adjustment_seeds" not in tables
    finally:
        store.close()


@pytest.mark.parametrize(("budget_before", "budget_after"), [(-5, 100), ("abc", 1.5),
                                                              (100, True), (1.5, 100)])
def test_past_adjustment_budgets_must_be_non_negative_integers(tmp_path, budget_before,
                                                               budget_after):
    """原 d4-3 改寫:前值取廣告現況,後值由正常寫入驗證。"""
    store = CampaignStore(tmp_path / "dsp.db")
    try:
        if not isinstance(budget_before, int) or budget_before < 0:
            with pytest.raises(ValidationRejected):
                store.seed_campaign("c1", budget=budget_before)
        else:
            store.seed_campaign("c1", budget=budget_before)
            with pytest.raises(ValidationRejected):
                store.execute(Operation("c1", "update_budget", {"new_budget": budget_after},
                                        1, "bad-budget"))
            assert store.history("c1") == []
    finally:
        store.close()


def test_a_missing_first_day_seeds_an_empty_one_day_window(tmp_path):
    """d5:逐日第 1 天缺資料時 1 天窗照樣在(五欄 null);較長時間窗有結果,d1 欄 na、d7 欄有值。"""
    from rtb.analyzer import dsp_client
    from rtb.analyzer import investigation as inv
    from rtb.analyzer.task_store import TaskRow
    from rtb.domain.task_state import TaskState
    from rtb.dsp.server import DspServer

    store = _seeded(tmp_path, profile=seed.HistoryProfile(daily=(None,) + (DAY,) * 6))
    try:
        assert seed.consistency_problems(store, "c1", NOW) == []
        window = store.get_metrics("c1", "1d")
        assert (window.impressions, window.spend) == (None, None)
    finally:
        store.close()
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0, store_clock=_clock(NOW))
    import threading

    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        read = dsp_client.make_query_reader(f"http://127.0.0.1:{server.server_address[1]}", 2.0)(
            TaskRow("t1", 2, TaskState.COLLECTING_EVIDENCE, "c1", None, None, NOW),
            inv.QueryOption.CHECK_LONGER_WINDOW.value)
    finally:
        server.shutdown()
        server.server_close()
    assert read.reason is None and read.raw is not None
    payload = inv.receipt_payload(inv.QueryOption.CHECK_LONGER_WINDOW, read.raw, NOW)
    assert payload["d1_impressions"] == "na" and payload["d1_revenue"] == "na"
    assert payload["d7_impressions"] == "600"


def test_past_adjustments_include_normal_budget_operations(tmp_path):
    """[S1415] 真正的加額即時可見,減額不能把最近一次加額擠掉。"""
    moment = [NOW]
    store = CampaignStore(tmp_path / "budget.db", clock=lambda: moment[0].isoformat())
    try:
        store.seed_campaign("c1", 100)
        seed.seed_platform_history(store, {"c1": seed.HistoryProfile(daily=(DAY,) * 7)}, NOW)
        raised = store.execute(Operation("c1", "update_budget", {"new_budget": 120}, 1, "raise"))
        for index in range(5):
            store.execute(Operation("c1", "update_budget", {"new_budget": 119 - index},
                                    index + 2, f"lower-{index}"))
        rows = store.get_past_adjustments("c1")
        assert len(rows) == 1
        assert rows[0].budget_before == 100 and rows[0].budget_after == 120
        assert rows[0].committed_at == raised.committed_at
        assert rows[0].after["conversions"] is None
    finally:
        store.close()


def test_latest_raise_daily_buckets_survive_rolling_retention(tmp_path):
    """[S1423] 最近加額 40 天前,六個對照日桶仍可計算。保留期由寫入端執行(這裡是一筆減額),
    讀取不寫資料庫。"""
    moment = [NOW - timedelta(days=40)]
    store = CampaignStore(tmp_path / "retention.db", clock=lambda: moment[0].isoformat())
    try:
        store.seed_campaign("c1", 100)
        store.seed_daily("c1", [DAY.as_fields()] * 7, now=moment[0])
        store.execute(Operation("c1", "update_budget", {"new_budget": 120}, 1, "raise"))
        moment[0] += timedelta(days=4)
        store.seed_daily("c1", [DAY.as_fields()] * 7, now=moment[0])
        moment[0] = NOW
        stored = store._conn.execute("SELECT count(*) FROM daily_metrics").fetchone()[0]
        before_write = (store.get_daily("c1"), store.get_past_adjustments("c1"))
        assert store._conn.execute("SELECT count(*) FROM daily_metrics").fetchone()[0] == stored
        store.execute(Operation("c1", "update_budget", {"new_budget": 110}, 2, "lower"))
        count = store._conn.execute("SELECT count(*) FROM daily_metrics "
                                    "WHERE campaign_id = 'c1'").fetchone()[0]
        assert count == 36  # 30 個完整日 + 最近加額 D 前後六日
        # 寫入端補寫的日桶就是讀取推算的那一份:寫入前後讀到的一樣
        assert (store.get_daily("c1"), store.get_past_adjustments("c1")) == before_write
        rows = store.get_past_adjustments("c1")
        assert len(rows) == 1
        assert rows[0].before["conversions"] == 6
        assert rows[0].after["conversions"] == 6
    finally:
        store.close()


def test_demo_daily_rollover_preserves_full_windows(tmp_path):
    """[S1426] 午夜後讀取:剛完成的那天由展示樣板推出、1d/7d 跟著滑動,讀取不寫資料庫。"""
    moment = [datetime(2026, 9, 26, 23, 59, tzinfo=UTC)]
    store = CampaignStore(tmp_path / "rollover.db", clock=lambda: moment[0].isoformat())
    try:
        store.seed_campaign("c1", 100)
        older = seed.DayFigures(100, 10, 1, 1.0, 8.0)
        seed.seed_platform_history(store, {"c1": seed.HistoryProfile(
            daily=(DAY, older, older, older, older, older, older), template=DAY)}, moment[0])
        before = store.get_daily("c1")
        moment[0] += timedelta(minutes=2)
        after = store.get_daily("c1")
        assert len(after) == 7 and after[0].days_ago == 1
        assert after[0].conversions == DAY.conversions
        assert after[1].conversions == before[0].conversions
        assert store.get_metrics("c1", "1d").conversions == after[0].conversions
        assert store.get_metrics("c1", "7d").conversions == sum(r.conversions or 0 for r in after)
        assert store.get_daily("c1") == after
    finally:
        store.close()


def test_bounded_history_preserves_recent_budget_changes(tmp_path):
    """[S1422] 回傳列有界,完整七日摘要不受截斷影響。"""
    from rtb.dsp.server import DspHandler

    store = CampaignStore(tmp_path / "history.db", clock=lambda: NOW.isoformat())
    try:
        store.seed_campaign("c1", 100)
        store.execute(Operation("c1", "update_budget", {"new_budget": 101}, 1, "budget-0"))
        for index in range(59):
            store.execute(Operation("c1", "pause_campaign", {},
                                    index + 2, f"pause-{index}"))
        body = DspHandler._get_history(object(), store, "c1", None)
        assert set(body) == {"history", "summary", "truncated"} and body["truncated"] is True
        assert len(body["history"]) == 50
        assert not any(row["action"] == "update_budget" for row in body["history"])
        assert body["summary"]["budget_changes_7d"] == 1
        assert body["summary"]["has_recent_budget_change"] is True
        from rtb.analyzer import dsp_client
        from rtb.analyzer import investigation as inv
        checked = dsp_client.check_history(body)
        assert checked is not None
        receipt = inv.receipt_payload(inv.QueryOption.CHECK_CHANGE_HISTORY, checked, NOW)
        assert receipt["budget_changes_last_3d"] == "1" and receipt["history_truncated"] == "true"
        assert len(json.dumps(body)) < 64 * 1024
    finally:
        store.close()


def test_an_untruncated_history_keeps_the_existing_shape(tmp_path):
    """[S1422] 沒超過 50 筆時維持既有 {"history": [...]} 形狀,收據照舊由列與決策 now 算
    (代碼審 r1 spec-conformance:原本一律附摘要)。"""
    from rtb.analyzer import dsp_client
    from rtb.analyzer import investigation as inv
    from rtb.dsp.server import DspHandler

    store = CampaignStore(tmp_path / "history.db", clock=_clock(NOW))
    try:
        store.seed_campaign("c1", 100)
        for index in range(50):
            store.execute(Operation("c1", "pause_campaign", {}, index + 1, f"pause-{index}"))
        body = DspHandler._get_history(object(), store, "c1", None)
        assert set(body) == {"history"} and len(body["history"]) == 50
        checked = dsp_client.check_history(body)
        assert checked == body
        assert "history_truncated" not in inv.receipt_payload(
            inv.QueryOption.CHECK_CHANGE_HISTORY, checked, NOW)
    finally:
        store.close()


def test_history_summary_and_rows_come_from_one_snapshot(tmp_path):
    """代碼審 r1 外家否決 2:摘要與最近 50 列在同一個讀取快照裡讀;另一條連線剛好插在兩次查詢之間
    寫一筆加額,兩邊都看不到它(原本列看得到、摘要看不到,收據寫最近 3 天 0 筆)。"""
    path = tmp_path / "race.db"
    store = CampaignStore(path, clock=_clock(NOW))
    writer = CampaignStore(path, clock=_clock(NOW))
    try:
        store.seed_campaign("c1", 100)
        for index in range(60):
            store.execute(Operation("c1", "pause_campaign", {}, index + 1, f"pause-{index}"))

        class Interleave:
            def __init__(self, conn):
                self.conn, self.touched = conn, 0

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def execute(self, sql, params=()):
                if "FROM operations" in sql:
                    self.touched += 1
                    if self.touched == 2:  # 摘要查完、列還沒查
                        writer.execute(Operation("c1", "update_budget", {"new_budget": 120},
                                                 61, "raise-between"))
                return self.conn.execute(sql, params)

        store._conn = Interleave(store._conn)
        rows, summary = store.history_limited("c1")
        store._conn = store._conn.conn
        assert summary is not None
        seen = sum(row.action == "update_budget" for row in rows)
        assert (seen, summary["total_budget_changes"]) in ((0, 0), (1, 1))
        assert writer.history("c1")[-1].action == "update_budget"  # 寫入確實發生了
    finally:
        store.close()
        writer.close()


def test_old_adjustment_seed_tables_migrate_to_the_single_operation_source(tmp_path):
    """舊種子與 REAL 金額、相對日鍵可在開庫時轉成 2a 單源。"""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE campaigns (id TEXT PRIMARY KEY, budget INTEGER NOT NULL,
                status TEXT NOT NULL, version INTEGER NOT NULL);
            CREATE TABLE operations (operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL, action TEXT NOT NULL, params_json TEXT NOT NULL,
                version_after INTEGER NOT NULL, received_at TEXT NOT NULL,
                committed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE);
            CREATE TABLE metrics (campaign_id TEXT NOT NULL, window_name TEXT NOT NULL,
                impressions INTEGER, clicks INTEGER, conversions INTEGER, spend REAL,
                revenue REAL, PRIMARY KEY (campaign_id, window_name));
            CREATE TABLE daily_metrics (campaign_id TEXT NOT NULL, days_ago INTEGER NOT NULL,
                impressions INTEGER, clicks INTEGER, conversions INTEGER, spend REAL,
                revenue REAL, no_data INTEGER NOT NULL, PRIMARY KEY (campaign_id, days_ago));
            CREATE TABLE past_adjustments (campaign_id TEXT, rank INTEGER, days_ago INTEGER,
                budget_before INTEGER, budget_after INTEGER);
            CREATE TABLE past_adjustment_seeds (campaign_id TEXT PRIMARY KEY);
            INSERT INTO campaigns VALUES ('c1', 110, 'active', 3);
            INSERT INTO past_adjustments VALUES ('c1', 1, 4, 100, 110);
            INSERT INTO past_adjustments VALUES ('c1', 2, 9, 90, 100);
            INSERT INTO past_adjustment_seeds VALUES ('c1');
        """)
        for version, days, budget in ((2, 9, 100), (3, 4, 110)):
            at = (NOW - timedelta(days=days)).isoformat()
            conn.execute("INSERT INTO operations (campaign_id, action, params_json, "
                         "version_after, received_at, committed_at, idempotency_key) "
                         "VALUES (?, 'update_budget', ?, ?, ?, ?, ?)",
                         ("c1", json.dumps({"new_budget": budget}), version, at, at,
                          f"legacy-{version}"))
        for age in range(1, 8):
            conn.execute("INSERT INTO daily_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         ("c1", age, 100, 10, 2, 1.5, 8.0, 0))
        conn.execute("INSERT INTO metrics VALUES ('c1', '1d', 100, 10, 2, 1.5, 8.0)")
    store = CampaignStore(path, clock=lambda: NOW.isoformat())
    try:
        assert store.get_metrics("c1", "1d").spend == "1.50"
        assert store._conn.execute("SELECT spend, typeof(spend) FROM metrics").fetchone() == (
            150, "integer")
        assert store._conn.execute("PRAGMA table_info(daily_metrics)").fetchall()[1][1] == "day_utc"
        rows = store.get_past_adjustments("c1")
        assert len(rows) == 1 and rows[0].budget_before == 100
        assert rows[0].before["conversions"] == rows[0].after["conversions"] == 6
        tables = {r[0] for r in store._conn.execute("SELECT name FROM sqlite_master")}
        assert "past_adjustments" not in tables and "past_adjustment_seeds" not in tables
    finally:
        store.close()


# ---- Phase 14 增量 2a 代碼審 r1 ----
def _dump_all(path):
    with sqlite3.connect(path) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
                for table in tables}


def test_reads_never_write_and_still_work_while_the_write_lock_is_held(tmp_path):
    """鏡頭3-2:逐日、1d/7d、過去調整、歷史四種讀取不寫資料庫,跨了好幾天也只在記憶體推算;
    別的連線握著寫入鎖時照樣讀得到(原本讀前物化要搶寫入鎖,逾時變 503)。"""
    path = tmp_path / "dsp.db"
    stale = NOW - timedelta(days=10)
    seeding = CampaignStore(path, clock=_clock(stale))
    seeding.seed_campaign("c1", 100)
    seed.seed_platform_history(seeding, {"c1": seed.HistoryProfile(
        daily=(DAY,) * 7, adjustments=(seed.AdjustmentSeed(4, 110),), template=DAY)}, stale)
    seeding.close()
    before = _dump_all(path)
    store = CampaignStore(path, clock=_clock(NOW), busy_timeout_seconds=0.2)
    holder = sqlite3.connect(path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        daily = store.get_daily("c1")
        assert [row.conversions for row in daily] == [DAY.conversions] * 7  # 十天都由樣板推出
        assert store.get_metrics("c1", "1d").conversions == DAY.conversions
        assert store.get_metrics("c1", "7d").conversions == 7 * DAY.conversions
        [past] = store.get_past_adjustments("c1")
        assert past.after["conversions"] == 3 * DAY.conversions
        rows, _summary = store.history_limited("c1")
        assert len(rows) == 1
        holder.execute("ROLLBACK")
    finally:
        holder.close()
        store.close()
    assert _dump_all(path) == before


def test_a_completed_day_without_a_template_has_no_data_instead_of_a_copy(tmp_path):
    """鏡頭1:種完之後新完成的日子,沒有樣板就是沒資料,不把最新那天(含沒資料的那天)照抄下去;
    有樣板才由樣板推出。"""
    for profile, expected in (
            (seed.HistoryProfile(daily=(DAY,) * 7), None),
            (seed.HistoryProfile(daily=(None,) + (DAY,) * 6), None),
            (seed.HistoryProfile(daily=(None,) + (DAY,) * 6, template=DAY), DAY.conversions)):
        folder = tmp_path / f"p{len(list(tmp_path.iterdir()))}"
        folder.mkdir()
        store = _seeded(folder, profile=profile)
        later = CampaignStore(folder / "dsp.db", clock=_clock(NOW + timedelta(days=3)))
        try:
            rows = later.get_daily("c1")
            assert [row.conversions for row in rows[:3]] == [expected] * 3
            assert all(row.no_data is (expected is None) for row in rows[:3])
            assert rows[3].conversions == (profile.daily[0].conversions
                                           if profile.daily[0] is not None else None)
            assert seed.consistency_problems(later, "c1", NOW) == []
        finally:
            later.close()
            store.close()


def test_one_read_uses_one_clock_reading_across_midnight(tmp_path):
    """外家 finder 1:一次讀取只讀一次時鐘,整個回應用同一個 UTC 日期;時鐘在讀取途中跨過午夜,
    逐日與同一時刻的 7 天窗仍對得上(原本物化與投影各讀一次,昨天變成缺資料、7 天窗還是舊的)。"""
    before_midnight = datetime(2026, 9, 26, 23, 59, 59, tzinfo=UTC)
    ticks = []

    def clock():
        ticks.append(None)
        return (before_midnight if len(ticks) == 1 else
                before_midnight + timedelta(seconds=2)).isoformat()

    path = tmp_path / "dsp.db"
    seeding = CampaignStore(path, clock=_clock(before_midnight))
    seeding.seed_campaign("c1", 100)
    first = seed.DayFigures(100, 10, 2, "1.00", "2.00")
    seed.seed_platform_history(seeding, {"c1": seed.HistoryProfile(
        daily=(first,) + (DAY,) * 6)}, before_midnight)
    seeding.close()
    store = CampaignStore(path, clock=clock)
    try:
        rows = store.get_daily("c1")
        assert len(ticks) == 1
        assert [row.no_data for row in rows] == [False] * 7
        assert sum(row.conversions for row in rows) == 14
        ticks.clear()
        ticks.append(None)  # 之後的讀取都在午夜之後
        after = store.get_daily("c1")
        assert after[0].no_data and after[1].conversions == 2
        assert store.get_metrics("c1", "7d").conversions == sum(
            row.conversions or 0 for row in after)
    finally:
        store.close()


def test_seven_day_totals_never_overflow_into_a_server_error(tmp_path):  # noqa: PLR0915
    """外家 finder 5、外家否決 1、鏡頭1 發現 3,r2 鏡頭B 發現 1:七天加總在讀取時用 Python 整數算、
    不寫回資料庫,不丟原始例外、不回 500;而且 DSP 在寫入邊界保證 1d/7d 與加額前後三天的合計都在
    分析端讀取白名單的上限內——日桶與樣板的金額每天最多是整數分上限的七分之一、計數最多是整數
    上限的七分之一,超過就整份種子拒收。r2 鏡頭A:HTTP 段在同一行程起伺服器、注入固定時鐘。"""
    import threading

    from rtb.analyzer import dsp_client
    from rtb.dsp.server import DspServer
    from rtb.dsp.store import DAILY_MAX_CENTS, DAILY_MAX_COUNT, money_text

    daily_max = money_text(DAILY_MAX_CENTS)
    biggest = {"impressions": DAILY_MAX_COUNT, "clicks": 1, "conversions": 1,
               "spend": daily_max, "revenue": daily_max}
    store = CampaignStore(tmp_path / "dsp.db", clock=_clock(NOW))
    try:
        store.seed_campaign("c1", 100)
        store.seed_daily("c1", [biggest] * 7, now=NOW, template=biggest)
        for too_big in ({**biggest, "impressions": DAILY_MAX_COUNT + 1},
                        {**biggest, "spend": money_text(DAILY_MAX_CENTS + 1)},
                        {**biggest, "revenue": "-" + money_text(DAILY_MAX_CENTS + 1)},
                        {**biggest, "spend": "90000000000000000.00"}):
            with pytest.raises(ValidationRejected):
                store.seed_daily("c1", [biggest] * 6 + [too_big], now=NOW)
            with pytest.raises(ValidationRejected):  # 樣板一樣
                store.seed_daily("c1", [biggest] * 7, now=NOW, template=too_big)
        week = store.get_metrics("c1", "7d")
        assert week.impressions == 7 * DAILY_MAX_COUNT
        assert week.spend == money_text(7 * DAILY_MAX_CENTS)
    finally:
        store.close()
    server = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2,
                       delay_seconds=0.0, store_clock=_clock(NOW))
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        read = dsp_client.make_query_reader(f"http://127.0.0.1:{server.server_address[1]}", 2.0)
        from rtb.analyzer.task_store import TaskRow
        from rtb.domain.task_state import TaskState

        task = TaskRow("t1", 2, TaskState.COLLECTING_EVIDENCE, "c1", None, None, NOW)
        longer = read(task, "check_longer_window")
        daily = read(task, "check_daily_trend")
    finally:
        server.shutdown()
        server.server_close()
    assert longer.reason is None and longer.raw["7d"]["spend"] == money_text(7 * DAILY_MAX_CENTS)
    assert daily.reason is None


def test_a_seven_day_window_without_any_data_is_not_found(tmp_path):
    """鏡頭1 發現 2:七天全沒資料時沒有 7 天窗(404,同舊種子不種 7d 窗),1 天窗是五欄 null;
    一致性核對不誤報。"""
    store = _seeded(tmp_path, profile=seed.HistoryProfile(daily=(None,) * 7))
    try:
        with pytest.raises(MetricsNotFound):
            store.get_metrics("c1", "7d")
        assert store.get_metrics("c1", "1d").conversions is None
        assert seed.consistency_problems(store, "c1", NOW) == []
    finally:
        store.close()


def _legacy(path, daily_amount=1.5, window_amount=1.5):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE campaigns (id TEXT PRIMARY KEY, budget INTEGER NOT NULL,
                status TEXT NOT NULL, version INTEGER NOT NULL);
            CREATE TABLE operations (operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL, action TEXT NOT NULL, params_json TEXT NOT NULL,
                version_after INTEGER NOT NULL, received_at TEXT NOT NULL,
                committed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE);
            CREATE TABLE metrics (campaign_id TEXT NOT NULL, window_name TEXT NOT NULL,
                impressions INTEGER, clicks INTEGER, conversions INTEGER, spend REAL,
                revenue REAL, PRIMARY KEY (campaign_id, window_name));
            CREATE TABLE daily_metrics (campaign_id TEXT NOT NULL, days_ago INTEGER NOT NULL,
                impressions INTEGER, clicks INTEGER, conversions INTEGER, spend REAL,
                revenue REAL, no_data INTEGER NOT NULL, PRIMARY KEY (campaign_id, days_ago));
            INSERT INTO campaigns VALUES ('c1', 120, 'active', 2);
        """)
        for age in range(1, 8):
            conn.execute("INSERT INTO daily_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         ("c1", age, 100, 10, 2, daily_amount, 8.0, 0))
        conn.execute("INSERT INTO metrics VALUES ('c1', '1h', 100, 10, 2, ?, 8.0)",
                     (window_amount,))


def test_legacy_amounts_that_no_longer_fit_do_not_stop_the_dsp_from_starting(tmp_path):
    """外家 finder 3:舊版收任何有限浮點金額;新版存不下的(整數部分超過 13 位)升級時記缺值,
    DSP 照樣起得來,不讓整個升級回滾。"""
    path = tmp_path / "legacy.db"
    _legacy(path, daily_amount=1e30, window_amount=1e30)
    store = CampaignStore(path, clock=_clock(NOW))
    try:
        assert store.get_metrics("c1", "1h").spend is None
        assert store.get_metrics("c1", "1h").revenue == "8.00"
        assert [row.spend for row in store.get_daily("c1")] == [None] * 7
    finally:
        store.close()


def test_a_legacy_raise_without_a_provable_budget_before_is_reported_as_missing_evidence(
        tmp_path):
    """鏡頭3-3:遷移前 2 小時的真實加額是這個廣告第一筆操作、舊種子表也沒有它,補不回調整前預算;
    過去調整照樣回這一筆、調整前預算 null(讀取層收下,領域判證據不足),不回 404 讓加額消失。
    操作紀錄是只增不改的稽核表:遷移不回頭改它,補得回來的前值另記在補值表。"""
    from rtb.analyzer import dsp_client
    from rtb.analyzer import investigation as inv
    from rtb.domain import nine_rules as rules
    from rtb.dsp.server import DspHandler

    path = tmp_path / "legacy.db"
    _legacy(path)
    at = (NOW - timedelta(hours=2)).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO operations (campaign_id, action, params_json, version_after, "
                     "received_at, committed_at, idempotency_key) "
                     "VALUES ('c1', 'update_budget', ?, 2, ?, ?, 'real-raise-1')",
                     (json.dumps({"new_budget": 120}), at, at))
        legacy_rows = conn.execute("SELECT * FROM operations").fetchall()
    store = CampaignStore(path, clock=_clock(NOW))
    try:
        [row] = store.get_past_adjustments("c1")
        assert (row.budget_before, row.budget_after, row.committed_at) == (None, 120, at)
        assert [r[:8] for r in store._conn.execute("SELECT * FROM operations")] == legacy_rows
        body = DspHandler._get_adjustments(object(), store, "c1", None)
    finally:
        store.close()
    checked = dsp_client.check_adjustments(body, "c1")
    assert checked is not None
    receipt = inv.receipt_payload(inv.QueryOption.CHECK_PAST_ADJUSTMENTS, checked, NOW)
    assert receipt["adj1_budget_change"] == "na"
    past = rules.PastAdjustments((rules.AdjustmentRow(0, None, 120, 6, None),))
    assert rules._past_decision(past) == rules.RuleDecision(
        rules.WorthVerdict.INSUFFICIENT, None, rules.RuleReason.MISSING_ROW_VALUE,
        rules.QueryKind.PAST)


def test_writes_persist_days_after_reading_the_newest_day_under_the_write_lock(tmp_path):
    """外家 finder 2:兩個寫入都要補同一段跨日日桶並做保留;後一個在拿到寫入鎖之前,前一個已經補完、
    把最舊的那天清掉。寫入端在鎖內才讀最新日期,後一個什麼都不用補,不會展開已被清掉的那天而丟例外。"""
    path = tmp_path / "dsp.db"
    old = datetime(2026, 8, 1, 12, tzinfo=UTC)
    seeding = CampaignStore(path, clock=_clock(old))
    seeding.seed_campaign("c1", 100)
    seeding.seed_daily("c1", [DAY.as_fields()] * 7, now=old, template=DAY.as_fields())
    seeding.close()
    later = old + timedelta(days=40)
    first = CampaignStore(path, clock=_clock(later))
    second = CampaignStore(path, clock=_clock(later))
    try:
        original = second._begin_write_transaction

        def late_lock():
            first.execute(Operation("c1", "pause_campaign", {}, 1, "first"))
            original()

        second._begin_write_transaction = late_lock
        second.execute(Operation("c1", "update_budget", {"new_budget": 90}, 2, "second"))
        count = second._conn.execute("SELECT count(*) FROM daily_metrics").fetchone()[0]
        assert count == 30
    finally:
        first.close()
        second.close()


def test_a_legacy_day_whose_every_value_is_over_the_daily_limit_becomes_no_data(tmp_path):
    """代碼審 r3 鏡頭A 發現 3、外家 finder 2:舊相對日數資料遷移時,一天五欄都超過每天上限 → 五欄記
    缺值,那天就是沒資料(no_data 為真),不回「五欄全空卻說有資料」讓整週逐日被讀取層拒收。"""
    from rtb.analyzer import dsp_client
    from rtb.dsp.server import DspHandler

    path = tmp_path / "legacy.db"
    _legacy(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE daily_metrics SET impressions = ?, clicks = ?, conversions = ?, "
                     "spend = 2e12, revenue = 2e12 WHERE days_ago = 1", (2**62, 2**62, 2**62))
    store = CampaignStore(path, clock=_clock(NOW))
    try:
        day1 = store.get_daily("c1")[0]
        assert day1.no_data and day1.impressions is None and day1.spend is None
        body = DspHandler._get_daily(object(), store, "c1", None)
    finally:
        store.close()
    assert dsp_client.check_daily(body, "c1") is not None


def test_stored_date_keyed_buckets_over_the_daily_limit_read_as_missing(tmp_path):
    """代碼審 r3 外家 finder 1:前一版已是 UTC 日期鍵、整數分的資料庫開庫時不走遷移,裡面可能有每天
    上限以前存下的超額日桶。讀取推算時跟遷移同一套:超過每天上限的欄當缺值(整天都超就是沒資料),
    所以 1d/7d 合計仍在讀取白名單上限內,不會整份 invalid。"""
    from rtb.analyzer import dsp_client
    from rtb.dsp.store import MAX_CENTS

    path = tmp_path / "dsp.db"
    store = CampaignStore(path, clock=_clock(NOW))
    try:
        store.seed_campaign("c1", 100)
        store.seed_daily("c1", [DAY.as_fields()] * 7, now=NOW)
        store._conn.execute("UPDATE daily_metrics SET spend = ?", (MAX_CENTS,))
        store._conn.execute("UPDATE daily_metrics SET impressions = ?, clicks = ?, "
                            "conversions = ?, revenue = ? WHERE day_utc = ?",
                            (2**62, 2**62, 2**62, MAX_CENTS,
                             (NOW - timedelta(days=2)).date().isoformat()))
        week = store.get_metrics("c1", "7d")
        daily = store.get_daily("c1")
    finally:
        store.close()
    assert week.spend is None and week.conversions == 6 * DAY.conversions
    assert daily[1].no_data and daily[0].spend is None and not daily[0].no_data
    body = {"campaign_id": "c1", "window": "7d", "impressions": week.impressions,
            "clicks": week.clicks, "conversions": week.conversions, "spend": week.spend,
            "revenue": week.revenue}
    assert dsp_client._window_body(body, "c1", "7d") is not None
