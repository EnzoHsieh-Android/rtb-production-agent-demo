"""規則模式探索的歷史錄製鍵、批次清單與錄製批次驗收(Phase 15 增量 2/3,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈模型建議、機械核對與人讀報告〉〈回退〉)。

用字面 `Caller.RULE_MINING` 與評估版本的輸出上限,照模型用戶端的錄製鍵函式重算「這份系統提示加這張
彙總表」應有的錄製鍵;缺錄製時給人看預期鍵,批次驗收比對目錄鍵集合也用它。

增量 3 加批次清單(`manifest.json`,放在入庫根底下這一版評估的目錄,跟各種子的錄製子目錄並列)與
驗收(代碼審 r1 後改成「錄製當下就綁定、判定不搬檔」):
- 一個固定種子一批、一批只有一次模型呼叫;展示編號 `phase15-seed-<種子>-<序號>`,序號從 1 起逐次加一、
  不重用;批次編號 `phase15-rule-mining-<種子>-YYYYMMDD`。
- 清單記三種子、資料雜湊、評估版本與版本雜湊(系統提示在雜湊內)、每次嘗試的展示編號、批次、模型、
  預期鍵、錄製鍵、開始/結束時間、結果類別與狀態;非 `ok` 記「呼叫失敗」、不入庫、不算撤除判斷。
- 即時錄製收尾時把這次嘗試的暫存錄製目錄(絕對路徑)、錄製鍵與目錄裡每個檔的 SHA-256 記進清單;
  驗收判定只收同一個目錄、同樣的檔與雜湊,對不上是參數錯、不改清單。
- 每種子只准第一次成功錄製入庫:可入庫的那次必須是該種子第一次 `outcome=ok` 且內容沒被驗收退回的
  嘗試,判定後不得再錄;入庫目錄的檔案雜湊要恰等於清單記的(同版本替換可機檢);換批要換評估版本
  (目錄名跟著換,舊批保留)。搬進入庫目錄由協調者手動做,程式不寫入庫的錄製檔。
- 一批的驗收:目錄裡只有同一批的錄製檔、失敗類 0 份、正式後端錄的(模型用戶端門面的共用驗收),
  錄製鍵集合恰等於依當次資料/提示/模型/輸出上限重算的預期集合(一個鍵),每份 `caller=rule_mining`、
  `outcome=ok`、模型與批次跟清單相符;重播找不到錄製 0 筆由命令列另查。

這支是**歷史驗證**的一半:停用模型探勘或回退增量 2/3 時,送出路徑(分析端窄入口與評估端的探勘
執行器)撤掉,這支照留,舊錄製的鍵仍能重算、舊批仍能驗收、舊帳仍計上限([S1517]);所以它只經模型
用戶端門面取錄製鍵、錄製檔驗收與呼叫者列舉,不匯入模型閘道或任何送出入口,也不寫任何檔。
"""

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rtb import modelclient as mc
from rtb.eval import rule_mining_history as h
from rtb.eval import rule_mining_prompt as p
from rtb.eval import rule_mining_vocab as v

MANIFEST_NAME = "manifest.json"
LOCK_NAME = ".recording.lock"  # 錄製與判定寫批次清單時的排他鎖(命令列建、放)
MANIFEST_VERSION = 1
DEMO_PATTERN = re.compile(r"phase15-seed-(\d{5})-([1-9]\d{0,5})")
BATCH_PATTERN = re.compile(r"phase15-rule-mining-(\d{5})-(\d{8})")
BATCH_SHAPE = "phase15-rule-mining-<種子>-YYYYMMDD"

RECORDING_NAME = re.compile(r"[0-9a-f]{64}\.json")
HEX64 = re.compile(r"[0-9a-f]{64}")
MODEL_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")

# 一次嘗試的狀態
CALLING = "呼叫中"  # 送出前先記;行程中斷就停在這裡,要用 --abandon 明確判成呼叫失敗
FAILED = "呼叫失敗"
PENDING = "待驗收"
ACCEPTED = "可入庫"  # 驗收判定通過:入庫目錄應恰有這次記下的檔案(協調者手動搬)
REJECTED = "驗收沒過"  # 只給錄製內容本身沒過驗收(內容已跟錄製當下的雜湊綁住)
STATUSES = frozenset({CALLING, FAILED, PENDING, ACCEPTED, REJECTED})


def expected_key(system: str, table: str, model: str | None = None) -> str:
    """這份提示在規則模式探索呼叫者、評估版本輸出上限下的錄製鍵(模型預設模型用戶端的預設模型)。"""
    return mc.recording_key(mc.Caller.RULE_MINING, model or mc.DEFAULT_MODEL, system, table,
                            v.MAX_OUTPUT_TOKENS)


