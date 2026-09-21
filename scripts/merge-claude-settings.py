#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Enzo Hsieh
# SPDX-License-Identifier: MIT
# MIT licensed. Full text: scripts/lumos header, or LICENSE at
# https://github.com/EnzoHsieh-Android/Lumos
"""Merge graph hook entries into ~/.claude/settings.json — idempotent.

跟 scripts/install-hooks.sh 配合用。已存在的 hook entry 不重複加。
不會清掉使用者既有的其他 settings (mcpServers/permissions/...)。
"""
from __future__ import annotations
import json
import re
import shutil
import sys
from pathlib import Path

# ── 兩家目標(Projects/Codex完全支援_計劃 d2,2026-09-04)──
# Claude Code:~/.claude/settings.json 的 hooks 段;Codex CLI:~/.codex/hooks.json 頂層 hooks 段。
# 兩者外層 JSON 同形(事件→[{matcher?,hooks:[{type,command,timeout}]}]),內層語意不同:
# matcher 名、payload 形狀、輸出欄位各自查(r1 外家 F2)。★不碰 ~/.codex/config.toml★(stdlib 無 TOML 寫入器)。
def _parse_target(argv):
    """--target claude|codex(預設 claude);值拼錯或缺值 → 擋下(r1 外家 #6:靜默退回 claude 會改錯檔)。"""
    import os
    val = None
    for i, a in enumerate(argv):
        if a.startswith("--target="):
            val = a.split("=", 1)[1]
        elif a == "--target":
            val = argv[i + 1] if i + 1 < len(argv) else ""
    if val is None:
        return "claude"
    if val not in ("claude", "codex"):
        print(f"擋下:--target 只能是 claude 或 codex,給的是 {val!r};沒動任何檔", file=sys.stderr)
        sys.exit(2)
    return val


