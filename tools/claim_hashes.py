"""宣稱清單的雜湊輔助:印出每份清單裡哪幾個檔的雜湊跟現況不一樣、正確值是多少(Phase 11 增量 2)。

用法:python -B -m tools.claim_hashes claims/
(-B:python -m 會在這支任何一行執行之前就把它編譯成 .pyc,程式裡攔不到;下面的
sys.dont_write_bytecode 擋的是之後匯入驗證器產生的 .pyc。)
只印,不寫任何檔、不寫回清單:清單照樣要人改,改動照樣進提交讓審查員看到。它讓「重貼雜湊」更容易,
所以最後一行一定提醒先確認證據仍成立;證據在語意上還成不成立,機器判不出來,歸審查員。
結束代碼:0 印完(不管有沒有不一樣的);2 清單讀不懂或參數錯。不判通過或擋下,那是驗證器的事。
"""

import sys

sys.dont_write_bytecode = True  # 只印不寫:連匯入驗證器產生的 __pycache__ 都不寫(代碼審)

import argparse  # noqa: E402
import hashlib  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import TextIO  # noqa: E402

try:
    from tools import verify_claims
except ModuleNotFoundError:  # 直接跑 python tools/claim_hashes.py 找不到 tools 套件
    print("請在 repo 根用 python -B -m tools.claim_hashes claims/ 執行", file=sys.stderr)
    raise SystemExit(2) from None

REMINDER = "重貼雜湊前先確認證據仍成立"
SIDES = ("scope", "harness")


def stale_entries(root: Path, claims_dir: Path) -> list[tuple[str, str, str, str]]:
    """(清單檔名, 路徑, 清單寫的雜湊, 現況的雜湊);檔案不存在或路徑不合規則的不列(驗證器會擋)。"""
    stale = []
    for path in sorted(claims_dir.glob("*.json")):
        data, _ = verify_claims.read_json(path)
        for side in SIDES:
            entries = data.get(side) if isinstance(data, dict) else None
            for entry in entries if isinstance(entries, list) else []:
                rel = entry.get("path") if isinstance(entry, dict) else None
                if not isinstance(rel, str) or verify_claims.path_problem(root, rel):
                    continue
                current = hashlib.sha256((root / rel).read_bytes()).hexdigest()
                if current != entry.get("sha256"):
                    stale.append((path.name, rel, str(entry.get("sha256")), current))
    return stale


def run(argv: list[str] | None = None, *, out: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description="只印出清單裡跟現況不一樣的雜湊,不寫任何檔")
    parser.add_argument("claims_dir", help="證據清單目錄(repo 根底下的 claims/)")
    stream = out or sys.stdout
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else verify_claims.EXIT_UNDECIDABLE
    claims_dir = Path(args.claims_dir).resolve()
    if not claims_dir.is_dir():
        print(f"找不到清單目錄 {args.claims_dir}", file=stream)
        return verify_claims.EXIT_UNDECIDABLE
    try:
        stale = stale_entries(claims_dir.parent, claims_dir)
    except verify_claims.Undecidable as exc:
        print(f"無法判定:{exc}", file=stream)
        return verify_claims.EXIT_UNDECIDABLE
    for name, rel, old, new in stale:
        print(f"{name} {rel} 清單寫 {old} 現況是 {new}", file=stream)  # 一檔一行,好 grep
    if not stale:
        print("清單已列的檔雜湊都跟現況一樣(少列的檔不在此列,以驗證器為準)", file=stream)
    print(REMINDER, file=stream)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
