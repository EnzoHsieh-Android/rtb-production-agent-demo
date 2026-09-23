"""多個執行迴圈同時跑(Phase 4 增量 3a):互斥只靠收件表的租約與收據,不再有單一執行者鎖。

沿用本專案既有的並行測試做法(嘗試紀錄、Mock DSP、分析行程的並行測試都是這樣寫):同一個行程裡
開多個執行緒,每個執行緒各自建自己的收件表連線與執行迴圈物件(不同擁有者、共用同一個資料庫檔與
同一個假 DSP),用執行緒柵欄在指定位置讓兩邊同時放行。

柵欄一律放在進資料庫交易之前:開始一筆與寫嘗試紀錄都包在立即取得寫入鎖的交易裡,柵欄放在交易
裡面的話,先拿到鎖的那個等柵欄、另一個拿不到鎖進不來,兩邊互等。所以柵欄掛在那些方法的最開頭
(它們一進去才開交易)。等待都有逾時,卡住時測試失敗而不是掛著。
"""

import threading

import pytest

from rtb.domain.attempt import AttemptState, operation_key
from rtb.executor import attempt_store, runner
from rtb.executor.execution import Executor, LeaseLost, Result
from rtb.executor.inbox_store import InboxStore
from tests.executor.conftest import Clock
from tests.executor.fakes import Harness, proposal
from tests.executor.test_runner import ENV, argv

WAIT = 10  # 秒:柵欄與信號的逾時
LEASE = 60


@pytest.fixture
def h(tmp_path, clock):
    harness = Harness(tmp_path, clock)
    yield harness
    harness.close()


def worker(h, owner, action, clock=None):
    """一個工作者要做的事;連線在呼叫的那個執行緒裡才開(SQLite 連線只能在建立它的執行緒用)。"""
    def run():
        store = InboxStore(h.db)
        try:
            return action(Executor(store, h.dsp, h.signer, h.config, clock or h.clock, owner))
        finally:
            store.close()
    return run


def process(executor):
    return executor.process_one()


def reconcile(executor):
    return executor.reconcile_all()


def meet_before(monkeypatch, method, parties=2):
    """在 Executor 的某個方法最開頭(它一進去才開交易)放柵欄:每個執行緒第一次呼叫時等齊再放行。"""
    barrier = threading.Barrier(parties, timeout=WAIT)
    local = threading.local()
    original = getattr(Executor, method)

    def wrapped(self, *args, **kwargs):
        if not getattr(local, "met", False):
            local.met = True
            barrier.wait()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Executor, method, wrapped)


