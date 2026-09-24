---
type: verification
status: pass
date: 2026-09-24
valid_under: "docs-readme 工作樹；系統 Python 3.14.6 與 Pillow 12.2.0；流程節點對照目前 main 的 src/rtb/demo/flow.py，配色對照展示頁深色 CSS。"
revalidate_when: "修改 main 的流程定義、展示頁處理者深色配色、F6/F7 合約、README 流程描述、docs/assets/make_agent_flow_gif.py，或換字型與 Pillow 版本時重跑。"
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/README流程動圖_計劃]]"
---
# README流程動圖驗證

2026-09-24 在 docs-readme 工作樹用系統 Python 3.14.6 與 Pillow 12.2.0 重畫 GIF 與 SVG。產圖腳本移至 `docs/assets/make_agent_flow_gif.py`；它是文件產出物工具，不在 pyproject 的 mypy 掃描路徑 `src`、`tools`，也沒有行內 `# mypy:` 例外。以不存在的 `--font` 路徑執行，明確報錯並以代碼 2 退出。

`/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m ruff check .` 通過；同一 venv 的 `python -m mypy` 回報 73 支原始檔無錯；`python -m pytest -q tests/tools tests/test_static_checks.py` 為 305 passed。以 Pillow 抽出 `/tmp/rtb-readme-flow-review/` 的第 1、6、7、15–20 格，目視確認主線直接送出、灰色 AI 規劃節點、F6/F7 回頭線與末格。SVG 已用標準庫 XML 解析，人工回頭段有箭頭標記。這些檢查只涵蓋本地產物與指定測試，沒有宣稱 CI 已跑。