def default_root() -> Path:
    """這一版評估的入庫目錄:入庫根底下以評估版本命名(換版就是新目錄,舊批原地保留)。"""
    return mc.default_recordings_dir() / v.EVAL_VERSION


def model_of(environ: Mapping[str, str]) -> str:
    """這一趟要用的模型(跟閘道判模式時讀同一個環境變數)。"""
    return environ.get(mc.MODEL_ENV) or mc.DEFAULT_MODEL


def live_recording_requested(environ: Mapping[str, str]) -> bool:
    """使用者在環境明確要了即時加錄製(兩個開關都是 1);閘道另判其餘前置條件。"""
    return environ.get(mc.LIVE_ENV) == "1" and environ.get(mc.RECORD_ENV) == "1"


def replay_environ(environ: Mapping[str, str], model: str) -> dict[str, str]:
    """重播用的環境:拿掉即時與錄製開關(缺錄製絕不改走即時),模型固定成錄製當時那一個。"""
    kept = {k: val for k, val in environ.items() if k not in (mc.LIVE_ENV, mc.RECORD_ENV)}
    kept[mc.MODEL_ENV] = model
    return kept


def demo_parts(demo_id: str) -> tuple[int, int] | None:
    """展示編號 → (種子, 序號);格式不對回 None。"""
    found = DEMO_PATTERN.fullmatch(demo_id)
    return (int(found.group(1)), int(found.group(2))) if found else None


def demo_id_of(seed: int, sequence: int) -> str:
    return f"phase15-seed-{seed}-{sequence}"


def batch_seed(batch_id: str) -> int | None:
    found = BATCH_PATTERN.fullmatch(batch_id)
    return int(found.group(1)) if found else None


# ---- 批次清單 ----
@dataclass(frozen=True)
class Attempt:
    seed: int
    sequence: int
    demo_id: str
    batch_id: str
    model: str
    expected_key: str
    started_at: str  # UTC ISO 8601
    finished_at: str | None
    outcome: str | None  # 模型呼叫的結果類別(ok、timeout…);呼叫中是 None
    recording_key: str | None
    list_nanousd: int
    status: str
    staging_dir: str  # 這次嘗試的暫存錄製目錄(絕對路徑,錄製當下記)
    files: tuple[tuple[str, str], ...] = ()  # 錄製收尾時目錄裡每個檔 (檔名, SHA-256),依檔名排序
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    eval_version: str
    version_sha256: str
    seeds: tuple[tuple[int, str], ...]  # (種子, 資料雜湊)
    attempts: tuple[Attempt, ...] = ()


class ManifestUnreadable(ValueError):
    """批次清單讀不懂(格式、型別或值域不對)。報告與驗收都不接受讀不懂的清單。"""


def new_manifest() -> Manifest:
    return Manifest(v.EVAL_VERSION, p.version_sha256(),
                    tuple((seed, h.EXPECTED_DATA_SHA256[seed]) for seed in v.SEEDS))


def dumps(manifest: Manifest) -> str:
    data: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION, "eval_version": manifest.eval_version,
        "version_sha256": manifest.version_sha256,
        "seeds": [{"seed": seed, "data_sha256": sha} for seed, sha in manifest.seeds],
        "attempts": [{**asdict(a), "files": dict(a.files), "problems": list(a.problems)}
                     for a in manifest.attempts]}
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


_OPTIONAL = (str, type(None))
_ATTEMPT_FIELDS: dict[str, type | tuple[type, ...]] = {
    "seed": int, "sequence": int, "demo_id": str, "batch_id": str, "model": str,
    "expected_key": str, "started_at": str, "finished_at": _OPTIONAL, "outcome": _OPTIONAL,
    "recording_key": _OPTIONAL, "list_nanousd": int, "status": str, "staging_dir": str,
    "files": dict, "problems": list}
_OUTCOMES = frozenset(o.value for o in mc.Outcome)


def utc_time(text: str) -> bool:
    """清單的時間:ISO 8601、帶 UTC(+00:00)。"""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return False
    return moment.utcoffset() == timedelta(0) and moment.isoformat() == text


def _plain(text: str) -> bool:
    return not any(unicodedata.category(ch) in ("Cc", "Cf", "Cs") for ch in text)


