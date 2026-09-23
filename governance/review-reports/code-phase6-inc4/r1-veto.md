severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 範圍外的佔額度項目遺失稽核身分
severity: major
blocking: 是 — 違反 S367；正式稽核無法辨認時間範圍外、目前仍佔用額度的提案。

引句:「holding: tuple[Holding, ...]  # 目前佔額度的每一把鍵,不受範圍限制;加總就是已用額度」

`Holding` 只有 `key`、`amount`、`legacy`，缺少設計要求的任務、修訂、廣告、開始時間與目前狀態。當未結案嘗試開始於查詢範圍之外時，它會出現在 `holding`，卻不會出現在 `passed`，因此呼叫端無法從其他輸出補回身分。

file: `src/rtb/executor/attempt_store.py:375`

file: `src/rtb/executor/observability.py:134`

file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:339`

file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:364`

現有測試只核對 `holding` 的金額，沒有核對明細欄位，因此會假綠。

file: `tests/executor/test_observability.py:191`

具體輸入：租戶 `tenant-a` 有一筆兩小時前開始、仍未結案、預留 70 的提案；查詢範圍只取最近一小時。預期 `holding` 至少能指出 `task_id=outside-window`、修訂 1、`campaign_id=campaign-7`、開始時間與 `state=in_flight`。實際只剩鍵、70 與非舊列旗標。

重現命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c $'from datetime import timedelta\nfrom rtb.executor.inbox_store import InboxStore\nfrom rtb.executor import attempt_store, observability\nfrom rtb.executor.attempt_store import Reservation\nfrom tests.executor.conftest import NOW\nfrom tests.executor.fakes import proposal\ns=InboxStore(":memory:")\np=proposal(task_id="outside-window", campaign_id="campaign-7")\nwith s.transaction() as tx:\n attempt_store.begin(tx,p,NOW-timedelta(hours=2),capability_expires_at=NOW+timedelta(minutes=2),reservation=Reservation("tenant-a",70,1000))\nwith s.transaction() as tx:\n a=observability.aggregate_audit(tx,"tenant-a",1000,NOW,NOW-timedelta(hours=1),NOW+timedelta(hours=1))\nprint("passed=",a.passed)\nprint("holding=",a.holding)\nprint("holding_fields=",tuple(a.holding[0].__dataclass_fields__))'
```

輸出：

```text
passed= ()
holding= (Holding(key='k1-a1fcba55d71cd8e5445769fbd9462dcdca4be75ad4ede1453dc9ae9da135c11b', amount=70, legacy=False),)
holding_fields= ('key', 'amount', 'legacy')
```
