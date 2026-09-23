severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出(前面是過程紀錄);原文未改 -->

## F1 到期與版本變更同時成立時仍誤報 version_changed
severity: major
blocking: 是 — 兩條重跑路徑都先執行 `precheck()`，再獨立判斷期限；helper 看不到提案已過期，因此「已過期且版本也變更」會覆寫成 `version_changed`，違反新增註解與其他失敗維持 `not_happened`／既有確認方式的合約，並讓收件口回報錯誤的終點原因。

引句:「其他原因(含提案過期、權限不過)回空值,照嘗試結果代碼確認。」

引句:「blocked = proposal.decision_expires_at <= self.clock() or checked is not None」

`_after_expiry()` 與 `_reconcile_not_found()` 都把 `_version_changed_or_none(checked)` 傳入終點寫入，但沒有把期限判斷傳給 helper。應只在提案仍有效且 `checked is VERSION_CHANGED` 時覆寫。

重現命令：
```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -B -c \
'from datetime import UTC,datetime; from tests.executor.fakes import proposal; from rtb.executor.execution import CampaignView,precheck,_version_changed_or_none; p=proposal(decision_expires_at="2026-09-22T12:40:00+00:00"); c=precheck(p,CampaignView(100,"active",9)); print("expired=",p.decision_expires_at<=datetime(2026,9,22,13,tzinfo=UTC)); print("checked=",c.value); print("ack_block=",_version_changed_or_none(c).value)'
```

輸出：
```text
expired= True
checked= version_changed
ack_block= version_changed
```
