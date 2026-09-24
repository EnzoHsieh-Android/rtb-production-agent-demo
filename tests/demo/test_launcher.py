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
    built = launcher.child_env(Role.EXECUTOR, demo_keys, user_env=USER_ENV)
    assert read_key(built) == demo_keys.signing_bytes(KEY_ENV)
    assert read_key(built, APPROVAL_KEY_ENV) == demo_keys.signing_bytes(APPROVAL_KEY_ENV)
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
    built = launcher.child_env(role, demo_keys, user_env=USER_ENV)
    assert set(built) == BASE | extra
    assert built["PYTHONPATH"] == str(SRC)
    assert "leak" not in built.values()
    assert built.get(KEY_ENV) in (None, demo_keys.capability)
    for name in MODEL & extra:
        assert built[name] == USER_ENV[name]


def test_only_the_faulted_process_gets_the_fault_nonce():
    demo_keys = keys.DemoKeys.generate()
    plain = launcher.child_env(Role.DSP, demo_keys, user_env=USER_ENV)
    faulted = launcher.child_env(Role.DSP, demo_keys, user_env=USER_ENV, fault_nonce="n" * 43)
    assert launcher.FAULT_NONCE_ENV not in plain
    assert faulted[launcher.FAULT_NONCE_ENV] == "n" * 43
    assert set(faulted) - set(plain) == {launcher.FAULT_NONCE_ENV}


def test_missing_user_basics_are_left_out_not_invented():
    built = launcher.child_env(Role.INBOX, keys.DemoKeys.generate(), user_env={"PATH": "/bin"})
    assert set(built) == {"PATH", "PYTHONPATH"}


# ---- [S1049] 故障跨行程交付 ----
def _root(tmp_path, demo_id="demo-1"):
    return launcher.prepare_root(tmp_path / "demos", demo_id)


def _plan(_root_dir=None, **overrides):
    fields = {"role": Role.DSP, "dsp_plan": (("timeout_before_commit", 0.0),),
              "clock_offset_seconds": 0.0, "crash_point": None} | overrides
    return launcher.FaultRequest(**fields)


def _run_child(_root_dir, config, nonce, *args, role="dsp", timeout=20):
    env = launcher.child_env(Role.DSP if role == "dsp" else Role.EXECUTOR,
                             keys.DemoKeys.generate(), user_env=os.environ, fault_nonce=nonce)
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


@pytest.mark.parametrize(("role", "args"), [
    ("dsp", ["--db=OUT/dsp.db"]),  # 等號寫法
    ("dsp", ["--db", "ROOT/dsp.db", "--db=OUT/dsp.db"]),  # 重複給,後一個蓋掉前一個
    ("dsp", ["--db", "ROOT/a.db", "--db", "ROOT/b.db"]),  # 重複給:兩個都在根目錄內也拒
    ("executor", ["--db", "ROOT/e.db", "--dsp-url", "http://127.0.0.1:9",
                  "--tenant-config", "OUT/tenants.json"]),  # 租戶設定在根目錄外
    ("executor", ["--db", "ROOT/e.db", "--dsp-url", "http://127.0.0.1:9",
                  "--tenant", "ROOT/tenants.json"]),  # 縮寫:正式入口不收縮寫
    ("executor", ["--db=OUT/e.db", "--dsp-url", "http://127.0.0.1:9",
                  "--tenant-config=OUT/tenants.json"]),
])
def test_target_paths_are_checked_the_way_the_real_entry_parses_them(tmp_path, role, args):
    """[代碼審 r1 s1/l2/x2] 目標路徑照正式入口同一支 parser 解析後的值核對:等號寫法、縮寫、
    重複給都擋,子行程不會在根目錄外建出資料庫。"""
    root = _root(tmp_path)
    out = tmp_path / "outside"
    out.mkdir()
    plan = _plan() if role == "dsp" else _plan(role=Role.EXECUTOR, dsp_plan=())
    config, nonce = launcher.write_fault_plan(root, plan)
    argv = [a.replace("ROOT", str(root)).replace("OUT", str(out)) for a in args]

    child = _run_child(root, config, nonce, *argv, role=role)

    assert child.returncode == launcher.EXIT_FAULT_REFUSED, (child.stdout, child.stderr)
    assert list(out.iterdir()) == []


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
    command, built = launcher.command_for(Role.DSP, ["--db", str(root / "dsp.db")],
                                        keys.DemoKeys.generate(), root=root, faults=None,
                                        user_env=USER_ENV)
    assert command[:4] == [sys.executable, "-P", "-m", "rtb.dsp.server"]
    assert launcher.FAULT_NONCE_ENV not in built


