"""展示的假錄製(Phase 13 增量 4,計劃〈拆增量〉增量 4 的三種標示截圖與測試用;不入庫)。

做法:照展示驅動程式同一支種子函式造平台,在行程內真的跑一次分析端驅動命令列(假的模型閘道照給定的
回答依輪次回答),收下每一輪真的送出去的內容,再寫成錄製檔;有提案時另用模型說明命令列同一支函式組出
說明的送出內容,一起寫。錄製鍵只看內容,所以跟展示裡子行程送出去的逐位元組相同。

三種廣告(送給 AI 的內容各不相同):
- normal:F1 受測廣告(F2、F3、F4 與 F6 的原任務、F7 每一件、F5 雙胞胎都共用這一組)
- attacked:F5 受攻擊廣告(名稱是對抗文字)
- follow_up:F4、F6 的接續任務(另一個寫入者先把預算改成 200 之後重新分析)

命令列(截圖用):`python -m tests.demo.fake_recordings <目錄> <批次> <規格 JSON 檔>`,規格是
{"normal": [第 1 輪回答, 第 2 輪回答…], "attacked": [...], "follow_up": [...],
"narrative": "說明"}。
"""

import json
import sys
import tempfile
import threading
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from rtb import modelclient as mc
from rtb import modelcore as core
from rtb.analyzer import ai_judge, narrate, runner
from rtb.analyzer import investigation as inv
from rtb.analyzer.task_store import TaskStore
from rtb.demo.driver import ADVERSARIAL_NAME, F1_CAMPAIGN, Campaign, seed_platform
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore, Operation
from rtb.executor.inbox_server import InboxServer
from rtb.modelrecording import Recording, recording_key
from tests.analyzer.test_investigation_e2e import FakeGate

NORMAL = F1_CAMPAIGN
ATTACKED = replace(F1_CAMPAIGN, name=ADVERSARIAL_NAME)
FAMILIES: dict[str, Campaign] = {"normal": NORMAL, "attacked": ATTACKED, "follow_up": NORMAL}
FOLLOW_UP_BUDGET = 200  # F4、F6 的另一個寫入者先改成的預算
NARRATIVE = "這個廣告花得比預期慢,程式照公式把預算從 100 加到 110,只供確認時參考。"
NARRATIVE_200 = "這個廣告花得比預期慢,程式照公式把預算從 200 加到 220,只供確認時參考。"


def answer(choice: str | list[str], *cited: tuple[str, str, str],
           reason: str = "照收據判斷") -> str:
    return json.dumps({"choice": choice, "reason": reason,
                       "evidence": [{"ref": r, "field": f, "value": v} for r, f, v in cited]},
                      ensure_ascii=False)


PROPOSE = answer("propose", ("base", "conversions", "1"), reason="有點擊也有轉換,值得加")
INSUFFICIENT = answer("stop_insufficient", ("base", "conversions", "1"),
                      reason="只有 1 小時的 1 筆轉換,證據不足")
LONGER_WINDOW = answer(["check_longer_window"], reason="先看 1 天與 7 天的成效")
OFF_MENU = json.dumps({"choice": "raise_budget", "reason": "x", "evidence": []})


class _RoundGate(FakeGate):
    """依這一件工作的輪次回答(看送出內容裡已查過幾個選項),不是依全體呼叫順序:同一個世界裡好幾件
    工作時,每一件都拿到自己那一輪的回答。"""

    def __init__(self, answers: list[str]) -> None:
        super().__init__(answers)
        self.by_round = list(answers)

    def complete(self, system, user, *, max_output_tokens, timeout_seconds):
        self.sent.append((system, user))
        queried = next(line for line in user.splitlines() if line.startswith("已查過的選項:"))
        done = [] if queried.endswith("(無)") else queried.split(":", 1)[1].split(",")
        # 每一輪最多選一個查詢時,已查過幾個就是第幾輪減一
        index = min(len(done), len(self.by_round) - 1)
        return core.ModelResult(self.by_round[index], core.Source.RECORDED, 0, 0, 0, 0, 0, 0,
                                1.0, "k", None)


