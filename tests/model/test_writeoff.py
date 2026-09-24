"""Phase 11B 增量 1:花費帳的人工核銷([S923])。

核銷只追加一列、原因與依據必填(訂閱後端沒有主控台帳單,依據寫本機紀錄);只接受已照預留金額結算的列,或預留超過最長請求期限還沒結算的
列(還在等回應的預留不能被核銷掉)。已用照核銷後的金額算,原列不改;核銷之後才來的結算,已用改算兩者中較高
的金額,並印出錯誤要人看。
"""

import io
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelledger_view as view
from rtb import modelledger_writeoff as writeoff
from tests.model.fakes import FakeBackend, live, reply, request


def _rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return {r.id: r for r in reader.calls_between("0000", "9999")}
    finally:
        reader.close()


def _dump(ledger, tables=("model_reservations", "model_settlements")):
    conn = sqlite3.connect(ledger)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()  # noqa: S608
                for t in tables}
    finally:
        conn.close()


def _cli(*args):
    out, err = io.StringIO(), io.StringIO()
    code = writeoff.run(list(args), out=out, err=err)
    return code, out.getvalue() + err.getvalue()


def _write_off(reservation_id, amount="0.001", reason="子行程逾時前就被殺,本機紀錄顯示沒有回應",
               usage="子行程結束狀態 -9;Claude Code 用量畫面該時段 0 次"):
    return _cli("--reservation-id", str(reservation_id), "--amount-usd", amount,
                "--reason", reason, "--evidence", usage)


def test_a_write_off_is_appended_and_changes_the_used_amount(  # noqa: PLR0915 - 一條合約的整段情境
        tmp_path, monkeypatch, caplog):
    ledger = mc.live_ledger_path()  # 核銷命令列寫死家目錄那一本(共用夾具已指到暫存目錄)
    recordings = tmp_path / "rec"
    recordings.mkdir()
    clock = [datetime(2026, 9, 24, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(core, "utc_now", lambda: clock[0])

    def run(req, backend):
        return mc.call_model(req, live(backend), recordings_dir=recordings, ledger=ledger)

    with pytest.raises(mc.TransientServiceError):  # 1:照預留結算的失敗
        run(request("甲", max_output_tokens=500), FakeBackend(mc.TransientServiceError("x")))
    run(request("乙"), FakeBackend(reply("好")))  # 2:照實際 token 結算的成功
    with pytest.raises(KeyboardInterrupt):  # 3:預留後被殺、沒結算
        run(request("丙", max_output_tokens=500), FakeBackend(KeyboardInterrupt()))
    rows = _rows(ledger)
    reserved_1, reserved_3 = rows[1].reserved_nanousd, rows[3].reserved_nanousd
    assert ledger_db.used_so_far(ledger, "demo-1").demo_nanousd == (
        reserved_1 + rows[2].settled_nanousd + reserved_3)
    before = _dump(ledger)

    code, text = _write_off(2)  # 照實際結算的不能核銷
    assert code == writeoff.EXIT_REFUSED and "不能核銷" in text
    code, text = _write_off(3)  # 還在最長請求期限內:可能還在等回應
    assert code == writeoff.EXIT_REFUSED
    for reason in ("", "   "):  # 原因必填
        code, _ = _write_off(1, reason=reason)
        assert code == writeoff.EXIT_REFUSED
    code, _ = _write_off(1, usage=" ")
    assert code == writeoff.EXIT_REFUSED
    with pytest.raises(SystemExit):  # 沒給原因:參數錯
        writeoff.run(["--reservation-id", "1", "--amount-usd", "0",
                      "--evidence", "u"], out=io.StringIO(), err=io.StringIO())
    code, text = _write_off(1, amount="0")
    assert code == writeoff.EXIT_OK, text
    code, _ = _write_off(1, amount="0")  # 同一筆不能核銷兩次
    assert code == writeoff.EXIT_REFUSED
    assert ledger_db.used_so_far(ledger, "demo-1").demo_nanousd == (
        rows[2].settled_nanousd + reserved_3)
    clock[0] += core.LONGEST_REQUEST + timedelta(seconds=1)  # 超過最長請求期限
    code, text = _write_off(3, amount="0.0001")  # 主行程(這個測試行程)還活著:仍不准
    assert code == writeoff.EXIT_REFUSED and "還活著" in text
    # 主行程已不在(模擬被殺)、開機以來秒數也過了期限:才准
    monkeypatch.setattr(ledger_db, "_owner_alive", lambda _pid: False)
    monkeypatch.setattr(core, "monotonic_now", lambda: rows[3].reserved_monotonic
                        + core.LONGEST_REQUEST.total_seconds() + 1)
    code, _ = _write_off(3, amount="0.0001")
    assert code == writeoff.EXIT_OK
    assert ledger_db.used_so_far(ledger, "demo-1").demo_nanousd == rows[2].settled_nanousd + 100_000
    assert _dump(ledger) == before  # 原列不改,只追加核銷列
    offs = _dump(ledger, ("model_write_offs",))["model_write_offs"]
    assert len(offs) == 2 and all("本機紀錄" in str(o) for o in offs)
    with pytest.raises(sqlite3.DatabaseError):  # 帳本三張表都只增不改
        conn = sqlite3.connect(ledger)
        try:
            conn.execute("UPDATE model_write_offs SET amount_nanousd = 0")
        finally:
            conn.close()
    with pytest.raises(ledger_db.WriteOffRefused):
        ledger_db.write_off(ledger, 99, Decimal("0"), "r", "u")  # 不存在的列

    # 核銷之後才來的結算:已用改算較高的金額,並印出錯誤
    caplog.set_level(logging.ERROR)
    release = threading.Event()

    def late(_call):
        release.wait(5)
        return reply("遲到的回應", input_tokens=1000, output_tokens=300)

    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(run(request("丁"), FakeBackend(late))))
    worker.start()
    deadline = time.monotonic() + 5
    while 4 not in _rows(ledger) and time.monotonic() < deadline:
        time.sleep(0.01)
    clock[0] += core.LONGEST_REQUEST + timedelta(seconds=1)
    # 模擬主行程已判定不在、開機以來秒數也過了期限(上面已換掉主行程存活判斷),才核銷得了
    monkeypatch.setattr(core, "monotonic_now", lambda: _rows(ledger)[4].reserved_monotonic
                        + core.LONGEST_REQUEST.total_seconds() + 1)
    code, _ = _write_off(4, amount="0")
    assert code == writeoff.EXIT_OK
    assert ledger_db.used_so_far(ledger, "demo-1").demo_nanousd == rows[2].settled_nanousd + 100_000
    release.set()
    worker.join(10)
    settled = _rows(ledger)[4].settled_nanousd
    assert outcome and settled > 0
    assert ledger_db.used_so_far(ledger, "demo-1").demo_nanousd == (
        rows[2].settled_nanousd + 100_000 + settled)
    assert any("核銷" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
