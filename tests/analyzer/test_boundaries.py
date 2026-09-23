"""Phase 1 留下的兩條邊界測試,增量 4 第一次有真的 DSP 客戶端時補驗:S52、S53。"""

import ast
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rtb.analyzer import dsp_client, flow, inbox_client, policy
from rtb.analyzer.task_store import TaskStore
from rtb.domain.task_state import TaskState
from rtb.dsp.server import DspServer
from rtb.dsp.store import CampaignStore
from rtb.executor.inbox_server import InboxServer


# ---- S53 ----
def test_the_analyzer_package_never_imports_dsp_internals():
    config = Path(__file__).resolve().parents[2] / "src" / "rtb" / "analyzer" / "ruff.toml"
    for banned in ("rtb.dsp", "rtb.executor"):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--config", str(config),
             "--stdin-filename", str(config.parent / "probe.py"), "-"],
            input=f"import {banned}\n", capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 1 and "TID251" in result.stdout, (banned, result.stdout)


# ---- S52 ----
def test_the_agent_cannot_reach_fault_injection_on_production_style_servers(tmp_path):
    """DSP 與收件口都不帶 --fault-injection 啟動(生產樣態),真的用戶端跑完整套正常流程。"""
    dsp_store = CampaignStore(tmp_path / "dsp.db")
    dsp_store.seed_campaign("c1", budget=100)
    dsp_store.seed_metrics("c1", "1h", impressions=500, clicks=12, conversions=1, spend=1.0,
                           revenue=3.0)
    # 兩個伺服器各自進自己的 try/finally:第二個建構或啟動失敗,第一個已經開的監聽 socket
    # 也要關掉,不能等 GC 回收(代碼審第 1 輪指出,舊寫法把兩個建構都放在同一個保護傘外面)。
    dsp = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2, delay_seconds=0.0)
    try:
        threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
        inbox = InboxServer(tmp_path / "inbox.db", max_pending=4, fault_injection=False)
        try:
            threading.Thread(target=inbox.serve_forever, args=(0.02,), daemon=True).start()
            _walk_one_task_end_to_end(tmp_path, dsp, inbox)
        finally:
            inbox.shutdown()
            inbox.server_close()
    finally:
        dsp.shutdown()
        dsp.server_close()


def _walk_one_task_end_to_end(tmp_path, dsp, inbox):
    dsp_url = f"http://127.0.0.1:{dsp.server_address[1]}"
    inbox_url = f"http://127.0.0.1:{inbox.server_address[1]}"
    store = TaskStore(tmp_path / "analyzer.db")
    try:
        now = datetime.now(UTC)
        store.create_task("t1", "c1", now)
        evidence_source = dsp_client.make_client(dsp_url, timeout_seconds=3)
        submit = inbox_client.make_client(inbox_url, timeout_seconds=3)

        def step():  # 整批共用同一個時間:推進者很自然的寫法,不能因此卡在蒐證與分析之間彈跳
            return flow.advance(store, "t1", evidence_source, policy.decide, submit, now)

        assert step() is TaskState.COLLECTING_EVIDENCE
        assert step() is TaskState.ANALYZING
        state = step()
        assert state in (TaskState.PROPOSED, TaskState.NO_ACTION)
        if state is TaskState.PROPOSED:
            assert step() is TaskState.HANDED_OFF
    finally:
        store.close()


def test_fault_headers_sent_to_production_style_servers_are_refused(tmp_path):
    """就算有人手動組出 X-Fault 標頭送給沒開故障注入的伺服器,伺服器自己也會拒絕——
    這是最後一道防線,真正的防線是用戶端從沒有能送出這個標頭的公開介面(見 test_httpclient.py)。
    """
    import http.client

    dsp_store = CampaignStore(tmp_path / "dsp.db")
    dsp_store.seed_campaign("c1", budget=100)
    dsp = DspServer(tmp_path / "dsp.db", fault_injection=False, hang_seconds=0.2, delay_seconds=0.0)
    threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", dsp.server_address[1], timeout=3)
        conn.request("GET", "/campaigns/c1", headers={"X-Fault": "timeout_before_commit"})
        resp = conn.getresponse()
        assert resp.status == 400
        conn.close()
    finally:
        dsp.shutdown()
        dsp.server_close()


NETWORK_MODULES = frozenset({
    "urllib", "http", "socket", "ssl", "socketserver", "asyncio", "requests", "httpx", "urllib3",
    "aiohttp", "ftplib", "smtplib", "xmlrpc", "rtb.httpkit",
})


