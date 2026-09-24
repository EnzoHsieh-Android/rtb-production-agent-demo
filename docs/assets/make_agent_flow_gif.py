"""README 的 GIF/SVG 產出物工具，不是專案工具，也不參與正式流程。

使用系統 Python 與 Pillow 12 重畫；Pillow 不列入專案開發依賴，以維持零依賴家規。
執行：python3 docs/assets/make_agent_flow_gif.py [--font 字型檔]
字型可用 --font 或 RTB_FLOW_FONT 指定；預設才使用 macOS Hiragino Sans GB。
找不到字型會直接報錯退出。
流程對照：main 上的 src/rtb/demo/flow.py；F6/F7 以本專案合約為準。
配色與線型對照：展示頁上主線後的 src/rtb/demo/static/demo.css。
"""

# ruff: noqa: RUF001, RUF002 - 繁體中文畫面文字需要全形標點。
from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass
from html import escape
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "docs" / "assets"
SIZE = (1200, 640)
DEFAULT_FONT = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
BG = "#0b0d12"
SURFACE = "#11141b"
MUTED = "#a8adb9"
FAINT = "#7d8492"
PLANNED = ("#282c35", "#8c929f", "#e1e4e9")
TEXT = "#f4f4f5"
ACCENT = "#9aa8ff"
CURRENT = "#52e0ff"
RETURN_DASH = (6, 4)
PLANNED_DASH = (6, 2)
PALETTE = {
    "程式": ("#193a50", "#93ccee", "#ecf8ff"),
    "AI": ("#39254b", "#d5a4f0", "#f8eeff"),
    "人工": ("#493614", "#f1c875", "#fff6df"),
    "外部平台": ("#173a2b", "#89d0ac", "#e8fff1"),
}
LANES = ("分析", "收件", "執行", "廣告平台", "人工")
Y = {lane: 172 + 87 * i for i, lane in enumerate(LANES)}
APPROVAL_TO_HUMAN = ((990, 371), (990, 475), (966, 475), (966, 520), (985, 520))
APPROVAL_RETURN = ((1018, 545), (1018, 575), (735, 575), (735, 310),
                   (850, 310), (850, 286))
QUEUE_TO_DEADLETTER = ((850, 286), (850, 299), (790, 299), (790, 346), (817, 346))
DEADLETTER_TO_REPLAY = ((850, 371), (850, 460), (790, 460), (790, 520),
                        (817, 520))
REPLAY_RETURN = ((850, 545), (850, 558), (800, 558), (800, 286), (817, 286))
HUMAN_ROUTES = (APPROVAL_TO_HUMAN, APPROVAL_RETURN, QUEUE_TO_DEADLETTER,
                DEADLETTER_TO_REPLAY, REPLAY_RETURN)


@dataclass(frozen=True)
class Node:
    key: str
    x: int
    lane: str
    label: tuple[str, ...]
    owner: str
    kind: str = "step"


