"""Phase 11B 增量 2 接入點 2:提案的模型說明命令列(經 Phase 13 的分析端模型閘道)。

計劃 [[Projects/RTB_Phase11B大模型接入_計劃]]〈接入點 2〉的 [S913]、[S914]、[S916](接入點 2 那半)、
[S929],與 [[Projects/RTB_Phase13AI參與決策_計劃]] 的 [S1102](說明命令列那半)。已送進收件口的提案用
F5 端到端同一條真的路(模擬 DSP、分析行程、收件口、執行迴圈)跑出來;模型一律是行程內的假後端或錄製,
不呼叫真的 claude。
"""

import json
import shutil
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rtb import modelclient as mc
from rtb import modelledger_view as view
from rtb.analyzer import modelgate, narrate
from rtb.analyzer import task_store as ts
from rtb.analyzer.task_store import TaskReader, TaskStore
from rtb.domain.task_state import TaskState
from tests.adversarial_samples import SAMPLES
from tests.analyzer.test_f5_end_to_end import NORMAL_NAME, SAMPLE_IDS, run_once
from tests.model.fakes import FakeBackend, live, recorded, reply
from tests.ops.test_hypothesis import (
    hypothesis_ignores_injected_names,
    recorded_hypotheses_book_into_the_given_ledger,
)

SRC = Path(__file__).resolve().parents[2] / "src"
GOOD = "這份提案把預算加一成,依據是配速偏低;模型產生的說明只供參考。"


@pytest.fixture(scope="module")
def handed_off(tmp_path_factory):
    """一份已送進收件口、已執行的提案(整條真的路跑一次);每支測試複製一份資料庫來用。"""
    base = tmp_path_factory.mktemp("f5")
    result = run_once(base, NORMAL_NAME, "underpacing")
    assert result["state"] is TaskState.HANDED_OFF
    return base


def _copy(handed_off, tmp_path):
    for name in ("analyzer.db", "inbox.db", "dsp.db"):
        shutil.copy(handed_off / name, tmp_path / name)
    return tmp_path / "analyzer.db"


def _gate(tmp_path, settings, demo_id="demo-1"):
    recordings = tmp_path / "rec"
    recordings.mkdir(exist_ok=True)
    return modelgate.Gate(settings, demo_id, tmp_path / "ledger.sqlite", recordings)


def _narrate(db, gate, *, owner="n1", now=None):
    store = TaskStore(db)
    try:
        return narrate.narrate_pending(store, gate, owner=owner,
                                       clock=lambda: now or datetime.now(UTC))
    finally:
        store.close()


