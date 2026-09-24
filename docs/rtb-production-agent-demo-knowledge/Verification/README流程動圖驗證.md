---
type: verification
status: pass
date: 2026-09-24
valid_under: "系統 Python 3.14、Pillow 12，展示頁 CSS 四種處理者配色與流程節點維持本次核對版本。"
revalidate_when: "修改展示頁處理者配色或流程、F6/F7 合約、README 的流程描述，或產圖腳本時重跑。"
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/README流程動圖_計劃]]"
---
# README流程動圖驗證

2026-09-24 在 docs-readme 工作樹重跑產圖腳本兩次，兩次 GIF 與 SVG 的 SHA-256 各自相同；最後把起點字幕改為程式可證實的「收到工作」後再重畫。最終 GIF 為 1200×640、22 格、22 秒、970,849 bytes，SVG 為 13,932 bytes 且 XML 可解析。Ruff 對產圖腳本通過。用 Pillow 抽出第 1 格、中段第 12 格、最後第 22 格，另看 F7 第 18 格與 F6 第 21 格；字幕、四種配色、角標與回佇列箭頭均可讀。

本機系統 Python 沒裝 mypy；CI 會依 pyproject.toml 掃 tools 目錄，產圖腳本對可選的 Pillow 匯入採單檔型別檢查例外。若 CI 的 mypy 閘報錯，回到本紀錄重驗。
