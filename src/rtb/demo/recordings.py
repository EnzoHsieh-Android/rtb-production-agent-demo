"""展示錄製批次入庫前的檢查(Phase 13 增量 4,計劃〈錄製批次與入庫〉「驗過」的第 1、2、4 條,[S1164];
另加 F1 到 F6 任何一輪 AI 退回就不過,跟 CI 守衛同一支判法,增量 4 代碼審 r2 m1)。

入庫的展示錄製(recordings/model/phase13-demo/)要先過這三條才准入庫:
1. 批次裡設定錯誤、花費帳忙碌、無法可靠分類這三類的錄製都是 0 份(claude 剛好登出時整批都是設定錯誤,
   重播時全數退回規則,卻看起來像量過了)。
2. 目錄裡每個錄製檔的批次編號都等於這一批,也沒有殘留的佔位檔(沿用模型用戶端的開錄前目錄檢查)。
4. 用錄製模式跑一次 F1 到 F6,找不到錄製的筆數為 0(登入預檢沒過時整趟走規則、一份錄製都不會留下,
   空目錄
   會通過前兩條)。F7 共用 F1 的錄製鍵([S1158]),不另跑。

找不到錄製的筆數讀每個情境自己的暫存花費帳(錄製模式每次呼叫都記一筆,找不到的記「沒有錄製」),所以
分析端的調查、模型說明與原因假說三種呼叫都算進去。

這支檔不給展示伺服器與驅動程式匯入(它讀錄製檔,要用模型用戶端的錄製模組;驅動程式的匯入閉包不含模型
用戶端,[S1143])。命令列:`python -m rtb.demo.recordings --dir <錄製目錄> --batch-id <批次>
--work-dir <暫存目錄>`,印一行 JSON,都過結束代碼 0,不過 1。
"""

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from rtb import modelclient as mc
from rtb.demo.driver import DEMO_RECORDINGS, DONE, NOT_EXERCISED, Driver
from rtb.demo.keys import DemoKeys
from rtb.demo.launcher import MODEL_VARIABLES
from rtb.demo.present import build_demo_state
from rtb.demo.state_store import StateReader, StateWriter
from rtb.modelledger_view import ModelLedgerView
from rtb.modelledger_view import Outcome as LedgerOutcome

REPLAYED = ("F1", "F2", "F3", "F4", "F5", "F6")
BATCH_PATTERN = re.compile(r"phase13-demo-\d{8}")  # 入庫的展示批次(計劃〈錄製批次與入庫〉)
BATCH_SHAPE = "phase13-demo-YYYYMMDD"
FINISHED = frozenset({DONE, NOT_EXERCISED})  # 重播算數:情境要真的跑完(代碼審 r1 k1)
_EVER = ("2000-01-01T00:00:00+00:00", "9999-01-01T00:00:00+00:00")


@dataclass(frozen=True)
class BatchCheck:
    """一次入庫前檢查的結果:找不到錄製幾筆、問題(批次本身的、重播沒跑完或沒問到的)、各情境跑的
    結果(給人追查)。"""

    missing: int
    problems: tuple[str, ...]
    verdicts: tuple[tuple[str, str, str | None], ...] = ()

    @property
    def passed(self) -> bool:
        return self.missing == 0 and not self.problems


def batch_problems(directory: Path, batch_id: str) -> tuple[str, ...]:
    """第 1、2 條與正式後端:經模型用戶端門面的共用驗收(跟評估批次同一份),另核批次編號格式、
    錄製檔都是這一批。"""
    problems = [] if BATCH_PATTERN.fullmatch(batch_id) else [
        f"批次編號要是 {BATCH_SHAPE}:{batch_id}"]
    problems += mc.batch_file_problems(directory, BATCH_PATTERN, BATCH_SHAPE)
    if directory.is_dir():
        others = sorted({str(data.get("batch_id")) for _p, data in mc.recording_files(directory)
                         if isinstance(data, dict) and "batch_id" in data} - {batch_id})
        if others:
            problems.append(f"目錄裡有別的批次:{others}(要檢查的是 {batch_id})")
    return tuple(problems)


def replay_problems(verdicts: Sequence[tuple[str, str, str | None]],
                    ledgers: Mapping[str, Path]) -> tuple[int, tuple[str, ...]]:
    """第 4 條:回(找不到錄製的筆數, 重播本身的問題)。每個情境要跑完(照預期或故障沒走到),每一本帳
    都要在、都有呼叫紀錄——情境在第一次問 AI 之前就失敗,帳根本不會建,那一段錄製等於沒驗
    (代碼審 r1 k1/l2/s3)。"""
    problems = [f"{code} 沒有跑完:{status}({reason})" for code, status, reason in verdicts
                if status not in FINISHED]
    missing = 0
    for code, ledger in ledgers.items():
        calls = _calls(ledger)
        if calls is None:
            problems.append(f"{code} 的花費帳不在或讀不了:這個情境一次都沒有問 AI")
            continue
        if not calls:
            problems.append(f"{code} 的花費帳沒有任何呼叫紀錄")
        missing += sum(1 for outcome in calls if outcome == LedgerOutcome.NO_RECORDING.value)
    return missing, tuple(problems)


