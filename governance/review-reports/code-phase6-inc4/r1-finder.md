severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 「目前佔額度」明細缺少提案身分，稽核無法說明是哪幾筆
severity: major
blocking: 是 — 違反 S367；查詢五無法逐筆識別目前佔用額度的任務與修訂，事故 F7 的稽核結果不完整，現有測試又只驗金額而假綠。

引句:「目前佔這個租戶額度的一把鍵:計入金額,以及是不是 Phase 6 之前沒有租戶的舊列。」

`Holding` 只有 `key`、`amount`、`legacy`，缺少合約要求的 `task_id`、`revision`，也沒有設計所列的廣告、開始時間與目前狀態。結果雖能回答「用了多少」，卻無法回答「目前是哪幾份提案佔住額度」，也無法用任務與修訂和停下紀錄／人工核可紀錄對帳。

file: `src/rtb/executor/attempt_store.py:375`

file: `src/rtb/executor/observability.py:66`

現有 S365／S367 測試只加總或比較 `holding` 的金額，沒有檢查逐筆身分欄位，因此未捕捉這項合約缺口。

引句:「assert sum(entry.amount for entry in audit.holding) == 100」

file: `tests/executor/test_observability.py:170`

重現：

```text
$ PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c "from rtb.executor.attempt_store import Holding; from rtb.executor.observability import AggregateAudit; print(Holding.__annotations__); print(AggregateAudit.__annotations__)"
{'key': <class 'str'>, 'amount': <class 'int'>, 'legacy': <class 'bool'>}
{'stopped': tuple[rtb.executor.observability.Stopped, ...], 'passed': tuple[rtb.executor.observability.Passed, ...], 'holding': tuple[rtb.executor.attempt_store.Holding, ...], 'utilization': <class 'rtb.executor.observability.Utilization'>}
```

S362 屬增量 3，未列為缺漏。停下紀錄三個索引與舊嘗試表補租戶索引的順序符合本次指定設計；既有未結案查詢的全表掃描亦未列為問題。