TARGET = _parse_target(sys.argv[1:])
# Codex 的家目錄尊重 CODEX_HOME(Codex 自己就是這樣找 config/hooks 的;r1 外家 #1)
_CODEX_HOME = Path(__import__("os").environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
if TARGET == "codex":
    SETTINGS = _CODEX_HOME / "hooks.json"
    HOOKS_DIR = _CODEX_HOME / "hooks"
    _HOOKS_SUBDIR = str(HOOKS_DIR).replace("\\", "/") + "/"
else:
    SETTINGS = Path.home() / ".claude" / "settings.json"
    HOOKS_DIR = Path.home() / ".claude" / "hooks"
    _HOOKS_SUBDIR = ".claude/hooks/"

_PY = shutil.which("python3") or shutil.which("python") or "python3"
# W3:${HOME} 只有 POSIX shell 展開;native Windows(Claude Code 經 cmd/PowerShell 跑 hook)
# 不展開 → hook 路徑變字面 ${HOME} → L1/L3 靜默不觸發。Windows 用解析後的絕對 home。
_HOME = str(Path.home()).replace("\\", "/")


# ★逾時預算的單一來源★(2026-09-07 全 repo 審視 #14)
#
# 出身:外層天花板寫在這張註冊表、內層逾時寫在各支 hook 裡,兩邊各自演化。量出來的結果:
# 五支裡三支違反自家「外要明顯大於內」的規則,其中一支內層是外層的 2.5 倍。
#
# ★內層 ≥ 外層代表什麼★:內層那條「逾時就 fail-open」的分支**結構上永遠跑不到**——
# 外面會先把整支 hook 砍掉(SIGKILL,繞過 try/except)。影響鏡頭那支的 fail-open 分支裡
# 有「把冷卻窗記號清掉」,跑不到就表示:超時之後那個檔被鎖住 20 分鐘完全不注入,沒人知道。
# **一個為了 fail-open 而寫的分支,自己被 fail-closed 掉了。**
#
# 現在天花板只寫在這裡,並且用 --budget 傳給 hook;hook 自己算內層(見各 hook 的 _inner_budget)。
HOOK_BUDGET = {
    "lumos-entry-hook.py": 10,
    "ci-status-hook.py": 15,
    "impact-hook.py": 30,
    "dispatch-lens-hook.py": 60,
    # ★從 10 提到 40★(2026-09-07;r1 通才席指出提到 30 還是不夠):
    # 它內層要跑圖譜同步檢查,實測需要 25 秒。內層公式是「外層×0.7」,
    # 所以外層 30 只給到 21 秒——★還是撐不完那 25 秒,天花板提了等於沒提★,
    # 使用者只是多等 20 秒拿到同一個「查不到」。40×0.7=28 才真的夠。
    # 代價誠實講:這支掛在收工事件,使用者最壞情況要多等到 28 秒。
    # REVISIT:2026-12-07 量一次這段實際耗時分布;若多數遠低於 25 秒,把天花板調回去
    "check-graph-sync.py": 40,
    # 記憶過期清掃:多數檢查是本機指令(毫秒),慢的是要連遠端問「推了沒」那幾條。
    # 預算用完會明說「還有幾條沒驗到」,不會假裝驗過。
    "memory-sweep.py": 12,
}

# 有些 hook 除了 --budget 還要別的旗標。★寫在這裡而不是散在下面的註冊表裡★,
# 理由同 HOOK_BUDGET:天花板與旗標都只能有一個來源,否則改一個地方會漏另一個。
HOOK_ARGS = {
    # --quiet 讓它沒事完全不出聲、有話說時走 JSON 通道加安全框。
    # ★不帶 --write★:2026-09-14 代碼審三輪之後改成唯讀——三輪 blocker 全在寫檔上。
    "memory-sweep.py": "--quiet",
}


def _hook_cmd(rel_path):  # rel_path = "verification-rot-check.py"
    # W6:Claude Code 在 Windows 用 Git Bash 跑 hook command → 反斜線會被 shell 吃掉
    # (C:\Users → C:Users → python 找不到 → hook 靜默失敗)。故 Windows 下 python 路徑與
    # home 都用正斜線 + 引號。Unix 保留 ${HOME}(可攜、Mac 已驗)。
    # Codex 目標:命令列帶明確 --harness codex(d2:hook 靠旗標分支,不由 payload 欄位猜家族;
    # 現役五支 hook 都不讀 argv,多帶旗標無害,S1 再各自認旗標)。
    if TARGET == "codex":
        # 絕對路徑(不是 ${HOME}/.codex):CODEX_HOME 可以不在 HOME 底下
        hooks_dir = str(HOOKS_DIR).replace("\\", "/")
        py = _PY.replace("\\", "/") if sys.platform == "win32" else _PY
        _b = HOOK_BUDGET.get(rel_path)
        _bf = f' --budget {_b}' if _b else ''
        _bf += (' ' + HOOK_ARGS[rel_path]) if rel_path in HOOK_ARGS else ''
        return (f'{py} "{hooks_dir}/{rel_path}" --harness codex{_bf}' if sys.platform != "win32"
                else f'"{py}" "{hooks_dir}/{rel_path}" --harness codex{_bf}')
    if sys.platform == "win32":
        py = _PY.replace("\\", "/")
        _b = HOOK_BUDGET.get(rel_path)
        _bf = f' --budget {_b}' if _b else ''
        _bf += (' ' + HOOK_ARGS[rel_path]) if rel_path in HOOK_ARGS else ''
        return f'"{py}" "{_HOME}/.claude/hooks/{rel_path}"{_bf}'
    _b = HOOK_BUDGET.get(rel_path)
    _bf = f' --budget {_b}' if _b else ''
    _bf += (' ' + HOOK_ARGS[rel_path]) if rel_path in HOOK_ARGS else ''
    return f'{_PY} "${{HOME}}/.claude/hooks/{rel_path}"{_bf}'


HOOK_ENTRIES = {
    "SessionStart": [
        {
            # 開場一行:第一步敲 lumos + 指令索引在哪(Projects/指令索引與情境測試_計劃 2026-08-22)。
            # 沒有 docs/*-knowledge 的專案靜默。
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_cmd("lumos-entry-hook.py"),
                    "timeout": HOOK_BUDGET["lumos-entry-hook.py"],
                }
            ],
        },
        {
            # CI 紅燈後備網(CI回流閉環_計劃 [S2b]):主路徑是 push 後同輪 lumos ci-wait,
            # 這支只在主路徑沒跑完(session 中斷/關機)時兜底,開場推播紅燈。
            # 總開關:專案未宣告 .lumos/config.json 的 ci 區塊即完全靜默(零侵入)。
            # 生命週期對稱:檔名同時在 scripts/lumos 的 _GLOBAL_CLAUDE_HOOKS 白名單中,
            # 否則註冊指向不存在的檔、下次 init 時被 _prune_dangling 剪掉(見 Issues/hook卸載殘留註冊)。
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_cmd("ci-status-hook.py"),
                    "timeout": HOOK_BUDGET["ci-status-hook.py"],
                }
            ],
        },
        {
            # 記憶過期清掃(2026-09-14):Claude Code 的記憶檔會講「當下狀態」——某某還沒推、
            # 某某還沒裝——而推翻那句話的事件不會回來改那個檔。★而且記憶是開場自動塞進視野的、
            # 圖譜要主動查,所以錯的那份反而先被讀到★(實測 76 篇裡三篇在說謊)。
            # 這支唯讀地重驗每篇自帶的 verify 檢查,對不上就印進這次對話(不改任何檔),
            # 並揪出「抄了圖譜狀態」的影子副本。沒有記憶目錄的專案完全靜默。
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_cmd("memory-sweep.py"),
                    "timeout": HOOK_BUDGET["memory-sweep.py"],
                }
            ],
        },
    ],
    "PreToolUse": [
        {
            # 主動影響幅度偵測:Edit/Write/MultiEdit 動手前注入 additionalContext。
            # 比照現有 claude/ hooks 現況:不進 ANCHOR_FILES(見設計 §5)。
            # 生產實測:用 `claude --debug` 驗 PreToolUse additionalContext 注入時機;
            # 若版本行為有變可退回 stderr 備援(check-graph-sync.py 已證此路可行)。
            # (設計 §3 r5-F2)
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_cmd("impact-hook.py"),
                    "timeout": HOOK_BUDGET["impact-hook.py"],
                }
            ],
        },
        {
            # 派工鏡頭注入(Projects/派工鏡頭注入_計劃 2026-09-03):派子代理前,派工詞含
            # `LUMOS-IMPACT: <base>..<head>` 就把固定席節點附進派工詞(updatedInput)。
            # 不帶標記=一次字串比對就放行;任何失敗放行;永不 deny。
            # 本 hook 在 scripts/lumos 的 ANCHOR_FILES 內(會改寫子代理輸入,硬約束)。
            # 外層 60 > 內層 45(dispatch-lens-hook.py INNER_TIMEOUT)。
            "matcher": "Agent",
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_cmd("dispatch-lens-hook.py"),
                    "timeout": HOOK_BUDGET["dispatch-lens-hook.py"],
                }
            ],
        },
    ],
    # NOTE(2026-08-21 體檢 #8):verification-rot-check(PostToolUse/Bash,每次 Bash 都觸發、
    # 逐候選 claude -p 25s)撤除——docs/.rot-check-cache.json 與 .rot-queue.jsonl 從未被建立,
    # 核心邏輯從沒跑到卻每次付啟動成本;且註冊曾重複兩份。要復活先修到核心可觸發再接回。
    "PostToolUse": [],
    "Stop": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_cmd("check-graph-sync.py"),
                    "timeout": HOOK_BUDGET["check-graph-sync.py"],
                }
            ]
        },
        # NOTE(2026-07-06):code-loop-guard Stop nag 已移除——每回合注入太擾民。
        # code-loop 把關改由 pre-push git hook(scripts/hooks/pre-push)在 push 時單點強制,
        # 不再每回合 nag。(2026-07-07 起懸空註冊由 _prune_dangling 自動清,不再需手動。)
    ],
}


