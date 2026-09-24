"""Phase 11B 增量 1 代碼審第 1 輪:花費帳的補強(錢)。

- 自報花費跟 token 數分開讀:用量缺欄時,自報仍參與取較高者,超過預留就照實記、標超支。
- 核銷未結算的列:預留時記下主行程編號與開機識別,主行程還活著就不准核銷;
  牆鐘與單調時鐘對不上時保守拒絕。
- 預留只讀一次系統時鐘:判上限的月份就是寫進帳的月份。
- 荒謬的自報或 token 數(超過上限的千倍)當讀不懂、標超支,不讓原生溢位例外漏出去。
- 結算寫不進去又超支:狀態同時帶超支與未結算,有上限地重試,仍失敗就把金額印到標準錯誤。
- 即時模式的帳檔與啟用紀錄跟著帳號的家目錄走,不跟環境變數 HOME。
- 快取寫入分開的數字加總小於總數時,差額算 1 小時寫入。
- 最長請求期限與花費估計差距門檻的數值釘住。
"""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelledger_view as view
from tests.model.fakes import FakeBackend, claude_json, fake_claude, live, reply, request

SRC = Path(__file__).resolve().parents[2] / "src"


def _rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()


def _call(tmp_path, backend, req=None, name="l"):
    return mc.call_model(req or request(), live(backend), recordings_dir=tmp_path,
                         ledger=tmp_path / f"{name}.sqlite")


def test_reported_cost_counts_even_without_token_counts(tmp_path):
    """成功形狀缺用量、錯誤形狀沒有用量,但都自報 0.6 美元(遠大於預留):入帳照自報乘 1.2、標超支。"""
    reported = 600_000_000
    for name, output, error in (
            ("success", claude_json(cost_usd=0.6, usage=False), mc.UnreadableModelResponse),
            ("failure", claude_json("boom", is_error=True, cost_usd=0.6, usage=False),
             mc.TransientServiceError)):
        script = fake_claude(tmp_path / name, output)
        with pytest.raises(error) as failed:
            _call(tmp_path, cc.ClaudeCodeBackend(script), name=name)
        [row] = _rows(tmp_path / f"{name}.sqlite")
        assert row.effective_nanousd == reported * 6 // 5 > row.reserved_nanousd, name
        assert row.overrun is True and row.reported_nanousd == reported, name
        assert failed.value.settlement.value.startswith("overrun"), name


def test_a_live_owner_blocks_writing_off_its_reservation(tmp_path, monkeypatch):
    """還在等回應的預留(主行程活著)不能被核銷掉;主行程死了、兩種時鐘對得上才准。"""
    ledger = tmp_path / "l.sqlite"
    base_wall = datetime.now(UTC)
    with pytest.raises(KeyboardInterrupt):  # 這個測試行程自己預留、沒結算:主行程還活著
        _call(tmp_path, FakeBackend(KeyboardInterrupt()), request(max_output_tokens=500))
    [mine] = _rows(ledger)
    monkeypatch.setattr(core, "utc_now", lambda: base_wall + core.LONGEST_REQUEST * 2)
    monkeypatch.setattr(core, "monotonic_now", lambda: getattr(mine, "reserved_monotonic", 0)
                        + core.LONGEST_REQUEST.total_seconds() * 2, raising=False)
    with pytest.raises(ledger_db.WriteOffRefused, match="還活著"):
        ledger_db.write_off(ledger, mine.id, Decimal("0"), "理由", "本機紀錄")
    # 另一個行程預留後死掉(模擬被殺):主行程不在了、兩種時鐘都過了期限 → 准核銷
    code = ("import os\nfrom pathlib import Path\nfrom rtb import modelclient as mc\n"
            "from rtb import modelcore as core\n"
            "class Die:\n    kind = core.Backend.CLAUDE_CODE\n"
            "    def send(self, call):\n        os._exit(0)\n"
            "req = mc.ModelRequest(mc.Caller.EVAL_CANDIDATE, 's', 'u', 500, 5.0, demo_id='d')\n"
            "settings = mc.Settings(mc.Mode.LIVE, mc.DEFAULT_MODEL, False, Die(), ())\n"
            f"mc.call_model(req, settings, recordings_dir=Path({str(tmp_path)!r}), "
            f"ledger=Path({str(ledger)!r}))\n")
    subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(SRC)},
                   check=True, timeout=60)
    dead = _rows(ledger)[-1]
    assert dead.outcome is None and dead.owner_pid != os.getpid()
    monkeypatch.setattr(core, "monotonic_now",
                        lambda: dead.reserved_monotonic + core.LONGEST_REQUEST.total_seconds() * 2)
    ledger_db.write_off(ledger, dead.id, Decimal("0"), "被殺的行程", "本機紀錄:行程已不在")
    # 牆鐘過了期限、單調時鐘沒過(睡眠或被暫停):保守拒絕
    with pytest.raises(KeyboardInterrupt):
        _call(tmp_path, FakeBackend(KeyboardInterrupt()), request(max_output_tokens=400))
    other = _rows(ledger)[-1]
    monkeypatch.setattr(core, "utc_now", lambda: base_wall + core.LONGEST_REQUEST * 4)
    monkeypatch.setattr(core, "monotonic_now", lambda: other.reserved_monotonic + 1.0)
    monkeypatch.setattr(ledger_db, "_owner_alive", lambda _pid: False)  # 只剩時鐘這一道
    with pytest.raises(ledger_db.WriteOffRefused, match="時鐘"):
        ledger_db.write_off(ledger, other.id, Decimal("0"), "理由", "本機紀錄")


