"""資料與指令的邊界:Phase 7 增量 3 的 S210、S211、S212(事故 F5)。

換任何攻擊文字,系統行為一個位元都不變:決策規則只讀可信證據;執行行程不讀提案的自由文字。
差異測試是取樣證明加回歸網,防忘記,不防刻意針對清單外寫法去改決策規則。
"""

import ast
import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from rtb.analyzer.dsp_client import _campaign_text
from rtb.analyzer.flow import NoAction, ProposalDecision
from rtb.analyzer.task_store import TaskRow
from rtb.domain.evidence import MAX_UNTRUSTED_TEXT_LENGTH, Evidence, EvidenceKind, TrustClass
from rtb.domain.proposal import content_hash
from rtb.domain.task_state import TaskState
from tests.adversarial_samples import CATEGORIES, NAME_LIMIT, SAMPLES

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
TASK = TaskRow(task_id="t1", seq=3, state=TaskState.ANALYZING, campaign_id="c1", proposal=None,
               error_detail=None, written_at=NOW)
SPEND = {"underpacing": 0.5, "on_pace": 100.0}  # 預算 100,1 小時窗:0.5 明顯偏低、100 不算


def _evidence(kind, trust, payload, version, suffix):
    return Evidence(
        evidence_id=f"t1-3-{suffix}", task_id="t1", kind=kind, source="dsp", observed_at=NOW,
        campaign_version_observed=version, content_hash="a" * 64, trust_class=trust,
        payload=MappingProxyType(payload))


def _batch(name, spend):
    """三筆證據:編號、任務、讀取時間、版本在所有變體間相同,只有廣告文字的內容不同。
    「沒有名稱」也是三筆(名稱空值),不是少一筆。名稱直接交給分析端 DSP 用戶端的同一支函式轉成
    廣告文字內容,不在測試裡另抄一份截斷規則(用戶端改了規則,差異測試跟著測真的流程)。"""
    text = _campaign_text({} if name is None else {"name": name})
    return (
        _evidence(EvidenceKind.CAMPAIGN_STATE, TrustClass.TRUSTED,
                  {"id": "c1", "budget": 100, "status": "active", "version": 7}, 7, "state"),
        _evidence(EvidenceKind.METRICS, TrustClass.TRUSTED,
                  {"campaign_id": "c1", "window": "1h", "impressions": 500, "clicks": 12,
                   "conversions": 1, "spend": spend, "revenue": 5.0}, None, "metrics"),
        _evidence(EvidenceKind.CAMPAIGN_TEXT, TrustClass.UNTRUSTED_TEXT, text, 7, "text"),
    )


def _fingerprint(decision):
    if isinstance(decision, ProposalDecision):
        return ("proposal", content_hash(decision.proposal))
    return (type(decision).__name__,)


# ---- S210 ----
@pytest.mark.parametrize("pace", ["underpacing", "on_pace"])
def test_no_adversarial_campaign_name_changes_the_decision(pace):
    # Phase 14 增量 2b:正式規則是九條,要四查詢才判得到「值得加」;這裡帶齊一份平穩的四查詢
    # (跟決策規則測試同一份),名稱照樣不影響任何決策
    from tests.analyzer.test_policy import decide_full

    baseline = decide_full(TASK, _batch(None, SPEND[pace]), NOW)
    expected = ProposalDecision if pace == "underpacing" else NoAction
    assert isinstance(baseline, expected)  # 兩種配速各打到一條路:一次出提案、一次不需動作

    for category, name in SAMPLES:
        decision = decide_full(TASK, _batch(name, SPEND[pace]), NOW)
        assert decision == baseline, category
        assert _fingerprint(decision) == _fingerprint(baseline), category
    if isinstance(baseline, ProposalDecision):  # 仍可引用:提案列出三筆證據編號,含廣告文字
        assert baseline.proposal.evidence_refs == ("t1-3-state", "t1-3-metrics", "t1-3-text")


# ---- S211 ----
def test_the_adversarial_samples_cover_every_documented_category():
    assert set(CATEGORIES) == {
        "scenario", "english", "fake_system", "fake_proposal_json", "secret_request",
        "control_and_bidi", "oversized", "normal"}
    covered = {category for category, _text in SAMPLES}
    assert covered == set(CATEGORIES)  # 每一類至少一份,也沒有清單外的類別
    oversized = [text for category, text in SAMPLES if category == "oversized"]
    assert len(oversized) == 3 and all(len(text) == NAME_LIMIT for text in oversized)
    assert any(ord(ch) > 0xFFFF for ch in oversized[2])  # 需要代理對的那一份
    assert all(len(text) > MAX_UNTRUSTED_TEXT_LENGTH for text in oversized)


# ---- S212 ----
MARKER = "zz-risk-marker-5c1d"  # 不跟任何合法值重疊的專屬標記


def test_the_execution_side_never_reads_free_text(tmp_path, monkeypatch):
    from rtb.capabilitykit import decode
    from rtb.executor import dsp_client as executor_dsp
    from rtb.executor.capability_signer import CapabilitySigner
    from rtb.executor.execution import CampaignView
    from tests.capability_samples import TEST_KEY
    from tests.executor.fakes import proposal, write_config

    # 1. 執行行程重讀的廣告現況只有三個欄位
    assert [field.name for field in dataclasses.fields(CampaignView)] == [
        "budget", "status", "version"]

    # 2. 執行行程套件底下「全部」程式檔都沒有以屬性存取讀理由摘要(不縮小成只掃幾支檔)
    package = Path(__file__).resolve().parents[2] / "src" / "rtb" / "executor"
    files = sorted(package.rglob("*.py"))
    assert len(files) >= 7  # 守衛的守衛:真的掃到整個套件
    readers = [
        f"{path.name}:{node.lineno}" for path in files
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if (isinstance(node, ast.Attribute) and node.attr == "risk_summary")
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant) and node.args[1].value == "risk_summary")
    ]
    assert readers == []

    # 3. 送給 DSP 的寫入本文與能力憑證聲明都不含理由摘要
    item = proposal(risk_summary=MARKER)
    sent = {}

    def capture(url, method, body, timeout, headers=None):
        sent.update(url=url, method=method, body=body, timeout=timeout, headers=headers)
        return 200, {"version_after": 2}

    monkeypatch.setattr(executor_dsp, "request_json", capture)
    signer = CapabilitySigner(TEST_KEY)
    token = signer.sign(item, "k1", write_config(tmp_path / "tenants.json"),
                        int(NOW.timestamp()))
    executor_dsp.DspClient("http://127.0.0.1:1", 1.0).write(item, "k1", token,
                                                      on_call=lambda _call: None)

    assert sent["body"] is not None and MARKER not in repr(sent["body"])
    assert MARKER not in repr(decode(token, TEST_KEY))  # 聲明解開來看,不看編碼後的字串
