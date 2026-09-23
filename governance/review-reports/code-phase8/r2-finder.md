severity: major

## F1 總曝險預判與開始交易間的競態仍讓無關核可放行過時決策
severity: major
blocking: 是 — 過時決策會在實際不需要總曝險核可時寫入 DSP，表示第 1 輪針對 [S512] 的修正仍可被正常併發時序繞過

引句:「有效核可只放行它核准、而且這一次真的需要的那一關」

`_approvals()` 在獨立交易中預判總曝險；若當時額度已滿，就把總曝險核可留在 `held`。真正開始嘗試前，`_too_late()` 看到這張核可便豁免新鮮度；但 `attempt_store.begin()` 隨後才在寫入交易內重算額度。若兩個交易之間另一筆嘗試釋放額度，這次實際不需要總曝險核可，過時決策卻已獲得豁免，最後照常寫入 DSP。

file: `src/rtb/executor/execution.py:499`

file: `src/rtb/executor/execution.py:511`

file: `src/rtb/executor/execution.py:661`

file: `src/rtb/executor/execution.py:720`

file: `src/rtb/executor/attempt_store.py:346`

重現命令以記憶體資料庫令預判額度為 90、寫入交易內變成 0；門檻 100、變更額 20：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'exec("
from pathlib import Path
from datetime import UTC, datetime, timedelta
from tests.executor.fakes import proposal, FakeDsp
from tests.executor.conftest import Clock, NOW
from tests.capability_samples import TEST_APPROVAL_KEY
from rtb.executor import approval, attempt_store
from rtb.executor.capability_signer import Tenant
from rtb.executor.execution import Executor, _Signed
from rtb.executor.inbox_store import InboxStore, BlockCode

class E(Executor):
    def _sign(self, proposal, key=None, not_after=None):
        expires = self.clock() + timedelta(minutes=5)
        if not_after is not None:
            expires = min(expires, datetime.fromtimestamp(not_after, UTC))
        return _Signed(\"token\", expires, tenant)

clock = Clock()
store = InboxStore(Path(\":memory:\"))
p = proposal(requested_change={\"new_budget\": 120})
store.accept(p, clock)
clock.now = NOW + timedelta(minutes=15)
tenant = Tenant(\"t-default\", frozenset({\"c1\"}), 1000, 100)
tok = approval.issue(
    TEST_APPROVAL_KEY, p, BlockCode.AGGREGATE_LIMIT_REACHED, tenant,
    approver=\"ops\", max_increase=1000,
    issued_at=int(clock().timestamp()), expires_at=int(clock().timestamp()) + 600)
store.add_approval(
    p, BlockCode.AGGREGATE_LIMIT_REACHED, approval.approval_id(tok), tok, clock())
values = iter((90, 0))
old = attempt_store.aggregate_used
attempt_store.aggregate_used = lambda tx, name, now: next(values)
try:
    dsp = FakeDsp()
    result = E(
        store, dsp, object(), Path(\".\"), clock,
        approval_key=TEST_APPROVAL_KEY).process_one()
    print(\"result=\", result.kind.value,
          None if result.block_code is None else result.block_code.value)
    print(\"writes=\", len(dsp.writes))
finally:
    attempt_store.aggregate_used = old
    store.close()
")'
```

輸出：

```text
result= executed None
writes= 1
```

判斷「這一次真的需要」必須使用開始嘗試交易內重算出的 `over_limit`，不能使用先前交易的預判結果。反向競態也存在：預判尚未超額而先移除核可，進交易時額度被別人占用，則一張其實正好需要且有效的核可會被錯誤忽略。

## F2 讀不回內容的升級前死信仍被宣告重放成功
severity: major
blocking: 是 — 第 1 輪 F3 的信封修正仍留有原漏洞；不可解析的舊死信會被放回佇列、稽核記成成功，但沒有耐久信封且之後無法處理

引句:「不是死信或讀不回提案就不補(之後的條件判斷會拒絕)。」

`_backfill_envelope()` 遇到不可解析的 payload 會回傳 `None`，但後續 `_replay_refusal()` 完全不檢查 payload 或信封是否存在。於是方法仍清除死信處置、歸零投遞次數並回 `REQUEUED`，兩筆稽核的 `envelope` 都是空值。這不但沒有修到第 1 輪要求的耐久追蹤，還會把不可處理的資料重新放入工作佇列。

file: `src/rtb/executor/inbox_store.py:712`

file: `src/rtb/executor/inbox_store.py:750`

file: `src/rtb/executor/inbox_store.py:758`

file: `src/rtb/executor/inbox_store.py:772`

重現命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'from pathlib import Path; from tests.executor.fakes import proposal; from tests.executor.conftest import NOW; from rtb.executor.inbox_store import InboxStore; s=InboxStore(Path(":memory:")); p=proposal(); s.accept(p,lambda:NOW); c=s._conn; c.execute("BEGIN IMMEDIATE"); c.execute("UPDATE proposals SET disposition='\''dead_letter'\'',dead_letter_reason='\''delivery_limit'\'',deliveries=5,payload='\''{'\''"); c.execute("COMMIT"); out=s.replay(p.task_id,p.revision,"ops",lambda:NOW); print("outcome=",out.value); print("proposal=",c.execute("SELECT state,disposition,deliveries FROM proposals").fetchall()); print("envelopes=",c.execute("SELECT count(*) FROM dead_letters").fetchone()[0]); print("audit=",c.execute("SELECT envelope,action,reason FROM dead_letter_ops ORDER BY id").fetchall()); s.close()'
```

輸出：

```text
outcome= requeued
proposal= [('pending', None, 0)]
envelopes= 0
audit= [(None, 'replay_requested', None), (None, 'replay_requeued', None)]
```

第 1 輪其餘折入逐項核對後，寫入鎖後讀時間、只檢查待處理名額、重送轉換時重驗核可到期、統一以例外回滾，以及核可政策版本的直接測試，均已落到對應路徑；未見同類漏修。
