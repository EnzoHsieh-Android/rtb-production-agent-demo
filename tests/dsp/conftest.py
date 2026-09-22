"""DSP 行程測試的共用工具:真的啟動獨立行程、埠由系統分配、結束時一定關閉。"""

import contextlib
import json
import os
import select
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")
_DEFAULT_KEY = object()


class DspProcess:
    def __init__(self, db_path, fault_injection=False, hang_seconds=1.5, delay_seconds=0.3,  # noqa: PLR0913 - 對應 DSP 的啟動參數
                 busy_timeout_seconds=5.0, socket_timeout_seconds=10.0,
                 capability_key=_DEFAULT_KEY):
        cmd = [sys.executable, "-m", "rtb.dsp.server", "--db", str(db_path),
               "--hang-seconds", str(hang_seconds), "--delay-seconds", str(delay_seconds),
               "--busy-timeout-seconds", str(busy_timeout_seconds),
               "--socket-timeout-seconds", str(socket_timeout_seconds)]
        if fault_injection:
            cmd.append("--fault-injection")
        from rtb.capabilitykit import KEY_ENV
        from tests.capability_samples import TEST_KEY

        key = TEST_KEY if capability_key is _DEFAULT_KEY else capability_key
        env = {**os.environ, "PYTHONPATH": SRC}
        env.pop(KEY_ENV, None)  # 不給金鑰時要明確刪掉,不能靠「不設定」而繼承父行程遺留的
        if key is not None:
            env[KEY_ENV] = key.decode()  # 經環境變數傳給子行程,走真實的啟動程式讀取路徑
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, env=env)
        try:
            self.base = f"http://127.0.0.1:{self._read_port()}"
        except BaseException:
            self.stop()  # 啟動失敗也不能留下孤兒行程
            raise

    def _read_port(self, wait_seconds=15.0):
        """讀到換行或超過期限為止;即使子行程只印了半行也不會卡住。"""
        deadline, buffer = time.monotonic() + wait_seconds, b""
        fd = self.proc.stdout.fileno()
        while b"\n" not in buffer:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([fd], [], [], max(remaining, 0))
            assert ready, f"DSP 在 {wait_seconds} 秒內沒有印出完整的埠行(目前:{buffer!r})"
            chunk = os.read(fd, 256)
            assert chunk, f"DSP 提前結束,沒有印出埠(目前:{buffer!r})"
            buffer += chunk
        line = buffer.split(b"\n")[0].decode().strip()
        assert line.startswith("PORT="), f"DSP 沒有印出埠:{line!r}"
        return line.split("=")[1]

    def stop(self):
        try:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
        finally:
            self.proc.stdout.close()

    def request(self, method, path, body=None, headers=None, timeout=3.0):
        """回 (狀態碼, 解析後的 JSON)。逾時會丟 TimeoutError,不吞掉。"""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())


@pytest.fixture
def start_dsp(tmp_path):
    started = []

    def _start(fault_injection=False, seed=True, **kwargs):
        from rtb.dsp.store import CampaignStore

        db = tmp_path / "dsp.db"
        if seed and not db.exists():
            CampaignStore(db).seed_campaign("c1", budget=100)
        proc = DspProcess(db, fault_injection=fault_injection, **kwargs)
        started.append(proc)
        return proc

    yield _start
    for proc in started:
        with contextlib.suppress(Exception):  # 一個行程收不掉,也要繼續收其他的
            proc.stop()
