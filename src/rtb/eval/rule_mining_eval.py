"""規則模式探索的單一種子執行與離線命令列(Phase 15 增量 2/3,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈模型建議、機械核對與人讀報告〉〈拆增量〉2、3)。

一個固定種子一批:生成歷史 → 探索集彙總表(增量 1)→ 完整提示位元組閘 → 經分析端窄入口
`rtb.analyzer.rule_mining_model` 呼叫模型**一次**(方向同 Phase 13 評估執行器依賴 AI 決策函式;
評估端不直接匯入模型閘道)→ 封閉 JSON 解析、去重、同探索集重算、保留側判定。

- 非 `ok` 的呼叫(缺錄製、逾時、上限拒絕、額度、超支、暫時性錯誤、花費帳忙碌…)一律記「呼叫失敗」,
  不解析、不計分、不重試;同種子要重錄換新展示編號。
- 提示超過本案上限就在呼叫前拒跑(不分塊、不截列)。
- 輸出只有核對結果與人讀報告;沒有正式決策、提案、DSP 寫入或 Issue 的路徑。匯入它就算送出點
  (邊界測試 `SENDING_ENTRIES`)。停用模型探勘時連同分析端窄入口一起撤(命令列在這支裡,一起撤),
  歷史錄製鍵與批次驗收留在 `rule_mining_recordings`,比較與報告留在 `rule_mining_report`。

命令列 `python -m rtb.eval.rule_mining_eval`(增量 3):
- 預設:重播入庫目錄(`recordings/model/<評估版本>/`,批次清單 `manifest.json` 加每種子一個子目錄)
  的三批,印人讀報告(含錄製批次驗收);還沒入庫的種子 AI 欄寫「等錄製」、未量,不呼叫模型。
- `--verify`:同上,驗收有任何問題(含等錄製)以結束代碼 1 結束;CI 用它,只重播。
- `--baseline-only`:只產純基準報告(不開閘道、不讀錄製)。
- 即時錄製一個種子:使用者環境明確設 `RTB_MODEL_LIVE=1`、`RTB_MODEL_RECORD=1`,帶 `--demo-id
  phase15-seed-<種子>-<序號>`、`--batch-id`、`--recordings-dir <全新空目錄>`;先過閘道預檢(判成
  錄製就拒絕,不用掉序號),再把這次嘗試記進批次清單、送出一次、記下結果。缺錄製絕不改走即時。
- 驗收與入庫:`--verify` 或 `--check-in` 加同一個 `--demo-id` 與 `--recordings-dir`,只准重播;入庫要
  唯一呼叫 `outcome=ok`、批次驗收過、重播找不到錄製 0 筆,每種子只准第一次成功錄製入庫。
- `--ledger` 只在錄製(重播)模式能用;即時模式的花費帳寫死在帳號家目錄那一本(閘道擋)。
"""

import argparse
import fcntl
import json
import os
import shlex
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TextIO

from rtb.analyzer import rule_mining_model as rmm
from rtb.eval import rule_mining_check as c
from rtb.eval import rule_mining_history as h
from rtb.eval import rule_mining_prompt as p
from rtb.eval import rule_mining_recordings as r
from rtb.eval import rule_mining_report as report
from rtb.eval import rule_mining_vocab as v

CALL_OK = "ok"
CALL_FAILED = "呼叫失敗"
Ask = Callable[[str, str], rmm.Reply]

EXIT_OK = 0
EXIT_FAILED = 1  # 驗收沒過、呼叫失敗
EXIT_REFUSED = 2  # 參數錯、閘道拒絕、前置條件不齊(什麼都沒送)


class PromptRefused(ValueError):
    """完整提示過大:拒跑,不呼叫。"""


@dataclass(frozen=True)
class Prepared:
    """一個種子送出前的一切:歷史、兩側重算器、完整彙總表與位元組數。"""

    seed: int
    history: h.History
    explore: c.Recounter
    holdout: c.Recounter
    table: str
    prompt_bytes: int


def prepare_from(inputs: p.SeedInputs) -> Prepared:
    """組表走預檢同一支(`seed_inputs`,探索側建表);重算器照同一份切側各建一個。"""
    return Prepared(inputs.history.seed, inputs.history,
                    c.Recounter(inputs.history, inputs.sides.explore),
                    c.Recounter(inputs.history, inputs.sides.holdout), inputs.table,
                    p.prompt_bytes(inputs.table))


def prepare(seed: int) -> Prepared:
    return prepare_from(p.seed_inputs(seed))


@dataclass(frozen=True)
class SeedRun:
    seed: int
    expected_key: str  # 依這份提示重算的錄製鍵(缺錄製時給人對)
    reply: rmm.Reply
    verification: c.Verification | None  # 呼叫失敗時沒有

    @property
    def status(self) -> str:
        return CALL_OK if self.reply.ok else CALL_FAILED


