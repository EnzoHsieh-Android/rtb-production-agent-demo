"""指標測試的資料場景:用正式開法建好兩個資料庫的表,再直接寫入列,時間與內容逐筆掌控。

指標只讀,算的是「已經寫下來的紀錄」;走真的執行迴圈造這些紀錄,很難讓時間剛好落在窗界上。
這裡只負責寫測試資料,不驗任何行為。
"""

import sqlite3
from datetime import UTC, datetime, timedelta

from rtb import PROGRAM_VERSION
from rtb.analyzer.task_store import TaskStore
from rtb.domain.proposal import POLICY_VERSION
from rtb.executor.attempt_store import iso
from rtb.executor.capability_signer import Tenant
from rtb.executor.inbox_store import InboxStore

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
TENANTS = (Tenant("acme", frozenset({"c1", "c2"}), 1000, 500),
           Tenant("beta", frozenset({"c3"}), 1000, 500))
CAMPAIGN_TENANT = {"c1": "acme", "c2": "acme", "c3": "beta"}


def at(seconds: float = 0, minutes: float = 0) -> datetime:
    return T0 + timedelta(seconds=seconds, minutes=minutes)


class Rows:
    def __init__(self, tmp_path):
        self.executor_db = tmp_path / "executor.db"
        self.analyzer_db = tmp_path / "analyzer.db"
        InboxStore(self.executor_db).close()  # 建齊收件口與嘗試紀錄的表、索引
        TaskStore(self.analyzer_db).close()
        self.executor = sqlite3.connect(self.executor_db, isolation_level=None)
        self.analyzer = sqlite3.connect(self.analyzer_db, isolation_level=None)

    def close(self):
        self.executor.close()
        self.analyzer.close()

    # ---- 執行端 ----
    def event(  # noqa: PLR0913 - 事件表的每一欄
        self, when, task, kind, *, revision=1, digest=None, campaign="c1", key=None,
        policy=POLICY_VERSION, tenant="from_campaign", reason=None, deliveries=None,
        from_existing=False, version=PROGRAM_VERSION, source="executor_loop", actor="w1",
    ):
        """寫一列生命週期事件;租戶預設照廣告填(簽發後的事件),傳 None 代表沒記(簽發前)。"""
        if tenant == "from_campaign":
            tenant = CAMPAIGN_TENANT.get(campaign)
        self.executor.execute(
            "INSERT INTO lifecycle_events (at, task_id, revision, content_hash, campaign_id, key, "
            "policy_version, tenant, kind, reason, source, actor, deliveries, from_existing, "
            "program_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iso(when), task, revision, digest or f"h-{task}-{revision}", campaign,
             key or f"k-{task}-{revision}", policy, tenant, kind, reason, source, actor,
             deliveries, int(from_existing), version))

    def attempt(  # noqa: PLR0913 - 嘗試表的每一欄
        self, key, seq, state, when, *, task=None, revision=1, campaign="c1",
        tenant="from_campaign", send_count=1, version=PROGRAM_VERSION, code=None,
    ):
        """寫一列嘗試;第一列帶任務、修訂與租戶(跟正式寫法一樣只記在第一列)。"""
        if tenant == "from_campaign":
            tenant = CAMPAIGN_TENANT.get(campaign)
        first = seq == 1
        self.executor.execute(
            "INSERT INTO attempts (key, seq, campaign_id, state, code, send_count, "
            "verification_timeouts, written_at, task_id, revision, tenant, source, actor, "
            "program_version) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'executor_loop', 'w1', ?)",
            (key, seq, campaign, state, code, send_count, iso(when),
             (task or key) if first else None, revision if first else None,
             tenant if first else None, version))

    def attempts(self, key, states, *, task=None, campaign="c1", tenant="from_campaign",
                 version=PROGRAM_VERSION):
        """一把鍵的整串嘗試:[(狀態, 時間), ...]。"""
        for seq, (state, when) in enumerate(states, start=1):
            self.attempt(key, seq, state, when, task=task, campaign=campaign, tenant=tenant,
                         version=version)

    def dsp_call(  # noqa: PLR0913 - 呼叫紀錄的每一欄
        self, when, kind, result, *, status=None, error=None, latency_ms=10.0, task="t1",
        campaign="c1", key=None, version=PROGRAM_VERSION,
    ):
        self.executor.execute(
            "INSERT INTO dsp_calls (at, call_kind, result, status, error_code, latency_ms, "
            "task_id, revision, campaign_id, key, source, actor, program_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'executor_loop', 'w1', ?)",
            (iso(when), kind, result, status, error, latency_ms, task, campaign,
             key or f"k-{task}-1", version))

    def pending(self, task, received, *, revision=1, disposition=None):
        """收件表一列(待處理:處置為空;處理中:處置寫處理中)。"""
        self.executor.execute(
            "INSERT INTO proposals (task_id, revision, content_hash, state, payload, expires_at, "
            "received_at, disposition, deliveries) VALUES (?, ?, ?, 'pending', '{}', ?, ?, ?, 0)",
            (task, revision, f"h-{task}-{revision}", iso(received + timedelta(hours=1)),
             iso(received), disposition))

    # ---- 分析端 ----
    def task(self, task, created, campaign="c1"):
        self.analyzer.execute(
            "INSERT INTO tasks (task_id, seq, state, campaign_id, written_at) "
            "VALUES (?, 1, 'received', ?, ?)", (task, campaign, iso(created)))

    def follow_up(self, original, follow, when, generation=1, campaign="c1"):
        self.analyzer.execute(
            "INSERT INTO follow_ups (original_task_id, follow_up_task_id, generation, "
            "campaign_id, reason, outcome, written_at) "
            "VALUES (?, ?, ?, ?, 'version_changed', 'created', ?)",
            (original, follow, generation, campaign, iso(when)))

    def tool_call(self, when, endpoint, latency_ms=5.0, task="t1", outcome="ok"):
        self.analyzer.execute(
            "INSERT INTO tool_calls (task_id, task_seq, endpoint, outcome, latency_ms, at) "
            "VALUES (?, 1, ?, ?, ?, ?)", (task, endpoint, outcome, latency_ms, iso(when)))
