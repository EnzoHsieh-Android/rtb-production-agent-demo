"""mypy 轉 SARIF 的轉換器:讓 lumos 的「新增告警閘」能擋下新增的型別錯誤。"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "tools" / "mypy_sarif.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("mypy_sarif", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE = "\n".join([
    'src/a.py:2:12: error: Incompatible return value type (got "int", expected "str")'
    "  [return-value]",
    "src/a.py:2:12: note: this is a note that belongs to the error above",
    "src/b.py:10:5: warning: Unused type: ignore comment  [unused-ignore]",
    "src/c.py:7:1: error: Something without a code",
    "not a diagnostic line at all",
])


def test_parse_reads_errors_and_warnings_with_position_rule_and_message():
    adapter = load_adapter()

    found = adapter.parse_mypy_output(SAMPLE)

    assert [(f.path, f.line, f.column, f.level, f.rule) for f in found] == [
        ("src/a.py", 2, 12, "error", "return-value"),
        ("src/b.py", 10, 5, "warning", "unused-ignore"),
        ("src/c.py", 7, 1, "error", "mypy"),  # 沒有規則代碼時用工具名
    ]
    assert "Incompatible return value" in found[0].message


def test_notes_and_unrelated_lines_are_not_reported_as_findings():
    adapter = load_adapter()

    assert adapter.parse_mypy_output("src/a.py:1:1: note: just a note\nrandom text\n") == []


def test_sarif_document_has_the_structure_lumos_and_other_consumers_expect():
    adapter = load_adapter()
    findings = adapter.parse_mypy_output(SAMPLE)

    document = adapter.to_sarif(findings)

    assert document["version"] == "2.1.0" and len(document["runs"]) == 1
    run = document["runs"][0]
    assert run["tool"]["driver"]["name"] == "mypy" and len(run["results"]) == 3
    first = run["results"][0]
    assert first["ruleId"] == "return-value" and first["level"] == "error"
    location = first["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "src/a.py"
    assert location["region"] == {"startLine": 2, "startColumn": 12}
    rules = {rule["id"] for rule in run["tool"]["driver"]["rules"]}
    assert rules == {"return-value", "unused-ignore", "mypy"}


def test_an_empty_result_is_still_a_valid_sarif_document():
    adapter = load_adapter()

    document = adapter.to_sarif([])

    assert document["runs"][0]["results"] == []


def run_adapter(tmp_path, *mypy_args):
    out = tmp_path / "out.sarif"
    result = subprocess.run(
        [sys.executable, str(ADAPTER), "--out", str(out), *mypy_args],
        capture_output=True, text=True, cwd=ROOT, timeout=300,
    )
    return result, out


def test_end_to_end_a_real_type_error_becomes_a_sarif_result(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(x: int) -> str:\n    return x\n", encoding="utf-8")

    result, out = run_adapter(tmp_path, "--config-file", "/dev/null", "--strict", str(bad))

    assert result.returncode == 0, result.stderr  # 有發現不代表工具失敗;告警由 lumos 判斷
    results = json.loads(out.read_text(encoding="utf-8"))["runs"][0]["results"]
    assert any(r["ruleId"] == "return-value" for r in results)


def test_a_clean_project_produces_zero_results(tmp_path):
    result, out = run_adapter(tmp_path)  # 用專案設定檢查 src

    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["runs"][0]["results"] == []


def test_when_mypy_itself_cannot_run_the_adapter_fails_loudly_instead_of_writing_an_empty_report(
    tmp_path,
):
    result, out = run_adapter(tmp_path, "--config-file", "/nonexistent/mypy.ini")

    assert result.returncode != 0  # 工具壞掉不能被當成「沒有告警」
    assert not out.exists()


def test_errors_without_a_column_number_are_still_reported():
    adapter = load_adapter()
    text = 'src/x.py:8: error: Unused "type: ignore" comment  [unused-ignore]\n'

    found = adapter.parse_mypy_output(text)

    expected = [("src/x.py", 8, 1, "unused-ignore")]
    assert [(f.path, f.line, f.column, f.rule) for f in found] == expected


def test_paths_are_percent_encoded_in_the_sarif_uri_and_survive_a_decode_round_trip():
    from urllib.parse import unquote

    adapter = load_adapter()
    finding = adapter.Finding("d with space/b c%20.py", 3, 2, "error", "x", "m")

    uri = adapter.to_sarif([finding])["runs"][0]["results"][0]["locations"][0][
        "physicalLocation"]["artifactLocation"]["uri"]

    assert " " not in uri and unquote(uri) == "d with space/b c%20.py"


def test_only_paths_inside_the_configured_type_checked_directories_are_passed_to_mypy():
    adapter = load_adapter()

    kept = adapter.select_paths(["src/rtb/a.py", "tests/test_x.py", "tools/t.py", "docs/n.py"],
                                ["src", "tools"])

    assert kept == ["src/rtb/a.py", "tools/t.py"]


def test_when_every_changed_file_is_outside_the_checked_directories_mypy_is_not_run(
    tmp_path, monkeypatch
):
    adapter = load_adapter()
    calls = []
    monkeypatch.setattr(adapter, "run_mypy", lambda args: calls.append(args))
    out = tmp_path / "o.sarif"

    code = adapter.main(["--out", str(out), "--only-configured-paths", "tests/test_x.py"])

    assert code == 0 and calls == []  # 只改了測試檔:沒有要檢查的型別,不能誤跑整包 mypy
    assert json.loads(out.read_text(encoding="utf-8"))["runs"][0]["results"] == []


def test_a_nonzero_exit_with_output_nobody_can_parse_is_a_failure_not_a_clean_report(
    tmp_path, monkeypatch
):
    adapter = load_adapter()
    fake = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="weird unparseable text\n", stderr=""
    )
    monkeypatch.setattr(adapter, "run_mypy", lambda _args: fake)
    out = tmp_path / "o.sarif"

    code = adapter.main(["--out", str(out)])

    assert code == 2 and not out.exists()  # 這個分支曾經沒有測試守著


def test_lumos_passes_temporary_extracted_copies_of_the_changed_files_and_they_must_still_match():
    """lumos 會把「改動前」與「現在」的檔案各抽到 .lumos/lintbase-*/base|head/ 再交給命令;
    範圍判斷不能被這層臨時目錄擋掉,否則 mypy 根本不會跑,新增的型別錯誤就全部漏過
    (2026-09-22 用 lumos 自己的判定函式實測抓到)。"""
    adapter = load_adapter()
    paths = [
        ".lumos/lintbase-ab12/head/src/rtb/domain/x.py",
        ".lumos/lintbase-ab12/base/tools/t.py",
        ".lumos/lintbase-ab12/head/tests/test_x.py",
        ".lumos/lintbase-ab12/head/docs/n.py",
    ]

    kept = adapter.select_paths(paths, ["src", "tools"])

    assert kept == paths[:2]


def test_paths_reached_through_a_symlink_to_the_project_are_still_recognised(
    tmp_path, monkeypatch
):
    """macOS 的 /tmp 是 /private/tmp 的符號連結:同一個檔案用兩種寫法,結果必須一樣。
    不一致時 mypy 會被整批略過並回報「沒有新增告警」(2026-09-22 第二輪代碼審實測)。"""
    real = tmp_path / "real"
    (real / "src" / "rtb").mkdir(parents=True)
    (real / "src" / "rtb" / "a.py").write_text("", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)
    adapter = load_adapter()
    monkeypatch.setattr(adapter, "ROOT", real.resolve())
    reached_through_link = str(link / "src" / "rtb" / "a.py")

    assert adapter.select_paths([reached_through_link], ["src"]) == [reached_through_link]


def test_a_file_outside_the_project_root_makes_the_adapter_fail_loudly_instead_of_reporting_clean(
    tmp_path, monkeypatch
):
    adapter = load_adapter()
    calls = []
    monkeypatch.setattr(adapter, "run_mypy", lambda args: calls.append(args))
    out = tmp_path / "o.sarif"

    code = adapter.main(["--out", str(out), "--only-configured-paths", "/somewhere/else/x.py"])

    assert code == 2 and calls == [] and not out.exists()  # 不能悄悄變成一份乾淨的報告


def test_path_normalisation_stops_dot_dot_segments_and_windows_separators_from_fooling_the_scope():
    adapter = load_adapter()

    assert adapter.select_paths(["src/../tests/x.py"], ["src", "tools"]) == []
    assert adapter.select_paths([".lumos/lintbase-ab/head/tests/../src/x.py"], ["src"]) == [
        ".lumos/lintbase-ab/head/tests/../src/x.py"
    ]
    assert adapter.select_paths(["src\\rtb\\x.py"], ["src"]) == ["src\\rtb\\x.py"]


def _project_with_pyproject(tmp_path, monkeypatch, body):
    adapter = load_adapter()
    (tmp_path / "pyproject.toml").write_text(body, encoding="utf-8")
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    return adapter


@pytest.mark.parametrize("body", [
    "[tool.mypy]\nstrict = true\n",                 # 沒有 files
    "[tool.mypy]\nfiles = []\n",                    # 空清單
    "[tool.mypy]\nfiles = 5\n",                     # 型別不對
    "[tool.other]\nx = 1\n",                        # 沒有 mypy 區段
])
def test_an_unusable_check_scope_is_an_error_not_a_silent_clean_report(tmp_path, monkeypatch, body):
    adapter = _project_with_pyproject(tmp_path, monkeypatch, body)

    with pytest.raises(ValueError):
        adapter.configured_directories()


def test_a_scope_written_as_a_string_is_split_into_directories(tmp_path, monkeypatch):
    adapter = _project_with_pyproject(tmp_path, monkeypatch, '[tool.mypy]\nfiles = "src, tools"\n')

    assert adapter.configured_directories() == ["src", "tools"]


def test_a_scope_of_dot_means_the_whole_project(tmp_path, monkeypatch):
    adapter = _project_with_pyproject(tmp_path, monkeypatch, '[tool.mypy]\nfiles = ["."]\n')
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "x.py").write_text("x = 1\n", encoding="utf-8")

    target = str(tmp_path / "pkg" / "x.py")
    kept = adapter.select_paths([target], adapter.configured_directories())

    assert kept == [target]


def test_main_fails_loudly_when_the_scope_cannot_be_determined(tmp_path, monkeypatch, capsys):
    adapter = _project_with_pyproject(tmp_path, monkeypatch, "[tool.mypy]\nstrict = true\n")
    monkeypatch.setattr(adapter, "run_mypy", lambda _args: pytest.fail("mypy 不該被執行"))
    out = tmp_path / "out.sarif"

    code = adapter.main(["--out", str(out), "--only-configured-paths", "src/x.py"])

    assert code == 2 and not out.exists()
    assert "範圍" in capsys.readouterr().err
