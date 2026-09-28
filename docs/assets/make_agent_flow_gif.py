"""以系統 Python 與 Pillow 產出 README 流程圖；對照 src/rtb/demo/flow.py。

執行：python3 docs/assets/make_agent_flow_gif.py [--font 字型檔]
Pillow 只用於文件產出，不列入專案依賴。顏色取自 demo.css 的深色主題。
"""

# ruff: noqa: RUF001, RUF002, RUF003 - 圖上的繁體中文需要全形標點。
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from html import escape
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SIZE = (1800, 760)
FONT = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
BG, SURFACE, RAISED = "#0b0d12", "#11141b", "#171a22"
TEXT, MUTED, BORDER = "#f4f4f5", "#a8adb9", "#424958"
ACCENT, CURRENT, HUMAN = "#9aa8ff", "#ffd166", "#f1c875"
COLORS = {
    "程式": ("#193a50", "#93ccee", "#ecf8ff"),
    "AI": ("#39254b", "#d5a4f0", "#f8eeff"),
    "人工": ("#493614", HUMAN, "#fff6df"),
    "平台": ("#173a2b", "#89d0ac", "#e8fff1"),
}
LANES = (("分析", 110, 320), ("收件", 340, 425), ("執行", 440, 540),
         ("廣告平台", 555, 625), ("人工", 640, 710))
NOTES = (
    (525, 313, "太舊回 A；缺資料或不慢就不提案"),
    (1290, 228, "收件收下後另寫，只給人看，執行端不讀"),
    (1570, 313, "告警原因只是假說，不進決策"),
    (1065, 714, "重放／核可後都回佇列，重新檢查"),
)
LEGEND = (
    (112, "程式", COLORS["程式"][1]),
    (180, "AI", COLORS["AI"][1]),
    (235, "人工／回頭線", HUMAN),
    (385, "模擬平台", COLORS["平台"][1]),
    (585, "A／B／C 唯讀平台；九條規則決定提案，AI 只寫說明與告警假說", TEXT),
)
EDGE_LABELS = ((300, 119, "太舊"), (535, 222, "缺資料／不慢"),
               (855, 215, "不值得／證據不足"), (240, 430, "只讀"))


@dataclass(frozen=True)
class Node:
    key: str
    x: int
    y: int
    label: tuple[str, ...]
    owner: str
    width: int = 100


NODES = (
    Node("receive", 115, 180, ("收到工作",), "程式"),
    Node("step_a", 240, 180, ("A 現況", "＋1h 成效"), "程式", 112),
    Node("filter", 400, 180, ("初篩：新鮮／配速", "暫停／異常"), "程式", 150),
    Node("step_b", 565, 180, ("B 歷史", "＋過去調整"), "程式", 112),
    Node("step_c", 715, 180, ("C 逐日＋1d/7d", "重讀現況"), "程式", 128),
    Node("rule", 885, 180, ("九條判定",), "程式", 104),
    Node("proposal", 1010, 180, ("值得加才", "程式算金額"), "程式", 116),
    Node("submit", 1130, 180, ("送出提案",), "程式"),
    Node("stop", 830, 275, ("不提案／", "證據不足"), "程式", 112),
    # AI 說明是收件收下後的旁支：從待處理佇列往上岔出，不擋在送出與收件之間；
    # 被拒收或過時的提案不會有說明
    Node("narrate", 1290, 275, ("AI 寫提案說明",), "AI", 142),
    Node("alert", 1450, 275, ("告警",), "程式"),
    Node("hypothesis", 1620, 275, ("AI 推測原因",), "AI", 132),
    Node("inbox", 1130, 382, ("收件檢查",), "程式"),
    Node("queue", 1255, 382, ("待處理佇列",), "程式", 112),
    Node("deadletter", 1120, 490, ("多次未啟動", "先停下"), "程式", 112),
    Node("recheck", 1255, 490, ("寫前重查",), "程式"),
    Node("limits", 1390, 490, ("權限／版本", "單筆／總額"), "程式", 112),
    Node("write", 1535, 490, ("憑證寫入",), "程式"),
    Node("verify", 1650, 490, ("核對結果",), "程式"),
    Node("done", 1745, 490, ("完成",), "程式", 60),
    Node("platform", 1535, 590, ("Mock DSP", "模擬平台"), "平台"),
    # 分析端的 A／B／C 都向平台唯讀取證；寫入只在執行端。
    Node("platform_read", 215, 590, ("Mock DSP", "讀現況／指標"), "平台", 112),
    Node("replay", 1145, 675, ("F6 人工重放",), "人工", 128),
    Node("approve", 1395, 675, ("F7 人工核可",), "人工", 128),
)
BY_KEY = {node.key: node for node in NODES}
MAIN = ("receive", "step_a", "filter", "step_b", "step_c", "rule",
        "proposal", "submit", "inbox", "queue",
        "recheck", "limits", "write", "platform", "verify", "done")
