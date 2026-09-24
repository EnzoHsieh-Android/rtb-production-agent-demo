"""宣稱驗證器(Phase 11 增量 1):依證據清單做機械檢查,自己跑證據測試,印出通過或擋下。

驗證器的測試一律在 tmp_path 裡造一個小 repo(幾支一瞬間跑完的小測試),不跑正式證據:tests/ 底下的
測試會被 checks 工作的全套收集,在這裡真跑正式證據會讓 F7 在同一個工作裡巢狀多跑一次。正式五份清單
只檢查前幾步([S809]),真跑交給 CI 的平行工作。
"""

import ast
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_static_wiring import ci_problems, parse_run_commands

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "tools" / "verify_claims.py"
HASH_HELPER = ROOT / "tools" / "claim_hashes.py"
REQUIRED = ("aggregate-blast-radius", "concurrency", "idempotency-unknown-outcome",
            "permission-guardrail", "prompt-injection")
COMMAND = "python tools/verify_claims.py claims/"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_claims", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_claims"] = module
    spec.loader.exec_module(module)
    return module


# ── 小 repo ──────────────────────────────────────────────────────────────────
# src/rtb/dsp 沒有 __init__.py(命名空間套件,跟正式專案一樣),server 用
# `from rtb.dsp import store` 匯入 store:只解析套件會漏掉 store.py。

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


def sha(root, path):
    return hashlib.sha256((root / path).read_bytes()).hexdigest()


def manifest(root, claim_id, **changes):
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


def write_manifests(root, **changes):
    claims = root / "claims"
    claims.mkdir(exist_ok=True)
    for claim_id in REQUIRED:
        text = json.dumps(manifest(root, claim_id, **dict(changes)), ensure_ascii=False, indent=1)
        (claims / f"{claim_id}.json").write_text(text, encoding="utf-8")


def write_files(root, files):
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    write_files(tmp_path, FILES)
    write_manifests(tmp_path)
    return tmp_path


def set_raw(root, claim_id, text):
    (root / "claims" / f"{claim_id}.json").write_text(text, encoding="utf-8")


def verify(root, **kwargs):
    module = load_verifier()
    out = io.StringIO()
    code = module.run([str(root / "claims")], out=out, **kwargs)
    return code, out.getvalue()


def test_a_clean_repo_passes_and_actually_runs_the_evidence(repo):
    code, output = verify(repo)

    assert code == 0, output
    assert "通過" in output
    assert "2 支證據測試" in output  # 兩個節點都真的跑了


# ── [S800] 格式:每一層白名單、重複鍵、NaN、布林不是整數 ──────────────────────

def _with_key(root, where, key, value):
    data = manifest(root, "concurrency")
    target = data if where == "top" else data[where][0]
    target[key] = value
    return json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize(("where", "key"), [
    ("top", "result"), ("evidence", "passed"), ("scope", "status"),
    ("harness", "verdict"), ("enumerations", "done"),
])
def test_a_manifest_with_any_unknown_key_is_blocked(repo, where, key):
    set_raw(repo, "concurrency", _with_key(repo, where, key, "ok"))

    code, output = verify(repo)

    assert code == 1
    assert key in output and "concurrency" in output


@pytest.mark.parametrize(("label", "mangle", "expected"), [
    ("重複鍵", lambda t: t.replace('"policy":', '"policy": "x", "policy":', 1), "重複"),
    ("巢狀重複鍵", lambda t: t.replace('"kind": "unit"', '"kind": "unit", "kind": "unit"', 1),
     "重複"),
    ("NaN", lambda t: t.replace('"manifest_version": 1', '"manifest_version": NaN', 1), "NaN"),
    ("布林", lambda t: t.replace('"manifest_version": 1', '"manifest_version": true', 1),
     "manifest_version"),
    ("缺鍵", lambda t: t.replace('"symbols":', '"symbolz":', 1), "symbols"),
])
def test_a_manifest_that_parses_but_breaks_the_reading_rules_is_blocked(
        repo, label, mangle, expected):
    text = json.dumps(manifest(repo, "concurrency"), ensure_ascii=False)
    assert mangle(text) != text, label
    set_raw(repo, "concurrency", mangle(text))

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


@pytest.mark.parametrize(("changes", "expected"), [
    ({"manifest_version": 2}, "manifest_version"),
    ({"policy": "  "}, "policy"),
    ({"symbols": "src/rtb/dsp/server.py:act"}, "symbols"),
    ({"evidence": []}, "evidence"),
    ({"evidence": [{"node": NODE_UPDATE, "covers": ["update", "pause"], "kind": "unit",
                    "injection": "DEATH"}]}, "injection"),  # 只有 failure_injection 能帶
    ({"evidence": [{"node": NODE_UPDATE, "covers": ["update", "pause"],
                    "kind": "failure_injection"}]}, "缺鍵:injection"),  # 故障注入一定要寫手段
    ({"evidence": [{"node": NODE_UPDATE, "covers": ["update", "pause"],
                    "kind": "failure_injection", "injection": " "}]}, "injection 要寫注入手段"),
])
def test_a_field_with_the_wrong_value_or_type_is_blocked(repo, changes, expected):
    write_manifests(repo, **changes)

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


@pytest.mark.parametrize(("side", "mangle", "expected"), [
    ("scope", lambda entry: dict(entry, sha256=entry["sha256"].upper()), "64 位"),
    ("scope", lambda entry: entry, "重複列了"),  # 同一支列兩次,解析時不准默默留一份
])
def test_a_file_entry_with_a_bad_hash_or_listed_twice_is_blocked(repo, side, mangle, expected):
    data = manifest(repo, "concurrency")
    data[side].append(mangle(dict(data[side][0])))
    if expected == "64 位":
        data[side].pop(0)
    set_raw(repo, "concurrency", json.dumps(data, ensure_ascii=False))

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


def test_a_block_before_the_run_says_the_evidence_was_not_run(repo):
    write_manifests(repo, policy="")

    code, output = verify(repo)

    assert code == 1 and "沒跑證據測試" in output


def test_the_same_evidence_node_twice_in_one_manifest_is_blocked(repo):
    evidence = manifest(repo, "concurrency")["evidence"]
    write_manifests(repo, evidence=[*evidence, dict(evidence[0], covers=[])])

    code, output = verify(repo)

    assert code == 1 and "重複" in output and NODE_UPDATE in output


# ── [S801] 存在與路徑規則、symbols 只認最外層 ─────────────────────────────────

def _add_scope(root, path):
    """在 concurrency 那份的 scope 多列一項;雜湊照實際的 kit.py 算,只留下路徑本身的問題。"""
    data = manifest(root, "concurrency")
    data["scope"].append({"path": path, "sha256": sha(root, "src/rtb/kit.py")})
    set_raw(root, "concurrency", json.dumps(data, ensure_ascii=False))


def test_a_file_listed_in_both_scope_and_harness_is_blocked(repo):
    write_manifests(repo, harness=[*HARNESS, "src/rtb/kit.py"])

    code, output = verify(repo)

    assert code == 1 and "同時" in output


SYMBOL_EXTRAS = """

if True:
    def hidden():
        return 1


def shadowed():
    return 1


shadowed = None


def reimported():
    return 1


from rtb.kit import ok as reimported  # noqa: E402


def outer():
    def inner():
        return 1
    return inner


def in_try():
    return 1


try:
    from rtb.kit import ok as in_try  # noqa: F811
except ImportError:
    pass


def in_if():
    return 1


if True:
    in_if = None


def deleted():
    return 1


del deleted


def via_global():
    return 1


def rebinder():
    global via_global
    via_global = None


def uses_local():
    act = 1  # 函式裡的區域變數跟 symbol 同名,不算重新綁定
    WRITE_ACTIONS = ()
    return act, WRITE_ACTIONS


# 代碼審第 2 輪:照 Python 語意本來就不算重綁的幾種寫法,不能誤擋
_names = [act for act in ()]  # 推導式有自己的作用域
declared_only: object  # 只有型別註記、沒有值


class Holder:
    @property
    def value(self):
        return 1

    @value.setter
    def value(self, new):
        pass
"""
OVERLOADED = """

from typing import overload


@overload
def shaped(x: int) -> int: ...
@overload
def shaped(x: str) -> str: ...
def shaped(x):
    return x


announced: object  # 先只宣告型別,後面才定義


def announced():
    return 1
"""
SERVER = "src/rtb/dsp/server.py"


OPAQUE = {
    "star": "\ntry:\n    from rtb.kit import *  # noqa: F403\nexcept ImportError:\n    pass\n",
    "match": "\nmatch 1:\n    case act:\n        pass\n",
    "attribute": "\nHandler.handle = None\n",
    "setattr": "\nsetattr(Handler, \"handle\", None)\n",
}


@pytest.mark.parametrize(("side", "value", "expected"), [
    ("scope", "src/rtb/gone.py", "src/rtb/gone.py"),
    ("scope", "{root}/src/rtb/kit.py", "絕對"),
    ("scope", "src/rtb/../rtb/kit.py", "路徑不准有 .."),
    ("scope", "src/rtb/link.py", "符號連結"),
    ("scope", "src/RTB/kit.py", "src/RTB/kit.py"),  # 中間目錄大小寫錯:macOS 上檔案「存在」
    ("scope", "src/rtb/Kit.py", "src/rtb/Kit.py"),
    ("scope", "src/rtb", "不是檔案"),
    ("symbols", f"{SERVER}:nope", "nope"),
    ("symbols", f"{SERVER}:hidden", "hidden"),  # 在 if 底下,不算最外層
    ("symbols", f"{SERVER}:shadowed", "shadowed"),  # 定義之後被重新指派
    ("symbols", f"{SERVER}:Handler.nope", "Handler.nope"),
    ("symbols", f"{SERVER}:store", "store"),  # 匯入進來的名字不是定義
    ("symbols", f"{SERVER}:reimported", "reimported"),  # 定義之後被匯入蓋掉
    ("symbols", f"{SERVER}:outer.inner", "outer.inner"),  # 函式裡的巢狀函式不是類別方法
    ("symbols", f"{SERVER}:in_try", "in_try"),  # try 區塊裡被匯入蓋掉(代碼審第 1 輪)
    ("symbols", f"{SERVER}:in_if", "in_if"),  # if 區塊裡被重新指派
    ("symbols", f"{SERVER}:deleted", "deleted"),  # 被 del
    ("symbols", f"{SERVER}:via_global", "via_global"),  # 函式裡用 global 改掉
    # 代碼審第 2 輪:看不懂的綁定方式一律擋,不去展開
    ("symbols", f"{SERVER}:act|star", "import *"),
    ("symbols", f"{SERVER}:act|match", "match"),
    ("symbols", f"{SERVER}:Handler.handle|attribute", "Handler"),
    ("symbols", f"{SERVER}:Handler.handle|setattr", "Handler"),
    ("symbols", SERVER, "寫法"),  # 沒寫名稱
])
def test_a_missing_file_or_symbol_is_blocked(repo, side, value, expected):
    (repo / "src/rtb/link.py").symlink_to(repo / "src/rtb/kit.py")
    value, _, opaque = value.partition("|")
    write_files(repo, {SERVER: FILES[SERVER] + SYMBOL_EXTRAS + OPAQUE.get(opaque, "")})
    if side == "scope":
        write_manifests(repo)
        _add_scope(repo, value.format(root=repo))
    else:
        write_manifests(repo, symbols=[f"{SERVER}:act", value])

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


