"""執行行程的寫入能力憑證簽發:S29、S31、S33。"""

import json
import os
import time

import pytest

from rtb.capabilitykit import KEY_ENV, MIN_KEY_BYTES, decode
from rtb.domain.attempt import operation_key
from rtb.domain.proposal import parse_proposal
from rtb.dsp.store import CampaignStore
from rtb.executor.capability_signer import CapabilitySigner, SigningRefused
from tests.capability_samples import DEFAULT_TENANT, TEST_KEY
from tests.domain.proposal_samples import valid

NOW = 1_800_000_000


def proposal(**overrides):
    parsed = parse_proposal(valid(**overrides))
    assert parsed.proposal is not None, parsed.errors
    return parsed.proposal


@pytest.fixture
def config(tmp_path):
    folder = tmp_path / "conf"
    folder.mkdir(mode=0o700)
    path = folder / "tenants.json"

    def write(tenants=None, mode=0o600):
        body = {"tenants": tenants if tenants is not None else {
            DEFAULT_TENANT: {"campaigns": ["c1"], "max_budget": 1000}}}
        path.write_text(json.dumps(body), encoding="utf-8")
        os.chmod(path, mode)
        return path

    write()
    return write


def sign(path, prop=None, now=NOW):
    prop = prop or proposal()
    return CapabilitySigner(TEST_KEY).sign(prop, operation_key(prop), path, now)


# ---- 簽出來的內容 ----
def test_the_signed_claims_bind_the_exact_change_the_stored_version_and_the_key(config):
    path = config()
    prop = proposal(requested_change={"new_budget": 150}, campaign_version_observed=3)

    claims = decode(sign(path, prop), TEST_KEY)

    assert claims["tenant"] == DEFAULT_TENANT and claims["campaign_id"] == "c1"
    assert claims["action"] == "update_budget" and claims["new_budget"] == 150
    assert claims["expected_version"] == 3  # 取提案快照裡觀察到的版本
    assert claims["idempotency_key"] == operation_key(prop)
    assert claims["iat"] == NOW and NOW < claims["exp"] <= NOW + 300


def test_a_pause_capability_carries_no_amount(config):
    prop = proposal(action_type="pause_campaign", requested_change={})
    assert decode(sign(config(), prop), TEST_KEY)["new_budget"] is None


# ---- [S29] ----
def test_the_executor_refuses_to_sign_outside_its_current_tenant_configuration(config, tmp_path):
    path = config()
    with pytest.raises(SigningRefused):
        sign(path, proposal(campaign_id="c9"))  # 不屬於任何允許的租戶
    with pytest.raises(SigningRefused):
        sign(path, proposal(requested_change={"new_budget": 1001}))  # 超過上限
    assert sign(path, proposal(requested_change={"new_budget": 1000}))  # 等於上限可以

    config({DEFAULT_TENANT: {"campaigns": ["c1"], "max_budget": 100}})  # 調降:下一次立刻生效
    with pytest.raises(SigningRefused):
        sign(path, proposal(requested_change={"new_budget": 150}))

    for broken in ("not json", json.dumps({"tenants": {"t": {"campaigns": "c1"}}}),
                   json.dumps({"tenants": {"a": {"campaigns": ["c1"], "max_budget": 1000},
                                           "b": {"campaigns": ["c1"], "max_budget": 1000}}})):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(SigningRefused):
            sign(path)
    with pytest.raises(SigningRefused):
        sign(tmp_path / "conf" / "missing.json")


def test_a_config_that_is_a_named_pipe_is_refused_without_hanging(config):
    import threading

    path = config()
    path.unlink()
    os.mkfifo(path, 0o600)
    outcome = []

    def attempt():
        try:
            sign(path)
            outcome.append("signed")
        except SigningRefused:
            outcome.append("refused")

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=3)
    if worker.is_alive():  # 卡住了:打開寫端讓它醒來,再判失敗
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
        pytest.fail("讀具名管道的設定檔卡住,沒有拒絕簽發")
    assert outcome == ["refused"]


