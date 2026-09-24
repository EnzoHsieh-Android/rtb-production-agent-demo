"""前後比較表(Phase 12 增量 3,[S1041]):有沒有機械驗證,同一份造假擋不擋得下。

每一列:在暫存目錄造一個小 repo(tools/forgery_demos.py 的定義)、套上一種造假改法,然後
- 「沒有驗證器」:只跑證據測試,原樣取 pytest 的結束代碼與總結行;
- 「有驗證器」:跑宣稱驗證器,原樣取判定與第一條原因(多條附共幾條)。
同一份改動、同一份清單、同一組證據測試,差別只在有沒有跑驗證器:兩欄用同一套清環境規則(驗證器的
clean_environment,加 -E -s),外面的 PYTEST_ 變數與自動載入的外掛改不了任何一欄(代碼審 r1)。

每一步都在自己的行程群組跑,逾時先 SIGTERM 整組、等一下,最後一律對整組送 SIGKILL(不理 SIGTERM 的
孫行程也收掉);任一列失敗或逾時,那一列照實寫「這次沒產生:原因」,其他列照常。暫存 repo 產完就刪。
收到 SIGTERM 只記下「要停」:正在跑的那一步整組收掉,暫存 repo 照常刪完,不再跑下一列。

用法:python -E -s tools/forgery_comparison.py(用檔案路徑跑;同目錄的定義與驗證器也用檔案路徑載入,
不靠 tools 這個命名空間套件,外面同名的套件頂替不了);印一行 JSON:rows、note、seconds。
"""

import contextlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str) -> ModuleType:
    """用檔案路徑載入同目錄的模組(不經 tools 套件的匯入路徑)。"""
    spec = importlib.util.spec_from_file_location(f"forgery_comparison_{name}",
                                                  HERE / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"載入不了 {name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_demos = _load("forgery_demos")
_verifier = _load("verify_claims")
VERIFIER = HERE / "verify_claims.py"
STEP_TIMEOUT_SECONDS = 120.0  # 一列裡每一步(證據測試、驗證器)的上限;小 repo 平常一兩秒跑完
STOP_SECONDS = 5.0  # 送 SIGTERM 後等整組收手,再硬殺
POLL_SECONDS = 0.2
NOTE = ("比的是有沒有機械驗證:同一份改動、同一份清單、同一組測試,差別只在有沒有跑驗證器;"
        "不是比模型寫程式的品質。最後一列是天花板:改結果的鉤子連雜湊一起重算,有驗證器也擋不住,"
        "這部分歸人審(清單的差異會進提交,審查員看得到)。")
NOT_GENERATED = "這次沒產生:"
# pytest 的總結行(「2 passed」「1 passed, 1 skipped」「1 failed, 1 passed」…)
_SUMMARY = re.compile(r"\d+ (passed|failed|skipped|errors?|deselected|xfailed|xpassed)\b.*")
_stop = threading.Event()


class StepTimeout(Exception):
    """一步超過時限(整組已經收掉)。"""


class Stopped(Exception):
    """收到停止要求(整組已經收掉)。"""


def install_stop_handler() -> None:
    """SIGTERM 只記下「要停」,不在任何一行丟例外(代碼審 r1 s4:原本在刪暫存 repo 途中丟
    SystemExit,暫存目錄殘留一半)。等待時與兩列之間檢查。"""
    signal.signal(signal.SIGTERM, _request_stop)


def _request_stop(_signum: int, _frame: FrameType | None) -> None:
    _stop.set()


def stop_requested() -> bool:
    return _stop.is_set()


def reset_stop() -> None:
    _stop.clear()


def _run(command: Sequence[str], cwd: Path, env: dict[str, str],
         timeout: float) -> tuple[int, str]:
    """在自己的行程群組跑一步,回 (結束代碼, 輸出);逾時或收到停止,整組收掉再丟例外。"""
    popen = subprocess.Popen(list(command), cwd=cwd, env=env, text=True,  # noqa: S603 - 指令是固定的 pytest 與專案內驗證器
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                out, _ = popen.communicate(timeout=POLL_SECONDS)
                return popen.returncode, out
            except subprocess.TimeoutExpired:
                if _stop.is_set():
                    raise Stopped("收到停止要求") from None
                if time.monotonic() >= deadline:
                    raise StepTimeout(f"逾時({timeout:.0f} 秒)") from None
    except BaseException:
        _end_group(popen)
        raise
    finally:
        _kill_group(popen)  # 正常結束也對整組補一次:測試自己起、還沒結束的孫行程不留


def _end_group(popen: subprocess.Popen[str]) -> None:
    """先 SIGTERM 整組、等一下;不管等不等得到,最後一律整組 SIGKILL(代碼審 r1 x1/l1-1:原本直屬
    子行程一結束就不再送,不理 SIGTERM 的孫行程留下;等待途中被打斷也照樣送)。"""
    try:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(popen.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            popen.wait(STOP_SECONDS)
    finally:
        _kill_group(popen)


def _kill_group(popen: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(popen.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        popen.wait(STOP_SECONDS)


def _pytest_summary(code: int, out: str) -> str:
    """結束代碼 0 或 1、而且最後一行是測試總結,才是「沒有驗證器」的結果;其他(找不到測試、用法錯、
    內部錯誤)是這一列沒產生(代碼審 r1 s5)。"""
    lines = [line.strip("= ").strip() for line in out.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    summary = _SUMMARY.fullmatch(last.split(" in ", 1)[0]) if last else None
    if code not in (0, 1) or summary is None:
        raise RuntimeError(f"證據測試沒有跑成(pytest 結束代碼 {code}:{last or '沒有輸出'})")
    return f"pytest 結束代碼 {code}:{summary.group(0)}"


def _verifier_summary(code: int, out: str) -> str:
    lines = out.splitlines()
    if code == 0:
        return next((line for line in lines if line.startswith("通過")), "通過")
    reasons = [line[2:] for line in lines if line.startswith("- ")]
    if code == 1 and reasons:
        more = f"(共 {len(reasons)} 條)" if len(reasons) > 1 else ""
        return f"擋下:{reasons[0]}{more}"
    head = next((line for line in lines if line.startswith(("擋下", "判不出"))), "")
    return f"判不出(結束代碼 {code}){head}"


def _pytest_command(python: str) -> list[str]:
    """跟驗證器跑證據測試同一套旗標:-E 不讀 PYTHON 開頭的變數、-s 不開 user site、設定檔與根目錄
    寫死(tools/verify_claims.py 的 run_evidence)。"""
    return [python, "-E", "-s", "-m", "pytest", "-c", _verifier.PYTEST_CONFIG, "--rootdir", ".",
            "-p", "no:cacheprovider", "-q", _demos.NODE_UPDATE, _demos.NODE_PAUSE]


def run_row(forgery: Any, step_timeout: float, python: str = sys.executable) -> dict[str, str]:
    """一列:造小 repo、套上造假、跑證據測試與驗證器。失敗或逾時照實寫這次沒產生。"""
    try:
        with tempfile.TemporaryDirectory(prefix="forgery-") as temporary:
            repo = Path(temporary)
            _demos.write_files(repo, _demos.FILES)
            _demos.write_manifests(repo)
            forgery.forge(repo)
            env = _verifier.clean_environment()  # 兩欄同一套:驗證器跑證據測試用的就是這一份
            code, out = _run(_pytest_command(python), repo, env, step_timeout)
            without = _pytest_summary(code, out)
            code, out = _run([python, "-E", "-s", str(VERIFIER), str(repo / "claims")], repo, env,
                             step_timeout)
            with_verifier = _verifier_summary(code, out)
    except Exception as broken:  # 任一列出事只影響那一列
        reason = f"{NOT_GENERATED}{type(broken).__name__}: {broken}"
        return {"forgery": forgery.label, "without_verifier": reason, "with_verifier": reason}
    return {"forgery": forgery.label, "without_verifier": without, "with_verifier": with_verifier}


def _has_pytest(python: str) -> bool:
    try:
        checked = subprocess.run([python, "-E", "-s", "-c", "import pytest"],  # noqa: S603 - 固定指令
                                 env=_verifier.clean_environment(), capture_output=True,
                                 timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return checked.returncode == 0


def generate(forgeries: Sequence[Any] | None = None, *,
             step_timeout: float = STEP_TIMEOUT_SECONDS,
             python: str = sys.executable) -> dict[str, Any]:
    """產生整張表:五種造假加一列天花板(給了 forgeries 就只跑那幾列,測試用)。產生器用的直譯器沒有
    pytest 就整張表不產生(代碼審 r1 s5)。收到停止要求就不再跑下一列。"""
    started = time.monotonic()
    if not _has_pytest(python):
        return {"rows": [], "note": f"{NOT_GENERATED}環境缺 pytest(跑證據測試要用)",
                "seconds": round(time.monotonic() - started, 1)}
    chosen = (*_demos.FORGERIES, _demos.CEILING) if forgeries is None else tuple(forgeries)
    rows = []
    for forgery in chosen:
        if _stop.is_set():
            break
        rows.append(run_row(forgery, step_timeout, python))
    return {"rows": rows, "note": NOTE, "seconds": round(time.monotonic() - started, 1)}


def main() -> None:
    install_stop_handler()
    result = generate()
    if _stop.is_set():
        raise SystemExit(128 + signal.SIGTERM)  # 被要求停下:不印半張表
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