CAPTIONS = {
    "receive": "收到一件工作，開始分析。",
    "step_a": "A：先讀現況與近一小時成效。",
    "platform_read": "分析端三步只讀平台；寫入只在執行端。",
    "filter": "用 A 讀回的資料初篩；不偏慢就不往下查。",
    "step_b": "B：讀操作歷史與過去預算調整。",
    "step_c": "C：讀逐日、近一天／七天成效，再重讀現況。",
    "rule": "程式依序判九條；查詢有問題就判證據不足。",
    "stop": "不值得加或證據不足，就不提案。",
    "proposal": "若判值得加，金額、廣告與動作由程式決定。",
    "submit": "提案送往獨立收件口。",
    "narrate": "收件收下後，AI 另外寫給人看的說明；執行端不讀、不等。",
    "alert": "發生告警時，另外請 AI 推測原因。",
    "hypothesis": "AI 只提供告警原因假說，不參與決策。",
    "inbox": "收件口檢查並保存提案。",
    "queue": "提案進入待處理佇列。",
    "deadletter": "F6：多次未能開始處理，先停下等人。",
    "recheck": "執行端寫入前重新讀現況。",
    "limits": "程式重查權限、版本、單筆與總額限制。",
    "write": "通過檢查才用受限憑證寫入。",
    "platform": "Mock DSP 模擬廣告平台回覆。",
    "verify": "程式核對平台的實際結果。",
    "done": "確認一致，工作才算完成。",
    "replay": "F6：人工重放後回佇列重驗。",
    "approve": "F7：人工核可後回佇列重驗。",
}
# 每條路徑的折點在節點框之外；回頭線以虛線標示。
EDGES = (
    ("receive", "step_a", ((165, 180), (184, 180)), "main"),
    ("step_a", "platform_read", ((240, 209), (240, 561)), "read"),
    ("step_a", "filter", ((296, 180), (325, 180)), "main"),
    ("filter", "step_b", ((475, 180), (509, 180)), "main"),
    ("filter", "step_a", ((400, 151), (400, 125), (240, 125), (240, 151)), "back"),
    ("filter", "stop", ((475, 198), (485, 198), (485, 235), (770, 235),
                        (770, 275), (774, 275)), "branch"),
    ("step_b", "step_c", ((621, 180), (651, 180)), "main"),
    ("step_c", "rule", ((779, 180), (833, 180)), "main"),
    ("rule", "stop", ((885, 208), (885, 226), (830, 226), (830, 246)), "branch"),
    ("rule", "proposal", ((937, 180), (952, 180)), "main"),
    ("proposal", "submit", ((1068, 180), (1080, 180)), "main"),
    ("queue", "narrate", ((1290, 353), (1290, 304)), "note"),
    ("alert", "hypothesis", ((1500, 275), (1554, 275)), "note"),
    ("submit", "inbox", ((1130, 208), (1130, 353)), "main"),
    ("inbox", "queue", ((1180, 382), (1199, 382)), "main"),
    ("queue", "recheck", ((1255, 410), (1255, 461)), "main"),
    ("recheck", "limits", ((1305, 490), (1334, 490)), "main"),
    ("limits", "write", ((1446, 490), (1485, 490)), "main"),
    ("write", "platform", ((1535, 518), (1535, 561)), "main"),
    ("platform", "verify", ((1585, 590), (1650, 590), (1650, 519)), "main"),
    ("verify", "done", ((1700, 490), (1715, 490)), "main"),
    ("queue", "deadletter", ((1199, 398), (1185, 398), (1185, 490), (1176, 490)), "human"),
    ("deadletter", "replay", ((1120, 519), (1120, 635), (1145, 635),
                               (1145, 645)), "human"),
    ("replay", "queue", ((1209, 675), (1195, 675), (1195, 428),
                          (1211, 428), (1211, 411)), "human"),
    ("limits", "approve", ((1390, 519), (1390, 645)), "human"),
    ("approve", "queue", ((1459, 675), (1480, 675), (1480, 430),
                           (1295, 430), (1295, 411)), "human"),
)