def test_stop_ends_a_real_started_process(tmp_path):
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


# ---- 代碼審 r1 l1/x1/t4/l5:停止收乾淨整個行程群組、就緒等待有總期限 ----
GRANDCHILD = """
import os, signal, subprocess, sys, time
ignore = sys.argv[1] == "ignore"
hold = sys.argv[2] == "hold"
leader_dies = sys.argv[3] == "die"
code = "import signal, time\\n"
if ignore:
    code += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n"
code += "time.sleep(60)\\n"
grand = subprocess.Popen([sys.executable, "-c", code],
                         stdout=None if hold else subprocess.DEVNULL)
if ignore:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print(f"READY {grand.pid}", flush=True)
if leader_dies:
    os._exit(9)
time.sleep(60)
"""


def _spawn_script(tmp_path, *args, prefix="READY", startup=5.0, script=GRANDCHILD):
    return launcher._spawn([sys.executable, "-c", script, *args], {"PATH": os.environ["PATH"]},
                           tmp_path, prefix, tmp_path / "spawn.log", startup_seconds=startup)


def _gone(pid, within=3.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize(("ignore", "hold", "leader"), [
    ("plain", "free", "live"),  # 一般孫行程
    ("ignore", "free", "live"),  # 忽略 SIGTERM:要升級成 SIGKILL
    ("plain", "free", "die"),  # 領頭先死(猝死點):孫行程一樣要收
    ("ignore", "hold", "live"),  # 孫行程握著 stdout 又不理 SIGTERM:stop 不能卡住
])
def test_stop_ends_the_whole_process_group(tmp_path, ignore, hold, leader):
    process = _spawn_script(tmp_path, ignore, hold, leader)
    grandchild = int(process.first_line.split()[1])

    began = time.monotonic()
    process.stop(grace_seconds=1.0)

    assert time.monotonic() - began < 5
    assert _gone(grandchild) and _gone(process.pid)


def test_a_process_that_never_gets_ready_is_cleaned_up(tmp_path):
    pid_file = tmp_path / "pid"
    script = f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\n" \
             "time.sleep(60)\n"
    with pytest.raises(launcher.StartFailed):
        _spawn_script(tmp_path, startup=1.0, script=script)
    assert _gone(int(pid_file.read_text()))


def test_readiness_has_one_overall_deadline(tmp_path):
    script = ("import time\nfor _ in range(20):\n"
              "    print('noise', flush=True)\n    time.sleep(0.3)\n")
    began = time.monotonic()
    with pytest.raises(launcher.StartFailed):
        _spawn_script(tmp_path, prefix="noise-never", startup=1.0, script=script)
    assert time.monotonic() - began < 3


def test_a_wrong_first_line_fails_at_once(tmp_path):
    began = time.monotonic()
    with pytest.raises(launcher.StartFailed, match="第一行"):
        _spawn_script(tmp_path, startup=5.0,
                      script="import time\nprint('Traceback', flush=True)\ntime.sleep(60)\n")
    assert time.monotonic() - began < 3


def test_an_early_exit_reports_its_exit_code(tmp_path):
    script = "import os, time\nos.close(1)\ntime.sleep(0.01)\nraise SystemExit(3)\n"
    with pytest.raises(launcher.StartFailed, match="3"):
        _spawn_script(tmp_path, startup=5.0, script=script)


# ---- 代碼審 r1 s3/s4/s5/l3/l4/t7:故障交付的縱深 ----
def _tamper(config, **changes):
    body = json.loads(config.read_text(encoding="utf-8"))
    body.update(changes)
    config.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("changes", [
    {"nonce_sha256": "é" * 64},  # 非 ASCII:compare_digest 對字串會丟 TypeError
    {"clock_offset_seconds": "abc"},
    {"dsp_plan": [["x"]]},
    {"dsp_plan": 5},
    {"crash_point": ["a"]},
    {"dsp_plan": [["timeout_befor_commit", 0]]},  # 打錯字的故障模式:不能靜默變成沒有故障
    {"crash_point": "before_dsp_call"},  # DSP 角色不准帶猝死點
])
def test_a_malformed_or_wrong_plan_is_refused_with_code_3(tmp_path, changes):
    root = _root(tmp_path)
    config, nonce = launcher.write_fault_plan(root, _plan())
    _tamper(config, **changes)

    child = _run_child(root, config, nonce, "--db", str(root / "dsp.db"))

    assert child.returncode == launcher.EXIT_FAULT_REFUSED, child.stderr


def test_an_executor_plan_may_not_carry_a_dsp_schedule(tmp_path):
    root = _root(tmp_path)
    config, nonce = launcher.write_fault_plan(root, _plan(role=Role.EXECUTOR))

    child = _run_child(root, config, nonce, "--db", str(root / "e.db"), "--dsp-url",
                       "http://127.0.0.1:9", "--tenant-config", str(root / "t.json"),
                       role="executor")

    assert child.returncode == launcher.EXIT_FAULT_REFUSED, child.stderr


def test_the_same_delivery_cannot_be_used_twice(tmp_path):
    """[s4] 核對成功後設定檔作廢、隨機值從子行程環境拿掉:同一份交付第二次啟動被拒。"""
    root = _root(tmp_path)
    demo_keys = keys.DemoKeys.generate()
    request = _plan()
    command, env = launcher.command_for(Role.DSP, ["--db", str(root / "dsp.db")], demo_keys,
                                        root=root, faults=request, user_env=os.environ)
    first = launcher._spawn(command, env, root, "PORT=", root / "a.log")
    try:
        second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20,
                                check=False)
    finally:
        first.stop()
    assert second.returncode == launcher.EXIT_FAULT_REFUSED


