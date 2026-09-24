"""Mock DSP 儲存層的事故測試:每條測試名稱描述一個失敗情境與它要守住的不變量。"""

import ast
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from rtb.dsp.errors import (
    CampaignNotFound,
    IdempotencyConflict,
    MetricsNotFound,
    StoreBusy,
    UnknownAction,
    ValidationRejected,
    VersionConflict,
)
from rtb.dsp.store import CampaignStore, Operation

NOW = "2026-09-21T12:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    s = CampaignStore(tmp_path / "dsp.db", clock=lambda: NOW)
    s.seed_campaign("c1", budget=100)
    return s


def budget_op(key="k1", budget=150, expected_version=1, campaign="c1"):
    return Operation(
        campaign_id=campaign,
        action="update_budget",
        params={"new_budget": budget},
        expected_version=expected_version,
        idempotency_key=key,
    )


def test_same_key_same_payload_applies_once_and_returns_original_result(store):
    first = store.execute(budget_op())
    second = store.execute(budget_op())

    assert second.replayed is True and first.replayed is False
    assert second.operation_id == first.operation_id
    assert second.version_after == first.version_after == 2
    assert store.get_campaign("c1").budget == 150
    assert len(store.history("c1")) == 1


def test_same_key_different_payload_is_rejected_and_changes_nothing(store):
    store.execute(budget_op(budget=150))

    with pytest.raises(IdempotencyConflict):
        store.execute(budget_op(budget=999))

    assert store.get_campaign("c1").budget == 150
    assert store.get_campaign("c1").version == 2
    assert len(store.history("c1")) == 1


def test_stale_expected_version_is_rejected_without_writing(store):
    store.execute(budget_op(key="k1", budget=150, expected_version=1))

    with pytest.raises(VersionConflict):
        store.execute(budget_op(key="k2", budget=200, expected_version=1))

    assert store.get_campaign("c1").budget == 150
    assert len(store.history("c1")) == 1
    assert store.get_operation_by_key("k2") is None


# 每一種寫入動作的參數範例。第三輪合約審計指出:新增第三種動作時若另開分支跳過共用的版本
# 檢查,只列舉現有兩種動作的測試接不住;所以這張表必須涵蓋路由表上的每一種寫入動作(下一支
# 測試守著),新增動作沒補範例就紅,補了就自動被版本測試涵蓋。
WRITE_ACTION_PARAMS = {"update_budget": {"new_budget": 9999}, "pause_campaign": {}}


@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))
def test_every_write_action_with_the_same_key_applies_once_and_replays(store, action):
    """每一種會改廣告狀態的寫入動作,同鍵同內容重送都只套用一次(Phase 11:冪等宣稱寫「每一種」,
    原本只有改預算有同鍵測試)。新增動作沒補範例,下面守表的測試會擋。"""
    op = Operation("c1", action, WRITE_ACTION_PARAMS[action], expected_version=1,
                   idempotency_key="k1")

    first, second = store.execute(op), store.execute(op)

    assert (first.replayed, second.replayed) == (False, True)
    assert second.operation_id == first.operation_id
    assert store.get_campaign("c1").version == 2
    assert len(store.history("c1")) == 1


def test_every_write_action_on_the_http_routes_has_a_version_check_example():
    """寫入路由分兩類:改廣告的每一種都要有範例;作廢路由不改廣告、不經寫入入口,另有測試
    (tests/dsp/test_void.py)。新增路由兩類都沒歸就紅。"""
    from rtb.dsp.server import CAMPAIGN_WRITE_ACTIONS, ROUTES

    posts = {action for method, _, action in ROUTES if method == "POST"}
    assert set(CAMPAIGN_WRITE_ACTIONS) == set(WRITE_ACTION_PARAMS)
    assert posts == set(CAMPAIGN_WRITE_ACTIONS) | {"void_operation"}


def test_only_the_store_module_writes_to_the_dsp_database():
    """2026-09-22 第四輪合約審計指出:版本檢查集中在儲存模組的寫入入口;另寫一個端點直接對
    資料庫下 UPDATE、不經過那個入口,上面列舉動作的測試全都看不到。這裡鎖住「DSP 裡只有
    儲存模組碰資料庫」:其他檔案出現寫入語句或直接用資料庫連線就紅。"""
    import re

    dsp = Path(__file__).resolve().parents[2] / "src" / "rtb" / "dsp"
    pattern = re.compile(
        r"\b(UPDATE|INSERT\s+INTO|DELETE\s+FROM|REPLACE\s+INTO)\b"
        r"|\._conn\b|sqlite3\.connect|\bconnect\(",
        re.IGNORECASE)
    offenders = [f"{file.name}:{number}"
                 for file in sorted(dsp.rglob("*.py")) if file.name != "store.py"
                 for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1)
                 if pattern.search(line)]

    assert offenders == []


