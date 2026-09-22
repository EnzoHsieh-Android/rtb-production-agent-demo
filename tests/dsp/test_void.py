"""模擬 DSP 的作廢冪等鍵(Phase 3 增量 4 對帳):S78、S88、S89、S94。

作廢是對帳「判失敗」之前的證明:DSP 的寫入與作廢在同一把資料庫寫入鎖下一筆一筆進行,
所以結果只有兩種——舊寫入先提交(作廢回已提交),或作廢先成功(之後同鍵寫入一律被拒)。
"""

import sqlite3
import threading

import pytest

from rtb.dsp import store as dsp_store
from rtb.dsp.errors import OperationVoided
from rtb.dsp.store import CampaignStore, Operation
from rtb.executor.capability_signer import CapabilitySigner, SigningRefused
from tests.capability_samples import TEST_KEY
from tests.dsp.test_capability import call, serve, tables, token  # noqa: F401 - serve 是 fixture
from tests.executor.fakes import proposal, write_config

NOW = "2026-09-23T12:00:00+00:00"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "dsp.db"
    store = CampaignStore(path, clock=lambda: NOW)
    store.seed_campaign("c1", budget=100)
    store.close()
    return path


def budget_op(key="k1", budget=150, expected_version=1):
    return Operation("c1", "update_budget", {"new_budget": budget}, expected_version, key)


def open_store(path):
    return CampaignStore(path, clock=lambda: NOW)


def rows(path):
    conn = sqlite3.connect(path)
    try:
        return (conn.execute("SELECT * FROM campaigns").fetchall(),
                conn.execute("SELECT * FROM operations").fetchall(),
                conn.execute("SELECT * FROM idempotency_keys").fetchall())
    finally:
        conn.close()


# ---- [S78] ----
def test_the_dsp_voids_a_key_or_reports_the_commit_that_won(db):
    store = open_store(db)
    try:
        # 還沒有操作紀錄:記下作廢;再作廢一次也回已作廢(冪等)
        assert store.void("k1").state == "voided"
        assert store.void("k1").state == "voided"
        before = rows(db)
        with pytest.raises(OperationVoided):
            store.execute(budget_op(key="k1"))
        assert rows(db) == before  # 廣告、操作紀錄、冪等紀錄三張表都沒動

        # 已有操作紀錄:不作廢,回已提交並附那筆紀錄
        store.execute(budget_op(key="k2"))
        won = store.void("k2")
        assert won.state == "committed" and won.operation is not None
        assert (won.operation.idempotency_key, won.operation.version_after) == ("k2", 2)
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT key FROM voided_keys").fetchall() == [("k1",)]
        finally:
            conn.close()
        # 已提交的同鍵重放照舊回原結果,不受作廢影響(作廢只擋還沒提交的鍵)
        assert store.execute(budget_op(key="k2")).replayed is True
    finally:
        store.close()


def test_voiding_does_not_touch_the_operation_fingerprint():
    """作廢跟指紋無關:同鍵重送的指紋只算廣告、動作、參數、預期版本。"""
    op = budget_op()
    assert op.fingerprint() == Operation("c1", "update_budget", {"new_budget": 150}, 1,
                                         "k1", policy_version="p").fingerprint()


# ---- [S89] ----
def late_write(db, outcome):  # SQLite 連線不跨執行緒:各自在自己的執行緒開
    writer = open_store(db)
    try:
        outcome["write"] = writer.execute(budget_op(key="k1"))
    except OperationVoided as exc:
        outcome["write"] = exc
    finally:
        writer.close()


def late_void(db, outcome):
    voider = open_store(db)
    try:
        outcome["void"] = voider.void("k1")
    finally:
        voider.close()


def race(db, monkeypatch, winner):
    """輸家先開始、停在拿寫入鎖之前(拿鎖前的一切都已做完),贏家整筆做完,再放行輸家。"""
    real_begin = dsp_store.begin_immediate
    loser = "late-write" if winner == "void" else "late-void"
    at_lock, go = threading.Event(), threading.Event()

    def gated_begin(conn):
        if threading.current_thread().name == loser:
            at_lock.set()  # 輸家已做完拿鎖前的一切,停在拿鎖這一步
            assert go.wait(5)
        return real_begin(conn)

    monkeypatch.setattr(dsp_store, "begin_immediate", gated_begin)
    outcome: dict[str, object] = {}
    first, second = (late_void, late_write) if winner == "void" else (late_write, late_void)
    loser_thread = threading.Thread(target=second, args=(db, outcome), name=loser)
    loser_thread.start()
    assert at_lock.wait(5)  # 前置:輸家確實已經開始、停在拿鎖之前
    first(db, outcome)
    go.set()
    loser_thread.join(5)
    return outcome


@pytest.mark.parametrize("winner", ["void", "write"])
def test_voiding_and_a_late_write_are_linearized_by_the_write_lock(db, monkeypatch, winner):
    """兩者都已開始、都在等寫入鎖時才放行一方:查作廢若放在寫入鎖之外,「作廢先」那一例
    的舊寫入會先查到「沒作廢」、之後才提交——這支測試就是要抓那種寫法。"""
    outcome = race(db, monkeypatch, winner)

    if winner == "void":
        assert outcome["void"].state == "voided"
        assert isinstance(outcome["write"], OperationVoided)
        assert rows(db)[1] == []
    else:
        assert outcome["write"].version_after == 2
        assert outcome["void"].state == "committed"
    # 重開資料庫之後作廢仍有效(或已提交的仍是已提交)
    reopened = open_store(db)
    try:
        again = reopened.void("k1")
        assert again.state == ("voided" if winner == "void" else "committed")
        if winner == "void":
            with pytest.raises(OperationVoided):
                reopened.execute(budget_op(key="k1"))
    finally:
        reopened.close()