def test_a_reservation_reads_the_clock_once(tmp_path, monkeypatch):
    """跨 UTC 月界的那一刻:判上限用的月份就是寫進帳的月份(同一次讀鐘)。"""
    ledger = tmp_path / "l.sqlite"
    req = request()
    per_call = core.reservation_nanousd(req, mc.DEFAULT_MODEL)
    monkeypatch.setattr(core, "MONTH_CAP_NANOUSD", per_call)
    monkeypatch.setattr(core, "utc_now", lambda: datetime(2026, 9, 2, tzinfo=UTC))
    ledger_db.reserve(ledger, req, mc.DEFAULT_MODEL, core.Backend.CLAUDE_CODE)  # 9 月用滿
    readings = iter([datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)]
                    + [datetime(2026, 9, 1, 0, 0, 1, tzinfo=UTC)] * 50)
    monkeypatch.setattr(core, "utc_now", lambda: next(readings))
    ledger_db.reserve(ledger, request(demo_id="demo-2"), mc.DEFAULT_MODEL,
                      core.Backend.CLAUDE_CODE)
    september = [r for r in _rows(ledger) if r.month == "2026-09"]
    assert sum(r.effective_nanousd for r in september) <= per_call
    assert _rows(ledger)[-1].month == "2026-08"


def test_absurd_usage_is_unreadable_and_an_overrun(tmp_path):
    """自報 100 億美元:當讀不懂、標超支,不讓溢位例外漏出去(評估才停得下來)。"""
    script = fake_claude(tmp_path / "c", claude_json(cost_usd=1e10))
    with pytest.raises(mc.UnreadableModelResponse) as failed:
        _call(tmp_path, cc.ClaudeCodeBackend(script))
    assert failed.value.settlement.value.startswith("overrun")
    [row] = _rows(tmp_path / "l.sqlite")
    assert row.overrun is True and row.effective_nanousd > row.reserved_nanousd
    huge_tokens = fake_claude(tmp_path / "t", claude_json(output_tokens=10**16))
    with pytest.raises(mc.UnreadableModelResponse):
        _call(tmp_path, cc.ClaudeCodeBackend(huge_tokens), name="t")


def test_an_unsettled_overrun_keeps_both_signals(tmp_path, monkeypatch, capsys):
    """結算寫不進去又超支:狀態同時帶超支與未結算;有上限地重試,仍失敗就把超支金額印到標準錯誤。"""
    attempts = []

    def busy(*_args, **_kwargs):
        from rtb.sqlitekit import DatabaseBusy

        attempts.append(1)
        raise DatabaseBusy("locked")

    monkeypatch.setattr(ledger_db, "settle", busy)
    result = _call(tmp_path, FakeBackend(reply(input_tokens=0, output_tokens=0,
                                               reported_usd=0.9)))
    assert result.settlement.value == "overrun_unsettled"
    assert len(attempts) == ledger_db.SETTLE_ATTEMPTS == 3
    assert "1.080000" in capsys.readouterr().err  # 0.9 美元乘 1.2


def test_the_ledger_follows_the_account_home_not_the_home_variable(tmp_path, monkeypatch):
    from tests.conftest import ACCOUNT_HOME, REAL_HOME

    # 共用夾具換掉了帳號家目錄的讀法:先驗真的那一支(只算路徑、不開帳)
    monkeypatch.setattr(view, "account_home", ACCOUNT_HOME)
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    assert view.ledger_path() == REAL_HOME / ".rtb" / "model-ledger.sqlite"
    assert cc.verification_path() == REAL_HOME / ".rtb" / "live-verification.json"
    assert not view.ledger_path().is_relative_to(tmp_path / "elsewhere")
    assert not cc.verification_path().is_relative_to(tmp_path / "elsewhere")
    assert view.ledger_path() == view.account_home() / ".rtb" / "model-ledger.sqlite"


def test_cache_writes_missing_from_the_split_count_as_one_hour(tmp_path):
    output = claude_json(input_tokens=0, output_tokens=0, cache_creation=100_000, cost_usd=0.0)
    output["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 0,
                                         "ephemeral_1h_input_tokens": 0}
    _call(tmp_path, cc.ClaudeCodeBackend(fake_claude(tmp_path / "c", output)),
          request(max_output_tokens=5000))
    [row] = _rows(tmp_path / "l.sqlite")
    assert (row.cache_write_5m_tokens, row.cache_write_1h_tokens) == (0, 100_000)
    assert row.list_nanousd == 100_000 * 4_000


def test_the_longest_request_and_mismatch_thresholds_are_pinned(tmp_path):
    assert timedelta(seconds=120) + timedelta(minutes=5) == core.LONGEST_REQUEST
    assert core.COST_MISMATCH == (1, 5)
    # token 數算出 0.0001 美元(輸入 50 個):自報差 19% 不標、差 21% 要標
    for name, reported, flagged in (("19", 0.000119, False), ("21", 0.000121, True),
                                    ("m19", 0.0000839, False), ("m21", 0.0000826, True)):
        _call(tmp_path, FakeBackend(reply(input_tokens=50, output_tokens=0,
                                          reported_usd=reported)), name=name)
        [row] = _rows(tmp_path / f"{name}.sqlite")
        assert row.cost_mismatch is flagged, name