NODES = (
    Node("a_receive", 108, "分析", ("收到", "工作"), "程式"),
    Node("a_collect", 184, "分析", ("蒐集", "資料"), "程式"),
    Node("a_fresh", 260, "分析", ("夠新", "夠齊？"), "程式", "decision"),
    Node("a_pacing", 336, "分析", ("花得", "偏慢？"), "程式", "decision"),
    Node("a_worth", 412, "分析", ("值得", "加？"), "程式", "decision"),
    Node("a_propose", 488, "分析", ("寫好", "建議"), "程式"),
    Node("a_submit", 640, "分析", ("送出", "建議"), "程式"),
    Node("a_narrate", 725, "分析", ("AI 說明", "未接入"), "AI", "planned"),
    Node("i_check", 780, "收件", ("收件", "檢查"), "程式", "decision"),
    Node("x_pending", 850, "收件", ("待處理", "佇列"), "程式"),
    Node("x_precheck", 920, "執行", ("寫前", "重查"), "程式", "decision"),
    Node("x_total", 990, "執行", ("總上限", "檢查"), "程式", "decision"),
    Node("x_write", 1055, "執行", ("送出", "寫入"), "程式"),
    Node("p_reply", 1070, "廣告平台", ("平台", "回覆"), "外部平台"),
    Node("x_verify", 1120, "執行", ("核對", "預算"), "程式", "decision"),
    Node("x_done", 1175, "執行", ("完成",), "程式", "terminal"),
    Node("h_approve", 1018, "人工", ("人工", "核可"), "人工", "decision"),
    Node("x_deadletter", 850, "執行", ("多次", "未啟動"), "程式"),
    Node("h_replay", 850, "人工", ("人工", "重放"), "人工", "decision"),
)
BY_KEY = {node.key: node for node in NODES}
MAIN = tuple(node.key for node in NODES[:16] if node.kind != "planned")
CAPTIONS = (
    ("a_receive", "收到工作，開始分析。"),
    ("a_collect", "程式讀取廣告現況與成效資料。"),
    ("a_fresh", "程式確認資料夠新、夠齊。"),
    ("a_pacing", "程式檢查預算是否花得偏慢。"),
    ("a_worth", "程式判斷這次值不值得加預算。"),
    ("a_propose", "程式根據規則寫好調整建議。"),
    ("a_submit", "分析端直接送出建議；AI 說明尚未接入。"),
    ("i_check", "收件口檢查內容、版本與重送。"),
    ("x_pending", "檢查通過後，建議排隊等待執行。"),
    ("x_precheck", "執行端寫入前重查現況、版本與權限。"),
    ("x_total", "程式核對單筆限制與帳戶總上限。"),
    ("x_write", "檢查通過，程式帶憑證送出寫入。"),
    ("p_reply", "模擬廣告平台檢查並回覆寫入結果。"),
    ("x_verify", "程式再讀平台實際預算，確認寫入成功。"),
    ("x_done", "確認一致，這份工作才算完成。"),
    ("h_approve", "F7：超過總上限，等待人工核可。"),
    ("approval_return", "F7：核可只讓同一提案回佇列，再重查各關。"),
    ("x_deadletter", "F6：多次未能開始處理，工作停下等人。"),
    ("h_replay", "F6：人工逐筆重放，舊建議回佇列重驗。"),
    ("legend", "三種顏色標出現有處理者；灰色 AI 說明仍在規劃。"),
)


def font(size: int, font_path: Path) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path), size)


def center_text(draw: ImageDraw.ImageDraw, font_path: Path, xy: tuple[int, int],  # noqa: PLR0913
                value: str, *, fill: str, size: int, anchor: str = "mm") -> None:
    draw.text(xy, value, fill=fill, font=font(size, font_path), anchor=anchor)


def dashed_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                color: str, pattern: tuple[int, int], width: int = 2) -> None:
    left, top, right, bottom = box
    on, off = pattern
    for x in range(left, right, on + off):
        draw.line((x, top, min(x + on, right), top), fill=color, width=width)
        draw.line((x, bottom, min(x + on, right), bottom), fill=color, width=width)
    for y in range(top, bottom, on + off):
        draw.line((left, y, left, min(y + on, bottom)), fill=color, width=width)
        draw.line((right, y, right, min(y + on, bottom)), fill=color, width=width)


def bounds(node: Node) -> tuple[int, int, int, int]:
    half = 22 if node.kind == "terminal" else 31
    return (node.x - half, Y[node.lane] - 25, node.x + half, Y[node.lane] + 25)


