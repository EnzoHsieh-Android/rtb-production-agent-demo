"""Phase 13 增量 1:共用模型入口(分析端模型閘道)與花費帳的改寫。

計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]〈共用模型入口與行程〉〈花費帳與採用判定〉:合約
[S1101]、
[S1103]、[S1134]、[S1154],以及增量 1 範圍裡的兩支模型用戶端共用函式(登入預檢、開錄前目錄檢查;它們的
合約 [S1142]、[S1160]、[S1165] 在增量 2、3 接上 runner 與評估執行器時綁)。Phase 11B 的 [S902]、
[S903]
照 Phase 13 改寫後的補強也在這裡。即時呼叫一律用行程內的假後端或假的 claude 腳本,不呼叫真的 claude。
"""

import ast
import inspect
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger as ledger_db
from rtb import modelledger_view as view
from rtb import modelrecording as rec_module
from rtb.analyzer import modelgate
from rtb.ops import metrics
from tests.conftest import child_prelude
from tests.model.fakes import (
    FakeBackend,
    fake_claude,
    invocations,
    live,
    recorded,
    reply,
    request,
    write_verification,
)
from tests.ops.rows import TENANTS, Rows
from tests.test_spawn_boundary import backend_offenders

SRC = Path(__file__).resolve().parents[2] / "src"
USD = mc.NANOUSD_PER_USD
PHASE13_CALLERS = ("INVESTIGATION", "NARRATIVE", "HYPOTHESIS")  # 本計劃的三個呼叫者(不計入上限)


