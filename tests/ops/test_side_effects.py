"""副作用核對(Phase 9 增量 3):[S652] [S653] [S664] [S665]。

未授權或違反護欄的副作用、重複有害副作用兩條目標為零的服務水準指標。核對函式逐項重看 DSP 的每一筆
寫入,只用寫入當時記下、之後不變的資料(嘗試第一列的提案快照與四樣核對材料、DSP 記下的租戶與政策
版本)。DSP 的窗用兩支唯讀端點翻頁讀,先讀 DSP、再開執行端快照。
"""

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from rtb.dsp.server import DspServer
from rtb.dsp.store import OPERATION_PAGE, CampaignStore, operation_cursor_query
from rtb.executor import attempt_store, guardrails
from rtb.executor.attempt_store import FirstRow, first_rows_for_proposal_query
from rtb.executor.inbox_store import InboxReads
from rtb.httpclient import MAX_RESPONSE_BYTES
from rtb.ops import side_effects as se
from rtb.ops import sli
from tests.executor.fakes import proposal
from tests.ops.rows import Rows, at

K = "k1-" + "0" * 64
PROP = proposal(requested_change={"new_budget": 140})  # 觀察到的版本 3、現況 100:加 40


def first_row(**overrides):
    base = FirstRow(key=K, task_id="t1", revision=1, campaign_id="c1", tenant="acme",
                    reserved_amount=40, ratio_allowance=50, max_budget=900,
                    aggregate_limit=5000, used_before=100, proposal=PROP,
                    written_at="2026-09-22T12:00:00.000000Z")
    return replace(base, **overrides)


def write(**overrides):
    base = se.DspWrite(operation_id=1, key=K, campaign_id="c1", tenant="acme",
                       action="update_budget", new_budget=140, expected_version=3,
                       committed_at="2026-09-22T12:00:01+00:00",
                       policy_version=PROP.policy_version)
    return replace(base, **overrides)


def verdict(w=None, f="default", stages=frozenset()):
    return se.check_write(w or write(), first_row() if f == "default" else f, stages)[0]


G, B, U = se.Verdict.GOOD, se.Verdict.BAD, se.Verdict.UNVERIFIABLE
RATIO, AGGREGATE = "budget_increase_too_large", "aggregate_limit_reached"
PAUSE = proposal(action_type="pause_campaign", requested_change={})
LOWER = proposal(requested_change={"new_budget": 60})


# ---- [S652] ----
def test_the_side_effect_checker_flags_each_kind_of_violation(monkeypatch):
    assert verdict() is G
    assert verdict(f=None) is B  # 沒經過開始一筆
    for changed in ({"campaign_id": "c2"}, {"action": "pause_campaign"}, {"new_budget": 150},
                    {"expected_version": 4}, {"tenant": "beta"},
                    {"policy_version": "demo-pacing-v0"}):
        assert verdict(write(**changed)) is B, changed  # 超出授權範圍、政策版本不同
    assert verdict(f=first_row(max_budget=139)) is B  # 超過單一廣告上限
    assert verdict(f=first_row(max_budget=140)) is G  # 剛好等於上限合法
    assert verdict(f=first_row(ratio_allowance=39)) is B  # 加 40 超過允許量又沒有比例核可
    assert verdict(f=first_row(ratio_allowance=39), stages=frozenset({RATIO})) is G
    assert verdict(f=first_row(ratio_allowance=40)) is G  # 剛好等於允許量合法
    assert verdict(f=first_row(used_before=4961)) is B  # 4961 + 40 超過 5000
    assert verdict(f=first_row(used_before=4961), stages=frozenset({AGGREGATE})) is G
    assert verdict(f=first_row(used_before=4960)) is G  # 剛好等於門檻合法

    lower = first_row(proposal=LOWER, reserved_amount=0, ratio_allowance=0, used_before=None)
    assert verdict(write(new_budget=60), lower) is G  # 加的量 0:不核對比例與總曝險
    assert verdict(write(new_budget=60), replace(lower, max_budget=59)) is B  # 單一上限照樣核對
    paused = first_row(proposal=PAUSE, reserved_amount=0, ratio_allowance=None,
                       max_budget=None, used_before=None)
    pause_write = write(action="pause_campaign", new_budget=None)
    assert verdict(pause_write, paused) is G  # 暫停跳過數值護欄
    assert verdict(replace(pause_write, tenant="beta"), paused) is B  # 授權範圍照樣核對
    assert verdict(replace(pause_write, expected_version=9), paused) is B
    assert verdict(replace(pause_write, policy_version="demo-pacing-v0"), paused) is B

    # 判法只用寫入當時記下的資料:之後改比例常數不改變結果
    before = [verdict(f=first_row(ratio_allowance=n)) for n in (39, 40, 50)]
    monkeypatch.setattr(guardrails, "MAX_INCREASE_NUMERATOR", 0)
    assert [verdict(f=first_row(ratio_allowance=n)) for n in (39, 40, 50)] == before


