"""模型用戶端測試的替身(計劃第 6 版):

- `FakeBackend`:行程內的假後端,實作「送出一次呼叫」的介面,給花費帳、錄製、上限、評估這些跟後端無關的
  測試用,不啟動任何子行程。
- `fake_claude`:一支假的 claude 指令(/bin/sh 腳本,印固定 JSON),給 Claude Code 後端的測試用;它把每次
  被呼叫的參數、環境、工作目錄、標準輸入記到自己旁邊的紀錄目錄。絕對路徑直接傳給模型用戶端,不經 PATH
  ([S935])。
不呼叫真的 claude、不發網路請求。
"""

import json
import os
import pwd
import shlex
import threading
from pathlib import Path

from rtb import modelclaude as cc
from rtb import modelclient as mc
from rtb import modelcore as core
from rtb import modelledger_view as view


def reply(  # noqa: PLR0913 - 回應的每一欄
        text='{"verdict": "not_worth"}', input_tokens=100, output_tokens=10, *,
          cache_5m=0, cache_1h=0, cache_read=0, reported_usd=None):
    return mc.BackendReply(
        text=text, input_tokens=input_tokens, output_tokens=output_tokens,
        cache_write_5m_tokens=cache_5m, cache_write_1h_tokens=cache_1h,
        cache_read_tokens=cache_read,
        reported_nanousd=None if reported_usd is None else round(reported_usd * 10**9))


class FakeBackend:
    """依序回應(最後一個重複用):元素是 BackendReply、例外物件,或收到呼叫後回應的函式。"""

    kind = core.Backend.CLAUDE_CODE

    def __init__(self, *responses):
        self.responses = list(responses) or [reply()]
        self.calls = []
        self._lock = threading.Lock()

    def send(self, call):
        with self._lock:
            self.calls.append(call)
            response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(call)
        return response


def live(backend=None, record=False, model=mc.DEFAULT_MODEL):
    return mc.Settings(mode=mc.Mode.LIVE, model=model, record=record,
                       backend=backend if backend is not None else FakeBackend(), notices=())


def recorded(model=mc.DEFAULT_MODEL):
    return mc.Settings(mode=mc.Mode.RECORDED, model=model, record=False, backend=None,
                       notices=())


def request(user="hello", *, demo_id="demo-1", batch_id=None, max_output_tokens=50,
            caller=mc.Caller.EVAL_CANDIDATE, timeout_seconds=5.0):
    return mc.ModelRequest(caller=caller, system="固定系統提示", user=user,
                           max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds,
                           demo_id=demo_id, batch_id=batch_id)


# ---- 假的 claude 指令 ----
def claude_json(  # noqa: PLR0913 - JSON 輸出的每一欄
        result='{"verdict": "not_worth"}', *, num_turns=1, is_error=False, denials=(),
                input_tokens=100, output_tokens=10, cache_creation=0, cache_read=0,
                cost_usd=0.0003, subtype=None, usage=True, **extra):
    """Claude Code 非互動 JSON 輸出的形狀(欄位名以實作當下的 claude 為準;錯誤樣本待協調者錄製時用
    真實輸出校正)。usage=False 時不給用量欄位。"""
    data = {"type": "result", "subtype": subtype or ("error" if is_error else "success"),
            "is_error": is_error, "num_turns": num_turns, "result": result,
            "total_cost_usd": cost_usd, "permission_denials": list(denials), **extra}
    if usage:
        data["usage"] = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                         "cache_creation_input_tokens": cache_creation,
                         "cache_read_input_tokens": cache_read}
    return data