def ai_fallback_problems(state_db: Path, demo_id: str,
                         codes: Sequence[str] = REPLAYED) -> tuple[str, ...]:
    """重播 F1 到 F6 時,任何一件工作的任何一輪 AI 退回程式規則(流程走 a_ai → a_rule)都算一條問題
    (使用者 2026-09-25 裁定的 CI 守衛;增量 4 代碼審 r2 m1 搬進這裡:錄完的自動檢查、入庫前檢查與 CI
    測試呼叫同一支,錄到退回就判不過、要重錄)。F7 不查。讀展示狀態庫,跟頁面同一份判斷列。"""
    reader = StateReader(state_db)
    try:
        shown = build_demo_state(reader, demo_id, running=False, now=datetime.now(UTC))
    finally:
        reader.close()
    wanted = set(codes) - {"F7"}
    return tuple(
        f"{scenario.code.value} 工作 {decision.task_id} 的 AI 這次沒有給出回答、退回程式規則"
        f"({decision.outcome})"
        for scenario in shown.scenarios if scenario.code.value in wanted
        for decision in scenario.path if decision.taken_edge == ("a_ai", "a_rule"))


def _calls(ledger: Path) -> list[str | None] | None:
    if not ledger.is_file():
        return None
    try:
        view = ModelLedgerView(ledger)
    except Exception:  # 讀不了、還沒建齊
        return None
    try:
        return [call.outcome for call in view.calls_between(*_EVER)]
    finally:
        view.close()


def check_demo_batch(directory: Path, batch_id: str, work_dir: Path, *,
                     codes: Sequence[str] = REPLAYED,
                     user_env: Mapping[str, str] | None = None) -> BatchCheck:
    """[S1164] 用錄製模式跑一次 F1 到 F6(讀這個目錄),數找不到錄製的筆數,再加上批次本身與重播本身的
    問題。使用者環境拿掉三個模型變數:這裡一律只重播,不即時呼叫。目錄一律先轉成絕對路徑:子行程在別的
    工作目錄跑,相對路徑會讀不到錄製(協調者 2026-09-25 實測:相對 --dir 重播全找不到)。"""
    directory, work_dir = Path(directory).resolve(), Path(work_dir).resolve()
    env = {k: v for k, v in (os.environ if user_env is None else user_env).items()
           if k not in MODEL_VARIABLES}
    demo_id = f"batch-check-{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = StateWriter(work_dir / "state.db", demo_id)
    try:
        driver = Driver(work_dir / "demos", demo_id, DemoKeys.generate(), state, user_env=env,
                        recordings_dir=directory)
        verdicts = tuple((v.code, v.status, v.reason) for v in driver.run(codes))
    finally:
        state.close()
    missing, replayed = replay_problems(
        verdicts, {code: driver.root / code / "model-ledger.db" for code in codes})
    fallbacks = ai_fallback_problems(work_dir / "state.db", demo_id, codes)
    return BatchCheck(missing, (*batch_problems(directory, batch_id), *replayed, *fallbacks),
                      verdicts)


# ---- 錄一批展示錄製(協調者用真 claude 錄;測試一律用假 claude) ----
RECORDED_CODES = REPLAYED  # F7 不錄:它與 F5 雙胞胎共用 F1 的錄製鍵([S1158])
NEXT_STEP = ("驗過之後把整個目錄搬進 " + str(DEMO_RECORDINGS) + "(整個替換入庫的那一份,不在舊目錄"
             "上疊錄),再跑一次入庫前檢查:python -m rtb.demo.recordings --dir "
             + str(DEMO_RECORDINGS) + " --batch-id {batch} --work-dir <暫存目錄>")


@dataclass(frozen=True)
class RecordResult:
    """錄一批的結果:開錄前或錄的途中的問題、各情境的結果、錄完自動跑的入庫前檢查(沒錄就是空的)。"""

    problems: tuple[str, ...]
    verdicts: tuple[tuple[str, str, str | None], ...] = ()
    check: BatchCheck | None = None
    next_step: str | None = None

    @property
    def passed(self) -> bool:
        return not self.problems and self.check is not None and self.check.passed


