"""錄製回應的讀寫(Phase 11B 增量 1,計劃〈模型用戶端〉[S931]):錄製鍵、錄製檔、佔位符。

錄製鍵只看內容(呼叫者、模型、系統提示、使用者內容、輸出上限,不含後端種類),錄製檔只新建不覆寫。
"""

import contextlib
import hashlib
import json
import math
import os
import re
import stat
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from rtb.modelcore import Caller, NoRecording, Outcome, SettlementState


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def recording_key(caller: Caller, model: str, system: str, user: str,
                  max_output_tokens: int) -> str:
    """錄製鍵只看內容(不看展示編號、批次、時間),同一個情境重跑得到同一個鍵([S921])。"""
    canonical = json.dumps([Caller(caller).value, model, _normalized(system), _normalized(user),
                            int(max_output_tokens)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_STEMS = "甲乙丙丁戊己庚辛壬癸"


class Placeholders:
    """把真實編號換成依出現順序的佔位符(任務甲、任務乙…第 11 個起是甲2、乙2…)。對照表只留在本機;
    模型輸出驗證通過之後才用 `restore` 換回真實編號。"""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._by_real: dict[str, str] = {}

    def substitute(self, real: str) -> str:
        if real not in self._by_real:
            n = len(self._by_real)
            suffix = _STEMS[n % 10] + ("" if n < 10 else str(n // 10 + 1))
            self._by_real[real] = self._prefix + suffix
        return self._by_real[real]

    def restore(self, text: str) -> str:
        if not self._by_real:
            return text
        by_placeholder = {v: k for k, v in self._by_real.items()}
        pattern = "|".join(re.escape(p) for p in sorted(by_placeholder, key=len, reverse=True))
        return re.sub(pattern, lambda match: by_placeholder[match.group(0)], text)


# ---- 錄製檔 ----
@dataclass(frozen=True)
class Recording:
    """一個錄製檔。不存標準錯誤(可能有本機路徑或帳號)。"""

    key: str
    caller: str
    model: str
    backend: str
    batch_id: str | None
    recorded_on: str
    outcome: str
    sub_reason: str | None
    text: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_write_5m_tokens: int | None
    cache_write_1h_tokens: int | None
    cache_read_tokens: int | None
    reported_nanousd: int | None
    list_nanousd: int
    latency_ms: float
    tool_use: bool  # 錄製當時的工具使用標記
    unclassified: bool  # 錄製當時的「無法可靠分類」標記
    settlement: str | None  # 錄製當時的結算狀態(重播照它停在同一個位置)


_OPTIONAL_TEXT = ("batch_id", "sub_reason", "text", "settlement")
_OPTIONAL_COUNT = ("input_tokens", "output_tokens", "cache_write_5m_tokens",
                   "cache_write_1h_tokens", "cache_read_tokens", "reported_nanousd")


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _well_typed(data: dict[str, object]) -> bool:
    """每一欄的型別都對(不信任錄製檔:它是存在版本庫裡、誰都改得到的檔)。"""
    text_ok = all(isinstance(data[name], str) for name in (
        "key", "caller", "model", "backend", "recorded_on", "outcome"))
    optional_text_ok = all(data[name] is None or isinstance(data[name], str)
                           for name in _OPTIONAL_TEXT)
    counts_ok = all(data[name] is None or _is_count(data[name]) for name in _OPTIONAL_COUNT)
    latency = data["latency_ms"]
    latency_ok = (isinstance(latency, int | float) and not isinstance(latency, bool)
                  and math.isfinite(latency) and latency >= 0)
    # 成功一定有文字、失敗一定沒有(代碼審第 2 輪:成功卻沒文字會走進失敗分支查表失敗)
    text_matches = (data["text"] is not None) == (data["outcome"] == Outcome.OK.value)
    return (text_ok and optional_text_ok and counts_ok and _is_count(data["list_nanousd"])
            and latency_ok and text_matches
            and isinstance(data["tool_use"], bool) and isinstance(data["unclassified"], bool))


def _pending_batch(data: object) -> tuple[bool, str | None]:
    """是不是還在錄的佔位(呼叫前佔住檔名時寫的);是的話回佔住它的批次。"""
    if isinstance(data, dict) and set(data) == {"claimed_by_batch"}:
        batch = data["claimed_by_batch"]
        return True, batch if isinstance(batch, str) else None
    return False, None


class MixedRecordingsDir(ValueError):
    """開錄前的目錄檢查沒過:錄製目錄不是空的、也不是只有同一批的錄製檔(Phase 13 [S1142]、[S1165])。
    """


_RECORDING_NAME = re.compile(r"[0-9a-f]{64}\.json")


_FRESH = "即時加錄製要給一個新的錄製目錄(專案根的 recordings/model/ 是入庫目錄,只供重播)"


def _batch_of(path: Path, batch_id: str) -> str | None:
    """一個錄製檔記的批次;讀不懂、檔名跟檔內的鍵不符、或是佔位檔(不論哪一批)丟 MixedRecordingsDir。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as bad:
        raise MixedRecordingsDir(f"{path.name} 讀不懂({type(bad).__name__})") from bad
    pending, batch = _pending_batch(data)
    if pending:  # 別批的佔位是混批;同一批的是中斷留下的,續錄會在那個鍵上撞錄製衝突
        where = "同一批" if batch == batch_id else f"別的批次({batch})"
        raise MixedRecordingsDir(
            f"錄製目錄裡有{where}留下的佔位 {path.name}:確認沒有行程還在錄之後刪掉再錄")
    try:
        recording = validated(path, data)  # 跟讀取同一套驗證:讀不回來的檔開錄前就拒絕
    except NoRecording as bad:
        raise MixedRecordingsDir(f"{path.name} 不是讀得回來的錄製檔({bad})") from bad
    if path.name != f"{recording.key}.json":
        raise MixedRecordingsDir(f"{path.name} 的檔名跟檔內的錄製鍵不符,讀的時候會被當成沒有錄製")
    return recording.batch_id


def default_recordings_dir() -> Path:
    """錄製目錄的預設值錨在專案根的 recordings/model/(從這支檔往上找 pyproject.toml),從別的工作目錄
    啟動照樣找得到([S920])。這裡就是入庫根:入庫的錄製都在它底下,只供重播;專案裡只有這一處算它
    (門面轉手,Phase 13 增量 3 代碼審 r2)。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent / "recordings" / "model"
    return here.parents[2] / "recordings" / "model"


def _inside_committed(target: Path) -> bool:
    """目標路徑是不是落在入庫根底下(含入庫根本身):目標與它每一層已存在的祖先,只要有一個跟入庫根是
    同一個檔案(同裝置、同 inode,跟隨符號連結)就算。比檔案身分、不比路徑字面,所以大小寫不同(不分大小
    寫的檔案系統)、經符號連結、還不存在的子目錄都認得出來(Phase 13 增量 3 代碼審 r2 a1/v1)。"""
    try:
        root = os.stat(default_recordings_dir())
    except OSError:
        return False  # 入庫根不存在:沒有東西可以疊上去
    candidate = Path(os.path.abspath(target))
    for path in (candidate, *candidate.parents):
        try:
            found = os.stat(path)
        except OSError:
            continue  # 還不存在(或讀不到)的那一層:往上看
        if (found.st_dev, found.st_ino) == (root.st_dev, root.st_ino):
            return True
    return False


def check_recordings_dir(directory: Path, batch_id: str | None) -> None:
    """開錄前的目錄檢查(即時加錄製的入口共用:runner 與說明命令列經模型閘道、評估執行器經 AI 決策模組
    開閘道、假說命令列直接呼叫):沒帶批次拒絕;目錄落在入庫根底下(預設錄製目錄,含還不存在的子目錄、
    大小寫不同或經符號連結的寫法)拒絕——入庫的錄製只供重播,即時錄製一律寫進新目錄,驗過才整個搬進去;
    其餘照 `check_one_batch` 核目錄內容。重播不經這支。"""
    if not batch_id or not batch_id.strip():
        raise MixedRecordingsDir("即時加錄製要帶批次編號")
    if _inside_committed(Path(directory)):
        raise MixedRecordingsDir(f"{directory} 在入庫目錄 {default_recordings_dir()} 底下;{_FRESH}")
    check_one_batch(directory, batch_id)


def check_one_batch(directory: Path, batch_id: str | None) -> None:
    """目錄內容的檢查(開錄前檢查的後半,評估的錄製批次驗收也用它核入庫目錄):目錄不存在或是空的就放行;裡面只准有
    檔名等於檔內錄製鍵、批次等於這一批的錄製檔。沒帶批次、目錄是懸空的符號連結或不是目錄、列不出來、
    有其他檔或子目錄、讀不懂的檔、別的批次、任何佔位(含同一批中斷留下的)一律拒絕:不要把兩批混在
    同一個目錄,也不要讓錄製在第一次呼叫才以錄製衝突或設定錯誤失敗(Phase 13〈錄製批次與入庫〉、
    代碼審 r1)。"""
    if not batch_id or not batch_id.strip():
        raise MixedRecordingsDir("即時加錄製要帶批次編號")
    folder = Path(directory)
    try:
        mode = folder.lstat().st_mode  # 只有「不存在」放行;上一層讀不到或是檔案都拒絕(代碼審 r2)
    except FileNotFoundError:
        return
    except OSError as broken:
        raise MixedRecordingsDir(f"錄製目錄讀不到({type(broken).__name__});{_FRESH}") from broken
    if stat.S_ISLNK(mode):
        raise MixedRecordingsDir(f"{folder} 是符號連結,不收;{_FRESH}")
    if not stat.S_ISDIR(mode):
        raise MixedRecordingsDir(f"{folder} 不是目錄")
    try:
        entries = sorted(folder.iterdir())
    except OSError as denied:
        raise MixedRecordingsDir(f"錄製目錄列不出來({type(denied).__name__})") from denied
    for entry in entries:
        if not entry.is_file() or entry.is_symlink() or not _RECORDING_NAME.fullmatch(entry.name):
            raise MixedRecordingsDir(f"錄製目錄裡有不是錄製檔的東西:{entry.name};{_FRESH}")
        found = _batch_of(entry, batch_id)
        if found != batch_id:
            raise MixedRecordingsDir(
                f"錄製目錄裡有別的批次({found}),這一批是 {batch_id};{_FRESH}")


class PendingRecording(Exception):
    """檔名已被佔住、還在錄(可能是別的批次,也可能是同批次的另一個呼叫)。"""

    def __init__(self, batch_id: str | None) -> None:
        super().__init__("錄製檔還在錄")
        self.batch_id = batch_id


def validated(path: Path, data: object) -> Recording:
    """錄製檔內容的共用驗證(讀取與開錄前目錄檢查同一套,代碼審 r2):欄位齊、結果類別、呼叫者與結算狀態
    是合法值、
    每欄型別都對;不符丟「沒有錄製」。不看鍵、呼叫者與模型(那是讀取時跟請求比的)。"""
    try:
        if not isinstance(data, dict):
            raise TypeError("不是物件")
        recording = Recording(**data)
        Outcome(recording.outcome)
        Caller(recording.caller)  # 呼叫者也要是合法成員,不然哪個呼叫者都讀不回(代碼審 r3)
        if recording.settlement is not None:
            SettlementState(recording.settlement)
    except (ValueError, TypeError) as bad:
        raise NoRecording(f"錄製檔讀不懂:{path.name}") from bad
    if not _well_typed(data):
        raise NoRecording(f"錄製檔欄位型別不對:{path.name}")
    return recording


def load_recording(path: Path, *, key: str, caller: Caller, model: str) -> Recording | None:
    """讀錄製檔並驗:鍵等於檔名、呼叫者與模型等於這次請求、每欄型別都對;不符丟「沒有錄製」。
    不存在回 None(懸空的符號連結也算存在);還在錄的佔位丟 `PendingRecording`。"""
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not path.is_file():
        raise NoRecording(f"錄製檔不是一般檔:{path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as bad:
        raise NoRecording(f"錄製檔讀不懂:{path.name}") from bad
    pending, batch = _pending_batch(data)
    if pending:
        raise PendingRecording(batch)
    recording = validated(path, data)
    if (recording.key != key or path.name != f"{key}.json"
            or recording.caller != Caller(caller).value or recording.model != model):
        raise NoRecording(f"錄製檔的鍵、呼叫者或模型跟這次請求不符:{path.name}")
    return recording


def claim(path: Path, batch_id: str | None) -> None:
    """呼叫前用獨佔建立佔住正式檔名(已存在就丟 FileExistsError,懸空的符號連結也算存在;不跟隨符號
    連結),內容是佔住它的批次。錄完用 `save_recording` 原子地換成正式內容;
    沒錄成用 `release` 放掉。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"claimed_by_batch": batch_id}, handle)


def release(path: Path) -> None:
    """放掉自己佔住、還沒錄成的檔名(只刪佔位,不刪正式錄製)。"""
    try:
        pending, _ = _pending_batch(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return
    if pending:
        with contextlib.suppress(OSError):  # 放不掉(目錄沒權限)就留著:下次呼叫前會被拒絕、要人清
            path.unlink(missing_ok=True)


def save_recording(path: Path, recording: Recording) -> None:
    """把自己佔住的檔名換成正式內容:先寫暫存檔,再原子地改名蓋掉佔位(只蓋自己的佔位,不覆寫別人的
    錄製:別人碰不到已被佔住的檔名)。"""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(asdict(recording), ensure_ascii=False, indent=1),
                             encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
