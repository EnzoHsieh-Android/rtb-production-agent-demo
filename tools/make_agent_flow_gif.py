"""重畫 README 的流程圖。僅本腳本需要 Pillow 12；正式程式沒有此依賴。

執行：python3 tools/make_agent_flow_gif.py
流程對照：rtb-12-page/src/rtb/demo/flow.py；F6/F7 以本專案合約為準。
配色與線型：rtb-12-page/src/rtb/demo/static/demo.css 的 flow-node。
"""

# ruff: noqa: RUF001, RUF002 - 繁體中文畫面文字需要全形標點。
# mypy: disable-error-code=import-not-found,import-untyped,no-any-return

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
SIZE = (1200, 640)
FONT_PATH = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
BG = "#0b0d12"
SURFACE = "#11141b"
MUTED = "#a8adb9"
FAINT = "#7d8492"
TEXT = "#f4f4f5"
ACCENT = "#9aa8ff"
CURRENT = "#ffd166"
PALETTE = {
    "程式": ("#193a50", "#93ccee", "#ecf8ff"),
    "AI": ("#39254b", "#d5a4f0", "#f8eeff"),
    "人工": ("#493614", "#f1c875", "#fff6df"),
    "外部平台": ("#173a2b", "#89d0ac", "#e8fff1"),
}
LANES = ("分析", "收件", "執行", "廣告平台", "人工")
Y = {lane: 172 + 87 * i for i, lane in enumerate(LANES)}


@dataclass(frozen=True)
class Node:
    key: str
    x: int
    lane: str
    label: tuple[str, ...]
    owner: str
    kind: str = "step"


NODES = (
    Node("start", 108, "分析", ("收到", "工作"), "程式"),
    Node("collect", 184, "分析", ("蒐集", "資料"), "程式"),
    Node("fresh", 260, "分析", ("夠新", "夠齊？"), "程式", "decision"),
    Node("pace", 336, "分析", ("花得", "偏慢？"), "程式", "decision"),
    Node("worth", 412, "分析", ("值得", "加？"), "程式", "decision"),
    Node("propose", 488, "分析", ("寫好", "建議"), "程式"),
    Node("narrate", 564, "分析", ("AI 寫", "說明"), "AI"),
    Node("submit", 640, "分析", ("送出", "建議"), "程式"),
    Node("handoff", 710, "收件", ("//", "交接"), "程式"),
    Node("intake", 780, "收件", ("收件", "檢查"), "程式", "decision"),
    Node("queue", 850, "收件", ("待處理", "佇列"), "程式"),
    Node("precheck", 920, "執行", ("寫前", "重查"), "程式", "decision"),
    Node("total", 990, "執行", ("總上限", "檢查"), "程式", "decision"),
    Node("write", 1055, "執行", ("送出", "寫入"), "程式"),
    Node("platform", 1070, "廣告平台", ("平台", "回覆"), "外部平台"),
    Node("verify", 1120, "執行", ("核對", "預算"), "程式", "decision"),
    Node("done", 1175, "執行", ("完成",), "程式", "terminal"),
    Node("approval", 1018, "人工", ("人工", "核可"), "人工", "decision"),
    Node("dead", 850, "執行", ("多次", "未啟動"), "程式"),
    Node("replay", 850, "人工", ("人工", "重放"), "人工", "decision"),
)
BY_KEY = {node.key: node for node in NODES}
MAIN = tuple(node.key for node in NODES[:17])
CAPTIONS = (
    ("start", "收到工作，開始分析。"),
    ("collect", "程式讀取廣告現況與成效資料。"),
    ("fresh", "程式確認資料夠新、夠齊。"),
    ("pace", "程式檢查預算是否花得偏慢。"),
    ("worth", "程式判斷這次值不值得加預算。"),
    ("propose", "程式根據規則寫好調整建議。"),
    ("narrate", "AI 寫給確認者看的說明；只供參考，不能改預算。"),
    ("submit", "分析端把建議送出。"),
    ("handoff", "兩段程式在收件口交接，經佇列斷點保存。"),
    ("intake", "收件口檢查內容、版本與重送。"),
    ("queue", "檢查通過後，建議排隊等待執行。"),
    ("precheck", "執行端寫入前重查現況、版本與權限。"),
    ("total", "程式核對單筆限制與帳戶總上限。"),
    ("write", "檢查通過，程式帶憑證送出寫入。"),
    ("platform", "模擬廣告平台檢查並回覆寫入結果。"),
    ("verify", "程式再讀平台實際預算，確認寫入成功。"),
    ("done", "確認一致，這份工作才算完成。"),
    ("approval", "F7：超過總上限，等待人工核可。"),
    ("approval_return", "F7：核可只讓同一提案回佇列，再重查各關。"),
    ("dead", "F6：多次未能開始處理，工作停下等人。"),
    ("replay", "F6：人工逐筆重放，舊建議回佇列重驗。"),
    ("legend", "四種顏色標出處理者；亮框是目前這一步。"),
)


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def center_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
                *, fill: str, size: int, anchor: str = "mm") -> None:
    draw.text(xy, value, fill=fill, font=font(size), anchor=anchor)


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


