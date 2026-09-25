"""展示錄製批次入庫前的檢查(Phase 13 增量 4,計劃〈錄製批次與入庫〉「驗過」的第 1、2、4 條,[S1164])。

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
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from rtb.demo.driver import MODEL_VARIABLES, Driver
from rtb.demo.keys import DemoKeys
from rtb.demo.state_store import StateWriter
from rtb.modelledger_view import ModelLedgerView
from rtb.modelledger_view import Outcome as LedgerOutcome
from rtb.modelrecording import MixedRecordingsDir, check_recordings_dir, validated

REPLAYED = ("F1", "F2", "F3", "F4", "F5", "F6")
REFUSED_OUTCOMES = frozenset({"config_error", "ledger_busy"})
_EVER = ("2000-01-01T00:00:00+00:00", "9999-01-01T00:00:00+00:00")


@dataclass(frozen=True)
class BatchCheck:
    """一次入庫前檢查的結果:找不到錄製幾筆、批次本身的問題、各情境跑的結果(給人追查)。"""

    missing: int
    problems: tuple[str, ...]
    verdicts: tuple[tuple[str, str, str | None], ...] = ()

    @property
    def passed(self) -> bool:
        return self.missing == 0 and not self.problems


def batch_problems(directory: Path, batch_id: str) -> tuple[str, ...]:
    """第 1、2 條:目錄只有同一批的錄製檔、沒有佔位檔;沒有設定錯誤、花費帳忙碌、無法可靠分類的
    錄製。"""
    try:
        check_recordings_dir(directory, batch_id)
    except MixedRecordingsDir as mixed:
        return (f"目錄不是只有這一批的錄製檔:{mixed}",)
    if not directory.is_dir():
        return ("錄製目錄不存在",)
    found = []
    for path in sorted(directory.glob("*.json")):
        recording = validated(path, json.loads(path.read_text(encoding="utf-8")))
        if recording.outcome in REFUSED_OUTCOMES:
            found.append(f"{path.name}:結果是 {recording.outcome}")
        if recording.unclassified:
            found.append(f"{path.name}:無法可靠分類")
    return tuple(found)


def missing_recordings(ledgers: Sequence[Path]) -> int:
    """這幾本暫存花費帳裡記成「沒有錄製」的呼叫筆數(帳還沒建就是這個情境沒有呼叫過)。"""
    total = 0
    for ledger in ledgers:
        if not ledger.is_file():
            continue
        try:
            view = ModelLedgerView(ledger)
        except Exception:  # 讀不了、還沒建齊的帳當成至少一筆對不上,不放行
            total += 1
            continue
        try:
            total += sum(1 for call in view.calls_between(*_EVER)
                         if call.outcome == LedgerOutcome.NO_RECORDING.value)
        finally:
            view.close()
    return total


def check_demo_batch(directory: Path, batch_id: str, work_dir: Path, *,
                     codes: Sequence[str] = REPLAYED,
                     user_env: Mapping[str, str] | None = None) -> BatchCheck:
    """[S1164] 用錄製模式跑一次 F1 到 F6(讀這個目錄),數找不到錄製的筆數,再加上批次本身的兩條。
    使用者環境拿掉三個模型變數:這裡一律只重播,不即時呼叫。"""
    problems = batch_problems(directory, batch_id)
    env = {k: v for k, v in (os.environ if user_env is None else user_env).items()
           if k not in MODEL_VARIABLES}
    demo_id = f"batch-check-{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = StateWriter(work_dir / "state.db", demo_id)
    try:
        driver = Driver(work_dir / "demos", demo_id, DemoKeys.generate(), state, user_env=env,
                        recordings_dir=directory)
        verdicts = driver.run(codes)
    finally:
        state.close()
    ledgers = [driver.root / code / "model-ledger.db" for code in codes]
    return BatchCheck(missing_recordings(ledgers), problems,
                      tuple((v.code, v.status, v.reason) for v in verdicts))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False,
                                     description="展示錄製批次入庫前的檢查(只重播,不呼叫模型)")
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    result = check_demo_batch(args.dir, args.batch_id, args.work_dir)
    print(json.dumps({**asdict(result), "passed": result.passed}, ensure_ascii=False))
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
