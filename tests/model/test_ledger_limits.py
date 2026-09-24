"""Phase 11B 增量 1 代碼審第 2 輪:花費帳的邊界(錢)。

- 核銷沒結算的預留:不論是不是同一次開機都先看主行程在不在;開機識別不隨牆鐘移動
  (撥鐘不會被當成重開機)。
- 核銷期限兩道(牆鐘、開機以來秒數)各自在期限內拒絕、過了才准。
- 荒謬值照預留結算(可核銷)、標超支,不把夾過的數字入帳。
- 快取寫入總數欄讀不懂就是讀不懂(不默默當 0)。
- 核銷金額有上界,太大丟「拒絕」不丟原生溢位。
- 預留把撞頂後的自動續寫(最多 3 次)算進去。
不呼叫真的 claude。
"""

import io
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelledger_view as view
from rtb import modelledger_writeoff as writeoff
from tests.model.fakes import FakeBackend, claude_json, fake_claude, live, request

BASE = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()


def _unsettled(tmp_path, monkeypatch, user):
    """在固定牆鐘下留一筆沒結算的預留(模擬行程在呼叫途中被殺)。"""
    monkeypatch.setattr(core, "utc_now", lambda: BASE)
    with pytest.raises(KeyboardInterrupt):
        mc.call_model(request(user, max_output_tokens=500), live(FakeBackend(KeyboardInterrupt())),
                      recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")
    return _rows(tmp_path / "l.sqlite")[-1]


def test_moving_the_system_clock_is_not_a_reboot(tmp_path, monkeypatch):
    """牆鐘與 time.time 一起撥快 480 秒、主行程還活著:不准核銷(不能當成重開過機而跳過檢查)。"""
    row = _unsettled(tmp_path, monkeypatch, "甲")
    real_time = time.time
    jump = 480 + core.LONGEST_REQUEST.total_seconds()
    monkeypatch.setattr(core, "utc_now", lambda: BASE + timedelta(seconds=jump))
    monkeypatch.setattr(time, "time", lambda: real_time() + jump)
    with pytest.raises(ledger_db.WriteOffRefused):
        ledger_db.write_off(tmp_path / "l.sqlite", row.id, Decimal("0"), "r", "本機紀錄")
    # 就算開機識別不同(被判成重開過機),主行程還活著就不准
    monkeypatch.setattr(core, "boot_identity", lambda: "id:another-boot")
    with pytest.raises(ledger_db.WriteOffRefused, match="還活著"):
        ledger_db.write_off(tmp_path / "l.sqlite", row.id, Decimal("0"), "r", "本機紀錄")
    monkeypatch.undo()
    monkeypatch.setattr(core, "utc_now", lambda: BASE + timedelta(seconds=jump))
    # 主行程判死之後,撥過的牆鐘仍擋不過開機以來秒數那一道
    monkeypatch.setattr(ledger_db, "_owner_alive", lambda _pid: False)
    monkeypatch.setattr(core, "monotonic_now", lambda: row.reserved_monotonic + 1.0)
    with pytest.raises(ledger_db.WriteOffRefused, match="時鐘"):
        ledger_db.write_off(tmp_path / "l.sqlite", row.id, Decimal("0"), "r", "本機紀錄")


@pytest.mark.parametrize("clock", ["wall", "monotonic"])
def test_both_write_off_deadlines_hold_until_they_pass(tmp_path, monkeypatch, clock):
    """主行程已不在:另一道時鐘遠過期限時,這一道在第 120 秒與剛好期限(420 秒)拒絕、第 421 秒才准。
    (最長請求期限 = 逾時上限 120 秒加 5 分鐘 = 420 秒。)"""
    monkeypatch.setattr(ledger_db, "_owner_alive", lambda _pid: False)
    edge = int(core.LONGEST_REQUEST.total_seconds())
    assert edge == 420
    far = 10 * edge
    for seconds, allowed in ((120, False), (edge, False), (edge + 1, True)):
        row = _unsettled(tmp_path, monkeypatch, f"{clock}-{seconds}")
        wall = seconds if clock == "wall" else far
        mono = seconds if clock == "monotonic" else far
        monkeypatch.setattr(core, "utc_now", lambda w=wall: BASE + timedelta(seconds=w))
        monkeypatch.setattr(core, "monotonic_now", lambda m=mono, r=row: r.reserved_monotonic + m)
        if allowed:
            ledger_db.write_off(tmp_path / "l.sqlite", row.id, Decimal("0"), "r", "本機紀錄")
        else:
            with pytest.raises(ledger_db.WriteOffRefused):
                ledger_db.write_off(tmp_path / "l.sqlite", row.id, Decimal("0"), "r", "本機紀錄")


def test_an_absurd_report_is_charged_the_reservation(tmp_path):
    """自報 100 億美元:照預留結算(可核銷)、標超支;不把夾過的 2 萬美元入帳鎖死這個展示與本月。"""
    script = fake_claude(tmp_path / "c", claude_json(cost_usd=1e10))
    with pytest.raises(mc.UnreadableModelResponse) as failed:
        mc.call_model(request(), live(cc.ClaudeCodeBackend(script)), recordings_dir=tmp_path,
                      ledger=tmp_path / "l.sqlite")
    assert failed.value.settlement.value.startswith("overrun")
    [row] = _rows(tmp_path / "l.sqlite")
    assert row.overrun is True and row.by_reservation is True
    assert row.effective_nanousd == row.reserved_nanousd
    assert ledger_db.used_so_far(tmp_path / "l.sqlite", "demo-1").month_nanousd == (
        row.reserved_nanousd)
    # 下一筆照常能預留(沒被鎖死)
    mc.call_model(request("下一筆"), live(), recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")


def test_an_unreadable_cache_total_is_unreadable(tmp_path):
    output = claude_json(cache_creation=0, cost_usd=0.0)
    output["usage"]["cache_creation_input_tokens"] = "50000"
    output["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 0,
                                         "ephemeral_1h_input_tokens": 0}
    script = fake_claude(tmp_path / "c", output)
    with pytest.raises(mc.UnreadableModelResponse):
        mc.call_model(request(), live(cc.ClaudeCodeBackend(script)), recordings_dir=tmp_path,
                      ledger=tmp_path / "l.sqlite")


def test_a_write_off_amount_has_a_ceiling(tmp_path):
    """核銷金額不能超過這筆的預留與結算兩者較高者;太大是拒絕,命令列不吐 traceback。"""
    ledger = mc.live_ledger_path()
    with pytest.raises(mc.TransientServiceError):  # 照預留結算的失敗,可以核銷
        mc.call_model(request(), live(FakeBackend(mc.TransientServiceError("x"))),
                      recordings_dir=tmp_path, ledger=ledger)
    [row] = _rows(ledger)
    too_much = Decimal(row.reserved_nanousd + 1) / core.NANOUSD_PER_USD
    for amount in (Decimal("1e12"), Decimal("1e6"), too_much):
        with pytest.raises(ledger_db.WriteOffRefused):
            ledger_db.write_off(ledger, row.id, amount, "r", "本機紀錄")
    out, err = io.StringIO(), io.StringIO()
    code = writeoff.run(["--reservation-id", str(row.id), "--amount-usd", "1e12", "--reason", "r",
                         "--evidence", "本機紀錄"], out=out, err=err)
    assert code == writeoff.EXIT_REFUSED
    ledger_db.write_off(ledger, row.id, Decimal(row.reserved_nanousd) / core.NANOUSD_PER_USD, "r",
                        "本機紀錄")  # 等於預留:准


def test_the_reservation_covers_automatic_continuations():
    """撞頂後 Claude Code 最多自動續寫 3 次(每次重送前文):預留與單次花費上限照 4 次請求算。"""
    req = request(max_output_tokens=100)
    price = core.PRICES[mc.DEFAULT_MODEL]
    one = ((len((req.system + req.user).encode()) + core.CLAUDE_FIXED_INPUT_TOKENS)
           * price.worst_input_nanousd + 100 * price.output_nanousd)
    assert core.OUTPUT_RECOVERY_ATTEMPTS == 3
    assert core.call_budget_nanousd(req, mc.DEFAULT_MODEL) >= 4 * one + 6 * 100 * (
        price.worst_input_nanousd)


def test_the_live_check_also_charges_an_absurd_report_its_reservation(tmp_path):
    """實測命令列的呼叫也走同一套荒謬值檢查:自報 100 萬美元照預留結算(可核銷)、標超支。"""
    from rtb import modelverify

    script = fake_claude(tmp_path / "c", claude_json(cost_usd=1e6))
    checker = modelverify.Checker(script, {"PATH": str(script.parent)}, mc.DEFAULT_MODEL)
    checker.run(checker.command_for(modelverify.SHORT_PROMPT, 50), modelverify.SHORT_PROMPT, 50)
    [row] = _rows(mc.live_ledger_path())
    assert row.overrun is True and row.by_reservation is True
    assert row.effective_nanousd == row.reserved_nanousd
