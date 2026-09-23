"""副作用核對(Phase 9 增量 3):[S652] [S653] [S664] [S665]。

未授權或違反護欄的副作用、重複有害副作用兩條目標為零的服務水準指標。核對函式逐項重看 DSP 的每一筆
寫入,只用寫入當時記下、之後不變的資料(嘗試第一列的提案快照與四樣核對材料、DSP 記下的租戶與政策
版本)。DSP 的窗用兩支唯讀端點翻頁讀,先讀 DSP、再開執行端快照。
"""

import json
import socket
import sqlite3
import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from rtb.capabilitykit import encode_audit_key
from rtb.domain.attempt import operation_key
from rtb.dsp.server import DspServer
from rtb.dsp.store import OPERATION_PAGE, CampaignStore, operation_cursor_query
from rtb.executor import attempt_store, guardrails, inbox_store
from rtb.executor.attempt_store import FirstRow, first_rows_for_proposal_query
from rtb.executor.inbox_store import InboxReads, ReadOnlyInbox
from rtb.httpclient import MAX_RESPONSE_BYTES, ClientHeader, request_json
from rtb.ops import side_effects as se
from rtb.ops import sli, slo
from tests.executor.fakes import proposal
from tests.ops.rows import Rows, at

K = "k1-" + "0" * 64
AUDIT = ("audit-" + "a" * 32).encode()  # 唯讀稽核金鑰(測試用,長度過最短金鑰)
PROP = proposal(requested_change={"new_budget": 140})  # 觀察到的版本 3、現況 100:加 40


def first_row(**overrides):
    base = FirstRow(key=K, task_id="t1", revision=1, campaign_id="c1", tenant="acme",
                    reserved_amount=40, ratio_allowance=50, max_budget=900,
                    aggregate_limit=5000, used_before=100, proposal=PROP,
                    snapshot_matches_key=True, written_at="2026-09-22T12:00:00.000000Z")
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
    # 代碼審第 1 輪:DSP 舊操作沒記預期版本,比照政策版本判無法核對,不是證實違規
    assert verdict(write(expected_version=None)) is U
    assert verdict(write(expected_version=None, campaign_id="c2")) is B
    # 代碼審第 1 輪:快照對不上它的鍵,不能拿來核授權範圍
    assert verdict(f=first_row(snapshot_matches_key=False)) is U

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
                                delay_seconds=0.0, audit_key=AUDIT)
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

    def first(self, key, prop, when, raw=None):
        """寫一把鍵的第一列;raw 給了就原樣存成提案快照(造毀損的快照)。"""
        self.rows.executor.execute(
            "INSERT INTO attempts (key, seq, campaign_id, state, send_count, "
            "verification_timeouts, written_at, task_id, revision, action, expected_version, "
            "proposal_json, tenant, reserved_amount) "
            "VALUES (?, 1, ?, 'in_flight', 1, 0, ?, ?, ?, ?, ?, ?, 'acme', 40)",
            (key, prop.campaign_id, attempt_store.iso(when), prop.task_id, prop.revision,
             prop.action_type.value, prop.campaign_version_observed,
             json.dumps(prop.to_primitives(), sort_keys=True) if raw is None else raw))

    def sources(self):
        return sli.Sources(self.rows.executor_db, self.rows.analyzer_db, self.url, 2.0, AUDIT)

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
    world.first(operation_key(alone), alone, at(minutes=35))  # 快照對得上鍵的正常第一列
    world.commit(operation_key(alone), at(minutes=40), budget=120)
    tally = sli.count("harmful_duplicates", at(0), at(minutes=60), world.sources())

    assert (tally.good, tally.valid, tally.unverifiable) == (1, 3, 1)
    assert tally.stable is True  # 副作用核對依因果順序讀,恆為穩定
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

    status, first_page = se.get_json(world.url, "/operations/after/0", 2.0, AUDIT)
    assert status == 200 and len(first_page["operations"]) == OPERATION_PAGE
    assert first_page["next"] == first_page["operations"][-1]["operation_id"]
    _, last_page = se.get_json(world.url, f"/operations/after/{first_page['next']}", 2.0, AUDIT)
    assert len(last_page["operations"]) == 6 and last_page["next"] is None
    _, cursor = se.get_json(world.url, f"/operations/since/{at(0).isoformat()}", 2.0, AUDIT)
    assert cursor["cursor"] == OPERATION_PAGE + 3  # 窗起點那一筆之前的最後一個編號
    steps = _plan(world.dsp_db, *operation_cursor_query(at(0)))
    assert any("operations_by_commit" in s for s in steps), steps

    inside = se.read_dsp_window(world.url, at(0), at(minutes=60), 2.0, AUDIT)
    assert [w.key[-1] for w in inside] == ["1", "2"]
    after = se.read_dsp_window(world.url, at(minutes=60), at(minutes=120), 2.0, AUDIT)
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
    status, page = se.get_json(world.url, "/operations/after/0", 2.0, AUDIT)
    assert status == 200 and len(page["operations"]) == OPERATION_PAGE
    assert len(json.dumps(page)) < MAX_RESPONSE_BYTES


