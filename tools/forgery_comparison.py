"""前後比較表(Phase 12 增量 3,[S1041]):有沒有機械驗證,同一份造假擋不擋得下。

每一列:在暫存目錄造一個小 repo(tools/forgery_demos.py 的定義)、套上一種造假改法,然後
- 「沒有驗證器」:只跑證據測試,原樣取 pytest 的結束代碼與總結行;
- 「有驗證器」:跑宣稱驗證器,原樣取判定與第一條原因(多條附共幾條)。
同一份改動、同一份清單、同一組證據測試,差別只在有沒有跑驗證器。

每一步都在自己的行程群組跑,逾時先 SIGTERM 整組、等一下再 SIGKILL(證據測試再起的孫行程一起收掉);
任一列失敗或逾時,那一列照實寫「這次沒產生:原因」,其他列照常。暫存 repo 產完就刪。

用法:python -m tools.forgery_comparison(在 repo 根);印一行 JSON:rows、note、seconds。
"""

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tools.forgery_demos import (
    CEILING,
    FILES,
    FORGERIES,
    NODE_PAUSE,
    NODE_UPDATE,
    Forgery,
    write_files,
    write_manifests,
)

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "tools" / "verify_claims.py"
STEP_TIMEOUT_SECONDS = 120.0  # 一列裡每一步(證據測試、驗證器)的上限;小 repo 平常一兩秒跑完
STOP_SECONDS = 5.0  # 送 SIGTERM 後等整組收手,再硬殺
NOTE = ("比的是有沒有機械驗證:同一份改動、同一份清單、同一組測試,差別只在有沒有跑驗證器;"
        "不是比模型寫程式的品質。最後一列是天花板:改結果的鉤子連雜湊一起重算,有驗證器也擋不住,"
        "這部分歸人審(清單的差異會進提交,審查員看得到)。")
NOT_GENERATED = "這次沒產生:"


class StepTimeout(Exception):
    """一步超過時限(整組已經收掉)。"""


def _run(command: Sequence[str], cwd: Path, env: dict[str, str],
         timeout: float) -> tuple[int, str]:
    """在自己的行程群組跑一步,回 (結束代碼, 標準輸出);逾時整組收掉並丟 StepTimeout。"""
    popen = subprocess.Popen(list(command), cwd=cwd, env=env, text=True,  # noqa: S603 - 指令是固定的 pytest 與專案內驗證器
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    try:
        out, _ = popen.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _end_group(popen)
        raise StepTimeout(f"逾時({timeout:.0f} 秒)") from None
    except BaseException:  # 自己被收掉(SIGTERM 轉成的結束)也先收掉這一步的整組,不留孤兒
        _end_group(popen)
        raise
    return popen.returncode, out


def _end_group(popen: subprocess.Popen[str]) -> None:
    for signum in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(popen.pid, signum)
        try:
            popen.communicate(timeout=STOP_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def _pytest_summary(code: int, out: str) -> str:
    lines = [line.strip("= ").strip() for line in out.splitlines() if line.strip()]
    last = lines[-1] if lines else "(沒有輸出)"
    if any(word in last for word in (" passed", " failed", " skipped", " error")):
        last = last.split(" in ", 1)[0]  # 拿掉會變的秒數
    return f"pytest 結束代碼 {code}:{last}"


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


def _environment() -> dict[str, str]:
    """從目前的環境起頭;拿掉外面的 PYTHONPATH,小 repo 的程式才不會被 repo 本身的同名套件蓋掉。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def run_row(forgery: Forgery, step_timeout: float) -> dict[str, str]:
    """一列:造小 repo、套上造假、跑證據測試與驗證器。失敗或逾時照實寫這次沒產生。"""
    try:
        with tempfile.TemporaryDirectory(prefix="forgery-") as temporary:
            repo = Path(temporary)
            write_files(repo, FILES)
            write_manifests(repo)
            forgery.forge(repo)
            env = _environment()
            code, out = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              NODE_UPDATE, NODE_PAUSE], repo, env, step_timeout)
            without = _pytest_summary(code, out)
            code, out = _run([sys.executable, str(VERIFIER), str(repo / "claims")], repo, env,
                             step_timeout)
            with_verifier = _verifier_summary(code, out)
    except Exception as broken:  # 任一列出事只影響那一列
        reason = f"{NOT_GENERATED}{type(broken).__name__}: {broken}"
        return {"forgery": forgery.label, "without_verifier": reason, "with_verifier": reason}
    return {"forgery": forgery.label, "without_verifier": without, "with_verifier": with_verifier}


def generate(forgeries: Sequence[Forgery] | None = None, *,
             step_timeout: float = STEP_TIMEOUT_SECONDS) -> dict[str, Any]:
    """產生整張表:五種造假加一列天花板(給了 forgeries 就只跑那幾列,測試用)。"""
    started = time.monotonic()
    chosen = (*FORGERIES, CEILING) if forgeries is None else tuple(forgeries)
    rows = [run_row(forgery, step_timeout) for forgery in chosen]
    return {"rows": rows, "note": NOTE, "seconds": round(time.monotonic() - started, 1)}


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)  # 一鍵展示逾時或取消時整組送 SIGTERM:收掉正在跑的那一步再走


def main() -> None:
    signal.signal(signal.SIGTERM, _terminate)
    print(json.dumps(generate(), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