@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))
@pytest.mark.parametrize("expected_version", [2, 3, 100])
def test_a_future_expected_version_is_rejected_not_only_a_stale_one(
    store, action, expected_version
):
    """2026-09-22 合約審計指出:原本只測「差 1 的舊版本」,把比對改成「只擋比現在小的」
    (未來版本照樣放行)測試仍全綠;第二輪又指出只對暫停放寬也測不到。現況是版本 1,
    每一種寫入動作帶比它大的預期版本都必須拒收。"""
    with pytest.raises(VersionConflict):
        store.execute(Operation("c1", action, WRITE_ACTION_PARAMS[action],
                                expected_version=expected_version, idempotency_key="k1"))

    assert store.get_campaign("c1").budget == 100
    assert store.get_campaign("c1").status == "active"
    assert store.get_campaign("c1").version == 1
    assert store.history("c1") == []


@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))
def test_every_write_action_rejects_a_stale_expected_version(store, action):
    store.execute(budget_op(key="k0", budget=150, expected_version=1))  # 現況推進到版本 2

    with pytest.raises(VersionConflict):
        store.execute(Operation("c1", action, WRITE_ACTION_PARAMS[action],
                                expected_version=1, idempotency_key="k1"))

    assert store.get_campaign("c1").version == 2
    assert len(store.history("c1")) == 1


def test_pause_with_a_stale_expected_version_is_rejected_without_writing(store):
    """2026-09-22 合約審計指出:全庫沒有任何一支測試對「暫停」動作做版本衝突,只對暫停豁免
    版本檢查,全套測試照樣綠。樂觀鎖要對每一種寫入動作都成立。"""
    store.execute(budget_op(key="k1", budget=150, expected_version=1))

    with pytest.raises(VersionConflict):
        store.execute(Operation("c1", "pause_campaign", {}, expected_version=1,
                                idempotency_key="p1"))

    assert store.get_campaign("c1").status == "active"
    assert store.get_campaign("c1").version == 2
    assert len(store.history("c1")) == 1


def test_each_operation_bumps_version_by_one_and_records_received_and_committed_times(store):
    store.execute(budget_op(key="k1", budget=150, expected_version=1))
    store.execute(budget_op(key="k2", budget=160, expected_version=2))

    versions = [h.version_after for h in store.history("c1")]
    assert versions == [2, 3]
    for entry in store.history("c1"):
        assert entry.received_at and entry.committed_at
        assert entry.committed_at >= entry.received_at


def test_invalid_budget_is_a_permanent_rejection_and_changes_nothing(store):
    with pytest.raises(ValidationRejected):
        store.execute(budget_op(budget=-5))

    assert store.get_campaign("c1").version == 1
    assert store.history("c1") == []


def test_pause_campaign_sets_paused_and_bumps_version(store):
    op = Operation("c1", "pause_campaign", {}, expected_version=1, idempotency_key="p1")
    store.execute(op)

    campaign = store.get_campaign("c1")
    assert campaign.status == "paused" and campaign.version == 2


def test_idempotency_record_survives_reopening_the_store(tmp_path):
    path = tmp_path / "dsp.db"
    first = CampaignStore(path, clock=lambda: NOW)
    first.seed_campaign("c1", budget=100)
    first.execute(budget_op())

    reopened = CampaignStore(path, clock=lambda: NOW)
    replay = reopened.execute(budget_op())

    assert replay.replayed is True
    assert len(reopened.history("c1")) == 1


def test_failure_between_state_change_and_idempotency_record_rolls_everything_back(
    store, monkeypatch
):
    def explode(*_args, **_kwargs):
        raise RuntimeError("crash after state change, before idempotency record")

    monkeypatch.setattr(store, "_record_idempotency", explode)

    with pytest.raises(RuntimeError):
        store.execute(budget_op())

    assert store.get_campaign("c1").budget == 100
    assert store.get_campaign("c1").version == 1
    assert store.history("c1") == []
    assert store.get_operation_by_key("k1") is None