def run(prepared: Prepared, ask: Ask, *, model: str | None = None) -> SeedRun:
    """送出這一批(至多一次)並核對。提示過大丟 `PromptRefused`,什麼都沒送。"""
    problem = p.gate_problem(prepared.prompt_bytes)
    if problem is not None:
        raise PromptRefused(problem)
    expected = r.expected_key(p.SYSTEM_PROMPT, prepared.table, model)
    reply = ask(p.SYSTEM_PROMPT, prepared.table)
    if not reply.ok or reply.text is None:
        return SeedRun(prepared.seed, expected, reply, None)
    verification = c.verify(c.parse_reply(reply.text), prepared.explore, prepared.holdout)
    return SeedRun(prepared.seed, expected, reply, verification)


def gate_ask(config: rmm.GateConfig, notify: Callable[[str], None] | None = None,
             opener: rmm.GateOpener | None = None, gate: rmm.OpenedGate | None = None
             ) -> Ask:
    """經分析端窄入口送出:每呼叫一次就開一次規則模式探索的閘道、送一次;給了 gate(`check_gate`
    已預檢過的閘道)就用它送,不另開。opener 只給測試換成綁假後端的閘道;產品碼一律用閘道本身。"""
    if gate is not None:
        return partial(rmm.suggest, config=config, notify=notify, gate=gate)
    if opener is None:
        return partial(rmm.suggest, config=config, notify=notify)
    return partial(rmm.suggest, config=config, notify=notify, open_gate=opener)


# ---- 命令列 ----
class Refused(Exception):
    """參數或前置條件不對:什麼都沒送、批次清單沒動。"""


@dataclass(frozen=True)
class _Io:
    out: TextIO
    err: TextIO
    environ: Mapping[str, str]
    root: Path
    opener: rmm.GateOpener | None

    def notify(self, text: str) -> None:
        print(text, file=self.err)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="AI 找規則模式的離線評估:預設重播入庫錄製、印人讀報告(不呼叫模型)")
    parser.add_argument("--baseline-only", action="store_true",
                        help="只產純基準報告:不開模型閘道、不讀錄製")
    parser.add_argument("--verify", action="store_true",
                        help="驗收(只准重播):沒過以結束代碼 1 結束;"
                             "帶 --demo-id 時驗那一次嘗試的目錄")
    parser.add_argument("--check-in", action="store_true",
                        help="驗收判定一次嘗試的錄製目錄(只重播、不搬檔):通過就在批次清單標成可入庫,"
                             "印出協調者手動搬的指令")
    parser.add_argument("--abandon", action="store_true",
                        help="清掉主人已不在的鎖檔;帶 --demo-id 時把停在呼叫中的那次"
                             "判成呼叫失敗並記錄")
    parser.add_argument("--demo-id", help="這次嘗試的展示編號 phase15-seed-<種子>-<序號>")
    parser.add_argument("--batch-id", help="即時錄製的批次編號 phase15-rule-mining-<種子>-YYYYMMDD")
    parser.add_argument("--recordings-dir", type=Path,
                        help="即時錄製、驗收、入庫時:這次嘗試的全新目錄(只放這一個種子);"
                             "重播報告時:入庫目錄(預設 recordings/model/<評估版本>)")
    parser.add_argument("--ledger", type=Path, help="只在錄製(重播)模式能用:花費帳換到別的路徑")
    return parser.parse_args(argv)


def _shown(folder: Path) -> str:
    base = r.default_root().parent.parent.parent  # 專案根
    return str(folder.relative_to(base)) if folder.is_relative_to(base) else str(folder)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _save(root: Path, manifest: r.Manifest) -> None:
    """批次清單原子地寫回(先寫暫存檔再改名)。寫之前先用讀取的同一套規則讀回一次:寫得出去的清單一定
    讀得回來(代碼審 r2:不能寫進自己讀不懂的值,讓所有命令都卡死)。"""
    text = r.dumps(manifest)
    try:
        r.loads(text)
    except r.ManifestUnreadable as bad:
        raise Refused(f"要寫的批次清單自己讀不回來({bad}):不寫") from bad
    root.mkdir(parents=True, exist_ok=True)
    target = root / r.MANIFEST_NAME
    temporary = root / f".{r.MANIFEST_NAME}.{os.getpid()}.tmp"
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _load(root: Path) -> r.Manifest | None:
    try:
        return r.load(root)
    except r.ManifestUnreadable as bad:
        raise Refused(f"批次清單讀不懂:{bad}") from bad


def _with(manifest: r.Manifest, attempt: r.Attempt) -> r.Manifest:
    """換掉(或加上)同一個展示編號的那次嘗試。"""
    others = [a for a in manifest.attempts if a.demo_id != attempt.demo_id]
    ordered = sorted([*others, attempt], key=lambda a: (a.seed, a.sequence))
    return replace(manifest, attempts=tuple(ordered))


def _rows(manifest: r.Manifest | None) -> tuple[report.AttemptRow, ...]:
    if manifest is None:
        return ()
    return tuple(report.AttemptRow(a.seed, a.sequence, a.demo_id, a.batch_id, a.model,
                                   a.expected_key, a.recording_key, a.started_at, a.finished_at,
                                   a.outcome, a.status)
                 for a in manifest.attempts)


