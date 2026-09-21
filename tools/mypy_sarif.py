"""把 mypy 的文字輸出轉成 SARIF 2.1.0,讓 lumos 的「新增告警閘」能擋下新增的型別錯誤。

用法:python tools/mypy_sarif.py --out <輸出檔> [其餘參數原樣交給 mypy]
mypy 沒有現成的 SARIF 輸出;等 lumos 自己提供轉換器之後,這支可以換掉。

工具自己壞掉(mypy 無法執行、輸出看不懂)要大聲失敗,不能寫出一份空報告被當成「沒有告警」。
"""

import argparse
import json
import posixpath
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

MYPY_FLAGS = ["--show-column-numbers", "--show-error-codes", "--no-error-summary",
              "--no-pretty", "--no-color-output"]
LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<column>\d+))?: (?P<level>error|warning|note): "
    r"(?P<message>.*?)(?:  \[(?P<rule>[\w-]+)\])?$"
)
DEFAULT_RULE = "mypy"
ROOT = Path(__file__).resolve().parents[1]
# lumos 執行命令前,會把「改動前」與「現在」的檔案各抽到這種臨時目錄再交給命令
LUMOS_TEMP_PREFIX = re.compile(r"^\.lumos/lintbase-[^/]+/(?:base|head)/")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    level: str
    rule: str
    message: str


def parse_mypy_output(text: str) -> list[Finding]:
    """只收 error 與 warning;note 是前一個錯誤的補充說明,不算獨立的發現。"""
    findings = []
    for raw in text.splitlines():
        match = LINE.match(raw)
        if match is None or match["level"] == "note":
            continue
        findings.append(Finding(
            path=match["path"], line=int(match["line"]), column=int(match["column"] or 1),
            level=match["level"], rule=match["rule"] or DEFAULT_RULE, message=match["message"],
        ))
    return findings


def _project_relative(path: str) -> str | None:
    """相對於專案根目錄的 posix 路徑:先統一分隔符號、解開符號連結、正規化(去掉 ..)。

    在專案根目錄之外就回 None。兩邊都解開符號連結是必要的:macOS 的 /tmp 是 /private/tmp
    的符號連結,同一個檔案用兩種寫法必須得到同一個結果。
    """
    normalized = path.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            return None
    relative = posixpath.normpath(normalized)
    return None if relative == ".." or relative.startswith("../") else relative


def _relative_uri(path: str) -> str:
    """SARIF 的 uri 是 URI 參照:空白與百分號都要編碼(lumos 讀取時會解碼)。"""
    relative = _project_relative(path)
    return quote(relative if relative is not None else Path(path).as_posix(), safe="/:")


def configured_directories() -> list[str]:
    """pyproject 的 [tool.mypy].files:型別檢查的範圍。

    讀不到、是空的或型別不對就丟 ValueError:範圍是空的,等於「什麼都不檢查」,
    不能讓它變成一份乾淨的報告。mypy 也允許把 files 寫成以逗號分隔的字串,這裡一併接受。
    """
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    files = config.get("tool", {}).get("mypy", {}).get("files")
    if isinstance(files, str):
        files = [part.strip() for part in files.split(",")]
    if not isinstance(files, list) or not all(isinstance(item, str) and item for item in files):
        raise ValueError("pyproject 的 [tool.mypy].files 沒有設定、是空的或型別不對")
    if not files:
        raise ValueError("pyproject 的 [tool.mypy].files 是空的,等於什麼都不檢查")
    return files


def _in_scope(relative: str, directory: str) -> bool:
    scope = posixpath.normpath(directory.replace("\\", "/"))
    if scope == ".":  # 範圍寫成 "." 就是整個專案
        return True
    return relative == scope or relative.startswith(scope + "/")


def partition_paths(paths: list[str], directories: list[str]) -> tuple[list[str], list[str]]:
    """把檔案分成「在型別檢查範圍內」與「根本在專案根目錄之外」兩堆;在專案內但不在範圍的直接略過。

    專案根目錄之外的檔案不能被悄悄略過:略過之後全部變空,就會回報「沒有新增告警」,
    而那其實是「工具沒有檢查任何東西」。呼叫端要把它當成失敗。
    """
    kept, outside = [], []
    for raw in paths:
        relative = _project_relative(raw)
        if relative is None:
            outside.append(raw)
            continue
        relative = LUMOS_TEMP_PREFIX.sub("", relative)  # 判斷範圍前先去掉 lumos 的臨時目錄前綴
        if any(_in_scope(relative, directory) for directory in directories):
            kept.append(raw)
    return kept, outside


def select_paths(paths: list[str], directories: list[str]) -> list[str]:
    """只留下在型別檢查範圍內的檔案(例如 src、tools);測試程式不做嚴格型別檢查,不能被丟給 mypy。"""
    return partition_paths(paths, directories)[0]


def to_sarif(findings: list[Finding]) -> dict[str, Any]:
    rules = sorted({finding.rule for finding in findings})
    results = [
        {
            "ruleId": finding.rule,
            "level": finding.level,
            "message": {"text": finding.message},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": _relative_uri(finding.path)},
                    "region": {"startLine": finding.line, "startColumn": finding.column},
                }
            }],
        }
        for finding in findings
    ]
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "mypy", "rules": [{"id": rule} for rule in rules]}},
            "results": results,
        }],
    }




def run_mypy(extra_args: list[str]) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "mypy", *MYPY_FLAGS, *extra_args]
    # 參數是開發者自己在指令列給的,固定執行目前這個 Python 的 mypy,不是外部輸入
    return subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, cwd=ROOT, timeout=600
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mypy 轉 SARIF")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--only-configured-paths", action="store_true",
        help="其餘參數都是檔案路徑(lumos 的 {LINT_FILES}):只檢查在 mypy 設定範圍內的檔案;"
             "全部都在範圍外就不執行 mypy,回報零發現",
    )
    args, mypy_args = parser.parse_known_args(argv)
    if args.only_configured_paths:
        try:
            directories = configured_directories()
        except ValueError as error:
            sys.stderr.write(f"無法決定型別檢查的範圍,拒絕回報乾淨:{error}\n")
            return 2
        kept, outside = partition_paths(mypy_args, directories)
        if outside:
            sys.stderr.write(f"這些檔案在專案根目錄之外,無法判斷是否該檢查,拒絕回報乾淨:{outside}\n")
            return 2
        mypy_args = kept
        if not mypy_args:
            args.out.write_text(json.dumps(to_sarif([]), indent=2), encoding="utf-8")
            return 0
    completed = run_mypy(mypy_args)
    findings = parse_mypy_output(completed.stdout)
    # mypy 的結束碼:0 乾淨、1 有型別錯誤、其他(例如 2)是工具本身出問題
    tool_failed = completed.returncode not in (0, 1) or (
        completed.returncode == 1 and not findings
    )
    if tool_failed:
        sys.stderr.write(f"mypy 無法產出可用的結果(結束碼 {completed.returncode}):\n")
        sys.stderr.write(completed.stdout + completed.stderr)
        return 2
    report = json.dumps(to_sarif(findings), ensure_ascii=False, indent=2)
    args.out.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
