"""Phase 15 增量 2:規則模式探索經分析端窄入口呼叫模型、花費上限與送出邊界(計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈模型建議、機械核對與人讀報告〉〈要改寫的既有合約〉)。

合約 [S1507] [S1510](匯入邊界) [S1514] [S1517] [S1518](匯入邊界)。不呼叫真的 claude、不設行程環境的
即時開關、不碰 ~/.rtb:即時一律是行程內的假後端(`tests/model/fakes.py`),帳本與錄製都在 tmp_path;
預設路徑用真的 `modelgate.open_gate`,它在錄製模式下只讀錄製。
"""

import ast
import dataclasses
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelledger_view as view
from rtb import sqlitekit
from rtb.analyzer import modelgate
from rtb.analyzer import rule_mining_model as rmm
from rtb.eval import rule_mining_baseline as rb
from rtb.eval import rule_mining_check as rc
from rtb.eval import rule_mining_eval as rme
from rtb.eval import rule_mining_history as rh
from rtb.eval import rule_mining_prompt as rp
from rtb.eval import rule_mining_recordings as rr
from rtb.eval import rule_mining_vocab as rv
from tests.analyzer.test_boundaries import _source_of
from tests.conftest import child_prelude
from tests.model.fakes import FakeBackend, live, reply, request
from tests.test_spawn_boundary import (
    CALL_MODEL_USERS,
    CALLER_USERS,
    CALLER_VALUES,
    GATE_USERS,
    SENDING_ENTRIES,
    SRC,
    backend_offenders,
    closure_of,
)

SEED = rv.SEEDS[0]
DEMO = f"phase15-seed-{SEED}-1"
BATCH = "phase15-rule-mining-test"
NARROW = "rtb.analyzer.rule_mining_model"
RUNNER = "rtb.eval.rule_mining_eval"
HISTORY_KEYS = "rtb.eval.rule_mining_recordings"
REPORT = "rtb.eval.rule_mining_report"
# 增量 3:命令列寫在探勘執行器裡(不另開模組);比較與報告是純函式,一併列入探勘閉包檢查
MINING = ("rtb.eval.rule_mining_vocab", "rtb.eval.rule_mining_history",
          "rtb.eval.rule_mining_baseline", "rtb.eval.rule_mining_prompt",
          "rtb.eval.rule_mining_check", HISTORY_KEYS, RUNNER, NARROW, REPORT)


@pytest.fixture(scope="module")
def prepared():
    return rme.prepare(SEED)


def honest_answer(prepared, count=2):
    """照探索側重算數字填的回覆(模型照抄彙總表時的樣子)。"""
    top = rb.top_k({k: prepared.explore.stats(k) for k in rv.all_conditions()})[:count]
    return json.dumps({"version": 1, "suggestions": [
        {"clauses": [{"condition": c, "threshold": t} for c, t in r.key],
         "direction": r.direction, "support": r.support, "counterexample": r.counter,
         "confidence_note": "照表抄"} for r in top]}, ensure_ascii=False, separators=(",", ":"))


def fake_opener(settings, seen=None):
    """代替 modelgate.open_gate:收同樣的參數,回綁好假設定的閘道(記下開閘道時帶的呼叫者)。"""

    def open_gate(environ, *, caller, demo_id, ledger, recordings, **rest):
        del environ
        if seen is not None:
            seen.append(caller)
        return modelgate.Gate(settings, caller, demo_id, ledger or rest.get("recorded_ledger"),
                              recordings, rest.get("batch_id"))

    return open_gate


def config(tmp_path, *, environ=None, demo_id=DEMO, batch_id=None, recordings=None):
    empty = tmp_path / "no-bin"
    empty.mkdir(exist_ok=True)
    return rmm.GateConfig(environ={"PATH": str(empty)} if environ is None else environ,
                          demo_id=demo_id, ledger=tmp_path / "ledger.sqlite",
                          recordings=recordings or tmp_path / "recordings", batch_id=batch_id)


def fake_ask(settings, cfg, seen=None):
    def ask(system, table):
        return rmm.suggest(system, table, cfg, open_gate=fake_opener(settings, seen))

    return ask


def ledger_rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()


def mining_request(prepared, demo_id=DEMO):
    return mc.ModelRequest(caller=mc.Caller.RULE_MINING, system=rp.SYSTEM_PROMPT,
                           user=prepared.table, max_output_tokens=rmm.MAX_OUTPUT_TOKENS,
                           timeout_seconds=rmm.TIMEOUT_SECONDS, demo_id=demo_id)