# ---- 代碼審第 1 輪 ----
def _pages(monkeypatch, operations, lookups=None):
    """把 DSP 的回應換成固定內容:游標 0、一頁 operations;依鍵查的回 lookups[鍵](沒有回 404)。"""
    def fake_get(_url, path, _timeout, _audit_key=None):
        if path.startswith("/operations/since/"):
            return 200, {"cursor": 0}
        if path.startswith("/operations/after/"):
            return 200, {"operations": operations, "next": None}
        found = (lookups or {}).get(path.rsplit("/", 1)[-1])
        return (404, {"error": "operation_not_found"}) if found is None else found
    monkeypatch.setattr(se, "get_json", fake_get)


def _operation(key, committed_at, operation_id=1):
    return {"operation_id": operation_id, "idempotency_key": key, "campaign_id": "c1",
            "tenant": "acme", "action": "update_budget", "new_budget": 140,
            "expected_version": 3, "committed_at": committed_at, "policy_version": "p"}


def test_a_malformed_commit_time_from_the_dsp_is_unreadable_not_a_crash(world, monkeypatch):
    """DSP 回應是不可信輸入:提交時間讀不懂或沒帶時區,要轉成讀不到,不能讓整支評估器當掉。"""
    for bad in ("yesterday", "2026-09-22T12:00:00", 12):
        _pages(monkeypatch, [_operation(K, bad)])
        with pytest.raises(se.DspUnreadable):
            se.read_dsp_window(world.url, at(0), at(minutes=60), 2.0, AUDIT)
        for name in ("unauthorized_side_effects", "harmful_duplicates"):
            tally = sli.count(name, at(0), at(minutes=60), world.sources())
            assert tally.missing is True and tally.valid == 0, (bad, name)


def test_a_failed_lookup_of_a_sibling_key_makes_duplicates_missing(world, monkeypatch):
    """重複核對第二段逐鍵查 DSP 失敗(回錯誤、提交時間讀不懂),整條回資料來源缺。"""
    keys = ["k1-" + c * 64 for c in "ab"]  # 同一份提案內容兩把鍵
    for n, key in enumerate(keys):
        world.first(key, PROP, at(minutes=n))
    window = [_operation(keys[1], at(minutes=10).isoformat(), 2)]
    for answer in ((503, {"error": "busy"}), (200, _operation(keys[0], "garbage"))):
        _pages(monkeypatch, window, {keys[0]: answer})
        tally = sli.count("harmful_duplicates", at(0), at(minutes=60), world.sources())
        assert tally.missing is True and tally.valid == 0, answer


def test_the_same_key_committed_twice_is_a_harmful_duplicate(world, monkeypatch):
    """防禦性核對:DSP 的冪等鍵有唯一限制,正常路徑不會一鍵兩筆;讀到了第二筆算壞事件。"""
    key = operation_key(PROP)
    world.first(key, PROP, at(0))
    _pages(monkeypatch, [_operation(key, at(minutes=5).isoformat(), 1),
                         _operation(key, at(minutes=6).isoformat(), 2)])
    tally = sli.count("harmful_duplicates", at(0), at(minutes=60), world.sources())
    assert (tally.good, tally.valid) == (1, 2)
    assert tally.bad_at == (attempt_store.iso(at(minutes=6)),)

    # 快照讀不回來(不知道提案內容)也照樣抓得到:一鍵兩筆不用看快照就證實重複
    broken = "k1-" + "6" * 64
    world.first(broken, PROP, at(0), raw="{not json")
    _pages(monkeypatch, [_operation(broken, at(minutes=5).isoformat(), 1),
                         _operation(broken, at(minutes=6).isoformat(), 2)])
    tally = sli.count("harmful_duplicates", at(0), at(minutes=60), world.sources())
    assert (tally.good, tally.valid, tally.unverifiable) == (0, 1, 1)
    assert tally.bad_at == (attempt_store.iso(at(minutes=6)),)