def test_the_nonce_is_gone_from_the_child_environment_after_the_check(tmp_path):
    root = _root(tmp_path)
    config, nonce = launcher.write_fault_plan(root, _plan(role=Role.EXECUTOR, dsp_plan=(),
                                                          crash_point="after_receiving"))
    probe = ("import os, runpy, sys\n"
             "from rtb.demo.launcher import child\n"
             "sys.argv = ['child', *sys.argv[1:]]\n"
             "real = child.executor_faults.run\n"
             "def spy(args, plan):\n"
             "    print('NONCE' if child.FAULT_NONCE_ENV in os.environ else 'CLEAN', flush=True)\n"
             "    return 0\n"
             "child.executor_faults.run = spy\n"
             "raise SystemExit(child.main())\n")
    env = launcher.child_env(Role.EXECUTOR, keys.DemoKeys.generate(), user_env=os.environ,
                             fault_nonce=nonce)
    out = subprocess.run([sys.executable, "-c", probe, "executor", str(config), "--", "--db",
                          str(root / "e.db"), "--dsp-url", "http://127.0.0.1:9",
                          "--tenant-config", str(root / "t.json")],
                         env=env, capture_output=True, text=True, timeout=20, check=False)
    assert out.stdout.strip() == "CLEAN", out.stderr


@pytest.mark.parametrize("role", ["inbox", "analyzer", "model_entry"])
def test_roles_without_fault_tools_are_refused(tmp_path, role):
    """設定檔就是給這個角色的(啟動器照寫):故障啟動器照樣拒,因為這個角色沒有故障手段。"""
    root = _root(tmp_path)
    config, nonce = launcher.write_fault_plan(root, _plan(role=Role(role), dsp_plan=()))

    # 參數照執行迴圈寫得完整合法:只有「這個角色沒有故障手段」這一條擋得下
    child = _run_child(root, config, nonce, "--db", str(root / "x.db"), "--dsp-url",
                       "http://127.0.0.1:9", "--tenant-config", str(root / "t.json"), role=role)

    assert child.returncode == launcher.EXIT_FAULT_REFUSED


