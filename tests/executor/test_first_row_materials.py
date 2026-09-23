"""開始一筆記下核對材料(Phase 9 增量 3):[S663]。

副作用核對只能用寫入當時就記下、之後不變的資料判(之後改租戶設定或比例常數,過去的窗不能翻案)。
開始一筆在第一列多記四欄:比例允許加的量(依處理一筆開頭讀到的目前預算算出,不記常數)、單一廣告
上限與總額上限(簽發時讀到的租戶設定)、已用額度(判門檻用的那個數)。舊資料庫開啟時補上,舊列為空。
"""

import sqlite3

import pytest

from rtb.domain.attempt import operation_key
from rtb.executor import guardrails
from rtb.executor.inbox_store import InboxStore
from tests.executor.fakes import Harness, write_config

MATERIALS = "ratio_allowance, max_budget, aggregate_limit, aggregate_used"


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def materials(h, prop):
    return h.query(f"SELECT {MATERIALS} FROM attempts WHERE key = ? AND seq = 1",  # noqa: S608 - 固定欄位
                   (operation_key(prop),))[0]


# ---- [S663] ----
def test_the_first_attempt_row_records_what_it_was_checked_against(h):
    write_config(h.config, max_budget=900, aggregate_limit=5000)
    first = h.submit(task_id="t1", campaign_id="c1", requested_change={"new_budget": 140})
    h.process()
    second = h.submit(task_id="t2", campaign_id="c2", requested_change={"new_budget": 130})
    h.process()
    h.dsp.campaigns["c3"] = h.dsp.campaigns["c3"].__class__(budget=80, status="active", version=3)
    lower = h.submit(task_id="t3", campaign_id="c3", requested_change={"new_budget": 60})
    h.process()
    pause = h.submit(task_id="t4", campaign_id="c1", action_type="pause_campaign",
                     requested_change={}, campaign_version_observed=4)
    h.dsp.campaigns["c1"] = h.dsp.campaigns["c1"].__class__(budget=140, status="active",
                                                             version=4)
    h.process()

    allowance = guardrails.increase_allowance(100)  # 判比例用的同一支:現況 100 允許加 50
    assert allowance == 50
    assert materials(h, first) == (allowance, 900, 5000, 0)
    assert materials(h, second) == (allowance, 900, 5000, 40)  # 已用額度含第一筆加的 40
    # 減預算不判門檻,已用額度不記
    assert materials(h, lower) == (guardrails.increase_allowance(80), 900, 5000, None)
    assert materials(h, pause) == (None, 900, 5000, None)  # 暫停不判比例與門檻

    h.store.close()
    conn = sqlite3.connect(h.db)  # 模擬增量 3 之前的資料庫:拿掉四欄
    for column in MATERIALS.split(", "):
        conn.execute(f"ALTER TABLE attempts DROP COLUMN {column}")
    conn.commit()
    conn.close()
    h.store = InboxStore(h.db)  # 開啟時補欄位、不回填
    assert h.query(f"SELECT {MATERIALS} FROM attempts WHERE seq = 1") == [(None,) * 4] * 4  # noqa: S608 - 固定欄位
