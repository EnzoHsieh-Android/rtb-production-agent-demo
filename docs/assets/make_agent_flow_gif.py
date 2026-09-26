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
    (540, 312, "太舊回蒐集；缺資料或不慢就不提案"),
    (925, 313, "送件後供人參考，不進決策"),
    (1065, 714, "重放／核可後都回佇列，重新檢查"),
)
LEGEND = (
    (112, "程式", COLORS["程式"][1]),
    (180, "AI", COLORS["AI"][1]),
    (235, "人工／回頭線", HUMAN),
    (385, "模擬平台", COLORS["平台"][1]),
    (585, "AI 只選查詢或結論；所有寫入由程式與人工關卡決定", TEXT),
)
EDGE_LABELS = ((290, 119, "太舊"), (560, 136, "未開 AI 時"),
               (470, 222, "缺資料／不慢"), (240, 430, "只讀"))


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
    Node("collect", 240, 180, ("蒐集資料",), "程式"),
    Node("filter", 365, 180, ("新鮮齊全？", "配速偏慢？"), "程式", 120),
    Node("ai", 505, 180, ("AI 選下一步",), "AI", 118),
    Node("rule", 650, 180, ("程式規則", "接手判斷"), "程式"),
    Node("proposal", 775, 180, ("程式算金額", "寫好提案"), "程式", 112),
    Node("submit", 910, 180, ("送出提案",), "程式"),
    Node("query", 505, 275, ("選唯讀查詢", "再蒐證"), "AI", 118),
    Node("stop", 655, 275, ("不提案／", "證據不足"), "程式", 112),
    Node("narrate", 910, 275, ("AI 說明／假說", "只給人參考"), "AI", 138),
    Node("inbox", 1045, 382, ("收件檢查",), "程式"),
    Node("queue", 1170, 382, ("待處理佇列",), "程式", 112),
    Node("deadletter", 1035, 490, ("多次未啟動", "先停下"), "程式", 112),
    Node("recheck", 1170, 490, ("寫前重查",), "程式"),
    Node("limits", 1305, 490, ("權限／版本", "單筆／總額"), "程式", 112),
    Node("write", 1450, 490, ("憑證寫入",), "程式"),
    Node("verify", 1580, 490, ("核對結果",), "程式"),
    Node("done", 1700, 490, ("完成",), "程式", 78),
    Node("platform", 1450, 590, ("Mock DSP", "模擬平台"), "平台"),
    # 蒐集資料與 AI 追加查詢都向平台讀現況與指標(分析端的 DSP 用戶端);只讀、不寫
    Node("platform_read", 215, 590, ("Mock DSP", "讀現況／指標"), "平台", 112),
    Node("replay", 1060, 675, ("F6 人工重放",), "人工", 128),
    Node("approve", 1310, 675, ("F7 人工核可",), "人工", 128),
)
BY_KEY = {node.key: node for node in NODES}
MAIN = ("receive", "collect", "filter", "ai", "proposal", "submit", "inbox", "queue",
        "recheck", "limits", "write", "platform", "verify", "done")
CAPTIONS = {
    "receive": "收到一件工作，開始分析。",
    "collect": "程式向模擬平台讀最新的廣告現況與指標。",
    "platform_read": "分析端只讀平台，不寫入；寫入只在執行端。",
    "filter": "太舊重蒐集；缺資料或不慢就不提案；偏慢才進入判斷。",
    "ai": "開啟 AI 判斷時，AI 只選固定的下一步。",
    "query": "AI 可選四種唯讀查詢，查完回到蒐證。",
    "rule": "呼叫或收據核對失敗，改由程式規則決定。",
    "stop": "AI 可判不提案或證據不足，工作就停下。",
    "proposal": "若判值得加，金額、廣告與動作由程式決定。",
    "submit": "提案送往獨立收件口；說明與假說只給人參考。",
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
    ("receive", "collect", ((165, 180), (190, 180)), "main"),
    ("collect", "platform_read", ((215, 209), (215, 561)), "read"),
    ("collect", "filter", ((290, 180), (305, 180)), "main"),
    ("filter", "ai", ((425, 180), (446, 180)), "main"),
    ("filter", "collect", ((340, 152), (340, 132), (240, 132), (240, 151)), "back"),
    ("filter", "stop", ((425, 196), (435, 196), (435, 230), (580, 230),
                        (580, 275), (599, 275)), "branch"),
    ("filter", "rule", ((385, 152), (385, 122), (650, 122), (650, 151)), "branch"),
    ("ai", "rule", ((564, 180), (600, 180)), "branch"),
    ("ai", "proposal", ((505, 151), (505, 102), (775, 102), (775, 151)), "main"),
    ("ai", "query", ((505, 208), (505, 246)), "query"),
    ("query", "collect", ((446, 275), (240, 275), (240, 209)), "query"),
    ("ai", "stop", ((564, 190), (590, 190), (590, 275), (599, 275)), "branch"),
    ("rule", "proposal", ((700, 180), (719, 180)), "branch"),
    ("rule", "stop", ((650, 209), (650, 246)), "branch"),
    ("proposal", "submit", ((831, 180), (860, 180)), "main"),
    ("submit", "narrate", ((910, 208), (910, 246)), "note"),
    ("submit", "inbox", ((960, 180), (1045, 180), (1045, 353)), "main"),
    ("inbox", "queue", ((1095, 382), (1114, 382)), "main"),
    ("queue", "recheck", ((1170, 410), (1170, 461)), "main"),
    ("recheck", "limits", ((1220, 490), (1249, 490)), "main"),
    ("limits", "write", ((1361, 490), (1400, 490)), "main"),
    ("write", "platform", ((1450, 518), (1450, 561)), "main"),
    ("platform", "verify", ((1500, 590), (1580, 590), (1580, 519)), "main"),
    ("verify", "done", ((1630, 490), (1661, 490)), "main"),
    ("queue", "deadletter", ((1114, 398), (1100, 398), (1100, 490), (1091, 490)), "human"),
    ("deadletter", "replay", ((1035, 519), (1035, 635), (1060, 635),
                               (1060, 645)), "human"),
    ("replay", "queue", ((1124, 675), (1110, 675), (1110, 428), (1126, 428), (1126, 411)), "human"),
    ("limits", "approve", ((1305, 519), (1305, 645)), "human"),
    ("approve", "queue", ((1374, 675), (1395, 675), (1395, 430),
                          (1210, 430), (1210, 411)), "human"),
)


DASHED = ("human", "query", "note", "back", "read")  # 回頭線、旁支與只讀線畫虛線


def _edge_color(kind: str) -> str:
    if kind in ("human", "back"):
        return HUMAN
    if kind in ("query", "note"):
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
        if kind == "main" and key in MAIN and first in MAIN \
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
        '<title id="title">RTB Agent 從 AI 調查到安全寫入</title>',
        '<desc id="desc">分析端向模擬平台唯讀讀取廣告現況與指標；'
        '資料太舊會重蒐集，缺資料或配速不慢會不提案；'
        '配速偏慢時，未開 AI 才交程式規則。AI 可選唯讀查詢或下結論，'
        'AI 失敗時改由程式規則判斷。程式計算提案，收件與執行端重查後才寫入模擬平台。'
        '說明與假說只給人參考；F6 重放與 F7 核可回到佇列重驗。</desc>',
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
    keys = (*MAIN, "platform_read", "query", "rule", "stop", "deadletter", "replay", "approve")
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