def draw_node(draw: ImageDraw.ImageDraw, node: Node, *, state: str) -> None:
    fill, stroke, fg = PALETTE[node.owner]
    box = bounds(node)
    if node.kind == "decision":
        x1, y1, x2, y2 = box
        points = ((x1, y1 + 8), (x1 + 8, y1), (x2 - 8, y1), (x2, y1 + 8),
                  (x2, y2 - 8), (x2 - 8, y2), (x1 + 8, y2), (x1, y2 - 8))
        draw.polygon(points, fill=fill)
        draw.line((*points, points[0]), fill=CURRENT if state == "current" else stroke,
                  width=4 if state == "current" else 2, joint="curve")
    else:
        draw.rounded_rectangle(box, radius=10, fill=fill,
                               outline=CURRENT if state == "current" else stroke,
                               width=4 if state == "current" else 2)
    if state != "current":
        if node.owner == "AI":
            dashed_rect(draw, box, stroke, (6, 3))
        elif node.owner == "外部平台":
            dashed_rect(draw, box, stroke, (2, 3))
        elif node.owner == "人工":
            draw.rounded_rectangle((box[0] + 4, box[1] + 4, box[2] - 4, box[3] - 4),
                                   radius=7, outline=stroke, width=1)
    label_y = Y[node.lane] - 8 if len(node.label) == 2 else Y[node.lane]
    for line in node.label:
        center_text(draw, (node.x, label_y), line, fill=fg, size=14)
        label_y += 20
    badge = node.owner
    badge_w = 64 if badge == "外部平台" else 35 if badge == "程式" or badge == "人工" else 25
    badge_box = (box[2] - badge_w, box[1] - 14, box[2], box[1] + 2)
    draw.rounded_rectangle(badge_box, radius=4, fill=fill, outline=stroke, width=1)
    center_text(draw, ((badge_box[0] + badge_box[2]) // 2, box[1] - 6), badge,
                fill=fg, size=14)


def line_arrow(draw: ImageDraw.ImageDraw, points: tuple[tuple[int, int], ...],
               color: str, width: int = 2) -> None:
    draw.line(points, fill=color, width=width, joint="curve")
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
    if second.key == "platform":
        start = (first.x, a[3] + 2)
        end = (second.x, b[1] - 2)
        points = (start, (second.x, start[1]), end)
    elif first.key == "platform":
        start = (first.x, a[1] - 2)
        end = (second.x, b[3] + 2)
        points = (start, (first.x, end[1]), end)
    elif first.lane == second.lane:
        points = (start, end)
    else:
        points = (start, (middle, start[1]), (middle, end[1]), end)
    line_arrow(draw, points, color)


def branches(draw: ImageDraw.ImageDraw, *, stage: str) -> None:
    approval_color = CURRENT if stage in {"approval", "approval_return"} else "#85551e"
    replay_color = CURRENT if stage in {"dead", "replay"} else "#85551e"
    line_arrow(draw, ((990, 371), (990, 476), (1018, 476), (1018, 494)), approval_color)
    line_arrow(draw, ((1018, 545), (1018, 576), (850, 576), (850, 286)), approval_color)
    line_arrow(draw, ((850, 286), (850, 320)), replay_color)
    line_arrow(draw, ((850, 371), (850, 494)), replay_color)
    line_arrow(draw, ((850, 545), (850, 558), (816, 558), (816, 286)), replay_color)
    center_text(draw, (1064, 469), "F7 超額", fill=PALETTE["人工"][1], size=14)
    center_text(draw, (898, 469), "F6 重放", fill=PALETTE["人工"][1], size=14)
    center_text(draw, (731, 573), "人工同意或重放後回到佇列", fill=MUTED, size=14)


def legend(draw: ImageDraw.ImageDraw, *, final: bool) -> None:
    draw.rounded_rectangle((84, 588, 1110, 628), radius=10, fill=SURFACE,
                           outline="#424958", width=1)
    x = 105
    for owner in PALETTE:
        fill, stroke, fg = PALETTE[owner]
        w = 130 if owner == "外部平台" else 95
        draw.rounded_rectangle((x, 595, x + w, 620), radius=5, fill=fill,
                               outline=stroke, width=2)
        center_text(draw, (x + w // 2, 608), owner, fill=fg, size=14)
        x += w + 14
    if final:
        center_text(draw, (843, 608), "AI 只能寫說明與推測；改預算的永遠是程式，超過上限要人確認",
                    fill=TEXT, size=14)
    else:
        center_text(draw, (862, 608), "亮框＝目前步驟   ·   時間向右 →", fill=MUTED, size=14)


def render_frame(index: int) -> Image.Image:
    key, caption = CAPTIONS[index]
    frame = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((24, 20, 1176, 89), radius=16, fill=SURFACE,
                           outline="#424958", width=1)
    center_text(draw, (600, 51), caption, fill=TEXT, size=24)
    center_text(draw, (66, 112), "RTB AGENT  ·  時間向右 →", fill=ACCENT, size=16,
                anchor="lm")
    for lane in LANES:
        y = Y[lane]
        draw.rounded_rectangle((72, y - 34, 1189, y + 34), radius=8,
                               fill="#171a22" if lane == "人工" else SURFACE)
        center_text(draw, (20, y), lane, fill=TEXT, size=16, anchor="lm")
    for offset in range(len(MAIN) - 1):
        color = ACCENT if key == "legend" or (key in MAIN and offset < MAIN.index(key)) else FAINT
        main_edge(draw, BY_KEY[MAIN[offset]], BY_KEY[MAIN[offset + 1]], color)
    branches(draw, stage=key)
    for node in NODES:
        if key == "legend":
            state = "visited"
        elif node.key == key:
            state = "current"
        elif node.key in MAIN and (key not in MAIN or MAIN.index(node.key) < MAIN.index(key)):
            state = "visited"
        else:
            state = "future"
        draw_node(draw, node, state=state)
    center_text(draw, (565, 212), "只供參考・不能改預算", fill=PALETTE["AI"][1], size=14)
    if key == "approval_return":
        draw_node(draw, BY_KEY["approval"], state="current")
    legend(draw, final=key == "legend")
    return frame


def svg_node(node: Node) -> str:
    fill, stroke, fg = PALETTE[node.owner]
    x1, y1, x2, y2 = bounds(node)
    dash = ' stroke-dasharray="6 2"' if node.owner == "AI" else (
        ' stroke-dasharray="2 3"' if node.owner == "外部平台" else "")
    if node.kind == "decision":
        shape = (f'<path d="M{x1 + 8} {y1}H{x2 - 8}L{x2} {y1 + 8}V{y2 - 8}'
                 f'L{x2 - 8} {y2}H{x1 + 8}L{x1} {y2 - 8}V{y1 + 8}Z"/>' )
    else:
        shape = f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="50" rx="10"/>'
    label = "".join(f'<text x="{node.x}" y="{Y[node.lane] - 3 + 19 * i}" '
                    f'text-anchor="middle" font-size="14">{escape(line)}</text>'
                    for i, line in enumerate(node.label))
    badge_width = 64 if node.owner == "外部平台" else 35 if node.owner != "AI" else 25
    return (f'<g fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}>{shape}</g>'
            f'<g fill="{fg}" font-weight="700">{label}'
            f'<rect x="{x2 - badge_width}" y="{y1 - 14}" width="{badge_width}" '
            f'height="16" rx="4" fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{x2 - badge_width / 2}" y="{y1 - 2}" text-anchor="middle" '
            f'font-size="14">{escape(node.owner)}</text></g>')


def write_svg() -> None:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="640" '
        'viewBox="0 0 1200 640" role="img" aria-labelledby="title desc" '
        'font-family="Hiragino Sans GB, sans-serif">',
        '<title id="title">RTB Agent 流程靜態總覽</title>',
        '<desc id="desc">由左往右讀：分析、收件、執行、廣告平台、人工五條泳道。'
        'AI 說明只供參考，F7 超額需人工核可，F6 停下的工作由人工重放。</desc>',
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
        if second_key == "platform":
            path = f'M{a.x} {ab[3]}V{bb[1]}'
        elif first_key == "platform":
            path = f'M{a.x} {ab[1]}V{bb[3]}H{bb[0]}'
        elif a.lane == b.lane:
            path = f'M{ab[2]} {Y[a.lane]}H{bb[0]}'
        else:
            mid = (ab[2] + bb[0]) // 2
            path = f'M{ab[2]} {Y[a.lane]}H{mid}V{Y[b.lane]}H{bb[0]}'
        parts.append(f'<path d="{path}" fill="none" stroke="{ACCENT}" '
                     'stroke-width="2" marker-end="url(#arrow)"/>')
    parts.insert(2, f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
                    f'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
                    f'<path d="M0 0L10 5L0 10Z" fill="{ACCENT}"/></marker></defs>')
    parts.extend((
        '<path d="M990 371V476H1018V494M1018 545V576H850V271" fill="none" '
        f'stroke="{PALETTE["人工"][1]}" stroke-width="2" stroke-dasharray="6 4"/>',
        '<path d="M850 286V320M850 371V494M850 545V558H816V286" fill="none" '
        f'stroke="{PALETTE["人工"][1]}" stroke-width="2" stroke-dasharray="6 4"/>',
    ))
    parts.extend(svg_node(node) for node in NODES)
    parts.extend((
        f'<text x="492" y="216" fill="{PALETTE["AI"][1]}" font-size="14">'
        '只供參考・不能改預算</text>',
        f'<text x="1040" y="470" fill="{PALETTE["人工"][1]}" font-size="14">F7 超額</text>',
        f'<text x="872" y="470" fill="{PALETTE["人工"][1]}" font-size="14">F6 重放</text>',
        f'<text x="596" y="573" fill="{MUTED}" font-size="14">'
        '核可或人工重放後回待處理佇列，重新檢查</text>',
        f'<rect x="84" y="588" width="1026" height="40" rx="10" fill="{SURFACE}"/>',
    ))
    x = 105
    for owner, (fill, stroke, fg) in PALETTE.items():
        width = 130 if owner == "外部平台" else 95
        parts.append(f'<rect x="{x}" y="595" width="{width}" height="25" rx="5" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        parts.append(f'<text x="{x + width // 2}" y="613" fill="{fg}" font-size="14" '
                     f'text-anchor="middle" font-weight="700">{owner}</text>')
        x += width + 14
    parts.extend((
        f'<text x="600" y="613" fill="{TEXT}" font-size="14">'
        'AI 只能寫說明與推測；改預算的永遠是程式，超過上限要人確認</text>',
        '</svg>',
    ))
    (ASSETS / "agent-flow.svg").write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    frames = [render_frame(i) for i in range(len(CAPTIONS))]
    palette = frames[-1].quantize(colors=64)
    indexed = [frame.quantize(palette=palette) for frame in frames]
    indexed[0].save(ASSETS / "agent-flow.gif", save_all=True, append_images=indexed[1:],
                    duration=1000, loop=0, optimize=True, disposal=2)
    write_svg()


if __name__ == "__main__":
    main()