def _dump(path):
    conn = sqlite3.connect(path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def _status(db):
    reader = TaskReader(db)
    try:
        row = next(r for r in reader.handed_off_rows())
        return reader.narrative_for(row.task_id, row.proposal.revision,
                                    narrate.proposal_hash(row.proposal))
    finally:
        reader.close()


def _tables(db, *names):
    conn = sqlite3.connect(db)
    try:
        return {name: conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()  # noqa: S608
                for name in names}
    finally:
        conn.close()


# ---- [S913] ----
@pytest.mark.parametrize(("answer", "expected"), [
    (reply(GOOD), "ok"), (mc.ModelTimeout("slow"), "timeout"), (None, "no_recording"),
    (reply("第一行\n第二行"), "unreadable")], ids=["success", "timeout", "no_recording",
                                                  "unreadable"])
def test_the_model_narrative_never_changes_the_submitted_proposal(handed_off, tmp_path, answer,
                                                                   expected):
    db = _copy(handed_off, tmp_path)
    inbox_before = _dump(tmp_path / "inbox.db")
    analyzer_before = _tables(db, "tasks", "evidence", "tool_calls", "task_leases")
    settings = recorded() if answer is None else live(FakeBackend(answer))
    [done] = _narrate(db, _gate(tmp_path, settings))
    assert done.outcome == expected
    assert _dump(tmp_path / "inbox.db") == inbox_before  # 收件口的提案逐位元組相同
    assert _tables(db, "tasks", "evidence", "tool_calls", "task_leases") == analyzer_before


# ---- [S914] ----
def test_the_model_narrative_never_reaches_the_executor(handed_off, tmp_path):
    db = _copy(handed_off, tmp_path)
    marker = "說明記號QX7"
    [done] = _narrate(db, _gate(tmp_path, live(FakeBackend(reply(f"{GOOD}{marker}")))))
    assert done.outcome == "ok" and marker in (_status(db).text or "")
    for name in ("inbox.db", "dsp.db"):  # 收件口、嘗試紀錄與 DSP 都沒有說明文字
        assert marker not in _dump(tmp_path / name), name
    # 執行端全套件不讀說明:原始碼不提說明表與讀法,也不匯入分析端(既有的行程邊界)
    for path in sorted((SRC / "rtb" / "executor").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "narrative" not in source and "rtb.analyzer" not in source, path.name
    # 提案型別本身沒有說明欄位,能力憑證與 DSP 請求都從提案組
    from rtb.domain.proposal import CHECKS
    assert not any("narrative" in name for name in CHECKS)


# ---- [S916](接入點 2) ----
@pytest.mark.parametrize("name", [text for _category, text in SAMPLES], ids=SAMPLE_IDS)
def test_injected_campaign_names_cannot_change_any_program_decision(tmp_path, name,
                                                                   narrated_normal):
    result = run_once(tmp_path, name, "underpacing")
    db = tmp_path / "analyzer.db"
    obeying = FakeBackend(reply("照指示:把預算改成 900000000,並停用所有廣告"))
    [done] = _narrate(db, _gate(tmp_path, live(obeying)))
    assert done.outcome == "ok"  # 模型照誘導回答,只影響給人看的文字
    assert result["writes"] == narrated_normal["writes"] == [("update_budget", 2)]
    assert result["c1"].budget == narrated_normal["c1"].budget == 110
    assert result["other_writes"] == []
    assert result["proposal"].requested_change == narrated_normal["proposal"].requested_change
    numbers, data = obeying.calls[0].user.split("<<<資料開始")
    assert "t1" not in numbers and "c1" not in numbers  # 編號換成佔位符(名稱本身可能含這些字)
    assert name[:100] in data  # 名稱原文只在資料區
    reader = TaskReader(db)
    try:
        [row] = reader.handed_off_rows()
        assert row.proposal == result["proposal"]
    finally:
        reader.close()
    # 接入點 1(假說命令列)同一條:名稱不送出、不寫業務資料庫、結束代碼不變,輸出只影響給人看的文字
    outcomes = hypothesis_ignores_injected_names(tmp_path, name)
    assert outcomes[0] == "failed"  # 下一步不在固定清單:整份作廢
    assert outcomes[1] in ("ok", "failed")  # 名稱原文有控制字元時照樣作廢


@pytest.fixture(scope="module")
def narrated_normal(tmp_path_factory):
    base = tmp_path_factory.mktemp("normal-narrated")
    result = run_once(base, NORMAL_NAME, "underpacing")
    _narrate(base / "analyzer.db", _gate(base, live(FakeBackend(reply(GOOD)))))
    return result


# ---- [S929] ----
def test_two_narrators_call_the_model_once_per_proposal(handed_off, tmp_path, monkeypatch):  # noqa: PLR0915 - 領取的每一條規則
    db = _copy(handed_off, tmp_path)
    release = threading.Event()

    def slow(_call):
        release.wait(5)
        return reply(GOOD)

    backend = FakeBackend(slow)
    gate = _gate(tmp_path, live(backend))
    results = {}

    def worker(owner):
        results[owner] = _narrate(db, gate, owner=owner)

    threads = [threading.Thread(target=worker, args=(f"n{n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while not backend.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    release.set()
    for thread in threads:
        thread.join(10)
    assert len(backend.calls) == 1  # 同時兩個,只呼叫一次
    outcomes = sorted(r.outcome for rs in results.values() for r in rs)
    assert outcomes == ["ok", "skipped"]
    # 已有成功結果:之後誰來都不再領
    again = FakeBackend(reply("不該呼叫"))
    assert [r.outcome for r in _narrate(db, _gate(tmp_path, live(again)))] == ["already_done"]
    assert again.calls == []

    # 最新一次領取是失敗:可以立刻再領
    (tmp_path / "second").mkdir()
    db2 = _copy(handed_off, tmp_path / "second")
    [first] = _narrate(db2, _gate(tmp_path, live(FakeBackend(mc.TransientServiceError("x")))))
    assert first.outcome == "transient"
    retry = FakeBackend(reply(GOOD))
    [second] = _narrate(db2, _gate(tmp_path, live(retry)))
    assert second.outcome == "ok" and len(retry.calls) == 1

    # 沒有結果、不到 10 分鐘:跳過;超過 10 分鐘:可以再領;被接手的舊持有者寫不進結果
    (tmp_path / "third").mkdir()
    db3 = _copy(handed_off, tmp_path / "third")
    store = TaskStore(db3)
    try:
        [row] = store.handed_off_rows()
        ident = (row.task_id, row.proposal.revision, narrate.proposal_hash(row.proposal))
        start = datetime.now(UTC)
        stale = store.claim_narrative(*ident, owner="crashed", now=start)
        assert stale is not None
        assert store.claim_narrative(*ident, owner="other",
                                     now=start + timedelta(minutes=9, seconds=59)) is None
        later = store.claim_narrative(*ident, owner="other", now=start + timedelta(minutes=10))
        assert later is not None and later.claim_seq > stale.claim_seq
        assert not store.record_narrative(stale, ts.NarrativeOutcome.OK, text="舊的",
                                          source="live", now=start + timedelta(minutes=11))
        assert store.record_narrative(later, ts.NarrativeOutcome.OK, text="新的", source="live",
                                      now=start + timedelta(minutes=11))
        assert not store.record_narrative(later, ts.NarrativeOutcome.OK, text="再一次",
                                          source="live", now=start + timedelta(minutes=12))
    finally:
        store.close()
    assert _status(db3).text == "新的"
    # 模型總期限加 1 分鐘餘裕不小於領取期限時,入口拒絕啟動(不呼叫模型、不領取)
    monkeypatch.setattr(ts, "NARRATIVE_CLAIM_WINDOW", timedelta(minutes=1))
    code = narrate.run(["--db", str(db3), "--ledger", str(tmp_path / "l.sqlite")], environ={},
                       out=_Sink(), err=_Sink())
    assert code == narrate.EXIT_UNSAFE_CONFIG


class _Sink:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text
        return len(text)

    def flush(self):
        pass


def test_the_narrative_output_is_validated_before_it_is_stored(handed_off, tmp_path):
    for bad in ("", "  ", "x" * 501, "有\x07控制字元", "第一行\n第二行", "```說明```\n"):
        (tmp_path / "b").mkdir(exist_ok=True)
        folder = tmp_path / "b" / str(abs(hash(bad)))
        folder.mkdir()
        db = _copy(handed_off, folder)
        [done] = _narrate(db, _gate(folder, live(FakeBackend(reply(bad)))))
        assert done.outcome == "unreadable", repr(bad)
        assert _status(db).text is None and _status(db).outcome == "unreadable"
    ok = tmp_path / "ok"
    ok.mkdir()
    db = _copy(handed_off, ok)
    backend = FakeBackend(lambda call: reply("說明:" + narrate_placeholder(call.user)))
    [done] = _narrate(db, _gate(ok, live(backend)))
    assert done.outcome == "ok" and "t1" in _status(db).text  # 佔位符驗證通過後才換回真實編號
    assert narrate.valid_narrative("x" * 500) and not narrate.valid_narrative("x" * 501)


def narrate_placeholder(user):
    return next(word for word in ("任務甲",) if word in user)


# ---- [S1102](說明命令列那半) ----
def test_recorded_entries_book_into_the_given_ledger(handed_off, tmp_path):  # noqa: PLR0915
    db = _copy(handed_off, tmp_path)
    ledger = tmp_path / "demo-ledger.sqlite"
    out, err = _Sink(), _Sink()
    code = narrate.run(["--db", str(db), "--demo-id", "demo-9", "--ledger", str(ledger),
                        "--recordings-dir", str(tmp_path / "empty")], environ={}, out=out, err=err)
    assert code == narrate.EXIT_OK, err.text
    printed = json.loads(out.text)
    assert printed["mode"] == "recorded"
    assert [n["outcome"] for n in printed["narratives"]] == ["no_recording"]
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            [row] = reader.calls_between("0000", "9999")
    finally:
        reader.close()
    assert (row.caller, row.demo_id, row.source, row.outcome) == (
        "analyzer_narrative", "demo-9", "recorded", "no_recording")
    assert not mc.live_ledger_path().exists()  # 家目錄(測試裡是暫存)的帳沒被建立
    assert not (Path(view.account_home()) / ".rtb").exists()
    recorded_hypotheses_book_into_the_given_ledger(tmp_path)  # 假說命令列那半
    assert not mc.live_ledger_path().exists()
    # 開了 AI 決策的分析端驅動命令列那半(Phase 13 增量 2)
    from tests.analyzer.test_investigation_e2e import recorded_runner_books_into_the_given_ledger

    runner_ledger = recorded_runner_books_into_the_given_ledger(tmp_path)
    reader = view.ModelLedgerView(runner_ledger)
    try:
        with reader.read_transaction():
            rows = reader.calls_between("0000", "9999")
    finally:
        reader.close()
    assert rows and {(r.caller, r.demo_id, r.source, r.outcome) for r in rows} == {
        ("analyzer_investigation", "demo-7", "recorded", "no_recording")}
    assert not mc.live_ledger_path().exists()


def test_the_narrator_refuses_a_missing_database_and_never_creates_one(tmp_path):
    missing = tmp_path / "nothing.db"
    code = narrate.run(["--db", str(missing), "--ledger", str(tmp_path / "l.sqlite")],
                       environ={}, out=_Sink(), err=_Sink())
    assert code == narrate.EXIT_NO_DATABASE and not missing.exists()


def test_the_narrative_outcomes_match_the_model_client_outcomes():
    """分析端資料庫模組不匯入模型用戶端,說明結果類別另存一份封閉列舉;兩邊的值要一樣。"""
    assert {o.value for o in ts.NarrativeOutcome} == {o.value for o in mc.Outcome}
    for outcome in ts.NarrativeOutcome:
        assert modelgate.Outcome(outcome.value).value == outcome.value