def _codex_entries(entries: dict) -> dict:
    """Claude 註冊表 → Codex 註冊表(Projects/Codex完全支援_計劃 S0-2 對照表,2026-09-04 實測 0.144.1):
    · SessionStart / Stop:同名同形。
    · PreToolUse `Edit|Write|MultiEdit` → matcher `apply_patch`(Codex 改檔工具;Edit/Write 只是它的別名,
      tool_name 仍回 apply_patch;tool_input 是 patch 全文沒有 file_path——S1 適配 impact-hook)。
    · PreToolUse `Agent`(派工鏡頭)→ 改掛 `SubagentStart`(無 matcher):spawn_agent 的 message 對 hook
      是密文改不了(實驗 A),子代理開場的 additionalContext 才到得了子代理(實驗 5)。
    其餘事件原樣。"""
    out = {}
    for event, arr in entries.items():
        for e in arr:
            ne = dict(e)
            ev = event
            if event == "PreToolUse" and e.get("matcher") == "Edit|Write|MultiEdit":
                ne["matcher"] = "apply_patch"
            elif event == "PreToolUse" and e.get("matcher") == "Agent":
                ev = "SubagentStart"
                ne.pop("matcher", None)
            out.setdefault(ev, []).append(ne)
    return out


if TARGET == "codex":
    HOOK_ENTRIES = _codex_entries(HOOK_ENTRIES)


