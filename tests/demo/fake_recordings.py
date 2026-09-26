"""展示的假錄製(Phase 13 增量 4 起,截圖與測試用;不入庫)。

Phase 14 增量 3(計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈拆增量〉3):分析端不再呼叫 AI,
假錄製只造**提案說明**(NARRATIVE;需要時另造假說)。做法:照展示驅動程式同一支種子函式造平台,在行程內
真的跑一次純規則的分析端驅動命令列,再用模型說明命令列同一支函式組出每一份提案的說明送出內容,寫成錄製檔。
錄製鍵只看內容,所以跟展示裡子行程送出去的逐位元組相同。

兩種廣告(說明的送出內容各不相同):
- normal:F1 受測廣告(F1、F2、F3 的追蹤那一件都是這一組)
- attacked:F5 受攻擊廣告(名稱是對抗文字)
F4、F6 的接續任務照九條第 3 條不提案,沒有說明可錄;F7 不另外錄(說明內容的編號都換成佔位符)。

命令列(截圖用):`python -m tests.demo.fake_recordings <目錄> <批次> [說明文字]`。
"""

import json
import sys
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from rtb import modelclient as mc
from rtb import modelcore as core
from rtb.analyzer import narrate, runner
from rtb.analyzer.task_store import TaskStore
from rtb.demo.driver import ADVERSARIAL_NAME, F1_CAMPAIGN, Campaign, seed_platform
from rtb.dsp.server import DspServer
from rtb.executor.inbox_server import InboxServer
from rtb.modelrecording import Recording, recording_key

NORMAL = F1_CAMPAIGN
ATTACKED = replace(F1_CAMPAIGN, name=ADVERSARIAL_NAME)
FAMILIES: dict[str, Campaign] = {"normal": NORMAL, "attacked": ATTACKED}
NARRATIVE = "這個廣告花得比預期慢,程式照公式把預算從 100 加到 110,只供確認時參考。"


def run_family(family: str, *, campaigns: int = 1) -> list[str]:
    """在行程內照展示種子跑一次純規則的分析端;回每一份提案的說明送出內容。campaigns:同一種廣告種幾個。"""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        base = FAMILIES[family]
        seeded = [replace(base, campaign_id=f"k{i:04d}") for i in range(campaigns)] \
            if campaigns > 1 else [base]
        seed_platform(root / "dsp.db", seeded)
        store = TaskStore(root / "analyzer.db")
        for i, campaign in enumerate(seeded):
            store.create_task(f"t{i + 1}" if campaigns == 1 else f"t{i:04d}",
                              campaign.campaign_id, datetime.now(UTC))
        store.close()
        _run_runner(root)
        store = TaskStore(root / "analyzer.db")
        try:
            return [narrate.prompt_for(store, row)[0] for row in store.handed_off_rows()]
        finally:
            store.close()


def _run_runner(root: Path) -> None:
    dsp = DspServer(root / "dsp.db", fault_injection=False, hang_seconds=0.2, delay_seconds=0.0)
    inbox = InboxServer(root / "inbox.db", fault_injection=False)
    for server in (dsp, inbox):
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        argv = ["--db", str(root / "analyzer.db"),
                "--dsp-url", f"http://127.0.0.1:{dsp.server_address[1]}",
                "--inbox-url", f"http://127.0.0.1:{inbox.server_address[1]}",
                "--interval-seconds", "0.01"]
        code = runner.run(argv, max_rounds=40, out=_Sink(), err=_Sink())
        if code != 0:
            raise RuntimeError(f"分析端結束代碼 {code}")
    finally:
        for server in (dsp, inbox):
            server.shutdown()
            server.server_close()


class _Sink:
    def write(self, _text: str) -> int:
        return 0

    def flush(self) -> None:
        return None


FAKE_BACKEND = "fake"  # 假錄製的後端:入庫前的批次檢查一律拒收(代碼審增量 4 r1 s2)


def recording(caller: core.Caller,  # noqa: PLR0913 - 錄製檔的每一欄
              system: str, user: str, max_tokens: int, text: str,
              batch: str, *, outcome: core.Outcome = core.Outcome.OK,
              backend: str = FAKE_BACKEND) -> Recording:
    key = recording_key(caller, core.DEFAULT_MODEL, system, user, max_tokens)
    ok = outcome is core.Outcome.OK
    return Recording(key=key, caller=caller.value, model=core.DEFAULT_MODEL, backend=backend,
                     batch_id=batch, recorded_on="2026-09-25", outcome=outcome.value,
                     sub_reason=None, text=text if ok else None, input_tokens=None,
                     output_tokens=None, cache_write_5m_tokens=None, cache_write_1h_tokens=None,
                     cache_read_tokens=None, reported_nanousd=None, list_nanousd=0,
                     latency_ms=1.0, tool_use=False, unclassified=False, settlement=None)


def write(directory: Path, found: Recording) -> Path:
    """寫一份假錄製;拒絕寫到專案的入庫錄製目錄(recordings/model)底下(代碼審增量 4 r1 t3)。"""
    root = mc.default_recordings_dir().resolve()
    target = directory.resolve()
    if target == root or root in target.parents:
        raise ValueError(f"假錄製不准寫進入庫錄製目錄:{directory}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{found.key}.json"
    path.write_text(json.dumps(asdict(found), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def fake_batch(directory: Path, batch: str, families: Iterable[str] = ("normal",),
               narrative: str = NARRATIVE, *, backend: str = FAKE_BACKEND) -> list[str]:
    """照規格寫一批假錄製:每種廣告每一份提案的說明;回錄製鍵。"""
    keys = []
    for family in families:
        for user in run_family(family):
            found = recording(core.Caller.NARRATIVE, narrate.SYSTEM_PROMPT, user,
                              narrate.MAX_OUTPUT_TOKENS, narrative, batch, backend=backend)
            write(directory, found)
            keys.append(found.key)
    return keys


def main(argv: list[str]) -> None:
    directory, batch = Path(argv[0]), argv[1]
    narrative = argv[2] if len(argv) > 2 else NARRATIVE
    keys = fake_batch(directory, batch, tuple(FAMILIES), narrative)
    print(json.dumps({"directory": str(directory), "batch": batch, "keys": keys}))


if __name__ == "__main__":
    main(sys.argv[1:])
