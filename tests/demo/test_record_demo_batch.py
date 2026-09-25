"""錄展示批次的命令列(Phase 13 增量 4 補:協調者要用真 claude 錄 F1 到 F6 的展示錄製)。

測試一律用假 claude 加測試用的即時模式啟用紀錄(帳號家目錄是夾具換上的暫存目錄,子行程也是),不碰真
模型、不寫真的 ~/.rtb。"""

import json
import os

import pytest

from rtb import modelclient as mc
from rtb.demo import driver as driver_module
from rtb.demo import recordings as demo_recordings
from tests.model.fakes import claude_json, fake_claude, invocations, write_verification

BATCH = "phase13-demo-20260925"
PROPOSE = json.dumps({"choice": "propose", "reason": "有點擊也有轉換,值得加",
                      "evidence": [{"ref": "base", "field": "conversions", "value": "1"}]},
                     ensure_ascii=False)


def _live_env(tmp_path, *, verified=True, with_claude=True):
    """假 claude 每次都回一個合法的「值得加」答案(說明命令列也拿到同一段文字);寫一份測試用的啟用
    紀錄。"""
    env = dict(os.environ)
    script = None
    if with_claude:
        script = fake_claude(tmp_path / "bin", claude_json(PROPOSE))
        env["PATH"] = f"{script.parent}:{env['PATH']}"
    if verified:
        write_verification()
    return env, script


def test_a_recorded_batch_covers_f1_to_f6_and_passes_the_intake_check(tmp_path, monkeypatch):
    """以即時加錄製跑 F1 到 F6(F7 不跑),三支模型入口都寫進同一個全新目錄、同一個批次;錄完自動
    跑入庫前檢查,通過。"""
    env, script = _live_env(tmp_path)
    ran = []
    real = driver_module.Driver.run_one

    def spy(self, code):
        ran.append(code)
        return real(self, code)

    monkeypatch.setattr(driver_module.Driver, "run_one", spy)
    target = tmp_path / "batch"
    result = demo_recordings.record_demo_batch(target, BATCH, tmp_path / "work", user_env=env)
    assert result.problems == (), result
    assert result.check is not None and result.check.passed, result.check
    assert ran[:6] == ["F1", "F2", "F3", "F4", "F5", "F6"] and "F7" not in ran
    assert invocations(script)  # 真的即時呼叫了(假 claude)
    files = sorted(target.glob("*.json"))
    assert files
    recordings = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    assert {r["batch_id"] for r in recordings} == {BATCH}
    assert {r["backend"] for r in recordings} == {"claude_code"}
    callers = {r["caller"] for r in recordings}
    assert {mc.Caller.INVESTIGATION.value, mc.Caller.NARRATIVE.value} <= callers
    assert result.passed


@pytest.mark.parametrize("case", ["not_empty", "committed", "bad_batch", "unverified",
                                  "no_claude"])
def test_recording_refuses_before_calling_anything(tmp_path, case):
    """開錄前:目錄要不存在或是空的、不在入庫目錄底下;批次編號要是 phase13-demo-YYYYMMDD;即時啟用紀錄
    或 claude 不可用就拒絕(不退回錄製假裝錄好)。都不呼叫模型、不跑任何情境。"""
    env, script = _live_env(tmp_path, verified=case != "unverified",
                            with_claude=case != "no_claude")
    target, batch = tmp_path / "batch", BATCH
    if case == "not_empty":
        target.mkdir()
        (target / "note.txt").write_text("x", encoding="utf-8")
    elif case == "committed":
        target = mc.default_recordings_dir() / "phase13-demo-probe"
    elif case == "bad_batch":
        batch = "demo-live-x"
    result = demo_recordings.record_demo_batch(target, batch, tmp_path / "work", user_env=env)
    assert result.problems and not result.passed and result.check is None
    assert result.verdicts == ()
    if script is not None:
        assert invocations(script) == []
    assert not (mc.default_recordings_dir() / "phase13-demo-probe").exists()


def test_the_command_line_prints_the_result_and_the_next_step(tmp_path, capsys):
    env, _script = _live_env(tmp_path, verified=False)
    with pytest.raises(SystemExit) as ended:
        demo_recordings.main(["--record", "--dir", str(tmp_path / "b"), "--batch-id", BATCH,
                              "--work-dir", str(tmp_path / "w")], user_env=env)
    assert ended.value.code == 1
    shown = json.loads(capsys.readouterr().out)
    assert shown["passed"] is False and shown["problems"]