def recording_problems(directory: Path, batch_id: str,
                       env: Mapping[str, str]) -> tuple[str, ...]:
    """開錄前的檢查:批次編號格式、目錄不存在或是空的、不在入庫目錄底下(共用的開錄前目錄檢查);
    即時開關打開之後模型用戶端真的判成即時加錄製、登入預檢過了。不過就不跑任何情境(不退回錄製假裝
    錄好了)。"""
    problems = [] if BATCH_PATTERN.fullmatch(batch_id) else [
        f"批次編號要是 {BATCH_SHAPE}:{batch_id}"]
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        problems.append(f"錄製目錄要不存在或是空的:{directory}")
    try:
        mc.check_recordings_dir(directory, batch_id)
    except mc.MixedRecordingsDir as mixed:
        problems.append(f"錄製目錄不能開錄:{mixed}")
    if problems:
        return tuple(problems)
    claude = shutil.which("claude", path=env.get("PATH", ""))
    try:
        settings = mc.settings_from_env(env, f"record-{batch_id}", claude)
    except mc.UnknownModel as unknown:
        return (f"模型設定不對:{unknown}",)
    if settings.mode is not mc.Mode.LIVE or not settings.record:
        why = ";".join(settings.notices) or ("PATH 上找不到 claude" if claude is None
                                             else "即時模式沒開")
        return (f"即時模式不能用,不錄:{why}",)
    checked = mc.preflight_login(settings)
    if checked.outcome is mc.Preflight.FAILED:
        return (f"claude 登入預檢沒過,不錄:{checked.reason}",)
    return ()


def record_demo_batch(directory: Path, batch_id: str, work_dir: Path, *,
                      user_env: Mapping[str, str] | None = None) -> RecordResult:
    """依序以即時加錄製跑 F1 到 F6,分析端、說明、假說三支模型入口都寫進同一個全新目錄、同一個批次;
    錄完自動跑一次入庫前檢查(重播 F1 到 F6)。即時模式的花費帳照設計記在帳號家目錄那一本。目錄一律
    先轉成絕對路徑(同 `check_demo_batch`)。"""
    directory, work_dir = Path(directory).resolve(), Path(work_dir).resolve()
    env = {**(os.environ if user_env is None else user_env),
           mc.LIVE_ENV: "1", mc.RECORD_ENV: "1"}
    problems = recording_problems(directory, batch_id, env)
    if problems:
        return RecordResult(problems)
    demo_id = f"record-{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = StateWriter(work_dir / "record-state.db", demo_id)
    try:
        driver = Driver(work_dir / "record-demos", demo_id, DemoKeys.generate(), state,
                        user_env=env, live=RECORDED_CODES, recordings_dir=directory,
                        live_batch=(directory, batch_id))
        verdicts = tuple((v.code, v.status, v.reason) for v in driver.run(RECORDED_CODES))
        modes = _modes(work_dir / "record-state.db", demo_id)
    finally:
        state.close()
    found = [f"{code} 錄的時候沒有跑完:{status}({reason})" for code, status, reason in verdicts
             if status not in FINISHED]
    found += [f"{code} 的分析端沒有判成即時({mode})" for code, mode in modes.items()
              if mode != "live"]
    check = check_demo_batch(directory, batch_id, work_dir / "check",
                             user_env={k: v for k, v in env.items() if k not in MODEL_VARIABLES})
    return RecordResult(tuple(found), verdicts, check, NEXT_STEP.format(batch=batch_id))


def _modes(state_db: Path, demo_id: str) -> dict[str, str | None]:
    reader = StateReader(state_db)
    try:
        return {code: (details.model_mode if details is not None else None)
                for code in RECORDED_CODES
                for details in (reader.scenario_details(demo_id, code),)}
    finally:
        reader.close()


def main(argv: list[str] | None = None, *, user_env: Mapping[str, str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="展示錄製批次:入庫前的檢查(只重播),或加 --record 以即時加錄製錄一批")
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--record", action="store_true",
                        help="以即時加錄製跑 F1 到 F6 錄進 --dir(全新目錄),錄完自動檢查")
    args = parser.parse_args(argv)
    args.dir, args.work_dir = args.dir.resolve(), args.work_dir.resolve()  # 相對路徑一收到就轉絕對
    if args.record:
        recorded = record_demo_batch(args.dir, args.batch_id, args.work_dir, user_env=user_env)
        print(json.dumps({**asdict(recorded), "passed": recorded.passed}, ensure_ascii=False))
        raise SystemExit(0 if recorded.passed else 1)
    result = check_demo_batch(args.dir, args.batch_id, args.work_dir, user_env=user_env)
    print(json.dumps({**asdict(result), "passed": result.passed}, ensure_ascii=False))
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