def test_the_symbol_fixtures_do_not_break_a_clean_manifest(repo):
    write_files(repo, {SERVER: FILES[SERVER] + SYMBOL_EXTRAS + OVERLOADED})
    write_manifests(repo, symbols=[f"{SERVER}:act", f"{SERVER}:Holder.value",
                                   f"{SERVER}:shaped", f"{SERVER}:announced",
                                   f"{SERVER}:Handler.handle"])

    code, output = verify(repo)

    assert code == 0, output


@pytest.mark.parametrize(("changes", "expected"), [
    ({"enumerations": [{"source": "src/rtb/dsp/Server.py:WRITE_ACTIONS"}]},
     "src/rtb/dsp/Server.py"),
    ({"evidence": [{"node": "tests/dsp/Test_server.py::test_update", "covers": ["update"],
                    "kind": "unit"},
                   {"node": NODE_PAUSE, "covers": ["pause"], "kind": "unit"}]},
     "tests/dsp/Test_server.py"),
])
def test_an_evidence_node_or_enumeration_source_outside_the_path_rules_is_blocked(
        repo, changes, expected):
    write_manifests(repo, **changes)

    code, output = verify(repo)

    assert code == 1
    assert any(expected in line and "大小寫" in line for line in output.splitlines()), output


# ── [S802] 雜湊新舊 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["src/rtb/kit.py", "tests/dsp/samples.py"])
def test_a_stale_scope_hash_is_blocked(repo, path):
    (repo / path).write_text((repo / path).read_text(encoding="utf-8") + "# 改過\n",
                             encoding="utf-8")

    code, output = verify(repo)

    assert code == 1
    assert path in output and "雜湊" in output


# ── [S803] 列舉覆蓋 ──────────────────────────────────────────────────────────

REGISTRY = 'WRITE_ACTIONS = ("update", "pause")'


@pytest.mark.parametrize(("label", "changes", "registry", "expected"), [
    ("少標一項", {"evidence": [{"node": NODE_UPDATE, "covers": ["update"], "kind": "unit"}]},
     None, "pause"),
    ("covers 打錯字", {"evidence": [
        {"node": NODE_UPDATE, "covers": ["update"], "kind": "unit"},
        {"node": NODE_PAUSE, "covers": ["pause", "paus"], "kind": "unit"}]}, None, "paus"),
    ("範圍詞沒列舉", {"enumerations": [], "evidence": [
        {"node": NODE_UPDATE, "covers": [], "kind": "unit"}]}, None, "範圍詞"),
    ("同義詞也算範圍詞", {"policy": "各種寫入動作都只套用一次。", "enumerations": [], "evidence": [
        {"node": NODE_UPDATE, "covers": [], "kind": "unit"}]}, None, "各種"),
    ("動態組出來", {}, 'WRITE_ACTIONS = tuple(x for x in ("update", "pause"))', "WRITE_ACTIONS"),
    ("接著被加長", {}, REGISTRY + '\nWRITE_ACTIONS = WRITE_ACTIONS + ("void",)', "WRITE_ACTIONS"),
    ("從別處匯入", {}, "from rtb.kit import ok as WRITE_ACTIONS", "WRITE_ACTIONS"),
    ("元素不是字串", {}, 'WRITE_ACTIONS = ("update", 1)', "WRITE_ACTIONS"),
    # 代碼審第 1 輪:只看最外層直接敘述會讀成舊值
    ("可變的 list", {}, 'WRITE_ACTIONS = ["update", "pause"]', "WRITE_ACTIONS"),
    ("可變的 set", {}, 'WRITE_ACTIONS = {"update", "pause"}', "WRITE_ACTIONS"),
    ("if 裡重綁", {}, REGISTRY + '\nif True:\n    WRITE_ACTIONS = ("update", "pause", "void")',
     "WRITE_ACTIONS"),
    ("try 裡被匯入蓋掉", {}, REGISTRY + "\ntry:\n    from rtb.kit import EXTRA as WRITE_ACTIONS"
     "\nexcept ImportError:\n    pass", "WRITE_ACTIONS"),
    ("+= 加長", {}, REGISTRY + '\nWRITE_ACTIONS += ("void",)', "WRITE_ACTIONS"),
    ("函式裡 global 改掉", {}, REGISTRY + '\n\n\ndef widen():\n    global WRITE_ACTIONS\n'
     '    WRITE_ACTIONS = ("void",)', "WRITE_ACTIONS"),
    ("被 del", {}, REGISTRY + "\ndel WRITE_ACTIONS", "WRITE_ACTIONS"),
    # 代碼審第 2 輪
    ("推導式裡的 := 會綁到外層", {}, REGISTRY + '\n_ = [(WRITE_ACTIONS := ("void",)) for _ in "a"]',
     "WRITE_ACTIONS"),
])
def test_an_uncovered_enumerated_item_is_blocked(repo, label, changes, registry, expected):
    if registry is not None:
        write_files(repo, {SERVER: FILES[SERVER].replace(REGISTRY, registry)})
    write_manifests(repo, **changes)

    code, output = verify(repo)

    assert code == 1, (label, output)
    assert expected in output


@pytest.mark.parametrize(("tail", "expected"), [
    ("\ntry:\n    from rtb.kit import *  # noqa: F403\nexcept ImportError:\n    pass\n",
     "import *"),
    ('\nmatch {"actions": 1}:\n    case {"actions": WRITE_ACTIONS}:\n        pass\n', "match"),
])
def test_a_registry_module_the_verifier_cannot_follow_is_blocked(repo, tail, expected):
    """登錄表放在沒有 symbols 的模組,只靠登錄表那道檢查擋(代碼審第 2 輪:看不懂就擋)。"""
    write_files(repo, {"src/rtb/registry.py": REGISTRY + "\n" + tail})
    write_manifests(repo, enumerations=[{"source": "src/rtb/registry.py:WRITE_ACTIONS"}])

    code, output = verify(repo)

    assert code == 1, output
    assert any("registry.py:WRITE_ACTIONS" in line and expected in line
               for line in output.splitlines()), output


@pytest.mark.parametrize("assignment", [
    'WRITE_ACTIONS: tuple[str, ...] = ("update", "pause")',
    'WRITE_ACTIONS = frozenset({"update", "pause"})',
    'WRITE_ACTIONS = frozenset(("update", "pause"))',
    # 推導式變數有自己的作用域,不算重綁(代碼審第 2 輪:原本會誤擋)
    REGISTRY + "\n_seen = [WRITE_ACTIONS for WRITE_ACTIONS in ()]",
])
def test_every_documented_literal_form_of_a_registry_is_read(repo, assignment):
    write_files(repo, {SERVER: FILES[SERVER].replace(REGISTRY, assignment)})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 0, output


@pytest.mark.parametrize("word", ["每一個", "每一支", "每一筆", "每一項", "每條", "每一條", "每次"])
def test_every_scope_word_in_the_plan_is_recognised(repo, word):
    write_manifests(repo, policy=f"{word}寫入動作都只套用一次。", enumerations=[],
                    evidence=[{"node": NODE_UPDATE, "covers": [], "kind": "unit"}])

    code, output = verify(repo)

    assert code == 1 and word in output


# ── [S804] 真的跑:跳過、預期失敗、預期失敗卻通過、失敗、被取消選取、逾時 ───────

DESELECT_CONFTEST = (
    "def pytest_collection_modifyitems(config, items):\n"
    "    items[:] = [i for i in items if i.name != \"test_pause\"]\n"
)


@pytest.mark.parametrize(("label", "files", "expected"), [
    ("跳過", {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause", "@pytest.mark.skip\ndef test_pause")}, "跳過"),
    ("預期失敗", {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act",
        "@pytest.mark.xfail\ndef test_pause():\n    assert not act")}, "跳過"),
    ("預期失敗卻通過", {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause", "@pytest.mark.xfail\ndef test_pause")}, "沒通過"),
    ("失敗", {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")}, "沒通過"),
    ("取消選取", {"tests/dsp/conftest.py": DESELECT_CONFTEST}, "沒收集到"),
    # 代碼審第 1 輪:標記明寫 strict=False 會蓋過 xfail_strict,JUnit 裡長得跟通過一樣
    ("非嚴格預期失敗卻通過", {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause", "@pytest.mark.xfail(strict=False, reason=\"flaky\")\ndef test_pause")},
     "預期失敗卻通過"),
    ("模組層非嚴格預期失敗", {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "from tests.dsp import samples\n",
        "from tests.dsp import samples\n\npytestmark = pytest.mark.xfail(strict=False)\n")},
     "預期失敗卻通過"),
    ("執行期才加的非嚴格預期失敗", {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n", "def test_pause(request):\n"
        "    request.node.add_marker(pytest.mark.xfail(strict=False))\n")}, "預期失敗卻通過"),
])
def test_a_skipped_or_failing_or_missing_test_is_blocked(repo, label, files, expected):
    write_files(repo, files)
    write_manifests(repo)  # 雜湊照新內容算,只留下要測的那個原因

    code, output = verify(repo)

    assert code == 1, (label, output)
    assert NODE_PAUSE in output and expected in output


def test_a_run_that_leaves_no_xfail_record_is_blocked(repo):
    """JUnit 寫完之後才硬結束(結束代碼 0),驗證器的紀錄器沒機會寫;讀不到紀錄就擋,不當成沒有。"""
    write_files(repo, {
        "tests/dsp/test_server.py": TEST_SERVER.replace(
            "def test_pause", "@pytest.mark.xfail(strict=False)\ndef test_pause"),
        "tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
            "\n\ndef pytest_unconfigure(config):\n    import os\n    os._exit(0)\n")})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 1 and "預期失敗的紀錄" in output


def test_a_failure_shows_what_pytest_said_so_a_time_limit_can_be_told_apart(repo):
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act",
        "def test_pause():\n    assert 70 < 60, \"超過 60 秒上限\"\n    assert act")})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 1 and "超過 60 秒上限" in output