def _replay(prepared: Prepared, folder: Path, attempt: r.Attempt, args: argparse.Namespace,
            io: _Io) -> SeedRun:
    """重播一次嘗試的錄製(閘道一定判錄製:拿掉即時開關、不給展示編號)。"""
    config = rmm.GateConfig(r.replay_environ(io.environ, attempt.model), None, args.ledger,
                            folder)
    return run(prepared, gate_ask(config, io.notify), model=attempt.model)


def _source(attempt: r.Attempt, done: SeedRun) -> report.Source:
    return report.Source(attempt.demo_id, done.reply.batch_id or attempt.batch_id, attempt.model,
                         attempt.finished_at, done.reply.list_nanousd)


def _layout_problems(root: Path, manifest: r.Manifest | None) -> list[str]:
    """入庫目錄只准有批次清單與判定可入庫的種子子目錄(殘留的鎖檔、沒有判定紀錄的種子目錄都算問題)。"""
    if not root.is_dir():
        return []
    allowed = {r.MANIFEST_NAME} | {str(seed) for seed in v.SEEDS
                                   if r.accepted(manifest, seed) is not None}
    return [f"入庫目錄裡有不該有的 {entry.name}(清單沒有它的可入庫紀錄,或是殘留的檔)"
            for entry in sorted(root.iterdir()) if entry.name not in allowed]


def _report_all(args: argparse.Namespace, io: _Io) -> int:
    """重播入庫目錄的三批,印人讀報告(驗收結果在最後一節)。撤除比較只用驗收通過的批次。"""
    root = args.recordings_dir or io.root
    manifest = None if args.baseline_only else _load(root)
    if args.baseline_only:
        overall: list[str] = []
    elif manifest is None:
        overall = ["批次清單不存在:三批都等錄製", *_layout_problems(root, None)]
    else:
        overall = r.manifest_problems(manifest) + _layout_problems(root, manifest)
    reports, per_seed = [], []
    for seed in v.SEEDS:
        inputs = p.seed_inputs(seed)
        ai, problems = _seed_ai(inputs, root, manifest, args, io, bool(overall))
        reports.append(report.seed_report(inputs, ai))
        per_seed.append((seed, ai, problems))
    if args.baseline_only:
        checks = ["- 只產純基準:沒有讀錄製、沒有開模型閘道"]
        failed = False
    else:
        failed = bool(overall) or any(problems for _, _, problems in per_seed)
        checks = [f"- 驗收:{'沒過' if failed else '通過'}(目錄 {_shown(root)})"]
        checks += [f"- {problem}" for problem in overall]
        for seed, ai, problems in per_seed:
            checks.append(f"- {seed}:{'沒過' if problems else '通過'}({_ai_status_text(ai)})")
            checks += [f"  - {problem}" for problem in problems if problem != report.WAITING]
        if any(ai.status == report.WAITING for _, ai, _ in per_seed):
            checks.append("- 等錄製的種子由協調者用真模型錄製;指令見 recordings/model/README.md"
                          "〈規則模式探索的評估錄製〉")
    context = report.Context(_shown(root), _rows(manifest), tuple(checks))
    print(report.render(reports, context), end="", file=io.out)
    return EXIT_FAILED if args.verify and failed else EXIT_OK


def _ai_status_text(ai: report.AiResult) -> str:
    return ai.status if ai.detail is None else f"{ai.status}:{ai.detail}"


def _committed_problems(folder: Path, attempt: r.Attempt) -> list[str]:
    """入庫目錄的內容要恰等於清單記的檔與雜湊(同版本替換、改寫回覆都抓得到)。"""
    if not folder.is_dir():
        return [f"判定可入庫、還沒搬進入庫目錄 {_shown(folder)}(照判定時印的指令手動搬)"]
    try:
        found = r.file_hashes(folder)
    except r.Unhashable as bad:
        return [f"入庫目錄 {_shown(folder)}:{bad}"]
    if found != attempt.files:
        return [f"入庫目錄 {_shown(folder)} 的檔案或 SHA-256 跟批次清單記的不符"]
    return []


def _seed_ai(inputs: p.SeedInputs, root: Path, manifest: r.Manifest | None,
             args: argparse.Namespace, io: _Io, blocked: bool
             ) -> tuple[report.AiResult, list[str]]:
    if args.baseline_only:
        return report.AiResult(report.BASELINE_ONLY), []
    seed = inputs.history.seed
    attempt = r.accepted(manifest, seed)
    if attempt is None:
        tried = r.attempts_of(manifest, seed) if manifest is not None else ()
        lost = [a for a in tried if a.status == r.PENDING and _lost(a)]
        detail = ("第一次成功錄製已遺失,須換評估版本" if lost
                  else f"已試 {len(tried)} 次、沒有判定可入庫" if tried else None)
        return report.AiResult(report.WAITING, detail=detail), [report.WAITING]
    folder = root / str(seed)
    problems = _committed_problems(folder, attempt)
    if problems:
        return report.AiResult(report.UNVERIFIED, detail="入庫目錄跟清單不符"), problems
    prepared = prepare_from(inputs)
    expected = r.expected_key(p.SYSTEM_PROMPT, prepared.table, attempt.model)
    problems = r.batch_problems(folder, attempt, expected)
    done = _replay(prepared, folder, attempt, args, io)
    if done.verification is None:
        problems.append(f"重播沒有讀到可用的錄製({done.reply.outcome})")
    if problems or blocked:  # 驗收沒過的批次不進比較與撤除判斷
        return report.AiResult(report.UNVERIFIED, detail="批次驗收沒過"), problems
    return report.AiResult(report.MEASURED, done.verification, _source(attempt, done)), problems


