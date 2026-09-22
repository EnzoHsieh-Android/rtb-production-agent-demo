"""執行迴圈的邊界:不匯入 DSP 的內部模組,只經共用 HTTP 用戶端呼叫 DSP(執行一筆 S56)。

直接解析原始碼,不看 noqa(比照分析行程的邊界掃描)。收件口伺服器本身是 HTTP 伺服器,
不在這條規則的範圍:這裡只掃執行迴圈的三支檔。
"""

import ast
from pathlib import Path

from tests.analyzer.test_boundaries import NETWORK_MODULES

EXECUTOR = Path(__file__).resolve().parents[2] / "src" / "rtb" / "executor"
LOOP_FILES = ("execution.py", "dsp_client.py", "runner.py")


def _offenders(source: str, name: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        elif (isinstance(node, ast.Name) and node.id in ("__import__", "import_module")) or (
                isinstance(node, ast.Attribute) and node.attr == "import_module"):
            found.append(f"{name}: 動態匯入")
        for module in modules:
            root = module.split(".")[0]
            if module.startswith("rtb.dsp") or module in NETWORK_MODULES or (
                    root in NETWORK_MODULES):
                found.append(f"{name}: {module}")
    return found


# ---- [S56] ----
def test_the_executor_reaches_the_dsp_only_through_the_shared_client():
    offenders = []
    for name in LOOP_FILES:
        offenders += _offenders((EXECUTOR / name).read_text(encoding="utf-8"), name)
    assert offenders == []
    client = (EXECUTOR / "dsp_client.py").read_text(encoding="utf-8")
    assert "from rtb.httpclient import" in client  # 真的經共用用戶端
    # 掃描器自己會抓:各種寫法各試一次
    for probe in ("import rtb.dsp.store", "from rtb.dsp.server import DspServer",
                  "import urllib.request", "from http import client", "import socket",
                  "__import__('socket')", "importlib.import_module('x')"):
        assert _offenders(probe, "probe"), probe