DASHED = ("human", "note", "back", "read")  # 回頭線、旁支與只讀線畫虛線


def _edge_color(kind: str) -> str:
    if kind in ("human", "back"):
        return HUMAN
    if kind == "note":
        return COLORS["AI"][1]
    if kind == "read":
        return COLORS["平台"][1]
    return ACCENT


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _arrow(draw: ImageDraw.ImageDraw, points: tuple[tuple[int, int], ...],
           color: str, *, dashed: bool = False) -> None:
    for start, end in pairwise(points):
        if dashed:
            length = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
            for offset in range(0, length, 12):
                a, b = offset / length, min(offset + 7, length) / length
                draw.line(((start[0] + (end[0] - start[0]) * a,
                            start[1] + (end[1] - start[1]) * a),
                           (start[0] + (end[0] - start[0]) * b,
                            start[1] + (end[1] - start[1]) * b)), fill=color, width=3)
        else:
            draw.line((start, end), fill=color, width=3)
    x, y = points[-1]
    px, py = points[-2]
    head = ((x - 8, y - 5), (x - 8, y + 5)) if x > px else (
        ((x + 8, y - 5), (x + 8, y + 5)) if x < px else (
            ((x - 5, y + 8), (x + 5, y + 8)) if y < py else
            ((x - 5, y - 8), (x + 5, y - 8))))
    draw.polygon(((x, y), *head), fill=color)