def test_a_run_that_exceeds_the_time_limit_is_blocked_and_leaves_no_process_behind(repo):
    marker = repo / "child.pid"
    slow = TEST_SERVER.replace(
        "def test_pause():\n    assert act",
        "def test_pause():\n    import subprocess, sys, time\n"
        "    child = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(60)\"])\n"
        f"    open({str(marker)!r}, \"w\").write(str(child.pid))\n"
        "    time.sleep(60)\n    assert act")
    write_files(repo, {"tests/dsp/test_server.py": slow})
    write_manifests(repo)

    code, output = verify(repo, timeout=5)

    assert code == 1 and "逾時" in output
    child = int(marker.read_text())
    with pytest.raises(ProcessLookupError):  # 測試另起的孫行程也跟著整組結束
        for _ in range(50):
            os.kill(child, 0)
            time.sleep(0.1)


# ── [S805] 自己跑,不讀外部結果檔 ─────────────────────────────────────────────

FAKE_JUNIT = ('<?xml version="1.0"?><testsuites><testsuite tests="2" failures="0">'
              '<testcase classname="tests.dsp.test_server" name="test_update"/>'
              '<testcase classname="tests.dsp.test_server" name="test_pause"/>'
              "</testsuite></testsuites>")


def test_the_verifier_runs_the_tests_itself(repo):
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")})
    write_manifests(repo)
    for name in ("junit.xml", "report.xml", "results.xml", "claims/junit.xml"):
        (repo / name).write_text(FAKE_JUNIT, encoding="utf-8")

    code, output = verify(repo)

    assert code == 1 and NODE_PAUSE in output


# ── [S807] 五條必要宣稱 ──────────────────────────────────────────────────────

def test_a_missing_required_claim_is_blocked(repo):
    (repo / "claims" / "concurrency.json").unlink()

    code, output = verify(repo)

    assert code == 1 and "concurrency" in output


def test_an_empty_claims_directory_is_blocked(repo):
    for path in (repo / "claims").iterdir():
        path.unlink()

    code, output = verify(repo)

    assert code == 1
    assert all(claim_id in output for claim_id in REQUIRED)


def test_the_required_claims_are_the_five_in_the_plan():
    assert tuple(sorted(load_verifier().REQUIRED_CLAIMS)) == REQUIRED


# ── [S810] 依賴閉包 ──────────────────────────────────────────────────────────

def _omit(side, path):
    listed = SCOPE if side == "scope" else HARNESS
    return lambda _repo: {side: [p for p in listed if p != path]}


def _symbol_file(repo):
    write_files(repo, {"src/rtb/extra.py": "def spare():\n    return 1\n"})
    return {"symbols": ["src/rtb/extra.py:spare"]}


def _enumeration_file(repo):
    write_files(repo, {"src/rtb/registry.py": 'REG = ("update", "pause")\n'})
    return {"enumerations": [{"source": "src/rtb/registry.py:REG"}]}


def _conftest_import(repo):
    write_files(repo, {
        "src/rtb/fixture_only.py": "VALUE = 1\n",
        "tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
            "\n\ndef pytest_configure(config):\n    from rtb import fixture_only  # noqa: F401\n"),
    })
    return {}


def _root_conftest(repo):
    write_files(repo, {"conftest.py": "import pytest\n"})
    return {}


def _plugins_in_conftest(repo):
    write_files(repo, {
        "tests/dsp/plugin_helpers.py": "VALUE = 1\n",
        "tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
            '\n\npytest_plugins = ("tests.dsp.plugin_helpers",)\n'),
    })
    return {}


def _plugin_in_addopts(repo):
    write_files(repo, {
        "tests/dsp/addopts_plugin.py": "VALUE = 1\n",
        "pyproject.toml": FILES["pyproject.toml"] + 'addopts = "-p tests.dsp.addopts_plugin"\n',
    })
    return {}


def _plugin_in_tools(repo):
    write_files(repo, {
        "tools/helpers.py": "VALUE = 1\n",
        "tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
            '\n\npytest_plugins = ("tools.helpers",)\n'),
    })
    return {}


def _plugin_at_root(repo):
    write_files(repo, {"myplug.py": "VALUE = 1\n",
                       "tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
                           '\n\npytest_plugins = ("myplug",)\n')})
    return {}


def _test_imports_tools(repo):
    write_files(repo, {"tools/fake.py": "def check():\n    return True\n",
                       "tests/dsp/test_server.py": TEST_SERVER.replace(
                           "from tests.dsp import samples\n",
                           "from tests.dsp import samples\nfrom tools.fake import check\n")})
    return {}


def _declared_outside(repo):
    write_files(repo, {"tools/launcher.py": "VALUE = 1\n"})
    return {"harness": [*HARNESS, "tools/launcher.py"]}  # 手動列在 src、tests 以外


def _declared_dynamic_entry(repo):
    write_files(repo, {"tests/dsp/launcher.py": "from tests.dsp import hidden\n",
                       "tests/dsp/hidden.py": "VALUE = 1\n"})
    return {"harness": [*HARNESS, "tests/dsp/launcher.py"]}  # 作者手動列了動態入口


@pytest.mark.parametrize(("prepare", "expected"), [
    (_omit("scope", "src/rtb/dsp/store.py"), "src/rtb/dsp/store.py"),  # 套件屬性匯入
    (_omit("scope", "src/rtb/kit.py"), "src/rtb/kit.py"),  # 遞迴第二層
    (_omit("scope", "src/rtb/__init__.py"), "src/rtb/__init__.py"),  # 上層套件初始化檔
    (_omit("harness", "tests/dsp/conftest.py"), "tests/dsp/conftest.py"),
    (_omit("harness", "tests/dsp/samples.py"), "tests/dsp/samples.py"),
    (_omit("harness", "tests/__init__.py"), "tests/__init__.py"),
    (_omit("harness", "pyproject.toml"), "pyproject.toml"),
    (_omit("harness", "tests/dsp/test_server.py"), "tests/dsp/test_server.py"),  # 證據測試檔本身
    (_symbol_file, "src/rtb/extra.py"),
    (_enumeration_file, "src/rtb/registry.py"),
    (_conftest_import, "src/rtb/fixture_only.py"),
    # 代碼審第 1 輪:根 conftest、pytest_plugins 字串、pyproject addopts 的 -p 都會被 pytest 載入
    (_root_conftest, "harness 少列 conftest.py"),
    (_plugins_in_conftest, "tests/dsp/plugin_helpers.py"),
    (_plugin_in_addopts, "addopts"),  # 代碼審第 3 輪:ini_options 只准 testpaths 與 pythonpath
    # 代碼審第 2 輪:解析到 src、tests 以外的 repo 內檔一律擋;harness 宣告的 .py 也當起點
    (_plugin_in_tools, "tools/helpers.py"),
    (_plugin_at_root, "myplug.py"),
    (_test_imports_tools, "tools/fake.py"),
    (_declared_dynamic_entry, "tests/dsp/hidden.py"),
    (_declared_outside, "tools/launcher.py 在 src、tests 以外"),
])
def test_a_file_missing_from_the_dependency_closure_is_blocked(repo, prepare, expected):
    changes = prepare(repo)
    write_manifests(repo, **changes)

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


def test_imports_inside_functions_type_checking_and_relative_imports_are_followed(repo):
    write_files(repo, {
        "src/rtb/dsp/store.py": (
            "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from rtb import typed\n\n\n"
            "def apply(action):\n    from . import local\n    from rtb import kit\n"
            "    return kit.ok() and local.YES\n"),
        "src/rtb/typed.py": "X = 1\n",
        "src/rtb/dsp/local.py": "YES = True\n",
    })
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 1
    assert "src/rtb/typed.py" in output and "src/rtb/dsp/local.py" in output


# ── [S811] 結束代碼 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "{",  # 語法錯
    '{"manifest_version": ' + "9" * 5000 + "}",  # 超長整數,標準 json 丟的是 ValueError
    b"\xff\xfe not utf-8",  # 編碼錯
])
def test_an_unreadable_manifest_is_undecidable_not_blocked(repo, raw):
    path = repo / "claims" / "concurrency.json"
    if isinstance(raw, bytes):
        path.write_bytes(raw)
    else:
        path.write_text(raw, encoding="utf-8")

    code, output = verify(repo)

    assert code == 2 and "concurrency" in output


def test_the_exit_code_tells_pass_block_and_undecidable_apart(repo):
    module = load_verifier()
    assert verify(repo)[0] == 0
    (repo / "src/rtb/kit.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    assert verify(repo)[0] == 1
    assert module.run([str(repo / "no-such-dir")], out=io.StringIO()) == 2
    assert module.run(["--no-such-flag"], out=io.StringIO()) == 2
    assert module.run([], out=io.StringIO()) == 2


# ── [S812] 節點編號 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("node", [
    "tests/dsp/test_server.py::test_update[1]",  # 帶參數:只跑到部分參數
    "tests/dsp/test_server.py::test_nope",  # pytest 找不到,整批一支都不跑
    "tests/dsp/test_server.py",  # 沒寫函式
])
def test_a_parametrized_or_unknown_node_id_is_named_in_the_block(repo, node):
    evidence = manifest(repo, "concurrency")["evidence"]
    write_manifests(repo, evidence=[*evidence, {"node": node, "covers": [], "kind": "unit"}])

    code, output = verify(repo)

    assert code == 1, output
    assert any(node in line and ("找不到" in line or "證據節點" in line)
               for line in output.splitlines())


