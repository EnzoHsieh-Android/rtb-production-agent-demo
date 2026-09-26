"""錄展示批次的命令列(Phase 13 增量 4 補:協調者要用真 claude 錄 F1 到 F6 的展示錄製;Phase 14 增量 3
改成只錄提案說明與告警假說,新批號 phase14-demo-YYYYMMDD,[S1428])。

測試一律用假 claude 加測試用的即時模式啟用紀錄(帳號家目錄是夾具換上的暫存目錄,子行程也是),不碰真
模型、不寫真的 ~/.rtb。"""

import json
import os

import pytest

from rtb import modelclient as mc
from rtb.demo import driver as driver_module
from rtb.demo import recordings as demo_recordings
from tests.model.fakes import claude_json, fake_claude, invocations, write_verification

BATCH = "phase14-demo-20260926"
NARRATIVE = "這個廣告花得比預期慢,程式照公式把預算從 100 加到 110,只供確認時參考。"


def _live_env(tmp_path, *, verified=True, with_claude=True):
    """假 claude 每次都回一段說明;寫一份測試用的啟用紀錄。"""
    env = dict(os.environ)
    script = None
    if with_claude:
        script = fake_claude(tmp_path / "bin", claude_json(NARRATIVE))
        env["PATH"] = f"{script.parent}:{env['PATH']}"
    if verified:
        write_verification()
    return env, script


def test_a_recorded_batch_covers_f1_to_f6_and_passes_the_intake_check(tmp_path, monkeypatch):
    """以即時加錄製跑 F1 到 F6(F7 不跑),說明與假說入口寫進同一個全新目錄、同一個批次(分析端不呼叫
    AI,批次裡沒有分析端調查);錄完自動跑入庫前檢查,通過。"""
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
    assert mc.Caller.NARRATIVE.value in callers and mc.Caller.INVESTIGATION.value not in callers
    assert result.passed


@pytest.mark.parametrize("case", ["not_empty", "committed", "bad_batch", "unverified",
                                  "no_claude"])
def test_recording_refuses_before_calling_anything(tmp_path, case):
    """開錄前:目錄要不存在或是空的、不在入庫目錄底下;批次編號要是 phase14-demo-YYYYMMDD;即時啟用紀錄
    或 claude 不可用就拒絕(不退回錄製假裝錄好)。都不呼叫模型、不跑任何情境。"""
    env, script = _live_env(tmp_path, verified=case != "unverified",
                            with_claude=case != "no_claude")
    target, batch = tmp_path / "batch", BATCH
    if case == "not_empty":
        target.mkdir()
        (target / "note.txt").write_text("x", encoding="utf-8")
    elif case == "committed":
        target = mc.default_recordings_dir() / "phase14-demo-probe"
    elif case == "bad_batch":
        batch = "demo-live-x"
    result = demo_recordings.record_demo_batch(target, batch, tmp_path / "work", user_env=env)
    assert result.problems and not result.passed and result.check is None
    assert result.verdicts == ()
    if script is not None:
        assert invocations(script) == []
    assert not (mc.default_recordings_dir() / "phase14-demo-probe").exists()


def test_the_command_line_prints_the_result_and_the_next_step(tmp_path, capsys):
    env, _script = _live_env(tmp_path, verified=False)
    with pytest.raises(SystemExit) as ended:
        demo_recordings.main(["--record", "--dir", str(tmp_path / "b"), "--batch-id", BATCH,
                              "--work-dir", str(tmp_path / "w")], user_env=env)
    assert ended.value.code == 1
    shown = json.loads(capsys.readouterr().out)
    assert shown["passed"] is False and shown["problems"]


# ---- 協調者 2026-09-25:相對路徑的 --dir 重播全找不到錄製 ----
def test_the_intake_check_accepts_relative_paths(tmp_path, monkeypatch, capsys):
    """命令列用相對路徑給 --dir 與 --work-dir(錄完印的下一步就是這種寫法):一收到就轉成絕對路徑,
    子行程在別的工作目錄也讀得到同一批錄製,有效批次照樣通過。"""
    from tests.demo import fake_recordings as fake

    fake.fake_batch(tmp_path / "rec", BATCH, ("normal", "attacked"), backend="claude_code")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as done:
        demo_recordings.main(["--dir", "rec", "--batch-id", BATCH, "--work-dir", "work"])
    printed = json.loads(capsys.readouterr().out)
    assert done.value.code == 0 and printed["passed"] is True, printed
    assert printed["missing"] == 0
    assert "{batch}" not in demo_recordings.NEXT_STEP or str(
        driver_module.DEMO_RECORDINGS) in demo_recordings.NEXT_STEP.format(batch=BATCH)
