"""整套測試共用的隔離夾具(Phase 11B 增量 1,[S932])。

模型花費帳與即時模式啟用紀錄寫死在帳號家目錄(從帳號資料庫讀,不看 HOME),所以每支測試都用只在測試開的
覆寫環境變數把帳號家目錄指到那支測試的暫存目錄;子行程只要照規定從 os.environ 起頭給環境
(tests/test_suite_isolation.py 用語法樹掃)就跟著繼承。另外清掉模型相關的環境變數、HOME 與 PATH 換成
暫存目錄(PATH 只剩系統基本路徑,找不到真的 claude)。這些都擋不住「子行程自己拼一份不含覆寫的環境」
或別的行程寫真帳,所以整套開始時記下真帳與啟用紀錄的狀態、結束時再比一次,變了就讓整套失敗(兜底)。
"""

import os
import pwd
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from rtb import modelledger_view

# 真的家目錄從帳號資料庫讀,不看 HOME(HOME 在測試裡會被換掉)
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_LEDGER = REAL_HOME / ".rtb" / "model-ledger.sqlite"
REAL_VERIFICATION = REAL_HOME / ".rtb" / "live-verification.json"
ACCOUNT_HOME_ENV = modelledger_view.ACCOUNT_HOME_ENV
MODEL_ENV = ("ANTHROPIC_API_KEY", "RTB_MODEL_LIVE", "RTB_MODEL_RECORD", "RTB_MODEL")
# 整套測試的 PATH:只有每支測試的暫存目錄加系統基本路徑,真的 claude 不在上面([S935])
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def ledger_state(path: Path = REAL_LEDGER) -> tuple[object, ...]:
    """真帳(含 WAL 與共享記憶體檔)與即時模式啟用紀錄的存在與否、大小、修改時間。"""
    state: list[object] = []
    targets = [Path(f"{path}{suffix}") for suffix in ("", "-wal", "-shm")]
    if path == REAL_LEDGER:
        targets.append(REAL_VERIFICATION)
    for suffix, target in zip(("", "-wal", "-shm", "verification"), targets, strict=False):
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
        print(f"\n[S932] 整套測試改動了帳號家目錄的真花費帳或即時模式啟用紀錄:{REAL_LEDGER}、"
              f"{REAL_VERIFICATION}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


_BEFORE = pytest.StashKey[tuple[object, ...]]()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """每支測試再給一個自己的假家目錄、再清一次模型開關([S932];PATH 在 pytest_configure 就換好了)。
    花費帳與啟用紀錄跟著帳號家目錄走、不看 HOME,所以也設帳號家目錄的覆寫環境變數(子行程繼承)。
    另外每支測試
    有自己的暫存目錄(系統共用暫存目錄裡的殘留不影響結果),管理政策來源指到空的暫存目錄(本機的
    MDM 或管理設定不影響結果;代碼審第 1 輪)。
    用自己的替換器,不跟測試共用 `monkeypatch`:測試裡呼叫 `monkeypatch.undo()`(既有幾支測試會)不會把
    家目錄還原成真的。"""
    from rtb import modelclaude

    patch = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    patch.setenv("HOME", str(home))
    patch.setenv(ACCOUNT_HOME_ENV, str(home))
    for name in MODEL_ENV:
        patch.delenv(name, raising=False)
    patch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("tmp")))
    policy = tmp_path_factory.mktemp("managed")
    (policy / "system").mkdir()
    patch.setattr(modelclaude, "MANAGED_DIRS", (policy / "system",))
    patch.setattr(modelclaude, "MDM_PLISTS", (policy / modelclaude.MDM_PLIST_NAME,))
    patch.setattr(modelclaude, "MANAGED_PREFERENCES", policy / "preferences")
    try:
        yield home
    finally:
        patch.undo()
