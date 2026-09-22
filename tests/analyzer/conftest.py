"""分析行程流程測試的共用工具:假的三個可替換介面、可控時鐘。"""

from datetime import UTC, datetime

import pytest

from rtb.analyzer.task_store import TaskStore

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    task_store = TaskStore(tmp_path / "analyzer.db")
    yield task_store
    task_store.close()


class Counting:
    """記錄被呼叫幾次、每次的引數;可以設成回傳固定值或丟固定例外。"""

    def __init__(self, returns=None, raises=None):
        self.calls = []
        self._returns, self._raises = returns, raises

    def __call__(self, *args):
        self.calls.append(args)
        if self._raises is not None:
            raise self._raises
        return self._returns

    @property
    def call_count(self):
        return len(self.calls)


def make_proposal(**overrides):
    from rtb.domain.proposal import parse_proposal
    from tests.domain.proposal_samples import valid

    result = parse_proposal(valid(**overrides))
    assert result.proposal is not None, result.errors
    return result.proposal


def make_evidence(**overrides):
    from types import MappingProxyType

    from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass

    fields = {
        "evidence_id": "e1", "task_id": "t1", "kind": EvidenceKind.CAMPAIGN_STATE,
        "source": "dsp", "observed_at": NOW, "campaign_version_observed": 3,
        "content_hash": "a" * 64, "trust_class": TrustClass.TRUSTED,
        "payload": MappingProxyType({"budget": 100}),
    }
    fields.update(overrides)
    return Evidence(**fields)