def _node(draw: ImageDraw.ImageDraw, path: Path, node: Node, current: bool) -> None:
    fill, stroke, fg = COLORS[node.owner]
    box = (node.x - node.width // 2, node.y - 28,
           node.x + node.width // 2, node.y + 28)
    draw.rounded_rectangle(box, radius=11, fill=fill,
                           outline=CURRENT if current else stroke, width=4 if current else 2)
    top = node.y - 11 * (len(node.label) - 1)
    for index, line in enumerate(node.label):
        draw.text((node.x, top + index * 23), line, font=_font(path, 16),
                  fill=fg, anchor="mm")


def _frame(path: Path, key: str) -> Image.Image:
    frame = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((24, 17, 1776, 83), radius=16, fill=SURFACE, outline=BORDER)
    draw.text((900, 50), CAPTIONS[key], font=_font(path, 25), fill=TEXT, anchor="mm")
    for name, top, bottom in LANES:
        draw.rounded_rectangle((95, top, 1776, bottom), radius=10, fill=RAISED)
        draw.text((20, (top + bottom) // 2), name, font=_font(path, 18), fill=TEXT, anchor="lm")
    for first, _second, points, kind in EDGES:
        color = _edge_color(kind)
        if kind in ("main", "note") and key in MAIN and first in MAIN \
                and MAIN.index(first) >= MAIN.index(key):
            color = BORDER
        _arrow(draw, points, color, dashed=kind in DASHED)
    for node in NODES:
        _node(draw, path, node, node.key == key)
    for x, y, value in NOTES:
        draw.text((x, y), value, font=_font(path, 15), fill=MUTED, anchor="mm")
    for x, y, value in EDGE_LABELS:
        draw.text((x, y), value, font=_font(path, 13), fill=ACCENT, anchor="mm")
    draw.rounded_rectangle((95, 722, 1776, 750), radius=7, fill=SURFACE, outline=BORDER)
    for x, value, color in LEGEND:
        draw.text((x, 736), value, font=_font(path, 15), fill=color, anchor="lm")
    return frame


def _svg_node(node: Node) -> str:
    fill, stroke, fg = COLORS[node.owner]
    x, y = node.x - node.width // 2, node.y - 28
    lines = "".join(f'<text x="{node.x}" y="{node.y - 7 * (len(node.label) - 1) + i * 23}" '
                    f'text-anchor="middle">{escape(line)}</text>'
                    for i, line in enumerate(node.label))
    return (f'<rect x="{x}" y="{y}" width="{node.width}" height="56" rx="11" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
            f'<g fill="{fg}" font-size="16" font-weight="700">{lines}</g>')


def _write_svg() -> None:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="760" '
        'viewBox="0 0 1800 760" role="img" aria-labelledby="title desc" '
        'font-family="Hiragino Sans GB, sans-serif">',
        '<title id="title">RTB Agent 九條規則判斷到安全寫入</title>',
        '<desc id="desc">分析端第一步 A 唯讀現況與一小時成效，'
        '程式用這批資料初篩新鮮度、配速、暫停與異常；'
        '只有配速偏低才續讀 B 歷史與過去調整、C 逐日與一天七天並重讀現況。'
        '程式依九條規則決定是否提案，資料有問題就判證據不足，值得加才計算金額。'
        '收件收下後，AI 另外寫給人看的提案說明，執行端不讀、不等；'
        '告警時另外請 AI 推測原因；兩者都不參與決策。'
        '收件與執行端重查後才寫入模擬平台；F6 重放與 F7 核可回佇列重驗。</desc>',
        f'<rect width="1800" height="760" fill="{BG}"/>',
        f'<rect x="24" y="17" width="1752" height="66" rx="16" fill="{SURFACE}" '
        f'stroke="{BORDER}"/>',
        f'<text x="900" y="58" text-anchor="middle" fill="{TEXT}" font-size="25" '
        'font-weight="700">RTB Agent：從調查到確認寫入</text>',
    ]
    for name, top, bottom in LANES:
        parts.append(f'<rect x="95" y="{top}" width="1681" height="{bottom - top}" '
                     f'rx="10" fill="{RAISED}"/>')
        parts.append(f'<text x="20" y="{(top + bottom) // 2 + 6}" fill="{TEXT}" '
                     f'font-size="18">{name}</text>')
    for _first, _second, points, kind in EDGES:
        color = _edge_color(kind)
        dash = ' stroke-dasharray="7 5"' if kind in DASHED else ""
        route = " ".join(f"{x},{y}" for x, y in points)
        parts.append(f'<polyline points="{route}" fill="none" stroke="{color}" '
                     f'stroke-width="3"{dash} marker-end="url(#arrow-{kind})"/>')
    for kind in sorted({row[3] for row in EDGES}):
        color = _edge_color(kind)
        parts.insert(3, f'<defs><marker id="arrow-{kind}" markerWidth="3" markerHeight="3" '
                     'refX="8" refY="4" orient="auto" viewBox="0 0 8 8">'
                     f'<path d="M0 0L8 4L0 8Z" fill="{color}"/></marker></defs>')
    parts.extend(_svg_node(node) for node in NODES)
    for x, y, value in NOTES:
        parts.append(f'<text x="{x}" y="{y}" text-anchor="middle" dominant-baseline="middle" '
                     f'fill="{MUTED}" font-size="15">{escape(value)}</text>')
    for x, y, value in EDGE_LABELS:
        parts.append(f'<text x="{x}" y="{y}" text-anchor="middle" dominant-baseline="middle" '
                     f'fill="{ACCENT}" font-size="13">{escape(value)}</text>')
    parts.append(f'<rect x="95" y="722" width="1681" height="28" rx="7" fill="{SURFACE}" '
                 f'stroke="{BORDER}"/>')
    for x, value, color in LEGEND:
        parts.append(f'<text x="{x}" y="736" dominant-baseline="middle" '
                     f'fill="{color}" font-size="15">{escape(value)}</text>')
    parts.append('</svg>')
    (ROOT / "docs/assets/agent-flow.svg").write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="重畫 README 的 GIF 與 SVG")
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    path = args.font or Path(os.environ.get("RTB_FLOW_FONT", FONT))
    if not path.is_file():
        parser.error(f"找不到字型：{path}")
    try:
        _font(path, 15)
    except OSError as exc:
        parser.error(f"無法讀取字型：{exc}")
    keys = (*MAIN, "platform_read", "stop", "narrate", "alert", "hypothesis",
            "deadletter", "replay", "approve")
    frames = [_frame(path, key) for key in keys]
    sheet = Image.new("RGB", (SIZE[0], SIZE[1] * len(frames)))
    for index, frame in enumerate(frames):
        sheet.paste(frame, (0, SIZE[1] * index))
    palette = sheet.quantize(colors=64)
    indexed = [frame.quantize(palette=palette) for frame in frames]
    indexed[0].save(ROOT / "docs/assets/agent-flow.gif", save_all=True,
                    append_images=indexed[1:], duration=900, loop=0, optimize=True, disposal=2)
    _write_svg()


if __name__ == "__main__":
    main()
