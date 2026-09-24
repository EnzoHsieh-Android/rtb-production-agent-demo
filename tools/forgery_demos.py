"""造假示範的定義(Phase 12 增量 3 從 tests/tools/test_verify_claims.py 原樣搬來,只搬家、內容不改)。

一個一瞬間跑完的小 repo(幾支小測試、五份證據清單)與計劃列的幾種「看起來完成、其實沒有」的改法。
宣稱驗證器的測試與展示頁的前後比較表(tools/forgery_comparison.py)用同一份定義:比較表的每一列就是
驗證器測試擋下的那幾種造假,加一列驗證器擋不住的天花板。

五個改法是公開名字(代碼審 r1:不另造底線函式加別名);驗證器測試從 FORGERIES 展開、用明確的
項目名字(帶原本的底線寫法),搬家前後展開的項目清單不變。
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED = ("aggregate-blast-radius", "concurrency", "idempotency-unknown-outcome",
            "permission-guardrail", "prompt-injection")


TEST_SERVER = """\
import pytest

from rtb.dsp.server import act
from tests.dsp import samples


def test_update():
    assert act("update") == samples.EXPECTED


def test_pause():
    assert act("pause") == samples.EXPECTED
"""


TEST_FAULTS = """\
import pytest

from rtb.dsp.server import act


@pytest.fixture
def broken(monkeypatch):
    monkeypatch.setenv("FAULT", "timeout_before_commit")
    return True


@pytest.fixture
def layered(broken):
    return broken


def test_fault_via_fixture(broken):
    assert act("update")


def test_fault_via_nested_fixture(layered):
    assert act("update")


def test_fault_via_conftest(crash_after_commit):
    assert act("update")


def test_fault_inline():
    fault = "DEATH"
    assert fault and act("update")


def test_fault_raises():
    fault = "DEATH"
    with pytest.raises(ZeroDivisionError):
        1 / 0


def test_fault_without_assert():
    fault = "DEATH"
    act(fault)


def test_no_fault():
    assert act("update")


def test_redefined():
    fault = "DEATH"
    assert fault and act("update")


def test_redefined():  # noqa: F811 - pytest 跑的是後面這支,它沒有注入
    assert act("update")


def not_a_fixture():
    return "DEATH"


def test_param_named_like_a_helper(not_a_fixture=None):
    assert act("update")


def test_requests_but_never_uses(monkeypatch):
    assert act("update")


class Modes:
    CRASH = 1


def test_fault_via_attribute():
    mode = Modes.CRASH  # 只寫在 assert 條件裡不算引用(代碼審第 2 輪)
    assert mode and act("update")


def assert_ok(value):
    assert value


def test_fault_via_assert_helper():
    fault = "DEATH"
    assert_ok(fault and act("update"))


def test_docstring_only():
    'DEATH'
    assert act("update")


@pytest.mark.skipif(False, reason="DEATH")
def test_decorator_only():
    assert act("update")


def test_default_only(fault="DEATH"):
    assert act("update")


def test_assert_message_only():
    assert act("update"), "DEATH"


def test_substring_only():
    mode = "NO_DEATH_HERE"
    assert mode and act("update")


def test_uncalled_nested_assert():
    fault = "DEATH"

    def never_called():
        assert fault

    act(fault)


def test_dead_branch_assert():
    fault = "DEATH"
    if False:
        assert fault
    act(fault)


def test_assertion_named_helper():
    fault = "DEATH"
    assertion_free(fault)


def assertion_free(value):
    return value


def test_mock_style_assert(monkeypatch):
    fault = "DEATH"

    class Probe:
        def assert_called(self):
            return True

    Probe().assert_called()
    act(fault)


class TestFaults:
    def test_in_class(self, monkeypatch):
        monkeypatch.setenv("X", "1")
        assert act("update")
