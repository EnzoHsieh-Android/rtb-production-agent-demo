"""領域層共用的小檢查:時間必須帶時區只有一份定義(Phase 9 增量 2 代碼審第 2 輪)。

嘗試紀錄、分析端任務歷史、指標三處原本各寫一份「沒帶時區就拒絕」,判準與訊息各自漂移。現在都呼叫
領域層這一支;這裡驗它的行為,並掃原始碼確認三處沒有自己再寫一份判斷式。
"""

import ast
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from rtb.domain._checks import AWARE_REQUIRED, require_aware

SRC = Path(__file__).resolve().parents[2] / "src" / "rtb"
USERS = (SRC / "executor" / "attempt_store.py", SRC / "analyzer" / "task_store.py",
         SRC / "ops" / "metrics.py")


def test_require_aware_checks_every_moment_with_one_fixed_message():
    aware = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
    require_aware()
    require_aware(aware, aware.astimezone(timezone(timedelta(hours=8))))
    for moments in ((aware.replace(tzinfo=None),), (aware, aware.replace(tzinfo=None)),
                    ("2026-09-24T01:00:00Z",), (None,)):
        with pytest.raises(ValueError) as refused:
            require_aware(*moments)
        assert str(refused.value) == AWARE_REQUIRED


def test_time_zone_checks_are_not_written_again_elsewhere():
    """三處只准呼叫共用的那一支:不准自己用 is_aware 判斷,也不准另定義同名的私有檢查。"""
    for path in USERS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        own = [node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and "aware" in node.name
               and node.name != "_aware_time"]
        uses = [node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id == "is_aware"]
        assert (own, uses) == ([], []), path.name
        assert any(isinstance(node, ast.Name) and node.id == "require_aware"
                   for node in ast.walk(tree)), path.name
