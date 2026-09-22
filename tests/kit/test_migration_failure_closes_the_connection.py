"""三支「連線後補欄位」的資料庫模組:補欄位失敗時都要關掉剛開的連線,寫法一致。

收件口與模擬 DSP 每個請求都會新開一個儲存物件,補欄位失敗(例如等鎖逾時)若不關連線,
每個失敗的請求就漏一條連線;分析行程的任務表是長活物件,但同一件事用同一種寫法。
等鎖逾時一律轉成各模組自己的「忙碌」例外,呼叫端用同一套方式判斷能不能重試。
"""

import sqlite3

import pytest

from rtb.analyzer import task_store
from rtb.dsp import store as dsp_store
from rtb.executor import inbox_store
from rtb.sqlitekit import DatabaseBusy

CASES = [
    (dsp_store, "CampaignStore", "_migrate_columns", dsp_store.StoreBusy),
    (task_store, "TaskStore", "_migrate_evidence_payload_column", task_store.TaskStoreBusy),
    (inbox_store, "InboxStore", "_migrate_columns", inbox_store.InboxBusy),
]
ERRORS = [(RuntimeError("補欄位失敗"), RuntimeError), (DatabaseBusy("database is locked"), None)]


@pytest.mark.parametrize(("raised", "expected"), ERRORS, ids=["other_error", "busy"])
@pytest.mark.parametrize(("module", "cls", "migrate", "busy"), CASES, ids=[c[1] for c in CASES])
def test_a_failed_column_migration_closes_the_new_connection(  # noqa: PLR0913 - 模組與錯誤兩組參數
        tmp_path, monkeypatch, module, cls, migrate, busy, raised, expected):
    opened = []
    real_connect = module.connect

    def recording_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    def fail(_self):
        raise raised

    monkeypatch.setattr(module, "connect", recording_connect)
    monkeypatch.setattr(getattr(module, cls), migrate, fail)

    with pytest.raises(expected or busy):
        getattr(module, cls)(tmp_path / "x.db")

    assert len(opened) == 1  # 前置:真的開過一條連線
    with pytest.raises(sqlite3.ProgrammingError):  # 已關閉的連線不能再用
        opened[0].execute("SELECT 1")
