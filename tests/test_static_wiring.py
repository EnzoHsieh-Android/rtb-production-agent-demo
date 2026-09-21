"""接線檢查:專案裡設定了哪個靜態分析工具,它就必須真的被閘執行,不能只是設定放著。

專治「加了工具卻忘記接線」與「接了線卻沒有作用」:
- 設定存在、卻沒人在推送前或 CI 執行,等於沒有檢查;
- lumos 的新增告警閘只會執行帶 {LINT_FILES} 佔位符的命令,沒帶的整條被跳過(2026-09-22 代碼審抓到);
- CI 的指令若被 echo、|| true、if: 條件包住,字面上有工具名稱也不會真的擋。
"""

import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANALYZERS = {"ruff", "mypy", "pyright", "bandit", "pylint", "flake8", "pytype", "pyre"}
CI_FILE = ROOT / ".github" / "workflows" / "ci.yml"
LINT_TOOL_PREFIX = {"ruff": ".venv/bin/ruff check", "mypy": ".venv/bin/python tools/mypy_sarif.py"}


# CI 裡三個檢查步驟必須是「精確的」指令字串:任何加上 --exit-zero、|| exit 0、| tee、--help、
# --collect-only、-k 之類的變體,都會讓檢查「看起來有跑、其實不擋」。
EXPECTED_STEPS = {
    "ruff": "python -m ruff check .",
    "mypy": "python -m mypy",
    "pytest": "python -m pytest -q",
}
NON_STEP_PARENTS = ("with:", "env:")  # 這兩個底下的 run: 鍵不是要執行的步驟
SHELL_CONTROL_WORDS = (
    "if", "then", "else", "fi", "case", "for", "while", "until", "exit", "set", "trap",
)
SHELL_OPERATORS = ("||", "&&", ";", "|")
TRIGGER_LIMITS = ("branches", "branches-ignore", "paths", "paths-ignore", "tags", "tags-ignore")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _parent_key(lines: list[str], index: int) -> str:
    """往上找第一行縮排比較淺的非空行,就是這一行所屬的鍵。"""
    here = _indent(lines[index])
    for back in range(index - 1, -1, -1):
        if lines[back].strip() and _indent(lines[back]) < here:
            return lines[back].strip()
    return ""


def parse_run_commands(text: str) -> list[str]:
    """取出 CI 裡真正會執行的步驟指令;支援單行 run: 與多行的 run: | 區塊。

    不算 with:、env: 底下的 run: 鍵;步驟名稱、註解都不看。只支援這幾種寫法,其他寫法由
    ci_problems 明確指出,而不是默默解讀錯。
    """
    lines, commands, index = text.splitlines(), [], 0
    while index < len(lines):
        stripped = lines[index].strip()
        is_run = stripped.startswith(("run:", "- run:"))
        if is_run and _parent_key(lines, index).lstrip("- ") not in NON_STEP_PARENTS:
            value = stripped.split("run:", 1)[1].strip()
            if value in ("|", ">", "|-", ">-"):
                indent = _indent(lines[index])
                index += 1
                while index < len(lines) and (
                    not lines[index].strip() or _indent(lines[index]) > indent
                ):
                    if lines[index].strip():
                        commands.append(lines[index].strip())
                    index += 1
                continue
            commands.append(value)
        index += 1
    return commands


def ci_problems(text: str) -> list[str]:
    """CI 檔案裡會讓檢查「看起來有跑、其實不擋」的寫法,以及本檢查不支援的寫法。"""
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        code = line.split("#", 1)[0]
        stripped = code.strip()
        if "continue-on-error" in code:
            problems.append(f"{number}: continue-on-error")
        if stripped.startswith(("if:", "- if:")):
            problems.append(f"{number}: 步驟或工作被條件包住")
        if any(stripped.startswith(f"{key}:") for key in TRIGGER_LIMITS):
            problems.append(f"{number}: 推送觸發被分支、路徑或標籤限制")
        if "|| true" in code or "||true" in code or "|| exit" in code:
            problems.append(f"{number}: || true 或 || exit 吞掉失敗")
        if stripped.startswith(("run: exit", "- run: exit", "exit ")):
            problems.append(f"{number}: exit 讓後面的步驟不會執行")
        if stripped.startswith(("run:", "- run:")):
            value = stripped.split("run:", 1)[1].strip()
            plain_block = value in ("|", ">", "|-", ">-")
            if value[:1] in ('"', "'", "{") or (value[:1] in "|>" and not plain_block):
                problems.append(f"{number}: 不支援的 run 寫法(請用單行或 run: | 區塊):{value}")
    problems += [f"shell 控制流程或運算子會讓指令不一定執行、或吞掉結束碼:{c}"
                 for c in parse_run_commands(text) if _has_shell_control(c)]
    return problems


def _has_shell_control(command: str) -> bool:
    words = command.split()
    return bool(words and words[0] in SHELL_CONTROL_WORDS) or any(
        operator in command for operator in SHELL_OPERATORS
    )


def runs_tool(commands: list[str], tool: str) -> bool:
    """必須有一個步驟的指令「完全等於」預期字串,不接受任何變體。"""
    return EXPECTED_STEPS[tool] in commands


def configured_analyzers() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return ANALYZERS & set(pyproject.get("tool", {}))


def lint_commands() -> list[str]:
    declared = json.loads((ROOT / ".lumos" / "lint.json").read_text(encoding="utf-8"))
    return [command for commands in declared.values() for command in commands]


def test_the_expected_analyzers_are_actually_configured():
    assert {"ruff", "mypy"} <= configured_analyzers()  # 守衛的守衛:設定沒了,這條檢查就沒意義


