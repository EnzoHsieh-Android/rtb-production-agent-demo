"""事故 F7 端到端(Phase 6 增量 1,[S340]):3000 個廣告各加一成、單筆都在單一廣告上限內,
8 個工作者執行緒並行,總曝險到門檻就停,DSP 收到的加預算總額不超過門檻。增量 3 起超過門檻的
先停在待核可(沒有人核可),跑完之後撥時鐘過提案到期、跑一次處理待核可,才確認成已擋下。

行程內假 DSP(寫入立刻成功、驗證也成功),每個工作者各開自己的收件表連線(SQLite 連線只能在建立它
的執行緒用);工作者一直處理到沒有待處理的提案為止。比照啟動程式:資料庫忙碌休息一下再來;取件後
碰到忙碌的那筆留在處理中,時鐘撥過租約後再跑一輪,讓它照正式的重新投遞被接手。
"""

import json
import threading
import time

from rtb.executor.execution import CampaignView, Executor, Result
from rtb.executor.inbox_store import InboxBusy, InboxStore
from tests.executor.fakes import Harness, proposal

CAMPAIGNS = 3000
WORKERS = 8
BUDGET, NEW_BUDGET = 100, 110  # 每筆加一成:10
AGGREGATE_LIMIT = 12_345  # 最多放行 1234 筆
EXPECTED_PASSES = AGGREGATE_LIMIT // (NEW_BUDGET - BUDGET)


def _seed(h):
    ids = [f"k{i:04d}" for i in range(CAMPAIGNS)]
    h.dsp.campaigns = {c: CampaignView(budget=BUDGET, status="active", version=3) for c in ids}
    h.config.write_text(json.dumps({"tenants": {"acct": {
        "campaigns": ids, "max_budget": 1000, "aggregate_limit": AGGREGATE_LIMIT}}}),
        encoding="utf-8")
    for i, campaign in enumerate(ids):
        h.store.accept(proposal(task_id=f"t{i}", campaign_id=campaign,
                                requested_change={"new_budget": NEW_BUDGET}), h.clock)


def _run_workers(h):
    """每個工作者開自己的收件表連線,一直處理到沒有待處理的提案為止。"""
    outcomes: list[Result] = []
    lock = threading.Lock()

    def work(owner):
        store = InboxStore(h.db)
        try:
            executor = Executor(store, h.dsp, h.signer, h.config, h.clock, owner)
            busy = 0
            while True:
                try:  # 比照啟動程式:資料庫忙碌是正常競爭,休息一下再來,不是讓工作者死掉
                    kind = executor.process_one().kind
                except InboxBusy:
                    busy += 1
                    assert busy < 50, "資料庫一直忙碌"
                    time.sleep(0.05)
                    continue
                if kind is Result.IDLE:
                    break
                with lock:
                    outcomes.append(kind)
        finally:
            store.close()

    threads = [threading.Thread(target=work, args=(f"w{n}",)) for n in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        assert not thread.is_alive(), "工作者卡住了"
    return outcomes


def test_f7_many_small_increases_stop_at_the_aggregate_limit(tmp_path, clock):
    started = time.monotonic()
    h = Harness(tmp_path, clock, max_pending=CAMPAIGNS)
    try:
        _seed(h)

        outcomes = _run_workers(h)
        for _ in range(5):  # 取件後碰到資料庫忙碌的那筆會留在處理中,要等租約到期才重投
            if not h.query("SELECT 1 FROM proposals WHERE disposition = 'in_progress'"):
                break
            h.clock.advance(seconds=61)
            outcomes += _run_workers(h)

        assert len(h.dsp.writes) == EXPECTED_PASSES
        assert (NEW_BUDGET - BUDGET) * len(h.dsp.writes) <= AGGREGATE_LIMIT
        assert outcomes.count(Result.EXECUTED) == EXPECTED_PASSES
        assert outcomes.count(Result.AWAITING_APPROVAL) == CAMPAIGNS - EXPECTED_PASSES
        h.clock.advance(hours=1, seconds=1)  # 沒有人核可:提案到期後由處理待核可結案
        assert h.executor().process_awaiting() == CAMPAIGNS - EXPECTED_PASSES
        blocked = h.query("SELECT count(*) FROM proposals WHERE disposition = 'blocked' "
                          "AND block_code = ?", ("aggregate_limit_reached",))
        assert blocked == [(CAMPAIGNS - EXPECTED_PASSES,)]
        recorded = h.query("SELECT count(*), min(used + amount > cap) FROM write_stops "
                           "WHERE kind = 'aggregate_limit_reached'")
        assert recorded == [(CAMPAIGNS - EXPECTED_PASSES, 1)]  # 每一筆擋下都記了、當時確實超過
    finally:
        h.close()
    assert time.monotonic() - started < 120  # 使用者 2026-09-25 裁定:上限 60 → 120 秒