def test_the_analyzer_reaches_the_network_only_through_the_shared_client():
    """2026-09-22 第二輪合約審計指出:標頭的封閉列舉只保護走共用用戶端的程式;分析行程裡
    另寫一個直接用 urllib 發請求的小工具,就能把任意標頭(含故障注入)送出去,原本的測試
    與原始碼掃描都看不到。這裡直接解析原始碼:分析行程不准自己匯入網路模組,也不准動態匯入。"""
    analyzer = Path(__file__).resolve().parents[2] / "src" / "rtb" / "analyzer"
    offenders = []
    for file in sorted(analyzer.rglob("*.py")):
        offenders += _network_offenders(file.name, ast.parse(file.read_text(encoding="utf-8")))
    assert offenders == []


def _network_offenders(label, tree):
    """自己匯入網路模組或動態匯入的地方(分析行程目錄與 [S408] 的匯入閉包共用這一份判斷)。

    只看名稱、不追它綁到哪裡:任何叫 import_module 的名字或屬性都算動態匯入,刻意從嚴(寧可誤報、
    不漏)。代價是閉包裡的模組不能有同名的普通函式;真的撞到就改名,不要放寬這裡(代碼審第 2 輪)。"""
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        elif (isinstance(node, ast.Name) and node.id in ("__import__", "import_module")) or (
            isinstance(node, ast.Attribute) and node.attr == "import_module"
        ) or (isinstance(node, ast.alias) and node.name == "import_module"):
            offenders.append(f"{label}: 動態匯入")  # 連引用或別名都不准,不只直接呼叫
        offenders += [f"{label}: {m}" for m in modules
                      if m in NETWORK_MODULES or m.split(".")[0] in NETWORK_MODULES]
    return offenders


# ---- 寫入能力憑證 S30 ----
def test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer():
    """直接解析分析行程的原始碼,不看 noqa:ruff 禁令一行 noqa 就能跳過(領域層踩過同一個坑)。"""
    import ast

    from rtb.capabilitykit import KEY_ENV
    from rtb.executor import capability_signer

    analyzer = Path(__file__).resolve().parents[2] / "src" / "rtb" / "analyzer"
    offenders = []
    for file in sorted(analyzer.rglob("*.py")):
        source = file.read_text(encoding="utf-8")
        if KEY_ENV in source:
            offenders.append((file.name, "key env name"))
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.startswith(("rtb.executor", "rtb.capabilitykit")):
                    offenders.append((file.name, name))
    assert offenders == []
    assert capability_signer.__name__.startswith("rtb.executor.")  # 簽發器住在被禁的套件裡


def test_importing_the_analyzer_does_not_load_the_capability_module():
    """S30 的補強:原始碼掃描只看直接匯入;這裡在乾淨的子行程裡載入整個分析行程,
    確認連間接匯入(例如經共用 HTTP 用戶端)都沒有把憑證模組帶進來。"""
    import os
    import subprocess
    import sys

    src = Path(__file__).resolve().parents[2] / "src"
    analyzer = src / "rtb" / "analyzer"
    modules = sorted(f"rtb.analyzer.{f.stem}" for f in analyzer.glob("*.py")
                     if f.stem != "__init__")
    code = ("import importlib, sys\n"
            f"for name in {modules!r}: importlib.import_module(name)\n"
            "banned = ('rtb.capabilitykit', 'rtb.executor')\n"
            "print(sorted(m for m in sys.modules if m.startswith(banned)))")
    env = {**os.environ, "PYTHONPATH": str(src)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True, env=env)
    assert out.stdout.strip() == "[]"


# ---- Phase 6 增量 2 [S408] ----
_ALLOWED_CALLERS = {"rtb.analyzer.dsp_client": "GET", "rtb.analyzer.inbox_client": "POST"}
_HTTP_MODULE, _REQUEST = "rtb.httpclient", "request_json"


