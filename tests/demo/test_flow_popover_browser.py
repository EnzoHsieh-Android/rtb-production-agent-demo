# ruff: noqa: S106
"""增量 4 代碼審 r2 p2/p3/p5/s3:在真的瀏覽器裡驗流程圖浮出框的腳本行為。

pytest 把頁面與另存報告寫成靜態 HTML 檔,用 node 的 Playwright 以 file:// 打開(不起 rtb 伺服器、
不裝新套件):
- 鍵盤:Tab 到格子、Enter 釘住後焦點進框,↑↓/PageDown/Space 捲框不捲整頁、不收框,框內 Enter 不開關
  格子,Esc 收框並把焦點還給格子。
- 滑鼠:點兩下取消釘住後(焦點還留在格子),再 hover 移開照延遲收框。
- 窄螢幕:情境列載入時把選中那張卡捲進可視區。
- 另存報告帶 meta CSP 仍跑得動腳本、樣式也套得上,沒有違規訊息。

本機要有 node 與 Playwright 的 node 模組(npx 快取或 RTB_PLAYWRIGHT_NODE_PATH 指定的 node_modules)
和對得上版本的 chromium;找不到就跳過並寫明原因(CI 沒裝,照例跳過,行為的靜態那一半由 test_page 守)。
"""

import glob
import json
import os
import pwd
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from rtb.demo.page import DEMO_CSS, STYLESHEET_PATH, render_page, render_report
from rtb.demo.state import ScenarioCode
from tests.demo.sample_data import make_demo_state

# 測試裡 HOME 與 PATH 都換成暫存目錄;找工具看帳號資料庫的家目錄(只讀,不碰 ~/.rtb)
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def _node() -> str | None:
    for candidate in (os.environ.get("RTB_NODE"),
                      os.path.join(os.environ.get("NVM_BIN", ""), "node"),
                      shutil.which("node"),
                      *sorted(glob.glob(str(_REAL_HOME / ".nvm/versions/node/*/bin/node")),
                              reverse=True)):
        if candidate and os.access(candidate, os.X_OK) and Path(candidate).is_file():
            return candidate
    return None


def _browsers() -> Path:
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        return Path(configured)
    mac = _REAL_HOME / "Library" / "Caches" / "ms-playwright"
    return mac if mac.is_dir() else _REAL_HOME / ".cache" / "ms-playwright"


def _playwright_modules() -> Path | None:
    """找一份 chromium 版本對得上本機瀏覽器快取的 Playwright node 模組。"""
    configured = os.environ.get("RTB_PLAYWRIGHT_NODE_PATH")
    candidates = ([Path(configured)] if configured else
                  sorted(_REAL_HOME.glob(".npm/_npx/*/node_modules")))
    for modules in candidates:
        manifest = modules / "playwright-core" / "browsers.json"
        if not (modules / "playwright").is_dir() or not manifest.is_file():
            continue
        browsers = json.loads(manifest.read_text(encoding="utf-8"))["browsers"]
        revision = next((b["revision"] for b in browsers if b["name"] == "chromium"), None)
        if revision and any((_browsers() / f"{name}-{revision}").is_dir()
                            for name in ("chromium_headless_shell", "chromium")):
            return modules
    return None


SCRIPT = r"""
const fs = require('fs');
const { chromium } = require('playwright');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
// 鍵盤捲動有平滑動畫:按完鍵等它停下再量
const settle = page => page.waitForTimeout(600);
const snapshot = (page, id) => page.evaluate(boxId => {
  const box = document.getElementById(boxId);
  const active = document.activeElement;
  return { hidden: box.hidden, scrollTop: box.scrollTop, scrollHeight: box.scrollHeight,
           clientHeight: box.clientHeight, focusInBox: box.contains(active),
           focusOnNode: !!active && active.dataset && active.dataset.flowPopover === boxId,
           scrollY: window.scrollY, position: getComputedStyle(box).position };
}, id);
(async () => {
  const browser = await chromium.launch();
  const out = {};
  try {
    const wide = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await wide.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(cfg.page);
    const node = page.locator(`.flow-node[data-flow-popover="${cfg.box}"]`);
    await node.scrollIntoViewIfNeeded();  // 真的按 Tab 時瀏覽器會把格子捲進畫面
    await node.focus();
    out.afterFocus = await snapshot(page, cfg.box);
    await page.keyboard.press('Enter');
    out.afterEnter = await snapshot(page, cfg.box);
    for (let i = 0; i < 3; i += 1) await page.keyboard.press('ArrowDown');
    await settle(page);
    out.afterArrows = await snapshot(page, cfg.box);
    await page.keyboard.press('PageDown');
    await settle(page);
    out.afterPageDown = await snapshot(page, cfg.box);
    await page.keyboard.press('Space');
    await settle(page);
    out.afterSpace = await snapshot(page, cfg.box);
    await page.keyboard.press('Enter');
    out.afterEnterInBox = await snapshot(page, cfg.box);
    await page.keyboard.press('Escape');
    out.afterEscape = await snapshot(page, cfg.box);
    await node.click();
    out.afterClick = await snapshot(page, cfg.box);
    await node.click();
    out.afterSecondClick = await snapshot(page, cfg.box);
    await page.mouse.move(5, 5);
    await node.hover();
    out.afterHover = await snapshot(page, cfg.box);
    await page.mouse.move(5, 5);
    await page.waitForTimeout(1000);
    out.afterLeave = await snapshot(page, cfg.box);
    out.pageErrors = errors;

    const narrow = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const strip = await narrow.newPage();
    await strip.goto(cfg.narrow);
    out.narrow = await strip.evaluate(() => {
      const list = document.querySelector('.scenario-index');
      const row = list.querySelector('.scenario-row.is-selected');
      const l = list.getBoundingClientRect();
      const r = row.getBoundingClientRect();
      return { scrollLeft: list.scrollLeft, overflow: list.scrollWidth > list.clientWidth,
               visible: r.left >= l.left - 1 && r.right <= l.right + 1, scrollY: window.scrollY };
    });

    const report = await wide.newPage();
    const violations = [];
    report.on('console', m => {
      if (/Content.Security.Policy/i.test(m.text())) violations.push(m.text());
    });
    report.on('pageerror', e => violations.push(String(e)));
    await report.goto(cfg.report);
    const first = report.locator('.flow-node[data-flow-popover]').first();
    const reportBox = await first.getAttribute('data-flow-popover');
    await first.hover();
    out.report = await snapshot(report, reportBox);
    out.reportViolations = violations;
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})().catch(error => { console.error(error); process.exit(1); });
"""


