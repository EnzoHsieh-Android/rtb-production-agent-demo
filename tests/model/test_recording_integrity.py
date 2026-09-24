"""Phase 11B 增量 1 代碼審第 1 輪:錄製與模型用戶端例外的補強([S931] 周邊)。

- 寫錄製檔失敗、花費帳永久錯誤這類不是模型用戶端的例外,一律包成九類之一(暫時性、無法可靠分類),
  帶上已算好的結算狀態與原價;錄製失敗另立類別,保留已拿到的回應。
- 呼叫前就用獨佔建立佔住錄製檔名:別的批次同時錄同一個鍵,在呼叫前就拒絕;懸空的符號連結也算已存在。
- 錄製檔存工具使用、無法可靠分類與結算狀態,重播時還原;載入時驗鍵、呼叫者、模型與欄位型別。
不呼叫真的 claude。
"""

import json
import os
import threading

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelrecording as rec
from tests.model.fakes import (
    FakeBackend,
    claude_json,
    fake_claude,
    live,
    recorded,
    reply,
    request,
)


def test_a_failed_recording_write_is_a_model_failure_that_keeps_the_answer(tmp_path):
    """檔名佔到了、呼叫也回來了,才寫不進錄製檔(呼叫途中目錄變成唯讀)。"""
    recordings = tmp_path / "rec"
    recordings.mkdir()

    def answer_then_lock(_call):
        recordings.chmod(0o500)  # 寫不進去
        return reply("答案")

    try:
        with pytest.raises(mc.ModelCallFailed) as failed:
            mc.call_model(request(batch_id="b1"), live(FakeBackend(answer_then_lock), record=True),
                          recordings_dir=recordings, ledger=tmp_path / "l.sqlite")
    finally:
        recordings.chmod(0o700)
    error = failed.value
    assert error.outcome is mc.Outcome.TRANSIENT and error.unclassified
    assert not isinstance(error, mc.ConfigError)  # 已經呼叫了:不是「確定沒呼叫」那類
    assert error.settlement is mc.SettlementState.SETTLED and error.list_nanousd > 0
    assert error.result is not None and error.result.text == "答案"  # 拿到的回應留著
    # 一開始就佔不到檔名(目錄唯讀):確定沒呼叫,才是設定錯誤
    backend = FakeBackend(reply())
    recordings.chmod(0o500)
    try:
        with pytest.raises(mc.ConfigError):
            mc.call_model(request("other", batch_id="b1"), live(backend, record=True),
                          recordings_dir=recordings, ledger=tmp_path / "l.sqlite")
    finally:
        recordings.chmod(0o700)
    assert backend.calls == []


def test_a_permanent_ledger_error_is_a_model_failure(tmp_path):
    ledger = tmp_path / "ledger-is-a-directory"
    ledger.mkdir()
    with pytest.raises(mc.ModelCallFailed) as failed:
        mc.call_model(request(), live(FakeBackend(reply())), recordings_dir=tmp_path,
                      ledger=ledger)
    assert failed.value.outcome is mc.Outcome.TRANSIENT and failed.value.unclassified
    assert failed.value.settlement is None  # 還沒預留


def test_the_recording_name_is_claimed_before_calling(tmp_path):
    """兩個批次同時錄同一個鍵:佔到檔名的那個呼叫,另一個在呼叫前就被拒絕、不花額度。"""
    release, calls = threading.Event(), []

    def slow(call):
        calls.append(call)
        release.wait(5)
        return reply("答案")

    backend = FakeBackend(slow)
    outcomes = []

    def worker(batch):
        try:
            outcomes.append(mc.call_model(request(batch_id=batch), live(backend, record=True),
                                          recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite"))
        except mc.ModelCallFailed as failed:
            outcomes.append(failed)

    first = threading.Thread(target=worker, args=("b1",))
    first.start()
    while not calls:
        pass
    worker("b2")  # 第一個還在等回應:第二個批次要在呼叫前就被拒絕
    release.set()
    first.join(5)
    assert len(calls) == 1
    assert any(isinstance(o, mc.RecordingConflict) for o in outcomes)
    booked = [r for r in _rows(tmp_path / "l.sqlite") if r.source == "live"]
    assert len(booked) == 1


def test_a_dangling_symlink_counts_as_an_existing_recording(tmp_path):
    backend = FakeBackend(reply())
    key = rec.recording_key(mc.Caller.EVAL_CANDIDATE, mc.DEFAULT_MODEL, "固定系統提示", "hello",
                            50)
    os.symlink(tmp_path / "nowhere.json", tmp_path / f"{key}.json")
    with pytest.raises(mc.NoRecording):  # 讀的那一方也當「存在、但不是正常的錄製檔」
        rec.load_recording(tmp_path / f"{key}.json", key=key, caller=mc.Caller.EVAL_CANDIDATE,
                           model=mc.DEFAULT_MODEL)
    with pytest.raises(mc.ModelCallFailed):
        mc.call_model(request(batch_id="b1"), live(backend, record=True),
                      recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")
    assert backend.calls == []


KNOWN = (cc.ErrorSample("result", "overloaded", mc.Outcome.TRANSIENT, "overloaded"),)


def test_replays_keep_the_flags_and_settlement(tmp_path, monkeypatch):
    """即時時的工具使用、無法可靠分類、結算狀態寫進錄製檔,重播時還原(重播停的位置跟即時一樣)。"""
    monkeypatch.setattr(cc, "KNOWN_ERRORS", KNOWN)
    cases = {"tool": claude_json("overloaded", is_error=True, num_turns=3),
             "odd": claude_json("???", is_error=True)}
    for name, output in cases.items():
        script = fake_claude(tmp_path / name, output, code=1)
        with pytest.raises(mc.ModelCallFailed) as live_failure:
            mc.call_model(request(name, batch_id="b1"),
                          live(cc.ClaudeCodeBackend(script), record=True),
                          recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")
        with pytest.raises(mc.ModelCallFailed) as replayed:
            mc.call_model(request(name), recorded(), recordings_dir=tmp_path,
                          ledger=tmp_path / "l.sqlite")
        for flag in ("tool_use", "unclassified"):
            assert getattr(replayed.value, flag) == getattr(live_failure.value, flag), (name, flag)
    assert live_failure.value.unclassified  # 最後一個是認不出的錯誤
    stored = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("*.json")]
    assert all({"tool_use", "unclassified", "settlement"} <= set(s) for s in stored)


def test_a_tampered_recording_is_not_trusted(tmp_path):
    mc.call_model(request(batch_id="b1"), live(FakeBackend(reply("答案")), record=True),
                  recordings_dir=tmp_path, ledger=tmp_path / "l.sqlite")
    [path] = list(tmp_path.glob("*.json"))
    original = json.loads(path.read_text(encoding="utf-8"))
    for field, value in (("key", "0" * 64), ("caller", "ops_hypothesis"),
                         ("model", "claude-other"), ("input_tokens", "many")):
        path.write_text(json.dumps({**original, field: value}), encoding="utf-8")
        with pytest.raises(mc.NoRecording):
            mc.call_model(request(), recorded(), recordings_dir=tmp_path,
                          ledger=tmp_path / "l.sqlite")


def _rows(ledger):
    from rtb import modelledger_view as view

    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()