def test_concurrent_requests_with_the_same_key_apply_exactly_once(tmp_path):
    path = tmp_path / "dsp.db"
    CampaignStore(path, clock=lambda: NOW).seed_campaign("c1", budget=100)
    workers = 20
    barrier = threading.Barrier(workers)
    results, errors = [], []

    def call():
        own_store = CampaignStore(path, clock=lambda: NOW)  # 每個執行緒自己的連線
        barrier.wait()
        try:
            results.append(own_store.execute(budget_op()))
        except Exception as exc:  # 收集以便斷言
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check = CampaignStore(path, clock=lambda: NOW)
    assert errors == []
    assert len(check.history("c1")) == 1
    assert check.get_campaign("c1").version == 2
    assert len({r.operation_id for r in results}) == 1
    assert sum(1 for r in results if not r.replayed) == 1


@pytest.mark.parametrize("bad_version", [True, 1.0, "1", None, 0, -1])
def test_expected_version_must_be_a_positive_plain_integer(store, bad_version):
    with pytest.raises(ValidationRejected):
        store.execute(budget_op(expected_version=bad_version))

    assert store.get_campaign("c1").version == 1 and store.history("c1") == []


def test_budget_above_the_sqlite_integer_limit_is_a_permanent_rejection(store):
    with pytest.raises(ValidationRejected):
        store.execute(budget_op(budget=2**63))

    assert store.get_campaign("c1").version == 1
    assert store.execute(budget_op(key="k2", budget=2**63 - 1)).version_after == 2


@pytest.mark.parametrize("bad_budget", [0, True, "5", None, 1.5])
def test_non_positive_or_non_integer_budgets_are_rejected(store, bad_budget):
    with pytest.raises(ValidationRejected):
        store.execute(budget_op(budget=bad_budget))

    assert store.history("c1") == []


def test_unknown_action_is_rejected_as_a_permanent_error(store):
    with pytest.raises(UnknownAction):
        store.execute(Operation("c1", "delete_everything", {}, 1, "k1"))


@pytest.mark.parametrize("bad_key", ["", "a/b", "a?b", "x" * 129, "é", "abc\n", "abc "])
def test_idempotency_key_must_be_short_plain_ascii(store, bad_key):
    with pytest.raises(ValidationRejected):
        store.execute(budget_op(key=bad_key))


def test_lookup_by_key_is_not_reported_as_a_replay(store):
    first = store.execute(budget_op())

    looked_up = store.get_operation_by_key("k1")

    assert first.replayed is False and looked_up.replayed is False
    assert looked_up.operation_id == first.operation_id


def test_lock_contention_beyond_the_wait_limit_raises_store_busy(tmp_path):
    path = tmp_path / "dsp.db"
    CampaignStore(path, clock=lambda: NOW).seed_campaign("c1", budget=100)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    impatient = CampaignStore(path, clock=lambda: NOW, busy_timeout_seconds=0.2)

    with pytest.raises(StoreBusy):
        impatient.execute(budget_op())

    holder.execute("ROLLBACK")
    assert impatient.execute(budget_op()).replayed is False  # 鎖放開後同一把鍵可以重試


def test_permanent_database_errors_are_not_disguised_as_retryable_store_busy(store):
    store._conn.execute("PRAGMA query_only=1")  # 白箱:模擬唯讀資料庫這類永久故障

    with pytest.raises(sqlite3.OperationalError):
        store.execute(budget_op())


def test_original_error_is_not_hidden_when_the_transaction_is_already_gone(store, monkeypatch):
    def rolled_back_then_fail(*_args, **_kwargs):
        store._conn.execute("ROLLBACK")  # SQLite 有時會自己回滾(例如磁碟滿)
        raise RuntimeError("the real cause")

    monkeypatch.setattr(store, "_apply", rolled_back_then_fail)

    with pytest.raises(RuntimeError, match="the real cause"):
        store.execute(budget_op())


def test_constructor_closes_its_connection_when_setup_fails(tmp_path, monkeypatch):
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not a database" * 100)
    opened = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        opened.append(real_connect(*args, **kwargs))
        return opened[-1]

    monkeypatch.setattr(sqlite3, "connect", spy)

    with pytest.raises(sqlite3.DatabaseError):
        CampaignStore(garbage)

    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")  # 連線已被關閉


def test_extended_busy_error_codes_are_still_recognised_as_retryable_store_busy(store):
    class FakeConnection:
        in_transaction = False

        def execute(self, sql, *args):
            if sql == "BEGIN IMMEDIATE":
                exc = sqlite3.OperationalError("database is locked")
                exc.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT  # 擴充碼 517,低 8 位是 BUSY
                raise exc
            raise AssertionError(sql)

    store._conn = FakeConnection()

    with pytest.raises(StoreBusy):
        store.execute(budget_op())


