"""分析端對外呼叫的端點是封閉列舉(Phase 9 增量 2):[S637]、[S648]。

端點會變成指標的標籤;原本是一般字串,呼叫端剛好只傳幾個固定值,但呼叫紀錄收任何字串,無界值就能
灌進標籤。記呼叫紀錄的函式改成只收列舉成員;已經寫進去的舊列讀出來時,不在列舉裡的值一律歸成其他。
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from rtb.analyzer.task_store import TaskReader, TaskStore, ToolEndpoint

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


# ---- [S637] ----
def test_analyzer_tool_call_endpoints_are_a_closed_list(tmp_path):
    store = TaskStore(tmp_path / "analyzer.db")
    try:
        for endpoint in ToolEndpoint:
            if endpoint is ToolEndpoint.OTHER:
                continue
            store.record_tool_call("t1", 1, endpoint, "ok", 1.0, NOW)
        for raw in ("dsp:campaign", "anything-goes", "x" * 200):  # 一般字串一律拒,連長得一樣的也拒
            with pytest.raises(ValueError, match="ToolEndpoint"):  # 比照執行端封閉列舉的慣例
                store.record_tool_call("t1", 1, raw, "ok", 1.0, NOW)
        with pytest.raises(ValueError, match="其他"):  # 「其他」只給讀舊列用,不能寫
            store.record_tool_call("t1", 1, ToolEndpoint.OTHER, "ok", 1.0, NOW)
        written = [call.endpoint for call in store.list_tool_calls("t1")]
    finally:
        store.close()

    assert written == [e for e in ToolEndpoint if e is not ToolEndpoint.OTHER]
    assert all(type(e) is ToolEndpoint for e in written)


def test_the_endpoints_in_use_are_members():
    """分析端實際呼叫的端點都在列舉裡(改成列舉後,原本傳字串的呼叫點都要換掉)。"""
    assert {e.value for e in ToolEndpoint} >= {"dsp:campaign", "dsp:metrics", "dsp:operation",
                                               "inbox:submit"}


# ---- [S648] ----
def test_legacy_tool_call_endpoints_read_as_other(tmp_path):
    path = tmp_path / "analyzer.db"
    TaskStore(path).close()
    conn = sqlite3.connect(path)
    conn.executemany(  # 改成列舉之前寫進去的舊列:任意字串
        "INSERT INTO tool_calls (task_id, task_seq, endpoint, outcome, latency_ms, at) "
        "VALUES ('t1', 1, ?, 'ok', 1.0, '2026-09-22T12:00:00.000000Z')",
        [("dsp:campaign",), ("legacy:whatever",), ("",)])
    conn.commit()
    conn.close()

    for opener in (TaskStore, TaskReader):
        store = opener(path)
        try:
            endpoints = [call.endpoint for call in store.list_tool_calls("t1")]
        finally:
            store.close()
        assert endpoints == [ToolEndpoint.DSP_CAMPAIGN, ToolEndpoint.OTHER, ToolEndpoint.OTHER]
        assert all(type(e) is ToolEndpoint for e in endpoints)
