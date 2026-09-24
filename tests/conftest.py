"""整套測試共用的隔離夾具(Phase 11B 增量 1,[S932])。

模型花費帳即時模式寫死在使用者家目錄(~/.rtb/model-ledger.sqlite),所以整套測試一律把家目錄指到暫存
目錄、清掉模型相關的環境變數,並把 PATH 換成暫存目錄加系統基本路徑:任何測試(含它啟動的子行程,環境從
這裡繼承)都碰不到真帳,也找不到真的 claude。另外在整套開始時記下真帳的狀態、結束時再比一次,變了就讓
整套失敗。
"""

import os
import pwd
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# 真的家目錄從帳號資料庫讀,不看 HOME(HOME 在測試裡會被換掉)
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_LEDGER = REAL_HOME / ".rtb" / "model-ledger.sqlite"
MODEL_ENV = ("ANTHROPIC_API_KEY", "RTB_MODEL_LIVE", "RTB_MODEL_RECORD", "RTB_MODEL")
# 整套測試的 PATH:只有每支測試的暫存目錄加系統基本路徑,真的 claude 不在上面([S935])
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def ledger_state(path: Path = REAL_LEDGER) -> tuple[object, ...]:
    """真帳(含 WAL 與共享記憶體檔)的存在與否、大小、修改時間。"""
    state: list[object] = []
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{path}{suffix}")
        if target.exists():
            stat = target.stat()
            state.append((suffix, stat.st_size, stat.st_mtime_ns))
        else:
            state.append((suffix, None))
    return tuple(state)


def pytest_configure(config: pytest.Config) -> None:
    """pytest 一啟動就改行程環境(計劃第 7 版):刪掉即時與錄製開關、PATH 換成暫存目錄加系統基本路徑,
    收集階段與各層級夾具都生效,碰不到真的 claude([S935])。"""
    for name in MODEL_ENV:
        os.environ.pop(name, None)
    os.environ["PATH"] = os.pathsep.join((tempfile.mkdtemp(prefix="rtb-test-bin-"), *SYSTEM_PATH))
    os.environ["HOME"] = tempfile.mkdtemp(prefix="rtb-test-home-")  # 收集階段也碰不到真帳
    config.stash[_BEFORE] = ledger_state()


def pytest_sessionfinish(session: pytest.Session) -> None:
    before = session.config.stash.get(_BEFORE, None)
    if before is not None and ledger_state() != before:
        print(f"\n[S932] 整套測試改動了使用者家目錄的真花費帳:{REAL_LEDGER}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


_BEFORE = pytest.StashKey[tuple[object, ...]]()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """每支測試再給一個自己的假家目錄、再清一次模型開關([S932];PATH 在 pytest_configure 就換好了)。
    用自己的替換器,不跟測試共用 `monkeypatch`:測試裡呼叫 `monkeypatch.undo()`(既有幾支測試會)不會把
    家目錄還原成真的。"""
    patch = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    patch.setenv("HOME", str(home))
    for name in MODEL_ENV:
        patch.delenv(name, raising=False)
    try:
        yield home
    finally:
        patch.undo()
