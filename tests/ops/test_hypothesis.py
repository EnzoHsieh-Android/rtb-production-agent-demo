"""Phase 11B 增量 2 接入點 1:異常原因假說命令列(維運,告警響才呼叫模型)。

計劃 [[Projects/RTB_Phase11B大模型接入_計劃]]〈接入點 1〉的 [S910]、[S911]、
[S912](維運套件的行為那半;
逐檔白名單在 tests/ops/test_ops_boundaries.py)與 [S916](接入點 1 那半,由
tests/analyzer/test_narrate.py
的同名合約測試呼叫這裡的 `hypothesis_ignores_injected_names`),以及
[[Projects/RTB_Phase13AI參與決策_計劃]]
的 [S1102](假說命令列那半)。告警用 rows.py 直接寫紀錄造;模型一律是假的 claude 腳本或錄製,不呼叫真的
claude。
"""

import ast
import io
import json
import sqlite3
import subprocess
import sys
import threading
import tomllib
from contextlib import contextmanager
from datetime import timedelta

import pytest

from rtb import modelclient as mc
from rtb import modelledger_view as view
from rtb.capabilitykit import AUDIT_KEY_ENV
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.ops import hypothesis, slo
from tests.model.fakes import claude_json, fake_claude, invocations, write_verification
from tests.ops.rows import Rows, at
from tests.ops.test_ops_boundaries import OPS, _ops_offenders

NOW = at(minutes=600)
CLI_KEY = "k" * 32
VALID = {"hypotheses": ["任務甲那批在收件後都過期了,可能是執行迴圈沒在跑"],
         "next_step": "open_example_trace"}


@pytest.fixture
def rows(tmp_path):
    built = Rows(tmp_path)
    yield built
    built.close()


@pytest.fixture
def dsp_url(tmp_path):
    dsp_db = tmp_path / "dsp.db"
    CampaignStore(dsp_db).close()
    server = DspServer(dsp_db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0,
                       audit_key=CLI_KEY.encode())
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def tenants_file(tmp_path):
    path = tmp_path / "tenants.json"
    path.write_text(json.dumps({"tenants": {
        "acme": {"campaigns": ["c1", "c2"], "max_budget": 1000, "aggregate_limit": 500},
        "beta": {"campaigns": ["c3"], "max_budget": 1000, "aggregate_limit": 500}}}),
        encoding="utf-8")
    path.chmod(0o600)
    return path


def fire_alert(rows):
    """最近 4 秒 12 件提案都在收件口過期:安全完成的快燒與慢燒都響。"""
    for n in range(12):
        rows.event(NOW - timedelta(seconds=4) + timedelta(milliseconds=200 * n), f"e{n:02d}",
                   "received")
        rows.event(NOW - timedelta(seconds=3) + timedelta(milliseconds=200 * n), f"e{n:02d}",
                   "expired")


def args(rows, dsp_url, tmp_path, *extra):
    return ["--executor-db", str(rows.executor_db), "--analyzer-db", str(rows.analyzer_db),
            "--dsp-url", dsp_url, "--dsp-timeout-seconds", "0.5", "--now", NOW.isoformat(),
            "--tenants-config", str(tenants_file(tmp_path)), *extra]


def run(rows, dsp_url, tmp_path, environ, *extra):
    out, err = io.StringIO(), io.StringIO()
    code = hypothesis.run(args(rows, dsp_url, tmp_path, *extra), out=out, err=err,
                          environ={AUDIT_KEY_ENV: CLI_KEY, **environ})
    return code, json.loads(out.getvalue()), err.getvalue()


def live_env(tmp_path, answer, **options):
    script = fake_claude(tmp_path / "bin", claude_json(
        answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)), **options)
    write_verification()
    return script, {"RTB_MODEL_LIVE": "1", "PATH": str(script.parent), "HOME": str(tmp_path)}


def slo_code(rows, dsp_url):
    return slo.run(["--executor-db", str(rows.executor_db), "--analyzer-db",
                    str(rows.analyzer_db), "--dsp-url", dsp_url, "--dsp-timeout-seconds", "0.5",
                    "--now", NOW.isoformat()], out=io.StringIO(), err=io.StringIO(),
                   environ={AUDIT_KEY_ENV: CLI_KEY})


def dump(*paths):
    out = {}
    for path in paths:
        conn = sqlite3.connect(path)
        try:
            out[path.name] = "\n".join(conn.iterdump())
        finally:
            conn.close()
    return out