def test_every_configured_analyzer_is_executed_by_the_ci_workflow():
    commands = parse_run_commands(CI_FILE.read_text(encoding="utf-8"))

    for tool in configured_analyzers() & set(EXPECTED_STEPS):
        expected = EXPECTED_STEPS[tool]
        assert runs_tool(commands, tool), f"{tool} 有設定,但 CI 沒有精確執行「{expected}」"


def test_the_ci_workflow_contains_nothing_that_makes_a_failing_check_pass():
    assert ci_problems(CI_FILE.read_text(encoding="utf-8")) == []


def test_the_ci_workflow_runs_on_every_push_and_actually_runs_the_test_suite():
    text = CI_FILE.read_text(encoding="utf-8")

    assert "push:" in [line.strip() for line in text.splitlines()]
    commands = parse_run_commands(text)
    assert all(runs_tool(commands, tool) for tool in ("pytest", "ruff", "mypy"))


def test_every_lumos_lint_command_can_actually_run_in_the_new_alert_gate():
    commands = lint_commands()

    assert commands
    for command in commands:
        # lumos 的新增告警閘只執行帶 {LINT_FILES} 的命令;沒帶的被整條跳過,而且不會報錯
        assert "{LINT_FILES}" in command and "{LINT_SARIF_OUT}" in command, command


def test_every_configured_analyzer_is_also_wired_into_lumos_before_push():
    commands = lint_commands()

    for tool in configured_analyzers() & set(LINT_TOOL_PREFIX):
        assert any(command.startswith(LINT_TOOL_PREFIX[tool]) for command in commands), (
            f"{tool} 沒有接進 lumos 的 lint 宣告"
        )


# ---- 守衛的守衛:剖析與檢查函式本身要抓得到問題 ----


def test_the_ci_parser_reads_multiline_run_blocks():
    text = (
        "steps:\n  - name: x\n    run: |\n      python -m ruff check .\n      python -m mypy\n"
        "  - run: python -m pytest -q\n"
    )

    found = parse_run_commands(text)

    assert found == ["python -m ruff check .", "python -m mypy", "python -m pytest -q"]


def test_a_command_that_merely_mentions_the_tool_does_not_count_as_running_it():
    assert not runs_tool(['echo "python -m mypy"'], "mypy")
    assert not runs_tool(["python -m mypy --version && true"], "pytest")
    assert runs_tool(["python -m mypy"], "mypy")


def test_the_ci_problem_detector_flags_every_way_of_making_a_check_toothless():
    bad = "\n".join([
        "on:", "  push:", "    branches: [nonexistent]", "jobs:", "  x:", "    if: false",
        "    steps:", "      - run: python -m mypy || true", "        continue-on-error: true",
    ])

    found = " ".join(ci_problems(bad))

    assert all(word in found for word in ("分支", "條件", "|| true", "continue-on-error"))


BYPASSES = [
    "python -m mypy || exit 0", "python -m ruff check --exit-zero .", "python -m mypy | tee log",
    "python -m mypy --help", "python -m mypy docs", "python -m pytest --collect-only",
    "python -m pytest -k nothing_matches", "python3 -m mypy", 'echo "python -m mypy"',
]


def test_no_variant_of_a_step_counts_as_running_the_tool():
    for command in BYPASSES:
        for tool in EXPECTED_STEPS:
            assert not runs_tool([command], tool), command


def test_run_keys_under_with_or_env_are_not_steps_and_do_not_count():
    text = (
        "steps:\n  - uses: x/y@v1\n    with:\n      run: python -m mypy\n"
        "  - name: b\n    env:\n      run: python -m ruff check .\n"
    )

    assert parse_run_commands(text) == []


def test_exit_early_and_tag_only_triggers_are_flagged():
    text = (
        "on:\n  push:\n    tags: ['v*']\njobs:\n  x:\n    steps:\n"
        "      - run: |\n          exit 0\n          python -m mypy\n"
    )

    found = " ".join(ci_problems(text))

    assert "標籤" in found and "exit" in found


def test_unsupported_yaml_run_forms_are_reported_clearly_not_misread():
    for value in ('"python -m mypy"', "'python -m mypy'", "|+", "|2", "{run: x}"):
        problems = ci_problems(f"steps:\n  - run: {value}\n")
        assert any("不支援" in problem for problem in problems), value


def test_an_innocent_line_mentioning_paths_is_not_a_trigger_limit():
    assert ci_problems("steps:\n  - run: echo paths and branches\n") == []


@pytest.mark.parametrize("line", [
    "if false; then", "then", "fi", "case $X in", "for x in a; do", "while false; do",
    "exit 0", "set +e", "python -m mypy || echo ok", "python -m mypy && true",
])
def test_shell_control_flow_inside_a_run_block_is_flagged(line):
    text = f"steps:\n  - run: |\n      {line}\n      python -m mypy\n"

    assert any("shell" in problem for problem in ci_problems(text)), line


def test_a_plain_multiline_run_block_is_not_flagged():
    text = "steps:\n  - run: |\n      python -m ruff check .\n      python -m mypy\n"

    assert ci_problems(text) == []


def test_every_configured_analyzer_has_a_known_lumos_wiring_rule():
    unknown = configured_analyzers() - set(LINT_TOOL_PREFIX)

    assert not unknown, f"這些工具有設定,卻沒有 lumos 接線規則,請補上:{sorted(unknown)}"


def test_lumos_lint_commands_keep_the_flags_that_make_them_effective():
    commands = lint_commands()
    ruff = [c for c in commands if c.startswith(LINT_TOOL_PREFIX["ruff"])]
    mypy = [c for c in commands if c.startswith(LINT_TOOL_PREFIX["mypy"])]

    assert ruff and mypy
    assert all("--output-format sarif" in c and "--exit-zero" not in c for c in ruff)
    assert all("--only-configured-paths" in c for c in mypy)