"""


FILES = {
    "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n',
    "src/rtb/__init__.py": "",
    "src/rtb/kit.py": "def ok():\n    return True\n",
    "src/rtb/dsp/store.py": "from rtb import kit\n\n\ndef apply(action):\n    return kit.ok()\n",
    "src/rtb/dsp/server.py": (
        "from rtb.dsp import store\n\nWRITE_ACTIONS = (\"update\", \"pause\")\n\n\n"
        "def act(action):\n    return store.apply(action)\n\n\n"
        "class Handler:\n    def handle(self):\n        return act(\"update\")\n"
    ),
    "tests/__init__.py": "",
    "tests/dsp/__init__.py": "",
    "tests/dsp/conftest.py": (
        "import pytest\n\n\n@pytest.fixture\ndef unused():\n    return 1\n\n\n"
        "@pytest.fixture\ndef crash_after_commit():\n    return \"after_dsp_commit\"\n"),
    "tests/dsp/test_faults.py": TEST_FAULTS,
    "tests/dsp/samples.py": "EXPECTED = True\n",
    "tests/dsp/test_server.py": TEST_SERVER,
}


SCOPE = ["src/rtb/__init__.py", "src/rtb/dsp/server.py", "src/rtb/dsp/store.py", "src/rtb/kit.py"]


HARNESS = ["pyproject.toml", "tests/__init__.py", "tests/dsp/__init__.py",
           "tests/dsp/conftest.py", "tests/dsp/samples.py", "tests/dsp/test_server.py"]


NODE_UPDATE = "tests/dsp/test_server.py::test_update"


NODE_PAUSE = "tests/dsp/test_server.py::test_pause"


def sha(root: Path, path: str) -> str:
    return hashlib.sha256((root / path).read_bytes()).hexdigest()


def manifest(root: Path, claim_id: str, **changes: Any) -> dict[str, Any]:
    data = {
        "manifest_version": 1,
        "claim_id": claim_id,
        "policy": "每一種寫入動作都只套用一次。",
        "scope": [{"path": p, "sha256": sha(root, p)} for p in changes.pop("scope", SCOPE)],
        "harness": [{"path": p, "sha256": sha(root, p)}
                    for p in changes.pop("harness", HARNESS)],
        "enumerations": [{"source": "src/rtb/dsp/server.py:WRITE_ACTIONS"}],
        "evidence": [
            {"node": NODE_UPDATE, "covers": ["update"], "kind": "unit"},
            {"node": NODE_PAUSE, "covers": ["pause"], "kind": "unit"},
        ],
        "symbols": ["src/rtb/dsp/server.py:act", "src/rtb/dsp/server.py:Handler.handle"],
    }
    data.update(changes)
    return data


def write_manifests(root: Path, **changes: Any) -> None:
    claims = root / "claims"
    claims.mkdir(exist_ok=True)
    for claim_id in REQUIRED:
        text = json.dumps(manifest(root, claim_id, **dict(changes)), ensure_ascii=False, indent=1)
        (claims / f"{claim_id}.json").write_text(text, encoding="utf-8")


def write_files(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


def set_raw(root: Path, claim_id: str, text: str) -> None:
    (root / "claims" / f"{claim_id}.json").write_text(text, encoding="utf-8")


EVIL_CONFTEST_HOOK = (
    "\n\n@pytest.hookimpl(hookwrapper=True)\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    report = (yield).get_result()\n"
    "    if report.when == \"call\":\n        report.outcome = \"passed\"\n"
)


def only_says_done(repo: Path) -> None:
    set_raw(repo, "concurrency", json.dumps({"claim_id": "concurrency", "result": "已完成"},
                                            ensure_ascii=False))


def code_changed_hash_not(repo: Path) -> None:
    (repo / "src/rtb/kit.py").write_text("def ok():\n    return 1\n", encoding="utf-8")


def claim_wider_than_evidence(repo: Path) -> None:
    # 就像 Mock-DSP 那條:登錄表多了作廢,宣稱說「每一種」,卻沒有作廢的證據
    text = FILES["src/rtb/dsp/server.py"].replace('("update", "pause")',
                                                  '("update", "pause", "void")')
    write_files(repo, {"src/rtb/dsp/server.py": text})
    write_manifests(repo)


def skipped_test(repo: Path) -> None:
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause", "@pytest.mark.skip\ndef test_pause")})
    write_manifests(repo)


def conftest_changed_without_rehash(repo: Path) -> None:
    # 測試先改成會失敗、清單照實重算;之後才在 conftest 偷偷加改結果的鉤子,沒重算雜湊
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")})
    write_manifests(repo)
    conftest = repo / "tests/dsp/conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + EVIL_CONFTEST_HOOK,
                        encoding="utf-8")


# ── 前後比較表的六列(Phase 12 增量 3,[S1041])──────────────────────────────────
def rehashed_conftest_rewrite(repo: Path) -> None:
    """天花板:測試先改成會失敗,conftest 加改結果的鉤子,而且連雜湊一起重算。驗證器擋不住(機械上看不出
    重看過還是重貼),歸審查員;清單差異會進提交,審查員看得到。"""
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")})
    conftest = repo / "tests/dsp/conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + EVIL_CONFTEST_HOOK,
                        encoding="utf-8")
    write_manifests(repo)  # 重算雜湊


@dataclass(frozen=True)
class Forgery:
    """一種造假:給人看的說法、在小 repo 上的改法、驗證器擋下時原因裡一定會出現的字(天花板是
    空的)。"""

    label: str
    forge: Callable[[Path], None]
    expected: str | None


FORGERIES = (
    Forgery("只寫「已完成」,沒有附任何證據", only_says_done, "result"),
    Forgery("改了程式,沒重算雜湊", code_changed_hash_not, "src/rtb/kit.py"),
    Forgery("宣稱範圍比證據大(登錄表多了作廢,卻沒有作廢的證據)", claim_wider_than_evidence,
            "void"),
    Forgery("證據測試被跳過", skipped_test, NODE_PAUSE),
    Forgery("conftest 偷改測試結果,沒重算雜湊", conftest_changed_without_rehash,
            "tests/dsp/conftest.py"),
)
CEILING = Forgery("conftest 偷改測試結果,連雜湊一起重算(有驗證器也擋不住,這部分歸人審)",
                  rehashed_conftest_rewrite, None)
