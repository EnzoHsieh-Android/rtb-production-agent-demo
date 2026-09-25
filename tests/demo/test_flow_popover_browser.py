# ruff: noqa: S106
"""增量 4 代碼審 r2 p2/p3/p5/s3、r3 v1/c2:在真的瀏覽器裡驗流程圖浮出框的腳本行為。

pytest 把頁面與另存報告寫成靜態 HTML 檔,用 Python 版 Playwright(同步 API,requirements-dev.txt
釘版本;使用者 2026-09-25 裁定)以 file:// 打開,不起 rtb 伺服器:
- 鍵盤:聚焦格子、Enter 釘住後焦點進框,↑↓/PageDown/Space 捲框不捲整頁、不收框,框內 Enter 不開關
  格子,Esc 收框並把焦點還給格子;Tab 到框裡的關閉鈕按 Enter 關框,焦點同樣還給格子。
- 滑鼠:點兩下取消釘住後(焦點還留在格子),再 hover 移開照延遲收框。
- 窄螢幕:情境列載入時把選中那張卡捲進可視區。
- 另存報告帶 meta CSP 仍跑得動腳本、樣式也套得上,沒有違規訊息。

本機沒裝 Playwright 或瀏覽器時跳過並提示安裝指令;CI(環境變數 CI=true)一律要跑,缺了就紅
(ci.yml 的 checks 工作在跑測試前先 `python -m playwright install --with-deps chromium`)。
"""

import ast
import os
import pwd
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from rtb.demo.page import DEMO_CSS, STYLESHEET_PATH, render_page, render_report
from rtb.demo.state import ScenarioCode
from tests.demo.sample_data import make_demo_state

INSTALL_HINT = ("pip install -r requirements-dev.txt 之後跑 "
                "python -m playwright install chromium")
# 測試裡 HOME 換成暫存目錄;Playwright 的瀏覽器快取在真家目錄底下(帳號資料庫讀,只讀、不碰 ~/.rtb)
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def _browsers_path() -> Path:
    mac = _REAL_HOME / "Library" / "Caches" / "ms-playwright"
    return mac if mac.is_dir() else _REAL_HOME / ".cache" / "ms-playwright"


def _unavailable(reason: str) -> None:
    """本機沒裝就跳過並提示;CI 一定要跑,沒裝就是設定壞了。"""
    if os.environ.get("CI") == "true":
        pytest.fail(f"CI 必須跑瀏覽器測試:{reason}")
    pytest.skip(f"{reason}:{INSTALL_HINT}")


def _inline(markup: str) -> str:
    """互動頁連同源樣式表;file:// 打開時把樣式內嵌進來(只為了讓版面在瀏覽器裡成立)。"""
    return markup.replace(f'<link rel="stylesheet" href="{STYLESHEET_PATH}">',
                          f"<style>{DEMO_CSS}</style>")


def _snapshot(page: Any, box_id: str) -> dict[str, Any]:
    return page.evaluate("""boxId => {
      const box = document.getElementById(boxId);
      const active = document.activeElement;
      return { hidden: box.hidden, scrollTop: box.scrollTop, scrollHeight: box.scrollHeight,
               clientHeight: box.clientHeight, focusInBox: box.contains(active),
               focusOnClose: !!active && active.classList.contains('flow-popover-close'),
               focusOnNode: !!active && !!active.dataset && active.dataset.flowPopover === boxId,
               scrollY: window.scrollY, position: getComputedStyle(box).position };
    }""", box_id)


def _files(tmp_path: Path) -> tuple[Path, Path, Path, str]:
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
    return page_file, narrow_file, report_file, box


@pytest.fixture
def browser(monkeypatch):
    try:
        from playwright.sync_api import Error, sync_playwright  # 沒裝就跳過
    except ImportError:
        _unavailable("沒裝 Python 版 Playwright")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(_browsers_path()))
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except Error as missing:
            _unavailable(f"Playwright 的 chromium 起不來({str(missing).splitlines()[0]})")
        try:
            yield launched
        finally:
            launched.close()