def _values_problem(raw: dict[str, Any]) -> str | None:
    """值域:會寫進報告或下一步指令的欄位先驗格式(代碼審 r1 資安 F4)。"""
    checks = (
        ("seed/sequence", raw["seed"] >= 0 and raw["sequence"] >= 1),
        ("demo_id", bool(DEMO_PATTERN.fullmatch(raw["demo_id"]))),
        ("batch_id", bool(BATCH_PATTERN.fullmatch(raw["batch_id"]))),
        ("model", bool(MODEL_PATTERN.fullmatch(raw["model"]))),
        ("expected_key", bool(HEX64.fullmatch(raw["expected_key"]))),
        ("recording_key", raw["recording_key"] is None
         or bool(HEX64.fullmatch(raw["recording_key"]))),
        ("started_at", utc_time(raw["started_at"])),
        ("finished_at", raw["finished_at"] is None or utc_time(raw["finished_at"])),
        ("outcome", raw["outcome"] is None or raw["outcome"] in _OUTCOMES),
        ("list_nanousd", raw["list_nanousd"] >= 0),
        ("status", raw["status"] in STATUSES),
        ("staging_dir", Path(raw["staging_dir"]).is_absolute() and _plain(raw["staging_dir"])),
        ("files", all(isinstance(k, str) and RECORDING_NAME.fullmatch(k) and isinstance(x, str)
                      and HEX64.fullmatch(x) for k, x in raw["files"].items())),
        ("problems", all(isinstance(x, str) for x in raw["problems"])),
    )
    return next((name for name, ok in checks if not ok), None)


def _attempt(raw: object) -> Attempt:
    if not isinstance(raw, dict) or set(raw) != set(_ATTEMPT_FIELDS):
        raise ManifestUnreadable("嘗試的欄位不對")
    for name, kind in _ATTEMPT_FIELDS.items():
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, kind):
            raise ManifestUnreadable(f"嘗試的 {name} 型別不對")
    wrong = _values_problem(raw)
    if wrong is not None:
        raise ManifestUnreadable(f"嘗試的 {wrong} 值不合格式")
    return Attempt(**{**raw, "files": tuple(sorted(raw["files"].items())),
                      "problems": tuple(raw["problems"])})


def loads(text: str) -> Manifest:
    try:
        data = json.loads(text)
    except ValueError as bad:
        raise ManifestUnreadable("批次清單不是 JSON") from bad
    if not isinstance(data, dict) or set(data) != {
            "manifest_version", "eval_version", "version_sha256", "seeds", "attempts"}:
        raise ManifestUnreadable("批次清單的頂層欄位不對")
    if data["manifest_version"] != MANIFEST_VERSION:
        raise ManifestUnreadable("批次清單版本不對")
    seeds, attempts = data["seeds"], data["attempts"]
    if not (isinstance(data["eval_version"], str) and isinstance(data["version_sha256"], str)
            and isinstance(seeds, list) and isinstance(attempts, list)):
        raise ManifestUnreadable("批次清單的型別不對")
    if not all(isinstance(s, dict) and set(s) == {"seed", "data_sha256"}
               and type(s["seed"]) is int and isinstance(s["data_sha256"], str) for s in seeds):
        raise ManifestUnreadable("批次清單的種子列不對")
    return Manifest(data["eval_version"], data["version_sha256"],
                    tuple((s["seed"], s["data_sha256"]) for s in seeds),
                    tuple(_attempt(a) for a in attempts))


def load(root: Path) -> Manifest | None:
    """讀入庫目錄的批次清單;不存在回 None(等錄製),讀不懂丟 `ManifestUnreadable`。"""
    path = root / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as bad:
        raise ManifestUnreadable("批次清單讀不到") from bad


def attempts_of(manifest: Manifest, seed: int) -> tuple[Attempt, ...]:
    return tuple(a for a in manifest.attempts if a.seed == seed)


def accepted(manifest: Manifest | None, seed: int) -> Attempt | None:
    """這個種子判定可入庫的那一次(沒有回 None)。"""
    if manifest is None:
        return None
    return next((a for a in attempts_of(manifest, seed) if a.status == ACCEPTED), None)


def next_sequence(manifest: Manifest | None, seed: int) -> int:
    """這個種子下一次嘗試要用的序號(從 1 起逐次加一)。"""
    return 1 + (max((a.sequence for a in attempts_of(manifest, seed)), default=0)
                if manifest is not None else 0)