# ---- [S664] ----
def test_unverifiable_writes_are_reported_apart_from_the_denominator():
    legacy = first_row(ratio_allowance=None, max_budget=None, aggregate_limit=None,
                       used_before=None)
    assert verdict(f=legacy) is U  # 沒有任何一項違規、但缺材料
    assert verdict(write(policy_version="demo-pacing-v0"), legacy) is B  # 有一項證實違規就判壞
    assert verdict(write(policy_version=None)) is U  # DSP 那一筆沒記政策版本
    assert verdict(write(policy_version=None, campaign_id="c2")) is B
    assert verdict(f=first_row(tenant=None)) is U

    other = "k1-" + "9" * 64
    writes = [write(), write(key="k1-" + "1" * 64, policy_version="x"),
              write(key="k1-" + "2" * 64), write(key="someone-else", new_budget=1)]
    firsts = {K: first_row(), "k1-" + "1" * 64: first_row(key="k1-" + "1" * 64),
              "k1-" + "2" * 64: legacy}
    tally = se.tally_unauthorized(writes, firsts, {})
    assert (tally.good, tally.valid, tally.unverifiable) == (1, 2, 1)  # 別的寫入者不算
    assert tally.bad_at == ("2026-09-22T12:00:01.000000Z",)
    assert other not in firsts


# ---- DSP 與執行端的場景 ----
class World:
    def __init__(self, tmp_path):
        self.rows = Rows(tmp_path)
        self.dsp_db = tmp_path / "dsp.db"
        store = CampaignStore(self.dsp_db)
        store.seed_campaign("c1", budget=100, tenant="acme")
        store.close()
        self.server = DspServer(self.dsp_db, fault_injection=False, hang_seconds=0.2,
                                delay_seconds=0.0)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.version = 1

    def commit(self, key, when, budget=140, policy="demo-pacing-v1"):
        conn = sqlite3.connect(self.dsp_db)
        conn.execute(
            "INSERT INTO operations (campaign_id, action, params_json, version_after, "
            "received_at, committed_at, idempotency_key, policy_version, expected_version) "
            "VALUES ('c1', 'update_budget', ?, ?, ?, ?, ?, ?, ?)",
            (json.dumps({"new_budget": budget}), self.version + 1, when.isoformat(),
             when.isoformat(), key, policy, self.version))
        conn.commit()
        conn.close()
        self.version += 1

    def first(self, key, prop, when):
        self.rows.executor.execute(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, task_id, revision, action, expected_version, "
            "proposal_json, tenant, reserved_amount) "
            "VALUES (?, 1, ?, 'in_flight', 1, 0, ?, ?, ?, ?, ?, ?, 'acme', 40)",
            (key, prop.campaign_id, attempt_store.iso(when), prop.task_id, prop.revision,
             prop.action_type.value, prop.campaign_version_observed,
             json.dumps(prop.to_primitives(), sort_keys=True)))

    def sources(self):
        return sli.Sources(self.rows.executor_db, self.rows.analyzer_db, self.url, 2.0)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.rows.close()


@pytest.fixture
def world(tmp_path):
    built = World(tmp_path)
    yield built
    built.close()


def _plan(path, sql, params):
    conn = sqlite3.connect(path)
    try:
        return [row[-1] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)]
    finally:
        conn.close()


# ---- [S653] ----
def test_a_second_commit_of_the_same_proposal_is_a_harmful_duplicate(world):
    keys = ["k1-" + c * 64 for c in "abc"]  # 同一份提案內容卻有三把鍵(例:鍵算法改過版)
    for n, key in enumerate(keys):
        world.first(key, PROP, at(minutes=-20 + n))
    world.commit(keys[0], at(minutes=-10))  # 第一筆在窗外
    world.commit(keys[1], at(minutes=10))
    world.commit(keys[2], at(minutes=20))
    world.commit("k1-" + "d" * 64, at(minutes=30))  # 找不到第一列:無法核對
    alone = proposal(task_id="t9", requested_change={"new_budget": 120})
    world.first("k1-" + "e" * 64, alone, at(minutes=35))
    world.commit("k1-" + "e" * 64, at(minutes=40), budget=120)
    tally = sli.count("harmful_duplicates", at(0), at(minutes=60), world.sources())

    assert (tally.good, tally.valid, tally.unverifiable) == (1, 3, 1)
    assert sorted(tally.bad_at) == [attempt_store.iso(at(minutes=10)),
                                    attempt_store.iso(at(minutes=20))]
    sql, params = first_rows_for_proposal_query("t1", 1)
    steps = _plan(world.rows.executor_db, sql, params)
    assert any("attempts_first_rows_by_task" in s for s in steps), steps