def test_same_key_against_another_campaign_is_a_conflict_not_a_replay(store):
    store.seed_campaign("c2", budget=50)
    store.execute(budget_op(key="k1", campaign="c1"))

    with pytest.raises(IdempotencyConflict):
        store.execute(budget_op(key="k1", campaign="c2"))

    assert store.get_campaign("c2").version == 1


def test_same_key_with_a_different_expected_version_is_a_conflict_not_a_replay(store):
    store.execute(budget_op(key="k1", expected_version=1))

    with pytest.raises(IdempotencyConflict):
        store.execute(budget_op(key="k1", expected_version=2))


def test_database_runs_in_wal_mode_so_readers_do_not_block_the_writer(store):
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_schema_itself_refuses_a_second_operation_for_the_same_key(store):
    store.execute(budget_op(key="k1"))

    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO operations (campaign_id, action, params_json, version_after, "
            "received_at, committed_at, idempotency_key) VALUES ('c1','x','{}',9,'t','t','k1')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO idempotency_keys (key, fingerprint, operation_id) VALUES ('k1','f',1)"
        )


CRASH_CHILD = """
import os, sys
from pathlib import Path
from rtb.dsp.store import CampaignStore, Operation
store = CampaignStore(Path(sys.argv[1]))
def die(*_a, **_k):
    ops = store._conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    keys = store._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0]
    state = f"in_tx={store._conn.in_transaction} ops={ops} keys={keys}"
    print("DEATH " + state, file=sys.stderr, flush=True)
    os._exit(9)  # 行程猝死:沒有例外、沒有 ROLLBACK、沒有 finally
store._record_idempotency = die
store.execute(Operation("c1", "update_budget", {"new_budget": 150}, 1, "k1"))
"""


def test_process_death_between_state_change_and_idempotency_record_leaves_no_half_state(tmp_path):
    path = tmp_path / "dsp.db"
    CampaignStore(path, clock=lambda: NOW).seed_campaign("c1", budget=100)
    src = str(Path(__file__).resolve().parents[2] / "src")

    child = subprocess.run([sys.executable, "-c", CRASH_CHILD, str(path)],
                           env={**os.environ, "PYTHONPATH": src}, timeout=20, capture_output=True,
                           text=True)

    assert child.returncode == 9  # 確實是猝死,不是正常結束
    # 死點必須正好在「狀態與歷史已寫入、冪等紀錄還沒寫」之間,交易仍開著
    assert "DEATH in_tx=True ops=1 keys=0" in child.stderr
    reopened = CampaignStore(path, clock=lambda: NOW)
    assert reopened.get_campaign("c1").budget == 100  # 狀態沒有半途改動
    assert reopened.history("c1") == [] and reopened.get_operation_by_key("k1") is None
    assert reopened.execute(budget_op()).replayed is False  # 恢復後同一把鍵可正常套用


class NaiveStore:
    """對照組:先查後寫、沒有交易也沒有唯一約束的錯誤實作。

    它證明的是「這套執行緒屏障手法能讓 20 個請求同時在途,並且抓得到雙寫」。真實儲存層
    的抓漏力另外由變異檢查證明(拿掉交易或唯一約束後,並行測試會變紅)。"""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.conn.execute("CREATE TABLE IF NOT EXISTS naive_ops (idempotency_key TEXT)")

    def execute(self, op):
        seen = self.conn.execute("SELECT 1 FROM naive_ops WHERE idempotency_key = ?",
                                 (op.idempotency_key,)).fetchone()
        if seen:
            return "replayed"
        time.sleep(0.05)  # 放大「檢查」與「寫入」之間的空窗
        self.conn.execute("INSERT INTO naive_ops (idempotency_key) VALUES (?)",
                          (op.idempotency_key,))
        return "applied"


def _run_concurrently(workers, call):
    barrier, outcomes = threading.Barrier(workers), []

    def run(i):
        barrier.wait()
        outcomes.append(call(i))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes


def test_control_a_naive_check_then_write_store_double_applies_under_the_same_concurrent_test(
    tmp_path,
):
    path = tmp_path / "dsp.db"
    CampaignStore(path, clock=lambda: NOW).seed_campaign("c1", budget=100)
    NaiveStore(path)  # 先建好表;每條執行緒之後用自己的連線,SQLite 連線不能跨執行緒

    outcomes = _run_concurrently(20, lambda _i: NaiveStore(path).execute(budget_op()))

    assert outcomes.count("applied") > 1  # 對照組確實會雙寫,所以測試手法抓得到這種錯誤


