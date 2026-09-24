"""錄製回應的讀寫(Phase 11B 增量 1,計劃〈模型用戶端〉[S931]):錄製鍵、錄製檔、佔位符。

錄製鍵只看內容(呼叫者、模型、系統提示、使用者內容、輸出上限,不含後端種類),錄製檔只新建不覆寫。
"""

import hashlib
import json
import os
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from rtb.modelcore import Caller, NoRecording, Outcome, RecordingConflict


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


def load_recording(path: Path) -> Recording | None:
    if not path.is_file():
        return None
    try:
        recording = Recording(**json.loads(path.read_text(encoding="utf-8")))
        Outcome(recording.outcome)
    except (ValueError, TypeError) as bad:
        raise NoRecording(f"錄製檔讀不懂:{path.name}") from bad
    return recording


def save_recording(path: Path, recording: Recording) -> None:
    """只新建、不覆寫:先寫暫存檔,再用硬連結原子地放到正式檔名(已存在就失敗)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(asdict(recording), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    try:
        os.link(temporary, path)
    except FileExistsError as exists:
        raise RecordingConflict("錄製檔已存在,不覆寫") from exists
    finally:
        temporary.unlink()