def _inline(markup: str) -> str:
    """互動頁連同源樣式表;file:// 打開時把樣式內嵌進來(只為了讓版面在瀏覽器裡成立)。"""
    return markup.replace(f'<link rel="stylesheet" href="{STYLESHEET_PATH}">',
                          f"<style>{DEMO_CSS}</style>")


def test_flow_popover_keyboard_hover_strip_and_saved_report_in_a_browser(  # noqa: PLR0915
        tmp_path):
    node, modules = _node(), _playwright_modules()
    if node is None:
        pytest.skip("找不到 node(可用 RTB_NODE 指定):瀏覽器行為這支跳過,靜態那一半由 test_page 守")
    if modules is None:
        pytest.skip("找不到版本對得上本機 chromium 的 Playwright node 模組(可用 "
                    "RTB_PLAYWRIGHT_NODE_PATH 指定 node_modules):跳過")
    state = make_demo_state(running=False)
    first = state.scenarios[0]
    long_path = tuple(replace(first.path[0], task_id="t1") for _ in range(30))
    tall = replace(first, path=(*long_path, *first.path[1:]))
    tall_state = replace(state, scenarios=(tall, *state.scenarios[1:]))
    page_file = tmp_path / "page.html"
    page_file.write_text(_inline(render_page(tall_state, form_token="t", refresh_tick=0,
                                             selected=ScenarioCode.F1)), encoding="utf-8")
    narrow_file = tmp_path / "narrow.html"
    narrow_file.write_text(_inline(render_page(state, form_token="t", refresh_tick=0,
                                               selected=ScenarioCode.F7)), encoding="utf-8")
    report_file = tmp_path / "report.html"
    report_file.write_text(render_report(state, inline_styles=True), encoding="utf-8")
    markup = page_file.read_text(encoding="utf-8")
    box = markup.split('data-flow-popover="', 1)[1].split('"', 1)[0]
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"page": page_file.as_uri(), "narrow": narrow_file.as_uri(),
                                  "report": report_file.as_uri(), "box": box}), encoding="utf-8")
    script = tmp_path / "popover.js"
    script.write_text(SCRIPT, encoding="utf-8")
    env = {**os.environ, "NODE_PATH": str(modules),
           "PLAYWRIGHT_BROWSERS_PATH": str(_browsers())}
    done = subprocess.run([node, str(script), str(config)], env=env, capture_output=True,
                          text=True, timeout=180, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    seen = json.loads(done.stdout)

    # 鍵盤(p2):框比可視區高,焦點進框後方向鍵、翻頁、空白鍵捲框不捲整頁,框內按鍵不開關格子
    assert seen["afterFocus"]["hidden"] is False
    enter = seen["afterEnter"]
    assert enter["hidden"] is False and enter["focusInBox"] is True
    assert enter["scrollHeight"] > enter["clientHeight"] + 200
    assert seen["afterArrows"]["scrollTop"] > 0
    assert seen["afterArrows"]["scrollY"] == enter["scrollY"]
    assert seen["afterPageDown"]["scrollTop"] > seen["afterArrows"]["scrollTop"]
    assert seen["afterSpace"]["scrollTop"] > seen["afterPageDown"]["scrollTop"]
    assert seen["afterSpace"]["hidden"] is False
    assert seen["afterSpace"]["scrollY"] == enter["scrollY"]
    assert seen["afterEnterInBox"]["hidden"] is False
    assert seen["afterEscape"]["hidden"] is True and seen["afterEscape"]["focusOnNode"] is True
    # 滑鼠(p3):點兩下取消釘住、焦點留在格子,再 hover 移開照延遲收
    assert seen["afterClick"]["hidden"] is False and seen["afterSecondClick"]["hidden"] is True
    assert seen["afterHover"]["hidden"] is False
    assert seen["afterLeave"]["hidden"] is True
    assert seen["pageErrors"] == []
    # 窄螢幕(p5):選中的 F7 卡捲進情境列的可視區,不捲整頁
    narrow = seen["narrow"]
    assert narrow["overflow"] is True and narrow["scrollLeft"] > 0 and narrow["visible"] is True
    assert narrow["scrollY"] == 0
    # 另存報告(s3):meta CSP 下腳本照跑(hover 開框)、樣式照套(框是 fixed),沒有違規
    assert seen["report"]["hidden"] is False and seen["report"]["position"] == "fixed"
    assert seen["reportViolations"] == []
