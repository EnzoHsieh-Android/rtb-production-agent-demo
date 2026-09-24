"""模擬 DSP 的逐日成效與過去調整(Phase 13 增量 2,計劃〈新增的兩種模擬資料〉):兩張只由展示種子寫
的表、兩支只准 GET 的唯讀端點、只給種子用的過去日期寫法,以及種子的一致性([S1126] [S1127] [S1148]
[S1153])。"""

import ast
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rtb.dsp import seed
from rtb.dsp.errors import ValidationRejected
from rtb.dsp.store import CampaignStore, Operation

SRC = Path(__file__).resolve().parents[2] / "src"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
DAY = seed.DayFigures(impressions=100, clicks=10, conversions=2, spend=1.5, revenue=8.0)
PROFILE = seed.HistoryProfile(
    daily=(DAY, DAY, None, DAY, DAY, DAY, DAY),
    adjustments=(
        seed.AdjustmentSeed(4, 100, 110, before=seed.DayFigures(300, 30, 6, 4.5, 24.0),
                            after=seed.DayFigures(300, 30, 3, 4.5, 12.0)),
        seed.AdjustmentSeed(9, 90, 100, before=seed.DayFigures(300, 30, 3, 4.5, 12.0),
                            after=seed.DayFigures(300, 30, 6, 4.5, 24.0)),
    ))


def _seeded(tmp_path, profile=PROFILE, campaigns=("c1",)):
    store = CampaignStore(tmp_path / "dsp.db")
    for campaign in campaigns:
        store.seed_campaign(campaign, budget=90)
        store.seed_metrics(campaign, "1h", impressions=500, clicks=12, conversions=1, spend=0.5,
                           revenue=5.0)
    seed.seed_platform_history(store, dict.fromkeys(campaigns, profile), NOW)
    return store


def _table_dump(path):
    with sqlite3.connect(path) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
                for table in ("daily_metrics", "past_adjustments", "past_adjustment_seeds",
                              "operations", "campaigns", "metrics")}


ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def test_the_daily_and_past_adjustment_endpoints_are_read_only_and_dateless(tmp_path, start_dsp):
    """[S1126] 兩支端點只接受 GET,呼叫任意次後各表與操作紀錄不變;回應只含相對天數,不含日期時間戳
    。"""
    _seeded(tmp_path).close()
    before = _table_dump(tmp_path / "dsp.db")
    dsp = start_dsp(seed=False)
    for _ in range(3):
        for path in ("/campaigns/c1/daily", "/campaigns/c1/adjustments"):
            status, body = dsp.request("GET", path)
            assert status == 200, body
            text = json.dumps(body)
            assert not ISO_DATE.search(text), text
            assert not {"committed_at", "received_at", "date", "at"} & {
                k for row in body["rows"] for k in row}
    for path in ("/campaigns/c1/daily", "/campaigns/c1/adjustments"):
        for method in ("POST", "PUT", "DELETE"):
            status, _ = dsp.request(method, path, {} if method != "DELETE" else None)
            assert status in (404, 405, 501), (method, path, status)  # 只准 GET
    assert _table_dump(tmp_path / "dsp.db") == before
    from rtb.dsp.server import ROUTES

    new = {name: method for method, _p, name in ROUTES if name in ("get_daily", "get_adjustments")}
    assert new == {"get_daily": "GET", "get_adjustments": "GET"}


def test_seeded_daily_and_adjustment_data_agree_with_windows_and_history(tmp_path):
    """[S1127] 逐日第 1 天等於 1 天窗、7 天加總等於 7 天窗;過去調整筆數等於操作歷史裡 3 天以前的
    預算調整筆數;情境中當天的新寫入不影響這條一致性。展示種子(驅動程式實際用的那一份)也照這條。"""
    store = _seeded(tmp_path, campaigns=("c1", "c2"))
    try:
        for campaign in ("c1", "c2"):
            assert seed.consistency_problems(store, campaign, NOW) == []
        week = store.get_metrics("c1", "7d")
        assert (week.impressions, week.conversions, week.spend) == (600, 12, 9.0)
        assert len([h for h in store.history("c1") if h.action == "update_budget"]) == 2
        # 當天另一個寫入者真的寫一筆預算調整:只落在最近 3 天內,一致性不變
        version = store.get_campaign("c1").version
        store.execute(Operation("c1", "update_budget", {"new_budget": 120}, version, "today-1"))
        assert seed.consistency_problems(store, "c1", NOW) == []
        # 殺傷力:把 7 天窗改掉、或多種一筆過去調整,都抓得到
        store.seed_metrics("c1", "7d", impressions=1, clicks=1, conversions=1, spend=1.0,
                           revenue=1.0)
        assert seed.consistency_problems(store, "c1", NOW)
    finally:
        store.close()
    from rtb.demo import driver

    driver.seed_platform(tmp_path / "demo.db", [driver.Campaign("c9", 100, 0.5)])
    demo = CampaignStore(tmp_path / "demo.db")
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
            if isinstance(node, ast.Attribute) and node.attr == "seed_past_operations":
                callers.append(path.relative_to(SRC).as_posix())
    assert sorted(set(callers)) == ["rtb/dsp/seed.py"]
