"""Phase 13 增量 4:開了 AI 決策的分析端驅動命令列在就緒之後印一行它實際判出的模型模式與原因,
展示頁照這一行顯示(計劃〈展示頁怎麼顯示〉「頂端摘要的模型模式照 Phase 12」)。"""

import json

from rtb.analyzer import runner
from tests.analyzer.test_investigation_e2e import AiWorld, _propose


def test_the_runner_prints_the_mode_it_chose_right_after_ready(tmp_path):
    world = AiWorld(tmp_path, [_propose()])
    world.gate.notices = ("即時模式需要展示編號,這次改用錄製",)
    assert world.run_until_done(max_rounds=1) == 0
    first, second = world.out.getvalue().splitlines()[:2]
    assert first == runner.READY
    assert second.startswith(runner.MODEL_LINE + " ")
    assert second.isascii()  # 語系不是 UTF-8 時印中文會讓分析端當掉(代碼審 r1 l5)
    shown = json.loads(second.removeprefix(runner.MODEL_LINE + " "))
    assert shown == {"mode": "recorded", "notices": ["即時模式需要展示編號,這次改用錄製"]}


def test_the_mode_line_comes_after_the_login_preflight_and_says_when_it_failed(tmp_path):
    """(代碼審 r1 k4)模式行在登入預檢之後印,反映實際生效的模式:即時但預檢沒過 → 整趟改由程式規則,
    寫原因。"""
    from rtb.analyzer import modelgate
    from tests.model.fakes import live

    world = AiWorld(tmp_path, [_propose()])
    world.gate.settings = live()
    world.gate.preflight = modelgate.Preflight.FAILED
    assert world.run_until_done(max_rounds=1) == 0
    assert world.gate.events[0] == "preflight"
    line = world.out.getvalue().splitlines()[1]
    shown = json.loads(line.removeprefix(runner.MODEL_LINE + " "))
    assert shown["mode"] == runner.PREFLIGHT_FAILED_MODE == "rule"
    assert shown["notices"] == ["登入預檢沒過,這一趟改由程式規則決定:沒登入"]


def test_without_ai_judge_only_ready_is_printed(tmp_path):
    world = AiWorld(tmp_path, [_propose()])
    world.argv = lambda dsp, inbox: ["--db", str(world.root / "analyzer.db"), "--dsp-url", dsp,
                                     "--inbox-url", inbox, "--interval-seconds", "0.01"]
    assert world.run_until_done(max_rounds=1) == 0
    assert world.out.getvalue().splitlines() == [runner.READY]
