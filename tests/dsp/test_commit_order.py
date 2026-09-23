"""DSP 提交時間不倒退(Phase 9 增量 3):[S667]。

副作用核對依操作編號往下翻頁、讀到提交時間超過窗終點就停;依編號翻頁要等於依時間翻頁,提交時間就
得不小於上一筆。DSP 的時鐘可以注入、也可能倒退:提交時把時間取成時鐘讀數與上一筆提交時間較大的那個。
"""

import sqlite3
from datetime import UTC, datetime

from rtb.dsp.store import CampaignStore, Operation


def _op(key, budget, version):
    return Operation(campaign_id="c1", action="update_budget", params={"new_budget": budget},
                     expected_version=version, idempotency_key=key)


# ---- [S667] ----
def test_dsp_commit_times_never_go_backwards(tmp_path):
    readings = iter([
        "2026-09-22T12:00:05+00:00", "2026-09-22T12:00:05+00:00",  # 第一筆:收到、提交
        "2026-09-22T12:00:01+00:00", "2026-09-22T12:00:01+00:00",  # 第二筆:時鐘倒退 4 秒
        "2026-09-22T12:00:09+00:00", "2026-09-22T12:00:09+00:00",  # 第三筆:恢復正常
    ])
    store = CampaignStore(tmp_path / "dsp.db", clock=lambda: next(readings))
    try:
        store.seed_campaign("c1", budget=100)
        results = [store.execute(_op("k-a", 110, 1)), store.execute(_op("k-b", 120, 2)),
                   store.execute(_op("k-c", 130, 3))]
    finally:
        store.close()

    times = [r.committed_at for r in results]
    assert times == ["2026-09-22T12:00:05+00:00", "2026-09-22T12:00:05+00:00",
                     "2026-09-22T12:00:09+00:00"]  # 倒退的那一筆墊高到上一筆
    ids = [r.operation_id for r in results]
    assert ids == sorted(ids)
    stored = store_rows(tmp_path / "dsp.db")
    assert [t for _, t in stored] == sorted(t for _, t in stored)  # 依編號的順序就是依時間的順序


def test_dsp_commit_times_are_stored_in_one_utc_form(tmp_path):
    """代碼審第 1 輪:時鐘可注入非 UTC 的偏移;提交時間寫入前統一成固定 UTC 格式,游標查詢用同一種
    表示,字串比較才等於時間比較。第二筆 07:30-05:00 就是 12:30Z,晚於第一筆。"""
    readings = iter(["2026-09-22T12:00:00+00:00", "2026-09-22T12:00:00+00:00",
                     "2026-09-22T07:30:00-05:00", "2026-09-22T07:30:00-05:00"])
    store = CampaignStore(tmp_path / "dsp.db", clock=lambda: next(readings))
    try:
        store.seed_campaign("c1", budget=100)
        results = [store.execute(_op("k-a", 110, 1)), store.execute(_op("k-b", 120, 2))]
        assert [r.committed_at for r in results] == ["2026-09-22T12:00:00+00:00",
                                                     "2026-09-22T12:30:00+00:00"]
        assert [t for _, t in store_rows(tmp_path / "dsp.db")] == [
            "2026-09-22T12:00:00+00:00", "2026-09-22T12:30:00+00:00"]
        # 窗起點 12:15Z:第一筆 12:00 在窗外,游標停在它(編號 1),翻頁從 12:30 那筆開始
        assert store.operation_cursor(datetime(2026, 9, 22, 12, 15, tzinfo=UTC)) == 1
    finally:
        store.close()


def store_rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT operation_id, committed_at FROM operations ORDER BY operation_id").fetchall()
    finally:
        conn.close()
