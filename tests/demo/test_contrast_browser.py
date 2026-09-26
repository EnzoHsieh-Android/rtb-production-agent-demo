# ruff: noqa: S106
"""協調者 2026-09-25 裁定:展示頁與另存報告在系統偏好淺色與深色兩種模式下,看得到的文字都要達 WCAG AA
對比(一般文字 4.5:1,大字 3:1)。重拍截圖時發現:頁面後段把主題色重設成深色,卻漏了幾個狀態色,偏好
淺色時 F5「模型考題沒通過」那一行變成淺粉底配淺字、執行中的標示深底配深黃字。

做法:pytest 把頁面(含考題沒通過、判不提案、執行中、另存報告)寫成靜態檔,用 Python 版 Playwright 在
淺色、深色兩種偏好下打開,逐一讀每個看得到的文字的實際前景色與實際背景色(往上疊到不透明為止)算
對比;流程格的浮出框逐一打開再量。沒裝瀏覽器時跟浮出框那支同一套跳過規則(CI 一定要跑)。
"""

from dataclasses import replace

import pytest

from rtb.demo.page import DEMO_CSS, STYLESHEET_PATH, render_page, render_report
from rtb.demo.state import ScenarioCode
from tests.demo.sample_data import make_demo_state
from tests.demo.test_flow_popover_browser import browser  # noqa: F401 - 共用夾具

CONTRAST_SCRIPT = r"""() => {
  // 每個看得到的文字:實際前景色疊在實際背景色上(往上找到不透明的背景為止)算 WCAG 對比
  const parse = c => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const channel = v => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  const lum = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  const over = (top, bottom) =>
    [0, 1, 2].map(i => top[i] * top[3] + bottom[i] * (1 - top[3])).concat([1]);
  const backgroundOf = el => {
    const stack = [];
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c[3] > 0) { stack.push(c); if (c[3] >= 1) break; }
    }
    let base = parse(getComputedStyle(document.body).backgroundColor) || [255, 255, 255, 1];
    if (base[3] < 1) base = over(base, [255, 255, 255, 1]);
    let result = stack.length && stack[stack.length - 1][3] >= 1 ? stack.pop() : base;
    while (stack.length) result = over(stack.pop(), result);
    return result;
  };
  const describe = el => {
    const own = typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\s+/).join('.') : '';
    const parent = el.parentElement;
    const parentClass = parent && typeof parent.className === 'string' && parent.className
      ? '.' + parent.className.trim().split(/\s+/)[0] : '';
    return el.tagName.toLowerCase() + own + ' < ' +
      (parent ? parent.tagName.toLowerCase() + parentClass : '');
  };
  const found = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const el = node.parentElement;
    if (!node.textContent.trim() || !el || seen.has(el)) continue;
    seen.add(el);
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (style.visibility === 'hidden' || style.display === 'none' || box.width === 0 ||
        el.closest('[hidden]') || el.closest('svg')) continue;
    if (el.closest('details:not([open])') && !el.closest('summary')) continue;
    const color = parse(style.color);
    if (!color) continue;
    const background = backgroundOf(el);
    const foreground = over(color, background);
    const [hi, lo] = [lum(foreground), lum(background)].sort((x, y) => y - x);
    const ratio = (hi + 0.05) / (lo + 0.05);
    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;  // 大字 3:1,一般 4.5:1
    if (ratio < need) {
      found.push({ sel: describe(el), fg: style.color, ratio, text: node.textContent.trim(),
                   bg: 'rgb(' + background.slice(0, 3).map(Math.round).join(',') + ')' });
    }
  }
  return found;
}"""


def _inline(markup: str) -> str:
    return markup.replace(f'<link rel="stylesheet" href="{STYLESHEET_PATH}">',
                          f"<style>{DEMO_CSS}</style>")


def _pages() -> dict[str, str]:
    state = make_demo_state(running=False)
    f5 = next(s for s in state.scenarios if s.code is ScenarioCode.F5)
    # 結局標示那一行也要量到對比(Phase 14 增量 3 撤除考題那一行)
    failed = replace(f5, outcome_note="故障照預期;接續任務照九條規則第 3 條:剛被調過預算,先不動")
    marked = replace(state, scenarios=tuple(failed if s.code is ScenarioCode.F5 else s
                                            for s in state.scenarios))
    pages = {f"page-{code.value}": _inline(render_page(marked, form_token="t", refresh_tick=0,
                                                       selected=code))
             for code in ScenarioCode}
    pages["running"] = _inline(render_page(make_demo_state(running=True), form_token="t",
                                           refresh_tick=0))
    pages["report"] = render_report(marked, inline_styles=True)
    return pages


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_visible_text_meets_aa_contrast_in_both_color_schemes(
        tmp_path, browser, scheme):  # noqa: F811 - 共用夾具
    context = browser.new_context(color_scheme=scheme, viewport={"width": 1280, "height": 900})
    low: dict[tuple[str, str, str], tuple[float, str, str]] = {}
    try:
        for name, markup in _pages().items():
            path = tmp_path / f"{name}.html"
            path.write_text(markup, encoding="utf-8")
            page = context.new_page()
            page.goto(path.as_uri())
            found = page.evaluate(CONTRAST_SCRIPT)
            boxes = page.evaluate("[...document.querySelectorAll('.flow-popover')].map(b => b.id)")
            for box in boxes:
                page.evaluate("id => { document.querySelectorAll('.flow-popover')"
                              ".forEach(b => { b.hidden = b.id !== id; }); }", box)
                found += page.evaluate(CONTRAST_SCRIPT)
            for item in found:
                low.setdefault((item["sel"], item["fg"], item["bg"]),
                               (round(item["ratio"], 2), item["text"][:40], name))
            page.close()
    finally:
        context.close()
    assert not low, "\n".join(f"{ratio} {sel} {fg} on {bg} {text!r} ({name})"
                              for (sel, fg, bg), (ratio, text, name) in sorted(low.items()))
