#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Enzo Hsieh
# SPDX-License-Identifier: MIT
# MIT licensed. Full text: scripts/lumos header, or LICENSE at
# https://github.com/EnzoHsieh-Android/Lumos
# fetch-notesmd.sh — 下載 notesmd-cli 二進位到 scripts/bin/(平台自動偵測)
#
# notesmd-cli (原 Yakitrak/obsidian-cli) 只用於 rename/移檔的連結改寫,
# 經 scripts/graph-rename.sh 封印 wrapper 呼叫(只放行 move)。
# 二進位 9MB、平台相依,不進 git;每台機器跑此腳本取得。
#
# 用法: scripts/fetch-notesmd.sh [版本]   (預設 PINNED_VER)

set -euo pipefail

PINNED_VER="v0.3.6"   # 三題驗收 + 五項補測通過的版本(2026-06-13);升版前重測 T2 frontmatter
VER="${1:-$PINNED_VER}"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
DEST_DIR="$REPO_ROOT/scripts/bin"
DEST="$DEST_DIR/notesmd-cli"
mkdir -p "$DEST_DIR"

# 平台偵測 → release asset 名(對齊 notesmd-cli release 命名)
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS" in
  Darwin) ASSET="notesmd-cli_${VER#v}_darwin_all.tar.gz" ;;  # mac 通用二進位
  Linux)
    case "$ARCH" in
      x86_64|amd64) ASSET="notesmd-cli_${VER#v}_linux_amd64.tar.gz" ;;
      aarch64|arm64) ASSET="notesmd-cli_${VER#v}_linux_arm64.tar.gz" ;;
      *) echo "ERROR: 未支援的 Linux 架構: $ARCH" >&2; exit 2 ;;
    esac ;;
  *) echo "ERROR: 未支援的 OS: $OS(Windows 請手動從 release 下載)" >&2; exit 2 ;;
esac

URL="https://github.com/Yakitrak/notesmd-cli/releases/download/${VER}/${ASSET}"
echo "下載 $ASSET ($VER)..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$URL" | tar xz -C "$TMP"
mv "$TMP/notesmd-cli" "$DEST"
chmod +x "$DEST"
echo "✓ $("$DEST" --version) → $DEST"