def test_a_nonzero_pytest_exit_without_a_named_failure_is_still_blocked(repo):
    write_files(repo, {"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        "\n\ndef pytest_sessionfinish(session):\n    session.exitstatus = 3\n")})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 1 and "結束代碼 3" in output


# ── [S814] claim_id 跟檔名 ───────────────────────────────────────────────────

def test_a_claim_id_that_differs_from_the_file_name_is_blocked(repo):
    set_raw(repo, "concurrency", json.dumps(manifest(repo, "Concurrency"), ensure_ascii=False))

    code, output = verify(repo)

    assert code == 1 and "Concurrency" in output


# ── [S815] 不受外部 pytest 設定與環境影響 ────────────────────────────────────

EVIL_PLUGIN = (
    "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    report = (yield).get_result()\n"
    "    if report.when == \"call\":\n        report.outcome = \"passed\"\n"
)


def _python_with_user_site(tmp_path_factory):
    """造一個 user site 開著的直譯器(代碼審第 1 輪:CI 的 setup-python 沒有 venv,user site 開著;
    本機的 venv 關著,直接跑測不到)。venv 帶系統套件時 user site 會開;再放一個 .pth 指回目前
    的套件目錄,讓它匯入得到 pytest。建立只要幾十毫秒。

    這是本專案測試第一次造 venv,非造不可:venv 的 pyvenv.cfg 寫 include-system-site-packages =
    false 時,CPython 的 site.venv() 會把 ENABLE_USER_SITE 直接設成 False,任何環境變數都改不動。"""
    venv = tmp_path_factory.mktemp("usersite-python")
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip",
                    str(venv)], check=True, timeout=120)
    python = venv / "bin" / "python"
    purelib = subprocess.run([str(python), "-c", "import sysconfig;print(sysconfig.get_path("
                              "'purelib'))"], capture_output=True, text=True, check=True,
                             timeout=60).stdout.strip()
    here = next(p for p in sys.path if p.endswith("site-packages") and Path(p, "pytest").exists())
    Path(purelib, "rtb_test_packages.pth").write_text(here + "\n", encoding="utf-8")
    enabled = subprocess.run([str(python), "-c", "import site;print(site.ENABLE_USER_SITE)"],
                             capture_output=True, text=True, check=True, timeout=60).stdout
    assert enabled.strip() == "True"  # 前提:這個直譯器的 user site 真的開著
    return python


def _user_site(python, base):
    return Path(subprocess.run([str(python), "-c", "import site;print(site.getusersitepackages())"],
                               env={**os.environ, "PYTHONUSERBASE": str(base)},
                               capture_output=True, text=True, check=True,
                               timeout=60).stdout.strip())


@pytest.mark.parametrize("route", ["PYTEST_ADDOPTS", "PYTHONPATH", "PYTHONUSERBASE"])
def test_the_pytest_run_ignores_outside_config_and_environment(repo, tmp_path_factory,
                                                                monkeypatch, route):
    """兩條路各自能把改結果的外掛帶進來:PYTEST_ADDOPTS 帶 -p(外掛放在 repo 根,python -m 會把
    工作目錄放進搜尋路徑);PYTHONPATH 指到外面一個 sitecustomize,開機時自己設 PYTEST_ADDOPTS。"""
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")})
    write_manifests(repo)
    if route == "PYTEST_ADDOPTS":
        (repo / "evil.py").write_text(EVIL_PLUGIN, encoding="utf-8")
        monkeypatch.setenv("PYTEST_ADDOPTS", "-p evil")
    elif route == "PYTHONPATH":
        outside = tmp_path_factory.mktemp("outside")
        (outside / "evil.py").write_text(EVIL_PLUGIN, encoding="utf-8")
        (outside / "sitecustomize.py").write_text(
            "import os\nos.environ[\"PYTEST_ADDOPTS\"] = \"-p evil\"\n", encoding="utf-8")
        monkeypatch.setenv("PYTHONPATH", str(outside))
    else:  # user site 裡的 usercustomize 在清完環境之後才自己設 PYTEST_ADDOPTS
        python = _python_with_user_site(tmp_path_factory)
        base = tmp_path_factory.mktemp("userbase")
        site_dir = _user_site(python, base)
        site_dir.mkdir(parents=True)
        (site_dir / "evil.py").write_text(EVIL_PLUGIN, encoding="utf-8")
        (site_dir / "usercustomize.py").write_text(
            "import os\nos.environ[\"PYTEST_ADDOPTS\"] = \"-p evil\"\n", encoding="utf-8")
        monkeypatch.setenv("PYTHONUSERBASE", str(base))
        monkeypatch.setattr(sys, "executable", str(python))

    code, output = verify(repo)

    assert code == 1 and NODE_PAUSE in output  # 外掛若被載入,失敗會被改成通過


def _failing_pause(repo):
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")})
    write_manifests(repo)
    # python -m 會把工作目錄放進搜尋路徑,repo 根的 evil.py 載得到
    (repo / "evil.py").write_text(EVIL_PLUGIN, encoding="utf-8")


def test_an_installed_plugin_is_not_loaded_automatically(repo):
    _failing_pause(repo)
    dist = repo / "evil_plugin-0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: evil-plugin\nVersion: 0\n",
                                   encoding="utf-8")
    (dist / "entry_points.txt").write_text("[pytest11]\nevil = evil\n", encoding="utf-8")

    code, output = verify(repo)

    assert code == 1 and NODE_PAUSE in output


def test_a_pytest_config_below_the_repo_root_is_ignored(repo):
    _failing_pause(repo)
    # 比 pyproject 更靠近測試檔:沒有 -c 釘住的話 pytest 會改用它(設定照抄,只多了 -p evil)
    (repo / "tests" / "pytest.ini").write_text(
        "[pytest]\npythonpath = ../src\naddopts = -p evil\n", encoding="utf-8")

    code, output = verify(repo)

    assert code == 1 and NODE_PAUSE in output


@pytest.mark.parametrize("name", ["pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"])
def test_a_second_pytest_config_file_is_blocked(repo, name):
    (repo / name).write_text("[pytest]\naddopts =\n", encoding="utf-8")

    code, output = verify(repo)

    assert code == 1 and name in output


# ── [S816] 輸出帶版本指紋 ────────────────────────────────────────────────────

def test_the_output_names_the_verifier_and_manifest_versions(repo):
    code, output = verify(repo)

    assert code == 0
    assert hashlib.sha256(VERIFIER.read_bytes()).hexdigest() in output
    for claim_id in REQUIRED:
        assert sha(repo, f"claims/{claim_id}.json") in output
    assert "提交編號" in output


# ── [S808] 互不匯入 ──────────────────────────────────────────────────────────

def _imported_modules(path, *, strings=False):
    """靜態匯入的模組名;strings=True 時連字串裡出現的模組名也算(動態匯入要靠字串帶名字)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif strings and isinstance(node, ast.Constant) and isinstance(node.value, str):
            names |= {word for word in node.value.replace(",", " ").split()
                      if word == "rtb" or word.startswith("rtb.")}
    return names


def _touches(names, package):
    return sorted(n for n in names if n == package or n.startswith(f"{package}."))


def test_the_verifier_and_the_product_do_not_import_each_other():
    # 驗證器連字串都不准帶產品模組名;這支測試檔的字串是小 repo 的樣本碼,只看靜態匯入
    for tool in (VERIFIER, HASH_HELPER):
        assert _touches(_imported_modules(tool, strings=True), "rtb") == [], tool
        assert "lumos" not in tool.read_text(encoding="utf-8")
    assert _touches(_imported_modules(Path(__file__)), "rtb") == []
    for path in sorted((ROOT / "src" / "rtb").rglob("*.py")):
        assert _touches(_imported_modules(path), "tools") == [], path


def test_the_import_scanner_sees_static_and_string_imports(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("import importlib\n\n\ndef f():\n    from rtb.dsp import store\n"
                      "    importlib.import_module(\"rtb.x\")\n", encoding="utf-8")
    assert _touches(_imported_modules(sample), "rtb") == ["rtb.dsp"]
    assert _touches(_imported_modules(sample, strings=True), "rtb") == ["rtb.dsp", "rtb.x"]


def test_the_tools_directory_bans_importing_the_product_through_ruff():
    config = (ROOT / "tools" / "ruff.toml").read_text(encoding="utf-8")
    assert 'extend = "../pyproject.toml"' in config
    assert '"rtb".msg' in config


# ── [S809] 正式五份清單 ──────────────────────────────────────────────────────

def test_the_committed_claims_pass():
    module = load_verifier()

    manifests, problems = module.check_manifests(ROOT, ROOT / "claims")

    assert problems == []
    assert sorted(m.claim_id for m in manifests) == list(REQUIRED)


# ── [S813] CI 接線 ───────────────────────────────────────────────────────────

def jobs_of(text):
    """把 CI 檔切成「工作名 → 那個工作的文字」;只認 jobs: 底下縮排兩格的鍵。"""
    jobs, current, inside = {}, None, False
    for line in text.splitlines():
        if line.startswith("jobs:"):
            inside = True
            continue
        if inside and line and not line.startswith(" "):
            inside = False
        if not inside:
            continue
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":"):
            current = line.strip()[:-1]
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


def claims_job_problems(text):
    problems = list(ci_problems(text))
    jobs = jobs_of(text)
    owners = [name for name, body in jobs.items() if COMMAND in parse_run_commands(body)]
    if len(owners) != 1:
        problems.append(f"驗證器指令要剛好在一個工作裡,現在在:{owners}")
    for name in owners:
        if name == "checks":
            problems.append("驗證器不准跑在 checks 工作裡")
        if any(line.strip().startswith("needs:") for line in jobs[name].splitlines()):
            problems.append(f"{name} 工作不准 needs 別的工作")
    if any("verify_claims" in c for c in parse_run_commands(jobs.get("checks", ""))):
        problems.append("checks 工作裡不准出現驗證器")
    # 代碼審第 1 輪:shell: bash {0} 不會在第一個指令失敗時停下;defaults: 會改掉整份的 shell
    if any(line.split("#", 1)[0].strip().startswith("defaults:") for line in text.splitlines()):
        problems.append("CI 檔不准有 defaults:")
    for name in owners:
        lines = [line.split("#", 1)[0].strip().removeprefix("- ") for line in
                 jobs[name].splitlines()]
        if any(line.startswith("shell:") for line in lines):
            problems.append(f"{name} 工作不准指定 shell:")
        if f"run: {COMMAND}" not in lines:
            problems.append(f"{name} 工作裡驗證器指令要是單行 run:")
    return problems


def test_the_ci_runs_the_claims_verifier_and_it_can_fail():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert claims_job_problems(text) == []


CI_OK = """\
on:
  push:
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: python -m pytest -q
  claims:
    runs-on: ubuntu-latest
    steps:
      - run: python tools/verify_claims.py claims/