# ---- [S94] ----
def test_the_operation_lookup_returns_params_and_expected_version(db):
    store = open_store(db)
    try:
        store.execute(budget_op(key="k1", budget=150, expected_version=1))
        found = store.get_operation_by_key("k1")
        assert found is not None
        assert (found.params, found.expected_version) == ({"new_budget": 150}, 1)
        # 加欄位之前寫的舊列沒有預期版本:查詢誠實回空值
        conn = sqlite3.connect(db)
        conn.execute("UPDATE operations SET expected_version = NULL")
        conn.commit()
        conn.close()
        old = store.get_operation_by_key("k1")
        assert old is not None and old.expected_version is None
    finally:
        store.close()


def test_an_old_dsp_database_gains_the_expected_version_column(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(dsp_store.SCHEMA)
    conn.execute("ALTER TABLE operations DROP COLUMN expected_version")
    conn.execute("INSERT INTO campaigns (id, budget, status, version) "
                 "VALUES ('c1', 1, 'active', 2)")
    conn.execute("INSERT INTO operations (campaign_id, action, params_json, version_after, "
                 "received_at, committed_at, idempotency_key) VALUES ('c1', 'pause_campaign', "
                 "'{}', 2, 'x', 'x', 'old')")
    conn.commit()
    conn.close()

    store = CampaignStore(path)
    try:
        old = store.get_operation_by_key("old")
        assert old is not None and old.expected_version is None
    finally:
        store.close()


# ---- [S88] ----
def void_call(srv, key="k1", **cap):
    cap = {"action": "void_operation", "new_budget": None, "expected_version": 1,
           "idempotency_key": key, **cap}
    return call(srv, "POST", "/campaigns/c1/void", {"expected_version": 1},
                {"Idempotency-Key": key, "X-Capability": token(**cap)})


def test_voiding_needs_a_void_capability_scoped_to_the_key(serve, tmp_path):  # noqa: F811
    srv = serve()
    before = tables(srv)
    # 一般寫入憑證(改預算)拿來作廢:動作不符,拒收
    status, body = call(srv, "POST", "/campaigns/c1/void", {"expected_version": 1},
                        {"Idempotency-Key": "k1", "X-Capability": token(idempotency_key="k1")})
    assert (status, body["error"]) == (403, "capability_scope_mismatch")
    # 暫停憑證的新預算本來就是空值:除了動作,其他範圍全對得上——只靠動作不符擋下
    pause_cap = token(action="pause_campaign", idempotency_key="k1", expected_version=1)
    assert call(srv, "POST", "/campaigns/c1/void", {"expected_version": 1},
                {"Idempotency-Key": "k1", "X-Capability": pause_cap})[0] == 403
    # 鍵、租戶、預期版本任一不符也拒收
    assert void_call(srv, idempotency_key="k2")[0] == 403
    assert void_call(srv, tenant="t-other")[0] == 403
    assert void_call(srv, expected_version=2)[0] == 403
    assert tables(srv) == before
    # 範圍都對:作廢成功,再作廢一次也成功
    assert void_call(srv) == (200, {"state": "voided", "operation": None})
    assert void_call(srv)[1]["state"] == "voided"

    # 簽發端:簽作廢只看租戶,不看預算上限
    config = write_config(tmp_path / "tenants.json", campaigns=("c1",), max_budget=10)
    signer = CapabilitySigner(TEST_KEY)
    over_cap = proposal(campaign_id="c1", requested_change={"new_budget": 999})
    with pytest.raises(SigningRefused):
        signer.sign(over_cap, "k1", config, 1_800_000_000)
    signed = signer.sign_void(over_cap, "k1", config, 1_800_000_000)
    from rtb.capabilitykit import decode
    voided = decode(signed, TEST_KEY)
    assert (voided["action"], voided["new_budget"], voided["idempotency_key"]) == (
        "void_operation", None, "k1")
    with pytest.raises(SigningRefused) as refused:
        signer.sign_void(proposal(campaign_id="c9"), "k1", config, 1_800_000_000)
    assert refused.value.reason == "campaign_not_allowed"


def test_an_out_of_range_expected_version_is_rejected_before_anything_is_voided(serve):  # noqa: F811
    """預期版本要跟新預算一樣有上界:超過資料庫整數上限的值不能一路帶到寫入或作廢。"""
    srv = serve()
    before = tables(srv)
    huge = 2**63

    status, body = call(srv, "POST", "/campaigns/c1/void", {"expected_version": huge},
                        {"Idempotency-Key": "k1",
                         "X-Capability": token(action="void_operation", new_budget=None,
                                               expected_version=huge, idempotency_key="k1")})

    assert (status, body["error"]) == (422, "validation_rejected")
    assert tables(srv) == before  # 三張表與作廢表都沒動