# ---- [S910] ----
def test_no_alert_means_no_model_call(rows, dsp_url, tmp_path):
    rows.event(NOW - timedelta(seconds=3), "h1", "handed_off")  # 有事件,但沒有任何告警
    script, environ = live_env(tmp_path, VALID)
    code, printed, err = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    assert code == slo_code(rows, dsp_url) == hypothesis.EXIT_OK
    assert printed["hypothesis"] == {"status": "no_alert", "message": "沒有告警,不呼叫模型"}
    assert "沒有告警,不呼叫模型" in err
    assert [s["name"] for s in printed["slos"]] == [s.name for s in slo.SLOS]  # 告警照常印
    assert invocations(script) == []
    assert not mc.live_ledger_path().exists()  # 不記帳


# ---- [S911] ----
@pytest.mark.parametrize("answer", [
    {**VALID, "next_step": "restart_everything"},  # 下一步不在固定清單
    {**VALID, "hypotheses": ["一", "二", "三", "四"]},  # 超過 3 條
    {**VALID, "hypotheses": ["長" * 501]},  # 超過 500 字
    {**VALID, "hypotheses": ["第一行\n第二行"]},  # 含換行
    {**VALID, "hypotheses": ["響鈴\x07"]},  # 不可列印
    {**VALID, "hypotheses": []},  # 沒有假說
    {**VALID, "extra": 1},  # 多欄
    "```json\n" + json.dumps(VALID, ensure_ascii=False) + "\n```",  # 包程式碼圍欄
    "不是 JSON",
], ids=["step", "too_many", "too_long", "newline", "unprintable", "empty", "extra_key", "fence",
        "not_json"])
def test_an_invalid_hypothesis_is_discarded_and_the_alert_still_prints(rows, dsp_url, tmp_path,
                                                                       answer):
    fire_alert(rows)
    _, environ = live_env(tmp_path, answer)
    code, printed, err = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    assert code == slo_code(rows, dsp_url)  # 結束代碼跟服務水準命令列一致
    assert printed["hypothesis"]["status"] == "failed"
    assert printed["hypothesis"]["message"] == "模型沒有給出假說"
    assert "模型沒有給出假說" in err
    fired = next(s for s in printed["slos"] if s["name"] == "safe_completion")
    assert fired["fast"]["fired"] is True  # 告警照常印
    assert "hypotheses" not in printed["hypothesis"]


def test_a_valid_hypothesis_is_labelled_and_placeholders_are_restored(rows, dsp_url, tmp_path):
    fire_alert(rows)
    script, environ = live_env(tmp_path, VALID)
    code, printed, _ = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    assert code == slo_code(rows, dsp_url)
    shown = printed["hypothesis"]
    assert shown["status"] == "ok" and shown["label"] == "模型產生、僅供參考"
    assert shown["source"] == "live" and shown["alerts"] == ["safe_completion"]
    assert shown["next_step"] == "open_example_trace"
    assert shown["next_step_shown"] == hypothesis.NEXT_STEPS["open_example_trace"]
    [sent] = invocations(script)
    assert "任務甲" in sent["stdin"] and not any(f"e{n:02d}" in sent["stdin"] for n in range(12))
    [text] = shown["hypotheses"]  # 送出的是佔位符,驗證通過後印出的換回真實編號
    assert "任務甲" not in text and any(f"e{n:02d}" in text for n in range(12))
    assert len(hypothesis.NEXT_STEPS) == 6


def test_the_hypothesis_input_is_whitelisted_and_bounded(rows, dsp_url, tmp_path):
    fire_alert(rows)
    rows.tool_call(NOW - timedelta(seconds=2), "dsp:campaign", latency_ms=1234.5, task="e00")
    statuses, user, _ = hypothesis.gather_input(
        NOW, executor_db=rows.executor_db, analyzer_db=rows.analyzer_db, dsp_url=dsp_url,
        dsp_timeout_seconds=0.5, audit_key=CLI_KEY.encode(),
        tenants_config=tenants_file(tmp_path))
    assert [s.name for s in hypothesis.fired(statuses)] == ["safe_completion"]
    assert "e00" not in user and "k-e00" not in user and "h-e00" not in user  # 編號換佔位符
    assert "2026" not in user and "1234.5" not in user and "latency" not in user  # 沒有時間與延遲
    assert user.count("範例追蹤") == 1 and user.count("任務") >= 3
    assert "同一步內" in user or "跨行程交接" in user  # 間隔類別,不送時間長短
    assert len(user.encode("utf-8")) <= hypothesis.MAX_INPUT_BYTES
    long = hypothesis.bounded("\n".join(["x" * 100] * 400))
    assert len(long.encode("utf-8")) <= hypothesis.MAX_INPUT_BYTES and long.endswith("已截斷)")