"""


@pytest.mark.parametrize("mangle", [
    lambda t: t.replace("python tools/verify_claims.py claims/",
                        "python tools/verify_claims.py claims/ || true"),
    lambda t: t.replace("  claims:\n    runs-on", "  claims:\n    needs: checks\n    runs-on"),
    lambda t: t.replace("  claims:\n    runs-on", "  claims:\n    if: false\n    runs-on"),
    lambda t: t.replace("  claims:\n    runs-on",
                        "  claims:\n    continue-on-error: true\n    runs-on"),
    lambda t: t.replace("      - run: python -m pytest -q",
                        "      - run: python -m pytest -q\n"
                        "      - run: python tools/verify_claims.py claims/"),
    lambda t: t.replace("python tools/verify_claims.py claims/", "echo skip"),
    # 代碼審第 1 輪:shell: bash {0} 不會在第一個指令失敗時停下
    lambda t: t.replace("      - run: python tools/verify_claims.py claims/",
                        "      - shell: bash {0}\n        run: |\n"
                        "          python tools/verify_claims.py claims/\n          echo done"),
    lambda t: t.replace("jobs:", "defaults:\n  run:\n    shell: bash {0}\njobs:"),
    lambda t: t.replace("      - run: python tools/verify_claims.py claims/",
                        "      - run: |\n          python tools/verify_claims.py claims/"),
    lambda t: t.replace("  claims:", "  checks2:").replace(
        "      - run: python -m pytest -q\n",
        "      - run: python -m pytest -q\n      - run: python tools/verify_claims.py claims/\n"),
])
def test_every_way_of_making_the_claims_job_toothless_is_flagged(mangle):
    assert claims_job_problems(CI_OK) == []
    assert claims_job_problems(mangle(CI_OK)) != []


# ── 代碼審第 1 輪補的擋法 ─────────────────────────────────────────────────────

def test_the_evidence_runs_with_the_environment_and_user_site_ignored(repo):
    """直接看子行程的旗標:-E(不讀 PYTHON 開頭的變數)與 -s(不開 user site)各自都要在。"""
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n", "def test_pause():\n    import sys\n"
        "    assert sys.flags.ignore_environment and sys.flags.no_user_site\n")})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 0, output


def test_an_evidence_test_outside_the_tests_directory_is_blocked(repo):
    write_files(repo, {"scripts/test_side.py": "def test_side():\n    assert True\n"})
    evidence = manifest(repo, "concurrency")["evidence"]
    write_manifests(repo, evidence=[*evidence, {"node": "scripts/test_side.py::test_side",
                                                "covers": [], "kind": "unit"}],
                    harness=[*HARNESS, "scripts/test_side.py"])

    code, output = verify(repo)

    assert code == 1
    assert any("scripts/test_side.py" in line and "tests/" in line
               for line in output.splitlines()), output


def test_an_evidence_test_in_a_directory_without_init_is_blocked(repo):
    """沒有 __init__.py 的測試目錄,pytest 會把同層的 helper 當頂層模組匯入,閉包解析不到;
    與其模擬 pytest 的各種匯入模式,不如要求每層都是套件(本 repo 現在每層都有)。"""
    (repo / "tests/dsp/__init__.py").unlink()
    write_manifests(repo, harness=[p for p in HARNESS if p != "tests/dsp/__init__.py"])

    code, output = verify(repo)

    assert code == 1
    assert any("tests/dsp" in line and "__init__.py" in line for line in output.splitlines())


PLUGINS_RULE = "只准最外層一次 tuple 字面"


@pytest.mark.parametrize(("files", "expected"), [
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = [name for name in ("tests.dsp.samples",)]\n')}, "pytest_plugins"),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = ("somewhere_else",)\n')}, "somewhere_else 不在 repo 裡"),
    # 代碼審第 3 輪:pytest_plugins 只准一次 tuple 字面,list 或之後再改都擋
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = ["tests.dsp.samples"]\n')}, PLUGINS_RULE),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = "tests.dsp.samples"\n')}, PLUGINS_RULE),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = ()\npytest_plugins += ("tests.dsp.samples",)\n')},
     PLUGINS_RULE),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = []\npytest_plugins.append("tests.dsp.samples")\n')},
     PLUGINS_RULE),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = ("tests.dsp.samples",)\nextra = list(pytest_plugins)\n')},
     PLUGINS_RULE),  # 一次 tuple 之後又在別處用到這個名字
])
def test_a_plugin_the_verifier_cannot_hash_is_blocked(repo, files, expected):
    """讀不出字面值、或指到 repo 外(已安裝套件)的外掛,驗證器算不到它的雜湊,一律擋。"""
    write_files(repo, files)
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


def test_a_builtin_plugin_in_pytest_plugins_is_allowed(repo):
    write_files(repo, {"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\npytest_plugins = ("pytester",)\n')})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 0, output


def test_the_builtin_plugin_list_matches_the_installed_pytest():
    from _pytest.config import builtin_plugins

    assert frozenset(builtin_plugins) == load_verifier().PYTEST_BUILTIN_PLUGINS


@pytest.mark.parametrize(("pyproject", "expected"), [
    ('[tool.pytest]\ntestpaths = ["tests"]\npythonpath = ["src"]\naddopts = ["-p", "tests.plug"]\n',
     "[tool.pytest]"),  # pytest 9 的原生表
    ('[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src", "tests/helpers"]\n',
     "pythonpath"),
    ('[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\naddopts = 3\n',
     "addopts"),
    # 代碼審第 3 輪:白名單,只准 testpaths 與 pythonpath;addopts 的 -o 能改掉 pythonpath
    ('[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
     'addopts = \'-o "pythonpath=src tests/helpers"\'\n', "addopts"),
    ('[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
     'addopts = "-p no:randomly"\n', "addopts"),
    ('[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
     'python_files = ["check_*.py"]\n', "python_files"),
])
def test_a_pytest_setting_the_verifier_cannot_follow_is_blocked(repo, pyproject, expected):
    """代碼審第 2 輪:看不懂就擋,不去模擬 pytest 的每一種設定。"""
    write_files(repo, {"pyproject.toml": pyproject})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


@pytest.mark.parametrize(("label", "prepare", "expected"), [
    ("kind 是陣列", lambda r: write_manifests(r, evidence=[
        {"node": NODE_UPDATE, "covers": [], "kind": ["unit"]}]), "kind"),
    ("範圍內程式語法錯", lambda r: (write_files(r, {"src/rtb/kit.py": "def ok(:\n"}),
                             write_manifests(r)), "src/rtb/kit.py"),
])
def test_a_strange_value_or_broken_code_is_a_reason_not_a_crash(repo, label, prepare, expected):
    prepare(repo)

    code, output = verify(repo)

    assert code == 1, (label, output)
    assert expected in output and "Traceback" not in output


# ── 代碼審第 3 輪:改成白名單,驗證器看到的程式要是實際跑的程式 ────────────────

@pytest.mark.parametrize(("files", "expected"), [
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\nimport sys\nsys.path.insert(0, "tests/dsp/helpers")\n')}, "sys.path"),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\nimport sys\nsys.path += ["tests/dsp/helpers"]\n')}, "sys.path"),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\ndef pytest_configure(config):\n    import sys\n    sys.path.append("x")\n')},
     "sys.path"),
    ({"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\nimport site\nsite.addsitedir("tests/dsp/helpers")\n')}, "addsitedir"),
    ({"tests/dsp/samples.py": "try:\n    import not_a_real_package_xyz  # noqa: F401\n"
      "except ImportError:\n    pass\nEXPECTED = True\n"}, "not_a_real_package_xyz"),
])
def test_code_that_changes_where_imports_come_from_is_blocked(repo, files, expected):
    """動 sys.path 或 site.addsitedir 會讓頂層名稱解析到驗證器沒算進閉包的檔;解析不到、又不是
    標準函式庫或已安裝套件的頂層名稱,一律擋(預設擋)。"""
    write_files(repo, files)
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


def test_standard_library_and_installed_packages_resolve_without_being_listed(repo):
    write_files(repo, {"tests/dsp/samples.py": (
        "import json  # noqa: F401\nimport pytest  # noqa: F401\n"
        "from _pytest import nodes  # noqa: F401\nEXPECTED = True\n")})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 0, output


def _extends_registry(repo):
    write_files(repo, {"src/rtb/dsp/extra.py": (
        "from rtb.dsp import server\n\n"
        "server.WRITE_ACTIONS = (*server.WRITE_ACTIONS, \"void\")\n")})
    return {"scope": [*SCOPE, "src/rtb/dsp/extra.py"]}


def _replaces_method_from_conftest(repo):
    write_files(repo, {"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        "\n\nfrom rtb.dsp import server\n\nserver.Handler.handle = lambda self: None\n")})
    return {}


def _setattr_from_conftest(repo):
    write_files(repo, {"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        '\n\nfrom rtb.dsp import server\n\nsetattr(server, "WRITE_ACTIONS", ("void",))\n')})
    return {}


@pytest.mark.parametrize(("prepare", "expected"), [
    (_extends_registry, "WRITE_ACTIONS"),
    (_replaces_method_from_conftest, "Handler"),
    (_setattr_from_conftest, "WRITE_ACTIONS"),
])
def test_another_module_that_rewrites_a_registry_or_symbol_is_blocked(repo, prepare, expected):
    """別的模組用「模組.登錄表 = …」或 setattr 擴充登錄表、換掉 symbol,整個閉包都查。"""
    changes = prepare(repo)
    write_manifests(repo, **changes)

    code, output = verify(repo)

    assert code == 1, output
    assert any(expected in line and "屬性" in line for line in output.splitlines()), output


NESTED = """

class Outer:
    class Inner:
        def meth(self):
            return 1