def draw_node(draw: ImageDraw.ImageDraw, font_path: Path, node: Node, *, state: str) -> None:
    fill, stroke, fg = PLANNED if node.kind == "planned" else PALETTE[node.owner]
    box = bounds(node)
    if node.kind == "decision":
        x1, y1, x2, y2 = box
        points = ((x1, y1 + 8), (x1 + 8, y1), (x2 - 8, y1), (x2, y1 + 8),
                  (x2, y2 - 8), (x2 - 8, y2), (x1 + 8, y2), (x1, y2 - 8))
        draw.polygon(points, fill=fill)
        draw.line((*points, points[0]), fill=CURRENT if state == "current" else stroke,
                  width=4 if state == "current" else 3 if node.owner == "人工" else 2,
                  joint="curve")
    else:
        draw.rounded_rectangle(box, radius=10, fill=fill,
                               outline=CURRENT if state == "current" else stroke,
                               width=4 if state == "current" else 3 if node.owner == "人工" else 2)
    if state != "current":
        if node.kind == "planned":
            dashed_rect(draw, box, stroke, PLANNED_DASH)
        elif node.owner == "外部平台":
            dashed_rect(draw, box, stroke, (2, 3))
    label_y = Y[node.lane] - 8 if len(node.label) == 2 else Y[node.lane]
    for line in node.label:
        center_text(draw, font_path, (node.x, label_y), line, fill=fg, size=14)
        label_y += 20
    badge = "11B 增量 2" if node.kind == "planned" else node.owner
    badge_w = 76 if node.kind == "planned" else 64 if badge == "外部平台" else 35
    badge_top = box[1] - (28 if node.kind == "planned" else 14)
    badge_left = box[2] + 4 if node.owner == "外部平台" else box[2] - badge_w
    badge_box = (badge_left, badge_top, badge_left + badge_w, badge_top + 16)
    draw.rounded_rectangle(badge_box, radius=4, fill=fill, outline=stroke, width=1)
    center_text(draw, font_path, ((badge_box[0] + badge_box[2]) // 2, badge_top + 8), badge,
                fill=fg, size=14)


def line_arrow(draw: ImageDraw.ImageDraw, points: tuple[tuple[int, int], ...],
               color: str, width: int = 2, dash: tuple[int, int] | None = None) -> None:
    if dash is None:
        draw.line(points, fill=color, width=width, joint="curve")
    else:
        on, off = dash
        for (x1, y1), (x2, y2) in pairwise(points):
            length = math.hypot(x2 - x1, y2 - y1)
            if not length:
                continue
            for offset in range(0, math.ceil(length), on + off):
                finish = min(offset + on, length)
                draw.line(((x1 + (x2 - x1) * offset / length,
                            y1 + (y2 - y1) * offset / length),
                           (x1 + (x2 - x1) * finish / length,
                            y1 + (y2 - y1) * finish / length)), fill=color, width=width)
    x, y = points[-1]
    px, py = points[-2]
    if x > px:
        head = ((x, y), (x - 7, y - 4), (x - 7, y + 4))
    elif x < px:
        head = ((x, y), (x + 7, y - 4), (x + 7, y + 4))
    elif y < py:
        head = ((x, y), (x - 4, y + 7), (x + 4, y + 7))
    else:
        head = ((x, y), (x - 4, y - 7), (x + 4, y - 7))
    draw.polygon(head, fill=color)


def main_edge(draw: ImageDraw.ImageDraw, first: Node, second: Node, color: str) -> None:
    a = bounds(first)
    b = bounds(second)
    start = (a[2] + 1, (a[1] + a[3]) // 2)
    end = (b[0] - 2, (b[1] + b[3]) // 2)
    middle = (start[0] + end[0]) // 2
    if second.key == "p_reply":
        start = (first.x, a[3] + 2)
        end = (first.x, b[1] - 3)
        points = (start, end)
    elif first.key == "p_reply":
        start = (a[2] + 1, Y[first.lane])
        end = (second.x, b[3] + 2)
        points = (start, (1104, start[1]), (1104, 380), (second.x, 380), end)
    elif first.key == "a_submit":
        start = (first.x, a[3] + 2)
        end = (b[0] - 2, Y[second.lane])
        points = (start, (first.x, end[1]), end)
    elif first.lane == second.lane:
        points = (start, end)
    else:
        points = (start, (middle, start[1]), (middle, end[1]), end)
    line_arrow(draw, points, color)


def branches(draw: ImageDraw.ImageDraw, font_path: Path, *, stage: str) -> None:
    approval_color = CURRENT if stage in {"h_approve", "approval_return"} else PALETTE["人工"][1]
    replay_color = CURRENT if stage in {"x_deadletter", "h_replay"} else PALETTE["人工"][1]
    for route in HUMAN_ROUTES[:2]:
        line_arrow(draw, route, approval_color, dash=RETURN_DASH)
    for route in HUMAN_ROUTES[2:]:
        line_arrow(draw, route, replay_color, dash=RETURN_DASH)
    center_text(draw, font_path, (1064, 469), "F7 超額", fill=PALETTE["人工"][1], size=14)
    center_text(draw, font_path, (898, 469), "F6 重放", fill=PALETTE["人工"][1], size=14)
    center_text(draw, font_path, (410, 565), "人工同意或重放後回佇列，重新檢查",
                fill=MUTED, size=14)


def legend(draw: ImageDraw.ImageDraw, font_path: Path, *, final: bool) -> None:
    draw.rounded_rectangle((84, 588, 1110, 628), radius=10, fill=SURFACE,
                           outline="#424958", width=1)
    x = 105
    for owner in PALETTE:
        fill, stroke, fg = PLANNED if owner == "AI" else PALETTE[owner]
        w = 130 if owner == "外部平台" else 95
        draw.rounded_rectangle((x, 595, x + w, 620), radius=5, fill=fill,
                               outline=stroke, width=2)
        center_text(draw, font_path, (x + w // 2, 608), "AI（規劃）" if owner == "AI" else owner,
                    fill=fg, size=14)
        x += w + 14
    if final:
        center_text(draw, font_path, (843, 608),
                    "AI 說明未接入；改預算由程式把關，超過上限要人確認", fill=TEXT, size=14)
    else:
        center_text(draw, font_path, (862, 608), "亮框＝目前步驟   ·   時間向右 →",
                    fill=MUTED, size=14)


def draw_main_edges(draw: ImageDraw.ImageDraw, key: str) -> None:
    for offset in range(len(MAIN) - 1):
        color = ACCENT if key == "legend" or (key in MAIN and offset < MAIN.index(key)) else FAINT
        first, second = MAIN[offset], MAIN[offset + 1]
        if (first, second) == ("a_propose", "a_submit"):
            line_arrow(draw, ((488, 199), (488, 218), (640, 218), (640, 199)), color)
        else:
            main_edge(draw, BY_KEY[first], BY_KEY[second], color)


def render_frame(index: int, font_path: Path) -> Image.Image:
    key, caption = CAPTIONS[index]
    frame = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((24, 20, 1176, 89), radius=16, fill=SURFACE,
                           outline="#424958", width=1)
    center_text(draw, font_path, (600, 51), caption, fill=TEXT, size=24)
    center_text(draw, font_path, (66, 112), "RTB AGENT  ·  時間向右 →", fill=ACCENT, size=16,
                anchor="lm")
    for lane in LANES:
        y = Y[lane]
        draw.rounded_rectangle((72, y - 34, 1189, y + 34), radius=8,
                               fill="#171a22" if lane == "人工" else SURFACE)
        center_text(draw, font_path, (20, y), lane, fill=TEXT, size=16, anchor="lm")
    draw_main_edges(draw, key)
    line_arrow(draw, ((672, 172), (692, 172)), PLANNED[1], dash=PLANNED_DASH)
    branches(draw, font_path, stage=key)
    for node in NODES:
        if key == "legend":
            state = "visited"
        elif node.key == key:
            state = "current"
        elif node.key in MAIN and (key not in MAIN or MAIN.index(node.key) < MAIN.index(key)):
            state = "visited"
        else:
            state = "future"
        draw_node(draw, font_path, node, state=state)
    center_text(draw, font_path, (565, 208), "直接送出", fill=TEXT, size=13)
    center_text(draw, font_path, (800, 218), "送件後、給確認者參考（規劃中）",
                fill=PLANNED[2], size=12)
    if key == "approval_return":
        draw_node(draw, font_path, BY_KEY["h_approve"], state="current")
    legend(draw, font_path, final=key == "legend")
    return frame


def svg_node(node: Node) -> str:
    fill, stroke, fg = PLANNED if node.kind == "planned" else PALETTE[node.owner]
    x1, y1, x2, y2 = bounds(node)
    dash = (f' stroke-dasharray="{PLANNED_DASH[0]} {PLANNED_DASH[1]}"'
            if node.kind == "planned" else
            ' stroke-dasharray="2 3"' if node.owner == "外部平台" else "")
    if node.kind == "decision":
        shape = (f'<path d="M{x1 + 8} {y1}H{x2 - 8}L{x2} {y1 + 8}V{y2 - 8}'
                 f'L{x2 - 8} {y2}H{x1 + 8}L{x1} {y2 - 8}V{y1 + 8}Z"/>' )
    else:
        shape = f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="50" rx="10"/>'
    label = "".join(f'<text x="{node.x}" y="{Y[node.lane] - 3 + 19 * i}" '
                    f'text-anchor="middle" font-size="14">{escape(line)}</text>'
                    for i, line in enumerate(node.label))
    badge = "11B 增量 2" if node.kind == "planned" else node.owner
    badge_width = 76 if node.kind == "planned" else 64 if node.owner == "外部平台" else 35
    badge_top = y1 - (28 if node.kind == "planned" else 14)
    badge_left = x2 + 4 if node.owner == "外部平台" else x2 - badge_width
    width = 3 if node.owner == "人工" else 2
    return (f'<g fill="{fill}" stroke="{stroke}" stroke-width="{width}"{dash}>{shape}</g>'
            f'<g fill="{fg}" font-weight="700">{label}'
            f'<rect x="{badge_left}" y="{badge_top}" width="{badge_width}" '
            f'height="16" rx="4" fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{badge_left + badge_width / 2}" y="{badge_top + 12}" text-anchor="middle" '
            f'font-size="14">{escape(badge)}</text></g>')


def svg_path(points: tuple[tuple[int, int], ...]) -> str:
    return "M" + "L".join(f"{x} {y}" for x, y in points)


def write_svg() -> None:  # noqa: PLR0915 - SVG 組件逐段加入, 對照圖面較容易。
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="640" '
        'viewBox="0 0 1200 640" role="img" aria-labelledby="title desc" '
        'font-family="Hiragino Sans GB, sans-serif">',
        '<title id="title">RTB Agent 流程靜態總覽</title>',
        '<desc id="desc">泳道由上到下為分析、收件、執行、廣告平台、人工；時間向右。'
        '主線由寫好建議直接送出；AI 說明規劃於送件後另行產生，給確認者參考。'
        '圖只畫 F7 核可與 F6 重放兩條人工回頭線。</desc>',
        f'<rect width="1200" height="640" fill="{BG}"/>',
        f'<rect x="24" y="20" width="1152" height="69" rx="16" fill="{SURFACE}"/>',
        f'<text x="600" y="64" fill="{TEXT}" font-size="24" text-anchor="middle" '
        'font-weight="700">RTB Agent：從分析到確認寫入</text>',
        f'<text x="66" y="117" fill="{ACCENT}" font-size="16">時間向右 →</text>',
    ]
    for lane in LANES:
        y = Y[lane]
        parts.extend((
            f'<rect x="72" y="{y - 34}" width="1117" height="68" rx="8" '
            f'fill="{SURFACE}"/>',
            f'<text x="20" y="{y + 6}" fill="{TEXT}" font-size="16">{lane}</text>',
        ))
    for first_key, second_key in pairwise(MAIN):
        a, b = BY_KEY[first_key], BY_KEY[second_key]
        ab, bb = bounds(a), bounds(b)
        if (first_key, second_key) == ("a_propose", "a_submit"):
            path = 'M488 199V218H640V199'
        elif first_key == "a_submit":
            path = f'M{a.x} {ab[3] + 2}V{Y[b.lane]}H{bb[0] - 2}'
        elif second_key == "p_reply":
            path = f'M{a.x} {ab[3] + 2}V{bb[1] - 3}'
        elif first_key == "p_reply":
            path = f'M{ab[2] + 1} {Y[a.lane]}H1104V380H{b.x}V{bb[3] + 2}'
        elif a.lane == b.lane:
            path = f'M{ab[2]} {Y[a.lane]}H{bb[0]}'
        else:
            mid = (ab[2] + bb[0]) // 2
            path = f'M{ab[2]} {Y[a.lane]}H{mid}V{Y[b.lane]}H{bb[0]}'
        parts.append(f'<path d="{path}" fill="none" stroke="{ACCENT}" '
                     'stroke-width="2" marker-end="url(#arrow)"/>')
    parts.insert(2, f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
                    f'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
                    f'<path d="M0 0L10 5L0 10Z" fill="{ACCENT}"/></marker>'
                    f'<marker id="human-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
                    f'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
                    f'<path d="M0 0L10 5L0 10Z" fill="{PALETTE["人工"][1]}"/>'
                    '</marker></defs>')
    parts.append(f'<path d="M672 172H692" fill="none" stroke="{PLANNED[1]}" '
                 f'stroke-width="2" stroke-dasharray="{PLANNED_DASH[0]} '
                 f'{PLANNED_DASH[1]}"/>')
    parts.extend(
        f'<path d="{svg_path(route)}" fill="none" stroke="{PALETTE["人工"][1]}" '
        f'stroke-width="2" stroke-dasharray="{RETURN_DASH[0]} {RETURN_DASH[1]}" '
        'marker-end="url(#human-arrow)"/>'
        for route in HUMAN_ROUTES
    )
    parts.extend(svg_node(node) for node in NODES)
    parts.extend((
        f'<text x="565" y="212" fill="{TEXT}" font-size="13" '
        'text-anchor="middle">直接送出</text>',
        f'<text x="800" y="222" fill="{PLANNED[2]}" font-size="12" '
        'text-anchor="middle">送件後、給確認者參考（規劃中）</text>',
        f'<text x="1040" y="470" fill="{PALETTE["人工"][1]}" font-size="14">F7 超額</text>',
        f'<text x="872" y="470" fill="{PALETTE["人工"][1]}" font-size="14">F6 重放</text>',
        f'<text x="190" y="566" fill="{MUTED}" font-size="14">'
        '人工同意或重放後回佇列，重新檢查</text>',
        f'<rect x="84" y="588" width="1026" height="40" rx="10" fill="{SURFACE}"/>',
    ))
    x = 105
    for owner in PALETTE:
        fill, stroke, fg = PLANNED if owner == "AI" else PALETTE[owner]
        width = 130 if owner == "外部平台" else 95
        parts.append(f'<rect x="{x}" y="595" width="{width}" height="25" rx="5" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        parts.append(f'<text x="{x + width // 2}" y="613" fill="{fg}" font-size="14" '
                     f'text-anchor="middle" font-weight="700">'
                     f'{"AI（規劃）" if owner == "AI" else owner}</text>')
        x += width + 14
    parts.extend((
        f'<text x="600" y="613" fill="{TEXT}" font-size="14">'
        'AI 說明未接入；改預算由程式把關，超過上限要人確認</text>',
        '</svg>',
    ))
    (ASSETS / "agent-flow.svg").write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="重畫 README 的 GIF 與 SVG 產物")
    parser.add_argument("--font", type=Path, help="可讀取的 TrueType 或 OpenType 字型檔")
    args = parser.parse_args()
    font_choice = args.font or os.environ.get("RTB_FLOW_FONT")
    font_path = Path(font_choice) if font_choice else DEFAULT_FONT
    if not font_path.is_file():
        parser.error(f"找不到字型：{font_path}（可用 --font 或 RTB_FLOW_FONT 指定）")
    try:
        font(14, font_path)
    except OSError as exc:
        parser.error(f"無法讀取字型：{font_path}（{exc}）")
    ASSETS.mkdir(parents=True, exist_ok=True)
    frames = [render_frame(i, font_path) for i in range(len(CAPTIONS))]
    palette = frames[-1].quantize(colors=64)
    indexed = [frame.quantize(palette=palette) for frame in frames]
    indexed[0].save(ASSETS / "agent-flow.gif", save_all=True, append_images=indexed[1:],
                    duration=1000, loop=0, optimize=True, disposal=2)
    write_svg()


if __name__ == "__main__":
    main()