# ---- [S665] ----
def test_the_dsp_operation_window_reader_is_bounded_and_read_first(world, monkeypatch):
    conn = sqlite3.connect(world.dsp_db)
    base = at(minutes=-600)
    conn.executemany(  # 一頁以上的舊操作(直接寫列,快)
        "INSERT INTO operations (campaign_id, action, params_json, version_after, received_at, "
        "committed_at, idempotency_key, policy_version, expected_version) "
        "VALUES ('c1', 'update_budget', '{\"new_budget\": 1}', 1, ?, ?, ?, 'p', 1)",
        [((base + timedelta(seconds=n)).isoformat(), (base + timedelta(seconds=n)).isoformat(),
          f"old-{n}") for n in range(OPERATION_PAGE + 3)])
    conn.commit()
    conn.close()
    world.commit("k1-" + "1" * 64, at(0))  # 剛好在窗起點:在這個窗
    world.commit("k1-" + "2" * 64, at(minutes=30))
    world.commit("k1-" + "3" * 64, at(minutes=60))  # 剛好在窗終點:在下一個窗

    status, first_page = se.get_json(world.url, "/operations/after/0", 2.0)
    assert status == 200 and len(first_page["operations"]) == OPERATION_PAGE
    assert first_page["next"] == first_page["operations"][-1]["operation_id"]
    _, last_page = se.get_json(world.url, f"/operations/after/{first_page['next']}", 2.0)
    assert len(last_page["operations"]) == 6 and last_page["next"] is None
    _, cursor = se.get_json(world.url, f"/operations/since/{at(0).isoformat()}", 2.0)
    assert cursor["cursor"] == OPERATION_PAGE + 3  # 窗起點那一筆之前的最後一個編號
    steps = _plan(world.dsp_db, *operation_cursor_query(at(0)))
    assert any("operations_by_commit" in s for s in steps), steps

    inside = se.read_dsp_window(world.url, at(0), at(minutes=60), 2.0)
    assert [w.key[-1] for w in inside] == ["1", "2"]
    after = se.read_dsp_window(world.url, at(minutes=60), at(minutes=120), 2.0)
    assert [w.key[-1] for w in after] == ["3"]

    order, batches = _read_order(world, monkeypatch)
    assert order[0] == "dsp" and "executor" in order
    assert order.index("executor") > max(i for i, o in enumerate(order) if o == "dsp")
    assert batches == ["firsts", "uses"]  # 兩筆寫入各一批,不逐筆查


def _read_order(world, monkeypatch):
    """跑一次未授權副作用的計數,記下讀 DSP 與開執行端快照的順序、批量讀取函式各被叫幾次。"""
    order, batches = [], []
    real_get, real_open = se.get_json, se.ReadOnlyInbox

    def logged_get(*args):
        order.append("dsp")
        return real_get(*args)

    def logged_open(*args, **kwargs):
        order.append("executor")
        return real_open(*args, **kwargs)

    real_firsts, real_uses = attempt_store.first_rows_for, InboxReads.approval_uses_for
    monkeypatch.setattr(se, "get_json", logged_get)
    monkeypatch.setattr(se, "ReadOnlyInbox", logged_open)
    monkeypatch.setattr(attempt_store, "first_rows_for",
                        lambda tx, keys: batches.append("firsts") or real_firsts(tx, keys))
    monkeypatch.setattr(InboxReads, "approval_uses_for",
                        lambda self, tx, keys: batches.append("uses") or real_uses(self, tx, keys))
    sli.count("unauthorized_side_effects", at(0), at(minutes=60), world.sources())
    return order, batches


def test_a_full_operations_page_fits_the_client_response_limit(world):
    """每一欄都塞到上限的一整頁,共用用戶端照樣讀得回來(回應上限 64 KB)。"""
    conn = sqlite3.connect(world.dsp_db)
    conn.execute("UPDATE campaigns SET tenant = ? WHERE id = 'c1'", ("t" * 64,))
    conn.executemany(
        "INSERT INTO operations (campaign_id, action, params_json, version_after, received_at, "
        "committed_at, idempotency_key, policy_version, expected_version) "
        "VALUES ('c1', 'update_budget', ?, 1, ?, ?, ?, ?, ?)",
        [(json.dumps({"new_budget": 2**63 - 1}), at(n).isoformat(), at(n).isoformat(),
          f"{n:0128d}", "p" * 64, 2**63 - 1) for n in range(OPERATION_PAGE)])
    conn.commit()
    conn.close()
    status, page = se.get_json(world.url, "/operations/after/0", 2.0)
    assert status == 200 and len(page["operations"]) == OPERATION_PAGE
    assert len(json.dumps(page)) < MAX_RESPONSE_BYTES
