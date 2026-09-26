"""分析端驅動命令列的整條路徑與調查紀錄隔離(Phase 13 增量 2 [S1114];Phase 14 增量 3 改寫)。

Phase 14 增量 3(計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈拆增量〉3):分析端驅動拔掉
`--ai-judge`,原本這支檔裡帶假模型閘道跑的端到端([S1113] [S1116] [S1137] [S1142] [S1156] [S1160]、
AI 提案否決與 AI 開規則輪)隨入口刪除;只留「執行端不讀調查紀錄、送進收件口的提案只有規則欄位」這一條,
改用純規則的分析端跑。真的 DSP 與收件口在行程內,不碰模型。"""

import io
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from rtb.analyzer import runner
from rtb.analyzer.task_store import TaskStore
from rtb.dsp import seed
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor.inbox_server import InboxServer

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"


class RuleWorld:
    """一個情境:行程內的 DSP 與收件口、分析端資料庫;分析端只用九條規則。"""

    def __init__(self, tmp_path, *, tasks=(("t1", "c1"),)):
        self.root = Path(tmp_path)
        self.root.mkdir(parents=True, exist_ok=True)
        store = CampaignStore(self.root / "dsp.db")
        campaigns = sorted({c for _t, c in tasks})
        for campaign in campaigns:
            store.seed_campaign(campaign, budget=100)
            store.seed_metrics(campaign, "1h", impressions=500, clicks=12, conversions=1,
                               spend=0.5, revenue=5.0)
        seed.seed_platform_history(store, dict.fromkeys(campaigns, seed.DEMO_PROFILE),
                                   datetime.now(UTC))
        store.close()
        tasks_db = TaskStore(self.root / "analyzer.db")
        for task_id, campaign in tasks:
            tasks_db.create_task(task_id, campaign, datetime.now(UTC))
        tasks_db.close()

    def run_until_done(self, max_rounds=40):
        dsp = DspServer(self.root / "dsp.db", fault_injection=False, hang_seconds=0.2,
                        delay_seconds=0.0)
        inbox = InboxServer(self.root / "inbox.db", fault_injection=False)
        for server in (dsp, inbox):
            threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
        try:
            return runner.run(
                ["--db", str(self.root / "analyzer.db"),
                 "--dsp-url", f"http://127.0.0.1:{dsp.server_address[1]}",
                 "--inbox-url", f"http://127.0.0.1:{inbox.server_address[1]}",
                 "--timeout-seconds", "2", "--interval-seconds", "0.01"],
                max_rounds=max_rounds, out=io.StringIO(), err=io.StringIO())
        finally:
            for server in (dsp, inbox):
                server.shutdown()
                server.server_close()


# ---- [S1114] ----
def test_the_executor_never_sees_model_rounds(tmp_path):
    """[S1114] 執行端與收件口不讀調查紀錄表;送進收件口的提案只有規則的那一組欄位,不帶任何模型產生
    的欄位(Phase 14 增量 3:原本用 AI 選的提案,改成純規則提案)。"""
    offenders = []
    for path in sorted((SRC / "executor").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {word}" for word in (
            "investigation_rounds", "investigation_raw", "InvestigationRecord", "ai_judge",
            "rtb.analyzer.investigation") if word in text]
    assert offenders == []
    world = RuleWorld(tmp_path)
    assert world.run_until_done() == 0
    with sqlite3.connect(world.root / "inbox.db") as conn:
        [raw] = [r[0] for r in conn.execute("SELECT payload FROM proposals")]
    fields = set(json.loads(raw))
    assert fields == {"task_id", "revision", "campaign_id", "action_type", "requested_change",
                      "reason_codes", "evidence_refs", "campaign_version_observed",
                      "decision_created_at", "decision_expires_at", "policy_version",
                      "risk_summary"}
    assert json.loads(raw)["reason_codes"] == ["low_pacing"]
    with sqlite3.connect(world.root / "analyzer.db") as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        rounds = (conn.execute("SELECT count(*) FROM investigation_rounds").fetchone()[0]
                  if "investigation_rounds" in tables else 0)
    assert rounds == 0  # 純規則:不寫調查紀錄