# ---- [S912](行為那半:維運不寫分析端、收件口或 DSP 的資料庫) ----
def test_only_the_hypothesis_command_calls_the_model_or_writes_the_ledger(  # noqa: PLR0915
        rows, dsp_url, tmp_path):
    fire_alert(rows)
    before = dump(rows.executor_db, rows.analyzer_db, tmp_path / "dsp.db")
    script, environ = live_env(tmp_path, VALID)
    code, printed, _ = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    assert printed["hypothesis"]["status"] == "ok" and len(invocations(script)) == 1
    assert dump(rows.executor_db, rows.analyzer_db, tmp_path / "dsp.db") == before
    ledger = view.ModelLedgerView(mc.live_ledger_path())  # 唯一的寫入是經模型用戶端記花費帳
    try:
        with ledger.read_transaction():
            [booked] = ledger.calls_between("0000", "9999")
    finally:
        ledger.close()
    assert (booked.caller, booked.demo_id, booked.source) == ("ops_hypothesis", "demo-1", "live")
    assert code == slo_code(rows, dsp_url)
    # 靜態那半:逐檔白名單裡模型用戶端的名字只准假說命令列用;別的維運檔用了就抓得到
    assert _ops_offenders(OPS) == []
    for extra in ("\ndef _w(r, s):\n    return call_model(r, s)\n",
                  "\ndef _w(e):\n    return mc.settings_from_env(e, None, None)\n",
                  "\ndef _w(p):\n    return live_ledger_path()\n"):
        copy = tmp_path / f"ops{abs(hash(extra))}"
        copy.mkdir()
        for path in OPS.glob("*.py"):
            (copy / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        with (copy / "metrics.py").open("a", encoding="utf-8") as file:
            file.write(extra)
        assert _ops_offenders(copy), extra
    # ruff 的匯入禁令只對假說命令列整檔關掉:別的維運檔匯入模型用戶端照樣擋;假說命令列自己的匯入
    # 除了模型用戶端,不碰其他任何被禁的模組
    rules = tomllib.loads((OPS / "ruff.toml").read_text(encoding="utf-8"))
    banned = set(rules["lint"]["flake8-tidy-imports"]["banned-api"]) - {"rtb.modelclient"}
    assert rules["lint"]["per-file-ignores"] == {"hypothesis.py": ["TID251"]}
    tree = ast.parse((OPS / "hypothesis.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                 for a in n.names}
    assert not {m for m in imported for b in banned if m == b or m.startswith(b + ".")}
    probe = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(OPS / "ruff.toml"),
         "--stdin-filename", str(OPS / "metrics.py"), "-"],
        input="import rtb.modelclient\n", capture_output=True, text=True, timeout=60, check=False)
    assert probe.returncode == 1 and "TID251" in probe.stdout


# ---- [S1102](假說命令列那半;合約測試在 tests/analyzer/test_narrate.py 呼叫它) ----
@contextmanager
def _world(folder, name=None):
    """自己建一份 rows 與帶稽核金鑰的真 DSP(給別的測試檔呼叫的輔助函式用,不靠夾具)。"""
    data = Rows(folder)
    dsp_db = folder / "dsp.db"
    seeding = CampaignStore(dsp_db)
    if name is not None:
        seeding.seed_campaign("c1", budget=100, name=name)
    seeding.close()
    server = DspServer(dsp_db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0,
                       audit_key=CLI_KEY.encode())
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        yield data, f"http://127.0.0.1:{server.server_address[1]}", dsp_db
    finally:
        server.shutdown()
        server.server_close()
        data.close()


def recorded_hypotheses_book_into_the_given_ledger(tmp_path):
    folder = tmp_path / "hypothesis-ledger"
    folder.mkdir()
    ledger = folder / "demo-ledger.sqlite"
    with _world(folder) as (data, url, _):
        fire_alert(data)
        code, printed, _ = run(data, url, folder, {}, "--demo-id", "demo-9", "--ledger",
                               str(ledger), "--recordings-dir", str(folder / "empty"))
        assert code == slo_code(data, url)
        shown = printed["hypothesis"]
        assert shown["status"] == "failed" and shown["reason"] == "no_recording"
        assert shown["mode"] == "recorded"
        reader = view.ModelLedgerView(ledger)
        try:
            with reader.read_transaction():
                [row] = reader.calls_between("0000", "9999")
        finally:
            reader.close()
        assert (row.caller, row.demo_id, row.source) == ("ops_hypothesis", "demo-9", "recorded")
        assert not mc.live_ledger_path().exists()
        # 即時模式的帳寫死家目錄那一本:給了 --ledger 是參數錯
        _, environ = live_env(folder, VALID)
        out, err = io.StringIO(), io.StringIO()
        code = hypothesis.run(args(data, url, folder, "--demo-id", "d", "--ledger", str(ledger)),
                              out=out, err=err, environ={AUDIT_KEY_ENV: CLI_KEY, **environ})
        assert code == hypothesis.EXIT_BAD_ARGUMENTS and "家目錄" in err.getvalue()



# ---- 增量 4 代碼審 r2 v2:展示給的 --recorded-ledger 在錄製模式有告警時才用 ----
def test_recorded_hypotheses_book_into_the_recorded_ledger(rows, dsp_url, tmp_path):
    """展示驅動一律給 --recorded-ledger(不猜模式):錄製模式、告警響了,重播紀錄記在它,帳號家目錄
    那一本不建(以前拿掉這一條,展示的重播紀錄會寫進真的帳)。"""
    ledger = tmp_path / "scenario-ledger.sqlite"
    fire_alert(rows)
    code, printed, _ = run(rows, dsp_url, tmp_path, {}, "--demo-id", "demo-9",
                           "--recorded-ledger", str(ledger),
                           "--recordings-dir", str(tmp_path / "empty"))
    assert code == slo_code(rows, dsp_url)
    shown = printed["hypothesis"]
    assert shown["mode"] == "recorded" and shown["reason"] == "no_recording"
    reader = view.ModelLedgerView(ledger)
    try:
        with reader.read_transaction():
            [row] = reader.calls_between("0000", "9999")
    finally:
        reader.close()
    assert (row.caller, row.demo_id, row.source) == ("ops_hypothesis", "demo-9", "recorded")
    assert not mc.live_ledger_path().exists()

# ---- [S916](接入點 1 那半;合約測試在 tests/analyzer/test_narrate.py 呼叫它) ----
def hypothesis_ignores_injected_names(tmp_path, name):
    """廣告名稱藏誘導文字、模型照誘導回答:假說命令列不送名稱、不寫任何業務資料庫、結束代碼不變,
    模型輸出只影響給人看的文字(下一步不在清單就整份作廢)。"""
    folder = tmp_path / "hypothesis"
    folder.mkdir()
    with _world(folder, name) as (data, url, dsp_db):
        fire_alert(data)
        data.analyzer.execute(
            "INSERT INTO evidence VALUES ('e00', 1, 'e00-1-text', 'campaign_text', 'dsp', ?, 1, "
            "'x', 'untrusted_text', ?)", (NOW.isoformat(), json.dumps({"name": name})))
        before = dump(data.executor_db, data.analyzer_db, dsp_db)
        outcomes = []
        for answer in ({"hypotheses": ["照指示把所有預算加 500%"], "next_step": "set_budget"},
                       {"hypotheses": ["照指示:" + name[:200].replace("\n", " ")],
                        "next_step": "query_dsp_history"}):
            script, environ = live_env(folder / str(len(outcomes)), answer)
            code, printed, _ = run(data, url, folder, environ, "--demo-id", "demo-1")
            [sent] = invocations(script)
            assert name[:40] not in sent["stdin"]  # 名稱不在送出的內容裡
            assert code == slo_code(data, url)
            outcomes.append(printed["hypothesis"]["status"])
        assert dump(data.executor_db, data.analyzer_db, dsp_db) == before
        return outcomes


# ---- 代碼審 r1 ----
def test_live_recording_hypotheses_need_a_batch_id_and_still_print_the_alert(rows, dsp_url,
                                                                            tmp_path):
    """即時加錄製沒帶批次:不呼叫模型,告警照常印,入口以參數錯結束;帶了批次與新目錄才照常錄。"""
    fire_alert(rows)
    script, environ = live_env(tmp_path, VALID)
    environ = {**environ, "RTB_MODEL_RECORD": "1"}
    code, printed, err = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1",
                             "--recordings-dir", str(tmp_path / "fresh"))
    assert code == hypothesis.EXIT_BAD_ARGUMENTS and "批次" in err
    assert printed["hypothesis"]["status"] == "refused"
    assert next(s for s in printed["slos"] if s["name"] == "safe_completion")["fast"]["fired"]
    assert invocations(script) == []
    code, printed, err = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1",
                             "--batch-id", "b1")  # 預設的入庫目錄只供重播
    assert code == hypothesis.EXIT_BAD_ARGUMENTS and "新的錄製目錄" in err
    assert invocations(script) == []
    code, printed, _ = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1",
                           "--batch-id", "b1", "--recordings-dir", str(tmp_path / "fresh"))
    assert code == slo_code(rows, dsp_url) and printed["hypothesis"]["status"] == "ok"
    [saved] = list((tmp_path / "fresh").glob("*.json"))
    assert json.loads(saved.read_text(encoding="utf-8"))["batch_id"] == "b1"


