"""Phase 15 增量 3:規則模式探索的離線命令列、錄製批次驗收與入庫、人讀報告與 README(計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈模型建議、機械核對與人讀報告〉〈拆增量〉3)。

合約 [S1508] [S1509],以及 [S1507]/[S1514] 在命令列層面的補強(缺錄製不轉即時、展示編號序號)。
代碼審 r1 後的流程:錄製當下把暫存目錄、錄製鍵與檔案雜湊綁進批次清單 → `--check-in` 只做驗收判定、
不搬檔 → 協調者照印出的指令手動搬 → `--verify` 核入庫內容等於清單記的雜湊。
不呼叫真的 claude、不設行程環境的即時開關、不碰 ~/.rtb:即時一律是行程內的假後端,入庫目錄、錄製目錄
與帳本都在 tmp_path;重播走真的模型閘道(錄製模式只讀錄製),PATH 上放一支會留記號的假 claude,
斷言它沒被執行。

入庫錄製由協調者用真模型錄:`test_committed_rule_mining_recordings_replay_all_three_seeds`
在錄製入庫前是紅的(不跳過,免得忘了錄)。
"""

import dataclasses
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from rtb import modelclient as mc
from rtb import modelcore as core
from rtb.analyzer import modelgate
from rtb.eval import rule_mining_eval as rme
from rtb.eval import rule_mining_prompt as rp
from rtb.eval import rule_mining_recordings as rr
from rtb.eval import rule_mining_report as report
from rtb.eval import rule_mining_vocab as rv
from tests.eval.test_rule_mining_model import honest_answer, ledger_rows
from tests.model.fakes import FakeBackend, fake_claude, invocations, live, reply

SRC = Path(__file__).resolve().parents[2] / "src"
REPO = SRC.parent
COMMITTED_REPORT = REPO / "governance" / "eval" / "phase15-rule-mining.md"
DAY = "20260928"
LIVE_ENV = {"RTB_MODEL_LIVE": "1", "RTB_MODEL_RECORD": "1"}


def demo(seed, sequence):
    return f"phase15-seed-{seed}-{sequence}"


def batch(seed):
    return f"phase15-rule-mining-{seed}-{DAY}"


@pytest.fixture(scope="module")
def prepared():
    return {seed: rme.prepare(seed) for seed in rv.SEEDS}


class Live:
    """代替 modelgate.open_gate 的即時閘道(假後端,錄製照真的模型用戶端寫);記下開過幾次、帶的呼叫者。
    花費帳用 tmp 的一本頂替帳號家目錄那一本;即時模式給了帳檔路徑照真的閘道拒絕。"""

    def __init__(self, tmp_path, *responses):
        self.backend = FakeBackend(*responses)
        self.ledger = tmp_path / "home-ledger.sqlite"
        self.callers = []

    def __call__(self, environ, *, caller, demo_id, ledger, recordings,  # noqa: PLR0913 - 同閘道
                 batch_id=None, recorded_ledger=None, notify=None):
        del recorded_ledger, notify
        assert rr.live_recording_requested(environ)
        if ledger is not None:
            raise modelgate.GateRefused("即時模式的花費帳寫死在帳號家目錄那一本")
        mc.check_recordings_dir(Path(recordings), batch_id)  # 跟真的閘道一樣先驗錄製目錄
        self.callers.append(caller)
        return modelgate.Gate(live(self.backend, record=True), caller, demo_id, self.ledger,
                              Path(recordings), batch_id)


def cli(argv, root, environ, opener=None):
    out, err = io.StringIO(), io.StringIO()
    code = rme.command(argv, out=out, err=err, environ=environ, root=root, opener=opener)
    return code, out.getvalue(), err.getvalue()


def replay_env(tmp_path, extra=None):
    """重播的環境:PATH 上一支會留記號的假 claude(證明沒被執行);extra 可以把即時開關也加上。"""
    script = fake_claude(tmp_path / "bin", "{}")
    return {"PATH": str(script.parent), "HOME": str(tmp_path / "home"), **(extra or {})}, script


def record(tmp_path, root, seed, sequence, answer_or_failure, name=None, response=None):  # noqa: PLR0913
    folder = tmp_path / (name or f"staging-{root.name}-{seed}-{sequence}")
    if response is None:
        response = (answer_or_failure if isinstance(answer_or_failure, Exception)
                    else reply(answer_or_failure, input_tokens=3000, output_tokens=400))
    opener = Live(tmp_path, response)
    code, out, err = cli(["--demo-id", demo(seed, sequence), "--batch-id", batch(seed),
                          "--recordings-dir", str(folder)], root,
                         {**LIVE_ENV, "PATH": str(tmp_path / "no-bin")}, opener)
    return code, out, err, folder, opener


def check_in(tmp_path, root, seed, sequence, folder, *,  # noqa: PLR0913 - 一次驗收的每一樣
             flag="--check-in", environ=None):
    env, script = replay_env(tmp_path, environ)
    code, out, err = cli([flag, "--demo-id", demo(seed, sequence), "--recordings-dir",
                          str(folder), "--ledger", str(tmp_path / "replay.sqlite")], root, env)
    assert invocations(script) == []
    return code, out, err


def manifest(root):
    return rr.load(root)


def attempt(root, seed, sequence):
    return next(a for a in manifest(root).attempts if a.demo_id == demo(seed, sequence))


def attempt_status(root, demo_id):
    found = [a.status for a in manifest(root).attempts if a.demo_id == demo_id]
    return found[0] if found else None


def move(root, seed, sequence, out=None):
    """照判定印出的指令手動搬(協調者的動作;程式不搬)。"""
    done = attempt(root, seed, sequence)
    target = root / str(seed)
    target.mkdir()
    for name, _ in done.files:
        shutil.copyfile(Path(done.staging_dir) / name, target / name)
        if out is not None:
            assert f"cp {Path(done.staging_dir) / name} " in out, out


def record_judge_move(tmp_path, root, prepared, seed, answer=None):
    sequence = rr.next_sequence(manifest(root), seed)
    code, out, err, folder, _ = record(tmp_path, root, seed, sequence,
                                       answer or honest_answer(prepared[seed]))
    assert code == rme.EXIT_OK, out + err
    code, out, err = check_in(tmp_path, root, seed, sequence, folder)
    assert code == rme.EXIT_OK, out + err
    move(root, seed, sequence, out)
    return folder


def verify(tmp_path, root, environ=None):
    env, script = replay_env(tmp_path, environ)
    code, out, err = cli(["--verify", "--ledger", str(tmp_path / "ci.sqlite")], root, env)
    assert invocations(script) == []
    return code, out, err