def _attempt_for(demo_id: str) -> tuple[int, int]:
    parts = r.demo_parts(demo_id)
    if parts is None or parts[0] not in v.SEEDS:
        raise Refused(f"展示編號要是 phase15-seed-<固定種子>-<序號>(種子 {v.SEEDS}):{demo_id!r}")
    return parts


LOST = ("種子 {seed} 的第一次成功錄製({demo})已遺失(暫存錄製目錄或錄製檔不見):依計劃不准用下一個"
        "序號重抽,也不得同版本另批取代,須換評估版本並記理由")


def _lost(attempt: r.Attempt) -> bool:
    """結果 ok 的嘗試,錄製檔卻已不在暫存目錄(或收尾時就沒讀到)。"""
    if not attempt.files:
        return True
    folder = Path(attempt.staging_dir)
    try:
        return r.read_regular(folder / attempt.files[0][0]) is None
    except r.Unhashable:
        return False  # 還在,只是被換成別的東西:判定時當參數錯


def _fresh(folder: Path) -> Path:
    """即時錄製的暫存目錄:不存在就建成 0700;已存在要是真的目錄(不是符號連結)而且是空的,並改成
    0700(代碼審 r2 資安 N2:錄製檔寫下到算雜湊之間,別人不能往裡面讀寫)。"""
    try:
        found = os.lstat(folder)
    except FileNotFoundError:
        folder.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(folder, 0o700)
        found = os.lstat(folder)
    if not stat.S_ISDIR(found.st_mode) or any(folder.iterdir()):
        raise Refused(f"錄製目錄要是全新的空目錄(不收符號連結):{folder}")
    os.chmod(folder, 0o700)
    return folder


