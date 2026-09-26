"""Phase 13 增量 4 展示串接:啟動器的即時粒度、就緒之後的輸出、一次跑完的模型入口
(計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈展示情境怎麼接 AI〉;[S1145] 的啟動器那一半)。
Phase 14 增量 3:分析端不再拿模型變數,即時清單只控制說明與假說兩支模型入口。"""

import sys

from rtb.demo import keys, launcher
from rtb.demo.launcher import Role
from tests.demo.test_launcher import BASE, MODEL, USER_ENV


def test_only_model_entries_get_model_env_and_only_when_live(tmp_path, monkeypatch):
    """[S1145] 改寫(Phase 14 增量 3):分析端永遠拿不到模型變數(不論即時清單);模型入口只在這個情境
    列在即時清單時多三個模型變數;其他角色一律沒有。"""
    demo_keys = keys.DemoKeys.generate()
    for role in (Role.ANALYZER, Role.INBOX, Role.EXECUTOR, Role.DSP):
        env = launcher.command_for(role, ["--db", "x"], demo_keys, root=None, faults=None,
                                   user_env=USER_ENV)[1]
        assert not set(env) & MODEL, role
    assert set(launcher.child_env(Role.ANALYZER, demo_keys, user_env=USER_ENV)) == BASE
    assert set(launcher.child_env(Role.MODEL_ENTRY, demo_keys, user_env=USER_ENV)) == BASE | MODEL
    root = launcher.prepare_root(tmp_path / "demos", "demo-1")
    probe = "import json, os\nprint(json.dumps(sorted(os.environ)))\n"
    monkeypatch.setattr(launcher, "entry_command", lambda _e, _a: [sys.executable, "-c", probe])
    for live, expected in ((True, MODEL), (False, set())):
        done = launcher.run_entry("narrate", [], demo_keys, root=root, user_env=USER_ENV,
                                  timeout_seconds=20, live_model=live)
        assert set(__import__("json").loads(done.stdout)) & MODEL == expected, live


def test_a_model_entry_runs_once_with_the_model_entry_whitelist(tmp_path, monkeypatch):
    """模型說明與假說兩支命令列一次跑完:環境照模型入口的白名單,標準輸出整份交回;逾時收掉整組。"""
    root = launcher.prepare_root(tmp_path / "demos", "demo-1")
    probe = "import json, os\nprint(json.dumps(sorted(os.environ)))\n"
    real = launcher.entry_command

    def probing(entry, args):
        built = real(entry, args)
        assert built[-len(args):] == list(args) and launcher.ENTRIES[entry] in " ".join(built)
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
