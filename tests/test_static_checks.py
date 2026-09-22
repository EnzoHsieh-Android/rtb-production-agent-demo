"""靜態檢查閘:型別檢查(mypy)與程式碼規則(ruff)必須是乾淨的。

這兩個檢查已經接進 lumos 的 lint 宣告(推送前)與 CI(推送後),這裡再放一份測試,理由是:
pytest 只要跑就會檢查,拿不到 lumos 或 CI 環境的地方(例如只有 .venv 的乾淨機器)也守得住。
三處各自獨立,任何一處失守,其他兩處仍會抓到。
"""

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *args], capture_output=True, text=True, cwd=ROOT, timeout=300
    )


def test_mypy_strict_mode_reports_no_type_errors_in_src():
    result = run_tool("mypy")

    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_reports_no_rule_violations_anywhere_in_the_project():
    result = run_tool("ruff", "check", ".")

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_type_checker_would_actually_catch_a_type_error(tmp_path):
    """守衛的守衛:確認 mypy 真的會對型別錯誤變紅,而不是設定壞了永遠綠。"""
    bad = tmp_path / "bad.py"
    bad.write_text("def f(x: int) -> str:\n    return x\n", encoding="utf-8")

    result = run_tool("mypy", "--strict", str(bad))

    assert result.returncode != 0 and "incompatible return value" in result.stdout.lower()


def test_the_first_party_package_is_declared_so_import_sorting_does_not_depend_on_the_directory():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    isort = config["tool"]["ruff"]["lint"]["isort"]
    assert {"rtb", "tests"} <= set(isort["known-first-party"])