def _source_of(src, module):
    """本專案模組的原始碼檔;沒有原始碼檔(命名空間套件、不存在)回 None,跳過不當錯。"""
    base = src.joinpath(*module.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _imported_modules(src, module, tree):
    """檔案裡任何位置(含函式內部、型別檢查專用區塊)的匯入,相對匯入依層數接成完整模組名。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_from(src, module, node)
            found.add(target)
            found |= {f"{target}.{alias.name}" for alias in node.names}  # 從套件匯入子模組
    return {name for name in found if name == "rtb" or name.startswith("rtb.")}


def analyzer_import_closure(src):
    """分析行程原始碼的靜態匯入閉包:{模組名: 語法樹}。"""
    analyzer = src / "rtb" / "analyzer"
    # 套件初始化檔也算(代碼審第 1 輪外家席):匯入任何子模組都會先執行它所在各層套件的初始化檔
    pending = [f"rtb.analyzer.{path.stem}" for path in sorted(analyzer.glob("*.py"))
               if path.stem != "__init__"]
    closure = {}
    while pending:
        module = pending.pop()
        if module in closure:
            continue
        parts = module.split(".")
        pending += [".".join(parts[:i]) for i in range(1, len(parts))]  # 沿途的父套件
        path = _source_of(src, module)
        if path is None:
            continue
        closure[module] = ast.parse(path.read_text(encoding="utf-8"))
        pending += sorted(_imported_modules(src, module, closure[module]) - set(closure))
    return closure


def _resolve_from(src, module, node):
    """from 匯入的完整模組名;相對匯入依點數往上找,跟所在檔案的套件路徑接起來。"""
    if not node.level:
        return node.module or ""
    is_package = _source_of(src, module).name == "__init__.py"
    parts = (module if is_package else module.rpartition(".")[0]).split(".")
    base = ".".join(parts[:len(parts) - node.level + 1])
    return f"{base}.{node.module}" if node.module else base


def _check_http_imports(src, module, tree):
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
                a.name.split(".")[:2] == _HTTP_MODULE.split(".") for a in node.names):
            offenders.append(f"{module}: 匯入整個共用 HTTP 用戶端模組")
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_from(src, module, node)
            names = {(a.name, a.asname) for a in node.names}
            if f"{target}.httpclient" == _HTTP_MODULE and any(n == "httpclient" for n, _ in names):
                offenders.append(f"{module}: 從套件匯入共用 HTTP 用戶端模組")
            if target == _HTTP_MODULE and (module not in _ALLOWED_CALLERS
                                           or names != {(_REQUEST, None)}):
                offenders.append(f"{module}: 從共用 HTTP 用戶端匯入 {sorted(names)}")
            if target != _HTTP_MODULE and any(n == _REQUEST for n, _ in names):
                # 從允許的用戶端轉手匯入再改名,名字就不再是請求函式(代碼審第 3 輪外家席)
                offenders.append(f"{module}: 從 {target} 轉手匯入請求函式")
    return offenders


def _check_request_uses(module, tree):
    """請求函式每一次出現都要是直接呼叫,方法是字面常數、不帶標頭;回傳 (違規, 方法清單)。"""
    calls = {id(node.func): node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    offenders, methods = [], []
    for node in ast.walk(tree):
        mentioned = (isinstance(node, ast.Name) and node.id == _REQUEST) or (
            isinstance(node, ast.Attribute) and node.attr == _REQUEST)
        if not mentioned:
            continue
        call = calls.get(id(node))
        if call is None or module not in _ALLOWED_CALLERS:
            offenders.append(f"{module}:{node.lineno} 提到請求函式卻不是允許的直接呼叫")
            continue
        method = call.args[1] if len(call.args) > 1 else next(
            (k.value for k in call.keywords if k.arg == "method"), None)
        has_headers = len(call.args) > 4 or any(k.arg in ("headers", None) for k in call.keywords)
        if not (isinstance(method, ast.Constant) and isinstance(method.value, str)) or has_headers:
            offenders.append(f"{module}:{node.lineno} 方法不是字面常數或帶了標頭")
            continue
        url = call.args[0] if call.args else None
        methods.append((method.value, url))
    return offenders, methods


def write_call_offenders(src):
    offenders = []
    for module, tree in sorted(analyzer_import_closure(src).items()):
        if module == _HTTP_MODULE:
            continue
        offenders += _check_http_imports(src, module, tree)
        # 繞過共用用戶端自己發請求:網路模組與動態匯入禁令套到整個閉包(代碼審第 1 輪外家席)
        offenders += _network_offenders(module, tree)
        found, methods = _check_request_uses(module, tree)
        offenders += found
        if module == "rtb.analyzer.dsp_client":
            offenders += [f"{module}: DSP 用戶端用了 {m}" for m, _ in methods if m != "GET"]
        if module == "rtb.analyzer.inbox_client":
            posts = [url for m, url in methods if m == "POST"]
            to_proposals = [u for u in posts if isinstance(u, ast.JoinedStr) and isinstance(
                u.values[-1], ast.Constant) and u.values[-1].value == "/proposals"]
            if len(posts) != 1 or len(to_proposals) != 1 or len(methods) != len(posts):
                offenders.append(f"{module}: 收件口用戶端應只有一個送到提案路徑的 POST")
    return offenders


def test_the_analyzer_has_no_write_call_besides_submitting_a_proposal():
    src = Path(__file__).resolve().parents[2] / "src"
    closure = analyzer_import_closure(src)
    assert {"rtb.analyzer.dsp_client", "rtb.analyzer.inbox_client", _HTTP_MODULE} <= set(closure)
    assert not any(m.startswith(("rtb.executor", "rtb.dsp")) for m in closure)  # 閉包沒拉進執行側
    assert write_call_offenders(src) == []


_FORGETFUL = {  # 「一不小心」會寫出來的寫法:(要改的檔, 加在檔尾的程式)
    "dsp_post": ("analyzer/dsp_client.py",
                 '\ndef _w(u):\n    return request_json(u, "POST", {}, 1)\n'),
    "alias_assign": ("analyzer/dsp_client.py", "\n_rj = request_json\n"),
    "partial": ("analyzer/dsp_client.py",
                "\nimport functools\n_p = functools.partial(request_json)\n"),
    "import_as": ("analyzer/dsp_client.py", "\nfrom rtb.httpclient import request_json as _rj\n"),
    "reexport_alias": ("analyzer/policy.py",
                       "\nfrom rtb.analyzer.dsp_client import request_json as _send\n"),
    "reexport_attribute": ("analyzer/policy.py",
                           "\nfrom rtb.analyzer import dsp_client as _d\n"
                           "_send = _d.request_json\n"),
    "method_variable": ("analyzer/dsp_client.py",
                        '\ndef _w(u):\n    m = "GET"\n    return request_json(u, m, None, 1)\n'),
    "headers": ("analyzer/dsp_client.py",
                '\ndef _w(u):\n    return request_json(u, "GET", None, 1, headers={})\n'),
    "module_import": ("analyzer/policy.py", "\nimport rtb.httpclient as _h\n"),
    "package_import": ("analyzer/policy.py", "\nfrom rtb import httpclient\n"),
    "relative_in_function": ("analyzer/flow.py",
                             "\ndef _w():\n    from ..httpclient import request_json\n"),
    "second_entry": ("httpclient.py",
                     "\ndef post_json(u):\n    return request_json(u, 'POST', {}, 1)\n"),
}


@pytest.mark.parametrize("variant", [*sorted(_FORGETFUL), "domain_helper", "package_init",
                                     "domain_package_init", "domain_urllib"])
def test_the_write_call_scan_catches_forgetful_variants(tmp_path, variant):
    """[S408] 的殺傷力:複製一份原始碼,放進一種不小心的寫法,掃描就要找到。"""
    import shutil

    src = tmp_path / "src"
    shutil.copytree(Path(__file__).resolve().parents[2] / "src" / "rtb", src / "rtb")
    if variant == "package_init":  # 套件初始化檔:匯入任何子模組都會先執行它
        target, extra = "analyzer/__init__.py", (
            "from rtb.httpclient import request_json\n\n\ndef _w(u):\n"
            "    return request_json(u, 'POST', {}, 1)\n")
    elif variant == "domain_package_init":  # 沒有人寫 from rtb.domain import:只靠「沿途父套件」
        target, extra = "domain/__init__.py", (
            "from rtb.httpclient import request_json\n\n\ndef _w(u):\n"
            "    return request_json(u, 'POST', {}, 1)\n")
    elif variant == "domain_urllib":  # 領域層小函式繞過共用用戶端,直接用標準庫發請求
        (src / "rtb" / "domain" / "raw_post.py").write_text(
            "from urllib.request import Request, urlopen\n\n\ndef post(u):\n"
            "    return urlopen(Request(u, method='POST'))\n", encoding="utf-8")
        target, extra = "analyzer/flow.py", "\ndef _w():\n    from rtb.domain import raw_post\n"
    elif variant == "domain_helper":  # 領域層順手加一支發請求的函式,分析端在函式裡才匯入它
        (src / "rtb" / "domain" / "write_helper.py").write_text(
            "from rtb.httpclient import request_json\n\n\ndef post(u):\n"
            "    return request_json(u, 'POST', {}, 1)\n", encoding="utf-8")
        target, extra = "analyzer/flow.py", "\ndef _w():\n    from rtb.domain import write_helper\n"
    elif variant == "second_entry":
        with (src / "rtb" / "httpclient.py").open("a", encoding="utf-8") as file:
            file.write(_FORGETFUL[variant][1])
        target, extra = "analyzer/dsp_client.py", "\nfrom rtb.httpclient import post_json\n"
    else:
        target, extra = _FORGETFUL[variant]
    with (src / "rtb" / target).open("a", encoding="utf-8") as file:
        file.write(extra)

    assert write_call_offenders(src) != [], variant
