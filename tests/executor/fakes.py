"""執行迴圈測試共用的替身:假的 DSP 讀寫用戶端、租戶設定檔、帶一份提案的收件口。

假 DSP 預設照真 DSP 的語意回應(寫入成功就升版本、記完整操作紀錄;同一把鍵再寫一次,
內容相同回第一次的結果、內容不同回冪等衝突;作廢時已有紀錄就回已提交,否則記下作廢、
之後同鍵寫入回 409 操作已作廢);冪等語意要跟真 DSP(src/rtb/dsp/store.py)對齊,
改那邊時回頭改這裡。測試要模擬別的回應就排進 `answers`/`void_answers`,
要在呼叫當下插一腳(例如趁讀取時送新修訂)就設 `on_read`/`on_write`。
"""

import json
import os
import threading
from dataclasses import replace
from pathlib import Path

from rtb.domain.proposal import POLICY_VERSION, ActionType, parse_proposal
from rtb.executor.attempt_store import DspCall, DspCallKind, DspCallResult, dsp_error_code
from rtb.executor.capability_signer import CapabilitySigner
from rtb.executor.execution import (
    CampaignView,
    DspUnavailable,
    Executor,
    OperationRecord,
    VoidAnswer,
    WriteAnswer,
)
from rtb.executor.inbox_store import InboxStore
from tests.capability_samples import TEST_APPROVAL_KEY, TEST_KEY
from tests.domain.proposal_samples import valid


def proposal(**overrides):
    # Phase 8 起執行前檢查會擋政策版本不是現行版本的提案;樣本預設的 "v1" 不是現行版本,執行端測試
    # 預設改用現行版本。要測舊版本被擋就明寫 policy_version(新規則造成的改動,不是放寬檢查)
    overrides.setdefault("policy_version", POLICY_VERSION)
    parsed = parse_proposal(valid(**overrides))
    assert parsed.proposal is not None, parsed.errors
    return parsed.proposal


# 總曝險門檻預設給寬鬆值:Phase 6 之前的測試不是在測總曝險,缺欄會當 0 把加預算全擋掉
LOOSE_AGGREGATE_LIMIT = 10**12