def test_a_first_row_whose_snapshot_does_not_match_its_key_is_never_good(world):
    """批量讀取沿用快照的鍵一致性檢查:快照讀不回來或算不回它的鍵,不能拿來證明「沒重複」。"""
    mismatched, corrupted, fine = "k1-" + "7" * 64, "k1-" + "8" * 64, operation_key(PROP)
    world.first(mismatched, PROP, at(0))  # 快照解析得回來,但算出的鍵不是它
    world.first(corrupted, PROP, at(0), raw="{not json")
    world.first(fine, PROP, at(0))
    with ReadOnlyInbox(world.rows.executor_db).read_transaction() as tx:
        firsts = attempt_store.first_rows_for(tx, [mismatched, corrupted, fine])
    assert firsts[mismatched].proposal == PROP and not firsts[mismatched].snapshot_matches_key
    assert firsts[corrupted].proposal is None and not firsts[corrupted].snapshot_matches_key
    assert firsts[fine].snapshot_matches_key

    for key, minute in ((mismatched, 10), (corrupted, 20)):
        world.commit(key, at(minutes=minute))
    for name in ("harmful_duplicates", "unauthorized_side_effects"):
        tally = sli.count(name, at(0), at(minutes=60), world.sources())
        assert tally.good == 0 and tally.unverifiable == 2, (name, tally)


def test_the_dsp_being_unreachable_is_missing_not_zero(world):
    """DSP 讀不到:兩條目標為零的指標回資料來源缺,違規欄為空(不是「沒違規」)。"""
    with socket.socket() as probe:  # 拿一個剛釋放、沒人在聽的埠
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    sources = replace(world.sources(), dsp_url=f"http://127.0.0.1:{port}",
                      dsp_timeout_seconds=0.5)
    for name in ("unauthorized_side_effects", "harmful_duplicates"):
        tally = sli.count(name, at(0), at(minutes=60), sources)
        assert tally.missing is True and (tally.good, tally.valid) == (0, 0), name
    statuses = slo.evaluate(at(minutes=60), counter=lambda name, since, until: sli.count(
        name, since, until, sources))
    for status in statuses:
        if status.name in ("unauthorized_side_effects", "harmful_duplicates"):
            assert status.missing is True and status.violating is None, status


def test_a_page_boundary_between_equal_commit_times_reads_each_operation_once(world):
    """翻頁剛好切在第 50、51 筆,兩筆又是同一個提交時間:每一筆只讀一次,落在該落的窗。"""
    base = at(minutes=-30)
    tied = base + timedelta(seconds=100)
    times = [base + timedelta(seconds=n) for n in range(OPERATION_PAGE - 1)]
    times += [tied, tied, base + timedelta(seconds=200)]  # 第 50、51 筆同時間
    conn = sqlite3.connect(world.dsp_db)
    conn.executemany(
        "INSERT INTO operations (campaign_id, action, params_json, version_after, received_at, "
        "committed_at, idempotency_key, policy_version, expected_version) "
        "VALUES ('c1', 'update_budget', '{\"new_budget\": 1}', 1, ?, ?, ?, 'p', 1)",
        [(t.isoformat(), t.isoformat(), f"op-{n + 1}") for n, t in enumerate(times)])
    conn.commit()
    conn.close()

    def keys(since, until):
        return [w.key for w in se.read_dsp_window(world.url, since, until, 2.0, AUDIT)]

    everything = keys(base, base + timedelta(seconds=300))
    assert everything == [f"op-{n}" for n in range(1, len(times) + 1)]  # 不漏不重
    assert keys(base, tied) == [f"op-{n}" for n in range(1, OPERATION_PAGE)]  # 終點不含
    assert keys(tied, base + timedelta(seconds=300)) == ["op-50", "op-51", "op-52"]


def _call(world, path, value=None, header=ClientHeader.AUDIT_KEY):
    """value 是標頭裡原樣送的字串(稽核金鑰要先 base64url)。"""
    headers = None if value is None else {header: value}
    return request_json(f"{world.url}{path}", "GET", None, 2.0, headers)