# ---- 鎖:同一時間只准一個錄製或判定寫批次清單(代碼審 r1 正確性 5、資安 F2;r2 正確性 5) ----
def _lock_owner(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return f"pid {int(data['pid'])}、{data['what']}、{data['since']}"
    except (OSError, ValueError, KeyError, TypeError):
        return "讀不懂"


@contextmanager
def _locked(root: Path, what: str) -> Iterator[None]:
    """入庫目錄的排他鎖。內容(pid、隨機碼、做什麼、何時)先寫進暫存檔並對它 flock,再用 os.link 原子地
    放到鎖的路徑:鎖一出現就帶著內容,而且持有者活著時 flock 一直在。主人死了作業系統自動放掉 flock,
    所以判斷主人還在不在看 flock、不看 pid(pid 被重用也不會誤判)。已有鎖就拒絕。"""
    root.mkdir(parents=True, exist_ok=True)
    path = root / r.LOCK_NAME
    token = json.dumps({"pid": os.getpid(), "nonce": uuid.uuid4().hex, "what": what,
                        "since": _now()}, ensure_ascii=False)
    temporary = root / f".{r.LOCK_NAME}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, token.encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            os.link(temporary, path)
        except FileExistsError as busy:
            raise Refused(f"另一個錄製或判定正在進行(鎖檔 {path}:{_lock_owner(path)});"
                          "確定沒有行程在跑,用 --abandon 清掉並記錄") from busy
        finally:
            temporary.unlink(missing_ok=True)
        try:
            yield
        finally:
            with suppress(OSError):
                if os.stat(path).st_ino == os.fstat(fd).st_ino:
                    path.unlink()
    finally:
        os.close(fd)


def _clear_stale_lock(path: Path) -> str | None:
    """鎖的主人已不在(拿得到 flock)才清,而且清的是剛驗過的同一個檔;主人還在就拒絕。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        owner = _lock_owner(path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as held:
            raise Refused(f"鎖檔的主人還在執行({owner}):不清") from held
        if os.stat(path).st_ino == os.fstat(fd).st_ino:
            path.unlink()
        return f"清掉殘留鎖檔({owner})"
    finally:
        os.close(fd)


def _abandon(args: argparse.Namespace, io: _Io) -> int:
    """明確處理中斷留下的東西,並記錄:鎖檔主人已不在就清掉;帶 --demo-id 時處理那次停在「呼叫中」的
    嘗試——暫存目錄裡已有可用的成功錄製就轉「待驗收」(照常判定,不能藉此作廢成功的錄製),真的沒有
    可用錄製檔才判成呼叫失敗;原因與時間都寫進清單。"""
    notes = [note for note in (_clear_stale_lock(io.root / r.LOCK_NAME),) if note]
    if args.demo_id is not None:
        with _locked(io.root, f"abandon {args.demo_id}"):
            notes.append(_abandon_attempt(args.demo_id, io, notes[0] if notes else None))
    print("\n".join(notes or ["沒有要處理的鎖檔或嘗試"]), file=io.out)
    return EXIT_OK


def _abandon_attempt(demo_id: str, io: _Io, lock_note: str | None) -> str:
    manifest = _load(io.root)
    attempt = next((a for a in (manifest.attempts if manifest else ()) if a.demo_id == demo_id),
                   None)
    if manifest is None or attempt is None or attempt.status != r.CALLING:
        raise Refused(f"批次清單裡 {demo_id} 不是「呼叫中」:沒有要處理的")
    try:
        data, files = r.recorded_file(Path(attempt.staging_dir), attempt.expected_key)
    except r.Unhashable:
        data, files = None, ()
    recording = r.usable_recording(data, attempt)
    when = f"中斷:{_now()} 以 --abandon 處理" + (f"({lock_note})" if lock_note else "")
    if recording is not None:
        outcome = CALL_OK
        changed = replace(attempt, finished_at=_now(), outcome=outcome,
                          recording_key=attempt.expected_key,
                          list_nanousd=getattr(recording, "list_nanousd", 0), status=r.PENDING,
                          files=files,
                          problems=(*attempt.problems, when + ";暫存目錄已有成功的錄製,轉待驗收"))
        note = f"{demo_id} 的暫存目錄已有成功的錄製:轉待驗收,接著用 --check-in 判定"
    else:
        changed = replace(attempt, finished_at=_now(), status=r.FAILED, files=files,
                          problems=(*attempt.problems, when + ";沒有可用的錄製,判成呼叫失敗"))
        note = (f"{demo_id} 判成呼叫失敗;同種子下一次用 "
                f"{r.demo_id_of(attempt.seed, attempt.sequence + 1)}")
    _save(io.root, _with(manifest, changed))
    return note


def _manifest_for(seed: int, sequence: int, root: Path) -> r.Manifest:
    """即時錄製前對批次清單的檢查:清單本身沒問題、種子還沒判定可入庫、沒有待驗收或呼叫中的嘗試、
    序號是下一個。第一次成功的錄製遺失時明講要換評估版本。"""
    manifest = _load(root) or r.new_manifest()
    problems = r.manifest_problems(manifest)
    if problems:
        raise Refused("批次清單有問題,先處理:" + ";".join(problems))
    if r.accepted(manifest, seed) is not None:
        raise Refused(f"種子 {seed} 已判定可入庫:每種子只准第一次成功錄製入庫,換批要換評估版本")
    for attempt in r.attempts_of(manifest, seed):
        if attempt.status == r.PENDING and _lost(attempt):
            raise Refused(LOST.format(seed=seed, demo=attempt.demo_id))
    for status, hint in ((r.PENDING, "先用 --check-in 做驗收判定"),
                         (r.CALLING, "上一次還沒有結果;確定中斷了就用 --abandon 處理")):
        waiting = [a.demo_id for a in r.attempts_of(manifest, seed) if a.status == status]
        if waiting:
            raise Refused(f"種子 {seed} 有{status}的嘗試 {waiting}:{hint}")
    wanted = r.next_sequence(manifest, seed)
    if sequence != wanted:
        raise Refused(f"種子 {seed} 下一次要用序號 {wanted}({r.demo_id_of(seed, wanted)});"
                      "序號逐次加一、不重用")
    return manifest


def _ledger_clean(check: rmm.GateCheck, manifest: r.Manifest, seed: int) -> None:
    """跨 checkout 的防線(代碼審 r2 資安 N3):即時模式寫的那本花費帳(帳號家目錄)唯讀查一次,這個種子
    有清單沒記的規則模式探索呼叫(含這次要用的展示編號)就拒絕。"""
    try:
        used = r.ledger_demo_ids(Path(check.gate.ledger))
    except r.LedgerProblem as bad:
        raise Refused(f"花費帳讀不了({type(bad).__name__}),無法確認展示編號沒用過:不送出") from bad
    known = {a.demo_id for a in r.attempts_of(manifest, seed)}
    stray = [demo for demo in used if (r.demo_parts(demo) or (0, 0))[0] == seed
             and demo not in known]
    if stray:
        raise Refused(f"花費帳顯示種子 {seed} 有批次清單沒記的呼叫 {stray}"
                      "(可能在別的 checkout 錄過):不送出")


def _gate_ready(config: rmm.GateConfig, io: _Io) -> rmm.GateCheck:
    """閘道預檢:判成即時、登入預檢過了才准記嘗試;否則拒絕,沒有送出、沒有用掉序號。回傳的閘道就是
    之後送出用的那一個(不另開)。"""
    try:
        check = (rmm.check_gate(config, notify=io.notify) if io.opener is None
                 else rmm.check_gate(config, notify=io.notify, open_gate=io.opener))
    except ValueError as refused:  # 閘道拒絕、不認得的模型
        raise Refused(f"閘道拒絕:{refused}") from refused
    for notice in check.notices:
        io.notify(notice)
    if check.mode != "live" or check.login_problem is not None:
        why = check.mode if check.login_problem is None else check.login_problem
        raise Refused(f"閘道預檢沒過({why}):沒有送出,也沒有用掉序號")
    return check


class _Snapshot:
    """送出一回來就讀一次預期鍵的錄製檔,位元組留在記憶體;雜湊只從這次讀到的位元組算
    (代碼審 r2 資安 N2:不在解析、核對之後才回頭再開檔)。"""

    def __init__(self, ask: Ask, folder: Path, expected: str) -> None:
        self._ask, self._folder, self._expected = ask, folder, expected
        self.files: tuple[tuple[str, str], ...] = ()
        self.problem: str | None = None

    def __call__(self, system: str, table: str) -> rmm.Reply:
        reply = self._ask(system, table)
        try:
            _data, self.files = r.recorded_file(self._folder, self._expected)
        except r.Unhashable as bad:
            self.problem = f"錄製檔讀不到可靠內容:{bad}"
        return reply


def _finished(started: r.Attempt, done: SeedRun, snapshot: _Snapshot) -> r.Attempt:
    """記結果:結果類別、錄製鍵、原價,以及預期鍵那一份錄製檔的 SHA-256(錄製當下綁定內容)。暫存目錄
    裡的其他項目不看、不記。呼叫成功卻沒讀到錄製檔時照記待驗收(沒有檔):第一次成功錄製遺失。"""
    problems = [x for x in (snapshot.problem,) if x]
    reply = done.reply
    if not (reply.ok and reply.mode == "live"):
        status = r.FAILED
        problems.append(reply.problem or reply.outcome)
    elif reply.key != started.expected_key:
        status = r.REJECTED  # 呼叫成功但錄到的不是預期鍵(提示或模型對不上),內容本身不能入庫
        problems.append("錄製鍵跟預期鍵不符")
    else:
        status = r.PENDING
    return replace(started, finished_at=_now(), outcome=reply.outcome, recording_key=reply.key,
                   list_nanousd=reply.list_nanousd, status=status, files=snapshot.files,
                   problems=tuple(problems))


def _record(args: argparse.Namespace, io: _Io) -> int:
    """即時錄製一個種子:持鎖,預檢全過才記嘗試、用同一個閘道送出一次、記結果與錄製內容雜湊。"""
    seed, sequence = _attempt_for(args.demo_id)
    if args.batch_id is None or r.batch_seed(args.batch_id) != seed:
        raise Refused(f"即時錄製要帶這個種子的批次編號 {r.BATCH_SHAPE}:{args.batch_id!r}")
    if not r.live_recording_requested(io.environ):
        raise Refused("即時錄製要使用者在環境明確設 RTB_MODEL_LIVE=1 與 RTB_MODEL_RECORD=1;"
                      "缺錄製不會自動改走即時")
    with _locked(io.root, f"record {args.demo_id}"):
        manifest = _manifest_for(seed, sequence, io.root)
        prepared = prepare(seed)
        gate = p.gate_problem(prepared.prompt_bytes)
        if gate is not None:
            raise Refused(f"拒跑:{gate}")
        if args.recordings_dir is None:
            raise Refused("即時錄製要明寫 --recordings-dir,給一個持久的全新空目錄"
                          "(不在入庫目錄底下)")
        config = rmm.GateConfig(io.environ, args.demo_id, args.ledger, args.recordings_dir,
                                args.batch_id)
        check = _gate_ready(config, io)
        _ledger_clean(check, manifest, seed)
        folder = _fresh(args.recordings_dir)  # 預檢都過了才建(0700)
        model = r.model_of(io.environ)
        expected = r.expected_key(p.SYSTEM_PROMPT, prepared.table, model)
        started = r.Attempt(seed, sequence, args.demo_id, args.batch_id, model, expected, _now(),
                            None, None, None, 0, r.CALLING, r.canonical_dir(folder))
        _save(io.root, _with(_manifest_for(seed, sequence, io.root), started))  # 先記再送
        snapshot = _Snapshot(gate_ask(config, io.notify, gate=check.gate), folder, expected)
        done = run(prepared, snapshot, model=model)
        finished = _finished(started, done, snapshot)
        _save(io.root, _with(_still_mine(io.root, started), finished))
    return _recorded(finished, done, folder, io)


def _recorded(finished: r.Attempt, done: SeedRun, folder: Path, io: _Io) -> int:
    seed, sequence = finished.seed, finished.sequence
    extra = r.other_entries(folder, finished.expected_key)
    lines = [f"種子 {seed} 第 {sequence} 次({finished.demo_id}):結果 {done.reply.outcome},"
             f"狀態 {finished.status};估算花費(原價)"
             f"{done.reply.list_nanousd / 10**9:.6f} 美元"]
    if extra:
        lines.append(f"提醒:暫存目錄還有 {extra} 個別的項目,不記進清單、判定時忽略")
    if finished.status == r.PENDING and finished.files:
        lines.append(f"下一步:--check-in --demo-id {finished.demo_id} --recordings-dir "
                     f"{shlex.quote(finished.staging_dir)}")
    elif finished.status == r.PENDING:
        lines.append(LOST.format(seed=seed, demo=finished.demo_id))
    else:
        lines.append(f"不入庫、不算撤除判斷;重錄用 {r.demo_id_of(seed, sequence + 1)}(新目錄)")
    print("\n".join(lines), file=io.out)
    return EXIT_OK if finished.status == r.PENDING and finished.files else EXIT_FAILED


def _still_mine(root: Path, started: r.Attempt) -> r.Manifest:
    """收尾前重讀清單:同一個展示編號還是自己記的那筆(開始時間與暫存目錄都對)才准寫結果。"""
    manifest = _load(root)
    mine = next((a for a in (manifest.attempts if manifest else ())
                 if a.demo_id == started.demo_id), None)
    if manifest is None or mine is None or (mine.started_at, mine.staging_dir, mine.status) != (
            started.started_at, started.staging_dir, r.CALLING):
        raise Refused(f"批次清單裡 {started.demo_id} 已經不是這個行程記的那筆:不覆蓋,請人工查")
    return manifest


def _move_steps(root: Path, attempt: r.Attempt) -> list[str]:
    """判定通過後給協調者手動搬的指令(程式不寫入庫目錄的錄製檔,比照 Phase 13/14)。"""
    target = shlex.quote(_shown(root / str(attempt.seed)))
    staging = Path(attempt.staging_dir)
    return ["下一步(協調者手動搬;程式不寫入庫目錄的錄製檔):", f"  mkdir {target}",
            *(f"  cp {shlex.quote(str(staging / name))} {target}/" for name, _ in attempt.files),
            "  PYTHONPATH=src .venv/bin/python -m rtb.eval.rule_mining_eval --verify"]


def _same_dir(staging: Path, attempt: r.Attempt) -> None:
    if r.canonical_dir(staging) != attempt.staging_dir:
        raise Refused(f"--recordings-dir 不是 {attempt.demo_id} 錄製時記的目錄 "
                      f"{attempt.staging_dir}:清單沒動")


def _snapshot_of(staging: Path, attempt: r.Attempt) -> bytes:
    """判定的快照(代碼審 r2 資安 N1):目錄本身要是錄製時記的那個真目錄(不收符號連結),預期錄製檔用
    lstat 確認是一般檔、只讀一次進記憶體、雜湊等於清單記的。之後只在這份快照上判定;目錄層級的問題
    (連結、缺檔、型別不對、讀取途中被改、雜湊不符)一律是參數錯,拒絕、清單不動。"""
    _same_dir(staging, attempt)
    try:
        found = os.lstat(staging)
    except FileNotFoundError:
        found = None
    if found is None or not attempt.files:
        raise Refused(LOST.format(seed=attempt.seed, demo=attempt.demo_id))
    if not stat.S_ISDIR(found.st_mode):
        raise Refused(f"錄製目錄 {staging} 不是真的目錄(不收符號連結):清單沒動")
    [(name, sha)] = attempt.files
    try:
        data = r.read_regular(staging / name)
    except r.Unhashable as bad:
        raise Refused(f"錄製目錄 {staging}:{bad};清單沒動") from bad
    if data is None:
        raise Refused(LOST.format(seed=attempt.seed, demo=attempt.demo_id))
    if r.sha256_of(data) != sha:
        raise Refused(f"錄製目錄 {staging} 的錄製檔 SHA-256 跟錄製當下記的不符:清單沒動")
    return data


def _check_in(args: argparse.Namespace, io: _Io) -> int:
    """驗收判定一次嘗試(只重播,不搬檔):`--check-in` 且通過就把那次標成可入庫、印手動搬的指令;
    `--verify --demo-id` 只判、不寫。"""
    _attempt_for(args.demo_id)
    staging = args.recordings_dir
    if staging is None:
        raise Refused("驗收要帶這次嘗試的 --recordings-dir(錄製時記的那個目錄)")
    with _locked(io.root, f"check-in {args.demo_id}") if args.check_in else nullcontext():
        return _judge(args, io, staging)


def _already_accepted(attempt: r.Attempt, args: argparse.Namespace, io: _Io) -> int:
    """重跑判定:入庫目錄內容等於清單記錄就是已完成;還沒搬就再印一次搬的指令(`--check-in` 回 0,
    `--verify` 回 1:入庫目錄缺檔或內容不符都不算過)。暫存錄製已不見又還沒搬,明講遺失。"""
    _same_dir(args.recordings_dir, attempt)
    problems = _committed_problems(io.root / str(attempt.seed), attempt)
    if not problems:
        print(f"{attempt.demo_id} 已判定可入庫,入庫目錄內容也等於清單記錄:已完成", file=io.out)
        return EXIT_OK
    if _lost(attempt):
        raise Refused(LOST.format(seed=attempt.seed, demo=attempt.demo_id))
    lines = [f"{attempt.demo_id} 已判定可入庫,但入庫目錄還沒好:", *(f"- {x}" for x in problems),
             *_move_steps(io.root, attempt)]
    print("\n".join(lines), file=io.out)
    return EXIT_FAILED if args.verify else EXIT_OK


def _judge(args: argparse.Namespace, io: _Io, staging: Path) -> int:
    manifest = _load(io.root)
    attempt = next((a for a in (manifest.attempts if manifest else ())
                    if a.demo_id == args.demo_id), None)
    if manifest is None or attempt is None:
        raise Refused(f"批次清單沒有 {args.demo_id} 這次嘗試(要先用即時錄製記下)")
    if attempt.status == r.ACCEPTED:
        return _already_accepted(attempt, args, io)
    if attempt.status != r.PENDING:
        raise Refused(f"{args.demo_id} 的狀態是{attempt.status}:只有結果 ok、待驗收的嘗試能判定")
    if r.accepted(manifest, attempt.seed) is not None:
        raise Refused(f"種子 {attempt.seed} 已判定可入庫:每種子只准第一次成功錄製入庫")
    data = _snapshot_of(staging, attempt)
    extra = r.other_entries(staging, attempt.expected_key)
    with tempfile.TemporaryDirectory(prefix="rule-mining-judge-") as private:  # 0700
        snapshot = Path(private) / attempt.files[0][0]
        snapshot.write_bytes(data)
        problems, done = _judge_snapshot(snapshot.parent, attempt, args, io)
    lines = [f"種子 {attempt.seed}({args.demo_id})驗收判定:{'沒過' if problems else '通過'}",
             *(f"- {problem}" for problem in problems)]
    if extra:
        lines.append(f"- 提醒:暫存目錄還有 {extra} 個別的項目,已忽略(判定只看清單記的那份錄製)")
    if done.verification is not None:
        ver = done.verification
        lines.append(f"- AI 有效 {ver.n} 條、無效提交 {ver.invalid_count} 筆、核對失敗 "
                     f"{ver.mismatches} 筆")
    if args.check_in and problems:
        _save(io.root, _with(manifest, replace(attempt, status=r.REJECTED,
                                               problems=tuple(problems))))
        lines.append("錄製內容沒過驗收:這次記成驗收沒過,同種子用下一個序號重錄")
    elif args.check_in:
        _save(io.root, _with(manifest, replace(attempt, status=r.ACCEPTED)))
        lines += _move_steps(io.root, attempt)
    print("\n".join(lines), file=io.out)
    return EXIT_FAILED if problems else EXIT_OK


def _judge_snapshot(folder: Path, attempt: r.Attempt, args: argparse.Namespace,
                    io: _Io) -> tuple[list[str], SeedRun]:
    """只在行程自己的 0700 快照目錄上做內容驗收與重播;內容一致卻讀不到是重播環境的問題,拒絕。"""
    prepared = prepare(attempt.seed)
    expected = r.expected_key(p.SYSTEM_PROMPT, prepared.table, attempt.model)
    problems = r.batch_problems(folder, attempt, expected)
    done = _replay(prepared, folder, attempt, args, io)
    if done.verification is None:
        if not problems:
            raise Refused(f"重播讀不到錄製({done.reply.outcome}),但內容跟清單記的一致:"
                          "多半是重播環境的問題,清單沒動")
        problems.append(f"重播沒有讀到可用的錄製({done.reply.outcome})")
    return problems, done


def command(argv: list[str] | None = None, *, out: TextIO | None = None,
            err: TextIO | None = None, environ: Mapping[str, str] | None = None,
            root: Path | None = None, opener: rmm.GateOpener | None = None) -> int:
    """命令列本體。root(入庫目錄)與 opener(即時錄製用的閘道)只給測試換;產品碼用預設。"""
    args = _parse(argv)
    io = _Io(out or sys.stdout, err or sys.stderr, os.environ if environ is None else environ,
             root or r.default_root(), opener)
    try:
        return _dispatch(args, io)
    except (Refused, PromptRefused) as refused:
        print(f"拒絕開始:{refused}", file=io.err)
        return EXIT_REFUSED


def _dispatch(args: argparse.Namespace, io: _Io) -> int:
    if args.baseline_only and any((args.verify, args.check_in, args.abandon, args.demo_id,
                                   args.batch_id, args.recordings_dir, args.ledger)):
        raise Refused("--baseline-only 不接其他參數")
    if sum((args.check_in, args.verify, args.abandon)) > 1:
        raise Refused("--check-in、--verify、--abandon 一次只能用一個")
    if args.abandon:
        if args.batch_id or args.recordings_dir or args.ledger:
            raise Refused("--abandon 只接 --demo-id")
        return _abandon(args, io)
    if args.demo_id is None:
        if args.check_in or args.batch_id is not None:
            raise Refused("--check-in 與 --batch-id 要跟 --demo-id 一起用")
        return _report_all(args, io)
    if args.verify or args.check_in:
        if args.batch_id is not None:
            raise Refused("驗收只准重播:不要帶 --batch-id")
        return _check_in(args, io)
    return _record(args, io)


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(command(argv))


if __name__ == "__main__":
    main()
