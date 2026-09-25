"""Phase 13 增量 4 展示串接:啟動器的即時粒度、就緒之後的輸出、一次跑完的模型入口
(計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈展示情境怎麼接 AI〉;[S1145] 的啟動器那一半)。"""

import os
import sys

from rtb.demo import keys, launcher
from rtb.demo.launcher import Role
from tests.demo.test_launcher import BASE, MODEL, USER_ENV


def test_the_analyzer_gets_model_env_only_when_live_and_ai_judged():
    """[S1145] 分析端只在「這個情境列在即時清單」而且「這次帶 --ai-judge」時多三個模型變數;
    模型入口照舊有;其他角色一律沒有。"""
    demo_keys = keys.DemoKeys.generate()

    def env(role, args, *, live):
        return launcher.command_for(role, args, demo_keys, root=None, faults=None,
                                    user_env=USER_ENV, live_model=live)[1]

    assert set(env(Role.ANALYZER, ["--ai-judge"], live=True)) == BASE | MODEL
    assert set(env(Role.ANALYZER, ["--ai-judge"], live=False)) == BASE
    assert set(env(Role.ANALYZER, ["--db", "x"], live=True)) == BASE  # 沒開 AI 決策
    assert set(env(Role.INBOX, ["--ai-judge"], live=True)) == BASE  # 別的角色不因即時多拿
    assert set(launcher.child_env(Role.ANALYZER, demo_keys, user_env=USER_ENV)) == BASE
    assert set(launcher.child_env(Role.MODEL_ENTRY, demo_keys, user_env=USER_ENV)) == BASE | MODEL


def test_lines_printed_after_ready_can_be_read(tmp_path):
    """分析端在就緒那一行之後另印一行模型模式;啟動器把就緒之後的行留著給驅動讀。"""
    process = launcher._spawn(
        [sys.executable, "-c", "import time\nprint('READY', flush=True)\n"
         "print('MODEL {\"mode\": \"recorded\"}', flush=True)\ntime.sleep(30)\n"],
        {"PATH": os.environ["PATH"]}, tmp_path, "READY", tmp_path / "x.log")
    try:
        assert process.next_line(5) == 'MODEL {"mode": "recorded"}'
        assert process.next_line(0.2) is None  # 沒有下一行:等到期限回空的,不卡住
    finally:
        process.stop()


def test_a_model_entry_runs_once_with_the_model_entry_whitelist(tmp_path, monkeypatch):
    """模型說明與假說兩支命令列一次跑完:環境照模型入口的白名單,標準輸出整份交回;逾時收掉整組。"""
    root = launcher.prepare_root(tmp_path / "demos", "demo-1")
    probe = "import json, os\nprint(json.dumps(sorted(os.environ)))\n"
    real = launcher.entry_command

    def probing(entry, args):
        assert real(entry, args)[-len(args) - 1:] == [launcher.ENTRIES[entry], *args]
        return [sys.executable, "-c", probe]

    monkeypatch.setattr(launcher, "entry_command", probing)
    done = launcher.run_entry("narrate", ["--db", "x"], keys.DemoKeys.generate(), root=root,
                              user_env=USER_ENV, timeout_seconds=20)
    assert done.code == 0 and not done.timed_out
    seen = set(__import__("json").loads(done.stdout))
    assert seen - (BASE | MODEL) <= {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}

    monkeypatch.setattr(launcher, "entry_command",
                        lambda _entry, _args: [sys.executable, "-c", "import time\ntime.sleep(60)"])
    slow = launcher.run_entry("hypothesis", [], keys.DemoKeys.generate(), root=root,
                              user_env=USER_ENV, timeout_seconds=0.5)
    assert slow.timed_out and slow.code is None


def test_the_entries_are_the_two_model_entry_modules():
    assert launcher.ENTRIES == {"narrate": "rtb.analyzer.narrate",
                                "hypothesis": "rtb.ops.hypothesis"}