def test_hypotheses_with_untraceable_numbers_are_not_shown(rows, dsp_url, tmp_path):
    """假說文字裡的數字要對回送出去的內容:對不上的那一條不顯示並標出拿掉幾條,全部對不上就整份作廢;
    沒有數詞的常用寫法照常顯示(代碼審 r3)。"""
    fire_alert(rows)
    answer = {"hypotheses": ["過期的都是同一批,執行迴圈可能沒在跑", "昨天花了 999999"],
              "next_step": "open_example_trace"}
    _, environ = live_env(tmp_path / "a", answer)
    code, printed, _ = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    shown = printed["hypothesis"]
    assert shown["status"] == "ok"
    assert shown["hypotheses"] == ["過期的都是同一批,執行迴圈可能沒在跑"]
    assert shown["dropped_hypotheses"] == 1 and shown["note"] == "有 1 條因數字對不回未顯示"
    plain = {"hypotheses": ["過期的都是同一批", "執行迴圈一直卡在等鎖,要進一步看",
                            "工作者之間的版本不一致"], "next_step": "open_example_trace"}
    _, environ = live_env(tmp_path / "c", plain)
    code, printed, _ = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    assert printed["hypothesis"]["status"] == "ok"
    assert printed["hypothesis"]["hypotheses"] == plain["hypotheses"]
    assert "dropped_hypotheses" not in printed["hypothesis"]
    _, environ = live_env(tmp_path / "b", {"hypotheses": ["昨天花了 999999"],
                                          "next_step": "open_example_trace"})
    code, printed, _ = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    assert printed["hypothesis"]["status"] == "failed"
    assert printed["hypothesis"]["reason"] == "untraceable_numbers"
    assert printed["hypothesis"]["dropped_hypotheses"] == 1
    assert code == slo_code(rows, dsp_url)