def write_config(path: Path, campaigns=("c1", "c2", "c3"), max_budget=1000,
                 aggregate_limit=LOOSE_AGGREGATE_LIMIT):
    spec = {"campaigns": list(campaigns), "max_budget": max_budget}
    if aggregate_limit is not None:
        spec["aggregate_limit"] = aggregate_limit
    path.write_text(json.dumps({"tenants": {"t-default": spec}}), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


class FakeDsp:
    def __init__(self):
        self.campaigns = {c: CampaignView(budget=100, status="active", version=3)
                          for c in ("c1", "c2", "c3")}
        self.answers: list[WriteAnswer] = []  # 排好的寫入回應;空了就照真 DSP 的語意成功
        self.read_failures = 0  # 接下來幾次讀取要失敗
        self.lookup_failures = 0
        self.operations: dict[str, OperationRecord] = {}  # 冪等鍵 -> 完整操作紀錄
        self.voided: set[str] = set()
        self.void_answers: list[VoidAnswer] = []  # 排好的作廢回應;空了就照真 DSP 的語意
        self.voids: list[str] = []
        self.writes: list[tuple] = []
        self.reads: list[str] = []
        self.lookups: list[str] = []
        self.on_read = self.on_write = self.on_call = None
        # 多個執行緒(並行測試裡的多個工作者)同時呼叫時,狀態的讀改寫要一段做完。攔截點
        # (on_write)在鎖外面先跑:等在攔截點的工作者不能握著鎖,否則接手的另一個永遠寫不進來
        self._lock = threading.RLock()

    def _called(self, kind):
        if self.on_call is not None:
            self.on_call(kind)

    @staticmethod
    def _notify(on_call, kind, status, error=None, failure=None):
        """照真用戶端的分類,對呼叫端傳來的回呼回報一次呼叫(Phase 9);假的沒有網路,耗時記 0。"""
        if status is None:
            result = failure or DspCallResult.TIMEOUT
        elif 200 <= status < 300:
            result = DspCallResult.RESPONDED
        else:
            result = DspCallResult.CLIENT_ERROR if status < 500 else DspCallResult.SERVER_ERROR
        code = None if status is None or error is None else dsp_error_code({"error": error})
        on_call(DspCall(kind, result, status, 0.0, code))

    def read_campaign(self, campaign_id, *, on_call):
        try:
            found = self._read_campaign(campaign_id)
        except DspUnavailable:
            self._notify(on_call, DspCallKind.READ_CAMPAIGN, None,
                         failure=DspCallResult.SERVER_ERROR)
            raise
        self._notify(on_call, DspCallKind.READ_CAMPAIGN, 200 if found is not None else 404,
                     None if found is not None else "campaign_not_found")
        return found

    def _read_campaign(self, campaign_id):
        self._called("read")
        with self._lock:
            self.reads.append(campaign_id)
        if self.on_read is not None:
            self.on_read(campaign_id)
        with self._lock:
            if self.read_failures:
                self.read_failures -= 1
                raise DspUnavailable("讀取失敗")
            return self.campaigns.get(campaign_id)

    def write(self, prop, key, token, *, on_call):
        answer = self._write(prop, key, token)
        self._notify(on_call, DspCallKind.WRITE, answer.status, answer.error, answer.failure)
        return answer

    def _write(self, prop, key, token):
        self._called("write")
        with self._lock:
            self.writes.append((prop, key, token))
        if self.on_write is not None:
            self.on_write(prop, key, token)
        with self._lock:
            if self.answers:
                return self.answers.pop(0)
            if key in self.voided:
                return WriteAnswer(409, "operation_voided")
            existing = self.operations.get(key)
            if existing is not None:  # 同鍵重送:內容相同回第一次的結果,不同就是冪等衝突
                if existing == self.record_for(prop, existing.version_after):
                    return WriteAnswer(200, None, existing.version_after)
                return WriteAnswer(422, "idempotency_conflict")
            return self.apply(prop, key)

    def apply(self, prop, key):
        """照真 DSP 的語意套用一次寫入:升版本、記操作紀錄。"""
        with self._lock:
            return self._apply(prop, key)

    def _apply(self, prop, key):
        current = self.campaigns[prop.campaign_id]
        if prop.action_type is ActionType.UPDATE_BUDGET:
            updated = replace(current, budget=prop.requested_change["new_budget"],
                              version=current.version + 1)
        else:
            updated = replace(current, status="paused", version=current.version + 1)
        self.campaigns[prop.campaign_id] = updated
        self.operations[key] = self.record_for(prop, updated.version)
        return WriteAnswer(200, None, updated.version)

    @staticmethod
    def record_for(prop, version_after, **overrides):
        """這份提案若由 DSP 提交,操作紀錄長什麼樣;overrides 用來造出內容對不上的紀錄。"""
        fields = {
            "campaign_id": prop.campaign_id, "action": prop.action_type.value,
            "new_budget": prop.requested_change.get("new_budget"),
            "expected_version": prop.campaign_version_observed, "version_after": version_after,
        }
        return OperationRecord(**{**fields, **overrides})

    def _lookup(self, key, on_call):
        self._called("lookup")
        with self._lock:
            self.lookups.append(key)
            failed = bool(self.lookup_failures)
            if failed:
                self.lookup_failures -= 1
            found = None if failed else self.operations.get(key)
        if failed:
            self._notify(on_call, DspCallKind.LOOKUP_OPERATION, None,
                         failure=DspCallResult.SERVER_ERROR)
            raise DspUnavailable("查詢失敗")
        self._notify(on_call, DspCallKind.LOOKUP_OPERATION, 200 if found is not None else 404,
                     None if found is not None else "operation_not_found")
        return found

    def operation_version(self, key, *, on_call):
        record = self._lookup(key, on_call)
        return None if record is None else record.version_after

    def operation_record(self, key, *, on_call):
        return self._lookup(key, on_call)

    def void(self, prop, key, token, *, on_call):
        answer = self._void(prop, key, token)
        self._notify(on_call, DspCallKind.VOID, answer.status, answer.error, answer.failure)
        return answer

    def _void(self, prop, key, token):
        self._called("void")
        with self._lock:
            self.voids.append(key)
            if self.void_answers:
                return self.void_answers.pop(0)
            if key in self.operations:
                return VoidAnswer(200, None, "committed", self.operations[key])
            self.voided.add(key)
            return VoidAnswer(200, None, "voided")


class Harness:
    """一個執行行程資料庫 + 假 DSP + 真的簽發器與租戶設定檔,時間由測試控制。"""

    def __init__(self, tmp_path, clock, max_pending=20):
        self.tmp_path, self.clock = tmp_path, clock
        self.db = tmp_path / "executor.db"
        self.store = InboxStore(self.db, max_pending=max_pending)
        self.dsp = FakeDsp()
        self.config = write_config(tmp_path / "tenants.json")
        self.signer = CapabilitySigner(TEST_KEY)

    def executor(self, owner="executor"):
        return Executor(self.store, self.dsp, self.signer, self.config, self.clock, owner,
                        TEST_APPROVAL_KEY)

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