def test_concurrent_same_key_with_different_payloads_lets_exactly_one_payload_win(tmp_path):
    path = tmp_path / "dsp.db"
    CampaignStore(path, clock=lambda: NOW).seed_campaign("c1", budget=100)

    def call(i):
        own = CampaignStore(path, clock=lambda: NOW)
        try:
            return own.execute(budget_op(budget=150 if i % 2 == 0 else 999))
        except IdempotencyConflict as exc:
            return exc

    outcomes = _run_concurrently(20, call)

    check = CampaignStore(path, clock=lambda: NOW)
    winning_budget = check.get_campaign("c1").budget
    assert winning_budget in (150, 999) and len(check.history("c1")) == 1
    successes = [o for o in outcomes if not isinstance(o, IdempotencyConflict)]
    assert len(successes) == 10 and len({o.operation_id for o in successes}) == 1
    assert all(isinstance(o, IdempotencyConflict) for o in outcomes if o not in successes)


def test_runtime_code_imports_only_the_standard_library_and_this_project():
    src = Path(__file__).resolve().parents[2] / "src"
    allowed = set(sys.stdlib_module_names) | {"rtb"}
    offenders = []
    for file in src.rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            offenders += [f"{file.name}: {n}" for n in names if n not in allowed]
            # 動態匯入會躲過上面的靜態掃描(2026-09-22 合約審計實測用 importlib 匯入第三方模組繞過)
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "__import__")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
            ):
                offenders.append(f"{file.name}: 動態匯入")
    assert offenders == []


def test_metrics_are_returned_as_raw_facts_for_a_known_window(store):
    store.seed_metrics("c1", "1d", impressions=1000, clicks=50, conversions=5,
                       spend=100.0, revenue=300.0)

    record = store.get_metrics("c1", "1d")

    assert (record.impressions, record.clicks, record.conversions) == (1000, 50, 5)
    assert (record.spend, record.revenue) == (100.0, 300.0)


def test_missing_fields_stay_missing_and_are_not_turned_into_zero(store):
    store.seed_metrics("c1", "1d", impressions=1000, clicks=None)

    record = store.get_metrics("c1", "1d")

    assert record.impressions == 1000 and record.clicks is None and record.revenue is None


def test_metrics_for_an_unknown_window_campaign_or_missing_row_are_typed_errors(store):
    store.seed_metrics("c1", "1d", impressions=1)

    with pytest.raises(ValidationRejected):
        store.get_metrics("c1", "5years")
    with pytest.raises(CampaignNotFound):
        store.get_metrics("ghost", "1d")
    with pytest.raises(MetricsNotFound):
        store.get_metrics("c1", "7d")


def test_seeding_the_same_window_again_replaces_the_previous_numbers(store):
    store.seed_metrics("c1", "1d", impressions=10)
    store.seed_metrics("c1", "1d", impressions=99)

    assert store.get_metrics("c1", "1d").impressions == 99


@pytest.mark.parametrize("bad", [
    {"impressions": "abc"}, {"impressions": b"x"}, {"impressions": True}, {"impressions": 1.5},
    {"impressions": 2**63}, {"spend": float("nan")}, {"spend": float("inf")},
    {"spend": "12"}, {"revenue": [1]},
])
def test_seeding_rejects_values_that_would_be_stored_or_served_as_something_else(store, bad):
    with pytest.raises(ValidationRejected):
        store.seed_metrics("c1", "1d", **bad)

    with pytest.raises(MetricsNotFound):
        store.get_metrics("c1", "1d")  # 被拒絕的資料沒有留下任何列


def test_seeding_rejects_an_unknown_window_or_an_unknown_campaign(store):
    with pytest.raises(ValidationRejected):
        store.seed_metrics("c1", "2h", impressions=1)
    with pytest.raises(CampaignNotFound):
        store.seed_metrics("ghost", "1d", impressions=1)


def test_amounts_are_stored_as_floats_and_integers_beyond_exact_float_range_are_rejected(store):
    store.seed_metrics("c1", "1d", spend=12)

    assert store.get_metrics("c1", "1d").spend == 12.0  # REAL 欄位:整數讀回是浮點數,不是「原樣」
    with pytest.raises(ValidationRejected):
        store.seed_metrics("c1", "1h", spend=2**53 + 1)  # 浮點數無法精確表示,存了會失真
    exact = store.seed_metrics("c1", "7d", spend=2**53)

    assert exact is None and store.get_metrics("c1", "7d").spend == float(2**53)
