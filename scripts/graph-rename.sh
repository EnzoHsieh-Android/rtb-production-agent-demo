#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Enzo Hsieh
# SPDX-License-Identifier: MIT
# MIT licensed. Full text: scripts/lumos header, or LICENSE at
# https://github.com/EnzoHsieh-Android/Lumos
# graph-rename.sh — 圖譜 rename/移檔的封印 wrapper(只放行 notesmd-cli `move`)
#
# 為什麼封印: notesmd-cli `move` 經五項補測通過(連結改寫含 frontmatter 字串、
#   定點替換不重排、BOM/CRLF 保留),但同工具的 `frontmatter --edit` 會污染
#   (鍵序字母化、日期加引號型別損傷,見 2026-06-13_Yakitrak三題驗收)。
#   wrapper 物理上只暴露 move,其他 subcommand 一律拒絕——污染路徑不存在於工作流。
#
# 用法:
#   scripts/graph-rename.sh "Systems/舊名" "Systems/新名"   # 路徑形態(必須帶資料夾)
#   下游自動帶 --vault(從 docs/*-knowledge 推),連結全 vault 改寫(含 frontmatter 字串)
#
# 對齊紀律: 之後務必 git diff 確認 + python3 scripts/lumos doctor(plan_refs 斷鏈兜底)

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
BIN="$REPO_ROOT/scripts/bin/notesmd-cli"

if [[ ! -x "$BIN" ]]; then
  echo "擋下:找不到改名工具 notesmd-cli,先跑這個抓下來:" >&2; echo "    scripts/fetch-notesmd.sh" >&2
  exit 2
fi

if [[ $# -ne 2 ]]; then
  echo "用法: scripts/graph-rename.sh <舊路徑> <新路徑>" >&2
  echo "  例: scripts/graph-rename.sh \"Systems/舊名\" \"Systems/新名\"" >&2
  echo "  (只做 rename/移檔;frontmatter 編輯禁用 → 走 lumos set/append)" >&2
  exit 1
fi

# 推 vault 名(docs/*-knowledge 的目錄 basename)
VAULT=""
for d in "$REPO_ROOT"/docs/*-knowledge; do
  [[ -d "$d" ]] && VAULT="$(basename "$d")" && break
done
[[ -z "$VAULT" && -d "$REPO_ROOT/docs/knowledge" ]] && VAULT="knowledge"
if [[ -z "$VAULT" ]]; then
  echo "擋下:目前目錄底下找不到知識圖譜(docs/*-knowledge)" >&2
  exit 2
fi

# vault 須先註冊(add-vault 動 obsidian.json,補測4 證實 remove 可完全還原;
# 此處不自動 remove,保留註冊供連續操作,使用者自行 remove-vault 清理)
"$BIN" add-vault "$REPO_ROOT/docs/$VAULT" >/dev/null 2>&1 || true

echo "→ rename: $1 → $2 (vault=$VAULT)"
exec "$BIN" move "$1" "$2" --vault "$VAULT"
