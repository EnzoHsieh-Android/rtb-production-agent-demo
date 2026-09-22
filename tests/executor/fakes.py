"""執行迴圈測試共用的替身:假的 DSP 讀寫用戶端、租戶設定檔、帶一份提案的收件口。

假 DSP 預設照真 DSP 的語意回應(寫入成功就升版本、記操作紀錄);測試要模擬別的回應就排進
`answers`,要在呼叫當下插一腳(例如趁讀取時送新修訂)就設 `on_read`/`on_write`。
"""

import json
import os
from dataclasses import replace
from pathlib import Path

from rtb.domain.proposal import ActionType, parse_proposal
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.execution import CampaignView, DspUnavailable, Executor, WriteAnswer
from rtb.executor.inbox_store import InboxStore
from tests.capability_samples import TEST_KEY
from tests.domain.proposal_samples import valid


def proposal(**overrides):
    parsed = parse_proposal(valid(**overrides))
    assert parsed.proposal is not None, parsed.errors
    return parsed.proposal


def write_config(path: Path, campaigns=("c1", "c2", "c3"), max_budget=1000):
    path.write_text(json.dumps({"tenants": {"t-default": {
        "campaigns": list(campaigns), "max_budget": max_budget}}}), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


class FakeDsp:
    def __init__(self):
        self.campaigns = {c: CampaignView(budget=100, status="active", version=3)
                          for c in ("c1", "c2", "c3")}
        self.answers: list[WriteAnswer] = []  # 排好的寫入回應;空了就照真 DSP 的語意成功
        self.read_failures = 0  # 接下來幾次讀取要失敗
        self.lookup_failures = 0
        self.operations: dict[str, int] = {}  # 冪等鍵 -> 寫入後版本
        self.writes: list[tuple] = []
        self.reads: list[str] = []
        self.lookups: list[str] = []
        self.on_read = self.on_write = self.on_call = None

    def _called(self, kind):
        if self.on_call is not None:
            self.on_call(kind)

    def read_campaign(self, campaign_id):
        self._called("read")
        self.reads.append(campaign_id)
        if self.on_read is not None:
            self.on_read(campaign_id)
        if self.read_failures:
            self.read_failures -= 1
            raise DspUnavailable("讀取失敗")
        return self.campaigns.get(campaign_id)

    def write(self, prop, key, token):
        self._called("write")
        self.writes.append((prop, key, token))
        if self.on_write is not None:
            self.on_write(prop, key, token)
        if self.answers:
            return self.answers.pop(0)
        return self.apply(prop, key)

    def apply(self, prop, key):
        """照真 DSP 的語意套用一次寫入:升版本、記操作紀錄。"""
        current = self.campaigns[prop.campaign_id]
        if prop.action_type is ActionType.UPDATE_BUDGET:
            updated = replace(current, budget=prop.requested_change["new_budget"],
                              version=current.version + 1)
        else:
            updated = replace(current, status="paused", version=current.version + 1)
        self.campaigns[prop.campaign_id] = updated
        self.operations[key] = updated.version
        return WriteAnswer(200, None, updated.version)

    def operation_version(self, key):
        self._called("lookup")
        self.lookups.append(key)
        if self.lookup_failures:
            self.lookup_failures -= 1
            raise DspUnavailable("查詢失敗")
        return self.operations.get(key)


class Harness:
    """一個執行行程資料庫 + 假 DSP + 真的簽發器與租戶設定檔,時間由測試控制。"""

    def __init__(self, tmp_path, clock, max_pending=20):
        self.tmp_path, self.clock = tmp_path, clock
        self.db = tmp_path / "executor.db"
        self.store = InboxStore(self.db, max_pending=max_pending)
        self.dsp = FakeDsp()
        self.config = write_config(tmp_path / "tenants.json")
        self.signer = CapabilitySigner(TEST_KEY)

    def executor(self):
        return Executor(self.store, self.dsp, self.signer, self.config, self.clock)

    def submit(self, **overrides):
        prop = proposal(**overrides)
        self.store.accept(prop, self.clock)
        return prop

    def process(self):
        return self.executor().process_one()

    def query(self, sql, params=()):
        with self.store.transaction() as tx:
            return tx.conn.execute(sql, params).fetchall()

    def attempts(self):
        return self.query("SELECT key, seq, state, code, send_count, written_version "
                          "FROM attempts ORDER BY key, seq")

    def proposals(self):
        return self.query("SELECT task_id, revision, state, disposition, block_code "
                          "FROM proposals ORDER BY task_id, revision")

    def close(self):
        self.store.close()