def test_a_refused_live_recording_says_why_even_without_an_alert(rows, dsp_url, tmp_path):
    """代碼審 r3:沒有告警時,即時加錄製的入口參數錯照樣印原因。"""
    rows.event(NOW - timedelta(seconds=3), "h1", "handed_off")
    _, environ = live_env(tmp_path, VALID)
    environ = {**environ, "RTB_MODEL_RECORD": "1"}
    code, printed, err = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1")
    assert code == hypothesis.EXIT_BAD_ARGUMENTS
    assert printed["hypothesis"]["status"] == "no_alert"
    assert "批次" in err and "拒絕" in err


def test_the_recording_directory_is_checked_once_before_the_call(rows, dsp_url, tmp_path,
                                                                 monkeypatch):
    """代碼審 r2:目錄檢查只在呼叫模型之前做一次;呼叫之後目錄裡多出同批別的佔位,也不把已經成功的
    假說改判成參數錯。"""
    fire_alert(rows)
    _, environ = live_env(tmp_path, VALID)
    environ = {**environ, "RTB_MODEL_RECORD": "1"}
    real, calls = mc.check_recordings_dir, []

    def once_then_mixed(directory, batch_id):
        calls.append(directory)
        if len(calls) > 1:
            raise mc.MixedRecordingsDir("別的行程留下的佔位")
        return real(directory, batch_id)

    monkeypatch.setattr(mc, "check_recordings_dir", once_then_mixed)
    code, printed, _ = run(rows, dsp_url, tmp_path, environ, "--demo-id", "demo-1",
                           "--batch-id", "b1", "--recordings-dir", str(tmp_path / "fresh"))
    assert printed["hypothesis"]["status"] == "ok"
    assert code == slo_code(rows, dsp_url) and len(calls) == 1