def test_flow_popover_keyboard_hover_strip_and_saved_report_in_a_browser(  # noqa: PLR0915
        tmp_path, browser):
    page_file, narrow_file, report_file, box = _files(tmp_path)
    wide = browser.new_context(viewport={"width": 1280, "height": 800})
    page = wide.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(page_file.as_uri())
    node = page.locator(f'.flow-node[data-flow-popover="{box}"]')
    node.scroll_into_view_if_needed()  # 真的按 Tab 時瀏覽器會把格子捲進畫面
    node.focus()
    seen = {"afterFocus": _snapshot(page, box)}
    page.keyboard.press("Enter")
    seen["afterEnter"] = _snapshot(page, box)
    for _ in range(3):
        page.keyboard.press("ArrowDown")
    page.wait_for_timeout(600)  # 鍵盤捲動有平滑動畫:按完鍵等它停下再量
    seen["afterArrows"] = _snapshot(page, box)
    page.keyboard.press("PageDown")
    page.wait_for_timeout(600)
    seen["afterPageDown"] = _snapshot(page, box)
    page.keyboard.press("Space")
    page.wait_for_timeout(600)
    seen["afterSpace"] = _snapshot(page, box)
    page.keyboard.press("Enter")
    seen["afterEnterInBox"] = _snapshot(page, box)
    page.keyboard.press("Escape")
    seen["afterEscape"] = _snapshot(page, box)

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

    # 關閉按鈕(r3 v1/c2):Enter 釘住、Tab 到框裡的關閉鈕、Enter 關框,焦點跟 Esc 一樣還給格子
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    on_close = _snapshot(page, box)
    assert on_close["hidden"] is False and on_close["focusOnClose"] is True
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)  # 超過收框延遲:焦點沒回到格子會被瀏覽器丟到 body
    closed = _snapshot(page, box)
    assert closed["hidden"] is True and closed["focusOnNode"] is True

    # 滑鼠(p3):點兩下取消釘住、焦點留在格子,再 hover 移開照延遲收
    node.click()
    assert _snapshot(page, box)["hidden"] is False
    node.click()
    assert _snapshot(page, box)["hidden"] is True
    page.mouse.move(5, 5)
    node.hover()
    assert _snapshot(page, box)["hidden"] is False
    page.mouse.move(5, 5)
    page.wait_for_timeout(1000)
    assert _snapshot(page, box)["hidden"] is True
    assert errors == []

    # 窄螢幕(p5):選中的 F7 卡捲進情境列的可視區,不捲整頁
    narrow = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    narrow.goto(narrow_file.as_uri())
    strip = narrow.evaluate("""() => {
      const list = document.querySelector('.scenario-index');
      const row = list.querySelector('.scenario-row.is-selected');
      const l = list.getBoundingClientRect();
      const r = row.getBoundingClientRect();
      return { scrollLeft: list.scrollLeft, overflow: list.scrollWidth > list.clientWidth,
               visible: r.left >= l.left - 1 && r.right <= l.right + 1, scrollY: window.scrollY };
    }""")
    assert strip["overflow"] is True and strip["scrollLeft"] > 0 and strip["visible"] is True
    assert strip["scrollY"] == 0

    # 另存報告(s3):meta CSP 下腳本照跑(hover 開框)、樣式照套(框是 fixed),沒有違規
    report = wide.new_page()
    violations: list[str] = []
    report.on("console", lambda message: violations.append(message.text)
              if "content security policy" in message.text.lower()
              or "content-security-policy" in message.text.lower() else None)
    report.on("pageerror", lambda error: violations.append(str(error)))
    report.goto(report_file.as_uri())
    first = report.locator(".flow-node[data-flow-popover]").first
    report_box = first.get_attribute("data-flow-popover")
    assert report_box is not None
    first.hover()
    shown = _snapshot(report, report_box)
    assert shown["hidden"] is False and shown["position"] == "fixed"
    assert violations == []


ROOT = Path(__file__).resolve().parents[2]


def test_the_browser_test_is_wired_into_the_dev_requirements_and_ci():
    """(r3 a1)Python 版 Playwright 釘版本放開發依賴(產品零依賴不變);CI 的 checks 工作在跑測試前
    裝好 chromium;這支檔不再找 node 或 npx 快取。"""
    pins = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("playwright==") for line in pins)
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "playwright" not in project  # 不是產品依賴
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    checks = workflow.split("\n  checks:", 1)[1].split("\n  claims:", 1)[0]
    install = checks.index("run: python -m playwright install --with-deps chromium")
    assert install < checks.index("run: python -m pytest -q")
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported = {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for alias in n.names} | {n.module for n in ast.walk(tree)
                                         if isinstance(n, ast.ImportFrom)}
    assert not imported & {"subprocess", "glob", "shutil"}  # 不起 node、不掃 npx/nvm 快取
