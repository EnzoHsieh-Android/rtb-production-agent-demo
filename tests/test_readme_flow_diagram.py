"""README 流程動圖產生器(docs/assets/make_agent_flow_gif.py)的畫法守衛:AI 寫提案說明是收件收下之後的
旁支,不准又被畫回送出與收件之間的主線(Issues/流程圖把AI說明畫在送出建議之前;代碼審 r1 正確性 F3)。

產生器要 Pillow,Pillow 不是專案依賴;所以不匯入產生器,只用語法樹讀它模組層的 EDGES、NOTES、CAPTIONS
字面值,CI 沒裝 Pillow 也照樣跑、不跳過。
"""

import ast
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "docs" / "assets" / "make_agent_flow_gif.py"


def _literal(name: str) -> Any:
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    found = [node.value for node in tree.body
             if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]
    assert len(found) == 1, f"產生器模組層要剛好有一個 {name} = 字面值"
    return ast.literal_eval(found[0])


def test_the_readme_diagram_keeps_the_narrative_off_the_main_line() -> None:
    edges = _literal("EDGES")
    pairs = {(first, second): kind for first, second, _points, kind in edges}
    assert pairs.get(("submit", "inbox")) == "main", "送出提案要直接接收件檢查"
    assert not [p for p in pairs if p[0] == "narrate"], "AI 說明是旁支,不准有出去的邊"
    into = {p: kind for p, kind in pairs.items() if p[1] == "narrate"}
    # 說明命令列只讀收件收下、交給執行的提案:旁支從待處理佇列岔出,畫成 AI 色虛線
    assert into == {("queue", "narrate"): "note"}, into


def test_the_readme_diagram_says_the_narrative_comes_after_intake_the_same_way() -> None:
    """格子標題與註記用同一個說法(建議被收下後),不再寫「送件後供確認者參考」。"""
    caption = _literal("CAPTIONS")["narrate"]
    notes = [text for _x, _y, text in _literal("NOTES")]
    [note] = [text for text in notes if "只給人看" in text]
    assert caption.startswith("建議被收下後") and note.startswith("建議被收下後")
    assert not any("確認者" in text for text in (*notes, caption))
