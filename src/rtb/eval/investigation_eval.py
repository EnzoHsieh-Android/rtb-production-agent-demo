"""AI 調查的評估執行器與命令列(Phase 13 增量 3,計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]
〈評估案例〉〈錄製批次與入庫〉)。

- 逐筆呼叫增量 2 的**同一支 AI 決策函式**(`ai_judge.Judge`),只把查詢的來源從 DSP 換成案例裡存的結果;
  續租回呼傳一支永遠成功的、先前各輪的調查紀錄放在記憶體。輪數上限、選項驗證、證據核對、退回全部照
  正式路徑([S1146]);這裡沒有自己的迴圈規則,只把「選查詢 → 回蒐集證據」那一步換成從案例取結果。
- 每次模型呼叫記下結果類別、延遲、原價與批次(重播時是錄製當時的值),給報告算模型那一列。
- 錄製批次的驗收([S1141]):入庫目錄裡設定錯誤、花費帳忙碌、無法可靠分類的錄製各 0 份;
  錄製檔都是同一批、批次編號照 `phase13-eval-YYYYMMDD`、沒有佔位檔(用模型用戶端共用的
  開錄前目錄檢查核);重播整個評估集找不到錄製 0 筆。
- 命令列 `python -m rtb.eval.investigation_eval`:預設重播入庫目錄 recordings/model/
  phase13-investigation-eval/(CI 只重播、不啟動 claude);即時加錄製經 AI 決策模組開模型
  閘道,開閘道時先跑模型用戶端的開錄前目錄檢查,目錄不是空的也不是只有同一批就拒絕開始
  ([S1165])。`--verify` 只准重播,驗不過以結束代碼 1 結束。評估批次不另存批次紀錄檔(開錄前
  目錄檢查不收錄製檔以外的檔);模型那一列從重播時帶回的錄製當時數字算,錄製日期與批次從
  錄製檔讀。
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, TextIO

from rtb import modelclient as mc
from rtb.analyzer import ai_judge, policy
from rtb.analyzer import investigation as inv
from rtb.analyzer.flow import AiContext, NoAction, ProposalDecision, QueryMore
from rtb.analyzer.task_store import InvestigationRecord, TaskRow
from rtb.domain.evidence import Evidence, EvidenceKind, PayloadValue, TrustClass
from rtb.domain.task_state import TaskState
from rtb.domain.worth import WorthVerdict
from rtb.eval import investigation_report as report_mod
from rtb.eval.investigation_cases import NOW, Case
from rtb.eval.investigation_report import Call, CaseRun
from rtb.eval.investigation_set import CASES

EXIT_OK = 0
EXIT_VERIFY_FAILED = 1
EXIT_REFUSED = 2  # 參數錯、閘道拒絕(跟 argparse 的參數錯同一個代碼)
BATCH_PATTERN = re.compile(r"phase13-eval-\d{8}")
FAILED_OUTCOMES = MappingProxyType({mc.Outcome.CONFIG_ERROR.value: "設定錯誤",
                                    mc.Outcome.LEDGER_BUSY.value: "花費帳忙碌"})
Ask = Callable[[str, str], Any]  # AI 決策函式的模型呼叫(系統提示, 使用者內容) → 模型結果


def _project_root() -> Path:
    here = Path(__file__).resolve()
    return next(parent for parent in here.parents if (parent / "pyproject.toml").is_file())


COMMITTED_ROOT = _project_root() / "recordings" / "model"  # 入庫的錄製都在這底下,只供重播
DEFAULT_RECORDINGS = COMMITTED_ROOT / "phase13-investigation-eval"


# ---- 逐筆跑 ----
class _Logged:
    """把模型呼叫原樣轉手,順便記下每次的結果類別、延遲、原價;例外照丟,由 AI 決策函式
    照正式路徑處理。"""

    def __init__(self, ask: Ask) -> None:
        self._inner = ask
        self.calls: list[Call] = []

    def __call__(self, system: str, user: str) -> Any:
        try:
            result = self._inner(system, user)
        except Exception as failed:
            outcome = getattr(failed, "outcome", None)
            if outcome is not None:
                self.calls.append(Call(str(outcome.value), getattr(failed, "latency_ms", None),
                                       getattr(failed, "list_nanousd", 0),
                                       getattr(failed, "recording_batch_id", None),
                                       bool(getattr(failed, "shared", False))))
            raise
        self.calls.append(Call(mc.Outcome.OK.value, result.latency_ms, result.list_nanousd,
                               result.batch_id, bool(result.shared)))
        return result


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(inv.canonical_json(payload).encode("ascii")).hexdigest()


def _task(case: Case, seq: int) -> TaskRow:
    return TaskRow(f"eval-{case.case_id}", seq, TaskState.ANALYZING, f"c-{case.group}", None, None,
                   NOW)


def case_evidence(case: Case, task: TaskRow, state: inv.Progress) -> tuple[Evidence, ...]:
    """蒐證那一步:現況、1 小時指標、廣告名稱三種(跟分析端 DSP 用戶端同樣的形狀),加上這件工作先前選過
    的每個查詢的收據——收據用正式路徑同一支函式,從案例存的原始結果算(不打 DSP)。AI 已用過就只給基本
    的(同 [S1138])。"""
    ident = task.task_id
    status: dict[str, PayloadValue] = {"id": task.campaign_id, "budget": case.state["budget"],
              "status": case.state["status"], "version": 1}
    metrics: dict[str, PayloadValue] = {"campaign_id": task.campaign_id, "window": "1h",
                                        **case.metrics}
    text: dict[str, PayloadValue] = {"name": case.name, "truncated": False}
    base = (
        Evidence(f"{ident}-{task.seq}-state", ident, EvidenceKind.CAMPAIGN_STATE, "eval", NOW, 1,
                 _hash(status), TrustClass.TRUSTED, MappingProxyType(status)),
        Evidence(f"{ident}-{task.seq}-metrics", ident, EvidenceKind.METRICS, "eval", NOW, None,
                 _hash(metrics), TrustClass.TRUSTED, MappingProxyType(metrics)),
        Evidence(f"{ident}-{task.seq}-text", ident, EvidenceKind.CAMPAIGN_TEXT, "eval", NOW, 1,
                 _hash(text), TrustClass.UNTRUSTED_TEXT, MappingProxyType(text)),
    )
    if state.used:
        return base
    receipts = tuple(inv.receipt_evidence(ident, task.seq, option, case.results[option.value],
                                          None, NOW) for option in state.queried)
    return base + receipts


def _verdict(outcome: Any) -> WorthVerdict:
    """最後有效答案:提案 = 值得加;不提案依原因(判不值得加、判證據不足)。案例都過了前置過濾,
    走不到別的原因。"""
    if isinstance(outcome.result, ProposalDecision):
        return WorthVerdict.WORTH
    if isinstance(outcome.result, NoAction):
        reason = outcome.no_action_reason
        if reason is policy.NoActionReason.JUDGED_NOT_WORTH:
            return WorthVerdict.NOT_WORTH
        if reason is policy.NoActionReason.JUDGED_INSUFFICIENT:
            return WorthVerdict.INSUFFICIENT
    raise ValueError(f"評估案例走到了前置過濾或別的結局:{outcome!r}")


def run_case(case: Case, ask: Ask) -> CaseRun:
    """一筆案例跑到下結論或退回為止;選查詢就回到「蒐集證據」,從案例取結果再交給同一支
    AI 決策函式。"""
    logged = _Logged(ask)
    judge = ai_judge.Judge(logged)
    counter = CallCounter()
    records: list[InvestigationRecord] = []
    for seq in range(1, inv.MAX_ROUNDS + 2):  # 最多 3 輪模型呼叫;多一輪留給「上限後只剩結論」的保險
        task = _task(case, seq)
        evidence = case_evidence(case, task, inv.progress(records))
        outcome = judge(task, evidence, NOW, AiContext(_renewed, tuple(records), counter))
        if outcome.record is not None:
            records.append(outcome.record)
        if not isinstance(outcome.result, QueryMore):
            return CaseRun(case, _verdict(outcome), tuple(records), tuple(logged.calls))
    raise AssertionError(f"{case.case_id} 超過輪數上限還在選查詢:AI 決策函式的上限沒有生效")


class CallCounter:
    """送出前的呼叫記次(評估沒有任務列,在記憶體裡計;照流程層那支同一個語意):回這筆案例第幾次模型
    呼叫,已達上限回上限加 1、不記。每筆案例一個。"""

    def __init__(self) -> None:
        self.used = 0

    def __call__(self, limit: int) -> int:
        if self.used >= limit:
            return limit + 1
        self.used += 1
        return self.used


def _renewed() -> None:
    """評估沒有任務列與租約:續租永遠成功(計劃〈調查紀錄與狀態同一交易〉,[S1146])。"""


def run_set(cases: Sequence[Case], ask: Ask) -> tuple[CaseRun, ...]:
    return tuple(run_case(case, ask) for case in cases)


rule_verdict = report_mod.rule_verdict  # 現行規則對同一筆的答案(比較表與退回核對用)


# ---- 錄製批次的驗收 ----
def recording_files(directory: Path) -> list[tuple[Path, Any]]:
    found = []
    for path in sorted(directory.iterdir()):
        try:
            found.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, UnicodeDecodeError, ValueError):
            found.append((path, None))
    return found


def batch_problems(directory: Path, runs: Sequence[CaseRun]) -> list[str]:
    """[S1141] 的驗過條件(缺一條就不准入庫):失敗類錄製 0 份;同一批、格式對、沒有佔位與別的檔;
    重播找不到錄製 0 筆(runs 是用錄製模式跑的那一趟)。"""
    if not directory.is_dir():
        return [f"錄製目錄不存在:{directory.name}"]
    files = recording_files(directory)
    problems = [] if files else ["錄製目錄是空的"]
    batches = {data.get("batch_id") for _path, data in files if isinstance(data, dict)
               and "batch_id" in data}
    batch = next(iter(batches)) if len(batches) == 1 else None
    if batch is None or not isinstance(batch, str) or not BATCH_PATTERN.fullmatch(batch):
        found = sorted(map(str, batches))
        problems.append(f"錄製檔的批次編號要是同一個 phase13-eval-YYYYMMDD:{found}")
    else:
        try:
            mc.check_recordings_dir(directory, batch)
        except mc.MixedRecordingsDir as mixed:
            problems.append(f"錄製目錄不是只有這一批的錄製檔:{mixed}")
    bad: dict[str, int] = {}
    for path, data in files:
        try:
            recording = mc.validated(path, data)
        except mc.NoRecording:
            continue  # 佔位或讀不懂:上面的目錄檢查已經算進問題
        label = FAILED_OUTCOMES.get(recording.outcome)
        if recording.unclassified:
            label = "無法可靠分類"
        if label is not None:
            bad[label] = bad.get(label, 0) + 1
    problems += [f"{label}的錄製有 {count} 份" for label, count in sorted(bad.items())]
    missing = sum(1 for run in runs if run.missing_recording)
    if missing:
        problems.append(f"重播時找不到錄製:{missing} 筆")
    return problems


def recording_dates(directory: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """錄製檔記的錄製日期與批次(讀得懂的才算),給報告標「歷史觀測」。"""
    if not directory.is_dir():
        return (), ()
    dates, batches = set(), set()
    for path, data in recording_files(directory):
        try:
            recording = mc.validated(path, data)
        except mc.NoRecording:
            continue
        dates.add(recording.recorded_on)
        batches.add(str(recording.batch_id))
    return tuple(sorted(dates)), tuple(sorted(batches))


# ---- 命令列 ----
def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="用調查評估集評 AI 調查(預設重播入庫的錄製,不呼叫模型),印出逐格報告與採用決定")
    parser.add_argument("--demo-id", help="展示編號;即時模式必填")
    parser.add_argument("--recordings-dir", type=Path,
                        help="錄製目錄(預設 recordings/model/phase13-investigation-eval)")
    parser.add_argument("--ledger", type=Path, help="只在錄製模式能用:花費帳換到別的路徑")
    parser.add_argument("--batch-id", help="即時加錄製的批次編號,格式 phase13-eval-YYYYMMDD")
    parser.add_argument("--verify", action="store_true",
                        help="驗收錄製批次(只准重播):驗不過以結束代碼 1 結束")
    return parser.parse_args(argv)


def _fresh(folder: Path | None) -> bool:
    """即時加錄製的目錄要明寫、而且不在入庫目錄底下(不在入庫的錄製上疊錄,計劃〈錄製批次與入庫〉)。"""
    if folder is None:
        return False
    return not Path(folder).resolve().is_relative_to(COMMITTED_ROOT.resolve())


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        err: TextIO | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    source = os.environ if environ is None else environ
    folder = args.recordings_dir or DEFAULT_RECORDINGS
    if args.batch_id is not None and not BATCH_PATTERN.fullmatch(args.batch_id):
        print(f"批次編號要是 phase13-eval-YYYYMMDD(錄製那天):{args.batch_id!r}", file=errors)
        return EXIT_REFUSED
    try:  # 即時加錄製時,開閘道就先跑模型用戶端的開錄前目錄檢查([S1165])
        gate = ai_judge.open_investigation_gate(source, demo_id=args.demo_id, ledger=args.ledger,
                                                recordings=folder, batch_id=args.batch_id)
    except ValueError as refused:  # GateRefused、UnknownModel 都是 ValueError
        print(f"拒絕開始:{refused}", file=errors)
        return EXIT_REFUSED
    live = gate.mode.value == "live"
    if live and gate.settings.record and not _fresh(args.recordings_dir):
        where = ("沒帶 --recordings-dir" if args.recordings_dir is None
                 else f"{args.recordings_dir} 在入庫目錄 {COMMITTED_ROOT} 底下")
        print(f"拒絕開始:即時加錄製要給一個新的錄製目錄({where});入庫目錄只供重播,驗過才整個搬進去",
              file=errors)
        return EXIT_REFUSED
    if args.verify and live:
        print("--verify 只准重播錄製(不要帶即時開關)", file=errors)
        return EXIT_REFUSED
    for notice in gate.notices:
        print(notice, file=errors)
    runs = run_set(CASES, ai_judge.gate_complete(gate))
    dates, batches = recording_dates(folder)
    report = report_mod.build_report(runs, recorded_on=dates, batches=batches, live=live)
    problems = [] if live else batch_problems(folder, runs)
    lines = [report_mod.render(report, report_mod.decide(report)), "## 錄製批次驗收", ""]
    if live:
        lines.append(f"- 即時模式(批次 {args.batch_id or '未錄製'}):錄完用 --verify 重播驗收")
    else:
        shown = folder.relative_to(_project_root()) if folder.is_relative_to(
            _project_root()) else folder
        lines.append(f"- 驗收:{'通過' if not problems else '沒過'}(目錄 {shown})")
        lines += [f"- {problem}" for problem in problems]
    print("\n".join(lines), file=out or sys.stdout)
    return EXIT_VERIFY_FAILED if args.verify and problems else EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()