def _prune_dangling(settings: dict) -> list:
    """剪掉指向 ~/.claude/hooks/ 下「不存在檔案」的 hook 註冊(懸空只會每回合報錯)。
    起因 2026-07-07 現場事故:code-loop-guard.py 被工具鏈更新刪除、settings 註冊沒清 →
    每回合「檔案不存在」。只動 command 含 `.claude/hooks/` 的項;使用者自訂
    (指向他處)的 command 一律不碰。回傳被剪的 (event, script) 列表。"""
    pruned = []
    for event, entries in list(settings.get("hooks", {}).items()):
        if not isinstance(entries, list):      # schema 錯(值不是陣列)→ 當空,別炸(code-codex-s0 r1 F3)
            settings["hooks"][event] = []
            continue
        kept_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kept_hooks = []
            for h in (entry.get("hooks") or []):
                if not isinstance(h, dict):
                    continue
                cmd = h.get("command", "")
                if _HOOKS_SUBDIR in cmd.replace("\\", "/"):
                    script = _hook_script(cmd)
                    if script.endswith(".py") and not (HOOKS_DIR / script).exists():
                        pruned.append((event, script))
                        continue
                kept_hooks.append(h)
            if kept_hooks or not entry.get("hooks"):
                entry = dict(entry)
                if "hooks" in entry:
                    entry["hooks"] = kept_hooks
                kept_entries.append(entry)
            # else:整組 hooks 都懸空 → entry 一併剪
        settings["hooks"][event] = kept_entries
    return pruned


def _hook_script(cmd: str):
    m = re.search(r"([\w.-]+\.py)", cmd or "")
    return m.group(1) if m else cmd


def _equivalent(a: dict, b: dict) -> bool:
    """同一個 hook entry 認定為已存在 (避免重複註冊)。
    比對:matcher (PostToolUse 需要) + 內層 hook 腳本檔名
    (認出舊裸路徑 == 新 `python …/xxx.py` 為同一 hook)。"""
    if a.get("matcher") != b.get("matcher"):
        return False
    a_s = sorted(_hook_script(h.get("command", "")) for h in (a.get("hooks") or []) if isinstance(h, dict))
    b_s = sorted(_hook_script(h.get("command", "")) for h in (b.get("hooks") or []) if isinstance(h, dict))
    return a_s == b_s


def main() -> int:
    if SETTINGS.exists():
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"ERROR: {SETTINGS} JSON 損毀: {e}(hooks 設定檔壞掉,本次不動它;修好 JSON 再跑一次 lumos install)", file=sys.stderr)
            return 1
    else:
        settings = {}

    # schema 防護:語法合法但 hooks 不是物件(null / 陣列)→ 視為空物件重建,不炸(r1 單reviewer F3:
    # 之前 hooks: null 會在 _prune_dangling 炸 AttributeError,teardown 因此漏剪懸空還印成功)
    if not isinstance(settings.get("hooks"), dict):
        if "hooks" in settings:
            print(f"  ⚠ {SETTINGS} 的 hooks 欄位不是物件({type(settings.get('hooks')).__name__}),當空物件重建", file=sys.stderr)
        settings["hooks"] = {}
    changed = False

    # 先清懸空(腳本已被刪、註冊還在 → 每回合報錯),再 merge
    for event, script in _prune_dangling(settings):
        print(f"  [prune] {event} hook → {script}(檔案不存在,懸空註冊已清)")
        changed = True

    # --prune-only:只剪懸空、★不 re-add★(teardown 用——刪掉我方 hook .py 後跑此模式,
    # 剪掉變懸空的我方註冊而不把它加回去;預設 merge 模式會 re-add,反而抵銷 teardown)。
    prune_only = "--prune-only" in sys.argv

    for event, entries_to_add in ({} if prune_only else HOOK_ENTRIES).items():
        existing = settings["hooks"].setdefault(event, [])
        for new_entry in entries_to_add:
            match_idx = next((i for i, e in enumerate(existing) if _equivalent(new_entry, e)), None)
            if match_idx is not None:
                if existing[match_idx] != new_entry:
                    existing[match_idx] = new_entry  # 遷移:取代成 resolved-python 格式
                    print(f"  [migrate] {event} hook → resolved-python")
                    changed = True
                else:
                    print(f"  [skip] {event} hook already current")
                continue
            existing.append(new_entry)
            print(f"  [add ] {event} hook")
            changed = True

    # ★自癒去重(2026-08-21 體檢 #1)★:舊版安裝器在 _equivalent 上線前已把同一 hook 註冊兩次
    # (verification-rot-check / check-graph-sync 各兩份,每次事件跑兩遍、Sonnet 呼叫翻倍)。
    # 合併後再掃一次,等價項只留第一份;與 --prune-only 一樣是修復動作,每次執行都跑。
    for event, entries in settings["hooks"].items():
        kept = []
        for e in (entries if isinstance(entries, list) else []):
            if not isinstance(e, dict):
                continue
            if any(_equivalent(e, k) for k in kept):
                print(f"  [dedupe] {event} hook 重複註冊,移除一份")
                changed = True
                continue
            kept.append(e)
        settings["hooks"][event] = kept

    if not changed:
        print(f"{SETTINGS.name} 已經是最新狀態,無需修改")
        return 0

    # Backup before write
    if SETTINGS.exists():
        backup = SETTINGS.with_suffix(".json.bak")
        backup.write_text(SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  備份到: {backup}")

    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"已更新: {SETTINGS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