FAKE_VERSION = "9.9.9 (Claude Code)"
# 產品那一支讀帳號家目錄的函式(匯入時記下;pytest 裡此時已被換成丟錯的那支,pytest 外就是產品的)與
# 帳號資料庫的真家目錄:寫啟用紀錄前拿來比
PRODUCT_ACCOUNT_HOME = view.account_home
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def write_verification(version=FAKE_VERSION, isolation="empty_home", **checks):
    """在帳號家目錄(測試裡是共用夾具換上的暫存目錄)寫一份即時模式啟用紀錄(預設全過、版本跟假 claude
    一樣)。帳號家目錄還是產品那一支(沒被夾具換掉,例如在 pytest 外呼叫),或算出來的路徑在真的家目錄
    底下,就拒寫:帳號家目錄不看 HOME,在 pytest 外呼叫會寫進真的 ~/.rtb(代碼審第 2 輪:真的發生過)。"""
    if view.account_home is PRODUCT_ACCOUNT_HOME:
        raise RuntimeError("write_verification 只能在 pytest 的共用夾具底下用(會寫帳號家目錄)")
    path = cc.verification_path()
    if path.is_relative_to(REAL_HOME):
        raise RuntimeError(f"啟用紀錄會寫進真的家目錄,拒寫:{path}")
    record = {"claude_version": version, "isolation": isolation, "checked_on": "2026-09-24",
              "checks": {**dict.fromkeys(cc.REQUIRED_CHECKS, True), **checks}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def fake_claude(  # noqa: PLR0913 - 假指令的每種行為
        directory: Path, output: object = None, *, code: int = 0, logged_in: bool = True,
        sleep: float = 0, grandchild: bool = False, executable: bool = True,
        auth_sleep: float = 0, stderr: str = "") -> Path:
    """在 directory 建一支假的 claude 並回傳它的絕對路徑。output 是 dict(印成 JSON)或字串(原樣印)。
    每次被呼叫(登入狀態檢查以外)都在 `<腳本>.log/` 底下記一組 args、env、cwd、stdin 檔。"""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "claude"
    log = directory / "claude.log"
    log.mkdir(exist_ok=True)
    body = output if output is not None else claude_json()
    payload = directory / "claude.out"
    payload.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    auth = json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai"})
    q = shlex.quote
    lines = [
        "#!/bin/sh",
        f'if [ "$1" = "auth" ]; then /bin/sleep {auth_sleep}; /bin/echo {q(auth)}; exit 0; fi',
        f'if [ "$1" = "--version" ]; then /bin/echo {q(FAKE_VERSION)}; exit 0; fi',
        f"n={q(str(log))}/$$",
        'for a in "$@"; do printf "%s\\n" "$a"; done > "$n.args"',
        '/usr/bin/env > "$n.env"',
        '/bin/pwd > "$n.cwd"',
        '/bin/ls -A > "$n.ls"',
        '/bin/cat > "$n.stdin"',
    ]
    if grandchild:
        lines.append(f"(/bin/sleep 30; /usr/bin/touch {q(str(directory / 'grandchild.alive'))}) & "
                     f"echo $! > {q(str(directory / 'grandchild.pid'))}")
    lines.append('/bin/pwd > "$n.started"')  # 讀完標準輸入、孫行程也起了:給中斷測試等
    if sleep:
        lines.append(f"/bin/sleep {sleep}")
    if stderr:
        lines.append(f"/bin/echo {q(stderr)} >&2")
    lines += [f"/bin/cat {q(str(payload))}", f"exit {code}"]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755 if executable else 0o644)
    return script


def invocations(script: Path) -> list[dict]:
    """假 claude 被呼叫過的每一次:參數、環境、工作目錄、標準輸入。"""
    log = script.with_name("claude.log")
    found = []
    for args in sorted(log.glob("*.args")):
        stem = args.with_suffix("")
        env = dict(line.split("=", 1) for line in
                   Path(f"{stem}.env").read_text(encoding="utf-8").splitlines() if "=" in line)
        found.append({"args": args.read_text(encoding="utf-8").splitlines(), "env": env,
                      "cwd": Path(f"{stem}.cwd").read_text(encoding="utf-8").strip(),
                      "cwd_listing": Path(f"{stem}.ls").read_text(encoding="utf-8").split(),
                      "stdin": Path(f"{stem}.stdin").read_text(encoding="utf-8")})
    return found
