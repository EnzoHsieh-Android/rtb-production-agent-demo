"""整套測試共用的隔離夾具(Phase 11B 增量 1,[S932])。

模型花費帳與即時模式啟用紀錄寫死在帳號家目錄(只從帳號資料庫讀,不看任何環境變數),所以 pytest 一啟動就
把讀帳號家目錄的函式換成「還沒設好就丟錯」,每支測試的夾具再換成那支測試的暫存目錄。子行程沒有夾具:
碰得到模型用戶端的子行程程式碼要用 child_prelude 開頭自己換
(tests/test_suite_isolation.py 用語法樹掃)。另外清掉模型相關的環境變數、HOME 與 PATH 換成暫存目錄
(PATH 只剩系統基本路徑,找不到真的 claude),子行程環境一律從 os.environ 起頭才傳得下去。
這些擋不住別的行程寫真帳,所以整套開始時記下真帳與啟用紀錄的狀態、結束時再比一次,變了就讓整套失敗
(兜底)。
"""

import os
import pwd
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from rtb import modelledger_view
from rtb.demo import launcher

# 真的家目錄從帳號資料庫讀,不看 HOME(HOME 在測試裡會被換掉)
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_LEDGER = REAL_HOME / ".rtb" / "model-ledger.sqlite"
REAL_VERIFICATION = REAL_HOME / ".rtb" / "live-verification.json"
PRODUCT_ACCOUNT_HOME = modelledger_view.account_home  # 產品那一支(只讀帳號資料庫)
PRODUCT_MODULE_COMMAND = launcher.module_command  # 產品那一支(展示啟動器起子行程的指令)


def unset_account_home() -> Path:
    """pytest 一啟動就換上的帳號家目錄:還沒被每支測試的夾具換成暫存目錄之前(收集階段、session 或
    module 範圍的夾具)一律丟錯,不回真的家目錄(代碼審第 3 輪)。"""
    raise RuntimeError("測試裡還沒設好帳號家目錄(共用夾具之前),不准碰花費帳或啟用紀錄")


def child_prelude(home: Path) -> str:
    """測試啟動的 Python 子行程程式碼的開頭:換掉帳號家目錄的讀法(子行程沒有共用夾具;產品碼不讀任何
    環境變數,所以只能在子行程裡換)。"""
    return ("from pathlib import Path as _RtbPath\n"
            "from rtb import modelledger_view as _rtb_view\n"
            f"_rtb_view.account_home = lambda: _RtbPath({str(home)!r})\n")


def _run_module(module: str) -> str:
    return ("import runpy, sys\n"
            f"sys.argv[0] = {module!r}\n"
            f"runpy.run_module({module!r}, run_name='__main__', alter_sys=True)\n")


def isolated_module_command(home: Path, module: str, args: Sequence[str]) -> list[str]:
    """展示啟動器在測試裡起子行程的指令:先跑 child_prelude 換掉帳號家目錄,再照原樣跑那支模組。"""
    return [sys.executable, "-P", "-c", child_prelude(home) + _run_module(module), *args]


def demo_server_command() -> list[str]:
    """測試起展示伺服器子行程的指令:伺服器自己換掉帳號家目錄,也讓它的啟動器起的每個子行程都換
    (代碼審 Phase 13 增量 4 r1 l3)。家目錄取這支測試的夾具換上的那一個。"""
    home = modelledger_view.account_home()
    children = child_prelude(home)
    code = (children + "import sys as _rtb_sys\n"
            "from rtb.demo import launcher as _rtb_launcher\n"
            f"_RTB_CHILD = {children!r}\n"
            "def _rtb_command(module, args):\n"
            "    run = ('import runpy, sys\\nsys.argv[0] = ' + repr(module) + '\\n'\n"
            "           + 'runpy.run_module(' + repr(module) + \", run_name='__main__', "
            "alter_sys=True)\\n\")\n"
            "    return [_rtb_sys.executable, '-P', '-c', _RTB_CHILD + run, *args]\n"
            "_rtb_launcher.module_command = _rtb_command\n"
            + _run_module("rtb.demo.server"))
    return [sys.executable, "-c", code]


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
    if path == REAL_LEDGER:  # 真的 ~/.rtb 底下有哪些檔(代碼審 Phase 13 增量 4 r1 l3:不只看那一本帳)
        state.append(("rtb-dir", real_rtb_listing()))
    return tuple(state)


def real_rtb_listing() -> tuple[str, ...]:
    """帳號家目錄(從帳號資料庫讀)底下 .rtb 目錄裡的每一個檔名;目錄不存在是空的。"""
    folder = REAL_HOME / ".rtb"
    return tuple(sorted(p.name for p in folder.iterdir())) if folder.is_dir() else ()


def pytest_configure(config: pytest.Config) -> None:
    """pytest 一啟動就改行程環境(計劃第 7 版):刪掉即時與錄製開關、PATH 換成暫存目錄加系統基本路徑,
    收集階段與各層級夾具都生效,碰不到真的 claude([S935])。"""
    for name in MODEL_ENV:
        os.environ.pop(name, None)
    os.environ["PATH"] = os.pathsep.join((tempfile.mkdtemp(prefix="rtb-test-bin-"), *SYSTEM_PATH))
    os.environ["HOME"] = tempfile.mkdtemp(prefix="rtb-test-home-")  # 收集階段也碰不到真帳
    modelledger_view.account_home = unset_account_home  # 夾具換上暫存目錄之前一律丟錯
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
    花費帳與啟用紀錄跟著帳號家目錄走、不看 HOME,所以也換掉帳號家目錄的讀法(子行程用 child_prelude)。
    另外每支測試
    有自己的暫存目錄(系統共用暫存目錄裡的殘留不影響結果),管理政策來源指到空的暫存目錄(本機的
    MDM 或管理設定不影響結果;代碼審第 1 輪)。
    用自己的替換器,不跟測試共用 `monkeypatch`:測試裡呼叫 `monkeypatch.undo()`(既有幾支測試會)不會把
    家目錄還原成真的。"""
    from rtb import modelclaude

    patch = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    patch.setenv("HOME", str(home))
    patch.setattr(modelledger_view, "account_home", lambda: home)
    for name in MODEL_ENV:
        patch.delenv(name, raising=False)
    patch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("tmp")))
    policy = tmp_path_factory.mktemp("managed")
    (policy / "system").mkdir()
    patch.setattr(modelclaude, "MANAGED_DIRS", (policy / "system",))
    patch.setattr(modelclaude, "MDM_PLISTS", (policy / modelclaude.MDM_PLIST_NAME,))
    patch.setattr(modelclaude, "MANAGED_PREFERENCES", policy / "preferences")
    # 展示啟動器起的子行程也先換掉帳號家目錄的讀法(代碼審 Phase 13 增量 4 r1 l3:它們不吃這個夾具,
    # 起的分析端與模型入口在 pytest 裡寫過真的花費帳)
    patch.setattr(launcher, "module_command", lambda module, args: isolated_module_command(
        home, module, args))
    try:
        yield home
    finally:
        patch.undo()