def run_all(*calls):
    """每個呼叫各開一個執行緒同時跑;回傳各自的結果或丟出的例外。"""
    results = [None] * len(calls)

    def target(i, call):
        try:
            results[i] = call()
        except Exception as exc:  # 測試要看到每個執行緒丟了什麼
            results[i] = exc

    threads = [threading.Thread(target=target, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WAIT)
        assert not thread.is_alive(), "執行緒卡住了"
    return results


def legacy_attempt(h, state, **proposal_overrides):
    """收件表沒有對應處理中訊息的舊鍵(Phase 3 時代留下、或收件表被人工處置過)。"""
    prop = proposal(**proposal_overrides)
    with h.store.transaction() as tx:
        row = attempt_store.begin(tx, prop, h.clock(), capability_expires_at=h.clock()).row
        if state is AttemptState.COMMITTED_UNVERIFIED:
            row = attempt_store.transition(tx, row.key, row.seq, state, h.clock(),
                                           written_version=4)
        elif state is not AttemptState.IN_FLIGHT:
            row = attempt_store.transition(tx, row.key, row.seq, state, h.clock())
    return row.key


def states(h, key):
    return [r[2] for r in h.attempts() if r[0] == key]


# ---- [S130] ----
def test_two_live_workers_process_a_message_once(h, monkeypatch):
    h.submit()
    meet_before(monkeypatch, "process_one")  # 兩邊在取件之前一起放行

    results = run_all(worker(h, "A", process), worker(h, "B", process))

    kinds = sorted(r.kind.value for r in results)
    assert kinds == sorted([Result.EXECUTED.value, Result.IDLE.value])  # 一個處理,另一個沒拿到
    assert len(h.dsp.writes) == 1
    assert h.proposals() == [("t1", 1, "pending", "handed_off", None)]


# ---- [S131] ----
def test_two_workers_never_run_the_same_campaign_at_once(h, monkeypatch):
    h.submit(task_id="t1")
    h.submit(task_id="t2", requested_change={"new_budget": 160})  # 同一個廣告的另一份提案
    # 兩邊都取完件、都還沒開始嘗試時一起放行:測的是開始嘗試那一道(同廣告已有未結案嘗試就不開始)。
    # 取件那一道(已有嘗試的廣告不交出去)是增量 1 已測過的另一道
    meet_before(monkeypatch, "_take")
    # 先開始嘗試的那一方停在 DSP 寫入,等另一方的開始嘗試有結論才放行:「同時」指的是它還在途中。
    # 不停住的話它會一路寫完、嘗試結案,另一方之後合法開始,就測不到這一道了
    other_done = threading.Event()
    h.dsp.on_write = lambda *_a: other_done.wait(WAIT)

    results = [None, None]

    def run(i, owner):
        results[i] = worker(h, owner, process)()
        if results[i].kind is Result.DEFERRED:
            other_done.set()

    threads = [threading.Thread(target=run, args=(i, o)) for i, o in enumerate("AB")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
        assert not thread.is_alive()

    kinds = sorted(r.kind.value for r in results)
    assert kinds == sorted([Result.EXECUTED.value, Result.DEFERRED.value])
    assert len(h.dsp.writes) == 1  # DSP 不會同時有兩筆這個廣告的寫入
    assert len({r[0] for r in h.attempts()}) == 1  # 只有一份開始嘗試


def _pause_a_at_the_dsp_write(h):
    """A 走到假 DSP 的寫入攔截點就停,等測試放行;別的執行緒呼叫寫入不受影響。"""
    at_dsp, go = threading.Event(), threading.Event()
    a_thread = []

    def hook(*_args):
        if threading.current_thread() in a_thread:
            at_dsp.set()
            assert go.wait(WAIT)

    h.dsp.on_write = hook
    return at_dsp, go, a_thread


# ---- [S129] ----
def test_a_restarting_worker_leaves_a_live_workers_attempt_alone(h):
    prop = h.submit()
    at_dsp, go, a_thread = _pause_a_at_the_dsp_write(h)
    result = []
    a = worker(h, "A", process)
    thread = threading.Thread(target=lambda: result.append(a()))
    a_thread.append(thread)
    thread.start()
    assert at_dsp.wait(WAIT)  # A 已寫下嘗試中、正在等 DSP

    # 另一個工作者重啟:只啟動、不跑任何一輪(等同重啟恢復跑完就結束)
    assert runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp,
                      max_rounds=0, sleep=lambda _s: None, owner="B") == 0
    assert states(h, operation_key(prop)) == ["in_flight"]  # A 那筆沒被動

    go.set()
    thread.join(WAIT)
    assert result and result[0].kind is Result.EXECUTED  # A 沒停機,照常寫完
    assert states(h, operation_key(prop))[-1] == "verified"
    assert h.proposals() == [("t1", 1, "pending", "handed_off", None)]
    assert len(h.dsp.operations) == 1


def test_a_restarting_worker_leaves_a_live_legacy_reconciliation_alone(h):
    """舊鍵沒有收件表訊息,對帳它時也會先寫嘗試中再呼叫 DSP:另一個工作者這時重啟,不能把它當
    孤兒轉掉(代碼審第 2 輪外家席重現:轉掉的話 DSP 已套用、本地卻停在結果不明)。"""
    key = legacy_attempt(h, AttemptState.UNKNOWN)
    at_dsp, go, a_thread = _pause_a_at_the_dsp_write(h)
    result = []
    a = worker(h, "A", reconcile)
    thread = threading.Thread(target=lambda: result.append(a()))
    a_thread.append(thread)
    thread.start()
    assert at_dsp.wait(WAIT)  # A 查不到、檢查通過、寫下嘗試中,正在等 DSP
    assert states(h, key)[-1] == "in_flight"

    assert runner.run(argv(h.db, h.config), environ=ENV, clock=h.clock, dsp=h.dsp,
                      max_rounds=0, sleep=lambda _s: None, owner="B") == 0
    assert states(h, key)[-1] == "in_flight"  # A 那筆沒被動

    go.set()
    thread.join(WAIT)
    assert not thread.is_alive()
    assert result == [False]  # A 沒停機:對帳正常結束(子執行緒的例外不會傳回來,要看回傳值)
    assert states(h, key)[-1] == "verified"  # A 照常寫完
    assert len(h.dsp.operations) == 1


# ---- [S138] ----
def test_reconciliation_recovers_a_stale_legacy_in_flight_attempt(h):
    """舊鍵的嘗試中寫下超過一個租約時間:沒人在做了,對帳自己轉結果不明再推進,不必等重啟;
    剛寫下的不碰。"""
    key = legacy_attempt(h, AttemptState.IN_FLIGHT)
    h.executor().reconcile_all()
    assert states(h, key) == ["in_flight"]  # 剛寫下:可能有人正在做

    h.clock.advance(seconds=LEASE - 1)  # 租約時間內的中間點:仍不碰
    h.executor().reconcile_all()
    assert states(h, key) == ["in_flight"]

    h.clock.advance(seconds=1)  # 剛好一個租約時間:算夠舊,接手
    h.executor().reconcile_all()

    assert states(h, key)[1] == "unknown" and states(h, key)[-1] == "verified"
    assert len(h.dsp.operations) == 1


# ---- [S132] ----
def test_a_worker_that_wakes_after_losing_its_lease_gives_up_without_halting(h):
    prop = h.submit()
    b_clock = Clock()
    at_dsp, go, a_thread = _pause_a_at_the_dsp_write(h)
    result = []
    a = worker(h, "A", process)
    thread = threading.Thread(target=lambda: result.append(a()))
    a_thread.append(thread)
    thread.start()
    assert at_dsp.wait(WAIT)  # A 在呼叫 DSP 前卡住

    b_clock.advance(seconds=LEASE + 1)  # 對 B 來說 A 的租約已過期
    # B 原子接手、轉結果不明、查不到、同鍵重送、驗證、確認
    worker(h, "B", reconcile, clock=b_clock)()
    assert states(h, operation_key(prop))[-1] == "verified"

    go.set()  # A 醒來:它那個卡住的寫入照樣送出(舊請求晚到),DSP 同鍵回原結果
    thread.join(WAIT)
    assert result and result[0].kind is Result.LEASE_LOST  # 寫不進任何結果,放棄,不停機
    assert len(h.dsp.operations) == 1 and h.dsp.campaigns["c1"].version == 4  # 只套用一次
    assert states(h, operation_key(prop))[-1] == "verified"
    assert h.proposals() == [("t1", 1, "pending", "handed_off", None)]


# ---- [S134] ----
def test_two_workers_reconciling_a_legacy_key_never_halt_and_write_once(h, monkeypatch):
    key = legacy_attempt(h, AttemptState.UNKNOWN)
    meet_before(monkeypatch, "_write")  # 兩邊查不到、檢查通過後,寫嘗試紀錄之前一起放行

    results = run_all(worker(h, "A", reconcile), worker(h, "B", reconcile))

    assert not [r for r in results if isinstance(r, Exception)]  # 輸家放棄這把鍵,不停機
    assert len(h.dsp.writes) <= 1 and len(h.dsp.operations) == 1
    assert states(h, key)[-1] == "verified"


# ---- [S136] ----
def test_two_workers_timing_out_on_a_legacy_key_never_halt(h, monkeypatch):
    key = legacy_attempt(h, AttemptState.COMMITTED_UNVERIFIED)
    h.dsp.read_failures = 2  # 兩邊驗證讀 DSP 都逾時
    meet_before(monkeypatch, "_verification_timeout")

    results = run_all(worker(h, "A", reconcile), worker(h, "B", reconcile))

    assert not [r for r in results if isinstance(r, Exception)]
    with h.store.transaction() as tx:
        row = attempt_store.latest(tx, key)
    assert row.state is AttemptState.COMMITTED_UNVERIFIED
    assert row.verification_timeouts == 1  # 只有一方記下


def test_a_sequence_mismatch_with_a_valid_receipt_still_halts():
    """對照:有收據而序號對不上,代表有人繞過租約寫了嘗試紀錄,仍是系統錯誤(Phase 3 的 S69 守
    端到端,這裡守分流本身)。"""
    from rtb.executor.execution import ExecutorHalted, _no_progress

    with pytest.raises(LeaseLost):
        _no_progress("k", None)
    with pytest.raises(ExecutorHalted):
        _no_progress("k", object())  # type: ignore[arg-type]
