"""收件口寫入的時間一律要帶時區:跟讀取路徑、嘗試紀錄同一條規則。

沒帶時區的時間轉 UTC 字串時會被當成伺服器本地時間,寫進去的時間就錯了,之後照時間篩、判過期、
清保留期都會跟著錯。Phase 6 增量 4 讓讀取停下紀錄的查詢拒絕沒帶時區的時間,寫入那一頭沒有同樣
檢查,兩邊嚴格度不一樣;這裡把收件口所有寫時間的地方都收成同一支轉換(沒帶時區就拒絕)。
"""

import pytest

from rtb.domain.attempt import operation_key
from rtb.executor.inbox_store import ApprovalUse, BlockCode, InboxStore, Stop, StopKind
from tests.executor.conftest import NOW
from tests.executor.fakes import proposal

NAIVE = NOW.replace(tzinfo=None)


@pytest.fixture
def store(tmp_path):
    opened = InboxStore(tmp_path / "executor.db")
    yield opened
    opened.close()


def _rows(store, table):
    with store.transaction() as tx:
        return tx.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 - 測試固定表名


def test_a_stop_without_a_timezone_is_refused(store):
    prop = proposal()
    with store.transaction() as tx, pytest.raises(ValueError, match="時區"):
        store.record_stop(tx, Stop(StopKind.AGGREGATE_LIMIT_REACHED, prop, operation_key(prop),
                                   "t-default", 50, 90, 100), NAIVE)
    assert _rows(store, "write_stops") == 0


def test_a_proposal_received_at_a_time_without_a_timezone_is_refused(store):
    with pytest.raises(ValueError, match="時區"):
        store.accept(proposal(), lambda: NAIVE)
    assert _rows(store, "proposals") == 0


def test_an_approval_or_its_use_without_a_timezone_is_refused(store):
    prop = proposal()
    with pytest.raises(ValueError, match="時區"):
        store.add_approval(prop, BlockCode.BUDGET_INCREASE_TOO_LARGE, "ap-1", "token", NAIVE)
    with store.transaction() as tx, pytest.raises(ValueError, match="時區"):
        store.record_approval_use(tx, ApprovalUse(
            "ap-1", prop, operation_key(prop), "t-default",
            BlockCode.BUDGET_INCREASE_TOO_LARGE, 10, None, None), NAIVE)
    assert _rows(store, "approvals") == 0 and _rows(store, "approval_uses") == 0


def test_a_replay_at_a_time_without_a_timezone_is_refused(store):
    with pytest.raises(ValueError, match="時區"):
        store.replay("t1", 1, "ops", lambda: NAIVE)
    assert _rows(store, "dead_letter_ops") == 0
