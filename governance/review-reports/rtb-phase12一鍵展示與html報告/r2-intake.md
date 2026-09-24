preflight-4: ran
r2 為 delta 審,前掃沿用 r1。

## 收貨機械重現(編排者 Claude 查證的部分;協調者核對的另補)

| id | 指令或讀檔 | 輸出摘要 | 判定 |
|---|---|---|---|
| n3 | sed -n 18,22p;80,91p tests/executor/test_f7_end_to_end.py | CAMPAIGNS = 3000、AGGREGATE_LIMIT = 12_345(最多放行 1234 筆);斷言 AWAITING_APPROVAL 筆數 = CAMPAIGNS − EXPECTED_PASSES,到期後由處理待核可全部結案成擋下 | HIT |
| n5 | grep -n "def issue" src/rtb/executor/approval.py;grep -n "def add_approval" src/rtb/executor/inbox_store.py | issue(key, proposal, stage, tenant, *, approver, max_increase, issued_at, expires_at)(approval.py:72);add_approval 在 inbox_store.py:1444 | HIT |
| n8 | Phase 12 增量 1 分支 phase12-inc1 的 src/rtb/executor/inbox_store.py LifecycleKind | 成員含 RECLAIMED、LEASE_RELEASED、APPROVAL_RELEASED、REPLAY_REQUEUED 四種放回待處理的事件 | HIT |
| n9 | grep class AwaitingOutcome / LastFailure / Freshness / WorthVerdict | AwaitingOutcome(inbox_store.py:501,EXPIRED/SUPERSEDED/RELEASED)、LastFailure(:93)、Freshness(src/rtb/domain/evidence.py:37)、WorthVerdict(src/rtb/domain/worth.py:24);都不在第 3 版對應清單 | HIT |
| n12 | grep -n "build-system\|^\[project\]" pyproject.toml;tests/executor/test_crash_recovery.py:155 | pyproject 沒有 [project] 也沒有 build-system;既有子行程測試都帶 PYTHONPATH: SRC | HIT |
| n13 | /Users/enzo/rtb-11b-rev/src/rtb/modelclient.py:328 | 入口只把 RTB_MODEL_LIVE 等於 "1" 當成開 | HIT |
| n15 | grep -n "def read_json" src/rtb/httpkit.py | 請求本文只有 read_json(:202)一支 | HIT |
| p1 | src/rtb/analyzer/policy.py 的 decide 回 explain(...)[0] | 正式決策函式把不提案原因丟掉,只回決策結果 | HIT |
| y3 | Phase 12 增量 1 的 flow.py(提交 5e5168e) | RoutePath 與 NoActionReason 已在 r2 期間補進對應表 | HIT |