def _attempt_problems(seed: int, a: Attempt) -> list[str]:
    problems = []
    if a.demo_id != demo_id_of(seed, a.sequence):
        problems.append(f"{seed}:展示編號 {a.demo_id} 跟序號 {a.sequence} 不符")
    if batch_seed(a.batch_id) != seed:
        problems.append(f"{seed}:批次 {a.batch_id} 不是 {BATCH_SHAPE}")
    ok = a.outcome == mc.Outcome.OK.value
    if a.status in (PENDING, ACCEPTED, REJECTED) and not ok:
        problems.append(f"{seed}:第 {a.sequence} 次結果是 {a.outcome},不能是{a.status}")
    if a.status == FAILED and ok:
        problems.append(f"{seed}:第 {a.sequence} 次結果是 ok,不能記呼叫失敗")
    names = [name for name, _ in a.files]
    expected_file = f"{a.expected_key}.json"
    # 待驗收可以沒有檔(收尾時錄製檔已不見:第一次成功錄製遺失,判定與再錄都會明講要換評估版本)
    if a.status in (PENDING, ACCEPTED) and (
            a.recording_key != a.expected_key
            or names not in ([expected_file], [] if a.status == PENDING else [expected_file])):
        problems.append(f"{seed}:第 {a.sequence} 次的錄製鍵或檔案跟預期鍵不符")
    if names not in ([], [expected_file]):  # 清單只記預期鍵那一份錄製檔(代碼審 r2)
        problems.append(f"{seed}:第 {a.sequence} 次記了預期鍵以外的檔")
    return problems


def _seed_problems(seed: int, attempts: tuple[Attempt, ...]) -> list[str]:
    problems = []
    if [a.sequence for a in attempts] != list(range(1, len(attempts) + 1)):
        problems.append(f"{seed}:序號要從 1 起逐次加一、不重用")
    for attempt in attempts:
        problems += _attempt_problems(seed, attempt)
    done = [a for a in attempts if a.status == ACCEPTED]
    if len(done) > 1:
        problems.append(f"{seed}:可入庫 {len(done)} 次(每種子只准一次)")
    first_ok = next((a for a in attempts if a.outcome == mc.Outcome.OK.value
                     and a.status != REJECTED), None)
    if done and done[0] is not first_ok:
        problems.append(f"{seed}:可入庫的不是第一次成功的錄製")
    if done and attempts[-1] is not done[0]:
        problems.append(f"{seed}:判定可入庫後又錄了一次(換批要換評估版本)")
    return problems


def manifest_problems(manifest: Manifest) -> list[str]:
    """批次清單本身的機檢:評估版本與版本雜湊是現行的、三個固定種子與資料雜湊、每種子的序號與入庫規則。
    """
    problems = []
    if (manifest.eval_version, manifest.version_sha256) != (v.EVAL_VERSION, p.version_sha256()):
        problems.append(f"批次清單的評估版本 {manifest.eval_version} 或版本雜湊不是現行的"
                        f" {v.EVAL_VERSION}:舊批只作歷史核對,現行版本要另錄")
    expected = tuple((seed, h.EXPECTED_DATA_SHA256[seed]) for seed in v.SEEDS)
    if manifest.seeds != expected:
        problems.append("批次清單的種子或資料雜湊不是固定三種子")
    stray = sorted({a.seed for a in manifest.attempts} - set(v.SEEDS))
    if stray:
        problems.append(f"批次清單有固定種子以外的嘗試:{stray}")
    for seed in v.SEEDS:
        problems += _seed_problems(seed, attempts_of(manifest, seed))
    return problems


# ---- 目錄內容與一批的驗收 ----
class Unhashable(ValueError):
    """目錄或檔不是一般的目錄/一般檔(符號連結、子目錄…),或讀取途中被改:算不出可靠的內容雜湊。"""


def file_hashes(directory: Path) -> tuple[tuple[str, str], ...]:
    """(入庫目錄用)目錄裡每個一般檔的 (檔名, SHA-256),依檔名排序;目錄不存在回空。入庫目錄要恰等於
    清單記的,所以任何非一般檔都丟 `Unhashable`。"""
    if not directory.is_dir():
        return ()
    found = []
    for path in sorted(directory.iterdir()):
        data = read_regular(path)
        if data is None:
            raise Unhashable(f"{path.name} 不是一般檔")
        found.append((path.name, sha256_of(data)))
    return tuple(found)


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path) -> bytes | None:
    """讀一個一般檔的全部位元組(只開一次檔):不存在回 None;符號連結、不是一般檔、讀取途中大小或
    修改時間變了,丟 `Unhashable`。判定與收尾都只用這一次讀到的位元組算雜湊、做快照(代碼審 r2)。"""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise Unhashable(f"{path.name} 不是一般檔(符號連結或目錄)")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as bad:
        raise Unhashable(f"{path.name} 打不開({type(bad).__name__})") from bad
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        data = handle.read()
        after = os.fstat(handle.fileno())
    if ((opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_ino, before.st_size,
                                                                 before.st_mtime_ns)
            or (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)
            or len(data) != opened.st_size):
        raise Unhashable(f"{path.name} 讀取途中被改")
    return data