def test_the_operation_list_endpoints_require_the_audit_key(world):
    """兩支列操作端點回全租戶的明細,只給帶唯讀稽核金鑰的讀(代使用者裁定);既有依鍵、依廣告
    的唯讀端點不變。"""
    for path in ("/operations/after/0", f"/operations/since/{at(0).isoformat()}"):
        assert _call(world, path)[0] == 401
        assert _call(world, path, encode_audit_key(b"x" * len(AUDIT)))[0] == 403
        assert _call(world, path, encode_audit_key(AUDIT + b"x"))[0] == 403
        assert _call(world, path, "not base64!")[0] == 403  # 解不開當帶錯
        assert _call(world, path, AUDIT.decode())[0] == 403  # 沒編碼的原文不收
        # 放在能力憑證標頭不算:稽核金鑰只認自己的專用標頭(代碼審第 2 輪)
        assert _call(world, path, encode_audit_key(AUDIT), ClientHeader.CAPABILITY)[0] == 401
        assert _call(world, path, encode_audit_key(AUDIT))[0] == 200
    assert _call(world, "/operations/" + K)[0] == 404  # 既有端點不要金鑰(查不到回 404)

    bare = DspServer(world.dsp_db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0)
    threading.Thread(target=bare.serve_forever, args=(0.02,), daemon=True).start()
    try:
        url = f"http://127.0.0.1:{bare.server_address[1]}"
        status, body = request_json(f"{url}/operations/after/0", "GET", None, 2.0,
                                    {ClientHeader.AUDIT_KEY: encode_audit_key(AUDIT)})
        assert status == 503 and body["error"] == "audit_not_configured"  # 沒設金鑰一律拒收
    finally:
        bare.shutdown()
        bare.server_close()


def _raw_response(world, path: bytes) -> bytes:
    """用原始位元組送請求(路徑裡的非 ASCII 位元組照伺服器的 latin-1 解碼進去),回整個回應。"""
    port = world.server.server_address[1]
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
        conn.sendall(b"GET " + path + b" HTTP/1.1\r\nHost: 127.0.0.1:" + str(port).encode()
                     + b"\r\nX-Dsp-Audit-Key: " + encode_audit_key(AUDIT).encode()
                     + b"\r\nConnection: close\r\n\r\n")
        return conn.makefile("rb").read()


def test_a_cursor_must_be_a_plain_decimal_within_the_integer_range(world):
    """上標數字(isdigit 收、int 不收)、超過 SQLite 整數上限的游標回 400 invalid_cursor,不是 500。"""
    superscript = _raw_response(world, b"/operations/after/\xb2")  # 上標 2
    assert b" 400 " in superscript and b"invalid_cursor" in superscript, superscript
    for cursor in ("9999999999999999999", "9223372036854775808", "-1", "1e3"):
        status, body = _call(world, "/operations/after/" + cursor, encode_audit_key(AUDIT))
        assert (status, body.get("error")) == (400, "invalid_cursor"), cursor
    assert _call(world, "/operations/after/9223372036854775807",
                 encode_audit_key(AUDIT))[0] == 200


def test_approval_uses_for_a_batch_of_keys_uses_the_key_index(world):
    """核可使用批量查詢走依鍵的索引,不每批整表掃(Phase 6 已核可放行數的查詢計畫另有測試釘住)。"""
    sql, params = inbox_store.approval_uses_for_query(["k1-a", "k1-b"])
    steps = _plan(world.rows.executor_db, sql, params)
    assert any("approval_uses_by_key" in s for s in steps), steps
    assert not [s for s in steps if s.startswith("SCAN")], steps


def test_a_non_latin1_audit_key_goes_through(world):
    """代碼審第 2 輪:共用讀法認可的非 Latin-1 金鑰(例:"密" 重複 32 次)也要送得出去:標頭裡放
    金鑰位元組的 base64url,DSP 解碼後比對。"""
    key = ("密" * 32).encode()
    server = DspServer(world.dsp_db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0,
                       audit_key=key)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert se.read_dsp_window(url, at(0), at(minutes=60), 2.0, key) == ()
        sources = replace(world.sources(), dsp_url=url, dsp_audit_key=key)
        tally = sli.count("unauthorized_side_effects", at(0), at(minutes=60), sources)
        assert tally.missing is False
    finally:
        server.shutdown()
        server.server_close()