# ---- [S1508] ----
def test_rule_mining_recordings_verify_before_check_in(tmp_path, prepared):  # noqa: PLR0915
    root = tmp_path / "committed"
    # 第 1 次逾時:記「呼叫失敗」、不入庫;錄製目錄裡的逾時錄製過得了共用的批次檢查,照樣不准判定
    code, out, _, first, opener = record(tmp_path, root, 15001, 1, mc.ModelTimeout("逾時"))
    assert code == rme.EXIT_FAILED and "呼叫失敗" in out and len(opener.backend.calls) == 1
    failed = attempt(root, 15001, 1)
    assert (failed.status, failed.outcome, failed.sequence) == (rr.FAILED, "timeout", 1)
    assert mc.batch_file_problems(first, rr.BATCH_PATTERN, rr.BATCH_SHAPE) == []
    expected = rr.expected_key(rp.SYSTEM_PROMPT, prepared[15001].table)
    assert any("要是 rule_mining 且 ok" in p for p in rr.batch_problems(first, failed, expected))
    code, _, err = check_in(tmp_path, root, 15001, 1, first)
    assert code == rme.EXIT_REFUSED and "呼叫失敗" in err

    # 第 2 次(新展示編號、新目錄)成功:待驗收;錄製當下綁目錄、鍵與每個檔的 SHA-256
    answer = honest_answer(prepared[15001])
    code, out, _, second, _ = record(tmp_path, root, 15001, 2, answer)
    assert code == rme.EXIT_OK and "--check-in" in out
    pending = attempt(root, 15001, 2)
    assert (pending.status, pending.outcome) == (rr.PENDING, "ok")
    assert pending.expected_key == pending.recording_key == expected
    assert pending.staging_dir == str(second.resolve())
    assert pending.files == rr.file_hashes(second) and [n for n, _ in pending.files] == [
        f"{expected}.json"]
    assert pending.started_at <= pending.finished_at and pending.batch_id == batch(15001)
    assert pending.model == mc.DEFAULT_MODEL and pending.list_nanousd > 0

    _tampered_or_wrong_directories_are_refused(tmp_path, root, prepared, second)

    # 判定通過:標成可入庫、印手動搬的指令;程式自己不建入庫目錄、不搬檔(環境開著即時開關也只重播)
    code, out, _ = check_in(tmp_path, root, 15001, 2, second, environ=LIVE_ENV)
    assert code == rme.EXIT_OK and "手動搬" in out, out
    assert attempt(root, 15001, 2).status == rr.ACCEPTED
    assert not (root / "15001").exists()
    # 還沒搬就重跑判定:不算錯,再印一次指令;CI 驗收指出還沒搬
    code, out2, _ = check_in(tmp_path, root, 15001, 2, second)
    assert code == rme.EXIT_OK and "手動搬" in out2
    code, vout, _ = verify(tmp_path, root)
    assert code == rme.EXIT_FAILED and "還沒搬進入庫目錄" in vout
    move(root, 15001, 2, out)
    # 搬完再跑判定:已完成
    code, out, _ = check_in(tmp_path, root, 15001, 2, second)
    assert code == rme.EXIT_OK and "已完成" in out
    # 每種子只准第一次成功入庫:再錄拒絕
    code, _, err, _, opener = record(tmp_path, root, 15001, 3, answer)
    assert code == rme.EXIT_REFUSED and "已判定可入庫" in err and opener.backend.calls == []

    # CI 只重播入庫錄製:兩個種子還在等錄製,驗收沒過;不啟動 claude
    code, out, _ = verify(tmp_path, root)
    assert code == rme.EXIT_FAILED and "- 15001:通過(已量)" in out
    assert "- 15002:沒過(等錄製)" in out
    for seed in (15002, 15003):
        record_judge_move(tmp_path, root, prepared, seed)
    code, out, err = verify(tmp_path, root)
    assert code == rme.EXIT_OK, out + err
    assert "- 驗收:通過" in out

    # 批次清單供機檢:三種子、資料雜湊、評估版本與版本雜湊、所有嘗試
    saved = manifest(root)
    assert saved == rr.loads((root / rr.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert saved.seeds == tuple((s, rr.h.EXPECTED_DATA_SHA256[s]) for s in rv.SEEDS)
    assert (saved.eval_version, saved.version_sha256) == (rv.EVAL_VERSION, rp.version_sha256())
    assert [(a.demo_id, a.status) for a in saved.attempts] == [
        (demo(15001, 1), rr.FAILED), (demo(15001, 2), rr.ACCEPTED),
        (demo(15002, 1), rr.ACCEPTED), (demo(15003, 1), rr.ACCEPTED)]
    assert rr.manifest_problems(saved) == []
    _committed_content_is_bound_to_the_manifest(tmp_path, root, prepared)
    _manifest_rules_are_machine_checked(tmp_path, root)


def _tampered_or_wrong_directories_are_refused(tmp_path, root, prepared, staging):  # noqa: PLR0915
    """代碼審 r1 正確性 1、資安 F1/F3;r2 資安 N1:判定只收錄製時記的那個真目錄,只看清單記的那份
    錄製檔、雜湊要相同;打錯目錄、指向它的符號連結、目錄或錄製檔換成符號連結、改過內容、檔不見都是參數錯,
    拒絕、清單不動;暫存目錄裡的雜檔(多一份別的鍵、筆記檔)忽略,不影響判定。"""
    before = (root / rr.MANIFEST_NAME).read_text(encoding="utf-8")
    [original] = staging.iterdir()
    data = json.loads(original.read_text(encoding="utf-8"))
    copy = tmp_path / "copy-of-staging"
    shutil.copytree(staging, copy)
    link = tmp_path / "link-to-staging"
    link.symlink_to(staging, target_is_directory=True)
    for flag in ("--check-in", "--verify"):
        for folder in (tmp_path / "typo-dir", copy, tmp_path / "empty", link):
            code, _, err = check_in(tmp_path, root, 15001, 2, folder, flag=flag)
            assert code == rme.EXIT_REFUSED and "錄製時記的目錄" in err, err
    backup = original.read_bytes()
    decoy = tmp_path / "decoy.json"
    decoy.write_bytes(backup)
    refused = [
        (lambda: original.write_text(json.dumps({**data, "caller": "eval_candidate"}),
                                     encoding="utf-8"), "SHA-256"),
        (lambda: original.write_text(json.dumps({**data, "text": data["text"].replace(
            '"照表抄"', '"改過"')}), encoding="utf-8"), "SHA-256"),
        (lambda: (original.unlink(), original.symlink_to(decoy)), "不是一般檔"),
        (lambda: original.unlink(), "已遺失"),
    ]
    for edit, needle in refused:
        edit()
        for flag in ("--check-in", "--verify"):
            code, _, err = check_in(tmp_path, root, 15001, 2, staging, flag=flag)
            assert code == rme.EXIT_REFUSED and needle in err, (needle, err)
        original.unlink(missing_ok=True)
        original.write_bytes(backup)
    # 暫存目錄本身被換成指向真目錄的符號連結(路徑字面一樣):不收
    real = tmp_path / "real-staging"
    staging.rename(real)
    staging.symlink_to(real, target_is_directory=True)
    code, _, err = check_in(tmp_path, root, 15001, 2, staging, flag="--check-in")
    assert code == rme.EXIT_REFUSED and "不收符號連結" in err, err
    staging.unlink()
    real.rename(staging)
    # 雜檔忽略:多一份別的鍵、一個筆記檔、一個子目錄,只判清單記的那份,照樣通過(只判不寫)
    other_key = rr.expected_key(rp.SYSTEM_PROMPT, prepared[15001].table + "\n")
    (staging / f"{other_key}.json").write_text(json.dumps({**data, "key": other_key}),
                                              encoding="utf-8")
    (staging / "notes.txt").write_text("x", encoding="utf-8")
    (staging / "sub").mkdir()
    code, out, err = check_in(tmp_path, root, 15001, 2, staging, flag="--verify")
    assert code == rme.EXIT_OK and "3 個別的項目,已忽略" in out, out + err
    for extra in (staging / f"{other_key}.json", staging / "notes.txt"):
        extra.unlink()
    (staging / "sub").rmdir()
    assert (root / rr.MANIFEST_NAME).read_text(encoding="utf-8") == before
    assert attempt(root, 15001, 2).status == rr.PENDING


def _committed_content_is_bound_to_the_manifest(tmp_path, root, prepared):
    """代碼審 r1 正確性 3/4、資安 F3:入庫目錄的檔要恰等於清單記的 SHA-256;同版本另錄一份替換、
    改回覆文字、多放檔、殘留鎖檔或沒有判定紀錄的種子目錄,--verify 都沒過;沒過的批次不進比較與
    撤除判斷(AI 欄寫驗收沒過、未量)。"""
    [committed] = (root / "15002").iterdir()
    backup = committed.read_bytes()
    data = json.loads(backup)
    other_root = tmp_path / "other-root"
    elsewhere = tmp_path / "another-account"  # 另一本花費帳:同一本會被跨 checkout 的檢查擋下
    elsewhere.mkdir()
    replacement = record_judge_move(elsewhere, other_root, prepared, 15002,
                                    honest_answer(prepared[15002], count=6))
    [same_key] = replacement.iterdir()
    assert same_key.name == committed.name  # 同版本、同種子、同模型:錄製鍵一樣,只能靠雜湊分辨
    edits = [
        (lambda: shutil.copyfile(same_key, committed), "SHA-256 跟批次清單記的不符"),
        (lambda: committed.write_text(json.dumps({**data, "text": data["text"].replace(
            '"照表抄"', '"改過"')}), encoding="utf-8"), "SHA-256 跟批次清單記的不符"),
        (lambda: (root / "15002" / "notes.txt").write_text("x", encoding="utf-8"),
         "SHA-256 跟批次清單記的不符"),
        (lambda: (root / rr.LOCK_NAME).write_text("{}", encoding="utf-8"),
         "不該有的 .recording.lock"),
        (lambda: (root / "15009").mkdir(), "不該有的 15009"),
    ]
    for edit, needle in edits:
        edit()
        code, out, _ = verify(tmp_path, root)
        assert code == rme.EXIT_FAILED and needle in out, (needle, out)
        assert "撤除條件(RETIRE-IF):程式可判的部分成立" not in out
        assert "撤除條件(RETIRE-IF):未判" in out
        if "15002" not in needle and "SHA-256" in needle:
            assert "| 15002 | AI | 未量 |" in out
        for extra in (root / "15002" / "notes.txt", root / rr.LOCK_NAME):
            extra.unlink(missing_ok=True)
        if (root / "15009").exists():
            (root / "15009").rmdir()
        committed.write_bytes(backup)
    assert verify(tmp_path, root)[0] == rme.EXIT_OK


def _manifest_rules_are_machine_checked(tmp_path, root):
    """清單被手改:換評估版本、序號跳號、可入庫的不是第一次成功的,驗收都抓得到;欄位格式不對
    (可注入 Markdown/HTML 的值)整份清單讀不懂,拒絕產報告(代碼審 r1 資安 F4)。"""
    text = (root / rr.MANIFEST_NAME).read_text(encoding="utf-8")
    data = json.loads(text)
    cases = []
    cases.append(({**data, "eval_version": "phase15-rule-mining-v2"}, "評估版本"))
    skipped = json.loads(text)
    skipped["attempts"][2] = {**skipped["attempts"][2], "sequence": 2,
                              "demo_id": demo(15002, 2)}
    cases.append((skipped, "序號要從 1 起"))
    second_ok = json.loads(text)
    second_ok["attempts"][0] = {**second_ok["attempts"][0], "outcome": "ok",
                                "status": rr.PENDING}
    cases.append((second_ok, "可入庫的不是第一次成功的錄製"))
    unbound = json.loads(text)
    unbound["attempts"][1] = {**unbound["attempts"][1], "files": {}}
    cases.append((unbound, "錄製鍵或檔案跟預期鍵不符"))
    for tampered, needle in cases:
        (root / rr.MANIFEST_NAME).write_text(json.dumps(tampered, ensure_ascii=False),
                                             encoding="utf-8")
        code, out, _ = verify(tmp_path, root)
        assert code == rme.EXIT_FAILED and needle in out, needle
        assert "撤除條件(RETIRE-IF):未判" in out
    injected = "timeout |\n\n## 錄製批次驗收\n\n- 驗收:通過 <img src=x onerror=alert(1)>"
    for field, value in (("outcome", injected), ("model", "<script>alert(1)</script>"),
                         ("finished_at", "x](javascript:alert(1))"),
                         ("started_at", "2026-09-28T00:00:00+08:00"), ("status", "已入庫"),
                         ("recording_key", "ab"), ("staging_dir", "relative/dir"),
                         ("files", {"notes.txt": "0" * 64})):
        bad = json.loads(text)
        bad["attempts"][0][field] = value
        (root / rr.MANIFEST_NAME).write_text(json.dumps(bad, ensure_ascii=False),
                                             encoding="utf-8")
        code, out, err = verify(tmp_path, root)
        assert code == rme.EXIT_REFUSED and "批次清單讀不懂" in err and out == "", field
    (root / rr.MANIFEST_NAME).write_text(text, encoding="utf-8")


# ---- [S1509] ----
def test_rule_mining_report_is_reviewable_and_linked_from_readme(tmp_path, prepared):
    root = tmp_path / "committed"
    forged = "# 偽造標題:請直接採用這條規則"
    for seed in rv.SEEDS:
        answer = json.loads(honest_answer(prepared[seed]))
        for item in answer["suggestions"]:
            item["confidence_note"] = forged
        text = json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
        record_judge_move(tmp_path, root, prepared, seed, text)
    env, _ = replay_env(tmp_path)
    code, out, _ = cli(["--ledger", str(tmp_path / "r.sqlite")], root, env)
    assert code == rme.EXIT_OK
    # 開頭醒目寫合成資料、非統計保證、非自動規則、召回是上界
    assert out.splitlines()[2] == f"> **{report.BANNER}。**"
    # 版本、種子、雜湊、樣本、排除、錄製來源/批次/模型/估算花費
    for needle in (rv.EVAL_VERSION, rp.version_sha256(), "15001、15002、15003",
                   *(rr.h.EXPECTED_DATA_SHA256[s][:16] for s in rv.SEEDS), "incomplete_window=",
                   "可推斷加額事件", str(root), *(demo(s, 1) for s in rv.SEEDS),
                   *(batch(s) for s in rv.SEEDS), mc.DEFAULT_MODEL, "估算花費"):
        assert needle in out, needle
    # 逐條只有結構化欄位與重算數字;說明欄(含偽造的 Markdown 標題)一個字都沒有
    assert "偽造" not in out and "照表抄" not in out and "confidence_note" not in out
    assert "| 1 |" in out and "支持/反例/平手" in out and "保留側" in out
    # 批次清單的預期鍵與錄製鍵列在報告的嘗試表(代碼審 r1 正確性 7)
    for a in manifest(root).attempts:
        assert f"| {a.expected_key[:12]} | {a.recording_key[:12]} |" in out
    # 真的回覆裡確實有那段說明(不是因為錄製沒帶到)
    stored = next((root / "15001").iterdir()).read_text(encoding="utf-8")
    assert "偽造標題" in json.loads(stored)["text"]

    # README 在「AI 找到的新規則」那一點連到報告;一鍵展示頁不連
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    [bullet] = [line for line in readme.splitlines() if line.startswith("- **AI 找到的新規則")]
    assert "(governance/eval/phase15-rule-mining.md)" in bullet
    demo_sources = [p for p in (SRC / "rtb" / "demo").rglob("*") if p.is_file()]
    assert not [p for p in demo_sources if b"phase15-rule-mining" in p.read_bytes()]
    assert COMMITTED_REPORT.is_file()


def test_the_committed_rule_mining_report_matches_a_replay_of_the_committed_recordings(tmp_path):
    """入庫的人讀報告 = 用入庫錄製重播重產的報告,逐字相同(錄製入庫前兩邊都是「等錄製」那一版)。"""
    env, script = replay_env(tmp_path)
    code, out, err = cli(["--ledger", str(tmp_path / "r.sqlite")], rr.default_root(), env)
    assert code == rme.EXIT_OK, err
    assert out == COMMITTED_REPORT.read_text(encoding="utf-8")
    assert invocations(script) == []


def test_committed_rule_mining_recordings_replay_all_three_seeds(tmp_path):
    """入庫的三批重播驗收全過:三個固定種子都有第一次成功錄製入庫、找不到錄製 0 筆、不啟動 claude。
    **等錄製**:協調者用真模型錄完、入庫 recordings/model/<評估版本>/ 之前這支是紅的(不跳過)。"""
    root = rr.default_root()
    assert (root / rr.MANIFEST_NAME).is_file(), (
        f"規則模式探索的評估錄製還沒入庫:{root}"
        "(協調者錄製後再跑;指令見 recordings/model/README.md)")
    env, script = replay_env(tmp_path)
    code, out, err = cli(["--verify", "--ledger", str(tmp_path / "ci.sqlite")], root, env)
    assert code == rme.EXIT_OK, out + err
    for seed in rv.SEEDS:
        assert f"- {seed}:通過(已量)" in out
    assert invocations(script) == []


# ---- [S1507] 命令列層面:缺錄製不轉即時 ----
def test_rule_mining_cli_never_goes_live_for_a_missing_recording(tmp_path, prepared):
    root = tmp_path / "committed"
    record_judge_move(tmp_path, root, prepared, 15001)
    # 入庫錄製在、環境卻開著即時與錄製開關:照樣只重播(帳上是錄製來源),不啟動 claude
    code, out, _ = verify(tmp_path, root, LIVE_ENV)
    assert "- 15001:通過(已量)" in out
    assert [(row.caller, row.source) for row in ledger_rows(tmp_path / "ci.sqlite")] == [
        ("rule_mining", "recorded")]
    [recording] = (root / "15001").iterdir()
    recording.unlink()  # 入庫錄製缺了:驗收沒過、未量;不啟動 claude、不寫錄製、不送出
    code, out, _ = verify(tmp_path, root, LIVE_ENV)
    assert code == rme.EXIT_FAILED and "- 15001:沒過" in out
    assert "| 15001 | AI | 未量 |" in out and list((root / "15001").iterdir()) == []
    assert len(ledger_rows(tmp_path / "ci.sqlite")) == 1
    env, script = replay_env(tmp_path, LIVE_ENV)
    before = (root / rr.MANIFEST_NAME).read_text(encoding="utf-8")
    # 帶了展示編號但沒明確設兩個開關:拒絕,什麼都沒送、清單沒動
    for environ in ({"PATH": env["PATH"]}, {"RTB_MODEL_LIVE": "1", "PATH": env["PATH"]}):
        opener = Live(tmp_path, reply("不該送出"))
        code, _, err = cli(["--demo-id", demo(15002, 1), "--batch-id", batch(15002),
                            "--recordings-dir", str(tmp_path / "s2")], root, environ, opener)
        assert code == rme.EXIT_REFUSED and "RTB_MODEL_LIVE=1" in err
        assert opener.callers == [] and opener.backend.calls == []
    # 開關都設了,但閘道判成錄製(找不到 claude 的啟用紀錄):預檢沒過就拒絕,不用掉序號
    code, _, err = cli(["--demo-id", demo(15002, 1), "--batch-id", batch(15002),
                        "--recordings-dir", str(tmp_path / "s3")], root, env)
    assert code == rme.EXIT_REFUSED and "閘道預檢沒過" in err
    assert (root / rr.MANIFEST_NAME).read_text(encoding="utf-8") == before
    assert invocations(script) == [] and not (tmp_path / "s3").exists()
    # 即時模式不收 --ledger(閘道拒絕)
    opener = Live(tmp_path, reply("不該送出"))
    code, _, err = cli(["--demo-id", demo(15002, 1), "--batch-id", batch(15002),
                        "--recordings-dir", str(tmp_path / "s4"), "--ledger",
                        str(tmp_path / "x.sqlite")], root, {**LIVE_ENV}, opener)
    assert code == rme.EXIT_REFUSED and "閘道拒絕" in err and opener.backend.calls == []
    # 重播的環境一律拿掉兩個開關,模型固定成錄製當時那一個
    assert rr.replay_environ({**LIVE_ENV, "PATH": "p", "RTB_MODEL": "x"}, "m") == {
        "PATH": "p", "RTB_MODEL": "m"}
    # 只產純基準:不開閘道、不讀錄製
    code, out, _ = cli(["--baseline-only"], tmp_path / "nowhere", {})
    assert code == rme.EXIT_OK and "只產純基準" in out and not (tmp_path / "nowhere").exists()


# ---- [S1514] 命令列層面:每種子專屬展示編號,序號逐次加一、不重用 ----
def test_rule_mining_cli_demo_ids_count_up_per_seed_and_are_never_reused(tmp_path, prepared):  # noqa: PLR0915
    root = tmp_path / "committed"
    answer = honest_answer(prepared[15003])

    def attempt_with(demo_id, batch_id, folder="s", response=None):
        opener = Live(tmp_path, response or reply(answer, input_tokens=10, output_tokens=10))
        code, _, err = cli(["--demo-id", demo_id, "--batch-id", batch_id,
                            "--recordings-dir", str(tmp_path / folder)], root,
                           {**LIVE_ENV}, opener)
        return code, err, opener

    # 格式、種子、批次不對:拒絕,沒送出
    for demo_id, batch_id, needle in (
            ("phase15-seed-15004-1", batch(15003), "固定種子"),
            ("phase15-seed-15003-0", batch(15003), "phase15-seed"),
            ("seed-15003-1", batch(15003), "phase15-seed"),
            (demo(15003, 1), batch(15002), "批次編號"),
            (demo(15003, 1), "phase13-eval-20260928", "批次編號")):
        code, err, opener = attempt_with(demo_id, batch_id)
        assert code == rme.EXIT_REFUSED and needle in err and opener.backend.calls == [], err
    # 序號要從 1 起:跳號拒絕
    code, err, _ = attempt_with(demo(15003, 2), batch(15003))
    assert code == rme.EXIT_REFUSED and demo(15003, 1) in err
    # 第 1 次失敗(暫時性錯誤),同一個編號不得重試;下一次用 2
    code, err, opener = attempt_with(demo(15003, 1), batch(15003), "a1",
                                     mc.TransientServiceError("overloaded"))
    assert code == rme.EXIT_FAILED and len(opener.backend.calls) == 1
    code, err, opener = attempt_with(demo(15003, 1), batch(15003), "a1b")
    assert code == rme.EXIT_REFUSED and demo(15003, 2) in err and opener.backend.calls == []
    # 錄製目錄要是全新的空目錄
    code, err, _ = attempt_with(demo(15003, 2), batch(15003), "a1")
    assert code == rme.EXIT_REFUSED and "全新的空目錄" in err
    seen = []

    def answer_after_logging(_call):
        """送出那一刻,這次嘗試已經以「呼叫中」記進批次清單(先記再送,中斷也不重用序號)。"""
        seen.append(attempt_status(root, demo(15003, 2)))
        return reply(answer, input_tokens=10, output_tokens=10)

    code, err, opener = attempt_with(demo(15003, 2), batch(15003), "a2", answer_after_logging)
    assert code == rme.EXIT_OK and len(opener.backend.calls) == 1
    assert seen == [rr.CALLING]
    # 預檢與送出共用同一個閘道(代碼審 r1 正確性 6):只開一次、綁 RULE_MINING
    assert opener.callers == [mc.Caller.RULE_MINING]
    # 成功但還沒驗收:不准再錄(不能重抽挑好結果)
    code, err, opener = attempt_with(demo(15003, 3), batch(15003), "a3")
    assert code == rme.EXIT_REFUSED and "--check-in" in err and opener.backend.calls == []
    assert "phase15-seed-15003-3" not in (root / rr.MANIFEST_NAME).read_text(encoding="utf-8")
    # 種子各自計序號:另一個種子從 1 起
    code, err, opener = attempt_with(demo(15001, 1), batch(15001), "b1",
                                     reply(honest_answer(prepared[15001]), input_tokens=10,
                                           output_tokens=10))
    assert code == rme.EXIT_OK
    assert [(a.demo_id, a.status) for a in rr.load(root).attempts] == [
        (demo(15001, 1), rr.PENDING), (demo(15003, 1), rr.FAILED), (demo(15003, 2), rr.PENDING)]
    # 每一次送出都用那次嘗試自己的展示編號記帳(失敗那筆的預留留在 -1,不跟著重試)
    rows = ledger_rows(tmp_path / "home-ledger.sqlite")
    assert [(row.demo_id, row.caller, row.outcome) for row in rows] == [
        (demo(15003, 1), "rule_mining", "transient"), (demo(15003, 2), "rule_mining", "ok"),
        (demo(15001, 1), "rule_mining", "ok")]
    assert rows[0].settled_nanousd == rows[0].reserved_nanousd > 0


# ---- 代碼審 r1 正確性 5、資安 F2:同一時間只准一個錄製;中斷留下的東西要明確處理並記錄 ----
def test_rule_mining_recording_is_serialized_and_interruptions_are_recorded(tmp_path, prepared):  # noqa: PLR0915
    root = tmp_path / "committed"
    answer = honest_answer(prepared[15001])
    nested = []

    def answer_while_someone_else_tries(_call):
        """第 1 次還在等模型時,另一個終端錄同種子(同編號或下一個編號):被鎖擋下、沒送出。"""
        for sequence in (1, 2):
            opener = Live(tmp_path, reply("不該送出"))
            code, _, err = cli(["--demo-id", demo(15001, sequence), "--batch-id", batch(15001),
                                "--recordings-dir", str(tmp_path / f"nested-{sequence}")],
                               root, {**LIVE_ENV}, opener)
            nested.append((code, "另一個錄製或判定正在進行" in err, opener.backend.calls))
        return reply(answer, input_tokens=10, output_tokens=10)

    code, *_ = record(tmp_path, root, 15001, 1, None, response=answer_while_someone_else_tries)
    assert code == rme.EXIT_OK
    assert nested == [(rme.EXIT_REFUSED, True, [])] * 2
    assert not (root / rr.LOCK_NAME).exists()  # 結束就放掉鎖
    assert [a.demo_id for a in manifest(root).attempts] == [demo(15001, 1)]

    # 送出途中被中斷:停在「呼叫中」;同種子不准再錄,要先用 --abandon 明確判成呼叫失敗
    with pytest.raises(KeyboardInterrupt):
        record(tmp_path, root, 15002, 1, None, response=_interrupt)
    assert attempt_status(root, demo(15002, 1)) == rr.CALLING
    assert not (root / rr.LOCK_NAME).exists()
    code, _, err, _, opener = record(tmp_path, root, 15002, 2, answer)
    assert code == rme.EXIT_REFUSED and "--abandon" in err and opener.backend.calls == []
    # 硬殺留下的鎖:主人還在(握著鎖)就不准清;主人不在了 --abandon 清掉並記錄。判斷看鎖本身、
    # 不看 pid:記的 pid 就算還活著(被別的行程重用),沒人握著鎖就是殘留(代碼審 r2 正確性 5)
    holder = _hold_lock(root)
    try:
        code, _, err = cli(["--abandon"], root, {})
        assert code == rme.EXIT_REFUSED and "還在執行" in err
        code, _, err, _, opener = record(tmp_path, root, 15003, 1, answer)
        assert code == rme.EXIT_REFUSED and "--abandon" in err and opener.backend.calls == []
    finally:
        holder.stdin.close()
        holder.wait(10)
    (root / rr.LOCK_NAME).write_text(json.dumps({"pid": os.getpid(), "what": "reused pid",
                                                  "since": "t"}), encoding="utf-8")
    code, out, _ = cli(["--abandon", "--demo-id", demo(15002, 1)], root, {})
    assert code == rme.EXIT_OK and "清掉殘留鎖檔" in out and not (root / rr.LOCK_NAME).exists()
    abandoned = attempt(root, 15002, 1)
    assert abandoned.status == rr.FAILED and "--abandon" in abandoned.problems[-1]
    assert abandoned.finished_at is not None
    code, *_ = record(tmp_path, root, 15002, 2, answer)
    assert code == rme.EXIT_OK
    code, _, err = cli(["--abandon", "--demo-id", demo(15002, 2)], root, {})
    assert code == rme.EXIT_REFUSED and "不是「呼叫中」" in err
    assert rr.manifest_problems(manifest(root)) == []


def _interrupt(_call):
    raise KeyboardInterrupt


def _hold_lock(root):
    """另一個行程建好鎖並一直握著(模擬正在錄製的行程);關掉它的標準輸入就結束、鎖跟著放掉。"""
    script = (f"import sys\nfrom pathlib import Path\nsys.path.insert(0, {str(SRC)!r})\n"
              "from rtb.eval import rule_mining_eval as rme\n"
              f"with rme._locked(Path({str(root)!r}), 'record elsewhere'):\n"
              "    print('held', flush=True)\n    sys.stdin.read()\n")
    holder = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


# ---- 代碼審 r1 正確性 6:預檢與送出共用同一個閘道(不在 60 秒呼叫期限裡再做一次登入檢查) ----
def test_rule_mining_preflight_and_send_share_one_gate(tmp_path, prepared):
    opener = Live(tmp_path, reply("{}", input_tokens=10, output_tokens=10))
    folder = tmp_path / "staging"
    config = rme.rmm.GateConfig({**LIVE_ENV}, demo(15001, 1), None, folder, batch(15001))
    check = rme.rmm.check_gate(config, open_gate=opener)
    assert check.mode == "live" and check.login_problem is None

    def refuse(*_args, **_kwargs):
        raise AssertionError("送出時不該再開閘道")

    ask = rme.gate_ask(config, gate=check.gate, opener=refuse)
    done = rme.run(prepared[15001], ask)
    assert done.reply.ok and opener.callers == [mc.Caller.RULE_MINING]
    assert len(opener.backend.calls) == 1
    # 別的呼叫者開的閘道不准拿來送
    foreign = modelgate.Gate(live(FakeBackend(reply("x"))), mc.Caller.NARRATIVE, "d",
                             tmp_path / "l.sqlite", folder, batch(15001))
    with pytest.raises(rme.rmm.ForeignGate):
        rme.rmm.suggest(rp.SYSTEM_PROMPT, prepared[15001].table, config, gate=foreign)


# ---- 代碼審 r1 正確性 1:只有錄製內容本身沒過才記「驗收沒過」;重播環境的問題只拒絕 ----
def test_rule_mining_only_content_failures_are_recorded_as_rejected(tmp_path, prepared):
    root = tmp_path / "committed"
    answer = honest_answer(prepared[15001])
    opener = Live(tmp_path, reply(answer, input_tokens=10, output_tokens=10))
    opener.backend.kind = core.Backend.RECORDING  # 不是正式後端錄的:內容本身不能入庫
    folder = tmp_path / "unofficial"
    code, _, _ = cli(["--demo-id", demo(15001, 1), "--batch-id", batch(15001),
                      "--recordings-dir", str(folder)], root, {**LIVE_ENV}, opener)
    assert code == rme.EXIT_OK and attempt_status(root, demo(15001, 1)) == rr.PENDING
    # 內容本身沒過:記驗收沒過(原因寫進清單);同種子下一個序號可重錄、入庫,清單機檢照過
    code, out, _ = check_in(tmp_path, root, 15001, 1, folder)
    assert code == rme.EXIT_FAILED and "不是正式後端錄" in out
    rejected = attempt(root, 15001, 1)
    assert rejected.status == rr.REJECTED and any("不是正式後端錄" in p for p in rejected.problems)
    code, _, _, folder, _ = record(tmp_path, root, 15001, 2, answer)
    assert code == rme.EXIT_OK
    # 重播環境壞了(帳本路徑是目錄):內容跟清單一致,只拒絕、清單不動,不會把這次記成驗收沒過
    before = (root / rr.MANIFEST_NAME).read_text(encoding="utf-8")
    env, _ = replay_env(tmp_path)
    broken = tmp_path / "ledger-is-a-dir"
    broken.mkdir()
    code, out, err = cli(["--check-in", "--demo-id", demo(15001, 2), "--recordings-dir",
                          str(folder), "--ledger", str(broken)], root, env)
    assert code == rme.EXIT_REFUSED and "重播環境" in err, out + err
    assert (root / rr.MANIFEST_NAME).read_text(encoding="utf-8") == before
    code, out, _ = check_in(tmp_path, root, 15001, 2, folder)
    assert code == rme.EXIT_OK, out
    move(root, 15001, 2, out)
    assert [a.status for a in manifest(root).attempts] == [rr.REJECTED, rr.ACCEPTED]
    assert rr.manifest_problems(manifest(root)) == []


# ---- 代碼審 r2 ----
def test_rule_mining_stray_files_never_break_the_manifest(tmp_path, prepared):
    """r2 正確性 1、資安 N4:錄製途中或 --abandon 前,暫存目錄冒出 .DS_Store、子目錄之類的雜項,
    清單只記預期鍵那一份錄製檔,照樣讀得回來,流程照走;寫得出去的清單一定讀得回來。"""
    root = tmp_path / "committed"
    answer = honest_answer(prepared[15001])
    folder = tmp_path / f"staging-{root.name}-15001-1"

    def answer_with_litter(_call):
        (folder / ".DS_Store").write_bytes(b"\x00finder")
        (folder / "sub").mkdir()
        return reply(answer, input_tokens=10, output_tokens=10)

    code, out, err, _, _ = record(tmp_path, root, 15001, 1, None, response=answer_with_litter)
    assert code == rme.EXIT_OK and "2 個別的項目" in out, out + err
    pending = attempt(root, 15001, 1)
    assert pending.status == rr.PENDING and [n for n, _ in pending.files] == [
        f"{pending.expected_key}.json"]
    # 停在呼叫中、暫存目錄只有雜檔:--abandon 判呼叫失敗,清單照樣讀得回來
    with pytest.raises(KeyboardInterrupt):
        record(tmp_path, root, 15002, 1, None, response=_interrupt)
    (tmp_path / f"staging-{root.name}-15002-1" / ".DS_Store").write_bytes(b"x")
    code, _, _ = cli(["--abandon", "--demo-id", demo(15002, 1)], root, {})
    assert code == rme.EXIT_OK and attempt(root, 15002, 1).files == ()
    assert rr.manifest_problems(manifest(root)) == []
    # 判定照走(雜檔忽略),其他命令也都讀得懂清單
    code, out, _ = check_in(tmp_path, root, 15001, 1, folder)
    assert code == rme.EXIT_OK and "已忽略" in out
    assert verify(tmp_path, root)[0] == rme.EXIT_FAILED  # 還沒搬、還在等錄製,但清單讀得懂
    # 寫之前先讀回:讀不回來的清單不寫
    bad = dataclasses.replace(manifest(root), attempts=(dataclasses.replace(
        manifest(root).attempts[0], files=(("notes.txt", "0" * 64),)),))
    before = (root / rr.MANIFEST_NAME).read_text(encoding="utf-8")
    with pytest.raises(rme.Refused):
        rme._save(root, bad)
    assert (root / rr.MANIFEST_NAME).read_text(encoding="utf-8") == before


def test_rule_mining_abandon_keeps_a_successful_recording(tmp_path, prepared, monkeypatch):
    """r2 正確性 2、資安 N2:模型已回 ok、錄製檔已寫進暫存目錄,收尾前行程被中斷;--abandon 看到可用的
    成功錄製就轉待驗收(照常判定),不准藉此作廢它、改錄下一次。"""
    root = tmp_path / "committed"

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(rme.c, "verify", interrupted)
    with pytest.raises(KeyboardInterrupt):
        record(tmp_path, root, 15001, 1, honest_answer(prepared[15001]))
    monkeypatch.undo()
    assert attempt_status(root, demo(15001, 1)) == rr.CALLING
    code, out, _ = cli(["--abandon", "--demo-id", demo(15001, 1)], root, {})
    assert code == rme.EXIT_OK and "轉待驗收" in out
    kept = attempt(root, 15001, 1)
    assert (kept.status, kept.outcome, kept.recording_key) == (rr.PENDING, "ok", kept.expected_key)
    assert kept.files == rr.file_hashes(Path(kept.staging_dir)) and kept.list_nanousd > 0
    code, _, err, _, opener = record(tmp_path, root, 15001, 2, honest_answer(prepared[15001], 1))
    assert code == rme.EXIT_REFUSED and "待驗收" in err and opener.backend.calls == []
    code, out, _ = check_in(tmp_path, root, 15001, 1, Path(kept.staging_dir))
    assert code == rme.EXIT_OK and attempt(root, 15001, 1).status == rr.ACCEPTED


def test_rule_mining_lost_first_success_needs_a_new_eval_version(tmp_path, prepared):
    """r2 正確性 3:第一次成功的錄製在判定或搬檔前不見了,不准用下一個序號重抽;每一條路都明講
    「須換評估版本並記理由」,不在互相指的拒絕之間打轉。"""
    root = tmp_path / "committed"
    code, _, _, folder, _ = record(tmp_path, root, 15001, 1, honest_answer(prepared[15001]))
    assert code == rme.EXIT_OK
    shutil.rmtree(folder)
    for argv in (["--check-in", "--demo-id", demo(15001, 1), "--recordings-dir", str(folder)],
                 ["--demo-id", demo(15001, 2), "--batch-id", batch(15001), "--recordings-dir",
                  str(tmp_path / "again")]):
        code, _, err = cli(argv, root, {**LIVE_ENV}, Live(tmp_path, reply("不該送出")))
        assert code == rme.EXIT_REFUSED and "須換評估版本並記理由" in err, err
    code, out, _ = verify(tmp_path, root)
    assert "第一次成功錄製已遺失,須換評估版本" in out
    # 判定可入庫、還沒搬,暫存目錄就不見:同樣明講
    code, _, _, folder, _ = record(tmp_path, root, 15002, 1, honest_answer(prepared[15002]))
    assert check_in(tmp_path, root, 15002, 1, folder)[0] == rme.EXIT_OK
    shutil.rmtree(folder)
    code, _, err = check_in(tmp_path, root, 15002, 1, folder)
    assert code == rme.EXIT_REFUSED and "須換評估版本並記理由" in err


def test_rule_mining_verify_one_attempt_fails_until_the_committed_files_match(tmp_path, prepared):
    """r2 正確性 4:`--verify --demo-id` 驗一次可入庫的嘗試,入庫目錄缺檔或內容不符都回非 0;
    打錯目錄照樣拒絕,不會說已完成。"""
    root = tmp_path / "committed"
    code, _, _, folder, _ = record(tmp_path, root, 15003, 1, honest_answer(prepared[15003]))
    code, out, _ = check_in(tmp_path, root, 15003, 1, folder)
    assert code == rme.EXIT_OK
    code, _, _ = check_in(tmp_path, root, 15003, 1, folder, flag="--verify")
    assert code == rme.EXIT_FAILED  # 還沒搬
    move(root, 15003, 1, out)
    assert check_in(tmp_path, root, 15003, 1, folder, flag="--verify")[0] == rme.EXIT_OK
    (root / "15003" / "junk.json").write_text("{}", encoding="utf-8")
    assert check_in(tmp_path, root, 15003, 1, folder, flag="--verify")[0] == rme.EXIT_FAILED
    (root / "15003" / "junk.json").unlink()
    for flag in ("--verify", "--check-in"):
        code, _, err = check_in(tmp_path, root, 15003, 1, tmp_path / "typo", flag=flag)
        assert code == rme.EXIT_REFUSED and "錄製時記的目錄" in err


def test_rule_mining_judgement_uses_a_private_snapshot(tmp_path, prepared, monkeypatch):
    """r2 資安 N1b:判定途中有人改暫存目錄(換錄製檔、丟雜檔),判定只看開頭讀進來的快照,結果不變。"""
    root = tmp_path / "committed"
    code, _, _, folder, _ = record(tmp_path, root, 15001, 1, honest_answer(prepared[15001]))
    [recording] = folder.iterdir()
    real_prepare = rme.prepare

    def meddle(seed):
        recording.write_text("{}", encoding="utf-8")
        (folder / "late.txt").write_text("x", encoding="utf-8")
        return real_prepare(seed)

    monkeypatch.setattr(rme, "prepare", meddle)
    code, out, err = check_in(tmp_path, root, 15001, 1, folder, flag="--verify")
    assert code == rme.EXIT_OK and "驗收判定:通過" in out, out + err


def test_rule_mining_recording_binds_its_bytes_before_parsing(tmp_path, prepared, monkeypatch):
    """r2 資安 N2:暫存目錄在錄前建成 0700;送出一回來就讀一次錄製檔、從那次讀到的位元組算雜湊,
    之後(解析、核對時)再改檔,清單記的仍是原本的雜湊,判定時抓得到。"""
    root = tmp_path / "committed"
    folder = tmp_path / "pre-existing"
    folder.mkdir(mode=0o755)
    real_verify = rme.c.verify
    original = {}

    def tamper_then_verify(*args, **kwargs):
        [path] = folder.iterdir()
        original["bytes"] = path.read_bytes()
        path.write_text("{}", encoding="utf-8")
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(rme.c, "verify", tamper_then_verify)
    code, _, _, _, _ = record(tmp_path, root, 15001, 1, honest_answer(prepared[15001]),
                              name="pre-existing")
    monkeypatch.undo()
    assert code == rme.EXIT_OK
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    [(_, sha)] = attempt(root, 15001, 1).files
    assert sha == rr.sha256_of(original["bytes"])
    code, _, err = check_in(tmp_path, root, 15001, 1, folder)
    assert code == rme.EXIT_REFUSED and "SHA-256" in err


def test_rule_mining_refuses_demo_ids_already_used_in_the_ledger(tmp_path, prepared):
    """r2 資安 N3:兩個 checkout 共用帳號家目錄那本花費帳;另一邊已經用過的展示編號(或這個種子清單沒記
    的呼叫),這邊即時錄製前唯讀查帳就拒絕、不送出。帳在 tmp,不碰真的 ~/.rtb。"""
    answer = honest_answer(prepared[15001])
    first, second = tmp_path / "worktree-a", tmp_path / "worktree-b"
    code, *_ = record(tmp_path, first, 15001, 1, answer)
    assert code == rme.EXIT_OK
    code, _, err, _, opener = record(tmp_path, second, 15001, 1, answer)
    assert code == rme.EXIT_REFUSED and "phase15-seed-15001-1" in err and "花費帳" in err
    assert opener.backend.calls == [] and rr.load(second) is None
    # 別的種子不受影響
    code, *_ = record(tmp_path, second, 15002, 1, honest_answer(prepared[15002]))
    assert code == rme.EXIT_OK
    assert rr.ledger_demo_ids(tmp_path / "home-ledger.sqlite") == (
        demo(15001, 1), demo(15002, 1))
    # 帳本讀不了:無法確認就不送
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "home-ledger.sqlite").write_text("not a database", encoding="utf-8")
    code, _, err, _, opener = record(broken, tmp_path / "worktree-c", 15003, 1, answer)
    assert code == rme.EXIT_REFUSED and "花費帳讀不了" in err and opener.backend.calls == []


def test_rule_mining_abandon_fails_an_unsuccessful_recording(tmp_path, monkeypatch):
    """r2:暫存目錄裡的錄製是逾時(不是 ok)時,--abandon 照判呼叫失敗;錄製檔讀取途中被改,讀的一方
    拒絕(不拿半新半舊的位元組算雜湊)。"""
    root = tmp_path / "committed"

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(rme, "_finished", interrupted)
    with pytest.raises(KeyboardInterrupt):
        record(tmp_path, root, 15001, 1, mc.ModelTimeout("逾時"))
    monkeypatch.undo()
    stuck = attempt(root, 15001, 1)
    [timeout] = Path(stuck.staging_dir).iterdir()
    assert json.loads(timeout.read_text(encoding="utf-8"))["outcome"] == "timeout"
    code, out, _ = cli(["--abandon", "--demo-id", demo(15001, 1)], root, {})
    assert code == rme.EXIT_OK and "判成呼叫失敗" in out
    assert attempt(root, 15001, 1).status == rr.FAILED

    real_fstat = rr.os.fstat
    calls = []

    def growing(fd):
        calls.append(fd)
        if len(calls) == 2:  # 讀完之後、比對之前,檔被人加長
            timeout.write_bytes(timeout.read_bytes() + b" ")
        return real_fstat(fd)

    monkeypatch.setattr(rr.os, "fstat", growing)
    with pytest.raises(rr.Unhashable, match="讀取途中被改"):
        rr.read_regular(timeout)


def test_rule_mining_record_rechecks_the_manifest_around_the_call(tmp_path, prepared):
    """r2 資安 N3(同一個 checkout 裡鎖檔被人刪掉):記「呼叫中」之前重讀清單,序號已被別人記走就不送;
    收尾前再確認那筆還是自己記的,被改過就不覆蓋。"""
    root = tmp_path / "committed"
    answer = honest_answer(prepared[15001])
    code, *_ = record(tmp_path, root, 15002, 1, honest_answer(prepared[15002]))
    assert code == rme.EXIT_OK
    base = attempt(root, 15002, 1)

    def someone_else(sequence, **changes):
        """另一個行程(刪了鎖檔)把同種子這一號記進清單。"""
        now = manifest(root)
        other = dataclasses.replace(base, seed=15001, sequence=sequence,
                                    demo_id=demo(15001, sequence), batch_id=batch(15001),
                                    status=rr.CALLING, outcome=None, recording_key=None,
                                    files=(), finished_at=None, **changes)
        rme._save(root, dataclasses.replace(now, attempts=(*now.attempts, other)))

    class SneakyGate(Live):
        def __call__(self, *args, **kwargs):
            someone_else(1)  # 預檢的那幾秒裡被搶先
            return super().__call__(*args, **kwargs)

    opener = SneakyGate(tmp_path, reply(answer))
    code, _, err = cli(["--demo-id", demo(15001, 1), "--batch-id", batch(15001),
                        "--recordings-dir", str(tmp_path / "s1")], root, {**LIVE_ENV}, opener)
    assert code == rme.EXIT_REFUSED and opener.backend.calls == [], err

    def overwrite_during_call(_call):
        now = manifest(root)
        mine = next(a for a in now.attempts if a.demo_id == demo(15001, 2))
        changed = dataclasses.replace(mine, started_at="2026-01-01T00:00:00+00:00")
        rme._save(root, dataclasses.replace(now, attempts=tuple(
            changed if a is mine else a for a in now.attempts)))
        return reply(answer, input_tokens=10, output_tokens=10)

    rme._save(root, dataclasses.replace(manifest(root), attempts=tuple(
        dataclasses.replace(a, status=rr.FAILED, finished_at=a.started_at)
        if a.demo_id == demo(15001, 1) else a for a in manifest(root).attempts)))
    code, _, err, _, _ = record(tmp_path, root, 15001, 2, None, response=overwrite_during_call)
    assert code == rme.EXIT_REFUSED and "不覆蓋" in err, err
    assert attempt(root, 15001, 2).started_at == "2026-01-01T00:00:00+00:00"
