"""AI 調查的評估執行器與命令列(Phase 13 增量 3,計劃 [[Projects/RTB_Phase13AI參與決策_計劃]]
〈評估案例〉〈錄製批次與入庫〉)。

- 逐筆直接呼叫 AI 決策函式(`ai_judge.Judge`),只把查詢的來源從 DSP 換成案例裡存的結果;先前各輪的
  調查紀錄放在記憶體。輪數上限、選項驗證、證據核對、退回都在 Judge 裡([S1146]);Phase 14 增量 3 起
  AI 退出正式加額決策,這裡是 Judge 唯一的呼叫者,不經流程層、沒有任務庫、不開 A/B/C 規則輪:不再選
  查詢就當場結束。Phase 14 增量 4([S1410])起每筆分開存「AI 原始」(模型自己下的結論;退回、呼叫
  失敗、輪數用完都是沒有有效答案,不拿規則答案頂替)與案例九條結果 `rule_verdict(case)`,「AI+規則
  否決」只由報告層派生。
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
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, TextIO

from rtb import modelclient as mc
from rtb.analyzer import ai_judge
from rtb.analyzer import investigation as inv
from rtb.analyzer.investigation import AiContext, QueryMore
from rtb.analyzer.task_store import InvestigationRecord, TaskRow
from rtb.domain.evidence import Evidence, EvidenceKind, PayloadValue, TrustClass
from rtb.domain.task_state import TaskState
from rtb.eval import investigation_report as report_mod
from rtb.eval.investigation_cases import NOW, Case
from rtb.eval.investigation_report import Call, CaseRun
from rtb.eval.investigation_set import CASES

EXIT_OK = 0
EXIT_VERIFY_FAILED = 1
EXIT_REFUSED = 2  # 參數錯、閘道拒絕(跟 argparse 的參數錯同一個代碼)
BATCH_PATTERN = re.compile(r"phase13-eval-\d{8}")
Ask = Callable[[str, str], Any]  # AI 決策函式的模型呼叫(系統提示, 使用者內容) → 模型結果


# 入庫位置在門面的預設錄製目錄(入庫根)底下;專案裡只有門面那一處算入庫根
DEFAULT_RECORDINGS = mc.default_recordings_dir() / "phase13-investigation-eval"
FRESH_HINT = ("即時加錄製要明寫 --recordings-dir,給一個入庫目錄以外的新目錄;"
              "入庫目錄只供重播,驗過才整個搬進去")


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
                               result.batch_id, bool(result.shared), result.text))
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


def run_case(case: Case, ask: Ask) -> CaseRun:
    """一筆案例跑到下結論或退回為止;選查詢就回到「蒐集證據」,從案例取結果再交給同一支
    AI 決策函式。結果分開持有 AI 原始(模型自己的有效結論,沒有就是 None)與案例九條結果([S1410]):
    不再把「AI 答的或退回規則答的」混成一個最後答案。"""
    logged = _Logged(ask)
    judge = ai_judge.Judge(logged)  # 原始錄製重播:還原模型自己的答案(AI 原始)
    counter = CallCounter()
    records: list[InvestigationRecord] = []
    for seq in range(1, inv.MAX_ROUNDS + 2):  # 最多 3 輪模型呼叫;多一輪留給「上限後只剩結論」的保險
        task = _task(case, seq)
        evidence = case_evidence(case, task, inv.progress(records))
        outcome = judge(task, evidence, NOW, AiContext(tuple(records), counter))
        if outcome.record is not None:
            records.append(outcome.record)
        if isinstance(outcome.result, QueryMore):
            continue
        # Phase 14 增量 3/4([S1146] 改寫、[S1410]):當場結束,不開規則輪、不經流程層。AI 原始只取模型
        # 自己下的結論;退回(選項外、呼叫失敗、輪數用完)與舊前置過濾都是「無有效答案」,另記原因。
        # 程式規則一律取案例九條結果;「AI+規則否決」由報告層從這兩者派生
        preflight = None
        if outcome.record is None and not records:  # Phase 13 舊前置過濾:沒送模型就結案
            reason = outcome.no_action_reason
            preflight = reason.value if reason is not None else type(outcome.result).__name__
        return CaseRun(case, report_mod.ai_conclusion(records), rule_verdict(case),
                       report_mod.rule_reason(case), tuple(records), tuple(logged.calls),
                       preflight)
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


def run_set(cases: Sequence[Case], ask: Ask) -> tuple[CaseRun, ...]:
    return tuple(run_case(case, ask) for case in cases)


rule_verdict = report_mod.rule_verdict  # 正式九條對同一筆的答案(程式規則那一列與派生否決用)


# ---- 錄製批次的驗收 ----
def batch_problems(directory: Path, runs: Sequence[CaseRun]) -> list[str]:
    """[S1141] 的驗過條件(缺一條就不准入庫):批次本身的三條走模型用戶端門面的共用驗收(失敗類錄製
    0 份;同一批、格式對、沒有佔位與別的檔;正式後端錄的,跟展示批次同一份);重播找不到錄製 0 筆(runs
    是用錄製模式跑的那一趟)。"""
    problems = mc.batch_file_problems(directory, BATCH_PATTERN, "phase13-eval-YYYYMMDD")
    missing = sum(1 for run in runs if run.missing_recording)
    if missing:
        problems.append(f"重播時找不到錄製(或沒送出):{missing} 筆")
    return problems


def recording_dates(directory: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """錄製檔記的錄製日期與批次(讀得懂的才算),給報告標「歷史觀測」。"""
    if not directory.is_dir():
        return (), ()
    dates, batches = set(), set()
    for path, data in mc.recording_files(directory):
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


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        err: TextIO | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = _parse(argv)
    errors = err or sys.stderr
    source = os.environ if environ is None else environ
    folder = args.recordings_dir or DEFAULT_RECORDINGS
    if args.batch_id is not None and not BATCH_PATTERN.fullmatch(args.batch_id):
        print(f"批次編號要是 phase13-eval-YYYYMMDD(錄製那天):{args.batch_id!r}", file=errors)
        return EXIT_REFUSED
    # 即時加錄製時,開閘道就先跑模型用戶端的開錄前目錄檢查([S1165]):「不准寫進入庫目錄」
    # 只有那一套判準;沒帶 --recordings-dir 時用的預設目錄就在入庫根底下,一定被它拒絕,
    # 拒絕訊息另附明寫目錄的提示
    try:
        gate = ai_judge.open_investigation_gate(
            source, demo_id=args.demo_id, ledger=args.ledger, recordings=folder,
            batch_id=args.batch_id, notify=lambda text: print(text, file=errors))
    except ValueError as refused:  # GateRefused、UnknownModel 都是 ValueError
        hint = "" if args.recordings_dir is not None else f"({FRESH_HINT})"
        print(f"拒絕開始:{refused}{hint}", file=errors)
        return EXIT_REFUSED
    live = gate.mode.value == "live"
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
        root = mc.default_recordings_dir().parent.parent
        shown = folder.relative_to(root) if folder.is_relative_to(root) else folder
        lines.append(f"- 驗收:{'通過' if not problems else '沒過'}(目錄 {shown})")
        lines += [f"- {problem}" for problem in problems]
    print("\n".join(lines), file=out or sys.stdout)
    return EXIT_VERIFY_FAILED if args.verify and problems else EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()