def run_family(family: str, answers: list[str], *, campaigns: int = 1,
               ) -> tuple[list[str], list[str]]:
    """在行程內照展示種子跑一次分析端;回(每一輪送給 AI 的內容, 每一份提案的說明送出內容)。
    campaigns:同一種廣告種幾個(F7 那種很多件的情境,每件各跑)。"""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        base = FAMILIES[family]
        seeded = [replace(base, campaign_id=f"k{i:04d}") for i in range(campaigns)]
        seed_platform(root / "dsp.db", seeded)
        if family == "follow_up":
            _other_writer(root / "dsp.db", [c.campaign_id for c in seeded])
        store = TaskStore(root / "analyzer.db")
        for i, campaign in enumerate(seeded):
            store.create_task(f"t{i:04d}", campaign.campaign_id, datetime.now(UTC))
        store.close()
        gate = _RoundGate(answers)
        _run_runner(root, gate)
        store = TaskStore(root / "analyzer.db")
        try:
            narratives = [narrate.prompt_for(store, row)[0] for row in store.handed_off_rows()]
        finally:
            store.close()
        return [user for _system, user in gate.sent], narratives


def _other_writer(dsp_db: Path, campaigns: list[str]) -> None:
    store = CampaignStore(dsp_db)
    try:
        for campaign in campaigns:
            version = store.get_campaign(campaign).version
            store.execute(Operation(campaign_id=campaign, action="update_budget",
                                    params={"new_budget": FOLLOW_UP_BUDGET},
                                    expected_version=version,
                                    idempotency_key=f"other-writer-{FOLLOW_UP_BUDGET}"))
    finally:
        store.close()


def _run_runner(root: Path, gate: FakeGate) -> None:
    dsp = DspServer(root / "dsp.db", fault_injection=False, hang_seconds=0.2, delay_seconds=0.0)
    inbox = InboxServer(root / "inbox.db", fault_injection=False)
    for server in (dsp, inbox):
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        argv = ["--db", str(root / "analyzer.db"),
                "--dsp-url", f"http://127.0.0.1:{dsp.server_address[1]}",
                "--inbox-url", f"http://127.0.0.1:{inbox.server_address[1]}",
                "--interval-seconds", "0.01", "--ai-judge"]
        code = runner.run(argv, max_rounds=40, out=_Sink(), err=_Sink(),
                          open_gate=lambda _environ, **_kwargs: gate)
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


def fake_batch(directory: Path, batch: str, plan: dict[str, list[str]],
               narrative: str | None = None, *, backend: str = FAKE_BACKEND) -> list[str]:
    """照規格寫一批假錄製:每種廣告依輪次的回答,與(給了 narrative 時)每一份提案的說明;回錄製鍵。"""
    keys = []
    for family, answers in plan.items():
        users, narratives = run_family(family, answers)
        for index, user in enumerate(users):
            text = answers[min(index, len(answers) - 1)]
            found = recording(core.Caller.INVESTIGATION, inv.SYSTEM_PROMPT, user,
                              ai_judge.MAX_OUTPUT_TOKENS, text, batch, backend=backend)
            write(directory, found)
            keys.append(found.key)
        if narrative is not None:
            for user in narratives:
                text = NARRATIVE_200 if family == "follow_up" else narrative
                found = recording(core.Caller.NARRATIVE, narrate.SYSTEM_PROMPT, user,
                                  narrate.MAX_OUTPUT_TOKENS, text, batch, backend=backend)
                write(directory, found)
                keys.append(found.key)
    return keys


def main(argv: list[str]) -> None:
    directory, batch, spec_path = Path(argv[0]), argv[1], Path(argv[2])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    narrative = spec.pop("narrative", None)
    keys = fake_batch(directory, batch, spec, narrative)
    print(json.dumps({"directory": str(directory), "batch": batch, "keys": keys}))


if __name__ == "__main__":
    main(sys.argv[1:])