"""


@pytest.mark.parametrize(("tail", "symbol"), [
    ("\n_ = [setattr(Handler, \"handle\", None) for _ in (1,)]\n", "Handler.handle"),
    ("\nOuter.Inner.meth = None\n", "Outer.Inner.meth"),
    ("\nsetattr(Outer.Inner, \"meth\", None)\n", "Outer.Inner.meth"),
    ("\n\nclass Outer2:\n    class Inner:\n        def meth(self):\n            return 1\n\n"
     "    Inner.meth = None\n", "Outer2.Inner.meth"),  # 在外層類別本體裡改巢狀類別
    # 代碼審:for、with 的目標、推導式的迭代目標是屬性鏈,也是屬性指派
    ("\nfor Handler.handle in (lambda self: 2,):\n    pass\n", "Handler.handle"),
    ("\nimport contextlib\n\nwith contextlib.nullcontext(None) as Handler.handle:\n    pass\n",
     "Handler.handle"),
    ("\n_ = [1 for Handler.handle in (None,)]\n", "Handler.handle"),
])
def test_an_attribute_rewrite_hidden_in_a_comprehension_or_nested_class_is_blocked(
        repo, tail, symbol):
    write_files(repo, {SERVER: FILES[SERVER] + NESTED + tail})
    write_manifests(repo, symbols=[f"{SERVER}:act", f"{SERVER}:{symbol}"])

    code, output = verify(repo)

    assert code == 1, output
    assert symbol in output


# ── [S806] 故障注入:測試(含它用到的 fixture)真的引用了宣告的手段,而且有斷言 ─────────

FAULT_FILE = "tests/dsp/test_faults.py"


def _with_fault(repo, name, injection):
    evidence = manifest(repo, "concurrency")["evidence"]
    fault = {"node": f"{FAULT_FILE}::{name}", "covers": [], "kind": "failure_injection",
             "injection": injection}
    write_manifests(repo, evidence=[*evidence, fault], harness=[*HARNESS, FAULT_FILE])


@pytest.mark.parametrize(("name", "injection"), [
    ("test_fault_via_fixture", "timeout_before_commit"),  # 手段寫在它用的 fixture 裡
    ("test_fault_via_nested_fixture", "timeout_before_commit"),  # fixture 再用 fixture
    ("test_fault_via_conftest", "after_dsp_commit"),  # fixture 在 conftest
    ("test_fault_via_fixture", "monkeypatch"),  # 手段是 pytest 內建 fixture 的名字
    ("test_fault_inline", "DEATH"),
    ("test_fault_raises", "DEATH"),  # pytest.raises 也算斷言
    ("TestFaults::test_in_class", "monkeypatch"),
    ("test_fault_via_attribute", "CRASH"),  # 手段寫成屬性
    ("test_fault_via_assert_helper", "DEATH"),  # assert 開頭的輔助函式也算斷言
    ("test_mock_style_assert", "DEATH"),  # x.assert_* 屬性呼叫也算斷言
])
def test_a_failure_injection_that_uses_its_injection_and_asserts_passes(repo, name, injection):
    _with_fault(repo, name, injection)

    code, output = verify(repo)

    assert code == 0, output


@pytest.mark.parametrize(("name", "injection", "expected"), [
    ("test_no_fault", "DEATH", "注入手段"),
    ("test_fault_via_fixture", "DEATH", "注入手段"),  # 寫了別的手段
    ("test_fault_without_assert", "DEATH", "斷言"),
    ("test_gone", "DEATH", "找不到"),
    ("test_param_named_like_a_helper", "DEATH", "注入手段"),  # 同名的普通函式不是 fixture
    ("test_requests_but_never_uses", "monkeypatch", "注入手段"),  # 只要了 fixture、沒用它
    ("test_redefined", "DEATH", "找不到"),  # 同名定義兩次:不能拿前一支(沒被跑的)來判
    # 代碼審:不會執行的文字不算引用,字串要整串相等
    ("test_docstring_only", "DEATH", "注入手段"),
    ("test_decorator_only", "DEATH", "注入手段"),
    ("test_default_only", "DEATH", "注入手段"),
    ("test_assert_message_only", "DEATH", "注入手段"),
    ("test_substring_only", "DEATH", "注入手段"),
    # 代碼審:不會執行的斷言不算
    ("test_uncalled_nested_assert", "DEATH", "斷言"),
    ("test_dead_branch_assert", "DEATH", "常數條件"),  # 第 2 輪起常數條件整支擋
    ("test_assertion_named_helper", "DEATH", "斷言"),
])
def test_a_failure_injection_without_the_injection_or_assertion_is_blocked(
        repo, name, injection, expected):
    _with_fault(repo, name, injection)

    code, output = verify(repo)

    assert code == 1, output
    assert any(name in line and expected in line for line in output.splitlines()), output


@pytest.mark.parametrize(("tail", "expected"), [
    ("\ntry:\n    from rtb.kit import *  # noqa: F403\nexcept ImportError:\n    pass\n",
     "import *"),
    ("\nmatch 1:\n    case test_fault_inline:\n        pass\n", "match"),
])
def test_a_failure_injection_in_a_module_the_verifier_cannot_follow_is_blocked(
        repo, tail, expected):
    """找測試函式也照「看不懂就擋」(代碼審第 2 輪):import *、match 可能把測試名字換掉。"""
    write_files(repo, {FAULT_FILE: TEST_FAULTS + tail})
    _with_fault(repo, "test_fault_inline", "DEATH")

    code, output = verify(repo)

    assert code == 1, output
    assert any("test_fault_inline" in line and expected in line
               for line in output.splitlines()), output


FIXTURE_TRICKS = {
    "別名": ("import pytest\n\nfrom rtb.dsp.server import act\n\n\n"
             "@pytest.fixture(name=\"broken\")\ndef _renamed():\n    return \"DEATH\"\n\n\n"
             "def test_trick(broken):\n    assert broken and act(\"update\")\n", "name="),
    "類別層同名": ("import pytest\n\nfrom rtb.dsp.server import act\n\n\n"
                 "@pytest.fixture\ndef mode():\n    return \"DEATH\"\n\n\n"
                 "class TestTrick:\n    @pytest.fixture\n"
                 "    def mode(self):\n        return \"safe\"\n\n"
                 "    def test_trick(self, mode):\n        assert mode and act(\"update\")\n",
                 "類別"),
    "同檔同名兩次": ("import pytest\n\nfrom rtb.dsp.server import act\n\n\n"
                   "@pytest.fixture\ndef mode():\n    return \"DEATH\"\n\n\n"
                   "@pytest.fixture\ndef mode():  # noqa: F811\n    return \"safe\"\n\n\n"
                   "def test_trick(mode):\n    assert mode and act(\"update\")\n", "mode"),
}


@pytest.mark.parametrize("trick", sorted(FIXTURE_TRICKS))
def test_a_fixture_the_verifier_cannot_resolve_like_pytest_is_blocked(repo, trick):
    """fixture 解析照「看不懂就擋」:帶 name= 的別名、測試所在類別有同名 fixture、同檔同名定義兩次,
    都可能讓 pytest 實際用的不是驗證器看的那一個(代碼審)。"""
    text, expected = FIXTURE_TRICKS[trick]
    write_files(repo, {"tests/dsp/test_trick.py": text})
    node = ("tests/dsp/test_trick.py::TestTrick::test_trick" if "class" in text
            else "tests/dsp/test_trick.py::test_trick")
    evidence = manifest(repo, "concurrency")["evidence"]
    write_manifests(repo, evidence=[*evidence, {"node": node, "covers": [],
                                                "kind": "failure_injection", "injection": "DEATH"}],
                    harness=[*HARNESS, "tests/dsp/test_trick.py"])

    code, output = verify(repo)

    assert code == 1, output
    assert any("test_trick" in line and expected in line for line in output.splitlines()), output


def test_a_conftest_the_verifier_cannot_follow_blocks_failure_injection_evidence(repo):
    write_files(repo, {"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        "\n\ntry:\n    from rtb.kit import *  # noqa: F403\nexcept ImportError:\n    pass\n")})
    _with_fault(repo, "test_fault_via_conftest", "after_dsp_commit")

    code, output = verify(repo)

    assert code == 1
    assert any("conftest.py" in line and "import *" in line for line in output.splitlines()), output


# ── 造假示範:計劃列的幾種「看起來完成、其實沒有」全部擋下 ─────────────────────────

EVIL_CONFTEST_HOOK = (
    "\n\n@pytest.hookimpl(hookwrapper=True)\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    report = (yield).get_result()\n"
    "    if report.when == \"call\":\n        report.outcome = \"passed\"\n"
)


def _only_says_done(repo):
    set_raw(repo, "concurrency", json.dumps({"claim_id": "concurrency", "result": "已完成"},
                                            ensure_ascii=False))


def _code_changed_hash_not(repo):
    (repo / "src/rtb/kit.py").write_text("def ok():\n    return 1\n", encoding="utf-8")


def _claim_wider_than_evidence(repo):
    # 就像 Mock-DSP 那條:登錄表多了作廢,宣稱說「每一種」,卻沒有作廢的證據
    text = FILES["src/rtb/dsp/server.py"].replace('("update", "pause")',
                                                  '("update", "pause", "void")')
    write_files(repo, {"src/rtb/dsp/server.py": text})
    write_manifests(repo)


def _skipped_test(repo):
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause", "@pytest.mark.skip\ndef test_pause")})
    write_manifests(repo)


def _conftest_changed_without_rehash(repo):
    # 測試先改成會失敗、清單照實重算;之後才在 conftest 偷偷加改結果的鉤子,沒重算雜湊
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")})
    write_manifests(repo)
    conftest = repo / "tests/dsp/conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + EVIL_CONFTEST_HOOK,
                        encoding="utf-8")


@pytest.mark.parametrize(("forge", "expected"), [
    (_only_says_done, "result"),
    (_code_changed_hash_not, "src/rtb/kit.py"),
    (_claim_wider_than_evidence, "void"),
    (_skipped_test, NODE_PAUSE),
    (_conftest_changed_without_rehash, "tests/dsp/conftest.py"),
])
def test_every_forgery_in_the_plan_is_blocked(repo, forge, expected):
    forge(repo)

    code, output = verify(repo)

    assert code == 1, output
    assert expected in output


# ── [S817] 雜湊輔助:只印哪幾個檔的雜湊跟現況不一樣、正確值,不寫任何檔 ──────────────

def test_a_conftest_that_rewrites_results_and_is_rehashed_passes_which_is_the_ceiling(repo):
    """天花板釘住:作者連 conftest 的雜湊一起重算,驗證器擋不住(機械上看不出重看過還是重貼),
    歸審查員;清單差異會進提交,審查員看得到。這支測試是紀錄,不是要驗證器擋。"""
    write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace(
        "def test_pause():\n    assert act", "def test_pause():\n    assert not act")})
    conftest = repo / "tests/dsp/conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + EVIL_CONFTEST_HOOK,
                        encoding="utf-8")
    write_manifests(repo)  # 重算雜湊

    code, output = verify(repo)

    assert code == 0, output


REMINDER = "重貼雜湊前先確認證據仍成立"


def _snapshot(root):
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def hashes(root, *args):
    return subprocess.run([sys.executable, "-m", "tools.claim_hashes", *args,
                           str(root / "claims")], cwd=ROOT, capture_output=True, text=True,
                          timeout=120, check=False)


def test_the_hash_helper_only_prints_the_stale_files_and_writes_nothing(repo):
    (repo / "src/rtb/kit.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    before = _snapshot(repo)

    result = hashes(repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == REMINDER
    stale = [line for line in lines if "src/rtb/kit.py" in line]
    assert len(stale) == len(REQUIRED)  # 五份清單都列了這支
    assert all(sha(repo, "src/rtb/kit.py") in line for line in stale)
    assert not any("src/rtb/dsp/store.py" in line for line in lines)  # 沒變的不列
    assert _snapshot(repo) == before  # 一個位元組都沒寫


def test_the_hash_helper_says_so_when_nothing_is_stale(repo):
    result = hashes(repo)

    assert result.returncode == 0
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == REMINDER
    assert any("清單已列的檔雜湊都跟現況一樣" in line and "以驗證器為準" in line
               for line in lines[:-1])


def test_the_hash_helper_skips_paths_the_verifier_would_block(repo):
    data = manifest(repo, "concurrency")
    data["scope"] += [{"path": "src/rtb/gone.py", "sha256": "0" * 64},
                      {"path": "/etc/hosts", "sha256": "0" * 64}]
    set_raw(repo, "concurrency", json.dumps(data, ensure_ascii=False))

    result = hashes(repo)

    assert result.returncode == 0, result.stderr
    assert "gone.py" not in result.stdout and "/etc/hosts" not in result.stdout


def test_the_hash_helper_cannot_read_a_broken_manifest(repo):
    set_raw(repo, "concurrency", "{")

    assert hashes(repo).returncode == 2


def test_the_hash_helper_writes_no_bytecode_either(repo, tmp_path_factory):
    """「不寫任何檔」包括 __pycache__:在沒有既存 __pycache__ 的複本跑(代碼審)。python -m 會在
    輔助本身任何一行執行之前就把它編譯成 .pyc,程式裡攔不到,所以文件寫的指令帶 -B;程式開頭的
    sys.dont_write_bytecode 擋的是之後匯入驗證器產生的 .pyc。"""
    copy = tmp_path_factory.mktemp("helper-copy")
    (copy / "tools").mkdir()
    for name in ("verify_claims.py", "claim_hashes.py"):
        (copy / "tools" / name).write_bytes((ROOT / "tools" / name).read_bytes())
    before = _snapshot(copy)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}

    result = subprocess.run([sys.executable, "-B", "-m", "tools.claim_hashes",
                             str(repo / "claims")], cwd=copy, env=env, capture_output=True,
                            text=True, timeout=120, check=False)

    assert result.returncode == 0, result.stderr
    assert _snapshot(copy) == before


def test_running_the_hash_helper_as_a_script_says_how_to_run_it(repo):
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "claim_hashes.py"),
                             str(repo / "claims")], cwd=ROOT, capture_output=True, text=True,
                            timeout=120, check=False)

    assert result.returncode == 2
    assert "python -B -m tools.claim_hashes" in result.stderr
    assert "Traceback" not in result.stderr


def test_reading_a_registry_inside_an_assignment_target_is_not_a_rewrite(repo):
    """只算真的寫入:拿登錄表當字典的鍵是讀,不是改它。"""
    write_files(repo, {"tests/dsp/conftest.py": FILES["tests/dsp/conftest.py"] + (
        "\n\nfrom rtb.dsp import server\n\nSEEN = {}\nSEEN[server.WRITE_ACTIONS] = 1\n")})
    write_manifests(repo)

    code, output = verify(repo)

    assert code == 0, output


def test_without_dash_b_only_the_helper_itself_gets_compiled(repo, tmp_path_factory):
    """沒加 -B 時,python -m 會先把輔助本身編成 .pyc(攔不到);程式開頭的旗標要保證匯入的驗證器
    不再多寫一個 .pyc。"""
    copy = tmp_path_factory.mktemp("helper-copy-no-b")
    (copy / "tools").mkdir()
    for name in ("verify_claims.py", "claim_hashes.py"):
        (copy / "tools" / name).write_bytes((ROOT / "tools" / name).read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}

    result = subprocess.run([sys.executable, "-m", "tools.claim_hashes", str(repo / "claims")],
                            cwd=copy, env=env, capture_output=True, text=True, timeout=120,
                            check=False)

    assert result.returncode == 0, result.stderr
    written = sorted(p.name for p in (copy / "tools").rglob("*.pyc"))
    assert written and all(name.startswith("claim_hashes.") for name in written), written


# ── 增量 2 代碼審第 2 輪:故障注入證據一律「看不懂就擋」 ─────────────────────────

TRICK_HEAD = "import pytest\n\nfrom rtb.dsp.server import act\n\n\n"
TRICK_FIXTURE = '@pytest.fixture\ndef mode():\n    return "DEATH"\n\n\n'


def _trick(body, *, head=TRICK_HEAD):
    return head + body


INJECTION_TRICKS = {
    # 死碼與常數條件
    "常數 if": (_trick('def test_trick():\n    fault = "DEATH"\n    if True:\n'
                     '        assert act(fault)\n'), "常數條件"),
    "常數 while": (_trick('def test_trick():\n    fault = "DEATH"\n    while 0:\n        pass\n'
                        '    assert act(fault)\n'), "常數條件"),
    "條件運算式": (_trick('def test_trick():\n    fault = "DEATH" if False else "safe"\n'
                        '    assert act(fault)\n'), "常數條件"),
    "and 常數": (_trick('def test_trick():\n    fault = False and "DEATH"\n'
                       '    assert act(fault)\n'),
                 "常數條件"),
    "return 之後": (_trick('def test_trick():\n    assert act("update")\n    return\n'
                         '    act("DEATH")\n'), "之後還有敘述"),
    "raise 之後": (_trick('def test_trick():\n    assert act("update")\n    raise SystemExit\n'
                        '    act("DEATH")\n'), "之後還有敘述"),
    "fixture 裡的死碼": (_trick('@pytest.fixture\ndef mode():\n    if 1:\n'
                              '        return "DEATH"\n\n\n'
                              'def test_trick(mode):\n    assert act(mode)\n'), "常數條件"),
    # 不算引用
    "型別註記": (_trick('def test_trick():\n    fault: "DEATH" = "safe"\n    assert act(fault)\n'),
                 "注入手段"),
    "f 字串片段": (_trick('def test_trick():\n    fault = f"DEATH{1}"\n    assert act(fault)\n'),
                   "注入手段"),
    "只在 assert 條件": (_trick('def test_trick():\n    out = act("update")\n'
                              '    assert "DEATH" not in str(out)\n'), "注入手段"),
    # 不算斷言
    "任意物件的 raises": (_trick('class Probe:\n    def raises(self, kind):\n'
                               '        return kind\n\n\ndef test_trick():\n'
                               '    fault = "DEATH"\n    Probe().raises(fault)\n'),
                          "斷言"),
    "別的物件的 raises 放在 with": (_trick(
        'import contextlib\n\n\nclass Probe:\n    def raises(self, kind):\n'
        '        return contextlib.nullcontext()\n\n\ndef test_trick():\n'
        '    fault = "DEATH"\n    probe = Probe()\n    with probe.raises(ValueError):\n'
        '        act(fault)\n'), "斷言"),
    "raises 不在 with": (_trick('def test_trick():\n    fault = "DEATH"\n'
                              '    pytest.raises(ValueError)\n'
                              '    act(fault)\n'), "斷言"),
    # 類別層
    "類別本體有 fixture": (_trick('class TestTrick:\n    @pytest.fixture\n    def other(self):\n'
                                '        return 1\n\n    def test_trick(self):\n'
                                '        fault = "DEATH"\n        assert act(fault)\n'), "類別"),
    "類別有基底": (_trick('class Base:\n    pass\n\n\nclass TestTrick(Base):\n'
                        '    def test_trick(self):\n        fault = "DEATH"\n'
                        '        assert act(fault)\n'),
                   "基底"),
    # 覆蓋規則
    "帶預設值的參數不追": (_trick(TRICK_FIXTURE + 'def test_trick(mode=None):\n'
                                 '    assert act("update")\n'),
                           "注入手段"),
    "parametrize 同名": (_trick(TRICK_FIXTURE + '@pytest.mark.parametrize("mode", ["safe"])\n'
                                'def test_trick(mode):\n    assert act(mode)\n'), "parametrize"),
    "parametrize 非字面": (_trick('NAMES = "mode"\n\n\n' + TRICK_FIXTURE +
                                  '@pytest.mark.parametrize(NAMES, ["safe"])\n'
                                  'def test_trick(mode):\n    assert act(mode)\n'), "parametrize"),
    "最外層賦值蓋掉": (_trick(TRICK_FIXTURE + 'mode = None\n\n\ndef test_trick(mode):\n'
                             '    assert act(mode)\n'), "綁定"),
    "最外層匯入蓋掉": (_trick(TRICK_FIXTURE + 'from rtb.kit import ok as mode  # noqa: E402\n\n\n'
                             'def test_trick(mode):\n    assert act(mode)\n'), "綁定"),
    "usefixtures 非字面": (_trick('NAME = "mode"\n\n\n' + TRICK_FIXTURE +
                                  '@pytest.mark.usefixtures(NAME)\ndef test_trick():\n'
                                  '    assert act("update")\n'), "usefixtures"),
    "autouse 非字面": (_trick('AUTO = True\n\n\n@pytest.fixture(autouse=AUTO)\ndef mode():\n'
                              '    return "DEATH"\n\n\ndef test_trick():\n'
                              '    assert act("update")\n'),
                       "autouse"),
    # 代碼審第 3 輪:死碼改白名單(條件沒有名字、屬性、呼叫就是常數;break、continue、兩支都結束的 if)
    "not True": (_trick('def test_trick():\n    act("DEATH")\n    if not True:\n'
                        '        assert False\n'), "常數條件"),
    "空 tuple 條件": (_trick('def test_trick():\n    act("DEATH")\n    if ():\n'
                           '        assert False\n'), "常數條件"),
    "字面比較": (_trick('def test_trick():\n    act("DEATH")\n    if 1 == 2:\n'
                      '        assert False\n'), "常數條件"),
    "for 字面迭代": (_trick('def test_trick():\n    act("DEATH")\n    for _ in ():\n'
                          '        assert False\n'), "常數條件"),
    "break 之後": (_trick('def test_trick(request):\n    act("DEATH")\n'
                        '    for _ in request.param_list:\n        break\n'
                        '        assert False\n'), "之後還有敘述"),
    "continue 之後": (_trick('def test_trick(request):\n    act("DEATH")\n'
                           '    for _ in request.param_list:\n        continue\n'
                           '        assert False\n'), "之後還有敘述"),
    "if else 兩支都 return": (_trick('def test_trick(request):\n    act("DEATH")\n'
                                   '    if request:\n        return\n    else:\n'
                                   '        return\n    assert False\n'), "之後還有敘述"),
    # 代碼審第 3 輪:parametrize 的其他寫法一律擋
    "模組層 pytestmark": (_trick('pytestmark = pytest.mark.parametrize("mode", ["safe"])\n\n\n'
                                 + TRICK_FIXTURE + 'def test_trick(mode):\n'
                                 '    assert act(mode)\n'), "pytestmark"),
    "類別本體 pytestmark": (_trick(TRICK_FIXTURE + 'class TestTrick:\n'
                                   '    pytestmark = [\n'
                                   '        pytest.mark.parametrize("mode", ["safe"])]\n\n'
                                   '    def test_trick(self, mode):\n        assert act(mode)\n'),
                            "pytestmark"),
    "變數裝飾器": (_trick('CASES = pytest.mark.parametrize("mode", ["safe"])\n\n\n'
                        + TRICK_FIXTURE + '@CASES\ndef test_trick(mode):\n'
                        '    assert act(mode)\n'), "裝飾器"),
    "測試檔 pytest_generate_tests": (_trick(
        'def pytest_generate_tests(metafunc):\n'
        '    metafunc.parametrize("mode", ["safe"])\n\n\n'
        + TRICK_FIXTURE + 'def test_trick(mode):\n    assert act(mode)\n'),
        "pytest_generate_tests"),
    "類別 pytest_generate_tests": (_trick(
        TRICK_FIXTURE + 'class TestTrick:\n    def pytest_generate_tests(self, metafunc):\n'
        '        metafunc.parametrize("mode", ["safe"])\n\n'
        '    def test_trick(self, mode):\n        assert act(mode)\n'), "pytest_generate_tests"),
    "不是 pytest 的 mark": (_trick('from types import SimpleNamespace  # noqa: E402\n\n'
                                   'helpers = SimpleNamespace()\n\n\n' + TRICK_FIXTURE +
                                   '@helpers.mark.slow\ndef test_trick(mode):\n'
                                   '    assert act(mode)\n'), "裝飾器"),
    # 代碼審第 3 輪:藏在區塊裡的 fixture、來源不明的 fixture 裝飾器
    "try 裡的同名 fixture": (_trick(TRICK_FIXTURE + 'try:\n    @pytest.fixture\n'
                                    '    def mode():  # noqa: F811\n        return "safe"\n'
                                    'except ImportError:\n    pass\n\n\n'
                                    'def test_trick(mode):\n    assert act(mode)\n'), "綁定"),
    "if 裡的同名 fixture": (_trick(TRICK_FIXTURE + 'if pytest:\n    @pytest.fixture\n'
                                   '    def mode():  # noqa: F811\n        return "safe"\n\n\n'
                                   'def test_trick(mode):\n    assert act(mode)\n'), "綁定"),
    "假 fixture 裝飾器": (_trick('def fake_fixture(**kwargs):\n    return lambda f: f\n\n\n'
                                '@fake_fixture(autouse=True)\ndef mode():\n'
                                '    return "DEATH"\n\n\ndef test_trick():\n'
                                '    assert act("update")\n'), "來源"),
}
INJECTION_PASSES = {
    "usefixtures 帶手段": _trick(TRICK_FIXTURE + '@pytest.mark.usefixtures("mode")\n'
                                 'def test_trick():\n    assert act("update")\n'),
    "autouse 帶手段": _trick('@pytest.fixture(autouse=True)\ndef mode():\n    return "DEATH"\n\n\n'
                             'def test_trick():\n    assert act("update")\n'),
    "from pytest import raises": _trick(
        'def test_trick():\n    fault = "DEATH"\n    with raises(ZeroDivisionError):\n'
        '        1 / 0\n    act(fault)\n', head=TRICK_HEAD + "from pytest import raises\n\n\n"),
    # 代碼審第 3 輪:沒被呼叫的巢狀函式不算執行,裡面的常數條件不擋(跟引用判定同一個邊界)
    "巢狀函式裡的常數條件": _trick('def test_trick():\n    def _reference():\n        if True:\n'
                                   '            return 1\n        return 2\n\n'
                                   '    fault = "DEATH"\n    assert act(fault)\n'),
    "lambda 裡的條件運算式": _trick('def test_trick():\n    _ = lambda: 1 if True else 2\n'
                                    '    fault = "DEATH"\n    assert act(fault)\n'),
    "不帶呼叫的 pytest.mark": _trick('@pytest.mark.slow\ndef test_trick():\n'
                                     '    fault = "DEATH"\n    assert act(fault)\n'),
    "from pytest import fixture": _trick(
        '@fixture(autouse=True)\ndef mode():\n    return "DEATH"\n\n\n'
        'def test_trick():\n    assert act("update")\n',
        head=TRICK_HEAD + "from pytest import fixture\n\n\n"),
    "import pytest as 別名": _trick(
        '@pt.fixture\ndef mode():\n    return "DEATH"\n\n\n'
        'def test_trick(mode):\n    assert act(mode)\n',
        head="import pytest as pt\n\nfrom rtb.dsp.server import act\n\n\n"),
}


def _with_trick(repo, text):
    write_files(repo, {"tests/dsp/test_trick.py": text})
    node = ("tests/dsp/test_trick.py::TestTrick::test_trick" if "class TestTrick" in text
            else "tests/dsp/test_trick.py::test_trick")
    evidence = manifest(repo, "concurrency")["evidence"]
    write_manifests(repo, evidence=[*evidence, {"node": node, "covers": [],
                                                "kind": "failure_injection", "injection": "DEATH"}],
                    harness=[*HARNESS, "tests/dsp/test_trick.py"])


@pytest.mark.parametrize("trick", sorted(INJECTION_TRICKS))
def test_failure_injection_evidence_the_verifier_cannot_read_is_blocked(repo, trick):
    """死碼、常數條件、只出現在 assert 條件或 f 字串片段的手段、不在 with 裡的 raises、類別層
    fixture、覆蓋 fixture 的寫法、非字面的 usefixtures 與 autouse,一律擋並講明原因。"""
    text, expected = INJECTION_TRICKS[trick]
    _with_trick(repo, text)

    code, output = verify(repo)

    assert code == 1, output
    assert any("test_trick" in line and expected in line for line in output.splitlines()), output


@pytest.mark.parametrize("trick", sorted(INJECTION_PASSES))
def test_usefixtures_autouse_and_imported_raises_are_followed(repo, trick):
    _with_trick(repo, INJECTION_PASSES[trick])

    code, output = verify(repo)

    assert code == 0, output


def test_a_conftest_pytest_generate_tests_on_the_way_blocks_failure_injection(repo):
    """路上的 conftest 定義 pytest_generate_tests,它能用同名參數蓋掉 fixture(代碼審第 3 輪)。"""
    conftest = repo / "tests" / "dsp" / "conftest.py"
    write_files(repo, {"tests/dsp/conftest.py": conftest.read_text(encoding="utf-8") +
                       "\n\ndef pytest_generate_tests(metafunc):\n    pass\n"})
    _with_trick(repo, _trick(TRICK_FIXTURE + 'def test_trick(mode):\n    assert act(mode)\n'))

    code, output = verify(repo)

    assert code == 1, output
    assert any("test_trick" in line and "pytest_generate_tests" in line
               for line in output.splitlines()), output


def test_a_stop_signal_ends_the_evidence_run_it_started(repo):
    """[Phase 12 代碼審 r3 s2/p1/x2] 驗證器收到 SIGTERM:它另開行程群組起的證據測試(連同測試再起的孫
    行程)一起結束,不變孤兒。一鍵展示逾時或取消時先送 SIGTERM,靠的就是這一條。"""
    import signal

    marker = repo / "child.pid"
    slow = TEST_SERVER.replace(
        "def test_pause():\n    assert act",
        "def test_pause():\n    import subprocess, sys, time\n"
        "    child = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(60)\"])\n"
        f"    open({str(marker)!r}, \"w\").write(str(child.pid))\n"
        "    time.sleep(60)\n    assert act")
    write_files(repo, {"tests/dsp/test_server.py": slow})
    write_manifests(repo)
    verifier = subprocess.Popen([sys.executable, str(VERIFIER), str(repo / "claims")],
                                cwd=repo, stdout=subprocess.PIPE, text=True)
    try:
        for _ in range(300):
            if marker.is_file() and marker.read_text():
                break
            time.sleep(0.1)
        child = int(marker.read_text())
        verifier.send_signal(signal.SIGTERM)
        assert verifier.wait(10) != 0
    finally:
        verifier.kill()
        verifier.stdout.close()
    with pytest.raises(ProcessLookupError):
        for _ in range(50):
            os.kill(child, 0)
            time.sleep(0.1)