def test_command_for_refuses_faults_for_another_role(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(ValueError, match="故障"):
        launcher.command_for(Role.EXECUTOR, [], keys.DemoKeys.generate(), root=root,
                             faults=_plan(), user_env={})


def test_prepare_root_refuses_to_reuse_or_share(tmp_path):
    root = _root(tmp_path, "demo-9")
    assert (root.stat().st_mode & 0o777) == 0o700
    with pytest.raises(FileExistsError):
        launcher.prepare_root(tmp_path / "demos", "demo-9")
    for bad in ("../escape", "展示-一", "a/b", ""):
        with pytest.raises(ValueError):
            launcher.prepare_root(tmp_path / "demos", bad)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    with pytest.raises(ValueError, match="別人"):
        launcher.prepare_root(shared, "demo-1")


def test_the_fault_plan_file_is_private(tmp_path):
    root = _root(tmp_path)
    config, _ = launcher.write_fault_plan(root, _plan())
    assert (config.stat().st_mode & 0o777) == 0o600


def test_child_processes_do_not_import_from_their_working_directory(tmp_path):
    """[s3] 子行程的工作目錄是展示根目錄;python -m 不能把它放進 sys.path,根目錄裡的同名檔蓋不掉
    標準函式庫。"""
    root = _root(tmp_path)
    (root / "argparse.py").write_text("raise SystemExit('SHADOWED')\n", encoding="utf-8")
    command, env = launcher.command_for(Role.INBOX, ["--db", str(root / "inbox.db")],
                                        keys.DemoKeys.generate(), root=root, faults=None,
                                        user_env=os.environ)
    assert "-P" in command
    process = launcher._spawn(command, env, root, "PORT=", root / "i.log")
    process.stop()


@pytest.mark.parametrize(("role", "extra"), [
    (Role.DSP, {KEY_ENV, AUDIT_KEY_ENV}),
    (Role.EXECUTOR, {KEY_ENV, APPROVAL_KEY_ENV}),
    (Role.ANALYZER, set()),
])
def test_started_processes_really_get_only_the_whitelist(tmp_path, monkeypatch, role, extra):
    """[S1003][代碼審 r1 t1] 端到端:經 start() 起一個印出自己環境鍵的子行程;使用者環境用真的
    os.environ 加上注入的雜項(秘密、SSH_AUTH_SOCK、TMPDIR),子行程只拿到白名單。"""
    for name, value in {"AWS_SECRET_ACCESS_KEY": "leak", "SSH_AUTH_SOCK": "agent.sock",
                        "TMPDIR": "elsewhere", "ANTHROPIC_API_KEY": "leak"}.items():
        monkeypatch.setenv(name, value)
    probe = ("import json, os\n"
             f"print({launcher._READY_PREFIX[role]!r} + json.dumps(sorted(os.environ)),"
             " flush=True)\n"
             "import time\ntime.sleep(30)\n")
    real = launcher.command_for

    def probing(*args, **kwargs):
        _, built = real(*args, **kwargs)
        return [sys.executable, "-c", probe], built

    monkeypatch.setattr(launcher, "command_for", probing)
    root = _root(tmp_path)
    process = launcher.start(role, [], keys.DemoKeys.generate(), root=root, faults=None,
                             user_env=os.environ)
    try:
        seen = set(json.loads(process.first_line[len(launcher._READY_PREFIX[role]):]))
    finally:
        process.stop()
    allowed = {"PATH", "HOME", "LANG", "USER", "PYTHONPATH"} | extra
    # 直譯器自己可能補的變數(macOS 的 __CF_USER_TEXT_ENCODING、LC_CTYPE)不是從使用者環境來的
    assert seen - allowed <= {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}, seen - allowed
    assert extra <= seen


# ---- 代碼審 r1 t2:金鑰測試逐把驗 ----
def test_each_environment_name_carries_its_own_key():
    demo_keys = keys.DemoKeys.generate()
    dsp = launcher.child_env(Role.DSP, demo_keys, user_env={})
    executor = launcher.child_env(Role.EXECUTOR, demo_keys, user_env={})
    assert read_key(dsp) == demo_keys.capability.encode("utf-8")
    assert read_audit_key(dsp) == demo_keys.audit.encode("utf-8")
    assert read_key(executor) == demo_keys.capability.encode("utf-8")
    assert read_key(executor, APPROVAL_KEY_ENV) == demo_keys.approval.encode("utf-8")
    assert demo_keys.approval not in dsp.values()  # DSP 拿不到核可金鑰
    assert demo_keys.audit not in executor.values()  # 執行迴圈拿不到稽核金鑰
    for name in (KEY_ENV, AUDIT_KEY_ENV, APPROVAL_KEY_ENV):
        assert demo_keys.signing_bytes(name) == demo_keys.text(name).encode("utf-8")


def test_every_key_changes_between_demos_and_none_is_printed():
    first, second = keys.DemoKeys.generate(), keys.DemoKeys.generate()
    for name in (KEY_ENV, AUDIT_KEY_ENV, APPROVAL_KEY_ENV):
        assert first.text(name) != second.text(name)
    assert len({first.text(n) for n in (KEY_ENV, AUDIT_KEY_ENV, APPROVAL_KEY_ENV)}) == 3
    for text in (first.capability, first.audit, first.approval):
        assert text not in repr(first) and text not in str(first)


# ---- 代碼審 r2 o2/v1/v2/s3:領頭單獨先死、一次性交付、上層目錄是符號連結 ----
LONE_LEADER = "import os\nprint('READY', flush=True)\nos._exit(9)\n"


@pytest.mark.parametrize("wait", [0.0, 0.5])
def test_stop_after_the_leader_died_alone_does_not_raise(tmp_path, wait):
    """[o2/v1] 領頭自己結束、沒被收屍、群組裡沒有別人:macOS 對這種群組送訊號回 EPERM,
    stop 不能因此丟例外(驅動程式收尾會中斷、其他行程變孤兒)。"""
    process = _spawn_script(tmp_path, script=LONE_LEADER)
    time.sleep(wait)

    process.stop(grace_seconds=1.0)

    assert process.poll() == 9


def test_a_wrong_first_line_then_an_exit_is_a_start_failure(tmp_path):
    """[o2/v1] 印一行不是就緒的訊息後立刻結束:要判成起不來(StartFailed),不是權限錯誤。"""
    for _ in range(5):
        with pytest.raises(launcher.StartFailed):
            _spawn_script(tmp_path, startup=5.0,
                          script="import os\nprint('Traceback', flush=True)\nos._exit(1)\n")


@pytest.mark.parametrize("copy_as", ["copy.json", "faults.json.USED"])
def test_a_config_already_marked_used_is_refused(tmp_path, copy_as):
    """[v2、第 3 輪代碼審 p2] 同一份交付只能用一次:用之前先複製一份(或取成大寫字尾的名字,APFS 不分
    大小寫),配同一個隨機值再用照樣被拒;用過的設定檔不留在根目錄。"""
    import shutil

    root = _root(tmp_path)
    request = _plan()
    command, env = launcher.command_for(Role.DSP, ["--db", str(root / "dsp.db")],
                                        keys.DemoKeys.generate(), root=root, faults=request,
                                        user_env=os.environ)
    config_at = command.index("rtb.demo.launcher.child") + 2
    copy = root / copy_as
    shutil.copy(command[config_at], copy)
    first = launcher._spawn(command, env, root, "PORT=", root / "a.log")
    try:
        assert not Path(command[config_at]).exists()
        again = [*command[:config_at], str(copy), *command[config_at + 1:]]
        second = subprocess.run(again, env=env, capture_output=True, text=True, timeout=20,
                                check=False)
    finally:
        first.stop()
    assert second.returncode == launcher.EXIT_FAULT_REFUSED, second.stdout


def test_prepare_root_refuses_a_symlinked_base(tmp_path):
    """[s3] 上層目錄是符號連結:核對的會是連結指向的目錄,連結的擁有者之後可以改指向。"""
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="符號連結"):
        launcher.prepare_root(link, "demo-1")


@pytest.mark.parametrize(("args", "at_least"), [
    ([], 2 * 3.0 + 1),  # 分析端預設逾時 3 秒
    (["--timeout-seconds", "10"], 2 * 10.0 + 1),
])
def test_the_analyzer_gets_enough_time_to_finish_its_step(args, at_least):
    """[n3] 分析端收到 SIGTERM 會做完這一步(最多兩次呼叫)才停:啟動器給的停止時限至少
    兩倍逾時加一秒,不在一步中途硬殺。"""
    assert launcher.stop_grace_seconds(Role.ANALYZER, args) >= at_least
    assert launcher.stop_grace_seconds(Role.DSP, args) == launcher.STOP_SECONDS