def test_a_config_with_a_huge_integer_is_refused_not_crashed(config):
    path = config()
    path.write_text('{"tenants": {"t": {"campaigns": ["c1"], "max_budget": ' + "9" * 5000 + "}}}",
                    encoding="utf-8")
    with pytest.raises(SigningRefused):
        sign(path)


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_a_config_file_writable_by_others_is_refused(config, mode):
    with pytest.raises(SigningRefused):
        sign(config(mode=mode))


def test_a_config_in_a_directory_writable_by_others_is_refused(config):
    path = config()
    os.chmod(path.parent, 0o777)  # noqa: S103 - 測試刻意造出不安全的目錄
    try:
        with pytest.raises(SigningRefused):
            sign(path)
    finally:
        os.chmod(path.parent, 0o700)


def test_a_symlinked_config_is_refused(config):
    path = config()
    link = path.parent / "link.json"
    link.symlink_to(path)
    with pytest.raises(SigningRefused):
        sign(link)


def test_a_config_owned_by_someone_else_is_refused(config, monkeypatch):
    path = config()
    monkeypatch.setattr(os, "getuid", lambda: os.stat(path).st_uid + 1)
    with pytest.raises(SigningRefused):
        sign(path)


# ---- [S31] ----
@pytest.mark.parametrize("key", [None, b"", b"s" * (MIN_KEY_BYTES - 1)],
                         ids=["none", "empty", "short"])
def test_a_missing_or_short_key_disables_both_signing_and_verifying(key, tmp_path):
    with pytest.raises(ValueError):
        CapabilitySigner(key)

    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "rtb"
    readers = sorted(str(f.relative_to(src)) for f in src.rglob("*.py")
                     if KEY_ENV in f.read_text(encoding="utf-8"))
    assert readers == ["capabilitykit.py"]  # 變數名稱只出現在共用模組一處

    # DSP 的啟動程式(真的子行程、真的讀環境變數):讀不到可用金鑰就啟動成「拒收所有寫入」
    from tests.capability_samples import header
    from tests.dsp.conftest import DspProcess

    db = tmp_path / "dsp.db"
    store = CampaignStore(db)
    store.seed_campaign("c1", budget=100)
    store.close()
    dsp = DspProcess(db, capability_key=key)
    try:
        status, body = dsp.request("POST", "/campaigns/c1/budget",
                                   {"new_budget": 150, "expected_version": 1},
                                   {"Idempotency-Key": "k1", **header()})
        assert (status, body["error"]) == (503, "capability_not_configured")
    finally:
        dsp.stop()


# ---- [S33] ----
def test_every_capability_the_executor_signs_is_accepted_by_a_separately_launched_dsp(
    config, tmp_path, monkeypatch,
):
    """兩邊各自從同一個環境變數讀金鑰:執行行程這邊經共用讀取函式,DSP 是真的獨立子行程。"""
    from rtb.capabilitykit import read_key
    from tests.dsp.conftest import DspProcess

    monkeypatch.setenv(KEY_ENV, TEST_KEY.decode())
    db = tmp_path / "dsp.db"
    store = CampaignStore(db)
    store.seed_campaign("c1", budget=100)
    store.close()
    dsp = DspProcess(db, capability_key=TEST_KEY)
    try:
        signer = CapabilitySigner(read_key(os.environ))
        path = config()
        cases = [
            (proposal(requested_change={"new_budget": 150}, campaign_version_observed=1),
             "/campaigns/c1/budget", {"new_budget": 150, "expected_version": 1}),
            (proposal(action_type="pause_campaign", requested_change={},
                      campaign_version_observed=2),
             "/campaigns/c1/pause", {"expected_version": 2}),
        ]
        for prop, route, body in cases:
            key = operation_key(prop)
            cap = signer.sign(prop, key, path, int(time.time()))
            status, reply = dsp.request("POST", route, body,
                                        {"Idempotency-Key": key, "X-Capability": cap})
            assert status == 200, reply
    finally:
        dsp.stop()


def test_signing_needs_no_network_and_no_dsp(config):
    """簽發是純本地計算:不碰網路,DSP 不在也簽得出來。"""
    assert sign(config()).count(".") == 1