def canonical_dir(folder: Path) -> str:
    """暫存錄製目錄的正規寫法:上層目錄解開(macOS 的 /tmp 之類),最後一段照字面;最後一段本身是符號
    連結的,比對時另外用 lstat 拒絕。"""
    absolute = Path(os.path.abspath(folder))
    return str(absolute.parent.resolve() / absolute.name)


def recorded_file(folder: Path, expected: str) -> tuple[bytes | None, tuple[tuple[str, str], ...]]:
    """暫存目錄裡預期鍵那一份錄製檔:讀一次,回(位元組, 清單要記的檔與雜湊)。其他項目一律不看、
    不記(代碼審 r2:.DS_Store 之類的雜檔不能讓清單寫進自己讀不懂的值)。"""
    name = f"{expected}.json"
    data = read_regular(folder / name)
    return data, (() if data is None else ((name, sha256_of(data)),))


def usable_recording(data: bytes | None, attempt: Attempt) -> object | None:
    """位元組是不是一份可用的錄製:讀得懂、預期鍵、rule_mining、ok、模型與批次跟這次嘗試相符。"""
    if data is None:
        return None
    try:
        recording = mc.validated(Path(f"{attempt.expected_key}.json"), json.loads(data))
    except (mc.NoRecording, ValueError):
        return None
    wanted = (attempt.expected_key, mc.Caller.RULE_MINING.value, mc.Outcome.OK.value,
              attempt.model, attempt.batch_id)
    got = (recording.key, recording.caller, recording.outcome, recording.model,
           recording.batch_id)
    return recording if got == wanted else None


def other_entries(folder: Path, expected: str) -> int:
    """暫存目錄裡預期錄製檔以外的項目數(只給提醒用,不影響結果)。"""
    try:
        return sum(1 for entry in folder.iterdir() if entry.name != f"{expected}.json")
    except OSError:
        return 0


class LedgerProblem(ValueError):
    """花費帳讀不了(壞檔、表沒建齊、SQLite 錯誤):無法確認展示編號沒用過。"""


def ledger_demo_ids(ledger: Path) -> tuple[str, ...]:
    """唯讀查一本花費帳裡規則模式探索用過的展示編號(用花費帳既有的唯讀開法,不自己開 SQL);帳本
    不存在回空,讀不了丟 `LedgerProblem`。跨 checkout 唯一的共同紀錄就是帳號家目錄那一本(代碼審 r2
    資安 N3)。"""
    if not ledger.exists():
        return ()
    try:
        reader = mc.ModelLedgerView(ledger)
        try:
            with reader.read_transaction():
                calls = reader.calls_between("0000", "9999")
        finally:
            reader.close()
    except FileNotFoundError:
        return ()
    except Exception as bad:  # 唯讀開法的各類錯誤(含表沒建齊)一律當讀不了
        raise LedgerProblem(type(bad).__name__) from bad
    mining = mc.Caller.RULE_MINING.value
    used = {c.demo_id for c in calls if c.caller == mining and c.demo_id}
    return tuple(sorted(used))


def batch_problems(directory: Path, attempt: Attempt, expected: str) -> list[str]:
    """一個種子一批的錄製內容能不能入庫(或入庫後仍然成立)。目錄與雜湊跟清單的核對、重播找不到
    錄製,由命令列另查。"""
    problems = mc.batch_file_problems(directory, BATCH_PATTERN, BATCH_SHAPE)
    if not directory.is_dir():
        return problems
    found: dict[str, object] = {}
    for path, data in mc.recording_files(directory):
        try:
            recording = mc.validated(path, data)
        except mc.NoRecording:
            continue  # 讀不懂的檔:上面的共用驗收已算進問題
        found[path.stem] = recording
        if (recording.caller, recording.outcome) != (mc.Caller.RULE_MINING.value,
                                                     mc.Outcome.OK.value):
            problems.append(f"{path.name}:呼叫者 {recording.caller}、結果 {recording.outcome},"
                            "要是 rule_mining 且 ok")
        if (recording.model, recording.batch_id) != (attempt.model, attempt.batch_id):
            problems.append(f"{path.name}:模型或批次跟批次清單不符")
    if set(found) != {expected}:
        problems.append(f"錄製鍵集合要恰等於預期的 {{{expected[:12]}…}},"
                        f"實際 {sorted(k[:12] for k in found)}")
    if (attempt.expected_key, attempt.recording_key) != (expected, expected):
        problems.append("批次清單記的預期鍵或錄製鍵跟重算的預期鍵不符")
    return problems