@pytest.fixture
def dirs(tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    return recordings, tmp_path / "ledger.sqlite"


def call(settings, req, dirs):
    recordings, ledger = dirs
    return mc.call_model(req, settings, recordings_dir=recordings, ledger=ledger)


def rows(ledger):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.calls_between("0000", "9999")
    finally:
        reader.close()


def demo_used(ledger, demo_id):
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            return reader.used_by_demo(demo_id)
    finally:
        reader.close()


# ---- [S1103] ----
def test_the_investigation_caller_is_a_bounded_label(dirs, tmp_path):
    assert mc.Caller.INVESTIGATION.value == "analyzer_investigation"
    assert {c.value for c in mc.Caller} == {"eval_candidate", "ops_hypothesis",
                                           "analyzer_narrative", "live_verification",
                                           "analyzer_investigation"}
    call(live(FakeBackend(reply("x"))), request(caller=mc.Caller.INVESTIGATION), dirs)
    call(live(FakeBackend(reply("y"))), request("u2", caller=mc.Caller.NARRATIVE), dirs)
    data = Rows(tmp_path)
    try:
        now = datetime.now(UTC)
        report = metrics.collect_window(
            now - timedelta(hours=1), now + timedelta(hours=1), executor_db=data.executor_db,
            analyzer_db=data.analyzer_db, tenants=TENANTS, model_ledger=dirs[1])
    finally:
        data.close()
    callers = {s.label_map()["model_caller"] for s in report.samples if s.name == "model_and_jev"}
    assert callers == {"analyzer_investigation", "analyzer_narrative"}
    assert callers <= {c.value for c in mc.Caller} | {metrics.OTHER}  # 標籤是封閉集合


# ---- [S1134] ----
def test_the_phase13_callers_are_not_capped_but_still_booked(dirs, monkeypatch):
    capped = frozenset({mc.Caller.EVAL_CANDIDATE, mc.Caller.VERIFICATION})
    assert capped == ledger_db.CAPPED_CALLERS
    before = {table: tuple(columns) for table, columns in view.TABLES.items()}
    # 上限縮到一次都放不下:三個呼叫者照樣送出、照樣各記一筆估算成本
    monkeypatch.setattr(core, "DEMO_CAP_NANOUSD", 1)
    monkeypatch.setattr(core, "MONTH_CAP_NANOUSD", 1)
    callers = [mc.Caller[name] for name in PHASE13_CALLERS]
    for n, caller in enumerate(callers):
        backend = FakeBackend(reply("說明", input_tokens=100, output_tokens=10))
        result = call(live(backend), request(f"u{n}", caller=caller), dirs)
        assert result.source is mc.Source.LIVE and len(backend.calls) == 1
    booked = rows(dirs[1])
    assert [r.caller for r in booked] == [c.value for c in callers]
    assert all(r.outcome == "ok" and r.settled_nanousd > 0 for r in booked)
    # 計入上限的呼叫者照樣被擋
    blocked = FakeBackend(reply("不該送出"))
    with pytest.raises(mc.LocalCapRefused):
        call(live(blocked), request("e", caller=mc.Caller.EVAL_CANDIDATE), dirs)
    assert blocked.calls == []
    monkeypatch.undo()
    # 不計入的呼叫者花再多,也不把計入上限的呼叫者推過上限
    for n in range(25):
        backend = FakeBackend(reply("x", input_tokens=0, output_tokens=100_000))
        call(live(backend), request(f"big{n}", caller=mc.Caller.NARRATIVE,
                                     max_output_tokens=32_000), dirs)
    assert sum(r.effective_nanousd for r in rows(dirs[1])) > 20 * USD
    assert ledger_db.used_so_far(dirs[1], "demo-1").month_nanousd < 1 * USD
    allowed = FakeBackend(reply("照樣送出"))
    call(live(allowed), request("e2", caller=mc.Caller.EVAL_CANDIDATE), dirs)
    assert len(allowed.calls) == 1
    # 花費帳不加欄位
    assert {table: tuple(columns) for table, columns in view.TABLES.items()} == before
    assert "caller" in view.RESERVATION_COLUMNS and len(view.RESERVATION_COLUMNS) == 14


# ---- [S902]、[S903] 照 Phase 13 改寫:只加總計入上限的呼叫者 ----
def test_the_caps_count_only_the_capped_callers(dirs):
    big = request(demo_id="demo-1", max_output_tokens=3_000)
    per_call = core.reservation_nanousd(big, mc.DEFAULT_MODEL)
    # 不計入的呼叫者先把這次展示用到遠超過 1 美元
    for n in range(5):
        call(live(FakeBackend(reply("x", input_tokens=0, output_tokens=30_000))),
             request(f"n{n}", caller=mc.Caller.HYPOTHESIS, max_output_tokens=32_000), dirs)
    assert demo_used(dirs[1], "demo-1") > 1 * USD
    assert ledger_db.used_so_far(dirs[1], "demo-1").demo_nanousd == 0
    # 計入上限的評估候選照樣只受自己的已用管:花到 0.72 美元後,小的照樣過、0.3 美元的擋下
    assert per_call > 280_000_000
    for n in range(2):
        call(live(FakeBackend(reply("x", input_tokens=0, output_tokens=30_000))),
             request(f"e{n}", demo_id="demo-1", max_output_tokens=3_000), dirs)
    assert ledger_db.used_so_far(dirs[1], "demo-1").demo_nanousd == 720_000_000
    call(live(FakeBackend(reply("小的"))), request("small", demo_id="demo-1"), dirs)
    blocked = FakeBackend(reply("不該送出"))
    with pytest.raises(mc.LocalCapRefused):
        call(live(blocked), big, dirs)
    assert blocked.calls == []


# ---- [S1101] ----
def test_the_ledger_sums_one_demo_including_open_reservations(dirs):
    settled = call(live(FakeBackend(reply("a", input_tokens=10, output_tokens=5))),
                   request("a"), dirs)
    assert settled.source is mc.Source.LIVE
    with pytest.raises(KeyboardInterrupt):  # 呼叫途中被打斷:預留還沒結算
        call(live(FakeBackend(KeyboardInterrupt())), request("b", max_output_tokens=500), dirs)
    call(live(FakeBackend(reply("c", input_tokens=7, output_tokens=3))),
         request("c", caller=mc.Caller.NARRATIVE), dirs)  # 不計入上限的呼叫者也算這次展示花的
    call(live(FakeBackend(reply("d", input_tokens=9, output_tokens=9))),
         request("d", demo_id="demo-2"), dirs)
    booked = rows(dirs[1])
    open_one = next(r for r in booked if r.outcome is None)
    assert open_one.reserved_nanousd == core.reservation_nanousd(
        request("b", max_output_tokens=500), mc.DEFAULT_MODEL)
    expected = sum(r.effective_nanousd for r in booked if r.demo_id == "demo-1")
    assert expected == sum(r.settled_nanousd or r.reserved_nanousd for r in booked
                           if r.demo_id == "demo-1")
    assert demo_used(dirs[1], "demo-1") == expected
    assert demo_used(dirs[1], "demo-2") == booked[-1].settled_nanousd
    assert demo_used(dirs[1], "nobody") == 0


# ---- [S1154] ----
def test_the_stop_exception_reaches_the_runner_without_touching_the_backend(tmp_path):
    assert mc.CallTerminated is cc.CallTerminated
    assert modelgate.CallTerminated is mc.CallTerminated
    assert issubclass(modelgate.CallTerminated, BaseException)
    assert not issubclass(modelgate.CallTerminated, Exception)
    for module in ("rtb.analyzer.modelgate", "rtb.analyzer.runner"):
        path = SRC.joinpath(*module.split(".")).with_suffix(".py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert backend_offenders(tree, module, module) == [], module
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                     for a in n.names}
        assert not imported & {"rtb.modelclaude", "rtb.modelverify"}, module
    # 真的收到停止訊號:經閘道呼叫的模型在途中被 SIGTERM,呼叫端接得到閘道轉手的那個例外
    script = fake_claude(tmp_path / "bin", sleep=30)
    home = view.account_home()
    write_verification()
    code = child_prelude(home) + (
        "import os, signal, sys, threading, time\n"
        "from pathlib import Path\n"
        "from rtb.analyzer import modelgate\n"
        f"log = Path({str(script.with_name('claude.log'))!r})\n"
        "def later():\n"
        "    while not list(log.glob('*.started')):\n"
        "        time.sleep(0.02)\n"
        "    os.kill(os.getpid(), signal.SIGTERM)\n"
        "threading.Thread(target=later, daemon=True).start()\n"
        f"environ = {{'RTB_MODEL_LIVE': '1', 'PATH': {str(script.parent)!r}, "
        f"'HOME': {str(home)!r}}}\n"
        "gate = modelgate.open_gate(environ, caller=modelgate.Caller.NARRATIVE, demo_id='demo-1', "
        "ledger=None, "
        f"recordings=Path({str(tmp_path / 'rec')!r}))\n"
        "assert gate.mode is modelgate.Mode.LIVE, gate.notices\n"
        "try:\n"
        "    gate.complete('s', 'u', max_output_tokens=10, "
        "timeout_seconds=20.0)\n"
        "except modelgate.CallTerminated:\n"
        "    print('stopped')\n"
        "    sys.exit(3)\n")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 3 and result.stdout.strip() == "stopped", (
        result.stdout + result.stderr)
    assert len(invocations(script)) == 1


# ---- 模型閘道:模式、帳檔、錄製目錄、claude 路徑都在這裡判 ----
def test_the_gate_decides_mode_ledger_and_recordings_once(tmp_path, dirs):
    recordings, ledger = dirs
    call(live(FakeBackend(reply("錄好的")), record=True),
         request("問題", caller=mc.Caller.NARRATIVE, batch_id="b1"), dirs)
    gate = modelgate.open_gate({}, caller=modelgate.Caller.NARRATIVE, demo_id="demo-1",
                               ledger=ledger, recordings=recordings)
    assert gate.mode is modelgate.Mode.RECORDED and gate.ledger == ledger
    # 呼叫者在開閘道時綁死,送出時不收(代碼審 r2)
    assert "caller" not in inspect.signature(modelgate.Gate.complete).parameters
    assert gate.caller is modelgate.Caller.NARRATIVE
    result = gate.complete("固定系統提示", "問題", max_output_tokens=50,
                       timeout_seconds=5.0)
    assert (result.text, result.source) == ("錄好的", mc.Source.RECORDED)
    assert rows(ledger)[-1].demo_id == "demo-1"
    # 沒給錄製目錄就用專案根的預設;沒給帳檔就是帳號家目錄那一本
    narrative = modelgate.Caller.NARRATIVE
    default = modelgate.open_gate({}, caller=narrative, demo_id=None, ledger=None, recordings=None)
    assert default.recordings == mc.default_recordings_dir()
    assert default.ledger == mc.live_ledger_path()
    # 即時模式的帳寫死家目錄那一本:給了 --ledger 就拒絕
    script = fake_claude(tmp_path / "bin")
    write_verification()
    environ = {"RTB_MODEL_LIVE": "1", "PATH": str(script.parent)}
    with pytest.raises(modelgate.GateRefused, match="家目錄"):
        modelgate.open_gate(environ, caller=narrative, demo_id="demo-1", ledger=ledger,
                            recordings=recordings)
    live_gate = modelgate.open_gate(environ, caller=narrative, demo_id="demo-1", ledger=None,
                                    recordings=recordings)
    assert live_gate.mode is modelgate.Mode.LIVE and live_gate.ledger == mc.live_ledger_path()
    # 沒有展示編號就錄製,原因照實帶出來
    no_demo = modelgate.open_gate(environ, caller=narrative, demo_id=None, ledger=ledger,
                                  recordings=recordings)
    assert no_demo.mode is modelgate.Mode.RECORDED
    assert any("展示編號" in note for note in no_demo.notices)
    with pytest.raises(mc.UnknownModel):
        modelgate.open_gate({"RTB_MODEL": "gpt-x"}, caller=narrative, demo_id=None, ledger=ledger,
                            recordings=recordings)
    with pytest.raises(modelgate.GateRefused, match="呼叫者"):
        modelgate.open_gate({}, caller="analyzer_narrative", demo_id=None, ledger=ledger,
                            recordings=recordings)


# ---- 登入預檢(增量 2 的 runner 在印 READY 之後呼叫它,[S1160]) ----
def test_the_login_preflight_checks_once_and_marks_the_backend(tmp_path):
    assert mc.preflight_login(recorded()).outcome is mc.Preflight.NOT_APPLICABLE
    script = fake_claude(tmp_path / "ok")
    write_verification()
    settings = mc.settings_from_env({"RTB_MODEL_LIVE": "1"}, "demo-1", script)
    assert settings.mode is mc.Mode.LIVE
    checked = mc.preflight_login(settings)
    assert checked.outcome is mc.Preflight.PASSED and checked.reason is None
    logins = list(script.with_name("claude.log").glob("*.args"))
    assert logins == []  # 登入檢查不記進假 claude 的呼叫紀錄(它只記送出)
    call_dir = tmp_path / "rec"
    call_dir.mkdir()
    auth_runs = []
    real = cc.run_claude

    def counting(args, *rest, **kwargs):
        auth_runs.append(args[1] if len(args) > 1 else "")
        return real(args, *rest, **kwargs)

    cc_patch = pytest.MonkeyPatch()
    cc_patch.setattr(cc, "run_claude", counting)
    try:
        for n in range(2):  # 預檢之後的第一次與第二次送出都不再做登入檢查
            mc.call_model(request(f"第{n}次"), settings, recordings_dir=call_dir,
                          ledger=tmp_path / "l.sqlite")
    finally:
        cc_patch.undo()
    assert len(auth_runs) == 2
    assert "auth" not in auth_runs  # 預檢過了就不再做登入檢查
    out = fake_claude(tmp_path / "out", logged_in=False)
    failed = mc.preflight_login(mc.settings_from_env({"RTB_MODEL_LIVE": "1"}, "demo-1", out))
    assert failed.outcome is mc.Preflight.FAILED and "登入" in (failed.reason or "")
    assert not hasattr(mc, "check_login")  # 名字不跟後端的登入檢查撞(邊界測試的後端名單)


# ---- 開錄前的目錄檢查(runner 與評估執行器共用,[S1142]、[S1165]) ----
def test_the_recording_directory_must_be_empty_or_one_batch(tmp_path):
    fresh = tmp_path / "fresh"
    mc.check_recordings_dir(fresh, "b1")  # 不存在等於空的
    fresh.mkdir()
    mc.check_recordings_dir(fresh, "b1")
    same = tmp_path / "same"
    same.mkdir()
    for n in range(2):
        mc.call_model(request(f"q{n}", batch_id="b1"), live(FakeBackend(reply("a")), True),
                      recordings_dir=same, ledger=tmp_path / "l.sqlite")
    mc.check_recordings_dir(same, "b1")
    with pytest.raises(mc.MixedRecordingsDir, match="b2"):
        mc.check_recordings_dir(same, "b2")
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(mc.MixedRecordingsDir):
        mc.check_recordings_dir(stray, "b1")
    pending = tmp_path / "pending"
    pending.mkdir()
    key = mc.recording_key(mc.Caller.EVAL_CANDIDATE, mc.DEFAULT_MODEL, "s", "u", 5)
    from rtb import modelrecording as rec
    rec.claim(pending / f"{key}.json", "b9")  # 別的批次留下的佔位
    with pytest.raises(mc.MixedRecordingsDir):
        mc.check_recordings_dir(pending, "b1")
    # 代碼審 r1:同一批中斷留下的佔位也拒絕(要先確認沒有行程還在錄、刪掉再錄)
    with pytest.raises(mc.MixedRecordingsDir, match="佔位"):
        mc.check_recordings_dir(pending, "b9")
    with pytest.raises(mc.MixedRecordingsDir):
        mc.check_recordings_dir(tmp_path / "same" / next(p.name for p in same.iterdir()), "b1")



def test_the_recording_directory_check_has_no_gaps(tmp_path):  # noqa: PLR0915 - 逐個邊角
    """代碼審 r1:開錄前目錄檢查的邊角——檔名跟檔內的鍵不符、沒帶批次、懸空的符號連結、列不出目錄、
    預設的入庫目錄(只供重播)。"""
    folder = tmp_path / "rec"
    folder.mkdir()
    mc.call_model(request("q", batch_id="b1"), live(FakeBackend(reply("a")), True),
                  recordings_dir=folder, ledger=tmp_path / "l.sqlite")
    [recorded_file] = list(folder.iterdir())
    mc.check_recordings_dir(folder, "b1")
    other = "f" * 64
    recorded_file.rename(folder / f"{other}.json")  # 內容的鍵還是原本那個
    with pytest.raises(mc.MixedRecordingsDir, match="鍵"):
        mc.check_recordings_dir(folder, "b1")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(mc.MixedRecordingsDir, match="批次"):
        mc.check_recordings_dir(empty, None)
    with pytest.raises(mc.MixedRecordingsDir, match="批次"):
        mc.check_recordings_dir(empty, "")
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")
    with pytest.raises(mc.MixedRecordingsDir):
        mc.check_recordings_dir(dangling, "b1")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(mc.MixedRecordingsDir):
            mc.check_recordings_dir(locked, "b1")
    finally:
        locked.chmod(0o700)
    with pytest.raises(mc.MixedRecordingsDir, match="新的錄製目錄"):
        mc.check_recordings_dir(mc.default_recordings_dir(), "b1")
    # 代碼審 r2:空白批次;上一層是檔案或讀不到;欄位值讀取時會拒絕的錄製檔
    with pytest.raises(mc.MixedRecordingsDir, match="批次"):
        mc.check_recordings_dir(empty, "  ")
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    with pytest.raises(mc.MixedRecordingsDir):
        mc.check_recordings_dir(afile / "rec", "b1")
    closed = tmp_path / "closed"
    (closed / "rec").mkdir(parents=True)
    closed.chmod(0)
    try:
        with pytest.raises(mc.MixedRecordingsDir):
            mc.check_recordings_dir(closed / "rec", "b1")
    finally:
        closed.chmod(0o700)
    for field, value in (("outcome", "unknown"), ("settlement", "sort_of")):
        bad = tmp_path / f"bad-{field}"
        bad.mkdir()
        mc.call_model(request(f"q-{field}", batch_id="b1"), live(FakeBackend(reply("a")), True),
                      recordings_dir=bad, ledger=tmp_path / "l.sqlite")
        [saved] = list(bad.iterdir())
        data = json.loads(saved.read_text(encoding="utf-8"))
        data[field] = value
        if field == "outcome":
            data["text"] = None
        saved.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(mc.NoRecording):  # 讀的時候會拒絕
            rec_module.load_recording(saved, key=data["key"], caller=mc.Caller.EVAL_CANDIDATE,
                                      model=mc.DEFAULT_MODEL)
        with pytest.raises(mc.MixedRecordingsDir):  # 開錄前就該拒絕,兩邊同一套規則
            mc.check_recordings_dir(bad, "b1")


def test_the_cap_message_says_it_counts_only_the_capped_callers(dirs, monkeypatch):
    """代碼審 r1:「已達上限」訊息的已用只含計入上限的呼叫者,跟展示頁讀的 used_by_demo 分開講。"""
    monkeypatch.setattr(core, "DEMO_CAP_NANOUSD", 1)
    with pytest.raises(mc.LocalCapRefused) as refused:
        call(live(FakeBackend(reply("x"))), request("e"), dirs)
    assert "計入上限的呼叫者在這次展示已用" in str(refused.value)
    assert "計入上限的呼叫者在本月已用" in str(refused.value)


def test_numbers_in_model_text_must_trace_back_to_the_evidence():
    """代碼審 r1(Phase 13 計劃〈省掉不值得發生的模型工作〉④:11B 的說明與假說也照這條):文字裡提到的
    數字要能對回送出去的證據,對不上的句子不顯示。"""
    evidence = "預算=100、新預算=110、點擊=12、轉換=1、花費=0.5、風險說明:budget +10%"
    kept, dropped = mc.traceable_sentences(
        "預算從 100 加到 110,加了 10%。昨天花費999999美元,轉換率100%。點擊 12 次!", evidence)
    assert kept == "預算從 100 加到 110,加了 10%。點擊 12 次!" and dropped == 1
    assert mc.traceable_sentences("沒有任何數字的說明。", evidence) == ("沒有任何數字的說明。", 0)
    assert mc.traceable_sentences("花費 0.50 元,新預算 110.0。", evidence)[1] == 0  # 同值不同寫法
    assert mc.traceable_sentences("花費1,000元。", "花費=1000") == ("花費1,000元。", 0)
    assert mc.traceable_sentences("全形數字１２次。", evidence) == ("全形數字１２次。", 0)
    assert mc.traceable_sentences("只有 999。", evidence) == ("", 1)
    # 代碼審 r2(協調者裁定):負號保留;中文或大寫數字、數字後接萬千億、認不出的數字記號一律當對不回
    assert mc.traceable_sentences("剩餘預算 11.88。", "剩餘預算 -11.88") == ("", 1)
    kept = "剩餘預算 -11.88。"
    assert mc.traceable_sentences(kept, "剩餘預算 -11.88") == (kept, 0)
    assert mc.traceable_sentences("變動為 -5。", "變動=5") == ("", 1)
    for fabricated in ("建議預算九十九萬九千。", "預算從 100 萬加到 110 萬。", "花費壹佰元。",
                       "排名第⑨。", "上看 10⁶ 美元。", "損失上看 1e5 美元。", "花掉 ½。",
                       "第 Ⅻ 批。", "兩次都失敗。", "預算加一成。"):
        assert mc.traceable_sentences(fabricated, evidence + "、100、110、10、5、1") == ("", 1), (
            fabricated)
    # 全形的句末標點也斷句
    assert mc.traceable_sentences("預算從 100 加到 110\uff1b點擊率約 2.4%\u3002", evidence) == (
        "預算從 100 加到 110\uff1b", 1)
    shouted = "點擊 12 次\uff01真的\uff1f"
    assert mc.traceable_sentences(shouted + "昨天 999 次\uff01", evidence) == (shouted, 1)
    assert modelgate.traceable_sentences is mc.traceable_sentences
