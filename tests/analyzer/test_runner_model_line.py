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
    shown = json.loads(second.removeprefix(runner.MODEL_LINE + " "))
    assert shown == {"mode": "recorded", "notices": ["即時模式需要展示編號,這次改用錄製"]}


def test_without_ai_judge_only_ready_is_printed(tmp_path):
    world = AiWorld(tmp_path, [_propose()])
    world.argv = lambda dsp, inbox: ["--db", str(world.root / "analyzer.db"), "--dsp-url", dsp,
                                     "--inbox-url", inbox, "--interval-seconds", "0.01"]
    assert world.run_until_done(max_rounds=1) == 0
    assert world.out.getvalue().splitlines() == [runner.READY]