# ---- [S1507] ----
def test_rule_mining_live_calls_require_the_gateway_and_count_toward_caps(  # noqa: PLR0915
        prepared, tmp_path, monkeypatch):
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    # 預設重播:缺錄製就停在「沒有錄製」、記呼叫失敗,不改走即時;帳記在指定的暫存帳本
    run = rme.run(prepared, rme.gate_ask(config(tmp_path)))
    assert run.status == rme.CALL_FAILED and run.verification is None
    assert (run.reply.outcome, run.reply.mode, run.reply.text) == ("no_recording", "recorded", None)
    assert "錄製" in (run.reply.problem or "")
    assert run.expected_key == rr.expected_key(rp.SYSTEM_PROMPT, prepared.table)
    booked = ledger_rows(tmp_path / "ledger.sqlite")
    assert [(r.caller, r.outcome, r.source, r.reserved_nanousd) for r in booked] == [
        ("rule_mining", "no_recording", "recorded", 0)]
    # 即時前置條件不齊(這個字典開了開關,但找不到 claude,或沒有展示編號):閘道判錄製、停在沒有錄製。
    # 第二種放一支假的 claude 在 PATH 上,被執行就留記號(展示編號先檢查,根本不會跑到它)
    fake_bin, marker = tmp_path / "fake-bin", tmp_path / "fake-claude-ran"
    fake_bin.mkdir()
    (fake_bin / "claude").write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n", encoding="utf-8")
    (fake_bin / "claude").chmod(0o755)
    for environ, demo_id in (({"RTB_MODEL_LIVE": "1", "PATH": str(tmp_path / "no-bin")}, DEMO),
                             ({"RTB_MODEL_LIVE": "1", "PATH": str(fake_bin)}, None)):
        run = rme.run(prepared, rme.gate_ask(config(tmp_path, environ=environ, demo_id=demo_id)))
        assert (run.status, run.reply.mode, run.reply.outcome) == (
            rme.CALL_FAILED, "recorded", "no_recording")
    assert not marker.exists()
    # 錄製模式不給帳本:閘道在入口拒絕,什麼都沒呼叫
    with pytest.raises(modelgate.GateRefused):
        rmm.suggest(rp.SYSTEM_PROMPT, prepared.table, rmm.GateConfig(
            {"PATH": str(tmp_path / "no-bin")}, DEMO, None, rec_dir))

    # 明確授權的即時(假後端)加錄製:經閘道送出合成彙總,呼叫者綁死 RULE_MINING,計入上限
    answer = honest_answer(prepared)
    backend = FakeBackend(reply(answer, input_tokens=3000, output_tokens=400))
    seen = []
    live_cfg = config(tmp_path, batch_id=BATCH)
    run = rme.run(prepared, fake_ask(live(backend, record=True), live_cfg, seen))
    assert seen == [mc.Caller.RULE_MINING]
    assert run.status == rme.CALL_OK and run.reply.source == "live"
    assert len(backend.calls) == 1
    sent = backend.calls[0]
    assert (sent.system, sent.user) == (rp.SYSTEM_PROMPT, prepared.table)
    assert (sent.max_output_tokens, sent.timeout_seconds) == (6144, 60.0)
    assert run.reply.key == run.expected_key  # 歷史錄製鍵模組重算的鍵就是模型用戶端存的鍵
    assert (rec_dir / f"{run.expected_key}.json").is_file()
    assert run.verification is not None
    assert (run.verification.n, run.verification.invalid_count) == (2, 0)
    live_row = ledger_rows(tmp_path / "ledger.sqlite")[-1]
    assert (live_row.caller, live_row.source, live_row.outcome) == ("rule_mining", "live", "ok")
    assert live_row.reserved_nanousd == core.reservation_nanousd(mining_request(prepared),
                                                                 mc.DEFAULT_MODEL)
    spent = ledger_db.used_so_far(tmp_path / "ledger.sqlite", DEMO)
    assert spent.demo_nanousd == live_row.settled_nanousd > 0  # 計入上限的已用
    # 之後的預設重播讀回同一份,答案與核對結果相同
    replay = rme.run(prepared, rme.gate_ask(config(tmp_path, recordings=rec_dir)))
    assert replay.status == rme.CALL_OK and replay.reply.source == "recorded"
    assert replay.reply.text == answer and replay.verification == run.verification

    # 花費帳忙碌:預留寫不進去,沒有送出,記呼叫失敗
    monkeypatch.setattr(ledger_db, "connect", lambda path: sqlitekit.connect(path, 0.05))
    holder = sqlite3.connect(tmp_path / "ledger.sqlite", isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        busy_backend = FakeBackend(reply("不該送出"))
        run = rme.run(prepared, fake_ask(live(busy_backend), config(tmp_path)))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert (run.status, run.reply.outcome) == (rme.CALL_FAILED, "ledger_busy")
    assert busy_backend.calls == [] and run.verification is None
    # 探勘只寫帳本與錄製:沒有別的檔(沒有分析端資料庫、提案或設定)
    written = {p.name for p in tmp_path.iterdir()}
    assert written <= {"no-bin", "fake-bin", "recordings", "ledger.sqlite", "ledger.sqlite-wal",
                       "ledger.sqlite-shm"}, written


# ---- [S1503]/[S1505] 送出路徑(代碼審 r1):執行器送的是探索側建的表,兩個重算器各對一側 ----
def test_rule_mining_runner_sends_the_explore_table_and_recounts_both_sides(prepared):
    history = prepared.history
    split = rh.split(a.ad_id for a in history.ads)
    explore, holdout = rb.summarize(history, split.explore), rb.summarize(history, split.holdout)
    assert prepared.table == rp.summary_table(explore)
    assert prepared.table != rp.summary_table(holdout)
    assert f"B={prepared.prompt_bytes}" in rp.PREFLIGHT_RECORD[0]  # 預檢記的同一個位元組數
    assert prepared.prompt_bytes == rp.preflight_seed(SEED).prompt_bytes
    for key in rv.all_conditions():
        assert prepared.explore.stats(key) == explore.stats[key], key
        assert prepared.holdout.stats(key) == holdout.stats[key], key
    # 端到端:照探索側填的回覆有效,保留側判定用的是保留側的數字
    run = rme.run(prepared, lambda _s, _u: rmm.Reply("ok", "recorded", honest_answer(prepared),
                                                     "recorded", "k", None, 0))
    assert run.verification is not None and run.verification.n == 2
    for checked in run.verification.valid:
        condition, direction = checked.key
        assert checked.holdout == rb.holdout_verdict(holdout.stats[condition], direction)
        assert checked.holdout_stats == holdout.stats[condition]


# ---- [S1504] 錄製重播路徑(代碼審 r1):崩潰型回覆錄進去、重播出來都不崩 ----
def test_rule_mining_hostile_recordings_replay_without_crashing(prepared, tmp_path):
    folder = tmp_path / "recordings"
    nested = "[" * 500 + "]" * 500
    hostile = [
        nested,  # 報告 F1:平衡 500 層
        honest_answer(prepared).replace('"照表抄"', '"\\ud800"', 1),  # 報告 F2 輸入 A
        '{"version":1,"suggestions":[{"clauses":[],"direction":' + nested
        + ',"support":1,"counterexample":0,"confidence_note":"a"}]}',
    ]
    runs = []
    for n, text in enumerate(hostile):
        table = prepared.table + f"\n{n}"  # 每份各一個錄製鍵
        case = dataclasses.replace(prepared, table=table,
                                   prompt_bytes=rmm.prompt_bytes(rp.SYSTEM_PROMPT, table))
        backend = FakeBackend(reply(text, input_tokens=100, output_tokens=100))
        live_run = rme.run(case, fake_ask(live(backend, record=True),
                                          config(tmp_path, batch_id=BATCH, recordings=folder)))
        replayed = rme.run(case, rme.gate_ask(config(tmp_path, recordings=folder)))
        assert replayed.reply.source == "recorded" and replayed.reply.text == text
        assert replayed.verification == live_run.verification
        runs.append(replayed.verification)
    assert [(v.rejected, v.invalid_count) for v in runs] == [
        (rc.TOO_DEEP, 1), (None, 1), (rc.TOO_DEEP, 1)]
    assert [i.reason for i in runs[1].invalid] == [rc.BAD_NOTE]
    # 報告 F2 輸入 B:錄製檔的文字被竄改成孤立代理字元,重播整份拒絕、不崩
    target = next(folder.glob("*.json"))
    data = json.loads(target.read_text(encoding="utf-8"))
    data["text"] = "\ud800"
    target.write_text(json.dumps(data), encoding="utf-8")
    table = next(prepared.table + f"\n{n}" for n in range(3) if rr.expected_key(
        rp.SYSTEM_PROMPT, prepared.table + f"\n{n}") == target.stem)
    case = dataclasses.replace(prepared, table=table,
                               prompt_bytes=rmm.prompt_bytes(rp.SYSTEM_PROMPT, table))
    tampered = rme.run(case, rme.gate_ask(config(tmp_path, recordings=folder)))
    assert tampered.reply.text == "\ud800"
    assert tampered.verification is not None
    assert (tampered.verification.rejected, tampered.verification.invalid_count) == (
        rc.UNPARSABLE, 1)


# ---- [S1514] ----
def test_rule_mining_single_call_respects_reservation(prepared, tmp_path, monkeypatch):
    assert (rmm.MAX_OUTPUT_TOKENS, rmm.TIMEOUT_SECONDS, rmm.PROMPT_BYTES_LIMIT) == (
        rv.MAX_OUTPUT_TOKENS, rv.CALL_TIMEOUT_SECONDS, rv.PROMPT_BYTES_LIMIT) == (6144, 60, 20480)
    # 20480 位元組的完整提示、6144 輸出:repo 的預留函式算得 941875200,每展示 1 美元還剩 58124800
    widest = mc.ModelRequest(caller=mc.Caller.RULE_MINING, system="s" * 480, user="u" * 20000,
                             max_output_tokens=6144, timeout_seconds=60.0, demo_id=DEMO)
    assert core.reservation_nanousd(widest, mc.DEFAULT_MODEL) == 941_875_200
    assert core.DEMO_CAP_NANOUSD - 941_875_200 == 58_124_800
    # 十條填滿(兩子句、24 位元組代碼、九位數筆數、80 個三位元組字)的緊湊回覆仍在 6000 位元組內
    code = "a" * 24
    full = {"clauses": [{"condition": code, "threshold": code}] * 2, "direction": "not_improve",
            "support": 999_999_999, "counterexample": 999_999_999, "confidence_note": "中" * 80}
    widest_reply = json.dumps({"version": 1, "suggestions": [full] * rv.K}, ensure_ascii=False,
                              separators=(",", ":"))
    assert len(widest_reply.encode()) <= rv.REPLY_BYTES_LIMIT <= rv.MAX_OUTPUT_TOKENS
    _oversized_prompts_are_refused_before_the_gate(prepared, tmp_path)
    need = core.reservation_nanousd(mining_request(prepared), mc.DEFAULT_MODEL)
    assert 600_000_000 < need < core.DEMO_CAP_NANOUSD
    _reservation_must_fit_the_remaining_caps(prepared, tmp_path, monkeypatch, need)
    _failures_are_booked_once_without_retry(prepared, tmp_path, need)
    _concurrent_reservations_count_toward_the_cap(prepared, tmp_path, monkeypatch, need)


def _oversized_prompts_are_refused_before_the_gate(prepared, tmp_path):
    """提示過大:評估端與窄入口都在呼叫前拒絕,不開閘道、不記帳;剛好 20480 可以。"""
    opened = []

    def refuse_to_open(*_args, **_kwargs):
        opened.append("refused")

    for pad in (20480 - prepared.prompt_bytes, 20481 - prepared.prompt_bytes):
        table = prepared.table + "x" * pad
        size = rmm.prompt_bytes(rp.SYSTEM_PROMPT, table)
        if size > rmm.PROMPT_BYTES_LIMIT:
            with pytest.raises(rmm.PromptTooLarge):
                rmm.suggest(rp.SYSTEM_PROMPT, table, config(tmp_path), open_gate=refuse_to_open)
            asked = []
            too_big = dataclasses.replace(prepared, table=table, prompt_bytes=size)
            with pytest.raises(rme.PromptRefused):
                rme.run(too_big, lambda _system, user, asked=asked: asked.append(user))
            assert asked == []
        else:
            assert size == 20480
            rmm.suggest(rp.SYSTEM_PROMPT, table, config(tmp_path), open_gate=fake_opener(
                live(FakeBackend(reply("{}")), record=False)))
            opened.append("allowed")
    assert opened == ["allowed"]
    assert not (tmp_path / "recordings").exists()


def _reservation_must_fit_the_remaining_caps(prepared, tmp_path, monkeypatch, need):
    """預留額用 repo 預留函式照當時價目重算,須 ≤ 每展示與每月剩餘上限;無餘裕就拒絕即時呼叫。"""
    ledger = tmp_path / "caps.sqlite"

    def attempt(demo_id):
        backend = FakeBackend(reply("{}", input_tokens=10, output_tokens=10))
        cfg = rmm.GateConfig({}, demo_id, ledger, tmp_path / "recordings")
        return rme.run(prepared, fake_ask(live(backend), cfg)).reply.outcome, backend.calls

    monkeypatch.setattr(core, "DEMO_CAP_NANOUSD", need)  # 剛好放得下
    outcome, calls = attempt("d-exact")
    assert outcome == "ok" and len(calls) == 1
    monkeypatch.setattr(core, "DEMO_CAP_NANOUSD", need - 1)  # 差一
    assert attempt("d-short") == ("local_cap_refused", [])
    monkeypatch.setattr(core, "DEMO_CAP_NANOUSD", need)
    price = core.PRICES[mc.DEFAULT_MODEL]
    doubled = dataclasses.replace(price, output_nanousd=2 * price.output_nanousd)
    monkeypatch.setitem(core.PRICES, mc.DEFAULT_MODEL, doubled)  # 價目一變,同一上限就放不下
    assert attempt("d-price") == ("local_cap_refused", [])
    monkeypatch.undo()
    # 展示已用額(別的計入上限呼叫者)使重算預留超過剩餘 → 拒絕;每月上限同理
    spent_before = core.DEMO_CAP_NANOUSD - need + 1
    output_tokens = -(-spent_before * 5 // 6 // core.PRICES[mc.DEFAULT_MODEL].output_nanousd)
    mc.call_model(request("先花", demo_id="d-used", max_output_tokens=50),
                  live(FakeBackend(reply("x", input_tokens=0, output_tokens=output_tokens))),
                  recordings_dir=tmp_path / "recordings", ledger=ledger)
    assert ledger_db.used_so_far(ledger, "d-used").demo_nanousd >= spent_before
    assert attempt("d-used") == ("local_cap_refused", [])
    monkeypatch.setattr(core, "MONTH_CAP_NANOUSD", ledger_db.used_so_far(
        ledger, None).month_nanousd + need - 1)
    assert attempt("d-month") == ("local_cap_refused", [])
    monkeypatch.undo()


def _failures_are_booked_once_without_retry(prepared, tmp_path, need):
    """60 秒逾時:記呼叫失敗、整筆預留入帳、不重試;暫時性錯誤同樣只送一次。"""
    for failure, outcome in ((mc.ModelTimeout("逾時"), "timeout"),
                             (mc.TransientServiceError("overloaded"), "transient")):
        backend = FakeBackend(failure, reply("第二次不該發生"))
        cfg = rmm.GateConfig({}, f"d-{outcome}", tmp_path / "fail.sqlite", tmp_path / "recordings")
        run = rme.run(prepared, fake_ask(live(backend), cfg))
        assert (run.status, run.reply.outcome, len(backend.calls)) == (rme.CALL_FAILED, outcome, 1)
        row = ledger_rows(tmp_path / "fail.sqlite")[-1]
        assert row.outcome == outcome and row.settled_nanousd == row.reserved_nanousd == need
        assert row.by_reservation


def _concurrent_reservations_count_toward_the_cap(prepared, tmp_path, monkeypatch, need):
    """併行預留也計入上限:上限放得下兩筆時,四條同時送,至多兩條送出。"""
    release = threading.Event()

    def slow(_call):
        release.wait(30)
        return reply("{}", input_tokens=0, output_tokens=0)

    monkeypatch.setattr(core, "DEMO_CAP_NANOUSD", 2 * need + need // 2)
    slow_backend, outcomes = FakeBackend(slow), []
    barrier = threading.Barrier(4)
    cfg = rmm.GateConfig({}, "d-race", tmp_path / "race.sqlite", tmp_path / "recordings")

    def worker():
        barrier.wait()
        outcomes.append(rme.run(prepared, fake_ask(live(slow_backend), cfg)).reply.outcome)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 30
    while len(slow_backend.calls) + len(outcomes) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    in_flight = ledger_db.used_so_far(tmp_path / "race.sqlite", "d-race").demo_nanousd
    release.set()
    for thread in threads:
        thread.join(10)
    assert len(outcomes) == 4 and len(slow_backend.calls) <= 2
    assert set(outcomes) <= {"ledger_busy", "local_cap_refused", "ok"}
    assert in_flight == len(slow_backend.calls) * need <= core.DEMO_CAP_NANOUSD


# ---- [S1510] ----
FORBIDDEN_PREFIXES = ("rtb.dsp", "rtb.executor", "rtb.ops", "rtb.demo")
FORBIDDEN_MODULES = frozenset({
    "rtb.analyzer.runner", "rtb.analyzer.inbox_client", "rtb.analyzer.dsp_client",
    "rtb.analyzer.rule_round", "rtb.analyzer.narrate", "rtb.analyzer.ai_judge",
    "rtb.analyzer.instrumented", "rtb.httpclient", "rtb.httpkit", "rtb.capabilitykit"})
NETWORK_ROOTS = frozenset({"urllib", "http", "socket", "ssl", "smtplib", "ftplib", "xmlrpc",
                           "requests", "httpx", "aiohttp", "webbrowser", "email"})
# 探勘模組各自准直接取用的專案模組與名字(寫死;取用的名字不含任何決策、提案、寫入或送件)
MINING_USES = {
    "rtb.analyzer.investigation": {"canonical_json"},
    "rtb.eval.scoring": {"wilson_lower"},
    "rtb.analyzer.modelgate": {"Caller", "Gate", "ModelCallFailed", "Outcome", "open_gate"},
    # 增量 3:歷史錄製鍵模組多取入庫根、模式開關名(只讀,給重播拿掉即時開關)與錄製檔的共用驗收
    "rtb.modelclient": {"Caller", "recording_key", "DEFAULT_MODEL", "default_recordings_dir",
                        "MODEL_ENV", "LIVE_ENV", "RECORD_ENV", "Outcome", "batch_file_problems",
                        "recording_files", "validated", "NoRecording",
                        # 代碼審 r2:唯讀查帳號家目錄花費帳裡用過的展示編號(花費帳既有的唯讀開法)
                        "ModelLedgerView"},
}
MINING_SOURCES = ("rtb.eval.rule_mining", NARROW, "rtb.domain.metrics")


def forbidden_reach(roots, src=SRC):
    """閉包裡碰到的禁區:送件、DSP、執行端、分析端驅動等專案模組,以及任一支閉包模組匯入的網路模組。"""
    reached = closure_of(list(roots), src)
    found = [m for m in reached if m in FORBIDDEN_MODULES or m.startswith(FORBIDDEN_PREFIXES)]
    for module in reached:
        path = _source_of(src, module)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        found += [f"{module}: {n}" for n in names if n.split(".")[0] in NETWORK_ROOTS]
    return sorted(found)


def _module_path(module, src=SRC):
    return src.joinpath(*module.split(".")).with_suffix(".py")


def foreign_uses(module, src=SRC):
    """探勘模組從探勘家族以外的專案模組取用的名字,超出 MINING_USES 的就列出。"""
    tree = ast.parse(_module_path(module, src).read_text(encoding="utf-8"))
    aliases, found = {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"{module}: import {a.name}" for a in node.names if a.name.startswith("rtb")]
        if not (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("rtb")):
            continue
        for alias in node.names:
            full = f"{node.module}.{alias.name}"
            if full.startswith(MINING_SOURCES) or str(node.module).startswith(MINING_SOURCES):
                continue
            if full in MINING_USES:
                aliases[alias.asname or alias.name] = full
            elif alias.name not in MINING_USES.get(str(node.module), ()):
                found.append(f"{module}: from {node.module} import {alias.name}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (
                node.value.id in aliases and node.attr not in MINING_USES[aliases[node.value.id]]):
            found.append(f"{module}:{node.lineno} {aliases[node.value.id]}.{node.attr}")
    return found


def test_rule_mining_cannot_write_policy_proposals_or_issues(tmp_path):
    # 探勘的匯入閉包(含評估端執行器與分析端窄入口)碰不到送件、DSP、執行端、分析端驅動或網路
    assert forbidden_reach(MINING) == []
    assert "rtb.modelclaude" in closure_of([NARROW])  # 只經模型用戶端(唯一起 claude 的地方)
    # 探勘模組取用別的模組只准寫死的幾個名字(不呼叫流程推進、決策規則、提案、寫入或送件)
    offenders = [line for module in MINING for line in foreign_uses(module)]
    assert offenders == []
    # 窄入口只匯入模型閘道
    tree = ast.parse(_module_path(NARROW).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert {m for m in imported if m.startswith("rtb")} == {"rtb.analyzer"}
    assert [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            and n.module == "rtb.analyzer" for a in n.names] == ["modelgate"]
    # 殺傷力:探勘模組多匯入收件口、DSP 用戶端或網路,都抓得到;多取用閘道以外的名字也抓得到
    shadow = tmp_path / "src"
    shutil.copytree(SRC, shadow)
    target = _module_path(RUNNER, shadow)
    for probe in ("from rtb.analyzer import inbox_client\n", "import rtb.dsp.store\n",
                  "import urllib.request\n", "from rtb.analyzer import runner\n"):
        target.write_text(probe + _module_path(RUNNER).read_text(encoding="utf-8"),
                          encoding="utf-8")
        assert forbidden_reach([RUNNER], shadow), probe
    target.write_text(_module_path(RUNNER).read_text(encoding="utf-8")
                      + "\nfrom rtb.analyzer import investigation as _inv\n_inv.Progress\n",
                      encoding="utf-8")
    assert foreign_uses(RUNNER, shadow)


# ---- [S1517] ----
def test_rule_mining_retirement_preserves_recordings_and_caps(prepared, tmp_path):  # noqa: PLR0915
    # 呼叫者列舉、上限集合、錄製鍵對照:值與成員名寫死、鍵集合等於列舉成員名
    assert mc.Caller.RULE_MINING.value == "rule_mining"
    assert mc.Caller.RULE_MINING in ledger_db.CAPPED_CALLERS
    assert set(CALLER_USERS) == {c.name for c in mc.Caller}
    assert CALLER_VALUES["rule_mining"] == "RULE_MINING"
    # 撤除模型段只撤兩支送出入口;歷史錄製鍵模組留在 CALLER_USERS["RULE_MINING"],這格不會變空
    retired = {NARROW, RUNNER}
    assert retired <= SENDING_ENTRIES and retired <= CALL_MODEL_USERS
    assert NARROW in GATE_USERS
    assert CALLER_USERS["RULE_MINING"] - retired == {HISTORY_KEYS}
    assert HISTORY_KEYS not in SENDING_ENTRIES | CALL_MODEL_USERS
    history_tree = ast.parse(_module_path(HISTORY_KEYS).read_text(encoding="utf-8"))
    assert backend_offenders(history_tree, HISTORY_KEYS, HISTORY_KEYS) == []
    assert not closure_of([HISTORY_KEYS]) & (retired | {"rtb.analyzer.modelgate"})

    # 先錄一批(假後端)並記帳
    folder, ledger = tmp_path / "recordings", tmp_path / "ledger.sqlite"
    answer = reply(honest_answer(prepared), input_tokens=0, output_tokens=30_000)
    run = rme.run(prepared, fake_ask(live(FakeBackend(answer), record=True),
                                     config(tmp_path, batch_id=BATCH, recordings=folder)))
    assert run.status == rme.CALL_OK
    spent = ledger_db.used_so_far(ledger, DEMO).demo_nanousd
    assert spent > 0
    table_file = tmp_path / "table.txt"
    table_file.write_text(prepared.table, encoding="utf-8")
    # 演練撤除:原始碼副本刪掉兩支送出入口,另開行程只用留下的模組讀回舊錄製、重算鍵、查上限
    shadow = tmp_path / "shadow"
    shutil.copytree(SRC, shadow)
    for module in retired:
        _module_path(module, shadow).unlink()
    script = child_prelude(tmp_path / "home") + f"""
import json
from pathlib import Path
from rtb import modelclient as mc, modelledger as ledger_db, modelrecording as rec
from rtb.eval import rule_mining_prompt as rp, rule_mining_recordings as rr
for gone in ({NARROW!r}, {RUNNER!r}):
    try:
        __import__(gone)
    except ModuleNotFoundError:
        pass
    else:
        raise SystemExit("撤除後還匯入得到 " + gone)
table = Path({str(table_file)!r}).read_text(encoding="utf-8")
key = rr.expected_key(rp.SYSTEM_PROMPT, table)
found = rec.load_recording(Path({str(folder)!r}) / f"{{key}}.json", key=key,
                           caller=mc.Caller.RULE_MINING, model=mc.DEFAULT_MODEL)
print(json.dumps({{"key": key, "outcome": found.outcome, "caller": found.caller,
                  "demo": ledger_db.used_so_far(Path({str(ledger)!r}), {DEMO!r}).demo_nanousd,
                  "capped": mc.Caller.RULE_MINING in ledger_db.CAPPED_CALLERS}}))
"""
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          timeout=60, check=False,
                          env={**os.environ, "PYTHONPATH": str(shadow)})
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen == {"key": run.expected_key, "outcome": "ok", "caller": "rule_mining",
                    "demo": spent, "capped": True}
    # 舊帳照樣計上限:同一展示再來一筆計入上限的呼叫,會被已用的探勘花費擋下
    other = request("評估", demo_id=DEMO, max_output_tokens=12_000)
    need = core.reservation_nanousd(other, mc.DEFAULT_MODEL)
    assert need <= core.DEMO_CAP_NANOUSD < spent + need  # 單獨放得下,加上探勘舊帳就放不下
    blocked = FakeBackend(reply("不該送出"))
    with pytest.raises(mc.LocalCapRefused):
        mc.call_model(other, live(blocked), recordings_dir=tmp_path / "other", ledger=ledger)
    assert blocked.calls == []
    # 舊錄製照樣能在錄製模式重播(不經即時)
    replay = rme.run(prepared, rme.gate_ask(config(tmp_path, recordings=folder)))
    assert replay.reply.source == "recorded" and replay.reply.key == run.expected_key


# ---- [S1518] ----
def test_rule_mining_waits_for_phase14_and_keeps_import_boundaries():
    from rtb.demo import driver
    from tests.eval import test_model_candidate as candidate

    # Phase 14 增量 3 已合入:分析端驅動沒有 AI 判斷開關、展示驅動不再組它
    runner_tree = ast.parse((SRC / "rtb" / "analyzer" / "runner.py").read_text(encoding="utf-8"))
    flags = {a.value for n in ast.walk(runner_tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "add_argument" for a in n.args
             if isinstance(a, ast.Constant)}
    assert flags == {"--db", "--dsp-url", "--inbox-url", "--timeout-seconds",
                     "--interval-seconds", "--owner"}
    assert "--ai-judge" not in driver.World.analyzer_args.__code__.co_consts
    # AI 決策模組只給評估執行器;探勘閉包碰不到它、分析端驅動、收件口或 DSP
    judge_users = set()
    for path in sorted((SRC / "rtb").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            for a in n.names} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                                 for a in n.names}
        if "rtb.analyzer.ai_judge" in names:
            judge_users.add(".".join(path.relative_to(SRC).with_suffix("").parts))
    assert judge_users == {"rtb.eval.investigation_eval"}
    assert forbidden_reach(MINING) == []
    # 精確集合隨新列舉與窄入口同步更新(只加窄入口與探勘模組,不放寬整個套件)
    assert {c.value for c in mc.Caller} >= {"rule_mining"}
    assert {"rtb.analyzer.narrate", "rtb.analyzer.ai_judge", NARROW} == GATE_USERS
    assert {m for m in candidate.PHASE15_ALLOWED if not m.startswith("rtb.eval.")} == {NARROW}
    assert candidate.RULE_MINING_MODEL == NARROW
    assert candidate.rule_mining_senders() == ({"rule_mining_eval"}, {"rule_mining_eval"})
    # 匯入探勘執行器或窄入口就算送出點:既有評估模組匯入它們會被擋
    for probe in ("from rtb.eval import rule_mining_eval\n", "import rtb.eval.rule_mining_eval\n",
                  "from rtb.analyzer import rule_mining_model\n",
                  "from rtb.analyzer.rule_mining_model import suggest\n"):
        assert backend_offenders(ast.parse(probe), "scoring.py", "rtb.eval.scoring"), probe
    # 各層的靜態檢查規則也禁匯入窄入口(評估端除外)
    rtb = SRC / "rtb"
    for config_file, where in ((rtb / "analyzer" / "ruff.toml", rtb / "analyzer"),
                               (rtb / "demo" / "ruff.toml", rtb / "demo"),
                               (rtb / "dsp" / "ruff.toml", rtb / "dsp"),
                               (rtb / "executor" / "ruff.toml", rtb / "executor"),
                               (rtb / "domain" / "ruff.toml", rtb / "domain"),
                               (SRC.parent / "pyproject.toml", rtb)):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config_file),
             "--stdin-filename", str(where / "probe.py"), "-"],
            input="from rtb.analyzer import rule_mining_model\n", capture_output=True, text=True,
            timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, config_file
