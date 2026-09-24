"""Phase 12 增量 1:展示金鑰、子行程環境白名單、故障跨行程交付([S1003] [S1004] [S1049] [S1059])。"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rtb.capabilitykit import (
    APPROVAL_KEY_ENV,
    AUDIT_KEY_ENV,
    KEY_ENV,
    MIN_KEY_BYTES,
    read_audit_key,
    read_key,
)
from rtb.demo import keys, launcher
from rtb.demo.launcher import Role
from rtb.dsp.store import CampaignStore

SRC = Path(__file__).resolve().parents[2] / "src"
USER_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/home/someone", "LANG": "zh_TW.UTF-8",
            "USER": "someone", "AWS_SECRET_ACCESS_KEY": "leak", "PYTHONPATH": "/elsewhere",
            "RTB_MODEL_LIVE": "1", "RTB_MODEL": "claude-sonnet-5", "RTB_MODEL_RECORD": "1",
            "ANTHROPIC_API_KEY": "leak", KEY_ENV: "user-own-key-should-not-pass-through-at-all"}
BASE = {"PATH", "HOME", "LANG", "USER", "PYTHONPATH"}
MODEL = {"RTB_MODEL_LIVE", "RTB_MODEL", "RTB_MODEL_RECORD"}


# ---- [S1004] [S1059] 金鑰 ----
def test_demo_keys_are_random_and_long_enough():
    first, second = keys.DemoKeys.generate(), keys.DemoKeys.generate()
    for text in (first.capability, first.audit, first.approval):
        assert isinstance(text, str) and text.isascii()
        assert len(text.encode("utf-8")) >= MIN_KEY_BYTES
    assert len({first.capability, first.audit, first.approval}) == 3
    assert first != second


def test_child_processes_read_back_the_same_key_the_server_signs_with():
    demo_keys = keys.DemoKeys.generate()
    env = launcher.child_env(Role.EXECUTOR, demo_keys, user_env=USER_ENV)
    assert read_key(env) == demo_keys.signing_bytes(KEY_ENV)
    assert read_key(env, APPROVAL_KEY_ENV) == demo_keys.signing_bytes(APPROVAL_KEY_ENV)
    dsp_env = launcher.child_env(Role.DSP, demo_keys, user_env=USER_ENV)
    assert read_audit_key(dsp_env) == demo_keys.signing_bytes(AUDIT_KEY_ENV)


def test_the_key_text_is_not_in_its_repr():
    demo_keys = keys.DemoKeys.generate()
    assert demo_keys.capability not in repr(demo_keys)


# ---- [S1003] 環境白名單 ----
@pytest.mark.parametrize(("role", "extra"), [
    (Role.DSP, {KEY_ENV, AUDIT_KEY_ENV}),
    (Role.EXECUTOR, {KEY_ENV, APPROVAL_KEY_ENV}),
    (Role.INBOX, set()),
    (Role.ANALYZER, set()),
    (Role.MODEL_ENTRY, MODEL),
])
def test_demo_processes_get_only_whitelisted_environment(role, extra):
    demo_keys = keys.DemoKeys.generate()
    env = launcher.child_env(role, demo_keys, user_env=USER_ENV)
    assert set(env) == BASE | extra
    assert env["PYTHONPATH"] == str(SRC)
    assert "leak" not in env.values()
    assert env.get(KEY_ENV) in (None, demo_keys.capability)
    for name in MODEL & extra:
        assert env[name] == USER_ENV[name]


def test_only_the_faulted_process_gets_the_fault_nonce():
    demo_keys = keys.DemoKeys.generate()
    plain = launcher.child_env(Role.DSP, demo_keys, user_env=USER_ENV)
    faulted = launcher.child_env(Role.DSP, demo_keys, user_env=USER_ENV, fault_nonce="n" * 43)
    assert launcher.FAULT_NONCE_ENV not in plain
    assert faulted[launcher.FAULT_NONCE_ENV] == "n" * 43
    assert set(faulted) - set(plain) == {launcher.FAULT_NONCE_ENV}


def test_missing_user_basics_are_left_out_not_invented():
    env = launcher.child_env(Role.INBOX, keys.DemoKeys.generate(), user_env={"PATH": "/bin"})
    assert set(env) == {"PATH", "PYTHONPATH"}


# ---- [S1049] 故障跨行程交付 ----
def _root(tmp_path, demo_id="demo-1"):
    return launcher.prepare_root(tmp_path / "demos", demo_id)


def _plan(_root_dir=None, **overrides):
    fields = {"role": Role.DSP, "dsp_plan": (("timeout_before_commit", 0.0),),
              "clock_offset_seconds": 0.0, "crash_point": None} | overrides
    return launcher.FaultRequest(**fields)


def _run_child(_root_dir, config, nonce, *args, role="dsp", timeout=20):
    env = launcher.child_env(Role.DSP if role == "dsp" else Role.EXECUTOR,
                             keys.DemoKeys.generate(), user_env={"PATH": os.environ["PATH"]},
                             fault_nonce=nonce)
    return subprocess.run([sys.executable, "-m", "rtb.demo.launcher.child", role, str(config),
                           "--", *args], env=env, capture_output=True, text=True,
                          timeout=timeout, check=False)


def test_the_launcher_writes_the_plan_inside_the_root(tmp_path):
    root = _root(tmp_path)
    config, nonce = launcher.write_fault_plan(root, _plan(root))
    assert config.resolve().is_relative_to(root.resolve())
    assert nonce not in config.read_text(encoding="utf-8")
    assert len(nonce.encode()) >= 32


@pytest.mark.parametrize("how", ["no nonce", "wrong nonce", "outside root", "no marker",
                                 "other demo marker", "db outside root"])
def test_fault_tools_refuse_anything_but_the_launcher_capability(tmp_path, how):
    """[S1049] 沒有啟動器交付的設定檔與相符的一次性隨機值、設定檔在根目錄外、根目錄不是這次展示
    建的、目標資料庫在根目錄外,子行程都拒絕啟動故障(不會退回成沒有故障的正常啟動)。"""
    root = _root(tmp_path)
    config, nonce = launcher.write_fault_plan(root, _plan(root))
    db = root / "dsp.db"
    if how == "no nonce":
        nonce = None
    elif how == "wrong nonce":
        nonce = "x" * 43
    elif how == "outside root":
        moved = tmp_path / "elsewhere.json"
        moved.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
        config = moved
    elif how == "no marker":
        (root / launcher.ROOT_MARKER).unlink()
    elif how == "other demo marker":
        (root / launcher.ROOT_MARKER).write_text("demo-2", encoding="utf-8")
    elif how == "db outside root":
        db = tmp_path / "dsp.db"

    child = _run_child(root, config, nonce, "--db", str(db))

    assert child.returncode == launcher.EXIT_FAULT_REFUSED, (child.stdout, child.stderr)
    assert "PORT=" not in child.stdout


@pytest.mark.parametrize("how", ["wrong role", "unknown crash point"])
def test_a_plan_for_another_role_or_an_unknown_crash_point_is_refused(tmp_path, how):
    root = _root(tmp_path)
    if how == "wrong role":
        config, nonce = launcher.write_fault_plan(root, _plan())
    else:
        config, nonce = launcher.write_fault_plan(
            root, _plan(role=Role.EXECUTOR, dsp_plan=(), crash_point="nowhere"))

    child = _run_child(root, config, nonce, "--db", str(root / "executor.db"), role="executor")

    assert child.returncode == launcher.EXIT_FAULT_REFUSED, child.stderr
    assert "READY" not in child.stdout


def test_a_self_written_plan_without_the_launcher_is_refused(tmp_path):
    """直接執行或自己造一份設定檔:沒有啟動器寫的標記與隨機值雜湊,一樣拒。"""
    root = tmp_path / "fake-root"
    root.mkdir()
    config = root / "faults.json"
    config.write_text(json.dumps({"role": "dsp", "dsp_plan": [], "clock_offset_seconds": 0}),
                      encoding="utf-8")

    child = _run_child(root, config, "x" * 43, "--db", str(root / "dsp.db"))

    assert child.returncode == launcher.EXIT_FAULT_REFUSED


def test_a_delivered_dsp_plan_is_applied_to_the_first_write(tmp_path):
    """交付正確時,DSP 子行程照排定的故障回應第一個寫入(提交前逾時:不提交)。"""
    from rtb.demo.launcher import start

    root = _root(tmp_path)
    CampaignStore(root / "dsp.db").seed_campaign("c1", budget=100)
    demo_keys = keys.DemoKeys.generate()
    process = start(Role.DSP, ["--db", str(root / "dsp.db"), "--hang-seconds", "0.3"],
                    demo_keys, root=root, faults=_plan(root), user_env=os.environ)
    try:
        from rtb.executor.capability_signer import CapabilitySigner
        from rtb.executor.dsp_client import DspClient
        from tests.executor.fakes import proposal, write_config

        config = write_config(root / "tenants.json")
        prop = proposal(campaign_id="c1", campaign_version_observed=1)
        token = CapabilitySigner(demo_keys.signing_bytes(KEY_ENV)).sign(
            prop, "k1", config, int(time.time()))
        answer = DspClient(process.url, 0.2).write(prop, "k1", token, on_call=lambda _call: None)
        assert answer.status is None  # 排定的提交前逾時:用戶端沒拿到回應
        time.sleep(0.5)
        assert CampaignStore(root / "dsp.db").get_campaign("c1").version == 1
    finally:
        process.stop()


def test_a_plain_start_has_no_fault_door(tmp_path):
    """沒排故障的子行程直接跑正式入口,不經故障啟動器,也拿不到隨機值。"""
    root = _root(tmp_path)
    command, env = launcher.command_for(Role.DSP, ["--db", str(root / "dsp.db")],
                                        keys.DemoKeys.generate(), root=root, faults=None,
                                        user_env=USER_ENV)
    assert command[:3] == [sys.executable, "-m", "rtb.dsp.server"]
    assert launcher.FAULT_NONCE_ENV not in env


def test_stop_ends_the_whole_process_group(tmp_path):
    root = _root(tmp_path)
    process = launcher.start(Role.INBOX, ["--db", str(root / "inbox.db")],
                             keys.DemoKeys.generate(), root=root, faults=None,
                             user_env=os.environ)
    pid = process.pid
    process.stop()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("子行程沒結束")
